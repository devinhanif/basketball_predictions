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

from nba.props.role_change import RoleChangeConfig

#: Stats modeled this milestone.
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
    #: Trailing-games window the as-of rolling features are computed over
    #: (see ``nba.props.minutes`` module docstring "mis-centering fix" --
    #: an *unbounded* career-to-date window lets a player's rookie/bench
    #: seasons permanently drag down the rolling average after a real role
    #: change (more minutes, more usage), which is exactly the "steady,
    #: high-usage players under-predicted" bias this bounds against.
    #: ~150 games is roughly two seasons -- long enough for stable
    #: shrinkage denominators, short enough to track a durable role change.
    lookback_games: int = 150
    #: Join the as-of team/game-context features (rest/b2b, travel,
    #: tanking incentive, national TV, standings) onto the minutes feature
    #: frame and apply the fixed-effect adjustment below -- see
    #: ``nba.props.minutes`` module docstring "game-context wiring". Flag
    #: exists so the maintainer can A/B this on the real 4-season DB; the
    #: props-modeler's own tests only ever run on the fixture/synthetic data.
    #: Default OFF: the real 4-season A/B showed the context adjustments
    #: slightly WORSEN accuracy (DNP log loss 0.3881->0.3908, minutes MAE
    #: 6.185->6.222); they only corrected an aggregate minutes bias
    #: (0.572->0.037), which a single offset achieves without features.
    #: The hand-set constants aren't learned, so this is kept available
    #: (flag + plumbing) for a future learned fit, not shipped on.
    use_game_context: bool = False
    #: Fixed, documented effect sizes for the three context signals
    #: CLAUDE.md's motivation names as mechanistically likely to move WHO
    #: plays and HOW MUCH minutes (rather than game win probability, where
    #: rest/travel/tanking didn't beat Elo): back-to-backs, travel, and
    #: tanking incentive. These are NOT fit from data -- deliberately, so
    #: toggling ``use_game_context`` is a clean, leakage-free A/B: each
    #: row's adjustment is a pure function of that row's own as-of context
    #: columns, never of any other row (future or past). Applied as a
    #: per-row additive nudge on top of (not instead of) the existing
    #: empirical-Bayes shrinkage. Signs are directional guesses (tired/
    #: resting/tanking teams play their rotation players less), not tuned
    #: on any backtest; the real A/B decides if the sign/magnitude is
    #: actually right.
    b2b_p_play_adjust: float = -0.03
    b2b_mu_adjust: float = -1.0
    tanking_p_play_adjust: float = -0.08  # x tanking_incentive, already in [0, 1]
    tanking_mu_adjust: float = -2.0
    travel_p_play_adjust_per_1000mi: float = -0.01
    travel_mu_adjust_per_1000mi: float = -0.3


@dataclass
class CountStatConfig:
    """Per-stat frequency (per-minute rate) + dispersion defaults for reb/ast/fg3m."""

    default_rate_per_min: float
    default_alpha: float  # NegBin dispersion default (NB2: Var = mu + alpha*mu^2)
    k_rate_minutes: float = 200.0  # pseudo-count in *minutes* for the rate
    k_alpha_games: float = 20.0  # pseudo-count in *games* for the dispersion
    #: Trailing-games window for the as-of rolling sums -- see
    #: ``MinutesModelConfig.lookback_games`` for why this isn't unbounded.
    lookback_games: int = 150


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
    #: Trailing-games window for the as-of rolling sums -- see
    #: ``MinutesModelConfig.lookback_games`` for why this isn't unbounded.
    lookback_games: int = 150


@dataclass
class ZeroInflationConfig:
    """Zero-inflation detection tolerance (CLAUDE.md: "check ... for 3PM")."""

    #: Only fit an extra zero-mass if the observed zero rate exceeds the
    #: base NegBin's implied zero probability by more than this, and there
    #: are enough games to make the comparison meaningful.
    tolerance: float = 0.05
    min_games: int = 10


@dataclass
class CombosConfig:
    """PRA / P+R / P+A / R+A combo distributions (CLAUDE.md milestone 8)."""

    enabled: bool = True
    min_pairs_for_corr: int = 30


@dataclass
class CoherenceConfig:
    """Hierarchical (MinT forecast-proportions) team-total reconciliation."""

    enabled: bool = True
    k_team_games: float = 10.0  # pseudo-count (team-games) for the team-total shrinkage prior


@dataclass
class ConformalConfig:
    """Split-conformal interval wrapper (CLAUDE.md milestone 8).

    Two-sided: the learned ``adjustment`` (see ``nba.props.conformal``) may
    be negative (tightens an over-wide parametric interval) as well as
    positive (widens a too-narrow one) -- real 4-season validation showed
    rebounds/assists/3PM intervals land badly *over*-covered, which a
    widen-only wrapper could never correct.
    """

    enabled: bool = True
    alpha: float = 0.2  # 1 - alpha = 80%, matching CLAUDE.md's 80%-coverage target
    cal_frac: float = 0.5  # fraction of (chronologically earliest) rows used for calibration


@dataclass
class VolatilityBucketSpec:
    """One bucket's half-open coefficient-of-variation interval ``[lo, hi)``."""

    name: str
    lo: float
    hi: float


@dataclass
class VolatilityConfig:
    """Volatility-bucket slicing (tests the hypothesis that the model's
    edge concentrates in high-volatility players -- see
    ``nba.props.volatility`` module docstring for the as-of CV metric and
    the bucketing/CI-honesty rules).
    """

    enabled: bool = True
    #: Number of strictly-prior games the CV is computed over.
    lookback_games: int = 10
    #: Minimum strictly-prior games required before a CV is trusted at
    #: all; below this the row falls in the ``insufficient_history``
    #: bucket rather than being forced into low/high on noise.
    min_games_for_cv: int = 5
    #: Which history drives the bucket assignment: the target stat's own
    #: CV ("stat") or the shared minutes-CV ("minutes") -- CLAUDE.md:
    #: "a minutes-CV variant available too."
    cv_variant: str = "stat"
    #: If True, recompute buckets each run as a median split of this
    #: sample's own (finite) CV distribution instead of the fixed cutoffs
    #: below.
    use_quantile_cutoffs: bool = False
    #: Fixed CV cutoffs (chosen as a documented, round-number default --
    #: not tuned on any backtest). Easy to extend to 3+ buckets.
    buckets: list[VolatilityBucketSpec] = field(
        default_factory=lambda: [
            VolatilityBucketSpec(name="low", lo=0.0, hi=0.35),
            VolatilityBucketSpec(name="high", lo=0.35, hi=float("inf")),
        ]
    )
    #: Below this many player-games, a bucket's CI is reported as an
    #: explicit "insufficient data" note instead of a numeric point
    #: estimate (CLAUDE.md milestone 7, point 4's honesty guard).
    min_bucket_n: int = 100


@dataclass
class DispersionConfig:
    """Native predictive-dispersion (variance) calibration (CLAUDE.md
    Phase 2's "calibrated P(stat >= N)" + 80%-coverage asks -- see
    ``nba.props.dispersion`` module docstring).

    Fits one scalar multiplier per stat on each distribution family's
    *fitted variance* so the native (pre-conformal) 80% interval lands
    near ``target_coverage`` -- real 4-season validation showed points
    badly under-dispersed (54% native coverage, needs widening) and
    rebounds/assists/3PM badly over-dispersed (~96%, needs tightening).
    Fit on a strictly chronologically-earlier calibration split, same
    never-random discipline as ``nba.props.conformal``.
    """

    enabled: bool = True
    target_coverage: float = 0.8
    cal_frac: float = 0.5
    #: Below this many calibration-split rows, calibration is skipped
    #: (multiplier left at the documented no-op 1.0) rather than fitting a
    #: multiplier to noise -- same honesty-guard pattern as
    #: ``ZeroInflationConfig.min_games`` / ``conformal.MIN_RELIABLE_TEST_N``.
    min_cal_n: int = 50
    #: Tunable blend, in ``[0.0, 1.0]``, between the original fitted
    #: dispersion (``0.0``) and the fully-recalibrated dispersion
    #: (``1.0``) -- see ``nba.props.dispersion`` module docstring for why
    #: full recalibration (the old, implicit default of ``1.0``) wins on
    #: native 80% coverage but costs too much CRPS. Defaulted to a gentler
    #: ``0.5`` middle ground; override here (or construct a custom
    #: ``PropsConfig`` for a sweep over e.g. ``{0.0, 0.25, 0.5, 0.75, 1.0}``)
    #: with no code changes required.
    dispersion_strength: float = 0.5


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
    combos: CombosConfig = field(default_factory=CombosConfig)
    coherence: CoherenceConfig = field(default_factory=CoherenceConfig)
    role_change: RoleChangeConfig = field(default_factory=RoleChangeConfig)
    conformal: ConformalConfig = field(default_factory=ConformalConfig)
    volatility: VolatilityConfig = field(default_factory=VolatilityConfig)
    dispersion: DispersionConfig = field(default_factory=DispersionConfig)
    seed: int = 0
    n_boot: int = 500


DEFAULT_CONFIG = PropsConfig()
