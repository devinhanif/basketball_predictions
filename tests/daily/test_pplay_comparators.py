"""Comparators C and C' in the P(play) shadow: feature sets, refit, scoring, parity check."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from nba.daily.pplay_shadow import features as F
from nba.daily.pplay_shadow import model as M
from nba.daily.pplay_shadow.settle import PARITY_FLOOR, parity_check, settle_pplay_shadow
from nba.daily.pplay_shadow.store import ensure_tables
from tests.daily.conftest import RUN_DATE
from tests.daily.test_pplay_shadow import GID, _run


def test_feature_sets_are_nested_and_match_the_holdout_harness() -> None:
    assert F.FEATURES[: len(F.CP_FEATURES)] == F.CP_FEATURES  # C subset C' subset R, frozen order
    assert F.CP_FEATURES[: len(F.C_FEATURES)] == F.C_FEATURES
    assert len(F.CP_FEATURES) - len(F.C_FEATURES) == 13
    assert len(F.FEATURES) - len(F.CP_FEATURES) == 5
    from research.eval import recent_role_eval as RR

    arms = RR.arm_columns()
    assert arms["base"] == F.C_FEATURES
    assert arms["Cp"] == F.CP_FEATURES
    assert arms["R"] == F.FEATURES


def test_comparators_are_written_with_r_and_null_where_r_has_no_row(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _run(con)
    rows = con.execute(
        "SELECT player_id, p_play_r, p_play_c, p_play_cp FROM forward_pplay_shadow"
    ).fetchall()
    assert rows
    for _pid, r, c, cp in rows:
        if r is None:
            assert c is None and cp is None
        else:
            assert 0.0 < c < 1.0 and 0.0 < cp < 1.0
    assert any(r is not None for _p, r, _c, _cp in rows)
    assert M.COMPARATOR_FEATURES == {"c": F.C_FEATURES, "cp": F.CP_FEATURES}


def test_settle_scores_the_comparators(con: duckdb.DuckDBPyConnection) -> None:
    _run(con)
    con.execute("INSERT INTO games (game_id, game_date, season, home_team, away_team, "
                "home_pts, away_pts) VALUES (?, ?, 2026, 1, 2, 100, 90) ON CONFLICT DO NOTHING",
                [GID, RUN_DATE])  # fmt: skip
    out = settle_pplay_shadow(con, datetime(2027, 1, 1))
    assert isinstance(out, dict)
    got = con.execute(
        "SELECT status, ll_r, ll_c, ll_cp FROM forward_pplay_scores WHERE status = 'scored'"
    ).fetchall()
    for _s, ll_r, ll_c, ll_cp in got:
        assert (ll_r is None) == (ll_c is None) == (ll_cp is None)


def test_old_tables_are_migrated() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE forward_pplay_shadow (run_id VARCHAR)")
    c.execute("CREATE TABLE forward_pplay_scores (game_id VARCHAR)")
    ensure_tables(c)
    sc = {r[0] for r in c.execute("DESCRIBE forward_pplay_scores").fetchall()}
    sh = {r[0] for r in c.execute("DESCRIBE forward_pplay_shadow").fetchall()}
    assert {"ll_c", "ll_cp", "p_play_c", "p_play_cp"} <= sc
    assert {"p_play_c", "p_play_cp"} <= sh


def _scores(rows: list[tuple[date, float, float]]) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    ensure_tables(c)
    for i, (d, ll_prod, ll_r) in enumerate(rows):
        c.execute(
            "INSERT INTO forward_pplay_scores "
            "(game_id, game_date, player_id, ll_prod, ll_r, status) "
            "VALUES (?, ?, ?, ?, ?, 'scored')",
            [f"g{i}", d, i, ll_prod, ll_r],
        )
    return c


def test_parity_check_flags_a_weak_trailing_week() -> None:
    last = date(2026, 11, 20)
    good = [(last - timedelta(days=i % 7), 0.60, 0.50) for i in range(300)]  # R better by 0.10
    status, line = parity_check(_scores(good))
    assert status == "ok" and line and "PARITY WARNING" not in line
    bad = [(last - timedelta(days=i % 7), 0.60, 0.58) for i in range(300)]  # only -0.02
    status, line = parity_check(_scores(bad))
    assert status == "fault" and line and line.startswith("- PARITY WARNING")
    # an old bad week outside the trailing 7 days does not count
    old = [(last - timedelta(days=30), 0.60, 0.59) for _ in range(300)]
    status, _ = parity_check(_scores(good + old))
    assert status == "ok"
    assert parity_check(_scores(bad[:50]))[0] == "insufficient"
    assert parity_check(_scores([]))[0] == "no_data"
    assert PARITY_FLOOR == -0.05


def test_parity_line_reaches_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from nba.daily.report import _pplay_shadow_section

    last = date(2026, 11, 20)
    c = _scores([(last - timedelta(days=i % 7), 0.60, 0.58) for i in range(300)])
    c.execute("CREATE TABLE IF NOT EXISTS forward_pplay_shadow_x (a INT)")
    c.execute("INSERT INTO forward_pplay_shadow (run_id, game_id, player_id) VALUES ('r', 'g', 1)")
    text = "\n".join(_pplay_shadow_section(c))
    assert "PARITY WARNING" in text
