"""Forward tables and ``player_availability`` are declared in more than one place: do they agree?

Each DDL is executed into its own in-memory DuckDB and compared through ``PRAGMA table_info``
(column order, name, type, NOT NULL, default, primary key), so comments and whitespace do not
count and every real difference does.

Result (2026-11-17): ``forward_predictions`` and ``forward_scores`` are identical in
``nba/db/schema.sql`` and ``nba/daily/store.py``; ``player_availability`` is identical in its
three places. The forward table SETS differ: ``forward_withdrawals`` exists only in
``nba/daily/store.py``. So no single source was chosen (the code is left alone) and the set test is
``xfail(strict=True)``: it starts passing, and fails loudly, the day the two are reconciled.
"""

from __future__ import annotations

from collections.abc import Callable

import duckdb
import pytest

from nba.daily import store
from nba.db.connect import apply_schema
from nba.ingest import availability, injury_intraday

Columns = list[tuple[object, ...]]


def _schema_sql() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    apply_schema(con)
    return con


def _store_ddl() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    store.ensure_tables(con)
    return con


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> Columns:
    # (cid, name, type, notnull, dflt_value, pk): the full column contract in declaration order
    return [tuple(r) for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()]


def _forward_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'forward\\_%' ESCAPE '\\' AND table_type = 'BASE TABLE'"
    ).fetchall()
    return {str(r[0]) for r in rows}


@pytest.mark.parametrize("table", ["forward_predictions", "forward_scores"])
def test_shared_forward_tables_are_identical(table: str) -> None:
    a, b = _columns(_schema_sql(), table), _columns(_store_ddl(), table)
    assert a, f"{table} missing from schema.sql"
    assert a == b, f"{table}: schema.sql {a} != store.py {b}"


@pytest.mark.xfail(
    strict=True,
    reason="forward_withdrawals is created only by nba/daily/store.py, not by nba/db/schema.sql",
)
def test_forward_table_sets_are_identical() -> None:
    assert _forward_tables(_schema_sql()) == _forward_tables(_store_ddl())


def test_the_documented_difference_is_the_only_one() -> None:
    """Pins the xfail above to exactly one known difference."""
    a, b = _forward_tables(_schema_sql()), _forward_tables(_store_ddl())
    assert a == {"forward_predictions", "forward_scores"}
    assert b - a == {"forward_withdrawals"}


def _from_ingest_availability() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    availability.ensure_player_availability_table(con)
    return con


def _from_injury_intraday() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(injury_intraday._REPORTS_DDL)
    return con


@pytest.mark.parametrize(
    "build", [_from_ingest_availability, _from_injury_intraday], ids=["availability", "intraday"]
)
def test_player_availability_is_identical(build: Callable[[], duckdb.DuckDBPyConnection]) -> None:
    want = _columns(_schema_sql(), "player_availability")
    assert want, "player_availability missing from schema.sql"
    assert _columns(build(), "player_availability") == want
