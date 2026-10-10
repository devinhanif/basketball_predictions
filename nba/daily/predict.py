"""As-of predictors for one slate. Every query filters ``game_date < slate_date``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.daily.injury import MAX_REPORT_AGE_HOURS, to_report_dt
from nba.features.injury_report import (
    ReportTriggerConfig,
    load_report_rows,
    serve_pretip_flagged,
)
from nba.models.injury_elo import InjuryFeatureConfig, feature_config_from
from nba.models.rung0_baselines import MovEloBaseline
from nba.props.forward import (
    ContextSlateResult,
    ForwardConfig,
    predict_slate,
    predict_slate_context,
)
from nba.registry.protocol import RegistryAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
ELO_CONFIG = REPO_ROOT / "configs" / "mov_elo_tuned.yaml"
ELO_MODEL_NAME = "rung0_mov_elo"
INJURY_ELO_MODEL_NAME = "rung0_injury_elo"
INJURY_CONFIG = REPO_ROOT / "configs" / "injury_elo.yaml"
PROPS_MODEL_NAME = "props_rolling_avg_baseline"
PROPS_VERSION = "baseline-v1"
PROP_STATS = ("pts", "reb", "ast", "fg3m")
CONTEXT_PROPS_MODEL_NAME = "props_context_residual"
CONTEXT_PROPS_VERSION = "ctxres-v1"
#: optional comparison rows: same model, integer-support quantiles (docs/INTEGER_QUANTILES.md)
CONTEXT_INT_PROPS_MODEL_NAME = "props_context_residual_int"
#: optional SHADOW rows (pts only): short-minutes mixture, integer-support quantiles
#: (docs/LOWER_TAIL.md); comparison only, never primary
CONTEXT_LT_PROPS_MODEL_NAME = "props_context_residual_lt"
RECENCY_PROPS_MODEL_NAME = "props_recency_v1"
RECENCY_PROPS_VERSION = "recency-v1"
DEFAULT_CONTEXT_CACHE = REPO_ROOT / "data" / "models" / "context_residual"
REPORT_LEAD_MINUTES = 60  # same pre-tip rule as injury Elo: real tip-off minus 60 min
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


def load_injury_settings(
    artifact_path: str | None = None,
) -> tuple[InjuryFeatureConfig, float, int]:
    """Feature config, ridge lambda and min signal games for the injury model.

    Prefers the ``injury_elo.yaml`` stored with the registered production
    version (so a promoted version is reproduced exactly); else the repo copy.
    """
    path = INJURY_CONFIG
    if artifact_path:
        cand = Path(artifact_path) / "injury_elo.yaml"
        if cand.exists():
            path = cand
    raw = yaml.safe_load(path.read_text())
    return (
        feature_config_from(raw),
        float(raw["fit"]["ridge_lambda"]),
        int(raw["fit"]["min_signal_games"]),
    )


def injury_report_rows(
    con: duckdb.DuckDBPyConnection,
    game_ids: list[str],
    table: str = "player_availability",
    game_dates: dict[str, date] | None = None,
) -> pl.DataFrame:
    """Official-report rows (any time; the real-tip leakage filter is applied
    inside ``predict_games``) for ``game_ids``. Empty frame if no table.

    ``load_report_rows`` joins ``games`` for the game date, and an UPCOMING game has no
    ``games`` row until it is played, so for the live path the rows of such games would
    vanish and every slate game would silently fall back to MOV-Elo. ``game_dates``
    (``game_id -> slate date``) lets those games be read straight from ``table`` (their
    ``game_id`` was set at parse time from the schedule)."""
    config = ReportTriggerConfig(statuses=("out", "doubtful"), table=table)
    empty = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
        }
    )
    try:
        rows = load_report_rows(con, config, game_ids=game_ids)
        if not game_dates:
            return rows
        known = set(rows["game_id"].to_list()) | {
            str(r[0]) for r in con.execute("SELECT game_id FROM games").fetchall()
        }
        forward = [g for g in game_ids if g not in known and g in game_dates]
        if not forward:
            return rows
        marks = ", ".join("?" for _ in config.sources)
        extra = con.execute(
            "SELECT game_id, player_id, lower(status) AS status, CAST(as_of AS TIMESTAMP) AS as_of "
            f"FROM {table} WHERE source IN ({marks}) AND player_id IS NOT NULL "
            f"AND game_id IN ({', '.join('?' for _ in forward)})",
            [*config.sources, *forward],
        ).pl()
    except duckdb.CatalogException:
        return empty
    if extra.height == 0:
        return rows
    extra = extra.with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("game_id")
        .replace_strict(game_dates, return_dtype=pl.Date, default=None)
        .alias("game_date"),
    ).select(rows.columns)
    return pl.concat([rows, extra], how="vertical_relaxed")


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


def slate_report_outs(
    con: duckdb.DuckDBPyConnection,
    games: list[tuple[str, datetime]],
    made_at: datetime,
    lead_minutes: int = REPORT_LEAD_MINUTES,
) -> dict[str, set[int]]:
    """``game_id -> players OUT`` on the latest official report usable for that
    game: stamped (US-Eastern clock) at or before ``min(now, tip-off - lead)`` and
    <= 36 h old. ``games`` = ``[(game_id, tipoff_naive_utc)]``. A game with no
    usable report is ABSENT (never imputed as "nobody out"). Snapshots are chosen
    PER game from that game's own report rows (``serve_pretip_flagged``, the training rule
    ``latest_pretip_flagged``), so a snapshot that does not mention the game never counts."""
    if not games:
        return {}
    tips_et = {gid: to_report_dt(tip) for gid, tip in games}
    rows = injury_report_rows(
        con,
        list(tips_et),
        game_dates={gid: t.date() for gid, t in tips_et.items()},
    )
    cfg = ReportTriggerConfig(statuses=("out",), lead_minutes=lead_minutes, tip_source="real")
    flagged, _used = serve_pretip_flagged(
        rows, tips_et, to_report_dt(made_at), cfg, max_age_hours=MAX_REPORT_AGE_HOURS
    )
    return flagged


def context_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    *,
    cache_root: Path | None = None,
    official_roster: pl.DataFrame | None = None,
    int_variant: bool = False,
    lower_tail_variant: bool = False,
) -> ContextSlateResult:
    """Context-residual props (primary) + recency comparison for ``games`` =
    ``[(game_id, home_team, away_team)]``; fit on rows strictly before ``slate``.
    ``official_roster`` (default None = recent-games roster) enables the pre-tip roster source."""
    frame = pl.DataFrame(
        {
            "game_id": [g[0] for g in games],
            "home_team": [g[1] for g in games],
            "away_team": [g[2] for g in games],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    return predict_slate_context(
        con,
        slate,
        frame,
        report_out,
        elo_params,
        cache_root=cache_root or DEFAULT_CONTEXT_CACHE,
        official_roster=official_roster,
        int_variant=int_variant,
        lower_tail_variant=lower_tail_variant,
    )


def recency_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    exclude_players: set[int],
    official_roster: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Recency-weighted played-games average only (no sim, no model fit)."""
    frame = pl.DataFrame(
        {
            "game_id": [g[0] for g in games],
            "home_team": [g[1] for g in games],
            "away_team": [g[2] for g in games],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    return predict_slate(
        con,
        slate,
        frame,
        exclude_players,
        config=ForwardConfig(sim_stats=()),
        official_roster=official_roster,
    )
