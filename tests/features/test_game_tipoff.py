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


# --- production default is now the real tip (maintainer decision 2026-10-09) ---


def test_production_defaults_are_real_tip_and_proxy19_still_available() -> None:
    from nba.eval.injury_elo_eval import feature_config_from, load_config
    from nba.models.injury_elo import InjuryFeatureConfig
    from nba.props.context_residual import ContextResidualConfig

    assert InjuryFeatureConfig().tip_source == "real"
    assert feature_config_from(load_config("configs/injury_elo.yaml")).tip_source == "real"
    assert ContextResidualConfig().report.tip_source == "real"
    # the old gate stays selectable, and the raw trigger config keeps its research default
    assert InjuryFeatureConfig(tip_source="proxy19").tip_source == "proxy19"
    assert ContextResidualConfig(report=ReportTriggerConfig()).report.tip_source == "proxy19"


def test_daily_context_training_path_never_uses_post_tip_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nba.props.context_residual import ContextResidualConfig, flagged_from_availability

    monkeypatch.setattr("nba.features.game_tipoff.build_game_tipoff", lambda *a, **k: _tips())
    r = _rows()
    cfg = ContextResidualConfig().report
    avail = r.select("game_id", "player_id", "status", "as_of").with_columns(
        pl.lit(cfg.sources[0]).alias("source")
    )
    games = (
        r.select("game_id", "game_date")
        .unique()
        .with_columns(home_team=pl.lit(1), away_team=pl.lit(2))
    )
    flagged, _ = flagged_from_availability(avail, games, cfg)
    assert flagged["g1"] == {1}  # 13:00 game: the 17:00 report is invisible
    assert flagged["g2"] == {2}


def test_daily_injury_training_path_never_uses_post_tip_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb

    from nba.models.injury_elo import InjuryFeatureConfig, build_injury_features

    monkeypatch.setattr("nba.features.game_tipoff.build_game_tipoff", lambda *a, **k: _tips())
    con = duckdb.connect(":memory:")
    games = pl.DataFrame(
        {
            "game_id": ["g1"],
            "game_date": [dt.date(2024, 1, 15)],
            "season": [2023],
            "home_team": [1],
            "away_team": [2],
            "home_pts": [100],
            "away_pts": [90],
        }
    )
    stats = pl.DataFrame(
        schema={
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "minutes": pl.Float64,
            "comp": pl.Float64,
        }
    )
    rows = _rows().filter(pl.col("game_id") == "g1")
    out = build_injury_features(
        con, games, stats, InjuryFeatureConfig(), report_rows=rows, with_oracle=False
    )
    assert out["report_as_of"][0] == dt.datetime(2024, 1, 15, 11, 0)  # not the 17:00 snapshot


def test_persist_live_tips_roundtrip_enters_build_game_tipoff(tmp_path: Path) -> None:
    from nba.features.game_tipoff import persist_live_tips

    persist_live_tips([("0022600001", dt.datetime(2026, 10, 22, 23, 30))], "2026-27", tmp_path)
    t = build_game_tipoff(tmp_path)
    assert t["game_id"].to_list() == ["0022600001"]
    assert t["tipoff_utc"][0] == dt.datetime(2026, 10, 22, 23, 30)
    assert t["tip_et"][0] == dt.datetime(2026, 10, 22, 19, 30)  # EDT
