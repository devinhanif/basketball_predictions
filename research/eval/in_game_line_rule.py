# ruff: noqa: E501
"""IN_GAME_LINE_MODEL rule constants and pure functions (rule: docs/prereg/IN_GAME_LINE_MODEL.md, DRAFT).

P(player finishes >= line | state at a game-minute checkpoint). Nothing here reads a database or an outcome. Season convention: ``season`` =
start year (2023 = 2023-24). Season 2025 is the frozen holdout: every game id is checked by ``check_game_id`` before any file is opened.
Every number the gate compares against is a constant below, fixed in code before the gate was computed.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats as sps

ID = "IN_GAME_LINE_MODEL"
STATS = ("pts", "reb", "ast", "fg3m")
PRIMARY = "pts"
SECONDARY = ("reb", "ast", "fg3m")
TRAIN_SEASONS = (2019, 2020, 2021, 2022)
SELECT = 2023
REPORT = 2024
ALL_SEASONS = (*TRAIN_SEASONS, SELECT, REPORT)
MAX_SEASON = 2024

# --- units: (player-game, stat, line, checkpoint)
LINES: dict[str, tuple[int, ...]] = {
    "pts": (10, 15, 20, 25, 30),
    "reb": (2, 4, 6, 8, 10),
    "ast": (2, 4, 6, 8),
    "fg3m": (1, 2, 3, 4),
}
# Game-minutes elapsed. End of each quarter (12, 24, 36) and every 6 game-minutes (6, 12, ..., 42) are the same grid; 48 is the end of regulation
# (nothing left to forecast) and is never a checkpoint. Overtime is never a checkpoint, but overtime production counts toward the final.
CHECKPOINTS = (6, 12, 18, 24, 30, 36, 42)
BUCKETS = (
    "Q1",
    "Q2",
    "Q3",
    "Q4",
)  # period of the checkpoint: 6, 12 -> Q1; 18, 24 -> Q2; 30, 36 -> Q3; 42 -> Q4
BLOWOUT_MARGIN = 20  # |score margin, player's team view| >= this
REGULATION_SECONDS = 2880
PERIOD_SECONDS = 720

# --- B0: linear pace with a PERIOD-AWARE minutes rule (Devin 2026-10-21 23:40: the blowout effect on minutes is margin x period, not margin alone)
# Expected remaining minutes = s * (non-Q4 remaining game minutes + Q4_FACTOR(margin) * Q4 remaining game minutes), where s is the shrunk share
# of game time the player has been on court. Benching is a Q4 phenomenon in the one night that motivated this rule (Booker 29 min through Q3,
# 0 in Q4), so margin only scales the Q4 part of the remaining time; through Q3 the minutes rule ignores the margin. Whether that is true is a
# descriptive gate item (SHAPE_*), not an assumption the pass depends on; arm M learns the shape from data.
SHARE_PRIOR_STARTER = 0.70  # prior on-court share of game time (starter), shrunk with SHARE_K game-minutes of pseudo-time
SHARE_PRIOR_BENCH = 0.40
SHARE_K = 6.0
RATE_K = 10.0  # pseudo-minutes of the league per-minute rate in the rate-so-far shrink
RATE_PRIOR = {
    "pts": 0.50,
    "reb": 0.18,
    "ast": 0.11,
    "fg3m": 0.05,
}  # per player-minute, constants, not fitted
Q4_FACTOR_BLOWOUT = 0.5  # |margin| >= BLOWOUT_MARGIN: the Q4 share of remaining time is halved
Q4_FACTOR_NORMAL = 1.0
# B0 / B1 count distribution: negative binomial on the integer grid with variance = VMR x mean (constants; the same recalibration is then applied
# to EVERY arm). Points use a larger VMR because points arrive in 1/2/3 units.
VMR = {"pts": 2.5, "reb": 1.4, "ast": 1.6, "fg3m": 1.3}
MEAN_FLOOR = 0.02

# --- metrics, inference, floors (log loss; floors are practical, negative = gain)
FLOORS = {"pts": 0.005, "reb": 0.005, "ast": 0.005, "fg3m": 0.005}
N_BOOT = 2000
SEED = 0
ALPHA = 0.05
CLUSTERINGS = ("game", "player_season", "week")
SLOPE_RANGE = (
    0.9,
    1.1,
)  # calibration slope of the recalibrated arm M on the report season (descriptive stat is logit-slope of y on logit p)
TOP5_SHARE_MAX = 0.5
M0_MARGIN = 0.0025  # Amendment 3 (M3): M must beat the re-expression control M0 (same LightGBM on b0_logit, e, line, need only) by at least this, CI upper < 0
SLICE_HURT_MAX = 0.005  # no declared slice may be hurt by more than this against either baseline
BH_Q = 0.05  # secondary family: reb, ast, fg3m on the report season

# --- minimum-data gate (label-free; thresholds fixed here before any number of this rule was computed)
REQUIRED_CELLS: tuple[tuple[str, bool], ...] = (
    ("Q1", False),
    ("Q2", False),
    ("Q3", False),
    ("Q4", False),
    ("Q3", True),
    ("Q4", True),
)  # (bucket, blowout) cells of the primary stat that must clear the count bars; Q1/Q2 blowout cells are reported, not required
MIN_CELL_PLAYER_GAMES = 250  # distinct (game, player) per required cell, per season
MIN_CELL_GAMES = 100
COMPLETE_MIN_REPORT = 0.95  # share of regular-season games fully reconstructed: 2023 and 2024
COMPLETE_MIN_TRAIN = 0.90  # 2019-2022
AST_RESOLVED_MIN = 0.95  # assists named in made-shot descriptions that resolve to a player id (readiness of the ast stat only; not a pts criterion)
FOULS_CONSISTENT_MIN = 0.95  # within a game, share of fouling players whose counted fouls equal their highest running personal-foul number
N_SEEDS = 20
NOISE_SIGMA = 0.50  # sd of the per-player-season logit shift of the null-noise arm (= POS_SIGMA: an arm as dispersed as the planted effect)
GATE_DE_BAR = 0.0025  # mean over seeds of the binding 95% half-width of the paired log-loss contrast, primary stat, BOTH seasons (half the floor)
POS_SEEDS = 5
POS_SIGMA = 0.50  # planted sd of the per-player-season logit shift, oracle arm: must reach the floor and clear zero in EVERY seed, both seasons
# Descriptive shape item (Devin 2026-10-21 23:40): starters' minutes by margin bucket x period. Reported, never a pass criterion.
SHAPE_Q3_RATIO_MIN = 0.85  # Q3 minutes of starters, margin at the end of Q2 >= 20 over margin < 10: "little or no effect through Q3"
SHAPE_Q4_RATIO_MAX = (
    0.70  # Q4 minutes of starters, margin at the end of Q3 >= 20 over margin < 10: "strong in Q4"
)
SHAPE_BUCKETS = ((0, 10), (10, 20), (20, 1000))  # |margin| buckets (lo inclusive, hi exclusive)


def check_season(season: int) -> int:
    if season > MAX_SEASON:
        raise SystemExit(f"season {season} > {MAX_SEASON}: the 2025 holdout is never read")
    return season


def check_game_id(game_id: str) -> str:
    """Regular-season ids only (``002`` + two-digit start-year suffix); anything from 2025-26 on is refused before a file is opened."""
    if not re.fullmatch(r"002\d{7}", game_id):
        raise SystemExit(f"not a regular-season game id: {game_id}")
    if 2000 + int(game_id[3:5]) > MAX_SEASON:
        raise SystemExit(f"game {game_id} is season >= 2025: the holdout is never read")
    return game_id


def is_regular(game_id: str) -> bool:
    return game_id.startswith("002")


def bucket_of(e: int) -> str:
    """Quarter bucket of a checkpoint (game minutes elapsed)."""
    if e not in CHECKPOINTS:
        raise ValueError(f"not a checkpoint: {e}")
    return BUCKETS[(e - 1) // 12]


def is_blowout(margin: Any) -> Any:
    return np.abs(np.asarray(margin)) >= BLOWOUT_MARGIN


def lines_open(stat: str, current: int) -> tuple[int, ...]:
    """Only lines not already reached."""
    return tuple(L for L in LINES[stat] if current < L)


def q4_factor(margin: Any) -> Any:
    return np.where(is_blowout(margin), Q4_FACTOR_BLOWOUT, Q4_FACTOR_NORMAL)


def b0_remaining_minutes(e: Any, minutes: Any, starter: Any, margin: Any) -> Any:
    """B0's period-aware minutes rule (see the constants block). ``e`` game minutes elapsed, ``minutes`` on court so far, ``starter`` 0/1,
    ``margin`` the player's team's lead. Returns expected remaining on-court minutes in regulation."""
    e = np.asarray(e, float)
    share0 = np.where(np.asarray(starter) > 0, SHARE_PRIOR_STARTER, SHARE_PRIOR_BENCH)
    share = (np.asarray(minutes, float) + SHARE_K * share0) / (e + SHARE_K)
    remaining = 48.0 - e
    q4_part = np.minimum(remaining, 12.0)
    return share * ((remaining - q4_part) + q4_factor(margin) * q4_part)


def b0_mean_remaining(stat: str, current: Any, minutes: Any, rem_minutes: Any) -> Any:
    """Linear pace: (current + prior pseudo-minutes) rate so far, times expected remaining minutes. Returns the expected REMAINING stat."""
    rate = (np.asarray(current, float) + RATE_K * RATE_PRIOR[stat]) / (
        np.asarray(minutes, float) + RATE_K
    )
    return rate * np.asarray(rem_minutes, float)


def nb_sf(stat: str, mean: Any, k: Any) -> Any:
    """P(Y >= k) for Y ~ NegBin(mean, variance = VMR x mean) on the integer grid (k <= 0 gives 1)."""
    mu = np.maximum(np.asarray(mean, float), MEAN_FLOOR)
    n = mu / (VMR[stat] - 1.0)
    p = n / (n + mu)
    kk = np.asarray(k)
    return np.where(kk <= 0, 1.0, sps.nbinom.sf(np.maximum(kk, 1) - 1, n, p))


def b0_prob(
    stat: str, line: Any, current: Any, minutes: Any, e: Any, starter: Any, margin: Any
) -> Any:
    """B0: P(final >= line) from linear pace and the period-aware minutes rule, remaining stat ~ NegBin on the integer grid."""
    rem = b0_remaining_minutes(e, minutes, starter, margin)
    mu = b0_mean_remaining(stat, current, minutes, rem)
    return nb_sf(stat, mu, np.asarray(line) - np.asarray(current))


def b1_prob(stat: str, line: Any, current: Any, pregame_mean: Any) -> Any:
    """B1: the pre-game count distribution conditioned on the current stat: P(X >= line) / P(X >= current), X ~ NegBin(pregame_mean)."""
    num = nb_sf(stat, pregame_mean, np.asarray(line))
    den = np.maximum(nb_sf(stat, pregame_mean, np.asarray(current)), 1e-12)
    return np.minimum(num / den, 1.0)


def logit(p: Any) -> Any:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigmoid(z: Any) -> Any:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(z, float), -30.0, 30.0)))


def log_loss_rows(p: Any, y: Any) -> Any:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def brier_rows(p: Any, y: Any) -> Any:
    return (np.asarray(p, float) - np.asarray(y, float)) ** 2


# --- clustered paired bootstrap (rows are the units; clusters are given as integer codes)
def cluster_sums(delta: np.ndarray, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = int(codes.max()) + 1 if len(codes) else 0
    return np.bincount(codes, weights=delta, minlength=n), np.bincount(codes, minlength=n).astype(
        float
    )


def boot_draws(
    delta: np.ndarray, codes: np.ndarray, seed: int = SEED, n_boot: int = N_BOOT
) -> tuple[float, np.ndarray]:
    """(point, bootstrap means) resampling whole clusters (ratio of resampled cluster sums to resampled cluster sizes)."""
    s, c = cluster_sums(delta, codes)
    k = len(s)
    point = float(s.sum() / c.sum())
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    step = 250
    for i in range(0, n_boot, step):
        m = min(step, n_boot - i)
        w = rng.multinomial(k, np.full(k, 1.0 / k), size=m).astype(float)
        out[i : i + m] = (w @ s) / np.maximum(w @ c, 1.0)
    return point, out


def boot_ci(
    delta: np.ndarray,
    codes: np.ndarray,
    seed: int = SEED,
    n_boot: int = N_BOOT,
    alpha: float = ALPHA,
) -> tuple[float, float, float]:
    """(point, lo, hi) of the mean paired delta, resampling whole clusters."""
    point, out = boot_draws(delta, codes, seed, n_boot)
    lo, hi = np.quantile(out, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)


def binding_p(
    delta: np.ndarray, codes: dict[str, np.ndarray], seed: int = SEED, n_boot: int = N_BOOT
) -> float:
    """One-sided bootstrap p-value for delta < 0 (share of resampled means >= 0, add-one); the LARGEST over the three clusterings binds."""
    ps = []
    for k in CLUSTERINGS:
        _, d = boot_draws(delta, codes[k], seed, n_boot)
        ps.append((1.0 + float(np.sum(d >= 0.0))) / (n_boot + 1.0))
    return max(ps)


def binding_ci(
    delta: np.ndarray, codes: dict[str, np.ndarray], seed: int = SEED, n_boot: int = N_BOOT
) -> dict[str, Any]:
    """The widest of the game, player-season and ISO-week clusterings binds (CLUSTERINGS)."""
    res = {k: boot_ci(delta, codes[k], seed, n_boot) for k in CLUSTERINGS}
    bind = max(CLUSTERINGS, key=lambda k: res[k][2] - res[k][1])
    point, lo, hi = res[bind]
    return {
        "point": point,
        "lo": lo,
        "hi": hi,
        "half": (hi - lo) / 2,
        "binding": bind,
        "by": {k: (v[2] - v[1]) / 2 for k, v in res.items()},
    }


def top5_share(delta: np.ndarray, player_season: np.ndarray) -> float:
    """Share of the total gain (-sum delta over players with a net gain) carried by the 5 largest player-season gains. Gain-positive only."""
    s, _ = cluster_sums(-delta, player_season)
    pos = np.sort(s[s > 0])[::-1]
    return float(pos[:5].sum() / pos.sum()) if len(pos) else 1.0


def calibration_slope(p: np.ndarray, y: np.ndarray) -> float:
    """Slope of y on logit(p) from a one-feature logistic fit with intercept (Newton, 25 steps; 1.0 = calibrated)."""
    x = logit(p)
    a, b = 0.0, 1.0
    for _ in range(25):
        q = sigmoid(a + b * x)
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        w = q * (1 - q)
        h = np.array(
            [[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]]
        ) + 1e-9 * np.eye(2)
        a, b = np.array([a, b]) + np.linalg.solve(h, g)
    return float(b)


def platt_fit(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """The 2-parameter recalibration (intercept, slope on logit p), fitted on training rows only and applied identically to EVERY arm."""
    x = logit(p)
    a, b = 0.0, 1.0
    for _ in range(25):
        q = sigmoid(a + b * x)
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        w = q * (1 - q)
        h = np.array(
            [[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]]
        ) + 1e-9 * np.eye(2)
        a, b = np.array([a, b]) + np.linalg.solve(h, g)
    return float(a), float(b)


def platt_apply(p: np.ndarray, ab: tuple[float, float]) -> np.ndarray:
    return np.clip(np.asarray(sigmoid(ab[0] + ab[1] * logit(p))), 1e-4, 1 - 1e-4)


def bh_reject(pvals: Sequence[float], q: float = BH_Q) -> list[bool]:
    m = len(pvals)
    order = np.argsort(pvals)
    ok = [False] * m
    thresh = -1
    for rank, i in enumerate(order, 1):
        if pvals[i] <= q * rank / m:
            thresh = rank
    for rank, i in enumerate(order, 1):
        ok[i] = rank <= thresh
    return ok


def gate_cell_ok(cell: dict[str, int]) -> bool:
    return cell["player_games"] >= MIN_CELL_PLAYER_GAMES and cell["games"] >= MIN_CELL_GAMES


def shape_check(q3_ratio: float | None, q4_ratio: float | None) -> dict[str, Any]:
    """Descriptive: 'little or no effect through Q3, strong in Q4' (reported; the gate verdict does not depend on it)."""
    return {
        "q3_ratio": q3_ratio,
        "q4_ratio": q4_ratio,
        "q3_little_or_none": None if q3_ratio is None else q3_ratio >= SHAPE_Q3_RATIO_MIN,
        "q4_strong": None if q4_ratio is None else q4_ratio <= SHAPE_Q4_RATIO_MAX,
    }


def primary_verdict(
    select_vs_b0: float,
    select_vs_b1: float,
    report_vs_b0: dict[str, Any],
    report_vs_b1: dict[str, Any],
    report_top5_share: float,
    report_slope: float,
    slice_deltas: dict[str, float],
    report_vs_m0: dict[str, Any],
    planted_future_ok: bool,
    truncation_ok: bool,
) -> dict[str, Any]:
    """The only primary claim (pts, report season 2024; 2023 selects). ``select_*`` are the 2023 point estimates of the log-loss delta (arm M minus
    the BINDING baseline, negative = gain); ``report_vs_*`` are ``binding_ci`` dicts on 2024 against the binding B0 (the better of the pooled and the
    per-checkpoint recalibrated B0) and the binding B1 (the best of the pooled, per-checkpoint and stored-grid B1); ``slice_deltas`` are the worst-case
    (max over the two binding baselines) 2024 slice deltas; ``report_vs_m0`` is M minus the re-expression control M0. ``planted_future_ok`` and
    ``truncation_ok`` must hold in both seasons. A failed criterion voids the claim; there is no partial pass."""
    f = FLOORS[PRIMARY]
    crit = {
        "select_point_vs_b0": select_vs_b0 <= -f,
        "select_point_vs_b1": select_vs_b1 <= -f,
        "report_point_vs_b0": report_vs_b0["point"] <= -f,
        "report_point_vs_b1": report_vs_b1["point"] <= -f,
        "ci_upper_below_zero_vs_b0": report_vs_b0["hi"] < 0.0,
        "ci_upper_below_zero_vs_b1": report_vs_b1["hi"] < 0.0,
        "top5_share": report_top5_share <= TOP5_SHARE_MAX,
        "calibration_slope": SLOPE_RANGE[0] <= report_slope <= SLOPE_RANGE[1],
        "no_slice_hurt": all(v <= SLICE_HURT_MAX for v in slice_deltas.values()),
        "beats_reexpression_control_m0": report_vs_m0["point"] <= -M0_MARGIN
        and report_vs_m0["hi"] < 0.0,
        "planted_future_identical": bool(planted_future_ok),
        "truncation_identical": bool(truncation_ok),
    }
    return {
        "criteria": crit,
        "pass": all(crit.values()),
        "failed": [k for k, v in crit.items() if not v],
    }


# --------------------------------------------------------------------------- freeze and harness pin (the pattern of ARCHETYPE_MATCHUP_ALL)
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/IN_GAME_LINE_MODEL.md"
HARNESS_FILES: tuple[str, ...] = (
    "research/eval/in_game_line_rule.py",
    "research/eval/in_game_line_features.py",
    "research/eval/in_game_line_gate.py",
    "research/eval/in_game_line_eval.py",
)
# Modules a number can depend on that are NOT byte-pinned: library versions are declared (LIB_*) and asserted before any outcome is read.
LIB_NAMES = ("python", "polars", "lightgbm", "numpy", "scipy", "duckdb")
_DECL = re.compile(r"^((?:PIN|LIB)_[A-Za-z0-9_.]+): (\S+)$")


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


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=ROOT).returncode == 0
    )


def text_sha(text: str) -> str:
    return hashlib.sha256(text.split(MARKER)[0].encode()).hexdigest()


def pin_of(blobs: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--all", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
            return h
    return None


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    for line in run("log", "--all", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.fullmatch(rf"harness\({ID}\): ([0-9a-f]{{64}})", subj.strip())
        if m:
            return h, m.group(1)
    return None


def require_harness_pinned(
    sha: str, run: Callable[..., str] = git, root: Path = ROOT, ok: Callable[..., bool] = git_ok
) -> dict[str, str]:
    """Refuse unless the rule is frozen, a ``harness(ID): <pin>`` commit descends from the freeze commit, and every harness file on disk is
    byte-identical to its version at both commits and hashes to the pin."""
    fc = freeze_commit(sha, run)
    if fc is None:
        raise RuntimeError("the rule is not frozen")
    pc = pin_commit(run)
    if pc is None:
        raise RuntimeError(f"no `harness({ID}): <pin>` commit: the harness is not pinned")
    h, pin = pc
    out: dict[str, str] = {}
    for rel in HARNESS_FILES:
        frozen = run("show", f"{h}:{rel}")
        if frozen != (root / rel).read_text():
            raise RuntimeError(f"{rel} differs from its version at pin commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(frozen.encode()).hexdigest()
        if run("show", f"{fc}:{rel}") != frozen:
            raise RuntimeError(
                f"{rel} differs between the freeze commit and the pin commit: refusing"
            )
    if pin_of(out) != pin:
        raise RuntimeError("the pin in the commit subject does not match the harness blobs")
    if not ok("merge-base", "--is-ancestor", fc, h):
        raise RuntimeError("pin commit does not descend from the freeze commit")
    return out


def versions() -> dict[str, str]:
    import sys

    import duckdb
    import lightgbm
    import numpy
    import polars
    import scipy

    return {
        "LIB_python": ".".join(map(str, sys.version_info[:3])),
        "LIB_polars": polars.__version__,
        "LIB_lightgbm": lightgbm.__version__,
        "LIB_numpy": numpy.__version__,
        "LIB_scipy": scipy.__version__,
        "LIB_duckdb": duckdb.__version__,
    }


def declared(doc_text: str) -> dict[str, str]:
    """``PIN_*`` / ``LIB_*`` lines (``NAME: value``) above the results marker of the rule text."""
    out: dict[str, str] = {}
    for line in doc_text.split(MARKER)[0].splitlines():
        m = _DECL.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def input_mismatches(decl: dict[str, str], actual: dict[str, str]) -> list[str]:
    bad = [
        f"{k}: declared {decl.get(k)!r}, recomputed {v!r}"
        for k, v in actual.items()
        if decl.get(k) != v
    ]
    bad += [f"{k}: declared but not recomputed" for k in decl if k not in actual]
    return bad
