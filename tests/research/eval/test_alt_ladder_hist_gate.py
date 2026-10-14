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


def test_off_ladders_flags_a_ladder_centred_far_from_the_books_main_line() -> None:
    key = ["game_id", "player_id", "stat", "book"]

    def ladder(game: str, centre: float, book: str = "dk") -> list[tuple[object, ...]]:
        # rungs around the centre priced from -400 to +400: the rung nearest a raw implied
        # probability of 0.5 is the one at the centre
        pts = [centre - 2.0, centre - 1.0, centre, centre + 1.0, centre + 2.0]
        px = [-400, -200, 105, 200, 400]
        return [(game, 7, "pts", book, p, x) for p, x in zip(pts, px, strict=True)]

    rows = ladder("g_ok", 12.5) + ladder("g_off", 5.5) + ladder("g_nomain", 5.5)
    over = pl.DataFrame(rows, schema=[*key, "point", "po"], orient="row")
    main = pl.DataFrame(
        {
            "game_id": ["g_ok", "g_off"],
            "player_id": [7, 7],
            "stat": ["pts", "pts"],
            "book": ["dk", "dk"],
            "main_point": [12.5, 12.5],
            "main_imp": [0.5, 0.5],
        }
    )
    off, summary = g.off_ladders(over, main)
    assert off["game_id"].to_list() == ["g_off"]
    row = summary.row(0, named=True)
    assert (row["ladders"], row["with_main_line"], row["off"], row["no_main_line"]) == (3, 2, 1, 1)
    assert (row["off1"], row["off2_only"]) == (1, 0)  # the rung nearest 0.5 is far from the main
    # exactly 3.0 points away is NOT off (strictly more than 3.0)
    edge = pl.DataFrame(
        {
            "game_id": ["g_ok"],
            "player_id": [7],
            "stat": ["pts"],
            "book": ["dk"],
            "main_point": [9.5],
            "main_imp": [0.95],  # keeps test (b) quiet: only the 3.0-point edge of (a) is probed
        }
    )
    assert g.off_ladders(over.filter(pl.col("game_id") == "g_ok"), edge)[0].is_empty()


def test_alt_over_rungs_marks_off_ladders_and_keeps_them_for_s3() -> None:
    season = 2023
    snap = dt.datetime(season, 11, 1, 21, 56)
    tips = pl.DataFrame(
        {
            "game_id": [f"00{season}01", f"00{season}02"],
            "tipoff_utc": [dt.datetime(season, 11, 2, 1, 0)] * 2,
        }
    )
    main = pl.DataFrame(
        {
            "game_id": [f"00{season}01"],
            "player_id": [7],
            "stat": ["pts"],
            "book": ["dk"],
            "main_point": [30.5],  # the points ladder (centred near 12) is far away: off
            "main_imp": [0.5],
        }
    )
    t, meta = g.alt_over_rungs(_quotes(season, snap), tips, _model(season), main)
    off = t.filter(pl.col("ladder_off"))
    assert set(off["stat"].to_list()) == {"pts"}
    assert set(off["game_id"].to_list()) == {f"00{season}01"}
    assert any(r["book"] == "dk" and r["off"] == 1 for r in meta["ladder_integrity_by_book"])
    t0, _ = g.alt_over_rungs(_quotes(season, snap), tips, _model(season))  # no main: nothing off
    assert not t0["ladder_off"].any()
    assert t0.height == t.height  # the flag never drops a row; the arms do (S3 adds them back)


def test_contradiction_test_flags_a_ladder_with_no_rung_near_even_money() -> None:
    """Review B1b: a mislabelled small-stat ladder priced against a points line has no rung near
    0.5 (test (a) cannot fire), but its rungs below the main line carry LOWER over probability than
    the main line itself, which a real ladder never does."""
    key = ["game_id", "player_id", "stat", "book"]
    # main line 12.5 at raw implied 0.52; the ladder starts at -150 (0.6) for 2.5 / 3.5 / 4.5 ...
    # real ladder: rungs below the line are MORE likely than the line
    real = [("real", 7, "pts", "dk", p, x) for p, x in ((9.5, -250), (10.5, -200), (11.5, -150))]
    # mislabelled: every rung is far below the line yet priced at +300 or longer (implied <= 0.25)
    fake = [("fake", 7, "pts", "dk", p, x) for p, x in ((2.5, 300), (3.5, 500), (4.5, 1000))]
    over = pl.DataFrame(real + fake, schema=[*key, "point", "po"], orient="row")
    main = pl.DataFrame(
        {
            "game_id": ["real", "fake"],
            "player_id": [7, 7],
            "stat": ["pts", "pts"],
            "book": ["dk", "dk"],
            "main_point": [12.5, 12.5],
            "main_imp": [0.52, 0.52],
        }
    )
    off, summary = g.off_ladders(over, main)
    assert off["game_id"].to_list() == ["fake"]
    row = summary.row(0, named=True)
    assert (row["off1"], row["off2_only"], row["off"]) == (0, 1, 1)  # only test (b) catches it
    # a rung less than 1.0 point from the line is never tested by (b): 12.5 vs 11.5 is exactly 1.0
    near = pl.DataFrame(
        [("near", 7, "pts", "dk", 12.0 - 0.5, 300)], schema=[*key, "point", "po"], orient="row"
    )
    main_near = main.head(1).with_columns(pl.lit("near").alias("game_id"))
    assert g.off_ladders(near, main_near)[0].height == 1  # 1.0 below, implied 0.25 < 0.47: flagged
    near2 = near.with_columns(pl.lit(12.0).alias("point"))
    assert g.off_ladders(near2, main_near)[0].is_empty()  # 0.5 below the line: not tested


def test_vendor_defect_window_is_excluded_outright_for_draftkings_points_only() -> None:
    season = 2023
    tips = pl.DataFrame(
        {
            "game_id": [f"00{season}01", f"00{season}02"],
            "tipoff_utc": [dt.datetime(season, 12, 8, 1, 0)] * 2,
        }
    )
    model = _model(season)
    days = (
        (dt.datetime(season, 12, 7, 21, 56), True),
        (dt.datetime(season, 12, 19, 21, 56), True),
        (dt.datetime(season, 12, 6, 21, 56), False),
        (dt.datetime(season, 12, 20, 21, 56), False),
    )
    for day, expect in days:
        raw = _quotes(season, day).with_columns(pl.col("book").replace({"dk": "draftkings"}))
        t, _ = g.alt_over_rungs(raw, tips, model)
        w = t.filter(pl.col("vendor_window"))
        assert (w.height > 0) is expect, day
        assert t.filter(pl.col("vendor_window") & ~pl.col("ladder_off")).is_empty()
        if expect:
            # DraftKings points only: its other stats are not in the window
            assert set(w["stat"].to_list()) == {"pts"}
            assert set(w["book"].to_list()) == {"draftkings"}
            assert t.filter(pl.col("stat") == "reb")["vendor_window"].sum() == 0
    other = _quotes(season, days[0][0])  # book 'dk' is not DraftKings by name: never in the window
    t2, _ = g.alt_over_rungs(other, tips, model)
    assert t2["vendor_window"].sum() == 0
