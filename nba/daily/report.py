"""Rolling forward metrics with game-date-clustered bootstrap CIs."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np

from nba.daily.season import FORWARD_SEASON, ROLLOVER_MIN_GAMES
from nba.daily.store import ensure_tables

#: Reference for win prob: an uninformative p=0.5 forecast.
NAIVE_LOG_LOSS = float(np.log(2.0))


@dataclass(frozen=True)
class ClusterCI:
    point: float
    lo: float
    hi: float
    n: int
    n_clusters: int


def cluster_bootstrap_mean(
    values: np.ndarray, clusters: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> ClusterCI:
    """Percentile CI for the mean, resampling whole clusters (e.g. game dates)
    so same-night correlation is not mistaken for independent samples."""
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        nan = float("nan")
        return ClusterCI(nan, nan, nan, 0, 0)
    _, inv = np.unique(clusters, return_inverse=True)
    k = int(inv.max()) + 1
    sums = np.bincount(inv, weights=v, minlength=k)
    cnts = np.bincount(inv, minlength=k).astype(float)
    point = float(v.mean())
    if k < 2:
        return ClusterCI(point, float("nan"), float("nan"), v.size, k)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return ClusterCI(point, float(lo), float(hi), v.size, k)


def rollover_status(con: duckdb.DuckDBPyConnection) -> tuple[int, bool]:
    """(# completed 2026-27 games ingested, rollover trigger fired?)."""
    row = con.execute(
        "SELECT count(*) FROM games WHERE season = ? AND home_pts > 0 AND away_pts > 0",
        [FORWARD_SEASON],
    ).fetchone()
    n = int(row[0]) if row else 0
    return n, n >= ROLLOVER_MIN_GAMES


def _fmt(ci: ClusterCI, d: int = 4) -> str:
    return f"{ci.point:.{d}f} [{ci.lo:.{d}f}, {ci.hi:.{d}f}] (n={ci.n}, dates={ci.n_clusters})"


def build_report(con: duckdb.DuckDBPyConnection, season: int = FORWARD_SEASON) -> str:
    ensure_tables(con)
    n_games, trigger = rollover_status(con)
    lines = [f"# Forward (out-of-sample) report, season={season}", ""]
    lines.append(
        f"Completed {FORWARD_SEASON}-{(FORWARD_SEASON + 1) % 100:02d} games ingested: "
        f"{n_games} (rollover threshold {ROLLOVER_MIN_GAMES})."
    )
    if trigger:
        lines.append(
            "**HOLDOUT ROLLOVER TRIGGER FIRED** (docs/HOLDOUT_ACCESS_LOG.md rule 3): "
            "re-designate the canonical props holdout as season=2026, fold season=2025 "
            "into the tuning pool, and do not tune on 2026 before its first confirmatory use."
        )
    lines.append("")
    win = con.execute(
        "SELECT game_date, log_loss, brier, model_name, version FROM forward_scores "
        "WHERE season = ? AND target = 'win_prob_home' AND status = 'scored'",
        [season],
    ).fetchall()
    lines.append("## Win probability (home)")
    if not win:
        lines.append("No settled forward win predictions yet.")
    else:
        d = np.array([str(r[0]) for r in win])
        ll = np.array([r[1] for r in win], dtype=float)
        br = np.array([r[2] for r in win], dtype=float)
        lines += [
            f"- models: {sorted({(r[3], r[4]) for r in win})}",
            f"- log loss: {_fmt(cluster_bootstrap_mean(ll, d))}",
            f"- Brier: {_fmt(cluster_bootstrap_mean(br, d))}",
            f"- log loss minus naive p=0.5 ({NAIVE_LOG_LOSS:.4f}): "
            f"{_fmt(cluster_bootstrap_mean(ll - NAIVE_LOG_LOSS, d))}",
        ]
        if len(win) < 100:
            lines.append(f"- CAUTION: only {len(win)} settled games; CIs are very wide.")
    lines += ["", "## Props (normal CRPS, lower is better)"]
    props = con.execute(
        "SELECT target, game_date, crps, y, pred FROM forward_scores "
        "WHERE season = ? AND target <> 'win_prob_home' AND status = 'scored'",
        [season],
    ).fetchall()
    dnp = con.execute(
        "SELECT count(*) FROM forward_scores WHERE season = ? AND status = 'dnp'", [season]
    ).fetchone()
    if not props:
        lines.append("No settled forward prop predictions yet.")
    for stat in sorted({r[0] for r in props}):
        sub = [r for r in props if r[0] == stat]
        d = np.array([str(r[1]) for r in sub])
        crps = np.array([r[2] for r in sub], dtype=float)
        bias = np.array([r[4] - r[3] for r in sub], dtype=float)
        lines.append(
            f"- {stat}: CRPS {_fmt(cluster_bootstrap_mean(crps, d))}; "
            f"mean bias (pred-actual) {_fmt(cluster_bootstrap_mean(bias, d), 3)}"
        )
    lines.append(f"- DNP / not-in-box (excluded from metrics): {int(dnp[0]) if dnp else 0}")
    lines += [
        "",
        "CIs: percentile bootstrap resampling game dates (2000 draws, seed 0). "
        "Props are conditional on the player playing.",
    ]
    return "\n".join(lines) + "\n"
