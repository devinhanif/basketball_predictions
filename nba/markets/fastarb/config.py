"""Typed loader for configs/fastarb.yaml. No fee rate lives in code; unverified fees refuse."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.config import FeeModel
from nba.parlay.config import load_config as load_parlay_config

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = ROOT / "configs" / "fastarb.yaml"


class UnverifiedFeeError(RuntimeError):
    """A fee object used by the poller is not marked ``verified: true``."""


@dataclass(frozen=True)
class FeeSchedule:
    """``fee = coefficient * C * p * (1 - p)`` rounded once per order to $0.01 (banker's)."""

    name: str
    coefficient: float
    verified: bool
    source: str = ""

    def order_fee(self, price: float, contracts: int) -> float:
        raw = (
            Decimal(str(self.coefficient))
            * contracts
            * Decimal(str(price))
            * (1 - Decimal(str(price)))
        )
        return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

    def require_verified(self) -> None:
        if not self.verified:
            raise UnverifiedFeeError(f"fee object {self.name!r} is not verified: true; refusing")


@dataclass(frozen=True)
class FastArbConfig:
    raw: dict[str, Any]
    kalshi_fee: FeeModel
    us_fee: FeeSchedule
    intl_fee: FeeSchedule

    def section(self, name: str) -> dict[str, Any]:
        sec = self.raw[name]
        assert isinstance(sec, dict)
        return sec

    @property
    def min_gap(self) -> float:
        return float(self.raw["alert"]["min_gap"])

    @property
    def lag_gap(self) -> float:
        return float(self.raw["alert"]["lag_gap"])

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    def path(self, key: str) -> Path:
        return self.resolve(str(self.raw["paths"][key]))

    def require_verified_fees(self, *, intl: bool = False) -> None:
        """The poller's gate. Kalshi's object lives in configs/parlay.yaml (same verified flag)."""
        if not self.kalshi_fee.verified:
            raise UnverifiedFeeError(f"Kalshi fee object {self.kalshi_fee.name!r} is not verified")
        self.us_fee.require_verified()
        if intl:
            self.intl_fee.require_verified()


def _fee(d: dict[str, Any]) -> FeeSchedule:
    if d.get("formula") != "p_one_minus_p" or d.get("rounding") != "bankers_cent":
        raise ValueError(f"unsupported fee formula/rounding in {d.get('name')!r}")
    v = d.get("verified", False)
    if not isinstance(v, bool):
        raise ValueError("verified must be a YAML boolean")
    return FeeSchedule(str(d["name"]), float(d["coefficient"]), v, str(d.get("source", "")))


def load_config(path: Path = DEFAULT_PATH, parlay_path: Path | None = None) -> FastArbConfig:
    raw = yaml.safe_load(path.read_text())
    parlay = load_parlay_config(parlay_path) if parlay_path else load_parlay_config()
    return FastArbConfig(
        raw=raw,
        kalshi_fee=parlay.fee,
        us_fee=_fee(raw["polymarket_us"]["fee"]),
        intl_fee=_fee(raw["polymarket_intl"]["fee"]),
    )
