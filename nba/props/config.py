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

# The opponent-adjustment config lives here rather than in ``nba.props.opponent`` so this live
# module does not import the research archetype code that module depends on.
#: Default trailing-games window, mirrors every other as-of builder in this
#: package (``DEFAULT_LOOKBACK_GAMES`` in ``nba.props.minutes`` /
#: ``nba.props.stat_models``).
DEFAULT_LOOKBACK_GAMES = 150


@dataclass
class OpponentAdjustmentConfig:
    """``use_opponent_adjustment`` flag + tuning knobs (CLAUDE.md task ask).

    Defaulted OFF: the real 4-season A/B showed opponent adjustment slightly
    WORSENS CRPS for every stat (pts 3.670->3.676, reb 1.539->1.541, ast
    1.297->1.300, fg3m 0.743->0.745) and does not help beat the season-avg
    baseline -- the box-score-derived defense/pace factors add noise without
    improving central tendency. Kept available (flag + plumbing) for a future
    version using real defensive ratings. Set ``enabled=True`` to exercise it.
    Nothing here is tuned on any backtest.
    """

    enabled: bool = False
    lookback_games: int = DEFAULT_LOOKBACK_GAMES
    #: Below this many strictly-prior games for the OPPONENT, the defense
    #: and pace factors are forced to the neutral 1.0 (not enough history
    #: to trust the ratio) -- same honesty-guard pattern as
    #: ``ZeroInflationConfig.min_games`` / ``DispersionConfig.min_cal_n``.
    min_games_for_factor: int = 5
    #: Conservative symmetric-ish clip on the opponent-defense factor alone
    #: (applied before combining with pace) -- guards against a small
    #: sample's noisy allowed-stat average producing an extreme ratio.
    def_factor_clip_lo: float = 0.80
    def_factor_clip_hi: float = 1.25
    #: Same clip, for the pace factor alone.
    pace_factor_clip_lo: float = 0.85
    pace_factor_clip_hi: float = 1.15
    #: ``docs/NEXT_OPTIONS.md`` §3 Option A: replace the team-level
    #: "opponent allows X" factor with an archetype-level one -- each
    #: defense's as-of allowed-stat average broken out by the SHOOTING
    #: player's archetype cluster (:mod:`nba.coldstart.archetypes`),
    #: instead of pooling every opponent across all archetypes together.
    #: See :func:`build_archetype_opponent_features` /
    #: :func:`compute_archetype_opponent_factors`. Default OFF: this is
    #: RETRYING a documented failure (the team-level version above
    #: REGRESSED CRPS on every stat, see this module's docstring) at finer
    #: granularity -- it may still fail, which is a valid, reportable
    #: negative result. Independent of ``enabled``: when True, the
    #: archetype-level factor is applied INSTEAD OF the team-level one
    #: (see ``nba.props.run``), so the two are never stacked and an A/B
    #: against either the no-adjustment baseline or the already-failed
    #: team-level version is a single-flag toggle.
    use_archetype_factor: bool = False
    #: Cluster count for the archetype split (matches the k=6 used
    #: elsewhere per ``docs/NEXT_OPTIONS.md`` §3; not re-tuned here).
    archetype_k: int = 6
    #: Per-(opponent_team, archetype) cell sample-size guard, analogous to
    #: ``min_games_for_factor`` but intentionally STRICTER: splitting the
    #: same opponent history by archetype shrinks the per-cell sample size,
    #: so a cell that would have cleared the team-level guard can still be
    #: far noisier once split six ways. Below this many strictly-prior
    #: games FOR THAT SPECIFIC (opponent, archetype) CELL, the factor is
    #: forced to the neutral 1.0 rather than fit on noise -- the hard
    #: pre-commit ``docs/NEXT_OPTIONS.md`` §3 requires ("if the per-cell
    #: opp_games_played_prior rarely clears min_games_for_factor, don't
    #: ship regardless of the point estimate").
    min_games_for_archetype_factor: int = 10


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
    #: Option A (``docs/NEXT_OPTIONS.md`` §1): replace the hand-set
    #: constants above with coefficients fit by a small ridge regression of
    #: minutes/DNP residuals on ``GAME_CONTEXT_FEATURE_COLUMNS`` (plus any
    #: ``extra_features`` columns supplied to
    #: ``nba.props.minutes.fit_learned_game_context``), fit only on a
    #: chronologically-earlier calibration split -- same ``cal_frac``
    #: discipline as ``DispersionConfig``/``ConformalConfig``. Mutually
    #: exclusive in effect with ``use_game_context``'s hand-set adjustment:
    #: when both are True and a fitted ``LearnedGameContextParams`` is
    #: passed to ``predict_minutes``, the learned adjustment is applied
    #: instead of the fixed-effect one (see ``nba.props.minutes`` module
    #: docstring). Default OFF -- this re-tries a documented failure
    #: (the hand-set version regressed DNP log loss/MAE) with a learned
    #: fit instead of a guess; the maintainer A/Bs it on the real 4-season
    #: DB per this module's docstring, nothing here is tuned on a backtest.
    use_learned_game_context: bool = False
    learned_context_cal_frac: float = 0.5
    learned_context_ridge_alpha: float = 1.0
    #: Below this many calibration-split rows (overall, for p_play; the mu
    #: fit additionally requires this many *played* calibration rows), the
    #: learned fit is skipped and left at the documented no-op (all-zero
    #: coefficients) rather than fit to noise -- same honesty-guard pattern
    #: as ``DispersionConfig.min_cal_n``.
    learned_context_min_cal_n: int = 200
    #: Cold-start gate for the learned game-context adjustment. The real-DB
    #: A/B (2026-10-08) showed the learned fit + depth-chart feature IMPROVES
    #: cold-start players (n_played_prior<20: minutes MAE -1.47, DNP log loss
    #: -0.021, CIs exclude 0) but slightly REGRESSES established players. So
    #: when set, the learned adjustment is applied only to rows with
    #: ``n_played_prior < learned_context_max_n_played`` (established rows keep
    #: the base shrinkage model). None = apply to all rows (ungated). Defaults
    #: to 20 (the A/B-proven gate): gated, the fit improves overall minutes MAE
    #: (-0.096) and DNP log loss (-0.0017) with ZERO change to established
    #: players, vs. a regression on established players when ungated.
    learned_context_max_n_played: float | None = 20.0
    #: Option B (``docs/NEXT_OPTIONS.md`` §1): explicit blowout/garbage-time
    #: branch. ``projected_margin`` (an OPTIONAL as-of pre-game absolute
    #: point-spread input, NOT computed by this module -- see
    #: ``nba.props.minutes`` module docstring) shrinks ``mu`` toward a
    #: learned garbage-time mean and widens ``sigma`` once
    #: ``|projected_margin|`` exceeds ``garbage_time_margin_threshold``,
    #: ramping linearly to full effect over the next
    #: ``garbage_time_margin_scale`` points. Default OFF; no real-data A/B
    #: has been run yet (see this module's docstring for the exact
    #: maintainer command).
    use_garbage_time: bool = False
    garbage_time_margin_threshold: float = 20.0
    garbage_time_margin_scale: float = 10.0
    garbage_time_cal_frac: float = 0.5
    garbage_time_min_cal_n: int = 100
    #: Pseudo-count (blowout calibration rows) shrinking the observed
    #: blowout-minutes mean toward ``default_mu`` -- same empirical-Bayes
    #: discipline as every other rate in this module.
    garbage_time_k: float = 20.0
    #: Multiplicative widening of ``sigma`` at full garbage-time weight
    #: (``w=1``); ``sigma_applied = sigma * (1 + w * (garbage_time_sigma_inflate - 1))``.
    garbage_time_sigma_inflate: float = 1.5


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
    opponent_adjustment: OpponentAdjustmentConfig = field(default_factory=OpponentAdjustmentConfig)
    conformal: ConformalConfig = field(default_factory=ConformalConfig)
    volatility: VolatilityConfig = field(default_factory=VolatilityConfig)
    dispersion: DispersionConfig = field(default_factory=DispersionConfig)
    seed: int = 0
    n_boot: int = 500
    #: Optional frozen-holdout season filter (CLAUDE.md "no leakage" /
    #: docs/ACCEPTANCE_CRITERIA_2026-10-08.md "canonical frozen holdout for
    #: props/routing"). ``None`` (default) preserves the exact prior
    #: behavior -- every row, no filter. Set ``holdout_season=2025`` with
    #: ``holdout_mode="exclude_holdout"`` to tune/backtest on the tunable
    #: pool only, or ``holdout_mode="holdout_only"`` for the single
    #: permitted confirmatory touch (log it to
    #: docs/HOLDOUT_ACCESS_LOG.md before running). See
    #: ``nba.truth.walkforward.filter_by_holdout_mode``.
    holdout_season: int | None = None
    holdout_mode: str = "all"


DEFAULT_CONFIG = PropsConfig()
