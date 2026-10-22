"""Paper-trader config (configs/papertrade.yaml); fees from the verified configs/parlay.yaml."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.config import FeeModel, load_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "papertrade.yaml"
PARLAY_PATH = ROOT / "configs" / "parlay.yaml"
FREEZE_PATH = ROOT / "configs" / "papertrade_freeze.json"
ALIAS_PATH = ROOT / "configs" / "kalshi_aliases.yaml"
PREREG_PATH = ROOT / "docs" / "prereg" / "PAPER_TRADER.md"
DB_PATH = ROOT / "data" / "papertrade" / "papertrade.duckdb"
LEGS_FILE = ROOT / "data" / "papertrade" / "open_legs.json"
SHAKEDOWN_SUFFIX = "-shakedown"


@dataclass(frozen=True)
class PaperConfig:
    rule_version: str
    freeze_date: date
    stake_usd: float
    slots: tuple[str, ...]
    shrink: float
    max_spread: float
    min_leg_ask: float
    max_leg_ask: float
    min_ev_per_dollar: float
    trade_win_legs: bool
    min_p_play: float
    max_forecast_age_h: float
    allowed_forecast_versions: tuple[str, ...]
    entry_lead_min: float
    max_book_slippage: float
    top_legs_for_parlays: int
    exit_margin: float
    exit_from_first_tip: bool
    look1_min_positions: int
    look1_min_dates: int
    look2_dates: int
    season_end_date: date
    alpha_l1: float
    alpha_l2: float
    concentration_cap: float
    bootstrap_reps: int
    bootstrap_seed: int
    prop_series: dict[str, str]
    fee: FeeModel

    @property
    def scored(self) -> bool:
        """A shakedown rule version is never scored and never needs the freeze digest."""
        return not self.rule_version.endswith(SHAKEDOWN_SUFFIX)

    def with_rule_version(self, rule_version: str) -> PaperConfig:
        return replace(self, rule_version=rule_version)

    def params(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "fee"}
        for k in ("slots", "allowed_forecast_versions"):
            d[k] = list(d[k])
        for k in ("freeze_date", "season_end_date"):
            d[k] = d[k].isoformat()
        d["fee_model"] = self.fee.to_json_dict()
        return d


def load(path: Path = CONFIG_PATH, parlay_path: Path = PARLAY_PATH) -> PaperConfig:
    raw = yaml.safe_load(path.read_text())
    fee = load_config(parlay_path).fee
    if not fee.verified:
        raise ValueError("fee model in configs/parlay.yaml is not marked verified; refusing to run")
    return PaperConfig(
        rule_version=str(raw["rule_version"]),
        freeze_date=raw["freeze_date"],
        stake_usd=float(raw["stake_usd"]),
        slots=tuple(str(s) for s in raw["slots"]),
        shrink=float(raw["shrink"]),
        max_spread=float(raw["max_spread"]),
        min_leg_ask=float(raw["min_leg_ask"]),
        max_leg_ask=float(raw["max_leg_ask"]),
        min_ev_per_dollar=float(raw["min_ev_per_dollar"]),
        trade_win_legs=bool(raw["trade_win_legs"]),
        min_p_play=float(raw["min_p_play"]),
        max_forecast_age_h=float(raw["max_forecast_age_h"]),
        allowed_forecast_versions=tuple(str(v) for v in raw["allowed_forecast_versions"]),
        entry_lead_min=float(raw["entry_lead_min"]),
        max_book_slippage=float(raw["max_book_slippage"]),
        top_legs_for_parlays=int(raw["top_legs_for_parlays"]),
        exit_margin=float(raw["exit_margin"]),
        exit_from_first_tip=bool(raw["exit_from_first_tip"]),
        look1_min_positions=int(raw["look1_min_positions"]),
        look1_min_dates=int(raw["look1_min_dates"]),
        look2_dates=int(raw["look2_dates"]),
        season_end_date=raw["season_end_date"],
        alpha_l1=float(raw["alpha_l1"]),
        alpha_l2=float(raw["alpha_l2"]),
        concentration_cap=float(raw["concentration_cap"]),
        bootstrap_reps=int(raw["bootstrap_reps"]),
        bootstrap_seed=int(raw["bootstrap_seed"]),
        prop_series={str(k): str(v) for k, v in raw["prop_series"].items()},
        fee=fee,
    )
