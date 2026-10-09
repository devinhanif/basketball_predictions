"""Measurements on collected lineup snapshots (read-only).

1. ``announcement_lead``: for each (game, team), when did the lineup first read 'Confirmed'
   (our ``fetched_at`` and the feed's own timestamp) relative to tip-off?
2. ``open_decisions``: the red-team open question. Players listed questionable/doubtful on
   the latest official report usable at T-60, and what the latest snapshot strictly before
   T-30 says about them. 'open' = the team's lineup is still 'Expected' at that snapshot
   (or there is no snapshot), i.e. the game-time decision was NOT yet resolved at T-30.
3. ``starter_skew``: announced five vs the box-score starters of the same game (the
   train/serve skew of the T-30 model, which is fit on box-score starters).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta

import duckdb
import numpy as np

from nba.daily.injury import to_report_dt
from nba.lineups.source import CONFIRMED

LEAD = timedelta(minutes=30)
T60 = timedelta(minutes=60)
MAX_REPORT_AGE = timedelta(hours=36)
GTD_STATUSES = ("questionable", "doubtful")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


@dataclass
class LeadStats:
    n_team_games: int = 0
    n_confirmed_before_tip: int = 0
    n_confirmed_before_t30: int = 0
    lead_minutes: list[float] = field(default_factory=list)  # first Confirmed fetch -> tip
    source_lead_minutes: list[float] = field(default_factory=list)  # feed ts -> tip


def announcement_lead(lcon: duckdb.DuckDBPyConnection) -> LeadStats:
    rows = lcon.execute(
        """
        WITH f AS (SELECT game_id, team_id, min(fetched_at) AS first_fetch
                   FROM lineup_snapshots WHERE lineup_status = 'Confirmed'
                   GROUP BY game_id, team_id)
        SELECT s.game_id, s.team_id, f.first_fetch, max(s.source_ts) AS src_ts, t.tipoff
        FROM lineup_snapshots s
        JOIN f ON f.game_id = s.game_id AND f.team_id = s.team_id
                  AND s.fetched_at = f.first_fetch
        JOIN game_tips t ON t.game_id = s.game_id
        WHERE s.lineup_status = 'Confirmed'
        GROUP BY s.game_id, s.team_id, f.first_fetch, t.tipoff
        """
    ).fetchall()
    total = lcon.execute(
        "SELECT count(*) FROM (SELECT DISTINCT s.game_id, s.team_id FROM lineup_snapshots s "
        "JOIN game_tips t USING (game_id))"
    ).fetchone()
    out = LeadStats(n_team_games=int(total[0]) if total else 0)
    for _g, _t, first_fetch, src_ts, tip in rows:
        lead = (tip - first_fetch).total_seconds() / 60.0
        if lead > 0:
            out.n_confirmed_before_tip += 1
            out.lead_minutes.append(lead)
            if first_fetch < tip - LEAD:
                out.n_confirmed_before_t30 += 1
        if src_ts is not None:
            out.source_lead_minutes.append((tip - src_ts).total_seconds() / 60.0)
    return out


@dataclass
class OpenDecisions:
    n_games: int = 0
    n_candidates: int = 0  # (game, player) questionable/doubtful at T-60
    by_state: dict[str, int] = field(default_factory=dict)
    n_games_with_open: int = 0
    n_games_with_candidates: int = 0
    n_games_no_report: int = 0
    #: per game with a candidate: (tip-off date ISO, n candidates, n open) -- for the
    #: date-clustered CI of the player-level open share (docs/FORWARD_PREREG_2026_27.md 2.1)
    per_game: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def n_open(self) -> int:
        return self.by_state.get("open_lineup_unconfirmed", 0) + self.by_state.get(
            "no_snapshot_before_t30", 0
        )


def open_decisions(
    lcon: duckdb.DuckDBPyConnection, con: duckdb.DuckDBPyConnection
) -> OpenDecisions:
    out = OpenDecisions()
    tips = lcon.execute(
        "SELECT t.game_id, t.tipoff FROM game_tips t WHERE EXISTS "
        "(SELECT 1 FROM lineup_snapshots s WHERE s.game_id = t.game_id) ORDER BY 2"
    ).fetchall()
    for gid, tip in tips:
        out.n_games += 1
        cutoff60 = to_report_dt(tip - T60)
        try:
            latest = con.execute(
                "SELECT max(as_of) FROM player_availability "
                "WHERE source = 'nba_official_report' AND as_of <= ?",
                [cutoff60],
            ).fetchone()
        except duckdb.CatalogException:
            latest = None
        if latest is None or latest[0] is None or latest[0] < cutoff60 - MAX_REPORT_AGE:
            out.n_games_no_report += 1
            continue
        gtd = {
            int(r[0]): str(r[1])
            for r in con.execute(
                "SELECT player_id, status FROM player_availability "
                "WHERE source = 'nba_official_report' AND as_of = ? AND player_id IS NOT NULL "
                "AND lower(status) IN ('questionable', 'doubtful')",
                [latest[0]],
            ).fetchall()
        }
        in_game = {
            int(r[0]): int(r[1])
            for r in lcon.execute(
                "SELECT DISTINCT player_id, team_id FROM lineup_snapshots WHERE game_id = ?", [gid]
            ).fetchall()
        }
        cands = {p: s for p, s in gtd.items() if p in in_game}
        if not cands:
            continue
        out.n_games_with_candidates += 1
        snap = lcon.execute(
            "SELECT snapshot_id FROM lineup_snapshots WHERE game_id = ? AND fetched_at < ? "
            "GROUP BY snapshot_id, fetched_at ORDER BY fetched_at DESC LIMIT 1",
            [gid, tip - LEAD],
        ).fetchone()
        rows = (
            lcon.execute(
                "SELECT player_id, team_id, lineup_status, roster_status FROM lineup_snapshots "
                "WHERE snapshot_id = ? AND game_id = ?",
                [snap[0], gid],
            ).fetchall()
            if snap
            else []
        )
        status_by_team: dict[int, set[str]] = {}
        roster = {}
        for pid, tid, ls, rs in rows:
            status_by_team.setdefault(int(tid), set()).add(str(ls))
            roster[int(pid)] = str(rs).lower()
        any_open = False
        n_open_game = 0
        for pid in cands:
            if not rows:
                state = "no_snapshot_before_t30"
            elif status_by_team.get(in_game[pid]) != {CONFIRMED}:
                state = "open_lineup_unconfirmed"
            elif pid not in roster:
                state = "resolved_absent_from_confirmed_list"
            else:
                state = "resolved_" + roster[pid]
            out.by_state[state] = out.by_state.get(state, 0) + 1
            out.n_candidates += 1
            is_open = state in ("open_lineup_unconfirmed", "no_snapshot_before_t30")
            any_open |= is_open
            n_open_game += int(is_open)
        out.n_games_with_open += int(any_open)
        out.per_game.append((tip.date().isoformat(), len(cands), n_open_game))
    return out


@dataclass
class StarterSkew:
    n_games: int = 0
    n_announced: int = 0
    n_box_starters_match: int = 0
    n_announced_not_played: int = 0
    n_box_starters_not_announced: int = 0


def starter_skew(con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection) -> StarterSkew:
    """Announced five (the snapshot each logged T-30 decision used) vs box-score starters."""
    out = StarterSkew()
    try:
        dec = con.execute(
            "SELECT game_id, snapshot_id FROM forward_t30_decisions WHERE outcome = 'logged'"
        ).fetchall()
    except duckdb.CatalogException:
        return out
    for gid, sid in dec:
        box = con.execute(
            "SELECT player_id, coalesce(starter, false), coalesce(minutes, 0) > 0 "
            "FROM player_game_stats WHERE game_id = ?",
            [gid],
        ).fetchall()
        if not box:
            continue
        ann = {
            int(r[0])
            for r in lcon.execute(
                "SELECT player_id FROM lineup_snapshots WHERE snapshot_id = ? AND game_id = ? "
                "AND announced_starter",
                [sid, gid],
            ).fetchall()
        }
        box_st = {int(p) for p, st, _ in box if st}
        played = {int(p) for p, _, pl_ in box if pl_}
        out.n_games += 1
        out.n_announced += len(ann)
        out.n_box_starters_match += len(ann & box_st)
        out.n_announced_not_played += len(ann - played)
        out.n_box_starters_not_announced += len(box_st - ann)
    return out


def _q(v: list[float], qs: tuple[float, ...] = (0.1, 0.5, 0.9)) -> str:
    if not v:
        return "n/a"
    return "/".join(f"{x:.0f}" for x in np.quantile(np.asarray(v), qs))


def render_collector_section(
    con: duckdb.DuckDBPyConnection, lcon: duckdb.DuckDBPyConnection
) -> list[str]:
    """Markdown lines: announcement timing, open game-time decisions at T-30, starter skew."""
    lead = announcement_lead(lcon)
    od = open_decisions(lcon, con)
    sk = starter_skew(con, lcon)
    lines = ["", "### T-30 collector measurements (sample sizes shown; small n = no claim)"]
    lines.append(
        f"- team-games with snapshots: {lead.n_team_games}; confirmed before tip: "
        f"{lead.n_confirmed_before_tip}; confirmed before T-30: {lead.n_confirmed_before_t30}"
    )
    lines.append(
        f"- minutes before tip when first seen Confirmed (our fetch; p10/p50/p90, "
        f"n={len(lead.lead_minutes)}): {_q(lead.lead_minutes)}; by the feed's own timestamp "
        f"(n={len(lead.source_lead_minutes)}): {_q(lead.source_lead_minutes)}"
    )
    lines.append(
        f"- OPEN GAME-TIME DECISIONS at the latest pre-T-30 snapshot: {od.n_open} of "
        f"{od.n_candidates} (game, player) questionable/doubtful on the T-60 report"
        + (
            f" ({od.n_open / od.n_candidates:.1%}, Wilson 95% "
            f"[{wilson(od.n_open, od.n_candidates)[0]:.1%}, "
            f"{wilson(od.n_open, od.n_candidates)[1]:.1%}])"
            if od.n_candidates
            else ""
        )
    )
    lines.append(
        f"  - games with any open: {od.n_games_with_open} of {od.n_games_with_candidates} games "
        f"having a candidate ({od.n_games} games with snapshots; {od.n_games_no_report} without "
        f"a usable T-60 report); states: {dict(sorted(od.by_state.items()))}"
    )
    lines.append(
        f"- announced five vs box-score starters on {sk.n_games} logged games: "
        f"{sk.n_box_starters_match}/{sk.n_announced} announced starters are box starters; "
        f"announced but did not play: {sk.n_announced_not_played}; box starters not announced: "
        f"{sk.n_box_starters_not_announced}"
    )
    return lines
