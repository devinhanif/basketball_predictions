"""Self-tests (guard 8.6), end-to-end synthetic verdicts, and the real-data safety rails."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from research.market_forward import engine
from research.market_forward import score as sc
from research.market_forward.__main__ import (
    PREFLIGHT_LAST,
    _replicates,
    cmd_read,
    main,
)
from research.market_forward.fee import load_pinned_fee
from research.market_forward.report import format_look, format_preflight, look_json
from research.market_forward.selftest import SelfTestResult, run_selftests
from research.market_forward.spec import LONGSHOT, UNDERS, WINDOW_START
from research.market_forward.synth import (
    draw_outcomes,
    make_world,
    plant_scale,
    world_universe,
)

FEE = load_pinned_fee(UNDERS.all_series)


def _frames(spec, n_games: int, seed: int = 3):  # type: ignore[no-untyped-def]
    w = draw_outcomes(make_world(spec, n_games, seed=seed), np.random.default_rng(9))
    allf = sc.make_frame(w, FEE)
    return allf, allf.subset(np.asarray(w["in_pool"].to_numpy(), dtype=bool))


def test_selftests_pass_on_the_frozen_procedure() -> None:
    allf, pool = _frames(UNDERS, 60)
    r = run_selftests(allf, pool, k=200, b=400, seed=2026)
    assert r.identity_failures == 0 and r.identity_n == allf.n
    assert 0.02 <= r.null_lo_excl_rate <= 0.09, r
    assert r.plant_abs_diff <= 0.005 and r.ok


def test_selftest_catches_an_unclustered_ci() -> None:
    """If the CI ignores the nesting (every contract its own game and date) coverage collapses."""
    allf, pool = _frames(UNDERS, 25)
    n = pool.n
    bad = pool.df.with_columns(
        game=pl.Series([f"g{i}" for i in range(n)]), date=pl.Series([f"d{i}" for i in range(n)])
    )
    pool_bad = sc.Frame(
        bad,
        pool.bid_c,
        pool.ask_c,
        pool.y_u,
        pool.fee_no_u,
        pool.fee_yes_u,
        pool.r_no_u,
        pool.r_yes_u,
    )
    r = run_selftests(allf, pool_bad, k=200, b=150, seed=2026)
    assert not r.null_in_band and not r.ok
    assert r.null_lo_excl_rate > 0.09


def test_selftest_catches_a_broken_identity() -> None:
    allf, pool = _frames(UNDERS, 20)
    allf.r_yes_u[3] += 5
    r = run_selftests(allf, pool, k=4, b=50, seed=2026)
    assert r.identity_failures == 1 and not r.ok


def test_selftest_dnp_payoff_is_not_simulated() -> None:
    w = draw_outcomes(make_world(UNDERS, 30, seed=2), np.random.default_rng(1), dnp_rate=0.05)
    allf = sc.make_frame(w, FEE)
    pool = allf.subset(np.asarray(w["in_pool"].to_numpy(), dtype=bool))
    r = run_selftests(allf, pool, k=10, b=50, seed=2026)
    assert r.identity_failures == 0  # fractional scalar payoffs satisfy the identity exactly


def _stub_ok() -> SelfTestResult:
    return SelfTestResult(
        0, 1, 200, 0.05, 0.05, True, 0.0, 0.0, 0.0, 1.0, 0.05, 0.05, True, "s", True
    )


def test_calibrated_null_world_gives_no_positive_ev_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: _stub_ok())
    for spec in (LONGSHOT, UNDERS):
        w = make_world(spec, 400, seed=11)
        uni = world_universe(draw_outcomes(w, np.random.default_rng(5)))
        res = engine.score_look(spec, uni, FEE, 1, b=500)
        assert res.verdict.name == "NO_POSITIVE_EV_FOUND", (spec.rule_id, res.pooled)
        assert res.pooled is not None and res.pooled.R < 0
        assert res.n_games == 400 and res.n_dates >= 50


def test_planted_three_cent_edge_passes_and_ci_covers_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: _stub_ok())
    for spec in (LONGSHOT, UNDERS):
        w = make_world(spec, 400, seed=11)
        k = plant_scale(w, FEE, 0.03)
        uni = world_universe(draw_outcomes(w, np.random.default_rng(5), scale=k))
        res = engine.score_look(spec, uni, FEE, 1, b=500)
        assert res.pooled is not None
        assert res.verdict.name == "PASS", (spec.rule_id, res.pooled.R)
        assert res.pooled.decision.lo < 0.03 < res.pooled.decision.hi + 0.02
        assert abs(res.pooled.R - 0.03) < 0.02


def test_plant_scale_hits_the_target_edge_exactly() -> None:
    w = make_world(UNDERS, 100, seed=4)
    k = plant_scale(w, FEE, 0.03)
    base = sc.make_frame(w.filter(w["in_pool"]), FEE)
    truth = ((1 - k * base.mid) - base.p_no_u / 1e6 - base.fee_no_u / 1e6).mean()
    assert truth == pytest.approx(0.03, abs=1e-12)


def test_replicates_coverage_and_rates() -> None:
    w = make_world(UNDERS, 320, seed=11)
    k = plant_scale(w, FEE, 0.03)
    null = _replicates(UNDERS, FEE, w, 1.0, 30, 1, 300, 1)
    assert null["truth"] < 0 and null.get("PASS", 0.0) == 0.0
    assert null["lo_excl"] <= 0.15 and null["hi_excl"] <= 0.15
    plant = _replicates(UNDERS, FEE, w, k, 30, 1, 300, 2)
    assert plant["truth"] == pytest.approx(0.03) and plant.get("PASS", 0.0) >= 0.9


def test_insufficient_sample_prints_counts_only_for_both_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # C3: LONGSHOT Section 6 also prints counts and the projected date only below the minimum.
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: _stub_ok())
    monkeypatch.setattr(
        engine, "pooled", lambda *a, **k: (_ for _ in ()).throw(AssertionError("R computed"))
    )
    for spec in (UNDERS, LONGSHOT):
        w = make_world(spec, 40, seed=2)
        res = engine.score_look(
            spec, world_universe(draw_outcomes(w, np.random.default_rng(1))), FEE, 1, b=200
        )
        assert res.verdict.name == "INSUFFICIENT_SAMPLE" and res.pooled is None
        txt = format_look(res)
        assert "R = " not in txt and "ROI" not in txt and "BUCKETS" not in txt
        assert "VERDICT: INSUFFICIENT_SAMPLE" in txt and "counts only" in txt


def test_selftest_failure_prints_counts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = _stub_ok().__class__(1, 1, 200, 0.5, 0.0, False, 0, 0, 0, 0, 0, 0, False, "s", False)
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: bad)
    w = make_world(UNDERS, 400, seed=2)
    res = engine.score_look(
        UNDERS, world_universe(draw_outcomes(w, np.random.default_rng(1))), FEE, 1, b=200
    )
    txt = format_look(res)
    assert res.verdict.name == "SELFTEST_FAILED" and res.pooled is None
    assert "R = " not in txt and "PAPER KELLY" not in txt and "counts only" in txt
    assert "SELFTEST_FAILED" in look_json(res)


def test_unders_report_has_kelly_band_and_sensitivities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "run_selftests", lambda *a, **k: _stub_ok())
    w = make_world(UNDERS, 400, seed=11)
    res = engine.score_look(
        UNDERS, world_universe(draw_outcomes(w, np.random.default_rng(5))), FEE, 1, b=300
    )
    txt = format_look(res)
    assert "PAPER ONLY. Not a stake. Not advice." in txt
    assert "R_band" in txt and "S1 fee rounded" in txt and "S5 DNP" in txt
    assert not re.search(r"\$\s*\d", txt)  # no dollar stake amounts anywhere
    lres = engine.score_look(
        LONGSHOT,
        world_universe(draw_outcomes(make_world(LONGSHOT, 400, seed=11), np.random.default_rng(5))),
        FEE,
        1,
        b=300,
    )
    ltxt = format_look(lres)
    assert "KXNBASPREAD (descriptive only" in ltxt and "PAPER KELLY" not in ltxt


def test_project_date_uses_observed_rates() -> None:
    from datetime import timedelta

    sched = [date(2026, 10, 25) + timedelta(days=i) for i in range(120) for _ in range(5)]
    got = engine.project_date(sched, date(2026, 10, 25), date(2026, 11, 24), 150, 31)
    assert got is not None and got > date(2026, 11, 24)
    assert engine.project_date(sched, date(2026, 10, 25), date(2026, 11, 24), 0, 0) is None


# ---- real-data safety rails ----


def test_read_refuses_before_its_look_date(tmp_path: Path) -> None:
    ns = type(
        "NS",
        (),
        dict(
            rule="UNDERS_FORWARD",
            look=1,
            db=str(tmp_path / "x"),
            raw_root="r",
            schedule="s",
            nba_db=None,
            asof_db=None,
            json=None,
        ),
    )()
    with pytest.raises(SystemExit, match="opens 2027-01-15"):
        cmd_read(ns)  # today is before 2027-01-15


def test_preflight_window_is_outside_both_scoring_windows() -> None:
    assert PREFLIGHT_LAST < WINDOW_START


def test_cli_verify_and_refusal_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify"]) == 0
    assert "ok" in capsys.readouterr().out


def test_preflight_report_prints_no_return() -> None:
    allf, pool = _frames(UNDERS, 20)
    w = draw_outcomes(make_world(UNDERS, 20, seed=3), np.random.default_rng(9))
    pf = engine.preflight(UNDERS, world_universe(w), FEE, selftest_k=10, selftest_b=50)
    txt = format_preflight("UNDERS_FORWARD", pf, FEE.describe(), {})
    assert "NO returns" in txt and "R =" not in txt and "ROI" not in txt
    assert pf.identity_failures == 0


def test_no_order_or_credential_surface_in_the_package() -> None:
    root = Path(__file__).resolve().parents[3] / "research" / "market_forward"
    banned = re.compile(
        r"portfolio|create_order|place_order|/orders|api_key|apikey|authorization|bearer|"
        r"private_key|KALSHI_API|requests\.post|httpx\.post|\.post\(|urlopen|httpx|requests",
        re.IGNORECASE,
    )
    for p in sorted(root.glob("*.py")):
        hits = banned.findall(p.read_text())
        assert not hits, (p.name, hits)
