# ruff: noqa: E501
"""As-of feature frame for the recent-role P(play) shadow (model R of docs/prereg/RECENT_ROLE_HOLDOUT.md).

The logic is vendored from ``research/eval/pplay_context_eval.py`` (``build_base``, the T-60 / morning /
evening report terms, the strength tercile), ``research/workload/asof.py`` (the five recent-minutes load
columns) and ``research/recent_role/prod_state.py`` (:mod:`nba.daily.pplay_shadow.state`), because the live
path may not import research code (tests/test_layering.py). Parity with the research frame on seasons
<= 2024 is the gate (tests/daily/test_pplay_shadow_parity.py; the real-data run is the maintainer's).

One code path builds BOTH the training rows (completed games dated before ``as_of``) and the forecast rows
(tonight's slate). Tonight's rows enter the scratch database as placeholders exactly as production's
forward path does (``nba.props.forward._insert_placeholders``): a game row and a player row with NULL
minutes, so no builder can read a tonight value that does not exist. Every box, game, availability and
tip read is filtered to ``game_date < as_of`` / ``as_of <= known_at`` here, never trusted to the caller.

Known deviation (documented, not tuned): ``is_national_tv`` is a ``games`` column the schedule feed does not
carry for a not-yet-played game; tonight's rows therefore take the builder's fill default (False), while
training rows carry the real flag.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.pplay_shadow.state import STAGE1_PROD_COLS, prod_state_rows
from nba.features.game_tipoff import attach_real_tips
from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    usable_report_rows,
)
from nba.props.context_residual import asof_elo_margin, team_game_context
from nba.props.forward import _insert_placeholders  # slate placeholders, shared with production
from nba.props.minutes import GAME_CONTEXT_FEATURE_COLUMNS, build_minutes_features

KEYS = ["game_id", "player_id"]
STATUS_CODE = {"available": 1.0, "probable": 2.0, "questionable": 3.0, "doubtful": 4.0, "out": 5.0}
MORNING_HOUR_LT = 15
MIN_PRIOR_PLAYED = 5
HISTORY_DB = Path("data/history/nba_history.duckdb")
OFFICIAL_SOURCE = "nba_official_report"

TERM_TAG_T60 = ["terc", "tag60_code", "q60", "d60", "q60_x_terc", "d60_x_terc"]
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
R_COLS = ("wl_min_last", "wl_mean_min_l3", "wl_min_3d", "wl_min_7d", "wl_mean_min_l5")
#: R = C + production's 13 recent-minutes/role columns + 5 recent-role columns, in the frozen order
#: (research.eval.recent_role_eval.arm_columns()["R"]).
FEATURES: list[str] = [
    *BASE_FEATURES,
    *TERM_TAG_T60,
    *TERM_REPORT,
    *STAGE1_PROD_COLS,
    *R_COLS,
]
#: the comparators, exactly research.eval.recent_role_eval.arm_columns()["base"] / ["Cp"]:
#: C = WORKLOAD's feature set, C' = C + production's 13 recent-minutes/role columns (R minus R_COLS)
C_FEATURES: list[str] = [*BASE_FEATURES, *TERM_TAG_T60, *TERM_REPORT]
CP_FEATURES: list[str] = [*C_FEATURES, *STAGE1_PROD_COLS]


# --------------------------------------------------------------------------- recent-minutes load columns


def load_min_features(played: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
    """``wl_min_3d, wl_min_7d, wl_mean_min_l3, wl_mean_min_l5, wl_min_last`` for each target (game_id,
    player_id, game_date). ``played`` holds player_id, game_date, minutes; only ``minutes > 0`` rows dated
    STRICTLY before the target date are read (same-date and later rows never count)."""
    p = (
        played.filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
        .select("player_id", "game_date", "minutes")
    )
    by_p = {k[0]: g for k, g in p.group_by(["player_id"], maintain_order=True)}
    res: list[dict[str, Any]] = []
    for pid, grp in targets.group_by(["player_id"], maintain_order=True):
        key = pid[0]
        g = by_p.get(key)
        if g is None:
            dates = np.array([], dtype="datetime64[D]")
            mins = np.array([], dtype=float)
        else:
            dates = g["game_date"].to_numpy().astype("datetime64[D]")
            mins = g["minutes"].to_numpy().astype(float)
        cm = np.concatenate([[0.0], np.cumsum(mins)])
        for r in grp.iter_rows(named=True):
            day = np.datetime64(r["game_date"], "D")
            hi = int(np.searchsorted(dates, day, side="left"))  # prior rows: date < D
            rec: dict[str, Any] = {"game_id": r["game_id"], "player_id": key}
            for nm, days in (("3d", 3), ("7d", 7)):
                lo = int(np.searchsorted(dates, day - np.timedelta64(days, "D"), side="left"))
                rec[f"wl_min_{nm}"] = float(cm[hi] - cm[lo])
            idx = np.arange(hi - 1, -1, -1)  # latest first
            l3, l5 = idx[:3], idx[:5]
            rec["wl_mean_min_l3"] = float(mins[l3].mean()) if len(l3) else float("nan")
            rec["wl_mean_min_l5"] = float(mins[l5].mean()) if len(l5) else float("nan")
            rec["wl_min_last"] = float(mins[idx[0]]) if len(idx) else float("nan")
            res.append(rec)
    out = pl.DataFrame(res, infer_schema_length=None)
    return targets.select(*KEYS).join(out, on=KEYS, how="left")


# --------------------------------------------------------------------------- report terms


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
    rows: pl.DataFrame, snap: pl.DataFrame, code: str, q: str, d: str
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
    rows: pl.DataFrame, snaps: pl.DataFrame, prior_wp: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """terc, the T-60 tag columns and the morning / evening 'listed' flags for ``rows``
    (game_id, player_id, team_id, season), strictly as-of: report rows are used only if stamped at or
    before the real tip minus 60 minutes (and, in the caller, at or before the run's known-at)."""
    r = rows.select("game_id", "player_id", "team_id", "season").with_columns(
        pl.col("game_id").cast(pl.Utf8),
        pl.col("player_id").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
        pl.col("season").cast(pl.Int64),
    )
    out = r.join(strength_terciles(prior_wp), on=["team_id", "season"], how="left")
    views = snapshot_views(snaps)
    out = out.join(
        _attach_snapshot(r, views["t60"], "tag60_code", "q60", "d60"), on=KEYS, how="left"
    )
    out = out.join(_listed(r, views["morning"], "on_morning_report"), on=KEYS, how="left")
    out = out.join(_listed(r, views["evening"], "on_evening_report"), on=KEYS, how="left")
    out = out.with_columns(
        (pl.col("q60") * pl.col("terc")).alias("q60_x_terc"),
        (pl.col("d60") * pl.col("terc")).alias("d60_x_terc"),
    )
    return out.select(*KEYS, *TERM_TAG_T60, *TERM_REPORT), views


# --------------------------------------------------------------------------- the frame


@dataclass(frozen=True)
class SlateGame:
    game_id: str
    home_team: int
    away_team: int
    tipoff: dt.datetime  # naive UTC


def _season_for(d: dt.date) -> int:
    return d.year if d.month >= 8 else d.year - 1


def _flagged_out(
    avail: pl.DataFrame, games: pl.DataFrame, report: ReportTriggerConfig, tips: pl.DataFrame
) -> dict[str, set[int]]:
    """Latest usable pre-tip OUT set per game (``flagged_from_availability`` with explicit tips)."""
    rows = (
        avail.filter(
            pl.col("source").is_in(list(report.sources)) & pl.col("player_id").is_not_null()
        )
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            "player_id",
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            "game_date",
        )
    )
    if report.tip_source == "real":
        rows = attach_real_tips(rows, tips)
    flagged, _ = latest_pretip_flagged(rows, report)
    return flagged


def build_frame(
    con: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    slate: list[SlateGame],
    targets: pl.DataFrame,
    *,
    elo_params: dict[str, float],
    known_at_et: dt.datetime,
    tips: pl.DataFrame,
    report: ReportTriggerConfig | None = None,
    history_db: Path = HISTORY_DB,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Feature frame (:data:`FEATURES` plus keys) for every eligible training row dated before ``as_of`` and
    every eligible slate target.

    ``targets``: game_id, player_id, team_id of tonight's forecast rows (all on ``slate`` games).
    ``known_at_et``: the run's clock as a naive US/Eastern time (report rows stamped later are ignored).
    ``tips``: game_id, tipoff_utc, tip_et for history and the slate (real tips; proxy where missing).
    Returns ``(frame, info)``: ``frame`` has ``is_slate``, ``played`` (NULL on the slate) and ``game_date``;
    ``info`` has the slate rows' reasons for absence.
    """
    report = report or ReportTriggerConfig(tip_source="real")
    games_h = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_date < ?",
        [as_of],
    ).pl()
    pgs_h = con.execute(
        "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter "
        "FROM player_game_stats s JOIN games g USING (game_id) WHERE g.game_date < ?",
        [as_of],
    ).pl()
    static = con.execute("SELECT player_id, birth_date FROM players_static").pl()
    avail = con.execute(
        "SELECT game_id, player_id, status, as_of, source FROM player_availability "
        "WHERE game_id IS NOT NULL AND source = ?",
        [OFFICIAL_SOURCE],
    ).pl()
    avail = avail.filter(pl.col("as_of").cast(pl.Datetime("us")) <= known_at_et)
    slate_ids = [g.game_id for g in slate]
    if games_h.filter(pl.col("game_id").is_in(slate_ids)).height:
        raise ValueError("a slate game is already in the pre-as_of history")

    season_now = _season_for(as_of)
    slate_games = pl.DataFrame(
        {
            "game_id": slate_ids,
            "game_date": [as_of] * len(slate),
            "season": [season_now] * len(slate),
            "home_team": [g.home_team for g in slate],
            "away_team": [g.away_team for g in slate],
            "home_pts": [None] * len(slate),
            "away_pts": [None] * len(slate),
        },
        schema=games_h.schema,
    )
    games_all = pl.concat([games_h, slate_games])

    # scratch database: history strictly before as_of plus tonight's placeholders (NULL minutes)
    sc = duckdb.connect(":memory:")
    try:
        for name, sql in {
            "games": "SELECT * FROM games WHERE game_date < ?",
            "player_game_stats": "SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) WHERE g.game_date < ?",
            "team_context": "SELECT t.* FROM team_context t JOIN games g USING (game_id) WHERE g.game_date < ?",
        }.items():
            sc.register("_src", con.execute(sql, [as_of]).pl())
            sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            sc.unregister("_src")
        sc.register("_src", con.execute("SELECT * FROM players_static").pl())
        sc.execute("CREATE TABLE players_static AS SELECT * FROM _src")
        sc.unregister("_src")
        team_game = {t: g.game_id for g in slate for t in (g.home_team, g.away_team)}
        if targets.height:
            _insert_placeholders(
                sc,
                as_of,
                pl.DataFrame(
                    {
                        "game_id": slate_ids,
                        "home_team": [g.home_team for g in slate],
                        "away_team": [g.away_team for g in slate],
                    }
                ),
                targets.select("player_id", "team_id").unique(),
                team_game,
            )
        mf = build_minutes_features(sc, use_game_context=True)
    finally:
        sc.close()
    lab = pgs_h.select(
        "game_id",
        "player_id",
        (pl.col("minutes") > 0).fill_null(False).cast(pl.Float64).alias("played"),
    )
    mf = (
        mf.join(lab, on=KEYS, how="left")
        .with_columns(pl.col("game_id").is_in(slate_ids).alias("is_slate"))
        .sort(["player_id", "game_date", "game_id"])
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
    if int(mf.filter(~pl.col("is_slate"))["played"].null_count()):
        raise ValueError("a history row has no box-score label")

    flagged = _flagged_out(avail, games_all, report, tips)
    out_df = pl.DataFrame(
        [(g, p) for g, ps in flagged.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    ).with_columns(pl.lit(1).alias("_out"))
    mf = mf.join(out_df, on=KEYS, how="left")
    reasons: dict[tuple[str, int], str] = {}
    for r in mf.filter(pl.col("is_slate")).iter_rows(named=True):
        if r["_out"] is not None:
            reasons[(r["game_id"], r["player_id"])] = "out_on_report"
        elif not (r["n_played_prior"] >= MIN_PRIOR_PLAYED):
            reasons[(r["game_id"], r["player_id"])] = f"n_played_prior<{MIN_PRIOR_PLAYED}"
    mf = (
        mf.filter(pl.col("_out").is_null())
        .filter(pl.col("n_played_prior") >= MIN_PRIOR_PLAYED)
        .drop("_out")
    )

    elo = asof_elo_margin(games_all, elo_params)
    tctx = team_game_context(games_all, pgs_h, elo).select("game_id", "team_id", "exp_margin")
    mf = mf.join(tctx, on=["game_id", "team_id"], how="left")
    mf = mf.join(
        static.select("player_id", "birth_date").unique("player_id"), on="player_id", how="left"
    )
    mf = mf.with_columns(
        ((pl.col("game_date") - pl.col("birth_date").cast(pl.Date)).dt.total_days() / 365.25).alias(
            "age"
        )
    ).sort(["game_date", "game_id", "player_id"])
    base_keys = mf.select("game_id", "player_id", "team_id", "season", "game_date")

    # report terms
    snaps = (
        avail.filter(pl.col("player_id").is_not_null())
        .select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
        )
        .join(games_all.select("game_id", "game_date", "season"), on="game_id")
    )
    snaps = attach_real_tips(snaps.with_columns(pl.col("game_date").cast(pl.Date)), tips).drop(
        "season"
    )
    prior_wp = prior_win_pct(games_h, load_history_games(history_db))
    terms, views = build_terms(base_keys, snaps, prior_wp)
    frame = mf.join(terms, on=KEYS, how="left")

    # recent-minutes load columns and production's recent-role state
    played = pgs_h.join(games_all.select("game_id", "game_date"), on="game_id").select(
        pl.col("player_id").cast(pl.Int64),
        "game_id",
        "game_date",
        pl.col("minutes").cast(pl.Float64),
    )
    load = load_min_features(played, base_keys.select("game_id", "player_id", "game_date"))
    box = pgs_h.join(games_all.select("game_id", "game_date", "season"), on="game_id")
    state = prod_state_rows(box, base_keys)
    frame = (
        frame.join(load, on=KEYS, how="left")
        .join(state.select(*KEYS, *STAGE1_PROD_COLS), on=KEYS, how="left")
        .sort(["game_date", "game_id", "player_id"])
    )
    if frame.height != mf.height:
        raise ValueError("a feature join changed the row count")
    info: dict[str, Any] = {
        "reasons": reasons,
        "t60_snapshot_ts": {
            str(g): str(t)
            for g, t in views["t60"].group_by("game_id").agg(pl.col("snap_ts").max()).iter_rows()
        },
        "n_history_rows": int(frame.filter(~pl.col("is_slate")).height),
    }
    return frame, info
