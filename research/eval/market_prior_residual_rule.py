# ruff: noqa: E501
"""MARKET_PRIOR_RESIDUAL: frozen constants, refusals and verdict logic (numpy only, no heavy imports at module load).

Rule: docs/prereg/MARKET_PRIOR_RESIDUAL.md (mirrored here; the doc wins on any disagreement, and the harness pin below
makes a later edit of this file visible). Nothing that scores an outcome may run unless
(1) the doc is no longer marked DRAFT, (2) the text above ``=== RESULTS BELOW ===`` hashes to ``--sha``, and a commit whose
subject is ``prereg(MARKET_PRIOR_RESIDUAL): <sha>`` is reachable from HEAD and carries a copy of the rule that hashes to the same
value, (3) the harness files equal their blobs at that commit. Season 2025 is never loaded by this rule: ``assert_seasons`` raises.

CI code: ``nba/truth/bootstrap.py`` (named in the brief) does not exist at base 85a928f. The only cluster-bootstrap used is
``research.eval.model_clv_eval.Boot`` (the LINE_CALIBRATION precedent), wrapped by ``cluster_ci`` below; swapping the import for
``nba.truth.bootstrap`` when it lands is a one-line change that the harness pin will show.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

ID = "MARKET_PRIOR_RESIDUAL"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/MARKET_PRIOR_RESIDUAL.md"
DOC = ROOT / DOC_REL

# ---- frozen constants (each is justified in the doc's "What each choice protects against") ----
SELECT_SEASON = 2023
REPORT_SEASON = 2024
MAX_SEASON = 2024  # 2025 is the frozen holdout: never loaded, counted or described by this rule
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
BOOK = "pinnacle"
SNAPSHOT_KIND = "t60"
MIN_LEAD_MIN = 60.0  # hard known-at filter: snapshot_ts <= tipoff_utc - 60 min
FLOOR = -0.003  # LINE_CALIBRATION P1 floor
N_BOOT = 2000
SEED = 0
CLUSTERINGS: tuple[str, ...] = ("game", "player_season", "week")
MIN_WEEKS = (
    20  # week clustering is reported only (no decision weight) below this many weeks in a season
)
N_MIN_TRAIN = 3000  # pooled prior Pinnacle legs before the first scored month
HALF_LIFE_DAYS = 365.0  # same fixed half-life as LINE_CALIBRATION; never tuned
K_PLACEBO = 50  # permutation null size (BEST_PRACTICES 2026-10-12: K >= 50)
P_CLIP = 1e-6  # market probabilities are clipped here only to take a logit
MODEL_CLIP = 0.5 / 199  # production probabilities, as ODDS_HISTORY / LINE_CALIBRATION
LGB_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.02,
    "n_estimators": 200,
    "num_leaves": 4,
    "max_depth": 3,
    "min_child_samples": 100,
    "subsample": 0.7,
    "subsample_freq": 1,
    "colsample_bytree": 0.5,
    "reg_lambda": 10.0,
    "random_state": 0,
    "deterministic": True,
    "force_row_wise": True,
    "n_jobs": 1,
    "verbose": -1,
}
# Gate bars (doc "Minimum-data gate")
GATE_MIN_LEGS_POOLED = 20_000
GATE_MIN_LEGS_STAT = 5_000
GATE_MIN_GAMES = 1_000
GATE_MIN_PLAYER_SEASONS = 200
GATE_MAX_HALFWIDTH = 0.0015
GATE_MAX_NULL_BIAS = 0.0010  # R minus M under a world where the outcome is drawn from M
GATE_TOL = 1e-9
REPORT_COLUMNS = (
    "has_report",
    "since_flag",
    "flag_dir",
    "since_team",
    "n_out_rot",
    "vac_min",
    "vac_own",
)  # knockout R_NR drops these
LABEL_COLUMNS = (
    "y",
    "over",
)  # the label-free gate must never select these (except G6, which says so)


# ---- refusals ----
def assert_seasons(seasons: Iterable[int]) -> None:
    for s in seasons:
        if int(s) > MAX_SEASON:
            raise ValueError(
                f"season {s} > {MAX_SEASON}: 2025 is the frozen holdout; this rule never touches it"
            )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (the bytes of ``awk '/^=== RESULTS BELOW ===$/{exit} {print}'``)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def doc_status(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Status:"):
            return line
    return "Status: MISSING"


def require_not_draft(doc: Path = DOC) -> None:
    st = doc_status(doc.read_text())
    if "DRAFT" in st or "FROZEN" not in st:
        raise RuntimeError(f"{doc.name} is not frozen ({st[:80]!r}): refusing to run")


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, run: Callable[..., str] = git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    require_not_draft(doc)
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, run) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carrying this rule in git log: the rule is not frozen"
        )
    return sha


HARNESS_FILES = (
    "research/eval/market_prior_residual_eval.py",
    "research/eval/market_prior_residual_rule.py",
    "research/eval/market_prior_residual_gate.py",
    "research/eval/model_clv_eval.py",
)


def require_harness_pinned(sha: str, run: Callable[..., str] = git) -> dict[str, str]:
    h = freeze_commit(sha, run)
    if h is None:
        raise RuntimeError("the rule is not frozen")
    out: dict[str, str] = {}
    for rel in HARNESS_FILES:
        frozen = run("show", f"{h}:{rel}")
        disk = (ROOT / rel).read_text()
        if frozen != disk:
            raise RuntimeError(f"{rel} differs from its version at freeze commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(disk.encode()).hexdigest()
    return out


# ---- known-at ----
def lead_minutes(snapshot_ts: np.ndarray, tipoff_utc: np.ndarray) -> np.ndarray:
    """Minutes between the stored snapshot and the real tip (positive = before tip). NaT -> NaN."""
    a = np.asarray(snapshot_ts, dtype="datetime64[us]")
    b = np.asarray(tipoff_utc, dtype="datetime64[us]")
    d = (b - a).astype("timedelta64[s]").astype(np.float64) / 60.0
    d[np.isnat(a) | np.isnat(b)] = np.nan
    return np.asarray(d)


def known_at_keep(
    snapshot_ts: np.ndarray, tipoff_utc: np.ndarray, min_lead: float = MIN_LEAD_MIN
) -> np.ndarray:
    """True where snapshot_ts <= tip - min_lead minutes. A missing tip or snapshot is NOT kept (never a proxy tip)."""
    lead = lead_minutes(snapshot_ts, tipoff_utc)
    return np.isfinite(lead) & (lead >= min_lead - 1e-9)


# ---- probabilities and losses ----
def logit(p: np.ndarray, eps: float = P_CLIP) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1.0 - eps)
    return np.log(p / (1.0 - p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def log_loss_vec(p: np.ndarray, y: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1.0 - eps)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def brier_vec(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.asarray((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2)


def time_weights(game_dates: np.ndarray, block_start: np.datetime64) -> np.ndarray:
    """0.5 ** (age_days / HALF_LIFE_DAYS), age counted from the block's first day; rows must be strictly earlier."""
    age = (np.datetime64(block_start, "D") - np.asarray(game_dates, dtype="datetime64[D]")).astype(
        np.float64
    )
    if (age <= 0).any():
        raise ValueError("a fit row is not strictly before its block: walk-forward violated")
    return 0.5 ** (age / HALF_LIFE_DAYS)


# ---- clusters and CIs ----
def cluster_codes(df: Any, kind: str) -> np.ndarray:
    """Dense integer cluster codes for ``kind`` in CLUSTERINGS from a polars frame with game_id, player_id, season, game_date."""
    import polars as pl

    if kind == "game":
        key = df["game_id"].cast(pl.Utf8)
    elif kind == "player_season":
        key = df["player_id"].cast(pl.Utf8) + "|" + df["season"].cast(pl.Utf8)
    elif kind == "week":
        gd = df["game_date"]
        key = gd.dt.iso_year().cast(pl.Utf8) + "-" + gd.dt.week().cast(pl.Utf8)
    else:
        raise ValueError(kind)
    return np.unique(key.to_numpy(), return_inverse=True)[1].astype(np.int64)


def n_clusters(codes: np.ndarray) -> int:
    return int(np.unique(codes).size)


def cluster_ci(
    v: np.ndarray, codes: np.ndarray, seed: int = SEED, B: int | None = None
) -> dict[str, Any]:
    """Cluster-bootstrap mean of ``v`` (percentile 95% CI, one-sided p for mean >= 0 failing, via Boot)."""
    from research.eval.model_clv_eval import Boot

    B = N_BOOT if B is None else B
    G = int(codes.max()) + 1
    return Boot(codes, G, seed, B).mean(np.asarray(v, dtype=float), np.ones(len(v), dtype=bool))


def envelope(cis: dict[str, dict[str, Any]], decisive: Iterable[str]) -> dict[str, Any]:
    """Binding CI over the decisive clusterings: [min lo, max hi] (at least as wide as the widest single one); p = max p."""
    ks = [k for k in decisive if cis[k].get("lo") is not None]
    if not ks:
        return {"lo": None, "hi": None, "p": None, "point": None, "binding": None}
    wid = {k: cis[k]["hi"] - cis[k]["lo"] for k in ks}
    return {
        "point": cis[ks[0]]["mean"],
        "lo": min(cis[k]["lo"] for k in ks),
        "hi": max(cis[k]["hi"] for k in ks),
        "p": max(cis[k]["p"] for k in ks),
        "binding": max(ks, key=lambda k: wid[k]),
    }


def decisive_clusterings(n_weeks: int) -> tuple[str, ...]:
    return CLUSTERINGS if n_weeks >= MIN_WEEKS else ("game", "player_season")


# ---- the disagreement regression ----
def fit_logit(
    X: np.ndarray,
    y: np.ndarray,
    w: np.ndarray | None = None,
    beta0: np.ndarray | None = None,
    iters: int = 25,
    ridge: float = 1e-8,
) -> np.ndarray:
    """Weighted logistic regression by Newton steps (no penalty beyond a numerical 1e-8 ridge on the slopes)."""
    n, k = X.shape
    w = np.ones(n) if w is None else w
    beta = np.zeros(k) if beta0 is None else beta0.copy()
    R = np.eye(k) * ridge
    R[0, 0] = 0.0
    for _ in range(iters):
        p = sigmoid(X @ beta)
        g = X.T @ (w * (y - p)) - R @ beta
        H = (X * (w * p * (1 - p))[:, None]).T @ X + R + np.eye(k) * 1e-12
        step = np.linalg.solve(H, g)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


def disagreement_design(p_m: np.ndarray, p_r: np.ndarray) -> np.ndarray:
    lm = logit(p_m)
    return np.column_stack([np.ones(len(lm)), lm, logit(p_r) - lm])


def disagreement_boot(
    y: np.ndarray,
    p_m: np.ndarray,
    p_r: np.ndarray,
    codes: np.ndarray,
    seed: int = SEED,
    B: int | None = None,
) -> dict[str, Any]:
    """Coefficient on (logit p_R - logit p_M) in logit P(over) ~ 1 + logit p_M + (logit p_R - logit p_M), with a cluster-bootstrap CI.

    Each resample reweights rows by their cluster's multinomial count and refits from the point estimate."""
    from research.eval.model_clv_eval import Boot

    B = N_BOOT if B is None else B
    X = disagreement_design(p_m, p_r)
    yy = np.asarray(y, dtype=float)
    b0 = fit_logit(X, yy)
    G = int(codes.max()) + 1
    W = Boot(codes, G, seed, B).W
    est = np.empty(B)
    for b in range(B):
        est[b] = fit_logit(X, yy, W[b][codes], b0, iters=12)[2]
    lo, hi = np.percentile(est, [2.5, 97.5])
    return {
        "coef": float(b0[2]),
        "coef_market": float(b0[1]),
        "intercept": float(b0[0]),
        "lo": float(lo),
        "hi": float(hi),
        "se": float(est.std(ddof=1)),
        "p": float((1 + np.sum(est <= 0)) / (B + 1)),
        "n": int(len(yy)),
        "sd_d": float(X[:, 2].std()),
    }


# ---- placebo ----
def within_group_derangement(groups: np.ndarray, seed: int) -> np.ndarray:
    """``out[i]`` = another row of i's group (a derangement: no row maps to itself). Groups of size 1 are pooled together.

    Used to give each leg the production-feature block of a different leg of the same (date, stat), so the market columns, the
    label and the offset stay on the row while the features belong to someone else. A random order shifted by one place is a single
    cycle, hence a derangement."""
    rng = np.random.default_rng(seed)
    codes = np.unique(groups, return_inverse=True)[1]
    sizes = np.bincount(codes)
    single = sizes[codes] == 1
    out = np.arange(len(codes))
    for g in np.unique(codes[~single]):
        rows = np.flatnonzero(codes == g)
        perm = rng.permutation(rows)
        out[perm] = np.roll(perm, -1)
    s = np.flatnonzero(single)
    if len(s) > 1:
        perm = rng.permutation(s)
        out[perm] = np.roll(perm, -1)
    return out


# ---- verdict ----
def verdict(
    a: dict[str, Any], b: dict[str, Any], c: dict[str, Any], stage1_sign_ok: bool = True
) -> dict[str, Any]:
    """Pure verdict logic. a, b, c are dicts with the report-season numbers:

    a: {"point", "hi"}  R minus M per-leg log loss (binding CI upper ``hi``)
    b: {"coef", "lo"}   disagreement coefficient (binding CI lower ``lo``)
    c: {"point", "hi"}  R minus PL per-leg log loss
    """
    pa = a["point"] is not None and a["point"] <= FLOOR and a["hi"] is not None and a["hi"] < 0
    pb = b["coef"] is not None and b["coef"] > 0 and b["lo"] is not None and b["lo"] > 0
    pc = c["point"] is not None and c["point"] <= FLOOR and c["hi"] is not None and c["hi"] < 0
    pc_sign = (
        c["point"] is not None and c["point"] < 0 and c["hi"] is not None and c["hi"] < 0
    )  # beats the placebo at all
    worse = a.get("lo") is not None and a["lo"] > 0
    checks = {
        "a_floor": bool(pa),
        "b_disagreement": bool(pb),
        "c_beats_placebo_floor": bool(pc),
        "c_beats_placebo_sign": bool(pc_sign),
        "stage1_sign": bool(stage1_sign_ok),
    }
    if pa and pb and pc:
        word = "PASS" if stage1_sign_ok else "FAIL_SELECT_SIGN"
    elif pb and pc_sign:
        word = "INFO_BELOW_FLOOR"  # the line is not efficient w.r.t. our features, but by less than the practical floor
    elif pb:
        word = "PLACEBO_EXPLAINS"  # disagreement predicts outcomes, but R is no better than the same fit on scrambled features
    elif pa:
        word = "FAIL_NO_DISAGREEMENT_SIGNAL"
    elif worse:
        word = "FAIL_WORSE_THAN_MARKET"
    else:
        word = "FAIL_NO_GAIN"
    return {"word": word, "checks": checks}


def stage1_sign(coef: float | None) -> bool:
    """2023 select-season sign condition: the disagreement coefficient is positive (point estimate only).

    R minus M is deliberately not part of it: the gate's null rehearsal (outcomes drawn from the market) shows R costs +0.0005 per leg
    on 2023 by overfitting alone, which would mask a true gain of that size."""
    return coef is not None and coef > 0


def bh_adjust(p: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (monotone)."""
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        adj[i] = prev
    return adj.tolist()
