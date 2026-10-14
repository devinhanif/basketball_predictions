"""Shared pieces of the ALT_LINE_70 and ALT_PARLAY_X run harnesses (docs/prereg/ALT_LINE_70.md and
docs/prereg/ALT_PARLAY_X.md, both FROZEN on main).

Nothing here decides anything: it hashes rule text the way the freeze did, looks for the freeze
commit, checks a status line, and provides the cluster bootstrap, BH, American-odds and weekly
plan-simulation arithmetic that the rules specify. The rules are the source of truth; each function
names the sentence it implements.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

RESULTS_MARK = "=== RESULTS BELOW ==="
B_DEFAULT = 5_000
SEED_GAME = 2026  # game-clustered bootstrap (ALT_LINE_70 Part 1 CI)
SEED_DATE = 2027  # date-clustered bootstrap
SEED_WEEK = 2028  # week-clustered sensitivity (ALT_PARLAY_X T2, descriptive)
PLAN_SEED = 2026
PLAN_R = 2_000
PLAN_BETS_PER_WEEK = 10
PLAN_STAKE = 2.0


class PreregError(RuntimeError):
    """A rule, a commit, a hash or a pin does not match: the harness must not score."""


# ------------------------------------------------------------------------------ rule identity


def rule_text_above_mark(path: Path) -> str:
    """Everything above the line ``=== RESULTS BELOW ===``, each line newline-terminated: exactly
    what ``awk '/^=== RESULTS BELOW ===$/{exit} {print}'`` prints."""
    out: list[str] = []
    for line in path.read_text().splitlines():
        if line == RESULTS_MARK:
            return "".join(x + "\n" for x in out)
        out.append(line)
    raise PreregError(f"{path} has no '{RESULTS_MARK}' line")


def rule_sha256(path: Path) -> str:
    return hashlib.sha256(rule_text_above_mark(path).encode()).hexdigest()


def frozen_status(path: Path) -> bool:
    """The first ``Status:`` line of the rule begins ``Status: **FROZEN``."""
    for line in path.read_text().splitlines():
        if line.startswith("Status:"):
            return line.startswith("Status: **FROZEN")
    return False


def git_prereg_shas(rule_id: str, repo: Path) -> list[str]:
    """sha256 values in commit subjects ``prereg(<rule_id>): <64 hex>`` reachable from HEAD."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "log", "HEAD", "--format=%s", "--fixed-strings"]
            + [f"--grep=prereg({rule_id}): "],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    pat = re.compile(rf"^prereg\({re.escape(rule_id)}\): ([0-9a-f]{{64}})\s*$")
    return [m.group(1) for s in res.stdout.splitlines() if (m := pat.match(s))]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [{"check": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks]

    def require(self) -> None:
        bad = self.failures()
        if bad:
            raise PreregError(
                "refusing to run: " + "; ".join(f"{c.name} ({c.detail})" for c in bad)
            )


def check_rule(
    pf: Preflight,
    rule_path: Path,
    rule_id: str,
    repo: Path,
    expected_sha: str | None = None,
    lookup: Callable[[str, Path], list[str]] = git_prereg_shas,
) -> str | None:
    """Rule text hash, freeze commit and FROZEN status. ``expected_sha`` pins a rule whose sha is
    known now (ALT_LINE_70, ALT_PARLAY_X); ``None`` (ALT_LADDER_HIST) accepts any sha that a
    ``prereg(<id>): <sha>`` commit also carries."""
    if not rule_path.exists():
        pf.add(f"{rule_id}: rule file exists", False, str(rule_path))
        return None
    sha = rule_sha256(rule_path)
    if expected_sha is not None:
        pf.add(
            f"{rule_id}: rule text sha256 equals the pinned frozen sha",
            sha == expected_sha,
            f"computed {sha[:8]}, pinned {expected_sha[:8]}",
        )
    shas = lookup(rule_id, repo)
    pf.add(
        f"{rule_id}: a commit 'prereg({rule_id}): <sha>' carries the rule text sha",
        sha in shas,
        f"computed {sha[:8]}; commits carry {[x[:8] for x in shas]}",
    )
    pf.add(f"{rule_id}: status line begins 'Status: **FROZEN'", frozen_status(rule_path), "")
    return sha


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------ bootstrap


def cluster_matrix(n_clusters: int, b: int, rng: np.random.Generator) -> np.ndarray:
    """``[b, n_clusters]`` counts: row i is one resample of ``n_clusters`` clusters drawn with
    replacement (``rng.integers``), as multiplicities."""
    if n_clusters == 0:
        return np.zeros((b, 0))
    idx = rng.integers(0, n_clusters, size=(b, n_clusters))
    flat = (idx + np.arange(b)[:, None] * n_clusters).ravel()
    return np.bincount(flat, minlength=b * n_clusters).reshape(b, n_clusters).astype(np.float64)


def make_boot(sizes: dict[int, int], b: int, seed: int) -> dict[int, np.ndarray]:
    """One resample matrix per season (seasons ascending, one generator): the matrix of a season
    is shared by every cell, bin, stat and arm (ALT_LINE_70 'Clustering')."""
    rng = np.random.default_rng(seed)
    return {s: cluster_matrix(sizes[s], b, rng) for s in sorted(sizes)}


def boot_ratio(
    parts: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
) -> tuple[float, np.ndarray]:
    """Ratio estimator on cluster totals, strata = seasons: ``parts`` holds ``(matrix[B, G],
    num[G], den[G])`` per stratum; returns the point estimate and the B bootstrap values."""
    num = sum(float(n.sum()) for _, n, _ in parts)
    den = sum(float(d.sum()) for _, _, d in parts)
    point = num / den if den else float("nan")
    b = parts[0][0].shape[0]
    nb = np.zeros(b)
    db = np.zeros(b)
    for m, n, d in parts:
        nb += m @ n
        db += m @ d
    with np.errstate(divide="ignore", invalid="ignore"):
        boot = np.where(db > 0, nb / db, np.nan)
    return point, boot


def percentile_interval(boot: np.ndarray) -> tuple[float, float]:
    x = boot[np.isfinite(boot)]
    if x.size == 0:
        return float("nan"), float("nan")
    lo, hi = np.percentile(x, [2.5, 97.5])
    return float(lo), float(hi)


def p_two_sided(boot: np.ndarray) -> float:
    """2 min(P(b <= 0), P(b >= 0)) with the +1 correction, capped at 1."""
    x = boot[np.isfinite(boot)]
    n = x.size
    if n == 0:
        return float("nan")
    lo = (int((x <= 0).sum()) + 1) / (n + 1)
    hi = (int((x >= 0).sum()) + 1) / (n + 1)
    return float(min(1.0, 2.0 * min(lo, hi)))


def p_le0(boot: np.ndarray) -> float:
    """One-sided p for 'the statistic is <= 0': (#{b <= 0} + 1) / (B + 1)."""
    x = boot[np.isfinite(boot)]
    return float((int((x <= 0).sum()) + 1) / (x.size + 1)) if x.size else float("nan")


def decide(
    point: float, boot_game: np.ndarray | None, boot_date: np.ndarray | None
) -> dict[str, Any]:
    """Decision interval = union of the game- and date-clustered percentile intervals (lower = min
    of the lowers, upper = max of the uppers); p = max of the two clusterings (two-sided and
    one-sided 'ROI <= 0'). A missing clustering is ignored."""
    boots = [x for x in (boot_game, boot_date) if x is not None]
    los, his = zip(*(percentile_interval(x) for x in boots), strict=True)
    out: dict[str, Any] = {
        "point": point,
        "lo": float(np.nanmin(los)),
        "hi": float(np.nanmax(his)),
        "p_two_sided": float(np.nanmax([p_two_sided(x) for x in boots])),
        "p_le0": float(np.nanmax([p_le0(x) for x in boots])),
    }
    if boot_game is not None:
        out["interval_game"] = list(percentile_interval(boot_game))
    if boot_date is not None:
        out["interval_date"] = list(percentile_interval(boot_date))
    return out


def bh_q(p: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values)."""
    arr = np.asarray(p, dtype=float)
    m = arr.size
    if m == 0:
        return []
    order = np.argsort(arr)
    scaled = arr[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(scaled[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return [float(x) for x in out]


def cluster_totals(
    cluster_idx: np.ndarray, n_clusters: int, values: np.ndarray | None = None
) -> np.ndarray:
    """Per-cluster sums of ``values`` (or counts when ``values`` is None)."""
    return np.bincount(cluster_idx, weights=values, minlength=n_clusters).astype(np.float64)


# ------------------------------------------------------------------------------ odds


def american_from_decimal(d: float) -> float:
    """American price equivalent of a decimal price (``nan`` for d <= 1)."""
    if not np.isfinite(d) or d <= 1.0:
        return float("nan")
    return float((d - 1.0) * 100.0) if d >= 2.0 else float(-100.0 / (d - 1.0))


def break_even(p_claimed: float, hit: float) -> dict[str, float]:
    """Break-even decimal and American price at the claimed probability and at the realised rate."""
    dc = 1.0 / p_claimed if p_claimed > 0 else float("nan")
    dr = 1.0 / hit if hit > 0 else float("nan")
    return {
        "decimal_at_claim": dc,
        "american_at_claim": american_from_decimal(dc),
        "decimal_at_realised": dr,
        "american_at_realised": american_from_decimal(dr),
    }


# ------------------------------------------------------------------------------ weekly plan


WeekUnits = tuple[
    np.ndarray, np.ndarray
]  # (profit matrix [n_units, kmax] in stake units, k [n_units])


def build_weeks(profits: np.ndarray, weeks: list[Any], unit_of: list[Any]) -> list[WeekUnits]:
    """Group rungs into weeks and units: for each Monday-start week (ascending) a padded matrix
    whose row is one unit's rung profits (stake units); ``unit_of`` identifies each rung's unit."""
    by_week: dict[Any, dict[Any, list[float]]] = {}
    for w, u, p in zip(weeks, unit_of, profits, strict=True):
        by_week.setdefault(w, {}).setdefault(u, []).append(float(p))
    out: list[WeekUnits] = []
    for w in sorted(by_week):
        units = list(by_week[w].values())
        kmax = max(len(u) for u in units)
        mat = np.zeros((len(units), kmax))
        ks = np.zeros(len(units), dtype=int)
        for i, u in enumerate(units):
            mat[i, : len(u)] = u
            ks[i] = len(u)
        out.append((mat, ks))
    return out


def simulate_plan(
    season_weeks: dict[int, list[WeekUnits]],
    r: int = PLAN_R,
    seed: int = PLAN_SEED,
    max_bets: int = PLAN_BETS_PER_WEEK,
    stake: float = PLAN_STAKE,
) -> dict[int, dict[str, np.ndarray]]:
    """The $2 x 10-a-week plan: per week, if more than ``max_bets`` units are available draw
    ``max_bets`` units uniformly without replacement, then ONE rung uniformly within each drawn
    unit; with fewer units bet all of them (one rung each). ``r`` independent plan draws per
    season from a single ``default_rng(seed)`` consumed in a fixed order (seasons ascending, weeks
    ascending; within a week the unit draw first, then the rung draw). Returns per season the
    weekly profit and staked dollars, shape ``[r, n_weeks]``."""
    rng = np.random.default_rng(seed)
    out: dict[int, dict[str, np.ndarray]] = {}
    for season in sorted(season_weeks):
        weeks = season_weeks[season]
        prof = np.zeros((r, len(weeks)))
        staked = np.zeros((r, len(weeks)))
        for wi, (mat, ks) in enumerate(weeks):
            n_u = mat.shape[0]
            if n_u > max_bets:
                u = rng.random((r, n_u))
                sel = np.argpartition(u, max_bets - 1, axis=1)[:, :max_bets]
            else:
                sel = np.tile(np.arange(n_u), (r, 1))
            m = sel.shape[1]
            rr = np.floor(rng.random((r, m)) * ks[sel]).astype(int)
            prof[:, wi] = mat[sel, rr].sum(axis=1) * stake
            staked[:, wi] = m * stake
        out[season] = {"profit": prof, "staked": staked}
    return out


def plan_summary(profit: np.ndarray, staked: np.ndarray) -> dict[str, Any]:
    """E2 (descriptive): total staked, final profit (median, 5th, 95th percentile), P(final > 0),
    median and 95th-percentile max drawdown of the weekly cumulative path, share of weeks with a
    loss (median over plan draws), longest run of losing weeks (median and 95th)."""
    r, n_w = profit.shape
    if n_w == 0:
        return {"weeks": 0}
    cum = np.cumsum(profit, axis=1)
    final = cum[:, -1]
    peak = np.maximum.accumulate(np.concatenate([np.zeros((r, 1)), cum], axis=1), axis=1)[:, 1:]
    dd = (peak - cum).max(axis=1)
    loss = profit < 0
    run = np.zeros(r)
    longest = np.zeros(r)
    for j in range(n_w):
        run = np.where(loss[:, j], run + 1, 0)
        longest = np.maximum(longest, run)
    return {
        "weeks": int(n_w),
        "total_staked_median": float(np.median(staked.sum(axis=1))),
        "final_profit_median_p05_p95": [
            float(x) for x in (np.median(final), np.percentile(final, 5), np.percentile(final, 95))
        ],
        "p_final_positive": float((final > 0).mean()),
        "max_drawdown_median_p95": [float(np.median(dd)), float(np.percentile(dd, 95))],
        "share_losing_weeks_median": float(np.median(loss.mean(axis=1))),
        "longest_losing_run_median_p95": [
            float(np.median(longest)),
            float(np.percentile(longest, 95)),
        ],
    }
