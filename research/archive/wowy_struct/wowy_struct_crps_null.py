# ruff: noqa: E501
"""WOWY_STRUCT G1 conversion check: the permuted-arm null in REAL integer-support CRPS (DRAFT rule; dev season 2022 only).

    cd <worktree> && NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 \
        /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_struct_crps_null [--k 30]

Question: the G1 thresholds t_c (pts leader out 0.0050, pts top 25% 0.0063, ast leader out 0.0030) came from a squared-error
permutation null converted to CRPS with a Gaussian rule (CRPS ~ 0.564 x RMSE; relative CRPS gain ~ half the relative
squared-error gain). This measures the same null directly: ROLE, S+R and K player-season-permuted P+R arms are fitted
with the production heads (``ContextResidualModel``, lower_tail="mixture", seed 0, production LightGBM settings) on the
D1 design of the gate (train 2020-21, test 2022, one fit, warm features), scored by integer-support CRPS (199-point grid,
``ceil(q - 0.5)``). Seasons 2020-2022 only: the frame is cut to season <= 2022 before any arm exists and asserted.
No 2023, 2024 or 2025 outcome is read. The decision rule (fixed before the run, ``decide``): every cell's
max(0.003, 2 sd of the CRPS-scale null) within +-20% of t_c, else report and stop (t_c is never changed here).
Writes ``reports/prereg_wowy_redistribution/crps_null.json`` (does not touch gate.json); per-arm checkpoints under ``data/ops/wowy_struct_crps_null_cache``.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import crps_from_quantiles
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G

MAX_SEASON_THIS_CHECK = 2022
T_C = {"pts|leader_out": 0.0050, "pts|top25_fixed": 0.0063, "ast|leader_out": 0.0030}
FLOOR = 0.003
TOL = 0.20
CHOSEN = {"pts": "struct_u_pts", "ast": "struct_d_ast"}
CELLS = (("pts", "leader_out"), ("pts", "top25_fixed"), ("ast", "leader_out"))
CUT_Q = (0.75, 0.90)
CACHE = G.REAL / "data/ops/wowy_struct_crps_null_cache"


def _f(x: Any) -> float:
    return float(x)


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


# --------------------------------------------------------------------------- pure pieces (unit-tested)


def perm_player_season(
    values: np.ndarray, player: np.ndarray, season: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Permute ``values`` within each player-season (same construction as the gate and the squared-error null)."""
    v = values.copy()
    key = np.char.add(np.char.add(player.astype(str), "_"), season.astype(str))
    for k in np.unique(key):
        idx = np.flatnonzero(key == k)
        v[idx] = v[rng.permutation(idx)]
    return v


def clustered_null(
    gain: np.ndarray, games: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """``gain`` is ``[K, n]`` per-row (CRPS_ROLE - CRPS_P+R_k); positive means the permuted arm is better. Returns the
    null mean and sd across the K draws on the cell, the 2-sd threshold, and game-clustered percentile CIs (resample
    games, recompute every draw's cell mean, then the mean and the sd across draws)."""
    k_draws = gain.shape[0]
    _, inv = np.unique(games, return_inverse=True)
    g = int(inv.max()) + 1
    cnt = np.bincount(inv, minlength=g).astype(float)
    sums = np.stack(
        [np.bincount(inv, weights=gain[k], minlength=g) for k in range(k_draws)], axis=1
    )
    per_draw = gain.mean(axis=1)
    out: dict[str, Any] = {
        "n_rows": int(gain.shape[1]),
        "n_games": g,
        "K": int(k_draws),
        "null_mean": _f(per_draw.mean()),
        "null_sd": _f(per_draw.std(ddof=1)),
        "two_sd": _f(2 * per_draw.std(ddof=1)),
        "null_max": _f(per_draw.max()),
    }
    rng = np.random.default_rng(seed)
    means, sds = [], []
    for _ in range(n_boot // 250):
        draws = rng.integers(0, g, size=(250, g))
        s = sums[draws].sum(axis=1)  # [250, K]
        c = cnt[draws].sum(axis=1)[:, None]
        m = s / c
        means.append(m.mean(axis=1))
        sds.append(m.std(axis=1, ddof=1))
    mb, sb = np.concatenate(means), np.concatenate(sds)
    out["null_mean_ci95"] = [_f(np.quantile(mb, 0.025)), _f(np.quantile(mb, 0.975))]
    out["two_sd_ci95"] = [_f(2 * np.quantile(sb, 0.025)), _f(2 * np.quantile(sb, 0.975))]
    return out


def decide(
    measured_two_sd: dict[str, float], t_c: dict[str, float] = T_C, tol: float = TOL
) -> dict[str, Any]:
    """The pre-registered decision rule of this check: threshold_c = max(0.003, 2 sd of the CRPS-scale null); every cell
    must satisfy |threshold_c / t_c - 1| <= tol. Returns per-cell ratios and the verdict."""
    cells: dict[str, Any] = {}
    for cell, t in t_c.items():
        thr = max(FLOOR, measured_two_sd[cell])
        ratio = thr / t
        cells[cell] = {
            "measured_two_sd": measured_two_sd[cell],
            "measured_threshold": thr,
            "t_c": t,
            "ratio_to_t_c": ratio,
            "within_tolerance": bool(abs(ratio - 1.0) <= tol + 1e-12),
        }
    return {
        "cells": cells,
        "all_within": all(v["within_tolerance"] for v in cells.values()),
        "tol": tol,
    }


# --------------------------------------------------------------------------- arms


def fit_score(
    tr: pl.DataFrame, te: pl.DataFrame, stat: str, names: list[str], cfg: ContextResidualConfig
) -> tuple[np.ndarray, np.ndarray]:
    """One production-heads fit on ``tr`` (sorted by date), mixture quantiles on ``te`` -> (integer CRPS, squared error of
    the mean head) per test row."""
    model = ContextResidualModel(stat, cfg, names).fit(tr)
    assert model.mean_head is not None
    x = te.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()
    mu = np.asarray(model.mean_head.predict(x))
    s = model._scale(x)
    m = te["m"].to_numpy()
    q = model.lower_tail_quantiles(te, m + mu, s, CRPS_TAUS)
    qi = to_integer_support(q)
    y = te["y"].to_numpy()
    crps = np.asarray(crps_from_quantiles(qi.astype(float), y))
    se = (te["resid"].to_numpy() - mu) ** 2
    return crps, se


def cached_arm(
    stat: str,
    arm: str,
    tr: pl.DataFrame,
    te: pl.DataFrame,
    names: list[str],
    cfg: ContextResidualConfig,
) -> dict[str, np.ndarray]:
    """``fit_score`` with a per-(stat, arm) checkpoint under ``CACHE`` (a re-run reuses finished arms)."""
    p = CACHE / f"{stat}_{arm}.npz"
    if p.exists():
        z = np.load(p)
        return {k: z[k] for k in z.files}
    crps, se = fit_score(tr, te, stat, names, cfg)
    np.savez_compressed(p, crps=crps, se=se)
    return {"crps": crps, "se": se}


def main(k_draws: int = 30, n_boot: int = 2000) -> dict[str, Any]:
    t0 = time.time()
    db = str(G.REAL / "nba.duckdb")
    hdb = str(G.REAL / "data/history/nba_history.duckdb")
    feats, outp, games = SG.warm_frame(db, hdb)
    full = pl.concat([SG.load_box_state(hdb), SG.load_box_state(db)], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = games.select("game_id", "game_date")
    pos = pl.concat([SG.load_positions(hdb), SG.load_positions(db)]).unique("player_id")
    fe = feats.filter(pl.col("season").is_in([2020, 2021, 2022]))
    assert int(fe["season"].max()) <= MAX_SEASON_THIS_CHECK  # type: ignore[arg-type]
    # realised minutes: a LABEL for the lower-tail short flag (as ``attach_realised``), never a model input
    mins = (
        full.filter(pl.col("season") <= MAX_SEASON_THIS_CHECK)
        .select(pl.col("game_id"), pl.col("player_id").cast(pl.Int64), "minutes")
        .unique(["game_id", "player_id"])
    )
    n0 = fe.height
    fe = fe.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        mins, on=["game_id", "player_id"], how="left"
    )
    assert fe.height == n0 and fe["minutes"].null_count() == 0
    del feats, full
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    fr = SA.attach_struct(fe, SA.build_struct(rows, op, pool, states, dates, pos))
    f20, f21, f22 = (fr.filter(pl.col("season") == y) for y in (2020, 2021, 2022))
    sel = pl.concat(
        [
            f.filter(
                (pl.col("vac_min") >= 24.0)
                & (pl.col("has_report") == 1)
                & (pl.col("n_out_rot") > 0)
            )
            for f in (f20, f21)
        ],
        how="vertical_relaxed",
    )
    cuts: dict[str, tuple[float, float]] = {}
    for stat, col in CHOSEN.items():
        a = np.abs(sel[col].to_numpy())
        a = a[a > 0]
        cuts[stat] = (_f(np.quantile(a, CUT_Q[0])), _f(np.quantile(a, CUT_Q[1])))
    _log(f"frames ready; cuts {cuts}", t0)

    cfg = ContextResidualConfig(lower_tail="mixture", n_jobs=2)
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "design": "D1: train 2020-21, test 2022, one fit per arm, production heads + lower-tail mixture, integer CRPS",
        "K": k_draws,
        "seasons_read": [2020, 2021, 2022],
        "cells": {},
    }
    conv: dict[str, float] = {}
    for stat in ("pts", "ast"):
        col, role = CHOSEN[stat], f"role_share_{stat}"
        names = stat_feature_names(stat)
        tr = stat_frame(
            pl.concat([f20, f21], how="vertical_relaxed").filter(pl.col("has_report") == 1),
            stat,
        ).sort(["game_date", "game_id", "player_id"])
        te = stat_frame(f22.filter(pl.col("has_report") == 1), stat).sort(
            ["game_date", "game_id", "player_id"]
        )
        ok_tr = np.isfinite(tr["resid"].to_numpy())
        tr = tr.filter(pl.Series(ok_tr))
        masks = SG.cell_masks(te, col, f"leader_out_{stat}", cuts[stat])

        base = cached_arm(stat, "ROLE", tr, te, [*names, role], cfg)
        _log(f"{stat} ROLE done", t0)
        sr = cached_arm(stat, "SR", tr, te, [*names, role, col], cfg)
        _log(f"{stat} S+R done", t0)
        rng = np.random.default_rng(0)
        pr_crps, pr_se = [], []
        for k in range(k_draws):
            ptr = perm_player_season(
                tr[col].to_numpy(), tr["player_id"].to_numpy(), tr["season"].to_numpy(), rng
            )
            pte = perm_player_season(
                te[col].to_numpy(), te["player_id"].to_numpy(), te["season"].to_numpy(), rng
            )
            r = cached_arm(
                stat,
                f"PR{k:02d}",
                tr.with_columns(pl.Series("_perm", ptr)),
                te.with_columns(pl.Series("_perm", pte)),
                [*names, role, "_perm"],
                cfg,
            )
            pr_crps.append(r["crps"])
            pr_se.append(r["se"])
            _log(f"{stat} P+R draw {k + 1}/{k_draws}", t0)
        pr_c, pr_s = np.stack(pr_crps), np.stack(pr_se)
        for st_, sn in CELLS:
            if st_ != stat:
                continue
            mk = masks[sn] & np.isfinite(base["crps"]) & np.all(np.isfinite(pr_c), axis=0)
            gain = base["crps"][mk][None, :] - pr_c[:, mk]
            res = clustered_null(gain, te["game_id"].to_numpy()[mk], n_boot=n_boot)
            # same fits, squared error of the mean head (the unit the t_c conversion started from)
            rel = 100 * (1.0 - pr_s[:, mk].sum(axis=1) / base["se"][mk].sum())
            crps_level = _f(base["crps"][mk].mean())
            res["role_crps_level"] = crps_level
            res["se_null_mean_pct"] = _f(rel.mean())
            res["se_null_sd_pct"] = _f(rel.std(ddof=1))
            res["gaussian_rule_two_sd_from_same_fits"] = (
                2 * 0.5 * res["se_null_sd_pct"] / 100 * crps_level
            )
            res["observed_SR_minus_ROLE_crps"] = _f((sr["crps"][mk] - base["crps"][mk]).mean())
            res["observed_SR_se_gain_pct"] = 100 * _f(
                1.0 - sr["se"][mk].sum() / base["se"][mk].sum()
            )
            out["cells"][f"{stat}|{sn}"] = res
            conv[f"{stat}|{sn}"] = res["two_sd"]
            _log(
                f"{stat}|{sn}: n {res['n_rows']} games {res['n_games']} 2sd {res['two_sd']:.5f}", t0
            )
    out["decision"] = decide(conv)
    gate = json.loads(G.OUT.read_text())
    d1 = "D1_train2020-21_test2022"
    out["converted_D1_two_sd_from_gate_json"] = {
        c: gate["struct_null"]["cells"][c][d1]["two_sd_crps_equiv(unverified)"] for c in conv
    }
    out["ratio_to_converted_D1"] = {
        c: conv[c] / out["converted_D1_two_sd_from_gate_json"][c] for c in conv
    }
    path = Path(__file__).resolve().parents[3] / "reports/prereg_wowy_redistribution/crps_null.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=float))
    print(
        json.dumps(
            {"decision": out["decision"], "ratio_to_converted_D1": out["ratio_to_converted_D1"]},
            indent=1,
            default=float,
        ),
        flush=True,
    )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    main(args.k, args.boot)
