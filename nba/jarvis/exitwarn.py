"""Exit / cash-out warning: say so, loudly, when the venue's bid is far below fair.

Devin cashed out a bet for $0 on 2026-10-25. A sale gives up the position at the BID, so a bid far
below what the position is worth is money handed to the counterparty. This module only renders a
line of text from numbers the caller supplies; it places nothing, reads no credential and has no
execution path (project non-negotiable 5).

The benchmark ("fair") is passed in, never invented here:
  * the venue's own live mid when both sides are posted (``venue_fair``), or the ask when no bid is
    posted at all (an upper bound, and the line says so);
  * or a model / no-vig benchmark the caller holds (``source`` names it).
Thresholds live in ``configs/jarvis.yaml`` (``exit_warning``); nothing is hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExitWarning:
    bid: float | None  # dollars per $1 contract, None = no bid posted
    fair: float
    contracts: float | None
    gives_away_usd: float | None  # gross, before any fee: contracts x (fair - bid)
    source: str
    text: str


def venue_fair(bid: float | None, ask: float | None) -> tuple[float, str] | None:
    """The venue's own benchmark for the side held: mid when both prices exist, else the ask."""
    if ask is None:
        return None
    if bid is None:
        return ask, "ask, no bid posted"
    return (bid + ask) / 2.0, "venue mid"


def _c(x: float) -> str:
    c = x * 100.0
    return f"{c:.0f}c" if abs(c - round(c)) < 0.05 else f"{c:.1f}c"


def exit_warning(
    cfg: Any,
    bid: float | None,
    fair: float | None,
    contracts: float | None = None,
    *,
    source: str = "venue mid",
) -> ExitWarning | None:
    """A warning when ``bid`` is below ``bid_fraction`` of ``fair`` (a missing bid counts as 0), the
    fair price is meaningful (>= ``min_fair``) and, when contracts are known, the amount given away
    is at least ``min_giveaway_usd``. Otherwise None (a bid near fair is not news)."""
    s = cfg.exit_warning
    if not s.get("enabled", True) or fair is None:
        return None
    if not 0.0 < fair < 1.0 or fair < float(s["min_fair"]):
        return None
    eff = 0.0 if bid is None else bid
    if eff >= float(s["bid_fraction"]) * fair:
        return None
    gives = None if contracts is None else float(contracts) * (fair - eff)
    if gives is not None and gives < float(s["min_giveaway_usd"]):
        return None
    shown = "no bid" if bid is None else f"bid {_c(bid)}"
    text = f"DON'T SELL: {shown} vs fair {_c(fair)} ({source})"
    if gives is not None:
        text += f"; selling now gives away ~${gives:.2f} before fees"
    text += ". A cash-out offer this low is not what the position is worth."
    return ExitWarning(bid, fair, contracts, gives, source, text)


def allowed_numbers(w: ExitWarning) -> list[float]:
    """Every number the warning text may contain (for the numguard post-check)."""
    out = [w.fair, 0.0 if w.bid is None else w.bid]
    if w.contracts is not None:
        out.append(float(w.contracts))
    if w.gives_away_usd is not None:
        out.append(w.gives_away_usd)
    return out


__all__ = ["ExitWarning", "allowed_numbers", "exit_warning", "venue_fair"]
