# ruff: noqa: E501
"""XGB_PTS_VS_LT harness (rule: docs/prereg/XGB_PTS_VS_LT.md; DRAFT until frozen).

Question: does the leak-free XGBoost quantile pts model (``hybrid_lf`` pts arm, frozen configs of
``data/colab/ctxres_v3_leakfree/run_20261009``) beat production-as-it-now-stands for pts (context
residual + lower-tail short-minutes mixture, integer support) on 2024, with 2023 as the selection /
consistency season?  Arms: A0 = production lower-tail mixture on integer support (stored
components, no refit); A1 = hybrid_lf pts refit through the pinned engine; A2 = the same recipe on
v1 features (descriptive).  Primary metric: integer-support CRPS, A1 - A0.

    M="-m research.eval.xgb_pts_vs_lt_eval"
    uv run --offline --with xgboost python -W ignore $M gate  --allow-draft --doc docs/prereg/XGB_PTS_VS_LT.md
    uv run --offline --with xgboost python -W ignore $M smoke --doc docs/prereg/XGB_PTS_VS_LT.md --sha <64 hex>
    uv run --offline --with xgboost python -W ignore $M run   --doc docs/prereg/XGB_PTS_VS_LT.md --sha <64 hex>
    uv run --offline --with xgboost python -W ignore $M report --doc docs/prereg/XGB_PTS_VS_LT.md --sha <64 hex>

``--sha`` must equal the sha256 of everything above the ``=== RESULTS BELOW ===`` line and the
document's ``Status:`` line must say FROZEN; ``--allow-draft`` skips both and is for the gate stage
only.  The gate stage computes NO A1 - A0 contrast of any kind (the rule is not frozen yet; it must
not be shaped by a result).  ``nba.duckdb`` is opened read-only; every SQL carries ``season <= 2024``;
season 2025 is never loaded.  Heavy stages take ``data/ops/heavy.lock`` (``mkdir``; retry 120 s;
``rmdir`` in ``finally``).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
)
from nba.eval.lower_tail_eval import EPS, arm_grid, guard, load_components, randomized_pit
from nba.features.game_tipoff import build_game_tipoff
from nba.props.context_residual import to_integer_support
from nba.props.metrics import paired_score_delta_ci
from research.eval.minutes_hazard_eval import HeavyLock, frozen_sha

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/XGB_PTS_VS_LT.md"
OUT_DIR = ROOT / "reports/prereg_xgb_pts_vs_lt"
REPORT_MD = ROOT / "reports/prereg_xgb_pts_vs_lt.md"
CACHE_DIR = ROOT / "data/ops/xgb_pts_vs_lt_cache"
LT_DIR = ROOT / "reports/lower_tail"
LOWER_TAIL_JSON = LT_DIR / "candidates.json"
RUN_DIR = ROOT / "data/colab/ctxres_v3_leakfree/run_20261009"
RT_UNITS = ROOT / "data/reviews/redteam_ctxres_v3_leakfree_2026-10-09/units"
JOB = ROOT / "research/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py"
ENGINE = ROOT / "research/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py"
ENGINE_SHA_ORIGINAL = "4952cc9c46f1b180d530a903426322ee4f1cee73ab5822ffac5a89450e5b8ca6"
DK_CACHE = CACHE_DIR / "dk_t60_pts.parquet"
DEFAULT_PAIRS_DIR = Path(
    "/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/"
    "7ff798e7-932b-4807-b103-45d365238231/scratchpad"
)

STAT = "pts"
SEASONS = (2023, 2024)
KEYS = ["game_id", "player_id"]
PHI = 1610612755
# --- frozen thresholds (mirrors the document; changing one changes the rule)
FLOOR = -0.005
N_BOOT = 2000
BOOT_SEED = 0
TLL_MARGIN = 0.002
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
TEAM_SHARE_MAX = 0.25
NULL_GAP_PP = 2.0
KEY_DROP_MAX = 0.005
PROXY_HOUR_ET = 19.0
REPRO_A0_TOL = 1e-6
REPRO_A1_TOL = 1e-4
REF_FAIR_2024 = -0.007822916307786082  # red team light1.json 'disc_both|pts|2024'
REF_FAIR_2023 = -0.014498540534795951  # red team light1.json 'disc_both|pts|2023'
THRESHOLDS = (10, 15, 20, 25, 30)
BUCKETS = ("<5", "5-20", ">20")

UNSTATED: list[str] = [
    "A0 = the stored lower-tail components (reports/lower_tail/components_pts.npz + components_pi.npz, collected 2026-10-11 on the current DB) pushed through the production arm_grid('mixture') and to_integer_support; no refit. The three input files are pinned by sha256 in the document.",
    "A1/A2 rows = the frozen ctxres_v2/ridge_v2 export of 2026-10-08/09 (2023 n 27,619; 2024 n 27,583). The 2026-10-11 data clean-up added 118 scored rows (10 games) to the 2024 production set that the export does not contain. Every contrast is scored on the key INTERSECTION per season; the dropped A0-only keys are counted and bounded (<= 0.5% of A0 rows).",
    "integer support for every arm: to_integer_support(q) = max(ceil(q - 0.5), 0) then a running maximum along the grid, on the 199-point grid (CRPS_TAUS), scored by crps_from_quantiles (the engine's crps_rows is the same formula).",
    "A1 = the engine arm 'ref_fixed' (v1 + v2 groups a-h,j + the leak-free rg_f3b8321a ridge columns; 95 pts columns), reg:quantileerror at 19 levels, ext_tails to the 199 grid, XGBoost 3.4.1 CPU hist, seed 20261008, n_jobs 4. A2 = same recipe on the 42 v1 columns (red team 'v1only').",
    "the engine file has been moved by the research/ restructure (two docstring path strings differ); the pin in the job (4952cc9c) is verified against the engine with those two strings reverted, then the pin is replaced by the current file's sha for the call (code is byte-identical otherwise).",
    "the frozen A1 frame still carries the postseason look-ahead in sch_days_to_next / sch_b2b_first (red team: effect <= 0.0005) and the 19:00 ET report-time proxy; both are descriptive-flagged, the proxy through the proxy-safe subset guard (games with a real tip at or after 19:00 ET).",
    "main lines: DraftKings, snapshot_kind t60, stat pts, one line per (game, player), x.5 lines only (status 0 = no pushes), from the ODDS_HISTORY pairs frame; only rows whose key is in the scored intersection count. P(over) = share of the 199 integer quantiles >= ceil(line); log loss clipped to [0.5/199, 1 - 0.5/199] (the lower-tail convention).",
    "bootstrap: game-clustered, 2000 resamples, seed 0, via paired_score_delta_ci / boot_p_value (the production helpers); BH over the two-member family {A1 2023, A1 2024}.",
    "minutes buckets for the missingness audit: <5 (including DNP / NULL), 5-20, >20 of tonight's stored minutes; rows = the whole frozen frame (2022-2024) and, separately, the 2023-2024 scored rows.",
    "starter / bench = starter10 >= 0.5 (pre-tip role, as add_slice_columns); first15 = team_game_no < 15; postseason = game_id not starting with '002' (playoffs and play-in); minutes >= 5 uses tonight's realised minutes (a post-hoc stratifier, never an input).",
    "planted tests rebuild v1 + v2 + two ridge months through the real builders with report tip_source='proxy19' (the setting of the frozen frame) and, separately, 'real'; tolerance 1e-12 relative for float-summation columns.",
]


# --------------------------------------------------------------------------- freeze


def doc_status(doc: Path) -> str:
    for line in doc.read_text().splitlines():
        m = re.match(r"^Status:\s*(\S+)", line)
        if m:
            return m.group(1).rstrip(".:;,")
    return "MISSING"


def require_frozen(doc: Path, expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(doc)
    if allow_draft:
        return sha
    if doc_status(doc) != "FROZEN":
        raise SystemExit(f"STOP: {doc} status is {doc_status(doc)!r}, not FROZEN")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(sha_file(p).encode())
    return h.hexdigest()


def frozen_inputs() -> dict[str, str]:
    """sha256 of every stored input the run depends on (pinned in the document)."""

    def unit(v: str) -> list[Path]:
        return sorted((RUN_DIR / "checkpoints/units" / v / STAT).glob("*.npz"))

    return {
        "components_pts.npz": sha_file(LT_DIR / "components_pts.npz"),
        "components_pi.npz": sha_file(LT_DIR / "components_pi.npz"),
        "candidates.json": sha_file(LOWER_TAIL_JSON),
        "ctxres_v2.parquet": sha_file(ROOT / "data/colab/ctxres_v2/ctxres_v2.parquet"),
        "ridge_v2.parquet": sha_file(ROOT / "data/colab/ridge_v2/ridge_v2.parquet"),
        "base_config.json": sha_file(ROOT / "data/colab/ridge_v2/base_config.json"),
        "units_hybrid_lf_pts": sha_files(unit("hybrid_lf")),
        "units_v1_prod_pts": sha_files(unit("v1_prod")),
        "units_fixsched_pts": sha_files(sorted((RT_UNITS / "fixsched" / STAT).glob("*.npz"))),
        "units_v1only_pts": sha_files(sorted((RT_UNITS / "v1only" / STAT).glob("*.npz"))),
        "dk_t60_pts.parquet": sha_file(DK_CACHE) if DK_CACHE.exists() else "MISSING",
    }


def parse_doc_inputs(doc: Path) -> dict[str, str]:
    """Lines ``- input <name>: <64 hex>`` of the document."""
    out: dict[str, str] = {}
    for line in doc.read_text().splitlines():
        m = re.match(r"^- input (\S+): ([0-9a-f]{64})\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# --------------------------------------------------------------------------- engine


def load_job() -> Any:
    """The leak-free job module with its engine pin re-pointed at the (moved) engine file."""
    text = ENGINE.read_text()
    normalised = text.replace(
        "research/eval/ridge_v2_eval.py", "nba/eval/ridge_v2_eval.py"
    ).replace("research.eval.ridge_v2_eval", "nba.eval.ridge_v2_eval")
    got = hashlib.sha256(normalised.encode()).hexdigest()
    if got != ENGINE_SHA_ORIGINAL:
        raise RuntimeError(f"engine code drifted: normalised sha256 {got} != {ENGINE_SHA_ORIGINAL}")
    spec = importlib.util.spec_from_file_location("xgb_pts_vs_lt_job", JOB)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("xgb_pts_vs_lt_job", mod)
    spec.loader.exec_module(mod)
    setattr(mod, "ENGINE_SHA256", hashlib.sha256(ENGINE.read_bytes()).hexdigest())  # noqa: B010
    return mod


def load_frame(njobs: int = 4) -> tuple[Any, Any, Any]:
    """(job, engine, ctx): the frozen frame loaded through the engine's own ``load`` (season <= 2024)."""
    job = load_job()
    staged = job.stage(CACHE_DIR / "engine")
    os.environ["NBA_PARQUET"] = str(staged)
    os.environ["NBA_NJOBS"] = str(njobs)
    job.setup("full")
    job.rs.load()
    ctx = job.C
    if int(ctx.season.max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 present: the frozen holdout must not be loaded")
    return job, job.rs, ctx


def frame_meta(ctx: Any) -> pl.DataFrame:
    """Test-row meta (2023-2024) of the frozen frame, in engine order."""
    sl = slice(ctx.t0i, None)
    d = ctx.df.iloc[sl]
    return pl.DataFrame(
        {
            "game_id": d["game_id"].to_numpy().astype(str),
            "player_id": d["player_id"].to_numpy().astype(np.int64),
            "season": d["season"].to_numpy().astype(np.int64),
            "team_id": d["team_id"].to_numpy().astype(np.int64),
            "y": ctx.y[STAT][sl],
            "team_game_no": d["team_game_no"].to_numpy().astype(float),
            "starter10": d["starter10"].to_numpy().astype(float),
            "n_out_rot": d["n_out_rot"].to_numpy().astype(float),
            "has_report": d["has_report"].to_numpy().astype(float),
            "min_gap": d["min_gap"].to_numpy().astype(float),
        }
    )


def load_units(ctx: Any, base: Path, variant: str) -> tuple[np.ndarray, np.ndarray]:
    """Stored 199-grid pts quantiles for the test rows: (q float32 [nt, 199], ok bool [nt])."""
    q = np.full((ctx.nt, 199), np.nan, np.float32)
    for b in ctx.blocks:
        if not b["ok"]:
            continue
        with np.load(base / variant / STAT / f"{b['fold']}.npz") as z:
            q[b["a"] - ctx.t0i : b["b"] - ctx.t0i] = z["q"]
    return q, ~np.isnan(q[:, 0])


# --------------------------------------------------------------------------- A0 (stored)


def load_a0() -> dict[str, Any]:
    """Production (``off``) and lower-tail mixture grids on integer support, from stored components."""
    comp = load_components(STAT, LT_DIR)
    pic = {k: v for k, v in np.load(LT_DIR / "components_pi.npz", allow_pickle=True).items()}
    if not (
        np.array_equal(comp["row_gid"], pic["row_gid"])
        and np.array_equal(comp["row_pid"], pic["row_pid"])
    ):
        raise RuntimeError("pi components are not row-aligned with the pts components")
    if int(comp["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in the lower-tail components")
    grids = {k: to_integer_support(arm_grid(k, comp, pic, STAT)) for k in ("off", "mixture")}
    meta = pl.DataFrame(
        {
            "game_id": comp["row_gid"].astype(str),
            "player_id": comp["row_pid"].astype(np.int64),
            "season": comp["row_season"].astype(np.int64),
            "y": comp["row_y"].astype(float),
            "minutes": comp["row_minutes"].astype(float),
            "starter_act": comp["row_starter_act"].astype(float),
        }
    )
    return {"meta": meta, "qi": {k: v.astype(np.int16) for k, v in grids.items()}}


def crps_int(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Integer-support CRPS per row of a (possibly continuous) 199-grid."""
    return crps_from_quantiles(to_integer_support(np.asarray(q, dtype=float)), y)


def check_a0_repro(a0: dict[str, Any]) -> dict[str, Any]:
    ref = json.loads(LOWER_TAIL_JSON.read_text())[STAT]
    rec: dict[str, Any] = {}
    ok = True
    for season in SEASONS:
        sm = (a0["meta"]["season"] == season).to_numpy()
        y = a0["meta"]["y"].to_numpy()[sm]
        for kind in ("off", "mixture"):
            got = float(crps_from_quantiles(a0["qi"][kind][sm].astype(float), y).mean())
            stored = float(ref[str(season)][kind]["crps_int"])
            n_ref = int(ref[str(season)]["off"]["n"])
            good = abs(got - stored) <= REPRO_A0_TOL and int(sm.sum()) == n_ref
            ok &= good
            rec[f"{season}_{kind}"] = {
                "got": got,
                "stored": stored,
                "delta": got - stored,
                "n": int(sm.sum()),
                "n_ref": n_ref,
                "ok": good,
            }
    return {"ok": bool(ok), "detail": rec}


# --------------------------------------------------------------------------- scoring helpers


def clustered(delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    c = paired_score_delta_ci(
        delta, np.zeros_like(delta), n_boot=n_boot, seed=BOOT_SEED, cluster_ids=gid
    )
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def ll_rows(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def thr_ll(qi: np.ndarray, y: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Log loss of P(Y >= n) from an integer 199-grid (per row; ``n`` per row)."""
    p = (qi >= n[:, None]).mean(axis=1)
    return ll_rows(p, y >= n)


def row_calibration(qi: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    pit = randomized_pit(qi.astype(float), y, 0)
    return {
        "bias": float((y - qi.mean(axis=1)).mean()),
        "cov80": float(((pit > 0.10) & (pit <= 0.90)).mean()),
        "guard": guard(pit),
    }


def to_arm_int(q: np.ndarray) -> np.ndarray:
    return to_integer_support(np.asarray(q, dtype=float))


def align_keys(a0_meta: pl.DataFrame, fr_meta: pl.DataFrame) -> dict[str, Any]:
    """Row index pairs of the key intersection per season, plus key-set accounting."""
    j = (
        fr_meta.with_row_index("i1")
        .join(a0_meta.with_row_index("i0").select([*KEYS, "i0"]), on=KEYS, how="inner")
        .sort("i1")
    )
    acct: dict[str, Any] = {}
    for s in SEASONS:
        a = a0_meta.filter(pl.col("season") == s)
        f = fr_meta.filter(pl.col("season") == s)
        a_only = a.join(f.select(KEYS), on=KEYS, how="anti")
        f_only = f.join(a.select(KEYS), on=KEYS, how="anti")
        acct[str(s)] = {
            "n_a0": a.height,
            "n_frame": f.height,
            "n_intersection": int(a.height - a_only.height),
            "a0_only": a_only.height,
            "frame_only": f_only.height,
            "a0_only_share": a_only.height / max(a.height, 1),
            "a0_only_games": sorted(a_only["game_id"].unique().to_list())[:20],
            "identical": a_only.height == 0 and f_only.height == 0,
        }
    return {"i0": j["i0"].to_numpy(), "i1": j["i1"].to_numpy(), "acct": acct}


# --------------------------------------------------------------------------- gate: checks 3, 4, 5, 6


def minutes_from_db(db_path: str) -> pl.DataFrame:
    import duckdb

    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SET memory_limit='1GB'")
        d = con.execute(
            "SELECT s.game_id, s.player_id::BIGINT AS player_id, s.minutes "
            "FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024"
        ).pl()
    finally:
        con.close()
    return d


def minutes_bucket(m: np.ndarray) -> np.ndarray:
    m = np.nan_to_num(m, nan=0.0)
    return np.where(m < 5, "<5", np.where(m <= 20, "5-20", ">20"))


def check_3_missingness(ctx: Any, db_path: str) -> dict[str, Any]:
    """NULL rate of every A1 pts feature by tonight's-minutes bucket; also a player-constant flag."""
    cols = list(ctx.base_cols[STAT]) + list(ctx.arms["ref_fixed"][STAT])
    df = ctx.df[["game_id", "player_id", "season", "game_date", *cols]].copy()
    mins = minutes_from_db(db_path).to_pandas()
    df = df.merge(mins, on=["game_id", "player_id"], how="left", validate="one_to_one")
    df["_b"] = minutes_bucket(df["minutes"].to_numpy(dtype=float))
    out: dict[str, Any] = {"n_rows": len(df), "n_columns": len(cols), "columns": {}}
    pid = df["player_id"].to_numpy()
    for scope, sel in (
        ("all", np.ones(len(df), bool)),
        ("scored", df["season"].to_numpy() >= 2023),
    ):
        sub = df[sel]
        counts = {b: int((sub["_b"] == b).sum()) for b in BUCKETS}
        out[f"{scope}_bucket_n"] = counts
        for c in cols:
            nul = sub[c].isna().to_numpy()
            rates = {b: float(nul[(sub["_b"] == b).to_numpy()].mean()) for b in BUCKETS}
            gap = (max(rates.values()) - min(rates.values())) * 100.0
            rec = out["columns"].setdefault(c, {})
            rec[f"{scope}_rates"] = rates
            rec[f"{scope}_gap_pp"] = gap
    # NULL status that is a function of the player alone (a static fact) or of the calendar month alone
    # (ridge warm-up months) cannot encode tonight's minutes: flag both, mechanically, never by name
    month = df["game_date"].astype(str).str.slice(0, 7).to_numpy()
    for c in cols:
        nul = df[c].isna().to_numpy().astype(float)
        rec = out["columns"][c]
        if nul.sum() == 0:
            rec["player_constant"] = True
            rec["month_constant"] = True
            continue
        s = pl.DataFrame({"p": pid, "n": nul}).group_by("p").agg(pl.col("n").mean().alias("m"))
        rec["player_constant"] = bool(((s["m"] == 0.0) | (s["m"] == 1.0)).all())
        s = pl.DataFrame({"p": month, "n": nul}).group_by("p").agg(pl.col("n").mean().alias("m"))
        rec["month_constant"] = bool(((s["m"] == 0.0) | (s["m"] == 1.0)).all())
    gaps = {c: v["all_gap_pp"] for c, v in out["columns"].items()}
    over = {c: g for c, g in gaps.items() if g > NULL_GAP_PP}
    over_nonstatic = {
        c: g
        for c, g in over.items()
        if not (out["columns"][c]["player_constant"] or out["columns"][c]["month_constant"])
    }
    gaps_sc = {c: v["scored_gap_pp"] for c, v in out["columns"].items()}
    out["max_gap_pp_all"] = max(gaps.values())
    out["max_gap_pp_scored"] = max(gaps_sc.values())
    out["columns_over_2pp_all"] = dict(sorted(over.items(), key=lambda kv: -kv[1]))
    out["columns_over_2pp_not_exempt"] = dict(sorted(over_nonstatic.items(), key=lambda kv: -kv[1]))
    out["columns_over_2pp_scored"] = dict(
        sorted(((c, g) for c, g in gaps_sc.items() if g > NULL_GAP_PP), key=lambda kv: -kv[1])
    )
    out["as_drafted_ok"] = len(over) == 0
    out["amended_ok"] = len(over_nonstatic) == 0
    return out


def _plant_build(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    poss: pl.DataFrame,
    demo: Any,
    elo: dict[str, float],
    report: Any,
) -> pl.DataFrame:
    from nba.props.context_residual import build_features, flagged_from_availability
    from research.features.opponent_ridge_v2 import MonthlyRidge, RidgeCfg, build_inputs
    from research.props.context_features_v2 import build_v2_features

    flagged, _ = flagged_from_availability(avail, games, report, max_season=MAX_SEASON)
    v1 = build_features(games, pgs, static, flagged, elo)
    full = build_v2_features(v1, games, pgs, static, avail, poss, report)
    inp = build_inputs(v1, pgs, flagged, demo)
    eng = MonthlyRidge(inp)
    jobs = [(STAT, RidgeCfg(alpha_p=50.0, alpha_o=50.0))]
    res = eng.run(jobs, months=[np.datetime64("2024-01", "M"), np.datetime64("2023-12", "M")])
    rg = pl.DataFrame(
        {
            "game_id": inp.game_id,
            "player_id": inp.player_id,
            **{f"rg_{s}_{k}": v for (s, _c), comps in res.items() for k, v in comps.items()},
        }
    )
    return full.join(rg, on=["game_id", "player_id"], how="left")


def check_4_planted(db_path: str, feature_cols: list[str], tip_source: str) -> dict[str, Any]:
    """Planted-future / planted-same-game through the real builders (ported from the red team's plant.py).

    Games dated >= D (D included) get every box line, minute count, starter flag, final score and
    possession count randomised, plus a planted post-tip 'Out' report for every player of every game on D.
    Rows dated < D test 'planted future'; rows on D test 'planted same game'."""
    import datetime as dt

    import yaml

    from nba.features.injury_report import ReportTriggerConfig
    from research.features.opponent_ridge_v2 import load_demo
    from research.props.context_features_v2 import load_inputs_v2

    day = dt.date(2024, 1, 15)
    games, pgs, static, avail, poss = load_inputs_v2(db_path)
    demo = load_demo(db_path)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 loaded")
    elo = {
        k: float(v)
        for k, v in yaml.safe_load((ROOT / "configs/mov_elo_tuned.yaml").read_text()).items()
    }
    report = ReportTriggerConfig(tip_source=tip_source)
    base = _plant_build(games, pgs, static, avail, poss, demo, elo, report)
    rng = np.random.default_rng(99)
    gd = games.select("game_id", pl.col("game_date").cast(pl.Date))
    fut = set(gd.filter(pl.col("game_date") >= day)["game_id"].to_list())
    on_d = set(gd.filter(pl.col("game_date") == day)["game_id"].to_list())
    isf = pl.col("game_id").is_in(sorted(fut))
    n = pgs.height

    def rnd(lo: int, hi: int) -> pl.Series:
        return pl.Series(rng.integers(lo, hi, n))

    pgs2 = pgs.with_columns(
        pl.when(isf & (pl.col("minutes") > 0))
        .then(pl.Series(rng.uniform(0.5, 48, n)).cast(pgs["minutes"].dtype))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        *[
            pl.when(isf & (pl.col("minutes") > 0))
            .then(rnd(0, h).cast(pgs[c].dtype))
            .otherwise(pl.col(c))
            .alias(c)
            for c, h in (
                ("pts", 45),
                ("reb", 20),
                ("ast", 15),
                ("fg3m", 9),
                ("oreb", 8),
                ("dreb", 14),
                ("fga", 30),
                ("tov", 8),
                ("fta", 15),
            )
        ],
        pl.when(isf)
        .then(pl.Series(rng.integers(0, 2, n).astype(bool)))
        .otherwise(pl.col("starter"))
        .alias("starter"),
    )
    ng = games.height
    games2 = games.with_columns(
        pl.when(isf)
        .then(pl.Series(rng.integers(70, 150, ng)).cast(games["home_pts"].dtype))
        .otherwise(pl.col("home_pts"))
        .alias("home_pts"),
        pl.when(isf)
        .then(pl.Series(rng.integers(70, 150, ng)).cast(games["away_pts"].dtype))
        .otherwise(pl.col("away_pts"))
        .alias("away_pts"),
    )
    npo = poss.height
    poss2 = poss.with_columns(
        *[
            pl.when(isf)
            .then(pl.Series(rng.integers(0, 80, npo)).cast(poss[c].dtype))
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("on_poss", "z_rim", "z_mid", "z_c3", "z_a3", "makes", "ast_makes")
        ]
    )
    plant = (
        pgs.filter(pl.col("game_id").is_in(sorted(on_d)))
        .select("game_id", "player_id")
        .join(gd, on="game_id")
        .with_columns(
            pl.lit("Out").alias("status"),
            (pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(hours=23, minutes=30)).alias(
                "as_of"
            ),
        )
    )
    src = avail["source"].drop_nulls().mode()[0]
    plant = plant.with_columns(pl.lit(src).alias("source")).select(
        [
            pl.col(c).cast(avail[c].dtype)
            for c in ("game_id", "player_id", "status", "as_of", "source")
        ]
    )
    avail2 = pl.concat([avail.select("game_id", "player_id", "status", "as_of", "source"), plant])
    pert = _plant_build(games2, pgs2, static, avail2, poss2, demo, elo, report)

    # compare only the A1 feature columns that the rebuilt frame also carries (rg names map rg_<hash>_p_pts -> rg_pts_p)
    def rebuilt_name(c: str) -> str:
        m = re.match(r"^rg_[0-9a-f]{8}_(\w+?)_(pts|reb|ast|fg3m)$", c)
        return f"rg_{m.group(2)}_{m.group(1)}" if m else c

    names = {c: rebuilt_name(c) for c in feature_cols}
    present = {c: r for c, r in names.items() if r in base.columns and r in pert.columns}
    missing = sorted(set(names) - set(present))
    dcol = pl.col("game_date").cast(pl.Date)
    res: dict[str, Any] = {
        "tip_source": tip_source,
        "n_columns_requested": len(feature_cols),
        "n_columns_compared": len(present),
        "columns_not_in_rebuilt_frame": missing,
    }
    for label, sel in (("future_rows_lt_D", dcol < day), ("same_game_rows_on_D", dcol == day)):
        b = base.filter(sel)
        p = pert.filter(sel)
        j = b.join(p, on=KEYS, how="full", suffix="__p", coalesce=True)
        changed: dict[str, Any] = {}
        for c, r in present.items():
            x = j[r].cast(pl.Float64).to_numpy()
            y = j[r + "__p"].cast(pl.Float64).to_numpy()
            same = (x == y) | (np.isnan(x) & np.isnan(y))
            if not same.all():
                fin = np.isfinite(x) & np.isfinite(y) & ~same
                rel = (
                    float(np.max(np.abs(x[fin] - y[fin]) / np.maximum(np.abs(x[fin]), 1e-9)))
                    if fin.any()
                    else float("inf")
                )
                bad = int((~same).sum())
                changed[c] = {"n_rows": bad, "max_rel_diff": rel}
        material = {c: v for c, v in changed.items() if v["max_rel_diff"] > 1e-12}
        res[label] = {
            "n_rows": j.height,
            "changed": changed,
            "changed_beyond_1e-12": material,
            "ok": not material,
        }
    # controls: label-derived features must move on rows after D (the test is not inert)
    after = base.filter(dcol > day).join(
        pert.filter(dcol > day), on=KEYS, how="inner", suffix="__p"
    )
    ctrl: dict[str, float] = {}
    for c in ("m10_pts", "pr_usage", "n48_pts", "opp_pos_pts", "game_total_feat"):
        if c in after.columns and c + "__p" in after.columns:
            x = after[c].cast(pl.Float64).to_numpy()
            y = after[c + "__p"].cast(pl.Float64).to_numpy()
            ctrl[c] = float(np.mean(~np.isclose(x, y, equal_nan=True)))
    res["control_share_changed_after_D"] = ctrl
    res["inert"] = not any(v > 0.5 for v in ctrl.values())
    res["ok"] = bool(
        res["future_rows_lt_D"]["ok"] and res["same_game_rows_on_D"]["ok"] and not res["inert"]
    )
    return res


def tip_exposure(meta: pl.DataFrame) -> dict[str, Any]:
    """Share of scored rows whose game tips before the 19:00 ET proxy (report-time exposure)."""
    tips = build_game_tipoff(max_season=MAX_SEASON)
    t = tips.select(
        "game_id",
        (pl.col("tip_et").dt.hour() + pl.col("tip_et").dt.minute() / 60.0).alias("tip_hour"),
    )
    j = meta.join(t, on="game_id", how="left")
    out: dict[str, Any] = {}
    for s in SEASONS:
        d = j.filter(pl.col("season") == s)
        h = d["tip_hour"]
        out[str(s)] = {
            "n": d.height,
            "tip_missing": int(h.is_null().sum()),
            "tip_lt_19": int((h < PROXY_HOUR_ET).sum()),
            "tip_ge_19": int((h >= PROXY_HOUR_ET).sum()),
            "share_lt_19": float((h < PROXY_HOUR_ET).sum() / max(d.height, 1)),
        }
    return out


def prepare_dk(pairs_dir: Path, refresh: bool = False) -> dict[str, Any]:
    """Cache the DK t60 pts main-line subset of the ODDS_HISTORY pairs frames (season <= 2024).
    Built once from ``pairs_dir``; later calls reuse (and report) the cache unless ``refresh``."""
    if DK_CACHE.exists() and not refresh:
        d = pl.read_parquet(DK_CACHE)
        return {
            "source_sha256": "reused cache",
            "rows": {str(s): int(d.filter(pl.col("season") == s).height) for s in SEASONS},
            "cache_sha256": sha_file(DK_CACHE),
        }
    parts = []
    srcs: dict[str, str] = {}
    for s in SEASONS:
        p = pairs_dir / f"pairs_{s}.parquet"
        srcs[p.name] = sha_file(p)
        d = pl.read_parquet(
            p, columns=[*KEYS, "stat", "book", "snapshot_kind", "line", "season", "_dnp", "status"]
        )
        parts.append(
            d.filter(
                (pl.col("stat") == STAT)
                & (pl.col("book") == "draftkings")
                & (pl.col("snapshot_kind") == "t60")
                & (pl.col("status") == 0)
                & ~pl.col("_dnp")
            ).select(KEYS[0], KEYS[1], "line", "season")
        )
    dk = pl.concat(parts).with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("season").cast(pl.Int64)
    )
    if int(dk["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season > 2024 in pairs")
    if dk.group_by(KEYS).len().filter(pl.col("len") > 1).height:
        raise RuntimeError("more than one DK t60 line per (game, player)")
    if not ((dk["line"] % 1.0) == 0.5).all():
        raise RuntimeError("non x.5 line in the DK subset")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dk.write_parquet(DK_CACHE)
    return {
        "source_sha256": srcs,
        "rows": {str(s): int(dk.filter(pl.col("season") == s).height) for s in SEASONS},
        "cache_sha256": sha_file(DK_CACHE),
    }


def reproduce_stored_a1(ctx: Any) -> dict[str, Any]:
    """Check 2: stored hybrid_lf / v1_prod pts checkpoints, both on integer support (fair), vs the red team figure.
    Also the stored descriptive arms (fixsched, v1only) vs v1_prod. No A0 involved."""
    hq, hok = load_units(ctx, RUN_DIR / "checkpoints/units", "hybrid_lf")
    vq, vok = load_units(ctx, RUN_DIR / "checkpoints/units", "v1_prod")
    if not (hok.all() and vok.all()):
        raise RuntimeError("stored checkpoints incomplete")
    y = ctx.y[STAT][ctx.t0i :]
    gid = ctx.gid[ctx.t0i :]
    season = ctx.test_season
    ch, cv = crps_int(hq, y), crps_int(vq, y)
    ref = {2023: REF_FAIR_2023, 2024: REF_FAIR_2024}
    rec: dict[str, Any] = {}
    ok = True
    for s in SEASONS:
        m = season == s
        c = clustered((ch - cv)[m], gid[m])
        good = (
            abs(c["point"] - ref[s]) <= REPRO_A1_TOL
            and int(m.sum()) == {2023: 27619, 2024: 27583}[s]
        )
        ok &= good
        rec[str(s)] = {
            "delta_vs_v1_prod": c,
            "red_team_reference": ref[s],
            "abs_diff": abs(c["point"] - ref[s]),
            "n": int(m.sum()),
            "ok": good,
            "mde_80pct_from_ci": 2.8 * (c["hi"] - c["lo"]) / (2 * 1.96),
            "se_from_ci": (c["hi"] - c["lo"]) / (2 * 1.96),
        }
    desc: dict[str, Any] = {}
    for name, base in (("fixsched", RT_UNITS), ("v1only", RT_UNITS)):
        q, okk = load_units(ctx, base, name)
        cq = crps_int(q, y)
        for s in SEASONS:
            m = season == s
            desc[f"{name}_vs_v1_prod_{s}"] = clustered((cq - cv)[m], gid[m])
            desc[f"{name}_vs_hybrid_lf_{s}"] = clustered((cq - ch)[m], gid[m])
    # integer-support map equivalence (the red team used np.ceil(q - 0.5) without the clip and running max)
    rt = np.ceil(hq.astype(float) - 0.5)
    equiv = float(np.max(np.abs(rt - to_integer_support(hq.astype(float)))))
    return {
        "ok": bool(ok),
        "seasons": rec,
        "descriptive_stored": desc,
        "integer_map_max_abs_diff_vs_ceil_m05": equiv,
    }


def run_gate(
    db_path: str,
    doc: Path,
    sha: str,
    allow_draft: bool,
    pairs_dir: Path,
    out_dir: Path = OUT_DIR,
    refresh_dk: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    tests: list[dict[str, Any]] = []

    def chk(name: str, value: Any, threshold: str, ok: Any, counts: bool = True) -> None:
        """``counts=False``: the literal pre-amendment wording, recorded for the audit trail only."""
        tests.append(
            {
                "test": name,
                "value": value,
                "threshold": threshold,
                "ok": bool(ok),
                "counts": counts,
            }
        )

    # data movement guard: no season-2025 anywhere
    a0 = load_a0()
    print(f"[{time.time() - t0:.0f}s] A0 grids built (stored components)", flush=True)
    r1 = check_a0_repro(a0)
    chk(
        "1 A0 reproduces stored lower-tail mixture and production(off) integer CRPS and n to 1e-6, 2 seasons",
        int(r1["ok"]),
        "== 1",
        r1["ok"],
    )
    job, rs, ctx = load_frame(4)
    print(f"[{time.time() - t0:.0f}s] frame loaded: {len(ctx.df)} rows, test {ctx.nt}", flush=True)
    r2 = reproduce_stored_a1(ctx)
    chk(
        "2 A1 (stored hybrid_lf pts, integer support both arms) reproduces the red team fair delta vs v1_prod to 1e-4, 2 seasons",
        int(r2["ok"]),
        "== 1",
        r2["ok"],
    )
    fm = frame_meta(ctx)
    keys = align_keys(a0["meta"], fm)
    acct = keys["acct"]
    worst = max(a["a0_only_share"] for a in acct.values())
    frame_only = sum(a["frame_only"] for a in acct.values())
    literal = all(a["identical"] for a in acct.values())
    chk(
        "5 literal: identical row keys A0 vs frozen frame (arms A1/A2 share one frame by construction)",
        int(literal),
        "== 1 (as drafted; superseded by the amendment below)",
        literal,
        counts=False,
    )
    chk(
        "5 amended: frame keys subset of A0 keys and A0-only share <= 0.5% per season (scored on the intersection)",
        worst,
        f"frame_only == 0 and share <= {KEY_DROP_MAX}",
        frame_only == 0 and worst <= KEY_DROP_MAX,
    )
    labels_equal = bool(
        np.array_equal(a0["meta"]["y"].to_numpy()[keys["i0"]], fm["y"].to_numpy()[keys["i1"]])
    )
    chk(
        "5 labels agree between A0 and the frozen frame on every shared key",
        int(labels_equal),
        "== 1",
        labels_equal,
    )
    feature_cols = list(ctx.base_cols[STAT]) + list(ctx.arms["ref_fixed"][STAT])
    r3 = check_3_missingness(ctx, db_path)
    chk(
        "3 literal: every A1 pts feature NULL-rate gap across minutes buckets (<5, 5-20, >20) <= 2 pp (whole frame)",
        r3["max_gap_pp_all"],
        f"<= {NULL_GAP_PP} (as drafted; superseded by the amendment below)",
        r3["as_drafted_ok"],
        counts=False,
    )
    chk(
        "3 amended: same, exempting columns whose NULL status is constant within player or within calendar month (computed, not named)",
        len(r3["columns_over_2pp_not_exempt"]),
        "== 0 columns",
        r3["amended_ok"],
    )
    print(f"[{time.time() - t0:.0f}s] missingness audit done", flush=True)
    r4: dict[str, Any] = {}
    for ts in ("proxy19", "real"):
        r4[ts] = check_4_planted(db_path, feature_cols, ts)
        print(f"[{time.time() - t0:.0f}s] planted tests ({ts}) done", flush=True)
    chk(
        "4 planted-future and planted-same-game, builders at report tip_source=proxy19 (the frozen frame's setting), not inert",
        int(r4["proxy19"]["ok"]),
        "== 1",
        r4["proxy19"]["ok"],
    )
    chk(
        "4 same, builders at report tip_source=real",
        int(r4["real"]["ok"]),
        "== 1",
        r4["real"]["ok"],
    )
    exp = tip_exposure(fm.join(a0["meta"].select([*KEYS]), on=KEYS, how="inner"))
    dk = prepare_dk(pairs_dir, refresh_dk)
    dk_df = pl.read_parquet(DK_CACHE)
    n_dk: dict[str, int] = {}
    inter = fm[keys["i1"]].with_columns(pl.Series("i0", keys["i0"]))
    for s in SEASONS:
        k = inter.filter(pl.col("season") == s).join(
            dk_df.filter(pl.col("season") == s).select(KEYS), on=KEYS, how="inner"
        )
        n_dk[str(s)] = k.height
    chk(
        "6 DK t60 pts main-line rows inside the scored intersection (each season)",
        min(n_dk.values()),
        ">= 5000",
        min(n_dk.values()) >= 5000,
    )
    se24 = r2["seasons"]["2024"]["se_from_ci"]
    mde = 2.8 * se24
    res: dict[str, Any] = {
        "status": "GATE_PASSED"
        if all(t["ok"] for t in tests if t["counts"])
        else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "doc": str(doc),
        "sha256": sha,
        "seeds": {"bootstrap": BOOT_SEED, "pit": 0, "engine": 20261008},
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests if t["counts"]),
        "as_drafted_all_ok": all(t["ok"] for t in tests),
        "a0_repro": r1,
        "a1_repro": r2,
        "key_accounting": acct,
        "missingness": r3,
        "planted": r4,
        "tip_exposure": exp,
        "dk": {**dk, "scored_rows": n_dk},
        "power": {
            "se_of_paired_delta_from_A1_vs_v1_prod_2024": se24,
            "mde_80pct_two_sided_5pct": mde,
            "floor": FLOOR,
            "note": "proxy: the A1 - A0 SE is not computed at the gate (no A1 - A0 contrast before the freeze); the A1 - v1_prod paired SE is used",
        },
        "inputs_sha256": frozen_inputs(),
        "unstated_details": UNSTATED,
        "max_season_loaded": int(ctx.season.max()),
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    gate_path = out_dir / "gate.json"
    if gate_path.exists() and not allow_draft:
        old = json.loads(gate_path.read_text())
        if old.get("draft"):
            gate_path.rename(out_dir / "gate_draft.json")
    gate_path.write_text(json.dumps(res, indent=1, default=_jd, sort_keys=True))
    return res


def _jd(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


# --------------------------------------------------------------------------- run: refit A1 / A2


def refit_arm(
    job: Any,
    rs: Any,
    ctx: Any,
    arm: str,
    out_dir: Path,
    max_blocks: int | None = None,
    only: list[int] | None = None,
) -> dict[str, float]:
    """Refit the pts arm block by block (resumable npz checkpoints). ``A1`` = engine arm ref_fixed,
    ``A2`` = the same recipe on the v1 feature columns only. Returns seconds per block."""
    params = dict(ctx.base_cfg["params"], n_jobs=ctx.njobs)
    v1 = [c for c in ctx.spec["v1_features"][STAT]]
    if any(
        c.startswith("ridge_")
        for c in v1 + list(ctx.base_cols[STAT]) + list(ctx.arms["ref_fixed"][STAT])
    ):
        raise RuntimeError("leaky ridge_* column in a feature list")
    if arm == "A2":
        rs.arm_cols = lambda a, st: np.array([ctx.ci[c] for c in v1])
    done: dict[str, float] = {}
    todo = [b for b in ctx.blocks if b["ok"]]
    if only is not None:
        todo = [todo[i] for i in only]
    elif max_blocks is not None:
        todo = todo[:max_blocks]
    for i, blk in enumerate(todo):
        f = out_dir / arm / STAT / f"{blk['fold']}.npz"
        if f.exists():
            continue
        t = time.monotonic()
        pred = rs.stat_pred(blk, "ref_fixed", STAT, params, ctx.B["rounds"])
        f.parent.mkdir(parents=True, exist_ok=True)
        np.savez(f, q=np.asarray(pred["q"], np.float32))
        done[str(blk["fold"])] = time.monotonic() - t
        print(
            f"  {arm}/{STAT} block {blk['fold']} ({i + 1}/{len(todo)}) {done[str(blk['fold'])]:.1f}s",
            flush=True,
        )
    return done


def load_arm_units(ctx: Any, out_dir: Path, arm: str) -> np.ndarray:
    q, ok = load_units(ctx, out_dir, arm)
    if not ok.all():
        raise RuntimeError(f"{arm}: refit units incomplete")
    return q


# --------------------------------------------------------------------------- scoring (pure functions)


def build_masks(meta: pl.DataFrame) -> dict[str, np.ndarray]:
    gt = meta["game_id"].str.slice(0, 3)
    return {
        "all": np.ones(meta.height, dtype=bool),
        "regular": (gt == "002").to_numpy(),
        "postseason": (gt != "002").to_numpy(),
        "minutes>=5": (meta["minutes"] >= 5).to_numpy(),
        "minutes<5": (meta["minutes"] < 5).to_numpy(),
        "starter": (meta["starter10"] >= 0.5).to_numpy(),
        "bench": (meta["starter10"] < 0.5).to_numpy(),
        "first15": (meta["team_game_no"] < 15).to_numpy(),
        "rest_of_season": (meta["team_game_no"] >= 15).to_numpy(),
        "without_PHI": (meta["team_id"] != PHI).to_numpy(),
        "starter_actual": (meta["starter_act"] > 0.5).to_numpy(),
        "tip>=19ET(proxy-safe)": (meta["tip_hour"] >= PROXY_HOUR_ET).fill_null(False).to_numpy(),
        "tip<19ET(proxy-exposed)": (meta["tip_hour"] < PROXY_HOUR_ET).fill_null(False).to_numpy(),
    }


GUARD_SLICES = ("regular", "postseason", "starter", "bench", "first15")


def team_shares(delta: np.ndarray, team: np.ndarray) -> dict[str, Any]:
    """Share of the summed gain (-delta) carried by each team; largest first."""
    gain = -delta
    total = float(gain.sum())
    out = {}
    for t in np.unique(team):
        out[int(t)] = float(gain[team == t].sum() / total) if total != 0 else float("nan")
    top = max(out.items(), key=lambda kv: kv[1])
    return {
        "total_gain": total,
        "top_team": top[0],
        "top_share": top[1],
        "shares": dict(sorted(out.items(), key=lambda kv: -kv[1])[:5]),
    }


def score_all(
    meta: dict[int, pl.DataFrame],
    qi: dict[str, dict[int, np.ndarray]],
    dk: dict[int, tuple[np.ndarray, np.ndarray]],
) -> dict[str, Any]:
    """Everything the rule reads, as a pure function of aligned arrays.

    ``meta[season]``: scored-intersection rows (columns game_id, y, minutes, starter10, starter_act,
    team_game_no, team_id, tip_hour); ``qi[arm][season]``: integer 199-grids aligned to those rows
    (arms A0, A1, A2, and optional descriptive arms); ``dk[season]``: (row index into meta, line)."""
    out: dict[str, Any] = {"seasons": {}}
    pvals: dict[int, float] = {}
    for s in SEASONS:
        m = meta[s]
        y = m["y"].to_numpy()
        gid = m["game_id"].to_numpy()
        masks = build_masks(m)
        sc = {a: crps_from_quantiles(q[s].astype(float), y) for a, q in qi.items()}
        rec: dict[str, Any] = {
            "n": m.height,
            "n_games": int(len(np.unique(gid))),
            "crps": {a: float(v.mean()) for a, v in sc.items()},
        }
        d1 = sc["A1"] - sc["A0"]
        rec["primary"] = {**clustered(d1, gid), "p": boot_p_value(d1, gid, N_BOOT, BOOT_SEED)}
        pvals[s] = rec["primary"]["p"]
        for pair in (("A2", "A0"), ("A1", "A2")):
            if pair[0] in sc and pair[1] in sc:
                rec[f"{pair[0]}-{pair[1]}"] = clustered(sc[pair[0]] - sc[pair[1]], gid)
        for extra in [a for a in sc if a not in ("A0", "A1", "A2")]:
            rec[f"{extra}-A0"] = clustered(sc[extra] - sc["A0"], gid)
        sl: dict[str, Any] = {}
        for name, mk in masks.items():
            n = int(mk.sum())
            r: dict[str, Any] = {"n": n}
            if n >= MIN_SLICE_N or name == "all":
                r["A1-A0"] = clustered(d1[mk], gid[mk])
                if "A2" in sc:
                    r["A2-A0"] = clustered((sc["A2"] - sc["A0"])[mk], gid[mk])
            sl[name] = r
        rec["slices"] = sl
        rec["calibration"] = {
            a: row_calibration(q[s], y) for a, q in qi.items() if a in ("A0", "A1", "A2")
        }
        rec["team_shares"] = team_shares(d1, m["team_id"].to_numpy())
        rec["drop_top_team"] = clustered(
            d1[m["team_id"].to_numpy() != rec["team_shares"]["top_team"]],
            gid[m["team_id"].to_numpy() != rec["team_shares"]["top_team"]],
        )
        # threshold log loss: DK T-60 main lines (rule) and fixed thresholds (descriptive)
        idx, line = dk[s]
        if len(idx):
            n_thr = np.ceil(line)
            yy, gg = y[idx], gid[idx]
            ll = {
                a: thr_ll(q[s][idx].astype(float), yy, n_thr)
                for a, q in qi.items()
                if a in ("A0", "A1", "A2")
            }
            rec["tll_dk"] = {
                "n": int(len(idx)),
                "A0": float(ll["A0"].mean()),
                "A1": float(ll["A1"].mean()),
                "A1-A0": clustered(ll["A1"] - ll["A0"], gg),
            }
            if "A2" in ll:
                rec["tll_dk"]["A2-A0"] = clustered(ll["A2"] - ll["A0"], gg)
        thr: dict[str, Any] = {}
        for t in THRESHOLDS:
            n_arr = np.full(len(y), float(t))
            l0, l1 = (
                thr_ll(qi["A0"][s].astype(float), y, n_arr),
                thr_ll(qi["A1"][s].astype(float), y, n_arr),
            )
            thr[str(t)] = clustered(l1 - l0, gid)
        rec["tll_fixed_thresholds_A1-A0"] = thr
        out["seasons"][str(s)] = rec
    adj = bh_adjust(np.array([pvals[2023], pvals[2024]]))
    out["bh"] = {"2023": float(adj[0]), "2024": float(adj[1])}
    return out


def _slice_point(season_res: dict[str, Any], name: str, bound: float) -> bool:
    """True iff the slice has a scored A1 - A0 delta (n >= 300) with point <= ``bound``; a slice
    that cannot be scored fails the condition (it cannot be verified)."""
    v = season_res["slices"].get(name, {})
    return bool("A1-A0" in v and v["A1-A0"]["point"] <= bound)


def evaluate_rule(res: dict[str, Any], gate_ok: bool, refit_ok: bool) -> dict[str, Any]:
    s23, s24 = res["seasons"]["2023"], res["seasons"]["2024"]

    def primary(r: dict[str, Any], bh: float) -> dict[str, bool]:
        p = r["primary"]
        return {"floor": p["point"] <= FLOOR, "ci_hi<0": p["hi"] < 0.0, "bh<0.05": bh < 0.05}

    p1 = primary(s23, res["bh"]["2023"])
    p2 = primary(s24, res["bh"]["2024"])
    p3 = {
        str(s): bool(res["seasons"][str(s)]["tll_dk"]["A1-A0"]["point"] <= TLL_MARGIN)
        for s in SEASONS
    }
    cal = s24["calibration"]["A1"]
    p4 = {
        "abs_bias<=0.5": abs(cal["bias"]) <= MAX_BIAS,
        "cov80_in_[.75,.85]": COV_LO <= cal["cov80"] <= COV_HI,
        "pit_guard": bool(cal["guard"]["ok"]),
    }
    bad = {
        k: v["A1-A0"]["point"]
        for k, v in s24["slices"].items()
        if k in GUARD_SLICES and v["n"] >= MIN_SLICE_N and v["A1-A0"]["point"] > SLICE_WORSE
    }
    p5 = {"no_guard_slice_worse_than_+0.01": not bad}
    p6 = {str(s): _slice_point(res["seasons"][str(s)], "minutes>=5", 0.0) for s in SEASONS}
    p7 = {"top_team_share<=0.25": bool(s24["team_shares"]["top_share"] <= TEAM_SHARE_MAX)}
    p8 = {"proxy_safe_delta<=floor": _slice_point(s24, "tip>=19ET(proxy-safe)", FLOOR)}
    conds = {
        "P1_2023": all(p1.values()),
        "P2_2024": all(p2.values()),
        "P3_tll": all(p3.values()),
        "P4_calibration": all(p4.values()),
        "P5_slices": all(p5.values()),
        "P6_minutes>=5_no_harm": all(p6.values()),
        "P7_concentration": all(p7.values()),
        "P8_proxy_safe": all(p8.values()),
    }
    passed = bool(all(conds.values()) and gate_ok and refit_ok)
    return {
        "P1_2023": p1,
        "P2_2024": p2,
        "P3_tll_by_season": p3,
        "P4": p4,
        "P5_slices_worse": bad,
        "P6_by_season": p6,
        "P7": p7,
        "P8": p8,
        "conditions": conds,
        "gate_ok": gate_ok,
        "refit_matches_stored": refit_ok,
        "pass": passed,
        "closed_no_pass_2023": not conds["P1_2023"],
        "verdict": "SHADOW_CANDIDATE" if passed else "CLOSED",
    }


def assemble_and_score(
    ctx: Any,
    a0: dict[str, Any],
    q1: np.ndarray,
    q2: np.ndarray,
    stored1: np.ndarray,
    fixs: np.ndarray,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Align A0 and the frame arms on the key intersection and score (pure given its inputs)."""
    fm = frame_meta(ctx)
    keys = align_keys(a0["meta"], fm)
    tips = build_game_tipoff(max_season=MAX_SEASON).select(
        "game_id",
        (pl.col("tip_et").dt.hour() + pl.col("tip_et").dt.minute() / 60.0).alias("tip_hour"),
    )
    dk_df = pl.read_parquet(DK_CACHE)
    meta: dict[int, pl.DataFrame] = {}
    qi: dict[str, dict[int, np.ndarray]] = {
        a: {} for a in ("A0", "A1", "A2", "A1_stored", "A1_fixsched")
    }
    dk: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    i0, i1 = keys["i0"], keys["i1"]
    seas = fm["season"].to_numpy()[i1]
    for s in SEASONS:
        sel = seas == s
        a_idx, f_idx = i0[sel], i1[sel]
        m = fm[f_idx].join(tips, on="game_id", how="left")
        am = a0["meta"][a_idx]
        if not np.array_equal(am["y"].to_numpy(), m["y"].to_numpy()):
            raise RuntimeError("labels differ between A0 and the frozen frame on shared keys")
        m = m.with_columns(am["minutes"].alias("minutes"), am["starter_act"].alias("starter_act"))
        meta[s] = m
        qi["A0"][s] = a0["qi"]["mixture"][a_idx]
        qi["A1"][s] = to_arm_int(q1[f_idx])
        qi["A2"][s] = to_arm_int(q2[f_idx])
        qi["A1_stored"][s] = to_arm_int(stored1[f_idx])
        qi["A1_fixsched"][s] = to_arm_int(fixs[f_idx])
        j = m.with_row_index("r").join(
            dk_df.filter(pl.col("season") == s).select([*KEYS, "line"]), on=KEYS, how="inner"
        )
        dk[s] = (j["r"].to_numpy(), j["line"].to_numpy())
    return score_all(meta, qi, dk), keys


def run_arms(doc: Path, sha: str, pairs_dir: Path, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    gate = json.loads((out_dir / "gate.json").read_text())
    if gate.get("draft") or not gate.get("gate_ok") or gate.get("sha256") != sha:
        raise RuntimeError(
            "no passing non-draft gate recorded for this frozen sha: no arm may be fitted"
        )
    pinned = parse_doc_inputs(doc)
    now = frozen_inputs()
    drift = {k: (pinned.get(k), now[k]) for k in now if pinned.get(k) != now[k]}
    if drift:
        raise RuntimeError(f"stored inputs differ from the pins in the document: {drift}")
    t0 = time.time()
    job, rs, ctx = load_frame(4)
    a0 = load_a0()
    if not check_a0_repro(a0)["ok"]:
        raise RuntimeError("A0 does not reproduce the stored lower-tail OOF: stop")
    refit_dir = CACHE_DIR / "units"
    timings: dict[str, Any] = {}
    for arm in ("A1", "A2"):
        timings[arm] = refit_arm(job, rs, ctx, arm, refit_dir)
        print(f"[{time.time() - t0:.0f}s] {arm} refit ready", flush=True)
    q1, q2 = load_arm_units(ctx, refit_dir, "A1"), load_arm_units(ctx, refit_dir, "A2")
    stored1, _ = load_units(ctx, RUN_DIR / "checkpoints/units", "hybrid_lf")
    stored2, _ = load_units(ctx, RT_UNITS, "v1only")
    fixs, _ = load_units(ctx, RT_UNITS, "fixsched")
    d1 = float(np.max(np.abs(q1 - stored1)))
    d2 = float(np.max(np.abs(q2 - stored2)))
    refit_ok = d1 <= 1e-3 and d2 <= 1e-3
    print(f"refit vs stored: A1 max|dq| {d1:.2e}, A2 max|dq| {d2:.2e}", flush=True)
    res, keys = assemble_and_score(ctx, a0, q1, q2, stored1, fixs)
    rule = evaluate_rule(res, True, refit_ok)
    full = {
        **res,
        "rule": rule,
        "key_accounting": keys["acct"],
        "refit_vs_stored_max_abs_dq": {"A1": d1, "A2": d2},
        "refit_seconds": {a: float(sum(t.values())) for a, t in timings.items()},
        "sha256": sha,
        "seeds": {"bootstrap": BOOT_SEED, "engine": 20261008},
        "n_boot": N_BOOT,
        "run_seconds": time.time() - t0,
    }
    (out_dir / "results.json").write_text(json.dumps(full, indent=1, default=_jd))
    return full


def run_smoke(doc: Path, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Refit two pts blocks (earliest, latest) for A1 and A2, compare to the stored units, time them,
    and extrapolate the full-run wall time.  Scores nothing."""
    t0 = time.time()
    job, rs, ctx = load_frame(4)
    ok_blocks = [b for b in ctx.blocks if b["ok"]]
    only = [0, len(ok_blocks) - 1]
    smoke_dir = CACHE_DIR / "smoke_units"
    rec: dict[str, Any] = {"blocks": [str(ok_blocks[i]["fold"]) for i in only], "arms": {}}
    for arm in ("A1", "A2"):
        times = refit_arm(job, rs, ctx, arm, smoke_dir, only=only)
        stored_base, name = (
            (RUN_DIR / "checkpoints/units", "hybrid_lf") if arm == "A1" else (RT_UNITS, "v1only")
        )
        diffs = {}
        for i in only:
            b = ok_blocks[i]
            with (
                np.load(smoke_dir / arm / STAT / f"{b['fold']}.npz") as z,
                np.load(stored_base / name / STAT / f"{b['fold']}.npz") as w,
            ):
                diffs[str(b["fold"])] = float(np.max(np.abs(z["q"] - w["q"])))
        t_first, t_last = (times.get(str(ok_blocks[i]["fold"])) for i in only)
        est = (
            None if t_first is None or t_last is None else 0.5 * (t_first + t_last) * len(ok_blocks)
        )
        rec["arms"][arm] = {
            "seconds": times,
            "max_abs_dq_vs_stored": diffs,
            "estimated_full_seconds": est,
        }
    rec["total_estimated_minutes"] = (
        sum(a["estimated_full_seconds"] or 0 for a in rec["arms"].values()) / 60.0
    )
    rec["smoke_seconds"] = time.time() - t0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke.json").write_text(json.dumps(rec, indent=1, default=_jd))
    return rec


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _ci_s(c: dict[str, float]) -> str:
    return f"{_f(c['point'])} [{_f(c['lo'])}, {_f(c['hi'])}]"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    L = [f"# XGB_PTS_VS_LT results (sha256 `{r['sha256']}`)", ""]
    L += [
        "| season | n | games | CRPS A0 | CRPS A1 | CRPS A2 | A1 - A0 [95% CI] | p | BH |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in ("2023", "2024"):
        x = r["seasons"][s]
        L.append(
            f"| {s} | {x['n']} | {x['n_games']} | {_f(x['crps']['A0'], 4, False)} | {_f(x['crps']['A1'], 4, False)} | {_f(x['crps']['A2'], 4, False)} | {_ci_s(x['primary'])} | {x['primary']['p']:.4f} | {r['bh'][s]:.4f} |"
        )
    L += [
        "",
        "## Secondary and descriptive contrasts",
        "",
        "| season | contrast | delta [95% CI] |",
        "|---|---|---|",
    ]
    for s in ("2023", "2024"):
        x = r["seasons"][s]
        for k in ("A2-A0", "A1-A2", "A1_stored-A0", "A1_fixsched-A0"):
            if k in x:
                L.append(f"| {s} | {k} | {_ci_s(x[k])} |")
        L.append(
            f"| {s} | threshold log loss A1 - A0, DK T-60 main lines (n {x['tll_dk']['n']}) | {_ci_s(x['tll_dk']['A1-A0'])} |"
        )
        if "A2-A0" in x["tll_dk"]:
            L.append(
                f"| {s} | threshold log loss A2 - A0, DK T-60 main lines | {_ci_s(x['tll_dk']['A2-A0'])} |"
            )
        for t, c in x["tll_fixed_thresholds_A1-A0"].items():
            L.append(f"| {s} | threshold log loss A1 - A0 at pts >= {t} (all rows) | {_ci_s(c)} |")
        L.append(
            f"| {s} | drop the top-share team (team {x['team_shares']['top_team']}, share {x['team_shares']['top_share']:.3f}) | {_ci_s(x['drop_top_team'])} |"
        )
    L += [
        "",
        "## Slices (A1 - A0)",
        "",
        "| season | slice | n | delta [95% CI] |",
        "|---|---|---|---|",
    ]
    for s in ("2023", "2024"):
        for name, v in r["seasons"][s]["slices"].items():
            L.append(
                f"| {s} | {name} | {v['n']} | {_ci_s(v['A1-A0']) if 'A1-A0' in v else 'n < 300'} |"
            )
    L += [
        "",
        "## Calibration (integer support)",
        "",
        "| season | arm | bias | cov80 | PIT guard ok |",
        "|---|---|---|---|---|",
    ]
    for s in ("2023", "2024"):
        for a, v in r["seasons"][s]["calibration"].items():
            L.append(f"| {s} | {a} | {_f(v['bias'], 3)} | {v['cov80']:.3f} | {v['guard']['ok']} |")
    L += ["", "## Rule", "", "```", json.dumps(r["rule"], indent=1), "```", ""]
    return "\n".join(L)


def write_ledger(out_dir: Path = OUT_DIR) -> Path:
    r = json.loads((out_dir / "results.json").read_text())
    body = ["| id | test | result |", "|---|---|---|"]
    for s in ("2023", "2024"):
        x = r["seasons"][s]
        p = x["primary"]
        body.append(
            f"| T### | XGB_PTS_VS_LT pts {s}, A1 (hybrid_lf quantile) - A0 (production lower-tail mixture), integer-support CRPS (n {x['n']}, {x['n_games']} games) | {_f(p['point'])} [{_f(p['lo'])}, {_f(p['hi'])}] game-clustered, p_bh {r['bh'][s]:.4f}; floor {FLOOR} |"
        )
    for s in ("2023", "2024"):
        x = r["seasons"][s]
        body.append(
            f"| T### | XGB_PTS_VS_LT pts {s}, threshold log loss A1 - A0 at DK T-60 main lines (n {x['tll_dk']['n']}) | {_ci_s(x['tll_dk']['A1-A0'])}; margin +{TLL_MARGIN} |"
        )
    x = r["seasons"]["2024"]
    body.append(
        f"| T### | XGB_PTS_VS_LT descriptive 2024, A2 (same recipe, v1 features) - A0 | {_ci_s(x['A2-A0'])} |"
    )
    body.append(
        f"| T### | XGB_PTS_VS_LT descriptive 2024, minutes >= 5 / < 5 slices A1 - A0 | {_ci_s(x['slices']['minutes>=5']['A1-A0'])} / {_ci_s(x['slices']['minutes<5']['A1-A0'])} |"
    )
    body.append(
        f"| T### | XGB_PTS_VS_LT verdict | {r['rule']['verdict']}; conditions {json.dumps(r['rule']['conditions'])} |"
    )
    p = out_dir / "ledger_rows.md"
    p.write_text("\n".join(body) + "\n")
    return p


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "nba.duckdb"))
    ap.add_argument("--pairs-dir", type=Path, default=DEFAULT_PAIRS_DIR)
    ap.add_argument("--refresh-dk", action="store_true")
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.doc, a.sha, a.allow_draft)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "report":
        REPORT_MD.write_text(render_report())
        write_ledger()
        print("wrote", REPORT_MD)
        return 0
    with HeavyLock():
        if a.stage == "gate":
            res = run_gate(a.db, a.doc, sha, a.allow_draft, a.pairs_dir, refresh_dk=a.refresh_dk)
            print(
                json.dumps(
                    {k: res[k] for k in ("sha256", "tests", "gate_ok")}, indent=1, default=_jd
                )
            )
            return 0 if res["gate_ok"] else 1
        if a.stage == "smoke":
            print(json.dumps(run_smoke(a.doc, sha), indent=1, default=_jd))
            return 0
        g = run_gate(a.db, a.doc, sha, False, a.pairs_dir)
        if not g["gate_ok"]:
            print("GATE FAILED: stop, no model result", flush=True)
            return 2
        res = run_arms(a.doc, sha, a.pairs_dir)
        REPORT_MD.write_text(render_report())
        write_ledger()
        print(json.dumps(res["rule"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
