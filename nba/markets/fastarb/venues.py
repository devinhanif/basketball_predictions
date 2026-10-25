"""Parse Kalshi, Polymarket US and Polymarket international payloads into canonical quotes.

Pure functions over already-fetched JSON (fixtures in tests). Nothing here touches the network.
Whole-number lines, unknown teams, closed markets and fee-coefficient mismatches are skipped and
counted in ``QuoteBook.skipped``; nothing is guessed.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from nba.live import kalshi_pull as kp
from nba.markets.fastarb.props import (
    Ask,
    GameKey,
    PropKey,
    QuoteBook,
    is_half_point,
    margin_side,
    team_id,
)

K = "kalshi"
PM_US = "polymarket_us"
PM_INTL = "polymarket_intl"

_SPREAD_SUFFIX = re.compile(r"^([A-Z]+?)(\d+)$")
_SLUG = re.compile(r"^nba-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$")


def _f(v: Any) -> float | None:
    return kp.to_f(v)


def _amount(a: Any) -> float | None:
    return _f(a.get("value")) if isinstance(a, dict) else None


def game_from_slug(slug: str) -> GameKey | None:
    m = _SLUG.match(slug)
    if m is None:
        return None
    away, home = team_id(m[1]), team_id(m[2])
    if away is None or home is None:
        return None
    return GameKey(away, home, m[3])


def _put(book: QuoteBook, prop: PropKey, venue: str, is_yes: bool, ask: Ask) -> None:
    book.add(prop, venue, "yes" if is_yes else "no", ask)


# --------------------------------------------------------------------------------------- Kalshi


def kalshi_quotes(markets: list[dict[str, Any]]) -> QuoteBook:
    """KXNBAGAME / KXNBASPREAD / KXNBATOTAL open markets -> canonical quotes (both sides each)."""
    book = QuoteBook()
    for m in markets:
        if str(m.get("status", "active")) not in ("active", "open"):
            book.skipped[f"{K}:not_open"] += 1
            continue
        ticker = str(m.get("ticker", ""))
        series = ticker.split("-")[0]
        ev = str(m.get("event_ticker", ""))
        teams, day = kp.event_teams(ev), kp.event_date(ev)
        if teams is None or day is None:
            book.skipped[f"{K}:bad_event"] += 1
            continue
        away, home = team_id(teams[0]), team_id(teams[1])
        if away is None or home is None:
            book.skipped[f"{K}:unmatched_team:{teams[0]}/{teams[1]}"] += 1
            continue
        game = GameKey(away, home, day.isoformat())
        yes_ask, no_ask = _f(m.get("yes_ask_dollars")), _f(m.get("no_ask_dollars"))
        yes_sz, no_sz = _f(m.get("yes_ask_size_fp")), _f(m.get("yes_bid_size_fp"))
        title = str(m.get("title", ticker))
        sides: list[tuple[PropKey, bool]] = []  # (prop, is_yes) for the market's YES contract
        suffix = ticker.rsplit("-", 1)[-1]
        strike = _f(m.get("floor_strike"))
        if series == "KXNBAGAME":
            t = team_id(suffix)
            ms = None if t is None else margin_side(game, t, 0.0, True)
            if ms is None:
                book.skipped[f"{K}:unmatched_team:{suffix}"] += 1
                continue
            sides.append((PropKey(game, "margin", 0.0), ms[1]))
        elif series == "KXNBASPREAD":
            mm = _SPREAD_SUFFIX.match(suffix)
            t = team_id(mm[1]) if mm else None
            if t is None or strike is None or m.get("strike_type") != "greater":
                book.skipped[f"{K}:unmatched_spread:{suffix}"] += 1
                continue
            ms = margin_side(game, t, -strike, True)  # "T wins by over L" = T covers -L
            if ms is None or not is_half_point(ms[0]):
                book.skipped[f"{K}:bad_line"] += 1
                continue
            sides.append((PropKey(game, "margin", ms[0]), ms[1]))
        elif series == "KXNBATOTAL":
            if strike is None or not is_half_point(strike) or m.get("strike_type") != "greater":
                book.skipped[f"{K}:bad_line"] += 1
                continue
            sides.append((PropKey(game, "total", strike), True))
        else:
            book.skipped[f"{K}:series_ignored"] += 1
            continue
        for prop, yes_means_yes in sides:
            for is_contract_yes, px, sz in ((True, yes_ask, yes_sz), (False, no_ask, no_sz)):
                if px is None:
                    continue
                canonical_yes = is_contract_yes if yes_means_yes else not is_contract_yes
                side_txt = "YES" if is_contract_yes else "NO"
                levels = [(px, sz)] if sz else []
                _put(
                    book,
                    prop,
                    K,
                    canonical_yes,
                    Ask(
                        K,
                        px,
                        f"Kalshi {side_txt} on '{title}' ({ticker})",
                        ticker,
                        levels,
                        f"Kalshi {ticker} {side_txt}",
                    ),
                )
    return book


# ---------------------------------------------------------------------------- Polymarket US

US_WINNER = "basketball_team_full_game_winner"
US_SPREAD = "basketball_team_full_game_spread"
US_TOTAL = "basketball_team_full_game_total"
US_TYPES = (US_WINNER, US_SPREAD, US_TOTAL)


def us_filter_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep only what the checker reads (the full event is ~2 MB: props, quarters, halves)."""
    keep = ("slug", "title", "startTime", "live", "ended", "teams")
    out = {k: event.get(k) for k in keep}
    out["teams"] = [
        {k: t.get(k) for k in ("id", "name", "abbreviation")} for t in event.get("teams") or []
    ]
    mk = []
    for m in event.get("markets") or []:
        if m.get("sportsMarketType") not in US_TYPES:
            continue
        mk.append(
            {
                k: m.get(k)
                for k in (
                    "slug",
                    "sportsMarketType",
                    "line",
                    "status",
                    "active",
                    "closed",
                    "feeCoefficient",
                    "bestBidQuote",
                    "bestAskQuote",
                    "question",
                )
            }
            | {
                "marketSides": [
                    {k: s.get(k) for k in ("description", "long", "teamId", "quote", "tradable")}
                    for s in m.get("marketSides") or []
                ]
            }
        )
    out["markets"] = mk
    return out


def us_is_live(event: dict[str, Any]) -> bool:
    return bool(event.get("live") or event.get("ended"))


def us_rules_text(event: dict[str, Any]) -> dict[str, Any]:
    """Event and per-market-type rules text from a RAW gateway event (archived once a day)."""
    by_type: dict[str, dict[str, Any]] = {}
    for m in event.get("markets") or []:
        t = m.get("sportsMarketType")
        if t in US_TYPES and t not in by_type:
            by_type[t] = {
                "slug": m.get("slug"),
                "description": m.get("description"),
                "rulesDisclaimer": m.get("rulesDisclaimer"),
            }
    return {"slug": event.get("slug"), "description": event.get("description"), "markets": by_type}


def us_quotes(
    event: dict[str, Any],
    fee_coefficient: float,
    books: dict[str, dict[str, Any]] | None = None,
) -> QuoteBook:
    """A (filtered or full) gateway event -> quotes.

    ``books`` = market slug -> /book payload; with a book the Ask carries every level, without one
    it carries the quoted top price and unknown size."""
    book = QuoteBook()
    game = game_from_slug(str(event.get("slug", "")))
    if game is None:
        book.skipped[f"{PM_US}:bad_event_slug"] += 1
        return book
    if us_is_live(event):  # in play: prices move per possession; never compared here
        book.skipped[f"{PM_US}:live"] += 1
        return book
    id_to_team: dict[int, int] = {}
    for t in event.get("teams") or []:
        tid = team_id(str(t.get("abbreviation", "")))
        if tid is not None and t.get("id") is not None:
            id_to_team[int(t["id"])] = tid
    for m in event.get("markets") or []:
        mtype = m.get("sportsMarketType")
        if mtype not in US_TYPES:
            continue
        if m.get("status") != "MARKET_STATUS_OPEN" or m.get("closed"):
            book.skipped[f"{PM_US}:not_open"] += 1
            continue
        fc = _f(m.get("feeCoefficient"))
        if fc is None or abs(fc - fee_coefficient) > 1e-9:
            book.skipped[f"{PM_US}:fee_mismatch"] += 1
            continue
        slug = str(m.get("slug", ""))
        mkt_book = (books or {}).get(slug)
        for s in m.get("marketSides") or []:
            px = _amount(s.get("quote"))
            if px is None or not s.get("tradable", True):
                continue
            desc = str(s.get("description", ""))
            team = id_to_team.get(int(s["teamId"])) if s.get("teamId") is not None else None
            prop_side: tuple[PropKey, bool] | None = None
            if mtype == US_WINNER:
                ms = None if team is None else margin_side(game, team, 0.0, True)
                if ms is not None:
                    prop_side = (PropKey(game, "margin", 0.0), ms[1])
            elif mtype == US_SPREAD:
                try:
                    h = float(desc)
                except ValueError:
                    h = None
                ms = None if team is None or h is None else margin_side(game, team, h, True)
                if ms is not None and is_half_point(ms[0]):
                    prop_side = (PropKey(game, "margin", ms[0]), ms[1])
            else:
                line = _f(m.get("line"))
                if line is not None and is_half_point(line) and desc in ("Over", "Under"):
                    prop_side = (PropKey(game, "total", line), desc == "Over")
            if prop_side is None:
                book.skipped[f"{PM_US}:unmatched_side"] += 1
                continue
            levels = us_levels(mkt_book, bool(s.get("long"))) if mkt_book else []
            if levels:
                px = levels[0][0]  # the book is fresher than the event payload
            _put(
                book,
                prop_side[0],
                PM_US,
                prop_side[1],
                Ask(
                    PM_US,
                    px,
                    f"Polymarket US '{desc}' on {m.get('question', slug)} ({slug})",
                    slug,
                    levels,
                    f"PolyUS {slug} '{desc}'",
                ),
            )
    return book


def us_levels(book_payload: dict[str, Any] | None, long_side: bool) -> list[tuple[float, float]]:
    """Ask levels (price, contracts), cheapest first, for buying the LONG or SHORT instrument.

    Long buys lift the offers; short buys hit the bids at price 1 - bid. Sort order of the payload
    is not documented, so it is never relied on."""
    md = (book_payload or {}).get("marketData") or {}
    if long_side:
        rows = [(_amount(r.get("px")), _f(r.get("qty"))) for r in md.get("offers") or []]
        lv = [(p, q) for p, q in rows if p is not None and q]
        return sorted(lv, key=lambda x: x[0])
    rows = [(_amount(r.get("px")), _f(r.get("qty"))) for r in md.get("bids") or []]
    lv2 = [(round(1.0 - p, 6), q) for p, q in rows if p is not None and q]
    return sorted(lv2, key=lambda x: x[0])


def us_ladder(event: dict[str, Any]) -> dict[str, tuple[str, float | None]]:
    """market slug -> (sportsMarketType, line) for the markets the checker may poll."""
    return {
        str(m["slug"]): (str(m["sportsMarketType"]), _f(m.get("line")))
        for m in event.get("markets") or []
        if m.get("sportsMarketType") in US_TYPES
    }


# ------------------------------------------------------------------ Polymarket international


def intl_quotes(event: dict[str, Any]) -> QuoteBook:
    """gamma ``/events?slug=`` event -> quotes (top of book from bestBid/bestAsk; no depth)."""
    book = QuoteBook()
    game = game_from_slug(str(event.get("slug", "")))
    if game is None:
        book.skipped[f"{PM_INTL}:bad_event_slug"] += 1
        return book
    name_to_team: dict[str, int] = {}
    for t in event.get("teams") or []:
        tid = team_id(str(t.get("abbreviation", "")))
        if tid is not None:
            name_to_team[str(t.get("name"))] = tid
    for m in event.get("markets") or []:
        mtype = m.get("sportsMarketType")
        if mtype not in ("moneyline", "spreads", "totals") or not m.get("acceptingOrders", False):
            continue
        ask, bid = _f(m.get("bestAsk")), _f(m.get("bestBid"))
        if ask is None or bid is None:
            book.skipped[f"{PM_INTL}:no_price"] += 1
            continue
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
        except ValueError:
            continue
        first = str(outcomes[0]) if outcomes else ""
        line = _f(m.get("line"))
        q = str(m.get("question", ""))
        # bestAsk/bestBid are for the FIRST outcome; the second outcome's ask is 1 - bestBid.
        asks = (ask, 1.0 - bid)
        for i, px in enumerate(asks):
            out_name = str(outcomes[i]) if i < len(outcomes) else ""
            prop_side: tuple[PropKey, bool] | None = None
            if mtype == "moneyline":
                t = name_to_team.get(out_name)
                ms = None if t is None else margin_side(game, t, 0.0, True)
                if ms is not None:
                    prop_side = (PropKey(game, "margin", 0.0), ms[1])
            elif mtype == "spreads" and line is not None:
                t = name_to_team.get(first)  # "Spread: 76ers (-3.5)": outcome 0 covers -line
                other = name_to_team.get(str(outcomes[1])) if len(outcomes) > 1 else None
                ms = None if t is None else margin_side(game, t, -abs(line), i == 0)
                if ms is not None and other is not None and is_half_point(ms[0]):
                    prop_side = (PropKey(game, "margin", ms[0]), ms[1])
            elif mtype == "totals" and line is not None and is_half_point(line):
                prop_side = (PropKey(game, "total", line), out_name == "Over")
            if prop_side is None:
                book.skipped[f"{PM_INTL}:unmatched_side"] += 1
                continue
            _put(
                book,
                prop_side[0],
                PM_INTL,
                prop_side[1],
                Ask(
                    PM_INTL,
                    px,
                    f"Polymarket INTL '{out_name}' on {q} (not tradable from the US)",
                    str(m.get("slug")),
                    [],
                    f"PolyINTL {m.get('slug')} '{out_name}'",
                ),
            )
    return book


def slug_for(game: GameKey) -> str:
    return f"nba-{game.slug_suffix()}"


def et_date(d: date) -> str:
    return d.isoformat()
