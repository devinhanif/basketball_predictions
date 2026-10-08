"""Walk-forward tuning + evaluation of season time-decay carryover (cold-start method 4).

PRE-REGISTERED DECISION RULE (written before any real-data run; do not edit
after seeing a result):

    Keep ``use_time_decay`` ON (flip the default) ONLY IF, on the
    out-of-selection REPORT seasons, with the tuned parameters:

    1. FIRST-15-GAMES slice (player's first 15 appearances of a season):
       the clustered-by-game paired bootstrap CI of
       ``mean(abs_err_decay - abs_err_off)`` lies entirely BELOW 0; AND
    2. REST-OF-SEASON slice: no regression, i.e. the same clustered CI's
       upper bound is <= ``REST_NONINFERIORITY_MARGIN`` (0.02 points) AND the
       point estimate is <= 0.

    Otherwise the flag stays OFF and the result is reported as negative.
    Exactly this rule, evaluated on ``report`` seasons only, decides; the
    in-selection numbers are optimistic and informational.

FROZEN HOLDOUT: season 2025 is never read for scoring or tuning
(``MAX_TUNE_SEASON = 2024``; requesting a later season raises). Decay only
looks backward, so excluding 2025 rows from scoring is sufficient for the
tuned numbers to be holdout-clean.

OBJECTIVE / WHY A PROXY: the full possession sim is ~2000 sims x ~5000 games
per evaluation, far too slow for a grid of ~15 builds. The proxy scores the
per-player points projection the sim's heads consume, built from exactly the
rates the flag changes (``build_player_shot_rates``):

    pred_pts = shot_share_prior * LEAGUE_TEAM_FGA
               * (sum_z zone_mix_z * zone_fg_pct_z * zone_points_z
                  + ft_trip_rate * ft_pct)

scored against the realized box-score points of that appearance. Team FGA is
a constant (not the realized game volume) so no outcome information enters.
For a point forecast, CRPS reduces to absolute error, so the per-row score
is ``abs_err`` (points) and its mean is the MAE. Minutes are NOT modelled
(identical across arms, so it cancels in the paired delta but inflates the
absolute level). Points only: ``build_player_reb_ast_rates`` is owned
elsewhere and is not wired to the flag, so reb/ast are not tuned here.
The pseudo-count ``TimeDecayConfig.k`` is unused by this wiring (shot-rate
shrinkage keeps its own module constants) and is not tuned.

SEARCH: small coordinate grid (stage 1: half_life x carryover_decay_weight;
stage 2: peak_age and age_curve_width one at a time around the stage-1 best),
on SELECTION seasons only (all tunable seasons except the last); the report
is on the last tunable season. Memory: only needed columns are kept, only
float32 prediction vectors persist across trials.

Maintainer command (real DB, ~15 SQL builds; expect roughly 5-20 minutes,
mostly DuckDB window passes; run it yourself, not under a watchdog)::

    uv run python -m nba.eval.time_decay_tune --db nba.duckdb
"""

from __future__ import annotations

import argparse
import itertools
from dataclasses import asdict, dataclass, field

import duckdb
import numpy as np
import polars as pl

from nba.features.player_possession_features import build_player_shot_rates
from nba.features.time_decay import TimeDecayConfig
from nba.props.metrics import ConfidenceInterval, paired_score_delta_ci

MAX_TUNE_SEASON = 2024  # 2025 is the frozen holdout (docs/ACCEPTANCE_CRITERIA).
FIRST_N_GAMES = 15
LEAGUE_TEAM_FGA = 87.0
ZONE_POINTS = {"rim": 2.0, "mid": 2.0, "above3": 3.0}
REST_NONINFERIORITY_MARGIN = 0.02

HALF_LIVES = (0.75, 1.5, 3.0)
CARRYOVER_WEIGHTS = (0.0, 0.3, 0.6)
PEAK_AGES = (25.0, 27.0, 29.0)
AGE_WIDTHS = (6.0, 9.0, 15.0)

_RATE_COLS = [
    "game_id",
    "player_id",
    "shot_share_prior",
    "ft_trip_rate_prior",
    "ft_pct_prior",
    *[f"zone_mix_{z}_prior" for z in ZONE_POINTS],
    *[f"zone_fg_pct_{z}_prior" for z in ZONE_POINTS],
]


def _load_actuals(con: duckdb.DuckDBPyConnection, max_season: int) -> pl.DataFrame:
    """(game_id, player_id, season, pts, appearance_idx) for tunable seasons only."""
    rows = con.execute(
        """
        SELECT pgs.game_id, pgs.player_id, g.season,
               COALESCE(pgs.pts, 0) AS pts,
               ROW_NUMBER() OVER (
                   PARTITION BY pgs.player_id, g.season ORDER BY g.game_date, pgs.game_id
               ) AS appearance_idx
        FROM player_game_stats pgs JOIN games g USING (game_id)
        WHERE g.season <= ?
        """,
        [max_season],
    ).fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "season": pl.Int64,
        "pts": pl.Float64,
        "appearance_idx": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema, orient="row").sort(["game_id", "player_id"])


def project_points(rates: pl.DataFrame) -> pl.DataFrame:
    """(game_id, player_id, pred_pts) from shot-rate columns (pure function)."""
    zone_pts = sum(
        (pl.col(f"zone_mix_{z}_prior") * pl.col(f"zone_fg_pct_{z}_prior") * ZONE_POINTS[z])
        for z in ZONE_POINTS
    )
    ppa = zone_pts + pl.col("ft_trip_rate_prior") * pl.col("ft_pct_prior")
    return rates.select(
        "game_id",
        "player_id",
        (pl.col("shot_share_prior") * LEAGUE_TEAM_FGA * ppa).cast(pl.Float32).alias("pred_pts"),
    )


def score_config(
    con: duckdb.DuckDBPyConnection,
    actuals: pl.DataFrame,
    use_time_decay: bool,
    config: TimeDecayConfig | None = None,
) -> np.ndarray:
    """Per-row abs error vector aligned to ``actuals`` row order (float32)."""
    rates = build_player_shot_rates(con, use_time_decay=use_time_decay, time_decay_config=config)
    pred = project_points(rates.select(_RATE_COLS))
    del rates
    j = actuals.select("game_id", "player_id", "pts").join(
        pred, on=["game_id", "player_id"], how="left"
    )
    j = j.sort(["game_id", "player_id"])
    err = (j["pts"].cast(pl.Float32) - j["pred_pts"]).abs().fill_null(float("nan"))
    return err.to_numpy().astype(np.float32)


def _mean(err: np.ndarray, mask: np.ndarray) -> float:
    return float(np.nanmean(err[mask])) if mask.any() else float("nan")


def candidate_configs() -> list[TimeDecayConfig]:
    """Stage-1 grid (half_life x carryover weight)."""
    return [
        TimeDecayConfig(half_life_seasons=h, carryover_decay_weight=w)
        for h, w in itertools.product(HALF_LIVES, CARRYOVER_WEIGHTS)
    ]


def age_candidates(best: TimeDecayConfig) -> list[TimeDecayConfig]:
    out = [
        TimeDecayConfig(
            half_life_seasons=best.half_life_seasons,
            carryover_decay_weight=best.carryover_decay_weight,
            peak_age=p,
            age_curve_width=best.age_curve_width,
        )
        for p in PEAK_AGES
        if p != best.peak_age
    ]
    out += [
        TimeDecayConfig(
            half_life_seasons=best.half_life_seasons,
            carryover_decay_weight=best.carryover_decay_weight,
            peak_age=best.peak_age,
            age_curve_width=w,
        )
        for w in AGE_WIDTHS
        if w != best.age_curve_width
    ]
    return out


@dataclass
class SliceDelta:
    name: str
    n: int
    mae_off: float
    mae_decay: float
    delta: ConfidenceInterval


@dataclass
class TimeDecayTuneResult:
    best: TimeDecayConfig
    select_seasons: list[int]
    report_seasons: list[int]
    trials: list[tuple[dict[str, float], float]] = field(default_factory=list)
    report_slices: list[SliceDelta] = field(default_factory=list)
    select_slices: list[SliceDelta] = field(default_factory=list)
    keep_flag_on: bool = False

    def summary(self) -> str:
        lines = [f"best={asdict(self.best)}  keep_flag_on={self.keep_flag_on}"]
        for tag, sl in (("SELECT(optimistic)", self.select_slices), ("REPORT", self.report_slices)):
            for s in sl:
                lines.append(
                    f"[{tag}] {s.name}: n={s.n} MAE off={s.mae_off:.3f} decay={s.mae_decay:.3f} "
                    f"delta={s.delta.point:+.4f} CI[{s.delta.lo:+.4f},{s.delta.hi:+.4f}]"
                )
        return "\n".join(lines)


def slice_deltas(
    err_off: np.ndarray,
    err_on: np.ndarray,
    actuals: pl.DataFrame,
    seasons: list[int],
    n_boot: int = 1000,
    seed: int = 0,
) -> list[SliceDelta]:
    """Clustered-by-game paired deltas for first-15 / rest / all slices."""
    season = actuals["season"].to_numpy()
    idx = actuals["appearance_idx"].to_numpy()
    gid = actuals["game_id"].to_numpy()
    base = np.isin(season, seasons) & np.isfinite(err_off) & np.isfinite(err_on)
    out: list[SliceDelta] = []
    for name, m in (
        ("first_15", base & (idx <= FIRST_N_GAMES)),
        ("rest", base & (idx > FIRST_N_GAMES)),
        ("all", base),
    ):
        if m.sum() == 0:
            continue
        ci = paired_score_delta_ci(
            err_on[m].astype(float),
            err_off[m].astype(float),
            n_boot=n_boot,
            seed=seed,
            cluster_ids=gid[m],
        )
        out.append(SliceDelta(name, int(m.sum()), _mean(err_off, m), _mean(err_on, m), ci))
    return out


def decide_keep(slices: list[SliceDelta]) -> bool:
    """Apply the pre-registered rule (see module docstring) to REPORT slices."""
    by = {s.name: s for s in slices}
    if "first_15" not in by or "rest" not in by:
        return False
    f, r = by["first_15"].delta, by["rest"].delta
    return bool(f.hi < 0.0 and r.hi <= REST_NONINFERIORITY_MARGIN and r.point <= 0.0)


def run_time_decay_tune(
    con: duckdb.DuckDBPyConnection,
    max_season: int = MAX_TUNE_SEASON,
    n_boot: int = 1000,
    seed: int = 0,
    configs: list[TimeDecayConfig] | None = None,
    tune_ages: bool = True,
) -> TimeDecayTuneResult:
    """Tune on selection seasons, report on the last tunable season."""
    if max_season > MAX_TUNE_SEASON:
        raise ValueError(f"season {max_season} is the frozen holdout; max is {MAX_TUNE_SEASON}")
    actuals = _load_actuals(con, max_season)
    seasons = sorted(int(s) for s in actuals["season"].unique().to_list())
    if len(seasons) < 2:
        raise ValueError("need >= 2 tunable seasons (one to select on, one to report)")
    select_seasons, report_seasons = seasons[:-1], seasons[-1:]
    sel_mask = np.isin(actuals["season"].to_numpy(), select_seasons)

    err_off = score_config(con, actuals, use_time_decay=False)
    trials: list[tuple[dict[str, float], float]] = []
    best_cfg: TimeDecayConfig | None = None
    best_err: np.ndarray | None = None
    best_score = float("inf")

    def consider(cfg: TimeDecayConfig) -> None:
        nonlocal best_cfg, best_err, best_score
        err = score_config(con, actuals, use_time_decay=True, config=cfg)
        score = _mean(err, sel_mask & np.isfinite(err))
        trials.append((asdict(cfg), score))
        if score < best_score:
            best_cfg, best_err, best_score = cfg, err, score

    for cfg in configs if configs is not None else candidate_configs():
        consider(cfg)
    assert best_cfg is not None
    if tune_ages:
        for cfg in age_candidates(best_cfg):
            consider(cfg)
    assert best_cfg is not None and best_err is not None

    res = TimeDecayTuneResult(
        best=best_cfg, select_seasons=select_seasons, report_seasons=report_seasons, trials=trials
    )
    res.select_slices = slice_deltas(err_off, best_err, actuals, select_seasons, n_boot, seed)
    res.report_slices = slice_deltas(err_off, best_err, actuals, report_seasons, n_boot, seed)
    res.keep_flag_on = decide_keep(res.report_slices)
    return res


def main() -> None:
    from nba.db.connect import connect

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--max-season", type=int, default=MAX_TUNE_SEASON)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    con = connect(args.db, read_only=True)
    print(f"seed={args.seed}")
    print(run_time_decay_tune(con, args.max_season, args.n_boot, args.seed).summary())


if __name__ == "__main__":
    main()
