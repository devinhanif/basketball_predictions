"""Rung 3: possession-by-possession Monte Carlo simulation.

CLAUDE.md architecture ladder, rung 3: "Possession Monte Carlo sim, one
logistic/GBM head per step ... Full-game distributions, player lines."
This first version is scoped to the team-level game head only (win prob /
margin / total) -- see ``research/sim/engine.py``'s module docstring for the
possession-outcome model and ``research/features/possession_features.py`` for
the as-of team ratings it consumes. Per-player shooter/zone/assist heads
are a later milestone.

Unlike rungs 1-2, this rung has no trainable parameters fit from
``(X, y)`` -- all the "fitting" already happened upstream, as-of, inside
``research.features.possession_features`` (the shrunk off/def ratings). ``fit``
here is therefore a light bookkeeping no-op (records how many training
games were available, for ``get_config``), consistent with the
3-method contract (every rung still implements ``fit``/``predict``/
``get_metrics``/``get_config`` identically).
"""

from __future__ import annotations

import hashlib

import numpy as np
import polars as pl

from nba.models.base import RungModelBase
from research.features.possession_features import PACE_DEFAULT
from research.sim.engine import (
    DEFAULT_HOME_PPP_BONUS,
    DEFAULT_PACE_SD,
    GameSimResult,
    simulate_game,
)

#: Feature columns this rung expects on every row of ``df`` passed to
#: ``predict``/``simulate_full`` -- see
#: ``research.features.possession_features.build_possession_matchup_features``.
POSSESSION_SIM_FEATURE_COLUMNS: list[str] = [
    "home_off_rtg_prior",
    "home_def_rtg_prior",
    "home_pace_prior",
    "away_off_rtg_prior",
    "away_def_rtg_prior",
    "away_pace_prior",
    "league_avg_ppp_asof",
]

#: League-average PPP fallback if a row is somehow missing the column
#: entirely (e.g. a caller passing a bare toy DataFrame in a unit test
#: without joining ``build_possession_matchup_features``) -- matches
#: ``research.features.possession_features.LEAGUE_AVG_PPP_DEFAULT``.
_LEAGUE_AVG_PPP_FALLBACK = 1.146


class PossessionSimRung(RungModelBase):
    """Monte Carlo possession sim, wrapped in the rung 3-method contract."""

    def __init__(
        self,
        seed: int = 0,
        n_sims: int = 2000,
        pace_sd: float = DEFAULT_PACE_SD,
        home_ppp_bonus: float = DEFAULT_HOME_PPP_BONUS,
    ) -> None:
        super().__init__(seed=seed)
        self.n_sims = n_sims
        self.pace_sd = pace_sd
        self.home_ppp_bonus = home_ppp_bonus
        self.n_train_games_: int = 0

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        # No learned parameters (see module docstring); only bookkeeping.
        self.n_train_games_ = train_df.height

    def _row_seed(self, game_id: str, row_index: int) -> int:
        """Per-game seed derived deterministically from ``self.seed`` + game_id.

        Keeps the whole rung deterministic given ``self.seed`` while
        giving every game its own independent sim stream (so game A's sim
        draws can never be identical to game B's purely because they
        happened to share a possession-count draw order). Uses
        ``hashlib`` (stable across processes/machines) rather than
        Python's built-in ``hash()``, which is salted per-process
        (``PYTHONHASHSEED``) for strings and would make "same seed -> same
        predictions" only true *within* a single process, not a real
        reproducibility guarantee.
        """
        payload = f"{self.seed}:{game_id}:{row_index}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        return int(digest[:8], 16) % (2**31 - 1)

    def simulate_full(self, df: pl.DataFrame) -> list[GameSimResult]:
        """Full sim output (win prob, margin, total) per row -- not just win prob."""
        for col in POSSESSION_SIM_FEATURE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(col))
        rows = df.select(["game_id", *POSSESSION_SIM_FEATURE_COLUMNS]).fill_null(
            _LEAGUE_AVG_PPP_FALLBACK
        )
        # pace columns should fall back to the pace default, not the PPP
        # default, if ever missing -- overwrite those two specifically
        # after the blanket fill_null above.
        rows = rows.with_columns(
            [
                pl.col("home_pace_prior").fill_null(PACE_DEFAULT),
                pl.col("away_pace_prior").fill_null(PACE_DEFAULT),
            ]
        )
        results: list[GameSimResult] = []
        for i, row in enumerate(rows.iter_rows(named=True)):
            result = simulate_game(
                home_off_rtg=row["home_off_rtg_prior"],
                home_def_rtg=row["home_def_rtg_prior"],
                home_pace=row["home_pace_prior"],
                away_off_rtg=row["away_off_rtg_prior"],
                away_def_rtg=row["away_def_rtg_prior"],
                away_pace=row["away_pace_prior"],
                league_avg_ppp=row["league_avg_ppp_asof"],
                n_sims=self.n_sims,
                seed=self._row_seed(row["game_id"], i),
                pace_sd=self.pace_sd,
                home_ppp_bonus=self.home_ppp_bonus,
            )
            results.append(result)
        return results

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        results = self.simulate_full(df)
        return np.array([r.win_prob_home for r in results], dtype=float)

    def get_config(self) -> dict[str, object]:
        return {
            "model_name": "rung3_sim",
            "rung": 3,
            "seed": self.seed,
            "n_sims": self.n_sims,
            "pace_sd": self.pace_sd,
            "home_ppp_bonus": self.home_ppp_bonus,
            "n_train_games": self.n_train_games_,
            "feature_columns": POSSESSION_SIM_FEATURE_COLUMNS,
            "outcome_model": "exponential-tilt of empirical league possession-outcome shape "
            "(see research.sim.possession_model); expected PPP combined log5-style from "
            "as-of shrunk off/def ratings (research.features.possession_features)",
        }
