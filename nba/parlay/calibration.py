"""Joint-calibration comparison of engines with paired per-event bootstrap."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

_EPS = 1e-6


def log_loss_vec(p: Sequence[float], y: Sequence[int]) -> NDArray[np.float64]:
    pp = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    yy = np.asarray(y, dtype=float)
    return np.asarray(-(yy * np.log(pp) + (1 - yy) * np.log(1 - pp)), dtype=float)


def brier_vec(p: Sequence[float], y: Sequence[int]) -> NDArray[np.float64]:
    return np.asarray((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2, dtype=float)


@dataclass(frozen=True)
class PairedComparison:
    metric: str
    engine_a: str
    engine_b: str
    n: int
    mean_diff: float  # mean(loss_a - loss_b); negative => A better
    ci_lo: float
    ci_hi: float
    a_better_significant: bool


def paired_bootstrap(
    loss_a: NDArray[np.float64],
    loss_b: NDArray[np.float64],
    *,
    metric: str,
    names: tuple[str, str],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> PairedComparison:
    d = loss_a - loss_b
    n = len(d)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return PairedComparison(
        metric=metric,
        engine_a=names[0],
        engine_b=names[1],
        n=n,
        mean_diff=float(d.mean()),
        ci_lo=float(lo),
        ci_hi=float(hi),
        a_better_significant=bool(hi < 0.0),
    )


def compare_engines(
    probs: Mapping[str, Sequence[float]],
    outcomes: Sequence[int],
    *,
    baseline: str = "independence",
    n_boot: int = 2000,
    seed: int = 0,
) -> list[PairedComparison]:
    """Each non-baseline engine vs ``baseline`` on log loss and Brier of the joint event.

    ``probs[name][i]`` is the engine's joint P for event i; ``outcomes[i]`` in {0,1}.
    """
    out: list[PairedComparison] = []
    for name, p in probs.items():
        if name == baseline:
            continue
        for metric, fn in (("log_loss", log_loss_vec), ("brier", brier_vec)):
            out.append(
                paired_bootstrap(
                    fn(p, outcomes),
                    fn(probs[baseline], outcomes),
                    metric=metric,
                    names=(name, baseline),
                    n_boot=n_boot,
                    seed=seed,
                )
            )
    return out


def comparison_table(rows: Sequence[PairedComparison]) -> str:
    lines = [
        "| metric | A | B | n | mean(A-B) | 95% CI | A better? |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.metric} | {r.engine_a} | {r.engine_b} | {r.n} | {r.mean_diff:+.5f} | "
            f"[{r.ci_lo:+.5f}, {r.ci_hi:+.5f}] | {'yes' if r.a_better_significant else 'no'} |"
        )
    return "\n".join(lines)
