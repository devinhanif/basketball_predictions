# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""ROAD_STARS harness (rule: docs/prereg/ROAD_STARS.md; refuses unless the text above `=== RESULTS BELOW ===` hashes to `--sha` AND a commit
whose subject is `prereg(ROAD_STARS): <sha>` is reachable from HEAD).

ROAD = production `context_residual` pts heads refit with two added as-of columns, `star10` = 1[m10_pts >= 20] and
`star10_away` = star10 x (1 - is_home_f); stored lower-tail `w_q` and `pi` unchanged (production month blocks, LightGBM, seed 0).
Comparator A0 = stored production + stored mixture. Primary cell = star rows, integer-support CRPS, paired game-clustered bootstrap.
Arms: ROAD; PL (past-only placebo: each star10_away is the value of a strictly earlier row of its player-season);
NUL_k (K >= 50 random-coin arms: the road flag replaced by a fair coin per (team, game), same coin for teammates; scored on the SELECT
season BEFORE ROAD and the threshold mean - 2 sd frozen in `null_threshold.json`); reb/ast/fg3m ROAD heads (descriptive no-harm only).

    M="-m research.eval.road_stars_eval"
    .venv/bin/python -W ignore $M gate  --sha <64 hex>     # minimum-data gates, control refits, planted future (heavy.lock)
    .venv/bin/python -W ignore $M null  --sha <64 hex>     # K=50 coin-flag arms on 2023 (checkpointed per arm), writes null_threshold.json
    .venv/bin/python -W ignore $M arms  --sha <64 hex>     # ROAD, PL (+ descriptive reb/ast/fg3m); refuses without the frozen null threshold
    .venv/bin/python -W ignore $M score --sha <64 hex>     # integer CRPS vs A0, guards, verdict, report (light)
    .venv/bin/python -W ignore $M run   --sha <64 hex>     # gate -> null -> arms -> score under ONE data/ops/heavy.lock

`nba.duckdb` is read-only; season 2025 is never loaded (asserted); there is no Stage 2 code here: a 2025 touch needs a new, committed, confirmed
HOLDOUT_ACCESS_LOG row and a new command.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, bh_adjust
from nba.eval.lower_tail_eval import load_components, randomized_pit
from nba.props.context_residual import PROP_STATS, ContextResidualConfig, stat_feature_names
from research.eval import four_factors_eval as F
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as RRF
from research.eval import workload_eval as WE
from research.recent_role import placebo as PB

ID = "ROAD_STARS"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/ROAD_STARS.md"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
OUT_DIR = ROOT / "reports/prereg_road_stars"
REPORT_MD = ROOT / "reports/prereg_road_stars.md"
CACHE = ROOT / "data/ops/road_stars_cache"
LT_DIR = PR.LT_DIR
DB = PR.DB
KEYS = ["game_id", "player_id"]
STAT = "pts"
OTHER_STATS = tuple(s for s in PROP_STATS if s != STAT)
SEASONS = (2023, 2024)
SELECT_SEASON = 2023
SEED = 0
STAR_THR = 20.0
ROAD_COLS = ("star10", "star10_away")
PL_COL = "pl_star10_away"
K_NULL = 50
NULL_SD_MULT = 2.0

FLOOR = -0.005  # star-cell integer CRPS, ROAD minus A0
ALPHA = 0.05  # BH m = 1 (pts only), so the adjusted p is the p
OVERALL_POINT_TOL = 0.0005  # no-harm guard on all rows
OVERALL_CI_TOL = 0.001
PLACEBO_MARGIN = -0.003  # ROAD - PL on the star cell
COV_TOL = 0.01
COV_TAUS: tuple[float, ...] = (0.10, 0.20, 0.80, 0.90)
SLICE_TOL = 0.01
MIN_SLICE_N = 300
GUARD_SLICES = ("non_star", "star_home", "star_road", "bench", "playoffs", "min<5", "cold_start")
REPRO_TOL = 1e-6
A0_REF = PR.A0_REF["pts"]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze
def freeze_commit(sha: str, git: Callable[..., str] = PR._git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def freeze_time(sha: str, git: Callable[..., str] = PR._git) -> float | None:
    h = freeze_commit(sha, git)
    if h is None:
        return None
    out = git("log", "-1", "--format=%ct", h).strip()
    return float(out) if out else None


def require_frozen(
    sha: str | None, doc: Path | None = None, git: Callable[..., str] = PR._git
) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = WE.frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; no arm may be fitted"
        )
    return sha


def check_checkpoint(path: Path, frozen_at: float | None) -> bool:
    """True if a checkpoint may be reused (exists, written after the freeze commit); older ones are refused loudly."""
    if not path.exists():
        return False
    if frozen_at is not None and path.stat().st_mtime < frozen_at:
        raise RuntimeError(f"{path} predates the freeze commit; delete it and rerun")
    return True


# --------------------------------------------------------------------------- frame and columns
def add_road_columns(fr: pl.DataFrame) -> pl.DataFrame:
    """`star10` and `star10_away` from the as-of production columns `m10_pts` and `is_home_f` (a null m10_pts is 'not a star')."""
    for c in (*ROAD_COLS, PL_COL):
        if c in fr.columns:
            raise ValueError(f"{c} already in the frame")
    star = (pl.col("m10_pts") >= STAR_THR).fill_null(False).cast(pl.Float64)
    return fr.with_columns(star.alias("star10")).with_columns(
        (pl.col("star10") * (1.0 - pl.col("is_home_f").cast(pl.Float64))).alias("star10_away")
    )


def build_frame() -> tuple[pl.DataFrame, ContextResidualConfig]:
    """Production feature rows (played rows, `build_features`) + the two ROAD columns + the past-only placebo of `star10_away`."""
    study = F.load_study(DB)
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    fr = add_road_columns(feats)
    fr = PB.permute_past_only(fr, ("star10_away",), SEED, "pl_")
    WE.assert_seasons(fr)
    return fr, study["cfg"]


def coin_frame(fr: pl.DataFrame, k: int, seed: int = SEED) -> pl.DataFrame:
    """Null arm k: `star10_away` = star10 x coin, one fair coin per (game_id, team_id) shared by every teammate (seed per arm)."""
    keys = fr.select("game_id", "team_id").unique().sort(["game_id", "team_id"])
    coin = (np.random.default_rng([seed, 1000 + k]).random(keys.height) < 0.5).astype(float)
    keys = keys.with_columns(pl.Series("_coin", coin))
    out = fr.join(keys, on=["game_id", "team_id"], how="left", maintain_order="left")
    if out.height != fr.height or out["_coin"].null_count() > 0:
        raise RuntimeError("coin join changed the rows")
    return out.with_columns((pl.col("star10") * pl.col("_coin")).alias("star10_away")).drop("_coin")


def names_for(stat: str, arm: str) -> list[str]:
    base = stat_feature_names(stat)
    if arm == "ROAD":
        return [*base, *ROAD_COLS]
    if arm == "PL":
        return [*base, "star10", PL_COL]
    if arm == "CTRL":
        return base
    raise ValueError(arm)


def walk(
    fr: pl.DataFrame, cfg: ContextResidualConfig, stat: str, arm: str, seasons: tuple[int, ...]
) -> dict[str, Any]:
    return RRF.collect_heads(fr, stat, cfg, names_for(stat, arm), test_seasons=seasons, log=False)


# --------------------------------------------------------------------------- row metadata and masks
def meta_for(fr: pl.DataFrame, comp: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """m10_pts and is_home_f of the component rows (stored order); raises on any null or missing row."""
    want = pl.DataFrame(
        {"game_id": comp["row_gid"].astype(str), "player_id": comp["row_pid"].astype(np.int64)}
    )
    j = want.join(
        fr.select("game_id", "player_id", "m10_pts", "is_home_f"),
        on=KEYS,
        how="left",
        maintain_order="left",
    )
    if j.height != want.height or j["m10_pts"].null_count() or j["is_home_f"].null_count():
        raise RuntimeError("row metadata missing or null for some component rows")
    return {
        "star": j["m10_pts"].to_numpy() >= STAR_THR,
        "home": j["is_home_f"].cast(pl.Float64).to_numpy() == 1.0,
    }


def masks_for(comp: dict[str, np.ndarray], meta: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    pm = PR.slice_masks(comp)
    star, home = meta["star"], meta["home"]
    return {
        "all": pm["all"],
        "star": star,
        "non_star": ~star,
        "star_home": star & home,
        "star_road": star & ~home,
        "bench": pm["bench"],
        "playoffs": pm["playoffs"],
        "min<5": pm["min<5"],
        "cold_start": pm["cold_start"],
        "cold_start_star": pm["cold_start"] & star,
    }


def crps_of(
    comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], stat: str = STAT
) -> np.ndarray:
    return PR.crps_int(WE.mixture_qi(comp, pic, stat), comp["row_y"])


# --------------------------------------------------------------------------- null (coin) arms
def null_path() -> Path:
    return OUT_DIR / "null_threshold.json"


def null_ckpt(season: int, k: int) -> Path:
    return CACHE / f"null_{season}_{k:03d}.npz"


def null_summary(deltas: np.ndarray) -> dict[str, float]:
    d = np.asarray(deltas, dtype=float)
    mean, sd = float(d.mean()), float(d.std(ddof=1))
    return {"K": int(len(d)), "mean": mean, "sd": sd, "threshold": mean - NULL_SD_MULT * sd}


def run_null(
    sha: str,
    k_total: int = K_NULL,
    seasons: tuple[int, ...] = (SELECT_SEASON,),
) -> dict[str, Any]:
    """K coin-flag arms per season (the SELECT season by default; 2024 only on request), scored vs A0 on the star cell. Writes the frozen
    threshold BEFORE any ROAD arm exists. A season's arms are 'season-only' walks: identical month blocks and training sets to the 2-season walk."""
    if k_total < K_NULL:
        raise RuntimeError(f"the rule needs K >= {K_NULL}")
    require_frozen(sha)
    require_gate(sha)
    frozen_at = freeze_time(sha)
    fr, cfg = build_frame()
    comp = load_components(STAT, LT_DIR)
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"sha256": sha, "K_required": K_NULL, "seasons": {}}
    for s in seasons:
        comp_r, pic_r = RRF.restrict_stored(comp, pic, s)
        star = meta_for(fr, comp_r)["star"]
        a0 = crps_of(comp_r, pic_r)
        ds, dall = [], []
        for k in range(k_total):
            p = null_ckpt(s, k)
            if check_checkpoint(p, frozen_at):
                z = np.load(p)
                cr = z["crps"].astype(np.float64)
            else:
                t0 = time.time()
                h = walk(coin_frame(fr, k), cfg, STAT, "ROAD", (s,))
                if not RRF.same_rows(h, comp_r):
                    raise RuntimeError(f"null arm {k} season {s}: rows differ from stored")
                cr = crps_of(h, pic_r)
                np.savez_compressed(p, crps=cr.astype(np.float32))
                _log(f"null {s} arm {k + 1}/{k_total} star delta {(cr - a0)[star].mean():+.5f} ({time.time() - t0:.0f}s)")  # fmt: skip
                cr = cr.astype(np.float32).astype(np.float64)
            ds.append(float((cr - a0)[star].mean()))
            dall.append(float((cr - a0).mean()))
        out["seasons"][str(s)] = {**null_summary(np.array(ds)), "deltas_star": ds, "mean_overall": float(np.mean(dall)), "n_star": int(star.sum()), "n_rows": int(len(a0))}  # fmt: skip
    WE.write_json(null_path(), out)
    return out


def require_null(sha: str) -> dict[str, Any]:
    p = null_path()
    if not p.exists():
        raise RuntimeError(
            "no null_threshold.json: run the null stage first (null arms are scored BEFORE ROAD)"
        )
    d = json.loads(p.read_text())
    s = d["seasons"].get(str(SELECT_SEASON))
    if d.get("sha256") != sha or s is None or s["K"] < K_NULL:
        raise RuntimeError("the frozen null threshold is missing, for another rule, or K < 50")
    return d


def threshold_for(null: dict[str, Any], season: int) -> dict[str, float]:
    """Season's own null if it was run; otherwise the SELECT season's mean and sd carried over."""
    return null["seasons"].get(str(season)) or null["seasons"][str(SELECT_SEASON)]


# --------------------------------------------------------------------------- arms
def heads_path(stat: str, arm: str) -> Path:
    return CACHE / f"heads_{stat}_{arm}.npz"


def load_heads(stat: str, arm: str) -> dict[str, np.ndarray]:
    z = np.load(heads_path(stat, arm), allow_pickle=True)
    return {k: z[k] for k in z.files}


def run_arms(sha: str, with_other_stats: bool = True) -> None:
    require_frozen(sha)
    require_gate(sha)
    require_null(sha)
    frozen_at = freeze_time(sha)
    fr, cfg = build_frame()
    CACHE.mkdir(parents=True, exist_ok=True)
    plan = [(STAT, "ROAD"), (STAT, "PL")]
    if with_other_stats:
        plan += [(s, "ROAD") for s in OTHER_STATS]
    for stat, arm in plan:
        p = heads_path(stat, arm)
        if check_checkpoint(p, frozen_at):
            _log(f"{stat} {arm} from checkpoint")
            continue
        t0 = time.time()
        h = walk(fr, cfg, stat, arm, SEASONS)
        if not RRF.same_rows(h, load_components(stat, LT_DIR)):
            raise RuntimeError(f"{stat} {arm}: rows differ from the stored components")
        np.savez_compressed(p, **h)
        _log(f"{stat} {arm} walk done {time.time() - t0:.0f}s")


# --------------------------------------------------------------------------- scoring
def coverage_cells(
    qis: dict[str, np.ndarray], y: np.ndarray, season: np.ndarray, masks: dict[str, np.ndarray]
) -> dict[str, Any]:
    """Randomised-PIT coverage P(PIT < tau) of each arm; the SAME uniforms for every arm (seed 0 over the full array)."""
    out: dict[str, Any] = {}
    pits = {a: randomized_pit(q.astype(float), y.astype(float)) for a, q in qis.items()}
    for s in SEASONS:
        out[str(s)] = {
            cell: {
                a: {
                    f"{t:.2f}": float((pit[(season == s) & masks[cell]] < t).mean())
                    for t in COV_TAUS
                }
                for a, pit in pits.items()
            }
            for cell in ("star", "all")
        }
    return out


def coverage_ok(cov: dict[str, Any]) -> tuple[bool, list[Any]]:
    bad = []
    for s, cells in cov.items():
        for cell, arms in cells.items():
            for t, v0 in arms["A0"].items():
                worse = abs(arms["ROAD"][t] - float(t)) - abs(v0 - float(t))
                if worse > COV_TOL:
                    bad.append((s, cell, t, worse))
    return not bad, bad


def score_pts(smoke: bool = False) -> dict[str, Any]:
    fr, _ = build_frame()
    comp = load_components(STAT, LT_DIR)
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    y, gid, season = comp["row_y"], comp["row_gid"].astype(str), comp["row_season"]
    meta = meta_for(fr, comp)
    masks = masks_for(comp, meta)
    arms = {"A0": comp}
    if smoke:  # plumbing only: ROAD and PL are A0 (deltas exactly 0)
        arms.update({"ROAD": comp, "PL": comp})
    else:
        for a in ("ROAD", "PL"):
            arms[a] = load_heads(STAT, a)
            if not RRF.same_rows(arms[a], comp):
                raise RuntimeError(f"{a}: rows differ from the stored components")
    qis = {a: WE.mixture_qi(c, pic, STAT) for a, c in arms.items()}
    sc = {a: PR.crps_int(q, y) for a, q in qis.items()}
    cells: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        for name, (a, b, cell) in {
            "ROAD_minus_A0_star": ("ROAD", "A0", "star"),
            "ROAD_minus_A0_all": ("ROAD", "A0", "all"),
            "ROAD_minus_PL_star": ("ROAD", "PL", "star"),
            "PL_minus_A0_star": ("PL", "A0", "star"),
        }.items():
            m = sm & masks[cell]
            cells.append({"season": s, "contrast": name, "cell": cell, "n_rows": int(m.sum()), "n_games": int(len(np.unique(gid[m]))), "a_crps": float(sc[a][m].mean()), "b_crps": float(sc[b][m].mean()), **WE.delta_stats(sc[a][m] - sc[b][m], gid[m])})  # fmt: skip
        d = sc["ROAD"] - sc["A0"]
        for nm, mk in masks.items():
            m = mk & sm
            rec: dict[str, Any] = {"season": s, "slice": nm, "n": int(m.sum())}
            if m.sum() >= MIN_SLICE_N or nm == "all":
                ds = WE.delta_stats(d[m], gid[m])
                rec.update({k: ds[k] for k in ("delta", "ci_lo", "ci_hi")})
            slices.append(rec)
    cov = coverage_cells({"A0": qis["A0"], "ROAD": qis["ROAD"]}, y, season, masks)
    desc: list[dict[str, Any]] = []  # reb / ast / fg3m: descriptive no-harm, cannot rescue
    for stat in OTHER_STATS:
        c0 = load_components(stat, LT_DIR)
        if smoke or not heads_path(stat, "ROAD").exists():
            continue
        hr = load_heads(stat, "ROAD")
        if not RRF.same_rows(hr, c0):
            raise RuntimeError(f"{stat} ROAD: rows differ")
        d = crps_of(hr, pic, stat) - crps_of(c0, pic, stat)
        g0, s0 = c0["row_gid"].astype(str), c0["row_season"]
        for s in SEASONS:
            for cell in ("star", "all"):
                m = (s0 == s) & masks[cell]
                desc.append({"stat": stat, "season": s, "cell": cell, "n_rows": int(m.sum()), **WE.delta_stats(d[m], g0[m])})  # fmt: skip
    return {"cells": cells, "slices": slices, "coverage": cov, "descriptive_other_stats": desc, "a0_crps": {str(s): float(sc["A0"][season == s].mean()) for s in SEASONS}}  # fmt: skip


def decide(res: dict[str, Any], null: dict[str, Any], gate_ok: bool) -> dict[str, Any]:
    """The frozen pass rule. Per season (2023 select, 2024 report) all of: star-cell ROAD-A0 <= FLOOR with CI upper < 0 and p <= 0.05 (BH m = 1);
    overall no-harm (point <= +0.0005, CI upper <= +0.001); ROAD-PL on stars <= -0.003; star-cell delta <= null mean - 2 null sd (that season's
    null, else the select season's carried over); coverage no worse than A0 by > 0.01 at the four taus on stars and overall; no guard slice with
    n >= 300 worse by > +0.01; gate clean. Pass needs both seasons."""
    cell = {(c["season"], c["contrast"]): c for c in res["cells"]}
    cov_ok, cov_bad = coverage_ok(res["coverage"])
    per: dict[str, Any] = {}
    for s in SEASONS:
        a = cell[(s, "ROAD_minus_A0_star")]
        o = cell[(s, "ROAD_minus_A0_all")]
        p = cell[(s, "ROAD_minus_PL_star")]
        thr = threshold_for(null, s)
        worse = [
            (x["slice"], x["n"], x["delta"])
            for x in res["slices"]
            if x["season"] == s
            and x["slice"] in GUARD_SLICES
            and x.get("n", 0) >= MIN_SLICE_N
            and x.get("delta", -1.0) > SLICE_TOL  # fmt: skip
        ]
        primary = bool(a["delta"] <= FLOOR and a["ci_hi"] < 0 and bh_adjust(np.array([a["p"]]))[0] <= ALPHA)  # fmt: skip
        no_harm = bool(o["delta"] <= OVERALL_POINT_TOL and o["ci_hi"] <= OVERALL_CI_TOL)
        placebo = bool(p["delta"] <= PLACEBO_MARGIN)
        null_ok = bool(a["delta"] <= thr["threshold"] and a["delta"] <= FLOOR)
        per[str(s)] = {
            "primary": primary, "no_harm": no_harm, "placebo": placebo, "null_guard": null_ok, "slices_ok": not worse, "slices_worse": worse,
            "star_delta": a["delta"], "star_ci": [a["ci_lo"], a["ci_hi"]], "overall_delta": o["delta"], "overall_ci_hi": o["ci_hi"],
            "road_minus_pl": p["delta"], "null_mean": thr["mean"], "null_sd": thr["sd"], "null_threshold": thr["threshold"], "mde": a["mde"],
        }  # fmt: skip
    ok_s = {
        s: bool(
            v["primary"] and v["no_harm"] and v["placebo"] and v["null_guard"] and v["slices_ok"]
        )
        for s, v in per.items()
    }
    return {
        "per_season": per,
        "coverage_ok": cov_ok,
        "coverage_bad": cov_bad,
        "gate_ok": bool(gate_ok),
        "pass": bool(all(ok_s.values()) and cov_ok and gate_ok),
        "stage1_pass_both_seasons": bool(all(ok_s.values()) and cov_ok and gate_ok),
    }


def run_score(sha: str | None, smoke: bool = False) -> dict[str, Any]:
    if smoke:
        sha, gate_ok = "smoke", True
        null = {"seasons": {str(SELECT_SEASON): {"K": 0, "mean": 0.0, "sd": 0.0, "threshold": 0.0}}}
        out_dir = OUT_DIR / "smoke"
    else:
        sha = require_frozen(sha)
        gate_ok = bool(require_gate(sha)["gate_ok"])
        null = require_null(sha)
        t_null = null_path().stat().st_mtime
        for p in (heads_path(STAT, "ROAD"), heads_path(STAT, "PL")):
            if p.stat().st_mtime < t_null:
                raise RuntimeError(f"{p} was written before the frozen null threshold: ROAD must be fitted after the null arms are scored")  # fmt: skip
        out_dir = OUT_DIR
    res = score_pts(smoke)
    verdict = decide(res, null, gate_ok)
    out = {
        "sha256": sha,
        "scores": res,
        "verdict": verdict,
        "null": null,
        "stage2": "no code here; 2025 never loaded",
    }
    WE.write_json(out_dir / "results.json", out)
    if not smoke:
        REPORT_MD.write_text(render(out))
    return out


def _f(x: Any, nd: int = 5) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def render(out: dict[str, Any]) -> str:
    v = out["verdict"]
    lines = [f"# {ID} results", "", f"Rule sha256 {out['sha256'][:8]}. Season 2025 never loaded. Stage 1 pass (both seasons, all guards): **{v['pass']}**.", "",
             "| season | contrast | n rows | n games | delta | CI | p | MDE |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for c in out["scores"]["cells"]:
        lines.append(f"| {c['season']} | {c['contrast']} | {c['n_rows']} | {c['n_games']} | {_f(c['delta'])} | [{_f(c['ci_lo'])}, {_f(c['ci_hi'])}] | {_f(c['p'], 4)} | {_f(c['mde'])} |")  # fmt: skip
    lines += ["", "| season | slice | n | ROAD - A0 | CI |", "|---|---|---|---|---|"]
    for s in out["scores"]["slices"]:
        ci = "" if "ci_lo" not in s else f"[{_f(s['ci_lo'])}, {_f(s['ci_hi'])}]"
        lines.append(f"| {s['season']} | {s['slice']} | {s['n']} | {_f(s.get('delta'))} | {ci} |")
    lines += ["", "Descriptive reb/ast/fg3m (cannot rescue):", "", "| stat | season | cell | n | delta | CI |", "|---|---|---|---|---|---|"]  # fmt: skip
    for d in out["scores"]["descriptive_other_stats"]:
        lines.append(f"| {d['stat']} | {d['season']} | {d['cell']} | {d['n_rows']} | {_f(d['delta'])} | [{_f(d['ci_lo'])}, {_f(d['ci_hi'])}] |")  # fmt: skip
    lines += ["", "Coverage P(PIT < tau):", "", "```json", json.dumps(out["scores"]["coverage"], indent=1), "```", "", "```json", json.dumps(v, indent=1, default=float), "```", ""]  # fmt: skip
    return "\n".join(lines)


# --------------------------------------------------------------------------- gate
def planted_heads(fr: pl.DataFrame, cfg: ContextResidualConfig) -> list[dict[str, Any]]:
    """As-of plant on the real frame (pts, 2024): overwrite labels and BOTH road columns of rows dated on/after a mid-2024 cutoff; centre/scale of
    rows in blocks that end before the cutoff's month are bit-identical, rows in later blocks change."""
    chk = WE._check
    d24 = fr.filter(pl.col("season") == 2024).sort("game_date")["game_date"]
    cut = d24[d24.len() // 2]
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
        pl.when(late).then(99.0).otherwise(pl.col("pts").cast(pl.Float64)).alias("pts"),
        *[pl.when(late).then(1.0).otherwise(pl.col(c)).alias(c) for c in ROAD_COLS],
    )
    a = walk(fr, cfg, STAT, "ROAD", (2024,))
    b = walk(edited, cfg, STAT, "ROAD", (2024,))
    ja = pl.DataFrame({"game_id": a["row_gid"], "player_id": a["row_pid"], "ca": a["row_c"], "sa": a["row_s"]})  # fmt: skip
    jb = pl.DataFrame({"game_id": b["row_gid"], "player_id": b["row_pid"], "cb": b["row_c"], "sb": b["row_s"]})  # fmt: skip
    date = fr.select("game_id", "player_id", "game_date")
    j = ja.join(jb, on=KEYS).join(date, on=KEYS)
    nxt = cut.replace(day=1).replace(month=cut.month % 12 + 1, year=cut.year + (cut.month == 12))
    pre = j.filter(pl.col("game_date") < cut.replace(day=1))
    post = j.filter(pl.col("game_date") >= nxt)
    same = float(max(np.abs(pre["ca"].to_numpy() - pre["cb"].to_numpy()).max(), np.abs(pre["sa"].to_numpy() - pre["sb"].to_numpy()).max())) if pre.height else 0.0  # fmt: skip
    moved = float(np.abs(post["ca"].to_numpy() - post["cb"].to_numpy()).max()) if post.height else 0.0  # fmt: skip
    return [
        chk(
            "planted future (ROAD heads, pts): centre and scale of rows in blocks before the cutoff identical",
            {"cutoff": str(cut), "n_rows": pre.height, "max_abs_diff": same},
            "== 0 and n > 0",
            same == 0.0 and pre.height > 0,
        ),  # fmt: skip
        chk(
            "control (ROAD heads, pts): rows in later blocks change",
            {"n_rows": post.height, "max_abs_diff": moved},
            "> 0",
            moved > 0.0,
        ),  # fmt: skip
    ]


def coin_checks(fr: pl.DataFrame) -> dict[str, Any]:
    """The coin construction: teammates share one coin, about half the (team, game) cells are 'away', deterministic per arm, different across arms."""
    a, b, a2 = coin_frame(fr, 0), coin_frame(fr, 1), coin_frame(fr, 0)
    star = fr["star10"].to_numpy() == 1.0
    g = a.filter(pl.col("star10") == 1.0).group_by(["game_id", "team_id"]).agg(pl.col("star10_away").n_unique().alias("nu"))  # fmt: skip
    return {
        "teammate_coin_shared": bool((g["nu"] == 1).all()),
        "share_away_among_stars": float(a["star10_away"].to_numpy()[star].mean()),
        "deterministic": bool(np.array_equal(a["star10_away"].to_numpy(), a2["star10_away"].to_numpy())),
        "differs_across_arms": bool(not np.array_equal(a["star10_away"].to_numpy(), b["star10_away"].to_numpy())),
    }  # fmt: skip


def run_gate(
    sha: str | None, draft: bool = False, null_seasons: tuple[int, ...] = (SELECT_SEASON,)
) -> dict[str, Any]:
    """Minimum-data gates and plants. Scored arms: CONTROL refits only (the pts heads with production features: 2-season walk and the season-only
    walks the null arms use); they must equal the stored components. No ROAD / placebo / coin arm is scored here."""
    if not draft:
        require_frozen(sha)
    t0 = time.time()
    chk = WE._check
    tests: list[dict[str, Any]] = []
    comp = load_components(STAT, LT_DIR)
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    if int(comp["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in components")
    refs = json.loads((LT_DIR / "candidates.json").read_text())
    crps = crps_of(comp, pic)
    cells = []
    for s in SEASONS:
        m = comp["row_season"] == s
        cells.append({"season": s, "crps_int": float(crps[m].mean()), "reference": float(refs[STAT][str(s)]["mixture"]["crps_int"]), "n": int(m.sum()), "n_ref": int(refs[STAT][str(s)]["off"]["n"])})  # fmt: skip
    tests.append(chk("G1 A0 reproduction from stored components (pts mixture integer CRPS vs candidates.json and the quoted 3.140236 / 3.176840)", {c["season"]: round(c["crps_int"], 6) for c in cells}, f"abs diff <= {REPRO_TOL}, n equal", all(abs(c["crps_int"] - c["reference"]) <= REPRO_TOL and c["n"] == c["n_ref"] and abs(c["crps_int"] - A0_REF[c["season"]]) <= 5e-7 for c in cells)))  # fmt: skip
    fr, cfg = build_frame()
    meta = meta_for(fr, comp)  # raises on any null / missing row
    nulls = int(fr.filter(pl.col("n_prior") >= 5)["m10_pts"].null_count())
    tests.append(chk("G2 as-of columns m10_pts and is_home_f attach to every A0 row with 0 nulls (no NULL pattern)", {"n_a0_rows": int(len(comp["row_y"])), "n_star": int(meta["star"].sum()), "frame_null_m10_pts_n_prior>=5": nulls}, "all A0 rows found, 0 null", nulls == 0))  # fmt: skip
    cc = coin_checks(fr)
    tests.append(chk("coin construction: shared by teammates, deterministic per arm, ~50% away, differs across arms", cc, "shared, deterministic, differs, share in [0.4, 0.6]", cc["teammate_coin_shared"] and cc["deterministic"] and cc["differs_across_arms"] and 0.4 <= cc["share_away_among_stars"] <= 0.6))  # fmt: skip
    ctrl = walk(fr, cfg, STAT, "CTRL", SEASONS)
    r = RRF.heads_vs_stored(ctrl, comp)
    tests.append(chk("control refit of the pts heads (production features only) equals the stored components", {"rows_equal": r["rows_equal"], "max_abs_diff": r["max_abs_diff"]}, f"rows equal, max abs diff <= {REPRO_TOL}", r["rows_equal"] and r["max_abs_diff"] <= REPRO_TOL))  # fmt: skip
    del ctrl
    for s in null_seasons:
        comp_r, _ = RRF.restrict_stored(comp, pic, s)
        c1 = walk(fr, cfg, STAT, "CTRL", (s,))
        r1 = RRF.heads_vs_stored(c1, comp_r)
        tests.append(chk(f"control refit, season-only walk {s} (the construction of the null arms) equals the stored components of that season", {"rows_equal": r1["rows_equal"], "max_abs_diff": r1["max_abs_diff"]}, f"rows equal, max abs diff <= {REPRO_TOL}", r1["rows_equal"] and r1["max_abs_diff"] <= REPRO_TOL))  # fmt: skip
    tests += planted_heads(fr, cfg)
    masks_all = masks_for(comp, meta)
    sizes = {
        str(s): {k: int((mk & (comp["row_season"] == s)).sum()) for k, mk in masks_all.items()}
        for s in SEASONS
    }
    tests.append(chk("G4 slice sizes (n rows) per season", sizes, f"star, star_home, star_road, non_star and every guard slice >= {MIN_SLICE_N} in both seasons", all(sizes[str(s)][k] >= MIN_SLICE_N for s in SEASONS for k in ("star", "star_home", "star_road", "non_star", *GUARD_SLICES))))  # fmt: skip
    tests.append(chk("G5 power (known FAIL, waived: Devin's option 1, non-negotiable 8 exception): best case 0.001-0.003 on stars vs floor -0.005; MDE ~0.0037 per season", {"waived_by": "Devin 2026-10-12"}, "recorded, not blocking", True))  # fmt: skip
    log_text = HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else ""
    tests.append(chk("HOLDOUT_ACCESS_LOG: no row names ROAD_STARS (never evaluated on season 2025)", {"rows_naming_id": sum(ID in ln for ln in log_text.splitlines())}, "== 0", ID not in log_text))  # fmt: skip
    gate = {"tests": tests, "gate_ok": all(t["ok"] for t in tests), "sha256": sha, "draft": draft, "seconds": time.time() - t0, "slice_sizes": sizes}  # fmt: skip
    WE.write_json(OUT_DIR / ("gate_draft.json" if draft else "gate.json"), gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return gate


def require_gate(sha: str) -> dict[str, Any]:
    p = OUT_DIR / "gate.json"
    if not p.exists():
        raise RuntimeError("no gate.json: run the gate stage first")
    g = json.loads(p.read_text())
    if not g.get("gate_ok") or g.get("sha256") != sha:
        raise RuntimeError("the frozen gate has not passed for this rule: stop (kill criterion)")
    return g


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "null", "arms", "score", "run"])
    ap.add_argument("--sha", default=None)
    ap.add_argument(
        "--null-2024",
        action="store_true",
        help="also K=50 coin arms on the report season (the threshold is then its own)",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="score only: plumbing check, A0 in every arm, writes under smoke/",
    )
    ap.add_argument(
        "--no-other-stats",
        action="store_true",
        help="arms: skip the descriptive reb/ast/fg3m refits",
    )
    a = ap.parse_args(argv)
    if a.cmd == "score" and a.smoke:
        run_score(None, smoke=True)
        return 0
    sha = require_frozen(a.sha)
    nul = (SELECT_SEASON, 2024) if a.null_2024 else (SELECT_SEASON,)
    with WE.heavy():
        if a.cmd == "gate":
            return 0 if run_gate(sha, null_seasons=nul)["gate_ok"] else 1
        if a.cmd == "null":
            require_gate(sha)
            run_null(sha, seasons=nul)
            return 0
        if a.cmd == "arms":
            run_arms(sha, not a.no_other_stats)
            return 0
        if a.cmd == "score":
            run_score(sha)
            return 0
        if not run_gate(sha, null_seasons=nul)["gate_ok"]:
            return 1
        run_null(sha, seasons=nul)
        run_arms(sha, not a.no_other_stats)
        run_score(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
