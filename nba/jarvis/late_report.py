"""Display-only "latest report" line on a posted prop play (DECISIONS 2026-11-02 19:44).

When the LATE_REPORT shadow (``props_context_residual_late``, written at T-15 with the official
report as of that moment) differs from production (``props_context_residual``) by at least
``MIN_DIFF`` points of predictive mean for the play's player and stat, the play text gets one line,
e.g. ``latest report: Tatum 27.1 vs 25.3``; when the shadow's own report excluded the player (OUT or
DOUBTFUL at T-15), ``latest report: George OUT (production 16.7)``.

It is a line of text and nothing else: it never changes a label, an edge, a stake, a price, the
dedupe key or the input hash (``Play.display_lines`` is not part of ``Play.key`` or
``Play.inputs``). ``nba.duckdb`` is opened read-only; any failure leaves the plays unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import duckdb

from nba.jarvis.models import Play
from nba.jarvis.sources import open_ro

LATE_MODEL = "props_context_residual_late"
PROD_MODEL = "props_context_residual"
LATE_DECISIONS = "forward_late_decisions"
#: points of predictive mean (conditional on playing) at which the line is shown
MIN_DIFF = 1.0

Key = tuple[str, int, str]  # game_id, player_id, stat


def _has(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    r = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(r and r[0])


def read_late(
    con: duckdb.DuckDBPyConnection, game_ids: list[str], now: datetime
) -> tuple[dict[Key, tuple[float | None, float | None]], dict[str, set[int]]]:
    """``((game, player, stat) -> (production mean, late mean))`` from the latest row of each
    model made at or before ``now`` (naive UTC), and ``game -> players the late arm's report
    excluded`` for games it logged."""
    if not game_ids or not _has(con, "forward_predictions"):
        return {}, {}
    marks = ",".join("?" for _ in game_ids)
    rows = con.execute(
        f"""SELECT game_id, player_id, target, model_name,
                   CAST(json_extract(prediction, '$.mean') AS DOUBLE)
            FROM (SELECT *, row_number() OVER (
                      PARTITION BY game_id, model_name, target, player_id
                      ORDER BY made_at DESC, run_id DESC) rn
                  FROM forward_predictions
                  WHERE game_id IN ({marks}) AND model_name IN (?, ?) AND player_id > 0
                    AND made_at <= ? AND made_at < tipoff)
            WHERE rn = 1""",  # noqa: S608 - placeholders only
        [*game_ids, PROD_MODEL, LATE_MODEL, now],
    ).fetchall()
    means: dict[Key, list[float | None]] = {}
    for gid, pid, stat, model, mean in rows:
        slot = means.setdefault((str(gid), int(pid), str(stat)), [None, None])
        slot[0 if model == PROD_MODEL else 1] = None if mean is None else float(mean)
    outs: dict[str, set[int]] = {}
    if _has(con, LATE_DECISIONS):
        for gid, excl in con.execute(
            f"SELECT game_id, report_excluded FROM {LATE_DECISIONS} "  # noqa: S608
            f"WHERE outcome = 'logged' AND decided_at <= ? AND game_id IN ({marks})",
            [now, *game_ids],
        ).fetchall():
            outs[str(gid)] = {int(p) for p in json.loads(excl or "[]")}
    return {k: (v[0], v[1]) for k, v in means.items()}, outs


def late_line(
    name: str, prod: float | None, late: float | None, excluded: bool, min_diff: float = MIN_DIFF
) -> str | None:
    """The display line, or None when there is nothing worth saying."""
    if excluded:
        tail = "" if prod is None else f" (production {prod:.1f})"
        return f"latest report: {name} OUT{tail}"
    if prod is None or late is None or abs(late - prod) < min_diff:
        return None
    return f"latest report: {name} {late:.1f} vs {prod:.1f}"


def annotate(
    plays: list[Play],
    db_path: Path,
    name_of: Callable[[int], str],
    now: datetime,
    *,
    connect: Callable[[Path], duckdb.DuckDBPyConnection] | None = None,
) -> list[Play]:
    """``plays`` with a ``display_lines`` entry on each prop play the late shadow moved. Never
    raises: a missing store, a locked file or a bad row returns the plays as they were."""
    keyed = [
        (i, (p.game_id, int(leg.player_id), str(leg.stat)))
        for i, p in enumerate(plays)
        for leg in p.legs[:1]
        if leg.kind == "prop" and leg.player_id and leg.stat
    ]
    if not keyed or not db_path.exists():
        return plays
    try:
        # one attempt: a store held by its writer just means no line this tick (no waiting)
        con = connect(db_path) if connect is not None else open_ro(db_path, retries=1)
        try:
            means, outs = read_late(con, sorted({k[0] for _, k in keyed}), now)
        finally:
            con.close()
        out = list(plays)
        for i, (gid, pid, stat) in keyed:
            prod, late = means.get((gid, pid, stat), (None, None))
            line = late_line(name_of(pid), prod, late, pid in outs.get(gid, set()))
            if line is not None:
                out[i] = replace(out[i], display_lines=(*out[i].display_lines, line))
        return out
    except Exception:
        return plays
