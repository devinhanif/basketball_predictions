"""Paper-trader config (configs/papertrade.yaml); fees from the verified configs/parlay.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.config import FeeModel, load_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "papertrade.yaml"
PARLAY_PATH = ROOT / "configs" / "parlay.yaml"
DB_PATH = ROOT / "data" / "papertrade" / "papertrade.duckdb"
LEGS_FILE = ROOT / "data" / "papertrade" / "open_legs.json"


@dataclass(frozen=True)
class PaperConfig:
    rule_version: str
    stake_usd: float
    slots: tuple[str, ...]
    shrink: float
    max_spread: float
    min_leg_ask: float
    max_leg_ask: float
    min_ev_per_dollar: float
    trade_win_legs: bool
    min_p_play: float
    entry_lead_min: float
    top_legs_for_parlays: int
    exit_margin: float
    exit_from_first_tip: bool
    min_slate_dates: int
    min_positions: int
    bootstrap_reps: int
    bootstrap_seed: int
    prop_series: dict[str, str]
    fee: FeeModel

    def params(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "fee"}
        d["slots"] = list(self.slots)
        d["fee_model"] = self.fee.to_json_dict()
        return d


def load(path: Path = CONFIG_PATH, parlay_path: Path = PARLAY_PATH) -> PaperConfig:
    raw = yaml.safe_load(path.read_text())
    fee = load_config(parlay_path).fee
    if not fee.verified:
        raise ValueError("fee model in configs/parlay.yaml is not marked verified; refusing to run")
    return PaperConfig(
        rule_version=str(raw["rule_version"]),
        stake_usd=float(raw["stake_usd"]),
        slots=tuple(str(s) for s in raw["slots"]),
        shrink=float(raw["shrink"]),
        max_spread=float(raw["max_spread"]),
        min_leg_ask=float(raw["min_leg_ask"]),
        max_leg_ask=float(raw["max_leg_ask"]),
        min_ev_per_dollar=float(raw["min_ev_per_dollar"]),
        trade_win_legs=bool(raw["trade_win_legs"]),
        min_p_play=float(raw["min_p_play"]),
        entry_lead_min=float(raw["entry_lead_min"]),
        top_legs_for_parlays=int(raw["top_legs_for_parlays"]),
        exit_margin=float(raw["exit_margin"]),
        exit_from_first_tip=bool(raw["exit_from_first_tip"]),
        min_slate_dates=int(raw["min_slate_dates"]),
        min_positions=int(raw["min_positions"]),
        bootstrap_reps=int(raw["bootstrap_reps"]),
        bootstrap_seed=int(raw["bootstrap_seed"]),
        prop_series={str(k): str(v) for k, v in raw["prop_series"].items()},
        fee=fee,
    )
