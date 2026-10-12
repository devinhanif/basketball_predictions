"""Late scratches after the last pre-tip information: a descriptive nightly statistic.

A late scratch is a (game, player) the forward log treated as available (logged in
``forward_pplay_shadow`` or in the primary prop rows before tip, and NOT 'out' on the latest
official report at the cutoff) who did not play (no box row, or minutes <= 0). The cutoff is T-30
when a lineup snapshot older than T-30 exists for the game, else T-60. Each player is split, at
T-30 and at T-15 separately, by the latest snapshot strictly before that instant (the frozen
``lineup_snapshots_dedup`` rule):

* ``starter``      the team's lineup is Confirmed and the player is in the announced five;
* ``bench``        the team's lineup is Confirmed and the player is not in the five (or not listed);
* ``unconfirmed``  no snapshot, or the team's lineup was still Expected.

Games with no usable official report (none within 36 h before the cutoff) are excluded and
counted. Read-only on both databases; no model, no scored arm, nothing here feeds a prediction.
Rolling 7 and 28 day windows end at the latest completed game date; the share carries a Wilson
interval (descriptive: players within a game are not independent, so it understates the
uncertainty).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.daily.injury import MAX_REPORT_AGE_HOURS, to_report_dt
from nba.lineups.analysis import wilson
from nba.lineups.store import DEDUP_SELECT

GROUPS = ("starter", "bench", "unconfirmed")
CUTOFFS = (30, 15)
WINDOWS = (7, 28)
PRIMARY_TARGETS = ("pts", "reb", "ast", "fg3m")


def _out_at(con: duckdb.DuckDBPyConnection, tip: datetime, minutes: int) -> set[int] | None:
    """Players 'out' on the latest official report at ``tip - minutes``; None if none usable."""
    cutoff = to_report_dt(tip - timedelta(minutes=minutes))
    try:
        row = con.execute(
            "SELECT max(as_of) FROM player_availability "
            "WHERE source = 'nba_official_report' AND as_of <= ?",
            [cutoff],
        ).fetchone()
        latest = row[0] if row else None
        if latest is None or latest < cutoff - timedelta(hours=MAX_REPORT_AGE_HOURS):
            return None
        rows = con.execute(
            "SELECT DISTINCT player_id FROM player_availability "
            "WHERE source = 'nba_official_report' AND as_of = ? AND lower(status) = 'out' "
            "AND player_id IS NOT NULL",
            [latest],
        ).fetchall()
    except duckdb.CatalogException:
        return None
    return {int(r[0]) for r in rows}


def _snapshot_before(
    lcon: duckdb.DuckDBPyConnection, game_id: str, tip: datetime, minutes: int
) -> str | None:
    row = lcon.execute(
        "SELECT snapshot_id FROM lineup_snapshots WHERE game_id = ? AND fetched_at < ? "
        "GROUP BY snapshot_id, fetched_at ORDER BY fetched_at DESC LIMIT 1",
        [game_id, tip - timedelta(minutes=minutes)],
    ).fetchone()
    return str(row[0]) if row else None


def _dedup_rows(
    lcon: duckdb.DuckDBPyConnection, snapshot_id: str, game_id: str
) -> list[tuple[Any, ...]]:
    sql = "SELECT player_id, team_id, lineup_status, announced_starter FROM {src} WHERE "
    sql += "snapshot_id = ? AND game_id = ?"
    try:
        return lcon.execute(
            sql.format(src="lineup_snapshots_dedup"), [snapshot_id, game_id]
        ).fetchall()
    except (
        duckdb.CatalogException
    ):  # older lineups file without the view: same frozen select, inline
        return lcon.execute(sql.format(src=f"({DEDUP_SELECT})"), [snapshot_id, game_id]).fetchall()


def _groups(
    lcon: duckdb.DuckDBPyConnection,
    game_id: str,
    tip: datetime,
    minutes: int,
    players: dict[int, int | None],
) -> dict[int, str]:
    sid = _snapshot_before(lcon, game_id, tip, minutes)
    if sid is None:
        return {p: "unconfirmed" for p in players}
    rows = _dedup_rows(lcon, sid, game_id)
    confirmed_teams = {int(r[1]) for r in rows if r[2] == "Confirmed"}
    row_by_player = {int(r[0]): r for r in rows}
    out: dict[int, str] = {}
    for pid, team in players.items():
        r = row_by_player.get(pid)
        team_id = team if team is not None else (int(r[1]) if r else None)
        if team_id is None or team_id not in confirmed_teams:
            out[pid] = "unconfirmed"
        elif r is not None and r[2] == "Confirmed" and bool(r[3]):
            out[pid] = "starter"
        else:
            out[pid] = "bench"
    return out


def late_scratch_rows(
    con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection, season: int
) -> tuple[list[dict[str, Any]], int]:
    """One row per candidate (game, player) of every completed ``season`` game, and the number of
    completed games skipped for lack of a usable official report."""
    try:
        games = con.execute(
            """
            SELECT g.game_id, g.game_date, t.tipoff
            FROM games g
            JOIN (SELECT game_id, max(tipoff) AS tipoff FROM forward_predictions GROUP BY 1) t
              USING (game_id)
            WHERE g.season = ? AND g.home_pts > 0 AND g.away_pts > 0
              AND EXISTS (SELECT 1 FROM player_game_stats b WHERE b.game_id = g.game_id)
            ORDER BY g.game_date, g.game_id
            """,
            [season],
        ).fetchall()
    except duckdb.CatalogException:
        return [], 0
    try:
        has_pplay = bool(
            con.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'forward_pplay_shadow'"
            ).fetchone()[0]  # type: ignore[index]
        )
    except duckdb.CatalogException:
        has_pplay = False
    rows: list[dict[str, Any]] = []
    n_no_report = 0
    for gid, gdate, tip in games:
        pool: dict[int, int | None] = {}
        if has_pplay:
            for pid, tid in con.execute(
                "SELECT player_id, max(team_id) FROM forward_pplay_shadow "
                "WHERE game_id = ? AND made_at < tipoff GROUP BY 1",
                [gid],
            ).fetchall():
                pool[int(pid)] = int(tid) if tid is not None else None
        for (pid,) in con.execute(
            "SELECT DISTINCT player_id FROM forward_predictions "
            "WHERE game_id = ? AND made_at < tipoff AND player_id > 0 "
            "AND target IN (SELECT unnest(?))",
            [gid, list(PRIMARY_TARGETS)],
        ).fetchall():
            pool.setdefault(int(pid), None)
        if not pool:
            continue
        has_snap = _snapshot_before(lcon, gid, tip, 30) is not None
        cut = 30 if has_snap else 60
        out = _out_at(con, tip, cut)
        if out is None:
            n_no_report += 1
            continue
        pool = {p: t for p, t in pool.items() if p not in out}
        played = {
            int(r[0])
            for r in con.execute(
                "SELECT player_id FROM player_game_stats WHERE game_id = ? AND minutes > 0", [gid]
            ).fetchall()
        }
        grp = {m: _groups(lcon, gid, tip, m, pool) for m in CUTOFFS}
        for pid in pool:
            rows.append(
                {
                    "game_id": gid,
                    "game_date": gdate,
                    "player_id": pid,
                    "cutoff_min": cut,
                    "dnp": pid not in played,
                    "grp30": grp[30][pid],
                    "grp15": grp[15][pid],
                }
            )
    return rows, n_no_report


def _cell(sub: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(sub)
    k = sum(1 for r in sub if r["dnp"])
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "share": (k / n) if n else None, "wilson": [lo, hi]}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-date counts and rolling windows ending at the latest game date."""
    if not rows:
        return {"end": None, "per_date": {}, "windows": {}}
    end: date = max(r["game_date"] for r in rows)
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_date[r["game_date"]].append(r)
    per_date: dict[str, Any] = {}
    for d, v in sorted(by_date.items()):
        starters = [r for r in v if r["grp30"] == "starter"]
        per_date[d.isoformat()] = _cell(v) | {"starter_t30": _cell(starters)}
    windows: dict[str, Any] = {}
    for w in WINDOWS:
        sub = [r for r in rows if end - timedelta(days=w - 1) <= r["game_date"] <= end]
        cells: dict[str, Any] = {"all": _cell(sub), "n_dates": len({r["game_date"] for r in sub})}
        for m in CUTOFFS:
            for g in GROUPS:
                cells[f"{g}_t{m}"] = _cell([r for r in sub if r[f"grp{m}"] == g])
        windows[f"{w}d"] = cells
    return {"end": end.isoformat(), "per_date": per_date, "windows": windows}


def _pct(c: dict[str, Any]) -> str:
    if not c["n"]:
        return "n=0"
    lo, hi = c["wilson"]
    return f"{c['k']}/{c['n']} = {100 * c['share']:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def render(summary: dict[str, Any], n_no_report: int = 0) -> list[str]:
    lines = [
        "",
        "## Late scratches after the cutoff (descriptive; no model, no scored arm)",
        "- Candidates: logged, not OUT on the latest official report at T-30 (T-60 when no lineup "
        "snapshot existed before T-30); scratch = did not play. Wilson 95% intervals ignore the "
        "within-game clustering.",
    ]
    if summary["end"] is None:
        return lines + ["- no completed games with a usable report yet."]
    for d, c in list(summary["per_date"].items())[-7:]:
        st = c["starter_t30"]
        lines.append(f"- {d}: {_pct(c)}; confirmed starters at T-30 {st['k']}/{st['n']}")
    for name, cells in summary["windows"].items():
        lines.append(
            f"- rolling {name} to {summary['end']} ({cells['n_dates']} dates): {_pct(cells['all'])}"
        )
        for m in CUTOFFS:
            parts = [f"{g} {_pct(cells[f'{g}_t{m}'])}" for g in GROUPS]
            lines.append(f"  - split at T-{m}: " + "; ".join(parts))
    if n_no_report:
        lines.append(
            f"- {n_no_report} completed games excluded: no usable official report at the cutoff."
        )
    return lines


def late_scratch_section(
    con: duckdb.DuckDBPyConnection, lineups_db: Path | None = None, season: int = 2026
) -> list[str]:
    """Report section; never raises (the briefing must not fail on an auxiliary section)."""
    from nba.lineups.store import DEFAULT_LINEUPS_DB, connect_lineups

    try:
        lcon = connect_lineups(lineups_db or DEFAULT_LINEUPS_DB, read_only=True, retries=2)
    except Exception as exc:
        return ["", f"## Late scratches: lineup database unavailable ({exc!r})"]
    try:
        rows, n_no = late_scratch_rows(con, lcon, season)
    except Exception as exc:
        return ["", f"## Late scratches: unavailable ({exc!r})"]
    finally:
        lcon.close()
    if not rows and not n_no:
        return []
    return render(summarize(rows), n_no)
