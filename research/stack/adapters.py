"""Adapters that turn existing candidates into OOF frames.

* :func:`from_dist_frame` -- any frame with ``mean`` + ``std`` + a family name.
* :func:`season_avg_oof` -- played-only recency-weighted average (same formula
  as ``nba.props.forward.recency_weighted_dist``; replicated here so the stack
  package does not import the heavy sim stack).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from nba.props.baselines import _DEFAULT_STD
from research.stack.oof import TAUS

FAMILIES = ("normal", "gamma", "nbinom")


def quantile_grid(mean: np.ndarray, std: np.ndarray, family: str) -> np.ndarray:
    """(n, 19) quantiles at TAUS for ``family`` with the given mean/std."""
    mean = np.asarray(mean, dtype=float)
    std = np.maximum(np.asarray(std, dtype=float), 1e-6)
    t = TAUS[None, :]
    if family == "normal":
        q = stats.norm.ppf(t, loc=mean[:, None], scale=std[:, None])
    elif family == "gamma":
        m = np.maximum(mean, 1e-6)
        shape = (m / std) ** 2
        q = stats.gamma.ppf(t, a=shape[:, None], scale=(std**2 / m)[:, None])
    elif family == "nbinom":
        m = np.maximum(mean, 1e-6)
        var = np.maximum(std**2, m * (1 + 1e-6))  # under-dispersed -> ~Poisson
        pr = m / var
        nn = m * pr / (1 - pr)
        q = stats.nbinom.ppf(t, n=nn[:, None], p=pr[:, None])
    else:
        raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
    return np.maximum.accumulate(np.asarray(q, dtype=float), axis=1)


def from_dist_frame(
    df: pd.DataFrame,
    target: str,
    family: str,
    *,
    made_with_data_through: pd.Series | None = None,
) -> pd.DataFrame:
    """Build an OOF frame from ``game_id, player_id, game_date, season, mean, std``.

    ``made_with_data_through`` defaults to the frame column of that name; there
    is deliberately no default of ``game_date - 1`` so callers must state it.
    """
    thru = (
        made_with_data_through
        if made_with_data_through is not None
        else df.get("made_with_data_through")
    )
    if thru is None:
        raise ValueError("made_with_data_through must be provided (column or argument)")
    q = quantile_grid(df["mean"].to_numpy(), df["std"].to_numpy(), family)
    out = pd.DataFrame(
        {
            "target": target,
            "game_id": df["game_id"].to_numpy(),
            "player_id": df["player_id"].to_numpy(),
            "game_date": df["game_date"].to_numpy(),
            "season": df["season"].to_numpy(),
            "made_with_data_through": np.asarray(thru),
            "mean": df["mean"].to_numpy(dtype=float),
            "q_grid": list(q),
        }
    )
    for col in ("fold_id", "p_play", "meta"):
        if col in df.columns:
            out[col] = df[col].to_numpy()
    return out


def season_avg_oof(
    history: pd.DataFrame,
    stat: str,
    *,
    halflife_games: float = 10.0,
    min_prior_played: int = 3,
    max_lookback: int = 200,
    p_play_window: int = 10,
    max_season: int | None = None,
) -> pd.DataFrame:
    """Played-only recency-weighted Normal per player-game, strictly as-of.

    ``history``: ``game_id, player_id, game_date, season, y, played`` with one
    row per player-game INCLUDING DNP rows (``played`` False). For each played
    row the prediction uses only that player's earlier played games (strictly
    earlier dates); ``p_play`` is the share of the player's previous
    ``p_play_window`` rows that were played. Only played rows are emitted
    (conditional-on-play contract). ``max_season`` drops later seasons.
    """
    if stat not in _DEFAULT_STD:
        raise ValueError(f"unknown stat {stat!r}")
    h = history.sort_values(["player_id", "game_date", "game_id"])
    if max_season is not None:
        h = h[h["season"] <= max_season]
    recs: list[dict[str, Any]] = []
    for _pid, g in h.groupby("player_id", sort=False):
        dates = pd.to_datetime(g["game_date"]).to_numpy()
        y = g["y"].to_numpy(dtype=float)
        played = g["played"].to_numpy(dtype=bool)
        gid = g["game_id"].to_numpy()
        seas = g["season"].to_numpy()
        pid = int(g["player_id"].iloc[0])
        for i in range(len(g)):
            if not played[i]:
                continue
            prior = np.where((dates < dates[i]) & played)[0][-max_lookback:]
            if len(prior) < min_prior_played:
                continue
            vals = y[prior]
            n = len(vals)
            w = 0.5 ** (np.arange(n)[::-1] / halflife_games)
            mean = float((w * vals).sum() / w.sum())
            var = float((w * (vals - mean) ** 2).sum() / w.sum()) * n / (n - 1)
            std = var**0.5 if var > 0 else _DEFAULT_STD[stat]
            before = np.where(dates < dates[i])[0][-p_play_window:]
            recs.append(
                {
                    "game_id": gid[i],
                    "player_id": pid,
                    "game_date": dates[i],
                    "season": int(seas[i]),
                    "made_with_data_through": dates[prior[-1]],
                    "mean": mean,
                    "std": std,
                    "p_play": float(played[before].mean()) if len(before) else np.nan,
                }
            )
    if not recs:
        raise ValueError("no rows produced (not enough history)")
    return from_dist_frame(pd.DataFrame(recs), stat, "normal")
