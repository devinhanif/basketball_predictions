# ruff: noqa: E501
"""REPLACEMENT_DELTA minimum-data gate and data-capture audit (non-negotiable 8). DRAFT rule: docs/prereg/REPLACEMENT_DELTA.md.

    cd <worktree> && NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 \\
        /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.eval.replacement_delta_gate [--skip-planted]

Read-only on ``$NBA_ROOT/nba.duckdb`` and ``$NBA_ROOT/data/history/nba_history.duckdb`` (every query ``season <= 2024``; season 2025 is
never read), the stored A0 components in ``$NBA_ROOT/reports/lower_tail`` (``row_y`` is replaced by NaN on load: no 2023/2024 outcome
reaches any statistic) and the paired-arm score arrays in ``$NBA_ROOT/reports/minutes_v2`` (per-row CRPS of two production-protocol arms,
used only for the sd and design effect of a refit contrast). Outcomes of the DEV seasons (2020, 2021 and the warm-up season 2022) are read for
the effect-size coefficient kappa only; nothing from 2023-2024 is related to any column of this rule.

Writes ``reports/prereg_replacement_delta/gate.json`` (relative to the working tree) and prints one line per stage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.props import repl_delta as RD

REAL = Path(os.environ.get("NBA_ROOT", "/Users/devin/Downloads/nba-prediction")).resolve()
DB = REAL / "nba.duckdb"
HDB = REAL / "data/history/nba_history.duckdb"
LT = REAL / "reports/lower_tail"
MV2 = REAL / "reports/minutes_v2"
MAX_SEASON = 2024
DEV_SEASONS = (2020, 2021)
WARM_SEASON = 2022
TEST_SEASONS = (2023, 2024)
K_SEASONS = tuple(
    range(2015, 2022)
)  # prior-strength estimation: complete seasons before the 2022 warm-up
FLOOR = -0.005
DEFF_FREE = 1.1
PLANT_CUTOFF = dt.date(2024, 1, 15)
OUT = Path("reports/prereg_replacement_delta/gate.json")
STATS = RD.STATS


def _json(o: Any) -> Any:
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return float(o)


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load_box(db: Path) -> pl.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.fga, s.fta, s.tov, s.oreb, "
            "CAST(g.game_date AS DATE) AS game_date, g.season "
            "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024 AND s.minutes > 0"
        ).pl()
    finally:
        con.close()


# --------------------------------------------------------------------------- the warm production frame


def warm_world() -> dict[str, Any]:
    """Production feature rows over history + nba.duckdb in ONE ``build_features`` (every season warm), the shared state
    table, the OUT rotation rows and the prepped box. Real tips come from both schedule directories; read-only."""
    import yaml

    import nba.features.game_tipoff as GT
    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import (
        _CI,
        _DI,
        MIN_PRIOR_PLAYED,
        ROTATION_MIN40,
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
        player_states,
    )

    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    parts = [load_inputs(str(d), None, max_season=MAX_SEASON) for d in (HDB, DB)]
    games = pl.concat([p[0] for p in parts], how="vertical_relaxed").with_columns(
        pl.col("game_date").cast(pl.Date)
    )
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 loaded: frozen holdout")
    pgs = pl.concat([p[1] for p in parts], how="vertical_relaxed")
    static = pl.concat([p[2] for p in parts], how="vertical_relaxed").unique(
        "player_id", keep="last"
    )
    avail = pl.concat([p[3] for p in parts], how="vertical_relaxed")
    orig = GT.build_game_tipoff

    def both(schedule_dir: Any = None, max_season: int | None = None) -> pl.DataFrame:
        return pl.concat(
            [
                orig(REAL / "data/schedule", max_season),
                orig(REAL / "data/history/schedule", max_season),
            ],
            how="vertical_relaxed",
        ).unique("game_id", keep="last")

    GT.build_game_tipoff = both
    try:
        flagged, info = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    finally:
        GT.build_game_tipoff = orig
    feats = build_features(games, pgs, static, flagged, elo)
    played = (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = player_states(played)
    st = RD.state_table(played, states, _DI, _CI)
    out_rows = RD.out_rotation_rows(flagged, info, st, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    out_all = pl.DataFrame(
        [(g, int(p)) for g, ps in flagged.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    )
    box = pl.concat([load_box(HDB), load_box(DB)], how="vertical_relaxed")
    return {
        "games": games,
        "feats": feats,
        "flagged": flagged,
        "info": info,
        "st": st,
        "out_rows": out_rows,
        "out_all": out_all,
        "b": RD.prep_box(box),
        "box": box,
        "pgs": pgs,
        "played": played,
    }


def build_rd(
    w: dict[str, Any],
    repl: Any,
    k: Any,
    b: pl.DataFrame | None = None,
    st: pl.DataFrame | None = None,
) -> pl.DataFrame:
    feats = w["feats"]
    scored = feats.select("game_id", "team_id", "player_id", "game_date", "season").with_columns(
        pl.col("player_id").cast(pl.Int64)
    )
    return RD.build_repl_delta(
        w["b"] if b is None else b,
        w["st"] if st is None else st,
        w["out_rows"],
        w["out_all"],
        scored,
        set(w["flagged"]),
        repl,
        k,
        placebo_seed=0,
    )


# --------------------------------------------------------------------------- statistics


def cluster_ols(y: np.ndarray, x: np.ndarray, ctrl: np.ndarray, cl: np.ndarray) -> dict[str, Any]:
    """Frisch-Waugh coefficient of ``x`` on ``y`` controlling ``ctrl`` (an intercept is added), cluster-robust (CR0) se."""
    c = np.column_stack([np.ones(len(y)), ctrl]) if ctrl.size else np.ones((len(y), 1))
    gx = np.linalg.lstsq(c, x, rcond=None)[0]
    gy = np.linalg.lstsq(c, y, rcond=None)[0]
    xt, yt = x - c @ gx, y - c @ gy
    den = float((xt**2).sum())
    if den <= 0:
        return {"coef": float("nan"), "se": float("nan"), "t": float("nan"), "n": float(len(y))}
    beta = float((xt * yt).sum() / den)
    e = yt - beta * xt
    _, inv = np.unique(cl.astype(str), return_inverse=True)
    sc = np.bincount(inv, weights=xt * e)
    se = math.sqrt(float((sc**2).sum())) / den
    return {
        "coef": beta,
        "se": se,
        "t": beta / se if se > 0 else float("nan"),
        "n": float(len(y)),
        "clusters": float(inv.max() + 1),
        "gamma": [float(v) for v in gx],
    }


def gain_and_var(
    q_old: np.ndarray, q_new: np.ndarray, chunk: int = 400
) -> tuple[np.ndarray, np.ndarray]:
    """Per row: mean and variance over Y ~ G (G's own equal-mass quantile atoms) of CRPS(F, Y) - CRPS(G, Y), F = ``q_old``,
    G = ``q_new`` (199-point grids). Outcome-free."""
    from nba.props.context_residual import CRPS_TAUS

    taus = CRPS_TAUS
    gm = np.empty(len(q_old))
    gv = np.empty(len(q_old))
    for a in range(0, len(q_old), chunk):
        f = q_old[a : a + chunk].astype(float)
        g = q_new[a : a + chunk].astype(float)
        y = g[:, :, None]

        def pin(q: np.ndarray, y: np.ndarray = y) -> np.ndarray:
            d = y - q[:, None, :]
            return np.asarray(np.where(d >= 0, taus * d, (taus - 1.0) * d).mean(axis=2) * 2.0)

        diff = pin(f) - pin(g)
        gm[a : a + chunk] = diff.mean(axis=1)
        gv[a : a + chunk] = diff.var(axis=1)
    return gm, gv


def cluster_deff(d: np.ndarray, key: np.ndarray) -> dict[str, float]:
    d = np.asarray(d, dtype=float)
    _, inv = np.unique(np.asarray(key).astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    n, mu = len(d), float(d.mean())
    s = np.bincount(inv, weights=d - mu, minlength=g)
    se = math.sqrt(g / (g - 1) * float((s**2).sum())) / n
    sd = float(d.std(ddof=1))
    return {
        "n": float(n),
        "sd_row": sd,
        "deff": (se * math.sqrt(n) / sd) ** 2 if sd > 0 else float("nan"),
        "clusters": float(g),
    }


# --------------------------------------------------------------------------- stages


def capture_audit() -> dict[str, Any]:
    """What data exists, what is used, what is repairable (read-only counts; every query caps season <= 2024)."""
    res: dict[str, Any] = {}
    for name, db in (("history", HDB), ("main", DB)):
        con = duckdb.connect(str(db), read_only=True)

        def q(sql: str, con: duckdb.DuckDBPyConnection = con) -> list[Any]:
            return con.execute(sql).fetchall()

        res[name] = {
            "box_played_rows_by_season": q(
                "SELECT g.season, count(*), sum(CASE WHEN s.fga IS NULL OR s.fta IS NULL OR s.tov IS NULL OR s.oreb IS NULL THEN 1 ELSE 0 END) "
                "FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024 AND s.minutes > 0 GROUP BY 1 ORDER BY 1"
            ),
            "games_by_season": q(
                "SELECT season, count(*) FROM games WHERE season <= 2024 GROUP BY 1 ORDER BY 1"
            ),
            "official_report_games_by_season": q(
                "SELECT g.season, count(DISTINCT a.game_id), min(a.as_of), max(a.as_of) FROM player_availability a "
                "JOIN games g USING (game_id) WHERE g.season <= 2024 AND a.source = 'nba_official_report' GROUP BY 1 ORDER BY 1"
            ),
            "stints_games_by_season": q(
                "SELECT g.season, count(DISTINCT s.game_id) FROM stints s JOIN games g USING (game_id) WHERE g.season <= 2024 GROUP BY 1 ORDER BY 1"
            ),
            "possessions_games_by_season": q(
                "SELECT g.season, count(DISTINCT p.game_id) FROM possessions p JOIN games g USING (game_id) WHERE g.season <= 2024 GROUP BY 1 ORDER BY 1"
            ),
        }
        con.close()
    return res


def stage_priors(w: dict[str, Any]) -> dict[str, Any]:
    b = w["b"]
    repl = RD.replacement_rates(b)
    k = RD.estimate_k(b, repl, K_SEASONS)
    return {
        "repl": {str(s): v for s, v in repl.items()},
        "k": k,
        "k_values": {x: v["k"] for x, v in k.items()},
        "k_seasons": list(K_SEASONS),
        "fringe_player_seasons_2022": int(
            RD.fringe_player_seasons(b).filter(pl.col("season") == 2022).height
        ),
    }


def stage_parity(w: dict[str, Any], rd: pl.DataFrame) -> dict[str, Any]:
    f = (
        w["feats"]
        .select("game_id", "player_id", "team_id", "season", "vac_min", "n_out_rot", "has_report")
        .with_columns(pl.col("player_id").cast(pl.Int64))
    )
    j = f.join(rd.select("game_id", "player_id", "n_out"), on=["game_id", "player_id"], how="left")
    rep = j.filter(pl.col("has_report") == 1)
    mism = rep.filter(pl.col("n_out").fill_null(-1) != pl.col("n_out_rot").fill_null(-2))
    # vac_min parity through the OUT rows
    mine = w["out_rows"].group_by(["game_id", "team_id"]).agg(pl.col("min10").sum().alias("vm"))
    tj = (
        f.filter(pl.col("has_report") == 1)
        .unique(["game_id", "team_id"])
        .join(mine, on=["game_id", "team_id"], how="left")
        .with_columns(pl.col("vm").fill_null(0.0))
    )
    return {
        "reported_rows": int(rep.height),
        "n_out_vs_n_out_rot_mismatch_rows": int(mism.height),
        "vac_min_max_abs_diff_team_games": float(
            (tj["vm"] - tj["vac_min"].fill_null(0.0)).abs().max()
        ),
        "no_report_rows_with_feature": int(
            j.filter((pl.col("has_report") == 0) & pl.col("n_out").is_not_null()).height
        ),
    }


def stage_kappa(w: dict[str, Any], rd: pl.DataFrame) -> dict[str, Any]:
    """Dev-season effect size: the coefficient of the (residualised) REPLACEMENT_DELTA column on the stat residual vs the recency mean,
    controlling production's own vacancy columns; plus the coefficient BEYOND the unsubtracted twin V0 (the quality claim)."""
    from nba.props.context_residual import stat_frame

    fr = (
        w["feats"]
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .join(rd, on=["game_id", "player_id"], how="left")
    )
    res: dict[str, Any] = {}
    for stat in STATS:
        sf = stat_frame(fr, stat).filter((pl.col("has_report") == 1) & (pl.col("n_out") > 0))
        ctrl_a0 = [f"vac_{stat}", "vac_min", "n_out_rot", "min10", f"m10_{stat}", "starter10"]
        ctrl_v0 = [*ctrl_a0, f"v0_{stat}", f"v0d_{stat}"]
        per: dict[str, Any] = {}
        for name, seasons in (("dev_2020_21", DEV_SEASONS), ("holdout_2022", (WARM_SEASON,))):
            sub = sf.filter(pl.col("season").is_in(list(seasons))).drop_nulls(
                [f"rd_{stat}", *ctrl_v0, "resid"]
            )
            y, gid = sub["resid"].to_numpy(), sub["game_id"].to_numpy()
            x = sub[f"rd_{stat}"].to_numpy()
            per[name] = {
                "beyond_A0": cluster_ols(y, x, sub.select(ctrl_a0).to_numpy(), gid),
                "beyond_V0": cluster_ols(y, x, sub.select(ctrl_v0).to_numpy(), gid),
                "V0_alone_beyond_A0": cluster_ols(
                    y, sub[f"v0d_{stat}"].to_numpy(), sub.select(ctrl_a0).to_numpy(), gid
                ),
                "placebo_beyond_A0": cluster_ols(
                    y, sub[f"pq_rd_{stat}"].to_numpy(), sub.select(ctrl_a0).to_numpy(), gid
                ),
            }
        res[stat] = per
    return res


def a0_rows(stat: str) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    c = load_components(stat, LT)
    if int(c["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in stored components")
    c["row_y"] = np.full(
        len(c["row_y"]), np.nan
    )  # outcomes are not needed by any statistic here; NaN so none can reach one
    return c


def paired_arm_sd(stat: str, gid_set: set[tuple[str, int]], mask_fn: Any) -> dict[str, Any]:
    """sd and design effect of a real refit contrast (minutes_v2 A1 minus A0, per-row integer CRPS of two production-protocol arms),
    on the rows ``mask_fn`` selects. Version A of the sizing; outcome-free in the sense that no candidate of THIS rule is scored."""
    a0 = np.load(MV2 / f"props_{stat}_A0.npz", allow_pickle=True)
    a1 = np.load(MV2 / f"props_{stat}_A1.npz", allow_pickle=True)
    d = a1["crps_int"] - a0["crps_int"]
    gid, pid, season = a0["gid"].astype(str), a0["pid"].astype(np.int64), a0["season"]
    m = mask_fn(gid, pid, a0) & np.isfinite(d)
    return {"d": d, "gid": gid, "pid": pid, "season": season, "mask": m}


def stage_cuts(w: dict[str, Any], rd: pl.DataFrame) -> dict[str, float]:
    """Fixed slice cut-offs from the dev seasons only: the 0.75 quantile of |qv_<stat>| over affected rows of seasons 2020-21."""
    fr = (
        w["feats"]
        .select("game_id", "season", pl.col("player_id").cast(pl.Int64))
        .join(rd, on=["game_id", "player_id"], how="left")
    )
    sub = fr.filter(
        pl.col("season").is_in(list(DEV_SEASONS))
        & (pl.col("n_out") > 0)
        & pl.col("share").is_not_null()
    )
    return {s: float(np.quantile(np.abs(sub[f"qv_{s}"].to_numpy()), 0.75)) for s in STATS}


def _slice_block(
    sm: np.ndarray,
    scen_gain: dict[str, np.ndarray],
    dA: np.ndarray,
    gid: np.ndarray,
    dates: dict[str, Any],
) -> dict[str, Any]:
    """Rows, expected delta and version-A power of the pass line on a slice (the rows where the shift is large)."""
    from research.eval import prereg_gates_common as C

    dm = sm & np.isfinite(dA)
    if dm.sum() < 50:
        return {"rows": int(sm.sum())}
    cg = cluster_deff(dA[dm], gid[dm])
    cd = cluster_deff(dA[dm], np.array([str(dates[g]) for g in gid[dm]]))
    return {
        "rows": int(sm.sum()),
        "games": int(np.unique(gid[sm]).size),
        "halfwidth95_A": 1.96 * cg["sd_row"] * math.sqrt(max(cg["deff"], cd["deff"]) / cg["n"]),
        "expected_delta": {nm: float(-gg[sm].mean()) for nm, gg in scen_gain.items()},
        "power_A": {
            nm: C.sizing_row(
                nm,
                cg["sd_row"],
                max(cg["deff"], cd["deff"]),
                float(sm.sum()),
                FLOOR,
                float(-gg[sm].mean()),
            )["power_one_season"]
            for nm, gg in scen_gain.items()
        },
    }


def stage_power(
    w: dict[str, Any], rd: pl.DataFrame, kappa: dict[str, Any], cuts: dict[str, float], t0: float
) -> dict[str, Any]:
    """Per stat and test season: affected-row counts, expected CRPS gain at the dev kappa (outcome-free), half-width and power of the
    pass line (estimate <= min(-0.005, -half-width)) on the AFFECTED rows (a reported game with >= 1 OUT rotation player), under
    version B (outcome-free sd of the shifted-forecast contrast) and version A (sd and design effect of a real refit contrast)."""
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support
    from research.eval import prereg_gates_common as C
    from research.eval import recent_role_full_eval as RF

    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    fr = (
        w["feats"]
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .join(rd, on=["game_id", "player_id"], how="left")
    )
    dates = dict(w["games"].select("game_id", "game_date").rows())
    res: dict[str, Any] = {}
    for stat in STATS:
        c = a0_rows(stat)
        gid, pid, season = c["row_gid"].astype(str), c["row_pid"].astype(np.int64), c["row_season"]
        keys = pl.DataFrame({"game_id": gid, "player_id": pid, "_i": np.arange(len(gid))})
        ctrl_names = [f"vac_{stat}", "vac_min", "n_out_rot", "min10", f"m10_{stat}", "starter10"]
        cols = [
            "game_id",
            "player_id",
            "n_out",
            f"rd_{stat}",
            f"qv_{stat}",
            "share",
            "last_starter",
            "team_game_no",
            *ctrl_names,
        ]
        j = keys.join(
            fr.select(list(dict.fromkeys(cols))), on=["game_id", "player_id"], how="left"
        ).sort("_i")
        n_out = j["n_out"].to_numpy().astype(float)
        have = np.isfinite(j[f"rd_{stat}"].to_numpy().astype(float))
        aff = np.isfinite(n_out) & (n_out > 0) & have
        kd = kappa[stat]["dev_2020_21"]["beyond_A0"]
        kh = kappa[stat]["holdout_2022"]["beyond_A0"]
        kv = max(min(kd["coef"], kh["coef"]), 0.0)
        gam = np.asarray(kd["gamma"], dtype=float)
        ctrl = np.column_stack(
            [np.ones(len(j))] + [j[n].to_numpy().astype(float) for n in ctrl_names]
        )
        xt = np.where(
            aff, j[f"rd_{stat}"].to_numpy().astype(float) - np.nan_to_num(ctrl) @ gam, 0.0
        )
        shift = kv * xt
        raw_shift = np.where(
            aff, kv * j[f"rd_{stat}"].to_numpy().astype(float), 0.0
        )  # WOWY-style accounting: kappa x the RAW column (overstates: part of it is already in production's columns)
        grid = arm_grid("mixture", c, pic, stat)
        q0 = RF.arm_qi(stat, c, pic, pic["row_pi"]).astype(float)
        idx = np.flatnonzero(aff & (shift != 0))
        gm, gv = np.zeros(len(gid)), np.zeros(len(gid))
        if idx.size:
            a, b2 = gain_and_var(q0[idx], to_integer_support(grid[idx] + shift[idx, None]))
            gm[idx], gv[idx] = a, b2
        idx_r = np.flatnonzero(aff & (raw_shift != 0))
        gm_r = np.zeros(len(gid))
        if idx_r.size:
            gm_r[idx_r], _ = gain_and_var(
                q0[idx_r], to_integer_support(grid[idx_r] + raw_shift[idx_r, None])
            )
        kmax = max(max(kd["coef"], kh["coef"]), 0.0)
        raw_col = j[f"rd_{stat}"].to_numpy().astype(float)
        scen_gain: dict[str, np.ndarray] = {}
        for name, kk, raw in (
            ("resid_kmin(primary)", kv, False),
            ("raw_kmin", kv, True),
            ("resid_kmax", kmax, False),
            ("raw_kmax(most generous)", kmax, True),
        ):
            sh = np.where(
                aff, kk * (raw_col if raw else (raw_col - np.nan_to_num(ctrl) @ gam)), 0.0
            )
            ii = np.flatnonzero(aff & (sh != 0))
            gg = np.zeros(len(gid))
            if ii.size:
                gg[ii], _ = gain_and_var(q0[ii], to_integer_support(grid[ii] + sh[ii, None]))
            scen_gain[name] = gg
        pa = paired_arm_sd(stat, set(), lambda g, p, a0: np.ones(len(g), bool))
        pkey = {(g, int(p)): i for i, (g, p) in enumerate(zip(pa["gid"], pa["pid"], strict=True))}
        pos = np.array([pkey.get((g, int(p)), -1) for g, p in zip(gid, pid, strict=True)])
        dA = np.where(pos >= 0, pa["d"][np.maximum(pos, 0)], np.nan)
        cold_n_out = np.load(MV2 / f"props_{stat}_A0.npz", allow_pickle=True)["meta_n_out_rot"][
            np.maximum(pos, 0)
        ]
        per: dict[str, Any] = {
            "kappa_dev_2020_21": kd["coef"],
            "kappa_dev_t": kd["t"],
            "kappa_holdout_2022": kh["coef"],
            "kappa_holdout_t": kh["t"],
            "kappa_used": kv,
            "kappa_larger_of_the_two": kmax,
            "rows_missing_features_share": float((~have).mean()),
            "cold_vs_warm_n_out": {
                str(s): {
                    "rows_with_both": int(((season == s) & (pos >= 0) & np.isfinite(n_out)).sum()),
                    "n_out_differs": int(
                        (
                            (season == s) & (pos >= 0) & np.isfinite(n_out) & (n_out != cold_n_out)
                        ).sum()
                    ),
                    "cold_affected": int(((season == s) & (pos >= 0) & (cold_n_out > 0)).sum()),
                    "warm_affected": int(((season == s) & (pos >= 0) & aff).sum()),
                }
                for s in TEST_SEASONS
            },
        }
        for s in (*TEST_SEASONS, "pooled"):
            m = np.isin(season, TEST_SEASONS) if s == "pooled" else season == s
            fm = m & aff
            dm = fm & np.isfinite(dA)
            e_b = -float(gm[fm].mean()) if fm.any() else 0.0
            sd_b = (
                math.sqrt(float(gv[fm].mean()) + float(gm[fm].var())) if fm.any() else float("nan")
            )
            row_b = C.sizing_row("B", max(sd_b, 1e-9), DEFF_FREE, float(fm.sum()), FLOOR, e_b)
            cg, cd = (
                cluster_deff(dA[dm], gid[dm]),
                cluster_deff(dA[dm], np.array([str(dates[g]) for g in gid[dm]])),
            )
            row_a = C.sizing_row(
                "A", cg["sd_row"], max(cg["deff"], cd["deff"]), float(fm.sum()), FLOOR, e_b
            )
            all_m = m & np.isfinite(dA)
            ca, cda = (
                cluster_deff(dA[all_m], gid[all_m]),
                cluster_deff(dA[all_m], np.array([str(dates[g]) for g in gid[all_m]])),
            )
            per[str(s)] = {
                "rows": int(m.sum()),
                "games": int(np.unique(gid[m]).size),
                "affected_rows": int(fm.sum()),
                "affected_share": float(fm.sum() / m.sum()),
                "affected_games": int(np.unique(gid[fm]).size),
                "expected_delta_affected": e_b,
                "expected_delta_all_rows": float(-gm[m].mean()),
                "scenarios_expected_delta_affected": {
                    nm: float(-gg[fm].mean()) if fm.any() else 0.0 for nm, gg in scen_gain.items()
                },
                "scenarios_power_A": {
                    nm: C.sizing_row(
                        nm,
                        cg["sd_row"],
                        max(cg["deff"], cd["deff"]),
                        float(fm.sum()),
                        FLOOR,
                        float(-gg[fm].mean()) if fm.any() else 0.0,
                    )["power_one_season"]
                    for nm, gg in scen_gain.items()
                },
                "expected_delta_affected_if_raw_column_were_all_new(overstated)": float(
                    -gm_r[fm].mean()
                )
                if fm.any()
                else 0.0,
                "version_B": row_b,
                "version_A": row_a,
                "deff_game_date_A": [cg["deff"], cd["deff"]],
                "all_rows_noharm_halfwidth_A": 1.96
                * ca["sd_row"]
                * math.sqrt(max(ca["deff"], cda["deff"]) / ca["n"]),
                "top25_abs_qv_slice(fixed dev cut)": _slice_block(
                    fm & (np.abs(j[f"qv_{stat}"].to_numpy().astype(float)) >= cuts[stat]),
                    scen_gain,
                    dA,
                    gid,
                    dates,
                ),
                "slices_rows": {
                    "n_out==1": int((fm & (n_out == 1)).sum()),
                    "n_out>=2": int((fm & (n_out >= 2)).sum()),
                    "starter(last_starter==1)": int(
                        (fm & (j["last_starter"].to_numpy() == 1)).sum()
                    ),
                    "bench(last_starter==0)": int((fm & (j["last_starter"].to_numpy() == 0)).sum()),
                    "first15(team_game_no<15)": int(
                        (fm & (j["team_game_no"].to_numpy() < 15)).sum()
                    ),
                    "playoffs(game_id 004)": int(
                        (fm & np.array([g.startswith("004") for g in gid])).sum()
                    ),
                },
            }
        mins = c["row_minutes"]
        per["production_n_out_rot_firing_by_realised_minutes"] = {
            lab: float(
                np.mean((cold_n_out > 0)[(np.isin(season, TEST_SEASONS)) & msk & (pos >= 0)])
            )
            for lab, msk in (
                ("<5", mins < 5),
                ("5-10", (mins >= 5) & (mins < 10)),
                ("10-20", (mins >= 10) & (mins < 20)),
                (">=20", mins >= 20),
            )
        }
        per["firing_by_realised_minutes"] = {
            lab: float(np.mean(aff[(np.isin(season, TEST_SEASONS)) & msk]))
            for lab, msk in (
                ("<5", mins < 5),
                ("5-10", (mins >= 5) & (mins < 10)),
                ("10-20", (mins >= 10) & (mins < 20)),
                (">=20", mins >= 20),
            )
        }
        res[stat] = per
        _log(t0, f"sized {stat}")
    return res


def stage_planted(
    w: dict[str, Any], rd: pl.DataFrame, repl: Any, k: Any, t0: float
) -> dict[str, Any]:
    """Real-row planted future: scramble every box row dated on or after PLANT_CUTOFF (jointly permuting stats and minutes across
    those rows), rebuild the production states and every column, and compare the columns of rows dated BEFORE the cutoff."""
    from nba.props.context_residual import _CI, _DI, MIN_PRIOR_PLAYED, ROTATION_MIN40, player_states

    box, pgs, games = w["box"], w["pgs"], w["games"]
    late = (box["game_date"] >= PLANT_CUTOFF).to_numpy()
    idx = np.flatnonzero(late)
    perm = np.random.default_rng(7).permutation(idx)
    stat_cols = ["minutes", "pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "oreb"]
    scr = box.clone()
    for cname in stat_cols:
        v = scr[cname].to_numpy().copy()
        v[idx] = v[perm]
        scr = scr.with_columns(pl.Series(cname, v))
    # pgs (production inputs) carry the same player-game stats: take them from the scrambled box
    pg2 = pgs.drop(["minutes", "pts", "reb", "ast", "fg3m"]).join(
        scr.select(["game_id", "player_id", "minutes", "pts", "reb", "ast", "fg3m"]).with_columns(
            pl.col("player_id").cast(pl.Int32)
        ),
        on=["game_id", "player_id"],
        how="inner",
    )
    played = (
        pg2.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    st = RD.state_table(played, player_states(played), _DI, _CI)
    w2 = {
        **w,
        "st": st,
        "out_rows": RD.out_rotation_rows(
            w["flagged"], w["info"], st, MIN_PRIOR_PLAYED, ROTATION_MIN40
        ),
    }
    rd2 = build_rd(w2, repl, k, b=RD.prep_box(scr), st=st)
    dd = w["feats"].select("game_id", pl.col("player_id").cast(pl.Int64), "game_date")
    j = rd.join(rd2, on=["game_id", "player_id"], suffix="_2").join(dd, on=["game_id", "player_id"])
    early = j.filter(pl.col("game_date") < PLANT_CUTOFF)
    late_j = j.filter(pl.col("game_date") >= PLANT_CUTOFF)
    worst = 0.0
    null_equal = True
    for cname in (*RD.COLUMNS, *RD.PLACEBO_COLUMNS):
        a, b2 = early[cname].to_numpy().astype(float), early[cname + "_2"].to_numpy().astype(float)
        null_equal &= bool(np.array_equal(np.isnan(a), np.isnan(b2)))
        worst = max(worst, float(np.nanmax(np.abs(a - b2), initial=0.0)))
    moved = float(np.nanmean(np.abs(late_j["qv_pts"].to_numpy() - late_j["qv_pts_2"].to_numpy())))
    _log(t0, "planted-future rebuilt")
    return {
        "cutoff": str(PLANT_CUTOFF),
        "rows_scrambled": int(idx.size),
        "earlier_rows_compared": int(early.height),
        "max_abs_diff_earlier_rows": worst,
        "null_pattern_equal": null_equal,
        "tolerance": 1e-9,
        "later_rows_mean_abs_move_qv_pts": moved,
    }


def stage_gbm(w: dict[str, Any], rd: pl.DataFrame, t0: float, boots: int = 1000) -> dict[str, Any]:
    """Held-out GBM check on DEV seasons only (nothing from 2023-2025): production-feature LightGBM mean head (production settings, seed 0)
    fitted on one set of seasons and scored on another; relative squared-error gain of adding each arm's four columns, on the AFFECTED rows of
    the test season (a reported game with >= 1 OUT rotation player and a finite share) and on the top-25% |qv| slice (fixed dev cut), game-clustered
    95% interval. Designs: D1 train 2020-21 -> test 2022; D2a 2020 -> 2021; D2b 2021 -> 2020 (pts and ast); D1 only for reb and fg3m."""
    import lightgbm as lgb
    import yaml

    from nba.props.context_residual import (
        ContextResidualConfig,
        _params,
        stat_feature_names,
        stat_frame,
    )

    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    fr = (
        w["feats"]
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .join(rd, on=["game_id", "player_id"], how="left")
    )
    cuts = stage_cuts(w, rd)
    designs = {"D1": ((2020, 2021), 2022), "D2a": ((2020,), 2021), "D2b": ((2021,), 2020)}
    res: dict[str, Any] = {}
    for stat in STATS:
        sf = stat_frame(fr, stat).filter(pl.col("season").is_in([2020, 2021, 2022]))
        base_cols = stat_feature_names(stat)
        arms = {
            "BASE": [],
            "V0": [f"v0_{stat}", f"v0d_{stat}", "share", "n_out"],
            "RD": [f"qv_{stat}", f"rd_{stat}", "share", "n_out"],
            "PQ": [f"pq_qv_{stat}", f"pq_rd_{stat}", "share", "n_out"],
        }
        res[stat] = {}
        for dname, (tr_s, te_s) in designs.items():
            if dname != "D1" and stat not in ("pts", "ast"):
                continue
            tr = sf.filter(pl.col("season").is_in(list(tr_s)))
            te = sf.filter(pl.col("season") == te_s)
            aff = ((te["n_out"] > 0) & te["share"].is_not_null()).fill_null(False).to_numpy()
            top = aff & (np.abs(te[f"qv_{stat}"].fill_null(0.0).to_numpy()) >= cuts[stat])
            gid = te["game_id"].to_numpy()
            y = te["resid"].to_numpy()
            se: dict[str, np.ndarray] = {}
            for arm, extra in arms.items():
                cols = [*base_cols, *extra]
                m = lgb.LGBMRegressor(**_params(cfg)).fit(  # type: ignore[arg-type]
                    tr.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy(),
                    tr["resid"].to_numpy(),
                )
                pred = m.predict(te.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy())
                se[arm] = (y - pred) ** 2
            rngb = np.random.default_rng(0)
            u, code = np.unique(gid, return_inverse=True)
            wts = rngb.multinomial(len(u), np.full(len(u), 1.0 / len(u)), size=boots).astype(float)

            def rel_gain(
                a: str,
                mask: np.ndarray,
                se: dict[str, np.ndarray] = se,
                code: np.ndarray = code,
                u: np.ndarray = u,
                wts: np.ndarray = wts,
            ) -> dict[str, float]:
                d = se["BASE"] - se[a]
                num_g = np.bincount(code[mask], weights=d[mask], minlength=len(u))
                den_g = np.bincount(code[mask], weights=se["BASE"][mask], minlength=len(u))
                rep = (wts @ num_g) / (wts @ den_g)
                return {
                    "gain_pct": float(100 * d[mask].sum() / se["BASE"][mask].sum()),
                    "lo": float(100 * np.percentile(rep, 2.5)),
                    "hi": float(100 * np.percentile(rep, 97.5)),
                    "n": int(mask.sum()),
                }

            res[stat][dname] = {
                "train": list(tr_s),
                "test": te_s,
                "affected": {a: rel_gain(a, aff) for a in ("V0", "RD", "PQ")},
                "top25_abs_qv": {a: rel_gain(a, top) for a in ("V0", "RD", "PQ")},
                "base_rmse_affected": float(np.sqrt(se["BASE"][aff].mean())),
                "rd_beyond_v0_pct": float(
                    100 * (se["V0"][aff].sum() - se["RD"][aff].sum()) / se["BASE"][aff].sum()
                ),
            }
            _log(t0, f"gbm {stat} {dname}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-planted", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    t0 = time.time()
    res: dict[str, Any] = {
        "measured_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "real_root": str(REAL),
    }
    res["capture_audit"] = capture_audit()
    _log(t0, "capture audit")
    w = warm_world()
    _log(t0, f"warm world: {w['feats'].height} production rows, {w['b'].height} box rows")
    pri = stage_priors(w)
    res["priors"] = pri
    repl = {int(s): v for s, v in pri["repl"].items()}
    rd = build_rd(w, repl, pri["k_values"])
    _log(t0, f"features built: {rd.height} rows")
    res["parity"] = stage_parity(w, rd)
    res["kappa"] = stage_kappa(w, rd)
    _log(t0, "kappa")
    res["slice_cuts"] = stage_cuts(w, rd)
    res["power"] = stage_power(w, rd, res["kappa"], res["slice_cuts"], t0)
    res["gbm_dev"] = stage_gbm(w, rd, t0)
    if not a.skip_planted:
        res["planted_future"] = stage_planted(w, rd, repl, pri["k_values"], t0)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=_json))
    _log(t0, f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
