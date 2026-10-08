"""Forward (pre-tip) routed player-prop predictions for one slate.

The backtest entrypoints (``nba.eval.player_points_sim_eval`` /
``player_reb_ast_sim_eval``) are shaped around completed games: rosters come
from the box score and the as-of features are computed for every historical
row. A daily loop has neither a box score nor a roster for tonight. This
module builds the same inputs for games that have NOT been played, reusing the
backtest builders unchanged so the model is the one that was evaluated.

How leakage is excluded (by construction, not by convention):

* A private in-memory scratch DuckDB is populated with ONLY rows whose
  ``game_date < as_of`` (games, box scores, possessions, static player data).
  Anything dated ``as_of`` or later -- including a half-ingested same-day game
  or a planted future row -- never reaches a feature builder.
* The slate games and roster players are then added as *placeholder* rows
  (no scores, NULL box-score values). The existing builders compute every
  ``*_prior`` feature with ``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING``,
  so a placeholder row reads only history, exactly like a historical row would.
* Rosters come from who played in each team's most recent games before
  ``as_of`` (minus ``out_players``); never from tonight's box score.

Routing (2026-10-08 analysis, ``docs/RESULTS_2026-10-08.md`` section 2): for pts,
reb, ast the possession sim is used for players whose as-of Syntetos-Boylan
volatility class is ``insufficient_history`` / ``intermittent`` / ``erratic``,
and the season average for ``smooth`` / ``lumpy`` regulars. 3PM has no sim head,
so it is always the season average. The bucket is computed with
``nba.coldstart.sb_classification`` primitives on the as-of series.

Known limitations (documented, not hidden):

* A player with no NBA row before ``as_of`` (a true rookie debut) or who joined
  a team inside the roster window via an offseason move not yet visible in box
  scores cannot be projected; there is no pre-game roster feed. The first
  games of a season use last season's rosters.
* Sim point/rebound/assist distributions are driven by hurdle-mean projected
  minutes (as in the backtest) and do not model DNP explicitly; they are used
  as a distribution conditional on playing.
* Hand-set minutes game-context adjustments (b2b, travel) are off here because
  those builders have no schedule for unplayed games.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from datetime import date

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.sb_classification import (
    INSUFFICIENT_HISTORY_BUCKET,
    classify_sb,
    compute_adi_cv2,
)
from nba.features.player_possession_features import build_player_shot_rates
from nba.features.player_rebound_assist_features import build_player_reb_ast_rates
from nba.features.possession_features import build_team_possession_rates
from nba.props.baselines import season_average_baseline
from nba.props.config import THRESHOLDS
from nba.props.distributions import Distribution
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.sim.player_attribution import (
    DEFAULT_ASSISTED_FG_RATE,
    profiles_from_features,
    simulate_game_with_players,
)

PROP_STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
# Points routes to season-avg: no out-of-sample sim win, and forward sim-routed points
# bias was -3.25 [-3.77,-2.78] on 14 2024-25 slates (projected rosters). 2026-10-08.
SIM_STATS: tuple[str, ...] = ("reb", "ast")
#: SB classes routed to the sim (2026-10-08 routing result); the rest -> season average.
SIM_BUCKETS: frozenset[str] = frozenset({INSUFFICIENT_HISTORY_BUCKET, "intermittent", "erratic"})
SB_MIN_GAMES = 10
DEFAULT_N_SIMS = 1000
MODEL_SIM = "sim"
MODEL_SEASON_AVG = "season_avg"

OUTPUT_COLUMNS: list[str] = [
    "game_id",
    "team_id",
    "player_id",
    "is_home",
    "stat",
    "model",
    "bucket",
    "mean",
    "std",
    "dist_family",
    "dist_params",
    "p_ge",
    "q10",
    "q50",
    "q90",
    "mean_sim",
    "mean_season_avg",
    "n_games_prior",
    "proj_minutes",
]


@dataclass(frozen=True)
class ForwardConfig:
    sim_buckets: frozenset[str] = SIM_BUCKETS
    sim_stats: tuple[str, ...] = SIM_STATS
    roster_window_games: int = 10  # a player is "on the roster" if in a team's last N games
    max_roster: int = 13
    min_proj_minutes: float = 2.0
    #: 'conditional': sim minutes = minutes-given-play for players with
    #: P(play) >= ``min_p_play`` (the rest get season-average only); 'hurdle_mean':
    #: P(play)*minutes for everyone (the backtest shape, which assumed the real
    #: roster was known).
    sim_minutes: str = "conditional"
    min_p_play: float = 0.5
    thresholds: dict[str, list[int]] = field(default_factory=lambda: dict(THRESHOLDS))


def route_model(stat: str, bucket: str, cfg: ForwardConfig) -> str:
    """Current routing rule: sim for cold-start/intermittent/erratic on sim stats."""
    if stat in cfg.sim_stats and bucket in cfg.sim_buckets:
        return MODEL_SIM
    return MODEL_SEASON_AVG


def _season_for(d: date) -> int:
    return d.year if d.month >= 8 else d.year - 1


def game_seed(seed: int, game_id: str) -> int:
    """Deterministic per-game seed independent of slate ordering."""
    key = int(game_id) if game_id.isdigit() else zlib.crc32(game_id.encode())
    return (seed * 1_000_003 + key) % (2**32)


# --------------------------------------------------------------------------- scratch


_COPY_SQL: dict[str, str] = {
    "games": "SELECT * FROM games WHERE game_date < ?",
    "player_game_stats": (
        "SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) WHERE g.game_date < ?"
    ),
    "possessions": (
        "SELECT p.game_id, p.poss_idx, p.off_team, p.def_team, p.pts, p.outcome, "
        "p.shooter_id, p.shot_zone, p.off_players "
        "FROM possessions p JOIN games g USING (game_id) WHERE g.game_date < ?"
    ),
}


def _build_history_scratch(
    con: duckdb.DuckDBPyConnection, as_of: date
) -> duckdb.DuckDBPyConnection:
    """In-memory copy of the strictly-before-``as_of`` history the builders need."""
    sc = duckdb.connect(":memory:")
    for name, sql in _COPY_SQL.items():
        df = con.execute(sql, [as_of]).pl()
        sc.register("_src", df)
        sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
        sc.unregister("_src")
    ps = con.execute("SELECT * FROM players_static").pl()
    sc.register("_src", ps)
    sc.execute("CREATE TABLE players_static AS SELECT * FROM _src")
    sc.unregister("_src")
    return sc


def _candidate_roster(
    sc: duckdb.DuckDBPyConnection,
    team_ids: list[int],
    exclude: set[int],
    window: int,
) -> pl.DataFrame:
    """Players whose latest played game (history only) is one of their team's
    last ``window`` games; ``(player_id, team_id)``."""
    teams = ",".join(str(int(t)) for t in team_ids)
    df = sc.execute(
        f"""
        WITH played AS (
            SELECT s.player_id, s.team_id, g.game_date, g.game_id
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE s.minutes > 0 AND s.team_id IN ({teams})
        ),
        team_recent AS (
            SELECT team_id, game_id FROM (
                SELECT team_id, game_id, row_number() OVER
                    (PARTITION BY team_id ORDER BY game_date DESC, game_id DESC) rn
                FROM (SELECT DISTINCT team_id, game_id, game_date FROM played)
            ) WHERE rn <= {int(window)}
        ),
        latest AS (
            SELECT player_id, team_id, game_id, row_number() OVER
                (PARTITION BY player_id ORDER BY game_date DESC, game_id DESC) rn
            FROM (
                SELECT s.player_id, s.team_id, g.game_date, g.game_id
                FROM player_game_stats s JOIN games g USING (game_id) WHERE s.minutes > 0
            )
        )
        SELECT l.player_id, l.team_id FROM latest l
        JOIN team_recent r ON r.team_id = l.team_id AND r.game_id = l.game_id
        WHERE l.rn = 1 ORDER BY l.player_id
        """
    ).pl()
    if exclude and not df.is_empty():
        df = df.filter(~pl.col("player_id").is_in(sorted(exclude)))
    return df


def _insert_placeholders(
    sc: duckdb.DuckDBPyConnection,
    as_of: date,
    games: pl.DataFrame,
    roster: pl.DataFrame,
    team_game: dict[int, str],
) -> None:
    season = _season_for(as_of)
    g = pl.DataFrame(
        {
            "game_id": games["game_id"].cast(pl.Utf8),
            "game_date": [as_of] * games.height,
            "season": [season] * games.height,
            "home_team": games["home_team"].cast(pl.Int64),
            "away_team": games["away_team"].cast(pl.Int64),
        }
    )
    sc.register("_g", g)
    sc.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team) "
        "SELECT game_id, game_date, season, home_team, away_team FROM _g"
    )
    sc.unregister("_g")
    r = pl.DataFrame(
        {
            "game_id": [team_game[int(t)] for t in roster["team_id"].to_list()],
            "player_id": roster["player_id"].cast(pl.Int64),
            "team_id": roster["team_id"].cast(pl.Int64),
        }
    )
    sc.register("_r", r)
    sc.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id) "
        "SELECT game_id, player_id, team_id FROM _r"
    )
    sc.unregister("_r")


# --------------------------------------------------------------------------- priors


def _stat_series(
    sc: duckdb.DuckDBPyConnection, player_ids: list[int], stat: str
) -> dict[int, np.ndarray]:
    """Per-player prior non-null series of ``stat`` in (game_date, game_id) order."""
    if stat not in PROP_STATS:
        raise ValueError(f"unsupported stat {stat!r}")
    ids = ",".join(str(int(p)) for p in player_ids)
    rows = sc.execute(
        f"""
        SELECT s.player_id, list({stat} ORDER BY g.game_date, g.game_id)
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE s.player_id IN ({ids}) AND s.{stat} IS NOT NULL
        GROUP BY s.player_id
        """
    ).fetchall()
    return {int(pid): np.asarray(vals, dtype=float) for pid, vals in rows}


def _season_avg_dists(series: list[np.ndarray], stat: str) -> list[Distribution]:
    means = [float(v.mean()) if len(v) else None for v in series]
    stds = [float(v.std(ddof=1)) if len(v) > 1 else None for v in series]
    feats = pl.DataFrame(
        {"season_avg_prior": means, "season_std_prior": stds},
        schema={"season_avg_prior": pl.Float64, "season_std_prior": pl.Float64},
    )
    return list(season_average_baseline(feats, stat))


def _row_from_dist(dist: Distribution, thresholds: list[int]) -> dict[str, object]:
    return {
        "mean": dist.mean(),
        "std": float(dist.params().get("std", float("nan"))),
        "dist_family": dist.family,
        "dist_params": json.dumps(dist.params(), sort_keys=True),
        "p_ge": json.dumps({str(t): dist.p_ge(float(t)) for t in thresholds}, sort_keys=True),
        "q10": dist.ppf(0.10),
        "q50": dist.ppf(0.50),
        "q90": dist.ppf(0.90),
    }


# --------------------------------------------------------------------------- public


def predict_slate(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    games: pl.DataFrame,
    out_players: set[int] | None = None,
    *,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int = 0,
    config: ForwardConfig | None = None,
) -> pl.DataFrame:
    """Routed pts/reb/ast/fg3m distributions for every projected player on a slate.

    ``games`` needs columns ``game_id``, ``home_team``, ``away_team`` (extra
    columns ignored). Only rows with ``game_date < as_of`` are read from
    ``con`` (read-only use). ``out_players`` are excluded before projection, so
    the sim redistributes their share across teammates. Returns one row per
    (player, stat) with ``OUTPUT_COLUMNS``; empty (with those columns) when
    nothing can be projected. Deterministic for a given ``seed``/``n_sims``.
    """
    cfg = config or ForwardConfig()
    empty = pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS})
    if games.height == 0:
        return empty
    team_game: dict[int, str] = {}
    for r in games.iter_rows(named=True):
        for t in (int(r["home_team"]), int(r["away_team"])):
            if t in team_game:
                raise ValueError(f"team {t} appears twice on the slate")
            team_game[t] = str(r["game_id"])
    exclude = set(out_players or ())

    sc = _build_history_scratch(con, as_of)
    try:
        cand = _candidate_roster(sc, sorted(team_game), exclude, cfg.roster_window_games)
        if cand.is_empty():
            return empty
        series = {s: _stat_series(sc, cand["player_id"].to_list(), s) for s in PROP_STATS}
        _insert_placeholders(sc, as_of, games, cand, team_game)
        slate_ids = [str(g) for g in games["game_id"].to_list()]

        team_rates = build_team_possession_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
        shot_rates = build_player_shot_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
        reb_ast = build_player_reb_ast_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
        minutes_feats = build_minutes_features(sc, use_game_context=False).filter(
            pl.col("game_id").is_in(slate_ids)
        )
    finally:
        sc.close()

    dists = predict_minutes(minutes_feats)
    if cfg.sim_minutes not in {"conditional", "hurdle_mean"}:
        raise ValueError(f"unknown sim_minutes {cfg.sim_minutes!r}")
    mins = minutes_feats.select(
        ["game_id", "player_id", "team_id", "games_played_prior"]
    ).with_columns(
        p_play=pl.Series([d.p_play for d in dists], dtype=pl.Float64),
        proj_minutes=pl.Series(
            [d.mu if cfg.sim_minutes == "conditional" else d.mean() for d in dists],
            dtype=pl.Float64,
        ),
    )
    mins = mins.filter(pl.col("proj_minutes") >= cfg.min_proj_minutes)
    if cfg.sim_minutes == "conditional":
        mins = mins.filter(pl.col("p_play") >= cfg.min_p_play)
    mins = (
        mins.sort(
            ["game_id", "team_id", "proj_minutes", "player_id"],
            descending=[False, False, True, False],
        )
        .group_by(["game_id", "team_id"], maintain_order=True)
        .head(cfg.max_roster)
    )
    if mins.is_empty():
        return empty
    proj = {int(r["player_id"]): float(r["proj_minutes"]) for r in mins.iter_rows(named=True)}

    rows: list[dict[str, object]] = []
    for grow in games.iter_rows(named=True):
        gid, home, away = str(grow["game_id"]), int(grow["home_team"]), int(grow["away_team"])
        g_rates = team_rates.filter(pl.col("game_id") == gid)
        h = g_rates.filter(pl.col("is_home"))
        a = g_rates.filter(~pl.col("is_home"))
        gm = mins.filter(pl.col("game_id") == gid)
        home_ids = gm.filter(pl.col("team_id") == home)["player_id"].to_list()
        away_ids = gm.filter(pl.col("team_id") == away)["player_id"].to_list()
        if h.height == 0 or a.height == 0 or not home_ids or not away_ids:
            continue
        g_shot = shot_rates.filter(pl.col("game_id") == gid)
        g_ra = reb_ast.filter(pl.col("game_id") == gid)

        def _afr(team_id: int, _ra: pl.DataFrame = g_ra) -> float:
            t = _ra.filter(pl.col("team_id") == team_id)
            return float(t[0, "assisted_fg_rate_prior"]) if t.height else DEFAULT_ASSISTED_FG_RATE

        sim = simulate_game_with_players(
            profiles_from_features(g_shot, proj, home_ids, reb_ast_rates=g_ra),
            profiles_from_features(g_shot, proj, away_ids, reb_ast_rates=g_ra),
            home_off_rtg=float(h[0, "off_rtg_prior"]),
            home_def_rtg=float(h[0, "def_rtg_prior"]),
            home_pace=float(h[0, "pace_prior"]),
            away_off_rtg=float(a[0, "off_rtg_prior"]),
            away_def_rtg=float(a[0, "def_rtg_prior"]),
            away_pace=float(a[0, "pace_prior"]),
            league_avg_ppp=float(h[0, "league_avg_ppp_asof"]),
            n_sims=n_sims,
            seed=game_seed(seed, gid),
            home_assisted_fg_rate=_afr(home),
            away_assisted_fg_rate=_afr(away),
        )
        sim_by_stat: dict[str, dict[int, Distribution]] = {
            "pts": {**sim.home_player_points, **sim.away_player_points},
            "reb": {**sim.home_player_rebounds, **sim.away_player_rebounds},
            "ast": {**sim.home_player_assists, **sim.away_player_assists},
        }
        for side_home, ids in ((True, home_ids), (False, away_ids)):
            team_id = home if side_home else away
            for pid in ids:
                pid = int(pid)
                n_prior = int(gm.filter(pl.col("player_id") == pid)[0, "games_played_prior"])
                for stat in PROP_STATS:
                    vals = series[stat].get(pid, np.empty(0))
                    adi, cv2, _ = compute_adi_cv2(vals.tolist(), SB_MIN_GAMES)
                    bucket = classify_sb(adi, cv2)
                    savg = _season_avg_dists([vals], stat)[0]
                    sim_d = sim_by_stat.get(stat, {}).get(pid)
                    model = route_model(stat, bucket, cfg)
                    if model == MODEL_SIM and sim_d is None:
                        model = MODEL_SEASON_AVG
                    chosen = sim_d if model == MODEL_SIM and sim_d is not None else savg
                    row = _row_from_dist(chosen, cfg.thresholds[stat])
                    row.update(
                        game_id=gid,
                        team_id=team_id,
                        player_id=pid,
                        is_home=side_home,
                        stat=stat,
                        model=model,
                        bucket=bucket,
                        mean_sim=sim_d.mean() if sim_d is not None else None,
                        mean_season_avg=savg.mean(),
                        n_games_prior=n_prior,
                        proj_minutes=proj[pid],
                    )
                    rows.append(row)
    if not rows:
        return empty
    return (
        pl.DataFrame(rows).select(OUTPUT_COLUMNS).sort(["game_id", "team_id", "player_id", "stat"])
    )
