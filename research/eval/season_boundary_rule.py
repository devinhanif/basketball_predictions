# ruff: noqa: E501
"""SEASON_BOUNDARY constants and pure as-of functions (rule: docs/prereg/SEASON_BOUNDARY.md, DRAFT).

Nothing here reads an outcome of a scored row. Every quantity for a row is built from games dated strictly before that row's date.
Season labels are ``games.season`` (2023 = 2023-24). Season 2025 is the frozen holdout and is never loaded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ID = "SEASON_BOUNDARY"
STATS = ("pts", "reb", "ast", "fg3m")
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
MIN_FLOOR = 0.03  # minutes CRPS floor, minutes (continuous support)
SELECT, REPORT, MAX_SEASON = 2023, 2024, 2024
K_GRID = (5, 10, 20)  # soft-reset k; chosen on the select season only
K_MINUTES = None  # minutes arms reuse the k chosen for A1 (no second grid)
SLICE_G = 20  # first 20 played games of a player-season
MIN_PRIOR_FOR_L = 20  # a "returner" has >= 20 played games before the season
HL_THIS, HL_LONG1, HL_LONG2 = 10.0, 40.0, 100.0
LEAGUE_C = 20.0  # pseudo-count (league games) of last season's league rate in the as-of league rate
LEAGUE_DRIVER = {"fg3m": "fg3a"}  # A2 driver per stat (default: the stat itself)
STAB = 0.5  # additive stabiliser inside log ratios (zero fg3m)
AGE_MIN_CELL = (
    300  # player-seasons per age bin in the frozen aging table, else the bin's logdelta is 0
)
AGE_LO, AGE_HI = 19, 36


def w_this(g: np.ndarray, k: float) -> np.ndarray:
    """Weight on this season's games after g played games: g / (g + k)."""
    g = np.asarray(g, float)
    return g / (g + k)


def a0_equivalent_weight(g: np.ndarray, hl: float = HL_THIS) -> np.ndarray:
    """Share of production's half-life-10 recency mass that sits on this season's g games when last season is long: 1 - 0.5**(g/hl)."""
    return 1.0 - 0.5 ** (np.asarray(g, float) / hl)


def decayed_states(pid: np.ndarray, season: np.ndarray, x: np.ndarray) -> dict[str, np.ndarray]:
    """Sequential per-player states over PLAYED rows sorted by (player, date, game). ``x`` is (n, c). Returns, per row and strictly BEFORE it:
    ``g`` (played games this season), ``n_prior``, ``m_this`` (half-life 10 within the season only; NaN at g = 0), ``m10`` (half-life 10
    carried across seasons = production's recency mean), ``L0`` (0.5 * m40 + 0.5 * m100 frozen at the player's first game of the season;
    NaN when fewer than MIN_PRIOR_FOR_L prior games)."""
    n, c = x.shape
    d10, d40, d100 = (0.5 ** (1.0 / h) for h in (HL_THIS, HL_LONG1, HL_LONG2))
    g = np.zeros(n, dtype=np.int32)
    nprior = np.zeros(n, dtype=np.int32)
    m_this = np.full((n, c), np.nan)
    m10 = np.full((n, c), np.nan)
    L0 = np.full((n, c), np.nan)
    s10 = np.zeros(c)
    w10 = 0.0
    s40 = np.zeros(c)
    w40 = 0.0
    s100 = np.zeros(c)
    w100 = 0.0
    st = np.zeros(c)
    wt = 0.0
    cnt = 0
    gs = 0
    l_season = np.full(c, np.nan)
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s10[:] = s40[:] = s100[:] = st[:] = 0.0
            w10 = w40 = w100 = wt = 0.0
            cnt = gs = 0
            l_season = np.full(c, np.nan)
            new_season = True
        else:
            new_season = season[i] != season[i - 1]
        if new_season:
            st[:] = 0.0
            wt = 0.0
            gs = 0
            l_season = (
                0.5 * s40 / w40 + 0.5 * s100 / w100
                if cnt >= MIN_PRIOR_FOR_L
                else np.full(c, np.nan)
            )
        g[i] = gs
        nprior[i] = cnt
        if wt > 0:
            m_this[i] = st / wt
        if cnt > 0:
            m10[i] = s10 / w10
        L0[i] = l_season
        s10 = d10 * s10 + x[i]
        w10 = d10 * w10 + 1.0
        s40 = d40 * s40 + x[i]
        w40 = d40 * w40 + 1.0
        s100 = d100 * s100 + x[i]
        w100 = d100 * w100 + 1.0
        st = d10 * st + x[i]
        wt = d10 * wt + 1.0
        cnt += 1
        gs += 1
    return {"g": g, "n_prior": nprior, "m_this": m_this, "m10": m10, "L0": L0}


def a1_base(
    st: dict[str, np.ndarray],
    k: float,
    age_logdelta: np.ndarray | None = None,
    league_f: np.ndarray | None = None,
) -> np.ndarray:
    """A1 (age_logdelta given) / A2 (league_f also given) base: w * m_this + (1 - w) * L0 * exp(age) * f; falls back to production m10 when
    L0 is undefined. Shapes (n, c); age_logdelta and league_f broadcast over rows."""
    w = w_this(st["g"], k)[:, None]
    L = st["L0"].copy()
    if age_logdelta is not None:
        L = L * np.exp(age_logdelta)
    if league_f is not None:
        L = L * league_f
    mt = np.where(np.isnan(st["m_this"]), 0.0, st["m_this"])
    base = w * mt + (1.0 - w) * L
    return np.where(np.isnan(st["L0"]), st["m10"], base)


def league_asof(
    box: pd.DataFrame, cols: list[str], prev_rate: dict[str, float], c: float = LEAGUE_C
) -> pd.DataFrame:
    """As-of league per-36 rate by date for ONE season's regular-season box rows (columns ``game_id, game_date, minutes`` + ``cols``):
    shrunk rate on date d uses only dates < d: (n_g * obs + c * prev) / (n_g + c), n_g = league games before d. Returns one row per date with
    ``f_<col>`` = shrunk rate / prev rate (1.0 on the first date)."""
    b = box.sort_values("game_date")
    daily = b.groupby("game_date").agg(
        games=("game_id", "nunique"), minutes=("minutes", "sum"), **{k: (k, "sum") for k in cols}
    )
    cum = daily.cumsum().shift(1).fillna(0.0)
    out = pd.DataFrame(index=daily.index)
    for k in cols:
        obs = np.where(
            cum["minutes"] > 0,
            36.0 * cum[k] / cum["minutes"].where(cum["minutes"] > 0, 1.0),
            prev_rate[k],
        )
        shr = (cum["games"] * obs + c * prev_rate[k]) / (cum["games"] + c)
        out[f"f_{k}"] = shr / prev_rate[k]
    return out
