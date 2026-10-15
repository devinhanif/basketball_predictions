# ruff: noqa: E501
"""Unit tests for the ALT_RUNG_CAL harness pieces and the freeze/pin refusals. Synthetic arrays only."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from research.eval import alt_rung_cal_eval as ev
from research.eval import alt_rung_cal_rule as rule


def test_shift_table_recovers_a_bin_shift_and_keeps_within_bin_resolution() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.5, 0.6, 20_000)
    hit = (rng.random(20_000) < np.clip(p + 0.03, 0, 1)).astype(float)
    out = ev.shift_table(p, hit, np.ones(p.size), np.array([0.51, 0.59]))
    assert abs((out[0] - 0.51) - 0.03) < 0.01
    assert (
        out[1] > out[0]
    )  # A0's resolution inside the bin is kept (a step table would give equal values)


def test_shift_table_is_identity_below_100_raw_rungs() -> None:
    p = np.full(99, 0.95)
    out = ev.shift_table(p, np.zeros(99), np.ones(99), np.array([0.95]))
    assert np.isclose(out[0], 0.95)
    out2 = ev.shift_table(np.full(100, 0.95), np.zeros(100), np.ones(100), np.array([0.95]))
    assert out2[0] < 0.2  # 100 raw rungs: the shift applies (and is clipped at the floor)


def test_hit_coupling_and_modes() -> None:
    q = np.array([24.0, 25.0, 0.3])
    assert list(ev.hit_of(q[:2], np.array([24.5, 24.5]), True)) == [0.0, 1.0]
    assert list(ev.hit_of(q[2:], np.array([0.5]), False)) == [1.0]


def test_cell_word_margins() -> None:
    assert ev.cell_word(0.01, -0.02, 0.02) == "CALIBRATED"
    assert ev.cell_word(-0.03, -0.04, -0.01) == "OVERCONFIDENT"
    assert ev.cell_word(0.03, 0.01, 0.05) == "UNDERCONFIDENT"
    assert ev.cell_word(-0.03, -0.05, 0.01) == "INCONCLUSIVE"


def test_verify_inputs_refuses_on_mismatch() -> None:
    meta = {
        "input_sha256": {v: "a" * 64 for v in ev.INPUT_NAMES.values()},
        "window_game_ids_sha256": {"2023": "b" * 64, "2024": "c" * 64},
    }
    decl = {k: "a" * 64 for k in ev.INPUT_NAMES} | {
        "WINDOW_IDS_SHA256_2023": "b" * 64,
        "WINDOW_IDS_SHA256_2024": "c" * 64,
    }
    assert all(ev.verify_inputs(meta, decl).values())
    decl["PAIRS_SHA256_2024"] = "d" * 64
    with pytest.raises(RuntimeError):
        ev.verify_inputs(meta, decl)


def test_text_sha_stops_at_marker_and_declared_parses_hash_lines(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"x\nNAME_A: {'1' * 64}\n{rule.MARKER}\nNAME_B: {'2' * 64}\n")
    assert rule.declared(doc) == {"NAME_A": "1" * 64}
    assert rule.text_sha(doc.read_text()) == rule.text_sha(
        f"x\nNAME_A: {'1' * 64}\n{rule.MARKER}\nother"
    )


def test_freeze_and_pin_refusals_with_a_fake_git(tmp_path: Path) -> None:
    doc_text = "rule text\n" + rule.MARKER + "\nresults\n"
    sha = rule.text_sha(doc_text)
    files = {rel: f"content of {rel}\n" for rel in rule.HARNESS_FILES}
    for rel, txt in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(txt)
    blobs = rule.disk_blobs(tmp_path)
    pin = rule.pin_of(blobs)

    def fake(*args: str) -> str:
        if args[0] == "log":
            return f"fc\tprereg(ALT_RUNG_CAL): {sha}\nph\tharness(ALT_RUNG_CAL): {pin}\n"
        if args[0] == "show":
            h, _, rel = args[1].partition(":")
            return doc_text if rel == rule.DOC_REL else files[rel]
        return ""

    assert rule.freeze_commit(sha, fake) == "fc"
    assert rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: True) == blobs
    with pytest.raises(RuntimeError, match="descend"):
        rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: False)
    (tmp_path / rule.HARNESS_FILES[0]).write_text("edited\n")
    with pytest.raises(RuntimeError):
        rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: True)
    with pytest.raises(RuntimeError):
        other = tmp_path / "other.md"
        other.write_text("a different rule\n")
        rule.require_frozen(sha, other, fake)


def test_bh_adjust_equals_the_nba_implementation() -> None:
    from nba.eval.context_residual_eval import bh_adjust as ref

    for p in ([0.01, 0.04, 0.03, 0.5], [0.2], [0.001, 0.001, 0.9, 0.04, 0.04, 0.3]):
        assert np.array_equal(ev.bh_adjust(np.array(p)), ref(np.array(p)))


def test_preflight_refuses_on_a_count_mismatch() -> None:
    n = 10
    R = {
        "pm": np.full(n, 0.6),
        "unit": np.array([f"u{i}" for i in range(n)]),
        "has_main": np.ones(n, bool),
    }
    fr = {
        "rungs": {ev.PRIMARY: R, "fanduel": R},
        "meta": {"window_games": {"2023": 39, "2024": 62}},
    }
    arms = {"scored": np.ones(n)}
    with pytest.raises(RuntimeError, match="pinned count mismatch"):
        ev.preflight(fr, [], [], arms, arms)


def test_check_t493_refuses_on_a_coefficient_mismatch(tmp_path: Path) -> None:
    import json

    sel = tmp_path / "select.json"
    sel.write_text(
        json.dumps(
            {
                "draftkings": {
                    "blocks_cal": [{"stat": "pts", "block": "2024-11", "beta": [0, 0, 0, 0, 0]}]
                }
            }
        )
    )
    good = [{"season": 2024, "stat": "pts", "block": "2024-11", "beta_main": [0.0, 0, 0, 0, 0]}]
    assert ev.check_t493(good, sel)["blocks"] == 1
    bad = [{"season": 2024, "stat": "pts", "block": "2024-11", "beta_main": [1e-6, 0, 0, 0, 0]}]
    with pytest.raises(RuntimeError, match="does not reproduce"):
        ev.check_t493(bad, sel)


def test_run_refuses_before_reading_anything_when_not_frozen(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        ev.main(["run", "--data-root", str(tmp_path / "nowhere"), "--sha", "0" * 64])
