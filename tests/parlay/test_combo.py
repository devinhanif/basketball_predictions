"""Combo mode: the reference math, the builders, the golden vectors and the no-prices guard."""

from __future__ import annotations

import ast
import gzip
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from combo_fixture import GOLDEN, HOME, build, cases, game, shared
from scipy import stats

from nba.parlay import combo, combo_build, combo_eval
from nba.parlay.copula import nearest_correlation, sample_latent_uniforms
from nba.parlay.line_cal import CalCell, CalMap

G = game()
SH = shared()
GAMES = {"G1": G}
NO_CORR = {**SH, "corr": []}


def leg(pid: int, stat: str, line: float, side: str = "over") -> dict[str, Any]:
    return {
        "kind": "player",
        "game_id": "G1",
        "player_id": pid,
        "stat": stat,
        "line": line,
        "side": side,
    }


# ---------------------------------------------------------------- integer support and marginals


def test_over_half_line_is_survival_at_next_integer() -> None:
    grid = G["players"][0]["stats"]["pts"]
    r = combo.evaluate_combo([leg(1, "pts", 24.5)], GAMES, SH)
    assert r.p_joint == combo.surv_at(grid, 25)
    u = combo.evaluate_combo([leg(1, "pts", 24.5, "under")], GAMES, SH)
    assert u.p_joint == pytest.approx(1.0 - combo.surv_at(grid, 25), abs=1e-12)  # Y <= 24
    assert combo.line_to_bounds(24.5, "over")[0] == 25
    assert combo.line_to_bounds(24.5, "under")[1] == 24


def test_whole_number_lines_are_refused() -> None:
    with pytest.raises(ValueError):
        combo.line_to_bounds(24.0, "over")


def test_single_leg_reduces_to_marginal_exactly() -> None:
    r = combo.evaluate_combo([leg(1, "reb", 6.5)], GAMES, SH)
    assert r.method == "marginal" and r.se == 0.0
    assert r.p_joint == r.p_independent and r.lift == 1.0


def test_same_stat_legs_merge_to_an_interval() -> None:
    grid = G["players"][0]["stats"]["pts"]
    r = combo.evaluate_combo([leg(1, "pts", 20.5), leg(1, "pts", 29.5, "under")], GAMES, SH)
    assert r.p_joint == pytest.approx(combo.surv_at(grid, 21) - combo.surv_at(grid, 30), abs=1e-12)
    assert r.n_entities == 1
    hi = combo.evaluate_combo([leg(1, "pts", 20.5), leg(1, "pts", 25.5)], GAMES, SH)
    assert hi.p_joint == pytest.approx(combo.surv_at(grid, 26), abs=1e-12)
    empty = combo.evaluate_combo([leg(1, "pts", 29.5), leg(1, "pts", 20.5, "under")], GAMES, SH)
    assert empty.p_joint == 0.0 and empty.method == "empty"


# ---------------------------------------------------------------- joint properties


def _all_combos() -> list[list[dict[str, Any]]]:
    legs = [
        leg(p, s, ml, side)
        for p, s, ml in [
            (1, "pts", 24.5),
            (1, "reb", 6.5),
            (1, "fg3m", 2.5),
            (2, "pts", 8.5),
            (3, "reb", 8.5),
            (3, "pts", 21.5),
        ]
        for side in ("over", "under")
    ]
    out = []
    for k in (2, 3):
        for c in itertools.islice(itertools.combinations(legs, k), 0, None, 7):
            if len({(x["player_id"], x["stat"]) for x in c}) == k:
                out.append(list(c))
    return out


def test_joint_is_bounded_by_min_marginal_and_frechet() -> None:
    for c in _all_combos():
        r = combo.evaluate_combo(c, GAMES, SH)
        assert r.p_joint <= min(r.marginals) + 1e-12
        assert r.p_joint >= max(0.0, sum(r.marginals) - (len(r.marginals) - 1)) - 1e-12


def test_no_correlation_means_product() -> None:
    r = combo.evaluate_combo([leg(1, "pts", 24.5), leg(1, "reb", 6.5)], GAMES, NO_CORR)
    assert r.p_joint == pytest.approx(r.p_independent, abs=1e-8)
    r3 = combo.evaluate_combo(
        [leg(1, "pts", 24.5), leg(1, "reb", 6.5), leg(3, "reb", 8.5)], GAMES, NO_CORR
    )
    assert abs(r3.p_joint - r3.p_independent) <= 4 * r3.se + 1e-4


def test_exact_bivariate_matches_scipy() -> None:
    for a1, b1, a2, b2, rho in [
        (0.3, math.inf, -0.2, math.inf, 0.5),
        (-1.0, 0.8, 0.1, 1.5, -0.3),
        (0.0, math.inf, 0.0, math.inf, 0.9),
    ]:
        ref = stats.multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]], allow_singular=True)
        hi1, hi2 = min(b1, 12.0), min(b2, 12.0)
        want = ref.cdf([hi1, hi2]) - ref.cdf([a1, hi2]) - ref.cdf([hi1, a2]) + ref.cdf([a1, a2])
        assert combo.bivariate_rect(a1, b1, a2, b2, rho) == pytest.approx(want, abs=2e-6)


def test_vectorised_bivariate_matches_reference() -> None:
    rng = np.random.default_rng(0)
    a1, a2 = rng.normal(0, 1, 40), rng.normal(0, 1, 40)
    rho = rng.uniform(-0.6, 0.8, 40)
    vec = combo_eval.biv_over(a1, a2, rho)
    for i in range(40):
        assert vec[i] == pytest.approx(
            combo.bivariate_rect(a1[i], math.inf, a2[i], math.inf, rho[i]), abs=2e-6
        )


def test_qmc_agrees_with_numpy_copula_monte_carlo() -> None:
    legs = [leg(1, "pts", 24.5), leg(1, "reb", 6.5), leg(1, "ast", 5.5), leg(1, "fg3m", 2.5)]
    r = combo.evaluate_combo(legs, GAMES, SH)
    ents = combo.merge_legs(legs, G)
    corr, _ = combo.build_corr([m.entity for m in ents], G, SH["corr"])
    u = sample_latent_uniforms(nearest_correlation(np.array(corr)), 400_000, 7)
    thr = 1.0 - np.array(r.marginals)
    mc = float(np.mean((u > thr[None, :]).all(axis=1)))
    se_mc = math.sqrt(mc * (1 - mc) / 400_000)
    assert r.method == "qmc"
    assert abs(mc - r.p_joint) <= 4 * math.hypot(se_mc, r.se)
    assert r.se < 2e-4


def test_same_player_overs_have_lift_above_one_and_mixed_sides_below() -> None:
    up = combo.evaluate_combo([leg(1, "pts", 24.5), leg(1, "fg3m", 2.5)], GAMES, SH)
    mixed = combo.evaluate_combo([leg(1, "pts", 24.5), leg(1, "fg3m", 2.5, "under")], GAMES, SH)
    assert up.lift is not None and up.lift > 1.2
    assert mixed.lift is not None and mixed.lift < 1.0


def test_cross_game_legs_are_independent_and_lift_one() -> None:
    g2 = json.loads(json.dumps(G))
    g2["game_id"] = "G2"
    a = leg(1, "pts", 24.5)
    b = {**leg(1, "pts", 24.5), "game_id": "G2"}
    r = combo.evaluate_combo([a, b], {"G1": G, "G2": g2}, SH)
    single = combo.evaluate_combo([a], GAMES, SH)
    assert r.p_joint == pytest.approx(single.p_joint**2, abs=1e-12)
    assert r.lift == pytest.approx(1.0, abs=1e-12)


def test_game_legs_use_signed_own_margin_and_total() -> None:
    win = {"kind": "team_margin", "game_id": "G1", "team": HOME, "gt": 0.0}
    away_win = {"kind": "team_margin", "game_id": "G1", "team": G["away_team"], "gt": 0.0}
    pts = leg(1, "pts", 24.5)  # home player: positively tied to the home margin
    a = combo.evaluate_combo([win, pts], GAMES, SH)
    b = combo.evaluate_combo([away_win, pts], GAMES, SH)
    assert a.lift is not None and a.lift > 1.0
    assert b.lift is not None and b.lift < 1.0
    both = combo.evaluate_combo([win, away_win], GAMES, SH)
    assert both.p_joint == 0.0 or both.method == "empty" or both.p_joint < 1e-6


def test_psd_repair_shrinks_only_when_needed() -> None:
    ok = [[1.0, 0.3], [0.3, 1.0]]
    assert combo.repair_to_pd(ok) == (ok, 0.0)
    bad = [[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]]
    fixed, t = combo.repair_to_pd(bad)
    assert t > 0 and combo.cholesky(fixed) is not None


def test_dnp_is_reported_separately_and_counts_as_a_miss() -> None:
    r = combo.evaluate_combo([leg(1, "pts", 24.5), leg(2, "pts", 8.5)], GAMES, SH)
    assert r.p_all_play == pytest.approx(0.98 * 0.90)
    assert r.p_joint_incl_dnp == pytest.approx(r.p_all_play * r.p_joint)
    same = combo.evaluate_combo([leg(1, "pts", 24.5), leg(1, "reb", 6.5)], GAMES, SH)
    assert same.p_all_play == pytest.approx(0.98)  # one player counted once


# ---------------------------------------------------------------- scripts


def test_identity_script_equals_unconditional() -> None:
    sh = json.loads(json.dumps(SH))
    sh["scripts"]["params"] = [
        [s, st, r, 0.0, 1.0, 1]
        for s in combo.SCRIPTS
        for st in combo.STATS
        for r in ("starter", "bench")
    ]
    legs = [leg(1, "pts", 24.5), leg(1, "reb", 6.5)]
    a = combo.evaluate_combo(legs, GAMES, sh)
    b = combo.evaluate_combo(legs, GAMES, sh, script="blowout")
    assert a.p_joint == pytest.approx(b.p_joint, abs=1e-12)


def test_script_shift_moves_marginals_and_rejects_game_legs() -> None:
    base = combo.evaluate_combo([leg(1, "pts", 24.5)], GAMES, SH)
    blow = combo.evaluate_combo([leg(1, "pts", 24.5)], GAMES, SH, script="blowout")
    assert blow.p_joint < base.p_joint  # starter mu is negative in the fixture
    with pytest.raises(ValueError):
        combo.evaluate_combo(
            [{"kind": "total", "game_id": "G1", "line": 227.5, "side": "over"}],
            GAMES,
            SH,
            script="close",
        )
    with pytest.raises(ValueError):
        combo.evaluate_combo([leg(1, "pts", 24.5)], GAMES, SH, script="nope")


def test_script_priors_are_integer_corrected_probabilities() -> None:
    pri = {s: combo.script_prior(s, G, SH) for s in combo.SCRIPTS}
    assert all(0 < v < 1 for v in pri.values())
    d = combo.script_definitions(G, SH)
    assert d["shootout"]["min_total"] == math.ceil(228.0 + 12.0)
    assert d["grind"]["max_total"] == math.floor(228.0 - 12.0)
    # |margin| >= 15 with mu 3, sd 13.6: P(M >= 14.5) + P(M <= -14.5)
    want = stats.norm.sf((14.5 - 3.0) / 13.6) + stats.norm.cdf((-14.5 - 3.0) / 13.6)
    assert pri["blowout"] == pytest.approx(want, abs=1e-9)


# ---------------------------------------------------------------- framings 1 and 2


def test_lift_ranking_sorted_with_words() -> None:
    rows = combo.lift_ranking(G, SH, top_n=8)
    lifts = [r["lift"] for r in rows]
    assert lifts == sorted(lifts, reverse=True) and lifts[0] > 1.5
    assert all(r["p_joint"] >= 0.02 for r in rows)
    assert all("same player" in r["reason"] for r in rows[:3])


def test_target_search_meets_target_inside_the_band_and_is_not_degenerate() -> None:
    out = combo.target_search(G, SH, target=0.15, k_legs=2, top_n=3)
    assert out["feasible"]
    for c in out["combos"]:
        assert c["p_joint"] >= 0.15
        assert c["lift"] is not None and c["lift"] > 1.0  # correlation helps: not the product
        for leg_ in c["legs"]:
            r = combo.evaluate_combo([leg_], GAMES, SH)
            assert combo.TARGET_P_MIN <= r.p_joint <= combo.TARGET_P_MAX
    lifts = [c["lift"] for c in out["combos"]]
    assert lifts == sorted(lifts, reverse=True)


def test_target_search_reports_infeasible_target() -> None:
    out = combo.target_search(G, SH, target=0.9, k_legs=3, top_n=2, beam=6)
    assert out["feasible"] is False and out["combos"]
    assert all(c["p_joint"] < 0.9 for c in out["combos"])


def test_main_line_floor_mode_never_goes_below_the_posted_line() -> None:
    for c in combo.candidate_legs(G, floor_mode="main_line"):
        ml = next(p for p in G["players"] if p["player_id"] == c["player_id"])["stats"][c["stat"]][
            "main_line"
        ]
        assert c["line"] >= ml


# ---------------------------------------------------------------- golden vectors


def test_golden_vectors_reproduce() -> None:
    stored = json.loads(GOLDEN.read_text())
    fresh = json.loads(json.dumps(build()))
    for a, b in zip(stored["cases"], fresh["cases"], strict=True):
        assert a["name"] == b["name"]
        for key, want in a["expect"].items():
            got = b["expect"][key]
            if isinstance(want, float):
                assert got == pytest.approx(want, abs=1e-12), (a["name"], key)
            else:
                assert got == want, (a["name"], key)
    assert stored["script_priors"] == pytest.approx(fresh["script_priors"], abs=1e-12)
    assert stored["lift_ranking_top5"] == fresh["lift_ranking_top5"]
    assert stored["target_search"] == fresh["target_search"]
    assert len(stored["cases"]) == len(cases())


# ---------------------------------------------------------------- builders


def _cal_map() -> CalMap:
    beta = (0.0, 0.8, 0.0, 0.0, 0.0)  # logit slope 0.8: shrinks toward 0.5
    return CalMap(
        {}, {(combo_build.BOOK, "pts"): CalCell(combo_build.BOOK, "pts", beta, 5000, 4000.0)}
    )


def _raw_row() -> np.ndarray:
    k = np.arange(1, 61)
    return np.asarray(1.0 - stats.poisson.cdf(k - 1, 25.0))[None, :]


def test_calibrate_surv_is_monotone_valid_and_banded() -> None:
    raw = _raw_row()
    med, spr = np.array([25.0]), np.array([5.0])
    surv, k_lo, k_hi = combo_build.calibrate_surv_banded(
        _cal_map(), "pts", raw, med, spr, np.array([1.0])
    )
    s = surv[0]
    assert np.all(np.diff(s) <= 1e-12) and s.min() >= 0.0 and s.max() <= 1.0
    # band: |line - 25| <= 5 -> k in [21, 30]
    assert int(k_lo[0]) == 21 and int(k_hi[0]) == 30
    # inside the band the shrink pulls a 0.7+ probability down and a 0.3- probability up
    assert s[20] < raw[0, 20] and s[29] > raw[0, 29]
    # beyond the band the grid keeps its tails (no fattening beyond the held offset)
    assert s[55] < 0.01


def test_pit_z_of_own_grid_is_standard_normal() -> None:
    rng = np.random.default_rng(1)
    mu = rng.uniform(3, 20, 4000)
    k = np.arange(1, 61)
    surv = 1.0 - stats.poisson.cdf(k[None, :] - 1, mu[:, None])
    y = rng.poisson(mu).astype(float)
    z = combo_build.pit_z(surv, y, rng.random(len(y)))
    assert abs(z.mean()) < 0.05 and abs(z.std() - 1.0) < 0.05


def test_trim_surv_round_trip() -> None:
    row = _raw_row()[0]
    k0, vals = combo_build.trim_surv(row)
    grid = {"k0": k0, "surv": vals}
    for k in (1, k0, k0 + 3, 25, 40, 59, 60):
        assert combo.surv_at(grid, k) == pytest.approx(round(float(row[k - 1]), 4), abs=1e-12)
    assert combo.surv_at(grid, 0) == 1.0 and combo.surv_at(grid, 500) == 0.0
    assert combo_build.trim_surv(np.ones(5)) == (6, [])


def test_payload_is_json_and_inside_the_size_budget() -> None:
    raw = _raw_row()[0]
    players = [
        {"player_id": i, "name": f"P{i}", "team": 1 + i % 2, "p_play": 0.95, "starter": i % 2,
         "stats": {s: {"cal": raw, "raw": raw, "main_line": 24.5} for s in combo.STATS}}
        for i in range(20)
    ]  # fmt: skip
    gp = combo_build.build_game_payload(
        game_id="g",
        date="2026-11-10",
        home_team=1,
        away_team=2,
        tip_utc="t",
        made_at="m",
        mu_m=1.0,
        sd_m=13.6,
        mu_t=228.0,
        sd_t=18.8,
        players=players,
    )
    raw_bytes = json.dumps(gp, separators=(",", ":")).encode()
    assert len(raw_bytes) < 40_000  # 20 players x 4 stats, wide pts-like grids: well under 40 KB
    assert len(gzip.compress(raw_bytes)) < 8_000
    assert json.loads(raw_bytes)["players"][0]["stats"]["pts"]["raw_at_main"] > 0


# ---------------------------------------------------------------- the read-only, no-prices guard

_FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "nba.kalshi",
    "nba.odds",
    "nba.markets",
    "nba.export",
    "nba.daily",
}
_FORBIDDEN_WORDS = (
    "price",
    "fee",
    "kelly",
    "stake",
    "bankroll",
    "order",
    "portfolio",
    "api_key",
    "secret",
)


@pytest.mark.parametrize("name", ["combo.py", "combo_build.py", "combo_eval.py"])
def test_combo_modules_have_no_network_market_or_money_vocabulary(name: str) -> None:
    path = Path(combo.__file__).with_name(name)
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            assert not any(m == f or m.startswith(f + ".") for f in _FORBIDDEN_IMPORTS), (name, m)
    # identifiers and string constants outside docstrings
    doc_nodes = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(getattr(n.body[0], "value", None), ast.Constant)
    }
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.arg):
            text = node.arg
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_nodes
        ):
            text = node.value
        if text:
            low = text.lower()
            assert not any(w in low for w in _FORBIDDEN_WORDS), (name, text)


def test_combo_export_contract_exists_and_names_every_top_level_key() -> None:
    doc = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "combo_export_v1.md"
    ).read_text()
    for key in (
        "schema",
        "night",
        "shared",
        "games",
        "corr",
        "scripts.params",
        "scripts.status",
        "players",
        "surv",
        "k0",
        "provenance",
        "cal_k",
        "p_play",
        "tip_utc",
        "made_at",
    ):
        assert key in doc, key


def test_shipped_params_file_is_intact_and_has_no_holdout_data() -> None:
    import hashlib

    path = Path(combo.__file__).with_name("combo_params_v1.json")
    p = json.loads(path.read_text())
    body = json.dumps(
        {k: v for k, v in p.items() if k != "sha256"}, sort_keys=True, separators=(",", ":")
    )
    assert p["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert p["corr_fit_seasons"] == [2023, 2024]
    assert set(p["script_status"]) == set(combo.SCRIPTS)
    assert {r[0] for r in p["script_params"]} == set(combo.SCRIPTS)
    rels = {"same_player", "teammate", "opponent", "own_margin", "game"}
    assert all(len(r) == 5 and r[0] in rels for r in p["corr"])


def test_eval_refuses_season_2025_by_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        combo_eval.load_season(tmp_path, 2025)
    with pytest.raises(ValueError, match="holdout"):
        combo_eval.fit_fold_map(tmp_path, tmp_path, 2025)
