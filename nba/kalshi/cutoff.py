"""Live-vs-historical tier selection (CLAUDE.md: "call the cutoff endpoint
to know which tier to use").

Kalshi's public API splits market data into two tiers: live endpoints
(``/markets``, ``/markets/{ticker}/candlesticks``) that only serve recent,
not-yet-fully-archived data, and ``/historical/*`` endpoints for markets
that settled before a rolling cutoff. ``GET /historical/cutoff`` (no auth)
returns that boundary; this module is the pure decision logic for "which
tier does a given market/time belong to", kept separate from the network
call (``nba.kalshi.client.KalshiClient.fetch_cutoff``) so it is trivially
unit-testable with no network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CutoffInfo:
    """Parsed ``GET /historical/cutoff`` response.

    Kalshi's real response carries several boundary timestamps (settled
    markets, filled trades, closed orders, settled positions); this
    ingestor only needs the settled-markets boundary, which is what
    determines whether a market's candlesticks live on the live or
    historical tier.
    """

    settled_markets_cutoff: datetime


def parse_cutoff_response(raw: dict[str, object]) -> CutoffInfo:
    """Parse the raw ``GET /historical/cutoff`` JSON body.

    Accepts either ``settled_markets_cutoff`` (documented field name) or a
    bare ``cutoff`` key (tolerated for simpler synthetic fixtures); raises
    ``KeyError`` loudly if neither is present rather than defaulting to
    "treat everything as live", which would silently route stale historical
    reads at the live endpoint (which CLAUDE.md flags as a real failure
    mode: "past the boundary the live endpoints hand back HTTP 200 and an
    empty array rather than an error").
    """
    value = raw.get("settled_markets_cutoff", raw.get("cutoff"))
    if value is None:
        raise KeyError(
            f"cutoff response missing 'settled_markets_cutoff' (or 'cutoff') field: {raw!r}"
        )
    assert isinstance(value, str)
    return CutoffInfo(settled_markets_cutoff=datetime.fromisoformat(value))


def select_tier(target_ts: datetime, cutoff: CutoffInfo) -> str:
    """Return ``'historical'`` if ``target_ts`` is before the settled-markets
    cutoff, else ``'live'``.

    A market/candlestick timestamp strictly before the cutoff is guaranteed
    archived (query ``/historical/*``); at or after, query the live
    endpoints. Ties go to ``'live'`` (the more conservative choice: a
    not-yet-archived market queried against ``/historical/*`` returns
    nothing, whereas querying ``/live`` for an already-archived market may
    still succeed during the rollover window).
    """
    return "historical" if target_ts < cutoff.settled_markets_cutoff else "live"
