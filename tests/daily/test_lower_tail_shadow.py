"""--log-lower-tail-variant: pts SHADOW rows next to production; settle + frozen live rule."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import duckdb
import numpy as np

from nba.daily import lt_live
from nba.daily.__main__ import _parser
from nba.daily.pipeline import run_daily
from nba.daily.schedule import ScheduledGame
from nba.daily.settle import quantile_crps, settle_pending
from nba.props.forward import QUANTILE_TAUS
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db
from tests.props.test_forward_context import AS_OF, _big_history, _insert_game

LT = "props_context_residual_lt"


def _rows(con: duckdb.DuckDBPyConnection, run_id: str, model: str) -> list[tuple[object, ...]]:
    return con.execute(
        "SELECT game_id, target, player_id, version, CAST(prediction AS VARCHAR) "
        "FROM forward_predictions WHERE run_id = ? AND model_name = ? "
        "ORDER BY game_id, target, player_id",
        [run_id, model],
    ).fetchall()


def test_cli_flag_default_off() -> None:
    p = _parser()
    assert p.parse_args(["run", "--date", "2026-10-28"]).log_lower_tail_variant is False
    on = p.parse_args(["run", "--date", "2026-10-28", "--log-lower-tail-variant"])
    assert on.log_lower_tail_variant is True


def test_shadow_rows_primary_byte_identical_settle_and_live_rule() -> None:
    c = build_fixture_db(":memory:")
    _big_history(c, 100, seed=3)
    tip = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 30)
    games = [
        ScheduledGame("0022600901", tip, T1, T2),
        ScheduledGame("0022600902", tip + timedelta(hours=3), T3, T4),
    ]
    kw = dict(schedule_fn=lambda s: games, skip_ingest=True, skip_injury=True, n_sims=50)
    s0 = run_daily(c, AS_OF, now=tip - timedelta(hours=4), **kw)  # type: ignore[arg-type]
    s1 = run_daily(c, AS_OF, now=tip - timedelta(hours=3), log_lower_tail_variant=True, **kw)  # type: ignore[arg-type]
    assert LT not in s0.model_status and "SHADOW" in s1.model_status[LT]
    for model in ("props_context_residual", "props_recency_v1"):
        a, b = _rows(c, s0.run_id, model), _rows(c, s1.run_id, model)
        assert a and a == b  # byte-identical stored predictions with the flag on/off
    assert not _rows(c, s0.run_id, LT)
    lt = _rows(c, s1.run_id, LT)
    assert lt and {r[1] for r in lt} == {"pts"} and {r[3] for r in lt} == {"ctxres-v1"}
    for r in lt:
        grid = np.array(json.loads(str(r[4]))["q_grid"])
        assert len(grid) == 19 and (grid == np.round(grid)).all()
    n_primary_pts = c.execute(
        "SELECT count(*) FROM forward_predictions WHERE run_id = ? AND target = 'pts' "
        "AND model_name = 'props_context_residual'",
        [s1.run_id],
    ).fetchone()
    assert n_primary_pts is not None and n_primary_pts[0] == len(lt)  # context rows only, pts only

    # results arrive: slate game 1 gets a box score, then settle scores primary and lt rows
    rng = np.random.default_rng(4)
    _insert_game(c, rng, "0022600901", AS_OF, T1, T2, {T1: 1000, T2: 1100, T3: 3000, T4: 3100})
    out = settle_pending(c, tip + timedelta(days=1))
    assert out["prop_scored"] > 0
    sc = c.execute(
        "SELECT s.crps, s.y, p.prediction FROM forward_scores s JOIN forward_predictions p "
        "ON p.game_id = s.game_id AND p.model_name = s.model_name AND p.player_id = s.player_id "
        "AND p.target = s.target AND p.run_id = ? WHERE s.model_name = ? AND s.status = 'scored'",
        [s1.run_id, LT],
    ).fetchall()
    assert sc
    for crps, y, pred in sc:
        grid = json.loads(pred)["q_grid"]
        assert crps is not None and crps >= 0
        assert abs(crps - quantile_crps(list(QUANTILE_TAUS), grid, y)) < 1e-9

    # frozen live rule: too few rows / dates => no claim
    res = lt_live.evaluate(c, season=2025)
    assert res["n_player_games"] > 0 and res["verdict"] == "insufficient_n_no_claim"
    assert res["min_player_games"] == 500 and res["min_dates"] == 14


def test_live_rule_verdicts() -> None:
    grid_p = [float(x) for x in range(1, 20)]
    row = lambda d, g: (d, g, 1, json.dumps(grid_p), json.dumps(grid_p), 10.0)  # noqa: E731
    same = [row(f"2026-11-{1 + i % 20:02d}", f"g{i}") for i in range(600)]
    r = lt_live.evaluate_pairs(same)
    assert r["verdict"] == "no_claim" and r["delta_crps"] == 0.0 and r["n_dates"] == 20
    assert lt_live.evaluate_pairs(same[:100])["verdict"] == "insufficient_n_no_claim"
    assert lt_live.evaluate_pairs([row("2026-11-01", f"g{i}") for i in range(600)])["verdict"] == (
        "insufficient_n_no_claim"
    )
    # lt grid strictly closer to the outcome on every row => better, floor and CI satisfied
    better = [
        (d, g, p, json.dumps(grid_p), json.dumps([10.0] * 19), y) for d, g, p, _, _, y in same
    ]
    rb = lt_live.evaluate_pairs(better)
    assert rb["verdict"] == "lt_better" and rb["ci95"][1] < 0
    worse = [(d, g, p, json.dumps([10.0] * 19), json.dumps(grid_p), y) for d, g, p, _, _, y in same]
    assert lt_live.evaluate_pairs(worse)["verdict"] == "lt_worse"
