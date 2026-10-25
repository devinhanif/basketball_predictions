"""Typed loader for configs/jarvis.yaml. No rate, cap or threshold lives in code.

The iMessage recipient is deliberately NOT part of the config: only the NAME of the environment
variable is (``delivery.env_var``); its value is read at send time and never stored or logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.config import FeeModel, ParlayConfig
from nba.parlay.config import load_config as load_parlay_config

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "configs" / "jarvis.yaml"
PARLAY_PATH = ROOT / "configs" / "parlay.yaml"

VENUES = ("kalshi", "polymarket")
LABELS = ("arbitrage", "strong", "thin", "none")


@dataclass(frozen=True)
class JarvisConfig:
    bankroll: dict[str, float]
    nightly_cap: float
    per_bet_cap: float
    per_label_cap: dict[str, float]
    min_bet: float
    kelly_fraction: float
    none_stake: dict[str, float]
    none_near_cents: float
    none_mid_cents: float
    strong_cents: float
    arbitrage_min_cents: float
    max_quote_age_min: float
    max_ref_age_min: float
    min_minutes_to_tip: float
    fees: dict[str, FeeModel]
    fee_rounding: dict[str, str]
    polymarket: dict[str, Any]
    dedupe_minutes: float
    slate_lead_min: float
    max_chars: int
    max_texts: int
    display_tz: str
    alert_labels: tuple[str, ...]
    alert_when_cap_used: bool
    env_var: str
    paths: dict[str, Path]
    llm: dict[str, Any]
    min_settled: int
    min_settled_dates: int
    root: Path = ROOT

    def fee(self, venue: str) -> FeeModel:
        return self.fees[venue]


def _path(root: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else root / q


def load_config(
    path: str | Path = DEFAULT_PATH,
    parlay_path: str | Path = PARLAY_PATH,
    *,
    root: Path = ROOT,
) -> JarvisConfig:
    raw = yaml.safe_load(Path(path).read_text())
    pcfg: ParlayConfig = load_parlay_config(parlay_path)
    pm = raw["fees"]["polymarket"]
    pm_fee = FeeModel(
        str(pm["name"]),
        str(pm["formula"]),
        float(pm["coefficient"]),
        bool(pm.get("verified", False)) is True,
        None if pm.get("maker_coefficient") is None else float(pm["maker_coefficient"]),
        1.0,
        (),
        str(pm.get("source", "")),
    )
    caps, sz, lab, fr = raw["caps"], raw["sizing"], raw["labels"], raw["freshness"]
    dl = raw["delivery"]
    cfg = JarvisConfig(
        bankroll={k: float(v) for k, v in raw["bankroll_usd"].items()},
        nightly_cap=float(caps["nightly_usd"]),
        per_bet_cap=float(caps["per_bet_usd"]),
        per_label_cap={k: float(v) for k, v in caps["per_label_usd"].items()},
        min_bet=float(caps["min_bet_usd"]),
        kelly_fraction=float(sz["kelly_fraction"]),
        none_stake={k: float(v) for k, v in sz["none_stake_usd"].items()},
        none_near_cents=float(sz["none_near_cents"]),
        none_mid_cents=float(sz["none_mid_cents"]),
        strong_cents=float(lab["strong_cents"]),
        arbitrage_min_cents=float(lab["arbitrage_min_cents"]),
        max_quote_age_min=float(fr["max_quote_age_min"]),
        max_ref_age_min=float(fr["max_ref_age_min"]),
        min_minutes_to_tip=float(fr["min_minutes_to_tip"]),
        fees={"kalshi": pcfg.fee, "polymarket": pm_fee},
        fee_rounding={"kalshi": "ceil_cent", "polymarket": str(pm.get("rounding", "ceil_cent"))},
        polymarket=dict(raw["polymarket"]),
        dedupe_minutes=float(dl["dedupe_minutes"]),
        slate_lead_min=float(dl["slate_lead_min"]),
        max_chars=int(dl["max_chars"]),
        max_texts=int(dl["max_texts"]),
        display_tz=str(dl["display_tz"]),
        alert_labels=tuple(str(x) for x in dl["alert_labels"]),
        alert_when_cap_used=bool(dl.get("alert_when_cap_used", True)),
        env_var=str(dl["env_var"]),
        paths={k: _path(root, str(v)) for k, v in raw["paths"].items()},
        llm=dict(raw["llm"]),
        min_settled=pcfg.min_settled,
        min_settled_dates=pcfg.min_settled_dates,
        root=root,
    )
    missing = [v for v in VENUES if v not in cfg.bankroll or v not in cfg.fees]
    if missing:
        raise ValueError(f"jarvis config lacks bankroll/fee for venues {missing}")
    if set(cfg.per_label_cap) != set(LABELS):
        raise ValueError(f"caps.per_label_usd must have exactly {LABELS}")
    return cfg
