"""ODDS_HISTORY scorer: did the production props model beat the market on past seasons?

Rule: ``docs/prereg/ODDS_HISTORY.md`` (DRAFT until the hash in its header is recorded and
committed). Nothing here changes the rule; every number below is a literal implementation of its
sections 1-6, and every place where the text is silent is a named constant or a documented choice
flagged in the report (see ``CHOICES``).

Stages (model side and price side are produced SEPARATELY and are joined only by ``score``)::

    uv run python -m research.eval.odds_history_eval model-rows  --season 2023   # heavy (lock)
    uv run python -m research.eval.odds_history_eval market-rows --season 2023   # prices only
    uv run python -m research.eval.odds_history_eval game-rows   --season 2023   # H3 model side
    uv run python -m research.eval.odds_history_eval score --season 2024 --frozen-sha <8 hex>

``score`` REFUSES unless the sha256 of the frozen block of the rule, recomputed here, equals the
one passed AND equals the one recorded in the rule's header line. Season 2025 is the frozen
holdout: every stage refuses it unless ``--holdout-row`` points at a committed
``docs/HOLDOUT_ACCESS_LOG.md`` row for this rule; and the touch itself is not implemented here.

Read-only on every DuckDB (``read_only=True``); season <= 2024 is asserted before any SQL.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "prereg" / "ODDS_HISTORY.md"
ODDS_DB = ROOT / "data" / "odds" / "odds_history.duckdb"
OUT_DIR = ROOT / "reports" / "odds_history"
REPORT_MD = ROOT / "reports" / "odds_history.md"
HEAVY_LOCK = ROOT / "data" / "ops" / "heavy.lock"
LOWER_TAIL_JSON = ROOT / "reports" / "lower_tail" / "candidates.json"
HOLDOUT_LOG = ROOT / "docs" / "HOLDOUT_ACCESS_LOG.md"
REALTIP_OOF = ROOT / "data" / "injury_elo_realtip" / "oof_predictions.parquet"

# ----------------------------------------------------------------------------- rule constants
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
MARKET_STAT: dict[str, str] = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
BOOKS: tuple[str, ...] = ("pinnacle", "draftkings", "fanduel", "consensus")
TESTED_BOOKS: tuple[str, ...] = ("pinnacle", "consensus")
KINDS: tuple[str, ...] = ("t60", "t5")
SEASONS: tuple[int, ...] = (2023, 2024)
SELECT_SEASON, REPORT_SEASON = 2023, 2024
HOLDOUT_SEASON = 2025
MAX_SEASON = 2024
N_BOOT = 5000  # section 3
BOOT_SEED = 2026  # section 3
FLOOR = 0.002  # section 3 (log loss units)
ALPHA = 0.05
W_GRID: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(21))  # section 2, H2
EDGE_MIN = 0.03  # section 3 money
LEAD_MIN = 60  # the model's as-of: injury reports at least this many minutes before the real tip
#: log-loss clip for MODEL probabilities, the 199-quantile grid resolution (as LOWER_TAIL/F9b).
#: The rule is silent on a clip; see CHOICES["model_clip"].
MODEL_EPS = 0.5 / 199
MARKET_EPS = 1e-9
SEEDS = {"model": 0, "bootstrap": BOOT_SEED, "pit": 0}
A0_TOL = 1e-6
PGE_KMAX: dict[str, int] = {"pts": 80, "reb": 35, "ast": 30, "fg3m": 18}  # nba.props.full_support
N_Q = 199
TIE_DECIMALS = 12

#: Places where the draft is silent or ambiguous. Each is implemented as written here and is
#: surfaced in the report so the rule can be amended BEFORE the freeze.
CHOICES: dict[str, str] = {
    "model_clip": "Model probabilities are clipped to [0.5/199, 1-0.5/199] before any log loss or "
    "logit (the 199-quantile grid cannot express a probability below 1/398); market "
    "probabilities are not clipped (never extreme).",
    "consensus_line": "consensus: the line that most books have as their main line (ties -> "
    "lower line); the probability is the median multiplicative de-vig over every book with a "
    "two-sided quote AT that line; n_books recorded, no minimum.",
    "w_cell": "H2 weight w is fit per (stat, book, snapshot_kind) cell on 2023 (the rule says "
    "'per stat').",
    "verdict": "beats_market = point <= -0.002 AND CI upper < 0 AND (tested cells only) "
    "Holm-adjusted p < 0.05; |point| < 0.002 = indistinguishable; everything else = "
    "market_better (literal), with a qualifier when the point is <= -0.002 but not established.",
    "holm_scope": "Holm over the 16 tests per snapshot kind is computed on the report season "
    "(2024); 2023 H1 cells are descriptive (Holm over their 8 tests), H2 is not scored on 2023.",
    "push": "whole-number lines: model P(over | no push) = P(Y>=k+1)/(P(Y>=k+1)+P(Y<=k-1)); "
    "pushes (Y == k) are dropped from both sides and counted; non-0.5 multiples (e.g. 7.25) and "
    "lines <= 0 are dropped and counted (odd_line).",
    "leak_guard": "applied to t60 pairs: snapshot_ts must be <= real tip - 60 min (the model's "
    "as-of); t5 is later by design and is not a leak (the model never sees prices).",
    "ev_gate": "EV is reported only for T-60 cells (tested or untested) that beat the market on "
    "H1 or H2; edge = p_model(side) - p_market_devig(side) >= 0.03; consensus has no own price "
    "so no EV; pushes are not in the paired set so they are not in the EV set either.",
    "slices": "vol = recency sd / max(recency mean, 1) and tier = recency mean, both in thirds of "
    "the stat's model-row universe in that season; line distance in thirds of the pooled pairs "
    "of the stat; over/under = the model's lean (p_model > p_market); first15 = team game < 15.",
    "clv": "model side = over if p_model > p_market(T-60) else under; line moved => agree if the "
    "line moved toward that side; same line => agree if the de-vigged price moved toward it; "
    "zero moves excluded.",
}


class RuleError(RuntimeError):
    """The rule is not frozen / does not match / the holdout guard fired."""


# ----------------------------------------------------------------------------- freeze guard


def frozen_block(doc: Path = DOC) -> str:
    """The text between the FROZEN markers, byte for byte what the ``awk`` one-liner in the rule's
    header prints (lines strictly between BEGIN and END, each with a trailing newline)."""
    # Markers match a WHOLE line only. The rule's own "Verify:" header quotes both marker strings
    # on one line; a substring match started collecting there, so the hash covered the line that
    # records the hash and changed when it was written (found at the freeze, 2026-10-10).
    out: list[str] = []
    flag = False
    for line in doc.read_text().splitlines():
        if line.strip() == "<!-- FROZEN-END -->":
            flag = False
        if flag:
            out.append(line + "\n")
        if line.strip() == "<!-- FROZEN-BEGIN -->":
            flag = True
    return "".join(out)


def frozen_sha256(doc: Path = DOC) -> str:
    return hashlib.sha256(frozen_block(doc).encode()).hexdigest()


def recorded_sha(doc: Path = DOC) -> str | None:
    """The 64-hex value on the header line ``sha256 of the frozen section:``, or None."""
    for line in doc.read_text().splitlines():
        if line.lower().startswith("sha256 of the frozen section"):
            m = re.search(r"\b[0-9a-f]{64}\b", line)
            return m.group(0) if m else None
    return None


def require_frozen(sha_arg: str, doc: Path = DOC) -> str:
    """Refuse unless the doc is frozen AND the passed 8-hex prefix matches the recomputed hash."""
    if not re.fullmatch(r"[0-9a-f]{8}", sha_arg or ""):
        raise RuleError("--frozen-sha must be exactly 8 lowercase hex characters")
    computed = frozen_sha256(doc)
    rec = recorded_sha(doc)
    if rec is None:
        raise RuleError(
            "the rule is not frozen: the header line 'sha256 of the frozen section:' holds no "
            "64-hex hash (DRAFT). Refusing to score."
        )
    if rec != computed:
        raise RuleError(
            f"the recorded sha ({rec[:8]}...) differs from the recomputed frozen block "
            f"({computed[:8]}...): the rule changed after the freeze. Refusing to score."
        )
    if not computed.startswith(sha_arg):
        raise RuleError(f"--frozen-sha {sha_arg} does not match the frozen block {computed[:8]}")
    return computed


# ----------------------------------------------------------------------------- holdout guard


def _git_committed(path: Path) -> bool:  # pragma: no cover - needs a git checkout
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            cwd=ROOT,
        ).returncode
        clean = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(path)], capture_output=True, cwd=ROOT
        ).returncode
        return tracked == 0 and clean == 0
    except Exception:
        return False


def guard_season(
    season: int,
    holdout_row: str | None = None,
    committed_check: Callable[[Path], bool] = _git_committed,
) -> None:
    """Seasons 2023 and 2024 pass. Season 2025 needs ``--holdout-row`` = a committed
    HOLDOUT_ACCESS_LOG.md that already holds a row for this rule; even then the touch is not
    implemented here (it is the maintainer's one logged run). Anything else is refused."""
    if season in SEASONS:
        return
    if season != HOLDOUT_SEASON:
        raise RuleError(f"season {season} is outside the study (2023, 2024; 2025 holdout)")
    if not holdout_row:
        raise RuleError("season 2025 is the frozen holdout: --holdout-row <logged row> required")
    p = Path(holdout_row)
    if not p.exists():
        raise RuleError(f"--holdout-row {p} does not exist")
    text = p.read_text()
    has_row = any(
        "ODDS_HISTORY" in ln and "2025" in ln and ln.lstrip().startswith("|")
        for ln in text.splitlines()
    )
    if not has_row:
        raise RuleError("no HOLDOUT_ACCESS_LOG row mentioning ODDS_HISTORY and 2025 was found")
    if not committed_check(p):
        raise RuleError("the holdout row is not committed: commit it BEFORE the run")
    raise NotImplementedError(
        "the 2025 touch is not implemented in this build (guard only); it is the maintainer's "
        "single logged run after the freeze"
    )


@contextlib.contextmanager
def heavy_lock(lock: Path = HEAVY_LOCK, retry_s: float = 120.0) -> Iterator[None]:
    """mkdir lock (repo convention); retry every ``retry_s``; always removed."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            print(f"heavy.lock held; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)

    def _release(*_: Any) -> None:
        shutil.rmtree(lock, ignore_errors=True)
        raise SystemExit(143)

    old = {s: signal.signal(s, _release) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        shutil.rmtree(lock, ignore_errors=True)


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def _jd(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_jd))


# ============================================================================= STAGE 1: model rows


def _raw_quantiles() -> Callable[..., np.ndarray]:
    """Production's quantile construction. It moved from ``nba.props.pts_tail`` to
    ``nba.props.lower_tail`` on 2026-10-10 (same code); accept either home."""
    try:
        from nba.props.lower_tail import raw_quantiles
    except ImportError:  # pragma: no cover - pre-move checkouts
        from nba.props.pts_tail import raw_quantiles  # type: ignore[no-redef]
    return raw_quantiles


def pge_matrix(qi: np.ndarray, kmax: int) -> np.ndarray:
    """``P(Y >= k)``, k = 1..kmax, from an integer-support quantile grid ``qi`` ``[n, 199]``.

    ``ceil(q - 0.5) >= k`` iff ``q > k - 0.5``, i.e. the full_support convention
    ``P(Y >= N) = P(cont > N - 0.5)``, exact on the 199 equal-weight samples."""
    n, nq = qi.shape
    out = np.empty((n, kmax), dtype=np.float64)
    for k in range(1, kmax + 1):
        out[:, k - 1] = (qi >= k).sum(axis=1) / nq
    return out


def pge_moments(pge: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and std of the integer distribution from its survival vector (k = 1..K)."""
    k = np.arange(1, pge.shape[1] + 1, dtype=float)
    mean = pge.sum(axis=1)
    second = ((2.0 * k - 1.0) * pge).sum(axis=1)
    var = np.maximum(second - mean**2, 0.0)
    return mean, np.sqrt(var)


MODEL_META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "game_date",
    "season",
    "minutes",
    "n_prior",
    "min10",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "is_home_f",
)


def collect_stat_rows(
    feats: pl.DataFrame,
    stat: str,
    cfg: Any,
    season: int,
    progress: Callable[[str], None] = lambda s: None,
) -> dict[str, Any]:
    """Production walk-forward for one stat and ONE test season (month blocks, train strictly
    before the block, 2022 warm-up, config seed): the integer-support survival vector per row.

    Returns ``{"frame": pl.DataFrame, "crps_int": float, "c": ..., "s": ...}``."""
    from nba.eval.context_residual_eval import crps_from_quantiles, month_blocks
    from nba.props.context_residual import (
        CRPS_TAUS,
        ContextResidualModel,
        split_calibration,
        stat_feature_names,
        stat_frame,
        to_integer_support,
    )

    if season > MAX_SEASON:
        raise RuleError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    names = stat_feature_names(stat)
    test_all = sf.filter(pl.col("season") == season)
    qs: list[np.ndarray] = []
    parts: list[pl.DataFrame] = []
    cs: list[np.ndarray] = []
    ss: list[np.ndarray] = []
    means: list[np.ndarray] = []
    blocks = month_blocks(test_all)
    for i, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season") == season
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        z = (cal_df["resid"].to_numpy() - mu) / sc
        q = _raw_quantiles()(c, s, z, CRPS_TAUS)
        qs.append(to_integer_support(q).astype(np.int16))
        cs.append(c)
        ss.append(s)
        means.append(np.clip(c + s * float(model.z_sorted.mean()), 0.0, None))
        parts.append(
            block.select(
                *MODEL_META_COLS,
                pl.col("y"),
                pl.col("m").alias("m_recency"),
                pl.col("s").alias("sd_recency"),
            )
        )
        progress(f"{stat} {season} block {i + 1}/{len(blocks)} {start} n={block.height}")
    qi = np.vstack(qs)
    meta = pl.concat(parts)
    y = meta["y"].to_numpy()
    crps = float(crps_from_quantiles(qi.astype(float), y).mean())
    kmax = max(PGE_KMAX[stat], int(qi.max()))
    pge = pge_matrix(qi, kmax)
    mean_int, std_int = pge_moments(pge)
    frame = meta.with_columns(
        pl.lit(stat).alias("stat"),
        pl.Series("mean_prod", np.concatenate(means)),
        pl.Series("mean_int", mean_int),
        pl.Series("std_int", std_int),
        pl.Series("c", np.concatenate(cs)),
        pl.Series("s", np.concatenate(ss)),
        pl.Series("tail_mass", (qi > kmax).mean(axis=1)),
        pl.Series("p_ge", pge.tolist(), dtype=pl.List(pl.Float64)),
    )
    return {"frame": frame, "crps_int": crps, "qi_max": int(qi.max()), "kmax": kmax}


def a0_check(
    stat: str,
    season: int,
    got_crps: float,
    n_rows: int,
    frame: pl.DataFrame,
    ref_json: Path = LOWER_TAIL_JSON,
    comp_dir: Path | None = None,
) -> dict[str, Any]:
    """Self-check against LOWER_TAIL's stored production ('off') arm: integer-support CRPS to
    1e-6 and, when the stored components exist, the centre/scale rows themselves."""
    ref = json.loads(ref_json.read_text())[stat][str(season)]["off"]
    out: dict[str, Any] = {
        "stat": stat,
        "season": season,
        "crps_int": got_crps,
        "lower_tail_crps_int": float(ref["crps_int"]),
        "delta": got_crps - float(ref["crps_int"]),
        "n": n_rows,
        "lower_tail_n": int(ref["n"]),
    }
    out["ok"] = bool(abs(out["delta"]) <= A0_TOL and n_rows == out["lower_tail_n"])
    npz = (comp_dir or ref_json.parent) / f"components_{stat}.npz"
    if npz.exists():
        z = np.load(npz, allow_pickle=True)
        sm = z["row_season"] == season
        stored = pl.DataFrame(
            {
                "game_id": z["row_gid"][sm].astype(str),
                "player_id": z["row_pid"][sm].astype(np.int64),
                "c0": z["row_c"][sm],
                "s0": z["row_s"][sm],
            }
        )
        j = frame.select("game_id", "player_id", "c", "s").join(
            stored, on=["game_id", "player_id"], how="inner"
        )
        out["components_matched"] = int(j.height)
        out["max_abs_dc"] = float((j["c"] - j["c0"]).abs().max())  # type: ignore[arg-type]
        out["max_abs_ds"] = float((j["s"] - j["s0"]).abs().max())  # type: ignore[arg-type]
    return out


def model_rows(
    season: int,
    db_path: str = "nba.duckdb",
    out_dir: Path = OUT_DIR,
    holdout_row: str | None = None,
    stats: tuple[str, ...] = STATS,
    config_path: str = "configs/context_residual.yaml",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    lock: bool = True,
) -> dict[str, Any]:
    """Stage 1. One parquet per season; per-stat checkpoints; A0 self-check in the meta file."""
    guard_season(season, holdout_row)
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    cm = heavy_lock() if lock else contextlib.nullcontext()
    with cm:
        elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
        cfg = ContextResidualConfig(
            **yaml.safe_load(Path(config_path).read_text()).get("model", {})
        )
        if cfg.lower_tail != "off" or cfg.young_pick != "off" or cfg.integer_support:
            raise RuleError("the production arm needs lower_tail/young_pick off, no integer flag")
        games, pgs, static, avail = load_inputs(db_path, None)
        assert int(games["season"].max()) <= MAX_SEASON, "holdout must not be loaded"  # type: ignore[arg-type]
        flagged, _ = flagged_from_availability(avail, games, cfg.report)
        feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
        assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
        print(f"features built: {feats.height} rows", flush=True)
        frames: list[pl.DataFrame] = []
        checks: list[dict[str, Any]] = []
        for stat in stats:
            part = out_dir / f"model_rows_{season}_{stat}.parquet"
            chk_path = out_dir / f"model_rows_{season}_{stat}.a0.json"
            if part.exists() and chk_path.exists():
                print(f"{stat}: checkpoint found, reusing", flush=True)
                frames.append(pl.read_parquet(part))
                checks.append(json.loads(chk_path.read_text()))
                continue
            res = collect_stat_rows(feats, stat, cfg, season, lambda m: print(m, flush=True))
            frame = res["frame"]
            chk = a0_check(stat, season, res["crps_int"], frame.height, frame)
            chk["qi_max"], chk["kmax"] = res["qi_max"], res["kmax"]
            chk["max_tail_mass"] = float(frame["tail_mass"].max())
            frame.write_parquet(part)
            write_json(chk_path, chk)
            print(f"{stat} {season}: {json.dumps(chk)}", flush=True)
            frames.append(frame)
            checks.append(chk)
        # one table; lists of different lengths per stat are fine in a long frame
        allf = pl.concat(frames, how="vertical")
        allf.write_parquet(out_dir / f"model_rows_{season}.parquet")
    meta = {
        "season": season,
        "n_rows": allf.height,
        "per_stat": {s: int((allf["stat"] == s).sum()) for s in stats},
        "a0": checks,
        "a0_ok": bool(all(c["ok"] for c in checks)),
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "config": config_path,
        "rule_status": "DRAFT",
    }
    write_json(out_dir / f"model_rows_{season}.meta.json", meta)
    return meta


# ============================================================================= STAGE 2: market rows


def american_prob(price: np.ndarray) -> np.ndarray:
    """Vigged implied probability of an American price (array)."""
    a = np.asarray(price, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a < 0, -a / (-a + 100.0), 100.0 / (a + 100.0))


def devig_mult(ro: np.ndarray, ru: np.ndarray) -> np.ndarray:
    """Multiplicative (proportional) de-vig: P(over) = ro / (ro + ru)."""
    a, b = np.asarray(ro, dtype=float), np.asarray(ru, dtype=float)
    return np.asarray(a / (a + b))


def devig_power(ro: np.ndarray, ru: np.ndarray, iters: int = 80) -> np.ndarray:
    """Power-method de-vig: the exponent k with ro**k + ru**k = 1, P(over) = ro**k (bisection)."""
    ro = np.asarray(ro, dtype=float)
    ru = np.asarray(ru, dtype=float)
    lo = np.full(ro.shape, 1e-6)
    hi = np.full(ro.shape, 50.0)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        f = ro**mid + ru**mid - 1.0  # decreasing in k
        pos = f > 0
        lo = np.where(pos, mid, lo)
        hi = np.where(pos, hi, mid)
    k = 0.5 * (lo + hi)
    return np.asarray(ro**k)


def valid_line(point: pl.Expr) -> pl.Expr:
    """Half-point or whole-number line above zero (model push handling covers x.0 and x.5)."""
    return point.is_not_null() & (point > 0) & (((point * 2.0).round(0) - point * 2.0).abs() < 1e-9)


KEY = ["game_id", "snapshot_kind", "book", "stat", "player_id"]


def build_lines(q: pl.DataFrame) -> dict[str, Any]:
    """From raw prop quote rows (``game_id, snapshot_kind, snapshot_ts, requested_at, book,
    market, player_name, player_id, side, point, price_american``) build the two-sided line table
    and the reason counters. Pure; no model information."""
    q = q.with_columns(
        pl.col("market").replace_strict(MARKET_STAT, default=None).alias("stat")
    ).filter(pl.col("stat").is_not_null() & pl.col("side").is_in(["over", "under"]))
    unres = q.filter(pl.col("player_id").is_null())
    res = q.filter(pl.col("player_id").is_not_null())
    lines = res.group_by([*KEY, "point"], maintain_order=True).agg(
        (pl.col("side") == "over").sum().alias("n_over"),
        (pl.col("side") == "under").sum().alias("n_under"),
        pl.col("price_american").filter(pl.col("side") == "over").first().alias("price_over"),
        pl.col("price_american").filter(pl.col("side") == "under").first().alias("price_under"),
        pl.col("snapshot_ts").first().alias("snapshot_ts"),
        pl.col("requested_at").first().alias("requested_at"),
    )
    lines = lines.with_columns(
        ((pl.col("n_over") == 1) & (pl.col("n_under") == 1)).alias("two_sided"),
        ((pl.col("n_over") > 1) | (pl.col("n_under") > 1)).alias("dup"),
        valid_line(pl.col("point")).alias("valid"),
    )
    good = lines.filter(pl.col("two_sided") & pl.col("valid"))
    good = good.with_columns(
        pl.Series("ro", american_prob(good["price_over"].to_numpy())),
        pl.Series("ru", american_prob(good["price_under"].to_numpy())),
    )
    ro, ru = good["ro"].to_numpy(), good["ru"].to_numpy()
    good = good.with_columns(
        pl.Series("p_over_mult", devig_mult(ro, ru)),
        pl.Series("p_over_power", devig_power(ro, ru)),
        pl.Series("overround", ro + ru - 1.0),
        pl.Series("bal", np.round(np.abs(ro - ru), TIE_DECIMALS)),
    )
    groups = lines.group_by(KEY).agg(
        pl.col("two_sided").and_(pl.col("valid")).sum().alias("n_good"),
        pl.col("dup").any().alias("any_dup"),
        pl.col("valid").not_().any().alias("any_odd"),
    )
    return {"lines": lines, "good": good, "groups": groups, "unres": unres}


def main_lines(good: pl.DataFrame) -> pl.DataFrame:
    """The book's main line per player-snapshot: most balanced prices; ties -> lower line."""
    n_good = good.group_by(KEY).agg(pl.len().alias("n_lines_two_sided"))
    top = good.sort([*KEY, "bal", "point"]).unique(subset=KEY, keep="first", maintain_order=True)
    return top.join(n_good, on=KEY, how="left")


def consensus_rows(good: pl.DataFrame, mains: pl.DataFrame) -> pl.DataFrame:
    """Per player-snapshot: the modal main line (ties -> lower), then the median multiplicative
    (and power) de-vigged P(over) over every book with a two-sided quote at that line."""
    k4 = ["game_id", "snapshot_kind", "stat", "player_id"]
    votes = mains.group_by([*k4, "point"]).agg(pl.len().alias("votes"))
    pick = votes.sort([*k4, "votes", "point"], descending=[False] * 4 + [True, False]).unique(
        subset=k4, keep="first", maintain_order=True
    )
    at = good.join(pick.select([*k4, "point"]), on=[*k4, "point"], how="inner")
    return at.group_by(k4).agg(
        pl.col("point").first().alias("line"),
        pl.col("p_over_mult").median().alias("p_over_mult"),
        pl.col("p_over_power").median().alias("p_over_power"),
        pl.col("book").n_unique().alias("n_books"),
        pl.col("snapshot_ts").first().alias("snapshot_ts"),
        pl.col("requested_at").first().alias("requested_at"),
    )


def market_table(good: pl.DataFrame) -> pl.DataFrame:
    """Final market rows: pinnacle/draftkings/fanduel main lines + consensus."""
    mains = main_lines(good)
    books = mains.filter(pl.col("book").is_in(["pinnacle", "draftkings", "fanduel"])).select(
        "game_id",
        "player_id",
        "stat",
        "book",
        "snapshot_kind",
        "snapshot_ts",
        "requested_at",
        pl.col("point").alias("line"),
        "price_over",
        "price_under",
        pl.col("ro").alias("p_over_raw"),
        pl.col("ru").alias("p_under_raw"),
        "overround",
        "p_over_mult",
        "p_over_power",
        pl.lit(1).cast(pl.UInt32).alias("n_books"),
        "n_lines_two_sided",
    )
    cons = consensus_rows(good, mains).select(
        "game_id",
        "player_id",
        "stat",
        pl.lit("consensus").alias("book"),
        "snapshot_kind",
        "snapshot_ts",
        "requested_at",
        "line",
        pl.lit(None, dtype=pl.Int64).alias("price_over"),
        pl.lit(None, dtype=pl.Int64).alias("price_under"),
        pl.lit(None, dtype=pl.Float64).alias("p_over_raw"),
        pl.lit(None, dtype=pl.Float64).alias("p_under_raw"),
        pl.lit(None, dtype=pl.Float64).alias("overround"),
        "p_over_mult",
        "p_over_power",
        "n_books",
        pl.lit(None, dtype=pl.UInt32).alias("n_lines_two_sided"),
    )
    cons = cons.with_columns(
        pl.col("price_over").cast(pl.Int64), pl.col("price_under").cast(pl.Int64)
    )
    books = books.with_columns(
        pl.col("price_over").cast(pl.Int64), pl.col("price_under").cast(pl.Int64)
    )
    return pl.concat([books, cons.select(books.columns)]).sort(
        ["game_id", "snapshot_kind", "stat", "player_id", "book"]
    )


def reason_counts(built: dict[str, Any]) -> dict[str, Any]:
    """Quote-group reasons per snapshot kind and book (a group = one player's quotes of one stat
    at one book and snapshot): resolved / unresolved_name / one_sided / ambiguous_duplicate."""
    g = built["groups"]
    out: dict[str, Any] = {}
    unres_g = (
        built["unres"].group_by(["game_id", "snapshot_kind", "book", "stat", "player_name"]).len()
    )
    for kind in KINDS:
        out[kind] = {}
        for book in (*BOOKS[:3], "all"):
            gk = g.filter(pl.col("snapshot_kind") == kind)
            uk = unres_g.filter(pl.col("snapshot_kind") == kind)
            if book != "all":
                gk = gk.filter(pl.col("book") == book)
                uk = uk.filter(pl.col("book") == book)
            n_good = int((gk["n_good"] > 0).sum())
            rest = gk.filter(pl.col("n_good") == 0)
            amb = int(rest["any_dup"].sum())
            out[kind][book] = {
                "quote_groups": int(gk.height) + int(uk.height),
                "quoted_two_sided": n_good,
                "one_sided": int(rest.height) - amb,
                "ambiguous_duplicate": amb,
                "unresolved_name": int(uk.height),
                "odd_line_groups": int(gk["any_odd"].sum()),
            }
    return out


def unresolved_names(built: dict[str, Any]) -> pl.DataFrame:
    out: pl.DataFrame = (
        built["unres"]
        .group_by("player_name")
        .agg(
            pl.len().alias("rows"),
            pl.col("game_id").n_unique().alias("games"),
            pl.col("book").n_unique().alias("books"),
        )
        .sort("rows", descending=True)
    )
    return out


def load_quotes(season: int, odds_db: Path = ODDS_DB) -> pl.DataFrame:
    import duckdb

    assert season in SEASONS, season
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        markets = ",".join(f"'{m}'" for m in MARKET_STAT)
        return con.execute(
            "SELECT game_id, snapshot_kind, snapshot_ts, requested_at, book, market, player_name, "
            "player_id, side, point, price_american, implied_prob FROM odds_history "
            f"WHERE season = {int(season)} AND market IN ({markets})"
        ).pl()
    finally:
        con.close()


def load_played(season: int, db_path: str = "nba.duckdb") -> pl.DataFrame:
    """(game_id, player_id, minutes) for every box-score row of ``season`` (read-only)."""
    import duckdb

    assert season <= MAX_SEASON
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT s.game_id, CAST(s.player_id AS BIGINT) AS player_id, s.minutes "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season = {int(season)}"
        ).pl()
    finally:
        con.close()


def load_tips(season: int, db_path: str = "nba.duckdb") -> pl.DataFrame:
    """(game_id, home_team, away_team, tipoff_utc) for games with a REAL tip (no proxy)."""
    import duckdb

    from nba.features.game_tipoff import build_game_tipoff

    assert season <= MAX_SEASON
    con = duckdb.connect(db_path, read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, home_team, away_team, home_pts, away_pts FROM games "
            f"WHERE season = {int(season)}"
        ).pl()
    finally:
        con.close()
    tips = build_game_tipoff(str(ROOT / "data" / "schedule")).select("game_id", "tipoff_utc")
    return g.join(tips, on="game_id", how="left")


def coverage_vs_played(
    market: pl.DataFrame, played: pl.DataFrame, games_with_odds: dict[str, set[str]]
) -> dict[str, Any]:
    """`no_quote` against the box-score universe (descriptive; the model-row universe is a subset):
    played player-games (minutes > 0) and played >= 20 min in games the pull covers, with a
    two-sided main-line quote per stat/book/snapshot."""
    pl_all = played.filter(pl.col("minutes") > 0)
    out: dict[str, Any] = {}
    for kind in KINDS:
        gids = games_with_odds[kind]
        base = pl_all.filter(pl.col("game_id").is_in(list(gids)))
        for thr_name, thr in (("any", 0.0), ("ge20", 20.0)):
            uni = base.filter(pl.col("minutes") >= thr) if thr else base
            for stat in STATS:
                for book in BOOKS:
                    m = market.filter(
                        (pl.col("snapshot_kind") == kind)
                        & (pl.col("stat") == stat)
                        & (pl.col("book") == book)
                    ).select("game_id", "player_id")
                    j = uni.join(m, on=["game_id", "player_id"], how="inner")
                    out[f"{kind}/{thr_name}/{stat}/{book}"] = {
                        "played": int(uni.height),
                        "quoted": int(j.height),
                        "no_quote": int(uni.height - j.height),
                        "share": float(j.height / uni.height) if uni.height else None,
                    }
    return out


def game_market_rows(season: int, odds_db: Path = ODDS_DB) -> pl.DataFrame:
    """h2h moneylines: one row per (game, snapshot_kind, source in {pinnacle, consensus}) with the
    de-vigged P(home win) (multiplicative primary, power sensitivity). 'consensus' = median over
    every book with both sides. Prices only."""
    import duckdb

    assert season in SEASONS
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        h = con.execute(
            "SELECT game_id, snapshot_kind, snapshot_ts, book, side, price_american FROM "
            f"odds_history WHERE season = {int(season)} AND market = 'h2h' "
            "AND side IN ('home','away')"
        ).pl()
    finally:
        con.close()
    return h2h_table(h)


def h2h_table(h: pl.DataFrame) -> pl.DataFrame:
    k = ["game_id", "snapshot_kind", "book"]
    w = h.group_by(k).agg(
        (pl.col("side") == "home").sum().alias("nh"),
        (pl.col("side") == "away").sum().alias("na"),
        pl.col("price_american").filter(pl.col("side") == "home").first().alias("price_home"),
        pl.col("price_american").filter(pl.col("side") == "away").first().alias("price_away"),
        pl.col("snapshot_ts").first().alias("snapshot_ts"),
    )
    w = w.filter((pl.col("nh") == 1) & (pl.col("na") == 1))
    rh, ra = american_prob(w["price_home"].to_numpy()), american_prob(w["price_away"].to_numpy())
    w = w.with_columns(
        pl.Series("p_home_mult", devig_mult(rh, ra)), pl.Series("p_home_power", devig_power(rh, ra))
    )
    pin = w.filter(pl.col("book") == "pinnacle").select(
        "game_id",
        "snapshot_kind",
        "snapshot_ts",
        pl.lit("pinnacle").alias("source"),
        "p_home_mult",
        "p_home_power",
        pl.lit(1).cast(pl.UInt32).alias("n_books"),
        pl.col("price_home").cast(pl.Int64),
        pl.col("price_away").cast(pl.Int64),
    )
    cons = (
        w.group_by(["game_id", "snapshot_kind"])
        .agg(
            pl.col("p_home_mult").median(),
            pl.col("p_home_power").median(),
            pl.len().cast(pl.UInt32).alias("n_books"),
            pl.col("snapshot_ts").first(),
        )
        .with_columns(
            pl.lit("consensus").alias("source"),
            pl.lit(None, dtype=pl.Int64).alias("price_home"),
            pl.lit(None, dtype=pl.Int64).alias("price_away"),
        )
    )
    return pl.concat([pin, cons.select(pin.columns)]).sort(["game_id", "snapshot_kind", "source"])


def market_rows(
    season: int,
    db_path: str = "nba.duckdb",
    out_dir: Path = OUT_DIR,
    holdout_row: str | None = None,
    odds_db: Path = ODDS_DB,
) -> dict[str, Any]:
    """Stage 2. Prices only: no model row is read or joined."""
    guard_season(season, holdout_row)
    out_dir.mkdir(parents=True, exist_ok=True)
    q = load_quotes(season, odds_db)
    built = build_lines(q)
    table = market_table(built["good"])
    # cross-check the multiplicative de-vig against the table's own implied_prob
    ov = (
        q.filter((pl.col("side") == "over") & pl.col("implied_prob").is_not_null())
        .filter(pl.col("player_id").is_not_null())
        .select(
            "game_id",
            "snapshot_kind",
            "book",
            "player_id",
            pl.col("market").replace_strict(MARKET_STAT, default=None).alias("stat"),
            "point",
            pl.col("implied_prob").alias("ip"),
        )
    )
    chk = built["good"].join(ov, on=[*KEY, "point"], how="inner")
    devig_max_diff = (
        float(np.abs(chk["p_over_mult"].to_numpy() - chk["ip"].to_numpy()).max())
        if chk.height
        else None
    )
    reasons = reason_counts(built)
    names = unresolved_names(built)
    table.write_parquet(out_dir / f"market_rows_{season}.parquet")
    names.write_csv(out_dir / f"market_unresolved_names_{season}.csv")
    gm = game_market_rows(season, odds_db)
    gm.write_parquet(out_dir / f"game_market_{season}.parquet")
    # coverage against the box-score universe and the real-tip game list
    played = load_played(season, db_path)
    tips = load_tips(season, db_path)
    real = set(tips.filter(pl.col("tipoff_utc").is_not_null())["game_id"].to_list())
    with_odds: dict[str, set[str]] = {
        k: set(q.filter(pl.col("snapshot_kind") == k)["game_id"].unique().to_list()) for k in KINDS
    }
    unmatched = {k: sorted(real - with_odds[k]) for k in KINDS}
    cov = coverage_vs_played(table, played, with_odds)
    n_main = {
        k: {
            b: int(table.filter((pl.col("snapshot_kind") == k) & (pl.col("book") == b)).height)
            for b in BOOKS
        }
        for k in KINDS
    }
    snap_gap = (
        table.join(tips.select("game_id", "tipoff_utc"), on="game_id", how="left")
        .filter(pl.col("snapshot_kind") == "t60")
        .with_columns(
            ((pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_seconds() / 60.0).alias("m")
        )
    )
    meta = {
        "season": season,
        "rule_status": "DRAFT",
        "n_market_rows": table.height,
        "main_line_rows": n_main,
        "reasons": reasons,
        "unmatched_event": {
            "games_with_real_tip": len(real),
            "by_kind": {k: len(v) for k, v in unmatched.items()},
            "game_ids": {k: v[:20] for k, v in unmatched.items()},
        },
        "unresolved_names": {
            "distinct": names.height,
            "rows": int(names["rows"].sum()) if names.height else 0,
            "top": names.head(15).to_dicts(),
        },
        "coverage_vs_box_score": cov,
        "devig_vs_table_max_abs_diff": devig_max_diff,
        "t60_snapshot_minutes_before_tip": {
            "min": float(snap_gap["m"].min()),  # type: ignore[arg-type]
            "share_ge_60": float((snap_gap["m"] >= 60.0).mean()),  # type: ignore[arg-type]
        },
        "game_market_rows": gm.height,
        "git_sha": git_sha(),
        "choices": CHOICES,
    }
    write_json(out_dir / f"market_rows_{season}.meta.json", meta)
    return meta


# ======================================================================= STAGE 2b: game rows (H3)


def game_rows(
    season: int,
    db_path: str = "nba.duckdb",
    out_dir: Path = OUT_DIR,
    holdout_row: str | None = None,
    config_path: str = "configs/injury_elo.yaml",
) -> dict[str, Any]:
    """H3 model side: the rung0_injury_elo walk-forward OOF win probabilities (real-tip report
    gating, as in production), regenerated with ``nba.eval.injury_elo_eval`` and compared with the
    stored ``data/injury_elo_realtip`` file."""
    guard_season(season, holdout_row)
    import duckdb

    from nba.eval.injury_elo_eval import load_config, run_injury_elo_eval

    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(config_path)
    tmp = out_dir / "_game_oof_tmp"
    con = duckdb.connect(db_path, read_only=True)
    try:
        res = run_injury_elo_eval(con, cfg, tmp)
    finally:
        con.close()
    oof = pl.read_parquet(tmp / "oof_predictions.parquet")
    shutil.rmtree(tmp, ignore_errors=True)  # also removes the ORACLE file, never loaded
    mine = oof.filter((pl.col("model") == "rung0_injury_elo") & (pl.col("season") == season))
    tips = load_tips(season, db_path)
    y = tips.select(
        "game_id", "tipoff_utc", (pl.col("home_pts") > pl.col("away_pts")).alias("home_win")
    )
    rows = mine.select("game_id", "game_date", "season", "p", "made_with_data_through").join(
        y, on="game_id", how="left"
    )
    check: dict[str, Any] = {"n": rows.height}
    if REALTIP_OOF.exists():
        ref = pl.read_parquet(REALTIP_OOF).filter(
            (pl.col("model") == "rung0_injury_elo") & (pl.col("season") == season)
        )
        j = rows.join(ref.select("game_id", pl.col("p").alias("p0")), on="game_id", how="inner")
        check["matched_to_stored"] = j.height
        check["max_abs_dp_vs_stored_realtip"] = float((j["p"] - j["p0"]).abs().max())  # type: ignore[arg-type]
    rows.write_parquet(out_dir / f"game_rows_{season}.parquet")
    meta = {
        "season": season,
        "n_games": rows.height,
        "check": check,
        "overall_injury_elo_log_loss_all_oof": res["variants"]["injury_elo"]["log_loss"],
        "git_sha": git_sha(),
    }
    write_json(out_dir / f"game_rows_{season}.meta.json", meta)
    return meta


# ============================================================================= STAGE 3: score


def ll_vec(p: np.ndarray, o: np.ndarray, eps: float) -> np.ndarray:
    """Per-unit log loss of probability ``p`` of event ``o`` (0/1), clipped to [eps, 1-eps]."""
    pc = np.clip(np.asarray(p, dtype=float), eps, 1.0 - eps)
    return np.asarray(-np.where(np.asarray(o) > 0.5, np.log(pc), np.log1p(-pc)))


def brier_vec(p: np.ndarray, o: np.ndarray) -> np.ndarray:
    return np.asarray((np.asarray(p, dtype=float) - np.asarray(o, dtype=float)) ** 2)


def model_p_over(
    pge: np.ndarray, line: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Model P(over L) per row from survival vectors ``pge[i, k-1] = P(Y_i >= k)``.

    Returns ``(p_over, over_event, status)``; ``status``: 0 = scored, 1 = push (Y == L, dropped),
    2 = odd line (not a multiple of 0.5), 3 = model puts all mass on the push (undefined)."""
    n, kmax = pge.shape
    line = np.asarray(line, dtype=float)
    twice = line * 2.0
    odd = np.abs(twice - np.round(twice)) > 1e-9
    whole = (np.abs(line - np.round(line)) < 1e-9) & ~odd

    def ge(nn: np.ndarray) -> np.ndarray:
        nn = nn.astype(int)
        idx = np.clip(nn - 1, 0, kmax - 1)
        v = pge[np.arange(n), idx]
        return np.where(nn <= 0, 1.0, np.where(nn > kmax, 0.0, v))

    fl = np.floor(line)
    p_half = ge(fl + 1)  # L = k + 0.5 -> P(Y >= k + 1)
    k = np.round(line)
    p_gt = ge(k + 1)  # whole: P(Y >= k + 1)
    p_lt = 1.0 - ge(k)  # P(Y <= k - 1)
    denom = p_gt + p_lt
    with np.errstate(divide="ignore", invalid="ignore"):
        p_whole = np.where(denom > 0, p_gt / denom, np.nan)
    p = np.where(whole, p_whole, p_half)
    status = np.zeros(n, dtype=int)
    status[odd] = 2
    status[whole & (np.asarray(y) == k)] = 1
    status[whole & (denom <= 0) & (np.asarray(y) != k)] = 3
    over = (np.asarray(y, dtype=float) > line).astype(float)
    return np.asarray(p), over, status


def boot_ci(
    delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT, seed: int = BOOT_SEED
) -> dict[str, float]:
    """Percentile CI of mean(delta), game-clustered (whole games resampled), two-sided
    bootstrap p. A fresh ``default_rng(seed)`` per call keeps every test independent of order."""
    from nba.props.metrics import _clustered_boot_means

    delta = np.asarray(delta, dtype=float)
    point = float(delta.mean()) if delta.size else float("nan")
    if delta.size < 2 or np.allclose(delta, 0.0):
        return {
            "point": point,
            "lo": point,
            "hi": point,
            "p": 1.0 if delta.size else float("nan"),
            "n": int(delta.size),
            "n_games": int(len(np.unique(gid))),
        }
    b = _clustered_boot_means(delta, np.asarray(gid), n_boot, np.random.default_rng(seed))
    p = 2.0 * min(float(np.mean(b >= 0.0)), float(np.mean(b <= 0.0)))
    return {
        "point": point,
        "lo": float(np.quantile(b, 0.025)),
        "hi": float(np.quantile(b, 0.975)),
        "p": float(min(max(p, 1.0 / (n_boot + 1)), 1.0)),
        "n": int(delta.size),
        "n_games": int(len(np.unique(gid))),
    }


def holm_adjust(p: list[float] | np.ndarray) -> np.ndarray:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    order = np.argsort(p, kind="stable")
    adj = np.empty(m)
    run = 0.0
    for rank, idx in enumerate(order):
        run = max(run, (m - rank) * p[idx])
        adj[idx] = min(run, 1.0)
    return adj


def verdict(point: float, hi: float, holm_p: float | None, floor: float = FLOOR) -> dict[str, Any]:
    """Section 3 labels. ``holm_p`` None = untested column (floor + CI only)."""
    if abs(point) < floor:
        return {"label": "indistinguishable", "qualifier": ""}
    holm_ok = True if holm_p is None else holm_p < ALPHA
    if point <= -floor and hi < 0 and holm_ok:
        return {"label": "beats_market", "qualifier": ""}
    q = ""
    if point <= -floor:
        q = "negative_delta_ci_not_below_0" if hi >= 0 else "negative_delta_holm_not_significant"
    return {"label": "market_better", "qualifier": q}


def logit(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(p) - np.log1p(-p))


def blend_p(p_mkt: np.ndarray, p_model: np.ndarray, w: float) -> np.ndarray:
    """``logit(p) = logit(p_mkt) + w (logit(p_model) - logit(p_mkt))`` with the model clipped."""
    pm = np.clip(p_model, MODEL_EPS, 1.0 - MODEL_EPS)
    pk = np.clip(p_mkt, MARKET_EPS, 1.0 - MARKET_EPS)
    z = logit(pk) + w * (logit(pm) - logit(pk))
    return np.asarray(1.0 / (1.0 + np.exp(-z)))


def fit_blend_weight(
    p_mkt: np.ndarray, p_model: np.ndarray, over: np.ndarray, grid: tuple[float, ...] = W_GRID
) -> tuple[float, dict[float, float]]:
    """w in the grid minimising mean log loss of the blend (ties -> the smaller w)."""
    curve = {w: float(ll_vec(blend_p(p_mkt, p_model, w), over, MARKET_EPS).mean()) for w in grid}
    best = min(grid, key=lambda w: (round(curve[w], 12), w))
    return best, curve


def reliability(p: np.ndarray, o: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    from nba.truth.metrics import calibration_curve

    c = calibration_curve(o, p, n_bins)
    return [
        {"pred": a, "obs": b, "n": float(n)}
        for a, b, n in zip(c.bin_mean_pred, c.bin_observed_rate, c.bin_count, strict=True)
    ]


# ---------------------------------------------------------------------------- pairing


MODEL_JOIN_COLS: tuple[str, ...] = (
    "y",
    "p_ge",
    "mean_int",
    "std_int",
    "m_recency",
    "sd_recency",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "is_home_f",
)


def pairs_for_season(
    model: pl.DataFrame,
    market: pl.DataFrame,
    played: pl.DataFrame,
    tips: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """JOIN model rows to market rows (the only place a prediction meets a price) and compute
    the per-unit probabilities. Returns the paired frame (status 0 only) and the reason counts."""
    model = model.with_columns(pl.col("player_id").cast(pl.Int64)).with_row_index("_mi")
    market = market.with_columns(pl.col("player_id").cast(pl.Int64))
    played = played.with_columns(pl.col("player_id").cast(pl.Int64))
    keys = ["game_id", "player_id", "stat"]
    mk = market.join(model.select(*keys, "_mi"), on=keys, how="left")
    dnp = (
        played.filter(pl.col("minutes").is_null() | (pl.col("minutes") <= 0))
        .select("game_id", "player_id")
        .unique()
        .with_columns(pl.lit(True).alias("_dnp"))
    )
    mk = mk.join(dnp, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("_dnp").fill_null(False)
    )
    counts: dict[str, Any] = {}
    paired_parts: list[pl.DataFrame] = []
    for stat in STATS:
        mm = model.filter(pl.col("stat") == stat)
        mks = mk.filter(pl.col("stat") == stat)
        have = mks.filter(pl.col("_mi").is_not_null())
        j = have.join(mm.select("_mi", *MODEL_JOIN_COLS), on="_mi", how="left")
        if j.is_empty():
            continue
        width = int(j["p_ge"].list.len().max())  # type: ignore[arg-type]
        pge = j["p_ge"].list.to_array(width).to_numpy().astype(float)
        p_over, over, status = model_p_over(pge, j["line"].to_numpy(), j["y"].to_numpy())
        j = j.drop("p_ge").with_columns(
            pl.Series("p_model", p_over), pl.Series("over", over), pl.Series("status", status)
        )
        paired_parts.append(j.filter(pl.col("status") == 0))
        for kind in KINDS:
            for book in BOOKS:
                a = mks.filter((pl.col("snapshot_kind") == kind) & (pl.col("book") == book))
                b = j.filter((pl.col("snapshot_kind") == kind) & (pl.col("book") == book))
                counts[f"{kind}/{stat}/{book}"] = {
                    "market_rows": int(a.height),
                    "paired": int((b["status"] == 0).sum()),
                    "push": int((b["status"] == 1).sum()),
                    "odd_line": int((b["status"] == 2).sum()),
                    "model_push_certain": int((b["status"] == 3).sum()),
                    "dnp": int(a.filter(pl.col("_mi").is_null() & pl.col("_dnp")).height),
                    "no_model_row": int(a.filter(pl.col("_mi").is_null() & ~pl.col("_dnp")).height),
                    "model_rows": int(mm.height),
                    "no_quote": int(mm.height - b.height),
                }
    pairs = pl.concat(paired_parts)
    pairs = pairs.join(
        tips.select("game_id", "tipoff_utc").unique("game_id"), on="game_id", how="left"
    )
    return pairs, counts


def leak_guard(pairs: pl.DataFrame, lead_min: int = LEAD_MIN) -> dict[str, Any]:
    """Section 5.5: any t60 pair with snapshot_ts > real tip - 60 min (or no real tip) voids
    the run. t5 is later than the as-of by construction and is not a leak."""
    t = pairs.filter(pl.col("snapshot_kind") == "t60")
    asof = pl.col("tipoff_utc") - pl.duration(minutes=lead_min)
    bad = t.filter(pl.col("tipoff_utc").is_null() | (pl.col("snapshot_ts") > asof))
    res = {"t60_pairs": int(t.height), "violations": int(bad.height)}
    if bad.height:
        raise RuleError(f"leak guard: {bad.height} t60 pair(s) with snapshot_ts after the as-of")
    return res


# ---------------------------------------------------------------------------- slices


def tertile_edges(x: np.ndarray) -> tuple[float, float]:
    e = np.quantile(x[np.isfinite(x)], [1 / 3, 2 / 3])
    return float(e[0]), float(e[1])


def thirds(x: np.ndarray, edges: tuple[float, float]) -> np.ndarray:
    return np.where(x <= edges[0], "low", np.where(x <= edges[1], "mid", "high"))


def add_slice_columns(pairs: pl.DataFrame, model_universe: pl.DataFrame) -> pl.DataFrame:
    """Slice labels (see CHOICES['slices']). Tertile edges come from the stat's model-row
    universe in that season (vol, tier) and from the stat's pooled pairs (line distance)."""
    parts: list[pl.DataFrame] = []
    for stat in STATS:
        u = model_universe.filter(pl.col("stat") == stat)
        vol_u = u["sd_recency"].to_numpy() / np.maximum(u["m_recency"].to_numpy(), 1.0)
        e_vol = tertile_edges(vol_u)
        e_tier = tertile_edges(u["m_recency"].to_numpy())
        p = pairs.filter(pl.col("stat") == stat)
        if p.is_empty():
            continue
        vol = p["sd_recency"].to_numpy() / np.maximum(p["m_recency"].to_numpy(), 1.0)
        dist = np.abs(p["line"].to_numpy() - p["mean_int"].to_numpy()) / np.maximum(
            p["std_int"].to_numpy(), 1e-6
        )
        e_dist = tertile_edges(dist)
        lean_over = p["p_model"].to_numpy() > p["p_over_mult"].to_numpy()
        tm = np.where(
            p["has_report"].to_numpy() == 0,
            "no_report",
            np.where(p["n_out_rot"].to_numpy() > 0, "teammate_out", "report_none_out"),
        )
        parts.append(
            p.with_columns(
                pl.Series("sl_vol", thirds(vol, e_vol)),
                pl.Series("sl_tier", thirds(p["m_recency"].to_numpy(), e_tier)),
                pl.Series(
                    "sl_role", np.where(p["starter10"].to_numpy() >= 0.5, "starter", "bench")
                ),
                pl.Series("sl_dist", thirds(dist, e_dist)),
                pl.Series("sl_side", np.where(lean_over, "lean_over", "lean_under")),
                pl.Series("sl_home", np.where(p["is_home_f"].to_numpy() == 1, "home", "away")),
                pl.Series(
                    "sl_phase", np.where(p["team_game_no"].to_numpy() < 15, "first15", "rest")
                ),
                pl.Series("sl_tm", tm),
            )
        )
    return pl.concat(parts)


SLICE_COLS: tuple[str, ...] = (
    "sl_vol",
    "sl_tier",
    "sl_role",
    "sl_dist",
    "sl_side",
    "sl_home",
    "sl_phase",
    "sl_tm",
)


# ---------------------------------------------------------------------------- cells


def cell_arrays(pairs: pl.DataFrame, stat: str, book: str, kind: str) -> pl.DataFrame:
    return pairs.filter(
        (pl.col("stat") == stat) & (pl.col("book") == book) & (pl.col("snapshot_kind") == kind)
    )


def h1_cell(c: pl.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    """Model vs market (multiplicative primary) on one cell: log loss, Brier, power sensitivity."""
    if c.is_empty():
        return {"n": 0}
    o = c["over"].to_numpy()
    pm, pk, pp = c["p_model"].to_numpy(), c["p_over_mult"].to_numpy(), c["p_over_power"].to_numpy()
    gid = c["game_id"].to_numpy()
    ll_m, ll_k = ll_vec(pm, o, MODEL_EPS), ll_vec(pk, o, MARKET_EPS)
    ci = boot_ci(ll_m - ll_k, gid, n_boot)
    ci_p = boot_ci(ll_m - ll_vec(pp, o, MARKET_EPS), gid, n_boot)
    ci_b = boot_ci(
        brier_vec(np.clip(pm, MODEL_EPS, 1 - MODEL_EPS), o) - brier_vec(pk, o), gid, n_boot
    )
    return {
        "n": ci["n"],
        "n_games": ci["n_games"],
        "ll_model": float(ll_m.mean()),
        "ll_market": float(ll_k.mean()),
        "delta": {k: ci[k] for k in ("point", "lo", "hi", "p")},
        "delta_power_devig": {k: ci_p[k] for k in ("point", "lo", "hi", "p")},
        "brier_delta": {k: ci_b[k] for k in ("point", "lo", "hi")},
        "rel_model": reliability(np.clip(pm, MODEL_EPS, 1 - MODEL_EPS), o),
        "rel_market": reliability(pk, o),
        "over_rate": float(o.mean()),
    }


def h2_cell(c_fit: pl.DataFrame, c_test: pl.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    """Blend weight fit on the fit season's cell, scored frozen on the test season's cell."""
    if c_fit.is_empty() or c_test.is_empty():
        return {"n": 0}
    w, curve = fit_blend_weight(
        c_fit["p_over_mult"].to_numpy(), c_fit["p_model"].to_numpy(), c_fit["over"].to_numpy()
    )
    o = c_test["over"].to_numpy()
    pk = c_test["p_over_mult"].to_numpy()
    pb = blend_p(pk, c_test["p_model"].to_numpy(), w)
    d = ll_vec(pb, o, MARKET_EPS) - ll_vec(pk, o, MARKET_EPS)
    ci = boot_ci(d, c_test["game_id"].to_numpy(), n_boot)
    return {
        "w": w,
        "w_curve": {f"{k:.2f}": v for k, v in curve.items()},
        "n_fit": int(c_fit.height),
        "n": ci["n"],
        "n_games": ci["n_games"],
        "ll_blend": float(ll_vec(pb, o, MARKET_EPS).mean()),
        "ll_market": float(ll_vec(pk, o, MARKET_EPS).mean()),
        "delta": {k: ci[k] for k in ("point", "lo", "hi", "p")},
    }


def slice_table(c: pl.DataFrame, n_boot: int = N_BOOT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if c.is_empty():
        return out
    o = c["over"].to_numpy()
    d = ll_vec(c["p_model"].to_numpy(), o, MODEL_EPS) - ll_vec(
        c["p_over_mult"].to_numpy(), o, MARKET_EPS
    )
    gid = c["game_id"].to_numpy()
    for col in SLICE_COLS:
        vals = c[col].to_numpy()
        for v in sorted(set(vals.tolist())):
            m = vals == v
            if m.sum() < 30:
                continue
            ci = boot_ci(d[m], gid[m], n_boot)
            out.append(
                {"slice": f"{col}={v}", "n": ci["n"], **{k: ci[k] for k in ("point", "lo", "hi")}}
            )
    return out


def clv_cell(c60: pl.DataFrame, c5: pl.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    """T-60 -> T-5 movement in the model's direction (see CHOICES['clv'])."""
    if c60.is_empty() or c5.is_empty():
        return {"n": 0}
    keys = ["game_id", "player_id"]
    a = c60.select(*keys, "line", "p_over_mult", "p_model")
    b = c5.select(*keys, pl.col("line").alias("line5"), pl.col("p_over_mult").alias("p5"))
    j = a.join(b, on=keys, how="inner")
    if j.is_empty():
        return {"n": 0}
    lean_over = j["p_model"].to_numpy() > j["p_over_mult"].to_numpy()
    dl = j["line5"].to_numpy() - j["line"].to_numpy()
    dp = j["p5"].to_numpy() - j["p_over_mult"].to_numpy()
    moved = dl != 0
    sig = np.where(moved, np.sign(dl), np.sign(dp))
    keep = sig != 0
    agree = ((sig > 0) == lean_over).astype(float)[keep]
    gid = j["game_id"].to_numpy()[keep]
    if agree.size == 0:
        return {"n": 0}
    ci = boot_ci(agree - 0.5, gid, n_boot)
    return {
        "n": int(agree.size),
        "n_line_moved": int((moved & keep).sum()),
        "agree_rate": float(agree.mean()),
        "agree_minus_half": {k: ci[k] for k in ("point", "lo", "hi")},
    }


def payout(price: np.ndarray, win: np.ndarray) -> np.ndarray:
    a = np.asarray(price, dtype=float)
    profit = np.where(a > 0, a / 100.0, 100.0 / np.abs(a))
    return np.where(win > 0.5, profit, -1.0)


def ev_cell(
    c: pl.DataFrame, p_use: np.ndarray, edge_min: float = EDGE_MIN, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """Realised return per unit stake of the pairs with edge >= edge_min at the book's own price."""
    if c.is_empty() or c["price_over"].null_count() == c.height:
        return {"n": 0}
    pk = c["p_over_mult"].to_numpy()
    o = c["over"].to_numpy()
    edge_over = p_use - pk
    edge_under = (1.0 - p_use) - (1.0 - pk)
    bet_over = edge_over >= edge_min
    bet_under = edge_under >= edge_min
    sel = bet_over | bet_under
    if not sel.any():
        return {"n": 0}
    price = np.where(bet_over, c["price_over"].to_numpy(), c["price_under"].to_numpy())[sel]
    win = np.where(bet_over, o, 1.0 - o)[sel]
    ret = payout(price, win)
    ci = boot_ci(ret, c["game_id"].to_numpy()[sel], n_boot)
    return {
        "n": int(sel.sum()),
        "n_over": int(bet_over.sum()),
        "n_under": int(bet_under.sum()),
        "hit_rate": float(win.mean()),
        "mean_return": {k: ci[k] for k in ("point", "lo", "hi")},
        "positive_ev_claim": bool(ci["lo"] > 0),
    }


# ---------------------------------------------------------------------------- H3


def h3_games(
    game_rows_df: pl.DataFrame, game_market_df: pl.DataFrame, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """rung0_injury_elo OOF vs the de-vigged Pinnacle moneyline (else consensus) at T-60."""
    gm = game_market_df.filter(pl.col("snapshot_kind") == "t60")
    pin = gm.filter(pl.col("source") == "pinnacle").select(
        "game_id", pl.col("p_home_mult").alias("p_pin"), pl.col("p_home_power").alias("pw_pin")
    )
    con = gm.filter(pl.col("source") == "consensus").select(
        "game_id", pl.col("p_home_mult").alias("p_con"), pl.col("p_home_power").alias("pw_con")
    )
    j = (
        game_rows_df.join(pin, on="game_id", how="left")
        .join(con, on="game_id", how="left")
        .with_columns(
            pl.coalesce("p_pin", "p_con").alias("p_mkt"),
            pl.coalesce("pw_pin", "pw_con").alias("pw_mkt"),
            pl.col("p_pin").is_not_null().alias("src_pinnacle"),
        )
        .filter(pl.col("p_mkt").is_not_null() & pl.col("home_win").is_not_null())
    )
    if j.is_empty():
        return {"n": 0}
    o = j["home_win"].cast(pl.Float64).to_numpy()
    pm, pk, pw = j["p"].to_numpy(), j["p_mkt"].to_numpy(), j["pw_mkt"].to_numpy()
    ll_m, ll_k = ll_vec(pm, o, 1e-9), ll_vec(pk, o, MARKET_EPS)
    gid = j["game_id"].to_numpy()
    ci = boot_ci(ll_m - ll_k, gid, n_boot)
    ci_p = boot_ci(ll_m - ll_vec(pw, o, MARKET_EPS), gid, n_boot)
    ci_b = boot_ci(brier_vec(pm, o) - brier_vec(pk, o), gid, n_boot)
    v = verdict(ci["point"], ci["hi"], None)
    return {
        "n": ci["n"],
        "n_pinnacle": int(j["src_pinnacle"].sum()),
        "n_consensus_fallback": int((~j["src_pinnacle"]).sum()),
        "ll_model": float(ll_m.mean()),
        "ll_market": float(ll_k.mean()),
        "delta": {k: ci[k] for k in ("point", "lo", "hi", "p")},
        "delta_power_devig": {k: ci_p[k] for k in ("point", "lo", "hi")},
        "brier_delta": {k: ci_b[k] for k in ("point", "lo", "hi")},
        "verdict": v,
        "rel_model": reliability(pm, o),
        "rel_market": reliability(pk, o),
    }


# ---------------------------------------------------------------------------- orchestration


def load_model_rows(season: int, out_dir: Path) -> pl.DataFrame:
    meta = json.loads((out_dir / f"model_rows_{season}.meta.json").read_text())
    if not meta.get("a0_ok"):
        raise RuleError(f"model rows {season}: the A0 self-check did not pass; refusing")
    return pl.read_parquet(out_dir / f"model_rows_{season}.parquet")


def min_data_gates(
    season: int, out_dir: Path, market: pl.DataFrame, played: pl.DataFrame, tips: pl.DataFrame
) -> dict[str, Any]:
    """Section 5, checks 1-4 recomputed on the data the run uses (check 5 is the leak guard).
    Any failure means: stop, report, no model result."""
    meta = json.loads((out_dir / f"market_rows_{season}.meta.json").read_text())
    n_real = int(meta["unmatched_event"]["games_with_real_tip"])
    miss = max(int(v) for v in meta["unmatched_event"]["by_kind"].values())
    played20 = played.filter(pl.col("minutes") >= 20.0).select("game_id", "player_id")
    q = (
        market.filter(
            (pl.col("snapshot_kind") == "t60")
            & (pl.col("stat") == "pts")
            & pl.col("book").is_in(["pinnacle", "consensus"])
        )
        .select("game_id", "player_id")
        .unique()
    )
    cov = played20.join(q, on=["game_id", "player_id"], how="inner").height / max(
        played20.height, 1
    )
    allr = meta["reasons"]["t60"]["all"]
    resolved = 1.0 - allr["unresolved_name"] / max(allr["quote_groups"], 1)
    snap = (
        market.filter(pl.col("snapshot_kind") == "t60")
        .group_by("game_id")
        .agg(pl.col("snapshot_ts").min())
        .join(tips.select("game_id", "tipoff_utc"), on="game_id", how="left")
    )
    early = (
        (pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_seconds() / 60.0 >= 55.0
    ).fill_null(False)
    timing = float(snap.select(early.mean()).item()) if snap.height else 0.0

    def chk(name: str, value: float, thr: float) -> dict[str, Any]:
        return {"check": name, "value": value, "threshold": thr, "ok": bool(value >= thr)}

    checks = [
        chk("1 games with a real tip that have prices at t60 and t5", 1 - miss / n_real, 0.99),
        chk("2 players >=20 min with a pinnacle-or-consensus pts quote at t60", cov, 0.60),
        chk("3 posted props with a resolved player id", resolved, 0.97),
        chk("4 games with the t60 snapshot at least 55 min before tip", timing, 0.99),
    ]
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


def score_season(
    season: int,
    out_dir: Path = OUT_DIR,
    db_path: str = "nba.duckdb",
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Section 2-3 on one season. The join of model rows to prices happens here and nowhere else."""
    model = load_model_rows(season, out_dir)
    market = pl.read_parquet(out_dir / f"market_rows_{season}.parquet")
    played = load_played(season, db_path)
    tips = load_tips(season, db_path)
    gates = min_data_gates(season, out_dir, market, played, tips)
    if not gates["all_ok"]:
        return {"season": season, "gates": gates, "stopped": True}
    pairs, counts = pairs_for_season(model, market, played, tips)
    guard = leak_guard(pairs)
    pairs = add_slice_columns(pairs, model)
    res: dict[str, Any] = {
        "season": season,
        "gates": gates,
        "pairing_counts": counts,
        "leak_guard": guard,
        "h1": {},
        "slices": {},
        "clv": {},
        "choices": CHOICES,
    }
    for kind in KINDS:
        for stat in STATS:
            for book in BOOKS:
                c = cell_arrays(pairs, stat, book, kind)
                res["h1"][f"{kind}/{stat}/{book}"] = h1_cell(c, n_boot)
                if book in TESTED_BOOKS:
                    res["slices"][f"{kind}/{stat}/{book}"] = slice_table(c, n_boot)
    for stat in STATS:
        for book in BOOKS:
            res["clv"][f"{stat}/{book}"] = clv_cell(
                cell_arrays(pairs, stat, book, "t60"), cell_arrays(pairs, stat, book, "t5"), n_boot
            )
    # H2: weights from the select season, scored frozen on the report season
    res["h2"] = {}
    if season == REPORT_SEASON:
        fit_model = load_model_rows(SELECT_SEASON, out_dir)
        fit_market = pl.read_parquet(out_dir / f"market_rows_{SELECT_SEASON}.parquet")
        fit_pairs, _ = pairs_for_season(
            fit_model,
            fit_market,
            load_played(SELECT_SEASON, db_path),
            load_tips(SELECT_SEASON, db_path),
        )
        leak_guard(fit_pairs)
        for kind in KINDS:
            for stat in STATS:
                for book in BOOKS:
                    r = h2_cell(
                        cell_arrays(fit_pairs, stat, book, kind),
                        cell_arrays(pairs, stat, book, kind),
                        n_boot,
                    )
                    res["h2"][f"{kind}/{stat}/{book}"] = r
    apply_multiplicity(res, season)
    res["ev"] = ev_section(res, pairs, n_boot)
    if season == REPORT_SEASON:
        res["outcomes"] = outcomes(res)
    mmeta = json.loads((out_dir / f"market_rows_{season}.meta.json").read_text())
    res["market_reasons"] = {
        "quote_groups": mmeta["reasons"],
        "unmatched_event": mmeta["unmatched_event"],
        "unresolved_names": mmeta["unresolved_names"],
        "coverage_vs_box_score": mmeta["coverage_vs_box_score"],
    }
    return res


def apply_multiplicity(res: dict[str, Any], season: int) -> None:
    """Holm over the family per snapshot kind (tested books only) and the verdict labels.

    The family size is fixed by the rule (16 = 4 stats x {H1, H2} x 2 books on the report season;
    8 on the select season, where H2 is not scored); a missing cell counts as p = 1."""
    expected = len(STATS) * len(TESTED_BOOKS) * (2 if season == REPORT_SEASON else 1)
    for kind in KINDS:
        fam: list[tuple[str, str]] = []
        ps: list[float] = []
        for stat in STATS:
            for book in TESTED_BOOKS:
                for h in ("h1", "h2"):
                    cell = res[h].get(f"{kind}/{stat}/{book}")
                    if cell and cell.get("n", 0) > 0:
                        fam.append((h, f"{kind}/{stat}/{book}"))
                        ps.append(cell["delta"]["p"])
        adj = holm_adjust(ps + [1.0] * (expected - len(ps))) if ps else np.array([])
        for (h, key), a in zip(fam, adj[: len(fam)], strict=True):
            res[h][key]["holm_p"] = float(a)
            res[h][key]["holm_family_size"] = expected
            d = res[h][key]["delta"]
            res[h][key]["verdict"] = verdict(d["point"], d["hi"], float(a))
    for h in ("h1", "h2"):
        for cell in res[h].values():
            if cell.get("n", 0) > 0 and "verdict" not in cell:
                d = cell["delta"]
                cell["verdict"] = verdict(d["point"], d["hi"], None)  # untested column


def ev_section(res: dict[str, Any], pairs: pl.DataFrame, n_boot: int) -> dict[str, Any]:
    """Descriptive money, only for T-60 cells that beat the market on H1 or H2."""
    out: dict[str, Any] = {}
    for stat in STATS:
        for book in BOOKS:
            key = f"t60/{stat}/{book}"
            passed = [
                h
                for h in ("h1", "h2")
                if res[h].get(key, {}).get("verdict", {}).get("label") == "beats_market"
            ]
            if not passed or book == "consensus":
                continue
            c = cell_arrays(pairs, stat, book, "t60")
            r: dict[str, Any] = {"passed": passed}
            if "h1" in passed:
                r["model"] = ev_cell(c, c["p_model"].to_numpy(), EDGE_MIN, n_boot)
            if "h2" in passed:
                w = res["h2"][key]["w"]
                r["blend"] = ev_cell(
                    c,
                    blend_p(c["p_over_mult"].to_numpy(), c["p_model"].to_numpy(), w),
                    EDGE_MIN,
                    n_boot,
                )
            out[key] = r
    return out


def outcomes(res: dict[str, Any]) -> dict[str, Any]:
    """Section 7 per stat, on Pinnacle: a pass (H1 or H2 beats the market) at T-5 is the strongest
    result, at T-60 only a timing edge, otherwise the market is better."""
    out: dict[str, Any] = {}
    for stat in STATS:

        def passed(kind: str, stat: str = stat) -> bool:
            return any(
                res[h].get(f"{kind}/{stat}/pinnacle", {}).get("verdict", {}).get("label")
                == "beats_market"
                for h in ("h1", "h2")
            )

        p5, p60 = passed("t5"), passed("t60")
        out[stat] = {
            "pinnacle_t5_pass": p5,
            "pinnacle_t60_pass": p60,
            "outcome": "strongest" if p5 else ("timing_edge_t60_only" if p60 else "market_better"),
        }
    return out


def score_games(season: int, out_dir: Path, n_boot: int = N_BOOT) -> dict[str, Any]:
    g = pl.read_parquet(out_dir / f"game_rows_{season}.parquet")
    gm = pl.read_parquet(out_dir / f"game_market_{season}.parquet")
    return h3_games(g, gm, n_boot)


# ---------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return ""
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _ci_s(d: dict[str, float]) -> str:
    return f"{_f(d['point'])} [{_f(d['lo'])}, {_f(d['hi'])}]"


def render_report(results: dict[int, dict[str, Any]], frozen_sha: str) -> str:
    ln = [
        "# ODDS_HISTORY results (rule: docs/prereg/ODDS_HISTORY.md)",
        "",
        f"Frozen block sha256: `{frozen_sha}`. Paired log-loss delta = model (or blend) minus "
        f"market; negative is better. 95% percentile CI clustered by game_id, B={N_BOOT}, "
        f"seed {BOOT_SEED}. Floor {FLOOR}. Verdict label per section 3. n = paired units. The "
        "model rows are the T-60 production predictions in BOTH snapshot kinds: the T-5 columns "
        "ask whether the T-60 model beats the later market.",
        "",
    ]
    for season in sorted(results):
        r = results[season]
        tag = "report season (formal tests)" if season == REPORT_SEASON else "select season"
        ln += [f"## Season {season} - {tag}", ""]
        ln += ["### Minimum data checks (section 5; fail = stop, no model result)", ""]
        ln += ["| check | value | threshold | ok |", "|---|---|---|---|"]
        for g in r["gates"]["checks"]:
            ln.append(f"| {g['check']} | {g['value']:.4f} | {g['threshold']} | {g['ok']} |")
        ln.append("")
        if r.get("stopped"):
            ln += ["**STOPPED: a minimum-data check failed. No model result.**", ""]
            continue
        for kind in KINDS:
            ln += [f"### H1 props skill, {kind}", ""]
            ln += [
                "| stat | book | n | games | LL model | LL market | delta [CI] | Holm p | "
                "Brier delta | power-devig delta | verdict |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for stat in STATS:
                for book in BOOKS:
                    c = r["h1"].get(f"{kind}/{stat}/{book}")
                    if not c or c.get("n", 0) == 0:
                        continue
                    v = c["verdict"]
                    ln.append(
                        f"| {stat} | {book}{'' if book in TESTED_BOOKS else ' (untested)'} | "
                        f"{c['n']} | {c['n_games']} | {c['ll_model']:.4f} | {c['ll_market']:.4f} | "
                        f"{_ci_s(c['delta'])} | {_f(c.get('holm_p'), 3, False)} | "
                        f"{_f(c['brier_delta']['point'])} | "
                        f"{_f(c['delta_power_devig']['point'])} | "
                        f"{v['label']} {v['qualifier']} |"
                    )
            ln.append("")
            if r["h2"]:
                ln += [f"### H2 blend (w fit on {SELECT_SEASON}, frozen), {kind}", ""]
                ln += [
                    "| stat | book | w | n | LL blend | LL market | delta [CI] | Holm p | "
                    "verdict |",
                    "|---|---|---|---|---|---|---|---|---|",
                ]
                for stat in STATS:
                    for book in BOOKS:
                        c = r["h2"].get(f"{kind}/{stat}/{book}")
                        if not c or c.get("n", 0) == 0:
                            continue
                        v = c["verdict"]
                        ln.append(
                            f"| {stat} | {book}{'' if book in TESTED_BOOKS else ' (untested)'} | "
                            f"{c['w']:.2f} | {c['n']} | {c['ll_blend']:.4f} | "
                            f"{c['ll_market']:.4f} | "
                            f"{_ci_s(c['delta'])} | {_f(c.get('holm_p'), 3, False)} | "
                            f"{v['label']} {v['qualifier']} |"
                        )
                ln.append("")
        if r.get("h3", {}).get("n"):
            h = r["h3"]
            ln += [
                "### H3 games (rung0_injury_elo OOF vs de-vigged Pinnacle moneyline at T-60)",
                "",
                f"n={h['n']} (Pinnacle {h['n_pinnacle']}, consensus fallback "
                f"{h['n_consensus_fallback']}); LL model {h['ll_model']:.4f}, LL market "
                f"{h['ll_market']:.4f}; delta {_ci_s(h['delta'])}; "
                f"verdict {h['verdict']['label']} {h['verdict']['qualifier']}.",
                "",
            ]
        ln += ["### Money (descriptive; only cells that beat the market)", ""]
        if r["ev"]:
            for key, e in r["ev"].items():
                ln.append(f"- `{key}`: {json.dumps(e, default=_jd)}")
        else:
            ln.append("- none: no T-60 cell beat the market, so no EV is reported.")
        ln += ["", "### CLV agreement (T-60 -> T-5 move in the model's direction)", ""]
        ln += ["| stat | book | n | agree rate | (agree - 0.5) [CI] |", "|---|---|---|---|---|"]
        for stat in STATS:
            for book in BOOKS:
                c = r["clv"].get(f"{stat}/{book}")
                if c and c.get("n"):
                    ln.append(
                        f"| {stat} | {book} | {c['n']} | {c['agree_rate']:.3f} | "
                        f"{_ci_s(c['agree_minus_half'])} |"
                    )
        ln += ["", "### Reliability, ten equal-width bins (pred -> observed over-rate, n)", ""]
        for kind in ("t60",):
            for stat in STATS:
                for book in TESTED_BOOKS:
                    c = r["h1"].get(f"{kind}/{stat}/{book}")
                    if not c or not c.get("n"):
                        continue
                    for who in ("model", "market"):
                        bins = " ".join(
                            f"{b['pred']:.2f}->{b['obs']:.2f}({int(b['n'])})"
                            for b in c[f"rel_{who}"]
                        )
                        ln.append(f"- `{kind}/{stat}/{book}` {who}: {bins}")
        if r.get("outcomes"):
            ln += ["", "### Section 7 outcome per stat (Pinnacle)", ""]
            ln += ["| stat | T-5 pass | T-60 pass | outcome |", "|---|---|---|---|"]
            for stat, o in r["outcomes"].items():
                ln.append(
                    f"| {stat} | {o['pinnacle_t5_pass']} | {o['pinnacle_t60_pass']} | "
                    f"{o['outcome']} |"
                )
        mr = r["market_reasons"]
        ln += ["", "### Price-side reasons (quote groups = one player's quotes of one stat)", ""]
        ln += [
            "| kind | book | quote groups | two-sided | one_sided | ambiguous_duplicate | "
            "unresolved_name |",
            "|---|---|---|---|---|---|---|",
        ]
        for kind, books in mr["quote_groups"].items():
            for book, q in books.items():
                ln.append(
                    f"| {kind} | {book} | {q['quote_groups']} | {q['quoted_two_sided']} | "
                    f"{q['one_sided']} | {q['ambiguous_duplicate']} | {q['unresolved_name']} |"
                )
        ln.append(
            "unmatched_event (games with a real tip and no prices): "
            f"{mr['unmatched_event']['by_kind']} of "
            f"{mr['unmatched_event']['games_with_real_tip']}; distinct unresolved names: "
            f"{mr['unresolved_names']['distinct']} ({mr['unresolved_names']['rows']} rows)."
        )
        ln += ["", "### Pairing counts (reasons)", ""]
        ln += [
            "| cell | market rows | paired | dnp | no_model_row | push | odd_line | "
            "model_push_certain | model rows | no_quote |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for key, c in r["pairing_counts"].items():
            ln.append(
                f"| {key} | {c['market_rows']} | {c['paired']} | {c['dnp']} | "
                f"{c['no_model_row']} | "
                f"{c['push']} | {c['odd_line']} | {c['model_push_certain']} | {c['model_rows']} | "
                f"{c['no_quote']} |"
            )
        ln += ["", "### Slices (no decision weight; delta [CI] model - market)", ""]
        for key, rows in r["slices"].items():
            if not rows:
                continue
            ln.append(f"**{key}**")
            ln.append("")
            ln += ["| slice | n | delta [CI] |", "|---|---|---|"]
            for s in rows:
                ln.append(f"| {s['slice']} | {s['n']} | {_ci_s(s)} |")
            ln.append("")
    ln += ["## Choices the rule text leaves open (amend before the freeze if wrong)", ""]
    ln += [f"- **{k}**: {v}" for k, v in CHOICES.items()]
    ln += ["", "## Proposed ledger rows (unnumbered; drafts)", ""]
    for season in sorted(results):
        if season != REPORT_SEASON or results[season].get("stopped"):
            continue
        r = results[season]
        for kind in KINDS:
            for stat in STATS:
                for book in TESTED_BOOKS:
                    for h in ("h1", "h2"):
                        c = r[h].get(f"{kind}/{stat}/{book}")
                        if not c or not c.get("n"):
                            continue
                        ln.append(
                            f"| ODDS_HISTORY {h.upper()} {stat} {book} {kind} {season} | "
                            f"n={c['n']} games={c['n_games']} delta {_ci_s(c['delta'])} Holm p "
                            f"{_f(c.get('holm_p'), 3, False)} | {c['verdict']['label']} |"
                        )
    ln += [
        "",
        "## Draft results-section text",
        "",
        "Fill from the tables above only; do not add a claim the tables do not carry. State n, "
        "the clustered CI and the family corrected over for every sentence; say plainly when "
        "the market was better.",
        "",
    ]
    return "\n".join(ln)


# ---------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("stage", choices=("model-rows", "market-rows", "game-rows", "score"))
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--holdout-row", default=None)
    ap.add_argument("--frozen-sha", default=None)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--no-lock", action="store_true", help="model-rows: skip heavy.lock (tests)")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    try:
        if a.stage == "score":
            if not a.frozen_sha:
                raise RuleError("score requires --frozen-sha <8 hex>")
            full = require_frozen(a.frozen_sha)
            guard_season(a.season, a.holdout_row)
            res = score_season(a.season, out, a.db_path, a.n_boot)
            res["frozen_sha256"], res["git_sha"] = full, git_sha()
            if res.get("stopped"):
                write_json(out / f"results_{a.season}.json", res)
                print(f"STOPPED: a minimum-data check failed: {json.dumps(res['gates'])}")
                return 3
            res["h3"] = score_games(a.season, out, a.n_boot)
            write_json(out / f"results_{a.season}.json", res)
            allr = {
                s: json.loads((out / f"results_{s}.json").read_text())
                for s in SEASONS
                if (out / f"results_{s}.json").exists()
            }
            REPORT_MD.write_text(render_report({int(k): v for k, v in allr.items()}, full))
            print(f"wrote {out / f'results_{a.season}.json'} and {REPORT_MD}")
            return 0
        if a.stage == "model-rows":
            meta = model_rows(a.season, a.db_path, out, a.holdout_row, lock=not a.no_lock)
            print(json.dumps({k: meta[k] for k in ("season", "n_rows", "per_stat", "a0_ok")}))
            return 0 if meta["a0_ok"] else 1
        if a.stage == "market-rows":
            mm = market_rows(a.season, a.db_path, out, a.holdout_row)
            print(json.dumps({k: mm[k] for k in ("season", "n_market_rows", "unmatched_event")}))
            return 0
        gm = game_rows(a.season, a.db_path, out, a.holdout_row)
        print(json.dumps(gm))
        return 0
    except RuleError as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
