"""Game scripts, sliders, who-sits shift, best picks and the live script math (no network)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from nba.parlay import combo as C
from nba.parlay import game_scripts as GS

PARAMS = json.loads((Path(C.__file__).parent / "combo_params_v1.json").read_text())
SHARED: dict[str, Any] = {
    "corr": PARAMS["corr"],
    "scripts": {
        "definitions": PARAMS["definitions"],
        "params": PARAMS["script_params"],
        "status": PARAMS["script_status"],
    },
}


def _grid(mean: float, sd: float, main: float | None, k_max: int = 60) -> dict[str, Any]:
    """Survival grid of a normal on the integers (calibrated-looking), trimmed like the payload."""
    from statistics import NormalDist

    nd = NormalDist(mean, sd)
    surv = [round(1.0 - nd.cdf(k - 0.5), 4) for k in range(1, k_max)]
    k0 = next((i + 1 for i, v in enumerate(surv) if v < 1.0), k_max)
    last = max((i for i, v in enumerate(surv) if v > 0.0), default=0)
    g: dict[str, Any] = {"k0": k0, "surv": surv[k0 - 1 : last + 1], "main_line": main}
    if main is not None:
        g["raw_at_main"] = round(1.0 - nd.cdf(main), 4)
    return g


def game() -> dict[str, Any]:
    def p(
        pid: int, name: str, team: int, mu: float, rb: float, pp: float, st: int
    ) -> dict[str, Any]:
        return {
            "player_id": pid, "name": name, "team": team, "p_play": pp, "starter": st,
            "stats": {
                "pts": _grid(mu, 6.0, math.floor(mu) + 0.5),
                "reb": _grid(rb, 2.5, math.floor(rb) + 0.5),
            },
        }  # fmt: skip

    return {
        "game_id": "G1", "date": "2026-11-10", "home_team": 1, "away_team": 2, "tip_utc": "x",
        "made_at": "x", "home_abbr": "AAA", "away_abbr": "BBB",
        "game": {"mu_m": 3.0, "sd_m": 13.63, "mu_t": 228.0, "sd_t": 18.82},
        "players": [
            p(10, "Home Star", 1, 24.0, 6.0, 0.95, 1),
            p(11, "Home Bench", 1, 8.0, 3.0, 0.9, 0),
            p(20, "Away Star", 2, 22.0, 8.0, 0.95, 1),
            p(21, "Away Out", 2, 12.0, 4.0, 0.3, 0),
        ],
    }  # fmt: skip


def test_priors_are_the_combo_priors_and_in_range() -> None:
    g = game()
    pri = GS.script_priors(g, SHARED)
    assert set(pri) == set(GS.SCRIPTS)
    for s in GS.SCRIPTS:
        assert pri[s] == C.script_prior(s, g, SHARED)
        assert 0.05 < pri[s] < 0.6
    assert abs(pri["shootout"] - 0.27) < 0.04 and abs(pri["grind"] - 0.27) < 0.04  # by definition


def _full_cov(g: dict[str, Any], pid: int, stat: str) -> tuple[np.ndarray, np.ndarray]:
    """The reference: Sigma_pg Sigma_gg^-1 with numpy."""
    table = SHARED["corr"]
    sign = 1.0 if next(p for p in g["players"] if p["player_id"] == pid)["team"] == 1 else -1.0
    a = sign * C.corr_lookup(table, "own_margin", stat, "margin")
    b = C.corr_lookup(table, "game", stat, "total")
    r = C.corr_lookup(table, "game", "margin", "total")
    sgg = np.array([[1.0, r], [r, 1.0]])
    spg = np.array([a, b])
    return spg @ np.linalg.inv(sgg), np.array([1.0 - spg @ np.linalg.inv(sgg) @ spg])


def test_point_condition_matches_matrix_conditioning() -> None:
    g = game()
    for pid in (10, 20):
        pl = next(p for p in g["players"] if p["player_id"] == pid)
        for stat in ("pts", "reb"):
            for m, t in ((3.0, 228.0), (20.0, 250.0), (-18.0, 205.0)):
                mean, sd = GS.point_condition(g, SHARED, pl, stat, m, t)
                w, var = _full_cov(g, pid, stat)
                z = np.array([(m - 3.0) / 13.63, (t - 228.0) / 18.82])
                assert abs(mean - float(w @ z)) < 1e-12
                assert abs(sd - math.sqrt(float(var[0]))) < 1e-12
    pl = g["players"][0]
    mean, sd = GS.point_condition(g, SHARED, pl, "pts", 3.0, 228.0)
    assert mean == 0.0 and 0.98 < sd < 1.0  # at the expected point: no shift, slightly tighter


def test_point_condition_direction_and_home_sign() -> None:
    g = game()
    home, away = g["players"][0], g["players"][2]
    hi = GS.point_condition(g, SHARED, home, "pts", 3.0, 260.0)[0]
    lo = GS.point_condition(g, SHARED, home, "pts", 3.0, 190.0)[0]
    assert hi > 0 > lo  # a high total raises points
    big_home = GS.point_condition(g, SHARED, home, "pts", 20.0, 228.0)[0]
    big_away = GS.point_condition(g, SHARED, away, "pts", 20.0, 228.0)[0]
    assert big_home > 0 > big_away  # the margin is home minus away


def test_cond_surv_is_a_valid_survival_and_reduces_to_the_grid_at_the_mean() -> None:
    g = game()
    grid = g["players"][0]["stats"]["pts"]
    prev = 1.0
    for k in range(1, 50):
        s = GS.cond_surv(grid, k, 0.3, 0.99)
        assert 0.0 <= s <= prev + 1e-12
        prev = s
    for k in (10, 20, 24, 30):
        assert abs(GS.cond_surv(grid, k, 0.0, 1.0) - C.surv_at(grid, k)) < 1e-9
    assert GS.cond_surv(grid, 0, 0.5, 0.9) == 1.0 and GS.cond_surv(grid, 500, 0.5, 0.9) == 0.0


def test_point_regions() -> None:
    g = game()
    assert GS.point_regions(g, SHARED, 20.0, 228.0) == ["blowout"]
    assert GS.point_regions(g, SHARED, -3.0, 228.0) == ["close"]
    mt = math.ceil(228.0 + 12.0)
    assert "shootout" in GS.point_regions(g, SHARED, 10.0, mt)
    assert "grind" in GS.point_regions(g, SHARED, 10.0, math.floor(228.0 - 12.0))
    assert GS.point_regions(g, SHARED, 10.0, 228.0) == []


def test_shift_surv_is_a_location_shift() -> None:
    g = game()
    grid = g["players"][0]["stats"]["pts"]
    for k in range(1, 40):
        assert GS.shift_surv(grid, k, 0.0) == C.surv_at(grid, k)
        assert abs(GS.shift_surv(grid, k, 2.0) - C.surv_at(grid, k - 2)) < 1e-12
    prev = 1.0
    for k in range(0, 60):
        s = GS.shift_surv(grid, k, 1.7)
        assert s <= prev + 1e-12
        prev = s
    assert GS.shift_surv(grid, 24, 3.0) > C.surv_at(grid, 24)  # more points: likelier to clear


def test_live_probs_collapse_to_the_prior_at_tip_and_to_the_score_at_the_end() -> None:
    g = game()
    pri = GS.script_priors(g, SHARED)
    p0 = GS.live_script_probs(g, SHARED, 0.0, 0, 0)
    for s in GS.SCRIPTS:
        assert abs(p0[s] - pri[s]) < 1e-12
    late = GS.live_script_probs(g, SHARED, 47.9, 22, 210)  # already a blowout, total within reach
    assert late["blowout"] > 0.99 and late["close"] < 0.01
    tight = GS.live_script_probs(g, SHARED, 47.9, 1, 226)
    assert tight["close"] > 0.99 and tight["blowout"] < 0.01
    assert tight["shootout"] < 0.01 and tight["grind"] < 0.01


def test_live_probs_move_the_right_way() -> None:
    g = game()
    pri = GS.script_priors(g, SHARED)
    lead = GS.live_script_probs(g, SHARED, 30.0, 20, 140)  # a big lead at 30 minutes
    assert lead["blowout"] > pri["blowout"] and lead["close"] < pri["close"]
    even = GS.live_script_probs(g, SHARED, 30.0, 1, 140)
    assert even["close"] > pri["close"]
    fast = GS.live_script_probs(g, SHARED, 24.0, 0, 150)  # 150 at the half is ahead of 228 pace
    slow = GS.live_script_probs(g, SHARED, 24.0, 0, 100)
    assert fast["shootout"] > pri["shootout"] > slow["shootout"]
    assert slow["grind"] > pri["grind"] > fast["grind"]
    # every probability is a probability
    for st in (fast, slow, lead, even):
        assert all(0.0 <= v <= 1.0 for v in st.values())


def test_best_picks_shape_ranking_and_no_market_without_a_callback() -> None:
    g = game()
    picks = GS.best_picks(g, SHARED)
    assert set(picks) == set(GS.SCRIPTS)
    seen_unlikely = False
    for s in GS.SCRIPTS:
        blk = picks[s]
        assert blk["status"] == "exploratory"
        ps = [leg["p_leg_script"] for leg in blk["legs"]]
        assert ps == sorted(ps, reverse=True) and 0 < len(ps) <= GS.TOP_LEGS
        for leg in blk["legs"]:
            assert leg["status"] == "exploratory" and leg["market"] is None
            assert leg["p_script"] == blk["p_script"]
            assert leg["p_leg_script"] <= C.TESTED_P_MAX  # nothing above the tested range is ranked
            assert abs(leg["shift"] - (leg["p_leg_script"] - leg["p_leg"])) < 2e-4
            assert leg["player_id"] != 21  # p_play 0.3 < the minimum
            seen_unlikely |= leg["player_id"] == 21
        for pr in blk["pairs"]:
            assert pr["player_id"] != 21 and len(pr["legs"]) == 2
            assert pr["lift"] is not None and pr["p_joint_script"] >= 0.02
    assert not seen_unlikely
    text = json.dumps(picks)
    for banned in ("stake", "kelly", "bankroll", "size"):
        assert banned not in text


def test_script_shifts_have_the_documented_sign_for_starters() -> None:
    """Fitted params push starters DOWN in a blowout and UP in a shootout."""
    g = game()
    over = {
        "kind": "player",
        "game_id": "G1",
        "player_id": 10,
        "stat": "pts",
        "line": 24.5,
        "side": "over",
    }
    base = C.evaluate_game_legs([over], g, SHARED).p_joint
    blow = C.evaluate_game_legs([over], g, SHARED, script="blowout").p_joint
    shoot = C.evaluate_game_legs([over], g, SHARED, script="shootout").p_joint
    assert blow < base < shoot


def test_market_fn_is_attached_only_to_over_legs_and_never_to_the_script_probability() -> None:
    g = game()
    calls: list[tuple[int, str, int]] = []

    def fn(pid: int, stat: str, k: int) -> dict[str, Any]:
        calls.append((pid, stat, k))
        return {"ticker": f"T{pid}{stat}{k}", "verdict": "no_positive_ev_found"}

    picks = GS.best_picks(g, SHARED, market_fn=fn)
    n = 0
    for s in GS.SCRIPTS:
        for leg in picks[s]["legs"]:
            if leg["side"] == "under":
                assert leg["market"] is None
            else:
                assert (
                    leg["market"]["ticker"]
                    == f"T{leg['player_id']}{leg['stat']}{int(leg['line'] + 0.5)}"
                )
                n += 1
    assert n > 0 and all(k == int(math.floor(0)) or k >= 1 for _, _, k in calls)


def test_ask_legs_outside_the_band_are_not_candidates() -> None:
    g = game()
    grid = g["players"][0]["stats"]["pts"]
    k_lo = next(k for k in range(1, 60) if C.surv_at(grid, k) < 0.30)  # below the 0.35 band floor
    k_ok = next(k for k in range(1, 60) if 0.4 <= C.surv_at(grid, k) <= 0.6 and k != 25)
    cands = GS.candidate_legs(g, {(10, "pts"): [k_lo, k_ok]})
    lines = {
        c["line"]
        for c in cands
        if c["player_id"] == 10 and c["stat"] == "pts" and c["side"] == "over"
    }
    assert k_ok - 0.5 in lines and k_lo - 0.5 not in lines


def test_no_execution_surface() -> None:
    for mod in (GS,):
        src = Path(mod.__file__).read_text().lower()
        for word in (
            "place_order",
            "create_order",
            "portfolio",
            "/orders",
            "private_key",
            "api_key",
        ):
            assert word not in src
