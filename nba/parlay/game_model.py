"""Game-level marginals: margin and total, both Normal, fitted on seasons < 2025 only.

margin (home minus away) ~ Normal(b + s * logit(p_home), sd_margin), ``p_home`` = production
injury-Elo win probability, so win and spread legs share one latent (the margin).
total ~ Normal(a + c * f, sd_total), ``f`` = as-of average of the two teams' game totals
(points scored + allowed per game = pace x efficiency), exponentially decayed and shrunk toward
the as-of league mean. Every feature uses only games dated strictly before the game.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

FIT_MAX_SEASON = 2024  # season 2025 is never used for fitting or selection


def logit(p: NDArray[np.float64] | float) -> Any:
    pp = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(pp / (1 - pp))


@dataclass(frozen=True)
class GameModelParams:
    margin_b: float
    margin_s: float  # points of margin per logit unit
    margin_sd: float
    total_a: float
    total_c: float
    total_sd: float
    league_total: float  # fallback feature when a team has no history
    halflife: float
    prior_k: float
    n_margin: int
    n_total: int
    fit_seasons: tuple[int, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(s: str) -> GameModelParams:
        d = json.loads(s)
        d["fit_seasons"] = tuple(d["fit_seasons"])
        return GameModelParams(**d)

    def margin_mu(self, p_home: float) -> float:
        return self.margin_b + self.margin_s * float(logit(p_home))

    def total_mu(self, feat: float | None) -> float:
        f = self.league_total if feat is None else feat
        return self.total_a + self.total_c * f


class TotalFeatureBuilder:
    """Sequential as-of team scoring environment. ``update_day`` after each date's games."""

    def __init__(self, halflife: float, prior_k: float, league_halflife: float = 600.0) -> None:
        self.decay = 0.5 ** (1.0 / halflife)
        self.ldecay = 0.5 ** (1.0 / league_halflife)
        self.k = prior_k
        self.team: dict[int, tuple[float, float]] = {}  # (weighted sum, weight)
        self.l_sum = 0.0
        self.l_w = 0.0

    @property
    def league(self) -> float | None:
        return None if self.l_w <= 0 else self.l_sum / self.l_w

    def team_level(self, team: int) -> float | None:
        lg = self.league
        if lg is None:
            return None
        s, w = self.team.get(team, (0.0, 0.0))
        return (s + self.k * lg) / (w + self.k)

    def feature(self, home: int, away: int) -> float | None:
        h, a = self.team_level(home), self.team_level(away)
        return None if h is None or a is None else 0.5 * (h + a)

    def update_day(self, games: list[tuple[int, int, float]]) -> None:
        """games: (home, away, total points). League mean is frozen within the day."""
        for home, away, tot in games:
            for t in (home, away):
                s, w = self.team.get(t, (0.0, 0.0))
                self.team[t] = (s * self.decay + tot, w * self.decay + 1.0)
        for _, _, tot in games:
            self.l_sum = self.l_sum * self.ldecay + tot
            self.l_w = self.l_w * self.ldecay + 1.0


def asof_total_features(games: pl.DataFrame, halflife: float, prior_k: float) -> pl.DataFrame:
    """Per game: ``total_feat`` (None until the league has history). ``games`` needs game_id,
    game_date, home_team, away_team, home_pts, away_pts (unplayed rows skipped)."""
    b = TotalFeatureBuilder(halflife, prior_k)
    out_id: list[str] = []
    out_f: list[float | None] = []
    g = games.sort(["game_date", "game_id"])
    for _, day in g.group_by("game_date", maintain_order=True):
        upd: list[tuple[int, int, float]] = []
        for r in day.iter_rows(named=True):
            out_id.append(str(r["game_id"]))
            out_f.append(b.feature(int(r["home_team"]), int(r["away_team"])))
            if r["home_pts"] is not None and r["away_pts"] is not None:
                upd.append(
                    (int(r["home_team"]), int(r["away_team"]), r["home_pts"] + r["away_pts"])
                )
        b.update_day(upd)
    return pl.DataFrame({"game_id": out_id, "total_feat": out_f})


def fit_game_model(
    games: pl.DataFrame,
    p_home: pl.DataFrame,
    *,
    halflife: float = 20.0,
    prior_k: float = 8.0,
    exclude_seasons: tuple[int, ...] = (),
) -> GameModelParams:
    """OLS fits on seasons <= FIT_MAX_SEASON (minus ``exclude_seasons``).

    ``p_home``: game_id, p (injury-Elo OOF). ``games``: needs season + points.
    """
    g = games.filter(
        (pl.col("season") <= FIT_MAX_SEASON)
        & ~pl.col("season").is_in(list(exclude_seasons))
        & pl.col("home_pts").is_not_null()
    )
    margin = (pl.col("home_pts") - pl.col("away_pts")).cast(pl.Float64)
    m = g.join(p_home.select("game_id", "p"), on="game_id").with_columns(margin.alias("margin"))
    x = np.asarray(logit(m["p"].to_numpy()), dtype=float)
    y = m["margin"].to_numpy().astype(float)
    s, b = np.polyfit(x, y, 1)
    msd = float(np.std(y - (b + s * x), ddof=2))
    feats = asof_total_features(games.filter(pl.col("season") <= FIT_MAX_SEASON), halflife, prior_k)
    t = (
        g.join(feats, on="game_id")
        .filter(pl.col("total_feat").is_not_null())
        .with_columns((pl.col("home_pts") + pl.col("away_pts")).cast(pl.Float64).alias("total"))
    )
    f = t["total_feat"].to_numpy().astype(float)
    ty = t["total"].to_numpy().astype(float)
    c, a = np.polyfit(f, ty, 1)
    tsd = float(np.std(ty - (a + c * f), ddof=2))
    seasons = tuple(sorted(int(v) for v in g["season"].unique().to_list()))
    return GameModelParams(
        margin_b=float(b),
        margin_s=float(s),
        margin_sd=msd,
        total_a=float(a),
        total_c=float(c),
        total_sd=tsd,
        league_total=float(ty.mean()),
        halflife=halflife,
        prior_k=prior_k,
        n_margin=len(y),
        n_total=len(ty),
        fit_seasons=seasons,
    )


def slate_total_feature(
    games_before: pl.DataFrame, params: GameModelParams, home: int, away: int, slate: date
) -> float | None:
    """As-of total feature for one future game from completed games dated < ``slate``."""
    b = TotalFeatureBuilder(params.halflife, params.prior_k)
    g = games_before.filter(pl.col("game_date") < slate).sort(["game_date", "game_id"])
    for _, day in g.group_by("game_date", maintain_order=True):
        b.update_day(
            [
                (int(r["home_team"]), int(r["away_team"]), r["home_pts"] + r["away_pts"])
                for r in day.iter_rows(named=True)
                if r["home_pts"] is not None and r["away_pts"] is not None
            ]
        )
    return b.feature(home, away)
