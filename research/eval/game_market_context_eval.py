# ruff: noqa: E501
"""GAME_MARKET_CONTEXT harness (rule: docs/prereg/GAME_MARKET_CONTEXT.md; DRAFT until frozen).

    M="-m research.eval.game_market_context_eval"
    .venv/bin/python -W ignore $M gate  --allow-draft --doc docs/prereg/GAME_MARKET_CONTEXT.md   # checks 1-6, only A0 fitted
    .venv/bin/python -W ignore $M smoke --doc docs/prereg/GAME_MARKET_CONTEXT.md --sha <64 hex>  # one month block, A0 vs A1 (pts)
    .venv/bin/python -W ignore $M run   --doc docs/prereg/GAME_MARKET_CONTEXT.md --sha <64 hex>  # gate -> 6 arms x 4 stats -> results.json
    .venv/bin/python -W ignore $M report --doc docs/prereg/GAME_MARKET_CONTEXT.md --sha <64 hex> # reports/prereg_game_market_context.md

``--sha`` must equal the sha256 of everything above the whole-line ``=== RESULTS BELOW ===`` marker
(parsed as a whole line, never as a substring: the freeze command quoted in the header contains the
marker text). ``--allow-draft`` skips that check and is for the gate stage only. Research module
(``nba/`` untouched): the market columns go through ``stat_feature_names``-derived name lists.
``nba.duckdb`` and ``odds_history.duckdb`` are opened read-only, every SQL carries ``season <= 2024``
and the frames are asserted; season 2025 is never loaded. Heavy stages take ``data/ops/heavy.lock``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import attach_realised
from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
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
from research.eval.f11_lineup_context import _ci, _jd, git_sha
from research.eval.four_factors_eval import (
    COV_HI,
    COV_LO,
    FLOOR,
    MAX_BIAS,
    MIN_SLICE_N,
    N_BOOT,
    SLICE_WORSE,
    _chk,
    _delta_stats,
    row_scores,
)
from research.eval.minutes_hazard_eval import HeavyLock, frozen_sha
from research.eval.odds_history_eval import (
    MARKET_EPS,
    MODEL_EPS,
    PGE_KMAX,
    ll_vec,
    model_p_over,
    pge_matrix,
)
from research.features.game_market_context import (
    LEAD_MIN,
    MKT_COLS,
    ODDS_DB,
    attach_market,
    build_game_market,
    league_total_mean,
    load_market_rows,
    placebo_fixed_points,
    placebo_market,
)

#: HEAD when this process started: artifacts written later in the run carry the code that ran, not a
#: commit made mid-run (results.json of the 2026-10-11 run stamped an unrelated HEAD; T501).
RUN_GIT_SHA = git_sha()

DOC = Path("docs/prereg/GAME_MARKET_CONTEXT.md")
OUT_DIR = Path("reports/prereg_game_market_context")
REPORT_MD = Path("reports/prereg_game_market_context.md")
CACHE_DIR = Path("data/ops/game_market_context_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
PAIRS_DIR = Path("data/ops/model_clv_pairs")
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
STATS: tuple[str, ...] = PROP_STATS
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "P1a", "P1b")
KEYS = ["game_id", "player_id"]

COVERAGE_MIN = 0.90
NULL_GAP_PP = 2.0
LL_NONINF = 0.002
MATURE_GAMES = (
    600  # market-bearing games in the training window for a test block to count as "mature"
)
AUDIT_SEASONS = (2023, 2024)
REPLACE: dict[str, str | None] = {
    "exp_margin": "mkt_margin",
    "abs_margin": "mkt_abs_margin",
    "r20_pts_for": "mkt_total",
    "r20_pts_against": "mkt_pace",
    "opp_r20_pts_for": None,
    "opp_r20_pts_against": None,
}

UNSTATED: list[str] = [
    "market source: odds_history.duckdb snapshot_kind='t60' only, season <= 2024 in the SQL, books pinnacle / draftkings / fanduel / betmgm; Pinnacle where it has a complete (spread AND total) quote for the game, else the MEDIAN over >= 2 complete consensus books; games with neither have NULL market columns",
    "snapshot choice: of several t60 snapshots of one (game, book) the latest with snapshot_ts <= real tipoff_utc - 55 min; a game with no real tip is NOT given a proxy (its market is NULL); a late snapshot is refused, never clipped",
    "main line: the quote closest to even money per (game, book, market, side); spread sides must mirror (away = - home) else the book's margin is NULL; over/under points must match else the total is NULL",
    "signs: mkt_margin = - home spread for a home player, + home spread for an away player (positive = player's team favoured); mkt_abs_margin = |home spread|",
    "mkt_pace = mkt_total - mean of the ACTUAL totals of the last 300 completed games with game_date STRICTLY earlier (same-day games excluded), NULL below 50 prior games",
    "A1 replaces exp_margin, abs_margin, r20_pts_for, r20_pts_against in place (same column positions) with mkt_margin, mkt_abs_margin, mkt_total, mkt_pace and DROPS opp_r20_pts_for / opp_r20_pts_against (no market analogue); A2 = production names + the four market columns appended; P1a / P1b = A1 / A2 names on the placebo frame",
    "training rows of 2022 (and any game without a usable snapshot) carry NULL market columns; LightGBM treats NaN as missing natively; every arm scores the SAME rows (n_prior >= 5, played, season 2023/2024) including rows of games with a NULL market",
    "A3 (descriptive): A1 names minus every player-own box-stat history column (m10_pts/reb/ast/fg3m, m5/m40/m100/sd10/role_gap/rate of the stat); target offset m = 0 and s = 1 (the model predicts the raw stat); keeps minutes windows, role, rest, market context, teammates-out (n_out_rot, vac_min, vac_<stat>), opp_allow_<stat>",
    "placebo: the game-level (home margin, total) tuples are permuted ACROSS games within a game date as a derangement (no game keeps its own numbers); dates with one market game are pooled by (season, month); pace and sign are re-derived per row; numpy default_rng(0)",
    "over/under: the DraftKings t60 pairs of data/ops/model_clv_pairs/pairs_{2023,2024}.parquet (their line/price/keys), with p_over recomputed in-run for EVERY arm from its own integer quantile grid (same pge_matrix/model_p_over as the pairs builder); rows where any arm has status != 0 (push, odd line, certain push) are dropped for all arms; log loss clipped at MODEL_EPS = 0.5/199; non-inferiority = point estimate of (arm - A0) <= +0.002, CI reported",
    "'stat passes' for arm A in {A1, A2} = dCRPS <= -0.005 with CI upper < 0 and BH p < 0.05 (family {A1,A2} x 4 stats per season) in BOTH 2023 (mature-block rows, AM1) and 2024 (all rows), AND (A - placebo) CI upper < 0 in both seasons, AND the DraftKings log-loss non-inferiority in both seasons, AND the 2024 guards; no cell passing 2023 closes the question",
    "tercile slices cut |market margin| and market total at the tercile edges of the scored market-bearing rows of the same season (descriptive); 'mature' blocks = test month blocks with >= 600 market-bearing training games (descriptive)",
    "check 4 buckets = DNP, <5, 5-20, >20 realised minutes over all player_game_stats rows of 2023-2024; the gap is max-min of the NULL-market rate over the four, DNP included (stricter); the played-only gap is reported too",
    "check 1 compares A0 with reports/lower_tail/candidates.json 'off' AS IT STANDS (re-baselined on the current DB 2026-10-11; 2024 n 27,701), all 4 stats x 2 seasons, CRPS to 1e-6, n identical",
    "snapshot_ts is naive UTC and is compared with the schedule's tipoff_utc (naive UTC); attach_real_tips (tip_et) is used to assert every used game has a REAL tip (no proxy fallback)",
]


# --------------------------------------------------------------------------- freeze


def require_frozen(doc: Path, expected: str | None, allow_draft: bool) -> str:
    """sha256 of the doc above the whole-line marker; refuses unless it equals ``expected`` AND the
    Status line says FROZEN (``--allow-draft`` is the gate only)."""
    import re

    sha = frozen_sha(doc)
    if allow_draft:
        return sha
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    status = [ln for ln in doc.read_text().splitlines() if ln.startswith("Status:")]
    if not status or "FROZEN" not in status[0]:
        raise SystemExit("STOP: the doc's Status line does not say FROZEN")
    return sha


# --------------------------------------------------------------------------- inputs


def load_study(
    db_path: str = "nba.duckdb",
    odds_db: str | Path = ODDS_DB,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    tips = build_game_tipoff(max_season=MAX_SEASON)  # holdout: never read 2025-26 schedule files
    rows = load_market_rows(odds_db)
    gm, counts = build_game_market(rows, tips)
    lg = league_total_mean(games)
    base = attach_market(feats, gm, lg)
    return {
        "games": games,
        "pgs": pgs,
        "feats": feats,
        "base": base,
        "cfg": cfg,
        "tips": tips,
        "odds_rows": rows,
        "gm": gm,
        "gm_counts": counts,
        "lg": lg,
    }


def arm_names(stat: str, arm: str) -> list[str]:
    prod = stat_feature_names(stat)
    a1: list[str] = []
    for n in prod:
        if n in REPLACE:
            r = REPLACE[n]
            if r is not None:
                a1.append(r)
        else:
            a1.append(n)
    if arm == "A0":
        return prod
    if arm in ("A1", "P1a"):
        return a1
    if arm in ("A2", "P1b"):
        return [*prod, *MKT_COLS]
    if arm == "A3":
        own = {f"m10_{s}" for s in STATS} | {
            f"{p}_{stat}" for p in ("m5", "m40", "m100", "sd10", "role_gap", "rate")
        }
        return [n for n in a1 if n not in own]
    raise KeyError(arm)


def arm_frames(base: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Identical keys and row order in every arm; the placebo arms differ in the market columns only."""
    plc = placebo_market(base, SEEDS["placebo"])
    return {"A0": base, "A1": base, "A2": base, "A3": base, "P1a": plc, "P1b": plc}


# --------------------------------------------------------------------------- walk-forward

META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "game_date",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    "mkt_margin",
    "mkt_abs_margin",
    "mkt_total",
    "mkt_src",
)


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    max_blocks: int | None = None,
    own_history: bool = True,
) -> dict[str, Any]:
    """Walk-forward month blocks (train strictly before the block; 2022 warm-up), integer grid.
    ``own_history=False`` (A3) sets the target offset m to 0 and s to 1: the model predicts the stat."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    if not own_history:
        sf = sf.with_columns(
            pl.lit(0.0).alias("m"), pl.lit(1.0).alias("s"), pl.col("y").alias("resid")
        )
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
        mt = block.select([c_ for c_ in META_COLS if c_ in block.columns]).with_columns(
            pl.lit(
                int(train.filter(pl.col("mkt_total").is_not_null())["game_id"].n_unique())
            ).alias("train_mkt_games")
        )
        metas.append(mt)
        print(
            f"  {stat} block {start} done ({time.time() - t0:.0f}s, n={block.height})", flush=True
        )
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def collect_cached(
    frame: pl.DataFrame, stat: str, arm: str, cfg: ContextResidualConfig
) -> dict[str, Any]:
    p = CACHE_DIR / f"arm_{stat}_{arm}.npz"
    if p.exists():
        z = np.load(p)
        return {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    t0 = time.time()
    out = collect_arm(frame, stat, cfg, arm_names(stat, arm), own_history=(arm != "A3"))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=out["qi"], y=out["y"])
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {arm} done in {time.time() - t0:.0f}s", flush=True)
    return out


# --------------------------------------------------------------------------- gate


def check_a0_repro(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for stat, a in arms.items():
        for season in TEST_SEASONS:
            sm = (a["meta"]["season"] == season).to_numpy()
            got = float(crps_from_quantiles(a["qi"][sm].astype(float), a["y"][sm]).mean())
            stored = float(ref[stat][str(season)]["off"]["crps_int"])
            n_ref = int(ref[stat][str(season)]["off"]["n"])
            good = abs(got - stored) <= 1e-6 and int(sm.sum()) == n_ref
            ok &= good
            rec[f"{stat}_{season}"] = {
                "got": got,
                "stored": stored,
                "delta": got - stored,
                "n": int(sm.sum()),
                "n_ref": n_ref,
                "ok": good,
            }
    return {"ok": bool(ok), "detail": rec}


def check_2_coverage(
    games: pl.DataFrame, gm: pl.DataFrame, base: pl.DataFrame
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reg = games.filter(pl.col("game_id").str.starts_with("002") & pl.col("home_pts").is_not_null())
    have = set(gm["game_id"].to_list())
    src = {k: v for k, v in gm.select("game_id", "mkt_src").iter_rows()}
    out: dict[str, Any] = {}
    tests: list[dict[str, Any]] = []
    scored = base.filter(pl.col("n_prior") >= 5)
    for s in (2022, 2023, 2024):
        r = reg.filter(pl.col("season") == s)
        a = games.filter(pl.col("season") == s)
        ids = r["game_id"].to_list()
        v = float(np.mean([g in have for g in ids])) if ids else 0.0
        va = float(np.mean([g in have for g in a["game_id"].to_list()])) if a.height else 0.0
        n_pin = sum(1 for g in ids if src.get(g) == "pinnacle")
        n_con = sum(1 for g in ids if src.get(g) == "consensus")
        sr = scored.filter(pl.col("season") == s)
        out[str(s)] = {
            "regular_season_games": len(ids),
            "with_market": int(sum(g in have for g in ids)),
            "coverage_regular": v,
            "coverage_all_games": va,
            "pinnacle_games": n_pin,
            "consensus_games": n_con,
            "scored_rows": sr.height,
            "scored_rows_with_market": int(sr["mkt_total"].is_not_null().sum()),
            "scored_row_coverage": float(sr["mkt_total"].is_not_null().sum()) / max(sr.height, 1),
        }
        if s in AUDIT_SEASONS:
            tests.append(
                _chk(
                    f"2 market coverage {s} (regular season; Pinnacle else consensus; spread AND total)",
                    v,
                    f">= {COVERAGE_MIN}",
                    v >= COVERAGE_MIN,
                )
            )
    return tests, out


def check_3_known_at(
    gm: pl.DataFrame, counts: dict[str, int], rows: pl.DataFrame, tips: pl.DataFrame
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = attach_real_tips(gm.select("game_id").with_columns(pl.lit(1).alias("_x")), tips)
    n_proxy = int(probe["tip_et"].is_null().sum())
    min_lead = float(gm["mkt_min_before_tip"].min())  # type: ignore[arg-type]
    late = int((gm["mkt_min_before_tip"] < LEAD_MIN - 1e-9).sum())
    j = rows.join(tips.select("game_id", "tipoff_utc"), on="game_id", how="left")
    used_ts = gm.select("game_id", "mkt_snapshot_ts")
    lu_bad = int(
        j.join(used_ts, on="game_id")
        .filter(pl.col("snapshot_ts") == pl.col("mkt_snapshot_ts"))
        .filter(pl.col("last_update") > pl.col("snapshot_ts"))
        .height
    )
    out = {
        "snapshot_rows": counts,
        "min_minutes_before_tip": min_lead,
        "median_minutes_before_tip": float(gm["mkt_min_before_tip"].median()),  # type: ignore[arg-type]
        "used_games_without_real_tip": n_proxy,
        "used_rows_last_update_after_snapshot": lu_bad,
        "games": gm.height,
    }
    return [
        _chk("3 every used snapshot is <= tip - 55 min (violations)", late, "== 0", late == 0),
        _chk("3 every used game has a real tip (no proxy)", n_proxy, "== 0", n_proxy == 0),
        _chk(
            "3 no used quote was updated after its snapshot stamp",
            lu_bad,
            "== 0",
            lu_bad == 0,
        ),
    ], out


def _bucket(m: pl.Expr) -> pl.Expr:
    return (
        pl.when(m.is_null() | (m <= 0))
        .then(pl.lit("DNP"))
        .when(m < 5)
        .then(pl.lit("<5"))
        .when(m <= 20)
        .then(pl.lit("5-20"))
        .otherwise(pl.lit(">20"))
    )


def check_4_missingness(
    pgs: pl.DataFrame, games: pl.DataFrame, gm: pl.DataFrame
) -> tuple[float, float, list[dict[str, Any]]]:
    have = set(gm["game_id"].to_list())
    d = (
        pgs.join(games.select(["game_id", "season"]), on="game_id", how="left")
        .filter(pl.col("season").is_in(list(AUDIT_SEASONS)))
        .with_columns(_bucket(pl.col("minutes")).alias("_b"))
        .with_columns(pl.col("game_id").is_in(list(have)).not_().alias("_miss"))
    )
    rates: dict[str, float] = {}
    audit: list[dict[str, Any]] = []
    for b in ("DNP", "<5", "5-20", ">20"):
        sub = d.filter(pl.col("_b") == b)
        rates[b] = float(sub["_miss"].mean())  # type: ignore[arg-type]
        audit.append({"bucket": b, "n": sub.height, "null_market_rate": rates[b]})
    gap = (max(rates.values()) - min(rates.values())) * 100.0
    played = [v for k, v in rates.items() if k != "DNP"]
    return gap, (max(played) - min(played)) * 100.0, audit


def _gm_equal(a: pl.DataFrame, b: pl.DataFrame, ids: set[str]) -> bool:
    cols = ["mkt_home_margin", "mkt_total", "mkt_p_home", "mkt_src"]
    x = a.filter(pl.col("game_id").is_in(list(ids))).sort("game_id").select("game_id", *cols)
    y = b.filter(pl.col("game_id").is_in(list(ids))).sort("game_id").select("game_id", *cols)
    return x.equals(y)


def _inject(
    rows: pl.DataFrame,
    tips: pl.DataFrame,
    gids: set[str],
    minutes_before: float,
    dm: float,
    dt_: float,
) -> pl.DataFrame:
    """Copy of the t60 rows of ``gids`` re-stamped ``minutes_before`` before tip with the spread
    moved by ``dm`` (home line) and the total by ``dt_``; appended to ``rows``."""
    sub = rows.filter(pl.col("game_id").is_in(list(gids))).join(
        tips.select("game_id", "tipoff_utc"), on="game_id"
    )
    sub = sub.with_columns(
        (pl.col("tipoff_utc") - pl.duration(seconds=int(minutes_before * 60))).alias("snapshot_ts"),
        pl.when(pl.col("market") == "spreads")
        .then(pl.col("point") + pl.when(pl.col("side") == "home").then(dm).otherwise(-dm))
        .when(pl.col("market") == "totals")
        .then(pl.col("point") + dt_)
        .otherwise(pl.col("point"))
        .alias("point"),
    ).drop("tipoff_utc")
    return pl.concat([rows, sub.select(rows.columns)])


def planted_tests(inp: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    games, rows, tips, gm = inp["games"], inp["odds_rows"], inp["tips"], inp["gm"]
    feats, lg = inp["feats"], inp["lg"]
    reg24 = games.filter(
        (pl.col("season") == 2024) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = set(reg24.filter(pl.col("game_date") == day)["game_id"].to_list()) & set(
        gm["game_id"].to_list()
    )
    # same-game 1: a snapshot 30 min and 54 min before tip (refused) leaves the market identical
    g30, _ = build_game_market(_inject(rows, tips, targets, 30, 5.0, 20.0), tips)
    g54, _ = build_game_market(_inject(rows, tips, targets, 54, 5.0, 20.0), tips)
    same30 = _gm_equal(gm, g30, targets)
    same54 = _gm_equal(gm, g54, targets)
    # non-inert control: a snapshot at exactly tip - 55 (usable, later than the stored one) is taken
    g55, _ = build_game_market(_inject(rows, tips, targets, 55, 5.0, 20.0), tips)
    a = gm.filter(pl.col("game_id").is_in(list(targets))).sort("game_id")
    b = g55.filter(pl.col("game_id").is_in(list(targets))).sort("game_id")
    moved = float(
        (
            (a["mkt_total"].to_numpy() != b["mkt_total"].to_numpy())
            | (a["mkt_home_margin"].to_numpy() != b["mkt_home_margin"].to_numpy())
        ).mean()
    )
    # same-game 2: altering the day's final scores leaves the day's pace baseline identical
    sc2 = games.with_columns(
        pl.when(pl.col("game_date") == day)
        .then(pl.col("home_pts") + 40)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    lg2 = league_total_mean(sc2)
    day_ids = games.filter(pl.col("game_date") == day)["game_id"].to_list()
    lg_same = (
        lg.filter(pl.col("game_id").is_in(day_ids))
        .sort("game_id")
        .equals(lg2.filter(pl.col("game_id").is_in(day_ids)).sort("game_id"))
    )
    after = (
        games.filter(pl.col("game_date") > day)
        .sort(["game_date", "game_id"])["game_id"]
        .to_list()[:100]
    )
    lg_moves = (
        not lg.filter(pl.col("game_id").is_in(after))
        .sort("game_id")
        .equals(lg2.filter(pl.col("game_id").is_in(after)).sort("game_id"))
    )
    # future: spreads/totals/scores on/after the cutoff altered -> earlier games identical, later change
    cutoff = dt.date(2024, 1, 15)
    fut_ids = set(games.filter(pl.col("game_date") >= cutoff)["game_id"].to_list())
    before = set(games.filter(pl.col("game_date") < cutoff)["game_id"].to_list())
    rows2 = rows.with_columns(
        pl.when(pl.col("game_id").is_in(list(fut_ids)) & (pl.col("market") == "totals"))
        .then(pl.col("point") + 10.0)
        .when(pl.col("game_id").is_in(list(fut_ids)) & (pl.col("market") == "spreads"))
        .then(pl.col("point") + 3.0)
        .otherwise(pl.col("point"))
        .alias("point")
    )
    gf, _ = build_game_market(rows2, tips)
    fut_ok = _gm_equal(gm, gf, before & set(gm["game_id"].to_list()))
    fut_moves = not _gm_equal(gm, gf, fut_ids & set(gm["game_id"].to_list()))
    sc3 = games.with_columns(
        pl.when(pl.col("game_date") >= cutoff)
        .then(pl.col("home_pts") + 25)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    lg3 = league_total_mean(sc3)
    lg_fut_ok = (
        lg.filter(pl.col("game_id").is_in(list(before)))
        .sort("game_id")
        .equals(lg3.filter(pl.col("game_id").is_in(list(before))).sort("game_id"))
    )
    # placebo: same marginal, no game information, no fixed points
    base = inp["base"]
    plc = placebo_market(base, SEEDS["placebo"])
    r = base.filter(pl.col("_hm").is_not_null()).unique("game_id").sort("game_id")
    p = plc.filter(pl.col("_hm").is_not_null()).unique("game_id").sort("game_id")
    marg_eq = bool(
        np.allclose(np.sort(r["_hm"].to_numpy()), np.sort(p["_hm"].to_numpy()))
        and np.allclose(np.sort(r["_tot"].to_numpy()), np.sort(p["_tot"].to_numpy()))
    )
    fp = placebo_fixed_points(base, plc)
    hm_act = games.select("game_id", (pl.col("home_pts") - pl.col("away_pts")).alias("_act_home"))
    sgn = pl.when(pl.col("is_home_f") == 1).then(1.0).otherwise(-1.0)

    def _corr(fr: pl.DataFrame) -> tuple[float, float]:
        """corr(mkt_margin, actual signed margin) WITHIN home rows and WITHIN away rows, so the
        shared home-court level (a function of is_home_f only) is not counted as game information."""
        d = (
            fr.filter(pl.col("mkt_margin").is_not_null())
            .join(hm_act, on="game_id", how="left")
            .with_columns((sgn * pl.col("_act_home")).alias("_act"))
            .filter(pl.col("_act").is_not_null())
        )
        cs = [
            float(np.corrcoef(g["mkt_margin"].to_numpy(), g["_act"].to_numpy())[0, 1])
            for g in (d.filter(pl.col("is_home_f") == 1), d.filter(pl.col("is_home_f") == 0))
        ]
        return min(cs), max(cs, key=abs)

    c_real = _corr(base)[0]
    c_plc = _corr(plc)[1]
    # sign: a home row is the negated home spread, an away row the home spread
    one = base.filter(pl.col("_hm").is_not_null()).unique("game_id").head(1)["game_id"].item()
    rr = base.filter(pl.col("game_id") == one)
    sign_ok = bool(
        np.allclose(
            rr["mkt_margin"].to_numpy(),
            np.where(rr["is_home_f"].to_numpy() == 1, 1.0, -1.0) * rr["_hm"].to_numpy(),
        )
        and c_real > 0.3
    )
    detail = {
        "plant_day": str(day),
        "plant_games": len(targets),
        "tip55_snapshot_changed_share": moved,
        "placebo_unchanged": fp,
        "corr_real_vs_actual_signed_margin_within_home_flag_min": c_real,
        "corr_placebo_vs_actual_signed_margin_within_home_flag_maxabs": c_plc,
    }
    t = [
        _chk("5 PLANT same-game: tip-30 snapshot refused", int(same30), "== 1", same30),
        _chk("5 PLANT same-game: tip-54 snapshot refused", int(same54), "== 1", same54),
        _chk(
            "5 PLANT same-game is not inert (tip-55 snapshot is taken: share of games changed)",
            moved,
            ">= 0.9",
            moved >= 0.9,
        ),
        _chk(
            "5 PLANT same-game: altered same-day final scores leave that day's pace baseline identical",
            int(lg_same and lg_moves),
            "== 1 (and later days do move)",
            lg_same and lg_moves,
        ),
        _chk(
            "5 PLANT future (>= 2024-01-15) odds edits: earlier market identical",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk(
            "5 PLANT future is not inert (later market changes)", int(fut_moves), "== 1", fut_moves
        ),
        _chk(
            "5 PLANT future score edits: earlier pace baseline identical",
            int(lg_fut_ok),
            "== 1",
            lg_fut_ok,
        ),
        _chk("5 placebo keeps the marginal distribution", int(marg_eq), "== 1", marg_eq),
        _chk(
            "5 placebo fixed points (share of rows whose own game's numbers survive)",
            fp["unchanged_share"],
            "<= 0.01",
            fp["unchanged_share"] <= 0.01,
        ),
        _chk(
            "5 placebo has no game information (max |corr| with the actual signed margin within home/away rows) and real has (min > 0.3)",
            c_plc,
            "|x| < 0.05 and real > 0.3",
            abs(c_plc) < 0.05 and c_real > 0.3,
        ),
        _chk("5 sign of mkt_margin by home/away", int(sign_ok), "== 1", sign_ok),
    ]
    del feats
    return t, detail


def check_6_keys(base: pl.DataFrame) -> dict[str, Any]:
    fr = arm_frames(base)
    ref = fr["A0"].select(KEYS)
    same = {a: bool(f.select(KEYS).equals(ref)) for a, f in fr.items()}
    ys = {a: bool(f["pts"].equals(fr["A0"]["pts"])) for a, f in fr.items()}
    return {"keys_equal": same, "y_equal": ys, "ok": all(same.values()) and all(ys.values())}


def descriptive_corr(base: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in AUDIT_SEASONS:
        d = base.filter(
            (pl.col("season") == s)
            & (pl.col("n_prior") >= 5)
            & pl.col("mkt_margin").is_not_null()
            & pl.col("exp_margin").is_not_null()
            & pl.col("r20_pts_for").is_not_null()
            & pl.col("opp_r20_pts_for").is_not_null()
        )
        proxy_total = (
            d["r20_pts_for"].to_numpy() + d["opp_r20_pts_for"].to_numpy()
        )  # expected total from the two 20-game scoring averages
        out[str(s)] = {
            "n": d.height,
            "corr_mkt_margin_exp_margin": float(
                np.corrcoef(d["mkt_margin"].to_numpy(), d["exp_margin"].to_numpy())[0, 1]
            ),
            "corr_mkt_total_r20_for_plus_opp_r20_for": float(
                np.corrcoef(d["mkt_total"].to_numpy(), proxy_total)[0, 1]
            ),
            "corr_mkt_abs_abs_margin": float(
                np.corrcoef(d["mkt_abs_margin"].to_numpy(), d["abs_margin"].to_numpy())[0, 1]
            ),
        }
    return out


def training_sufficiency(arms_a0: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"mature_games": MATURE_GAMES}
    meta = arms_a0["pts"]["meta"]
    for s in AUDIT_SEASONS:
        m = meta.filter(pl.col("season") == s)
        blocks = (
            m.with_columns(pl.col("game_date").dt.strftime("%Y-%m").alias("ym"))
            .group_by("ym")
            .agg(pl.len().alias("rows"), pl.col("train_mkt_games").first())
            .sort("ym")
        )
        mature = float((m["train_mkt_games"] >= MATURE_GAMES).mean())
        out[str(s)] = {
            "blocks": [dict(zip(blocks.columns, r, strict=True)) for r in blocks.iter_rows()],
            "share_rows_in_mature_blocks": mature,
        }
    return out


def ou_inputs(stat: str, a: dict[str, Any], pairs: pl.DataFrame) -> dict[str, Any]:
    """P(over) at the DraftKings t60 line for the rows of arm ``a`` (aligned to the pair rows)."""
    meta = (
        a["meta"].select(KEYS).with_row_index("_i").with_columns(pl.col("player_id").cast(pl.Int64))
    )
    pr = pairs.filter(
        (pl.col("stat") == stat)
        & (pl.col("book") == "draftkings")
        & (pl.col("snapshot_kind") == "t60")
    ).select("game_id", "player_id", "line", "p_model", "p_over_mult", "season")
    j = (
        pr.with_columns(pl.col("player_id").cast(pl.Int64))
        .join(meta, on=KEYS, how="inner")
        .sort(["game_id", "player_id"])
    )
    idx = j["_i"].to_numpy()
    kmax = max(PGE_KMAX[stat], int(a["qi"].max()))
    pge = pge_matrix(a["qi"][idx], kmax)
    p_over, over, status = model_p_over(pge, j["line"].to_numpy(), a["y"][idx])
    return {
        "game_id": j["game_id"].to_numpy(),
        "player_id": j["player_id"].to_numpy(),
        "season": j["season"].to_numpy(),
        "p": p_over,
        "over": over,
        "status": status,
        "p_stored": j["p_model"].to_numpy(),
        "p_mkt": j["p_over_mult"].to_numpy(),
        "n_pairs": pr.height,
    }


def load_pairs() -> pl.DataFrame:
    parts = []
    for s in AUDIT_SEASONS:
        assert s <= MAX_SEASON
        parts.append(pl.read_parquet(PAIRS_DIR / f"pairs_{s}.parquet"))
    return pl.concat(parts, how="diagonal_relaxed")


def run_gate(
    db_path: str, doc: Path, sha: str, allow_draft: bool, out_dir: Path = OUT_DIR
) -> dict[str, Any]:
    t0 = time.time()
    inp = load_study(db_path)
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    games, pgs, base, gm, tips = inp["games"], inp["pgs"], inp["base"], inp["gm"], inp["tips"]
    tests: list[dict[str, Any]] = []
    t2, cov = check_2_coverage(games, gm, base)
    tests += t2
    t3, ka = check_3_known_at(gm, inp["gm_counts"], inp["odds_rows"], tips)
    tests += t3
    gap_all, gap_played, audit = check_4_missingness(pgs, games, gm)
    tests.append(
        _chk(
            "4 max NULL-market-rate gap across realised-minutes buckets incl. DNP (pp)",
            gap_all,
            f"<= {NULL_GAP_PP}",
            gap_all <= NULL_GAP_PP,
        )
    )
    t5, planted = planted_tests(inp)
    tests += t5
    k6 = check_6_keys(base)
    tests.append(
        _chk(
            "6 identical row keys and y across the six arm frames", int(k6["ok"]), "== 1", k6["ok"]
        )
    )
    desc = descriptive_corr(base)
    print(f"checks 2-6 done {time.time() - t0:.0f}s", flush=True)
    arms: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        arms[stat] = collect_cached(base, stat, "A0", inp["cfg"])
    repro = check_a0_repro(arms)
    tests.append(
        _chk(
            "1 A0 reproduces production integer CRPS and n to 1e-6 (4 stats x 2 seasons)",
            int(repro["ok"]),
            "== 1",
            repro["ok"],
        )
    )
    names_ok = all(arm_names(s, "A0") == stat_feature_names(s) for s in STATS)
    tests.append(
        _chk("1 A0 names == production stat_feature_names", int(names_ok), "== 1", names_ok)
    )
    suff = training_sufficiency(arms)
    pairs = load_pairs()
    ou: dict[str, Any] = {}
    for stat in STATS:
        o = ou_inputs(stat, arms[stat], pairs)
        ok0 = o["status"] == 0
        ou[stat] = {
            "pairs_rows": int(o["n_pairs"]),
            "matched": int(len(o["p"])),
            "status0": int(ok0.sum()),
            "max_abs_diff_vs_stored_p_model": float(
                np.max(np.abs(o["p"][ok0] - o["p_stored"][ok0]))
            ),
            "share_within_1e-6": float(np.mean(np.abs(o["p"][ok0] - o["p_stored"][ok0]) <= 1e-6)),
            "share_within_1e-6_by_season": {
                str(se): float(
                    np.mean(
                        np.abs(
                            o["p"][ok0 & (o["season"] == se)]
                            - o["p_stored"][ok0 & (o["season"] == se)]
                        )
                        <= 1e-6
                    )
                )
                for se in AUDIT_SEASONS
            },
        }
    res: dict[str, Any] = {
        "status": "GATE_PASSED" if all(t["ok"] for t in tests) else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "doc": str(doc),
        "sha256": sha,
        "git_sha": RUN_GIT_SHA,
        "seeds": SEEDS,
        "coverage": cov,
        "known_at": ka,
        "missingness_audit": audit,
        "missingness_gap_pp": {"all": gap_all, "played_only": gap_played},
        "planted": planted,
        "keys": k6,
        "descriptive_corr": desc,
        "training_sufficiency": suff,
        "ou_reproduction_info": ou,
        "a0_repro": repro,
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "max_season_loaded": int(base["season"].max()),
        "unstated_details": UNSTATED,
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1, default=_jd, sort_keys=True))
    write_gate_ledger(out_dir)
    return res


# --------------------------------------------------------------------------- scoring


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    dd = add_slice_columns(d)
    has = d["mkt_total"].is_not_null().to_numpy()
    masks: dict[str, np.ndarray] = {
        "all": np.ones(d.height, dtype=bool),
        "market_rows": has,
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "mature_blocks": (d["train_mkt_games"] >= MATURE_GAMES).to_numpy(),
        "pinnacle": (d["mkt_src"] == "pinnacle").fill_null(False).to_numpy(),
        "consensus": (d["mkt_src"] == "consensus").fill_null(False).to_numpy(),
    }
    for col, label in (("mkt_abs_margin", "spread"), ("mkt_total", "total")):
        v = d[col].to_numpy().astype(float)
        if has.sum() >= 30:
            lo, hi = np.nanquantile(v[has], [1 / 3, 2 / 3])
            masks[f"{label}_low"] = has & (v <= lo)
            masks[f"{label}_mid"] = has & (v > lo) & (v <= hi)
            masks[f"{label}_high"] = has & (v > hi)
    return masks


GUARD_SLICES = ("starter", "bench", "first15")
ARM_SET = ("A1", "A2", "A3", "P1a", "P1b")


def score_stat(stat: str, arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ref = arms["A0"]["meta"]
    for a, v in arms.items():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
            f"{a}: arms must score identical rows"
        )
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    contrasts: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: row_scores(v["qi"][sm], v["y"][sm], stat) for a, v in arms.items()}
        masks = slice_masks(d)
        mk = masks["market_rows"]
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
                dm = _ci((s["crps"] - sc["A0"]["crps"])[mk], gid[mk], N_BOOT)
                r["restricted"] = {
                    "n": int(mk.sum()),
                    "dcrps": dm["point"],
                    "ci_lo": dm["lo"],
                    "ci_hi": dm["hi"],
                }
                mt = masks["mature_blocks"]
                ms = _delta_stats((s["crps"] - sc["A0"]["crps"])[mt], gid[mt])
                r["mature"] = {"n": int(mt.sum()), "p_bh": None, **ms}
            rows.append(r)
        for a in ARM_SET:
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
                    ds = _ci(da[m], gid[m], N_BOOT)
                    rec.update({"dcrps": ds["point"], "ci_lo": ds["lo"], "ci_hi": ds["hi"]})
                sl.append(rec)
        for a, p in (("A1", "P1a"), ("A2", "P1b")):
            dd_ = sc[a]["crps"] - sc[p]["crps"]
            c = _ci(dd_, gid, N_BOOT)
            cr = _ci(dd_[mk], gid[mk], N_BOOT)
            contrasts.append(
                {
                    "stat": stat,
                    "season": season,
                    "arm": a,
                    "vs": p,
                    "dcrps": c["point"],
                    "ci_lo": c["lo"],
                    "ci_hi": c["hi"],
                    "restricted_dcrps": cr["point"],
                    "restricted_ci_lo": cr["lo"],
                    "restricted_ci_hi": cr["hi"],
                }
            )
    return {"rows": rows, "slices": sl, "contrasts": contrasts}


def ou_scores(
    stat: str, arms: dict[str, dict[str, Any]], pairs: pl.DataFrame
) -> list[dict[str, Any]]:
    o = {a: ou_inputs(stat, v, pairs) for a, v in arms.items()}
    ref = o["A0"]
    for v in o.values():
        assert np.array_equal(v["game_id"], ref["game_id"]) and np.array_equal(
            v["player_id"], ref["player_id"]
        )
    ok = np.ones(len(ref["p"]), dtype=bool)
    for v in o.values():
        ok &= v["status"] == 0
    out: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        m = ok & (ref["season"] == season)
        gid = ref["game_id"][m]
        ll = {a: ll_vec(v["p"][m], v["over"][m], MODEL_EPS) for a, v in o.items()}
        mk = ll_vec(ref["p_mkt"][m], ref["over"][m], MARKET_EPS)
        for a in arms:
            rec: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": a,
                "n": int(m.sum()),
                "n_games": int(len(np.unique(gid))),
                "logloss": float(ll[a].mean()),
                "market_logloss": float(mk.mean()),
            }
            if a != "A0":
                c = _ci(ll[a] - ll["A0"], gid, N_BOOT)
                rec.update({"dll": c["point"], "ci_lo": c["lo"], "ci_hi": c["hi"]})
            out.append(rec)
    return out


def add_bh(rows: list[dict[str, Any]]) -> None:
    for season in TEST_SEASONS:
        tgt = [r for r in rows if r["season"] == season and r["arm"] in ("A1", "A2")]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)
        # AM1: the same BH family, p-values computed on the mature-block rows
        adj_m = bh_adjust(np.array([r["mature"]["p"] for r in tgt]))
        for r, a in zip(tgt, adj_m, strict=True):
            r["mature"]["p_bh"] = float(a)


def r1_passes(row: dict[str, Any], season: int) -> bool:
    """R1 for one cell-season. AM1: 2023 is judged on the mature-block rows (>= MATURE_GAMES
    market-bearing training games), BH over the same family of 8 with mature-row p-values;
    2024 on all scored rows (every 2024 block is mature)."""
    x = row["mature"] if season == 2023 else row
    p_bh = x["p_bh"]
    return bool(x["dcrps"] <= FLOOR and x["ci_hi"] < 0.0 and p_bh is not None and p_bh < 0.05)


def evaluate_rules(res: dict[str, Any], tests_ok: bool) -> dict[str, Any]:
    rows, sl, con, ou = res["rows"], res["slices"], res["contrasts"], res["ou"]
    tab = {(r["stat"], r["season"], r["arm"]): r for r in rows}
    ctab = {(c["stat"], c["season"], c["arm"]): c for c in con}
    otab = {(o["stat"], o["season"], o["arm"]): o for o in ou}
    out: dict[str, Any] = {}
    for arm in ("A1", "A2"):
        for stat in STATS:
            r1 = {str(se): r1_passes(tab[(stat, se, arm)], se) for se in TEST_SEASONS}
            r2 = {str(se): bool(ctab[(stat, se, arm)]["ci_hi"] < 0.0) for se in TEST_SEASONS}
            r4 = {str(se): bool(otab[(stat, se, arm)]["dll"] <= LL_NONINF) for se in TEST_SEASONS}
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
                "no_slice_worse_than_+0.01": not bad,
            }
            out[f"{arm}_{stat}"] = {
                "rule1_crps_by_season": r1,
                "rule2_beats_placebo_by_season": r2,
                "rule3_guards": guards,
                "rule4_ou_noninferior_by_season": r4,
                "slices_worse": bad,
                "pass": bool(
                    all(r1.values())
                    and all(r2.values())
                    and all(guards.values())
                    and all(r4.values())
                    and tests_ok
                ),
                "pass_2023_crps": bool(r1["2023"]),
            }
    out["close_no_cell_passes_2023"] = not any(
        v["pass_2023_crps"] for k, v in out.items() if isinstance(v, dict) and "pass_2023_crps" in v
    )
    return out


def run_arms(db_path: str, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    path = out_dir / "results.json"
    gate = json.loads((out_dir / "gate.json").read_text())
    if gate.get("draft") or not gate.get("gate_ok") or gate.get("sha256") != sha:
        raise RuntimeError("no passing gate recorded for this frozen sha: no arm may be fitted")
    t0 = time.time()
    inp = load_study(db_path)
    frames = arm_frames(inp["base"])
    arms: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STATS}
    for stat in STATS:
        for arm in ARMS:
            arms[stat][arm] = collect_cached(frames[arm], stat, arm, inp["cfg"])
            print(f"[{time.time() - t0:.0f}s] {stat} {arm} ready", flush=True)
    repro = check_a0_repro({s: arms[s]["A0"] for s in STATS})
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    pairs = load_pairs()
    tables: dict[str, list[dict[str, Any]]] = {"rows": [], "slices": [], "contrasts": [], "ou": []}
    for stat in STATS:
        t = score_stat(stat, arms[stat])
        for k in ("rows", "slices", "contrasts"):
            tables[k] += t[k]
        tables["ou"] += ou_scores(stat, arms[stat], pairs)
        print(f"[{time.time() - t0:.0f}s] {stat} scored", flush=True)
    add_bh(tables["rows"])
    rules = evaluate_rules(tables, True)
    res = {
        **tables,
        "rules": rules,
        "seeds": SEEDS,
        "git_sha": RUN_GIT_SHA,
        "sha256": sha,
        "n_boot": N_BOOT,
        "a0_repro": repro,
        "arms_seconds": time.time() - t0,
    }
    path.write_text(json.dumps(res, indent=1, default=_jd))
    return res


def run_smoke(db_path: str) -> None:
    t0 = time.time()
    inp = load_study(db_path)
    for arm in ("A0", "A1"):
        o = collect_arm(
            inp["base"], "pts", inp["cfg"], arm_names("pts", arm), (2023,), max_blocks=1
        )
        crps = float(crps_from_quantiles(o["qi"].astype(float), o["y"]).mean())
        print(
            f"{arm} one-block crps_int {crps:.4f} n={len(o['y'])} [{time.time() - t0:.0f}s]",
            flush=True,
        )


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    L = [
        f"# GAME_MARKET_CONTEXT results (sha256 `{r['sha256']}`, git `{r['git_sha']}`)",
        "",
        "| stat | season | arm | n | games | CRPS | dCRPS vs A0 | CI | p_bh | restricted (market rows) dCRPS [CI] | mature-block rows dCRPS [CI] p_bh (R1 basis 2023) | bias | cov80 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in r["rows"]:
        rs = x.get("restricted")
        rtxt = (
            f"{_f(rs['dcrps'], 4, True)} [{_f(rs['ci_lo'], 4, True)}, {_f(rs['ci_hi'], 4, True)}] n {rs['n']}"
            if rs
            else "n/a"
        )
        mt = x.get("mature")
        mtxt = (
            f"{_f(mt['dcrps'], 4, True)} [{_f(mt['ci_lo'], 4, True)}, {_f(mt['ci_hi'], 4, True)}] p_bh {_f(mt['p_bh'], 3)} n {mt['n']}"
            if mt
            else "n/a"
        )
        L.append(
            f"| {x['stat']} | {x['season']} | {x['arm']} | {x['n_rows']} | {x['n_games']} | {_f(x['crps_int'])} | {_f(x['dcrps'], 4, True)} | [{_f(x['ci_lo'], 4, True)}, {_f(x['ci_hi'], 4, True)}] | {_f(x['p_bh'], 3)} | {rtxt} | {mtxt} | {_f(x['bias'], 3, True)} | {_f(x['cov80'], 3)} |"
        )
    L += [
        "",
        "## Real vs placebo (A1 - P1a, A2 - P1b)",
        "",
        "| stat | season | arm | vs | dCRPS | CI |",
        "|---|---|---|---|---|---|",
    ]
    for c in r["contrasts"]:
        L.append(
            f"| {c['stat']} | {c['season']} | {c['arm']} | {c['vs']} | {_f(c['dcrps'], 4, True)} | [{_f(c['ci_lo'], 4, True)}, {_f(c['ci_hi'], 4, True)}] |"
        )
    L += [
        "",
        "## Over/under log loss at DraftKings t60 lines (arm - A0)",
        "",
        "| stat | season | arm | n | games | logloss | market | dLL | CI |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for o in r["ou"]:
        L.append(
            f"| {o['stat']} | {o['season']} | {o['arm']} | {o['n']} | {o['n_games']} | {_f(o['logloss'])} | {_f(o['market_logloss'])} | {_f(o.get('dll'), 4, True)} | [{_f(o.get('ci_lo'), 4, True)}, {_f(o.get('ci_hi'), 4, True)}] |"
        )
    L += [
        "",
        "## Slices (arm - A0)",
        "",
        "| stat | season | arm | slice | n | dCRPS | CI |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in r["slices"]:
        if x["arm"] in ("A1", "A2"):
            L.append(
                f"| {x['stat']} | {x['season']} | {x['arm']} | {x['slice']} | {x['n']} | {_f(x.get('dcrps'), 4, True)} | [{_f(x.get('ci_lo'), 4, True)}, {_f(x.get('ci_hi'), 4, True)}] |"
            )
    L += ["", "## Rules", "", "```", json.dumps(r["rules"], indent=1), "```", ""]
    return "\n".join(L)


def gate_ledger_lines(out_dir: Path = OUT_DIR) -> list[str]:
    """Ledger rows (T### placeholders) for the gate measurement in ``gate.json``."""
    g = json.loads((out_dir / "gate.json").read_text())
    n_ok = sum(1 for t in g["tests"] if t["ok"])
    cov = g["coverage"]
    mp = g["missingness_gap_pp"]
    rep = g["a0_repro"]["detail"]
    dmax = max(abs(v["delta"]) for v in rep.values())
    return [
        f"| T### | GAME_MARKET_CONTEXT gate (draft sha {g['sha256'][:8]}) | {n_ok}/{len(g['tests'])} checks pass; A0 max |delta| {dmax:.1e} (4 stats x 2 seasons, n 27,619 / 27,701); market coverage 2023 {cov['2023']['coverage_regular']:.4f}, 2024 {cov['2024']['coverage_regular']:.4f}; NULL-market gap by minutes bucket {mp['all']:.2f} pp; used snapshots later than tip-55: {g['known_at']['snapshot_rows']['rows_refused_late']} refused, min lead {g['known_at']['min_minutes_before_tip']:.1f} min; mature-block row share 2023 {g['training_sufficiency']['2023']['share_rows_in_mature_blocks']:.3f} |",
    ]


def write_gate_ledger(out_dir: Path = OUT_DIR) -> Path:
    p = out_dir / "ledger_rows.md"
    p.write_text(
        "\n".join(["| id | test | result |", "|---|---|---|", *gate_ledger_lines(out_dir)]) + "\n"
    )
    return p


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    tab = {(x["stat"], x["season"], x["arm"]): x for x in r["rows"]}
    ctab = {(c["stat"], c["season"], c["arm"]): c for c in r["contrasts"]}
    otab = {(o["stat"], o["season"], o["arm"]): o for o in r["ou"]}
    body = ["| id | test | result |", "|---|---|---|", *gate_ledger_lines(out_dir)]
    for arm in ("A1", "A2"):
        for stat in STATS:
            for se in TEST_SEASONS:
                x, c, o = tab[(stat, se, arm)], ctab[(stat, se, arm)], otab[(stat, se, arm)]
                body.append(
                    f"| T### | GAME_MARKET_CONTEXT {stat} {se} {arm}-A0 (n {x['n_rows']}, {x['n_games']} games) | "
                    f"dCRPS {_f(x['dcrps'], 4, True)} CI [{_f(x['ci_lo'], 4, True)}, {_f(x['ci_hi'], 4, True)}] p_bh {_f(x['p_bh'], 3)}; "
                    f"vs placebo {_f(c['dcrps'], 4, True)} [{_f(c['ci_lo'], 4, True)}, {_f(c['ci_hi'], 4, True)}]; "
                    f"DK O/U dLL {_f(o['dll'], 4, True)} [{_f(o['ci_lo'], 4, True)}, {_f(o['ci_hi'], 4, True)}] (n {o['n']}) |"
                )
    for k, v in r["rules"].items():
        if isinstance(v, dict) and "pass" in v:
            body.append(
                f"| T### | GAME_MARKET_CONTEXT verdict {k} | pass {v['pass']}; crps {v['rule1_crps_by_season']}; placebo {v['rule2_beats_placebo_by_season']}; guards {v['rule3_guards']}; O/U {v['rule4_ou_noninferior_by_season']} |"
            )
    body.append(
        f"| T### | GAME_MARKET_CONTEXT close (no cell passes 2023) | {r['rules']['close_no_cell_passes_2023']} |"
    )
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--db", default="nba.duckdb")
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.doc, a.sha, a.allow_draft)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    with HeavyLock():
        if a.stage == "gate":
            res = run_gate(a.db, a.doc, sha, a.allow_draft)
            print(
                json.dumps(
                    {k: res[k] for k in ("sha256", "tests", "gate_ok")}, indent=1, default=_jd
                )
            )
            return 0 if res["gate_ok"] else 1
        if a.stage == "smoke":
            run_smoke(a.db)
        elif a.stage == "run":
            g = run_gate(a.db, a.doc, sha, False)
            if not g["gate_ok"]:
                print("GATE FAILED: stop, no model result", flush=True)
                return 2
            res = run_arms(a.db, sha)
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
