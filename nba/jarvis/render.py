"""Python-rendered play text. Every number here comes from a computed ``Play``; the language model
contributes at most one clearly separated explanation line (already checked in ``explain.py``).

True odds are shown the same way whether the bet later wins or loses; a hit is annotated, never
celebrated (``scored_line``). No stake prompts: the stake shown is the paper book's size.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Play, PlayLeg

VENUE = {"kalshi": "Kalshi", "polymarket": "Polymarket"}


def cents(x: float) -> str:
    c = x * 100.0
    return f"{c:.0f}c" if abs(c - round(c)) < 0.05 else f"{c:.1f}c"


def clock(ts: datetime | None, tz: str) -> str:
    if ts is None:
        return "tip ?"
    local = ts.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))
    return local.strftime("%-I:%M%p").lower().replace("m", "") + local.strftime(" %Z")


def edge_phrase(p: Play) -> str:
    e = p.edge_cents
    nan = e != e
    if p.label == "arbitrage":
        return f"ARBITRAGE {e:+.1f}c per pair after fees (risk-free only if both legs fill)"
    if p.label == "strong":
        if len(p.legs) == 2:
            return f"STRONG {e:+.1f}c per pair after fees (arbitrage pending fee check)"
        return f"STRONG edge {e:+.1f}c vs Pinnacle no-vig after fee"
    if p.label == "thin":
        return f"THIN edge {e:+.1f}c vs Pinnacle no-vig after fee"
    if nan:
        return "NO EDGE measured: no Pinnacle line for this bet (entertainment)"
    return f"NO EDGE {e:+.1f}c vs Pinnacle no-vig after fee (fair price or worse: entertainment)"


def leg_phrase(leg: PlayLeg, max_price: float | None = None) -> str:
    cap = "" if max_price is None else f", max {cents(max_price)}"
    venue = VENUE.get(leg.venue, leg.venue)
    return f"{venue} {leg.side.upper()} {leg.desc} at {cents(leg.price)}{cap}"


def stake_phrase(p: Play) -> str:
    if p.contracts <= 0:
        return "stake $0 (nightly cap used)"
    unit = "pair" if len(p.legs) == 2 else "contract"
    return f"stake ${p.stake_usd:.2f} ({p.contracts} {unit}{'' if p.contracts == 1 else 's'})"


def play_lines(p: Play, tz: str) -> list[str]:
    head = f"{p.matchup} {clock(p.tipoff, tz)}"
    if len(p.legs) == 2:
        mp: list[float | None] = list(p.inputs.get("max_prices") or [None, None])  # type: ignore[call-overload]
        legs = [leg_phrase(leg, mp[i]) for i, leg in enumerate(p.legs)]
        body = f"PLAY: {legs[0]} + {legs[1]}; {stake_phrase(p)}"
    else:
        body = f"PLAY: {leg_phrase(p.legs[0], p.max_price)}; {stake_phrase(p)}"
    view = []
    if p.model_prob is not None and p.fair_prob is not None:
        flag = "" if p.model_prob >= p.legs[0].price else " (our model sees it below this price)"
        view = [
            f"view: our model {p.model_prob * 100:.0f}% vs Pinnacle {p.fair_prob * 100:.0f}%{flag}"
        ]
    return [head, body, edge_phrase(p), *view, *[f"note: {n}" for n in p.notes if n][:2]]


def full_text(p: Play, explanation: str, tz: str) -> str:
    return "\n".join([*play_lines(p, tz), f"why: {explanation}" if explanation else ""]).strip()


def compact_line(p: Play, tz: str) -> str:
    leg = p.legs[0]
    tail = (
        f"+{VENUE.get(p.legs[1].venue, p.legs[1].venue)} {p.legs[1].desc}"
        if len(p.legs) == 2
        else ""
    )
    e = "n/a" if p.edge_cents != p.edge_cents else f"{p.edge_cents:+.1f}c"
    cap = cents(p.max_price)
    st = f"${p.stake_usd:.2f}" if p.contracts else "$0"
    return (
        f"{p.matchup} {clock(p.tipoff, tz).split(' ')[0]} {VENUE.get(leg.venue, leg.venue)} "
        f"{leg.side.upper()} {leg.desc}{tail} max {cap} {st} [{p.label} {e}]"
    )


def _pack(lines: list[str], limit: int) -> list[list[str]]:
    texts: list[list[str]] = [[]]
    for ln in lines:
        ln = ln if len(ln) <= limit else ln[: limit - 1] + "…"
        if sum(len(x) + 1 for x in texts[-1]) + len(ln) > limit and texts[-1]:
            texts.append([])
        texts[-1].append(ln)
    return texts


def chunk(lines: list[str], max_chars: int, max_texts: int) -> list[str]:
    """Pack whole lines into texts of at most ``max_chars`` (counting the "(i/n) " prefix); past
    ``max_texts`` the tail is dropped with a count. Over-long lines are cut with an ellipsis."""
    texts = _pack(lines, max_chars)
    if len(texts) > 1:
        texts = _pack(lines, max_chars - 8)  # room for the "(i/n) " prefix
    if len(texts) > max_texts:
        dropped = sum(len(t) for t in texts[max_texts:])
        texts = texts[:max_texts]
        while True:
            note = f"(+{dropped} more lines not sent)"
            if (
                sum(len(x) + 1 for x in texts[-1]) + len(note) <= max_chars - 8
                or len(texts[-1]) <= 1
            ):
                texts[-1].append(note)
                break
            texts[-1].pop()
            dropped += 1
    out = ["\n".join(t) for t in texts]
    if len(out) > 1:
        out = [f"({i + 1}/{len(out)}) {t}" for i, t in enumerate(out)]
    return out


def slate_texts(
    cfg: JarvisConfig,
    plays: list[Play],
    top_explanation: str,
    *,
    date_label: str,
    first_tip: datetime | None,
    n_games: int,
) -> list[str]:
    tz = cfg.display_tz
    head = f"JARVIS {date_label}: {n_games} games, first tip {clock(first_tip, tz)}"
    lines = [head]
    if plays:
        top = plays[0]
        lines += play_lines(top, tz)[1:]
        lines.append(f"why: {top_explanation}" if top_explanation else "")
        lines.append("Best play per game:")
        lines += [f"{i + 1}) {compact_line(p, tz)}" for i, p in enumerate(plays)]
    else:
        lines.append("No live prices read yet for tonight's games (snapshot feed stale or down).")
    return chunk([ln for ln in lines if ln], cfg.max_chars, cfg.max_texts)


def alert_text(cfg: JarvisConfig, p: Play, explanation: str, reason: str) -> list[str]:
    lines = [f"JARVIS ALERT ({reason})", *play_lines(p, cfg.display_tz)]
    if explanation:
        lines.append(f"why: {explanation}")
    return chunk(lines, cfg.max_chars, cfg.max_texts)


def scored_line(row: dict[str, object]) -> str:
    """Same format for wins and losses; a hit carries the annotation, a miss nothing extra."""
    price = float(row["price"])  # type: ignore[arg-type]
    edge = row.get("edge_cents")
    e = "n/a" if edge is None or edge != edge else f"{float(edge):+.1f}c"  # type: ignore[arg-type]
    base = (
        f"{row['slate_date']} {row['matchup']} {row['venue']} {str(row['side']).upper()} "
        f"{row['ticker']} at {cents(price)} [{row['edge_label']} {e}] "
        f"pnl ${float(row['pnl_usd']):+.2f}"  # type: ignore[arg-type]
    )
    if row.get("won") and edge is not None and edge == edge:
        ev_pct = float(edge) / (price * 100.0) * 100.0  # type: ignore[arg-type]
        base += f" -- this one hit; it was still a {ev_pct:+.0f}% bet"
    return base
