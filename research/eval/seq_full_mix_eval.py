# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""SEQ_FULL_MIX harness (rule: docs/prereg/SEQ_FULL_MIX.md; DRAFT until the main session commits `prereg(SEQ_FULL_MIX): <sha256>`).

AVG = 0.5 * pmf(SEQc) + 0.5 * pmf(FULL), integer grid. SEQc = ENSEMBLE E2's learned walk-forward blend of [A0 continuous mixture, SEQ-E2] on regular-season
rows (game_id prefix 002), postseason / play-in rows take A0. FULL = RECENT_ROLE_FULL exactly as frozen (73f3f4b0). PRIMARY contrast on the held-out season,
points: AVG minus FULL (what the sequence model adds once FULL is in), floor -0.005, CI upper < 0.

    M="-m research.eval.seq_full_mix_eval"
    .venv/bin/python -W ignore $M gate [--allow-draft]      # 2023/24 from stored arrays: reproduction, guards, invariance, E2S position (about 2 min, heavy.lock)
    .venv/bin/python -W ignore $M null --k 50               # permutation null of the blend + mixture on 2023/24 (resumable; about 6 s per draw)
    .venv/bin/python -W ignore $M rehearse                  # the 2025 path with season 2024 standing in (reads nba.duckdb, cap never raised; heavy)
    .venv/bin/python -W ignore $M run --sha <64 hex> --seq-run <dir with oof_E2/E2P/E2S>   # THE one 2025 touch: refuses unless frozen + committed row

`run` is the only command that can read a 2025 outcome. Every other command asserts `season <= 2024`.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from nba.eval.context_residual_eval import bh_adjust, boot_p_value, crps_from_quantiles
from nba.eval.lower_tail_eval import arm_grid, collect_pi, load_components
from nba.props.context_residual import PROP_STATS, to_integer_support
from research.eval import ensemble_eval as E
from research.eval import near_miss_bundle_eval as N
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as F
from research.eval import router_g2_eval as G
from research.eval import seq_full_mix_rule as RULE
from research.eval import workload_eval as WE

ID = RULE.ID
ROOT = RULE.ROOT
OUT_DIR = ROOT / "reports/prereg_seq_full_mix"
REPORT_MD = ROOT / "reports/prereg_seq_full_mix.md"
CACHE = ROOT / "data/ops/seq_full_mix_cache"
SEQ_STORED = N.SEQ_RUN
STATS = tuple(PROP_STATS)
SELECT_SEASONS = (2023, 2024)
HOLDOUT_SEASON = 2025
REPO_CAP = 2024
N_BOOT = 2000
BOOT_SEED = 0
FLOOR = -0.005  # integer CRPS, primary AVG - FULL and AVG - A0 (house props floor)
PLACEBO_MARGIN = -0.003  # AVG - AVG_P (SEQ past-only placebo in the same mixture), point
MIXTURE_MARGIN = (
    -0.003
)  # AVG - AVG_A0 (SEQc 50/50 with A0: FULL's information beyond the mixture effect), point
NULL_K = 50  # permutation draws, on the select seasons before the freeze and again on the held-out season at scoring
NULL_SD_MULT = 2.0  # a noise arm may not beat its permutation null by more than this many null sd
BLEND_TAU = 0.5
REGULAR = "002"
PROGS_PRIMARY = ("E2",)
PROGS_GUARD = ("E2P", "E2S")
STAT_PRIMARY = "pts"
T467 = {
    2023: -0.0083501,
    2024: -0.0083145,
}  # stored exact FULL - A0 pts (reports/prereg_recent_role_full/results.json), 1e-6
T471_AVG_MINUS_A0 = {
    2023: -0.0183516,
    2024: -0.0180972,
}  # reports/seq_blend_slices_2026-10-12/results.json (point, exact to 1e-6)
T471_AVG_MINUS_FULL = {2023: -0.0100015, 2024: -0.0097828}
T473 = (
    ROOT / "reports/prereg_recent_role_full/stage2/results.json"
)  # logged 2025 A0 / FULL pts integer CRPS (the identity check for the regenerated forecasts)
EXACT_TOL = 1e-6


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float))


# --------------------------------------------------------------------------- pure functions
def within_date_perm(date_days: np.ndarray, seed: int) -> np.ndarray:
    """Index permuting rows among the rows of one calendar date (a different permutation per seed)."""
    rng = np.random.default_rng([seed, 20261012])
    idx = np.arange(len(date_days))
    for d in np.unique(date_days):
        w = np.flatnonzero(date_days == d)
        idx[w] = rng.permutation(w)
    return idx


def regular_flag(gid: np.ndarray) -> np.ndarray:
    return np.array([str(g)[:3] == REGULAR for g in gid], dtype=float)


@contextmanager
def stack_bound(cap: int) -> Iterator[None]:
    """`research.stack.router` refuses any season >= its FROZEN_SEASON; lift it to cap + 1 for one blend call (only the held-out-season path passes cap = 2025)."""
    from research.stack import router as R

    old = R.FROZEN_SEASON
    R.FROZEN_SEASON = max(old, cap + 1)
    try:
        yield
    finally:
        R.FROZEN_SEASON = old


def learned_blend_scored(
    first199: np.ndarray,
    seq199: np.ndarray,
    y: np.ndarray,
    date_days: np.ndarray,
    season: np.ndarray,
    ctx: pd.DataFrame,
    stat: str,
    cap: int = REPO_CAP,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`near_miss_bundle_eval.learned_blend` (T442's walk-forward softmax gate over [first, seq], 30-day blocks, 3000 rows minimum, strictly earlier rows,
    0.5 / 0.5 before the first fit), line for line, with the season cap as a parameter. Rows keep their input order."""
    from research.stack.data import StackData
    from research.stack.router import SoftmaxGate, WalkForwardConfig, walk_forward

    keys = pd.DataFrame(
        {"game_date": pd.to_datetime(date_days.astype("datetime64[D]")), "season": season}
    )
    cand = np.stack([E.grid19(first199), E.grid19(seq199)], axis=1)
    data = StackData(
        stat, ["first", "seq"], keys, y.astype(float), cand, ctx.reset_index(drop=True)
    )
    with stack_bound(cap):
        res = walk_forward(
            data,
            SoftmaxGate,
            WalkForwardConfig(
                block_days=N.GATE_BLOCK_DAYS, min_train_rows=N.GATE_MIN_ROWS, max_season=cap
            ),
        )
    w = np.where(np.isnan(res.weights), 0.5, res.weights)
    return (
        np.asarray(w[:, :1] * first199 + w[:, 1:2] * seq199),
        w,
        np.asarray(res.scored, dtype=bool),
    )


def learned_blend(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
    pooled, w, _ = learned_blend_scored(*args, **kwargs)
    return pooled, w


def seqc_grid(a0_int: np.ndarray, blended_cont: np.ndarray, reg: np.ndarray) -> np.ndarray:
    """SEQc: the learned blend (integer grid) on regular-season rows, A0 elsewhere."""
    return G.route(a0_int, to_integer_support(blended_cont).astype(np.int16), reg, tau=BLEND_TAU)


def mix_half(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return E.mix_integer_quantiles(a, b, np.full(len(a), 0.5))


def blend_ctx(b: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "log_n_prior": np.log1p(b["n_prior"]),
            "starter10": b["starter10"],
            "n_rep_out": b["n_rep_out"],
            "has_report": b["has_report"],
            "team_game_no": b["team_game_no"],
        }
    )


def build_arms(
    stat: str,
    b: dict[str, Any],
    date_days: np.ndarray,
    ctx: pd.DataFrame,
    seq199: dict[str, np.ndarray],
    cap: int = REPO_CAP,
) -> dict[str, np.ndarray]:
    """Integer 199-grids of every arm for one stat on the rows of ``b``. ``seq199``: program -> SEQ continuous 199-grid aligned to these rows.
    Needs ``b['mix']`` (A0 continuous), ``b['a0_int']``, ``b['full_int']``."""
    reg = regular_flag(b["game_id"])
    a0, full = b["a0_int"], b["full_int"]
    arms: dict[str, np.ndarray] = {"A0": a0, "FULL": full}
    names = {"E2": "SEQc", "E2P": "SEQc_P", "E2S": "SEQc_S"}
    for prog, grid in seq199.items():
        pooled, _ = learned_blend(b["mix"], grid, b["y"], date_days, b["season"], ctx, stat, cap)
        sq = seqc_grid(a0, pooled, reg)
        arms[names[prog]] = sq
        avg = {"E2": "AVG", "E2P": "AVG_P", "E2S": "AVG_S"}[prog]
        arms[avg] = mix_half(sq, full)
    if "SEQc" in arms:
        arms["AVG_A0"] = mix_half(arms["SEQc"], a0)
    return arms


CONTRASTS: tuple[tuple[str, str], ...] = (
    ("AVG", "FULL"),  # PRIMARY (pts)
    ("AVG", "A0"),
    ("AVG", "AVG_P"),  # SEQ real vs SEQ past-only placebo, same mixture
    ("AVG", "AVG_A0"),  # FULL's information beyond the mixture effect
    (
        "AVG",
        "AVG_S",
    ),  # SEQ real vs SEQ with inputs shuffled among the players of a date, same mixture
    ("AVG_S", "FULL"),  # shuffled-input SEQ in the mixture (noise arm)
    ("AVG_P", "FULL"),  # descriptive
    ("SEQc", "A0"),  # descriptive
    ("FULL", "A0"),  # descriptive (T467 / T473)
    ("AVG", "SEQc"),  # descriptive: what FULL adds to SEQ
)


def contrast_rows(
    arms: dict[str, np.ndarray],
    y: np.ndarray,
    gid: np.ndarray,
    season: np.ndarray,
    seasons: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    crps = {k: crps_from_quantiles(v.astype(float), y) for k, v in arms.items()}
    out: dict[str, dict[str, Any]] = {}
    for a, c in CONTRASTS:
        if a not in crps or c not in crps:
            continue
        d = crps[a] - crps[c]
        out[f"{a}_minus_{c}"] = {}
        for s in seasons:
            m = season == s
            ci = E.clustered(d[m], gid[m])
            out[f"{a}_minus_{c}"][str(s)] = {
                "n": int(m.sum()), "n_games": int(len(np.unique(gid[m]))), "delta": ci["point"],
                "ci_lo": ci["lo"], "ci_hi": ci["hi"], "p": float(boot_p_value(d[m], gid[m], N_BOOT, BOOT_SEED)),
                "mde": (ci["hi"] - ci["lo"]) / 2.0,
            }  # fmt: skip
    return out


def arm_guards(
    stat: str,
    b: dict[str, Any],
    q: np.ndarray,
    seasons: tuple[int, ...],
    dk_seasons: tuple[int, ...],
) -> dict[str, Any]:
    """ROUTER_G2's guards (g1 calibration, g2 slices, g2b minutes<5, g3 concentration, g4 thresholds, g5 proxy-safe) for arm ``q`` against A0, per season.
    g4: DK T-60 margin where DK lines exist (2023/24) AND the fixed 20/25/30 thresholds; for a season with no DK lines only the fixed thresholds."""
    rows = np.arange(len(b["y"]))
    out: dict[str, Any] = {}
    saved = E.SEASONS
    try:
        for s in seasons:
            E.SEASONS = (s,)
            use_dk = stat == STAT_PRIMARY and s in dk_seasons
            ev = E.evaluate_arm(stat, b, rows, q, ci_slices=False, dk=use_dk)[str(s)]
            g = E.guards_for(
                {k: v for k, v in ev.items() if not k.startswith("_")},
                stat,
                e1_like=stat == STAT_PRIMARY,
            )
            if stat == STAT_PRIMARY and not use_dk:
                m = b["season"] == s
                fixed = {
                    str(t): float(
                        (
                            E.thr_ll(q[m].astype(float), b["y"][m], np.full(int(m.sum()), float(t)))
                            - E.thr_ll(
                                b["a0_int"][m].astype(float),
                                b["y"][m],
                                np.full(int(m.sum()), float(t)),
                            )
                        ).mean()
                    )
                    for t in E.THRESHOLDS
                }
                ev["tll_fixed"] = fixed
                g["g4_thresholds"] = bool(all(v <= E.TLL_FIXED_MAX for v in fixed.values()))
            sl = ev["slices"]["minutes<5"]
            g["g2b_minutes<5"] = bool(
                sl["n"] < E.MIN_SLICE_N or sl.get("point", -1.0) <= E.SLICE_WORSE
            )
            out[str(s)] = {
                "n": ev["n"], "delta_vs_a0": ev["delta"], "calibration": ev["calibration"], "slices": ev["slices"],
                "team_share": ev["team_share"], "tll_dk": ev.get("tll_dk"), "tll_fixed": ev.get("tll_fixed"),
                "guards": g, "guards_all_pass": bool(all(g.values())),
            }  # fmt: skip
    finally:
        E.SEASONS = saved
    return out


# --------------------------------------------------------------------------- the stored world (2023 / 2024)
def stored_world() -> dict[str, Any]:
    """A0 arrays, FULL (stored heads/pi caches), SEQ E2 / E2S for 2023-24. Seasons <= 2024 asserted."""
    w = N.load_world()
    pic = dict(np.load(F.LT_DIR / "components_pi.npz", allow_pickle=True))
    pi_r = np.load(F.pi_path("R"))["pi"]
    for st in STATS:
        b = w["base"][st]
        if int(b["season"].max()) > REPO_CAP:
            raise RuntimeError("season > 2024 in the stored world")
        comp = load_components(st, F.LT_DIR)
        comp_full = F.load_heads(st, "R")
        if not F.same_rows(comp_full, comp):
            raise RuntimeError(f"{st}: FULL rows are not the A0 rows")
        b["full_int"] = F.arm_qi(st, comp_full, pic, pi_r)
        b["full_mix"] = arm_grid("mixture", comp_full, PR.candidate_pic(pic, pi_r), st)
        if not np.array_equal(WE.mixture_qi(comp, pic, st), b["a0_int"]):
            raise RuntimeError(f"{st}: A0 from the components is not the ensemble A0")
    w["seq_programs"] = {"E2": w["seq"]}
    w["seq_programs"]["E2S"] = load_seq_run(SEQ_STORED, "E2S", w["base"], REPO_CAP, key_from="E2")
    return w


def load_seq_run(
    run_dir: Path,
    prog: str,
    base: dict[str, dict[str, Any]],
    max_season: int,
    key_from: str | None = None,
) -> dict[str, np.ndarray]:
    """Per stat the continuous 199-grid of ``prog`` aligned to the A0 rows (error on any missing key). ``key_from``: take the (game_id, player_id) keys
    positionally from another program's file of the same run (ENSEMBLE's stored E2S file carries the SHUFFLED input's player id; its rows are in E2's order)."""
    z = np.load(run_dir / f"oof_{prog}.npz", allow_pickle=True)
    if int(z["season"].max()) > max_season:
        raise RuntimeError(f"season > {max_season} in {prog}")
    gid, pid = z["game_id"].astype(str), z["player_id"].astype(np.int64)
    if key_from is not None:
        k = np.load(run_dir / f"oof_{key_from}.npz", allow_pickle=True)
        if not (
            np.array_equal(k["game_id"].astype(str), gid)
            and np.array_equal(k["date_days"], z["date_days"])
        ):
            raise RuntimeError(f"{prog} rows are not in {key_from}'s order")
        pid = k["player_id"].astype(np.int64)
    src = pl.DataFrame({"game_id": gid, "player_id": pid, "_i": np.arange(len(gid))})
    out: dict[str, np.ndarray] = {}
    for si, st in enumerate(STATS):
        b = base[st]
        j = pl.DataFrame(
            {"game_id": b["game_id"].astype(str), "player_id": b["player_id"].astype(np.int64)}
        ).join(src, on=["game_id", "player_id"], how="left")
        if j["_i"].null_count():
            raise RuntimeError(
                f"{prog}/{st}: {j['_i'].null_count()} A0 rows missing from the SEQ output"
            )
        out[st] = E.ext19(z["q"][j["_i"].to_numpy(), si, :])
    return out


def seq_199(w: dict[str, Any], stat: str) -> dict[str, np.ndarray]:
    return {p: g[stat] for p, g in w["seq_programs"].items()}


# --------------------------------------------------------------------------- gate (stored arrays)
def check(name: str, value: Any, threshold: str, ok: bool) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def run_gate(allow_draft: bool = False) -> dict[str, Any]:
    t0 = time.time()
    tests: list[dict[str, Any]] = []
    with WE.heavy():
        _log("gate: loading the stored world (seasons <= 2024)")
        w = stored_world()
        res: dict[str, Any] = {"stats": {}}
        for st in STATS:
            b = w["base"][st]
            ctx = w["ctx"]
            _log(f"gate {st}: arms")
            arms = build_arms(st, b, w["date_days"], ctx, seq_199(w, st))
            rows = contrast_rows(arms, b["y"], b["game_id"], b["season"], SELECT_SEASONS)
            guards = arm_guards(st, b, arms["AVG"], SELECT_SEASONS, (2023, 2024))
            res["stats"][st] = {"contrasts": rows, "guards_AVG": guards}
            if st == STAT_PRIMARY:
                for s in SELECT_SEASONS:
                    tests.append(check(f"pts {s} AVG - A0 reproduces T471", {"delta": rows["AVG_minus_A0"][str(s)]["delta"], "stored": T471_AVG_MINUS_A0[s]}, f"<= {EXACT_TOL}", abs(rows["AVG_minus_A0"][str(s)]["delta"] - T471_AVG_MINUS_A0[s]) <= EXACT_TOL))  # fmt: skip
                    tests.append(check(f"pts {s} AVG - FULL reproduces T471", {"delta": rows["AVG_minus_FULL"][str(s)]["delta"], "stored": T471_AVG_MINUS_FULL[s]}, f"<= {EXACT_TOL}", abs(rows["AVG_minus_FULL"][str(s)]["delta"] - T471_AVG_MINUS_FULL[s]) <= EXACT_TOL))  # fmt: skip
                    tests.append(check(f"pts {s} FULL - A0 reproduces T467", {"delta": rows["FULL_minus_A0"][str(s)]["delta"], "stored": T467[s]}, f"<= {EXACT_TOL}", abs(rows["FULL_minus_A0"][str(s)]["delta"] - T467[s]) <= EXACT_TOL))  # fmt: skip
                res["pts_arms_crps"] = {k: {str(s): float(crps_from_quantiles(v.astype(float), b["y"])[b["season"] == s].mean()) for s in SELECT_SEASONS} for k, v in arms.items()}  # fmt: skip
            for s in SELECT_SEASONS:
                tests.append(check(f"{st} {s}: every ROUTER_G2 guard passes for AVG", guards[str(s)]["guards"], "all True", guards[str(s)]["guards_all_pass"]))  # fmt: skip
            if st == STAT_PRIMARY:
                tests += invariance_checks(st, b, w, arms)
                nodk = arm_guards(
                    st, b, arms["AVG"], (2024,), ()
                )  # the season-with-no-DK-lines branch the held-out season takes
                fx, fy = nodk["2024"]["tll_fixed"], guards["2024"]["tll_fixed"]
                tests.append(check("g4 fixed-threshold branch (no DK lines) equals the DK branch's fixed values", {"no_dk": fx, "dk_branch": fy}, "<= 1e-12", all(abs(fx[k] - fy[k]) <= 1e-12 for k in fx)))  # fmt: skip
                tests.append(check("g4 no-DK branch verdict equals the full verdict when the DK margin passes", {"no_dk": nodk["2024"]["guards"].get("g4_thresholds"), "full": guards["2024"]["guards"].get("g4_thresholds")}, "equal", nodk["2024"]["guards"].get("g4_thresholds") == guards["2024"]["guards"].get("g4_thresholds")))  # fmt: skip
            del arms
        res["gate"] = tests
        res["gate_ok"] = bool(all(t["ok"] for t in tests))
        res["seconds"] = time.time() - t0
        res["draft"] = allow_draft
    write_json(OUT_DIR / ("gate_draft.json" if allow_draft else "gate.json"), res)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return res


def invariance_checks(
    stat: str, b: dict[str, Any], w: dict[str, Any], arms: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    """Appending later rows must not change a single earlier blend output (the walk-forward is past-only): the 2025 scoring stacks 2025 rows after
    the 2023/24 rows. Checked by appending a time-shifted copy of the 2024 rows (+ 400 days, season 2025) and comparing the 2023/24 rows' SEQc."""
    m = b["season"] == 2024
    n = len(b["y"])
    b2 = {
        k: (np.concatenate([v, v[m]]) if isinstance(v, np.ndarray) and len(v) == n else v)
        for k, v in b.items()
    }
    b2["season"] = np.concatenate([b["season"], np.full(int(m.sum()), 2025)])
    dd = np.concatenate([w["date_days"], w["date_days"][m] + 400])
    ctx2 = blend_ctx(b2)
    grid = np.concatenate([w["seq_programs"]["E2"][stat], w["seq_programs"]["E2"][stat][m]])
    pooled, _ = learned_blend(b2["mix"], grid, b2["y"], dd, b2["season"], ctx2, stat, cap=2025)
    reg = regular_flag(b2["game_id"])
    sq = seqc_grid(b2["a0_int"], pooled, reg)
    same = bool(np.array_equal(sq[:n], arms["SEQc"]))
    return [
        check(
            "appending later rows leaves every earlier SEQc row unchanged",
            {"rows_compared": n, "appended": int(m.sum())},
            "bit-identical",
            same,
        )
    ]


# --------------------------------------------------------------------------- permutation null
def null_draw(
    stat: str,
    b: dict[str, Any],
    date_days: np.ndarray,
    ctx: pd.DataFrame,
    seq199: np.ndarray,
    seed: int,
    crps_full: np.ndarray,
    crps_a0: np.ndarray,
    cap: int = REPO_CAP,
) -> dict[str, Any]:
    """One permutation draw: the SEQ forecasts permuted among the players of each date, blend re-learned, SEQc mixed 50/50 with FULL. Returns per-season
    means of AVG_perm - FULL and SEQc_perm - A0."""
    perm = within_date_perm(date_days, seed)
    reg = regular_flag(b["game_id"])
    pooled, _, scored = learned_blend_scored(
        b["mix"], seq199[perm], b["y"], date_days, b["season"], ctx, stat, cap
    )
    sq = seqc_grid(b["a0_int"], pooled, reg)
    avg = mix_half(sq, b["full_int"])
    d_avg = crps_from_quantiles(avg.astype(float), b["y"]) - crps_full
    d_seq = crps_from_quantiles(sq.astype(float), b["y"]) - crps_a0
    ss = [int(s) for s in np.unique(b["season"])]
    # Rows before the blend's first fit weigh 0.5 / 0.5 whatever the member is (the first 30 days of the first season): with a permuted member those
    # rows are garbage by construction, so the null statistic is taken on the rows the walk actually scored (all of the held-out season's rows are).
    return {
        "seasons": ss,
        "avg_minus_full": [float(d_avg[(b["season"] == s) & scored].mean()) for s in ss],
        "seqc_minus_a0": [float(d_seq[(b["season"] == s) & scored].mean()) for s in ss],
        "n_scored": [int(((b["season"] == s) & scored).sum()) for s in ss],
    }


def run_null(
    k: int = NULL_K,
    tag: str = "select",
    world: tuple[dict[str, Any], np.ndarray, pd.DataFrame, np.ndarray] | None = None,
    cache: Path | None = None,
    cap: int = REPO_CAP,
) -> dict[str, Any]:
    """K permutation draws for pts (resumable, one small file per draw). ``world`` = (b, date_days, ctx, seq199) or None for the stored 2023/24 world."""
    cache = cache or CACHE
    cache.mkdir(parents=True, exist_ok=True)
    if world is None:
        w = stored_world()
        b, date_days, ctx, seq199 = (
            w["base"][STAT_PRIMARY],
            w["date_days"],
            w["ctx"],
            w["seq_programs"]["E2"][STAT_PRIMARY],
        )
    else:
        b, date_days, ctx, seq199 = world
    crps_full = crps_from_quantiles(b["full_int"].astype(float), b["y"])
    crps_a0 = crps_from_quantiles(b["a0_int"].astype(float), b["y"])
    draws: list[dict[str, Any]] = []
    for s in range(k):
        f = cache / f"null_{STAT_PRIMARY}_{tag}_s{s}.npz"
        if f.exists():
            z = np.load(f)
            draws.append(
                {
                    "seasons": z["seasons"].tolist(),
                    "avg_minus_full": z["avg_minus_full"].tolist(),
                    "seqc_minus_a0": z["seqc_minus_a0"].tolist(),
                }
            )
            continue
        t0 = time.time()
        d = null_draw(STAT_PRIMARY, b, date_days, ctx, seq199, s, crps_full, crps_a0, cap)
        np.savez(f, **{kk: np.array(v) for kk, v in d.items()})
        draws.append(d)
        _log(
            f"null {tag}: draw {s + 1}/{k} in {time.time() - t0:.1f}s  AVG_perm-FULL {d['avg_minus_full']}"
        )
    return summarize_null(draws)


def summarize_null(draws: list[dict[str, Any]]) -> dict[str, Any]:
    seasons = draws[0]["seasons"]
    out: dict[str, Any] = {"k": len(draws), "seasons": seasons, "per_season": {}}
    for i, s in enumerate(seasons):
        v = np.array([d["avg_minus_full"][i] for d in draws])
        u = np.array([d["seqc_minus_a0"][i] for d in draws])
        out["per_season"][str(s)] = {
            "mean": float(v.mean()), "sd": float(v.std(ddof=1)), "min": float(v.min()), "max": float(v.max()),
            "q01": float(np.quantile(v, 0.01)), "q05": float(np.quantile(v, 0.05)),
            "seqc_minus_a0_mean": float(u.mean()), "seqc_minus_a0_sd": float(u.std(ddof=1)),
        }  # fmt: skip
    out["pooled_within_season_sd"] = float(
        np.sqrt(np.mean([out["per_season"][str(s)]["sd"] ** 2 for s in seasons]))
    )
    return out


def run_null_gate(k: int = NULL_K) -> dict[str, Any]:
    """Pre-freeze sizing of the noise guard on 2023/24 (stored arrays only): the null, where the real E2 and the shuffled-input E2S sit in it."""
    with WE.heavy():
        w = stored_world()
        st = STAT_PRIMARY
        b = w["base"][st]
        null = run_null(k, "select", (b, w["date_days"], w["ctx"], w["seq_programs"]["E2"][st]))
        arms = build_arms(st, b, w["date_days"], w["ctx"], seq_199(w, st))
        crps_full = crps_from_quantiles(arms["FULL"].astype(float), b["y"])
        _, _, scored = learned_blend_scored(
            b["mix"], w["seq_programs"]["E2"][st], b["y"], w["date_days"], b["season"], w["ctx"], st
        )
        pos: dict[str, Any] = {}
        for name in ("AVG", "AVG_S"):
            d = crps_from_quantiles(arms[name].astype(float), b["y"]) - crps_full
            for s in SELECT_SEASONS:
                v = float(d[(b["season"] == s) & scored].mean())
                nu = null["per_season"][str(s)]
                pos.setdefault(name, {})[str(s)] = {
                    "avg_minus_full": v,
                    "z_vs_null": (v - nu["mean"]) / nu["sd"],
                    "null_mean": nu["mean"],
                    "null_sd": nu["sd"],
                }
        out = {
            "null": null,
            "positions": pos,
            "sigma0_pre": null["pooled_within_season_sd"],
            "k": k,
            "mult": NULL_SD_MULT,
        }
    write_json(OUT_DIR / "null_gate.json", out)
    return out


# --------------------------------------------------------------------------- the held-out season: regenerate A0 / FULL, build the world, score
STAGE2_DIR = OUT_DIR / "stage2"
EXPECTED_AVG_MINUS_FULL = -0.007  # the 2023/24 figure (-0.0100 / -0.0098) shrunk for selection among six constructions and the 50/50 weight (T471 post-hoc)
SIGMA0_PRE = 0.00010  # frozen: the larger per-season sd of the K = 50 permutation null on 2023/24 (0.000103 / 0.000012), docs/prereg/SEQ_FULL_MIX.md
MIN_ROWS = 25_000  # data gate on the held-out season's pts rows (T473: 28,173 rows / 1,321 games)
REQUIRED = ("game_id", "player_id", "season", "y", "minutes", "starter10", "team_id", "n_prior", "team_game_no", "has_report", "n_rep_out", "tip_hour", "a0_int", "full_int", "mix", "full_mix")  # fmt: skip
META_COLS = (
    "game_id",
    "player_id",
    "game_date",
    "team_id",
    "n_prior",
    "team_game_no",
    "has_report",
    "n_rep_out",
)
EXPORT25_META = ROOT / "data/colab/seq_full_mix/seq_ensemble_cur25_meta.parquet"
STORED_META = ROOT / "data/colab/seq_ensemble/seq_ensemble_cur_meta.parquet"


def forecast_members(
    fr: pl.DataFrame, cfg: Any, season: int, log: bool = True
) -> dict[str, dict[str, np.ndarray]]:
    """A0 and FULL continuous mixture grids (all four stats) for ONE season by the production walk (month blocks, every earlier row trains) with the
    FULL construction of RECENT_ROLE_FULL (heads + `pi` with the five recent-role columns). `w_q` and the A0 `pi` come from production's `collect_pi` on
    this very walk. Same code as `recent_role_full_eval.stage2_pipeline`, for all stats, no guard arms."""
    ts = (season,)
    pic = collect_pi(fr, cfg, stats=STATS, test_seasons=ts)
    pic["row_gid"] = pic["row_gid"].astype(str)
    pic["row_pid"] = pic["row_pid"].astype(np.int64)
    _log(f"members {season}: pi (FULL columns)")
    pi_full = PR.align_to_stored(
        PR.walk_pi(fr, PR.CAND_COLS, cfg, test_seasons=ts, log=log), pic["row_gid"], pic["row_pid"]
    )
    out: dict[str, dict[str, np.ndarray]] = {}
    for st in STATS:
        _log(f"members {season}: {st} heads A0")
        h0 = F.collect_heads(fr, st, cfg, F.heads_names(st, "A0"), test_seasons=ts, log=log)
        _log(f"members {season}: {st} heads FULL")
        h1 = F.collect_heads(fr, st, cfg, F.heads_names(st, "R"), test_seasons=ts, log=log)
        if not (
            F.same_rows(h0, h1)
            and np.array_equal(h0["row_gid"].astype(str), pic["row_gid"])
            and np.array_equal(h0["row_pid"].astype(np.int64), pic["row_pid"])
        ):
            raise RuntimeError(f"{st}: A0 / FULL / pi rows differ")
        out[st] = {
            "game_id": h0["row_gid"].astype(str), "player_id": h0["row_pid"].astype(np.int64), "season": h0["row_season"].astype(np.int64),
            "y": h0["row_y"].astype(float), "minutes": h0["row_minutes"].astype(float), "starter10": h0["row_starter10"].astype(float),
            "mix": arm_grid("mixture", h0, PR.candidate_pic(pic, pic["row_pi"]), st),
            "full_mix": arm_grid("mixture", h1, PR.candidate_pic(pic, pi_full), st),
        }  # fmt: skip
    return out


def members_vs_stored(
    members: dict[str, dict[str, np.ndarray]], w: dict[str, Any], season: int, label: str
) -> list[dict[str, Any]]:
    """The regenerated season must equal the stored arrays (A0 and FULL, integer grids exactly; continuous grids reported)."""
    tests: list[dict[str, Any]] = []
    for st in STATS:
        b, m = w["base"][st], members[st]
        sel = b["season"] == season
        rows_eq = bool(
            np.array_equal(b["game_id"][sel], m["game_id"])
            and np.array_equal(b["player_id"][sel], m["player_id"])
            and np.array_equal(b["y"][sel], m["y"])
        )
        a0_eq = rows_eq and bool(
            np.array_equal(b["a0_int"][sel], to_integer_support(m["mix"]).astype(np.int16))
        )
        fu_eq = rows_eq and bool(
            np.array_equal(b["full_int"][sel], to_integer_support(m["full_mix"]).astype(np.int16))
        )
        dmax = float(np.abs(b["mix"][sel] - m["mix"]).max()) if rows_eq else float("inf")
        tests.append(check(f"[{label}] {st} {season}: regenerated rows, A0 and FULL integer grids equal the stored arrays", {"rows_equal": rows_eq, "a0_int_equal": a0_eq, "full_int_equal": fu_eq, "max_abs_diff_cont_a0": dmax, "n": int(sel.sum())}, "all equal", rows_eq and a0_eq and fu_eq))  # fmt: skip
    return tests


def target_base(
    members: dict[str, dict[str, np.ndarray]], meta_path: Path
) -> tuple[dict[str, dict[str, Any]], np.ndarray]:
    """Per stat the base dict (same keys as the stored world) for the held-out rows, covariates joined from the export meta, tips from the schedule."""
    from nba.features.game_tipoff import build_game_tipoff

    meta = (
        pl.read_parquet(meta_path)
        .select(*META_COLS)
        .with_columns(pl.col("player_id").cast(pl.Int64), pl.col("game_date").cast(pl.Date))
    )
    tips = build_game_tipoff().select(
        "game_id",
        (pl.col("tip_et").dt.hour() + pl.col("tip_et").dt.minute() / 60.0).alias("tip_hour"),
    )
    out: dict[str, dict[str, Any]] = {}
    date_days: np.ndarray | None = None
    for st, m in members.items():
        j = (
            pl.DataFrame({"game_id": m["game_id"], "player_id": m["player_id"]})
            .join(meta, on=["game_id", "player_id"], how="left")
            .join(tips, on="game_id", how="left")
        )
        if j["team_id"].null_count() or j["game_date"].null_count():
            raise RuntimeError(f"{st}: scored rows missing from the SEQ export meta")
        b: dict[str, Any] = {
            k: m[k]
            for k in (
                "game_id",
                "player_id",
                "season",
                "y",
                "minutes",
                "starter10",
                "mix",
                "full_mix",
            )
        }
        b["team_id"] = j["team_id"].to_numpy()
        b["n_prior"] = j["n_prior"].to_numpy()
        b["team_game_no"] = j["team_game_no"].to_numpy()
        b["has_report"] = j["has_report"].to_numpy()
        b["n_rep_out"] = j["n_rep_out"].to_numpy()
        b["tip_hour"] = j["tip_hour"].fill_null(float("nan")).to_numpy()
        b["a0_int"] = to_integer_support(m["mix"]).astype(np.int16)
        b["full_int"] = to_integer_support(m["full_mix"]).astype(np.int16)
        out[st] = b
        dd = j["game_date"].to_numpy().astype("datetime64[D]").astype(np.int64)
        if date_days is not None and not np.array_equal(dd, date_days):
            raise RuntimeError(f"{st}: rows are not the pts rows")
        date_days = dd
    assert date_days is not None
    return out, date_days


def combine_world(
    w: dict[str, Any], tgt: dict[str, dict[str, Any]], tgt_days: np.ndarray, below: int
) -> dict[str, Any]:
    """Stored rows with season < ``below`` followed by the held-out rows. SEQ grids are attached afterwards from the run directory."""
    keep = w["base"][STAT_PRIMARY]["season"] < below
    base: dict[str, dict[str, Any]] = {}
    for st in STATS:
        old = w["base"][st]
        base[st] = {k: np.concatenate([old[k][keep], tgt[st][k]]) for k in REQUIRED}
    days = np.concatenate([w["date_days"][keep], tgt_days])
    if not np.all(np.diff(days) >= 0):
        raise RuntimeError("combined world is not in date order")
    return {"base": base, "date_days": days, "ctx": blend_ctx(base[STAT_PRIMARY])}


def attach_seq(
    world: dict[str, Any],
    run_dir: Path,
    progs: tuple[str, ...],
    max_season: int,
    key_from: dict[str, str] | None = None,
) -> None:
    world["seq_programs"] = {
        p: load_seq_run(run_dir, p, world["base"], max_season, (key_from or {}).get(p))
        for p in progs
    }


def score_world(
    world: dict[str, Any],
    target: int,
    cap: int,
    null_k: int,
    null_cache: Path,
    seasons_report: tuple[int, ...],
) -> dict[str, Any]:
    dk_seasons = tuple(int(s) for s in pl.read_parquet(E.DK_CACHE)["season"].unique().to_list())
    res: dict[str, Any] = {"target": target, "stats": {}}
    for st in STATS:
        b = world["base"][st]
        seqs = {p: g[st] for p, g in world["seq_programs"].items()}
        _log(f"score {st}: arms")
        arms = build_arms(st, b, world["date_days"], world["ctx"], seqs, cap)
        rows = contrast_rows(arms, b["y"], b["game_id"], b["season"], seasons_report)
        guards = arm_guards(st, b, arms["AVG"], (target,), dk_seasons)
        res["stats"][st] = {
            "contrasts": rows, "guards_AVG": guards,
            "crps": {k: float(crps_from_quantiles(v.astype(float), b["y"])[b["season"] == target].mean()) for k, v in arms.items()},
        }  # fmt: skip
        if st == STAT_PRIMARY:
            res["slices"] = {
                k: {
                    "n": guards[str(target)]["slices"][k]["n"],
                    "AVG_minus_A0": guards[str(target)]["slices"][k].get("point"),
                }
                for k in guards[str(target)]["slices"]
            }
            res["null"] = run_null(
                null_k,
                f"target{target}",
                (b, world["date_days"], world["ctx"], seqs["E2"]),
                null_cache,
                cap,
            )
        world.setdefault("_arms", {})[st] = {
            k: arms[k] for k in ("A0", "FULL", "AVG")
        }  # kept for the post-verdict descriptive step only
        del arms
    return res


def decide(res: dict[str, Any], gate_ok: bool, target: int, sigma0_pre: float) -> dict[str, Any]:
    """The frozen pass rule (docs/prereg/SEQ_FULL_MIX.md) on the held-out season. Primary: pts AVG - FULL <= -0.005, CI upper < 0, p <= 0.05 (m = 1)."""
    t = str(target)
    c = res["stats"][STAT_PRIMARY]["contrasts"]
    prim, a0, pl_, mx, sh = c["AVG_minus_FULL"][t], c["AVG_minus_A0"][t], c.get("AVG_minus_AVG_P", {}).get(t), c["AVG_minus_AVG_A0"][t], c.get("AVG_minus_AVG_S", {}).get(t)  # fmt: skip
    nu = res["null"]["per_season"][t]
    sigma = max(sigma0_pre, nu["sd"])
    items = {
        "primary_AVG_minus_FULL": bool(
            prim["delta"] <= FLOOR and prim["ci_hi"] < 0 and prim["p"] <= 0.05
        ),
        "AVG_minus_A0": bool(a0["delta"] <= FLOOR and a0["ci_hi"] < 0),
        "router_g2_guards_pts": bool(
            res["stats"][STAT_PRIMARY]["guards_AVG"][t]["guards_all_pass"]
        ),
        "placebo_AVG_minus_AVG_P": bool(pl_ is not None and pl_["delta"] <= PLACEBO_MARGIN),
        "shuffled_input_AVG_minus_AVG_S": bool(sh is not None and sh["delta"] <= PLACEBO_MARGIN),
        "mixture_control_AVG_minus_AVG_A0": bool(mx["delta"] <= MIXTURE_MARGIN),
        "null_real_beyond_permutation": bool(prim["delta"] <= nu["mean"] - NULL_SD_MULT * sigma),
        "gates": bool(gate_ok),
    }
    return {
        "pass": bool(all(items.values())), "items": items, "primary": prim, "null": {**nu, "sigma_used": sigma, "sigma0_pre": sigma0_pre},
        "floor": FLOOR, "note": "A pass is a production-change PROPOSAL only (Devin's decision); a miss is read against the stated expectation, not as a broken pipeline.",
    }  # fmt: skip


def secondary_family(res: dict[str, Any], target: int) -> list[dict[str, Any]]:
    """Declared secondary family (m = 7, BH q = 0.05, descriptive; cannot rescue or fail the rule): pts AVG - A0, and AVG - FULL / AVG - A0 for reb, ast, fg3m."""
    t = str(target)
    cells = [(STAT_PRIMARY, "AVG_minus_A0")] + [
        (s, k) for s in STATS if s != STAT_PRIMARY for k in ("AVG_minus_FULL", "AVG_minus_A0")
    ]
    rows = [{"stat": s, "contrast": k, **res["stats"][s]["contrasts"][k][t]} for s, k in cells]
    adj = bh_adjust(np.array([r["p"] for r in rows]))
    for r, a in zip(rows, adj, strict=True):
        r["p_bh"] = float(a)
        r["clears_floor"] = bool(r["delta"] <= FLOOR and r["ci_hi"] < 0)
    return rows


def require_unspent(out_dir: Path, resume: bool) -> None:
    if (out_dir / "results.json").exists():
        raise RuntimeError(
            f"{out_dir / 'results.json'} exists: the 2025 touch is spent (one run, no reruns); Devin decides what happens next"
        )
    if (out_dir / "touch_started.json").exists() and not resume:
        raise RuntimeError(
            f"{out_dir / 'touch_started.json'} exists: a started touch is consumed; resume only with --resume (same sha, same code)"
        )


def t473_identity(members: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    """The regenerated 2025 pts A0 / FULL must be the forecasts T473 scored: same row count, same integer CRPS (logged in stage2/results.json)."""
    logged = json.loads(T473.read_text())["scores"]["rows"]
    ref = {r["arm"]: r for r in logged if "crps_int" in r}
    m = members[STAT_PRIMARY]
    out: dict[str, Any] = {"n": int(len(m["y"])), "logged_n": int(ref["A0"]["n_rows"])}
    for arm, key in (("A0", "mix"), ("FULL", "full_mix")):
        out[arm] = float(
            crps_from_quantiles(to_integer_support(m[key]).astype(float), m["y"]).mean()
        )
        out[f"{arm}_logged"] = float(ref[arm]["crps_int"])
    return check("[T473 identity] regenerated 2025 pts A0 and FULL equal the forecasts T473 scored", out, f"n equal; |diff| <= {EXACT_TOL}", out["n"] == out["logged_n"] and abs(out["A0"] - out["A0_logged"]) <= EXACT_TOL and abs(out["FULL"] - out["FULL_logged"]) <= EXACT_TOL)  # fmt: skip


def _body(
    sha: str | None,
    target: int,
    cap_ctx: Callable[[], Any],
    out_dir: Path,
    build: Callable[[], tuple[pl.DataFrame, Any, dict[str, Any]]],
    seq_run: Path,
    meta_path: Path,
    stored_seq: bool,
    null_k: int,
    resume: bool = False,
) -> dict[str, Any]:
    """Gate A (stored season regenerated under the 2024 cap) -> raise the cap -> touch marker -> rebuild -> gate B -> data gate -> expected value written
    -> the held-out season's members -> world -> score -> verdict. ``stored_seq``: the SEQ files are the stored ENSEMBLE ones (rehearsal)."""
    t0 = time.time()
    if target > REPO_CAP and sha is None:
        raise RuntimeError(
            "a held-out-season run needs the frozen --sha and the committed holdout row"
        )
    w = stored_world()
    progs = ("E2", "E2S") if stored_seq else (*PROGS_PRIMARY, *PROGS_GUARD)
    key_from = {"E2S": "E2"} if stored_seq else None
    gate_season = REPO_CAP if target > REPO_CAP else target
    tests: list[dict[str, Any]] = []
    if target > REPO_CAP:
        fr, cfg, _ = build()
        tests += members_vs_stored(
            forecast_members(fr, cfg, gate_season), w, gate_season, "gate A, cap 2024"
        )
        del fr
        if not all(t["ok"] for t in tests):
            return _stop(out_dir, sha, "gate A (2024 regeneration, cap 2024)", tests, False)
    with cap_ctx():
        if target > REPO_CAP:
            F.assert_cap(target)
            WE.write_json(out_dir / "touch_started.json", {"sha256": sha, "season": target, "head": PR._git("rev-parse", "HEAD").strip(), "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "seq_run": str(seq_run)})  # fmt: skip
        fr, cfg, _ = build()
        if target > REPO_CAP:
            tests += members_vs_stored(
                forecast_members(fr, cfg, gate_season), w, gate_season, f"gate B, cap {target}"
            )
            dg = F.data_gate_2025(fr, target)
            tests += dg
            if not all(t["ok"] for t in tests):
                return _stop(out_dir, sha, "gate B / data gate", tests, True)
            WE.write_json(out_dir / "expected.json", {"expected_avg_minus_full_pts": EXPECTED_AVG_MINUS_FULL, "written_before": "any 2025 SEQ forecast is read or scored", "basis": "2023/24 -0.0100 / -0.0098 shrunk for selection"})  # fmt: skip
        members = forecast_members(fr, cfg, target)
        if target > REPO_CAP:
            tests.append(t473_identity(members))
            if not tests[-1]["ok"]:
                return _stop(out_dir, sha, "T473 identity", tests, True)
        else:  # rehearsal: the regenerated stand-in season must be the stored one
            tests += members_vs_stored(members, w, target, "rehearsal")
        tgt, tgt_days = target_base(members, meta_path)
        world = combine_world(w, tgt, tgt_days, target)
        attach_seq(world, seq_run, progs, target, key_from)
        res = score_world(
            world,
            target,
            target if target > REPO_CAP else REPO_CAP,
            null_k,
            out_dir / "null_cache",
            tuple(s for s in (2023, 2024, target) if s <= target),
        )
    if (
        target <= REPO_CAP
    ):  # rehearsal: the scoring path must reproduce the gate's numbers for the stand-in season
        c = res["stats"][STAT_PRIMARY]["contrasts"]
        for key, ref in (
            ("AVG_minus_FULL", T471_AVG_MINUS_FULL),
            ("AVG_minus_A0", T471_AVG_MINUS_A0),
        ):
            d = c[key][str(target)]["delta"]
            tests.append(
                check(
                    f"[rehearsal] pts {target} {key} equals the stored gate value",
                    {"delta": d, "stored": ref[target]},
                    f"<= {EXACT_TOL}",
                    abs(d - ref[target]) <= EXACT_TOL,
                )
            )
    verdict = decide(res, all(t["ok"] for t in tests), target, SIGMA0_PRE)
    rerun: dict[str, Any] = {}
    if not stored_seq:  # reported, not a criterion: does the Colab rerun reproduce the stored E2 on the select seasons?
        c = res["stats"][STAT_PRIMARY]["contrasts"]
        rerun = {str(s): {"AVG_minus_A0_rerun": c["AVG_minus_A0"][str(s)]["delta"], "AVG_minus_A0_stored": T471_AVG_MINUS_A0[s], "AVG_minus_FULL_rerun": c["AVG_minus_FULL"][str(s)]["delta"], "AVG_minus_FULL_stored": T471_AVG_MINUS_FULL[s]} for s in SELECT_SEASONS}  # fmt: skip
    out = {
        "sha256": sha,
        "target": target,
        "scores": res,
        "verdict": verdict,
        "secondary": secondary_family(res, target),
        "rerun_reproduction": rerun,
        "gate": tests,
        "seconds": time.time() - t0,
    }
    WE.write_json(out_dir / "results.json", out)
    descriptive_after_verdict(world, target, out_dir)
    return out


def descriptive_after_verdict(world: dict[str, Any], target: int, out_dir: Path) -> None:
    """Descriptive outputs (rule section "Descriptive outputs"): only after results.json is written; a failure here writes descriptive_error.txt and never
    touches the verdict."""
    from research.eval import seq_full_mix_descriptive as D

    try:
        D.run_descriptive(
            world,
            target,
            out_dir,
            out_dir / "results.json",
            "nba.duckdb",
            ROOT / "data/odds/odds_history.duckdb",
            ROOT / "data/ops/recent_role_holdout_cache/run/blocks",
        )
    except Exception as e:  # noqa: BLE001
        (out_dir / "descriptive_error.txt").write_text(f"{type(e).__name__}: {e}\n")
        _log(f"descriptive step failed (the verdict is unaffected): {e}")


def _stop(
    out_dir: Path, sha: str | None, stage: str, tests: list[dict[str, Any]], consumed: bool
) -> dict[str, Any]:
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    out = {
        "sha256": sha,
        "stopped_at": stage,
        "gate": tests,
        "gate_ok": False,
        "touch_consumed": consumed,
    }
    WE.write_json(out_dir / "gate_failed.json", out)
    _log(f"STOPPED at {stage}; touch_consumed={consumed}")
    return out


def check_seq_run(seq_run: Path, sha: str) -> dict[str, Any]:
    """Before anything reads a 2025 outcome: the Colab artifacts are this rule's full-budget run (sha, seeds, width, test seasons, no smoke)."""
    cfg = json.loads((seq_run / "config.json").read_text())
    want = {
        "rule_sha256": sha,
        "seeds": [0, 1, 2],
        "hidden": 64,
        "test_seasons": [2023, 2024, 2025],
        "season_2025_used": True,
    }
    bad = {k: (cfg.get(k), v) for k, v in want.items() if cfg.get(k) != v}
    if cfg.get("smoke"):
        bad["smoke"] = (cfg.get("smoke"), None)
    if bad:
        raise RuntimeError(f"the Colab run is not this rule's full-budget run: {bad}")
    return cfg


def run_held_out(
    sha: str, seq_run: Path, resume: bool = False, null_k: int = NULL_K
) -> dict[str, Any]:
    """THE one 2025 touch. Refuses unless the rule is frozen and the Devin-confirmed holdout row is committed; unspent; the Colab artifacts present."""
    RULE.require_ready(sha)
    require_unspent(STAGE2_DIR, resume)
    for p in (*PROGS_PRIMARY, *PROGS_GUARD):
        if not (seq_run / f"oof_{p}.npz").exists():
            raise RuntimeError(f"{seq_run / f'oof_{p}.npz'} missing: run the Colab job first")
    check_seq_run(seq_run, sha)
    from research.eval.recent_role_holdout import preimport, season_cap

    preimport()
    with WE.heavy():
        out = _body(
            sha,
            HOLDOUT_SEASON,
            lambda: season_cap(HOLDOUT_SEASON),
            STAGE2_DIR,
            F.build_frame,
            seq_run,
            EXPORT25_META,
            False,
            null_k,
            resume,
        )
    if "verdict" in out:
        REPORT_MD.write_text(render(out))
    return out


def rehearse(null_k: int = 3) -> dict[str, Any]:
    """The whole held-out path with season 2024 standing in for 2025: stored SEQ files, no sha, no log row, the cap never raised. Scratch output in
    OUT_DIR/rehearsal; the 2024 contrasts must equal the gate's (T471)."""
    memo: list[Any] = []

    def build_once() -> tuple[pl.DataFrame, Any, dict[str, Any]]:
        if not memo:
            memo.append(F.build_frame())
        return memo[0]

    out_dir = OUT_DIR / "rehearsal"
    with WE.heavy():
        out = _body(
            None, REPO_CAP, nullcontext, out_dir, build_once, SEQ_STORED, STORED_META, True, null_k
        )
    for t in out.get("gate", []):
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return out


def render(out: dict[str, Any]) -> str:
    v, sc, t = out["verdict"], out["scores"], str(out["target"])
    f = lambda x, nd=5: "" if x is None else f"{x:.{nd}f}"  # noqa: E731
    lines = [f"# {ID} results (season {out['target']}, one run)", "", f"Rule sha256 {str(out['sha256'])[:8]}. Verdict: **{'PASS' if v['pass'] else 'NOT A PASS'}** (a pass is a proposal; Devin decides).", ""]  # fmt: skip
    lines += ["| item | ok |", "|---|---|", *[f"| {k} | {x} |" for k, x in v["items"].items()], "", "| stat | contrast | n rows | n games | delta | CI | p |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    for st in STATS:
        for k, d in sc["stats"][st]["contrasts"].items():
            r = d.get(t)
            if r:
                lines.append(
                    f"| {st} | {k} | {r['n']} | {r['n_games']} | {f(r['delta'])} | [{f(r['ci_lo'])}, {f(r['ci_hi'])}] | {f(r['p'], 4)} |"
                )
    lines += [
        "",
        "```json",
        json.dumps({"null": v["null"], "secondary": out["secondary"]}, indent=1, default=float),
        "```",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("cmd", choices=["gate", "null", "rehearse", "run"])
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--k", type=int, default=NULL_K)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--seq-run", default=None)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        r = run_gate(a.allow_draft)
        return 0 if r["gate_ok"] else 1
    if a.cmd == "null":
        print(json.dumps(run_null_gate(a.k), indent=1, default=float))
    if a.cmd == "rehearse":
        r = rehearse(a.k if a.k != NULL_K else 3)
        return 0 if all(t["ok"] for t in r.get("gate", [])) else 1
    if a.cmd == "run":
        if not a.sha or not a.seq_run:
            raise SystemExit("run needs --sha and --seq-run")
        r = run_held_out(a.sha, Path(a.seq_run), a.resume, a.k)
        print(json.dumps(r.get("verdict", r), indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
