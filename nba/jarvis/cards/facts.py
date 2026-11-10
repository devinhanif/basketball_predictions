"""Gather the numbers a card shows from values other JARVIS modules already computed.

Nothing here computes a price, an edge, a fee, a label or a decision. ``single_candidate`` (the
same function the slate uses) prices an over; the halftime scorer's rows carry the live edge; the
enter / skip verdict applies the scorer's OWN display rule (``halftime_overs.display_ok`` and
``min_edge``) unchanged. This module only selects, orders and re-labels.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from nba.jarvis import halftime_overs as HO
from nba.jarvis.book import sha
from nba.jarvis.cards.model import HalfLeg, Knows, OverRow
from nba.jarvis.config import JarvisConfig
from nba.jarvis.explain import key_players
from nba.jarvis.ingame import PlayerLine, norm_name
from nba.jarvis.models import Game, Play, Quote
from nba.jarvis.slate import SlateState, single_candidate

log = logging.getLogger("nba.jarvis.cards.facts")

STAT_KEY = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "fg3m"}


def player_name(q_label: str, fallback: str) -> str:
    """The venue's own title is 'Name: 25+ points'; the name is the part before the colon."""
    head = q_label.split(":", 1)[0].strip()
    return head if head else fallback


def pick_id(slate: date, game_id: str, ticker: str) -> str:
    return sha([str(slate), game_id, ticker])


def over_from_play(
    p: Play, slate: date, shown_at: str, display_name: str, now: datetime | None = None
) -> OverRow | None:
    leg = p.legs[0]
    if leg.kind != "prop" or leg.side != "yes" or leg.stat is None or leg.threshold is None:
        return None
    if p.edge_cents != p.edge_cents:  # NaN: no sharp line, no measured edge
        return None
    return OverRow(
        pick_id=pick_id(slate, p.game_id, leg.ticker),
        player=display_name,
        stat=leg.stat,
        line=float(leg.threshold),
        ask=float(leg.price),
        fee_c=float(p.fee_cents),
        fair=p.fair_prob,
        pin=p.pin_prob,
        model=p.model_prob,
        edge_c=float(p.edge_cents),
        ref_age_min=p.ref_age_min,
        label=p.label,
        ticker=leg.ticker,
        venue=leg.venue,
        player_id=leg.player_id,
        includes_p_plays=any(n.startswith("fair price includes P(plays)") for n in p.notes),
        shown_at=shown_at,
        quote_age_min=(
            None
            if now is None or leg.quote_ts is None
            else max(0.0, (now - leg.quote_ts).total_seconds() / 60.0)
        ),
    )


#: Notes ``single_candidate`` attaches when it CAPS a play at thin for a weak input (a Pinnacle
#: line older than max_ref_age_min, Pinnacle newer than the price, no P(plays)). A capped over is
#: not shown.
CAP_NOTES = (
    "prop DNP rule unverified and no P(plays)",
    "Pinnacle line is",
    "min newer than this price",
)


def is_capped(p: Play) -> bool:
    return any(n.startswith(CAP_NOTES[:2]) or CAP_NOTES[2] in n for n in p.notes)


def pregame_overs(
    cfg: JarvisConfig,
    st: SlateState,
    game_id: str,
    top_n: int,
    shown_at: str,
    kb: Any = None,
    hold: Callable[[Play], bool] | None = None,
) -> tuple[list[OverRow], int]:
    """Up to ``top_n`` overs with a measured edge above zero after fee, best EV first (one line per
    player and stat), and how many overs were priced (and passed every gate) at all. Yes side of a
    prop only. Same gates as the slate: a play capped at thin for a weak input, or held on a stale
    Pinnacle reference (``hold``, the 11-08 preview hold), is neither shown nor counted."""
    best: dict[tuple[str, str], OverRow] = {}
    priced = 0
    for q in st.tradable(cfg):
        if q.game_id != game_id or q.kind != "prop":
            continue
        p = single_candidate(cfg, st, q, "yes")
        if p is None or is_capped(p) or (hold is not None and hold(p)):
            continue
        row = over_from_play(p, st.slate, shown_at, _name(q, kb), st.now)
        if row is None:
            continue
        priced += 1
        if row.edge_c <= 0.0:
            continue
        key = (row.player, row.stat)
        if key not in best or (row.edge_c, -row.line) > (best[key].edge_c, -best[key].line):
            best[key] = row
    ranked = sorted(best.values(), key=lambda r: (-r.edge_c, r.player))
    return ranked[:top_n], priced


def priced_lines(
    cfg: JarvisConfig,
    st: SlateState,
    game_id: str,
    shown_at: str,
    kb: Any = None,
    hold: Callable[[Play], bool] | None = None,
    unmeasured: set[tuple[int, str]] | None = None,
) -> dict[tuple[int, str], OverRow]:
    """The player-checkpoint view of ``pregame_overs``: for every (player_id, stat) with a priced
    over, the line with the best EV after fees (ties: the lower line), including a negative EV.
    The SAME helpers price it (``single_candidate``, ``is_capped``, the stale-reference ``hold``,
    ``over_from_play``); nothing is computed here, only selected. A price with no sharp reference
    has no measured edge (NaN) and is not returned; ``unmeasured`` (when given) collects the
    (player_id, stat) of every priced over that is NOT returned (no sharp line, capped at thin for
    a weak input, or held on a stale reference), so a card can say "priced, no usable EV"."""
    best: dict[tuple[int, str], OverRow] = {}
    for q in st.tradable(cfg):
        if q.game_id != game_id or q.kind != "prop" or not q.player_id:
            continue
        p = single_candidate(cfg, st, q, "yes")
        if p is None:
            continue
        row = (
            None
            if is_capped(p) or (hold is not None and hold(p))
            else over_from_play(p, st.slate, shown_at, _name(q, kb), st.now)
        )
        if row is None or row.player_id is None:
            if unmeasured is not None and q.stat is not None:
                unmeasured.add((int(q.player_id), q.stat))
            continue
        key = (int(row.player_id), row.stat)
        if key not in best or (row.edge_c, -row.line) > (best[key].edge_c, -best[key].line):
            best[key] = row
    return best


def _name(q: Quote, kb: Any = None) -> str:
    """The resolver's display name (right diacritics) when it knows the player, else the venue's
    own title text before the colon."""
    if kb is not None and q.player_id:
        try:
            n = str(kb.name(int(q.player_id)))
        except Exception:  # noqa: BLE001 - a name lookup never breaks a card
            n = ""
        if n and not n.lower().startswith("player "):
            return n
    return player_name(q.label, f"player {q.player_id}")


def pick_record(row: OverRow, slate: date, game: Game) -> dict[str, Any]:
    """What the paper log keeps the first time a card shows an over (price at prediction time)."""
    return {
        "pick_id": row.pick_id, "slate": str(slate), "game_id": game.game_id,
        "matchup": game.matchup, "tip": None if game.tipoff is None else str(game.tipoff),
        "ticker": row.ticker, "venue": row.venue, "player": row.player,
        "player_id": row.player_id, "stat": row.stat, "line": row.line, "ask": row.ask,
        "fee_c": row.fee_c, "fair": row.fair, "pin": row.pin, "model": row.model,
        "edge_c": row.edge_c, "ref_age_min": row.ref_age_min, "label": row.label,
        "shown_at": row.shown_at, "includes_p_plays": row.includes_p_plays,
    }  # fmt: skip


def overs_from_picks(picks: list[dict[str, Any]]) -> list[OverRow]:
    """The overs a card showed before tip, rebuilt from the log (frozen at the price shown)."""
    out = [
        OverRow(
            pick_id=str(p["pick_id"]),
            player=str(p["player"]),
            stat=str(p["stat"]),
            line=float(p["line"]),
            ask=float(p["ask"]),
            fee_c=float(p["fee_c"]),
            fair=p.get("fair"),
            pin=p.get("pin"),
            model=p.get("model"),
            edge_c=float(p["edge_c"]),
            ref_age_min=p.get("ref_age_min"),
            label=str(p.get("label", "")),
            ticker=str(p["ticker"]),
            venue=str(p["venue"]),
            player_id=p.get("player_id"),
            includes_p_plays=bool(p.get("includes_p_plays")),
            shown_at=str(p.get("shown_at", "")),
        )  # fmt: skip
        for p in picks
    ]
    return sorted(out, key=lambda r: (-r.edge_c, r.player))


# ------------------------------------------------------------------------------ halftime


def box_cur(
    players: tuple[PlayerLine, ...], name: str, stat: str, team_hint: str | None
) -> int | None:
    """The stat so far from a live box score, matched on the exact normalised name (an unmatched
    or ambiguous name returns None; it is never guessed)."""
    want = norm_name(name)
    hits = [p for p in players if norm_name(p.name) == want]
    if team_hint:
        narrowed = [p for p in hits if p.team == team_hint]
        hits = narrowed or hits
    return hits[0].stat(STAT_KEY[stat]) if len(hits) == 1 and stat in STAT_KEY else None


def _scorer_index(
    rows: list[dict[str, Any]], matchup: str
) -> dict[tuple[str, str, int], dict[str, Any]]:
    idx: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in rows:
        if r.get("game") != matchup:
            continue
        idx[(norm_name(str(r["player_name"])), str(r["stat"]), int(r["k"]))] = r
    return idx


def _verdict(r: dict[str, Any], s: dict[str, Any]) -> tuple[str, str]:
    """Enter / skip by the halftime scorer's own rule: edge >= min_edge AND a real, tight book."""
    min_edge = float(s["min_edge"])
    edge_c = 100.0 * float(r["edge_model"])
    bid, ask = r.get("bid_c"), int(r["ask_c"])
    ok_book = HO.display_ok(r, int(s["display_min_bid_c"]), int(s["display_max_spread_c"]))
    ok_edge = float(r["edge_model"]) >= min_edge - 1e-12
    if ok_edge and ok_book:
        return "enter", (
            f"model {round(100 * float(r['p_model']))}% vs ask {ask}c plus a "
            f"{int(r['fee_c'])}c fee: {edge_c:+.0f}c after the fee, "
            f"over the {100 * min_edge:.0f}c bar"
        )
    if not ok_edge:
        return (
            "skip",
            f"the edge is {edge_c:+.0f}c after the fee, under the {100 * min_edge:.0f}c bar",
        )
    if bid is None or bid < int(s["display_min_bid_c"]):
        return "skip", "nobody is bidding, so the ask is not a price you could trade back out of"
    return "skip", (
        f"the book is {ask - int(bid)}c wide (bid {int(bid)}c, ask {ask}c); "
        f"the limit is {int(s['display_max_spread_c'])}c"
    )


def half_legs(
    cfg: JarvisConfig,
    overs: list[OverRow],
    scorer_rows: list[dict[str, Any]],
    matchup: str,
    box: tuple[PlayerLine, ...],
    limit: int = 4,
) -> list[HalfLeg]:
    """Pre-tip overs first, then the scorer's own top picks that are not already a leg."""
    s = HO.settings(cfg)
    on = HO.enabled(cfg) and "min_edge" in s
    idx = _scorer_index(scorer_rows, matchup) if on else {}
    none_why = (
        "no live price was scored for this line at the half"
        if on
        else "the halftime scorer is off, so there is no live price"
    )
    gm = [r for r in scorer_rows if r.get("game") == matchup]
    top = (
        HO.top_overs(
            gm,
            matchup,
            float(s["min_edge"]),
            int(s["top_n"]),
            int(s["display_min_bid_c"]),
            int(s["display_max_spread_c"]),
        )  # fmt: skip
        if on
        else []
    )
    listed = {(norm_name(str(r["player_name"])), str(r["stat"]), int(r["k"])) for r in top}
    listed_ps = {(k[0], k[1]) for k in listed}
    top_n = int(s["top_n"]) if on else 0
    legs: list[HalfLeg] = []
    seen: set[tuple[str, str, int]] = set()
    for o in overs:
        key = (norm_name(o.player), o.stat, int(o.line))
        seen.add(key)
        r = idx.get(key)
        if r is not None:
            verdict, why = _verdict(r, s)
            if verdict == "enter" and key not in listed:  # the scorer's own list is the rule
                verdict = "skip"
                why = (
                    "a better strike for this player clears the bar"
                    if (key[0], key[1]) in listed_ps
                    else f"it is not among the scorer's top {top_n} for this game"
                )
            legs.append(
                HalfLeg(o.player, o.stat, int(o.line), _int(r.get("cur")), int(r["ask_c"]),
                        _int(r.get("bid_c")), _int(r.get("fee_c")), float(r["p_model"]),
                        100.0 * float(r["edge_model"]), verdict, why, True)
            )  # fmt: skip
        else:
            cur = box_cur(box, o.player, o.stat, None)
            legs.append(
                HalfLeg(o.player, o.stat, int(o.line), cur, None, None, None, None, None, "skip",
                        none_why, True)
            )  # fmt: skip
    for r in top:
        key = (norm_name(str(r["player_name"])), str(r["stat"]), int(r["k"]))
        if key in seen:
            continue
        seen.add(key)
        verdict, why = _verdict(r, s)
        legs.append(
            HalfLeg(str(r["player_name"]), str(r["stat"]), int(r["k"]), _int(r.get("cur")),
                    int(r["ask_c"]), _int(r.get("bid_c")), _int(r.get("fee_c")),
                    float(r["p_model"]), 100.0 * float(r["edge_model"]), verdict, why, False)
        )  # fmt: skip
    return legs[:limit]


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------ knowledge


def game_knowledge(kb: Any, game: Game, now: datetime) -> Knows:
    """Injuries (rotation players only), confirmed starters and open game-time decisions. Fail
    soft: a missing store becomes a note, never an error."""
    out: dict[str, list[str]] = {}
    gtd: dict[str, list[str]] = {}
    starters: dict[str, list[str]] = {}
    starters_note: dict[str, str] = {}
    notes: list[str] = []
    report_age: float | None = None
    lineup_age: float | None = None
    try:
        inj = kb.injuries(game.game_id)
        if inj.get("error"):
            notes.append("injury report not readable")
        else:
            report_age = _age_min(inj.get("report_as_of_utc"), now)
            for team, rows in (inj.get("teams") or {}).items():
                for r in key_players(rows):
                    why = f" ({short_reason(r['reason'])})" if short_reason(r.get("reason")) else ""
                    if r["status"] == "out":
                        out.setdefault(team, []).append(f"{r['player']}{why}")
                    elif r["status"] in ("questionable", "doubtful"):
                        gtd.setdefault(team, []).append(f"{r['player']} ({r['status']})")
    except Exception as exc:  # noqa: BLE001 - a card never fails on knowledge
        log.warning("card injuries unavailable (%s)", type(exc).__name__)
        notes.append("injury report not readable")
    try:
        lu = kb.lineups(game.game_id)
        if lu.get("error"):
            notes.append("lineups not readable")
        else:
            starters, starters_note = _starters(lu.get("starters") or {})
            lineup_age = _age_min(lu.get("snapshot_utc"), now)
    except Exception as exc:  # noqa: BLE001
        log.warning("card lineups unavailable (%s)", type(exc).__name__)
        notes.append("lineups not readable")
    return Knows(
        _cap(out), _cap(gtd), starters, starters_note, report_age, lineup_age, "; ".join(notes)
    )


def _starters(raw: dict[str, list[dict[str, str]]]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """The lineup store keeps every snapshot's rows, so a name can repeat and statuses mix. Distinct
    names only, 'Confirmed' ones first; five confirmed is a lineup, fewer or more is said, not
    guessed."""
    names: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for team, rows in raw.items():
        conf: list[str] = []
        exp: list[str] = []
        for r in rows:
            n = str(r.get("player", ""))
            if str(r.get("status", "")).lower() == "confirmed":
                if n not in conf:
                    conf.append(n)
            elif n not in exp and n not in conf:
                exp.append(n)
        exp = [n for n in exp if n not in conf]
        if len(conf) == 5:
            names[team], notes[team] = conf, "confirmed"
        elif len(conf) > 5:
            names[team], notes[team] = (
                conf[:5],
                f"{len(conf)} names marked confirmed, lineup moving",
            )
        elif conf:
            names[team], notes[team] = conf, "confirmed so far, not all five yet"
        else:
            names[team], notes[team] = exp[:5], "expected, not confirmed yet"
    return names, notes


def short_reason(reason: object) -> str:
    """'Injury/Illness-RightFingerSprain' -> 'right finger sprain' (the report's own words)."""
    r = re.sub(r"^Injury/Illness[- ]*", "", str(reason or "").strip(), flags=re.I)
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", r).lower().strip(" -")[:28]


def _cap(d: dict[str, list[str]], n: int = 5) -> dict[str, list[str]]:
    return {t: v if len(v) <= n else [*v[:n], f"+{len(v) - n} more"] for t, v in d.items()}


def _age_min(ts: object, now: datetime) -> float | None:
    """Minutes from a stored UTC timestamp to the card's own clock (never the wall clock)."""
    if ts is None:
        return None
    try:
        return max(0.0, (now - datetime.fromisoformat(str(ts))).total_seconds() / 60.0)
    except ValueError:
        return None


def clock_text(now: datetime, tip: datetime | None) -> str:
    if tip is None:
        return ""
    mins = int((tip - now).total_seconds() // 60)
    return "" if mins < 0 else f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"
