"""Write-on-change for ``forward_predictions``: fewer rows, identical eligible content."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb
import pytest

from nba.daily import checkpoint as ck
from nba.daily.settle import settle_eligible_pending
from nba.daily.store import (
    ForwardPrediction,
    LeakageError,
    append_predictions,
    append_predictions_ex,
    content_hash,
    ensure_tables,
)
from nba.db.connect import connect
from tests.daily.test_checkpoint import INT, PROD, _pred

TIPS = {"0022600001": datetime(2026, 10, 28, 23, 30), "0022600002": datetime(2026, 10, 29, 1, 0)}
PLAYERS = (11, 12, 13)


def _batch(
    run_t: datetime, epoch: dict[tuple[str, int], int], n_slate: int = 2
) -> list[ForwardPrediction]:
    out: list[ForwardPrediction] = []
    for gid, tip in TIPS.items():
        out.append(
            ForwardPrediction(gid, tip, "rung0_injury_elo", "v1", "win_prob_home",
                              {"p_home": 0.55 + 0.01 * epoch.get((gid, -1), 0),
                               "n_slate_games": n_slate})
        )  # fmt: skip
        for pid in PLAYERS:
            lam = 14.0 + epoch.get((gid, pid), 0)
            for model in (PROD, INT):
                out.append(
                    ForwardPrediction(
                        gid, tip, model, "v1", "pts",
                        _pred(lam, "pts", n_games_with_report=n_slate, n_slate_games=n_slate),
                        pid,
                    )
                )  # fmt: skip
    return out


def _day(con: duckdb.DuckDBPyConnection, dedup: bool) -> list[tuple[int, int]]:
    """Seven runs; the injury snapshot changes player 11 of game 1 at run 3, game 2 at run 5."""
    ensure_tables(con)
    t0 = datetime(2026, 10, 28, 20, 0)
    res = []
    for i in range(7):
        ep: dict[tuple[str, int], int] = {}
        if i >= 3:
            ep[("0022600001", 11)] = 1
        if i >= 5:
            ep[("0022600002", -1)] = 2
            ep[("0022600002", 12)] = 1
        res.append(
            append_predictions_ex(
                con, f"r{i}", t0 + timedelta(minutes=30 * i), _batch(t0, ep, n_slate=2 - i % 2),
                dedup=dedup,
            )
        )  # fmt: skip
    return res


def test_identical_consecutive_runs_write_zero_rows() -> None:
    con = connect(":memory:")
    t0 = datetime(2026, 10, 28, 18, 0)
    first = append_predictions_ex(con, "a", t0, _batch(t0, {}), dedup=True)
    again = append_predictions_ex(con, "b", t0 + timedelta(minutes=30), _batch(t0, {}), dedup=True)
    assert first[0] > 0 and first[1] == 0
    assert again == (0, first[0])  # all deduped; volatile slate counters do not defeat it
    flipped = append_predictions_ex(
        con, "c", t0 + timedelta(minutes=60), _batch(t0, {}, n_slate=1), dedup=True
    )
    assert flipped[0] == 0


def test_changed_snapshot_writes_only_affected_groups() -> None:
    con = connect(":memory:")
    t0 = datetime(2026, 10, 28, 18, 0)
    append_predictions_ex(con, "a", t0, _batch(t0, {}), dedup=True)
    w, d = append_predictions_ex(
        con, "b", t0 + timedelta(minutes=30), _batch(t0, {("0022600001", 11): 1}), dedup=True
    )
    # one player-stat group (PROD + INT arms together) changed; nothing else
    assert w == 2
    assert con.execute(
        "SELECT DISTINCT game_id, player_id, target FROM forward_predictions WHERE run_id = 'b'"
    ).fetchall() == [("0022600001", 11, "pts")]
    assert d == len(_batch(t0, {})) - 2


def test_no_dedup_against_later_rows_or_changed_tipoff() -> None:
    con = connect(":memory:")
    t0 = datetime(2026, 10, 28, 18, 0)
    late = t0 + timedelta(minutes=90)
    append_predictions_ex(con, "late", late, _batch(t0, {}), dedup=True)
    # an earlier made_at arriving after a later one is always written (no ordering assumptions)
    w, _ = append_predictions_ex(con, "early", t0, _batch(t0, {}), dedup=True)
    assert w == len(_batch(t0, {}))
    p = _batch(t0, {})[0]
    moved = ForwardPrediction(
        p.game_id, p.tipoff + timedelta(hours=1), p.model_name, p.version, p.target, p.prediction
    )
    assert content_hash(moved.tipoff, moved.prediction) != content_hash(p.tipoff, p.prediction)
    w2, _ = append_predictions_ex(con, "mv", late + timedelta(minutes=5), [moved], dedup=True)
    assert w2 == 1


def test_leakage_still_enforced_with_dedup() -> None:
    con = connect(":memory:")
    ensure_tables(con)
    p = _batch(datetime(2026, 10, 28), {})[0]
    with pytest.raises(LeakageError):
        append_predictions_ex(con, "x", p.tipoff, [p], dedup=True)
    # even when the row is a duplicate of the stored one
    ok = datetime(2026, 10, 28, 12, 0)
    append_predictions_ex(con, "ok", ok, [p], dedup=True)
    with pytest.raises(LeakageError):
        append_predictions_ex(con, "dup", p.tipoff + timedelta(minutes=1), [p], dedup=True)
    assert con.execute("SELECT count(*) FROM forward_predictions").fetchone() == (1,)


def _settle_world(dedup: bool) -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    runs = _day(con, dedup)
    assert sum(w for w, _ in runs) > 0
    for gid, tip in TIPS.items():
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts,"
            " away_pts) VALUES (?, ?, 2026, 1, 2, 110, 100)",
            [gid, tip.date()],
        )
        for pid in PLAYERS:
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb,"
                " ast, fg3m, starter) VALUES (?,?,1,30,15,5,3,1,true)",
                [gid, pid],
            )
    return con


def _elig(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    cols = [
        r[0]
        for r in con.execute("DESCRIBE forward_scores_elig").fetchall()
        if r[0] not in ("scored_at", "run_id", "made_at")
    ]
    return con.execute(
        f"SELECT {', '.join(cols)} FROM forward_scores_elig "
        "ORDER BY game_id, model_name, target, player_id"
    ).fetchall()


def test_settle_and_checkpoint_identical_with_and_without_dedup() -> None:
    full, ded = _settle_world(False), _settle_world(True)
    n_full = full.execute("SELECT count(*) FROM forward_predictions").fetchone()
    n_ded = ded.execute("SELECT count(*) FROM forward_predictions").fetchone()
    assert n_full and n_ded and n_ded[0] < n_full[0] // 2
    at = datetime(2026, 11, 1)
    assert settle_eligible_pending(full, at) == settle_eligible_pending(ded, at)
    ef, ed = _elig(full), _elig(ded)
    assert ef and ef == ed
    pf, cf = ck.paired_deltas(full, PROD, INT, "pts", season=2026, same_run=True)
    pd_, cd = ck.paired_deltas(ded, PROD, INT, "pts", season=2026, same_run=True)
    assert cf == cd and cf.n_pairs > 0 and cf.run_mismatch == 0
    drop = ["run_arm", "run_cmp", "made_at_arm"]
    assert pf.drop(drop).equals(pd_.drop(drop))
    # settle's latest-per-key (non-eligible) path agrees too
    from nba.daily.settle import settle_pending

    assert settle_pending(full, at) == settle_pending(ded, at)
    q = "SELECT game_id, model_name, target, player_id, pred, y, crps FROM forward_scores"
    q += " ORDER BY ALL"
    assert full.execute(q).fetchall() == ded.execute(q).fetchall()


def test_plain_append_is_unchanged() -> None:
    con = connect(":memory:")
    t0 = datetime(2026, 10, 28, 18, 0)
    n = len(_batch(t0, {}))
    assert append_predictions(con, "a", t0, _batch(t0, {})) == n
    assert append_predictions(con, "b", t0 + timedelta(minutes=1), _batch(t0, {})) == n
