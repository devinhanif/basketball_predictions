# ruff: noqa: E501
"""TEAMMATE_SITS gate: event definitions and the sizing core on a synthetic league (no DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import ContextResidualConfig, build_features, player_states
from research.teammate_sits import gate as G
from research.teammate_sits import scenario as S
from tests.research.teammate_sits.test_scenario import ELO, _league


def _world() -> dict[str, object]:
    gdf, pdf, static, flagged = _league(seed=1, n_days=330)
    gdf = gdf.with_columns(pl.col("game_date").cast(pl.Date))
    played = S.played_frame(gdf, pdf)
    return {
        "games": gdf,
        "pgs": pdf,
        "static": static,
        "flagged": flagged,
        "played": played,
        "states": player_states(played),
        "info": S.game_info_of(gdf),
        "cfg": ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300),
    }


def test_absence_index_counts_team_games_since_last_played() -> None:
    games = pl.DataFrame(
        {
            "game_date": [dt.date(2024, 1, d) for d in (1, 3, 5, 7)],
            "home_team": [1, 2, 1, 1],
            "away_team": [3, 1, 4, 5],
        }
    )
    sit = pl.DataFrame(
        {
            "team_id": [1, 1, 1],
            "game_date": [dt.date(2024, 1, 3), dt.date(2024, 1, 5), dt.date(2024, 1, 7)],
            "last_date": [dt.date(2024, 1, 1), dt.date(2024, 1, 1), dt.date(2024, 1, 3)],
        }
    )
    assert G.absence_index(sit, games)["k"].to_list() == [1, 2, 2]


def test_events_primary_has_one_fresh_regular_starter_per_team_game() -> None:
    w = _world()
    reg, prim = G.events(w)
    assert prim.height > 0
    assert (prim["starter10"] >= S.REGULAR_STARTER10).all()
    assert (prim["k"] <= G.MAX_K).all() and (prim["k"] >= 1).all()
    assert prim.group_by("game_id", "team_id").len()["len"].max() == 1
    tm = G.teammate_rows(w, prim)
    assert (tm["player_id"] != tm["sitter_id"]).all()
    assert G.production_tie(w)["pass"]


def test_size_core_runs_and_reports_bars() -> None:
    w = _world()
    feats = build_features(w["games"], w["pgs"], w["static"], w["flagged"], ELO)  # type: ignore[arg-type]
    out = G.size_core(w, feats, (2023,), 0.0)
    blk = out["by_season"]["2023"]
    assert set(blk) == {"pts", "reb", "ast", "fg3m"}
    for r in blk.values():
        assert r["n"] > 0
        assert set(r["clusters"]) == {"game", "player_season", "sitter_season"}
        assert np.isfinite(r["sizing"]["halfwidth95"]) and isinstance(r["g2_pass"], bool)
        assert (
            r["oracle_expected_gain"] <= 1e-12
        )  # S cannot lose in expectation when S is the truth
