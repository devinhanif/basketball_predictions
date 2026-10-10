"""M4: serving uses the training rule.

Per game: latest own snapshot <= min(now, real tip - 60), <= 36 h old."""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import polars as pl

from nba.daily.injury import to_report_dt
from nba.daily.predict import slate_report_outs
from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    serve_pretip_flagged,
)
from nba.ingest.availability import ensure_player_availability_table

SRC = "nba_official_report"
EARLY, LATE, OTHER = "0022600101", "0022600102", "0022600103"
# tips (naive UTC): early 17:00 ET (21:00Z), late 22:00 ET (02:00Z next day); Oct = EDT
TIP_EARLY = datetime(2026, 10, 28, 21, 0)
TIP_LATE = datetime(2026, 10, 29, 2, 0)


def _et(h: int, m: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 10, day, h, m)


def _db(rows: list[tuple[int, datetime, str, str]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE)")
    ensure_player_availability_table(con)
    for pid, as_of, gid, status in rows:
        con.execute(
            "INSERT INTO player_availability VALUES (?,?,?,?,NULL,?,?)",
            [pid, as_of, gid, status, SRC, as_of],
        )
    return con


GAMES = [(EARLY, TIP_EARLY), (LATE, TIP_LATE)]
NOW = datetime(2026, 10, 28, 23, 0)  # 19:00 ET: early game already started


def test_early_game_never_sees_snapshot_after_its_cutoff() -> None:
    con = _db(
        [
            (1, _et(10, 0), EARLY, "out"),  # <= 16:00 cutoff
            (2, _et(17, 30), EARLY, "out"),  # after the early game's tip-60: ignored
            (3, _et(17, 30), LATE, "out"),
        ]
    )
    out = slate_report_outs(con, GAMES, NOW)
    assert out[EARLY] == {1}
    assert out[LATE] == {3}


def test_game_without_qualifying_snapshot_is_absent_not_nobody_out() -> None:
    con = _db(
        [
            (3, _et(17, 30), LATE, "out"),  # newest league-wide snapshot omits EARLY
            (9, _et(18, 30), OTHER, "out"),
        ]
    )
    out = slate_report_outs(con, GAMES, NOW)
    assert EARLY not in out
    assert out[LATE] == {3}


def test_late_game_sees_newest_qualifying_and_status_changes_apply() -> None:
    con = _db(
        [
            (3, _et(11, 0), LATE, "out"),
            (4, _et(11, 0), LATE, "out"),
            (3, _et(20, 0), LATE, "available"),  # newest <= 21:00 cutoff: 3 back in
            (4, _et(20, 0), LATE, "out"),
            (5, _et(21, 30), LATE, "out"),  # after cutoff 21:00
        ]
    )
    assert slate_report_outs(con, GAMES, datetime(2026, 10, 29, 1, 45))[LATE] == {4}


def test_report_with_nobody_out_is_present_and_empty() -> None:
    con = _db([(3, _et(11, 0), LATE, "available")])
    assert slate_report_outs(con, GAMES, NOW) == {LATE: set()}


def test_now_caps_snapshots_and_36h_age_guard() -> None:
    con = _db([(3, _et(20, 0), LATE, "out")])
    early_now = datetime(2026, 10, 28, 22, 0)  # 18:00 ET < 20:00 snapshot
    assert LATE not in slate_report_outs(con, GAMES, early_now)
    stale = _db([(3, _et(1, 0, day=26), LATE, "out")])  # > 36 h before 21:00 ET Oct 28
    assert slate_report_outs(stale, GAMES, NOW) == {}


def test_serving_equals_training_function_when_rules_agree() -> None:
    """Same rows, now unbounded and no age cap -> identical to latest_pretip_flagged."""
    rows = pl.DataFrame(
        {
            "game_id": [EARLY, EARLY, LATE, LATE, LATE],
            "player_id": [1, 2, 3, 4, 5],
            "status": ["out", "out", "out", "doubtful", "out"],
            "as_of": [_et(10), _et(17, 30), _et(11), _et(20), _et(20)],
            "game_date": [_et(0).date()] * 5,
        }
    ).with_columns(pl.col("as_of").cast(pl.Datetime("us")))
    tips = {EARLY: to_report_dt(TIP_EARLY), LATE: to_report_dt(TIP_LATE)}
    cfg = ReportTriggerConfig(statuses=("out",), tip_source="real")
    train_rows = rows.with_columns(
        pl.col("game_id").replace_strict(tips, return_dtype=pl.Datetime("us")).alias("tip_et")
    )
    t_flag, t_used = latest_pretip_flagged(train_rows, cfg)
    s_flag, s_used = serve_pretip_flagged(rows, tips, datetime(2030, 1, 1), cfg)
    assert (s_flag, s_used) == (t_flag, t_used)
    assert s_flag[EARLY] == {1}
    assert s_flag[LATE] == {5}  # the 11:00 snapshot is superseded
    assert s_used[LATE] == _et(20) and to_report_dt(TIP_LATE) - timedelta(minutes=60) > _et(20)


def _legacy(con: duckdb.DuckDBPyConnection, games: list[tuple[str, datetime]], now: datetime):  # type: ignore[no-untyped-def]
    """The pre-M4 league-wide rule, kept only as the byte-identity reference."""
    out: dict[str, set[int]] = {}
    for gid, tip in games:
        cutoff = to_report_dt(min(now, tip - timedelta(minutes=60)))
        (latest,) = con.execute(
            "SELECT max(as_of) FROM player_availability WHERE source = ? AND as_of <= ?",
            [SRC, cutoff],
        ).fetchone()  # type: ignore[misc]
        if latest is None or latest < cutoff - timedelta(hours=36):
            continue
        rows = con.execute(
            "SELECT DISTINCT player_id FROM player_availability WHERE source = ? "
            "AND as_of = ? AND status = 'out'",
            [SRC, latest],
        ).fetchall()
        out[gid] = {int(r[0]) for r in rows}
    return out


def test_identical_to_legacy_when_every_snapshot_covers_every_game() -> None:
    snaps = [_et(9), _et(12), _et(15, 30), _et(20)]
    rows = []
    for k, s in enumerate(snaps):
        for gid in (EARLY, LATE):
            rows.append((10 * k + 1, s, gid, "out"))  # same OUT set on both games
            rows.append((100 + k, s, gid, "available"))
    con = _db(rows)
    for now in (datetime(2026, 10, 28, 20, 0), NOW, datetime(2026, 10, 29, 1, 45)):
        assert slate_report_outs(con, GAMES, now) == _legacy(con, GAMES, now)
