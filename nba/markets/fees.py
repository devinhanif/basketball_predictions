"""Venue fee formulas, each held once. Pure: standard library only, no I/O, no config reading.

Rates, multipliers and formulas always come from a verified config object
(``configs/parlay.yaml`` ``fee_model``, ``configs/fastarb.yaml`` ``polymarket_us.fee``,
``configs/jarvis.yaml`` ``fees.polymarket``) through the callers' thin wrappers; no rate lives here.

Three DISTINCT formulas exist in the tree and are kept apart on purpose, bit for bit:

* ``ceil_cent_fee``: Kalshi. Binary floats, ``M * rate * C * P * (1 - P)`` evaluated left to
  right, rounded UP once per order to the cent through ``ceil(round(raw * 100, 9)) / 100``; a raw
  fee <= 0 returns 0.0. Also carries the ``flat`` formula (``M * rate * C``). Wrapped by
  ``nba.parlay.ev.order_fee`` (which validates the price and contracts and picks the
  maker/taker rate).
* ``bankers_cent_fee``: Polymarket US as the fast-arb poller computes it
  (``nba.markets.fastarb.config.FeeSchedule.order_fee``). Decimal of ``str(float)``,
  ``coefficient * C * P * (1 - P)`` left to right, quantized to $0.01 half-even. No multiplier, no
  formula switch, no input validation (a price outside [0, 1] yields a signed result).
* ``bankers_cent_fee_scaled``: Polymarket US as JARVIS computes it
  (``nba.jarvis.edge._order_fee`` when ``fee_rounding`` is ``bankers_cent``). Same rounding as
  ``bankers_cent_fee`` but a different evaluation: ``(M * rate * C) * (P * (1 - P))`` with the
  multiplier ``M`` applied, and the formula field ignored (always ``p_one_minus_p``).

``bankers_cent_fee`` and ``bankers_cent_fee_scaled`` price the same venue with the same config
coefficient (0.0695 in both YAMLs) but multiply in a different order under Decimal's 28-digit
context, so a double rounding can in principle land on the other side of a half cent. Since
2026-11-16 (Devin: fast-arb's order) JARVIS calls ``bankers_cent_fee`` whenever its multiplier is
1, which is every Polymarket config today; ``bankers_cent_fee_scaled`` remains only for a
multiplier other than 1. The two agreed on 159,240 of 159,240 tested inputs before the switch.

Informational only. Nothing here sends, signs or submits anything.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")

__all__ = ["CENT", "bankers_cent_fee", "bankers_cent_fee_scaled", "ceil_cent_fee"]


def ceil_cent_fee(
    formula: str, multiplier: float, rate: float, contracts: int, price: float
) -> float:
    """Kalshi per-order fee in dollars: ``ceil_to_cent(M * rate * C * P * (1 - P))`` (or ``flat``:
    ``ceil_to_cent(M * rate * C)``), rounded up once per order. A raw fee <= 0 is 0.0."""
    if formula == "p_one_minus_p":
        raw = multiplier * rate * contracts * price * (1.0 - price)
    elif formula == "flat":
        raw = multiplier * rate * contracts
    else:
        raise ValueError(f"unknown fee formula {formula!r}")
    return math.ceil(round(raw * 100.0, 9)) / 100.0 if raw > 0 else 0.0


def bankers_cent_fee(coefficient: float, contracts: int, price: float) -> float:
    """Polymarket US per-order fee (fast-arb form): ``coefficient * C * p * (1 - p)`` in Decimal,
    rounded once per order to $0.01, half-even (banker's)."""
    raw = Decimal(str(coefficient)) * contracts * Decimal(str(price)) * (1 - Decimal(str(price)))
    return float(raw.quantize(CENT, rounding=ROUND_HALF_EVEN))


def bankers_cent_fee_scaled(multiplier: float, rate: float, contracts: int, price: float) -> float:
    """Polymarket US per-order fee (JARVIS form): ``(M * rate * C) * (p * (1 - p))`` in Decimal,
    rounded once per order to $0.01, half-even (banker's)."""
    raw = Decimal(str(multiplier)) * Decimal(str(rate)) * contracts
    raw *= Decimal(str(price)) * (Decimal(1) - Decimal(str(price)))
    return float(raw.quantize(CENT, rounding=ROUND_HALF_EVEN))
