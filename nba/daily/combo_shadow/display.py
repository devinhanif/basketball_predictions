# ruff: noqa: E501
"""The daily display of the COMBO_STOCKS shadow: counts and gates only. No score, no delta, no CRPS: deltas from non-look days are not
published and decide nothing (rule 7 and the reader's ``daily_display``).

Plain SQL on the shadow's own tables (no ``nba.eval`` import: the live path may not import research code, tests/test_layering.py). The look itself
(``nba.eval.combo_shadow_live``) applies the full eligibility rule and prints servability and the missingness table.
"""

from __future__ import annotations

from typing import Any

import duckdb

LEAD = 60
TARGETS = ("blk", "stl", "pra", "pr", "pa", "ra", "bs")


def _one(con: duckdb.DuckDBPyConnection, sql: str, args: list[Any] | None = None) -> int:
    row = con.execute(sql, args or []).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def counts(con: duckdb.DuckDBPyConnection, season: int = 2026) -> dict[str, Any] | None:
    """None when the shadow never ran (no table)."""
    try:
        rows = _one(con, "SELECT count(*) FROM forward_combo_shadow")
    except duckdb.CatalogException:
        return None
    beats = con.execute(
        "SELECT status, count(*) FROM forward_combo_heartbeat WHERE slate_date >= current_date - 14 GROUP BY 1 ORDER BY 1"
    ).fetchall()
    per_target = {
        str(t): int(n)
        for t, n in con.execute(
            f"""SELECT target, count(*) FROM (SELECT s.target FROM forward_combo_shadow s JOIN games g USING (game_id)
                WHERE g.season = ? AND g.home_pts > 0 AND s.game_id LIKE '002%' AND s.made_at <= s.tipoff_utc - {LEAD} * INTERVAL 1 MINUTE
                GROUP BY s.game_id, s.player_id, s.target) GROUP BY 1""",
            [season],
        ).fetchall()
    }
    return {
        "rows": rows,
        "rows_after_tip": _one(
            con, "SELECT count(*) FROM forward_combo_shadow WHERE made_at >= tipoff_utc"
        ),
        "settled": _one(con, "SELECT count(*) FROM forward_combo_scores WHERE status = 'scored'"),
        "heartbeats_14d": {str(s): int(n) for s, n in beats},
        "on_time_triples": per_target,
        "counting_start": con.execute(
            "SELECT max(value) FROM forward_combo_meta WHERE key = 'counting_start'"
        ).fetchone()[0],  # type: ignore[index]
    }


def section(con: duckdb.DuckDBPyConnection, season: int = 2026) -> list[str]:
    c = counts(con, season)
    if c is None or (not c["rows"] and not c["heartbeats_14d"]):
        return []
    per = ", ".join(f"{t} {c['on_time_triples'].get(t, 0)}" for t in TARGETS)
    return [
        "",
        "## COMBO_STOCKS shadow: counts and gates only (no delta is published on a non-look day)",
        f"- rows {c['rows']} ({c['settled']} settled), rows stamped at or after tip {c['rows_after_tip']} (must be 0).",
        f"- on-time settled triples (regular season {season}) by target: {per} (n_min 3,000 per target at the final look).",
        f"- slate heartbeats, last 14 days: {c['heartbeats_14d'] or 'none'}; counting start: {c['counting_start'] or 'NOT SET (warm-up rows, never scored)'}.",
    ]
