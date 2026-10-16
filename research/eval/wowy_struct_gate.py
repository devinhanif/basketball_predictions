# ruff: noqa: E501
"""WOWY_REDISTRIBUTION follow-up 2 (STRUCT) gate: structural allocation of vacated production (DRAFT rule; gate only).

    cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -W ignore -m research.eval.wowy_struct_gate

Feature ``struct_*`` of ``nba/props/struct_alloc.py``: tonight's OUT rotation players' as-of per-game stat (per-minute
rate x expected minutes) is handed out to the remaining as-of rotation pool in proportion to the same quantity (version
RATE) or, for pts and fg3m, to a usage proxy (version USAGE); minutes are handed out pro rata (role) or with weight 2 for
the OUT player's position group (pos). The version is chosen on 2020-22 only (larger clustered t of the coefficient).

Outcomes read: seasons 2020, 2021 (history) and 2022 only, for the coefficient kappa (residual of the stat vs its recency
mean on the feature, controlling vac_<stat> and vac_min, game-clustered). Sizing uses stored A0 quantile grids (forecasts
only) for the expected gain, version B (outcome-free sd, design effect 1.1) as primary and version A (stored FULL-minus-A0
2023/24 deltas, second moments only) as secondary. No 2023, 2024 or 2025 outcome is related to any STRUCT column.
Adds the key ``struct`` to ``reports/prereg_wowy_redistribution/gate.json``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import (
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_redistribution_slices as SL

FLOOR = -0.005
QUANT = {"top25": 0.75, "top10": 0.90}


def _f(x: Any) -> float:
    return float(x)


def load_box_state(db: str) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return C.query(
            con,
            "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.fga, s.fta, s.tov, "
            "s.fg3a, CAST(s.starter AS DOUBLE) AS starter, t.touches, t.passes, t.secondary_assists AS sec_ast, "
            "t.free_throw_assists AS ft_ast, t.rebound_chances_total AS rbc, "
            "t.uncontested_field_goals_attempted AS unc, t.contested_field_goals_attempted AS con, "
            "g.game_date, g.season FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
            "LEFT JOIN player_game_tracking t ON t.game_id = s.game_id AND t.player_id = s.player_id "
            "WHERE g.season <= 2024 AND s.minutes > 0",
        ).with_columns(pl.col("game_date").cast(pl.Date))
    finally:
        con.close()


def load_positions(db: str) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute("SELECT player_id, position FROM players_static").pl()
    finally:
        con.close()


def chosen_col(stat: str, version: dict[str, str]) -> str:
    return version.get(stat, f"struct_d_{stat}")


def slice_masks_struct(df: pl.DataFrame, col: str, leader: str) -> dict[str, np.ndarray]:
    """Masks over ``df`` (one season; rows with has_report and n_out_rot > 0 considered): VAC24, top 25% / 10% of |col| within VAC24, leader out."""
    base = ((df["vac_min"] >= 24.0) & (df["has_report"] == 1)).fill_null(False).to_numpy()
    a = np.abs(df[col].fill_null(0.0).to_numpy())
    out = {"VAC24": base}
    for name, q in QUANT.items():
        cut = np.quantile(a[base], q) if base.any() else np.inf
        out[name] = base & (a >= cut) & (a > 0)
    out["leader_out"] = (
        (df[leader] == 1).fill_null(False).to_numpy()
        & (df["n_out_rot"] > 0).fill_null(False).to_numpy()
        & (df["has_report"] == 1).fill_null(False).to_numpy()
    )
    return out


def slope_on(
    sub: pl.DataFrame, stat: str, col: str, extra: bool = False
) -> dict[str, float] | None:
    """Coefficient of ``col`` on the residual vs the recency mean, controlling vac_<stat> and vac_min; ``extra`` adds the
    player's own as-of per-game stat and the product of his per-minute rate and vac_min (production's own ingredients)."""
    sf = stat_frame(sub, stat)
    if sf.height < 200:
        return None
    ctrl_cols = [pl.col(f"vac_{stat}"), pl.col("vac_min")]
    if extra:
        ctrl_cols += [
            pl.col(f"m10_{stat}"),
            (pl.col(f"rate_{stat}") * pl.col("vac_min")).alias("_rv"),
        ]
    ctrl = sf.select(ctrl_cols).to_numpy()
    ok = (
        np.isfinite(ctrl).all(axis=1)
        & np.isfinite(sf["resid"].to_numpy())
        & np.isfinite(sf[col].to_numpy())
    )
    return G.clustered_slope(
        sf["resid"].to_numpy()[ok],
        sf[col].to_numpy()[ok],
        ctrl[ok],
        sf["game_id"].to_numpy()[ok],
    )


def slope_ctrl(
    sub: pl.DataFrame, stat: str, col: str, extra_cols: tuple[str, ...] = ()
) -> dict[str, float] | None:
    """Coefficient of ``col`` (residual vs the recency mean) controlling vac_<stat>, vac_min and ``extra_cols``."""
    sf = stat_frame(sub, stat)
    if sf.height < 200:
        return None
    ctrl = sf.select(
        [pl.col(f"vac_{stat}"), pl.col("vac_min"), *[pl.col(c) for c in extra_cols]]
    ).to_numpy()
    ok = (
        np.isfinite(ctrl).all(axis=1)
        & np.isfinite(sf["resid"].to_numpy())
        & np.isfinite(sf[col].to_numpy())
    )
    return G.clustered_slope(
        sf["resid"].to_numpy()[ok],
        sf[col].to_numpy()[ok],
        ctrl[ok],
        sf["game_id"].to_numpy()[ok],
    )


# --------------------------------------------------------------------------- warm production frame


def warm_frame(db: str, hdb: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Production feature rows built over history + nba.duckdb in ONE ``build_features`` (every season warm), the OUT
    rotation players, and games. Real tips from both schedule directories. Read-only; seasons <= 2024."""
    from pathlib import Path

    import yaml

    import nba.features.game_tipoff as GT
    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import (
        MIN_PRIOR_PLAYED,
        ROTATION_MIN40,
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
        player_states,
    )

    elo = {
        k: _f(v) for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    parts = [load_inputs(d, None) for d in (hdb, db)]
    games = pl.concat([p[0] for p in parts], how="vertical_relaxed")
    if int(games["season"].max()) > C.MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 loaded: frozen holdout")
    pgs = pl.concat([p[1] for p in parts], how="vertical_relaxed")
    static = pl.concat([p[2] for p in parts], how="vertical_relaxed").unique(
        "player_id", keep="last"
    )
    avail = pl.concat([p[3] for p in parts], how="vertical_relaxed")
    orig = GT.build_game_tipoff

    def both(schedule_dir: Any = None) -> pl.DataFrame:
        return pl.concat(
            [orig(G.REAL / "data/schedule"), orig(G.REAL / "data/history/schedule")],
            how="vertical_relaxed",
        ).unique("game_id", keep="last")

    GT.build_game_tipoff = both  # type: ignore[assignment]
    try:
        flagged, info = flagged_from_availability(avail, games, cfg.report)
    finally:
        GT.build_game_tipoff = orig
    feats = build_features(games, pgs, static, flagged, elo)
    g2 = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        pgs.join(g2.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = player_states(played)
    outp = W.out_rotation_players(played, states, flagged, info, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    return feats, outp, g2


# --------------------------------------------------------------------------- GBM held-out arms


def gbm_eval(
    train: pl.DataFrame, test: pl.DataFrame, stat: str, col: str, role: str
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """Squared error of the production-feature GBM on test rows for: base, +struct, +role share, +struct+role, +struct
    permuted within player-season (train and test separately). Residual vs the recency mean; one seed, same params."""
    import lightgbm as lgb

    names = [c for c in stat_feature_names(stat) if c in train.columns]
    rng = np.random.default_rng(0)

    def prep(df: pl.DataFrame) -> pl.DataFrame:
        sf = stat_frame(df.filter(pl.col("has_report") == 1), stat)
        perm = sf[col].to_numpy().copy()
        key = (sf["player_id"].cast(pl.Utf8) + "_" + sf["season"].cast(pl.Utf8)).to_numpy()
        for k in np.unique(key):
            idx = np.flatnonzero(key == k)
            perm[idx] = perm[rng.permutation(idx)]
        return sf.with_columns(pl.Series("_perm", perm))

    tr, te = prep(train), prep(test)
    arms = {
        "base": [],
        "struct": [col],
        "role": [role],
        "struct_role": [col, role],
        "perm_ps": ["_perm"],
    }
    errs: dict[str, np.ndarray] = {}
    for arm, extra in arms.items():
        cols = [*names, *extra]
        x = tr.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()
        ok = np.isfinite(tr["resid"].to_numpy())
        m = lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.04,
            num_leaves=15,
            min_child_samples=100,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            random_state=0,
            n_jobs=2,
            verbose=-1,
        )
        m.fit(x[ok], tr["resid"].to_numpy()[ok])
        xt = te.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()
        errs[arm] = np.asarray((te["resid"].to_numpy() - m.predict(xt)) ** 2)
    return te, errs


def gbm_contrasts(
    te: pl.DataFrame, errs: dict[str, np.ndarray], mask: np.ndarray, n_boot: int = 1000
) -> dict[str, Any]:
    ok = mask & np.all([np.isfinite(e) for e in errs.values()], axis=0)
    _, inv = np.unique(te["game_id"].to_numpy()[ok], return_inverse=True)
    g = int(inv.max()) + 1
    sums = {k: np.bincount(inv, weights=e[ok], minlength=g) for k, e in errs.items()}
    draws = np.random.default_rng(0).integers(0, g, size=(n_boot, g))

    def rel(a: str, b: str) -> dict[str, Any]:
        r = 1.0 - sums[a][draws].sum(axis=1) / sums[b][draws].sum(axis=1)
        return {
            "relative_gain_pct": 100 * _f(1.0 - sums[a].sum() / sums[b].sum()),
            "ci_pct": [100 * _f(np.quantile(r, 0.025)), 100 * _f(np.quantile(r, 0.975))],
        }

    return {
        "n": int(ok.sum()),
        "games": g,
        "struct_vs_base": rel("struct", "base"),
        "role_vs_base": rel("role", "base"),
        "struct_beyond_role(struct_role_vs_role)": rel("struct_role", "role"),
        "perm_ps_vs_base": rel("perm_ps", "base"),
    }


def cell_masks(
    df: pl.DataFrame, col: str, leader: str, cuts: tuple[float, float]
) -> dict[str, np.ndarray]:
    """VAC24, leader out, and the two size slices at FIXED cut-offs (absolute |col|, set on 2020-21)."""
    base = ((df["vac_min"] >= 24.0) & (df["has_report"] == 1)).fill_null(False).to_numpy()
    a = np.abs(df[col].fill_null(0.0).to_numpy())
    return {
        "VAC24": base,
        "leader_out": (df[leader] == 1).fill_null(False).to_numpy()
        & (df["n_out_rot"] > 0).fill_null(False).to_numpy()
        & (df["has_report"] == 1).fill_null(False).to_numpy(),
        "top25_fixed": base & (a >= cuts[0]) & (a > 0),
        "top10_fixed": base & (a >= cuts[1]) & (a > 0),
    }


STATS_USED = ("pts", "ast")
SLICES_USED = ("VAC24", "leader_out", "top25_fixed", "top10_fixed")
DAYS_CUT = "2025-01-15"


DESIGNS_GATE = ("D1_train2020-21_test2022", "D2a_train2020_test2021", "D2b_train2021_test2020")


def cell_gate_rule(sizing: dict[str, Any], gbm: dict[str, Any]) -> dict[str, Any]:
    """The draft's declared cell gate (docs/prereg/WOWY_STRUCT.md, 'Gate'), computed from stored numbers only:
    (a) power_b >= 0.5 in 2023 and 2024 at the PLAIN (smaller) coefficient; (b) STRUCT beyond role share positive in D1,
    D2a and D2b and its D1 interval excludes 0; (c) the player-season-permuted arm gains less than STRUCT in all three
    designs. (Before 2026-10-14 this key held (a) at the beyond-role kappa and no (b)/(c): all 8 cells passed; that
    superseded version is kept in gate.json as ``cell_gate_beyond_role_superseded``.)"""
    verdict: dict[str, Any] = {}
    for stat in STATS_USED:
        for sn in SLICES_USED:
            p = [
                sizing[stat][str(s)].get(sn, {}).get("plain_2020_21(reference)", {}).get("power_b")
                for s in G.SEASONS
            ]
            a = bool(all(x is not None and x >= 0.5 for x in p))
            br = [gbm[stat][d][sn]["struct_beyond_role(struct_role_vs_role)"] for d in DESIGNS_GATE]
            b = bool(all(x["relative_gain_pct"] > 0 for x in br) and br[0]["ci_pct"][0] > 0.0)
            c = bool(
                all(
                    gbm[stat][d][sn]["perm_ps_vs_base"]["relative_gain_pct"]
                    < gbm[stat][d][sn]["struct_vs_base"]["relative_gain_pct"]
                    for d in DESIGNS_GATE
                )
            )
            verdict[f"{stat}|{sn}"] = {
                "a_power_b_plain_2023_2024": p,
                "a": a,
                "b_beyond_role_D1_D2a_D2b_pct": [x["relative_gain_pct"] for x in br],
                "b": b,
                "c": c,
                "pass_gate": bool(a and b and c),
            }
    return verdict


def regate(path: Any = None) -> dict[str, Any]:
    """Recompute ``struct_warm.cell_gate`` from the numbers already stored in gate.json (no data is read); the previous
    value moves to ``cell_gate_beyond_role_superseded``. Idempotent."""
    path = path or G.OUT
    gate = json.loads(path.read_text())
    sw = gate["struct_warm"]
    if "cell_gate_beyond_role_superseded" not in sw:
        sw["cell_gate_beyond_role_superseded"] = sw["cell_gate"]
    sw["cell_gate"] = cell_gate_rule(sw["sizing"], sw["gbm_warm"])
    path.write_text(json.dumps(gate, indent=1, default=float))
    return dict(sw["cell_gate"])


def main() -> dict[str, Any]:
    import datetime as dt

    t0 = time.time()
    db = str(G.REAL / "nba.duckdb")
    hdb = str(G.REAL / "data/history/nba_history.duckdb")
    feats, outp, games = warm_frame(db, hdb)
    print(f"[{time.time() - t0:5.1f}s] warm production frame {feats.height} rows", flush=True)
    box_m, box_h = load_box_state(db), load_box_state(hdb)
    full = pl.concat([box_h, box_m], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = games.select("game_id", "game_date")
    pos = pl.concat([load_positions(hdb), load_positions(db)]).unique("player_id")
    seasons = (2020, 2021, 2022, 2023, 2024)
    fe = feats.filter(pl.col("season").is_in(list(seasons)))
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    st = SA.build_struct(rows, op, pool, states, dates, pos)
    fr = SA.attach_struct(fe, st)
    print(f"[{time.time() - t0:5.1f}s] struct attached", flush=True)
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "supersedes": "key `struct_cold_superseded` (cold 2022 test frame)",
    }

    par: dict[str, Any] = {}
    for sea in (2022, 2023, 2024):
        cc = fr.filter(
            (pl.col("season") == sea) & (pl.col("n_out_rot") > 0) & (pl.col("has_report") == 1)
        )
        par[str(sea)] = {
            s_: {
                "mean_abs": _f((cc[f"V_{s_}"] - cc[f"vac_{s_}"]).abs().mean()),
                "max_abs": _f((cc[f"V_{s_}"] - cc[f"vac_{s_}"]).abs().max()),
            }
            for s_ in ("pts", "ast", "min")
        }
    out["parity_V_vs_vac_warm"] = par

    def vac_rows(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.filter(
            (pl.col("vac_min") >= 24.0) & (pl.col("has_report") == 1) & (pl.col("n_out_rot") > 0)
        )

    f20, f21, f22 = (fr.filter(pl.col("season") == y) for y in (2020, 2021, 2022))
    sel = pl.concat([vac_rows(f20), vac_rows(f21)], how="vertical_relaxed")

    vers: dict[str, Any] = {}
    for stat, cands in (("pts", ("struct_d_pts", "struct_u_pts")), ("ast", ("struct_d_ast",))):
        res = {c: slope_ctrl(sel, stat, c) for c in cands}
        vers[stat] = {
            c: ({"coef": r["coef"], "se": r["se"], "t": r["coef"] / r["se"]} if r else None)
            for c, r in res.items()
        }
    pts_t = {c: (vers["pts"][c]["t"] if vers["pts"][c] else -9.0) for c in vers["pts"]}
    chosen = {"pts": max(pts_t, key=lambda c: pts_t[c]), "ast": "struct_d_ast"}
    out["version_choice_2020_21_only"] = {
        "coefficients": vers,
        "chosen": chosen,
        "earlier_choice_2020_22_pooled(disclosed)": {"pts": "struct_u_pts"},
    }
    print(f"[{time.time() - t0:5.1f}s] versions {chosen}", flush=True)

    cuts: dict[str, tuple[float, float]] = {}
    for stat in STATS_USED:
        a = np.abs(sel[chosen[stat]].to_numpy())
        a = a[a > 0]
        cuts[stat] = (_f(np.quantile(a, 0.75)), _f(np.quantile(a, 0.90)))
    out["fixed_cutoffs_2020_21(|struct| quantiles 0.75 / 0.90)"] = cuts

    designs = {
        "D1_train2020-21_test2022": ([f20, f21], f22),
        "D2a_train2020_test2021": ([f20], f21),
        "D2b_train2021_test2020": ([f21], f20),
    }
    gb: dict[str, Any] = {}
    for stat in STATS_USED:
        col, role = chosen[stat], f"role_share_{stat}"
        gb[stat] = {}
        for dn, (trn, tst) in designs.items():
            te, errs = gbm_eval(pl.concat(trn, how="vertical_relaxed"), tst, stat, col, role)
            msd = cell_masks(te, col, f"leader_out_{stat}", cuts[stat])
            gb[stat][dn] = {sn: gbm_contrasts(te, errs, m_) for sn, m_ in msd.items()}
        print(f"[{time.time() - t0:5.1f}s] gbm {stat}", flush=True)
    out["gbm_warm"] = gb

    kap: dict[str, Any] = {}
    for stat in STATS_USED:
        col, role = chosen[stat], f"role_share_{stat}"
        kap[stat] = {}
        for label, frames in (("pooled_2020_21", [f20, f21]), ("2022_heldout", [f22])):
            parts: dict[str, list[pl.DataFrame]] = {}
            for fr_ in frames:
                for sn, m_ in cell_masks(fr_, col, f"leader_out_{stat}", cuts[stat]).items():
                    parts.setdefault(sn, []).append(fr_.filter(pl.Series(m_)))
            kap[stat][label] = {}
            for sn, lst in parts.items():
                sub = pl.concat(lst, how="vertical_relaxed")
                kap[stat][label][sn] = {
                    "rows": sub.height,
                    "plain": slope_ctrl(sub, stat, col),
                    "beyond_role": slope_ctrl(sub, stat, col, (role,)),
                }
    out["kappa"] = kap
    print(f"[{time.time() - t0:5.1f}s] kappas", flush=True)

    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    lt = G.REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    full_pi = np.load(G.REAL / "data/ops/recent_role_full_cache/pi_R.npz", allow_pickle=True)["pi"]
    fr_eval = fr.filter(pl.col("season").is_in([2023, 2024]))
    sizing: dict[str, Any] = {}
    for stat in STATS_USED:
        col = chosen[stat]
        cs = load_components(stat, lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        gid = cs["row_gid"].astype(str)
        season = cs["row_season"]
        y = cs["row_y"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        jf = keys.join(
            fr_eval.select(
                "game_id",
                "player_id",
                "vac_min",
                "n_out_rot",
                "has_report",
                col,
                f"leader_out_{stat}",
            ),
            on=["game_id", "player_id"],
            how="left",
        ).with_columns(
            pl.col("vac_min").fill_null(0.0),
            pl.col("n_out_rot").fill_null(0),
            pl.col("has_report").fill_null(0),
            pl.col(col).fill_null(0.0),
            pl.col(f"leader_out_{stat}").fill_null(0),
        )
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        hd = dict(
            np.load(
                G.REAL / f"data/ops/recent_role_full_cache/heads_{stat}_R.npz", allow_pickle=True
            ).items()
        )
        q1 = RF.arm_qi(stat, hd, pic, full_pi)
        d_ = PR.crps_int(q1, y) - PR.crps_int(
            q0, y
        )  # stored FULL - A0: second moments only (version A)
        grid = arm_grid("mixture", cs, pic, stat)
        shift = jf[col].to_numpy()
        sizing[stat] = {"feature": col}
        for s in G.SEASONS:
            in_s = season == s
            msub = cell_masks(jf.filter(pl.Series(in_s)), col, f"leader_out_{stat}", cuts[stat])
            masks = {k: np.zeros(len(y), dtype=bool) for k in msub}
            for k, v in msub.items():
                masks[k][np.flatnonzero(in_s)] = v
            seas: dict[str, Any] = {}
            for sn, ms in masks.items():
                if not ms.any():
                    continue
                cg = C.cluster_stats(d_[ms], gid[ms])
                entry: dict[str, Any] = {"rows": int(ms.sum()), "games": int(cg["clusters"])}
                for kn, kdict in (
                    ("beyond_role_2020_21(primary)", "beyond_role"),
                    ("plain_2020_21(reference)", "plain"),
                ):
                    kv = max((kap[stat]["pooled_2020_21"][sn][kdict] or {"coef": 0.0})["coef"], 0.0)
                    gm = np.zeros(len(y))
                    gv = np.zeros(len(y))
                    sel_ = ms & (shift != 0) & (kv > 0)
                    if sel_.any():
                        gm[sel_], gv[sel_] = SL.expected_gain_var(
                            q0[sel_].astype(float),
                            to_integer_support(grid[sel_] + kv * shift[sel_, None]),
                        )
                    e = -_f(gm[ms].mean())
                    sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var()))
                    rb = C.sizing_row("b", max(sd_free, 1e-9), SL.DEFF_FREE, ms.sum(), FLOOR, e)
                    ra = C.sizing_row("a", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e)
                    entry[kn] = {
                        "kappa": kv,
                        "expected_delta": e,
                        "halfwidth_b": rb["halfwidth95"],
                        "power_b": rb.get("power_one_season"),
                        "halfwidth_a": ra["halfwidth95"],
                        "power_a": ra.get("power_one_season"),
                    }
                seas[sn] = entry
            sizing[stat][str(s)] = seas
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    out["sizing"] = sizing
    verdict = cell_gate_rule(sizing, gb)
    out["cell_gate"] = verdict

    cut = dt.date.fromisoformat(DAYS_CUT)
    stat_cols = ["pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "minutes", "fg3a", "starter"]
    plant = full.with_columns(
        [
            pl.when(pl.col("game_date") >= cut)
            .then(pl.col(c).fill_null(0.0) * 3.0 + 7.0)
            .otherwise(pl.col(c))
            .alias(c)
            for c in stat_cols
        ]
    )
    early = (
        rows.join(dates, on="game_id")
        .filter(pl.col("game_date") < cut)
        .select("game_id", "team_id", "player_id")
    )
    op_e = outp.join(early.select("game_id").unique(), on="game_id", how="inner")
    plant_pool, plant_states = W.asof_rotation_pool(plant), SA.decayed_states(plant)
    keys3 = ["game_id", "team_id", "player_id"]
    a = SA.build_struct(early, op_e, pool, states, dates, pos).sort(keys3)
    b = SA.build_struct(early, op_e, plant_pool, plant_states, dates, pos).sort(keys3)
    fcols = [c for c in a.columns if c not in keys3 and not c.startswith("leader_out_")]
    maxdiff = max(_f(np.abs(a[c].to_numpy() - b[c].to_numpy()).max()) for c in fcols)
    disc = all(a[c].to_list() == b[c].to_list() for c in a.columns if c.startswith("leader_out_"))
    late = (
        rows.join(dates, on="game_id")
        .filter(pl.col("game_date") >= cut + dt.timedelta(days=30))
        .select("game_id", "team_id", "player_id")
    )
    op_l = outp.join(late.select("game_id").unique(), on="game_id", how="inner")
    la = SA.build_struct(late, op_l, pool, states, dates, pos).sort(keys3)
    lb = SA.build_struct(late, op_l, plant_pool, plant_states, dates, pos).sort(keys3)
    out["planted_future"] = {
        "note": "STRUCT inputs (states, pool) scrambled from the cutoff; OUT sets come from the report and were audited separately (docs/reviews/struct_leak_audit_2026-10-14.md)",
        "cutoff": DAYS_CUT,
        "early_rows": early.height,
        "max_abs_diff_float_columns": maxdiff,
        "discrete_equal": disc,
        "tolerance": 1e-9,
        "pass": bool(maxdiff <= 1e-9 and disc),
        "later_rows_mean_abs_move_struct_d_ast": _f(
            np.abs(la["struct_d_ast"].to_numpy() - lb["struct_d_ast"].to_numpy()).mean()
        ),
    }
    gate = json.loads(G.OUT.read_text())
    if "struct" in gate and "struct_cold_superseded" not in gate:
        gate["struct_cold_superseded"] = gate.pop("struct")
    gate["struct_warm"] = out
    G.OUT.write_text(json.dumps(gate, indent=1, default=float))
    print(
        json.dumps(
            {"versions": chosen, "cell_gate": verdict, "planted": out["planted_future"]},
            indent=1,
            default=float,
        ),
        flush=True,
    )
    return out


if __name__ == "__main__":
    import sys

    if "--regate" in sys.argv:
        print(json.dumps(regate(), indent=1, default=float))
    else:
        main()
