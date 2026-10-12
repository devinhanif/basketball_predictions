# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""First-half and second-half player points: build from play-by-play, forecast as-of, score (descriptive; seasons 2022-2024 only).

    .venv/bin/python -W ignore -m research.eval.half_points build   # pbp -> half table + reconciliation (light)
    .venv/bin/python -W ignore -m research.eval.half_points arms    # forecasts (a) split, (b) direct, naive (needs feature_significance pts 'full' arm cached)
    .venv/bin/python -W ignore -m research.eval.half_points score   # tables + results.json

Definitions (fixed before any score was looked at)
* 1H = periods 1-2, 2H = periods 3-4, OT = periods >= 5. Player points per event: Made Shot -> ``shot_value``; Free Throw whose
  description does not start with "MISS" -> 1. Reconciled against the box ``pts`` for every played row.
* Production full-game points forecast = the walk-forward 'full' arm of feature_significance (production config, month blocks, 2023-24),
  mapped to the integer grid like every arm here. It contains OT points; the 2H target does not (an OT-free sensitivity row is reported).
* (naive) 0.5 x the full-game quantiles, mapped to the integer grid (``ceil(x - 0.5)``).
* (a) split: T ~ the 199-point production quantile grid; H1 | T ~ Binomial(T, s1), H2 = T - H1 (so 2H uses share 1 - s1), where
  s1 = (W_h1 + K*s0) / (W_pts + K): W are half-life-10-game weighted sums over the player's PRIOR played games, s0 the as-of league 1H share of
  the player's position class (G/F/C, all games dated strictly before), K = 20 points. Not tuned.
* (b) direct: the production ContextResidualModel (same config, month blocks) fit separately per half on residual (half points - m10_pts*share),
  production's pts features plus s1, W_pts and 8 team/opponent as-of half-scoring columns (rolling 20 games, shifted).
* Over/under line = floor(median of the player's last 15 prior half scores) + 0.5; over iff P(H > line) > 0.5.
Season 2025 is never loaded; ``nba.duckdb`` read-only.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.stats import binom

from nba.eval.context_residual_eval import TEST_SEASONS, crps_from_quantiles, month_blocks
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualModel,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.metrics import _clustered_boot_means
from research.eval import feature_significance as FS

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/ops/half_points_cache"
DB = str(ROOT / "nba.duckdb")
OUT_JSON = ROOT / "reports/half_points_2026-10-12.results.json"
K_PRIOR = 20.0
HALFLIFE = 10.0
LINE_WINDOW = 15
N_BOOT = 2000
MIN_TRAIN = 1500
EXTRA_Q = ("s_half", "w_pts", "t_q_for", "t_q_ag", "o_q_for", "o_q_ag")
EXTRA = (
    "s_half",
    "w_pts",
    "t_h1_for",
    "t_h1_ag",
    "t_h2_for",
    "t_h2_ag",
    "o_h1_for",
    "o_h1_ag",
    "o_h2_for",
    "o_h2_ag",
)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- build
def build_half_table() -> dict[str, Any]:
    con = duckdb.connect(DB, read_only=True)
    try:
        ids = [
            r[0] for r in con.execute("SELECT game_id FROM games WHERE season <= 2024").fetchall()
        ]
        box = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024"
        ).pl()
        games = con.execute(
            "SELECT game_id, home_team, away_team, home_pts, away_pts FROM games WHERE season <= 2024"
        ).pl()
    finally:
        con.close()
    have = [i for i in ids if (ROOT / f"data/pbp/{i}.parquet").exists()]
    ev = (
        pl.scan_parquet([str(ROOT / f"data/pbp/{i}.parquet") for i in have])
        .select(
            "game_id",
            "period",
            "player_id",
            "team_id",
            "player_name",
            "action_type",
            "description",
            "shot_value",
        )
        .filter(pl.col("action_type").is_in(["Made Shot", "Free Throw"]))
        .collect()
        .with_columns(
            pl.when(pl.col("action_type") == "Made Shot")
            .then(pl.col("shot_value"))
            .when(~pl.col("description").str.starts_with("MISS"))
            .then(1)
            .otherwise(0)
            .alias("pts")
        )
    )
    per = ev.group_by("game_id", "player_id").agg(
        pl.col("pts").filter(pl.col("period") == 1).sum().alias("q1"),
        pl.col("pts").filter(pl.col("period") <= 2).sum().alias("h1"),
        pl.col("pts").filter(pl.col("period").is_in([3, 4])).sum().alias("h2"),
        pl.col("pts").filter(pl.col("period") >= 5).sum().alias("ot"),
        pl.col("pts").sum().alias("pbp_total"),
        pl.col("player_name").first().alias("player_name"),
    )
    played = box.filter(pl.col("minutes") > 0).join(per, on=["game_id", "player_id"], how="left")
    played = played.with_columns(
        *[pl.col(c).fill_null(0) for c in ("q1", "h1", "h2", "ot", "pbp_total")]
    )
    played = played.filter(pl.col("game_id").is_in(have))
    mism = played.filter(pl.col("pbp_total") != pl.col("pts"))
    team = ev.group_by("game_id", "team_id").agg(
        pl.col("pts").filter(pl.col("period") == 1).sum().alias("t_q1"),
        pl.col("pts").filter(pl.col("period") <= 2).sum().alias("t_h1"),
        pl.col("pts").filter(pl.col("period").is_in([3, 4])).sum().alias("t_h2"),
        pl.col("pts").filter(pl.col("period") >= 5).sum().alias("t_ot"),
        pl.col("pts").sum().alias("t_total"),
    )
    gt = pl.concat(
        [
            games.select(
                "game_id", pl.col("home_team").alias("team_id"), pl.col("home_pts").alias("g_pts")
            ),
            games.select(
                "game_id", pl.col("away_team").alias("team_id"), pl.col("away_pts").alias("g_pts")
            ),
        ]
    )
    tj = team.join(gt, on=["game_id", "team_id"], how="inner")
    rec = {
        "games_total": len(ids),
        "games_with_pbp": len(have),
        "played_rows": played.height,
        "player_mismatch_rows": mism.height,
        "player_mismatch_rate": mism.height / max(played.height, 1),
        "player_mismatch_sum_abs": int((mism["pbp_total"] - mism["pts"]).abs().sum())
        if mism.height
        else 0,
        "team_games": tj.height,
        "team_mismatch_vs_game_pts": int((tj["t_total"] != tj["g_pts"]).sum()),
        "ot_player_rows": int((played["ot"] > 0).sum()),
        "ot_games": int(team.filter(pl.col("t_ot") > 0)["game_id"].n_unique()),
        "first_half_share_of_points": float(
            played["h1"].sum() / max(played["h1"].sum() + played["h2"].sum(), 1)
        ),
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    played.write_parquet(CACHE / "half_table.parquet")
    team.write_parquet(CACHE / "team_halves.parquet")
    (CACHE / "reconcile.json").write_text(json.dumps(rec, indent=1))
    return rec


# --------------------------------------------------------------------------- as-of features
def asof_half_features(fr: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, player_id): s1 (shrunk as-of 1H share of points), w_pts, medians of prior half scores, team/opp as-of half scoring."""
    tab = pl.read_parquet(CACHE / "half_table.parquet").select(
        "game_id", pl.col("player_id").cast(pl.Int64), "q1", "h1", "h2", "ot"
    )
    base = (
        fr.select("game_id", "player_id", "team_id", "game_date", "pos_code")
        .join(tab, on=["game_id", "player_id"], how="inner")
        .sort(["player_id", "game_date", "game_id"])
    )
    n = base.height
    pid = base["player_id"].to_numpy()
    h1 = base["h1"].to_numpy().astype(float)
    q1 = base["q1"].to_numpy().astype(float)
    h2 = base["h2"].to_numpy().astype(float)
    ot = base["ot"].to_numpy().astype(float)
    tot = h1 + h2 + ot
    dec = 0.5 ** (1.0 / HALFLIFE)
    w1 = np.zeros(n)
    wt = np.zeros(n)
    med1 = np.full(n, np.nan)
    med2 = np.full(n, np.nan)
    wq = np.zeros(n)
    medq = np.full(n, np.nan)
    sw1 = swt = swq = 0.0
    histq: list[float] = []
    hist1: list[float] = []
    hist2: list[float] = []
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            sw1 = swt = swq = 0.0
            hist1, hist2, histq = [], [], []
        w1[i], wt[i], wq[i] = sw1, swt, swq
        if len(hist1) >= 5:
            med1[i] = float(np.median(hist1[-LINE_WINDOW:]))
            med2[i] = float(np.median(hist2[-LINE_WINDOW:]))
            medq[i] = float(np.median(histq[-LINE_WINDOW:]))
        sw1 = dec * sw1 + h1[i]
        swt = dec * swt + tot[i]
        swq = dec * swq + q1[i]
        histq.append(q1[i])
        hist1.append(h1[i])
        hist2.append(h2[i])
    base = base.with_columns(
        pl.Series("w1", w1),
        pl.Series("wt", wt),
        pl.Series("med1", med1),
        pl.Series("med2", med2),
        pl.Series("wq", wq),
        pl.Series("medq", medq),
    )
    # as-of league 1H share by position class (games dated strictly before)
    daily = (
        base.group_by("pos_code", "game_date")
        .agg(
            pl.col("h1").sum().alias("d_h1"),
            pl.col("q1").sum().alias("d_q1"),
            (pl.col("h1") + pl.col("h2") + pl.col("ot")).sum().alias("d_tot"),
        )
        .sort(["pos_code", "game_date"])
        .with_columns(
            pl.col("d_h1").cum_sum().over("pos_code").alias("c_h1"),
            pl.col("d_q1").cum_sum().over("pos_code").alias("c_q1"),
            pl.col("d_tot").cum_sum().over("pos_code").alias("c_tot"),
        )
        .select("pos_code", "game_date", "c_h1", "c_q1", "c_tot")
    )
    base = base.sort("game_date").join_asof(
        daily.sort("game_date"),
        on="game_date",
        by="pos_code",
        strategy="backward",
        allow_exact_matches=False,
    )
    s0 = (base["c_h1"] / base["c_tot"]).fill_null(0.5).fill_nan(0.5)
    s0q = (base["c_q1"] / base["c_tot"]).fill_null(0.25).fill_nan(0.25)
    base = base.with_columns(s0.alias("s0"), s0q.alias("s0q"))
    s1 = (base["w1"] + K_PRIOR * base["s0"]) / (base["wt"] + K_PRIOR)
    sq = (base["wq"] + K_PRIOR * base["s0q"]) / (base["wt"] + K_PRIOR)
    base = base.with_columns(s1.alias("s1"), sq.alias("sq"), base["wt"].alias("w_pts"))
    # team / opponent as-of half scoring (rolling 20, shifted, across seasons like production r20)
    th = pl.read_parquet(CACHE / "team_halves.parquet")
    gm = fr.select("game_id", "game_date").unique("game_id")
    tg = th.join(gm, on="game_id", how="inner")
    opp = tg.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("t_h1").alias("a_h1"),
        pl.col("t_h2").alias("a_h2"),
        pl.col("t_q1").alias("a_q1"),
    )
    tg = (
        tg.join(opp, on="game_id")
        .filter(pl.col("team_id") != pl.col("opp_id"))
        .sort(["team_id", "game_date", "game_id"])
    )
    roll = [
        pl.col(c).shift(1).rolling_mean(20, min_samples=5).over("team_id").alias(a)
        for c, a in (
            ("t_h1", "t_h1_for"),
            ("a_h1", "t_h1_ag"),
            ("t_h2", "t_h2_for"),
            ("a_h2", "t_h2_ag"),
            ("t_q1", "t_q_for"),
            ("a_q1", "t_q_ag"),
        )
    ]
    tg = tg.with_columns(roll).select(
        "game_id",
        "team_id",
        "opp_id",
        "t_h1_for",
        "t_h1_ag",
        "t_h2_for",
        "t_h2_ag",
        "t_q_for",
        "t_q_ag",
    )
    oppf = tg.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("t_h1_for").alias("o_h1_for"),
        pl.col("t_h1_ag").alias("o_h1_ag"),
        pl.col("t_h2_for").alias("o_h2_for"),
        pl.col("t_h2_ag").alias("o_h2_ag"),
        pl.col("t_q_for").alias("o_q_for"),
        pl.col("t_q_ag").alias("o_q_ag"),
    )
    tg = tg.join(oppf, on=["game_id", "opp_id"], how="left").drop("opp_id")
    out = base.join(tg, on=["game_id", "team_id"], how="left")
    return out.select(
        "game_id",
        "player_id",
        "q1",
        "h1",
        "h2",
        "ot",
        "s1",
        "sq",
        "s0q",
        "w_pts",
        "med1",
        "med2",
        "medq",
        "t_q_for",
        "t_q_ag",
        "o_q_for",
        "o_q_ag",
        "t_h1_for",
        "t_h1_ag",
        "t_h2_for",
        "t_h2_ag",
        "o_h1_for",
        "o_h1_ag",
        "o_h2_for",
        "o_h2_ag",
    )


# --------------------------------------------------------------------------- forecasts
def binom_split_quantiles(qi_total: np.ndarray, share: np.ndarray, chunk: int = 400) -> np.ndarray:
    """Quantiles (CRPS_TAUS, integer support) of H ~ Binomial(T, share) with T uniform on the production quantile grid ``qi_total``."""
    n, k = qi_total.shape
    out = np.empty((n, len(CRPS_TAUS)), dtype=np.int16)
    for s in range(0, n, chunk):
        t = qi_total[s : s + chunk].astype(int)
        p = share[s : s + chunk]
        hi = int(t.max())
        x = np.arange(hi + 1)[None, None, :]
        pmf = binom.pmf(x, t[:, :, None], p[:, None, None]).mean(axis=1)  # (rows, hi+1)
        cdf = np.cumsum(pmf, axis=1)
        for j, tau in enumerate(CRPS_TAUS):
            out[s : s + chunk, j] = (cdf < tau - 1e-12).sum(axis=1).clip(0, hi)
    return out


def _prep_half(df: pl.DataFrame, col: str, shr: pl.Expr) -> pl.DataFrame:
    d = df.with_columns(shr.alias("s_half")).filter(
        pl.col(col).is_not_null() & pl.col("s_half").is_not_null()
    )
    return d.with_columns(
        (pl.col("m10_pts") * pl.col("s_half")).alias("m"),
        (pl.col("sd10_pts") * pl.col("s_half").sqrt()).alias("s"),
        pl.col(col).cast(pl.Float64).alias("y"),
        (pl.col(col).cast(pl.Float64) - pl.col("m10_pts") * pl.col("s_half")).alias("resid"),
    )


def run_arms() -> None:
    fr, cfg = FS.build_frame()
    z: dict[str, list[np.ndarray]] = {k: [] for k in ("qi", "y", "gid", "pid", "season")}
    starts = sorted({p.name.split("_")[1] for p in FS.CACHE.glob("pts_*_full.npz")})
    for s in starts:
        d = np.load(FS.CACHE / f"pts_{s}_full.npz")
        for k in z:
            z[k].append(d[k])
    full = {k: np.concatenate(v) for k, v in z.items()}
    _log(f"production full-game pts OOF rows: {len(full['y'])} (blocks cached: {len(starts)})")
    feats = asof_half_features(fr)
    sf = stat_frame(fr, "pts").join(feats, on=["game_id", "player_id"], how="left")
    sf = sf.with_columns(pl.col("player_id").cast(pl.Int64))
    names0 = stat_feature_names("pts")
    # row order of the production arm: blocks over the test months, rows in stat_frame order within a block
    test_all = sf.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    res: dict[str, list[np.ndarray]] = {
        k: []
        for k in (
            "gid",
            "pid",
            "season",
            "q1",
            "h1",
            "h2",
            "q_b1",
            "q_b2",
            "q_b3",
            "s1",
            "sq",
            "s0q",
            "med1",
            "med2",
            "medq",
            "block",
        )
    }
    for bi, (start, end) in enumerate(month_blocks(test_all)):
        if str(start) not in starts:
            continue
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train0 = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train0.height < MIN_TRAIN:
            continue
        for half, col, shr in (
            (1, "h1", pl.col("s1")),
            (2, "h2", 1.0 - pl.col("s1")),
            (3, "q1", pl.col("sq")),
        ):
            tr, bl = _prep_half(train0, col, shr), _prep_half(block, col, shr)
            model = ContextResidualModel(
                "pts",
                dataclasses.replace(cfg, seed=0),
                [*names0, *(EXTRA_Q if half == 3 else EXTRA)],
            ).fit(tr)
            _, q = model.predict(bl, CRPS_TAUS)
            res[f"q_b{half}"].append(to_integer_support(q).astype(np.int16))
            if half == 1:
                for k, c in (
                    ("gid", "game_id"),
                    ("pid", "player_id"),
                    ("season", "season"),
                    ("h1", "h1"),
                    ("h2", "h2"),
                    ("s1", "s1"),
                    ("med1", "med1"),
                    ("med2", "med2"),
                    ("q1", "q1"),
                    ("sq", "sq"),
                    ("s0q", "s0q"),
                    ("medq", "medq"),
                ):
                    res[k].append(bl[c].to_numpy())
                res["block"].append(np.full(bl.height, bi))
            else:
                assert len(res["gid"][-1]) == bl.height
        _log(f"block {bi + 1} {start} n={block.height} train={train0.height}")
    out = {k: np.concatenate(v) for k, v in res.items()}
    # align to the production OOF rows
    key = pl.DataFrame(
        {
            "gid": full["gid"].astype(str),
            "pid": full["pid"].astype(np.int64),
            "i": np.arange(len(full["y"])),
        }
    )
    mine = pl.DataFrame(
        {
            "gid": out["gid"].astype(str),
            "pid": out["pid"].astype(np.int64),
            "j": np.arange(len(out["gid"])),
        }
    )
    j = key.join(mine, on=["gid", "pid"], how="inner").sort("i")
    _log(f"production rows {len(full['y'])}, matched half rows {j.height}")
    ii, jj = j["i"].to_numpy(), j["j"].to_numpy()
    qi_t = full["qi"][ii].astype(int)
    s1 = out["s1"][jj]
    q_a1 = binom_split_quantiles(qi_t, s1)
    q_a2 = binom_split_quantiles(qi_t, 1.0 - s1)
    q_n = to_integer_support(0.5 * qi_t.astype(float)).astype(np.int16)
    sq = out["sq"][jj]
    q_a3 = binom_split_quantiles(qi_t, sq)
    q_n3 = to_integer_support(out["s0q"][jj][:, None] * qi_t.astype(float)).astype(np.int16)
    np.savez(
        CACHE / "forecasts.npz",
        gid=full["gid"][ii],
        pid=full["pid"][ii],
        season=full["season"][ii],
        y_total=full["y"][ii],
        h1=out["h1"][jj],
        h2=out["h2"][jj],
        s1=s1,
        med1=out["med1"][jj],
        med2=out["med2"][jj],
        q_n=q_n,
        q_a1=q_a1,
        q_a2=q_a2,
        q_a3=q_a3,
        q_n3=q_n3,
        q1=out["q1"][jj],
        sq=sq,
        medq=out["medq"][jj],
        q_b3=out["q_b3"][jj],
        q_b1=out["q_b1"][jj],
        q_b2=out["q_b2"][jj],
        q_total=qi_t.astype(np.int16),
    )
    _log("saved forecasts.npz")


# --------------------------------------------------------------------------- scoring
def ci(v: np.ndarray, gid: np.ndarray, seed: int = 20261012) -> list[float]:
    b = _clustered_boot_means(v.astype(float), gid, N_BOOT, np.random.default_rng(seed))
    return [float(v.mean()), float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))]


def over_under(q: np.ndarray, med: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(correct, usable) per row: line = floor(median)+0.5; predicted over iff more than half the quantile grid is above the line."""
    line = np.floor(med) + 0.5
    p_over = (q > line[:, None]).mean(axis=1)
    pred = p_over > 0.5
    act = h > line
    ok = ~np.isnan(med)
    return (pred == act).astype(float), ok


def arm_metrics(
    q: np.ndarray, h: np.ndarray, med: np.ndarray, gid: np.ndarray, mask: np.ndarray
) -> dict[str, Any]:
    qm, hm, gm = q[mask], h[mask], gid[mask]
    crps = crps_from_quantiles(qm.astype(float), hm.astype(float))
    mae = np.abs(qm[:, 99].astype(float) - hm)
    corr, ok = over_under(qm, med[mask], hm)
    okm = ok
    return {
        "n": int(mask.sum()),
        "crps": ci(crps, gm),
        "mae_median": ci(mae, gm),
        "bias_mean": ci(qm.astype(float).mean(axis=1) - hm, gm),
        "ou_acc": ci(corr[okm], gm[okm]) if okm.any() else None,
        "ou_n": int(okm.sum()),
        "ou_base_over": float((hm[okm] > np.floor(med[mask][okm]) + 0.5).mean())
        if okm.any()
        else None,
        "_crps": crps,
        "_mae": mae,
        "_corr": corr,
        "_ok": okm,
    }


def paired(a: dict[str, Any], b: dict[str, Any], gid: np.ndarray, key: str) -> list[float]:
    return ci(a[key] - b[key], gid)


def run_score() -> dict[str, Any]:
    z = np.load(CACHE / "forecasts.npz")
    gid = z["gid"].astype(str)
    feats = (
        FS.PR.build_frame()[0]
        .select(
            "game_id",
            "player_id",
            "abs_margin",
            "exp_margin",
            "minutes",
            "min10",
            "m10_pts",
            "team_id",
        )
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )
    tab = pl.read_parquet(CACHE / "half_table.parquet").select(
        "game_id", "player_id", "player_name"
    )
    team = pl.read_parquet(CACHE / "team_halves.parquet")
    d = (
        pl.DataFrame(
            {"game_id": gid, "player_id": z["pid"].astype(np.int64), "i": np.arange(len(gid))}
        )
        .join(feats, on=["game_id", "player_id"], how="left")
        .join(tab, on=["game_id", "player_id"], how="left")
        .sort("i")
    )
    tot = team.group_by("game_id").agg(
        pl.col("t_ot").sum().alias("ot_pts"),
        (pl.col("t_h1") + pl.col("t_h2") + pl.col("t_ot")).sum().alias("both"),
    )
    gm_final = (
        team.join(
            team.select(
                "game_id", pl.col("team_id").alias("o"), pl.col("t_total").alias("t_opp_total")
            ),
            on="game_id",
        )
        .filter(pl.col("team_id") != pl.col("o"))
        .select(
            "game_id", "team_id", (pl.col("t_total") - pl.col("t_opp_total")).alias("final_margin")
        )
    )
    d = d.join(gm_final, on=["game_id", "team_id"], how="left").join(
        tot.select("game_id", "ot_pts"), on="game_id", how="left"
    )
    absm = d["abs_margin"].to_numpy()
    cuts = np.nanquantile(absm, [1 / 3, 2 / 3])
    slices: dict[str, np.ndarray] = {
        "all": np.ones(len(gid), bool),
        f"exp |margin| low (<{cuts[0]:.1f})": absm < cuts[0],
        f"exp |margin| mid ({cuts[0]:.1f}-{cuts[1]:.1f})": (absm >= cuts[0]) & (absm < cuts[1]),
        f"exp |margin| high (>={cuts[1]:.1f})": absm >= cuts[1],
        "exp |margin| >= 8 (heavy favourite)": absm >= 8,
        "ex-post final |margin| >= 15": np.abs(d["final_margin"].to_numpy()) >= 15,
        "ex-post final |margin| < 15": np.abs(d["final_margin"].to_numpy()) < 15,
        "ex-post played < 60% of min10": (d["minutes"] < 0.6 * d["min10"]).to_numpy(),
        "no overtime": (d["ot_pts"].fill_null(0) == 0).to_numpy(),
        "stars (m10_pts >= 20)": (d["m10_pts"] >= 20).to_numpy(),
    }
    res: dict[str, Any] = {
        "n": len(gid),
        "n_games": int(len(np.unique(gid))),
        "abs_margin_cuts": [float(c) for c in cuts],
        "slices": {},
    }
    for sname, mask in slices.items():
        sres: dict[str, Any] = {}
        for half, h, med in (
            (1, z["h1"], z["med1"]),
            (2, z["h2"], z["med2"]),
            (3, z["q1"], z["medq"]),
        ):
            arms = {
                "naive_0.5xfull": arm_metrics(z["q_n3" if half == 3 else "q_n"], h, med, gid, mask),
                "a_split": arm_metrics(z[f"q_a{half}"], h, med, gid, mask),
                "b_direct": arm_metrics(z[f"q_b{half}"], h, med, gid, mask),
            }
            g = gid[mask]
            sres["Q1" if half == 3 else f"H{half}"] = {
                "mean_actual": float(h[mask].mean()),
                "sd_actual": float(h[mask].std()),
                "arms": {
                    k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                    for k, v in arms.items()
                },
                "delta_crps_a_vs_naive": paired(
                    arms["a_split"], arms["naive_0.5xfull"], g, "_crps"
                ),
                "delta_crps_b_vs_naive": paired(
                    arms["b_direct"], arms["naive_0.5xfull"], g, "_crps"
                ),
                "delta_crps_b_vs_a": paired(arms["b_direct"], arms["a_split"], g, "_crps"),
            }
        res["slices"][sname] = {**sres, "n": int(mask.sum())}
    # coverage of the 80% interval (q10..q90 of the grid) for each arm and half, all rows
    cov = {}
    for half, h in ((1, z["h1"]), (2, z["h2"]), (3, z["q1"])):
        for arm, key in (
            ("naive", "q_n3" if half == 3 else "q_n"),
            ("a_split", f"q_a{half}"),
            ("b_direct", f"q_b{half}"),
        ):
            q = z[key]
            lo, hi = q[:, 19], q[:, 179]  # tau ~0.1 and ~0.9
            cov[f"{'Q1' if half == 3 else 'H' + str(half)}_{arm}"] = float(
                ((h >= lo) & (h <= hi)).mean()
            )
    res["coverage_80_all"] = cov
    # examples (deterministic rules)
    sel_star = (d["m10_pts"] >= 20).to_numpy()
    h1, h2 = z["h1"], z["h2"]
    pm1, pm2 = z["q_b1"][:, 99], z["q_b2"][:, 99]
    hit_idx = np.where(sel_star & (h1 == pm1) & (h2 == pm2))[0]
    blow = np.where(sel_star & (np.abs(d["final_margin"].to_numpy()) >= 25))[0]
    miss_idx = blow[np.argmax(np.abs(h2[blow] - pm2[blow]))] if len(blow) else None

    def ex(i: int) -> dict[str, Any]:
        return {
            "player": d["player_name"][i],
            "game_id": gid[i],
            "minutes": float(d["minutes"][i]),
            "m10_pts": float(d["m10_pts"][i]),
            "final_margin_of_players_team": float(d["final_margin"][i]),
            "exp_margin": float(d["exp_margin"][i]),
            "pred_median_1H": int(pm1[i]),
            "actual_1H": int(h1[i]),
            "pred_median_2H": int(pm2[i]),
            "actual_2H": int(h2[i]),
            "pred_median_total": int(z["q_total"][i, 99]),
            "actual_total": int(z["y_total"][i]),
        }

    res["example_hit"] = ex(int(hit_idx[0])) if len(hit_idx) else None
    res["n_hit_candidates"] = int(len(hit_idx))
    res["example_miss"] = ex(int(miss_idx)) if miss_idx is not None else None
    res["reconcile"] = json.loads((CACHE / "reconcile.json").read_text())
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=1))
    for sname, s in res["slices"].items():
        for hk in ("H1", "H2", "Q1"):
            a = s[hk]["arms"]
            print(
                f"{sname:<40} {hk} n={s['n']} crps naive {a['naive_0.5xfull']['crps'][0]:.3f} a {a['a_split']['crps'][0]:.3f} b {a['b_direct']['crps'][0]:.3f}  ou b {a['b_direct']['ou_acc'][0] if a['b_direct']['ou_acc'] else None}"
            )
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["build", "arms", "score"])
    a = ap.parse_args(argv)
    if a.stage == "build":
        print(json.dumps(build_half_table(), indent=1))
    elif a.stage == "arms":
        run_arms()
    else:
        run_score()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
