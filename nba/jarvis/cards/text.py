"""Deterministic sentence templates for the card. No language model, no invented number.

Every figure printed here is an argument that a caller read from a computed value; every sentence
is a fixed template over those arguments. A hit and a miss are formatted identically (a hit adds
the annotation "this one hit; it was still a -X% bet"). No stake, no sizing prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nba.jarvis.cards.model import CardData, HalfLeg, OverRow, Recap, SettledRow
from nba.jarvis.render import VENUE, cents

STAT = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "3PM"}
NO_OVER = "No over has positive EV after fees tonight."
DISCLAIMER = "Paper only. Not advice."

ON_PACE, BEHIND, HIT, MISS, NEUTRAL = "🟢", "🟠", "✅", "❌", "⚪"


@dataclass(frozen=True)
class Style:
    tz: str = "America/New_York"
    pace_fraction: float = 0.5  # share of the line expected by the half
    narrow_pts: float = 3.0  # model vs sharp line within this: "narrow"
    wide_pts: float = 8.0  # ... at least this far apart: "wide"
    bar_width: int = 10
    min_edge_c: float = 5.0  # the halftime scorer's bar, for display only


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def tz_label(style: Style, ts: datetime) -> str:
    z = ts.replace(tzinfo=UTC).astimezone(ZoneInfo(style.tz)).strftime("%Z")
    return "ET" if z in ("EST", "EDT") else z


def clock(ts: datetime | None, style: Style) -> str:
    if ts is None:
        return "tip time not set"
    loc = ts.replace(tzinfo=UTC).astimezone(ZoneInfo(style.tz))
    return f"{loc.strftime('%-I:%M %p')} {tz_label(style, ts)}"


def bar(cur: int | None, line: float, width: int) -> str:
    """Character progress toward the line: ▰ filled, ▱ empty. Unknown stat: all empty."""
    frac = 0.0 if cur is None or line <= 0 else min(1.0, max(0.0, cur / line))
    n = round(frac * width)
    return "▰" * n + "▱" * (width - n)


def line_label(line: float, stat: str) -> str:
    return f"{line:g}+ {STAT.get(stat, stat)}"


def title(card: CardData, style: Style) -> str:
    return f"{card.away} @ {card.home} — {clock(card.tip, style)}"


def status_line(card: CardData, tip_in: str = "") -> str:
    a, h = card.away, card.home
    if card.phase == "final":
        return f"🏁 **Final** — {a} {card.away_score}, {h} {card.home_score}"
    if card.phase == "halftime":
        return f"🟡 **Halftime** — {a} {card.away_score}, {h} {card.home_score}"
    if card.phase == "live":
        when = f" {card.clock}" if card.clock else ""
        return f"🔴 **Live**{when} — {a} {card.away_score}, {h} {card.home_score}"
    return "⏳ **Pre-tip**" + (f" — tips in {tip_in}" if tip_in else "")


# ------------------------------------------------------------------------------ overs


def agreement_word(gap: float, style: Style) -> str:
    if gap <= style.narrow_pts:
        return "close"
    return "far apart" if gap >= style.wide_pts else "some gap"


def confidence_words(row: OverRow, style: Style) -> str:
    """How closely our model and the sharp line agree (NOT a confidence interval)."""
    if row.model is None or row.pin is None:
        return "No model number for this one, so the sharp line is the only read."
    gap = abs(row.model - row.pin) * 100.0
    below = f" That is below the {cents(row.ask)} price." if row.model < row.ask else ""
    return (
        f"Our model says {row.model * 100:.0f}%; it and the sharp line are "
        f"{plural(round(gap), 'percentage point')} apart ({agreement_word(gap, style)})." + below
    )


def ev_phrase(row: OverRow) -> str:
    ev_pct = row.edge_c / (row.ask * 100.0 + row.fee_c) * 100.0
    return (
        f"EV {row.edge_c / 100.0:+.2f} per $1 contract ({ev_pct:+.0f}%) "
        f"after the {row.fee_c:.0f}c fee"
    )


def why_line(row: OverRow, style: Style) -> str:
    venue = VENUE.get(row.venue, row.venue)
    bits = [f"{venue} asks {cents(row.ask)}"]
    sharp = row.pin if row.pin is not None else row.fair
    if sharp is not None:
        age = "" if row.ref_age_min is None else f", {row.ref_age_min:.0f} min old"
        plays = ", adjusted for the chance he plays" if row.includes_p_plays else ""
        bits.append(f"the sharp line (Pinnacle, no vig{plays}{age}) puts fair at {cents(sharp)}")
    if row.fair is not None and sharp is not None and abs(row.fair - sharp) > 1e-9:
        bits.append(f"JARVIS fair {cents(row.fair)}")
    return "; ".join(bits) + ". " + confidence_words(row, style)


def status_of_over(
    row: OverRow,
    legs: dict[tuple[str, str, int], HalfLeg],
    rows: dict[tuple[str, str, float], SettledRow],
    style: Style,
) -> str:
    s = rows.get((row.player, row.stat, row.line))
    if s is not None:
        return NEUTRAL if s.status == "void" else (HIT if s.won else MISS)
    leg = legs.get((row.player, row.stat, int(row.line)))
    if leg is not None and leg.cur is not None:
        return ON_PACE if leg.cur >= row.line * style.pace_fraction else BEHIND
    return NEUTRAL


def overs_field(card: CardData, style: Style) -> tuple[str, str]:
    name = "Top overs by EV after fees" + (" (as shown pre-tip)" if card.overs_frozen else "")
    if not card.overs:
        return name, card.overs_note or NO_OVER
    legs = {(h.player, h.stat, h.line): h for h in card.half}
    settled = {(r.player, r.stat, r.line): r for r in (card.recap.rows if card.recap else ())}
    lines: list[str] = []
    for i, o in enumerate(card.overs, 1):
        e = status_of_over(o, legs, settled, style)
        lines.append(
            f"{e} **{i}. {o.player} {line_label(o.line, o.stat)}** — ask {cents(o.ask)}, "
            f"fair {'n/a' if o.fair is None else cents(o.fair)}, {ev_phrase(o)}"
            + (f" ({o.stale})" if o.stale else "")
        )
        lines.append(f"> {why_line(o, style)}")
    return name, "\n".join(lines)


# ------------------------------------------------------------------------------ halftime


def half_field(card: CardData, style: Style) -> tuple[str, str] | None:
    if card.phase not in ("halftime", "final", "live") or not card.half:
        if card.phase == "halftime":
            return "Halftime entry check", "Nothing to enter at the half: no leg clears the bar."
        return None
    lines: list[str] = []
    for h in card.half:
        lines += _leg_lines(h, style)
    return "Halftime entry check", "\n".join(lines)


def _leg_lines(h: HalfLeg, style: Style) -> list[str]:
    head = f"**{h.player} {line_label(h.line, h.stat)}**"
    if h.cur is None:
        pace = f"{NEUTRAL} {head}: the box score was not read, so no pace call."
    else:
        need = max(0, h.line - h.cur)
        expect = h.line * style.pace_fraction
        if h.cur >= h.line:
            tail, emoji = "already there", ON_PACE
        elif h.cur >= expect:
            tail, emoji = f"needs {need} more, on pace", ON_PACE
        else:
            tail, emoji = f"needs {need} more, behind pace ({expect:g} expected by now)", BEHIND
        pace = f"{emoji} {head} `{bar(h.cur, h.line, style.bar_width)}` {h.cur} of {h.line}, {tail}"
    price = ""
    if h.ask_c is not None and h.p_model is not None:
        bid = "no bid" if h.bid_c is None else f"bid {h.bid_c}c"
        price = f"live ask {h.ask_c}c ({bid}) vs model {round(100 * h.p_model)}%. "
    verdict = ("**Enter**: " if h.verdict == "enter" else "**Skip**: ") + h.why + "."
    return [pace, f"> {price}{verdict}"]


# ------------------------------------------------------------------------------ knowledge


def _team_lists(d: dict[str, list[str]], first: str | None) -> str:
    teams = sorted(d, key=lambda t: (t != first, t))
    return "; ".join(f"{t}: {', '.join(d[t])}" for t in teams)


def knows_field(card: CardData, style: Style, channel_team: str | None = None) -> tuple[str, str]:
    k = card.knows
    lines: list[str] = []
    lines.append(
        f"**Out:** {_team_lists(k.out, channel_team)}."
        if k.out
        else "**Out:** no rotation player is listed out."
    )
    lines.append(
        f"**Game-time decisions:** {_team_lists(k.gtd, channel_team)}."
        if k.gtd
        else "**Game-time decisions:** none open."
    )
    if k.starters:
        age = "" if k.lineup_age_min is None else f" Lineups read {k.lineup_age_min:.0f} min ago."
        teams = sorted(k.starters, key=lambda t: (t != channel_team, t))
        parts = [
            f"{t}: {', '.join(k.starters[t])}"
            + (f" ({k.starters_note[t]})" if k.starters_note.get(t) else "")
            for t in teams
        ]
        lines.append(f"**Starters:** {'; '.join(parts)}.{age}")
    else:
        lines.append("**Starters:** not out yet (they usually land about 30 minutes before tip).")
    if k.report_age_min is not None:
        lines.append(f"The injury report is {k.report_age_min:.0f} min old.")
    lines.append(_sure_line(card.overs, style))
    if k.note:
        lines.append(f"({k.note})")
    return "What the game knows", "\n".join(lines)


def _sure_line(overs: tuple[OverRow, ...], style: Style) -> str:
    head = "**Model vs sharp line** (agreement, not a confidence interval):"
    gaps = [
        abs(o.model - o.pin) * 100.0 for o in overs if o.model is not None and o.pin is not None
    ]
    if not overs:
        return f"{head} no over to compare."
    if not gaps:
        return f"{head} no model number on these overs; the sharp line is the only read."
    words = [agreement_word(g, style) for g in gaps]
    parts = [f"{words.count(w)} {w}" for w in ("close", "some gap", "far apart") if w in words]
    return (
        f"{head} on {plural(len(gaps), 'over')}, " + ", ".join(parts) + f" (close = within "
        f"{style.narrow_pts:g} percentage points, far apart = {style.wide_pts:g} or more)."
    )


# ------------------------------------------------------------------------------ recap


def settled_line(r: SettledRow) -> str:
    base = f"{line_label(r.line, r.stat)} {r.player}: bought at {cents(r.ask)} + {r.fee_c:.0f}c fee"
    if r.status == "void":
        return f"{NEUTRAL} {base}; did not play, the venue's rule decides, not counted"
    out = f"{HIT if r.won else MISS} {base}: {r.units:+.2f} units"
    if r.won:
        pct = r.edge_c / (r.ask * 100.0 + r.fee_c) * 100.0
        out += f" — this one hit; it was a {pct:+.0f}% EV bet when shown"
    return out


def recap_field(card: CardData) -> tuple[str, str] | None:
    rc = card.recap
    if card.phase != "final" or rc is None:
        return None
    lines = [settled_line(r) for r in rc.rows]
    if rc.waiting:
        lines.append(
            f"{NEUTRAL} Waiting on the box score for {plural(rc.waiting, 'over')}; "
            "this card updates when it lands."
        )
    if not rc.rows and not rc.waiting:
        lines.append("No over was shown for this game, so there is nothing to score.")
    settled_n = rc.n_hit + rc.n_miss
    if settled_n:
        lines.append(
            f"**This game:** {rc.n_hit} hit, {rc.n_miss} miss, {rc.units:+.2f} units on paper "
            "(each over risks one unit)."
        )
    lines.append(season_line(rc))
    return "Recap", "\n".join(lines)


def season_line(rc: Recap) -> str:
    return (
        f"**Season so far** (descriptive, not a tested claim): n = {rc.season_n} settled over "
        f"{plural(rc.season_dates, 'date')}, {rc.season_hit} hit, "
        f"{rc.season_n - rc.season_hit} miss, {rc.season_units:+.2f} units."
    )


def footer(card: CardData, style: Style) -> str:
    return f"Data as of {clock(card.as_of, style)} · {DISCLAIMER}"
