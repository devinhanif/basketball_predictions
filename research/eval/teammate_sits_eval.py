# ruff: noqa: E501
"""TEAMMATE_SITS scoring harness (rule: docs/prereg/TEAMMATE_SITS.md, FROZEN, body sha256 c23f7b36...).

Model class: gradient-boosted trees (production ``props_context_residual`` heads, walk-forward month blocks); the two
arms share every fitted head and every row and differ only in the three vacancy inputs of the sitter's teammates.

    # cwd = the branch worktree (it reads the frozen docs/prereg and configs/ relatively)
    # label-free: everything up to (not including) the outcome join; prints G-REGATE
    mkdir <main checkout>/data/ops/heavy.lock && (PYTHONPATH=<worktree> python -W ignore -m research.eval.teammate_sits_eval \\
        --data-root <main checkout> --dry-run; rmdir <main checkout>/data/ops/heavy.lock)
    # the single run (main session only, after the adversary diff)
    ... -m research.eval.teammate_sits_eval --data-root <main checkout> --run --sha <64 hex>

Order (both modes): verify_rule (FROZEN status, body hash c23f7b36) -> load (season <= 2024, refuses 2025) -> G-TIP
-> arms + G-EQ -> planted-future -> month-block heads (checkpointed per stat-season-block; test-row labels BLINDED
before prediction) -> G-DET -> G-DELTA -> G-REGATE (label-free G2 on the run's own grids). ``--dry-run`` stops here.
``--run`` refuses if G-REGATE changed any stat's G2 word (the frozen text makes a failing stat UNRESOLVED; the
coordinator's instruction is to stop and say so, so the main session decides), then joins outcomes and scores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.teammate_sits import gate as G
from research.teammate_sits import scenario as S

RULE = Path("docs/prereg/TEAMMATE_SITS.md")
FROZEN_SHA = "c23f7b3617a1e22d6cc450f9d7dc413aa28ef9652e4947c2ff21b35a25c9d39d"
MARKER = "=== RESULTS BELOW ==="
# constants mirrored from the frozen text (section 1)
FLOOR = -0.005
CLOSE_AT = -0.002
N_BOOT = 5000
SEED = 2026
SELECT, REPORT = 2023, 2024
SEASONS = (SELECT, REPORT)
BH_Q = 0.05
STATS = ("pts", "reb", "ast", "fg3m")
MIN_SLICE_N = 300
MAX_SEASON = 2024
#: G2 words fixed by the frozen text (section 9, exact rerun 5179d07c), 2024: (CRPS G2, slope G2)
FROZEN_G2 = {
    "pts": ("PASS", "PASS"),
    "reb": ("PASS", "PASS"),
    "ast": ("PASS", "PASS"),
    "fg3m": ("FLOOR_UNREACHABLE", "SLOPE_UNRESOLVED"),
}
CACHE = Path("data/ops/teammate_sits_run")
OUT = Path("reports/teammate_sits")
N_PLANT = 3  # event dates planted (first, middle, last)
CLUSTERS = ("game", "player_season", "sitter_season")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ rule


def body_sha(text: str) -> str:
    """sha256 of every line above ``MARKER`` (each newline-terminated), as ``awk '/^MARKER$/{exit} {print}' | shasum -a 256``."""
    out = []
    for line in text.split("\n"):
        if line == MARKER:
            break
        out.append(line + "\n")
    return hashlib.sha256("".join(out).encode()).hexdigest()


def verify_rule(path: Path = RULE, sha: str | None = None) -> str:
    """Refuse unless the rule text hashes to the frozen sha (and to ``sha`` when given) and its status is FROZEN."""
    text = path.read_text()
    status = next((ln for ln in text.split("\n") if ln.startswith("Status:")), "")
    if not status.startswith("Status: **FROZEN"):
        raise RuntimeError(f"rule status is not FROZEN: {status[:60]!r}")
    got = body_sha(text)
    if got != FROZEN_SHA:
        raise RuntimeError(f"rule body hash {got[:8]} != frozen {FROZEN_SHA[:8]}")
    if sha is not None and sha != FROZEN_SHA:
        raise RuntimeError(f"--sha {sha[:8]} is not the frozen rule {FROZEN_SHA[:8]}")
    return got


def code_pin(data_version: str = "") -> str:
    """12-hex hash of the harness, scenario and gate source plus the data_version (checkpoint key: changed code or a
    changed DB never reuses old heads)."""
    h = hashlib.sha256()
    for f in (Path(__file__), Path(S.__file__), Path(G.__file__)):
        h.update(f.read_bytes())
    h.update(data_version.encode())
    return h.hexdigest()[:12]


def data_version(root: Path) -> str:
    """Newest data_version from the data root's manifest summary ("unknown" if absent)."""
    f = root / "docs" / "data_manifests" / "latest.json"
    return (
        str(json.loads(f.read_text()).get("data_version", "unknown")) if f.exists() else "unknown"
    )


def git_head() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def require_season(max_season: int) -> None:
    if max_season > MAX_SEASON:
        raise RuntimeError("season 2025 is the frozen holdout; this harness never loads it")


# ------------------------------------------------------------------------------------ arms


def build_arms(
    w: dict[str, Any], feats: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(arm S, arm U, sitters) on the primary events. Each arm row carries ``sitter_id``. G-EQ is asserted here:
    S rows are the production feature rows unchanged, and S and U differ only in the vacancy columns."""
    _, primary = G.events(w)
    sitters = primary.select(
        "game_id",
        pl.col("player_id").alias("sitter_id"),
        "team_id",
        "k",
        "season",
        pl.col("min10").alias("sitter_min10"),
    )
    keys = sitters.select("game_id", "sitter_id", "team_id")
    base = feats.filter(pl.col("game_id").is_in(sitters["game_id"].unique().to_list()))
    vac_u = S.scenario_vacancy(
        w["played"],
        w["states"],
        w["flagged"],
        w["info"],
        keys.select("game_id", "sitter_id").rows(),
        remove=True,
    )
    arm_u = S.scenario_features(base, vac_u, keys)
    arm_s = (
        keys.join(base, on=["game_id", "team_id"])
        .filter(pl.col("player_id") != pl.col("sitter_id"))
        .select(arm_u.columns)
    )
    order = ["game_id", "sitter_id", "player_id"]
    arm_s, arm_u = arm_s.sort(order), arm_u.sort(order)
    # G-EQ
    if not arm_s.select(order).equals(arm_u.select(order)):
        raise RuntimeError("G-EQ: arms do not share rows")
    other = [c for c in arm_s.columns if c not in S.SCN_COLS]
    if not arm_s.select(other).equals(arm_u.select(other)):
        raise RuntimeError("G-EQ: arms differ outside the vacancy columns")
    chk = arm_s.drop("sitter_id").join(base, on=["game_id", "player_id"], how="anti")
    if chk.height:
        raise RuntimeError("G-EQ: S rows not found in the production feature frame")
    return arm_s, arm_u, sitters


def blind(frame: pl.DataFrame) -> pl.DataFrame:
    """Test rows with every outcome column replaced by 0 (prediction never reads them; this makes it provable)."""
    from nba.props.context_residual import PROP_STATS

    return frame.with_columns(
        [pl.lit(0).cast(frame.schema[s]).alias(s) for s in PROP_STATS if s in frame.columns]
    )


# ------------------------------------------------------------------------------------ heads


def walk_heads(
    feats: pl.DataFrame,
    arm_s: pl.DataFrame,
    arm_u: pl.DataFrame,
    sitters: pl.DataFrame,
    static: pl.DataFrame,
    cfg: Any,
    stat: str,
    seasons: tuple[int, ...] = SEASONS,
    cache: Path | None = CACHE,
) -> dict[str, dict[str, np.ndarray]]:
    """Per season: month-block heads (train = every played row before the block start, production stat_frame,
    production feature names), predicted on the BLINDED S and U rows of the block. Integer-support 199-point grids."""
    from nba.eval.context_residual_eval import month_blocks
    from nba.props.context_residual import (
        CRPS_TAUS,
        ContextResidualModel,
        predict_rows,
        stat_feature_names,
        stat_frame,
        to_integer_support,
    )

    names = stat_feature_names(stat)
    sf = stat_frame(feats, stat)
    pos = static.select(
        pl.col("player_id").alias("sitter_id"),
        pl.col("position").cast(pl.Utf8).str.slice(0, 1).alias("_sp"),
    ).unique("sitter_id")
    code = pl.col("_sp").replace_strict({"G": 0, "F": 1, "C": 2}, default=-1)
    meta_frame = (
        sitters.select("game_id", "sitter_id", "k", "sitter_min10")
        .join(pos, on="sitter_id", how="left")
        .with_columns(code.alias("sitter_pos"))
        .drop("_sp")
    )
    out: dict[str, dict[str, np.ndarray]] = {}
    for season in seasons:
        require_season(season)
        s_rows = blind(arm_s.filter(pl.col("season") == season))
        u_rows = blind(arm_u.filter(pl.col("season") == season))
        parts: dict[str, list[np.ndarray]] = {}
        for b, (start, end) in enumerate(month_blocks(s_rows)):
            path = None if cache is None else cache / f"{stat}_{season}_{b:02d}.npz"
            if path is not None and path.exists():
                z = dict(np.load(path, allow_pickle=True))
            else:
                train = sf.filter(pl.col("game_date") < start).sort(
                    ["game_date", "game_id", "player_id"]
                )
                model = ContextResidualModel(stat, cfg, names).fit(train)
                blk_s = s_rows.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
                blk_u = u_rows.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
                rs, ms, qs = predict_rows({stat: model}, blk_s, stat, CRPS_TAUS)
                ru, mu, qu = predict_rows({stat: model}, blk_u, stat, CRPS_TAUS)
                kk = ["game_id", "sitter_id", "player_id"]
                if not rs.select(kk).equals(ru.select(kk)):
                    raise RuntimeError("arms lost row alignment in a block")
                meta = rs.join(meta_frame, on=["game_id", "sitter_id"], how="left")
                z = {
                    "game_id": rs["game_id"].to_numpy().astype(str),
                    "player_id": rs["player_id"].to_numpy().astype(np.int64),
                    "sitter_id": rs["sitter_id"].to_numpy().astype(np.int64),
                    "game_date": rs["game_date"].cast(pl.Utf8).to_numpy().astype(str),
                    "k": meta["k"].to_numpy(),
                    "sitter_min10": meta["sitter_min10"].to_numpy(),
                    "sitter_pos": meta["sitter_pos"].fill_null(-1).to_numpy(),
                    "pos_code": rs["pos_code"].fill_null(-1).to_numpy(),
                    "starter10": rs["starter10"].to_numpy(),
                    "q_s": to_integer_support(qs),
                    "q_u": to_integer_support(qu),
                    "mean_s": ms,
                    "mean_u": mu,
                }
                if path is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    np.savez_compressed(path, **z)
                _log(f"{stat} {season} block {b} ({start}): {len(z['game_id'])} rows")
            for k, v in z.items():
                parts.setdefault(k, []).append(np.asarray(v))
        out[str(season)] = {k: np.concatenate(v) for k, v in parts.items()} if parts else {}
    return out


def g_det(feats: pl.DataFrame, arm_s: pl.DataFrame, cfg: Any, stat: str) -> bool:
    """Two fits of the first 2023 block give identical grids."""
    from nba.eval.context_residual_eval import month_blocks
    from nba.props.context_residual import (
        CRPS_TAUS,
        ContextResidualModel,
        predict_rows,
        stat_feature_names,
        stat_frame,
    )

    s_rows = blind(arm_s.filter(pl.col("season") == SELECT))
    start, end = month_blocks(s_rows)[0]
    blk = s_rows.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
    train = (
        stat_frame(feats, stat)
        .filter(pl.col("game_date") < start)
        .sort(["game_date", "game_id", "player_id"])
    )
    qs = []
    for _ in range(2):
        m = ContextResidualModel(stat, cfg, stat_feature_names(stat)).fit(train)
        qs.append(predict_rows({stat: m}, blk, stat, CRPS_TAUS)[2])
    return bool(np.array_equal(qs[0], qs[1]))


# ------------------------------------------------------------------------------------ guards


def g_tip(games: pl.DataFrame, event_game_ids: list[str]) -> dict[str, int]:
    from nba.features.game_tipoff import build_game_tipoff, tip_coverage

    cov = tip_coverage(
        games.filter(pl.col("game_id").is_in(event_game_ids)).select("game_id"),
        tips=build_game_tipoff(max_season=MAX_SEASON),
    )
    if cov["proxy_fallback"] != 0:
        raise RuntimeError(
            f"G-TIP: {cov['proxy_fallback']} event games would use the 19:00 proxy; run stops"
        )
    return cov


def planted_future(
    w: dict[str, Any], sitters: pl.DataFrame, n: int = N_PLANT, season: int = REPORT
) -> dict[str, Any]:
    """Classic planted future on ``n`` event dates of ``season`` (first, evenly spaced, last): every box-score row
    dated on or after the date gets pts 99, minutes 48 and a flipped starter flag. For every event ON that date, both
    arms' vacancy (S = production ``vacated_features``, U = remove mode) must be unchanged."""
    from nba.props.context_residual import player_states, vacated_features

    ev = sitters.filter(pl.col("season") == season).join(
        w["games"].select("game_id", "game_date"), on="game_id"
    )
    days = sorted(set(ev["game_date"].to_list()))
    if not days:
        raise RuntimeError("planted future: no events in the season")
    pick = sorted({days[int(i)] for i in np.linspace(0, len(days) - 1, min(n, len(days)))})
    pg = w["pgs"].join(w["games"].select("game_id", "game_date"), on="game_id")

    def arms(pgs: pl.DataFrame, pairs: list[tuple[str, int]]) -> tuple[pl.DataFrame, pl.DataFrame]:
        pf = S.played_frame(w["games"], pgs)
        st = player_states(pf)
        gids = sorted({g for g, _ in pairs})
        u = S.scenario_vacancy(pf, st, w["flagged"], w["info"], pairs, remove=True)
        s_ = vacated_features(pf, st, w["flagged"], w["info"]).filter(pl.col("game_id").is_in(gids))
        return u.sort(["game_id", "sitter_id", "team_id"]), s_.sort(["game_id", "team_id"])

    all_pairs = ev.filter(pl.col("game_date").is_in(pick)).select("game_id", "sitter_id").rows()
    ref_u, ref_s = arms(w["pgs"], all_pairs)
    checked = 0
    for d in pick:
        hit = pl.col("game_date") >= d
        planted = pg.with_columns(
            pl.when(hit).then(pl.lit(99)).otherwise(pl.col("pts")).alias("pts"),
            pl.when(hit).then(pl.lit(48.0)).otherwise(pl.col("minutes")).alias("minutes"),
            pl.when(hit)
            .then(~pl.col("starter").fill_null(False))
            .otherwise(pl.col("starter"))
            .alias("starter"),
        ).drop("game_date")
        pairs = ev.filter(pl.col("game_date") == d).select("game_id", "sitter_id").rows()
        gids = [g for g, _ in pairs]
        u, s_ = arms(planted, pairs)
        if not (
            u.equals(ref_u.filter(pl.col("game_id").is_in(gids)))
            and s_.equals(ref_s.filter(pl.col("game_id").is_in(gids)))
        ):
            raise RuntimeError(f"planted future moved an arm on {d}: as-of logic broken")
        checked += len(pairs)
    return {"dates": [str(d) for d in pick], "events": checked, "pass": True}


def g_delta(heads: dict[str, dict[str, dict[str, np.ndarray]]]) -> dict[str, float]:
    """Share of 2024 rows with |delta_mean| > 0, per stat; information only (assigns no word)."""
    out = {}
    for stat, h in heads.items():
        r = h[str(REPORT)]
        out[stat] = float(np.mean(np.abs(r["mean_s"] - r["mean_u"]) > 0))
    return out


def regate(heads: dict[str, dict[str, dict[str, np.ndarray]]]) -> dict[str, Any]:
    """Section-9 G2 on the run's own month-block grids (label-free; same g2_stat as the frozen size.json)."""
    out: dict[str, Any] = {"by_season": {}, "changed": []}
    for stat, h in heads.items():
        for season, r in h.items():
            rows = pl.DataFrame(
                {"game_id": r["game_id"], "player_id": r["player_id"], "sitter_id": r["sitter_id"]}
            )
            g = G.g2_stat(
                r["q_s"],
                r["q_u"],
                r["mean_s"],
                r["mean_u"],
                rows,
                int(season),
                np.random.default_rng(G.SIZE_SEED),
            )
            out["by_season"].setdefault(season, {})[stat] = g
        now = out["by_season"][str(REPORT)][stat]
        if (now["crps_g2"], now["slope_g2"]) != FROZEN_G2[stat]:
            out["changed"].append(
                {"stat": stat, "frozen": FROZEN_G2[stat], "run": (now["crps_g2"], now["slope_g2"])}
            )
    return out


# ------------------------------------------------------------------------------------ scoring


def crps_int(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    from nba.eval.context_residual_eval import crps_from_quantiles

    return np.asarray(crps_from_quantiles(q, y.astype(float)))


def _weights(key: np.ndarray, rng: np.random.Generator, b: int) -> tuple[np.ndarray, np.ndarray]:
    _, inv = np.unique(np.asarray(key).astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    w = rng.multinomial(g, np.full(g, 1.0 / g), size=b).astype(float)
    return w, inv


def boot_mean(
    d: np.ndarray, key: np.ndarray, b: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Cluster bootstrap of the mean (clusters resampled with replacement; ratio of sums). 95% percentile CI and the
    one-sided p for mean < 0: (1 + #boot >= 0) / (B + 1)."""
    w, inv = _weights(key, np.random.default_rng(seed), b)
    sums = np.bincount(inv, weights=d)
    cnt = np.bincount(inv).astype(float)
    m = (w @ sums) / (w @ cnt)
    lo, hi = np.quantile(m, [0.025, 0.975])
    return {"lo": float(lo), "hi": float(hi), "p_lt0": float((1 + np.sum(m >= 0)) / (b + 1))}


def ols_slope(x: np.ndarray, y: np.ndarray) -> float:
    xc = x - x.mean()
    return float((xc * (y - y.mean())).sum() / max(float((xc**2).sum()), 1e-12))


def boot_slope(
    x: np.ndarray, y: np.ndarray, key: np.ndarray, b: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Cluster bootstrap of the OLS slope with intercept; p for b <= 0: (1 + #boot <= 0) / (B + 1)."""
    w, inv = _weights(key, np.random.default_rng(seed), b)
    sx, sy = np.bincount(inv, weights=x), np.bincount(inv, weights=y)
    sxx, sxy = np.bincount(inv, weights=x * x), np.bincount(inv, weights=x * y)
    n = np.bincount(inv).astype(float)
    N, X, Y, XX, XY = (w @ v for v in (n, sx, sy, sxx, sxy))
    den = XX - X * X / N
    bb = (XY - X * Y / N) / np.where(np.abs(den) > 1e-12, den, np.nan)
    if not np.isfinite(bb).any():  # delta has no spread: the slope is not identified
        return {"lo": float("nan"), "hi": float("nan"), "p_le0": 1.0}
    lo, hi = np.nanquantile(bb, [0.025, 0.975])
    return {"lo": float(lo), "hi": float(hi), "p_le0": float((1 + np.sum(~(bb > 0))) / (b + 1))}


def cluster_keys(r: dict[str, np.ndarray], season: int) -> dict[str, np.ndarray]:
    return {
        "game": r["game_id"].astype(str),
        "player_season": np.char.add(r["player_id"].astype(str), f"_{season}"),
        "sitter_season": np.char.add(r["sitter_id"].astype(str), f"_{season}"),
    }


def binding(cis: dict[str, dict[str, float]]) -> dict[str, float]:
    """Envelope over the three clusterings: [min lo, max hi] (at least as wide as the widest single CI; the
    project's envelope, market_prior_residual_rule.envelope). Rule line 4."""
    lo = np.array([c["lo"] for c in cis.values()], dtype=float)
    hi = np.array([c["hi"] for c in cis.values()], dtype=float)
    widest = max(cis, key=lambda k: cis[k]["hi"] - cis[k]["lo"])
    return {"lo": float(np.min(lo)), "hi": float(np.max(hi)), "cluster": widest}  # type: ignore[dict-item]


def widest(cis: dict[str, dict[str, float]]) -> dict[str, float]:
    """The widest single CI (rule line 6 says 'the widest of' for the slope family)."""
    name = max(
        cis,
        key=lambda k: (
            (cis[k]["hi"] - cis[k]["lo"]) if np.isfinite(cis[k]["hi"] - cis[k]["lo"]) else np.inf
        ),
    )
    return {**cis[name], "cluster": name}  # type: ignore[dict-item]


def top5_share(d: np.ndarray, key: np.ndarray) -> float:
    """Share of the summed S - U carried by the five groups contributing most in the direction of the total."""
    _, inv = np.unique(key.astype(str), return_inverse=True)
    g = np.bincount(inv, weights=d)
    tot = float(g.sum())
    if tot == 0:
        return float("nan")
    top = np.sort(g * np.sign(tot))[::-1][:5]
    return float(top.sum() * np.sign(tot) / tot)


def slice_masks(r: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    known = (r["pos_code"] != -1) & (r["sitter_pos"] != -1)
    return {
        "k=1": r["k"] == 1,
        "k=2-3": r["k"] >= 2,
        "sitter_min10>=30": r["sitter_min10"] >= 30,
        "sitter_min10<30": r["sitter_min10"] < 30,
        "teammate_starter10>=0.5": r["starter10"] >= 0.5,
        "teammate_starter10<0.5": r["starter10"] < 0.5,
        "same_pos_as_sitter": known & (r["pos_code"] == r["sitter_pos"]),
        "other_pos": known & (r["pos_code"] != r["sitter_pos"]),
    }


def score_season(r: dict[str, np.ndarray], y: np.ndarray, season: int) -> dict[str, Any]:
    d = crps_int(r["q_s"], y) - crps_int(r["q_u"], y)
    keys = cluster_keys(r, season)
    cis = {c: boot_mean(d, keys[c]) for c in CLUSTERS}
    bind = binding(cis)
    delta = r["mean_s"] - r["mean_u"]
    resid = y.astype(float) - r["mean_u"]
    scis = {c: boot_slope(delta, resid, keys[c]) for c in CLUSTERS}
    sbind = widest(scis)
    out: dict[str, Any] = {
        "n": len(d),
        "point": float(d.mean()),
        "ci": cis,
        "binding": bind,
        "p_worst": max(c["p_lt0"] for c in cis.values()),
        "slope": ols_slope(delta, resid),
        "slope_ci": scis,
        "slope_binding": sbind,
        "slope_envelope": binding(scis),
        "p_slope_worst": max(c["p_le0"] for c in scis.values()),
        "top5_sitter_season": top5_share(d, keys["sitter_season"]),
        "top5_teammate_season": top5_share(d, keys["player_season"]),
        "slices": {},
    }
    for name, m in slice_masks(r).items():
        n = int(m.sum())
        if n >= MIN_SLICE_N:
            ci = boot_mean(d[m], keys["game"][m])
            out["slices"][name] = {
                "n": n,
                "point": float(d[m].mean()),
                "lo": ci["lo"],
                "hi": ci["hi"],
            }
        else:
            out["slices"][name] = {"n": n, "shown": False}
    return out


def bh(p: dict[str, float]) -> dict[str, float]:
    from nba.eval.context_residual_eval import bh_adjust

    ks = list(p)
    adj = bh_adjust(np.array([p[k] for k in ks]))
    return {k: float(a) for k, a in zip(ks, adj, strict=True)}


def decide(
    scores: dict[str, dict[str, Any]], g2_words: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    """Rule lines 5 and 6, first match wins; BH m = 4 fixed over the four stats in both families."""
    rep = {s: scores[s][str(REPORT)] for s in STATS}
    sel = {s: scores[s][str(SELECT)] for s in STATS}
    adj = bh({s: rep[s]["p_worst"] for s in STATS})
    adj_b = bh({s: rep[s]["p_slope_worst"] for s in STATS})
    out: dict[str, Any] = {}
    for s in STATS:
        r, b = rep[s], rep[s]["binding"]
        crps_g2, slope_g2 = g2_words[s]
        if b["lo"] > 0:
            word = "HURTS"
        elif crps_g2 != "PASS":
            word = "UNRESOLVED"
        elif r["point"] <= FLOOR and b["hi"] < 0 and adj[s] <= BH_Q and sel[s]["point"] < 0:
            word = "HELPS"
        elif r["point"] > CLOSE_AT and b["lo"] > FLOOR:
            word = "CLOSED_TIE"
        else:
            word = "INCONCLUSIVE"
        sb = r["slope_binding"]
        if slope_g2 != "PASS":
            sword = "SLOPE_UNRESOLVED"
        elif adj_b[s] > BH_Q or not sb["lo"] > 0 or sel[s]["slope"] <= 0:
            sword = "NO_SIGNAL"
        elif sb["lo"] > 1:
            sword = "UNDER_REACTS"
        elif sb["hi"] < 1:
            sword = "OVER_REACTS"
        else:
            sword = "RIGHT_SIZED"
        k1 = r["slices"].get("k=1", {})
        out[s] = {
            "word": word,
            "slope_word": sword,
            "bh_p": adj[s],
            "bh_p_slope": adj_b[s],
            "display_ok": bool(
                word == "HELPS"
                and sword in ("RIGHT_SIZED", "UNDER_REACTS")
                and k1.get("point", 1.0) <= 0
            ),
            "top5_over_half": bool(
                r["top5_sitter_season"] > 0.5 or r["top5_teammate_season"] > 0.5
            ),
        }
    words = {s: out[s]["word"] for s in STATS}
    resolved = [s for s in STATS if words[s] != "UNRESOLVED"]
    out["kill"] = {
        "pts_display_closed": words["pts"] == "HURTS" or out["pts"]["slope_word"] == "OVER_REACTS",
        "scenario_closed_as_forecast": bool(resolved)
        and all(words[s] == "CLOSED_TIE" for s in resolved),
    }
    return out


# ------------------------------------------------------------------------------------ pipeline


def prepare(root: Path, stats: tuple[str, ...] = STATS) -> dict[str, Any]:
    """Everything before the outcome join (the --dry-run). No test-row outcome enters any array returned."""
    import yaml

    from nba.props.context_residual import build_features

    verify_rule()
    w = G.load(root, time.time())
    require_season(int(w["games"]["season"].max()))
    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    feats = build_features(w["games"], w["pgs"], w["static"], w["flagged"], elo)
    arm_s, arm_u, sitters = build_arms(w, feats)
    _log(f"arms: {arm_s.height} rows, {sitters.height} events; G-EQ pass")
    tip = g_tip(w["games"], sitters["game_id"].unique().to_list())
    _log(f"G-TIP {tip}")
    plant = planted_future(w, sitters)
    _log(f"planted future {plant}")
    dv = data_version(root)
    pin = code_pin(dv)
    cache = CACHE / pin  # checkpoints keyed to this exact code and data_version
    _log(f"checkpoints: {cache}")
    heads = {
        s: walk_heads(feats, arm_s, arm_u, sitters, w["static"], w["cfg"], s, cache=cache)
        for s in stats
    }
    for season in SEASONS:
        ref = None
        for s in stats:
            r = heads[s][str(season)]
            keys = pl.DataFrame(
                {"game_id": r["game_id"], "sitter_id": r["sitter_id"], "player_id": r["player_id"]}
            )
            if ref is None:
                ref = keys
            elif not keys.equals(ref):
                raise RuntimeError(
                    f"rule line 2: scored rows differ across stats in {season} ({s})"
                )
            j = keys.join(
                feats.select("game_id", "player_id", s), on=["game_id", "player_id"], how="left"
            )
            if j.height != keys.height or j[s].null_count():
                raise RuntimeError(
                    f"outcome join would fail for {s} {season}; stop before any outcome is read"
                )
    det = g_det(feats, arm_s, w["cfg"], stats[0])
    if not det:
        raise RuntimeError("G-DET: two fits of the first block differ")
    rg = regate(heads)
    return {
        "feats_keys": feats.select("game_id", "player_id", *STATS),
        "heads": heads,
        "guards": {"G-TIP": tip, "planted_future": plant, "G-DET": det, "G-DELTA": g_delta(heads)},
        "regate": rg,
        "provenance": {
            "rule_sha": FROZEN_SHA,
            "code_pin": pin,
            "git_head": git_head(),
            "data_version": dv,
            "n_boot": N_BOOT,
            "seed": SEED,
        },
    }


def outcomes(labels: pl.DataFrame, r: dict[str, np.ndarray], stat: str) -> np.ndarray:
    """THE outcome join: realised stat for each scored row (game_id, player_id)."""
    k = pl.DataFrame({"game_id": r["game_id"], "player_id": r["player_id"]}).with_row_index("_i")
    j = k.join(
        labels.select("game_id", "player_id", stat), on=["game_id", "player_id"], how="left"
    ).sort("_i")
    if j[stat].null_count():
        raise RuntimeError("outcome join lost rows")
    return j[stat].to_numpy().astype(float)


def score(prep: dict[str, Any]) -> dict[str, Any]:
    if prep["regate"]["changed"]:
        raise RuntimeError(
            f"G-REGATE changed a G2 word: {prep['regate']['changed']}; stopping before the outcome join"
        )
    scores: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        for season in SEASONS:
            r = prep["heads"][stat][str(season)]
            y = outcomes(prep["feats_keys"], r, stat)
            scores.setdefault(stat, {})[str(season)] = score_season(r, y, season)
    return {"scores": scores, "decision": decide(scores, FROZEN_G2)}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - maintainer
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", required=True, type=Path)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--sha", default=None)
    a = ap.parse_args(argv)
    if a.run:
        verify_rule(sha=a.sha or "")
    prep = prepare(a.data_root)
    rg = {
        s: {
            k: v
            for k, v in g.items()
            if k
            in ("crps_g2", "slope_g2", "oracle_expected_gain", "power_shared", "slope_se_shared")
        }
        for s, g in prep["regate"]["by_season"][str(REPORT)].items()
    }
    _log(f"G-REGATE 2024: {json.dumps(rg)}")
    _log(
        f"G-REGATE changed: {prep['regate']['changed'] or 'none'}; G-DELTA {prep['guards']['G-DELTA']}"
    )
    OUT.mkdir(parents=True, exist_ok=True)
    g2 = json.dumps(prep["regate"], indent=2, sort_keys=True, default=str)
    (OUT / "g2_run.json").write_text(g2)
    g2_sha = hashlib.sha256(g2.encode()).hexdigest()
    prep["provenance"]["g2_run_sha256"] = g2_sha
    _log(f"g2_run.json sha256 {g2_sha}; provenance {json.dumps(prep['provenance'])}")
    (OUT / "guards.json").write_text(
        json.dumps(prep["guards"], indent=2, sort_keys=True, default=str)
    )
    if a.dry_run:
        _log("dry run: stopped before the outcome join")
        return 0
    res = score(prep)
    res["provenance"] = prep["provenance"]
    (OUT / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=str))
    _log(json.dumps({s: res["decision"][s] for s in STATS}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
