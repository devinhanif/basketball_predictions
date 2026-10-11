"""team_coach_games: game-number allocation, post-season carry, known_at, 2025 fallback."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from nba.data.coaches import build_team_coach_games, load_reference

A, B = 1610612737, 1610612738


def _games(n: int) -> pl.DataFrame:
    rows = [
        {"game_id": f"00224{i:05d}", "game_date": date(2024, 11, 1 + i), "season": 2024,
         "home_team": A if i % 2 else B, "away_team": B if i % 2 else A}
        for i in range(1, n + 1)
    ]  # fmt: skip
    rows.append({"game_id": "0042400101", "game_date": date(2025, 4, 20), "season": 2024,
                 "home_team": A, "away_team": B})  # fmt: skip
    rows.append({"game_id": "0022500001", "game_date": date(2025, 11, 1), "season": 2025,
                 "home_team": A, "away_team": B})  # fmt: skip
    return pl.DataFrame(rows)


def _ref() -> pl.DataFrame:
    return pl.DataFrame(
        {"season": [2024, 2024, 2024], "team_id": [A, A, B], "team_abbr": ["A", "A", "B"],
         "seq": [0, 1, 0], "coach_name": ["Old Coach", "New Coach", "Steady"], "games": [3, 3, 6],
         "source": ["t"] * 3}
    )  # fmt: skip


def test_change_takes_effect_on_the_game_number_and_postseason_uses_last_coach() -> None:
    g = _games(6)
    tips = g.select("game_id", pl.lit(datetime(2024, 11, 1, 23)).alias("tipoff_utc"))
    tc = pl.DataFrame({"season": [2025], "team_id": [A], "coach_id": [7], "name": ["Later Hire"],
                       "coach_type": ["Head Coach"]})  # fmt: skip
    out = build_team_coach_games(g, _ref(), tips, tc)
    a = out.filter((pl.col("team_id") == A) & (pl.col("season") == 2024)).sort("game_id")
    assert a["coach_name"].to_list() == ["Old Coach"] * 3 + ["New Coach"] * 3 + ["New Coach"]
    assert a["known_at"].null_count() == 0  # verified rows carry the real tip
    b = out.filter((pl.col("team_id") == B) & (pl.col("season") == 2024))
    assert set(b["coach_name"]) == {"Steady"}
    # 2025 has no reference: unverified fallback, never knowable as-of
    f = out.filter(pl.col("season") == 2025)
    assert f.height == 1 and f["verified"].to_list() == [False] and f["known_at"].null_count() == 1
    assert out.select("game_id", "team_id").n_unique() == out.height


def test_reference_file_sums_to_82_for_every_team_season() -> None:
    ref = load_reference()
    sums = ref.group_by("season", "team_id").agg(pl.col("games").sum().alias("n"))
    assert sums.height == 90 and (sums["n"] == 82).all()
