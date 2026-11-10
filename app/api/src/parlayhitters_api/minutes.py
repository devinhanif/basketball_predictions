"""How much of a stat's spread is minutes, from fields the data holds. Descriptive and rough.

The model forecasts a stat, not minutes. The export does carry the model's projected minutes for
the game (`proj_minutes`) and, from the box scores, how much his minutes have varied. A crude
split of the spread follows if one assumption is made and said out loud:

    stat = (per-minute rate) x (minutes), the rate held fixed.

Then the relative spread of the stat that comes from minutes alone is the relative spread of his
minutes (his recent standard deviation over his recent average), and the share of the stat's
variance that minutes explain is (that relative spread x the stat's mean / the stat's standard
deviation) squared, capped at 1. It is an approximation, not a fitted decomposition, and every
screen that shows it says so. With fewer than STEADY_MIN_GAMES earlier games it is not computed.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from .breakdown import Context
from .forecast import dist_grid
from .role_stats import STEADY_MIN_GAMES, steadiness

Json = dict[str, Any]

METHOD_NOTE = (
    "A rough split, not a fitted one. It assumes his per-minute rate stays fixed and that his "
    "minutes vary as they did over his recent games. Real games break both assumptions a little."
)


class MinutesInfo(BaseModel):
    #: The minutes the what-if starts from, and where they came from: the model's projection, or
    #: his recent average when this data holds no projection. None when neither exists.
    baseline: float | None
    baseline_source: str | None  # "model" | "recent"
    proj_minutes: float | None
    p_play: float | None
    recent_avg: float | None
    recent_sd: float | None
    recent_n: int
    stat_mean: float | None
    stat_sd: float | None
    #: Share of the stat's variance that minutes explain, 0..1; None when not computable.
    minutes_share: float | None
    #: The 80% range (stored quantiles 0.10 to 0.90) now, and if minutes were known exactly.
    range_now: tuple[float, float]
    range_minutes_known: tuple[float, float] | None
    method: str = METHOD_NOTE


def moments(p_ge: list[float]) -> tuple[float, float]:
    """Mean and standard deviation of a count from P(Y >= k), k = 0..K (p_ge[0] = 1)."""
    ey = sum(p_ge[1:])
    ey2 = sum((2 * k - 1) * p for k, p in enumerate(p_ge) if k >= 1)
    return ey, math.sqrt(max(0.0, ey2 - ey * ey))


def minutes_info(forecast: Json, ctx: Context | None) -> MinutesInfo:
    q = [float(v) for v in forecast["q"]]
    mean, sd = moments(dist_grid(forecast)["p_ge"])
    steady = steadiness(ctx.all_prior) if ctx is not None else None
    proj = forecast.get("proj_minutes")
    proj = None if proj is None else float(proj)
    recent_avg = None if steady is None else steady.avg_minutes
    baseline, source = (
        (proj, "model") if proj is not None else (recent_avg, "recent" if recent_avg else None)
    )
    share: float | None = None
    if (
        steady is not None
        and steady.n >= STEADY_MIN_GAMES
        and steady.cv is not None
        and mean > 0
        and sd > 0
    ):
        share = min(1.0, (steady.cv * mean / sd) ** 2)
    p10, p90 = q[1], q[17]
    known = None
    if share is not None:
        # Same centre, spread shrunk by the share of variance minutes explain (normal-ish).
        mid, half = (p10 + p90) / 2, (p90 - p10) / 2 * math.sqrt(1 - share)
        known = (mid - half, mid + half)
    pp = forecast.get("p_play")
    return MinutesInfo(
        baseline=baseline,
        baseline_source=source,
        proj_minutes=proj,
        p_play=None if pp is None else float(pp),
        recent_avg=recent_avg,
        recent_sd=None if steady is None else steady.sd_minutes,
        recent_n=0 if steady is None else steady.n,
        stat_mean=mean,
        stat_sd=sd,
        minutes_share=share,
        range_now=(p10, p90),
        range_minutes_known=known,
    )
