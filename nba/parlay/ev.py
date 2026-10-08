"""EV engine: fees from config, ask-based cost, interval, market-as-prior, capped Kelly.

Informational only. No order placement exists anywhere in this package.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from nba.parlay.config import FeeModel, ParlayConfig
from nba.parlay.legs import Leg

NO_POSITIVE_EV = "no_positive_ev_found"
POSITIVE_EV = "positive_ev_at_conservative_bound"


def fee_per_contract(price: float, fee: FeeModel, contracts: int = 1) -> float:
    """Total fee in dollars for ``contracts`` bought at ``price`` (dollars, 0..1).

    Rounded UP to the cent on the whole order, as in the published formula shape.
    """
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be in [0, 1]")
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    if fee.formula == "p_one_minus_p":
        raw = fee.coefficient * contracts * price * (1.0 - price)
    elif fee.formula == "flat":
        raw = fee.coefficient * contracts
    else:
        raise ValueError(f"unknown fee formula {fee.formula!r}")
    return math.ceil(round(raw * 100.0, 9)) / 100.0 / contracts if raw > 0 else 0.0


def ev_per_contract(prob: float, ask: float, fee: FeeModel) -> float:
    """EV of buying 1 YES contract at ``ask``: prob*1 - ask - fee (payout $1 if it hits)."""
    return prob - ask - fee_per_contract(ask, fee)


def shrink_to_market(
    model_prob: float, market_prob: float, n_settled: int, prior_k: float, skill: float
) -> tuple[float, float]:
    """Market-as-prior. Weight on model = n/(n+k), zero unless out-of-sample ``skill`` > 0.

    ``skill`` = 1 - Brier_model / Brier_market on settled history (so > 0 means model
    beat the market). Returns (shrunk_prob, model_weight).
    """
    w = 0.0 if skill <= 0.0 or n_settled <= 0 else n_settled / (n_settled + prior_k)
    return w * model_prob + (1.0 - w) * market_prob, w


def prob_interval(
    hits: int, n_sims: int, alpha: float, logit_sd: float, n_draws: int, seed: int
) -> tuple[float, float]:
    """Posterior interval: Beta over MC hits, widened by logit-scale calibration noise."""
    rng = np.random.default_rng(seed)
    p = rng.beta(hits + 1.0, n_sims - hits + 1.0, size=n_draws)
    p = np.clip(p, 1e-9, 1 - 1e-9)
    logit = np.log(p / (1 - p)) + rng.normal(0.0, logit_sd, size=n_draws)
    p = 1.0 / (1.0 + np.exp(-logit))
    return float(np.quantile(p, alpha / 2)), float(np.quantile(p, 1 - alpha / 2))


def kelly_fraction(prob: float, cost: float, frac: float, cap: float) -> float:
    """Capped fractional Kelly for a $1-payout contract costing ``cost`` (fees included)."""
    if cost >= 1.0 or prob <= cost:
        return 0.0
    full = (prob - cost) / (1.0 - cost)
    return float(min(max(frac * full, 0.0), cap))


@dataclass(frozen=True)
class Recommendation:
    legs: list[dict[str, Any]]
    model_prob: float
    prob_interval: tuple[float, float]
    price: float
    fees: float
    ev: float
    ev_low: float
    verdict: str
    engine: str = ""
    raw_model_prob: float = 0.0
    model_weight: float = 1.0
    spread: float | None = None
    kelly: float = 0.0
    n_settled_track: int = 0
    note: str = "Informational only; not financial advice. Estimated edges are noisy."

    def to_dict(self) -> dict[str, Any]:
        return {
            "legs": self.legs,
            "model_prob": self.model_prob,
            "prob_interval": list(self.prob_interval),
            "price": self.price,
            "fees": self.fees,
            "ev": self.ev,
            "ev_low": self.ev_low,
            "verdict": self.verdict,
            "engine": self.engine,
            "raw_model_prob": self.raw_model_prob,
            "model_weight": self.model_weight,
            "spread": self.spread,
            "kelly": self.kelly,
            "n_settled_track": self.n_settled_track,
            "note": self.note,
        }


def evaluate(
    legs: Sequence[Leg],
    joint_prob: float,
    hits: int,
    n_sims: int,
    ask: float,
    cfg: ParlayConfig,
    *,
    bid: float | None = None,
    engine: str = "",
    n_settled: int = 0,
    skill: float = 0.0,
    seed: int = 0,
) -> Recommendation:
    """Full contract. Cost uses the ASK (never the mid); spread reported if bid given.

    With a market prior, the market's mid (if bid known, else ask) is the prior and the
    model is shrunk toward it; with no track record (n_settled=0 or skill<=0) the
    market wins entirely, which makes a positive-EV verdict unreachable by construction.
    """
    fee = fee_per_contract(ask, cfg.fee)
    market = ask if bid is None else (ask + bid) / 2.0
    p_use, w = shrink_to_market(joint_prob, market, n_settled, cfg.prior_k, skill)
    lo, hi = prob_interval(
        hits, max(n_sims, 1), cfg.alpha, cfg.calibration_logit_sd, cfg.n_draws, seed
    )
    if n_sims == 0:  # closed-form engine: no MC noise, interval from calibration noise only
        lo, hi = prob_interval(
            int(round(joint_prob * 1000)),
            1000,
            cfg.alpha,
            cfg.calibration_logit_sd,
            cfg.n_draws,
            seed,
        )
    # Apply the same market shrinkage to the interval bounds.
    lo_s = w * lo + (1 - w) * market
    hi_s = w * hi + (1 - w) * market
    ev = p_use - ask - fee
    ev_low = lo_s - ask - fee
    verdict = POSITIVE_EV if ev_low > 0.0 else NO_POSITIVE_EV
    return Recommendation(
        legs=[leg.to_dict() for leg in legs],
        model_prob=p_use,
        prob_interval=(lo_s, hi_s),
        price=ask,
        fees=fee,
        ev=ev,
        ev_low=ev_low,
        verdict=verdict,
        engine=engine,
        raw_model_prob=joint_prob,
        model_weight=w,
        spread=None if bid is None else ask - bid,
        kelly=kelly_fraction(lo_s, ask + fee, cfg.kelly_fraction, cfg.kelly_cap),
        n_settled_track=n_settled,
    )
