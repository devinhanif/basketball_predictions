"""Joint-probability engines behind a flag (independence, copulas, possession-sim stub)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from nba.parlay.copula import (
    PairCorr,
    build_corr_matrix,
    hits_from_uniforms,
    sample_latent_uniforms,
)
from nba.parlay.legs import Leg

ENGINE_NAMES = ("independence", "gaussian_copula", "t_copula", "possession_sim")


@dataclass(frozen=True)
class JointResult:
    prob: float
    n_sims: int  # 0 for closed-form engines
    hits: int  # successes among n_sims (0 if closed form)


class JointEngine(Protocol):
    @property
    def name(self) -> str: ...

    def joint(self, legs: Sequence[Leg], marg: Sequence[float]) -> JointResult:
        """``marg`` = P(leg hits) per leg (side-adjusted)."""
        ...


@dataclass
class IndependenceEngine:
    name: str = "independence"

    def joint(self, legs: Sequence[Leg], marg: Sequence[float]) -> JointResult:
        return JointResult(prob=float(np.prod(marg)), n_sims=0, hits=0)


@dataclass
class CopulaEngine:
    corr: PairCorr
    kind: str = "gaussian"
    n_sims: int = 20000
    seed: int = 0
    t_df: float = 6.0

    @property
    def name(self) -> str:
        return "gaussian_copula" if self.kind == "gaussian" else "t_copula"

    def joint(self, legs: Sequence[Leg], marg: Sequence[float]) -> JointResult:
        # Convert side-adjusted hit prob back to p_yes for the latent threshold.
        p_yes = [m if leg.side == "yes" else 1.0 - m for leg, m in zip(legs, marg, strict=True)]
        r = build_corr_matrix(legs, self.corr)
        u = sample_latent_uniforms(r, self.n_sims, self.seed, self.kind, self.t_df)
        h = hits_from_uniforms(u, p_yes, [leg.side for leg in legs]).all(axis=1)
        hits = int(h.sum())
        return JointResult(prob=hits / self.n_sims, n_sims=self.n_sims, hits=hits)


@dataclass
class PossessionSimEngine:
    """Stub hook: reads joint outcomes from simulated games (rung 3/4). Not implemented.

    Use only if it beats the copula on joint calibration by more than bootstrap noise.
    ``sampler`` should return a (n_sims, k) boolean leg-hit matrix for the legs.
    """

    sampler: Callable[[Sequence[Leg]], np.ndarray] | None = None
    name: str = "possession_sim"

    def joint(self, legs: Sequence[Leg], marg: Sequence[float]) -> JointResult:
        if self.sampler is None:
            raise NotImplementedError("possession-sim joint is a stub; no sampler supplied")
        h = np.asarray(self.sampler(legs), dtype=bool).all(axis=1)
        return JointResult(prob=float(h.mean()), n_sims=len(h), hits=int(h.sum()))


def make_engine(
    name: str, corr: PairCorr | None = None, n_sims: int = 20000, seed: int = 0, t_df: float = 6.0
) -> JointEngine:
    if name == "independence":
        return IndependenceEngine()
    if name in ("gaussian_copula", "t_copula"):
        if corr is None:
            raise ValueError("copula engines need an estimated PairCorr")
        kind = "gaussian" if name == "gaussian_copula" else "t"
        return CopulaEngine(corr=corr, kind=kind, n_sims=n_sims, seed=seed, t_df=t_df)
    if name == "possession_sim":
        return PossessionSimEngine()
    raise ValueError(f"unknown engine {name!r}; choose from {ENGINE_NAMES}")
