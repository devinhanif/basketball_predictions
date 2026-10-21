# ruff: noqa: E501
"""MATCHUP_AVAILABILITY harness: as-of correctness (planted future), slice logic, refusal, bars frozen in code, no-2025 guard."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.eval import matchup_availability_eval as mev
from research.eval import matchup_availability_features as feat
from research.eval import matchup_availability_rule as rule
from research.eval import role_prior_salary_rule as role_rule

ROOT = Path(__file__).resolve().parents[3]


def _events(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for e in (1, 2):
        for day in range(0, 60, 2):
            for oth in (10, 11, 12):
                rows.append((e, oth, day, 2023, float(rng.uniform(0, 5))))
    return pd.DataFrame(rows, columns=["ent", "oth", "day", "season", "mm"])


def test_decayed_asof_hand_computed() -> None:
    ev = pd.DataFrame(
        {
            "ent": [1, 1, 1],
            "oth": [10, 10, 11],
            "day": [0, 1, 2],
            "season": 2023,
            "mm": [4.0, 2.0, 3.0],
        }
    )
    q = pd.DataFrame({"ent": [1], "day": [3], "season": 2023})
    long, hist = feat.decayed_asof(ev, q, ["mm"], decay=0.5)
    got = dict(zip(long.oth, long.mm, strict=True))
    assert got[10] == pytest.approx(4.0 * 0.25 + 2.0 * 0.5)
    assert got[11] == pytest.approx(3.0)
    assert hist.nhist.iloc[0] == 3
    # a query on the same day as an event does not see it (strictly before)
    q2 = pd.DataFrame({"ent": [1], "day": [2], "season": 2023})
    long2, h2 = feat.decayed_asof(ev, q2, ["mm"], decay=0.5)
    assert 11 not in set(long2.oth) and h2.nhist.iloc[0] == 2


def test_planted_future_does_not_move_asof_output() -> None:
    ev = _events()
    pert = ev.copy()
    fut = pert.day >= 40
    pert.loc[fut, "mm"] = 999.0
    q = pd.DataFrame({"ent": [1, 2, 1], "day": [11, 25, 39], "season": 2023})
    a, ha = feat.decayed_asof(ev, q, ["mm"])
    b, hb = feat.decayed_asof(pert, q, ["mm"])
    pd.testing.assert_frame_equal(a, b)
    assert (ha.nhist == hb.nhist).all()


def test_previous_season_window_drops_older() -> None:
    ev = pd.DataFrame(
        {"ent": [1, 1], "oth": [10, 10], "day": [0, 400], "season": [2021, 2023], "mm": [5.0, 7.0]}
    )
    q = pd.DataFrame({"ent": [1], "day": [800], "season": 2023})
    long, h = feat.decayed_asof(ev, q, ["mm"])
    assert long.mm.iloc[0] == pytest.approx(7.0) and h.nhist.iloc[0] == 1


def test_pick_primary_threshold_and_ties() -> None:
    s = pd.DataFrame(
        {"qidx": [0, 0, 0, 1, 1], "oth": [5, 6, 7, 8, 9], "w": [6.0, 3.0, 1.0, 5.0, 5.0]}
    )
    top = rule.pick_primary(s)
    assert top.loc[top.qidx == 0, "primary"].iloc[0] == 5
    assert top.loc[top.qidx == 0, "primary_share"].iloc[0] == pytest.approx(0.6)
    assert top.loc[top.qidx == 1, "primary"].iloc[0] == 8  # tie -> smaller id


def test_slice_labels_and_verdict_words() -> None:
    lab = rule.slice_labels(
        np.array([True, False, True, False]), np.array([False, True, True, False])
    )
    assert list(lab) == ["S1", "S2", "S12", "S3"]
    assert rule.verdict_word(True, True) == "PASS"
    assert rule.verdict_word(True, False) == "S1_ONLY"
    assert rule.verdict_word(False, True) == "S2_ONLY"
    assert rule.verdict_word(False, False) == "FAIL"


def test_out_opposing_requires_opposing_team() -> None:
    ta = pd.DataFrame({"player": [10, 11], "day": [0, 0], "team": [200, 100]})
    pairs = pd.DataFrame(
        {
            "game_id": ["g", "g", "g"],
            "oth": [10, 11, 12],
            "day": [5, 5, 5],
            "opp_team": [200, 200, 200],
        }
    )
    out = feat.out_opposing(pairs, {"g": {10, 11, 12}}, ta)
    assert list(out) == [True, False, False]  # 11 is on the other team now; 12 has no earlier game
    assert not feat.out_opposing(pairs, {}, ta).any()


def test_reason_string_does_not_need_numbers() -> None:
    s = rule.reason_string("D", "X", "wing stopper", 0.06)
    assert "primary defender X (wing stopper) OUT" in s and "+0.06" in s
    assert "freed" in rule.reason_string("O", "Y", "big", None, 0.3)
    with pytest.raises(ValueError):
        rule.reason_string("Z", "a", "b", None)


def test_shrunk_excess_shrinks_to_archetype() -> None:
    assert feat.shrunk_excess(10.0, 10.0, 500.0) == 0.0
    assert abs(feat.shrunk_excess(20.0, 10.0, 100.0)) < abs(
        feat.shrunk_excess(20.0, 10.0, 1000.0) * 10
    )


def test_season_guard() -> None:
    with pytest.raises(ValueError):
        rule.check_season(2025)
    assert rule.check_season(2024) == 2024


def test_constants_match_reused_power_method() -> None:
    assert (
        rule.N_BOOT == role_rule.N_BOOT
        and rule.SEED == role_rule.SEED
        and rule.ALPHA == role_rule.ALPHA
    )


def test_run_refuses_unfrozen(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        mev.require_frozen(None)
    doc = tmp_path / "d.md"
    doc.write_text("text\n=== RESULTS BELOW ===\n")
    with pytest.raises(SystemExit):
        mev.require_frozen("0" * 64, doc)
    with pytest.raises(SystemExit):
        mev.main(["run"])


def test_modules_do_not_query_2025_or_labels() -> None:
    import re

    for name in ("rule", "features", "gate", "eval"):
        text = (ROOT / f"research/eval/matchup_availability_{name}.py").read_text()
        assert not re.search(r"season\s*(=|>=|>|between)\s*2025|\b2025-26\b", text), name
        assert "read_only=False" not in text and "duckdb.connect(" not in text.replace(
            "read_only=True", ""
        ), name
    gate = (ROOT / "research/eval/matchup_availability_gate.py").read_text()
    assert (
        'read_rows("pts", S, ROW_COLS)' in gate
        and '"y"' not in gate.split("ROW_COLS = ")[1].split("\n")[0]
    )
