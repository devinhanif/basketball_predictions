# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX scorer: the blend's past-only property, the permutation helper, the decision rule failing each way, the touch refusals."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.eval import seq_full_mix_eval as E

ROOT = Path(__file__).resolve().parents[3]
MARK = "=== RESULTS BELOW ===\n"


def test_within_date_perm_is_a_permutation_inside_each_date_and_seeded() -> None:
    d = np.repeat(np.arange(20), 7)
    p = E.within_date_perm(d, 3)
    assert sorted(p) == list(range(len(d))) and np.array_equal(d[p], d)
    assert np.array_equal(p, E.within_date_perm(d, 3)) and not np.array_equal(
        p, E.within_date_perm(d, 4)
    )
    assert (p != np.arange(len(d))).mean() > 0.7


def test_seqc_routes_regular_rows_to_the_blend_and_the_rest_to_a0() -> None:
    a0 = np.zeros((4, 199), dtype=np.int16)
    blend = np.full((4, 199), 7.0)
    reg = np.array([1.0, 0.0, 1.0, 0.0])
    out = E.seqc_grid(a0, blend, reg)
    assert (
        (out[0] == 7).all() and (out[2] == 7).all() and (out[1] == 0).all() and (out[3] == 0).all()
    )
    assert E.regular_flag(np.array(["0022300001", "0042300001", "0052300001"])).tolist() == [
        1.0,
        0.0,
        0.0,
    ]


def test_equal_mixture_of_a_grid_with_itself_is_itself() -> None:
    q = np.sort(np.random.default_rng(0).integers(0, 30, (6, 199)), axis=1).astype(np.int16)
    assert np.array_equal(E.mix_half(q, q), q)


def _toy_blend(n_per: int = 4000):
    rng = np.random.default_rng(0)
    season = np.repeat([2023, 2024], n_per)
    days = np.concatenate(
        [np.sort(rng.integers(19600, 19760, n_per)), np.sort(rng.integers(19970, 20130, n_per))]
    )
    n = 2 * n_per
    first = np.sort(rng.gamma(3.0, 2.0, (n, 199)), axis=1)
    seq = first + rng.normal(0, 0.3, (n, 1))
    y = rng.poisson(6.0, n).astype(float)
    ctx = pd.DataFrame(
        {
            "log_n_prior": rng.normal(size=n),
            "starter10": rng.random(n),
            "n_rep_out": rng.integers(0, 3, n),
            "has_report": rng.integers(0, 2, n),
            "team_game_no": rng.integers(0, 82, n),
        }
    )
    return first, seq, y, days, season, ctx


def test_blend_is_past_only_and_the_cap_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from research.eval import near_miss_bundle_eval as N

    first, seq, y, days, season, ctx = _toy_blend()
    mine, _ = E.learned_blend(first, seq, y, days, season, ctx, "pts")
    theirs, _ = N.learned_blend(first, seq, y, days, season, ctx, "pts")
    assert np.array_equal(mine, theirs)  # line-for-line equal to T442's blend under the 2024 cap
    n = len(y)
    m = season == 2024
    first2, seq2, y2 = (
        np.concatenate([first, first[m]]),
        np.concatenate([seq, seq[m]]),
        np.concatenate([y, y[m]]),
    )
    ctx2 = pd.concat([ctx, ctx[m]], ignore_index=True)
    s2 = np.concatenate([season, np.full(int(m.sum()), 2025)])
    d2 = np.concatenate([days, days[m] + 400])
    with pytest.raises(ValueError, match="season > 2024|frozen"):
        E.learned_blend(
            first2, seq2, y2, d2, s2, ctx2, "pts"
        )  # the wall holds unless the cap is passed explicitly
    later, _ = E.learned_blend(first2, seq2, y2, d2, s2, ctx2, "pts", cap=2025)
    assert np.array_equal(later[:n], mine)  # appending later rows changes no earlier row
    from research.stack import router as RT

    assert RT.FROZEN_SEASON == 2025  # restored


def _res(target: int = 2025, **over: float) -> dict:
    def cell(d: float, hi: float | None = None, p: float = 0.001) -> dict:
        return {
            "n": 28000,
            "n_games": 1300,
            "delta": d,
            "ci_lo": d - 0.003,
            "ci_hi": d + 0.003 if hi is None else hi,
            "p": p,
            "mde": 0.003,
        }

    t = str(target)
    c = {
        "AVG_minus_FULL": {t: cell(over.get("prim", -0.008))},
        "AVG_minus_A0": {t: cell(over.get("a0", -0.02))},
        "AVG_minus_AVG_P": {t: cell(over.get("pl", -0.006))},
        "AVG_minus_AVG_S": {t: cell(over.get("sh", -0.006))},
        "AVG_minus_AVG_A0": {t: cell(over.get("mx", -0.007))},
    }
    return {
        "stats": {
            "pts": {
                "contrasts": c,
                "guards_AVG": {t: {"guards_all_pass": over.get("guards", 1.0) == 1.0}},
            }
        },
        "null": {"per_season": {t: {"mean": -0.0008, "sd": 0.0001}}},
    }


def test_decision_rule_passes_and_fails_each_way() -> None:
    ok = E.decide(_res(), True, 2025, 0.0002)
    assert ok["pass"] and all(ok["items"].values())
    assert not E.decide(_res(prim=-0.004), True, 2025, 0.0002)["items"][
        "primary_AVG_minus_FULL"
    ]  # above the floor
    assert not E.decide(_res(), False, 2025, 0.0002)["pass"]  # a failed gate
    bad_ci = _res()
    bad_ci["stats"]["pts"]["contrasts"]["AVG_minus_FULL"]["2025"]["ci_hi"] = 0.001
    assert not E.decide(bad_ci, True, 2025, 0.0002)["items"][
        "primary_AVG_minus_FULL"
    ]  # CI reaches 0
    assert not E.decide(_res(a0=-0.004), True, 2025, 0.0002)["items"]["AVG_minus_A0"]
    assert not E.decide(_res(guards=0.0), True, 2025, 0.0002)["items"]["router_g2_guards_pts"]
    assert not E.decide(_res(pl=-0.002), True, 2025, 0.0002)["items"]["placebo_AVG_minus_AVG_P"]
    assert not E.decide(_res(sh=-0.002), True, 2025, 0.0002)["items"][
        "shuffled_input_AVG_minus_AVG_S"
    ]
    assert not E.decide(_res(mx=-0.002), True, 2025, 0.0002)["items"][
        "mixture_control_AVG_minus_AVG_A0"
    ]
    r = _res(prim=-0.005)
    r["null"]["per_season"]["2025"] = {"mean": -0.0049, "sd": 0.0002}
    assert not E.decide(r, True, 2025, 0.0002)["items"][
        "null_real_beyond_permutation"
    ]  # no better than a permuted member
    missing = _res()
    del missing["stats"]["pts"]["contrasts"]["AVG_minus_AVG_P"]
    assert not E.decide(missing, True, 2025, 0.0002)["items"][
        "placebo_AVG_minus_AVG_P"
    ]  # a missing placebo can never pass


def test_require_unspent_blocks_a_spent_or_started_touch(tmp_path: Path) -> None:
    E.require_unspent(tmp_path, False)
    (tmp_path / "touch_started.json").write_text("{}")
    with pytest.raises(RuntimeError, match="consumed"):
        E.require_unspent(tmp_path, False)
    E.require_unspent(tmp_path, True, {})
    (tmp_path / "results.json").write_text("{}")
    with pytest.raises(RuntimeError, match="spent"):
        E.require_unspent(tmp_path, True)


def test_the_doc_states_the_code_constants() -> None:
    doc = (ROOT / "docs/prereg/SEQ_FULL_MIX.md").read_text().split(MARK)[0]
    for needle in (
        f"{E.FLOOR}",
        f"{E.PLACEBO_MARGIN}",
        f"{E.MIXTURE_MARGIN}",
        f"K = {E.NULL_K}",
        f"{E.SIGMA0_PRE:.5f}",
    ):
        assert needle in doc, needle


def _toy_base(n: int = 2400, season: int = 2025) -> dict:
    rng = np.random.default_rng(1)
    lam = rng.gamma(4.0, 2.0, n)
    y = rng.poisson(lam).astype(float)
    taus = (np.arange(199) + 0.5) / 199
    from scipy.stats import poisson

    a0 = np.stack([poisson.ppf(taus, mu) for mu in lam]).astype(np.int16)
    gid = np.array([f"00{rng.choice([2, 2, 2, 4])}2500{i // 20:04d}" for i in range(n)])
    return {
        "game_id": gid, "player_id": np.arange(n), "season": np.full(n, season), "y": y, "minutes": rng.uniform(0, 36, n),
        "starter10": rng.random(n), "n_prior": rng.integers(0, 60, n), "team_game_no": rng.integers(0, 82, n),
        "n_rep_out": rng.integers(0, 3, n), "tip_hour": rng.choice([17.0, 19.5, 20.0], n), "team_id": rng.integers(1, 31, n),
        "a0_int": a0,
    }  # fmt: skip


def test_guards_run_on_a_season_with_no_dk_lines_and_use_the_fixed_thresholds() -> None:
    b = _toy_base()
    out = E.arm_guards("pts", b, b["a0_int"], (2025,), dk_seasons=(2023, 2024))
    g = out["2025"]["guards"]
    assert set(g) >= {
        "g1_calibration",
        "g2_slices",
        "g3_concentration",
        "g4_thresholds",
        "g5_proxy_safe",
        "g2b_minutes<5",
    }
    assert set(out["2025"]["tll_fixed"]) == {"20", "25", "30"} and out["2025"]["tll_dk"] is None
    assert all(
        abs(v) < 1e-12 for v in out["2025"]["tll_fixed"].values()
    )  # the arm IS A0, so the log-loss difference is exactly 0
    assert g["g4_thresholds"] is True
    worse = np.clip(b["a0_int"] + 3, 0, None).astype(
        np.int16
    )  # a clearly worse arm must fail the fixed-threshold guard
    assert E.arm_guards("pts", b, worse, (2025,), ())["2025"]["guards"]["g4_thresholds"] is False
    assert E.SELECT_SEASONS == (2023, 2024) and E.HOLDOUT_SEASON == 2025
