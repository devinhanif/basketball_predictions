"""prop_lines is read selectively (only the rows and columns a shown prop uses). The quotes the
store keeps must be exactly what reading the whole table gives, in the same order."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from parlayhitters_api.export_store import PROP_LINE_COLS, ExportStore, _table

FIX = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.mark.parametrize("name", ["export_small", "export_best_real"])
def test_selective_read_keeps_exactly_the_full_read_quotes(name: str) -> None:
    store = ExportStore(FIX / name)
    con = duckdb.connect(str(FIX / name / "export.duckdb"), read_only=True)
    try:
        everything = _table(con, "prop_lines")
        grids = _table(con, "prop_grids")
    finally:
        con.close()
    props = store._latest_props(grids)
    full = ExportStore._quotes_by_prop(everything, props)
    assert full, "the fixture should carry quotes"
    assert list(full) == list(store.quotes)
    assert full == store.quotes  # same quotes, same order within each prop


def test_only_the_columns_the_app_reads_are_kept() -> None:
    assert set(PROP_LINE_COLS) == {
        "run_id", "game_id", "player_id", "stat", "book", "line", "side", "price_american",
        "unusable", "unusable_reason", "is_alternate", "minutes_before_tip", "snapshot_ts",
    }  # fmt: skip
