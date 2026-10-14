"""Tests for the ALT_PARLAY_X fixed construction: one leg per unit, distinct games, one slate date,
product P and D, determinism under a seed, and the pooled half-width algebra."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.eval import alt_line_70_gate as g
from research.eval import alt_parlay_x_gate as x


def _rungs() -> pl.DataFrame:
    rows = []
    d1, d2 = dt.date(2024, 1, 1), dt.date(2024, 1, 2)
    # date 1: three games, two players each; date 2: one game only
    for gi, (game, day) in enumerate([("g1", d1), ("g2", d1), ("g3", d1), ("g4", d2)]):
        for pid in (1, 2):
            for point, pm in ((9.5, 0.72), (10.5, 0.64), (11.5, 0.55)):
                rows.append(
                    {
                        "book": "betrivers",
                        "game_id": game,
                        "player_id": 10 * gi + pid,
                        "stat": "pts",
                        "point": point,
                        "pm": pm,
                        "dec": 1.0 / pm * 1.08,
                        "game_date": day,
                    }
                )
    # a different book must be ignored
    rows.append({**rows[0], "book": "draftkings"})
    return pl.DataFrame(rows)


def test_pool_legs_keeps_one_rung_per_unit_inside_the_bin() -> None:
    legs = x.pool_legs(_rungs(), 0.60, 0.70, [1, 2, 3])
    assert legs.height == 8  # 8 units, each has exactly one rung with P in [0.60, 0.70)
    assert set(legs["pm"].to_list()) == {0.64}
    assert legs.select(x.KEY).unique().height == 8
    assert set(legs["book"].to_list()) == {"betrivers"}
    # the primary pool has no upper bound and takes P = 0.72 only (0.64, 0.55 are below 0.70)
    ge = x.pool_legs(_rungs(), x.THRESHOLD, 1.0 + 1e-9, [1, 2, 3])
    assert set(ge["pm"].to_list()) == {0.72}


def test_pool_legs_choice_is_seeded() -> None:
    wide = x.pool_legs(_rungs(), 0.50, 0.80, [7, 7])
    again = x.pool_legs(_rungs(), 0.50, 0.80, [7, 7])
    assert wide.equals(again)
    assert wide.height == 8  # still one leg per unit although three rungs qualify
    other = x.pool_legs(_rungs(), 0.50, 0.80, [8, 8])
    assert other.height == 8


def test_parlays_are_cross_game_same_date_with_product_odds() -> None:
    legs = x.pool_legs(_rungs(), 0.60, 0.70, [1, 2, 3]).sort([*x.KEY, "point"])
    for m in (2, 3):
        par = x.draw_parlays(legs, m, per_date=20, seed=[5, m])
        assert par.height > 0
        gid = legs["game_id"].to_list()
        dates = legs["game_date"].to_list()
        for row in par.iter_rows(named=True):
            idx = [row[f"i{k}"] for k in range(m)]
            assert len({gid[i] for i in idx}) == m  # distinct games
            assert {dates[i] for i in idx} == {row["game_date"]}  # one slate date
            assert abs(row["P"] - 0.64**m) < 1e-12
            exp = float(np.prod(legs["dec"].to_numpy()[idx]))
            assert abs(row["D"] - exp) < 1e-12
        assert par["game_date"].n_unique() == 1  # the single-game date gives no parlay
        # identical leg sets are never drawn twice
        keys = [tuple(r[f"i{k}"] for k in range(m)) for r in par.iter_rows(named=True)]
        assert len(keys) == len(set(keys))
    assert x.draw_parlays(legs, 4, 20, [1]).height == 0  # only three games on the best date


def test_draw_is_deterministic_and_capped() -> None:
    legs = x.pool_legs(_rungs(), 0.60, 0.70, [1, 2, 3])
    a = x.draw_parlays(legs, 2, 3, [9])
    b = x.draw_parlays(legs, 2, 3, [9])
    assert a.equals(b)
    assert a.height <= 3


def test_pooled_halfwidth_adds_cluster_sums() -> None:
    a = {
        "parlays": 100,
        "dates": 10,
        "roi_halfwidth_cons_date": 0.2,
        "roi_halfwidth_independent": 0.1,
    }
    b = {
        "parlays": 300,
        "dates": 30,
        "roi_halfwidth_cons_date": 0.1,
        "roi_halfwidth_independent": 0.05,
    }
    out = x.pool_parlays([a, b])
    s = (0.2 * 100 / 1.96) ** 2 + (0.1 * 300 / 1.96) ** 2
    assert out["parlays"] == 400 and out["dates"] == 40
    assert abs(out["roi_halfwidth_cons_date"] - round(1.96 * np.sqrt(s) / 400, 4)) < 1e-9


def test_secondary_part1_bins_are_disjoint_and_half_open() -> None:
    surv = np.array([[0.50, 0.59, 0.60, 0.70, 0.79, 0.80, 0.89, 0.90]])
    out = g.secondary_part1(np.array(["a"]), surv)
    assert out["55"]["line_rows"] == 2  # 0.50, 0.59
    assert out["65"]["line_rows"] == 1  # 0.60 (0.70 belongs to 75)
    assert out["75"]["line_rows"] == 2  # 0.70, 0.79 (0.80 belongs to 85)
    assert out["85"]["line_rows"] == 2  # 0.80, 0.89 (0.90 is outside)
    assert sum(v["line_rows"] for v in out.values()) == 8 - 1


def test_pool_summaries_matches_hand_algebra() -> None:
    a = {
        "rungs": 100,
        "units_player_game_stat": 90,
        "games": 40,
        "dates": 20,
        "roi_halfwidth_cons": 0.1,
        "roi_halfwidth_independent": 0.05,
    }
    out = g.pool_summaries([a, a])
    assert out["rungs"] == 200
    assert abs(out["roi_halfwidth_cons"] - round(0.1 / np.sqrt(2), 4)) < 1e-9


def test_estimators_exact_values_and_centring() -> None:
    p = np.array([0.5, 0.5, 0.5, 0.5])
    idx = np.array([[0, 1], [2, 3]])
    # all legs hit: joint hit 1 vs product .25 -> gap .75; marginal residual g = .5 for every leg,
    # so the centred product is 1.0 and the excess joint hit is exactly 0
    out = x.estimators([(p, np.ones(4), idx)])
    assert abs(out["gap"] - 0.75) < 1e-12
    assert abs(out["excess"]) < 1e-12
    # a pair that always moves together while the margin is calibrated -> positive excess and rho
    hit = np.array([1.0, 1.0, 0.0, 0.0])
    out2 = x.estimators([(p, hit, idx)])
    assert out2["excess"] > 0.2 and out2["rho"] > 0.9


def test_null_halfwidths_are_seeded_and_shrink_with_more_parlays() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.5, 0.8, size=60)
    small = np.array([[i, i + 30] for i in range(10)])
    large = np.array([[i, (i * 7 + 3) % 30 + 30] for i in range(30)])
    a = x.null_halfwidths([(p, p, small)], 200, [1])
    b = x.null_halfwidths([(p, p, small)], 200, [1])
    c = x.null_halfwidths([(p, p, large)], 200, [1])
    assert a == b
    assert c["gap_halfwidth"] < a["gap_halfwidth"]


def test_pulled_units_and_fingerprint() -> None:
    t60 = _rungs()
    t5 = _rungs().filter(pl.col("game_id") != "g2")  # g2 units were pulled between the captures
    pulled = x.pulled_units(t60, t5)
    assert set(pulled["game_id"].to_list()) == {"g2"}
    assert set(pulled["book"].to_list()) == {"betrivers"}
    legs = x.pool_legs_with_pulled(t5, t60, 0.60, 0.70, [1, 2, 3])
    assert "g2" in set(legs["game_id"].to_list())
    base = x.pool_legs(t5, 0.60, 0.70, [1, 2, 3])
    par = x.draw_parlays(base, 2, 20, [4])
    f1 = x.parlay_fingerprint(base, par, 2)
    assert f1 == x.parlay_fingerprint(base, par, 2) and len(f1) == 64
    assert x.parlay_index(par, 2).shape == (par.height, 2)
