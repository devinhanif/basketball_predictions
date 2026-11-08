"""The stale-row gap (2026-11-02): a player newly ruled OUT after the morning run has no new forward
row, so his old p_play stays the latest. The consumer refuses him; the stored row is not touched."""

from __future__ import annotations

import json
from datetime import date, datetime

import duckdb

from nba.parlay.__main__ import drop_out_on_report
from nba.parlay.game_model import GameModelParams
from nba.parlay.kalshi_map import MarketRow
from nba.parlay.legs import Leg
from nba.parlay.out_gate import SKIP_REASON, load_out_on_report, utc_to_et_naive
from nba.parlay.slate import load_slate

HOME, AWAY = 10, 20
GID = "0022600001"
PARAMS = GameModelParams(0.0, 7.0, 13.6, 220.0, 0.5, 18.8, 220.0, 20.0, 5.0, 1, 1, (2023,))
SLATE = date(2026, 10, 20)
GRID = [float(v) for v in range(19)]
TIP = datetime(2026, 10, 21, 0, 30)
MORNING = datetime(2026, 10, 20, 11, 0)  # the 07:00 ET forward row
OUT_AT = datetime(2026, 10, 20, 14, 15)  # 14:15 ET (stored as naive ET): ruled OUT, no new row
SERVE = datetime(2026, 10, 20, 19, 0)  # UTC = 15:00 ET


def _db(avail: list[tuple[int, datetime, str]] | None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("ATTACH ':memory:' AS nba")
    con.execute(
        "CREATE TABLE nba.forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT,"
        " prediction JSON)"
    )
    con.execute(
        "CREATE TABLE nba.games (game_id VARCHAR, game_date DATE, home_team INT, away_team INT,"
        " home_pts INT, away_pts INT)"
    )
    con.execute("CREATE TABLE nba.player_game_stats (game_id VARCHAR, player_id INT, team_id INT)")
    con.execute("INSERT INTO nba.games VALUES ('0022500001', '2026-04-01', 10, 20, 100, 90)")
    con.execute(
        "INSERT INTO nba.player_game_stats VALUES ('0022500001', 3, 20), ('0022500001', 4, 20)"
    )
    win = {"p_home": 0.6, "home_team": HOME, "away_team": AWAY}
    con.execute(
        "INSERT INTO nba.forward_predictions VALUES ('r', ?, ?, ?, 'rung0_injury_elo', 'v', "
        "'win_prob_home', -1, ?)",
        [MORNING, GID, TIP, json.dumps(win)],
    )
    for pid in (3, 4):
        con.execute(
            "INSERT INTO nba.forward_predictions VALUES ('r', ?, ?, ?, 'props_context_residual', "
            "'v', 'pts', ?, ?)",
            [MORNING, GID, TIP, pid, json.dumps({"q_grid": GRID, "p_play": 0.91})],
        )
    if avail is not None:
        con.execute(
            "CREATE TABLE nba.player_availability (player_id INT, as_of TIMESTAMP, game_id VARCHAR,"
            " status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)"
        )
        for pid, at, status in avail:
            con.execute(
                "INSERT INTO nba.player_availability VALUES (?,?,?,?,NULL,'official',?)",
                [pid, at, GID, status, at],
            )
    return con


def _mapped() -> dict[str, tuple[MarketRow, Leg]]:
    out = {}
    for pid in (3, 4):
        m = MarketRow(f"T{pid}", "S", "E", "t", pid, "pts", 5.0, 0.4, 0.45, None)
        out[f"T{pid}"] = (m, Leg(GID, pid, "pts", 5.0, "yes", AWAY))
    return out


def test_stale_row_case_player_ruled_out_after_the_morning_row_is_refused() -> None:
    con = _db([(3, OUT_AT, "out"), (4, OUT_AT, "available")])
    info = load_slate(con, SLATE, PARAMS, prefix="nba.", as_of=SERVE)
    # the stored row is untouched: still the 07:00 p_play (we neither rewrite nor reprice it)
    assert info.ctxs[GID].p_play[3] == 0.91
    assert set(info.out_on_report) == {(GID, 3)}
    skipped: dict[str, int] = {}
    kept = drop_out_on_report(_mapped(), info, skipped)
    assert set(kept) == {"T4"} and skipped == {SKIP_REASON: 1}


def test_known_at_a_report_after_the_serve_time_is_not_used() -> None:
    con = _db([(3, OUT_AT, "out")])
    early = datetime(2026, 10, 20, 17, 0)  # UTC = 13:00 ET, before the 14:15 ET report
    info = load_slate(con, SLATE, PARAMS, prefix="nba.", as_of=early)
    assert info.out_on_report == {}
    assert set(drop_out_on_report(_mapped(), info, {})) == {"T3", "T4"}


def test_out_then_upgraded_is_priced_again_and_doubtful_is_not_out() -> None:
    back = datetime(2026, 10, 20, 14, 45)  # ET
    con = _db([(3, OUT_AT, "out"), (3, back, "probable"), (4, OUT_AT, "doubtful")])
    info = load_slate(con, SLATE, PARAMS, prefix="nba.", as_of=SERVE)
    assert info.out_on_report == {}


def test_missing_availability_table_is_said_not_silent() -> None:
    info = load_slate(_db(None), SLATE, PARAMS, prefix="nba.", as_of=SERVE)
    assert info.out_on_report == {} and any("OUT-on-report gate is off" in n for n in info.notes)
    assert load_out_on_report(_db(None), [GID], SERVE, "nba.") == {}


def test_nothing_in_the_gate_writes_or_orders() -> None:
    import inspect

    import nba.parlay.out_gate as og

    src = inspect.getsource(og)
    for word in ("INSERT", "UPDATE", "DELETE", "CREATE", "order", "portfolio"):
        assert word not in src.replace("DNP settlement", "")


def test_top_source_refuses_a_player_ruled_out_after_the_forecast_row() -> None:
    from datetime import timedelta

    from tests.parlay import test_top_source as t

    from nba.parlay import line_cal as lc
    from nba.parlay import top_source

    odds = t.build_odds([t.quote("draftkings", "over", -115), t.quote("draftkings", "under", -105)])
    nba = t.build_nba()
    now = t.MADE + timedelta(minutes=30)
    ok = top_source.load_legs(nba, odds, t.SLATE, now=now, calmap=lc.load_map())
    assert len(ok.legs) == 2  # control: priced from the stored row
    nba.execute(
        "INSERT INTO player_availability VALUES (?, ?, ?, 'out')",
        [t.PID, utc_to_et_naive(t.MADE + timedelta(minutes=10)), t.GID],  # ET, after the row
    )
    refused = top_source.load_legs(nba, odds, t.SLATE, now=now, calmap=lc.load_map())
    assert refused.legs == [] and refused.dropped[SKIP_REASON] == 1
    # known-at: before the report existed, the same row is priced
    early = top_source.load_legs(
        nba, odds, t.SLATE, now=t.MADE + timedelta(minutes=5), calmap=lc.load_map()
    )
    assert len(early.legs) == 2


def test_report_stored_in_et_is_ignored_at_18_utc_and_used_at_1930_utc() -> None:
    con = _db([(3, OUT_AT, "out")])  # 14:15 ET == 18:15 UTC
    assert load_out_on_report(con, [GID], datetime(2026, 10, 20, 18, 0), "nba.") == {}
    got = load_out_on_report(con, [GID], datetime(2026, 10, 20, 19, 30), "nba.")
    assert set(got) == {(GID, 3)} and got[(GID, 3)].as_of == OUT_AT


def test_null_player_id_rows_do_not_break_the_gate() -> None:
    con = _db([(3, OUT_AT, "out")])
    con.execute(
        "INSERT INTO nba.player_availability VALUES (NULL, ?, ?, 'out', NULL, 'x', ?)",
        [OUT_AT, GID, OUT_AT],
    )
    assert set(load_out_on_report(con, [GID], SERVE, "nba.")) == {(GID, 3)}
