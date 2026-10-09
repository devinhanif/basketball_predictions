"""Live-rehearsal hardening: injury-probe negative cache, empty-slate exit, full-roster cap."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl

from nba.daily.injury import MISS_GRACE_MINUTES, SLOT_MINUTES, candidate_slots, pull_latest_report
from nba.daily.pipeline import run_daily
from nba.ingest.cache import RateLimiter
from nba.props.forward import ForwardConfig, predict_slate
from tests.daily.conftest import RUN_DATE, TIPOFF, before_tip, slate_games


def _noop_pull(c: object, slot: datetime, **kw: object) -> None:
    return None


class _Probe:
    def __init__(self, present: str | None = None) -> None:
        self.urls: list[str] = []
        self.present = present

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        return self.present is not None and self.present in url


def _pull(
    con: duckdb.DuckDBPyConnection, now: datetime, tmp: Path, probe: _Probe
) -> datetime | None:
    return pull_latest_report(con, now, data_dir=tmp, probe=probe, pull=_noop_pull, name_index={})


def test_known_missing_slots_are_not_reprobed(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    now = TIPOFF - timedelta(hours=3)
    p1 = _Probe()
    assert _pull(con, now, tmp_path, p1) is None
    n_first = len(p1.urls)
    p2 = _Probe()
    _pull(con, now, tmp_path, p2)  # same instant: only slots too fresh to call missing re-probe
    fresh_slots = MISS_GRACE_MINUTES // SLOT_MINUTES + 1
    assert len(p2.urls) < n_first
    assert len(p2.urls) <= fresh_slots * 3  # <= a few URL variants per slot


def test_newer_slots_are_probed_and_found_after_older_misses_cached(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    now = TIPOFF - timedelta(hours=3)
    _pull(con, now, tmp_path, _Probe())
    later = now + timedelta(hours=1)
    newest = candidate_slots(later)[0]
    probe = _Probe(present=newest.strftime("%Y-%m-%d_%I_%M%p"))
    assert _pull(con, later, tmp_path, probe) == newest


def test_miss_cache_is_keyed_per_run_day(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    now = TIPOFF - timedelta(hours=3)
    _pull(con, now, tmp_path, _Probe())
    p = _Probe()
    _pull(con, now + timedelta(days=1), tmp_path, p)  # a new ET day starts with no known misses
    assert len(p.urls) >= 40


def test_empty_slate_makes_no_injury_or_roster_calls(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    calls: list[str] = []

    def probe(url: str) -> bool:
        calls.append(url)
        return False

    def roster(team: int, season: str) -> pl.DataFrame:
        calls.append(f"roster{team}")
        return pl.DataFrame()

    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda season: [], now=before_tip(), skip_ingest=True,
        probe=probe, roster_source="official", roster_fetch=roster, roster_dir=tmp_path,
        rate_limiter=RateLimiter(0.0), data_dir=tmp_path,
    )  # fmt: skip
    assert calls == [] and s.n_slate == 0


def _official(team: int, n: int, base: int) -> pl.DataFrame:
    return pl.DataFrame({"team_id": [team] * n, "player_id": list(range(base, base + n))})


def test_full_roster_cap_covers_21_and_leaves_existing_rows_unchanged(
    con: duckdb.DuckDBPyConnection,
) -> None:
    g = slate_games()[0]
    frame = pl.DataFrame(
        {"game_id": [g.game_id], "home_team": [g.home_team], "away_team": [g.away_team]}
    )
    off = pl.concat([_official(g.home_team, 21, 7000), _official(g.away_team, 21, 8000)])
    capped = predict_slate(
        con, RUN_DATE, frame, set(), official_roster=off,
        config=ForwardConfig(sim_stats=(), official_max_roster=18),
    )  # fmt: skip
    full = predict_slate(
        con, RUN_DATE, frame, set(), official_roster=off, config=ForwardConfig(sim_stats=())
    )
    assert ForwardConfig().official_max_roster is None
    assert full["player_id"].n_unique() > capped["player_id"].n_unique()  # fixed 18 dropped some
    key = ["game_id", "player_id", "stat"]
    joined = capped.join(full, on=key, suffix="_f")
    assert joined.height == capped.height  # every previously predicted row is still there
    for c in ("mean", "q10", "q50", "q90"):
        if c in capped.columns:
            assert joined[c].to_list() == joined[f"{c}_f"].to_list()
