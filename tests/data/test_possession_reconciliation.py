"""Possession-count reconciliation scaffold.

CLAUDE.md requires validating possession counts against the box-score
estimate ``FGA + 0.44*FTA + TOV - OREB`` per team-game (mean error <= 1).
That needs a `possessions` table built by the play-by-play parser
(nba/parse/, not yet built -- see Milestone "possession/stint parser")
plus team-level FGA/FTA/OREB box score fields that are not present on the
`player_game_stats` schema in CLAUDE.md. This test is a placeholder that
documents the contract the parser milestone must satisfy; it is skipped
until both the parser and the box-score fields exist.
"""

from __future__ import annotations

import duckdb
import pytest

from tests.fixtures.loader import build_fixture_db


def _possessions_table_is_populated(con: duckdb.DuckDBPyConnection) -> bool:
    count = con.execute("SELECT COUNT(*) FROM possessions").fetchone()[0]
    return bool(count)


@pytest.mark.skip(
    reason=(
        "Deferred to the possession/stint parser milestone (nba/parse/): "
        "requires the `possessions` table to be populated from play-by-play "
        "and team-level FGA/FTA/OREB box score aggregates not yet carried "
        "by player_game_stats. See CLAUDE.md possession-count reconciliation."
    )
)
def test_possession_count_reconciliation_mean_error_within_one() -> None:
    con = build_fixture_db(":memory:")
    assert _possessions_table_is_populated(con), (
        "remove this skip once the parser populates `possessions`"
    )
    # Full reconciliation (team-game mean |possessions - est| <= 1) belongs
    # to the parser milestone's own test suite.
    con.close()
