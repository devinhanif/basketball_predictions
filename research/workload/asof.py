# ruff: noqa: E501
"""WORKLOAD as-of builders (docs/prereg/WORKLOAD.md; DRAFT until frozen). Research only.

Every value for a (game, team) or (game, player) uses games dated STRICTLY EARLIER than the row's own
game date; tonight's box, minutes, tracking, result and the schedule AFTER tonight never enter. The one
tonight-fact is the schedule itself (tip instant and arena), which is published before the game.

Three feature families (the knockout units):

* SCHEDULE (team): ``rest_days`` (0 = second night), ``b2b_second``, ``three_in_four`` (tonight is the
  3rd game in 4 calendar days), ``five_in_seven``, ``games_last10d`` (prior games in d-10..d-1),
  ``road_streak`` (consecutive away games INCLUDING tonight; 0 at home), ``road_trip4plus``, ``early_tip``
  (local tip before 18:00 at tonight's arena).
* TRAVEL (team): ``miles_from_prev`` (haversine between the previous game's arena and tonight's),
  ``miles_7d`` (legs of games dated d-6..d), ``tz_cross_hr`` (UTC offset of tonight's arena minus the
  previous arena's, at tonight's tip; positive = east), ``eastbound`` (tz_cross_hr > 0),
  ``alt_km`` (tonight's arena elevation) and ``alt_delta_km`` (tonight minus previous arena).
* LOAD (player), two sub-blocks so the knockout can say whether tracking adds beyond minutes:
  LOAD_MIN: ``min_3d``, ``min_7d`` (minutes in prior games dated d-3..d-1 / d-7..d-1), ``g_3d``, ``g_7d``,
  ``mean_min_l3``, ``mean_min_l5`` (last 3 / 5 PLAYED games), ``min_last``, ``min_l5_vs_base`` (l5 mean
  minus the mean of played games ranked 6..25 back; 0 when fewer than 5 base games).
  LOAD_TRACK: ``dist_3d``, ``dist_7d`` (tracked distance in the windows; 0 when the player played no game
  in the window; NaN when he played but no game in it has a tracking row), ``dpm_l3`` (distance per
  minute, last 3 played), ``speed_l3`` (minute-weighted), ``dpm_decline`` and ``speed_decline`` = shrunk
  (last 3 minus own baseline of played games ranked 4..30 back): ``w/(w+K) * (l3 - base)`` with ``w`` the
  tracked minutes of the last 3 and ``K = 30`` minutes; 0 when the baseline has < 100 tracked minutes
  (no NaN by construction, as ADVANCED_RATES G6 required), and ``track_n_l3``.

A row's windows are over the player's PLAYED games (``minutes > 0``) with ``game_date < D``; a
same-date game never counts (a double-header does not exist in the NBA, so this is a pure safeguard).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

K_SHRINK = 30.0
BASE_MIN_TRACKED = 100.0
SCHEDULE_COLS = (
    "rest_days",
    "b2b_second",
    "three_in_four",
    "five_in_seven",
    "games_last10d",
    "road_streak",
    "road_trip4plus",
    "early_tip",
)
TRAVEL_COLS = ("miles_from_prev", "miles_7d", "tz_cross_hr", "eastbound", "alt_km", "alt_delta_km")
LOAD_MIN_COLS = (
    "min_3d",
    "min_7d",
    "g_3d",
    "g_7d",
    "mean_min_l3",
    "mean_min_l5",
    "min_last",
    "min_l5_vs_base",
)
LOAD_TRACK_COLS = (
    "dist_3d",
    "dist_7d",
    "dpm_l3",
    "speed_l3",
    "dpm_decline",
    "speed_decline",
    "track_n_l3",
)
#: Production already carries these (``rest_days`` = date gap, ``b2b``; the minutes model M2 uses them too):
#: kept in the frame for slicing only, never a model column.
SLICE_ONLY = ("rest_days", "b2b_second")
FAMILIES: dict[str, tuple[str, ...]] = {
    "LOAD": LOAD_MIN_COLS + LOAD_TRACK_COLS,
    "SCHEDULE": tuple(c for c in SCHEDULE_COLS if c not in SLICE_ONLY),
    "TRAVEL": TRAVEL_COLS,
}
ALL_COLS = LOAD_MIN_COLS + LOAD_TRACK_COLS + SCHEDULE_COLS + TRAVEL_COLS
PREFIX = "wl_"
#: model columns (prefixed in :func:`build_workload_frame`), by family
MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    k: tuple(PREFIX + c for c in v) for k, v in FAMILIES.items()
}
MODEL_COLS = tuple(c for v in MODEL_FAMILIES.values() for c in v)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _offset_hr(tz: str, when_utc: datetime) -> float:
    off = when_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).utcoffset()
    assert off is not None
    return off.total_seconds() / 3600.0


def team_schedule_features(games: pl.DataFrame, teams: dict[int, dict[str, Any]]) -> pl.DataFrame:
    """SCHEDULE and TRAVEL columns per (game_id, team_id).

    ``games``: game_id, season, game_date (Date), tip_utc (naive UTC Datetime), home_team, away_team.
    ``teams``: team_id -> {lat, lon, tz, altitude_m} (``configs/team_markets.yaml``). Prior games are
    the team's earlier games in the same season dated strictly before tonight; the first game of a
    season has NULL rest/travel (the off-season is not a leg). Neutral venues use the home team's
    arena (about ten games in 2022-24; recorded, not corrected).
    """
    rows: list[tuple[Any, ...]] = []
    for r in games.iter_rows(named=True):
        for tid, home in ((r["home_team"], True), (r["away_team"], False)):
            rows.append(
                (tid, r["season"], r["game_date"], r["tip_utc"], r["game_id"], home, r["home_team"])
            )
    rows.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    out: list[dict[str, Any]] = []
    prev: list[tuple[Any, ...]] = []
    cur_key: tuple[Any, Any] | None = None
    leg_hist: list[tuple[date, float]] = []
    for tid, season, d, tip, gid, home, arena in rows:
        if (tid, season) != cur_key:
            cur_key, prev, leg_hist = (tid, season), [], []
        a = teams[arena]
        local_h = tip.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(str(a["tz"]))).hour
        rec: dict[str, Any] = {"game_id": gid, "team_id": tid}
        past = [p for p in prev if p[2] < d]
        if past:
            last = past[-1]
            rec["rest_days"] = (d - last[2]).days - 1
            rec["b2b_second"] = int(rec["rest_days"] == 0)
            rec["three_in_four"] = int(1 + sum(1 for p in past if (d - p[2]).days <= 3) >= 3)
            rec["five_in_seven"] = int(1 + sum(1 for p in past if (d - p[2]).days <= 6) >= 5)
            rec["games_last10d"] = sum(1 for p in past if 1 <= (d - p[2]).days <= 10)
            pa = teams[last[6]]
            miles = haversine_miles(
                float(pa["lat"]), float(pa["lon"]), float(a["lat"]), float(a["lon"])
            )
            rec["miles_from_prev"] = miles
            rec["tz_cross_hr"] = _offset_hr(str(a["tz"]), tip) - _offset_hr(str(pa["tz"]), tip)
            rec["eastbound"] = int(rec["tz_cross_hr"] > 0)
            rec["alt_delta_km"] = (float(a["altitude_m"]) - float(pa["altitude_m"])) / 1000.0
            legs = [m for (ld, m) in leg_hist if (d - ld).days <= 6]
            rec["miles_7d"] = miles + sum(legs)
            leg_hist.append((d, miles))
        else:
            for k in ("rest_days", "b2b_second", "three_in_four", "five_in_seven", "games_last10d"):
                rec[k] = None
            for k in ("miles_from_prev", "miles_7d", "tz_cross_hr", "eastbound", "alt_delta_km"):
                rec[k] = None
        streak = 0
        if not home:
            streak = 1
            for p in reversed(past):
                if p[5]:
                    break
                streak += 1
        rec["road_streak"] = streak
        rec["road_trip4plus"] = int(streak >= 4)
        rec["early_tip"] = int(local_h < 18)
        rec["alt_km"] = float(a["altitude_m"]) / 1000.0
        out.append(rec)
        prev.append((tid, season, d, tip, gid, home, arena))
    schema: dict[str, Any] = {"game_id": pl.Utf8, "team_id": pl.Int64}
    for c in (
        "rest_days",
        "three_in_four",
        "five_in_seven",
        "games_last10d",
        "b2b_second",
        "eastbound",
    ):
        schema[c] = pl.Int64
    return pl.DataFrame(out, schema_overrides=schema, infer_schema_length=None)


def _wmean(num: np.ndarray, w: np.ndarray) -> float:
    s = float(w.sum())
    return float(num.sum() / s) if s > 0 else float("nan")


def player_load_features(played: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
    """LOAD columns for each target (game_id, player_id, game_date).

    ``played``: player_id, game_id, game_date, minutes (> 0 rows only are used), distance, speed (NULL when
    the game has no tracking row for the player). Tracking minutes weights use the box minutes.
    """
    pl_played = (
        played.filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
        .select("player_id", "game_date", "minutes", "distance", "speed")
    )
    by_p = {k[0]: g for k, g in pl_played.group_by(["player_id"], maintain_order=True)}
    res: list[dict[str, Any]] = []
    for pid, grp in targets.group_by(["player_id"], maintain_order=True):
        key = pid[0]
        g = by_p.get(key)
        if g is None:
            dates = np.array([], dtype="datetime64[D]")
            mins = dist = spd = np.array([], dtype=float)
        else:
            dates = g["game_date"].to_numpy().astype("datetime64[D]")
            mins = g["minutes"].to_numpy().astype(float)
            dist = g["distance"].to_numpy().astype(float)
            spd = g["speed"].to_numpy().astype(float)
        trk = ~(np.isnan(dist) | np.isnan(spd))
        cm = np.concatenate([[0.0], np.cumsum(mins)])
        cd = np.concatenate([[0.0], np.cumsum(np.where(trk, dist, 0.0))])
        cu = np.concatenate([[0.0], np.cumsum((~trk).astype(float))])
        for r in grp.iter_rows(named=True):
            D = np.datetime64(r["game_date"], "D")
            hi = int(np.searchsorted(dates, D, side="left"))  # prior rows: date < D
            rec: dict[str, Any] = {"game_id": r["game_id"], "player_id": key}
            for nm, days in (("3d", 3), ("7d", 7)):
                lo = int(np.searchsorted(dates, D - np.timedelta64(days, "D"), side="left"))
                n = hi - lo
                rec[f"min_{nm}"] = float(cm[hi] - cm[lo])
                rec[f"g_{nm}"] = float(n)
                if n == 0:
                    rec[f"dist_{nm}"] = 0.0
                elif cu[hi] - cu[lo] >= n:
                    rec[f"dist_{nm}"] = float("nan")
                else:
                    rec[f"dist_{nm}"] = float(cd[hi] - cd[lo])
            idx = np.arange(hi - 1, -1, -1)  # latest first
            m_all = mins[idx]
            l3, l5 = idx[:3], idx[:5]
            rec["mean_min_l3"] = float(mins[l3].mean()) if len(l3) else float("nan")
            rec["mean_min_l5"] = float(mins[l5].mean()) if len(l5) else float("nan")
            rec["min_last"] = float(m_all[0]) if len(m_all) else float("nan")
            base5 = m_all[5:25]
            rec["min_l5_vs_base"] = (
                float(mins[l5].mean() - base5.mean()) if len(base5) >= 5 and len(l5) == 5 else 0.0
            )
            t3 = l3[trk[l3]] if len(l3) else l3
            w3 = float(mins[t3].sum())
            rec["track_n_l3"] = float(len(t3))
            rec["dpm_l3"] = _wmean(dist[t3], mins[t3]) if len(t3) else float("nan")
            rec["speed_l3"] = _wmean(spd[t3] * mins[t3], mins[t3]) if len(t3) else float("nan")
            bidx = idx[3:30]
            tb = bidx[trk[bidx]] if len(bidx) else bidx
            wb = float(mins[tb].sum())
            for nm, arr, l3v in (("dpm", dist, rec["dpm_l3"]), ("speed", spd, rec["speed_l3"])):
                if len(t3) == 0 or wb < BASE_MIN_TRACKED:
                    rec[f"{nm}_decline"] = 0.0
                else:
                    num = arr[tb] * mins[tb] if nm == "speed" else arr[tb]
                    base = float(num.sum() / wb)
                    rec[f"{nm}_decline"] = w3 / (w3 + K_SHRINK) * (float(l3v) - base)
            res.append(rec)
    out = pl.DataFrame(res, infer_schema_length=None)
    return targets.select("game_id", "player_id").join(out, on=["game_id", "player_id"], how="left")


def build_workload_frame(
    rows: pl.DataFrame,
    played: pl.DataFrame,
    sched: pl.DataFrame,
) -> pl.DataFrame:
    """Join LOAD (player) and SCHEDULE/TRAVEL (team) onto rows(game_id, player_id, team_id, game_date)."""
    load = player_load_features(played, rows.select("game_id", "player_id", "game_date"))
    load = load.rename({c: PREFIX + c for c in load.columns if c in ALL_COLS})
    sched = sched.rename({c: PREFIX + c for c in sched.columns if c in ALL_COLS})
    out = rows.join(load, on=["game_id", "player_id"], how="left").join(
        sched, on=["game_id", "team_id"], how="left"
    )
    if out.height != rows.height:
        raise ValueError("workload join changed the row count")
    return out


def permute_within_player_season(
    frame: pl.DataFrame, cols: tuple[str, ...], seed: int = 0
) -> pl.DataFrame:
    """Placebo: permute each column's values across rows within (player_id, season)."""
    rng = np.random.default_rng(seed)
    pos = (
        frame.with_row_index("_i")
        .group_by(["player_id", "season"], maintain_order=True)
        .agg(pl.col("_i"))
    )
    perm = np.arange(frame.height)
    for ids in pos["_i"].to_list():
        a = np.array(ids)
        perm[a] = rng.permutation(a)
    return frame.with_columns([pl.Series(c, frame[c].to_numpy()[perm]) for c in cols])


def permute_within_team_season(
    frame: pl.DataFrame, cols: tuple[str, ...], seed: int = 0
) -> pl.DataFrame:
    """Placebo for team-game columns: permute their values across a team's games within (team_id, season),
    one permutation per team-game (every player of a team-game keeps the same, wrong, values)."""
    rng = np.random.default_rng(seed)
    tg = (
        frame.select("game_id", "team_id", "season", *cols)
        .unique(["game_id", "team_id"])
        .sort(["team_id", "season", "game_id"])
    )
    pos = (
        tg.with_row_index("_i")
        .group_by(["team_id", "season"], maintain_order=True)
        .agg(pl.col("_i"))
    )
    perm = np.arange(tg.height)
    for ids in pos["_i"].to_list():
        a = np.array(ids)
        perm[a] = rng.permutation(a)
    shuffled = tg.with_columns([pl.Series(c, tg[c].to_numpy()[perm]) for c in cols])
    out = frame.drop(list(cols)).join(
        shuffled.drop("season"), on=["game_id", "team_id"], how="left"
    )
    if out.height != frame.height:
        raise ValueError("placebo join changed the row count")
    return out.select(frame.columns)


__all__ = [
    "ALL_COLS",
    "FAMILIES",
    "MODEL_COLS",
    "MODEL_FAMILIES",
    "build_workload_frame",
    "haversine_miles",
    "permute_within_player_season",
    "permute_within_team_season",
    "player_load_features",
    "team_schedule_features",
]
