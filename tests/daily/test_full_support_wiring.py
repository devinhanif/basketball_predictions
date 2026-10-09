"""Every forward prop arm stores full-support P(Y >= k); integer arm re-scores identically."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from nba.daily.pipeline import run_daily
from nba.daily.schedule import ScheduledGame
from nba.daily.settle import settle_pending
from nba.props import full_support as fs
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db
from tests.props.test_forward_context import AS_OF, _big_history, _insert_game

PRIMARY, RECENCY = "props_context_residual", "props_recency_v1"
INT, LT = "props_context_residual_int", "props_context_residual_lt"


def test_all_arms_store_full_support_and_int_rescoring_is_identical() -> None:
    c = build_fixture_db(":memory:")
    _big_history(c, 100, seed=3)
    tip = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 30)
    games = [
        ScheduledGame("0022600901", tip, T1, T2),
        ScheduledGame("0022600902", tip + timedelta(hours=3), T3, T4),
    ]
    s = run_daily(
        c,
        AS_OF,
        now=tip - timedelta(hours=3),
        schedule_fn=lambda _s: games,
        skip_ingest=True,
        skip_injury=True,
        n_sims=50,
        log_int_variant=True,
        log_lower_tail_variant=True,
    )
    rows = c.execute(
        "SELECT model_name, game_id, target, player_id, CAST(prediction AS VARCHAR) "
        "FROM forward_predictions WHERE run_id = ? AND target <> 'win_prob_home'",
        [s.run_id],
    ).fetchall()
    by_model: dict[str, dict[tuple[str, str, int], dict[str, object]]] = {}
    for model, gid, tgt, pid, pj in rows:
        by_model.setdefault(model, {})[(gid, tgt, pid)] = json.loads(pj)
    assert {PRIMARY, RECENCY, INT, LT} <= set(by_model)
    for model in (PRIMARY, RECENCY, INT, LT):
        for pred in by_model[model].values():
            surv = fs.survival(pred.get(fs.FIELD))  # type: ignore[arg-type]
            assert surv is not None and fs.is_complete(surv), model
            assert np.all(np.diff(surv) <= 1e-12)
            for t, v in pred["p_ge"].items():  # type: ignore[attr-defined]
                got = float(surv[int(t) - 1]) if int(t) - 1 < surv.size else 0.0
                # beyond the stored support the mass is at most TAIL_EPS by construction
                tol = 0.5 / pred[fs.FIELD]["n"] + 1e-9  # type: ignore[index]
                if int(t) - 1 >= surv.size:
                    tol = fs.TAIL_EPS
                assert abs(got - v) <= tol, (model, t)
    for key, pred in by_model[INT].items():  # convention invariance, row by row
        assert pred[fs.FIELD] == by_model[PRIMARY][key][fs.FIELD]
    assert all(k[1] == "pts" for k in by_model[LT])

    rng = np.random.default_rng(4)
    _insert_game(c, rng, "0022600901", AS_OF, T1, T2, {T1: 1000, T2: 1100, T3: 3000, T4: 3100})
    settle_pending(c, tip + timedelta(days=1))
    sc = c.execute(
        "SELECT model_name, game_id, target, player_id, rps, status FROM forward_scores_elig "
        "WHERE target <> 'win_prob_home'"
    ).fetchall()
    rps = {(m, g, t, p): r for m, g, t, p, r, st in sc if st == "scored"}
    assert any(k[0] == LT for k in rps) and all(r is not None and r >= 0 for r in rps.values())
    n_int = 0
    for (m, g, t, p), r in rps.items():
        if m == INT:
            assert r == rps[(PRIMARY, g, t, p)]  # delta_int exactly 0
            n_int += 1
    assert n_int > 0


def test_frame_preds_additive_only() -> None:
    import polars as pl

    from nba.daily.pipeline import _frame_preds

    tip = datetime(2026, 10, 28, 23, 30)
    g = ScheduledGame("0022600901", tip, T1, T2)
    base = {
        "game_id": g.game_id,
        "player_id": 5,
        "stat": "reb",
        "mean": 5.5,
        "std": 2.1,
        "dist_family": "negbin",
        "dist_params": '{"n": 4.0}',
        "p_ge": '{"2": 0.9}',
        "q10": 3.0,
        "q50": 5.0,
        "q90": 8.0,
        "q_grid": "[1.0, 2.0]",
        "p_play": 0.8,
        "mean_uncond": 4.4,
        "p_ge_uncond": '{"2": 0.72}',
        "model": "context_residual",
        "bucket": "x",
        "mean_sim": None,
        "mean_season_avg": 5.4,
        "proj_minutes": 24.0,
        "n_games_prior": 12,
    }
    extra = {"p_ge_full": '{"c": [199, 150, 0], "n": 199}', "starter_rate": 0.75}
    old = _frame_preds(pl.DataFrame([base]), [g], "m", "v", AS_OF, {"k": 1})[0]
    new = _frame_preds(pl.DataFrame([{**base, **extra}]), [g], "m", "v", AS_OF, {"k": 1})[0]
    assert set(new.prediction) - set(old.prediction) == {"p_ge_full", "starter_rate"}
    kept = {k: v for k, v in new.prediction.items() if k not in extra}
    assert json.dumps(kept, sort_keys=True) == json.dumps(old.prediction, sort_keys=True)
    assert new.prediction["p_ge_full"] == {"c": [199, 150, 0], "n": 199}
    nan = _frame_preds(
        pl.DataFrame([{**base, "starter_rate": float("nan")}]), [g], "m", "v", AS_OF, {}
    )
    assert "starter_rate" not in nan[0].prediction  # NaN never reaches the JSON column
