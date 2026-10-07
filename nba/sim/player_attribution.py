"""Per-player points attribution on top of the rung-3 team possession sim.

CLAUDE.md architecture ladder, rung 3: "one logistic/GBM head per step
(duration, outcome, shooter, zone, make, rebound, FT) ... Full-game
distributions, **player lines**." This module is the payoff of the sim
pivot: :func:`simulate_game_with_players` runs the same team-level
possession Monte Carlo as ``nba.sim.engine.simulate_game`` (same support/
tilt machinery from ``nba.sim.possession_model``, same deterministic-given-
seed contract) and additionally attributes every scoring possession's
points to one on-court player via a shooter -> zone -> make/miss -> FT
chain. Scope this milestone is **points only** (CLAUDE.md: rebounds/
assists are a later milestone).

## Design: how player attribution stays consistent with the team-level sim

The team-level outcome distribution (how many 0/1/2/3/.../8-point
possessions a team has in a simulated game) is drawn *exactly* the same way
``nba.sim.engine._sample_points`` draws it: group simulated games by their
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
``nba.sim.engine.GameSimResult``) is the *same* possession-outcome draw
``nba.sim.engine.simulate_game`` would produce for the same inputs (same
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
(``nba.features.player_possession_features``) and ``nba.props.minutes``.
See ``tests/ml/test_player_attribution.py::test_no_leakage_profiles_from_features``
for the planted-future-game proof at the feature-assembly boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from nba.sim.engine import (
    DEFAULT_HOME_PPP_BONUS,
    DEFAULT_PACE_SD,
    MIN_POSSESSIONS,
    GameSimResult,
)
from nba.sim.possession_model import (
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


@dataclass(frozen=True)
class PlayerSimProfile:
    """One player's as-of rates + projected (never actual) minutes for one game.

    Every field is expected to already be shrunk/as-of (see
    ``nba.features.player_possession_features.build_player_shot_rates`` for
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


@dataclass
class EmpiricalPointsDist:
    """Empirical per-player points distribution from the sim's own samples.

    Duck-types ``nba.props.distributions.Distribution`` (``mean``/``cdf``/
    ``sf``/``p_ge``/``ppf``/``params``/``family``) so it can be scored with
    the *existing* ``nba.props.metrics`` CRPS/threshold-log-loss/coverage
    functions unchanged -- see ``nba.eval.player_points_sim_eval`` (this
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
      schema; see ``nba.features.player_possession_features`` module
      docstring on why there is no separate corner/above-break split).
    - ``k >= 3``: a made 3-point field goal (``k in (3, 4)`` for a clean
      3 or a 3-and-1 FT bonus; ``k >= 5`` is empirically negligible per
      ``nba.sim.possession_model``'s documented base shape but handled the
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
    ``nba.sim.engine._sample_points``, kept as a separate, duplicated
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


def _attribute_points(
    outcome_counts: np.ndarray,
    support: np.ndarray,
    weights_by_k: dict[int, np.ndarray],
    n_players: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Per-sim, per-player point totals (shape ``(n_sims, n_players)``).

    For every outcome value ``k`` with ``count > 0`` in some sims, draws a
    nested multinomial distributing those ``count`` k-point possessions
    across the roster, grouped by unique count value (same vectorization
    pattern as :func:`_sample_outcome_counts`). The sum over players always
    equals ``outcome_counts @ support`` exactly -- see module docstring.
    """
    n_sims = outcome_counts.shape[0]
    player_points = np.zeros((n_sims, n_players), dtype=float)
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
    return player_points


def _expected_ppp(off_rtg: float, def_rtg: float, league_avg_ppp: float) -> float:
    """Log5-style combination -- duplicated from ``nba.sim.engine`` (same
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
) -> PlayerGameSimResult:
    """Team possession sim (identical shape to ``nba.sim.engine.simulate_game``)
    plus per-player points attribution. Deterministic given ``seed``.
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
    home_player_points = _attribute_points(
        home_counts, support, home_weights_by_k, len(home_profiles), rng
    )
    away_player_points = _attribute_points(
        away_counts, support, away_weights_by_k, len(away_profiles), rng
    )

    home_dists = {
        p.player_id: EmpiricalPointsDist(home_player_points[:, i])
        for i, p in enumerate(home_profiles)
    }
    away_dists = {
        p.player_id: EmpiricalPointsDist(away_player_points[:, i])
        for i, p in enumerate(away_profiles)
    }

    return PlayerGameSimResult(
        home=home_result,
        away=away_result,
        home_player_points=home_dists,
        away_player_points=away_dists,
        n_sims=n_sims,
        seed=seed,
    )


#: Points thresholds reported in the prop_predictions-shaped output below --
#: mirrors ``nba.props.config.THRESHOLDS["pts"]`` (duplicated as a literal,
#: not imported, to keep this module free of a hard dependency on
#: ``nba/props`` beyond what callers choose to wire in themselves).
POINTS_THRESHOLDS: list[int] = [10, 15, 20, 25, 30]


def player_points_prediction_rows(
    game_id: str,
    result: PlayerGameSimResult,
    thresholds: list[int] = POINTS_THRESHOLDS,
) -> list[dict[str, object]]:
    """Per-player sim points summary in the ``prop_predictions`` shape
    (CLAUDE.md data schema) -- the sim's version of a points prop. Plain
    dicts (no DB write); callers that want rows in the actual
    ``prop_predictions`` table should pass this through
    ``nba.props.run.write_prop_predictions``-style insertion themselves
    (this module stays DB-free, consistent with its no-leakage design).
    """
    import json

    rows: list[dict[str, object]] = []
    for side, dists in (("home", result.home_player_points), ("away", result.away_player_points)):
        for player_id, dist in dists.items():
            p_ge = {str(th): dist.p_ge(float(th)) for th in thresholds}
            rows.append(
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "side": side,
                    "stat": "pts",
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


def profiles_from_features(
    shot_rates: pl.DataFrame,
    projected_minutes: dict[int, float],
    player_ids: list[int],
) -> list[PlayerSimProfile]:
    """Assemble :class:`PlayerSimProfile` rows for one game from already-
    computed as-of feature frames.

    ``shot_rates`` is one game's slice of
    ``nba.features.player_possession_features.build_player_shot_rates``'s
    output (already as-of by construction -- see that module); keyed by
    ``player_id``. ``projected_minutes`` maps ``player_id -> projected
    (never actual) minutes`` for this game, typically
    ``nba.props.minutes.predict_minutes(...)[i].mean()``. Any
    ``player_id`` in ``player_ids`` missing from ``shot_rates`` gets the
    league-wide default rates (brand-new player, zero as-of history) rather
    than being silently dropped.
    """
    from nba.features.player_possession_features import (
        LEAGUE_FT_PCT_DEFAULT,
        LEAGUE_FT_TRIP_RATE_DEFAULT,
        LEAGUE_SHOT_SHARE_DEFAULT,
        LEAGUE_ZONE_FG_PCT_DEFAULT,
        LEAGUE_ZONE_MIX_DEFAULT,
    )

    by_player = {int(row["player_id"]): row for row in shot_rates.iter_rows(named=True)}
    profiles = []
    for pid in player_ids:
        row = by_player.get(int(pid))
        mins = float(projected_minutes.get(pid, 0.0))
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
            )
        )
    return profiles


__all__ = [
    "POINTS_THRESHOLDS",
    "TEAM_MINUTES_PER_GAME",
    "EmpiricalPointsDist",
    "PlayerGameSimResult",
    "PlayerSimProfile",
    "player_points_prediction_rows",
    "profiles_from_features",
    "simulate_game_with_players",
]
