# ruff: noqa: E501
"""MARKET_UPDATE gate: known-at windows, coverage counting, capacity arithmetic. Synthetic frames only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from research.eval import market_update_eval as mu

PRIOR = datetime(2024, 1, 1, 17, 56)
TIP = datetime(2024, 1, 1, 19, 0)  # decision cut = 18:30


def _games() -> pl.DataFrame:
    return pl.DataFrame({"game_id": ["g1"], "prior_et": [PRIOR], "tip_et": [TIP]})


def _avail(rows: list[tuple[int, datetime, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1"] * len(rows),
            "player_id": [r[0] for r in rows],
            "as_of": [r[1] for r in rows],
            "status": [r[2] for r in rows],
        }
    )


def test_window_is_open_at_prior_and_closed_at_decision_cut() -> None:
    av = _avail(
        [
            (
                1,
                datetime(2024, 1, 1, 17, 0),
                "questionable",
            ),  # before the prior: belongs to the market
            (1, datetime(2024, 1, 1, 18, 0), "out"),  # news
            (2, PRIOR, "out"),  # exactly at the prior: NOT news (<= prior is the market's)
            (3, datetime(2024, 1, 1, 18, 30), "available"),  # exactly at the cut: news
        ]
    )
    ch = mu.window_changes(av, _games()).sort("player_id")
    assert ch["player_id"].to_list() == [1, 3]
    assert ch["s0"].to_list() == ["questionable", "none"]
    assert ch["changed"].to_list() == [True, True]


def test_planted_future_rows_after_decision_cut_never_enter() -> None:
    base = [(1, datetime(2024, 1, 1, 18, 0), "questionable")]
    future = [(1, datetime(2024, 1, 1, 18, 31), "out"), (9, TIP, "out")]
    a = mu.window_changes(_avail(base), _games())
    b = mu.window_changes(_avail(base + future), _games())
    assert a.sort("player_id").equals(b.sort("player_id"))
    assert b["s1"].to_list() == ["questionable"]


def test_unchanged_status_is_not_news() -> None:
    av = _avail(
        [
            (1, datetime(2024, 1, 1, 12, 0), "questionable"),
            (1, datetime(2024, 1, 1, 18, 0), "questionable"),
        ]
    )
    ch = mu.window_changes(av, _games())
    assert ch["changed"].to_list() == [False]


def test_coverage_counts_legs_games_and_own_player() -> None:
    legs = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2"],
            "player_id": [1, 2, 3],
            "book": ["draftkings"] * 3,
            "season": [2023] * 3,
        }
    )
    ch = pl.DataFrame(
        {
            "game_id": ["g1"],
            "player_id": [1],
            "s0": ["questionable"],
            "s1": ["out"],
            "changed": [True],
        }
    )
    cov = mu.coverage(legs, ch)["all"]
    assert cov["legs"] == 3
    assert cov["legs_game_has_post_report"] == 2
    assert cov["legs_game_has_change"] == 2
    assert cov["legs_own_changed"] == 1


def test_weeks_to_n_arithmetic() -> None:
    r = mu.weeks_to_n(1500, news_per_game=2.0, games_per_week=50, capture=1.0)
    assert r["news_legs_per_week"] == 100.0 and r["week"] == 15.0
    assert r["week_ends"] == "2027-02-01"
    assert mu.weeks_to_n(10, 0.0, 50, 1.0)["week"] is None


def test_load_legs_refuses_holdout(tmp_path: Path) -> None:
    def frame(season: int) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "snapshot_kind": ["t60"],
                "book": ["draftkings"],
                "p_model": [0.5],
                "over": [1],
                "p_over_mult": [0.5],
                "season": [season],
                "snapshot_ts": [datetime(2024, 1, 1, 22, 56)],
                "tipoff_utc": [datetime(2024, 1, 2, 0, 0)],
                "game_id": ["g1"],
            }
        )

    frame(2023).write_parquet(tmp_path / "pairs_2023.parquet")
    frame(2025).write_parquet(tmp_path / "pairs_2024.parquet")
    with pytest.raises(AssertionError):
        mu.load_legs(tmp_path)
