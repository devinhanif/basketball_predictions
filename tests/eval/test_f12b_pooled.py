# ruff: noqa: E501
"""F12B_POOLED gate: no 2025 window; the pooled gate runs on a synthetic DB, spread only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
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


# --------------------------------------------------------------------------- second review 2026-10-27 (B1, m2, m3, m6, REPORT path)
def _db_window(path: Path, with_2025: bool = True) -> str:
    """Two 2023 games (g1 close, g2 blowout with no garbage: margins small until the end), one planted season-2025 game, a box
    `starter` column and `players_static.draft_status`."""
    import duckdb

    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    con.execute(
        "INSERT INTO games VALUES ('g1', '2023-11-01', 2023, 1, 2, 100, 98), ('g2', '2023-11-02', 2023, 1, 2, 130, 100)"
    )
    if with_2025:
        con.execute("INSERT INTO games VALUES ('g2025', '2025-11-02', 2025, 1, 2, 120, 100)")
    con.execute(
        "CREATE TABLE possessions (game_id VARCHAR, poss_idx INTEGER, period INTEGER, clock_start FLOAT, clock_end FLOAT, off_team INTEGER, score_diff INTEGER, pts INTEGER)"
    )
    games = ["g1", "g2", *(["g2025"] if with_2025 else [])]
    for g in games:
        con.execute(
            "INSERT INTO possessions VALUES (?, 0, 4, 704, 600, 1, 2, 0), (?, 1, 4, 600, 0, 2, -2, 0)",
            [g, g],
        )
    con.execute(
        "CREATE TABLE stints (game_id VARCHAR, team_id INTEGER, period INTEGER, start_clock FLOAT, end_clock FLOAT, players INTEGER[5], stint_idx INTEGER)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INTEGER, minutes DOUBLE, starter BOOLEAN)"
    )
    for g in games:
        for per in (1, 2, 3, 4):
            con.execute(
                "INSERT INTO stints VALUES (?, 1, ?, 720, 0, [10, 11, 12, 13, 14], ?)",
                [g, per, per],
            )
        for p in (10, 11, 12, 13, 14):
            con.execute("INSERT INTO player_game_stats VALUES (?, ?, 48.0, ?)", [g, p, p != 14])
    con.execute(
        "CREATE TABLE players_static (player_id INTEGER, draft_year INTEGER, draft_status VARCHAR)"
    )
    con.execute(
        "INSERT INTO players_static VALUES (10, 2022, 'drafted'), (11, 2015, 'drafted'), (12, 2015, 'drafted'), (13, 2015, 'drafted'), (14, NULL, 'undrafted')"
    )
    con.close()
    return str(path)


class _Spy:
    """duckdb.connect spy: every connection must be read-only; every result is scanned for the planted 2025 game."""

    def __init__(self, real: Any) -> None:
        self.real, self.read_only, self.hits = real, [], []

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.read_only.append(bool(k.get("read_only")))
        con = self.real(*a, **k)
        spy = self

        class Res:
            def __init__(self, r: Any) -> None:
                self.r = r

            def pl(self) -> Any:
                df = self.r.pl()
                if "game_id" in df.columns and "g2025" in df["game_id"].cast(str).to_list():
                    spy.hits.append(df.columns)
                return df

            def fetchall(self) -> Any:
                rows = self.r.fetchall()
                if any("g2025" in map(str, row) for row in rows):
                    spy.hits.append("fetchall")
                return rows

        class Con:
            def execute(self, *x: Any, **y: Any) -> Res:
                return Res(con.execute(*x, **y))

            def close(self) -> None:
                con.close()

        return Con()


def test_B1_no_query_returns_a_2025_row_and_every_connection_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    db = _db_window(tmp_path / "w.duckdb")
    spy = _Spy(duckdb.connect)
    monkeypatch.setattr(duckdb, "connect", spy)
    res = P.window_premise(db, (2023,))
    assert spy.read_only and all(spy.read_only)
    assert spy.hits == [], spy.hits
    assert res["games"] == 2 and "gap" in res["descriptive"]["young_starters_gap"]
    with pytest.raises(SystemExit, match="2024"):
        P._young_starters(db, pl.DataFrame(), [], (2025,))
    # negative control: the pre-fix unfiltered query IS caught by the spy
    con = duckdb.connect(db, read_only=True)
    con.execute("SELECT game_id, player_id, starter FROM player_game_stats").pl()
    con.close()
    assert spy.hits


def test_REPORT_path_is_computed_after_a_SELECT_pass_and_decided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_s, db_r = _db_window(tmp_path / "s.duckdb", False), _db_window(tmp_path / "r.duckdb")
    real = P.window_premise

    def fake(d: str, s: tuple[int, ...]) -> dict:  # type: ignore[type-arg]
        if d.endswith("s.duckdb"):
            return {"P1": True, "P2": True, "gaps": {}, "descriptive": {}}
        return real(d, s)

    monkeypatch.setattr(P, "window_premise", fake)
    monkeypatch.setattr(P, "WINDOWS", {"select": (db_s, (2023,)), "report": (db_r, (2023,))})
    monkeypatch.setattr(P, "require_frozen", lambda *a, **k: "abc")
    res = P.run_premise("a" * 64, Path("/"))
    rep = res["report"]
    assert isinstance(rep, dict) and rep["games"] == 2
    y = rep["gaps"]["young"]
    assert {"n_boot", "n_boot_dropped_nonfinite", "half_width"} <= set(y) and y["n_boot"] == 2000
    assert (
        res["verdict"] == "FAIL" and res["reason"] == "REPORT failed P1 or P2"
    )  # all gaps 0 -> P1 fails; half-width 0
    dn = rep["descriptive"]["draft_year"]
    assert (
        dn["null_draft_year_share_of_drafted_player_games"] == 0.0
        and dn["undrafted_share_of_player_games"] == 0.2
    )


def test_m3_wide_report_is_inconclusive_whatever_P2_says() -> None:
    ok = {"P1": True, "P2": True}
    wide_fail = {"P1": True, "P2": False, "gaps": {"young": {"half_width": 0.06}}}
    v = P.decide(ok, wide_fail)
    assert v["verdict"] == "INCONCLUSIVE" and v["report_P2"] is False
    assert (
        P.decide(ok, {"P1": False, "P2": True, "gaps": {"young": {"half_width": 0.03}}})["verdict"]
        == "FAIL"
    )
