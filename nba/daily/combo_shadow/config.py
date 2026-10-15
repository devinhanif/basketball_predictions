# ruff: noqa: E501
"""The frozen config of COMBO_STOCKS_SHADOW, loaded and verified.

``configs/combo_stocks_shadow.yaml`` is hashed into the rule (sha256 8932ccec...); a changed file is a new rule id, so the
writer REFUSES to run on any other bytes. The frozen base normals (``combo_copula.base_normals``) are checked against the sha in
the config the same way. ``r_id`` is the sha256 of the 6x6 latent correlation as float64 bytes (the 4-dp matrix of the config).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from nba.props import combo_copula as C

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "configs" / "combo_stocks_shadow.yaml"
#: the sha256 values the frozen rule text names (any other value is a different rule)
FROZEN_CONFIG_SHA = "8932ccec58776dc3c567514c6c2f7f4ab7f667c2559fa24b103edd7c02774389"
FROZEN_BASE_SHA = "08ddb2a22c0828b2ea50e7dcbf2f0462bf5c4aeabf9789381423049c2b9a5a3a"
TARGETS = ("blk", "stl", "pra", "pr", "pa", "ra", "bs")
COMBO_TARGETS = ("pra", "pr", "pa", "ra", "bs")
STOCK_TARGETS = ("blk", "stl")


class ComboConfigError(RuntimeError):
    """The config on disk is not the frozen one (or its derived objects do not match their frozen shas)."""


@dataclass(frozen=True)
class ComboConfig:
    raw: dict[str, Any]
    config_sha: str
    r: np.ndarray
    r_id: str
    base: np.ndarray
    base_id: str
    floors: dict[str, float]
    n_min: int


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def r_id_of(r: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(r, dtype=np.float64).tobytes()).hexdigest()


def load_config(path: Path = CONFIG_PATH, *, verify: bool = True) -> ComboConfig:
    """Load the config; with ``verify`` (the live default) raise ``ComboConfigError`` unless the bytes, the base normals and
    the stat order equal the frozen values. ``verify=False`` exists for tests that probe the refusal itself."""
    sha = sha256_file(path)
    raw = yaml.safe_load(path.read_text())
    cp = raw["copula"]
    r = np.array(cp["R"], dtype=float)
    base = C.base_normals(int(cp["n_draws"]), seed=int(cp["base_seed"]))
    base_id = hashlib.sha256(base.tobytes()).hexdigest()
    if verify:
        if sha != FROZEN_CONFIG_SHA:
            raise ComboConfigError(
                f"config sha256 {sha[:8]} is not the frozen {FROZEN_CONFIG_SHA[:8]}"
            )
        if base_id != FROZEN_BASE_SHA or base_id != cp["base_sha256"]:
            raise ComboConfigError(
                f"base normals sha256 {base_id[:8]} is not the frozen {FROZEN_BASE_SHA[:8]}"
            )
        if list(raw["stat_order"]) != list(C.STAT_ORDER) or list(raw["targets"]) != list(TARGETS):
            raise ComboConfigError("stat_order / targets differ from the frozen rule")
        if r.shape != (len(C.STAT_ORDER), len(C.STAT_ORDER)) or not np.allclose(r, r.T):
            raise ComboConfigError("R is not a symmetric 6x6 matrix")
    return ComboConfig(
        raw=raw, config_sha=sha, r=r, r_id=r_id_of(r), base=base, base_id=base_id,
        floors={str(k): float(v) for k, v in raw["floors"].items()}, n_min=int(raw["n_min"]),
    )  # fmt: skip
