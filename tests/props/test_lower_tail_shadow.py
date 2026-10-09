"""pts lower-tail SHADOW rows (docs/LOWER_TAIL.md): primary untouched, as-of discipline."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.eval.lower_tail_eval import attach_realised
from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    build_features,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.forward import ContextSlateResult, predict_slate_context
from nba.props.lower_tail import predict_pi
from tests.fixtures.loader import build_fixture_db
from tests.props.test_context_residual import ELO, _league
from tests.props.test_forward_context import (
    SLATE,
    START,
    T1,
    T2,
    T3,
    T4,
    _big_history,
    _insert_game,
)

N_DAYS = 260
AS_OF = START + timedelta(days=N_DAYS)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    con = build_fixture_db(":memory:")
    _big_history(con, N_DAYS, seed=5)
    # make short-minutes games common enough for the pi model and the short component to exist
    con.execute(
        "UPDATE player_game_stats SET minutes = minutes * 0.25, pts = CAST(pts * 0.25 AS INT) "
        "WHERE minutes IS NOT NULL AND hash(game_id || CAST(player_id AS VARCHAR)) % 10 = 0"
    )
    cache = tmp_path_factory.mktemp("cache")
    report = {"0022600901": set(), "0022600902": set()}
    off = predict_slate_context(con, AS_OF, SLATE, report, ELO, cache_root=cache)
    meta_off = (cache / AS_OF.isoformat() / "meta.json").read_bytes()
    on = predict_slate_context(
        con, AS_OF, SLATE, report, ELO, cache_root=cache, lower_tail_variant=True
    )
    return {"con": con, "cache": cache, "off": off, "on": on, "meta_off": meta_off, "rep": report}


def test_primary_and_recency_identical_flag_on_off(world: dict[str, Any]) -> None:
    off: ContextSlateResult = world["off"]
    on: ContextSlateResult = world["on"]
    assert off.lower_tail_variant is None
    assert on.primary.equals(off.primary) and on.recency.equals(off.recency)
    assert "lower_tail_error" not in on.info, on.info.get("lower_tail_error")


def test_cache_fingerprint_unchanged_when_off(world: dict[str, Any]) -> None:
    cache: Path = world["cache"]
    d = cache / AS_OF.isoformat()
    assert (d / "meta.json").read_bytes() == world["meta_off"]  # on-run reused it untouched
    fp = json.loads((d / "meta.json").read_text())["fingerprint"]
    assert "lower_tail" not in fp["cfg"] and "stats" not in fp
    lt = json.loads((cache / f"{AS_OF.isoformat()}_lt" / "meta.json").read_text())["fingerprint"]
    assert lt["cfg"]["lower_tail"] == "mixture" and lt["stats"] == ["pts"]


def test_lt_rows_pts_only_integer_mean_untouched_and_nonvacuous(world: dict[str, Any]) -> None:
    on: ContextSlateResult = world["on"]
    v = on.lower_tail_variant
    assert v is not None and v.height > 0
    assert set(v["stat"].unique().to_list()) == {"pts"}
    j = on.primary.join(v, on=["game_id", "player_id", "stat"], suffix="_lt")
    assert j.height == v.height
    differs = 0
    for r in j.iter_rows(named=True):
        grid = np.array(json.loads(r["q_grid_lt"]))
        assert len(grid) == 19 and (grid == np.round(grid)).all() and (np.diff(grid) >= 0).all()
        assert r["mean"] == r["mean_lt"]  # stored mean is the primary's
        base = to_integer_support(np.array(json.loads(r["q_grid"])))
        differs += int(not np.array_equal(base, grid))
    assert differs > 0, "mixture identical to production everywhere: shadow is vacuous"


def test_planted_same_game_and_future_do_not_move_lt(world: dict[str, Any]) -> None:
    con: duckdb.DuckDBPyConnection = world["con"]
    on: ContextSlateResult = world["on"]
    rng = np.random.default_rng(11)
    base = {T1: 1000, T2: 1100, T3: 3000, T4: 3100}
    # tonight's slate game already carrying a (hallucinated) box score: huge, tiny and DNP lines
    _insert_game(con, rng, "0022600901", AS_OF, T1, T2, base)
    con.execute(
        "UPDATE player_game_stats SET minutes = 1.0, pts = 90 WHERE game_id = '0022600901' "
        "AND player_id IN (1000, 1001, 1100)"
    )
    con.execute(
        "UPDATE player_game_stats SET minutes = NULL, pts = 0, reb = 0, ast = 0, fg3m = 0 "
        "WHERE game_id = '0022600901' AND player_id IN (1002, 1101)"
    )
    # and games after the slate date
    _insert_game(con, rng, "0022600955", AS_OF + timedelta(days=3), T1, T2, base)
    con.execute("UPDATE player_game_stats SET minutes = 1.0, pts = 99 WHERE game_id = '0022600955'")
    again = predict_slate_context(
        con, AS_OF, SLATE, world["rep"], ELO, cache_root=None, lower_tail_variant=True
    )
    assert again.primary.equals(on.primary)
    assert again.lower_tail_variant is not None and on.lower_tail_variant is not None
    assert again.lower_tail_variant.equals(on.lower_tail_variant)


@pytest.fixture(scope="module")
def fitted() -> tuple[pl.DataFrame, pl.DataFrame]:
    gdf, pdf, static = _league(n_days=330)
    feats = attach_realised(build_features(gdf, pdf, static, {}, ELO), gdf, pdf)
    fr = stat_frame(feats, "pts").sort(["game_date", "game_id", "player_id"])
    cut = date(2023, 6, 1)
    return fr.filter(pl.col("game_date") < cut), fr.filter(pl.col("game_date") >= cut)


def test_pi_and_quantiles_ignore_tonights_realised_columns(
    fitted: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    train, test = fitted
    shorten = np.random.default_rng(1).random(train.height) < 0.1  # ensure a short-minutes class
    train = train.with_columns(
        pl.when(pl.Series(shorten)).then(pl.col("minutes") * 0.2).otherwise(pl.col("minutes"))
        .alias("minutes")
    )  # fmt: skip
    cfg = ContextResidualConfig(
        n_estimators=30, min_child_samples=30, min_cal_rows=300, lower_tail="mixture"
    )
    m = ContextResidualModel("pts", cfg, stat_feature_names("pts")).fit(train)
    assert m.lt_ is not None and m.lt_["pi_model"] is not None
    pi0 = predict_pi(m.lt_, _matrix(test, list(COMMON_FEATURES)))
    _, q0 = m.predict(test, CRPS_TAUS)
    rng = np.random.default_rng(0)
    n = test.height
    planted = test.with_columns(
        pl.Series("minutes", rng.uniform(0.0, 48.0, n)),
        pl.Series("pts", rng.integers(0, 70, n)),
        pl.Series("y", rng.uniform(0.0, 70.0, n)),
        pl.Series("resid", rng.normal(0.0, 30.0, n)),
    )
    assert planted["minutes"].to_list() != test["minutes"].to_list()
    assert predict_pi(m.lt_, _matrix(planted, list(COMMON_FEATURES))).tobytes() == pi0.tobytes()
    assert m.predict(planted.drop("minutes"), CRPS_TAUS)[1].tobytes() == q0.tobytes()
    assert m.predict(planted, CRPS_TAUS)[1].tobytes() == q0.tobytes()
    assert not set(COMMON_FEATURES) & {"minutes", "pts", "y", "resid"}


def test_mixture_coarse_grid_matches_fine_grid() -> None:
    """The 19-quantile read-out (stored in forward rows) and the 199-grid come from one mixture."""
    from nba.props.context_residual import QUANTILE_TAUS
    from nba.props.lower_tail import mixture_quantiles

    rng = np.random.default_rng(0)
    n = 30
    c, s = rng.uniform(5, 25, n), rng.uniform(2, 6, n)
    m_row, pi = rng.uniform(4, 25, n), rng.uniform(0.01, 0.4, n)
    z = rng.normal(size=800)
    w_q = np.quantile(rng.beta(2, 8, 300), CRPS_TAUS)
    taus19 = np.array(QUANTILE_TAUS)
    q199 = mixture_quantiles(c, s, m_row, pi, z, w_q, CRPS_TAUS)
    q19 = mixture_quantiles(c, s, m_row, pi, z, w_q, taus19)
    assert q19.shape == (n, 19) and (np.diff(q19, axis=1) >= 0).all()
    ref = np.array([np.interp(taus19, CRPS_TAUS, row) for row in q199])
    assert np.abs(q19 - ref).max() < 0.5
