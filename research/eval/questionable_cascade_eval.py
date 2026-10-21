# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""QUESTIONABLE_CASCADE harness (rule: docs/prereg/QUESTIONABLE_CASCADE.md; DRAFT until frozen). Model class: LightGBM context-residual heads.

Run from a checkout that has ``nba.duckdb``, ``configs/`` and ``data/`` (read by relative path), read-only on ``nba.duckdb``:

    python -W ignore -m research.eval.questionable_cascade_eval check                     # label-free preflight (no outcome array is loaded)
    python -W ignore -m research.eval.questionable_cascade_eval rehearse [--seeds 20]     # SYNTHETIC outcomes drawn from the stored A0 grid (row_y never loaded)
    python -W ignore -m research.eval.questionable_cascade_eval null --k 50               # sizes PLACEBO_TOL on the SELECT season; reads training labels, builds no C arm
    python -W ignore -m research.eval.questionable_cascade_eval run --sha <64 hex>        # real outcomes; refuses a DRAFT, an unfrozen rule, an unpinned harness, season > 2024

* ``check``: the pinned counts, the production OUT-set tie (my ``out`` rotation set reproduces ``n_out_rot`` / ``vac_min``), the stored-A0 frame
  reproduction to 1e-9, the input hashes pinned in the rule. ``row_y`` is never loaded.
* ``rehearse``: the POSITIVE CONTROL. Outcomes are drawn from the stored A0 integer grid shifted on affected-teammate rows by a PLANTED status effect
  (a Q/D rotation teammate sits with probability 1 - r_status and teammates gain 0.81 pts per 33 vacated minutes x f). ORACLE = a candidate that knows the
  expected shift; WRONG = the same shifts moved to other affected rows of the same date. The scoring path must say PASS for ORACLE at f = 1 and not for
  the null truth. It does not fit a candidate (that needs labels).
* ``run``: the real thing. Order: not a draft -> frozen AND pinned -> label-free preflight -> CTRL heads walks (the stored A0 must reproduce to
  ``REPRO_TOL``, else refuse before any C arm is built) -> C1 / C2 / PL walks -> integer-CRPS scoring. NOT EXECUTED in the draft (heavy, labels).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import questionable_cascade_features as F
from research.eval import questionable_cascade_gate as G
from research.eval import questionable_cascade_rule as rule

STATS = rule.STATS
SEASONS = rule.SEASONS
SELECT, REPORT = rule.SELECT, rule.REPORT
LT = Path("reports/lower_tail")  # relative to the repository root (cwd)
CACHE = Path("data/ops/questionable_cascade_cache")
OUT_REAL = Path("reports/questionable_cascade")
OUT_REHEARSAL = Path("reports/prereg_questionable_cascade/rehearsal")
OUT_NULL = Path("reports/prereg_questionable_cascade/null")
DESC_EFFECT, DESC_MPG = 0.81, 33.0
PINNED: dict[str, Any] = {
    "rows": {"2023": 27_619, "2024": 27_701},
    "teammate_affected_rows": {"2023": 11_046, "2024": 11_181},
    "own_qd_rows": {"2023": 1_005, "2024": 927},
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ rows and masks


def a0_components(stat: str, with_labels: bool) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    c = load_components(stat, LT)
    rule.require_season(int(c["row_season"].max()))
    if not with_labels:
        c = {k: v for k, v in c.items() if k != "row_y"}
    return c


def row_columns(
    c: dict[str, np.ndarray], w: dict[str, Any], rates: dict[str, float]
) -> dict[str, np.ndarray]:
    """C2 columns aligned to the stored A0 rows plus the three row sets (teammate-affected, own Q/D, rest)."""
    team = w["feats"].select("game_id", pl.col("player_id").cast(pl.Int64), "team_id")
    keys = pl.DataFrame(
        {"game_id": c["row_gid"].astype(str), "player_id": c["row_pid"].astype(np.int64)}
    ).join(team, on=["game_id", "player_id"], how="left")
    cols = F.build_columns(
        keys, w["snap"], w["tagged"], rates, set(w["snap"]["game_id"].unique().to_list())
    )
    out = {k: cols[k].to_numpy().astype(float) for k in F.C2_FEATURES}
    st = np.nan_to_num(out["qc_status"], nan=-1.0)
    has = ~np.isnan(out["qc_status"])
    own = has & np.isin(st, (3.0, 4.0))
    tm = has & ((np.nan_to_num(out["qc_nq"]) + np.nan_to_num(out["qc_nd"])) >= 1) & ~own
    out["own"], out["tm"], out["rest"] = own, tm, ~(own | tm)
    return out


def slice_masks(rc: dict[str, np.ndarray], c: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Declared slices of the affected-teammate rows."""
    tm = rc["tm"]
    ev = np.nan_to_num(rc["qc_evac_min"])
    return {
        "high_vacated(>=20 min)": tm & (ev >= 20.0),
        "low_vacated(<20 min)": tm & (ev < 20.0),
        "doubtful_only": tm & (np.nan_to_num(rc["qc_nq"]) == 0),
        "questionable_present": tm & (np.nan_to_num(rc["qc_nq"]) >= 1),
        "back_to_back": tm & (c["row_b2b"] >= 1),
        "bench(starter10<0.5)": tm & (c["row_starter10"] < 0.5),
    }


def week_keys(dates: dict[str, Any], gid: np.ndarray) -> np.ndarray:
    return G.week_key(np.array([str(dates[g]) for g in gid]))


# ------------------------------------------------------------------------------------ scoring


def mid_coverage(y: np.ndarray, q: np.ndarray) -> float:
    """Mid-coverage 0.5 (P(y < q) + P(y <= q)): on an integer lattice the inclusive share sits above tau by the tie mass, which an upward shift would
    wrongly be charged for (found by the rehearsal's first planted run)."""
    return float(0.5 * ((y < q).mean() + (y <= q).mean()))


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    from research.eval import pi_recent_role_eval as PR

    return np.asarray(PR.crps_int(qi, y))


def score_arms(
    qi: dict[str, dict[str, np.ndarray]],
    y: dict[str, np.ndarray],
    gid: np.ndarray,
    pid: np.ndarray,
    season: np.ndarray,
    wk: np.ndarray,
    rc: dict[str, np.ndarray],
    comp: dict[str, np.ndarray],
    seed: int = rule.SEED,
    n_boot: int = rule.N_BOOT,
) -> dict[str, Any]:
    """Contrasts per stat and season. ``qi[stat][arm]`` integer 199-quantile grids; arms ``A0`` and ``C2`` required, ``C1``, ``PL``, ``CTRL`` optional."""
    from nba.props.context_residual import CRPS_TAUS

    out: dict[str, Any] = {}
    sl = slice_masks(rc, comp)
    for stat in qi:
        sc = {arm: crps_int(q, y[stat]) for arm, q in qi[stat].items()}
        per: dict[str, Any] = {}
        for s in (*SEASONS, "pooled"):
            m = np.isin(season, SEASONS) if s == "pooled" else season == s
            ps = np.array([f"{p}_{x}" for p, x in zip(pid[m], season[m], strict=True)])
            bt = rule.MultiBoot(
                {"game": gid[m], "player_season": ps, "week": wk[m]},
                seed + (1 if s == "pooled" else int(s)),
                n_boot,
            )

            def ci(
                v: np.ndarray, mask: np.ndarray, bt: rule.MultiBoot = bt, m: np.ndarray = m
            ) -> dict[str, Any]:
                return bt.ci(v[m], mask[m])

            tm, own, rest = rc["tm"], rc["own"], rc["rest"]
            d2 = sc["C2"] - sc["A0"]
            block: dict[str, Any] = {
                "n_rows": int(m.sum()),
                "n_teammate": int((m & tm).sum()),
                "n_own": int((m & own).sum()),
                "n_rest": int((m & rest).sum()),
                "C2_minus_A0_teammate": ci(d2, tm),
                "C2_minus_A0_rest": ci(d2, rest),
                "C2_minus_A0_all": ci(d2, np.ones(len(d2), bool)),
            }
            if "C1" in sc:
                block["C1_minus_A0_own"] = ci(sc["C1"] - sc["A0"], own)
                block["C1_minus_A0_teammate"] = ci(sc["C1"] - sc["A0"], tm)
                block["C2_minus_C1_teammate"] = ci(sc["C2"] - sc["C1"], tm)
            else:
                block["C1_minus_A0_own"] = ci(d2, own)
            if "PL" in sc:
                block["C2_minus_PL_teammate"] = ci(sc["C2"] - sc["PL"], tm)
                block["PL_minus_CTRL_teammate"] = ci(sc["PL"] - sc.get("CTRL", sc["A0"]), tm)
            cov: dict[str, list[float]] = {}
            for tau in rule.COV_TAUS:
                j = int(np.argmin(np.abs(np.asarray(CRPS_TAUS) - tau)))
                cov[str(tau)] = [mid_coverage(y[stat][m], qi[stat][a][m, j]) for a in ("A0", "C2")]
            block["coverage_A0_C2"] = cov
            sls: dict[str, Any] = {}
            for name, mask in sl.items():
                if int((m & mask).sum()) >= rule.MIN_SLICE_N:
                    sls[name] = ci(d2, mask)
                else:
                    sls[name] = {"n": int((m & mask).sum()), "reported": False}
            block["slices"] = sls
            per[str(s)] = block
        out[stat] = per
    return out


# ------------------------------------------------------------------------------------ preflight


def preflight(
    w: dict[str, Any], rates: dict[str, float], declared: dict[str, str]
) -> dict[str, Any]:
    """Label-free. Raises on any difference from the pinned counts, hashes or the production tie."""
    bad = {}
    for name, rel in {
        **{f"COMPONENTS_SHA256_{st.upper()}": f"components_{st}.npz" for st in STATS},
        "COMPONENTS_SHA256_PI": "components_pi.npz",
    }.items():
        if name in declared:
            got = rule.file_sha256(LT / rel)
            if declared[name] != got:
                bad[name] = (got[:12], declared[name][:12])
    if bad:
        raise RuntimeError(f"pinned input sha256 mismatch (stored A0 components): {bad}")
    if declared and declared.get("SNAPSHOT_SOURCE_SHA256") != F.snapshot_source_sha():
        raise RuntimeError("the snapshot definition differs from the pinned one: re-register")
    tie = G.production_tie(w)
    if tie["n_out_rot_differs"] or tie["vac_min_differs"]:
        raise RuntimeError(f"the out rotation set differs from production's: {tie}")
    rep = G.a0_reproduction(w, Path("."))
    if not all(v["pass"] for v in rep.values()):
        raise RuntimeError(f"the rebuilt frame does not reproduce the stored A0: {rep}")
    counts: dict[str, Any] = {
        "production_tie": tie,
        "a0_reproduction": {k: v["pass"] for k, v in rep.items()},
    }
    c = a0_components("pts", with_labels=False)
    rc = row_columns(c, w, rates)
    have: dict[str, Any] = {"rows": {}, "teammate_affected_rows": {}, "own_qd_rows": {}}
    for s in SEASONS:
        m = c["row_season"] == s
        have["rows"][str(s)] = int(m.sum())
        have["teammate_affected_rows"][str(s)] = int((m & rc["tm"]).sum())
        have["own_qd_rows"][str(s)] = int((m & rc["own"]).sum())
    for k in have:
        if have[k] != PINNED[k]:
            raise RuntimeError(f"{k}: {have[k]} != pinned {PINNED[k]}")
    counts["pinned_counts_hold"] = have
    return counts


# ------------------------------------------------------------------------------------ positive control


def draw_y(qi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return qi[np.arange(len(qi)), rng.integers(0, qi.shape[1], len(qi))].astype(float)


def planted_shift(
    w: dict[str, Any],
    gid: np.ndarray,
    c: dict[str, np.ndarray],
    rc: dict[str, np.ndarray],
    r_true: dict[str, float],
    rng: np.random.Generator,
    f: float,
) -> tuple[np.ndarray, np.ndarray]:
    """(realised shift, expected shift) per A0 row. A Q/D rotation teammate sits with probability 1 - r_true[status], independently per (game, player);
    teammates gain ``f * 0.81 * min(vacated/33, 2)`` pts on rotation rows. The expected shift is the same formula with the expected vacated minutes."""
    ct = F.cascade_table(w["tagged"], r_true).sort(
        ["game_id", "team_id", "player_id"]
    )  # fixed order: the seeded draws must not depend on join order
    tg = ct.select("game_id", "team_id").unique(maintain_order=True).with_row_index("k")
    ct = ct.join(tg, on=["game_id", "team_id"])
    k = ct["k"].to_numpy()
    sit = (rng.random(ct.height) < ct["_w"].to_numpy()).astype(float)
    real = np.bincount(k, weights=sit * ct["min10"].to_numpy(), minlength=tg.height)
    own_real = tg.with_columns(pl.Series("real", real))
    keys = pl.DataFrame({"game_id": gid, "player_id": c["row_pid"].astype(np.int64)})
    team = w["feats"].select("game_id", pl.col("player_id").cast(pl.Int64), "team_id")
    j = keys.join(team, on=["game_id", "player_id"], how="left").join(
        own_real.select("game_id", "team_id", "real"), on=["game_id", "team_id"], how="left"
    )
    # a Q/D teammate's own row (class `own`) is not in the affected-teammate set, so its self-inclusion never matters
    vac = np.nan_to_num(j["real"].to_numpy().astype(float))
    rot = c["row_min10"] >= 10.0
    real_shift = f * DESC_EFFECT * np.minimum(vac / DESC_MPG, 2.0) * rot * rc["tm"]
    exp_shift = (
        f
        * DESC_EFFECT
        * np.minimum(np.nan_to_num(rc["qc_evac_min"]) / DESC_MPG, 2.0)
        * rot
        * rc["tm"]
    )
    return real_shift, exp_shift


def permute_within_group(
    shift: np.ndarray, mask: np.ndarray, group: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Move the shifts of the masked rows among the masked rows of the same group (date)."""
    out = np.zeros_like(shift)
    idx = np.flatnonzero(mask)
    order = idx[np.argsort(group[idx], kind="stable")]
    g = group[order]
    cuts = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        blk = order[a:b]
        out[blk] = shift[blk[rng.permutation(len(blk))]]
    return out


def rehearse(seeds: int, f_list: tuple[float, ...] = (1.0, 0.5)) -> dict[str, Any]:
    """pts only. Synthetic outcomes from the stored A0 grid. Truths: null, planted(f). Candidates: ORACLE (C2 slot), WRONG (PL slot)."""
    from nba.eval.lower_tail_eval import arm_grid
    from nba.props.context_residual import to_integer_support

    t0 = time.time()
    w = G.build_world(Path("."), t0)
    seas = dict(w["games"].select("game_id", "season").rows())
    rates = F.rates_for_cascade(F.play_rates(w["tagged"], w["pgs"], seas, SELECT))
    c = a0_components("pts", with_labels=False)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    rc = row_columns(c, w, rates)
    gid, pid, season = c["row_gid"].astype(str), c["row_pid"].astype(np.int64), c["row_season"]
    dates = dict(w["games"].select("game_id", "game_date").rows())
    day = np.array([str(dates[g]) for g in gid])
    wk = week_keys(dates, gid)
    grid = arm_grid(
        "mixture", {**c, "row_y": np.zeros(len(c["row_c"]))}, pic, "pts"
    )  # row_y is only a length carrier
    q0 = to_integer_support(grid).astype(np.int16)
    out: dict[str, Any] = {"truths": {}, "rates_used": rates}
    for truth in ("null", *[f"planted_f{f}" for f in f_list]):
        f = 0.0 if truth == "null" else float(truth.split("f")[-1])
        words: list[str] = []
        wrong_words: list[str] = []
        pts_delta: list[float] = []
        failed: dict[str, int] = {}
        for sd in range(seeds):
            rng = np.random.default_rng(1000 + sd)
            real, expd = planted_shift(w, gid, c, rc, rates, rng, f if f else 1.0)
            if truth == "null":
                y = draw_y(q0, rng)
            else:
                y = draw_y(to_integer_support(grid + real[:, None]).astype(np.int16), rng)
            q_or = to_integer_support(grid + expd[:, None]).astype(np.int16)
            wrong = permute_within_group(expd, rc["tm"], day, rng)
            q_wr = to_integer_support(grid + wrong[:, None]).astype(np.int16)
            sc = score_arms(
                {"pts": {"A0": q0, "C2": q_or, "PL": q_wr}},
                {"pts": y},
                gid,
                pid,
                season,
                wk,
                rc,
                c,
                seed=rule.SEED + sd,
                n_boot=1000,
            )
            # the select season sign needs the SELECT season too: score_arms has it; the per-stat decision uses all declared guards
            # the WRONG shifts in the C2 slot (right size, wrong rows) must not pass either: the scorer is not blind to where the effect is
            wrong2 = permute_within_group(expd, rc["tm"], day, rng)
            q_w2 = to_integer_support(grid + wrong2[:, None]).astype(np.int16)
            sc_w = score_arms(
                {"pts": {"A0": q0, "C2": q_wr, "PL": q_w2}},
                {"pts": y},
                gid,
                pid,
                season,
                wk,
                rc,
                c,
                seed=rule.SEED + sd,
                n_boot=1000,
            )
            wrong_words.append(rule.decide(sc_w)["words"]["pts"])
            dec = rule.decide(sc)
            words.append(dec["words"]["pts"])
            for gname, ok in dec["detail"]["pts"]["guards"].items():
                if not ok:
                    failed[gname] = failed.get(gname, 0) + 1
            pts_delta.append(sc["pts"][str(REPORT)]["C2_minus_A0_teammate"]["point"])
        out["truths"][truth] = {
            "seeds": seeds,
            "words": {k: words.count(k) for k in sorted(set(words))},
            "wrong_rows_in_c2_slot_words": {
                k: wrong_words.count(k) for k in sorted(set(wrong_words))
            },
            "mean_report_delta_teammate": float(np.mean(pts_delta)),
            "guard_failures": failed,
        }
        _log(f"rehearsal truth {truth}: {out['truths'][truth]['words']} ({time.time() - t0:.0f}s)")
    return out


# ------------------------------------------------------------------------------------ the heavy parts (labels; not run in the draft)


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


def permute_within_date(
    fr: pl.DataFrame, cols: tuple[str, ...], seed: int, prefix: str = "pl_"
) -> pl.DataFrame:
    """The placebo block: the columns of ``cols`` moved JOINTLY among the rows of the same game date (a within-date permutation of the whole block)."""
    date = fr["game_date"].cast(pl.Int32).to_numpy()
    idx = np.arange(fr.height)
    for d in np.unique(date):
        sel = np.flatnonzero(date == d)
        idx[sel] = sel[np.random.default_rng([seed, int(d)]).permutation(len(sel))]
    return fr.with_columns(
        *[pl.Series(prefix + c, fr[c].to_numpy().astype(float)[idx]) for c in cols]
    )


def cascade_frame(w: dict[str, Any], rates: dict[str, float]) -> pl.DataFrame:
    """Production's feature frame plus the C2 columns (NaN in games with no usable snapshot, the production NULL pattern)."""
    feats = w["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    cols = F.build_columns(
        feats.select("game_id", "team_id", "player_id"),
        w["snap"],
        w["tagged"],
        rates,
        set(w["snap"]["game_id"].unique().to_list()),
    )
    fr = feats.join(cols, on=["game_id", "player_id"], how="left")
    if fr.height != feats.height:
        raise RuntimeError("attaching the cascade columns changed the rows")
    rule.require_season(int(fr["season"].max()))
    return fr


def null(k: int, out: Path) -> dict[str, Any]:
    """Permutation null for the placebo guard on the SELECT season (pts, affected-teammate rows): CTRL once, then K walks with the C2 block permuted
    within date (seed SEED + i), scored PL_i - CTRL. Reads training labels, builds no C arm. Per-draw checkpoints in ``out``."""
    import yaml

    from nba.props.context_residual import ContextResidualConfig, stat_feature_names
    from research.eval import recent_role_full_eval as RF

    w = G.build_world(Path("."), time.time())
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    seas = dict(w["games"].select("game_id", "season").rows())
    rates = F.rates_for_cascade(F.play_rates(w["tagged"], w["pgs"], seas, SELECT))
    base = cascade_frame(w, rates)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    stored = a0_components("pts", with_labels=True)
    rc = row_columns(stored, w, rates)
    aff = rc["tm"] & (stored["row_season"] == SELECT)
    y = stored["row_y"]
    out.mkdir(parents=True, exist_ok=True)
    names0 = stat_feature_names("pts")
    t0 = time.time()
    ctrl = RF.collect_heads(base, "pts", cfg, names0, log=False)
    rep = RF.heads_vs_stored(ctrl, stored)
    if not rep["rows_equal"] or rep["max_abs_diff"] > rule.REPRO_TOL:
        raise RuntimeError(
            f"the control walk differs from the stored A0 in the null run: {rep['max_abs_diff']}"
        )
    c_ctrl = crps_int(RF.arm_qi("pts", ctrl, pic, pic["row_pi"]), y)
    deltas: list[float] = []
    for i in range(k):
        path = out / f"null_{i:03d}.json"
        if path.exists():
            deltas.append(json.loads(path.read_text())["delta"])
            continue
        fr = permute_within_date(base, F.C2_FEATURES, rule.SEED + i)
        comp = RF.collect_heads(
            fr, "pts", cfg, [*names0, *("pl_" + c for c in F.C2_FEATURES)], log=False
        )
        d = float((crps_int(RF.arm_qi("pts", comp, pic, pic["row_pi"]), y) - c_ctrl)[aff].mean())
        path.write_text(json.dumps({"i": i, "delta": d}))
        deltas.append(d)
        _log(f"null draw {i + 1}/{k}: PL - CTRL = {d:+.5f} ({time.time() - t0:.0f}s)")
    a = np.asarray(deltas)
    mu, sd = float(a.mean()), float(a.std(ddof=1))
    return {
        "k": len(a),
        "mean": mu,
        "sd": sd,
        "min": float(a.min()),
        "proposed_PLACEBO_TOL": float(min(-0.001, mu - 2.0 * sd)),
    }


def run(sha: str) -> dict[str, Any]:
    import yaml

    from nba.props.context_residual import ContextResidualConfig, stat_feature_names
    from research.eval import recent_role_full_eval as RF

    blobs = rule.require_ready(sha)  # not a draft, PLACEBO_TOL sized, frozen, pinned
    _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
    frozen_at = freeze_time(sha)
    t0 = time.time()
    w = G.build_world(Path("."), t0)
    seas = dict(w["games"].select("game_id", "season").rows())
    rates = F.rates_for_cascade(F.play_rates(w["tagged"], w["pgs"], seas, SELECT))
    pre = preflight(w, rates, rule.declared())
    _log("preflight passed; no outcome loaded yet")
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    fr = permute_within_date(cascade_frame(w, rates), F.C2_FEATURES, rule.SEED)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    arms: dict[str, tuple[str, ...]] = {
        "CTRL": (),
        "C1": F.C1_FEATURES,
        "C2": F.C2_FEATURES,
        "PL": tuple("pl_" + c for c in F.C2_FEATURES),
    }
    heads: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for arm in (
        "CTRL",
        "C1",
        "C2",
        "PL",
    ):  # CTRL first: the reproduction gate fires before any C arm is built
        for stat in STATS:
            path = CACHE / f"heads_{stat}_{arm}.npz"
            if not check_checkpoint(path, frozen_at):
                _log(f"{stat} heads walk {arm}")
                comp = RF.collect_heads(fr, stat, cfg, [*stat_feature_names(stat), *arms[arm]])
                np.savez_compressed(path, **comp)
            z = np.load(path, allow_pickle=True)
            heads.setdefault(stat, {})[arm] = {k: z[k] for k in z.files}
            if arm == "CTRL":
                rep = RF.heads_vs_stored(heads[stat][arm], a0_components(stat, with_labels=True))
                if not rep["rows_equal"] or rep["max_abs_diff"] > rule.REPRO_TOL:
                    raise RuntimeError(
                        f"{stat} control walk differs from the stored A0 by {rep['max_abs_diff']}: refusing before any C arm is built"
                    )
    _log("the control walk reproduces the stored A0 for every stat; scoring")
    qi: dict[str, dict[str, np.ndarray]] = {}
    y: dict[str, np.ndarray] = {}
    for stat in STATS:
        stored = a0_components(stat, with_labels=True)
        y[stat] = stored["row_y"]
        qi[stat] = {"A0": RF.arm_qi(stat, stored, pic, pic["row_pi"])}
        for arm in ("CTRL", "C1", "C2", "PL"):
            qi[stat][arm] = RF.arm_qi(stat, heads[stat][arm], pic, pic["row_pi"])
    ref = a0_components("pts", with_labels=False)
    rc = row_columns(ref, w, rates)
    gid = ref["row_gid"].astype(str)
    dates = dict(w["games"].select("game_id", "game_date").rows())
    sc = score_arms(
        qi,
        y,
        gid,
        ref["row_pid"].astype(np.int64),
        ref["row_season"],
        week_keys(dates, gid),
        rc,
        ref,
    )
    return {
        "preflight": pre,
        "scores": sc,
        "decision": rule.decide(sc),
        "pin": rule.pin_of(blobs),
        "rates_used": rates,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run", "null"))
    ap.add_argument("--sha", default=None)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.cmd == "run":
        res = run(a.sha or "")
        out = a.out or (rule.ROOT / OUT_REAL)
    elif a.cmd == "check":
        w = G.build_world(Path("."), t0)
        seas = dict(w["games"].select("game_id", "season").rows())
        rates = F.rates_for_cascade(F.play_rates(w["tagged"], w["pgs"], seas, SELECT))
        res = preflight(w, rates, {})
        print(json.dumps(res, indent=1, default=str))
        return 0
    elif a.cmd == "null":
        print(json.dumps(null(max(a.k, 50), a.out or (rule.ROOT / OUT_NULL)), indent=1))
        return 0
    else:
        res = rehearse(a.seeds)
        out = a.out or (rule.ROOT / OUT_REHEARSAL)
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
