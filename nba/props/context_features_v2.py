"""Context-residual v2 feature builder and Colab export (seasons 2022-2024 ONLY).

v2 = production v1 features (``nba.props.context_residual``) PLUS the groups below. Every v2 feature
is strictly as-of: it uses games dated BEFORE the target game's date, the official pre-tip injury
report (latest snapshot stamped at or before tip-off proxy minus 60 minutes, via
``nba.sim.usage_redistribution.usable_report_rows``), or public schedule/static facts. Nothing uses
tonight's box score, minutes, lineup, starter flag or possessions. DNP rows (minutes NULL, stats 0)
are excluded from every rate; they are used only as an ABSENCE signal about PAST games.

Groups (letters match the task brief; ``V2_GROUPS`` is the machine-readable list)::

    a  own status: pre-tip report status, absent/OUT last game -> returning, streaks
    b  teammates RETURNING (rotation players absent/OUT last team game, not OUT now): their minutes,
       usage and stat averages (the negative of v1's "vacated")
    c  possession-derived per-player rates (played-only, recency-weighted, shrunk to as-of league):
       on-court usage, shot-zone mix, FTA rate, assisted-make share, TOV rate, OREB/DRB per 36
    d  schedule density: games in last 4/6/7 days, 3-in-4, back-to-back (second / first night),
       travel miles over the last 7 days, opponent schedule density
    e  opponent defence vs the player's position group: as-of mean of (stat - player's own recency
       mean) allowed to that group, shrunk toward 0
    f  game environment: as-of team scoring environment total (nba.parlay.game_model) and the
       implied team/opponent points with the Elo margin
    g  draft slot x rookie flag
    h  national TV flag
    i  opponent-adjusted as-of ridge: player and opponent-team effects on per-36 rates
       (refit monthly on prior months only, 540-day lookback)
    j  overtime-normalised baselines: per-48-minute decayed means (game length from minutes)

Documented limitations: the Elo margin is the MOV-Elo ``exp_margin`` already in v1 (the injury-Elo
OOF used by ``game_model.fit_game_model`` is not available as-of for every row and its OLS map is
fit on all seasons <= 2024, which would leak forward inside a 2022-2024 export); the as-of total
feature needs no fit (an affine map is irrelevant to trees). Travel uses approximate home-arena
coordinates (neutral-site games count as the home team's arena). The report cutoff is the same
19:00 ET proxy as v1; matinee tips are a known limitation. ``next-day game`` uses the public
schedule only. Season 2025 is never loaded by default (SQL filter ``season <= 2024``); the opt-in
``include_holdout_2025`` path (experiment 3, docs/CTXRES_V3.md) writes a separate file and leaves
the default output byte-identical.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.parlay.game_model import asof_total_features
from nba.props.baselines import _DEFAULT_STD
from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ROTATION_MIN40,
    ContextResidualConfig,
    _sel,
    build_features,
    flagged_from_availability,
    player_states,
    stat_feature_names,
)
from nba.sim.usage_redistribution import ReportTriggerConfig, usable_report_rows

MAX_SEASON = 2024  # season 2025 is never loaded
EXPORT_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
# Experiment 3 only (docs/CTXRES_V3.md): opt-in via ``include_holdout_2025``; default unchanged.
MAX_SEASON_V3 = 2025
EXPORT_SEASONS_V3: tuple[int, ...] = (2022, 2023, 2024, 2025)
V3_PARQUET = "ctxres_v3_with2025.parquet"
HALFLIFE = 10.0
SEED = 20261008
STATUS_ORD: dict[str, int] = {
    "available": 1,
    "probable": 2,
    "questionable": 3,
    "doubtful": 4,
    "out": 5,
}
OUT_ORD = STATUS_ORD["out"]
K_ON = 100.0  # pseudo on-court possessions (decayed-sum units)
K_FGA = 20.0
K_MAKES = 10.0
K_MIN = 100.0
RIDGE_ALPHA = 50.0
RIDGE_MIN_ROWS = 3000
RIDGE_LOOKBACK_DAYS = 540
K_OPP = 40.0  # pseudo player-games for the opponent-vs-position effect
ABSENCE_CAP = 30

#: group -> (common columns, per-stat column templates with ``{stat}``)
V2_GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "a": (
        (
            "own_listed",
            "own_status_ord",
            "own_out_prev",
            "absent_prev",
            "abs_streak_prev",
            "since_return",
            "last_abs_len",
            "own_returning",
        ),
        (),
    ),
    "b": (("ret_n", "ret_min", "ret_usage"), ("ret_{stat}",)),
    "c": (
        (
            "pr_usage",
            "pr_zone_rim",
            "pr_zone_mid",
            "pr_zone_c3",
            "pr_zone_a3",
            "pr_fta_rate",
            "pr_ast_share",
            "pr_tov_rate",
            "pr_oreb36",
            "pr_dreb36",
            "pr_on_poss_w",
        ),
        (),
    ),
    "d": (
        (
            "sch_n_prev4",
            "sch_n_prev7",
            "sch_three_in_four",
            "sch_four_in_six",
            "sch_b2b_second",
            "sch_b2b_first",
            "sch_days_to_next",
            "sch_travel_leg",
            "sch_travel7",
            "sch_opp_n_prev7",
            "sch_opp_three_in_four",
            "sch_opp_travel7",
        ),
        (),
    ),
    "e": ((), ("opp_pos_{stat}",)),
    "f": (("game_total_feat", "implied_team_pts", "implied_opp_pts"), ()),
    "g": (
        ("draft_pick_f", "ln_pick", "yrs_since_draft", "is_rookie", "rookie_ln_pick", "undrafted"),
        (),
    ),
    "h": (("nat_tv", "nat_tv_major", "tv_home"), ()),
    "i": ((), ("ridge_player_{stat}", "ridge_opp_{stat}")),
    "j": (("n48_min",), ("n48_{stat}", "rate48_{stat}")),
}

#: names that may never be model inputs: same-game box-score/label/post-tip quantities
LEAK_DENY = (
    r"^(pts|reb|ast|fg3m|stl|blk|tov|fgm|fga|fg3a|ftm|fta|oreb|dreb|pf|minutes|min|starter|y"
    r"|resid|label|plus_minus)$|(^|_)(actual|post|tonight|final)(_|$)"
)


def group_columns(group: str, stat: str) -> list[str]:
    common, per = V2_GROUPS[group]
    return [*common, *[t.format(stat=stat) for t in per]]


def v2_feature_names(stat: str, groups: tuple[str, ...] | None = None) -> list[str]:
    gs = groups if groups is not None else tuple(V2_GROUPS)
    return [c for g in gs for c in group_columns(g, stat)]


def all_v2_columns() -> list[str]:
    out: list[str] = []
    for s in PROP_STATS:
        for c in v2_feature_names(s):
            if c not in out:
                out.append(c)
    return out


def all_v1_columns() -> list[str]:
    out: list[str] = []
    for s in PROP_STATS:
        for c in stat_feature_names(s):
            if c not in out:
                out.append(c)
    return out


# --------------------------------------------------------------------------- helpers

#: approximate home-arena (lat, lon) by NBA team id
ARENAS: dict[int, tuple[float, float]] = {
    1610612737: (33.757, -84.396),
    1610612738: (42.366, -71.062),
    1610612739: (41.496, -81.688),
    1610612740: (29.949, -90.082),
    1610612741: (41.881, -87.674),
    1610612742: (32.790, -96.810),
    1610612743: (39.749, -105.008),
    1610612744: (37.768, -122.388),
    1610612745: (29.751, -95.362),
    1610612746: (34.043, -118.267),
    1610612747: (34.043, -118.267),
    1610612748: (25.781, -80.187),
    1610612749: (43.045, -87.917),
    1610612750: (44.980, -93.276),
    1610612751: (40.683, -73.975),
    1610612752: (40.751, -73.994),
    1610612753: (28.539, -81.384),
    1610612754: (39.764, -86.156),
    1610612755: (39.901, -75.172),
    1610612756: (33.446, -112.071),
    1610612757: (45.532, -122.667),
    1610612758: (38.580, -121.500),
    1610612759: (29.427, -98.438),
    1610612760: (35.463, -97.515),
    1610612761: (43.643, -79.379),
    1610612762: (40.768, -111.901),
    1610612763: (38.898, -77.021),
    1610612765: (42.341, -83.055),
    1610612766: (35.225, -80.839),
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(min(1.0, math.sqrt(a)))


def ew_pre_post(
    keys: np.ndarray, x: np.ndarray, halflife: float = HALFLIFE
) -> tuple[np.ndarray, np.ndarray]:
    """Decayed running sums per group. Rows must be sorted by (group, time).

    ``pre[i]`` = sum over EARLIER rows of the same group (decay ``0.5**(1/halflife)`` per row);
    ``post[i]`` additionally includes row ``i``."""
    d = 0.5 ** (1.0 / halflife)
    n, k = x.shape
    pre = np.zeros((n, k))
    post = np.zeros((n, k))
    s = np.zeros(k)
    for i in range(n):
        if i == 0 or keys[i] != keys[i - 1]:
            s = np.zeros(k)
        pre[i] = s
        s = d * s + x[i]
        post[i] = s
    return pre, post


def _league_prior_asof(played: pl.DataFrame, num: str, den: str, fallback: float) -> np.ndarray:
    """Expanding league ratio sum(num)/sum(den) over played rows dated STRICTLY BEFORE each row."""
    daily = (
        played.group_by("game_date")
        .agg(pl.col(num).sum().alias("_n"), pl.col(den).sum().alias("_d"))
        .sort("game_date")
        .with_columns(
            pl.col("_n").cum_sum().shift(1).alias("_cn"),
            pl.col("_d").cum_sum().shift(1).alias("_cd"),
        )
        .with_columns(
            pl.when(pl.col("_cd").fill_null(0.0) > 50.0)
            .then(pl.col("_cn") / pl.col("_cd"))
            .otherwise(fallback)
            .alias("_p")
        )
    )
    return played.join(daily.select("game_date", "_p"), on="game_date", how="left")["_p"].to_numpy()


# --------------------------------------------------------------------------- (a) status


def pretip_status(
    avail: pl.DataFrame, games: pl.DataFrame, cfg: ReportTriggerConfig
) -> tuple[pl.DataFrame, set[str]]:
    """Per (game_id, player_id) status ordinal from the LATEST usable pre-tip snapshot.

    ``avail``: game_id, player_id, status, as_of, source (raw ``player_availability`` rows).
    Reports stamped after ``game_date + tipoff proxy - lead`` are ignored. Returns the
    table and the set of games that have a usable report (games without one are never imputed)."""
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "status_ord": pl.Int64}
    rows = (
        avail.filter(pl.col("source").is_in(list(cfg.sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            pl.col("game_date").cast(pl.Date),
        )
    )
    usable = usable_report_rows(rows, cfg)
    if usable.height == 0:
        return pl.DataFrame(schema=schema), set()
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_latest"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_latest"))
    snap = snap.with_columns(
        pl.col("status")
        .replace_strict(STATUS_ORD, default=None, return_dtype=pl.Int64)
        .alias("status_ord")
    ).filter(pl.col("status_ord").is_not_null())
    tab = snap.group_by(["game_id", "player_id"]).agg(pl.col("status_ord").max())
    reported = {str(g) for g in latest["game_id"].to_list()}
    return tab.select(list(schema)), reported


def absence_features(
    pgs: pl.DataFrame, games: pl.DataFrame, status: pl.DataFrame, reported: set[str]
) -> pl.DataFrame:
    """Own absence/return features for every (game_id, player_id) row of ``pgs`` (DNP rows
    included as history). Streaks use PRIOR rows only; tonight's status comes from the pre-tip
    report. Columns: group (a)."""
    rows = (
        pgs.select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            "minutes",
        )
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .sort(["player_id", "game_date", "game_id"])
    )
    pid = rows["player_id"].to_numpy()
    absent = (rows["minutes"].fill_null(0.0).to_numpy() <= 0.0).astype(float)
    n = len(pid)
    streak = np.zeros(n)  # consecutive absent rows ending at the PREVIOUS row
    since_ret = np.full(n, float(ABSENCE_CAP))
    last_len = np.zeros(n)
    abs_prev = np.zeros(n)
    run_abs = 0
    run_play = ABSENCE_CAP
    last_abs_run = 0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            run_abs, run_play, last_abs_run = 0, ABSENCE_CAP, 0
        else:
            abs_prev[i] = absent[i - 1]
        streak[i] = min(run_abs, ABSENCE_CAP)
        since_ret[i] = min(run_play, ABSENCE_CAP)
        last_len[i] = min(max(last_abs_run, run_abs), ABSENCE_CAP)
        if absent[i]:
            run_abs += 1
            run_play = 0
        else:
            if run_abs > 0:
                last_abs_run = run_abs
            run_abs = 0
            run_play = min(run_play + 1, ABSENCE_CAP)
    prev_gid = pl.col("game_id").shift(1).over("player_id")
    out_tab = status.filter(pl.col("status_ord") == OUT_ORD).select(
        pl.col("game_id").alias("_pg"), "player_id", pl.lit(1).alias("own_out_prev")
    )
    df = (
        rows.select("game_id", "player_id")
        .with_columns(
            prev_gid.alias("_pg"),
            pl.Series("absent_prev", abs_prev),
            pl.Series("abs_streak_prev", streak),
            pl.Series("since_return", since_ret),
            pl.Series("last_abs_len", last_len),
        )
        .join(out_tab, on=["_pg", "player_id"], how="left")
        .with_columns(pl.col("own_out_prev").fill_null(0))
    )
    cur = status.rename({"status_ord": "own_status_ord"})
    df = df.join(cur, on=["game_id", "player_id"], how="left")
    rep = pl.col("game_id").is_in(sorted(reported))
    df = df.with_columns(
        pl.when(rep)
        .then(pl.col("own_status_ord").fill_null(0))
        .otherwise(None)
        .alias("own_status_ord"),
    ).with_columns(
        pl.when(rep)
        .then((pl.col("own_status_ord") > 0).cast(pl.Int8))
        .otherwise(None)
        .alias("own_listed"),
        pl.when(rep)
        .then(
            (
                ((pl.col("absent_prev") > 0) | (pl.col("own_out_prev") > 0))
                & (pl.col("own_status_ord") != OUT_ORD)
            ).cast(pl.Int8)
        )
        .otherwise(None)
        .alias("own_returning"),
    )
    return df.select("game_id", "player_id", *group_columns("a", "pts"))


# --------------------------------------------------------------------------- (c) possession rates


def possession_rates(played: pl.DataFrame, poss: pl.DataFrame) -> tuple[pl.DataFrame, np.ndarray]:
    """As-of possession-derived rates for each PLAYED row.

    ``played``: sorted by (player_id, game_date, game_id); needs game_id, player_id, game_date,
    minutes, oreb, dreb, fga, tov, fta (box score, PAST games only). ``poss``: one row per
    (game_id, player_id) with on_poss, z_rim, z_mid, z_c3, z_a3, makes, ast_makes (possession
    table counts; TOV/FT-trip possessions carry no shooter id in the data, so usage, TOV and FTA
    rates use the box score and the possession table supplies on-court exposure, zones and
    assisted makes). Rates use decayed sums over STRICTLY EARLIER played games and are shrunk toward
    the as-of league ratio. Also returns the post-game (including the row) usage for as-of
    lookups of absent teammates."""
    cols = ["on_poss", "z_rim", "z_mid", "z_c3", "z_a3", "makes", "ast_makes"]
    box = ["fga", "tov", "fta"]
    df = (
        played.select("game_id", "player_id", "game_date", "minutes", "oreb", "dreb", *box)
        .join(poss.select(["game_id", "player_id", *cols]), on=["game_id", "player_id"], how="left")
        .with_columns([pl.col(c).fill_null(0).cast(pl.Float64) for c in [*cols, *box]])
        .with_columns(
            (pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov")).alias("use_num"),
            (pl.col("z_rim") + pl.col("z_mid") + pl.col("z_c3") + pl.col("z_a3")).alias("fga_p"),
            pl.col("oreb").fill_null(0).cast(pl.Float64),
            pl.col("dreb").fill_null(0).cast(pl.Float64),
            pl.col("minutes").cast(pl.Float64),
        )
    )
    ws = [
        "on_poss",
        "use_num",
        "fga_p",
        "z_rim",
        "z_mid",
        "z_c3",
        "z_a3",
        "fta",
        "makes",
        "ast_makes",
        "tov",
        "minutes",
        "oreb",
        "dreb",
    ]
    ix = {c: i for i, c in enumerate(ws)}
    x = df.select(ws).to_numpy()
    pre, post = ew_pre_post(df["player_id"].to_numpy(), x)

    def lp(num: str, den: str, fb: float) -> np.ndarray:
        return _league_prior_asof(df, num, den, fb)

    def shrunk(
        s: np.ndarray, num: str, den: str, k: float, fb: float, scale: float = 1.0
    ) -> np.ndarray:
        p0 = lp(num, den, fb)
        return scale * (s[:, ix[num]] + k * p0) / (s[:, ix[den]] + k)

    out = pl.DataFrame(
        {
            "game_id": df["game_id"],
            "player_id": df["player_id"],
            "pr_usage": shrunk(pre, "use_num", "on_poss", K_ON, 0.2),
            "pr_zone_rim": shrunk(pre, "z_rim", "fga_p", K_FGA, 0.3),
            "pr_zone_mid": shrunk(pre, "z_mid", "fga_p", K_FGA, 0.2),
            "pr_zone_c3": shrunk(pre, "z_c3", "fga_p", K_FGA, 0.08),
            "pr_zone_a3": shrunk(pre, "z_a3", "fga_p", K_FGA, 0.3),
            "pr_fta_rate": shrunk(pre, "fta", "on_poss", K_ON, 0.04, 100.0),
            "pr_ast_share": shrunk(pre, "ast_makes", "makes", K_MAKES, 0.6),
            "pr_tov_rate": shrunk(pre, "tov", "use_num", K_FGA, 0.12),
            "pr_oreb36": shrunk(pre, "oreb", "minutes", K_MIN, 0.03, 36.0),
            "pr_dreb36": shrunk(pre, "dreb", "minutes", K_MIN, 0.1, 36.0),
            "pr_on_poss_w": pre[:, ix["on_poss"]],
        }
    )
    p0u = lp("use_num", "on_poss", 0.2)
    usage_post = (post[:, ix["use_num"]] + K_ON * p0u) / (post[:, ix["on_poss"]] + K_ON)
    return out, usage_post


# ----- (b) returning teammates


def returning_teammates(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    status: pl.DataFrame,
    reported: set[str],
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    usage_post: np.ndarray,
) -> pl.DataFrame:
    """Per (game_id, team_id): rotation teammates who were absent (DNP row) or OUT on the report
    for the team's PREVIOUS game and are NOT OUT on tonight's pre-tip report.

    Values are the returner's as-of averages (state after his latest game dated before today).
    Games without a usable report get null (never imputed)."""
    g = games.select("game_id", "game_date", "season", "home_team", "away_team")
    tg = pl.concat(
        [
            g.select("game_id", "game_date", "season", pl.col("home_team").alias("team_id")),
            g.select("game_id", "game_date", "season", pl.col("away_team").alias("team_id")),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    tg = tg.with_columns(
        pl.col("game_id").shift(1).over("team_id").alias("prev_game_id"),
        pl.col("season").shift(1).over("team_id").alias("prev_season"),
    ).filter(pl.col("prev_game_id").is_not_null() & (pl.col("prev_season") == pl.col("season")))
    base = pgs.select("game_id", pl.col("player_id").cast(pl.Int64), "team_id", "minutes")
    dnp = base.filter(pl.col("minutes").fill_null(0.0) <= 0.0).select(
        "game_id", "team_id", "player_id"
    )
    outp = (
        status.filter(pl.col("status_ord") == OUT_ORD)
        .join(base.select("game_id", "player_id", "team_id"), on=["game_id", "player_id"])
        .select("game_id", "team_id", "player_id")
    )
    absent_prev = pl.concat([dnp, outp]).unique().rename({"game_id": "prev_game_id"})
    cand = tg.join(absent_prev, on=["prev_game_id", "team_id"]).select(
        "game_id", "team_id", "player_id", "game_date"
    )
    out_now = status.filter(pl.col("status_ord") == OUT_ORD).select(
        "game_id", "player_id", pl.lit(1).alias("_out_now")
    )
    cand = cand.join(out_now, on=["game_id", "player_id"], how="left").filter(
        pl.col("_out_now").is_null()
    )
    post = states["post"]
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "_pd": played["game_date"],
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "r_min": _sel(post, 10.0, "min"),
            "r_usage": usage_post,
            **{f"ret_{s}": _sel(post, 10.0, s) for s in PROP_STATS},
        }
    ).sort("_pd")
    cand = cand.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
    j = cand.join_asof(
        st, left_on="_key", right_on="_pd", by="player_id", strategy="backward"
    ).filter((pl.col("n_after") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= ROTATION_MIN40))
    agg = j.group_by(["game_id", "team_id"]).agg(
        pl.len().alias("ret_n"),
        pl.col("r_min").sum().alias("ret_min"),
        pl.col("r_usage").sum().alias("ret_usage"),
        *[pl.col(f"ret_{s}").sum() for s in PROP_STATS],
    )
    keys = tg.select("game_id", "team_id").filter(pl.col("game_id").is_in(sorted(reported)))
    res = keys.join(agg, on=["game_id", "team_id"], how="left")
    zero = ["ret_n", "ret_min", "ret_usage", *[f"ret_{s}" for s in PROP_STATS]]
    return res.with_columns([pl.col(c).fill_null(0.0).cast(pl.Float64) for c in zero])


# --------------------------------------------------------------------------- (d) schedule


def schedule_features(games: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id) schedule density and travel, from the public schedule.

    Counts use only PRIOR games; ``days_to_next`` / ``b2b_first`` use the next scheduled game's
    date (published schedule, known pre-tip). Travel miles = great-circle legs between consecutive
    game venues (home team's arena) arriving at games in the last 7 days including tonight's."""
    g = games.select("game_id", "game_date", "season", "home_team", "away_team")
    long = pl.concat(
        [
            g.select(
                "game_id",
                "game_date",
                pl.col("home_team").alias("team_id"),
                pl.col("home_team").alias("venue"),
                pl.col("away_team").alias("opp_id"),
            ),
            g.select(
                "game_id",
                "game_date",
                pl.col("away_team").alias("team_id"),
                pl.col("home_team").alias("venue"),
                pl.col("home_team").alias("opp_id"),
            ),
        ]
    ).sort(["team_id", "game_date", "game_id"])
    rows: list[pl.DataFrame] = []
    for _tid, sub in long.group_by("team_id", maintain_order=True):
        d = sub["game_date"].cast(pl.Int32).to_numpy().astype(np.int64)
        v = sub["venue"].to_numpy()
        n = len(d)
        idx = np.arange(n)
        n4 = idx - np.searchsorted(d, d - 3, side="left")
        n6 = idx - np.searchsorted(d, d - 5, side="left")
        n7 = idx - np.searchsorted(d, d - 6, side="left")
        legs = np.zeros(n)
        for i in range(1, n):
            a, b = ARENAS.get(int(v[i - 1])), ARENAS.get(int(v[i]))
            if a is not None and b is not None and d[i] - d[i - 1] <= 30:
                legs[i] = haversine_miles(a[0], a[1], b[0], b[1])
        cs = np.cumsum(legs)
        lo = np.searchsorted(d, d - 6, side="left")
        trav7 = cs - np.where(lo > 0, cs[np.maximum(lo - 1, 0)], 0.0)
        nxt = np.full(n, 7.0)
        if n > 1:
            nxt[:-1] = np.minimum(d[1:] - d[:-1], 7)
        # Look-ahead guard: whether a postseason game follows depends on tonight's result
        # (series end / play-in elimination), so "days to next" is only pre-tip knowledge when
        # neither this game nor the next is postseason (game_id prefixes 004 playoffs, 005 play-in,
        # 006 cup final). Otherwise use the same neutral 7 as a team's last game.
        gids = sub["game_id"].to_list()
        regular = np.array([not str(g).startswith(("004", "005", "006")) for g in gids])
        nxt_regular = np.append(regular[1:], False)
        nxt[~(regular & nxt_regular)] = 7.0
        prev_gap = np.full(n, 7.0)
        if n > 1:
            prev_gap[1:] = np.minimum(d[1:] - d[:-1], 7)
        rows.append(
            pl.DataFrame(
                {
                    "game_id": sub["game_id"],
                    "team_id": sub["team_id"],
                    "opp_id": sub["opp_id"],
                    "sch_n_prev4": n4.astype(float),
                    "sch_n_prev7": n7.astype(float),
                    "sch_three_in_four": (n4 >= 2).astype(np.int8),
                    "sch_four_in_six": (n6 >= 3).astype(np.int8),
                    "sch_b2b_second": (prev_gap == 1).astype(np.int8),
                    "sch_b2b_first": (nxt == 1).astype(np.int8),
                    "sch_days_to_next": nxt,
                    "sch_travel_leg": legs,
                    "sch_travel7": trav7,
                }
            )
        )
    tf = pl.concat(rows)
    opp = tf.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("sch_n_prev7").alias("sch_opp_n_prev7"),
        pl.col("sch_three_in_four").alias("sch_opp_three_in_four"),
        pl.col("sch_travel7").alias("sch_opp_travel7"),
    )
    return tf.join(opp, on=["game_id", "opp_id"], how="left").drop("opp_id")


# ----- (e) opponent vs position


def opp_vs_position(feats: pl.DataFrame) -> pl.DataFrame:
    """As-of opponent defence by position group, per (game_id, opp_id, pos_code).

    Unit = (stat - the player's OWN recency mean) of every player-row the defending team
    faced; decayed sums over the defender's EARLIER games only, shrunk toward 0 by ``K_OPP``
    pseudo player-games. Rows need ``n_prior >= 1`` (own mean defined)."""
    stats = list(PROP_STATS)
    rows = feats.filter(pl.col("n_prior") >= 1).select(
        "game_id",
        "game_date",
        "opp_id",
        "pos_code",
        *[(pl.col(s).cast(pl.Float64) - pl.col(f"m10_{s}")).alias(f"r_{s}") for s in stats],
    )
    agg = (
        rows.group_by(["opp_id", "pos_code", "game_id", "game_date"])
        .agg(pl.len().alias("_cnt").cast(pl.Float64), *[pl.col(f"r_{s}").sum() for s in stats])
        .sort(["opp_id", "pos_code", "game_date", "game_id"])
    )
    key = (agg["opp_id"].cast(pl.Int64) * 10 + agg["pos_code"].cast(pl.Int64) + 1).to_numpy()
    x = agg.select(["_cnt", *[f"r_{s}" for s in stats]]).to_numpy()
    pre, _ = ew_pre_post(key, x, halflife=20.0)
    cols = {f"opp_pos_{s}": pre[:, i + 1] / (pre[:, 0] + K_OPP) for i, s in enumerate(stats)}
    return pl.DataFrame(
        {"game_id": agg["game_id"], "opp_id": agg["opp_id"], "pos_code": agg["pos_code"], **cols}
    )


# --------------------------------------------------------------------------- (f)-(h) small groups


def game_env_features(games: pl.DataFrame) -> pl.DataFrame:
    """As-of team scoring environment per game (nba.parlay.game_model.asof_total_features)."""
    g = games.select("game_id", "game_date", "home_team", "away_team", "home_pts", "away_pts")
    return asof_total_features(g, halflife=20.0, prior_k=8.0).rename(
        {"total_feat": "game_total_feat"}
    )


def tv_features(games: pl.DataFrame) -> pl.DataFrame:
    if "national_tv" not in games.columns:
        return games.select("game_id").with_columns(
            pl.lit(0).cast(pl.Int8).alias("nat_tv"), pl.lit(0).cast(pl.Int8).alias("nat_tv_major")
        )
    tv = pl.col("national_tv").cast(pl.Utf8)
    major = (
        tv.str.contains("ESPN|ABC|TNT|NBC|Peacock|Amazon|CBS|Prime") & ~(tv == "NBA TV")
    ).fill_null(False)
    return games.select(
        "game_id",
        (tv.is_not_null() & (tv != "")).cast(pl.Int8).alias("nat_tv"),
        major.cast(pl.Int8).alias("nat_tv_major"),
    )


def draft_features(feats: pl.DataFrame, static: pl.DataFrame) -> pl.DataFrame:
    need = {"player_id", "draft_year", "draft_pick"}
    if not need.issubset(static.columns):
        raise ValueError("players_static needs draft_year and draft_pick")
    s = static.select(pl.col("player_id").cast(pl.Int64), "draft_year", "draft_pick").unique(
        "player_id"
    )
    df = feats.select("game_id", "player_id", "season").join(s, on="player_id", how="left")
    undrafted = pl.col("draft_pick").is_null() | (pl.col("draft_pick") <= 0)
    pick = pl.when(undrafted).then(61.0).otherwise(pl.col("draft_pick").cast(pl.Float64))
    yrs = (pl.col("season") - pl.col("draft_year")).cast(pl.Float64)
    df = df.with_columns(
        pick.alias("draft_pick_f"),
        undrafted.cast(pl.Int8).alias("undrafted"),
        yrs.alias("yrs_since_draft"),
    )
    df = df.with_columns(
        pl.col("draft_pick_f").log().alias("ln_pick"),
        (pl.col("yrs_since_draft") == 0).fill_null(False).cast(pl.Int8).alias("is_rookie"),
    ).with_columns((pl.col("is_rookie") * pl.col("ln_pick")).alias("rookie_ln_pick"))
    return df.select("game_id", "player_id", *group_columns("g", "pts"))


# --------------------------------------------------------------------------- (i) ridge, (j) OT norm


def opp_adjusted_ridge(v1: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """As-of no-intercept ridge on player and opponent-team effects, per stat per 36 minutes.

    Refit once per calendar month on played rows (>= 5 min) dated STRICTLY BEFORE the month's
    first day and within ``RIDGE_LOOKBACK_DAYS``; rows of that month read the coefficients.
    Target is the per-36 rate minus the training weighted mean, weights = minutes. Unseen players
    / teams get 0; months with < ``RIDGE_MIN_ROWS`` training rows get null."""
    import scipy.sparse as sp
    from sklearn.linear_model import Ridge

    mins = pgs.select("game_id", pl.col("player_id").cast(pl.Int64), "minutes")
    d = (
        v1.select("game_id", "player_id", "opp_id", "game_date", *PROP_STATS)
        .join(mins, on=["game_id", "player_id"], how="left")
        .filter(pl.col("minutes") >= 5.0)
        .sort(["game_date", "game_id", "player_id"])
    )
    dates = d["game_date"].to_numpy().astype("datetime64[D]")
    pid, oid = d["player_id"].to_numpy(), d["opp_id"].to_numpy()
    w_all = d["minutes"].to_numpy().astype(float)
    yr = {s_: d[s_].to_numpy().astype(float) / w_all * 36.0 for s_ in PROP_STATS}
    months = dates.astype("datetime64[M]")
    out_cols: dict[str, np.ndarray] = {
        f"ridge_{k}_{s_}": np.full(d.height, np.nan) for s_ in PROP_STATS for k in ("player", "opp")
    }
    for mth in np.unique(months):
        start = mth.astype("datetime64[D]")
        cur = months == mth
        tr = (dates < start) & (dates >= start - np.timedelta64(RIDGE_LOOKBACK_DAYS, "D"))
        if tr.sum() < RIDGE_MIN_ROWS:
            continue
        pu, pinv = np.unique(pid[tr], return_inverse=True)
        ou, oinv = np.unique(oid[tr], return_inverse=True)
        n = int(tr.sum())
        rows = np.arange(n)
        X = sp.csr_matrix(
            (
                np.ones(2 * n),
                (np.r_[rows, rows], np.r_[pinv, len(pu) + oinv]),
            ),
            shape=(n, len(pu) + len(ou)),
        )
        pmap = {int(k): i for i, k in enumerate(pu)}
        omap = {int(k): len(pu) + i for i, k in enumerate(ou)}
        pi = np.array([pmap.get(int(k), -1) for k in pid[cur]])
        oi = np.array([omap.get(int(k), -1) for k in oid[cur]])
        for s_ in PROP_STATS:
            y = yr[s_][tr]
            mu = float(np.average(y, weights=w_all[tr]))
            coef = (
                Ridge(alpha=RIDGE_ALPHA, fit_intercept=False, solver="sparse_cg")
                .fit(X, y - mu, sample_weight=w_all[tr])
                .coef_
            )
            coef = coef.copy()
            coef[len(pu) :] -= coef[len(pu) :].mean()  # opponent effects centred on the league
            out_cols[f"ridge_player_{s_}"][cur] = np.where(pi >= 0, coef[np.maximum(pi, 0)], 0.0)
            out_cols[f"ridge_opp_{s_}"][cur] = np.where(oi >= 0, coef[np.maximum(oi, 0)], 0.0)
    res = d.select("game_id", "player_id").with_columns(
        [pl.Series(k, v) for k, v in out_cols.items()]
    )
    return res


def ot_normalised_rates(played: pl.DataFrame, pgs_all: pl.DataFrame) -> pl.DataFrame:
    """Recency-weighted per-48 baselines that overtime cannot inflate.

    ``game_len`` = total player minutes in the game / 10 (48 regulation, 53 with one OT).
    ``n48_<stat>`` = decayed mean of stat * 48 / game_len over EARLIER games; ``n48_min`` likewise
    for minutes; ``rate48_<stat>`` = 48 * decayed stat sum / decayed minutes sum (per-minute
    rate, overtime-invariant). Labels elsewhere stay raw."""
    glen = (
        pgs_all.group_by("game_id")
        .agg(pl.col("minutes").fill_null(0.0).sum().alias("_tot"))
        .with_columns((pl.col("_tot") / 10.0).clip(40.0, 80.0).alias("game_len"))
        .select("game_id", "game_len")
    )
    df = played.join(glen, on="game_id", how="left").with_columns(
        pl.col("game_len").fill_null(48.0)
    )
    scale = 48.0 / df["game_len"].to_numpy()
    cols = [np.ones(df.height)]
    cols += [df[s_].to_numpy().astype(float) * scale for s_ in PROP_STATS]
    cols += [df["minutes"].to_numpy().astype(float) * scale]
    cols += [df[s_].to_numpy().astype(float) for s_ in PROP_STATS]
    cols += [df["minutes"].to_numpy().astype(float)]
    pre, _ = ew_pre_post(df["player_id"].to_numpy(), np.column_stack(cols))
    w = pre[:, 0]
    k = len(PROP_STATS)
    out: dict[str, np.ndarray] = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        for i, s_ in enumerate(PROP_STATS):
            out[f"n48_{s_}"] = np.where(w > 0, pre[:, 1 + i] / w, np.nan)
            out[f"rate48_{s_}"] = np.where(
                pre[:, 2 + 2 * k] > 0, 48.0 * pre[:, 2 + k + i] / pre[:, 2 + 2 * k], np.nan
            )
        out["n48_min"] = np.where(w > 0, pre[:, 1 + k] / w, np.nan)
    return pl.DataFrame(
        {"game_id": df["game_id"], "player_id": df["player_id"], **{k_: v for k_, v in out.items()}}
    )


def feature_allowlist() -> set[str]:
    return set(all_v1_columns()) | set(all_v2_columns())


def assert_feature_allowlist(names: list[str], allowed: set[str] | None = None) -> None:
    """Static leak guard: every model input must be an allow-listed as-of feature (or a
    ``rel__`` transform of one) and match none of the same-game / post-tip deny patterns."""
    allow = allowed if allowed is not None else feature_allowlist()
    deny = re.compile(LEAK_DENY)
    bad = []
    for n in names:
        base = n[5:] if n.startswith("rel__") else n
        if base not in allow or deny.search(base):
            bad.append(n)
    if bad:
        raise ValueError(f"features not allow-listed / same-game or post-tip names: {bad[:10]}")


# --------------------------------------------------------------------------- assemble


def build_v2_features(
    v1: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    poss: pl.DataFrame,
    report_cfg: ReportTriggerConfig | None = None,
) -> pl.DataFrame:
    """Return ``v1`` (output of ``context_residual.build_features``) with the v2 columns joined.

    ``pgs`` must include DNP rows (history signal only) and ``oreb``/``dreb``; ``games`` must
    carry ``national_tv`` if available. Row count and order are those of ``v1``."""
    cfg = report_cfg or ReportTriggerConfig()
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    pgs = pgs.with_columns(pl.col("player_id").cast(pl.Int64))
    v1 = v1.with_columns(pl.col("player_id").cast(pl.Int64))
    status, reported = pretip_status(avail, games, cfg)
    played = (
        pgs.join(games.select(["game_id", "game_date", "season"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = player_states(played)
    pr, usage_post = possession_rates(played, poss.with_columns(pl.col("player_id").cast(pl.Int64)))
    ab = absence_features(pgs, games, status, reported)
    ret = returning_teammates(games, pgs, status, reported, played, states, usage_post)
    sch = schedule_features(games)
    opp = opp_vs_position(v1)
    env = game_env_features(games)
    tv = tv_features(games)
    dr = draft_features(v1, static)
    rg = opp_adjusted_ridge(v1, pgs)
    ot = ot_normalised_rates(played, pgs)
    out = (
        v1.join(ab, on=["game_id", "player_id"], how="left")
        .join(ret, on=["game_id", "team_id"], how="left")
        .join(pr, on=["game_id", "player_id"], how="left")
        .join(sch, on=["game_id", "team_id"], how="left")
        .join(opp, on=["game_id", "opp_id", "pos_code"], how="left")
        .join(env, on="game_id", how="left")
        .join(tv, on="game_id", how="left")
        .join(dr, on=["game_id", "player_id"], how="left")
        .join(rg, on=["game_id", "player_id"], how="left")
        .join(ot, on=["game_id", "player_id"], how="left")
    )
    out = out.with_columns(
        (pl.col("game_total_feat") / 2 + pl.col("exp_margin") / 2).alias("implied_team_pts"),
        (pl.col("game_total_feat") / 2 - pl.col("exp_margin") / 2).alias("implied_opp_pts"),
        (pl.col("nat_tv") * pl.col("is_home_f")).alias("tv_home"),
    )
    # games without a usable report: returning-teammate features stay null (never imputed)
    return out.sort(["game_date", "game_id", "player_id"])


# --------------------------------------------------------------------------- loading + export


def load_inputs_v2(
    db_path: str, max_season: int = MAX_SEASON
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, pgs, static, avail, poss_player_game); read-only; season <= max_season."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SET memory_limit='3GB'")
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts, "
            "national_tv "
            f"FROM games WHERE season <= {int(max_season)}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, "
            "s.starter, s.oreb, s.dreb, s.fga, s.tov, s.fta "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {int(max_season)}"
        ).pl()
        static = con.execute(
            "SELECT player_id, position, draft_year, draft_pick FROM players_static"
        ).pl()
        avail = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.as_of, a.source FROM player_availability a "
            f"JOIN games g USING (game_id) WHERE g.season <= {int(max_season)}"
        ).pl()
        shot = "outcome IN ('FGM2','FGM3','FGA_miss')"
        made = "outcome IN ('FGM2','FGM3')"
        zsum = ", ".join(
            f"sum(CASE WHEN {shot} AND shot_zone='{z}' THEN 1 ELSE 0 END) AS z_{k}"
            for z, k in (("rim", "rim"), ("mid", "mid"), ("corner3", "c3"), ("above3", "a3"))
        )
        poss = con.execute(
            f"""
            WITH pg AS (SELECT p.* FROM possessions p JOIN games g USING (game_id)
                        WHERE g.season <= {int(max_season)}),
            onc AS (SELECT game_id, pid AS player_id, count(*) AS on_poss
                    FROM (SELECT game_id, unnest(off_players) AS pid FROM pg) GROUP BY 1, 2),
            sh AS (SELECT game_id, shooter_id AS player_id, {zsum},
                     sum(CASE WHEN {made} THEN 1 ELSE 0 END) AS makes,
                     sum(CASE WHEN {made} AND assister_id IS NOT NULL THEN 1 ELSE 0 END)
                       AS ast_makes
                   FROM pg WHERE shooter_id IS NOT NULL GROUP BY 1, 2)
            SELECT o.game_id, o.player_id, o.on_poss,
                   coalesce(s.z_rim,0) AS z_rim, coalesce(s.z_mid,0) AS z_mid,
                   coalesce(s.z_c3,0) AS z_c3, coalesce(s.z_a3,0) AS z_a3,
                   coalesce(s.makes,0) AS makes, coalesce(s.ast_makes,0) AS ast_makes
            FROM onc o LEFT JOIN sh s ON o.game_id = s.game_id AND o.player_id = s.player_id
            """
        ).pl()
    finally:
        con.close()
    return games, pgs, static, avail, poss


def month_fold_id() -> pl.Expr:
    return (pl.col("game_date").dt.year() * 100 + pl.col("game_date").dt.month()).alias("fold_id")


def feature_spec(v1_cols: list[str] | None = None) -> dict[str, Any]:
    v1 = v1_cols or all_v1_columns()
    return {
        "stats": list(PROP_STATS),
        "keys": ["game_id", "player_id", "team_id", "game_date", "season", "fold_id"],
        "labels": list(PROP_STATS),
        "baseline": {s: {"mean": f"base_m_{s}", "std": f"base_s_{s}"} for s in PROP_STATS},
        "v1_features": {s: stat_feature_names(s) for s in PROP_STATS},
        "v2_features": {s: v2_feature_names(s) for s in PROP_STATS},
        "v2_groups": {g: {s: group_columns(g, s) for s in PROP_STATS} for g in V2_GROUPS},
        "v1_union": v1,
        "allowlist": sorted(feature_allowlist()),
        "leak_deny_regex": LEAK_DENY,
        "v2_union": all_v2_columns(),
        "slice_columns": ["has_report", "n_out_rot", "min_gap", "team_game_no", "starter10"],
        "min_prior_played": MIN_PRIOR_PLAYED,
        "default_std": dict(_DEFAULT_STD),
        "seasons": list(EXPORT_SEASONS),
        "warmup_season": 2022,
        "seed": SEED,
        "v1_production_config": {
            "n_estimators": 200,
            "learning_rate": 0.04,
            "num_leaves": 15,
            "min_child_samples": 100,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "cal_frac": 0.15,
            "min_cal_rows": 2000,
        },
    }


def export_dataset(
    db_path: str = "nba.duckdb",
    out_dir: str = "data/colab/ctxres_v2",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    include_holdout_2025: bool = False,
) -> dict[str, Any]:
    """Build v1+v2 features (read-only DB) and write one parquet + spec.

    Default: seasons 2022-2024 to ``ctxres_v2.parquet`` (unchanged). ``include_holdout_2025``
    (experiment 3 only; see docs/CTXRES_V3.md) also keeps season 2025 and writes the SEPARATE file
    ``ctxres_v3_with2025.parquet``; it refuses an ``out_dir`` that holds the v2 export so the v2
    parquet and spec can never be overwritten."""
    max_season = MAX_SEASON_V3 if include_holdout_2025 else MAX_SEASON
    seasons = EXPORT_SEASONS_V3 if include_holdout_2025 else EXPORT_SEASONS
    out_name = V3_PARQUET if include_holdout_2025 else "ctxres_v2.parquet"
    o = Path(out_dir)
    if include_holdout_2025 and (o / "ctxres_v2.parquet").exists():
        raise ValueError(f"{o} holds the v2 export; use a separate directory for the 2025 export")
    games, pgs, static, avail, poss = load_inputs_v2(db_path, max_season)
    if games["season"].max() > max_season:  # type: ignore[operator]
        raise ValueError(f"season > {max_season} present: it must not be loaded")
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig()
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    v1 = build_features(games, pgs, static, flagged, elo_params)
    full = build_v2_features(v1, games, pgs, static, avail, poss, cfg.report)
    del v1, games, pgs, static, avail, poss  # memory-light: only ``full`` is needed from here on
    full = full.filter(
        (pl.col("n_prior") >= MIN_PRIOR_PLAYED) & pl.col("season").is_in(list(seasons))
    )
    keep_v1 = [c for c in all_v1_columns() if c in full.columns]
    base = [pl.col(f"m10_{s}").alias(f"base_m_{s}") for s in PROP_STATS] + [
        pl.col(f"sd10_{s}").alias(f"base_s_{s}") for s in PROP_STATS
    ]
    sel = ["game_id", "player_id", "team_id", "game_date", "season", *PROP_STATS]
    out = full.select(
        *sel,
        month_fold_id(),
        *base,
        *[c for c in keep_v1 if c not in sel],
        *[c for c in all_v2_columns() if c not in keep_v1 and c not in sel],
    )
    del full
    out = out.with_columns(
        [pl.col(c).cast(pl.Float32) for c, t in out.schema.items() if t in (pl.Float64,)]
    )
    o.mkdir(parents=True, exist_ok=True)
    out.write_parquet(o / out_name, compression="zstd")
    spec = feature_spec(keep_v1)
    if include_holdout_2025:
        spec["seasons"] = list(seasons)
        spec["includes_holdout_2025"] = True
    spec["n_rows"] = out.height
    spec["date_min"] = str(out["game_date"].min())
    spec["date_max"] = str(out["game_date"].max())
    spec["null_share_v2"] = {c: float(out[c].is_null().mean() or 0.0) for c in all_v2_columns()}  # type: ignore[arg-type]
    spec["built_at"] = dt.datetime.now().isoformat(timespec="seconds")
    (o / "feature_spec.json").write_text(json.dumps(spec, indent=1))
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export the ctxres_v2 parquet for the Colab sweep")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--elo-config", default="configs/mov_elo_tuned.yaml")
    ap.add_argument(
        "--include-holdout-2025",
        action="store_true",
        help="experiment 3 only: also export season 2025 to a separate "
        f"{V3_PARQUET} (default out dir data/colab/ctxres_v3); see docs/CTXRES_V3.md",
    )
    a = ap.parse_args(argv)
    out_dir = a.out_dir or (
        "data/colab/ctxres_v3" if a.include_holdout_2025 else "data/colab/ctxres_v2"
    )
    spec = export_dataset(a.db, out_dir, a.elo_config, a.include_holdout_2025)
    print(json.dumps({k: spec[k] for k in ("n_rows", "date_min", "date_max")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
