"""Rung-3 possession-by-possession Monte Carlo engine (CLAUDE.md architecture ladder).

``simulate_game`` takes two teams' as-of possession rates (shrunk
off/def rating + pace, from ``nba.features.possession_features``) and
runs ``n_sims`` independent possession-by-possession simulations of the
game, returning win probability, margin distribution, and total points
distribution. Deterministic given ``seed``.

Possession-outcome combination: the two teams' as-of offensive/defensive
ratings are combined log5-style (standard sports-analytics efficiency
combination, same idea as Bill James' log5 for win probabilities, applied
here to points-per-possession):

    expected_ppp(offense, defense) = off_rtg * def_rtg / league_avg_ppp

i.e. a league-average defense against a league-average offense reproduces
the league-average points-per-possession; a better-than-average offense
or a worse-than-average defense both push the expected rate up. A small,
configurable, documented home-court bonus is added to the home team's
expected PPP only (CLAUDE.md's home-court effect, kept inside the sim
rather than as a separate baseline here since rung 0 already owns that
baseline in isolation).

Possession count ("pace"): a single possession count per game is sampled
from ``Normal((home_pace + away_pace) / 2, pace_sd)`` and applied to both
teams (both teams get essentially the same number of possessions in a
real game -- they alternate -- so this is one shared draw, not two
independent ones; see module docstring tests for this design choice).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nba.sim.possession_model import (
    BASE_OUTCOME_PROBS,
    BASE_OUTCOME_SUPPORT,
    tilt_outcome_probs,
)

#: Possession-count standard deviation (per game, shared by both teams --
#: see module docstring). ~6 possessions/game is a reasonable rough
#: game-to-game pace swing given a team's own season-long pace SD is
#: typically only a couple of possessions but single games vary more
#: (overtime, foul-heavy/transition-heavy games, garbage time).
DEFAULT_PACE_SD = 6.0

#: Minimum possessions per team per game a sim is allowed to draw --
#: guards against a pathological negative/near-zero draw from the normal
#: sampler at extreme pace_sd configs.
MIN_POSSESSIONS = 60

#: Default home-court bonus added to the home team's expected
#: points-per-possession only. ~0.02 PPP * ~100 possessions ~= 2 points,
#: a conservative slice of the real ~2-3 point NBA home-court edge
#: (the rest is implicitly carried by ratings already reflecting home/away
#: splits in the underlying box scores). Documented default, not tuned.
DEFAULT_HOME_PPP_BONUS = 0.02


@dataclass
class GameSimResult:
    """Monte Carlo output for one game (CLAUDE.md "Sim outputs")."""

    win_prob_home: float
    margin_mean: float
    margin_sd: float
    total_mean: float
    total_sd: float
    home_score_mean: float
    away_score_mean: float
    n_sims: int
    seed: int
    n_poss_mean: float


def _expected_ppp(off_rtg: float, def_rtg: float, league_avg_ppp: float) -> float:
    """Log5-style combination of an offense's and a defense's as-of rating."""
    if league_avg_ppp <= 0:
        return off_rtg
    return float(off_rtg * def_rtg / league_avg_ppp)


def _sample_points(
    n_poss: np.ndarray,
    probs: np.ndarray,
    support: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Vectorized total points across ``n_sims`` games given a per-sim possession count.

    ``n_poss`` varies per sim (pace is itself sampled), so possession
    counts are grouped by their unique value and each group gets one
    batched ``rng.multinomial(n, probs, size=group_size)`` call -- this
    keeps the whole draw vectorized (no per-possession Python loop) while
    still allowing pace to vary sim-to-sim.
    """
    n_sims = len(n_poss)
    totals = np.empty(n_sims, dtype=float)
    unique_n, inverse = np.unique(n_poss, return_inverse=True)
    for i, n in enumerate(unique_n):
        mask = inverse == i
        group_size = int(mask.sum())
        if group_size == 0:
            continue
        counts = rng.multinomial(int(n), probs, size=group_size)
        totals[mask] = counts @ support
    return totals


def simulate_game(
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
    outcome_support: np.ndarray | None = None,
    outcome_probs: np.ndarray | None = None,
) -> GameSimResult:
    """Run ``n_sims`` possession-by-possession simulations of one game.

    Deterministic given ``seed`` (a fresh ``np.random.default_rng(seed)``
    is created per call -- no shared global RNG state).
    """
    support = BASE_OUTCOME_SUPPORT if outcome_support is None else np.asarray(outcome_support)
    base_probs = BASE_OUTCOME_PROBS if outcome_probs is None else np.asarray(outcome_probs)
    rng = np.random.default_rng(seed)

    home_target = _expected_ppp(home_off_rtg, away_def_rtg, league_avg_ppp) + home_ppp_bonus
    away_target = _expected_ppp(away_off_rtg, home_def_rtg, league_avg_ppp)
    home_probs = tilt_outcome_probs(home_target, support, base_probs)
    away_probs = tilt_outcome_probs(away_target, support, base_probs)

    mean_pace = (home_pace + away_pace) / 2.0
    n_poss = rng.normal(mean_pace, pace_sd, size=n_sims)
    n_poss = np.maximum(np.round(n_poss), MIN_POSSESSIONS).astype(int)

    home_scores = _sample_points(n_poss, home_probs, support, rng)
    away_scores = _sample_points(n_poss, away_probs, support, rng)

    margins = home_scores - away_scores
    totals = home_scores + away_scores
    # Possession-level sims occasionally tie (two teams scoring the same
    # integer total over the same number of possessions); real NBA games
    # resolve ties via overtime, which this rung-3 team-level model does
    # not simulate separately -- a tied game is scored as a coin-flip win
    # for either side, consistent with it being a genuinely ~50/50 game.
    home_wins = (margins > 0).astype(float)
    ties = margins == 0
    if ties.any():
        home_wins[ties] = rng.integers(0, 2, size=int(ties.sum())).astype(float)

    return GameSimResult(
        win_prob_home=float(home_wins.mean()),
        margin_mean=float(margins.mean()),
        margin_sd=float(margins.std(ddof=1)) if n_sims > 1 else 0.0,
        total_mean=float(totals.mean()),
        total_sd=float(totals.std(ddof=1)) if n_sims > 1 else 0.0,
        home_score_mean=float(home_scores.mean()),
        away_score_mean=float(away_scores.mean()),
        n_sims=n_sims,
        seed=seed,
        n_poss_mean=float(n_poss.mean()),
    )
