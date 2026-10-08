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
from nba.props.forward import SIM_STATS
from tests.daily.conftest import RUN_DATE, T1, T2, before_tip, slate_games


def _run(con: duckdb.DuckDBPyConnection, now: datetime, **kw: object) -> pipeline.RunSummary:
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=now, skip_ingest=True,
        skip_injury=True, n_sims=100, **kw,
    )  # type: ignore[arg-type]  # fmt: skip


def test_routed_run_uses_routed_props_pre_tip(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, before_tip(), props_model="routed")
    label = s.model_status[ROUTED_PROPS_MODEL_NAME]
    # the label follows SIM_STATS instead of a hardcoded (stale) routing description
    assert ("sim for " + "/".join(SIM_STATS) in label) if SIM_STATS else ("disabled" in label)
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
    s = _run(con, before_tip(), props_model="routed")
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


# -- context-residual primary + recency comparison -----------------------------------


def test_context_failure_falls_back_to_recency_with_reason(con: duckdb.DuckDBPyConnection) -> None:
    """The 12-game fixture is far too small to fit the model: the slate must still
    get recency predictions under BOTH names, with the reason recorded."""
    s = _run(con, before_tip())
    assert s.model_status["props_context_residual"].startswith("FAILED")
    assert "not enough rows" in s.model_status["props_context_residual"]
    rows = con.execute(
        "SELECT model_name, made_at, tipoff, prediction FROM forward_predictions "
        "WHERE target = 'pts' AND player_id <> -1"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {"props_context_residual", "props_recency_v1"}
    assert all(r[1] < r[2] for r in rows)
    prim = [json.loads(r[3]) for r in rows if r[0] == "props_context_residual"]
    assert all(
        p["routed_to"] == "recency_fallback" and "failed" in p["fallback_reason"] for p in prim
    )
    assert s.n_props_rows > 0


def test_context_run_writes_primary_and_recency_and_caches(tmp_path: object) -> None:
    from datetime import timedelta

    from nba.daily.schedule import ScheduledGame
    from tests.daily.conftest import T3, T4
    from tests.fixtures.loader import build_fixture_db
    from tests.props.test_forward_context import AS_OF, _big_history

    c = build_fixture_db(":memory:")
    _big_history(c, 100, seed=3)
    tip = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 30)
    games = [
        ScheduledGame("0022600901", tip, T1, T2),
        ScheduledGame("0022600902", tip + timedelta(hours=3), T3, T4),
    ]
    kw = dict(schedule_fn=lambda s: games, skip_ingest=True, skip_injury=True, n_sims=50)
    s1 = run_daily(c, AS_OF, now=tip - timedelta(hours=3), **kw)  # type: ignore[arg-type]
    st = s1.model_status["props_context_residual"]
    assert st.startswith("ctxres-v1") and "fit" in st and "FAILED" not in st
    assert "sim for" not in st and "0/2 games had a report" in st
    rows = c.execute(
        "SELECT model_name, count(*), min(version) FROM forward_predictions "
        "WHERE target IN ('pts','reb','ast','fg3m') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == ["props_context_residual", "props_recency_v1"]
    assert rows[0][1] == rows[1][1] > 0 and s1.n_props_rows == rows[0][1]
    pj = json.loads(
        c.execute(
            "SELECT prediction FROM forward_predictions WHERE model_name = 'props_context_residual'"
            " AND target = 'pts' LIMIT 1"
        ).fetchone()[0]  # type: ignore[index]
    )
    assert len(pj["q_grid"]) == 19 and pj["routed_to"] == "context_residual"
    s2 = run_daily(c, AS_OF, now=tip - timedelta(hours=2), **kw)  # type: ignore[arg-type]
    assert "cached" in s2.model_status["props_context_residual"]  # same day: no refit


def test_report_has_paired_props_section(con: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.report import build_report
    from nba.daily.store import ensure_tables

    ensure_tables(con)
    rows = []
    for i in range(6):
        for model, crps in (("props_context_residual", 1.0), ("props_recency_v1", 1.4)):
            rows.append(
                (datetime(2026, 11, 1), f"G{i}", datetime(2026, 11, 1 + i % 3).date(), 2026,
                 model, "v", "pts", 100 + i, datetime(2026, 11, 1), 10.0, 11.0, None, None,
                 crps, "scored")
            )  # fmt: skip
    con.executemany("INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    text = build_report(con, 2026)
    assert "Paired: props_context_residual minus props_recency_v1" in text
    assert "- pts: delta CRPS -0.4000" in text and "### props_context_residual" in text
