"""Routed props wired into the daily loop; uncached games refresh hook; schema DDL."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.daily import ingest_step, pipeline
from nba.daily.pipeline import run_daily
from nba.daily.predict import ROUTED_PROPS_MODEL_NAME, routed_prop_predictions
from nba.daily.store import DDL
from nba.ingest import games as games_mod
from tests.daily.conftest import RUN_DATE, T1, T2, before_tip, slate_games


def _run(con: duckdb.DuckDBPyConnection, now: datetime, **kw: object) -> pipeline.RunSummary:
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=now, skip_ingest=True,
        skip_injury=True, n_sims=100, **kw,
    )  # type: ignore[arg-type]  # fmt: skip


def test_default_run_uses_routed_props_pre_tip(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, before_tip())
    assert ROUTED_PROPS_MODEL_NAME in s.model_status
    assert "FAILED" not in s.model_status[ROUTED_PROPS_MODEL_NAME]
    rows = con.execute(
        "SELECT made_at, tipoff, player_id, target, prediction FROM forward_predictions "
        "WHERE model_name = ?",
        [ROUTED_PROPS_MODEL_NAME],
    ).fetchall()
    assert rows and len(rows) == s.n_props_rows
    assert all(r[0] < r[1] for r in rows)
    pred = json.loads(rows[0][4])
    for k in ("mean", "std", "p_ge", "q10", "q50", "q90", "routed_to", "bucket", "n_sims"):
        assert k in pred
    assert pred["features_as_of_before"] == RUN_DATE.isoformat()


def test_injury_outs_excluded_from_routed_props(con: duckdb.DuckDBPyConnection) -> None:
    df = routed_prop_predictions(con, RUN_DATE, [("0022600001", T1, T2)], {1000}, n_sims=50)
    assert 1000 not in df["player_id"].to_list() and df.height > 0


def test_routed_failure_falls_back_loudly(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> pl.DataFrame:
        raise RuntimeError("sim exploded")

    monkeypatch.setattr(pipeline, "routed_prop_predictions", boom)
    s = _run(con, before_tip())
    assert "FAILED" in s.model_status[ROUTED_PROPS_MODEL_NAME]
    assert "props_rolling_avg_baseline" in s.model_status
    assert s.n_props_rows > 0  # win-prob and baseline props still written


def test_rolling_flag_keeps_baseline(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, before_tip(), props_model="rolling")
    assert ROUTED_PROPS_MODEL_NAME not in s.model_status


def test_refresh_season_games_is_uncached_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    frame = pl.DataFrame({"game_id": ["x"]})

    def fake(season: str) -> pl.DataFrame:
        calls.append(season)
        return frame

    monkeypatch.setattr(games_mod, "_fetch_games_for_season", fake)
    assert games_mod.refresh_season_games("2026-27") is frame
    assert games_mod.refresh_season_games("2026-27") is frame
    assert calls == ["2026-27", "2026-27"]  # every call hits the fetcher (no cache)


def test_ingest_step_defaults_to_refresh_season_games(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    empty = pl.DataFrame(
        schema={"game_id": pl.Utf8, "game_date": pl.Date, "season": pl.Int64,
                "home_team": pl.Int64, "away_team": pl.Int64, "home_pts": pl.Int64,
                "away_pts": pl.Int64}
    )  # fmt: skip

    def fake(season: str) -> pl.DataFrame:
        seen.append(season)
        return empty

    monkeypatch.setattr(ingest_step, "refresh_season_games", fake)
    ingest_step.incremental_ingest(con, "2026-27", 2026, pull_box=lambda c, g: None)
    assert seen == ["2026-27"]


def test_schema_sql_has_forward_tables_matching_store_ddl() -> None:
    schema = (Path(__file__).resolve().parents[2] / "nba" / "db" / "schema.sql").read_text()
    a = duckdb.connect(":memory:")
    a.execute(schema)
    b = duckdb.connect(":memory:")
    b.execute(DDL)
    for t in ("forward_predictions", "forward_scores"):
        assert a.execute(f"DESCRIBE {t}").fetchall() == b.execute(f"DESCRIBE {t}").fetchall()
    a.execute(schema)  # idempotent


def test_quantile_crps_matches_normal_closed_form() -> None:
    from scipy.stats import norm

    from nba.daily.settle import normal_crps, prediction_crps, quantile_crps
    from nba.props.forward import QUANTILE_TAUS

    taus = list(QUANTILE_TAUS)
    qs = [float(norm.ppf(t, loc=12.0, scale=5.0)) for t in taus]
    for y in (3.0, 12.0, 20.0):
        assert quantile_crps(taus, qs, y) == pytest.approx(normal_crps(12.0, 5.0, y), rel=0.06)
    pred = {"mean": 12.0, "std": 5.0, "q_grid": qs}
    assert prediction_crps(pred, 20.0) == quantile_crps(taus, qs, 20.0)
    assert prediction_crps({"mean": 12.0, "std": 5.0}, 20.0) == normal_crps(12.0, 5.0, 20.0)
    # an asymmetric (skewed) distribution is scored by its quantiles, not its mean/std
    skew = [float(x) for x in np.exp(np.linspace(0, 3, 19))]
    assert prediction_crps({"mean": 5.0, "std": 3.0, "q_grid": skew}, 1.0) != normal_crps(
        5.0, 3.0, 1.0
    )
