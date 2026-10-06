"""Default parameters for the props pipeline (minutes model + stat distributions).

Kept as plain dataclasses (not YAML) because ``nba/props/`` does not own
``configs/`` -- see the props-modeler status file for the escalation this
would normally route through. Every default below is a documented,
league-wide fallback used only when a player has no (or very little)
as-of history; CLAUDE.md's empirical-Bayes shrinkage (``nba.coldstart``)
blends it with the observed rate as soon as there is any history at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Stats modeled this milestone. PRA/combos are explicitly out of scope
#: (next milestone, per CLAUDE.md build order).
TARGET_STATS: list[str] = ["pts", "reb", "ast", "fg3m"]

#: P(stat >= N) thresholds reported in ``prop_predictions.p_ge`` and used
#: for threshold log loss / calibration. Chosen to span a plausible range
#: per stat; not tuned on any backtest.
THRESHOLDS: dict[str, list[int]] = {
    "pts": [10, 15, 20, 25, 30],
    "reb": [2, 4, 6, 8, 10],
    "ast": [2, 4, 6, 8],
    "fg3m": [1, 2, 3, 4],
}


@dataclass
class MinutesModelConfig:
    """Hurdle minutes model defaults (league-wide "average rotation player")."""

    default_p_play: float = 0.82
    default_mu: float = 24.0
    default_sigma: float = 8.0
    k_play: float = 8.0  # pseudo-count (games) for shrinking P(plays)
    k_mu: float = 6.0  # pseudo-count (played games) for shrinking mean minutes
    k_sigma: float = 6.0  # pseudo-count (played games) for shrinking std minutes
    min_sigma: float = 2.0
    max_minutes: float = 48.0


@dataclass
class CountStatConfig:
    """Per-stat frequency (per-minute rate) + dispersion defaults for reb/ast/fg3m."""

    default_rate_per_min: float
    default_alpha: float  # NegBin dispersion default (NB2: Var = mu + alpha*mu^2)
    k_rate_minutes: float = 200.0  # pseudo-count in *minutes* for the rate
    k_alpha_games: float = 20.0  # pseudo-count in *games* for the dispersion


@dataclass
class PointsConfig:
    """Frequency-severity decomposition defaults for points.

    Frequency = scoring-event rate per minute (an event is a made 3 or an
    implied make-equivalent worth ~2 points -- see
    ``nba.props.stat_models`` for why attempts/misses aren't available).
    Severity = points value per scoring event.
    """

    default_events_per_min: float = 0.42
    default_value_per_event: float = 2.3
    default_value_variance: float = 0.3**2
    k_events_minutes: float = 200.0
    k_value_events: float = 30.0
    k_value_var_events: float = 30.0


@dataclass
class ZeroInflationConfig:
    """Zero-inflation detection tolerance (CLAUDE.md: "check ... for 3PM")."""

    #: Only fit an extra zero-mass if the observed zero rate exceeds the
    #: base NegBin's implied zero probability by more than this, and there
    #: are enough games to make the comparison meaningful.
    tolerance: float = 0.05
    min_games: int = 10


@dataclass
class PropsConfig:
    minutes: MinutesModelConfig = field(default_factory=MinutesModelConfig)
    points: PointsConfig = field(default_factory=PointsConfig)
    count_stats: dict[str, CountStatConfig] = field(
        default_factory=lambda: {
            "reb": CountStatConfig(default_rate_per_min=0.18, default_alpha=0.15),
            "ast": CountStatConfig(default_rate_per_min=0.13, default_alpha=0.20),
            "fg3m": CountStatConfig(default_rate_per_min=0.08, default_alpha=0.30),
        }
    )
    zero_inflation: ZeroInflationConfig = field(default_factory=ZeroInflationConfig)
    seed: int = 0
    n_boot: int = 500


DEFAULT_CONFIG = PropsConfig()
