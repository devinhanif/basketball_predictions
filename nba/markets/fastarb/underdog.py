"""Underdog Fantasy pick'em lines vs Pinnacle, from a MANUAL input file only.

Underdog's terms of use (section 6(ix), read 2026-10-25: legal.underdogsports.com/?g=42203) prohibit
accessing the Services other than manually through a browser or approved app, so nothing in this
package polls Underdog and no module imports an HTTP client for it. A person types the lines into a
CSV; this module only does the arithmetic.

CSV columns: player,stat,ud_line,pin_line,pin_over_dec,pin_under_dec[,game][,note]
(``pin_*`` are Pinnacle's decimal odds for the main line of that prop, typed or copied by hand).

Model (assumptions, all in configs/fastarb.yaml or below, unverified): the stat is Normal with
sd = sd_per_sqrt_mean * sqrt(mean); the mean is solved so P(over pin_line) equals Pinnacle's
proportionally de-vigged probability. That is a crude count-stat approximation; the verdict it
supports is "worth a manual look", not a calibrated edge.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

#: sd = factor * sqrt(mean). Assumed from typical NBA box-score dispersion; not fitted here.
SD_PER_SQRT_MEAN = {"points": 1.4, "rebounds": 1.15, "assists": 1.1, "threes": 0.95}
DEFAULT_SD_FACTOR = 1.2
_N = NormalDist()


def break_even(multiplier: float, picks: int) -> float:
    """Per-leg win probability at which an all-correct ``picks``-pick entry has zero EV."""
    return float(multiplier ** (-1.0 / picks))


@dataclass
class UdRow:
    player: str
    stat: str
    ud_line: float
    pin_line: float
    p_over_pin: float
    game: str = ""


@dataclass
class UdResult:
    row: UdRow
    side: str  # over | under
    diff: float
    p_win: float
    flagged: bool  # line differs by the configured threshold
    plus_ev_2pick: bool  # p_win above the 2-pick break-even
    text: str


def read_rows(path: Path) -> list[UdRow]:
    out: list[UdRow] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            over, under = float(r["pin_over_dec"]), float(r["pin_under_dec"])
            io, iu = 1.0 / over, 1.0 / under
            out.append(
                UdRow(
                    r["player"].strip(),
                    r["stat"].strip().lower(),
                    float(r["ud_line"]),
                    float(r["pin_line"]),
                    io / (io + iu),
                    r.get("game", "") or "",
                )
            )
    return out


def p_over(row: UdRow, line: float) -> tuple[float, float]:
    """(P(X > line), P(X < line)) under the Normal model anchored to Pinnacle's main line."""
    k = SD_PER_SQRT_MEAN.get(row.stat, DEFAULT_SD_FACTOR)
    mu = row.pin_line
    z = _N.inv_cdf(min(max(1.0 - row.p_over_pin, 1e-6), 1 - 1e-6))
    for _ in range(30):  # mean and sd depend on each other: fixed-point
        sd = k * max(mu, 1.0) ** 0.5
        mu = row.pin_line - sd * z
    sd = k * max(mu, 1.0) ** 0.5
    over = (
        1.0 - _N.cdf((line + 0.5 - mu) / sd)
        if abs(line - round(line)) < 1e-9
        else 1.0 - _N.cdf((line - mu) / sd)
    )
    under = (
        _N.cdf((line - 0.5 - mu) / sd)
        if abs(line - round(line)) < 1e-9
        else _N.cdf((line - mu) / sd)
    )
    return over, under


def evaluate(rows: list[UdRow], ud: dict[str, Any]) -> list[UdResult]:
    table = {int(k): float(v) for k, v in ud["payout_table"].items()}
    be2 = break_even(table[2], 2)
    small = set(ud["small_stats"])
    out: list[UdResult] = []
    for r in rows:
        diff = r.ud_line - r.pin_line
        thr = float(ud["min_diff_small"] if r.stat in small else ud["min_diff"])
        if diff == 0:
            continue
        side = "under" if diff > 0 else "over"
        po, pu = p_over(r, r.ud_line)
        p_win = pu / (pu + po) if side == "under" else po / (pu + po)  # a push voids the leg
        flagged = abs(diff) >= thr
        plus = p_win > be2
        text = (
            f"UD {r.player} {r.stat} {side.upper()} {r.ud_line:g} (Pinnacle line {r.pin_line:g}, "
            f"no-vig over {r.p_over_pin:.3f}): model P(win) {p_win:.3f} vs 2-pick "
            f"break-even {be2:.3f} "
            f"({table[2]:g}x); ASSUMED payouts, see configs/fastarb.yaml"
        )
        out.append(UdResult(r, side, diff, p_win, flagged, plus, text))
    out.sort(key=lambda x: x.p_win, reverse=True)
    return out
