# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,unused-ignore,index,assignment,call-overload,misc"
"""TEAM_BUDGET evaluation (docs/prereg/TEAM_BUDGET.md, FROZEN sha256 e43ca3c3; this module implements the rule, changes nothing).

    M="-m research.eval.team_budget_eval"
    .venv/bin/python $M gate      # G1-G7 re-run, planted checks 2-4, missingness audit -> reports/prereg_team_budget/gate_frozen.json
    .venv/bin/python $M arms      # A0/T1/T2/T2nc/placebos/oracles/gamma0.5 -> results.json (REFUSES unless frozen + gate ok)
    .venv/bin/python $M attack    # shuffled-label T2, drop-one coach columns (only if a T2 cell passes) -> attack.json
    .venv/bin/python $M report    # reports/prereg_team_budget.md from the JSON
    .venv/bin/python $M run       # gate -> arms -> attack -> report under ONE heavy.lock (pid inside, removed only by its owner)

Research module only; ``nba/`` untouched; ``nba.duckdb`` read-only; ``season <= 2024`` asserted. Pure functions (no DB) are
unit-tested on synthetic data in tests/research/eval/test_team_budget_eval.py. Readings of ambiguities in the frozen text are listed
in the ``AMBIGUITIES`` constant and printed into results.json; none changes a constant of the rule.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, bh_adjust, month_blocks
from nba.props.context_residual import ContextResidualConfig, _params, to_integer_support
from research.budget import asof as A
from research.budget.constraint import OT_MINUTES, rake_groups
from research.eval.f11_lineup_context import frozen_sha256

DOC = Path("docs/prereg/TEAM_BUDGET.md")
FROZEN_FULL = "e43ca3c3289b3f05bfcb49b9447b4757a6aee2cd39e91edcbdb39ae8e8a5766f"
OUT_DIR = Path("reports/prereg_team_budget")
REPORT_MD = Path("reports/prereg_team_budget.md")
CACHE_DIR = Path("data/ops/team_budget_cache")
LOCK = Path("data/ops/heavy.lock")
LT_DIR = Path("reports/lower_tail")
PAIRS_DIR = Path("data/ops/model_clv_pairs")
STATS = ("pts", "reb", "ast", "fg3m")
SEASONS = (2023, 2024)
N_BOOT = 2000
FLOOR = -0.005
ATTACK_FLOOR = -0.003
ROLE_VS_T1 = -0.003
COACH_VS_NC = -0.002
CARRY = 0.8
COV_TOL = 0.03
BIAS_TOL = 0.05
MAX_BIAS = 0.5
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
R_LO, R_HI = 0.8, 1.25
E_FLOOR = 0.5  # minutes: floor of e_i in the ratio e~/e (players with e < 0.5 are never A0 rows in practice)
KBAR_MIN_ROWS = 500  # eligible earlier rows needed before the as-of mean replaces 1.0
CAP_MIN = 48.0
SEED = 0
DEFAULT_OT = 0.06
DEFAULT_COVER = 0.988

ROLE_COLS = [
    "e", "p", "mu", "rank_e", "e_share", "start_rate", "apps", "mean_min", "close_share", "trend",
    "gw", "n_roster", "S", "rest_days", "b2b", "abs_margin", "n_out_rot",
    "win_pct", "gb6", "gb10", "late",
]  # fmt: skip
COACH_COLS = ["h_depth", "h_conc", "h_blowout", "h_star_rest_all", "h_star_rest_matched"]
ARMS_PRIMARY = ("T1", "T2")

AMBIGUITIES = {
    "kbar_k2bar_rows": "the as-of mean of e~/e (T1) and e2/e (T2) uses played roster rows with >= 5 prior appearances in the data (a proxy of 'A0 rows', available for 2022 too) dated strictly earlier; 1.0 until KBAR_MIN_ROWS exist. T2's mean can only use test-season (out-of-fold) predictions, so it stays 1.0 longer.",
    "e_floor": "ratio denominators use max(e, 0.5) minutes",
    "abs_elo_margin": "|as-of Elo margin| comes from the stored lower-tail components (row_abs_margin) per team-game; NaN for team-games with no component row (all 2022); LightGBM takes NaN",
    "n_out_rot": "number of OUT-flagged players among those with >= 15 minutes per appearance in the previous 10 games (self-defined; production's own n_out_rot is not exported for DNP rows)",
    "oracles": "O1 = T1 with the realised roster minutes as the budget; O2 = realised minutes / max(e, 0.5) normalised by its as-of mean, both inside the [0.8, 1.25] clip",
    "placebo_P2": "every T2 feature column is permuted jointly within team-season in training and test rows; the ratio denominator e stays the real one",
    "shuffled_label": "T2 share model refit with training shares permuted within each block's training set (seed 0); the same stat-independent fit scores every stat",
    "consistency_kappa": "Dirichlet kappa by method of moments from the 2023 out-of-fold T2 shares, used for 2024 draws only; descriptive",
    "decision_p": "P(over) = share of integer quantiles >= ceil(line) of each arm's grid; A0's comparator uses the mixture grid, not the stored p_model",
}


# --------------------------------------------------------------------------- freeze + lock
def require_frozen(doc: Path = DOC, sha: str = FROZEN_FULL) -> None:
    if not sha or frozen_sha256(doc) != sha:
        raise RuntimeError(
            "TEAM_BUDGET is not frozen at the expected sha256 (doc changed or unset): no arm may be fitted"
        )


@contextmanager
def heavy_lock(
    lock: Path = LOCK, retry_s: float = 120.0, max_wait_s: float = 3600.0
) -> Iterator[None]:
    """mkdir lock with our pid inside; retry every 120 s; remove it only if the pid file is ours."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.time() - t0 > max_wait_s:
                raise TimeoutError("heavy.lock held past max_wait_s") from None
            print(f"heavy.lock held; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)
    mine = str(os.getpid())
    (lock / "pid").write_text(mine)
    try:
        yield
    finally:
        pf = lock / "pid"
        if pf.exists() and pf.read_text().strip() == mine:
            shutil.rmtree(lock, ignore_errors=True)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float))


# --------------------------------------------------------------------------- pure helpers
def asof_cum_mean(
    dates: np.ndarray, vals: np.ndarray, qdates: np.ndarray, default: float, min_n: int = 1
) -> np.ndarray:
    """For each query date: the mean of ``vals`` over rows dated STRICTLY earlier; ``default`` if fewer
    than ``min_n`` such rows. Dates are int64 day numbers."""
    order = np.argsort(dates, kind="stable")
    d, v = dates[order], vals[order]
    cs = np.concatenate([[0.0], np.cumsum(v)])
    idx = np.searchsorted(d, qdates, side="left")  # rows with date < q
    n = idx.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = cs[idx] / n
    return np.where(idx >= min_n, m, default)


def ratio_factor(
    e_new: np.ndarray, e: np.ndarray, kbar: np.ndarray, gamma: float = 1.0
) -> np.ndarray:
    """r = clip((e_new / max(e, 0.5)) / kbar, 0.8, 1.25) ** gamma."""
    r = np.clip((e_new / np.maximum(e, E_FLOOR)) / kbar, R_LO, R_HI)
    return np.asarray(r**gamma)


def scale_to_int(grid: np.ndarray, r: np.ndarray) -> np.ndarray:
    """A0 continuous quantile grid x per-row factor, then the integer grid (``ceil(q - 0.5)``)."""
    return to_integer_support(grid * r[:, None]).astype(np.int16)


def permute_within(frame: pl.DataFrame, cols: list[str], by: list[str], seed: int) -> pl.DataFrame:
    """Permute the rows of ``cols`` JOINTLY within groups ``by`` (marginals kept, link to the row broken)."""
    rng = np.random.default_rng(seed)
    new = np.arange(frame.height)
    key = frame.select(by).with_row_index("_i")
    for grp in key.partition_by(by, maintain_order=False):
        pos = grp["_i"].to_numpy()
        new[pos] = rng.permutation(pos)
    perm = frame.select(cols)[new.tolist()]
    return frame.with_columns([perm[c] for c in cols])


def p_over_from_grid(qi: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(Y > line) on lines ending .5: share of integer quantiles >= ceil(line)."""
    need = np.ceil(line)
    return np.asarray((qi >= need[:, None]).mean(axis=1))


def window_sum(tbl: pl.DataFrame, val_cols: list[str], lookback: int) -> pl.DataFrame:
    """Sum ``val_cols`` over the previous ``lookback`` team-games of each (team, player). ``tbl``: team_id, _k
    (team-game index), player_id, val_cols. Returns team_id, _t, player_id, sums (a row feeds _k+1 .. _k+lookback)."""
    return (
        tbl.with_columns(pl.int_ranges(pl.col("_k") + 1, pl.col("_k") + lookback + 1).alias("_t"))
        .explode("_t")
        .group_by("team_id", "_t", "player_id")
        .agg([pl.col(c).sum() for c in val_cols])
    )


# --------------------------------------------------------------------------- world (DB)
def load_world(db_path: str = "nba.duckdb") -> dict[str, Any]:
    import duckdb
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import flagged_from_availability

    games, pgs, _static, avail = load_inputs(db_path, None)
    if int(games["season"].max()) > MAX_SEASON:
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    con = duckdb.connect(db_path, read_only=True)
    try:
        coach = (
            con.execute(
                f"SELECT c.game_id, c.team_id, c.coach_id, c.coach_name FROM team_coach_games c JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
            )
            .pl()
            .with_columns(
                pl.coalesce(pl.col("coach_id").cast(pl.Utf8), pl.col("coach_name")).alias(
                    "coach_key"
                )
            )
        )
        reasons = con.execute(
            "SELECT game_id, player_id, reason FROM player_availability WHERE game_id IS NOT NULL"
        ).pl()
        close = con.execute(
            f"""WITH l AS (SELECT s.game_id, s.team_id, UNNEST(s.players) AS player_id FROM stints s JOIN games g USING (game_id)
                 WHERE g.season <= {MAX_SEASON} AND s.period = 4 AND s.end_clock = 0)
               SELECT DISTINCT game_id, team_id, player_id FROM l"""
        ).pl()
        sc = duckdb.connect(":memory:")
        for name, sql in {
            "games": f"SELECT * FROM games WHERE season <= {MAX_SEASON}",
            "player_game_stats": f"SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}",
        }.items():
            df = con.execute(sql).pl()
            sc.register("_src", df)
            sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            sc.unregister("_src")
    finally:
        con.close()
    box = pgs.select("game_id", "team_id", "player_id", "minutes", "starter").with_columns(
        pl.col("minutes").fill_null(0.0).cast(pl.Float64), pl.col("starter").fill_null(False)
    )
    return {
        "games": games, "box": box, "coach": coach, "flagged": flagged, "reasons": reasons,
        "close": close, "sc": sc, "cfg": cfg,
    }  # fmt: skip


def production_minutes(sc: Any) -> pl.DataFrame:
    from nba.props.minutes import build_minutes_features, predict_minutes

    mf = build_minutes_features(sc, use_game_context=False)
    d = predict_minutes(mf)
    return (
        mf.select("game_id", "player_id", "game_date")
        .with_columns(pl.Series("p", [x.p_play for x in d]), pl.Series("mu", [x.mu for x in d]))
        .with_columns(pl.col("player_id").cast(pl.Int32))
    )


def build_frame(
    world: dict[str, Any], pm: pl.DataFrame, abs_margin: pl.DataFrame | None = None
) -> dict[str, pl.DataFrame]:
    """Roster x team-game feature frame (2022-2024) and the team-game table. Everything is strictly as-of;
    tonight's box, minutes, starters, stints and score are never read for a tonight value."""
    from research.budget.status import coach_star_rest_asof, team_status
    from research.eval.team_budget_gates import star_pairs

    games, box = world["games"], world["box"].with_columns(pl.col("player_id").cast(pl.Int32))
    tgm = (
        pl.concat(
            [
                games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
                games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
            ]
        )
        .sort(["team_id", "game_date", "game_id"])
        .with_columns(pl.int_range(pl.len()).over("team_id").alias("_k"))
    )
    shape = A.team_game_shape(world["box"], games).join(
        world["coach"].select("game_id", "team_id", "coach_key"),
        on=["game_id", "team_id"],
        how="left",
    )
    status = team_status(games)
    hab = A.coach_habits_asof(
        shape.select(
            "game_id",
            "team_id",
            "game_date",
            "coach_key",
            "depth",
            "conc",
            "top5_min",
            "b2b",
            "abs_margin",
        )
    )
    pairs = star_pairs(
        world["box"],
        tgm.select("game_id", "team_id", "game_date"),
        world["reasons"],
        shape.select("game_id", "team_id", "b2b"),
    )
    pairs = pairs.join(status.select("game_id", "team_id", "status"), on=["game_id", "team_id"])
    star = coach_star_rest_asof(
        shape.select("game_id", "team_id", "game_date", "coach_key").join(
            status.select("game_id", "team_id", "status"), on=["game_id", "team_id"]
        ),
        pairs,
    )
    # roster before OUT removal -> n_out_rot, star_out; then OUT removal
    ros = A.asof_roster(world["box"], tgm.select("game_id", "team_id", "game_date")).with_columns(
        pl.col("player_id").cast(pl.Int32)
    )
    out_rows = [(g, int(p)) for g, ps in world["flagged"].items() for p in ps]
    outdf = (
        pl.DataFrame(
            out_rows, schema={"game_id": pl.Utf8, "player_id": pl.Int32}, orient="row"
        ).with_columns(pl.lit(True).alias("_out"))
        if out_rows
        else pl.DataFrame(schema={"game_id": pl.Utf8, "player_id": pl.Int32, "_out": pl.Boolean})
    )
    ros = ros.join(outdf, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("_out").fill_null(False)
    )
    ros = ros.with_columns(
        pl.col("mean_min").rank("ordinal", descending=True).over("game_id", "team_id").alias("_mr")
    )
    tout = ros.group_by("game_id", "team_id").agg(
        ((pl.col("mean_min") >= 15) & pl.col("_out")).sum().alias("n_out_rot"),
        ((pl.col("_mr") <= 2) & pl.col("_out")).any().alias("star_out"),
    )
    ros = ros.filter(~pl.col("_out")).drop("_out", "_mr")
    # production e: tonight's row if present, else the latest earlier row, else p=0.5, mu=mean_min
    r = ros.join(
        pm.select("game_id", "player_id", "p", "mu"), on=["game_id", "player_id"], how="left"
    )
    prev = pm.sort(["player_id", "game_date", "game_id"]).select(
        "player_id", "game_date", pl.col("p").alias("p_prev"), pl.col("mu").alias("mu_prev")
    )
    r = r.join(tgm.select("game_id", "team_id", "game_date", "_k"), on=["game_id", "team_id"]).sort(
        "game_date"
    )
    r = r.join_asof(
        prev.sort("game_date"),
        on="game_date",
        by="player_id",
        strategy="backward",
        allow_exact_matches=False,
    )
    r = (
        r.with_columns(
            pl.col("p").is_null().alias("imputed"),
            pl.coalesce(pl.col("p"), pl.col("p_prev"), pl.lit(0.5)).alias("p"),
            pl.coalesce(pl.col("mu"), pl.col("mu_prev"), pl.col("mean_min")).alias("mu"),
        )
        .drop("p_prev", "mu_prev")
        .with_columns((pl.col("p") * pl.col("mu")).alias("e"))
    )
    r = r.with_columns(
        pl.col("e")
        .rank("ordinal", descending=True)
        .over("game_id", "team_id")
        .cast(pl.Float64)
        .alias("rank_e"),
        pl.col("e").sum().over("game_id", "team_id").alias("S"),
        pl.len().over("game_id", "team_id").cast(pl.Float64).alias("n_roster"),
    ).with_columns((pl.col("e") / pl.col("S")).alias("e_share"))
    # window features from the box (prior team-games only)
    sh = box.join(tgm.select("game_id", "team_id", "_k"), on=["game_id", "team_id"]).with_columns(
        (pl.col("minutes") / pl.col("minutes").sum().over("game_id", "team_id"))
        .fill_nan(0.0)
        .alias("_sh"),
        pl.lit(1.0).alias("_one"),
    )
    w10 = window_sum(
        sh.select("team_id", "_k", "player_id", "_sh", "_one"), ["_sh", "_one"], 10
    ).rename({"_sh": "sh10", "_one": "gw"})
    w3 = window_sum(sh.select("team_id", "_k", "player_id", "_sh"), ["_sh"], 3).rename(
        {"_sh": "sh3"}
    )
    close_games = (
        shape.filter(pl.col("abs_margin") <= A.CLOSE)
        .select("game_id", "team_id")
        .with_columns(pl.lit(1.0).alias("_cg"))
    )
    cg = tgm.join(close_games, on=["game_id", "team_id"], how="left").with_columns(
        pl.col("_cg").fill_null(0.0)
    )
    oc = (
        world["close"]
        .with_columns(pl.col("player_id").cast(pl.Int32))
        .join(
            cg.filter(pl.col("_cg") > 0).select("game_id", "team_id", "_k"),
            on=["game_id", "team_id"],
        )
        .with_columns(pl.lit(1.0).alias("_oc"))
    )
    wc = window_sum(oc.select("team_id", "_k", "player_id", "_oc"), ["_oc"], 10)
    den = (
        cg.select("team_id", "_k", "_cg")
        .with_columns(pl.int_ranges(pl.col("_k") + 1, pl.col("_k") + 11).alias("_t"))
        .explode("_t")
        .group_by("team_id", "_t")
        .agg(pl.col("_cg").sum().alias("_den"))
    )
    r = (
        r.join(
            w10,
            left_on=["team_id", "_k", "player_id"],
            right_on=["team_id", "_t", "player_id"],
            how="left",
        )
        .join(
            w3,
            left_on=["team_id", "_k", "player_id"],
            right_on=["team_id", "_t", "player_id"],
            how="left",
        )
        .join(
            wc,
            left_on=["team_id", "_k", "player_id"],
            right_on=["team_id", "_t", "player_id"],
            how="left",
        )
        .join(den, left_on=["team_id", "_k"], right_on=["team_id", "_t"], how="left")
        .with_columns(
            pl.col("gw").fill_null(0.0),
            (
                pl.col("sh3").fill_null(0.0)
                / pl.min_horizontal(pl.col("_k"), pl.lit(3)).clip(lower_bound=1)
                - pl.col("sh10").fill_null(0.0)
                / pl.min_horizontal(pl.col("_k"), pl.lit(10)).clip(lower_bound=1)
            ).alias("trend"),
            pl.when(pl.col("_den").fill_null(0.0) > 0)
            .then(pl.col("_oc").fill_null(0.0) / pl.col("_den"))
            .otherwise(0.0)
            .alias("close_share"),
        )
        .drop("sh3", "sh10", "_oc", "_den")
    )
    tf = (
        shape.select("game_id", "team_id", "b2b", "abs_margin", "coach_key", "game_date")
        .rename({"abs_margin": "final_abs_margin"})
        .join(tout, on=["game_id", "team_id"], how="left")
        .join(
            status.select("game_id", "team_id", "status", "win_pct", "gb6", "gb10", "late"),
            on=["game_id", "team_id"],
        )
        .join(
            hab.select("game_id", "team_id", "coach_n_prior", "h_depth", "h_conc", "h_blowout"),
            on=["game_id", "team_id"],
        )
        .join(star, on=["game_id", "team_id"])
        .join(tgm.select("game_id", "team_id", "_k"), on=["game_id", "team_id"])
        .with_columns(
            pl.col("n_out_rot").fill_null(0).cast(pl.Float64),
            pl.col("star_out").fill_null(False),
            pl.col("b2b").cast(pl.Float64),
            pl.col("late").cast(pl.Float64),
        )
    )
    tf = tf.sort(["team_id", "game_date"]).with_columns(
        (pl.col("game_date") - pl.col("game_date").shift(1).over("team_id"))
        .dt.total_days()
        .fill_null(7)
        .cast(pl.Float64)
        .alias("rest_days")
    )
    if abs_margin is not None:
        tf = tf.join(abs_margin, on=["game_id", "team_id"], how="left")
    else:
        tf = tf.with_columns(pl.lit(None, dtype=pl.Float64).alias("abs_margin"))
    # realised team minutes / roster minutes (labels, never features) and OT / cover as-of
    act = box.group_by("game_id", "team_id").agg(pl.col("minutes").sum().alias("T"))
    inr = box.join(
        r.select("game_id", "team_id", "player_id").with_columns(pl.lit(True).alias("_in")),
        on=["game_id", "team_id", "player_id"],
        how="left",
    )
    cov = inr.group_by("game_id", "team_id").agg(
        (pl.col("minutes") * pl.col("_in").fill_null(False).cast(pl.Float64))
        .sum()
        .alias("m_roster")
    )
    tf = (
        tf.join(act, on=["game_id", "team_id"])
        .join(cov, on=["game_id", "team_id"])
        .with_columns(
            (pl.col("T") > 241.0).cast(pl.Float64).alias("ot"),
            (pl.col("m_roster") / pl.col("T")).alias("cover"),
        )
        .join(games.select("game_id", "season"), on="game_id")
    )
    d_all = tf["game_date"].dt.epoch("d").to_numpy().astype(np.int64)
    ot_rate = asof_cum_mean(d_all, tf["ot"].to_numpy(), d_all, DEFAULT_OT)
    full = (
        tf["_k"] >= 10
    ).to_numpy()  # early-season rosters are partial by construction: not in the cover mean
    cover_asof = asof_cum_mean(d_all[full], tf["cover"].to_numpy()[full], d_all, DEFAULT_COVER)
    tf = tf.with_columns(
        pl.Series("ot_rate_asof", ot_rate), pl.Series("cover_asof", cover_asof)
    ).with_columns((240.0 + OT_MINUTES * pl.col("ot_rate_asof")).alias("B"))
    tf = tf.with_columns((pl.col("B") * pl.col("cover_asof")).alias("B_roster"))
    r = r.join(
        tf.select(
            "game_id",
            "team_id",
            "season",
            "rest_days",
            "b2b",
            "abs_margin",
            "n_out_rot",
            "win_pct",
            "gb6",
            "gb10",
            "late",
            "B_roster",
            "m_roster",
            *COACH_COLS,
            "coach_n_prior",
            "status",
            "star_out",
            "ot",
            "final_abs_margin",
        ),
        on=["game_id", "team_id"],
    )
    r = r.join(
        box.select("game_id", "team_id", "player_id", "minutes", "starter"),
        on=["game_id", "team_id", "player_id"],
        how="left",
    ).with_columns(pl.col("minutes").fill_null(0.0))
    r = r.with_columns((pl.col("minutes") / pl.col("m_roster")).alias("share")).filter(
        pl.col("m_roster") > 0
    )
    prior_played = (
        box.sort(["player_id", "game_id"])
        .with_columns((pl.col("minutes") > 0).cast(pl.Float64).alias("_pl"))
        .with_columns(
            (pl.col("_pl").cum_sum().over("player_id") - pl.col("_pl")).alias("prior_played")
        )
        .select("game_id", "player_id", "prior_played")
    )
    r = r.join(prior_played, on=["game_id", "player_id"], how="left").with_columns(
        ((pl.col("minutes") > 0) & (pl.col("prior_played") >= 5)).alias("elig")
    )
    return {"roster": r.sort(["game_id", "team_id", "player_id"]), "tf": tf}


# --------------------------------------------------------------------------- T1 / oracles
def group_ids(r: pl.DataFrame, budget_col: str = "B_roster") -> tuple[np.ndarray, np.ndarray]:
    """Integer id per roster row (first-appearance order of (game, team)) and the budget per id."""
    g = (
        r.group_by("game_id", "team_id", maintain_order=True)
        .agg(pl.col(budget_col).first())
        .with_row_index("_g")
    )
    ids = r.select("game_id", "team_id").join(
        g.select("game_id", "team_id", "_g"), on=["game_id", "team_id"], how="left"
    )["_g"]
    return ids.to_numpy().astype(np.int64), g[budget_col].to_numpy()


def kbar_for(
    r: pl.DataFrame, num: np.ndarray, den: np.ndarray, rows_mask: np.ndarray | None = None
) -> np.ndarray:
    """As-of mean of num/max(den,E_FLOOR) over eligible rows of strictly earlier dates (per roster row)."""
    d = r["game_date"].dt.epoch("d").to_numpy().astype(np.int64)
    use = r["elig"].to_numpy() & (np.ones(len(d), bool) if rows_mask is None else rows_mask)
    ratio = num / np.maximum(den, E_FLOOR)
    return asof_cum_mean(d[use], ratio[use], d, 1.0, KBAR_MIN_ROWS)


def t1_values(r: pl.DataFrame, budget_col: str = "B_roster") -> dict[str, np.ndarray]:
    """T1 (or O1 with budget_col='m_roster'): raked expected minutes and the normalised factor."""
    e = r["e"].to_numpy()
    gids, b = group_ids(r, budget_col)
    et = rake_groups(e, gids, b, CAP_MIN)
    kbar = kbar_for(r, et, e)
    return {"e_new": et, "kbar": kbar, "r": ratio_factor(et, e, kbar)}


def o2_values(r: pl.DataFrame) -> dict[str, np.ndarray]:
    e, m = r["e"].to_numpy(), r["minutes"].to_numpy()
    kbar = kbar_for(r, m, e)
    return {"e_new": m, "kbar": kbar, "r": ratio_factor(m, e, kbar)}


# --------------------------------------------------------------------------- T2 share model
def share_walk_forward(
    r: pl.DataFrame,
    cols: list[str],
    cfg: ContextResidualConfig,
    shuffle: bool = False,
    seed: int = SEED,
    seasons: tuple[int, ...] = SEASONS,
) -> np.ndarray:
    """Out-of-fold predicted roster-minute share for every roster row of the test seasons (NaN elsewhere): walk-forward
    month blocks, trained strictly before each block on all earlier rows (2022 warm-up included)."""
    import lightgbm as lgb

    if max(seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    out = np.full(r.height, np.nan)
    test = r.with_row_index("_i").filter(pl.col("season").is_in(list(seasons)))
    rng = np.random.default_rng(seed)
    for start, end in month_blocks(test):
        blk = test.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = r.filter(pl.col("game_date") < start)
        if blk.is_empty() or train.height < 1500:
            continue
        y = train["share"].to_numpy()
        if shuffle:
            y = rng.permutation(y)
        x = train.select(cols).cast(pl.Float64).to_numpy()
        t0 = time.time()
        m = lgb.LGBMRegressor(**_params(cfg)).fit(x, y)
        out[blk["_i"].to_numpy()] = m.predict(blk.select(cols).cast(pl.Float64).to_numpy())
        _log(
            f"  share block {start} done ({time.time() - t0:.0f}s, n={blk.height}, cols={len(cols)})"
        )
    return out


def t2_values(r: pl.DataFrame, s_hat: np.ndarray) -> dict[str, np.ndarray]:
    """Rake the share forecasts (>= 0) to the roster budget with the 48-minute cap, then the normalised factor.
    Rows without a forecast (warm-up season) carry NaN and r = 1."""
    ok = ~np.isnan(s_hat)
    e = r["e"].to_numpy()
    base = np.where(ok, np.clip(s_hat, 0.0, None), 0.0)
    gids, b = group_ids(r)
    e2 = rake_groups(base, gids, b, CAP_MIN)
    kbar = kbar_for(r, e2, e, rows_mask=ok)
    rr = np.where(ok, ratio_factor(e2, e, kbar), 1.0)
    return {"e_new": e2, "kbar": kbar, "r": rr}


# --------------------------------------------------------------------------- scoring + pass rule
def row_factor(comp_keys: pl.DataFrame, r: pl.DataFrame, rvals: np.ndarray) -> np.ndarray:
    """Per A0 row factor: the roster row's r where the player is rostered for the team he played for, else 1
    (outside the budget). Joined on (game, team, player): a traded player can sit on two as-of rosters."""
    t = r.select("game_id", "team_id", "player_id").with_columns(pl.Series("_r", rvals))
    j = comp_keys.select("game_id", "team_id", "player_id").join(
        t, on=["game_id", "team_id", "player_id"], how="left"
    )
    assert j.height == comp_keys.height
    return j["_r"].fill_null(1.0).to_numpy()


def evaluate(
    res: dict[str, Any], tests_ok: bool, audit_ok: bool, attack: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The frozen pass rule per primary cell (T1/T2 x stat). ``res['rows']`` carry the paired statistics."""
    tab = {(x["stat"], x["season"], x["arm"]): x for x in res["rows"]}
    sl = res["slices"]
    out: dict[str, Any] = {}
    for stat in STATS:
        for arm in ARMS_PRIMARY:
            a23, a24, z24 = tab[(stat, 2023, arm)], tab[(stat, 2024, arm)], tab[(stat, 2024, "A0")]
            sel = bool(a23["dcrps"] <= FLOOR and a23["ci_hi"] < 0.0)
            r1 = bool(a24["dcrps"] <= FLOOR and a24["ci_hi"] < 0.0 and a24["p_bh"] <= 0.05)
            sls = {
                s["slice"]: s
                for s in sl
                if s["stat"] == stat and s["season"] == 2024 and s["arm"] == arm
            }
            worse = {
                k: v["dcrps"]
                for k, v in sls.items()
                if v["n"] >= MIN_SLICE_N
                and v.get("dcrps") is not None
                and v["dcrps"] > SLICE_WORSE
                and k not in ("overtime",)
            }
            cold_new = all(
                sls[k]["ci_lo"] <= 0.0
                for k in ("cold5_9", "coach_new")
                if k in sls and sls[k].get("ci_lo") is not None
            )
            guards = {
                "cov80": bool(abs(a24["cov80"] - 0.8) <= abs(z24["cov80"] - 0.8) + COV_TOL),
                "bias": bool(
                    abs(a24["bias"]) <= abs(z24["bias"]) + BIAS_TOL and abs(a24["bias"]) <= MAX_BIAS
                ),
                "no_slice_worse": not worse,
                "cold_and_coach_new_ci": bool(cold_new),
            }
            pl_arm = "P1" if arm == "T1" else "P2"
            beats_p = bool(a24[f"minus_{pl_arm}"] <= ATTACK_FLOOR)
            not_beat_oracle = bool(a24["dcrps"] >= tab[(stat, 2024, "O2")]["dcrps"])
            cell: dict[str, Any] = {
                "select_2023": sel, "rule1_2024": r1, "guards": guards, "slices_worse": worse,
                "beats_placebo_by_0.003": beats_p, "tests_ok": bool(tests_ok), "audit_ok": bool(audit_ok),
                "not_beating_oracle": not_beat_oracle,
            }  # fmt: skip
            if arm == "T2":
                cell["role_claim_T2_minus_T1_le_-0.003"] = bool(a24["minus_T1"] <= ROLE_VS_T1)
                cell["coach_claim_T2_minus_T2nc_le_-0.002"] = bool(a24["minus_T2nc"] <= COACH_VS_NC)
            cell["pass_before_attack"] = bool(
                sel
                and r1
                and all(guards.values())
                and beats_p
                and tests_ok
                and audit_ok
                and not_beat_oracle
            )
            out[f"{arm}/{stat}"] = cell
    if attack is not None:
        for key, cell in out.items():
            arm, stat = key.split("/")
            if arm == "T2" and cell["pass_before_attack"]:
                a = attack.get("T2", {}).get(stat, {})
                cell["shuffled_ok"] = bool(a.get("shuffled_ok", False))
                cell["one_coach_column_carries"] = bool(a.get("one_coach_column_carries", True))
                cell["coach_claim"] = bool(
                    cell["coach_claim_T2_minus_T2nc_le_-0.002"]
                    and not cell["one_coach_column_carries"]
                )
                cell["PASS"] = bool(cell["shuffled_ok"])
            else:
                cell["PASS"] = bool(cell["pass_before_attack"] and arm == "T1")
    return {"cells": out, "close": not any(v["select_2023"] for v in out.values())}


def carries(gain_full: float, gain_drop: float, gain_only: float) -> bool:
    """Dropping the column leaves <= 20% of the full gain, or the column alone keeps >= 80% of it (gains = dCRPS)."""
    if gain_full >= 0:
        return False
    return bool(gain_drop / gain_full <= 1.0 - CARRY or gain_only / gain_full >= CARRY)


# --------------------------------------------------------------------------- per-stat world and scoring
def abs_margin_table(box: pl.DataFrame) -> pl.DataFrame:
    """(game_id, team_id) -> as-of |Elo margin| from the stored pts components (one value per team-game)."""
    comp = np.load(LT_DIR / "components_pts.npz", allow_pickle=True)
    d = pl.DataFrame(
        {
            "game_id": comp["row_gid"].astype(str),
            "player_id": comp["row_pid"].astype(np.int32),
            "abs_margin": comp["row_abs_margin"],
        }
    ).join(
        box.select("game_id", "player_id", "team_id").with_columns(
            pl.col("player_id").cast(pl.Int32)
        ),
        on=["game_id", "player_id"],
        how="left",
    )
    return d.group_by("game_id", "team_id").agg(pl.col("abs_margin").first())


class StatWorld:
    """A0 rows of one stat: keys, labels, the continuous mixture grid and slice metadata."""

    def __init__(
        self, stat: str, r: pl.DataFrame, tf: pl.DataFrame, box: pl.DataFrame, games: pl.DataFrame
    ) -> None:
        from nba.eval.lower_tail_eval import arm_grid, load_components

        pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
        comp = load_components(stat, LT_DIR)
        if not (
            np.array_equal(comp["row_gid"], pic["row_gid"])
            and np.array_equal(comp["row_pid"], pic["row_pid"])
        ):
            raise RuntimeError("pi components not row-aligned")
        if int(comp["row_season"].max()) > MAX_SEASON:
            raise RuntimeError("season > 2024 in the lower-tail components")
        self.stat = stat
        self.grid = arm_grid("mixture", comp, pic, stat)
        self.y = comp["row_y"].astype(float)
        keys = pl.DataFrame(
            {"game_id": comp["row_gid"].astype(str), "player_id": comp["row_pid"].astype(np.int32)}
        )
        self.keys = keys
        m = keys.with_columns(
            pl.Series("season", comp["row_season"].astype(np.int64)),
            pl.Series("starter10", comp["row_starter10"]),
            pl.Series("n_prior", comp["row_n_prior"]),
            pl.Series("row_b2b", comp["row_b2b"]),
        ).join(
            box.select("game_id", "player_id", "team_id").with_columns(
                pl.col("player_id").cast(pl.Int32)
            ),
            on=["game_id", "player_id"],
            how="left",
        )
        m = m.join(
            tf.select(
                "game_id", "team_id", "_k", "coach_n_prior", "status", "late", "star_out", "ot"
            ),
            on=["game_id", "team_id"],
            how="left",
        )
        self.meta = m
        self.keys = m.select("game_id", "team_id", "player_id")
        self.gid = m["game_id"].to_numpy()

    def masks(self, r_arm: np.ndarray | None = None) -> dict[str, np.ndarray]:
        m = self.meta
        out = {
            "all": np.ones(m.height, bool),
            "starter": (m["starter10"] >= 0.5).to_numpy(),
            "bench": (m["starter10"] < 0.5).to_numpy(),
            "star_out": m["star_out"].fill_null(False).to_numpy(),
            "overtime": (m["ot"].fill_null(0.0) > 0).to_numpy(),
            "cold5_9": ((m["n_prior"] >= 5) & (m["n_prior"] < 10)).to_numpy(),
            "b2b": (m["row_b2b"] > 0.5).to_numpy(),
            "first15": (m["games_prior"].fill_null(99) < 15).to_numpy(),
            "coach_new": (m["coach_n_prior"].fill_null(0.0) < 20).to_numpy(),
            "late": (m["late"].fill_null(0.0) > 0).to_numpy(),
        }
        for s in ("contending", "middle", "out"):
            out[f"status:{s}"] = (m["status"] == s).fill_null(False).to_numpy()
        if r_arm is not None:
            for t, mk in (
                _F().tercile_masks(m.with_columns(pl.Series("r_arm", r_arm)), "r_arm").items()
            ):
                out[f"r:{t}"] = mk
        return out


def _F() -> Any:
    from research.eval import four_factors_eval as F

    return F


def score_stat(
    sw: StatWorld, qis: dict[str, np.ndarray], rs: dict[str, np.ndarray]
) -> dict[str, Any]:
    """Rows (per season x arm) with CRPS, deltas vs A0 (clustered CI/p/MDE), paired contrasts and slices."""
    F = _F()
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for season in SEASONS:
        sm = (sw.meta["season"] == season).to_numpy()
        gid = sw.gid[sm]
        sc = {a: F.row_scores(q[sm], sw.y[sm], sw.stat) for a, q in qis.items()}
        base = sc["A0"]["crps"]
        for a, s in sc.items():
            row: dict[str, Any] = {
                "stat": sw.stat, "season": season, "arm": a, "n_rows": int(sm.sum()), "n_games": int(len(np.unique(gid))),
                "crps_int": float(s["crps"].mean()), "bias": float(s["resid"].mean()),
                "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()), "pit_q10": float((s["pit"] <= 0.10).mean()), "tll": float(s["tll"].mean()),
            }  # fmt: skip
            if a != "A0":
                row.update(F._delta_stats(s["crps"] - base, gid))
                for other in ("P1", "P2", "T1", "T2nc", "O2"):
                    if other in sc and other != a:
                        row[f"minus_{other}"] = float((s["crps"] - sc[other]["crps"]).mean())
            rows.append(row)
        for a in ARMS_PRIMARY:
            masks = sw.masks(rs[a])
            sub = {k: v[sm] for k, v in masks.items()}
            d = sc[a]["crps"] - base
            for name, mk in sub.items():
                rec: dict[str, Any] = {
                    "stat": sw.stat,
                    "season": season,
                    "arm": a,
                    "slice": name,
                    "n": int(mk.sum()),
                }
                if mk.sum() >= MIN_SLICE_N or name == "all":
                    ds = F._delta_stats(d[mk], gid[mk])
                    rec.update({k: ds[k] for k in ("dcrps", "ci_lo", "ci_hi")})
                slices.append(rec)
    return {"rows": rows, "slices": slices}


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the 8 primary cells ({T1, T2} x 4 stats), separately per season."""
    for season in SEASONS:
        tgt = [x for x in rows if x["season"] == season and x["arm"] in ARMS_PRIMARY]
        adj = bh_adjust(np.array([x["p"] for x in tgt]))
        for x, a in zip(tgt, adj, strict=True):
            x["p_bh"] = float(a)


def legs_with_p(legs: dict[str, Any], p: np.ndarray) -> dict[str, Any]:
    """Replace the stored model probability by an arm's P(over) and recompute side, EV, outcome columns."""
    from nba.markets import decision_scoring as DS

    is_over, ev, p_side, dec = DS.choose_side(p, legs["price_over"], legs["price_under"])
    out = dict(legs)
    out.update(
        p_model=p, is_over=is_over, ev=ev, p_side=p_side, dec=dec,
        profit=DS.profit_per_unit(is_over, legs["went_over"], dec),
        hit=(is_over == (legs["went_over"] > 0.5)).astype(np.float64),
        p_mkt_side=np.where(is_over, legs["mult"], 1.0 - legs["mult"]),
    )  # fmt: skip
    return out


def decision_p(
    legs: dict[str, Any],
    worlds: dict[str, StatWorld],
    qis: dict[str, dict[str, np.ndarray]],
    arm: str,
) -> np.ndarray:
    """P(over) per leg from the arm's integer grid; legs not on an A0 row get NaN (none expected)."""
    p = np.full(len(legs["line"]), np.nan)
    for stat, sw in worlds.items():
        idx = sw.keys.with_row_index("_i").with_columns(pl.col("player_id").cast(pl.Int64))
        m = np.flatnonzero(legs["stat"] == stat)
        lk = pl.DataFrame(
            {
                "game_id": legs["game_id"][m],
                "player_id": legs["player_id"][m].astype(np.int64),
                "_p": np.arange(len(m)),
            }
        )
        j = lk.join(idx, on=["game_id", "player_id"], how="left")
        ok = j["_i"].is_not_null().to_numpy()
        ii = j["_i"].fill_null(0).to_numpy()
        pp = p_over_from_grid(qis[stat][arm][ii], legs["line"][m])
        p[m[ok]] = pp[ok]
    return p


# --------------------------------------------------------------------------- stages
def _cfg() -> ContextResidualConfig:
    import yaml

    return ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )


def _cached_shat(tag: str, r: pl.DataFrame, make: Any) -> np.ndarray:
    p = CACHE_DIR / f"share_{tag}.npy"
    if p.exists():
        a = np.load(p)
        if len(a) == r.height:
            return np.asarray(a)
    out = np.asarray(make())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(p, out)
    return out


def build_all() -> dict[str, Any]:
    world = load_world()
    _log("world loaded; production minutes ...")
    pm = production_minutes(world["sc"])
    am = abs_margin_table(world["box"])
    fr = build_frame(world, pm, am)
    fr["world"], fr["pm"], fr["am"] = world, pm, am
    return fr


def consistency(
    r: pl.DataFrame, e2: np.ndarray, s_hat: np.ndarray, t1_e: np.ndarray
) -> dict[str, Any]:
    """Secondary: |sum e - B| for production vs the raked arms; Dirichlet joint draws within-team correlation vs realised."""
    gids, b = group_ids(r)
    e = r["e"].to_numpy()
    g = int(gids.max()) + 1
    s_prod = np.bincount(gids, weights=e, minlength=g)
    s_t1 = np.bincount(gids, weights=t1_e, minlength=g)
    ok = ~np.isnan(s_hat)
    s_t2 = np.bincount(gids, weights=np.where(ok, e2, 0.0), minlength=g)
    has2 = np.bincount(gids, weights=ok.astype(float), minlength=g) > 0
    out: dict[str, Any] = {
        "prod_abs_gap_mean": float(np.abs(s_prod - b).mean()),
        "T1_abs_gap_max": float(np.abs(s_t1 - b).max()),
        "T2_abs_gap_max": float(np.abs(s_t2 - b)[has2].max()) if has2.any() else None,
    }
    sh = r["share"].to_numpy()
    season = r["season"].to_numpy()
    m = np.clip(np.nan_to_num(s_hat, nan=0.0), 0.0, None)
    tr = (season == 2023) & ok & (m > 0.02)
    var = float(np.mean((sh[tr] - m[tr]) ** 2))
    kappa = float(np.mean(m[tr] * (1 - m[tr])) / var - 1.0) if tr.any() and var > 0 else 60.0
    out["kappa_from_2023"] = kappa
    z = (sh - m) / np.sqrt(np.maximum(m * (1 - m), 1e-9) / (kappa + 1.0))
    rng = np.random.default_rng(SEED)
    real, drawn = [], []
    te = np.flatnonzero((season == 2024) & ok)
    team_games = np.unique(gids[te])
    for gi in rng.permutation(team_games)[:300]:
        idx = np.flatnonzero((gids == gi) & ok & (m > 0))
        if len(idx) < 4:
            continue
        zz = z[idx]
        real.append((zz.sum() ** 2 - (zz**2).sum()) / (len(zz) * (len(zz) - 1)))
        d = rng.dirichlet(kappa * m[idx] / m[idx].sum() + 1e-9, size=400)
        c = np.corrcoef(d.T)
        drawn.append((c.sum() - len(idx)) / (len(idx) * (len(idx) - 1)))
    out["pairwise_corr_realised_z"] = float(np.mean(real)) if real else None
    out["pairwise_corr_dirichlet_draws"] = float(np.mean(drawn)) if drawn else None
    out["draw_sum_exact"] = bool(np.allclose(rng.dirichlet(np.ones(5)).sum(), 1.0))
    return out


def run_gate(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """G1-G7 re-run on the frozen data plus the planted checks 2-4. Light (no fit)."""
    from research.eval import team_budget_gates as G

    require_frozen(DOC, FROZEN_FULL)
    res: dict[str, Any] = {"frozen_sha256": frozen_sha256(DOC)}
    con = G.connect()
    try:
        res["sql"] = G.gate_sql(con)
        res["coach"] = G.gate_coach(con)
        res["reliability"] = G.gate_reliability(con)
    finally:
        con.close()
    res["a0"] = G.gate_a0()
    res["roster"] = G.gate_roster()
    res["planted"] = planted_checks()
    res["gate_ok"] = bool(
        res["sql"]["pass"]
        and res["coach"]["pass"]
        and res["a0"]["pass"]
        and res["roster"]["pass"]
        and res["planted"]["ok"]
        and all(
            res["reliability"][k]["keep"]
            for k in ("depth", "conc", "blowout_pull_career", "star_rest_all_status")
        )
    )
    _write_json(out_dir / "gate_frozen.json", res)
    _log(f"gate_ok={res['gate_ok']}")
    return res


def planted_checks(n_games: int = 24) -> dict[str, Any]:
    """Checks 2-4 on the real frame: edit target games' box, starters, stints (close table) and score; every feature of the
    target games is identical; the NEXT game of the same team moves (non-inert control); a late OUT is ignored; rake sums exact."""
    import duckdb

    from nba.props.context_residual import flagged_from_availability

    base = build_all()
    world = base["world"]
    games = world["games"]
    g24 = games.filter(pl.col("season") == 2024).sort("game_date")["game_id"].to_list()
    tg = g24[100 :: max(1, len(g24) // n_games)][:n_games]
    box2 = world["box"].with_columns(
        pl.when(pl.col("game_id").is_in(tg))
        .then(pl.col("minutes").reverse().over("game_id", "team_id"))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(pl.col("game_id").is_in(tg))
        .then(~pl.col("starter"))
        .otherwise(pl.col("starter"))
        .alias("starter"),
    )
    games2 = games.with_columns(
        pl.when(pl.col("game_id").is_in(tg))
        .then(pl.col("home_pts") + 30)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    close2 = world["close"].filter(~pl.col("game_id").is_in(tg))
    sc2 = duckdb.connect(":memory:")
    for name, df in {
        "games": games2,
        "player_game_stats": base["world"]["sc"]
        .execute("SELECT * FROM player_game_stats")
        .pl()
        .join(
            box2.select(
                "game_id",
                "team_id",
                "player_id",
                pl.col("minutes").alias("_m"),
                pl.col("starter").alias("_s"),
            ),
            on=["game_id", "team_id", "player_id"],
            how="left",
        )
        .with_columns(
            pl.coalesce(pl.col("_m"), pl.col("minutes")).alias("minutes"),
            pl.coalesce(pl.col("_s"), pl.col("starter")).alias("starter"),
        )
        .drop("_m", "_s"),
    }.items():
        sc2.register("_src", df)
        sc2.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
        sc2.unregister("_src")
    w2 = dict(world, games=games2, box=box2, close=close2, sc=sc2)
    new = build_frame(w2, production_minutes(sc2), base["am"])
    cols = ["game_id", "team_id", "player_id", *ROLE_COLS, *COACH_COLS]
    # career-pooled habits and 10-game windows legitimately carry an EARLIER planted game into LATER target games,
    # so the identity test covers every game dated up to and including the EARLIEST planted game
    first_date = games.filter(pl.col("game_id").is_in(tg))["game_date"].min()
    upto = games.filter(pl.col("game_date") <= first_date)["game_id"].to_list()
    a = base["roster"].select(cols).filter(pl.col("game_id").is_in(upto)).sort(cols[:3])
    b = new["roster"].select(cols).filter(pl.col("game_id").is_in(upto)).sort(cols[:3])
    diff_cols = [
        c
        for c in cols[3:]
        if a.shape != b.shape
        or not np.array_equal(
            np.nan_to_num(a[c].to_numpy().astype(float), nan=-9e9),
            np.nan_to_num(b[c].to_numpy().astype(float), nan=-9e9),
        )
    ]
    same = a.shape == b.shape and all(
        np.array_equal(
            np.nan_to_num(a[c].to_numpy().astype(float), nan=-9e9),
            np.nan_to_num(b[c].to_numpy().astype(float), nan=-9e9),
        )
        for c in cols[3:]
    )
    nxt = base["roster"].select(cols).filter(~pl.col("game_id").is_in(upto)).sort(cols[:3])
    nxt2 = new["roster"].select(cols).filter(~pl.col("game_id").is_in(upto)).sort(cols[:3])
    control_moves = not (
        nxt.shape == nxt2.shape
        and all(
            np.allclose(
                np.nan_to_num(nxt[c].to_numpy().astype(float)),
                np.nan_to_num(nxt2[c].to_numpy().astype(float)),
            )
            for c in cols[3:]
        )
    )
    # late OUT after the real tip must not enter the flagged set
    from nba.features.game_tipoff import build_game_tipoff

    tips = build_game_tipoff().filter(pl.col("game_id").is_in(tg))
    row = base["roster"].filter(pl.col("game_id") == tg[0]).head(1)
    late = pl.DataFrame(
        {
            "game_id": [tg[0]],
            "player_id": [int(row["player_id"][0])],
            "status": ["out"],
            "as_of": [
                tips.filter(pl.col("game_id") == tg[0])["tipoff_utc"][0]
                + __import__("datetime").timedelta(hours=1)
            ],
            "source": ["official"],
        }
    )
    cfg = world["cfg"]
    import duckdb as _d

    con = _d.connect("nba.duckdb", read_only=True)
    try:
        av = con.execute(
            "SELECT game_id, player_id, status, as_of, source FROM player_availability WHERE game_id IS NOT NULL"
        ).pl()
    finally:
        con.close()
    av2 = pl.concat(
        [
            av,
            late.with_columns(
                pl.col("as_of").cast(av.schema["as_of"]),
                pl.col("player_id").cast(av.schema["player_id"]),
            ),
        ],
        how="diagonal_relaxed",
    )
    f0, _ = flagged_from_availability(av, games, cfg.report)
    f1, _ = flagged_from_availability(av2, games, cfg.report)
    late_ok = f0.get(tg[0], set()) == f1.get(tg[0], set())
    t1 = t1_values(base["roster"])
    gids, b_ = group_ids(base["roster"])
    exact = float(np.abs(np.bincount(gids, weights=t1["e_new"]) - b_).max())
    out = {
        "n_target_games": len(tg), "n_games_compared": len(upto), "differing_columns": diff_cols, "shapes": [list(a.shape), list(b.shape)], "same_game_identical": bool(same), "control_next_games_move": bool(control_moves),
        "late_out_ignored": bool(late_ok), "rake_max_abs_err": exact, "rake_exact": bool(exact < 1e-9),
    }  # fmt: skip
    out["ok"] = bool(same and control_moves and late_ok and out["rake_exact"])
    return out


def run_arms(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    require_frozen(DOC, FROZEN_FULL)
    gate = json.loads((out_dir / "gate_frozen.json").read_text())
    if not gate.get("gate_ok") or gate.get("frozen_sha256") != FROZEN_FULL:
        raise RuntimeError("the frozen gate has not passed: no arm may be fitted")
    fr = build_all()
    r, tf, world = fr["roster"], fr["tf"], fr["world"]
    cfg = _cfg()
    _log(f"frame: {r.height} roster rows, {tf.height} team-games")
    all_cols = ROLE_COLS + COACH_COLS
    s_t2 = _cached_shat("T2", r, lambda: share_walk_forward(r, all_cols, cfg))
    s_nc = _cached_shat("T2nc", r, lambda: share_walk_forward(r, ROLE_COLS, cfg))
    rp = permute_within(r, all_cols, ["team_id", "season"], SEED)
    s_p2 = _cached_shat("P2", r, lambda: share_walk_forward(rp, all_cols, cfg))
    v = {"T1": t1_values(r), "O1": t1_values(r, "m_roster"), "O2": o2_values(r)}
    v["T2"], v["T2nc"], v["P2"] = t2_values(r, s_t2), t2_values(r, s_nc), t2_values(r, s_p2)
    p1 = permute_within(
        r.with_columns(pl.Series("_r", v["T1"]["r"])), ["_r"], ["team_id", "season"], SEED
    )["_r"].to_numpy()
    rvals = {a: x["r"] for a, x in v.items()}
    rvals["P1"] = p1
    box = world["box"]
    worlds = {s: StatWorld(s, r, tf, box, world["games"]) for s in STATS}
    res: dict[str, Any] = {"rows": [], "slices": [], "ambiguities": AMBIGUITIES}
    qis: dict[str, dict[str, np.ndarray]] = {}
    t0 = time.time()
    for stat, sw in worlds.items():
        fac = {a: row_factor(sw.keys, r, x) for a, x in rvals.items()}
        fac["T1g5"], fac["T2g5"] = fac["T1"] ** 0.5, fac["T2"] ** 0.5
        arms = {"A0": scale_to_int(sw.grid, np.ones(sw.grid.shape[0]))}
        arms.update({a: scale_to_int(sw.grid, f) for a, f in fac.items()})
        qis[stat] = arms
        sc = score_stat(sw, arms, {a: fac[a] for a in ARMS_PRIMARY})
        res["rows"] += sc["rows"]
        res["slices"] += sc["slices"]
        _log(f"[{time.time() - t0:.0f}s] {stat} scored")
    add_bh(res["rows"])
    res["verdict"] = evaluate(
        res,
        bool(gate["planted"]["ok"]),
        bool(gate["roster"]["missingness_audit"]["pass_null_asymmetry_le_2pp"]),
    )
    res["consistency"] = consistency(r, v["T2"]["e_new"], s_t2, v["T1"]["e_new"])
    try:
        from nba.markets import decision_scoring as DS

        legs = DS.load_legs(PAIRS_DIR)
        res["decision_score"] = {}
        for arm in ("A0", "T1", "T2"):
            p = decision_p(legs, worlds, qis, arm)
            keep = ~np.isnan(p)
            lg = {
                k: (v_[keep] if isinstance(v_, np.ndarray) and v_.shape[:1] == keep.shape else v_)
                for k, v_ in legs.items()
            }
            gm = {g: i for i, g in enumerate(np.unique(lg["game_id"]))}
            lg["gi"], lg["G"] = np.array([gm[g] for g in lg["game_id"]]), len(gm)
            out = DS.run_all(legs_with_p(lg, p[keep]))
            res["decision_score"][arm] = {
                "n_legs": int(keep.sum()),
                "cells": [
                    {
                        "season": c["season"],
                        "stat": c["stat"],
                        "by_threshold": [
                            {"threshold": t["threshold"], "model": t["model"]}
                            for t in c["by_threshold"]
                        ],
                    }
                    for c in out["cells"]
                ],
            }
    except Exception as exc:  # secondary, cannot rescue or fail the rule
        res["decision_score"] = {"error": repr(exc)}
    _write_json(out_dir / "results.json", res)
    return res


def _gain2024(sw: StatWorld, r: pl.DataFrame, rv: np.ndarray, base_qi: np.ndarray) -> float:
    F = _F()
    sm = (sw.meta["season"] == 2024).to_numpy()
    q = scale_to_int(sw.grid, row_factor(sw.keys, r, rv))
    a = F.row_scores(base_qi[sm], sw.y[sm], sw.stat)["crps"]
    b = F.row_scores(q[sm], sw.y[sm], sw.stat)["crps"]
    return float((b - a).mean())


def run_attack(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    require_frozen(DOC, FROZEN_FULL)
    res = json.loads((out_dir / "results.json").read_text())
    fr = build_all()
    r, tf, world = fr["roster"], fr["tf"], fr["world"]
    cfg = _cfg()
    all_cols = ROLE_COLS + COACH_COLS
    s_sh = _cached_shat("SH", r, lambda: share_walk_forward(r, all_cols, cfg, shuffle=True))
    rsh = t2_values(r, s_sh)["r"]
    s_t2 = _cached_shat("T2", r, lambda: share_walk_forward(r, all_cols, cfg))
    rt2 = t2_values(r, s_t2)["r"]
    cells = res["verdict"]["cells"]
    passing = [
        k.split("/")[1] for k, v in cells.items() if k.startswith("T2/") and v["pass_before_attack"]
    ]
    out: dict[str, Any] = {"T2": {}}
    for stat in STATS:
        sw = StatWorld(stat, r, tf, world["box"], world["games"])
        a0 = scale_to_int(sw.grid, np.ones(sw.grid.shape[0]))
        mde = [
            x for x in res["rows"] if x["stat"] == stat and x["season"] == 2024 and x["arm"] == "T2"
        ][0]["mde"]
        g_sh = _gain2024(sw, r, rsh, a0)
        ent: dict[str, Any] = {"gain_shuffled": g_sh, "mde": mde, "shuffled_ok": bool(g_sh >= -mde)}
        if stat in passing:
            g_full = _gain2024(sw, r, rt2, a0)
            ko = {}
            for col in COACH_COLS:
                cols = [c for c in all_cols if c != col]
                s_d = _cached_shat(
                    f"KO_{col}", r, lambda cols=cols: share_walk_forward(r, cols, cfg)
                )
                gd = _gain2024(sw, r, t2_values(r, s_d)["r"], a0)
                ko[col] = {"gain_drop": gd, "carries": carries(g_full, gd, g_full)}
            ent.update(
                {
                    "gain_full": g_full,
                    "knockout": ko,
                    "one_coach_column_carries": any(v["carries"] for v in ko.values()),
                }
            )
        out["T2"][stat] = ent
    res["verdict"] = evaluate(
        res,
        bool(json.loads((out_dir / "gate_frozen.json").read_text())["planted"]["ok"]),
        True,
        out,
    )
    _write_json(out_dir / "attack.json", out)
    _write_json(out_dir / "results.json", res)
    return out


def render_report(out_dir: Path = OUT_DIR) -> str:
    res = json.loads((out_dir / "results.json").read_text())
    ln = [
        "# TEAM_BUDGET results", "", f"Frozen sha256 {FROZEN_FULL[:8]} (docs/prereg/TEAM_BUDGET.md). Season 2025 never loaded.", "",
        "| stat | season | arm | n | CRPS | dCRPS [CI] | p_bh | MDE | cov80 | bias |", "|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for x in res["rows"]:
        if x["arm"] == "A0":
            ln.append(
                f"| {x['stat']} | {x['season']} | A0 | {x['n_rows']} | {x['crps_int']:.6f} | | | | {x['cov80']:.3f} | {x['bias']:+.3f} |"
            )
        else:
            ln.append(
                f"| {x['stat']} | {x['season']} | {x['arm']} | {x['n_rows']} | {x['crps_int']:.6f} | {x['dcrps']:+.4f} [{x['ci_lo']:+.4f}, {x['ci_hi']:+.4f}] | {x.get('p_bh', float('nan')):.3f} | {x['mde']:.4f} | {x['cov80']:.3f} | {x['bias']:+.3f} |"
            )
    ln += [
        "",
        "## Verdict",
        "",
        "```json",
        json.dumps(res["verdict"], indent=1),
        "```",
        "",
        "## Team consistency (secondary)",
        "",
        "```json",
        json.dumps(res.get("consistency"), indent=1),
        "```",
        "",
    ]
    ln += ["## Readings of ambiguities in the frozen text", ""] + [
        f"* {k}: {v}" for k, v in res["ambiguities"].items()
    ]
    return "\n".join(ln)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["gate", "arms", "attack", "report", "run"])
    a = ap.parse_args(argv)
    if a.cmd == "report":
        REPORT_MD.write_text(render_report())
        print(REPORT_MD)
        return 0
    require_frozen(DOC, FROZEN_FULL)
    with heavy_lock():
        if a.cmd in ("gate", "run"):
            ok = run_gate()["gate_ok"]
            if not ok:
                return 1
        if a.cmd in ("arms", "run"):
            run_arms()
        if a.cmd in ("attack", "run"):
            run_attack()
        if a.cmd == "run":
            REPORT_MD.write_text(render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
