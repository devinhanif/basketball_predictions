"""PPLAY_CONTEXT evaluation harness (rule: docs/prereg/PPLAY_CONTEXT.md; DRAFT until frozen).

    python -W ignore -m research.eval.pplay_context_eval gate --allow-draft \
        --doc docs/prereg/PPLAY_CONTEXT.md        # minimum-data checks 1-4 (draft allowed)
    python -W ignore -m research.eval.pplay_context_eval smoke --doc ... --sha <64 hex>
    python -W ignore -m research.eval.pplay_context_eval run   --doc ... --sha <64 hex>
    python -W ignore -m research.eval.pplay_context_eval report --doc ... --sha <64 hex>

Stages. ``gate`` measures the four minimum-data checks on the real data and writes
``reports/prereg_pplay_context/gate.json`` (no arm is fitted, no P2 number exists). ``smoke`` fits
one month block (P1 and P2, T-60). ``run`` is the full walk-forward (checkpoints per month block
under ``data/pplay_context/``). ``report`` scores the stored per-row predictions (paired,
game-clustered) and writes ``results.json`` and ``reports/prereg_pplay_context.md``. ``smoke``,
``run`` and ``report`` need ``--sha`` equal to the sha256 of everything above the marker line and
re-run the gate; ``--allow-draft`` skips the hash check and is for the gate stage only.

``nba.duckdb`` is opened read-only with ``season <= 2024`` in every query (asserted); season 2025
is never loaded. Heavy stages take ``data/ops/heavy.lock`` (retry 120 s, released in ``finally``).

The P(play) row construction is MINUTES_V2's secondary (``research.eval.minutes_v2_eval.run_pplay``)
copied line for line up to the Platt map; check 1 is the equivalence proof against its stored
``reports/minutes_v2/pplay.npz``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    month_blocks,
)
from nba.features.game_tipoff import attach_real_tips
from nba.features.injury_report import ReportTriggerConfig, usable_report_rows
from nba.props.context_residual import (
    ContextResidualConfig,
    _matrix,
    _params,
    asof_elo_margin,
    team_game_context,
)
from nba.props.forward import P_PLAY_PLATT
from nba.props.minutes import GAME_CONTEXT_FEATURE_COLUMNS, build_minutes_features, predict_minutes
from research.eval.minutes_hazard_eval import BUCKETS, HeavyLock, bucket_name, frozen_sha
from research.eval.minutes_v2_eval import _ci, _ll, ece, load_world
from research.props.minutes_v2 import MIN_TRAIN

DEFAULT_DOC = Path("docs/prereg/PPLAY_CONTEXT.md")
PREREG = DEFAULT_DOC
OUT_DIR = Path("reports/prereg_pplay_context")
REPORT_PATH = Path("reports/prereg_pplay_context.md")
CACHE = Path("data/pplay_context")
STORED = Path("reports/minutes_v2/pplay.npz")
HISTORY_DB = Path("data/history/nba_history.duckdb")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEASONS = (SELECT_SEASON, REPORT_SEASON)
N_BOOT = 2000
MIN_TRAIN_ROWS = MIN_TRAIN
TOL_P0 = 1e-6
TOL_P0_BY_SEASON = {2023: 1e-6, 2024: 0.005}  # frozen amended check 1
GAP_PP = 2.0
T60_SHARE_MIN = 0.95
MORNING_DAY_SHARE_MIN = 0.90
DEFINED_SHARE_MIN = 0.95

# ---- term constants (draft "Hypothesis" and "Fixed design"; final at the freeze)
WINDOW = 82  # prior team games for the team's b2b star sit-out rate
K_PSEUDO = 20.0  # pseudo-count, in b2b games, shrinking toward the league rate
MIN_B2B = 10  # prior b2b games for "defined" (frozen amendment to check 3: 20 was impossible)
STAR_PTS = 20.0  # prior-10 points
STAR_TOP = 3  # top-3 prior-10 minutes among the team's players of the previous game
MORNING_HOUR_LT = 15  # morning snapshot = the earliest same-day snapshot, stamped before 15:00 ET
STATUS_CODE = {"available": 1.0, "probable": 2.0, "questionable": 3.0, "doubtful": 4.0, "out": 5.0}

FAMILIES = ("T-60", "Morning")
MIN_SLICE_N = 300
DELTA_FLOOR = -0.005
ECE_TOL = 0.005
SLICE_TOL = 0.01
PLACEBO_MARGIN = -0.003

KEYS = ["game_id", "player_id"]
TERM_B2B_SIT = ["team_b2b_sit"]
TERM_STAR = ["star", "b2b_star"]
TERM_TAG = {
    "T-60": ["terc", "tag60_code", "q60", "d60", "q60_x_terc", "d60_x_terc"],
    "Morning": ["terc", "tagm_code", "qm", "dm", "qm_x_terc", "dm_x_terc"],
}
TERM_REPORT = ["on_morning_report", "on_evening_report"]
BASE_FEATURES = [
    "games_played_prior",
    "n_played_prior",
    "play_rate_prior",
    "avg_minutes_given_played_prior",
    "std_minutes_given_played_prior",
    "starter_rate_prior",
    *GAME_CONTEXT_FEATURE_COLUMNS,
    "age",
    "exp_margin",
    "dnp_last5",
    "days_since_played",
]
TERM_COLUMNS = [
    "is_b2b_second",
    "n_b2b_prior",
    "b2b_defined",
    "team_b2b_sit",
    "star",
    "b2b_star",
    "terc",
    "tag60_code",
    "q60",
    "d60",
    "q60_x_terc",
    "d60_x_terc",
    "tagm_code",
    "qm",
    "dm",
    "qm_x_terc",
    "dm_x_terc",
    "on_morning_report",
    "on_evening_report",
    "has_t60",
    "has_morning",
    "has_evening",
]
UNSTATED_DETAILS = (
    "star = played the team's previous game AND (prior-10 pts >= 20 incl. that game, min 5 games, "
    "OR top-3 prior-10 minutes among the team's players of that game); the descriptive tables' "
    "definition (data/scratch/fan_answers_2026-10-11.py); a player who missed the previous game is "
    "not a star row",
    "team rate unit: each prior b2b second-night team game contributes the share of its star "
    "opportunities who sat (traded-away players excluded); rate = (sum + 20*L) / (n_b2b + 20), "
    "L = league mean of that share over all b2b games strictly before the date; defined only if "
    "n_b2b >= 10 inside the 82-game window (frozen amendment), else the row takes L (counted)",
    "the 82-game window is over all team games (playoffs included), crossing seasons",
    "is_b2b_second = previous team game was yesterday; the model gets star and b2b_star (b2b "
    "itself is already a P1 feature); agreement with P1's b2b column is reported",
    "strength tercile = prior-season regular-season win% rank among 30 teams (ties by team_id), "
    "2 = contender; 2022's comes from data/history 2021-22; tag code unlisted 0, available 1, "
    "probable 2, questionable 3, doubtful 4 (null when the game has no such snapshot)",
    "morning = earliest same-day snapshot stamped before 15:00 ET (usable under the real-tip "
    "gate); evening = latest snapshot stamped on the previous calendar day; a game with no such "
    "snapshot gets null, never 'nobody listed'",
    "placebo P3 permutes team_b2b_sit across team-games within season (seed 0)",
    "P0 is reported against both p_platt (the draft's P0) and p_raw (reference only)",
)


def _savez(path: Path, data: dict[str, Any]) -> None:
    np.savez_compressed(path, **data)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze


def configure(doc: Path) -> None:
    """Point the doc path, cache and outputs at ``doc`` (id = file stem, lower-cased)."""
    global PREREG, OUT_DIR, REPORT_PATH, CACHE
    stem = doc.stem.lower()
    PREREG = doc
    OUT_DIR = Path(f"reports/prereg_{stem}")
    REPORT_PATH = Path(f"reports/prereg_{stem}.md")
    CACHE = Path(f"data/{stem}")


def require_frozen(expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(PREREG)
    if allow_draft:
        return sha
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def assert_seasons(df: pl.DataFrame, col: str = "season") -> None:
    if col in df.columns and df.height and int(df[col].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")


# --------------------------------------------------------------------------- base rows (P0)


def build_base(world: dict[str, Any]) -> pl.DataFrame:
    """MINUTES_V2's secondary P(play) rows (``run_pplay``) up to the Platt map, plus realised
    minutes for the audit. Rows: build_minutes_features rows with n_played_prior >= 5, excluding
    players OUT on the real-tip-gated T-60 report; label ``played`` = minutes > 0."""
    games, pgs, static = world["games"], world["pgs"], world["static"]
    mf = build_minutes_features(world["sc"], use_game_context=True)
    dists = predict_minutes(mf)
    mf = mf.with_columns(pl.Series("p_raw", np.array([d.p_play for d in dists])))
    lab = pgs.select(
        "game_id",
        "player_id",
        (pl.col("minutes") > 0).fill_null(False).cast(pl.Float64).alias("played"),
        pl.col("minutes").cast(pl.Float64).fill_null(0.0).alias("min_real"),
    )
    mf = mf.join(lab, on=["game_id", "player_id"], how="inner").sort(
        ["player_id", "game_date", "game_id"]
    )
    mf = mf.with_columns(
        (1.0 - pl.col("played"))
        .shift(1)
        .rolling_sum(5, min_samples=1)
        .over("player_id")
        .alias("dnp_last5"),
        pl.when(pl.col("played") == 1.0)
        .then(pl.col("game_date"))
        .otherwise(None)
        .shift(1)
        .over("player_id")
        .alias("_lp"),
    ).with_columns(pl.col("_lp").forward_fill().over("player_id").alias("_lp"))
    mf = mf.with_columns(
        (pl.col("game_date") - pl.col("_lp"))
        .dt.total_days()
        .cast(pl.Float64)
        .alias("days_since_played")
    ).drop("_lp")
    out = [(g, p) for g, ps in world["flagged"].items() for p in ps]
    out_df = pl.DataFrame(
        out, schema={"game_id": pl.Utf8, "player_id": pl.Int64}, orient="row"
    ).with_columns(pl.lit(1).alias("_out"))
    mf = (
        mf.join(out_df, on=["game_id", "player_id"], how="left")
        .filter(pl.col("_out").is_null())
        .filter(pl.col("n_played_prior") >= 5)
    )
    elo = asof_elo_margin(games, world["elo_params"])
    tctx = team_game_context(games, pgs, elo).select("game_id", "team_id", "exp_margin")
    mf = mf.join(tctx, on=["game_id", "team_id"], how="left")
    mf = mf.join(
        static.select("player_id", "birth_date").unique("player_id"), on="player_id", how="left"
    )
    mf = mf.with_columns(
        ((pl.col("game_date") - pl.col("birth_date").cast(pl.Date)).dt.total_days() / 365.25).alias(
            "age"
        )
    ).sort(["game_date", "game_id", "player_id"])
    platt_a, platt_b = P_PLAY_PLATT
    rp = np.clip(mf["p_raw"].to_numpy(), 1e-3, 1 - 1e-3)
    return mf.with_columns(
        pl.Series("p_platt", 1.0 / (1.0 + np.exp(-(platt_a + platt_b * np.log(rp / (1.0 - rp))))))
    )


# --------------------------------------------------------------------------- terms


def _i64(df: pl.DataFrame, cols: tuple[str, ...]) -> pl.DataFrame:
    return df.with_columns([pl.col(c).cast(pl.Int64) for c in cols if c in df.columns])


def team_games(games: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, team) with the team's game index and rest gap (all games, as-of)."""
    g = games.select(
        "game_id", pl.col("game_date").cast(pl.Date), "season", "home_team", "away_team"
    )
    tg = pl.concat(
        [
            g.select("game_id", "game_date", "season", pl.col("home_team").alias("team_id")),
            g.select("game_id", "game_date", "season", pl.col("away_team").alias("team_id")),
        ]
    )
    tg = _i64(tg, ("team_id", "season")).sort(["team_id", "game_date", "game_id"])
    tg = tg.with_columns(
        pl.int_range(pl.len()).over("team_id").alias("tgn"),
        (pl.col("game_date") - pl.col("game_date").shift(1).over("team_id"))
        .dt.total_days()
        .alias("rest_gap"),
    )
    return tg.with_columns((pl.col("rest_gap") == 1).fill_null(False).alias("is_b2b_second"))


def _played_states(pgs: pl.DataFrame, tg: pl.DataFrame) -> pl.DataFrame:
    """Played rows with prior-10 (incl. that game) minutes/points and the minutes rank."""
    p = _i64(
        pgs.select(
            pl.col("game_id").cast(pl.Utf8),
            "player_id",
            "team_id",
            pl.col("minutes").cast(pl.Float64),
            pl.col("pts").cast(pl.Float64),
        ),
        ("player_id", "team_id"),
    ).filter(pl.col("minutes") > 0)
    p = p.join(
        tg.select("game_id", "team_id", "game_date", "tgn"), on=["game_id", "team_id"], how="inner"
    ).sort(["player_id", "game_date", "game_id"])
    p = p.with_columns(
        pl.col("minutes").rolling_mean(10, min_samples=5).over("player_id").alias("m10_incl"),
        pl.col("pts").rolling_mean(10, min_samples=5).over("player_id").alias("p10_incl"),
    )
    return p.with_columns(
        pl.col("m10_incl").rank("ordinal", descending=True).over(["game_id", "team_id"]).alias("mr")
    )


def star_opportunities(tg: pl.DataFrame, pgp: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(star table keyed by (player, next team game); star opportunities with ``sat``).

    ``sat`` reads the next game's own box score, so it is used only for LATER dates."""
    star_expr = (
        (pl.col("p10_incl") >= STAR_PTS).fill_null(False)
        | (pl.col("mr") <= STAR_TOP).fill_null(False)
    ).alias("star")
    prev = pgp.select("player_id", "team_id", (pl.col("tgn") + 1).alias("tgn"), star_expr)
    star_tbl = prev.join(
        tg.select("team_id", "tgn", "game_id", "game_date", "is_b2b_second"),
        on=["team_id", "tgn"],
        how="inner",
    )
    cand = star_tbl.filter(pl.col("star"))
    played_in = pgp.select("player_id", "game_id", pl.lit(True).alias("played"))
    other = pgp.select("player_id", "game_date", pl.col("team_id").alias("tm_other")).unique(
        ["player_id", "game_date"]
    )
    cand = (
        cand.join(played_in, on=["player_id", "game_id"], how="left")
        .join(other, on=["player_id", "game_date"], how="left")
        .filter(pl.col("tm_other").is_null() | (pl.col("tm_other") == pl.col("team_id")))
        .with_columns(pl.col("played").is_null().alias("sat"))
    )
    return star_tbl, cand


def team_rates(
    tg: pl.DataFrame, pgp: pl.DataFrame, window: int = WINDOW
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(star table keyed by (player, next team game), per-(game, team) as-of team b2b sit rate)."""
    star_tbl, cand = star_opportunities(tg, pgp)
    agg = cand.group_by(["team_id", "game_id"]).agg(
        pl.len().alias("nopp"), pl.col("sat").sum().alias("sits")
    )
    tgx = tg.join(agg, on=["team_id", "game_id"], how="left").with_columns(
        (pl.col("is_b2b_second") & (pl.col("nopp").fill_null(0) > 0)).cast(pl.Float64).alias("ind")
    )
    tgx = tgx.with_columns(
        pl.when(pl.col("ind") > 0)
        .then(pl.col("sits") / pl.col("nopp"))
        .otherwise(0.0)
        .cast(pl.Float64)
        .alias("gx")
    ).sort(["team_id", "tgn"])
    tgx = tgx.with_columns(
        pl.col("gx").shift(1).rolling_sum(window, min_samples=1).over("team_id").alias("sx"),
        pl.col("ind").shift(1).rolling_sum(window, min_samples=1).over("team_id").alias("nb"),
    ).with_columns(pl.col("sx").fill_null(0.0), pl.col("nb").fill_null(0.0))
    daily = (
        tgx.group_by("game_date")
        .agg(pl.col("gx").sum().alias("dx"), pl.col("ind").sum().alias("dn"))
        .sort("game_date")
        .with_columns(pl.col("dx").cum_sum().alias("cx"), pl.col("dn").cum_sum().alias("cn"))
        .select("game_date", "cx", "cn")
    )
    tgx = tgx.sort("game_date").join_asof(
        daily, on="game_date", strategy="backward", allow_exact_matches=False
    )
    tgx = tgx.with_columns(
        pl.when(pl.col("cn") > 0).then(pl.col("cx") / pl.col("cn")).otherwise(None).alias("league")
    ).with_columns(
        (pl.col("nb") >= MIN_B2B).alias("b2b_defined"),
        ((pl.col("sx") + K_PSEUDO * pl.col("league")) / (pl.col("nb") + K_PSEUDO)).alias("_raw"),
    )
    tgx = tgx.with_columns(
        pl.when(pl.col("b2b_defined"))
        .then(pl.col("_raw"))
        .otherwise(pl.col("league"))
        .alias("team_b2b_sit"),
        pl.col("nb").alias("n_b2b_prior"),
    )
    return star_tbl, tgx.select(
        "game_id", "team_id", "is_b2b_second", "n_b2b_prior", "b2b_defined", "team_b2b_sit"
    )


def strength_terciles(prior_wp: pl.DataFrame) -> pl.DataFrame:
    """(team_id, season, terc) from prior-season win%; 2 = contender, 0 = weak (ties by team_id)."""
    d = prior_wp.select(
        pl.col("team_id").cast(pl.Int64), pl.col("season").cast(pl.Int64), "prior_wp"
    ).sort(["season", "prior_wp", "team_id"], descending=[False, True, False])
    d = d.with_columns(
        pl.col("prior_wp").rank("ordinal", descending=True).over("season").alias("_rk"),
        pl.len().over("season").alias("_n"),
    )
    return d.select(
        "team_id",
        "season",
        (2 - ((pl.col("_rk") - 1) * 3 // pl.col("_n"))).cast(pl.Float64).alias("terc"),
    )


def prior_win_pct(games: pl.DataFrame, history_games: pl.DataFrame | None) -> pl.DataFrame:
    """(team_id, season, prior_wp): regular-season win% of season-1 (game_id prefix 002)."""

    def wins(df: pl.DataFrame) -> pl.DataFrame:
        r = df.filter(pl.col("game_id").str.starts_with("002") & pl.col("home_pts").is_not_null())
        return pl.concat(
            [
                r.select(
                    pl.col("season").cast(pl.Int64),
                    pl.col("home_team").cast(pl.Int64).alias("team_id"),
                    (pl.col("home_pts") > pl.col("away_pts")).cast(pl.Float64).alias("w"),
                ),
                r.select(
                    pl.col("season").cast(pl.Int64),
                    pl.col("away_team").cast(pl.Int64).alias("team_id"),
                    (pl.col("away_pts") > pl.col("home_pts")).cast(pl.Float64).alias("w"),
                ),
            ]
        )

    parts = [wins(games)]
    if history_games is not None and history_games.height:
        parts.append(wins(history_games))
    wp = pl.concat(parts).group_by(["team_id", "season"]).agg(pl.col("w").mean().alias("wp"))
    return wp.select(
        "team_id", (pl.col("season") + 1).alias("season"), pl.col("wp").alias("prior_wp")
    )


def _snap(df: pl.DataFrame, earliest: bool) -> pl.DataFrame:
    """Rows of each game's earliest/latest snapshot: (game_id, player_id, status, snap_ts)."""
    if df.height == 0:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "status": pl.Utf8,
                "snap_ts": pl.Datetime("us"),
            }
        )
    pick = pl.col("as_of").min() if earliest else pl.col("as_of").max()
    sel = df.group_by("game_id").agg(pick.alias("snap_ts"))
    return (
        df.join(sel, on="game_id")
        .filter(pl.col("as_of") == pl.col("snap_ts"))
        .unique(["game_id", "player_id"], keep="last")
        .select("game_id", pl.col("player_id").cast(pl.Int64), "status", "snap_ts")
    )


def snapshot_views(snaps: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """T-60 (latest usable), morning (earliest same-day, before 15:00) and previous-evening
    (latest stamped the previous calendar day) snapshots; all under the real-tip gate."""
    if snaps.height and "tip_et" not in snaps.columns:
        raise ValueError("snapshots need a tip_et column (attach_real_tips)")
    use = usable_report_rows(snaps, ReportTriggerConfig(tip_source="real"))
    use = use.with_columns(pl.col("game_date").cast(pl.Date))
    same = use.filter(pl.col("as_of").dt.date() == pl.col("game_date"))
    morning = _snap(same, True)
    morning_games = morning.filter(pl.col("snap_ts").dt.hour() < MORNING_HOUR_LT)["game_id"]
    morning = morning.filter(pl.col("game_id").is_in(morning_games.to_list()))
    prev = use.filter(pl.col("as_of").dt.date() == pl.col("game_date") - pl.duration(days=1))
    return {"t60": _snap(use, False), "morning": morning, "evening": _snap(prev, False)}


def _code_expr(has_game: str) -> pl.Expr:
    c = pl.lit(0.0)
    for s, v in STATUS_CODE.items():
        c = pl.when(pl.col("status") == s).then(pl.lit(v)).otherwise(c)
    return pl.when(pl.col(has_game).is_null()).then(None).otherwise(c).cast(pl.Float64)


def _attach_snapshot(
    rows: pl.DataFrame, snap: pl.DataFrame, code: str, q: str, d: str, has: str
) -> pl.DataFrame:
    games_with = snap.select("game_id").unique().with_columns(pl.lit(1).alias("_hg"))
    j = (
        rows.select(KEYS)
        .join(snap.select(*KEYS, "status"), on=KEYS, how="left")
        .join(games_with, on="game_id", how="left")
        .with_columns(_code_expr("_hg").alias(code))
    )
    return j.select(
        *KEYS,
        pl.col(code),
        (pl.col(code) == 3.0).cast(pl.Float64).alias(q),
        (pl.col(code) == 4.0).cast(pl.Float64).alias(d),
        pl.col("_hg").is_not_null().cast(pl.Int8).alias(has),
    )


def _listed(rows: pl.DataFrame, snap: pl.DataFrame, name: str) -> pl.DataFrame:
    games_with = snap.select("game_id").unique().with_columns(pl.lit(1).alias("_hg"))
    listed = snap.select(*KEYS, pl.lit(1.0).alias("_l"))
    return (
        rows.select(KEYS)
        .join(listed, on=KEYS, how="left")
        .join(games_with, on="game_id", how="left")
        .select(
            *KEYS,
            pl.when(pl.col("_hg").is_null())
            .then(None)
            .otherwise(pl.col("_l").fill_null(0.0))
            .cast(pl.Float64)
            .alias(name),
        )
    )


def build_terms(
    rows: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    snaps: pl.DataFrame,
    prior_wp: pl.DataFrame,
) -> pl.DataFrame:
    """The four terms for ``rows`` (game_id, player_id, team_id, season, game_date), strictly
    as-of: nothing about tonight's box score or stint is read, and report rows are used only if
    stamped at or before the real tip minus 60 minutes."""
    r = _i64(
        rows.select("game_id", "player_id", "team_id", "season").with_columns(
            pl.col("game_id").cast(pl.Utf8)
        ),
        ("player_id", "team_id", "season"),
    )
    tg = team_games(games)
    pgp = _played_states(pgs, tg)
    star_tbl, rates = team_rates(tg, pgp)
    out = r.join(rates, on=["game_id", "team_id"], how="left").join(
        star_tbl.select("player_id", "team_id", "game_id", "star"),
        on=["player_id", "team_id", "game_id"],
        how="left",
    )
    out = out.with_columns(
        pl.col("star").fill_null(False).cast(pl.Float64),
        pl.col("is_b2b_second").fill_null(False),
    ).with_columns((pl.col("star") * pl.col("is_b2b_second").cast(pl.Float64)).alias("b2b_star"))
    out = out.join(strength_terciles(prior_wp), on=["team_id", "season"], how="left")
    views = snapshot_views(snaps)
    for key, code, q, d, has in (
        ("t60", "tag60_code", "q60", "d60", "has_t60"),
        ("morning", "tagm_code", "qm", "dm", "has_morning"),
    ):
        out = out.join(_attach_snapshot(r, views[key], code, q, d, has), on=KEYS, how="left")
    out = out.join(_listed(r, views["morning"], "on_morning_report"), on=KEYS, how="left")
    out = out.join(_listed(r, views["evening"], "on_evening_report"), on=KEYS, how="left")
    out = out.with_columns(
        pl.col("on_evening_report").is_not_null().cast(pl.Int8).alias("has_evening"),
        *(
            (pl.col(f"{t}") * pl.col("terc")).alias(f"{t}_x_terc")
            for t in ("q60", "d60", "qm", "dm")
        ),
    )
    return out.select(*KEYS, *TERM_COLUMNS)


def permute_team_game_values(
    frame: pl.DataFrame, col: str = "team_b2b_sit", seed: int = 0
) -> pl.DataFrame:
    """Placebo column: ``col`` permuted across team-games within each season (all players of a
    team-game keep one shared value)."""
    tgv = (
        frame.select("game_id", "team_id", "season", col)
        .unique(["game_id", "team_id"])
        .sort(["game_id", "team_id"])
    )
    rng = np.random.default_rng(seed)
    vals = tgv[col].to_numpy().astype(float).copy()
    season = tgv["season"].to_numpy()
    for s in np.unique(season):
        idx = np.where(season == s)[0]
        vals[idx] = vals[rng.permutation(idx)]
    perm = tgv.select("game_id", "team_id").with_columns(
        pl.Series(f"{col}_perm", vals, nan_to_null=True)
    )
    return frame.join(perm, on=["game_id", "team_id"], how="left")


# --------------------------------------------------------------------------- data (real)


def load_snapshots(db_path: str = "nba.duckdb", tips: pl.DataFrame | None = None) -> pl.DataFrame:
    """Official-report rows of games with season <= 2024 (read-only), real tips attached."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
            "CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date, g.season "
            "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
            f"WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL "
            f"AND g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert_seasons(df)
    df = df.with_columns(
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("player_id").cast(pl.Int64),
        pl.col("game_date").cast(pl.Date),
    )
    return attach_real_tips(df, tips).drop("season")


def load_history_games(path: Path = HISTORY_DB) -> pl.DataFrame | None:
    """2021-22 regular-season games from data/history (read-only) for the 2022 win% prior."""
    if not path.exists():
        return None
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(
            "SELECT game_id, season, home_team, away_team, home_pts, away_pts FROM games "
            "WHERE season = 2021 AND substr(game_id,1,3)='002' AND home_pts IS NOT NULL"
        ).pl()
    finally:
        con.close()


def prepare(db_path: str = "nba.duckdb") -> dict[str, Any]:
    world = load_world(db_path)
    try:
        base = build_base(world)
        games, pgs = world["games"], world["pgs"]
    finally:
        world["sc"].close()
    assert_seasons(base)
    snaps = load_snapshots(db_path)
    prior_wp = prior_win_pct(games, load_history_games())
    terms = build_terms(
        base.select("game_id", "player_id", "team_id", "season", "game_date"),
        games,
        pgs,
        snaps,
        prior_wp,
    )
    frame = base.join(terms, on=KEYS, how="left")
    if frame.height != base.height:
        raise ValueError("term join changed the row count")
    frame = permute_team_game_values(frame)
    return {
        "frame": frame,
        "games": games,
        "pgs": pgs,
        "snaps": snaps,
        "prior_wp": prior_wp,
        "rows": base.select("game_id", "player_id", "team_id", "season", "game_date"),
    }


# --------------------------------------------------------------------------- gate


def check_p0(frame: pl.DataFrame, stored: dict[str, np.ndarray]) -> dict[str, Any]:
    """Check 1: P0 (Platt, and raw for reference) reproduces MINUTES_V2's stored secondary log
    loss to 1e-6, per season on the full row sets (the literal rule). Row-set differences are
    diagnosed by game and a matching-games subset; nothing is tuned."""
    test = frame.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    gid = test["game_id"].to_numpy().astype(str)
    y = test["played"].to_numpy().astype(bool)
    season = test["season"].to_numpy()
    sgid, sy = stored["gid"].astype(str), stored["y"].astype(bool)
    sseason = stored["season"]
    detail: dict[str, Any] = {"n_mine": test.height, "n_stored": int(len(sy))}
    ok = True
    for s in SEASONS:
        m, ms = season == s, sseason == s
        cm, cs = Counter(gid[m].tolist()), Counter(sgid[ms].tolist())
        diff_games = sorted(g for g in set(cm) | set(cs) if cm[g] != cs[g])
        same_m = m & ~np.isin(gid, diff_games)
        same_s = ms & ~np.isin(sgid, diff_games)
        ent: dict[str, Any] = {
            "n_mine": int(m.sum()),
            "n_stored": int(ms.sum()),
            "games_with_different_row_counts": len(diff_games),
            "differing_games_first20": diff_games[:20],
            "rows_aligned_full": bool(
                m.sum() == ms.sum()
                and np.array_equal(gid[m], sgid[ms])
                and np.array_equal(y[m], sy[ms])
            ),
            "rows_aligned_matching_games": bool(
                same_m.sum() == same_s.sum()
                and np.array_equal(gid[same_m], sgid[same_s])
                and np.array_equal(y[same_m], sy[same_s])
            ),
        }
        season_ok = True
        for key in ("p_platt", "p_raw"):
            mine = test[key].to_numpy()
            full_mine, full_st = (
                float(_ll(mine[m], y[m]).mean()),
                float(_ll(stored[key][ms], sy[ms]).mean()),
            )
            r: dict[str, Any] = {
                "mine": full_mine,
                "stored": full_st,
                "abs_diff": abs(full_mine - full_st),
                "within_1e-6": abs(full_mine - full_st) <= TOL_P0,
                "within_0.005_hazard2_precedent_info": abs(full_mine - full_st) <= 0.005,
            }
            if ent["rows_aligned_matching_games"] and same_m.any():
                sub_mine = float(_ll(mine[same_m], y[same_m]).mean())
                sub_st = float(_ll(stored[key][same_s], sy[same_s]).mean())
                r["matching_games"] = {
                    "n": int(same_m.sum()),
                    "mine": sub_mine,
                    "stored": sub_st,
                    "abs_diff": abs(sub_mine - sub_st),
                }
            ent[key] = r
            season_ok &= bool(abs(full_mine - full_st) <= TOL_P0_BY_SEASON[s])
        ent["season_ok"] = season_ok
        detail[str(s)] = ent
        ok &= season_ok
    return {"ok": bool(ok), "detail": detail}


def check_snapshots(games: pl.DataFrame, snaps: pl.DataFrame) -> dict[str, Any]:
    """Check 2: T-60 coverage of games, morning coverage of game days (and b2b-day thinness)."""
    g = games.filter(pl.col("season").is_in(list(SEASONS))).select(
        "game_id", pl.col("game_date").cast(pl.Date), "season", "home_team", "away_team"
    )
    views = snapshot_views(snaps)
    tg = team_games(games).filter(pl.col("season").is_in(list(SEASONS)))
    b2b_games = set(tg.filter(pl.col("is_b2b_second"))["game_id"].to_list())
    t60 = set(views["t60"]["game_id"].to_list())
    mor = set(views["morning"]["game_id"].to_list())
    evn = set(views["evening"]["game_id"].to_list())
    detail: dict[str, Any] = {}
    ok = True
    for label, sub in (("all", g), *((str(s), g.filter(pl.col("season") == s)) for s in SEASONS)):
        gids = sub["game_id"].to_list()
        days = sub.group_by("game_date").agg(pl.col("game_id"))
        day_has = [any(x in mor for x in ids) for ids in days["game_id"].to_list()]
        t60_share = sum(x in t60 for x in gids) / max(len(gids), 1)
        day_share = sum(day_has) / max(len(day_has), 1)
        detail[label] = {
            "games": len(gids),
            "t60_games": sum(x in t60 for x in gids),
            "t60_share": t60_share,
            "game_days": len(day_has),
            "morning_days": int(sum(day_has)),
            "morning_day_share": day_share,
            "morning_game_share": sum(x in mor for x in gids) / max(len(gids), 1),
            "evening_game_share": sum(x in evn for x in gids) / max(len(gids), 1),
        }
        ok &= t60_share >= T60_SHARE_MIN and day_share >= MORNING_DAY_SHARE_MIN
    m = views["morning"].group_by("game_id").agg(pl.len().alias("n"))
    m = m.with_columns(pl.col("game_id").is_in(list(b2b_games)).alias("b2b"))
    med: dict[str, float | None] = {}
    for k, v in (("b2b_games", True), ("other_games", False)):
        sub = m.filter(pl.col("b2b") == v)
        med[k] = float(sub["n"].median()) if sub.height else None  # type: ignore[arg-type]
    detail["morning_rows_per_snapshot_median"] = med
    detail["pre_tip_gate"] = "usable_report_rows(tip_source=real): as_of <= real tip - 60 min"
    return {"ok": bool(ok), "detail": detail}


def check_defined(frame: pl.DataFrame) -> dict[str, Any]:
    """Check 3: team_b2b_sit defined (>= MIN_B2B prior b2b games) for >= 95% of rows."""
    t = frame.filter(pl.col("season").is_in(list(SEASONS)))
    n = t.height
    nb = t["n_b2b_prior"].to_numpy().astype(float)
    detail: dict[str, Any] = {
        "n_rows": n,
        "defined_share": float(t["b2b_defined"].mean()) if n else float("nan"),  # type: ignore[arg-type]
        "fallback_rows": int((~t["b2b_defined"]).sum()) if n else 0,
        "n_b2b_prior_quantiles": {
            q: float(np.quantile(nb, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
        }
        if n
        else {},
        "what_if_share_at_threshold": {
            str(k): float((nb >= k).mean()) for k in (5, 8, 10, 12, 15, 20)
        }
        if n
        else {},
        "league_rate_null_rows": int(t["team_b2b_sit"].is_null().sum()),
        "by_season_defined_share": {
            str(s): float(t.filter(pl.col("season") == s)["b2b_defined"].mean())  # type: ignore[arg-type]
            for s in SEASONS
            if t.filter(pl.col("season") == s).height
        },
    }
    return {"ok": bool(n and detail["defined_share"] >= DEFINED_SHARE_MIN), "detail": detail}


AUDIT_NULLS: dict[str, str] = {
    "team_b2b_sit(fallback to league)": "fallback",
    "team_b2b_sit(null)": "team_b2b_sit",
    "tag60_code(no T-60 snapshot)": "tag60_code",
    "tagm_code(no morning snapshot)": "tagm_code",
    "on_morning_report(no morning snapshot)": "on_morning_report",
    "on_evening_report(no evening snapshot)": "on_evening_report",
    "terc(no prior-season win%)": "terc",
    "star": "star",
    "b2b_star": "b2b_star",
}


def missingness(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Null rate of every term by realised-minutes bucket (eligible rows, 2023/2024)."""
    t = frame.filter(pl.col("season").is_in(list(SEASONS)))
    mins = t["min_real"].to_numpy()
    out: list[dict[str, Any]] = []
    for label, col in AUDIT_NULLS.items():
        null = (~t["b2b_defined"]).to_numpy() if col == "fallback" else t[col].is_null().to_numpy()
        for lo, hi in BUCKETS:
            m = (mins >= lo) & (mins < hi)
            out.append(
                {
                    "col": label,
                    "bucket": bucket_name(lo, hi),
                    "n": int(m.sum()),
                    "null_rate": float(null[m].mean()) if m.any() else float("nan"),
                }
            )
    return out


def max_gap_pp(audit: list[dict[str, Any]]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in audit:
        if r["n"] > 0:
            by.setdefault(r["col"], []).append(r["null_rate"])
    return {c: 100.0 * (max(v) - min(v)) for c, v in by.items()}


def _term_equal(a: pl.DataFrame, b: pl.DataFrame, keys: pl.DataFrame | None = None) -> bool:
    """Byte-identical term frames (optionally restricted to the given (game, player) keys)."""
    if keys is not None:
        a, b = keys.join(a, on=KEYS, how="inner"), keys.join(b, on=KEYS, how="inner")
    return bool(a.sort(KEYS).equals(b.sort(KEYS)))


def planted_tests(
    rows: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    snaps: pl.DataFrame,
    prior_wp: pl.DataFrame,
    n_games: int = 4,
    seed: int = 7,
) -> dict[str, bool]:
    """Same-game, future and post-tip plants must leave the pre-tip terms byte-identical;
    two positive controls (past edit, snapshot edit) must change them (the test can fail)."""
    rng = np.random.default_rng(seed)
    base = build_terms(rows, games, pgs, snaps, prior_wp)
    r = rows.with_columns(pl.col("game_date").cast(pl.Date))

    def box_edit(df: pl.DataFrame, mask: pl.Series) -> pl.DataFrame:
        n = df.height
        mins = np.where(rng.random(n) < 0.3, 0.0, rng.uniform(1, 48, n))
        pts = rng.integers(0, 60, n).astype(float)
        return df.with_columns(
            pl.when(mask).then(pl.Series(mins)).otherwise(pl.col("minutes")).alias("minutes"),
            pl.when(mask)
            .then(pl.Series(pts))
            .otherwise(pl.col("pts").cast(pl.Float64))
            .alias("pts"),
        )

    pgs = pgs.with_columns(pl.col("minutes").cast(pl.Float64))
    # same game: edit one game's own box score; that game's term rows are unchanged
    cand = r.filter(pl.col("season") == REPORT_SEASON)["game_id"].unique().sort().to_numpy()
    picks = rng.choice(cand, size=min(n_games, len(cand)), replace=False)
    ok_same = True
    for g in picks:
        edited = box_edit(pgs, pgs["game_id"] == g)
        ok_same &= _term_equal(
            base,
            build_terms(rows, games, edited, snaps, prior_wp),
            rows.filter(pl.col("game_id") == g).select(KEYS),
        )
    # future: edit every box score and report row dated on/after the cut
    dates = r["game_date"].sort()
    cut = dates[len(dates) // 2]
    gdate = games.select("game_id", pl.col("game_date").cast(pl.Date))
    pg_d = pgs.join(gdate, on="game_id", how="left")
    fut = box_edit(pg_d, pg_d["game_date"] >= cut).drop("game_date")
    sn_fut = snaps.with_columns(
        pl.when(pl.col("game_date") >= cut)
        .then(pl.Series(rng.choice(list(STATUS_CODE), snaps.height)))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    ok_future = _term_equal(
        base,
        build_terms(rows, games, fut, sn_fut, prior_wp),
        r.filter(pl.col("game_date") < cut).select(KEYS),
    )
    # post-tip snapshots: rows stamped after tip-60 (and after tip) change nothing
    tips = snaps.select("game_id", "tip_et").unique("game_id")
    sample = r["game_id"].unique().sort().to_numpy()
    sample = rng.choice(sample, size=min(40, len(sample)), replace=False)
    plant = (
        r.filter(pl.col("game_id").is_in(list(sample)))
        .join(tips, on="game_id", how="inner")
        .with_columns(pl.col("tip_et").fill_null(pl.col("game_date").cast(pl.Datetime("us"))))
    )
    late = pl.concat(
        [
            plant.select(
                "game_id",
                "player_id",
                pl.lit("questionable").alias("status"),
                (pl.col("tip_et") - pl.duration(minutes=30)).alias("as_of"),
                "game_date",
                "tip_et",
            ),
            plant.select(
                "game_id",
                "player_id",
                pl.lit("doubtful").alias("status"),
                (pl.col("tip_et") + pl.duration(minutes=10)).alias("as_of"),
                "game_date",
                "tip_et",
            ),
        ]
    ).select(snaps.columns)
    ok_post = _term_equal(
        base,
        build_terms(rows, games, pgs, pl.concat([snaps, late.cast(snaps.schema)]), prior_wp),
    )
    # positive controls: the plants above must be able to fail
    gdates = games.select("game_id", pl.col("game_date").cast(pl.Date))
    pg_d2 = pgs.join(gdates, on="game_id", how="left")
    past = box_edit(pg_d2, pg_d2["game_date"] < cut).drop("game_date")
    ctrl_past = not _term_equal(
        base,
        build_terms(rows, games, past, snaps, prior_wp),
        r.filter(pl.col("game_date") >= cut).select(KEYS),
    )
    sn_ctrl = snaps.with_columns(pl.lit("questionable").alias("status"))
    ctrl_snap = not _term_equal(base, build_terms(rows, games, pgs, sn_ctrl, prior_wp))
    return {
        "planted_same_game_box_score": bool(ok_same),
        "planted_future_box_scores_and_reports": bool(ok_future),
        "planted_post_tip_snapshots": bool(ok_post),
        "control_past_edit_changes_later_rows": bool(ctrl_past),
        "control_snapshot_edit_changes_tags": bool(ctrl_snap),
    }


def check_missingness_and_plants(
    prep: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = missingness(prep["frame"])
    gaps = max_gap_pp(audit)
    miss = {
        "ok": bool(gaps and max(gaps.values()) <= GAP_PP),
        "detail": {"max_gap_pp_by_col": gaps},
        "audit": audit,
    }
    plants = planted_tests(
        prep["rows"], prep["games"], prep["pgs"], prep["snaps"], prep["prior_wp"]
    )
    return miss, {"ok": bool(all(plants.values())), "detail": plants}


def window_what_if(prep: dict[str, Any]) -> dict[str, Any]:
    """Information only (not a rule): share of eligible rows with n_b2b_prior >= k when the team
    window is longer than 82 games. Shows what the data can support, changes nothing."""
    tg = team_games(prep["games"])
    pgp = _played_states(prep["pgs"], tg)
    keys = (
        prep["rows"]
        .select("game_id", "team_id", "season")
        .with_columns(pl.col("team_id").cast(pl.Int64))
    )
    out: dict[str, Any] = {}
    for w in (82, 123, 164):
        _, rates = team_rates(tg, pgp, window=w)
        t = keys.join(rates, on=["game_id", "team_id"], how="left").filter(
            pl.col("season").is_in(list(SEASONS))
        )
        nb = t["n_b2b_prior"].to_numpy().astype(float)
        out[str(w)] = {
            "median_n_b2b_prior": float(np.median(nb)),
            "share_ge_20": float((nb >= 20).mean()),
            "share_ge_15": float((nb >= 15).mean()),
            "share_ge_10": float((nb >= 10).mean()),
            "share_by_season": {
                str(s): {
                    f"ge_{k}": float(
                        (t.filter(pl.col("season") == s)["n_b2b_prior"].to_numpy() >= k).mean()
                    )
                    for k in (10, 15, 20)
                }
                for s in SEASONS
            },
        }
    return out


def star_rate_sanity(prep: dict[str, Any]) -> dict[str, Any]:
    """Information only: pooled star sit rate on b2b second nights vs other nights from the
    harness's own opportunity table (regular season, 2023/2024); the descriptive tables gave
    0.124 vs 0.068 (reports/fan_answers_2026-10-11.md A1)."""
    tg = team_games(prep["games"])
    pgp = _played_states(prep["pgs"], tg)
    _, cand = star_opportunities(tg, pgp)
    c = cand.join(tg.select("game_id", "team_id", "season"), on=["game_id", "team_id"]).filter(
        pl.col("season").is_in(list(SEASONS)) & pl.col("game_id").str.starts_with("002")
    )
    b = c.filter(pl.col("is_b2b_second"))
    o = c.filter(~pl.col("is_b2b_second"))
    return {
        "n_b2b": b.height,
        "sit_rate_b2b": float(b["sat"].mean()),  # type: ignore[arg-type]
        "n_other": o.height,
        "sit_rate_other": float(o["sat"].mean()),  # type: ignore[arg-type]
    }


def b2b_agreement(frame: pl.DataFrame) -> dict[str, Any]:
    t = frame.filter(pl.col("season").is_in(list(SEASONS)))
    mine = t["is_b2b_second"].to_numpy().astype(bool)
    theirs = t["b2b"].to_numpy().astype(bool)
    return {
        "n": t.height,
        "share_agree": float((mine == theirs).mean()) if t.height else float("nan"),
        "share_mine": float(mine.mean()) if t.height else float("nan"),
        "share_p1_b2b": float(theirs.mean()) if t.height else float("nan"),
    }


def do_gate(prep: dict[str, Any], stored: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    if stored is None:
        z = np.load(STORED, allow_pickle=True)
        stored = {k: z[k] for k in z.files}
    gate: dict[str, Any] = {}
    gate["1_p0_reproduces"] = check_p0(prep["frame"], stored)
    gate["2_snapshot_coverage"] = check_snapshots(prep["games"], prep["snaps"])
    gate["3_team_b2b_sit_defined"] = check_defined(prep["frame"])
    miss, plants = check_missingness_and_plants(prep)
    gate["4a_missingness_gap"] = miss
    gate["4b_planted"] = plants
    gate["info_b2b_agreement"] = b2b_agreement(prep["frame"])
    gate["info_window_what_if"] = window_what_if(prep)
    gate["info_star_sit_rates"] = star_rate_sanity(prep)
    gate["all_ok"] = bool(
        all(v["ok"] for k, v in gate.items() if isinstance(v, dict) and "ok" in v)
    )
    return gate


def write_gate(gate: dict[str, Any], sha: str, allow_draft: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "gate.json"
    res = {
        "status": "GATE_PASSED" if gate["all_ok"] else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "sha256": sha,
        "git_sha": git_sha(),
        "seasons": list(SEASONS),
        "unstated_details": list(UNSTATED_DETAILS),
        "tests": [
            {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
        ],
        "gate": gate,
    }
    path.write_text(json.dumps(res, indent=1, default=float))
    return path


# --------------------------------------------------------------------------- fits


def arm_features(family: str) -> dict[str, list[str]]:
    """Feature lists per arm for one family (P1 is shared; P3 swaps in the permuted rate)."""
    terms = [*TERM_B2B_SIT, *TERM_STAR, *TERM_TAG[family], *TERM_REPORT]
    sfx = "T60" if family == "T-60" else "M"
    p3 = ["team_b2b_sit_perm" if c == "team_b2b_sit" else c for c in terms]
    p4 = [c for c in terms if c not in TERM_TAG[family]]
    return {
        f"P2_{sfx}": [*BASE_FEATURES, *terms],
        f"P3_{sfx}": [*BASE_FEATURES, *p3],
        f"P4_{sfx}": [*BASE_FEATURES, *p4],
    }


def all_arms(families: tuple[str, ...] = FAMILIES) -> dict[str, list[str]]:
    arms: dict[str, list[str]] = {"P1": list(BASE_FEATURES)}
    for f in families:
        arms.update(arm_features(f))
    return arms


META_COLS = {
    "is_b2b_second": "m_b2b",
    "q60": "m_q60",
    "qm": "m_qm",
    "star": "m_star",
    "terc": "m_terc",
    "games_into_season": "m_gis",
}


def walk_forward(
    frame: pl.DataFrame,
    arms: dict[str, list[str]],
    cfg: ContextResidualConfig,
    ckpt: Path | None = None,
    only_start: dt.date | None = None,
    n_estimators: int | None = None,
) -> dict[str, np.ndarray]:
    """Month-block walk-forward on 2023/2024 test rows; train strictly before the block (2022 is
    warm-up). Per-row predictions of every arm, checkpointed per block."""
    if max(TEST_SEASONS) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    assert_seasons(frame)
    test_all = frame.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    parts: list[dict[str, np.ndarray]] = []
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        if only_start is not None and start != only_start:
            continue
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files})
            _log(f"block {bi + 1}/{len(blocks)} {start} loaded from checkpoint")
            continue
        block = frame.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train = frame.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN_ROWS:
            continue
        ytr = train["played"].to_numpy().astype(int)
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy(),
            "pid": block["player_id"].to_numpy(),
            "season": block["season"].to_numpy(),
            "y": block["played"].to_numpy(),
            "p0": block["p_platt"].to_numpy(),
            "p0raw": block["p_raw"].to_numpy(),
        }
        for src, dst in META_COLS.items():
            if src in block.columns:
                part[dst] = block[src].cast(pl.Float64).to_numpy()
        params = _params(cfg, "binary")
        if n_estimators is not None:
            params["n_estimators"] = n_estimators
        for arm, names in arms.items():
            clf = lgb.LGBMClassifier(**params).fit(_matrix(train, names), ytr)  # type: ignore[arg-type]
            part[arm] = np.asarray(clf.predict_proba(_matrix(block, names)))[:, 1]
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            _savez(path, part)
        parts.append(part)
        _log(f"block {bi + 1}/{len(blocks)} {start} n_test={block.height} train={train.height}")
    if not parts:
        raise ValueError("no month block had enough training rows")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


# --------------------------------------------------------------------------- analysis


def _pair(
    a: np.ndarray, b: np.ndarray, y: np.ndarray, gid: np.ndarray, n_boot: int
) -> dict[str, float]:
    d = _ll(a, y) - _ll(b, y)
    c = _ci(d, gid, n_boot)
    return {
        "dlogloss": c["point"],
        "ci_lo": c["lo"],
        "ci_hi": c["hi"],
        "p": boot_p_value(d, gid, n_boot),
    }


def family_arms(family: str) -> dict[str, str]:
    sfx = "T60" if family == "T-60" else "M"
    return {"P2": f"P2_{sfx}", "P3": f"P3_{sfx}", "P4": f"P4_{sfx}"}


def slice_masks(z: dict[str, np.ndarray], family: str) -> dict[str, np.ndarray]:
    tag = z["m_q60"] if family == "T-60" else z["m_qm"]
    out: dict[str, np.ndarray] = {
        "b2b_second_nights": z["m_b2b"] > 0.5,
        "questionable_rows": np.nan_to_num(tag) > 0.5,
        "star_rows": z["m_star"] > 0.5,
        "first15_team_games": z["m_gis"] < 15,
    }
    for t, name in ((0.0, "weak"), (1.0, "middle"), (2.0, "contender")):
        out[f"strength_{name}"] = np.nan_to_num(z["m_terc"], nan=-1.0) == t
    return {k: np.asarray(v, dtype=bool) for k, v in out.items()}


def analyze(z: dict[str, np.ndarray], n_boot: int | None = None) -> dict[str, Any]:
    """Main table, production gap, slices and the pass rule from per-row predictions."""
    n_boot = N_BOOT if n_boot is None else n_boot
    main: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    pairs: dict[str, dict[str, Any]] = {}
    for season in SEASONS:
        sm = z["season"] == season
        y, gid = z["y"][sm].astype(bool), z["gid"][sm]
        p_values: list[float] = []
        fam_pairs: dict[str, dict[str, float]] = {}
        for fam in FAMILIES:
            arms = family_arms(fam)
            fam_pairs[fam] = _pair(z[arms["P2"]][sm], z["P1"][sm], y, gid, n_boot)
            p_values.append(fam_pairs[fam]["p"])
        adj = bh_adjust(np.array(p_values))
        for fam, p_bh in zip(FAMILIES, adj, strict=True):
            fam_pairs[fam]["p_bh"] = float(p_bh)
        for fam in FAMILIES:
            arms = family_arms(fam)
            cols: dict[str, np.ndarray] = {
                "P0": z["p0"],
                "P1": z["P1"],
                "P2": z[arms["P2"]],
                "P3": z[arms["P3"]],
                "P4": z[arms["P4"]],
            }
            for arm, p in cols.items():
                ps = p[sm]
                row: dict[str, Any] = {
                    "family": fam,
                    "season": season,
                    "arm": arm,
                    "n_rows": int(sm.sum()),
                    "n_games": int(len(np.unique(gid))),
                    "logloss": float(_ll(ps, y).mean()),
                    "brier": float(((ps - y) ** 2).mean()),
                    "ece": ece(ps, y.astype(float)),
                }
                if arm in ("P2", "P3", "P4"):
                    pr = _pair(ps, z["P1"][sm], y, gid, n_boot)
                    row.update(
                        dlogloss=pr["dlogloss"], ci_lo=pr["ci_lo"], ci_hi=pr["ci_hi"], p=pr["p"]
                    )
                    if arm == "P2":
                        row["p_bh"] = fam_pairs[fam]["p_bh"]
                main.append(row)
            for name, a, b in (
                ("P2-P0", "P2", "P0"),
                ("P2-P3", "P2", "P3"),
                ("P2-P4", "P2", "P4"),
            ):
                pr = _pair(cols[a][sm], cols[b][sm], y, gid, n_boot)
                pairs.setdefault(f"{fam}|{season}", {})[name] = pr
            for sname, mask in slice_masks(z, fam).items():
                mm = mask & sm
                if mm.sum() == 0:
                    continue
                ym, gm = z["y"][mm].astype(bool), z["gid"][mm]
                pr = _pair(z[arms["P2"]][mm], z["P1"][mm], ym, gm, n_boot)
                slices.append(
                    {
                        "family": fam,
                        "season": season,
                        "slice": sname,
                        "arm": "P2",
                        "n": int(mm.sum()),
                        "dlogloss": pr["dlogloss"],
                        "ci_lo": pr["ci_lo"],
                        "ci_hi": pr["ci_hi"],
                    }
                )
    return {
        "main": main,
        "slices": slices,
        "pairs": pairs,
        "verdict": pass_rule(main, slices, pairs),
    }


def pass_rule(
    main: list[dict[str, Any]], slices: list[dict[str, Any]], pairs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Per family, both seasons (2023 selects, 2024 confirms); every condition must hold."""
    out: dict[str, Any] = {}
    for fam in FAMILIES:
        per: dict[str, Any] = {}
        for season in SEASONS:
            rows = {r["arm"]: r for r in main if r["family"] == fam and r["season"] == season}
            p2, p1 = rows["P2"], rows["P1"]
            bad = [
                s["slice"]
                for s in slices
                if s["family"] == fam
                and s["season"] == season
                and s["n"] >= MIN_SLICE_N
                and s["dlogloss"] > SLICE_TOL
            ]
            vs_p3 = pairs[f"{fam}|{season}"]["P2-P3"]["dlogloss"]
            checks = {
                "floor": p2["dlogloss"] <= DELTA_FLOOR,
                "ci_below_0": p2["ci_hi"] < 0,
                "bh_p": p2["p_bh"] < 0.05,
                "ece": p2["ece"] - p1["ece"] <= ECE_TOL,
                "slices": not bad,
                "beats_placebo": vs_p3 <= PLACEBO_MARGIN,
            }
            per[str(season)] = {"checks": checks, "bad_slices": bad, "pass": all(checks.values())}
        out[fam] = {"seasons": per, "pass": all(v["pass"] for v in per.values())}
    return out


# --------------------------------------------------------------------------- report


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _fmt(x: Any, nd: int = 4) -> str:
    return "-" if x is None else f"{x:+.{nd}f}"


def render(res: dict[str, Any]) -> str:
    o = ["# PPLAY_CONTEXT: results (docs/prereg/PPLAY_CONTEXT.md)", ""]
    o += [f"Frozen sha256 `{res['sha256']}`; git {res['git_sha']}; seeds {res['seeds']}.", ""]
    for fam, v in res["verdict"].items():
        o.append(f"* {fam}: **{'PASS' if v['pass'] else 'no pass'}**")
        for s, d in v["seasons"].items():
            fails = ",".join(k for k, ok in d["checks"].items() if not ok) or "none"
            o.append(f"  * {s}: failed checks: {fails}; bad slices: {d['bad_slices'] or 'none'}")
    o += [
        "",
        "## Paired log loss (arm minus P1; game-clustered 95% CI; BH over the two families)",
        "",
    ]
    rows = []
    for r in res["main"]:
        ci = (
            f"{_fmt(r.get('dlogloss'))} [{_fmt(r.get('ci_lo'))},{_fmt(r.get('ci_hi'))}]"
            if "dlogloss" in r
            else "-"
        )
        p = (
            "-"
            if "p" not in r
            else f"{r['p']:.3f}" + (f" ({r['p_bh']:.3f})" if "p_bh" in r else "")
        )
        rows.append(
            [
                r["family"],
                str(r["season"]),
                r["arm"],
                f"{r['n_rows']} ({r['n_games']})",
                f"{r['logloss']:.4f}",
                ci,
                p,
                f"{r['brier']:.4f}",
                f"{r['ece']:.4f}",
            ]
        )
    o += _table(
        [
            "family",
            "season",
            "arm",
            "n (games)",
            "logloss",
            "d vs P1 [CI]",
            "p (BH)",
            "Brier",
            "ECE",
        ],
        rows,
    )
    o += ["", "## Pair contrasts (production gap and which term carries it)", ""]
    prow = []
    for key, d in res["pairs"].items():
        fam, season = key.split("|")
        for name, pr in d.items():
            prow.append(
                [
                    fam,
                    season,
                    name,
                    f"{_fmt(pr['dlogloss'])} [{_fmt(pr['ci_lo'])},{_fmt(pr['ci_hi'])}]",
                ]
            )
    o += _table(["family", "season", "contrast", "dlogloss [CI]"], prow)
    o += ["", "## Slices (P2 minus P1)", ""]
    o += _table(
        ["family", "season", "slice", "n", "dlogloss [CI]"],
        [
            [
                s["family"],
                str(s["season"]),
                s["slice"],
                str(s["n"]),
                f"{_fmt(s['dlogloss'])} [{_fmt(s['ci_lo'])},{_fmt(s['ci_hi'])}]",
            ]
            for s in res["slices"]
        ],
    )
    o += ["", "## Gate tests", ""]
    o += _table(["test", "ok"], [[t["test"], str(t["ok"])] for t in res["tests"]])
    return "\n".join(o) + "\n"


def do_report(scores: dict[str, np.ndarray], gate: dict[str, Any], sha: str) -> dict[str, Any]:
    ana = analyze(scores)
    res: dict[str, Any] = {
        "sha256": sha,
        "git_sha": git_sha(),
        "seeds": {"lightgbm": 0, "bootstrap": 0, "placebo": 0, "n_boot": N_BOOT},
        "tests": [
            {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
        ],
        "audit": gate["4a_missingness_gap"]["audit"],
        "unstated_details": list(UNSTATED_DETAILS),
        **ana,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=float))
    REPORT_PATH.write_text(render(res))
    return res


# --------------------------------------------------------------------------- CLI


def _load_cfg() -> ContextResidualConfig:
    import yaml

    raw = yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    return ContextResidualConfig(**raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="prereg document")
    ap.add_argument("--sha", default=None, help="frozen sha256 (64 hex)")
    ap.add_argument("--allow-draft", action="store_true", help="gate stage only: skip hash check")
    ap.add_argument("--db", default="nba.duckdb")
    args = ap.parse_args(argv)
    if args.allow_draft and args.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    configure(args.doc)
    sha = require_frozen(args.sha, args.allow_draft)
    _log(f"prereg sha256 {'(DRAFT, unchecked) ' if args.allow_draft else 'verified '}{sha[:8]}")
    if args.stage == "report":
        gate = json.loads((OUT_DIR / "gate.json").read_text())["gate"]
        z = np.load(CACHE / "scores.npz", allow_pickle=True)
        res = do_report({k: z[k] for k in z.files}, gate, sha)
        _log(f"report written; pass: { {f: v['pass'] for f, v in res['verdict'].items()} }")
        return 0
    with HeavyLock():
        t0 = time.time()
        prep = prepare(args.db)
        _log(f"prepared in {time.time() - t0:.0f}s: {prep['frame'].height} rows")
        gate = do_gate(prep)
        write_gate(gate, sha, args.allow_draft)
        if not gate["all_ok"]:
            _log("GATE FAILED: stop, no model result")
            return 2
        if args.stage == "gate":
            return 0
        cfg = _load_cfg()
        if args.stage == "smoke":
            arms = all_arms()
            part = walk_forward(
                prep["frame"],
                {k: arms[k] for k in ("P1", "P2_T60")},
                cfg,
                only_start=dt.date(2023, 12, 1),
            )
            ll = {k: float(_ll(part[k], part["y"].astype(bool)).mean()) for k in ("P1", "P2_T60")}
            _log(f"smoke block log loss {ll} (n={len(part['y'])}); nothing written")
            return 0
        scores = walk_forward(prep["frame"], all_arms(), cfg, ckpt=CACHE / "blocks")
        CACHE.mkdir(parents=True, exist_ok=True)
        _savez(CACHE / "scores.npz", scores)
        _log("scores written; run `report` next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
