"""Arithmetic in-game baseline A1 for INGAME_LINE_CAL_FWD (draft rule; prereg section 3).

P(final >= k) for a player who has entered the game, from simple arithmetic on the current box
score and the clock: the player's pre-game per-minute rate, shrunk toward what he has done so far,
times the minutes he should still play, with an integer negative binomial for the remaining stat.
No fitted parameter, no outcome, no I/O: a pure function of one stored checkpoint row. It is T508's
B0 with the league rate and the starter/bench share replaced by the player's own pre-game rate and
minutes (the T508-attack fix), kept as arithmetic.

Formula (constants are the rule's; changing any of them is a new rule):

* ``r0 = mu / max(m, 1)``; ``r = (x + 10 r0) / (t + 10)``
* ``s0 = min(m, 48) / 48``; ``s = (t + 6 s0) / (e + 6)``
* ``g3 = max(0, 36 - e)``; ``g4 = 48 - max(e, 36)``; ``q = 0.5 if |d| >= 20 else 1.0``
* ``R = min(s (g3 + q g4), 48 - e)``; ``lam = r R``
* ``Y ~ NegBin(mean lam, var v lam)``, ``v`` = pts 2.5, reb 1.4, ast 1.6, fg3m 1.3
* ``p = P(Y >= k - x)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy.stats import nbinom

#: variance-to-mean ratios of the remaining stat (T508 B0)
VMR: dict[str, float] = {"pts": 2.5, "reb": 1.4, "ast": 1.6, "fg3m": 1.3}
RATE_PSEUDO_MIN = 10.0
SHARE_PSEUDO_MIN = 6.0
BLOWOUT_MARGIN = 20
Q4_BLOWOUT_FACTOR = 0.5
GAME_MIN = 48.0
Q4_START = 36.0
CLIP = (0.005, 0.995)  # applied by the scorer to every arm alike, not here


@dataclass(frozen=True)
class CheckpointState:
    stat: str  # pts | reb | ast | fg3m
    k: int  # Kalshi strike (finish >= k)
    cur: int  # current stat
    minutes: float  # minutes played so far (ESPN integer MIN in the live store)
    e: float  # elapsed regulation game minutes (0-48)
    margin: int  # player's team lead (own - opponent)
    pregame_mean: float  # pre-game expected stat given he plays
    pregame_minutes: float  # pre-game expected minutes


def expected_remaining_minutes(s: CheckpointState) -> float:
    if not 0.0 <= s.e <= GAME_MIN:
        raise ValueError(f"elapsed minutes out of regulation: {s.e}")
    s0 = min(max(s.pregame_minutes, 0.0), GAME_MIN) / GAME_MIN
    share = (max(s.minutes, 0.0) + SHARE_PSEUDO_MIN * s0) / (s.e + SHARE_PSEUDO_MIN)
    g3 = max(0.0, Q4_START - s.e)
    g4 = GAME_MIN - max(s.e, Q4_START)
    q = Q4_BLOWOUT_FACTOR if abs(s.margin) >= BLOWOUT_MARGIN else 1.0
    return min(share * (g3 + q * g4), GAME_MIN - s.e)


def expected_remaining_stat(s: CheckpointState) -> float:
    r0 = max(s.pregame_mean, 0.0) / max(s.pregame_minutes, 1.0)
    rate = (max(s.cur, 0) + RATE_PSEUDO_MIN * r0) / (max(s.minutes, 0.0) + RATE_PSEUDO_MIN)
    return rate * expected_remaining_minutes(s)


def p_at_least(s: CheckpointState) -> float:
    """P(final >= k) under A1 (unclipped)."""
    need = s.k - s.cur
    if need <= 0:
        return 1.0
    lam = expected_remaining_stat(s)
    if lam <= 0.0:
        return 0.0
    v = VMR[s.stat]
    # NegBin(n, p) with mean n(1-p)/p = lam and variance lam / p = v lam
    p = 1.0 / v
    n = lam / (v - 1.0)
    return float(nbinom.sf(need - 1, n, p))
