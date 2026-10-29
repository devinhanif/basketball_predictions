"""2026-10-28 incident: a None-formatting crash in the recap took every tick down (463 crashes), so
the halftime scorer never ran. These tests pin the fixes: the None-safe kill-switch line, a recap
that can no longer stop a tick, and a store-busy message that names the file and the holder."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis import halftime_overs as HO
from nba.jarvis import runner, sources
from nba.jarvis.book import KillState
from nba.jarvis.config import JarvisConfig
from nba.jarvis.posts import recap_lines
from nba.jarvis.render import scored_line
from nba.jarvis.runner import Runtime, run_slate, run_tick, tick_line
from tests.jarvis.conftest import SLATE, TIP1, Sent
from tests.jarvis.test_features import live_files

CLOCK = TIP1 + timedelta(minutes=70)


def test_recap_kill_line_survives_a_state_with_bets_but_no_mean(
    cfg: JarvisConfig, rt: Runtime
) -> None:
    """kill_switch() returns KillState(False, n, None) while 0 < n < min_bets: the old line
    formatted None with ':+.2f' (TypeError at posts.py:235)."""
    day = date(2026, 10, 27)
    txt = "\n".join(recap_lines(cfg, rt.book, day, [], KillState(False, 4, None)))
    assert f"Kill switch off: no settled CLV yet (n=4; needs {cfg.kill_min_bets}" in txt
    txt = "\n".join(recap_lines(cfg, rt.book, day, [], KillState(False, 0, None)))
    assert f"Kill switch: needs {cfg.kill_min_bets} settled bets with a close (has 0)" in txt
    txt = "\n".join(recap_lines(cfg, rt.book, day, [], KillState(False, 30, 1.234)))
    assert "Kill switch off: mean CLV +1.23c over the last 30 bets" in txt
    # an active switch with no mean also prints (message() was already None-safe)
    txt = "\n".join(recap_lines(cfg, rt.book, day, [], KillState(True, 30, None, True, day)))
    assert "KILL SWITCH LATCHED" in txt and "n/a" in txt


def test_scored_line_tolerates_a_missing_pnl() -> None:
    row: dict[str, object] = {
        "slate_date": "2026-10-27", "matchup": "A@B", "leg_desc": "X 20+ pts", "venue": "kalshi",
        "price": 0.4, "edge_label": "thin", "edge_cents": 1.0, "won": False, "pnl_usd": None,
    }  # fmt: skip
    assert "pnl n/a" in scored_line(row)
    assert "pnl $-2.00" in scored_line({**row, "pnl_usd": -2.0})


def test_a_recap_bug_cannot_take_the_halftime_check_in_down(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_slate(rt, SLATE)
    live_files(cfg.root, SLATE, "STATUS_HALFTIME", away=55, home=48)
    rt.clock = lambda: CLOCK

    def boom(*a: Any, **k: Any) -> None:
        raise TypeError("unsupported format string passed to NoneType.__format__")

    monkeypatch.setattr(runner, "_maybe_recap", boom)
    monkeypatch.setattr(
        HO,
        "score_halftimes",
        lambda *a, **k: {"ok": True, "rows": [], "stored": 7, "counts": {"games_halftime_seen": 1}},
    )
    rep = run_tick(rt, SLATE)
    assert rep["recap"] == {"kind": "recap", "ok": False, "error": "TypeError"}
    assert any("halftime check-in" in t for t in sent.texts)  # the tick went on
    line = tick_line(rep)
    assert "recap_error=TypeError" in line and "ht_scored=7rows/1games" in line


def test_tick_line_is_unchanged_when_nothing_was_swallowed() -> None:
    rep = {"at": "2026-10-29T01:00:00", "plays": 2, "alerts": [], "halftime": [], "ingame": []}
    assert tick_line(rep) == "tick 2026-10-29T01:00:00: plays=2 alerts=0 slate_send=no"
    rep["halftime"] = [{"kind": "halftime_scorer", "ok": False, "error": "ForeignStoreError"}]
    assert tick_line(rep).endswith("| halftime_scorer_error=ForeignStoreError")


LOCK_MSG = (
    'IO Error: Could not set lock on file "/x/nba.duckdb": Conflicting lock is held in '
    "/usr/bin/python3.12 (PID {pid}) by user devin. See also https://duckdb.org/docs/stable"
)


def test_describe_store_busy_names_the_file_and_the_holder() -> None:
    me = sources.describe_store_busy(duckdb.IOException(LOCK_MSG.format(pid=os.getpid())))
    assert me.startswith("/x/nba.duckdb held by PID " + str(os.getpid()))
    assert "python" in me.lower() or "pytest" in me.lower()
    gone = sources.describe_store_busy(duckdb.IOException(LOCK_MSG.format(pid=2**22 + 12345)))
    assert "exited since" in gone and gone.startswith("/x/nba.duckdb held by PID")
    other = sources.describe_store_busy(duckdb.IOException("disk on fire"))
    assert other.startswith("unknown file; holder unknown")


def test_a_real_duckdb_lock_is_named_in_the_idle_report(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "held.duckdb"
    code = (
        "import duckdb,sys;c=duckdb.connect(sys.argv[1]);c.execute('create table t(a int)');"
        "print('ready',flush=True);sys.stdin.readline()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code, str(db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"

        def locked(*a: Any, **k: Any) -> None:
            duckdb.connect(str(db), read_only=True)

        monkeypatch.setattr(runner, "_tick", lambda rt, slate, now: locked())
        rep = run_tick(rt, SLATE)
        assert rep["idle"].startswith("store busy, retry next tick (")
        assert str(db) in rep["idle"] and f"held by PID {holder.pid}" in rep["idle"]
        assert "alerts=0" in tick_line(rep) and str(db) in tick_line(rep)
    finally:
        holder.kill()
        holder.wait()
