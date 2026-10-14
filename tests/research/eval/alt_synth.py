"""Synthetic data root for the ALT_LINE_70 / ALT_PARLAY_X harness tests: model rows with a full
survival vector and a realised stat drawn from it, tip tables, and an odds store with BetRivers
ladders at ``t60`` and ``t5`` (plus a Unibet copy and a one-line DraftKings quote)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
from scipy.stats import poisson

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
BASE = {"pts": 14.0, "reb": 5.0, "ast": 3.5, "fg3m": 1.6}
K = {"pts": 45, "reb": 22, "ast": 18, "fg3m": 10}
MARKET = {
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "fg3m": "player_threes",
}


def _american(q: float) -> int:
    q = min(max(q, 0.03), 0.97)
    a = -q / (1 - q) * 100 if q >= 0.5 else (1 - q) / q * 100
    a = int(round(a))
    return a if abs(a) >= 100 else (100 if a >= 0 else -100)


def build_root(
    root: Path,
    n_games: int = 24,
    with_y: bool = True,
    seed: int = 7,
    edge: float = 0.0,
) -> None:
    """``edge`` shifts the realised mean away from the model mean (0 = a calibrated model)."""
    rng = np.random.default_rng(seed)
    (root / "reports/odds_history").mkdir(parents=True, exist_ok=True)
    (root / "data/ops/model_clv_pairs").mkdir(parents=True, exist_ok=True)
    (root / "data/odds").mkdir(parents=True, exist_ok=True)
    (root / "reports/prereg_alt_line_70").mkdir(parents=True, exist_ok=True)
    quotes: list[tuple[object, ...]] = []
    shas: dict[str, str] = {}
    for season in SEASONS:
        rows = []
        tips = {}
        for g in range(n_games):
            game = f"00{season}{g:03d}"
            day = dt.date(season, 11, 1) + dt.timedelta(days=g // 3)
            tip = dt.datetime(day.year, day.month, day.day, 23, 0)
            tips[game] = tip
            for j in range(6):
                pid = 1 + j + 10 * (g % 8)
                for st in STATS:
                    mu = BASE[st] * (0.5 + rng.random())
                    ks = np.arange(1, K[st] + 1)
                    surv = poisson.sf(ks - 1, mu)
                    y = float(rng.poisson(mu * (1.0 + edge)))
                    row = {
                        "game_id": game,
                        "player_id": pid,
                        "stat": st,
                        "game_date": day,
                        "minutes": 30.0,
                        "starter10": 1.0 if j < 3 else 0.0,
                        "p_ge": [float(x) for x in surv],
                        "mu": mu,
                    }
                    if with_y:
                        row["y"] = y
                    rows.append(row)
                    m = int(round(mu))
                    for kind, mins in (("t60", 70), ("t5", 9)):
                        snap = tip - dt.timedelta(minutes=mins)
                        for point in np.arange(m - 2.5, m + 2.0, 1.0):
                            if point < 0.5:
                                continue
                            k = int(point + 0.5)
                            p = float(poisson.sf(k - 1, mu)) + float(rng.normal(0, 0.02))
                            po = _american(p * 1.05)
                            pu = _american((1 - p) * 1.05)
                            for book in ("betrivers", "unibet_us"):
                                if book == "unibet_us" and season != 2023:
                                    continue
                                for side, px in (("over", po), ("under", pu)):
                                    quotes.append(
                                        (
                                            season,
                                            game,
                                            kind,
                                            snap,
                                            book,
                                            MARKET[st],
                                            pid,
                                            side,
                                            float(point),
                                            px,
                                        )
                                    )
                        quotes.append(
                            (
                                season,
                                game,
                                kind,
                                snap,
                                "draftkings",
                                MARKET[st],
                                pid,
                                "over",
                                m + 0.5,
                                -110,
                            )
                        )
                        quotes.append(
                            (
                                season,
                                game,
                                kind,
                                snap,
                                "draftkings",
                                MARKET[st],
                                pid,
                                "under",
                                m + 0.5,
                                -110,
                            )
                        )
        df = pl.DataFrame(rows).drop("mu")
        path = root / f"reports/odds_history/model_rows_{season}.parquet"
        df.write_parquet(path)
        tpath = root / f"data/ops/model_clv_pairs/pairs_{season}.parquet"
        pl.DataFrame({"game_id": list(tips), "tipoff_utc": list(tips.values())}).write_parquet(
            tpath
        )
        for p in (path, tpath):
            shas[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    # an unmatched player (no model row): void_or_unmatched counts these BetRivers rungs
    for season in SEASONS:
        for g in range(n_games):
            game = f"00{season}{g:03d}"
            day = dt.date(season, 11, 1) + dt.timedelta(days=g // 3)
            tip = dt.datetime(day.year, day.month, day.day, 23, 0)
            for kind, mins in (("t60", 70), ("t5", 9)):
                snap = tip - dt.timedelta(minutes=mins)
                for side, px in (("over", -150), ("under", 120)):
                    quotes.append(
                        (
                            season,
                            game,
                            kind,
                            snap,
                            "betrivers",
                            "player_points",
                            9999,
                            side,
                            5.5,
                            px,
                        )
                    )
    con = duckdb.connect(str(root / "data/odds/odds_history.duckdb"))
    con.execute(
        "create table odds_history (season INTEGER, game_id VARCHAR, snapshot_kind VARCHAR, "
        "snapshot_ts TIMESTAMP, book VARCHAR, market VARCHAR, player_id BIGINT, side VARCHAR, "
        "point DOUBLE, price_american INTEGER)"
    )
    con.executemany("insert into odds_history values (?,?,?,?,?,?,?,?,?,?)", quotes)
    con.close()
    (root / "reports/prereg_alt_line_70/gate.json").write_text(json.dumps({"input_sha256": shas}))
