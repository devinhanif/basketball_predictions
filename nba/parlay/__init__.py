"""Joint-probability and EV engine (read-only; paper-trade logging only, no execution)."""

from nba.parlay.config import FeeModel, ParlayConfig, load_config
from nba.parlay.engines import ENGINE_NAMES, make_engine
from nba.parlay.ev import NO_POSITIVE_EV, Recommendation, evaluate
from nba.parlay.legs import Leg, dist_from_params, leg_prob

__all__ = [
    "ENGINE_NAMES",
    "NO_POSITIVE_EV",
    "FeeModel",
    "Leg",
    "ParlayConfig",
    "Recommendation",
    "dist_from_params",
    "evaluate",
    "leg_prob",
    "load_config",
    "make_engine",
]
