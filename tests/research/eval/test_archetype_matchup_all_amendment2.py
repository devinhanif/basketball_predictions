# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL Amendment 2: M1 share guard, M2 alignment, M3 arm/shuffle/knockouts/pin, M4 verdict, S1-S3. Synthetic and planted data only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_arm as arm
from research.eval import archetype_matchup_all_eval as mev
from research.eval import archetype_matchup_all_features as af
from research.eval import archetype_matchup_all_gate as gate
from research.eval import archetype_matchup_all_rule as rule
from research.eval import archetype_matchup_features as feat
from research.eval import matchup_availability_features as t505

ROOT = Path(__file__).resolve().parents[3]
SHUF = [-0.1] * rule.N_SHUFFLE


def _parts(points: dict[str, float]) -> dict[str, float]:
    """Per-stat floor-unit deltas -> parts of CM (each / 4)."""
    return {s: v / len(rule.STATS) for s, v in points.items()}


EVEN = _parts({"pts": -1.2, "reb": -1.2, "ast": -1.2, "fg3m": -1.2})


def _verdict(**kw: Any) -> dict[str, Any]:
    a: dict[str, Any] = {
        "select_cm_point": -1.2,
        "report_cm_point": -1.2,
        "report_cm_hi": -0.2,
        "report_stat_parts": EVEN,
        "report_top5_share": 0.3,
        "report_shuffled_cm_points": SHUF,
        "report_identity_only_cm_point": -0.1,
        "planted_future_ok": True,
        "minutes_leak_void": False,
    }
    a.update(kw)
    return rule.primary_verdict(**a)


# --------------------------------------------------------------------------- M1, M4, S1, S2: the verdict


def test_verdict_passes_only_when_all_criteria_hold() -> None:
    v = _verdict()
    assert v["pass"] and v["failed"] == []
    assert "cm_point" in _verdict(report_cm_point=-0.9)["failed"]
    assert "ci_upper_below_zero" in _verdict(report_cm_hi=0.1)["failed"]
    assert "select_season_point" in _verdict(select_cm_point=-0.5)["failed"]
    assert "top5_share" in _verdict(report_top5_share=0.51)["failed"]
    assert "planted_future_identical" in _verdict(planted_future_ok=False)["failed"]
    assert "no_minutes_leak" in _verdict(minutes_leak_void=True)["failed"]


def test_m1_fg3m_only_fails_the_share_criterion() -> None:
    """The adversary's case: fg3m -4 floors, the other three stats at 0: CM = -1.0, every old criterion holds, share = 1.0."""
    parts = _parts({"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": -4.0})
    v = _verdict(report_cm_point=-1.0, report_stat_parts=parts)
    assert not v["pass"] and v["failed"] == ["max_stat_share"]
    assert v["criteria"]["cm_point"] and v["criteria"]["no_stat_hurt"]


def test_m1_even_four_stat_effect_passes_and_boundary_share() -> None:
    assert _verdict(report_stat_parts=_parts({s: -1.0 for s in rule.STATS}), report_cm_point=-1.0)[
        "pass"
    ]
    # share exactly 0.5 passes (at most), just above fails
    half = _parts({"pts": -2.0, "reb": 0.0, "ast": -1.0, "fg3m": -1.0})  # CM -1.0, pts share 0.5
    assert _verdict(report_cm_point=-1.0, report_stat_parts=half)["criteria"]["max_stat_share"]
    over = _parts({"pts": -2.2, "reb": 0.0, "ast": -0.9, "fg3m": -0.9})
    assert not _verdict(report_cm_point=-1.0, report_stat_parts=over)["criteria"]["max_stat_share"]


def test_m1_no_stat_hurt_and_hurt_counts_toward_share() -> None:
    hurt = _parts({"pts": 1.01, "reb": -3.0, "ast": -3.0, "fg3m": -3.0})
    v = _verdict(report_cm_point=float(sum(hurt.values())), report_stat_parts=hurt)
    assert "no_stat_hurt" in v["failed"]
    ok = _parts({"pts": 1.0, "reb": -2.0, "ast": -2.0, "fg3m": -2.0})
    assert _verdict(report_cm_point=float(sum(ok.values())), report_stat_parts=ok)["criteria"][
        "no_stat_hurt"
    ]


def test_s1_shuffled_control_is_the_mean_of_five_and_strictly_under_half() -> None:
    assert _verdict(report_shuffled_cm_points=[-0.59] * 5)["pass"]
    assert "shuffled_keeps_under_half" in _verdict(report_shuffled_cm_points=[-0.6] * 5)["failed"]
    # one seed keeping 100% does not decide by itself: the MEAN is what is compared
    assert _verdict(report_shuffled_cm_points=[-1.2, 0.0, 0.0, 0.0, 0.0])["pass"]
    with pytest.raises(AssertionError):
        _verdict(report_shuffled_cm_points=[-0.1] * 4)


def test_s2_identity_only_arm_keeping_half_voids() -> None:
    assert _verdict(report_identity_only_cm_point=-0.59)["pass"]
    assert (
        "identity_only_keeps_under_half" in _verdict(report_identity_only_cm_point=-0.6)["failed"]
    )


def test_keep_share() -> None:
    assert rule.keep_share(-0.3, -1.2) == pytest.approx(0.25)
    assert rule.keep_share(0.2, -1.2) == pytest.approx(-1 / 6)
    assert rule.keep_share(-0.3, 0.1) is None


def test_m4_minutes_leak_needs_both_the_signature_and_the_gain() -> None:
    audit = {
        "off_arch": {"gap_realised": 0.70, "gap_pre": 0.64},
        "e_usual": {"gap_realised": 0.55, "gap_pre": 0.20},
    }
    keeps_low: dict[str, float | None] = {"identity": 0.2, "shift": 0.9, "unknown": 0.9}
    keeps_high: dict[str, float | None] = {"identity": 0.8, "shift": 0.9, "unknown": 0.9}
    # off_arch: realised gap 0.70 >= 0.30 but only 0.06 above the pre-tip gap -> not a minutes-tracker; e_usual: 0.35 above -> tracker
    assert rule.minutes_leak(audit, keeps_low) == {"void": True, "columns": ["e_usual"]}
    assert rule.minutes_leak(audit, keeps_high)["void"] is False
    assert (
        rule.minutes_leak({"off_arch": {"gap_realised": 0.29, "gap_pre": 0.0}}, keeps_low)["void"]
        is False
    )
    assert (
        rule.minutes_leak({"shift_l1": {"gap_realised": 0.5, "gap_pre": 0.1}}, {"shift": None})[
            "void"
        ]
        is False
    )


# --------------------------------------------------------------------------- M2: alignment


def _population(seed: int, flip: bool) -> pd.DataFrame:
    """Players drawn around 6 well-separated archetypes; ``flip`` swaps the ordering of two clusters' first-column means (what moves raw labels)."""
    rng = np.random.default_rng(seed)
    cols = t505.OFF_FEATURES
    centers = np.array(
        [
            [i * 3.0 + (0.4 if i == 4 and flip else 0) - (0.4 if i == 5 and flip else 0)]
            + [((i * 7 + j) % 5) * 1.5 for j in range(1, len(cols))]
            for i in range(6)
        ]
    )
    if flip:
        centers[4, 0], centers[5, 0] = centers[5, 0], centers[4, 0]
    lab = np.repeat(np.arange(6), 60)
    x = centers[lab] + rng.normal(0, 0.35, size=(len(lab), len(cols)))
    df = pd.DataFrame(x, columns=cols)
    df["player_id"] = np.arange(len(lab))
    pos = np.eye(3)[lab % 3]
    for k, nm in enumerate(af.POS_COLS):
        df[nm] = pos[:, k]
    return df


def test_alignment_repairs_a_swapped_ordering_and_reports_agreement() -> None:
    ref = _population(0, flip=False)
    cur = _population(1, flip=True)
    ref_lab = t505.assign_archetypes(ref, t505.OFF_FEATURES, 6)
    cur_lab = t505.assign_archetypes(cur, t505.OFF_FEATURES, 6)
    raw, _ = af.agreement(ref_lab, cur_lab)
    aligned, rep = af.align_pair(ref, ref_lab, cur, cur_lab, t505.OFF_FEATURES)
    assert raw < 0.8, "the test population must actually drift (otherwise it proves nothing)"
    assert rep["agreement_before"] == pytest.approx(raw)
    assert rep["agreement_after"] >= rule.ALIGN_MIN_AGREE and rep["agreement_after"] > 0.95
    assert rep["n_players_in_both"] == 360
    assert sorted(rep["mapping"].values()) == list(range(6)), "the map is a bijection"
    assert aligned.index.equals(cur_lab.index)


def test_canonical_frame_makes_the_labels_a_function_of_the_data_not_of_row_order() -> None:
    f = _population(7, flip=False)
    shuffled = f.sample(frac=1.0, random_state=3).reset_index(drop=True)
    assert not f.player_id.equals(shuffled.player_id)
    a, b = af.canonical_frame(f), af.canonical_frame(shuffled)
    pd.testing.assert_frame_equal(a, b)
    la = t505.assign_archetypes(a, t505.OFF_FEATURES, 6)
    lb = t505.assign_archetypes(b, t505.OFF_FEATURES, 6)
    assert la.equals(lb)


def test_alignment_is_identity_when_nothing_drifted() -> None:
    ref = _population(0, flip=False)
    lab = t505.assign_archetypes(ref, t505.OFF_FEATURES, 6)
    aligned, rep = af.align_pair(ref, lab, ref, lab, t505.OFF_FEATURES)
    assert (aligned == lab).all() and rep["agreement_after"] == 1.0
    assert all(int(k) == v for k, v in rep["mapping"].items())


def test_agreement_only_counts_players_in_both() -> None:
    a = pd.Series([0, 1, 2], index=[1, 2, 3])
    b = pd.Series([0, 2], index=[1, 2])
    assert af.agreement(a, b) == (0.5, 2)
    assert np.isnan(af.agreement(a, pd.Series([0], index=[9]))[0])


def _mix(perm: np.ndarray | None) -> pd.DataFrame:
    """T506 mix of a 2-defender roster with the defender archetype labels optionally permuted (tables permuted consistently)."""
    days = 10
    rows = []
    for d in range(days + 1):
        gid = f"002000{d:02d}"
        date = pd.Timestamp("2023-11-01") + pd.Timedelta(days=d)
        for dfn, mm in ((10, 30.0), (11, 10.0)):
            rows.append((gid, date, 2023, 1, dfn, 100, 200, mm, 20.0, 8.0))
    mu = pd.DataFrame(
        rows,
        columns=[
            "game_id",
            "game_date",
            "season",
            "off_player_id",
            "def_player_id",
            "off_team",
            "def_team",
            "mm",
            "poss",
            "pts",
        ],
    )
    n = feat.rule.N_DEF
    R = np.full((feat.rule.N_OFF, n), 0.2)
    R[:, 0], R[:, 1] = 0.1, 0.3
    lift = np.ones((feat.rule.N_OFF, n))
    lift[:, 0] = 1.5
    lab = {10: 0, 11: 1}
    if perm is not None:
        full = np.arange(n)
        full[: len(perm)] = perm
        R = R[:, np.argsort(full)]
        lift = lift[:, np.argsort(full)]
        lab = {k: int(full[v]) for k, v in lab.items()}
    rows_df = pd.DataFrame(
        {
            "game_id": ["00200010"],
            "player_id": [1],
            "team_id": [100],
            "game_date": [pd.Timestamp("2023-11-11")],
            "min10": [30.0],
            "n_prior": [50],
        }
    )
    games = pd.DataFrame({"game_id": ["00200010"], "home_team": [100], "away_team": [200]})
    return feat.mix_frame(
        rows_df,
        mu,
        games,
        {"00200010": {10}},
        2023,
        {"lift": lift, "R": R, "lg": 0.2},
        pd.Series({1: 0}),
        pd.Series(lab),
    )


def test_defender_label_relabelling_cannot_change_a_feature() -> None:
    """Why defender labels are aligned for the report only: the six features are invariant to a relabelling of defender archetypes."""
    base = _mix(None)
    swapped = _mix(np.array([3, 1, 2, 0, 4, 5]))
    for c in ("e_usual", "delta_rate", "shift_l1", "retained", "def_unknown_share"):
        assert base[c].iloc[0] == pytest.approx(swapped[c].iloc[0]), c


def test_identity_feature_is_a_function_of_off_arch_and_tables() -> None:
    A = np.array([[0.5, 0.5], [1.0, 0.0]])
    R = np.array([[0.1, 0.3], [0.5, 0.9]])
    e = af.identity_feature({"A": A, "R": R, "lg": 0.2}, np.array([0, 1, 1, 0]))
    np.testing.assert_allclose(e, [0.0, 0.3, 0.3, 0.0], atol=1e-12)


def test_missingness_vs_realised_detects_a_column_that_tracks_tonights_minutes() -> None:
    n = 4000
    rng = np.random.default_rng(0)
    mins = rng.uniform(0, 40, n)
    pre = rng.uniform(5, 35, n)  # pre-tip role is unrelated to tonight's minutes here
    bucket = np.where(pre < 10, "lt10", np.where(pre >= 28, "ge28", "mid"))
    sf = pd.DataFrame(
        {
            "bucket": bucket,
            "off_arch": rng.integers(0, 7, n),
            "no_roster": mins < 5,  # tracks REALISED minutes
            "delta_rate": rng.normal(size=n),
            "shift_l1": rng.uniform(size=n),
            "retained": rng.uniform(size=n),
            "def_unknown_share": rng.uniform(size=n) * 0.4,
        }
    )
    out = af.missingness_vs_realised(sf, mins)
    assert out["e_usual"]["gap_realised"] > 0.9 and out["e_usual"]["gap_pre"] < 0.1
    assert out["off_arch"]["gap_realised"] < 0.1
    assert rule.minutes_leak(
        {"e_usual": out["e_usual"]}, {"identity": 0.1, "shift": 1, "unknown": 1}
    )["void"]


# --------------------------------------------------------------------------- M3: arm M


def _synthetic(
    n_months: int = 6, per: int = 1500, seed: int = 0, effect: float = 0.0
) -> arm.StatPool:
    rng = np.random.default_rng(seed)
    n = n_months * per
    month = np.repeat([f"2023-{m:02d}" for m in range(10, 10 + n_months)], per)
    mean = rng.uniform(2.0, 14.0, n)
    z = rng.normal(size=n)
    x = pd.DataFrame(
        {
            "off_arch": rng.integers(0, 7, n).astype(float),
            "e_usual": rng.normal(size=n),
            "delta_rate": z,
            "shift_l1": rng.uniform(0, 1.5, n),
            "retained": rng.uniform(0.3, 1.0, n),
            "def_unknown_share": rng.uniform(0, 0.5, n),
            "e_lg": rng.normal(size=n),
        }
    )
    y = rng.poisson(mean * np.exp(effect * z)).astype(float)
    q0 = np.sort(rng.poisson(mean[:, None], size=(n, arch.NQ)).astype(float), axis=1)
    shuf = {
        s: x.assign(delta_rate=rng.permutation(z), e_usual=rng.permutation(x.e_usual.to_numpy()))
        for s in rule.SHUFFLE_SEEDS
    }
    return arm.StatPool("pts", q0, y, arm.a0_mean(q0), month, np.ones(n, bool), x, shuf)


def test_constants_are_in_code_and_match_the_rule_text() -> None:
    p = arm.LGBM_PARAMS
    assert (
        rule.N_LEAVES,
        rule.N_TREES,
        rule.LEARNING_RATE,
        rule.MIN_DATA_IN_LEAF,
        rule.LGBM_SEED,
    ) == (4, 150, 0.05, 20, 0)
    assert (
        p["num_leaves"] == 4
        and p["learning_rate"] == 0.05
        and p["min_data_in_leaf"] == 20
        and p["seed"] == 0
    )
    assert p["boost_from_average"] is True and p["deterministic"] is True and p["num_threads"] == 1
    assert p["bagging_fraction"] == 1.0 and p["feature_fraction"] == 1.0
    assert (
        rule.LAMBDA_CLIP == (0.5, 2.0)
        and rule.CAT_COLS == ("off_arch",)
        and rule.MIN_TRAIN_ROWS == 200
    )


def test_zero_features_give_lambda_one_and_exactly_zero_delta() -> None:
    n = 600
    rng = np.random.default_rng(1)
    e = rng.normal(size=n // 2)
    resid = np.r_[e, -e]  # exactly mean-zero residual: the initial score (training mean) is 0
    mean = rng.uniform(2, 10, n)
    x = pd.DataFrame({"a": np.ones(n), "off_arch": np.zeros(n)})
    for cols in ((), ("a", "off_arch")):  # no feature at all; features that are constant
        pred = arm.fit_predict(x, resid, x, cols)
        np.testing.assert_allclose(pred, 0.0, atol=1e-12)
        lam = arm.lam_from(mean, pred)
        assert (lam == 1.0).all()
    q0 = np.sort(rng.poisson(mean[:, None], size=(n, arch.NQ)).astype(float), axis=1)
    y = rng.poisson(mean).astype(float)
    base = arm.crps_rows(q0, y)
    assert (arm.arm_delta(q0, y, np.ones(n), base) == 0.0).all()


def test_constant_features_predict_the_training_mean_recalibration() -> None:
    x = pd.DataFrame({"a": np.ones(300)})
    pred = arm.fit_predict(x, np.full(300, 0.4), x, ("a",))
    np.testing.assert_allclose(pred, 0.4)


def test_planted_feature_effect_is_recovered_and_no_effect_is_not_invented() -> None:
    planted = _synthetic(effect=0.35)
    deltas = arm.run_arms(planted)
    assert deltas["M"].mean() < -0.01, "a planted feature-driven shift must lower the CRPS"
    assert deltas["ko_shift"].mean() > deltas["M"].mean() + 0.005, (
        "dropping delta_rate (in the shift block) must lose the gain"
    )
    assert (
        abs(np.mean([deltas[f"shuffle{s}"].mean() for s in rule.SHUFFLE_SEEDS]))
        < abs(deltas["M"].mean()) / 2
    )
    assert abs(deltas["identity_only"].mean()) < abs(deltas["M"].mean()) / 2
    null = arm.run_arms(_synthetic(effect=0.0, seed=3))
    assert abs(null["M"].mean()) < 0.01
    first = planted.month == "2023-10"
    assert (deltas["M"][first] == 0.0).all(), "the first month has no earlier row: served as A0"


def test_strictly_earlier_months_only_and_planted_future() -> None:
    p = _synthetic(effect=0.3, n_months=5)
    cols = tuple(rule.FEATURES)
    resid = p.y - p.mean
    lam = arm.walk_forward_lambda(p.x, resid, p.mean, p.month, p.is_score, cols)
    cut = "2023-12"
    fut = p.month >= cut
    rng = np.random.default_rng(9)
    y2, x2 = p.y.copy(), p.x.copy()
    y2[fut] = rng.poisson(30, int(fut.sum()))
    # later months' outcomes are scrambled; features of the cut month and later stay (a row's own features are known pre-tip), later months' features scrambled
    later = p.month > cut
    x2.loc[later, "delta_rate"] = 99.0
    lam2 = arm.walk_forward_lambda(x2, y2 - p.mean, p.mean, p.month, p.is_score, cols)
    sel = p.month <= cut
    np.testing.assert_array_equal(lam[sel], lam2[sel])
    assert not np.array_equal(lam[~sel], lam2[~sel])


def test_training_pool_never_contains_the_scored_month_or_later() -> None:
    seen: list[tuple[int, str]] = []
    orig = arm.fit_predict

    def spy(x_tr: pd.DataFrame, r: np.ndarray, x_te: pd.DataFrame, cols: Any) -> np.ndarray:
        seen.append((len(x_tr), ""))
        return orig(x_tr, r, x_te, cols)

    p = _synthetic(n_months=4, per=500)
    arm.fit_predict = spy  # type: ignore[assignment]
    try:
        arm.walk_forward_lambda(
            p.x, p.y - p.mean, p.mean, p.month, p.is_score, tuple(rule.FEATURES)
        )
    finally:
        arm.fit_predict = orig  # type: ignore[assignment]
    assert [n for n, _ in seen] == [
        500,
        1000,
        1500,
    ]  # months 2, 3, 4 train on 1, 2, 3 earlier months


def test_pool_spanning_two_seasons_scores_only_the_report_rows() -> None:
    p = _synthetic(n_months=5, per=400)
    month = np.where(
        np.arange(len(p.month)) < 2 * 400,
        p.month,
        np.char.replace(p.month.astype(str), "2023", "2024"),
    )
    # first two months stay 2023-xx; the rest 2024-xx: keys sort 2023 < 2024
    is_score = np.arange(len(month)) >= 2 * 400
    lam = arm.walk_forward_lambda(p.x, p.y - p.mean, p.mean, month, is_score, tuple(rule.FEATURES))
    assert len(lam) == int(is_score.sum())
    # the first scored month has the two earlier (2023) months in its pool: it is fitted, not served as A0
    assert (lam[:400] != 1.0).any()


def test_categorical_off_arch_recovers_a_non_monotone_category_effect() -> None:
    rng = np.random.default_rng(5)
    n = 4000
    cat = rng.integers(0, 7, n).astype(float)
    effect = np.where(
        np.isin(cat, [1, 4]), 0.4, -0.2
    )  # categories 1 and 4 high, the rest low (not ordered in the label)
    x = pd.DataFrame({"off_arch": cat, "e_usual": rng.normal(size=n)})
    resid = effect + rng.normal(0, 0.3, n)
    cat_pred = arm.fit_predict(x, resid, x, ("off_arch",))
    assert np.corrcoef(cat_pred, effect)[0, 1] > 0.9


def test_run_is_deterministic_and_chunked_crps_equals_unchunked() -> None:
    p = _synthetic(n_months=3, per=500, effect=0.2)
    a = arm.run_arms(p)
    b = arm.run_arms(p)
    assert all(np.array_equal(a[k], b[k]) for k in a)
    rng = np.random.default_rng(2)
    q = np.sort(rng.poisson(6, size=(900, arch.NQ)).astype(float), axis=1)
    y = rng.poisson(6, 900).astype(float)
    np.testing.assert_array_equal(arm.crps_rows(q, y, chunk=250), arch.crps_int_q(q, y))


def test_skipping_unit_lambda_rows_is_exactly_the_full_computation() -> None:
    """Equivalence proof of the only optimisation: rows with lambda == 1 score exactly 0 either way."""
    rng = np.random.default_rng(4)
    q = np.sort(rng.poisson(5, size=(700, arch.NQ)).astype(float), axis=1)
    y = rng.poisson(5, 700).astype(float)
    lam = np.where(rng.uniform(size=700) < 0.4, 1.0, rng.uniform(0.7, 1.4, 700))
    base = arm.crps_rows(q, y)
    full = arch.crps_int_q(q * lam[:, None], y) - base
    np.testing.assert_array_equal(arm.arm_delta(q, y, lam, base), full)


def test_lambda_clip_and_mean_floor() -> None:
    lam = arm.lam_from(np.array([10.0, 10.0, 0.01, 4.0]), np.array([50.0, -50.0, 5.0, 0.0]))
    np.testing.assert_allclose(lam, [2.0, 0.5, 1.0, 1.0])


def test_arm_columns_and_knockout_blocks() -> None:
    cols = arm.arm_columns()
    assert cols["M"] == tuple(rule.FEATURES)
    assert (
        "off_arch" not in cols["ko_identity"]
        and "e_usual" not in cols["ko_identity"]
        and len(cols["ko_identity"]) == 4
    )
    assert cols["ko_shift"] == ("off_arch", "e_usual", "def_unknown_share")
    assert cols["ko_all_features"] == () and cols["identity_only"] == ("off_arch", "e_lg")
    assert set(rule.BLOCK_OF) == set(rule.FEATURES) and set(rule.BLOCK_OF.values()) == set(
        rule.KNOCKOUTS
    ) - {"all_features"}


def test_run_arms_emits_every_arm_and_shuffles_use_the_shuffled_frame() -> None:
    p = _synthetic(n_months=3, per=500, effect=0.3)
    d = arm.run_arms(p)
    assert set(d) == {
        "M",
        "ko_identity",
        "ko_shift",
        "ko_unknown",
        "ko_all_features",
        "identity_only",
        *(f"shuffle{s}" for s in rule.SHUFFLE_SEEDS),
    }
    assert not np.array_equal(d["M"], d["shuffle0"])


# --------------------------------------------------------------------------- M3: harness pin


def test_harness_files_exist_and_cover_every_module_of_the_rule() -> None:
    for rel in rule.HARNESS_FILES:
        assert (ROOT / rel).is_file(), rel
    for m in ("rule", "features", "arm", "gate", "eval"):
        assert f"research/eval/archetype_matchup_all_{m}.py" in rule.HARNESS_FILES
    assert len(set(rule.HARNESS_FILES)) == len(rule.HARNESS_FILES)
    blobs = rule.disk_blobs()
    assert set(blobs) == set(rule.HARNESS_FILES) and all(len(v) == 64 for v in blobs.values())
    assert rule.pin_of(blobs) == rule.pin_of(dict(reversed(list(blobs.items()))))


def _fake_repo(
    tmp: Path, edit_after_pin: bool = False, bad_pin: bool = False
) -> tuple[Any, Any, Path, str]:
    root = tmp / "repo"
    store: dict[str, str] = {}
    for rel in rule.HARNESS_FILES:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}\n")
        store[rel] = f"# {rel}\n"
    doc = "rule text\n" + rule.MARKER + "\nresults\n"
    sha = rule.text_sha(doc)
    blobs = {rel: __import__("hashlib").sha256(t.encode()).hexdigest() for rel, t in store.items()}
    pin = "0" * 64 if bad_pin else rule.pin_of(blobs)
    log_lines = f"c3\tharness({rule.ID}): {pin}\nc2\tother\nc1\tprereg({rule.ID}): {sha}\n"

    def run(*args: str) -> str:
        if args[:2] == ("log", "--all"):
            return log_lines
        if args[0] == "show":
            ref, _, rel = args[1].partition(":")
            return doc if rel == rule.DOC_REL else store[rel]
        raise AssertionError(args)

    if edit_after_pin:
        (root / rule.HARNESS_FILES[0]).write_text("# edited after the pin\n")
    return run, (lambda *a: True), root, sha


def test_harness_pin_accepts_the_pinned_files(tmp_path: Path) -> None:
    run, ok, root, sha = _fake_repo(tmp_path)
    out = rule.require_harness_pinned(sha, run, root, ok)
    assert set(out) == set(rule.HARNESS_FILES)


def test_harness_pin_refuses_an_edit_after_the_pin_a_bad_pin_and_no_ancestry(
    tmp_path: Path,
) -> None:
    run, ok, root, sha = _fake_repo(tmp_path / "a", edit_after_pin=True)
    with pytest.raises(RuntimeError, match="differs"):
        rule.require_harness_pinned(sha, run, root, ok)
    run, ok, root, sha = _fake_repo(tmp_path / "b", bad_pin=True)
    with pytest.raises(RuntimeError, match="does not match"):
        rule.require_harness_pinned(sha, run, root, ok)
    run, _ok, root, sha = _fake_repo(tmp_path / "c")
    with pytest.raises(RuntimeError, match="descend"):
        rule.require_harness_pinned(sha, run, root, lambda *a: False)
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_harness_pinned("f" * 64, run, root, lambda *a: True)


def test_run_refuses_without_freeze_or_pin() -> None:
    with pytest.raises(SystemExit):
        mev.run(None)
    with pytest.raises(SystemExit):
        mev.require_ready("0" * 64)


# --------------------------------------------------------------------------- summaries end to end (synthetic)


def _synthetic_deltas(
    gain_each: float, n: int = 600, seed: int = 0
) -> dict[str, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    out: dict[str, dict[str, np.ndarray]] = {}
    arms = [
        "M",
        "ko_identity",
        "ko_shift",
        "ko_unknown",
        "ko_all_features",
        "identity_only",
        *(f"shuffle{s}" for s in rule.SHUFFLE_SEEDS),
    ]
    for a in arms:
        scale = {"M": 1.0, "identity_only": 0.1}.get(a, 0.5 if a.startswith("ko") else 0.1)
        out[a] = {
            s: -gain_each * scale * rule.FLOORS[s] + rng.normal(0, 0.2 * rule.FLOORS[s], n)
            for s in rule.STATS
        }
    return out


def test_season_summary_and_final_verdict_end_to_end() -> None:
    n = 600
    game = np.arange(n) // 4
    ps = np.array([f"p{i % 30}" for i in range(n)])
    wk = np.arange(n) % 12
    sel = mev.season_summary(_synthetic_deltas(2.0, n, 0), game, ps, wk)
    rep = mev.season_summary(_synthetic_deltas(2.0, n, 1), game, ps, wk)
    assert sel["cm_point"] < -1.5 and rep["cm"]["binding"]["hi"] < 0
    assert max(v for v in rep["stat_shares"].values() if v is not None) < 0.5
    assert rep["knockouts"]["identity"]["keep_share"] == pytest.approx(0.5, abs=0.15)
    audit = {"off_arch": {"gap_realised": 0.6, "gap_pre": 0.6}}
    v = mev.final_verdict(sel, rep, True, audit)
    assert (
        v["failed"] == []
        or v["failed"] == ["shuffled_keeps_under_half"]
        or "identity_only_keeps_under_half" not in v["failed"]
    )
    assert v["minutes_leak"]["void"] is False
    nul = mev.season_summary(_synthetic_deltas(0.0, n, 2), game, ps, wk)
    assert not mev.final_verdict(nul, nul, True, audit)["pass"]
    # planted-future failure voids even a strong result
    assert "planted_future_identical" in mev.final_verdict(sel, rep, False, audit)["failed"]


def test_single_stat_gain_is_caught_by_the_composite_pipeline() -> None:
    n = 600
    game = np.arange(n) // 4
    ps = np.array([f"p{i % 30}" for i in range(n)])
    wk = np.arange(n) % 12
    d = _synthetic_deltas(0.0, n, 3)
    for a in d:
        d[a]["fg3m"] = d[a]["fg3m"] - (4.0 if a == "M" else 0.4) * rule.FLOORS["fg3m"]
    s = mev.season_summary(d, game, ps, wk)
    assert s["stat_shares"]["fg3m"] == pytest.approx(1.0, abs=0.1)
    v = mev.final_verdict(s, s, True, {})
    assert "max_stat_share" in v["failed"] and not v["pass"]


# --------------------------------------------------------------------------- S3: planted rows and the real season assertion


def test_inserted_future_rows_leave_the_past_untouched_and_move_the_future() -> None:
    days = 12
    rows = []
    for d in range(days):
        gid = f"002000{d:02d}"
        date = pd.Timestamp("2023-11-01") + pd.Timedelta(days=d)
        for dfn, mm in ((10, 30.0), (11, 10.0), (12, 8.0)):
            rows.append((gid, date, 2023, 1, dfn, 100, 200, mm, 20.0, 8.0))
    mu = pd.DataFrame(
        rows,
        columns=[
            "game_id",
            "game_date",
            "season",
            "off_player_id",
            "def_player_id",
            "off_team",
            "def_team",
            "mm",
            "poss",
            "pts",
        ],
    )
    cut = int(feat.to_day(pd.Series([pd.Timestamp("2023-11-08")]))[0])
    pert = gate.insert_future_rows(mu, cut, 2023, 0)
    assert len(pert) > len(mu)
    d0 = feat.to_day(mu.game_date)
    past = mu[d0 < cut]
    pd.testing.assert_frame_equal(
        past.reset_index(drop=True),
        pert[feat.to_day(pert.game_date) < cut]
        .reset_index(drop=True)[list(past.columns)]
        .iloc[: len(past)]
        .reset_index(drop=True),
    )
    assert (pert.def_player_id >= 9_000_000).any()

    def frame(m: pd.DataFrame, date: str) -> pd.DataFrame:
        r = pd.DataFrame(
            {
                "game_id": ["00200099"],
                "player_id": [1],
                "team_id": [100],
                "game_date": [pd.Timestamp(date)],
                "min10": [30.0],
                "n_prior": [50],
            }
        )
        g = pd.DataFrame({"game_id": ["00200099"], "home_team": [100], "away_team": [200]})
        R = np.full((feat.rule.N_OFF, feat.rule.N_DEF), 0.2)
        R[:, 0], R[:, 1] = 0.1, 0.3
        tab = {"lift": np.ones_like(R), "R": R, "lg": 0.2}
        return feat.mix_frame(
            r, m, g, {}, 2023, tab, pd.Series({1: 0}), pd.Series({10: 0, 11: 1, 12: 0})
        )

    # a game dated on the cut day: the planted rows dated on or after the cut are not strictly earlier, so its features are unchanged
    a, b = frame(mu, "2023-11-08"), frame(pert, "2023-11-08")
    for c in rule.FEATURES:
        np.testing.assert_allclose(a[c].to_numpy(float), b[c].to_numpy(float), atol=1e-12)
    # a game a few days later sees the planted rows (the positive control: the check can fail)
    a2, b2 = frame(mu, "2023-11-14"), frame(pert, "2023-11-14")
    assert any(not np.allclose(a2[c].to_numpy(float), b2[c].to_numpy(float)) for c in rule.FEATURES)


def test_post_tip_out_rows_do_not_change_the_pretip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    import duckdb
    import polars as pl

    from nba.features import game_tipoff

    games = pd.DataFrame(
        {
            "game_id": [f"002000{i}" for i in range(6)],
            "game_date": pd.to_datetime(["2023-11-01"] * 3 + ["2023-11-02"] * 3),
            "season": 2023,
            "home_team": 1,
            "away_team": 2,
        }
    )
    tips = pl.DataFrame(
        {
            "game_id": games.game_id.tolist(),
            "tip_et": [
                pd.Timestamp(d) + pd.Timedelta(hours=19, minutes=30) for d in games.game_date
            ],
        }
    ).with_columns(pl.col("tip_et").cast(pl.Datetime("us")))
    monkeypatch.setattr(game_tipoff, "build_game_tipoff", lambda max_season=None: tips)
    c = duckdb.connect(":memory:")
    c.execute(
        "create table player_availability(game_id varchar, player_id bigint, status varchar, as_of timestamp, source varchar)"
    )
    # games 0-2 have a usable report (as_of 12:00 the same day, well before tip - 60); games 3-5 only a post-tip row (not usable)
    for i in range(3):
        c.execute(
            "insert into player_availability values (?, ?, 'Out', ?, 'nba_official_report')",
            [f"002000{i}", 1000 + i, pd.Timestamp("2023-11-01 12:00")],
        )
    for i in range(3, 6):
        c.execute(
            "insert into player_availability values (?, ?, 'Out', ?, 'nba_official_report')",
            [f"002000{i}", 1000 + i, pd.Timestamp("2023-11-03 12:00")],
        )
    out = gate.planted_post_tip_out(c, games)
    assert out["post_tip_sets_unchanged"] is True
    assert out["games_with_report_planted"] == 3 and out["games_without_report_planted"] == 3
    assert out["pre_tip_control_detected"] is True and out["pre_tip_control_games_created"] == 3
    # S5: the edge at the REAL tip (19:30): +1 min must not count, -61 min must count (the real snapshots are at 12:00)
    assert out["real_tip_edge_games_with_tip"] == 3
    assert out["tip_plus_1min_sets_unchanged"] is True
    assert out["tip_minus_61min_expected_to_count"] == 3 and out["tip_minus_61min_counted"] == 3
    assert out["tip_minus_61min_all_counted"] is True


def test_season_assertion_is_a_real_check() -> None:
    mu = pd.DataFrame({"season": [2022, 2024]})
    games = pd.DataFrame({"season": [2024]})
    ok = gate.season_assertions(mu, games, {"2024": pd.DataFrame({"game_date": ["2025-04-10"]})})
    assert ok["ok"] and ok["max_season_matchups"] == 2024
    bad = gate.season_assertions(
        pd.DataFrame({"season": [2025]}),
        games,
        {"2024": pd.DataFrame({"game_date": ["2025-04-10"]})},
    )
    assert not bad["ok"]
    late = gate.season_assertions(mu, games, {"2024": pd.DataFrame({"game_date": ["2025-11-01"]})})
    assert not late["ok"]


# --------------------------------------------------------------------------- Amendment 3: M5 pins, S5, m1-m3


def _closure(root_mod: str, stop: set[str]) -> set[str]:
    """Repo-local modules (research/, nba/) reachable from ``root_mod`` through import statements (any depth, lazy imports included)."""
    import ast

    def modfile(m: str) -> Path | None:
        base = ROOT / Path(*m.split("."))
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        return base / "__init__.py" if (base / "__init__.py").is_file() else None

    seen: set[str] = set()
    todo = [root_mod]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        f = modfile(m)
        if f is None or m in stop:
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            todo += [x for x in names if x.split(".")[0] in ("research", "nba") and modfile(x)]
    return seen


def test_harness_pin_covers_every_number_changing_import_transitively() -> None:
    pinned = {rel[:-3].replace("/", ".") for rel in rule.HARNESS_FILES}
    assert "research.coldstart.archetypes" in pinned
    reach = _closure("research.eval.archetype_matchup_all_eval", set(rule.DIGEST_COVERED))
    uncovered = []
    for m in sorted(reach):
        if m in pinned or m in rule.DIGEST_COVERED:
            continue
        if (
            ROOT / Path(*m.split("."))
        ).is_dir():  # a package __init__: no code that changes a number is pinned by name
            continue
        if any(m == k or (k.endswith(".") and m.startswith(k)) for k in rule.EXEMPT_UNUSED):
            continue
        uncovered.append(m)
    assert uncovered == [], (
        f"unpinned, not digest-covered and not exempted with a reason: {uncovered}"
    )
    assert all(
        len(v) > 20 for v in [*rule.DIGEST_COVERED.values(), *rule.EXEMPT_UNUSED.values()]
    ), "every exemption states its reason"


def test_exempt_modules_are_not_called_by_the_archetype_functions_in_use() -> None:
    import inspect

    from research.coldstart import archetypes

    for fn in (archetypes.fit_archetypes, archetypes.encode_position_fractions):
        src = inspect.getsource(fn)
        for bad in (
            "player_possession_features",
            "player_rebound_assist_features",
            "carryover",
            "shrinkage",
            "time_decay",
        ):
            assert bad not in src


def test_declared_pins_parse_and_mismatch_is_reported() -> None:
    doc = (
        "text\nPIN_A0_2023_pts: "
        + "a" * 64
        + "\nLIB_sklearn: 1.9.1\n  not a pin: x\n"
        + rule.MARKER
        + "\nPIN_X: ignored\n"
    )
    d = rule.declared(doc)
    assert d == {"PIN_A0_2023_pts": "a" * 64, "LIB_sklearn": "1.9.1"}
    assert rule.input_mismatches(d, dict(d)) == []
    assert len(rule.input_mismatches(d, {**d, "LIB_sklearn": "1.9.2"})) == 1
    assert (
        len(rule.input_mismatches(d, {"LIB_sklearn": "1.9.1"})) == 1
    )  # declared but not recomputed
    assert len(rule.input_mismatches({}, {"PIN_NEW": "1"})) == 1  # recomputed but not declared
    assert set(rule.versions()) == {f"LIB_{n}" for n in rule.LIB_NAMES}


def test_digests_are_deterministic_and_sensitive() -> None:
    off = pd.Series([0, 1, 2], index=[30, 10, 20])
    dfn = pd.Series([1, 1, 0], index=[30, 10, 20])
    d0 = af.label_digest(off, dfn)
    assert d0 == af.label_digest(off.sort_index(), dfn.sort_index())
    assert d0 != af.label_digest(off.replace({2: 3}), dfn)
    games = pd.DataFrame({"game_id": ["a", "b", "c"], "season": [2023, 2023, 2024]})
    fl = {"a": {2, 1}, "b": set(), "c": {5}}
    o0 = af.out_digest(fl, games, 2023)
    assert o0 == af.out_digest(
        {"b": set(), "a": {1, 2}, "c": {9}}, games, 2023
    )  # other season's set does not matter
    assert o0 != af.out_digest({**fl, "a": {1}}, games, 2023)
    assert o0 != af.out_digest(fl, games, 2024)


def test_verify_inputs_refuses_before_any_outcome_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("t\nPIN_A: 1\nLIB_x: 2\n" + rule.MARKER + "\n")
    mev.verify_inputs({"PIN_A": "1", "LIB_x": "2"}, doc)
    with pytest.raises(SystemExit, match="PIN_A"):
        mev.verify_inputs({"PIN_A": "9", "LIB_x": "2"}, doc)

    class Conn:
        def close(self) -> None: ...

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("an outcome was read")

    games = pd.DataFrame({"game_id": ["a"], "season": [2023]})
    monkeypatch.setattr(mev, "require_ready", lambda sha: {})
    monkeypatch.setattr(arch, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(mev.feat, "read_matchups", lambda c: pd.DataFrame({"season": [2023]}))
    monkeypatch.setattr(mev.feat, "read_games", lambda c: games)
    monkeypatch.setattr(mev.feat, "read_flagged", lambda c, g: {})
    monkeypatch.setattr(mev, "compute_input_digests", lambda c, g, f, m: {"PIN_A": "changed"})
    monkeypatch.setattr(mev, "DOC", doc)
    monkeypatch.setattr(mev.verify_inputs, "__defaults__", (doc,))
    monkeypatch.setattr(mev, "load_season", boom)
    monkeypatch.setattr(mev, "read_outcomes", boom)
    with pytest.raises(SystemExit, match="refusing before any outcome"):
        mev.run("0" * 64, tmp_path)


def test_run_order_and_notes_in_source() -> None:
    import inspect

    src = inspect.getsource(mev.execute)
    assert src.index("verify_inputs(") < src.index("load_season(")
    assert "weak evidence of no composite gain, not strong evidence" in src


def test_role_axis_qualifier_is_emitted_only_on_a_pass() -> None:
    n = 600
    game = np.arange(n) // 4
    ps = np.array([f"p{i % 30}" for i in range(n)])
    wk = np.arange(n) % 12
    d = _synthetic_deltas(2.0, n, 0)
    for st in rule.STATS:  # the identity knockout keeps 10% of the gain
        d["ko_identity"][st] = d["M"][st] * 0.1
    s = mev.season_summary(d, game, ps, wk)
    v = mev.final_verdict(s, s, True, {})
    assert s["knockouts"]["identity"]["keep_share"] == pytest.approx(0.1, abs=0.05)
    assert v["pass"] and v["qualifier"] == "gain needs the role axis"
    d2 = _synthetic_deltas(2.0, n, 0)
    for st in rule.STATS:
        d2["ko_identity"][st] = d2["M"][st] * 0.9
    s2 = mev.season_summary(d2, game, ps, wk)
    assert "qualifier" not in mev.final_verdict(s2, s2, True, {})
    assert "qualifier" not in mev.final_verdict(
        s, s, False, {}
    )  # failing (planted future): no qualifier on a non-pass


def test_gate_artifact_carries_the_run_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake(out: Path, label: str = "Run") -> dict[str, Any]:
        seen["label"] = label
        return {"checks": [], "verdict": "PASS"}

    monkeypatch.setattr(gate, "run", fake)
    assert gate.main(["--out", str(tmp_path / "g.json"), "--label", "Run 4"]) == 0
    assert seen["label"] == "Run 4"
    assert "{label}" in (ROOT / "research/eval/archetype_matchup_all_gate.py").read_text()


def test_a_one_cell_change_in_either_feature_source_table_changes_its_digest() -> None:
    mu = pd.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "mm": [1.0, 2.0, 3.0],
            "pts": [4.0, 5.0, 6.0],
            "game_date": pd.to_datetime(["2023-11-01"] * 3),
        }
    )
    games = pd.DataFrame({"game_id": ["g2", "g1"], "season": [2023, 2023], "home_team": [1, 2]})

    def dig(m: pd.DataFrame, g: pd.DataFrame) -> tuple[str, str]:
        return (
            arch.sha_table(m.sort_values(list(m.columns)).reset_index(drop=True)),
            arch.sha_table(g.sort_values("game_id").reset_index(drop=True)),
        )

    d0 = dig(mu, games)
    assert dig(mu.iloc[::-1], games.iloc[::-1]) == d0  # row order does not matter
    m2 = mu.copy()
    m2.loc[1, "mm"] = 2.0001
    assert dig(m2, games)[0] != d0[0] and dig(m2, games)[1] == d0[1]
    g2 = games.copy()
    g2.loc[0, "home_team"] = 3
    assert dig(mu, g2)[1] != d0[1] and dig(mu, g2)[0] == d0[0]
    import inspect

    src = inspect.getsource(mev.compute_input_digests)
    assert "PIN_MATCHUPS" in src and "PIN_GAMES" in src


def test_load_season_refuses_labels_that_differ_from_the_declared_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = pd.Series([0, 1], index=[1, 2])
    monkeypatch.setattr(mev.af, "build_aligned_archetypes", lambda c, S: (off, off, {}))
    with pytest.raises(SystemExit, match="PIN_LABELS_2023"):
        mev.load_season(
            2023, pd.DataFrame(), pd.DataFrame(), {}, None, {"PIN_LABELS_2023": "0" * 64}
        )


# --------------------------------------------------------------------------- Amendment 5: wiring fix and dryrun


def test_month_key_accepts_series_index_and_array() -> None:
    dates = ["2023-10-24", "2023-11-01", "2024-04-14"]
    want = ["2023-10", "2023-11", "2024-04"]
    as_ts = pd.to_datetime(dates)
    for x in (
        pd.Series(as_ts),
        pd.Series(dates),
        as_ts,
        as_ts.to_numpy(),
        np.array(dates),
        pd.Series(as_ts, index=[7, 8, 9]),
    ):
        assert arm.month_key(x).tolist() == want, type(x)
    assert arm.month_key(pd.Series(as_ts, index=[7, 8, 9])).shape == (3,)


def test_synthetic_outcomes_are_seeded_integer_draws_from_the_grid() -> None:
    q = np.sort(np.random.default_rng(0).poisson(6, size=(500, arch.NQ)).astype(float), axis=1)
    a = mev.synthetic_outcomes(q, 1)
    assert np.array_equal(a, mev.synthetic_outcomes(q, 1)) and not np.array_equal(
        a, mev.synthetic_outcomes(q, 2)
    )
    assert (a == np.round(a)).all() and (a >= 0).all() and a.max() <= q.max()
    m = mev.synthetic_minutes(np.array([10.0, np.nan, 30.0]), 3)
    assert ((m >= 0) & (m <= 48)).all()


def test_dryrun_never_reads_an_outcome_and_restores_the_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = mev.read_outcomes
    real_execute_src = __import__("inspect").getsource(mev.execute)
    seen: dict[str, Any] = {}

    def fake_execute(
        out_dir: Path, pins: dict[str, str], synthetic: bool = False
    ) -> dict[str, Any]:
        seen["synthetic"] = synthetic
        try:
            mev.read_outcomes("pts", 2023, pd.DataFrame())
        except AssertionError as e:
            seen["raised"] = str(e)
        return {"verdict": {}, "wall_seconds": 0.0}

    monkeypatch.setattr(mev, "execute", fake_execute)
    res = mev.dryrun(tmp_path)
    assert seen["synthetic"] is True and "never read an outcome" in seen["raised"]
    assert mev.read_outcomes is real and "SYNTHETIC" in res
    import inspect

    assert "synthetic" in inspect.signature(mev.load_season).parameters
    assert "synthetic" in real_execute_src and "not synthetic" in real_execute_src
