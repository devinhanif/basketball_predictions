"""Late-scratch statistic on a hand-built fixture (no network, read-only logic)."""

from __future__ import annotations

from datetime import date, datetime

import duckdb

from nba.daily import late_scratch as ls
from nba.daily.pplay_shadow.store import DDL as PPLAY_DDL
from nba.daily.store import ensure_tables
from nba.ingest.availability import ensure_player_availability_table
from nba.lineups.store import DDL as LINEUP_DDL

G1, G2 = "0022600201", "0022600202"
T1, T2 = 1610612738, 1610612755
TIP = datetime(2026, 10, 22, 23, 0)  # 19:00 ET (EDT)


def _lineup(
    lcon: duckdb.DuckDBPyConnection,
    sid: str,
    fetched: datetime,
    team: int,
    pid: int,
    status: str,
    starter: bool,
    gid: str = G1,
) -> None:
    lcon.execute(
        "INSERT INTO lineup_snapshots VALUES (?,?,?,?,1,'',?,'X',true,?,'n',?,?,?, 'Active', ?)",
        [
            sid,
            fetched,
            date(2026, 10, 22),
            gid,
            team,
            pid,
            status,
            starter,
            "G" if starter else "",
            fetched,
        ],
    )


def _fixture() -> tuple[duckdb.DuckDBPyConnection, duckdb.DuckDBPyConnection]:
    con = duckdb.connect(":memory:")
    ensure_tables(con)
    con.execute(PPLAY_DDL)
    ensure_player_availability_table(con)
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, "
        "home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats "
        "(game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT)"
    )
    for gid in (G1, G2):
        con.execute("INSERT INTO games VALUES (?, ?, 2026, 100, 90)", [gid, date(2026, 10, 22)])
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r', ?, ?, ?, 'm', 'v', 'pts', 1, '{}')",
            [
                datetime(2026, 10, 22, 20, 0),
                gid,
                TIP if gid == G1 else datetime(2026, 10, 25, 23, 0),
            ],
        )
    # G1: players 1-7 on team 1, 9 (OUT on the report) and 11 on team 2
    teams = {**{p: T1 for p in range(1, 8)}, 9: T2, 11: T2}
    for pid, tid in teams.items():
        con.execute(
            "INSERT INTO forward_pplay_shadow "
            "(run_id, made_at, game_id, tipoff, player_id, team_id) "
            "VALUES ('r', ?, ?, ?, ?, ?)",
            [datetime(2026, 10, 22, 20, 0), G1, TIP, pid, tid],
        )
    con.execute(  # G2 candidate from the primary prop rows only; no report slot usable for it
        "INSERT INTO forward_pplay_shadow "
        "(run_id, made_at, game_id, tipoff, player_id, team_id) "
        "VALUES ('r', ?, ?, ?, 21, ?)",
        [datetime(2026, 10, 22, 20, 0), G2, datetime(2026, 10, 25, 23, 0), T1],
    )
    # latest official report before T-30 (18:30 ET): 17:00 ET, player 9 out
    con.execute(
        "INSERT INTO player_availability "
        "VALUES (9, ?, NULL, 'out', NULL, 'nba_official_report', ?)",
        [datetime(2026, 10, 22, 17, 0)] * 2,
    )
    played = [1, 2, 3, 4, 6]  # 5 (starter), 7 (bench) and 11 did not play; 9 was out
    for pid in played:
        con.execute("INSERT INTO player_game_stats VALUES (?, ?, ?, 25.0)", [G1, pid, T1])
    con.execute("INSERT INTO player_game_stats VALUES (?, 11, ?, 0.0)", [G1, T2])  # listed DNP
    con.execute("INSERT INTO player_game_stats VALUES (?, 21, ?, 30.0)", [G2, T1])

    lcon = duckdb.connect(":memory:")
    lcon.execute(LINEUP_DDL)
    early = datetime(2026, 10, 22, 22, 20)  # T-40: team 1 confirmed, team 2 still expected
    for pid in range(1, 6):
        _lineup(lcon, "s1", early, T1, pid, "Confirmed", True)
    for pid in (6, 7):
        _lineup(lcon, "s1", early, T1, pid, "Confirmed", False)
    # the feed's duplicate: player 7 also in an Expected starters block (announced) -- the frozen
    # dedup rule keeps the Confirmed row, so he is NOT a confirmed starter
    _lineup(lcon, "s1", early, T1, 7, "Expected", True)
    _lineup(lcon, "s1", early, T2, 11, "Expected", False)
    late = datetime(2026, 10, 22, 22, 40)  # T-20: both teams confirmed, 11 starts
    for pid in range(1, 6):
        _lineup(lcon, "s2", late, T1, pid, "Confirmed", True)
    for pid in (6, 7):
        _lineup(lcon, "s2", late, T1, pid, "Confirmed", False)
    _lineup(lcon, "s2", late, T2, 11, "Confirmed", True)
    return con, lcon


def test_rows_split_by_confirmed_five_and_exclude_out_and_no_report() -> None:
    con, lcon = _fixture()
    rows, n_no_report = ls.late_scratch_rows(con, lcon, 2026)
    assert n_no_report == 1  # G2 has no lineup snapshot and its T-60 report is stale/absent
    by = {r["player_id"]: r for r in rows}
    assert set(by) == {1, 2, 3, 4, 5, 6, 7, 11}  # 9 was OUT on the cutoff report
    assert {p for p, r in by.items() if r["dnp"]} == {5, 7, 11}
    assert all(r["cutoff_min"] == 30 for r in rows)
    g30 = {p: r["grp30"] for p, r in by.items()}
    assert sorted(p for p, g in g30.items() if g == "starter") == [1, 2, 3, 4, 5]
    assert (
        g30[7] == "bench" and g30[6] == "bench"
    )  # dedup: Expected duplicate does not make a starter
    assert g30[11] == "unconfirmed"
    assert by[11]["grp15"] == "starter"  # confirmed and announced by T-15


def test_summary_counts_and_shares() -> None:
    con, lcon = _fixture()
    rows, _ = ls.late_scratch_rows(con, lcon, 2026)
    s = ls.summarize(rows)
    assert s["end"] == "2026-10-22"
    day = s["per_date"]["2026-10-22"]
    assert (day["k"], day["n"]) == (3, 8) and day["share"] == 3 / 8
    for w in ("7d", "28d"):
        c = s["windows"][w]
        assert (c["all"]["k"], c["all"]["n"]) == (3, 8)
        assert (c["starter_t30"]["k"], c["starter_t30"]["n"]) == (1, 5)
        assert (c["bench_t30"]["k"], c["bench_t30"]["n"]) == (1, 2)
        assert (c["unconfirmed_t30"]["k"], c["unconfirmed_t30"]["n"]) == (1, 1)
        assert (c["starter_t15"]["k"], c["starter_t15"]["n"]) == (2, 6)
    text = "\n".join(ls.render(s, 1))
    assert "3/8 = 37.5%" in text and "rolling 7d" in text and "rolling 28d" in text
    assert "1 completed games excluded" in text


def test_window_excludes_older_dates() -> None:
    old = {"game_date": date(2026, 9, 1), "dnp": True, "grp30": "bench", "grp15": "bench"}
    new = {"game_date": date(2026, 10, 22), "dnp": False, "grp30": "starter", "grp15": "starter"}
    s = ls.summarize([old, new])
    assert s["windows"]["7d"]["all"]["n"] == 1
    assert s["windows"]["28d"]["all"]["n"] == 1  # 51 days earlier
    assert ls.summarize([])["end"] is None
