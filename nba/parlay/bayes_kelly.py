"""Bayesian-Kelly stake sizer for a night's recommendations (advice only; no orders, no keys).

Per bet we hold a logit-normal posterior for the probability that OUR side wins. The centre is
a de-vigged sharp price (or a source mean), the width is the spread between sources, and the
posterior is shrunk toward the Kalshi price by a "market is roughly efficient" prior
(``prior_edge_sd``). Stakes are whole contracts that maximise expected log wealth over the
posterior, with Kalshi's per-ORDER round-up fee (taker or maker, from configs/parlay.yaml),
the nightly loss cap, the 50/50 steady/swing split and same-game dependence (Gaussian copula
on the win events, the same construction as nba/parlay/copula.py). The soft floor only warns.

Two facts shape the design (see docs/BAYES_KELLY.md):
  * One bet resolves once, so parameter uncertainty integrates out: its win probability is the
    posterior MEAN. Uncertainty therefore matters through (a) shrinkage toward the market and
    (b) errors shared by tonight's bets (``shared_error_rho``), which make outcomes correlated.
  * Single-bet closed form (fee included in c): n* = B (q - c) / (c (1 - c)).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from numpy.typing import NDArray
from scipy import stats
from scipy.stats import qmc

from nba.parlay.config import DEFAULT_PATH, FeeModel, load_config
from nba.parlay.copula import nearest_correlation
from nba.parlay.ev import NO_POSITIVE_EV, order_fee

SIZED = "sized_by_posterior_log_wealth"
ADVICE = "advice only: Devin places every order by hand; no stake is a recommendation to bet"
FloatArr = NDArray[np.float64]


@dataclass(frozen=True)
class BKConfig:
    prior_edge_sd: float
    shared_error_rho: float
    same_game_rho: float
    min_sd: float
    n_prior_k: float
    steady_threshold: float
    steady_share: float
    floor_base: float
    floor_monthly_growth: float
    z_nodes: int
    idio_nodes: int
    copula_points: int
    seed: int
    max_bets: int
    max_group: int


def load_bk_config(path: str | Path = DEFAULT_PATH) -> BKConfig:
    raw = yaml.safe_load(Path(path).read_text())["bayes_kelly"]
    return BKConfig(
        prior_edge_sd=float(raw["prior_edge_sd"]),
        shared_error_rho=float(raw["shared_error_rho"]),
        same_game_rho=float(raw["same_game_rho"]),
        min_sd=float(raw["min_sd"]),
        n_prior_k=float(raw["n_prior_k"]),
        steady_threshold=float(raw["steady_threshold"]),
        steady_share=float(raw["steady_share"]),
        floor_base=float(raw["floor_base"]),
        floor_monthly_growth=float(raw["floor_monthly_growth"]),
        z_nodes=int(raw["z_nodes"]),
        idio_nodes=int(raw["idio_nodes"]),
        copula_points=int(raw["copula_points"]),
        seed=int(raw["seed"]),
        max_bets=int(raw["max_bets"]),
        max_group=int(raw["max_group"]),
    )


def soft_floor(cfg: BKConfig, months: float) -> float:
    """The compounding soft floor: base x (1 + g) ** months (DECISIONS 2026-10-22/23)."""
    return float(cfg.floor_base * (1.0 + cfg.floor_monthly_growth) ** months)


# --------------------------------------------------------------------------- inputs
@dataclass(frozen=True)
class Bet:
    """One side of one market. ``price`` is dollars per contract for THIS side; ``p_center`` is
    the probability this side wins. Give ``p_center`` + ``p_sd`` or ``sources``."""

    ticker: str
    side: str
    price: float
    p_center: float | None = None
    p_sd: float | None = None
    sources: Mapping[str, float] = field(default_factory=dict)
    game: str | None = None
    tier: str | None = None
    order_type: str | None = None
    n_settled: int | None = None
    market_p: float | None = None

    @property
    def series(self) -> str:
        return self.ticker.split("-")[0]


def parse_bets(raw: Any) -> tuple[list[Bet], list[tuple[str, str, float]]]:
    """bets.json: a list of bets, or {"bets": [...], "corr": [{"a","b","rho"}, ...]}."""
    corr_raw: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        corr_raw = list(raw.get("corr", []))
        raw = raw["bets"]
    bets: list[Bet] = []
    for d in raw:
        src = {str(k): float(v) for k, v in (d.get("sources") or {}).items()}
        bets.append(
            Bet(
                ticker=str(d["ticker"]),
                side=str(d.get("side", "")),
                price=float(d["price"]),
                p_center=None if d.get("p_center") is None else float(d["p_center"]),
                p_sd=None if d.get("p_sd") is None else float(d["p_sd"]),
                sources=src,
                game=None if d.get("game") is None else str(d["game"]),
                tier=None if d.get("tier") is None else str(d["tier"]),
                order_type=None if d.get("order_type") is None else str(d["order_type"]),
                n_settled=None if d.get("n_settled") is None else int(d["n_settled"]),
                market_p=None if d.get("market_p") is None else float(d["market_p"]),
            )
        )
    tickers = [b.ticker for b in bets]
    if len(set(tickers)) != len(tickers):
        raise ValueError("duplicate ticker in bets")
    for b in bets:
        if not 0.0 < b.price < 1.0:
            raise ValueError(f"{b.ticker}: price must be in (0, 1) dollars")
        if b.tier not in (None, "steady", "swing"):
            raise ValueError(f"{b.ticker}: tier must be steady|swing")
    corr = [(str(c["a"]), str(c["b"]), float(c["rho"])) for c in corr_raw]
    for a, b2, rho in corr:
        if a not in tickers or b2 not in tickers:
            raise ValueError(f"corr names an unknown ticker: {a}, {b2}")
        if not -1.0 < rho < 1.0:
            raise ValueError("corr rho must be in (-1, 1)")
    return bets, corr


# --------------------------------------------------------------------------- posterior
def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(x: FloatArr) -> FloatArr:
    return np.asarray(1.0 / (1.0 + np.exp(-x)), dtype=float)


def centre_and_sd(bet: Bet, cfg: BKConfig) -> tuple[float, float]:
    """(centre, sd) on the probability scale. Sources: Pinnacle if present is the centre, else
    the mean; sd = RMS deviation of the OTHER sources from the centre, floored at ``min_sd``."""
    if bet.p_center is not None:
        return bet.p_center, 0.0 if bet.p_sd is None else bet.p_sd
    if not bet.sources:
        raise ValueError(f"{bet.ticker}: give p_center (+ p_sd) or sources")
    vals = bet.sources
    if "pinnacle" in vals:
        centre = vals["pinnacle"]
        dev = [v - centre for k, v in vals.items() if k != "pinnacle"]
    else:
        centre = float(np.mean(list(vals.values())))
        dev = [v - centre for v in vals.values()]
    sd = math.sqrt(sum(x * x for x in dev) / len(dev)) if dev else 0.0
    if bet.p_sd is not None:
        sd = bet.p_sd
    return centre, max(sd, cfg.min_sd)


@dataclass(frozen=True)
class Posterior:
    mu: float  # logit centre of the posterior
    sd: float  # logit sd of the posterior
    centre: float  # probability-scale centre used (after optional n shrink)
    sd_p: float  # input sd on the probability scale
    market: float  # market prior centre
    mean_p: float  # posterior mean win probability (the one-shot predictive)
    shrink_weight: float  # weight on the estimate vs the market prior (1 = no shrink)


def posterior(bet: Bet, cfg: BKConfig) -> Posterior:
    centre, sd_p = centre_and_sd(bet, cfg)
    if not 0.0 < centre < 1.0:
        raise ValueError(f"{bet.ticker}: p_center must be in (0, 1)")
    if sd_p < 0.0:
        raise ValueError(f"{bet.ticker}: p_sd must be >= 0")
    market = bet.market_p if bet.market_p is not None else bet.sources.get("kalshi_mid", bet.price)
    if bet.n_settled is not None:  # optional n-based shrink of the estimate toward the market
        w_n = bet.n_settled / (bet.n_settled + cfg.n_prior_k)
        centre = market + w_n * (centre - market)
    mu_m = _logit(centre)
    s_m = sd_p / (centre * (1.0 - centre))
    mu0 = _logit(market)
    if s_m == 0.0 or math.isinf(cfg.prior_edge_sd):
        mu, s, w = mu_m, s_m, 1.0  # certain estimate (or no prior): no shrink
    elif cfg.prior_edge_sd <= 0.0:
        mu, s, w = mu0, 0.0, 0.0  # "the market is exactly right"
    else:
        s0 = cfg.prior_edge_sd / (market * (1.0 - market))
        prec_m, prec_0 = 1.0 / s_m**2, 1.0 / s0**2
        var = 1.0 / (prec_m + prec_0)
        mu = var * (mu_m * prec_m + mu0 * prec_0)
        s, w = math.sqrt(var), prec_m / (prec_m + prec_0)
    mean_p = float(_p_given_z(np.array([mu]), np.array([s]), 0.0, 0.0, 15)[0, 0])
    return Posterior(mu, s, centre, sd_p, market, mean_p, w)


def _p_given_z(mu: FloatArr, s: FloatArr, rho: float, z: float, n_idio: int) -> NDArray[np.float64]:
    """P(win | shared factor z), averaging out each bet's independent part. Shape (1, n_bets)."""
    e, we = np.polynomial.hermite_e.hermegauss(n_idio)
    we = we / we.sum()
    lin = mu[None, :] + s[None, :] * (math.sqrt(rho) * z + math.sqrt(1.0 - rho) * e[:, None])
    return np.asarray((_sigmoid(lin) * we[:, None]).sum(axis=0, keepdims=True), dtype=float)


# --------------------------------------------------------------------------- fees and cost
def order_cost(bet: Bet, n: int, fee: FeeModel, order_type: str) -> tuple[float, float]:
    """(total cost, fee) of ``n`` whole contracts: n * price + one round-up fee for the order."""
    if n <= 0:
        return 0.0, 0.0
    f = order_fee(bet.price, fee, n, order_type=order_type, series=bet.series)
    return round(n * bet.price + f, 2), f


# --------------------------------------------------------------------------- outcome law
def _group_cmat(
    members: Sequence[int], bets: Sequence[Bet], corr: Mapping[frozenset[str], float], rho0: float
) -> FloatArr:
    k = len(members)
    r = np.eye(k)
    for a in range(k):
        for b in range(a + 1, k):
            key = frozenset((bets[members[a]].ticker, bets[members[b]].ticker))
            r[a, b] = r[b, a] = corr.get(key, rho0)
    return nearest_correlation(r) if k > 1 else r


def outcome_law(
    bets: Sequence[Bet],
    posts: Sequence[Posterior],
    corr: Sequence[tuple[str, str, float]],
    cfg: BKConfig,
) -> tuple[FloatArr, FloatArr]:
    """(outcomes (K, n) of 0/1 wins, pmf (K,)) = mixture over the shared error factor of the
    product over games of each game's Gaussian-copula joint. Exact for single-leg games."""
    n = len(bets)
    mu = np.array([p.mu for p in posts])
    s = np.array([p.sd for p in posts])
    corr_map = {frozenset((a, b)): r for a, b, r in corr}
    groups: dict[str, list[int]] = {}
    for i, b in enumerate(bets):
        groups.setdefault(b.game if b.game is not None else f"__solo_{i}", []).append(i)
    for g in groups.values():
        if len(g) > cfg.max_group:
            raise ValueError(f"at most {cfg.max_group} legs per game")
    zs, wz = np.polynomial.hermite_e.hermegauss(cfg.z_nodes)
    wz = wz / wz.sum()
    prepared: list[tuple[list[int], FloatArr | None]] = []
    for g in groups.values():
        if len(g) == 1:
            prepared.append((g, None))
            continue
        r = _group_cmat(g, bets, corr_map, cfg.same_game_rho)
        sob = qmc.Sobol(d=len(g), scramble=True, seed=cfg.seed)
        u = np.clip(sob.random(cfg.copula_points), 1e-12, 1 - 1e-12)
        prepared.append((g, stats.norm.ppf(u) @ np.linalg.cholesky(r).T))
    total = np.zeros((2,) * n)
    for z, w in zip(zs, wz, strict=True):
        p = _p_given_z(mu, s, cfg.shared_error_rho, float(z), cfg.idio_nodes)[0]
        full = np.ones((2,) * n)
        for g, x in prepared:
            k = len(g)
            arr = np.zeros((2,) * k)
            if x is None:
                arr[0], arr[1] = 1.0 - p[g[0]], p[g[0]]
            else:
                thr = stats.norm.ppf(p[g])
                wins = (x <= thr[None, :]).astype(int)
                np.add.at(arr, tuple(wins[:, j] for j in range(k)), 1.0 / len(x))
            shape = [2 if i in g else 1 for i in range(n)]
            full = full * arr.reshape(shape)
        total += w * full
    outcomes = np.array(list(itertools.product([0, 1], repeat=n)), dtype=float)
    return outcomes, total.reshape(-1)


# --------------------------------------------------------------------------- optimiser
def _optimise(
    bets: Sequence[Bet],
    outcomes: FloatArr,
    pmf: FloatArr,
    costs: Sequence[Sequence[float]],
    tiers: Sequence[str],
    bankroll: float,
    cap: float,
    cfg: BKConfig,
) -> list[int]:
    """Steepest-ascent on integer contracts; a move may add/remove 1, 2 or 5 contracts of one
    bet. Hard limits: total cost <= cap and <= bankroll, steady/swing cost <= their share."""
    n = len(bets)
    limits = {"steady": cap * cfg.steady_share, "swing": cap * (1.0 - cfg.steady_share)}
    cap_total = min(cap, bankroll * 0.999)

    def feasible(x: Sequence[int]) -> bool:
        if any(x[i] >= len(costs[i]) for i in range(n)):
            return False
        tot = sum(costs[i][x[i]] for i in range(n))
        if tot > cap_total + 1e-9:
            return False
        return all(
            sum(costs[i][x[i]] for i in range(n) if tiers[i] == t) <= lim + 1e-9
            for t, lim in limits.items()
        )

    def value(x: Sequence[int]) -> float:
        c = sum(costs[i][x[i]] for i in range(n))
        w = bankroll - c + outcomes @ np.array(x, dtype=float)
        return float(pmf @ np.log(w))

    x = [0] * n
    best = value(x)
    for _ in range(2000):
        move: tuple[int, int] | None = None
        for i in range(n):
            for d in (1, 2, 5, -1, -2, -5):
                ni = x[i] + d
                if ni < 0:
                    continue
                y = list(x)
                y[i] = ni
                if not feasible(y):
                    continue
                v = value(y)
                if v > best + 1e-12:
                    best, move = v, (i, ni)
        if move is None:
            break
        x[move[0]] = move[1]
    return x


def _cost_tables(
    bets: Sequence[Bet], fee: FeeModel, otypes: Sequence[str], limit: float
) -> list[list[float]]:
    out: list[list[float]] = []
    for b, ot in zip(bets, otypes, strict=True):
        m = int(limit / b.price) + 1
        out.append([order_cost(b, k, fee, ot)[0] for k in range(m + 1)])
    return out


# --------------------------------------------------------------------------- result
@dataclass(frozen=True)
class BetResult:
    ticker: str
    side: str
    tier: str
    order_type: str
    price: float
    p_center: float
    p_sd: float
    p_posterior: float
    shrink_weight: float
    contracts: int
    contracts_if_certain: int
    cost: float
    fee: float
    edge_per_contract: float
    expected_profit: float
    p_loss: float


@dataclass(frozen=True)
class SizeResult:
    verdict: str
    bets: list[BetResult]
    bankroll: float
    floor: float
    cap: float
    total_cost: float
    expected_profit: float
    p_net_loss: float
    p_below_floor: float
    worst_case_wealth: float
    expected_log_growth: float
    warning: str
    note: str = ADVICE

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "bets"}
        d["bets"] = [b.__dict__ for b in self.bets]
        return d


def size_bets(
    bets: Sequence[Bet],
    *,
    bankroll: float,
    floor: float,
    cap: float,
    corr: Sequence[tuple[str, str, float]] = (),
    cfg: BKConfig | None = None,
    parlay_cfg_path: str | Path = DEFAULT_PATH,
) -> SizeResult:
    cfg = cfg or load_bk_config(parlay_cfg_path)
    pcfg = load_config(parlay_cfg_path)
    if not pcfg.fee.verified:
        raise ValueError("fee model in configs/parlay.yaml is not verified")
    if not bets:
        raise ValueError("no bets")
    if len(bets) > cfg.max_bets:
        raise ValueError(f"at most {cfg.max_bets} bets")
    if not bankroll > 0 or not cap >= 0:
        raise ValueError("bankroll must be positive and cap non-negative")
    posts = [posterior(b, cfg) for b in bets]
    otypes = [b.order_type or pcfg.order_type for b in bets]
    tiers = [b.tier or ("steady" if b.price >= cfg.steady_threshold else "swing") for b in bets]
    outcomes, pmf = outcome_law(bets, posts, corr, cfg)
    costs = _cost_tables(bets, pcfg.fee, otypes, min(cap, bankroll))
    x = _optimise(bets, outcomes, pmf, costs, tiers, bankroll, cap, cfg)
    rows: list[BetResult] = []
    for i, (b, p) in enumerate(zip(bets, posts, strict=True)):
        sure = replace(b, p_sd=0.0, sources={}, p_center=p.centre, n_settled=None)
        sure_n = _solo_certain(sure, b, p, pcfg.fee, otypes[i], tiers[i], bankroll, cap, cfg)
        c, f = order_cost(b, x[i], pcfg.fee, otypes[i])
        n_ref = max(x[i], 1)
        c1, _ = order_cost(b, n_ref, pcfg.fee, otypes[i])
        rows.append(
            BetResult(
                ticker=b.ticker,
                side=b.side,
                tier=tiers[i],
                order_type=otypes[i],
                price=b.price,
                p_center=p.centre,
                p_sd=p.sd_p,
                p_posterior=p.mean_p,
                shrink_weight=p.shrink_weight,
                contracts=x[i],
                contracts_if_certain=sure_n,
                cost=c,
                fee=f,
                edge_per_contract=p.mean_p - c1 / n_ref,
                expected_profit=x[i] * p.mean_p - c,
                p_loss=1.0 - p.mean_p,
            )
        )
    total = sum(r.cost for r in rows)
    profit = outcomes @ np.array(x, dtype=float) - total
    wealth = bankroll + profit
    growth = float(pmf @ np.log(wealth)) - math.log(bankroll)
    p_below = float(pmf[wealth < floor - 1e-9].sum())
    worst = bankroll - total
    if total == 0.0:
        warning = "no stake, so the soft floor cannot be crossed tonight"
    elif worst < floor - 1e-9:
        warning = (
            f"WARNING: could cross the soft floor: worst case ends ${worst:.2f} vs floor "
            f"${floor:.2f} (P = {p_below:.1%}); the floor warns, it does not block"
        )
    else:
        warning = f"floor safe: even the worst case ends ${worst:.2f}, above the ${floor:.2f} floor"
    verdict = SIZED if total > 0 else NO_POSITIVE_EV
    return SizeResult(
        verdict=verdict,
        bets=rows,
        bankroll=bankroll,
        floor=floor,
        cap=cap,
        total_cost=round(total, 2),
        expected_profit=float(sum(r.expected_profit for r in rows)),
        p_net_loss=float(pmf[profit < -1e-9].sum()) if total > 0 else 0.0,
        p_below_floor=p_below if total > 0 else 0.0,
        worst_case_wealth=worst,
        expected_log_growth=growth,
        warning=warning,
    )


def _solo_certain(
    sure: Bet,
    orig: Bet,
    post: Posterior,
    fee: FeeModel,
    otype: str,
    tier: str,
    bankroll: float,
    cap: float,
    cfg: BKConfig,
) -> int:
    """Contracts for this bet alone if its probability were known exactly (``post.centre``):
    the 'sure edge' reference. Same fee, cap and tier ceiling; no shrink, no shared error."""
    p = post.centre
    cap_i = min(cap, bankroll * 0.999)
    cap_i = min(cap_i, cap * (cfg.steady_share if tier == "steady" else 1 - cfg.steady_share))
    best_n, best_v = 0, math.log(bankroll)
    for n in range(1, int(cap_i / orig.price) + 2):
        c, _ = order_cost(sure, n, fee, otype)
        if c > cap_i + 1e-9:
            break
        v = p * math.log(bankroll - c + n) + (1 - p) * math.log(bankroll - c)
        if v > best_v + 1e-12:
            best_n, best_v = n, v
    return best_n


# --------------------------------------------------------------------------- text
def format_result(r: SizeResult) -> str:
    lines = [
        f"verdict: {r.verdict}",
        f"bankroll ${r.bankroll:.2f}  floor ${r.floor:.2f}  nightly cap ${r.cap:.2f}",
        "",
        f"{'ticker':<26}{'tier':<7}{'ord':<6}{'price':>6}{'p_in':>7}{'+-':>6}{'p_post':>8}"
        f"{'n':>4}{'n_sure':>7}{'cost':>8}{'fee':>6}{'E[prof]':>9}{'P(loss)':>9}",
    ]
    for b in r.bets:
        lines.append(
            f"{b.ticker:<26}{b.tier:<7}{b.order_type:<6}{b.price:>6.2f}{b.p_center:>7.3f}"
            f"{b.p_sd:>6.3f}{b.p_posterior:>8.3f}{b.contracts:>4d}{b.contracts_if_certain:>7d}"
            f"{b.cost:>8.2f}{b.fee:>6.2f}{b.expected_profit:>+9.2f}{b.p_loss:>9.1%}"
        )
    lines += [
        "",
        f"total cost ${r.total_cost:.2f}  expected profit {r.expected_profit:+.2f}  "
        f"P(net loss) {r.p_net_loss:.1%}  P(below floor) {r.p_below_floor:.1%}  "
        f"E[log growth] {r.expected_log_growth:+.4f}",
        r.warning,
        r.note,
    ]
    return "\n".join(lines)
