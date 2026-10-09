"""Assistant settings: CLI flag > env var > configs/parlay.yaml ``assistant:`` > default."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.config import DEFAULT_PATH

DEFAULT_LLM = "qwen2.5:3b"  # 3B-class: fits an 8 GB machine
DEFAULT_HOST = "http://localhost:11434"


@dataclass(frozen=True)
class AssistantConfig:
    model: str = DEFAULT_LLM
    host: str = DEFAULT_HOST
    seed: int = 20261008
    max_tool_steps: int = 6
    top_n: int = 5
    timeout_s: float = 300.0


def load_assistant_config(
    path: str | Path = DEFAULT_PATH, *, model: str | None = None, host: str | None = None
) -> AssistantConfig:
    raw: dict[str, Any] = {}
    p = Path(path)
    if p.exists():
        raw = (yaml.safe_load(p.read_text()) or {}).get("assistant") or {}
    base = AssistantConfig()
    return AssistantConfig(
        model=model
        or os.environ.get("PARLAY_ASSISTANT_MODEL")
        or str(raw.get("model", base.model)),
        host=host or os.environ.get("OLLAMA_HOST") or str(raw.get("host", base.host)),
        seed=int(raw.get("seed", base.seed)),
        max_tool_steps=int(raw.get("max_tool_steps", base.max_tool_steps)),
        top_n=int(raw.get("top_n", base.top_n)),
        timeout_s=float(raw.get("timeout_s", base.timeout_s)),
    )
