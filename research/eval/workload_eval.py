# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment"
"""WORKLOAD evaluation harness (rule: docs/prereg/WORKLOAD.md, FROZEN sha256 261fabce, commit 282e2cc).

    M="-m research.eval.workload_eval"
    .venv/bin/python -W ignore $M gate                    # frozen minimum-data checks; no arm is fitted
    .venv/bin/python -W ignore $M arms [--cell P|M|S]     # fit + cache every arm (heavy.lock)
    .venv/bin/python -W ignore $M report                  # score cached arms, BH, pass rule, md (light)
    .venv/bin/python -W ignore $M attack                  # shuffled-label + family-alone arms for passing cells (heavy.lock)
    .venv/bin/python -W ignore $M run                     # gate -> arms -> report -> attack -> report, one lock

Every stage refuses unless the sha256 of everything above ``=== RESULTS BELOW ===`` in the doc equals
``FROZEN_FULL`` (and ``--sha``, when given, equals it too). ``nba.duckdb`` is read-only, ``season <= 2024`` in
every query and asserted; season 2025 is never loaded. Heavy stages take ``data/ops/heavy.lock`` (mkdir, a pid
file inside, retry 120 s, removed only by the process that created it).

Cells (m = 6, BH per season): P = P(play) log loss (candidate W vs comparator C), M = minutes CRPS (MW vs M2),
S = props integer CRPS for pts/reb/ast/fg3m (B vs A0, A0 = production + lower-tail mixture from the stored
components). Reading choices where the rule text is ambiguous are listed in ``AMBIGUITIES`` and are printed into
``results.json`` and the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    month_blocks,
)
from nba.eval.lower_tail_eval import ROW_COVARIATES, arm_grid
from nba.props.context_residual import (
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from research.workload import asof as W

DOC = Path("docs/prereg/WORKLOAD.md")
FROZEN_FULL = "261fabceabd4956808f962d5e5901d4f009499cfcb9085218ae5e3ea057b81c7"
OUT_DIR = Path("reports/prereg_workload")
REPORT_MD = Path("reports/prereg_workload.md")
CACHE = Path("data/ops/workload_cache")
LOCK = Path("data/ops/heavy.lock")
LT_DIR = Path("reports/lower_tail")
MV2_DIR = Path("reports/minutes_v2")
TEAMS_YAML = "configs/team_markets.yaml"
DB = "nba.duckdb"
PAIRS_DIR = Path("data/ops/model_clv_pairs")

STATS = ("pts", "reb", "ast", "fg3m")
CELLS = ("P", "M", *STATS)
SEASONS = (2023, 2024)
N_BOOT = 2000
SEED = 0
KEYS = ["game_id", "player_id"]
MIN_SLICE_N = 300
COV_TOL = 0.03
BIAS_TOL = 0.05
MAX_BIAS = 0.5
ECE_TOL = 0.005
CARRY = 0.8
NULL_GAP_PP = 2.0
PLACEBO_MULT = 3.0
#: floors and margins, frozen in the rule (metric units: CRPS, minutes, log loss)
FLOOR = {"P": -0.005, "M": -0.10, **{s: -0.005 for s in STATS}}
PLACEBO_MARGIN = {"P": -0.003, "M": -0.05, **{s: -0.003 for s in STATS}}
SLICE_TOL = {"P": 0.01, "M": 0.05, **{s: 0.01 for s in STATS}}
UNIT = {"P": "log loss", "M": "minutes CRPS", **{s: "integer CRPS" for s in STATS}}

LOAD_COLS = W.MODEL_FAMILIES["LOAD"]
SCHED_COLS = W.MODEL_FAMILIES["SCHEDULE"]
TRAVEL_COLS = W.MODEL_FAMILIES["TRAVEL"]
TRACK_COLS = tuple(W.PREFIX + c for c in W.LOAD_TRACK_COLS)
ALL_WL = W.MODEL_COLS
PL_PREFIX = "pl_"

AMBIGUITIES: list[dict[str, str]] = [
    {
        "text": "Cells S: 'A0 = production + lower-tail mixture ... B = A0 + 27 columns appended through stat_feature_names'",
        "reading": "A0 is the stored-components mixture arm (3.140236 / 3.176840 for pts). B, the placebo, the knockouts and the no-tracking arm are the production model with extra columns, passed through the SAME lower-tail mixture (A0's stored row mixing weights `row_pi` and per-block quantile weights `wq_*`), so every S arm is on the mixture support. Raw (non-mixture) production is not an arm.",
    },
    {
        "text": "Cell P comparator C = 'P1 (M2-p) + the report-tag-by-strength and morning/evening flags of PPLAY_CONTEXT (`build_terms`, without the b2b-habit terms)'",
        "reading": "C = BASE_FEATURES + TERM_TAG['T-60'] (terc, tag60_code, q60, d60, q60_x_terc, d60_x_terc) + TERM_REPORT (on_morning_report, on_evening_report). The b2b-habit terms team_b2b_sit, star and b2b_star are NOT in C or W.",
    },
    {
        "text": "Placebo: 'the arm beats its family placebo by a point <= -0.003 (props, P) / -0.05 min (minutes)'",
        "reading": "One placebo arm (all 27 columns permuted: LOAD within player-season, SCHEDULE and TRAVEL within team-season). The condition is judged on the REPORT season 2024 (B - PL <= margin); 2023 is reported. Per-family placebos are not run.",
    },
    {
        "text": "Pass rule 4: 'if removing one family removes >= 80% of the gain, or one family alone keeps >= 80%'",
        "reading": "Gains are 2024 mean differences vs the base. Removal arms (KO_LOAD, KO_SCHEDULE, KO_TRAVEL) run for every cell in `arms`; the 'family alone' arms (AL_*) run in `attack` for cells that pass rules 1-3 only. Flag 'one family carries it' = any KO leaves <= 20% of the full gain OR any AL keeps >= 80%.",
    },
    {
        "text": "'LOAD-without-tracking (LOAD_MIN only)' and 'A tracking claim needs LOAD(full) - LOAD_MIN <= floor/2 on the same rows'",
        "reading": "NT = the full candidate minus the 7 LOAD_TRACK columns (LOAD_MIN, SCHEDULE and TRAVEL stay). Tracking claim holds when 2024 mean(B - NT) <= floor/2 for the cell. Descriptive unless a cell otherwise passes.",
    },
    {
        "text": "Pass rule 3: 'the arm does not beat its own oracle bound (A3 for props, realised minutes for M)'",
        "reading": "Props: B's 2024 mean CRPS must exceed the stored A3 (production + realised minutes, reports/minutes_v2/props_<stat>_A3.npz) mean on the same season. P and M: not applicable (an oracle for realised minutes is zero CRPS; for P(play) the realised outcome); recorded as n/a, not as a pass.",
    },
    {
        "text": "'shuffled-label arm gives an effect >= -MDE'",
        "reading": "Labels permuted within the training set of each month block (props: residuals; P: played flag; M: minutes). MDE is the half-width of the cell's own 2024 B-vs-base CI. Shuffled arm passes if its 2024 mean effect >= -MDE.",
    },
    {
        "text": "Star slice 'as-of top-3 team minutes per appearance over 10 games'",
        "reading": "Slice only (never a feature): the three highest as-of `min10` (cell P: avg minutes given played) among the scored rows of the team-game. Starter = as-of start rate >= 0.5 (starter10; cell P starter_rate_prior).",
    },
    {
        "text": "M2 reproduction tolerance (the rule requires A0 to reproduce; M2 and C are new fits)",
        "reading": "M2 (refit here) is compared with the stored reports/minutes_v2/minutes_scores.npz M2 CRPS on the rows both have (stored 2024 has 27,583 rows, the data now 27,701, so the comparison is on the key intersection, >= 99% of rows required): 2023 within 1e-6 min, 2024 within 0.005 min (PPLAY_CONTEXT's frozen amendment for the 12-game data clean-up). A failure stops `arms` before any candidate arm is fitted. C has no stored reference and is not reproduced.",
    },
    {
        "text": "Select 2023, report 2024 and BH",
        "reading": "Rule 1 for 2023 has no BH requirement; for 2024 BH p <= 0.05 over the six primary cells {P, M, pts, reb, ast, fg3m} (candidate vs base), computed within each season. Two-sided clustered-bootstrap p (2000 draws, seed 0, cluster = game_id).",
    },
    {
        "text": "Planted tests and missingness audit 'on the frozen frames'",
        "reading": "`gate` audits the 55,320 A0 rows (props/minutes rows) and the cell-P rows (including DNP as its own bucket) with threshold max(2 pp, 3 x placebo); planted same-game / planted-future tests run on the REAL played table with non-inert controls. Any failure stops everything (kill criterion).",
    },
    {
        "text": "Decision score D2",
        "reading": "Descriptive only: DraftKings T-60 legs (nba.markets.decision_scoring) re-priced with each S cell's A0 and B integer grid. Errors are recorded, never fatal.",
    },
    {
        "text": "D3 oracle-minutes pair for pts and reb",
        "reading": "A3 = A0 features + realised minutes (`minutes_oracle`) and A3W = A3 + 27 columns, both through the mixture; the verdict line is 'effect runs through minutes' if B beats A0 but A3W - A3 > floor/2, else flagged.",
    },
]


# --------------------------------------------------------------------------- freeze, lock, small helpers
def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def frozen_sha(doc: Path = DOC) -> str:
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def require_frozen(sha: str | None = None, doc: Path = DOC, expected: str = FROZEN_FULL) -> str:
    """Refuse unless the doc text above the marker hashes to the frozen value (and ``sha``, if given)."""
    got = frozen_sha(doc)
    if got != expected:
        raise RuntimeError(f"{doc} is not the frozen rule: sha256 {got[:8]} != {expected[:8]}")
    if sha is not None and sha != expected:
        raise RuntimeError(f"--sha {sha[:8]} does not match the frozen rule {expected[:8]}")
    return got


class WorkloadLock:
    """data/ops/heavy.lock as a directory made with mkdir, a pid file inside, removed only by its creator."""

    def __init__(
        self,
        path: Path = LOCK,
        retry_s: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
        max_wait_s: float | None = None,
    ) -> None:
        self.path, self.retry_s, self.sleep, self.max_wait_s = path, retry_s, sleep, max_wait_s
        self.owned = False

    def __enter__(self) -> WorkloadLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        waited = 0.0
        while True:
            try:
                self.path.mkdir()
                break
            except FileExistsError:
                if self.max_wait_s is not None and waited >= self.max_wait_s:
                    raise TimeoutError(f"{self.path} still held") from None
                print(f"heavy.lock held; retry in {self.retry_s:.0f}s", flush=True)
                self.sleep(self.retry_s)
                waited += self.retry_s
        self.owned = True
        (self.path / "pid").write_text(str(os.getpid()))
        return self

    def release(self) -> None:
        if not self.owned:
            return
        pidf = self.path / "pid"
        if pidf.exists() and pidf.read_text().strip() == str(os.getpid()):
            shutil.rmtree(self.path, ignore_errors=True)
        self.owned = False

    def __exit__(self, *exc: object) -> None:
        self.release()


@contextmanager
def heavy() -> Iterator[WorkloadLock]:
    lock = WorkloadLock()

    def _bye(*_: Any) -> None:
        lock.release()
        raise SystemExit(143)

    with lock:
        old = {s: signal.signal(s, _bye) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            yield lock
        finally:
            for s, h in old.items():
                signal.signal(s, h)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))


def assert_seasons(df: pl.DataFrame, col: str = "season") -> None:
    if int(df[col].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 is the frozen holdout and must not be loaded")


# --------------------------------------------------------------------------- workload columns
def load_sources(db_path: str = DB) -> dict[str, Any]:
    """Games (with real tips), played rows with tracking, the team table and the schedule/travel columns."""
    from research.eval import workload_gates as G
    from research.features.game_location_context import load_team_markets

    con = duckdb.connect(db_path, read_only=True)
    try:
        games = G.load_games(con).filter(pl.col("tip_utc").is_not_null())
        played = G.load_played(con)
    finally:
        con.close()
    assert_seasons(games)
    assert_seasons(played)
    teams = load_team_markets(TEAMS_YAML)
    sched = W.team_schedule_features(games, teams)
    return {"games": games, "played": played, "teams": teams, "sched": sched}


def attach_workload(rows: pl.DataFrame, src: dict[str, Any]) -> pl.DataFrame:
    """Left-join the 27 model columns (plus the two slice-only ones) onto rows(game_id, player_id, team_id, game_date)."""
    keep = ["game_id", "player_id", "team_id", "game_date"]
    fr = W.build_workload_frame(
        rows.select(keep).with_columns(pl.col("player_id").cast(pl.Int64)),
        src["played"],
        src["sched"],
    )
    wl = fr.select(
        "game_id", "player_id", *[W.PREFIX + c for c in W.ALL_COLS if (W.PREFIX + c) in fr.columns]
    )
    out = rows.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl, on=KEYS, how="left", suffix="_wl"
    )
    if out.height != rows.height:
        raise ValueError("workload join changed the row count")
    return out


def make_placebo(frame: pl.DataFrame, seed: int = SEED) -> pl.DataFrame:
    """Add ``pl_wl_*``: LOAD permuted within player-season, SCHEDULE and TRAVEL within team-season."""
    pf = W.permute_within_player_season(frame, tuple(LOAD_COLS), seed)
    tf = W.permute_within_team_season(frame, tuple(SCHED_COLS + TRAVEL_COLS), seed)
    cols = [pl.Series(PL_PREFIX + c, pf[c].to_numpy()) for c in LOAD_COLS]
    cols += [pl.Series(PL_PREFIX + c, tf[c].to_numpy()) for c in SCHED_COLS + TRAVEL_COLS]
    return frame.with_columns(cols)


def arm_wl(arm: str) -> list[str]:
    """Workload columns carried by each arm (the base arm carries none)."""
    L, S, T, TR = list(LOAD_COLS), list(SCHED_COLS), list(TRAVEL_COLS), set(TRACK_COLS)
    table: dict[str, list[str]] = {
        "base": [],
        "B": list(ALL_WL),
        "PL": [PL_PREFIX + c for c in ALL_WL],
        "KO_LOAD": S + T,
        "KO_SCHEDULE": L + T,
        "KO_TRAVEL": L + S,
        "NT": [c for c in ALL_WL if c not in TR],
        "AL_LOAD": L,
        "AL_SCHEDULE": S,
        "AL_TRAVEL": T,
    }
    return table[arm]


PRIMARY_ARMS = ("B", "PL", "KO_LOAD", "KO_SCHEDULE", "KO_TRAVEL", "NT")
ATTACK_ARMS = ("AL_LOAD", "AL_SCHEDULE", "AL_TRAVEL")


# --------------------------------------------------------------------------- scoring helpers
def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, 1e-6, 1.0 - 1e-6)
    return np.asarray(-np.where(ev > 0.5, np.log(pc), np.log(1.0 - pc)))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(float(p[m].mean()) - float(y[m].mean()))
    return tot


def _ci(
    delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Percentile CI of the mean of ``delta`` clustered by game (multinomial resample of games)."""
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    s = np.bincount(inv, weights=delta, minlength=g)
    c = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(g, np.full(g, 1.0 / g), size=n_boot).astype(float)
    means = (w @ s) / np.maximum(w @ c, 1.0)
    return {
        "point": float(delta.mean()),
        "lo": float(np.percentile(means, 2.5)),
        "hi": float(np.percentile(means, 97.5)),
    }


def delta_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    ci = _ci(delta, gid)
    return {
        "delta": ci["point"],
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "p": float(boot_p_value(delta, gid, N_BOOT)),
        "mde": (ci["hi"] - ci["lo"]) / 2.0,
    }


def carries(gain_full: float, gain_drop: float, gain_only: float | None) -> bool:
    """Gains are metric differences (negative = better). 'Carried by one family': dropping it leaves <= 20% of the
    full gain, or it alone keeps >= 80% (``gain_only`` None = not run)."""
    if gain_full >= 0:
        return False
    if gain_drop / gain_full <= 1.0 - CARRY:
        return True
    return bool(gain_only is not None and gain_only / gain_full >= CARRY)


# --------------------------------------------------------------------------- cells S (props)
def collect_components(
    frame: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    shuffle: bool = False,
    seed: int = SEED,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, np.ndarray]:
    """lower_tail_eval.collect_stat with an explicit feature list (and optional shuffled training residuals)."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rng = np.random.default_rng(seed)
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk", *ROW_COVARIATES)
    }
    cal: dict[str, list[np.ndarray]] = {
        k: [] for k in ("y", "c", "s", "z", "blk", "minutes", "min10", "m")
    }
    for b, (start, end) in enumerate(month_blocks(test_all)):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        if shuffle:
            train = train.with_columns(
                pl.Series("resid", rng.permutation(train["resid"].to_numpy()))
            )
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        rows["gid"].append(block["game_id"].to_numpy())
        rows["pid"].append(block["player_id"].to_numpy())
        rows["season"].append(block["season"].to_numpy())
        rows["y"].append(block["y"].to_numpy())
        rows["c"].append(c)
        rows["s"].append(s)
        rows["blk"].append(np.full(n, b))
        for k in ROW_COVARIATES:
            rows[k].append(block[k].cast(pl.Float64).to_numpy())
        _, cal_df = split_calibration(train, cfg)
        assert model.mean_head is not None and model.z_sorted is not None
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        cal["y"].append(cal_df["y"].to_numpy())
        cal["c"].append(cal_df["m"].to_numpy() + mu)
        cal["s"].append(sc)
        cal["z"].append((cal_df["resid"].to_numpy() - mu) / sc)
        cal["blk"].append(np.full(cal_df.height, b))
        for k in ("minutes", "min10", "m"):
            cal[k].append(cal_df[k].cast(pl.Float64).to_numpy())
        print(f"  {stat} block {start} done ({time.time() - t0:.0f}s, n={n})", flush=True)
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


def mixture_qi(comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], stat: str) -> np.ndarray:
    """Integer-support quantiles of the lower-tail mixture arm (A0's stored pi / weights), int16."""
    if not (
        np.array_equal(comp["row_gid"], pic["row_gid"])
        and np.array_equal(comp["row_pid"], pic["row_pid"])
    ):
        raise RuntimeError("arm rows are not aligned with the stored mixture components")
    return to_integer_support(arm_grid("mixture", comp, pic, stat)).astype(np.int16)


def load_pic() -> dict[str, np.ndarray]:
    return dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))


def cache_path(cell: str, arm: str) -> Path:
    return CACHE / f"{cell}__{arm}.npz"


def a0_arm(stat: str, pic: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    from nba.eval.lower_tail_eval import load_components

    comp = load_components(stat, LT_DIR)
    if int(comp["row_season"].max()) > MAX_SEASON:
        raise RuntimeError("season > 2024 in components")
    return {
        "qi": mixture_qi(comp, pic, stat),
        "y": comp["row_y"],
        "gid": comp["row_gid"].astype(str),
        "pid": comp["row_pid"].astype(np.int64),
        "season": comp["row_season"],
    }


def run_props_arm(
    frame: pl.DataFrame,
    stat: str,
    arm: str,
    cfg: ContextResidualConfig,
    pic: dict[str, np.ndarray],
    shuffle: bool = False,
    extra: tuple[str, ...] = (),
    tag: str | None = None,
) -> Path:
    p = cache_path(stat, tag or arm)
    if p.exists():
        return p
    t0 = time.time()
    names = stat_feature_names(stat, extra=extra) + arm_wl(arm)
    comp = collect_components(frame, stat, cfg, names, shuffle=shuffle)
    qi = mixture_qi(comp, pic, stat)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(
        p,
        qi=qi,
        y=comp["row_y"],
        gid=comp["row_gid"].astype(str),
        pid=comp["row_pid"].astype(np.int64),
        season=comp["row_season"],
    )
    _log(f"props {stat} {tag or arm} done in {time.time() - t0:.0f}s")
    return p


# --------------------------------------------------------------------------- cell P (P(play))
def p_arms() -> dict[str, list[str]]:
    from research.eval import pplay_context_eval as PC

    c = [*PC.BASE_FEATURES, *PC.TERM_TAG["T-60"], *PC.TERM_REPORT]
    out = {"base": c}
    for a in (*PRIMARY_ARMS, *ATTACK_ARMS):
        out[a] = [*c, *arm_wl(a)]
    return out


def p_walk(
    frame: pl.DataFrame,
    arms: dict[str, list[str]],
    cfg: ContextResidualConfig,
    shuffle: frozenset[str] = frozenset(),
    seed: int = SEED,
    ckpt: Path | None = None,
    min_train: int = 1500,
    n_estimators: int | None = None,
) -> dict[str, np.ndarray]:
    """Month-block walk-forward for P(play): train strictly before the block, 2022 is warm-up. A name in
    ``shuffle`` trains on permuted labels (within the block's training set)."""
    assert_seasons(frame)
    test_all = frame.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    parts: list[dict[str, np.ndarray]] = []
    rng = np.random.default_rng(seed)
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files})
            _log(f"P block {bi + 1}/{len(blocks)} {start} from checkpoint")
            continue
        block = frame.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train = frame.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < min_train:
            continue
        ytr = train["played"].to_numpy().astype(int)
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy().astype(str),
            "pid": block["player_id"].to_numpy().astype(np.int64),
            "season": block["season"].to_numpy(),
            "y": block["played"].to_numpy(),
        }
        params = _params(cfg, "binary")
        if n_estimators is not None:
            params["n_estimators"] = n_estimators
        for arm, names in arms.items():
            y_fit = rng.permutation(ytr) if arm in shuffle else ytr
            clf = lgb.LGBMClassifier(**params).fit(_matrix(train, names), y_fit)  # type: ignore[arg-type]
            part[f"p_{arm}"] = np.asarray(clf.predict_proba(_matrix(block, names)))[:, 1]
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, **part)
        parts.append(part)
        _log(f"P block {bi + 1}/{len(blocks)} {start} n_test={block.height} train={train.height}")
    if not parts:
        raise ValueError("no month block had enough training rows")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


# --------------------------------------------------------------------------- cell M (minutes)
def m_arm_names() -> dict[str, list[str]]:
    from research.props.minutes_v2 import V2_MINUTES_FEATURES

    out = {"base": list(V2_MINUTES_FEATURES)}
    for a in (*PRIMARY_ARMS, *ATTACK_ARMS):
        out[a] = [*V2_MINUTES_FEATURES, *arm_wl(a)]
    return out


def m_walk(
    sf: pl.DataFrame,
    arms: dict[str, list[str]],
    cfg: ContextResidualConfig,
    shuffle: frozenset[str] = frozenset(),
    with_baselines: bool = True,
    seed: int = SEED,
    ckpt: Path | None = None,
) -> dict[str, np.ndarray]:
    """Month-block walk-forward of the minutes-given-plays mixture (MinutesV2Model) per arm; per-row scores.
    ``sf`` carries p_mu / p_sigma (production) when ``with_baselines``."""
    from research.eval.minutes_v2_eval import score_minutes
    from research.props.minutes_v2 import (
        MIN_TRAIN,
        MinutesV2Model,
        recency_ratio_quantiles,
        trunc_normal_quantiles,
    )

    assert_seasons(sf)
    test_all = sf.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    rng = np.random.default_rng(seed)
    parts: list[dict[str, np.ndarray]] = []
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files})
            _log(f"M block {bi + 1}/{len(blocks)} {start} from checkpoint")
            continue
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train = sf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        y = block["minutes"].cast(pl.Float64).to_numpy()
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy().astype(str),
            "pid": block["player_id"].to_numpy().astype(np.int64),
            "season": block["season"].to_numpy(),
            "y": y,
        }
        qs: dict[str, np.ndarray] = {}
        if with_baselines:
            _, cal_df = split_calibration(train, cfg)
            qs["P"] = trunc_normal_quantiles(block["p_mu"].to_numpy(), block["p_sigma"].to_numpy())
            qs["R"] = recency_ratio_quantiles(
                cal_df["minutes"].to_numpy(), cal_df["min10"].to_numpy(), block["min10"].to_numpy()
            )
        for arm, names in arms.items():
            tr = train
            if arm in shuffle:
                tr = train.with_columns(
                    pl.Series("minutes", rng.permutation(train["minutes"].to_numpy()))
                )
            qs[arm] = MinutesV2Model(names, "mixture", cfg).fit(tr).quantiles(block)
        for arm, q in qs.items():
            for k, v in score_minutes(q, y).items():
                if k in ("crps", "pit", "mean", "q10", "q90"):
                    part[f"{arm}_{k}"] = np.asarray(v)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, **part)
        parts.append(part)
        _log(f"M block {bi + 1}/{len(blocks)} {start} n_test={block.height}")
    if not parts:
        raise ValueError("no month block had enough training rows")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


# --------------------------------------------------------------------------- frames per cell
META_NEED = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "game_date",
    "starter10",
    "min10",
    "n_prior",
    "team_game_no",
    "minutes",
    "wl_b2b_second",
    "wl_three_in_four",
    "wl_road_trip4plus",
    "wl_tz_cross_hr",
    "wl_miles_from_prev",
    "wl_early_tip",
    "wl_speed_decline",
)


def save_meta(kind: str, frame: pl.DataFrame, rename: dict[str, str] | None = None) -> None:
    d = frame.rename(rename or {})
    keep = [c for c in META_NEED if c in d.columns]
    CACHE.mkdir(parents=True, exist_ok=True)
    d.select(keep).write_parquet(CACHE / f"meta_{kind}.parquet")


def load_props_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    from research.eval import four_factors_eval as F

    study = F.load_study(DB)
    src = load_sources()
    feats = study["feats"]
    rows = feats.select("game_id", "player_id", "team_id", "game_date")
    wl = attach_workload(rows, src)
    fr = feats.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl.drop("team_id", "game_date"), on=KEYS, how="left"
    )
    if fr.height != feats.height:
        raise ValueError("workload join changed the feature rows")
    fr = make_placebo(fr)
    assert_seasons(fr)
    return fr, study["cfg"], src


def load_p_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    from research.eval import pplay_context_eval as PC

    prep = PC.prepare(DB)
    frame = prep["frame"]
    src = load_sources()
    wl = attach_workload(frame.select("game_id", "player_id", "team_id", "game_date"), src)
    fr = frame.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl.drop("team_id", "game_date"), on=KEYS, how="left"
    )
    if fr.height != frame.height:
        raise ValueError("workload join changed the P(play) rows")
    return make_placebo(fr), PC._load_cfg(), src


def load_m_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    from research.eval import minutes_v2_eval as MV

    world = MV.load_world()
    try:
        frame = MV.build_frame(world)
        prod = MV.production_minutes_table(world["sc"])
    finally:
        world["sc"].close()
    src = load_sources()
    wl = attach_workload(frame.select("game_id", "player_id", "team_id", "game_date"), src)
    fr = frame.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl.drop("team_id", "game_date"), on=KEYS, how="left"
    )
    sf = stat_frame(fr, "pts").sort(["game_date", "game_id", "player_id"])
    sf = sf.join(prod.with_columns(pl.col("player_id").cast(pl.Int64)), on=KEYS, how="left")
    return make_placebo(sf), world["cfg"], src


# --------------------------------------------------------------------------- gate
def _check(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def _null_expr(d: pl.DataFrame, c: str) -> pl.Expr:
    if d[c].dtype in (pl.Float32, pl.Float64):
        return pl.col(c).is_null() | pl.col(c).is_nan()
    return pl.col(c).is_null()


def audit_nulls(
    frame: pl.DataFrame, cols: list[str], bucket: pl.Expr, seed: int = SEED
) -> dict[str, Any]:
    """NULL/NaN rate of every column by realised-minutes bucket; gap = max - min (pp); threshold max(2 pp, 3 x the
    same statistic after permuting the bucket label)."""
    d = frame.with_columns(bucket.alias("_b"))
    rng = np.random.default_rng(seed)
    d = d.with_columns(pl.Series("_p", d["_b"].to_numpy()[rng.permutation(d.height)]))

    def gap(lab: str) -> dict[str, float]:
        r = d.group_by(lab).agg([_null_expr(d, c).mean().alias(c) for c in cols])
        return {c: float(np.ptp(r[c].to_numpy())) * 100.0 for c in cols}

    real, plc = gap("_b"), gap("_p")
    thr = max(NULL_GAP_PP, PLACEBO_MULT * max(plc.values()))
    worst = max(real, key=lambda k: real[k])
    return {
        "max_gap_pp": real[worst],
        "worst_col": worst,
        "placebo_max_gap_pp": max(plc.values()),
        "threshold_pp": thr,
        "ok": real[worst] <= thr,
        "n_rows": d.height,
    }


def minutes_bucket(col: str = "minutes", dnp: bool = False) -> pl.Expr:
    m = pl.col(col)
    e = (
        pl.when(m < 5)
        .then(pl.lit("<5"))
        .when(m < 10)
        .then(pl.lit("5-10"))
        .when(m < 20)
        .then(pl.lit("10-20"))
        .otherwise(pl.lit(">20"))
    )
    if dnp:
        e = pl.when(m.is_null() | (m <= 0)).then(pl.lit("DNP")).otherwise(e)
    return e


def planted_tests(src: dict[str, Any], rows: pl.DataFrame) -> list[dict[str, Any]]:
    """Same-game and future edits on the REAL played table, with non-inert controls."""
    played = src["played"]
    games = src["games"].filter(pl.col("season") == 2024).sort("game_date")
    day = games["game_date"][games.height // 2]
    tgt = rows.filter(pl.col("season") == 2024).select("game_id", "player_id", "game_date")
    base = W.player_load_features(played, tgt.select("game_id", "player_id", "game_date"))

    def edited(mask: pl.Expr) -> pl.DataFrame:
        p2 = played.with_columns(
            pl.when(mask).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
            pl.when(mask).then(99.0).otherwise(pl.col("distance")).alias("distance"),
            pl.when(mask).then(9.0).otherwise(pl.col("speed")).alias("speed"),
        )
        return W.player_load_features(p2, tgt)

    def same(a: pl.DataFrame, b: pl.DataFrame, keys: pl.DataFrame) -> bool:
        x = a.join(keys, on=KEYS).sort(KEYS)
        y = b.join(keys, on=KEYS).sort(KEYS)
        native = [c for c in x.columns if c not in KEYS]
        return all(
            np.allclose(
                x[c].to_numpy().astype(float), y[c].to_numpy().astype(float), equal_nan=True
            )
            for c in native
        )

    out = []
    same_day = edited(pl.col("game_date") == day)  # edits tonight's own games
    k_day = tgt.filter(pl.col("game_date") == day).select(KEYS)
    k_after = tgt.filter(pl.col("game_date") > day).select(KEYS)
    out.append(
        _check(
            "planted same-game: rows of the edited day identical",
            k_day.height,
            "identical",
            same(base, same_day, k_day),
        )
    )
    out.append(
        _check(
            "control: rows after the edited day change",
            k_after.height,
            "not identical",
            not same(base, same_day, k_after),
        )
    )
    fut = edited(pl.col("game_date") >= day)
    k_early = tgt.filter(pl.col("game_date") <= day).select(KEYS)
    out.append(
        _check(
            "planted future: rows dated on or before the cutoff identical",
            k_early.height,
            "identical",
            same(base, fut, k_early),
        )
    )
    # schedule: dropping every game after the day cannot change rows dated up to the day
    full = src["sched"]
    cut = W.team_schedule_features(src["games"].filter(pl.col("game_date") <= day), src["teams"])
    gids = set(src["games"].filter(pl.col("game_date") <= day)["game_id"].to_list())
    f2 = full.filter(pl.col("game_id").is_in(list(gids))).sort(["game_id", "team_id"])
    c2 = cut.sort(["game_id", "team_id"])
    out.append(
        _check(
            "planted future (schedule): rows up to the cutoff identical",
            f2.height,
            "identical",
            f2.equals(c2),
        )
    )
    return out


def run_gate(sha: str | None = None, draft_ok: bool = False) -> dict[str, Any]:
    t0 = time.time()
    if not draft_ok:
        require_frozen(sha)
    from research.eval import workload_gates as G

    tests: list[dict[str, Any]] = []
    con = G.connect()
    try:
        trk = G.gate_tracking(con)
        arena = G.gate_arena(con)
    finally:
        con.close()
    tests.append(
        _check(
            "G1 tracking coverage",
            trk["has_tracking_ge10min_by_season"],
            ">= 0.95 of >=10-min rows",
            trk["pass"],
        )
    )
    tests.append(
        _check(
            "G3 arena/tz/tips/schedule",
            arena["worst_reference_rel_err"],
            "teams 30/30, 0 missing tips, ref err < 5%",
            arena["pass"],
        )
    )
    a0 = G.gate_a0()
    tests.append(
        _check(
            "G4 A0 reproduction from stored components",
            a0["max_abs_diff"],
            "<= 1e-6, n equal",
            a0["pass"],
        )
    )
    # frames
    fr, _, src = load_props_frame()
    a0_rows = fr.filter(pl.col("season").is_in(list(SEASONS)) & (pl.col("n_prior") >= 5))
    aud_a0 = audit_nulls(a0_rows, list(ALL_WL), minutes_bucket())
    tests.append(
        _check(
            "G6 missingness asymmetry, props/minutes rows (pp)",
            aud_a0["max_gap_pp"],
            f"<= {aud_a0['threshold_pp']:.2f}",
            aud_a0["ok"],
        )
    )
    tests += planted_tests(src, fr.select("game_id", "player_id", "season", "game_date"))
    del fr, a0_rows
    pf, _, _ = load_p_frame()
    p_rows = pf.filter(pl.col("season").is_in(list(SEASONS)))
    aud_p = audit_nulls(p_rows, list(ALL_WL), minutes_bucket("min_real", dnp=True))
    tests.append(
        _check(
            "G6 missingness asymmetry, P(play) rows incl. DNP (pp)",
            aud_p["max_gap_pp"],
            f"<= {aud_p['threshold_pp']:.2f}",
            aud_p["ok"],
        )
    )
    gate = {
        "tests": tests,
        "audit_props": aud_a0,
        "audit_pplay": aud_p,
        "tracking": trk,
        "arena": {
            k: arena[k]
            for k in ("team_games", "team_games_with_prior_in_season", "worst_reference_rel_err")
        },
        "a0": {"max_abs_diff": a0["max_abs_diff"], "n_match": a0["n_match"]},
        "gate_ok": all(t["ok"] for t in tests),
        "sha256": FROZEN_FULL,
        "seconds": time.time() - t0,
    }
    write_json(OUT_DIR / "gate.json", gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return gate


def require_gate() -> None:
    p = OUT_DIR / "gate.json"
    if not p.exists():
        raise RuntimeError("no gate.json: run the gate stage first")
    g = json.loads(p.read_text())
    if not g.get("gate_ok") or g.get("sha256") != FROZEN_FULL:
        raise RuntimeError("the frozen gate has not passed: stop (kill criterion)")


# --------------------------------------------------------------------------- stage: arms
def stage_props(cells: tuple[str, ...] = STATS) -> None:
    fr, cfg, _ = load_props_frame()
    save_meta("props", fr)
    pic = load_pic()
    for stat in cells:
        p0 = cache_path(stat, "base")
        if not p0.exists():
            a = a0_arm(stat, pic)
            CACHE.mkdir(parents=True, exist_ok=True)
            np.savez(p0, **a)
        for arm in PRIMARY_ARMS:
            run_props_arm(fr, stat, arm, cfg, pic)
        if stat in ("pts", "reb"):
            fr3 = fr.with_columns(pl.col("minutes").alias("minutes_oracle"))
            run_props_arm(fr3, stat, "base", cfg, pic, extra=("minutes_oracle",), tag="A3")
            run_props_arm(fr3, stat, "B", cfg, pic, extra=("minutes_oracle",), tag="A3W")


def stage_p() -> None:
    fr, cfg, _ = load_p_frame()
    save_meta(
        "pplay",
        fr,
        {
            "starter_rate_prior": "starter10",
            "avg_minutes_given_played_prior": "min10",
            "n_played_prior": "n_prior",
            "games_into_season": "team_game_no",
            "min_real": "minutes",
        },
    )
    out = CACHE / "P__scores.npz"
    if out.exists():
        return
    res = p_walk(fr, p_arms(), cfg, ckpt=CACHE / "P_blocks")
    np.savez_compressed(out, **res)


def stage_m() -> None:
    sf, cfg, _ = load_m_frame()
    save_meta("minutes", sf)
    out = CACHE / "M__scores.npz"
    if out.exists():
        return
    res = m_walk(sf, m_arm_names(), cfg, ckpt=CACHE / "M_blocks")
    check_m2_repro(res)
    np.savez_compressed(out, **res)


def check_m2_repro(res: dict[str, np.ndarray]) -> dict[str, Any]:
    """The base arm (M2) must reproduce the stored MINUTES_V2 M2 CRPS on the rows both have (the stored file
    predates the 2026-10-11 data clean-up: 2024 has 27,583 stored rows vs 27,701 now), before any candidate matters."""
    z = np.load(MV2_DIR / "minutes_scores.npz", allow_pickle=True)
    cur = pl.DataFrame(
        {
            "game_id": res["gid"],
            "player_id": res["pid"].astype(np.int64),
            "season": res["season"],
            "c": res["base_crps"],
        }
    )
    old = pl.DataFrame(
        {
            "game_id": z["row_gid"].astype(str),
            "player_id": z["row_pid"].astype(np.int64),
            "o": z["M2_crps"],
        }
    )
    j = cur.join(old, on=["game_id", "player_id"], how="inner")
    rec: dict[str, Any] = {}
    ok = True
    for s, tol in ((2023, 1e-6), (2024, 0.005)):
        c = j.filter(pl.col("season") == s)
        n_cur, n_old = int((cur["season"] == s).sum()), int((z["row_season"] == s).sum())
        got, want = float(c["c"].mean()), float(c["o"].mean())
        good = abs(got - want) <= tol and c.height >= 0.99 * min(n_cur, n_old)
        ok &= good
        rec[str(s)] = {
            "got": got,
            "stored": want,
            "n_common": c.height,
            "n_now": n_cur,
            "n_stored": n_old,
            "tol": tol,
            "ok": good,
        }
    write_json(OUT_DIR / "m2_repro.json", rec)
    _log(f"M2 repro ok={ok} {rec}")
    if not ok:
        raise RuntimeError(
            "M2 does not reproduce the stored MINUTES_V2 scores: stop before any candidate is read"
        )
    return rec


def run_arms(cell: str = "all") -> None:
    require_frozen()
    require_gate()
    CACHE.mkdir(parents=True, exist_ok=True)
    if cell in ("all", "S"):
        stage_props()
    if cell in ("all", "P"):
        stage_p()
    if cell in ("all", "M"):
        stage_m()


# --------------------------------------------------------------------------- scoring
def team_status_table() -> pl.DataFrame:
    from research.budget.status import team_status

    con = duckdb.connect(DB, read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
            f"WHERE season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    return team_status(g).select("game_id", "team_id", "status", "late")


def slice_masks(meta: pl.DataFrame, status: pl.DataFrame) -> dict[str, np.ndarray]:
    d = meta.join(status, on=["game_id", "team_id"], how="left")
    d = d.with_columns(
        pl.col("min10").rank("ordinal", descending=True).over(["game_id", "team_id"]).alias("_rk")
    )
    f = lambda c: d[c].to_numpy().astype(float)  # noqa: E731
    out = {
        "all": np.ones(d.height, dtype=bool),
        "starter": f("starter10") >= 0.5,
        "bench": f("starter10") < 0.5,
        "star": (d["_rk"] <= 3).to_numpy(),
        "non_star": (d["_rk"] > 3).to_numpy(),
        "contending": (d["status"] == "contending").to_numpy(),
        "middle": (d["status"] == "middle").to_numpy(),
        "out": (d["status"] == "out").to_numpy(),
        "late": d["late"].fill_null(False).to_numpy(),
        "second_night": f("wl_b2b_second") == 1,
        "three_in_four": f("wl_three_in_four") == 1,
        "road_trip4plus": f("wl_road_trip4plus") == 1,
        "eastbound_ge2tz": f("wl_tz_cross_hr") >= 2,
        "miles_ge1500": f("wl_miles_from_prev") >= 1500,
        "early_tip": f("wl_early_tip") == 1,
        "cold5_9": (f("n_prior") >= 5) & (f("n_prior") < 10),
        "first15": f("team_game_no") < 15,
    }
    sd = f("wl_speed_decline")
    out["speed_decline_bottom_decile"] = sd <= np.nanquantile(sd, 0.1)
    return {k: np.asarray(v, dtype=bool) for k, v in out.items()}


def _load_arm_props(stat: str, arm: str) -> dict[str, np.ndarray] | None:
    p = cache_path(stat, arm)
    return None if not p.exists() else dict(np.load(p, allow_pickle=True))


def cell_arrays(cell: str) -> dict[str, Any]:
    """Per-row metric arrays for every cached arm of a cell, one common row order, plus keys and y."""
    from research.eval import four_factors_eval as F

    out: dict[str, Any] = {"arms": {}}
    if cell in STATS:
        base = _load_arm_props(cell, "base")
        assert base is not None
        out.update(gid=base["gid"], pid=base["pid"], season=base["season"], y=base["y"])
        for arm in ("base", *PRIMARY_ARMS, *ATTACK_ARMS, "A3", "A3W"):
            a = _load_arm_props(cell, arm)
            if a is None:
                continue
            if not (
                np.array_equal(a["gid"], base["gid"]) and np.array_equal(a["pid"], base["pid"])
            ):
                raise RuntimeError(f"{cell}/{arm}: rows not aligned with A0")
            sc = F.row_scores(a["qi"], a["y"], cell)
            out["arms"][arm] = {
                "m": sc["crps"],
                "pit": sc["pit"],
                "bias": sc["resid"],
                "qi": a["qi"],
            }
        sh = _load_arm_props(cell, "SHUF")
        if sh is not None:
            sc = F.row_scores(sh["qi"], sh["y"], cell)
            out["arms"]["SHUF"] = {"m": sc["crps"], "pit": sc["pit"], "bias": sc["resid"]}
    elif cell == "P":
        z = dict(np.load(CACHE / "P__scores.npz", allow_pickle=True))
        out.update(gid=z["gid"], pid=z["pid"], season=z["season"], y=z["y"])
        for k in z:
            if k.startswith("p_"):
                arm = k[2:]
                out["arms"][arm] = {"m": _ll(z[k], z["y"]), "p": z[k]}
        sp = CACHE / "P__shuffle.npz"
        if sp.exists():
            zs = dict(np.load(sp, allow_pickle=True))
            for k in zs:
                if k.startswith("p_"):
                    out["arms"][k[2:] + "_shuf"] = {"m": _ll(zs[k], zs["y"]), "p": zs[k]}
    else:
        z = dict(np.load(CACHE / "M__scores.npz", allow_pickle=True))
        out.update(gid=z["gid"], pid=z["pid"], season=z["season"], y=z["y"])
        for k in z:
            if k.endswith("_crps"):
                arm = k[: -len("_crps")]
                out["arms"][arm] = {
                    "m": z[k],
                    "pit": z[f"{arm}_pit"],
                    "bias": z[f"{arm}_mean"] - z["y"],
                }
        sp = CACHE / "M__shuffle.npz"
        if sp.exists():
            zs = dict(np.load(sp, allow_pickle=True))
            for k in zs:
                if k.endswith("_crps"):
                    arm = k[: -len("_crps")]
                    out["arms"][arm + "_shuf"] = {
                        "m": zs[k],
                        "pit": zs[f"{arm}_pit"],
                        "bias": zs[f"{arm}_mean"] - zs["y"],
                    }
    return out


def score_cell(cell: str, status: pl.DataFrame) -> dict[str, Any]:
    arr = cell_arrays(cell)
    kind = {"P": "pplay", "M": "minutes"}.get(cell, "props")
    meta = pl.read_parquet(CACHE / f"meta_{kind}.parquet").with_columns(
        pl.col("player_id").cast(pl.Int64)
    )
    keys = pl.DataFrame({"game_id": arr["gid"], "player_id": arr["pid"].astype(np.int64)})
    mm = keys.join(meta.unique(KEYS), on=KEYS, how="left")
    if mm.height != len(arr["gid"]):
        raise RuntimeError("meta join changed the row count")
    masks_all = slice_masks(mm, status)
    rows: list[dict[str, Any]] = []
    sls: list[dict[str, Any]] = []
    for season in SEASONS:
        sm = arr["season"] == season
        gid = arr["gid"][sm]
        base = arr["arms"]["base"]
        for name, a in arr["arms"].items():
            r: dict[str, Any] = {
                "cell": cell, "season": season, "arm": name, "n_rows": int(sm.sum()),
                "n_games": int(len(np.unique(gid))), "metric": float(a["m"][sm].mean()), "unit": UNIT[cell],
            }  # fmt: skip
            if "pit" in a:
                pit = a["pit"][sm]
                r["cov80"] = float(((pit > 0.10) & (pit <= 0.90)).mean())
                r["bias"] = float(a["bias"][sm].mean())
            if "p" in a:
                r["ece"] = float(ece(a["p"][sm], arr["y"][sm]))
            if name != "base":
                r.update(delta_stats(a["m"][sm] - base["m"][sm], gid))
            rows.append(r)
        if "B" not in arr["arms"]:
            continue
        b = arr["arms"]["B"]
        for other in ("PL", "NT"):
            if other in arr["arms"]:
                d = delta_stats(b["m"][sm] - arr["arms"][other]["m"][sm], gid)
                rows.append(
                    {
                        "cell": cell,
                        "season": season,
                        "arm": f"B_minus_{other}",
                        "n_rows": int(sm.sum()),
                        "unit": UNIT[cell],
                        **d,
                    }
                )
        db = b["m"] - base["m"]
        for sname, mk in masks_all.items():
            m = mk & sm
            n = int(m.sum())
            rec: dict[str, Any] = {"cell": cell, "season": season, "slice": sname, "n": n}
            if n >= MIN_SLICE_N or sname == "all":
                ds = delta_stats(db[m], arr["gid"][m])
                rec.update({k: ds[k] for k in ("delta", "ci_lo", "ci_hi")})
            sls.append(rec)
    return {"rows": rows, "slices": sls}


def add_bh(rows: list[dict[str, Any]]) -> None:
    for season in SEASONS:
        tgt = [r for r in rows if r["season"] == season and r["arm"] == "B"]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


def oracle_bound(stat: str) -> dict[int, float] | None:
    p = MV2_DIR / f"props_{stat}_A3.npz"
    if not p.exists():
        return None
    z = np.load(p, allow_pickle=True)
    return (
        {s: float(z["crps_int"][z["season"] == s].mean()) for s in SEASONS}
        if "crps_int" in z.files
        else None
    )


def evaluate(res: dict[str, Any], gate_ok: bool) -> dict[str, Any]:
    """The pass rule per cell (rules 1-3 and the knockout flag); the shuffled-label test and the family-alone arms
    are added by ``attack`` for cells that reach 'pass_before_attack'."""
    tab = {(r["cell"], r["season"], r["arm"]): r for r in res["rows"]}
    sl = {(s["cell"], s["season"], s["slice"]): s for s in res["slices"]}
    out: dict[str, Any] = {}
    for cell in CELLS:
        if (cell, 2024, "B") not in tab:
            continue
        floor = FLOOR[cell]
        b23, b24, c24 = tab[(cell, 2023, "B")], tab[(cell, 2024, "B")], tab[(cell, 2024, "base")]
        sel = bool(b23["delta"] <= floor and b23["ci_hi"] < 0.0)
        r1 = bool(b24["delta"] <= floor and b24["ci_hi"] < 0.0 and b24.get("p_bh", 1.0) <= 0.05)
        worse = {
            s: v["delta"]
            for (c, y, s), v in sl.items()
            if c == cell and y == 2024 and v["n"] >= MIN_SLICE_N and v.get("delta") is not None
            and s not in ("all", "cold5_9") and v["delta"] > SLICE_TOL[cell]
        }  # fmt: skip
        cold = sl.get((cell, 2024, "cold5_9"), {})
        guards: dict[str, bool] = {
            "no_slice_worse": not worse,
            "cold_start": True if cold.get("delta") is None else bool(cold["ci_lo"] <= 0.0),
        }
        if "cov80" in b24:
            guards["cov80"] = bool(abs(b24["cov80"] - 0.8) <= abs(c24["cov80"] - 0.8) + COV_TOL)
            if cell == "M":
                guards["bias"] = bool(abs(b24["bias"]) <= MAX_BIAS)
            else:
                guards["bias"] = bool(
                    abs(b24["bias"]) <= abs(c24["bias"]) + BIAS_TOL and abs(b24["bias"]) <= MAX_BIAS
                )
        if "ece" in b24:
            guards["ece"] = bool(b24["ece"] <= c24["ece"] + ECE_TOL)
        bp = tab.get((cell, 2024, "B_minus_PL"))
        beats_pl = None if bp is None else bool(bp["delta"] <= PLACEBO_MARGIN[cell])
        ob = oracle_bound(cell) if cell in STATS else None
        oracle_ok = None if ob is None else bool(b24["metric"] > ob[2024])
        full = b24["delta"]
        ko = {}
        for fam in ("LOAD", "SCHEDULE", "TRAVEL"):
            k = tab.get((cell, 2024, f"KO_{fam}"))
            if k is not None:
                ko[fam] = {
                    "gain_drop": k["delta"],
                    "carries_by_drop": carries(full, k["delta"], None),
                }
        nt = tab.get((cell, 2024, "B_minus_NT"))
        out[cell] = {
            "select_2023": sel, "rule1_2024": r1, "guards": guards, "slices_worse": worse,
            "beats_placebo": beats_pl, "oracle_bound_ok": oracle_ok, "gate_ok": bool(gate_ok),
            "knockout_drop": ko,
            "tracking_claim": None if nt is None else bool(nt["delta"] <= floor / 2.0),
            "pass_before_attack": bool(
                sel and r1 and all(guards.values()) and beats_pl is True and oracle_ok is not False and gate_ok
            ),
        }  # fmt: skip
    return {"cells": out, "close": not any(v["select_2023"] for v in out.values())}


def d3_verdict(res: dict[str, Any]) -> dict[str, Any]:
    tab = {(r["cell"], r["season"], r["arm"]): r for r in res["rows"]}
    out: dict[str, Any] = {}
    for stat in ("pts", "reb"):
        a3, a3w = tab.get((stat, 2024, "A3")), tab.get((stat, 2024, "A3W"))
        if a3 is None or a3w is None:
            continue
        d = a3w["metric"] - a3["metric"]
        b = tab[(stat, 2024, "B")]["delta"]
        out[stat] = {
            "B_minus_A0": b,
            "A3W_minus_A3": d,
            "reading": "through minutes"
            if (b <= FLOOR[stat] and d > FLOOR[stat] / 2)
            else (
                "contradicts the minutes story (A3W also gains)"
                if d <= FLOOR[stat] / 2
                else "no B gain"
            ),
        }
    return out


def decision_scores(res_cells: tuple[str, ...] = STATS) -> dict[str, Any]:
    """D2 (descriptive): DraftKings T-60 legs re-priced with A0 and B integer grids."""
    try:
        from nba.markets import decision_scoring as DS
        from research.eval.team_budget_eval import legs_with_p, p_over_from_grid

        legs = DS.load_legs(PAIRS_DIR)
        out: dict[str, Any] = {}
        for armname in ("base", "B"):
            p = np.full(len(legs["line"]), np.nan)
            for stat in res_cells:
                a = _load_arm_props(stat, armname)
                if a is None:
                    continue
                idx = pl.DataFrame(
                    {"game_id": a["gid"], "player_id": a["pid"].astype(np.int64)}
                ).with_row_index("_i")
                m = np.flatnonzero(legs["stat"] == stat)
                lk = pl.DataFrame(
                    {
                        "game_id": legs["game_id"][m],
                        "player_id": legs["player_id"][m].astype(np.int64),
                        "_p": np.arange(len(m)),
                    }
                )
                j = lk.join(idx, on=KEYS, how="left")
                ok = j["_i"].is_not_null().to_numpy()
                pp = p_over_from_grid(a["qi"][j["_i"].fill_null(0).to_numpy()], legs["line"][m])
                p[m[ok]] = pp[ok]
            keep = ~np.isnan(p)
            lg = {
                k: (v[keep] if isinstance(v, np.ndarray) and v.shape[:1] == keep.shape else v)
                for k, v in legs.items()
            }
            gm = {g: i for i, g in enumerate(np.unique(lg["game_id"]))}
            lg["gi"], lg["G"] = np.array([gm[g] for g in lg["game_id"]]), len(gm)
            r = DS.run_all(legs_with_p(lg, p[keep]))
            out[armname] = {
                "n_legs": int(keep.sum()),
                "cells": [
                    {
                        "season": c["season"],
                        "stat": c["stat"],
                        "by_threshold": [
                            {"threshold": t["threshold"], "model": t["model"]}
                            for t in c["by_threshold"]
                        ],
                    }
                    for c in r["cells"]
                ],
            }
        return out
    except Exception as exc:  # secondary; cannot rescue or fail the rule
        return {"error": repr(exc)}


def run_report() -> dict[str, Any]:
    require_frozen()
    require_gate()
    status = team_status_table()
    res: dict[str, Any] = {
        "rows": [],
        "slices": [],
        "ambiguities": AMBIGUITIES,
        "sha256": FROZEN_FULL,
    }
    for cell in CELLS:
        if cell in STATS and not cache_path(cell, "B").exists():
            continue
        if cell == "P" and not (CACHE / "P__scores.npz").exists():
            continue
        if cell == "M" and not (CACHE / "M__scores.npz").exists():
            continue
        sc = score_cell(cell, status)
        res["rows"] += sc["rows"]
        res["slices"] += sc["slices"]
        _log(f"{cell} scored")
    add_bh(res["rows"])
    gate = json.loads((OUT_DIR / "gate.json").read_text())
    res["verdict"] = evaluate(res, bool(gate["gate_ok"]))
    res["d3_oracle_minutes"] = d3_verdict(res)
    res["d1_rest_dnp"] = (
        json.loads((OUT_DIR / "gates.json").read_text()).get("rest", {}).get("rest_events", [])
    )
    res["d2_decision_score"] = decision_scores()
    ap = OUT_DIR / "attack.json"
    if ap.exists():
        res["attack"] = json.loads(ap.read_text())
        merge_attack(res)
    write_json(OUT_DIR / "results.json", res)
    REPORT_MD.write_text(render(res))
    return res


def merge_attack(res: dict[str, Any]) -> None:
    """Fold shuffled-label and family-alone results into the verdict: a PASS needs all of rules 1-4."""
    for cell, v in res["verdict"]["cells"].items():
        at = res["attack"].get("cells", {}).get(cell)
        v["passed"] = False
        if not v["pass_before_attack"]:
            continue
        if at is None:
            v["passed"] = None  # attack not run yet
            continue
        v["shuffled_ok"] = at["shuffled_ok"]
        v["one_family_carries"] = at["one_family_carries"] or any(
            k["carries_by_drop"] for k in v["knockout_drop"].values()
        )
        v["passed"] = bool(at["shuffled_ok"] and not v["one_family_carries"])


# --------------------------------------------------------------------------- stage: attack
def run_attack() -> dict[str, Any]:
    require_frozen()
    require_gate()
    res = json.loads((OUT_DIR / "results.json").read_text())
    passing = [c for c, v in res["verdict"]["cells"].items() if v["pass_before_attack"]]
    out: dict[str, Any] = {"cells": {}, "passing": passing}
    if not passing:
        write_json(OUT_DIR / "attack.json", out)
        return out
    for cell in passing:
        if cell in STATS:
            fr, cfg, _ = load_props_frame()
            pic = load_pic()
            run_props_arm(fr, cell, "B", cfg, pic, shuffle=True, tag="SHUF")
            for arm in ATTACK_ARMS:
                run_props_arm(fr, cell, arm, cfg, pic)
        elif cell == "P":
            fr, cfg, _ = load_p_frame()
            arms = {k: v for k, v in p_arms().items() if k in ("base", "B", *ATTACK_ARMS)}
            r = p_walk(fr, arms, cfg, shuffle=frozenset({"B"}), ckpt=CACHE / "P_attack_blocks")
            np.savez_compressed(CACHE / "P__shuffle.npz", **r)
        else:
            sf, cfg, _ = load_m_frame()
            arms = {k: v for k, v in m_arm_names().items() if k in ("base", "B", *ATTACK_ARMS)}
            r = m_walk(
                sf,
                arms,
                cfg,
                shuffle=frozenset({"B"}),
                with_baselines=False,
                ckpt=CACHE / "M_attack_blocks",
            )
            np.savez_compressed(CACHE / "M__shuffle.npz", **r)
    # score the attack arms
    for cell in passing:
        arr = cell_arrays(cell)
        sm = arr["season"] == 2024
        gid = arr["gid"][sm]
        base_m = arr["arms"]["base"]["m"][sm]
        full = float((arr["arms"]["B"]["m"][sm] - base_m).mean())
        b24 = delta_stats(arr["arms"]["B"]["m"][sm] - base_m, gid)
        shuf_key = "SHUF" if cell in STATS else "B_shuf"
        gs = (
            float((arr["arms"][shuf_key]["m"][sm] - base_m).mean())
            if shuf_key in arr["arms"]
            else float("nan")
        )
        alone = {}
        for fam in ("LOAD", "SCHEDULE", "TRAVEL"):
            a = arr["arms"].get(f"AL_{fam}")
            alone[fam] = None if a is None else float((a["m"][sm] - base_m).mean())
        ko = res["verdict"]["cells"][cell]["knockout_drop"]
        car = {fam: carries(full, ko[fam]["gain_drop"], alone[fam]) for fam in ko}
        out["cells"][cell] = {
            "gain_full": full,
            "gain_shuffled": gs,
            "mde": b24["mde"],
            "shuffled_ok": bool(gs >= -b24["mde"]),
            "gain_alone": alone,
            "carries": car,
            "one_family_carries": any(car.values()),
        }
    write_json(OUT_DIR / "attack.json", out)
    return out


# --------------------------------------------------------------------------- report text
def _f(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.{nd}f}"


def render(res: dict[str, Any]) -> str:
    ln = [
        "# WORKLOAD results",
        "",
        f"Rule: docs/prereg/WORKLOAD.md, frozen sha256 {FROZEN_FULL[:8]}. Select 2023, report 2024, game-clustered 95% CI (2000 draws, seed 0), BH over the six primary cells per season.",
        "",
        "| cell | season | n rows / games | base | candidate | delta [CI] | p_bh | B-PL | cov80 | bias/ECE |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    tab = {(r["cell"], r["season"], r["arm"]): r for r in res["rows"]}
    for cell in CELLS:
        for s in SEASONS:
            b = tab.get((cell, s, "B"))
            if b is None:
                continue
            base = tab[(cell, s, "base")]
            bp = tab.get((cell, s, "B_minus_PL"))
            ln.append(
                f"| {cell} ({UNIT[cell]}) | {s} | {b['n_rows']} / {b['n_games']} | {base['metric']:.4f} | {b['metric']:.4f} | {_f(b['delta'])} [{_f(b['ci_lo'])}, {_f(b['ci_hi'])}] | {b.get('p_bh', float('nan')):.3f} | {_f(bp['delta']) if bp else 'n/a'} | {b.get('cov80', float('nan')):.3f} | {_f(b.get('bias', b.get('ece')))} |"
            )
    ln += [
        "",
        "## Verdict by cell",
        "",
        "```json",
        json.dumps(res["verdict"], indent=1, default=float),
        "```",
    ]
    ln += [
        "",
        "## D3 oracle minutes (descriptive)",
        "",
        "```json",
        json.dumps(res.get("d3_oracle_minutes", {}), indent=1, default=float),
        "```",
    ]
    ln += ["", "## Readings of ambiguous rule text", ""]
    for a in res["ambiguities"]:
        ln += [f"* Rule: {a['text']}", f"  Reading: {a['reading']}"]
    return "\n".join(ln) + "\n"


# --------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "arms", "report", "attack", "run"])
    ap.add_argument(
        "--sha", default=None, help="64-hex sha256 of the frozen rule (must equal FROZEN_FULL)"
    )
    ap.add_argument("--cell", default="all", choices=["all", "P", "M", "S"])
    a = ap.parse_args(argv)
    require_frozen(a.sha)
    if a.cmd == "gate":
        return 0 if run_gate(a.sha)["gate_ok"] else 1
    if a.cmd == "report":
        run_report()
        return 0
    if a.cmd == "arms":
        with heavy():
            run_arms(a.cell)
        return 0
    if a.cmd == "attack":
        with heavy():
            run_attack()
        run_report()
        return 0
    with heavy():
        if not run_gate(a.sha)["gate_ok"]:
            return 1
        run_arms("all")
        run_report()
        run_attack()
    run_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
