"""Pre-tip roster source: parsing, per-date cache, failure handling, merge rules."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.ingest.cache import RateLimiter
from nba.props.rosters import (
    load_official_rosters,
    merge_rosters,
    parse_common_team_roster,
    proxy_rosters_from_first_games,
    roster_cache_path,
)

FIXTURE = Path(__file__).parent / "fixtures" / "common_team_roster_sample.json"
T1, T2, T3 = 1610612738, 1610612755, 1610612747


def _sample() -> pl.DataFrame:
    raw = json.loads(FIXTURE.read_text())
    return pl.DataFrame(raw["rows"], schema=raw["headers"], orient="row", infer_schema_length=None)


def _no_sleep(_s: float) -> None:
    return None


def test_parse_sample_dedupes_and_keeps_rookie_flag() -> None:
    out = parse_common_team_roster(_sample(), T1)
    assert out.columns == ["team_id", "player_id", "exp", "player_name"]
    assert sorted(out["player_id"].to_list()) == [1000, 9001, 9002]  # duplicate row dropped
    assert set(out["team_id"].to_list()) == {T1}
    assert out.filter(pl.col("player_id") == 9001)["exp"][0] == "R"


def test_parse_requires_player_id() -> None:
    with pytest.raises(ValueError, match="PLAYER_ID"):
        parse_common_team_roster(pl.DataFrame({"x": [1]}), T1)


def test_cache_is_per_date_and_second_call_makes_no_request(tmp_path: Path) -> None:
    calls: list[tuple[int, str]] = []

    def fetch(team: int, season: str) -> pl.DataFrame:
        calls.append((team, season))
        return parse_common_team_roster(_sample(), team)

    lim = RateLimiter(0.0)
    kw = {"cache_root": tmp_path, "fetch": fetch, "rate_limiter": lim, "sleep": _no_sleep}
    d1, d2 = date(2026, 10, 20), date(2026, 10, 21)
    f1, miss = load_official_rosters(d1, "2026-27", [T1, T2], **kw)  # type: ignore[arg-type]
    assert miss == [] and len(calls) == 2 and f1.height == 6
    load_official_rosters(d1, "2026-27", [T1, T2], **kw)  # type: ignore[arg-type]
    assert len(calls) == 2  # cached
    load_official_rosters(d2, "2026-27", [T1], **kw)  # type: ignore[arg-type]
    assert len(calls) == 3  # a new as-of date never reads yesterday's file
    assert roster_cache_path(tmp_path, d1, T1).exists()


def test_fetch_failure_is_reported_not_raised_and_retried(tmp_path: Path) -> None:
    attempts = {"n": 0}

    def flaky(team: int, season: str) -> pl.DataFrame:
        attempts["n"] += 1
        if team == T2:
            raise RuntimeError("boom")
        return parse_common_team_roster(_sample(), team)

    frame, missing = load_official_rosters(
        date(2026, 10, 20), "2026-27", [T1, T2], cache_root=tmp_path, fetch=flaky,
        rate_limiter=RateLimiter(0.0), sleep=_no_sleep,
    )  # fmt: skip
    assert missing == [T2] and set(frame["team_id"].to_list()) == {T1}
    assert attempts["n"] == 1 + 3  # one success + three attempts on the failing team
    assert not roster_cache_path(tmp_path, date(2026, 10, 20), T2).exists()


def test_all_fail_returns_empty_frame(tmp_path: Path) -> None:
    def bad(team: int, season: str) -> pl.DataFrame:
        raise RuntimeError("down")

    frame, missing = load_official_rosters(
        date(2026, 10, 20), "2026-27", [T1], cache_root=tmp_path, fetch=bad,
        rate_limiter=RateLimiter(0.0), sleep=_no_sleep,
    )  # fmt: skip
    assert frame.is_empty() and missing == [T1]


def _frames() -> tuple[pl.DataFrame, pl.DataFrame]:
    recent = pl.DataFrame({"team_id": [T1, T1, T2, T3], "player_id": [1, 2, 3, 4]})
    official = pl.DataFrame({"team_id": [T1, T2, T2], "player_id": [1, 2, 9]})
    return recent, official


def test_merge_official_wins_for_movers_and_union_keeps_unlisted() -> None:
    recent, official = _frames()
    out = merge_rosters(recent, official, [T1, T2], set())
    got = dict(zip(out["player_id"].to_list(), out["team_id"].to_list(), strict=True))
    assert got == {1: T1, 2: T2, 3: T2, 9: T2}  # 2 moved T1 -> T2; 4 is on a non-slate team
    # player 2 appears exactly once
    assert out["player_id"].to_list().count(2) == 1


def test_merge_strict_drops_recent_players_of_teams_with_a_loaded_roster() -> None:
    recent, official = _frames()
    out = merge_rosters(recent, official, [T1, T2, T3], set(), drop_unlisted_recent=True)
    assert 3 not in out["player_id"].to_list()  # T2 has an official roster; 3 is unlisted
    assert 4 in out["player_id"].to_list()  # T3 has no official roster loaded: keep


def test_merge_excludes_out_players() -> None:
    recent, official = _frames()
    out = merge_rosters(recent, official, [T1, T2], {1, 9})
    assert 1 not in out["player_id"].to_list() and 9 not in out["player_id"].to_list()


def test_proxy_uses_first_two_weeks_regular_season_only() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, "
        "home_team INT, away_team INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, "
        "minutes FLOAT)"
    )
    con.execute(
        """INSERT INTO games VALUES
        ('0012400001','2024-10-05',2024,1,2),
        ('0022400001','2024-10-22',2024,1,2),
        ('0022400002','2024-11-01',2024,1,2),
        ('0022400003','2024-11-20',2024,1,2)"""
    )
    con.execute(
        """INSERT INTO player_game_stats VALUES
        ('0012400001',50,1,20),('0022400001',10,1,30),('0022400001',11,1,0),
        ('0022400002',12,1,25),('0022400002',10,2,25),('0022400003',13,1,25)"""
    )
    out = proxy_rosters_from_first_games(con, 2024, 14)
    got = dict(zip(out["player_id"].to_list(), out["team_id"].to_list(), strict=True))
    # 50: preseason, 11: DNP, 13: after the window. 10 keeps the team of his first appearance.
    assert got == {10: 1, 12: 1}


def test_roster_names_feed_the_injury_resolver_for_debutants() -> None:
    """A debutant is in neither the (lagging) static list nor any box score; the official
    roster's name makes him resolvable, and old caches without names are harmless."""
    from nba.ingest.availability import build_name_resolver
    from nba.props.rosters import roster_names
    from tests.fixtures.loader import build_fixture_db

    ro = pl.DataFrame({"team_id": [T1], "player_id": [9999999], "player_name": ["Zed Rookie-Name"]})
    assert roster_names(ro) == [("Zed Rookie-Name", 9999999)]
    assert roster_names(ro.drop("player_name")) == []
    con = build_fixture_db(":memory:")
    res = build_name_resolver(con, names=[], extra_names=roster_names(ro))
    assert res.lookup("Zed Rookie-Name", T1, None) == 9999999
    assert build_name_resolver(con, names=[]).lookup("Zed Rookie-Name", T1, None) is None
