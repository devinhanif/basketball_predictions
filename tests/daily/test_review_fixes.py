"""Regression tests for the 2026-10-09 live-path review fixes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest

from nba.daily import pipeline as pl_mod
from nba.daily.pipeline import RunSummary, run_daily
from nba.daily.schedule import ScheduledGame
from nba.daily.settle import T30_MODEL, settle_eligible_pending
from nba.daily.store import ensure_tables
from nba.props import forward as fwd
from nba.props import full_support
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db
from tests.props.test_forward_context import AS_OF, _big_history

TIP = datetime(2026, 10, 28, 23, 30)


def _seed(con: duckdb.DuckDBPyConnection, gid: str, made_min: int, snap_min: int | None) -> None:
    ensure_tables(con)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES (?, ?, 2026, 1, 2, 110, 100)",
        [gid, date(2026, 10, 28)],
    )
    pred: dict[str, object] = {"p_home": 0.6}
    if snap_min is not None:
        pred["snapshot_fetched_at"] = (TIP - timedelta(minutes=snap_min)).isoformat()
    con.execute(
        "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
        ["r", TIP - timedelta(minutes=made_min), gid, TIP, T30_MODEL, "v1", "win_prob_home", -1,
         json.dumps(pred)],
    )  # fmt: skip


def test_t30_row_made_at_tip_minus_28_with_snapshot_tip_minus_31_is_eligible(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _seed(con, "G_ok", made_min=28, snap_min=31)
    _seed(con, "G_late_snap", made_min=28, snap_min=25)
    _seed(con, "G_no_snap", made_min=28, snap_min=None)
    counts = settle_eligible_pending(con, datetime(2026, 10, 29))
    assert counts["scored"] == 1
    got = con.execute("SELECT game_id FROM forward_scores_elig").fetchall()
    assert got == [("G_ok",)]


# -- M2: shadow arms never replace the primary -------------------------------------------------


PRIMARY = "props_context_residual"


def _primary(con: duckdb.DuckDBPyConnection, run_id: str) -> list[tuple[str, int, str, dict]]:  # type: ignore[type-arg]
    rows = con.execute(
        "SELECT game_id, player_id, target, CAST(prediction AS VARCHAR) FROM forward_predictions "
        "WHERE run_id = ? AND model_name = ? ORDER BY game_id, target, player_id",
        [run_id, PRIMARY],
    ).fetchall()
    return [(g, p, t, json.loads(d)) for g, p, t, d in rows]


def test_shadow_failures_leave_primary_rows_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    c = build_fixture_db(":memory:")
    _big_history(c, 100, seed=3)
    tip = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 30)
    games = [
        ScheduledGame("0022600901", tip, T1, T2),
        ScheduledGame("0022600902", tip + timedelta(hours=3), T3, T4),
    ]
    kw = dict(schedule_fn=lambda s: games, skip_ingest=True, skip_injury=True, dedup=False)
    base = run_daily(c, AS_OF, now=tip - timedelta(hours=5), **kw)  # type: ignore[arg-type]
    base_rows = _primary(c, base.run_id)
    assert base_rows and not base.shadow_errors

    # (a) the integer-support builder raises inside predict_slate_context
    def boom(*a: object, **k: object) -> None:
        raise ValueError("int builder broke")

    monkeypatch.setattr(fwd, "to_integer_support", boom)
    s_a = run_daily(
        c, AS_OF, now=tip - timedelta(hours=4), log_int_variant=True,
        log_lower_tail_variant=True, **kw,  # type: ignore[arg-type]
    )  # fmt: skip
    monkeypatch.undo()
    assert _primary(c, s_a.run_id) == base_rows
    assert any("_int arm" in e and "int builder broke" in e for e in s_a.shadow_errors)
    assert not s_a.errors
    assert not c.execute(
        "SELECT 1 FROM forward_predictions WHERE run_id = ? AND model_name = ?",
        [s_a.run_id, "props_context_residual_int"],
    ).fetchall()

    # (b) the pipeline frame builder raises for the _lt arm only
    real = pl_mod._frame_preds

    def selective(df: object, upcoming: object, model_name: str, *a: object, **k: object) -> object:
        if model_name.endswith("_lt"):
            raise KeyError("lt frame broke")
        return real(df, upcoming, model_name, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(pl_mod, "_frame_preds", selective)
    s_b = run_daily(
        c, AS_OF, now=tip - timedelta(hours=3), log_int_variant=True,
        log_lower_tail_variant=True, **kw,  # type: ignore[arg-type]
    )  # fmt: skip
    monkeypatch.undo()
    assert _primary(c, s_b.run_id) == base_rows
    assert any("_lt arm" in e for e in s_b.shadow_errors)

    # (c) p_ge_full cannot be built (NaN-style failure): primary stays the context model,
    # just without the additive field (settled as 'unscorable')
    def nan_fail(*a: object, **k: object) -> None:
        raise ValueError("cannot convert float NaN to integer")

    monkeypatch.setattr(full_support, "from_samples", nan_fail)
    monkeypatch.setattr(full_support, "from_dist", nan_fail)
    s_c = run_daily(c, AS_OF, now=tip - timedelta(hours=2), **kw)  # type: ignore[arg-type]
    monkeypatch.undo()
    rows_c = _primary(c, s_c.run_id)
    assert len(rows_c) == len(base_rows)
    assert all("p_ge_full" not in d for *_, d in rows_c)
    assert all(d.get("fallback_reason") != "context model failed" for *_, d in rows_c)
    assert "FAILED" not in s_c.model_status[PRIMARY]
    for (_, _, _, d0), (_, _, _, dc) in zip(base_rows, rows_c, strict=True):
        assert np.isclose(d0["mean"], dc["mean"])


# -- M3: ingest / schedule failures degrade ------------------------------------------------------


def test_ingest_failure_degrades_and_still_predicts(con: duckdb.DuckDBPyConnection) -> None:
    from tests.daily.conftest import RUN_DATE, before_tip, slate_games

    def bad_fetch(season: str) -> object:
        raise TimeoutError("stats.nba.com timed out")

    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda x: slate_games(), now=before_tip(),
        fetch_games=bad_fetch, skip_injury=True, with_props=False,  # type: ignore[arg-type]
    )  # fmt: skip
    assert s.n_predicted_games == 2 and s.n_rows_written > 0
    assert len(s.errors) == 1 and s.errors[0].startswith("ingest: failed (TimeoutError")


def test_schedule_failure_uses_cached_schedule_else_raises(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    from tests.daily.conftest import RUN_DATE, before_tip, slate_games

    cache = tmp_path / "sched"
    kw = dict(skip_ingest=True, skip_injury=True, with_props=False, schedule_cache_dir=cache)
    ok = run_daily(con, RUN_DATE, schedule_fn=lambda x: slate_games(), now=before_tip(), **kw)  # type: ignore[arg-type]
    assert not ok.errors and ok.n_predicted_games == 2

    def down(season: str) -> list[ScheduledGame]:
        raise ConnectionError("schedule endpoint down")

    s = run_daily(con, RUN_DATE, schedule_fn=down, now=before_tip(60), **kw)  # type: ignore[arg-type]
    assert s.n_predicted_games == 2
    assert len(s.errors) == 1 and "using cached schedule" in s.errors[0]
    with pytest.raises(ConnectionError):
        run_daily(
            con, RUN_DATE, schedule_fn=down, now=before_tip(30), skip_ingest=True,
            skip_injury=True, with_props=False, schedule_cache_dir=tmp_path / "empty",
        )  # fmt: skip


def test_degraded_run_exits_5_and_refused_exits_4(
    monkeypatch: pytest.MonkeyPatch, con: duckdb.DuckDBPyConnection
) -> None:
    import nba.daily.__main__ as cli
    import nba.registry as registry

    def fake_run(*a: object, **k: object) -> RunSummary:
        s = RunSummary("r", RUN_D)
        s.errors = fake_run.errors  # type: ignore[attr-defined]
        s.n_refused_after_tipoff = fake_run.refused  # type: ignore[attr-defined]
        return s

    from tests.daily.conftest import RUN_DATE as RUN_D

    monkeypatch.setattr(cli, "run_daily", fake_run)
    monkeypatch.setattr(cli, "connect_with_retry", lambda *a, **k: con)
    monkeypatch.setattr(registry, "get_registry", lambda c: None)
    argv = ["run", "--date", "2026-10-28", "--no-props"]
    fake_run.errors, fake_run.refused = [], 0  # type: ignore[attr-defined]
    assert cli.main(argv) == 0
    fake_run.refused = 1  # type: ignore[attr-defined]
    assert cli.main(argv) == 4
    fake_run.errors = ["ingest: failed"]  # type: ignore[attr-defined]
    assert cli.main(argv) == 5
    with pytest.raises(SystemExit) as e:  # argparse keeps its own code 2, never "informational"
        cli.main(["run"])
    assert e.value.code == 2


def test_referees_unmatched_exit_code_is_not_argparse_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from nba.ingest import referees

    res = SimpleNamespace(
        page_date="x", n_games=1, stored=1, unmatched_officials=["a"], unmatched_teams=[]
    )
    monkeypatch.setattr(referees, "collect", lambda **k: res)
    assert referees.main(["collect"]) == 4
    res.unmatched_officials = []
    assert referees.main(["collect"]) == 0


# -- M5: bounded retry on the DuckDB writer lock -----------------------------------------------


def _holder(path: Path, seconds: float) -> subprocess.Popen[str]:
    code = (
        "import duckdb, sys, time\n"
        f"c = duckdb.connect({str(path)!r})\n"
        "print('ready', flush=True)\n"
        f"time.sleep({seconds})\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() == "ready"
    return p


def test_connect_with_retry_waits_for_a_held_lock_then_succeeds(tmp_path: Path) -> None:
    from nba.db.connect import connect_with_retry

    db = tmp_path / "w.duckdb"
    holder = _holder(db, 1.5)
    try:
        t0 = time.monotonic()
        con = connect_with_retry(db, max_wait_s=30, first_delay_s=0.3, max_delay_s=0.5)
        waited = time.monotonic() - t0
        assert waited >= 0.5  # it really waited for the other writer
        con.execute("CREATE TABLE IF NOT EXISTS t_after (i INT)")
        con.close()
    finally:
        holder.kill()


def test_connect_with_retry_is_bounded_and_reports_busy(tmp_path: Path) -> None:
    from nba.db.connect import connect_with_retry, is_lock_error

    db = tmp_path / "w.duckdb"
    holder = _holder(db, 20)
    try:
        t0 = time.monotonic()
        with pytest.raises(duckdb.IOException) as exc:
            connect_with_retry(db, max_wait_s=1.0, first_delay_s=0.2, max_delay_s=0.4)
        assert is_lock_error(exc.value) and time.monotonic() - t0 < 10
    finally:
        holder.kill()


def test_cli_returns_db_busy_code_when_lock_wait_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nba.daily.__main__ as cli

    db = tmp_path / "w.duckdb"
    holder = _holder(db, 20)
    monkeypatch.setenv("NBA_DB_LOCK_WAIT_S", "0.5")
    try:
        assert cli.main(["--db-path", str(db), "settle"]) == 75
    finally:
        holder.kill()


def test_non_lock_errors_are_not_retried(tmp_path: Path) -> None:
    from nba.db.connect import connect_with_retry

    calls: list[float] = []
    with pytest.raises(duckdb.IOException):
        connect_with_retry(tmp_path / "nodir" / "x.duckdb", max_wait_s=30, sleep=calls.append)
    assert calls == []
