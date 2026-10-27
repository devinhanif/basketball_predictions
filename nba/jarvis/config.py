"""Typed loader for configs/jarvis.yaml. No rate, cap or threshold lives in code.

Secrets are never part of the config: only the NAMES of the environment variables are
(``delivery.discord_env_var``, ``delivery.env_var``); their values are read at send time and never
stored, logged or put in an error message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

from nba.parlay.config import FeeModel, ParlayConfig
from nba.parlay.config import load_config as load_parlay_config

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "configs" / "jarvis.yaml"
PARLAY_PATH = ROOT / "configs" / "parlay.yaml"

VENUES = ("kalshi", "polymarket")
LABELS = ("arbitrage", "strong", "thin", "none", "arb_candidate")
LADDER_ORDER = ("none", "thin", "strong", "arbitrage")  # must be strictly increasing in stake


@dataclass(frozen=True)
class JarvisConfig:
    bankroll: dict[str, float]
    balance_start: float
    balance_current: float
    balance_floor: float
    nightly_cap: float
    reserve_for_strong: float
    per_bet_cap: float
    per_label_cap: dict[str, float]
    min_bet: float
    ladder: dict[str, float]
    none_max_contracts: int
    kill_min_bets: int
    strong_cents: float
    arbitrage_min_cents: float
    max_quote_age_min: float
    max_alert_quote_age_min: float
    max_ref_age_min: float
    max_ref_lead_min: float
    min_minutes_to_tip: float
    confirmed_max_gap_s: float
    settlement_rules_verified: bool
    kalshi_prop_dnp_rule_verified: bool
    fees: dict[str, FeeModel]
    fee_rounding: dict[str, str]
    polymarket: dict[str, Any]
    sinks: tuple[str, ...]
    discord_env_var: str
    env_var: str
    dedupe_minutes: float
    max_attempts_per_key: int
    realert_edge_cents: float
    slate_lead_min: float
    max_chars_discord: int
    max_chars_imessage: int
    max_texts: int
    display_tz: str
    alert_labels: tuple[str, ...]
    alert_when_cap_used: bool
    recap_after_et_hour: int
    halftime_checkins: bool
    daily_dollar: dict[str, Any]
    model_weights: dict[str, Any]
    ingame: dict[str, Any]
    exit_warning: dict[str, Any]
    paths: dict[str, Path]
    llm: dict[str, Any]
    min_settled: int
    min_settled_dates: int
    root: Path = ROOT
    props_sides: tuple[str, ...] = ("yes",)  # player props: Over / Yes only (Devin, 2026-10-27)

    def fee(self, venue: str) -> FeeModel:
        return self.fees[venue]

    @property
    def max_chars(self) -> int:
        """Per-message limit of the strictest configured sink."""
        lim = [
            self.max_chars_discord if s == "discord" else self.max_chars_imessage
            for s in self.sinks
        ]
        return min(lim) if lim else self.max_chars_discord

    @property
    def paper_only(self) -> bool:
        """At or below the floor JARVIS is paper-only until Devin resets the balance."""
        return self.balance_current <= self.balance_floor


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects a duplicated mapping key (a second props key must not win)."""


def _no_dupes(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for k, _v in node.value:
        key = loader.construct_object(k, deep=True)
        if key in seen:
            raise ConstructorError(
                None, None, f"duplicate key {key!r} in jarvis config", k.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dupes)


def _props_sides(raw: dict[str, Any]) -> tuple[str, ...]:
    """sides.props: a list containing "yes" and nothing but "yes" (Devin 2026-10-27: Over only)."""
    sides = raw.get("sides")
    if not isinstance(sides, dict) or "props" not in sides:
        return ("yes",)
    v = sides["props"]
    if not isinstance(v, list) or not v:
        raise ValueError(f"sides.props must be a non-empty list, got {v!r}")
    out = tuple(_side(x) for x in v)
    if "yes" not in out or "no" in out or set(out) != {"yes"}:
        raise ValueError(f"sides.props must be exactly [yes] (props are Over / Yes only): {out}")
    return out


def _side(x: object) -> str:
    """YAML reads a bare yes/no as a boolean; accept both spellings."""
    return ("yes" if x else "no") if isinstance(x, bool) else str(x)


def _path(root: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else root / q


def load_config(
    path: str | Path = DEFAULT_PATH,
    parlay_path: str | Path = PARLAY_PATH,
    *,
    root: Path = ROOT,
) -> JarvisConfig:
    raw = yaml.load(Path(path).read_text(), Loader=_StrictLoader)  # noqa: S506 (SafeLoader subclass)
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
    dl, bal, st = raw["delivery"], raw["balance"], raw["settlement"]
    cfg = JarvisConfig(
        bankroll={k: float(v) for k, v in raw["bankroll_usd"].items()},
        balance_start=float(bal["start_usd"]),
        balance_current=float(bal["current_usd"]),
        balance_floor=float(bal["floor_usd"]),
        nightly_cap=float(caps["nightly_usd"]),
        reserve_for_strong=float(caps["reserve_for_strong_usd"]),
        per_bet_cap=float(caps["per_bet_usd"]),
        per_label_cap={k: float(v) for k, v in caps["per_label_usd"].items()},
        min_bet=float(caps["min_bet_usd"]),
        ladder={k: float(v) for k, v in sz["ladder_usd"].items()},
        none_max_contracts=int(sz["none_max_contracts"]),
        kill_min_bets=int(raw["kill_switch"]["min_bets"]),
        strong_cents=float(lab["strong_cents"]),
        arbitrage_min_cents=float(lab["arbitrage_min_cents"]),
        max_quote_age_min=float(fr["max_quote_age_min"]),
        max_alert_quote_age_min=float(fr["max_alert_quote_age_min"]),
        max_ref_age_min=float(fr["max_ref_age_min"]),
        max_ref_lead_min=float(fr["max_ref_lead_min"]),
        min_minutes_to_tip=float(fr["min_minutes_to_tip"]),
        confirmed_max_gap_s=float(fr["confirmed_max_gap_s"]),
        settlement_rules_verified=bool(st["settlement_rules_verified"]) is True,
        kalshi_prop_dnp_rule_verified=bool(st["kalshi_prop_dnp_rule_verified"]) is True,
        fees={"kalshi": pcfg.fee, "polymarket": pm_fee},
        fee_rounding={"kalshi": "ceil_cent", "polymarket": str(pm.get("rounding", "ceil_cent"))},
        polymarket=dict(raw["polymarket"]),
        sinks=tuple(str(x) for x in dl["sinks"]),
        discord_env_var=str(dl["discord_env_var"]),
        env_var=str(dl["env_var"]),
        dedupe_minutes=float(dl["dedupe_minutes"]),
        max_attempts_per_key=int(dl["max_attempts_per_key"]),
        realert_edge_cents=float(dl["realert_edge_cents"]),
        slate_lead_min=float(dl["slate_lead_min"]),
        max_chars_discord=int(dl["max_chars_discord"]),
        max_chars_imessage=int(dl["max_chars_imessage"]),
        max_texts=int(dl["max_texts"]),
        display_tz=str(dl["display_tz"]),
        alert_labels=tuple(str(x) for x in dl["alert_labels"]),
        alert_when_cap_used=bool(dl.get("alert_when_cap_used", True)),
        recap_after_et_hour=int(dl["recap_after_et_hour"]),
        halftime_checkins=bool(dl["halftime_checkins"]),
        daily_dollar=dict(raw["daily_dollar"]),
        model_weights=dict(raw["model_weights"]),
        ingame=dict(raw["ingame"]),
        exit_warning=dict(raw.get("exit_warning", {"enabled": False})),
        paths={k: _path(root, str(v)) for k, v in raw["paths"].items()},
        llm=dict(raw["llm"]),
        min_settled=pcfg.min_settled,
        min_settled_dates=pcfg.min_settled_dates,
        root=root,
        props_sides=_props_sides(raw),
    )
    missing = [v for v in VENUES if v not in cfg.bankroll or v not in cfg.fees]
    if missing:
        raise ValueError(f"jarvis config lacks bankroll/fee for venues {missing}")
    if set(cfg.per_label_cap) != set(LABELS) or set(cfg.ladder) != set(LABELS):
        raise ValueError(f"caps.per_label_usd and sizing.ladder_usd must have exactly {LABELS}")
    steps = [cfg.ladder[k] for k in LADDER_ORDER]
    if any(a >= b for a, b in zip(steps, steps[1:], strict=False)):
        raise ValueError(f"stake ladder must increase strictly over {LADDER_ORDER}: {steps}")
    mw = cfg.model_weights
    if not 0.0 <= float(mw["w_max"]) <= 0.5 or float(mw["step"]) <= 0:
        raise ValueError("model_weights.w_max must be in [0, 0.5] and step > 0")
    ew = cfg.exit_warning
    if ew.get("enabled", False):
        if not 0.0 < float(ew["bid_fraction"]) <= 1.0 or not 0.0 < float(ew["min_fair"]) < 1.0:
            raise ValueError("exit_warning.bid_fraction must be in (0, 1] and min_fair in (0, 1)")
        if float(ew["min_giveaway_usd"]) < 0 or not 0.0 < float(ew["max_spread"]) < 1.0:
            raise ValueError("exit_warning.min_giveaway_usd >= 0 and max_spread in (0, 1)")
        if float(ew["max_age_min"]) <= 0:
            raise ValueError("exit_warning.max_age_min must be > 0")
    if cfg.reserve_for_strong >= cfg.nightly_cap:
        raise ValueError("caps.reserve_for_strong_usd must be below caps.nightly_usd")
    return cfg
