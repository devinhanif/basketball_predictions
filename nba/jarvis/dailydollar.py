"""The Daily Dollar: one $1 same-team long-shot combo a day (docs/TRADING_CONTEXT.md).

A ticket built for upside and written off when placed, with an honest line: EV is unknown. Legs
are the team's WIN plus the POINTS of its top-usage player (from our 2026 possessions) and,
optionally, his assists or the second-highest-usage player's points, all YES, all on Kalshi. They
are same-team by construction; nothing about them is measured as correlated on a combo price.
The venue's combo price is assumed to be the product of the single-leg asks (hypothesis; the
project logs Kalshi combo quotes to test it). The line prints the no-correlation fair price and
the correlation needed to break even next to the assumed factor (``corr_factor``, an ESTIMATE),
and says when no leg has an outside (Pinnacle) price (then fair = Kalshi mid x factor, assumed).
It buys only at or below that assumed fair, targets 50-300x, counts inside the non-reserved part
of the nightly cap, and is tracked on its own, never mixed into the label record.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from nba.jarvis import edge as E
from nba.jarvis.book import PaperBook
from nba.jarvis.config import JarvisConfig
from nba.jarvis.knowledge import Knowledge
from nba.jarvis.models import Play, PlayLeg, Quote
from nba.jarvis.slate import SlateState

LABEL = "daily_dollar"


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
    nc_fair = 1.0
    for q, f, _pin in legs:
        assert q.yes_ask is not None
        price *= q.yes_ask
        nc_fair *= f
    factor = float(dd["corr_factor"])
    fair = min(1.0, nc_fair * factor)
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
        "per": per,
        "fair": fair,
        "nc_fair": nc_fair,
        "breakeven": per / nc_fair if nc_fair > 0 else float("inf"),
        "n": n,
        "cost": cost,
        "mult": mult,
        "edge": (fair - per) * 100.0,
    }


def _candidates(
    st: SlateState, trad: list[Quote], game_id: str, tops: list[dict[str, Any]]
) -> list[list[Quote]]:
    """Prop-leg sets for one team: top player's points (a must), then optionally his assists or
    the second player's points. Thresholds enumerate the ladder."""

    def ladder(pid: int, stat: str) -> list[Quote]:
        return sorted(
            (
                q
                for q in trad
                if q.game_id == game_id
                and q.kind == "prop"
                and q.player_id == pid
                and q.stat == stat
                and 0.01 < (q.yes_ask or 0) <= 0.45
            ),
            key=lambda q: q.threshold or 0.0,
        )  # fmt: skip

    t1 = int(tops[0]["player_id"])
    pts1 = ladder(t1, "pts")
    out: list[list[Quote]] = [[p] for p in pts1]
    ast1 = ladder(t1, "ast")
    out += [[p, a] for p in pts1 for a in ast1]
    if len(tops) > 1:
        pts2 = ladder(int(tops[1]["player_id"]), "pts")
        out += [[p, q2] for p in pts1 for q2 in pts2]
    return out


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
    best: (
        tuple[float, dict[str, Any], list[tuple[Quote, float, bool]], str, list[dict[str, Any]]]
        | None
    )
    best = None
    for g in st.games:
        for w in (q for q in trad if q.game_id == g.game_id and q.kind == "game_win"):
            if w.team_id is None:
                continue
            tops = kb.top_usage(w.team_id, int(dd["top_players"]), int(dd["min_possessions"]))
            if not tops:
                continue
            for combo in _candidates(st, trad, g.game_id, tops):
                if 1 + len(combo) > int(dd["max_legs"]):
                    continue
                legs = [(w, *_leg_fair(st, w)), *[(q, *_leg_fair(st, q)) for q in combo]]
                t = _ticket(cfg, legs, budget)
                if t is not None and (best is None or t["edge"] > best[0]):
                    best = (t["edge"], t, legs, g.game_id, tops)
    if best is None:
        lo, hi = dd["target_multiple"]
        return None, (
            f"no same-team ticket (win + top-usage player's points) at or below the assumed fair "
            f"in the {lo}-{hi}x range tonight"
        )
    _, t, legs, gid, tops = best
    game = st.game(gid)
    assert game is not None
    pl = tuple(
        PlayLeg("kalshi", q.ticker, "yes", float(q.yes_ask or 0), q.kind, q.label,
                team_id=q.team_id, player_id=q.player_id, stat=q.stat, threshold=q.threshold,
                series=q.series, quote_ts=q.ts)
        for q, _f, _p in legs
    )  # fmt: skip
    n_pin = sum(1 for _q, _f, pin in legs if pin)
    top = tops[0]
    notes = (
        f"{kb.name(int(top['player_id']))} is the top-usage player: {top['share'] * 100:.0f}% of "
        f"{kb.abbr_of.get(int(legs[0][0].team_id or 0), '?')} possessions on court end "
        "with his shot "
        f"(2026, {top['on_court']} possessions)",
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
        fee_cents=(t["per"] - t["price"]) * 100.0,
        max_price=min(t["fair"], 0.99),
        contracts=t["n"],
        stake_usd=round(t["cost"], 2),
        reason=f"product of asks {t['price'] * 100:.2f}c, assumed fair {t['fair'] * 100:.2f}c",
        notes=notes,
        pin_prob=None,
        inputs={
            "leg_costs": costs,
            "legs": [[q.ticker, q.yes_ask, str(q.ts)] for q, _f, _p in legs],
            "no_corr_fair": t["nc_fair"],
            "per_contract_cost": t["per"],
            "breakeven_corr": t["breakeven"],
            "corr_factor": float(dd["corr_factor"]),
            "n_pinnacle_legs": n_pin,
            "n_legs": len(legs),
            "same_team_top_usage": True,
        },
    )
    return play, ""


def lines(p: Play | None, why: str) -> list[str]:
    """The Daily Dollar's own Discord lines: honest about what is and is not known."""
    if p is None:
        return [f"**Daily Dollar**: skipped ({why})"]
    legs = " + ".join(leg.desc for leg in p.legs)
    i = p.inputs
    assert p.fair_prob is not None and p.multiple is not None
    shared = (
        "same-team legs: the win plus the team's top-usage player's props"
        if i.get("same_team_top_usage")
        else "legs not verified as same-team"
    )
    ref = (
        f"no outside reference: fair = Kalshi mid x {i['corr_factor']:.1f} (assumed)"
        if not i["n_pinnacle_legs"]
        else f"{i['n_pinnacle_legs']} of {i['n_legs']} legs priced by Pinnacle, the rest Kalshi "
        f"mid, x {i['corr_factor']:.1f} (assumed)"
    )
    nc = float(i["no_corr_fair"])  # type: ignore[arg-type]
    per = float(i["per_contract_cost"])  # type: ignore[arg-type]
    be = float(i["breakeven_corr"])  # type: ignore[arg-type]
    return [
        f"**Daily Dollar** ($1 long shot, {p.matchup}; EV unknown (entertainment)): "
        f"BUY ALL on Kalshi (YES): {legs}",
        f"{shared}. Pays ${p.payout_usd:.0f} if all legs hit, {p.multiple:.0f}x; "
        f"costs ${p.stake_usd:.2f}.",
        f"Price {per * 100:.2f}c per $1 of payout; no-correlation fair {nc * 100:.2f}c, so it is "
        f"{(per / nc - 1) * 100:.0f}% above it and needs correlation >= {be:.2f}x "
        f"to break even (unmeasured). Buy only at or below {p.max_price * 100:.2f}c "
        f"({ref}).",
        *[f"note: {n}" for n in p.notes],
    ]


def recorded_today(book: PaperBook, slate: date) -> bool:
    return bool(
        book._rows(
            "SELECT 1 FROM plays WHERE slate_date = ? AND edge_label = ? LIMIT 1", [slate, LABEL]
        )
    )
