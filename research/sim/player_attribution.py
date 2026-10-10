"""Per-player points attribution on top of the rung-3 team possession sim.

CLAUDE.md architecture ladder, rung 3: "one logistic/GBM head per step
(duration, outcome, shooter, zone, make, rebound, FT) ... Full-game
distributions, **player lines**." This module is the payoff of the sim
pivot: :func:`simulate_game_with_players` runs the same team-level
possession Monte Carlo as ``research.sim.engine.simulate_game`` (same support/
tilt machinery from ``research.sim.possession_model``, same deterministic-given-
seed contract) and additionally attributes every scoring possession's
points to one on-court player via a shooter -> zone -> make/miss -> FT
chain, and additionally attributes **rebounds** (on every missed field
goal, split between the shooting team's offensive-rebound propensity and
the defending team's defensive-rebound propensity) and **assists** (on
every made field goal, whether it was assisted at all via a team-level
as-of rate, and if so, which on-court teammate gets credit, weighted by
their as-of assist rate) to on-court players. See "Rebound attribution"
and "Assist attribution" below for the exact design of each.

## Design: how player attribution stays consistent with the team-level sim

The team-level outcome distribution (how many 0/1/2/3/.../8-point
possessions a team has in a simulated game) is drawn *exactly* the same way
``research.sim.engine._sample_points`` draws it: group simulated games by their
drawn possession count, one vectorized ``rng.multinomial(n, probs, size=...)``
call per group. The only difference here is this module keeps the **counts
per outcome value** (how many 0-point, 1-point, ..., 8-point possessions a
given simulated game had) instead of immediately collapsing them to a
single point total via a dot product with the support.

For every outcome value ``k >= 1`` (a possession that scored points) and
every simulated game, the ``count[k]`` scoring possessions of that exact
point value are distributed among the team's on-court players via a
*second*, nested multinomial draw, weighted by each player's
``usage_weight_i * zone_score_i(k)`` (see :func:`_zone_score`): usage
weight is ``shot_share * availability`` (the "shooter" pick, usage x
on-court time) and ``zone_score_i(k)`` folds in the "zone" and "make/miss"
steps -- a player who both takes more shots from the zone that produces a
``k``-point possession *and* makes them more often gets more than their
raw usage share of the credit for that many points, exactly like a real
"who scored this bucket" resolution would. FT trips (CLAUDE.md step "free
throws") are folded into ``k == 1`` (a lone free throw, weighted by
``ft_trip_rate * ft_pct``) rather than simulated as a wholly separate step,
since the team-level engine's outcome support is already "points scored
this possession", not "event type" -- see :func:`_zone_score` for exactly
how each ``k`` bucket maps to zone/FT probabilities.

Because every point in ``count[k]`` is assigned to *some* player (the
nested multinomial always sums to ``count[k]``), **summed per-player points
equal the team total exactly, by construction, for every simulated game**
-- not approximately. This is what the "team-level totals consistent with
the existing engine" requirement becomes operationally: the team-level
margin/win-prob/total distribution a caller gets from this module (see
``PlayerGameSimResult.home``/``away``, both plain
``research.sim.engine.GameSimResult``) is the *same* possession-outcome draw
``research.sim.engine.simulate_game`` would produce for the same inputs (same
support, same tilt, same pace draw) -- just also broken out by player. See
``tests/ml/test_player_attribution.py::test_team_totals_match_engine_team_sim``
for the statistical-equivalence check (same seed, same mean/engine draw
shape, not merely "close").

## No leakage (CLAUDE.md risk #3)

``PlayerSimProfile.projected_minutes`` is the only "how much will this
player play" signal this module consumes, and it is never the target
game's own box score minutes -- callers build it from
``nba.props.minutes.predict_minutes`` (already a proven no-leakage, as-of
hurdle model) or a comparable as-of average. This module itself takes
plain floats/arrays and never touches a DuckDB connection, so it cannot
read a game's own outcome even by accident; the leakage boundary is
entirely upstream, in the as-of feature builders
(``research.features.player_possession_features``) and ``nba.props.minutes``.
See ``tests/ml/test_player_attribution.py::test_no_leakage_profiles_from_features``
for the planted-future-game proof at the feature-assembly boundary. The
rebound/assist rates (``PlayerSimProfile.orb_rate``/``drb_rate``/
``ast_rate``) carry the identical as-of guarantee -- see
``research.features.player_rebound_assist_features`` module docstring and
``tests/ml/test_player_rebound_assist_features.py`` for that boundary's
own planted-future proof.

## Rebound attribution

On every simulated zero-point possession (outcome ``k == 0`` in the team-
level support), some documented share (:data:`MISSED_FG_SHARE_OF_ZERO_OUTCOME`)
is treated as a missed field-goal attempt (as opposed to a turnover, which
also scores 0 points but creates no rebound chance) -- drawn via
``rng.binomial`` per simulated game, not a fixed rounded fraction, so the
missed-FGA count itself carries Monte Carlo noise like everything else in
this module. Every one of those missed-FGA events is then assigned to
exactly one of the 10 on-court players (the 5 shooting-team players,
eligible for the offensive rebound via their ``orb_rate``, and the 5
defending-team players, eligible for the defensive rebound via their
``drb_rate``) via a single multinomial draw over all 10 -- see
:func:`_attribute_rebounds`. Because every miss is assigned to exactly one
of the 10 players, a team's total simulated rebounds equal (own misses'
OREB share) + (opponent misses' DREB share), which is exactly how a real
box score's team total-rebound count decomposes.

## Assist attribution

Reuses the exact same per-``k``, per-player multinomial draw the points
head already performs (now returned alongside points by
:func:`_attribute_points_and_makes`) to also get each simulated game's
"how many field goals did player ``i`` make" count, restricted to
``k >= 2`` (``k == 1`` is a lone free throw, not a field goal, and cannot
be assisted). For each player's made-FG count, an independent
``rng.binomial`` draw (weighted by the game's team-level
``assisted_fg_rate``, see :func:`PlayerSimProfile`/``profiles_from_features``
and ``research.features.player_rebound_assist_features.LEAGUE_ASSISTED_FG_RATE_DEFAULT``)
decides how many of those makes were assisted at all; the assisted ones are
then distributed among that player's on-court **teammates only** -- the
scorer's own weight is hard-zeroed before normalizing (see
:func:`_normalized_weights_excluding_self`), not merely made unlikely --
via a multinomial weighted by each teammate's ``ast_rate``. See
``tests/ml/test_player_attribution.py::test_assists_never_go_to_the_scorer``
for the resulting invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from research.sim.engine import (
    DEFAULT_HOME_PPP_BONUS,
    DEFAULT_PACE_SD,
    MIN_POSSESSIONS,
    GameSimResult,
)
from research.sim.possession_model import (
    BASE_OUTCOME_PROBS,
    BASE_OUTCOME_SUPPORT,
    tilt_outcome_probs,
)

#: Team minutes per game (5 players x 48 minutes, ignoring OT) -- the
#: denominator turning a player's projected minutes into an "availability"
#: share of the team's playing time for the usage-weight calculation below.
TEAM_MINUTES_PER_GAME = 240.0

#: Floor every normalized weight vector gets clipped to before normalizing,
#: so a degenerate all-zero roster (e.g. every profile's shot_share is 0)
#: falls back to a uniform draw across the roster rather than raising a
#: divide-by-zero.
_WEIGHT_EPS = 1e-9

#: Documented share of zero-point possessions (outcome ``k == 0`` in
#: ``BASE_OUTCOME_SUPPORT``) treated as a missed field-goal attempt rather
#: than a turnover, for the purpose of generating rebound chances. This
#: schema's possessions table has no event-type split within the
#: "0 points scored" bucket at the team-outcome-support granularity this
#: engine samples from (see ``research.sim.possession_model`` module docstring:
#: the 0.4866 zero-point probability was computed by grouping on ``pts``
#: only). 0.85 is a documented estimate, not fit to this project's own
#: possession-level turnover/missed-FGA split -- a team's turnover rate is
#: typically a smaller share of its possessions than its missed-FGA rate,
#: so the bulk of the zero-point bucket is treated as misses. Replace with
#: a value fit from ``possessions.outcome`` once that reconciliation pass
#: is run (same documented-estimate spirit as every other hardcoded
#: constant in this module/``research.sim.possession_model``).
MISSED_FG_SHARE_OF_ZERO_OUTCOME = 0.85

#: Duplicated (not imported, same convention as ``_expected_ppp`` below)
#: from ``research.features.player_rebound_assist_features.
#: LEAGUE_ASSISTED_FG_RATE_DEFAULT`` -- the fallback "fraction of made
#: field goals that are assisted" when a caller doesn't pass a team's own
#: as-of value.
DEFAULT_ASSISTED_FG_RATE = 0.60


@dataclass(frozen=True)
class PlayerSimProfile:
    """One player's as-of rates + projected (never actual) minutes for one game.

    Every field is expected to already be shrunk/as-of (see
    ``research.features.player_possession_features.build_player_shot_rates`` for
    ``shot_share``/``zone_mix``/``zone_make_prob``/``ft_trip_rate``/``ft_pct``,
    and ``nba.props.minutes.predict_minutes`` for ``projected_minutes``) --
    this dataclass is a plain data container, not a feature builder, and
    performs no as-of/leakage logic of its own.
    """

    player_id: int
    shot_share: float
    zone_mix: tuple[float, float, float]  # (rim, mid, above3), sums to ~1
    zone_make_prob: tuple[float, float, float]  # (rim, mid, above3)
    ft_trip_rate: float
    ft_pct: float
    projected_minutes: float  # NEVER this game's actual minutes -- see module docstring
    # Rebound/assist rates (``research.features.player_rebound_assist_features``).
    # Defaulted (not required) so every existing points-only call site keeps
    # working unchanged; a profile with the defaults simply contributes no
    # rebounds/assists weight (falls back to the roster's uniform share via
    # :func:`_normalized_weights` if every profile on a side is left at 0).
    orb_rate: float = 0.0
    drb_rate: float = 0.0
    ast_rate: float = 0.0


@dataclass
class EmpiricalPointsDist:
    """Empirical per-player points distribution from the sim's own samples.

    Duck-types ``nba.props.distributions.Distribution`` (``mean``/``cdf``/
    ``sf``/``p_ge``/``ppf``/``params``/``family``) so it can be scored with
    the *existing* ``nba.props.metrics`` CRPS/threshold-log-loss/coverage
    functions unchanged -- see ``research.eval.player_points_sim_eval`` (this
    project's "sim's version of a points prop").
    """

    samples: np.ndarray
    family: str = "empirical_sim"

    def __post_init__(self) -> None:
        self.samples = np.asarray(self.samples, dtype=float)

    def mean(self) -> float:
        return float(self.samples.mean()) if len(self.samples) else 0.0

    def cdf(self, x: float) -> float:
        if len(self.samples) == 0:
            return float("nan")
        return float(np.mean(self.samples <= x))

    def sf(self, x: float) -> float:
        return 1.0 - self.cdf(x)

    def p_ge(self, n: float) -> float:
        if len(self.samples) == 0:
            return float("nan")
        return float(np.mean(self.samples >= n))

    def ppf(self, q: float) -> float:
        if len(self.samples) == 0:
            return float("nan")
        return float(np.quantile(self.samples, np.clip(q, 0.0, 1.0)))

    def params(self) -> dict[str, float]:
        return {
            "mean": self.mean(),
            "std": float(self.samples.std(ddof=1)) if len(self.samples) > 1 else 0.0,
            "n_sims": float(len(self.samples)),
        }


@dataclass
class PlayerGameSimResult:
    """Full output of :func:`simulate_game_with_players`."""

    home: GameSimResult
    away: GameSimResult
    home_player_points: dict[int, EmpiricalPointsDist]
    away_player_points: dict[int, EmpiricalPointsDist]
    home_player_rebounds: dict[int, EmpiricalPointsDist]
    away_player_rebounds: dict[int, EmpiricalPointsDist]
    home_player_assists: dict[int, EmpiricalPointsDist]
    away_player_assists: dict[int, EmpiricalPointsDist]
    n_sims: int
    seed: int


def _zone_score(profile: PlayerSimProfile, k: int) -> float:
    """``P(this player would produce a k-point make | they take the shot)``-ish scalar.

    Folds the "zone" and "make/miss" steps into one number per ``k`` bucket
    (see module docstring):

    - ``k == 1``: a lone free throw (no made field goal) -- weighted by
      ``ft_trip_rate * ft_pct`` (how often this player gets to the line,
      times how often they make one once there).
    - ``k in (2,)``: a made 2-point field goal -- weighted by the rim/mid
      zone-mix-times-make-probability (the two 2-point zones in this
      schema; see ``research.features.player_possession_features`` module
      docstring on why there is no separate corner/above-break split).
    - ``k >= 3``: a made 3-point field goal (``k in (3, 4)`` for a clean
      3 or a 3-and-1 FT bonus; ``k >= 5`` is empirically negligible per
      ``research.sim.possession_model``'s documented base shape but handled the
      same way rather than raising) -- weighted by the above3 zone-mix-
      times-make-probability.
    """
    rim_mix, mid_mix, above3_mix = profile.zone_mix
    rim_p, mid_p, above3_p = profile.zone_make_prob
    if k <= 1:
        return float(profile.ft_trip_rate * profile.ft_pct)
    if k == 2:
        return float(rim_mix * rim_p + mid_mix * mid_p)
    return float(above3_mix * above3_p)


def _usage_weight(profile: PlayerSimProfile) -> float:
    """``shot_share * availability`` -- the "who gets the ball" weight.

    ``availability`` is projected minutes as a fraction of the team's total
    playing time (``TEAM_MINUTES_PER_GAME``), never the target game's
    actual minutes -- see :class:`PlayerSimProfile`.
    """
    availability = max(profile.projected_minutes, 0.0) / TEAM_MINUTES_PER_GAME
    return float(max(profile.shot_share, 0.0) * availability)


def _normalized_weights(values: np.ndarray) -> np.ndarray:
    """Normalize to a probability vector; uniform fallback if everything's ~0."""
    values = np.clip(values, 0.0, None)
    total = values.sum()
    if total <= _WEIGHT_EPS:
        n = len(values)
        return np.full(n, 1.0 / n) if n else values
    return np.asarray(values / total, dtype=float)


def _roster_weights_by_outcome(
    profiles: list[PlayerSimProfile], support: np.ndarray
) -> dict[int, np.ndarray]:
    """One normalized weight vector per outcome value ``k`` in ``support``."""
    weights: dict[int, np.ndarray] = {}
    for k in support:
        k_int = int(k)
        if k_int <= 0:
            continue
        raw = np.array([_usage_weight(p) * _zone_score(p, k_int) for p in profiles], dtype=float)
        weights[k_int] = _normalized_weights(raw)
    return weights


def _sample_outcome_counts(
    n_poss: np.ndarray, probs: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Per-sim counts of every outcome value (shape ``(n_sims, len(probs))``).

    Same grouped-multinomial vectorization as
    ``research.sim.engine._sample_points``, kept as a separate, duplicated
    (small) function rather than importing that private helper: this one
    returns the full per-outcome counts matrix instead of immediately
    collapsing it to a point total, which is exactly what player
    attribution needs and ``_sample_points`` deliberately does not expose.
    """
    n_sims = len(n_poss)
    n_outcomes = len(probs)
    counts = np.empty((n_sims, n_outcomes), dtype=np.int64)
    unique_n, inverse = np.unique(n_poss, return_inverse=True)
    for i, n in enumerate(unique_n):
        mask = inverse == i
        group_size = int(mask.sum())
        if group_size == 0:
            continue
        counts[mask] = rng.multinomial(int(n), probs, size=group_size)
    return counts


def _attribute_points_and_makes(
    outcome_counts: np.ndarray,
    support: np.ndarray,
    weights_by_k: dict[int, np.ndarray],
    n_players: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-sim, per-player point totals AND made-field-goal counts (each
    shape ``(n_sims, n_players)``).

    For every outcome value ``k`` with ``count > 0`` in some sims, draws a
    nested multinomial distributing those ``count`` k-point possessions
    across the roster, grouped by unique count value (same vectorization
    pattern as :func:`_sample_outcome_counts`). The sum of the points
    matrix over players always equals ``outcome_counts @ support`` exactly
    -- see module docstring. The makes matrix accumulates the identical
    per-event scorer draw for every ``k >= 2`` bucket (a made field goal;
    ``k == 1`` is a lone free throw, not a field goal, and is excluded) --
    reusing the SAME multinomial draw as the points matrix, not a second
    independent one, so "who scored this bucket's points" and "who made
    this field goal" are the same event, as they must be for assist
    attribution (:func:`_attribute_assists`) to be a sane downstream step.
    """
    n_sims = outcome_counts.shape[0]
    player_points = np.zeros((n_sims, n_players), dtype=float)
    player_makes = np.zeros((n_sims, n_players), dtype=float)
    for j, k in enumerate(support):
        k_int = int(k)
        if k_int <= 0:
            continue
        counts_k = outcome_counts[:, j]
        if counts_k.sum() == 0:
            continue
        probs_k = weights_by_k[k_int]
        unique_c, inverse = np.unique(counts_k, return_inverse=True)
        for i, c in enumerate(unique_c):
            if c == 0:
                continue
            mask = inverse == i
            group_size = int(mask.sum())
            assigned = rng.multinomial(int(c), probs_k, size=group_size)
            player_points[mask, :] += k_int * assigned
            if k_int >= 2:
                player_makes[mask, :] += assigned
    return player_points, player_makes


def _normalized_weights_excluding_self(values: np.ndarray, exclude_idx: int) -> np.ndarray:
    """Like :func:`_normalized_weights`, but player ``exclude_idx`` always
    gets exactly 0 weight -- hard-zeroed before normalizing, not merely
    made unlikely -- and the all-zero fallback is uniform across every
    OTHER player, never including ``exclude_idx``. Used for assist
    attribution so a player can never be drawn as their own assister
    (CLAUDE.md risk-of-leakage-adjacent sanity: a model artifact that
    "self-assists" would be an obvious correctness bug, not a leakage one,
    but the test suite checks it with the same rigor).
    """
    values = np.clip(np.asarray(values, dtype=float), 0.0, None)
    n = len(values)
    values = values.copy()
    values[exclude_idx] = 0.0
    total = values.sum()
    if total <= _WEIGHT_EPS:
        if n <= 1:
            return np.zeros(n)
        fallback = np.full(n, 1.0 / (n - 1))
        fallback[exclude_idx] = 0.0
        return fallback
    return np.asarray(values / total, dtype=float)


def _attribute_rebounds(
    missed_fga: np.ndarray,
    off_profiles: list[PlayerSimProfile],
    def_profiles: list[PlayerSimProfile],
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Distribute each sim's missed-FGA count between the shooting team's
    offensive rebounders and the defending team's defensive rebounders.

    Returns ``(off_rebounds, def_rebounds)``, shapes ``(n_sims, n_off)``
    and ``(n_sims, n_def)``. Every missed-FGA event is assigned to exactly
    one of the ``n_off + n_def`` on-court players via a single multinomial
    draw over the concatenated ``[orb_rate_i for i in off] + [drb_rate_i
    for i in def]`` weight vector (grouped by unique missed-FGA count,
    same vectorization pattern as :func:`_sample_outcome_counts`) -- see
    module docstring's "Rebound attribution" section.
    """
    n_sims = len(missed_fga)
    n_off = len(off_profiles)
    n_def = len(def_profiles)
    off_reb = np.zeros((n_sims, n_off), dtype=float)
    def_reb = np.zeros((n_sims, n_def), dtype=float)
    off_w = np.array([max(p.orb_rate, 0.0) for p in off_profiles], dtype=float)
    def_w = np.array([max(p.drb_rate, 0.0) for p in def_profiles], dtype=float)
    probs = _normalized_weights(np.concatenate([off_w, def_w]))

    missed_fga_int = np.asarray(missed_fga, dtype=np.int64)
    unique_c, inverse = np.unique(missed_fga_int, return_inverse=True)
    for i, c in enumerate(unique_c):
        if c == 0:
            continue
        mask = inverse == i
        group_size = int(mask.sum())
        assigned = rng.multinomial(int(c), probs, size=group_size)
        off_reb[mask, :] += assigned[:, :n_off]
        def_reb[mask, :] += assigned[:, n_off:]
    return off_reb, def_reb


def _attribute_assists(
    made_by_player: np.ndarray,
    profiles: list[PlayerSimProfile],
    assisted_fg_rate: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Per-sim, per-player assist totals (shape ``(n_sims, n_players)``),
    given each player's made-field-goal counts for the same sims.

    For every player ``i``'s made-FG count, an independent
    ``rng.binomial(made_i, assisted_fg_rate)`` draw decides how many of
    those makes were assisted at all; the assisted ones are distributed
    among ``i``'s teammates (never ``i`` itself --
    :func:`_normalized_weights_excluding_self`) weighted by ``ast_rate``.
    See module docstring's "Assist attribution" section.
    """
    n_sims, n_players = made_by_player.shape
    assists_received = np.zeros((n_sims, n_players), dtype=float)
    ast_w = np.array([max(p.ast_rate, 0.0) for p in profiles], dtype=float)
    rate = float(np.clip(assisted_fg_rate, 0.0, 1.0))
    for i in range(n_players):
        made_i = np.asarray(made_by_player[:, i], dtype=np.int64)
        if made_i.sum() == 0:
            continue
        n_assisted_i = rng.binomial(made_i, rate)
        probs = _normalized_weights_excluding_self(ast_w, i)
        unique_c, inverse = np.unique(n_assisted_i, return_inverse=True)
        for u, c in enumerate(unique_c):
            if c == 0:
                continue
            mask = inverse == u
            group_size = int(mask.sum())
            assigned = rng.multinomial(int(c), probs, size=group_size)
            assists_received[mask, :] += assigned
    return assists_received


def _expected_ppp(off_rtg: float, def_rtg: float, league_avg_ppp: float) -> float:
    """Log5-style combination -- duplicated from ``research.sim.engine`` (same
    one-line formula; kept local so this module never imports a private
    helper from another module)."""
    if league_avg_ppp <= 0:
        return off_rtg
    return float(off_rtg * def_rtg / league_avg_ppp)


def simulate_game_with_players(
    home_profiles: list[PlayerSimProfile],
    away_profiles: list[PlayerSimProfile],
    home_off_rtg: float,
    home_def_rtg: float,
    home_pace: float,
    away_off_rtg: float,
    away_def_rtg: float,
    away_pace: float,
    league_avg_ppp: float,
    n_sims: int = 2000,
    seed: int = 0,
    pace_sd: float = DEFAULT_PACE_SD,
    home_ppp_bonus: float = DEFAULT_HOME_PPP_BONUS,
    home_assisted_fg_rate: float = DEFAULT_ASSISTED_FG_RATE,
    away_assisted_fg_rate: float = DEFAULT_ASSISTED_FG_RATE,
) -> PlayerGameSimResult:
    """Team possession sim (identical shape to ``research.sim.engine.simulate_game``)
    plus per-player points, rebounds, and assists attribution. Deterministic
    given ``seed``. ``home_assisted_fg_rate``/``away_assisted_fg_rate`` are
    each team's as-of "fraction of made FG that are assisted" (see
    ``research.features.player_rebound_assist_features.assisted_fg_rate_prior``);
    default to the documented league constant when a caller doesn't have a
    team-specific as-of value yet (e.g. a brand-new team-season).
    """
    support = BASE_OUTCOME_SUPPORT
    rng = np.random.default_rng(seed)

    home_target = _expected_ppp(home_off_rtg, away_def_rtg, league_avg_ppp) + home_ppp_bonus
    away_target = _expected_ppp(away_off_rtg, home_def_rtg, league_avg_ppp)
    home_probs = tilt_outcome_probs(home_target, support, BASE_OUTCOME_PROBS)
    away_probs = tilt_outcome_probs(away_target, support, BASE_OUTCOME_PROBS)

    mean_pace = (home_pace + away_pace) / 2.0
    n_poss = rng.normal(mean_pace, pace_sd, size=n_sims)
    n_poss = np.maximum(np.round(n_poss), MIN_POSSESSIONS).astype(int)

    home_counts = _sample_outcome_counts(n_poss, home_probs, rng)
    away_counts = _sample_outcome_counts(n_poss, away_probs, rng)

    home_totals = (home_counts @ support).astype(float)
    away_totals = (away_counts @ support).astype(float)

    margins = home_totals - away_totals
    totals = home_totals + away_totals
    home_wins = (margins > 0).astype(float)
    ties = margins == 0
    if ties.any():
        home_wins[ties] = rng.integers(0, 2, size=int(ties.sum())).astype(float)

    home_result = GameSimResult(
        win_prob_home=float(home_wins.mean()),
        margin_mean=float(margins.mean()),
        margin_sd=float(margins.std(ddof=1)) if n_sims > 1 else 0.0,
        total_mean=float(totals.mean()),
        total_sd=float(totals.std(ddof=1)) if n_sims > 1 else 0.0,
        home_score_mean=float(home_totals.mean()),
        away_score_mean=float(away_totals.mean()),
        n_sims=n_sims,
        seed=seed,
        n_poss_mean=float(n_poss.mean()),
    )
    away_result = GameSimResult(
        win_prob_home=1.0 - home_result.win_prob_home,
        margin_mean=-home_result.margin_mean,
        margin_sd=home_result.margin_sd,
        total_mean=home_result.total_mean,
        total_sd=home_result.total_sd,
        home_score_mean=home_result.away_score_mean,
        away_score_mean=home_result.home_score_mean,
        n_sims=n_sims,
        seed=seed,
        n_poss_mean=home_result.n_poss_mean,
    )

    home_weights_by_k = _roster_weights_by_outcome(home_profiles, support)
    away_weights_by_k = _roster_weights_by_outcome(away_profiles, support)
    home_player_points, home_player_makes = _attribute_points_and_makes(
        home_counts, support, home_weights_by_k, len(home_profiles), rng
    )
    away_player_points, away_player_makes = _attribute_points_and_makes(
        away_counts, support, away_weights_by_k, len(away_profiles), rng
    )

    # Rebounds: split the documented share of each team's zero-point
    # possessions (missed FGAs) between that team's offensive rebounders
    # and the opponent's defensive rebounders -- see module docstring.
    zero_idx = int(np.where(support == 0)[0][0])
    home_missed_fga = rng.binomial(
        home_counts[:, zero_idx].astype(np.int64), MISSED_FG_SHARE_OF_ZERO_OUTCOME
    )
    away_missed_fga = rng.binomial(
        away_counts[:, zero_idx].astype(np.int64), MISSED_FG_SHARE_OF_ZERO_OUTCOME
    )
    home_oreb_from_own_miss, away_dreb_from_home_miss = _attribute_rebounds(
        home_missed_fga, home_profiles, away_profiles, rng
    )
    away_oreb_from_own_miss, home_dreb_from_away_miss = _attribute_rebounds(
        away_missed_fga, away_profiles, home_profiles, rng
    )
    home_player_rebounds = home_oreb_from_own_miss + home_dreb_from_away_miss
    away_player_rebounds = away_oreb_from_own_miss + away_dreb_from_home_miss

    # Assists: each team's own made-FG counts, assisted-or-not + assigned
    # to a teammate per team's own as-of assisted-FG rate -- see module
    # docstring.
    home_player_assists = _attribute_assists(
        home_player_makes, home_profiles, home_assisted_fg_rate, rng
    )
    away_player_assists = _attribute_assists(
        away_player_makes, away_profiles, away_assisted_fg_rate, rng
    )

    home_point_dists = {
        p.player_id: EmpiricalPointsDist(home_player_points[:, i])
        for i, p in enumerate(home_profiles)
    }
    away_point_dists = {
        p.player_id: EmpiricalPointsDist(away_player_points[:, i])
        for i, p in enumerate(away_profiles)
    }
    home_reb_dists = {
        p.player_id: EmpiricalPointsDist(home_player_rebounds[:, i])
        for i, p in enumerate(home_profiles)
    }
    away_reb_dists = {
        p.player_id: EmpiricalPointsDist(away_player_rebounds[:, i])
        for i, p in enumerate(away_profiles)
    }
    home_ast_dists = {
        p.player_id: EmpiricalPointsDist(home_player_assists[:, i])
        for i, p in enumerate(home_profiles)
    }
    away_ast_dists = {
        p.player_id: EmpiricalPointsDist(away_player_assists[:, i])
        for i, p in enumerate(away_profiles)
    }

    return PlayerGameSimResult(
        home=home_result,
        away=away_result,
        home_player_points=home_point_dists,
        away_player_points=away_point_dists,
        home_player_rebounds=home_reb_dists,
        away_player_rebounds=away_reb_dists,
        home_player_assists=home_ast_dists,
        away_player_assists=away_ast_dists,
        n_sims=n_sims,
        seed=seed,
    )


#: Per-stat thresholds reported in the prop_predictions-shaped output below
#: -- mirror ``nba.props.config.THRESHOLDS`` (duplicated as literals, not
#: imported, to keep this module free of a hard dependency on ``nba/props``
#: beyond what callers choose to wire in themselves).
POINTS_THRESHOLDS: list[int] = [10, 15, 20, 25, 30]
REBOUNDS_THRESHOLDS: list[int] = [2, 4, 6, 8, 10]
ASSISTS_THRESHOLDS: list[int] = [2, 4, 6, 8]


def _prediction_rows_for_stat(
    game_id: str,
    stat: str,
    home_dists: dict[int, EmpiricalPointsDist],
    away_dists: dict[int, EmpiricalPointsDist],
    thresholds: list[int],
) -> list[dict[str, object]]:
    """Shared row-builder behind every ``player_*_prediction_rows`` function
    below -- one ``prop_predictions``-shaped (CLAUDE.md data schema) dict
    per (side, player). Plain dicts (no DB write); callers that want rows in
    the actual ``prop_predictions`` table should pass this through
    ``research.props.run.write_prop_predictions``-style insertion themselves
    (this module stays DB-free, consistent with its no-leakage design).
    """
    import json

    rows: list[dict[str, object]] = []
    for side, dists in (("home", home_dists), ("away", away_dists)):
        for player_id, dist in dists.items():
            p_ge = {str(th): dist.p_ge(float(th)) for th in thresholds}
            rows.append(
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "side": side,
                    "stat": stat,
                    "mean": dist.mean(),
                    "dist_family": dist.family,
                    "dist_params": json.dumps(dist.params()),
                    "p_ge": json.dumps(p_ge),
                    "q10": dist.ppf(0.10),
                    "q50": dist.ppf(0.50),
                    "q90": dist.ppf(0.90),
                }
            )
    return rows


def player_points_prediction_rows(
    game_id: str,
    result: PlayerGameSimResult,
    thresholds: list[int] = POINTS_THRESHOLDS,
) -> list[dict[str, object]]:
    """Per-player sim points summary in the ``prop_predictions`` shape --
    the sim's version of a points prop. See :func:`_prediction_rows_for_stat`.
    """
    return _prediction_rows_for_stat(
        game_id, "pts", result.home_player_points, result.away_player_points, thresholds
    )


def player_rebounds_prediction_rows(
    game_id: str,
    result: PlayerGameSimResult,
    thresholds: list[int] = REBOUNDS_THRESHOLDS,
) -> list[dict[str, object]]:
    """Per-player sim rebounds summary in the ``prop_predictions`` shape --
    the sim's version of a rebounds prop. See :func:`_prediction_rows_for_stat`.
    """
    return _prediction_rows_for_stat(
        game_id, "reb", result.home_player_rebounds, result.away_player_rebounds, thresholds
    )


def player_assists_prediction_rows(
    game_id: str,
    result: PlayerGameSimResult,
    thresholds: list[int] = ASSISTS_THRESHOLDS,
) -> list[dict[str, object]]:
    """Per-player sim assists summary in the ``prop_predictions`` shape --
    the sim's version of an assists prop. See :func:`_prediction_rows_for_stat`.
    """
    return _prediction_rows_for_stat(
        game_id, "ast", result.home_player_assists, result.away_player_assists, thresholds
    )


def profiles_from_features(
    shot_rates: pl.DataFrame,
    projected_minutes: dict[int, float],
    player_ids: list[int],
    reb_ast_rates: pl.DataFrame | None = None,
) -> list[PlayerSimProfile]:
    """Assemble :class:`PlayerSimProfile` rows for one game from already-
    computed as-of feature frames.

    ``shot_rates`` is one game's slice of
    ``research.features.player_possession_features.build_player_shot_rates``'s
    output (already as-of by construction -- see that module); keyed by
    ``player_id``. ``projected_minutes`` maps ``player_id -> projected
    (never actual) minutes`` for this game, typically
    ``nba.props.minutes.predict_minutes(...)[i].mean()``. ``reb_ast_rates``
    is optionally one game's slice of
    ``research.features.player_rebound_assist_features.build_player_reb_ast_rates``'s
    output (same as-of guarantee); when omitted (the default), every
    profile's ``orb_rate``/``drb_rate``/``ast_rate`` stay at the dataclass
    default of 0.0 (no rebound/assist weight -- callers who only care about
    points, e.g. existing call sites predating this feature, are
    unaffected). Any ``player_id`` in ``player_ids`` missing from
    ``shot_rates`` (or, if given, ``reb_ast_rates``) gets the league-wide
    default rates (brand-new player, zero as-of history) rather than being
    silently dropped.
    """
    from research.features.player_possession_features import (
        LEAGUE_FT_PCT_DEFAULT,
        LEAGUE_FT_TRIP_RATE_DEFAULT,
        LEAGUE_SHOT_SHARE_DEFAULT,
        LEAGUE_ZONE_FG_PCT_DEFAULT,
        LEAGUE_ZONE_MIX_DEFAULT,
    )
    from research.features.player_rebound_assist_features import (
        LEAGUE_AST_RATE_DEFAULT,
        LEAGUE_DRB_RATE_DEFAULT,
        LEAGUE_ORB_RATE_DEFAULT,
    )

    by_player = {int(row["player_id"]): row for row in shot_rates.iter_rows(named=True)}
    reb_ast_by_player: dict[int, dict[str, Any]] = {}
    if reb_ast_rates is not None:
        reb_ast_by_player = {
            int(row["player_id"]): row for row in reb_ast_rates.iter_rows(named=True)
        }

    def _reb_ast_rates(pid: int) -> tuple[float, float, float]:
        row = reb_ast_by_player.get(int(pid))
        if row is None:
            return (LEAGUE_ORB_RATE_DEFAULT, LEAGUE_DRB_RATE_DEFAULT, LEAGUE_AST_RATE_DEFAULT)
        return (
            float(row["orb_rate_prior"]),
            float(row["drb_rate_prior"]),
            float(row["ast_rate_prior"]),
        )

    profiles = []
    for pid in player_ids:
        row = by_player.get(int(pid))
        mins = float(projected_minutes.get(pid, 0.0))
        orb_rate, drb_rate, ast_rate = (
            _reb_ast_rates(pid) if reb_ast_rates is not None else (0.0, 0.0, 0.0)
        )
        if row is None:
            profiles.append(
                PlayerSimProfile(
                    player_id=pid,
                    shot_share=LEAGUE_SHOT_SHARE_DEFAULT,
                    zone_mix=(
                        LEAGUE_ZONE_MIX_DEFAULT["rim"],
                        LEAGUE_ZONE_MIX_DEFAULT["mid"],
                        LEAGUE_ZONE_MIX_DEFAULT["above3"],
                    ),
                    zone_make_prob=(
                        LEAGUE_ZONE_FG_PCT_DEFAULT["rim"],
                        LEAGUE_ZONE_FG_PCT_DEFAULT["mid"],
                        LEAGUE_ZONE_FG_PCT_DEFAULT["above3"],
                    ),
                    ft_trip_rate=LEAGUE_FT_TRIP_RATE_DEFAULT,
                    ft_pct=LEAGUE_FT_PCT_DEFAULT,
                    projected_minutes=mins,
                    orb_rate=orb_rate,
                    drb_rate=drb_rate,
                    ast_rate=ast_rate,
                )
            )
            continue
        profiles.append(
            PlayerSimProfile(
                player_id=pid,
                shot_share=float(row["shot_share_prior"]),
                zone_mix=(
                    float(row["zone_mix_rim_prior"]),
                    float(row["zone_mix_mid_prior"]),
                    float(row["zone_mix_above3_prior"]),
                ),
                zone_make_prob=(
                    float(row["zone_fg_pct_rim_prior"]),
                    float(row["zone_fg_pct_mid_prior"]),
                    float(row["zone_fg_pct_above3_prior"]),
                ),
                ft_trip_rate=float(row["ft_trip_rate_prior"]),
                ft_pct=float(row["ft_pct_prior"]),
                projected_minutes=mins,
                orb_rate=orb_rate,
                drb_rate=drb_rate,
                ast_rate=ast_rate,
            )
        )
    return profiles


__all__ = [
    "ASSISTS_THRESHOLDS",
    "DEFAULT_ASSISTED_FG_RATE",
    "MISSED_FG_SHARE_OF_ZERO_OUTCOME",
    "POINTS_THRESHOLDS",
    "REBOUNDS_THRESHOLDS",
    "TEAM_MINUTES_PER_GAME",
    "EmpiricalPointsDist",
    "PlayerGameSimResult",
    "PlayerSimProfile",
    "player_assists_prediction_rows",
    "player_points_prediction_rows",
    "player_rebounds_prediction_rows",
    "profiles_from_features",
    "simulate_game_with_players",
]
