# ruff: noqa: E501
"""FOUR_FACTORS (docs/prereg/FOUR_FACTORS.md, frozen sha256 0d87c2ce, commit 918e03c).

    M="-m research.eval.four_factors_eval"
    .venv/bin/python $M gate      # minimum-data checks 1-6 on the real data (light); stop on failure
    .venv/bin/python $M smoke     # one month block, A0 vs A2 on pts; nothing written
    .venv/bin/python $M arms      # A0-A4 x {pts,reb,ast,fg3m}; needs a passing gate; heavy.lock
    .venv/bin/python $M report    # reports/prereg_four_factors.md + ledger_rows.md from the JSON
    .venv/bin/python $M run       # gate -> arms -> report under one heavy.lock

Research module only (``nba/`` untouched, so ``ContextResidualConfig.four_factors`` does not exist;
the nine columns are passed through explicit ``stat_feature_names(stat, extra=...)`` lists, as in
F8/F11). Read-only on ``nba.duckdb``; every SQL carries ``season <= 2024``; the frames are asserted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import EPS, attach_realised, randomized_pit
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from research.eval.f11_lineup_context import (
    _bucket_minutes,
    _ci,
    _jd,
    _write_json,
    frozen_sha256,
    git_sha,
    heavy_lock,
    wait_for_replay,
)
from research.features.four_factors import (
    FF_COLS,
    FF_NO_PACE,
    FF_PACE,
    build_four_factors,
    placebo_ff,
)

DOC = Path("docs/prereg/FOUR_FACTORS.md")
OUT_DIR = Path("reports/prereg_four_factors")
REPORT_MD = Path("reports/prereg_four_factors.md")
CACHE_DIR = Path("data/ops/four_factors_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
FROZEN_PREFIX = "0d87c2ce"
FROZEN_FULL = "0d87c2cea7ad257798918927b4065070ac8b6585d3fa67d5ec469f4d7a67f441"
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
N_BOOT = 2000
STATS: tuple[str, ...] = PROP_STATS
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "A4")
TLL_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "pts": (10, 15),
    "reb": (4, 6),
    "ast": (2, 4),
    "fg3m": (1, 2),
}

# --- frozen thresholds
COVERAGE_MIN = 0.95
POSS_SHARE_MIN = 0.995
RECON_MAX = 0.5
NULL_GAP_PP = 2.0
FLOOR = -0.005
ATTACK_FLOOR = -0.003
A4_KEEP = 0.5
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
PIT_TOL = 0.02
SLICE_WORSE = 0.01
MIN_SLICE_N = 300

UNSTATED: list[str] = [
    "no ContextResidualConfig.four_factors flag exists (nba/ is untouched, as in F8/F11): A0 is the "
    "unmodified production name list, so 'flag off byte-identical' holds by construction and is "
    "asserted as names(A0) == stat_feature_names(stat)",
    "all games in `games` (regular season and playoffs, season <= 2024) are team-games; check 1 "
    "coverage is computed on regular-season ids ('002...') with a result",
    "`games` stores no periods: overtime count = max(possessions.period) - 4, floored at 0",
    "league as-of mean uses team-games on STRICTLY EARLIER game_date (same-day games excluded, since "
    "tip order within a date is unknown); team windows use (game_date, game_id) order with shift(1)",
    "team with < 5 prior games takes the league mean (ff = 0); otherwise shrunk with n = games in "
    "the window (<= 20) and pseudo-count 10; the window crosses seasons",
    "out-of-range possession counts (<80 or >130): pace48 and tov_pct of the team-game (and the "
    "opponent's allowed tov copy) are replaced by the league as-of mean at that date",
    "the first date of the load has no prior league mean: every ff_* is 0.0 there",
    "check 5 outcome terciles: terciles of the realised stat (all four stats) among played rows per "
    "season; gap = max-min null rate in pp over minutes buckets (<5, 5-10, 10-20, >20; DNP audited, "
    "outside the gap) and over terciles, over all nine columns",
    "check 5 audits the ff columns AFTER the left join onto the production feature frame (a join "
    "miss would show as NULL); the builder itself zero-fills the undefined first-date values",
    "check 3 passes when the team-game raw series have zero NULLs and the played player rows have "
    "no NULL fga/fgm/fg3m/fta/tov/oreb/drb",
    "'stat passes' = pass rule 1 (point <= -0.005, CI upper < 0, BH p < 0.05) in BOTH 2023 and 2024 "
    "for the arm; guards, A3 and A4 attack gates are evaluated on 2024 all rows (2023 reported)",
    "BH families: for each season, the four stats' all-rows p, separately for A1 and for A2; A3/A4 "
    "carry raw p only",
    "attack gate vs A3 uses (arm - A3) paired point on all 2024 rows for either arm (A3 is the "
    "placebo of the nine-column set); A4 keeps the gain when A4_dcrps <= 0.5 * A2_dcrps (2024); "
    "'A1 must pass' means A1 passes every rule of its own family",
    "slice guard slices (n >= 300, 2024, arm - A0 > +0.01 fails): starter, bench, first15 (team_game_no < 15), "
    "teammate_out, ff_pace terciles, ff_def_efg terciles (terciles per season of the row's column)",
    "mechanism tables use the candidate arm A2 vs A0: pts and fg3m by ff_pace tercile, reb by "
    "ff_def_orb tercile; slice tables carry an `arm` field because A1 and A2 are both reported",
    "tll = mean threshold log loss of P(y >= n) on the integer grid at pts 10/15, reb 4/6, ast 2/4, fg3m 1/2",
    "A0 reproduction: integer CRPS and n against reports/lower_tail/candidates.json 'off' to 1e-6, all 4 stats x 2 seasons",
    "planted same-game test edits the target day's possessions (drop every other, one OT period "
    "added), box score (fga/tov/oreb inflated) and overtime count; planted-future edits every "
    "team-game on or after 2024-01-15; ff must be byte-identical for all games dated <= the day / <= cutoff",
    "possessions read: game_id, period, off_team, pts only (the other named columns are not needed "
    "by the frozen formulas)",
]


# --------------------------------------------------------------------------- inputs


def load_extra(db_path: str) -> dict[str, pl.DataFrame]:
    con = duckdb.connect(db_path, read_only=True)
    try:
        box = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.fga, s.fgm, s.fg3m, "
            "s.fta, s.tov, s.oreb, s.dreb FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON}"
        ).pl()
        poss = con.execute(
            "SELECT p.game_id, p.period, p.off_team, p.def_team, COALESCE(p.pts, 0) AS pts "
            f"FROM possessions p JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    return {"box": box, "poss": poss}


def load_study(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    extra = load_extra(db_path)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return {
        "games": games,
        "feats": feats,
        "cfg": cfg,
        "box": extra["box"],
        "poss": extra["poss"],
    }


def attach_ff(feats: pl.DataFrame, ff: pl.DataFrame) -> pl.DataFrame:
    out = feats.join(ff, on=["game_id", "team_id"], how="left")
    assert out.height == feats.height, "ff join changed the row count"
    return out


def arm_names(stat: str, arm: str) -> list[str]:
    extra: dict[str, tuple[str, ...]] = {
        "A0": (),
        "A1": FF_PACE,
        "A2": FF_COLS,
        "A3": FF_COLS,
        "A4": FF_NO_PACE,
    }
    return stat_feature_names(stat, extra=extra[arm])


# --------------------------------------------------------------------------- gate


def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def ff_equal(a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str]) -> bool:
    keys = ["game_id", "team_id"]
    ids = list(game_ids)
    x = a.filter(pl.col("game_id").is_in(ids)).sort(keys)
    y = b.filter(pl.col("game_id").is_in(ids)).sort(keys)
    if x.height != y.height or not x.select(keys).equals(y.select(keys)):
        return False
    return all(np.array_equal(x[c].to_numpy(), y[c].to_numpy()) for c in FF_COLS)


def edit_same_game(
    box: pl.DataFrame, poss: pl.DataFrame, targets: set[str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Box inflated, every other possession dropped, last 20 of the rest moved to period 6."""
    t = list(targets)
    box2 = box.with_columns(
        *[
            pl.when(pl.col("game_id").is_in(t)).then(pl.col(c) + k).otherwise(pl.col(c)).alias(c)
            for c, k in (("fga", 17), ("tov", 9), ("oreb", 7), ("fta", 11), ("fgm", 5), ("fg3m", 3))
        ]
    )
    p = poss.sort(["game_id", "period"])
    p = p.with_columns(pl.int_range(pl.len()).over("game_id").alias("_i"))
    p = p.filter(~(pl.col("game_id").is_in(t) & (pl.col("_i") % 2 == 1)))
    p = p.with_columns(pl.int_range(pl.len()).over("game_id").alias("_j"))
    p = p.with_columns(
        pl.when(pl.col("game_id").is_in(t) & (pl.col("_j") >= pl.len().over("game_id") - 20))
        .then(pl.lit(6))
        .otherwise(pl.col("period"))
        .alias("period")
    ).drop("_i", "_j")
    return box2, p


def edit_future(
    games: pl.DataFrame, box: pl.DataFrame, poss: pl.DataFrame, cutoff: dt.date
) -> tuple[pl.DataFrame, pl.DataFrame]:
    late = games.filter(pl.col("game_date") >= cutoff)["game_id"].to_list()
    box2 = box.with_columns(
        *[
            pl.when(pl.col("game_id").is_in(late))
            .then(pl.col(c) * 2 + 3)
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("fga", "fgm", "fg3m", "fta", "tov", "oreb", "dreb")
        ]
    )
    p = poss.with_columns(pl.int_range(pl.len()).over("game_id").alias("_i"))
    p = p.filter(~(pl.col("game_id").is_in(late) & (pl.col("_i") % 3 == 0))).drop("_i")
    return box2, p


def missingness(
    feats_ff: pl.DataFrame,
) -> tuple[list[dict[str, Any]], float]:
    """NULL/NaN rate of every ff_* column by realised-minutes bucket and outcome tercile (pp gap)."""
    d = feats_ff.with_columns(_bucket_minutes(pl.col("minutes")).alias("_b"))
    audit: list[dict[str, Any]] = []
    gaps: list[float] = []
    played = d.filter(pl.col("_b") != "DNP")
    tercile_frames: list[tuple[str, pl.DataFrame]] = []
    for stat in STATS:
        pf = played.with_columns(
            pl.col(stat).rank("average").over("season").alias("_r"),
            pl.len().over("season").alias("_n"),
        ).with_columns(
            pl.when(pl.col("_r") <= pl.col("_n") / 3)
            .then(pl.lit(f"{stat}:T1"))
            .when(pl.col("_r") <= pl.col("_n") * 2 / 3)
            .then(pl.lit(f"{stat}:T2"))
            .otherwise(pl.lit(f"{stat}:T3"))
            .alias("_t")
        )
        tercile_frames.append((stat, pf))
    for c in FF_COLS:
        rates: list[float] = []
        for b in ("DNP", "<5", "5-10", "10-20", ">20"):
            sub = d.filter(pl.col("_b") == b)
            r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
            audit.append({"col": c, "bucket": b, "null_rate": r, "n": sub.height})
            if b != "DNP":
                rates.append(r)
        for stat, pf in tercile_frames:
            for t in ("T1", "T2", "T3"):
                sub = pf.filter(pl.col("_t") == f"{stat}:{t}")
                r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
                audit.append({"col": c, "bucket": f"{stat}:{t}", "null_rate": r, "n": sub.height})
                rates.append(r)
        gaps.append((max(rates) - min(rates)) * 100.0)
    return audit, max(gaps)


def run_gate(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    t0 = time.time()
    sha = frozen_sha256(DOC)
    assert sha == FROZEN_FULL, f"frozen sha mismatch: {sha}"
    inp = load_study(db_path)
    games, box, poss, feats = inp["games"], inp["box"], inp["poss"], inp["feats"]
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    ff, tg = build_four_factors(games, box, poss)
    print(f"ff built {time.time() - t0:.0f}s ({ff.height} team-games)", flush=True)
    tests: list[dict[str, Any]] = []
    # 1. coverage
    reg = games.filter(pl.col("game_id").str.starts_with("002") & pl.col("home_pts").is_not_null())
    have = set(
        tg.filter(pl.col("poss") > 0)
        .group_by("game_id")
        .agg(pl.len().alias("k"))
        .filter(pl.col("k") == 2)["game_id"]
        .to_list()
    )
    cov = {
        str(s): float(reg.filter(pl.col("season") == s)["game_id"].is_in(list(have)).mean())
        for s in (2022, 2023, 2024)
    }
    for s, v in cov.items():
        tests.append(
            _chk(f"1 possessions coverage {s}", v, f">= {COVERAGE_MIN}", v >= COVERAGE_MIN)
        )
    # 2. range
    inrange = float((~tg["bad_poss"]).mean())  # type: ignore[arg-type]
    n_bad = int(tg["bad_poss"].sum())
    tests.append(
        _chk(
            "2 team-game possession counts in [80,130]",
            inrange,
            f">= {POSS_SHARE_MIN}",
            inrange >= POSS_SHARE_MIN,
        )
    )
    # 3. nulls
    raw_nulls = int(
        tg.select(
            pl.sum_horizontal(
                *[pl.col(f"b_{c}").is_null() for c in ("fga", "fgm", "fg3m", "fta", "tov", "oreb")]
            ).sum()
        ).item()
    )
    played = box.filter(pl.col("minutes") > 0)
    row_nulls = int(
        played.select(
            pl.sum_horizontal(
                *[pl.col(c).is_null() for c in ("fga", "fgm", "fg3m", "fta", "tov", "oreb", "dreb")]
            ).sum()
        ).item()
    )
    tests.append(
        _chk(
            "3 box inputs non-null (team-game sums / played rows)",
            [raw_nulls, row_nulls],
            "== 0",
            raw_nulls == 0 and row_nulls == 0,
        )
    )
    # 4. reconcile
    dd = tg.filter(pl.col("poss") > 0).with_columns(
        (pl.col("poss_pts") - pl.col("b_pts")).abs().alias("_d")
    )
    mean_d, p99 = float(dd["_d"].mean()), float(dd["_d"].quantile(0.99))  # type: ignore[arg-type]
    tests.append(
        _chk(
            "4 possession points vs box points mean |diff|",
            {"mean": mean_d, "p99": p99},
            f"<= {RECON_MAX}",
            mean_d <= RECON_MAX,
        )
    )
    # 5. missingness (after the join onto the production frame, before any filter)
    fj = attach_ff(feats, ff)
    audit, gap = missingness(fj)
    tests.append(
        _chk(
            "5 max NULL-rate gap across minutes buckets / outcome terciles (pp)",
            gap,
            f"<= {NULL_GAP_PP}",
            gap <= NULL_GAP_PP,
        )
    )
    # 6. planted tests
    reg24 = games.filter(
        (pl.col("season") == 2024) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list())
    upto = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    later = set(games["game_id"].to_list()) - upto
    box3, poss3 = edit_same_game(box, poss, targets)
    ff3, tg3 = build_four_factors(games, box3, poss3)
    same_ok = ff_equal(ff, ff3, upto)
    moves = not ff_equal(ff, ff3, later)
    ot_edit = bool(
        tg3.filter(pl.col("game_id").is_in(list(targets)))["n_ot"].min() >= 2  # type: ignore[operator]
    )
    tests += [
        _chk(
            "6 PLANT same-game possessions/box/overtime edited: ff identical through the day",
            int(same_ok),
            "== 1",
            same_ok,
        ),
        _chk("6 PLANT same-game is not inert (later games change)", int(moves), "== 1", moves),
        _chk("6 PLANT overtime count was actually edited", int(ot_edit), "== 1", ot_edit),
    ]
    cutoff = dt.date(2024, 1, 15)
    before = set(games.filter(pl.col("game_date") <= cutoff)["game_id"].to_list())
    box4, poss4 = edit_future(games, box, poss, cutoff)
    ff4, _ = build_four_factors(games, box4, poss4)
    fut_ok = ff_equal(ff, ff4, before)
    fut_moves = not ff_equal(ff, ff4, set(games["game_id"].to_list()) - before)
    tests += [
        _chk(
            "6 PLANT future (>= cutoff date) edits: earlier ff unchanged",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk("6 PLANT future is not inert (later games change)", int(fut_moves), "== 1", fut_moves),
    ]
    # 7 (part): flag-off byte-identical by construction
    names_ok = all(arm_names(s, "A0") == stat_feature_names(s) for s in STATS)
    tests.append(
        _chk(
            "7 A0 names == production stat_feature_names (flag off)",
            int(names_ok),
            "== 1",
            names_ok,
        )
    )
    res: dict[str, Any] = {
        "frozen_sha256": sha,
        "frozen_prefix_ok": sha.startswith(FROZEN_PREFIX),
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "coverage": cov,
        "n_bad_poss_team_games": n_bad,
        "n_team_games": tg.height,
        "ff_summary": {
            c: {"mean": float(ff[c].mean() or 0.0), "std": float(ff[c].std() or 0.0)}  # type: ignore[arg-type]
            for c in FF_COLS
        },
        "audit": audit,
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "max_season_loaded": int(feats["season"].max()),
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            k: res[k]
            for k in ("frozen_sha256", "coverage", "n_bad_poss_team_games", "tests", "audit")
        },
        indent=2,
        default=_jd,
        sort_keys=True,
    )
    (out_dir / "gate.json").write_text(body)
    res["gate_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    _write_json(out_dir / "results.json", res)
    return res


# --------------------------------------------------------------------------- walk-forward

META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    *FF_COLS,
)
KEYS = ["game_id", "player_id"]


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    max_blocks: int | None = None,
) -> dict[str, Any]:
    """Walk-forward month blocks (train strictly before the block; 2022 warm-up), integer grid."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    qs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for b, (start, end) in enumerate(month_blocks(test_all)):
        if max_blocks is not None and b >= max_blocks:
            break
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        z = (cal_df["resid"].to_numpy() - mu) / sc
        q = raw_quantiles(c, s, z, CRPS_TAUS)
        qs.append(to_integer_support(q).astype(np.int16))
        ys.append(block["y"].to_numpy())
        metas.append(block.select([c_ for c_ in META_COLS if c_ in block.columns]))
        print(
            f"  {stat} block {start} done ({time.time() - t0:.0f}s, n={block.height})", flush=True
        )
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def arm_cache(stat: str, arm: str) -> Path:
    return CACHE_DIR / f"arm_{stat}_{arm}.npz"


def collect_cached(
    frame: pl.DataFrame, stat: str, arm: str, cfg: ContextResidualConfig
) -> dict[str, Any]:
    p = arm_cache(stat, arm)
    if p.exists():
        z = np.load(p)
        return {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    t0 = time.time()
    out = collect_arm(frame, stat, cfg, arm_names(stat, arm))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=out["qi"], y=out["y"])
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {arm} done in {time.time() - t0:.0f}s", flush=True)
    return out


# --------------------------------------------------------------------------- scoring


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def row_scores(
    qi: np.ndarray, y: np.ndarray, stat: str, seed: int = SEEDS["pit"]
) -> dict[str, np.ndarray]:
    qf = qi.astype(float)
    pit = randomized_pit(qf, y, seed)
    tll = np.mean([_ll((qf >= n).mean(axis=1), y >= n) for n in TLL_THRESHOLDS[stat]], axis=0)
    return {
        "crps": crps_from_quantiles(qf, y),
        "resid": y - qf.mean(axis=1),
        "pit": pit,
        "tll": tll,
    }


def tercile_masks(d: pl.DataFrame, col: str) -> dict[str, np.ndarray]:
    v = d[col].to_numpy().astype(float)
    season = d["season"].to_numpy()
    out = {f"T{i}": np.zeros(d.height, dtype=bool) for i in (1, 2, 3)}
    for s in np.unique(season):
        m = (season == s) & ~np.isnan(v)
        if not m.any():
            continue
        lo, hi = np.quantile(v[m], [1 / 3, 2 / 3])
        out["T1"] |= m & (v <= lo)
        out["T2"] |= m & (v > lo) & (v <= hi)
        out["T3"] |= m & (v > hi)
    return out


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    dd = add_slice_columns(d)
    out: dict[str, np.ndarray] = {
        "all": np.ones(d.height, dtype=bool),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "teammate_out": (dd["sl_tm_out"] == "teammate_out").to_numpy(),
    }
    for col in ("ff_pace", "ff_def_efg"):
        for t, m in tercile_masks(d, col).items():
            out[f"{col}:{t}"] = m
    return out


GUARD_SLICES: tuple[str, ...] = tuple(
    k
    for k in (
        "starter",
        "bench",
        "first15",
        "teammate_out",
        "ff_pace:T1",
        "ff_pace:T2",
        "ff_pace:T3",
        "ff_def_efg:T1",
        "ff_def_efg:T2",
        "ff_def_efg:T3",
    )
)


def _delta_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    ci = _ci(delta, gid, N_BOOT)
    return {
        "dcrps": ci["point"],
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "p": boot_p_value(delta, gid, N_BOOT),
        "mde": (ci["hi"] - ci["lo"]) / 2.0,
    }


def score_stat(stat: str, arms: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ref = arms["A0"]["meta"]
    for a, v in arms.items():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
            f"{a}: arms must score identical rows"
        )
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    mech: list[dict[str, Any]] = []
    attack: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: row_scores(v["qi"][sm], v["y"][sm], stat) for a, v in arms.items()}
        masks = slice_masks(d)
        for a in arms:
            s = sc[a]
            r: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": a,
                "n_rows": int(d.height),
                "n_games": int(len(np.unique(gid))),
                "crps_int": float(s["crps"].mean()),
                "bias": float(s["resid"].mean()),
                "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()),
                "pit_q10": float((s["pit"] <= 0.10).mean()),
                "pit_q20": float((s["pit"] <= 0.20).mean()),
                "tll": float(s["tll"].mean()),
                "dcrps": None,
                "ci_lo": None,
                "ci_hi": None,
                "p": None,
                "p_bh": None,
                "mde": None,
            }
            if a != "A0":
                r.update(_delta_stats(s["crps"] - sc["A0"]["crps"], gid))
            rows.append(r)
        for a in ("A1", "A2"):
            da = sc[a]["crps"] - sc["A0"]["crps"]
            for name, m in masks.items():
                n = int(m.sum())
                rec: dict[str, Any] = {
                    "stat": stat,
                    "season": season,
                    "arm": a,
                    "slice": name,
                    "n": n,
                }
                if n >= MIN_SLICE_N or name == "all":
                    ds = _delta_stats(da[m], gid[m])
                    rec.update({k: ds[k] for k in ("dcrps", "ci_lo", "ci_hi")})
                sl.append(rec)
        # mechanism (descriptive, candidate arm A2 vs A0)
        d2 = sc["A2"]["crps"] - sc["A0"]["crps"]
        mcol = {"pts": "ff_pace", "fg3m": "ff_pace", "reb": "ff_def_orb"}.get(stat)
        if mcol is not None:
            for tname, m in tercile_masks(d, mcol).items():
                rec = {
                    "stat": stat,
                    "season": season,
                    "by": mcol,
                    "tercile": tname,
                    "n": int(m.sum()),
                }
                if int(m.sum()):
                    ds = _delta_stats(d2[m], gid[m])
                    rec.update({k: ds[k] for k in ("dcrps", "ci_lo", "ci_hi")})
                mech.append(rec)
        ent: dict[str, Any] = {"stat": stat, "season": season}
        for a in ("A1", "A2"):
            ent[f"{a}_minus_A3"] = _ci(sc[a]["crps"] - sc["A3"]["crps"], gid, N_BOOT)
        for a in ("A1", "A2", "A3", "A4"):
            ent[f"{a}_dcrps"] = float((sc[a]["crps"] - sc["A0"]["crps"]).mean())
        attack.append(ent)
    return {"rows": rows, "slices": sl, "mech": mech, "attack": attack}


def add_bh(rows: list[dict[str, Any]]) -> None:
    for season in TEST_SEASONS:
        for arm in ("A1", "A2"):
            tgt = [r for r in rows if r["season"] == season and r["arm"] == arm]
            adj = bh_adjust(np.array([r["p"] for r in tgt]))
            for r, a in zip(tgt, adj, strict=True):
                r["p_bh"] = float(a)


# --------------------------------------------------------------------------- pass rule


def evaluate_rules(res: dict[str, Any], tests_ok: bool) -> dict[str, Any]:
    rows, sl, atk = res["rows"], res["slices"], res["attack"]
    tab = {(r["stat"], r["season"], r["arm"]): r for r in rows}
    out: dict[str, Any] = {}
    for stat in STATS:
        per: dict[str, Any] = {}
        at = {a["season"]: a for a in atk if a["stat"] == stat}
        for arm in ("A1", "A2"):
            r1 = {
                str(se): bool(
                    tab[(stat, se, arm)]["dcrps"] <= FLOOR
                    and tab[(stat, se, arm)]["ci_hi"] < 0.0
                    and tab[(stat, se, arm)]["p_bh"] < 0.05
                )
                for se in TEST_SEASONS
            }
            t24 = tab[(stat, 2024, arm)]
            bad = {
                s["slice"]: s["dcrps"]
                for s in sl
                if s["stat"] == stat
                and s["season"] == 2024
                and s["arm"] == arm
                and s["slice"] in GUARD_SLICES
                and s["n"] >= MIN_SLICE_N
                and s.get("dcrps") is not None
                and s["dcrps"] > SLICE_WORSE
            }
            guards = {
                "abs_bias<=0.5": bool(abs(t24["bias"]) <= MAX_BIAS),
                "cov80_in_[.75,.85]": bool(COV_LO <= t24["cov80"] <= COV_HI),
                "pit_q10_within_.02": bool(abs(t24["pit_q10"] - 0.10) <= PIT_TOL),
                "no_slice_worse_than_+0.01": not bad,
            }
            per[arm] = {
                "rule1_by_season": r1,
                "rule1": all(r1.values()),
                "rule2_guards": guards,
                "rule2": all(guards.values()),
                "slices_worse": bad,
                "beats_A3_by_0.003 (2024)": bool(
                    at[2024][f"{arm}_minus_A3"]["point"] <= ATTACK_FLOOR
                ),
                "A1_minus_A3_2024" if arm == "A1" else "A2_minus_A3_2024": at[2024][
                    f"{arm}_minus_A3"
                ]["point"],
            }
        a2g, a4g = at[2024]["A2_dcrps"], at[2024]["A4_dcrps"]
        a4_keep = bool(a2g < 0 and a4g <= A4_KEEP * a2g)
        per["A2"]["A4_keeps_half_of_A2_gain"] = a4_keep
        per["A2"]["A4_or_A1_pass"] = bool(a4_keep or per["A1"]["rule1"])
        for arm in ("A1", "A2"):
            p = per[arm]
            p["rule3"] = bool(
                p["beats_A3_by_0.003 (2024)"] and tests_ok and (arm == "A1" or p["A4_or_A1_pass"])
            )
            p["pass"] = bool(p["rule1"] and p["rule2"] and p["rule3"])
        per["stat_pass"] = bool(per["A1"]["pass"] or per["A2"]["pass"])
        per["pace_carries"] = bool(per["A1"]["pass"] and not per["A2"]["pass"])
        per["any_pass_2023"] = bool(
            per["A1"]["rule1_by_season"]["2023"] or per["A2"]["rule1_by_season"]["2023"]
        )
        out[stat] = per
    out["close_no_stat_passes_2023"] = not any(out[s]["any_pass_2023"] for s in STATS)
    return out


# --------------------------------------------------------------------------- stages


def check_a0_repro(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for stat in STATS:
        a = arms[stat]
        for season in TEST_SEASONS:
            sm = (a["meta"]["season"] == season).to_numpy()
            got = float(crps_from_quantiles(a["qi"][sm].astype(float), a["y"][sm]).mean())
            exact = float(ref[stat][str(season)]["off"]["crps_int"])
            n_ref = int(ref[stat][str(season)]["off"]["n"])
            good = abs(got - exact) <= 1e-6 and int(sm.sum()) == n_ref
            ok &= good
            rec[f"{stat}_{season}"] = {
                "got": got,
                "stored": exact,
                "delta": got - exact,
                "n": int(sm.sum()),
                "n_ref": n_ref,
                "ok": good,
            }
    return {"ok": bool(ok), "detail": rec}


def run_arms(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    stored = (out_dir / "gate.json").read_bytes()
    if hashlib.sha256(stored).hexdigest() != cur.get("gate_sha256"):
        raise RuntimeError("gate.json does not match its recorded sha256")
    if not cur.get("gate_ok"):
        raise RuntimeError("minimum-data gate did not pass: no arm may be fitted")
    sha = frozen_sha256(DOC)
    assert sha == FROZEN_FULL
    t0 = time.time()
    inp = load_study(db_path)
    ff, _ = build_four_factors(inp["games"], inp["box"], inp["poss"])
    base = attach_ff(inp["feats"], ff)
    frames = {
        "A0": base,
        "A1": base,
        "A2": base,
        "A3": placebo_ff(base, SEEDS["placebo"]),
        "A4": base,
    }
    cfg = inp["cfg"]
    arms: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STATS}
    for stat in STATS:
        arms[stat]["A0"] = collect_cached(frames["A0"], stat, "A0", cfg)
    repro = check_a0_repro({s: arms[s]["A0"] for s in STATS})
    print(f"A0 repro ok={repro['ok']} {json.dumps(repro['detail'])}", flush=True)
    cur["a0_repro"] = repro
    cur["tests"] = [t for t in cur["tests"] if not t["test"].startswith("7 A0 reproduces")] + [
        _chk(
            "7 A0 reproduces production integer CRPS and n to 1e-6 (4 stats x 2 seasons)",
            int(repro["ok"]),
            "== 1",
            repro["ok"],
        )
    ]
    _write_json(path, cur)
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    for stat in STATS:
        for arm in ARMS[1:]:
            arms[stat][arm] = collect_cached(frames[arm], stat, arm, cfg)
            print(f"[{time.time() - t0:.0f}s] {stat} {arm} ready", flush=True)
    tables: dict[str, list[dict[str, Any]]] = {"rows": [], "slices": [], "mech": [], "attack": []}
    for stat in STATS:
        t = score_stat(stat, arms[stat])
        for k in tables:
            tables[k] += t[k]
        print(f"[{time.time() - t0:.0f}s] {stat} scored", flush=True)
    add_bh(tables["rows"])
    tests_ok = all(t["ok"] for t in cur["tests"])
    rules = evaluate_rules(tables, tests_ok)
    cur["arms"] = {
        **tables,
        "rules": rules,
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "frozen_sha256": sha,
        "n_boot": N_BOOT,
        "arms_seconds": time.time() - t0,
    }
    _write_json(path, cur)
    arms_out: dict[str, Any] = cur["arms"]
    return arms_out


def run_smoke(db_path: str = "nba.duckdb") -> None:
    t0 = time.time()
    inp = load_study(db_path)
    print(f"loaded {time.time() - t0:.0f}s", flush=True)
    ff, tg = build_four_factors(inp["games"], inp["box"], inp["poss"])
    print(f"ff built {time.time() - t0:.0f}s", flush=True)
    for c in FF_COLS:
        print(f"  {c}: mean {float(ff[c].mean()):+.5f} std {float(ff[c].std()):.5f}", flush=True)  # type: ignore[arg-type]
    base = attach_ff(inp["feats"], ff)
    for arm in ("A0", "A2"):
        o = collect_arm(base, "pts", inp["cfg"], arm_names("pts", arm), (2023,), max_blocks=1)
        crps = float(crps_from_quantiles(o["qi"].astype(float), o["y"]).mean())
        print(
            f"{arm} one-block crps_int {crps:.4f} n={len(o['y'])} [{time.time() - t0:.0f}s]",
            flush=True,
        )


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    a = r["arms"]
    rules = a["rules"]
    pre = r["frozen_sha256"][:8]
    tab = {(t["stat"], t["season"], t["arm"]): t for t in a["rows"]}
    ln = [
        "# FOUR_FACTORS results",
        "",
        f"Rule: docs/prereg/FOUR_FACTORS.md, frozen sha256 {r['frozen_sha256']} (first 8: {pre}), commit 918e03c. "
        f"Run git sha {a['git_sha']}. Seeds {json.dumps(a['seeds'])}; bootstrap {a['n_boot']} game-clustered. "
        "Season 2025 never loaded.",
        "",
        "## Minimum-data checks",
        "",
        "| test | value | threshold | ok |",
        "|---|---|---|---|",
    ]
    for t in r["tests"]:
        ln.append(
            f"| {t['test']} | {json.dumps(t['value'], default=_jd)} | {t['threshold']} | {t['ok']} |"
        )
    ln += [
        "",
        f"Possession counts outside [80,130]: {r['n_bad_poss_team_games']} of {r['n_team_games']} team-games (league-mean fallback).",
        "",
    ]
    ln += [
        "## Verdict per stat",
        "",
        "| stat | A1 pass | A2 pass | stat pass | pace carries | rule1 A1 (2023/2024) | rule1 A2 (2023/2024) |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in STATS:
        p = rules[s]
        ln.append(
            f"| {s} | {p['A1']['pass']} | {p['A2']['pass']} | {p['stat_pass']} | {p['pace_carries']} | "
            f"{p['A1']['rule1_by_season']['2023']}/{p['A1']['rule1_by_season']['2024']} | "
            f"{p['A2']['rule1_by_season']['2023']}/{p['A2']['rule1_by_season']['2024']} |"
        )
    ln += [
        "",
        f"Close (no stat passes rule 1 on 2023 under either family): {rules['close_no_stat_passes_2023']}",
        "",
    ]
    ln += [
        "## Arms table",
        "",
        "| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |",
        "|" + "---|" * 17,
    ]
    for t in a["rows"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {t['arm']} | {t['n_rows']} | {t['n_games']} | {_f(t['crps_int'])} | "
            f"{_f(t['dcrps'], 4, True)} | {_f(t['ci_lo'], 4, True)} | {_f(t['ci_hi'], 4, True)} | {_f(t['p'], 3)} | "
            f"{_f(t['p_bh'], 3)} | {_f(t['mde'])} | {_f(t['bias'], 3, True)} | {_f(t['cov80'], 3)} | "
            f"{_f(t['pit_q10'], 3)} | {_f(t['pit_q20'], 3)} | {_f(t['tll'])} |"
        )
    ln += [
        "",
        "## Attack (dCRPS vs A0; A1/A2 minus A3 with CI)",
        "",
        "| stat | season | A1 | A2 | A3 | A4 | A1-A3 [lo,hi] | A2-A3 [lo,hi] |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in a["attack"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {_f(t['A1_dcrps'], 4, True)} | {_f(t['A2_dcrps'], 4, True)} | {_f(t['A3_dcrps'], 4, True)} | {_f(t['A4_dcrps'], 4, True)} | "
            f"{_f(t['A1_minus_A3']['point'], 4, True)} [{_f(t['A1_minus_A3']['lo'], 4, True)},{_f(t['A1_minus_A3']['hi'], 4, True)}] | "
            f"{_f(t['A2_minus_A3']['point'], 4, True)} [{_f(t['A2_minus_A3']['lo'], 4, True)},{_f(t['A2_minus_A3']['hi'], 4, True)}] |"
        )
    ln += [
        "",
        "## Slices (arm - A0)",
        "",
        "| stat | season | arm | slice | n | dcrps | ci_lo | ci_hi |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in a["slices"]:
        ln.append(
            f"| {s['stat']} | {s['season']} | {s['arm']} | {s['slice']} | {s['n']} | {_f(s.get('dcrps'), 4, True)} | {_f(s.get('ci_lo'), 4, True)} | {_f(s.get('ci_hi'), 4, True)} |"
        )
    ln += [
        "",
        "## Mechanism (A2 - A0, descriptive)",
        "",
        "| stat | season | by | tercile | n | dcrps | ci_lo | ci_hi |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in a["mech"]:
        ln.append(
            f"| {m['stat']} | {m['season']} | {m['by']} | {m['tercile']} | {m['n']} | {_f(m.get('dcrps'), 4, True)} | {_f(m.get('ci_lo'), 4, True)} | {_f(m.get('ci_hi'), 4, True)} |"
        )
    ln += [
        "",
        "## Missingness audit (NULL/NaN rate of ff_* after the join, by bucket)",
        "",
        "| col | bucket | null_rate |",
        "|---|---|---|",
    ]
    for x in r["audit"]:
        if x["bucket"] in ("DNP", "<5", "5-10", "10-20", ">20"):
            ln.append(f"| {x['col']} | {x['bucket']} | {x['null_rate']:.5f} |")
    ln += ["", "## Unstated details (chosen before scoring)", ""] + [f"- {u}" for u in UNSTATED]
    ln += ["", "## Proposed ledger rows (draft, unnumbered; maintainer appends)", ""]
    ln += ledger_rows(tab, pre)
    return "\n".join(ln) + "\n"


def ledger_rows(tab: dict[tuple[str, int, str], dict[str, Any]], pre: str) -> list[str]:
    ln: list[str] = []
    for stat in STATS:
        for season in TEST_SEASONS:
            for arm in ("A1", "A2"):
                t = tab[(stat, season, arm)]
                a0 = tab[(stat, season, "A0")]
                a3 = tab[(stat, season, "A3")]
                a4 = tab[(stat, season, "A4")]
                ln.append(
                    f"| T### | 2026-10-10 | FOUR_FACTORS (frozen sha256 {pre}) | {stat} {season} {arm} vs A0 all rows, "
                    f"integer-support CRPS, n={t['n_rows']} rows / {t['n_games']} games | {t['dcrps']:+.4f} | "
                    f"{t['ci_lo']:+.4f} | {t['ci_hi']:+.4f} | game | {_f(t['p_bh'], 3)} | MDE {t['mde']:.4f}; "
                    f"bias {a0['bias']:+.3f} -> {t['bias']:+.3f}; cov80 {a0['cov80']:.3f} -> {t['cov80']:.3f}; "
                    f"A3 placebo {a3['dcrps']:+.4f}, A4 no-pace {a4['dcrps']:+.4f} | not holdout (<=2024) |"
                )
    return ln


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    tab = {(t["stat"], t["season"], t["arm"]): t for t in r["arms"]["rows"]}
    pre = r["frozen_sha256"][:8]
    body = [
        "# FOUR_FACTORS proposed ledger rows (draft; maintainer appends to docs/TEST_LEDGER.md)",
        "",
        "Columns: id | date | test | result | effect | CI lo | CI hi | cluster | p(BH) | notes | holdout status.",
        "",
        *ledger_rows(tab, pre),
        "",
        "Verdict rows (one per stat) belong in the notes of the A2 rows: see reports/prereg_four_factors.md.",
    ]
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FOUR_FACTORS (frozen rule)")
    ap.add_argument("stage", choices=("gate", "arms", "report", "smoke", "run"))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "smoke":
        run_smoke(a.db_path)
    elif a.stage == "gate":
        res = run_gate(a.db_path)
        print(
            json.dumps(
                {k: res[k] for k in ("frozen_sha256", "tests", "gate_ok")}, indent=1, default=_jd
            )
        )
    elif a.stage == "arms":
        wait_for_replay()
        with heavy_lock():
            res = run_arms(a.db_path)
        print(json.dumps(res["rules"], indent=1))
    elif a.stage == "run":
        wait_for_replay()
        with heavy_lock():
            g = run_gate(a.db_path)
            print(
                json.dumps({k: g[k] for k in ("tests", "gate_ok")}, indent=1, default=_jd),
                flush=True,
            )
            if not g["gate_ok"]:
                print("GATE FAILED: stop, no model result", flush=True)
                return 2
            res = run_arms(a.db_path)
        REPORT_MD.write_text(render_report())
        write_ledger()
        print(json.dumps(res["rules"], indent=1))
    else:
        REPORT_MD.write_text(render_report())
        write_ledger()
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
