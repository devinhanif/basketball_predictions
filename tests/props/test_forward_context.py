"""Context-residual forward predictions: contract, leakage, cache, report rule."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.daily.predict import slate_report_outs
from nba.props.forward import (
    MODEL_CONTEXT,
    MODEL_RECENCY,
    OUTPUT_COLUMNS,
    predict_slate_context,
)
from tests.daily.conftest import STAT_COLS, T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db

AS_OF = date(2026, 2, 21)
START = date(2025, 10, 22)
N_DAYS = 120
ELO = {
    "k_factor": 20.0,
    "home_advantage_elo": 60.0,
    "season_carryover": 0.75,
    "mov_c": 2.2,
    "mov_div": 0.001,
}
SLATE = pl.DataFrame(
    {"game_id": ["0022600901", "0022600902"], "home_team": [T1, T3], "away_team": [T2, T4]}
)


def _insert_game(con: duckdb.DuckDBPyConnection, rng: np.random.Generator, gid: str, d: date,
                 h: int, a: int, base: dict[int, int]) -> None:  # fmt: skip
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES (?, ?, 2025, ?, ?, ?, ?)",
        [gid, d, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125))],
    )
    cols = ",".join(STAT_COLS)
    ph = ",".join("?" * len(STAT_COLS))
    for team in (h, a):
        for k in range(8):
            pid = base[team] + k
            v: dict[str, object] = {c: None for c in STAT_COLS}
            v.update(game_id=gid, player_id=pid, team_id=team)
            if k == 7 and rng.random() < 0.5:  # DNP row: minutes NULL, stats 0
                v.update(pts=0, reb=0, ast=0, fg3m=0, stl=0, blk=0, tov=0)
            else:
                mins = float(np.clip(rng.normal(34 - 3 * k, 4), 6, 42))
                scale = (8 - k) / 8.0 * mins / 30
                v.update(
                    minutes=mins,
                    starter=k < 5,
                    pts=int(rng.poisson(20 * scale)),
                    reb=int(rng.poisson(7 * scale)),
                    ast=int(rng.poisson(5 * scale)),
                    fg3m=int(rng.poisson(2.5 * scale)),
                    stl=0,
                    blk=0,
                    tov=1,
                )
            con.execute(
                f"INSERT INTO player_game_stats ({cols}) VALUES ({ph})", [v[c] for c in STAT_COLS]
            )


def _big_history(con: duckdb.DuckDBPyConnection, n_days: int = N_DAYS, seed: int = 1) -> None:
    rng = np.random.default_rng(seed)
    base = {T1: 1000, T2: 1100, T3: 3000, T4: 3100}
    for i in range(n_days):
        d = START + timedelta(days=i)
        _insert_game(con, rng, f"00225{i:05d}0", d, T1, T2, base)
        _insert_game(con, rng, f"00225{i:05d}1", d, T3, T4, base)


@pytest.fixture(scope="module")
def big() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    _big_history(c)
    return c


def _run(con: duckdb.DuckDBPyConnection, report: dict[str, set[int]] | None = None,
         cache: Path | None = None, as_of: date = AS_OF):  # type: ignore[no-untyped-def]  # fmt: skip
    return predict_slate_context(con, as_of, SLATE, report or {}, ELO, cache_root=cache)


def test_contract_and_calibration_shape(big: duckdb.DuckDBPyConnection) -> None:
    res = _run(big, {"0022600901": set(), "0022600902": set()})
    p, r = res.primary, res.recency
    assert p.columns == OUTPUT_COLUMNS and r.columns == OUTPUT_COLUMNS
    assert set(p["model"].unique()) == {MODEL_CONTEXT}
    assert set(r["model"].unique()) == {MODEL_RECENCY}
    assert p.height == r.height > 0
    for row in p.iter_rows(named=True):
        grid = json.loads(row["q_grid"])
        assert len(grid) == 19 and grid == sorted(grid) and grid[0] >= 0.0
        pg = json.loads(row["p_ge"])
        vals = [pg[k] for k in sorted(pg, key=int)]
        assert all(0.0 <= v <= 1.0 for v in vals)
        assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:], strict=False))
        assert row["q10"] <= row["q50"] <= row["q90"]
        assert row["mean_uncond"] == pytest.approx(row["p_play"] * row["mean"])
        assert 0.0 < row["p_play"] <= 1.0 and row["std"] > 0.0
    # the model actually moves the mean off the recency average
    j = p.join(r, on=["game_id", "player_id", "stat"], suffix="_r")
    assert (j["mean"] - j["mean_r"]).abs().max() > 1e-6  # type: ignore[operator]
    assert res.info["n_context_rows"] == p.height and res.info["n_fallback_rows"] == 0


def test_out_players_absent(big: duckdb.DuckDBPyConnection) -> None:
    base = _run(big, {"0022600901": set()})
    out = _run(big, {"0022600901": {1000, 1001}})
    assert {1000, 1001} <= set(base.primary["player_id"].to_list())
    assert {1000, 1001}.isdisjoint(out.primary["player_id"].to_list())
    assert {1000, 1001}.isdisjoint(out.recency["player_id"].to_list())


def test_same_day_and_future_rows_do_not_change_output() -> None:
    con = build_fixture_db(":memory:")
    _big_history(con, 100, seed=2)
    a = _run(con, {"0022600901": set()})
    rng = np.random.default_rng(9)
    base = {T1: 1000, T2: 1100, T3: 3000, T4: 3100}
    _insert_game(con, rng, "0022600777", AS_OF, T1, T2, base)  # same-day, half-ingested
    _insert_game(con, rng, "0022600778", AS_OF + timedelta(days=2), T1, T2, base)  # future
    b = _run(con, {"0022600901": set()})
    assert a.primary.equals(b.primary) and a.recency.equals(b.recency)
    _insert_game(con, rng, "0022600779", AS_OF - timedelta(days=1), T1, T2, base)  # past: moves it
    c = _run(con, {"0022600901": set()})
    assert not a.primary.equals(c.primary)


def test_cache_refit_rules(big: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    first = _run(big, cache=tmp_path)
    assert first.info["cache_hit"] is False and first.info["fit_seconds"] > 0
    assert (tmp_path / AS_OF.isoformat() / "models.pkl").exists()
    again = _run(big, cache=tmp_path)
    assert again.info["cache_hit"] is True  # same date: no refit
    assert again.primary.equals(first.primary)
    nxt = _run(big, cache=tmp_path, as_of=AS_OF + timedelta(days=1))
    assert nxt.info["cache_hit"] is False  # date changed: refit, new directory
    assert (tmp_path / (AS_OF + timedelta(days=1)).isoformat()).is_dir()


def test_slate_report_outs_uses_real_tip_minus_60() -> None:
    con = build_fixture_db(":memory:")
    tip = datetime(2026, 10, 28, 23, 30)  # 19:30 ET
    ins = (
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
        "VALUES (?, ?, 'G', ?, 'nba_official_report')"
    )
    con.execute(ins, [11, datetime(2026, 10, 28, 17, 45), "out"])  # 105 min before tip: usable
    con.execute(ins, [12, datetime(2026, 10, 28, 18, 45), "out"])  # 45 min before tip: not usable
    now = datetime(2026, 10, 28, 22, 59)
    assert slate_report_outs(con, [("G", tip)], now) == {"G": {11}}
    # "now" earlier than the report stamp (16:00 ET < 17:45 ET): not yet published
    assert slate_report_outs(con, [("G", tip)], datetime(2026, 10, 28, 20, 0)) == {}
    assert slate_report_outs(con, [("G", tip)], datetime(2026, 10, 28, 21, 50)) == {"G": {11}}
    # a game whose tip is 17:45 + 59 min has no usable report at all -> absent, not "nobody out"
    early = datetime(2026, 10, 28, 22, 44)  # 18:44 ET
    assert slate_report_outs(con, [("E", early)], now) == {}
    # reports older than 36 h are ignored
    assert slate_report_outs(con, [("G", tip + timedelta(days=3))], now + timedelta(days=3)) == {}
