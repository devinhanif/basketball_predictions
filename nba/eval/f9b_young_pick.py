"""F9b young top-10 pick prior (docs/prereg/F9b_YOUNG_PICK_PRIOR.md, frozen).

    .venv/bin/python -m nba.eval.f9b_young_pick checks   # minimum data checks 1-4 (light)
    .venv/bin/python -m nba.eval.f9b_young_pick repro    # check 5: A0 reproduces LOWER_TAIL pts
    .venv/bin/python -m nba.eval.f9b_young_pick arms     # A0-A3, 4 stats (heavy; holds heavy.lock)
    .venv/bin/python -m nba.eval.f9b_young_pick report   # reports/prereg_f9b.md from the JSON

Research module only: the live path (``nba.props``) is untouched. The eight ``yp_*`` columns are
computed here and joined on the feature frame; arms select them through an explicit ``names`` list
(``stat_feature_names(stat) + columns``), never through the ``young_pick`` flag. Read-only on
``nba.duckdb`` (``read_only=True``), season <= 2024 only (asserted); season 2025 is the frozen
holdout and is never loaded. A failed minimum-data check stops the study with no model result.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
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
from nba.eval.lower_tail_eval import EPS, LOW_THRESHOLDS, attach_realised, randomized_pit
from nba.props.context_residual import (
    CRPS_TAUS,
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _sel,
    build_features,
    flagged_from_availability,
    player_states,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from nba.props.metrics import paired_score_delta_ci

ROOT = Path(__file__).resolve().parents[2]
DOC = Path("docs/prereg/F9b_YOUNG_PICK_PRIOR.md")
OUT_DIR = Path("reports/prereg_f9b")
REPORT_MD = Path("reports/prereg_f9b.md")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
HEAVY_LOCK = ROOT / "data" / "ops" / "heavy.lock"
FROZEN_PREFIX = "884afcdb"
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
N_BOOT = 2000

# --- frozen constants
UNDRAFTED_ORD = 61.0
TOP_PICK = 10
DECAY: dict[int, float] = {1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25}
SLICE_MAX_SEASONS = 2
SLICE_MIN10 = 20.0
VET_MIN_SEASONS = 5
T10_MIN_GAMES = 5
MIN_STATUS_KNOWN = 0.99
MIN_FIRST_SEASON_KNOWN = 0.98
MAX_MISSING_GAP_PP = 2.0
MIN_S_ROWS, MIN_S_PLAYERS, MIN_S_GAMES, MIN_VET_ROWS = 600, 12, 300, 3000
MIN_GATE_SLICE_N = 300

YP_NEW: tuple[str, ...] = (
    "yp_pick_ord",
    "yp_undrafted",
    "yp_seasons",
    "yp_w",
    "yp_min_share",
    "yp_min_trend",
    "yp_pts_share",
    "yp_w_x_role",
)
#: A3 (role-only) arm: A1 without the pedigree columns.
YP_ROLE_ONLY: tuple[str, ...] = ("yp_seasons", "yp_min_share", "yp_min_trend", "yp_pts_share")
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3")


def arm_names(stat: str, arm: str) -> list[str]:
    """Explicit feature list for ``arm`` (A0 = production list; the rest append yp columns)."""
    extra = {"A0": (), "A1": YP_NEW, "A2": YP_NEW, "A3": YP_ROLE_ONLY}[arm]
    return stat_feature_names(stat, extra=extra)


def frozen_sha256(doc: Path = DOC) -> str:
    """sha256 of the doc text above the ``=== RESULTS BELOW ===`` line (the freeze evidence)."""
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


# --------------------------------------------------------------------------- static columns


def load_static_extra(db_path: str) -> pl.DataFrame:
    """players_static columns F9b needs (read-only). NULL ``draft_status`` = not re-pulled."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT player_id, draft_year, draft_pick, first_season, draft_status "
            "FROM players_static"
        ).pl()
    finally:
        con.close()


def player_yp_static(static_x: pl.DataFrame, first_loaded: pl.DataFrame) -> pl.DataFrame:
    """Player-level static columns (known at the draft / debut; no game stats).

    ``yp_status``: drafted = ``draft_pick`` non-null (or a draft_year / 'drafted' status with no
    pick -> pick NaN); undrafted = pick null AND ``draft_status == 'undrafted'`` (positive evidence
    from the nba_api commonplayerinfo field); otherwise unknown (incl. NULL status).
    ``first_season``: FROM_YEAR, else ``draft_year`` (``fs_fallback``), else the first loaded game
    season (``fs_truncated``). ``first_loaded``: player_id, first_loaded (min played season)."""
    s = static_x.select("player_id", "draft_year", "draft_pick", "first_season", "draft_status")
    s = s.unique("player_id").join(first_loaded, on="player_id", how="full", coalesce=True)
    has_pick = pl.col("draft_pick").is_not_null()
    undrafted = (~has_pick) & (pl.col("draft_status") == "undrafted")
    drafted = has_pick | pl.col("draft_year").is_not_null() | (pl.col("draft_status") == "drafted")
    status = (
        pl.when(has_pick)
        .then(pl.lit("drafted"))
        .when(undrafted)
        .then(pl.lit("undrafted"))
        .when(drafted)
        .then(pl.lit("drafted"))
        .otherwise(pl.lit("unknown"))
    )
    fs = (
        pl.when(pl.col("first_season").is_not_null())
        .then(pl.col("first_season"))
        .when(pl.col("draft_year").is_not_null())
        .then(pl.col("draft_year"))
        .otherwise(pl.col("first_loaded"))
        .cast(pl.Float64)
    )
    return s.with_columns(status.alias("yp_status")).select(
        "player_id",
        "yp_status",
        pl.when(pl.col("yp_status") == "undrafted")
        .then(pl.lit(UNDRAFTED_ORD))
        .when(has_pick)
        .then(pl.col("draft_pick").cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("yp_pick_ord"),
        pl.when(pl.col("yp_status") == "unknown")
        .then(pl.lit(None, dtype=pl.Float64))
        .when(pl.col("yp_status") == "undrafted")
        .then(pl.lit(1.0))
        .otherwise(pl.lit(0.0))
        .alias("yp_undrafted"),
        fs.alias("first_season_used"),
        (pl.col("first_season").is_null() & pl.col("draft_year").is_not_null())
        .cast(pl.Int8)
        .alias("fs_fallback"),
        (pl.col("first_season").is_null() & pl.col("draft_year").is_null())
        .cast(pl.Int8)
        .alias("fs_truncated"),
        pl.col("first_season").cast(pl.Float64).alias("first_season_raw"),
        pl.col("draft_year").cast(pl.Float64).alias("draft_year"),
    )


def yp_weight(seasons: np.ndarray, pick_ord: np.ndarray) -> np.ndarray:
    """``{1: 1, 2: .75, 3: .5, 4: .25, >=5: 0} * 1[pick_ord <= 10]`` (undrafted=61, NaN -> 0)."""
    w = np.zeros(len(seasons))
    for k, v in DECAY.items():
        w[seasons == k] = v
    return np.asarray(w * (np.nan_to_num(pick_ord, nan=1e9) <= TOP_PICK))


def _played(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    return (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )


def add_yp_columns(
    feats: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static_x: pl.DataFrame,
) -> pl.DataFrame:
    """Join the eight ``yp_*`` columns (plus ``yp_status``, ``fs_fallback``, ``fs_truncated``,
    ``first_season_raw``, ``draft_year``) onto the production feature frame. All as-of the game
    date: the draft/debut fields are static, the role columns use only strictly prior games."""
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = _played(games, pgs)
    first_loaded = played.group_by("player_id").agg(pl.col("season").min().alias("first_loaded"))
    ps = player_yp_static(static_x, first_loaded)
    min100 = pl.DataFrame(
        {
            "game_id": played["game_id"],
            "player_id": played["player_id"],
            "_min100": _sel(player_states(played)["pre"], 100.0, "min"),
        }
    )
    g = games.select("game_id", "game_date", "home_team", "away_team", "home_pts", "away_pts")
    tg = pl.concat(
        [
            g.select(
                "game_id",
                "game_date",
                pl.col("home_team").alias("team_id"),
                pl.col("home_pts").alias("pf"),
            ),
            g.select(
                "game_id",
                "game_date",
                pl.col("away_team").alias("team_id"),
                pl.col("away_pts").alias("pf"),
            ),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    tg = tg.with_columns(
        pl.col("pf")
        .shift(1)
        .rolling_mean(10, min_samples=T10_MIN_GAMES)
        .over("team_id")
        .alias("_t10")
    ).select("game_id", "team_id", "_t10")
    base = (
        feats.select("game_id", "player_id", "team_id", "season", "min5", "min10", "m10_pts")
        .join(ps, on="player_id", how="left")
        .join(min100, on=["game_id", "player_id"], how="left")
        .join(tg, on=["game_id", "team_id"], how="left")
    )
    seasons = (base["season"].cast(pl.Float64) - base["first_season_used"]).to_numpy() + 1.0
    pick = base["yp_pick_ord"].to_numpy()
    w = yp_weight(seasons, pick)
    share = base["min10"].to_numpy() / 240.0
    add = base.select("game_id", "player_id").with_columns(
        pl.Series("yp_pick_ord", pick),
        base["yp_undrafted"],
        pl.Series("yp_seasons", seasons),
        pl.Series("yp_w", w),
        pl.Series("yp_min_share", share),
        pl.Series("yp_min_trend", (base["min5"].to_numpy() - base["_min100"].to_numpy()) / 48.0),
        pl.Series("yp_pts_share", base["m10_pts"].to_numpy() / base["_t10"].to_numpy()),
        pl.Series("yp_w_x_role", w * share),
        base["yp_status"],
        base["fs_fallback"],
        base["fs_truncated"],
        base["first_season_raw"],
        base["draft_year"],
    )
    return feats.join(add, on=["game_id", "player_id"], how="left")


def placebo_frame(feats: pl.DataFrame, seed: int = SEEDS["placebo"]) -> pl.DataFrame:
    """A2 placebo: permute ``yp_pick_ord`` / ``yp_undrafted`` across players within
    (season, min(yp_seasons, 5)); recompute ``yp_w`` / ``yp_w_x_role`` from the permuted pick.
    Role and decay shape are kept (``yp_seasons`` and the role columns are untouched)."""
    pl_players = (
        feats.select(
            "player_id",
            "season",
            "yp_seasons",
            "yp_pick_ord",
            "yp_undrafted",
        )
        .group_by(["player_id", "season"])
        .agg(
            pl.col("yp_seasons").first(),
            pl.col("yp_pick_ord").first(),
            pl.col("yp_undrafted").first(),
        )
        .with_columns(pl.col("yp_seasons").clip(upper_bound=5).alias("_grp"))
        .sort(["season", "_grp", "player_id"])
    )
    rng = np.random.default_rng(seed)
    pick = pl_players["yp_pick_ord"].to_numpy().copy()
    und = pl_players["yp_undrafted"].to_numpy().copy()
    key = list(zip(pl_players["season"].to_list(), pl_players["_grp"].to_list(), strict=True))
    start = 0
    for i in range(1, len(key) + 1):
        if i == len(key) or key[i] != key[start]:
            perm = rng.permutation(np.arange(start, i))
            pick[start:i] = pick[perm]
            und[start:i] = und[perm]
            start = i
    new = pl_players.select("player_id", "season").with_columns(
        pl.Series("_pick_p", pick), pl.Series("_und_p", und)
    )
    out = feats.join(new, on=["player_id", "season"], how="left")
    w = yp_weight(out["yp_seasons"].to_numpy(), out["_pick_p"].to_numpy())
    return out.with_columns(
        pl.col("_pick_p").alias("yp_pick_ord"),
        pl.col("_und_p").alias("yp_undrafted"),
        pl.Series("yp_w", w),
        pl.Series("yp_w_x_role", w * out["yp_min_share"].to_numpy()),
    ).drop("_pick_p", "_und_p")


# --------------------------------------------------------------------------- slices


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    """Boolean masks over the rows of ``d`` (needs yp_* columns, ``add_slice_columns`` inputs)."""
    seas = d["yp_seasons"]
    pick = d["yp_pick_ord"]
    trunc = d["fs_truncated"] == 1
    s = (seas <= SLICE_MAX_SEASONS) & (pick <= TOP_PICK) & (d["min10"] >= SLICE_MIN10) & ~trunc
    s = s.fill_null(False)
    young = (seas <= SLICE_MAX_SEASONS).fill_null(False)
    dd = add_slice_columns(d)
    return {
        "all": np.ones(d.height, dtype=bool),
        "S": s.to_numpy(),
        "other_young": (young & ~s).to_numpy(),
        "veterans": ((seas >= VET_MIN_SEASONS) & ~trunc).fill_null(False).to_numpy(),
        "top10_seasons_3_4": ((seas >= 3) & (seas <= 4) & (pick <= TOP_PICK))
        .fill_null(False)
        .to_numpy(),
        "n_prior<20": (d["n_prior"] < 20).to_numpy(),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "teammate_out": (dd["sl_tm_out"] == "teammate_out").to_numpy(),
    }


GATE4_SLICES: tuple[str, ...] = ("n_prior<20", "starter", "bench", "first15", "teammate_out")
COMPLEMENT_SLICES: tuple[str, ...] = ("other_young", "veterans")
REPORT_SLICES: tuple[str, ...] = (
    "all",
    "S",
    *COMPLEMENT_SLICES,
    "top10_seasons_3_4",
    *GATE4_SLICES,
)


def study_rows(feats: pl.DataFrame, seasons: tuple[int, ...] = TEST_SEASONS) -> pl.DataFrame:
    """Rows of the study: played, ``n_prior >= 5``, test seasons (production row filter)."""
    return feats.filter(
        (pl.col("n_prior") >= MIN_PRIOR_PLAYED) & pl.col("season").is_in(list(seasons))
    )


# --------------------------------------------------------------------------- checks 1-3


def players_ge20(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    return (
        _played(games, pgs)
        .group_by("player_id")
        .agg(pl.len().alias("n"), pl.col("minutes").sum().alias("mins"))
        .filter(pl.col("n") >= 20)
    )


def check_1_2(
    games: pl.DataFrame, pgs: pl.DataFrame, static_x: pl.DataFrame, rows: pl.DataFrame
) -> dict[str, Any]:
    """Checks 1 and 2 plus the missingness audit and composition reports."""
    played = _played(games, pgs)
    first_loaded = played.group_by("player_id").agg(pl.col("season").min().alias("first_loaded"))
    ps = player_yp_static(static_x, first_loaded)
    pl20 = players_ge20(games, pgs).join(ps, on="player_id", how="left")
    n = pl20.height
    known = pl20["yp_status"].fill_null("unknown") != "unknown"
    fs_known = pl20["fs_truncated"].fill_null(1) == 0
    unk = (rows["yp_status"] == "unknown").fill_null(True)
    short = rows["minutes"] < 10
    nan_rate = {
        "played<10": float(unk.filter(short).mean()),  # type: ignore[arg-type]
        "rest": float(unk.filter(~short).mean()),  # type: ignore[arg-type]
    }
    gap = abs(nan_rate["played<10"] - nan_rate["rest"]) * 100.0
    nan_pick = rows["yp_pick_ord"].is_null() | rows["yp_pick_ord"].is_nan()
    nan_pick_rate = {
        "played<10": float(nan_pick.filter(short).mean()),  # type: ignore[arg-type]
        "rest": float(nan_pick.filter(~short).mean()),  # type: ignore[arg-type]
    }
    buckets = {
        "<10": rows["minutes"] < 10,
        "10-20": (rows["minutes"] >= 10) & (rows["minutes"] <= 20),
        ">20": rows["minutes"] > 20,
    }
    und_share = {
        k: float(rows.filter(m)["yp_undrafted"].drop_nulls().mean())  # type: ignore[arg-type]
        for k, m in buckets.items()
    }
    return {
        "n_players_ge20": n,
        "status_counts": {
            str(k): int(v) for k, v in pl20.group_by("yp_status").len().iter_rows() if k is not None
        },
        "status_known_share": float(known.mean()),  # type: ignore[arg-type]
        "n_status_unknown": int((~known).sum()),
        "first_season_known_share": float(fs_known.mean()),  # type: ignore[arg-type]
        "fs_fallback_share": float((pl20["fs_fallback"] == 1).mean()),  # type: ignore[arg-type]
        "fs_truncated_share": float((pl20["fs_truncated"] == 1).mean()),  # type: ignore[arg-type]
        "n_fs_from_first_season": int(pl20["first_season_raw"].is_not_null().sum()),
        "unknown_rate_by_minutes": nan_rate,
        "gap_pp": gap,
        "nan_yp_pick_ord_rate_by_minutes": nan_pick_rate,
        "undrafted_share_by_minutes_bucket": und_share,
        "unknown_players_top": (
            pl20.filter(~known)
            .sort("mins", descending=True)
            .head(10)
            .select("player_id", "n", "mins")
        ).to_dicts(),
    }


def slice_counts(rows: pl.DataFrame) -> dict[str, Any]:
    """Check 3 counts, per test season, plus the S composition audit."""
    out: dict[str, Any] = {}
    for season in TEST_SEASONS:
        d = rows.filter(pl.col("season") == season)
        m = slice_masks(d)
        s = d.filter(pl.Series(m["S"]))
        # S rows whose index differs by source (FROM_YEAR vs draft_year), when both known
        both = s.filter(
            pl.col("first_season_raw").is_not_null() & pl.col("draft_year").is_not_null()
        )
        differs = int(((both["first_season_raw"] - both["draft_year"]) != 0).sum())
        out[str(season)] = {
            "rows": d.height,
            "S_rows": s.height,
            "S_games": int(s["game_id"].n_unique()),
            "S_players": int(s["player_id"].n_unique()),
            "S_rows_by_yp_seasons": {
                str(int(k)): int(v) for k, v in s.group_by("yp_seasons").len().iter_rows()
            },
            "S_rows_fs_fallback": int((s["fs_fallback"] == 1).sum()),
            "S_rows_both_sources_known": both.height,
            "S_rows_index_differs_by_source": differs,
            "other_young_rows": int(m["other_young"].sum()),
            "veteran_rows": int(m["veterans"].sum()),
            "top10_seasons_3_4_rows": int(m["top10_seasons_3_4"].sum()),
            "rows_fs_truncated": int((d["fs_truncated"] == 1).sum()),
            "rows_yp_seasons_lt1": int((d["yp_seasons"] < 1).sum()),
        }
    return out


# --------------------------------------------------------------------------- check 4 (planted)

YP_ALL: tuple[str, ...] = (*YP_NEW, "yp_status", "fs_fallback", "fs_truncated")


def planted_future_check(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static_x: pl.DataFrame,
    flagged: dict[str, set[int]],
    elo: dict[str, float],
    base_feats: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Check 4 on the real data: a fake game dated after the last real game (and ``tonight``'s
    real stats scrambled) leaves every ``yp_*`` column of all earlier rows unchanged; the fake
    game's own static columns equal the player's. Season label stays <= 2024 (no 2025 rows)."""
    if base_feats is None:
        base_feats = add_yp_columns(
            build_features(games, pgs, static_x, flagged, elo), games, pgs, static_x
        )
    last = games.sort(["game_date", "game_id"]).tail(1)
    fake_date = last["game_date"][0] + dt.timedelta(days=30)
    fid = "9999999"
    fg = last.with_columns(
        pl.lit(fid).alias("game_id"),
        pl.lit(fake_date).cast(games.schema["game_date"]).alias("game_date"),
        pl.lit(150).cast(games.schema["home_pts"]).alias("home_pts"),
    )
    last_id = last["game_id"][0]
    tonight_rows = pgs.filter(pl.col("game_id") == last_id)
    fp = tonight_rows.with_columns(
        pl.lit(fid).alias("game_id"),
        pl.lit(60).cast(pgs.schema["pts"]).alias("pts"),
        pl.lit(48.0).cast(pgs.schema["minutes"]).alias("minutes"),
    )
    # a brand-new player (not in players_static) in the fake game as well
    newp = fp.head(1).with_columns(
        pl.lit(99999999).cast(pgs.schema["player_id"]).alias("player_id")
    )
    pgs2 = pl.concat([pgs, fp, newp])
    hit = (pl.col("game_id") == last_id) & (pl.col("minutes") > 0)
    pgs2 = pgs2.with_columns(
        pl.when(hit).then(99).otherwise(pl.col("pts")).alias("pts"),
        pl.when(hit).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
    )
    games2 = pl.concat([games, fg])
    got = add_yp_columns(
        build_features(games2, pgs2, static_x, flagged, elo), games2, pgs2, static_x
    )
    key = ["game_id", "player_id"]
    a = base_feats.select(*key, *YP_ALL).sort(key)
    b = got.filter(pl.col("game_id") != fid).select(*key, *YP_ALL).sort(key)
    equal_rows = a.height == b.height and a.equals(b)
    fake = got.filter(pl.col("game_id") == fid)
    return {
        "rows_compared": a.height,
        "fake_rows": fake.height,
        "all_yp_columns_equal": bool(equal_rows),
        "fake_game_date": str(fake_date),
        "tonight_game_id_scrambled": str(last_id),
    }


# --------------------------------------------------------------------------- inputs


def load_study_inputs(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    """Games/pgs/static (season <= 2024 asserted), flagged, the feature frame with yp columns."""
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    if cfg.young_pick != "off":
        raise ValueError("F9b does not use the young_pick flag; config must keep it off")
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    static_x = load_static_extra(db_path)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo)
    feats = attach_realised(feats, games, pgs)
    feats = add_yp_columns(feats, games, pgs, static_x)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return {
        "games": games,
        "pgs": pgs,
        "static": static,
        "static_x": static_x,
        "flagged": flagged,
        "elo": elo,
        "cfg": cfg,
        "feats": feats,
    }


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_jd))


def _jd(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def run_checks(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Checks 1-4 (light). Writes ``results.json`` (check 5 is merged in by :func:`run_repro`)."""
    inp = load_study_inputs(db_path)
    feats = inp["feats"]
    rows = study_rows(feats)
    c12 = check_1_2(inp["games"], inp["pgs"], inp["static_x"], rows)
    counts = slice_counts(rows)
    planted = planted_future_check(
        inp["games"], inp["pgs"], inp["static_x"], inp["flagged"], inp["elo"], base_feats=feats
    )
    checks: list[dict[str, Any]] = [
        _chk(
            "1 draft status known, players >=20 games",
            c12["status_known_share"],
            f">= {MIN_STATUS_KNOWN}",
            c12["status_known_share"] >= MIN_STATUS_KNOWN,
        ),
        _chk(
            "1b unknown-status rate gap played<10 vs rest (pp)",
            c12["gap_pp"],
            f"<= {MAX_MISSING_GAP_PP}",
            c12["gap_pp"] <= MAX_MISSING_GAP_PP,
        ),
        _chk(
            "2 first_season known (FROM_YEAR or draft_year)",
            c12["first_season_known_share"],
            f">= {MIN_FIRST_SEASON_KNOWN}",
            c12["first_season_known_share"] >= MIN_FIRST_SEASON_KNOWN,
        ),
    ]
    for season in TEST_SEASONS:
        c = counts[str(season)]
        checks += [
            _chk(f"3 S rows {season}", c["S_rows"], f">= {MIN_S_ROWS}", c["S_rows"] >= MIN_S_ROWS),
            _chk(
                f"3 S players {season}",
                c["S_players"],
                f">= {MIN_S_PLAYERS}",
                c["S_players"] >= MIN_S_PLAYERS,
            ),
            _chk(
                f"3 S games {season}",
                c["S_games"],
                f">= {MIN_S_GAMES}",
                c["S_games"] >= MIN_S_GAMES,
            ),
            _chk(
                f"3 veterans rows {season}",
                c["veteran_rows"],
                f">= {MIN_VET_ROWS}",
                c["veteran_rows"] >= MIN_VET_ROWS,
            ),
        ]
    checks.append(
        _chk(
            "4 planted future leaves yp_* unchanged",
            int(planted["all_yp_columns_equal"]),
            "== 1",
            bool(planted["all_yp_columns_equal"]),
        )
    )
    res = {
        "frozen_sha256": frozen_sha256(),
        "frozen_sha256_prefix_ok": frozen_sha256().startswith(FROZEN_PREFIX),
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "check_1_2": c12,
        "check_3_counts": counts,
        "check_4_planted": planted,
        "checks": checks,
        "checks_1_4_ok": all(c["ok"] for c in checks),
        "n_rows_study": int(rows.height),
        "max_season_loaded": int(feats["season"].max()),
    }
    prev = out_dir / "results.json"
    if prev.exists():
        old = json.loads(prev.read_text())
        for k in ("check_5", "arms"):
            if k in old:
                res[k] = old[k]
    _write_json(prev, res)
    return res


def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"check": name, "value": value, "threshold": threshold, "ok": bool(ok)}


# --------------------------------------------------------------------------- arms


META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "season",
    "min10",
    "n_prior",
    "starter10",
    "team_game_no",
    "n_out_rot",
    "has_report",
    "min_gap",
    "yp_seasons",
    "yp_pick_ord",
    "fs_truncated",
)


def collect_arm(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, Any]:
    """Walk-forward (month blocks, production protocol, 2022 warm-up) with an explicit feature
    list; returns integer-support quantile grids ``qi`` ``[n, 199]``, outcomes and row meta."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(feats, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    qs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for start, end in month_blocks(test_all):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
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
        q = raw_quantiles(c, s, z, CRPS_TAUS)
        qs.append(to_integer_support(q).astype(np.int16))
        ys.append(block["y"].to_numpy())
        metas.append(block.select(META_COLS))
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def crps_int_by_season(arm: dict[str, Any]) -> dict[int, float]:
    out: dict[int, float] = {}
    for season in TEST_SEASONS:
        sm = (arm["meta"]["season"] == season).to_numpy()
        out[season] = float(crps_from_quantiles(arm["qi"][sm].astype(float), arm["y"][sm]).mean())
    return out


def run_repro(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Check 5: A0 (production names, frame carries the extra yp columns, flag off) reproduces the
    LOWER_TAIL pts integer CRPS 3.1528 (2023) / 3.1884 (2024); exact stored values to 1e-6."""
    inp = load_study_inputs(db_path)
    arm = collect_arm(inp["feats"], "pts", inp["cfg"], arm_names("pts", "A0"))
    got = crps_int_by_season(arm)
    ref = json.loads(LOWER_TAIL_JSON.read_text())["pts"]
    doc = {2023: 3.1528, 2024: 3.1884}
    rec: dict[str, Any] = {}
    for season in TEST_SEASONS:
        exact = float(ref[str(season)]["off"]["crps_int"])
        rec[str(season)] = {
            "got": got[season],
            "lower_tail_exact": exact,
            "delta_exact": got[season] - exact,
            "doc_4dp": doc[season],
            "delta_doc_4dp": got[season] - doc[season],
            "n": int((arm["meta"]["season"] == season).sum()),
            "n_ref": int(ref[str(season)]["off"]["n"]),
        }
    ok = all(abs(v["delta_exact"]) <= 1e-6 for v in rec.values())
    flag_off = inp["cfg"].young_pick == "off"
    res = {"seasons": rec, "ok_1e-6": bool(ok), "flag_off": flag_off}
    path = out_dir / "results.json"
    cur = json.loads(path.read_text()) if path.exists() else {}
    cur["check_5"] = res
    cur["checks_5_ok"] = bool(ok and flag_off)
    chk = [c for c in cur.get("checks", []) if not c["check"].startswith("5 ")]
    for season in TEST_SEASONS:
        chk.append(
            _chk(
                f"5 A0 pts integer CRPS vs LOWER_TAIL {season} (|delta|)",
                abs(rec[str(season)]["delta_exact"]),
                "<= 1e-6",
                abs(rec[str(season)]["delta_exact"]) <= 1e-6,
            )
        )
    chk.append(_chk("5 flag young_pick off (default)", int(flag_off), "== 1", flag_off))
    cur["checks"] = chk
    cur["all_checks_ok"] = all(c["ok"] for c in chk)
    _write_json(path, cur)
    return res


# --------------------------------------------------------------------------- metrics


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def row_scores(
    qi: np.ndarray, y: np.ndarray, stat: str, seed: int = SEEDS["pit"]
) -> dict[str, Any]:
    """Per-row integer-support scores: CRPS, mean-bias residual, randomized PIT, threshold LL."""
    qf = qi.astype(float)
    pit = randomized_pit(qf, y, seed)
    thr = LOW_THRESHOLDS[stat]
    tll = np.mean([_ll((qf >= n).mean(axis=1), y >= n) for n in thr], axis=0)
    return {
        "crps": crps_from_quantiles(qf, y),
        "resid": y - qf.mean(axis=1),
        "pit": pit,
        "tll": tll,
    }


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def table_rows(
    stat: str,
    season: int,
    scores: dict[str, dict[str, Any]],
    gid: np.ndarray,
    masks: dict[str, np.ndarray],
    n_boot: int = N_BOOT,
) -> list[dict[str, Any]]:
    """Rows of ``[stat, season, arm, slice, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p,
    p_bh, mde, bias, cov80, pit_q10, tll_pts10_15]`` (dcrps = arm - A0; ``tll`` uses the stat's
    declared thresholds, pts 10/15). ``p_bh`` is filled later (BH over stats, slice S, A1)."""
    out: list[dict[str, Any]] = []
    for arm in ARMS:
        sc = scores[arm]
        for name in REPORT_SLICES:
            m = masks[name]
            n = int(m.sum())
            row: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": arm,
                "slice": name,
                "n_rows": n,
                "n_games": int(len(np.unique(gid[m]))) if n else 0,
            }
            if n == 0:
                out.append(row)
                continue
            pit = sc["pit"][m]
            row.update(
                crps_int=float(sc["crps"][m].mean()),
                bias=float(sc["resid"][m].mean()),
                cov80=float(((pit > 0.10) & (pit <= 0.90)).mean()),
                cov_le80=float((pit <= 0.80).mean()),
                pit_q10=float((pit <= 0.10).mean()),
                tll_pts10_15=float(sc["tll"][m].mean()),
            )
            if arm == "A0":
                row.update(dcrps=None, ci_lo=None, ci_hi=None, p=None, p_bh=None, mde=None)
            else:
                d = (sc["crps"] - scores["A0"]["crps"])[m]
                ci = _ci(d, gid[m], n_boot)
                row.update(
                    dcrps=ci["point"],
                    ci_lo=ci["lo"],
                    ci_hi=ci["hi"],
                    p=boot_p_value(d, gid[m], n_boot),
                    p_bh=None,
                    mde=(ci["hi"] - ci["lo"]) / 2.0,  # = 1.96 x clustered SE
                )
            out.append(row)
    return out


def attack_rows(
    stat: str,
    season: int,
    scores: dict[str, dict[str, Any]],
    gid: np.ndarray,
    mask: np.ndarray,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Gate 5 inputs on slice S: A1 - A2 and A1 - A3 paired dCRPS (clustered)."""
    out: dict[str, Any] = {"stat": stat, "season": season}
    for other in ("A2", "A3"):
        d = (scores["A1"]["crps"] - scores[other]["crps"])[mask]
        out[f"A1_minus_{other}"] = _ci(d, gid[mask], n_boot) if d.size else None
    return out


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the 4 stats' slice-S A1-vs-A0 p-values, within each season."""
    for season in TEST_SEASONS:
        tgt = [
            r
            for r in rows
            if r["season"] == season
            and r["arm"] == "A1"
            and r["slice"] == "S"
            and r.get("p") is not None
        ]
        if not tgt:
            continue
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


# --------------------------------------------------------------------------- gates


def _get(
    tab: dict[tuple[str, int, str, str], dict[str, Any]], stat: str, season: int, arm: str, sl: str
) -> dict[str, Any]:
    return tab[(stat, season, arm, sl)]


def evaluate_gates(
    rows: list[dict[str, Any]], attacks: list[dict[str, Any]], stat: str, season: int
) -> dict[str, Any]:
    """Pass rule 1-5 for one (stat, season). Pure function of the result tables."""
    tab = {(r["stat"], r["season"], r["arm"], r["slice"]): r for r in rows}
    atk = next(a for a in attacks if a["stat"] == stat and a["season"] == season)
    s0, s1 = _get(tab, stat, season, "A0", "S"), _get(tab, stat, season, "A1", "S")
    a1_all = _get(tab, stat, season, "A1", "all")
    powered = s1["n_rows"] >= MIN_S_ROWS
    g1 = bool(
        powered
        and s1["dcrps"] <= -0.02
        and s1["ci_hi"] < 0.0
        and s1["p_bh"] is not None
        and s1["p_bh"] < 0.05
    )
    g2 = bool(powered and abs(s1["bias"]) <= 0.75 * abs(s0["bias"]) and abs(s1["bias"]) <= 0.5)
    g3 = bool(
        powered
        and 0.75 <= s1["cov80"] <= 0.85
        and abs(s1["cov80"] - 0.80) <= abs(s0["cov80"] - 0.80)
    )

    def _d(sl: str) -> float | None:
        return _get(tab, stat, season, "A1", sl).get("dcrps")

    comp_ok = {k: bool(_d(k) is None or _d(k) <= 0.003) for k in COMPLEMENT_SLICES}  # type: ignore[operator]
    sl_bad = {
        k: _d(k)
        for k in GATE4_SLICES
        if _get(tab, stat, season, "A1", k)["n_rows"] >= MIN_GATE_SLICE_N and _d(k) > 0.01  # type: ignore[operator]
    }
    g4_parts = {
        "overall_point<=+0.001": bool(a1_all["dcrps"] <= 0.001),
        "overall_ci_hi<=+0.003": bool(a1_all["ci_hi"] <= 0.003),
        "complements<=+0.003": comp_ok,
        "overall_cov80_in_[.75,.85]": bool(0.75 <= a1_all["cov80"] <= 0.85),
        "slices_worse_than_+0.01": sl_bad,
    }
    g4 = bool(
        g4_parts["overall_point<=+0.001"]
        and g4_parts["overall_ci_hi<=+0.003"]
        and all(comp_ok.values())
        and g4_parts["overall_cov80_in_[.75,.85]"]
        and not sl_bad
    )
    d2, d3 = atk["A1_minus_A2"], atk["A1_minus_A3"]
    g5 = bool(powered and d2 and d3 and d2["point"] <= -0.01 and d3["point"] <= 0.005)
    # reading fixed before any result: "role effect" = A3 alone clears the gate-1 size and A1 does
    # not beat A3 by the gate-5 margin; such a stat is reported as role effect, not confirmed.
    s3 = _get(tab, stat, season, "A3", "S")
    role_effect = bool(
        powered
        and s3.get("dcrps") is not None
        and s3["dcrps"] <= -0.02
        and d3
        and d3["point"] > -0.01
    )
    return {
        "stat": stat,
        "season": season,
        "powered": powered,
        "gate1": g1,
        "gate2": g2,
        "gate3": g3,
        "gate4": g4,
        "gate4_parts": g4_parts,
        "gate5": g5,
        "role_effect": role_effect,
        "all": bool(g1 and g2 and g3 and g4 and g5 and not role_effect),
    }


def verdicts(gates: list[dict[str, Any]]) -> dict[str, Any]:
    """2023 decides whether a stat proceeds; 2024 confirms. Gate 4 failure on any overall /
    complement check = closed as harmful (kill criterion)."""
    out: dict[str, Any] = {}
    for stat in PROP_STATS:
        g = {x["season"]: x for x in gates if x["stat"] == stat}
        out[stat] = {
            "passes_2023": bool(g[SELECT_SEASON]["all"]),
            "confirmed_2024": bool(g[SELECT_SEASON]["all"] and g[REPORT_SEASON]["all"]),
            "gate4_fail_harm_2023": not g[SELECT_SEASON]["gate4"],
            "gate4_fail_harm_2024": not g[REPORT_SEASON]["gate4"],
            "role_effect_2023": bool(g[SELECT_SEASON]["role_effect"]),
        }
    out["pts_gate1_fail_2023_close"] = not next(
        x for x in gates if x["stat"] == "pts" and x["season"] == SELECT_SEASON
    )["gate1"]
    return out


# --------------------------------------------------------------------------- arms runner


@contextlib.contextmanager
def heavy_lock(lock: Path = HEAVY_LOCK, retry_s: float = 120.0) -> Iterator[None]:
    """mkdir lock (the repo convention, see nba/parse/rebuild_lineups.py); retry; always removed."""
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


def score_stat(
    stat: str,
    arms: dict[str, dict[str, Any]],
    seasons: tuple[int, ...] = TEST_SEASONS,
    n_boot: int = N_BOOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Result-table rows and gate-5 inputs for one stat from the four arms' collected outputs."""
    ref = arms["A0"]["meta"]
    for a in ARMS:
        same = (
            arms[a]["meta"]
            .select("game_id", "player_id")
            .equals(ref.select("game_id", "player_id"))
        )
        assert same, "arms must score identical rows"
        assert np.array_equal(arms[a]["y"], arms["A0"]["y"])
    masks_all = slice_masks(ref)
    y_all = arms["A0"]["y"]
    gid_all = ref["game_id"].to_numpy()
    rows: list[dict[str, Any]] = []
    attacks: list[dict[str, Any]] = []
    for season in seasons:
        sm = (ref["season"] == season).to_numpy()
        scores = {a: row_scores(arms[a]["qi"][sm], y_all[sm], stat) for a in ARMS}
        masks = {k: v[sm] for k, v in masks_all.items()}
        rows += table_rows(stat, season, scores, gid_all[sm], masks, n_boot)
        attacks.append(attack_rows(stat, season, scores, gid_all[sm], masks["S"], n_boot))
    return rows, attacks


def wait_for_replay(poll_s: float = 60.0) -> None:
    """Block while a full-season replay (``nba.daily.replay_season``) is running."""
    while subprocess.run(["pgrep", "-f", "nba.daily.replay_season"], capture_output=True).stdout:
        print("replay_season running; waiting", flush=True)
        time.sleep(poll_s)


def run_arms(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """A0-A3 x 4 stats, select 2023 / report 2024. Refuses to run if checks 1-5 did not pass."""
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    if not (cur.get("checks_1_4_ok") and cur.get("checks_5_ok")):
        raise RuntimeError("minimum data checks did not all pass: no arm may be fitted")
    inp = load_study_inputs(db_path)
    feats, cfg = inp["feats"], inp["cfg"]
    frames = {"A0": feats, "A1": feats, "A2": placebo_frame(feats), "A3": feats}
    rows: list[dict[str, Any]] = []
    attacks: list[dict[str, Any]] = []
    for stat in PROP_STATS:
        arms = {a: collect_arm(frames[a], stat, cfg, arm_names(stat, a)) for a in ARMS}
        r_s, a_s = score_stat(stat, arms, n_boot=n_boot)
        rows += r_s
        attacks += a_s
        print(f"{stat} done", flush=True)
        del arms
    add_bh(rows)
    gates = [evaluate_gates(rows, attacks, st, se) for st in PROP_STATS for se in TEST_SEASONS]
    res = {
        "table": rows,
        "attack": attacks,
        "gates": gates,
        "verdicts": verdicts(gates),
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "frozen_sha256": frozen_sha256(),
        "n_boot": n_boot,
    }
    cur["arms"] = res
    _write_json(path, cur)
    return res


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return ""
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    """Markdown from ``results.json``: checks, results table, gates, draft ledger rows and the
    results-section text (for the maintainer to review and append; nothing is appended here)."""
    r = json.loads((out_dir / "results.json").read_text())
    sha = r["frozen_sha256"]
    ln = [
        "# F9b young top-10 pick prior: results (docs/prereg/F9b_YOUNG_PICK_PRIOR.md)",
        "",
        f"Frozen sha256 (above the line): `{sha}` (prefix `{sha[:8]}`). git SHA at run: "
        f"`{r['git_sha']}`. Seeds: {r['seeds']}. CPU only. Seasons <= 2024 only "
        f"(max season loaded: {r.get('max_season_loaded')}); nba.duckdb read-only.",
        "",
        "## Minimum data checks",
        "",
        "| check | value | threshold | ok |",
        "|---|---|---|---|",
    ]
    for c in r["checks"]:
        v = c["value"]
        vs = f"{v:.6g}" if isinstance(v, float) else str(v)
        ln.append(f"| {c['check']} | {vs} | {c['threshold']} | {'ok' if c['ok'] else 'FAIL'} |")
    c12 = r["check_1_2"]
    ln += [
        "",
        f"Players with >= 20 played games in 2022-24: {c12['n_players_ge20']}; status counts "
        f"{c12['status_counts']}; first_season from FROM_YEAR for {c12['n_fs_from_first_season']}, "
        f"fs_fallback share {c12['fs_fallback_share']:.4f}, fs_truncated share "
        f"{c12['fs_truncated_share']:.4f}. Unknown-status rate by minutes "
        f"{c12['unknown_rate_by_minutes']}; NaN yp_pick_ord rate (unknown + drafted-no-pick) "
        f"{c12['nan_yp_pick_ord_rate_by_minutes']}; undrafted share by minutes bucket "
        f"{c12['undrafted_share_by_minutes_bucket']}.",
        "",
        "Slice composition (check 3):",
        "",
        "```",
        json.dumps(r["check_3_counts"], indent=1),
        "```",
        "",
    ]
    if "check_5" in r:
        ln += [
            "A0 reproduction (check 5):",
            "",
            "```",
            json.dumps(r["check_5"], indent=1),
            "```",
            "",
        ]
    arms = r.get("arms")
    if not arms:
        ln += ["Arms not run (a check failed or the arms stage has not been executed).", ""]
        return "\n".join(ln)
    ln += [
        "## Results (dCRPS = arm - A0, integer support, game-clustered 95% CI, 2000 resamples)",
        "",
        "mde = 1.96 x clustered SE of the dCRPS on that slice. bias = stat - forecast mean. "
        "cov80 = central 80% interval coverage from continuity-corrected randomized PIT "
        "(0.10 < PIT <= 0.90). tll = mean threshold log loss at the stat's declared thresholds "
        "(pts 10/15).",
        "",
        "| stat | season | arm | slice | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p "
        "| p_bh | mde | bias | cov80 | pit_q10 | tll_pts10_15 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in arms["table"]:
        if t.get("crps_int") is None:
            ln.append(
                f"| {t['stat']} | {t['season']} | {t['arm']} | {t['slice']} | {t['n_rows']} | "
                f"{t['n_games']} |" + " |" * 11
            )
            continue
        ln.append(
            f"| {t['stat']} | {t['season']} | {t['arm']} | {t['slice']} | {t['n_rows']} | "
            f"{t['n_games']} | "
            f"{_f(t['crps_int'])} | {_f(t['dcrps'], 4, True)} | {_f(t['ci_lo'], 4, True)} | "
            f"{_f(t['ci_hi'], 4, True)} | {_f(t['p'], 3)} | {_f(t['p_bh'], 3)} | {_f(t['mde'])} | "
            f"{_f(t['bias'], 3, True)} | {_f(t['cov80'], 3)} | {_f(t['pit_q10'], 3)} | "
            f"{_f(t['tll_pts10_15'])} |"
        )
    ln += ["", "## Attack gate inputs (slice S, dCRPS A1 - other)", ""]
    ln += ["| stat | season | A1-A2 [CI] | A1-A3 [CI] |", "|---|---|---|---|"]
    for a in arms["attack"]:

        def cell(d: dict[str, float] | None) -> str:
            return "" if d is None else f"{d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]"

        ln.append(
            f"| {a['stat']} | {a['season']} | {cell(a['A1_minus_A2'])} | {cell(a['A1_minus_A3'])} |"
        )
    ln += [
        "",
        "## Gates",
        "",
        "| stat | season | powered | g1 | g2 | g3 | g4 | g5 | role effect | pass |",
    ]
    ln.append("|---|---|---|---|---|---|---|---|---|---|")
    for g in arms["gates"]:
        ln.append(
            f"| {g['stat']} | {g['season']} | {g['powered']} | {g['gate1']} | {g['gate2']} | "
            f"{g['gate3']} | {g['gate4']} | {g['gate5']} | {g['role_effect']} | {g['all']} |"
        )
    ln += ["", "Gate 4 detail:", "", "```"]
    for g in arms["gates"]:
        ln.append(f"{g['stat']} {g['season']}: {json.dumps(g['gate4_parts'])}")
    ln += ["```", "", "## Verdicts", "", "```", json.dumps(arms["verdicts"], indent=1), "```", ""]
    ln += _ledger_text(r)
    ln += _results_section(r)
    return "\n".join(ln)


def _results_section(r: dict[str, Any]) -> list[str]:
    """Draft text for the prereg doc's results section (below the freeze line); not appended."""
    arms = r["arms"]
    tab = {(t["stat"], t["season"], t["arm"], t["slice"]): t for t in arms["table"]}
    pre = r["frozen_sha256"][:8]
    ok = all(c["ok"] for c in r["checks"])
    ln = [
        "",
        "## Draft results-section text (for docs/prereg/F9b_YOUNG_PICK_PRIOR.md; not appended)",
        "",
        f"Frozen sha256 (first 8): {pre}. Checks 1-5: {'all passed' if ok else 'FAILED'}; "
        f"seasons <= 2024 only, nba.duckdb read-only, CPU, seeds {r['seeds']}.",
        "",
    ]
    for stat in PROP_STATS:
        parts = []
        for season in TEST_SEASONS:
            s1, a1 = tab[(stat, season, "A1", "S")], tab[(stat, season, "A1", "all")]
            parts.append(
                f"{season}: slice S n={s1['n_rows']} dCRPS {s1['dcrps']:+.4f} "
                f"[{s1['ci_lo']:+.4f}, {s1['ci_hi']:+.4f}] p_BH {s1['p_bh']:.3f} "
                f"MDE {s1['mde']:.4f}; "
                f"overall {a1['dcrps']:+.4f} [{a1['ci_lo']:+.4f}, {a1['ci_hi']:+.4f}]"
            )
        ln.append(f"* {stat}: " + " | ".join(parts))
    v = arms["verdicts"]
    ln += [
        "",
        f"Verdict: confirmed on 2024 = {[s for s in PROP_STATS if v[s]['confirmed_2024']]}; "
        f"pts gate 1 failed on 2023 = {v['pts_gate1_fail_2023_close']} (kill criterion: close, no "
        "other cut-offs, no F9c). Nothing promoted.",
        "",
        "Implementation readings fixed before any arm was fitted (not rule changes): cov80 = "
        "central 80% interval coverage (0.10 < PIT <= 0.90) of the continuity-corrected "
        "randomized PIT on the integer-mapped grid; bias = y - mean of the integer-mapped "
        "quantile grid; MDE = half-width of the clustered 95% CI; gate 5 'role effect' = A3 "
        "alone has slice dCRPS <= -0.02 and "
        "A1 - A3 > -0.01 (such a stat is reported as role effect, not confirmed); fs_fallback rows "
        "(draft_year when FROM_YEAR is NULL) cannot be cross-checked against FROM_YEAR, and "
        "draft_year <= FROM_YEAR means a delayed debut would make the fallback index too OLD, not "
        "too young as the doc says.",
    ]
    return ln


def _ledger_text(r: dict[str, Any]) -> list[str]:
    """Proposed ledger rows (one per stat/season A1 vs A0 on S, plus one overall) and drafted
    holdout text for a confirmed stat. NOT appended anywhere."""
    arms = r["arms"]
    tab = {(t["stat"], t["season"], t["arm"], t["slice"]): t for t in arms["table"]}
    pre = r["frozen_sha256"][:8]
    ln = [
        "## Proposed ledger rows (draft; maintainer reviews and appends)",
        "",
        "Columns follow docs/TEST_LEDGER.md: id | date | test | result | effect | CI lo | CI hi | "
        "cluster | p(BH) | notes | holdout status.",
        "",
    ]
    for stat in PROP_STATS:
        for season in TEST_SEASONS:
            for sl, label in (("S", "slice S"), ("all", "overall")):
                t = tab[(stat, season, "A1", sl)]
                ln.append(
                    f"| T### | 2026-10-09 | F9b_YOUNG_PICK_PRIOR (frozen sha256 {pre}) | "
                    f"{stat} {season} A1 vs A0 {label}, integer-support CRPS, n={t['n_rows']} "
                    f"rows / {t['n_games']} games"
                    f" | {t['dcrps']:+.4f} | {t['ci_lo']:+.4f} | {t['ci_hi']:+.4f} | game | "
                    f"{_f(t['p_bh'], 3) if sl == 'S' else 'n/a'} | MDE {t['mde']:.4f}; "
                    f"bias A0 {tab[(stat, season, 'A0', sl)]['bias']:+.3f} -> A1 {t['bias']:+.3f}; "
                    f"cov80 {tab[(stat, season, 'A0', sl)]['cov80']:.3f} -> {t['cov80']:.3f} | "
                    f"not holdout (<=2024) |"
                )
    conf = [s for s in PROP_STATS if arms["verdicts"][s]["confirmed_2024"]]
    ln += ["", "## Drafted holdout row text (not appended)", ""]
    if conf:
        for s in conf:
            ln.append(
                f"- F9b {s}: confirmed on 2023 and 2024; request ONE logged holdout touch on "
                f"season "
                f"2025 for the A1 arm (frozen sha256 {pre}) before running; shadow-logging "
                f"recommendation only, nothing promoted."
            )
    else:
        ln.append("- No stat confirmed; no holdout row is requested.")
    return ln


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("stage", choices=("checks", "repro", "arms", "report"))
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--no-lock", action="store_true", help="skip heavy.lock (repro only)")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "checks":
        res = run_checks(a.db_path)
        print(
            json.dumps({k: res[k] for k in ("frozen_sha256", "checks", "checks_1_4_ok")}, indent=1)
        )
    elif a.stage == "repro":
        print(json.dumps(run_repro(a.db_path), indent=1))
    elif a.stage == "arms":
        wait_for_replay()
        with heavy_lock():
            res = run_arms(a.db_path)
        print(json.dumps(res["verdicts"], indent=1))
    else:
        REPORT_MD.write_text(render_report())
        print("wrote", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
