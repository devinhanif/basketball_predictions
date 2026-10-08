"""Maintainer entry point: usage-redistribution Option A vs Option B vs baseline.

CLAUDE.md / ``docs/NEXT_OPTIONS.md`` §2 task: A/B (head-to-head) test
``nba.sim.usage_redistribution``'s two options against the shipped flat-
``shot_share_prior`` baseline, on points/PRA CRPS for TEAMMATES in games
where a projected-top-usage player is flagged absent -- restricted sample,
reported explicitly, plus the full-sample CRPS to prove both options are a
no-op when nobody is out.

Wires ``nba.sim.usage_redistribution.apply_usage_redistribution`` into the
exact same ``nba.sim.player_attribution``/``nba.props.metrics`` machinery
``nba.eval.player_points_sim_eval`` already uses -- same pattern, same
metrics, same paired-bootstrap convention.

**Not run by this agent** (CLAUDE.md "no multi-minute jobs" + this task's
explicit instruction not to run the real/full eval). The author only ran
this against the committed fixture / tiny synthetic DBs as a smoke test --
see ``tests/ml/test_usage_redistribution_eval.py::test_smoke_runs_on_fixture``.

ENTRY POINT the maintainer runs for the real evaluation:

    uv run python -c "
    from nba.db.connect import connect
    from nba.eval.usage_redistribution_eval import run_usage_redistribution_eval
    con = connect('nba.duckdb', read_only=True)
    result = run_usage_redistribution_eval(con, n_sims=2000, seed=0)
    print(result.summary())
    "

(Expect real wall-clock time on the full 4-season DB, same budget caveat as
``nba.eval.player_points_sim_eval``; pass ``max_games`` for a smaller first
pass before committing to the full run.)

## Simplifications documented honestly (not bugs)

1. **Absence signal.** "Projected absent" = this module's own as-of
   ``predict_minutes`` projection falling below
   ``absence_minutes_threshold`` for a roster player who is a top-
   ``top_n_usage``-usage player on their team (by ``shot_share_prior``).
   Roster membership itself still comes from ``player_game_stats``
   presence (the same documented "set membership, never a box-score VALUE"
   limitation as ``nba.eval.player_points_sim_eval``) -- a player who is
   so completely out that they have no box-score row at all cannot be
   flagged by this eval (their historical reference function,
   ``nba.sim.usage_redistribution.historical_player_boost_reference``,
   does not have this limitation since it also consumes
   ``player_game_stats`` for roster membership including 0-minute DNPs,
   but this eval's own game-by-game loop inherits the same roster source
   as the points eval it's built on top of).
2. **One reference computation, not per-game-as-of.** The historical
   reference scalars (Option A / Option B) are computed ONCE, as of the
   earliest evaluated game's date (or a caller-supplied ``reference_as_of``),
   rather than recomputed as-of every single game in the loop --
   recomputing a full historical SQL+shrinkage pass per game would turn an
   already multi-minute full-DB eval into something far slower, for a
   reference value that (being an as-of CUMULATIVE average) only moves
   slowly game-to-game. A stricter per-game-as-of version is a documented
   follow-up if this one-shot version's restricted-sample result looks
   promising enough to refine.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.features.player_possession_features import build_player_shot_rates
from nba.features.possession_features import build_team_possession_rates
from nba.props.baselines import build_baseline_features
from nba.props.distributions import Distribution
from nba.props.metrics import ConfidenceInterval, crps_array, paired_score_delta_ci
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.sim.player_attribution import profiles_from_features, simulate_game_with_players
from nba.sim.usage_redistribution import (
    UsageRedistributionConfig,
    apply_usage_redistribution,
    historical_player_boost_reference,
    historical_team_boost_reference,
)

DEFAULT_N_SIMS = 2000
DEFAULT_ABSENCE_MINUTES_THRESHOLD = 5.0


def _actual_points(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Ground truth, evaluation-only -- never fed into the sim's inputs."""
    con.execute(
        "SELECT game_id, player_id, team_id, COALESCE(pts, 0) AS pts FROM player_game_stats"
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "team_id": pl.Int64, "pts": pl.Int64}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def _game_dates(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute("SELECT game_id, game_date FROM games")
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "game_date": pl.Date}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def _top_usage_ids(
    shot_rates_for_game: pl.DataFrame, player_ids: list[int], top_n: int
) -> set[int]:
    rows = shot_rates_for_game.filter(pl.col("player_id").is_in(player_ids))
    ranked = rows.sort("shot_share_prior", descending=True)
    return set(ranked.head(top_n).get_column("player_id").to_list())


def _absent_ids(
    ids: list[int],
    game_shot_rates: pl.DataFrame,
    proj_minutes_map: dict[int, float],
    top_n_usage: int,
    absence_minutes_threshold: float,
) -> set[int]:
    """Top-``top_n_usage``-usage roster players whose as-of projected
    minutes fall below ``absence_minutes_threshold`` -- the injected
    "projected absent" signal (never the game's own actual minutes/stats).
    """
    top_ids = _top_usage_ids(game_shot_rates, ids, top_n_usage)
    return {pid for pid in top_ids if proj_minutes_map.get(pid, 0.0) < absence_minutes_threshold}


@dataclass
class UsageRedistributionEvalResult:
    n_full_sample: int
    n_restricted_sample: int
    crps_baseline_full: float
    crps_option_a_full: float
    crps_option_b_full: float
    full_sample_ci_a_vs_baseline: ConfidenceInterval
    full_sample_ci_b_vs_baseline: ConfidenceInterval
    crps_baseline_restricted: float
    crps_option_a_restricted: float
    crps_option_b_restricted: float
    restricted_ci_a_vs_baseline: ConfidenceInterval
    restricted_ci_b_vs_baseline: ConfidenceInterval
    restricted_ci_a_vs_b: ConfidenceInterval
    boost_a: float
    boost_a_n: int
    boost_b: float
    boost_b_n: int

    def summary(self) -> str:
        return (
            f"FULL n={self.n_full_sample}  CRPS baseline={self.crps_baseline_full:.4f} "
            f"A={self.crps_option_a_full:.4f} B={self.crps_option_b_full:.4f}  "
            f"A-base CI={self.full_sample_ci_a_vs_baseline.lo:.4f}.."
            f"{self.full_sample_ci_a_vs_baseline.hi:.4f}  "
            f"B-base CI={self.full_sample_ci_b_vs_baseline.lo:.4f}.."
            f"{self.full_sample_ci_b_vs_baseline.hi:.4f}\n"
            f"RESTRICTED (>=1 projected-out top-{2}-usage teammate) "
            f"n={self.n_restricted_sample}  CRPS baseline={self.crps_baseline_restricted:.4f} "
            f"A={self.crps_option_a_restricted:.4f} B={self.crps_option_b_restricted:.4f}  "
            f"A-base CI={self.restricted_ci_a_vs_baseline.lo:.4f}.."
            f"{self.restricted_ci_a_vs_baseline.hi:.4f}  "
            f"B-base CI={self.restricted_ci_b_vs_baseline.lo:.4f}.."
            f"{self.restricted_ci_b_vs_baseline.hi:.4f}  "
            f"A-B CI={self.restricted_ci_a_vs_b.lo:.4f}..{self.restricted_ci_a_vs_b.hi:.4f}\n"
            f"boost_a={self.boost_a:.4f} (n={self.boost_a_n})  "
            f"boost_b={self.boost_b:.4f} (n={self.boost_b_n})"
        )


def run_usage_redistribution_eval(
    con: duckdb.DuckDBPyConnection,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int = 0,
    n_boot: int = 500,
    max_games: int | None = None,
    top_n_usage: int = 2,
    absence_minutes_threshold: float = DEFAULT_ABSENCE_MINUTES_THRESHOLD,
    max_boost_fraction: float = 0.3,
    boost_pseudo_count: float = 20.0,
    reference_as_of: dt.date | None = None,
) -> UsageRedistributionEvalResult:
    """Head-to-head Option A vs Option B vs baseline, restricted + full sample.

    See module docstring for the exact command the maintainer runs for the
    real, multi-season version, and the two documented simplifications.
    """
    team_rates = build_team_possession_rates(con)
    shot_rates = build_player_shot_rates(con)
    minutes_feats = build_minutes_features(con)
    minutes_dists = predict_minutes(minutes_feats)
    minutes_proj = minutes_feats.select(["game_id", "player_id"]).with_columns(
        pl.Series("projected_minutes", [d.mean() for d in minutes_dists])
    )
    actual = _actual_points(con)
    season_feats = build_baseline_features(con, "pts")
    dates = _game_dates(con)

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
    if max_games is not None:
        game_ids = game_ids[:max_games]

    if reference_as_of is None:
        dated = dates.filter(pl.col("game_id").is_in(game_ids)).sort("game_date")
        reference_as_of = dated[0, "game_date"] if dated.height else None

    boost_a, boost_a_n = (0.0, 0)
    boost_b, boost_b_n = (0.0, 0)
    if reference_as_of is not None:
        boost_a, boost_a_n = historical_player_boost_reference(
            con, reference_as_of, top_n_usage=top_n_usage, pseudo_count=boost_pseudo_count
        )
        boost_b, boost_b_n = historical_team_boost_reference(
            con, reference_as_of, top_n_usage=top_n_usage, pseudo_count=boost_pseudo_count
        )

    config_a = UsageRedistributionConfig(
        enabled=True,
        method="player",
        top_n_usage=top_n_usage,
        max_boost_fraction=max_boost_fraction,
        boost_pseudo_count=boost_pseudo_count,
    )
    config_b = UsageRedistributionConfig(
        enabled=True,
        method="team",
        top_n_usage=top_n_usage,
        max_boost_fraction=max_boost_fraction,
        boost_pseudo_count=boost_pseudo_count,
    )

    row_keys: list[tuple[str, int]] = []
    actual_pts: list[float] = []
    is_restricted: list[bool] = []
    baseline_dists: list[Distribution] = []
    option_a_dists: list[Distribution] = []
    option_b_dists: list[Distribution] = []

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

        home_absent = _absent_ids(
            home_ids, game_shot_rates, proj_minutes_map, top_n_usage, absence_minutes_threshold
        )
        away_absent = _absent_ids(
            away_ids, game_shot_rates, proj_minutes_map, top_n_usage, absence_minutes_threshold
        )
        restricted = bool(home_absent or away_absent)

        home_profiles = profiles_from_features(game_shot_rates, proj_minutes_map, home_ids)
        away_profiles = profiles_from_features(game_shot_rates, proj_minutes_map, away_ids)

        sim_kwargs: dict[str, Any] = dict(
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

        baseline_result = simulate_game_with_players(home_profiles, away_profiles, **sim_kwargs)
        a_home = apply_usage_redistribution(home_profiles, home_absent, config_a, boost_a)
        a_away = apply_usage_redistribution(away_profiles, away_absent, config_a, boost_a)
        option_a_result = simulate_game_with_players(a_home, a_away, **sim_kwargs)
        b_home = apply_usage_redistribution(home_profiles, home_absent, config_b, boost_b)
        b_away = apply_usage_redistribution(away_profiles, away_absent, config_b, boost_b)
        option_b_result = simulate_game_with_players(b_home, b_away, **sim_kwargs)

        for side_ids, absent_ids, base_d, a_d, b_d in (
            (
                home_ids,
                home_absent,
                baseline_result.home_player_points,
                option_a_result.home_player_points,
                option_b_result.home_player_points,
            ),
            (
                away_ids,
                away_absent,
                baseline_result.away_player_points,
                option_a_result.away_player_points,
                option_b_result.away_player_points,
            ),
        ):
            for pid in side_ids:
                if pid in absent_ids:
                    continue  # score TEAMMATES only, per task scope
                base_dist = base_d.get(pid)
                a_dist = a_d.get(pid)
                b_dist = b_d.get(pid)
                if base_dist is None or a_dist is None or b_dist is None:
                    continue
                y_row = roster.filter(pl.col("player_id") == pid)
                if y_row.height == 0:
                    continue
                row_keys.append((game_id, pid))
                actual_pts.append(float(y_row[0, "pts"]))
                is_restricted.append(restricted)
                baseline_dists.append(base_dist)
                option_a_dists.append(a_dist)
                option_b_dists.append(b_dist)

    n = len(actual_pts)
    if n == 0:
        nan_ci = ConfidenceInterval(float("nan"), float("nan"), float("nan"), 0)
        return UsageRedistributionEvalResult(
            n_full_sample=0,
            n_restricted_sample=0,
            crps_baseline_full=float("nan"),
            crps_option_a_full=float("nan"),
            crps_option_b_full=float("nan"),
            full_sample_ci_a_vs_baseline=nan_ci,
            full_sample_ci_b_vs_baseline=nan_ci,
            crps_baseline_restricted=float("nan"),
            crps_option_a_restricted=float("nan"),
            crps_option_b_restricted=float("nan"),
            restricted_ci_a_vs_baseline=nan_ci,
            restricted_ci_b_vs_baseline=nan_ci,
            restricted_ci_a_vs_b=nan_ci,
            boost_a=boost_a,
            boost_a_n=boost_a_n,
            boost_b=boost_b,
            boost_b_n=boost_b_n,
        )

    y_arr = np.array(actual_pts, dtype=float)
    restricted_mask = np.array(is_restricted, dtype=bool)
    crps_base = crps_array(baseline_dists, y_arr)
    crps_a = crps_array(option_a_dists, y_arr)
    crps_b = crps_array(option_b_dists, y_arr)

    del season_feats  # reserved for a future PRA/season-average comparison column

    n_restricted = int(restricted_mask.sum())
    if n_restricted > 0:
        ci_a_r = paired_score_delta_ci(
            crps_a[restricted_mask], crps_base[restricted_mask], n_boot=n_boot, seed=seed
        )
        ci_b_r = paired_score_delta_ci(
            crps_b[restricted_mask], crps_base[restricted_mask], n_boot=n_boot, seed=seed
        )
        ci_ab_r = paired_score_delta_ci(
            crps_a[restricted_mask], crps_b[restricted_mask], n_boot=n_boot, seed=seed
        )
        crps_base_r = float(np.nanmean(crps_base[restricted_mask]))
        crps_a_r = float(np.nanmean(crps_a[restricted_mask]))
        crps_b_r = float(np.nanmean(crps_b[restricted_mask]))
    else:
        nan_ci = ConfidenceInterval(float("nan"), float("nan"), float("nan"), 0)
        ci_a_r = ci_b_r = ci_ab_r = nan_ci
        crps_base_r = crps_a_r = crps_b_r = float("nan")

    return UsageRedistributionEvalResult(
        n_full_sample=n,
        n_restricted_sample=n_restricted,
        crps_baseline_full=float(np.nanmean(crps_base)),
        crps_option_a_full=float(np.nanmean(crps_a)),
        crps_option_b_full=float(np.nanmean(crps_b)),
        full_sample_ci_a_vs_baseline=paired_score_delta_ci(
            crps_a, crps_base, n_boot=n_boot, seed=seed
        ),
        full_sample_ci_b_vs_baseline=paired_score_delta_ci(
            crps_b, crps_base, n_boot=n_boot, seed=seed
        ),
        crps_baseline_restricted=crps_base_r,
        crps_option_a_restricted=crps_a_r,
        crps_option_b_restricted=crps_b_r,
        restricted_ci_a_vs_baseline=ci_a_r,
        restricted_ci_b_vs_baseline=ci_b_r,
        restricted_ci_a_vs_b=ci_ab_r,
        boost_a=boost_a,
        boost_a_n=boost_a_n,
        boost_b=boost_b,
        boost_b_n=boost_b_n,
    )


__all__ = ["UsageRedistributionEvalResult", "run_usage_redistribution_eval"]
