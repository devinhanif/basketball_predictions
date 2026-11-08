"""Display-only late-report line (nba.jarvis.late_report): shown at >= 1.0 point of mean or when the
late arm's report excluded the player; never changes a label, key, inputs or number."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.daily.late_report import LATE_DECISIONS_DDL
from nba.daily.store import ensure_tables
from nba.jarvis import late_report as jl
from nba.jarvis.models import Play, PlayLeg
from nba.jarvis.render import play_lines

TIP = datetime(2026, 11, 3, 1, 30)
NOW = TIP - timedelta(minutes=5)
G = "0022600167"
TATUM, WHITE, GEORGE, BROWN = 1628369, 1628401, 202331, 1627759
NAMES = {TATUM: "Tatum", WHITE: "White", GEORGE: "George", BROWN: "Brown"}


def _db(tmp: Path) -> Path:
    path = tmp / "nba.duckdb"
    con = duckdb.connect(str(path))
    ensure_tables(con)
    con.execute(LATE_DECISIONS_DDL)
    rows = [
        ("props_context_residual", TIP - timedelta(hours=6), TATUM, 25.33),
        ("props_context_residual", TIP - timedelta(hours=6), WHITE, 12.71),
        ("props_context_residual", TIP - timedelta(hours=18), GEORGE, 16.73),  # stale row
        ("props_context_residual", TIP - timedelta(hours=6), BROWN, 22.04),
        (jl.LATE_MODEL, TIP - timedelta(minutes=14), TATUM, 27.1),
        (jl.LATE_MODEL, TIP - timedelta(minutes=14), WHITE, 13.2),  # +0.49: below the bar
        ("props_context_residual_t15", TIP - timedelta(minutes=14), TATUM, 40.0),  # other arm
    ]
    for model, made, pid, mean in rows:
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["r", made, G, TIP, model, "v", "pts", pid, json.dumps({"mean": mean})],
        )
    con.execute(
        "INSERT INTO forward_late_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [G, TIP - timedelta(minutes=14), TIP, TIP - timedelta(minutes=15),
         datetime(2026, 11, 2, 20, 15), json.dumps({str(GEORGE): "out", str(BROWN): "doubtful"}),
         False, None, "logged", "ok", 2],
    )  # fmt: skip
    con.close()
    return path


def _play(pid: int, stat: str = "pts") -> Play:
    leg = PlayLeg("kalshi", f"T{pid}", "yes", 0.5, "prop", f"p{pid} 25+ pts", player_id=pid,
                  stat=stat, threshold=24.5)  # fmt: skip
    return Play(G, "BOS@HOU", TIP, (leg,), "thin", 2.0, 0.52, 1.0, 0.5, 3, 1.5, "r",
                model_prob=0.55, inputs={"x": 1})  # fmt: skip


def test_late_line_rules() -> None:
    assert jl.late_line("Tatum", 25.33, 27.1, None) == "latest report: Tatum 27.1 vs 25.3"
    assert jl.late_line("White", 12.71, 13.2, None) is None
    assert jl.late_line("White", 12.0, 13.0, None) == "latest report: White 13.0 vs 12.0"
    assert jl.late_line("X", None, 20.0, None) is None
    assert jl.late_line("X", 20.0, None, None) is None
    out = jl.late_line("George", 16.73, None, "out")
    assert out == "latest report: George OUT (production 16.7)"
    out = jl.late_line("Brown", 22.04, None, "doubtful")  # DOUBTFUL is never shown as OUT
    assert out == "latest report: Brown DOUBTFUL (production 22.0)"
    assert jl.late_line("Brown", None, None, "doubtful") == "latest report: Brown DOUBTFUL"
    assert jl.late_line("Tatum", 25.33, 27.1, None) == "latest report: Tatum 27.1 vs 25.3"


def test_excluded_json_parsing() -> None:
    assert jl._parse_excluded('{"5": "doubtful", "6": "out"}') == {5: "doubtful", 6: "out"}
    assert jl._parse_excluded("[5]") == {5: "out/doubtful"}  # no status stored: never "OUT"
    assert jl._parse_excluded(None) == {}


def test_annotate_adds_display_lines_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    plays = [_play(TATUM), _play(WHITE), _play(GEORGE), _play(TATUM, "reb"), _play(BROWN)]
    out = jl.annotate(plays, db, NAMES.__getitem__, NOW)
    assert [p.display_lines for p in out] == [
        ("latest report: Tatum 27.1 vs 25.3",),
        (),
        ("latest report: George OUT (production 16.7)",),
        (),
        ("latest report: Brown DOUBTFUL (production 22.0)",),
    ]
    for a, b in zip(plays, out, strict=True):  # nothing but the display line changed
        assert replace(b, display_lines=()) == a and a.key == b.key and a.inputs == b.inputs
    lines = play_lines(out[0], "America/New_York")
    assert "latest report: Tatum 27.1 vs 25.3" in lines
    assert play_lines(plays[1], "America/New_York") == play_lines(out[1], "America/New_York")


def test_rows_made_after_now_are_not_shown(tmp_path: Path) -> None:
    db = _db(tmp_path)
    early = TIP - timedelta(minutes=20)  # before the late arm ran: no late row, no decision yet
    out = jl.annotate([_play(TATUM), _play(GEORGE)], db, NAMES.__getitem__, early)
    assert [p.display_lines for p in out] == [(), ()]


def test_fail_soft(tmp_path: Path) -> None:
    plays = [_play(TATUM)]
    assert jl.annotate(plays, tmp_path / "missing.duckdb", NAMES.__getitem__, NOW) == plays

    def broken(_p: Path) -> duckdb.DuckDBPyConnection:
        raise duckdb.IOException("Could not set lock on file")

    db = _db(tmp_path)
    assert jl.annotate(plays, db, NAMES.__getitem__, NOW, connect=broken) == plays
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()  # no forward tables yet
    assert jl.annotate(plays, empty, NAMES.__getitem__, NOW) == plays


def test_runner_late_lines_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planted failures in the import and the lookups outside annotate return the plays as they
    were (the 10-28 every-tick crash shape)."""
    import sys
    from types import SimpleNamespace

    from nba.jarvis import runner

    plays = [_play(TATUM)]
    no_key = SimpleNamespace(cfg=SimpleNamespace(paths={}), kb=SimpleNamespace(name=str))
    assert runner._late_lines(no_key, plays, NOW) is plays  # type: ignore[arg-type]
    no_kb = SimpleNamespace(cfg=SimpleNamespace(paths={"nba_db": Path("x")}))
    assert runner._late_lines(no_kb, plays, NOW) is plays  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, "nba.jarvis.late_report", None)  # import raises ImportError
    ok = SimpleNamespace(
        cfg=SimpleNamespace(paths={"nba_db": Path("x")}), kb=SimpleNamespace(name=str)
    )
    assert runner._late_lines(ok, plays, NOW) is plays  # type: ignore[arg-type]
