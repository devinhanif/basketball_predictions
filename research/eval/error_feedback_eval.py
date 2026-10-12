# ruff: noqa: E501
"""ERROR_FEEDBACK harness (rule: docs/prereg/ERROR_FEEDBACK.md; a freeze candidate until the main session commits
``prereg(ERROR_FEEDBACK): <sha256>``).

Claim arm EF = production ``context_residual`` heads (production feature list, production hyper-parameters, seed 0)
plus five as-of columns built from the player's own recent errors against a REPORT-FREE walk-forward base
(``research.features.error_feedback``). Comparator A0 = the same heads without the columns, same rows, same window
(every row since 2019-20 dated before the block trains), same readout (production quantiles mapped to integer support,
integer CRPS). Select = 2022-2024 pooled (2021 fails the first-block coverage gate); report = 2025 once.

    M="-m research.eval.error_feedback_eval"
    .venv/bin/python -W ignore $M base                 # report-free base 2020-2024, 4 stats (heavy.lock; ~5 min; resumable per block)
    .venv/bin/python -W ignore $M null --k 50          # permutation null SH_k - A0, pts, 2022-2024 (heavy.lock; ~50 s per draw; resumable)
    .venv/bin/python -W ignore $M gate                 # minimum-data gate on the built base + null (heavy.lock; ~1 min)
    .venv/bin/python -W ignore $M rehearse             # the 2025 path with 2024 standing in, BLIND scoring (heavy.lock; ~10 min)
    .venv/bin/python -W ignore $M select --sha <64 hex>    # AFTER the freeze: the select-season evaluation, once
    .venv/bin/python -W ignore $M run --sha <64 hex> [--resume]   # THE one 2025 touch: frozen rule + committed holdout row + select pass

Every command except ``run`` loads ``season <= 2024`` only (SQL bound and asserts). ``run`` is the only code path that
raises the cap, and only after the refusals in ``research.eval.error_feedback_rule`` pass. Both databases are opened
read-only. Nothing here commits, writes a ledger, or touches nba/daily or nba/truth.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import bh_adjust, boot_p_value, crps_from_quantiles
from nba.eval.lower_tail_eval import attach_realised, randomized_pit
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    stat_feature_names,
    stat_frame,
)
from nba.props.metrics import paired_score_delta_ci
from research.eval import error_feedback_rule as RULE
from research.features import error_feedback as EF

ROOT = RULE.ROOT
ID = RULE.ID
DB = ROOT / "nba.duckdb"
HIST_DB = ROOT / "data/history/nba_history.duckdb"
HIST_SCHEDULE = ROOT / "data/history/schedule"
CONFIG = ROOT / "configs/context_residual.yaml"
ELO_CONFIG = ROOT / "configs/mov_elo_tuned.yaml"
OUT_DIR = ROOT / "reports/prereg_error_feedback"
CACHE = ROOT / "data/ops/error_feedback_cache"
LT_DIR = ROOT / "reports/lower_tail"
STATS: tuple[str, ...] = tuple(PROP_STATS)
SECONDARY: tuple[str, ...] = ("reb", "ast", "fg3m")
STAT_PRIMARY = "pts"
HIST_SEASONS: tuple[int, ...] = (2019, 2020, 2021)
CUR_START = 2022
BASE_SEASONS: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024)
# 2021 dropped from select: its first-block training coverage is 0.498 < 0.50 (outcome-free gate, 2026-10-12)
SELECT_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
HOLDOUT_SEASON = 2025
REPO_CAP = 2024
N_BOOT = 2000
BOOT_SEED = 0
FLOOR = -0.005
PLACEBO_MARGIN = -0.003
PL_SHIFT = 10
NULL_K = 50
NULL_SD_MULT = 2.0
NULL_SEED0 = 1000  # draw k permutes with seed NULL_SEED0 + k
COV_TAUS: tuple[float, ...] = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01
SLICE_TOL = 0.01
MIN_SLICE_N = 300
MIN_FEATURE_ROWS = 3  # ef_n >= 3 counts as "carries the feature"
COVERAGE_MIN = 0.50
MISSING_GAP_MAX = 0.02
MINUTE_BUCKETS: tuple[tuple[float, float], ...] = ((0, 5), (5, 10), (10, 20), (20, 99))
EXACT_TOL = 1e-9
PROD_REPRO_TOL = 1e-6
BLIND_SEED = 20261012
#: Outcome-free expected pts effects, persistence slope x ef_res_l5 shift of the A0 grid (the committed 2023-24 gate,
#: reports/prereg_error_feedback/gate.json, measured 2026-10-12 14:08 CDT); the slopes were a disclosed look.
EXPECTED_EFFECT = {"slope_2023_0.149": -0.0096, "slope_2024_0.102": -0.0045}
PROD_PGS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "starter",
)
ARMS_PRIMARY: tuple[str, ...] = ("A0", "EF", "PL")
ARMS_SECONDARY: tuple[str, ...] = ("A0", "EF")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S %Z')}] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float))


def heavy() -> Any:
    from research.eval.workload_eval import heavy as _heavy

    return _heavy()


# --------------------------------------------------------------------------- loading (season cap enforced)
class _Touch:
    """Created only by :func:`run_held_out` after the freeze and holdout-row refusals pass."""


class GateStop(RuntimeError):
    """A run-time gate failed (identity, rows, season cap). Inside the touch it consumes the touch; the remedy is a new id."""


@dataclass
class World:
    games: pl.DataFrame
    pgs: pl.DataFrame
    static: pl.DataFrame
    avail: pl.DataFrame
    tips: pl.DataFrame
    tips_cur: pl.DataFrame
    hist_ids: set[str]
    cur_ids: set[str]
    cfg: ContextResidualConfig
    elo: dict[str, float]
    cap: int


def assert_cap(df: pl.DataFrame, cap: int, what: str, col: str = "season") -> None:
    if df.height and int(df[col].max()) > cap:  # type: ignore[arg-type]
        raise GateStop(f"{what}: season > {cap} present; refusing")


def _side(path: Path, seasons: tuple[int, ...], cap: int) -> dict[str, pl.DataFrame]:
    from nba.db.connect import connect_with_retry

    ss = ",".join(str(int(s)) for s in seasons)
    con: duckdb.DuckDBPyConnection = connect_with_retry(path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
            f"WHERE season IN ({ss}) AND season <= {cap}"
        ).pl()
        pgs = con.execute(
            f"SELECT {', '.join('s.' + c for c in PROD_PGS)} FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season IN ({ss}) AND g.season <= {cap}"
        ).pl()
        static = con.execute(
            "SELECT player_id, position, birth_date, draft_year FROM players_static"
        ).pl()
        avail = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.as_of, a.source FROM player_availability a "
            f"JOIN games g USING (game_id) WHERE g.season IN ({ss}) AND g.season <= {cap}"
        ).pl()
    finally:
        con.close()
    assert_cap(games, cap, f"{path.name}:games")
    return {"games": games, "pgs": pgs, "static": static, "avail": avail}


def load_world(
    cap: int = REPO_CAP,
    touch: _Touch | None = None,
    db: Path = DB,
    history_db: Path = HIST_DB,
) -> World:
    """History (2019-21) and nba.duckdb (2022..cap), read-only. ``cap > 2024`` needs the touch token."""
    if cap > REPO_CAP and not isinstance(touch, _Touch):
        raise RuntimeError("season > 2024 may be loaded only inside the logged touch (run --sha)")
    if cap > HOLDOUT_SEASON:
        raise RuntimeError("season > 2025 (the live season) is never loaded here")
    from nba.features.game_tipoff import build_game_tipoff

    hist = _side(history_db, HIST_SEASONS, cap)
    cur = _side(db, tuple(range(CUR_START, cap + 1)), cap)
    overlap = set(hist["games"]["game_id"].to_list()) & set(cur["games"]["game_id"].to_list())
    if overlap:
        raise RuntimeError(f"{len(overlap)} game_id(s) in both databases")

    def cat(k: str) -> pl.DataFrame:
        return pl.concat([cur[k], hist[k]], how="vertical_relaxed")

    tips_cur = build_game_tipoff()
    tips = pl.concat([tips_cur, build_game_tipoff(HIST_SCHEDULE)], how="vertical_relaxed").unique(
        "game_id", keep="first", maintain_order=True
    )
    cfg = ContextResidualConfig(**yaml.safe_load(CONFIG.read_text()).get("model", {}))
    elo = {k: float(v) for k, v in yaml.safe_load(ELO_CONFIG.read_text()).items()}
    games = cat("games")
    assert_cap(games, cap, "world games")
    return World(
        games=games,
        pgs=cat("pgs"),
        static=cat("static").unique("player_id", keep="first", maintain_order=True),
        avail=cat("avail"),
        tips=tips,
        tips_cur=tips_cur,
        hist_ids=set(hist["games"]["game_id"].to_list()),
        cur_ids=set(cur["games"]["game_id"].to_list()),
        cfg=cfg,
        elo=elo,
        cap=cap,
    )


def build_frame(w: World, current_only: bool = False) -> pl.DataFrame:
    """Feature frame (one row per played player-game) with realised minutes and the actual starter flag attached as
    labels / slice strata only. ``current_only`` = production's window (2022 onward), used by the descriptive A0P arm
    and the harness reproduction check."""
    from research.eval import history_window_eval as hw

    if current_only:
        games = w.games.filter(pl.col("game_id").is_in(sorted(w.cur_ids)))
        pgs = w.pgs.filter(pl.col("game_id").is_in(sorted(w.cur_ids)))
        avail = w.avail.filter(pl.col("game_id").is_in(sorted(w.cur_ids)))
        flagged, _, _ = hw.flagged_real(avail, games, w.cfg, w.tips_cur, set())
    else:
        games, pgs, avail = w.games, w.pgs, w.avail
        flagged, _, _ = hw.flagged_real(avail, games, w.cfg, w.tips, w.hist_ids)
    fx = build_features(games, pgs.select(list(PROD_PGS)), w.static, flagged, w.elo)
    fx = attach_realised(fx, games, pgs)
    assert_cap(fx, w.cap, "feature frame")
    return fx


def stat_frames(fx: pl.DataFrame, stats: tuple[str, ...] = STATS) -> dict[str, pl.DataFrame]:
    return {s: stat_frame(fx, s).sort(["game_date", "game_id", "player_id"]) for s in stats}


def frame_sha(sf: pl.DataFrame, names: list[str]) -> str:
    """Fingerprint of a stat frame's keys, label and feature matrix (resume and identity checks)."""
    h = hashlib.sha256()
    h.update("|".join(names).encode())
    h.update("\n".join(sf["game_id"].cast(pl.Utf8).to_list()).encode())
    h.update(sf["player_id"].to_numpy().astype(np.int64).tobytes())
    h.update(sf["y"].to_numpy().astype(float).tobytes())
    h.update(
        np.ascontiguousarray(
            sf.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()
        ).tobytes()
    )
    return h.hexdigest()


# --------------------------------------------------------------------------- base (report-free walk-forward)
def base_path(stat: str, d: Path = CACHE) -> Path:
    return d / f"base_{stat}.npz"


class BlockCache:
    """Per-block checkpoints in ``root`` tied to an identity (refuses a directory written under a different one)."""

    def __init__(self, root: Path, identity: dict[str, Any]) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        idf = root / "identity.json"
        if idf.exists():
            prev = json.loads(idf.read_text())
            if prev != identity:
                raise RuntimeError(
                    f"{root} holds checkpoints of a different identity; move it aside by hand (never deleted here)"
                )
        else:
            write_json(idf, identity)

    def get(self, b: int, start: dt.date) -> dict[str, np.ndarray] | None:
        p = self.root / f"b{b:03d}_{start.isoformat()}.npz"
        if not p.exists():
            return None
        z = np.load(p, allow_pickle=False)
        return {k: z[k] for k in z.files}

    def put(self, b: int, start: dt.date, arrays: dict[str, np.ndarray]) -> None:
        p = self.root / f"b{b:03d}_{start.isoformat()}.npz"
        tmp = p.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **arrays)  # type: ignore[arg-type]
        tmp.replace(p)


def regen_base(
    sf: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    seasons: tuple[int, ...],
    cache_root: Path | None,
) -> dict[str, np.ndarray]:
    names = EF.base_feature_names(stat)
    cache = (
        BlockCache(
            cache_root, {"stat": stat, "seasons": list(seasons), "frame_sha": frame_sha(sf, names)}
        )
        if cache_root is not None
        else None
    )

    def on_block(b: int, start: dt.date, arr: dict[str, np.ndarray]) -> None:
        _log(
            f"base {stat} block {b} {start} rows {len(arr['row_y'])} train {int(arr['blk_train_rows'][0])}"
        )
        if cache is not None:
            cache.put(b, start, arr)

    return EF.walk_base(
        sf, stat, cfg, seasons, names, on_block=on_block, cached=cache.get if cache else None
    )


def run_base(stats: tuple[str, ...] = STATS) -> dict[str, Any]:
    """Build and store the report-free base for 2020-2024 (cap 2024; ``save_base`` refuses any later season)."""
    t0 = time.time()
    with heavy():
        w = load_world(REPO_CAP)
        fx = build_frame(w)
        sfs = stat_frames(fx, stats)
        meta: dict[str, Any] = {
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "seasons": list(BASE_SEASONS),
            "stats": {},
        }
        for stat in stats:
            base = regen_base(sfs[stat], stat, w.cfg, BASE_SEASONS, CACHE / "base_blocks" / stat)
            p = EF.save_base(base_path(stat), base, REPO_CAP)
            meta["stats"][stat] = {
                "path": str(p.relative_to(ROOT)),
                "sha256": RULE.file_sha256(p),
                "frame_sha": frame_sha(sfs[stat], EF.base_feature_names(stat)),
                **EF.describe(base),
            }
            _log(f"base {stat} stored ({time.time() - t0:.0f}s)")
    meta["wall_s"] = time.time() - t0
    write_json(OUT_DIR / "base_meta.json", meta)
    return meta


def load_stored_base(stat: str, check: bool = True) -> dict[str, np.ndarray]:
    p = base_path(stat)
    if check:
        meta = json.loads((OUT_DIR / "base_meta.json").read_text())
        want = meta["stats"][stat]["sha256"]
        got = RULE.file_sha256(p)
        if got != want:
            raise RuntimeError(f"{p} hashes to {got[:8]}, base_meta.json says {want[:8]}")
    return EF.load_base(p, REPO_CAP)


def base_subset(base: dict[str, np.ndarray], keep: np.ndarray) -> dict[str, np.ndarray]:
    return {k: v[keep] for k, v in base.items() if k.startswith("row_")}


# --------------------------------------------------------------------------- arms
def arm_names(stat: str) -> dict[str, list[str]]:
    a0 = stat_feature_names(stat)
    return {
        "A0": a0,
        "EF": [*a0, *EF.column_names("ef_")],
        "PL": [*a0, *EF.column_names("pl_")],
        "SH": [*a0, *EF.column_names("sh_")],
    }


def with_feedback(sf: pl.DataFrame, base: dict[str, np.ndarray]) -> pl.DataFrame:
    """``sf`` plus EF (``ef_``) and past-only placebo (``pl_``, rows 11-20 back) columns."""
    f = EF.attach(sf, base, shift=0, prefix="ef_")
    return EF.attach(f, base, shift=PL_SHIFT, prefix="pl_")


def with_shuffle(sf: pl.DataFrame, base: dict[str, np.ndarray], k: int) -> pl.DataFrame:
    res, pit = EF.shuffled(base, NULL_SEED0 + k)
    return EF.attach(sf, base, prefix="sh_", res=res, pit=pit)


def blind_perm(gid: np.ndarray, seed: int) -> np.ndarray:
    """Index permuting rows among the rows of one game (blind rehearsal scoring)."""
    rng = np.random.default_rng(seed)
    idx = np.arange(len(gid))
    out = idx.copy()
    _, inv = np.unique(gid, return_inverse=True)
    for g in range(int(inv.max()) + 1 if len(inv) else 0):
        m = np.flatnonzero(inv == g)
        out[m] = rng.permutation(m)
    return out


META_COLS: tuple[str, ...] = ("starter10", "n_prior", "minutes", "ef_n")


def walk_arm(
    sf: pl.DataFrame,
    stat: str,
    names: list[str],
    seasons: tuple[int, ...],
    cfg: ContextResidualConfig,
    blind_seed: int | None = None,
    train_filter: pl.Expr | None = None,
    label: str = "",
) -> dict[str, np.ndarray]:
    """Walk-forward one arm over the rows of ``seasons`` (month blocks; every earlier row trains unless ``train_filter``
    narrows it). Per row: centre, scale, integer CRPS and randomised integer PIT. ``blind_seed`` scores against labels
    permuted within each game (training labels untouched)."""
    from nba.eval.context_residual_eval import month_blocks

    test_all = sf.filter(pl.col("season").is_in(list(seasons)))
    out: dict[str, list[np.ndarray]] = {
        k: []
        for k in ("gid", "pid", "season", "day", "y", "c", "s", "crps", "pit", "blk", *META_COLS)
    }
    t0 = time.time()
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf.filter(
            (pl.col("game_date") >= start)
            & (pl.col("game_date") < end)
            & pl.col("season").is_in(list(seasons))
        )
        train = sf.filter(pl.col("game_date") < start)
        if train_filter is not None:
            train = train.filter(train_filter)
        if block.is_empty() or train.height < EF.MIN_TRAIN:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        assert model.z_sorted is not None
        c, s = model.predict_components(block)
        qi = EF.integer_grid(c, s, model.z_sorted)
        gid = block["game_id"].to_numpy().astype(str)
        y = block["y"].to_numpy().astype(float)
        ys = y[blind_perm(gid, blind_seed + b)] if blind_seed is not None else y
        out["gid"].append(gid)
        out["pid"].append(block["player_id"].to_numpy().astype(np.int64))
        out["season"].append(block["season"].to_numpy().astype(np.int64))
        out["day"].append(EF.date_days(block["game_date"]))
        out["y"].append(y)
        out["c"].append(c)
        out["s"].append(s)
        out["crps"].append(crps_from_quantiles(qi.astype(float), ys))
        out["pit"].append(randomized_pit(qi.astype(float), ys, seed=0))
        out["blk"].append(np.full(block.height, b, dtype=np.int64))
        for k in META_COLS:
            col = (
                block[k].cast(pl.Float64).to_numpy()
                if k in block.columns
                else np.full(block.height, np.nan)
            )
            out[k].append(col)
    _log(f"walk {label or stat} {len(out['y'])} blocks {time.time() - t0:.0f}s")
    return {k: np.concatenate(v) for k, v in out.items()}


def same_rows(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> bool:
    return bool(
        np.array_equal(a["gid"].astype(str), b["gid"].astype(str))
        and np.array_equal(a["pid"], b["pid"])
        and np.array_equal(a["y"], b["y"])
    )


def save_arm(path: Path, arr: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **arr)  # type: ignore[arg-type]
    tmp.replace(path)


def load_arm(path: Path) -> dict[str, np.ndarray]:
    z = np.load(path, allow_pickle=False)
    return {k: z[k] for k in z.files}


def cached_arm(path: Path, fn: Callable[[], dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    if path.exists():
        _log(f"resume: {path.name}")
        return load_arm(path)
    arr = fn()
    save_arm(path, arr)
    return arr


# --------------------------------------------------------------------------- statistics
def ci(a: np.ndarray, b: np.ndarray, cluster: np.ndarray) -> dict[str, float]:
    c = paired_score_delta_ci(a, b, n_boot=N_BOOT, seed=BOOT_SEED, cluster_ids=cluster)
    return {
        "point": float(c.point),
        "lo": float(c.lo),
        "hi": float(c.hi),
        "n": int(len(a)),
        "clusters": int(len(np.unique(cluster))),
    }


def cluster_sd(d: np.ndarray, key: np.ndarray) -> dict[str, float]:
    from research.eval.prereg_gates_common import cluster_stats

    return cluster_stats(d, key)


def coverage(pit: np.ndarray) -> dict[str, float]:
    return {f"{t:.2f}": float((pit < t).mean()) for t in COV_TAUS}


def slice_masks(sc: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    gid = sc["gid"].astype(str)
    out = {
        "bench(starter10<0.5)": sc["starter10"] < 0.5,
        "starter(starter10>=0.5)": sc["starter10"] >= 0.5,
        "cold_start(n_prior<10)": sc["n_prior"] < 10,
        "min<5(realised)": sc["minutes"] < 5,
        "playoffs": np.char.startswith(gid, "004"),
        "ef_n<3": sc["ef_n"] < MIN_FEATURE_ROWS,
    }
    for s in np.unique(sc["season"]):
        out[f"season_{int(s)}"] = sc["season"] == s
    return out


def _gap_by_minutes(missing: np.ndarray, minutes: np.ndarray) -> dict[str, Any]:
    by = {}
    for lo, hi in MINUTE_BUCKETS:
        m = (minutes >= lo) & (minutes < hi)
        by[f"{lo}-{hi}"] = {
            "n": int(m.sum()),
            "missing_share": float(missing[m].mean()) if m.any() else float("nan"),
        }
    vals = [v["missing_share"] for v in by.values() if v["n"] >= MIN_SLICE_N]
    return {"by_bucket": by, "max_gap": float(max(vals) - min(vals)) if vals else float("nan")}


def missingness(ef_n: np.ndarray, minutes: np.ndarray, n_prior: np.ndarray) -> dict[str, Any]:
    """Missingness audit (the opp_adjusted_ridge fingerprint is a NULL pattern that tracks tonight's minutes).

    ``ef_n < 3`` means fewer than three earlier base rows, i.e. (base rows need ``n_prior >= 5``) essentially
    ``n_prior < 8``: a cold-start indicator, and cold-start players play fewer minutes. So the audit asks whether the
    pattern is MORE than its as-of cause: (a) the unconditional gap by realised-minutes bucket may exceed that of the pure
    as-of indicator ``n_prior < 8`` by at most 0.01; (b) among rows with ``n_prior >= 10`` (the column should exist),
    the gap by realised-minutes bucket (buckets with n >= 300) is at most 0.02. The unconditional gap is reported."""
    miss = ef_n < MIN_FEATURE_ROWS
    unc = _gap_by_minutes(miss, minutes)
    ref = _gap_by_minutes(n_prior < MIN_FEATURE_ROWS + 5, minutes)
    big = n_prior >= 10
    cond = _gap_by_minutes(miss[big], minutes[big])
    excess = float(unc["max_gap"] - ref["max_gap"])
    ok_a = bool(excess <= 0.01)
    ok_b = bool(not np.isfinite(cond["max_gap"]) or cond["max_gap"] <= MISSING_GAP_MAX)
    return {
        "unconditional": unc,
        "reference_n_prior_lt8": ref,
        "excess_over_reference": excess,
        "n_prior_ge10": cond,
        "max_gap": excess,
        "pass": ok_a and ok_b,
    }


def null_summary(draws: list[float]) -> dict[str, float]:
    v = np.asarray(draws, dtype=float)
    return {
        "k": int(len(v)),
        "mean": float(v.mean()) if len(v) else float("nan"),
        "sd": float(v.std(ddof=1)) if len(v) > 1 else float("nan"),
        "min": float(v.min()) if len(v) else float("nan"),
        "max": float(v.max()) if len(v) else float("nan"),
        "line": float(v.mean() - NULL_SD_MULT * v.std(ddof=1)) if len(v) > 1 else float("nan"),
    }


def primary_verdict(
    a0: dict[str, np.ndarray],
    ef: dict[str, np.ndarray],
    pl_: dict[str, np.ndarray],
    mask: np.ndarray,
    null: dict[str, float],
    miss: dict[str, Any],
) -> dict[str, Any]:
    """The frozen pts checks on the rows of ``mask`` (a stage: select pooled, or the held-out season)."""
    gid = a0["gid"].astype(str)[mask]
    day = a0["day"][mask]
    ca, ce, cp = a0["crps"][mask], ef["crps"][mask], pl_["crps"][mask]
    prim = ci(ce, ca, gid)
    out: dict[str, Any] = {
        "n_rows": int(mask.sum()),
        "n_games": int(len(np.unique(gid))),
        "EF_minus_A0": prim,
        "EF_minus_A0_date_clustered": ci(ce, ca, day),
        "EF_minus_PL": ci(ce, cp, gid),
        "null": null,
        "missingness": miss,
    }
    cov_a0, cov_ef = coverage(a0["pit"][mask]), coverage(ef["pit"][mask])
    cov_ok = {t: abs(cov_ef[t] - float(t)) <= abs(cov_a0[t] - float(t)) + COV_TOL for t in cov_a0}
    out["coverage"] = {"A0": cov_a0, "EF": cov_ef, "ok": cov_ok}
    sl: dict[str, Any] = {}
    sub = {k: v[mask] for k, v in a0.items()}
    for name, m in slice_masks(sub).items():
        n = int(m.sum())
        if n < MIN_SLICE_N:
            sl[name] = {"n": n, "skipped": f"n < {MIN_SLICE_N}"}
            continue
        d = float((ce[m] - ca[m]).mean())
        sl[name] = {"n": n, "EF_minus_A0": d, "ok": bool(d <= SLICE_TOL)}
    out["slices"] = sl
    checks = {
        "primary: EF-A0 <= -0.005 and game-clustered CI upper < 0": bool(
            prim["point"] <= FLOOR and prim["hi"] < 0
        ),
        "placebo: EF-PL <= -0.003": bool(out["EF_minus_PL"]["point"] <= PLACEBO_MARGIN),
        "null guard: EF-A0 <= null mean - 2 null sd": bool(
            np.isfinite(null["line"]) and prim["point"] <= null["line"]
        ),
        "coverage: no tau worse than A0 by > 0.01": all(cov_ok.values()),
        "slices: none worse than +0.01 (n >= 300)": all(v.get("ok", True) for v in sl.values()),
        "missingness: no pattern beyond its as-of cause": bool(miss["pass"]),
    }
    out["checks"] = checks
    out["pass"] = all(checks.values())
    return out


def secondary_family(
    per_stat: dict[str, tuple[dict[str, np.ndarray], dict[str, np.ndarray]]],
    mask_fn: Callable[[dict[str, np.ndarray]], np.ndarray],
) -> dict[str, Any]:
    """reb/ast/fg3m EF - A0: game-clustered CI and two-sided bootstrap p, BH over m = 3 (labels only)."""
    rows: dict[str, Any] = {}
    ps = []
    for st in SECONDARY:
        a0, ef = per_stat[st]
        m = mask_fn(a0)
        gid = a0["gid"].astype(str)[m]
        d = ef["crps"][m] - a0["crps"][m]
        rows[st] = {
            "EF_minus_A0": ci(ef["crps"][m], a0["crps"][m], gid),
            "p": boot_p_value(d, gid, N_BOOT, BOOT_SEED),
        }
        ps.append(rows[st]["p"])
    adj = bh_adjust(np.asarray(ps))
    for st, a in zip(SECONDARY, adj, strict=True):
        pt = rows[st]["EF_minus_A0"]["point"]
        rows[st]["p_bh"] = float(a)
        rows[st]["label"] = (
            "improves"
            if (a <= 0.05 and pt < 0)
            else ("worse" if (a <= 0.05 and pt > 0) else "no label")
        )
    return rows


# --------------------------------------------------------------------------- permutation null (select seasons, pre-freeze)
def null_dir() -> Path:
    return CACHE / "null"


def run_null(k: int = NULL_K) -> dict[str, Any]:
    """A0 and K shuffled-residual arms SH_k (pts, 2022-2024, real labels). Resumable per draw. No EF arm is fitted."""
    t0 = time.time()
    stat = STAT_PRIMARY
    with heavy():
        w = load_world(REPO_CAP)
        sf = stat_frames(build_frame(w), (stat,))[stat]
        base = load_stored_base(stat)
        fr = with_feedback(sf, base)
        names = arm_names(stat)
        ident = {
            "frame_sha": frame_sha(fr, names["EF"]),
            "base_sha": RULE.file_sha256(base_path(stat)),
        }
        d = null_dir()
        d.mkdir(parents=True, exist_ok=True)
        idf = d / "identity.json"
        if idf.exists() and json.loads(idf.read_text()) != ident:
            raise RuntimeError(
                f"{d} was written under a different frame/base; move it aside by hand"
            )
        write_json(idf, ident)
        a0 = cached_arm(
            d / "A0.npz", lambda: walk_arm(fr, stat, names["A0"], SELECT_SEASONS, w.cfg, label="A0")
        )
        for j in range(k):
            p = d / f"SH_{j:03d}.npz"
            if p.exists():
                continue
            frj = with_shuffle(fr, base, j)
            arr = walk_arm(frj, stat, names["SH"], SELECT_SEASONS, w.cfg, label=f"SH_{j}")
            if not same_rows(arr, a0):
                raise RuntimeError("null draw rows differ from A0")
            save_arm(p, {k2: arr[k2] for k2 in ("crps", "pit", "c", "s")})
            _log(f"null draw {j + 1}/{k} done ({time.time() - t0:.0f}s)")
    return summarize_null(k)


def summarize_null(k: int = NULL_K) -> dict[str, Any]:
    d = null_dir()
    a0 = load_arm(d / "A0.npz")
    files = sorted(d.glob("SH_*.npz"))[:k]
    seasons = a0["season"]
    groups = {
        "pooled": np.isin(seasons, SELECT_SEASONS),
        **{str(s): seasons == s for s in SELECT_SEASONS},
    }
    draws: dict[str, list[float]] = {g: [] for g in groups}
    for f in files:
        sh = load_arm(f)
        delta = sh["crps"] - a0["crps"]
        for g, m in groups.items():
            draws[g].append(float(delta[m].mean()))
    out = {
        "k": len(files),
        "seeds": [NULL_SEED0 + i for i in range(len(files))],
        "by_group": {
            g: {**null_summary(np.asarray(v).tolist()), "draws": v} for g, v in draws.items()
        },
        "files_sha256": {f.name: RULE.file_sha256(f) for f in [d / "A0.npz", *files]},
    }
    write_json(OUT_DIR / "null.json", out)
    return out


# --------------------------------------------------------------------------- gate (pre-freeze; outcome-free except the null's comparator deltas)
def first_block_start(sf: pl.DataFrame, season: int) -> dt.date:
    d = sf.filter(pl.col("season") == season)["game_date"].min()
    assert isinstance(d, dt.date)
    return dt.date(d.year, d.month, 1)


def training_coverage(fr: pl.DataFrame, seasons: tuple[int, ...]) -> list[dict[str, Any]]:
    """Share of training rows (every row dated before the block, since 2019) carrying ef_n >= 3, at each block."""
    from nba.eval.context_residual_eval import month_blocks

    out = []
    test_all = fr.filter(pl.col("season").is_in(list(seasons)))
    for b, (start, _end) in enumerate(month_blocks(test_all)):
        tr = fr.filter(pl.col("game_date") < start)
        n = tr.height
        k = int((tr["ef_n"].to_numpy() >= MIN_FEATURE_ROWS).sum())
        out.append(
            {
                "block": b,
                "start": start.isoformat(),
                "train_rows": n,
                "with_feature": k,
                "share": k / n if n else float("nan"),
            }
        )
    return out


def planted_future_real(
    fr: pl.DataFrame, base: dict[str, np.ndarray], cut: dt.date
) -> dict[str, Any]:
    """Rewrite every base residual dated on/after ``cut``; columns of rows dated <= cut must not move. Control: rewrite
    residuals before ``cut``; columns of later rows must move."""
    cutd = (cut - dt.date(1970, 1, 1)).days
    res = EF.residuals(base)
    fut = base["row_day"] >= cutd
    rows = EF.date_days(fr["game_date"])
    early = rows <= cutd
    late = (rows > cutd) & (rows <= cutd + 7)  # their 10-row windows reach back before the cut
    a = EF.error_feedback_columns(
        fr["player_id"].to_numpy(), rows, base["row_pid"], base["row_day"], res, base["row_pit"]
    )
    res_f = np.where(fut, res + 25.0, res)
    pit_f = np.where(fut, 1.0 - base["row_pit"], base["row_pit"])
    b = EF.error_feedback_columns(
        fr["player_id"].to_numpy(), rows, base["row_pid"], base["row_day"], res_f, pit_f
    )
    res_p = np.where(~fut, res + 25.0, res)
    c = EF.error_feedback_columns(
        fr["player_id"].to_numpy(), rows, base["row_pid"], base["row_day"], res_p, base["row_pit"]
    )
    inert = all(np.array_equal(a[k][early], b[k][early], equal_nan=True) for k in a)
    moved = float(np.nanmean(np.abs(c["ef_res_l5"][late] - a["ef_res_l5"][late]) > 0))
    return {
        "cut": cut.isoformat(),
        "rows_le_cut": int(early.sum()),
        "future_inert": bool(inert),
        "control_rows_moved_share": moved,
        "pass": bool(inert and moved > 0.5),
    }


def prod_repro(w: World) -> dict[str, Any]:
    """Harness reproduction: production window (2022+ features), production names, pts 2023-2024 walk-forward vs the
    stored production components (reports/lower_tail/components_pts.npz). Forecasts only, no score."""
    from nba.eval.lower_tail_eval import load_components

    f0 = build_frame(w, current_only=True)
    sf0 = stat_frames(f0, ("pts",))["pts"]
    arr = walk_arm(sf0, "pts", stat_feature_names("pts"), (2023, 2024), w.cfg, label="A0P repro")
    st = load_components("pts", LT_DIR)
    ka = np.char.add(np.char.add(arr["gid"].astype(str), ":"), arr["pid"].astype(str))
    ks = np.char.add(
        np.char.add(st["row_gid"].astype(str), ":"), st["row_pid"].astype(np.int64).astype(str)
    )
    if not np.array_equal(ka, ks):
        common, ia, ib = np.intersect1d(ka, ks, return_indices=True)
        return {
            "rows_equal": False,
            "n_new": len(ka),
            "n_stored": len(ks),
            "n_common": len(common),
            "max_abs_diff_common": float(
                max(
                    np.abs(arr["c"][ia] - st["row_c"][ib]).max(),
                    np.abs(arr["s"][ia] - st["row_s"][ib]).max(),
                )
            )
            if len(common)
            else float("nan"),
            "pass": False,
        }
    diff = float(max(np.abs(arr["c"] - st["row_c"]).max(), np.abs(arr["s"] - st["row_s"]).max()))
    return {
        "rows_equal": True,
        "n": len(ka),
        "max_abs_diff": diff,
        "pass": bool(diff <= PROD_REPRO_TOL),
    }


def power(effect: float, line: float, se: float) -> float:
    from scipy.stats import norm

    return float(norm.cdf((line - effect) / se))


def run_gate() -> dict[str, Any]:
    t0 = time.time()
    out: dict[str, Any] = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "select_seasons": list(SELECT_SEASONS),
        "report_standin": REPO_CAP,
    }
    with heavy():
        w = load_world(REPO_CAP)
        fx = build_frame(w)
        sfs = stat_frames(fx)
        bases = {s: load_stored_base(s) for s in STATS}
        out["base"] = json.loads((OUT_DIR / "base_meta.json").read_text())
        frs = {s: with_feedback(sfs[s], bases[s]) for s in STATS}
        fr = frs[STAT_PRIMARY]
        # 1. training coverage per select block
        cov = training_coverage(fr, SELECT_SEASONS)
        out["training_coverage"] = cov
        out["training_coverage_first_block"] = cov[0]
        cov21 = training_coverage(fr, (2021,))
        out["training_coverage_2021_first_block(dropped season)"] = cov21[0]
        # 2. evaluation-row coverage and missingness by realised minutes, per season and stat
        ev: dict[str, Any] = {}
        for s in STATS:
            f = frs[s]
            ev[s] = {}
            for season in SELECT_SEASONS:
                m = f.filter(pl.col("season") == season)
                ev[s][str(season)] = {
                    "rows": m.height,
                    "share_ef_n_ge3": float((m["ef_n"].to_numpy() >= MIN_FEATURE_ROWS).mean()),
                    "missingness": missingness(
                        m["ef_n"].to_numpy(),
                        m["minutes"].cast(pl.Float64).to_numpy(),
                        m["n_prior"].to_numpy(),
                    ),
                }
        out["evaluation_rows"] = ev
        # 3. planted future on the real frame
        out["planted_future_real"] = planted_future_real(
            fr, bases[STAT_PRIMARY], dt.date(2023, 1, 15)
        )
        # 4. harness reproduction of production forecasts (pts 2023-24, production window)
        out["prod_repro"] = prod_repro(w)
    # 5. sizing from the permutation null (SH_0 - A0 per row; null spread across seeds)
    nul = json.loads((OUT_DIR / "null.json").read_text())
    a0 = load_arm(null_dir() / "A0.npz")
    sh0 = load_arm(sorted(null_dir().glob("SH_*.npz"))[0])
    delta = sh0["crps"] - a0["crps"]
    siz: dict[str, Any] = {}
    for g, msk in (
        ("2024 (report stand-in)", a0["season"] == REPO_CAP),
        ("select pooled 2022-24", np.isin(a0["season"], SELECT_SEASONS)),
    ):
        cg = cluster_sd(delta[msk], a0["gid"].astype(str)[msk])
        cd = cluster_sd(delta[msk], a0["day"][msk])
        deff = max(1.0, cg["deff"], cd["deff"])
        n = float(msk.sum())
        se = cg["sd_row"] * np.sqrt(deff / n)
        hw = 1.96 * se
        key = "2024" if g.startswith("2024") else "pooled"
        nsd = float(nul["by_group"][key]["sd"])
        line_null = float(nul["by_group"][key]["line"])
        se_tot = float(np.sqrt(se**2 + nsd**2))
        line = min(FLOOR, -hw, line_null)
        siz[g] = {
            "n_rows": int(n),
            "n_games": int(cg["clusters"]),
            "sd_row": cg["sd_row"],
            "deff_game": cg["deff"],
            "deff_date": cd["deff"],
            "deff_used": deff,
            "se_bootstrap": se,
            "halfwidth95": hw,
            "null_sd_fit_noise": nsd,
            "null_line": line_null,
            "se_total": se_tot,
            "pass_line": line,
            "power": {k: power(v, line, se_tot) for k, v in EXPECTED_EFFECT.items()},
        }
    # conservative sizing: the per-row sd / deff of a heads refit that really moves forecasts (stored FULL - A0, 2024,
    # the committed 13:57 gate); SH - A0 barely moves them, so its sd is a lower bound
    old = json.loads((OUT_DIR / "gate.json").read_text())["sizing"]["2024"]["primary"]
    n24 = float(siz["2024 (report stand-in)"]["n_rows"])
    se_c = float(old["sd_row"]) * float(np.sqrt(float(old["deff"]) / n24))
    hw_c = 1.96 * se_c
    nsd24 = float(nul["by_group"]["2024"]["sd"])
    line_c = min(FLOOR, -hw_c, float(nul["by_group"]["2024"]["line"]))
    se_tot_c = float(np.sqrt(se_c**2 + nsd24**2))
    siz["2024 conservative (sd/deff of stored FULL-A0)"] = {
        "n_rows": int(n24), "sd_row": float(old["sd_row"]), "deff_used": float(old["deff"]), "se_bootstrap": se_c,
        "halfwidth95": hw_c, "null_sd_fit_noise": nsd24, "se_total": se_tot_c, "pass_line": line_c,
        "power": {k: power(v, line_c, se_tot_c) for k, v in EXPECTED_EFFECT.items()},
    }  # fmt: skip
    out["sizing"] = siz
    out["null"] = {
        g: {k: v for k, v in d.items() if k != "draws"} for g, d in nul["by_group"].items()
    }
    first = out["training_coverage_first_block"]["share"]
    rep = siz["2024 conservative (sd/deff of stored FULL-A0)"]
    gate = [
        {
            "check": "first select block (2022-10): training rows with ef_n >= 3 >= 0.50",
            "value": first,
            "pass": bool(first >= COVERAGE_MIN),
        },
        {
            "check": "report half-width (2024 stand-in, conservative sd/deff) <= 0.005",
            "value": rep["halfwidth95"],
            "pass": bool(rep["halfwidth95"] <= abs(FLOOR)),
        },
        {
            "check": "missingness: excess over the as-of n_prior<8 pattern <= 0.01 and gap <= 0.02 among n_prior>=10 (every stat, select season)",
            "value": max(
                ev[s][str(se_)]["missingness"]["max_gap"] for s in STATS for se_ in SELECT_SEASONS
            ),
            "pass": all(
                ev[s][str(se_)]["missingness"]["pass"] for s in STATS for se_ in SELECT_SEASONS
            ),
        },
        {
            "check": "planted future inert, past control moves (real frame)",
            "value": out["planted_future_real"]["control_rows_moved_share"],
            "pass": out["planted_future_real"]["pass"],
        },
        {
            "check": "permutation null K >= 50 on the select seasons",
            "value": nul["k"],
            "pass": bool(nul["k"] >= NULL_K),
        },
        {
            "check": "harness reproduces stored production pts forecasts 2023-24 (<= 1e-6)",
            "value": out["prod_repro"].get(
                "max_abs_diff", out["prod_repro"].get("max_abs_diff_common")
            ),
            "pass": out["prod_repro"]["pass"],
        },
    ]
    out["gate"] = gate
    out["gate_pass"] = all(row["pass"] for row in gate)
    out["wall_s"] = time.time() - t0
    write_json(OUT_DIR / "gate_history_base.json", out)
    for row in gate:
        print(("PASS " if row["pass"] else "FAIL ") + f"{row['check']}: {row['value']}", flush=True)
    return out


# --------------------------------------------------------------------------- the evaluation body (select stage, rehearsal, touch)
def evaluate(
    w: World,
    fx: pl.DataFrame,
    bases: dict[str, dict[str, np.ndarray]],
    seasons: tuple[int, ...],
    cache: Path,
    blind_seed: int | None,
    null_k: int,
    null_from: Path | None = None,
) -> dict[str, Any]:
    """Arms on ``seasons``: A0 and EF for every stat, PL for pts; the pts permutation null (loaded from ``null_from`` for
    the select stage, else computed here with ``null_k`` draws); the descriptive production-window A0P (pts). Returns
    the verdict dictionary."""
    sfs = stat_frames(fx)
    out: dict[str, Any] = {"seasons": list(seasons), "blind": blind_seed is not None}
    per: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for stat in STATS:
        fr = with_feedback(sfs[stat], bases[stat])
        names = arm_names(stat)
        arms = ARMS_PRIMARY if stat == STAT_PRIMARY else ARMS_SECONDARY
        per[stat] = {}
        for arm in arms:
            per[stat][arm] = cached_arm(
                cache / f"{stat}_{arm}.npz",
                partial(
                    walk_arm,
                    fr,
                    stat,
                    names[arm],
                    seasons,
                    w.cfg,
                    blind_seed,
                    label=f"{stat} {arm}",
                ),
            )
            if not same_rows(per[stat][arm], per[stat]["A0"]):
                raise GateStop(f"{stat} {arm}: rows differ from A0")
        if stat == STAT_PRIMARY:
            out["missingness"] = missingness(
                per[stat]["A0"]["ef_n"], per[stat]["A0"]["minutes"], per[stat]["A0"]["n_prior"]
            )
            if null_from is not None:
                a0n = load_arm(null_from / "A0.npz")
                same = same_rows(a0n, per[stat]["A0"])
                diff = (
                    float(
                        max(
                            np.abs(a0n["c"] - per[stat]["A0"]["c"]).max(),
                            np.abs(a0n["s"] - per[stat]["A0"]["s"]).max(),
                        )
                    )
                    if same
                    else float("inf")
                )
                out["a0_reproduces_null_run"] = {
                    "rows_equal": same,
                    "max_abs_diff": diff,
                    "pass": bool(same and diff <= EXACT_TOL),
                }
                files = sorted(null_from.glob("SH_*.npz"))
                draws = [float((load_arm(f)["crps"] - a0n["crps"]).mean()) for f in files]
            else:
                draws = []
                for j in range(null_k):
                    frj = with_shuffle(fr, bases[stat], j)
                    sh = cached_arm(
                        cache / f"SH_{j:03d}.npz",
                        partial(
                            walk_arm,
                            frj,
                            stat,
                            names["SH"],
                            seasons,
                            w.cfg,
                            blind_seed,
                            label=f"SH_{j}",
                        ),
                    )
                    draws.append(float((sh["crps"] - per[stat]["A0"]["crps"]).mean()))
            out["null_draws"] = draws
    a0, ef, pl_ = (per[STAT_PRIMARY][k] for k in ARMS_PRIMARY)
    mask = np.isin(a0["season"], seasons)
    out["primary"] = primary_verdict(
        a0, ef, pl_, mask, null_summary(out["null_draws"]), out["missingness"]
    )
    if "a0_reproduces_null_run" in out:
        out["primary"]["checks"]["A0 reproduces the pre-freeze null run (<= 1e-9)"] = out[
            "a0_reproduces_null_run"
        ]["pass"]
        out["primary"]["pass"] = all(out["primary"]["checks"].values())
    out["secondary"] = secondary_family(
        {s: (per[s]["A0"], per[s]["EF"]) for s in SECONDARY},
        lambda a: np.isin(a["season"], seasons),
    )
    # descriptive: EF vs production's own window (2022 onward), pts, common rows
    f0 = build_frame(w, current_only=True)
    sf0 = stat_frames(f0, (STAT_PRIMARY,))[STAT_PRIMARY]
    a0p = cached_arm(
        cache / "pts_A0P.npz",
        lambda: walk_arm(
            sf0,
            STAT_PRIMARY,
            stat_feature_names(STAT_PRIMARY),
            tuple(s for s in seasons if s >= 2023),
            w.cfg,
            blind_seed,
            label="pts A0P",
        ),
    )
    ka = np.char.add(np.char.add(ef["gid"].astype(str), ":"), ef["pid"].astype(str))
    kp = np.char.add(np.char.add(a0p["gid"].astype(str), ":"), a0p["pid"].astype(str))
    _, ia, ib = np.intersect1d(ka, kp, return_indices=True)
    if len(ia):
        out["descriptive_EF_minus_A0P_common_rows"] = ci(
            ef["crps"][ia], a0p["crps"][ib], ef["gid"].astype(str)[ia]
        )
        out["descriptive_A0_minus_A0P_common_rows"] = ci(
            a0["crps"][ia], a0p["crps"][ib], a0["gid"].astype(str)[ia]
        )
    return out


def marker_info(sha: str) -> dict[str, Any]:
    diff = RULE.git("diff", "HEAD", "--", "research", "nba")
    return {
        "sha256": sha,
        "head": RULE.git("rev-parse", "HEAD").strip(),
        "code_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "base_sha256": {s: RULE.file_sha256(base_path(s)) for s in STATS},
        "null_json_sha256": RULE.file_sha256(OUT_DIR / "null.json"),
        "select_results_sha256": RULE.file_sha256(OUT_DIR / "select/results.json"),
    }


MARKER_KEYS = (
    "sha256",
    "head",
    "code_diff_sha256",
    "base_sha256",
    "null_json_sha256",
    "select_results_sha256",
)


def require_unspent(out_dir: Path, resume: bool, current: dict[str, Any] | None = None) -> None:
    """Refuse a spent touch, a failed run-time gate, a started touch without --resume, and a --resume whose sha, HEAD,
    code diff or input hashes differ from the started touch."""
    if (out_dir / "results.json").exists():
        raise RuntimeError(
            f"{out_dir / 'results.json'} exists: the touch is spent (one run, no reruns)"
        )
    if (out_dir / "gate_failed.json").exists():
        raise RuntimeError(
            f"{out_dir / 'gate_failed.json'} exists: a run-time gate failed; the remedy is a new id"
        )
    mk = out_dir / "touch_started.json"
    if not mk.exists():
        return
    if not resume:
        raise RuntimeError(
            f"{mk} exists: a started touch is consumed; resume only with --resume (same sha, code, inputs)"
        )
    if current is None:
        raise RuntimeError("--resume needs the identity of the current run")
    prev = json.loads(mk.read_text())
    bad = {k: (prev.get(k), current.get(k)) for k in MARKER_KEYS if prev.get(k) != current.get(k)}
    if bad:
        raise RuntimeError(
            f"--resume must repeat the started touch exactly; differs: {sorted(bad)}"
        )


def write_marker(out_dir: Path, marker: dict[str, Any], season: int) -> Path:
    """touch_started.json is written once and never overwritten; a resume appends touch_resumed_<n>.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {**marker, "season": season, "started": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    mk = out_dir / "touch_started.json"
    if not mk.exists():
        write_json(mk, payload)
        return mk
    p = out_dir / f"touch_resumed_{len(list(out_dir.glob('touch_resumed_*.json'))) + 1}.json"
    write_json(p, payload)
    return p


def touch_body(
    target: int,
    w: World,
    out_dir: Path,
    cache: Path,
    blind: bool,
    null_k: int,
) -> dict[str, Any]:
    """Shared by ``run`` (target 2025) and ``rehearse`` (target 2024): regenerate the target season's base inside the
    touch, attach the columns, fit A0/EF/PL/null/A0P on the target season, score once."""
    if w.cap != target:
        raise GateStop(f"world cap {w.cap} != target {target}")
    fx = build_frame(w)
    assert_cap(fx, target, "touch frame")
    if int(fx["season"].max()) != target:  # type: ignore[arg-type]
        raise GateStop(f"no season-{target} rows in the frame")
    sfs = stat_frames(fx)
    bases: dict[str, dict[str, np.ndarray]] = {}
    ident: dict[str, Any] = {}
    for stat in STATS:
        stored = load_stored_base(stat)
        earlier = base_subset(stored, stored["row_season"] < target)
        new = regen_base(sfs[stat], stat, w.cfg, (target,), cache / "base_blocks" / stat)
        if int(new["row_season"].max()) > target:
            raise GateStop("regenerated base beyond the target season")
        if target <= REPO_CAP:
            ref = base_subset(stored, stored["row_season"] == target)
            same = (
                len(ref["row_y"]) == len(new["row_y"])
                and np.array_equal(ref["row_gid"], new["row_gid"])
                and np.array_equal(ref["row_pid"], new["row_pid"])
            )
            diff = (
                float(
                    max(
                        np.abs(ref["row_c"] - new["row_c"]).max(),
                        np.abs(ref["row_s"] - new["row_s"]).max(),
                    )
                )
                if same
                else float("inf")
            )
            ident[stat] = {
                "rows_equal": same,
                "max_abs_diff": diff,
                "pass": bool(same and diff <= EXACT_TOL),
            }
        bases[stat] = EF.concat_base(
            earlier, {k: v for k, v in new.items() if k.startswith("row_")}
        )
        if int(bases[stat]["row_season"].max()) > target:
            raise GateStop("base rows beyond the target season")
    res = evaluate(w, fx, bases, (target,), cache, BLIND_SEED if blind else None, null_k)
    res["target"] = target
    if ident:
        res["regenerated_base_equals_stored"] = ident
    return res


def preflight(sha: str) -> dict[str, Any]:
    """Cheap refusals BEFORE the cap is raised: stored inputs present and hashing to what the gate and select stage
    recorded; the select stage passed under this sha."""
    meta = json.loads((OUT_DIR / "base_meta.json").read_text())
    for s in STATS:
        got = RULE.file_sha256(base_path(s))
        if got != meta["stats"][s]["sha256"]:
            raise RuntimeError(f"base_{s}.npz differs from base_meta.json")
    sel = OUT_DIR / "select/results.json"
    if not sel.exists():
        raise RuntimeError(
            "no select-stage results: run `select --sha` first (the touch follows a select pass only)"
        )
    r = json.loads(sel.read_text())
    if r.get("sha256") != sha:
        raise RuntimeError("select-stage results were produced under a different sha")
    if not r.get("primary", {}).get("pass"):
        raise RuntimeError("the select stage did not pass: the rule is closed; no 2025 touch")
    return marker_info(sha)


def require_gate() -> dict[str, Any]:
    """The pre-freeze gate passed and the null files still hash to what null.json recorded."""
    g: dict[str, Any] = json.loads((OUT_DIR / "gate_history_base.json").read_text())
    if not g.get("gate_pass"):
        raise RuntimeError("the minimum-data gate did not pass: no arm may be fitted")
    nul = json.loads((OUT_DIR / "null.json").read_text())
    if int(nul["k"]) < NULL_K:
        raise RuntimeError(f"the permutation null has K = {nul['k']} < {NULL_K}")
    for name, want in nul["files_sha256"].items():
        if RULE.file_sha256(null_dir() / name) != want:
            raise RuntimeError(
                f"{name} no longer hashes to null.json: the null was changed after the gate"
            )
    return g


def run_select(sha: str) -> dict[str, Any]:
    """AFTER the freeze: the select-season evaluation (2022-2024 pooled), once."""
    RULE.require_frozen(sha)
    require_gate()
    out_dir = OUT_DIR / "select"
    if (out_dir / "results.json").exists():
        raise RuntimeError("select results exist: one run, no reruns")
    t0 = time.time()
    with heavy():
        w = load_world(REPO_CAP)
        fx = build_frame(w)
        bases = {s: load_stored_base(s) for s in STATS}
        res = evaluate(
            w, fx, bases, SELECT_SEASONS, CACHE / f"select_{sha[:8]}", None, 0, null_from=null_dir()
        )
    res.update(
        {"sha256": sha, "head": RULE.git("rev-parse", "HEAD").strip(), "wall_s": time.time() - t0}
    )
    write_json(out_dir / "results.json", res)
    return res


def run_held_out(sha: str, resume: bool = False) -> dict[str, Any]:
    """THE one 2025 touch."""
    RULE.require_ready(sha)
    marker = preflight(sha)
    out_dir = OUT_DIR / "stage2"
    require_unspent(out_dir, resume, marker)
    t0 = time.time()
    with heavy():
        w = load_world(HOLDOUT_SEASON, touch=_Touch())
        write_marker(out_dir, marker, HOLDOUT_SEASON)
        try:
            res = touch_body(
                HOLDOUT_SEASON, w, out_dir, CACHE / f"stage2_{sha[:8]}", blind=False, null_k=NULL_K
            )
        except (
            GateStop
        ) as e:  # a run-time gate after the marker consumes the touch; other errors may --resume
            write_json(
                out_dir / "gate_failed.json",
                {"error": repr(e), "at": time.strftime("%Y-%m-%d %H:%M:%S %Z")},
            )
            raise
    res.update(
        {
            **marker,
            "wall_s": time.time() - t0,
            "verdict": "PROPOSAL (both stages pass)" if res["primary"]["pass"] else "NOT A PASS",
        }
    )
    write_json(out_dir / "results.json", res)
    return res


def rehearse(null_k: int = 3) -> dict[str, Any]:
    """The touch path with 2024 standing in for 2025: no sha, no log row, cap never raised, BLIND scoring (labels
    permuted within game) so no arm's skill on 2024 is seen before the freeze. Checks the regenerated 2024 base equals
    the stored one exactly."""
    out_dir = OUT_DIR / "rehearsal"
    t0 = time.time()
    with heavy():
        w = load_world(REPO_CAP)
        res = touch_body(REPO_CAP, w, out_dir, CACHE / "rehearsal", blind=True, null_k=null_k)
    # end-to-end equivalence: the touch path's A0 on 2024 must equal the pre-freeze null run's A0 on 2024 exactly
    nul = null_dir() / "A0.npz"
    if nul.exists():
        a0n = load_arm(nul)
        a0r = load_arm(CACHE / "rehearsal" / f"{STAT_PRIMARY}_A0.npz")
        m = a0n["season"] == REPO_CAP
        sub = {k: v[m] for k, v in a0n.items()}
        same = same_rows(sub, a0r)
        diff = (
            float(max(np.abs(sub["c"] - a0r["c"]).max(), np.abs(sub["s"] - a0r["s"]).max()))
            if same
            else float("inf")
        )
        res["a0_2024_equals_null_run"] = {
            "rows_equal": same,
            "max_abs_diff": diff,
            "pass": bool(same and diff <= EXACT_TOL),
        }
        print(
            ("PASS " if res["a0_2024_equals_null_run"]["pass"] else "FAIL ")
            + f"touch-path A0 2024 == null-run A0 2024: {diff}",
            flush=True,
        )
    res["wall_s"] = time.time() - t0
    res["note"] = (
        "BLIND: every score is against labels permuted within game; numbers carry no information about any arm"
    )
    write_json(out_dir / "results.json", res)
    for s, v in res.get("regenerated_base_equals_stored", {}).items():
        print(
            ("PASS " if v["pass"] else "FAIL ")
            + f"regenerated 2024 base == stored ({s}): {v['max_abs_diff']}",
            flush=True,
        )
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="error_feedback_eval")
    ap.add_argument("cmd", choices=["base", "null", "gate", "rehearse", "select", "run"])
    ap.add_argument("--sha")
    ap.add_argument("--k", type=int, default=NULL_K)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "base":
        run_base()
    elif a.cmd == "null":
        run_null(a.k)
    elif a.cmd == "gate":
        r = run_gate()
        return 0 if r["gate_pass"] else 3
    elif a.cmd == "rehearse":
        rehearse(a.k if a.k != NULL_K else 3)
    elif a.cmd == "select":
        run_select(a.sha)
    elif a.cmd == "run":
        if a.k != NULL_K:
            raise SystemExit(f"the held-out null is K = {NULL_K}; --k {a.k} refused")
        run_held_out(a.sha, a.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
