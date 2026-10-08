"""Parlay legs and their marginal distributions (read from props output)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from nba.props.distributions import (
    Distribution,
    GammaDist,
    NegBinDist,
    NormalDist,
    ZeroInflatedNegBinDist,
)

Side = Literal["yes", "no"]
_EPS = 1e-9


@dataclass(frozen=True)
class Leg:
    """One binary leg: stat >= threshold (side 'yes') or stat < threshold ('no')."""

    game_id: str
    player_id: int
    stat: str
    threshold: float
    side: Side = "yes"
    team_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> Leg:
        team = d.get("team_id")
        return Leg(
            game_id=str(d["game_id"]),
            player_id=int(d["player_id"]),
            stat=str(d["stat"]),
            threshold=float(d["threshold"]),
            side="no" if d.get("side", "yes") == "no" else "yes",
            team_id=None if team is None else int(team),
        )


def dist_from_params(family: str, params: Mapping[str, float] | str) -> Distribution:
    """Rebuild a props distribution from ``prop_predictions`` (dist_family, dist_params)."""
    p: Mapping[str, float] = json.loads(params) if isinstance(params, str) else params
    if family == "normal":
        return NormalDist(mean_=p["mean"], std=p["std"])
    if family == "gamma":
        return GammaDist(shape=p["shape"], scale=p["scale"])
    if family == "negbin":
        return NegBinDist(mu=p["mu"], alpha=p["alpha"])
    if family == "zinb":
        return ZeroInflatedNegBinDist(pi=p["pi"], base=NegBinDist(mu=p["mu"], alpha=p["alpha"]))
    raise ValueError(f"unsupported dist_family for legs: {family!r}")


def p_yes(dist: Distribution, threshold: float) -> float:
    """P(stat >= threshold), clipped to the open unit interval."""
    return min(max(float(dist.p_ge(threshold)), _EPS), 1.0 - _EPS)


def leg_prob(dist: Distribution, leg: Leg) -> float:
    """Marginal probability that the leg hits (respects side)."""
    p = p_yes(dist, leg.threshold)
    return p if leg.side == "yes" else 1.0 - p
