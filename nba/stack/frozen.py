"""A fitted router frozen into a small JSON blob (no pickles, no weights files).

``freeze_softmax`` fits a :class:`~nba.stack.router.SoftmaxGate` on a
:class:`~nba.stack.data.StackData` and returns a :class:`FrozenGate` holding the
context encoder statistics and the coefficient matrix. A frozen gate maps a
context frame to per-row candidate weights without any refit, so the exact same
object is used for the one-shot holdout evaluation and for forward (daily)
application. ``content_hash`` is the identity that pre-registrations refer to.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nba.stack import FROZEN_SEASON
from nba.stack.data import StackData
from nba.stack.router import ContextEncoder, SoftmaxGate, _softmax
from nba.stack.scoring import is_binary, pool

SCHEMA_VERSION = 1


@dataclass
class FrozenGate:
    target: str
    candidates: list[str]
    encoder: ContextEncoder
    coef: np.ndarray  # (d + 1, k)
    l2: float
    fit_info: dict[str, Any]

    @property
    def features(self) -> list[str]:
        return [*self.encoder.num, *self.encoder.cat]

    def weights(self, context: pd.DataFrame) -> np.ndarray:
        """(n, k) candidate weights; every gate feature must be present."""
        missing = [c for c in self.features if c not in context.columns]
        if missing:
            raise KeyError(f"context is missing gate features: {missing}")
        x = self.encoder.transform(context[self.features])
        return np.asarray(_softmax(np.column_stack([np.ones(len(x)), x]) @ self.coef))

    def apply(self, context: pd.DataFrame, cand: np.ndarray) -> np.ndarray:
        """Pool ``cand`` (props (n, k, 19); win (n, k)) with the frozen weights."""
        if cand.shape[1] != len(self.candidates):
            raise ValueError(f"expected {len(self.candidates)} candidates, got {cand.shape[1]}")
        return pool(self.target, self.weights(context), cand)

    def to_dict(self) -> dict[str, Any]:
        e = self.encoder
        return {
            "schema": SCHEMA_VERSION,
            "gate": "softmax",
            "target": self.target,
            "candidates": list(self.candidates),
            "pool": "logit_average" if is_binary(self.target) else "quantile_average",
            "l2": self.l2,
            "encoder": {
                "num": list(e.num),
                "cat": [[k, list(v)] for k, v in e.cat.items()],  # ordered: column order matters
                "med": [float(v) for v in e.med],
                "mu": [float(v) for v in e.mu],
                "sd": [float(v) for v in e.sd],
            },
            "coef": [[float(v) for v in row] for row in self.coef],
            "fit": self.fit_info,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FrozenGate:
        if d.get("schema") != SCHEMA_VERSION or d.get("gate") != "softmax":
            raise ValueError("unsupported frozen gate blob")
        enc = ContextEncoder()
        enc.num = list(d["encoder"]["num"])
        enc.cat = {str(k): list(v) for k, v in d["encoder"]["cat"]}
        enc.med = np.asarray(d["encoder"]["med"], dtype=float)
        enc.mu = np.asarray(d["encoder"]["mu"], dtype=float)
        enc.sd = np.asarray(d["encoder"]["sd"], dtype=float)
        coef = np.asarray(d["coef"], dtype=float)
        if coef.shape[1] != len(d["candidates"]):
            raise ValueError("coef width != number of candidates")
        return cls(
            d["target"], list(d["candidates"]), enc, coef, float(d["l2"]), dict(d.get("fit", {}))
        )

    def content_hash(self) -> str:
        """Hash of the numeric content only (candidates, encoder, coef), not metadata."""
        d = self.to_dict()
        core = {k: d[k] for k in ("target", "candidates", "pool", "l2", "encoder", "coef")}
        blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def freeze_softmax(data: StackData, *, l2: float = 1e-2, max_season: int) -> FrozenGate:
    """Fit once on ALL rows of ``data`` (which must be < the frozen holdout) and freeze."""
    if max_season >= FROZEN_SEASON:
        raise ValueError(f"max_season must be < {FROZEN_SEASON} (frozen holdout)")
    if int(data.keys["season"].max()) > max_season:
        raise ValueError("data contains seasons beyond max_season")
    enc = ContextEncoder().fit(data.context)
    x = enc.transform(data.context)
    gate = SoftmaxGate(l2=l2)
    gate.fit(x, data)
    assert gate.coef is not None
    seasons = sorted(int(s) for s in data.keys["season"].unique())
    info = {
        "n_rows": int(data.n),
        "seasons": seasons,
        "date_min": str(data.keys["game_date"].min().date()),
        "date_max": str(data.keys["game_date"].max().date()),
    }
    return FrozenGate(data.target, list(data.candidates), enc, gate.coef.copy(), l2, info)
