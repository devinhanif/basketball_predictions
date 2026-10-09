"""Real tip-off gating of official-report snapshots (red-team regression)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from nba.features.game_tipoff import attach_real_tips, build_game_tipoff, tip_coverage
from nba.sim.usage_redistribution import ReportTriggerConfig, latest_pretip_flagged


def _rows() -> pl.DataFrame:
    d = dt.date(2024, 1, 15)
    # g1: 13:00 ET tip; 11:00 report lists p1 OUT, 17:00 report lists p2 OUT
    # g2: 20:00 ET tip; same two snapshots
    recs = []
    for g in ("g1", "g2"):
        recs.append((g, 1, "out", dt.datetime(2024, 1, 15, 11, 0), d))
        recs.append((g, 2, "out", dt.datetime(2024, 1, 15, 17, 0), d))
    return pl.DataFrame(
        recs,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "game_date": pl.Date,
        },
        orient="row",
    )


def _tips(with_g1: bool = True) -> pl.DataFrame:
    rows = [("g2", dt.datetime(2024, 1, 16, 1, 0), dt.datetime(2024, 1, 15, 20, 0))]
    if with_g1:
        rows.append(("g1", dt.datetime(2024, 1, 15, 18, 0), dt.datetime(2024, 1, 15, 13, 0)))
    return pl.DataFrame(
        rows,
        schema={"game_id": pl.Utf8, "tipoff_utc": pl.Datetime("us"), "tip_et": pl.Datetime("us")},
        orient="row",
    )


def test_matinee_does_not_use_post_tip_report_when_real() -> None:
    rows = attach_real_tips(_rows(), _tips())
    flag, used = latest_pretip_flagged(rows, ReportTriggerConfig(tip_source="real"))
    assert flag["g1"] == {1}  # 17:00 snapshot is after 13:00 tip - 60: not usable
    assert used["g1"] == dt.datetime(2024, 1, 15, 11, 0)
    assert flag["g2"] == {2}  # 20:00 tip: 17:00 snapshot is fine


def test_proxy19_default_unchanged_and_admits_post_tip_report() -> None:
    flag, _ = latest_pretip_flagged(_rows(), ReportTriggerConfig())
    assert flag["g1"] == {2}  # documents the known proxy defect
    # proxy path ignores any tip_et column: byte-identical
    withtip = attach_real_tips(_rows(), _tips())
    flag2, used2 = latest_pretip_flagged(withtip, ReportTriggerConfig(tip_source="proxy19"))
    f0, u0 = latest_pretip_flagged(_rows(), ReportTriggerConfig())
    assert flag2 == f0 and used2 == u0
    assert ReportTriggerConfig().tip_source == "proxy19"


def test_missing_real_tip_falls_back_to_proxy() -> None:
    rows = attach_real_tips(_rows(), _tips(with_g1=False))
    flag, _ = latest_pretip_flagged(rows, ReportTriggerConfig(tip_source="real"))
    assert flag["g1"] == {2}
    cov = tip_coverage(_rows(), _tips(with_g1=False))
    assert cov == {"n_games": 2, "with_real_tip": 1, "proxy_fallback": 1}


def test_real_requires_tip_column_and_validates_source() -> None:
    with pytest.raises(ValueError):
        latest_pretip_flagged(_rows(), ReportTriggerConfig(tip_source="real"))
    with pytest.raises(ValueError):
        latest_pretip_flagged(_rows(), ReportTriggerConfig(tip_source="bogus"))


def test_build_game_tipoff_parses_schedule(tmp_path: Path) -> None:
    pl.DataFrame(
        {
            "gameId": ["0022300001", "0022300002"],
            "gameDateTimeEst": ["2023-10-24T19:30:00Z", None],
            "gameDateTimeUTC": ["2023-10-24T23:30:00Z", None],
        }
    ).write_parquet(tmp_path / "raw_2023-24.parquet")
    t = build_game_tipoff(tmp_path)
    assert t.height == 1
    r = t.row(0, named=True)
    assert r["tip_et"] == dt.datetime(2023, 10, 24, 19, 30)
    assert r["tipoff_utc"] == dt.datetime(2023, 10, 24, 23, 30)
