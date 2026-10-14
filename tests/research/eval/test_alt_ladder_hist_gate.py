"""Tests for the ALT_LADDER_HIST gate: the report-stamp window, over-rung construction
(join, dedupe, overround), cell algebra, and a tiny end-to-end run on a synthetic data root
(no outcome column is needed or read)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from research.eval import alt_ladder_hist_gate as g


def test_window_games_flags_a_stamp_between_snapshot_and_cutoff() -> None:
    # EDT = UTC-4. Game a: tip 23:00 UTC (19:00 ET), cutoff 18:00 ET, snapshot 21:56 UTC (17:56 ET):
    # the 17:00 ET stamp is BEFORE the snapshot -> not in the window.
    # Game b: tip 22:00 UTC (18:00 ET), cutoff 17:00 ET, snapshot 20:56 UTC (16:56 ET): the 17:00 ET
    # stamp is after the snapshot and exactly at the cutoff (inclusive) -> in the window.
    # Game c: same as b but the stamp is 17:01 ET, after the cutoff -> the model cannot see it.
    snap = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "tipoff_utc": [
                dt.datetime(2023, 11, 1, 23, 0),
                dt.datetime(2023, 11, 2, 22, 0),
                dt.datetime(2023, 11, 3, 22, 0),
            ],
            "snapshot_ts": [
                dt.datetime(2023, 11, 1, 21, 56),
                dt.datetime(2023, 11, 2, 20, 56),
                dt.datetime(2023, 11, 3, 20, 56),
            ],
        }
    )
    stamps = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "as_of": [
                dt.datetime(2023, 11, 1, 17, 0),
                dt.datetime(2023, 11, 2, 17, 0),
                dt.datetime(2023, 11, 3, 17, 1),
            ],
        }
    )
    assert g.window_games(stamps, snap)["game_id"].to_list() == ["b"]


def _model(season: int) -> pl.DataFrame:
    rows = []
    for gi, game in enumerate([f"00{season}01", f"00{season}02"]):
        for stat in g.STATS:
            rows.append(
                {
                    "game_id": game,
                    "player_id": 7,
                    "stat": stat,
                    "game_date": dt.date(season, 11, 1 + gi),
                    # P(Y>=k): k=1..10 -> 1.0, 11 -> .9, 12 -> .8, 13 -> .6, 14 -> .3
                    "p_ge": [1.0] * 10 + [0.9, 0.8, 0.6, 0.3],
                }
            )
    return pl.DataFrame(rows)


def _quotes(season: int, snap: dt.datetime, book_dup: bool = False) -> pl.DataFrame:
    rows = []
    for game in (f"00{season}01", f"00{season}02"):
        for market in g.ALT_MARKET_STAT:
            for point, po, pu in ((9.5, -2000, None), (11.5, -300, 220), (12.5, 120, -150)):
                rows.append((game, 7, market, "dk", snap, snap, "over", point, po))
                if pu is not None:
                    rows.append((game, 7, market, "dk", snap, snap, "under", point, pu))
            # a player who did not play (no model row): void, counted
            rows.append((game, 99, market, "dk", snap, snap, "over", 9.5, -500))
            # a whole-number line is dropped
            rows.append((game, 7, market, "dk", snap, snap, "over", 10.0, -250))
        if book_dup:
            rows.append((game, 7, "player_points_alternate", "fd", snap, snap, "over", 9.5, -900))
            rows.append((game, 7, "player_points_alternate", "fd", snap, snap, "over", 9.5, -800))
    df = pl.DataFrame(
        rows,
        schema=[
            "game_id",
            "player_id",
            "market",
            "book",
            "snapshot_ts",
            "last_update",
            "side",
            "point",
            "price_american",
        ],
        orient="row",
    )
    return df.with_columns(pl.col("market").replace_strict(g.ALT_MARKET_STAT).alias("stat"))


def test_alt_over_rungs_joins_drops_duplicates_and_reports_overround() -> None:
    season = 2023
    snap = dt.datetime(season, 11, 1, 21, 56)
    tips = pl.DataFrame(
        {
            "game_id": [f"00{season}01", f"00{season}02"],
            "tipoff_utc": [dt.datetime(season, 11, 2, 1, 0)] * 2,
        }
    )
    t, meta = g.alt_over_rungs(_quotes(season, snap, book_dup=True), tips, _model(season))
    # dk: per game and stat 3 half-point rungs for player 7 (+1 void player, +1 whole-number line)
    assert meta["duplicate_key_rows_dropped"] == {
        "over": 4,
        "under": 0,
    }  # fd pts 9.5 twice, x2 games
    dk = t.filter(pl.col("book") == "dk")
    assert dk.height == 3 * 4 * 2  # 3 rungs x 4 stats x 2 games
    assert meta["void_or_unmatched"] == 1 * 4 * 2
    # P(over 9.5) = P(Y>=10) = 1.0 ; 11.5 -> P(Y>=12) = .8 ; 12.5 -> P(Y>=13) = .6
    assert sorted(set(dk["pm"].round(6).to_list())) == [0.6, 0.8, 1.0]
    # overround only where the under was posted at the same key: 11.5 (-300 / +220) and 12.5
    two = dk.filter(pl.col("ovr").is_not_null())
    assert two.height == 2 * 4 * 2
    r = two.filter(pl.col("point") == 11.5)["ovr"][0]
    assert abs(r - (300 / 400 + 100 / 320 - 1.0)) < 1e-9
    assert (
        dk.filter(pl.col("point") == 9.5)["ovr"].null_count()
        == dk.filter(pl.col("point") == 9.5).height
    )
    assert abs(dk["lead_min"][0] - 184.0) < 1e-9  # tip 01:00 next day - snapshot 21:56 = 3 h 4 m
    assert meta["rows_with_lead_below_60"] == 0


def test_p1_cell_and_pooling_algebra() -> None:
    e = pl.DataFrame(
        {
            "game_id": ["a", "a", "b"],
            "player_id": [1, 2, 1],
            "stat": ["pts"] * 3,
            "pm": [0.8, 0.8, 0.8],
        }
    )
    c = g.p1_cell(e)
    assert c["rungs"] == 3 and c["games"] == 2 and c["player_games"] == 3
    # games hold 2 and 1 rungs: sqrt(4 + 1) / 3 times 1.96 sqrt(.16)
    assert abs(c["halfwidth_cons"] - round(1.96 * 0.4 * np.sqrt(5) / 3, 4)) < 1e-9
    assert abs(c["halfwidth_independent"] - round(1.96 * 0.4 / np.sqrt(3), 4)) < 1e-9
    pooled = g.pool_p1([c, c])
    assert pooled["rungs"] == 6 and pooled["games"] == 4
    assert abs(pooled["halfwidth_cons"] - round(c["halfwidth_cons"] / np.sqrt(2), 4)) < 1e-4
    assert g.pool_p1([{"rungs": 0}]) == {"rungs": 0}


def test_gate_flags_apply_the_carried_thresholds() -> None:
    big = {"rungs": 5000, "games": 400, "roi_halfwidth_cons": 0.04}
    small = {"rungs": 1000, "games": 400, "roi_halfwidth_cons": 0.04}
    wide = {"rungs": 5000, "games": 400, "roi_halfwidth_cons": 0.06}
    cell = {"rungs": 5000, "games": 400, "halfwidth_cons": 0.02}
    p1 = {s: cell for s in g.STATS}
    arm = {
        "ge70": big,
        "bins": {"55": small, "65": wide, "75": big, "85": big},
        "p1_band_70_90": p1,
        "p1_bins": {"55": p1},
    }
    f = g.gate_flags(arm, [{"rungs": 1, **arm}])
    assert f["part2_ge70_pooled"] is True
    assert f["part2_bins_pooled"] == {"55": False, "65": False, "75": True, "85": True}
    assert f["part1_band_pooled"] == {s: True for s in g.STATS}


def _write_root(root: Path) -> Path:
    (root / "reports/odds_history").mkdir(parents=True)
    (root / "data/ops/model_clv_pairs").mkdir(parents=True)
    (root / "data/odds").mkdir(parents=True)
    quotes = []
    avail = []
    games = []
    for season in g.SEASONS:
        m = _model(season).with_columns(pl.lit(30.0).alias("minutes"), pl.lit(99.0).alias("y"))
        m.write_parquet(root / f"reports/odds_history/model_rows_{season}.parquet")
        # game 01: tip 23:00 UTC (19:00 ET), snapshot 64 min before; game 02: tip 22:00 UTC, the
        # 17:00 ET report stamp falls between the snapshot and the cutoff
        tips = {
            f"00{season}01": dt.datetime(season, 11, 1, 23, 0),
            f"00{season}02": dt.datetime(season, 11, 2, 22, 0),
        }
        pl.DataFrame({"game_id": list(tips), "tipoff_utc": list(tips.values())}).write_parquet(
            root / f"data/ops/model_clv_pairs/pairs_{season}.parquet"
        )
        for game, tip in tips.items():
            snap = tip - dt.timedelta(minutes=64)
            games.append((game, season))
            avail.append((game, dt.datetime(season, tip.month, tip.day, 17, 0)))
            for market in g.ALT_MARKET_STAT:
                for point, po in ((9.5, -2000), (11.5, -300), (12.5, 120)):
                    quotes.append(
                        (season, game, "t60", snap, snap, "dk", market, 7, "over", point, po)
                    )
            for kind, mins in (("t60", 64), ("t5", 9)):
                s2 = tip - dt.timedelta(minutes=mins)
                for market in ("player_points", "player_rebounds"):
                    quotes.append(
                        (season, game, kind, s2, s2, "dk", market, 7, "over", 11.5, -300 + mins)
                    )
    con = duckdb.connect(str(root / "data/odds/odds_history.duckdb"))
    con.execute(
        "create table odds_history (season INTEGER, game_id VARCHAR, snapshot_kind VARCHAR, "
        "snapshot_ts TIMESTAMP, last_update TIMESTAMP, book VARCHAR, market VARCHAR, "
        "player_id BIGINT, side VARCHAR, point DOUBLE, price_american INTEGER)"
    )
    con.executemany("insert into odds_history values (?,?,?,?,?,?,?,?,?,?,?)", quotes)
    con.close()
    nba = root / "nba.duckdb"
    con = duckdb.connect(str(nba))
    con.execute("create table games (game_id VARCHAR, season INTEGER)")
    con.executemany("insert into games values (?,?)", games)
    con.execute("create table player_availability (game_id VARCHAR, as_of TIMESTAMP)")
    con.executemany("insert into player_availability values (?,?)", avail)
    con.close()
    return nba


def test_run_gate_end_to_end_on_a_synthetic_root(tmp_path: Path) -> None:
    nba = _write_root(tmp_path)
    res = g.run_gate(tmp_path, nba, hash_inputs=False)
    for season in ("2023", "2024"):
        meta = res["per_season"][season]["meta"]
        assert meta["games_with_alt_quotes"] == 2
        assert meta["games_with_report_stamp_in_window"] == 1  # game 02 only
        assert meta["rows_with_lead_below_60"] == 0
        books = res["per_season"][season]["books"]
        # 3 rungs x 4 stats x 2 games; P >= .70 on 9.5 (1.0) and 11.5 (.8) -> 2 x 4 x 2
        assert books["dk"]["all"]["rungs"] == 3 * 4 * 2
        assert books["dk"]["all"]["ge70"]["rungs"] == 2 * 4 * 2
        # the excluded arm drops the whole window game
        assert books["dk"]["excl_window"]["rungs"] == 3 * 4
        assert books["dk"]["excl_window"]["ge70"]["rungs"] == 2 * 4
        assert books["dk"]["excl_window_lead66"]["rungs"] == 3 * 4
    pooled = res["pooled"]["excl_window"]["dk"]
    assert pooled["ge70"]["rungs"] == 2 * 4 * 2
    assert pooled["gate"]["part2_ge70_pooled"] is False  # far below 1,500 rungs
    assert res["thresholds"]["part2"]["rungs"] == 1500
