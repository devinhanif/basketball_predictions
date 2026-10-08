"""Same-game joint model: game latents (margin, total) + player-stat latents in one copula.

Entities (one latent each): the game margin (home minus away), the game total, and every
(player, stat). Legs map onto entities: win/spread -> margin, total -> total, player N+ ->
(player, stat). Legs on the same entity share one latent, so win and spread legs are coherent
by construction (e.g. P(win) >= P(win by over 7.5) in every simulated game).

Correlations are pooled by relation and stat pair, estimated from same-game normal-score
residuals (randomized PIT -> Phi^-1), shrunk toward 0 and PSD-repaired:
  same_player / teammate / opponent  x (stat_a, stat_b)
  own_margin  (player vs the margin signed toward the player's own team)
  game        (player vs total; margin vs total)

Player marginals are CONDITIONAL on playing; DNP is handled outside the copula, assuming
availability is independent of the other latents:
  void  : a DNP leg voids the whole contract (stake and fee refunded, net 0)
  loss  : a DNP leg loses
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy import stats

from nba.parlay.copula import FloatArr, PairCorr, nearest_correlation, sample_latent_uniforms
from nba.parlay.legs import Leg
from nba.parlay.qdist import QuantileGridDist

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
GAME_STATS = ("win", "spread", "total")
REL_CODE = {"same_player": 0, "teammate": 1, "opponent": 2}
_REL_NAME = {v: k for k, v in REL_CODE.items()}
DNP_POLICIES = ("void", "loss")
_PAIR = len(STATS) ** 2


def _key(rel: str, a: str, b: str) -> tuple[str, str, str]:
    x, y = sorted((a, b))
    return (rel, x, y)


@dataclass(frozen=True)
class GameCtx:
    """Everything the joint model needs about one game (all as-of / pre-game)."""

    game_id: str
    home_team: int
    away_team: int
    mu_m: float
    sd_m: float
    mu_t: float
    sd_t: float
    dists: Mapping[tuple[int, str], QuantileGridDist] = field(default_factory=dict)
    team_of: Mapping[int, int] = field(default_factory=dict)
    p_play: Mapping[int, float] = field(default_factory=dict)


def is_game_leg(leg: Leg) -> bool:
    return leg.stat in GAME_STATS


def entity_of(leg: Leg) -> tuple[str, str, int, str]:
    if leg.stat in ("win", "spread"):
        return ("margin", leg.game_id, 0, "margin")
    if leg.stat == "total":
        return ("total", leg.game_id, 0, "total")
    return ("player", leg.game_id, leg.player_id, leg.stat)


def _sign(leg: Leg, ctx: GameCtx) -> float:
    if leg.team_id is None or leg.team_id not in (ctx.home_team, ctx.away_team):
        raise ValueError(f"{leg.stat} leg needs team_id in the game, got {leg.team_id}")
    return 1.0 if leg.team_id == ctx.home_team else -1.0


def leg_marginal(leg: Leg, ctx: GameCtx) -> float:
    """P(leg hits), side-adjusted, conditional on playing."""
    if leg.stat in ("win", "spread"):
        sg = _sign(leg, ctx)
        thr = 0.0 if leg.stat == "win" else leg.threshold
        # sg * M > thr, M ~ N(mu, sd)
        p = float(stats.norm.sf((thr - sg * ctx.mu_m) / ctx.sd_m))
    elif leg.stat == "total":
        p = float(stats.norm.sf((leg.threshold - ctx.mu_t) / ctx.sd_t))
    else:
        d = ctx.dists.get((leg.player_id, leg.stat))
        if d is None:
            raise KeyError(f"no distribution for player {leg.player_id} stat {leg.stat}")
        p = d.p_ge(leg.threshold)
    return p if leg.side == "yes" else 1.0 - p


def leg_hits(leg: Leg, u: NDArray[np.float64], ctx: GameCtx) -> NDArray[np.bool_]:
    """Boolean hit vector from the leg's entity uniforms ``u``."""
    uc = np.clip(u, 1e-12, 1 - 1e-12)
    if leg.stat in ("win", "spread"):
        m = ctx.mu_m + ctx.sd_m * stats.norm.ppf(uc)
        thr = 0.0 if leg.stat == "win" else leg.threshold
        yes = _sign(leg, ctx) * m > thr
    elif leg.stat == "total":
        yes = ctx.mu_t + ctx.sd_t * stats.norm.ppf(uc) > leg.threshold
    else:
        d = ctx.dists[(leg.player_id, leg.stat)]
        yes = uc > 1.0 - d.p_ge(leg.threshold)
    return np.asarray(yes if leg.side == "yes" else ~yes, dtype=bool)


def p_all_play(legs: Sequence[Leg], ctxs: Mapping[str, GameCtx]) -> float:
    """P(every distinct player in the legs plays), availability independent across players."""
    seen: dict[tuple[str, int], float] = {}
    for leg in legs:
        if is_game_leg(leg):
            continue
        seen[(leg.game_id, leg.player_id)] = ctxs[leg.game_id].p_play.get(leg.player_id, 1.0)
    return float(np.prod(list(seen.values()))) if seen else 1.0


def effective_prob(p_cond: float, p_nov: float, policy: str, cost: float) -> float:
    """Probability-equivalent for EV = p_eff - cost under a DNP policy.

    loss: P(all play and all hit).  void: refund of ``cost`` on a DNP, so
    EV = p_nov * (p_cond - cost)  <=>  p_eff = p_nov * p_cond + (1 - p_nov) * cost.
    """
    if policy == "loss":
        return p_nov * p_cond
    if policy == "void":
        return p_nov * p_cond + (1.0 - p_nov) * cost
    raise ValueError(f"dnp_leg_policy must be one of {DNP_POLICIES}, got {policy!r}")


@dataclass(frozen=True)
class JointOutput:
    p_cond: float  # P(all legs hit | every player plays)
    hits: int
    n_sims: int  # 0 for closed form
    marginals: tuple[float, ...]
    p_nov: float  # P(no leg voided by a DNP)


def entity_corr(
    entities: Sequence[tuple[str, str, int, str]], ctxs: Mapping[str, GameCtx], pc: PairCorr
) -> FloatArr:
    k = len(entities)
    m = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            ei, ej = entities[i], entities[j]
            if ei[1] != ej[1]:
                continue
            ctx = ctxs[ei[1]]
            m[i, j] = m[j, i] = _pair_rho(ei, ej, ctx, pc)
    return nearest_correlation(m) if k > 1 else m


def _team(ctx: GameCtx, pid: int) -> int | None:
    return ctx.team_of.get(pid)


def _pair_rho(
    ei: tuple[str, str, int, str], ej: tuple[str, str, int, str], ctx: GameCtx, pc: PairCorr
) -> float:
    kinds = {ei[0], ej[0]}
    if ei[0] == "player" and ej[0] == "player":
        ta, tb = _team(ctx, ei[2]), _team(ctx, ej[2])
        if ei[2] == ej[2]:
            rel = "same_player"
        elif ta is None or tb is None:
            return 0.0
        else:
            rel = "teammate" if ta == tb else "opponent"
        return pc.get(rel, ei[3], ej[3])
    if kinds == {"margin", "total"}:
        return pc.get("game", "margin", "total")
    pl_e = ei if ei[0] == "player" else ej
    other = ej if ei[0] == "player" else ei
    if other[0] == "total":
        return pc.get("game", pl_e[3], "total")
    t = _team(ctx, pl_e[2])
    if t is None:
        return 0.0
    sg = 1.0 if t == ctx.home_team else -1.0
    return sg * pc.get("own_margin", pl_e[3], "margin")


@dataclass
class JointModel:
    kind: str  # independence | gaussian | t
    corr: PairCorr
    n_sims: int = 20000
    seed: int = 0
    t_df: float = 6.0

    def joint_many(
        self, parlays: Sequence[Sequence[Leg]], ctxs: Mapping[str, GameCtx]
    ) -> list[JointOutput]:
        """Joint P for each parlay on shared draws (common random numbers across parlays)."""
        out: list[JointOutput] = []
        if self.kind == "independence":
            for legs in parlays:
                marg = tuple(leg_marginal(leg, ctxs[leg.game_id]) for leg in legs)
                out.append(JointOutput(float(np.prod(marg)), 0, 0, marg, p_all_play(legs, ctxs)))
            return out
        ents: list[tuple[str, str, int, str]] = []
        idx: dict[tuple[str, str, int, str], int] = {}
        for legs in parlays:
            for leg in legs:
                e = entity_of(leg)
                if e not in idx:
                    idx[e] = len(ents)
                    ents.append(e)
        if not ents:
            return out
        r = entity_corr(ents, ctxs, self.corr)
        gseed = zlib.crc32("|".join(sorted({e[1] for e in ents})).encode()) % (2**31)
        kind = "gaussian" if self.kind == "gaussian" else "t"
        u = sample_latent_uniforms(r, self.n_sims, self.seed + gseed, kind, self.t_df)
        for legs in parlays:
            ok = np.ones(self.n_sims, dtype=bool)
            for leg in legs:
                ok &= leg_hits(leg, u[:, idx[entity_of(leg)]], ctxs[leg.game_id])
            h = int(ok.sum())
            marg = tuple(leg_marginal(leg, ctxs[leg.game_id]) for leg in legs)
            # Jeffreys-style smoothing keeps log loss finite when no draw hits.
            out.append(
                JointOutput(
                    (h + 0.5) / (self.n_sims + 1.0), h, self.n_sims, marg, p_all_play(legs, ctxs)
                )
            )
        return out

    def joint(self, legs: Sequence[Leg], ctxs: Mapping[str, GameCtx]) -> JointOutput:
        return self.joint_many([legs], ctxs)[0]


@dataclass(frozen=True)
class GameResiduals:
    """Normal-score residuals of one game (inputs to correlation estimation)."""

    home_team: int
    z_margin: float  # home-signed
    z_total: float
    player: NDArray[np.int64]
    team: NDArray[np.int64]
    stat: NDArray[np.int64]  # index into STATS
    z: NDArray[np.float64]


def estimate_game_corr(games: Iterable[GameResiduals], shrink_k: float) -> PairCorr:
    """Pearson correlations of same-game normal scores by relation/stat pair, shrunk to 0."""
    n_keys = 3 * _PAIR + 4 + 4 + 1
    acc = np.zeros((6, n_keys))  # n, sx, sy, sxx, syy, sxy

    def add(key: NDArray[np.int64], x: NDArray[np.float64], y: NDArray[np.float64]) -> None:
        w = (np.ones_like(x), x, y, x * x, y * y, x * y)
        for i, wi in enumerate(w):
            acc[i] += np.bincount(key, weights=wi, minlength=n_keys)

    s = len(STATS)
    for g in games:
        m = len(g.z)
        if m:
            ii, jj = np.triu_indices(m, 1)
            a, b = g.stat[ii], g.stat[jj]
            swap = a > b
            x = np.where(swap, g.z[jj], g.z[ii])
            y = np.where(swap, g.z[ii], g.z[jj])
            sa, sb = np.minimum(a, b), np.maximum(a, b)
            rel = np.where(
                g.player[ii] == g.player[jj], 0, np.where(g.team[ii] == g.team[jj], 1, 2)
            )
            add((rel * _PAIR + sa * s + sb).astype(np.int64), x, y)
            own = np.where(g.team == g.home_team, g.z_margin, -g.z_margin)
            add(3 * _PAIR + g.stat, own, g.z)
            add(3 * _PAIR + s + g.stat, np.full(m, g.z_total), g.z)
        add(
            np.array([3 * _PAIR + 2 * s], dtype=np.int64),
            np.array([g.z_margin]),
            np.array([g.z_total]),
        )
    rho: dict[tuple[str, str, str], float] = {}
    n: dict[tuple[str, str, str], int] = {}
    for kid in range(n_keys):
        cnt = acc[0, kid]
        if cnt < 3:
            continue
        sx, sy, sxx, syy, sxy = acc[1:, kid]
        vx, vy = sxx / cnt - (sx / cnt) ** 2, syy / cnt - (sy / cnt) ** 2
        if vx < 1e-12 or vy < 1e-12:
            continue
        r = (sxy / cnt - sx * sy / cnt**2) / np.sqrt(vx * vy)
        if kid < 3 * _PAIR:
            rel_i, rest = divmod(kid, _PAIR)
            sa_i, sb_i = divmod(rest, s)
            key = _key(_REL_NAME[rel_i], STATS[sa_i], STATS[sb_i])
        elif kid < 3 * _PAIR + s:
            key = _key("own_margin", STATS[kid - 3 * _PAIR], "margin")
        elif kid < 3 * _PAIR + 2 * s:
            key = _key("game", STATS[kid - 3 * _PAIR - s], "total")
        else:
            key = _key("game", "margin", "total")
        rho[key] = float(r) * cnt / (cnt + shrink_k)
        n[key] = int(cnt)
    return PairCorr(rho=rho, n=n)


def corr_to_json(pc: PairCorr) -> list[list[object]]:
    return [[*k, pc.rho[k], pc.n.get(k, 0)] for k in sorted(pc.rho)]


def corr_from_json(rows: Sequence[Sequence[object]]) -> PairCorr:
    rho: dict[tuple[str, str, str], float] = {}
    n: dict[tuple[str, str, str], int] = {}
    for r in rows:
        key = (str(r[0]), str(r[1]), str(r[2]))
        rho[key] = float(str(r[3]))
        n[key] = int(str(r[4]))
    return PairCorr(rho=rho, n=n)
