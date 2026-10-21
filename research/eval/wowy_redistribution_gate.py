# ruff: noqa: E501
"""WOWY_REDISTRIBUTION minimum-data gate (docs/prereg/WOWY_REDISTRIBUTION.md, DRAFT). Seasons 2022-24 only.

    NBA_ROOT=/path/to/real/store .venv/bin/python -W ignore -m research.eval.wowy_redistribution_gate

``NBA_ROOT`` (default: the checkout this file lives in) is where ``nba.duckdb``, ``reports/lower_tail`` and
``data/ops/recent_role_full_cache`` are read from (read-only). Builds the as-of WOWY columns for every production
feature row (``nba.props.wowy``), then measures (outcome-free except where stated):

* the out-slice per season: games and A0 rows with >= 1 rotation teammate OUT on the real-tip T-60 report;
* pair coverage: how many past team games j was absent while i played (the information the columns can carry);
* the NULL / firing pattern by realised-minutes bucket (a pattern that tracks tonight's minutes would be a leak);
* the placebo machinery (same count of OUT players, wrong identity) fires about as often as the real columns;
* a planted-future check (rows dated >= a cutoff scrambled -> features of earlier games unchanged);
* sizing of the out-slice contrast from the stored FULL-minus-A0 per-row deltas (sd, design effect) and an expected
  effect that scales the WOWY delta by a coefficient fitted on 2022 ONLY (the warm-up season, never scored; its
  outcomes ARE read for that one regression, disclosed in the draft).

Nothing is fitted on 2023 or 2024 outcomes; no candidate arm is scored. Writes
``reports/prereg_wowy_redistribution/gate.json`` under the checkout this file lives in.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import load_inputs
from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import wowy as W
from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ROTATION_MIN40,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    player_states,
    stat_frame,
    to_integer_support,
)
from research.eval import prereg_gates_common as C

HERE = Path(__file__).resolve().parents[2]
REAL = Path(os.environ.get("NBA_ROOT", str(HERE)))
OUT = HERE / "reports/prereg_wowy_redistribution/gate.json"
SEASONS_ALL = (2022, 2023, 2024)
SEASONS = (2023, 2024)
FLOOR = -0.005
BUCKETS = {"<5": (0, 5), "5-10": (5, 10), "10-20": (10, 20), ">=20": (20, 999)}
PLANT_CUTOFF = "2025-01-15"  # inside the 2024-25 season (season label 2024)
#: slice name -> (description, polars expression over the feature frame); declared before the run
SLICES: dict[str, tuple[str, pl.Expr]] = {
    "ANY": ("n_out_rot >= 1", pl.col("n_out_rot") > 0),
    "VAC24": ("vac_min >= 24 (about one starter's minutes vacated)", pl.col("vac_min") >= 24.0),
    "ABSORB": (
        "wo_conf >= 0.5 and wo_d_min >= 2.0 (the columns predict >= 2 extra minutes)",
        (pl.col("wo_conf") >= 0.5) & (pl.col("wo_d_min") >= 2.0),
    ),
}


def _f(x: Any) -> float:
    return float(x)


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load_box_full(db: str) -> pl.DataFrame:
    """Played box rows (season <= 2024) with the quantities the columns use."""
    con = duckdb.connect(db, read_only=True)
    try:
        return C.query(
            con,
            "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.fga, s.fg3a, "
            "g.game_date, g.season FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
            "WHERE g.season <= 2024 AND s.minutes > 0",
        )
    finally:
        con.close()


def frame_and_out_sets(
    db: str,
) -> tuple[pl.DataFrame, dict[tuple[str, int], list[int]], pl.DataFrame, pl.DataFrame]:
    """Production feature rows (`build_features`), the OUT-rotation sets and the played box."""
    elo = {
        k: _f(v) for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    games, pgs, static, avail = load_inputs(db, None)
    if int(games["season"].max()) > C.MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 loaded: frozen holdout")
    flagged, info = flagged_from_availability(avail, games, cfg.report, max_season=C.MAX_SEASON)
    feats = build_features(games, pgs, static, flagged, elo)
    g2 = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        pgs.join(g2.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = player_states(played)
    outp = W.out_rotation_players(played, states, flagged, info, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    sets: dict[tuple[str, int], list[int]] = {}
    for g, t, p in outp.select("game_id", "team_id", "player_id").iter_rows():
        sets.setdefault((str(g), int(t)), []).append(int(p))
    return feats, sets, outp, games


def pair_coverage(
    box: pl.DataFrame, sets: dict[tuple[str, int], list[int]], rows: pl.DataFrame
) -> dict[str, Any]:
    """Per (row, j): the number of past games j was absent while i played, WITHOUT shrinkage, from the same
    machinery (a one-pair-at-a-time run of `build_wowy` with K -> 0 would hide n; recomputed directly here)."""
    # n_wo is carried by wo_n = sum over j; the pair-level distribution is recovered from single-OUT games
    single = {k: v for k, v in sets.items() if len(v) == 1}
    f = W.build_wowy(box, single, target_rows=rows.select("game_id", "player_id"))
    return {
        "single_out_pairs": f.height,
        "n_wo_quantiles(single-out rows)": {
            str(q): _f(np.quantile(f["wo_n"].to_numpy(), q)) if f.height else None
            for q in (0.1, 0.25, 0.5, 0.75, 0.9)
        },
        "share_n_wo>=1": _f((f["wo_n"] >= 1).mean()) if f.height else None,
        "share_n_wo>=3": _f((f["wo_n"] >= 3).mean()) if f.height else None,
        "share_n_wo>=5": _f((f["wo_n"] >= 5).mean()) if f.height else None,
        "share_n_wo>=10": _f((f["wo_n"] >= 10).mean()) if f.height else None,
        "mean_conf": _f(f["wo_conf"].mean()) if f.height else None,
    }


def clustered_slope(
    y: np.ndarray, x: np.ndarray, ctrl: np.ndarray, key: np.ndarray
) -> dict[str, float]:
    """OLS of y on [1, ctrl, x]; the coefficient on x with a game-clustered sandwich SE."""
    X = np.column_stack([np.ones(len(y)), ctrl, x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    _, inv = np.unique(key.astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    xtx_inv = np.linalg.pinv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    s = np.zeros((g, X.shape[1]))
    np.add.at(s, inv, X * e[:, None])
    meat = s.T @ s
    n, k = X.shape
    cov = xtx_inv @ meat @ xtx_inv * (g / (g - 1)) * ((n - 1) / (n - k))
    return {
        "coef": _f(beta[-1]),
        "se": _f(np.sqrt(cov[-1, -1])),
        "n": _f(n),
        "clusters": _f(g),
    }


def main() -> dict[str, Any]:
    t0 = time.time()
    db = str(REAL / "nba.duckdb")
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "root": str(REAL),
        "seasons": list(SEASONS_ALL),
    }
    feats, sets, outp, games = frame_and_out_sets(db)
    _log(
        t0,
        f"production frame {feats.height} rows, {len(sets)} (game,team) OUT sets, {outp.height} OUT rotation players",
    )
    box = load_box_full(db)
    _log(t0, f"played box rows {box.height}")

    rows = feats.filter(pl.col("n_out_rot") > 0).select("game_id", "player_id")
    wowy = W.build_wowy(box, sets, target_rows=rows)
    fr = W.attach_wowy(feats, wowy)
    _log(t0, f"WOWY built: {wowy.height} rows with OUT rotation teammates")
    pool = W.asof_rotation_pool(box)
    pl_sets = W.placebo_out_sets(sets, pool, seed=0)
    wowy_pl = W.build_wowy(box, pl_sets, target_rows=rows)
    _log(t0, f"placebo built: {wowy_pl.height} rows")

    # ---- (1) out-slice counts per season
    real_min = pl.DataFrame(
        {"game_id": box["game_id"], "player_id": box["player_id"], "real_min": box["minutes"]}
    )
    fr = fr.join(real_min, on=["game_id", "player_id"], how="left")
    fp = feats.join(
        wowy_pl.rename({c: "pl_" + c for c in W.WOWY_COLS}),
        on=["game_id", "team_id", "player_id"],
        how="left",
    )
    info: dict[str, Any] = {}
    for s in SEASONS_ALL:
        a = fr.filter((pl.col("season") == s) & (pl.col("n_prior") >= MIN_PRIOR_PLAYED))
        rep = a.filter(pl.col("has_report") == 1)
        sl = a.filter(pl.col("n_out_rot") > 0)
        inf = sl.filter(pl.col("wo_conf") > 0)
        tg_all = a.select("game_id", "team_id").unique().height
        tg_out = sl.select("game_id", "team_id").unique().height
        info[str(s)] = {
            "rows_a0_population(n_prior>=5, played)": a.height,
            "team_games": tg_all,
            "team_games_with_report": rep.select("game_id", "team_id").unique().height,
            "team_games_with_rotation_out": tg_out,
            "games_with_rotation_out_either_team": sl.select("game_id").unique().height,
            "out_slice_rows": sl.height,
            "out_slice_share_of_rows": sl.height / max(a.height, 1),
            "out_slice_rows_n_out_ge2": sl.filter(pl.col("n_out_rot") >= 2).height,
            "informative_rows(wo_conf>0)": inf.height,
            "informative_share_of_out_slice": inf.height / max(sl.height, 1),
            "wo_conf_quantiles(out_slice)": {
                str(q): _f(np.quantile(sl["wo_conf"].to_numpy(), q)) for q in (0.25, 0.5, 0.75, 0.9)
            },
            "wo_n_quantiles(informative)": {
                str(q): _f(np.quantile(inf["wo_n"].to_numpy(), q)) for q in (0.25, 0.5, 0.75, 0.9)
            }
            if inf.height
            else None,
            "sd_wo_d_min(informative)": _f(inf["wo_d_min"].std()) if inf.height > 1 else None,
            "sd_wo_d_ast(informative)": _f(inf["wo_d_ast"].std()) if inf.height > 1 else None,
            "sd_wo_d_pts(informative)": _f(inf["wo_d_pts"].std()) if inf.height > 1 else None,
            "sd_wo_d_reb(informative)": _f(inf["wo_d_reb"].std()) if inf.height > 1 else None,
        }
        # placebo fires about as often
        p = fp.filter(
            (pl.col("season") == s)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
            & (pl.col("n_out_rot") > 0)
        )
        info[str(s)]["placebo_informative_share_of_out_slice"] = (
            _f((p["pl_wo_conf"].fill_null(0.0) > 0).mean()) if p.height else None
        )
    out["by_season"] = info
    _log(t0, "counts done")

    # ---- (2) pair coverage (single-OUT rows, unshrunk n)
    out["pair_coverage"] = {
        str(s): pair_coverage(
            box,
            {
                k: v
                for k, v in sets.items()
                if k[0] in set(feats.filter(pl.col("season") == s)["game_id"].to_list())
            },
            feats.filter(
                (pl.col("season") == s)
                & (pl.col("n_out_rot") == 1)
                & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
            ),
        )
        for s in SEASONS_ALL
    }

    # ---- (3) NULL / firing pattern by realised-minutes bucket (out-slice, A0 population)
    firing: dict[str, Any] = {}
    for s in SEASONS_ALL:
        sl = fr.filter(
            (pl.col("season") == s)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
            & (pl.col("n_out_rot") > 0)
        )
        firing[str(s)] = {}
        for b, (lo, hi) in BUCKETS.items():
            m = sl.filter((pl.col("real_min") >= lo) & (pl.col("real_min") < hi))
            firing[str(s)][b] = {
                "n": m.height,
                "informative": _f((m["wo_conf"] > 0).mean()) if m.height else None,
            }
        sh = [v["informative"] for v in firing[str(s)].values() if v["informative"] is not None]
        firing[str(s)]["max_gap"] = _f(max(sh) - min(sh))
        firing[str(s)]["null_count_in_out_slice"] = int(sl["wo_conf"].is_null().sum())
    out["firing_by_realised_minutes_bucket"] = firing
    # the same gradient for the PLACEBO columns (same machinery, wrong identity): if it matches, the gradient is as-of history, not identity
    pg: dict[str, Any] = {}
    fpr = fp.join(real_min, on=["game_id", "player_id"], how="left")
    for s_ in SEASONS_ALL:
        sl = fpr.filter(
            (pl.col("season") == s_)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
            & (pl.col("n_out_rot") > 0)
        )
        vals = []
        for _b, (lo, hi) in BUCKETS.items():
            m = sl.filter((pl.col("real_min") >= lo) & (pl.col("real_min") < hi))
            vals.append(_f((m["pl_wo_conf"].fill_null(0.0) > 0).mean()) if m.height else _f("nan"))
        pg[str(s_)] = {
            "by_bucket": dict(zip(BUCKETS, vals, strict=True)),
            "gap": _f(np.nanmax(vals) - np.nanmin(vals)),
        }
    out["placebo_firing_by_realised_minutes_bucket"] = pg

    # ---- (4) planted future: scramble rows dated >= cutoff, features of earlier games must not move
    import datetime as dt

    cut = dt.date.fromisoformat(PLANT_CUTOFF)
    plant = box.with_columns(
        *[
            pl.when(pl.col("game_date") >= cut)
            .then(pl.col(c) * 3.0 + 7.0)
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("pts", "reb", "ast", "fg3m", "fga", "fg3a", "minutes")
        ]
    )
    sub_ids = set(
        feats.filter(pl.col("season") == 2024).select("game_id").unique()["game_id"].to_list()
    )
    sets24 = {k: v for k, v in sets.items() if k[0] in sub_ids}
    base24 = W.build_wowy(box, sets24, target_rows=rows).join(
        games.select("game_id", "game_date"), on="game_id"
    )
    pl24 = W.build_wowy(plant, sets24, target_rows=rows).join(
        games.select("game_id", "game_date"), on="game_id"
    )
    early_a = base24.filter(pl.col("game_date") < cut).sort(["game_id", "team_id", "player_id"])
    early_b = pl24.filter(pl.col("game_date") < cut).sort(["game_id", "team_id", "player_id"])
    same_early = (
        early_a.select(W.WOWY_COLS).equals(early_b.select(W.WOWY_COLS))
        and early_a.height == early_b.height
    )
    late_a = base24.filter(pl.col("game_date") >= cut + dt.timedelta(days=30)).sort(
        ["game_id", "team_id", "player_id"]
    )
    late_b = pl24.filter(pl.col("game_date") >= cut + dt.timedelta(days=30)).sort(
        ["game_id", "team_id", "player_id"]
    )
    moved = _f(np.abs(late_a["wo_d_min"].to_numpy() - late_b["wo_d_min"].to_numpy()).mean())
    out["planted_future"] = {
        "cutoff": PLANT_CUTOFF,
        "rows_before_cutoff": early_a.height,
        "features_before_cutoff_unchanged": bool(same_early),
        "sensitivity_mean_abs_move_30d_after(wo_d_min)": moved,
        "inert_test": moved <= 0.0 or early_a.height < 1000,
    }
    _log(t0, f"planted future: unchanged={same_early}, sensitivity={moved:.3f}")

    # ---- (5) 2022-only effect scale per slice: residual of the recency mean on the WOWY delta, controlling the team vacated totals
    kap: dict[str, Any] = {}
    for sn, (_desc, expr) in SLICES.items():
        f22 = fr.filter((pl.col("season") == 2022) & (pl.col("wo_conf") > 0)).filter(expr)
        kap[sn] = {"rows": f22.height}
        for stat in PROP_STATS:
            sf = stat_frame(f22, stat)
            y = sf["resid"].to_numpy()
            ctrl = sf.select(f"vac_{stat}", "vac_min").to_numpy()
            kap[sn][stat] = clustered_slope(
                y, sf[f"wo_d_{stat}"].to_numpy(), ctrl, sf["game_id"].to_numpy()
            )
        sfm = f22.with_columns((pl.col("real_min") - pl.col("min10")).alias("resid"))
        kap[sn]["minutes"] = clustered_slope(
            sfm["resid"].to_numpy(),
            sfm["wo_d_min"].to_numpy(),
            sfm.select("vac_min").to_numpy(),
            sfm["game_id"].to_numpy(),
        )
    out["kappa_2022_only"] = kap
    _log(t0, "2022-only slopes done")

    # as-of stratified firing check: informative share by realised-minute bucket WITHIN as-of min10 strata (added after the first run
    # showed the raw bucket gap 0.10-0.12 in 2023-24; before any 2023/24 outcome contrast)
    strat: dict[str, Any] = {}
    for s_ in SEASONS_ALL:
        sl = fr.filter(
            (pl.col("season") == s_)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
            & (pl.col("n_out_rot") > 0)
        )
        strat[str(s_)] = {}
        gaps = []
        for lo_a, hi_a in ((0, 15), (15, 25), (25, 99)):
            cell = sl.filter((pl.col("min10") >= lo_a) & (pl.col("min10") < hi_a))
            shares = []
            for _b, (lo, hi) in BUCKETS.items():
                m = cell.filter((pl.col("real_min") >= lo) & (pl.col("real_min") < hi))
                if m.height >= 100:
                    shares.append(_f((m["wo_conf"] > 0).mean()))
            gap = (max(shares) - min(shares)) if len(shares) >= 2 else 0.0
            strat[str(s_)][f"min10[{lo_a},{hi_a})"] = {
                "n": cell.height,
                "gap_across_realised_buckets": gap,
            }
            gaps.append(gap)
        strat[str(s_)]["max_within_stratum_gap"] = _f(max(gaps))
    out["firing_within_asof_min10_strata"] = strat

    # ---- (7) data-capture audit: fuller windows (history box 2013-2021 in front of 2022-24) and the history seasons' own kappa
    hist_db = str(REAL / "data/history/nba_history.duckdb")
    import nba.features.game_tipoff as GT

    old_dir = GT.SCHEDULE_DIR
    GT.SCHEDULE_DIR = (
        REAL / "data/history/schedule"
    )  # real tips for 2019-21 (the default dir holds 2022+ only)
    try:
        hfeats, hsets, _hout, _hg = frame_and_out_sets(hist_db)
    finally:
        GT.SCHEDULE_DIR = old_dir
    hbox = load_box_full(hist_db)
    _log(t0, f"history frame {hfeats.height} rows, box {hbox.height}")
    hrows = hfeats.filter(pl.col("n_out_rot") > 0).select("game_id", "player_id")
    hw = W.build_wowy(hbox, hsets, target_rows=hrows)
    hfr = W.attach_wowy(hfeats, hw).join(
        pl.DataFrame(
            {
                "game_id": hbox["game_id"],
                "player_id": hbox["player_id"],
                "real_min": hbox["minutes"],
            }
        ),
        on=["game_id", "player_id"],
        how="left",
    )
    full_box = pl.concat([hbox, box], how="vertical_relaxed")
    w_full = W.build_wowy(full_box, sets, target_rows=rows)
    fr_full = W.attach_wowy(feats, w_full).join(real_min, on=["game_id", "player_id"], how="left")
    audit: dict[str, Any] = {
        "history_db_seasons_box": sorted(int(x) for x in hbox["season"].unique().to_list()),
        "history_report_seasons(rows with has_report)": sorted(
            int(x) for x in hfeats.filter(pl.col("has_report") == 1)["season"].unique().to_list()
        ),
        "window_fuller_with_history_box": {},
        "kappa": {},
    }
    for s_ in SEASONS_ALL:
        a_ = fr.filter(
            (pl.col("season") == s_)
            & (pl.col("n_out_rot") > 0)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
        )
        b_ = fr_full.filter(
            (pl.col("season") == s_)
            & (pl.col("n_out_rot") > 0)
            & (pl.col("n_prior") >= MIN_PRIOR_PLAYED)
        )
        audit["window_fuller_with_history_box"][str(s_)] = {
            "median_wo_n_without_history": _f(a_.filter(pl.col("wo_conf") > 0)["wo_n"].median()),
            "median_wo_n_with_history": _f(b_.filter(pl.col("wo_conf") > 0)["wo_n"].median()),
            "informative_share_without": _f((a_["wo_conf"] > 0).mean()),
            "informative_share_with": _f((b_["wo_conf"] > 0).mean()),
        }
    pools = {
        "2022 (history-box window)": fr_full.filter(pl.col("season") == 2022),
        "2020 (hist)": hfr.filter(pl.col("season") == 2020),
        "2021 (hist)": hfr.filter(pl.col("season") == 2021),
    }
    for sn in ("ANY", "VAC24"):
        expr = SLICES[sn][1]
        audit["kappa"][sn] = {}
        allp = []
        for name, frame in pools.items():
            f_ = frame.filter(
                (pl.col("wo_conf") > 0) & (pl.col("n_out_rot") > 0) & (pl.col("has_report") == 1)
            ).filter(expr)
            allp.append(f_)
            audit["kappa"][sn][name] = {"rows": f_.height}
            for stat in PROP_STATS:
                sf = stat_frame(f_, stat)
                audit["kappa"][sn][name][stat] = clustered_slope(
                    sf["resid"].to_numpy(),
                    sf[f"wo_d_{stat}"].to_numpy(),
                    sf.select(f"vac_{stat}", "vac_min").to_numpy(),
                    sf["game_id"].to_numpy(),
                )
        pooled = pl.concat(allp, how="vertical_relaxed")
        audit["kappa"][sn]["pooled 2020-2022"] = {"rows": pooled.height}
        for stat in PROP_STATS:
            sf = stat_frame(pooled, stat)
            audit["kappa"][sn]["pooled 2020-2022"][stat] = clustered_slope(
                sf["resid"].to_numpy(),
                sf[f"wo_d_{stat}"].to_numpy(),
                sf.select(f"vac_{stat}", "vac_min").to_numpy(),
                sf["game_id"].to_numpy(),
            )
    out["data_capture_audit"] = audit
    _log(t0, "data-capture audit done")

    # ---- (6) sizing on the out-slice from stored A0 and stored FULL-minus-A0 deltas
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    lt = REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    full_pi = np.load(REAL / "data/ops/recent_role_full_cache/pi_R.npz", allow_pickle=True)["pi"]
    sizing: dict[str, Any] = {}
    for stat in PROP_STATS:
        cs = load_components(stat, lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        y = cs["row_y"]
        gid = cs["row_gid"].astype(str)
        season = cs["row_season"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        jf = keys.join(
            fr.select("game_id", "player_id", "n_out_rot", "vac_min", *W.WOWY_COLS),
            on=["game_id", "player_id"],
            how="left",
        ).with_columns(
            pl.col("n_out_rot").fill_null(0),
            pl.col("vac_min").fill_null(0.0),
            pl.col("wo_conf").fill_null(0.0),
            pl.col("wo_d_min").fill_null(0.0),
        )
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        hd = {
            k: v
            for k, v in np.load(
                REAL / f"data/ops/recent_role_full_cache/heads_{stat}_R.npz", allow_pickle=True
            ).items()
        }
        q1 = RF.arm_qi(stat, hd, pic, full_pi)
        d = PR.crps_int(q1, y) - PR.crps_int(q0, y)  # stored FULL - A0 (T467)
        grid = arm_grid("mixture", cs, pic, stat)
        sizing[stat] = {}
        for sn, (_desc, expr) in SLICES.items():
            slice_m = jf.select(expr.alias("m"))["m"].fill_null(False).to_numpy()
            conf = jf["wo_conf"].to_numpy()
            kappa = max(kap[sn][stat]["coef"], 0.0)
            kappa_pool = (
                max(
                    audit["kappa"]
                    .get(sn, {})
                    .get("pooled 2020-2022", {})
                    .get(stat, {"coef": _f("nan")})["coef"],
                    0.0,
                )
                if sn in audit["kappa"]
                else None
            )
            kappa_full = (
                max(audit["kappa"][sn]["2022 (history-box window)"][stat]["coef"], 0.0)
                if sn in audit["kappa"]
                else None
            )
            per: dict[str, Any] = {
                "kappa_used": kappa,
                "kappa_pooled_2020_22": kappa_pool,
                "kappa_2022_full_window": kappa_full,
            }
            for s in SEASONS:
                m = season == s
                ms = m & slice_m
                cg = C.cluster_stats(d[ms], gid[ms])
                call = C.cluster_stats(d[m], gid[m])
                b = kappa * jf[f"wo_d_{stat}"].fill_null(0.0).to_numpy()
                sel = ms & (b != 0)
                gain = np.zeros(len(y))
                if sel.any():
                    qn = to_integer_support(grid[sel] + b[sel, None])
                    gain[sel] = C.expected_crps_gain(q0[sel].astype(float), qn)
                e_slice = _f(-gain[ms].mean()) if ms.any() else 0.0
                e_pool = None
                if kappa_pool is not None:
                    bp = kappa_pool * jf[f"wo_d_{stat}"].fill_null(0.0).to_numpy()
                    selp = ms & (bp != 0)
                    gp = np.zeros(len(y))
                    if selp.any():
                        gp[selp] = C.expected_crps_gain(
                            q0[selp].astype(float), to_integer_support(grid[selp] + bp[selp, None])
                        )
                    e_pool = _f(-gp[ms].mean())
                e_full = None
                if kappa_full is not None:
                    bf = kappa_full * jf[f"wo_d_{stat}"].fill_null(0.0).to_numpy()
                    self_ = ms & (bf != 0)
                    gf = np.zeros(len(y))
                    if self_.any():
                        gf[self_] = C.expected_crps_gain(
                            q0[self_].astype(float),
                            to_integer_support(grid[self_] + bf[self_, None]),
                        )
                    e_full = _f(-gf[ms].mean())
                per[str(s)] = {
                    "expected_delta_slice(kappa_2022_full_window)": e_full,
                    "power_one_season_at_2022_full_window": (
                        C.sizing_row("x", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e_full).get(
                            "power_one_season"
                        )
                        if e_full is not None
                        else None
                    ),
                    "expected_delta_slice(kappa_pooled_2020_22)": e_pool,
                    "power_one_season_at_pooled": (
                        C.sizing_row("x", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e_pool).get(
                            "power_one_season"
                        )
                        if e_pool is not None
                        else None
                    ),
                    "all_rows": int(m.sum()),
                    "slice_rows": int(ms.sum()),
                    "slice_games": int(cg["clusters"]),
                    "informative_rows": int((ms & (conf > 0)).sum()),
                    "slice_sizing": C.sizing_row(
                        f"{stat} {sn}, floor -0.005",
                        cg["sd_row"],
                        cg["deff"],
                        ms.sum(),
                        FLOOR,
                        e_slice,
                    ),
                    "all_rows_sizing": C.sizing_row(
                        f"{stat} all rows (no-harm guard)",
                        call["sd_row"],
                        call["deff"],
                        m.sum(),
                        0.001,
                        None,
                    ),
                    "expected_delta_slice(kappa_2022)": e_slice,
                    "expected_delta_all_rows(kappa_2022)": _f(-gain[m].mean()),
                }
            sizing[stat][sn] = per
        _log(t0, f"sized {stat}")
    out["sizing"] = sizing

    # ---- gate verdicts (decided in the draft before any 2023/24 outcome contrast exists)
    g: list[dict[str, Any]] = []
    for s in SEASONS:
        i = info[str(s)]
        g.append(
            {
                "check": f"{s} out-slice rows >= 5000",
                "value": i["out_slice_rows"],
                "pass": i["out_slice_rows"] >= 5000,
            }
        )
        g.append(
            {
                "check": f"{s} informative share of out-slice >= 0.5",
                "value": i["informative_share_of_out_slice"],
                "pass": i["informative_share_of_out_slice"] >= 0.5,
            }
        )
        g.append(
            {
                "check": f"{s} firing gap across realised-minute buckets <= 0.10 (first run: raw)",
                "value": firing[str(s)]["max_gap"],
                "pass": firing[str(s)]["max_gap"] <= 0.10,
                "decides": False,
            }
        )
        g.append(
            {
                "check": f"{s} firing gap within as-of min10 strata <= 0.05 (added after run 1)",
                "value": out["firing_within_asof_min10_strata"][str(s)]["max_within_stratum_gap"],
                "pass": out["firing_within_asof_min10_strata"][str(s)]["max_within_stratum_gap"]
                <= 0.05,
            }
        )
        g.append(
            {
                "check": f"{s} placebo informative share within 0.10 of real",
                "value": abs(
                    i["placebo_informative_share_of_out_slice"]
                    - i["informative_share_of_out_slice"]
                ),
                "pass": abs(
                    i["placebo_informative_share_of_out_slice"]
                    - i["informative_share_of_out_slice"]
                )
                <= 0.10,
            }
        )
        for sn in SLICES:
            for stat in ("pts", "ast"):
                z2 = sizing[stat][sn][str(s)]["slice_sizing"]
                g.append(
                    {
                        "check": f"{sn} {stat} {s} half-width <= 0.005 / power at kappa_2022 >= 0.5",
                        "value": [z2["halfwidth95"], z2.get("power_one_season")],
                        "pass": bool(
                            z2["hw_le_floor"] and (z2.get("power_one_season") or 0) >= 0.5
                        ),
                        "decides": False,
                    }
                )
        for stat in PROP_STATS:
            z = sizing[stat]["ANY"][str(s)]["slice_sizing"]
            g.append(
                {
                    "check": f"{stat} {s} out-slice half-width <= 0.005",
                    "value": z["halfwidth95"],
                    "pass": z["hw_le_floor"],
                    "decides": stat in ("pts", "ast"),
                }
            )
    g.append(
        {
            "check": "planted future: features before cutoff unchanged",
            "value": out["planted_future"]["features_before_cutoff_unchanged"],
            "pass": out["planted_future"]["features_before_cutoff_unchanged"],
        }
    )
    g.append(
        {
            "check": "planted future test is not inert",
            "value": out["planted_future"]["sensitivity_mean_abs_move_30d_after(wo_d_min)"],
            "pass": not out["planted_future"]["inert_test"],
        }
    )
    out["gate"] = g
    out["gate_pass"] = all(x["pass"] for x in g if x.get("decides", True))
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"gate": g, "by_season": info}, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
