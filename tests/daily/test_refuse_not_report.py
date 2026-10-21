"""NEXT_SESSION plan item A (2026-10-20 opening night): the live loop refuses, it does not report.

1. a winner row is graded only with a box score behind the final score;
2. the morning job ingests (games + box scores) before it settles;
3. a forecast for a player the official roster places on a team outside the game is withdrawn;
(4. the export / top_source teams from the schedule cache: tests/parlay/test_top_source_refuse.py).
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import duckdb
import polars as pl

from nba.daily.__main__ import _parser
from nba.daily.pipeline import run_daily
from nba.daily.settle import settle_pending
from nba.ingest.cache import RateLimiter
from nba.ops.exitcodes import RC_DEGRADED
from nba.props.rosters import parse_common_team_roster
from tests.daily.conftest import RUN_DATE, T1, T2, T3, TIPOFF, before_tip, slate_games
from tests.daily.test_daily import GAMES_INS, STATS_INS

SCRIPT = (Path(__file__).resolve().parents[2] / "ops" / "nba_daily.sh").read_text()
G1 = "0022600001"


# -- (1) winner rows need a box score ----------------------------------------------------------


def _run(con: duckdb.DuckDBPyConnection, minutes: int = 120, **kw: object) -> object:
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=before_tip(minutes),
        skip_ingest=True, skip_injury=True, rate_limiter=RateLimiter(0.0), **kw,
    )  # type: ignore[arg-type]  # fmt: skip


def test_winner_not_graded_on_a_final_score_without_a_box_score(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # PHI@NYK 2026-10-20: stored 63-62 (half time) by the last pretip run; the 07:00 settle ran
    # before any ingest. Points alone are not a result: a box score with >= 10 played rows is.
    _run(con)
    con.execute(GAMES_INS + " (?, ?, 2026, ?, ?, 120, 100)", [G1, RUN_DATE, T1, T2])
    late = TIPOFF + timedelta(hours=5)
    assert settle_pending(con, late)["win_scored"] == 0
    for k in range(9):
        con.execute(STATS_INS + " VALUES (?, ?, ?, 20, 4, 2, 1, 0)", [G1, 8000 + k, T1])
    assert settle_pending(con, late)["win_scored"] == 0  # 9 played rows: still thin
    con.execute(STATS_INS + " VALUES (?, 8100, ?, 0, 0, 0, 0, 0)", [G1, T2])  # DNP row, minutes 0
    assert settle_pending(con, late)["win_scored"] == 0
    con.execute(STATS_INS + " VALUES (?, 8101, ?, 15, 3, 1, 1, 0)", [G1, T2])
    assert settle_pending(con, late)["win_scored"] == 1
    n_elig = con.execute(
        "SELECT count(*) FROM forward_scores_elig WHERE game_id=? AND target='win_prob_home'", [G1]
    ).fetchone()
    assert n_elig is not None and n_elig[0] >= 1  # the pre-registered rows follow the same rule


# -- (2) the morning job ingests before it settles ---------------------------------------------


def _morning() -> str:
    m = re.search(r"^\s*morning\)\n(.*?)^\s*;;\n", SCRIPT, re.M | re.S)
    assert m
    return m.group(1)


def test_morning_branch_runs_ingest_before_settle() -> None:
    steps = re.findall(r"^\s*step (\w+) ", _morning(), re.M)
    assert "ingest" in steps, steps
    assert steps.index("ingest") < steps.index("settle")
    ingest = re.search(r"^\s*step ingest (.*)$", _morning(), re.M)
    assert ingest and "nba.daily" in ingest.group(1) and " ingest " in ingest.group(1)


def test_ingest_subcommand_parses_with_a_default_date() -> None:
    assert _parser().parse_args(["ingest"]).date is None
    from datetime import date

    assert _parser().parse_args(["ingest", "--date", "2026-10-21"]).date == date(2026, 10, 21)


def _games_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["0022600777"], "game_date": [RUN_DATE], "season": [2026],
            "home_team": [T1], "away_team": [T3], "home_pts": [101], "away_pts": [99],
        }
    ).with_columns(pl.col("game_date").cast(pl.Date))  # fmt: skip


def test_ingest_subcommand_refreshes_games_then_pulls_missing_boxes(
    con: duckdb.DuckDBPyConnection,
) -> None:
    seen: list[str] = []
    pulled: list[str] = []

    def fetch(season: str) -> pl.DataFrame:
        seen.append(season)
        return _games_frame()

    def pull(c: duckdb.DuckDBPyConnection, gid: str) -> object:
        pulled.append(gid)
        for k in range(10):
            c.execute(STATS_INS + " VALUES (?, ?, ?, 20, 4, 2, 1, 0)", [gid, 9000 + k, T1])
        return None

    from nba.daily.__main__ import run_ingest

    rc = run_ingest(con, RUN_DATE, rate_limiter=RateLimiter(0.0), fetch_games=fetch, pull_box=pull)
    assert rc == 0 and seen == ["2026-27"] and pulled == ["0022600777"]
    row = con.execute("SELECT home_pts FROM games WHERE game_id='0022600777'").fetchone()
    assert row == (101,)


def test_ingest_subcommand_returns_degraded_when_the_fetch_fails(
    con: duckdb.DuckDBPyConnection,
) -> None:
    def down(season: str) -> pl.DataFrame:
        raise RuntimeError("blocked")

    from nba.daily.__main__ import run_ingest

    assert run_ingest(con, RUN_DATE, rate_limiter=RateLimiter(0.0), fetch_games=down) == RC_DEGRADED


# -- (3) withdrawals ---------------------------------------------------------------------------

MOVER = 1001  # T1's rotation player in the fixture history


def _roster_fetch(team: int, season: str) -> pl.DataFrame:
    """Official rosters: the mover has left T1 for T3 (T3 plays game 2, T1 plays game 1)."""
    ids = {T1: [1000, 1002, 1003, 1004, 1005], T3: [MOVER, 3000, 3001, 3002]}.get(
        team, [team % 99991]
    )
    return parse_common_team_roster(pl.DataFrame({"PLAYER_ID": ids, "EXP": ["5"] * len(ids)}), team)


def _mover_rows(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    return con.execute(
        "SELECT DISTINCT game_id, target FROM forward_predictions WHERE player_id = ? "
        "ORDER BY 1, 2",
        [MOVER],
    ).fetchall()


def test_a_misplaced_mover_is_withdrawn_not_scored_and_not_exported(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    # an earlier run (recent-games roster, or the new team's roster not yet fetched) forecast the
    # mover for his old team; forward_predictions is append-only, so that row stays the latest
    _run(con, 180)
    _run(con, roster_source="official", roster_dir=tmp_path / "r", roster_fetch=_roster_fetch)
    stale = [g for g, _ in _mover_rows(con) if g == G1]
    assert stale, "fixture must reproduce the stale row (mover forecast in a T1 game)"
    # game 1 is over: box score with the mover absent (he plays for T3 now)
    con.execute(GAMES_INS + " (?, ?, 2026, ?, ?, 120, 100)", [G1, RUN_DATE, T1, T2])
    for pid in [1000, 1002, 1003, 1004, 1005, 1006, 1007, 1008]:
        con.execute(STATS_INS + " VALUES (?, ?, ?, 25, 8, 3, 2, 1)", [G1, pid, T1])
    for k in range(5):
        con.execute(STATS_INS + " VALUES (?, ?, ?, 25, 8, 3, 2, 1)", [G1, 6000 + k, T2])
    settle_pending(con, TIPOFF + timedelta(days=1))
    scored = con.execute(
        "SELECT count(*) FROM forward_scores WHERE player_id = ? AND game_id = ?", [MOVER, G1]
    ).fetchone()
    assert scored == (0,)  # before the fix: one 'dnp' row per stat
    wd = con.execute(
        "SELECT game_id, model_name, target, player_id, official_team_id, reason "
        "FROM forward_withdrawals WHERE player_id = ? AND game_id = ?",
        [MOVER, G1],
    ).fetchall()
    assert wd and all(w[4] == T3 for w in wd)
    elig = con.execute(
        "SELECT count(*) FROM forward_scores_elig WHERE player_id = ? AND game_id = ?", [MOVER, G1]
    ).fetchone()
    assert elig == (0,)
    # append-only: the forecast rows are still there, and a second run withdraws nothing twice
    assert (G1, "pts") in _mover_rows(con)
    n = con.execute("SELECT count(*) FROM forward_withdrawals").fetchone()
    _run(con, roster_source="official", roster_dir=tmp_path / "r", roster_fetch=_roster_fetch)
    assert con.execute("SELECT count(*) FROM forward_withdrawals").fetchone() == n


def test_a_player_on_no_official_roster_is_not_withdrawn(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def fetch(team: int, season: str) -> pl.DataFrame:  # nobody lists the recent T1 players
        return parse_common_team_roster(
            pl.DataFrame({"PLAYER_ID": [team % 99991], "EXP": ["5"]}), team
        )

    _run(con, 180)
    _run(con, roster_source="official", roster_dir=tmp_path / "r", roster_fetch=fetch)
    assert con.execute("SELECT count(*) FROM forward_withdrawals").fetchone() == (0,)


def test_recent_roster_source_never_withdraws(con: duckdb.DuckDBPyConnection) -> None:
    _run(con)
    assert con.execute("SELECT count(*) FROM forward_withdrawals").fetchone() == (0,)
