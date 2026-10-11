"""GAME_MARKET_CONTEXT harness: arm names, freeze refusal, marker parsing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nba.props.context_residual import PROP_STATS, stat_feature_names
from research.eval import game_market_context_eval as ev
from research.eval.minutes_hazard_eval import frozen_sha
from research.features.game_market_context import MKT_COLS


def test_a0_is_production_and_arms_have_the_declared_swaps() -> None:
    for s in PROP_STATS:
        prod = stat_feature_names(s)
        assert ev.arm_names(s, "A0") == prod
        a1 = ev.arm_names(s, "A1")
        assert a1 == ev.arm_names(s, "P1a")
        for gone in (
            "exp_margin",
            "abs_margin",
            "r20_pts_for",
            "r20_pts_against",
            "opp_r20_pts_for",
            "opp_r20_pts_against",
        ):
            assert gone not in a1
        assert set(MKT_COLS) <= set(a1) and len(a1) == len(prod) - 2
        # same positions as the replaced columns
        assert a1.index("mkt_margin") == prod.index("exp_margin")
        assert a1.index("mkt_pace") == prod.index("r20_pts_against")
        a2 = ev.arm_names(s, "A2")
        assert a2 == ev.arm_names(s, "P1b") == [*prod, *MKT_COLS]


def test_a3_drops_every_own_stat_history_column_but_keeps_structure() -> None:
    for s in PROP_STATS:
        a3 = ev.arm_names(s, "A3")
        for st in PROP_STATS:
            assert f"m10_{st}" not in a3
        for p in ("m5", "m40", "m100", "sd10", "role_gap", "rate"):
            assert f"{p}_{s}" not in a3
        for keep in ("min10", "starter10", "n_out_rot", "mkt_total", f"vac_{s}", f"opp_allow_{s}"):
            assert keep in a3


def _doc(tmp: Path, status: str, header_quote: bool) -> Path:
    head = f"Status: {status}\n"
    if header_quote:  # the freeze command quoted in the header contains the marker text
        head += "Freeze: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' doc | shasum -a 256`\n"
    p = tmp / "d.md"
    p.write_text(head + "body\n=== RESULTS BELOW ===\nresults that must not be hashed\n")
    return p


def test_marker_is_a_whole_line_and_hash_excludes_results(tmp_path: Path) -> None:
    p = _doc(tmp_path, "DRAFT", True)
    expect = hashlib.sha256(
        p.read_text().split("\n=== RESULTS BELOW ===")[0].encode() + b"\n"
    ).hexdigest()
    assert frozen_sha(p) == expect
    p.write_text(p.read_text() + "more results\n")
    assert frozen_sha(p) == expect


def test_run_refuses_draft_status_and_wrong_sha(tmp_path: Path) -> None:
    p = _doc(tmp_path, "DRAFT", True)
    sha = frozen_sha(p)
    with pytest.raises(SystemExit):
        ev.require_frozen(p, sha, False)  # right hash, status not FROZEN
    with pytest.raises(SystemExit):
        ev.require_frozen(p, "0" * 64, False)
    with pytest.raises(SystemExit):
        ev.require_frozen(p, None, False)
    assert ev.require_frozen(p, None, True) == sha  # gate only
    q = _doc(tmp_path, "FROZEN 2026-10-12", True)
    assert ev.require_frozen(q, frozen_sha(q), False)


def test_allow_draft_is_gate_only() -> None:
    with pytest.raises(SystemExit):
        ev.main(["run", "--allow-draft"])


def _row(d: float, hi: float, p_bh: float | None, md: float, mhi: float, mp: float | None) -> dict:  # type: ignore[type-arg]
    return {
        "dcrps": d,
        "ci_hi": hi,
        "p_bh": p_bh,
        "mature": {"dcrps": md, "ci_hi": mhi, "p_bh": mp, "p": 0.01, "n": 10},
    }


def test_r1_2023_uses_mature_rows_and_2024_all_rows() -> None:
    # all-row 2023 fails but mature rows pass: AM1 judges 2023 on the mature rows
    r = _row(-0.001, 0.002, 0.4, -0.006, -0.001, 0.01)
    assert ev.r1_passes(r, 2023) is True
    assert ev.r1_passes(r, 2024) is False  # 2024 reads the all-row fields
    # all-row passes but mature fails: 2023 fails
    r2 = _row(-0.006, -0.001, 0.01, -0.002, 0.001, 0.5)
    assert ev.r1_passes(r2, 2023) is False and ev.r1_passes(r2, 2024) is True
    # each leg of the rule is required, on the mature fields in 2023
    assert ev.r1_passes(_row(0, 0, 1, -0.0049, -0.001, 0.01), 2023) is False  # above the floor
    assert ev.r1_passes(_row(0, 0, 1, -0.006, 0.0, 0.01), 2023) is False  # CI upper not < 0
    assert ev.r1_passes(_row(0, 0, 1, -0.006, -0.001, 0.05), 2023) is False  # BH p not < 0.05


def test_add_bh_mature_family_is_separate() -> None:
    rows = []
    for season in ev.TEST_SEASONS:
        for arm in ("A1", "A2"):
            for i, stat in enumerate(ev.STATS):
                rows.append(
                    {
                        "season": season,
                        "arm": arm,
                        "stat": stat,
                        "p": 0.001 * (i + 1),
                        "p_bh": None,
                        "mature": {"p": 0.5 if season == 2023 else 0.001, "p_bh": None},
                    }
                )
    ev.add_bh(rows)
    s23 = [r for r in rows if r["season"] == 2023]
    assert all(r["p_bh"] < 0.05 for r in s23)  # all-row p-values small
    assert all(r["mature"]["p_bh"] >= 0.5 for r in s23)  # mature p-values large: not rescued
