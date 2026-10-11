"""Fixture world for the LLM shadow tests: one 2026-27 slate game at 7:30pm ET, production T-60
rows, a facts store with planted post-tip facts, T-60 DK/Pinnacle lines, and box history in
season 2024. No network, no real database, season 2025 never loaded."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from scipy.stats import norm

from nba.daily.store import ForwardPrediction, append_predictions
from nba.odds.live_close import DDL as ODDS_DDL
from nba.shadow_llm.config import ShadowConfig
from tests.fixtures.loader import build_fixture_db

GAME = "0022600099"
T1, T2 = 1610612738, 1610612755
TIP = datetime(2026, 10, 28, 23, 30)  # naive UTC == 7:30pm ET
NOW = TIP - timedelta(minutes=65)
PROD_AT = TIP - timedelta(minutes=70)
FACTS_SCHEMA = Path(__file__).resolve().parents[2] / "nba" / "facts" / "schema.sql"

# (player, team, minutes); player 22 has a DK line but NO production row
PLAYERS = {11: (T1, 34.0), 12: (T1, 28.0), 13: (T1, 20.0), 21: (T2, 33.0), 22: (T2, 15.0)}
MEANS = {"pts": 18.0, "reb": 6.0, "ast": 4.0, "fg3m": 1.5}
STDS = {"pts": 6.0, "reb": 2.5, "ast": 2.0, "fg3m": 1.2}


def p_ge_full(mean: float, std: float) -> dict[str, Any]:
    c = []
    for k in range(1, 80):
        v = int(round(float(norm.sf(k - 0.5, mean, std)) * 10000))
        c.append(v)
        if v <= 10:
            break
    return {"n": 10000, "c": c}


def prop_pred(stat: str, minutes: float) -> dict[str, Any]:
    return {
        "mean": MEANS[stat],
        "std": STDS[stat],
        "p_ge_full": p_ge_full(MEANS[stat], STDS[stat]),
        "proj_minutes": minutes,
        "p_play": 0.95,
        "fallback_reason": None,
    }


@dataclass
class World:
    paths: dict[str, Path]
    cfg: ShadowConfig
    tmp: Path


def _insert_odds(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> None:
    cols = [
        "source", "game_id", "slate_date", "snapshot_kind", "requested_at", "received_at", "tipoff",
        "commence_time", "event_id", "book", "market", "outcome_name", "player_name", "player_id",
        "side", "point", "price_american", "implied_prob_raw", "implied_prob", "last_update",
        "raw_file",
    ]  # fmt: skip
    for r in rows:
        base: dict[str, Any] = {
            "source": "t", "game_id": GAME, "slate_date": date(2026, 10, 28),
            "snapshot_kind": "t60", "requested_at": NOW, "received_at": NOW - timedelta(seconds=30),
            "tipoff": TIP, "commence_time": TIP, "event_id": "e", "outcome_name": "x",
            "price_american": -110, "implied_prob_raw": 0.52, "implied_prob": 0.5,
            "last_update": None, "raw_file": "f",
        }  # fmt: skip
        base.update(r)
        con.execute(
            f"INSERT INTO live_book_odds VALUES ({','.join('?' * len(cols))})",
            [base.get(c) for c in cols],
        )


def make_world(tmp: Path) -> World:
    nba_path = tmp / "nba.duckdb"
    con = build_fixture_db(nba_path)
    # box history: 12 games of season 2024 (calendar 2025-01), 2 days apart
    for i in range(12):
        gid = f"00224{i:05d}"
        d = date(2025, 1, 2) + timedelta(days=2 * i)
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES (?,?,?,?,?,?,?)",
            [gid, d, 2024, T1, T2, 100 + i, 95],
        )
        for pid, (team, mins) in PLAYERS.items():
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, "
                "ast, fg3m, starter) VALUES (?,?,?,?,?,?,?,?,?)",
                [gid, pid, team, mins, 10 + i + pid % 5, 4, 3, 1, pid in (11, 21)],
            )
        for team in (T1, T2):
            con.execute(
                "INSERT INTO team_game_advanced (game_id, team_id, pace, off_rating, def_rating,"
                " net_rating) VALUES (?,?,?,?,?,?)",
                [gid, team, 98.5 + i * 0.1, 112.0, 110.0, 2.0],
            )
    preds: list[ForwardPrediction] = [
        ForwardPrediction(
            GAME, TIP, "rung0_injury_elo", "v2", "win_prob_home",
            {"p_home": 0.61, "p_mov_elo": 0.58, "d_out": 1.0, "d_doubt": 0.0, "home_team": T1,
             "away_team": T2, "fallback_reason": None, "report_as_of_et": "2026-10-28 12:30:00"},
        ),
        ForwardPrediction(
            GAME, TIP, "rung0_mov_elo", "v1", "win_prob_home", {"p_home": 0.58}
        ),
    ]  # fmt: skip
    for pid, (_team, mins) in PLAYERS.items():
        for stat in MEANS:
            preds.append(
                ForwardPrediction(
                    GAME,
                    TIP,
                    "props_context_residual",
                    "ctxres-v1",
                    stat,
                    prop_pred(stat, mins),
                    player_id=pid,
                )
            )
    # player 22 has no production row (excluded from legs): drop its rows
    preds = [p for p in preds if p.player_id != 22]
    append_predictions(con, "run-prod", PROD_AT, preds)
    con.close()

    facts_path = tmp / "facts.duckdb"
    fc = duckdb.connect(str(facts_path))
    fc.execute(FACTS_SCHEMA.read_text())
    g = f"game:{GAME}"
    known = datetime(2026, 10, 1)
    rows = [
        (1, g, "tips_at", None, None, TIP.isoformat(), known, TIP, None, "schedule", 1.0),
        (2, g, "home_team", f"team:{T1}", None, None, known, None, None, "schedule", 1.0),
        (3, g, "away_team", f"team:{T2}", None, None, known, None, None, "schedule", 1.0),
        (4, "player:11", "listed_status", g, None, "available", NOW - timedelta(hours=1), None,
         None, "injury_report", 1.0),
        (5, "player:12", "listed_status", g, None, "questionable", NOW - timedelta(hours=1), None,
         None, "injury_report", 0.8),
        # planted: known after t (pre-tip) and after tip
        (6, "player:11", "listed_status", g, None, "out", NOW + timedelta(minutes=1), None, None,
         "injury_report", 1.0),
        (7, "player:12", "listed_status", g, None, "out", TIP + timedelta(minutes=5), None, None,
         "injury_report", 1.0),
        (8, "player:11", "played_minutes", g, 31.0, None, TIP + timedelta(hours=3), None, None,
         "box", 1.0),
    ]  # fmt: skip
    for r in rows:
        fc.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", list(r))
    fc.close()

    odds_path = tmp / "market_asof.duckdb"
    oc = duckdb.connect(str(odds_path))
    oc.execute(ODDS_DDL)
    odds: list[dict[str, Any]] = []

    def prop(book: str, market: str, pid: int, name: str, point: float, p: float = 0.5) -> None:
        for side in ("over", "under"):
            odds.append(
                dict(book=book, market=market, player_id=pid, player_name=name, side=side,
                     point=point, implied_prob=p if side == "over" else 1 - p)
            )  # fmt: skip

    prop("draftkings", "player_points", 11, "Player Eleven", 20.5)
    prop("draftkings", "player_points", 11, "Player Eleven", 24.5, 0.3)  # alt line, not the main
    prop("pinnacle", "player_points", 11, "Player Eleven", 21.5)
    prop("draftkings", "player_rebounds", 11, "Player Eleven", 6.5)
    prop("draftkings", "player_points", 12, "Player Twelve", 15.5)
    prop("draftkings", "player_assists", 13, "Player Thirteen", 5.0)  # integer line: not sent
    prop("draftkings", "player_points", 21, "Player TwentyOne", 17.5)
    prop("draftkings", "player_points", 22, "Player TwentyTwo", 10.5)  # no production row
    for market, side, point in (("spreads", "home", -3.5), ("totals", "over", 224.5)):
        odds.append(dict(book="draftkings", market=market, player_id=None, player_name=None,
                         side=side, point=point))  # fmt: skip
    odds.append(dict(book="draftkings", market="h2h", player_id=None, player_name=None, side="home",
                     point=None))  # fmt: skip
    _insert_odds(oc, odds)
    oc.close()

    paths = {
        "nba": nba_path,
        "forward": nba_path,
        "facts": facts_path,
        "market": odds_path,
        "shadow": tmp / "shadow" / "llm_shadow.duckdb",
    }
    cfg = replace(
        ShadowConfig(),
        monthly_cap_usd=50.0,
        lock_dir=tmp / "lock",
        db_wait_s=1.0,
        window_min=(60, 70),
    )
    return World(paths, cfg, tmp)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)
