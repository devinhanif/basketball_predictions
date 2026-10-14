"""ALT_PARLAY_X run harness (rule: docs/prereg/ALT_PARLAY_X.md, FROZEN on main, sha 91d34595...).

    .venv/bin/python -W ignore -m research.eval.alt_parlay_x_eval \
        --data-root <checkout holding data/ and reports/> [--check-only]

It imports the frozen construction unchanged (``alt_parlay_x_gate``: ``pool_legs``,
``draw_parlays``, ``estimators``, ...) and REFUSES to run unless:
* the rule text hashes to the pinned frozen sha, a commit ``prereg(ALT_PARLAY_X): <sha>`` carries it
  and its status line says FROZEN (``Construction identity``: the rule pins the code);
* the two construction files hash to the rule's pinned values and numpy is the pinned version
  (``Generator.choice`` / ``integers`` streams are not stable across numpy versions);
* ``reports/prereg_alt_parlay_x/gate.json`` hashes to the value the rule quotes, every
  (season, pool, size) parlay count and every cell fingerprint rebuilt here equals the gate's, and
  the pooled counts equal the rule's table;
* ``max(season) <= 2024`` before any file is read;
* ``docs/prereg/ALT_LADDER_HIST.md`` is FROZEN (these parlays are scored on the same 2023-24
  player-games and BetRivers legs as ALT_LINE_70; the ALT_LADDER_HIST words are frozen before any
  harness of this family reads an outcome; ALT_LINE_70 review N2).

``--check-only`` performs all checks and the construction rebuild WITHOUT reading ``y``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import alt_harness_common as hc
from research.eval import alt_parlay_x_gate as x
from research.eval.alt_line_70_eval import (
    ADH_ID,
    ADH_PATH,
    MODEL_INPUTS,
    ODDS_DB,
    ODDS_FINGERPRINT,
    add_indices,
    load_model,
    load_tips,
    odds_fingerprint,
    with_outcome,
)
from research.eval.alt_line_70_gate import (
    MAX_SEASON,
    PRIMARY_KIND,
    SEASONS,
    SENSITIVITY_KIND,
    STATS,
    p_over_at,
    priced_rungs,
    survival_matrix,
)

RULE_ID = "ALT_PARLAY_X"
RULE_PATH = "docs/prereg/ALT_PARLAY_X.md"
RULE_SHA = "91d345950abf03ae306ff792ee4567d40a90d4ab2e85491a0e4633f533ae9254"
GATE_JSON = "reports/prereg_alt_parlay_x/gate.json"
GATE_JSON_SHA = "31e93881fffb8e6912b5964d6532c505b16cf0f56765b7788578e30715bb5a11"
LINE70_GATE_JSON = "reports/prereg_alt_line_70/gate.json"
CODE_SHA = {
    "research/eval/alt_parlay_x_gate.py": (
        "169b8194491a7f12ec5369441d90f8f50df52797616440147198786626c7eb76"
    ),
    "research/eval/alt_line_70_gate.py": (
        "8da31748003500f52f219ccc8c7bea71ee5ee91d56e30bc966112433e6413277"
    ),
}
NUMPY_PIN = "2.5.3"
OUT_DEFAULT = "reports/alt_parlay_x/results.json"
SEED_DATE_PARLAY = 2026  # the rule: date-clustered bootstrap B = 5,000, default_rng(2026)

# money gate (rule): pooled parlays, dates, conservative date-clustered ROI half-width
GATE_MIN_PARLAYS, GATE_MIN_DATES, GATE_MAX_HW = 1_500, 150, 0.20
CAL_POINT, CAL_MARGIN = 0.02, 0.03
INDEP_POINT = 0.01
ROI_FLOOR = 0.02
#: cells that can issue a word, fixed by the rule's model-only widths table
CAN_CALIBRATED = {("55", 2), ("55", 3), ("65", 2), ("65", 3)}
CAN_INDEP = {("55", 2), ("55", 3), ("65", 2), ("65", 3), ("ge70", 2), ("75", 2)}
#: the rule's gate result: 8 of 10 cells pass (every pool except 85)
EXPECTED_GATE_PASS = {(p, m): p != "85" for p, _, _ in x.POOLS for m in x.SIZES}
POOL_LABELS = [p for p, _, _ in x.POOLS]

UNSTATED = [
    "The date-cluster universe of a season is the sorted unique game_date of the stored model "
    "rows; one resample matrix per season (default_rng(2026)) is shared by every cell; the week "
    "sensitivity uses weeks of the same dates with default_rng(2028).",
    "T2's excess E re-estimates the legs' mean marginal residual g inside every bootstrap "
    "replicate from the resampled dates' legs (g is a fitted quantity); a fixed-g interval is "
    "narrower. The point estimates are exactly gate_module.estimators.",
    "Pooled T1/T2/rho and ROI use parlay weights across seasons; a directional word also needs the "
    "same sign of its statistic in each season.",
    "The p-value of T1 and T2 is the two-sided date-clustered bootstrap p of the cell's pooled "
    "statistic; the money p is one-sided (ROI <= 0); BH runs over the 10 cells of each family, the "
    "two 85 cells included although they get no word.",
    "Single-leg comparator C1 is the mean single-leg ROI over the leg appearances of the cell's "
    "parlays; by-stat slices are over those leg appearances too.",
    "Plan draws: one default_rng(2026) per plan call, seasons ascending, weeks ascending; a "
    "parlay is a one-outcome unit, so only the 'draw 10 of the week's parlays' step applies.",
]


# ------------------------------------------------------------------------------ cells


@dataclass
class SeasonCell:
    """One (season, pool, size) parlay table with the arrays the estimators need."""

    legs: pl.DataFrame
    par: pl.DataFrame
    idx: np.ndarray
    p: np.ndarray
    hit: np.ndarray
    fingerprint: str
    date_par: np.ndarray
    date_leg: np.ndarray
    week_par: np.ndarray
    week_leg: np.ndarray

    @property
    def n(self) -> int:
        return int(self.idx.shape[0])


def week_index(dates64: np.ndarray, weeks: np.ndarray) -> np.ndarray:
    """Index of the Monday-start week of each date in the sorted ``weeks`` (datetime64[D])."""
    monday = dates64 - ((dates64.astype("datetime64[D]").astype(int) + 3) % 7).astype(
        "timedelta64[D]"
    )
    return np.asarray(np.searchsorted(weeks, monday))


def build_season_cell(
    t5: pl.DataFrame,
    season: int,
    pool_index: int,
    lo: float,
    hi: float,
    m: int,
    dates: np.ndarray,
    weeks: np.ndarray,
    with_y: bool,
) -> SeasonCell | None:
    """The frozen construction for one cell (the gate module's own functions, same seeds)."""
    legs = x.pool_legs(t5, lo, hi, [x.ROOT_SEED, season, pool_index])
    if legs.is_empty():
        return None
    par = x.draw_parlays(legs, m, x.PER_DATE, [x.ROOT_SEED, season, pool_index, m])
    if par.is_empty():
        return None
    legs = legs.sort([*x.KEY, "point"])
    idx = x.parlay_index(par, m)
    d_leg = legs["game_date"].to_numpy().astype("datetime64[D]")
    d_par = par["game_date"].to_numpy().astype("datetime64[D]")
    hit = (
        (legs["y"] > legs["point"]).cast(pl.Float64).to_numpy() if with_y else np.zeros(legs.height)
    )
    return SeasonCell(
        legs=legs,
        par=par,
        idx=idx,
        p=legs["pm"].to_numpy().astype(float),
        hit=hit,
        fingerprint=x.parlay_fingerprint(legs, par, m),
        date_par=np.searchsorted(dates, d_par),
        date_leg=np.searchsorted(dates, d_leg),
        week_par=week_index(d_par, weeks),
        week_leg=week_index(d_leg, weeks),
    )


def parlay_profit(c: SeasonCell) -> np.ndarray:
    """Stake-unit profit of every parlay: D - 1 if every leg hit, else -1."""
    won = c.hit[c.idx].prod(axis=1) > 0.5
    return np.where(won, c.par["D"].to_numpy() - 1.0, -1.0)


# ------------------------------------------------------------------------------ bootstrap


def joint_bootstrap(
    parts: list[tuple[SeasonCell, np.ndarray, str]], chunk: int = 100
) -> dict[str, np.ndarray]:
    """Cluster bootstrap of T1's gap, T2's excess E and rho over seasons (strata). ``parts`` holds
    ``(cell, resample matrix [B, G], 'date' | 'week')`` per season. Within every replicate the legs'
    mean marginal residual g is RE-ESTIMATED from the resampled clusters' legs (see UNSTATED);
    with an all-ones matrix this reproduces ``alt_parlay_x_gate.estimators`` exactly."""
    b = parts[0][1].shape[0]
    num_gap = np.zeros(b)
    num_ex = np.zeros(b)
    num_rho = np.zeros(b)
    den = np.zeros(b)
    den_rho = np.zeros(b)
    for c, mat, kind in parts:
        g_n = mat.shape[1]
        par_c = c.date_par if kind == "date" else c.week_par
        leg_c = c.date_leg if kind == "date" else c.week_leg
        z = np.zeros((c.n, g_n))
        z[np.arange(c.n), par_c] = 1.0
        n_c = z.sum(axis=0)
        joint = c.hit[c.idx].prod(axis=1)
        prod_p = c.p[c.idx].prod(axis=1)
        resid_c = hc.cluster_totals(leg_c, g_n, c.hit - c.p)
        cnt_c = hc.cluster_totals(leg_c, g_n)
        v = c.p * (1.0 - c.p)
        m_legs = c.idx.shape[1]
        sq = np.zeros(c.n)
        for a in range(m_legs):
            for bb in range(a + 1, m_legs):
                sq += np.sqrt(v[c.idx[:, a]] * v[c.idx[:, bb]])
        sq_c = sq @ z
        s_gap = (joint - prod_p) @ z
        for c0 in range(0, b, chunk):
            mc = mat[c0 : c0 + chunk]
            with np.errstate(divide="ignore", invalid="ignore"):
                g = (mc @ resid_c) / (mc @ cnt_c)
            pc = np.clip(c.p[None, :] + g[:, None], 0.0, 1.0)
            shifted = np.ones((mc.shape[0], c.n))
            for a in range(m_legs):
                shifted *= pc[:, c.idx[:, a]]
            s_ex = (joint[None, :] - shifted) @ z
            r = c.hit[None, :] - c.p[None, :] - g[:, None]
            pair = np.zeros((mc.shape[0], c.n))
            for a in range(m_legs):
                for bb in range(a + 1, m_legs):
                    pair += r[:, c.idx[:, a]] * r[:, c.idx[:, bb]]
            s_rho = pair @ z
            sl = slice(c0, c0 + mc.shape[0])
            num_gap[sl] += mc @ s_gap
            num_ex[sl] += (mc * s_ex).sum(axis=1)
            num_rho[sl] += (mc * s_rho).sum(axis=1)
            den[sl] += mc @ n_c
            den_rho[sl] += mc @ sq_c
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "gap": np.where(den > 0, num_gap / den, np.nan),
            "excess": np.where(den > 0, num_ex / den, np.nan),
            "rho": np.where(den_rho > 0, num_rho / den_rho, np.nan),
        }


# ------------------------------------------------------------------------------ words


def t1_word(cell: dict[str, Any], q: float, can_cal: bool, gate_ok: bool, signs_agree: bool) -> str:
    if not gate_ok:
        return "NO WORD (gate)"
    gap, lo, hi = cell["point"], cell["lo"], cell["hi"]
    if can_cal and abs(gap) <= CAL_POINT and lo >= -CAL_MARGIN and hi <= CAL_MARGIN:
        return "CALIBRATED"
    if gap < -CAL_POINT and hi < 0 and signs_agree and q < 0.05:
        return "OVERCONFIDENT"
    if gap > CAL_POINT and lo > 0 and signs_agree and q < 0.05:
        return "UNDERCONFIDENT"
    return "INCONCLUSIVE"


def t2_word(
    cell: dict[str, Any], q: float, can_indep: bool, gate_ok: bool, signs_agree: bool
) -> str:
    if not gate_ok:
        return "NO WORD (gate)"
    e, lo, hi = cell["point"], cell["lo"], cell["hi"]
    if can_indep and abs(e) <= INDEP_POINT and lo >= -CAL_MARGIN and hi <= CAL_MARGIN:
        return "INDEPENDENCE SUPPORTED"
    if abs(e) > INDEP_POINT and (lo > 0 or hi < 0) and signs_agree and q < 0.05:
        return "DEPENDENT"
    return "INCONCLUSIVE"


def money_word(res: dict[str, Any], gate_ok: bool, q: float) -> str:
    if not gate_ok:
        return "NO WORD (gate)"
    seasons = [v["point"] for v in res["by_season"].values()]
    if res["point"] >= ROI_FLOOR and res["lo"] > 0 and all(s > 0 for s in seasons) and q < 0.05:
        return "PROFITABLE"
    if res["hi"] < ROI_FLOOR:
        return "NOT PROFITABLE"
    return "INCONCLUSIVE"


def interval_of(point: float, boot: np.ndarray) -> dict[str, Any]:
    lo, hi = hc.percentile_interval(boot)
    return {
        "point": point,
        "lo": lo,
        "hi": hi,
        "p_two_sided": hc.p_two_sided(boot),
        "p_le0": hc.p_le0(boot),
    }


# ------------------------------------------------------------------------------ scoring


def score_cells(
    cells: dict[tuple[str, int], dict[int, SeasonCell]],
    boot_date: dict[int, np.ndarray],
    boot_week: dict[int, np.ndarray],
) -> dict[str, Any]:
    """T1, T2, rho, money words, break-even rows and the plan for every (pool, size) cell."""
    out: dict[str, Any] = {}
    keys = [(p, m) for p in POOL_LABELS for m in x.SIZES]
    t1_raw: dict[tuple[str, int], dict[str, Any]] = {}
    t2_raw: dict[tuple[str, int], dict[str, Any]] = {}
    roi_raw: dict[tuple[str, int], dict[str, Any]] = {}
    for key in keys:
        per = cells.get(key, {})
        if not per:
            continue
        seasons = sorted(per)
        parts = [(per[s], boot_date[s], "date") for s in seasons]
        jb = joint_bootstrap(parts)
        est = x.estimators([(per[s].p, per[s].hit, per[s].idx) for s in seasons])
        wk_parts = [(per[s], boot_week[s], "week") for s in seasons]
        jw = joint_bootstrap(wk_parts)
        season_est = {s: x.estimators([(per[s].p, per[s].hit, per[s].idx)]) for s in seasons}
        t1_raw[key] = {
            **interval_of(est["gap"], jb["gap"]),
            "by_season": {str(s): season_est[s]["gap"] for s in seasons},
        }
        t2_raw[key] = {
            **interval_of(est["excess"], jb["excess"]),
            "by_season": {str(s): season_est[s]["excess"] for s in seasons},
            "rho": interval_of(est["rho"], jb["rho"]),
            "excess_week_clustered_interval": list(hc.percentile_interval(jw["excess"])),
        }
        profit = {s: parlay_profit(per[s]) for s in seasons}
        pg = [
            (
                boot_date[s],
                hc.cluster_totals(per[s].date_par, boot_date[s].shape[1], profit[s]),
                hc.cluster_totals(per[s].date_par, boot_date[s].shape[1]),
            )
            for s in seasons
        ]
        point, bd = hc.boot_ratio(pg)
        by_season = {}
        for s, part in zip(seasons, pg, strict=True):
            p1, b1 = hc.boot_ratio([part])
            by_season[str(s)] = interval_of(p1, b1) | {"parlays": per[s].n}
        roi_raw[key] = {**interval_of(point, bd), "by_season": by_season}
    q1 = dict(
        zip(keys, hc.bh_q([t1_raw.get(k, {}).get("p_two_sided", 1.0) for k in keys]), strict=True)
    )
    q2 = dict(
        zip(keys, hc.bh_q([t2_raw.get(k, {}).get("p_two_sided", 1.0) for k in keys]), strict=True)
    )
    q3 = dict(zip(keys, hc.bh_q([roi_raw.get(k, {}).get("p_le0", 1.0) for k in keys]), strict=True))
    for key in keys:
        label, m = key
        per = cells.get(key, {})
        name = f"{label}_size{m}"
        if not per:
            out[name] = {"parlays": 0, "gate_ok": False, "word": "NO WORD (gate)"}
            continue
        seasons = sorted(per)
        summ = [x.summarize_parlays(per[s].par) for s in seasons]
        pooled = x.pool_parlays(summ)
        gate_ok = bool(
            pooled["parlays"] >= GATE_MIN_PARLAYS
            and pooled["dates"] >= GATE_MIN_DATES
            and pooled["roi_halfwidth_cons_date"] <= GATE_MAX_HW
        )
        t1, t2, roi = t1_raw[key], t2_raw[key], roi_raw[key]
        sg1 = len({np.sign(v) for v in t1["by_season"].values() if v != 0}) <= 1
        sg2 = len({np.sign(v) for v in t2["by_season"].values() if v != 0}) <= 1
        t1["bh_q"], t2["bh_q"], roi["bh_q"] = q1[key], q2[key], q3[key]
        t1["word"] = t1_word(t1, q1[key], key in CAN_CALIBRATED, gate_ok, sg1)
        t2["word"] = t2_word(t2, q2[key], key in CAN_INDEP, gate_ok, sg2)
        roi["word"] = money_word(roi, gate_ok, q3[key])
        allp = {s: per[s] for s in seasons}
        n_all = sum(c.n for c in allp.values())
        joint = np.concatenate([c.hit[c.idx].prod(axis=1) for c in allp.values()])
        pj = np.concatenate([c.p[c.idx].prod(axis=1) for c in allp.values()])
        dd = np.concatenate([c.par["D"].to_numpy() for c in allp.values()])
        ovr = np.concatenate(
            [np.prod(1.0 + c.legs["ovr"].to_numpy()[c.idx], axis=1) for c in allp.values()]
        )
        single = np.concatenate(
            [
                np.where(c.hit[c.idx], c.legs["dec"].to_numpy()[c.idx] - 1.0, -1.0).ravel()
                for c in allp.values()
            ]
        )
        hr = float(joint.mean())
        out[name] = {
            "parlays": n_all,
            "by_season_parlays": {str(s): c.n for s, c in allp.items()},
            "gate": {**pooled, "ok": gate_ok},
            "T1": t1,
            "T2": t2,
            "money": roi,
            "break_even": {
                "mean_D": float(dd.mean()),
                "mean_joint_p": float(pj.mean()),
                "hit_rate": hr,
                "decimal_at_claim": float(1.0 / pj.mean()),
                "decimal_at_realised": float(1.0 / hr) if hr > 0 else float("nan"),
                "compounded_overround": float(ovr.mean()),
                "claimed_ev": float((pj * dd - 1.0).mean()),
                "realised_roi": float(
                    np.concatenate(list(map(parlay_profit, allp.values()))).mean()
                ),
                "ratio_hit_rate_to_mean_product_p": float(hr / pj.mean()),
                "C1_same_legs_singly_roi": float(single.mean()),
            },
            "leg_slices_by_stat": leg_slices(allp),
            "E2_plan": plan_block(allp),
        }
    return out


def leg_slices(cells: dict[int, SeasonCell]) -> dict[str, Any]:
    """Descriptive: gap and single-leg ROI by stat over the cell's leg appearances."""
    stat = np.concatenate([c.legs["stat"].to_numpy()[c.idx].ravel() for c in cells.values()])
    p = np.concatenate([c.p[c.idx].ravel() for c in cells.values()])
    hit = np.concatenate([c.hit[c.idx].ravel() for c in cells.values()])
    dec = np.concatenate([c.legs["dec"].to_numpy()[c.idx].ravel() for c in cells.values()])
    out = {}
    for st in STATS:
        mk = stat == st
        if mk.sum() == 0:
            continue
        out[st] = {
            "leg_appearances": int(mk.sum()),
            "gap": float((hit[mk] - p[mk]).mean()),
            "single_leg_roi": float(np.where(hit[mk] > 0.5, dec[mk] - 1.0, -1.0).mean()),
        }
    return out


def plan_block(cells: dict[int, SeasonCell]) -> dict[str, Any]:
    """E2: $2 x 10 parlays a week, R plan draws per season (a parlay is a one-outcome unit)."""
    weeks: dict[int, list[Any]] = {}
    for s, c in sorted(cells.items()):
        wk = (c.par["game_date"].dt.truncate("1w")).to_list()
        units = list(range(c.n))
        weeks[s] = hc.build_weeks(parlay_profit(c), wk, units)
    sims = hc.simulate_plan(weeks)
    return {str(s): hc.plan_summary(v["profit"], v["staked"]) for s, v in sims.items()}


# ------------------------------------------------------------------------------ preflight


def run_preflight(
    pf: hc.Preflight,
    data_root: Path,
    repo: Path,
    pre_alt_backup: Path | None = None,
    lookup: Any = hc.git_prereg_shas,
    adh_sha: str | None = None,
) -> None:
    assert max(SEASONS) <= MAX_SEASON  # before any file is read
    pf.add("seasons <= 2024 (holdout never loaded)", max(SEASONS) <= MAX_SEASON, str(SEASONS))
    hc.check_rule(pf, repo / RULE_PATH, RULE_ID, repo, RULE_SHA, lookup)
    hc.check_rule(pf, repo / ADH_PATH, ADH_ID, repo, adh_sha, lookup)
    pf.add("numpy version equals the rule's pin", np.__version__ == NUMPY_PIN, np.__version__)
    for f, want in CODE_SHA.items():
        path = repo / f
        got = hc.file_sha256(path) if path.exists() else "missing"
        pf.add(f"construction file {f} equals the rule's pinned sha256", got == want, got[:12])
    gate = data_root / GATE_JSON
    got = hc.file_sha256(gate) if gate.exists() else "missing"
    pf.add("gate.json equals the sha256 quoted in the rule", got == GATE_JSON_SHA, got[:12])
    g70 = data_root / LINE70_GATE_JSON
    frozen = json.loads(g70.read_text()).get("input_sha256", {}) if g70.exists() else {}
    pf.add("ALT_LINE_70 gate.json (input shas) readable", bool(frozen), str(g70))
    for f in MODEL_INPUTS:
        path = data_root / f
        ok = path.exists() and f in frozen and hc.file_sha256(path) == frozen[f]
        pf.add(f"input {f} matches the frozen sha256", ok, "")
    odds = data_root / ODDS_DB
    pf.add("odds store exists", odds.exists(), str(odds))
    if odds.exists():
        live = odds_fingerprint(odds)
        pf.add(
            "odds rows the rule reads equal the pinned fingerprint",
            live == ODDS_FINGERPRINT,
            f"{live} vs {ODDS_FINGERPRINT}",
        )
        if pre_alt_backup is not None:
            b = odds_fingerprint(pre_alt_backup)
            pf.add("odds rows the rule reads equal the pre-alternate backup", live == b, f"{b}")


def run_scoring(
    data_root: Path,
    pf: hc.Preflight,
    *,
    b: int = hc.B_DEFAULT,
    with_y: bool = True,
    gate_json: Path | None = None,
    check_counts: bool = True,
    run_attacks: bool = True,
    expected_gate: dict[tuple[str, int], bool] | None = EXPECTED_GATE_PASS,
) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON  # before any read
    odds_db = data_root / ODDS_DB
    tips = load_tips(data_root)
    models = {s: load_model(data_root, s, with_y) for s in SEASONS}
    dates = {
        s: np.sort(models[s]["game_date"].unique().to_numpy().astype("datetime64[D]"))
        for s in SEASONS
    }
    game_ids = {s: np.sort(models[s]["game_id"].unique().to_numpy()) for s in SEASONS}
    weeks = {
        s: np.unique(dates[s] - ((dates[s].astype(int) + 3) % 7).astype("timedelta64[D]"))
        for s in SEASONS
    }
    t5: dict[int, pl.DataFrame] = {}
    t60: dict[int, pl.DataFrame] = {}
    for s in SEASONS:
        for kind, store in ((PRIMARY_KIND, t5), (SENSITIVITY_KIND, t60)):
            t, meta = priced_rungs(odds_db, s, tips, models[s], kind)
            pf.add(
                f"{kind} {s}: timing rule violations are 0", meta["timing_rule_violations"] == 0, ""
            )
            t = add_indices(t, game_ids[s], dates[s])
            store[s] = with_outcome(t, models[s]) if with_y else t
    cells: dict[tuple[str, int], dict[int, SeasonCell]] = {}
    fingerprints: dict[str, Any] = {}
    gate = json.loads((gate_json or data_root / GATE_JSON).read_text()) if check_counts else None
    for pi, (label, lo, hi) in enumerate(x.POOLS):
        for m in x.SIZES:
            per: dict[int, SeasonCell] = {}
            for s in SEASONS:
                c = build_season_cell(t5[s], s, pi, lo, hi, m, dates[s], weeks[s], with_y)
                n = c.n if c else 0
                fp = c.fingerprint if c else None
                fingerprints[f"{s}_{label}_size{m}"] = fp
                if gate is not None:
                    gc = gate["seasons"][str(s)][label][f"size{m}"]
                    pf.add(
                        f"parlay count {s} {label} size {m} equals gate.json",
                        n == gc.get("parlays", 0),
                        f"{n} vs {gc.get('parlays', 0)}",
                    )
                    if fp is not None or "fingerprint_sha256" in gc:
                        pf.add(
                            f"fingerprint {s} {label} size {m} equals gate.json",
                            fp == gc.get("fingerprint_sha256"),
                            "",
                        )
                if c:
                    per[s] = c
            cells[(label, m)] = per
            if gate is not None:
                want = gate["pooled"][f"{label}_size{m}"]["parlays"]
                got = sum(c.n for c in per.values())
                pf.add(
                    f"pooled parlay count {label} size {m} equals the rule's table",
                    got == want,
                    f"{got} vs {want}",
                )
    if not with_y:
        return {"mode": "check-only", "counts_only": True, "fingerprints": fingerprints}
    pf.require()
    sizes_d = {s: len(dates[s]) for s in SEASONS}
    sizes_w = {s: len(weeks[s]) for s in SEASONS}
    boot_d = hc.make_boot(sizes_d, b, SEED_DATE_PARLAY)
    boot_w = hc.make_boot(sizes_w, b, hc.SEED_WEEK)
    res: dict[str, Any] = {
        "rule": f"{RULE_ID} sha256 {RULE_SHA}",
        "bootstrap": {"B": b, "date_seed": SEED_DATE_PARLAY, "week_seed": hc.SEED_WEEK},
        "numpy": np.__version__,
        "code_sha256": CODE_SHA,
        "fingerprints": fingerprints,
        "unstated_details": UNSTATED,
    }
    res["cells"] = score_cells(cells, boot_d, boot_w)
    if expected_gate is not None:
        want_gate = {f"{p}_size{m}": ok for (p, m), ok in expected_gate.items()}
        got_gate = {k: v.get("gate", {}).get("ok", False) for k, v in res["cells"].items()}
        if got_gate != want_gate:
            raise hc.PreregError(
                f"money-gate pattern {got_gate} differs from the rule's {want_gate}"
            )
    profitable = [
        k for k, v in res["cells"].items() if v.get("money", {}).get("word") == "PROFITABLE"
    ]
    res["profitable_cells"] = profitable
    if run_attacks and profitable:
        res["attacks"] = attacks(profitable, t5, t60, models, dates, weeks, with_y)
    else:
        res["attacks"] = "not run (no cell is PROFITABLE)"
    return res


# ------------------------------------------------------------------------------ attacks


def _rerun_roi(
    t5: dict[int, pl.DataFrame],
    dates: dict[int, np.ndarray],
    weeks: dict[int, np.ndarray],
    pool: str,
    m: int,
) -> dict[str, Any]:
    pi = POOL_LABELS.index(pool)
    _, lo, hi = x.POOLS[pi]
    n = 0
    tot = 0.0
    per_season = {}
    for s in sorted(t5):
        c = build_season_cell(t5[s], s, pi, lo, hi, m, dates[s], weeks[s], True)
        if c is None:
            continue
        pr = parlay_profit(c)
        per_season[str(s)] = float(pr.mean())
        n += c.n
        tot += float(pr.sum())
    return {"parlays": n, "roi": tot / n if n else float("nan"), "by_season": per_season}


def attacks(
    profitable: list[str],
    t5: dict[int, pl.DataFrame],
    t60: dict[int, pl.DataFrame],
    models: dict[int, pl.DataFrame],
    dates: dict[int, np.ndarray],
    weeks: dict[int, np.ndarray],
    with_y: bool,
    seeds: int = 50,
) -> dict[str, Any]:
    """Attacks on PROFITABLE cells, re-run through the same construction and seeds: planted future
    (each leg's model P taken from a player-game 1,000 rows away), shuffled P within (date, stat)
    (50 seeds), leave out the best 5% of parlays, pulled-line units added at their t60 price
    (viii). (vii) t60 + report window needs the availability store and is listed as manual."""
    out: dict[str, Any] = {}
    for name in profitable:
        pool, _, size = name.partition("_size")
        m = int(size)
        entry: dict[str, Any] = {"base": _rerun_roi(t5, dates, weeks, pool, m)}
        planted: dict[int, pl.DataFrame] = {}
        for s, df in t5.items():
            parts = []
            for st in STATS:
                d = models[s].filter(pl.col("stat") == st)
                surv = survival_matrix(d["p_ge"].to_list())
                sub = df.filter(pl.col("stat") == st)
                mi = (sub["mi"].to_numpy() + 1_000) % max(d.height, 1)
                parts.append(
                    sub.with_columns(pl.Series("pm", p_over_at(surv, mi, sub["point"].to_numpy())))
                )
            planted[s] = pl.concat(parts)
        entry["i_planted_future"] = _rerun_roi(planted, dates, weeks, pool, m)
        rois = []
        for seed in range(seeds):
            rng = np.random.default_rng([2026, 3, seed])
            shuf: dict[int, pl.DataFrame] = {}
            for s, df in t5.items():
                pm = df["pm"].to_numpy().copy()
                grp = df.with_row_index("r").group_by("game_date", "stat").agg(pl.col("r"))
                for rows in grp["r"].to_list():
                    pm[rows] = pm[rng.permutation(rows)]
                shuf[s] = df.with_columns(pl.Series("pm", pm))
            rois.append(_rerun_roi(shuf, dates, weeks, pool, m)["roi"])
        entry["ii_shuffle_p_roi_p05_p50_p95"] = [
            float(v) for v in np.nanpercentile(rois, [5, 50, 95])
        ]
        pi = POOL_LABELS.index(pool)
        _, lo, hi = x.POOLS[pi]
        profits, wk = [], []
        for s in sorted(t5):
            c = build_season_cell(t5[s], s, pi, lo, hi, m, dates[s], weeks[s], True)
            if c is not None:
                profits.append(parlay_profit(c))
                wk.append(c.week_par + s * 1000)
        allp = np.concatenate(profits)
        allw = np.concatenate(wk)
        keep = np.sort(allp)[: allp.size - int(np.ceil(0.05 * allp.size))]
        best_week = max(np.unique(allw), key=lambda w: allp[allw == w].sum())
        entry["iii_leave_out"] = {
            "roi_without_best_5pct": float(keep.mean()),
            "roi_without_best_week": float(allp[allw != best_week].mean()),
        }
        pulled: dict[int, pl.DataFrame] = {}
        for s in sorted(t5):
            extra = x.pulled_units(t60[s], t5[s]).select(t5[s].columns)
            pulled[s] = pl.concat([t5[s], extra], how="vertical")
        entry["viii_pulled_lines"] = _rerun_roi(pulled, dates, weeks, pool, m)
        entry["vii_t60_report_window"] = "manual: needs the availability store"
        out[name] = entry
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path(OUT_DEFAULT))
    ap.add_argument("--pre-alt-backup", type=Path, default=None)
    ap.add_argument(
        "--adh-sha", default=None, help="the exact frozen ALT_LADDER_HIST sha256 (64 hex)"
    )
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--b", type=int, default=hc.B_DEFAULT)
    args = ap.parse_args(argv)
    pf = hc.Preflight()
    run_preflight(pf, args.data_root, args.repo_root, args.pre_alt_backup, adh_sha=args.adh_sha)
    if args.check_only:
        res = run_scoring(args.data_root, pf, with_y=False)
        print(json.dumps(pf.as_dicts(), indent=1))
        raise SystemExit(0 if pf.ok else 2)
    pf.require()
    res = run_scoring(args.data_root, pf, b=args.b)
    res["checks"] = pf.as_dicts()
    res["adh_sha256"] = pf.info.get("ALT_LADDER_HIST_sha256")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}; profitable cells: {res['profitable_cells']}")


if __name__ == "__main__":
    main()
