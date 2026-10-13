"""Model stamps: deterministic weights fingerprints, the new table, and failure isolation."""

from __future__ import annotations

import pickle
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.daily import stamps
from nba.daily.pipeline import RunSummary, run_daily
from nba.daily.predict import (
    CONTEXT_PROPS_MODEL_NAME,
    ELO_MODEL_NAME,
    fit_mov_elo,
    load_elo_params,
)
from nba.daily.store import ForwardPrediction
from nba.eval.lower_tail_eval import attach_realised
from nba.props.context_residual import (
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    stat_feature_names,
    stat_frame,
)
from tests.daily.conftest import RUN_DATE, before_tip, slate_games
from tests.props.test_context_residual import ELO, _league


def _run(con: duckdb.DuckDBPyConnection) -> RunSummary:
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=before_tip(), skip_ingest=True,
        skip_injury=True, props_model="rolling",
    )  # fmt: skip


def test_digest_canonical_and_sensitive() -> None:
    a = {"x": np.arange(5, dtype=float), "y": [1, 2, {"k": 1.5, "j": None}], "z": "s"}
    b = {"z": "s", "y": [1, 2, {"j": None, "k": 1.5}], "x": np.arange(5, dtype=float)}
    assert stamps.canonical_digest(a) == stamps.canonical_digest(b)  # key order is irrelevant
    c = {**a, "x": np.arange(5, dtype=float) + 1e-12}
    assert stamps.canonical_digest(a)[0] != stamps.canonical_digest(c)[0]  # a tiny change shows
    assert stamps.canonical_digest(a)[1] == 0  # fully canonical, nothing hashed by repr


def test_mov_elo_fit_is_deterministic(con: duckdb.DuckDBPyConnection) -> None:
    params = load_elo_params()
    m1, n1 = fit_mov_elo(con, RUN_DATE, 2026, params)
    m2, n2 = fit_mov_elo(con, RUN_DATE, 2026, params)
    s1 = stamps.elo_stamp(ELO_MODEL_NAME, "v", m1.ratings_, params, None, n1)
    s2 = stamps.elo_stamp(ELO_MODEL_NAME, "v", m2.ratings_, params, None, n2)
    assert s1.weights_sha256 == s2.weights_sha256
    m3, n3 = fit_mov_elo(con, date(2026, 1, 10), 2025, params)  # fewer games -> other ratings
    assert n3 < n1
    assert stamps.elo_stamp(ELO_MODEL_NAME, "v", m3.ratings_, params, None, n3).weights_sha256 != (
        s1.weights_sha256
    )


@pytest.fixture(scope="module")
def train_frame() -> pl.DataFrame:
    gdf, pdf, static = _league(n_days=330)
    feats = attach_realised(build_features(gdf, pdf, static, {}, ELO), gdf, pdf)
    return stat_frame(feats, "pts").sort(["game_date", "game_id", "player_id"])


def _fit_ctx(train: pl.DataFrame, seed_shift: int = 0) -> dict[str, ContextResidualModel]:
    cfg = ContextResidualConfig(
        n_estimators=30 + seed_shift, min_child_samples=30, min_cal_rows=300
    )
    return {"pts": ContextResidualModel("pts", cfg, stat_feature_names("pts")).fit(train)}


def test_context_models_two_fits_same_digest(train_frame: pl.DataFrame) -> None:
    d1, approx1 = stamps.models_digest(_fit_ctx(train_frame))
    d2, approx2 = stamps.models_digest(_fit_ctx(train_frame))
    assert d1 == d2 and approx1 == approx2 == 0
    assert stamps.models_digest(_fit_ctx(train_frame, 1))[0] != d1  # different weights differ


def test_run_writes_stamps_and_leaves_predictions_alone(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con)
    assert s.stamp_status == "ok: 2 rows" and not s.shadow_errors
    rows = con.execute(
        "SELECT model_name, weights_sha256, weights_scope, git_sha, made_at, n_train "
        "FROM forward_model_stamps WHERE run_id = ? ORDER BY 1",
        [s.run_id],
    ).fetchall()
    assert [r[0] for r in rows] == ["props_rolling_avg_baseline", ELO_MODEL_NAME]
    assert all(len(r[1]) == 64 and r[3] and r[4] for r in rows)
    elo = next(r for r in rows if r[0] == ELO_MODEL_NAME)
    assert elo[5] and elo[5] > 0
    # every stamped model name is a name that was actually predicted in this run
    names = {
        r[0] for r in con.execute(
            "SELECT DISTINCT model_name FROM forward_predictions WHERE run_id = ?", [s.run_id]
        ).fetchall()
    }  # fmt: skip
    assert names == {r[0] for r in rows}


def test_stamp_failure_never_blocks_the_run(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _run(con)
    n_ok = baseline.n_rows_written

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("stamp table unavailable")

    monkeypatch.setattr(stamps, "collect_stamps", boom)
    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=before_tip(60), skip_ingest=True,
        skip_injury=True, props_model="rolling", dedup=False,
    )  # fmt: skip
    assert s.n_rows_written == n_ok and s.n_rows_written > 0  # predictions still written
    assert s.stamp_status.startswith("failed: RuntimeError")
    assert any("model stamps" in e for e in s.shadow_errors)  # surfaced -> ALERTS line
    assert con.execute(
        "SELECT count(*) FROM forward_model_stamps WHERE run_id = ?", [s.run_id]
    ).fetchone() == (0,)


def test_write_stamps_returns_failure_instead_of_raising() -> None:
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE TABLE {stamps.TABLE} (run_id VARCHAR)")  # wrong shape -> insert fails
    st = stamps.rule_stamp("m", "v")
    now = datetime(2026, 1, 1)
    out = stamps.write_stamps(
        con, "r", now, [st], git_sha="x", git_dirty=False, data_version=None, stamped_at=now,
    )  # fmt: skip
    assert out.startswith("failed:")


def test_collect_stamps_context_from_cache(
    con: duckdb.DuckDBPyConnection, train_frame: pl.DataFrame, tmp_path: Path
) -> None:
    models = _fit_ctx(train_frame)
    cache = tmp_path / RUN_DATE.isoformat()
    cache.mkdir()
    with (cache / "models.pkl").open("wb") as f:
        pickle.dump(models, f)
    summary = RunSummary(run_id="r1", run_date=RUN_DATE)
    summary.stamp_inputs["props_info"] = {"train_through": "2026-10-27", "n_train_rows": 1234}
    preds = [
        ForwardPrediction("g", before_tip(), name, "v", "pts", {}, player_id=1)
        for name in (CONTEXT_PROPS_MODEL_NAME, "props_context_residual_int", "props_recency_v1")
    ]
    out = {s.model_name: s for s in stamps.collect_stamps(con, RUN_DATE, preds, summary, tmp_path)}
    digest = stamps.models_digest(models)[0]
    assert out[CONTEXT_PROPS_MODEL_NAME].weights_sha256 == digest
    assert out["props_context_residual_int"].weights_sha256 == digest  # same fitted models
    assert out[CONTEXT_PROPS_MODEL_NAME].fit_through == date(2026, 10, 27)
    assert out[CONTEXT_PROPS_MODEL_NAME].n_train == 1234
    assert "no fitted weights" in out["props_recency_v1"].weights_scope
