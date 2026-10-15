"""Morning report: primary-source counts per stat make a silent fallback visible."""

from __future__ import annotations

from pathlib import Path

import duckdb

from nba.daily.report import _primary_source_section
from tests.export.test_best_model import (  # noqa: F401
    DAY,
    G_REG,
    _avg_row,
    _insert_avg,
    _insert_pplay,
    db,
)


def _text(db_path: Path, season: int = 2026) -> str:
    con = duckdb.connect(str(db_path))
    try:
        return "\n".join(_primary_source_section(con, season))
    finally:
        con.close()


def test_counts_per_stat_and_pplay(db: Path) -> None:  # noqa: F811
    con = duckdb.connect(str(db))
    _insert_avg(con, _avg_row())
    _insert_pplay(con, G_REG, 1000, 0.9)
    _insert_pplay(con, G_REG, 1001, None)
    con.close()
    text = _text(db)
    assert str(DAY) in text
    assert "- pts: production triples 3, with AVG 1, no AVG 2" in text
    assert "P(play): R 1, production only 1" in text
    assert "FALLBACK" not in text.replace("props_context_residual", "")  # a0 source row exists


def test_missing_a0_source_feed_is_flagged(db: Path) -> None:  # noqa: F811
    """AVG rows cite the _lt variant as A0 source, but it logged nothing."""
    con = duckdb.connect(str(db))
    row = list(_avg_row())
    row[-1] = "props_context_residual_lt"
    _insert_avg(con, tuple(row))
    con.close()
    text = _text(db)
    assert "a0 source props_context_residual_lt" in text
    assert "FALLBACK: AVG a0 source logged 0 rows" in text


def test_stat_with_no_avg_while_others_have_it_is_flagged(db: Path) -> None:  # noqa: F811
    con = duckdb.connect(str(db))
    _insert_avg(con, _avg_row(stat="reb"))
    con.close()
    # production fixture rows exist only for pts, so reb has no production triples; pts has no AVG
    assert "FALLBACK: no AVG rows for this stat" in _text(db)


def test_other_seasons_and_empty_db_give_no_section(db: Path) -> None:  # noqa: F811
    assert _text(db, 2025) == ""
    con = duckdb.connect(str(db))
    con.execute("DELETE FROM forward_predictions")
    con.close()
    assert _text(db) == ""
