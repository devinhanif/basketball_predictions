"""The fee, read through ``nba.parlay.config.FeeModel`` and checked against the pinned values.

Both rules pin: taker coefficient 0.07, formula ``p_one_minus_p``, multiplier M = 1.0 for every
series in
their universe, ``verified: true`` (LONGSHOT Section 3, UNDERS Section 5). The scorer REFUSES to run
if
the loaded config gives a different fee for any series in the universe. The rule's one exception (a
Kalshi schedule change recorded in ``configs/parlay.yaml`` with a source URL and an effective date
before
the read date) has no schema in the config today, so it is NOT implemented: this module fails closed
on
any difference (listed as an ambiguity in the hand-off).

Amounts are kept in integer micro-dollars (1e-6 dollars): with coefficient 7/100 and P = c/100 the
unrounded fee ``0.07 * P * (1 - P)`` is exactly ``7 * c * (100 - c)`` micro-dollars, an integer, so
the
Section 4 identity can be asserted with exact integers (finer than the rule's "hundredths of a
cent").
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import yaml

from nba.parlay.config import DEFAULT_PATH, FeeModel, load_config

PINNED_COEFFICIENT = Fraction(7, 100)
PINNED_FORMULA = "p_one_minus_p"
PINNED_MULTIPLIER = Fraction(1)
UNIT = 1_000_000  # micro-dollars per dollar
CENT = 10_000  # micro-dollars per cent


class FeeRefused(RuntimeError):
    """The fee config differs from the pinned one (or is unverified): the scorer does not run."""


def _frac(x: float) -> Fraction:
    # repr(0.07) == '0.07', so this is exactly 7/100 for the pinned value and not a binary float.
    return Fraction(repr(float(x)))


@dataclass(frozen=True)
class PinnedFee:
    """A FeeModel that passed the pin check, with its provenance for the report."""

    model: FeeModel
    series: tuple[str, ...]

    @property
    def coefficient(self) -> Fraction:
        return _frac(self.model.rate("taker"))

    def multiplier(self, series: str) -> Fraction:
        return _frac(self.model.multiplier(series))

    def fee_micro(self, series: str, price_c: int) -> int:
        """Unrounded fee for one contract at ``price_c`` cents, exact integer micro-dollars."""
        p_micro_sq = price_c * (100 - price_c) * 100  # P(1-P) in micro-dollars
        val = self.coefficient * self.multiplier(series) * p_micro_sq
        if val.denominator != 1:
            raise FeeRefused(
                f"fee for {series} at {price_c}c is not an integer micro-dollar: {val}"
            )
        return int(val)

    def fee_micro_rounded_up(self, series: str, price_c: int) -> int:
        """Sensitivity S1: the fee rounded UP to a whole cent for a single contract."""
        f = self.fee_micro(series, price_c)
        return -(-f // CENT) * CENT

    def describe(self) -> str:
        m = self.model
        return (
            f"fee_model={m.name} source={m.source!r} verified={m.verified} "
            f"formula={m.formula} taker_coefficient={m.rate('taker')} "
            f"default_multiplier={m.default_multiplier} multipliers={dict(m.multipliers)}"
        )


def load_pinned_fee(series: tuple[str, ...], path: str | Path = DEFAULT_PATH) -> PinnedFee:
    """Load ``fee_model`` through FeeModel and refuse unless it equals the pinned schedule."""
    # m1: nba/parlay/config.py coerces a string "false" to True (live path; noted for Devin, not
    # edited). The scorer therefore re-reads the raw YAML and requires a real boolean ``true``.
    raw_verified = (
        (yaml.safe_load(Path(path).read_text()) or {}).get("fee_model", {}).get("verified")
    )
    if raw_verified is not True:
        raise FeeRefused(f"fee_model.verified must be the YAML boolean true, got {raw_verified!r}")
    model = load_config(path).fee
    if not model.verified:
        raise FeeRefused("fee_model.verified is not true")
    if model.formula != PINNED_FORMULA:
        raise FeeRefused(f"fee formula {model.formula!r} != pinned {PINNED_FORMULA!r}")
    if _frac(model.rate("taker")) != PINNED_COEFFICIENT:
        raise FeeRefused(f"taker coefficient {model.rate('taker')} != pinned 0.07")
    if _frac(model.default_multiplier) != PINNED_MULTIPLIER:
        raise FeeRefused(f"default_multiplier {model.default_multiplier} != pinned 1.0")
    for s in series:
        if _frac(model.multiplier(s)) != PINNED_MULTIPLIER:
            raise FeeRefused(f"fee multiplier for {s} is {model.multiplier(s)}, pinned 1.0")
    return PinnedFee(model, series)
