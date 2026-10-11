"""Schema tests: apply schema.sql to a fresh in-memory DuckDB and assert
every table from CLAUDE.md's "Data schema (DuckDB)" section exists with
its expected columns.
"""

from __future__ import annotations

import duckdb
import pytest

from nba.db.connect import connect

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "games": {
        "game_id",
        "game_date",
        "season",
        "home_team",
        "away_team",
        "home_pts",
        "away_pts",
        "national_tv",
    },
    "possessions": {
        "game_id",
        "poss_idx",
        "period",
        "clock_start",
        "clock_end",
        "off_team",
        "def_team",
        "off_players",
        "def_players",
        "score_diff",
        "outcome",
        "shooter_id",
        "shot_zone",
        "assister_id",
        "oreb",
        "fta",
        "pts",
        "n_oreb",
        "n_dreb",
    },
    "stints": {
        "game_id",
        "team_id",
        "period",
        "start_clock",
        "end_clock",
        "players",
        "stint_idx",
    },
    "player_rates": {
        "player_id",
        "as_of",
        "usage",
        "tov_rate",
        "ft_pct",
        "foul_draw",
        "zone_mix",
        "zone_fg",
        "orb_rate",
        "drb_rate",
        "min_per_g",
        "n_poss",
        "source",
    },
    "team_context": {"game_id", "team_id", "rest_days", "b2b", "travel_miles", "injured_out"},
    "players_static": {
        "player_id",
        "position",
        "height_in",
        "weight_lb",
        "birth_date",
        "draft_year",
        "draft_pick",
        "college",
        "college_stats",
    },
    "player_game_stats": {
        "game_id",
        "player_id",
        "team_id",
        "minutes",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "stl",
        "blk",
        "tov",
        "starter",
    },
    "team_game_advanced": {
        "game_id",
        "team_id",
        "off_rating",
        "def_rating",
        "net_rating",
        "pace",
        "efg_pct",
        "tov_pct",
        "oreb_pct",
        "ft_rate",
    },
    "prop_predictions": {
        "run_id",
        "game_id",
        "player_id",
        "stat",
        "mean",
        "dist_family",
        "dist_params",
        "p_ge",
        "q10",
        "q50",
        "q90",
        "made_at",
    },
    "kalshi_markets": {
        "ticker",
        "series_ticker",
        "event_ticker",
        "title",
        "player_id",
        "stat",
        "threshold",
        "open_time",
        "close_time",
        "settled_ts",
        "result",
    },
    "kalshi_prices": {
        "ticker",
        "ts",
        "yes_bid",
        "yes_ask",
        "last",
        "volume",
        "open_interest",
        "source",
    },
    "paper_trades": {
        "trade_id",
        "created_at",
        "legs",
        "model_prob",
        "model_prob_lo",
        "model_prob_hi",
        "price",
        "fee_model",
        "expected_value",
        "settled",
        "outcome",
        "realized_pnl",
    },
    "experiments": {
        "run_id",
        "created_at",
        "rung",
        "model_name",
        "config",
        "metrics",
        "artifact_path",
        "stage",
        "version",
        "alias",
        "tags",
        "metadata",
    },
}


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    yield con
    con.close()


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
    return {row[1] for row in rows}


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_has_expected_columns(con: duckdb.DuckDBPyConnection, table: str) -> None:
    actual = _columns(con, table)
    expected = EXPECTED_COLUMNS[table]
    missing = expected - actual
    assert not missing, f"{table} missing columns: {missing}"


def test_schema_apply_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    from nba.db.connect import apply_schema

    apply_schema(con)
    apply_schema(con)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert set(EXPECTED_COLUMNS).issubset(tables)


_LEGACY_POSSESSIONS = (
    "CREATE TABLE possessions (game_id VARCHAR, poss_idx INT, period INT, clock_start FLOAT, "
    "clock_end FLOAT, off_team INT, def_team INT, off_players INT[5], def_players INT[5], "
    "score_diff INT, outcome VARCHAR, shooter_id INT, shot_zone VARCHAR, assister_id INT, "
    "oreb BOOLEAN, fta INT, pts INT, PRIMARY KEY (game_id, poss_idx))"
)


def test_connect_adds_rebound_count_columns_to_a_legacy_possessions_table(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An existing nba.duckdb predates n_oreb / n_dreb: connect() must add them, keep the rows."""
    db = tmp_path / "legacy.duckdb"
    raw = duckdb.connect(str(db))
    raw.execute(_LEGACY_POSSESSIONS)
    raw.execute(
        "INSERT INTO possessions VALUES ('g', 0, 1, 700, 690, 1, 2, NULL, NULL, 0, 'TOV', "
        "NULL, NULL, NULL, TRUE, 0, 0)"
    )
    raw.close()
    con = connect(db)
    cols = {r[0]: r[1] for r in con.execute("DESCRIBE possessions").fetchall()}
    row = con.execute("SELECT game_id, oreb, n_oreb, n_dreb FROM possessions").fetchall()
    con.close()
    assert cols["n_oreb"] == "INTEGER" and cols["n_dreb"] == "INTEGER"
    assert row == [("g", True, None, None)]  # old rows keep NULL until the backfill
    connect(db).close()  # idempotent
