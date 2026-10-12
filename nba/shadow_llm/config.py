"""``configs/llm_shadow.yaml`` over defaults. Tokens only: no prices live here."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "llm_shadow.yaml"


@dataclass(frozen=True)
class ShadowConfig:
    provider: str = "anthropic"  # anthropic | claude_code | stub (configs/llm_shadow.yaml sets it)
    model_id: str = "claude-opus-5-5"
    fallback_model_id: str = "claude-fable-5-1"
    max_tokens: int = 3000
    temperature: float = 0.0
    window_min: tuple[int, int] = (60, 70)
    planning_tokens_per_game: int = 8_500
    nightly_token_ceiling: int = 150_000  # hard: games beyond it are budget_skip (latest tip first)
    monthly_token_backstop: int = 2_500_000
    monthly_cap_usd: float | None = None  # REQUIRED for the real provider; None = refused
    input_token_cap: int = 8_000  # per call; the payload is trimmed to fit
    max_legs_per_game: int = 40
    transport_retries: int = 2  # only before tip - 45 min; schema violations are never retried
    retry_until_min: int = 45
    min_lead_min: int = 30  # a row is written only if made_at <= tip - 30 min
    max_wall_s: float = 180.0
    request_timeout_s: float = 90.0
    db_wait_s: float = 120.0
    claude_bin: str | None = None  # None: $NBA_CLAUDE_BIN, PATH, then the newest desktop-app binary
    claude_code_wall_s: float = 180.0  # claude_code: per-game cap; on cap the game is a logged skip
    claude_code_tick_wall_s: float = 300.0  # claude_code: stop starting games after this per tick
    max_searches_per_call: int = 3  # claude_code: a 4th WebSearch discards the call (prompt says 3)
    max_games_per_night: int = 5  # deterministic sample per ET slate; 0 = the whole slate
    sample_salt: str = (
        "nba-llm-shadow-2026-27-sample-v1"  # prereg A6 item 12; never changed after a result
    )
    facts_db: Path = ROOT / "data" / "facts" / "facts.duckdb"
    odds_db: Path = ROOT / "data" / "markets" / "market_asof.duckdb"
    shadow_db: Path = ROOT / "data" / "shadow" / "llm_shadow.duckdb"
    lock_dir: Path = ROOT / "data" / "ops" / "llm_shadow_lock"


def _p(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def load_config(path: Path | None = None) -> ShadowConfig:
    p = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        raw = loaded if isinstance(loaded, dict) else {}
    upd: dict[str, Any] = {}
    for k in ("provider", "model_id", "fallback_model_id", "sample_salt"):
        if k in raw:
            upd[k] = str(raw[k])
    if raw.get("claude_bin"):
        upd["claude_bin"] = str(raw["claude_bin"])
    for k in (
        "max_tokens",
        "planning_tokens_per_game",
        "nightly_token_ceiling",
        "monthly_token_backstop",
        "input_token_cap",
        "max_legs_per_game",
        "transport_retries",
        "retry_until_min",
        "min_lead_min",
        "max_searches_per_call",
        "max_games_per_night",
    ):
        if k in raw:
            upd[k] = int(raw[k])
    for k in (
        "temperature",
        "max_wall_s",
        "request_timeout_s",
        "db_wait_s",
        "claude_code_wall_s",
        "claude_code_tick_wall_s",
    ):
        if k in raw:
            upd[k] = float(raw[k])
    cap = raw.get("MONTHLY_CAP_USD", raw.get("monthly_cap_usd"))
    if cap is not None:
        upd["monthly_cap_usd"] = float(cap)
    if "window_min" in raw:
        lo, hi = raw["window_min"]
        upd["window_min"] = (int(lo), int(hi))
    for k in ("facts_db", "odds_db", "shadow_db", "lock_dir"):
        if k in raw:
            upd[k] = _p(raw[k])
    return replace(ShadowConfig(), **upd) if upd else ShadowConfig()
