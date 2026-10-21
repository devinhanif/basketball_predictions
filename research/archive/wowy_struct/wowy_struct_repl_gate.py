# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""WOWY_STRUCT (revision with STRUCT_REPL) minimum-data gate, data-capture audit and power re-measure (non-negotiable 8). DRAFT rule: docs/prereg/WOWY_STRUCT.md.

    cd <worktree> && NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 \\
        /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_struct_repl_gate [--skip-planted] [--stages a,b,...]

Read-only on ``$NBA_ROOT/nba.duckdb`` and ``$NBA_ROOT/data/history/nba_history.duckdb`` (every query ``season <= 2024``; season 2025 is never
read). Outcomes read: the DEV seasons 2020, 2021 and the warm-up season 2022 only (the effect coefficient kappa and the held-out GBM designs, exactly
the STRUCT gate's convention). The 2019+ power re-measure uses F1_WARM_WINDOW's arm-C forecast grids (``$NBA_ROOT/data/ops/f1_warm_window_cache/C_*.npz``,
the E7 ``A0_warm``) with ``row_y`` replaced by NaN on load: no 2023 or 2024 outcome reaches any statistic here. Writes
``--out`` (default a scratch file; the pinned ``reports/prereg_wowy_repl/gate.json`` is never overwritten: never re-run the gate in the pinned checkout).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid
from nba.props import repl_delta as RD
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props import wowy_repl as WR
from nba.props.context_residual import stat_feature_names, stat_frame, to_integer_support
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import f1_warm_window_gate as F1G
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_redistribution_slices as SL

REAL = Path(os.environ.get("NBA_ROOT", "/Users/devin/Downloads/nba-prediction")).resolve()
DB = REAL / "nba.duckdb"
HDB = REAL / "data/history/nba_history.duckdb"
K_SEASONS = tuple(
    range(2015, 2022)
)  # prior-strength estimation seasons (complete seasons before the 2022 warm-up), as REPLACEMENT_DELTA
# The pinned artifact (GATE_SHA256) is never a default output: a re-run writes to a scratch path unless --out says otherwise (review E7).
OUT = Path("/tmp/wowy_struct_repl_gate_rerun.json")
PINNED_ARTIFACT = Path(__file__).resolve().parents[3] / "reports/prereg_wowy_repl/gate.json"
STRUCT_GATE = (
    Path(__file__).resolve().parents[3] / "reports/prereg_wowy_redistribution/gate.json"
)  # the committed STRUCT gate (cut-offs, kappa)
CACHE = Path("data/ops/wowy_struct_repl_cache")
F1_CACHE = REAL / "data/ops/f1_warm_window_cache"
F1_SHA = "9b5a7d6fad1e5e91cd71be7fd8026b84ca58f318013ab124fbbbbaab99899ddc"  # F1_WARM_WINDOW frozen rule sha (prereg(F1_WARM_WINDOW) commit)
FLOOR = -0.005
STATS_USED = ("pts", "ast")
CELLS = (("pts", "leader_out"), ("pts", "top25_fixed"), ("ast", "leader_out"))
CHOSEN = {"pts": "struct_u_pts", "ast": "struct_d_ast"}
DESIGNS = SG.DESIGNS_GATE
FIRST_SEASON = 2020
SEASONS_ALL = (2020, 2021, 2022, 2023, 2024)
PLANT_CUTOFF = dt.date(2024, 1, 15)


def _f(x: Any) -> float:
    return float(x)


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def stage_priors(b: pl.DataFrame) -> dict[str, Any]:
    """Replacement rates (fringe, two prior seasons) and prior strengths k (method of moments, 2015-2021): REPLACEMENT_DELTA's definitions, reused."""
    repl = RD.replacement_rates(b)
    k = RD.estimate_k(b, repl, K_SEASONS)
    return {
        "repl": {str(s): v for s, v in repl.items()},
        "k": k,
        "k_values": {x: v["k"] for x, v in k.items()},
        "k_seasons": list(K_SEASONS),
    }


# --------------------------------------------------------------------------- data-capture audit (stints, possessions, reports)


def capture_audit() -> dict[str, Any]:
    """What exists, what is used, and whether rebuilt stints reconcile with box minutes (the playbook's 47-49% was the pre-2026-10-09 build)."""
    res: dict[str, Any] = {}
    for name, db in (("history", HDB), ("main", DB)):
        con = duckdb.connect(str(db), read_only=True)
        try:
            rec = con.execute(
                """
                WITH u AS (SELECT s.game_id, s.team_id, unnest(s.players) AS player_id, (s.start_clock - s.end_clock) AS sec
                           FROM stints s JOIN games g USING (game_id) WHERE g.season <= 2024),
                a AS (SELECT game_id, team_id, player_id, sum(sec) / 60.0 AS st_min FROM u GROUP BY 1, 2, 3),
                b AS (SELECT s.game_id, s.team_id, s.player_id, s.minutes AS bx_min, g.season
                      FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024 AND s.minutes > 0)
                SELECT b.season, count(*) AS n_box_rows,
                       avg(CASE WHEN a.player_id IS NULL THEN 1.0 ELSE 0.0 END) AS no_stint_share,
                       avg(CASE WHEN abs(a.st_min - b.bx_min) <= 1.0 THEN 1.0 ELSE 0.0 END) AS within_1min_share,
                       avg(CASE WHEN abs(a.st_min - b.bx_min) <= 0.5 THEN 1.0 ELSE 0.0 END) AS within_half_min_share,
                       avg(a.st_min - b.bx_min) AS mean_diff_min
                FROM b LEFT JOIN a USING (game_id, team_id, player_id) GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
            res[f"{name}_stints_vs_box_minutes"] = [
                {
                    "season": r[0],
                    "box_rows": r[1],
                    "no_stint_share": r[2],
                    "within_1min": r[3],
                    "within_half_min": r[4],
                    "mean_diff_min": r[5],
                }
                for r in rec
            ]
            res[f"{name}_report_games_by_season"] = con.execute(
                "SELECT g.season, count(DISTINCT a.game_id) FROM player_availability a JOIN games g USING (game_id) "
                "WHERE g.season <= 2024 AND a.source = 'nba_official_report' GROUP BY 1 ORDER BY 1"
            ).fetchall()
            res[f"{name}_possessions_games_by_season"] = con.execute(
                "SELECT g.season, count(DISTINCT p.game_id) FROM possessions p JOIN games g USING (game_id) WHERE g.season <= 2024 GROUP BY 1 ORDER BY 1"
            ).fetchall()
        finally:
            con.close()
    return res


def possessions_vs_box(sample_seasons: tuple[int, ...] = (2021, 2023)) -> dict[str, Any]:
    """Exact on-floor OFFENSIVE possessions (possessions table, the player in ``off_players``) against the box approximation ``P`` used by the features:
    per-player-game relative error and correlation, on a few seasons (the approximation's error, stated, not fixed)."""
    out: dict[str, Any] = {}
    for season in sample_seasons:
        db = HDB if season <= 2021 else DB
        con = duckdb.connect(str(db), read_only=True)
        try:
            ex = con.execute(
                f"""
                WITH q AS (SELECT p.game_id, unnest(p.off_players) AS player_id FROM possessions p JOIN games g USING (game_id) WHERE g.season = {season})
                SELECT game_id, player_id, count(*) AS exact_poss FROM q GROUP BY 1, 2
                """
            ).pl()
            box = load_box(db).filter(pl.col("season") == season)
        finally:
            con.close()
        pb = RD.prep_box(box).select("game_id", pl.col("player_id").cast(pl.Int64), "P", "minutes")
        j = pb.join(
            ex.with_columns(pl.col("player_id").cast(pl.Int64)),
            on=["game_id", "player_id"],
            how="inner",
        )
        rel = ((j["P"] - j["exact_poss"]) / j["exact_poss"]).filter(j["minutes"] >= 12)
        out[str(season)] = {
            "player_games_matched": j.height,
            "box_rows": pb.height,
            "corr_P_vs_exact": _f(np.corrcoef(j["P"].to_numpy(), j["exact_poss"].to_numpy())[0, 1]),
            "mean_rel_error_rotation_rows": _f(rel.mean()),
            "mean_abs_rel_error_rotation_rows": _f(rel.abs().mean()),
        }
    return out


# --------------------------------------------------------------------------- the warm world and the columns


def build_world(t0: float, first_season: int = FIRST_SEASON, root: Path = REAL) -> dict[str, Any]:
    """Production feature frame (history + nba.duckdb, F1_WARM_WINDOW's ``build``: the pinned configs, real tips from both schedule directories),
    the STRUCT columns and the STRUCT_REPL columns, seasons >= first_season. Read-only; seasons <= 2024."""
    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import (
        _CI,
        _DI,
        MIN_PRIOR_PLAYED,
        ROTATION_MIN40,
        player_states,
    )

    dbs = ["data/history/nba_history.duckdb", "nba.duckdb"]
    feats, games, flagged = F1G.build(root, dbs)
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    _log(t0, f"warm production frame {feats.height} rows")
    parts = [load_inputs(str(root / d), None) for d in dbs]
    pgs = pl.concat([p[1] for p in parts], how="vertical_relaxed")
    played = (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    states = player_states(played)
    st = RD.state_table(played, states, _DI, _CI)
    out_rows = RD.out_rotation_rows(flagged, info, st, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    out_all = pl.DataFrame(
        [(g, int(p)) for g, ps in flagged.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    )
    box = pl.concat([load_box(root / dbs[0]), load_box(root / dbs[1])], how="vertical_relaxed")
    b = RD.prep_box(box)
    pri = stage_priors(b)
    repl = {int(s): v for s, v in pri["repl"].items()}
    outp = W.out_rotation_players(played, states, flagged, info, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    full = pl.concat(
        [SG.load_box_state(str(root / dbs[0])), SG.load_box_state(str(root / dbs[1]))],
        how="vertical_relaxed",
    )
    dec = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    pos = pl.concat(
        [SG.load_positions(str(root / dbs[0])), SG.load_positions(str(root / dbs[1]))]
    ).unique("player_id")
    dates = games.select("game_id", "game_date")
    fe = feats.filter(pl.col("season") >= first_season)
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    fr = SA.attach_struct(fe, SA.build_struct(rows, op, pool, dec, dates, pos))
    _log(t0, "STRUCT columns attached")
    scored = fe.select(
        "game_id", "team_id", pl.col("player_id").cast(pl.Int64), "game_date", "season"
    )
    wr = WR.build_wowy_repl(
        b, st, out_rows, out_all, scored, set(flagged), repl, pri["k_values"], placebo_seed=0
    )
    fr = fr.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wr, on=["game_id", "player_id"], how="left"
    )
    if fr.height != fe.height:
        raise RuntimeError("attaching STRUCT_REPL columns changed the rows")
    _log(t0, f"STRUCT_REPL columns attached ({wr.height} rows)")
    return {
        "feats": feats,
        "games": games,
        "flagged": flagged,
        "info": info,
        "played": played,
        "st": st,
        "b": b,
        "box": box,
        "out_rows": out_rows,
        "out_all": out_all,
        "pri": pri,
        "repl": repl,
        "fr": fr,
        "root": root,
    }


def priors_sha(pri: dict[str, Any]) -> str:
    canon = {
        "repl": {
            s: {x: float(f"{v:.10g}") for x, v in d.items()} for s, d in sorted(pri["repl"].items())
        },
        "k": {x: float(f"{v:.10g}") for x, v in sorted(pri["k_values"].items())},
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- coverage and the NULL pattern


def coverage_audit(fr: pl.DataFrame, cuts: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """Share of cell rows with any on/off history (``ro_conf > 0``), its distribution, and the NULL pattern of the new columns by season."""
    out: dict[str, Any] = {"null_pattern": {}, "cells": {}}
    for s in SEASONS_ALL:
        f = fr.filter(pl.col("season") == s)
        rep = f.filter(pl.col("has_report") == 1)
        out["null_pattern"][str(s)] = {
            "rows": f.height,
            "no_report_share": _f((f["has_report"] != 1).mean()),
            "null_rb_pts_share_among_reported": _f(rep["rb_pts"].is_null().mean()),
            "null_rx_pts_share_among_reported": _f(rep["rx_pts"].is_null().mean()),
            "null_rb_pts_share_first15": _f(
                rep.filter(pl.col("team_game_no") < 15)["rb_pts"].is_null().mean()
            ),
            "null_rb_pts_share_after15": _f(
                rep.filter(pl.col("team_game_no") >= 15)["rb_pts"].is_null().mean()
            ),
        }
    for stat, sn in CELLS:
        col = CHOSEN[stat]
        for s in SEASONS_ALL:
            f = fr.filter((pl.col("season") == s) & (pl.col("n_prior") >= 5))
            m = SG.cell_masks(f, col, f"leader_out_{stat}", cuts[stat])[sn]
            sub = f.filter(pl.Series(m))
            conf = sub["ro_conf"].drop_nulls()
            out["cells"][f"{stat}|{sn}|{s}"] = {
                "rows": sub.height,
                "share_with_history(ro_conf>0)": _f((conf > 0).mean()) if conf.len() else None,
                "share_ro_conf_ge_0.5": _f((conf >= 0.5).mean()) if conf.len() else None,
                "median_ro_conf": _f(conf.median()) if conf.len() else None,
                "mean_ro_conf": _f(conf.mean()) if conf.len() else None,
                "null_rx_share": _f(sub[f"rx_{stat}"].is_null().mean()) if sub.height else None,
                "sd_rb": _f(sub[f"rb_{stat}"].drop_nulls().std()),
                "sd_rx": _f(sub[f"rx_{stat}"].drop_nulls().std()),
                "corr_rb_vs_struct": _f(
                    np.corrcoef(
                        sub.drop_nulls([f"rb_{stat}", col])[f"rb_{stat}"].to_numpy(),
                        sub.drop_nulls([f"rb_{stat}", col])[col].to_numpy(),
                    )[0, 1]
                ),
            }
    return out


# --------------------------------------------------------------------------- kappa and power (C forecasts)


def kappas(fr: pl.DataFrame, cuts: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """Pooled 2020-21 and held-out 2022 coefficients of rb and rx on the residual vs the recency mean, controlling vac_<stat>, vac_min (plain) and, in addition, the other new column and the role share."""
    res: dict[str, Any] = {}
    for stat, sn in CELLS:
        col, role = CHOSEN[stat], f"role_share_{stat}"
        rb, rx = f"rb_{stat}", f"rx_{stat}"
        key = f"{stat}|{sn}"
        res[key] = {}
        for label, seasons in (("pooled_2020_21", (2020, 2021)), ("2022_heldout", (2022,))):
            parts = []
            for s in seasons:
                f = fr.filter(pl.col("season") == s)
                parts.append(
                    f.filter(pl.Series(SG.cell_masks(f, col, f"leader_out_{stat}", cuts[stat])[sn]))
                )
            sub = pl.concat(parts, how="vertical_relaxed")
            res[key][label] = {
                "rows": sub.height,
                "rb_plain": SG.slope_ctrl(sub, stat, rb),
                "rb_given_rx_role": SG.slope_ctrl(sub, stat, rb, (rx, role)),
                "rx_plain": SG.slope_ctrl(sub, stat, rx),
                "rx_given_rb_role": SG.slope_ctrl(sub, stat, rx, (rb, role)),
                "struct_plain": SG.slope_ctrl(sub, stat, col),
            }
    return res


def c_forecasts(
    stat: str,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray, np.ndarray, str]:
    """F1 arm C (the E7 ``A0_warm``) components and mixture for ``stat``, with ``row_y`` replaced by NaN, the integer-support quantiles and the continuous grid."""
    from research.eval import recent_role_full_eval as RF

    z = np.load(F1_CACHE / f"C_{stat}.npz", allow_pickle=True)
    tag = str(z["_tag"])
    if not tag.startswith(F1_SHA):
        raise RuntimeError(f"C_{stat}.npz tag {tag[:12]} is not the frozen F1 sha {F1_SHA[:12]}")
    cs = {k: z[k] for k in z.files if k != "_tag"}
    cs["row_y"] = np.full(
        len(cs["row_y"]), np.nan
    )  # outcomes are not needed by version B; NaN so none can reach a statistic
    if int(cs["row_season"].max()) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in C components")
    zp = np.load(F1_CACHE / "C_pic.npz", allow_pickle=True)
    pic = {k: zp[k] for k in zp.files if k != "_tag"}
    q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"]).astype(float)
    grid = arm_grid("mixture", cs, pic, stat)
    return cs, pic, q0, grid, tag


def power_both_arms(
    fr: pl.DataFrame, kap: dict[str, Any], cuts: dict[str, tuple[float, float]], t0: float
) -> dict[str, Any]:
    """Version B power of STRUCT and STRUCT_REPL per cell and season on the C forecasts (outcome-free; same method as the STRUCT gate).
    STRUCT shift = kappa_struct x struct (the stored plain 2020-21 coefficient of ``struct_warm.kappa``); STRUCT_REPL shift = max(kappa_rb, 0) x rb + max(kappa_rx, 0) x rx,
    each the SMALLER of its plain and its given-the-other-and-role coefficient (the STRUCT gate's convention: the smaller kappa)."""
    stored = json.loads(STRUCT_GATE.read_text())["struct_warm"]["kappa"]
    out: dict[str, Any] = {
        "forecast_source": "F1_WARM_WINDOW arm C (E7 A0_warm), row_y replaced by NaN on load",
        "cells": {},
    }
    for stat in STATS_USED:
        cs, _pic, q0, grid, tag = c_forecasts(stat)
        out.setdefault("c_tags", {})[stat] = tag
        gid, season = cs["row_gid"].astype(str), cs["row_season"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        col = CHOSEN[stat]
        cols = [
            "game_id",
            "player_id",
            "vac_min",
            "n_out_rot",
            "has_report",
            col,
            f"leader_out_{stat}",
            f"rb_{stat}",
            f"rx_{stat}",
        ]
        jf = keys.join(fr.select(cols), on=["game_id", "player_id"], how="left").with_columns(
            pl.col("vac_min").fill_null(0.0),
            pl.col("n_out_rot").fill_null(0),
            pl.col("has_report").fill_null(0),
            pl.col(col).fill_null(0.0),
            pl.col(f"leader_out_{stat}").fill_null(0),
            pl.col(f"rb_{stat}").fill_null(0.0),
            pl.col(f"rx_{stat}").fill_null(0.0),
        )
        out.setdefault("rows_c", {})[stat] = {
            "rows": int(len(gid)),
            "matched_to_features": int(jf.filter(pl.col("has_report") >= 0).height),
        }
        for sn in sorted({c[1] for c in CELLS if c[0] == stat}):
            k_s = max(stored[stat]["pooled_2020_21"][sn]["plain"]["coef"], 0.0)
            kk = kap[f"{stat}|{sn}"]["pooled_2020_21"]
            cand_b = [kk["rb_plain"], kk["rb_given_rx_role"]]
            cand_x = [kk["rx_plain"], kk["rx_given_rb_role"]]
            k_b = max(min((c["coef"] for c in cand_b if c), default=0.0), 0.0)
            k_x = max(min((c["coef"] for c in cand_x if c), default=0.0), 0.0)
            plain_b = max((kk["rb_plain"] or {"coef": 0.0})["coef"], 0.0)
            arms = {
                "STRUCT": k_s * jf[col].to_numpy(),
                "STRUCT_REPL": k_b * jf[f"rb_{stat}"].to_numpy()
                + k_x * jf[f"rx_{stat}"].to_numpy(),
                "STRUCT_REPL[plain_kappa_rb]": plain_b * jf[f"rb_{stat}"].to_numpy(),
                "STRUCT_REPL[structural_kappa_1]": 1.0 * jf[f"rb_{stat}"].to_numpy(),
            }
            for s in G.SEASONS:
                in_s = season == s
                msub = SG.cell_masks(
                    jf.filter(pl.Series(in_s)), col, f"leader_out_{stat}", cuts[stat]
                )[sn]
                ms = np.zeros(len(gid), dtype=bool)
                ms[np.flatnonzero(in_s)] = msub
                for arm, shift in arms.items():
                    sel = ms & (shift != 0)
                    gm, gv = np.zeros(len(gid)), np.zeros(len(gid))
                    if sel.any():
                        gm[sel], gv[sel] = SL.expected_gain_var(
                            q0[sel], to_integer_support(grid[sel] + shift[sel, None])
                        )
                    e = -_f(gm[ms].mean())
                    sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var()))
                    rb_ = C.sizing_row("b", max(sd_free, 1e-9), SL.DEFF_FREE, ms.sum(), FLOOR, e)
                    hw = rb_["halfwidth95"]
                    out["cells"][f"{stat}|{sn}|{s}|{arm}"] = {
                        "kappa": {"struct": k_s}
                        if arm == "STRUCT"
                        else {"rb": k_b, "rx": k_x, "plain_rb": plain_b},
                        "rows": int(ms.sum()),
                        "games": int(len(np.unique(gid[ms]))),
                        "expected_delta": e,
                        "halfwidth_b": hw,
                        "power_b": rb_["power_one_season"],
                        "breakeven_halfwidth_multiplier": (abs(e) / hw) if e <= FLOOR else 0.0,
                    }
        _log(t0, f"power sized {stat}")
    return out


# --------------------------------------------------------------------------- dev GBM designs


def _perm_group(
    df: pl.DataFrame, cols: list[str], rng: np.random.Generator
) -> dict[str, np.ndarray]:
    """One within-player-season permutation applied jointly to ``cols`` (the columns stay a coherent set)."""
    idx_all = np.arange(df.height)
    key = (df["player_id"].cast(pl.Utf8) + "_" + df["season"].cast(pl.Utf8)).to_numpy()
    new_idx = idx_all.copy()
    for k in np.unique(key):
        ix = np.flatnonzero(key == k)
        new_idx[ix] = ix[rng.permutation(len(ix))]
    return {c: df[c].to_numpy().astype(float)[new_idx] for c in cols}


def gbm_arms_eval(
    train: pl.DataFrame, test: pl.DataFrame, stat: str
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """Production-feature LightGBM mean head on the residual vs the recency mean; squared error on test rows per arm (one seed, the STRUCT gate's parameters).
    Arms (all but ``base`` carry the role share): role, S+R, SR (STRUCT_REPL: rb, rx, ro_conf), RAW (rr, ro_conf), P+R_S and P+R_SR (columns permuted jointly within
    player-season, train and test separately, ``default_rng(0)``), PQ (identity placebo columns)."""
    import lightgbm as lgb

    col, role = CHOSEN[stat], f"role_share_{stat}"
    rb, rx, rr = f"rb_{stat}", f"rx_{stat}", f"rr_{stat}"
    names = [c for c in stat_feature_names(stat) if c in train.columns]
    rng = np.random.default_rng(0)

    def prep(df: pl.DataFrame) -> pl.DataFrame:
        sf = stat_frame(df.filter(pl.col("has_report") == 1), stat)
        ps = _perm_group(sf, [col], rng)
        pr = _perm_group(sf, [rb, rx, "ro_conf"], rng)
        return sf.with_columns(
            pl.Series("_perm_s", ps[col]),
            pl.Series("_perm_rb", pr[rb]),
            pl.Series("_perm_rx", pr[rx]),
            pl.Series("_perm_conf", pr["ro_conf"]),
        )

    tr, te = prep(train), prep(test)
    arms = {
        "base": [],
        "role": [role],
        "S+R": [role, col],
        "SR": [role, rb, rx, "ro_conf"],
        "RAW": [role, rr, "ro_conf"],
        "P+R_S": [role, "_perm_s"],
        "P+R_SR": [role, "_perm_rb", "_perm_rx", "_perm_conf"],
        "PQ": [role, f"pq_{rb}", f"pq_{rx}", "pq_ro_conf"],
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
        "role_vs_base": rel("role", "base"),
        "S+R_beyond_role": rel("S+R", "role"),
        "SR_beyond_role": rel("SR", "role"),
        "SR_vs_S+R(head_to_head)": rel("SR", "S+R"),
        "RAW_beyond_role": rel("RAW", "role"),
        "SR_vs_RAW": rel("SR", "RAW"),
        "P+R_S_beyond_role": rel("P+R_S", "role"),
        "P+R_SR_beyond_role": rel("P+R_SR", "role"),
        "PQ_beyond_role": rel("PQ", "role"),
    }


def cell_gate_rule(power: dict[str, Any], gbm: dict[str, Any]) -> dict[str, Any]:
    """The declared cell gate for the STRUCT_REPL arm (docs/prereg/WOWY_STRUCT.md, 'Gate'), computed from stored numbers only:
    (a) power_b >= 0.5 in 2023 and 2024 for STRUCT_REPL; (b) STRUCT_REPL beyond role share positive in D1, D2a and D2b and its D1 interval excludes 0;
    (c) the player-season-permuted columns (P+R_SR) AND the identity placebo (PQ) each gain less than STRUCT_REPL beyond role in all three designs.
    The STRUCT arm's own (a)-(c) is reported beside it; the head-to-head SR vs S+R is reported, not gated."""
    verdict: dict[str, Any] = {}
    for stat, sn in CELLS:
        key = f"{stat}|{sn}"
        row: dict[str, Any] = {}
        for arm, beyond, perm in (
            ("STRUCT_REPL", "SR_beyond_role", ("P+R_SR_beyond_role", "PQ_beyond_role")),
            ("STRUCT", "S+R_beyond_role", ("P+R_S_beyond_role",)),
        ):
            p = [power["cells"][f"{stat}|{sn}|{s}|{arm}"]["power_b"] for s in G.SEASONS]
            a = bool(all(x is not None and x >= 0.5 for x in p))
            br = [gbm[stat][d][sn][beyond] for d in DESIGNS]
            b = bool(all(x["relative_gain_pct"] > 0 for x in br) and br[0]["ci_pct"][0] > 0.0)
            c = bool(
                all(
                    gbm[stat][d][sn][pc]["relative_gain_pct"]
                    < gbm[stat][d][sn][beyond]["relative_gain_pct"]
                    for d in DESIGNS
                    for pc in perm
                )
            )
            row[arm] = {
                "a_power_b_2023_2024": p,
                "a": a,
                "b_beyond_role_D1_D2a_D2b_pct": [x["relative_gain_pct"] for x in br],
                "b_D1_ci_pct": br[0]["ci_pct"],
                "b": b,
                "c": c,
                "pass_gate": bool(a and b and c),
            }
        row["head_to_head_SR_vs_S+R_D1_D2a_D2b_pct"] = [
            gbm[stat][d][sn]["SR_vs_S+R(head_to_head)"]["relative_gain_pct"] for d in DESIGNS
        ]
        verdict[key] = row
    return verdict


# --------------------------------------------------------------------------- planted future on real rows


def planted_future(world: dict[str, Any], t0: float) -> dict[str, Any]:
    """Scramble every box row dated on/after PLANT_CUTOFF (stats x 3 + 7), rebuild the state table and the STRUCT_REPL columns for the EARLIER 2023-24 rows; the columns must not move
    (tolerance 1e-9, NULL pattern equal); the same scramble must move later rows' columns (the test is not inert)."""
    from nba.props.context_residual import _CI, _DI, MIN_PRIOR_PLAYED, ROTATION_MIN40, player_states

    w = world
    cut = PLANT_CUTOFF
    stat_cols = ["pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "oreb", "minutes"]
    box = w["box"].filter(
        pl.col("game_date") >= pl.date(2021, 1, 1)
    )  # same prefix for both runs; a rebuild of everything older is not needed
    plant = box.with_columns(
        [
            pl.when(pl.col("game_date") >= cut)
            .then(pl.col(c).fill_null(0.0) * 3.0 + 7.0)
            .otherwise(pl.col(c))
            .alias(c)
            for c in stat_cols
        ]
    )
    played = w["played"].filter(pl.col("game_date") >= pl.date(2021, 1, 1))
    played_p = played.with_columns(
        [
            pl.when(pl.col("game_date") >= cut)
            .then(pl.col(c).fill_null(0.0) * 3.0 + 7.0)
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "minutes")
            if c in played.columns
        ]
    )
    st_p = RD.state_table(played_p, player_states(played_p), _DI, _CI)
    st_0 = RD.state_table(played, player_states(played), _DI, _CI)
    flagged, info = w["flagged"], w["info"]
    out_p = RD.out_rotation_rows(flagged, info, st_p, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    out_0 = RD.out_rotation_rows(flagged, info, st_0, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    b_p, b_0 = RD.prep_box(plant), RD.prep_box(box)
    fe = w["feats"].filter(pl.col("season") == 2023)
    scored = fe.select(
        "game_id", "team_id", pl.col("player_id").cast(pl.Int64), "game_date", "season"
    )
    early = scored.filter(pl.col("game_date") < cut)
    late = scored.filter(pl.col("game_date") >= cut + dt.timedelta(days=30))
    # the later (planted) segment sits INSIDE season 2023: games on/after the cut are scrambled
    res: dict[str, Any] = {"cutoff": str(cut), "tolerance": 1e-9}
    repl, k = world["repl"], world["pri"]["k_values"]

    def run(
        sc: pl.DataFrame, b: pl.DataFrame, st: pl.DataFrame, orow: pl.DataFrame
    ) -> pl.DataFrame:
        return WR.build_wowy_repl(
            b, st, orow, w["out_all"], sc, set(flagged), repl, k, placebo_seed=0
        ).sort(["game_id", "player_id"])

    a, bb = run(early, b_0, st_0, out_0), run(early, b_p, st_p, out_p)
    diffs = {}
    for c in (*WR.COLUMNS, *WR.PLACEBO_COLUMNS):
        x, y = a[c].to_numpy().astype(float), bb[c].to_numpy().astype(float)
        diffs[c] = {
            "null_equal": bool(np.array_equal(np.isnan(x), np.isnan(y))),
            "max_abs_diff": _f(np.nanmax(np.abs(x - y), initial=0.0)),
        }
    la, lb = run(late, b_0, st_0, out_0), run(late, b_p, st_p, out_p)
    moved = {
        c: _f(np.nanmean(np.abs(la[c].to_numpy().astype(float) - lb[c].to_numpy().astype(float))))
        for c in ("rb_pts", "rx_pts", "rr_pts")
    }
    res.update(
        {
            "early_rows": early.height,
            "late_rows": late.height,
            "early_diffs": diffs,
            "later_rows_mean_abs_move": moved,
            "pass": bool(
                all(d["null_equal"] and d["max_abs_diff"] <= 1e-9 for d in diffs.values())
                and moved["rb_pts"] > 0
                and moved["rr_pts"] > 0
            ),
        }
    )
    _log(t0, f"planted future: pass={res['pass']}")
    return res


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-planted", action="store_true")
    ap.add_argument("--skip-gbm", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    t0 = time.time()
    res: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "data_version_manifest": json.loads(
            (REAL / "docs/data_manifests/latest.json").read_text()
        ).get("data_version"),
        "db_mtime": {
            "nba.duckdb": dt.datetime.fromtimestamp(DB.stat().st_mtime).isoformat(),
            "nba_history.duckdb": dt.datetime.fromtimestamp(HDB.stat().st_mtime).isoformat(),
        },
        "c_inputs_sha256": {
            f"C_{s}.npz": sha256_file(F1_CACHE / f"C_{s}.npz") for s in (*STATS_USED, "pic")
        },
    }
    res["capture"] = capture_audit()
    res["possessions_vs_box_P"] = possessions_vs_box()
    _log(t0, "capture audit done")
    world = build_world(t0)
    fr = world["fr"]
    res["priors_sha256"] = priors_sha(world["pri"])
    res["replacement_rates"] = world["pri"]["repl"]
    res["k_values"] = world["pri"]["k_values"]
    stored = json.loads(STRUCT_GATE.read_text())["struct_warm"]
    cuts = {
        s: tuple(stored["fixed_cutoffs_2020_21(|struct| quantiles 0.75 / 0.90)"][s])
        for s in STATS_USED
    }
    res["coverage"] = coverage_audit(fr, cuts)
    _log(t0, "coverage audit done")
    kap = kappas(fr, cuts)
    res["kappa"] = kap
    _log(t0, "kappas done")
    power = power_both_arms(fr, kap, cuts, t0)
    res["power"] = power
    if not a.skip_gbm:
        designs = {
            "D1_train2020-21_test2022": ([2020, 2021], 2022),
            "D2a_train2020_test2021": ([2020], 2021),
            "D2b_train2021_test2020": ([2021], 2020),
        }
        gb: dict[str, Any] = {}
        for stat in STATS_USED:
            gb[stat] = {}
            for dn, (tr_s, te_s) in designs.items():
                te, errs = gbm_arms_eval(
                    fr.filter(pl.col("season").is_in(tr_s)),
                    fr.filter(pl.col("season") == te_s),
                    stat,
                )
                msd = SG.cell_masks(te, CHOSEN[stat], f"leader_out_{stat}", cuts[stat])
                gb[stat][dn] = {
                    sn: gbm_contrasts(te, errs, msd[sn]) for sn in ("leader_out", "top25_fixed")
                }
                _log(t0, f"gbm {stat} {dn}")
        res["gbm"] = gb
        res["cell_gate"] = cell_gate_rule(power, gb)
        res["verdict_family"] = {
            "S_R(STRUCT)": [k for k, v in res["cell_gate"].items() if v["STRUCT"]["pass_gate"]],
            "T_R(STRUCT_REPL)": [
                k for k, v in res["cell_gate"].items() if v["STRUCT_REPL"]["pass_gate"]
            ],
        }
    if not a.skip_planted:
        res["planted_future"] = planted_future(world, t0)
    if a.out.resolve() == PINNED_ARTIFACT.resolve() and PINNED_ARTIFACT.exists():
        raise SystemExit(
            "refusing to overwrite the pinned artifact reports/prereg_wowy_repl/gate.json (review E7)"
        )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float))
    _log(t0, f"wrote {a.out} sha256 {sha256_file(a.out)[:16]}")
    print(json.dumps(res.get("cell_gate", {}), indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
