"""Strata assignment (descriptive) and the capture-to-report pipeline on a recorded fixture."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from research.market_forward import engine
from research.market_forward.__main__ import cmd_preflight
from research.market_forward.fee import load_pinned_fee
from research.market_forward.spec import UNDERS
from research.market_forward.strata import (
    StrataInputs,
    assign_strata,
    empty_strata,
    load_strata_inputs,
    map_game_ids,
)
from research.market_forward.strata_map import parse_key
from tests.research.market_forward.conftest import GAME, Mk, build_capture

TIP = datetime(2026, 10, 27, 23, 0)
CUT = TIP - timedelta(minutes=60)


def _rows(thresholds: list[float]) -> pl.DataFrame:
    n = len(thresholds)
    return pl.DataFrame(
        {
            "game": [GAME] * n,
            "kind": ["prop"] * n,
            "player_id": [7] * n,
            "stat": ["pts"] * n,
            "threshold": thresholds,
            "bid_c": [30] * n,
            "ask_c": [32] * n,
        }
    )


def test_unknown_when_inputs_missing_never_imputed() -> None:
    out, sc = empty_strata(_rows([21.0, 26.0]))
    assert out["z_bin"].to_list() == ["unknown"] * 2
    assert out["d_bin"].to_list() == ["no_pinnacle"] * 2
    assert sc.z_unknown == 2 and sc.pinnacle_none == 2 and sc.cross_n == 0


def test_z_bins_and_pinnacle_gap_use_only_rows_at_or_before_t_minus_60() -> None:
    key = ("0022600001", 7, "pts")
    inp = StrataInputs(game_ids={GAME: "0022600001"})
    # mean 20, sd = (q90-q10)/2.563 = 5.126/2.563 = 2; a LATER row (mean 40) must be ignored
    inp.ours[key] = [
        (CUT - timedelta(hours=2), 20.0, 17.437, 20.0, 22.563),
        (CUT + timedelta(minutes=5), 40.0, 37.0, 40.0, 43.0),
    ]
    inp.pinnacle[key] = [
        (CUT - timedelta(minutes=30), 24.5, 0.47),
        (CUT + timedelta(minutes=1), 30.5, 0.1),
    ]
    # C4: kalshi_markets.threshold is the integer N of "N+"; the line is N - 0.5.
    # N = 19 (line 18.5, z=-0.75), 21 (20.5, z=0.25), 25 (24.5, z=2.25 and D=0),
    # 24 (line 23.5 vs Pinnacle 24.5: D = -1, which the old round() printed as D = 0), 26 (D=+1).
    out, sc = assign_strata(_rows([19.0, 21.0, 25.0, 24.0, 26.0]), {GAME: TIP}, inp)
    assert out["z_bin"].to_list() == ["z<=-0.5", "-0.5<z<=0.5", "z>0.5", "z>0.5", "z>0.5"]
    assert out["d_bin"].to_list() == ["D<=-1", "D<=-1", "D=0", "D<=-1", "D>=+1"]
    assert out["cross"].to_list() == [False, False, True, False, False] and sc.cross_n == 1
    assert out["pin_gap"][2] == pytest.approx(0.47 - 0.31)
    assert out["line_minus_q50"][0] == pytest.approx(-1.5)
    assert out["line_minus_q50"][3] == pytest.approx(3.5)


def test_non_prop_rows_keep_threshold_as_the_line() -> None:
    key = ("0022600001", 7, "pts")
    inp = StrataInputs(game_ids={GAME: "0022600001"})
    inp.pinnacle[key] = [(CUT - timedelta(minutes=30), 20.5, 0.5)]
    out, _ = assign_strata(
        _rows([20.5]).with_columns(pl.lit("spread").alias("kind")), {GAME: TIP}, inp
    )
    assert out["d_bin"].to_list() == ["D=0"]


def test_pinnacle_older_than_240_minutes_is_no_pinnacle() -> None:
    key = ("0022600001", 7, "pts")
    inp = StrataInputs(game_ids={GAME: "0022600001"})
    inp.pinnacle[key] = [(CUT - timedelta(minutes=241), 20.5, 0.5)]
    out, _ = assign_strata(_rows([21.0]), {GAME: TIP}, inp)
    assert out["d_bin"].to_list() == ["no_pinnacle"]


def test_loaders_read_stored_rows_and_map_games() -> None:
    nba = duckdb.connect(":memory:")
    nba.execute(
        "CREATE TABLE prop_predictions (run_id VARCHAR, game_id VARCHAR, player_id INTEGER, "
        "stat VARCHAR, "
        "mean FLOAT, dist_family VARCHAR, dist_params JSON, p_ge JSON, q10 FLOAT, q50 FLOAT, "
        "q90 FLOAT, "
        "made_at TIMESTAMP)"
    )
    nba.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, "
        "home_team INTEGER, away_team INTEGER)"
    )
    parsed = parse_key("26OCT27OKCSAS")
    assert parsed is not None
    d, away, home = parsed
    nba.execute("INSERT INTO games VALUES ('0022600055', ?, 2026, ?, ?)", [d, home, away])
    nba.execute(
        "INSERT INTO prop_predictions VALUES "
        "('r','0022600055',7,'pts',20,'x','{}','{}',17,20,23,?)",
        [CUT - timedelta(hours=1)],
    )
    asof = duckdb.connect(":memory:")
    asof.execute(
        "CREATE TABLE live_book_odds (source VARCHAR, game_id VARCHAR, book VARCHAR, "
        "market VARCHAR, "
        "side VARCHAR, player_id INTEGER, point DOUBLE, implied_prob DOUBLE, received_at TIMESTAMP)"
    )
    asof.execute(
        "INSERT INTO live_book_odds VALUES "
        "('s','0022600055','pinnacle','player_points','over',7,20.5,0.5,?)",
        [CUT - timedelta(minutes=10)],
    )
    ids = map_game_ids(nba, ["26OCT27OKCSAS", "bogus"])
    assert ids == {"26OCT27OKCSAS": "0022600055"}
    inp = load_strata_inputs(nba, asof, ids)
    assert ("0022600055", 7, "pts") in inp.ours and ("0022600055", 7, "pts") in inp.pinnacle


# ---- capture -> universe -> report, fixture only ----


def test_preflight_on_a_recorded_capture_prints_counts_and_checks_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = datetime(2026, 10, 22, 23, 0)  # a pre-window game
    ms = [
        Mk(
            f"KXNBAPTS-26OCT22AAABBB-P{i}-{i}",
            "KXNBAPTS",
            "yes" if i % 3 == 0 else "no",
            0.3,
            0.32,
            ts=tip - timedelta(minutes=75),
            event="KXNBAPTS-26OCT22AAABBB",
        )
        for i in range(12)
    ]
    p = build_capture(tmp_path, ms, tip=tip, game="26OCT22AAABBB")
    ns = type(
        "NS",
        (),
        dict(
            rule="UNDERS_FORWARD",
            db=str(p["db"]),
            raw_root=str(p["raw"]),
            schedule=str(p["sched"]),
            k=10,
            b=50,
        ),
    )()
    rc = cmd_preflight(ns)
    out = capsys.readouterr().out
    assert "PREFLIGHT" in out and "NO returns" in out
    assert "identity failures: 0" in out
    assert "R =" not in out and "ROI" not in out and "mean y" not in out
    assert rc in (0, 1)


def test_capture_to_look_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    from research.market_forward.__main__ import build_universe

    ms = [Mk(f"KXNBAPTS-{GAME}-P{i}-{i}", "KXNBAPTS", "no", 0.3, 0.32) for i in range(8)]
    p = build_capture(tmp_path, ms)
    uni, tips = build_universe(
        UNDERS,
        p["db"],
        p["raw"],
        p["sched"],
        date(2026, 10, 25),
        date(2026, 10, 29),
        date(2026, 11, 1),
    )
    assert uni.df.height == 8 and GAME in uni.tips
    res = engine.score_look(
        UNDERS,
        uni,
        load_pinned_fee(UNDERS.all_series),
        1,
        b=50,
        selftest_k=10,
        selftest_b=50,
        schedule_dates=engine.schedule_dates_from_tips(tips),
        window_start=date(2026, 10, 25),
        cutoff=date(2026, 10, 29),
    )
    assert res.n_pool == 8 and res.verdict.name in {"INSUFFICIENT_SAMPLE", "SELFTEST_FAILED"}


def test_unders_look_prints_strata_with_unknowns_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    from research.market_forward.report import format_look
    from research.market_forward.selftest import SelfTestResult
    from research.market_forward.synth import draw_outcomes, make_world, world_universe

    ok = SelfTestResult(
        0, 1, 200, 0.05, 0.05, True, 0.0, 0.0, 0.0, 1.0, 0.05, 0.05, True, "s", True
    )
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: ok)
    w = draw_outcomes(make_world(UNDERS, 320, seed=3), np.random.default_rng(1))
    res = engine.score_look(
        UNDERS,
        world_universe(w),
        load_pinned_fee(UNDERS.all_series),
        1,
        b=100,
        strata=StrataInputs(),
        tips={},
    )
    assert res.strata_counts is not None and res.strata_counts.cross_n == 0
    assert res.strata_counts.z_unknown == res.n_pool
    txt = format_look(res)
    assert "CROSS CELL" in txt and "STRATA z (vs our mean)" in txt and "unknown" in txt


def test_report_prints_settled_by_db_hash_and_raw_range(tmp_path: Path) -> None:
    from datetime import date

    from research.market_forward.__main__ import build_universe, settled_by_for
    from research.market_forward.report import format_look

    ms = [Mk(f"KXNBAPTS-{GAME}-P{i}-{i}", "KXNBAPTS", "no", 0.3, 0.32) for i in range(8)]
    p = build_capture(tmp_path, ms)
    sb = settled_by_for(date(2026, 10, 28))
    uni, _ = build_universe(
        UNDERS,
        p["db"],
        p["raw"],
        p["sched"],
        date(2026, 10, 25),
        date(2026, 10, 29),
        date(2026, 11, 4),
        settled_by=sb,
    )
    # DB settled_ts is NULL, raw page dir 2026-10-28 <= read date: the game is settled via raw.
    assert uni.df.height == 8 and uni.settled_by == sb and uni.db_sha256 is not None
    res = engine.score_look(
        UNDERS, uni, load_pinned_fee(UNDERS.all_series), 1, b=50, selftest_k=10, selftest_b=50
    )
    txt = format_look(res)
    assert "settled_by (naive UTC; read date 23:59:59 ET): 2026-10-29T03:59:59" in txt
    assert f"db copy sha256: {uni.db_sha256}" in txt
    assert "raw page dirs read: 2026-10-26 .. 2026-11-04" not in txt  # only dirs that exist
    assert "raw page dirs read: 2026-10-28 .. 2026-10-28" in txt
