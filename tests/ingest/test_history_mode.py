"""History-mode ingest: retargetable DB/cache paths and the per-game circuit breaker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from nba.ingest import __main__ as cli
from nba.ingest.cache import (
    DEFAULT_DATA_DIR,
    CircuitBreaker,
    RateLimiter,
    fetch_cached,
    pull_ids_with_breaker,
)
from nba.ingest.games import upsert_games


def _parse(argv: list[str]) -> Any:
    return cli.build_parser().parse_args(argv)


def test_default_paths_unchanged() -> None:
    db, data = cli.resolve_paths(_parse(["boxscore", "--season", "2023-24"]))
    assert db is None  # None -> nba.duckdb
    assert data == DEFAULT_DATA_DIR


def test_history_flag_and_explicit_overrides(tmp_path: Path) -> None:
    db, data = cli.resolve_paths(_parse(["--history", "pbp", "--season", "2013-14"]))
    assert db == cli.HISTORY_DB and data == cli.HISTORY_DIR
    assert cli.HISTORY_DB.name == "nba_history.duckdb" and Path("nba.duckdb") != cli.HISTORY_DB
    db2, data2 = cli.resolve_paths(
        _parse(
            [
                "--db-path",
                str(tmp_path / "h.duckdb"),
                "--data-dir",
                str(tmp_path),
                "games",
                "--season",
                "2013-14",
            ]
        )
    )
    assert db2 == tmp_path / "h.duckdb" and data2 == tmp_path


def test_breaker_cools_down_then_stops() -> None:
    sleeps: list[float] = []
    b = CircuitBreaker(failures=2, cooldown_s=7.0, max_trips=1, sleep=sleeps.append)
    assert [b.failure(), b.failure()] == [False, False]  # trip 1 -> cooldown
    assert sleeps == [7.0]
    assert [b.failure(), b.failure()] == [False, True]  # trip 2 > max -> stop


def test_loop_isolates_failures_and_stops_on_throttle() -> None:
    con = duckdb.connect(":memory:")
    from nba.db.connect import apply_schema

    apply_schema(con)
    ids = [f"00{i}" for i in range(10)]
    bad = {"003"}

    def pull(gid: str) -> None:
        if gid in bad:
            raise TimeoutError("x")

    out = pull_ids_with_breaker(
        con, "boxscore", ids, pull, breaker=CircuitBreaker(sleep=lambda s: None)
    )
    assert out.failed == ["003"] and out.fetched == 9 and not out.stopped_early

    def dead(gid: str) -> None:
        raise TimeoutError("throttled")

    out2 = pull_ids_with_breaker(
        con,
        "boxscore",
        ids,
        dead,
        breaker=CircuitBreaker(failures=2, max_trips=1, sleep=lambda s: None),
    )
    assert out2.stopped_early and len(out2.failed) == 4 < len(ids)


def test_cli_writes_only_to_target_db_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hist = tmp_path / "hist.duckdb"
    data = tmp_path / "cache"
    from nba.db.connect import connect

    con = connect(hist)
    upsert_games(
        con,
        pl.DataFrame(
            {
                "game_id": ["0021300001", "0021300002"],
                "game_date": [None, None],
                "season": [2013, 2013],
                "home_team": [1610612737, 1610612738],
                "away_team": [1610612738, 1610612737],
                "home_pts": [100, 90],
                "away_pts": [90, 95],
            },
            schema_overrides={"game_date": pl.Date},
        ),
    )
    con.close()

    def fake_pull(con: Any, gid: str, *, data_dir: Path, rate_limiter: RateLimiter | None = None):
        return fetch_cached(
            con,
            "boxscore",
            gid,
            lambda: pl.DataFrame({"game_id": [gid]}),
            data_dir=data_dir,
            rate_limiter=rate_limiter,
            allow_empty=False,
        )

    monkeypatch.setattr(cli, "pull_game_boxscore", fake_pull)
    rc = cli.main(
        ["--db-path", str(hist), "--data-dir", str(data), "boxscore", "--season", "2013-14"]
    )
    assert rc == 0
    assert sorted(p.name for p in (data / "boxscore").glob("*.parquet")) == [
        "0021300001.parquet",
        "0021300002.parquet",
    ]
    con = duckdb.connect(str(hist), read_only=True)
    n = con.execute("SELECT count(*) FROM ingest_log WHERE source='boxscore' AND status='done'")
    assert n.fetchone() == (2,)
    con.close()
    # rerun: nothing refetched
    rc = cli.main(
        ["--db-path", str(hist), "--data-dir", str(data), "boxscore", "--season", "2013-14"]
    )
    assert rc == 0


def test_newest_first_ordering_keeps_playoffs_in_season() -> None:
    from nba.ingest.postgame import order_newest_first

    keys = [
        (k, lambda: pl.DataFrame(), False)
        for k in ["0021300001", "0041300101", "0022100005", "0022100001", "0042100101"]
    ]
    out = [k for k, _, _ in order_newest_first("tracking", keys)]
    assert out == ["0042100101", "0022100005", "0022100001", "0041300101", "0021300001"]
    shots = [("1_2013-14_RS", lambda: pl.DataFrame(), False)]
    assert order_newest_first("shots", shots) == shots


def test_escalating_cooldown_caps_and_resets_on_success() -> None:
    sleeps: list[float] = []
    b = CircuitBreaker(
        failures=1, cooldown_s=600, max_trips=10, sleep=sleeps.append, cooldown_cap_s=3600
    )
    for _ in range(6):
        assert b.failure() is False
    assert sleeps == [600, 1200, 2400, 3600, 3600, 3600]
    b.success()
    b.failure()
    assert sleeps[-1] == 600  # recovered -> escalation restarts


def test_seasons_filter_only_applies_to_game_keyed_sources() -> None:
    from nba.ingest.postgame import filter_seasons

    keys = [(k, lambda: pl.DataFrame(), False) for k in ["0022200001", "0022400001", "0042300001"]]
    assert [k for k, _, _ in filter_seasons("tracking", keys, [2022, 2023])] == [
        "0022200001",
        "0042300001",
    ]
    assert filter_seasons("shots", keys, [2022]) == keys
