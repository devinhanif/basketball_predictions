"""Metric slices required by CLAUDE.md's "Evaluation" section.

Each function returns a boolean numpy-compatible polars Series mask over a
matchup-feature DataFrame (see ``nba.features.team_features.
build_matchup_features``). ``nba.eval.report`` applies these masks to
slice log loss / Brier / calibration per CLAUDE.md: "cold-start bucket,
home/away, rest, season phase (first 15 games vs. rest), and
favorite/underdog".
"""

from __future__ import annotations

import numpy as np
import polars as pl

#: Games where either team enters with fewer than this many *games* of
#: history. A real cold-start bucket (CLAUDE.md: ">=1 starter has <500
#: career possessions") needs ``player_rates.n_poss``, which belongs to the
#: next milestone (cold-start methods 1-4). This games-count threshold is a
#: clearly-labeled placeholder hook so the walk-forward report already has
#: a cold-start column to fill in once that table is populated.
EARLY_SEASON_GAMES_THRESHOLD = 15
COLD_START_GAMES_THRESHOLD = 15


def rest_slices(df: pl.DataFrame) -> dict[str, np.ndarray]:
    home_b2b = df.select(pl.col("home_b2b")).to_series().to_numpy()
    away_b2b = df.select(pl.col("away_b2b")).to_series().to_numpy()
    return {
        "either_team_b2b": home_b2b | away_b2b,
        "no_b2b": ~(home_b2b | away_b2b),
    }


def season_phase_slices(df: pl.DataFrame) -> dict[str, np.ndarray]:
    min_games = df.select(pl.col("games_played_prior_min")).to_series().to_numpy()
    return {
        "first_15_games": min_games < EARLY_SEASON_GAMES_THRESHOLD,
        "rest_of_season": min_games >= EARLY_SEASON_GAMES_THRESHOLD,
    }


def favorite_underdog_slices(p_home: np.ndarray) -> dict[str, np.ndarray]:
    """Favorite = side the model gives >=0.5; this slices the home row's
    perspective (p_home is P(home win))."""
    return {
        "home_favorite": p_home >= 0.5,
        "home_underdog": p_home < 0.5,
    }


def cold_start_bucket_slice(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """Placeholder cold-start bucket -- see module docstring.

    TODO(next milestone): replace ``games_played_prior_min`` with a true
    possession-count threshold from ``player_rates.n_poss`` once cold-start
    methods 1-4 are implemented and that table is populated per-player.
    """
    min_games = df.select(pl.col("games_played_prior_min")).to_series().to_numpy()
    return {
        "cold_start_bucket_placeholder_low_history": min_games < COLD_START_GAMES_THRESHOLD,
        "cold_start_bucket_placeholder_warm": min_games >= COLD_START_GAMES_THRESHOLD,
    }
