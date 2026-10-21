# ruff: noqa: E501
"""MARKET_PRIOR_RESIDUAL harness tests on synthetic data (no real file is read).

Covers: verdict logic, the season guard, the DRAFT refusal, the known-at filter, label-free loading, the clustered bootstrap
wiring through ``Boot``, the disagreement regression, the derangement placebo, the walk-forward planted future, and the offset.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from research.eval import market_prior_residual_eval as ev
from research.eval import market_prior_residual_rule as rule


# ---------------------------------------------------------------- verdict logic
def _v(a_point, a_hi, b_coef, b_lo, c_point, c_hi, s1=True, a_lo=-0.01):
    return rule.verdict(
        {"point": a_point, "hi": a_hi, "lo": a_lo},
        {"coef": b_coef, "lo": b_lo},
        {"point": c_point, "hi": c_hi},
        s1,
    )


def test_verdict_pass_needs_all_three_and_select_sign():
    assert _v(-0.004, -0.001, 0.5, 0.1, -0.004, -0.001)["word"] == "PASS"
    assert _v(-0.004, -0.001, 0.5, 0.1, -0.004, -0.001, s1=False)["word"] == "FAIL_SELECT_SIGN"


def test_verdict_floor_boundary_is_inclusive_and_ci_upper_must_be_negative():
    assert _v(rule.FLOOR, -0.0001, 0.5, 0.1, rule.FLOOR, -0.0001)["word"] == "PASS"
    assert _v(rule.FLOOR + 1e-6, -0.0001, 0.5, 0.1, rule.FLOOR, -0.0001)["word"] != "PASS"
    assert _v(-0.004, 0.0, 0.5, 0.1, -0.004, -0.001)["word"] != "PASS"  # upper == 0 does not count


def test_verdict_words():
    # information but below the floor
    assert _v(-0.0004, 0.0001, 0.6, 0.2, -0.0004, -0.0001)["word"] == "INFO_BELOW_FLOOR"
    # disagreement predicts, but R is not better than scrambled features
    assert _v(-0.0004, 0.0001, 0.6, 0.2, -0.0001, 0.0002)["word"] == "PLACEBO_EXPLAINS"
    # R beats the floor but the disagreement coefficient is not significant
    assert _v(-0.004, -0.001, 0.2, -0.3, -0.004, -0.001)["word"] == "FAIL_NO_DISAGREEMENT_SIGNAL"
    # R significantly worse than the market
    assert _v(0.002, 0.003, -0.1, -0.5, 0.0, 0.001, a_lo=0.001)["word"] == "FAIL_WORSE_THAN_MARKET"
    assert _v(0.0001, 0.001, 0.0, -0.5, 0.0, 0.001)["word"] == "FAIL_NO_GAIN"


def test_stage1_sign():
    assert rule.stage1_sign(0.2)
    assert not rule.stage1_sign(-0.2)
    assert not rule.stage1_sign(0.0)
    assert not rule.stage1_sign(None)


def test_bh_adjust_known_values():
    adj = rule.bh_adjust([0.01, 0.04, 0.03, 0.5])
    assert adj == pytest.approx([0.04, 0.0533333333, 0.0533333333, 0.5])


# ---------------------------------------------------------------- season guard and DRAFT refusal
def test_season_guard():
    rule.assert_seasons((2023, 2024))
    with pytest.raises(ValueError, match="frozen holdout"):
        rule.assert_seasons((2023, 2025))
    with pytest.raises(ValueError):
        ev.load_legs(seasons=(2025,))
    frame = _frame(200)
    with pytest.raises(ValueError):
        ev.walk_forward(frame, np.zeros(200), 2025, ())
    with pytest.raises(ValueError):
        ev.walk_forward(frame, np.zeros(200), 2024, (2025,))


def test_draft_refusal(tmp_path: Path):
    doc = tmp_path / "MARKET_PRIOR_RESIDUAL.md"
    doc.write_text("# x\n\nStatus: **DRAFT**\nbody\n=== RESULTS BELOW ===\nignored\n")
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_not_draft(doc)
    sha = rule.frozen_sha(doc)
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_frozen(sha, doc, run=lambda *a: "")
    # a frozen-marked doc still needs the commit
    doc.write_text("# x\n\nStatus: **FROZEN** at abc\nbody\n=== RESULTS BELOW ===\nignored\n")
    sha = rule.frozen_sha(doc)
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_frozen(sha, doc, run=lambda *a: "")

    # a matching commit (subject + copy of the rule) opens it
    def fake(*args: str) -> str:
        if args[0] == "log":
            return f"deadbeef\tprereg({rule.ID}): {sha}\n"
        return doc.read_text()

    assert rule.require_frozen(sha, doc, run=fake) == sha
    with pytest.raises(RuntimeError, match="64 hex"):
        rule.require_frozen("abc", doc)


def test_run_refuses_while_draft_or_unfrozen():
    # the committed doc in this tree is a DRAFT (or, once frozen, has no matching commit for a bogus sha)
    with pytest.raises(RuntimeError):
        ev.run("0" * 64)


def test_results_marker_excluded_from_hash():
    a = "Status: FROZEN\nrule\n=== RESULTS BELOW ===\nresult 1\n"
    b = "Status: FROZEN\nrule\n=== RESULTS BELOW ===\nresult 2 appended later\n"
    assert rule.text_sha(a) == rule.text_sha(b)
    assert rule.text_sha(a) != rule.text_sha("Status: FROZEN\nrule edited\n=== RESULTS BELOW ===\n")


# ---------------------------------------------------------------- known-at filter
def test_known_at_filter():
    tip = np.array(["2024-01-01T20:00:00"] * 5 + ["NaT"], dtype="datetime64[us]")
    snap = np.array(
        [
            "2024-01-01T18:59:00",  # lead 61: keep
            "2024-01-01T19:00:00",  # lead 60: keep (boundary)
            "2024-01-01T19:01:00",  # lead 59: drop
            "2024-01-01T19:55:00",  # lead 5: drop
            "NaT",  # no stamp: drop
            "2024-01-01T10:00:00",  # no tip: drop (never a proxy tip)
        ],
        dtype="datetime64[us]",
    )
    assert rule.known_at_keep(snap, tip).tolist() == [True, True, False, False, False, False]
    lead = rule.lead_minutes(snap, tip)
    assert lead[0] == pytest.approx(61.0) and lead[1] == pytest.approx(60.0) and np.isnan(lead[4])


def _pairs(seasons=(2023,), n=40, late=5, with_over=True) -> dict[int, pl.DataFrame]:
    rng = np.random.default_rng(0)
    out = {}
    for s in seasons:
        tip = dt.datetime(s, 11, 1, 20, 0)
        rows = []
        for i in range(n):
            lead = 30 if i < late else 64  # `late` rows violate the 60-minute rule
            rows.append(
                {
                    "game_id": f"g{i // 4}",
                    "player_id": i % 4 + 1,
                    "stat": ev.rule.STATS[i % 4],
                    "book": "pinnacle" if i % 8 else "draftkings",
                    "snapshot_kind": "t60",
                    "snapshot_ts": tip - dt.timedelta(minutes=lead),
                    "tipoff_utc": tip,
                    "line": 10.5,
                    "price_over": -110,
                    "price_under": -110,
                    "p_over_mult": 0.5,
                    "p_model": 0.45,
                    "_dnp": bool(i == 7),
                    "status": 0,
                    "season": s,
                    "game_date": tip.date(),
                    "over": float(rng.random() < 0.5),
                    "y": 11.0,
                }
            )
        out[s] = pl.DataFrame(rows)
    return out


def test_load_legs_applies_known_at_hard_filter_and_counts(tmp_path: Path):
    for s, d in _pairs().items():
        d.write_parquet(tmp_path / f"pairs_{s}.parquet")
    tpl = str(tmp_path / "pairs_{season}.parquet")
    legs, counts = ev.load_legs(seasons=(2023,), pairs_path=tpl)
    c = counts["2023"]
    assert c["played_scored"] - c["after_known_at"] == c["dropped_known_at"] > 0
    assert (
        rule.lead_minutes(legs["snapshot_ts"].to_numpy(), legs["tipoff_utc"].to_numpy()) >= 60
    ).all()
    assert (legs["book"] == "pinnacle").all() and not legs["stat"].is_in(["steals"]).any()
    assert legs.height == c["after_known_at"]


def test_load_legs_label_free_never_selects_labels(tmp_path: Path):
    for s, d in _pairs().items():
        d.write_parquet(tmp_path / f"pairs_{s}.parquet")
    tpl = str(tmp_path / "pairs_{season}.parquet")
    free, _ = ev.load_legs(seasons=(2023,), pairs_path=tpl, with_labels=False)
    assert not set(rule.LABEL_COLUMNS) & set(free.columns)
    lab, _ = ev.load_legs(seasons=(2023,), pairs_path=tpl, with_labels=True)
    assert "over" in lab.columns
    # the gate module's loader calls pass with_labels=False everywhere except the disclosed G6 reproduction
    src = Path(ev.__file__).with_name("market_prior_residual_gate.py").read_text()
    assert "load_legs(with_labels=False)" in src
    assert src.count('"over"]') == 1  # only G6 selects the label column


# ---------------------------------------------------------------- clusters, bootstrap wiring
def _cluster_frame(n_games=60, per=20, seed=1) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    gid = np.repeat(np.arange(n_games), per)
    base = dt.date(2024, 1, 1)
    return pl.DataFrame(
        {
            "game_id": [f"g{g:04d}" for g in gid],
            "player_id": rng.integers(1, 30, len(gid)),
            "season": np.where(gid < n_games // 2, 2023, 2024),
            "game_date": [base + dt.timedelta(days=int(g // 3)) for g in gid],
        }
    )


def test_cluster_codes_kinds_and_iso_week_boundary():
    d = _cluster_frame()
    assert rule.n_clusters(rule.cluster_codes(d, "game")) == 60
    ps = rule.cluster_codes(d, "player_season")
    assert rule.n_clusters(ps) == d.select(pl.struct(["player_id", "season"]).n_unique()).item()
    # 2024-12-30 and 2025-01-02 share ISO week 2025-W01; 2024-12-29 (Sunday) is week 2024-W52
    edge = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "player_id": [1, 1, 1],
            "season": [2024] * 3,
            "game_date": [dt.date(2024, 12, 29), dt.date(2024, 12, 30), dt.date(2025, 1, 2)],
        }
    )
    w = rule.cluster_codes(edge, "week")
    assert w[0] != w[1] and w[1] == w[2]
    with pytest.raises(ValueError):
        rule.cluster_codes(d, "team")


def test_cluster_ci_goes_through_boot_and_is_seed_stable():
    from research.eval.model_clv_eval import Boot

    d = _cluster_frame()
    codes = rule.cluster_codes(d, "game")
    rng = np.random.default_rng(3)
    v = rng.normal(0.0, 1.0, d.height) + np.repeat(rng.normal(0, 1.0, 60), 20)  # shared game shock
    got = rule.cluster_ci(v, codes, seed=0, B=300)
    ref = Boot(codes, 60, 0, 300).mean(v, np.ones(len(v), dtype=bool))
    assert got == ref
    assert got["mean"] == pytest.approx(v.mean())
    # a clustered CI is wider than an iid row bootstrap when the noise is shared within a cluster
    iid = rule.cluster_ci(v, np.arange(len(v)), seed=0, B=300)
    assert (got["hi"] - got["lo"]) > 1.5 * (iid["hi"] - iid["lo"])


def test_envelope_and_week_rule():
    cis = {
        "game": {"lo": -0.002, "hi": 0.001, "p": 0.1, "mean": -0.0005},
        "player_season": {"lo": -0.003, "hi": 0.0005, "p": 0.2, "mean": -0.0005},
        "week": {"lo": -0.0015, "hi": 0.002, "p": 0.3, "mean": -0.0005},
    }
    e = rule.envelope(cis, rule.CLUSTERINGS)
    assert (e["lo"], e["hi"], e["p"]) == (-0.003, 0.002, 0.3)
    assert rule.decisive_clusterings(rule.MIN_WEEKS) == rule.CLUSTERINGS
    assert rule.decisive_clusterings(rule.MIN_WEEKS - 1) == ("game", "player_season")


# ---------------------------------------------------------------- the disagreement regression
def test_fit_logit_matches_statsmodels():
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(2000), rng.normal(size=2000), rng.normal(size=2000)])
    y = (rng.random(2000) < rule.sigmoid(X @ np.array([0.1, 0.8, -0.4]))).astype(float)
    ref = sm.Logit(y, X).fit(disp=0).params
    assert rule.fit_logit(X, y) == pytest.approx(ref, abs=1e-5)


def test_disagreement_boot_detects_planted_signal_and_not_noise():
    rng = np.random.default_rng(5)
    n = 6000
    d = _cluster_frame(n_games=300, per=20)
    codes = rule.cluster_codes(d, "game")
    pm = rng.uniform(0.35, 0.65, n)
    delta = rng.normal(0, 0.35, n)  # logit disagreement
    pr = rule.sigmoid(rule.logit(pm) + delta)
    y_sig = (rng.random(n) < pr).astype(float)  # the disagreement is real
    y_noise = (rng.random(n) < pm).astype(float)  # the disagreement is noise
    sig = rule.disagreement_boot(y_sig, pm, pr, codes, B=200)
    noi = rule.disagreement_boot(y_noise, pm, pr, codes, B=200)
    assert sig["coef"] > 0.5 and sig["lo"] > 0
    assert noi["lo"] < 0 < noi["hi"]
    assert sig["n"] == n


# ---------------------------------------------------------------- placebo derangement
def test_within_group_derangement():
    groups = np.array(["a"] * 5 + ["b"] * 3 + ["c"] + ["d"] + ["e"] * 2)
    out = rule.within_group_derangement(groups, 1)
    assert (out != np.arange(len(groups))).all()  # no fixed points
    assert (groups[out][:8] == groups[:8]).all()  # multi-row groups stay in their group
    assert sorted(out[8:10].tolist()) == [8, 9]  # the two singletons swap with each other
    assert sorted(out.tolist()) == list(range(len(groups)))  # a permutation
    assert not np.array_equal(out, rule.within_group_derangement(groups, 2))


# ---------------------------------------------------------------- walk-forward fits
def _frame(n: int, seed: int = 0, signal: float = 0.0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    names = ev.generic_feature_names()
    cols = {c: rng.normal(size=n) for c in names}
    start = dt.date(2023, 10, 20)
    dates = [start + dt.timedelta(days=int(i * 480 / n)) for i in range(n)]  # Oct 2023 .. Feb 2025
    season = np.array([2023 if d < dt.date(2024, 10, 1) else 2024 for d in dates])
    stat_i = rng.integers(0, 4, n)
    cols |= {
        "game_id": [f"g{i // 8}" for i in range(n)],
        "player_id": rng.integers(1, 60, n),
        "season": season,
        "game_date": dates,
        "stat": [rule.STATS[i] for i in stat_i],
        "stat_code": stat_i.astype(float),
        "line": np.full(n, 10.5),
        "p_over_mult": rng.uniform(0.35, 0.65, n),
        "p_model": rng.uniform(0.3, 0.7, n),
        "m_rec": np.full(n, 10.0) + rng.normal(size=n),
        "s_rec": np.full(n, 3.0),
        "starter10": (rng.random(n) < 0.5).astype(float),
        "team_game_no": rng.integers(1, 82, n).astype(float),
        "n_out_rot": rng.integers(0, 3, n).astype(float),
        "has_report": rng.integers(0, 2, n).astype(float),
        "min10": rng.uniform(5, 36, n),
    }
    return pl.DataFrame(cols)


def _labels(frame: pl.DataFrame, signal: float, seed: int = 9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    lm = rule.logit(frame["p_over_mult"].to_numpy())
    x = frame["m10_pts"].to_numpy()
    return (rng.random(frame.height) < rule.sigmoid(lm + signal * x)).astype(float)


def test_walk_forward_planted_future_and_positive_control(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(rule.LGB_PARAMS, "n_estimators", 20)
    frame = _frame(3000)
    y = _labels(frame, 0.0)
    dates = frame["game_date"].to_numpy().astype("datetime64[D]")
    season = frame["season"].to_numpy()
    start = np.datetime64("2024-04-01")
    te = (season == 2023) & (dates >= start) & (dates < np.datetime64("2024-05-01"))
    base_sc, base, _ = ev.walk_forward(frame, y, 2023, (), n_min=200)
    assert base_sc[te].all()
    # labels on/after the block start flipped: this block's predictions must not move
    y_fut = y.copy()
    y_fut[dates >= start] = 1.0 - y_fut[dates >= start]
    _, fut, _ = ev.walk_forward(frame, y_fut, 2023, (), n_min=200)
    assert np.array_equal(base[te], fut[te])
    # positive control: labels flipped inside the training window must move them
    y_in = y.copy()
    win = (dates < start) & (dates >= np.datetime64("2024-02-01"))
    y_in[win] = 1.0 - y_in[win]
    _, inn, _ = ev.walk_forward(frame, y_in, 2023, (), n_min=200)
    assert not np.array_equal(base[te], inn[te])


def test_walk_forward_min_train_burn_in_and_warm_start(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(rule.LGB_PARAMS, "n_estimators", 10)
    frame = _frame(3000)
    y = _labels(frame, 0.0)
    sc, lr, info = ev.walk_forward(frame, y, 2023, (), n_min=900)
    assert not sc[
        frame["game_date"].to_numpy().astype("datetime64[M]") == np.datetime64("2023-10")
    ].any()
    assert np.isnan(lr[~sc]).all() and np.isfinite(lr[sc]).all()
    assert any(not b["scored"] for b in info["blocks"]) and any(b["scored"] for b in info["blocks"])
    # 2024 with 2023 as warm start scores from its first month (the warm legs count as training)
    sc24, _, info24 = ev.walk_forward(frame, y, 2024, (2023,), n_min=900)
    assert sc24[frame["season"].to_numpy() == 2024].all()
    assert info24["blocks"][0]["n_train"] > 900


def test_offset_means_a_noise_world_stays_at_the_market_and_a_signal_moves_it(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _frame(6000)
    sc, lr, _ = ev.walk_forward(frame, _labels(frame, 0.0), 2023, (), n_min=500)
    lm = rule.logit(frame["p_over_mult"].to_numpy())
    noise_move = np.abs(lr[sc] - lm[sc]).mean()
    assert (
        noise_move < 0.2
    )  # regularised: R stays near the market when nothing is there (small early blocks move most)
    y_sig = _labels(frame, 0.8)
    sc2, lr2, _ = ev.walk_forward(frame, y_sig, 2023, (), n_min=500)
    d = lr2[sc2] - lm[sc2]
    x = frame["m10_pts"].to_numpy()[sc2]
    assert (
        np.corrcoef(d, x)[0, 1] > 0.6
    )  # the planted feature effect is recovered in the right direction
    assert np.abs(d).mean() > 2 * noise_move


def test_placebo_permutes_only_the_production_block():
    frame = _frame(400)
    groups = np.array(
        [
            f"{a}|{b}"
            for a, b in zip(
                frame["game_date"].cast(pl.Utf8).to_list(), frame["stat"].to_list(), strict=True
            )
        ]
    )
    perm = rule.within_group_derangement(groups, 3)
    real, plc = ev.design_matrix(frame), ev.design_matrix(frame, perm)
    names = ev.generic_feature_names()
    n_p = len(names)
    assert not np.array_equal(
        real[:, :n_p], plc[:, :n_p]
    )  # production block belongs to another leg
    mk = {n: i for i, n in enumerate(ev.MARKET_FEATURES)}
    assert np.array_equal(
        real[:, n_p + mk["line"]], plc[:, n_p + mk["line"]]
    )  # market columns stay with the row
    assert np.array_equal(real[:, n_p + mk["logit_pm"]], plc[:, n_p + mk["logit_pm"]])
    assert not np.array_equal(
        real[:, n_p + mk["line_minus_m"]], plc[:, n_p + mk["line_minus_m"]]
    )  # re-derived from permuted m


# ---------------------------------------------------------------- scoring pipeline end to end
def test_score_stage_runs_end_to_end_and_reports_n_and_clusters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rule, "N_BOOT", 40)
    monkeypatch.setattr(rule, "N_MIN_TRAIN", 300)
    monkeypatch.setitem(rule.LGB_PARAMS, "n_estimators", 10)
    frame = _frame(2400)
    y = _labels(frame, 0.0)
    out = ev.run_stages(frame, frame, y, k_placebo=2, verbose=False)
    for stage in ("select", "report"):
        r = out[stage]
        assert r["n"] > 100 and set(r["clusters"]) == set(rule.CLUSTERINGS)
        assert set(r["a_R_minus_M"]["by_clustering"]) == set(rule.CLUSTERINGS)
        assert (
            r["a_R_minus_M"]["binding"]["lo"]
            <= r["a_R_minus_M"]["binding"]["point"]
            <= r["a_R_minus_M"]["binding"]["hi"]
        )
        assert r["placebo_null"]["K"] == 2
        sec = r["secondary"]
        assert (
            "H2W_minus_M" in sec and "disagreement_A0_stored" in sec and "slices_R_minus_M" in sec
        )
        assert {f["test"] for f in sec["bh_family"]} <= {
            f"{s}:{k}" for s in rule.STATS for k in ("R_minus_M", "disagreement")
        }
    res = ev.decide(out)
    assert res["word"] in {
        "PASS", "INFO_BELOW_FLOOR", "PLACEBO_EXPLAINS", "FAIL_NO_DISAGREEMENT_SIGNAL",
        "FAIL_WORSE_THAN_MARKET", "FAIL_NO_GAIN", "FAIL_SELECT_SIGN",
    }  # fmt: skip
    assert res["word"] != "PASS"  # a noise world must not pass


def test_h2_reference_arm_equals_market_when_too_few_legs():
    frame = _frame(1200)
    y = _labels(frame, 0.0)
    sc = np.ones(frame.height, dtype=bool)
    p = ev.h2_walk_forward(frame, y, 2023, (), sc)
    m23 = frame["season"].to_numpy() == 2023
    pm = frame["p_over_mult"].to_numpy()
    # fewer than 2,000 earlier legs per stat everywhere: w = 0, the blend is the market itself (rows of the other season stay NaN)
    assert np.allclose(p[m23], pm[m23], atol=1e-12)
    assert np.isnan(p[~m23]).all()


def test_time_weights_require_strictly_earlier_rows():
    w = rule.time_weights(
        np.array(["2023-12-01", "2023-01-01"], dtype="datetime64[D]"), np.datetime64("2024-01-01")
    )
    assert w[0] > w[1] and 0 < w[1] < w[0] <= 1
    with pytest.raises(ValueError, match="strictly before"):
        rule.time_weights(
            np.array(["2024-01-01"], dtype="datetime64[D]"), np.datetime64("2024-01-01")
        )


def test_beats_p_direction():
    # a contrast clearly below zero must give a small p, one clearly above zero a p near 1
    d = _cluster_frame()
    codes = rule.cluster_codes(d, "game")
    rng = np.random.default_rng(0)
    below = rng.normal(-0.5, 1.0, d.height)
    above = rng.normal(0.5, 1.0, d.height)
    mk = lambda v: {  # noqa: E731
        "decisive": ["game"],
        "by_clustering": {"game": rule.cluster_ci(v, codes, B=400)},
    }
    monkey = rule.N_BOOT
    rule.N_BOOT = 400
    try:
        assert ev.beats_p(mk(below)) < 0.01
        assert ev.beats_p(mk(above)) > 0.95
    finally:
        rule.N_BOOT = monkey
