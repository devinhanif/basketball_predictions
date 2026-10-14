# ruff: noqa: E501
"""Does context change the correlation BETWEEN legs' residuals? (sizing, not a test; nothing here is frozen.)

Input: the 2020-22 walk-forward PIT frames of ``nba.parlay.context_data`` (production-equivalent marginals,
integer support, randomised PIT -> normal score ``z``; plus the "N+ at the model's median line" hit
indicator ``hit`` with the model's own ``p_line``). Output: for each pair class (same-player cross-stat,
teammates, opponents, player x own-team margin, player x game total) and each context (rotation teammate
OUT, team leader OUT, teammates competing for the stat, blowout vs close, favourite vs underdog), the
pairwise correlation inside the context, outside it, the difference, and a game-clustered bootstrap CI.

Two scales, because they answer different questions:

* ``z`` (normal score of the randomised PIT): the copula's own scale. Randomised-PIT noise attenuates it
  toward 0 for count stats, so differences are LOWER bounds in magnitude.
* ``hit`` (indicator of ``y >= line`` at the integer line whose model probability is nearest 0.5): what a
  parlay actually pays on. ``phi = sum(u_i u_j) / sqrt(sum u_i^2 sum u_j^2)`` with ``u = hit - p_line - stat
  mean``; the joint-probability change of a 2-leg parlay is ``cov = mean(u_i u_j)`` itself.

Translation to money (``parlay_joint``): Gaussian-copula orthant probabilities for legs at p = 0.5 (closed
form via arcsin) and the Sheppard map ``rho = sin(pi * phi / 2)`` from indicator phi to latent rho (exact at
p = 0.5, approximate elsewhere; the report says so). No stakes, no prices, no orders: this module reads
files and does arithmetic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
VAC_MIN_THRESHOLD = 24.0  # "about one starter's minutes vacated" (nba.props wowy sizing, VAC24)
COMPETE_TOP = (
    3  # a stat's "competitors" are the team's top-3 by as-of m10 among the players who played
)
MIN_PAIRS = 300
MIN_GAMES = 40
N_BOOT = 1000
BOOT_SEED = 20261014
CONTEXTS: tuple[str, ...] = (
    "vac24",
    "vac48",
    "lead_out",
    "compete",
    "lead_x_compete",
    "blowout",
    "fav",
)


@dataclass(frozen=True)
class PairClass:
    name: str
    kind: str  # same_player | teammate | opponent | own_margin | total
    a: str
    b: str
    ordered: bool = False  # cross-stat teammate/opponent pairs: every ordered (i, j), i != j


def pair_classes() -> list[PairClass]:
    out: list[PairClass] = []
    for a, b in (("pts", "reb"), ("pts", "ast"), ("ast", "reb"), ("pts", "fg3m")):
        out.append(PairClass(f"same_player:{a}-{b}", "same_player", a, b))
    for s in STATS:
        out.append(PairClass(f"teammate:{s}-{s}", "teammate", s, s))
    for a, b in (("pts", "ast"), ("pts", "reb"), ("ast", "reb")):
        out.append(PairClass(f"teammate_cross:{a}_i-{b}_j", "teammate", a, b, ordered=True))
    for s in STATS:
        out.append(PairClass(f"opponent:{s}-{s}", "opponent", s, s))
    for s in STATS:
        out.append(PairClass(f"own_margin:{s}", "own_margin", s, "margin"))
    for s in STATS:
        out.append(PairClass(f"total:{s}", "total", s, "total"))
    return out


# --------------------------------------------------------------------------- panel


@dataclass
class Panel:
    """One row per scored player-game, game-sorted; all arrays aligned."""

    n: int
    game_idx: np.ndarray  # dense game index per row
    n_games: int
    team: np.ndarray
    z: dict[str, np.ndarray]
    u: dict[str, np.ndarray]
    p: dict[str, np.ndarray]
    rank: dict[str, np.ndarray]  # rank of m10_stat within the team-game (1 = highest)
    tv: np.ndarray  # team VAC24 label: 1 / 0 / -1 (no report)
    tv48: np.ndarray  # dose check: 1 if vac_min >= 48, 0 if < 24, -1 otherwise
    lead: dict[str, np.ndarray]  # leader-out label per stat: 1 / 0 / -1
    blow: np.ndarray  # 1 top tercile of |Elo margin|, 0 bottom tercile, -1 middle
    fav: np.ndarray  # 1 if the player's team is the Elo favourite, 0 otherwise
    margin_z: np.ndarray  # standardised own-team margin residual
    total_z: np.ndarray  # standardised game-total residual
    cover_u: np.ndarray  # centred indicator: own team beat its Elo expected margin
    over_u: np.ndarray  # centred indicator: game total above the as-of expectation
    cuts: dict[str, float] = field(default_factory=dict)
    starts: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    team_game: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))


def load_panel(out_dir: Path) -> Panel:
    ctx = pl.read_parquet(out_dir / "context.parquet")
    if int(ctx["season"].max()) > 2022:  # type: ignore[arg-type]
        raise ValueError("seasons after 2022 are not allowed in the sizing panel")
    wide = ctx
    for s in STATS:
        d = pl.read_parquet(out_dir / f"pit_{s}.parquet").select(
            "game_id",
            "player_id",
            pl.col("z").alias(f"z_{s}"),
            pl.col("hit").cast(pl.Float64).alias(f"hit_{s}"),
            pl.col("p_line").alias(f"p_{s}"),
        )
        wide = wide.join(d, on=["game_id", "player_id"], how="inner")
    wide = wide.filter(pl.col("season").is_in([2020, 2021, 2022])).sort(
        ["game_id", "team_id", "player_id"]
    )
    gid = wide["game_id"].to_numpy()
    _, game_idx = np.unique(gid, return_inverse=True)
    team = wide["team_id"].to_numpy()
    tg_key = np.array([f"{g}|{t}" for g, t in zip(gid, team, strict=True)])
    _, team_game = np.unique(tg_key, return_inverse=True)
    z = {s: wide[f"z_{s}"].to_numpy().astype(float) for s in STATS}
    p = {s: wide[f"p_{s}"].to_numpy().astype(float) for s in STATS}
    hit = {s: wide[f"hit_{s}"].to_numpy().astype(float) for s in STATS}
    # centre z and u by their stat-level means (the pairs measure dependence, not marginal bias)
    for s in STATS:
        z[s] = z[s] - float(np.mean(z[s]))
    u = {s: hit[s] - p[s] - float(np.mean(hit[s] - p[s])) for s in STATS}
    # rank of m10_stat inside the team-game (1 = highest)
    rank: dict[str, np.ndarray] = {}
    for s in STATS:
        m = wide[f"m10_{s}"].to_numpy().astype(float)
        r = np.zeros(len(m), dtype=np.int64)
        order = np.lexsort((-m, team_game))
        tgs = team_game[order]
        first = np.r_[True, tgs[1:] != tgs[:-1]]
        grp_start = np.maximum.accumulate(np.where(first, np.arange(len(order)), 0))
        r[order] = np.arange(len(order)) - grp_start + 1
        rank[s] = r
    has_rep = wide["has_report"].to_numpy() == 1
    vac = wide["vac_min"].to_numpy().astype(float)
    tv = np.where(has_rep, np.where(vac >= VAC_MIN_THRESHOLD, 1, 0), -1).astype(np.int8)
    tv48 = np.where(
        has_rep,
        np.where(vac >= 2 * VAC_MIN_THRESHOLD, 1, np.where(vac < VAC_MIN_THRESHOLD, 0, -1)),
        -1,
    ).astype(np.int8)
    tv48 = np.where(
        has_rep,
        np.where(vac >= 2 * VAC_MIN_THRESHOLD, 1, np.where(vac < VAC_MIN_THRESHOLD, 0, -1)),
        -1,
    ).astype(np.int8)
    lead = {}
    for s in STATS:
        v = wide[f"lead_out_{s}"].to_numpy()
        lead[s] = np.where(np.isnan(v.astype(float)), -1, v).astype(np.int8)
    am = wide["abs_margin"].to_numpy().astype(float)
    q_lo, q_hi = (float(x) for x in np.nanquantile(am, [1 / 3, 2 / 3]))
    blow = np.where(am >= q_hi, 1, np.where(am <= q_lo, 0, -1)).astype(np.int8)
    fav = (wide["exp_margin"].to_numpy() > 0).astype(np.int8)
    home = wide["home_team"].to_numpy()
    hp = wide["home_pts"].to_numpy().astype(float)
    ap = wide["away_pts"].to_numpy().astype(float)
    own = np.where(team == home, hp - ap, ap - hp)
    mres = own - wide["exp_margin"].to_numpy().astype(float)
    tot = hp + ap
    tres = tot - (
        wide["r20_pts_for"].to_numpy().astype(float)
        + wide["opp_r20_pts_for"].to_numpy().astype(float)
    )
    tres = tres - np.nanmean(tres)
    margin_z = (mres - np.nanmean(mres)) / (np.nanstd(mres) or 1.0)
    total_z = tres / (np.nanstd(tres) or 1.0)
    cover = (mres > 0).astype(float)
    over = (tres > 0).astype(float)
    cover_u = cover - float(np.nanmean(cover))
    over_u = over - float(np.nanmean(over))
    starts = np.r_[0, np.flatnonzero(np.diff(game_idx)) + 1, len(game_idx)]
    return Panel(
        n=len(gid),
        game_idx=game_idx,
        n_games=int(game_idx.max()) + 1,
        team=team,
        z=z,
        u=u,
        p=p,
        rank=rank,
        tv=tv,
        tv48=tv48,
        lead=lead,
        blow=blow,
        fav=fav,
        margin_z=margin_z,
        total_z=total_z,
        cover_u=cover_u,
        over_u=over_u,
        cuts={"abs_margin_lo": q_lo, "abs_margin_hi": q_hi},
        starts=starts,
        team_game=team_game,
    )


def shuffle_labels(panel: Panel, seed: int) -> Panel:
    """Placebo: permute the TEAM-GAME level context labels (vac24, vac48, leader-out, favourite) among all
    team-games, the GAME level blowout label among games, and re-draw every player's within-team-game rank at
    random (the `compete` context). The pair table is then rebuilt from the same residuals, so the placebo
    distribution of context differences is the pure sampling noise of the whole procedure."""
    rng = np.random.default_rng(seed)
    out = Panel(**{k: getattr(panel, k) for k in panel.__dataclass_fields__})
    tg = panel.team_game
    n_tg = int(tg.max()) + 1
    first_idx = np.zeros(n_tg, dtype=np.int64)
    first_idx[tg[::-1]] = np.arange(panel.n)[::-1]  # first row index of each team-game
    perm = rng.permutation(n_tg)
    out.tv = panel.tv[first_idx][perm][tg]
    out.tv48 = panel.tv48[first_idx][perm][tg]
    out.lead = {s: panel.lead[s][first_idx][perm][tg] for s in STATS}
    out.fav = panel.fav[first_idx][rng.permutation(n_tg)][tg]
    out.rank = {}
    for s in STATS:
        order = np.lexsort((rng.random(panel.n), tg))
        tgs = tg[order]
        first = np.r_[True, tgs[1:] != tgs[:-1]]
        grp_start = np.maximum.accumulate(np.where(first, np.arange(len(order)), 0))
        r = np.zeros(panel.n, dtype=np.int64)
        r[order] = np.arange(len(order)) - grp_start + 1
        out.rank[s] = r
    g = panel.game_idx
    first_g = np.zeros(panel.n_games, dtype=np.int64)
    first_g[g[::-1]] = np.arange(panel.n)[::-1]
    pg = rng.permutation(panel.n_games)
    out.blow = panel.blow[first_g][pg][g]
    return out


# --------------------------------------------------------------------------- pair tables


@dataclass
class PairTable:
    g: np.ndarray
    x: np.ndarray
    y: np.ndarray
    ux: np.ndarray
    uy: np.ndarray
    pp: np.ndarray
    labels: dict[str, np.ndarray]


def _cat(parts: list[np.ndarray], dtype: Any = float) -> np.ndarray:
    return np.concatenate(parts) if parts else np.zeros(0, dtype=dtype)


def _lab_vac(ti: np.ndarray, tj: np.ndarray, kind: str) -> np.ndarray:
    if kind == "opponent":
        return np.where((ti == 1) | (tj == 1), 1, np.where((ti == 0) & (tj == 0), 0, -1)).astype(
            np.int8
        )
    return np.where(ti == tj, ti, -1).astype(np.int8)


def _lab_lead(li: np.ndarray, lj: np.ndarray) -> np.ndarray:
    return np.where((li == 1) | (lj == 1), 1, np.where((li == 0) & (lj == 0), 0, -1)).astype(
        np.int8
    )


def pair_table(panel: Panel, pc: PairClass) -> PairTable:
    """All pairs of a class over all games (each unordered pair once; cross-stat teammate pairs in both orders)."""
    G: list[np.ndarray] = []
    X: list[np.ndarray] = []
    Y: list[np.ndarray] = []
    UX: list[np.ndarray] = []
    UY: list[np.ndarray] = []
    PP: list[np.ndarray] = []
    L: dict[str, list[np.ndarray]] = {c: [] for c in CONTEXTS}
    a, b = pc.a, pc.b
    if pc.kind in ("same_player", "own_margin", "total"):
        idx = np.arange(panel.n)
        if pc.kind == "same_player":
            x, y = panel.z[a], panel.z[b]
            ux, uy = panel.u[a], panel.u[b]
            pp = panel.p[a] * panel.p[b]
            lead = _lab_lead(panel.lead[a], panel.lead[b])
            comp = np.full(panel.n, -1, dtype=np.int8)
        else:
            x = panel.z[a]
            y = panel.margin_z if pc.kind == "own_margin" else panel.total_z
            ux = panel.u[a]
            uy = panel.cover_u if pc.kind == "own_margin" else panel.over_u
            pp = panel.p[a] * 0.5
            lead = np.where(panel.lead[a] >= 0, panel.lead[a], -1).astype(np.int8)
            comp = np.full(panel.n, -1, dtype=np.int8)
        ok = np.isfinite(x) & np.isfinite(y)
        G.append(panel.game_idx[idx][ok])
        X.append(x[ok])
        Y.append(y[ok])
        UX.append(ux[ok])
        UY.append(uy[ok])
        PP.append(pp[ok])
        L["vac24"].append(panel.tv[ok])
        L["vac48"].append(panel.tv48[ok])
        L["lead_out"].append(lead[ok])
        L["compete"].append(comp[ok])
        L["blowout"].append(panel.blow[ok])
        L["fav"].append(
            panel.fav[ok] if pc.kind != "same_player" else np.full(int(ok.sum()), -1, dtype=np.int8)
        )
    else:
        for gi in range(panel.n_games):
            lo, hi = int(panel.starts[gi]), int(panel.starts[gi + 1])
            if hi - lo < 2:
                continue
            mi, mj = np.meshgrid(np.arange(lo, hi), np.arange(lo, hi), indexing="ij")
            ii, jj = mi.ravel(), mj.ravel()
            same_team = panel.team[ii] == panel.team[jj]
            m = same_team & (ii != jj) if pc.kind == "teammate" else ~same_team
            if not pc.ordered:
                m &= ii < jj
            ii, jj = ii[m], jj[m]
            if len(ii) == 0:
                continue
            G.append(np.full(len(ii), gi))
            X.append(panel.z[a][ii])
            Y.append(panel.z[b][jj])
            UX.append(panel.u[a][ii])
            UY.append(panel.u[b][jj])
            PP.append(panel.p[a][ii] * panel.p[b][jj])
            L["vac24"].append(_lab_vac(panel.tv[ii], panel.tv[jj], pc.kind))
            L["vac48"].append(_lab_vac(panel.tv48[ii], panel.tv48[jj], pc.kind))
            L["lead_out"].append(_lab_lead(panel.lead[a][ii], panel.lead[b][jj]))
            if pc.kind == "teammate":
                L["compete"].append(
                    np.where(
                        (panel.rank[a][ii] <= COMPETE_TOP) & (panel.rank[b][jj] <= COMPETE_TOP),
                        1,
                        0,
                    ).astype(np.int8)
                )
            else:
                L["compete"].append(np.full(len(ii), -1, dtype=np.int8))
            L["blowout"].append(panel.blow[ii])
            L["fav"].append(np.full(len(ii), -1, dtype=np.int8))
    labels = {c: _cat(v, np.int8).astype(np.int8) for c, v in L.items()}
    ld, cp = labels["lead_out"], labels["compete"]
    labels["lead_x_compete"] = np.where(
        (ld == 1) & (cp == 1), 1, np.where((ld == 0) & (cp == 0), 0, -1)
    ).astype(np.int8)
    return PairTable(
        g=_cat(G, np.int64).astype(np.int64),
        x=_cat(X),
        y=_cat(Y),
        ux=_cat(UX),
        uy=_cat(UY),
        pp=_cat(PP),
        labels=labels,
    )


# --------------------------------------------------------------------------- estimation


def _sums(tab: PairTable, mask: np.ndarray, n_games: int) -> np.ndarray:
    """(12, G) per-game sums over the masked pairs: n, Sx, Sy, Sxy, Sxx, Syy, Ux, Uy, Uxy, Uxx, Uyy, Pxy."""
    g = tab.g[mask]
    x, y, ux, uy = tab.x[mask], tab.y[mask], tab.ux[mask], tab.uy[mask]
    cols = (
        np.ones(int(mask.sum())),
        x,
        y,
        x * y,
        x**2,
        y**2,
        ux,
        uy,
        ux * uy,
        ux**2,
        uy**2,
        tab.pp[mask],
    )
    return np.vstack([np.bincount(g, weights=c, minlength=n_games) for c in cols])


def _stats(tot: np.ndarray) -> dict[str, np.ndarray]:
    """tot: (12, B) totals -> correlations.

    ``corr_z`` / ``phi`` / ``cov`` are CENTRED inside the stratum (each stratum's own pair means removed), so a
    context-specific marginal bias (everybody under-forecast in that context) is not counted as dependence:
    a bias is fixed by recalibrating marginals, not by a different correlation. ``*_raw`` keep the global
    centring only."""
    n, sx, sy, sxy, sxx, syy, ux, uy, uxy, uxx, uyy, pxy = tot
    with np.errstate(invalid="ignore", divide="ignore"):
        cxy = sxy / n - (sx / n) * (sy / n)
        vx = sxx / n - (sx / n) ** 2
        vy = syy / n - (sy / n) ** 2
        dxy = uxy / n - (ux / n) * (uy / n)
        wx = uxx / n - (ux / n) ** 2
        wy = uyy / n - (uy / n) ** 2
        return {
            "n": n,
            "corr_z": cxy / np.sqrt(vx * vy),
            "phi": dxy / np.sqrt(wx * wy),
            "cov": dxy,
            "corr_z_raw": sxy / np.sqrt(sxx * syy),
            "phi_raw": uxy / np.sqrt(uxx * uyy),
            "pp": pxy / n,
        }


def _ci(v: np.ndarray) -> tuple[float, float]:
    return float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))


def cell_estimates(
    tab: PairTable, ctx: str, n_games: int, weights: np.ndarray
) -> dict[str, Any] | None:
    lab = tab.labels[ctx]
    m1, m0 = lab == 1, lab == 0
    if m1.sum() < MIN_PAIRS or m0.sum() < MIN_PAIRS:
        return None
    s1, s0 = _sums(tab, m1, n_games), _sums(tab, m0, n_games)
    g1 = int((s1[0] > 0).sum())
    g0 = int((s0[0] > 0).sum())
    if g1 < MIN_GAMES or g0 < MIN_GAMES:
        return None
    pt1, pt0 = _stats(s1.sum(axis=1)), _stats(s0.sum(axis=1))
    b1, b0 = _stats((weights @ s1.T).T), _stats((weights @ s0.T).T)
    out: dict[str, Any] = {
        "ctx": ctx,
        "n_pairs_ctx": int(m1.sum()),
        "n_pairs_ref": int(m0.sum()),
        "n_games_ctx": g1,
        "n_games_ref": g0,
    }
    for k in ("corr_z", "phi", "cov", "corr_z_raw", "phi_raw"):
        if not np.isfinite(pt1[k]) or not np.isfinite(pt0[k]):
            continue
        d_pt = float(pt1[k]) - float(pt0[k])
        d_b = b1[k] - b0[k]
        lo, hi = _ci(d_b)
        out[k] = {
            "ctx": float(pt1[k]),
            "ref": float(pt0[k]),
            "diff": d_pt,
            "ci": [lo, hi],
            "excludes_0": bool(lo > 0 or hi < 0),
        }
    out["mean_pp"] = float(pt1["pp"])
    return out


def run_classes(
    panel: Panel,
    n_boot: int = N_BOOT,
    seed: int = BOOT_SEED,
    classes: list[PairClass] | None = None,
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(
        panel.n_games, np.full(panel.n_games, 1.0 / panel.n_games), size=n_boot
    ).astype(float)
    rows: list[dict[str, Any]] = []
    for pc in classes or pair_classes():
        tab = pair_table(panel, pc)
        allm = np.ones(len(tab.g), dtype=bool)
        s_all = _sums(tab, allm, panel.n_games)
        pt = _stats(s_all.sum(axis=1))
        bt = _stats((weights @ s_all.T).T)
        base = {
            "class": pc.name,
            "kind": pc.kind,
            "n_pairs": int(len(tab.g)),
            "n_games": int((s_all[0] > 0).sum()),
            "all": {
                k: {"est": float(pt[k]), "ci": list(_ci(bt[k]))}
                for k in ("corr_z", "phi", "cov", "corr_z_raw", "phi_raw")
                if np.isfinite(pt[k])
            },
            "mean_pp": float(pt["pp"]),
        }
        cells = [
            c for c in (cell_estimates(tab, ctx, panel.n_games, weights) for ctx in CONTEXTS) if c
        ]
        rows.append({**base, "cells": cells})
    return rows


# --------------------------------------------------------------------------- money translation


def sheppard_rho(phi: float) -> float:
    """Latent normal correlation from the phi of two median-split indicators (exact at p = 0.5)."""
    return math.sin(math.pi * phi / 2.0)


def parlay_joint(rhos: list[float]) -> float:
    """P(all legs hit), each leg p = 0.5, Gaussian copula. 2 legs: rho; 3 legs: (r12, r13, r23)."""
    if len(rhos) == 1:
        return 0.25 + math.asin(rhos[0]) / (2.0 * math.pi)
    if len(rhos) == 3:
        return 0.125 + sum(math.asin(r) for r in rhos) / (4.0 * math.pi)
    raise ValueError("2-leg takes (rho,), 3-leg takes (r12, r13, r23)")


def fair_odds(p: float) -> float:
    """Decimal odds that make a bet at probability p fair."""
    return 1.0 / p


def kalshi_cost_share(price: float, fee_per_contract: float) -> float:
    """Fee as a share of the contract price (fee supplied from the verified config by the caller)."""
    return fee_per_contract / price
