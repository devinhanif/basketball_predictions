"""PARLAY_JOINT_CAL minimum-data gate and the FIXED ticket construction.

Rule: docs/prereg/PARLAY_JOINT_CAL.md (DRAFT until frozen).

    <python> research/eval/parlay_joint_cal_gate.py \\
        --data-root <checkout holding data/ and reports/> \\
        --out reports/prereg_parlay_joint_cal/gate.json

Counts and model-only quantities only. No outcome column (``over``, ``y``) is read, no hit
rate, log loss or calibration is computed, and no CAL fit is made (a CAL fit needs labels).
The model P that sizes the gate is a sizing proxy (see PROXY_SLOPE), not the CAL map. Season
<= 2024 is asserted before any read. Self-contained on purpose: the scoring harness (written
after the freeze) imports the construction functions below unchanged.

Legs: one DraftKings (primary) or Pinnacle (consistency) T-60 main-line row per (game,
player, stat), played games only (DNP voided upstream in the MODEL_CLV pairs frame), side =
the more probable side of the leg's probability. A leg is eligible only if its (stat, month)
block has at least MIN_FIT earlier rows of the same book (LINE_CALIBRATION would otherwise
serve raw P, not CAL).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

MAX_SEASON = 2024
SEASONS = (2023, 2024)
BOOKS = ("draftkings", "pinnacle")
STATS = ("pts", "reb", "ast", "fg3m")
ROOT_SEED = 2026
PER_DATE = 20  # random tickets drawn per (season, type, size, date)
SIZES = (2, 3)
TOP_N = 3  # tickets shown per (date, size): the panel
# exact: a top-3 cross-game ticket never uses a leg ranked above 3 within its game
TOP_PER_GAME_CROSS = 3
TOP_UNITS_SAME = 8  # same-game candidates come from a game's 8 highest-q units
MIN_FIT = 2000  # LINE_CALIBRATION identity rule: fewer fit rows -> raw P
PROXY_SLOPE = 0.5  # sizing proxy for the CAL leg: q = 0.5 + 0.5 (q_raw - 0.5); NOT the CAL map
N_SIMS = 300
SIM_SEED = [ROOT_SEED, 99]
PAIRS_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "tipoff_utc", "line",
    "price_over", "price_under", "p_model", "season", "game_date", "_dnp",
]  # fmt: skip
ROWS_COLS = ["game_id", "player_id", "stat", "team_id"]  # no y
CODE_FILES = ("research/eval/parlay_joint_cal_gate.py",)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------- legs


def load_legs(root: Path, season: int, book: str) -> tuple[pl.DataFrame, dict[str, Any]]:
    """T-60 main-line legs of one (season, book), no outcome column. Asserts the lead time."""
    assert season <= MAX_SEASON, "season 2025 is the frozen holdout"
    pairs = root / f"data/ops/model_clv_pairs/pairs_{season}.parquet"
    rows = root / f"reports/odds_history/model_rows_{season}.parquet"
    d = pl.read_parquet(pairs, columns=PAIRS_COLS)
    assert d["season"].n_unique() == 1 and int(d["season"][0]) == season
    n_all = d.height
    d = d.filter((pl.col("snapshot_kind") == "t60") & (pl.col("book") == book))
    n_t60 = d.height
    assert d.group_by(["game_id", "player_id", "stat"]).len()["len"].max() == 1
    lead = (d["tipoff_utc"] - d["snapshot_ts"]).dt.total_seconds() / 60.0
    lead_min = float(lead.min())  # type: ignore[arg-type]
    lead_med = float(lead.median())  # type: ignore[arg-type]
    assert lead_min >= 60.0, "a T-60 snapshot closer than 60 minutes to the real tip"
    team = pl.read_parquet(rows, columns=ROWS_COLS).unique(["game_id", "player_id", "stat"])
    team = team.with_columns(pl.col("player_id").cast(pl.Int64))
    d = d.join(team, on=["game_id", "player_id", "stat"], how="left")
    n_noteam = int(d["team_id"].is_null().sum())
    n_dnp = int(d["_dnp"].sum())
    d = d.filter(~pl.col("_dnp") & pl.col("team_id").is_not_null())
    both = d.filter(pl.col("price_over").is_not_null() & pl.col("price_under").is_not_null())
    p = both["p_model"].to_numpy().astype(float)
    qraw = np.maximum(p, 1 - p)
    both = both.with_columns(
        pl.Series("q_raw", qraw),
        pl.Series("q_proxy", 0.5 + PROXY_SLOPE * (qraw - 0.5)),
        pl.Series("side_over", p >= 0.5),
    ).sort(["game_date", "game_id", "player_id", "stat"])
    diag = {
        "rows_in_pairs_file": n_all,
        "t60_rows_book": n_t60,
        "legs_after_dnp_team_price": both.height,
        "no_team_id": n_noteam,
        "dnp_rows_in_frame": n_dnp,
        "lead_min_minutes": lead_min,
        "lead_median_minutes": lead_med,
        "dates": int(both["game_date"].n_unique()),
        "games": int(both["game_id"].n_unique()),
        "pairs_sha256": file_sha(pairs),
        "model_rows_sha256": file_sha(rows),
    }
    return both, diag


def _blocks(legs_all: pl.DataFrame, season: int) -> list[dict[str, Any]]:
    """Per (stat, month) block of ``season``: earlier same-book rows (all loaded seasons) and
    the block's leg count. Counts only."""
    d = legs_all.with_columns(pl.col("game_date").dt.truncate("1mo").alias("mstart"))
    out: list[dict[str, Any]] = []
    for stat in STATS:
        c = d.filter(pl.col("stat") == stat)
        for b0 in sorted(c.filter(pl.col("season") == season)["mstart"].unique().to_list()):
            n_fit = c.filter(pl.col("game_date") < b0).height
            n_sc = c.filter((pl.col("season") == season) & (pl.col("mstart") == b0)).height
            out.append(
                {
                    "stat": stat,
                    "mstart": b0,
                    "n_fit": n_fit,
                    "n_score": n_sc,
                    "identity": n_fit < MIN_FIT,
                }
            )
    return out


def identity_share(legs_all: pl.DataFrame, season: int) -> dict[str, Any]:
    """Share of the season's legs whose CAL block has fewer than MIN_FIT earlier fit rows."""
    blocks = _blocks(legs_all, season)
    tot = sum(b["n_score"] for b in blocks)
    ident = sum(b["n_score"] for b in blocks if b["identity"])
    rows = [{**b, "mstart": str(b["mstart"])[:7]} for b in blocks]
    return {
        "legs": tot,
        "identity_legs": ident,
        "identity_share": ident / max(tot, 1),
        "blocks": rows,
    }


def cal_served(legs_all: pl.DataFrame, legs: pl.DataFrame, season: int) -> pl.DataFrame:
    """Keep only the legs of ``season`` whose (stat, month) block is fit with at least MIN_FIT
    earlier rows of the same book, so LINE_CALIBRATION serves CAL and not raw P. Legs in
    identity blocks are NOT eligible for any ticket under this rule. Counts only."""
    ok = pl.DataFrame(
        [
            {"stat": b["stat"], "mstart": b["mstart"], "cal_served": not b["identity"]}
            for b in _blocks(legs_all, season)
        ]
    )
    lg = legs.with_columns(pl.col("game_date").dt.truncate("1mo").alias("mstart"))
    lg = lg.join(ok, on=["stat", "mstart"], how="left")
    lg = lg.filter(pl.col("cal_served").fill_null(False))
    order = ["game_date", "game_id", "player_id", "stat"]
    return lg.drop(["mstart", "cal_served"]).sort(order)


# ------------------------------------------------------------------ construction (THE RULE)


def _by_date(date: np.ndarray) -> list[tuple[Any, np.ndarray]]:
    return [(d, np.flatnonzero(date == d)) for d in np.unique(date)]


def cross_tickets(
    date: np.ndarray, game: np.ndarray, m: int, per_date: int, seed: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Cross-game random tickets. Per date: up to ``per_date`` draws; a draw picks ``m``
    distinct games uniformly without replacement among that date's games with legs, then one
    leg uniformly within each picked game. A leg set that already appeared that date is dropped
    (no refill). Legs must be sorted (date, game, player, stat). Returns the leg indices
    (T, m), each row sorted, and the ticket date."""
    rng = np.random.default_rng(seed)
    out: list[tuple[int, ...]] = []
    out_d: list[Any] = []
    for d, idx in _by_date(date):
        games, inv = np.unique(game[idx], return_inverse=True)
        if len(games) < m:
            continue
        by_game = [idx[inv == g] for g in range(len(games))]
        seen: set[tuple[int, ...]] = set()
        for _ in range(per_date):
            gsel = rng.choice(len(games), size=m, replace=False)
            pick = tuple(sorted(int(by_game[g][rng.integers(len(by_game[g]))]) for g in gsel))
            if pick in seen:
                continue
            seen.add(pick)
            out.append(pick)
            out_d.append(d)
    return np.array(out, dtype=np.int64).reshape(-1, m), np.array(out_d, dtype="datetime64[D]")


def same_tickets(
    date: np.ndarray, game: np.ndarray, m: int, per_date: int, seed: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Same-game random tickets. Per date: up to ``per_date`` draws; a draw picks ONE game
    uniformly among that date's games with at least ``m`` legs, then ``m`` distinct legs
    (units) uniformly without replacement within it. Duplicate leg sets are dropped."""
    rng = np.random.default_rng(seed)
    out: list[tuple[int, ...]] = []
    out_d: list[Any] = []
    for d, idx in _by_date(date):
        games, inv = np.unique(game[idx], return_inverse=True)
        by_game = [idx[inv == g] for g in range(len(games))]
        ok = [g for g in range(len(games)) if len(by_game[g]) >= m]
        if not ok:
            continue
        seen: set[tuple[int, ...]] = set()
        for _ in range(per_date):
            g = ok[int(rng.integers(len(ok)))]
            pick = tuple(sorted(int(v) for v in rng.choice(by_game[g], size=m, replace=False)))
            if pick in seen:
                continue
            seen.add(pick)
            out.append(pick)
            out_d.append(d)
    return np.array(out, dtype=np.int64).reshape(-1, m), np.array(out_d, dtype="datetime64[D]")


def top_candidates(
    date: np.ndarray, game: np.ndarray, q: np.ndarray, m: int, kind: str
) -> tuple[np.ndarray, np.ndarray]:
    """Panel candidate sets, deterministic. ``kind='cross'``: per date, each game keeps its
    TOP_PER_GAME_CROSS highest-q legs (ties by index); all m-subsets of games x leg choices
    (exact for the top 3 by a product of leg q). ``kind='same'``: per game, the TOP_UNITS_SAME
    highest-q legs; all m-subsets of them. The caller ranks the candidates by its own joint
    probability and keeps TOP_N."""
    out: list[tuple[int, ...]] = []
    out_d: list[Any] = []
    keep_n = TOP_PER_GAME_CROSS if kind == "cross" else TOP_UNITS_SAME
    for d, idx in _by_date(date):
        games, inv = np.unique(game[idx], return_inverse=True)
        per_game: list[list[int]] = []
        for g in range(len(games)):
            gi = idx[inv == g]
            order = gi[np.lexsort((gi, -q[gi]))]
            per_game.append([int(v) for v in order[:keep_n]])
        if kind == "cross":
            for gs in itertools.combinations(range(len(games)), m):
                for pick in itertools.product(*[per_game[g] for g in gs]):
                    out.append(tuple(sorted(pick)))
                    out_d.append(d)
        else:
            for lst in per_game:
                for pick in itertools.combinations(lst, m):
                    out.append(tuple(sorted(pick)))
                    out_d.append(d)
    return np.array(out, dtype=np.int64).reshape(-1, m), np.array(out_d, dtype="datetime64[D]")


def keep_top(idx: np.ndarray, tdate: np.ndarray, p_joint: np.ndarray, n: int = TOP_N) -> np.ndarray:
    """Row positions of the ``n`` highest p_joint per date (ties: lower leg-index tuple first)."""
    keep: list[int] = []
    for d in np.unique(tdate):
        rows = np.flatnonzero(tdate == d)
        keys = [idx[rows, k] for k in range(idx.shape[1] - 1, -1, -1)]
        order = rows[np.lexsort((*keys, -p_joint[rows]))]
        keep.extend(int(v) for v in order[:n])
    return np.array(sorted(keep), dtype=np.int64)


def ticket_fingerprint(legs: pl.DataFrame, idx: np.ndarray) -> str:
    """sha256 over the tickets written as leg identities: pins the construction independently
    of the random stream."""
    g = legs["game_id"].to_list()
    p = legs["player_id"].to_list()
    s = legs["stat"].to_list()
    h = hashlib.sha256()
    for row in idx:
        h.update("|".join(f"{g[i]}:{p[i]}:{s[i]}" for i in row).encode() + b"\n")
    return h.hexdigest()


# ------------------------------------------------------------------ model-only quantities


def prod_q(q: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return np.asarray(np.prod(q[idx], axis=1))


def cons_halfwidth(p: np.ndarray, tdate: np.ndarray) -> dict[str, float]:
    """Half-width of a mean over tickets with per-ticket sd sqrt(p(1-p)): independent tickets,
    and the conservative bound that treats every ticket of one date as perfectly correlated."""
    sd = np.sqrt(p * (1 - p))
    n = len(p)
    ind = 1.96 * float(np.sqrt(np.sum(sd**2))) / n
    ud, inv = np.unique(tdate, return_inverse=True)
    s_d = np.bincount(inv, weights=sd, minlength=len(ud))
    return {"indep": ind, "conservative": 1.96 * float(np.sqrt(np.sum(s_d**2))) / n}


def sim_halfwidth(
    parts: list[tuple[np.ndarray, np.ndarray]], sims: int, seed: Sequence[int]
) -> float:
    """Model-only 95% half-width of the mean gap (hit - product of leg q) when every leg is
    exactly calibrated and independent (hit ~ Bernoulli(q)), with outcomes shared across the
    tickets that share a leg. ``parts`` = [(leg q array, ticket index)] per season; the value
    is 1.96 x the sd over ``sims`` replicates."""
    rng = np.random.default_rng(seed)
    gaps = np.zeros(sims)
    tot = sum(len(ix) for _, ix in parts)
    for q, ix in parts:
        p = prod_q(q, ix)
        u = rng.random((len(q), sims))
        hit = u < q[:, None]
        thit = np.ones((len(ix), sims), dtype=bool)
        for k in range(ix.shape[1]):
            thit &= hit[ix[:, k]]
        gaps += (thit.astype(np.float64) - p[:, None]).sum(axis=0)
    return 1.96 * float(np.std(gaps / tot, ddof=1))


def equicorr_joint(q: np.ndarray, idx: np.ndarray, rho: float) -> np.ndarray:
    """P(all legs hit) under an equicorrelated Gaussian copula with leg hit probabilities q:
    legs hit iff Z_i > Phi^-1(1 - q_i). Used only for the model-only copula power numbers."""
    from scipy import stats

    m = idx.shape[1]
    R = np.full((m, m), rho)
    np.fill_diagonal(R, 1.0)
    mv = stats.multivariate_normal(mean=np.zeros(m), cov=R, allow_singular=False)
    t = stats.norm.ppf(1 - q[idx])  # (T, m)
    out = np.empty(len(idx))
    for i in range(len(idx)):
        out[i] = mv.cdf(-t[i])
    return np.clip(out, 1e-9, 1 - 1e-9)


def copula_gain(q: np.ndarray, idx: np.ndarray, tdate: np.ndarray, rho: float) -> dict[str, float]:
    """If the truth were the equicorrelated copula (rho), the mean per-ticket log-loss gain of
    the copula probability over independence, with the independent-tickets and conservative
    (date-perfectly-correlated) half-widths of that mean. Exact two-point law per ticket."""
    p_i = np.clip(prod_q(q, idx), 1e-9, 1 - 1e-9)
    p_c = equicorr_joint(q, idx, rho)
    ll_i = -(p_c * np.log(p_i) + (1 - p_c) * np.log(1 - p_i))
    ll_c = -(p_c * np.log(p_c) + (1 - p_c) * np.log(1 - p_c))
    mean = ll_i - ll_c  # expected LL_ind - LL_cop > 0
    v = p_c * (1 - p_c) * (np.log(p_c / (1 - p_c)) - np.log(p_i / (1 - p_i))) ** 2
    sd = np.sqrt(v)
    n = len(p_c)
    ud, inv = np.unique(tdate, return_inverse=True)
    s_d = np.bincount(inv, weights=sd, minlength=len(ud))
    return {
        "rho": rho,
        "expected_gain_per_ticket": float(mean.mean()),
        "halfwidth_indep": 1.96 * float(np.sqrt(np.sum(v))) / n,
        "halfwidth_conservative": 1.96 * float(np.sqrt(np.sum(s_d**2))) / n,
    }


def relation_shares(legs: pl.DataFrame, idx: np.ndarray) -> dict[str, float]:
    pl_ = legs["player_id"].to_numpy()
    tm = legs["team_id"].to_numpy()
    same_p = team_m = opp = tot = 0
    for row in idx:
        for a, b in itertools.combinations(row, 2):
            tot += 1
            if pl_[a] == pl_[b]:
                same_p += 1
            elif tm[a] == tm[b]:
                team_m += 1
            else:
                opp += 1
    return {
        "pairs": tot,
        "same_player": same_p / max(tot, 1),
        "teammates": team_m / max(tot, 1),
        "opponents": opp / max(tot, 1),
    }


# ------------------------------------------------------------------------------ the gate


def describe(arr: np.ndarray) -> dict[str, float]:
    return {f"p{k}": float(np.quantile(arr, k / 100)) for k in (5, 25, 50, 75, 95)}


def cell_stats(
    legs_by_season: dict[int, pl.DataFrame], kind: str, m: int, sims: int
) -> dict[str, Any]:
    res: dict[str, Any] = {"kind": kind, "size": m, "seasons": {}}
    parts: list[tuple[np.ndarray, np.ndarray]] = []
    for si, season in enumerate(SEASONS):
        legs = legs_by_season[season]
        date = legs["game_date"].to_numpy().astype("datetime64[D]")
        game = legs["game_id"].to_numpy()
        q = legs["q_proxy"].to_numpy().astype(float)
        seed = [ROOT_SEED, season, m, 1 if kind == "cross" else 2]
        fn = cross_tickets if kind == "cross" else same_tickets
        idx, td = fn(date, game, m, PER_DATE, seed)
        p = prod_q(q, idx)
        hw = cons_halfwidth(p, td)
        entry: dict[str, Any] = {
            "tickets": len(idx),
            "dates": int(len(np.unique(td))),
            "p_joint_proxy": describe(p) if len(p) else {},
            "halfwidth_gap_indep_tickets": hw["indep"],
            "halfwidth_gap_conservative": hw["conservative"],
            "fingerprint_sha256": ticket_fingerprint(legs, idx),
        }
        if kind == "same":
            entry["relation_shares"] = relation_shares(legs, idx)
            for rho in (0.05, 0.10, 0.20):
                entry[f"copula_gain_rho{rho}"] = copula_gain(q, idx, td, rho)
        entry["model_only_halfwidth_gap"] = sim_halfwidth(
            [(q, idx)], sims, [*SIM_SEED, season, m, si]
        )
        res["seasons"][str(season)] = entry
        parts.append((q, idx))
    res["pooled_tickets"] = sum(res["seasons"][str(s)]["tickets"] for s in SEASONS)
    res["pooled_model_only_halfwidth_gap"] = sim_halfwidth(parts, sims, [*SIM_SEED, 0, m])
    return res


def top_stats(legs_by_season: dict[int, pl.DataFrame], m: int, sims: int) -> dict[str, Any]:
    """The panel slice (top TOP_N per date and size) ranked by the independence proxy. Counts and
    model-only widths; the harness re-ranks same-game candidates by the copula."""
    res: dict[str, Any] = {"size": m, "seasons": {}}
    empty = np.array([], dtype=np.int64)
    for si, season in enumerate(SEASONS):
        legs = legs_by_season[season]
        date = legs["game_date"].to_numpy().astype("datetime64[D]")
        game = legs["game_id"].to_numpy()
        q = legs["q_proxy"].to_numpy().astype(float)
        entry: dict[str, Any] = {}
        sel: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for kind in ("cross", "same"):
            idx, td = top_candidates(date, game, q, m, kind)
            keep = keep_top(idx, td, prod_q(q, idx)) if len(idx) else empty
            sel[kind] = (idx[keep], td[keep])
            entry[f"{kind}_candidates"] = len(idx)
            entry[f"{kind}_top{TOP_N}_tickets"] = len(keep)
        idx_u = np.vstack([sel["cross"][0], sel["same"][0]])
        td_u = np.concatenate([sel["cross"][1], sel["same"][1]])
        keep_u = keep_top(idx_u, td_u, prod_q(q, idx_u)) if len(idx_u) else empty
        entry["union_tickets"] = len(keep_u)
        entry["union_dates"] = int(len(np.unique(td_u[keep_u]))) if len(keep_u) else 0
        n_cross = len(sel["cross"][0])
        entry["union_share_same_game"] = float(np.mean(keep_u >= n_cross)) if len(keep_u) else 0.0
        if len(keep_u):
            ixu, tdu = idx_u[keep_u], td_u[keep_u]
            cons = cons_halfwidth(prod_q(q, ixu), tdu)["conservative"]
            entry["union_halfwidth_gap_conservative"] = cons
            entry["union_model_only_halfwidth_gap"] = sim_halfwidth(
                [(q, ixu)], sims, [*SIM_SEED, season, m, 20 + si]
            )
        for kind in ("cross", "same"):
            ix, td = sel[kind]
            if len(ix):
                p = prod_q(q, ix)
                entry[f"{kind}_p_joint_proxy"] = describe(p)
                entry[f"{kind}_halfwidth_gap_conservative"] = cons_halfwidth(p, td)["conservative"]
                entry[f"{kind}_model_only_halfwidth_gap"] = sim_halfwidth(
                    [(q, ix)], sims, [*SIM_SEED, season, m, 10 + si]
                )
        res["seasons"][str(season)] = entry
    return res


def run_gate(root: Path, sims: int) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON
    note = "q = 0.5 + 0.5 (max(p, 1-p) - 0.5): a sizing proxy for the CAL leg, not the CAL map"
    out: dict[str, Any] = {
        "rule": "PARLAY_JOINT_CAL (DRAFT)",
        "date_stamp": time.strftime("%a %b %d %H:%M:%S %Z %Y"),
        "proxy_note": note + "; no label was read",
        "constants": {
            "PER_DATE": PER_DATE,
            "SIZES": SIZES,
            "TOP_N": TOP_N,
            "TOP_PER_GAME_CROSS": TOP_PER_GAME_CROSS,
            "TOP_UNITS_SAME": TOP_UNITS_SAME,
            "MIN_FIT": MIN_FIT,
            "N_SIMS": sims,
        },
        "code_sha256": {f: file_sha(Path(f)) for f in CODE_FILES if Path(f).exists()},
        "books": {},
    }
    for book in BOOKS:
        legs_by: dict[int, pl.DataFrame] = {}
        diag: dict[str, Any] = {}
        for s in SEASONS:
            legs_by[s], diag[str(s)] = load_legs(root, s, book)
            _log(f"{book} {s}: {legs_by[s].height} legs, {diag[str(s)]['dates']} dates")
        allv = pl.concat([legs_by[s].select(["game_date", "season", "stat"]) for s in SEASONS])
        identity = {str(s): identity_share(allv, s) for s in SEASONS}
        eligible: dict[str, Any] = {}
        for s in SEASONS:
            before = legs_by[s].height
            legs_by[s] = cal_served(allv, legs_by[s], s)
            eligible[str(s)] = {
                "legs_before": before,
                "legs_cal_served": legs_by[s].height,
                "dates_cal_served": int(legs_by[s]["game_date"].n_unique()),
            }
        b: dict[str, Any] = {
            "legs": diag,
            "cal_identity": identity,
            "eligible_legs": eligible,
            "leg_q_proxy_by_season": {
                str(s): describe(legs_by[s]["q_proxy"].to_numpy().astype(float)) for s in SEASONS
            },
            "leg_q_raw_by_season": {
                str(s): describe(legs_by[s]["q_raw"].to_numpy().astype(float)) for s in SEASONS
            },
            "cells": {},
            "top": {},
        }
        for kind in ("cross", "same"):
            for m in SIZES:
                _log(f"{book} {kind} size {m}")
                b["cells"][f"{kind}_size{m}"] = cell_stats(legs_by, kind, m, sims)
        for m in SIZES:
            _log(f"{book} top size {m}")
            b["top"][f"size{m}"] = top_stats(legs_by, m, sims)
        out["books"][book] = b
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_parlay_joint_cal/gate.json"))
    ap.add_argument("--sims", type=int, default=N_SIMS)
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, sims=args.sims)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
