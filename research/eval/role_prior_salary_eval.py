# ruff: noqa: E501
"""ROLE_PRIOR_SALARY inference, label-free power / positive-control simulation, verdict words, and the refusing entrypoint.

The real arms (A0, S1, S2, PL) are NOT scored by this file: ``run`` refuses unless the rule is frozen (a commit
``prereg(ROLE_PRIOR_SALARY): <sha256 of the text above the results marker>`` exists). The simulation draws outcomes from the stored
A0 quantile grid itself (never a real outcome), so it measures how wide the CI is and whether the harness can see a planted shift.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import role_prior_salary_rule as rule

MARKER = "=== RESULTS BELOW ==="
DOC = Path(__file__).resolve().parents[2] / "docs/prereg/ROLE_PRIOR_SALARY.md"
CLUSTERINGS = ("game", "player_season", "week")


def boot_ci(
    values: np.ndarray, clusters: np.ndarray, n_boot: int = rule.N_BOOT, seed: int = rule.SEED
) -> dict[str, float]:
    from nba.props.metrics import _clustered_boot_means

    v = np.asarray(values, dtype=float)
    b = _clustered_boot_means(v, np.asarray(clusters), n_boot, np.random.default_rng(seed))
    lo, hi = float(np.quantile(b, rule.ALPHA / 2)), float(np.quantile(b, 1 - rule.ALPHA / 2))
    return {
        "point": float(v.mean()),
        "lo": lo,
        "hi": hi,
        "half_width": (hi - lo) / 2,
        "p": float((1 + np.sum(b >= 0)) / (1 + n_boot)),
        "n": int(len(v)),
        "clusters": int(len(np.unique(clusters))),
    }


def three_way(
    values: np.ndarray, game: np.ndarray, player_season: np.ndarray, week: np.ndarray
) -> dict[str, Any]:
    """CIs under the three clusterings; the binding one is the widest (BEST_PRACTICES 2026-10-20)."""
    out: dict[str, Any] = {
        k: boot_ci(values, c) for k, c in zip(CLUSTERINGS, (game, player_season, week), strict=True)
    }
    bind = max(CLUSTERINGS, key=lambda k: out[k]["half_width"])
    out["binding"] = {"clustering": bind, **out[bind]}
    return out


def top5_share(delta: np.ndarray, player_season: np.ndarray) -> float:
    """Share of the net gain (-sum delta) carried by the five player-seasons with the largest contributions; nan when there is no gain."""
    ids, inv = np.unique(player_season, return_inverse=True)
    contrib = -np.bincount(inv, weights=np.asarray(delta, float), minlength=len(ids))
    total = float(contrib.sum())
    if total <= 0:
        return float("nan")
    return float(np.sort(contrib)[::-1][:5].sum() / total)


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        adj[i] = prev
    return adj


def verdict(res: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """res[season][stat] = {point, hi, p (binding clustering), top5, pl_point (placebo mean diff), harm_lo}. Words:
    PASS (pts clears both seasons with guards), PASS_OTHER_STAT, PLACEBO_TOO (placebo keeps >= 50% of the gain), FAIL_GUARD (top-5 share
    > 0.5 or harm), FAIL_ONE_SEASON, FAIL_NO_GAIN. A stat clears a season when point <= -floor, binding-CI upper < 0, BH p <= 0.05."""
    seasons = sorted(res)
    tab: dict[str, dict[str, Any]] = {}
    for s in seasons:
        adj = bh_adjust(np.array([res[s][st]["p"] for st in rule.STATS]))
        for st, pa in zip(rule.STATS, adj, strict=True):
            r = res[s][st]
            clears = r["point"] <= -rule.FLOORS[st] and r["hi"] < 0 and pa <= rule.ALPHA
            tab.setdefault(s, {})[st] = {
                **r,
                "p_bh": float(pa),
                "clears": bool(clears),
                "placebo": bool(clears and -r["pl_point"] >= 0.5 * -r["point"]),
                "top5_fail": bool(clears and not (r["top5"] <= rule.TOP5_SHARE_MAX)),
                "harm": bool(r["harm_lo"] > 0),
            }
    both = [st for st in rule.STATS if all(tab[s][st]["clears"] for s in seasons)]
    anyc = [st for st in rule.STATS if any(tab[s][st]["clears"] for s in seasons)]
    no_harm = not any(tab[s][st]["harm"] for s in seasons for st in rule.STATS)
    good = [
        st
        for st in both
        if no_harm
        and all(not tab[s][st]["placebo"] and not tab[s][st]["top5_fail"] for s in seasons)
    ]
    if good:
        word = "PASS" if "pts" in good else "PASS_OTHER_STAT"
    elif both:
        word = (
            "PLACEBO_TOO"
            if any(tab[s][st]["placebo"] for s in seasons for st in both)
            else "FAIL_GUARD"
        )
    elif anyc:
        word = "FAIL_ONE_SEASON"
    else:
        word = "FAIL_NO_GAIN"
    return {"word": word, "table": tab, "both": both}


# --------------------------------------------------------------------------- label-free simulation (power and positive control)


def sim_cell(
    q0: np.ndarray,
    player_season: np.ndarray,
    game: np.ndarray,
    week: np.ndarray,
    sigma: float,
    recover: float,
    seed: int,
    crps_int_q: Any,
    n_boot: int = rule.N_BOOT,
) -> dict[str, Any]:
    """q0: A0's stored integer grid (n, 199) for the cohort rows. A planted per-player-season role shift lam_true = exp(sigma z_p) acts
    on the truth; outcomes are drawn from the shifted A0 grid (a uniformly drawn quantile, ceil(q - 0.5)); the arm applies
    exp(recover * sigma z_p) (recover = 1 is the oracle, 0.5 recovers half in log space, 0 with sigma > 0 is the null of a prior that
    carries no truth but is as noisy: use ``noise_arm``). The paired difference arm - A0 is bootstrapped under the three clusterings."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(player_season, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = np.ceil(q0[np.arange(len(q0)), u] * np.exp(sigma * z) - 0.5)
    base = crps_int_q(q0, y)
    arm = crps_int_q(q0 * np.exp(recover * sigma * z)[:, None], y)
    d = arm - base
    out = (
        three_way(d, game, player_season, week)
        if n_boot == rule.N_BOOT
        else _three_way_n(d, game, player_season, week, n_boot)
    )
    out["top5"] = top5_share(d, player_season)
    out["sd_row_diff"] = float(np.std(d))
    out["base_crps"] = float(base.mean())
    return out


def _three_way_n(
    values: np.ndarray, game: np.ndarray, player_season: np.ndarray, week: np.ndarray, n_boot: int
) -> dict[str, Any]:
    out: dict[str, Any] = {
        k: boot_ci(values, c, n_boot)
        for k, c in zip(CLUSTERINGS, (game, player_season, week), strict=True)
    }
    bind = max(CLUSTERINGS, key=lambda k: out[k]["half_width"])
    out["binding"] = {"clustering": bind, **out[bind]}
    return out


def noise_arm(
    q0: np.ndarray,
    player_season: np.ndarray,
    game: np.ndarray,
    week: np.ndarray,
    sigma: float,
    seed: int,
    crps_int_q: Any,
    n_boot: int = rule.N_BOOT,
) -> dict[str, Any]:
    """Null: truth equals A0 (no shift) and the arm shifts by an independent exp(sigma z_p): what a prior with no information does."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(player_season, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = np.ceil(q0[np.arange(len(q0)), u] - 0.5)
    d = crps_int_q(q0 * np.exp(sigma * z)[:, None], y) - crps_int_q(q0, y)
    out = _three_way_n(d, game, player_season, week, n_boot)
    out["top5"] = top5_share(d, player_season)
    out["sd_row_diff"] = float(np.std(d))
    return out


# --------------------------------------------------------------------------- refusing entrypoint


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text().split(MARKER)[0])


def require_frozen(sha: str | None, doc: Path = DOC) -> str:
    """Refuse unless the draft's text above the marker hashes to ``sha`` AND a commit titled ``prereg(ROLE_PRIOR_SALARY): <sha>`` exists."""
    if not sha:
        raise SystemExit("ROLE_PRIOR_SALARY is a DRAFT: run refuses without --sha of a frozen rule")
    if MARKER not in doc.read_text():
        raise SystemExit("rule text has no results marker: still a draft")
    if frozen_sha(doc) != sha:
        raise SystemExit("the text above the marker does not hash to --sha")
    found = subprocess.run(
        ["git", "log", "--all", f"--grep=prereg({rule.ID}): {sha}", "--format=%H"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not found:
        raise SystemExit(f"no commit 'prereg({rule.ID}): {sha}' found: not frozen")
    return found.splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--sha")
    a = ap.parse_args(argv)
    require_frozen(a.sha)
    raise SystemExit(
        "rule is frozen but the scoring arms are not built in this draft: build them behind the frozen rule"
    )


if __name__ == "__main__":
    raise SystemExit(main())
