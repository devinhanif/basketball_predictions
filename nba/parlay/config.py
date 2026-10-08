"""Typed loader for configs/parlay.yaml. No default rates live in code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "configs" / "parlay.yaml"


@dataclass(frozen=True)
class FeeModel:
    """Fee schedule. Rates come from configs/parlay.yaml; none are defaults in code."""

    name: str
    formula: str
    coefficient: float  # taker
    verified: bool = False
    maker_coefficient: float | None = None
    default_multiplier: float = 1.0
    multipliers: tuple[tuple[str, float], ...] = ()  # (series prefix, M)
    source: str = ""

    def multiplier(self, series: str = "") -> float:
        """Per-market fee multiplier M: longest matching series prefix, else the default."""
        best = ""
        m = self.default_multiplier
        for prefix, val in self.multipliers:
            if series.startswith(prefix) and len(prefix) > len(best):
                best, m = prefix, val
        return m

    def rate(self, order_type: str = "taker") -> float:
        if order_type == "taker":
            return self.coefficient
        if order_type == "maker":
            if self.maker_coefficient is None:
                raise ValueError("fee model has no maker_coefficient")
            return self.maker_coefficient
        raise ValueError(f"order_type must be taker|maker, got {order_type!r}")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "formula": self.formula,
            "coefficient": self.coefficient,
            "maker_coefficient": self.maker_coefficient,
            "default_multiplier": self.default_multiplier,
            "multipliers": [list(x) for x in self.multipliers],
            "verified": self.verified,
            "source": self.source,
        }

    @staticmethod
    def from_json_dict(d: dict[str, Any]) -> FeeModel:
        mk = d.get("maker_coefficient")
        return FeeModel(
            str(d["name"]),
            str(d["formula"]),
            float(d["coefficient"]),
            bool(d.get("verified", False)),
            None if mk is None else float(mk),
            float(d.get("default_multiplier", 1.0)),
            tuple((str(k), float(v)) for k, v in d.get("multipliers", [])),
            str(d.get("source", "")),
        )


@dataclass(frozen=True)
class ParlayConfig:
    fee: FeeModel
    engine: str
    n_sims: int
    t_df: float
    seed: int
    corr_shrink_k: float
    alpha: float
    calibration_logit_sd: float
    n_draws: int
    prior_k: float
    kelly_fraction: float
    kelly_cap: float
    min_settled: int
    order_type: str = "taker"


def load_config(path: str | Path = DEFAULT_PATH) -> ParlayConfig:
    raw = yaml.safe_load(Path(path).read_text())
    f, e, i = raw["fee_model"], raw["engine"], raw["interval"]
    return ParlayConfig(
        fee=FeeModel(
            f["name"],
            f["formula"],
            float(f["coefficient"]),
            bool(f.get("verified", False)),
            None if f.get("maker_coefficient") is None else float(f["maker_coefficient"]),
            float(f.get("default_multiplier", 1.0)),
            tuple((str(k), float(v)) for k, v in (f.get("fee_multiplier") or {}).items()),
            str(f.get("source", "")),
        ),
        order_type=str(f.get("default_order_type", "taker")),
        engine=str(e["default"]),
        n_sims=int(e["n_sims"]),
        t_df=float(e["t_df"]),
        seed=int(e["seed"]),
        corr_shrink_k=float(e["corr_shrink_k"]),
        alpha=float(i["alpha"]),
        calibration_logit_sd=float(i["calibration_logit_sd"]),
        n_draws=int(i["n_draws"]),
        prior_k=float(raw["market_prior"]["k"]),
        kelly_fraction=float(raw["sizing"]["kelly_fraction"]),
        kelly_cap=float(raw["sizing"]["kelly_cap"]),
        min_settled=int(raw["min_settled_for_claims"]),
    )
