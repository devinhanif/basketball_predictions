"""Rung 0: floor/ceiling baselines (CLAUDE.md architecture ladder).

Two baselines, both deterministic and seeded (though neither uses
randomness in practice -- the seed is still logged for the registry's
audit-metadata contract):

- ``HomeCourtBaseline``: a single constant P(home win), fit as the
  training fold's empirical home-win rate (falls back to a configured
  prior if the fold is empty).
- ``EloBaseline``: a classic sequential Elo rating, updated strictly in
  game order using only *prior* games' actual outcomes -- i.e. the rating
  used to predict a game never includes that game's own result. Ratings
  regress toward the mean at each season boundary (``season_carryover``).

Closing-line implied probability (CLAUDE.md's third rung-0 baseline) is
explicitly a stub: no market-odds source is wired into the pipeline yet
(Phase 3 / Kalshi ingestion owns that), and CLAUDE.md forbids fabricating
lines. ``ClosingLineStub.predict`` raises ``NotImplementedError`` with a
pointer to where it will be wired in.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.models.base import RungModelBase
from nba.models.common import clip_prob


class HomeCourtBaseline(RungModelBase):
    """Constant P(home win), estimated from the training fold's win rate."""

    def __init__(self, seed: int = 0, prior_home_win_rate: float = 0.5) -> None:
        super().__init__(seed=seed)
        self.prior_home_win_rate = prior_home_win_rate
        self.home_win_rate_: float = prior_home_win_rate

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        rate = float(y.mean()) if len(y) else self.prior_home_win_rate
        # Clipped so a small/unanimous training fold (e.g. the fixture)
        # never produces an overconfident p=1.0/0.0 that blows up log loss
        # on the first disagreeing test game -- a calibration choice, not a
        # correctness bug: a true constant-probability baseline should
        # never claim certainty from a handful of games.
        self.home_win_rate_ = float(clip_prob(np.array([rate]))[0])

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        return np.full(df.height, self.home_win_rate_, dtype=float)

    def get_config(self) -> dict[str, object]:
        return {
            "model_name": "rung0_home_court",
            "rung": 0,
            "seed": self.seed,
            "prior_home_win_rate": self.prior_home_win_rate,
            "fitted_home_win_rate": self.home_win_rate_,
        }


class EloBaseline(RungModelBase):
    """Sequential Elo rating. ``fit`` replays training games in date order.

    The rating dict ``self.ratings_`` after ``fit`` reflects everything
    learned from the training fold; ``predict`` on a held-out slice uses
    *that* frozen snapshot -- it is never updated with the test games'
    actual results, so test-fold predictions cannot leak future outcomes.

    At every season boundary encountered during ``fit`` (a row whose
    ``season`` differs from the previous row's), every rated team's
    rating is regressed toward ``initial_rating`` by
    ``1 - season_carryover`` -- the common NBA Elo practice of a partial
    reset between seasons (aging, roster turnover, offseason change; see
    CLAUDE.md cold-start case 4, "new season"). The default keeps 75% of
    a team's rating and regresses 25% toward the mean. If ``train_df`` has
    no ``season`` column (e.g. small unit-test fixtures), no regression is
    applied -- behavior is unchanged from before this was added.
    """

    def __init__(
        self,
        seed: int = 0,
        k_factor: float = 20.0,
        home_advantage_elo: float = 65.0,
        initial_rating: float = 1500.0,
        season_carryover: float = 0.75,
    ) -> None:
        super().__init__(seed=seed)
        self.k_factor = k_factor
        self.home_advantage_elo = home_advantage_elo
        self.initial_rating = initial_rating
        self.season_carryover = season_carryover
        self.ratings_: dict[int, float] = {}

    def _win_prob(self, home_team: int, away_team: int) -> float:
        home_rating = self.ratings_.get(home_team, self.initial_rating)
        away_rating = self.ratings_.get(away_team, self.initial_rating)
        diff = (home_rating + self.home_advantage_elo) - away_rating
        return float(1.0 / (1.0 + 10.0 ** (-diff / 400.0)))

    def _regress_ratings_to_mean(self) -> None:
        """Season-boundary regression-to-the-mean (standard NBA Elo practice)."""
        for team, rating in self.ratings_.items():
            self.ratings_[team] = (
                self.season_carryover * rating + (1.0 - self.season_carryover) * self.initial_rating
            )

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        self.ratings_ = {}
        has_season = "season" in train_df.columns
        ordered = train_df.sort(["game_date", "game_id"])
        seasons = ordered.select("season").to_series().to_list() if has_season else None
        current_season: object = None
        rows = ordered.select(["home_team", "away_team"]).iter_rows()
        for i, (row, outcome) in enumerate(zip(rows, y, strict=True)):
            if seasons is not None:
                season = seasons[i]
                if current_season is not None and season != current_season:
                    self._regress_ratings_to_mean()
                current_season = season
            home_team, away_team = row
            p_home = self._win_prob(home_team, away_team)
            home_rating = self.ratings_.get(home_team, self.initial_rating)
            away_rating = self.ratings_.get(away_team, self.initial_rating)
            actual_home = float(outcome)
            self.ratings_[home_team] = home_rating + self.k_factor * (actual_home - p_home)
            self.ratings_[away_team] = away_rating + self.k_factor * (
                (1.0 - actual_home) - (1.0 - p_home)
            )

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        return np.array(
            [self._win_prob(h, a) for h, a in df.select(["home_team", "away_team"]).iter_rows()],
            dtype=float,
        )

    def get_config(self) -> dict[str, object]:
        return {
            "model_name": "rung0_elo",
            "rung": 0,
            "seed": self.seed,
            "k_factor": self.k_factor,
            "home_advantage_elo": self.home_advantage_elo,
            "initial_rating": self.initial_rating,
            "season_carryover": self.season_carryover,
            "n_teams_rated": len(self.ratings_),
        }


class ClosingLineStub(RungModelBase):
    """Placeholder for the closing-line-implied-probability baseline.

    Not implemented: no odds/market data source exists in this pipeline
    yet (Phase 3, ``nba/kalshi/``). Deliberately raises rather than
    fabricate a line, per CLAUDE.md's no-fabrication rule.
    """

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        return None

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        raise NotImplementedError(
            "closing-line implied probability requires a market-odds source "
            "(Phase 3 Kalshi ingestion, not yet built); not fabricated here"
        )

    def get_config(self) -> dict[str, object]:
        return {
            "model_name": "rung0_closing_line_stub",
            "rung": 0,
            "seed": self.seed,
            "status": "stub_not_implemented",
            "blocked_on": "nba/kalshi/ (Phase 3 market-odds ingestion)",
        }
