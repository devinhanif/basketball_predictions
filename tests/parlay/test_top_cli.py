"""`python -m nba.parlay top`: file contract, empty slate, holdout refusal, read-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_top_source import MADE, SLATE, build_nba, build_odds, quote

from nba.parlay import __main__ as cli
from nba.parlay.line_cal import MAP_PATH


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, date: str, *, odds_rows: list | None = None
) -> tuple[int, Path]:
    model = tmp_path / "joint_model.json"
    model.write_text(json.dumps({"corr": [["same_player", "ast", "pts", 0.18, 100]]}))
    odds_file = tmp_path / "market_asof.duckdb"
    odds_file.write_text("")  # existence only; the connection is injected below
    conns = iter([build_nba(), build_odds(odds_rows or [])])
    seen: list[dict] = []

    def fake_connect(path: object, **kw: object) -> object:
        seen.append(kw)
        return next(conns)

    monkeypatch.setattr(cli, "connect_with_retry", fake_connect)
    rc = cli.main(
        [
            "top", "--date", date, "--now", "2026-10-28T22:05:00", "--nba-db", "x.duckdb",
            "--odds-db", str(odds_file),
            "--model", str(model), "--cal-map", str(MAP_PATH), "--out-dir", str(tmp_path / "out"),
        ]
    )  # fmt: skip
    assert all(kw.get("read_only") is True for kw in seen) and len(seen) == 2
    return rc, tmp_path / "out" / f"top_parlays_{date}.json"


def test_top_writes_the_contract_file_with_keep_your_money(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [quote("draftkings", "over", -115), quote("draftkings", "under", -105)]
    rc, path = _run(tmp_path, monkeypatch, SLATE.isoformat(), odds_rows=rows)
    assert rc == 0 and path.exists()
    rep = json.loads(path.read_text())
    assert rep["schema"] == "top_parlays/2" and rep["slate_date"] == "2026-10-28"
    assert rep["verdict"] == "keep_your_money" and rep["calibration"]["sha8"] == "a5441242"
    assert rep["calibration"]["map_sha8"]
    assert rep["sizes"]["2"] == [] and rep["sizes"]["3"] == []  # one player: only one leg per stat
    assert rep["empty_reason"] and rep["gate"]["open"] is False and rep["illustration"] is False
    assert rep["made_at"] == "2026-10-28T22:05:00Z" and rep["made_at"] > MADE.isoformat()
    assert rep["empty_reason"].startswith("size 2")
    jm = rep["diagnostics"]["joint_model"]
    assert len(jm["sha256"]) == 64 and "gate shut" in " ".join(rep["diagnostics"]["notes"])
    assert rep["gate"]["open"] is False and rep["gate"]["settled"] == 0


def test_top_on_a_date_with_no_slate_writes_an_empty_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, path = _run(tmp_path, monkeypatch, "2026-11-30")
    rep = json.loads(path.read_text())
    assert rc == 0 and rep["empty_reason"] == "no slate" and rep["verdict"] == "keep_your_money"


def test_top_refuses_the_holdout_season(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="holdout"):
        _run(tmp_path, monkeypatch, "2025-12-02")


def test_no_order_or_credential_surface_in_the_new_modules() -> None:
    src = Path(cli.__file__).parent
    for name in ("top_parlays", "top_source", "top_history", "line_cal", "line_cal_build"):
        text = (src / f"{name}.py").read_text().lower()
        for bad in (
            "place_order",
            "create_order",
            "api_key",
            "apikey",
            "secret",
            "requests",
            "httpx",
            "urllib",
        ):
            assert bad not in text, (name, bad)


def test_main_does_not_import_the_top_parlays_modules_for_other_subcommands() -> None:
    """M1: the live `evaluate` path must not pull in the top-parlays modules (and, through them,
    the assistant and its dependencies). Imports of nba.parlay.top_* live inside cmd_top only."""
    import ast

    tree = ast.parse(Path(cli.__file__).read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name != "cmd_top":
            for node in ast.walk(fn):
                if isinstance(node, ast.ImportFrom | ast.Import):
                    mods = (
                        [node.module]
                        if isinstance(node, ast.ImportFrom)
                        else [a.name for a in node.names]
                    )
                    assert not any(m and m.startswith("nba.parlay.top_") for m in mods), fn.name
    top_level = [
        n
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("nba.parlay.top_")
    ]
    assert not top_level
