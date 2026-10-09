"""Pure math: turn a ladder of contract prices into implied distribution summaries.

A ladder is a list of ``(x, G)`` points with ``G(x) = P(X > x)`` (survival function) read from
contract mid prices. Real books are noisy and non-monotone, so ladders are first projected onto
non-increasing sequences by pool-adjacent-violators (equal weights), then the MEDIAN is read off by
linear interpolation between the two rungs that bracket 0.5. No extrapolation: if the ladder does
not bracket 0.5 the result is ``None``. The median (not the mean) is used because a finite ladder
cannot give tail mass, and a median of a margin/total/stat distribution is what a "line" is.
"""

from __future__ import annotations

from collections.abc import Sequence


def pav_non_increasing(values: Sequence[float]) -> list[float]:
    """Least-squares non-increasing fit (pool adjacent violators, equal weights)."""
    blocks: list[list[float]] = []  # [sum, count]
    for v in values:
        blocks.append([float(v), 1.0])
        # non-increasing: merge while the previous block's mean is below the current block's
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out: list[float] = []
    for s, c in blocks:
        out.extend([s / c] * int(c))
    return out


def monotone_survival(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort by x, average duplicate x, clip to [0, 1], enforce non-increasing G."""
    by_x: dict[float, list[float]] = {}
    for x, g in points:
        by_x.setdefault(float(x), []).append(min(1.0, max(0.0, float(g))))
    xs = sorted(by_x)
    g_raw = [sum(by_x[x]) / len(by_x[x]) for x in xs]
    return list(zip(xs, pav_non_increasing(g_raw), strict=True))


def ladder_median(points: Sequence[tuple[float, float]]) -> float | None:
    """Median implied by survival points ``(x, P(X > x))``; None if unbracketed."""
    pts = monotone_survival(points)
    for (x0, g0), (x1, g1) in zip(pts, pts[1:], strict=False):
        if g0 >= 0.5 > g1:
            return x0 + (g0 - 0.5) / (g0 - g1) * (x1 - x0)
    return None


def devig_two_way(mid_a: float | None, mid_b: float | None) -> float | None:
    """P(A) from the two sides' mids of a two-outcome market, proportionally de-vigged.

    One side missing: use that side alone (``mid_a`` or ``1 - mid_b``). Both missing: None.
    """
    if mid_a is not None and mid_b is not None:
        s = mid_a + mid_b
        return mid_a / s if s > 0 else None
    if mid_a is not None:
        return mid_a
    if mid_b is not None:
        return 1.0 - mid_b
    return None
