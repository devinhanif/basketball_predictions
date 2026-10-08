"""Tests for the win-probability model-family bake-off (synthetic data, no DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from nba.eval.winprob_family_eval import (
    bh_reject,
    calibrate_wf,
    month_blocks,
    per_game_ll,
    run_arm_wf,
)
from nba.models.winprob_family import (
    BASE_FEATURES,
    LeakError,
    OffsetLogistic,
    PoissonScoreArm,
    StackArm,
    apply_drift_variant,
    assert_allowed_features,
    augment_swap,
    calibration_weights,
    check_importance_leak,
    crps_discrete,
    ece_equal_mass,
    efficiency_features,
    fit_calibrator,
    fit_offset_logistic,
    form_features,
    glm_strength_features,
    mirror_frame,
    schedule_features,
    standings_features,
    team_box_totals,
)


def make_games(n_days: int = 150, n_teams: int = 6, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    start = dt.date(2023, 10, 24)
    rows = []
    gid = 0
    ids = [1610612737 + i for i in range(n_teams)]
    for d in range(n_days):
        order = rng.permutation(ids)
        for i in range(0, n_teams - 1, 2):
            h, a = int(order[i]), int(order[i + 1])
            hp, ap = int(rng.normal(113, 11)), int(rng.normal(110, 11))
            rows.append((f"00223{gid:05d}", start + dt.timedelta(days=d), 2023, h, a, hp, ap))
            gid += 1
    return pl.DataFrame(
        rows,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )


def make_box(games: pl.DataFrame, seed: int = 1) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for gid, hp, ap, h, a in games.select(
        ["game_id", "home_pts", "away_pts", "home_team", "away_team"]
    ).iter_rows():
        for team, pts in ((h, hp), (a, ap)):
            fga = int(rng.integers(82, 94))
            rows.append(
                (
                    gid,
                    team,
                    240.0,
                    fga,
                    int(fga * 0.4),
                    int(rng.integers(18, 28)),
                    int(rng.integers(10, 16)),
                    int(rng.integers(8, 12)),
                    pts,
                )
            )
    return pl.DataFrame(
        rows,
        schema=["game_id", "team_id", "minutes", "fga", "fg3a", "fta", "tov", "oreb", "pts"],
        orient="row",
    )


def perturb_after(games: pl.DataFrame, d: dt.date) -> pl.DataFrame:
    late = pl.col("game_date") >= d
    return games.with_columns(
        pl.when(late).then(pl.col("home_pts") + 37).otherwise(pl.col("home_pts")).alias("home_pts"),
        pl.when(late).then(pl.col("away_pts") - 29).otherwise(pl.col("away_pts")).alias("away_pts"),
    )


CUT = dt.date(2024, 1, 20)


def test_schedule_rest_b2b_and_t4() -> None:
    games = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "game_date": [
                dt.date(2023, 11, 1),
                dt.date(2023, 11, 2),
                dt.date(2023, 11, 4),
                dt.date(2023, 11, 5),
            ],
            "home_team": [1, 1, 1, 1],
            "away_team": [2, 3, 4, 5],
        }
    )
    f = schedule_features(games).sort("game_id")
    assert f["rest_h"].to_list() == [7.0, 1.0, 2.0, 1.0]
    assert f["b2b_h"].to_list() == [0.0, 1.0, 0.0, 1.0]
    assert f["t4_h"].to_list() == [0.0, 0.0, 1.0, 1.0]  # 11/1,11/2,11/4 and 11/2,11/4,11/5


def test_asof_features_ignore_current_and_future_results() -> None:
    games = make_games()
    box = team_box_totals(make_box(games))
    pm = {g: 0.55 for g in games["game_id"].to_list()}
    g2 = perturb_after(games, CUT)
    cases = [
        (lambda g: standings_features(g)),
        (lambda g: efficiency_features(g, box)),
        (lambda g: form_features(g, pm)),
        (lambda g: glm_strength_features(g)),
    ]
    for fn in cases:
        a = fn(games).join(games.select(["game_id", "game_date"]), on="game_id")
        b = fn(g2).join(games.select(["game_id", "game_date"]), on="game_id")
        a = a.filter(pl.col("game_date") <= CUT).drop("game_date").sort("game_id")
        b = b.filter(pl.col("game_date") <= CUT).drop("game_date").sort("game_id")
        # games ON the cut date are perturbed too, yet their own features must not move
        assert a.equals(b)


def test_glm_uses_only_prior_months() -> None:
    games = make_games()
    g2 = games.with_columns(
        pl.when(pl.col("game_date") >= dt.date(2024, 2, 1))
        .then(pl.col("home_pts") + 50)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    a = glm_strength_features(games).join(games.select(["game_id", "game_date"]), on="game_id")
    b = glm_strength_features(g2).join(games.select(["game_id", "game_date"]), on="game_id")
    jan = pl.col("game_date") < dt.date(2024, 3, 1)  # Feb games use only pre-Feb data too
    assert a.filter(jan).sort("game_id")["glm_margin"].to_list() == pytest.approx(
        b.filter(jan).sort("game_id")["glm_margin"].to_list()
    )


def test_relative_variant_removes_league_level() -> None:
    games = make_games()
    eff = efficiency_features(games, team_box_totals(make_box(games)))
    rel = apply_drift_variant(eff, True)
    late = eff.tail(30)
    assert np.allclose(
        rel.tail(30)["ortg_h"].to_numpy(), (late["ortg_h"] - late["lg_ortg"]).to_numpy()
    )
    assert apply_drift_variant(eff, False).equals(eff)


def _frame(n: int = 700, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    dates = [dt.date(2022, 10, 20) + dt.timedelta(days=int(i // 5)) for i in range(n)]
    cols = {c: rng.normal(size=n) for c in BASE_FEATURES}
    off = rng.normal(0.2, 0.7, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(off + 0.3 * cols["d_out"])))).astype(float)
    return pl.DataFrame(
        {
            "game_id": [f"g{i:05d}" for i in range(n)],
            "game_date": dates,
            "season": [2022] * n,
            "home_team": rng.integers(1, 7, n),
            "away_team": rng.integers(7, 13, n),
            "home_pts": rng.integers(95, 130, n),
            "away_pts": rng.integers(95, 130, n),
            "y": y,
            "offset": off,
            "p0": 1 / (1 + np.exp(-off)),
            "home_flag": np.ones(n),
            **cols,
        }
    )


def test_mirror_is_an_involution_and_swap_labels() -> None:
    df = _frame(50)
    twice = mirror_frame(mirror_frame(df))
    for c in [*BASE_FEATURES, "offset"]:
        assert np.allclose(twice[c].to_numpy(), df[c].to_numpy())
    y = df["y"].to_numpy()
    aug, y2 = augment_swap(df, y)
    assert aug.height == 100 and np.allclose(y2[50:], 1 - y)
    assert aug["home_flag"].to_list() == [1.0] * 50 + [0.0] * 50
    assert np.allclose(aug["offset"].to_numpy()[50:], -df["offset"].to_numpy())


def test_offset_logistic_reduces_to_offset_with_strong_penalty() -> None:
    df = _frame(300)
    arm = OffsetLogistic(True, 1e8)
    arm.fit(df, df["y"].to_numpy())
    p = arm.predict(df)
    base = np.clip(df["p0"].to_numpy(), 0.02, 0.98)
    assert np.abs(p - base).max() < 0.03  # only the free intercept can move it
    assert set(arm.get_config()) >= {"model_name", "seed", "lam"}
    assert isinstance(arm.get_metrics(), dict)


def test_fit_offset_logistic_recovers_coefficient() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(4000, 1))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.8 * x[:, 0])))).astype(float)
    w, b = fit_offset_logistic(x, np.zeros(4000), y, 1.0)
    assert abs(w[0] - 0.8) < 0.1 and abs(b) < 0.1


def test_swap_arm_predicts_original_orientation() -> None:
    df = _frame(400)
    arm = OffsetLogistic(False, 20.0, swap=True)
    arm.fit(df, df["y"].to_numpy())
    p = arm.predict(df)
    assert p.shape == (400,) and np.all((p > 0) & (p < 1))
    assert arm.get_config()["n_features"] == len(BASE_FEATURES) + 1


def test_leak_detector() -> None:
    assert_allowed_features(list(BASE_FEATURES) + ["offset"])
    with pytest.raises(LeakError):
        assert_allowed_features([*BASE_FEATURES, "margin"])
    with pytest.raises(LeakError):
        assert_allowed_features([*BASE_FEATURES, "home_pts"])
    gain = {c: 1.0 for c in BASE_FEATURES}
    check_importance_leak(gain)
    gain["final_margin"] = 1e6
    with pytest.raises(LeakError):
        check_importance_leak(gain)


def test_walk_forward_ignores_future_labels() -> None:
    df = _frame(900)
    mk = lambda: OffsetLogistic(True, 200.0)  # noqa: E731
    base = run_arm_wf(df, mk, min_train=300)
    blocks = month_blocks(df["game_date"].to_list())
    last = blocks[-1]
    y2 = df["y"].to_numpy().copy()
    y2[last] = 1 - y2[last]
    flipped = run_arm_wf(df.with_columns(pl.Series("y", y2)), mk, min_train=300)
    before = np.arange(len(y2)) < last[0]
    assert np.allclose(base["p"][before], flipped["p"][before])
    assert base["fitted"].any() and not base["fitted"][:300].any()


def test_calibrators_behave() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0.2, 0.8, 4000)
    z = np.log(p / (1 - p))
    y = (rng.random(4000) < 1 / (1 + np.exp(-1.5 * z))).astype(float)  # under-confident p
    w = np.ones(4000)
    for m, prm in (("platt", 1.0), ("platt_slope", 1.0), ("beta", 1.0), ("iso", (100, 1.0))):
        cal = fit_calibrator(m, p, y, w, prm)
        assert per_game_ll(y, cal(p)).mean() < per_game_ll(y, p).mean()
    iso = fit_calibrator("iso", p, y, w, (100, 1.0))
    grid = np.linspace(0.25, 0.75, 50)
    assert np.all(np.diff(iso(grid)) >= -1e-12)
    slope = fit_calibrator("platt_slope", p, y, w, 1.0).theta[0]
    assert 1.3 < slope < 1.7


def test_calibration_window_never_sees_future() -> None:
    dates = [dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(100)]
    keep, w = calibration_weights(dates, dt.date(2023, 3, 1), 30, 10.0)
    assert not keep[dates.index(dt.date(2023, 3, 1)) :].any()
    assert keep.sum() == 30 and w[keep].max() <= 1.0
    df = _frame(900)
    p = df["p0"].to_numpy()
    y = df["y"].to_numpy()
    d = df["game_date"].to_list()
    out, _ = calibrate_wf(p, y, d, "platt_slope")
    y2 = y.copy()
    last = month_blocks(d)[-1]
    y2[last] = 1 - y2[last]
    out2, _ = calibrate_wf(p, y2, d, "platt_slope")
    assert np.allclose(out[: last[0]], out2[: last[0]])


def test_crps_and_poisson_distributions() -> None:
    assert crps_discrete(np.array([3.0]), np.array([1.0]), 5.0) == pytest.approx(2.0)
    games = make_games(80)
    df = games.with_columns(
        pl.Series("y", (games["home_pts"] > games["away_pts"]).cast(pl.Float64)),
        pl.lit(0.0).alias("offset"),
        pl.lit(0.5).alias("p0"),
        pl.lit(1.0).alias("home_flag"),
        *[pl.lit(0.0).alias(c) for c in BASE_FEATURES],
    )
    teams = sorted(set(games["home_team"].to_list()) | set(games["away_team"].to_list()))
    arm = PoissonScoreArm(False, teams)
    arm.fit(df, df["y"].to_numpy())
    p = arm.predict(df)
    assert np.all((p > 0.02) & (p < 0.98))
    l1, l2 = arm.lambdas(df)
    (xm, pm), (xt, pt) = arm.margin_total_pmfs(float(l1[0]), float(l2[0]))
    assert pm.sum() == pytest.approx(1.0) and pt.sum() == pytest.approx(1.0)
    assert float((xt * pt).sum()) == pytest.approx(
        arm.k_ * (l1[0] + l2[0] + 2 * arm.lam3_), rel=0.02
    )
    eq = PoissonScoreArm(False, teams)
    eq.k_, eq.lam3_ = 1.0, 0.0
    from scipy.stats import skellam

    assert skellam.sf(0, 100.0, 100.0) + 0.5 * skellam.pmf(0, 100.0, 100.0) == pytest.approx(0.5)


def test_stack_and_stats_helpers() -> None:
    df = _frame(300)
    for m in StackArm().spec.members:
        df = df.with_columns(pl.Series(f"p_{m}", df["p0"].to_numpy()))
    st = StackArm()
    st.fit(df, df["y"].to_numpy())
    assert np.all(np.abs(st.w_) < 1e-3)  # identical members carry no information
    assert bh_reject([0.001, 0.2, 0.9], 0.05) == [True, False, False]
    y = np.array([0, 1] * 50, dtype=float)
    assert ece_equal_mass(y, np.full(100, 0.5)) == pytest.approx(0.0)
