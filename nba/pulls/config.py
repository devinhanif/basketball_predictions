"""Typed loader for ``configs/pulls.yaml``. No threshold lives in code."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "pulls.yaml"
HEARTBEAT_NAME = "pulls"


@dataclass(frozen=True)
class PullsConfig:
    enabled: bool = True
    requests_db: Path = ROOT / "data" / "ops" / "pulls.duckdb"
    pinnacle_alert_max_age_min: float = 30.0
    request_need_by_min: float = 15.0
    cooldown_min: float = 10.0
    close_slots_reserved: int = 1
    ondemand_max_per_game: int = 4
    ondemand_daily_credit_cap: int = 20000
    interval_s: int = 120
    store_retries: int = 3
    store_wait_s: float = 2.0
    wd_window_h: float = 3.0
    wd_warn_pinnacle_age_min: float = 60.0
    wd_warn_grace_min: float = 5.0
    wd_heartbeat_max_age_min: float = 10.0

    @property
    def ops_dir(self) -> Path:
        """``data/ops`` (the requests db lives there; heartbeats are under ``heartbeat/``)."""
        return self.requests_db.parent


def _p(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def load_pulls_config(path: Path | None = None) -> PullsConfig:
    """``configs/pulls.yaml`` over the defaults; a missing file means the defaults."""
    p = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        raw = loaded if isinstance(loaded, dict) else {}
    cfg = PullsConfig()
    upd: dict[str, Any] = {}
    if "enabled" in raw:
        upd["enabled"] = bool(raw["enabled"]) is True
    if (raw.get("paths") or {}).get("requests_db"):
        upd["requests_db"] = _p(raw["paths"]["requests_db"])
    fr, bu, tk, wd = (raw.get(k) or {} for k in ("freshness", "budget", "tick", "watchdog"))
    for key, src, cast in (
        ("pinnacle_alert_max_age_min", fr, float),
        ("request_need_by_min", fr, float),
        ("cooldown_min", fr, float),
        ("close_slots_reserved", bu, int),
        ("ondemand_max_per_game", bu, int),
        ("ondemand_daily_credit_cap", bu, int),
        ("interval_s", tk, int),
        ("store_retries", tk, int),
        ("store_wait_s", tk, float),
    ):
        if key in src:
            upd[key] = cast(src[key])
    for key, name, cast in (
        ("wd_window_h", "window_h", float),
        ("wd_warn_pinnacle_age_min", "warn_pinnacle_age_min", float),
        ("wd_warn_grace_min", "warn_grace_min", float),
        ("wd_heartbeat_max_age_min", "heartbeat_max_age_min", float),
    ):
        if name in wd:
            upd[key] = cast(wd[name])
    out = replace(cfg, **upd) if upd else cfg
    if out.pinnacle_alert_max_age_min <= 0 or out.request_need_by_min <= 0:
        raise ValueError("pulls: pinnacle_alert_max_age_min and request_need_by_min must be > 0")
    if out.close_slots_reserved < 0 or out.ondemand_max_per_game < 0:
        raise ValueError("pulls: close_slots_reserved and ondemand_max_per_game must be >= 0")
    return out
