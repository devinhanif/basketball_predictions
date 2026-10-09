"""Integer-support quantile flag (docs/INTEGER_QUANTILES.md)."""

from __future__ import annotations

import json
from datetime import date

import duckdb
import numpy as np
import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.forward import predict_slate_context
from tests.fixtures.loader import build_fixture_db
from tests.props.test_context_residual import ELO, _league
from tests.props.test_forward_context import AS_OF, SLATE, _big_history

_grid = st.lists(
    st.floats(min_value=0.0, max_value=80.0, allow_nan=False), min_size=2, max_size=40
).map(sorted)


@given(_grid)
@settings(max_examples=100, deadline=None)
def test_transform_monotone_integer_nonneg(q: list[float]) -> None:
    out = to_integer_support(np.array(q))
    assert (out == np.round(out)).all() and (out >= 0).all()
    assert (np.diff(out) >= 0).all()


@given(_grid, st.integers(min_value=1, max_value=60))
@settings(max_examples=100, deadline=None)
def test_p_ge_invariant_and_non_increasing(q: list[float], n: int) -> None:
    arr = np.array(q)
    out = to_integer_support(arr)
    # P(stat >= N) = mean(q > N - 0.5) is exactly invariant on integer N
    assert np.mean(arr > n - 0.5) == np.mean(out >= n) == np.mean(out > n - 0.5)
    p = [float(np.mean(out > k - 0.5)) for k in range(1, 40)]
    assert all(a >= b for a, b in zip(p, p[1:], strict=False))


@pytest.fixture(scope="module")
def fitted() -> tuple[pl.DataFrame, pl.DataFrame]:
    gdf, pdf, static = _league(n_days=330)
    feats = build_features(gdf, pdf, static, {}, ELO)
    fr = stat_frame(feats, "reb").sort(["game_date", "game_id", "player_id"])
    cut = date(2023, 6, 1)
    return fr.filter(pl.col("game_date") < cut), fr.filter(pl.col("game_date") >= cut)


def _fit(train: pl.DataFrame, **kw: object) -> ContextResidualModel:
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300, **kw)  # type: ignore[arg-type]
    return ContextResidualModel("reb", cfg, stat_feature_names("reb")).fit(train)


def test_flag_off_byte_identical_and_on_is_integer(
    fitted: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    train, test = fitted
    m0, m1 = _fit(train), _fit(train, integer_support=True)
    mean0, q0 = m0.predict(test, CRPS_TAUS)
    mean1, q1 = m1.predict(test, CRPS_TAUS)
    assert ContextResidualConfig().integer_support is False
    assert _fit(train).predict(test, CRPS_TAUS)[1].tobytes() == q0.tobytes()
    assert mean0.tobytes() == mean1.tobytes()  # mean untouched
    assert np.array_equal(q1, to_integer_support(q0))
    assert (q1 == np.round(q1)).all() and (np.diff(q1, axis=1) >= 0).all()
    # stats outside integer_support_stats are untouched even with the flag on
    m_pts = _fit(train, integer_support=True, integer_support_stats=("ast",))
    assert m_pts.predict(test, CRPS_TAUS)[1].tobytes() == q0.tobytes()


@pytest.fixture(scope="module")
def big() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    _big_history(c)
    return c


def test_slate_variant_rows_primary_unchanged(big: duckdb.DuckDBPyConnection) -> None:
    base = predict_slate_context(big, AS_OF, SLATE, {}, ELO, cache_root=None)
    var = predict_slate_context(big, AS_OF, SLATE, {}, ELO, cache_root=None, int_variant=True)
    assert base.integer_variant is None
    assert var.primary.equals(base.primary)  # primary byte-identical with the variant on
    v = var.integer_variant
    assert v is not None and v.height > 0
    assert set(v["stat"].unique().to_list()) <= {"reb", "ast", "fg3m"}
    p = base.primary.join(v, on=["game_id", "player_id", "stat"], suffix="_i")
    assert p.height == v.height
    for r in p.iter_rows(named=True):
        grid = np.array(json.loads(r["q_grid_i"]))
        assert (grid == np.round(grid)).all() and (np.diff(grid) >= 0).all()
        assert json.loads(r["p_ge"]) == json.loads(r["p_ge_i"])  # exactly invariant
        assert r["mean"] == r["mean_i"]


def test_oof_eval_refuses_2025_and_reports_zero_p_ge_shift() -> None:
    from nba.eval.integer_quantiles_eval import evaluate

    rng = np.random.default_rng(0)
    n = 400
    base = np.sort(rng.gamma(2.0, 2.0, size=(n, 19)), axis=1)
    gid = [f"g{i // 10}" for i in range(n)]
    oof = pl.DataFrame(
        {
            "target": ["reb"] * n,
            "season": [2023] * n,
            "game_id": gid,
            "player_id": list(range(n)),
            "mean": base.mean(axis=1),
            "q_grid": base.tolist(),
        }
    )
    labels = pl.DataFrame(
        {
            "game_id": gid,
            "player_id": list(range(n)),
            "pts": 0,
            "reb": rng.poisson(4.0, n),
            "ast": 0,
            "fg3m": 0,
        }
    )
    # only the reb target is present; other stats have no rows and are skipped by the guard
    with pytest.raises(ValueError, match="2025"):
        evaluate(oof, labels, 2025)
