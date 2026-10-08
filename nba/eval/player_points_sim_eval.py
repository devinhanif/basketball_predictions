"""Maintainer entry point: sim player-points vs. season-average baseline.

CLAUDE.md milestone: "does the sim beat the season-average on points CRPS
(the thing feature-ML could NOT do)?" This module wires
``nba.sim.player_attribution.simulate_game_with_players`` into the existing
props scoring machinery (``nba.props.metrics``/``nba.props.baselines``,
reused unchanged) so the maintainer can run the real 4-season comparison.

**Not run by this agent** (CLAUDE.md "no multi-minute jobs" + this task's
explicit instruction not to run the real/full eval). The author only ran
this against the committed fixture (3 games) as a smoke test -- see
``tests/ml/test_player_points_sim_eval.py::test_smoke_runs_on_fixture``.

ENTRY POINT the maintainer runs for the real evaluation:

    uv run python -c "
    from nba.db.connect import connect
    from nba.eval.player_points_sim_eval import run_sim_vs_baseline_eval
    con = connect('nba.duckdb', read_only=True)
    result = run_sim_vs_baseline_eval(con, n_sims=2000, seed=0)
    print(result.summary())
    "

(Expect real wall-clock time on the full 4-season DB -- ~5000 games x 2000
sims each -- so the maintainer should budget accordingly and not run it
from a 600s-watchdog agent session; pass ``max_games`` for a smaller first
pass before committing to the full run.)

## Known limitation: roster membership (not leakage of minutes/stats VALUES)

Per-game rosters here are taken from which ``player_id``s have a
``player_game_stats`` row for that ``game_id`` -- i.e. "who actually
suited up" -- not from a true as-of roster/injury feed (this schema has no
such table). This *is* a mild form of outcome-adjacent information (a
DNP'd/injured player is correctly excluded), but it is purely about *set
membership*, never about any numeric minutes/points/efficiency VALUE for
the target game -- see ``nba.sim.player_attribution`` module docstring and
``tests/ml/test_player_attribution.py::test_no_leakage_profiles_from_features``
for the stricter guarantee this module *does* uphold: the predicted points
distribution for a given roster is provably invariant to every actual
minutes/stat value in the target game. Replace the roster source with a
real injury/active-roster feed if one becomes available.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.features.player_possession_features import build_player_shot_rates
from nba.features.possession_features import build_team_possession_rates
from nba.features.time_decay import TimeDecayConfig
from nba.props.baselines import build_baseline_features, season_average_baseline
from nba.props.distributions import Distribution
from nba.props.metrics import ConfidenceInterval, crps_array, mean_bias_ci, paired_score_delta_ci
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.sim.player_attribution import profiles_from_features, simulate_game_with_players

DEFAULT_N_SIMS = 2000


def _actual_points(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Ground truth, evaluation-only -- never fed into the sim's inputs."""
    con.execute(
        "SELECT game_id, player_id, team_id, COALESCE(pts, 0) AS pts FROM player_game_stats"
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "team_id": pl.Int64, "pts": pl.Int64}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass
class SimPlayerPointsEvalResult:
    n_player_games: int
    crps_sim: float
    crps_season_avg: float
    crps_sim_vs_season_avg: ConfidenceInterval
    mean_bias_sim: ConfidenceInterval
    #: Per-player-game raw rows (game_id, player_id, y, crps_sim,
    #: crps_season_avg), populated only when ``return_raw=True``. Feeds the
    #: per-bucket model-routing analysis (nba.eval.model_routing), which
    #: needs index-aligned per-row CRPS joined to archetype/SB bucket labels.
    raw: pl.DataFrame | None = None

    def summary(self) -> str:
        return (
            f"n={self.n_player_games}  "
            f"CRPS sim={self.crps_sim:.3f} season_avg={self.crps_season_avg:.3f}  "
            f"sim-season CI={self.crps_sim_vs_season_avg.lo:.3f}.."
            f"{self.crps_sim_vs_season_avg.hi:.3f}  "
            f"mean_bias CI={self.mean_bias_sim.lo:.3f}..{self.mean_bias_sim.hi:.3f}"
        )


def run_sim_vs_baseline_eval(
    con: duckdb.DuckDBPyConnection,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int = 0,
    n_boot: int = 500,
    max_games: int | None = None,
    use_oncourt_usage: bool = False,
    return_raw: bool = False,
    use_time_decay: bool = False,
    time_decay_config: TimeDecayConfig | None = None,
    max_season: int | None = None,
) -> SimPlayerPointsEvalResult:
    """Run the possession sim's per-player points predictions for every game
    with a box score and score them against actuals + the season-average
    baseline on CRPS and mean bias (CLAUDE.md required metrics). See module
    docstring for the exact command the maintainer runs for the real,
    multi-season version; ``max_games`` exists for an incremental first
    pass on the real DB without committing to the full run.
    """
    team_rates = build_team_possession_rates(con)
    if use_time_decay:
        shot_rates = build_player_shot_rates(
            con,
            use_oncourt_usage=use_oncourt_usage,
            use_time_decay=True,
            time_decay_config=time_decay_config,
        )
    else:
        shot_rates = build_player_shot_rates(con, use_oncourt_usage=use_oncourt_usage)
    minutes_feats = build_minutes_features(con)
    minutes_dists = predict_minutes(minutes_feats)
    minutes_proj = minutes_feats.select(["game_id", "player_id"]).with_columns(
        pl.Series("projected_minutes", [d.mean() for d in minutes_dists])
    )
    actual = _actual_points(con)
    season_feats = build_baseline_features(con, "pts")

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
    if max_season is not None:
        # Restrict to seasons <= max_season (use 2024 to keep the 2025 holdout untouched).
        ok = {
            r[0]
            for r in con.execute(
                "SELECT game_id FROM games WHERE season <= ?", [max_season]
            ).fetchall()
        }
        game_ids = [g for g in game_ids if g in ok]
    if max_games is not None:
        game_ids = game_ids[:max_games]

    row_keys: list[tuple[str, int]] = []
    pred_means: list[float] = []
    actual_pts: list[float] = []
    sim_dists: list[Distribution] = []

    for game_id in game_ids:
        g = games.filter(pl.col("game_id") == game_id)
        home_row = g.filter(pl.col("is_home"))
        away_row = g.filter(~pl.col("is_home"))
        if home_row.height == 0 or away_row.height == 0:
            continue
        home_team = int(home_row[0, "team_id"])
        away_team = int(away_row[0, "team_id"])
        roster = actual.filter(pl.col("game_id") == game_id)
        home_ids = roster.filter(pl.col("team_id") == home_team)["player_id"].to_list()
        away_ids = roster.filter(pl.col("team_id") == away_team)["player_id"].to_list()
        if not home_ids or not away_ids:
            continue

        game_shot_rates = shot_rates.filter(pl.col("game_id") == game_id)
        game_minutes = minutes_proj.filter(pl.col("game_id") == game_id)
        proj_minutes_map = {
            int(r["player_id"]): float(r["projected_minutes"])
            for r in game_minutes.iter_rows(named=True)
        }
        home_profiles = profiles_from_features(game_shot_rates, proj_minutes_map, home_ids)
        away_profiles = profiles_from_features(game_shot_rates, proj_minutes_map, away_ids)

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
        )
        for side_ids, dists in (
            (home_ids, result.home_player_points),
            (away_ids, result.away_player_points),
        ):
            for pid in side_ids:
                dist = dists.get(pid)
                if dist is None:
                    continue
                y_row = roster.filter(pl.col("player_id") == pid)
                if y_row.height == 0:
                    continue
                row_keys.append((game_id, pid))
                pred_means.append(dist.mean())
                actual_pts.append(float(y_row[0, "pts"]))
                sim_dists.append(dist)

    n = len(actual_pts)
    if n == 0:
        nan_ci = ConfidenceInterval(float("nan"), float("nan"), float("nan"), 0)
        return SimPlayerPointsEvalResult(
            n_player_games=0,
            crps_sim=float("nan"),
            crps_season_avg=float("nan"),
            crps_sim_vs_season_avg=nan_ci,
            mean_bias_sim=nan_ci,
        )

    y_arr = np.array(actual_pts, dtype=float)
    pred_arr = np.array(pred_means, dtype=float)
    crps_sim_arr = crps_array(sim_dists, y_arr)

    # Align the season-average baseline onto exactly the (game_id,
    # player_id) rows scored above, same order.
    keys_df = pl.DataFrame(
        {"game_id": [k[0] for k in row_keys], "player_id": [k[1] for k in row_keys]}
    )
    season_aligned = keys_df.join(season_feats, on=["game_id", "player_id"], how="left")
    season_dists = season_average_baseline(season_aligned, "pts")
    crps_season_arr = crps_array(list(season_dists), y_arr)

    raw = None
    if return_raw:
        raw = pl.DataFrame(
            {
                "game_id": [k[0] for k in row_keys],
                "player_id": [k[1] for k in row_keys],
                "y": y_arr,
                "pred": pred_arr,
                "crps_sim": crps_sim_arr,
                "crps_season_avg": crps_season_arr,
            }
        )

    return SimPlayerPointsEvalResult(
        n_player_games=n,
        crps_sim=float(np.nanmean(crps_sim_arr)),
        crps_season_avg=float(np.nanmean(crps_season_arr)),
        crps_sim_vs_season_avg=paired_score_delta_ci(
            crps_sim_arr, crps_season_arr, n_boot=n_boot, seed=seed
        ),
        mean_bias_sim=mean_bias_ci(pred_arr, y_arr, n_boot=n_boot, seed=seed),
        raw=raw,
    )
