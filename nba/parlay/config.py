"""Typed loader for configs/parlay.yaml. No default rates live in code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "configs" / "parlay.yaml"


@dataclass(frozen=True)
class FeeModel:
    name: str
    formula: str
    coefficient: float
    verified: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "formula": self.formula,
            "coefficient": self.coefficient,
            "verified": self.verified,
        }


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


def load_config(path: str | Path = DEFAULT_PATH) -> ParlayConfig:
    raw = yaml.safe_load(Path(path).read_text())
    f, e, i = raw["fee_model"], raw["engine"], raw["interval"]
    return ParlayConfig(
        fee=FeeModel(
            f["name"], f["formula"], float(f["coefficient"]), bool(f.get("verified", False))
        ),
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
