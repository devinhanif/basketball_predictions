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
import pickle
import time
import zlib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

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
from nba.props.baselines import _DEFAULT_STD
from nba.props.config import THRESHOLDS
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    fit_for_date,
    flagged_from_availability,
    predict_rows,
    to_integer_support,
)
from nba.props.distributions import Distribution, NormalDist
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.props.roster_cold import (
    BinPrior,
    apply_rookie_prior,
    cold_stat_dist,
    minute_bin_priors,
    new_team_k_multipliers,
    rookie_minutes_mu,
)
from nba.props.rosters import merge_rosters
from nba.sim.player_attribution import (
    DEFAULT_ASSISTED_FG_RATE,
    profiles_from_features,
    simulate_game_with_players,
)

#: Quantile grid stored per prediction so settlement can compute an empirical CRPS
#: (CRPS = 2 * integral of pinball loss over tau) without assuming a family.
QUANTILE_TAUS: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 20))

#: Platt recalibration of the minutes model's P(play) for rostered players:
#: p_cal = sigmoid(a + b * logit(p_raw)). Fit on 14 sampled 2023-24 slates
#: (n=2405 players, Brier 0.195 -> 0.176) and checked on 28 held-out 2024-25 slates
#: (Brier 0.209 -> 0.187; mean 0.725 vs realized 0.718). Raw p overshoots because
#: rostered players are selected for having just played.
P_PLAY_PLATT: tuple[float, float] = (-0.327, 0.581)

PROP_STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
# Points routes to season-avg: no out-of-sample sim win, and forward sim-routed points
# bias was -3.25 [-3.77,-2.78] on 14 2024-25 slates (projected rosters). 2026-10-08.
# Forward replay (56 slates, 2024-25): sim loses MAE to the recency average on reb/ast too
# (backtest wins relied on actual box-score rosters). Everything routes to season-avg;
# mean_sim is still logged for later scoring. 2026-10-08.
SIM_STATS: tuple[str, ...] = ()
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
    "p_play",
    "p_play_raw",
    "starter_rate",
    "mean_uncond",
    "p_ge_uncond",
    "q_grid",
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
    #: Recency half-life (games) of the played-games average. Chosen on 2023-24
    #: slates by MAE (plateau 6-14); evaluated on 2024-25.
    halflife_games: float = 10.0
    #: Drop DNP rows (minutes NULL, stats 0) before the sim's rate builders so
    #: shot/rebound/assist rates are per game PLAYED, matching the conditional
    #: contract. Minutes/P(play) still see DNP rows.
    rates_played_only: bool = True
    thresholds: dict[str, list[int]] = field(default_factory=lambda: dict(THRESHOLDS))
    #: Only consulted when an ``official_roster`` is passed (opening-week roster source; see
    #: ``nba.props.rosters``). ``rookie_prior``: no-history players get the draft-slot minutes
    #: prior and minutes-binned league stat priors (``nba.props.roster_cold``; measured gain).
    #: ``new_team_shrinkage``: movers get the role-change pseudo-count boost; OFF by default
    #: because the replays measured it as slightly worse than keeping the player's own history
    #: (docs/OPENING_WEEK_ROSTERS.md). ``drop_unlisted_recent``: also drop recent-games players
    #: that no official roster lists (cut / left); default keeps them (union).
    new_team_shrinkage: bool = False
    rookie_prior: bool = True
    drop_unlisted_recent: bool = False
    #: Roster cap used instead of ``max_roster`` when an official roster is passed: official
    #: rosters list up to 15 standard + 3 two-way players, and with the sim off the cap exists
    #: only to bound output size. At 13 the low-minute rookies and the stale-roster extras
    #: compete for the last slots (coverage of debutants 60-73%); at 18 it is 93-98%.
    official_max_roster: int = 18


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
    sc: duckdb.DuckDBPyConnection, player_ids: list[int], stat: str, *, played_only: bool = False
) -> dict[int, np.ndarray]:
    """Per-player prior series of ``stat`` in (game_date, game_id) order.

    DNP rows in ``player_game_stats`` have ``minutes`` NULL but stats of 0 (not
    NULL), so the default series (kept for the SB routing bucket, as evaluated)
    contains DNP zeros. ``played_only`` keeps only ``minutes > 0`` games: the
    distribution of the stat GIVEN the player plays."""
    if stat not in PROP_STATS:
        raise ValueError(f"unsupported stat {stat!r}")
    ids = ",".join(str(int(p)) for p in player_ids)
    played_sql = " AND s.minutes > 0" if played_only else ""
    rows = sc.execute(
        f"""
        SELECT s.player_id, list({stat} ORDER BY g.game_date, g.game_id)
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE s.player_id IN ({ids}) AND s.{stat} IS NOT NULL{played_sql}
        GROUP BY s.player_id
        """
    ).fetchall()
    return {int(pid): np.asarray(vals, dtype=float) for pid, vals in rows}


def recency_weighted_dist(vals: np.ndarray, stat: str, halflife_games: float) -> NormalDist:
    """Normal with an exponentially recency-weighted mean/std of played games
    (weight 0.5 ** (games_ago / halflife)); league default std when < 2 games."""
    n = len(vals)
    if n == 0:
        return NormalDist(0.0, _DEFAULT_STD[stat])
    w = 0.5 ** (np.arange(n)[::-1] / halflife_games)
    mean = float((w * vals).sum() / w.sum())
    if n < 2:
        return NormalDist(mean, _DEFAULT_STD[stat])
    var = float((w * (vals - mean) ** 2).sum() / w.sum()) * n / (n - 1)
    std = var**0.5
    return NormalDist(mean, std if std > 0 else _DEFAULT_STD[stat])


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
        "q_grid": json.dumps([round(dist.ppf(t), 4) for t in QUANTILE_TAUS]),
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
    official_roster: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Routed pts/reb/ast/fg3m distributions for every projected player on a slate.

    ``games`` needs columns ``game_id``, ``home_team``, ``away_team`` (extra
    columns ignored). Only rows with ``game_date < as_of`` are read from
    ``con`` (read-only use). ``out_players`` are excluded before projection, so
    the sim redistributes their share across teammates. Returns one row per
    (player, stat) with ``OUTPUT_COLUMNS``; empty (with those columns) when
    nothing can be projected. Deterministic for a given ``seed``/``n_sims``.

    ``official_roster`` (``team_id``, ``player_id``; default None = the recent-games roster
    only, the production behavior) switches on the pre-tip roster source: the forward roster
    becomes official roster (slate teams) union recent-games players minus ``out_players``,
    movers get boosted minutes pseudo-counts and no-history players get the cold-start
    priors, see ``nba.props.roster_cold``.
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
    use_sim = bool(cfg.sim_stats)

    sc = _build_history_scratch(con, as_of)
    try:
        cand = _candidate_roster(sc, sorted(team_game), exclude, cfg.roster_window_games)
        use_official = official_roster is not None and not official_roster.is_empty()
        if use_official:
            assert official_roster is not None
            cand = merge_rosters(
                cand,
                official_roster,
                sorted(team_game),
                exclude,
                drop_unlisted_recent=cfg.drop_unlisted_recent,
            )
        if cand.is_empty():
            return empty
        ids = cand["player_id"].to_list()
        series = {s: _stat_series(sc, ids, s) for s in PROP_STATS}  # incl. DNP zeros (SB bucket)
        played = {s: _stat_series(sc, ids, s, played_only=True) for s in PROP_STATS}
        _insert_placeholders(sc, as_of, games, cand, team_game)
        slate_ids = [str(g) for g in games["game_id"].to_list()]

        minutes_feats = build_minutes_features(sc, use_game_context=False).filter(
            pl.col("game_id").is_in(slate_ids)
        )
        empty_df = pl.DataFrame()
        cold_bins: dict[str, list[BinPrior]] = {}
        k_mult: np.ndarray | None = None
        rookie_mu: tuple[np.ndarray, np.ndarray] | None = None
        if use_official:
            season_i = _season_for(as_of)
            cold_bins = minute_bin_priors(sc, season_i)
            if cfg.new_team_shrinkage:
                k_mult = new_team_k_multipliers(sc, minutes_feats, season_i)
            if cfg.rookie_prior:
                static_raw = sc.execute("SELECT * FROM players_static").pl()
                rookie_mu = rookie_minutes_mu(sc, minutes_feats, season_i, static_raw)
        if cfg.rates_played_only and use_sim:
            sc.register("_slate", pl.DataFrame({"game_id": slate_ids}))
            sc.execute(
                "DELETE FROM player_game_stats WHERE (minutes IS NULL OR minutes = 0) "
                "AND game_id NOT IN (SELECT game_id FROM _slate)"
            )
            sc.unregister("_slate")
        team_rates, shot_rates, reb_ast = empty_df, empty_df, empty_df
        if use_sim:
            team_rates = build_team_possession_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
            shot_rates = build_player_shot_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
            reb_ast = build_player_reb_ast_rates(sc).filter(pl.col("game_id").is_in(slate_ids))
    finally:
        sc.close()

    dists = predict_minutes(minutes_feats, k_multiplier=k_mult)
    if rookie_mu is not None:
        dists = apply_rookie_prior(dists, minutes_feats, rookie_mu[0], rookie_mu[1])
    if cfg.sim_minutes not in {"conditional", "hurdle_mean"}:
        raise ValueError(f"unknown sim_minutes {cfg.sim_minutes!r}")
    mins = minutes_feats.select(
        ["game_id", "player_id", "team_id", "games_played_prior", "starter_rate_prior"]
    ).with_columns(
        p_play=pl.Series([d.p_play for d in dists], dtype=pl.Float64),
        proj_minutes=pl.Series(
            [d.mu if cfg.sim_minutes == "conditional" else d.mean() for d in dists],
            dtype=pl.Float64,
        ),
    )
    raw_p = mins["p_play"].clip(1e-3, 1 - 1e-3).to_numpy()
    platt_a, platt_b = P_PLAY_PLATT
    mins = mins.with_columns(
        p_play_raw=pl.col("p_play"),
        p_play=pl.Series(
            1.0 / (1.0 + np.exp(-(platt_a + platt_b * np.log(raw_p / (1.0 - raw_p))))),
            dtype=pl.Float64,
        ),
    )
    mins = mins.filter(pl.col("proj_minutes") >= cfg.min_proj_minutes)
    if cfg.sim_minutes == "conditional":
        mins = mins.filter(pl.col("p_play_raw") >= cfg.min_p_play)
    mins = (
        mins.sort(
            ["game_id", "team_id", "proj_minutes", "player_id"],
            descending=[False, False, True, False],
        )
        .group_by(["game_id", "team_id"], maintain_order=True)
        .head(max(cfg.max_roster, cfg.official_max_roster) if use_official else cfg.max_roster)
    )
    if mins.is_empty():
        return empty
    proj = {int(r["player_id"]): float(r["proj_minutes"]) for r in mins.iter_rows(named=True)}
    pplay = {int(r["player_id"]): float(r["p_play"]) for r in mins.iter_rows(named=True)}
    pplay_raw = {int(r["player_id"]): float(r["p_play_raw"]) for r in mins.iter_rows(named=True)}
    srate = {
        int(r["player_id"]): float(r["starter_rate_prior"] or 0.0)
        for r in mins.iter_rows(named=True)
    }

    rows: list[dict[str, object]] = []
    for grow in games.iter_rows(named=True):
        gid, home, away = str(grow["game_id"]), int(grow["home_team"]), int(grow["away_team"])
        g_rates = team_rates.filter(pl.col("game_id") == gid) if use_sim else empty_df
        h = g_rates.filter(pl.col("is_home")) if use_sim else empty_df
        a = g_rates.filter(~pl.col("is_home")) if use_sim else empty_df
        gm = mins.filter(pl.col("game_id") == gid)
        home_ids = gm.filter(pl.col("team_id") == home)["player_id"].to_list()
        away_ids = gm.filter(pl.col("team_id") == away)["player_id"].to_list()
        if use_sim and (h.height == 0 or a.height == 0 or not home_ids or not away_ids):
            continue
        if not (home_ids or away_ids):
            continue
        g_shot = shot_rates.filter(pl.col("game_id") == gid) if use_sim else empty_df
        g_ra = reb_ast.filter(pl.col("game_id") == gid) if use_sim else empty_df

        def _afr(team_id: int, _ra: pl.DataFrame = g_ra) -> float:
            t = _ra.filter(pl.col("team_id") == team_id)
            return float(t[0, "assisted_fg_rate_prior"]) if t.height else DEFAULT_ASSISTED_FG_RATE

        sim_by_stat: dict[str, dict[int, Distribution]] = {}
        if use_sim:
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
            sim_by_stat = {
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
                    pvals = played[stat].get(pid, np.empty(0))
                    savg = recency_weighted_dist(pvals, stat, cfg.halflife_games)
                    if len(pvals) == 0:  # no NBA history: league stat level at his minutes
                        cold = cold_stat_dist(cold_bins, stat, proj[pid])
                        savg = cold if cold is not None else savg
                    sim_d = sim_by_stat.get(stat, {}).get(pid)
                    model = route_model(stat, bucket, cfg)
                    if model == MODEL_SIM and sim_d is None:
                        model = MODEL_SEASON_AVG
                    chosen = sim_d if model == MODEL_SIM and sim_d is not None else savg
                    row = _row_from_dist(chosen, cfg.thresholds[stat])
                    pp = pplay[pid]
                    pj = json.loads(str(row["p_ge"]))
                    row["mean_uncond"] = pp * float(row["mean"])  # type: ignore[arg-type]
                    row["p_ge_uncond"] = json.dumps(
                        {k: pp * v for k, v in pj.items()}, sort_keys=True
                    )
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
                        p_play=pplay[pid],
                        p_play_raw=pplay_raw[pid],
                        starter_rate=srate[pid],
                    )
                    rows.append(row)
    if not rows:
        return empty
    return (
        pl.DataFrame(rows).select(OUTPUT_COLUMNS).sort(["game_id", "team_id", "player_id", "stat"])
    )


# --------------------------------------------------------------------------- context-residual

MODEL_CONTEXT = "context_residual"
MODEL_RECENCY = "recency"
MODEL_RECENCY_FALLBACK = "recency_fallback"
DEFAULT_MODEL_CACHE = Path("data/models/context_residual")


@dataclass
class ContextSlateResult:
    """``primary``: context-residual rows (recency rows, labelled
    ``recency_fallback``, for players with too little history); ``recency``:
    the comparison model for every rostered player; ``info``: fit/cache facts."""

    primary: pl.DataFrame
    recency: pl.DataFrame
    info: dict[str, Any]
    #: optional comparison rows with integer-support quantiles (``int_variant=True``)
    integer_variant: pl.DataFrame | None = None


_AVAIL_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "player_id": pl.Int64,
    "status": pl.Utf8,
    "as_of": pl.Datetime("us"),
    "source": pl.Utf8,
}


def _history_frames(
    con: duckdb.DuckDBPyConnection, as_of: date
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, pgs, static, availability) with every row dated before ``as_of``."""
    games = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_date < ?",
        [as_of],
    ).pl()
    pgs = con.execute(
        "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, "
        "s.starter FROM player_game_stats s JOIN games g USING (game_id) WHERE g.game_date < ?",
        [as_of],
    ).pl()
    static = con.execute("SELECT player_id, position FROM players_static").pl()
    try:
        avail = con.execute(
            "SELECT a.game_id, a.player_id, a.status, a.as_of, a.source FROM player_availability a "
            "JOIN games g ON g.game_id = a.game_id WHERE g.game_date < ? AND a.game_id IS NOT NULL",
            [as_of],
        ).pl()
    except duckdb.CatalogException:
        avail = pl.DataFrame(schema=_AVAIL_SCHEMA)
    return games, pgs, static, avail


def _with_slate_placeholders(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    as_of: date,
    slate: pl.DataFrame,
    roster: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Append the slate games (no scores) and one 'played' placeholder row per
    projected player (minutes 1, stats 0). Features are strictly pre-row, so the
    placeholder values never reach their own row."""
    season = _season_for(as_of)
    g = pl.DataFrame(
        {
            "game_id": slate["game_id"].cast(pl.Utf8),
            "game_date": [as_of] * slate.height,
            "season": [season] * slate.height,
            "home_team": slate["home_team"].cast(pl.Int64),
            "away_team": slate["away_team"].cast(pl.Int64),
            "home_pts": [None] * slate.height,
            "away_pts": [None] * slate.height,
        }
    )
    r = (
        roster.select("game_id", "player_id", "team_id")
        .unique()
        .with_columns(
            pl.lit(1.0).alias("minutes"),
            pl.lit(0).alias("pts"),
            pl.lit(0).alias("reb"),
            pl.lit(0).alias("ast"),
            pl.lit(0).alias("fg3m"),
            pl.lit(False).alias("starter"),
        )
    )
    return (
        pl.concat([games, g], how="diagonal_relaxed"),
        pl.concat([pgs, r], how="diagonal_relaxed"),
    )


def _load_or_fit_models(
    feats: pl.DataFrame,
    as_of: date,
    cfg: ContextResidualConfig,
    cache_dir: Path | None,
    lineups_known: bool = False,
) -> tuple[dict[str, ContextResidualModel], dict[str, Any]]:
    """Fit on played rows strictly before ``as_of`` or reuse a same-date cache.

    The cache key is the date directory plus a fingerprint of the training rows
    (count and newest date), so a re-run the same day does not refit but late
    ingested prior-day games do trigger one."""
    hist = feats.filter(pl.col("game_date") < as_of)
    fp: dict[str, Any] = {
        "n_rows": hist.height,
        "max_date": str(hist["game_date"].max()),
        "cfg": asdict(cfg),
    }
    fp["cfg"].pop("report", None)
    fp["tip_source"] = cfg.report.tip_source  # real vs proxy19 gate => different training features
    if lineups_known:  # different feature set => never share a cache with the T-60 models
        fp["lineups_known"] = True
    # output-only post-processing: does not change the fitted models, so no refit
    fp["cfg"].pop("integer_support", None)
    fp["cfg"].pop("integer_support_stats", None)
    # pts_tail changes the stored calibration arrays and the quantiles: keep the fingerprint of
    # existing caches unchanged while it is off, key on it once it is on
    fp["cfg"].pop("pts_tail_stats", None)
    if fp["cfg"].get("pts_tail") == "off":
        fp["cfg"].pop("pts_tail", None)
    if cache_dir is not None:
        meta_p, model_p = cache_dir / "meta.json", cache_dir / "models.pkl"
        if meta_p.exists() and model_p.exists():
            meta = json.loads(meta_p.read_text())
            if meta.get("fingerprint") == fp:
                with model_p.open("rb") as f:
                    models = pickle.load(f)  # noqa: S301  (local cache we wrote)
                return models, {"cache_hit": True, "fit_seconds": 0.0, **meta["info"]}
    t0 = time.perf_counter()
    models = fit_for_date(feats, as_of, cfg, lineups_known=lineups_known)
    secs = time.perf_counter() - t0
    info = {"n_train_rows": hist.height, "train_through": fp["max_date"]}
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with (cache_dir / "models.pkl").open("wb") as f:
            pickle.dump(models, f)
        (cache_dir / "meta.json").write_text(
            json.dumps({"fingerprint": fp, "info": {**info, "fit_seconds": secs}}, sort_keys=True)
        )
    return models, {"cache_hit": False, "fit_seconds": secs, **info}


def _ctx_row(
    base: dict[str, Any], mean: float, q19: np.ndarray, q199: np.ndarray, thresholds: list[int]
) -> dict[str, Any]:
    p_ge = {str(t): float(np.mean(q199 > t - 0.5)) for t in thresholds}
    std = float(np.std(q199))
    pp = float(base["p_play"])
    return {
        **base,
        "model": MODEL_CONTEXT,
        "mean": mean,
        "std": std,
        "dist_family": MODEL_CONTEXT,
        "dist_params": json.dumps({"mean": mean, "std": std}, sort_keys=True),
        "p_ge": json.dumps(p_ge, sort_keys=True),
        "q10": float(q19[1]),
        "q50": float(q19[9]),
        "q90": float(q19[17]),
        "q_grid": json.dumps([round(float(v), 4) for v in q19]),
        "mean_uncond": pp * mean,
        "p_ge_uncond": json.dumps({k: pp * v for k, v in p_ge.items()}, sort_keys=True),
    }


def predict_slate_context(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    games: pl.DataFrame,
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    *,
    out_players: set[int] | None = None,
    cache_root: Path | None = DEFAULT_MODEL_CACHE,
    cfg: ContextResidualConfig | None = None,
    config: ForwardConfig | None = None,
    official_roster: pl.DataFrame | None = None,
    int_variant: bool = False,
) -> ContextSlateResult:
    """Context-residual props for a slate (primary) plus the recency comparison.

    ``report_out`` maps slate ``game_id`` -> players OUT on the latest official
    report usable for that game (stamped before its real tip-off minus 60 min);
    games absent from it have no report (``has_report=0``, never imputed as
    "nobody out"). Models are fit on played rows with ``game_date < as_of`` and
    cached under ``cache_root/<as_of>/``. Raises on fit failure; the caller
    decides the fallback. ``int_variant`` additionally returns, in
    ``ContextSlateResult.integer_variant``, the same rows with integer-support quantiles
    (docs/INTEGER_QUANTILES.md) for ``cfg.integer_support_stats``; the primary is unchanged."""
    cfg = cfg or ContextResidualConfig()
    exclude = set(out_players or ())
    for s in report_out.values():
        exclude |= s
    rec_cfg = ForwardConfig(sim_stats=()) if config is None else config
    recency = predict_slate(
        con, as_of, games, exclude, config=rec_cfg, official_roster=official_roster
    )
    if recency.is_empty():
        return ContextSlateResult(recency, recency, {"cache_hit": False, "fit_seconds": 0.0})
    recency = recency.with_columns(pl.lit(MODEL_RECENCY).alias("model"))

    t0 = time.perf_counter()
    h_games, h_pgs, static, avail = _history_frames(con, as_of)
    flagged, _ = flagged_from_availability(avail, h_games, cfg.report)
    flagged = {**flagged, **{str(g): set(v) for g, v in report_out.items()}}
    roster = recency.select("game_id", "player_id", "team_id").unique()
    (
        a_games,
        a_pgs,
    ) = _with_slate_placeholders(h_games, h_pgs, as_of, games, roster)
    feats = build_features(a_games, a_pgs, static, flagged, elo_params)
    build_s = time.perf_counter() - t0
    cache_dir = None if cache_root is None else cache_root / as_of.isoformat()
    models, info = _load_or_fit_models(feats, as_of, cfg, cache_dir)
    info["feature_build_seconds"] = build_s

    slate_ids = [str(g) for g in games["game_id"].to_list()]
    slate_feats = feats.filter(pl.col("game_id").is_in(slate_ids))
    taus19 = np.array(QUANTILE_TAUS)
    out_rows: list[dict[str, Any]] = []
    int_rows: list[dict[str, Any]] = []
    n_ctx = 0
    by_key = {(r["game_id"], r["player_id"], r["stat"]): r for r in recency.iter_rows(named=True)}
    done: set[tuple[str, int, str]] = set()
    for stat in PROP_STATS:
        rows, mean, q199 = predict_rows(models, slate_feats, stat, CRPS_TAUS)
        if rows.is_empty():
            continue
        _, _, q19 = predict_rows(models, slate_feats, stat, taus19)
        for i, r in enumerate(rows.iter_rows(named=True)):
            key = (str(r["game_id"]), int(r["player_id"]), stat)
            base = by_key.get(key)
            if base is None:  # not a projected rostered player
                continue
            out_rows.append(
                _ctx_row(base, float(mean[i]), q19[i], q199[i], cfg_thresholds(config, stat))
            )
            if int_variant and stat in cfg.integer_support_stats and not cfg.integer_support:
                int_rows.append(
                    _ctx_row(
                        base,
                        float(mean[i]),
                        to_integer_support(q19[i]),
                        to_integer_support(q199[i]),
                        cfg_thresholds(config, stat),
                    )
                )
            done.add(key)
            n_ctx += 1
    for key, base in by_key.items():
        if key not in done:
            out_rows.append({**base, "model": MODEL_RECENCY_FALLBACK})
    info["n_context_rows"] = n_ctx
    info["n_fallback_rows"] = len(out_rows) - n_ctx
    primary = (
        pl.DataFrame(out_rows, infer_schema_length=None)
        .select(OUTPUT_COLUMNS)
        .sort(["game_id", "team_id", "player_id", "stat"])
    )
    variant = (
        pl.DataFrame(int_rows, infer_schema_length=None)
        .select(OUTPUT_COLUMNS)
        .sort(["game_id", "team_id", "player_id", "stat"])
        if int_rows
        else None
    )
    return ContextSlateResult(primary, recency, info, variant)


def cfg_thresholds(config: ForwardConfig | None, stat: str) -> list[int]:
    return (config or ForwardConfig()).thresholds[stat]
