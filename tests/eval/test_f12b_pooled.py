"""F12B_POOLED gate: no 2025 window; the pooled gate runs on a synthetic DB, spread only."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.eval.test_f12b_closing_risk import _db

from nba.eval import f12b_pooled as P


def test_windows_are_the_declared_select_and_report_seasons() -> None:
    assert P.WINDOWS["select"][1] == tuple(range(2013, 2022))
    assert P.WINDOWS["report"][1] == (2022, 2023, 2024)
    assert max(max(s) for _, s in P.WINDOWS.values()) <= 2024
    assert P.HALF_WIDTH_MAX == 0.04


def test_pooled_gate_on_synthetic_dbs_reports_spread_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path / "t.duckdb")
    monkeypatch.setattr(P, "WINDOWS", {"select": (db, (2023,)), "report": (db, (2023,))})
    res = P.run_gate(Path("/"))
    names = [c["check"] for c in res["checks"]]
    assert any(n.startswith("G5 pooled") and "(report)" in n for n in names)
    assert any(n.startswith("G1b") for n in names)
    assert not any(n.startswith("G2 as written") for n in names)
    pooled = res["windows"]["report"]["pooled"]
    assert set(pooled) == {
        "cells",
        "young_gap_halfwidth_no_exclusion",
        "inflation_sqrt_12_over_A",
        "young_gap_halfwidth_projected",
    }
    assert not res["all_ok"]  # two synthetic games cannot fill the G4 cells
