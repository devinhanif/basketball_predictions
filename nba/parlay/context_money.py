# ruff: noqa: E501
"""From a context shift in cross-leg correlation to money (sizing arithmetic only; no prices, stakes or orders).

Takes the cell estimates of ``nba.parlay.context_corr`` and answers: for a typical 2- or 3-leg same-game parlay
(every leg an over at its model median line, p = 0.5), by how much does the joint probability move between a
context and its complement, and how does that compare with what a parlay costs (vig, spread, fee)?

* ``phi -> rho``: the Sheppard map ``rho = sin(pi * phi / 2)`` (exact for p = 0.5 median splits).
* joint probability: Gaussian-copula orthant probability, closed form at p = 0.5 (2 legs ``1/4 + asin(r)/(2 pi)``,
  3 legs ``1/8 + (asin r12 + asin r13 + asin r23)/(4 pi)``).
* ``required_lift(cost)``: how far the true joint probability must exceed the probability a price implies to
  offset a loss of ``cost`` per unit at the model's own fair probability. Costs are SUPPLIED by the caller with
  their provenance (ledger rows, the verified fee config); nothing is hardcoded here.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from nba.parlay.context_corr import BOOT_SEED, N_BOOT, Panel, _ci


def sheppard_rho(phi: float) -> float:
    """Latent normal correlation from the phi of two median-split indicators (exact at p = 0.5)."""
    return math.sin(math.pi * phi / 2.0)


def parlay_joint(rhos: list[float]) -> float:
    """P(all legs hit), each leg p = 0.5, Gaussian copula. 2 legs: ``[rho]``; 3 legs: ``[r12, r13, r23]``."""
    if len(rhos) == 1:
        return 0.25 + math.asin(rhos[0]) / (2.0 * math.pi)
    if len(rhos) == 3:
        return 0.125 + sum(math.asin(r) for r in rhos) / (4.0 * math.pi)
    raise ValueError("2-leg takes (rho,), 3-leg takes (r12, r13, r23)")


def fair_odds(p: float) -> float:
    """Decimal odds that make a bet at probability ``p`` fair."""
    return 1.0 / p


def kl_bernoulli(p_true: float, p_model: float) -> float:
    """Expected log loss saved per parlay by pricing it at ``p_true`` instead of ``p_model`` (nats; KL divergence)."""
    return p_true * math.log(p_true / p_model) + (1 - p_true) * math.log(
        (1 - p_true) / (1 - p_model)
    )


def required_lift(cost: float) -> float:
    """Relative rise of the true joint probability above the price-implied one that offsets a loss of ``cost``
    per unit staked: ``1 / (1 - cost) - 1`` (cost 0.129 -> 0.148)."""
    if not 0.0 <= cost < 1.0:
        raise ValueError("cost must be in [0, 1)")
    return 1.0 / (1.0 - cost) - 1.0


def fee_share(price: float, fee_per_contract: float) -> float:
    """Fee as a share of the contract price; the caller takes ``fee_per_contract`` from the verified fee config."""
    return fee_per_contract / price


def narrow_cell(
    panel: Panel,
    m1: np.ndarray,
    m0: np.ndarray,
    a: str,
    b: str,
    n_boot: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> dict[str, Any]:
    """Same-player ``phi`` of stats (a, b) inside mask ``m1`` versus mask ``m0`` (stratum-centred), game-clustered
    bootstrap CI of the difference and the implied 2-leg joint-probability change. For post-hoc ceiling checks: a
    cell picked for its size is shrunk in practice (winner's curse), so these bound and do not estimate."""
    rng = np.random.default_rng(seed)
    w = rng.multinomial(
        panel.n_games, np.full(panel.n_games, 1.0 / panel.n_games), size=n_boot
    ).astype(float)

    def phi(mask: np.ndarray) -> tuple[float, np.ndarray, int, int]:
        g = panel.game_idx[mask]
        ua, ub = panel.u[a][mask], panel.u[b][mask]
        cols = [np.ones(int(mask.sum())), ua, ub, ua * ub, ua**2, ub**2]
        s = np.vstack([np.bincount(g, weights=c, minlength=panel.n_games) for c in cols])

        def f(t: np.ndarray) -> Any:
            n, sa, sb, sab, saa, sbb = t
            cov = sab / n - (sa / n) * (sb / n)
            return cov / np.sqrt((saa / n - (sa / n) ** 2) * (sbb / n - (sb / n) ** 2))

        return (
            float(f(s.sum(axis=1))),
            np.asarray(f((w @ s.T).T)),
            int(mask.sum()),
            int((s[0] > 0).sum()),
        )

    p1, b1, n1, g1 = phi(m1)
    p0, b0, n0, g0 = phi(m0)
    lo, hi = _ci(b1 - b0)
    p_ref = parlay_joint([sheppard_rho(p0)])
    p_ctx = parlay_joint([sheppard_rho(p1)])
    return {
        "stats": f"{a}-{b}",
        "n_ctx": n1,
        "n_ref": n0,
        "games_ctx": g1,
        "games_ref": g0,
        "phi_ctx": p1,
        "phi_ref": p0,
        "diff": p1 - p0,
        "ci": [lo, hi],
        "joint_rel_change": p_ctx / p_ref - 1.0,
    }


def _cell(rows: list[dict[str, Any]], cls: str, ctx: str) -> dict[str, Any]:
    for r in rows:
        if r["class"] == cls:
            for c in r["cells"]:
                if c["ctx"] == ctx:
                    return {**c, "_all": r["all"]}
    raise KeyError((cls, ctx))


SCENARIOS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "2-leg same player: points + assists overs, team leader OUT",
        [("same_player:pts-ast", "lead_out")],
    ),
    (
        "2-leg same player: points + rebounds overs, team leader OUT",
        [("same_player:pts-reb", "lead_out")],
    ),
    (
        "2-leg teammates: A's points + B's assists overs, both top-3, leader OUT",
        [("teammate_cross:pts_i-ast_j", "lead_x_compete")],
    ),
    (
        "2-leg teammates: A's assists + B's assists overs, both top-3 assisters",
        [("teammate:ast-ast", "compete")],
    ),
    (
        "2-leg rebounds over + own team beats its Elo margin, favourite vs underdog",
        [("own_margin:reb", "fav")],
    ),
    (
        "3-leg same player: points + rebounds + assists overs, team leader OUT",
        [
            ("same_player:pts-reb", "lead_out"),
            ("same_player:pts-ast", "lead_out"),
            ("same_player:ast-reb", "lead_out"),
        ],
    ),
    (
        "3-leg: A's points + A's assists + B's assists (teammates, top-3, leader OUT)",
        [
            ("same_player:pts-ast", "lead_out"),
            ("teammate_cross:pts_i-ast_j", "lead_x_compete"),
            ("teammate:ast-ast", "compete"),
        ],
    ),
]


def money_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Typical parlays priced under the pooled correlation (what the production copula uses), the reference group
    and the context group. ``rel_ctx_vs_pool`` is the error the pooled copula makes inside the context;
    ``rel_ctx_vs_ref`` is the full swing between the two groups; ``rel_ci_hi_vs_ref`` pushes every pair to the
    far end of its game-clustered 95% CI at once (a loose upper bound on the swing)."""
    out: list[dict[str, Any]] = []
    for name, parts in SCENARIOS:
        pool: list[float] = []
        ref: list[float] = []
        ctx: list[float] = []
        hi: list[float] = []
        for cls, cx in parts:
            c = _cell(rows, cls, cx)
            ph = c["phi"]
            pool.append(sheppard_rho(c["_all"]["phi"]["est"]))
            ref.append(sheppard_rho(ph["ref"]))
            ctx.append(sheppard_rho(ph["ctx"]))
            hi.append(sheppard_rho(ph["ref"] + max(ph["ci"], key=abs)))
        n_legs = 2 if len(parts) == 1 else 3
        p_pool, p_ref, p_ctx, p_hi = (parlay_joint(x) for x in (pool, ref, ctx, hi))
        p_ind = 0.5**n_legs
        out.append(
            {
                "scenario": name,
                "legs": n_legs,
                "p_independent": p_ind,
                "p_pooled": p_pool,
                "p_ref": p_ref,
                "p_ctx": p_ctx,
                "fair_odds_pooled": fair_odds(p_pool),
                "fair_odds_ctx": fair_odds(p_ctx),
                "rel_pooled_vs_independent": p_pool / p_ind - 1.0,
                "rel_ctx_vs_pool": p_ctx / p_pool - 1.0,
                "rel_ctx_vs_ref": p_ctx / p_ref - 1.0,
                "rel_ci_hi_vs_ref": p_hi / p_ref - 1.0,
                "log_loss_gain_ctx_segment": kl_bernoulli(p_ctx, p_pool),
            }
        )
    return out
