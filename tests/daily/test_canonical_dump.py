"""The restructuring oracle: same decisions -> same hash; run-time columns never matter."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb

from nba.daily import canonical_dump as cd
from nba.db.connect import apply_schema


def _db(path: Path, *, run_id: str, made_at: datetime, pred: float) -> Path:
    con = duckdb.connect(str(path))
    apply_schema(con)
    con.execute(
        "INSERT INTO forward_predictions VALUES (?, ?, '0022500001', '2025-10-21 23:30:00', "
        "'rung0_injury_elo', 'v2', 'win_prob_home', -1, '{\"p\": 0.61}')",
        [run_id, made_at],
    )
    con.execute(
        "INSERT INTO forward_scores VALUES (?, '0022500001', ?, 2025, 'rung0_injury_elo', 'v2', "
        "'win_prob_home', -1, ?, 1.0, ?, 0.49, 0.15, NULL, 'scored')",
        [made_at, date(2025, 10, 21), made_at, pred],
    )
    con.close()
    return path


def test_run_time_columns_do_not_change_the_hash(tmp_path: Path) -> None:
    a = _db(tmp_path / "a.duckdb", run_id="run-1", made_at=datetime(2026, 1, 1), pred=0.61)
    b = _db(tmp_path / "b.duckdb", run_id="run-2", made_at=datetime(2026, 6, 1), pred=0.61)
    assert cd.dump(a) == cd.dump(b)
    assert cd.first_difference(a, b) is None
    assert cd.main(["--db", str(a), "--db", str(b)]) == 0


def test_a_changed_decision_changes_the_hash_and_is_located(tmp_path: Path) -> None:
    a = _db(tmp_path / "a.duckdb", run_id="run-1", made_at=datetime(2026, 1, 1), pred=0.61)
    b = _db(tmp_path / "b.duckdb", run_id="run-1", made_at=datetime(2026, 1, 1), pred=0.62)
    assert cd.dump(a)[0] != cd.dump(b)[0]
    diff = cd.first_difference(a, b)
    assert diff is not None and diff.startswith("forward_scores row 0")
    assert cd.main(["--db", str(a), "--db", str(b)]) == 1


def test_dump_writes_csv_with_both_tables(tmp_path: Path) -> None:
    a = _db(tmp_path / "a.duckdb", run_id="r", made_at=datetime(2026, 1, 1), pred=0.61)
    out = tmp_path / "dump.csv"
    sha, n_pred, n_score = cd.dump(a, out)
    text = out.read_text()
    assert (n_pred, n_score) == (1, 1)
    assert text.startswith("# forward_predictions\n") and "# forward_scores\n" in text
    assert "run-" not in text and len(sha) == 64
