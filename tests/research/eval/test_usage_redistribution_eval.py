"""Smoke / end-to-end plumbing for ``research.eval.usage_redistribution_eval``
(small synthetic DBs only; the real-DB run is the maintainer's job)."""

from __future__ import annotations

import datetime as dt

from nba.db.connect import connect
from research.eval.usage_redistribution_eval import main, run_usage_redistribution_eval
from research.sim.usage_redistribution import ReportTriggerConfig


def test_smoke_precondition_fail_on_fixture() -> None:
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db()
    try:
        result = run_usage_redistribution_eval(con, n_sims=200, seed=0, max_games=3)
        assert not result.ran
        assert "PRECONDITIONS" in result.summary()
    finally:
        con.close()


def _build_synthetic(con, n_games: int = 14, n_out: int = 7) -> None:  # type: ignore[no-untyped-def]
    base = dt.date(2023, 10, 1)
    for i in range(n_games):
        gid = f"s{i:03d}"
        day = base + dt.timedelta(days=7 * i)
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES (?, ?, 2023, 1, 2, 100, 95)",
            [gid, day],
        )
        star_out = i >= n_games - n_out
        for team, players in ((1, (1, 2, 3)), (2, (11, 12, 13))):
            for p in players:
                minutes = 0 if (team == 1 and p == 1 and star_out) else 30  # DNP row
                con.execute(
                    "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
                    "reb, ast, fg3m, stl, blk, tov, starter) VALUES (?, ?, ?, ?, 12, 5, 3, 1, "
                    "1, 0, 1, true)",
                    [gid, p, team, minutes],
                )
        idx = 0
        for team, opp, shooters in ((1, 2, (1, 1, 1, 2, 3, 2)), (2, 1, (11, 11, 12, 13, 12, 13))):
            for s in shooters:
                if team == 1 and s == 1 and star_out:
                    s = 2
                made = idx % 2 == 0
                con.execute(
                    "INSERT INTO possessions (game_id, poss_idx, period, clock_start, clock_end, "
                    "off_team, def_team, score_diff, outcome, shooter_id, shot_zone, fta, pts) "
                    "VALUES (?, ?, 1, 700.0, 680.0, ?, ?, 0, ?, ?, 'rim', 0, ?)",
                    [gid, idx, team, opp, "FGM2" if made else "FGA_miss", s, 2 if made else 0],
                )
                idx += 1
        if star_out:
            for ts, st in ((dt.time(11, 45), "out"), (dt.time(17, 45), "out")):
                con.execute(
                    "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
                    "VALUES (1, ?, ?, ?, 'nba_official_report')",
                    [dt.datetime.combine(day, ts), gid, st],
                )
            # a late (post-cutoff) report that would CLEAR him: must be ignored
            con.execute(
                "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
                "VALUES (1, ?, ?, 'available', 'nba_official_report')",
                [dt.datetime.combine(day, dt.time(19, 30)), gid],
            )
        else:
            con.execute(
                "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
                "VALUES (12, ?, ?, 'probable', 'nba_official_report')",
                [dt.datetime.combine(day, dt.time(17, 45)), gid],
            )


def test_end_to_end_plumbing_on_synthetic_db() -> None:
    con = connect(":memory:")
    try:
        _build_synthetic(con)
        res = run_usage_redistribution_eval(
            con, n_sims=100, seed=0, n_boot=200, trigger=ReportTriggerConfig()
        )
        pre = res.preconditions
        assert pre.passed, pre.summary()
        assert pre.game_coverage == 1.0 and pre.lead_min_minutes >= 60
        assert pre.n_rows_after_cutoff_ignored == 7  # the post-cutoff 'available' rows
        assert res.ran and res.n_games_simulated == 7
        assert len(res.cells) == 8
        fg3m = [c for c in res.cells if c.stat == "fg3m"]
        assert all(c.status == "not_computable" and c.q_bh == 1.0 for c in fg3m)
        # tiny sample -> every computable cell must be flagged underpowered, never shipped
        assert all(c.status in ("underpowered", "not_computable") for c in res.cells)
        assert any(c.n_rows > 0 for c in res.cells)
        # mechanism really fired from the report signal (post-cutoff 'available' ignored)
        assert any(c.n_fired_rows > 0 for c in res.cells)
        for mech in ("2A", "2B"):  # each mechanism fires on its own, not just one of them
            assert any(c.n_fired_rows > 0 for c in res.cells if c.mechanism == mech)
        assert max(v[0] for v in res.boost_by_month.values()) > 0  # 2A raw boost positive
        assert "SHIP: none" in res.summary()
    finally:
        con.close()


def test_cli_attaches_availability_db_read_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import duckdb

    main_path = tmp_path / "main.duckdb"
    avail_path = tmp_path / "avail.duckdb"
    con = connect(main_path)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
        "home_pts, away_pts) VALUES ('g', '2023-12-01', 2023, 1, 2, 1, 1)"
    )
    con.close()
    a = connect(avail_path)
    a.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) "
        "VALUES (5, '2023-12-01 17:45:00', 'g', 'out', 'nba_official_report')"
    )
    a.close()
    out = tmp_path / "r.json"
    rc = main(["--db", str(main_path), "--availability-db", str(avail_path), "--out", str(out)])
    assert rc == 0 and out.exists()
    # both files remain openable for write afterwards (no lingering lock)
    duckdb.connect(str(avail_path)).close()


def test_negative_historical_boost_makes_mechanism_inert_by_design() -> None:
    """Real-data 2A/2B finding: when history shows teammates did NOT absorb
    extra share (raw boost < 0), the one-sided clip yields a true no-op."""
    from research.sim.player_attribution import PlayerSimProfile
    from research.sim.usage_redistribution import (
        UsageRedistributionConfig,
        apply_usage_redistribution,
    )

    def prof(pid: int, share: float) -> PlayerSimProfile:
        return PlayerSimProfile(pid, share, (0.4, 0.3, 0.3), (0.6, 0.4, 0.35), 0.2, 0.75, 30.0)

    profiles = [prof(1, 0.4), prof(2, 0.3), prof(3, 0.2)]
    cfg = UsageRedistributionConfig(enabled=True, method="player")
    assert apply_usage_redistribution(profiles, {1}, cfg, -0.007) is profiles
    assert apply_usage_redistribution(profiles, {1}, cfg, 0.05) is not profiles
