"""Audit-metadata and tag helpers for the registry adapter.

Ports the work registry's "audit metadata blob" and "model-level tags"
patterns, re-implemented generically: data date range, row counts,
lineage (git SHA), runtime versions, and a fixed tag shape.
"""

from __future__ import annotations

import datetime as dt
import platform
import subprocess
from pathlib import Path

import torch


def _git_sha(cwd: Path | None = None) -> str:
    """Return the current git SHA, or ``"unknown"`` outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def build_run_metadata(
    *,
    game_date_min: dt.date | None,
    game_date_max: dt.date | None,
    possession_count: int,
    seed: int,
    gpu: bool = False,
    runtime_seconds: float | None = None,
    repo_dir: Path | None = None,
) -> dict[str, object]:
    """Build the audit-metadata blob logged alongside every registered version.

    ``game_date_min``/``game_date_max``/``possession_count`` describe the
    as-of data lineage for the run; the caller computes these from its own
    walk-forward slice (this module does not query DuckDB itself, to stay
    decoupled from any one model's feature set).
    """
    return {
        "game_date_min": game_date_min.isoformat() if game_date_min else None,
        "game_date_max": game_date_max.isoformat() if game_date_max else None,
        "possession_count": possession_count,
        "git_sha": _git_sha(repo_dir),
        "seed": seed,
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "gpu": gpu,
        "runtime_seconds": runtime_seconds,
        "logged_at": dt.datetime.now(dt.UTC).isoformat(),
    }


def build_tags(
    *,
    rung: int,
    model_method: str,
    cold_start_flags: list[str] | None = None,
    source_config: str | None = None,
) -> dict[str, object]:
    """Build the governance/discovery tag blob for a registered version."""
    return {
        "rung": rung,
        "model_method": model_method,
        "cold_start_flags": cold_start_flags or [],
        "source_config": source_config,
    }
