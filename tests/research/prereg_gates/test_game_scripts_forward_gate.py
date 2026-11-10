"""GAME_SCRIPTS_FORWARD gate: the closed-form script priors equal the served `combo.script_prior`
(rule section 4.1 integrity check), and the projection counts post-freeze games only."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from nba.parlay import combo
from research.eval import game_scripts_forward_gate as g

SHARED = {
    "scripts": {
        "definitions": {"blowout_abs_margin": 15, "close_abs_margin": 5, "total_delta": 12.0}
    }
}


@pytest.mark.parametrize("mu_m", [-14.2, -3.0, 0.0, 0.58, 6.9, 12.5])
@pytest.mark.parametrize("mu_t", [205.0, 221.37, 228.99, 240.5])
def test_closed_form_equals_served_script_prior(mu_m: float, mu_t: float) -> None:
    game = {"game": {"mu_m": mu_m, "sd_m": 13.63, "mu_t": mu_t, "sd_t": 18.82}}
    assert g.p_blowout(mu_m, 13.63) == pytest.approx(
        combo.script_prior("blowout", game, SHARED), abs=1e-12
    )
    assert g.p_close(mu_m, 13.63) == pytest.approx(
        combo.script_prior("close", game, SHARED), abs=1e-12
    )
    assert g.p_shootout(mu_t, 18.82) == pytest.approx(
        combo.script_prior("shootout", game, SHARED), abs=1e-12
    )
    assert g.p_grind(mu_t, 18.82) == pytest.approx(
        combo.script_prior("grind", game, SHARED), abs=1e-12
    )


def test_shootout_and_grind_priors_are_a_constant_band() -> None:
    vals = [g.p_shootout(220.0 + f / 100, 18.82) for f in range(100)]
    assert min(vals) > 0.2530
    assert max(vals) < 0.2710


def test_projection_counts_only_games_after_the_freeze() -> None:
    days = [date(2026, 11, d) for d in (11, 12, 13, 14, 15)]
    sched = pl.DataFrame(
        {
            "game_id": [f"00226{i:05d}" for i in range(5)],
            "tip_utc": [datetime(2026, 11, d.day, 23, tzinfo=UTC) for d in days],
            "et_date": days,
        }
    )
    out = g.part_c(sched, date(2026, 11, 12), 1.0, (1, 3, 4))
    assert out["scheduled_after_freeze"] == 3
    assert out["date_reaching_n"] == {"1": "2026-11-13", "3": "2026-11-15", "4": None}
