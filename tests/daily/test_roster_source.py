"""--roster-source official wiring: fetch once per date, rookies appear, failure falls back."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from nba.daily.pipeline import RunSummary, run_daily
from nba.ingest.cache import RateLimiter
from nba.props.rosters import parse_common_team_roster
from tests.daily.conftest import RUN_DATE, T1, before_tip, slate_games

ROOKIE = 9001


def _run(con: duckdb.DuckDBPyConnection, **kw: object) -> RunSummary:
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=before_tip(),
        skip_ingest=True, skip_injury=True, rate_limiter=RateLimiter(0.0), **kw,
    )  # type: ignore[arg-type]  # fmt: skip


def _fake_fetch(calls: list[int]):  # type: ignore[no-untyped-def]
    def fetch(team: int, season: str) -> pl.DataFrame:
        calls.append(team)
        ids = [ROOKIE, 1000, 1001] if team == T1 else [team % 100000]
        return parse_common_team_roster(
            pl.DataFrame({"PLAYER_ID": ids, "EXP": ["R"] + ["5"] * (len(ids) - 1)}), team
        )

    return fetch


def _pred_players(con: duckdb.DuckDBPyConnection) -> set[int]:
    rows = con.execute(
        "SELECT DISTINCT player_id FROM forward_predictions WHERE player_id IS NOT NULL"
    ).fetchall()
    return {int(r[0]) for r in rows}


def test_official_source_adds_rookie_and_caches_per_date(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    calls: list[int] = []
    s = _run(con, roster_source="official", roster_dir=tmp_path, roster_fetch=_fake_fetch(calls))
    assert len(calls) == 30
    assert "30/30 teams" in s.model_status["roster_source"]
    assert "lack players_static" in s.model_status["roster_source"]  # 9001 has no pedigree row
    assert ROOKIE in _pred_players(con)
    _run(con, roster_source="official", roster_dir=tmp_path, roster_fetch=_fake_fetch(calls))
    assert len(calls) == 30  # same date: served from the cache


def test_default_recent_source_never_fetches_and_excludes_rookie(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    calls: list[int] = []
    s = _run(con, roster_dir=tmp_path, roster_fetch=_fake_fetch(calls))
    assert calls == [] and "roster_source" not in s.model_status
    assert ROOKIE not in _pred_players(con)


def test_failed_roster_fetch_falls_back_to_recent(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("nba.props.rosters.time.sleep", lambda s: None)

    def down(team: int, season: str) -> pl.DataFrame:
        raise RuntimeError("blocked")

    calls: list[int] = []

    def counting_down(team: int, season: str) -> pl.DataFrame:
        calls.append(team)
        return down(team, season)

    s = _run(con, roster_source="official", roster_dir=tmp_path, roster_fetch=counting_down)
    assert len(calls) == 6  # circuit breaker: 2 teams x 3 attempts, then the rest are skipped
    assert "FAILED" in s.model_status["roster_source"]
    assert s.n_predicted_games == 2 and s.n_props_rows > 0
    assert ROOKIE not in _pred_players(con)
