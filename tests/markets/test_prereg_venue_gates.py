"""Pure-function checks for the EXCHANGE_LADDER_CAL and WINNER_RECAL gate scripts"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.eval import exchange_ladder_cal_gate as ex
from research.eval import winner_recal_gate as wr


def test_event_game_code_and_date() -> None:
    assert ex.event_game_code("KXNBAPTS-26OCT05NYKPHI") == "26OCT05NYKPHI"
    assert ex._code_date("26OCT05NYKPHI") == "2026-10-05"


def test_power_table_monotone_in_games_and_effect() -> None:
    t = ex.power_table()
    a = t["sd_x1.0_games_400"]
    b = t["sd_x1.0_games_960"]
    assert a["power_T493_size"] >= a["power_half_of_T493"] >= a["power_CAL_minus_C0_size"]
    assert b["power_half_of_T493"] >= a["power_half_of_T493"]
    assert a["pass_line"] <= ex.FLOOR + 1e-12


def test_projection_scenarios_order() -> None:
    tips = [dt.datetime(2026, 10, 20) + dt.timedelta(days=i // 7) for i in range(1200)]
    sched = pl.DataFrame({"tip": tips})
    scen = ex.scenarios(0.05, 8.0, 45.0)
    out = ex.project(sched, scen)
    assert out["L_preseason_rate"]["first_date_both_minima_met"] is None
    assert out["M_every_game_preseason_depth"]["first_date_both_minima_met"] is not None
    last = ex.LOOK_DATES[-1].isoformat()
    assert (
        out["H_every_game_finals_depth"]["look_dates"][last]["usable_legs"]
        > out["M_every_game_preseason_depth"]["look_dates"][last]["usable_legs"]
    )


def test_winner_sizing_identity_has_zero_variance() -> None:
    # the logit map with slope 1 and intercept 0 leaves the probability unchanged
    p = np.array([0.3, 0.5, 0.7])
    assert np.allclose(wr.inv_logit(wr.logit(p)), p)


def test_gate_scripts_never_select_outcome_columns() -> None:
    import inspect

    src = inspect.getsource(wr.load_rows)
    assert "home_win" in src and 'assert "home_win" not in cols' in src
