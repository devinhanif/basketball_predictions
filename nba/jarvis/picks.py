"""Devin's own reads: ``python -m nba.jarvis pick "DET ML" --venue kalshi --price 0.51 --stake 3``.

The pick is logged exactly as called (his price, his stake, no second-guessing) and tracked apart
from JARVIS's labelled bets: label ``pick``, never counted in the nightly cap, the kill switch,
the label record or the model-weight record. JARVIS only reports, honestly, how the price
compares to fair and which venue currently asks less. Scored later like any play: hit rate, CLV
(close mid minus his entry) and P&L.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from nba.jarvis import edge as E
from nba.jarvis.book import PaperBook, now_naive
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game, Play, Quote
from nba.jarvis.slate import SlateState

_ML = re.compile(r"^\s*([A-Za-z]{2,4})\s+(?:ML|moneyline|to win|wins?)\s*$", re.IGNORECASE)
LABEL = "pick"


class PickError(ValueError):
    """The read could not be matched to a priced market; nothing is guessed."""


@dataclass(frozen=True)
class PickResult:
    play: Play
    lines: list[str]


def _find(st: SlateState, text: str, venue: str, ticker: str | None) -> tuple[Game, Quote]:
    if ticker:
        for q in st.quotes:
            if q.venue == venue and q.ticker == ticker:
                g = st.game(q.game_id)
                if g is not None:
                    return g, q
        raise PickError(f"ticker {ticker!r} is not in tonight's {venue} quotes")
    m = _ML.match(text)
    if not m:
        raise PickError(
            f"cannot read {text!r}: say 'TEAM ML' (e.g. 'DET ML') or pass --ticker "
            "for another market"
        )
    ab = m.group(1).upper()
    for g in st.games:
        if ab in (g.away_abbr, g.home_abbr):
            tid = g.away_team if ab == g.away_abbr else g.home_team
            for q in st.quotes:
                if (
                    q.game_id == g.game_id and q.venue == venue and q.kind == "game_win"
                    and q.team_id == tid and not q.indicative
                ):  # fmt: skip
                    return g, q
            raise PickError(f"no {venue} winner market priced for {ab} yet")
    raise PickError(f"{ab} is not playing on this slate")


def build_pick(
    cfg: JarvisConfig,
    st: SlateState,
    text: str,
    *,
    venue: str,
    price: float,
    stake: float,
    ticker: str | None = None,
    side: str = "yes",
    now: datetime | None = None,
) -> PickResult:
    if venue not in ("kalshi", "polymarket"):
        raise PickError("venue must be kalshi or polymarket")
    if not 0.01 <= price <= 0.99 or stake <= 0:
        raise PickError("price must be in [0.01, 0.99] and stake positive")
    g, q = _find(st, text, venue, ticker)
    if not E.side_allowed(cfg, q.kind, side):
        raise PickError("player props are Over / Yes only; a No on a prop is not logged")
    n = 0
    while E.order_cost(cfg, venue, price, n + 1, q.series) <= stake + 1e-9:
        n += 1
        if n >= 5000:
            break
    if n < 1:
        raise PickError(f"${stake:.2f} does not buy one contract at {price * 100:.0f}c")
    cost = E.order_cost(cfg, venue, price, n, q.series)
    leg = E.leg_from_quote(q, side, price)
    ref = st.refs.for_quote(q)
    fair: float | None = None
    edge: float | None = None
    if ref is not None:
        fair = ref.p_yes if side == "yes" else 1.0 - ref.p_yes
        edge = E.edge_cents(cfg, venue, fair, price, q.series)
    tag = (now or now_naive()).isoformat()
    play = Play(
        game_id=g.game_id, matchup=g.matchup, tipoff=g.tipoff, legs=(leg,), label=LABEL,
        edge_cents=float("nan") if edge is None else edge, fair_prob=fair,
        fee_cents=E.fee_per_contract_raw(cfg, venue, price, q.series) * 100.0,
        max_price=price, contracts=n, stake_usd=round(cost, 2),
        reason="Devin's read, logged as called", pin_prob=fair, key_suffix=f"#{tag}",
        inputs={"leg_costs": [round(cost, 4)], "called_price": price, "called_stake": stake},
    )  # fmt: skip
    lines = [
        f"Pick logged: BUY {leg.desc} on {venue} ({side.upper()}) at {price * 100:.0f}c, "
        f"${cost:.2f} for {n} contracts, pays ${n:.2f} if right ({n / cost:.1f}x)."
    ]
    if fair is None:
        lines.append(
            "Fair: no Pinnacle line for this exact market, so the price cannot be measured."
        )
    else:
        verdict = (
            "a fair price or worse" if (edge or 0) < 0
            else "a thin edge" if (edge or 0) < cfg.strong_cents
            else "a strong edge"
        )  # fmt: skip
        lines.append(
            f"Fair: Pinnacle no-vig {fair * 100:.0f}c vs your {price * 100:.0f}c, "
            f"{edge:+.1f}c per $1 after the fee: {verdict}. Logged anyway."
        )
    same = [
        x for x in st.quotes
        if x.game_id == g.game_id and x.kind == q.kind and x.team_id == q.team_id
        and not x.indicative
        and x.player_id == q.player_id and x.threshold == q.threshold
    ]  # fmt: skip
    asks = sorted(
        ((x.ask(side), x.venue) for x in same if x.ask(side) is not None),
        key=lambda t: float(t[0] or 1),
    )
    if asks:
        best_ask, best_venue = asks[0]
        assert best_ask is not None
        better = (
            ""
            if price <= best_ask + 1e-9
            else f" ({(price - best_ask) * 100:.0f}c above the best ask)"
        )
        lines.append(f"Best ask now: {best_venue} {best_ask * 100:.0f}c{better}.")
    return PickResult(play, lines)


def log_pick(book: PaperBook, res: PickResult, slate: date, now: datetime | None = None) -> None:
    book.record([res.play], source="pick", slate=slate, delivered="logged", now=now)
