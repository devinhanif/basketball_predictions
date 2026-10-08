"""As-of regularized adjusted plus-minus (RAPM) from real 5-on-5 possessions.

Used as an alternative "player value" for the injury-adjusted Elo
(``nba.models.injury_elo``). Mirrors the as-of window discipline of
``nba.features.player_possession_features``: a snapshot labelled with cutoff
date ``c`` is built ONLY from possessions dated strictly before ``c``; later
possessions can never change it (``tests/features/test_rapm.py`` proves this).

Design: one row per possession, +1 for each of the 5 offensive players, -1 for
each of the 5 defensive players, plus an unpenalized intercept and an
unpenalized "offense is home" column; target = points x 100. The ridge
solution needs only the Gram matrix ``X'WX`` and ``X'Wy``, so each month is
accumulated once (sparse product) and a refit is a single dense Cholesky solve
(about 1,100 players: a fraction of a second).

Sign convention: ``rapm = 2 * beta`` is net points per 100 possessions
(beta on offense plus beta on defense); positive means the player helps his
team. Prior seasons enter with weight ``history_decay ** season_gap``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import polars as pl
import scipy.sparse as sp
from scipy.linalg import solve as dense_solve


@dataclass(frozen=True)
class RapmConfig:
    ridge_lambda: float = 2000.0  # pseudo-possessions of shrinkage toward the prior
    history_decay: float = 0.5  # weight per season of age for earlier seasons
    prior_carry: float = 0.0  # >0: shrink toward carry * last month-start beta (off by default)


@dataclass
class PossessionData:
    """Possession arrays sorted by date. ``off``/``dfn``: (n, 5) player ids."""

    dates: np.ndarray  # datetime64[D]
    seasons: np.ndarray
    off: np.ndarray
    dfn: np.ndarray
    pts: np.ndarray
    off_home: np.ndarray  # 1.0 if the offensive team is the home team

    def __len__(self) -> int:
        return int(self.pts.shape[0])


def load_possessions(con: duckdb.DuckDBPyConnection, max_season: int) -> PossessionData:
    """Possessions with full 5-man lineups for games with season <= ``max_season``."""
    q = (
        "SELECT g.game_date, g.season, "
        "p.off_players[1], p.off_players[2], p.off_players[3], p.off_players[4], p.off_players[5], "
        "p.def_players[1], p.def_players[2], p.def_players[3], p.def_players[4], p.def_players[5], "
        "p.pts, CASE WHEN p.off_team = g.home_team THEN 1.0 ELSE 0.0 END "
        "FROM possessions p JOIN games g ON g.game_id = p.game_id "
        "WHERE g.season <= ? AND p.pts IS NOT NULL "
        "AND len(p.off_players) = 5 AND len(p.def_players) = 5 "
        "ORDER BY g.game_date, p.game_id, p.poss_idx"
    )
    cols = list(con.execute(q, [max_season]).fetchnumpy().values())
    dates = np.asarray(cols[0]).astype("datetime64[D]")
    off = np.column_stack([np.asarray(c, dtype=np.int64) for c in cols[2:7]])
    dfn = np.column_stack([np.asarray(c, dtype=np.int64) for c in cols[7:12]])
    return PossessionData(
        dates=dates,
        seasons=np.asarray(cols[1], dtype=np.int64),
        off=off,
        dfn=dfn,
        pts=np.asarray(cols[12], dtype=float),
        off_home=np.asarray(cols[13], dtype=float),
    )


def build_design(
    off: np.ndarray, dfn: np.ndarray, off_home: np.ndarray, index: dict[int, int]
) -> sp.csr_matrix:
    """Sparse (n, P+2) design: +1 offense, -1 defense, then intercept, off_home."""
    n = off.shape[0]
    p = len(index)
    keys = np.fromiter(sorted(index), dtype=np.int64)
    pos = np.fromiter((index[int(k)] for k in keys), dtype=np.int64)

    def lut(a: np.ndarray) -> np.ndarray:
        return pos[np.searchsorted(keys, a)]

    rows = np.repeat(np.arange(n), 10)
    cols = np.concatenate([lut(off), lut(dfn)], axis=1).ravel()
    vals = np.tile(np.array([1.0] * 5 + [-1.0] * 5), n)
    x = sp.csr_matrix((vals, (rows, cols)), shape=(n, p))
    extra = sp.csr_matrix(np.column_stack([np.ones(n), off_home]))
    return sp.hstack([x, extra], format="csr")


def solve_ridge(
    gram: np.ndarray,
    xty: np.ndarray,
    n_players: int,
    ridge_lambda: float,
    prior: np.ndarray | None = None,
) -> np.ndarray:
    """Solve ``(G + L) b = X'y + L prior``; L penalizes players only, not the 2 extra columns."""
    k = gram.shape[0]
    pen = np.zeros(k)
    pen[:n_players] = ridge_lambda
    rhs = xty.copy()
    if prior is not None:
        rhs = rhs + pen * prior
    a = gram + np.diag(pen) + 1e-9 * np.eye(k)
    return np.asarray(dense_solve(a, rhs, assume_a="pos"), dtype=float)


def _month_keys(dates: np.ndarray) -> np.ndarray:
    return dates.astype("datetime64[M]")


class _Chunks:
    """Per-month Gram/xty chunks plus season of each month, built once."""

    def __init__(self, poss: PossessionData, index: dict[int, int]) -> None:
        self.index = index
        self.months = _month_keys(poss.dates)
        self.uniq = np.unique(self.months)
        self.gram: dict[Any, np.ndarray] = {}
        self.xty: dict[Any, np.ndarray] = {}
        self.season: dict[Any, int] = {}
        self.x: dict[Any, sp.csr_matrix] = {}
        self.y: dict[Any, np.ndarray] = {}
        for m in self.uniq:
            sel = self.months == m
            x = build_design(poss.off[sel], poss.dfn[sel], poss.off_home[sel], index)
            y = poss.pts[sel] * 100.0
            self.gram[m] = np.asarray((x.T @ x).todense())
            self.xty[m] = np.asarray(x.T @ y).ravel()
            self.season[m] = int(poss.seasons[sel].max())
            self.x[m] = x
            self.y[m] = y

    def accumulate(
        self, before: Any, target_season: int, decay: float
    ) -> tuple[np.ndarray, np.ndarray]:
        k = len(self.index) + 2
        a = np.zeros((k, k))
        b = np.zeros(k)
        for m in self.uniq:
            if m >= before:
                break
            w = decay ** max(target_season - self.season[m], 0)
            if w == 0.0:
                continue
            a += w * self.gram[m]
            b += w * self.xty[m]
        return a, b


def player_index(poss: PossessionData) -> dict[int, int]:
    ids = np.unique(np.concatenate([poss.off.ravel(), poss.dfn.ravel()]))
    return {int(p): i for i, p in enumerate(ids)}


def build_snapshots(
    poss: PossessionData, cfg: RapmConfig, chunks: _Chunks | None = None
) -> pl.DataFrame:
    """Monthly as-of snapshots. Columns: cutoff, player_id, rapm, n_poss.

    The snapshot at ``cutoff`` (first day of a month with possessions) uses
    only possessions dated strictly before it. ``n_poss`` is the (decay-
    weighted) number of possessions the player was on court in that window.
    """
    index = player_index(poss) if chunks is None else chunks.index
    ch = _Chunks(poss, index) if chunks is None else chunks
    ids = np.array(sorted(index, key=index.__getitem__), dtype=np.int64)
    p = len(index)
    frames: list[pl.DataFrame] = []
    prev_beta: np.ndarray | None = None
    for m in ch.uniq:
        a, b = ch.accumulate(m, ch.season[m], cfg.history_decay)
        prior = None
        if cfg.prior_carry > 0.0 and prev_beta is not None:
            prior = np.zeros_like(b)
            prior[:p] = cfg.prior_carry * prev_beta[:p]
        beta = solve_ridge(a, b, p, cfg.ridge_lambda, prior)
        prev_beta = beta
        n_poss = np.diag(a)[:p]
        seen = n_poss > 0
        if not seen.any():
            continue
        cutoff = m.astype("datetime64[D]").astype(dt.date)
        frames.append(
            pl.DataFrame(
                {
                    "cutoff": pl.Series([cutoff] * int(seen.sum()), dtype=pl.Date),
                    "player_id": ids[seen],
                    "rapm": 2.0 * beta[:p][seen],
                    "n_poss": n_poss[seen],
                }
            )
        )
    return (
        pl.concat(frames)
        if frames
        else pl.DataFrame(
            schema={
                "cutoff": pl.Date,
                "player_id": pl.Int64,
                "rapm": pl.Float64,
                "n_poss": pl.Float64,
            }
        )
    )


def cv_ridge_lambda(
    poss: PossessionData,
    lambdas: Sequence[float],
    season: int,
    history_decay: float,
    min_train_months: int = 2,
) -> pl.DataFrame:
    """Walk-forward CV within ``season``: fit before month M, possession MSE in M.

    Returns lambda, mse, n (pooled over months); also ``mse_null`` (intercept
    + home only, lambda=inf) for reference.
    """
    sub_sel = poss.seasons <= season
    sub = PossessionData(
        poss.dates[sub_sel],
        poss.seasons[sub_sel],
        poss.off[sub_sel],
        poss.dfn[sub_sel],
        poss.pts[sub_sel],
        poss.off_home[sub_sel],
    )
    ch = _Chunks(sub, player_index(sub))
    p = len(ch.index)
    months = [m for m in ch.uniq if ch.season[m] == season]
    sse = dict.fromkeys(list(lambdas) + [float("inf")], 0.0)
    n_tot = 0
    for i, m in enumerate(months):
        if i < min_train_months:
            continue
        a, b = ch.accumulate(m, season, history_decay)
        for lam in lambdas:
            beta = solve_ridge(a, b, p, lam)
            r = ch.y[m] - ch.x[m] @ beta
            sse[lam] += float(r @ r)
        beta0 = solve_ridge(a, b, p, 1e12)
        r0 = ch.y[m] - ch.x[m] @ beta0
        sse[float("inf")] += float(r0 @ r0)
        n_tot += int(ch.y[m].shape[0])
    return pl.DataFrame(
        {
            "lambda": [float(k) for k in sse],
            "mse": [v / max(n_tot, 1) for v in sse.values()],
            "n": [n_tot] * len(sse),
        }
    )


def season_end_rapm(poss: PossessionData, season: int, ridge_lambda: float) -> pl.DataFrame:
    """Single-season (no history) RAPM using the whole season; for sanity lists only."""
    sel = poss.seasons == season
    sub = PossessionData(
        poss.dates[sel],
        poss.seasons[sel],
        poss.off[sel],
        poss.dfn[sel],
        poss.pts[sel],
        poss.off_home[sel],
    )
    index = player_index(sub)
    ch = _Chunks(sub, index)
    a, b = ch.accumulate(np.datetime64("2100-01", "M"), season, 0.0)
    p = len(index)
    beta = solve_ridge(a, b, p, ridge_lambda)
    ids = np.array(sorted(index, key=index.__getitem__), dtype=np.int64)
    return pl.DataFrame({"player_id": ids, "rapm": 2.0 * beta[:p], "n_poss": np.diag(a)[:p]}).sort(
        "rapm", descending=True
    )
