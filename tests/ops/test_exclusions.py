"""nba.ops.exclusions: rule-selected, read-only, never touches outcome scoring."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from nba.ops import exclusions as ex

ENTRY = ex.Exclusion("e1", "stale_price", date(2026, 10, 23), 1800.0, ("market_at_prediction",))


def _db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute(
        """CREATE TABLE market_at_prediction (capture_kind VARCHAR, run_id VARCHAR,
           made_at TIMESTAMP,
           game_id VARCHAR, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT,
           event_date DATE, ml_age_s DOUBLE, prop_json JSON)"""
    )
    t = datetime(2026, 10, 23, 20, 0)
    d = date(2026, 10, 23)
    prop = lambda a: json.dumps([{"age_s": a}, {"age_s": a + 100}])  # noqa: E731
    rows = [
        ("forward", "r", t, "g1", "m", "v", "win_prob_home", -1, d, 3000.0, None),  # stale
        ("forward", "r", t, "g2", "m", "v", "win_prob_home", -1, d, 600.0, None),  # fresh
        ("forward", "r", t, "g1", "m", "v", "pts", 7, d, None, prop(2500.0)),  # stale prop
        ("forward", "r", t, "g1", "m", "v", "ast", 8, d, None, prop(300.0)),  # fresh prop
        ("forward", "r", t, "g1", "m", "v", "reb", 9, d, None, "[]"),  # no quote: not stale
        ("forward", "r", t, "g3", "m", "v", "win_prob_home", -1, date(2026, 10, 22), 9000.0, None),
        ("backfill_proxy", "r", t, "g4", "m", "v", "win_prob_home", -1, d, 9000.0, None),
    ]
    con.executemany("INSERT INTO market_at_prediction VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.close()
    return path


def test_stale_price_selects_by_age_slate_and_kind(tmp_path: Path) -> None:
    keys = ex.stale_price_keys(ENTRY, _db(tmp_path / "m.duckdb"))
    assert {(k["game_id"], k["target"]) for k in keys} == {("g1", "win_prob_home"), ("g1", "pts")}
    assert all(k["reason"] == "stale_price" for k in keys)


def test_registry_file_is_well_formed() -> None:
    entries = ex.load_exclusions()
    assert any(e.reason == "stale_price" and e.slate_date == date(2026, 10, 23) for e in entries)
    assert all(e.max_quote_age_s > 0 and "forward_scores" not in e.applies_to for e in entries)


def test_cli_check_reports_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = ex.main(
        ["check", "--id", "stale_price_2026-10-23", "--market-db", str(_db(tmp_path / "m"))]
    )
    assert rc == 0
    assert "2 rows" in capsys.readouterr().out
    assert ex.main(["check", "--id", "nope"]) == 1
