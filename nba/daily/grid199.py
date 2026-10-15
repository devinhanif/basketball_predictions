# ruff: noqa: E501
"""Production's continuous 199-point forecast grid, persisted per forecast row.

``forward_prop_grid199`` holds, for every context-residual primary row the run wrote, the equal-mass
199 quantile values (``nba.props.forward._ctx_row``'s ``q199``) that ``p_ge`` / ``p_ge_full`` are derived
from. ``forward_predictions`` keeps only the integer survival and 19 quantiles, which cannot give the
CAL-rung map's median and spread exactly (docs/contracts/rung_cal_export_v1.md, amended 2026-10-15).

Own table, so ``forward_predictions`` and the canonical dump (replay oracle) cannot see it. Written after
every primary row, in its own try/except: ``persist`` returns a status string, never raises, and a failure
cannot change a prediction row or the run's exit code (the run adds a shadow-error alert). Write-on-change
per (game, player, stat) on the grid's sha256; a grid whose game has tipped (``made_at >= tipoff``) is
refused. Timestamps are naive UTC.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import duckdb
import numpy as np

TABLE = "forward_prop_grid199"
N_GRID = 199
MODEL = "props_context_residual"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    run_id VARCHAR,
    made_at TIMESTAMP,          -- known-at: the run that fit this grid
    game_id VARCHAR,
    tipoff TIMESTAMP,
    player_id INT,
    stat VARCHAR,
    model VARCHAR,              -- props_context_residual
    q199 DOUBLE[],              -- 199 equal-mass quantile values, conditional on playing, ascending
    q199_sha VARCHAR            -- sha256 of the float64 little-endian bytes
)
"""
COLUMNS = [
    "run_id",
    "made_at",
    "game_id",
    "tipoff",
    "player_id",
    "stat",
    "model",
    "q199",
    "q199_sha",
]


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def grid_sha(q199: Any) -> str:
    return hashlib.sha256(np.asarray(q199, dtype="<f8").tobytes()).hexdigest()


def persist(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    grids: Mapping[tuple[str, int, str], Any],
    tipoffs: Mapping[str, datetime],
) -> str:
    """Write the run's grids; returns ``"ok: N written, M unchanged, K refused"`` or ``"failed: ..."``.
    ``grids`` maps (game_id, player_id, stat) -> the 199 values; ``tipoffs`` maps game_id -> real tip."""
    try:
        if not grids:
            return "skipped: no grids"
        ensure_table(con)
        rows: list[tuple[Any, ...]] = []
        refused = 0
        for (gid, pid, stat), q in sorted(grids.items()):
            tip = tipoffs.get(gid)
            arr = np.asarray(q, dtype=float)
            if (
                tip is None
                or made_at >= tip
                or arr.shape != (N_GRID,)
                or not np.isfinite(arr).all()
            ):
                refused += 1
                continue
            rows.append(
                (run_id, made_at, gid, tip, int(pid), stat, MODEL, arr.tolist(), grid_sha(arr))
            )
        games = sorted({r[2] for r in rows})
        latest: dict[tuple[str, int, str], str] = {}
        if games:
            marks = ",".join("?" for _ in games)
            for g, p, s, sha in con.execute(
                f"""SELECT game_id, player_id, stat, q199_sha FROM (
                      SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat
                                                   ORDER BY made_at DESC, run_id DESC) rn
                      FROM {TABLE} WHERE game_id IN ({marks})) WHERE rn = 1""",  # noqa: S608
                games,
            ).fetchall():
                latest[(str(g), int(p), str(s))] = str(sha)
        new = [r for r in rows if latest.get((r[2], r[4], r[5])) != r[8]]
        if new:
            con.executemany(
                f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",  # noqa: S608
                new,
            )
        return f"ok: {len(new)} written, {len(rows) - len(new)} unchanged, {refused} refused"
    except Exception as exc:
        return f"failed: {type(exc).__name__}: {exc}"[:200]
