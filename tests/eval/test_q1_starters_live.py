# ruff: noqa: E501
"""Fixture tests for the Q1 starters live scorer (no database; synthetic frames)."""

from __future__ import annotations

import datetime as dt
import inspect
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.eval import q1_starters_live as Q

TIP = dt.datetime(2026, 11, 3, 0, 0)


def _q(mean: float, n: int = 199) -> list[int]:
    return sorted(int(v) for v in np.round(np.linspace(0, 2 * mean, n)))


def _frames(
    n_games: int = 6, per: int = 4, seed: int = 0
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    f, o, y = [], [], []
    for g in range(n_games):
        tip = TIP + dt.timedelta(days=g)
        for p in range(per):
            pid = 100 + p
            gid = f"g{g}"
            f.append(
                {
                    "game_id": gid,
                    "player_id": pid,
                    "tipoff": tip,
                    "base_made_at": tip - dt.timedelta(minutes=60),
                    "t15_made_at": tip - dt.timedelta(minutes=14),
                    "t15_snapshot_fetched_at": tip - dt.timedelta(minutes=18),
                    "start_status": "starter" if p < 3 else "bench",
                    "base_q": _q(4.0 if p < 3 else 1.0),
                }
            )
            for side, px in (("over", -110), ("under", -110)):
                o.append(
                    {
                        "game_id": gid,
                        "snapshot_kind": "t15",
                        "market": "player_points_q1",
                        "book": "fanduel",
                        "player_id": pid,
                        "side": side,
                        "point": 3.5,
                        "price_american": px,
                        "tipoff": tip,
                        "requested_at": tip - dt.timedelta(minutes=15),
                        "received_at": tip - dt.timedelta(minutes=14),
                    }
                )
            y.append({"game_id": gid, "player_id": pid, "q1_pts": int(rng.integers(0, 9))})
    return pl.DataFrame(f), pl.DataFrame(o), pl.DataFrame(y)


def test_constants_are_the_frozen_ones() -> None:
    assert (Q.N_MIN_GAMES, Q.N_MIN_LEGS, Q.N_MIN_DATES, Q.INTERIM_GAMES) == (250, 2000, 35, 125)
    assert (Q.FLOOR_P1, Q.FLOOR_P2, Q.GATE_HALFWIDTH) == (-0.005, -0.005, 0.005)
    assert Q.PRIMARY_BOOKS == ("fanduel", "williamhill_us")
    assert Q.LIVE_SEASON == 2026 and Q.HISTORY_MAX_SEASON == 2024


def test_p_over_half_point_integer_grid() -> None:
    q = np.array([_q(4.0)], dtype=float)
    pge = Q.pge_from_quantiles(q)
    # L = 3.5 -> P(Y >= 4); L = 4.5 -> P(Y >= 5): monotone, whole lines are undefined
    a, b = Q.p_over(pge, np.array([3.5]))[0], Q.p_over(pge, np.array([4.5]))[0]
    assert a > b
    assert a == pytest.approx((q[0] >= 4).mean())
    assert np.isnan(Q.p_over(pge, np.array([4.0]))[0])


def test_platt_recovers_planted_slope() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0.2, 0.8, 40000)
    z = 0.3 + 0.4 * Q.logit(p)
    y = (rng.random(p.size) < Q.expit(z)).astype(float)
    par = Q.fit_platt(p, y)
    assert par["a"] == pytest.approx(0.3, abs=0.06) and par["b"] == pytest.approx(0.4, abs=0.06)
    assert Q.fit_platt(p[:10], y[:10]) == {"a": 0.0, "b": 1.0, "n": 10}


def test_arm_probs_group_split_and_identity() -> None:
    par = {
        "global": {"a": 0.0, "b": 1.0},
        "groups": {"starter": {"a": 0.5, "b": 1.0}, "bench": {"a": -0.5, "b": 1.0}},
    }
    p = np.array([0.5, 0.5])
    arms = Q.arm_probs(par, p, np.array(["starter", "bench"]))
    assert arms["recal"] == pytest.approx([0.5, 0.5])
    assert arms["t15"][0] > 0.5 > arms["t15"][1]
    assert arms["base"] is p


def test_devig_and_logloss() -> None:
    assert Q.devig_over(np.array([-110]), np.array([-110]))[0] == pytest.approx(0.5)
    assert Q.devig_over(np.array([-200]), np.array([170]))[0] > 0.6
    assert Q.logloss(np.array([0.5]), np.array([1.0]))[0] == pytest.approx(np.log(2))


def test_cluster_ci_widens_with_clustering() -> None:
    rng = np.random.default_rng(2)
    g = np.repeat(np.arange(60), 8)
    v = rng.normal(size=60)[g] + 0.01 * rng.normal(size=g.size)  # fully clustered noise
    wide = Q.cluster_ci(v, g)
    naive = Q.cluster_ci(v, np.arange(g.size))
    assert wide["hi"] - wide["lo"] > 2 * (naive["hi"] - naive["lo"])
    assert wide["clusters"] == 60


def test_q1_points_from_pbp_rule() -> None:
    ev = pl.DataFrame(
        {
            "game_id": ["g"] * 6,
            "period": [1, 1, 1, 1, 2, 1],
            "player_id": [1, 1, 1, 2, 1, 2],
            "action_type": [
                "Made Shot",
                "Made Shot",
                "Free Throw",
                "Free Throw",
                "Made Shot",
                "Free Throw",
            ],
            "description": [
                "",
                "",
                "Free Throw 1 of 2",
                "MISS Free Throw",
                "",
                "Free Throw 2 of 2",
            ],
            "shot_value": [2, 3, None, None, 2, None],
        }
    )
    r = {(a, b): c for a, b, c in Q.q1_points_from_pbp(ev).iter_rows()}
    assert r[("g", 1)] == 6 and r[("g", 2)] == 1


def test_assemble_counts_every_drop_and_keeps_clean_rows() -> None:
    fc, odds, y = _frames()
    legs, cnt = Q.assemble_legs(fc, odds, y)
    assert legs.height == 24 and cnt["legs"] == 24
    # a snapshot at tip-14 (after the T-15 cutoff) is dropped and counted, never repaired
    late = fc.with_columns(
        pl.when(pl.col("game_id") == "g0")
        .then(pl.col("tipoff") - pl.duration(minutes=14))
        .otherwise(pl.col("t15_snapshot_fetched_at"))
        .alias("t15_snapshot_fetched_at")
    )
    legs2, cnt2 = Q.assemble_legs(late, odds, y)
    assert cnt2["snapshot_not_pre_cutoff"] == 4 and legs2.height == 20
    # a price received at or after tip is refused
    post = odds.with_columns(
        pl.when(pl.col("game_id") == "g1")
        .then(pl.col("tipoff"))
        .otherwise(pl.col("received_at"))
        .alias("received_at")
    )
    legs3, cnt3 = Q.assemble_legs(fc, post, y)
    assert cnt3["price_not_pre_tip"] == 4 and legs3.height == 20
    # base forecast stamped after the price capture started
    after = fc.with_columns(
        pl.when(pl.col("game_id") == "g2")
        .then(pl.col("tipoff") - pl.duration(minutes=14))
        .otherwise(pl.col("base_made_at"))
        .alias("base_made_at")
    )
    _, cnt4 = Q.assemble_legs(after, odds, y)
    assert cnt4["base_after_capture"] == 4
    # OUT players and missing status are removed and counted
    out = fc.with_columns(
        pl.when(pl.col("player_id") == 100)
        .then(pl.lit("out"))
        .when(pl.col("player_id") == 101)
        .then(None)
        .otherwise(pl.col("start_status"))
        .alias("start_status")
    )
    legs5, cnt5 = Q.assemble_legs(out, odds, y)
    assert cnt5["out"] == 6 and cnt5["no_status"] == 6 and legs5.height == 12
    # books outside the primary pair are ignored; one-sided quotes counted
    other = odds.with_columns(pl.lit("draftkings").alias("book"))
    assert Q.assemble_legs(fc, other, y)[0].height == 0
    one = odds.filter(~((pl.col("side") == "under") & (pl.col("game_id") == "g3")))
    assert Q.assemble_legs(fc, one, y)[1]["one_sided"] == 4


def test_planted_future_outcomes_and_late_prices_cannot_move_a_forecast() -> None:
    """Arms are functions of (stamped grid, line, start status, frozen params) only: changing the outcome
    of a game, or any price after tip, leaves every arm probability unchanged."""
    fc, odds, y = _frames()
    par = {
        "global": {"a": 0.1, "b": 0.5},
        "groups": {"starter": {"a": 0.0, "b": 0.6}, "bench": {"a": 0.0, "b": 0.2}},
    }
    legs, _ = Q.assemble_legs(fc, odds, y)
    legs = legs.with_columns(pl.col("tipoff").dt.date().cast(pl.Utf8).alias("game_date"))
    a = Q.score_arms(legs, par)
    y2 = y.with_columns((pl.col("q1_pts") + 20).alias("q1_pts"))
    legs2, _ = Q.assemble_legs(fc, odds, y2)
    legs2 = legs2.with_columns(pl.col("tipoff").dt.date().cast(pl.Utf8).alias("game_date"))
    b = Q.score_arms(legs2, par)
    assert a["mean_p_over"] == b["mean_p_over"]  # outcomes move scores, never probabilities
    assert a["mean_logloss"]["t15"] != b["mean_logloss"]["t15"]
    src = (
        inspect.getsource(Q.arm_probs)
        + inspect.getsource(Q.p_over)
        + inspect.getsource(Q.apply_platt)
    )
    assert "price" not in src and "market" not in src  # no market input to any arm


def test_score_arms_shapes_and_decision_cells() -> None:
    fc, odds, y = _frames(n_games=40)
    par = {
        "global": {"a": 0.0, "b": 1.0},
        "groups": {"starter": {"a": 0.0, "b": 1.0}, "bench": {"a": 0.0, "b": 1.0}},
    }
    legs, _ = Q.assemble_legs(fc, odds, y)
    legs = legs.with_columns(pl.col("tipoff").dt.date().cast(pl.Utf8).alias("game_date"))
    r = Q.score_arms(legs, par)
    assert r["n_legs"] == 160 and r["n_games"] == 40 and r["n_dates"] == 40
    # identity parameters: t15 == recal == base, so P1 is exactly zero
    assert r["P1_t15_minus_recal"]["game_clustered"]["mean"] == 0.0
    assert set(r["decision"]) == {"ev>=0.0", "ev>=0.02", "ev>=0.05", "ev>=0.1"}
    assert "status=starter" in r["slices"] and "book=fanduel" in r["slices"]
    assert Q.score_arms(legs.head(0), par)["status"] == "no legs"


def _res(
    ng: int, nl: int, nd: int, p1: tuple[float, float, float], p2: tuple[float, float, float]
) -> dict[str, Any]:
    def blk(t: tuple[float, float, float]) -> dict[str, Any]:
        c = {"mean": t[0], "lo": t[1], "hi": t[2]}
        return {"game_clustered": c, "date_clustered": c}

    return {
        "n_games": ng,
        "n_legs": nl,
        "n_dates": nd,
        "P1_t15_minus_recal": blk(p1),
        "P2_t15_minus_market": blk(p2),
        "gate_halfwidth_P2": 0.004,
    }


def test_verdict_states() -> None:
    assert Q.verdict(_res(60, 500, 20, (0, -1, 1), (0, -1, 1)))["state"] == "accumulating"
    v = Q.verdict(_res(130, 1000, 20, (0.01, 0.002, 0.02), (0.02, 0.01, 0.03)))
    assert v["state"] == "interim_kill_only" and len(v["kill"]) == 2
    v = Q.verdict(_res(130, 1000, 20, (-0.01, -0.02, -0.001), (-0.01, -0.02, -0.001)))
    assert v["state"] == "interim_kill_only" and v["kill"] == [] and "beats_the_line" not in v
    # large enough games but not legs/dates: still interim
    assert Q.verdict(_res(300, 1500, 40, (0, -1, 1), (0, -1, 1)))["state"] == "interim_kill_only"
    ok = Q.verdict(_res(300, 2400, 40, (-0.007, -0.01, -0.002), (-0.006, -0.01, -0.001)))
    assert ok["state"] == "final" and ok["beats_the_line"] and ok["gate_halfwidth_ok"]
    # CI excludes zero but the mean misses the practical floor
    miss = Q.verdict(_res(300, 2400, 40, (-0.003, -0.005, -0.001), (-0.006, -0.01, -0.001)))
    assert miss["P1_pass"] is False and miss["beats_the_line"] is False
    # mean clears the floor but the CI includes zero
    wide = Q.verdict(_res(300, 2400, 40, (-0.007, -0.01, 0.001), (-0.006, -0.01, -0.001)))
    assert wide["P1_pass"] is False


def test_params_loader_refuses_season_2025(tmp_path: Path) -> None:
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"fit_seasons": [2024], "params": {"global": {}, "groups": {}}}))
    assert Q.load_params(good) == {"global": {}, "groups": {}}
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"fit_seasons": [2024, 2025], "params": {}}))
    with pytest.raises(AssertionError):
        Q.load_params(bad)


def test_module_opens_databases_read_only_only() -> None:
    src = inspect.getsource(Q)
    assert src.count("duckdb.connect(") == src.count("read_only=True")
