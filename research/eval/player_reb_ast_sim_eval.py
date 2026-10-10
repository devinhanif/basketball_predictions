"""Maintainer entry point: sim player-rebounds/assists vs. season-average
baseline.

CLAUDE.md milestone (rung-3 player attribution extension): "does the sim's
rebound/assist heads beat the season-average baseline on CRPS + mean bias,
the same way ``research.eval.player_points_sim_eval`` already checks for
points?" This module wires
``research.sim.player_attribution.simulate_game_with_players``'s rebound and
assist outputs into the existing props scoring machinery
(``nba.props.metrics``/``nba.props.baselines``, reused unchanged), the same
pattern as the points eval.

**Not run by this agent** (CLAUDE.md "no multi-minute jobs" + this task's
explicit instruction not to run the real/full eval). The author only ran
this against the committed fixture (3 games) as a smoke test -- see
``tests/ml/test_player_reb_ast_sim_eval.py::test_smoke_runs_on_fixture``.

ENTRY POINT the maintainer runs for the real evaluation:

    uv run python -c "
    from nba.db.connect import connect
    from research.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
    con = connect('nba.duckdb', read_only=True)
    reb_result, ast_result = run_reb_ast_sim_vs_baseline_eval(con, n_sims=2000, seed=0)
    print('REB', reb_result.summary())
    print('AST', ast_result.summary())
    "

(Expect real wall-clock time on the full 4-season DB, same budget caveat as
``research.eval.player_points_sim_eval``; pass ``max_games`` for a smaller first
pass before committing to the full run.)

## Known limitations (inherited from the points eval + this milestone's own)

- Roster membership is taken from who has a ``player_game_stats`` row for
  the game (not a true as-of injury/active-roster feed) -- same documented
  limitation as ``research.eval.player_points_sim_eval`` (set-membership only,
  never a numeric box-score VALUE).
- The sim's rebound/assist attribution itself rests on the documented,
  not-yet-tuned-to-this-project's-own-data constants in
  ``research.sim.player_attribution`` (``MISSED_FG_SHARE_OF_ZERO_OUTCOME``,
  ``DEFAULT_ASSISTED_FG_RATE``) and ``nba.features.
  player_rebound_assist_features`` (league default rates) -- this eval
  module reports whatever those produce; if CRPS/bias look bad on the real
  DB, suspect those constants first before the attribution logic itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.features.possession_features import build_team_possession_rates
from nba.props.baselines import build_baseline_features, season_average_baseline
from nba.props.distributions import Distribution
from nba.props.metrics import ConfidenceInterval, crps_array, mean_bias_ci, paired_score_delta_ci
from nba.props.minutes import build_minutes_features, predict_minutes
from research.features.played_baseline import build_played_baseline_features
from research.features.player_possession_features import build_player_shot_rates
from research.features.player_rebound_assist_features import build_player_reb_ast_rates
from research.features.time_decay import TimeDecayConfig
from research.sim.player_attribution import (
    DEFAULT_ASSISTED_FG_RATE,
    profiles_from_features,
    simulate_game_with_players,
)

DEFAULT_N_SIMS = 2000


def _actual_stat(con: duckdb.DuckDBPyConnection, stat: str) -> pl.DataFrame:
    """Ground truth, evaluation-only -- never fed into the sim's inputs."""
    if stat not in {"reb", "ast"}:
        raise ValueError(f"unsupported stat {stat!r}")
    con.execute(
        f"SELECT game_id, player_id, team_id, COALESCE({stat}, 0) AS y FROM player_game_stats"
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "team_id": pl.Int64, "y": pl.Int64}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass
class SimPlayerStatEvalResult:
    stat: str
    n_player_games: int
    crps_sim: float
    crps_season_avg: float
    crps_sim_vs_season_avg: ConfidenceInterval
    mean_bias_sim: ConfidenceInterval
    #: Per-player-game raw rows (game_id, player_id, y, crps_sim,
    #: crps_season_avg), populated only when ``return_raw=True`` -- feeds the
    #: per-bucket model-routing analysis (research.eval.model_routing).
    raw: pl.DataFrame | None = None
    #: Per-row sim distributions aligned with ``raw`` (only when
    #: ``return_dists=True``); used by research.eval.fair_rematch.
    dists: list[Distribution] | None = None

    def summary(self) -> str:
        return (
            f"stat={self.stat} n={self.n_player_games}  "
            f"CRPS sim={self.crps_sim:.3f} season_avg={self.crps_season_avg:.3f}  "
            f"sim-season CI={self.crps_sim_vs_season_avg.lo:.3f}.."
            f"{self.crps_sim_vs_season_avg.hi:.3f}  "
            f"mean_bias CI={self.mean_bias_sim.lo:.3f}..{self.mean_bias_sim.hi:.3f}"
        )


def _nan_result(stat: str) -> SimPlayerStatEvalResult:
    nan_ci = ConfidenceInterval(float("nan"), float("nan"), float("nan"), 0)
    return SimPlayerStatEvalResult(
        stat=stat,
        n_player_games=0,
        crps_sim=float("nan"),
        crps_season_avg=float("nan"),
        crps_sim_vs_season_avg=nan_ci,
        mean_bias_sim=nan_ci,
    )


def run_reb_ast_sim_vs_baseline_eval(
    con: duckdb.DuckDBPyConnection,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int = 0,
    n_boot: int = 500,
    max_games: int | None = None,
    return_raw: bool = False,
    played_only: bool = False,
    only_game_ids: list[str] | None = None,
    return_dists: bool = False,
    team_rates: pl.DataFrame | None = None,
    minutes_proj: pl.DataFrame | None = None,
    min_season: int | None = None,
    max_season: int | None = None,
    sample_games: int | None = None,
    use_time_decay: bool = False,
    time_decay_config: TimeDecayConfig | None = None,
) -> tuple[SimPlayerStatEvalResult, SimPlayerStatEvalResult]:
    """Run the possession sim's per-player rebounds + assists predictions
    for every game with a box score and score both against actuals + the
    season-average baseline on CRPS and mean bias (CLAUDE.md required
    metrics). Returns ``(rebounds_result, assists_result)``. See module
    docstring for the exact command the maintainer runs for the real,
    multi-season version; ``max_games`` exists for an incremental first
    pass on the real DB without committing to the full run.

    ``played_only`` (default OFF, byte-identical when off): shot AND
    rebound/assist rates are built from played games only (numerators and
    denominators), and the season-average baseline is the played-only
    expanding mean/std. ``use_time_decay``/``time_decay_config`` apply to the
    SHOT rates only (the reb/ast builder has no time-decay path). The
    season/sample/``only_game_ids`` selectors mirror
    :func:`research.eval.player_points_sim_eval.run_sim_vs_baseline_eval`.
    """
    if team_rates is None:
        team_rates = build_team_possession_rates(con)
    if use_time_decay:
        shot_rates = build_player_shot_rates(
            con, use_time_decay=True, time_decay_config=time_decay_config, played_only=played_only
        )
    elif played_only:
        shot_rates = build_player_shot_rates(con, played_only=True)
    else:
        shot_rates = build_player_shot_rates(con)
    reb_ast_rates = (
        build_player_reb_ast_rates(con, played_only=True)
        if played_only
        else build_player_reb_ast_rates(con)
    )
    if minutes_proj is None:
        minutes_feats = build_minutes_features(con)
        minutes_dists = predict_minutes(minutes_feats)
        minutes_proj = minutes_feats.select(["game_id", "player_id"]).with_columns(
            pl.Series("projected_minutes", [d.mean() for d in minutes_dists])
        )
    actual_reb = _actual_stat(con, "reb")
    actual_ast = _actual_stat(con, "ast")
    if played_only:
        season_feats_reb = build_played_baseline_features(con, "reb")
        season_feats_ast = build_played_baseline_features(con, "ast")
    else:
        season_feats_reb = build_baseline_features(con, "reb")
        season_feats_ast = build_baseline_features(con, "ast")

    games = team_rates.select(
        [
            "game_id",
            "team_id",
            "is_home",
            "off_rtg_prior",
            "def_rtg_prior",
            "pace_prior",
            "league_avg_ppp_asof",
        ]
    )
    game_ids = games.select("game_id").unique().to_series().sort().to_list()
    if min_season is not None or max_season is not None:
        lo = -(10**9) if min_season is None else min_season
        hi = 10**9 if max_season is None else max_season
        ok = {
            r[0]
            for r in con.execute(
                "SELECT game_id FROM games WHERE season BETWEEN ? AND ?", [lo, hi]
            ).fetchall()
        }
        game_ids = [g for g in game_ids if g in ok]
    if only_game_ids is not None:
        keep = set(only_game_ids)
        game_ids = [g for g in game_ids if g in keep]
    if sample_games is not None and len(game_ids) > sample_games:
        pick = np.linspace(0, len(game_ids) - 1, sample_games).round().astype(int)
        game_ids = [game_ids[i] for i in sorted(set(pick.tolist()))]
    if max_games is not None:
        game_ids = game_ids[:max_games]

    row_keys: list[tuple[str, int]] = []
    reb_pred_means: list[float] = []
    ast_pred_means: list[float] = []
    actual_reb_vals: list[float] = []
    actual_ast_vals: list[float] = []
    reb_dists: list[Distribution] = []
    ast_dists: list[Distribution] = []

    for game_id in game_ids:
        g = games.filter(pl.col("game_id") == game_id)
        home_row = g.filter(pl.col("is_home"))
        away_row = g.filter(~pl.col("is_home"))
        if home_row.height == 0 or away_row.height == 0:
            continue
        home_team = int(home_row[0, "team_id"])
        away_team = int(away_row[0, "team_id"])
        # Roster membership only (set membership, not a box-score VALUE) --
        # same documented limitation as research.eval.player_points_sim_eval.
        roster_reb = actual_reb.filter(pl.col("game_id") == game_id)
        home_ids = roster_reb.filter(pl.col("team_id") == home_team)["player_id"].to_list()
        away_ids = roster_reb.filter(pl.col("team_id") == away_team)["player_id"].to_list()
        if not home_ids or not away_ids:
            continue

        game_shot_rates = shot_rates.filter(pl.col("game_id") == game_id)
        game_reb_ast_rates = reb_ast_rates.filter(pl.col("game_id") == game_id)
        game_minutes = minutes_proj.filter(pl.col("game_id") == game_id)
        proj_minutes_map = {
            int(r["player_id"]): float(r["projected_minutes"])
            for r in game_minutes.iter_rows(named=True)
        }
        home_profiles = profiles_from_features(
            game_shot_rates, proj_minutes_map, home_ids, reb_ast_rates=game_reb_ast_rates
        )
        away_profiles = profiles_from_features(
            game_shot_rates, proj_minutes_map, away_ids, reb_ast_rates=game_reb_ast_rates
        )

        def _team_assisted_fg_rate(reb_ast: pl.DataFrame, team_id: int, default: float) -> float:
            team_rows = reb_ast.filter(pl.col("team_id") == team_id)
            if team_rows.height == 0:
                return default
            return float(team_rows[0, "assisted_fg_rate_prior"])

        result = simulate_game_with_players(
            home_profiles,
            away_profiles,
            home_off_rtg=float(home_row[0, "off_rtg_prior"]),
            home_def_rtg=float(home_row[0, "def_rtg_prior"]),
            home_pace=float(home_row[0, "pace_prior"]),
            away_off_rtg=float(away_row[0, "off_rtg_prior"]),
            away_def_rtg=float(away_row[0, "def_rtg_prior"]),
            away_pace=float(away_row[0, "pace_prior"]),
            league_avg_ppp=float(home_row[0, "league_avg_ppp_asof"]),
            n_sims=n_sims,
            seed=seed,
            home_assisted_fg_rate=_team_assisted_fg_rate(
                game_reb_ast_rates, home_team, DEFAULT_ASSISTED_FG_RATE
            ),
            away_assisted_fg_rate=_team_assisted_fg_rate(
                game_reb_ast_rates, away_team, DEFAULT_ASSISTED_FG_RATE
            ),
        )

        roster_ast = actual_ast.filter(pl.col("game_id") == game_id)
        for side_ids, reb_dist_map, ast_dist_map in (
            (home_ids, result.home_player_rebounds, result.home_player_assists),
            (away_ids, result.away_player_rebounds, result.away_player_assists),
        ):
            for pid in side_ids:
                reb_dist = reb_dist_map.get(pid)
                ast_dist = ast_dist_map.get(pid)
                if reb_dist is None or ast_dist is None:
                    continue
                reb_y_row = roster_reb.filter(pl.col("player_id") == pid)
                ast_y_row = roster_ast.filter(pl.col("player_id") == pid)
                if reb_y_row.height == 0 or ast_y_row.height == 0:
                    continue
                row_keys.append((game_id, pid))
                reb_pred_means.append(reb_dist.mean())
                ast_pred_means.append(ast_dist.mean())
                actual_reb_vals.append(float(reb_y_row[0, "y"]))
                actual_ast_vals.append(float(ast_y_row[0, "y"]))
                reb_dists.append(reb_dist)
                ast_dists.append(ast_dist)

    n = len(row_keys)
    if n == 0:
        return _nan_result("reb"), _nan_result("ast")

    keys_df = pl.DataFrame(
        {"game_id": [k[0] for k in row_keys], "player_id": [k[1] for k in row_keys]}
    )

    def _score(
        stat: str,
        y_vals: list[float],
        pred_means: list[float],
        sim_dists: list[Distribution],
        season_feats: pl.DataFrame,
    ) -> SimPlayerStatEvalResult:
        y_arr = np.array(y_vals, dtype=float)
        pred_arr = np.array(pred_means, dtype=float)
        crps_sim_arr = crps_array(sim_dists, y_arr)
        season_aligned = keys_df.join(season_feats, on=["game_id", "player_id"], how="left")
        season_dists = season_average_baseline(season_aligned, stat)
        crps_season_arr = crps_array(list(season_dists), y_arr)
        raw = None
        if return_raw:
            raw = keys_df.with_columns(
                y=pl.Series(y_arr),
                crps_sim=pl.Series(crps_sim_arr),
                crps_season_avg=pl.Series(crps_season_arr),
            )
        return SimPlayerStatEvalResult(
            stat=stat,
            n_player_games=n,
            crps_sim=float(np.nanmean(crps_sim_arr)),
            crps_season_avg=float(np.nanmean(crps_season_arr)),
            crps_sim_vs_season_avg=paired_score_delta_ci(
                crps_sim_arr, crps_season_arr, n_boot=n_boot, seed=seed
            ),
            mean_bias_sim=mean_bias_ci(pred_arr, y_arr, n_boot=n_boot, seed=seed),
            raw=raw,
            dists=sim_dists if return_dists else None,
        )

    reb_result = _score("reb", actual_reb_vals, reb_pred_means, reb_dists, season_feats_reb)
    ast_result = _score("ast", actual_ast_vals, ast_pred_means, ast_dists, season_feats_ast)
    return reb_result, ast_result
