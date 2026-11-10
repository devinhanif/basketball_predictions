"""Local, no-network ``{player_id: name}`` fallback for ids the static nba_api list misses.

The bundled nba_api list predates each season's rookies and call-ups, so the export used to
write ``Player <id>``. Every source below already sits on disk, carries the NBA ``player_id``
next to the name, and comes from stats.nba.com / cdn.nba.com. Nothing is inferred or title-cased;
an id no source holds stays unnamed (the caller keeps the ``Player <id>`` placeholder).

Priority (first source holding the id wins; later disagreements are logged, not applied):

1. ``data/rosters/<as_of>/<team>.parquet`` ``player_name`` (CommonTeamRoster; newest snapshot
   wins; current spelling, keeps diacritics and suffixes).
2. ``data/lineups/lineups.duckdb`` ``lineup_snapshots_dedup.player_name`` (official lineups;
   newest fetch wins).
3. ``data/players_static/commonallplayers_2025-26.parquet`` ``display_first_last`` (official
   full list; ASCII-folded spelling, so last).

Skipped on purpose: ``configs/odds_player_aliases.yaml`` / ``kalshi_aliases.yaml`` hold only
normalised lower-case keys (no display form) and ESPN summaries carry ESPN ids, not NBA ids.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

log = logging.getLogger("nba.explain.local_names")

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass
class LocalNames:
    names: dict[int, str] = field(default_factory=dict)
    source: dict[int, str] = field(default_factory=dict)
    conflicts: list[tuple[int, str, str, str, str]] = field(default_factory=list)


def _roster(data_dir: Path) -> dict[int, str]:
    files = sorted(data_dir.glob("rosters/*/*.parquet"))  # date dirs sort oldest -> newest
    if not files:
        return {}
    out: dict[int, str] = {}
    con = duckdb.connect()
    try:
        for f in files:
            try:
                rows = con.execute(
                    "select try_cast(player_id as bigint), player_name from read_parquet(?)"
                    " where try_cast(player_id as bigint) is not null"
                    " and player_name is not null",
                    [str(f)],
                ).fetchall()
            except duckdb.Error:
                continue  # older snapshots may lack player_name
            for pid, name in rows:
                if str(name).strip():
                    out[int(pid)] = str(name).strip()
    finally:
        con.close()
    return out


def _lineups(data_dir: Path) -> dict[int, str]:
    path = data_dir / "lineups" / "lineups.duckdb"
    if not path.exists():
        return {}
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:  # locked by the writer: skip, never wait
        return {}
    try:
        rows = con.execute(
            "select try_cast(player_id as bigint), arg_max(player_name, fetched_at)"
            " from lineup_snapshots_dedup"
            " where try_cast(player_id as bigint) is not null and player_name is not null"
            " group by 1"
        ).fetchall()
    except duckdb.Error:
        return {}
    finally:
        con.close()
    return {int(p): str(n).strip() for p, n in rows if str(n).strip()}


def _commonall(data_dir: Path) -> dict[int, str]:
    f = data_dir / "players_static" / "commonallplayers_2025-26.parquet"
    if not f.exists():
        return {}
    con = duckdb.connect()
    try:
        rows = con.execute(
            "select try_cast(person_id as bigint), display_first_last from read_parquet(?)"
            " where try_cast(person_id as bigint) is not null"
            " and display_first_last is not null",
            [str(f)],
        ).fetchall()
    except duckdb.Error:
        return {}
    finally:
        con.close()
    return {int(p): str(n).strip() for p, n in rows if str(n).strip()}


def load_local_names(data_dir: Path | None = None) -> LocalNames:
    """Merge the local sources in priority order; log every disagreement."""
    root = data_dir or DEFAULT_DATA_DIR
    out = LocalNames()
    for label, loader in (
        ("rosters", _roster),
        ("lineups", _lineups),
        ("commonallplayers", _commonall),
    ):
        for pid, name in loader(root).items():
            if pid not in out.names:
                out.names[pid] = name
                out.source[pid] = label
            elif out.names[pid] != name:
                out.conflicts.append((pid, out.names[pid], out.source[pid], name, label))
    if out.conflicts:
        log.warning(
            "player-name conflicts (kept higher-priority source): %s",
            [(p, kept, ks, other, os_) for p, kept, ks, other, os_ in out.conflicts],
        )
    return out
