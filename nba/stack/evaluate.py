"""Paired, game-clustered evaluation of routers with the pre-registered rule.

Decision rule (see docs/ROUTER_V2.md, fixed before any real run): a router is
KEPT only if, vs the best single candidate on the scored rows (hindsight best,
conservative):

1. point delta <= -effect_floor and the 95% clustered CI upper bound < 0
   (lower score is better: CRPS for props, log loss for win);
2. the Benjamini-Hochberg q-value over the whole pre-registered comparison
   family is < 0.05;
3. no slice group (cold-start bucket, first-15 vs rest, starter/bench) with
   n >= min_slice_n regresses by more than slice_tol; a missing required slice
   makes the decision "undecidable" (not kept).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nba.props.metrics import _clustered_boot_means, paired_score_delta_ci
from nba.stack.data import StackData
from nba.stack.router import RouterResult, equal_pool
from nba.stack.scoring import score_rows

REQUIRED_SLICES = ("cold_start", "season_phase", "starter")


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """BH step-up adjusted q-values (monotone, capped at 1)."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def standard_slices(context: pd.DataFrame, first_n: int = 15) -> dict[str, np.ndarray]:
    """Slice labels from context columns ``cold_start_bucket``,
    ``games_played_season`` and ``starter`` (absent column -> slice omitted)."""
    s: dict[str, np.ndarray] = {}
    if "cold_start_bucket" in context:
        s["cold_start"] = context["cold_start_bucket"].astype(str).to_numpy()
    if "games_played_season" in context:
        gp = context["games_played_season"].to_numpy(dtype=float)
        s["season_phase"] = np.where(gp < first_n, "first15", "rest")
    if "starter" in context:
        s["starter"] = np.where(context["starter"].to_numpy(dtype=float) > 0.5, "starter", "bench")
    return s


@dataclass
class Comparison:
    router: str
    versus: str
    n: int
    games: int
    delta: float
    lo: float
    hi: float
    p: float
    q: float = float("nan")


@dataclass
class SliceDelta:
    router: str
    slice: str
    group: str
    n: int
    delta: float
    ok: bool


@dataclass
class Decision:
    router: str
    kept: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    target: str
    best_single: str
    n_scored: int
    mean_scores: dict[str, float]
    comparisons: list[Comparison]
    slices: list[SliceDelta]
    decisions: list[Decision]

    def to_markdown(self) -> str:
        lines = [
            f"# Router evaluation ({self.target})",
            "",
            f"Scored rows n={self.n_scored}; best single candidate: **{self.best_single}**.",
            "",
            "| predictor | mean score |",
            "|---|---|",
        ]
        lines += [f"| {k} | {v:.4f} |" for k, v in self.mean_scores.items()]
        lines += [
            "",
            "Deltas = mean(score_router - score_other); negative = router better.",
            "",
            "| router | vs | delta | 95% CI | p | BH q |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {c.router} | {c.versus} | {c.delta:+.4f} | [{c.lo:+.4f}, {c.hi:+.4f}] "
            f"| {c.p:.3f} | {c.q:.3f} |"
            for c in self.comparisons
        ]
        lines += [
            "",
            "| router | slice | group | n | delta vs best single | ok |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {s.router} | {s.slice} | {s.group} | {s.n} | {s.delta:+.4f} | {s.ok} |"
            for s in self.slices
        ]
        lines += ["", "## Decisions", ""]
        for d in self.decisions:
            lines.append(f"- **{d.router}**: {'KEPT' if d.kept else 'NOT KEPT'}")
            lines += [f"  - {r}" for r in d.reasons]
        return "\n".join(lines) + "\n"


def evaluate_stack(
    data: StackData,
    routers: dict[str, RouterResult],
    hard: RouterResult,
    slices: dict[str, np.ndarray],
    *,
    effect_floor: float = 0.005,
    slice_tol: float = 0.01,
    min_slice_n: int = 200,
    alpha: float = 0.05,
    n_boot: int = 2000,
    seed: int = 0,
    required_slices: tuple[str, ...] = REQUIRED_SLICES,
) -> EvalReport:
    mask = hard.scored.copy()
    for r in routers.values():
        mask &= r.scored
    if not mask.any():
        raise ValueError("no commonly scored rows")
    y = data.y[mask]
    cl = data.keys["game_id"].to_numpy()[mask]
    single = {
        c: score_rows(data.target, data.cand[mask, j], y) for j, c in enumerate(data.candidates)
    }
    scores: dict[str, np.ndarray] = dict(single)
    scores["equal_pool"] = score_rows(data.target, equal_pool(data)[mask], y)
    scores["hard_route"] = score_rows(data.target, hard.pooled[mask], y)
    for name, r in routers.items():
        scores[name] = score_rows(data.target, r.pooled[mask], y)
    means = {k: float(v.mean()) for k, v in scores.items()}
    best = min(single, key=lambda c: means[c])

    comps: list[Comparison] = []
    for name in routers:
        for other in (best, "equal_pool", "hard_route"):
            delta = scores[name] - scores[other]
            ci = paired_score_delta_ci(
                scores[name], scores[other], n_boot=n_boot, seed=seed, cluster_ids=cl
            )
            boot = _clustered_boot_means(delta, cl, n_boot, np.random.default_rng(seed))
            p = 2.0 * min(float((boot >= 0).mean()), float((boot <= 0).mean()))
            p = float(min(1.0, max(p, 1.0 / (n_boot + 1))))
            comps.append(
                Comparison(
                    name,
                    other,
                    int(mask.sum()),
                    int(len(np.unique(cl))),
                    ci.point,
                    ci.lo,
                    ci.hi,
                    p,
                )
            )
    qs = benjamini_hochberg(np.array([c.p for c in comps]))
    for c, q in zip(comps, qs, strict=True):
        c.q = float(q)

    sl: list[SliceDelta] = []
    for name in routers:
        for sname, labels in slices.items():
            lab = np.asarray(labels)[mask]
            for g in sorted(set(lab.tolist())):
                m = lab == g
                if m.sum() < min_slice_n:
                    continue
                d = float((scores[name][m] - scores[best][m]).mean())
                sl.append(SliceDelta(name, sname, str(g), int(m.sum()), d, d <= slice_tol))

    decisions: list[Decision] = []
    for name in routers:
        c = next(c for c in comps if c.router == name and c.versus == best)
        reasons: list[str] = []
        ok = True
        if not (c.delta <= -effect_floor and c.hi < 0):
            ok = False
            reasons.append(
                f"vs best single ({best}): delta {c.delta:+.4f} CI [{c.lo:+.4f}, {c.hi:+.4f}] "
                f"fails floor {effect_floor} / CI<0"
            )
        if not c.q < alpha:
            ok = False
            reasons.append(f"BH q={c.q:.3f} >= {alpha}")
        for req in required_slices:
            if req not in slices:
                ok = False
                reasons.append(f"required slice '{req}' unavailable: undecidable")
        bad = [s for s in sl if s.router == name and not s.ok]
        for s in bad:
            ok = False
            reasons.append(f"slice {s.slice}={s.group} regresses {s.delta:+.4f} > {slice_tol}")
        if ok:
            reasons.append("all criteria met")
        decisions.append(Decision(name, ok, reasons))
    return EvalReport(data.target, best, int(mask.sum()), means, comps, sl, decisions)
