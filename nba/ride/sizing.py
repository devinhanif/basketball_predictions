"""Contract sizing for a credit slice, and the fee. The fee model comes from configs/parlay.yaml
(verified config object, taker); nothing here hardcodes a rate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nba.papertrade.rules import plan_entry
from nba.parlay.config import FeeModel, load_config
from nba.parlay.ev import order_fee

PARLAY_CFG = Path(__file__).resolve().parents[2] / "configs" / "parlay.yaml"


def load_fee(path: Path = PARLAY_CFG) -> FeeModel:
    return load_config(path).fee


@dataclass(frozen=True)
class Slice:
    n_games_able: int
    slice_usd: float


def credit_slice(credit: float, committed: float, games_able: int) -> Slice:
    """Remaining credit split evenly across the games still able to qualify (this one included)."""
    n = max(1, games_able)
    return Slice(n, round(max(0.0, credit - committed) / n, 2))


@dataclass(frozen=True)
class Sized:
    contracts: int
    ask: float
    fee: float
    cost: float  # contracts * ask + fee
    payout_if_hit: float  # contracts * $1
    profit_if_hit: float
    expected_payout: float  # contracts * p
    ev: float  # expected_payout - cost
    fits: bool  # False when the cheapest order (1 contract) exceeds the slice


def size_order(
    slice_usd: float, ask: float, p_win: float, fee: FeeModel, series: str = "KXNBAGAME"
) -> Sized:
    if not 0.0 < ask < 1.0:
        raise ValueError("ask must be inside (0, 1)")
    n, f = plan_entry(ask, fee, slice_usd, series)
    cost = round(n * ask + f, 4)
    fits = cost <= slice_usd + 1e-9
    return Sized(
        n,
        ask,
        f,
        cost,
        float(n),
        round(n - cost, 4),
        round(n * p_win, 4),
        round(n * p_win - cost, 4),
        fits,
    )


def fee_for(price: float, contracts: int, fee: FeeModel, series: str = "KXNBAGAME") -> float:
    return order_fee(price, fee, contracts, series=series)
