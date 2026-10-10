"""A small synthetic replay + odds database for the explainer tests.

Game ``0022500999``: DAL (away, 1610612742) at HOU (home, 1610612745), 2025-11-03, tip 8:00 PM ET
(2025-11-04 01:00 UTC). Player ids are fake (9000001+) so names come from the books' spelling and
no test depends on the installed ``nba_api`` player list.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest

GAME = "0022500999"
HOU, DAL = 1610612745, 1610612742
TIP_UTC = datetime(2025, 11, 4, 1, 0)
MADE = datetime(2025, 11, 3, 22, 50)  # 5:50 PM ET, before tip - 60 (7:00 PM ET)
LATE = datetime(2025, 11, 4, 0, 30)  # after the T-60 cutoff: must never be shown

P_STAR, P_WING, P_BENCH, P_DNP, P_UNFORECAST = 9000001, 9000002, 9000003, 9000004, 9000005
NAMES = {
    P_STAR: "Alpha Star",
    P_WING: "Bravo Wing",
    P_BENCH: "Charlie Bench",
    P_DNP: "Delta Sitter",
    P_UNFORECAST: "Echo Newcomer",
}


def survival_counts(mean: float, kmax: int = 60, n: int = 10_000) -> dict[str, Any]:
    """``p_ge_full`` from a Poisson(mean) tail, cut like ``full_support._cut``."""
    from math import exp, factorial

    pmf = [exp(-mean) * mean**k / factorial(k) for k in range(kmax)]
    c: list[int] = []
    for k in range(1, kmax):
        p = max(0.0, 1.0 - sum(pmf[:k]))
        c.append(int(round(p * n)))
        if p <= 1e-3:
            break
    return {"n": n, "c": c}


def prop(
    mean: float,
    *,
    q10: float,
    q90: float,
    proj: float = 30.0,
    p_play: float = 0.9,
    recency: float | None = None,
) -> dict[str, Any]:
    grid = list(np.linspace(q10, q90, 19))
    return {
        "mean": mean,
        "mean_recency": recency if recency is not None else mean,
        "std": 4.0,
        "q10": q10,
        "q50": mean,
        "q90": q90,
        "q_grid": grid,
        "p_ge_full": survival_counts(mean),
        "p_play": p_play,
        "proj_minutes": proj,
        "bucket": "smooth",
        "routed_to": "context_residual",
    }


def _ddl(con: Any) -> None:
    con.execute(
        "CREATE TABLE games(game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
        "away_team INTEGER, home_pts INTEGER, away_pts INTEGER, national_tv VARCHAR)"
    )
    con.execute(
        "CREATE TABLE forward_predictions(run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, "
        "tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, "
        "player_id INTEGER, prediction JSON)"
    )
    con.execute(
        "CREATE TABLE forward_scores(scored_at TIMESTAMP, game_id VARCHAR, game_date DATE, "
        "season INTEGER, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INTEGER, "
        "made_at TIMESTAMP, y DOUBLE, pred DOUBLE, log_loss DOUBLE, brier DOUBLE, crps DOUBLE, "
        "status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE player_availability(player_id INTEGER, as_of TIMESTAMP, game_id VARCHAR, "
        "status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE player_game_stats(game_id VARCHAR, player_id INTEGER, team_id INTEGER, "
        "minutes FLOAT, pts INTEGER, reb INTEGER, ast INTEGER, fg3m INTEGER, starter BOOLEAN)"
    )


def build_replay(
    path: Path,
    *,
    home_pts: int = 110,
    away_pts: int = 102,
    with_forecasts: bool = True,
    star_pts: int = 41,
) -> None:
    con = duckdb.connect(str(path))
    _ddl(con)
    con.execute(
        "INSERT INTO games VALUES (?, '2025-11-03', 2025, ?, ?, ?, ?, NULL)",
        [GAME, HOU, DAL, home_pts, away_pts],
    )
    # box score: star (41 pts, above the interval), wing (inside), bench (below), unforecast
    box = [
        (P_STAR, HOU, 36.0, star_pts, 8, 5, 3, True),
        (P_WING, HOU, 30.0, 14, 4, 4, 1, True),
        (P_BENCH, DAL, 20.0, 0, 2, 1, 0, False),
        (P_UNFORECAST, DAL, 12.0, 5, 1, 1, 0, False),
        (P_DNP, HOU, 0.0, 0, 0, 0, 0, False),
    ]
    for r in box:
        con.execute("INSERT INTO player_game_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [GAME, *r])
    # report: stamped 5 PM ET the same day (<= 7 PM ET), plus a later stamp that must be ignored
    for ts, who, status, why in [
        (datetime(2025, 11, 3, 17, 0), P_DNP, "out", "Injury/Illness-RightKnee;Sprain"),
        (datetime(2025, 11, 3, 17, 0), P_WING, "questionable", "Injury/Illness-LeftAnkle;Sprain"),
        (datetime(2025, 11, 3, 19, 30), P_WING, "out", "Injury/Illness-LeftAnkle;Sprain"),
    ]:
        con.execute(
            "INSERT INTO player_availability VALUES (?, ?, ?, ?, ?, 'nba_official_report', ?)",
            [who, ts, GAME, status, why, ts],
        )
    if with_forecasts:
        rows: list[tuple[str, datetime, str, int, dict[str, Any]]] = []

        def add(model: str, made: datetime, target: str, pid: int, pred: dict[str, Any]) -> None:
            rows.append((model, made, target, pid, pred))

        for model, shift in (("props_context_residual", 0.0), ("props_recency_v1", 1.0)):
            for pid, base, proj, pp in (
                (P_STAR, 25.0, 36.0, 0.95),
                (P_WING, 14.0, 30.0, 0.8),
                (P_BENCH, 8.0, 20.0, 0.7),
                (P_DNP, 10.0, 22.0, 0.55),
            ):
                for target, scale in (("pts", 1.0), ("reb", 0.2), ("ast", 0.12), ("fg3m", 0.08)):
                    m = max(0.3, (base + shift) * scale)
                    add(
                        model,
                        MADE,
                        target,
                        pid,
                        prop(
                            m,
                            q10=max(0.0, m * 0.55),
                            q90=m * 1.6,
                            proj=proj,
                            p_play=pp,
                            recency=m - 0.5,
                        ),
                    )
        # a LATE row (after the cutoff) with a wildly different star forecast: never shown
        add("props_context_residual", LATE, "pts", P_STAR, prop(5.0, q10=1.0, q90=9.0))
        for model, p_home in (("rung0_injury_elo", 0.62), ("rung0_mov_elo", 0.55)):
            pred: dict[str, Any] = {
                "p_home": p_home,
                "primary": model == "rung0_injury_elo",
                "fallback_reason": None,
            }
            if model == "rung0_injury_elo":
                pred["p_mov_elo"] = 0.55
            add(model, MADE, "win_prob_home", -1, pred)
        add("rung0_injury_elo", LATE, "win_prob_home", -1, {"p_home": 0.99, "primary": True})
        for i, (model, made, target, pid, pred) in enumerate(rows):
            con.execute(
                "INSERT INTO forward_predictions VALUES (?, ?, ?, ?, ?, 'v', ?, ?, ?)",
                [f"run{i}", made, GAME, TIP_UTC, model, target, pid, json.dumps(pred)],
            )
    con.close()


def build_odds(path: Path, *, with_pinnacle_moneyline: bool = True) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE odds_history(source VARCHAR, season INTEGER, game_id VARCHAR, "
        "event_id VARCHAR, snapshot_kind VARCHAR, requested_at TIMESTAMP, snapshot_ts TIMESTAMP, "
        "book VARCHAR, market VARCHAR, outcome_name VARCHAR, player_name VARCHAR, "
        "player_id BIGINT, side VARCHAR, point DOUBLE, price_american INTEGER, "
        "implied_prob_raw DOUBLE, implied_prob DOUBLE, last_update TIMESTAMP, raw_file VARCHAR)"
    )
    snap = TIP_UTC - timedelta(minutes=65)

    def add(
        book: str,
        market: str,
        side: str,
        raw: float,
        *,
        pid: int | None = None,
        point: float | None = None,
        kind: str = "t60",
    ) -> None:
        con.execute(
            "INSERT INTO odds_history VALUES ('the_odds_api', 2025, ?, 'e', ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, NULL, 'f')",
            [
                GAME,
                kind,
                snap,
                snap,
                book,
                market,
                "x",
                NAMES.get(pid) if pid else None,
                pid,
                side,
                point,
                0,
                raw,
                raw,
            ],
        )

    if with_pinnacle_moneyline:
        add("pinnacle", "h2h", "home", 0.60)
        add("pinnacle", "h2h", "away", 0.44)  # raw sums to 1.04: the vig
    add("draftkings", "h2h", "home", 0.58)
    add("draftkings", "h2h", "away", 0.46)
    # star points: Pinnacle main line 24.5 (even) plus an alternate 29.5; DraftKings 25.5
    add("pinnacle", "player_points", "over", 0.52, pid=P_STAR, point=24.5)
    add("pinnacle", "player_points", "under", 0.52, pid=P_STAR, point=24.5)
    add("pinnacle", "player_points", "over", 0.35, pid=P_STAR, point=29.5)
    add("pinnacle", "player_points", "under", 0.70, pid=P_STAR, point=29.5)
    add("draftkings", "player_points", "over", 0.50, pid=P_STAR, point=25.5)
    add("draftkings", "player_points", "under", 0.54, pid=P_STAR, point=25.5)
    # wing: no Pinnacle, DraftKings only
    add("draftkings", "player_points", "over", 0.50, pid=P_WING, point=13.5)
    add("draftkings", "player_points", "under", 0.54, pid=P_WING, point=13.5)
    # bench: neither of the two preferred books; three others -> consensus median
    for book, pt in (("fanduel", 7.5), ("betmgm", 8.5), ("bovada", 8.5)):
        add(book, "player_points", "over", 0.50, pid=P_BENCH, point=pt)
        add(book, "player_points", "under", 0.54, pid=P_BENCH, point=pt)
    # a T-5 snapshot that must not be used
    add("pinnacle", "player_points", "over", 0.50, pid=P_STAR, point=99.5, kind="t5")
    add("pinnacle", "player_points", "under", 0.50, pid=P_STAR, point=99.5, kind="t5")
    con.close()


@pytest.fixture
def replay_db(tmp_path: Path) -> Path:
    p = tmp_path / "replay.duckdb"
    build_replay(p)
    return p


@pytest.fixture
def odds_db(tmp_path: Path) -> Path:
    p = tmp_path / "odds.duckdb"
    build_odds(p)
    return p


@pytest.fixture
def make_replay(tmp_path: Path) -> Callable[..., Path]:
    def _make(name: str = "r.duckdb", **kw: Any) -> Path:
        p = tmp_path / name
        build_replay(p, **kw)
        return p

    return _make
