"""Walk-forward bake-off of win-probability model families vs production injury-Elo.

PRE-REGISTERED DECISION RULE (written before the first real-data run)
--------------------------------------------------------------------
Baseline: ``rung0_injury_elo`` out-of-fold probabilities
(``data/injury_elo/oof_predictions.parquet``; seasons 2022-2024; season 2025 is the burned
holdout and is never loaded here). Scored universe = those OOF games (n ~ 3951).

Candidates = (arm x drift variant x calibrator). Arms: logit_offset, logit_nooffset,
gbm_noff, gbm_feat, gbm_init, poisson_dc, poisson_dc_offset, mlp_offset, stack (+ the
production injury_elo itself). Drift variants: ``raw`` (absolute team rates) and ``rel``
(ORtg/DRtg/pace/3PA rate/FTA rate minus the season's as-of league mean). Calibrators:
none, platt, platt_slope, platt_int, beta, iso (isotonic on equal-mass bins blended
with Platt). All hyper-parameters are fixed in code before the run (nothing is tuned on
the scored games); calibrator window / half-life / strength are chosen by an inner
walk-forward on the two latest PRIOR months only.

Walk-forward: month blocks; every model for a block is fit on games dated strictly
before the block (arms need >= 500 prior games, else the prediction is the injury-Elo
probability). Calibrators for a block are fit on the arm's earlier out-of-fold
predictions only (needs >= 400 prior rows, else identity).

R1 (REPLACE production injury-Elo): a candidate replaces production iff ALL hold
  1. paired log-loss delta (candidate - production) has a game-clustered 95% bootstrap CI
     entirely below 0 AND its two-sided bootstrap p-value passes Benjamini-Hochberg at
     q = 0.05 across ALL candidates (one family);
  2. Brier delta point estimate <= 0;
  3. equal-mass-bin ECE delta <= +0.01;
  4. no slice (rotation players OUT = 0 / 1 / 2+, early season = either team in its first
     15 games, playoffs) is worse by more than 0.005 log loss (slices with n < 100 cannot
     veto).
R2 (KEEP a calibrator, judged against UNCALIBRATED production): kept iff log loss
  improves with the game-clustered CI entirely below 0, OR ECE improves by >= 0.005 with
  the log-loss delta CI upper bound <= +0.001.
Everything else is a REJECT and is reported as such; most arms are expected to tie or
lose (Elo-family models are hard to beat on ~4k games).

Additional pre-registered arm definitions (fixed before the full run)
---------------------------------------------------------------------
* ``_sw`` arms (logit_offset, logit_nooffset, gbm_noff, gbm_feat, gbm_init, mlp_offset):
  each training game is duplicated with teams swapped (paired features exchanged,
  home-positive differentials and the injury-Elo logit negated, label 1 - y, ``home_flag``
  0); prediction uses the original orientation only (``home_flag`` 1). Compared with the
  un-augmented twin.
* ``glm_margin`` (every arm's feature): ridge ``margin ~ home + team_home - team_away`` refit
  each calendar month on games strictly before the month, weights ``0.5**(age/120d)`` (also
  the cross-season carryover), ridge precision 11. Not tuned. Own arms: ``glm_only`` (margin
  -> win prob through a fitted logistic scale) and ``glm_offset`` (injury-Elo offset plus the
  GLM margin only: does GLM add beyond injury-Elo?).
* ``mlp_offset_brier``: the MLP trained with Brier loss instead of BCE.
* Leak detector: static allow-list of model inputs (``assert_allowed_features``) plus a
  top-10 gain check on every LightGBM fit (``check_importance_leak``); a violation raises
  ``LeakError`` and aborts the run. LightGBM replaces XGBoost (not installed).
* Candidate count is large (~190 rows); BH is applied across ALL of them, so the bar is
  deliberately high. Expect REJECT for nearly everything.
* The MLP learning rate / epochs (1e-3 / 15) were reduced from (3e-3 / 40) after a smoke run
  showed gross overfitting; that smoke run scored the same OOF games, so the MLP rows carry
  a small selection caveat. No other hyper-parameter was changed after seeing results.

Maintainer command::

    uv run python -m nba.eval.winprob_family_eval --db nba.duckdb \\
        --oof data/injury_elo/oof_predictions.parquet --out-dir data/winprob_family
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.injury_elo_eval import feature_config_from, game_numbers, load_config
from nba.features.game_location_context import (
    build_context_features,
    build_schedule_table,
    load_team_markets,
)
from nba.models.base import RungModel
from nba.models.injury_elo import load_games_frame, load_stats_frame
from nba.models.winprob_family import (
    BASE_FEATURES,
    CAL_METHODS,
    CAL_PARAMS,
    CAL_WINDOWS,
    FAMILY_SEED,
    MAX_SEASON,
    GbmArm,
    MlpArm,
    OffsetLogistic,
    PoissonScoreArm,
    StackArm,
    StackSpec,
    apply_drift_variant,
    calibration_weights,
    crps_discrete,
    crps_normal,
    ece_equal_mass,
    efficiency_features,
    fit_calibrator,
    form_features,
    glm_strength_features,
    schedule_features,
    standings_features,
    status_features,
    team_box_totals,
)
from nba.parlay.game_model import asof_total_features

MIN_TRAIN = 500
MIN_CAL = 400
SLICE_VETO = 0.005
SLICE_MIN_N = 100
Q_BH = 0.05
LOGIT_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    pp = np.clip(p, LOGIT_EPS, 1 - LOGIT_EPS)
    return np.asarray(np.log(pp / (1 - pp)), dtype=float)


def per_game_ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    pp = np.clip(p, 1e-9, 1 - 1e-9)
    return np.asarray(-(y * np.log(pp) + (1 - y) * np.log(1 - pp)), dtype=float)


def bh_reject(pvals: list[float], q: float) -> list[bool]:
    """Benjamini-Hochberg step-up; flags in input order."""
    m = len(pvals)
    order = np.argsort(pvals)
    k = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            k = rank
    rej = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= k:
            rej[i] = True
    return rej


# --------------------------------------------------------------------------
# Frame construction (real DB)
# --------------------------------------------------------------------------


def drift_table(games: pl.DataFrame, team_box: pl.DataFrame) -> list[dict[str, Any]]:
    """Per-season league environment: pace, 3PA rate, FTA rate, home win rate, mean total."""
    tb = team_box.join(games.select(["game_id", "season"]), on="game_id")
    out = []
    for season in sorted(games["season"].unique().to_list()):
        g = games.filter(pl.col("season") == season)
        t = tb.filter(pl.col("season") == season)
        poss = t["fga"] + 0.44 * t["fta"] + t["tov"] - t["oreb"]
        pace = (poss * 48.0 / (t["minutes"] / 5.0)).mean()
        out.append(
            {
                "season": int(season),
                "n_games": g.height,
                "pace": float(pace),  # type: ignore[arg-type]
                "tpar": float(t["fg3a"].sum()) / float(t["fga"].sum()),
                "ftr": float(t["fta"].sum()) / float(t["fga"].sum()),
                "home_win_rate": float((g["home_pts"] > g["away_pts"]).mean()),  # type: ignore[arg-type]
                "mean_total": float((g["home_pts"] + g["away_pts"]).mean()),  # type: ignore[arg-type]
            }
        )
    return out


def build_family_frame(
    con: duckdb.DuckDBPyConnection,
    oof_path: str | Path,
    injury_cfg_path: str | Path = "configs/injury_elo.yaml",
    raw_dir: str | Path = "data/schedule",
    markets_path: str | Path = "configs/team_markets.yaml",
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """Scored universe with all as-of features (raw drift variant) and the drift table."""
    oof = pl.read_parquet(oof_path).filter(pl.col("model") == "rung0_injury_elo")
    assert set(oof["season"].unique().to_list()) <= {2022, 2023, 2024}, "holdout season present"
    games = load_games_frame(con, MAX_SEASON)
    assert int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    stats = load_stats_frame(con, MAX_SEASON)
    box = con.execute(
        "SELECT s.game_id, s.team_id, s.minutes, s.fga, s.fg3a, s.fta, s.tov, s.oreb "
        "FROM player_game_stats_played s JOIN games g ON g.game_id = s.game_id "
        "WHERE g.season <= ?",
        [MAX_SEASON],
    ).pl()
    team_box = team_box_totals(box)
    icfg = feature_config_from(load_config(injury_cfg_path))
    inj = status_features(con, games, stats, icfg)
    p_map = {str(g): float(p) for g, p in oof.select(["game_id", "p"]).iter_rows()}
    ctx = build_context_features(
        build_schedule_table(str(raw_dir)), load_team_markets(markets_path)
    )
    tv = con.execute(
        "SELECT game_id, (national_tv IS NOT NULL AND national_tv != '')::INT AS is_national_tv "
        "FROM games WHERE season <= ?",
        [MAX_SEASON],
    ).pl()
    gn = game_numbers(games)
    df = (
        games.join(oof.select(["game_id", "p"]).rename({"p": "p0"}), on="game_id", how="inner")
        .join(inj, on="game_id", how="left")
        .join(schedule_features(games), on="game_id", how="left")
        .join(standings_features(games), on="game_id", how="left")
        .join(efficiency_features(games, team_box), on="game_id", how="left")
        .join(form_features(games, p_map), on="game_id", how="left")
        .join(glm_strength_features(games), on="game_id", how="left")
        .join(
            ctx.select(
                [
                    "game_id",
                    pl.col("tz_shift_east_hr").alias("tz_shift"),
                    "altitude_diff_km",
                    pl.col("home_altitude_km"),
                    "days_into_season",
                ]
            ),
            on="game_id",
            how="left",
        )
        .join(tv, on="game_id", how="left")
        .with_columns(
            pl.col("game_id").str.starts_with("004").cast(pl.Float64).alias("is_playoff"),
            pl.lit(1.0).alias("home_flag"),
            (pl.col("days_into_season") / 100.0),
            pl.col("game_id")
            .map_elements(lambda g: gn[g] <= 15, return_dtype=pl.Boolean)
            .alias("early"),
            pl.col("p0").map_batches(lambda s: pl.Series(_logit(s.to_numpy()))).alias("offset"),
        )
        .sort(["game_date", "game_id"])
    )
    assert df.height == oof.height, "OOF games missing from the games table"
    feats = df.select(BASE_FEATURES)
    n_null = {c: int(feats[c].is_null().sum()) for c in BASE_FEATURES if feats[c].is_null().any()}
    df = df.with_columns([pl.col(c).fill_null(0.0) for c in BASE_FEATURES])
    df = df.with_columns(pl.col("y").cast(pl.Float64))
    total = asof_total_features(games, 20.0, 8.0)
    df = df.join(total, on="game_id", how="left").sort(["game_date", "game_id"])
    print(f"[frame] n={df.height} null-filled features: {n_null}", file=sys.stderr)
    return df, drift_table(games, team_box)


# --------------------------------------------------------------------------
# Walk-forward machinery
# --------------------------------------------------------------------------


def month_blocks(dates: list[Any]) -> list[np.ndarray]:
    keys = [(d.year, d.month) for d in dates]
    return [np.array([i for i, k in enumerate(keys) if k == blk]) for blk in dict.fromkeys(keys)]


def run_arm_wf(
    df: pl.DataFrame, make_arm: Callable[[], RungModel], min_train: int = MIN_TRAIN
) -> dict[str, Any]:
    """Month-block walk-forward of one arm. ``df`` must be date-sorted with a ``y`` column."""
    n = df.height
    y = df["y"].to_numpy()
    p = df["p0"].to_numpy().astype(float).copy()
    fitted = np.zeros(n, dtype=bool)
    lam = np.full((n, 4), np.nan)  # l1, l2, k, lam3 (Poisson arms)
    cfg: dict[str, object] = {}
    for idx in month_blocks(df["game_date"].to_list()):
        start = int(idx[0])
        if start < min_train:
            continue
        arm = make_arm()
        arm.fit(df[:start], y[:start])
        test = df[start : int(idx[-1]) + 1]
        p[idx] = arm.predict(test)
        fitted[idx] = True
        if isinstance(arm, PoissonScoreArm):
            l1, l2 = arm.lambdas(test)
            lam[idx, 0], lam[idx, 1], lam[idx, 2], lam[idx, 3] = l1, l2, arm.k_, arm.lam3_
        cfg = arm.get_config()
    return {"p": p, "fitted": fitted, "lam": lam, "config": cfg}


def calibrate_wf(
    p: np.ndarray, y: np.ndarray, dates: list[Any], method: str
) -> tuple[np.ndarray, dict[str, int]]:
    """Nested walk-forward calibration of ``p`` (see module docstring)."""
    out = p.copy()
    blocks = month_blocks(dates)
    chosen: dict[str, int] = {}
    grid = [(w, hl, prm) for (w, hl) in CAL_WINDOWS for prm in CAL_PARAMS[method]]

    def fit_cfg(rows_end: int, start_date: Any, cfg: tuple[Any, ...]) -> Any:
        w, hl, prm = cfg
        keep, wt = calibration_weights(dates[:rows_end], start_date, w, hl)
        if keep.sum() < 300:
            return None
        return fit_calibrator(method, p[:rows_end][keep], y[:rows_end][keep], wt[keep], prm)

    for bi, idx in enumerate(blocks):
        start = int(idx[0])
        if start < MIN_CAL:
            continue
        inner = [b for b in blocks[max(0, bi - 2) : bi]]
        best: tuple[Any, ...] | None = None
        best_ll = np.inf
        for cfg in grid:
            tot, cnt = 0.0, 0
            for ib in inner:
                cal = fit_cfg(int(ib[0]), dates[int(ib[0])], cfg)
                if cal is None:
                    continue
                tot += float(per_game_ll(y[ib], cal(p[ib])).sum())
                cnt += len(ib)
            if cnt and tot / cnt < best_ll:
                best_ll, best = tot / cnt, cfg
        if best is None:
            continue
        cal = fit_cfg(start, dates[start], best)
        if cal is None:
            continue
        out[idx] = cal(p[idx])
        key = f"{best[0]}|{best[1]}|{best[2]}"
        chosen[key] = chosen.get(key, 0) + 1
    return out, chosen


def arm_makers(teams: list[int]) -> dict[str, Callable[[], RungModel]]:
    return {
        "glm_only": lambda: OffsetLogistic(False, 1.0, cols=["glm_margin"], name="glm_only"),
        "glm_offset": lambda: OffsetLogistic(True, 1.0, cols=["glm_margin"], name="glm_offset"),
        "logit_offset": lambda: OffsetLogistic(True, 200.0),
        "logit_nooffset": lambda: OffsetLogistic(False, 20.0),
        "gbm_noff": lambda: GbmArm("noff"),
        "gbm_feat": lambda: GbmArm("feat"),
        "gbm_init": lambda: GbmArm("init"),
        "poisson_dc": lambda: PoissonScoreArm(False, teams),
        "poisson_dc_offset": lambda: PoissonScoreArm(True, teams),
        "mlp_offset": lambda: MlpArm(),
        "mlp_offset_brier": lambda: MlpArm(loss="brier"),
        "logit_offset_sw": lambda: OffsetLogistic(True, 200.0, swap=True, name="logit_offset_sw"),
        "logit_nooffset_sw": lambda: OffsetLogistic(
            False, 20.0, swap=True, name="logit_nooffset_sw"
        ),
        "gbm_noff_sw": lambda: GbmArm("noff", swap=True),
        "gbm_feat_sw": lambda: GbmArm("feat", swap=True),
        "gbm_init_sw": lambda: GbmArm("init", swap=True),
        "mlp_offset_sw": lambda: MlpArm(swap=True),
    }


#: Arms that use no drift-affected feature: run once (variant "raw") only.
DRIFT_FREE_ARMS = ("glm_only", "glm_offset")


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


class Booter:
    """Shared game-level resampling indices (each game is its own cluster)."""

    def __init__(self, n: int, n_boot: int, seed: int = FAMILY_SEED) -> None:
        rng = np.random.default_rng(seed)
        self.idx = rng.integers(0, n, size=(n_boot, n), dtype=np.int32)

    def delta(self, d: np.ndarray) -> dict[str, float]:
        means = d[self.idx].mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        b = len(means)
        p_le = (np.sum(means <= 0) + 1) / (b + 1)
        p_ge = (np.sum(means >= 0) + 1) / (b + 1)
        return {
            "delta": float(d.mean()),
            "lo": float(lo),
            "hi": float(hi),
            "p": float(min(1.0, 2 * min(p_le, p_ge))),
        }


def reliability(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    order = np.argsort(p, kind="stable")
    return [
        {"n": int(len(c)), "mean_p": float(p[c].mean()), "mean_y": float(y[c].mean())}
        for c in np.array_split(order, n_bins)
    ]


def slice_masks(df: pl.DataFrame) -> dict[str, np.ndarray]:
    nrot = df["n_rot_out"].to_numpy()
    return {
        "rot_out=0": nrot == 0,
        "rot_out=1": nrot == 1,
        "rot_out=2+": nrot >= 2,
        "early(<=15g)": df["early"].to_numpy(),
        "playoffs": df["is_playoff"].to_numpy() > 0.5,
    }


def evaluate_rows(
    df: pl.DataFrame, rows: dict[str, dict[str, Any]], n_boot: int
) -> dict[str, dict[str, Any]]:
    """Score every candidate vs production; apply R1 (BH over all) and R2."""
    y = df["y"].to_numpy()
    base = df["p0"].to_numpy()
    base_ll = per_game_ll(y, base)
    base_brier = float(np.mean((base - y) ** 2))
    base_ece = ece_equal_mass(y, base)
    masks = slice_masks(df)
    boot = Booter(df.height, n_boot)
    res: dict[str, dict[str, Any]] = {}
    for name, info in rows.items():
        p = info["p"]
        ll = per_game_ll(y, p)
        d = boot.delta(ll - base_ll)
        entry: dict[str, Any] = {
            "arm": info["arm"],
            "variant": info["variant"],
            "calibrator": info["calibrator"],
            "log_loss": float(ll.mean()),
            "brier": float(np.mean((p - y) ** 2)),
            "ece": ece_equal_mass(y, p),
            "vs_prod": d,
            "brier_delta": float(np.mean((p - y) ** 2)) - base_brier,
            "ece_delta": ece_equal_mass(y, p) - base_ece,
            "slices": {
                k: {
                    "n": int(m.sum()),
                    "ll_delta": float((ll[m] - base_ll[m]).mean()) if m.sum() else 0.0,
                }
                for k, m in masks.items()
            },
        }
        if info.get("raw_name"):
            raw_ll = per_game_ll(y, rows[info["raw_name"]]["p"])
            entry["vs_own_raw"] = boot.delta(ll - raw_ll)
        res[name] = entry
    names = [k for k, v in res.items() if v["arm"] != "injury_elo" or v["calibrator"] != "none"]
    rej = bh_reject([res[k]["vs_prod"]["p"] for k in names], Q_BH)
    for k, r in zip(names, rej, strict=True):
        v = res[k]
        vetoes = [
            s
            for s, sv in v["slices"].items()
            if sv["n"] >= SLICE_MIN_N and sv["ll_delta"] > SLICE_VETO
        ]
        r1 = bool(
            r
            and v["vs_prod"]["hi"] < 0
            and v["brier_delta"] <= 0
            and v["ece_delta"] <= 0.01
            and not vetoes
        )
        v["R1"] = {"bh_reject": bool(r), "slice_vetoes": vetoes, "replace": r1}
        if v["calibrator"] != "none":
            keep = v["vs_prod"]["hi"] < 0 or (
                v["ece_delta"] <= -0.005 and v["vs_prod"]["hi"] <= 0.001
            )
            v["R2"] = {"keep_calibrator": bool(keep)}
    return res


# --------------------------------------------------------------------------
# Margin / total distributions (Poisson arm vs the parlay game model)
# --------------------------------------------------------------------------


def _ols(x: np.ndarray, t: np.ndarray) -> tuple[float, float, float]:
    s, b = np.polyfit(x, t, 1)
    sd = float(np.std(t - (b + s * x), ddof=2))
    return float(b), float(s), sd


def game_model_wf(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """Walk-forward refit of nba.parlay.game_model's linear margin and total heads."""
    n = df.height
    margin = (df["home_pts"] - df["away_pts"]).to_numpy().astype(float)
    total = (df["home_pts"] + df["away_pts"]).to_numpy().astype(float)
    off = df["offset"].to_numpy()
    tf = df["total_feat"].to_numpy().astype(float)
    mu_m, sd_m, mu_t, sd_t = (np.full(n, np.nan) for _ in range(4))
    for idx in month_blocks(df["game_date"].to_list()):
        s = int(idx[0])
        if s < MIN_TRAIN:
            continue
        b, sl, sd = _ols(off[:s], margin[:s])
        mu_m[idx], sd_m[idx] = b + sl * off[idx], sd
        ok = ~np.isnan(tf[:s])
        if ok.sum() >= MIN_TRAIN:
            a, c, sdt = _ols(tf[:s][ok], total[:s][ok])
            mu_t[idx], sd_t[idx] = a + c * np.nan_to_num(tf[idx], nan=np.nanmean(tf[:s])), sdt
    return {"mu_m": mu_m, "sd_m": sd_m, "mu_t": mu_t, "sd_t": sd_t}


def crps_compare(
    df: pl.DataFrame, lam: np.ndarray, gm: dict[str, np.ndarray], arm: PoissonScoreArm, n_boot: int
) -> dict[str, Any]:
    """Paired CRPS of the bivariate-Poisson margin/total vs the Normal game model."""
    margin = (df["home_pts"] - df["away_pts"]).to_numpy().astype(float)
    total = (df["home_pts"] + df["away_pts"]).to_numpy().astype(float)
    ok = ~np.isnan(lam[:, 0]) & ~np.isnan(gm["mu_m"]) & ~np.isnan(gm["mu_t"])
    rows = np.where(ok)[0]
    cm_p, ct_p = np.zeros(len(rows)), np.zeros(len(rows))
    for j, i in enumerate(rows):
        arm.k_, arm.lam3_ = float(lam[i, 2]), float(lam[i, 3])
        (xm, pm), (xt, pt) = arm.margin_total_pmfs(float(lam[i, 0]), float(lam[i, 1]))
        cm_p[j] = crps_discrete(xm, pm, margin[i])
        ct_p[j] = crps_discrete(xt, pt, total[i])
    cm_g = crps_normal(gm["mu_m"][rows], float(np.nanmean(gm["sd_m"][rows])), margin[rows])
    ct_g = crps_normal(gm["mu_t"][rows], float(np.nanmean(gm["sd_t"][rows])), total[rows])
    boot = Booter(len(rows), min(n_boot, 1000))
    return {
        "n": int(len(rows)),
        "margin_crps_poisson": float(cm_p.mean()),
        "margin_crps_game_model": float(cm_g.mean()),
        "margin_delta": boot.delta(cm_p - cm_g),
        "total_crps_poisson": float(ct_p.mean()),
        "total_crps_game_model": float(ct_g.mean()),
        "total_delta": boot.delta(ct_p - ct_g),
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_family_eval(
    df: pl.DataFrame,
    drift: list[dict[str, Any]] | None = None,
    arms: list[str] | None = None,
    calibrate: bool = True,
    n_boot: int = 2000,
    with_crps: bool = True,
) -> dict[str, Any]:
    """Run every arm x variant (x calibrator) on the date-sorted scored frame ``df``."""
    assert int(df["season"].max()) <= MAX_SEASON, "holdout season present"  # type: ignore[arg-type]
    y = df["y"].to_numpy()
    dates = df["game_date"].to_list()
    teams = sorted(set(df["home_team"].to_list()) | set(df["away_team"].to_list()))
    makers = arm_makers(teams)
    if arms is not None:
        makers = {k: v for k, v in makers.items() if k in arms}
    rows: dict[str, dict[str, Any]] = {}
    configs: dict[str, Any] = {}
    cal_choices: dict[str, Any] = {}
    poisson_runs: dict[str, tuple[np.ndarray, PoissonScoreArm]] = {}

    def add(arm: str, variant: str, p: np.ndarray) -> None:
        nm = f"{arm}|{variant}"
        rows[nm] = {"arm": arm, "variant": variant, "calibrator": "none", "p": p}
        if calibrate:
            for meth in CAL_METHODS:
                pc, chosen = calibrate_wf(p, y, dates, meth)
                cn = f"{nm}|{meth}"
                rows[cn] = {
                    "arm": arm,
                    "variant": variant,
                    "calibrator": meth,
                    "p": pc,
                    "raw_name": nm,
                }
                cal_choices[cn] = chosen

    add("injury_elo", "-", df["p0"].to_numpy().astype(float))
    for variant in ("raw", "rel"):
        dv = apply_drift_variant(df, variant == "rel")
        wf: dict[str, dict[str, Any]] = {}
        for name, mk in makers.items():
            if variant == "rel" and name in DRIFT_FREE_ARMS:
                continue
            wf[name] = run_arm_wf(dv, mk)
            configs[f"{name}|{variant}"] = wf[name]["config"]
            add(name, variant, wf[name]["p"])
            if name.startswith("poisson"):
                poisson_runs[f"{name}|{variant}"] = (wf[name]["lam"], mk())  # type: ignore[assignment]
        members = StackSpec().members
        if all(m in wf for m in members):
            ds = dv
            for m in members:
                ds = ds.with_columns(pl.Series(f"p_{m}", wf[m]["p"]))
            fit_ok = np.logical_and.reduce([wf[m]["fitted"] for m in members])
            p_st = df["p0"].to_numpy().astype(float).copy()
            for idx in month_blocks(dates):
                s = int(idx[0])
                tr = np.where(fit_ok[:s])[0]
                if len(tr) < StackSpec().min_rows or not fit_ok[idx].any():
                    continue
                st = StackArm()
                st.fit(ds[tr.tolist()], y[tr])
                p_st[idx] = st.predict(ds[idx.tolist()])
                configs[f"stack|{variant}"] = st.get_config()
            add("stack", variant, p_st)
    scored = evaluate_rows(df, rows, n_boot)
    out: dict[str, Any] = {
        "n": df.height,
        "seasons": sorted(set(df["season"].to_list())),
        "base": {
            "log_loss": float(per_game_ll(y, df["p0"].to_numpy()).mean()),
            "brier": float(np.mean((df["p0"].to_numpy() - y) ** 2)),
            "ece": ece_equal_mass(y, df["p0"].to_numpy()),
        },
        "rows": scored,
        "configs": configs,
        "calibrator_choices": cal_choices,
        "reliability": {
            k: reliability(y, rows[k]["p"]) for k in rows if k.startswith("injury_elo")
        },
        "drift_table": drift or [],
    }
    if with_crps and poisson_runs:
        gm = game_model_wf(df)
        out["crps"] = {
            k: crps_compare(df, lam, gm, arm, n_boot) for k, (lam, arm) in poisson_runs.items()
        }
    out["_oof"] = {k: v["p"] for k, v in rows.items()}
    return out


def format_leaderboard(res: dict[str, Any], top: int = 25) -> str:
    rows = sorted(res["rows"].items(), key=lambda kv: kv[1]["log_loss"])
    base = res["base"]
    lines = [
        f"n={res['n']}  production injury_elo: ll={base['log_loss']:.4f} "
        f"brier={base['brier']:.4f} ece={base['ece']:.4f}",
        "| candidate | ll | dLL vs prod [95% CI] | brier d | ece d | R1 replace | R2 keep cal |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in rows[:top]:
        d = v["vs_prod"]
        lines.append(
            f"| {name} | {v['log_loss']:.4f} | {d['delta']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] "
            f"| {v['brier_delta']:+.4f} | {v['ece_delta']:+.4f} "
            f"| {v.get('R1', {}).get('replace', '-')} "
            f"| {v.get('R2', {}).get('keep_calibrator', '-')} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Win-probability model-family bake-off")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--oof", default="data/injury_elo/oof_predictions.parquet")
    ap.add_argument("--out-dir", default="data/winprob_family")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--no-calibration", action="store_true")
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    df, drift = build_family_frame(con, args.oof)
    res = run_family_eval(df, drift, calibrate=not args.no_calibration, n_boot=args.n_boot)
    oof = res.pop("_oof")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
    pl.DataFrame(
        {
            "game_id": df["game_id"],
            "game_date": df["game_date"],
            "y": df["y"],
            **{k: v for k, v in oof.items()},
        }
    ).write_parquet(out / "oof_all.parquet")
    print(format_leaderboard(res))
    print(json.dumps({"crps": res.get("crps"), "drift": res["drift_table"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
