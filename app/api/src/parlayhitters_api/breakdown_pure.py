"""Pure helpers of the leg breakdown: counting rules, the Wilson interval, line grids, and how a
stored price is named and read. No store, no I/O."""

from __future__ import annotations

import math
import statistics
from typing import Any

from .breakdown_models import Side, State, Tally
from .forecast import devig_over
from .history import Row

Json = dict[str, Any]

SMALL_N = 10  # fewer prior games than this is a small sample (also in role_stats)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k hits in n games; None when there are no games."""
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def hit_state(value: float, line: float, side: Side) -> State:
    """Over means strictly above the line; a value exactly on a whole-number line is a push."""
    if value == line:
        return "push"
    if side == "over":
        return "hit" if value > line else "miss"
    return "hit" if value < line else "miss"


def tally(rows: list[Row], stat: str, line: float, side: Side) -> Tally:
    n = len(rows)
    states = [hit_state(r.value(stat), line, side) for r in rows]
    hits = states.count("hit")
    pushes = states.count("push")
    ci = wilson(hits, n)
    return Tally(
        n=n,
        hits=hits,
        misses=states.count("miss"),
        pushes=pushes,
        rate=None if n == 0 else hits / n,
        ci_lo=None if ci is None else ci[0],
        ci_hi=None if ci is None else ci[1],
        avg=None if n == 0 else sum(r.value(stat) for r in rows) / n,
        avg_minutes=None if n == 0 else sum(r.minutes for r in rows) / n,
        small=n < SMALL_N,
    )


def default_line(values: list[int]) -> float:
    """Starting line when the book posted none: just under the middle of the last ten games."""
    if not values:
        return 0.5
    return math.floor(statistics.median(values)) + 0.5


def half_grid(lo: float, hi: float) -> list[float]:
    start = math.floor(lo * 2) / 2
    n = int(round((hi - start) * 2))
    return [start + 0.5 * i for i in range(n + 1)]


BOOK_NAMES = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "espnbet": "ESPN Bet",
    "fanatics": "Fanatics",
    "kalshi": "Kalshi",
    "novig": "Novig",
    "polymarket": "Polymarket",
    "polymarket_us": "Polymarket US",
}


def book_name(key: str) -> str:
    return BOOK_NAMES.get(key, key.replace("_", " ").capitalize())


def _market_chance(entry: Json) -> tuple[float | None, str | None]:
    """The de-vigged over chance of one book's two-sided quote, and the book's name. Fixture entries
    name no book (DraftKings). A pair whose sides came from different books is not de-vigged."""
    ov, un = entry.get("over_price"), entry.get("under_price")
    ob, ub = entry.get("over_book"), entry.get("under_book")
    if ov is None or un is None or ob != ub:
        return None, None
    try:
        return devig_over(int(ov), int(un)), book_name(str(ob or "draftkings"))
    except ValueError:
        return None, None


def _price_say(entry: Json) -> str:
    """Who quoted the price on screen (fixture entries name no book: DraftKings)."""
    ob, ub = entry.get("over_book"), entry.get("under_book")
    if ob is None and ub is None:
        return "Posted price, DraftKings about an hour before tip."
    if ob is not None and ub is not None and ob != ub:
        return (
            f"Posted prices, over from {book_name(ob)} and under from {book_name(ub)}, "
            "captured before tip."
        )
    return f"Posted price from {book_name(str(ob or ub))}, captured before tip."
