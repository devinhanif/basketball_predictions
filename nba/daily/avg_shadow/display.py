# ruff: noqa: E501
"""The daily display of the AVG shadow (rule item 7): counts and gates only. No score, no delta, no CRPS: deltas from non-look days are not published and decide nothing.

Plain SQL on the shadow's own tables (no ``nba.eval`` import: the live path may not import research code). Coverage here is distinct on-time AVG
triples over distinct FULL triples of settled regular-season games, an approximation; the look uses the full eligibility rule in ``nba.eval.avg_shadow_live``.
"""

from __future__ import annotations

from typing import Any

import duckdb

from nba.ops.exclusions import avg_split

LEAD = 60


def _one(con: duckdb.DuckDBPyConnection, sql: str, args: list[Any] | None = None) -> int:
    row = con.execute(sql, args or []).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def counts(con: duckdb.DuckDBPyConnection, season: int = 2026) -> dict[str, Any] | None:
    """None when the shadow never ran (no table)."""
    try:
        rows = _one(con, "SELECT count(*) FROM forward_avg_shadow")
    except duckdb.CatalogException:
        return None
    beats = con.execute(
        "SELECT status, count(*) FROM forward_avg_heartbeat WHERE slate_date >= current_date - 14 GROUP BY 1 ORDER BY 1"
    ).fetchall()
    avg_triples = _one(
        con,
        f"""SELECT count(*) FROM (SELECT 1 FROM forward_avg_shadow a JOIN games g USING (game_id)
            WHERE g.season = ? AND g.home_pts > 0 AND a.made_at <= a.tipoff_utc - {LEAD} * INTERVAL 1 MINUTE
            GROUP BY a.game_id, a.player_id, a.stat)""",
        [season],
    )
    full_triples = 0
    if _one(
        con,
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'forward_full_shadow'",
    ):
        full_triples = _one(
            con,
            f"""SELECT count(*) FROM (SELECT 1 FROM forward_full_shadow f JOIN games g USING (game_id)
                WHERE g.season = ? AND g.home_pts > 0 AND f.game_id LIKE '002%' AND f.made_at <= f.tipoff - {LEAD} * INTERVAL 1 MINUTE
                GROUP BY f.game_id, f.player_id, f.stat)""",
            [season],
        )
    return {
        "rows": rows,
        "stale_rows": _one(con, "SELECT count(*) FROM forward_avg_shadow WHERE stale_model"),
        "rows_after_tip": _one(
            con, "SELECT count(*) FROM forward_avg_shadow WHERE made_at >= tipoff_utc"
        ),
        "settled": _one(con, "SELECT count(*) FROM forward_avg_scores WHERE status = 'scored'"),
        "heartbeats_14d": {str(s): int(n) for s, n in beats},
        "avg_triples": avg_triples,
        "full_triples": full_triples,
        "split": avg_split(con),
        "counting_start": con.execute(
            "SELECT max(value) FROM forward_avg_meta WHERE key = 'counting_start'"
        ).fetchone()[0],  # type: ignore[index]
    }


def section(con: duckdb.DuckDBPyConnection, season: int = 2026) -> list[str]:
    c = counts(con, season)
    if c is None or (not c["rows"] and not c["heartbeats_14d"]):
        return []
    cov = f"{c['avg_triples'] / c['full_triples']:.1%}" if c["full_triples"] else "n/a"
    stale = f"{c['stale_rows'] / c['rows']:.1%}" if c["rows"] else "n/a"
    split_lines = [
        f"- scored rows split at {sp['split_at_utc']:%Y-%m-%d %H:%M} UTC ({sp['id']}): {sp['before']} before "
        f"(blank 2026 possession inputs) / {sp['after']} after (real possessions); not pooled in any readout."
        for sp in c["split"]
    ]
    return [
        "",
        "## AVG shadow: counts and gates only (AVG_SHADOW_LIVE_2; no delta is published on a non-look day)",
        f"- rows {c['rows']} ({c['settled']} settled), stale-model share {stale}, rows stamped at or after tip {c['rows_after_tip']} (must be 0).",
        f"- on-time triples {c['avg_triples']} of {c['full_triples']} settled FULL triples = coverage {cov} (approximate; caps: 95% overall, 90% starter and bench).",
        f"- slate heartbeats, last 14 days: {c['heartbeats_14d'] or 'none'}; counting start: {c['counting_start'] or 'NOT SET (warm-up rows, never scored)'}.",
        *split_lines,
    ]
