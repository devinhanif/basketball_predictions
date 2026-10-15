"""Tests for the PARLAY_JOINT_CAL fixed construction: cross-game tickets use distinct games of one
date, same-game tickets one game, the panel candidates are exact and deterministic, the identity
rule drops legs with too few earlier rows, and season 2025 is refused."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from research.eval import parlay_joint_cal_gate as g


def _frame() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d1, d2 = dt.date(2024, 1, 1), dt.date(2024, 1, 2)
    date, game, q = [], [], []
    for day, games in ((d1, ("g1", "g2", "g3", "g4")), (d2, ("g5",))):
        for gm in games:
            for k in range(5):
                date.append(day)
                game.append(gm)
                q.append(0.50 + 0.01 * k + 0.001 * len(game))
    return (
        np.array(date, dtype="datetime64[D]"),
        np.array(game),
        np.array(q, dtype=float),
    )


def test_cross_tickets_distinct_games_one_date() -> None:
    date, game, _ = _frame()
    for m in (2, 3):
        idx, td = g.cross_tickets(date, game, m, 20, [1, 2, 3])
        assert idx.shape[1] == m and len(idx) > 0
        for row, d in zip(idx, td, strict=True):
            assert len(set(game[row])) == m
            assert set(date[row]) == {d}
        assert len({tuple(r) for r in idx}) == len(idx)
        assert not np.any(td == np.datetime64("2024-01-02"))  # one game that date: no ticket


def test_same_tickets_one_game() -> None:
    date, game, _ = _frame()
    idx, td = g.same_tickets(date, game, 3, 20, [1, 2, 3])
    for row, d in zip(idx, td, strict=True):
        assert len(set(game[row])) == 1 and len(set(row)) == 3
        assert set(date[row]) == {d}


def test_determinism() -> None:
    date, game, _ = _frame()
    a = g.cross_tickets(date, game, 2, 20, [9, 9])
    b = g.cross_tickets(date, game, 2, 20, [9, 9])
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_top_candidates_cover_the_exact_top3() -> None:
    date, game, q = _frame()
    idx, td = g.top_candidates(date, game, q, 2, "cross")
    keep = g.keep_top(idx, td, g.prod_q(q, idx))
    # brute force over every cross-game pair of legs on the first date
    d0 = np.datetime64("2024-01-01")
    day = np.flatnonzero(date == d0)
    best = sorted(
        (q[a] * q[b] for i, a in enumerate(day) for b in day[i + 1 :] if game[a] != game[b]),
        reverse=True,
    )[:3]
    got = sorted(g.prod_q(q, idx[keep][td[keep] == d0]), reverse=True)
    assert np.allclose(got, best)


def test_cal_served_drops_identity_blocks() -> None:
    rows = []
    for i in range(2500):  # October: no earlier rows, identity
        rows.append((dt.date(2023, 10, 1 + i % 20), 2023, "pts", f"g{i}"))
    for i in range(10):  # November: 2,500 earlier rows, served
        rows.append((dt.date(2023, 11, 1 + i), 2023, "pts", f"h{i}"))
    allv = pl.DataFrame(rows, schema=["game_date", "season", "stat", "game_id"], orient="row")
    legs = allv.with_columns(pl.lit(1).alias("player_id"))
    kept = g.cal_served(allv, legs, 2023)
    assert kept.height == 10 and kept["game_id"].str.starts_with("h").all()


def test_holdout_season_refused(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        g.load_legs(tmp_path, 2025, "draftkings")


def test_prod_q_and_conservative_width() -> None:
    q = np.array([0.5, 0.6, 0.7])
    idx = np.array([[0, 1], [1, 2]])
    assert np.allclose(g.prod_q(q, idx), [0.3, 0.42])
    hw = g.cons_halfwidth(
        np.array([0.3, 0.42]), np.array(["2024-01-01", "2024-01-01"], dtype="datetime64[D]")
    )
    assert hw["conservative"] >= hw["indep"]
