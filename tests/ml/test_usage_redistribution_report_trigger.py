"""Report-triggered 2A/2B: leakage rules, rotation as-of, decision rule, e2e plumbing."""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.eval.usage_redistribution_eval import (
    CellResult,
    benjamini_hochberg,
    cluster_bootstrap_p,
    decide_cell,
    run_usage_redistribution_eval,
)
from nba.sim.usage_redistribution import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
    prior_minutes_state,
    rotation_flagged_by_team,
    usable_report_rows,
)

D = dt.date(2023, 12, 1)


def _rows(recs: list[tuple[str, int, str, dt.datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        [(g, p, s, a, D) for g, p, s, a in recs],
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
        },
        orient="row",
    )


def _at(h: int, m: int = 0, days: int = 0) -> dt.datetime:
    return dt.datetime.combine(D, dt.time(h, m)) + dt.timedelta(days=days)


def test_report_after_cutoff_is_ignored() -> None:
    cfg = ReportTriggerConfig()  # cutoff 18:00
    rows = _rows([("g", 1, "out", _at(18, 30)), ("g", 2, "out", _at(20, 0))])
    flagged, used = latest_pretip_flagged(rows, cfg)
    assert flagged == {} and used == {}


def test_future_report_is_ignored_and_latest_prior_wins() -> None:
    cfg = ReportTriggerConfig()
    rows = _rows(
        [
            ("g", 1, "out", _at(11, 45)),
            ("g", 2, "out", _at(11, 45)),
            ("g", 2, "available", _at(17, 45)),  # latest usable: 2 upgraded
            ("g", 3, "out", _at(17, 45)),
            ("g", 4, "out", _at(8, 0, days=1)),  # next-day: future
        ]
    )
    flagged, used = latest_pretip_flagged(rows, cfg)
    assert flagged["g"] == {3}  # 1 is absent from latest snapshot -> not out
    assert used["g"] == _at(17, 45)


def test_status_mapping_out_only_by_default_doubtful_optional() -> None:
    rows = _rows(
        [("g", 1, "out", _at(17)), ("g", 2, "doubtful", _at(17)), ("g", 3, "questionable", _at(17))]
    )
    assert latest_pretip_flagged(rows, ReportTriggerConfig())[0]["g"] == {1}
    both = ReportTriggerConfig(statuses=("out", "doubtful"))
    assert latest_pretip_flagged(rows, both)[0]["g"] == {1, 2}


def test_usable_rows_exactly_lead_before_tip_boundary() -> None:
    cfg = ReportTriggerConfig(tipoff_hour_et=19.0, lead_minutes=60)
    rows = _rows([("g", 1, "out", _at(18, 0)), ("g", 2, "out", _at(18, 1))])
    assert usable_report_rows(rows, cfg)["player_id"].to_list() == [1]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _game(c: duckdb.DuckDBPyConnection, gid: str, day: dt.date, season: int = 2023) -> None:
    c.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, ?, 1, 2, 100, 90)",
        [gid, day, season],
    )


def _pgs(c: duckdb.DuckDBPyConnection, gid: str, pid: int, team: int, minutes: float) -> None:
    c.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, stl, blk, tov, starter) VALUES (?, ?, ?, ?, 10, 5, 3, 1, 1, 0, 1, true)",
        [gid, pid, team, minutes],
    )


def test_rotation_is_as_of_not_future(con: duckdb.DuckDBPyConnection) -> None:
    base = dt.date(2023, 10, 1)
    for i in range(6):  # player 7 plays 10 mpg early -> bench as of D
        _game(con, f"e{i}", base + dt.timedelta(days=i))
        _pgs(con, f"e{i}", 7, 1, 10.0)
        _pgs(con, f"e{i}", 8, 1, 30.0)
    for i in range(30):  # later 7 becomes a 36-min starter (the future)
        _game(con, f"l{i}", D + dt.timedelta(days=1 + i))
        _pgs(con, f"l{i}", 7, 1, 36.0)
    _game(con, "target", D)
    state = prior_minutes_state(con)
    info = {"target": (D, 1, 2)}
    rot = rotation_flagged_by_team({"target": {7, 8, 99}}, info, state)
    assert rot == {("target", 1): {8}}  # 7 bench as-of, 99 unknown


def test_rotation_ignores_player_on_other_team(con: duckdb.DuckDBPyConnection) -> None:
    for i in range(6):
        _game(con, f"e{i}", dt.date(2023, 10, 1) + dt.timedelta(days=i))
        _pgs(con, f"e{i}", 8, 3, 30.0)  # team 3 not in the target game
    _game(con, "t", D)
    rot = rotation_flagged_by_team({"t": {8}}, {"t": (D, 1, 2)}, prior_minutes_state(con))
    assert rot == {}


def test_load_report_rows_filters_source_and_null_game(con: duckdb.DuckDBPyConnection) -> None:
    _game(con, "g", D)
    for src, gid in (
        ("nba_official_report", "g"),
        ("nba_inactive_list", "g"),
        ("nba_official_report", None),
    ):
        con.execute(
            "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
            "VALUES (5, ?, ?, 'out', ?)",
            [_at(12), gid, src],
        )
    df = load_report_rows(con, ReportTriggerConfig())
    assert df.height == 1 and df["status"][0] == "out"


def test_bh_known_values() -> None:
    q = benjamini_hochberg([0.01, 0.04, 0.03, 0.5])
    assert q == pytest.approx([0.04, 0.0533333, 0.0533333, 0.5], abs=1e-6)
    assert benjamini_hochberg([1.0] * 8) == [1.0] * 8


def test_cluster_bootstrap_p_detects_shift_and_null() -> None:
    rng = np.random.default_rng(0)
    clu = np.repeat(np.arange(200), 3)
    assert cluster_bootstrap_p(rng.normal(-0.5, 0.2, 600), clu) < 0.01
    assert cluster_bootstrap_p(rng.normal(0.0, 1.0, 600), clu) > 0.05
    assert cluster_bootstrap_p(np.array([]), np.array([])) == 1.0


def _cell(**kw: float) -> CellResult:
    base = dict(delta=-0.02, ci_hi=-0.01, q_bh=0.01, full_ci_hi=0.001)
    base.update(kw)
    return CellResult("2A", "pts", "pending", **base)  # type: ignore[arg-type]


def test_decision_rule_requires_every_condition() -> None:
    assert decide_cell(_cell()).status == "ship"
    assert decide_cell(_cell(ci_hi=0.001)).status == "no_ship"
    assert decide_cell(_cell(q_bh=0.2)).status == "no_ship"
    assert decide_cell(_cell(delta=-0.004)).status == "no_ship"
    assert decide_cell(_cell(full_ci_hi=0.01)).status == "no_ship"  # dilution regression


def test_holdout_season_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="holdout"):
        run_usage_redistribution_eval(con, max_season=2025)


def test_precondition_failure_runs_no_ab(con: duckdb.DuckDBPyConnection) -> None:
    _game(con, "g", D)
    res = run_usage_redistribution_eval(con)  # no reports at all
    assert not res.preconditions.passed and not res.ran and res.cells == []
    assert "FAIL" in res.summary()


def test_late_only_reports_fail_coverage_not_leak(con: duckdb.DuckDBPyConnection) -> None:
    for i in range(5):
        _game(con, f"g{i}", D + dt.timedelta(days=i))
        con.execute(
            "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
            "VALUES (5, ?, ?, 'out', 'nba_official_report')",
            [_at(19, 30, days=i), f"g{i}"],
        )
    res = run_usage_redistribution_eval(con)
    pre = res.preconditions
    assert pre.n_games_with_usable_report == 0 and not pre.passed
    assert pre.n_rows_after_cutoff_ignored == 5
