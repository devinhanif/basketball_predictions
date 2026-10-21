# ruff: noqa: E501
"""The frozen config of COMBO_STOCKS_SHADOW, loaded and verified.

``configs/combo_stocks_shadow.yaml`` is hashed into the rule (sha256 8932ccec...); a changed file is a new rule id, so the
writer REFUSES to run on any other bytes. The frozen base normals are a committed FILE (``BASE_PATH``, raw little-endian float64,
C order, ``n_draws x 6``) loaded at runtime and checked against the sha in the config the same way; ``combo_copula.base_normals``
is only the documented tool that made the file (see ``BASE_PATH`` below), never called on the live path. ``r_id`` is the sha256 of the 6x6 latent correlation as float64 bytes (the 4-dp matrix of the config).
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
#: The frozen base normals as bytes. ``combo_copula.base_normals(10000, seed=20261014)`` whitens through ``inv(cholesky(cov))``, so its
#: bytes depend on the BLAS build: arm64 Accelerate (the Mac the rule was frozen on) gives 08ddb2a2, the ubuntu runner gives 588b625d.
#: The file was written once on the Mac from that call (sha256 of the file == sha256 of the array bytes == FROZEN_BASE_SHA); the
#: path and dtype are constants here because the yaml is itself frozen (sha 8932ccec) and cannot carry them. Size 10000 x 6 x 8 = 480,000 bytes.
BASE_PATH = ROOT / "configs" / "combo_base_normals_08ddb2a2.f64"
BASE_DTYPE = "<f8"
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


def load_base(n_draws: int, d: int, path: Path = BASE_PATH) -> np.ndarray:
    """The frozen base normals from the committed file (raw ``<f8``, C order); the shape must fit ``n_draws x d`` exactly."""
    try:
        buf = path.read_bytes()
    except OSError as e:
        raise ComboConfigError(f"base normals file {path} unreadable: {e}") from e
    if len(buf) != n_draws * d * 8:
        raise ComboConfigError(
            f"base normals file has {len(buf)} bytes, expected {n_draws * d * 8}"
        )
    return np.frombuffer(buf, dtype=BASE_DTYPE).reshape(n_draws, d).astype(np.float64)


def load_config(
    path: Path = CONFIG_PATH, *, verify: bool = True, base_path: Path = BASE_PATH
) -> ComboConfig:
    """Load the config; with ``verify`` (the live default) raise ``ComboConfigError`` unless the bytes, the base normals and
    the stat order equal the frozen values. ``verify=False`` exists for tests that probe the refusal itself."""
    sha = sha256_file(path)
    raw = yaml.safe_load(path.read_text())
    cp = raw["copula"]
    r = np.array(cp["R"], dtype=float)
    base = load_base(int(cp["n_draws"]), len(C.STAT_ORDER), base_path)
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
