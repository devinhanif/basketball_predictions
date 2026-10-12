# ruff: noqa: E501
"""WORKLOAD as-of correctness: hand-computed values, planted future, same-day, placebo."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from research.workload import asof as W

TEAMS = {
    1: {"lat": 40.0, "lon": -74.0, "tz": "America/New_York", "altitude_m": 10},
    2: {"lat": 34.0, "lon": -118.0, "tz": "America/Los_Angeles", "altitude_m": 90},
    3: {"lat": 39.7, "lon": -105.0, "tz": "America/Denver", "altitude_m": 1609},
}


def _games(spec: list[tuple[str, date, int, int, int]]) -> pl.DataFrame:
    """spec = (id, date, home, away, tip_hour_utc)."""
    return pl.DataFrame(
        [
            {
                "game_id": g,
                "season": 2024,
                "game_date": d,
                "tip_utc": datetime(d.year, d.month, d.day, h),
                "home_team": h_,
                "away_team": a_,
            }
            for g, d, h_, a_, h in spec
        ]
    )


D0 = date(2024, 1, 10)


def test_haversine_known_distance() -> None:
    # New York -> Los Angeles is about 2,450 miles
    assert 2400 < W.haversine_miles(40.0, -74.0, 34.0, -118.0) < 2500
    assert W.haversine_miles(1.0, 2.0, 1.0, 2.0) == 0.0


def test_schedule_hand_values() -> None:
    g = _games(
        [
            ("a", D0, 1, 2, 0),  # team 2 away at 1 (tip 00:00 UTC = 19:00 ET)
            ("b", D0 + timedelta(days=1), 3, 2, 2),  # team 2 away again, one day later
            ("c", D0 + timedelta(days=2), 2, 3, 2),  # team 2 home
        ]
    )
    s = W.team_schedule_features(g, TEAMS).filter(pl.col("team_id") == 2).sort("game_id")
    a, b, c = (s.row(i, named=True) for i in range(3))
    assert a["rest_days"] is None and a["miles_from_prev"] is None and a["road_streak"] == 1
    assert b["rest_days"] == 0 and b["b2b_second"] == 1 and b["road_streak"] == 2
    assert (
        1500 < b["miles_from_prev"] < 2500 and b["tz_cross_hr"] == -2.0
    )  # NY (UTC-5) -> Denver (UTC-7): westbound
    assert c["road_streak"] == 0 and c["rest_days"] == 0


def test_tz_and_eastbound_sign() -> None:
    g = _games([("a", D0, 2, 3, 3), ("b", D0 + timedelta(days=1), 1, 2, 3)])
    s = W.team_schedule_features(g, TEAMS).filter(pl.col("team_id") == 2).sort("game_id")
    b = s.row(1, named=True)
    # team 2 played home (LA, UTC-8) then flew to NY (UTC-5): +3, eastbound
    assert b["tz_cross_hr"] == 3.0 and b["eastbound"] == 1
    assert b["alt_delta_km"] < 0


def test_density_flags() -> None:
    days = [0, 1, 3, 4, 6]
    g = _games([(f"g{i}", D0 + timedelta(days=d), 1, 2, 0) for i, d in enumerate(days)])
    s = W.team_schedule_features(g, TEAMS).filter(pl.col("team_id") == 1).sort("game_id")
    r = [s.row(i, named=True) for i in range(5)]
    assert r[2]["three_in_four"] == 1  # days 0,1,3: three games inside d-3..d
    assert r[3]["three_in_four"] == 1 and r[1]["three_in_four"] == 0
    assert r[4]["five_in_seven"] == 1 and r[3]["five_in_seven"] == 0
    assert r[4]["games_last10d"] == 4


def test_road_trip_and_early_tip() -> None:
    spec = [(f"g{i}", D0 + timedelta(days=2 * i), 1, 2, 0) for i in range(4)]
    spec += [("h", D0 + timedelta(days=9), 2, 1, 22)]
    s = W.team_schedule_features(_games(spec), TEAMS).filter(pl.col("team_id") == 2).sort("game_id")
    assert s["road_streak"].to_list() == [1, 2, 3, 4, 0]
    assert s["road_trip4plus"].to_list()[:4] == [0, 0, 0, 1]
    # 22:00 UTC at LA arena is 14:00 local: early; 00:00 UTC at NY is 19:00 local: not early
    assert s["early_tip"].to_list()[-1] == 1
    s1 = (
        W.team_schedule_features(_games(spec), TEAMS).filter(pl.col("team_id") == 1).sort("game_id")
    )
    assert s1["early_tip"].to_list()[0] == 0


def test_schedule_planted_future_is_ignored() -> None:
    base = [(f"g{i}", D0 + timedelta(days=i), 1, 2, 0) for i in range(4)]
    more = base + [("zz", D0 + timedelta(days=4), 3, 2, 0)]  # a LATER game for team 2 only
    a = W.team_schedule_features(_games(base), TEAMS).filter(pl.col("game_id") != "zz")
    b = W.team_schedule_features(_games(more), TEAMS).filter(pl.col("game_id") != "zz")
    assert a.sort(["game_id", "team_id"]).equals(b.sort(["game_id", "team_id"]))


def _box(n: int = 40) -> pl.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "player_id": 7,
                "game_id": f"p{i:03d}",
                "game_date": D0 + timedelta(days=i),
                "minutes": 30.0 + (i % 5),
                "distance": 2.0 + 0.01 * i,
                "speed": 4.0,
            }
        )
    return pl.DataFrame(rows)


def _targets(box: pl.DataFrame, i: int) -> pl.DataFrame:
    r = box.row(i, named=True)
    return pl.DataFrame(
        {"game_id": [r["game_id"]], "player_id": [7], "game_date": [r["game_date"]]}
    )


def test_load_windows_hand_values() -> None:
    box = _box()
    i = 20
    f = W.player_load_features(box, _targets(box, i)).row(0, named=True)
    prior = box.filter(pl.col("game_date") < box["game_date"][i])
    d = box["game_date"][i]
    w3 = prior.filter(pl.col("game_date") >= d - timedelta(days=3))
    assert f["g_3d"] == 3 and f["min_3d"] == w3["minutes"].sum()
    assert (
        abs(
            f["dist_7d"]
            - prior.filter(pl.col("game_date") >= d - timedelta(days=7))["distance"].sum()
        )
        < 1e-9
    )
    assert f["min_last"] == box["minutes"][i - 1]
    assert abs(f["mean_min_l5"] - float(np.mean(box["minutes"].to_list()[i - 5 : i]))) < 1e-9


def test_load_planted_future_and_same_day() -> None:
    box = _box()
    i = 25
    a = W.player_load_features(box, _targets(box, i))
    poison = box.with_columns(
        pl.when(pl.col("game_date") >= box["game_date"][i])
        .then(48.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(pl.col("game_date") >= box["game_date"][i])
        .then(99.0)
        .otherwise(pl.col("distance"))
        .alias("distance"),
        pl.when(pl.col("game_date") >= box["game_date"][i])
        .then(9.0)
        .otherwise(pl.col("speed"))
        .alias("speed"),
    )
    b = W.player_load_features(poison, _targets(box, i))
    assert a.equals(b)
    # control: editing the PREVIOUS game must move the features
    ctrl = box.with_columns(
        pl.when(pl.col("game_id") == box["game_id"][i - 1])
        .then(5.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    assert not a.equals(W.player_load_features(ctrl, _targets(box, i)))


def test_untracked_window_is_nan_and_empty_window_is_zero() -> None:
    box = _box().with_columns(
        pl.when(pl.col("game_date") >= D0 + timedelta(days=17))
        .then(None)
        .otherwise(pl.col("distance"))
        .alias("distance")
    )
    f = W.player_load_features(box, _targets(box, 20)).row(0, named=True)
    assert np.isnan(f["dist_3d"])  # played in the window, no tracking row
    gap = box.filter(pl.col("game_date") < D0 + timedelta(days=5))
    t = pl.DataFrame({"game_id": ["x"], "player_id": [7], "game_date": [D0 + timedelta(days=30)]})
    f2 = W.player_load_features(gap, t).row(0, named=True)
    assert f2["dist_3d"] == 0.0 and f2["g_7d"] == 0.0


def test_decline_is_shrunk_and_zero_without_baseline() -> None:
    box = _box(45)
    box = box.with_columns(
        pl.when(pl.col("game_date") >= D0 + timedelta(days=40))
        .then(3.0)
        .otherwise(pl.col("speed"))
        .alias("speed")
    )
    f = W.player_load_features(box, _targets(box, 44)).row(0, named=True)
    assert f["speed_l3"] < 4.0 and -1.0 < f["speed_decline"] < 0.0  # shrunk toward 0
    short = box.head(6)
    f0 = W.player_load_features(short, _targets(short, 5)).row(0, named=True)
    assert f0["speed_decline"] == 0.0 and f0["dpm_decline"] == 0.0


def test_placebo_permutes_within_player_season() -> None:
    fr = pl.DataFrame(
        {
            "player_id": [1] * 6 + [2] * 6,
            "season": [2024] * 12,
            "x": [float(i) for i in range(12)],
            "y": [float(i) * 2 for i in range(12)],
        }
    )
    p = W.permute_within_player_season(fr, ("x", "y"))
    assert sorted(p.filter(pl.col("player_id") == 1)["x"].to_list()) == list(map(float, range(6)))
    assert (p["y"] == 2 * p["x"]).all()  # same permutation for every column of a row
    assert p["x"].to_list() != fr["x"].to_list()


def test_team_placebo_moves_values_across_games_not_players() -> None:
    fr = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(6) for _ in range(2)],
            "team_id": [1] * 12,
            "season": [2024] * 12,
            "player_id": list(range(12)),
            "wl_x": [float(i) for i in range(6) for _ in range(2)],
        }
    )
    p = W.permute_within_team_season(fr, ("wl_x",))
    assert p.height == fr.height and p.columns == fr.columns
    by_game = p.group_by("game_id").agg(pl.col("wl_x").n_unique())
    assert by_game["wl_x"].max() == 1  # both players of a team-game share the value
    assert sorted(p.unique("game_id")["wl_x"].to_list()) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert p["wl_x"].to_list() != fr["wl_x"].to_list()


def test_frame_prefix_and_slice_only() -> None:
    assert all(c.startswith("wl_") for c in W.MODEL_COLS)
    assert "wl_rest_days" not in W.MODEL_COLS and "wl_b2b_second" not in W.MODEL_COLS
    assert len(W.MODEL_COLS) == 27
