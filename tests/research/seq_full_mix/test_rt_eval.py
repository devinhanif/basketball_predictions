# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX_RT scorer binding: its own rule, outputs and export paths, the real-tip expectation, run #1 files by window prefix, window check on the held-out run."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from research.eval import seq_full_mix_eval as S

NAMES = (
    "RULE",
    "ID",
    "VARIANT",
    "WINDOW",
    "SEQ_STORED",
    "OOF_PREFIX",
    "OUT_DIR",
    "REPORT_MD",
    "CACHE",
    "EXPORT25_META",
    "EXPORT25_NPZ",
    "EXPORT25_INFO",
    "STORED_META",
    "EXPECTED_AVG_MINUS_FULL",
    "STAGE2_DIR",
)


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch):
    for n in NAMES:  # restore the base binding after the test
        monkeypatch.setattr(S, n, getattr(S, n))
    return S


def test_configure_rt_rebinds_every_path_and_the_rule(rt, tmp_path: Path) -> None:
    rt.configure_rt(tmp_path, "WALL")
    assert (
        rt.ID == "SEQ_FULL_MIX_RT"
        and rt.RULE.ID == "SEQ_FULL_MIX_RT"
        and rt.VARIANT == "rt"
        and rt.OOF_PREFIX == "WALL_"
        and rt.WINDOW == "WALL"
    )
    assert (
        rt.OUT_DIR.name == "prereg_seq_full_mix_rt"
        and rt.STAGE2_DIR == rt.OUT_DIR / "stage2"
        and "seq_full_mix_rt" in str(rt.EXPORT25_NPZ)
        and "rt_cur" in str(rt.STORED_META)
    )
    assert rt.EXPECTED_AVG_MINUS_FULL == -0.0087  # the 2024 clean-game figure, not the proxy figure
    rt.configure_rt(None, "WALL")
    assert (
        rt.SEQ_STORED is None and rt.OOF_PREFIX == ""
    )  # the held-out run reads run #2's unprefixed files


def test_base_binding_is_untouched_after_the_fixture() -> None:
    assert (
        S.ID == "SEQ_FULL_MIX"
        and S.VARIANT == "base"
        and S.OOF_PREFIX == ""
        and S.EXPECTED_AVG_MINUS_FULL == -0.007
    )


def test_rt_references_come_from_its_own_gate_not_from_t471(rt, tmp_path: Path) -> None:
    rt.configure_rt(tmp_path, "WALL")
    rt.OUT_DIR = tmp_path
    assert rt.ref_values() is None  # nothing before the gate has run
    c = {
        m: {str(s): {"delta": v} for s, v in zip((2023, 2024), vals, strict=True)}
        for m, vals in (("AVG_minus_A0", (-0.02, -0.019)), ("AVG_minus_FULL", (-0.011, -0.009)))
    }
    (tmp_path / "gate_draft.json").write_text(json.dumps({"stats": {"pts": {"contrasts": c}}}))
    a0, full = rt.ref_values()
    assert a0 == {2023: -0.02, 2024: -0.019} and full == {2023: -0.011, 2024: -0.009}


def test_held_out_run_checks_the_window_per_program(rt, tmp_path: Path) -> None:
    from tests.research.seq_full_mix.test_fixes import SHA, _run_dir

    rt.configure_rt(None, "WALL")
    run, exp = _run_dir(
        tmp_path, E2={"window": "W22"}, E2P={"window": "WALL"}, E2S={"window": "WALL"}
    )
    with pytest.raises(RuntimeError, match="E2.window"):
        rt.check_seq_run(run, SHA, exp)
    d2 = tmp_path / "ok"
    d2.mkdir()
    run2, exp2 = _run_dir(d2, E2={"window": "WALL"}, E2P={"window": "WALL"}, E2S={"window": "WALL"})
    rt.check_seq_run(run2, SHA, exp2)
    assert np.array_equal(np.load(run2 / "oof_E2.npz")["season"], [2023, 2024, 2025])


def test_the_rt_doc_states_the_code_constants_and_the_no_selection_rule() -> None:
    root = Path(__file__).resolve().parents[3]
    doc = (root / "docs/prereg/SEQ_FULL_MIX_RT.md").read_text().split("=== RESULTS BELOW ===\n")[0]
    for needle in (
        f"{S.FLOOR}",
        f"{S.PLACEBO_MARGIN}",
        f"{S.MIXTURE_MARGIN}",
        f"K = {S.NULL_K}",
        f"{S.SIGMA0_PRE:.5f}",
        "-0.0087",
        "W_ALL",
        "2018-19 is excluded",
        "never selected",
        "has_report = 0",
    ):
        assert needle in doc, needle
    assert "Chosen window" not in doc and "selection rule" not in doc.lower().replace(
        "no window selection", ""
    )
