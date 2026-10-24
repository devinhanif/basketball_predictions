# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS eval and rule on synthetic arrays (no real data, no outcome of any real leg)."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import market_anchored_props_core as core
from research.eval import market_anchored_props_eval as E
from research.eval import market_anchored_props_rule as R
from research.eval.model_clv_eval import Boot
from tests.research.market_anchored_props.test_core import _rep, synth


def _aux(A: dict[str, np.ndarray], seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(A["m"])
    return {
        "min10": rng.uniform(5, 38, n), "team_game_no": rng.integers(1, 82, n).astype(float),
        "game_id": np.where(rng.random(n) < 0.9, "0022300001", "0042300001"), "n_soft": rng.integers(0, 12, n).astype(float),
        "tip_hour_et": rng.integers(17, 23, n).astype(float), "n_out_rot": rng.integers(0, 5, n).astype(float),
    }  # fmt: skip


def _scored_counts(A: dict[str, np.ndarray], y: np.ndarray) -> dict[int, int]:
    s1 = core.walk_forward(A, y, 2023, (2023,))["scored"].sum()
    s2 = core.walk_forward(A, y, 2024, (2023, 2024))["scored"].sum()
    return {2023: int(s1), 2024: int(s2)}


# ------------------------------------------------------------------ rule: frozen text, refusals, mirror


def test_frozen_text_hash_and_mirror() -> None:
    assert R.text_sha(R.DOC.read_text()) == R.FROZEN_SHA256
    R.check_mirror(core)


def test_mirror_fails_closed_on_a_drifted_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "LAM", 49.0)
    with pytest.raises(RuntimeError, match="differ from the frozen text"):
        R.check_mirror(core)


def test_freeze_commit_accepts_the_real_subject_suffix_and_refuses_others() -> None:
    sha = R.FROZEN_SHA256
    doc = R.DOC.read_text()

    def fake(subject: str) -> Any:
        def run(*args: str) -> str:
            if args[0] == "log":
                return f"abc123\t{subject}\n"
            return doc

        return run

    assert R.freeze_commit(sha, fake(f"prereg(MARKET_ANCHORED_PROPS): {sha} FREEZE")) == "abc123"
    assert R.freeze_commit(sha, fake(f"prereg(MARKET_ANCHORED_PROPS): {sha}")) == "abc123"
    assert R.freeze_commit(sha, fake(f"prereg(MARKET_ANCHORED_PROPS): {sha}x")) is None
    assert R.freeze_commit(sha, fake(f"prereg(OTHER): {sha}")) is None


def test_freeze_commit_is_reachable_in_this_checkout() -> None:
    out = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        cwd=R.ROOT,
        check=False,
    )
    if out.stdout.strip() != "true":
        pytest.skip("not a git checkout")
    assert R.freeze_commit(R.FROZEN_SHA256) is not None


def test_require_frozen_refuses_draft_and_wrong_sha(tmp_path: Path) -> None:
    d = tmp_path / "doc.md"
    d.write_text("Status: **DRAFT**\n=== RESULTS BELOW ===\n")
    with pytest.raises(RuntimeError, match="not frozen"):
        R.require_frozen(R.text_sha(d.read_text()), d)
    with pytest.raises(RuntimeError, match="required"):
        R.require_frozen("abc", d)
    with pytest.raises(RuntimeError, match="hashes to"):
        R.require_frozen("0" * 64)


def test_harness_pin_refuses_without_a_pin_commit_and_on_drift(tmp_path: Path) -> None:
    sha = R.FROZEN_SHA256
    doc = R.DOC.read_text()
    files = {rel: (R.ROOT / rel).read_text() for rel in R.HARNESS_FILES}
    blobs = {rel: __import__("hashlib").sha256(t.encode()).hexdigest() for rel, t in files.items()}
    pin = R.pin_of(blobs)

    def run_factory(with_pin: bool, drift: str | None = None) -> Any:
        def run(*args: str) -> str:
            if args[0] == "log":
                lines = [f"f0\tprereg(MARKET_ANCHORED_PROPS): {sha} FREEZE"]
                if with_pin:
                    lines.insert(0, f"p1\tharness(MARKET_ANCHORED_PROPS): {pin}")
                return "\n".join(lines)
            ref, _, rel = args[1].partition(":")
            if rel == R.DOC_REL:
                return doc
            if drift is not None and rel == drift:
                return files[rel] + "# drift\n"
            return files[rel]

        return run

    with pytest.raises(RuntimeError, match="not pinned"):
        R.require_harness_pinned(sha, run_factory(False), ok=lambda *a: True)
    with pytest.raises(RuntimeError, match="freeze commit"):
        R.require_harness_pinned(sha, run_factory(True, R.FROZEN_HARNESS[0]), ok=lambda *a: True)
    with pytest.raises(RuntimeError, match="pin commit"):
        R.require_harness_pinned(
            sha,
            run_factory(True, "research/eval/market_anchored_props_eval.py"),
            ok=lambda *a: True,
        )
    with pytest.raises(RuntimeError, match="descend"):
        R.require_harness_pinned(sha, run_factory(True), ok=lambda *a: False)
    assert set(R.require_harness_pinned(sha, run_factory(True), ok=lambda *a: True)) == set(
        R.HARNESS_FILES
    )


def test_seasons_after_2024_are_refused() -> None:
    with pytest.raises(ValueError):
        R.assert_seasons([2023, 2025])


# ------------------------------------------------------------------ words, precedence, consequences


def test_words_precedence_and_not_evaluated() -> None:
    sel_ok = _rep(-0.0005, -0.0005, -0.0005)
    inv = E.words(None, None, invalid="extras pin")
    assert inv == {
        "model": "NOT_EVALUATED",
        "market": "NOT_EVALUATED",
        "override": "NO_RESULT_INVALID_RUN",
    }
    k1 = _rep(0.0, 0.0, 0.0)
    k1["C_total"]["mean"] = 0.0011
    assert E.words(k1, None)["override"] == "KILLED_K1"
    assert (
        E.words(k1, _rep(-0.0035, 0.0, -0.0035))["model"] == "NOT_EVALUATED"
    )  # K1 wins over any report
    worse = _rep(0.002, 0.001, 0.0)
    assert E.words(sel_ok, worse) == {
        "model": "NOT_EVALUATED",
        "market": "NOT_EVALUATED",
        "override": "WORSE_THAN_MARKET",
    }
    assert E.words(sel_ok, _rep(-0.0015, -0.0012, -0.0015)) == {
        "model": "INFO",
        "market": "MISCALIBRATION_INFO",
        "override": "",
    }
    with pytest.raises(ValueError):
        E.words(sel_ok, None)
    # the K1 boundary: "above +0.0010" fires; exactly +0.0010 does not
    edge = _rep(0.0, 0.0, 0.0)
    edge["C_total"]["mean"] = 0.0010
    assert E.k1_word(edge) is None


def test_consequences_need_the_c_total_guard() -> None:
    rep = _rep(-0.0015, -0.0012, -0.0015)
    v = E.words(_rep(-0.0005, -0.0005, -0.0005), rep)
    c = E.consequences(v, rep)
    assert c["propose_logit_pool"] and c["propose_market_rescaling"] and c["stage3_written"]
    rep2 = _rep(-0.0015, -0.0012, -0.0015)
    rep2["C_total"]["hi"] = 0.0001  # the pool as a whole does not beat the market
    c2 = E.consequences(E.words(_rep(-0.0005, -0.0005, -0.0005), rep2), rep2)
    assert (
        not c2["propose_logit_pool"] and not c2["propose_market_rescaling"] and c2["stage3_written"]
    )
    c3 = E.consequences(E.words(None, None, "x"), None)
    assert c3 == {
        **c3,
        "evaluated": False,
        "stage3_written": False,
        "propose_logit_pool": False,
        "promoted": False,
    }
    det = E.consequences(
        {"model": "DETECTED_BELOW_INFO_FLOOR", "market": "NONE", "override": ""}, rep
    )
    assert det["propose_logit_pool_if_live_follow_up_passes"] and not det["propose_logit_pool"]


# ------------------------------------------------------------------ the secondary p-value (the losing tail)


def test_secondary_family_p_is_the_gain_tail_and_bh_over_ten() -> None:
    A = synth(n_per_month=1500, seed=21)
    y = core.draw_labels(core.world_eta(A, "mod", 0.5), 22)
    wf = core.walk_forward(A, y, 2024, (2023, 2024))
    rows = E.secondary_family(A, wf, b=200)
    assert [r["test"] for r in rows][:4] == [
        "C_mod:pts",
        "C_mod:reb",
        "C_mod:ast",
        "C_mod:fg3m",
    ] and len(rows) == 10
    # recompute one p by hand: (1 + #{bm >= 0}) / (B + 1), max over the three clusterings
    m = wf["scored"] & (A["stat_i"] == 0)
    v = (wf["ll_mm"] - wf["ll_mr"])[m]
    ps = []
    for key in ("game_idx", "ps_idx", "week_idx"):
        gi = np.unique(A[key][m], return_inverse=True)[1]
        bt = Boot(gi, int(gi.max()) + 1, seed=0, B=200)
        S = np.bincount(gi, weights=v, minlength=bt.G)
        C = np.bincount(gi, minlength=bt.G).astype(float)
        bm = (bt.W @ S) / (bt.W @ C)
        ps.append((1 + np.sum(bm >= 0)) / 201)
    assert rows[0]["p"] == pytest.approx(max(ps))
    assert (
        rows[0]["mean"] < 0 and rows[0]["p"] < 0.05
    )  # a planted gain gives a small p (not the opposite tail)
    assert all(0 < r["bh"] <= 1 for r in rows)


# ------------------------------------------------------------------ evaluate on synthetic worlds


def test_evaluate_end_to_end_on_a_planted_world(monkeypatch: pytest.MonkeyPatch) -> None:
    A = synth(n_per_month=1500, seed=31)
    y = core.draw_labels(core.world_eta(A, "both", 0.4), 32)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))
    res = E.evaluate(pl.DataFrame(), A, y, _aux(A), k=3, b=100, log=lambda s: None)
    assert set(res["words"]) == {"model", "market", "override"}
    assert res["words"]["model"] in R.MODEL_WORDS and res["words"]["market"] in R.MARKET_WORDS
    assert abs(res["identity_check"]["C_total_minus_(C_mkt+C_mod)"]) < 1e-12
    assert abs(res["identity_check"]["C_total_minus_(C_mod_first+C_mkt_after)"]) < 1e-12
    assert len(res["secondary_bh_family"]) == 10 and res["placebo_draws"]["K"] == 3
    assert (
        res["part_l"]["status"] == "NOT_COMPUTED"
        and res["exchange_slice_2024"]["status"] == "NOT_COMPUTED"
    )
    assert "stat_pts" in res["slices_2024"] and res["coefficients_by_month_2024"]


def test_evaluate_refuses_a_scored_count_that_differs() -> None:
    """The in-stage check is an assertion now (F5): the label-free check_scored_counts runs first in `run`."""
    A = synth(n_per_month=1500, seed=33)
    y = core.draw_labels(A["m"], 34)
    with pytest.raises(AssertionError, match="scored count drifted"):
        E.evaluate(pl.DataFrame(), A, y, _aux(A), k=2, b=50, log=lambda s: None)


def test_planted_k1_world_stops_before_stage_2(monkeypatch: pytest.MonkeyPatch) -> None:
    A = synth(n_per_month=1500, seed=35)
    flip = str(np.unique(A["month"][A["season"] == 2023])[2])
    y = core.draw_labels(E.planted_flip_world(A, 2023, f"{flip}-01", 2.0), 36)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))
    res = E.evaluate(pl.DataFrame(), A, y, _aux(A), k=2, b=50, log=lambda s: None)
    assert res["select_2023"]["C_total"]["mean"] > R.K1_SELECT_LIMIT
    assert res["words"] == {
        "model": "NOT_EVALUATED",
        "market": "NOT_EVALUATED",
        "override": "KILLED_K1",
    }
    assert "report_2024" not in res and not res["consequences"]["stage3_written"]


def test_planted_reversal_world_fires_worse_than_market(monkeypatch: pytest.MonkeyPatch) -> None:
    A = synth(n_per_month=2500, seed=71)
    y = core.draw_labels(E.planted_reversal_world(A, 1.0), 72)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))
    res = E.evaluate(pl.DataFrame(), A, y, _aux(A), k=2, b=100, log=lambda s: None)
    assert res["select_2023"]["C_total"]["mean"] < 0  # 2023 gains, K1 cannot fire
    assert res["words"] == {
        "model": "NOT_EVALUATED",
        "market": "NOT_EVALUATED",
        "override": "WORSE_THAN_MARKET",
    }


def test_refit_b0_md_reproduces_the_core_bit_for_bit() -> None:
    A = synth(n_per_month=1500, seed=37)
    y = core.draw_labels(core.world_eta(A, "mkt"), 38)
    wf = core.walk_forward(A, y, 2024, (2023, 2024))
    r = E.refit_b0_md(A, y, wf, 2024, (2023, 2024))
    assert len(r["coefs"]) == len(wf["coefs"])
    tab = E.coefficient_table(wf, r)
    assert set(tab[0]["slope_minus_1"]) == {"B0", "MR", "MM"}


def test_planted_future_passes_and_a_leak_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    A = synth(n_per_month=1500, seed=39)
    A["month"] = A["date"].astype("datetime64[M]")
    y = core.draw_labels(A["m"], 40)
    month = str(np.unique(A["month"][A["season"] == 2024])[2])
    assert E.planted_future(A, y, month)["pass"]
    real = core.walk_forward

    def leaky(A_: dict[str, np.ndarray], y_: np.ndarray, *a: Any, **k: Any) -> dict[str, Any]:
        out = real(A_, y_, *a, **k)
        out["lp_mm"] = out["lp_mm"] + 0.01 * float(y_.mean())  # every prediction sees every label
        return out

    monkeypatch.setattr(core, "walk_forward", leaky)
    assert not E.planted_future(A, y, month)["pass"]


def test_walk_forward_ages_raise_on_a_same_day_fit_row() -> None:
    from research.eval import market_prior_residual_rule as mrule

    with pytest.raises(ValueError):
        mrule.time_weights(
            np.array(["2024-01-01"], dtype="datetime64[D]"), np.datetime64("2024-01-01")
        )
    A = synth(seed=41)
    E.check_walk_forward_ages(A, 2024, (2023, 2024))  # strictly earlier rows: no raise


# ------------------------------------------------------------------ weeks, Part L, missingness, compare


def test_iso_weeks_follow_isocalendar_and_core_weeks_do_not() -> None:
    d = np.array(
        ["2024-01-01", "2024-01-03", "2024-01-04", "2024-01-07", "2024-01-08"],
        dtype="datetime64[D]",
    )
    iso = E.iso_week_idx(d)
    assert list(iso) == [0, 0, 0, 0, 1]  # Mon-Sun ISO week 2024-01 then 2024-02
    core_w = d.astype("datetime64[W]").astype(int)
    assert core_w[1] != core_w[2]  # numpy weeks start on Thursday: ambiguity A1
    assert [dt.date.fromisoformat(str(x)).isocalendar()[1] for x in d] == [1, 1, 1, 1, 2]
    A = {"date": d, "week_idx": core_w}
    assert E.with_week(A, "core") is A and list(E.with_week(A, "iso")["week_idx"]) == [
        0,
        0,
        0,
        0,
        1,
    ]
    with pytest.raises(ValueError):
        E.with_week(A, "other")


def test_jeffrey_anchoring() -> None:
    s = np.array([1.0, 0.9, 0.7, 0.5, 0.3, 0.1])  # S_A(k) for k = 0..5
    k0 = 3
    m0 = 0.42
    ks = np.arange(6)
    j = E.jeffrey(np.full(6, m0), s, np.full(6, s[k0]), ks, np.full(6, k0))
    assert j[k0] == pytest.approx(m0)  # anchored at the market's line
    assert np.all(np.diff(j) <= 0)  # still a survival function
    assert j[0] == pytest.approx(1.0)
    assert j[5] == pytest.approx(m0 * 0.1 / 0.5)
    assert j[1] == pytest.approx(1 - (1 - m0) * (1 - 0.9) / (1 - 0.5))
    assert np.isnan(
        E.jeffrey(np.array([0.5]), np.array([0.3]), np.array([1.0]), np.array([4]), np.array([3]))
    )[0]


def test_survival_at_edges() -> None:
    g = [[0.9, 0.6, 0.2]]
    assert list(E.survival_at(g * 5, np.array([0, 1, 2, 3, 4]))) == [1.0, 0.9, 0.6, 0.2, 0.0]


def test_part_l_on_synthetic_rungs() -> None:
    rng = np.random.default_rng(51)
    n = 12000
    days = np.sort(np.datetime64("2023-10-20") + rng.integers(0, 560, n).astype("timedelta64[D]"))
    L = {
        "V": rng.uniform(0.1, 0.9, n), "J": rng.uniform(0.1, 0.9, n), "dist": rng.integers(-6, 7, n),
        "stat_i": rng.integers(0, 4, n), "season": np.where(days < np.datetime64("2024-07-01"), 2023, 2024),
        "date": days, "month": days.astype("datetime64[M]"), "game_idx": np.arange(n) // 20,
    }  # fmt: skip
    L["dist"][L["dist"] == 0] = 1
    y = (rng.random(n) < L["J"]).astype(float)  # J is the truth here
    out = E.part_l(L, y, b=100)
    assert out["report_2024"]["J_minus_V"]["mean"] < 0
    assert (
        out["report_2024"]["n_vcal_scored"] > 0
        and "+1" in out["report_2024"]["by_distance_rungs_above_line_positive"]
    )


def test_missingness_table_shape() -> None:
    A = synth(seed=61)
    n = len(A["m"])
    legs = pl.DataFrame(
        {
            "n_soft": np.where(A["miss_u1"] == 1, 1, 5),
            "spread_abs": np.where(np.arange(n) % 50 == 0, np.nan, 5.0),
            "p_model": np.full(n, 0.5),
        }
    )
    y = (np.arange(n) % 2).astype(float)
    out = E.missingness(A, legs, y, np.where(np.arange(n) % 10 == 0, 2.0, 30.0))
    assert out["asymmetry"] and out["asymmetry"][0]["abs_diff"] >= out["asymmetry"][-1]["abs_diff"]
    assert "miss_u1|minutes" in out["2023"]


def test_compare_is_exact() -> None:
    a = {"x": [1.0, {"y": 2.0}], "seconds": 3, "z": float("nan")}
    assert E.compare(a, {"x": [1.0, {"y": 2.0}], "seconds": 9, "z": float("nan")}) == []
    assert E.compare(a, {"x": [1.0, {"y": 2.0000000000000004}], "seconds": 3, "z": float("nan")})
    assert E.compare(a, {"x": [1.0], "z": 1.0})


def test_gate_world_record_has_the_gate_keys() -> None:
    import inspect

    from research.eval import market_anchored_props_gate as gate

    assert list(inspect.signature(E.gate_world_record).parameters) == list(
        inspect.signature(gate.run_world).parameters
    )
    with E.eval_path_in_gate():
        assert gate.run_world is E.gate_world_record
        with pytest.raises(RuntimeError, match="never calls"):
            core.build_extras("x", "y")
    assert (
        gate.run_world is not E.gate_world_record and core.build_extras.__name__ == "build_extras"
    )


def test_live_odds_file_is_refused(tmp_path: Path) -> None:
    from research.eval import market_prior_residual_eval as mpr

    live = str(mpr.main_checkout() / E.LIVE_ODDS_REL)
    with pytest.raises(RuntimeError, match="byte copy"):
        E.dryrun(live, tmp_path / "x.json")
    with pytest.raises(RuntimeError, match="byte copy"):
        E.load_rungs(live, pl.DataFrame())
