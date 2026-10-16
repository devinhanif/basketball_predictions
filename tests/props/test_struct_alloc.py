"""STRUCT allocation features (docs/prereg/WOWY_REDISTRIBUTION.md follow-up, DRAFT)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl

from nba.props import struct_alloc as S
from nba.props import wowy as W
from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    ROTATION_MIN40,
    player_states,
    vacated_features,
)

T1, T2 = 1, 2
PLAYERS = [1, 2, 3, 4, 5, 6]


def _day(k: int) -> dt.date:
    return dt.date(2024, 1, 1) + dt.timedelta(days=k)


def _box(n: int = 30, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n):
        for p in PLAYERS:
            m = float(rng.integers(14, 36))
            rows.append(
                {
                    "game_id": f"G{g:03d}",
                    "team_id": T1,
                    "player_id": p,
                    "game_date": _day(g),
                    "season": 2023,
                    "minutes": m,
                    "pts": float(rng.integers(2, 26)),
                    "reb": float(rng.integers(0, 10)),
                    "ast": float(rng.integers(0, 8)),
                    "fg3m": float(rng.integers(0, 4)),
                    "fga": float(rng.integers(3, 20)),
                    "fta": float(rng.integers(0, 8)),
                    "tov": float(rng.integers(0, 5)),
                }
            )
    return pl.DataFrame(rows)


def _dates(n: int = 30) -> pl.DataFrame:
    return pl.DataFrame(
        {"game_id": [f"G{g:03d}" for g in range(n)], "game_date": [_day(g) for g in range(n)]}
    )


def _positions() -> pl.DataFrame:
    return pl.DataFrame({"player_id": PLAYERS, "position": ["G", "G", "F", "F", "C", "C"]})


def _run(box: pl.DataFrame, out_pid: int = 1, g: int = 25) -> pl.DataFrame:
    states = S.decayed_states(box)
    pool = W.asof_rotation_pool(box)
    gid = f"G{g:03d}"
    outp = pl.DataFrame({"game_id": [gid], "team_id": [T1], "player_id": [out_pid]})
    rows = box.filter((pl.col("game_id") == gid) & (pl.col("player_id") != out_pid)).select(
        "game_id", "team_id", "player_id"
    )
    return S.build_struct(rows, outp, pool, states, _dates(), _positions())


def test_allocated_shares_sum_to_vacated() -> None:
    f = _run(_box())
    for s in S.STATS:
        assert abs(f[f"struct_d_{s}"].sum() - f[f"V_{s}"][0]) < 1e-9
    for s in S.USAGE_STATS:
        assert abs(f[f"struct_u_{s}"].sum() - f[f"V_{s}"][0]) < 1e-9
    assert abs(f["struct_dmin_role"].sum() - f["V_min"][0]) < 1e-9
    assert abs(f["struct_dmin_pos"].sum() - f["V_min"][0]) < 1e-9


def test_position_variant_favours_same_group() -> None:
    f = _run(_box(), out_pid=1).sort("player_id")  # player 1 is a guard; player 2 the other guard
    role = dict(zip(f["player_id"].to_list(), f["struct_dmin_role"].to_list(), strict=True))
    pos = dict(zip(f["player_id"].to_list(), f["struct_dmin_pos"].to_list(), strict=True))
    assert pos[2] > role[2] and pos[5] < role[5]


def test_planted_future_changes_nothing() -> None:
    box = _box()
    f0 = _run(box).sort("player_id")
    scr = box.with_columns(
        *[
            pl.when(pl.col("game_id") >= "G025")
            .then(pl.col(c) * 5.0 + 3.0)
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("minutes", "pts", "reb", "ast", "fg3m", "fga", "fta", "tov")
        ]
    )
    # tonight's row is scored, but nothing of game 25 or later may enter a state or the pool
    f1 = _run(scr).sort("player_id")
    cols = [c for c in f0.columns if c.startswith(("struct_", "V_", "leader_out_", "role_share_"))]
    for c in (
        cols
    ):  # polars group-by sums are order-nondeterministic: floats to 1e-9, discrete columns exactly
        if c.startswith("leader_out_"):
            assert f0[c].to_list() == f1[c].to_list()
        else:
            assert np.allclose(f0[c].to_numpy(), f1[c].to_numpy(), rtol=0, atol=1e-9)


def test_vacated_totals_match_production_vac() -> None:
    box = _box()
    played = box.sort(["player_id", "game_date", "game_id"]).with_columns(
        pl.col("minutes").alias("minutes"), (pl.col("player_id") <= 3).alias("starter")
    )
    st = player_states(played)
    info = {f"G{g:03d}": (_day(g), T1, T2) for g in range(30)}
    flagged = {"G025": {1, 2}, "G020": {3}}
    prod = vacated_features(played, st, flagged, info).sort("game_id")
    outp = W.out_rotation_players(played, st, flagged, info, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    f = S.build_struct(
        box.filter(pl.col("game_id").is_in(["G025", "G020"])).select(
            "game_id", "team_id", "player_id"
        ),
        outp,
        W.asof_rotation_pool(box),
        S.decayed_states(box),
        _dates(),
        _positions(),
    )
    mine = (
        f.group_by("game_id")
        .agg(pl.col("V_min").first(), pl.col("V_pts").first(), pl.col("V_ast").first())
        .sort("game_id")
    )
    assert np.allclose(mine["V_min"].to_numpy(), prod["vac_min"].to_numpy())
    assert np.allclose(mine["V_pts"].to_numpy(), prod["vac_pts"].to_numpy())
    assert np.allclose(mine["V_ast"].to_numpy(), prod["vac_ast"].to_numpy())


def test_leader_flag() -> None:
    box = _box().with_columns(
        pl.when(pl.col("player_id") == 1)
        .then(pl.col("pts") + 30)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    f = _run(box, out_pid=1)
    assert (f["leader_out_pts"] == 1).all()
    f2 = _run(box, out_pid=6)
    assert (f2["leader_out_pts"] == 0).all()


def _prop(theta_use: float = 0.0) -> dict[str, dict[str, Any]]:
    n = len(S.TRAITS)
    th = [0.0] * n
    th[S.TRAITS.index("tr_use")] = theta_use
    return {s: {"mu": [0.0] * n, "sd": [1.0] * n, "theta": th} for s in S.STATS}


def _run_prop(box: pl.DataFrame, prop: dict[str, dict[str, Any]]) -> pl.DataFrame:
    states = S.decayed_states(box)
    pool = W.asof_rotation_pool(box)
    outp = pl.DataFrame({"game_id": ["G025"], "team_id": [T1], "player_id": [1]})
    rows = box.filter((pl.col("game_id") == "G025") & (pl.col("player_id") != 1)).select(
        "game_id", "team_id", "player_id"
    )
    return S.build_struct(rows, outp, pool, states, _dates(), _positions(), prop=prop)


def test_prop_with_flat_propensity_equals_plain_and_conserves() -> None:
    box = _box()
    f = _run_prop(box, _prop(0.0))
    for s in S.STATS:
        assert np.allclose(f[f"prop_d_{s}"].to_numpy(), f[f"struct_d_{s}"].to_numpy())
    f2 = _run_prop(box, _prop(0.5))
    for s in S.STATS:
        assert abs(f2[f"prop_d_{s}"].sum() - f2[f"V_{s}"][0]) < 1e-9


def test_prop_shifts_share_toward_high_trait_player() -> None:
    box = _box()
    f0 = _run_prop(box, _prop(0.0)).sort("player_id")
    f1 = _run_prop(box, _prop(1.0)).sort("player_id")
    hi = f1["tr_use"].arg_max()
    assert hi is not None
    assert f1["prop_d_pts"][hi] > f0["prop_d_pts"][hi]


def test_attach_struct_null_rule_and_role_share() -> None:
    box = _box()
    f = _run(box)
    assert (f["role_share_pts"] > 0).all() and (f["role_share_pts"] < 1).all()
    feats = pl.DataFrame(
        {
            "game_id": ["G025", "G025", "G026", "G027"],
            "team_id": [T1, T1, T1, T1],
            "player_id": [2, 3, 2, 2],
            "has_report": [1, 1, 1, 0],
        }
    )
    a = S.attach_struct(feats, f.filter(pl.col("player_id").is_in([2, 3])))
    # reported and scored: the value; reported, nobody OUT: explicit 0; no usable report: NULL
    assert a["struct_d_pts"][0] is not None and a["struct_d_pts"][0] > 0
    assert a["struct_d_pts"][2] == 0.0
    assert a["struct_d_pts"][3] is None and a["role_share_pts"][3] is None and a["V_pts"][3] is None
