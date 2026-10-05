"""DuckDB schema and connection helpers for the NBA prediction pipeline."""

from nba.db.connect import DEFAULT_DB_PATH, apply_schema, connect

__all__ = ["DEFAULT_DB_PATH", "apply_schema", "connect"]
