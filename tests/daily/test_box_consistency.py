"""Plan item A, review M2 + M3 (docs/reviews/refuse_not_report_review_2026-10-21.md).

M2: a target (winner or prop) is graded only when the box agrees with the game score: summed
player_game_stats points per team equal games.home_pts / away_pts. It closes a box cached while
the game was in progress and a partial games-feed score after the box is final (OKC@SAS 44-65).
M3: a refusal is counted, printed and exits 4 (fresh) / 5 (a refused game tipped >= 24 h ago).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pytest

from nba.daily import __main__ as cli
from nba.daily.pipeline import run_daily
from nba.daily.settle import refused_games, settle_pending
from nba.ingest.cache import RateLimiter
from nba.ops.exitcodes import RC_DEGRADED, RC_INFORMATIONAL
from tests.daily.conftest import RUN_DATE, T1, T2, T3, T4, TIPOFF, before_tip, slate_games
from tests.daily.test_daily import GAMES_INS, STATS_INS

G1, G2, G3 = "0022600001", "0022600002", "0022600003"
LATE = TIPOFF + timedelta(hours=5)


def _predict(con: duckdb.DuckDBPyConnection) -> None:
    run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=before_tip(120),
        skip_ingest=True, skip_injury=True, rate_limiter=RateLimiter(0.0),
    )  # fmt: skip


def _final(con: duckdb.DuckDBPyConnection, gid: str, home: int, away: int, h: int, a: int) -> None:
    con.execute(GAMES_INS + " (?, ?, 2026, ?, ?, ?, ?)", [gid, RUN_DATE, h, a, home, away])


def _box(con: duckdb.DuckDBPyConnection, gid: str, team: int, pts: list[int | None]) -> None:
    for k, p in enumerate(pts):
        played = 20 if p is not None else 0  # a DNP row has minutes 0 and NULL points
        con.execute(
            STATS_INS + " VALUES (?, ?, ?, ?, ?, 2, 1, 0)",
            [gid, int(gid[-2:]) * 10000 + team % 1000 * 10 + k, team, played, p],
        )


def _split(total: int, n: int, dnp: int = 0) -> list[int | None]:
    """``n`` played scorers summing to ``total``, then ``dnp`` DNP rows (NULL points)."""
    return [total // n + (total % n if k == 0 else 0) for k in range(n)] + [None] * dnp


def _scored(con: duckdb.DuckDBPyConnection, table: str, win: bool) -> int:
    op = "=" if win else "<>"
    row = con.execute(
        f"SELECT count(*) FROM {table} WHERE game_id = ? AND target {op} 'win_prob_home'", [G1]
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_partial_box_scores_no_target_and_an_agreeing_box_scores_all(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _predict(con)
    _final(con, G1, 120, 100, T1, T2)
    _box(con, G1, T1, _split(70, 5))  # home 70 of 120: a box pulled mid-game
    _box(con, G1, T2, _split(100, 6))  # away 100
    out = settle_pending(con, LATE)
    assert out["win_scored"] == 0 and out["prop_scored"] == 0 and out["prop_dnp"] == 0
    for table in ("forward_scores", "forward_scores_elig"):
        assert _scored(con, table, True) == 0 and _scored(con, table, False) == 0, table
    assert out["refused_box_mismatch"] == 1  # one game, however many keys
    con.execute(
        "UPDATE player_game_stats SET pts = pts + 50 WHERE game_id = ? AND team_id = ? "
        "AND player_id = (SELECT min(player_id) FROM player_game_stats WHERE game_id = ? "
        "AND team_id = ?)",
        [G1, T1, G1, T1],
    )  # the re-pulled final box: home sums to 120
    out = settle_pending(con, LATE)
    assert out["win_scored"] == 1 and out["refused_box_mismatch"] == 0
    assert _scored(con, "forward_scores", False) > 0
    assert _scored(con, "forward_scores_elig", True) >= 1


def test_dnp_rows_with_null_points_do_not_break_the_sum(con: duckdb.DuckDBPyConnection) -> None:
    _predict(con)
    _final(con, G1, 100, 90, T1, T2)
    _box(con, G1, T1, _split(100, 5, dnp=2))
    _box(con, G1, T2, _split(90, 6, dnp=1))
    out = settle_pending(con, LATE)
    assert out["win_scored"] == 1 and out["refused_box_mismatch"] == 0


def test_refusal_counts_per_reason_count_distinct_games(con: duckdb.DuckDBPyConnection) -> None:
    _predict(con)
    _final(con, G1, 120, 100, T1, T2)  # box disagrees
    _box(con, G1, T1, _split(70, 5))
    _box(con, G1, T2, _split(100, 6))
    _final(con, G2, 100, 90, T3, T4)  # no box rows at all
    now = TIPOFF + timedelta(hours=8)
    out = settle_pending(con, now)
    assert (out["refused_no_box"], out["refused_thin_box"], out["refused_box_mismatch"]) == (
        1, 0, 1,
    )  # fmt: skip
    assert out["win_scored"] == 0
    _box(con, G2, T3, _split(100, 4, dnp=2))  # agrees with the score, but 8 played rows
    _box(con, G2, T4, _split(90, 4, dnp=2))
    out = settle_pending(con, now)
    assert (out["refused_no_box"], out["refused_thin_box"], out["refused_box_mismatch"]) == (
        0, 1, 1,
    )  # fmt: skip
    assert out["win_scored"] == 0
    assert out["prop_dnp"] > 0  # props of G2 are graded on the agreeing (if thin) box
    assert {(g, r) for g, r, _ in refused_games(con, now)} == {
        (G1, "box_mismatch"), (G2, "thin_box"),
    }  # fmt: skip
    assert out["refused_stale"] == 0  # tipped 8 h / 5 h before: stale only from 24 h
    assert settle_pending(con, TIPOFF + timedelta(hours=30))["refused_stale"] == 2
    # a refused key is never written
    row = con.execute(
        "SELECT count(*) FROM forward_scores WHERE game_id = ? OR "
        "(game_id = ? AND target = 'win_prob_home')",
        [G1, G2],
    ).fetchone()
    assert row == (0,)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _cli(monkeypatch: pytest.MonkeyPatch, con: duckdb.DuckDBPyConnection, now: str) -> int:
    monkeypatch.setattr(cli, "connect_with_retry", lambda *a, **k: con)
    monkeypatch.setattr(cli, "SHADOW_SETTLERS", [])
    return cli.main(["settle", "--now", now])


def test_settle_cli_exit_codes_0_4_5(
    con: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _predict(con)
    assert _cli(monkeypatch, con, iso(TIPOFF)) == 0  # nothing final yet: no refusals
    _final(con, G1, 120, 100, T1, T2)  # final score, no box: tipped 5 h ago
    assert _cli(monkeypatch, con, iso(LATE)) == RC_INFORMATIONAL
    assert f"refused {G1} no_box" in capsys.readouterr().out
    assert _cli(monkeypatch, con, iso(TIPOFF + timedelta(hours=24))) == RC_DEGRADED  # tipped 24 h
    _box(con, G1, T1, _split(120, 5))
    _box(con, G1, T2, _split(100, 5))
    assert _cli(monkeypatch, con, iso(LATE + timedelta(days=2))) == 0  # box agrees: graded
    assert _scored(con, "forward_scores", True) == 1
