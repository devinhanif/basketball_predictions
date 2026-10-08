"""Smoke test for ``nba.eval.usage_redistribution_eval`` (CLAUDE.md "no
multi-minute jobs" -- real-DB run is the maintainer's job, see that
module's docstring for the exact command).
"""

from __future__ import annotations

from nba.eval.usage_redistribution_eval import run_usage_redistribution_eval


def test_smoke_runs_on_fixture() -> None:
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db()
    try:
        result = run_usage_redistribution_eval(con, n_sims=200, seed=0, max_games=3)
        # The committed fixture has no `possessions` rows, so every
        # per-game player-points call degrades gracefully (no shots -> no
        # scoring attribution) and no games qualify as "restricted" --
        # this is an honest zero, not a crash, and is exactly what the
        # no-op full-sample regression guard requires in this degenerate
        # case too.
        assert result.n_restricted_sample == 0
        assert result.boost_a_n == 0
        assert result.boost_b_n == 0
        summary = result.summary()
        assert "CRPS baseline" in summary
    finally:
        con.close()
