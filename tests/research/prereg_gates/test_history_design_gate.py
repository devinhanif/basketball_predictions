"""As-of tests for the roster-continuity builder in research/eval/history_design_gate.py."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from research.eval.history_design_gate import continuity_asof, prev_season_minutes

T, U = 1, 2


def _box(rows: list[tuple[str, int, int, float | None, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "minutes": pl.Float64,
            "season": pl.Int64,
        },
        orient="row",
    ).with_columns(pl.lit(dt.date(2023, 1, 1)).alias("game_date"))


def _tg(gids: list[str], season: int = 2023, team: int = T) -> pl.DataFrame:
    return pl.DataFrame(
        {"team_id": [team] * len(gids), "season": [season] * len(gids), "game_id": gids}
    ).with_columns(pl.int_range(pl.len()).alias("k").cast(pl.Int64))


def _world() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    # 2022: players 10 (600 min for T), 11 (300 for T), 12 (100 for T, 500 for U)
    prev_box = _box(
        [
            ("p1", T, 10, 600.0, 2022),
            ("p1", T, 11, 300.0, 2022),
            ("p1", T, 12, 100.0, 2022),
            ("p2", U, 12, 500.0, 2022),
        ]
    )
    # 2023: g0 has 10 (played), g1 adds 11 as a DNP row, g2 adds 12
    cur_box = _box(
        [
            ("g0", T, 10, 30.0, 2023),
            ("g1", T, 10, 30.0, 2023),
            ("g1", T, 11, None, 2023),
            ("g2", T, 12, 20.0, 2023),
        ]
    )
    tg = _tg(["g0", "g1", "g2", "g3"])
    pre = pl.DataFrame(
        {"team_id": [T], "season": [2023], "player_id": [10]},
        schema={"team_id": pl.Int64, "season": pl.Int64, "player_id": pl.Int64},
    )
    prev = prev_season_minutes(pl.concat([prev_box, cur_box]))
    return cur_box.select("game_id", "team_id", "player_id"), tg, pre, prev


def _c(f: pl.DataFrame, gid: str) -> float:
    return float(f.filter(pl.col("game_id") == gid)["continuity"][0])


def test_continuity_uses_preseason_for_game_one_and_only_earlier_games_after() -> None:
    box, tg, pre, prev = _world()
    f = continuity_asof(box, tg, pre, prev)
    assert _c(f, "g0") == pytest.approx(0.6)  # preseason: player 10 only
    assert _c(f, "g1") == pytest.approx(0.6)  # g0 added nobody new; g1's own box not read
    assert _c(f, "g2") == pytest.approx(0.9)  # 11 appeared (DNP row) in g1
    assert _c(f, "g3") == pytest.approx(1.0)  # 12's T minutes (100), not his U minutes


def test_continuity_without_preseason_is_zero_at_game_one_and_null_without_a_previous_season() -> (
    None
):
    box, tg, pre, prev = _world()
    f = continuity_asof(box, tg, pre.clear(), prev)
    assert _c(f, "g0") == 0.0
    f2 = continuity_asof(box, tg, pre, prev.clear())
    assert f2["continuity"].is_null().all()


def test_continuity_planted_future_changes_nothing_and_a_planted_past_does() -> None:
    box, tg, pre, prev = _world()
    base = continuity_asof(box, tg, pre, prev)
    # planted future: rewrite g2 and later (12 leaves g2, appears in g3); g0..g2 unchanged
    fut = pl.concat(
        [
            box.filter(pl.col("game_id") != "g2"),
            pl.DataFrame({"game_id": ["g3"], "team_id": [T], "player_id": [12]}).cast(box.schema),
        ]
    )
    f = continuity_asof(fut, tg, pre, prev)
    for gid in ("g0", "g1", "g2"):
        assert _c(f, gid) == _c(base, gid)
    assert _c(f, "g3") == pytest.approx(0.9)  # g3's own planted row is not read
    # planted past: 12 in the preseason moves game one
    pre2 = pl.concat([pre, pl.DataFrame({"team_id": [T], "season": [2023], "player_id": [12]})])
    f2 = continuity_asof(box, tg, pre2, prev)
    assert _c(f2, "g0") == pytest.approx(0.7)


def test_prev_season_minutes_counts_played_minutes_per_team_and_shifts_the_season() -> None:
    b = _box([("x", T, 5, 10.0, 2022), ("y", T, 5, None, 2022), ("z", U, 5, 7.0, 2022)])
    p = prev_season_minutes(b).sort("team_id")
    assert p.rows() == [(T, 2023, 5, 10.0), (U, 2023, 5, 7.0)]
