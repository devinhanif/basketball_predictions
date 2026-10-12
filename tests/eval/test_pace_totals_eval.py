# ruff: noqa: E501
"""PACE_TOTALS harness tests: synthetic data only (no database, no 2025)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy.special import ndtr

from research.eval import pace_periods as A
from research.eval import pace_totals_eval as P


def synth_base(seed: int = 0, line_has_pace: bool = False) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n_teams = 12
    pace_t = rng.normal(98, 7, n_teams)
    off_t = rng.normal(112, 3, n_teams)
    rows = []
    d0 = dt.date(2022, 11, 1)
    i = 0
    for day in range(0, 900):
        d = d0 + dt.timedelta(days=day)
        if d.month in (6, 7, 8, 9):
            continue
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            h, a = int(order[k]), int(order[k + 1])
            pace = (pace_t[h] + pace_t[a]) / 2 + rng.normal(0, 2)
            poss = pace * 2
            ppp = (off_t[h] + off_t[a]) / 2
            hp = int(round(poss / 2 * (ppp + rng.normal(0, 4)) / 100 + 1.5))
            ap = int(round(poss / 2 * (ppp + rng.normal(0, 4)) / 100 - 1.5))
            true = 2 * pace * ppp / 100
            line = float(
                round(
                    true * (1 if line_has_pace else 0)
                    + (0 if line_has_pace else 220 + rng.normal(0, 1))
                    + rng.normal(0, 2)
                    - 0.5
                )
                + 0.5
            )
            season = (
                2022 if d < dt.date(2023, 8, 1) else (2023 if d < dt.date(2024, 8, 1) else 2024)
            )
            rows.append(
                {
                    "game_id": f"g{i:05d}",
                    "game_date": d,
                    "season": season,
                    "home_team": h + 1,
                    "away_team": a + 1,
                    "home_pts": hp,
                    "away_pts": ap,
                    "n_poss": int(round(poss)),
                    "maxp": 4,
                    "line": line if season >= 2023 else None,
                    "fallback": bool(i % 7 == 0),
                    "dk_point": line if season >= 2023 else None,
                    "dk_po": -110,
                    "dk_pu": -110,
                }
            )
            i += 1
    return pl.DataFrame(rows, schema_overrides={"line": pl.Float64, "dk_point": pl.Float64})


@pytest.fixture(scope="module")
def base() -> pl.DataFrame:
    return synth_base()


def test_rolling_prior_matches_polars_pace(base: pl.DataFrame) -> None:
    fr = P.build_frame(base)
    ref = A.asof_rolls(
        base.select(
            "game_id",
            "game_date",
            "season",
            "home_team",
            "away_team",
            "home_pts",
            "away_pts",
            "n_poss",
            "maxp",
        )
    )
    h = ref.join(
        fr.select("game_id", "home_team", "pace_h"),
        left_on=["game_id", "team_id"],
        right_on=["game_id", "home_team"],
    ).drop_nulls("r20_pace")
    assert h.height > 1000
    assert np.allclose(h["r20_pace"].to_numpy(), h["pace_h"].to_numpy(), equal_nan=False)


def test_crps_int_matches_brute_force() -> None:
    y, mu, sg = np.array([210.0]), np.array([214.3]), np.array([12.0])
    got = P.crps_int(y, mu, sg, 100, 330)[0]
    ks = np.arange(100, 331)
    f = ndtr((ks + 0.5 - 214.3) / 12.0)
    assert got == pytest.approx(
        sum((f[j] - (1.0 if ks[j] >= 210 else 0.0)) ** 2 for j in range(len(ks)))
    )


def test_p_over_half_integer_and_integer() -> None:
    mu, sg = np.array([220.0]), np.array([12.0])
    assert P.p_over_at(np.array([220.5]), mu, sg)[0] == pytest.approx(
        1 - ndtr(0.5 / 12 * 0 + (220 + 0.5 - 220) / 12)
    )
    p = P.p_over_at(np.array([220.0]), mu, sg)[0]
    assert 0.4 < p < 0.6


def test_b_with_w_zero_is_m_and_same_rows(base: pl.DataFrame) -> None:
    _, res, skipped = P.run_pipeline(base, min_prior=150)
    assert res.height > 300 and skipped
    sc = P.score_arms(res)
    assert len({len(v) for v in sc.values()}) == 1  # same rows for every arm
    z = res.filter(pl.col("w") == 0.0)
    if z.height:
        s0 = P.score_arms(z)
        assert np.allclose(s0["crps_B"], s0["crps_M"])


def test_x_of_uses_window_lg_hca() -> None:
    d = {
        "pace_h": np.array([100.0]),
        "pace_a": np.array([96.0]),
        "off_h": np.array([112.0]),
        "def_h": np.array([110.0]),
        "off_a": np.array([111.0]),
        "def_a": np.array([113.0]),
    }
    x = P.x_of(d, 111.0, 2.0)
    assert x[0] == pytest.approx(98.0 * ((112 + 113 - 111 + 1) + (111 + 110 - 111 - 1)) / 100)


def test_planted_future_guard(base: pl.DataFrame) -> None:
    r = P.planted_future(base, dt.date(2024, 2, 1), min_prior=150)
    assert r["max_feature_diff_before"] == 0.0 and r["max_forecast_diff_before"] == 0.0
    assert r["pass"]


def test_shuffle_not_inert_and_placebo_no_gain() -> None:
    b = synth_base(line_has_pace=False)
    _, res, _ = P.run_pipeline(b, min_prior=150)
    rep = P.season_report(res, 2024)
    assert rep["crps_B_minus_M"]["mean"] < 0  # the line ignores pace here, so pace has real signal
    g = P.guard_placebo_shuffle(b, rep, 2024, min_prior=150)
    assert g["shuffle_P_worse_by"] >= 0.5
    assert g["shuffle"]["crps_B_minus_M"]["mean"] > rep["crps_B_minus_M"]["mean"] + 1.0
    assert g["placebo"]["crps_B_minus_M"]["mean"] > rep["crps_B_minus_M"]["mean"]


def test_placebo_past_only_uses_no_future() -> None:
    v = np.arange(60, dtype=float)
    out = P.rolling_prior(v, rng=np.random.default_rng(0))
    assert np.all(out[5:] <= v[:55].max() + 1e-9) and np.all(out[5:] < v[5:] + 1e-9)


def test_decision_score_profit_and_lean() -> None:
    n = 400
    rng = np.random.default_rng(1)
    r = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(n)],
            "y": rng.integers(200, 240, n).astype(float),
            "dk_point": np.full(n, 220.5),
            "dk_po": np.full(n, -110.0),
            "dk_pu": np.full(n, -110.0),
            "mu_B": np.full(n, 228.0),
            "sig_B": np.full(n, 12.0),
        }
    )
    d = P.decision_score(r)
    assert d["n_bets"] == n and d["status"] == "ok"
    y = r["y"].to_numpy()
    assert d["roi"] == pytest.approx(np.where(y > 220.5, 100 / 110, -1.0).mean())


def test_verdict_order_and_floors() -> None:
    ok = {"crps": True, "logloss": True, "decision": True}
    assert P.verdict(ok, True, True) == "passes"
    assert P.verdict({**ok, "crps": False}, True, True) == "fails_crps"
    assert P.verdict({**ok, "logloss": False}, True, True) == "fails_logloss"
    assert P.verdict({**ok, "decision": False}, True, True) == "fails_decision_guard"
    assert (
        P.verdict(ok, False, True) == "fails_guard" and P.verdict(ok, True, False) == "fails_guard"
    )


def test_refuses_unfrozen(tmp_path: Path) -> None:
    doc = tmp_path / "r.md"
    doc.write_text("Status: **FROZEN**\nrule\n" + P.MARKER + "\nresults\n")
    sha = P.frozen_sha(doc)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        P.require_frozen("0" * 64, doc, git=lambda *a: "")
    with pytest.raises(RuntimeError, match="no commit"):
        P.require_frozen(sha, doc, git=lambda *a: "abc\tother subject\n")
    assert P.require_frozen(sha, doc, git=lambda *a: f"abc\tprereg(PACE_TOTALS): {sha}\n") == sha
    doc.write_text("no marker here\n")
    with pytest.raises(RuntimeError, match="RESULTS BELOW"):
        P.require_frozen(sha, doc, git=lambda *a: "")
    with pytest.raises(RuntimeError, match="64 hex"):
        P.require_frozen(None, doc)


def test_results_below_text_does_not_change_sha(tmp_path: Path) -> None:
    doc = tmp_path / "r.md"
    doc.write_text("rule\n" + P.MARKER + "\nA\n")
    s1 = P.frozen_sha(doc)
    doc.write_text("rule\n" + P.MARKER + "\nB\n")
    assert P.frozen_sha(doc) == s1


def test_full_run_all_on_synthetic(base: pl.DataFrame) -> None:
    out = P.run_all(base, min_prior=150, cut=dt.date(2024, 2, 1))
    assert out["verdict"] in {
        "passes",
        "fails_crps",
        "fails_logloss",
        "fails_decision_guard",
        "fails_guard",
    }
    assert out["planted_future"]["pass"]
