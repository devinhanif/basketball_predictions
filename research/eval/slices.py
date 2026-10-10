"""Metric slices required by CLAUDE.md's "Evaluation" section.

Each function returns a boolean numpy-compatible polars Series mask over a
matchup-feature DataFrame (see ``nba.features.team_features.
build_matchup_features``). ``research.eval.report`` applies these masks to
slice log loss / Brier / calibration per CLAUDE.md: "cold-start bucket,
home/away, rest, season phase (first 15 games vs. rest), and
favorite/underdog".
"""

from __future__ import annotations

import numpy as np
import polars as pl

#: Season-phase slice only (first 15 games vs rest) -- unrelated to the
#: cold-start bucket below, kept as its own threshold per CLAUDE.md
#: "Evaluation": "season phase (first 15 games vs. rest)".
EARLY_SEASON_GAMES_THRESHOLD = 15


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
    """Real cold-start bucket per CLAUDE.md: ">=1 starter has <500 career possessions".

    ``df`` must carry an ``any_starter_cold_start`` boolean column -- see
    ``research.features.player_features.build_game_cold_start_flags``, which
    computes it from a documented minutes-based *proxy* for career
    possessions (the possessions table is empty pending the PBP parser).
    Games with no player-level data at all (``any_starter_cold_start`` is
    null, e.g. ``player_game_stats`` not joined) are conservatively treated
    as cold-start: there is no positive evidence any starter is warmed up.
    """
    flag = (
        df.select(pl.col("any_starter_cold_start").fill_null(True)).to_series().to_numpy()
    ).astype(bool)
    return {
        "cold_start_bucket_low_career_poss": flag,
        "cold_start_bucket_warm": ~flag,
    }
