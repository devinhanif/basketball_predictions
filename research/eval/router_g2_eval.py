# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,call-overload,attr-defined,misc,no-any-return,type-var"
"""ROUTER_G2 harness (rule: docs/prereg/ROUTER_G2.md; DRAFT until the main session commits `prereg(ROUTER_G2): <sha256>`).

A new router gate g' = P(minutes >= 5 | plays, as-of), a LightGBM fitted walk-forward (month blocks, 2022 warm-up, each block fitted on
rows dated strictly before it) on the RECENT_ROLE R feature set (T-60 report terms + production's recent-minutes columns + five
recent-role columns), routes three arms against A0 = production + lower-tail mixture on integer support:

    E1'  pts          g_eff * XGBoost(A1)  +  (1 - g_eff) * A0                       (pmf mixture)
    E2'  pts/reb/ast/fg3m   learned blend of [A0, SEQ-E2] where g_eff >= TAU, else A0
    E4'  pts          learned blend of [E1', SEQ-E2] where g_eff >= TAU, else E1'

g_eff = g' on regular-season rows (game_id prefix 002) that have a feature row, else 0 (hard route to A0: postseason, play-in, and the
rows the T-60 report lists OUT who nevertheless played). Stored members are reused (A1, SEQ, A0 components); only g' is new.

    M="-m research.eval.router_g2_eval"
    .venv/bin/python -W ignore $M gate                       # minimum-data gates; fits only g' (no CRPS arm is scored); pre-freeze allowed
    .venv/bin/python -W ignore $M run --sha <64 hex>         # gate -> g' walks -> arms -> score -> report, ONE data/ops/heavy.lock
    .venv/bin/python -W ignore $M smoke                      # plumbing proof: every gate = 0 so every arm is A0 and every delta is 0
    .venv/bin/python -W ignore $M holdout --sha <64 hex>     # Stage 2: refuses unless a committed, DEVIN CONFIRMED log row exists; no 2025 loader here

``run`` and ``holdout`` refuse unless the text above ``=== RESULTS BELOW ===`` hashes to ``--sha``, the document is marked FROZEN and a commit
``prereg(ROUTER_G2): <sha>`` is reachable from HEAD. Season 2025 is never loaded; ``nba.duckdb`` is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, bh_adjust, crps_from_quantiles, month_blocks
from nba.props.context_residual import to_integer_support
from research.eval import ensemble_eval as E
from research.eval import near_miss_bundle_eval as N

ID = "ROUTER_G2"
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/ROUTER_G2.md"
OUT_DIR = ROOT / "reports/prereg_router_g2"
REPORT_MD = ROOT / "reports/prereg_router_g2.md"
CACHE = ROOT / "data/ops/router_g2_cache"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
MARKER = "=== RESULTS BELOW ==="

STATS = N.STATS
SEASONS = N.SEASONS
KEYS = N.KEYS
# --- constants of the rule (changing one changes the rule)
TAU = 0.90  # an arm is used only where g_eff >= TAU, i.e. P(minutes < 5 | plays) <= 10%
REGULAR_PREFIX = "002"
FLOOR = -0.005
PLACEBO_MARGIN = (
    -0.001
)  # arm minus (arm with the input's placebo / the constant gate), point, CI upper < 0, EACH season
SHUF_G_MARGIN = (
    -0.001
)  # shuffled-label gate minus constant gate: FAILS if point <= this with CI upper < 0
SHUF_C_MIN = -0.0025  # A0-null world: a gain at or beyond this FAILS (half the floor)
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
NULL_GAP_PP = 2.0
MDE_MAX = 0.005
REPO_TOL = 1e-6
MIN_SHORT_ROWS = 1000
G_AUC_FLOOR = 0.70
#: declared cells, (arm, stat). BH over these six, separately for 2023 and 2024.
CELLS: tuple[tuple[str, str], ...] = (
    ("E1p", "pts"),
    ("E2p", "pts"),
    ("E2p", "reb"),
    ("E2p", "ast"),
    ("E2p", "fg3m"),
    ("E4p", "pts"),
)
#: added inputs whose past-only placebo each cell must beat (G is the gate's five recent-role columns)
PLACEBO_INPUTS: dict[str, tuple[str, ...]] = {
    "E1p": ("G", "A1"),
    "E2p": ("G", "SEQ"),
    "E4p": ("G", "A1", "SEQ"),
}


def cell_name(arm: str, stat: str) -> str:
    return f"{arm}|{stat}"


AMBIGUITIES: dict[str, str] = {
    "g'": "P(minutes >= 5 | plays, as-of): production LightGBM binary params, seed 0, fitted on PLAYED rows of the RECENT_ROLE Cell-P frame (rostered, not OUT on the real-tip T-60 report, n_played_prior >= 5) dated strictly before each month block (2022 warm-up), features = RECENT_ROLE's R set; trained on played rows only so it is the conditional the mixture weight needs",
    "g_eff": "g' on regular-season rows (game_id prefix 002) that have a feature row; 0 elsewhere (postseason and play-in, and rows the T-60 report lists OUT who played): pre-tip-known hard route to A0",
    "TAU": "0.90, fixed in the rule text; derived from the guard (minutes<5 slice <= +0.01) and the E2 slice arithmetic, not from any CRPS of an arm",
    "constant gate": "per month block the base rate of the label over the same training rows, with the same hard route: the gate with no features",
    "placebo G": "the five recent-role columns replaced by one strictly earlier row of the same player-season (RECENT_ROLE placebo, NaN for a player-season's first row); the rest of g''s columns real",
    "placebo A1/SEQ": "ensemble members replaced by the grid of ONE uniformly drawn strictly earlier row of the same player (near_miss_bundle_eval.past_only_index, seeds 1 and 2)",
    "SHUF-G": "g' trained on ONE permutation of the training labels per block (default_rng([0, block])), arm scored on real outcomes; compared with the constant-gate arm. Fails if the shuffled gate beats the constant gate by 0.001 with CI upper < 0",
    "SHUF-C": "labels drawn from A0's own predictive distribution (A0 is the truth); the blend gate refit on them; the arm must not beat A0 by 0.0025 on them",
    "flag": "E4' only: members ROUTER (A1) and SEQ; E1', E2' have one member plus the gate, so no carrier flag",
    "family": "BH over the six declared cells {E1' pts, E2' pts/reb/ast/fg3m, E4' pts}, separately for 2023 and 2024, two-sided game-clustered bootstrap p",
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float))


# --------------------------------------------------------------------------- freeze and holdout refusal
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def head_text(doc: Path = DOC) -> str:
    out: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        out.append(line)
    return "".join(out)


def frozen_sha(doc: Path = DOC) -> str:
    return hashlib.sha256(head_text(doc).encode()).hexdigest()


def doc_status(doc: Path = DOC) -> str:
    for line in head_text(doc).splitlines():
        if line.startswith("Status:"):
            return line.split()[1].strip("*:").upper()
    return "UNKNOWN"


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path = DOC, git: Callable[..., str] = _git) -> str:
    """Refuse unless ``sha`` is the hash of the rule text, the document says FROZEN, and the freeze commit exists."""
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if doc_status(doc) != "FROZEN":
        raise RuntimeError(f"{doc} is not marked FROZEN: no arm may be fitted")
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; no arm may be fitted"
        )
    return sha


def require_holdout_row(
    sha: str, log: Path = HOLDOUT_LOG, doc: Path = DOC, git: Callable[..., str] = _git
) -> str:
    """Stage 2 gate: a committed, unmodified HOLDOUT_ACCESS_LOG row naming this rule, PRE-REGISTERED and DEVIN CONFIRMED."""
    require_frozen(sha, doc, git)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            f"no committed holdout row for {ID} (PRE-REGISTERED, DEVIN CONFIRMED, sha8): the holdout stage refuses"
        )
    if git("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(
            "HOLDOUT_ACCESS_LOG.md has uncommitted changes: commit the row BEFORE the run"
        )
    if not git("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is not in any commit: the holdout stage refuses")
    return rows[0]


# --------------------------------------------------------------------------- pure helpers (unit tested)
def effective_gate(
    g: np.ndarray, game_id: np.ndarray, has_row: np.ndarray | None = None
) -> np.ndarray:
    """g' on regular-season rows that have a feature row; 0 everywhere else (the hard route to A0). NaN never survives."""
    g = np.asarray(g, dtype=float)
    reg = np.array([str(x)[:3] == REGULAR_PREFIX for x in game_id])
    ok = reg & np.isfinite(g)
    if has_row is not None:
        ok &= has_row
    return np.where(ok, np.clip(np.nan_to_num(g, nan=0.0), 0.0, 1.0), 0.0)


def route(first: np.ndarray, other: np.ndarray, g_eff: np.ndarray, tau: float = TAU) -> np.ndarray:
    """``other`` where g_eff >= tau, else ``first`` (both integer 199-grids of the same rows)."""
    return np.where((g_eff >= tau)[:, None], other, first)


def constant_gate(
    block_of_row: np.ndarray, block_start: dict[int, Any], dates: np.ndarray, label: np.ndarray
) -> np.ndarray:
    """Per row, the base rate of ``label`` over rows dated strictly before the row's block start (no features, as-of).
    ``block_of_row`` is float with NaN where the row has no block."""
    out = np.full(len(block_of_row), np.nan)
    lab = np.asarray(label, dtype=float)
    blk = np.nan_to_num(np.asarray(block_of_row, dtype=float), nan=-1.0).astype(int)
    for bi, start in block_start.items():
        m = dates < start
        if m.any():
            out[blk == bi] = float(lab[m].mean())
    return out


def gate_label_frame(fr: pl.DataFrame) -> pl.DataFrame:
    """Played rows only, ``played`` := minutes >= 5 (the label ``RR.walk`` fits), so g' is P(minutes >= 5 | plays)."""
    return fr.filter(pl.col("played") == 1).with_columns(
        (pl.col("min_real") >= 5).cast(pl.Int64).alias("played")
    )


def check_cell(
    delta: dict[str, dict[str, Any]], bh: dict[str, float], floor: float = FLOOR
) -> dict[str, bool]:
    return {
        s: bool(delta[s]["point"] <= floor and delta[s]["hi"] < 0 and bh[s] < 0.05) for s in delta
    }


def placebo_pass(d: dict[str, dict[str, Any]], margin: float = PLACEBO_MARGIN) -> bool:
    """arm minus its placebo / constant gate must be <= margin with CI upper < 0 in EVERY season."""
    return all(d[s]["point"] <= margin and d[s]["hi"] < 0 for s in d)


def shuf_g_fails(d: dict[str, dict[str, Any]], margin: float = SHUF_G_MARGIN) -> bool:
    """A gate trained on shuffled labels beating the constant gate is a pipeline defect, any season."""
    return any(d[s]["point"] <= margin and d[s]["hi"] < 0 for s in d)


def cell_verdict(
    harm: bool,
    core: bool,
    guards: bool,
    placebo: bool,
    shuffle_ok: bool,
    flag: bool,
    repro_ok: bool = True,
) -> str:
    return N.stat_verdict(harm, core, guards, placebo, shuffle_ok, flag, repro_ok)


# --------------------------------------------------------------------------- g' (the one thing fitted)
def build_gate_inputs() -> tuple[pl.DataFrame, Any, dict[str, list[str]]]:
    """The RECENT_ROLE Cell-P frame (R columns, past-only placebo columns), the P(play) config and the arm column lists."""
    from research.eval import recent_role_eval as RR

    fr, cfg, _ = RR.build_frame()
    cols = RR.arm_columns()
    return fr, cfg, {"G": cols["R"], "GPL": cols["PL"]}


def fit_gates(
    fr: pl.DataFrame, cfg: Any, arms: dict[str, list[str]], shuffle: bool = False
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Month-block walk-forward of g' (``recent_role_eval.walk``); every block fitted strictly on earlier PLAYED rows."""
    from research.eval import recent_role_eval as RR

    pf = gate_label_frame(fr)
    return RR.walk(pf, arms, cfg, shuffle=shuffle)


def block_starts(fr: pl.DataFrame) -> dict[int, Any]:
    from nba.eval.context_residual_eval import TEST_SEASONS

    pf = gate_label_frame(fr)
    test = pf.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    return dict(enumerate(s for s, _ in month_blocks(test)))


def align_to_rows(
    res: dict[str, np.ndarray], key: str, game_id: np.ndarray, player_id: np.ndarray
) -> np.ndarray:
    """``res[key]`` placed on the A0 rows (NaN where the gate has no row)."""
    n = len(game_id)
    left = pl.DataFrame(
        {"game_id": game_id, "player_id": player_id.astype(np.int64), "_i": np.arange(n)}
    )
    right = pl.DataFrame(
        {
            "game_id": res["gid"].astype(str),
            "player_id": res["pid"].astype(np.int64),
            "_v": res[key].astype(float),
        }
    ).unique(KEYS)
    j = left.join(right, on=KEYS, how="left").sort("_i")
    return j["_v"].to_numpy().astype(float)


def make_gates(
    real: dict[str, np.ndarray],
    shuf: dict[str, np.ndarray],
    fr: pl.DataFrame,
    b: dict[str, Any],
) -> dict[str, np.ndarray]:
    """Every gate vector on the A0 pts rows: g', its placebo, the shuffled-label gate, the constant gate, and each ``_eff`` form."""
    gid, pid = b["game_id"], b["player_id"]
    g = align_to_rows(real, "p_G", gid, pid)
    gpl = align_to_rows(real, "p_GPL", gid, pid)
    gsh = align_to_rows(shuf, "p_G", gid, pid)
    has = np.isfinite(g)
    pf = gate_label_frame(fr)
    starts = block_starts(fr)
    blk = align_to_rows(real, "blk", gid, pid)
    dates = pf["game_date"].to_numpy()
    gc = constant_gate(blk, starts, dates, pf["played"].to_numpy())
    return {
        "g": g,
        "g_pl": gpl,
        "g_shuf": gsh,
        "g_const": gc,
        "has_row": has,
        "geff": effective_gate(g, gid, has),
        "geff_pl": effective_gate(gpl, gid, has),
        "geff_shuf": effective_gate(gsh, gid, has),
        "geff_const": effective_gate(gc, gid, has),
    }


# --------------------------------------------------------------------------- arms (numpy; one stat)
def build_arms(
    stat: str, w: dict[str, Any], gates: dict[str, np.ndarray], y_fit: np.ndarray | None = None
) -> dict[str, np.ndarray]:
    """Every pipeline variant of one stat as integer 199-grids. ``y_fit`` replaces the labels every learned blend is fit on (SHUF-C)."""
    b = w["base"][stat]
    y = b["y"] if y_fit is None else y_fit
    dd, se = w["date_days"], b["season"]
    pts = stat == "pts"
    seq = w["seq"][stat]
    a0 = b["a0_int"]
    ge, ge_pl, ge_sh, ge_c = (
        gates["geff"],
        gates["geff_pl"],
        gates["geff_shuf"],
        gates["geff_const"],
    )
    pidx_s, _ = N.past_only_index(b["player_id"], dd, N.PLACEBO_SEED + 2)

    def blend(first: np.ndarray, seqg: np.ndarray) -> np.ndarray:
        pooled, _ = N.learned_blend(first.astype(float), seqg, y, dd, se, w["ctx"], stat)
        return to_integer_support(pooled).astype(np.int16)

    out: dict[str, np.ndarray] = {"A0": a0}
    # E2 family: first stage A0, T442's own construction (first = the continuous mixture grid)
    pooled, _ = N.learned_blend(b["mix"], seq, y, dd, se, w["ctx"], stat)
    e2 = to_integer_support(pooled).astype(np.int16)
    out["E2_all"] = e2
    out["E2p"] = route(a0, e2, ge)
    out["E2p_PL_G"] = route(a0, e2, ge_pl)
    out["E2p_CONST"] = route(a0, e2, ge_c)
    out["E2p_SHUFG"] = route(a0, e2, ge_sh)
    out["E2p_PL_SEQ"] = route(a0, blend(a0, seq[pidx_s]), ge)
    if not pts:
        return out
    a1, pr, g_prod = w["a1_int"], w["a1_present"], w["g"]
    pidx, _ = N.past_only_index(b["player_id"], dd, N.PLACEBO_SEED + 1)
    out["E1_gold"] = N.router_grid(
        a0, a1, g_prod, pr
    )  # ENSEMBLE E1 with the production-minutes g (T440)
    r = N.router_grid(a0, a1, ge, pr)
    r_pl = N.router_grid(a0, a1, ge_pl, pr)
    r_c = N.router_grid(a0, a1, ge_c, pr)
    r_sh = N.router_grid(a0, a1, ge_sh, pr)
    r_a1 = N.router_grid(a0, a1[pidx], ge, pr[pidx])
    out.update(E1p=r, E1p_PL_G=r_pl, E1p_CONST=r_c, E1p_SHUFG=r_sh, E1p_PL_A1=r_a1)
    out["E4p"] = route(r, blend(r, seq), ge)
    out["E4p_PL_G"] = route(r_pl, blend(r_pl, seq), ge_pl)
    out["E4p_CONST"] = route(r_c, blend(r_c, seq), ge_c)
    out["E4p_SHUFG"] = route(r_sh, blend(r_sh, seq), ge_sh)
    out["E4p_PL_A1"] = route(r_a1, blend(r_a1, seq), ge)
    out["E4p_PL_SEQ"] = route(r, blend(r, seq[pidx_s]), ge)
    return out


# --------------------------------------------------------------------------- scoring
class RowScores:
    """Per-row integer CRPS cached per arm so every contrast is a subtraction (one CRPS pass per arm)."""

    def __init__(self, b: dict[str, Any], y: np.ndarray | None = None) -> None:
        self.b = b
        self.y = b["y"] if y is None else y
        self.cache: dict[str, np.ndarray] = {}

    def crps(self, name: str, q: np.ndarray) -> np.ndarray:
        if name not in self.cache:
            self.cache[name] = crps_from_quantiles(q.astype(float), self.y)
        return self.cache[name]

    def delta(self, a: str, c: str) -> dict[str, dict[str, Any]]:
        """Mean (a - c) by season with the game-clustered CI; both arms must have been registered with ``crps``."""
        d = self.cache[a] - self.cache[c]
        out: dict[str, dict[str, Any]] = {}
        for s in SEASONS:
            m = self.b["season"] == s
            out[str(s)] = {
                "n": int(m.sum()),
                **E.clustered(d[m], self.b["game_id"][m]),
                "mean": float(d[m].mean()),
            }
        return out


def score_cell(
    arm: str,
    stat: str,
    w: dict[str, Any],
    arms: dict[str, np.ndarray],
    rs: RowScores,
    null_effects: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Everything the pass rule reads for one cell, from the already-built arms."""
    b = w["base"][stat]
    pts = stat == "pts"
    rows = np.arange(len(b["y"]))
    q = arms[arm]
    ev = E.evaluate_arm(stat, b, rows, q, ci_slices=False, dk=pts)
    res: dict[str, Any] = {"cell": cell_name(arm, stat), "arm": arm, "stat": stat, "seasons": {}}
    for s in SEASONS:
        r = ev[str(s)]
        g = E.guards_for({k: v for k, v in r.items() if not k.startswith("_")}, stat, e1_like=pts)
        sl = r["slices"]["minutes<5"]
        g["g2b_minutes<5"] = bool(sl["n"] < MIN_SLICE_N or sl.get("point", -1.0) <= SLICE_WORSE)
        m = b["season"] == s
        a0cal = E.calibration(b["a0_int"][m].astype(np.int16), b["y"][m])
        res["seasons"][str(s)] = {
            "n": r["n"], "n_games": r["n_games"], "crps_arm": r["crps_arm"], "crps_a0": r["crps_a0"], "delta": r["delta"],
            "p": r["p"], "calibration": r["calibration"], "a0_calibration": a0cal, "slices": r["slices"],
            "team_share": r["team_share"], "tll_dk": r.get("tll_dk"), "tll_fixed": r.get("tll_fixed"), "guards": g,
        }  # fmt: skip
    rs.crps("A0", arms["A0"])
    rs.crps(arm, q)
    # placebo / constant-gate contrasts: arm minus X
    pl_rec: dict[str, Any] = {}
    names = {f"PL_{x}": f"{arm}_PL_{x}" for x in PLACEBO_INPUTS[arm]}
    names["CONST"] = f"{arm}_CONST"
    for label, key in names.items():
        rs.crps(key, arms[key])
        pl_rec[label] = rs.delta(arm, key)
    res["placebo"] = pl_rec
    res["placebo_ok"] = {k: placebo_pass(v) for k, v in pl_rec.items()}
    # SHUF-G: shuffled-label gate minus constant gate
    rs.crps(f"{arm}_SHUFG", arms[f"{arm}_SHUFG"])
    sg = rs.delta(f"{arm}_SHUFG", f"{arm}_CONST")
    res["shuffle"] = {"SHUF_G": sg, "SHUF_C": null_effects[cell_name(arm, stat)]}
    res["shuffle_ok"] = not (
        shuf_g_fails(sg) or N.shuffle_fails({s: v for s, v in res["shuffle"]["SHUF_C"].items()})
    )
    # descriptive: what routing buys against the unrouted / old-g form of the same arm
    desc: dict[str, Any] = {}
    if arm == "E2p":
        rs.crps("E2_all", arms["E2_all"])
        desc["routed_minus_unrouted_E2"] = rs.delta(arm, "E2_all")
        desc["unrouted_E2_minus_A0"] = rs.delta("E2_all", "A0")
    if arm == "E1p":
        rs.crps("E1_gold", arms["E1_gold"])
        desc["E1p_minus_E1_old_g"] = rs.delta(arm, "E1_gold")
        desc["E1_old_g_minus_A0"] = rs.delta("E1_gold", "A0")
    if arm == "E4p":
        for k in ("E1p", "E2p"):
            rs.crps(k, arms[k])
        desc["E4p_minus_E1p"] = rs.delta(arm, "E1p")
        desc["E4p_minus_E2p"] = rs.delta(arm, "E2p")
    res["descriptive"] = desc
    # routed vs kept rows (descriptive): delta vs A0 on rows with g_eff < TAU (A0 used) and >= TAU
    # carrier flag: E4' only
    flags: dict[str, Any] = {}
    if arm == "E4p":
        for k in ("E1p", "E2p"):
            rs.crps(k, arms[k])
        for s in SEASONS:
            full = -res["seasons"][str(s)]["delta"]["point"]
            alone = {
                "ROUTER": -rs.delta("E1p", "A0")[str(s)]["point"],
                "SEQ": -rs.delta("E2p", "A0")[str(s)]["point"],
            }
            without = {"ROUTER": alone["SEQ"], "SEQ": alone["ROUTER"]}
            flags[str(s)] = {
                "gain_full": full,
                "alone": alone,
                "without": without,
                **N.one_member_flag(full, alone, without),
            }
    res["flags"] = flags
    return res


def null_world(
    stat: str, w: dict[str, Any], gates: dict[str, np.ndarray]
) -> dict[str, dict[str, float]]:
    """SHUF-C: arm minus A0 on labels drawn from A0's own predictive distribution (the blend gate refit on them), per cell of this stat."""
    b = w["base"][stat]
    yp = N.a0_null_labels(b["a0_int"], seed=1)
    arms = build_arms_null(stat, w, gates, yp)
    out: dict[str, dict[str, float]] = {}
    for arm in ("E1p", "E2p", "E4p"):
        if arm not in arms or (arm != "E2p" and stat != "pts"):
            continue
        d = N.delta_by_season(stat, b, arms[arm], b["a0_int"], y=yp)
        out[cell_name(arm, stat)] = {s: v["point"] for s, v in d.items()}
    return out


def build_arms_null(
    stat: str, w: dict[str, Any], gates: dict[str, np.ndarray], yp: np.ndarray
) -> dict[str, np.ndarray]:
    """Only the three headline arms, blends refit on ``yp`` (the stored members and g' are untouched)."""
    b = w["base"][stat]
    dd, se = w["date_days"], b["season"]
    seq, a0, ge = w["seq"][stat], b["a0_int"], gates["geff"]
    pooled, _ = N.learned_blend(b["mix"], seq, yp, dd, se, w["ctx"], stat)
    out = {"E2p": route(a0, to_integer_support(pooled).astype(np.int16), ge)}
    if stat == "pts":
        r = N.router_grid(a0, w["a1_int"], ge, w["a1_present"])
        pooled4, _ = N.learned_blend(r.astype(float), seq, yp, dd, se, w["ctx"], stat)
        out["E1p"] = r
        out["E4p"] = route(r, to_integer_support(pooled4).astype(np.int16), ge)
    return out


def decide_cell(res: dict[str, Any], repro_ok: bool, bh: dict[str, float]) -> dict[str, Any]:
    seasons = res["seasons"]
    core = check_cell({s: seasons[s]["delta"] for s in seasons}, bh)
    guards = {s: all(seasons[s]["guards"].values()) for s in seasons}
    harm = any(
        seasons[s]["delta"]["point"] >= -FLOOR and seasons[s]["delta"]["lo"] > 0 for s in seasons
    )
    flag = bool(res["flags"]) and any(res["flags"][s]["flag"] for s in res["flags"])
    v = cell_verdict(
        harm,
        all(core.values()),
        all(guards.values()),
        all(res["placebo_ok"].values()),
        res["shuffle_ok"],
        flag,
        repro_ok,
    )
    return {"verdict": v, "core": core, "guards_ok": guards, "bh_p": bh, "flag": flag}


# --------------------------------------------------------------------------- g' descriptive (secondary)
def gate_metrics(
    gates: dict[str, np.ndarray], b: dict[str, Any], g_prod: np.ndarray
) -> dict[str, Any]:
    """Log loss / AUC / ECE of P(minutes >= 5 | plays) for g', the production g and the constant gate, plus g' minus g (clustered)."""
    from research.eval import workload_eval as WE

    ok = gates["has_row"]
    ev = (b["minutes"] >= 5).astype(float)
    out: dict[str, Any] = {}
    for s in SEASONS:
        m = ok & (b["season"] == s) & np.isfinite(gates["g_const"])
        short = (1.0 - ev[m]).astype(int)
        rec: dict[str, Any] = {"n": int(m.sum()), "n_short": int(short.sum())}
        for name, p in (("g_prime", gates["g"]), ("g_prod", g_prod), ("const", gates["g_const"])):
            rec[name] = {
                "logloss": float(WE._ll(p[m], ev[m]).mean()),
                "auc_short": float(E.auc(-p[m], short)),
                "ece": float(WE.ece(p[m], ev[m])),
            }
        d1 = WE._ll(gates["g"][m], ev[m]) - WE._ll(g_prod[m], ev[m])
        d2 = WE._ll(gates["g"][m], ev[m]) - WE._ll(gates["g_const"][m], ev[m])
        rec["g_prime_minus_g_prod_ll"] = WE.delta_stats(d1, b["game_id"][m])
        rec["g_prime_minus_const_ll"] = WE.delta_stats(d2, b["game_id"][m])
        ge = gates["geff"][m]
        rec["share_routed_to_A0"] = float((ge < TAU).mean())
        rec["share_short_routed"] = float((ge[short == 1] < TAU).mean())
        out[str(s)] = rec
    return out


# --------------------------------------------------------------------------- gate stage
def minutes_bucket_np(m: np.ndarray) -> np.ndarray:
    return N.minutes_bucket(m)


def stage_gate(recompute_repeat: bool = True, locked: bool = False) -> dict[str, Any]:
    """Minimum-data gates (non-negotiable 8). Fits only g' (real, placebo, shuffled, a repeat, a planted-future copy); no CRPS arm is scored."""
    from research.eval import workload_eval as WE

    t0 = time.time()
    w = N.load_world()
    base = w["base"]
    b0 = base["pts"]
    res: dict[str, Any] = {
        "id": ID,
        "doc_status": doc_status(),
        "doc_sha256": frozen_sha(),
        "checks": {},
    }
    ck = res["checks"]
    # G1: A0 reproduction
    g1: dict[str, Any] = {}
    for st in STATS:
        g1[st] = {}
        for i, s in enumerate(SEASONS):
            m = base[st]["season"] == s
            c = float(
                crps_from_quantiles(base[st]["a0_int"][m].astype(float), base[st]["y"][m]).mean()
            )
            ref = N.A0_REF[st][i]
            g1[st][str(s)] = {
                "crps": c,
                "ref": ref,
                "n": int(m.sum()),
                "pass": abs(c - ref) <= REPO_TOL,
            }
    g1["max_season"] = int(b0["season"].max())
    g1["game_id_prefixes"] = {
        k: int(v)
        for k, v in zip(*np.unique([x[:3] for x in b0["game_id"]], return_counts=True), strict=True)
    }
    g1["pass"] = bool(
        all(g1[st][str(s)]["pass"] for st in STATS for s in SEASONS)
        and g1["max_season"] <= MAX_SEASON
    )
    ck["G1_A0_reproduction"] = g1
    # G2: stored members reproduce the ledger (E1 pts, E2 learned all stats) and align
    e1_ref = json.loads((E.OUT_DIR / "e1_results.json").read_text())["e1"]
    arr = E.e1_arrays(b0, w["g"])
    rr = arr["rows"]
    sub = {
        k: (v[rr] if isinstance(v, np.ndarray) and len(v) == len(b0["y"]) else v)
        for k, v in b0.items()
    }
    g2: dict[str, Any] = {"E1_pts": {}, "E2_learned": {}}
    for s in SEASONS:
        m = sub["season"] == s
        d = crps_from_quantiles(arr["e1"][m].astype(float), sub["y"][m]) - crps_from_quantiles(
            arr["a0_int"][m].astype(float), sub["y"][m]
        )
        ref = e1_ref[str(s)]["delta"]["point"]
        g2["E1_pts"][str(s)] = {
            "delta": float(d.mean()),
            "stored": ref,
            "n": int(m.sum()),
            "pass": abs(float(d.mean()) - ref) <= REPO_TOL,
        }
    sc = json.loads((E.OUT_DIR / "score_results.json").read_text())["cells"]
    mde: dict[str, Any] = {}
    e2_by_stat: dict[str, np.ndarray] = {}
    for st in STATS:
        b = base[st]
        t1 = time.time()
        pooled, _ = N.learned_blend(
            b["mix"], w["seq"][st], b["y"], w["date_days"], b["season"], w["ctx"], st
        )
        q = to_integer_support(pooled).astype(np.int16)
        e2_by_stat[st] = q
        _log(f"G2: E2 learned {st} reproduced in {time.time() - t1:.0f}s")
        g2["E2_learned"][st] = {}
        for s in SEASONS:
            m = b["season"] == s
            d = crps_from_quantiles(q[m].astype(float), b["y"][m]) - crps_from_quantiles(
                b["a0_int"][m].astype(float), b["y"][m]
            )
            ref = sc[f"E2|learned|{st}"][str(s)]["delta"]["point"]
            ci = E.clustered(d, b["game_id"][m])
            g2["E2_learned"][st][str(s)] = {
                "delta": float(d.mean()),
                "stored": ref,
                "pass": abs(float(d.mean()) - ref) <= REPO_TOL,
            }
            mde.setdefault(st, {})[str(s)] = (ci["hi"] - ci["lo"]) / 2.0
    pos_a1 = w["a1_present"]
    bk = minutes_bucket_np(b0["minutes"])
    miss = [float((~pos_a1)[bk == k].mean()) for k in range(3)]
    g2["a1_present"] = int(pos_a1.sum())
    g2["a1_missing_gap_pp"] = 100.0 * (max(miss) - min(miss))
    g2["seq_rows_missing"] = 0
    g2["pass"] = bool(
        all(v["pass"] for v in g2["E1_pts"].values())
        and all(v["pass"] for st in STATS for v in g2["E2_learned"][st].values())
        and g2["a1_missing_gap_pp"] <= NULL_GAP_PP
        and g2["a1_present"] >= 0.99 * len(b0["y"])
    )
    ck["G2_members_reproduce"] = g2
    # G3: g' (real + placebo, shuffled, a repeat, a planted-future copy)
    fr, cfg, arms_cols = build_gate_inputs()
    with nullcontext() if locked else WE.heavy():
        real, _ = fit_gates(fr, cfg, arms_cols)
        shuf, _ = fit_gates(fr, cfg, {"G": arms_cols["G"]}, shuffle=True)
        rep = fit_gates(fr, cfg, {"G": arms_cols["G"]})[0] if recompute_repeat else None
        plant = _planted_future(fr, cfg, arms_cols, real)
    gates = make_gates(real, shuf, fr, b0)
    n_all = len(b0["y"])
    has = gates["has_row"]
    absent_pre = sorted({x[:3] for x in b0["game_id"][~has]})
    bkt = minutes_bucket_np(b0["minutes"])
    miss_b = [float((~has)[bkt == k].mean()) for k in range(3)]
    g3: dict[str, Any] = {
        "rows": n_all,
        "rows_with_g_prime": int(has.sum()),
        "rows_without": int((~has).sum()),
        "absent_by_season": {str(s): int((~has & (b0["season"] == s)).sum()) for s in SEASONS},
        "absent_game_prefixes": absent_pre,
        "absent_rate_by_minutes_bucket": dict(zip(N.G_BUCKETS, miss_b, strict=True)),
        "absent_gap_pp": 100.0 * (max(miss_b) - min(miss_b)),
        "absent_minutes_mean": float(b0["minutes"][~has].mean()) if (~has).any() else None,
        "finite_and_in_unit_interval": bool(
            np.isfinite(gates["g"][has]).all()
            and (gates["g"][has] >= 0).all()
            and (gates["g"][has] <= 1).all()
        ),
        "placebo_nan_rows": int((~np.isfinite(gates["g_pl"]) & has).sum()),
        "const_nan_rows": int((~np.isfinite(gates["g_const"]) & has).sum()),
        "repeat_max_abs_diff": None
        if rep is None
        else float(
            np.nanmax(
                np.abs(align_to_rows(rep, "p_G", b0["game_id"], b0["player_id"]) - gates["g"])
            )
        ),
        "planted_future": plant,
    }
    g3["pass"] = bool(
        g3["rows_with_g_prime"] >= 0.998 * n_all
        and set(absent_pre) <= {REGULAR_PREFIX}
        and g3["absent_gap_pp"] <= NULL_GAP_PP
        and g3["finite_and_in_unit_interval"]
        and g3["const_nan_rows"] == 0
        and (g3["repeat_max_abs_diff"] in (None, 0.0))
        and plant["pass"]
    )
    ck["G3_gate_builds"] = g3
    # G4: premise: g' ranks near-DNP rows better than the production g it replaces
    gm = gate_metrics(gates, b0, w["g"])
    g4 = {
        "metrics": gm,
        "pass": bool(
            all(
                gm[str(s)]["n_short"] >= MIN_SHORT_ROWS
                and gm[str(s)]["g_prime"]["auc_short"] >= G_AUC_FLOOR
                and gm[str(s)]["g_prime"]["auc_short"] > gm[str(s)]["g_prod"]["auc_short"]
                for s in SEASONS
            )
        ),
    }
    ck["G4_gate_premise"] = g4
    # G5: power (half the clustered CI of the member deltas, the noisiest ones, against the floor)
    ck["G5_power"] = {
        "mde": mde,
        "floor": FLOOR,
        "pass": bool(all(v <= MDE_MAX for st in mde.values() for v in st.values())),
    }
    # G6: guards testable: A0 absolute calibration, slice sizes, DK rows
    g6: dict[str, Any] = {"a0_guard": {}, "slice_n": {}, "dk_rows_pts": {}, "routed_slice_n": {}}
    for st in STATS:
        b = base[st]
        g6["a0_guard"][st], g6["slice_n"][st] = {}, {}
        for s in SEASONS:
            m = b["season"] == s
            cal = E.calibration(b["a0_int"][m], b["y"][m])
            g6["a0_guard"][st][str(s)] = {
                "bias": cal["bias"],
                "cov80": cal["cov80"],
                "ok": cal["guard"]["ok"],
            }
            mk = E.slice_masks(b, np.flatnonzero(m))
            g6["slice_n"][st][str(s)] = {k: int(mk[k].sum()) for k in N.GUARD_SLICES}
    for s in SEASONS:
        pos, _ = E.dk_rows(b0, np.flatnonzero(b0["season"] == s), s)
        g6["dk_rows_pts"][str(s)] = int(len(pos))
        m = b0["season"] == s
        ge = gates["geff"][m]
        short = b0["minutes"][m] < 5
        g6["routed_slice_n"][str(s)] = {
            "routed_to_A0": int((ge < TAU).sum()),
            "kept": int((ge >= TAU).sum()),
            "short_routed": int((short & (ge < TAU)).sum()),
            "short_kept": int((short & (ge >= TAU)).sum()),
            "postseason_rows": int(
                np.array([x[:3] != REGULAR_PREFIX for x in b0["game_id"][m]]).sum()
            ),
        }
    g6["a0_guard_all_ok"] = bool(
        all(g6["a0_guard"][st][str(s)]["ok"] for st in STATS for s in SEASONS)
    )
    g6["slices_testable"] = bool(
        all(
            n >= MIN_SLICE_N
            for st in STATS
            for s in SEASONS
            for n in g6["slice_n"][st][str(s)].values()
        )
    )
    g6["pass"] = bool(g6["a0_guard_all_ok"] and g6["slices_testable"])
    ck["G6_guards_testable"] = g6
    # G7: provenance (post clean-up artefacts) and holdout virginity of the models routed
    prov = {}
    for name, p in {
        "lower_tail components (A0)": ROOT / "reports/lower_tail/components_pts.npz",
        "A1 XGBoost aligned": E.A1_ALIGNED,
        "SEQ E2 OOF": N.SEQ_RUN / "oof_E2.npz",
    }.items():
        prov[name] = {
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "after_cleanup_rebaseline_2026-10-11_15:08": p.stat().st_mtime
            >= time.mktime(time.strptime("2026-10-11 15:08", "%Y-%m-%d %H:%M")),
        }
    log = HOLDOUT_LOG.read_text().splitlines() if HOLDOUT_LOG.exists() else []
    hits = [
        ln[:120] for ln in log if re.search(r"ENSEMBLE|seq_ensemble|XGB_PTS_VS_LT|a1_pts|SEQ", ln)
    ]
    ck["G7_provenance_and_virgin"] = {
        "inputs": prov,
        "holdout_log_rows_naming_A1_or_SEQ": hits,
        "pass": bool(
            all(v["after_cleanup_rebaseline_2026-10-11_15:08"] for v in prov.values()) and not hits
        ),
    }
    res["gate_pass"] = bool(all(v.get("pass", True) for v in ck.values()))
    res["seconds"] = time.time() - t0
    write_json(OUT_DIR / "gate.json", res)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(
        CACHE / "gates.npz",
        gid=b0["game_id"].astype(str),
        pid=b0["player_id"],
        **{k: np.asarray(v) for k, v in gates.items()},
    )
    return res


def _planted_future(
    fr: pl.DataFrame, cfg: Any, arms_cols: dict[str, list[str]], real: dict[str, np.ndarray]
) -> dict[str, Any]:
    """Overwrite every label and R column of rows dated on or after a cutoff with 48: g' of earlier rows must be bit-identical,
    and later blocks must change (the control is not inert)."""
    from research.eval import recent_role_eval as RR

    cut = pl.date(2025, 1, 24)
    cols = [c for c in RR.R_COLS if c in fr.columns]
    later = pl.col("game_date") >= cut
    ft = fr.with_columns(
        [pl.when(later).then(48.0).otherwise(pl.col(c)).alias(c) for c in [*cols, "min_real"]]
    )
    pl_ = fit_gates(ft, cfg, {"G": arms_cols["G"]})[0]
    k0 = pl.DataFrame(
        {"g": real["gid"].astype(str), "p": real["pid"].astype(np.int64), "v": real["p_G"]}
    )
    k1 = pl.DataFrame(
        {"g": pl_["gid"].astype(str), "p": pl_["pid"].astype(np.int64), "v2": pl_["p_G"]}
    )
    j = k0.join(k1, on=["g", "p"], how="inner")
    dates = fr.select(
        pl.col("game_id").alias("g"), pl.col("player_id").cast(pl.Int64).alias("p"), "game_date"
    ).unique(["g", "p"])
    j = j.join(dates, on=["g", "p"], how="left")
    before = j.filter(pl.col("game_date") < cut)
    after = j.filter(pl.col("game_date") >= pl.date(2025, 2, 1))
    d_before = float((before["v"] - before["v2"]).abs().max()) if before.height else float("nan")
    d_after = float((after["v"] - after["v2"]).abs().max()) if after.height else float("nan")
    return {
        "rows_before": int(before.height),
        "max_abs_diff_before_cutoff": d_before,
        "rows_after": int(after.height),
        "max_abs_diff_after_cutoff": d_after,
        "pass": bool(before.height > 0 and d_before == 0.0 and d_after > 1e-6),
    }


# --------------------------------------------------------------------------- run stage
def stage_run(sha: str | None, smoke: bool = False) -> dict[str, Any]:
    """Gate -> arms -> score -> report under ONE heavy.lock. ``smoke`` (no sha, writes only under OUT_DIR/smoke) replaces every gate
    vector by zeros, so each arm IS A0 and every delta must be exactly 0: it proves the plumbing without any candidate information."""
    from research.eval import workload_eval as WE

    sha_ok = "smoke" if smoke else require_frozen(sha)
    out_dir = OUT_DIR / "smoke" if smoke else OUT_DIR
    with WE.heavy():
        if smoke:
            if not (CACHE / "gates.npz").exists():
                raise SystemExit("smoke needs the cached gate arrays: run the gate stage first")
            gate: dict[str, Any] = {
                "gate_pass": True,
                "checks": {"smoke": {"pass": True, "metrics": {}}},
            }
        else:
            gate = stage_gate(
                locked=True
            )  # re-measured under the frozen text; a failure stops everything
            if not gate["gate_pass"]:
                raise SystemExit(
                    "STOP: a minimum-data gate failed under the frozen rule; no arm is scored (kill criterion)"
                )
        w = N.load_world()
        z = np.load(CACHE / "gates.npz", allow_pickle=True)
        b0 = w["base"]["pts"]
        if not (
            np.array_equal(z["gid"].astype(str), b0["game_id"])
            and np.array_equal(z["pid"], b0["player_id"])
        ):
            raise RuntimeError("cached gates are not aligned with the A0 rows")
        gates = {k: z[k] for k in z.files if k not in ("gid", "pid")}
        if smoke:
            gates = {k: (np.zeros_like(v) if k.startswith("geff") else v) for k, v in gates.items()}
        res: dict[str, Any] = {
            "rule_sha256": sha_ok,
            "tau": TAU,
            "ambiguities": AMBIGUITIES,
            "gate": {k: v.get("pass") for k, v in gate["checks"].items()},
            "gate_metrics": gate["checks"].get("G4_gate_premise", {}).get("metrics", {}),
            "cells": {},
        }
        repro_ok = bool(gate["gate_pass"])
        keep: dict[str, dict[str, dict[str, np.ndarray]]] = {}
        for st in STATS:
            t1 = time.time()
            _log(f"{st}: building arms")
            arms = build_arms(st, w, gates)
            _log(f"{st}: SHUF-C world")
            nulls = null_world(st, w, gates)
            rs = RowScores(w["base"][st])
            for arm in ("E1p", "E2p", "E4p"):
                if (arm, st) not in CELLS:
                    continue
                r = score_cell(arm, st, w, arms, rs, nulls)
                res["cells"][r["cell"]] = r
                keep.setdefault(arm, {})[st] = {"A0": arms["A0"], "B": arms[arm]}
                write_json(out_dir / "results_partial.json", res)
                pts_ = [round(r["seasons"][s]["delta"]["point"], 5) for s in r["seasons"]]
                _log(f"{r['cell']}: delta {pts_}")
            del arms
            _log(f"{st}: done in {time.time() - t1:.0f}s")
    bh: dict[str, dict[str, float]] = {}
    for s in SEASONS:
        p = np.array([res["cells"][cell_name(a, st)]["seasons"][str(s)]["p"] for a, st in CELLS])
        adj = bh_adjust(p)
        bh[str(s)] = {cell_name(a, st): float(v) for (a, st), v in zip(CELLS, adj, strict=True)}
    for a, st in CELLS:
        c = cell_name(a, st)
        res["cells"][c]["decision"] = decide_cell(
            res["cells"][c], repro_ok, {s: bh[s][c] for s in bh}
        )
    res["decision_score_dk_t60"] = {arm: N.decision_scores(w, v) for arm, v in keep.items()}
    res["eligible_for_holdout_row"] = [
        c for c, r in res["cells"].items() if r["decision"]["verdict"] == "PASS"
    ]
    write_json(out_dir / "results.json", res)
    (out_dir / "report.md" if smoke else REPORT_MD).write_text(render(res))
    return res


def _f(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else (f"{x:+.{nd}f}" if isinstance(x, (int, float)) else str(x))


def render(res: dict[str, Any]) -> str:
    L = [f"# {ID}: results under rule sha256 {res['rule_sha256'][:8]} (TAU {res['tau']})", ""]
    L += [
        "| cell | season | n | A0 | arm | delta [95% CI, game-clustered] | BH p | cov80 | guards |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c, r in res["cells"].items():
        for s, x in r["seasons"].items():
            d = x["delta"]
            bad = ",".join(k for k, v in x["guards"].items() if not v)
            L.append(
                f"| {c} | {s} | {x['n']} | {x['crps_a0']:.4f} | {x['crps_arm']:.4f} | {_f(d['point'])} [{_f(d['lo'])}, {_f(d['hi'])}] | {r['decision']['bh_p'][s]:.4f} | {x['calibration']['cov80']:.3f} | {'ok' if not bad else 'FAIL ' + bad} |"
            )
    L += ["", "| cell | verdict | placebo ok | shuffle ok | flag |", "|---|---|---|---|---|"]
    for c, r in res["cells"].items():
        d = r["decision"]
        L.append(f"| {c} | {d['verdict']} | {r['placebo_ok']} | {r['shuffle_ok']} | {d['flag']} |")
    L += [
        "",
        f"Eligible for a holdout row (Devin's word): {res['eligible_for_holdout_row'] or 'none'}",
    ]
    return "\n".join(L) + "\n"


def stage_holdout(sha: str | None) -> None:
    row = require_holdout_row(sha or "")
    raise SystemExit(
        f"holdout row present ({row[:80]}...) but no 2025 loader exists in this module: the touch is a separate committed script written after the row"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "run", "smoke", "holdout"])
    ap.add_argument("--sha", default=None)
    a = ap.parse_args(argv)
    if a.stage == "gate":
        r = stage_gate()
        print(
            json.dumps(
                {
                    "gate_pass": r["gate_pass"],
                    "checks": {k: v.get("pass") for k, v in r["checks"].items()},
                }
            )
        )
        return 0 if r["gate_pass"] else 1
    if a.stage == "holdout":
        stage_holdout(a.sha)
        return 0
    if a.stage == "smoke":
        r = stage_run(None, smoke=True)
        bad = {
            c: v
            for c, v in r["cells"].items()
            if any(abs(v["seasons"][s]["delta"]["point"]) > 0 for s in v["seasons"])
        }
        print(json.dumps({"smoke_all_zero": not bad}))
        return 0 if not bad else 1
    stage_run(a.sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
