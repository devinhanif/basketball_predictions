"""Synthetic universes for the dry run and the tests. Never real prices or outcomes.

A world is a set of games on consecutive ET dates, each with player ladders (nested "k or more"
rungs
priced off a Poisson survival curve), team spread / team total ladders and a game total ladder.
Outcomes
are drawn ladder by ladder from one uniform ``u`` per ladder (nested rungs), with the true YES
probability
equal to the quote mid (calibrated null), or scaled so that the pooled expected NO return after fee
and
spread equals a planted edge.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import numpy as np
import polars as pl
from scipy.stats import norm, poisson

from research.market_forward.data import Universe, ladder_key
from research.market_forward.fee import CENT, UNIT, PinnedFee
from research.market_forward.spec import (
    COMBO_SERIES,
    PROP_SERIES,
    WINDOW_START,
    RuleSpec,
    kind_of,
)

_PLAYER_SERIES = PROP_SERIES + COMBO_SERIES[:1]
_LAMBDA = {
    "KXNBAPTS": (8.0, 28.0, 5),
    "KXNBAREB": (2.0, 12.0, 2),
    "KXNBAAST": (1.0, 9.0, 2),
    "KXNBA3PT": (0.5, 4.0, 1),
    "KXNBASTL": (0.4, 2.0, 1),
    "KXNBABLK": (0.3, 2.0, 1),
    "KXNBAPRA": (12.0, 45.0, 5),
}


def _quote(rng: np.random.Generator, p: float) -> tuple[int, int]:
    s = int(rng.integers(1, 6))  # spread 1..5 cents
    mid_c = int(round(100 * p))
    bid = int(np.clip(mid_c - s // 2, 1, 99 - s))
    return bid, bid + s


def make_world(
    spec: RuleSpec,
    n_games: int,
    *,
    seed: int,
    games_per_date: int = 6,
    players_per_game: int = 10,
    start: date = WINDOW_START,
) -> pl.DataFrame:
    """Contracts with quotes and ladder structure; outcomes unassigned (``y_u`` = 0)."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for gi in range(n_games):
        d = start + timedelta(days=gi // games_per_date)
        game = f"{d:%y%b%d}".upper() + f"T{gi:04d}"
        # player ladders
        for pi in range(players_per_game):
            series = _PLAYER_SERIES[(gi + pi) % len(_PLAYER_SERIES)]
            lo, hi, step = _LAMBDA[series]
            lam = float(rng.uniform(lo, hi))
            event = f"{series}-{game}"
            for j in range(1, 9):
                t = j * step
                p = float(poisson.sf(t - 1, lam))
                if p < 0.005 or p > 0.995:
                    continue
                bid, ask = _quote(rng, p)
                ticker = f"{event}-HOMP{pi:02d}PLAYER{pi}-{t}"
                rows.append(_row(spec, series, ticker, event, game, d, bid, ask, t, pi))
        # game-level ladders (only scored by LONGSHOT; UNDERS filters to props)
        for series, tag, centre, sd in (
            ("KXNBATOTAL", "", 228.0, 14.0),
            ("KXNBATEAMTOTAL", "HOM", 114.0, 9.0),
            ("KXNBATEAMTOTAL", "AWY", 114.0, 9.0),
            ("KXNBASPREAD", "HOM", 0.0, 12.0),
            ("KXNBASPREAD", "AWY", 0.0, 12.0),
        ):
            event = f"{series}-{game}"
            for j in range(-5, 6):
                tv = centre + 4 * j + (float(rng.uniform(-1, 1)) if tag == "" else 0.0)
                p = float(norm.sf((tv - centre) / sd))
                if p < 0.005 or p > 0.995:
                    continue
                bid, ask = _quote(rng, p)
                neg = "M" if tv < 0 else ""
                ticker = (
                    f"{event}-{tag}{neg}{int(round(abs(tv)))}"
                    if tag
                    else f"{event}-{int(round(tv))}"
                )
                rows.append(_row(spec, series, ticker, event, game, d, bid, ask, float(tv), None))
    df = pl.DataFrame(rows)
    df = df.unique(subset=["ticker"], keep="first", maintain_order=True)
    if spec.max_mid_sum is not None:
        df = df.filter((pl.col("bid_c") + pl.col("ask_c")) <= spec.max_mid_sum)
    keep = pl.col("series").is_in(list(spec.all_series))
    return df.filter(keep).with_columns(in_pool=pl.col("series").is_in(list(spec.decision_series)))


def _row(
    spec: RuleSpec,
    series: str,
    ticker: str,
    event: str,
    game: str,
    d: date,
    bid: int,
    ask: int,
    threshold: float,
    pid: int | None,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "series": series,
        "kind": kind_of(series),
        "event": event,
        "game": game,
        "date": d,
        "stat": "combo" if series in COMBO_SERIES else series.removeprefix("KXNBA").lower(),
        "ladder": ladder_key(series, ticker, event),
        "player_id": pid,
        "threshold": float(threshold),
        "bid_c": bid,
        "ask_c": ask,
        "age_min": 15.0,
        "y_u": 0,
        "dnp": False,
        "in_pool": series in spec.decision_series,
    }


def plant_scale(df: pl.DataFrame, fee: PinnedFee, edge: float) -> float:
    """Scale ``k`` on the mid so the pooled E[r_no] over the decision pool equals ``edge``."""
    sub = df.filter(pl.col("in_pool"))
    bid = sub["bid_c"].to_numpy()
    ask = sub["ask_c"].to_numpy()
    series = sub["series"].to_list()
    cost = np.array(
        [
            (100 - b) * CENT + fee.fee_micro(s, 100 - int(b))
            for b, s in zip(bid, series, strict=True)
        ]
    )
    mid = (bid + ask) / 200.0
    # E[r_no] = 1 - k*mid - cost/UNIT  ->  k = (mean(1 - cost) - edge) / mean(mid)
    return float(((1.0 - cost / UNIT).mean() - edge) / mid.mean())


def draw_outcomes(
    df: pl.DataFrame, rng: np.random.Generator, *, scale: float = 1.0, dnp_rate: float = 0.0
) -> pl.DataFrame:
    """Nested ladder outcomes: one ``u`` per ladder, YES iff ``u < min(1, scale * mid)``."""
    ladders = df["ladder"].to_list()
    uniq = {k: i for i, k in enumerate(dict.fromkeys(ladders))}
    u = rng.random(len(uniq))
    lad = np.array([uniq[k] for k in ladders])
    mid = (df["bid_c"].to_numpy() + df["ask_c"].to_numpy()) / 200.0
    yes = u[lad] < np.minimum(1.0, scale * mid)
    y_u = np.where(yes, UNIT, 0).astype(np.int64)
    dnp = np.zeros(len(df), dtype=bool)
    if dnp_rate > 0:
        dnp = rng.random(len(df)) < dnp_rate
        y_u = np.where(dnp, 160_000, y_u)
    return df.with_columns(
        pl.Series("y_u", y_u, dtype=pl.Int64), pl.Series("dnp", dnp, dtype=pl.Boolean)
    )


def world_universe(df: pl.DataFrame) -> Universe:
    """Wrap a synthetic frame as a Universe with full capture and settlement."""
    cov: dict[date, tuple[int, int]] = {}
    for d, n in Counter(df["date"].to_list()).items():
        cov[d] = (n, n)
    return Universe(
        df=df,
        counts=Counter(),
        game_counts=Counter(),
        coverage_by_date=cov,
        scalar_total=int(df["dnp"].sum()),
        scalar_missing=[],
        null_player_props=[],
        unmatched_games=[],
        n_dnp=int(df["dnp"].sum()),
    )
