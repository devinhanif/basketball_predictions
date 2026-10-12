"""TEAM_BUDGET status and star-rest: standings are as-of, rest propensity follows the coach."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from nba.features.game_context import TEAM_CONFERENCE
from research.budget import status as S


def _league(n_days: int = 40) -> pl.DataFrame:
    east = [t for t, c in TEAM_CONFERENCE.items() if c == "E"]
    rows = []
    d0 = date(2023, 10, 20)
    for k in range(n_days):
        for i in range(0, len(east) - 1, 2):
            h, a = east[i], east[i + 1]
            # lower index always wins: a fixed, deterministic ladder
            rows.append(
                {
                    "game_id": f"g{k:03d}_{i}",
                    "game_date": d0 + timedelta(days=k),
                    "season": 2023,
                    "home_team": h,
                    "away_team": a,
                    "home_pts": 110,
                    "away_pts": 100,
                }
            )
    return pl.DataFrame(rows)


def test_classify_thresholds() -> None:
    assert S.classify(0.0, -3.0, 40) == "contending"
    assert S.classify(2.0, 0.0, 40) == "middle"
    assert S.classify(5.0, 3.0, 40) == "out"
    assert S.classify(5.0, 3.0, 10) == "middle"  # too early to tell


def test_status_is_strictly_as_of_and_same_day_blind() -> None:
    g = _league()
    base = S.team_status(g).sort(["game_id", "team_id"])
    target = "g030_0"
    day = g.filter(pl.col("game_id") == target)["game_date"][0]
    planted = g.with_columns(
        pl.when(pl.col("game_date") >= day)
        .then(pl.lit(0))
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    new = S.team_status(planted).sort(["game_id", "team_id"])
    mask = pl.col("game_id").str.slice(1, 3).cast(pl.Int64) <= 30
    # everything dated up to and including the target date is unchanged by later results
    assert base.filter(mask).equals(new.filter(mask))
    assert not base.equals(new)  # the control moves later rows
    first = base.filter(pl.col("game_id").str.starts_with("g000"))
    assert (first["games_prior"] == 0).all()


def _pairs_world() -> tuple[pl.DataFrame, pl.DataFrame]:
    rows, pr = [], []
    d0 = date(2024, 1, 1)
    for k in range(30):
        gid = f"h{k:02d}"
        rows.append(
            {
                "game_id": gid,
                "team_id": 1,
                "game_date": d0 + timedelta(days=k),
                "coach_key": "A",
                "status": "contending",
            }
        )
        pr.append(
            {
                "game_id": gid,
                "team_id": 1,
                "status": "contending",
                "rest": 1 if k in (5, 6, 7) else 0,
            }
        )
    return pl.DataFrame(rows), pl.DataFrame(pr)


def test_star_rest_is_as_of_and_follows_the_coach() -> None:
    tg, pairs = _pairs_world()
    h = S.coach_star_rest_asof(tg, pairs).sort("game_id")
    v = dict(zip(h["game_id"], h["h_star_rest_matched"], strict=True))
    assert v["h05"] < v["h09"]  # the rests of games 5-7 only raise LATER rows
    assert v["h05"] == v["h04"]  # game 5's own rest is not in game 5's row
    assert v["h06"] > v["h05"]  # but the next row sees it
    # planting a different outcome in the target game and later leaves the target row unchanged
    p2 = pairs.with_columns(
        pl.when(pl.col("game_id") >= "h12").then(1).otherwise(pl.col("rest")).alias("rest")
    )
    h2 = S.coach_star_rest_asof(tg, p2).sort("game_id")
    up = pl.col("game_id") <= "h12"
    assert h.filter(up).equals(h2.filter(up))
    assert not h.equals(h2)
