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


def test_decide_follows_the_frozen_order_and_guard() -> None:
    ok = {"P1": True, "P2": True, "gaps": {"young": {"half_width": 0.03}}}
    assert P.decide({"P1": False, "P2": True}, None)["verdict"] == "FAIL"
    assert P.decide({"P1": True, "P2": False}, None)["reason"].endswith("REPORT not computed")
    assert P.decide(ok, {**ok, "P2": False})["verdict"] == "FAIL"
    assert P.decide(ok, ok)["verdict"] == "PASS"
    wide = {"P1": True, "P2": True, "gaps": {"young": {"half_width": 0.051}}}
    assert P.decide(ok, wide)["verdict"] == "INCONCLUSIVE"


def test_premise_refuses_without_the_frozen_rule_and_commit(tmp_path: Path) -> None:
    doc = tmp_path / P.DOC_REL
    doc.parent.mkdir(parents=True)
    doc.write_text("rule\n=== RESULTS BELOW ===\n")
    import hashlib

    sha = hashlib.sha256(b"rule\n").hexdigest()
    blob = doc.read_text()

    def git_ok(*a: str) -> str:
        if a[0] == "log":
            return f"abc\tprereg(F12B_POOLED): {sha[:8]} FROZEN\n"
        return blob

    def git_other(*a: str) -> str:
        return f"abc\tprereg(F12B_CLOSING_RISK): {sha[:8]}\n" if a[0] == "log" else blob

    assert P.require_frozen(sha, doc, git_ok) == "abc"
    with pytest.raises(SystemExit, match="no commit"):
        P.require_frozen(sha, doc, git_other)
    with pytest.raises(SystemExit, match="hashes to"):
        P.require_frozen("0" * 64, doc, git_ok)
    with pytest.raises(SystemExit, match="required"):
        P.require_frozen("", doc, git_ok)


def test_premise_computes_select_first_and_skips_report_when_select_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path / "t.duckdb")
    loaded: list[str] = []
    real = P.window_premise

    def rec(d: str, s: tuple[int, ...]) -> dict:  # type: ignore[type-arg]
        loaded.append(d)
        return real(d, s)

    monkeypatch.setattr(P, "window_premise", rec)
    monkeypatch.setattr(
        P, "WINDOWS", {"select": (db, (2023,)), "report": ("never.duckdb", (2024,))}
    )
    monkeypatch.setattr(P, "require_frozen", lambda *a, **k: "abc")
    res = P.run_premise("a" * 64, Path("/"))
    assert res["verdict"] == "FAIL" and res["report"].startswith("not computed")
    assert loaded == [str(Path("/") / db)]
    assert set(res["select"]["descriptive"]) >= {
        "per_season_unpowered",
        "mean_of_ratios_gap",
        "no_exclusion_gap",
    }
