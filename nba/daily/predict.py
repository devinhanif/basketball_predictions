"""As-of predictors for one slate. Every query filters ``game_date < slate_date``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.daily.injury import MAX_REPORT_AGE_HOURS, to_report_dt
from nba.eval.injury_elo_eval import feature_config_from
from nba.models.injury_elo import InjuryFeatureConfig
from nba.models.rung0_baselines import MovEloBaseline
from nba.props.forward import (
    ContextSlateResult,
    ForwardConfig,
    predict_slate,
    predict_slate_context,
)
from nba.registry.protocol import RegistryAdapter
from nba.sim.usage_redistribution import ReportTriggerConfig, load_report_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
ELO_CONFIG = REPO_ROOT / "configs" / "mov_elo_tuned.yaml"
ELO_MODEL_NAME = "rung0_mov_elo"
INJURY_ELO_MODEL_NAME = "rung0_injury_elo"
INJURY_CONFIG = REPO_ROOT / "configs" / "injury_elo.yaml"
PROPS_MODEL_NAME = "props_rolling_avg_baseline"
PROPS_VERSION = "baseline-v1"
PROP_STATS = ("pts", "reb", "ast", "fg3m")
ROUTED_PROPS_MODEL_NAME = "props_routed_sim"
ROUTED_PROPS_VERSION = "routed-v1"
CONTEXT_PROPS_MODEL_NAME = "props_context_residual"
CONTEXT_PROPS_VERSION = "ctxres-v1"
RECENCY_PROPS_MODEL_NAME = "props_recency_v1"
RECENCY_PROPS_VERSION = "recency-v1"
DEFAULT_CONTEXT_CACHE = REPO_ROOT / "data" / "models" / "context_residual"
REPORT_LEAD_MINUTES = 60  # same pre-tip rule as injury Elo: real tip-off minus 60 min
ROUTED_N_SIMS = 1000
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
    con: duckdb.DuckDBPyConnection, game_ids: list[str], table: str = "player_availability"
) -> pl.DataFrame:
    """Official-report rows (any time; the real-tip leakage filter is applied
    inside ``predict_games``) for ``game_ids``. Empty frame if no table."""
    try:
        return load_report_rows(
            con,
            ReportTriggerConfig(statuses=("out", "doubtful"), table=table),
            game_ids=game_ids,
        )
    except duckdb.CatalogException:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "status": pl.Utf8,
                "as_of": pl.Datetime("us"),
                "game_date": pl.Date,
            }
        )


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


def routed_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    exclude_players: set[int],
    *,
    n_sims: int = ROUTED_N_SIMS,
    seed: int = 0,
) -> pl.DataFrame:
    """Routed sim / season-average distributions (``nba.props.forward``) for
    ``games`` = ``[(game_id, home_team, away_team), ...]``. As-of: only rows with
    ``game_date < slate`` are read; ``exclude_players`` never appear."""
    frame = pl.DataFrame(
        {
            "game_id": [g[0] for g in games],
            "home_team": [g[1] for g in games],
            "away_team": [g[2] for g in games],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    return predict_slate(con, slate, frame, exclude_players, n_sims=n_sims, seed=seed)


def slate_report_outs(
    con: duckdb.DuckDBPyConnection,
    games: list[tuple[str, datetime]],
    made_at: datetime,
    lead_minutes: int = REPORT_LEAD_MINUTES,
) -> dict[str, set[int]]:
    """``game_id -> players OUT`` on the latest official report usable for that
    game: stamped (US-Eastern clock) at or before ``min(now, tip-off - lead)`` and
    <= 36 h old. ``games`` = ``[(game_id, tipoff_naive_utc)]``. A game with no
    usable report is ABSENT (never imputed as "nobody out"). The report is
    league-wide per snapshot; consumers filter to the game's two teams."""
    out: dict[str, set[int]] = {}
    for gid, tip in games:
        cutoff = to_report_dt(min(made_at, tip - timedelta(minutes=lead_minutes)))
        try:
            row = con.execute(
                "SELECT max(as_of) FROM player_availability "
                "WHERE source = 'nba_official_report' AND as_of <= ?",
                [cutoff],
            ).fetchone()
            latest = row[0] if row else None
            if latest is None or latest < cutoff - timedelta(hours=MAX_REPORT_AGE_HOURS):
                continue
            rows = con.execute(
                "SELECT DISTINCT player_id FROM player_availability "
                "WHERE source = 'nba_official_report' AND as_of = ? AND status = 'out' "
                "AND player_id IS NOT NULL",
                [latest],
            ).fetchall()
        except duckdb.CatalogException:
            return {}
        out[gid] = {int(r[0]) for r in rows}
    return out


def context_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    *,
    cache_root: Path | None = None,
) -> ContextSlateResult:
    """Context-residual props (primary) + recency comparison for ``games`` =
    ``[(game_id, home_team, away_team)]``; fit on rows strictly before ``slate``."""
    frame = pl.DataFrame(
        {
            "game_id": [g[0] for g in games],
            "home_team": [g[1] for g in games],
            "away_team": [g[2] for g in games],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    return predict_slate_context(
        con, slate, frame, report_out, elo_params, cache_root=cache_root or DEFAULT_CONTEXT_CACHE
    )


def recency_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    exclude_players: set[int],
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
    return predict_slate(con, slate, frame, exclude_players, config=ForwardConfig(sim_stats=()))


# --------------------------------------------------------------------------------------
# Active routes (nba.registry.routing). Default behavior is unchanged: nothing below is
# called by the existing pipeline unless a route has been explicitly promoted AND the
# caller opts in via ``context_prop_predictions_routed``.
# --------------------------------------------------------------------------------------

#: Candidate id -> how the daily pipeline can produce it today. Routes needing any other
#: candidate are listed as not applicable (reason logged), never applied partially.
ROUTE_FORWARD_CANDIDATES = ("context_residual", "recency")


@dataclass(frozen=True)
class ActiveRoute:
    target: str
    version: str
    spec: dict[str, Any]
    applicable: bool
    reason: str


def load_active_routes(registry: RegistryAdapter | None) -> dict[str, ActiveRoute]:
    """Production ``route_<stat>`` specs, if any. Missing / invalid routes are simply absent
    (the default models stay primary); an invalid-but-present route is reported with
    ``applicable=False`` and a reason."""
    from nba.registry.routing import ROUTE_FILE, read_spec, route_model_name, validate_spec

    out: dict[str, ActiveRoute] = {}
    if registry is None:
        return out
    for stat in PROP_STATS:
        try:
            path = registry.load_model(route_model_name(stat), alias="production")
        except LookupError:
            continue
        try:
            spec = read_spec(Path(path) / ROUTE_FILE)
        except OSError as e:
            out[stat] = ActiveRoute(stat, "unknown", {}, False, f"unreadable spec: {e}")
            continue
        version = Path(path).name
        errs = validate_spec(spec)
        ids = [c["id"] for c in spec.get("candidates", [])]
        missing = [i for i in ids if i not in ROUTE_FORWARD_CANDIDATES]
        if errs:
            out[stat] = ActiveRoute(stat, version, spec, False, "invalid: " + "; ".join(errs))
        elif spec["kind"] == "router" and missing:
            out[stat] = ActiveRoute(
                stat, version, spec, False, f"candidates not available forward: {missing}"
            )
        elif spec["kind"] == "champion" and spec["champion"] not in ROUTE_FORWARD_CANDIDATES:
            out[stat] = ActiveRoute(
                stat, version, spec, False, f"champion {spec['champion']} not available forward"
            )
        else:
            out[stat] = ActiveRoute(stat, version, spec, True, "ok")
    return out


def _grids(frame: pl.DataFrame, keys: pl.DataFrame) -> np.ndarray:
    j = keys.join(frame.select(["game_id", "player_id", "q_grid"]), on=["game_id", "player_id"])
    return np.asarray([json.loads(v) for v in j["q_grid"].to_list()], dtype=float)


def apply_active_route(
    route: ActiveRoute,
    stat: str,
    frames: dict[str, pl.DataFrame],
    con: duckdb.DuckDBPyConnection,
    slate: date,
    extra_context: Any = None,
) -> pl.DataFrame:
    """Routed primary rows for ``stat``. ``frames``: candidate id -> forward frame with
    ``game_id, player_id, stat, mean, std, q_grid, p_ge`` (JSON strings), one row per
    player-game of this stat. Only rows present in EVERY candidate frame are routed; the
    caller keeps the default primary for the rest. Gate features are computed by the same
    code as training (``nba.stack.populate``) from games strictly before ``slate``."""
    if not route.applicable:
        raise ValueError(f"route for {stat} not applicable: {route.reason}")
    spec = route.spec
    ids: list[str] = [c["id"] for c in spec["candidates"]]
    absent = [i for i in ids if i not in frames]
    if absent:
        raise KeyError(f"missing candidate frames: {absent}")
    sub = {i: frames[i].filter(pl.col("stat") == stat) for i in ids}
    keys = sub[ids[0]].select(["game_id", "player_id"])
    for i in ids[1:]:
        keys = keys.join(sub[i].select(["game_id", "player_id"]), on=["game_id", "player_id"])
    keys = keys.unique().sort(["game_id", "player_id"])
    if keys.is_empty():
        return keys
    tpl = keys.join(sub[spec["champion"]], on=["game_id", "player_id"], how="left")
    if spec["kind"] == "champion":
        return tpl.with_columns(
            pl.lit(f"route:{spec['route_model']}:{route.version}:champion").alias("model")
        )

    import pandas as pd

    from nba.stack.frozen import FrozenGate
    from nba.stack.populate import forward_context, history_from_con

    gate = FrozenGate.from_dict(spec["gate"])
    kp = keys.to_pandas()
    season = slate.year if slate.month >= 9 else slate.year - 1
    slate_rows = kp.assign(game_date=pd.Timestamp(slate), season=season)
    ctx = forward_context(history_from_con(con, slate), slate_rows)
    if extra_context is not None:
        ctx = ctx.merge(extra_context, on=["game_id", "player_id"], how="left")
    ctx = kp.merge(ctx, on=["game_id", "player_id"], how="left")
    if len(ctx) != len(kp):
        raise ValueError("slate game_ids already present in history (not as-of)")
    cand = np.stack([_grids(sub[i], keys) for i in ids], axis=1)
    w = gate.weights(ctx)
    pooled = gate.apply(ctx, cand)
    means = np.stack(
        [
            keys.join(sub[i].select(["game_id", "player_id", "mean"]), on=["game_id", "player_id"])[
                "mean"
            ].to_numpy()
            for i in ids
        ],
        axis=1,
    )
    pge = [
        keys.join(sub[i].select(["game_id", "player_id", "p_ge"]), on=["game_id", "player_id"])[
            "p_ge"
        ].to_list()
        for i in ids
    ]
    pooled_pge = []
    for r in range(len(keys)):
        dicts = [json.loads(pge[j][r]) for j in range(len(ids))]
        pooled_pge.append(
            json.dumps(
                {t: float(sum(w[r, j] * dicts[j][t] for j in range(len(ids)))) for t in dicts[0]}
            )
        )
    q = pooled
    return tpl.with_columns(
        pl.Series("mean", (w * means).sum(axis=1)),
        pl.Series("std", (q[:, 17] - q[:, 1]) / 2.563),
        pl.Series("q10", q[:, 1]),
        pl.Series("q50", q[:, 9]),
        pl.Series("q90", q[:, 17]),
        pl.Series("q_grid", [json.dumps([round(float(v), 4) for v in row]) for row in q]),
        pl.Series("p_ge", pooled_pge),
        pl.Series(
            "route_weights",
            [
                json.dumps({i: round(float(w[r, j]), 4) for j, i in enumerate(ids)})
                for r in range(len(keys))
            ],
        ),
        pl.lit(f"route:{spec['route_model']}:{route.version}").alias("model"),
    )


def context_prop_predictions_routed(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[tuple[str, int, int]],
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    registry: RegistryAdapter | None,
    *,
    cache_root: Path | None = None,
) -> tuple[ContextSlateResult, dict[str, str]]:
    """Like :func:`context_prop_predictions`, but each stat with an applicable promoted
    route has its primary rows replaced by the routed rows (recency / context rows stay
    available as comparison rows in ``recency`` and the unrouted primary is kept in
    ``info['unrouted_primary']``). Returns the result plus ``{stat: status}``. With no
    promoted route the output equals :func:`context_prop_predictions` exactly."""
    res = context_prop_predictions(con, slate, games, report_out, elo_params, cache_root=cache_root)
    routes = load_active_routes(registry)
    status: dict[str, str] = {}
    if not routes:
        return res, status
    frames = {"context_residual": res.primary, "recency": res.recency}
    routed_parts: list[pl.DataFrame] = []
    keep = res.primary
    for stat, route in routes.items():
        if not route.applicable:
            status[stat] = f"not applied: {route.reason}"
            continue
        try:
            part = apply_active_route(route, stat, frames, con, slate)
        except (KeyError, ValueError) as e:
            status[stat] = f"not applied: {e}"
            continue
        status[stat] = f"applied {route.spec['route_model']} {route.version}: {part.height} rows"
        routed_parts.append(part)
        keep = keep.join(
            part.select(["game_id", "player_id"]).with_columns(pl.lit(stat).alias("stat")),
            on=["game_id", "player_id", "stat"],
            how="anti",
        )
    if not routed_parts:
        return res, status
    cols = keep.columns
    primary = pl.concat([keep, *[p.select(cols) for p in routed_parts]], how="vertical_relaxed")
    info = {**res.info, "unrouted_primary": res.primary.height, "routes": status}
    return ContextSlateResult(primary, res.recency, info), status
