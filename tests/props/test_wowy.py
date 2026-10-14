# ruff: noqa: E501
"""WOWY redistribution features (docs/prereg/WOWY_REDISTRIBUTION.md DRAFT): hand-computed values, planted
future, stints, window, placebo, missingness rule and parity with production's OUT-rotation join."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props import wowy as W
from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    ROTATION_MIN40,
    player_states,
    vacated_features,
)

T1, T2 = 1, 2
P1, J, K_, Z = 11, 12, 13, 14


def _day(k: int) -> dt.date:
    return dt.date(2024, 1, 1) + dt.timedelta(days=k)


def _row(g: int, team: int, pid: int, minutes: float, **stats: float) -> dict[str, object]:
    base = {"pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0, "fga": 0.0, "fg3a": 0.0}
    base.update({k: float(v) for k, v in stats.items()})
    return {
        "game_id": f"G{g:03d}",
        "team_id": team,
        "player_id": pid,
        "minutes": float(minutes),
        "game_date": _day(g),
        **base,
    }


def _box(n_games: int = 11, j_absent: range = range(6, 10)) -> pl.DataFrame:
    """P1 plays every game: 20 min / 10 pts / 4 ast / 10 fga with J, 30 min / 18 pts / 8 ast / 15 fga without
    J. J plays except in ``j_absent`` (no row, as the real box lists only the dressed). Game n_games-1 is g."""
    rows = []
    for g in range(n_games):
        wo = g in j_absent
        rows.append(
            _row(
                g,
                T1,
                P1,
                30.0 if wo else 20.0,
                pts=18 if wo else 10,
                ast=8 if wo else 4,
                reb=5,
                fga=15 if wo else 10,
                fg3m=1,
                fg3a=3,
            )
        )
        if not wo:
            rows.append(_row(g, T1, J, 30.0, pts=15))
        rows.append(_row(g, T1, K_, 25.0, pts=9))
    return pl.DataFrame(rows)


def _out(g: int, players: list[int]) -> dict[tuple[str, int], list[int]]:
    return {(f"G{g:03d}", T1): players}


def test_hand_computed_deltas() -> None:
    box = _box()
    # g = G010: J listed OUT; the box row of J for g does not exist (he did not play)
    f = W.build_wowy(box, _out(10, [J]))
    r = f.filter((pl.col("player_id") == P1) & (pl.col("game_id") == "G010")).row(0, named=True)
    w = 4 / (4 + W.SHRINK_K)
    assert abs(r["wo_d_min"] - w * 10.0) < 1e-12
    assert abs(r["wo_d_pts"] - w * 8.0) < 1e-12
    assert abs(r["wo_d_ast"] - w * 4.0) < 1e-12
    assert abs(r["wo_conf"] - w) < 1e-12 and r["wo_n"] == 4.0
    # per-36 fga: without 15/30*36 = 18, with 10/20*36 = 18 -> equal rates, delta 0
    assert abs(r["wo_r_fga"]) < 1e-12
    # per-36 ast: 8/30*36 - 4/20*36 = 9.6 - 7.2
    assert abs(r["wo_r_ast"] - w * (9.6 - 7.2)) < 1e-12
    # K never played with J's absence differently: his own deltas are 0
    rk = f.filter((pl.col("player_id") == K_) & (pl.col("game_id") == "G010")).row(0, named=True)
    assert (
        rk["wo_d_min"] == 0.0 and rk["wo_conf"] > 0.0
    )  # a pair existed; no difference in K's minutes


def test_planted_future_changes_nothing() -> None:
    base = _box(n_games=14)
    out = _out(10, [J])
    f0 = W.build_wowy(base, out).filter(pl.col("game_id") == "G010")
    # rewrite game 10 and everything after: huge minutes/stats, J suddenly plays, P1 sits out for games 11+
    planted = base.with_columns(
        pl.when(pl.col("game_id") >= "G010")
        .then(pl.col("pts") * 7.0 + 50.0)
        .otherwise(pl.col("pts"))
        .alias("pts"),
        pl.when((pl.col("game_id") >= "G011") & (pl.col("player_id") == J))
        .then(1.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    )
    extra = pl.DataFrame([_row(12, T1, J, 40.0, pts=99), _row(13, T1, J, 40.0, pts=99)])
    planted = pl.concat([planted, extra])
    f1 = W.build_wowy(planted, out).filter(pl.col("game_id") == "G010")
    cols = ["player_id", *W.WOWY_COLS]
    assert f0.select(cols).sort("player_id").equals(f1.select(cols).sort("player_id"))


def test_tonights_own_box_row_is_not_an_input() -> None:
    box = _box()
    out = _out(10, [J])
    f0 = W.build_wowy(box, out)
    shifted = box.with_columns(
        pl.when(pl.col("game_id") == "G010")
        .then(pl.col("ast") + 20)
        .otherwise(pl.col("ast"))
        .alias("ast")
    )
    f1 = W.build_wowy(shifted, out)
    assert f0.select(W.WOWY_COLS).equals(f1.select(W.WOWY_COLS))


def test_other_team_stint_excludes_the_pair() -> None:
    box = _box()
    # J appears for T2 in game 9's date: his last appearance before g is for another team -> no pair
    moved = pl.concat([box, pl.DataFrame([_row(9, T2, J, 30.0, pts=12)])])
    f = W.build_wowy(moved, _out(10, [J]))
    r = f.filter((pl.col("player_id") == P1) & (pl.col("game_id") == "G010")).row(0, named=True)
    assert r["wo_conf"] == 0.0 and r["wo_d_min"] == 0.0


def test_stint_starts_at_first_game_with_the_team() -> None:
    # J first appears for T1 in game 5: his absences in games 0-4 (before the stint) must not count
    rows = []
    for g in range(11):
        j_in = g >= 5 and g not in (8, 9)
        rows.append(_row(g, T1, P1, 30.0 if not j_in else 20.0, pts=10))
        if j_in:
            rows.append(_row(g, T1, J, 30.0))
    f = W.build_wowy(pl.DataFrame(rows), _out(10, [J]))
    r = f.filter((pl.col("player_id") == P1) & (pl.col("game_id") == "G010")).row(0, named=True)
    # without J inside the stint: games 8, 9 only (n_wo = 2), not games 0-4
    assert r["wo_n"] == 2.0
    w = 2 / (2 + W.SHRINK_K)
    assert abs(r["wo_d_min"] - w * (30.0 - 20.0)) < 1e-12


def test_window_ignores_old_games() -> None:
    box = _box(n_games=11)
    f_small = W.build_wowy(
        box, _out(10, [J]), window=3
    )  # games 7..9 only: J absent 7,8,9; with-J none
    r = f_small.filter((pl.col("player_id") == P1) & (pl.col("game_id") == "G010")).row(
        0, named=True
    )
    assert r["wo_conf"] == 0.0  # no 'with' game inside the window -> no contrast, 0 not NaN


def test_target_rows_restrict_and_only_played_rows() -> None:
    box = _box()
    keep = pl.DataFrame({"game_id": ["G010"], "player_id": [K_]})
    f = W.build_wowy(box, _out(10, [J]), target_rows=keep)
    assert f.height == 1 and f["player_id"][0] == K_


def test_attach_missingness_rule() -> None:
    feats = pl.DataFrame(
        {
            "game_id": ["G010", "G011", "G012"],
            "team_id": [T1, T1, T1],
            "player_id": [P1, P1, P1],
            "has_report": [1, 1, 0],
        }
    )
    wowy = pl.DataFrame(
        {"game_id": ["G010"], "team_id": [T1], "player_id": [P1], **{c: [1.5] for c in W.WOWY_COLS}}
    )
    a = W.attach_wowy(feats, wowy).sort("game_id")
    assert a["wo_d_min"].to_list() == [
        1.5,
        0.0,
        None,
    ]  # reported & out -> value; reported & none -> 0; no report -> NULL


def test_placebo_count_exclusion_and_no_tonight_box() -> None:
    box = _box(n_games=11)
    # add two more rotation regulars so the pool has candidates
    extra = []
    for g in range(11):
        extra += [_row(g, T1, Z, 24.0), _row(g, T1, 15, 20.0)]
    box = pl.concat([box, pl.DataFrame(extra)])
    pool = W.asof_rotation_pool(box)
    real = {("G010", T1): [J]}
    pl_sets = W.placebo_out_sets(real, pool, seed=0)
    assert len(pl_sets[("G010", T1)]) == 1 and J not in pl_sets[("G010", T1)]
    # tonight's box does not change the pool
    tweaked = box.with_columns(
        pl.when(pl.col("game_id") == "G010").then(0.5).otherwise(pl.col("minutes")).alias("minutes")
    )
    assert W.asof_rotation_pool(tweaked)[("G010", T1)] == pool[("G010", T1)]
    # deterministic
    assert W.placebo_out_sets(real, pool, seed=0) == pl_sets


def test_head_cols_exist() -> None:
    for stat, cols in W.HEAD_COLS.items():
        assert stat in ("pts", "reb", "ast", "fg3m")
        assert all(c in W.WOWY_COLS for c in cols)


def test_out_rotation_players_matches_production_vacated() -> None:
    rng = np.random.default_rng(0)
    rows = []
    for g in range(30):
        for p in range(1, 9):
            mins = float(rng.integers(8, 36))
            rows.append(
                {
                    "game_id": f"G{g:03d}",
                    "player_id": p,
                    "team_id": T1,
                    "game_date": _day(g),
                    "season": 2023,
                    "minutes": mins,
                    "pts": float(rng.integers(0, 25)),
                    "reb": float(rng.integers(0, 10)),
                    "ast": float(rng.integers(0, 8)),
                    "fg3m": float(rng.integers(0, 4)),
                    "starter": p <= 5,
                }
            )
    played = pl.DataFrame(rows).sort(["player_id", "game_date", "game_id"])
    states = player_states(played)
    info = {f"G{g:03d}": (_day(g), T1, T2) for g in range(30)}
    flagged = {"G020": {1, 2}, "G025": {3}, "G010": {4}}
    mine = W.out_rotation_players(played, states, flagged, info, MIN_PRIOR_PLAYED, ROTATION_MIN40)
    agg = mine.group_by(["game_id", "team_id"]).agg(
        pl.len().alias("n_out_rot"), pl.col("min10").sum().alias("vac_min")
    )
    prod = vacated_features(played, states, flagged, info)
    a = agg.sort("game_id").select("game_id", "n_out_rot", "vac_min")
    b = prod.sort("game_id").select("game_id", pl.col("n_out_rot").cast(pl.UInt32), "vac_min")
    assert a.cast({"n_out_rot": pl.UInt32}).equals(b)
