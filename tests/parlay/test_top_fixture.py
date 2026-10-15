"""The app fixture is a labelled hindsight illustration of one season-2024 date, and rebuildable."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nba.parlay import top_history as th
from nba.parlay.assistant.numguard import check_prose, collect_numbers

FIXTURE = Path(__file__).resolve().parents[2] / "app" / "fixtures" / "top_parlays_example.json"


def test_fixture_is_labelled_season_2024_and_keeps_your_money() -> None:
    rep = json.loads(FIXTURE.read_text())
    assert rep["schema"] == "top_parlays/2" and rep["verdict"] == "keep_your_money"
    assert rep["illustration"] is True and rep["slate_date"] not in ("2024-11-12", "2024-11-20")
    assert all(leg["price_at"] < leg["tipoff"] for t in rep["sizes"]["2"] for leg in t["legs"])
    assert rep["made_at"] < min(
        leg["tipoff"] for ts_ in rep["sizes"].values() for t in ts_ for leg in t["legs"]
    )
    assert (
        rep["fixture"]["kind"] == "hindsight_illustration"
        and "not a forecast" in rep["fixture"]["label"]
    )
    day = date.fromisoformat(rep["slate_date"])
    assert date(2024, 10, 1) <= day <= date(2025, 6, 30)  # season 2024, never 2025-26
    assert set(rep["fixture"]["inputs_sha256"]) == {
        "pairs_2023.parquet",
        "model_rows_2023.parquet",
        "pairs_2024.parquet",
        "model_rows_2024.parquet",
    }
    assert len(rep["sizes"]["2"]) == 3 and len(rep["sizes"]["3"]) == 3
    assert rep["diagnostics"]["numguard_removed"] == 0
    for tickets in rep["sizes"].values():
        for t in tickets:
            assert {leg["book"] for leg in t["legs"]} == {"draftkings"}
            assert all(leg["price_at"] < leg["tipoff"] for leg in t["legs"])
            assert check_prose(t["why"], [*collect_numbers(t), 0.8])[1] == 0
            hs = t["hindsight"]
            assert (hs["note"] is not None) == hs["all_legs_hit"]  # only a hit is annotated
            if hs["note"]:
                assert hs["note"].startswith("This one hit;") and "bet" in hs["note"]


def test_fixture_rebuilds_byte_for_byte(history_dirs: tuple[Path, Path, Path]) -> None:
    pairs, model, corr = history_dirs
    rep = th.build_fixture(pairs, model, corr, date(2025, 1, 27))
    assert json.loads(json.dumps(rep, default=str)) == json.loads(FIXTURE.read_text())


def test_fixture_builder_refuses_season_2025_dates(history_dirs: tuple[Path, Path, Path]) -> None:
    import pytest

    pairs, model, corr = history_dirs
    with pytest.raises(ValueError, match="season-2024"):
        th.build_fixture(pairs, model, corr, date(2025, 11, 15))
