# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS eval: the 2026-10-24 review fixes F1-F6, F9 (synthetic arrays and monkeypatched loaders; no real label)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import market_anchored_props_core as core
from research.eval import market_anchored_props_eval as E
from research.eval import market_anchored_props_rule as R
from research.eval import market_prior_residual_eval as mpr
from tests.research.market_anchored_props.test_core import _rep, synth
from tests.research.market_anchored_props.test_eval import _aux, _scored_counts

# ------------------------------------------------------------------ F1: ISO weeks only


def test_run_parser_has_no_week_and_no_odds_copy() -> None:
    ap = E.build_parser()
    run_parser = next(a for a in ap._subparsers._group_actions if a.dest == "cmd").choices["run"]  # type: ignore[union-attr]
    opts = {o for a in run_parser._actions for o in a.option_strings}
    assert "--sha" in opts and "--week" not in opts and "--odds-copy" not in opts
    assert "extract" in next(a for a in ap._subparsers._group_actions if a.dest == "cmd").choices  # type: ignore[union-attr]
    assert R.WEEK_DEFINITION.startswith("iso")


# ------------------------------------------------------------------ F2, F3: pins, extract


def test_descriptive_and_model_rows_pins_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(R.DESCRIPTIVE_PINNED_SHA256, "exchange_quotes", "0" * 64)
    with pytest.raises(E.InvalidRun, match="differs from its pin"):
        E.check_descriptive_pins()
    monkeypatch.setitem(R.MODEL_ROWS_PINNED_SHA256, 2024, "0" * 64)
    try:
        mpr.resolve(E.MODEL_ROWS_REL.format(season=2024))
    except FileNotFoundError:
        with pytest.raises(E.InvalidRun, match="missing"):
            E.check_model_rows_pins()
        return
    with pytest.raises(E.InvalidRun, match="differs from its pin"):
        E.check_model_rows_pins()


def test_committed_extracts_match_their_pins_and_carry_no_label() -> None:
    got = E.check_descriptive_pins()
    assert got == R.DESCRIPTIVE_PINNED_SHA256
    for rel in R.DESCRIPTIVE_FILES.values():
        cols = set(pl.read_parquet_schema(R.ROOT / rel))
        assert not cols & {"y", "over", "under"}, cols


def test_extract_refuses_the_live_file_and_overwriting(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="byte copy"):
        E.extract(str(mpr.main_checkout() / E.LIVE_ODDS_REL))
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        E.extract(str(tmp_path / "copy.duckdb"))


# ------------------------------------------------------------------ F5: scored counts label-free


def test_scored_counts_are_label_free_and_equal_the_walk_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    A = synth(n_per_month=1500, seed=81)
    y = core.draw_labels(A["m"], 82)
    assert E.scored_counts(A) == _scored_counts(A, y)
    monkeypatch.setattr(R, "SCORED", {2023: 1, 2024: 2})
    with pytest.raises(E.InvalidRun, match="scored-leg reproduction"):
        E.check_scored_counts(A)


# ------------------------------------------------------------------ F6: WORSE_THAN_MARKET boundaries


def _tot(mean: float, lo: float) -> dict[str, Any]:
    rep = _rep(0.0, 0.0, 0.0)
    rep["C_total"] = {"mean": mean, "lo": lo, "hi": mean + 0.001, "n": 1000}
    return rep


def test_worse_than_market_boundaries() -> None:
    sel = _rep(-0.0005, -0.0005, -0.0005)
    assert core.verdict(_tot(0.0005, 1e-9), sel)["override"] == "WORSE_THAN_MARKET"
    assert core.verdict(_tot(0.0005, 0.0), sel)["override"] == ""
    assert core.verdict(_tot(0.000499, 1e-4), sel)["override"] == ""


# ------------------------------------------------------------------ F4: descriptive isolation


def test_a_descriptive_failure_leaves_the_words_and_records_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    A = synth(n_per_month=1500, seed=83)
    y = core.draw_labels(core.world_eta(A, "mod", 0.4), 84)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))

    def boom() -> Any:
        raise RuntimeError("DK loader broke")

    ok = E.evaluate(pl.DataFrame(), A, y, _aux(A), k=2, b=50, log=lambda s: None)
    bad = E.evaluate(
        pl.DataFrame(),
        A,
        y,
        _aux(A),
        k=2,
        b=50,
        dk_loader=boom,
        part_l_loader=boom,
        exchange_loader=boom,
        log=lambda s: None,
    )
    assert bad["words"] == ok["words"] and bad["report_2024"]["C_mod"] == ok["report_2024"]["C_mod"]
    for blk in ("draftkings_direction", "part_l", "exchange_slice_2024"):
        assert bad[blk]["status"] == "FAILED" and "broke" in bad[blk]["error"]
    assert bad["descriptive"] == "DONE" and "stat_pts" in bad["slices_2024"]


# ------------------------------------------------------------------ F9 (and F1, F4, F5 on the run path)


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, A: dict[str, np.ndarray], y: np.ndarray, calls: list[str]
) -> None:
    n = len(y)
    legs = pl.DataFrame(
        {"n_soft": np.full(n, 5), "spread_abs": np.full(n, 5.0), "p_model": np.full(n, 0.5)}
    )
    monkeypatch.setattr(R, "require_frozen", lambda sha: sha)
    monkeypatch.setattr(R, "require_harness_pinned", lambda sha: {})
    for f in (
        "check_pairs_pins",
        "check_extras_pins",
        "check_model_rows_pins",
        "check_descriptive_pins",
    ):
        monkeypatch.setattr(E, f, lambda: {})
    monkeypatch.setattr(E, "heavy_lock", lambda purpose: contextlib.nullcontext())
    monkeypatch.setattr(E, "load_legs_checked", lambda: (legs, {}, A))
    monkeypatch.setattr(E, "aux_columns", lambda legs_: _aux(A))

    def labels(legs_: pl.DataFrame, book: str = "pinnacle") -> np.ndarray:
        calls.append("labels")
        return y

    monkeypatch.setattr(E, "load_labels", labels)
    monkeypatch.setattr(
        E, "model_rows", lambda legs_, cols: pl.DataFrame({"minutes": np.full(n, 30.0)})
    )
    monkeypatch.setattr(E, "planted_future", lambda A_, y_: {"pass": True})

    def boom(*a: Any) -> Any:
        raise RuntimeError("descriptive input broke")

    monkeypatch.setattr(E, "dk_real", boom)
    monkeypatch.setattr(E, "part_l_real", boom)
    monkeypatch.setattr(E, "read_pinned", boom)


def test_run_path_order_iso_weeks_and_isolated_descriptive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    A = synth(n_per_month=1500, seed=85)
    y = core.draw_labels(
        core.world_eta(A, "mod", 0.5), 86
    )  # a gain world, so K1 does not stop the small synthetic run
    calls: list[str] = []
    _patch_run(monkeypatch, A, y, calls)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))
    real_stage1 = E.stage1

    def stage1(A_: dict[str, np.ndarray], y_: np.ndarray, b: int) -> Any:
        assert (tmp_path / "missingness.json").exists(), (
            "missingness must be written before any component"
        )
        assert np.array_equal(A_["week_idx"], E.iso_week_idx(A_["date"])), "run must use ISO weeks"
        calls.append("stage1")
        return real_stage1(A_, y_, b)

    monkeypatch.setattr(E, "stage1", stage1)
    monkeypatch.setattr(
        R, "K_PLACEBO", 2
    )  # small K and B for speed; the mirror check would (rightly) refuse this
    monkeypatch.setattr(R, "N_BOOT", 60)
    monkeypatch.setattr(R, "check_mirror", lambda c: None)
    assert E.run("f" * 64, out_dir=tmp_path) == 0
    assert calls[:2] == ["labels", "stage1"]
    res = json.loads((tmp_path / "results.json").read_text())
    assert res["week_definition"] == R.WEEK_DEFINITION and res["descriptive"] == "DONE"
    assert res["words"]["override"] in ("", "WORSE_THAN_MARKET")
    for blk in ("draftkings_direction", "part_l", "exchange_slice_2024"):
        assert res[blk]["status"] == "FAILED"


def test_run_refuses_before_labels_on_a_pin_or_scored_count_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    A = synth(n_per_month=1500, seed=87)
    y = core.draw_labels(A["m"], 88)
    calls: list[str] = []
    _patch_run(monkeypatch, A, y, calls)
    monkeypatch.setattr(R, "SCORED", {2023: 1, 2024: 2})
    assert E.run("f" * 64, out_dir=tmp_path) == 3
    res = json.loads((tmp_path / "results.json").read_text())
    assert res["words"] == {
        "model": "NOT_EVALUATED",
        "market": "NOT_EVALUATED",
        "override": "NO_RESULT_INVALID_RUN",
    }
    assert "labels" not in calls and "scored-leg" in res["invalid_reason"]

    def bad_pin() -> dict[str, str]:
        raise E.InvalidRun("model_rows_2024.parquet sha256 differs from its pin")

    monkeypatch.setattr(E, "check_model_rows_pins", bad_pin)
    monkeypatch.setattr(R, "SCORED", _scored_counts(A, y))
    assert E.run("f" * 64, out_dir=tmp_path) == 3
    assert (
        "labels" not in calls
        and "model_rows" in json.loads((tmp_path / "results.json").read_text())["invalid_reason"]
    )
