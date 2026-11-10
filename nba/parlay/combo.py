# ruff: noqa: E501
"""Combo mode reference implementation: pure probability math, no prices, no EV, no fees, no stakes.

Standard library only, on purpose: the browser's TypeScript port is tested against this file's
golden vectors (``tests/parlay/golden/combo_golden_v1.json``), so every step is spelled out and
deterministic. Nothing here reads a market, a database or a credential; it consumes the per-game
payload of ``docs/contracts/combo_export_v1.md``.

Math, in one paragraph. A leg is an interval on one integer stat (``over x.5`` is ``Y >= x + 1``,
``under x.5`` is ``Y <= x``; whole-number lines are refused, there is no push in this mode). Each
(player, stat) has a CALIBRATED survival grid ``S(k) = P(Y >= k)`` (the line-calibration map applied
at every rung and made monotone by the builder). A latent standard normal ``z = Phi^-1(U)`` with
``Y = F^-1(U)`` ties the stats together: ``Y >= k  <=>  z > Phi^-1(1 - S(k))``. The latents of one
game are jointly Gaussian with the pooled correlations of the payload (same player, teammate,
opponent, own margin, game total), so the joint probability of a combo is a Gaussian rectangle
probability: exact one-dimensional integration for two entities, a seeded randomized
quasi-Monte-Carlo (Genz) for more, with its standard error reported. Legs on the SAME stat of the
same player are comonotone (one latent) and are merged into one interval first. Different games are
independent by construction (measured cross-game residual r is about 0, see the contract), so a
cross-game joint is the product of the games' joints.

Availability. Every probability here is CONDITIONAL on every named player playing. ``p_all_play``
(product of the payload's ``p_play``, availability independent across players) is reported next to
it, and ``p_joint_incl_dnp = p_all_play * p_joint``: with no money in the picture there is no void,
a combo "hits" only if every player plays and every leg hits.

Scripts (``script=...``): conditional on a game script the player latent is shifted and rescaled,
``z = mu + sigma * eps`` with ``(mu, sigma)`` per (script, stat, role) from the payload, and ``eps``
keeps the pooled within-game correlations. Game legs are not supported inside a script.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist
from typing import Any

SCHEMA = "combo_export_v1"
STATS = ("pts", "reb", "ast", "fg3m")
SCRIPTS = ("blowout", "close", "shootout", "grind")
INF = math.inf
_ND = NormalDist()
_SQRT2 = math.sqrt(2.0)
_ZCLIP = 9.0
_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53)
QMC_POINTS = 1500
QMC_SHIFTS = 8
SIMPSON_N = 2000
RHO_MAX = 0.9999
SCREEN_QMC = (150, 4)  # search screening budget; answers are recomputed at the full budget

Payload = Mapping[str, Any]


def norm_cdf(x: float) -> float:
    """Phi(x); exact at +-inf."""
    return 0.5 * math.erfc(-x / _SQRT2)


def norm_ppf(p: float) -> float:
    """Phi^-1(p); +-inf at 0 and 1 (Wichura AS241 through ``statistics.NormalDist``)."""
    if p <= 0.0:
        return -INF
    if p >= 1.0:
        return INF
    return _ND.inv_cdf(p)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------- marginals


def surv_at(stat_grid: Payload, k: int) -> float:
    """S(k) = P(Y >= k) from a payload stat grid ``{k0, surv}``; 1 below the stored range, 0 above."""
    if k <= 0:
        return 1.0
    k0 = int(stat_grid["k0"])
    surv: Sequence[float] = stat_grid["surv"]
    if k < k0:
        return 1.0
    j = k - k0
    if j >= len(surv):
        return 0.0
    return float(surv[j])


def line_to_bounds(line: float, side: str) -> tuple[int, float]:
    """(lo, hi) integer bounds ``lo <= Y <= hi`` of a leg. ``over x.5`` -> (x + 1, inf); ``under
    x.5`` -> (0, x). Whole-number lines raise (no push is modelled)."""
    if abs(line - math.floor(line) - 0.5) > 1e-9:
        raise ValueError(f"line {line} is not an x.5 line")
    x = int(math.floor(line))
    if side == "over":
        return x + 1, INF
    if side == "under":
        return 0, x
    raise ValueError(f"side must be 'over' or 'under', got {side!r}")


def interval_prob(stat_grid: Payload, lo: float, hi: float) -> float:
    """P(lo <= Y <= hi) on the integer grid."""
    if hi < lo:
        return 0.0
    s_lo = surv_at(stat_grid, int(lo))
    s_hi = 0.0 if hi == INF else surv_at(stat_grid, int(hi) + 1)
    return max(0.0, s_lo - s_hi)


# ---------------------------------------------------------------- entities and legs


@dataclass(frozen=True)
class Entity:
    """One latent: a player-stat, the game margin (home minus away) or the game total."""

    kind: str  # player | margin | total
    player_id: int = 0
    stat: str = ""

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.kind, self.player_id, self.stat)


@dataclass
class Merged:
    """All legs on one entity, intersected: the latent interval (a, b] and its probability."""

    entity: Entity
    lo: float  # integer bounds for a player entity
    hi: float
    a: float  # latent bounds
    b: float
    p: float  # marginal probability of the intersection


def _player(game: Payload, pid: int) -> Payload:
    for p in game["players"]:
        if int(p["player_id"]) == pid:
            return p  # type: ignore[no-any-return]
    raise KeyError(f"player {pid} is not in game {game['game_id']}")


def _norm_interval_latent(mu: float, sd: float, lo: float, hi: float) -> tuple[float, float, float]:
    a = -INF if lo == -INF else (lo - mu) / sd
    b = INF if hi == INF else (hi - mu) / sd
    return a, b, max(0.0, norm_cdf(b) - norm_cdf(a))


def merge_legs(legs: Sequence[Mapping[str, Any]], game: Payload) -> list[Merged]:
    """Intersect legs per entity (comonotone). Leg forms:
    ``{"kind": "player", "player_id", "stat", "line", "side"}``,
    ``{"kind": "team_margin", "team", "gt"}`` (that team wins by more than ``gt``; ``gt = 0`` is
    "to win"; margins are integers so the caller passes an x.5 or a whole ``gt``),
    ``{"kind": "total", "line", "side"}``."""
    acc: dict[tuple[str, int, str], list[float]] = {}
    ents: dict[tuple[str, int, str], Entity] = {}
    g = game["game"]
    for leg in legs:
        kind = leg["kind"]
        if kind == "player":
            e = Entity("player", int(leg["player_id"]), str(leg["stat"]))
            lo, hi = line_to_bounds(float(leg["line"]), str(leg["side"]))
            lo_f, hi_f = float(lo), float(hi)
        elif kind == "team_margin":
            e = Entity("margin")
            gt = float(leg["gt"])
            if int(leg["team"]) == int(game["home_team"]):
                lo_f, hi_f = gt, INF
            elif int(leg["team"]) == int(game["away_team"]):
                lo_f, hi_f = -INF, -gt
            else:
                raise ValueError("team is not in the game")
        elif kind == "total":
            e = Entity("total")
            x = float(leg["line"])
            if str(leg["side"]) == "over":
                lo_f, hi_f = x, INF
            else:
                lo_f, hi_f = -INF, x
        else:
            raise ValueError(f"unknown leg kind {kind!r}")
        ents[e.key] = e
        cur = acc.get(e.key)
        if cur is None:
            acc[e.key] = [lo_f, hi_f]
        else:
            cur[0], cur[1] = max(cur[0], lo_f), min(cur[1], hi_f)
    out: list[Merged] = []
    for key, (lo_f, hi_f) in acc.items():
        e = ents[key]
        if e.kind == "player":
            grid = _player(game, e.player_id)["stats"][e.stat]
            p = interval_prob(grid, lo_f, hi_f)
            a = -INF if lo_f <= 0 else norm_ppf(1.0 - surv_at(grid, int(lo_f)))
            b = INF if hi_f == INF else norm_ppf(1.0 - surv_at(grid, int(hi_f) + 1))
            if hi_f < lo_f:
                a, b = INF, -INF
        elif e.kind == "margin":
            a, b, p = _norm_interval_latent(float(g["mu_m"]), float(g["sd_m"]), lo_f, hi_f)
        else:
            a, b, p = _norm_interval_latent(float(g["mu_t"]), float(g["sd_t"]), lo_f, hi_f)
        out.append(Merged(e, lo_f, hi_f, a, b, p))
    return out


# ---------------------------------------------------------------- correlation


def corr_lookup(table: Sequence[Sequence[Any]], rel: str, a: str, b: str) -> float:
    x, y = sorted((a, b))
    for r in table:
        if r[0] == rel and r[1] == x and r[2] == y:
            return float(r[3])
    return 0.0


def _team_of(game: Payload, pid: int) -> int:
    return int(_player(game, pid)["team"])


def pair_rho(e1: Entity, e2: Entity, game: Payload, table: Sequence[Sequence[Any]]) -> float:
    if e1.kind == "player" and e2.kind == "player":
        if e1.player_id == e2.player_id:
            return corr_lookup(table, "same_player", e1.stat, e2.stat)
        same_team = _team_of(game, e1.player_id) == _team_of(game, e2.player_id)
        return corr_lookup(table, "teammate" if same_team else "opponent", e1.stat, e2.stat)
    kinds = {e1.kind, e2.kind}
    if kinds == {"margin", "total"}:
        return corr_lookup(table, "game", "margin", "total")
    pl, other = (e1, e2) if e1.kind == "player" else (e2, e1)
    if other.kind == "total":
        return corr_lookup(table, "game", pl.stat, "total")
    sg = 1.0 if _team_of(game, pl.player_id) == int(game["home_team"]) else -1.0
    return sg * corr_lookup(table, "own_margin", pl.stat, "margin")


def cholesky(m: Sequence[Sequence[float]]) -> list[list[float]] | None:
    """Lower Cholesky factor, or None when the matrix is not positive definite (pivot <= 1e-10)."""
    n = len(m)
    c = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = m[i][j] - sum(c[i][t] * c[j][t] for t in range(j))
            if i == j:
                if s <= 1e-10:
                    return None
                c[i][i] = math.sqrt(s)
            else:
                c[i][j] = s / c[j][j]
    return c


def repair_to_pd(m: list[list[float]]) -> tuple[list[list[float]], float]:
    """Shrink toward the identity, ``(1 - t) R + t I``, with the smallest t on a 2^-20 grid that
    factors. Returns (matrix, t); t = 0 when R was already positive definite. A principal submatrix
    of a PSD matrix is PSD, so a payload table that was repaired for the whole game needs this
    only for a user-built subset that the full-game repair missed (none seen on 2023-24)."""
    if cholesky(m) is not None:
        return m, 0.0
    n = len(m)
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2.0
        r = [[(1 - mid) * m[i][j] + (mid if i == j else 0.0) for j in range(n)] for i in range(n)]
        if cholesky(r) is None:
            lo = mid
        else:
            hi = mid
    r = [[(1 - hi) * m[i][j] + (hi if i == j else 0.0) for j in range(n)] for i in range(n)]
    return r, hi


def build_corr(
    ents: Sequence[Entity], game: Payload, table: Sequence[Sequence[Any]]
) -> tuple[list[list[float]], float]:
    k = len(ents)
    m = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    for i in range(k):
        for j in range(i + 1, k):
            m[i][j] = m[j][i] = pair_rho(ents[i], ents[j], game, table)
    return repair_to_pd(m)


# ---------------------------------------------------------------- rectangle probabilities


def bivariate_rect(a1: float, b1: float, a2: float, b2: float, rho: float) -> float:
    """P(a1 < Z1 <= b1, a2 < Z2 <= b2) for a standard bivariate normal, by Simpson on the
    conditional form. Exact to about 1e-10."""
    if b1 <= a1 or b2 <= a2:
        return 0.0
    rho = max(-RHO_MAX, min(RHO_MAX, rho))
    s = math.sqrt(1.0 - rho * rho)
    lo, hi = max(a1, -_ZCLIP), min(b1, _ZCLIP)
    if hi <= lo:
        return 0.0
    n = SIMPSON_N
    h = (hi - lo) / n
    tot = 0.0
    for i in range(n + 1):
        z = lo + i * h
        inner = norm_cdf((b2 - rho * z) / s) - norm_cdf((a2 - rho * z) / s)
        w = 1 if i in (0, n) else (4 if i % 2 else 2)
        tot += w * norm_pdf(z) * inner
    return tot * h / 3.0


def _lcg_shifts(n_shifts: int, dim: int) -> list[list[float]]:
    x = 12345
    out: list[list[float]] = []
    for _ in range(n_shifts):
        row = []
        for _ in range(dim):
            x = (1664525 * x + 1013904223) % (2**32)
            row.append(x / 2**32)
        out.append(row)
    return out


def genz_rect(
    chol: Sequence[Sequence[float]],
    a: Sequence[float],
    b: Sequence[float],
    n_points: int = QMC_POINTS,
    n_shifts: int = QMC_SHIFTS,
) -> tuple[float, float]:
    """Genz (1992) separation-of-variables estimate of P(a < X <= b), X ~ N(0, C C^T), with a
    Richtmyer lattice (``frac(j sqrt(p_i) + shift_i)``) randomized by ``n_shifts`` fixed shifts.
    Returns (estimate, standard error across shifts). Deterministic."""
    k = len(a)
    if k == 1:
        c0 = chol[0][0]
        return max(0.0, norm_cdf(b[0] / c0) - norm_cdf(a[0] / c0)), 0.0
    alphas = [math.sqrt(_PRIMES[i]) for i in range(k - 1)]
    shifts = _lcg_shifts(n_shifts, k - 1)
    means: list[float] = []
    for sh in shifts:
        acc = 0.0
        for j in range(1, n_points + 1):
            y: list[float] = []
            d = norm_cdf(a[0] / chol[0][0])
            e = norm_cdf(b[0] / chol[0][0])
            f = e - d
            for i in range(1, k):
                w = (j * alphas[i - 1] + sh[i - 1]) % 1.0
                y.append(norm_ppf(min(max(d + w * (e - d), 1e-300), 1.0 - 1e-16)))
                s = sum(chol[i][t] * y[t] for t in range(i))
                d = norm_cdf((a[i] - s) / chol[i][i])
                e = norm_cdf((b[i] - s) / chol[i][i])
                f *= e - d
            acc += f
        means.append(acc / n_points)
    est = sum(means) / n_shifts
    var = sum((m - est) ** 2 for m in means) / (n_shifts - 1)
    return est, math.sqrt(var / n_shifts)


# ---------------------------------------------------------------- evaluation


@dataclass(frozen=True)
class ComboResult:
    p_joint: float  # P(all legs hit | every named player plays)
    p_independent: float  # product of the merged entities' marginals
    lift: float | None  # p_joint / p_independent (None when the product is 0)
    marginals: tuple[float, ...]  # per merged entity
    se: float  # standard error of p_joint (0 when exact)
    method: str  # marginal | exact_bivariate | qmc | product | empty
    p_all_play: float
    p_joint_incl_dnp: float
    repair_t: float
    script: str | None = None
    n_entities: int = 0


def _p_all_play(merged: Sequence[Merged], game: Payload) -> float:
    p = 1.0
    for m in merged:
        if m.entity.kind == "player" and m.entity.stat == _first_stat(merged, m.entity.player_id):
            pp = _player(game, m.entity.player_id).get("p_play")
            p *= 1.0 if pp is None else float(pp)  # null = availability unknown: conditional only
    return p


def _first_stat(merged: Sequence[Merged], pid: int) -> str:
    for m in merged:
        if m.entity.kind == "player" and m.entity.player_id == pid:
            return m.entity.stat
    return ""


def script_params(shared: Payload, script: str, stat: str, role: str) -> tuple[float, float]:
    for r in shared["scripts"]["params"]:
        if r[0] == script and r[1] == stat and r[2] == role:
            return float(r[3]), float(r[4])
    return 0.0, 1.0


def evaluate_game_legs(
    legs: Sequence[Mapping[str, Any]],
    game: Payload,
    shared: Payload,
    script: str | None = None,
    qmc: tuple[int, int] = (QMC_POINTS, QMC_SHIFTS),
) -> ComboResult:
    """Joint P of ``legs`` (all in ``game``) with the independent P and lift. ``qmc`` is
    (points, shifts) for three or more entities; the searches screen with a small budget and
    recompute their answers at the full one."""
    if not legs:
        raise ValueError("no legs")
    merged = merge_legs(legs, game)
    table = shared["corr"]
    if script is not None:
        if script not in SCRIPTS:
            raise ValueError(f"unknown script {script!r}")
        if any(m.entity.kind != "player" for m in merged):
            raise ValueError("game legs are not supported inside a script")
        for m in merged:
            if m.entity.kind == "player":
                role = "starter" if int(_player(game, m.entity.player_id)["starter"]) else "bench"
                mu, sg = script_params(shared, script, m.entity.stat, role)
                # infinite bounds stay infinite under a shift and a positive rescale
                m.a = m.a if math.isinf(m.a) else (m.a - mu) / sg
                m.b = m.b if math.isinf(m.b) else (m.b - mu) / sg
                m.p = max(0.0, norm_cdf(m.b) - norm_cdf(m.a))
    marg = tuple(m.p for m in merged)
    p_ind = math.prod(marg)
    p_play = _p_all_play(merged, game)
    if any(p <= 0.0 for p in marg) or any(m.b <= m.a for m in merged):
        return ComboResult(
            0.0,
            p_ind,
            None if p_ind == 0 else 0.0,
            marg,
            0.0,
            "empty",
            p_play,
            0.0,
            0.0,
            script,
            len(merged),
        )
    k = len(merged)
    repair_t = 0.0
    if k == 1:
        p, se, method = marg[0], 0.0, "marginal"
    else:
        r, repair_t = build_corr([m.entity for m in merged], game, table)
        a, b = [m.a for m in merged], [m.b for m in merged]
        if k == 2:
            p, se, method = bivariate_rect(a[0], b[0], a[1], b[1], r[0][1]), 0.0, "exact_bivariate"
        else:
            chol = cholesky(r)
            assert chol is not None
            p, se = genz_rect(chol, a, b, qmc[0], qmc[1])
            method = "qmc"
        # Frechet bounds: joint <= min marginal, joint >= sum - (k - 1)
        p = min(p, min(marg))
        p = max(p, max(0.0, sum(marg) - (k - 1)))
    lift = None if p_ind <= 0 else p / p_ind
    return ComboResult(p, p_ind, lift, marg, se, method, p_play, p_play * p, repair_t, script, k)


def evaluate_combo(
    legs: Sequence[Mapping[str, Any]],
    games: Mapping[str, Payload],
    shared: Payload,
    script: str | None = None,
) -> ComboResult:
    """Framing 4 (build your own). Each leg carries ``game_id``; legs of different games are
    independent, so their joints multiply (standard errors combine by the delta method on the
    product). ``lift`` compares against the product of all merged-entity marginals, so a pure
    cross-game combo has lift exactly 1."""
    by_game: dict[str, list[Mapping[str, Any]]] = {}
    for leg in legs:
        by_game.setdefault(str(leg["game_id"]), []).append(leg)
    parts = [evaluate_game_legs(v, games[g], shared, script) for g, v in sorted(by_game.items())]
    if len(parts) == 1:
        return parts[0]
    p = math.prod(r.p_joint for r in parts)
    p_ind = math.prod(r.p_independent for r in parts)
    rel2 = sum((r.se / r.p_joint) ** 2 for r in parts if r.p_joint > 0)
    marg = tuple(m for r in parts for m in r.marginals)
    play = math.prod(r.p_all_play for r in parts)
    method = "product" if all(r.method == "marginal" for r in parts) else "product_of_games"
    return ComboResult(
        p, p_ind, None if p_ind <= 0 else p / p_ind, marg, p * math.sqrt(rel2), method, play,
        play * p, max(r.repair_t for r in parts), script, sum(r.n_entities for r in parts),
    )  # fmt: skip


# ---------------------------------------------------------------- script priors


def script_definitions(game: Payload, shared: Payload) -> dict[str, dict[str, Any]]:
    """Checkable definitions of the four scripts for one game, from the payload only."""
    d = shared["scripts"]["definitions"]
    mu_t = float(game["game"]["mu_t"])
    return {
        "blowout": {"rule": f"|final margin| >= {d['blowout_abs_margin']}"},
        "close": {"rule": f"|final margin| <= {d['close_abs_margin']}"},
        "shootout": {
            "rule": f"final total >= {math.ceil(mu_t + d['total_delta'])}",
            "min_total": math.ceil(mu_t + d["total_delta"]),
        },
        "grind": {
            "rule": f"final total <= {math.floor(mu_t - d['total_delta'])}",
            "max_total": math.floor(mu_t - d["total_delta"]),
        },
    }


def script_prior(script: str, game: Payload, shared: Payload) -> float:
    """P(script) from the game's margin / total normals, integer-support corrected (a margin of 15
    is the continuous event M >= 14.5)."""
    g, d = game["game"], shared["scripts"]["definitions"]
    mu_m, sd_m, mu_t, sd_t = (float(g[k]) for k in ("mu_m", "sd_m", "mu_t", "sd_t"))
    if script == "blowout":
        t = d["blowout_abs_margin"] - 0.5
        return norm_cdf((-t - mu_m) / sd_m) + (1.0 - norm_cdf((t - mu_m) / sd_m))
    if script == "close":
        t = d["close_abs_margin"] + 0.5
        return norm_cdf((t - mu_m) / sd_m) - norm_cdf((-t - mu_m) / sd_m)
    if script == "shootout":
        return 1.0 - norm_cdf((math.ceil(mu_t + d["total_delta"]) - 0.5 - mu_t) / sd_t)
    if script == "grind":
        return norm_cdf((math.floor(mu_t - d["total_delta"]) + 0.5 - mu_t) / sd_t)
    raise ValueError(f"unknown script {script!r}")


# ---------------------------------------------------------------- framing 1: game-script lift

_REASONS = {
    (
        "same_player",
        "pts",
        "reb",
    ): "same player: points and rebounds both rise and fall with minutes played",
    (
        "same_player",
        "ast",
        "pts",
    ): "same player: points and assists both rise and fall with minutes and usage",
    (
        "same_player",
        "fg3m",
        "pts",
    ): "same player: threes are points, so a hot or cold shooting night moves both",
    (
        "same_player",
        "ast",
        "reb",
    ): "same player: assists and rebounds both rise and fall with minutes",
    ("same_player", "ast", "fg3m"): "same player: minutes and touches move both",
    ("same_player", "fg3m", "reb"): "same player: both need minutes on the floor",
}


def pair_reason(e1: Entity, e2: Entity, rho: float) -> str:
    """One plain-words reason for the pair, from the relation and the pooled correlation."""
    if e1.kind == "player" and e2.kind == "player" and e1.player_id == e2.player_id:
        x, y = sorted((e1.stat, e2.stat))
        base = _REASONS.get(("same_player", x, y), "same player: shared minutes")
        return f"{base} (latent r = {rho:+.2f})"
    if abs(rho) < 0.05:
        return f"different players: no reliable link (latent r = {rho:+.2f}), treated as near independent"
    return f"different players: weak link through game pace and margin (latent r = {rho:+.2f})"


def main_line_legs(game: Payload, side: str) -> list[dict[str, Any]]:
    out = []
    for p in game["players"]:
        for stat, grid in p["stats"].items():
            ml = grid.get("main_line")
            if ml is None:
                continue
            out.append(
                {"kind": "player", "game_id": str(game["game_id"]), "player_id": int(p["player_id"]),
                 "stat": stat, "line": float(ml), "side": side}
            )  # fmt: skip
    return out


def lift_ranking(
    game: Payload, shared: Payload, top_n: int = 10, max_legs: int = 3, min_p_joint: float = 0.02
) -> list[dict[str, Any]]:
    """Framing 1. Combos of legs at posted MAIN lines (all overs or all unders), ranked by
    lift = joint / product of marginals. Pairs of any two entities (exact); triples only when they
    contain a same-player pair (cross-player legs add nothing measurable, see the contract). Combos
    with joint below ``min_p_joint`` are dropped (a huge lift on a 0.1% event is noise)."""
    rows: list[dict[str, Any]] = []
    table = shared["corr"]
    for side in ("over", "under"):
        legs = main_line_legs(game, side)
        for size in range(2, max_legs + 1):
            for combo in combinations(legs, size):
                ids = [(c["player_id"], c["stat"]) for c in combo]
                if len(set(ids)) < size:
                    continue
                if size >= 3 and not _has_same_player_pair(combo):
                    continue
                res = evaluate_game_legs(combo, game, shared, qmc=SCREEN_QMC)
                if res.lift is None or res.p_joint < min_p_joint:
                    continue
                rows.append({"legs": [dict(c) for c in combo], "side": side, "lift": res.lift,
                             "p_joint": res.p_joint})  # fmt: skip
    rows.sort(key=lambda r: (-r["lift"], -r["p_joint"], str(r["legs"])))
    out: list[dict[str, Any]] = []
    for r in rows[: max(3 * top_n, top_n)]:  # recompute the screened leaders at the full budget
        res = evaluate_game_legs(r["legs"], game, shared)
        if res.lift is None or res.p_joint < min_p_joint:
            continue
        pe1, pe2, prho = _strongest_pair(r["legs"], game, table)
        out.append(
            {"legs": r["legs"], "side": r["side"], "p_joint": res.p_joint,
             "p_independent": res.p_independent, "lift": res.lift, "se": res.se,
             "method": res.method, "reason": pair_reason(pe1, pe2, prho)}
        )  # fmt: skip
    out.sort(key=lambda r: (-r["lift"], -r["p_joint"], str(r["legs"])))
    return out[:top_n]


def _has_same_player_pair(combo: Sequence[Mapping[str, Any]]) -> bool:
    pids = [c["player_id"] for c in combo]
    return len(set(pids)) < len(pids)


def _strongest_pair(
    combo: Sequence[Mapping[str, Any]], game: Payload, table: Sequence[Sequence[Any]]
) -> tuple[Entity, Entity, float]:
    best: tuple[Entity, Entity, float] | None = None
    for c1, c2 in combinations(combo, 2):
        e1 = Entity("player", int(c1["player_id"]), str(c1["stat"]))
        e2 = Entity("player", int(c2["player_id"]), str(c2["stat"]))
        r = pair_rho(e1, e2, game, table)
        if best is None or abs(r) > abs(best[2]):
            best = (e1, e2, r)
    assert best is not None
    return best


# ---------------------------------------------------------------- framing 2: target probability

TARGET_P_MIN = 0.35  # no lottery-ticket legs
TARGET_P_MAX = 0.85  # no near-certain filler legs (the degenerate "lowest lines" answer)


def candidate_legs(
    game: Payload,
    p_min: float = TARGET_P_MIN,
    p_max: float = TARGET_P_MAX,
    floor_mode: str = "p_band",
) -> list[dict[str, Any]]:
    """Over legs on the integer grid whose marginal lies in [p_min, p_max]. ``floor_mode``:
    ``p_band`` (default) or ``main_line`` (additionally the line must be at or above the posted
    main line, so "no easier than the book's own line")."""
    out = []
    for p in game["players"]:
        for stat, grid in p["stats"].items():
            ml = grid.get("main_line")
            k0, n = int(grid["k0"]), len(grid["surv"])
            for k in range(max(1, k0), k0 + n):
                s = surv_at(grid, k)
                if not p_min <= s <= p_max:
                    continue
                line = k - 0.5
                if floor_mode == "main_line" and (ml is None or line < float(ml)):
                    continue
                out.append(
                    {"kind": "player", "game_id": str(game["game_id"]), "player_id": int(p["player_id"]),
                     "stat": stat, "line": line, "side": "over", "p": s}
                )  # fmt: skip
    return out


def target_search(
    game: Payload,
    shared: Payload,
    target: float,
    k_legs: int,
    top_n: int = 5,
    beam: int = 40,
    floor_mode: str = "p_band",
    p_min: float = TARGET_P_MIN,
    p_max: float = TARGET_P_MAX,
) -> dict[str, Any]:
    """Framing 2. OBJECTIVE (explicit): among combos of ``k_legs`` over legs on distinct
    (player, stat) entities of one game, each leg's marginal in [p_min, p_max], with
    ``p_joint >= target``, MAXIMISE lift = p_joint / product of marginals; ties by the smaller
    excess ``p_joint - target``, then by a fixed key. The marginal band is the guard against the
    degenerate answer (stacking near-certain lowest lines to hit any target at lift 1).
    Beam search over legs added one at a time (width ``beam``, ranked by lift among partial combos
    that could still reach the target); the final answer is exact for k = 2 and a seeded QMC
    estimate beyond. When no combo reaches the target the result says so and returns the highest
    joint found (``feasible = False``)."""
    cands = candidate_legs(game, p_min, p_max, floor_mode)
    frontier: list[tuple[Sequence[Mapping[str, Any]], float]] = [((), 1.0)]
    for _ in range(k_legs):
        nxt: dict[
            tuple[tuple[int, str, float], ...], tuple[list[Mapping[str, Any]], float, float]
        ] = {}
        for combo, _score in frontier:
            used = {(c["player_id"], c["stat"]) for c in combo}
            for c in cands:
                if (c["player_id"], c["stat"]) in used:
                    continue
                new = [*combo, c]
                key = tuple(sorted((x["player_id"], x["stat"], x["line"]) for x in new))
                if key in nxt:
                    continue
                res = evaluate_game_legs(new, game, shared, qmc=SCREEN_QMC)
                lift = res.lift if res.lift is not None else 0.0
                nxt[key] = (new, lift, res.p_joint)
        ranked = sorted(nxt.values(), key=lambda t: (-t[1], -t[2], str(t[0])))
        frontier = [(t[0], t[1]) for t in ranked[: max(beam, top_n)]]
        if not frontier:
            break
    final: list[dict[str, Any]] = []
    for combo, _ in frontier:
        if len(combo) != k_legs:
            continue
        res = evaluate_game_legs(combo, game, shared)
        final.append(
            {"legs": [{k: v for k, v in c.items() if k != "p"} for c in combo],
             "p_joint": res.p_joint, "p_independent": res.p_independent, "lift": res.lift,
             "se": res.se, "method": res.method}
        )  # fmt: skip
    ok = [r for r in final if r["p_joint"] >= target]
    ok.sort(key=lambda r: (-(r["lift"] or 0.0), r["p_joint"] - target, str(r["legs"])))
    if ok:
        return {"target": target, "k_legs": k_legs, "feasible": True, "combos": ok[:top_n],
                "objective": "max lift s.t. p_joint >= target, marginals in band",
                "band": [p_min, p_max], "floor_mode": floor_mode}  # fmt: skip
    final.sort(key=lambda r: (-r["p_joint"], str(r["legs"])))
    return {"target": target, "k_legs": k_legs, "feasible": False, "combos": final[:top_n],
            "objective": "max lift s.t. p_joint >= target, marginals in band",
            "band": [p_min, p_max], "floor_mode": floor_mode}  # fmt: skip
