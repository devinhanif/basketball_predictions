"""The Daily Dollar: one $1 correlated long-shot combo a day (docs/TRADING_CONTEXT.md).

A ticket built for upside and written off when placed. Legs are CORRELATED (a team win with its
own players' big nights in the same game), all YES, all on one venue (Kalshi, where the props
are). The venue's combo price is assumed to be the product of the single-leg asks (hypothesis;
the project logs Kalshi combo quotes to test it). Fair = product of the single-leg fair prices x
``corr_factor``, an ESTIMATE, not a measurement (configs/jarvis.yaml says so). It buys only at or
below fair and targets a 50-300x payout. A leg's fair price is Pinnacle's no-vig where posted,
else the venue mid (said so). It counts inside the nightly cap (the non-reserved part) and is
tracked on its own, never mixed into the label record.
"""

from __future__ import annotations

from datetime import date
from itertools import combinations
from typing import Any

from nba.jarvis import edge as E
from nba.jarvis.book import PaperBook
from nba.jarvis.config import JarvisConfig
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.models import Play, PlayLeg, Quote
from nba.jarvis.slate import SlateState

LABEL = "daily_dollar"
MAX_PROPS = 12  # prop legs considered per team (the priciest, most liquid, long-shot band)


def _leg_fair(st: SlateState, q: Quote) -> tuple[float, bool]:
    """(fair YES price, from_pinnacle). Pinnacle no-vig where posted, else the venue mid."""
    ref = st.refs.for_quote(q)
    if ref is not None:
        return ref.p_yes, True
    assert q.yes_ask is not None
    bid = q.yes_bid if q.yes_bid is not None else q.yes_ask
    return (bid + q.yes_ask) / 2.0, False


def _ticket(
    cfg: JarvisConfig, legs: list[tuple[Quote, float, bool]], budget: float
) -> dict[str, Any] | None:
    dd = cfg.daily_dollar
    price = 1.0
    fair = 1.0
    for q, f, _pin in legs:
        assert q.yes_ask is not None
        price *= q.yes_ask
        fair *= f
    fair = min(1.0, fair * float(dd["corr_factor"]))
    if not 0.0 < price < 1.0:
        return None
    n = E.contracts_for(cfg, "kalshi", price, budget, budget, "KXNBAGAME")
    if n < 1:
        return None
    cost = E.order_cost(cfg, "kalshi", price, n, "KXNBAGAME")
    per = cost / n
    lo, hi = (float(x) for x in dd["target_multiple"])
    mult = n / cost
    if not (lo <= mult <= hi) or per > fair + 1e-12:
        return None
    return {
        "price": price,
        "fair": fair,
        "n": n,
        "cost": cost,
        "mult": mult,
        "edge": (fair - per) * 100.0,
    }


def build(
    cfg: JarvisConfig, st: SlateState, kb: Knowledge, *, room: float
) -> tuple[Play | None, str]:
    """(ticket, why-not). ``room`` is what the non-reserved part of tonight's cap has left."""
    dd = cfg.daily_dollar
    if not dd.get("enabled", True):
        return None, "disabled in config"
    budget = min(float(dd["stake_usd"]), room)
    if budget < float(dd["stake_usd"]) - 1e-9:
        return None, "the nightly cap room is below $1"
    trad = [
        q for q in st.tradable(cfg) if q.venue == "kalshi" and q.yes_ask and 0.01 < q.yes_ask < 0.99
    ]
    props = [q for q in trad if q.kind == "prop" and 0.02 <= (q.yes_ask or 0) <= 0.45]
    teams = kb.player_teams({q.player_id for q in props if q.player_id})
    best: tuple[float, dict[str, Any], list[tuple[Quote, float, bool]], str] | None = None
    for g in st.games:
        wins = [q for q in trad if q.game_id == g.game_id and q.kind == "game_win"]
        for w in wins:
            mine = [
                q for q in props
                if q.game_id == g.game_id and q.player_id and teams.get(q.player_id) == w.team_id
            ][:MAX_PROPS * 4]  # fmt: skip
            mine.sort(key=lambda q: -(q.yes_ask or 0))
            mine = mine[:MAX_PROPS]
            base = [(w, *_leg_fair(st, w))]
            for k in range(1, int(dd["max_legs"])):
                for combo in combinations(mine, k):
                    if len({q.player_id for q in combo}) < k:
                        continue  # different players: a leg per player
                    legs = [*base, *[(q, *_leg_fair(st, q)) for q in combo]]
                    t = _ticket(cfg, legs, budget)
                    if t is not None and (best is None or t["edge"] > best[0]):
                        best = (t["edge"], t, legs, g.game_id)
    if best is None:
        lo, hi = dd["target_multiple"]
        return None, f"no correlated ticket at or below fair in the {lo}-{hi}x range tonight"
    _, t, legs, gid = best
    game = st.game(gid)
    assert game is not None
    pl = tuple(
        PlayLeg("kalshi", q.ticker, "yes", float(q.yes_ask or 0), q.kind, q.label,
                team_id=q.team_id, player_id=q.player_id, stat=q.stat, threshold=q.threshold,
                series=q.series, quote_ts=q.ts)
        for q, _f, _p in legs
    )  # fmt: skip
    n_pin = sum(1 for _q, _f, pin in legs if pin)
    notes = (
        f"fair = product of leg fair prices x {float(dd['corr_factor']):.2f} correlation factor "
        "(an ESTIMATE, not measured)",
        f"leg fair prices: {n_pin} of {len(legs)} from Pinnacle, the rest the venue mid",
        "price assumes the venue prices the combo at the product of the leg asks: ask Kalshi for a "
        "combo quote and buy only at or below the max price",
    )
    costs = [round(t["cost"], 4)] + [0.0] * (len(pl) - 1)
    play = Play(
        game_id=gid,
        matchup=game.matchup,
        tipoff=game.tipoff,
        legs=pl,
        label=LABEL,
        edge_cents=t["edge"],
        fair_prob=t["fair"],
        fee_cents=(t["cost"] / t["n"] - t["price"]) * 100.0,
        max_price=min(t["fair"], 0.99),
        contracts=t["n"],
        stake_usd=round(t["cost"], 2),
        reason=f"product of asks {t['price'] * 100:.2f}c, fair {t['fair'] * 100:.2f}c",
        notes=notes,
        pin_prob=None,
        inputs={
            "leg_costs": costs,
            "legs": [[q.ticker, q.yes_ask, str(q.ts)] for q, _f, _p in legs],
        },
    )
    return play, ""


def lines(p: Play | None, why: str) -> list[str]:
    """The Daily Dollar's own Discord lines."""
    if p is None:
        return [f"**Daily Dollar**: skipped ({why})"]
    legs = " + ".join(leg.desc for leg in p.legs)
    assert p.fair_prob is not None and p.multiple is not None
    return [
        f"**Daily Dollar** ($1 long shot, {p.matchup}): BUY ALL on Kalshi (YES): {legs}",
        f"buy only at or below {p.max_price * 100:.2f}c per $1 of payout "
        f"(fair {p.fair_prob * 100:.2f}c "
        f"with an ESTIMATED correlation factor); pays ${p.payout_usd:.0f} if all legs hit, "
        f"{p.multiple:.0f}x; costs ${p.stake_usd:.2f}",
        *[f"note: {n}" for n in p.notes],
    ]


def recorded_today(book: PaperBook, slate: date) -> bool:
    return bool(
        book._rows(
            "SELECT 1 FROM plays WHERE slate_date = ? AND edge_label = ? LIMIT 1", [slate, LABEL]
        )
    )
