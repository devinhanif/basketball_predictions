# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""WORKLOAD_PTS_V2 harness (rule: docs/prereg/WORKLOAD_PTS_V2.md, FROZEN sha256 20d480f7, commit b9669de).

Claim WF = FULL heads (production + the 5 recent-role columns) + the OTHER 22 WORKLOAD columns, FULL `pi`, lower-tail mixture,
integer-support CRPS of pts; comparator FULL. Guards: past-only placebo of the 22 (PL), knockouts per family (naming), a both-arms
shared-permutation shuffle judged against its own K = 50 null (only if rules 1-2 pass). Descriptive: T452 re-judged (B - A0, B - PL27),
overlap with recent role, knockout partials, date-clustered CI, bias/coverage, DraftKings T-60 decision score.

    M="-m research.eval.workload_pts_v2_eval"
    .venv/bin/python -W ignore $M pins                # frame hash only, no fit (light; checks the 47b569fb pin)
    .venv/bin/python -W ignore $M run --sha <64 hex>  # gate -> arms -> score [-> shuffle -> score] -> report, ONE heavy.lock
    .venv/bin/python -W ignore $M gate|arms|score|shuffle|report --sha <64 hex>
    .venv/bin/python -W ignore $M dryrun --out <dir>  # synthetic labels, every stage end to end, nothing frozen read or written

Every real stage refuses unless the doc text above `=== RESULTS BELOW ===` hashes to `--sha`, a commit `prereg(WORKLOAD_PTS_V2): <sha prefix>`
exists whose copy of the doc hashes to the same value, and (after `gate`) the season <= 2024 frame hashes to the frozen 47b569fb pin.
`nba.duckdb` read-only; `season <= 2024` asserted on every frame; every real-tip read is capped at season 2024 (`tip_cap`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON
from nba.eval.lower_tail_eval import collect_pi, randomized_pit
from nba.props.context_residual import (
    COMMON_FEATURES,
    ContextResidualConfig,
    ContextResidualModel,
    stat_feature_names,
)
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as RRF
from research.eval import workload_eval as WE
from research.recent_role import placebo as PB
from research.workload import asof as W

ID = "WORKLOAD_PTS_V2"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/WORKLOAD_PTS_V2.md"
DOC = ROOT / DOC_REL
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
OUT_DIR = ROOT / "reports/prereg_workload_pts_v2"
REPORT_MD = ROOT / "reports/prereg_workload_pts_v2.md"
CACHE = ROOT / "data/ops/workload_pts_v2_cache"
LT_DIR = ROOT / "reports/lower_tail"
FULL_CACHE = ROOT / "data/ops/recent_role_full_cache"
WL_CACHE = ROOT / "data/ops/workload_cache"
T467_RESULTS = ROOT / "reports/prereg_recent_role_full/results.json"
DB = str(ROOT / "nba.duckdb")

STAT = "pts"
SEASONS = (2023, 2024)
KEYS = ["game_id", "player_id"]
SEED = 0
FRAME_PIN = "47b569fba20e58bcacfcf38148fc270d0b5e4cd6f235f7a8485c329bcd7a1614"
FRAME_ROWS = 55_320

FLOOR = -0.005  # rule 1-2: WF - FULL, integer CRPS
P_MAX = 0.05  # rule 2 (m = 1, BH is the identity)
PLACEBO_MARGIN = -0.003  # rule 3: WF - PL, each season
SHUF_K = 50  # rule 4: seeds per season
SHUF_TOL = -0.001  # rule 4: fires if mean(d) + 2 sd(d)/sqrt(K) < SHUF_TOL
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01  # rule 5
SLICE_TOL = 0.01  # rule 6
MIN_SLICE_N = 300
CARRY = 0.8  # naming: one family carries >= 80%
REPRO_TOL = 1e-6
T467_TOL = 1e-4  # positive control vs T467 when the reproduction gates hold
NULL_SHARE_MAX = 0.02
DESC_FLOOR = -0.003  # descriptive T452 reading: B - PL27 <= -0.003 in both seasons

R5: tuple[str, ...] = PR.R_COLS
MODEL27: tuple[str, ...] = tuple(W.MODEL_COLS)
X22: tuple[str, ...] = tuple(c for c in MODEL27 if c not in R5)
LOADX: tuple[str, ...] = tuple(c for c in W.MODEL_FAMILIES["LOAD"] if c not in R5)
SCHEDULE: tuple[str, ...] = tuple(W.MODEL_FAMILIES["SCHEDULE"])
TRAVEL: tuple[str, ...] = tuple(W.MODEL_FAMILIES["TRAVEL"])
PL = "pl_"  # past-only placebo prefix (one source row per target row, shared by all 27 columns)
FAMILIES = {"LOADX": LOADX, "SCHEDULE": SCHEDULE, "TRAVEL": TRAVEL}

# heads arm -> extra columns on top of stat_feature_names("pts"); pi used in scoring
HEADS: dict[str, tuple[str, ...]] = {
    "P0C": (),
    "FULL": R5,
    "WF": (*R5, *X22),
    "PL": (*R5, *(PL + c for c in X22)),
    "KO_LOADX": (*R5, *SCHEDULE, *TRAVEL),
    "KO_SCHEDULE": (*R5, *LOADX, *TRAVEL),
    "KO_TRAVEL": (*R5, *LOADX, *SCHEDULE),
    "B": MODEL27,
    "PL27": tuple(PL + c for c in MODEL27),
}
PI_OF = {"A0": "A0", "P0C": "A0", "FULL": "FULL", "WF": "FULL", "PL": "FULL", "KO_LOADX": "FULL", "KO_SCHEDULE": "FULL", "KO_TRAVEL": "FULL", "B": "A0", "PL27": "A0"}  # fmt: skip
GATE_ARMS = (
    "P0C",
    "FULL",
)  # fitted and compared in the gate (control refits; FULL was scored as T467)
CLAIM_ARMS = (
    "WF",
    "PL",
    "KO_LOADX",
    "KO_SCHEDULE",
    "KO_TRAVEL",
    "B",
    "PL27",
)  # fitted only after gate.json passes
CONTRASTS = (
    ("WF", "FULL"), ("WF", "PL"), ("FULL", "A0"), ("WF", "KO_LOADX"), ("WF", "KO_SCHEDULE"), ("WF", "KO_TRAVEL"),
    ("KO_LOADX", "FULL"), ("KO_SCHEDULE", "FULL"), ("KO_TRAVEL", "FULL"), ("B", "A0"), ("B", "PL27"), ("PL", "FULL"), ("PL27", "A0"),
)  # fmt: skip
SLICES = (
    "min<5",
    "bench",
    "playoffs",
    "cold_start",
    "b2b_second",
    "three_in_four",
    "road_trip4plus",
)
DESIGN = {
    "LOADX": "the 3 LOAD extras (g_3d, g_7d, min_l5_vs_base) + the 7 LOAD_TRACK columns, as the rule's H1 lists them; LOADX+SCHEDULE+TRAVEL = the 22"
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


# --------------------------------------------------------------------------- frame identity (recipe frozen in the rule)
def frame_sha(df: pl.DataFrame, cols: list[str]) -> str:
    """Rows sorted by (game_id, player_id); key columns newline-joined text; other columns in name order, each cast to Float64
    (null -> NaN), little-endian bytes, prefixed by the name and a NUL. Identical to the gate's `framehash.py`."""
    d = df.with_columns(pl.col("game_id").cast(pl.Utf8)).sort(KEYS)
    h = hashlib.sha256()
    for k in KEYS:
        h.update(k.encode() + b"\0" + "\n".join(map(str, d[k].to_list())).encode())
    for c in sorted(set(cols) - set(KEYS)):
        a = d[c].cast(pl.Float64, strict=False).to_numpy().astype("<f8")
        a = np.where(np.isnan(a), np.float64("nan"), a)
        h.update(c.encode() + b"\0" + a.tobytes())
    return h.hexdigest()


def hash_cols() -> list[str]:
    return [*stat_feature_names(STAT), *MODEL27, "pts", "minutes"]


def a0_frame_sha(fr: pl.DataFrame, gid: np.ndarray, pid: np.ndarray) -> tuple[str, int]:
    """The pinned hash: the A0 rows (stored component keys) joined to the frame, over `hash_cols()`."""
    keys = pl.DataFrame({"game_id": gid.astype(str), "player_id": pid.astype(np.int64)})
    j = keys.join(
        fr.with_columns(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64)),
        on=KEYS,
        how="left",
    )
    if j.height != keys.height:
        raise RuntimeError("A0 keys are not unique in the frame")
    cols = hash_cols()
    missing = [c for c in cols if c not in j.columns]
    if missing:
        raise RuntimeError(f"frame lacks hashed columns {missing}")
    found = int(j["game_date"].is_not_null().sum()) if "game_date" in j.columns else j.height
    return frame_sha(j.select(*KEYS, *cols), cols), found


# --------------------------------------------------------------------------- freeze and holdout
def frozen_sha(doc: Path) -> str:
    return WE.frozen_sha(doc)


def freeze_commit(sha: str, git: Callable[..., str] = _git, doc_rel: str = DOC_REL) -> str | None:
    """The freeze commit: subject `prereg(<ID>): <hex>` with `sha` starting with that hex (>= 8 chars), and the commit's own copy of the doc
    hashes to `sha` (the subjects carry the sha8 only)."""
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.match(rf"prereg\({re.escape(ID)}\): ([0-9a-f]{{8,64}})\b", subj.strip())
        if not m or not sha.startswith(m.group(1)):
            continue
        blob = git("show", f"{h}:{doc_rel}")
        keep: list[str] = []
        for ln in blob.splitlines(keepends=True):
            if ln.rstrip("\n") == MARKER:
                break
            keep.append(ln)
        if hashlib.sha256("".join(keep).encode()).hexdigest() == sha:
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, git: Callable[..., str] = _git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha[:8]}` whose doc hashes to {sha[:8]}: the rule is not frozen; no arm may be fitted"
        )
    return sha


def freeze_time(sha: str, git: Callable[..., str] = _git) -> float | None:
    h = freeze_commit(sha, git)
    if h is None:
        return None
    out = git("log", "-1", "--format=%ct", h).strip()
    return float(out) if out else None


@contextmanager
def tip_cap(cap: int = MAX_SEASON) -> Iterator[list[int | None]]:
    """Every real-tip read in the block goes through `build_game_tipoff(max_season <= 2024)`: a call with None is capped, a call above the
    cap raises. Yields the list of requested caps (for tests)."""
    from nba.features import game_tipoff as GT

    orig = GT.build_game_tipoff
    calls: list[int | None] = []

    def capped(schedule_dir: Any = None, max_season: int | None = None) -> pl.DataFrame:
        calls.append(max_season)
        if max_season is not None and max_season > cap:
            raise RuntimeError(
                f"tip read with max_season={max_season} > {cap}: holdout schedule file"
            )
        return orig(schedule_dir, max_season=cap if max_season is None else max_season)

    with mock.patch.object(GT, "build_game_tipoff", capped):
        yield calls


# --------------------------------------------------------------------------- context (real or synthetic)
@dataclass
class Ctx:
    """Everything a stage reads or writes. `real()` points at the repo; `dryrun` builds a synthetic world in a scratch dir."""

    out_dir: Path
    cache: Path
    cfg: ContextResidualConfig | None = None
    frame_fn: Callable[[], tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]] | None = None
    comps: dict[str, np.ndarray] | None = None  # stored A0 components (pts)
    pic: dict[str, np.ndarray] | None = None  # stored A0 pi + w_q
    full_heads: dict[str, np.ndarray] | None = None  # cached FULL heads (T467)
    full_pi: np.ndarray | None = None  # cached FULL pi
    b_cached: dict[str, np.ndarray] | None = None  # cached T452 B (qi on the mixture)
    t467: dict[int, float] = field(default_factory=dict)
    pin: str = FRAME_PIN
    pin_rows: int = FRAME_ROWS
    n_shuffle: int = SHUF_K
    holdout_text: str = ""
    real_plants: bool = True
    force_shuffle: bool = False  # dryrun only: exercise the shuffle path regardless of the primary
    frozen_at: float | None = None
    sha: str = "dryrun"
    _frame: Any = None

    def frame(self) -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
        if self._frame is None:
            assert self.frame_fn is not None
            self._frame = self.frame_fn()
        return self._frame


def build_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    """Production feature rows + all WORKLOAD columns (the gate's loaders) + the past-only placebo of the 27 (seed 0, one source row)."""
    from research.eval import four_factors_eval as F

    with tip_cap():
        study = F.load_study(DB)
        src = WE.load_sources(DB)
    feats = study["feats"]
    WE.assert_seasons(feats)
    wl = WE.attach_workload(feats.select("game_id", "player_id", "team_id", "game_date"), src)
    fr = feats.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl.drop("team_id", "game_date"), on=KEYS, how="left"
    )
    if fr.height != feats.height:
        raise ValueError("workload join changed the feature rows")
    fr = fr.with_columns(pl.col("game_id").cast(pl.Utf8))
    fr = PB.permute_past_only(fr, MODEL27, SEED, PL)
    WE.assert_seasons(fr)
    return fr, study["cfg"], {"src": src}


def real_ctx(sha: str | None) -> Ctx:
    def load(p: Path) -> dict[str, np.ndarray]:
        z = np.load(p, allow_pickle=True)
        return {k: z[k] for k in z.files}

    t467: dict[int, float] = {}
    if T467_RESULTS.exists():
        res = json.loads(T467_RESULTS.read_text())
        for r in res["scores"]["rows"]:
            if r.get("stat") == STAT and r.get("arm") == "FULL_minus_A0":
                t467[int(r["season"])] = float(r["delta"])
    return Ctx(
        out_dir=OUT_DIR, cache=CACHE, frame_fn=build_frame,
        comps=load(LT_DIR / f"components_{STAT}.npz"), pic=load(LT_DIR / "components_pi.npz"),
        full_heads=load(FULL_CACHE / f"heads_{STAT}_R.npz"), full_pi=np.load(FULL_CACHE / "pi_R.npz")["pi"],
        b_cached=load(WL_CACHE / f"{STAT}__B.npz") if (WL_CACHE / f"{STAT}__B.npz").exists() else None,
        t467=t467, holdout_text=HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else "",
        frozen_at=None if sha is None else freeze_time(sha), sha=sha or "none",
    )  # fmt: skip


# --------------------------------------------------------------------------- walks
_IMP: list[dict[str, float]] = []


class _Recording(ContextResidualModel):
    """ContextResidualModel that records each fit's centre + scale gain importance (by name). Fit and predictions are unchanged."""

    def fit(self, train: pl.DataFrame) -> ContextResidualModel:
        out = super().fit(train)
        g: dict[str, float] = {}
        for head in (self.mean_head, self.scale_head):
            if head is None:
                continue
            for n, v in zip(self.names, head.booster_.feature_importance("gain"), strict=True):
                g[n] = g.get(n, 0.0) + float(v)
        _IMP.append(g)
        return out


def walk(
    fr: pl.DataFrame,
    cfg: ContextResidualConfig,
    arm: str,
    shuffle: bool = False,
    seed: int = SEED,
    record: bool = False,
    log: bool = True,
    **kw: Any,
) -> dict[str, Any]:
    """`recent_role_full_eval.collect_heads` with the arm's explicit feature list (production month blocks, production LightGBM, seed 0).
    `record`: the same walk with each block's gain importances captured (outputs bit-identical; unit-tested)."""
    names = [*stat_feature_names(STAT), *HEADS[arm]]
    if not record:
        return RRF.collect_heads(fr, STAT, cfg, names, shuffle=shuffle, seed=seed, log=log, **kw)
    _IMP.clear()
    with mock.patch.object(RRF, "ContextResidualModel", _Recording):
        out = RRF.collect_heads(fr, STAT, cfg, names, shuffle=shuffle, seed=seed, log=log, **kw)
    tot: dict[str, float] = {}
    for g in _IMP:
        for k, v in g.items():
            tot[k] = tot.get(k, 0.0) + v
    out["imp_names"] = np.array(list(tot))
    out["imp_gain"] = np.array([tot[k] for k in tot])
    return out


def importance_share(comp: dict[str, Any], cols: tuple[str, ...] = X22) -> float:
    names, gain = [str(x) for x in comp["imp_names"]], np.asarray(comp["imp_gain"], dtype=float)
    tot = float(gain.sum())
    return (
        float(sum(g for n, g in zip(names, gain, strict=True) if n in cols) / tot)
        if tot > 0
        else 0.0
    )


def ckpt(ctx: Ctx, name: str) -> Path:
    return ctx.cache / f"{name}.npz"


def save(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **obj)


def load(path: Path) -> dict[str, np.ndarray]:
    z = np.load(path, allow_pickle=True)
    return {k: z[k] for k in z.files}


# --------------------------------------------------------------------------- scoring helpers
def qi_of(comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], pi: np.ndarray) -> np.ndarray:
    return WE.mixture_qi(comp, PR.candidate_pic(pic, pi), STAT)


def crps(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    return PR.crps_int(qi, y)


def coverage(qi: np.ndarray, y: np.ndarray, season: np.ndarray) -> dict[str, dict[str, float]]:
    """Randomised-PIT coverage P(PIT < tau) on the integer grid; fixed row order, seed 0."""
    pit = randomized_pit(qi.astype(float), y.astype(float), seed=SEED)
    return {
        str(s): {f"{t:.2f}": float((pit[season == s] < t).mean()) for t in COV_TAUS}
        for s in SEASONS
    }


def ratio_ci(
    num: np.ndarray, den: np.ndarray, gid: np.ndarray, n_boot: int = WE.N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """share = 1 - mean(num) / mean(den), point and a paired, game-clustered percentile interval (the same game weights for both means)."""
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    sn, sd = np.bincount(inv, weights=num, minlength=g), np.bincount(inv, weights=den, minlength=g)
    c = np.bincount(inv, minlength=g).astype(float)
    w = np.random.default_rng(seed).multinomial(g, np.full(g, 1.0 / g), size=n_boot).astype(float)
    tot = np.maximum(w @ c, 1.0)
    mn, md = (w @ sn) / tot, (w @ sd) / tot
    with np.errstate(divide="ignore", invalid="ignore"):
        sh = 1.0 - mn / md
    sh = sh[np.isfinite(sh)]
    point = 1.0 - float(num.mean()) / float(den.mean()) if den.mean() != 0 else float("nan")
    return {
        "share": point,
        "lo": float(np.percentile(sh, 2.5)) if len(sh) else float("nan"),
        "hi": float(np.percentile(sh, 97.5)) if len(sh) else float("nan"),
    }


def slice_masks(base: dict[str, np.ndarray], meta: pl.DataFrame) -> dict[str, np.ndarray]:
    """Rule 6 slices on the scored rows. min<5 / bench / playoffs / cold_start from the components (PR.slice_masks); b2b second, 3-in-4 and
    road trip 4+ from the as-of WORKLOAD slice columns joined by key."""
    m = PR.slice_masks(base)
    keys = pl.DataFrame(
        {"game_id": base["row_gid"].astype(str), "player_id": base["row_pid"].astype(np.int64)}
    )
    j = keys.join(meta, on=KEYS, how="left")
    out = {
        "all": m["all"],
        "min<5": m["min<5"],
        "bench": m["bench"],
        "playoffs": m["playoffs"],
        "cold_start": m["cold_start"],
    }
    for name, col in (
        ("b2b_second", "wl_b2b_second"),
        ("three_in_four", "wl_three_in_four"),
        ("road_trip4plus", "wl_road_trip4plus"),
    ):
        out[name] = (
            (j[col].fill_null(0).cast(pl.Float64).fill_nan(0.0).to_numpy() == 1)
            if col in j.columns
            else np.zeros(keys.height, bool)
        )
    return out


def meta_cols(fr: pl.DataFrame) -> pl.DataFrame:
    keep = [
        c
        for c in ("game_date", "wl_b2b_second", "wl_three_in_four", "wl_road_trip4plus")
        if c in fr.columns
    ]
    return fr.select(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64), *keep)


# --------------------------------------------------------------------------- gate
def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return WE._check(name, value, threshold, ok)


def inputs_identical(new: dict[str, np.ndarray], stored: dict[str, np.ndarray]) -> bool:
    """Data-drift test: same row keys and the same stored row inputs (y, m, minutes, min10, n_prior)."""
    if len(new["row_gid"]) != len(stored["row_gid"]) or not RRF.same_rows(new, stored):
        return False
    return all(
        k in stored
        and np.array_equal(np.asarray(new[k], float), np.asarray(stored[k], float), equal_nan=True)
        for k in ("row_y", "row_m", "row_minutes", "row_min10", "row_n_prior")
    )


def planted_heads(
    fr: pl.DataFrame, cfg: ContextResidualConfig, arm: str = "WF"
) -> list[dict[str, Any]]:
    """As-of plant on WF's feature list (pts, 2024): overwrite `pts`, `minutes` and every WORKLOAD column (and placebo) of rows dated
    on/after a mid-2024 cutoff; rows in blocks that end before the cutoff's month are bit-identical, rows in later blocks change."""
    d24 = fr.filter(pl.col("season") == 2024).sort("game_date")["game_date"]
    cut = d24[d24.len() // 2]
    late = pl.col("game_date") >= cut
    cols = [c for c in (*MODEL27, *(PL + c for c in MODEL27)) if c in fr.columns]
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
        pl.when(late).then(99.0).otherwise(pl.col("pts").cast(pl.Float64)).alias("pts"),
        *[pl.when(late).then(48.0).otherwise(pl.col(c).cast(pl.Float64)).alias(c) for c in cols],
    )
    a = walk(fr, cfg, arm, test_seasons=(2024,), log=False)
    b = walk(edited, cfg, arm, test_seasons=(2024,), log=False)
    ja = pl.DataFrame(
        {"game_id": a["row_gid"], "player_id": a["row_pid"], "ca": a["row_c"], "sa": a["row_s"]}
    )
    jb = pl.DataFrame(
        {"game_id": b["row_gid"], "player_id": b["row_pid"], "cb": b["row_c"], "sb": b["row_s"]}
    )
    date = fr.select(
        pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64), "game_date"
    )
    j = ja.join(jb, on=KEYS).join(date, on=KEYS)
    nxt = cut.replace(day=1).replace(month=cut.month % 12 + 1, year=cut.year + (cut.month == 12))
    pre, post = (
        j.filter(pl.col("game_date") < cut.replace(day=1)),
        j.filter(pl.col("game_date") >= nxt),
    )
    same = (
        float(
            max(
                np.abs(pre["ca"].to_numpy() - pre["cb"].to_numpy()).max(),
                np.abs(pre["sa"].to_numpy() - pre["sb"].to_numpy()).max(),
            )
        )
        if pre.height
        else 0.0
    )
    moved = (
        float(np.abs(post["ca"].to_numpy() - post["cb"].to_numpy()).max()) if post.height else 0.0
    )
    return [
        _chk(f"planted future (heads, {arm}): centre/scale of rows in blocks before the cutoff identical", {"cutoff": str(cut), "n_rows": pre.height, "max_abs_diff": same}, "== 0 and n > 0", same == 0.0 and pre.height > 0),
        _chk(f"control (heads, {arm}): rows in later blocks change", {"n_rows": post.height, "max_abs_diff": moved}, "> 0", moved > 0.0),
    ]  # fmt: skip


def placebo_nan_check(fr: pl.DataFrame, gid: np.ndarray, pid: np.ndarray) -> dict[str, Any]:
    """W6: placebo NaN iff the row has no earlier played row (strictly earlier date) in its player-season, on the whole frame (asserted);
    the A0-row NaN count against production `n_season == 0` (reported)."""
    src = PB.past_only_source(fr, SEED)
    d = fr.select(
        "player_id", "season", "game_date", *(["n_season"] if "n_season" in fr.columns else [])
    ).with_columns(pl.Series("_has", src >= 0))
    d = d.with_columns(
        (pl.col("game_date") == pl.col("game_date").min().over(["player_id", "season"])).alias(
            "_first_day"
        )
    )
    iff = bool((d["_has"] == ~d["_first_day"]).all())
    nan_col = fr[PL + X22[0]].to_numpy().astype(float)
    iff_col = bool(np.array_equal(np.isnan(nan_col), ~(src >= 0)))
    keys = pl.DataFrame({"game_id": gid.astype(str), "player_id": pid.astype(np.int64)})
    j = keys.join(
        fr.select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            "season",
            pl.Series("_has", src >= 0),
            *(["n_season"] if "n_season" in fr.columns else []),
        ),
        on=KEYS,
        how="left",
    )
    per = {str(s): int((~j.filter(pl.col("season") == s)["_has"]).sum()) for s in SEASONS}
    out: dict[str, Any] = {"nan_iff_first_day_whole_frame": iff and iff_col, "a0_nan_rows": per}
    if "n_season" in j.columns:
        out["a0_n_season_eq_0"] = int((j["n_season"] == 0).sum())
        out["a0_both"] = int(((~j["_has"]) & (j["n_season"] == 0)).sum())
    return out


def run_gate(ctx: Ctx) -> dict[str, Any]:
    """Every gate before any claim arm: frame pin, placebo NaN assertion, join coverage and missingness of the 22, reproduction (P0C, FULL heads,
    FULL pi) with the drift rule, the positive control FULL - A0, plants, slice sizes, holdout log. Writes gate.json."""
    t0 = time.time()
    tests: list[dict[str, Any]] = []
    fr, cfg0, aux = ctx.frame()
    cfg = ctx.cfg or cfg0
    WE.assert_seasons(fr)
    comps, pic = ctx.comps, ctx.pic
    assert (
        comps is not None
        and pic is not None
        and ctx.full_heads is not None
        and ctx.full_pi is not None
    )
    if int(comps["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in the stored components")
    sha, found = a0_frame_sha(fr, comps["row_gid"], comps["row_pid"])
    tests.append(_chk("frame identity: sha256 of the A0 rows x 71 columns equals the frozen pin", {"sha256": sha, "pin": ctx.pin, "a0_rows": len(comps["row_gid"]), "found": found}, f"== {ctx.pin[:8]}, rows {ctx.pin_rows}", sha == ctx.pin and found == ctx.pin_rows == len(comps["row_gid"])))  # fmt: skip
    if not tests[-1]["ok"]:
        return _write_gate(
            ctx,
            tests,
            sha,
            t0,
            {},
            stop="frame hash differs from the frozen pin: the data changed after the freeze; stop before any arm",
        )
    pn = placebo_nan_check(fr, comps["row_gid"], comps["row_pid"])
    tests.append(
        _chk(
            "W6: past-only placebo NaN iff no earlier played row in the player-season (whole frame); A0 NaN rows vs n_season == 0",
            pn,
            "asserted (iff); counts reported",
            pn["nan_iff_first_day_whole_frame"],
        )
    )
    keys = pl.DataFrame(
        {"game_id": comps["row_gid"].astype(str), "player_id": comps["row_pid"].astype(np.int64)}
    )
    a0rows = keys.join(
        fr.select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            "season",
            "minutes",
            *X22,
            *(["team_game_no"] if "team_game_no" in fr.columns else []),
        ),
        on=KEYS,
        how="left",
    )
    nulls = {c: int(a0rows.select(WE._null_expr(a0rows, c).sum()).item()) for c in X22}
    tests.append(_chk("join coverage: the 22 columns attach to every A0 row; NULL share per column", {"n_a0_rows": a0rows.height, "minutes_null": int(a0rows["minutes"].null_count()), "null_or_nan": nulls}, f"all rows found; every column null share < {NULL_SHARE_MAX:.0%}", a0rows["minutes"].null_count() == 0 and all(v / a0rows.height < NULL_SHARE_MAX for v in nulls.values())))  # fmt: skip
    aud = WE.audit_nulls(
        a0rows.filter(pl.col("season").is_in(list(SEASONS))),
        list(X22),
        WE.minutes_bucket("minutes"),
    )
    tests.append(
        _chk(
            "missingness of the 22 by realised-minutes bucket (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp")},
            f"<= max(2 pp, 3 x permuted) = {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )
    # reproduction
    ctx.cache.mkdir(parents=True, exist_ok=True)
    p0c = walk(fr, cfg, "P0C")
    save(ckpt(ctx, "heads_P0C"), p0c)
    full = walk(fr, cfg, "FULL")
    save(ckpt(ctx, "heads_FULL"), full)
    rp0 = RRF.heads_vs_stored(p0c, comps)
    rfu = RRF.heads_vs_stored(full, ctx.full_heads)
    pi_full = PR.align_to_stored(PR.walk_pi(fr, PR.CAND_COLS, cfg), pic["row_gid"], pic["row_pid"])
    dpi = (
        float(np.abs(pi_full - ctx.full_pi).max())
        if len(pi_full) == len(ctx.full_pi)
        else float("inf")
    )
    repro_ok = (
        rp0["rows_equal"]
        and rp0["max_abs_diff"] <= REPRO_TOL
        and rfu["rows_equal"]
        and rfu["max_abs_diff"] <= REPRO_TOL
        and dpi <= REPRO_TOL
    )
    drift: dict[str, Any] = {"drift": False}
    if not repro_ok:
        same_in = inputs_identical(p0c, comps) and inputs_identical(full, ctx.full_heads)
        if same_in:
            tests.append(
                _chk(
                    "reproduction (P0C, FULL heads, FULL pi) with IDENTICAL stored inputs",
                    {"P0C": rp0, "FULL": rfu, "pi_max_abs_diff": dpi},
                    f"<= {REPRO_TOL}",
                    False,
                )
            )
            return _write_gate(
                ctx,
                tests,
                sha,
                t0,
                drift,
                stop="reproduction failed with identical stored inputs: a code defect (kill)",
            )
        a0pic = collect_pi(fr, cfg, stats=(STAT,))
        drift = {
            "drift": True,
            "P0C_vs_stored": rp0,
            "FULL_vs_cache": rfu,
            "pi_full_max_abs_diff": dpi,
            "rows_now": len(p0c["row_gid"]),
            "rows_stored": len(comps["row_gid"]),
        }
        pi_full = PR.align_to_stored(
            PR.walk_pi(fr, PR.CAND_COLS, cfg), a0pic["row_gid"], a0pic["row_pid"]
        )
        save(ckpt(ctx, "a0_pic_refit"), a0pic)
    save(ckpt(ctx, "pi_FULL"), {"pi": pi_full})
    tests.append(_chk("reproduction: P0C == stored components; FULL refit == heads_pts_R.npz; FULL pi refit == pi_R.npz (or data drift: every arm refit, reported)", {"P0C": {k: rp0[k] for k in ("rows_equal", "max_abs_diff")}, "FULL": {k: rfu[k] for k in ("rows_equal", "max_abs_diff")}, "pi_max_abs_diff": dpi, "drift": drift["drift"]}, f"<= {REPRO_TOL}, or drift with differing inputs", True))  # fmt: skip
    # positive control: FULL - A0 (already scored as T467)
    base, bpic = (p0c, load(ckpt(ctx, "a0_pic_refit"))) if drift["drift"] else (comps, pic)
    y, gid, season = base["row_y"], base["row_gid"].astype(str), base["row_season"]
    d = crps(qi_of(full, bpic, pi_full), y) - crps(WE.mixture_qi(base, bpic, STAT), y)
    pc = {s: WE.delta_stats(d[season == s], gid[season == s]) for s in SEASONS}
    if drift["drift"]:
        pc_ok = all(pc[s]["delta"] <= FLOOR and pc[s]["ci_hi"] < 0 for s in SEASONS)
        thr = "drift: <= -0.005 with CI upper < 0, both seasons"
    else:
        pc_ok = bool(ctx.t467) and all(
            abs(pc[s]["delta"] - ctx.t467[s]) <= T467_TOL for s in SEASONS
        )
        thr = f"|FULL - A0 - T467| <= {T467_TOL} both seasons"
    tests.append(_chk("positive control: FULL - A0 reproduces T467 (harness can move)", {str(s): {"delta": pc[s]["delta"], "ci": [pc[s]["ci_lo"], pc[s]["ci_hi"]], "t467": ctx.t467.get(s), "n": int((season == s).sum())} for s in SEASONS}, thr, pc_ok))  # fmt: skip
    # plants
    if ctx.real_plants:
        tests += WE.planted_tests(
            aux["src"], fr.select("game_id", "player_id", "season", "game_date")
        )
    tests += planted_heads(fr, cfg, "WF")
    masks = slice_masks(base, meta_cols(fr))
    sizes = {str(s): {k: int((m & (season == s)).sum()) for k, m in masks.items()} for s in SEASONS}
    tests.append(
        _chk(
            "slice sizes (n rows) per season",
            sizes,
            f">= {MIN_SLICE_N} to count (a smaller slice is reported and cannot fail)",
            True,
        )
    )
    tests.append(_chk(f"HOLDOUT_ACCESS_LOG: no row names {ID}; no 2025 loader", {"rows_naming_id": sum(ID in ln for ln in ctx.holdout_text.splitlines()), "max_season_frame": int(fr["season"].max())}, "== 0; max season <= 2024", ID not in ctx.holdout_text and int(fr["season"].max()) <= MAX_SEASON))  # fmt: skip
    return _write_gate(ctx, tests, sha, t0, drift)


def _write_gate(
    ctx: Ctx,
    tests: list[dict[str, Any]],
    frame_hash: str,
    t0: float,
    drift: dict[str, Any],
    stop: str | None = None,
) -> dict[str, Any]:
    gate = {
        "tests": tests,
        "gate_ok": stop is None and all(t["ok"] for t in tests),
        "stop": stop,
        "sha256": ctx.sha,
        "frame_sha256": frame_hash,
        "drift": drift,
        "seconds": round(time.time() - t0, 1),
    }
    WE.write_json(ctx.out_dir / "gate.json", gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ")
            + f"{t['test']}: {json.dumps(t['value'], default=str)[:400]} ({t['threshold']})",
            flush=True,
        )
    if stop:
        print(f"STOP: {stop}", flush=True)
    return gate


def require_gate(ctx: Ctx) -> dict[str, Any]:
    p = ctx.out_dir / "gate.json"
    if not p.exists():
        raise RuntimeError("no gate.json: run the gate stage first")
    g = json.loads(p.read_text())
    if not g.get("gate_ok") or g.get("sha256") != ctx.sha:
        raise RuntimeError("the frozen gate has not passed for this rule: stop (kill criterion)")
    return g


def check_frame(ctx: Ctx, gate: dict[str, Any]) -> pl.DataFrame:
    """`arms` / `shuffle` refuse if the frame they build differs from the one the gate hashed (and from the pin)."""
    fr, _, _ = ctx.frame()
    assert ctx.comps is not None
    sha, _ = a0_frame_sha(fr, ctx.comps["row_gid"], ctx.comps["row_pid"])
    if sha != gate["frame_sha256"] or sha != ctx.pin:
        raise RuntimeError(
            f"frame sha {sha[:8]} differs from the gate's {str(gate['frame_sha256'])[:8]} / pin {ctx.pin[:8]}: refuse"
        )
    return fr


def check_checkpoint(ctx: Ctx, path: Path) -> bool:
    return RRF.check_checkpoint(path, ctx.frozen_at)


# --------------------------------------------------------------------------- arms
def run_arms(ctx: Ctx) -> None:
    gate = require_gate(ctx)
    fr = check_frame(ctx, gate)
    cfg = ctx.cfg or ctx.frame()[1]
    base = load(ckpt(ctx, "heads_P0C")) if gate["drift"].get("drift") else ctx.comps
    for arm in CLAIM_ARMS:
        p = ckpt(ctx, f"heads_{arm}")
        if check_checkpoint(ctx, p):
            _log(f"heads {arm} from checkpoint")
            continue
        _log(f"heads walk {arm} ({len(HEADS[arm])} extra columns)")
        comp = walk(fr, cfg, arm, record=(arm == "WF"))
        if not RRF.same_rows(comp, base):
            raise RuntimeError(f"{arm}: heads rows do not match the A0 rows")
        save(p, comp)


# --------------------------------------------------------------------------- score
def _arm_scores(ctx: Ctx, gate: dict[str, Any]) -> dict[str, Any]:
    drift = bool(gate["drift"].get("drift"))
    assert ctx.comps is not None and ctx.pic is not None
    base = load(ckpt(ctx, "heads_P0C")) if drift else ctx.comps
    pic = load(ckpt(ctx, "a0_pic_refit")) if drift else ctx.pic
    pis = {"A0": pic["row_pi"], "FULL": load(ckpt(ctx, "pi_FULL"))["pi"]}
    heads = {"A0": base, **{a: load(ckpt(ctx, f"heads_{a}")) for a in ("FULL", *CLAIM_ARMS)}}
    for a, h in heads.items():
        if not RRF.same_rows(h, base):
            raise RuntimeError(f"{a}: rows differ from the A0 rows")
    qis = {a: qi_of(h, pic, pis[PI_OF[a]]) for a, h in heads.items()}
    return {
        "base": base,
        "pic": pic,
        "pis": pis,
        "heads": heads,
        "qis": qis,
        "scores": {a: crps(q, base["row_y"]) for a, q in qis.items()},
    }


def run_score(ctx: Ctx) -> dict[str, Any]:
    gate = require_gate(ctx)
    fr, _, _ = ctx.frame()
    A = _arm_scores(ctx, gate)
    base, sc, qis = A["base"], A["scores"], A["qis"]
    y, gid, season = base["row_y"], base["row_gid"].astype(str), base["row_season"]
    meta = meta_cols(fr)
    keys = pl.DataFrame({"game_id": gid, "player_id": base["row_pid"].astype(np.int64)})
    date = (
        keys.join(meta.select(*KEYS, "game_date"), on=KEYS, how="left")["game_date"]
        .cast(pl.Utf8)
        .to_numpy()
        .astype(str)
    )
    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        ng = int(len(np.unique(gid[sm])))
        for a, v in sc.items():
            rows.append(
                {
                    "season": s,
                    "arm": a,
                    "n_rows": int(sm.sum()),
                    "n_games": ng,
                    "crps_int": float(v[sm].mean()),
                }
            )
        for a, b in CONTRASTS:
            rows.append(
                {
                    "season": s,
                    "arm": f"{a}_minus_{b}",
                    "n_rows": int(sm.sum()),
                    "n_games": ng,
                    **WE.delta_stats(sc[a][sm] - sc[b][sm], gid[sm]),
                }
            )
        dd = WE.delta_stats((sc["WF"] - sc["FULL"])[sm], date[sm])
        rows.append(
            {
                "season": s,
                "arm": "WF_minus_FULL_date_clustered",
                "n_rows": int(sm.sum()),
                "n_dates": int(len(np.unique(date[sm]))),
                **dd,
            }
        )
    for r in rows:
        if r["arm"] == "WF_minus_FULL":
            r["p_bh"] = r["p"]  # m = 1: BH is the identity (declared)
    masks = slice_masks(base, meta)
    d = sc["WF"] - sc["FULL"]
    slices = []
    for s in SEASONS:
        for name, mk in masks.items():
            m = mk & (season == s)
            rec: dict[str, Any] = {"season": s, "slice": name, "n": int(m.sum())}
            if m.sum() >= MIN_SLICE_N or (name == "all" and m.any()):
                rec.update(
                    {
                        k: v
                        for k, v in WE.delta_stats(d[m], gid[m]).items()
                        if k in ("delta", "ci_lo", "ci_hi")
                    }
                )
            elif m.any():
                rec["delta"] = float(d[m].mean())
                rec["reported_only"] = True
            slices.append(rec)
    cov = {a: coverage(qis[a], y, season) for a in ("A0", "FULL", "WF", "B")}
    bias = {
        a: {
            str(s): float((qis[a][season == s].astype(float).mean(axis=1) - y[season == s]).mean())
            for s in SEASONS
        }
        for a in ("A0", "FULL", "WF", "B")
    }
    desc = descriptive(ctx, A, gid, season)
    res: dict[str, Any] = {
        "rows": rows,
        "slices": slices,
        "coverage": cov,
        "bias": bias,
        "descriptive": desc,
    }
    shuf = shuffle_summary(ctx)
    verdict = decide(res, bool(gate["gate_ok"]), shuf)
    out = {
        "id": ID,
        "sha256": ctx.sha,
        "design_choices": DESIGN,
        "scores": res,
        "shuffle": shuf,
        "verdict": verdict,
        "gate": gate["tests"],
        "drift": gate["drift"],
    }
    if ctx.out_dir == OUT_DIR:
        out["decision_score_dk_t60"] = PR.decision_scores(
            {
                a: {STAT: {"qi": qis[a], "gid": gid, "pid": base["row_pid"].astype(np.int64)}}
                for a in ("FULL", "WF")
            }
        )
    WE.write_json(ctx.out_dir / "results.json", out)
    return out


def descriptive(ctx: Ctx, A: dict[str, Any], gid: np.ndarray, season: np.ndarray) -> dict[str, Any]:
    sc, qis, heads = A["scores"], A["qis"], A["heads"]
    out: dict[str, Any] = {}
    g_wf, g_b = sc["WF"] - sc["FULL"], sc["B"] - sc["A0"]
    out["overlap"] = {str(s): {**ratio_ci(g_wf[season == s], g_b[season == s], gid[season == s]), "row_corr_of_gains": float(np.corrcoef(g_wf[season == s], g_b[season == s])[0, 1]) if (season == s).sum() > 2 else float("nan"), "n": int((season == s).sum())} for s in SEASONS}  # fmt: skip
    if ctx.b_cached is not None:
        bc = ctx.b_cached
        same = (
            len(bc["qi"]) == len(qis["B"])
            and np.array_equal(bc["gid"].astype(str), heads["B"]["row_gid"].astype(str))
            and np.array_equal(bc["pid"].astype(np.int64), heads["B"]["row_pid"].astype(np.int64))
        )
        if same:
            dq = float(np.abs(bc["qi"].astype(float) - qis["B"].astype(float)).max())
            dc = {
                str(s): float((crps(bc["qi"], bc["y"]) - sc["B"])[season == s].mean())
                for s in SEASONS
            }
            out["B_refit_vs_cached_pts__B"] = {
                "rows_equal": True,
                "max_abs_qi_diff": dq,
                "mean_crps_diff": dc,
                "match_1e-6": dq <= REPRO_TOL,
            }
        else:
            out["B_refit_vs_cached_pts__B"] = {
                "rows_equal": False,
                "rows_cached": int(len(bc["qi"])),
                "rows_now": int(len(qis["B"])),
                "match_1e-6": False,
                "note": "drift reported beside the reading",
            }
    else:
        out["B_refit_vs_cached_pts__B"] = {"note": "no cached pts__B.npz (dryrun or missing)"}
    bpl = {
        s: WE.delta_stats((sc["B"] - sc["PL27"])[season == s], gid[season == s]) for s in SEASONS
    }
    holds = all(bpl[s]["delta"] <= DESC_FLOOR for s in SEASONS)
    out["T452_rejudged"] = {"B_minus_PL27": {str(s): bpl[s] for s in SEASONS}, "reading": ("T452's failed condition would have held under a valid placebo; with the knockouts naming LOAD, T452 becomes 'LOAD (recent-role minutes) carries a pts gain', i.e. FULL (a T452 annotation, not a new pass)" if holds else "B - PL27 > -0.003 in at least one season: T452's placebo condition fails under a valid placebo too"), "holds_both_seasons": holds}  # fmt: skip
    wf = heads["WF"]
    out["arm_saw_it"] = {
        "x22_gain_share_WF": importance_share(wf) if "imp_names" in wf else None,
        "note": "share 0 = the arm never read the 22 columns (pipeline fault, not a null)",
    }
    return out


def decide(res: dict[str, Any], gate_ok: bool, shuf: dict[str, Any]) -> dict[str, Any]:
    r = {(x["season"], x["arm"]): x for x in res["rows"]}
    a, b = r[(2023, "WF_minus_FULL")], r[(2024, "WF_minus_FULL")]
    r1 = a["delta"] <= FLOOR and a["ci_hi"] < 0
    r2 = b["delta"] <= FLOOR and b["ci_hi"] < 0 and b["p"] <= P_MAX
    r3 = all(r[(s, "WF_minus_PL")]["delta"] <= PLACEBO_MARGIN for s in SEASONS)
    if not (r1 and r2):
        r4: bool | None = None
        r4_note = "not run, primary failed"
    elif not shuf.get("complete"):
        r4, r4_note = False, "primary passed: the shuffle must run before a verdict (incomplete)"
    else:
        r4 = not shuf["fires"]
        r4_note = "guard fired" if shuf["fires"] else "guard did not fire"
    cov_bad = []
    for s in SEASONS:
        for t in COV_TAUS:
            k = f"{t:.2f}"
            worse = abs(res["coverage"]["WF"][str(s)][k] - t) - abs(
                res["coverage"]["FULL"][str(s)][k] - t
            )
            if worse > COV_TOL:
                cov_bad.append((s, k, worse))
    sl_bad = [
        (x["season"], x["slice"], x["n"], x["delta"])
        for x in res["slices"]
        if x["slice"] in SLICES and x["n"] >= MIN_SLICE_N and x.get("delta", -1.0) > SLICE_TOL
    ]
    g24 = b["delta"]
    naming = {}
    for fam in FAMILIES:
        drop = r[(2024, f"KO_{fam}_minus_FULL")]["delta"]
        naming[fam] = {
            "gain_left_when_dropped": drop,
            "carries": bool(WE.carries(g24, drop, None)) if g24 < 0 else False,
        }
    named = [f for f, v in naming.items() if v["carries"]]
    passed = bool(r1 and r2 and r3 and r4 is True and not cov_bad and not sl_bad and gate_ok)
    return {
        "pass": passed, "candidate_needs_adversary_clearance": passed and bool(named),
        "rule1_select_2023": bool(r1), "rule2_report_2024": bool(r2), "rule3_placebo": bool(r3), "rule4_shuffle": r4, "rule4_note": r4_note,
        "rule5_coverage_ok": not cov_bad, "coverage_bad": cov_bad, "rule6_slices_ok": not sl_bad, "slices_worse": sl_bad, "rule7_gate_ok": bool(gate_ok),
        "naming": naming, "named_family": named, "d2023": a["delta"], "d2024": g24, "ci2024": [b["ci_lo"], b["ci_hi"]], "mde2024": b["mde"],
        "null_statement": f"no effect larger than {b['ci_lo']:.4f} (the 2024 CI lower bound of WF - FULL, n {b['n_rows']} rows / {b['n_games']} games)",
    }  # fmt: skip


# --------------------------------------------------------------------------- shuffle guard (rule 4)
def run_shuffle(ctx: Ctx) -> dict[str, Any]:
    """K seeds x (SHUF_FC, SHUF_WF): one permutation of the training residuals per (seed, block), identical for both arms (hashes asserted),
    scored against the real outcomes with FULL pi. Per-seed checkpoint = per-season mean CRPS of both arms; seed 0 keeps its rows."""
    gate = require_gate(ctx)
    fr = check_frame(ctx, gate)
    cfg = ctx.cfg or ctx.frame()[1]
    A_base = load(ckpt(ctx, "heads_P0C")) if gate["drift"].get("drift") else ctx.comps
    pic = load(ckpt(ctx, "a0_pic_refit")) if gate["drift"].get("drift") else ctx.pic
    pi_full = load(ckpt(ctx, "pi_FULL"))["pi"]
    y, season = A_base["row_y"], A_base["row_season"]
    for s in range(ctx.n_shuffle):
        p = ckpt(ctx, f"shuf_{s:02d}")
        if check_checkpoint(ctx, p):
            continue
        t0 = time.time()
        fc = walk(fr, cfg, "FULL", shuffle=True, seed=s, log=False)
        wf = walk(fr, cfg, "WF", shuffle=True, seed=s, log=False)
        if list(fc["label_hashes"]) != list(wf["label_hashes"]):
            raise RuntimeError(
                f"seed {s}: SHUF_FC and SHUF_WF were not fitted on the same permuted residuals"
            )
        if not (RRF.same_rows(fc, A_base) and RRF.same_rows(wf, A_base)):
            raise RuntimeError(f"seed {s}: shuffled rows differ from the A0 rows")
        cfc, cwf = crps(qi_of(fc, pic, pi_full), y), crps(qi_of(wf, pic, pi_full), y)
        rec: dict[str, Any] = {"label_hashes": np.array(fc["label_hashes"]), "seasons": np.array(SEASONS), "mean_fc": np.array([cfc[season == x].mean() for x in SEASONS]), "mean_wf": np.array([cwf[season == x].mean() for x in SEASONS])}  # fmt: skip
        if s == 0:
            rec["d_rows"] = (cwf - cfc).astype(np.float64)
        save(p, rec)
        _log(
            f"shuffle seed {s + 1}/{ctx.n_shuffle} done in {time.time() - t0:.0f}s: d = {[round(float(rec['mean_wf'][i] - rec['mean_fc'][i]), 5) for i in range(len(SEASONS))]}"
        )
    return shuffle_summary(ctx)


def guard_fires(d: np.ndarray, tol: float = SHUF_TOL) -> bool:
    """Rule 4: FIRES if mean(d) + 2 sd(d) / sqrt(K) < tol."""
    k = len(d)
    return bool(d.mean() + 2.0 * d.std(ddof=1) / np.sqrt(k) < tol)


def shuffle_summary(ctx: Ctx) -> dict[str, Any]:
    paths = [ckpt(ctx, f"shuf_{s:02d}") for s in range(ctx.n_shuffle)]
    if not any(p.exists() for p in paths):
        return {"complete": False, "run": False}
    if not all(p.exists() for p in paths):
        return {"complete": False, "run": True, "n_done": sum(p.exists() for p in paths)}
    recs = [load(p) for p in paths]
    out: dict[str, Any] = {"complete": True, "run": True, "K": ctx.n_shuffle, "per_season": {}}
    fires = False
    for i, s in enumerate(SEASONS):
        d = np.array([float(r["mean_wf"][i] - r["mean_fc"][i]) for r in recs])
        sd = float(d.std(ddof=1)) if len(d) > 1 else float("nan")
        f = guard_fires(d) if len(d) > 1 else False
        fires = fires or f
        out["per_season"][str(s)] = {
            "mean": float(d.mean()),
            "null_sd": sd,
            "stat": float(d.mean() + 2 * sd / np.sqrt(len(d))),
            "fires": f,
            "seed0": float(d[0]),
            "seed0_z": float((d[0] - d.mean()) / sd) if sd > 0 else float("nan"),
        }
    r0 = recs[0]
    if "d_rows" in r0 and ctx.comps is not None:
        base = ctx.comps if len(r0["d_rows"]) == len(ctx.comps["row_y"]) else None
        if base is not None:
            for s in SEASONS:
                m = base["row_season"] == s
                out["per_season"][str(s)]["seed0_bootstrap_ci"] = WE.delta_stats(
                    r0["d_rows"][m], base["row_gid"].astype(str)[m]
                )
    out["fires"] = fires
    return out


# --------------------------------------------------------------------------- report
def render(out: dict[str, Any]) -> str:
    v, f = out["verdict"], PR._f
    lines = [f"# {ID} results", "", f"Rule sha256 {str(out['sha256'])[:8]}. Season 2025 never loaded. **{'PASS (candidate; adversary next)' if v['pass'] else 'NOT A PASS'}**. {v['null_statement'] if not v['pass'] else ''}", "",
             "| season | arm | n rows | n games | integer CRPS / delta | 95% CI (game) | p | MDE |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in out["scores"]["rows"]:
        if "crps_int" in r:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {f(r['crps_int'], 6)} | | | |"
            )
        else:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n_rows']} | {r.get('n_games', r.get('n_dates'))} | {f(r['delta'], 5)} | [{f(r['ci_lo'], 5)}, {f(r['ci_hi'], 5)}] | {f(r['p'])} | {f(r['mde'], 5)} |"
            )
    lines += ["", "| season | slice | n | WF - FULL | CI |", "|---|---|---|---|---|"]
    for s in out["scores"]["slices"]:
        ci = "" if "ci_lo" not in s else f"[{f(s['ci_lo'], 5)}, {f(s['ci_hi'], 5)}]"
        lines.append(f"| {s['season']} | {s['slice']} | {s['n']} | {f(s.get('delta'), 5)} | {ci} |")
    lines += ["", "```json", json.dumps({"verdict": v, "shuffle": out["shuffle"], "descriptive": out["scores"]["descriptive"], "coverage": out["scores"]["coverage"], "bias": out["scores"]["bias"], "drift": out["drift"]}, indent=1, default=float), "```", ""]  # fmt: skip
    return "\n".join(lines)


def run_report(ctx: Ctx, out: dict[str, Any] | None = None) -> str:
    out = out or json.loads((ctx.out_dir / "results.json").read_text())
    md = render(out)
    (REPORT_MD if ctx.out_dir == OUT_DIR else ctx.out_dir / "report.md").write_text(md)
    return md


def run_all(ctx: Ctx) -> int:
    if not run_gate(ctx)["gate_ok"]:
        return 1
    run_arms(ctx)
    out = run_score(ctx)
    v = out["verdict"]
    if (v["rule1_select_2023"] and v["rule2_report_2024"]) or ctx.force_shuffle:
        _log(
            "primary passed: running the K-seed shuffle guard"
            if not ctx.force_shuffle
            else "dryrun: shuffle path forced"
        )
        run_shuffle(ctx)
        out = run_score(ctx)
    run_report(ctx, out)
    return 0


# --------------------------------------------------------------------------- dryrun (synthetic labels; nothing frozen read or written)
def synthetic_frame(seed: int = 0, days: int = 70, per_day: int = 60) -> pl.DataFrame:
    """Played rows over 2022-24 with every column an arm or the hash reads. pts depend on m10_pts and on the recent-role column `wl_min_last`
    (so FULL beats A0), not on the 22. Real loaders are not used; labels are synthetic."""
    import datetime as dt

    rng = np.random.default_rng(seed)
    names = set(stat_feature_names(STAT)) | set(MODEL27) | set(COMMON_FEATURES)
    rows: dict[str, list[Any]] = {
        k: [] for k in ("game_id", "player_id", "team_id", "season", "game_date")
    }
    n = 0
    for season in (2022, 2023, 2024):
        start = dt.date(season, 11, 1)
        for k in range(days):
            d = start + dt.timedelta(days=2 * k + (k // 15) * 2)
            for i in range(per_day):
                rows["game_id"].append(
                    f"00{2 if k % 9 else 4}{season % 100:02d}{k:03d}{i // 20:02d}"
                )
                rows["player_id"].append(int(1000 + (i % 60) + 100 * (season - 2022)))
                rows["team_id"].append(int(i // 10))
                rows["season"].append(season)
                rows["game_date"].append(d)
                n += 1
    df = pl.DataFrame(rows)
    cols = {c: rng.normal(size=n) for c in sorted(names) if c not in df.columns}
    cols["n_prior"] = rng.integers(5, 60, n).astype(float)
    cols["n_season"] = rng.integers(0, 40, n).astype(float)
    cols["min10"] = rng.uniform(10, 34, n)
    cols["min40"] = cols["min10"]
    cols["starter10"] = rng.uniform(0, 1, n)
    cols["m10_pts"] = 0.45 * cols["min10"]
    cols["sd10_pts"] = np.full(n, 4.0)
    cols["wl_min_last"] = cols["min10"] + rng.normal(0, 6, n)
    minutes = np.clip(cols["wl_min_last"] + rng.normal(0, 4, n), 0.5, 44)
    short = rng.random(n) < 0.08
    minutes[short] = rng.uniform(0.5, 4.5, int(short.sum()))
    pts = rng.poisson(np.clip(0.45 * minutes, 0.2, None))
    extra = {
        "minutes": minutes, "pts": pts.astype(np.int64), "starter_act": (cols["starter10"] > 0.5).astype(float), "abs_margin": np.abs(rng.normal(6, 5, n)),
        "final_margin": rng.normal(0, 12, n), "b2b": (rng.random(n) < 0.15).astype(float), "gap_days": rng.integers(1, 4, n).astype(float),
        "since_flag": rng.integers(0, 30, n).astype(float), "wl_b2b_second": (rng.random(n) < 0.15).astype(float),
        "wl_three_in_four": (rng.random(n) < 0.25).astype(float), "wl_road_trip4plus": (rng.random(n) < 0.1).astype(float),
        **{f"m10_{s}": rng.normal(3, 1, n) for s in ("reb", "ast", "fg3m")},
    }  # fmt: skip
    cols.update({k: v for k, v in extra.items() if k != "pts"})
    df = df.with_columns([pl.Series(k, v) for k, v in cols.items()]).with_columns(
        pl.Series("pts", extra["pts"])
    )
    df = df.with_columns(
        pl.when(pl.col("wl_three_in_four").cum_count().over(["team_id", "season"]) == 1)
        .then(None)
        .otherwise(pl.col("wl_miles_7d"))
        .alias("wl_miles_7d")
    )
    return df.sort(["game_date", "game_id", "player_id"])


def dry_ctx(out: Path, n_shuffle: int = 3) -> Ctx:
    """A synthetic world: the 'stored' A0 components, A0 pi, FULL cache, FULL pi and T452 B are produced by this harness's own walks on the
    synthetic frame, so every reproduction gate is exercised (and holds). Small LightGBM (n_estimators 12) for speed."""
    cfg = ContextResidualConfig(n_estimators=12, min_cal_rows=300)
    fr0 = synthetic_frame()
    fr = PB.permute_past_only(fr0, MODEL27, SEED, PL)
    t0 = time.time()
    comps = walk(fr, cfg, "P0C", log=False)
    pic = collect_pi(fr, cfg, stats=(STAT,))
    full = walk(fr, cfg, "FULL", log=False)
    full_pi = PR.align_to_stored(
        PR.walk_pi(fr, PR.CAND_COLS, cfg, log=False), pic["row_gid"], pic["row_pid"]
    )
    b = walk(fr, cfg, "B", log=False)
    b_cached = {
        "qi": WE.mixture_qi(b, pic, STAT),
        "y": b["row_y"],
        "gid": b["row_gid"].astype(str),
        "pid": b["row_pid"].astype(np.int64),
    }
    y, season = comps["row_y"], comps["row_season"]
    d = crps(qi_of(full, pic, full_pi), y) - crps(WE.mixture_qi(comps, pic, STAT), y)
    t467 = {s: float(d[season == s].mean()) for s in SEASONS}
    pin, _ = a0_frame_sha(fr, comps["row_gid"], comps["row_pid"])
    _log(
        f"dryrun world built in {time.time() - t0:.0f}s: {fr.height} rows, A0 rows {len(y)}, synthetic FULL - A0 {t467}"
    )
    return Ctx(out_dir=out, cache=out / "cache", cfg=cfg, frame_fn=lambda: (fr, cfg, {}), comps=comps, pic=pic, full_heads=full, full_pi=full_pi,
               b_cached=b_cached, t467=t467, pin=pin, pin_rows=len(y), n_shuffle=n_shuffle, real_plants=False, force_shuffle=True, sha="dryrun")  # fmt: skip


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "cmd", choices=["pins", "gate", "arms", "score", "shuffle", "report", "run", "dryrun"]
    )
    ap.add_argument("--sha", default=None)
    ap.add_argument("--out", default=None, help="dryrun only: scratch output dir")
    ap.add_argument("--k", type=int, default=3, help="dryrun only: shuffle seeds")
    a = ap.parse_args(argv)
    if a.cmd == "dryrun":
        if not a.out:
            raise SystemExit("--out <scratch dir> is required for dryrun")
        out = Path(a.out)
        if out.resolve() in (OUT_DIR.resolve(), CACHE.resolve()):
            raise SystemExit("dryrun never writes the real output dirs")
        ctx = dry_ctx(out, a.k)
        rc = run_all(ctx)
        res = json.loads((out / "results.json").read_text())
        print(
            json.dumps(
                {
                    "rc": rc,
                    "verdict": res["verdict"],
                    "shuffle": res["shuffle"],
                    "arm_saw_it": res["scores"]["descriptive"]["arm_saw_it"],
                },
                indent=1,
                default=float,
            )
        )
        return rc
    if a.cmd == "pins":
        ctx = real_ctx(None)
        fr, _, _ = ctx.frame()
        assert ctx.comps is not None
        sha, found = a0_frame_sha(fr, ctx.comps["row_gid"], ctx.comps["row_pid"])
        print(
            json.dumps(
                {
                    "frame_sha256": sha,
                    "pin": FRAME_PIN,
                    "match": sha == FRAME_PIN,
                    "a0_rows": len(ctx.comps["row_gid"]),
                    "found": found,
                    "max_season": int(fr["season"].max()),
                },
                indent=1,
            )
        )
        return 0 if sha == FRAME_PIN else 1
    sha = require_frozen(a.sha)
    ctx = real_ctx(sha)
    if a.cmd == "report":
        run_report(ctx)
        return 0
    with WE.heavy():
        if a.cmd == "gate":
            return 0 if run_gate(ctx)["gate_ok"] else 1
        if a.cmd == "arms":
            run_arms(ctx)
            return 0
        if a.cmd == "score":
            run_report(ctx, run_score(ctx))
            return 0
        if a.cmd == "shuffle":
            run_shuffle(ctx)
            run_report(ctx, run_score(ctx))
            return 0
        return run_all(ctx)


if __name__ == "__main__":
    import research.eval.workload_pts_v2_eval as _E

    raise SystemExit(_E.main())
