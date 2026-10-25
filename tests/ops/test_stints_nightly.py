"""nba.ops.stints_nightly: nightly stints + possession lineups for final 2026 games (fixtures)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.cache import cache_path_for
from nba.ops import stints_nightly as sn
from tests.parse.helpers import AWAY, HOME, PbpBuilder

TODAY = date(2026, 10, 25)
G1, G2, G_TODAY, G_LIVE, G_2025 = (
    "0022600001",
    "0022600002",
    "0022600090",
    "0022600091",
    "0022500001",
)
#: minutes the tracker credits in the synthetic game (2 periods of 12; P4 out at 6:00 of Q1)
MINUTES = {
    1: 24.0,
    2: 24.0,
    3: 24.0,
    4: 6.0,
    5: 24.0,
    6: 18.0,
    11: 24.0,
    12: 24.0,
    13: 24.0,
    14: 24.0,
    15: 24.0,
}


def _pbp(game_id: str) -> pl.DataFrame:
    b = PbpBuilder(game_id)
    for period in (1, 2):
        for i, (team, pid) in enumerate([(HOME, 1), (AWAY, 11), (HOME, 2), (AWAY, 12), (HOME, 3)]):
            b.add(
                period,
                f"PT11M{50 - 5 * i}.00S",
                team,
                pid,
                "Made Shot",
                sub_type="Jump Shot",
                description=f"P{pid} Jump Shot (2 PTS)",
                player_name=f"P{pid}",
                shot_value=2,
                shot_distance=15,
                is_field_goal=1,
                location="h" if team == HOME else "v",
            )
    b.add(
        1,
        "PT06M00.00S",
        HOME,
        4,
        "Substitution",
        description="SUB: Bench6 FOR P4",
        player_name="P4",
    )
    return b.df()


def _add_game(
    con: duckdb.DuckDBPyConnection,
    gid: str,
    day: str,
    *,
    season: int = 2026,
    scored: bool = True,
    skew: float = 0.0,
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES (?, DATE '{day}', ?, ?, ?, {'10, 6' if scored else 'NULL, NULL'})",
        [gid, season, HOME, AWAY],
    )
    for team, starters, bench in ((HOME, [1, 2, 3, 4, 5], 6), (AWAY, [11, 12, 13, 14, 15], None)):
        for pid in starters + ([bench] if bench else []):
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, starter, "
                "fga, fta, oreb, tov) VALUES (?, ?, ?, ?, ?, 1, 0, 0, 0)",
                [gid, pid, team, MINUTES[pid] + skew, pid in starters],
            )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # the synthetic game is 24 minutes long: lower the final-game floor (one test restores 239.5)
    monkeypatch.setattr(sn, "MIN_TEAM_MINUTES", 100.0)
    db = tmp_path / "nba.duckdb"
    con = connect(db)
    _add_game(con, G1, "2026-10-23")
    _add_game(con, G2, "2026-10-24")
    _add_game(con, G_TODAY, "2026-10-25")  # today ET: not final yet
    _add_game(con, G_LIVE, "2026-10-24", scored=False)
    _add_game(con, G_2025, "2026-04-01", season=2025)
    con.close()
    calls: list[str] = []

    def fetch(_con: Any, gid: str, *, data_dir: Path, rate_limiter: Any) -> None:
        calls.append(gid)
        p = cache_path_for("pbp", gid, data_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        _pbp(gid).write_parquet(p)

    return {
        "db": db,
        "data": tmp_path / "data",
        "log": tmp_path / "recon.jsonl",
        "fetch": fetch,
        "calls": calls,
    }


def _run(env: dict[str, Any], **kw: Any) -> sn.Result:
    args: dict[str, Any] = {
        "today": TODAY,
        "since": sn.SEASON_START,
        "recon_log": env["log"],
        "fetch": env["fetch"],
        "rate_limit_s": 0.0,
    }
    args.update(kw)
    return sn.run_nightly(env["db"], env["data"], **args)


def _count(env: dict[str, Any], sql: str) -> int:
    con = duckdb.connect(str(env["db"]), read_only=True)
    try:
        return int(con.execute(sql).fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


def test_builds_only_scored_final_2026_games(env: dict[str, Any]) -> None:
    res = _run(env)
    assert res.rc == 0 and res.built == [G1, G2] and not res.failed
    assert sorted(env["calls"]) == [G1, G2]  # not today's game, not the unscored one, not 2025
    assert (
        _count(env, f"SELECT count(DISTINCT game_id) FROM stints WHERE game_id IN ('{G1}','{G2}')")
        == 2
    )
    assert (
        _count(
            env, f"SELECT count(*) FROM stints WHERE game_id IN ('{G_TODAY}','{G_LIVE}','{G_2025}')"
        )
        == 0
    )
    assert _count(env, f"SELECT count(*) FROM possessions WHERE game_id = '{G_TODAY}'") == 0
    # the possession lineup columns come from the same tracker run
    assert (
        _count(
            env, f"SELECT count(*) FROM possessions WHERE game_id = '{G1}' AND off_players IS NULL"
        )
        == 0
    )
    assert _count(env, f"SELECT count(*) FROM possessions WHERE game_id = '{G1}'") > 0


def test_reconciliation_share_is_logged_per_night(env: dict[str, Any]) -> None:
    res = _run(env)
    assert [n.game_date for n in res.nights] == ["2026-10-23", "2026-10-24"]
    assert all(n.share == 1.0 and n.player_games == 11 for n in res.nights)
    rows = [json.loads(x) for x in env["log"].read_text().splitlines()]
    assert [r["game_date"] for r in rows] == ["2026-10-23", "2026-10-24"]
    assert rows[0]["share"] == 1.0 and rows[0]["alert_below"] == 0.95 and rows[0]["built_now"] == 1


def test_second_run_is_idempotent(env: dict[str, Any]) -> None:
    _run(env)
    before = (
        _count(env, "SELECT count(*) FROM stints"),
        _count(env, "SELECT count(*) FROM possessions"),
    )
    env["calls"].clear()
    res = _run(env)
    assert res.rc == 0 and res.built == [] and res.already == 2 and env["calls"] == []
    assert (
        _count(env, "SELECT count(*) FROM stints"),
        _count(env, "SELECT count(*) FROM possessions"),
    ) == before
    assert len(env["log"].read_text().splitlines()) == 2  # rows keyed by date, not appended twice
    assert [n.share for n in res.nights] == [1.0, 1.0]  # still reconciled on the re-run


def test_alert_when_a_built_night_is_under_95_percent(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sn, "MIN_NIGHT_PLAYER_GAMES", 5)  # judge each fixture night alone
    con = connect(env["db"])
    con.execute("UPDATE player_game_stats SET minutes = minutes + 5 WHERE game_id = ?", [G2])
    con.close()
    res = _run(env)
    assert res.rc == sn.RC_RECONCILE_LOW and res.low_nights == ["2026-10-24"]
    assert not res.pooled_low
    # an unchanged night does not re-alert on the next run (the row stays in the log)
    res2 = _run(env)
    assert res2.rc == 0 and res2.low_nights == []
    low = [n for n in res2.nights if n.game_date == "2026-10-24"][0]
    assert low.share < sn.ALERT_SHARE


def _night(d: str, n: int, within: int, built: int = 1) -> sn.Night:
    return sn.Night(d, 1, n, within, within / n, built)


def test_small_nights_are_judged_in_the_pool_not_alone() -> None:
    # 10-20 (68, 64) alone is 94.1%; pooled with a perfect 45-player-game night it is 96.5%
    assert sn.low_nights([_night("a", 68, 64), _night("b", 45, 45)]) == ([], False)
    # a night with >= 150 player-games is judged alone
    assert sn.low_nights([_night("a", 200, 180), _night("b", 45, 45)]) == (["a"], False)
    # the pool itself under 95%: every small night built in this run is named
    assert sn.low_nights([_night("a", 68, 60), _night("b", 45, 45)]) == (["a", "b"], True)
    # nothing built now: no alert, whatever the share
    assert sn.low_nights([_night("a", 68, 10, built=0)]) == ([], False)


def test_partial_box_is_not_final(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sn, "MIN_TEAM_MINUTES", 239.5)  # the real floor: fixture boxes sum to 120
    res = _run(env)
    assert res.rc == 4 and res.eligible == 0 and env["calls"] == []


def test_one_team_short_of_240_is_not_final(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sn, "MIN_TEAM_MINUTES", 120.0)
    con = connect(env["db"])
    con.execute(
        "UPDATE player_game_stats SET minutes = 1 WHERE game_id = ? AND team_id = ?", [G1, AWAY]
    )
    con.close()
    assert _run(env).built == [G2]


def test_hold_switch_builds_stints_but_writes_no_possessions(env: dict[str, Any]) -> None:
    res = _run(env, hold_possessions=True)
    assert res.rc == 0 and res.built == [G1, G2] and res.held_possessions
    assert _count(env, "SELECT count(*) FROM stints") > 0
    assert _count(env, "SELECT count(*) FROM possessions") == 0
    assert res.nights and all(n.share == 1.0 for n in res.nights)


def test_default_writes_possessions(env: dict[str, Any]) -> None:
    res = _run(env)
    assert not res.held_possessions and _count(env, "SELECT count(*) FROM possessions") > 0


def test_main_reads_the_hold_env(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sn.HOLD_POSSESSIONS_ENV, "1")
    seen: dict[str, Any] = {}

    def fake(*a: Any, **k: Any) -> sn.Result:
        seen.update(k)
        return sn.Result()

    monkeypatch.setattr(sn, "run_nightly", fake)
    sn.main(["--db", str(env["db"]), "--data-dir", str(env["data"]), "--today", "2026-10-25"])
    assert seen["hold_possessions"] is True


def test_summary_carries_phase_timings(env: dict[str, Any]) -> None:
    res = _run(env)
    assert {"fetch_parse", "compute_write", "reconcile"} <= set(sn.summary(res)["timings_s"])


def test_only_the_empty_concat_valueerror_means_tracker_skipped(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    con = connect(env["db"])
    con.execute("UPDATE player_game_stats SET starter = false WHERE game_id = ?", [G1])
    con.close()
    res = _run(env)
    assert res.rc == 5 and G1 in res.failed and res.built == [G2]
    only_g1 = _run(env, until=date(2026, 10, 23))  # every ready game skipped: polars' empty concat
    assert only_g1.rc == 5 and only_g1.failed[G1] == "tracker skipped"

    def boom(*a: Any, **k: Any) -> None:
        raise ValueError("a real bug")

    monkeypatch.setattr(sn, "compute_all", boom)
    with pytest.raises(ValueError, match="a real bug"):
        _run(env)


def test_one_failed_fetch_does_not_stop_the_other_game(env: dict[str, Any]) -> None:
    real = env["fetch"]

    def flaky(con: Any, gid: str, **kw: Any) -> None:
        if gid == G1:
            raise RuntimeError("timeout")
        real(con, gid, **kw)

    res = _run(env, fetch=flaky)
    assert res.rc == 5 and res.built == [G2] and G1 in res.failed
    # next night's run heals it
    res2 = _run(env)
    assert res2.rc == 0 and res2.built == [G1]


def test_fetch_budget_stops_fetching_but_still_finishes(env: dict[str, Any]) -> None:
    res = _run(env, budget_s=-1.0)
    assert res.rc == 5 and set(res.failed) == {G1, G2} and res.built == []


def test_off_day_is_informational(env: dict[str, Any]) -> None:
    res = _run(env, since=date(2026, 10, 25))
    assert res.rc == 4 and env["calls"] == []


def test_until_bounds_the_backfill(env: dict[str, Any]) -> None:
    res = _run(env, until=date(2026, 10, 23))
    assert res.built == [G1]


def test_dry_run_writes_nothing(env: dict[str, Any]) -> None:
    res = _run(env, dry_run=True)
    assert res.rc == 0 and env["calls"] == [] and not env["log"].exists()
    assert _count(env, "SELECT count(*) FROM stints") == 0


def test_main_uses_et_today_and_lookback(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = sn.main(
        [
            "--db",
            str(env["db"]),
            "--data-dir",
            str(env["data"]),
            "--today",
            "2026-10-25",
            "--recon-log",
            str(env["log"]),
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and f"{G1}" in out and f"{G2}" in out
