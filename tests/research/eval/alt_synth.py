"""Synthetic data root for the ALT_LINE_70 / ALT_PARLAY_X harness tests: model rows with a full
survival vector and a realised stat drawn from it, tip tables, and an odds store with BetRivers
ladders at ``t60`` and ``t5`` (plus a Unibet copy and a one-line DraftKings quote)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

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


ALT_MARKET = {
    "pts": "player_points_alternate",
    "reb": "player_rebounds_alternate",
    "ast": "player_assists_alternate",
    "fg3m": "player_threes_alternate",
}


def build_alt_root(root: Path, n_games: int = 40, seed: int = 11, with_y: bool = True) -> None:
    """``build_root`` plus the pieces the ALT_LADDER_HIST harness reads: alternate over ladders at
    ``t60`` for DraftKings (over only) and FanDuel (both sides) with ``last_update``, main-market
    ``t60`` lines for both, ``nba.duckdb`` (games and report stamps)."""
    build_root(root, n_games=n_games, with_y=with_y, seed=seed)
    rng = np.random.default_rng(seed + 1)
    rows: list[tuple[object, ...]] = []
    games, stamps = [], []
    for season in SEASONS:
        m = pl.read_parquet(root / f"reports/odds_history/model_rows_{season}.parquet")
        tips = pl.read_parquet(root / f"data/ops/model_clv_pairs/pairs_{season}.parquet")
        tip_of = dict(zip(tips["game_id"], tips["tipoff_utc"], strict=True))
        for gi, game in enumerate(tips["game_id"]):
            tip = tip_of[game]
            games.append((game, season))
            et = (
                tip.replace(tzinfo=dt.UTC)
                .astimezone(ZoneInfo("America/New_York"))
                .replace(tzinfo=None)
            )
            # every tenth game has a report stamp between the snapshot and the cutoff (window game)
            stamp = et - dt.timedelta(minutes=62) if gi % 10 == 0 else et.replace(hour=11, minute=0)
            stamps.append((game, stamp))
        for r in m.iter_rows(named=True):
            surv = np.array(r["p_ge"])
            mn = int(np.argmin(np.abs(surv - 0.5))) + 1
            snap = tip_of[r["game_id"]] - dt.timedelta(minutes=64, seconds=21)
            upd = snap - dt.timedelta(seconds=30)
            base = (season, r["game_id"], "t60", snap)
            for book in ("draftkings", "fanduel"):
                rows.append(
                    (*base, upd, book, MARKET[r["stat"]], r["player_id"], "over", mn - 0.5, -110)
                )
            for k in range(max(1, mn - 6), min(len(surv), mn + 5) + 1):
                p = float(surv[k - 1]) + float(rng.normal(0, 0.02))
                over = _american(p * 1.06)
                under = _american((1 - p) * 1.06)
                mk, pid = ALT_MARKET[r["stat"]], r["player_id"]
                rows.append((*base, upd, "draftkings", mk, pid, "over", k - 0.5, over))
                rows.append((*base, upd, "fanduel", mk, pid, "over", k - 0.5, over))
                rows.append((*base, upd, "fanduel", mk, pid, "under", k - 0.5, under))
    con = duckdb.connect(str(root / "data/odds/odds_history.duckdb"))
    con.execute("alter table odds_history add column last_update TIMESTAMP")
    con.executemany(
        "insert into odds_history (season, game_id, snapshot_kind, snapshot_ts, last_update, "
        "book, market, player_id, side, point, price_american) values (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.close()
    nba = duckdb.connect(str(root / "nba.duckdb"))
    nba.execute("create table games (game_id VARCHAR, season INTEGER)")
    nba.executemany("insert into games values (?,?)", games)
    nba.execute("create table player_availability (game_id VARCHAR, as_of TIMESTAMP)")
    nba.executemany("insert into player_availability values (?,?)", stamps)
    nba.close()
