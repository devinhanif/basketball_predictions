"""nba.export.sits_core equals the research reference, function by function.

Moved because nothing under ``nba/`` may import ``research``; this proves the move changed nothing.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nba.export import sits_core as C
from nba.props.context_residual import (
    ContextResidualConfig,
    build_features,
    fit_for_date,
    player_states,
)
from research.teammate_sits import scenario as S
from tests.research.teammate_sits.test_scenario import ELO, _league


@pytest.fixture(scope="module")
def world() -> dict[str, object]:
    gdf, pdf, static, flagged = _league(n_days=120)
    played = C.played_frame(gdf, pdf)
    return {
        "games": gdf,
        "pgs": pdf,
        "static": static,
        "flagged": flagged,
        "played": played,
        "states": player_states(played),
        "info": C.game_info_of(gdf),
    }


def _roster(w: dict[str, object]) -> pl.DataFrame:
    pdf = w["pgs"]
    assert isinstance(pdf, pl.DataFrame)
    last = w["games"]
    assert isinstance(last, pl.DataFrame)
    day = last["game_date"].max()
    ids = last.filter(pl.col("game_date") == day)["game_id"].to_list()
    return pdf.filter(pl.col("game_id").is_in(ids)).select("game_id", "player_id").unique()


def test_constants_and_helpers_equal_reference(world: dict[str, object]) -> None:
    assert C.SCN_COLS == S.SCN_COLS and C.REGULAR_STARTER10 == S.REGULAR_STARTER10
    assert C.SEP == S.SEP
    assert C.game_info_of(world["games"]) == S.game_info_of(world["games"])  # type: ignore[arg-type]
    assert_frame_equal(
        C.played_frame(world["games"], world["pgs"]),  # type: ignore[arg-type]
        S.played_frame(world["games"], world["pgs"]),  # type: ignore[arg-type]
    )


def test_sitter_rows_and_candidates_equal_reference(world: dict[str, object]) -> None:
    w = world
    a = C.sitter_rows(w["played"], w["states"], w["flagged"], w["info"])  # type: ignore[arg-type]
    b = S.sitter_rows(w["played"], w["states"], w["flagged"], w["info"])  # type: ignore[arg-type]
    assert a.height > 50
    assert_frame_equal(a.sort(a.columns), b.sort(b.columns))
    roster = _roster(w)
    flagged = {g: s for g, s in w["flagged"].items()}  # type: ignore[attr-defined]
    ca = C.candidates(w["played"], w["states"], roster, flagged, w["info"])  # type: ignore[arg-type]
    cb = S.candidates(w["played"], w["states"], roster, flagged, w["info"])  # type: ignore[arg-type]
    assert ca.height > 0
    assert_frame_equal(ca, cb)


def test_scenario_vacancy_features_and_forecasts_equal_reference(
    world: dict[str, object],
) -> None:
    w = world
    gdf, pdf = w["games"], w["pgs"]
    assert isinstance(gdf, pl.DataFrame) and isinstance(pdf, pl.DataFrame)
    flagged: dict[str, set[int]] = w["flagged"]  # type: ignore[assignment]
    base = build_features(gdf, pdf, w["static"], flagged, ELO)  # type: ignore[arg-type]
    day = gdf["game_date"].max()
    roster = _roster(w)
    cand = C.candidates(w["played"], w["states"], roster, flagged, w["info"])  # type: ignore[arg-type]
    sitters = cand.select("game_id", pl.col("player_id").alias("sitter_id"), "team_id")
    pairs = sitters.select("game_id", "sitter_id").rows()
    va = C.scenario_vacancy(w["played"], w["states"], flagged, w["info"], pairs)  # type: ignore[arg-type]
    vb = S.scenario_vacancy(w["played"], w["states"], flagged, w["info"], pairs)  # type: ignore[arg-type]
    key = ["game_id", "sitter_id", "team_id"]
    assert_frame_equal(va.sort(key), vb.sort(key))
    slate = base.filter(pl.col("game_date") == day)
    fa = C.scenario_features(slate, va, sitters)
    fb = S.scenario_features(slate, vb, sitters)
    assert fa.height > 0
    assert_frame_equal(fa, fb)
    cfg = ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300)
    models = fit_for_date(base, cast(date, day), cfg)
    ra = C.scenario_forecasts(models, slate, fa, ("pts", "reb"))
    rb = S.scenario_forecasts(models, slate, fb)
    assert ra.height > 0
    both = rb.filter(pl.col("stat").is_in(["pts", "reb"]))
    assert_frame_equal(ra, both)
    # the reference runs four stats; ours runs only the ones asked for
    assert set(ra["stat"].unique().to_list()) == {"pts", "reb"}
    assert (ra["player_id"] != ra["sitter_id"]).all()
    assert np.isfinite(ra["delta_mean"].to_numpy()).all()
