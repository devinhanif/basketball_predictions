"""As-of predictors for one slate. Every query filters ``game_date < slate_date``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.models.rung0_baselines import MovEloBaseline
from nba.registry.protocol import RegistryAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
ELO_CONFIG = REPO_ROOT / "configs" / "mov_elo_tuned.yaml"
ELO_MODEL_NAME = "rung0_mov_elo"
PROPS_MODEL_NAME = "props_rolling_avg_baseline"
PROPS_VERSION = "baseline-v1"
PROP_STATS = ("pts", "reb", "ast", "fg3m")
PROP_LOOKBACK_GAMES = 82
PROP_MIN_GAMES = 5
PROP_ROSTER_WINDOW = 10  # team games used to find who is currently on the roster
MIN_STD = 0.5


@dataclass(frozen=True)
class ResolvedModel:
    model_name: str
    version: str
    artifact_path: str | None
    status: str  # 'registry' | 'unregistered_config_fallback'


def resolve_production(registry: RegistryAdapter | None, model_name: str) -> ResolvedModel:
    """Look up the production version; fall back loudly (not silently) if absent."""
    if registry is not None:
        try:
            path = registry.load_model(model_name, alias="production")
        except LookupError:
            pass
        else:
            return ResolvedModel(model_name, Path(path).name or "unknown", path, "registry")
    return ResolvedModel(model_name, "unregistered", None, "unregistered_config_fallback")


def load_elo_params(path: Path = ELO_CONFIG) -> dict[str, float]:
    raw = yaml.safe_load(path.read_text())
    keys = ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div")
    return {k: float(raw[k]) for k in keys}


def completed_games_before(con: duckdb.DuckDBPyConnection, slate: date) -> pl.DataFrame:
    return con.execute(
        """
        SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts
        FROM games
        WHERE game_date < ? AND home_pts IS NOT NULL AND away_pts IS NOT NULL
          AND home_pts > 0 AND away_pts > 0
        ORDER BY game_date, game_id
        """,
        [slate],
    ).pl()


def fit_mov_elo(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    slate_season: int,
    params: dict[str, float],
    seed: int = 0,
) -> tuple[MovEloBaseline, int]:
    """Fit on games strictly before ``slate``; apply the season-boundary
    regression the sequential fit would have applied if the slate is the first
    day of a new season (``fit`` only regresses between seasons it SEES)."""
    train = completed_games_before(con, slate)
    model = MovEloBaseline(seed=seed, **params)
    y = (train["home_pts"] > train["away_pts"]).cast(pl.Int8).to_numpy()
    model.fit(train, y)
    if train.height and int(train["season"].max()) < slate_season:  # type: ignore[arg-type]
        model._regress_ratings_to_mean()
    return model, train.height


def predict_win_probs(model: MovEloBaseline, games: list[tuple[int, int]]) -> list[float]:
    if not games:
        return []
    df = pl.DataFrame({"home_team": [g[0] for g in games], "away_team": [g[1] for g in games]})
    return [float(p) for p in np.asarray(model.predict(df))]


def rolling_prop_baseline(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    team_ids: list[int],
    exclude_players: set[int],
) -> pl.DataFrame:
    """Per-player rolling mean/std of each stat over their last
    ``PROP_LOOKBACK_GAMES`` played games before ``slate`` (as-of), for players
    on a slate team's recent roster. Conditional on playing (minutes > 0)."""
    if not team_ids:
        return pl.DataFrame()
    teams = ",".join(str(int(t)) for t in team_ids)
    sel = ", ".join(
        f"avg({s}) AS {s}_mean, coalesce(stddev_samp({s}), 0) AS {s}_std" for s in PROP_STATS
    )
    sql = f"""
        WITH played AS (
            SELECT s.player_id, s.team_id, s.pts, s.reb, s.ast, s.fg3m,
                   g.game_date, g.game_id
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE g.game_date < ? AND s.minutes > 0
        ),
        team_recent AS (
            SELECT team_id, game_id FROM (
                SELECT team_id, game_id, row_number() OVER
                    (PARTITION BY team_id ORDER BY max(game_date) DESC, game_id DESC) rn
                FROM played GROUP BY team_id, game_id
            ) WHERE rn <= {PROP_ROSTER_WINDOW}
        ),
        roster AS (
            SELECT p.player_id, arg_max(p.team_id, p.game_date) AS team_id
            FROM played p JOIN team_recent r USING (team_id, game_id)
            WHERE p.team_id IN ({teams}) GROUP BY p.player_id
        ),
        ranked AS (
            SELECT p.*, row_number() OVER
                (PARTITION BY p.player_id ORDER BY p.game_date DESC, p.game_id DESC) rn
            FROM played p JOIN roster USING (player_id)
        )
        SELECT r.player_id, ro.team_id, count(*) AS n_games, {sel}
        FROM ranked r JOIN roster ro USING (player_id)
        WHERE rn <= {PROP_LOOKBACK_GAMES}
        GROUP BY r.player_id, ro.team_id HAVING count(*) >= {PROP_MIN_GAMES}
        ORDER BY r.player_id
    """
    df = con.execute(sql, [slate]).pl()
    if exclude_players and not df.is_empty():
        df = df.filter(~pl.col("player_id").is_in(sorted(exclude_players)))
    return df
