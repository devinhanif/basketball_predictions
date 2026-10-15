"""The fact packet: every number a written dissection may use, read off a computed Breakdown.

The packet holds no number that is not already on the leg screen. Differences the writer needs
(whether recent results and the model sit far apart) are computed here, once, so a writer never
does arithmetic of its own: whatever a writer prints must be a packet leaf (see numguard).

``flags.gap_percentage_points`` is a difference between two RATES, in percentage points. It is
not a number of basketball points, and no sentence may call it "points": a writer names both
rates (last10.rate and model.p_hit) instead.
"""

from __future__ import annotations

from typing import Any

from ..breakdown import HEADS_UP_GAP, LAST_N, RECENT_N, SMALL_N, Breakdown, Tally

Packet = dict[str, Any]

MAX_SPLITS = 3
MAX_TEAMMATES = 2


def _tally(t: Tally) -> Packet:
    return {
        "n": t.n,
        "hits": t.hits,
        "misses": t.misses,
        "pushes": t.pushes,
        "rate": t.rate,
        "ci_lo": t.ci_lo,
        "ci_hi": t.ci_hi,
        "avg": t.avg,
        "avg_minutes": t.avg_minutes,
        "small": t.small,
    }


def build_packet(b: Breakdown) -> Packet:
    """Facts for one leg: last 10 and 5 against the line, minutes, splits, teammates out, what the
    opponent allows, the model's chance and its reason, and the flags a writer must respect."""
    m = b.model
    gap: float | None = None
    if m.available and m.p_hit is not None and b.last10.rate is not None and b.last10.n > 0:
        gap = b.last10.rate - m.p_hit
    disagree = gap is not None and abs(gap) >= HEADS_UP_GAP
    splits = [
        {
            "label": s.label,
            "n": s.this_season.n,
            "hits": s.this_season.hits,
            "rate": s.this_season.rate,
            "avg": s.this_season.avg,
            "small": s.this_season.small,
        }
        for s in b.splits
        if s.tonight and s.this_season.n > 0
    ][:MAX_SPLITS]
    teammates = [
        {
            "name": t.name,
            "status": t.status,
            "n_without": t.without.n,
            "hits_without": t.without.hits,
            "avg_without": t.without.avg,
            "n_with": t.with_.n,
            "avg_with": t.with_.avg,
            "small": t.without.small,
        }
        for t in b.teammates.items
    ][:MAX_TEAMMATES]
    opp = b.opponent_allowed
    return {
        "player": b.player,
        "team": b.team,
        "opponent": b.opponent,
        "home": b.home,
        "stat": b.stat,
        "stat_label": b.label,
        "side": b.side,
        "line": b.line,
        # "posted" is a sportsbook line; anything else is a reference point we picked.
        "line_kind": "posted" if b.line_source == "posted" else "reference",
        "windows": {"long": LAST_N, "short": RECENT_N, "small_below": SMALL_N},
        "last10": _tally(b.last10),
        "last5": _tally(b.last5),
        "minutes": {
            "n": b.role.n,
            "avg": b.role.avg_minutes,
            "starts": b.role.starts,
            "projected": b.role.minutes_forecast,
            "p_play": b.role.p_play,
            "p_play_source": b.role.p_play_label,
        },
        "splits": splits,
        "teammates_out": teammates,
        "opponent_allowed": None
        if opp is None
        else {
            "opp": opp.opp,
            "position_noun": opp.position_noun,
            "avg_allowed": opp.avg_allowed,
            "league_avg": opp.league_avg,
            "rank": opp.rank,
            "of": opp.of,
            "n_games": opp.n_games,
        },
        "model": {
            "available": m.available,
            "p_hit": m.p_hit,
            "p_over": m.p_over,
            "p_under": m.p_under,
            "mean": m.mean,
            "typical": m.typical,
            "extrapolated": m.extrapolated,
            "stamp": m.stamp,
            "source": m.source_label,
            "reason": m.reason or None,
        },
        "flags": {
            "no_history": b.last10.n == 0,
            "small_last10": b.last10.n > 0 and b.last10.small,
            "small_last5": b.last5.n > 0 and b.last5.n < RECENT_N,
            "no_model": not m.available,
            "extrapolated": m.available and m.extrapolated,
            "disagree": disagree,
            "gap_percentage_points": None if gap is None else round(abs(gap) * 100),
            "recent_above_model": gap is not None and gap > 0,
            "player_status": b.teammates.player_status,
        },
    }
