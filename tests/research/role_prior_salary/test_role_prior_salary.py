# ruff: noqa: E501
"""ROLE_PRIOR_SALARY harness tests on synthetic data (no real file, no database)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import role_prior_salary_crosswalk as xwalk
from research.eval import role_prior_salary_eval as ev
from research.eval import role_prior_salary_rule as rule


def test_parse_salary_and_names():
    assert rule.parse_salary("$55,761,216") == 55761216.0
    assert np.isnan(rule.parse_salary(float("nan"))) and np.isnan(rule.parse_salary(""))
    assert rule.nba_stem("Jaren Jackson Jr.") == "jacksja"
    assert rule.nba_stem("Fred VanVleet") == "vanvlfr"
    assert rule.nba_stem("Bogdan Bogdanovic") == rule.bref_stem("bogdabo02")
    assert rule.fold("Dončić") == "doncic"


def test_season_2025_is_refused():
    with pytest.raises(ValueError):
        rule.check_season(2025)
    assert rule.check_season(2024) == 2024
    assert rule.br_year_of(2023) == 2024


def test_tax_state_boundaries_and_pre_2023_has_no_second_apron():
    c = rule.CAP_TABLE[2024]
    assert rule.tax_state(c["tax"] - 1, 2024) == "under_tax"
    assert rule.tax_state(c["tax"], 2024) == "over_tax"
    assert rule.tax_state(c["apron1"], 2024) == "over_apron1"
    assert rule.tax_state(c["apron2"], 2024) == "over_apron2"
    assert rule.tax_state(10**9, 2023) == "over_apron1"  # no second apron before 2023-24


def test_cap_table_is_ordered_and_marks_secondary_sources():
    for c in rule.CAP_TABLE.values():
        assert c["cap"] < c["tax"] < c["apron1"]
        assert c["apron2"] is None or c["apron1"] < c["apron2"]
    assert rule.CAP_TABLE[2027]["status"] == "secondary_source"


def test_contract_class_bands():
    assert rule.contract_class(0.01, "6") == "minimum"
    assert rule.contract_class(0.05, "R") == "rookie_scale"
    assert rule.contract_class(0.05, "9") == "mid_exception"
    assert rule.contract_class(0.30, "9") == "max_range"
    assert (
        rule.contract_class(0.0005, "R") == "minimum"
    )  # a two-way-sized salary is not rookie scale


def _toy():
    sal = pd.DataFrame(
        {
            "team": ["AAA"] * 5,
            "br_year": [2024] * 5,
            "player": list("abcde"),
            "salary": [30e6, 10e6, 10e6, np.nan, 20e6],
            "player_bref_id": ["a01", "b01", "b01", "d01", "e01"],
        }
    )
    ros = pd.DataFrame(
        {
            "team": ["AAA"] * 3,
            "br_year": [2024] * 3,
            "player": list("abd"),
            "pos": ["G"] * 3,
            "birth_date": ["March 3, 1996", "May 4, 1993", "May 4, 1993"],
            "years_experience": ["5", "R", "2"],
            "player_bref_id": ["a01", "b01", "d01"],
        }
    )
    return sal, ros


def test_dead_money_counts_in_payroll_but_not_in_ranks():
    sal, ros = _toy()
    rows = rule.salary_rows(sal, ros)
    assert rows.set_index("player_bref_id").loc["b01", "n_lines"] == 2
    assert (
        rows.set_index("player_bref_id").loc["b01", "salary"] == 10e6
    )  # max of his lines, not the sum
    assert set(rows[~rows.alive].player_bref_id) == {
        "e01"
    }  # on the salary table, not on the roster
    f = rule.team_year_features(rows, ros).set_index("player_bref_id")
    assert f.loc["a01", "payroll"] == 60e6  # includes the dead 20e6
    assert f.loc["a01", "salary_rank"] == 1 and f.loc["b01", "salary_rank"] == 2
    assert np.isnan(f.loc["e01", "salary_rank"])  # dead money gets no rank
    assert f.loc["a01", "cap_share"] == pytest.approx(30e6 / rule.CAP_TABLE[2024]["cap"])
    assert f.loc["b01", "contract_class"] == "rookie_scale"
    assert f.loc["a01", "age"] == pytest.approx(
        (pd.Timestamp("2023-10-01") - pd.Timestamp("1996-03-03")).days / 365.25
    )


def test_blank_dollar_line_is_not_a_salary_row():
    sal, ros = _toy()
    assert "d01" not in set(rule.salary_rows(sal, ros).player_bref_id)


def test_lam_blend_identity_and_fade():
    base = np.array([20.0, 20.0, 20.0, 0.0, np.nan])
    role = np.array([30.0, 30.0, 30.0, 30.0, 30.0])
    g = np.array([0, 5, 10, 0, 0])
    lam = rule.lam_blend(role, base, g, 10)
    assert lam[0] == pytest.approx(1 + rule.W_MAX * 0.5)
    assert lam[1] == pytest.approx(1 + rule.W_MAX * 0.5 * 0.5)
    assert (
        lam[2] == 1.0 and lam[3] == 1.0 and lam[4] == 1.0
    )  # at K, base <= 0, non-finite: production
    assert np.all(rule.lam_blend(base * 0 + 5.0, base * 0 + 5.0, g, 10) == 1.0)


def test_known_at_planted_future():
    assert rule.known_at_ok("2023-11-01", "2023-10-24")
    assert not rule.known_at_ok(
        "2023-10-24", "2023-11-01"
    )  # a line stamped after the row's game is refused
    assert rule.known_at_ok("2023-10-24", pd.NaT)


def test_features_do_not_read_games():
    sal, ros = _toy()
    a = rule.team_year_features(rule.salary_rows(sal, ros), ros)
    b = rule.team_year_features(rule.salary_rows(sal.copy(), ros.copy()), ros)
    pd.testing.assert_frame_equal(
        a, b
    )  # pure function of the salary table and roster; no outcome argument exists


def test_crosswalk_tiers_and_unresolved():
    names = pd.DataFrame(
        {
            "player_id": [1, 2, 3, 4],
            "name": ["Jalen Williams", "Jaylin Williams", "Kenyon Martin Jr.", "KJ Martin"],
        }
    )
    names["stem"] = names.name.map(rule.nba_stem)
    names["folded"] = names.name.map(
        lambda s: rule.fold(" ".join(t for t in s.split() if rule.fold(t) not in rule._SUFFIX))
    )
    ids = pd.DataFrame(
        {
            "player_bref_id": [
                "willija02",
                "willija03",
                "martike04",
                "ghostgh01",
            ],
            "team": ["OKC", "OKC", "HOU", "HOU"],
            "br_year": [2024] * 4,
            "player": ["Jalen Williams", "Jaylin Williams", "Kenyon Martin", "Gus Ghost"],
        }
    )
    appear = pd.DataFrame({"player_id": [1, 2, 4], "team_id": [10, 10, 20], "season": [2023] * 3})
    birth = pd.DataFrame({"player_bref_id": ["martike04"], "birth_date": ["December 11, 2000"]})
    sb = pd.DataFrame({"player_id": [4], "birth_date": [pd.Timestamp("2000-12-11")]})
    xw, _ = xwalk.build(ids, names, appear, {"OKC": 10, "HOU": 20}, birth, sb)
    got = dict(zip(xw.player_bref_id, xw.player_id, strict=True))
    # willija02/03 share the stem 'willija': both candidates played for OKC; the exact-name tie-break picks by the page name
    assert got["willija02"] == 1 and got["willija03"] == 2
    assert got["martike04"] == 4  # KJ Martin by birth date and team-season evidence
    assert "ghostgh01" not in got
    un = xwalk.unresolved(ids, xw, pd.DataFrame({"player_bref_id": ["ghostgh01"]}))
    assert list(un.player_bref_id) == ["ghostgh01"]


def test_bootstrap_clusterings_binding_is_the_widest_and_top5_share():
    rng = np.random.default_rng(1)
    n = 300
    player = np.repeat(np.arange(60), 5)
    shared = rng.normal(0, 1, 60)[player]
    d = shared + rng.normal(0, 0.1, n)
    game = np.arange(n)
    week = player % 10
    r = ev.three_way(d, game, player, week)
    assert r["binding"]["half_width"] == max(r[k]["half_width"] for k in ev.CLUSTERINGS)
    assert (
        r["player_season"]["half_width"] > r["game"]["half_width"] * 2
    )  # the T500 pattern: player-shared effect widens the CI
    one = np.zeros(n)
    one[:5] = -10.0  # five rows carry the whole gain
    assert ev.top5_share(one, np.arange(n)) == pytest.approx(1.0)
    assert np.isnan(ev.top5_share(np.ones(n), np.arange(n)))


def _cell(point, hi, p, top5=0.3, pl=0.0, harm_lo=-0.01):
    return {"point": point, "hi": hi, "p": p, "top5": top5, "pl_point": pl, "harm_lo": harm_lo}


def _res(f):
    return {s: {st: f(s, st) for st in rule.STATS} for s in ("2023", "2024")}


def test_verdict_words_reachable():
    good = lambda s, st: _cell(-rule.FLOORS[st] * 2, -0.0001, 0.001)  # noqa: E731
    assert ev.verdict(_res(good))["word"] == "PASS"
    assert (
        ev.verdict(_res(lambda s, st: good(s, st) if st != "pts" else _cell(0.0, 0.01, 0.5)))[
            "word"
        ]
        == "PASS_OTHER_STAT"
    )
    assert (
        ev.verdict(
            _res(lambda s, st: _cell(-rule.FLOORS[st] * 2, -0.0001, 0.001, pl=-rule.FLOORS[st] * 2))
        )["word"]
        == "PLACEBO_TOO"
    )
    assert (
        ev.verdict(_res(lambda s, st: _cell(-rule.FLOORS[st] * 2, -0.0001, 0.001, top5=0.9)))[
            "word"
        ]
        == "FAIL_GUARD"
    )
    assert (
        ev.verdict(
            _res(
                lambda s, st: (
                    good(s, st) if (s == "2024" and st == "pts") else _cell(0.0, 0.01, 0.5)
                )
            )
        )["word"]
        == "FAIL_ONE_SEASON"
    )
    assert ev.verdict(_res(lambda s, st: _cell(0.0, 0.01, 0.5)))["word"] == "FAIL_NO_GAIN"


def test_bh_by_hand():
    assert ev.bh_adjust(np.array([0.01, 0.02, 0.03, 0.04])) == pytest.approx(
        [0.04, 0.04, 0.04, 0.04]
    )


def _grid(n=400, rng=None):
    rng = rng or np.random.default_rng(0)
    mean = rng.uniform(5, 25, n)
    z = np.sort(rng.standard_normal(199))
    return np.maximum(mean[:, None] + 0.45 * np.sqrt(mean)[:, None] * z[None, :], 0.0)


def test_positive_control_detects_a_planted_role_shift_and_null_does_not():
    q0 = _grid(800)
    player = np.array([f"p{i // 4}" for i in range(800)])
    game = np.array([f"g{i // 8}" for i in range(800)])
    week = np.array([f"w{i // 40}" for i in range(800)])
    r = ev.sim_cell(q0, player, game, week, 0.3, 1.0, 5, arch.crps_int_q, n_boot=300)
    assert (
        r["binding"]["hi"] < 0 and r["binding"]["point"] < -0.05
    )  # the harness sees an oracle prior
    nz = ev.noise_arm(q0, player, game, week, 0.3, 5, arch.crps_int_q, n_boot=300)
    assert nz["binding"]["point"] > 0  # an uninformative prior only hurts


def test_run_refuses_without_a_frozen_rule(tmp_path):
    with pytest.raises(SystemExit):
        ev.require_frozen(None)
    doc = tmp_path / "d.md"
    doc.write_text("draft only")
    with pytest.raises(SystemExit):
        ev.require_frozen("abc", doc)
    doc.write_text("rule text\n" + ev.MARKER + "\nresults")
    with pytest.raises(SystemExit):
        ev.require_frozen("0" * 64, doc)  # hash mismatch
    with pytest.raises(SystemExit):
        ev.require_frozen(ev.frozen_sha(doc), doc)  # right hash, no freeze commit
