# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""REPLACEMENT_DELTA harness (rule: docs/prereg/REPLACEMENT_DELTA.md; DRAFT until frozen, and NOT FREEZABLE today: gate FAIL).
Model class: LightGBM context-residual heads (production) with four added as-of columns per stat head.

    .venv/bin/python -W ignore -m research.eval.replacement_delta_eval check                       # label-free preflight (no outcome array is loaded)
    .venv/bin/python -W ignore -m research.eval.replacement_delta_eval rehearse [--seeds 10]      # synthetic effect / null / harm worlds, row_y never loaded
    .venv/bin/python -W ignore -m research.eval.replacement_delta_eval run --sha <64 hex>         # real outcomes; refuses unless frozen AND pinned

Run from the repository root of the checkout holding ``nba.duckdb`` (``configs/`` and ``data/schedule`` are read by relative path); the data
roots default to ``$NBA_ROOT`` (default: the main checkout) and are read-only.

* ``check``: recomputes every pinned count and input hash and the label-free equalities (production OUT-set tie, vac_min parity, planted-future on
  real rows, priors pin). ``row_y`` is never loaded.
* ``rehearse``: the scoring path (paired game-clustered bootstrap, BH, words, guards) on SYNTHETIC outcomes drawn from the stored A0 integer grid.
  Three worlds: ``effect`` (a planted shift proportional to the quality-weighted redistribution column), ``null`` (no effect, a candidate that claims one),
  ``harm`` (no effect, a candidate that shifts every row by noise). Candidates are planted shifts, not refits (a refit needs labels).
* ``run``: order = rule frozen and harness pinned -> preflight (no label) -> CONTROL heads walk (stored A0 must reproduce to 1e-6, else refuse before
  any other arm) -> V0 / RD / PQ walks -> integer-CRPS scoring. Nothing here touches season 2025 (``MAX_SEASON`` assert in every loader).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.props import repl_delta as RD
from research.eval import replacement_delta_gate as G
from research.eval import replacement_delta_rule as rule

MAX_SEASON = 2024
SEASONS = (2023, 2024)  # 2023 = select (must pass), 2024 = report (must pass)
STATS = RD.STATS
FLOOR = -0.005  # RD minus A0, integer CRPS, affected rows
CLOSE_AT = -0.002  # a season whose point estimate is above this closes the stat
NOHARM = 0.001  # upper CI of RD - A0 on ALL rows
T_C: float | None = (
    None  # G2 / Q margin: RD - PQ and RD - V0 must be <= -T_C. None until the K >= 30 placebo null is run and the value is written into the rule; `run` refuses while None.
)
SLICE_TOL = 0.01
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01
MIN_SLICE_N = 300
REPRO_TOL = 1e-6
N_BOOT, SEED = 5000, 2026
BH_Q = 0.05
ARM_COLS: dict[str, tuple[str, ...]] = {
    "RD": ("qv", "rd"),
    "V0": ("v0", "v0d"),
    "PQ": ("pq_qv", "pq_rd"),
}
CACHE = Path("data/ops/replacement_delta_cache")
LT = G.LT
OUT_REAL = Path("reports/replacement_delta")
OUT_REHEARSAL = Path("reports/prereg_replacement_delta/rehearsal")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def arm_columns(arm: str, stat: str) -> list[str]:
    """Columns an arm adds to the ``stat`` head (the same four everywhere: two stat-specific, the pool share, the OUT count)."""
    a, b = ARM_COLS[arm]
    return [f"{a}_{stat}", f"{b}_{stat}", "share", "n_out"]


# ------------------------------------------------------------------------------------ statistics (the RETURNING_TEAMMATE_2 code, copied so this rule pins its own)


class Boot:
    """Game-clustered bootstrap: ratio of resampled per-game sums, one weight matrix per season (rows of the same game move together)."""

    def __init__(self, gid: np.ndarray, seed: int = SEED, b: int = N_BOOT) -> None:
        u, self.code = np.unique(gid, return_inverse=True)
        self.G, self.B = len(u), b
        self.W = (
            np.random.default_rng(seed)
            .multinomial(self.G, np.full(self.G, 1.0 / self.G), size=b)
            .astype(float)
        )

    def _rep(self, v: np.ndarray, m: np.ndarray) -> np.ndarray:
        s = np.bincount(self.code[m], weights=v[m], minlength=self.G)
        c = np.bincount(self.code[m], minlength=self.G).astype(float)
        den = self.W @ c
        return (self.W @ s) / np.where(den > 0, den, np.nan)

    def ci(self, v: np.ndarray, m: np.ndarray) -> dict[str, float]:
        if int(m.sum()) == 0:
            return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
        lo, hi = np.nanpercentile(self._rep(v, m), [2.5, 97.5])
        return {
            "point": float(v[m].sum() / m.sum()),
            "lo": float(lo),
            "hi": float(hi),
            "n": int(m.sum()),
        }

    def p_le0(self, v: np.ndarray, m: np.ndarray) -> float:
        """One-sided bootstrap p for 'mean < 0' (share of replicates >= 0), floored at 1 / B."""
        return float(max(np.nanmean(self._rep(v, m) >= 0.0), 1.0 / self.B))


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    from research.eval import pi_recent_role_eval as PR

    return np.asarray(PR.crps_int(qi, y))


# ------------------------------------------------------------------------------------ scoring


def slice_masks(
    ft: dict[str, np.ndarray], stat: str, cuts: dict[str, float]
) -> dict[str, np.ndarray]:
    """Declared slices of the AFFECTED rows. ``ft``: arrays aligned to the scored rows (n_out, last_starter, team_game_no, gid, qv_<stat>)."""
    aff = ft["affected"]
    return {
        "n_out==1": aff & (ft["n_out"] == 1),
        "n_out>=2": aff & (ft["n_out"] >= 2),
        "starter(last_starter==1)": aff & (ft["last_starter"] == 1),
        "bench(last_starter==0)": aff & (ft["last_starter"] == 0),
        "first15(team_game_no<15)": aff & (ft["team_game_no"] < 15),
        "playoffs(game_id 004)": aff & np.array([str(g).startswith("004") for g in ft["gid"]]),
        "top25_abs_qv(fixed dev cut)": aff & (np.abs(ft[f"qv_{stat}"]) >= cuts.get(stat, np.inf)),
    }


def score_arms(
    qi: dict[str, dict[str, np.ndarray]],
    y: dict[str, np.ndarray],
    gid: np.ndarray,
    season: np.ndarray,
    ft: dict[str, np.ndarray],
    cuts: dict[str, float],
    seed: int = SEED,
) -> dict[str, Any]:
    """Contrasts per stat and season. ``qi[stat][arm]`` integer 199-quantile grids; arms: ``A0`` and ``RD`` (``V0``, ``PQ`` when present)."""
    from nba.props.context_residual import CRPS_TAUS

    out: dict[str, Any] = {}
    for stat in qi:
        per: dict[str, Any] = {}
        sc = {arm: crps_int(q, y[stat]) for arm, q in qi[stat].items()}
        sl = slice_masks(ft, stat, cuts)
        for s in SEASONS:
            m = season == s
            bt = Boot(gid[m], seed + s)
            ma = ft["affected"][m]
            d_rd = (sc["RD"] - sc["A0"])[m]
            block: dict[str, Any] = {
                "n_rows": int(m.sum()),
                "n_affected": int(ma.sum()),
                "n_games": int(np.unique(gid[m]).size),
                "RD_minus_A0_affected": bt.ci(d_rd, ma),
                "RD_minus_A0_all": bt.ci(d_rd, np.ones(int(m.sum()), bool)),
                "p_one_sided_affected": bt.p_le0(d_rd, ma),
            }
            if "PQ" in sc:
                block["RD_minus_PQ_affected"] = bt.ci((sc["RD"] - sc["PQ"])[m], ma)
            if "V0" in sc:
                block["RD_minus_V0_affected"] = bt.ci((sc["RD"] - sc["V0"])[m], ma)
                block["V0_minus_A0_affected"] = bt.ci((sc["V0"] - sc["A0"])[m], ma)
            cov: dict[str, list[float]] = {}
            for tau in COV_TAUS:
                j = int(np.argmin(np.abs(np.asarray(CRPS_TAUS) - tau)))
                cov[str(tau)] = [
                    float((y[stat][m] <= qi[stat][a][m, j]).mean()) for a in ("A0", "RD")
                ]
            block["coverage_A0_RD"] = cov
            sls: dict[str, Any] = {}
            for name, mask in sl.items():
                mm = mask[m]
                sls[name] = (
                    bt.ci(d_rd, mm)
                    if int(mm.sum()) >= MIN_SLICE_N
                    else {"n": int(mm.sum()), "reported": False}
                )
            block["slices"] = sls
            per[str(s)] = block
        out[stat] = per
    for s in SEASONS:  # BH over the stats (m = len(qi), 4 in the real run) within each season, on the one-sided affected-rows p
        names = list(out)
        adj = bh_adjust(np.array([out[st][str(s)]["p_one_sided_affected"] for st in names]))
        for st, a in zip(names, adj, strict=True):
            out[st][str(s)]["p_bh"] = float(a)
    return out


def decide(sc: dict[str, Any], t_c: float) -> dict[str, Any]:
    """The rule's words per stat from ``score_arms`` output. A stat PASSES only if BOTH seasons pass the primary (RD - A0 on affected rows <= FLOOR,
    CI upper < 0, BH-adjusted p <= BH_Q over the stats) and every guard holds in both seasons:
    G1 no harm (all rows), G2 RD - PQ <= -t_c, G3 coverage, G5 slices. PASS_QUALITY additionally needs RD - V0 <= -t_c in both seasons (the gain is
    the replacement subtraction, not the warm per-possession vacancy); otherwise PASS_WARM_ONLY. CLOSED = either season's point > CLOSE_AT.
    GUARD_FAIL = the primary passes and a guard fails. Else INCONCLUSIVE."""
    words: dict[str, str] = {}
    detail: dict[str, Any] = {}
    for st, per in sc.items():
        prim = {
            s: bool(
                per[str(s)]["RD_minus_A0_affected"]["point"] <= FLOOR
                and per[str(s)]["RD_minus_A0_affected"]["hi"] < 0
                and per[str(s)]["p_bh"] <= BH_Q
            )
            for s in SEASONS
        }
        guards: dict[str, bool] = {}
        for s in SEASONS:
            b = per[str(s)]
            guards[f"G1_no_harm_{s}"] = bool(b["RD_minus_A0_all"]["hi"] <= NOHARM)
            pq = b.get("RD_minus_PQ_affected")
            guards[f"G2_beats_placebo_{s}"] = bool(pq is not None and pq["point"] <= -t_c)
            guards[f"G3_coverage_{s}"] = all(
                abs(v[1] - float(t)) - abs(v[0] - float(t)) <= COV_TOL
                for t, v in b["coverage_A0_RD"].items()
            )
            guards[f"G5_slices_{s}"] = not any(
                v.get("reported", True) and v.get("point") is not None and v["point"] > SLICE_TOL
                for v in b["slices"].values()
            )
        quality = {
            s: bool(per[str(s)].get("RD_minus_V0_affected", {"point": 1.0})["point"] <= -t_c)
            for s in SEASONS
        }
        if all(prim.values()):
            word = (
                ("PASS_QUALITY" if all(quality.values()) else "PASS_WARM_ONLY")
                if all(guards.values())
                else "GUARD_FAIL"
            )
        elif any(per[str(s)]["RD_minus_A0_affected"]["point"] > CLOSE_AT for s in SEASONS):
            word = "CLOSED"
        else:
            word = "INCONCLUSIVE"
        words[st] = word
        detail[st] = {"primary": prim, "quality_beyond_V0": quality, "guards": guards}
    return {
        "words": words,
        "detail": detail,
        "any_pass": any(w.startswith("PASS") for w in words.values()),
    }


# ------------------------------------------------------------------------------------ features and the world


def libs() -> dict[str, str]:
    return {
        p: importlib.metadata.version(p) for p in ("lightgbm", "polars", "numpy", "scipy", "duckdb")
    }


def libs_sha() -> str:
    return hashlib.sha256(json.dumps(libs(), sort_keys=True).encode()).hexdigest()


def priors_sha(pri: dict[str, Any]) -> str:
    """Hash of the replacement rates and the prior strengths rounded to 10 significant digits (the pinned constants of the rule)."""
    canon = {
        "repl": {
            s: {x: float(f"{v:.10g}") for x, v in d.items()} for s, d in sorted(pri["repl"].items())
        },
        "k": {x: float(f"{v:.10g}") for x, v in sorted(pri["k_values"].items())},
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def world(skip_planted: bool = True) -> dict[str, Any]:
    t0 = time.time()
    w = G.warm_world()
    pri = G.stage_priors(w)
    repl = {int(s): v for s, v in pri["repl"].items()}
    rd = G.build_rd(w, repl, pri["k_values"])
    _log(f"warm world and features built in {time.time() - t0:.0f}s ({rd.height} rows)")
    return {"w": w, "pri": pri, "repl": repl, "rd": rd}


def a0_components(stat: str, with_labels: bool) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    c = load_components(stat, LT)
    if int(c["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season 2025 is the frozen holdout")
    if not with_labels:
        c = {k: v for k, v in c.items() if k != "row_y"}
    return c


def feature_frame(wd: dict[str, Any]) -> pl.DataFrame:
    """The REPLACEMENT_DELTA columns plus the two production columns the declared slices need (``last_starter``, ``team_game_no``), warm frame."""
    f = wd["w"]["feats"].select(
        "game_id", pl.col("player_id").cast(pl.Int64), "last_starter", "team_game_no"
    )
    return f.join(wd["rd"], on=["game_id", "player_id"], how="left")


def align_features(fr: pl.DataFrame, gid: np.ndarray, pid: np.ndarray) -> dict[str, np.ndarray]:
    """Feature arrays aligned to the stored A0 rows; ``affected`` = a reported game with >= 1 OUT rotation player and a finite share."""
    keys = pl.DataFrame(
        {"game_id": gid.astype(str), "player_id": pid.astype(np.int64), "_i": np.arange(len(gid))}
    )
    cols = [
        "game_id",
        "player_id",
        "last_starter",
        "team_game_no",
        *RD.COLUMNS,
        *RD.PLACEBO_COLUMNS,
    ]
    j = keys.join(fr.select(cols), on=["game_id", "player_id"], how="left").sort("_i")
    ft = {
        c: j[c].to_numpy().astype(float)
        for c in j.columns
        if c not in ("game_id", "player_id", "_i")
    }
    ft["gid"] = gid.astype(str)
    ft["affected"] = np.isfinite(ft["n_out"]) & (ft["n_out"] > 0) & np.isfinite(ft["share"])
    return ft


def preflight(world_: dict[str, Any], declared: dict[str, str]) -> dict[str, Any]:
    """Label-free: input hashes, library versions, priors, counts, production tie, planted future. Raises on any difference."""
    bad: dict[str, Any] = {}
    for name, rel in {
        **{f"COMPONENTS_SHA256_{st.upper()}": f"components_{st}.npz" for st in STATS},
        "COMPONENTS_SHA256_PI": "components_pi.npz",
    }.items():
        got = rule.file_sha256(LT / rel)
        if declared.get(name) != got:
            bad[name] = (got[:12], declared.get(name, "missing")[:12])
    if declared.get("LIBS_SHA256") != libs_sha():
        bad["LIBS_SHA256"] = (libs_sha()[:12], declared.get("LIBS_SHA256", "missing")[:12], libs())
    if declared.get("PRIORS_SHA256") != priors_sha(world_["pri"]):
        bad["PRIORS_SHA256"] = (
            priors_sha(world_["pri"])[:12],
            declared.get("PRIORS_SHA256", "missing")[:12],
        )
    if bad:
        raise RuntimeError(f"pinned input mismatch: {bad}")
    w, rd, repl = world_["w"], world_["rd"], world_["repl"]
    out: dict[str, Any] = {"libs": libs(), "priors_sha": priors_sha(world_["pri"])}
    par = G.stage_parity(w, rd)
    if (
        par["n_out_vs_n_out_rot_mismatch_rows"]
        or par["vac_min_max_abs_diff_team_games"] > 1e-12
        or par["no_report_rows_with_feature"]
    ):
        raise RuntimeError(f"production parity failed: {par}")
    out["parity"] = par
    plant = G.stage_planted(w, rd, repl, world_["pri"]["k_values"], time.time())
    if (
        plant["max_abs_diff_earlier_rows"] > plant["tolerance"]
        or not plant["null_pattern_equal"]
        or plant["later_rows_mean_abs_move_qv_pts"] <= 0
    ):
        raise RuntimeError(f"planted-future failed: {plant}")
    out["planted_future"] = plant
    c = a0_components("pts", with_labels=False)
    ft = align_features(feature_frame(world_), c["row_gid"], c["row_pid"])
    gate = rule.gate_json_checked()
    for s in SEASONS:
        m = c["row_season"] == s
        have = {
            "rows": int(m.sum()),
            "affected_rows": int((m & ft["affected"]).sum()),
            "games": int(np.unique(c["row_gid"][m]).size),
        }
        out[str(s)] = have
        gp = gate["power"]["pts"][str(s)]
        want = {"rows": gp["rows"], "affected_rows": gp["affected_rows"], "games": gp["games"]}
        if have != want:
            raise RuntimeError(f"{s}: counts {have} != pinned {want}")
    return out


# ------------------------------------------------------------------------------------ rehearsal


def draw_y(qi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return qi[np.arange(len(qi)), rng.integers(0, qi.shape[1], len(qi))].astype(float)


def calibrate_c(
    q0: np.ndarray, grid: np.ndarray, x: np.ndarray, target: float, n: int = 3000
) -> float:
    """Scale c so that the planted shift c x has an expected integer-CRPS gain of ``-target`` on a subsample of the affected rows (bisection)."""
    from nba.props.context_residual import to_integer_support

    nz = np.flatnonzero(x != 0)
    idx = np.sort(np.random.default_rng(0).choice(nz, size=min(n, nz.size), replace=False))
    lo, hi = 0.0, 50.0
    for _ in range(25):
        c = 0.5 * (lo + hi)
        gm, _ = G.gain_and_var(q0[idx], to_integer_support(grid[idx] + c * x[idx, None]))
        lo, hi = (c, hi) if float(gm.mean()) < target else (lo, c)
    return 0.5 * (lo + hi)


def rehearse(seeds: int, stats: tuple[str, ...] = ("pts",)) -> dict[str, Any]:
    """Synthetic worlds on the real feature rows and the stored A0 grids (labels never loaded). One stat by default (pts)."""
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support
    from research.eval import recent_role_full_eval as RF

    wd = world()
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    out: dict[str, Any] = {"worlds": {}, "t_c_rehearsal_only": 0.003}
    for stat in stats:
        c = a0_components(stat, with_labels=False)
        gid, season = c["row_gid"].astype(str), c["row_season"]
        ft = align_features(feature_frame(wd), gid, c["row_pid"])
        grid = arm_grid("mixture", {**c, "row_y": np.zeros(len(c["row_c"]))}, pic, stat)
        q0 = RF.arm_qi(stat, {**c, "row_y": np.zeros(len(c["row_c"]))}, pic, pic["row_pi"]).astype(
            np.int16
        )
        aff = ft["affected"]
        x = np.where(aff, np.nan_to_num(ft[f"rd_{stat}"]) - np.nanmean(ft[f"rd_{stat}"][aff]), 0.0)
        idx_aff = np.flatnonzero(aff)
        tstat = {"p": float(np.nanquantile(np.abs(ft[f"qv_{stat}"][aff]), 0.75))}
        c_claim = calibrate_c(q0.astype(float), grid, x, 0.005)
        worlds = (
            ("effect_0.0100", 0.0100),
            ("effect_0.0050", 0.0050),
            ("effect_0.0025", 0.0025),
            ("null", 0.0),
            ("harm", 0.0),
        )
        for world_name, tgt in worlds:
            cc = calibrate_c(q0.astype(float), grid, x, tgt) if tgt > 0 else 0.0
            shift = cc * x
            words: list[str] = []
            deltas: list[float] = []
            failed: dict[str, int] = {}
            for sd in range(seeds):
                rng = np.random.default_rng(1000 + sd)
                truth = shift if world_name.startswith("effect") else np.zeros_like(shift)
                y = draw_y(to_integer_support(grid + truth[:, None]).astype(np.int16), rng)
                if world_name == "harm":
                    cand_shift = (
                        0.5 * rng.normal(size=len(shift)) * np.ones_like(shift)
                    )  # noise on EVERY row
                elif world_name == "null":
                    cand_shift = c_claim * x  # claims an effect that is not there
                else:
                    cand_shift = shift
                pq = np.zeros(len(x))
                pq[idx_aff] = cand_shift[rng.permutation(idx_aff)]
                qi = {
                    stat: {
                        "A0": q0,
                        "RD": to_integer_support(grid + cand_shift[:, None]).astype(np.int16),
                        "V0": to_integer_support(grid + (0.6 * cand_shift)[:, None]).astype(
                            np.int16
                        ),
                        "PQ": to_integer_support(grid + pq[:, None]).astype(np.int16),
                    }
                }
                sc = score_arms(qi, {stat: y}, gid, season, ft, {stat: tstat["p"]}, seed=SEED + sd)
                dec = decide(sc, 0.003)
                words.append(dec["words"][stat])
                for g, ok in dec["detail"][stat]["guards"].items():
                    if not ok:
                        failed[g] = failed.get(g, 0) + 1
                deltas.append(sc[stat]["2024"]["RD_minus_A0_affected"]["point"])
            out["worlds"][f"{stat}:{world_name}"] = {
                "seeds": seeds,
                "words": {k: words.count(k) for k in sorted(set(words))},
                "mean_2024_RD_minus_A0_affected": float(np.mean(deltas)),
                "guard_failures": failed,
                "calibrated_c": cc,
            }
            _log(
                f"rehearsal {stat}:{world_name}: {out['worlds'][f'{stat}:{world_name}']['words']} mean delta {np.mean(deltas):+.4f}"
            )
    return out


# ------------------------------------------------------------------------------------ the real run


def freeze_time(sha: str) -> float:
    h = rule.freeze_commit(sha)
    if h is None:
        raise RuntimeError("not frozen")
    return float(
        subprocess.run(
            ["git", "show", "-s", "--format=%ct", h],
            capture_output=True,
            text=True,
            check=True,
            cwd=rule.ROOT,
        ).stdout.strip()
    )


def check_checkpoint(path: Path, frozen_at: float | None) -> bool:
    """True iff ``path`` exists and was written after the freeze commit (an older file is refused, never reused)."""
    if not path.exists():
        return False
    if frozen_at is not None and path.stat().st_mtime < frozen_at:
        raise RuntimeError(f"checkpoint {path} predates the freeze: refusing to reuse it")
    return True


def run(sha: str) -> dict[str, Any]:
    from nba.eval.context_residual_eval import TEST_SEASONS
    from nba.props.context_residual import stat_feature_names
    from research.eval import four_factors_eval as FF
    from research.eval import recent_role_full_eval as RF
    from research.eval import workload_eval as WE

    assert max(TEST_SEASONS) <= MAX_SEASON
    if T_C is None:
        raise RuntimeError(
            "G2 / Q are not sized: run the placebo null (K >= 30), put its threshold in the rule and the harness, then re-pin"
        )
    blobs = rule.require_ready(sha)  # frozen AND pinned, before anything else
    _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
    frozen_at = freeze_time(sha)
    declared = rule.declared()
    gate = rule.gate_json_checked()
    wd = world()
    pre = preflight(wd, declared)
    _log(
        "preflight passed (hashes, libs, priors, parity, planted future, counts); no outcome loaded yet"
    )
    study = FF.load_study("nba.duckdb")
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    cfg = study["cfg"]
    rd = wd["rd"]
    fr = feats.join(rd, on=["game_id", "player_id"], how="left")
    if fr.height != feats.height:
        raise RuntimeError("attaching REPLACEMENT_DELTA columns changed the rows")
    WE.assert_seasons(fr)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    heads: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for arm in (
        "CTRL",
        "V0",
        "RD",
        "PQ",
    ):  # CTRL first: the reproduction gate fires before any candidate arm is built
        for stat in STATS:
            path = CACHE / f"heads_{stat}_{arm}.npz"
            if not check_checkpoint(path, frozen_at):
                _log(f"{stat} heads walk {arm}")
                extra = [] if arm == "CTRL" else arm_columns(arm, stat)
                comp = RF.collect_heads(fr, stat, cfg, [*stat_feature_names(stat), *extra])
                np.savez_compressed(path, **comp)
            z = np.load(path, allow_pickle=True)
            heads.setdefault(stat, {})[arm] = {k: z[k] for k in z.files}
            if arm == "CTRL":
                stored = a0_components(stat, with_labels=True)
                rep = RF.heads_vs_stored(heads[stat][arm], stored)
                if not rep["rows_equal"] or rep["max_abs_diff"] > REPRO_TOL:
                    raise RuntimeError(
                        f"G4 failed: {stat} control walk differs from the stored A0 by {rep['max_abs_diff']}: refusing before any candidate arm is built"
                    )
    _log("G4 holds for every stat; scoring")
    qi: dict[str, dict[str, np.ndarray]] = {}
    y: dict[str, np.ndarray] = {}
    for stat in STATS:
        stored = a0_components(stat, with_labels=True)
        y[stat] = stored["row_y"]
        qi[stat] = {"A0": RF.arm_qi(stat, stored, pic, pic["row_pi"])}
        for arm in ("V0", "RD", "PQ"):
            qi[stat][arm] = RF.arm_qi(stat, heads[stat][arm], pic, pic["row_pi"])
    ref = a0_components("pts", with_labels=False)
    ft = align_features(feature_frame(wd), ref["row_gid"], ref["row_pid"])
    cuts = dict(gate["slice_cuts"])
    sc = score_arms(qi, y, ref["row_gid"].astype(str), ref["row_season"], ft, cuts)
    return {
        "preflight": pre,
        "scores": sc,
        "decision": decide(sc, T_C),
        "pin": rule.pin_of(blobs),
        "libs": libs(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--sha", default=None)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--stats", default="pts")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.cmd == "check":
        wd = world()
        res = preflight(wd, rule.declared())
        print(json.dumps(res, indent=1, default=str))
        return 0
    if a.cmd == "rehearse":
        res = rehearse(a.seeds, tuple(a.stats.split(",")))
        out = a.out or (rule.ROOT / OUT_REHEARSAL)
    else:
        res = run(a.sha or "")
        out = a.out or (rule.ROOT / OUT_REAL)
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
