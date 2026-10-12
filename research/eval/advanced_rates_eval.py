# ruff: noqa: E501
"""ADVANCED_RATES evaluation (docs/prereg/ADVANCED_RATES.md; DRAFT until FROZEN_FULL is set).

    M="-m research.eval.advanced_rates_eval"
    .venv/bin/python $M gate [--draft]   # frame, missingness audit, planted tests, A0 reproduction (light)
    .venv/bin/python $M arms             # A0 + B + placebo per cell; REFUSES unless the doc is frozen
    .venv/bin/python $M attack           # shuffled-label + knockouts for passing cells (needs arms)
    .venv/bin/python $M report           # reports/prereg_advanced_rates.md from the JSON
    .venv/bin/python $M run              # gate -> arms -> attack -> report under one heavy.lock

Research module only; ``nba/`` untouched; ``nba.duckdb`` read-only; ``season <= 2024`` in every SQL and
asserted. Nothing here is run by the drafting agent beyond the gate and the A0 reproduction.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    add_slice_columns,
    bh_adjust,
    month_blocks,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from research.eval import four_factors_eval as F
from research.eval.f11_lineup_context import (
    _bucket_minutes,
    _ci,
    _write_json,
    frozen_sha256,
    git_sha,
    heavy_lock,
)
from research.features import advanced_assemble as A

DOC = Path("docs/prereg/ADVANCED_RATES.md")
OUT_DIR = Path("reports/prereg_advanced_rates")
REPORT_MD = Path("reports/prereg_advanced_rates.md")
CACHE_DIR = Path("data/ops/advanced_rates_cache")
#: Set at the freeze (sha256 of the text above ``=== RESULTS BELOW ===``). Empty = DRAFT: ``arms`` refuses.
FROZEN_FULL = ""
STATS = F.STATS
N_BOOT = F.N_BOOT
FLOOR = -0.005
ATTACK_FLOOR = -0.003
SELECT_FLOOR = -0.005
MAX_BIAS = 0.5
COV_TOL = 0.03
BIAS_TOL = 0.05
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
NULL_GAP_PP = 2.0
PLACEBO_MULT = 3.0
CARRY = 0.8
SEEDS = {"placebo": 0, "shuffle": 0, "audit": 0}
META = tuple(c for c in F.META_COLS if not c.startswith("ff_")) + ("adv_stint_n",)
KEYS = ["game_id", "player_id"]
ALL_ADV_COLS: list[str] = sorted({c for v in A.FAMILY_COLUMNS.values() for c in v})


# --------------------------------------------------------------------------- frame
def load_frame(db_path: str = "nba.duckdb") -> dict[str, Any]:
    """Production feature frame plus every ADVANCED_RATES column, joined on (game_id, player_id)."""
    study = F.load_study(db_path)
    inp = A.load_inputs_db(db_path)
    feats = study["feats"]
    adv = A.build_advanced(inp, feats.select("game_id", "player_id", "min10"))
    frame = feats.join(adv, on=KEYS, how="left")
    assert frame.height == feats.height, "advanced join changed the row count"
    assert int(frame["season"].max()) <= MAX_SEASON  # type: ignore[arg-type,unused-ignore]
    return {"frame": frame, "inputs": inp, "cfg": study["cfg"], "feats": feats}


def arm_names(stat: str, family: str | None, drop: tuple[str, ...] = ()) -> list[str]:
    base = stat_feature_names(stat)
    if family is None:
        return base
    return base + [c for c in A.FAMILY_COLUMNS[family] if c not in drop and c not in base]


def placebo_permute(frame: pl.DataFrame, cols: list[str], seed: int) -> pl.DataFrame:
    """Permute the rows of ``cols`` JOINTLY within (player, season): marginals and each player-season's
    column structure survive, the link to the game does not."""
    rng = np.random.default_rng(seed)
    idx = np.arange(frame.height)
    key = frame.select("player_id", "season").with_row_index("_i")
    new = idx.copy()
    for grp in key.partition_by(["player_id", "season"], maintain_order=False):
        pos = grp["_i"].to_numpy()
        new[pos] = rng.permutation(pos)
    perm = frame.select(cols)[new.tolist()]
    return frame.with_columns([perm[c] for c in cols])


# --------------------------------------------------------------------------- fitting
def collect_arm(
    frame: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    shuffle_labels: bool = False,
    seed: int = 0,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, Any]:
    """Walk-forward month blocks (train strictly before the block), integer grid. ``shuffle_labels``
    permutes the TRAINING residuals within each block's training set (pipeline-leak control)."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rng = np.random.default_rng(seed)
    qs, ys, metas = [], [], []
    for start, end in month_blocks(test_all):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        if shuffle_labels:
            train = train.with_columns(
                pl.Series("resid", rng.permutation(train["resid"].to_numpy()))
            )
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        z = (cal_df["resid"].to_numpy() - mu) / model._scale(xc)
        qs.append(to_integer_support(raw_quantiles(c, s, z, CRPS_TAUS)).astype(np.int16))
        ys.append(block["y"].to_numpy())
        metas.append(block.select([c_ for c_ in META if c_ in block.columns]))
        print(
            f"  {stat} block {start} done ({time.time() - t0:.0f}s, n={block.height})", flush=True
        )
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def cached(tag: str, stat: str, make: Any) -> dict[str, Any]:
    """Per-arm checkpoint: an arm is computed once and reread after a crash."""
    p = CACHE_DIR / f"arm_{stat}_{tag}.npz"
    if p.exists():
        z = np.load(p)
        return {"qi": z["qi"], "y": z["y"], "meta": pl.read_parquet(p.with_suffix(".parquet"))}
    t0 = time.time()
    out: dict[str, Any] = make()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(p, qi=out["qi"], y=out["y"])
    out["meta"].write_parquet(p.with_suffix(".parquet"))
    print(f"{stat} {tag} done in {time.time() - t0:.0f}s", flush=True)
    return out


# --------------------------------------------------------------------------- scoring
def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    dd = add_slice_columns(d)
    out = {
        "all": np.ones(d.height, dtype=bool),
        "starter": (dd["sl_role"] == "starter").to_numpy(),
        "bench": (dd["sl_role"] == "bench").to_numpy(),
        "first15": (dd["sl_phase"] == "first15").to_numpy(),
        "teammate_out": (dd["sl_tm_out"] == "teammate_out").to_numpy(),
        "cold5_9": ((d["n_prior"] >= 5) & (d["n_prior"] < 10)).to_numpy(),
    }
    if "adv_stint_n" in d.columns:
        out["recent_move"] = (d["adv_stint_n"] < 10).to_numpy()
    return out


GUARD_SLICES = ("starter", "bench", "first15", "teammate_out", "recent_move")


def score_cell(
    stat: str, family: str, arms: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Rows for A0 / B / P: CRPS, delta vs A0 with clustered CI and p, B - P, guards inputs, slices."""
    ref = arms["A0"]["meta"]
    for v in arms.values():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), "arms must score identical rows"
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sls: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: F.row_scores(v["qi"][sm], v["y"][sm], stat) for a, v in arms.items()}
        for a in arms:
            s = sc[a]
            r: dict[str, Any] = {
                "stat": stat, "family": family, "season": season, "arm": a, "n_rows": int(d.height),
                "n_games": int(len(np.unique(gid))), "crps_int": float(s["crps"].mean()),
                "bias": float(s["resid"].mean()), "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()),
                "pit_q10": float((s["pit"] <= 0.10).mean()), "tll": float(s["tll"].mean()),
            }  # fmt: skip
            if a != "A0":
                r.update(F._delta_stats(s["crps"] - sc["A0"]["crps"], gid))
            if a == "B":
                bp = _ci(sc["B"]["crps"] - sc["P"]["crps"], gid, N_BOOT)
                r["B_minus_P"] = bp["point"]
                r["B_minus_P_hi"] = bp["hi"]
            rows.append(r)
        db = sc["B"]["crps"] - sc["A0"]["crps"]
        for name, m in slice_masks(d).items():
            n = int(m.sum())
            rec: dict[str, Any] = {
                "stat": stat,
                "family": family,
                "season": season,
                "slice": name,
                "n": n,
            }
            if n >= MIN_SLICE_N or name == "all":
                ds = F._delta_stats(db[m], gid[m])
                rec.update({k: ds[k] for k in ("dcrps", "ci_lo", "ci_hi")})
            sls.append(rec)
    return {"rows": rows, "slices": sls}


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the 15 cells separately for each season (arm B rows)."""
    for season in TEST_SEASONS:
        tgt = [r for r in rows if r["season"] == season and r["arm"] == "B"]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


def evaluate_cells(res: dict[str, Any], tests_ok: bool) -> dict[str, Any]:
    """The pass rule per cell (rules 1-3; rule 4, the knockout, is added by ``attack``)."""
    tab = {(r["family"], r["stat"], r["season"], r["arm"]): r for r in res["rows"]}
    out: dict[str, Any] = {}
    for fam, stat in A.CELLS:
        b23, b24 = tab[(fam, stat, 2023, "B")], tab[(fam, stat, 2024, "B")]
        a24 = tab[(fam, stat, 2024, "A0")]
        sel = bool(b23["dcrps"] <= SELECT_FLOOR and b23["ci_hi"] < 0.0)
        r1 = bool(b24["dcrps"] <= FLOOR and b24["ci_hi"] < 0.0 and b24["p_bh"] <= 0.05)
        sl = {
            s["slice"]: s
            for s in res["slices"]
            if s["family"] == fam and s["stat"] == stat and s["season"] == 2024
        }
        worse = {
            k: v["dcrps"]
            for k, v in sl.items()
            if k in GUARD_SLICES
            and v["n"] >= MIN_SLICE_N
            and v.get("dcrps") is not None
            and v["dcrps"] > SLICE_WORSE
        }
        cold = sl.get("cold5_9", {})
        cold_ok = True if cold.get("dcrps") is None else bool(cold["ci_lo"] <= 0.0)
        guards = {
            "cov80": bool(abs(b24["cov80"] - 0.8) <= abs(a24["cov80"] - 0.8) + COV_TOL),
            "bias": bool(
                abs(b24["bias"]) <= abs(a24["bias"]) + BIAS_TOL and abs(b24["bias"]) <= MAX_BIAS
            ),
            "no_slice_worse": not worse,
            "cold_start": cold_ok,
        }
        beats_p = bool(b24["B_minus_P"] <= ATTACK_FLOOR)
        out[f"{fam}/{stat}"] = {
            "select_2023": sel, "rule1_2024": r1, "guards": guards, "slices_worse": worse,
            "beats_placebo_by_0.003": beats_p, "tests_ok": bool(tests_ok),
            "pass_before_knockout": bool(sel and r1 and all(guards.values()) and beats_p and tests_ok),
        }  # fmt: skip
    fam_pass = {
        f: any(v["pass_before_knockout"] for k, v in out.items() if k.startswith(f + "/"))
        for f in A.FAMILY_COLUMNS
    }
    return {
        "cells": out,
        "families": fam_pass,
        "close": not any(v["select_2023"] for v in out.values()),
    }


def knockout_groups(family: str, stat_cols: list[str]) -> dict[str, list[str]]:
    """Column groups for the knockout: one group per base stat (all its representation columns), rest = 'other'."""
    groups: dict[str, list[str]] = {}
    for c in stat_cols:
        m = re.match(r"^(.*)_(ew5|ew20|ew100|slope20|vol20|stint20)$", c)
        groups.setdefault(m.group(1) if m else "other", []).append(c)
    return groups


def carries(gain_full: float, gain_drop: float, gain_only: float) -> bool:
    """'One group carries the gain' (gains are dCRPS, negative = better): dropping the group leaves
    <= 20% of the full gain, or the group alone keeps >= 80% of it."""
    if gain_full >= 0:
        return False
    return bool(gain_drop / gain_full <= 1.0 - CARRY or gain_only / gain_full >= CARRY)


# --------------------------------------------------------------------------- gate
def _check(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def audit_missingness(frame: pl.DataFrame, cols: list[str], seed: int = 0) -> dict[str, Any]:
    """NULL / NaN rate of every column by realised-minutes bucket on the model rows (2023-24, n_prior >= 5);
    asymmetry = max - min across the played buckets (pp). The threshold is max(2 pp, 3 x the same statistic
    after permuting the bucket label), set before any outcome."""
    d = frame.filter(
        pl.col("season").is_in(list(TEST_SEASONS)) & (pl.col("n_prior") >= 5)
    ).with_columns(_bucket_minutes(pl.col("minutes")).alias("_b"))

    def gap(lab: str) -> dict[str, float]:
        r = (
            d.group_by(lab)
            .agg([(pl.col(c).is_null() | pl.col(c).is_nan()).mean().alias(c) for c in cols])
            .filter(pl.col(lab) != "DNP")
        )
        return {c: float(np.ptp(r[c].to_numpy())) * 100.0 for c in cols}

    real = gap("_b")
    rng = np.random.default_rng(seed)
    pl_d = d.with_columns(pl.Series("_p", d["_b"].to_numpy()[rng.permutation(d.height)]))
    d = pl_d
    plc = gap("_p")
    thr = max(NULL_GAP_PP, PLACEBO_MULT * max(plc.values()))
    worst = max(real, key=lambda k: real[k])
    return {
        "max_gap_pp": real[worst],
        "worst_col": worst,
        "placebo_max_gap_pp": max(plc.values()),
        "threshold_pp": thr,
        "ok": real[worst] <= thr,
        "n_rows": d.height,
        "n_cols": len(cols),
    }


def planted_tests(inputs: dict[str, pl.DataFrame], rows: pl.DataFrame) -> list[dict[str, Any]]:
    """Same-game and future edits on the REAL inputs, with non-inert controls."""
    base = A.build_advanced(inputs, rows)
    games = inputs["games"]
    d24 = games.filter(pl.col("season") == 2024).sort("game_date")
    day = d24["game_date"][len(d24) // 2]
    same = set(d24.filter(pl.col("game_date") == day)["game_id"].to_list())
    out = []

    def eq(a: pl.DataFrame, b: pl.DataFrame, keys: pl.DataFrame) -> bool:
        x = a.join(keys, on=KEYS).sort(KEYS)
        y = b.join(keys, on=KEYS).sort(KEYS)
        return all(
            np.allclose(
                x[c].to_numpy().astype(float),
                y[c].to_numpy().astype(float),
                equal_nan=True,
                rtol=1e-6,
                atol=1e-6,
            )
            for c in x.columns
            if c not in KEYS
        )

    e1 = A.build_advanced(A.numeric_perturb(inputs, same), rows)
    k_same = rows.filter(pl.col("game_id").is_in(list(same))).select(KEYS)
    k_after = rows.join(
        games.filter(pl.col("game_date") > day).select("game_id"), on="game_id"
    ).select(KEYS)
    out.append(
        _check(
            "planted same-game: the edited games' own rows identical",
            int(k_same.height),
            "identical",
            eq(base, e1, k_same),
        )
    )
    out.append(
        _check(
            "control: rows after the edited day change",
            int(k_after.height),
            "not identical",
            not eq(base, e1, k_after),
        )
    )
    late = set(games.filter(pl.col("game_date") >= day)["game_id"].to_list())
    e2 = A.build_advanced(A.numeric_perturb(inputs, late), rows)
    k_early = rows.join(
        games.filter(pl.col("game_date") <= day).select("game_id"), on="game_id"
    ).select(KEYS)
    out.append(
        _check(
            "planted future: rows dated on or before the cutoff identical",
            int(k_early.height),
            "identical",
            eq(base, e2, k_early),
        )
    )
    return out


def run_gate(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, draft: bool = False
) -> dict[str, Any]:
    t0 = time.time()
    if not draft and not FROZEN_FULL:
        raise RuntimeError("DRAFT doc: pass --draft for the pre-freeze gate")
    st = load_frame(db_path)
    frame, inp = st["frame"], st["inputs"]
    tests: list[dict[str, Any]] = []
    tests.append(
        _check(
            "frame rows == production feature rows",
            frame.height,
            "== feats",
            frame.height == st["feats"].height,
        )
    )
    absent = int(frame.select(pl.col(ALL_ADV_COLS[0]).is_null().sum()).item())
    tests.append(
        _check(
            "every feature row keyed in the box (null rate of the first column)",
            absent,
            "== 0",
            absent == 0,
        )
    )
    aud = audit_missingness(frame, ALL_ADV_COLS, SEEDS["audit"])
    tests.append(
        _check(
            "missingness asymmetry across realised-minutes buckets (pp)",
            aud["max_gap_pp"],
            f"<= {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )
    tests += planted_tests(inp, frame.select(*KEYS, "min10"))
    gate = {
        "tests": tests,
        "audit": aud,
        "gate_ok": all(t["ok"] for t in tests),
        "draft": draft,
        "seconds": time.time() - t0,
        "git": git_sha(),
    }
    _write_json(out_dir / "gate.json", gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return gate


def run_a0_repro(db_path: str = "nba.duckdb") -> dict[str, Any]:
    """A0 only (the production feature list) against the re-baselined reference; the one fit allowed pre-freeze."""
    st = load_frame(db_path)
    arms = {
        s: cached("A0", s, lambda s=s: collect_arm(st["frame"], s, st["cfg"], arm_names(s, None)))
        for s in STATS
    }
    repro = F.check_a0_repro(arms)
    print(f"A0 repro ok={repro['ok']}", flush=True)
    return repro


# --------------------------------------------------------------------------- arms / report
def run_arms(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, make: Any = None
) -> dict[str, Any]:
    if not FROZEN_FULL or frozen_sha256(DOC) != FROZEN_FULL:
        raise RuntimeError(
            "the pre-registration is not frozen (FROZEN_FULL unset or the doc changed): no arm may be fitted"
        )
    gate = json.loads((out_dir / "gate.json").read_text())
    if not gate.get("gate_ok") or gate.get("draft"):
        raise RuntimeError("the frozen gate has not passed")
    st = load_frame(db_path)
    frame, cfg = st["frame"], st["cfg"]
    collect = make or (lambda fr, s, names: collect_arm(fr, s, cfg, names))
    a0 = {s: cached("A0", s, lambda s=s: collect(frame, s, arm_names(s, None))) for s in STATS}
    repro = F.check_a0_repro(a0)
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    res: dict[str, Any] = {"rows": [], "slices": [], "a0_repro": repro}
    t0 = time.time()
    for fam, stat in A.CELLS:
        names = arm_names(stat, fam)
        b = cached(
            f"B_{fam}", stat, lambda fam=fam, stat=stat, names=names: collect(frame, stat, names)
        )
        pf = placebo_permute(frame, A.FAMILY_COLUMNS[fam], SEEDS["placebo"])
        p = cached(f"P_{fam}", stat, lambda pf=pf, stat=stat, names=names: collect(pf, stat, names))
        sc = score_cell(stat, fam, {"A0": a0[stat], "B": b, "P": p})
        res["rows"] += sc["rows"]
        res["slices"] += sc["slices"]
        print(f"[{time.time() - t0:.0f}s] {fam}/{stat} scored", flush=True)
    add_bh(res["rows"])
    res["verdict"] = evaluate_cells(res, bool(gate["gate_ok"]))
    _write_json(out_dir / "results.json", res)
    return res


def _gain(a0: dict[str, Any], arm: dict[str, Any], stat: str) -> float:
    sm = (a0["meta"]["season"] == 2024).to_numpy()
    ra = F.row_scores(a0["qi"][sm], a0["y"][sm], stat)
    rb = F.row_scores(arm["qi"][sm], arm["y"][sm], stat)
    return float((rb["crps"] - ra["crps"]).mean())


def _attack_cell(
    frame: pl.DataFrame, fam: str, stat: str, a0: dict[str, Any], collect: Any
) -> dict[str, Any]:
    full_names = arm_names(stat, fam)
    b = cached(f"B_{fam}", stat, lambda: collect(frame, stat, full_names))
    shuf = cached(
        f"S_{fam}",
        stat,
        lambda: collect(frame, stat, full_names, shuffle_labels=True, seed=SEEDS["shuffle"]),
    )
    g_full, g_shuf = _gain(a0, b, stat), _gain(a0, shuf, stat)
    ko: dict[str, Any] = {}
    for g, cols in knockout_groups(fam, A.FAMILY_COLUMNS[fam]).items():
        drop_names = arm_names(stat, fam, drop=tuple(cols))
        only_names = stat_feature_names(stat) + cols
        drop = cached(f"KOd_{fam}_{g}", stat, lambda n=drop_names: collect(frame, stat, n))
        only = cached(f"KOo_{fam}_{g}", stat, lambda n=only_names: collect(frame, stat, n))
        gd, go = _gain(a0, drop, stat), _gain(a0, only, stat)
        ko[g] = {"gain_drop": gd, "gain_only": go, "carries": carries(g_full, gd, go)}
    return {
        "gain_full": g_full,
        "gain_shuffled": g_shuf,
        "shuffled_ok": bool(g_shuf >= -abs(g_full) * 0.2),
        "knockout": ko,
        "one_group_carries": any(v["carries"] for v in ko.values()),
    }


def run_attack(
    db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR, make: Any = None
) -> dict[str, Any]:
    """Rule 3/4 attacks for cells that passed rules 1-3: shuffled-label refit and the knockout by base-stat group."""
    res = json.loads((out_dir / "results.json").read_text())
    passing = [k for k, v in res["verdict"]["cells"].items() if v["pass_before_knockout"]]
    out: dict[str, Any] = {"cells": {}}
    if not passing:
        _write_json(out_dir / "attack.json", out)
        return out
    st = load_frame(db_path)
    frame, cfg = st["frame"], st["cfg"]
    collect = make or (lambda fr, s, names, **kw: collect_arm(fr, s, cfg, names, **kw))
    a0 = {s: cached("A0", s, lambda s=s: collect(frame, s, arm_names(s, None))) for s in STATS}
    for key in passing:
        fam, stat = key.split("/")
        out["cells"][key] = _attack_cell(frame, fam, stat, a0[stat], collect)
    _write_json(out_dir / "attack.json", out)
    return out


def render_report(out_dir: Path = OUT_DIR) -> str:
    res = json.loads((out_dir / "results.json").read_text())
    ln = [
        "# ADVANCED_RATES results",
        "",
        "| family | stat | season | n | dCRPS [CI] | p_bh | B-P | cov80 | bias |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["rows"]:
        if r["arm"] == "B":
            ln.append(
                f"| {r['family']} | {r['stat']} | {r['season']} | {r['n_rows']} | {r['dcrps']:+.4f} [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] | {r.get('p_bh', float('nan')):.3f} | {r['B_minus_P']:+.4f} | {r['cov80']:.3f} | {r['bias']:+.3f} |"
            )
    ln += ["", "## Verdict", "", "```json", json.dumps(res["verdict"], indent=1), "```"]
    return "\n".join(ln)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "a0", "arms", "attack", "report", "run"])
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        return 0 if run_gate(draft=a.draft)["gate_ok"] else 1
    if a.cmd == "a0":
        with heavy_lock():
            return 0 if run_a0_repro()["ok"] else 1
    if a.cmd == "arms":
        with heavy_lock():
            run_arms()
        return 0
    if a.cmd == "attack":
        with heavy_lock():
            run_attack()
        return 0
    if a.cmd == "report":
        REPORT_MD.write_text(render_report())
        return 0
    with heavy_lock():
        if not run_gate()["gate_ok"]:
            return 1
        run_arms()
        run_attack()
        REPORT_MD.write_text(render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
