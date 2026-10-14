"""Tests for the ALT_LINE_70 gate: line convention, counting, deterministic de-duplication, and a
tiny end-to-end run on a synthetic data root (no outcome column is needed or read)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from research.eval import alt_line_70_gate as g


def test_survival_matrix_pads_ragged_rows() -> None:
    m = g.survival_matrix([[1.0, 0.5], [0.9, 0.8, 0.1]])
    assert m.shape == (2, 3)
    assert m[0].tolist() == [1.0, 0.5, 0.0]


def test_p_over_at_uses_the_half_point_convention() -> None:
    surv = np.array([[1.0, 0.8, 0.5, 0.2]])  # P(Y>=1..4)
    row = np.zeros(5, dtype=int)
    # line 0.5 -> P(Y>=1); 2.5 -> P(Y>=3); 3.5 -> P(Y>=4); 9.5 beyond support -> 0; -0.5 -> 1
    pts = np.array([0.5, 1.5, 2.5, 3.5, 9.5])
    out = g.p_over_at(surv, row[:5], pts)
    assert out.tolist() == [1.0, 0.8, 0.5, 0.2, 0.0]
    assert g.p_over_at(surv, row[:1], np.array([-0.5])).tolist() == [1.0]


def test_decimal_odds_and_halfwidth() -> None:
    d = g.decimal_odds(np.array([-200, 100, 150]))
    assert np.allclose(d, [1.5, 2.0, 2.5])
    # one game holding all rows -> sqrt(sum n^2)/N = 1: width is 1.96 sqrt(p(1-p))
    hw = g.cluster_halfwidth(0.75, np.array([10.0]))
    assert abs(hw - 1.96 * np.sqrt(0.75 * 0.25)) < 1e-12
    # ten equal games -> sqrt(10 * n^2) / (10 n) = 1/sqrt(10)
    hw10 = g.cluster_halfwidth(0.75, np.full(10, 4.0))
    assert abs(hw10 - 1.96 * np.sqrt(0.75 * 0.25) / np.sqrt(10)) < 1e-12


def test_part1_counts_bins_band_and_distance() -> None:
    # one player-game per game; survival 1.0, .95, .85, .72, .60, .3, 0
    row = [1.0, 0.95, 0.85, 0.72, 0.60, 0.3, 0.0]
    surv = np.array([row, row])
    c = g.part1_counts("reb", np.array(["a", "b"]), surv)
    assert c["all_p70"]["line_rows"] == 8  # 4 lines per player-game
    assert c["bins"]["0.70-0.75"]["line_rows"] == 2
    assert c["bins"]["0.85-0.90"]["line_rows"] == 2
    assert c["bins"]["0.95-1.00"]["line_rows"] == 4  # P = 1.0 and 0.95
    assert c["band_70_90"]["line_rows"] == 4
    assert c["band_70_90"]["games"] == 2
    # model main line = column of P closest to .5 -> k=5 (0.60); band lines k=3 (.85) and 4 (.72)
    dist = c["band_by_distance_units"]
    assert dist["(0,1]"]["line_rows"] == 2  # k=4 is 1 step below
    assert dist["(1,2]"]["line_rows"] == 2  # k=3 is 2 steps below


def test_best_price_is_deterministic_and_ties_go_to_first_book() -> None:
    df = pl.DataFrame(
        {
            "game_id": ["g"] * 3,
            "player_id": [1] * 3,
            "stat": ["pts"] * 3,
            "point": [10.5] * 3,
            "book": ["b", "a", "c"],
            "dec": [1.5, 1.5, 1.4],
        }
    )
    out = g.best_price(df)
    assert out.height == 1
    assert out["book"].to_list() == ["a"]


def _write_root(root: Path) -> None:
    (root / "reports/odds_history").mkdir(parents=True)
    (root / "data/ops/model_clv_pairs").mkdir(parents=True)
    (root / "data/odds").mkdir(parents=True)
    quotes: list[tuple[int, str, str, dt.datetime, str, str, int, str, float, int]] = []
    for season in g.SEASONS:
        game = f"00{season}01"
        tip = dt.datetime(season, 11, 1, 23, 0)
        rows = []
        for stat in g.STATS:
            # strong player: P(Y>=k) high up to k=12, then falls
            surv = [1.0] * 10 + [0.9, 0.8, 0.6, 0.3, 0.1]
            rows.append(
                {
                    "game_id": game,
                    "player_id": 7,
                    "stat": stat,
                    "game_date": dt.date(season, 11, 1),
                    "minutes": 30.0,
                    "y": 99.0,  # an outcome column that the gate must never request
                    "p_ge": surv,
                }
            )
        pl.DataFrame(rows).write_parquet(root / f"reports/odds_history/model_rows_{season}.parquet")
        pl.DataFrame({"game_id": [game], "tipoff_utc": [tip], "junk": [1]}).write_parquet(
            root / f"data/ops/model_clv_pairs/pairs_{season}.parquet"
        )
        snap = tip - dt.timedelta(minutes=70)
        for market in ("player_points", "player_rebounds", "player_assists", "player_threes"):
            for book in ("betrivers", "draftkings"):
                for point in (8.5, 10.5, 11.5, 12.5, 13.5):
                    if book == "draftkings" and point != 12.5:
                        continue
                    for side, px in (("over", -250), ("under", 180)):
                        quotes.append((season, game, "t60", snap, book, market, 7, side, point, px))
    con = duckdb.connect(str(root / "data/odds/odds_history.duckdb"))
    con.execute(
        "create table odds_history (season INTEGER, game_id VARCHAR, snapshot_kind VARCHAR, "
        "snapshot_ts TIMESTAMP, book VARCHAR, market VARCHAR, player_id BIGINT, side VARCHAR, "
        "point DOUBLE, price_american INTEGER)"
    )
    con.executemany("insert into odds_history values (?,?,?,?,?,?,?,?,?,?)", quotes)
    con.close()


def test_run_gate_end_to_end_on_a_synthetic_root(tmp_path: Path) -> None:
    _write_root(tmp_path)
    res = g.run_gate(tmp_path, hash_inputs=False)
    assert set(res["part1"]) == {"2023", "2024"}
    p1 = res["part1"]["2023"]["pts"]
    assert p1["player_games"] == 1 and p1["games"] == 1
    # lines with P >= .70: k=1..10 (1.0), k=11 (.9), k=12 (.8) -> 12 lines; the band is [.70, .90)
    # so only the .8 line is in it (.9 belongs to the 0.90-0.95 bin)
    assert p1["all_p70"]["line_rows"] == 12
    assert p1["band_70_90"]["line_rows"] == 1
    assert p1["bins"]["0.90-0.95"]["line_rows"] == 1
    p2 = res["part2"]["2023"]
    assert p2["snapshot_after_tip_minus_60_or_no_tip"] == 0
    # betrivers quotes 5 half-point lines per stat; P(over) >= .7 on 8.5, 10.5, 11.5
    # (k=9,11,12 -> 1, .9, .8); 12.5 is k=13 (.6) and fails; 13.5 is k=14 (.3)
    a = p2["arm_A_primary_book"]
    assert a["rungs"] == 3 * 4
    assert a["by_stat"] == {"pts": 3, "reb": 3, "ast": 3, "fg3m": 3}
    assert {c["book"] for c in p2["ladder_cells"]} == {"betrivers"}
    # best price over books: draftkings only quotes 12.5 which fails the threshold
    assert p2["arm_B_best_price_any_book"]["rungs"] == 12
    assert a["decimal_min_med_max"][0] == 1.4
