"""Exit / cash-out warning: say so, loudly, when the bid is far below what the position is worth.

Devin cashed out a 6-leg combo for $0 on 2026-10-25 when every leg had hit (worth about $1 per
contract). A sale gives up the position at the BID, so a bid far below value is money handed to the
counterparty. This module renders text from numbers the caller supplies; it places nothing, reads no
credential and has no execution path (project non-negotiable 5).

Where "fair" comes from (never invented here):
  * a model / no-vig / settled-state benchmark the caller holds (``source`` names it), e.g. all legs
    of a combo already hit -> $1 per contract; or
  * the venue's own mid, but ONLY from a sane two-sided book (``venue_fair``): an empty side (bid 0,
    ask 1.00) or a spread wider than ``max_spread`` yields no fair at all.
Thresholds live in ``configs/jarvis.yaml`` (``exit_warning``); nothing is hardcoded here.
Cents are rendered whole so the text passes numguard raw.
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
    age_min: int | None
    text: str


@dataclass(frozen=True)
class ExitCheck:
    """The verdict and, always, why: ``reason`` is 'warn' or the first gate that stopped it."""

    warning: ExitWarning | None
    reason: str

    @property
    def text(self) -> str:
        if self.warning is not None:
            return self.warning.text
        return f"No warning: {REASONS.get(self.reason, self.reason)}. This checks the price only."


REASONS = {
    "disabled": "the exit warning is switched off in the config",
    "no_fair": "no fair value was given or could be read from a sane book",
    "fair_out_of_range": "fair must be between 0 and 100 cents",
    "below_min_fair": "fair is below the configured minimum, so a low bid is not news",
    "bid_near_fair": "the bid is not far below fair",
    "giveaway_small": "the amount a sale would give away is below the configured minimum",
    "wide_book": "the book is too wide or has an empty side, so there is no fair to compare with",
    "stale": "the price snapshot is too old to trust",
}


def clean_book(bid: float | None, ask: float | None) -> tuple[float | None, float | None]:
    """Kalshi's empty-side convention is bid 0.00 / ask 1.00: treat both as missing."""
    b = None if bid is None or bid <= 0.0 else bid
    a = None if ask is None or ask >= 1.0 else ask
    return b, a


def venue_fair(bid: float | None, ask: float | None, max_spread: float) -> float | None:
    """The venue mid, only from a two-sided book no wider than ``max_spread``; else None."""
    b, a = clean_book(bid, ask)
    if (
        b is None or a is None or a < b or a - b > max_spread + 1e-6
    ):  # prices are float32 in the store
        return None
    return (b + a) / 2.0


def _c(x: float) -> str:
    return f"{round(x * 100.0):.0f}c"


def exit_check(
    cfg: Any,
    bid: float | None,
    fair: float | None,
    contracts: float | None = None,
    *,
    source: str = "venue mid",
    age_min: float | None = None,
) -> ExitCheck:
    """DON'T SELL when ``bid`` (missing counts as 0) is below ``bid_fraction`` of ``fair``, fair is
    at least ``min_fair`` (and at most $1: anything above is clamped to 1.0), the snapshot (if
    any) is not older than ``max_age_min`` and, when contracts are known, the giveaway is at least
    ``min_giveaway_usd``. Always returns the reason."""
    s = cfg.exit_warning
    if not s.get("enabled", False):
        return ExitCheck(None, "disabled")
    if fair is None:
        return ExitCheck(None, "no_fair")
    if fair <= 0.0:
        return ExitCheck(None, "fair_out_of_range")
    fair = min(fair, 1.0)
    if fair < float(s["min_fair"]):
        return ExitCheck(None, "below_min_fair")
    if age_min is not None and age_min > float(s["max_age_min"]):
        return ExitCheck(None, "stale")
    eff = 0.0 if bid is None else bid
    if eff >= float(s["bid_fraction"]) * fair:
        return ExitCheck(None, "bid_near_fair")
    gives = None if contracts is None else float(contracts) * (fair - eff)
    if gives is not None and gives < float(s["min_giveaway_usd"]):
        return ExitCheck(None, "giveaway_small")
    shown = "no bid" if bid is None or bid <= 0.0 else f"bid {_c(bid)}"
    text = f"DON'T SELL AT THIS BID: {shown} vs fair {_c(fair)} ({source})"
    if gives is not None:
        text += f"; selling now gives away ~${gives:.2f} before fees"
    age = None if age_min is None else round(age_min)
    if age is not None:
        text += f" (Kalshi snapshot {age} min old)"
    text += "."
    return ExitCheck(ExitWarning(bid, fair, contracts, gives, source, age, text), "warn")


def exit_warning(
    cfg: Any,
    bid: float | None,
    fair: float | None,
    contracts: float | None = None,
    *,
    source: str = "venue mid",
    age_min: float | None = None,
) -> ExitWarning | None:
    return exit_check(cfg, bid, fair, contracts, source=source, age_min=age_min).warning


def no_bid_line(ask: float, age_min: float | None, label: str) -> str:
    """No bid is posted but an ask is: a sale would get $0. No fair is claimed."""
    tail = "" if age_min is None else f" (Kalshi snapshot {round(age_min)} min old)"
    return f"{label}: NO BID POSTED (ask {_c(ask)}); selling now would get $0{tail}."


def allowed_numbers(w: ExitWarning) -> list[float]:
    """Every number the warning text may contain (for the numguard post-check)."""
    out = [w.fair, 0.0 if w.bid is None else w.bid]
    if w.contracts is not None:
        out.append(float(w.contracts))
    if w.gives_away_usd is not None:
        out.append(w.gives_away_usd)
    if w.age_min is not None:
        out.append(float(w.age_min))
    return out


__all__ = [
    "REASONS",
    "ExitCheck",
    "ExitWarning",
    "allowed_numbers",
    "clean_book",
    "exit_check",
    "exit_warning",
    "no_bid_line",
    "venue_fair",
]
