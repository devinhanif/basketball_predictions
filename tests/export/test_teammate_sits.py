"""The nightly 'if a teammate sits' rows: what is produced, what is withheld, failure isolation.

Synthetic league, in-memory DuckDB, heads fitted here and cached under tmp (the export never fits).
"""

from __future__ import annotations

import json
import pickle
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl
import pytest

import nba.daily.predict as predict_mod
import nba.props.context_residual as ctx_mod
import nba.props.forward as forward_mod
from nba.daily.predict import load_elo_params
from nba.export import teammate_sits as T
from nba.export.build import TABLES
from nba.props.context_residual import (
    ContextResidualConfig,
    build_features,
    fit_for_date,
)
from tests.research.teammate_sits.test_scenario import _league

MADE_AT = datetime(2026, 1, 1, 12, 0)
AVAIL_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "player_id": pl.Int64,
    "status": pl.Utf8,
    "as_of": pl.Datetime("us"),
    "source": pl.Utf8,
}


class World:
    def __init__(self, tmp: Path) -> None:
        gdf, pdf, static, flagged = _league(n_days=120)
        self.day: date = cast(date, gdf["game_date"].max())
        self.slate_ids = gdf.filter(pl.col("game_date") == self.day)["game_id"].to_list()
        self.h_games = gdf.filter(pl.col("game_date") < self.day)
        self.h_pgs = pdf.join(self.h_games.select("game_id"), on="game_id")
        self.static = static
        self.hist_flagged = {g: set(v) for g, v in flagged.items() if g not in self.slate_ids}
        slate = gdf.filter(pl.col("game_date") == self.day)
        self.games = [
            {
                "game_id": g, "game_date": self.day, "final": False,
                "tipoff": datetime(2026, 1, 1, 23, 0), "home_team": int(h), "away_team": int(a),
            }
            for g, h, a in slate.select("game_id", "home_team", "away_team").iter_rows()
        ]  # fmt: skip
        played = pdf.filter(pl.col("game_id").is_in(self.slate_ids) & (pl.col("minutes") > 0))
        self.props = [
            {
                "game_id": g, "player_id": int(p), "team_id": int(t), "stat": "pts",
                "model_name": "props_context_residual", "is_latest": True,
                "mean": None, "p_play": 0.9,
            }
            for g, p, t in played.select("game_id", "player_id", "team_id").iter_rows()
        ]  # fmt: skip
        self.report_out: dict[str, set[int]] = {g: set() for g in self.slate_ids}
        self.root = tmp / "models"
        self._fit()

    def _frames(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        frame = pl.DataFrame(
            {
                "game_id": [g["game_id"] for g in self.games],
                "home_team": [g["home_team"] for g in self.games],
                "away_team": [g["away_team"] for g in self.games],
            },
            schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
        )
        roster = pl.DataFrame(
            [(p["game_id"], p["player_id"], p["team_id"]) for p in self.props],
            schema={"game_id": pl.Utf8, "player_id": pl.Int64, "team_id": pl.Int64},
            orient="row",
        )
        return forward_mod._with_slate_placeholders(
            self.h_games, self.h_pgs, self.day, frame, roster
        )

    def _fit(self) -> None:
        a_games, a_pgs = self._frames()
        feats = build_features(
            a_games, a_pgs, self.static, {**self.hist_flagged, **self.report_out}, load_elo_params()
        )
        cfg = ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300)
        models = fit_for_date(feats, self.day, cfg)
        hist = feats.filter(pl.col("game_date") < self.day)
        self.n_hist = hist.height
        d = self.root / self.day.isoformat()
        d.mkdir(parents=True)
        (d / "models.pkl").write_bytes(pickle.dumps(models))
        self.write_meta(self.n_hist)

    def write_meta(self, n_rows: int) -> None:
        d = self.root / self.day.isoformat()
        fp = {"n_rows": n_rows, "max_date": str(self.h_games["game_date"].max())}
        (d / "meta.json").write_text(json.dumps({"fingerprint": fp, "info": {}}))


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("sits"))


@pytest.fixture(autouse=True)
def _patched(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    monkeypatch.setattr(
        forward_mod,
        "_history_frames",
        lambda con, as_of: (
            world.h_games,
            world.h_pgs,
            world.static,
            pl.DataFrame(schema=AVAIL_SCHEMA),
        ),
    )
    monkeypatch.setattr(
        ctx_mod,
        "flagged_from_availability",
        lambda avail, games, cfg, **k: (dict(world.hist_flagged), {}),
    )
    monkeypatch.setattr(
        predict_mod, "slate_report_outs", lambda con, games, made_at, **k: dict(world.report_out)
    )


def _run(w: World, **kw: Any) -> T.SitsResult:
    args: dict[str, Any] = {
        "con": duckdb.connect(":memory:"), "day": w.day, "games": w.games, "prop_rows": w.props,
        "p_play_rows": [], "report_rows": [], "made_at": MADE_AT, "model_root": w.root,
    }  # fmt: skip
    args.update(kw)
    return T.compute(**args)


def test_rows_follow_the_contract(world: World) -> None:
    r = _run(world)
    assert r.info["status"] == "ok", r.info
    assert r.rows and r.info["n_rows"] == len(r.rows)
    assert [c for c, _ in T.COLUMNS] == list(r.rows[0])
    assert {x["stat"] for x in r.rows} == {"pts", "reb"}  # ast / fg3m have no word to show
    assert {x["rule_word"] for x in r.rows} == {"HELPS"}
    assert all(x["made_at"] == MADE_AT and x["schema_version"] == 1 for x in r.rows)
    assert all(x["sitter_id"] != x["player_id"] for x in r.rows)
    team_of = {(p["game_id"], p["player_id"]): p["team_id"] for p in world.props}
    for x in r.rows:  # teammate on the sitter's team only, never an opponent
        assert team_of[(x["game_id"], x["player_id"])] == x["team_id"]
        assert team_of[(x["game_id"], x["sitter_id"])] == x["team_id"]
        assert x["delta_mean"] == pytest.approx(x["scn_mean"] - x["base_mean"], abs=0.011)
        assert x["p_play"] == 0.9 and x["sitter_status"] == "unlisted"
    # a regular starter missing from the board is the point: one sitter at a time
    sitters = {(x["game_id"], x["sitter_id"]) for x in r.rows}
    assert r.info["n_scenarios"] == len(sitters) and r.info["n_games"] == len(world.slate_ids)
    assert r.info["rule_sha8"] == "c23f7b36" and r.info["stats"] == ["pts", "reb"]
    # missing a starter lifts teammates' points on average (the direction the frozen test found)
    pts = [x["delta_mean"] for x in r.rows if x["stat"] == "pts"]
    assert sum(pts) / len(pts) > 0


def test_base_equals_a_second_run_and_is_compared_to_production(world: World) -> None:
    a = _run(world)
    b = _run(world)
    assert a.rows == b.rows  # deterministic
    base = {
        (x["game_id"], x["player_id"], "pts"): x["base_mean"] for x in a.rows if x["stat"] == "pts"
    }
    props = [dict(p, mean=base.get((p["game_id"], p["player_id"], "pts"))) for p in world.props]
    c = _run(world, prop_rows=props)
    cmp = c.info["base_vs_production"]
    assert cmp["n_compared"] > 0 and cmp["max_abs_diff"] == 0 and cmp["n_over_0_05"] == 0


def test_sitter_status_from_the_report_and_out_players_are_not_sitters(world: World) -> None:
    first = _run(world)
    g, s = first.rows[0]["game_id"], first.rows[0]["sitter_id"]
    rep = [
        {
            "game_id": g,
            "player_id": s,
            "status": "Questionable",
            "report_as_of_et": datetime(2026, 1, 1, 9),
        },
        {
            "game_id": g,
            "player_id": s,
            "status": "Probable",
            "report_as_of_et": datetime(2026, 1, 1, 8),
        },
    ]
    r = _run(world, report_rows=rep)
    assert {x["sitter_status"] for x in r.rows if x["sitter_id"] == s and x["game_id"] == g} == {
        "questionable"
    }  # the latest line wins
    world.report_out[g] = {s}  # now he is really OUT: no scenario for him
    try:
        out = _run(world)
    finally:
        world.report_out[g] = set()
    assert s not in {x["sitter_id"] for x in out.rows if x["game_id"] == g}
    assert out.info["null_reasons"]["sitter_already_out"] == 1


def test_game_without_a_report_gets_no_scenario(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    g0 = world.slate_ids[0]
    some = {g: s for g, s in world.report_out.items() if g != g0}
    monkeypatch.setattr(predict_mod, "slate_report_outs", lambda con, games, made_at, **k: some)
    r = _run(world)
    assert r.info["status"] == "ok" and g0 not in {x["game_id"] for x in r.rows}
    assert r.info["null_reasons"]["no_report"] == 1


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"model_root": None}, "disabled"),
        ({"games": []}, "no_unfinished_games"),
        ({"max_rows": 3}, "over_row_budget"),
        ({"prop_rows": []}, "no_roster_rows"),
    ],
)
def test_withheld_with_a_reason_never_truncated(
    world: World, kw: dict[str, Any], reason: str
) -> None:
    r = _run(world, **kw)
    assert r.rows == [] and r.info["status"] == "absent" and r.info["reason"] == reason


def test_finished_games_get_no_scenarios(world: World) -> None:
    done = [dict(g, final=True) for g in world.games]
    assert _run(world, games=done).info["reason"] == "no_unfinished_games"


def test_no_cache_and_stale_cache_never_fit(world: World, tmp_path: Path) -> None:
    assert _run(world, model_root=tmp_path / "nothing").info["reason"] == "no_model_cache"
    world.write_meta(world.n_hist + 1)
    try:
        r = _run(world)
    finally:
        world.write_meta(world.n_hist)
    assert r.info["reason"] == "model_cache_stale" and r.rows == []


def test_time_budget_is_enforced_between_stages(world: World) -> None:
    ticks = iter(range(0, 10_000, 1000))
    r = _run(world, clock=lambda: float(next(ticks)), budget_s=500.0)
    assert r.info["reason"] == "over_time_budget" and r.rows == []


def test_failure_is_isolated_and_reported(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("history exploded")

    monkeypatch.setattr(forward_mod, "_history_frames", boom)
    r = _run(world)  # no exception escapes
    assert r.rows == [] and r.info["status"] == "absent"
    assert r.info["reason"] == "error:RuntimeError" and "exploded" in r.info["detail"]


def test_table_is_in_the_export_schema_and_sizes_are_bounded(world: World) -> None:
    assert [c for c, _ in TABLES["teammate_sits"]] == [c for c, _ in T.COLUMNS]
    r = _run(world)
    assert len(r.rows) <= T.MAX_ROWS
    per_game = len(r.rows) / len(world.slate_ids)
    assert per_game < 400  # two stats, one sitter at a time (contract: ~336/game for four)
