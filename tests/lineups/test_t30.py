"""T-30 shadow path: cutoff discipline, skip reasons, idempotence, feature use, report."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.daily.report import build_report
from nba.daily.schedule import ScheduledGame
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions, ensure_tables
from nba.daily.t30 import T30_MODEL_NAME, T30Summary, run_t30
from nba.ingest.cache import RateLimiter
from nba.lineups.analysis import open_decisions, wilson
from nba.lineups.source import CONFIRMED, EXPECTED
from nba.lineups.store import connect_lineups, upsert_tip
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db
from tests.lineups.helpers import add_snapshot, team_rows
from tests.props.test_forward_context import AS_OF, _big_history

TIP = datetime(2026, 2, 22, 0, 0)  # 7:00 pm ET on AS_OF
G1, G2 = "0022600901", "0022600902"
P1 = [1000 + i for i in range(8)]  # T1 roster (box starters = first five)
P2 = [1100 + i for i in range(8)]
P3 = [3000 + i for i in range(8)]
P4 = [3100 + i for i in range(8)]
ELO_OK = None


def _sched(season: str) -> list[ScheduledGame]:
    return [ScheduledGame(G1, TIP, T1, T2), ScheduledGame(G2, TIP, T3, T4)]


@pytest.fixture(scope="module")
def hist() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    _big_history(c, 120, seed=3)
    return c


@pytest.fixture
def con(hist: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    # fresh forward tables per test on a copy-free basis: tests use unique game ids per run
    ensure_tables(hist)
    hist.execute("DELETE FROM forward_predictions")
    hist.execute("DROP TABLE IF EXISTS forward_t30_decisions")
    return hist


def _lineup_rows(
    gid: str, h: tuple[int, list[int]], a: tuple[int, list[int]], status: str = CONFIRMED,
    home_five: list[int] | None = None, inactive: tuple[int, ...] = (),
) -> list:  # type: ignore[type-arg]  # fmt: skip
    (ht, hp), (at, ap) = h, a
    hs = home_five if home_five is not None else hp[:5]
    home = team_rows(gid, ht, True, hs, [p for p in hp if p not in hs], status=status,
                     inactive=inactive)  # fmt: skip
    return home + team_rows(gid, at, False, ap[:5], ap[5:], status=status)


def _store(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    return connect_lineups(tmp_path / "l.duckdb")


def _fill(lcon: duckdb.DuckDBPyConnection, at: datetime, **kw: object) -> None:
    rows = _lineup_rows(G1, (T1, P1), (T2, P2), **kw)  # type: ignore[arg-type]
    rows += _lineup_rows(G2, (T3, P3), (T4, P4), **{k: v for k, v in kw.items() if k == "status"})  # type: ignore[arg-type]
    add_snapshot(lcon, at, date(2026, 2, 21), rows)


def _run(
    con: duckdb.DuckDBPyConnection,
    lcon: duckdb.DuckDBPyConnection,
    now: datetime,
    tmp_path: Path,
) -> T30Summary:
    return run_t30(con, lcon, date(2026, 2, 21), schedule_fn=_sched, now=now,
                   model_cache=tmp_path / "cache")  # fmt: skip


def _t30_rows(con: duckdb.DuckDBPyConnection) -> list[tuple]:  # type: ignore[type-arg]
    return con.execute(
        "SELECT game_id, player_id, target, made_at, tipoff, prediction FROM forward_predictions "
        "WHERE model_name = ? ORDER BY 1, 2, 3",
        [T30_MODEL_NAME],
    ).fetchall()


def test_logs_shadow_rows_from_pre_cutoff_snapshot(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=40))
    now = TIP - timedelta(minutes=29)
    s = _run(con, lcon, now, tmp_path)
    assert s.n_logged_games == 2 and s.n_skipped_games == 0 and s.n_rows_written > 0
    rows = _t30_rows(con)
    assert len(rows) == s.n_rows_written
    assert {r[2] for r in rows} == {"pts", "reb", "ast", "fg3m"}
    for _gid, _pid, _t, made_at, tip, pred in rows:
        assert made_at < tip  # never at/after tip-off
        p = json.loads(pred)
        assert p["lineups_known"] is True and p["comparison_only"] is True
        assert datetime.fromisoformat(p["snapshot_fetched_at"]) < tip - timedelta(minutes=30)
        assert len(p["q_grid"]) == 19 and p["routed_to"] == "context_residual"
    dec = con.execute("SELECT game_id, outcome FROM forward_t30_decisions ORDER BY 1").fetchall()
    assert dec == [(G1, "logged"), (G2, "logged")]
    # nothing was written under the production names
    assert con.execute(
        "SELECT count(*) FROM forward_predictions WHERE model_name <> ?", [T30_MODEL_NAME]
    ).fetchone() == (0,)
    # idempotent: a second run at tip-20 adds nothing
    s2 = _run(con, lcon, TIP - timedelta(minutes=20), tmp_path)
    assert s2.n_already_decided == 2 and s2.n_rows_written == 0
    assert len(_t30_rows(con)) == len(rows)


def test_snapshot_after_cutoff_is_ignored_and_reason_recorded(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    _fill(lcon, TIP - timedelta(minutes=45), status=EXPECTED)  # only projected at the cutoff
    _fill(lcon, TIP - timedelta(minutes=25), status=CONFIRMED)  # confirmed too late (after T-30)
    s = _run(con, lcon, TIP - timedelta(minutes=10), tmp_path)
    assert s.n_logged_games == 0 and s.n_rows_written == 0
    assert _t30_rows(con) == []
    reasons = dict(con.execute("SELECT game_id, reason FROM forward_t30_decisions").fetchall())
    assert reasons[G1].startswith("lineup_not_confirmed:home=Expected,away=Expected")


def test_no_snapshot_and_after_tipoff_and_not_yet(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    lcon = _store(tmp_path)
    assert _run(con, lcon, TIP - timedelta(minutes=45), tmp_path).n_not_yet == 2  # not decided
    assert con.execute("SELECT count(*) FROM forward_t30_decisions").fetchone() == (0,)
    s = _run(con, lcon, TIP - timedelta(minutes=29), tmp_path)
    assert s.skipped[G1] == "no_snapshot_before_t30" and s.n_rows_written == 0
    assert s.skipped[G2] == "no_snapshot_before_t30"
    # a fresh store run after tip-off: refused and recorded, nothing written
    con.execute("DROP TABLE forward_t30_decisions")
    late = _run(con, lcon, TIP + timedelta(minutes=5), tmp_path)
    assert late.skipped[G1] == "run_after_tipoff" and _t30_rows(con) == []


def test_not_five_starters_is_skipped(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    lcon = _store(tmp_path)
    bad = team_rows(G1, T1, True, P1[:4], P1[4:]) + team_rows(G1, T2, False, P2[:5], P2[5:])
    add_snapshot(lcon, TIP - timedelta(minutes=40), date(2026, 2, 21), bad)
    s = _run(con, lcon, TIP - timedelta(minutes=29), tmp_path)
    assert s.skipped[G1] == "starters_not_5:home=4,away=5"


def test_announced_five_and_inactive_list_drive_the_features(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tonight's ANNOUNCED five (not the placeholder/box flag) feeds the t30 columns, the model
    is the T-30 feature set, and listed-inactive players are dropped from the roster."""
    import pickle

    import nba.daily.t30 as t30mod

    seen: list[pl.DataFrame] = []
    real = t30mod.build_features

    def spy(*a: object, **k: object) -> pl.DataFrame:
        f = real(*a, **k)  # type: ignore[arg-type]
        seen.append(f.filter(pl.col("game_id") == G1))
        return f

    monkeypatch.setattr(t30mod, "build_features", spy)
    lb = _store(tmp_path)
    five = [1000, 1001, 1002, 1003, 1006]
    _fill(lb, TIP - timedelta(minutes=40), home_five=five, inactive=(1007,))
    _run(con, lb, TIP - timedelta(minutes=29), tmp_path)
    f = seen[0].filter(pl.col("team_id") == T1)
    st = dict(zip(f["player_id"].to_list(), f["t30_starter"].to_list(), strict=True))
    assert st[1006] == 1.0 and st[1004] == 0.0 and st[1000] == 1.0
    delta = dict(zip(f["player_id"].to_list(), f["t30_start_delta"].to_list(), strict=True))
    assert delta[1006] == 1.0 and delta[1004] == -1.0  # promoted / demoted vs previous game
    assert 1007 not in {r[1] for r in _t30_rows(con)}  # listed inactive: not predicted
    with (tmp_path / "cache" / AS_OF.isoformat() / "models.pkl").open("rb") as fh:
        models = pickle.load(fh)  # noqa: S301
    assert "t30_starter" in models["pts"].names and "t30_vac_starter_min" in models["reb"].names


def test_planted_future_snapshot_does_not_change_output(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    now = TIP - timedelta(minutes=29)
    la = _store(tmp_path / "a")
    _fill(la, TIP - timedelta(minutes=40))
    _run(con, la, now, tmp_path / "a")
    ref = [r[5] for r in _t30_rows(con)]
    con.execute("DELETE FROM forward_predictions")
    con.execute("DROP TABLE forward_t30_decisions")
    lb = _store(tmp_path / "b")
    _fill(lb, TIP - timedelta(minutes=40))
    _fill(lb, TIP - timedelta(minutes=30), home_five=[1000, 1001, 1002, 1003, 1007])  # == cutoff
    _fill(lb, TIP - timedelta(minutes=10), home_five=[1003, 1004, 1005, 1006, 1007])
    _run(con, lb, now, tmp_path / "b")
    assert [r[5] for r in _t30_rows(con)] == ref
    used = {json.loads(r[5])["snapshot_fetched_at"] for r in _t30_rows(con)}
    assert used == {(TIP - timedelta(minutes=40)).isoformat()}  # not the T-30 or T-10 snapshots


def test_store_refuses_prediction_at_or_after_tipoff(con: duckdb.DuckDBPyConnection) -> None:
    p = ForwardPrediction(G1, TIP, T30_MODEL_NAME, "v", "pts", {"mean": 1.0, "std": 1.0}, 1000)
    with pytest.raises(LeakageError):
        append_predictions(con, "r", TIP, [p])


def test_open_decisions_measurement(tmp_path: Path) -> None:
    """Questionable at T-60: resolved when the team is Confirmed at the pre-T-30 snapshot,
    open while it is still Expected; the snapshot after the cutoff is not used."""
    lcon = _store(tmp_path)
    nba = build_fixture_db(":memory:")
    nba.execute(
        "CREATE TABLE IF NOT EXISTS player_availability (player_id INT, as_of TIMESTAMP, "
        "game_id VARCHAR, status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)"
    )
    # report stamped 16:00 ET (21:00Z), tip 00:00Z -> usable at T-60
    for pid, st in ((1002, "questionable"), (1003, "doubtful"), (1004, "probable")):
        nba.execute(
            "INSERT INTO player_availability VALUES (?, ?, NULL, ?, NULL, "
            "'nba_official_report', NULL)",
            [pid, datetime(2026, 2, 21, 16, 0), st],
        )
    for gid in (G1, G2):
        upsert_tip(lcon, gid, date(2026, 2, 21), TIP, "schedule", TIP)
    # pre-cutoff snapshot: G1 home Confirmed (1003 inactive), G1 away Expected
    rows = team_rows(
        G1, T1, True, [1000, 1001, 1002, 1004, 1005], [1003, 1006, 1007], inactive=(1003,)
    )
    rows += team_rows(G1, T2, False, P2[:5], P2[5:], status=EXPECTED)
    add_snapshot(lcon, TIP - timedelta(minutes=40), date(2026, 2, 21), rows)
    od = open_decisions(lcon, nba)
    assert od.n_candidates == 2  # 1004 is only probable
    assert od.by_state == {"resolved_active": 1, "resolved_inactive": 1}
    assert od.n_open == 0 and od.n_games_with_candidates == 1
    # a LATER pre-cutoff snapshot where both teams are back to Expected is the one that counts
    nba.execute(
        "INSERT INTO player_availability VALUES (1101, ?, NULL, 'questionable', NULL, "
        "'nba_official_report', NULL)",
        [datetime(2026, 2, 21, 16, 0)],
    )
    add_snapshot(
        lcon,
        TIP - timedelta(minutes=35),
        date(2026, 2, 21),
        team_rows(G1, T2, False, P2[:5], P2[5:], status=EXPECTED)
        + team_rows(G1, T1, True, P1[:5], P1[5:], status=EXPECTED),
    )
    od2 = open_decisions(lcon, nba)
    assert od2.n_candidates == 3 and od2.n_open == 3 and od2.n_games_with_open == 1
    # a snapshot at/after the cutoff never counts
    add_snapshot(lcon, TIP - timedelta(minutes=30), date(2026, 2, 21), rows)
    assert open_decisions(lcon, nba).n_open == 3


def test_wilson_interval() -> None:
    lo, hi = wilson(5, 20)
    assert 0.1 < lo < 0.25 < hi < 0.47
    assert all(x != x for x in wilson(0, 0))


def test_report_paired_t30_section(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    ensure_tables(con)
    con.execute("DELETE FROM forward_scores")
    d = date(2026, 3, 1)
    for i in range(6):
        for model, crps in (("props_context_residual", 1.0), (T30_MODEL_NAME, 0.9)):
            con.execute(
                "INSERT INTO forward_scores VALUES (now(), ?, ?, 2025, ?, 'v', 'pts', ?, now(), "
                "10, 9, NULL, NULL, ?, 'scored')",
                [f"G{i}", d + timedelta(days=i), model, 100 + i, crps],
            )
    text = build_report(con, season=2025, lineups_db=tmp_path / "none.duckdb")
    assert f"Paired SHADOW: {T30_MODEL_NAME} minus props_context_residual" in text
    assert "- pts: delta CRPS -0.1000" in text and "6 paired player-games" in text
    assert "no lineup collector database yet" in text
    con.execute("DELETE FROM forward_scores")


_ = (T1, T2, T3, T4, AS_OF)


def test_schedule_from_lineups_and_due_cli(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from nba.daily.t30 import schedule_from_lineups
    from nba.lineups.__main__ import main

    db = tmp_path / "l.duckdb"
    lcon = connect_lineups(db)
    for gid in (G1, G2):
        upsert_tip(lcon, gid, date(2026, 2, 21), TIP, "schedule", TIP)
    _fill(lcon, TIP - timedelta(minutes=40))
    games = schedule_from_lineups(lcon)("2025-26")
    assert {(g.game_id, g.home_team, g.away_team) for g in games} == {
        (G1, T1, T2),
        (G2, T3, T4),
    }
    lcon.close()
    due = ["--db-path", str(db), "due", "--now"]
    assert main([*due, (TIP - timedelta(minutes=31)).isoformat()]) == 1
    assert main([*due, (TIP - timedelta(minutes=30)).isoformat()]) == 0
    assert main([*due, TIP.isoformat()]) == 1  # tipped: nothing to decide
    capsys.readouterr()
    lcon = connect_lineups(db, read_only=True)
    s = run_t30(
        con,
        lcon,
        date(2026, 2, 21),
        schedule_fn=schedule_from_lineups(lcon),
        now=TIP - timedelta(minutes=25),
        model_cache=tmp_path / "cache",
    )
    assert s.n_logged_games == 2


def test_tip_known_game_with_no_snapshot_is_recorded_not_dropped(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The day's feed file can be unpublished (HTTP 302) so a game has a tip but no snapshot:
    the T-30 run must RECORD it as skipped, and ``due`` must fire so the run happens."""
    from nba.daily.t30 import schedule_from_lineups
    from nba.lineups.__main__ import main

    db = tmp_path / "l.duckdb"
    lcon = connect_lineups(db)
    upsert_tip(lcon, G1, date(2026, 2, 21), TIP, "schedule", TIP)
    lcon.close()
    assert (
        main(["--db-path", str(db), "due", "--now", (TIP - timedelta(minutes=29)).isoformat()]) == 0
    )
    lcon = connect_lineups(db, read_only=True)
    s = run_t30(
        con, lcon, date(2026, 2, 21), schedule_fn=schedule_from_lineups(lcon),
        now=TIP - timedelta(minutes=29), model_cache=tmp_path / "cache",
    )  # fmt: skip
    assert s.n_slate == 1 and s.skipped == {G1: "no_snapshot_before_t30"}
    assert s.n_logged_games == 0 and s.n_rows_written == 0
    row = con.execute("SELECT outcome, reason FROM forward_t30_decisions WHERE game_id = ?", [G1])
    assert row.fetchone() == ("skipped", "no_snapshot_before_t30")


def test_official_roster_call_is_rate_limited_and_cached(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The 5-minute tick must never fetch 30 rosters unthrottled: the roster call gets a
    >= 2.5 s limiter and the per-date cache; a second tick is a pure cache hit."""
    from nba.daily.t30 import T30_MIN_INTERVAL_S
    from nba.props.rosters import parse_common_team_roster

    lcon = connect_lineups(tmp_path / "l.duckdb")
    _fill(lcon, TIP - timedelta(minutes=40))
    calls: list[int] = []

    def fetch(team: int, season: str) -> pl.DataFrame:
        calls.append(team)
        return parse_common_team_roster(
            pl.DataFrame({"PLAYER_ID": [team % 100000], "EXP": ["5"]}), team
        )

    class Spy(RateLimiter):
        waits = 0

        def wait(self) -> None:
            Spy.waits += 1

    spy = Spy(T30_MIN_INTERVAL_S)
    kw = dict(roster_source="official", roster_dir=tmp_path / "r", roster_fetch=fetch,
              rate_limiter=spy, model_cache=tmp_path / "cache",
              static_fetch=lambda pid: pl.DataFrame())  # fmt: skip
    now = TIP - timedelta(minutes=25)
    run_t30(con, lcon, AS_OF, schedule_fn=_sched, now=now, **kw)  # type: ignore[arg-type]
    assert len(calls) == 30 and Spy.waits >= 30 and spy.min_interval_s >= 2.5
    con.execute("DELETE FROM forward_t30_decisions")
    run_t30(con, lcon, AS_OF, schedule_fn=_sched, now=now, **kw)  # type: ignore[arg-type]
    assert len(calls) == 30  # second tick: per-date cache, no fetches


def test_schedule_marker_written_only_after_success(tmp_path: Path) -> None:
    from nba.lineups.__main__ import MAX_SCHEDULE_ATTEMPTS, ensure_schedule_tips

    lcon = connect_lineups(":memory:")
    d = date(2026, 10, 28)
    calls: list[str] = []

    def down(season: str) -> list[ScheduledGame]:
        calls.append(season)
        raise TimeoutError("x")

    now = datetime(2026, 10, 28, 15, 0)
    for _ in range(MAX_SCHEDULE_ATTEMPTS + 2):
        ensure_schedule_tips(lcon, d, now, down, tmp_path)
    assert len(calls) == MAX_SCHEDULE_ATTEMPTS  # bounded retries, not one per tick forever
    assert not (tmp_path / "schedule_checked_2026-10-28").exists()

    tmp2 = tmp_path / "ok"
    ensure_schedule_tips(lcon, d, now, lambda s: [], tmp2)
    assert (tmp2 / "schedule_checked_2026-10-28").exists()
