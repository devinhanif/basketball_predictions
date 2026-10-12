# ruff: noqa: E501
"""prod_state_rows: parity with build_features on played rows, truncation truth on DNP rows, planted future."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import _CI, _DI, build_features, player_states
from research.recent_role import prod_state as PS

ELO = {
    "k_factor": 10.0,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 0.00067,
}


def _league(
    seed: int = 0, n_days: int = 260, n_teams: int = 6, per_team: int = 9
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 10, 20)
    games, rows = [], []
    gid = 0
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 2)
        season = d.year if d.month >= 8 else d.year - 1
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            h, a = int(order[k]), int(order[k + 1])
            gid += 1
            g = f"{gid:07d}"
            games.append(
                (g, d, season, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125)))
            )
            for t in (h, a):
                for p in range(per_team):
                    pid = t * 100 + p
                    team = t
                    if pid == 105 and day > 150:  # a trade: player 105 moves to team 2
                        if t != 1:
                            continue
                        team = 1
                    played = rng.random() > (0.05 if p < 6 else 0.45)
                    base_min = 34 - 3 * p
                    mins = float(max(base_min + rng.normal(0, 3), 4)) if played else None
                    lam = max(mins or 0, 0) / 34.0
                    rows.append((g, pid, team, mins,
                                 int(rng.poisson(14 * lam * (1.3 - p * 0.1))) if played else 0,
                                 int(rng.poisson(5 * lam)) if played else 0,
                                 int(rng.poisson(3 * lam)) if played else 0,
                                 int(rng.poisson(1.5 * lam)) if played else 0,
                                 bool(p < 5)))  # fmt: skip
    gdf = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )
    pdf = pl.DataFrame(
        rows,
        schema=[
            "game_id",
            "player_id",
            "team_id",
            "minutes",
            "pts",
            "reb",
            "ast",
            "fg3m",
            "starter",
        ],
        orient="row",
    )
    static = pl.DataFrame(
        {"player_id": sorted(pdf["player_id"].unique().to_list()), "position": "G"}
    )
    return gdf, pdf, static


def _box(pdf: pl.DataFrame, gdf: pl.DataFrame) -> pl.DataFrame:
    return pdf.join(gdf.select("game_id", "game_date", "season"), on="game_id")


def test_parity_with_build_features_on_every_played_row() -> None:
    gdf, pdf, static = _league()
    feats = build_features(gdf, pdf, static, {}, ELO)
    box = _box(pdf, gdf)
    q = feats.select("game_id", "player_id", "team_id", "season", "game_date")
    got = PS.prod_state_rows(box, q)
    assert got.height == feats.height
    assert got["game_id"].to_list() == feats["game_id"].to_list()
    for c in PS.ALL_STATE_COLS:
        a, b = got[c].to_numpy().astype(float), feats[c].to_numpy().astype(float)
        assert np.allclose(a, b, atol=1e-9, equal_nan=True), c
    assert (feats["n_prior"] == 0).any() and (
        feats["n_season"] == 0
    ).sum() > 6  # the edge rows exist


def test_dnp_rows_equal_the_forward_path_placeholder_truth() -> None:
    gdf, pdf, _ = _league()
    box = _box(pdf, gdf)
    dnp = box.filter(pl.col("minutes").is_null()).filter(pl.col("player_id") % 7 == 3)
    assert dnp.height > 40
    q = dnp.select("game_id", "player_id", "team_id", "season", "game_date")
    got = PS.prod_state_rows(box, q)
    checked = 0
    for r, g in zip(q.iter_rows(named=True), got.iter_rows(named=True), strict=True):
        hist = box.filter(
            (pl.col("player_id") == r["player_id"])
            & (pl.col("game_date") < r["game_date"])
            & (pl.col("minutes") > 0)
        )
        if hist.height < 3:
            continue
        probe = pl.DataFrame({
            "player_id": [r["player_id"]], "team_id": [r["team_id"]], "season": [r["season"]], "game_date": [r["game_date"]],
            "game_id": ["~probe"], "pts": [0], "reb": [0], "ast": [0], "fg3m": [0], "minutes": [1.0], "starter": [False],
        }).cast(dict(hist.select(PS.PLAYED_COLS).schema))  # fmt: skip
        both = pl.concat([hist.select(PS.PLAYED_COLS), probe], how="vertical_relaxed").sort(
            ["player_id", "game_date", "game_id"]
        )
        st = player_states(both)
        i = both.height - 1
        pre = st["pre"][i]
        want = {
            "n_prior": float(st["n_prior"][i]), "n_season": float(st["n_season"][i]), "gap_days": float(st["gap_days"][i]),
            "since_flag": float(st["since_flag"][i]), "flag_dir": float(st["flag_dir"][i]), "since_team": float(st["since_team"][i]),
            "min5": pre[_DI[5.0], _CI["min"]], "min10": pre[_DI[10.0], _CI["min"]], "min40": pre[_DI[40.0], _CI["min"]],
            "starter10": pre[_DI[10.0], _CI["starter"]], "m10_pts": pre[_DI[10.0], _CI["pts"]], "m100_ast": pre[_DI[100.0], _CI["ast"]],
            "last_starter": float(both["starter"].cast(pl.Float64).to_numpy()[i - 1]),
        }  # fmt: skip
        for c, v in want.items():
            assert abs(g[c] - v) < 1e-9, (c, g[c], v, r)
        checked += 1
    assert checked > 25


def test_planted_future_changes_nothing_and_previous_game_edit_moves_it() -> None:
    gdf, pdf, _ = _league()
    box = _box(pdf, gdf)
    D = sorted(box["game_date"].unique().to_list())[200]
    q = box.filter(pl.col("game_date") <= D).select(
        "game_id", "player_id", "team_id", "season", "game_date"
    )
    a = PS.prod_state_rows(box, q)
    nonsense = box.with_columns(
        pl.when(pl.col("game_date") >= D).then(47.9).otherwise(pl.col("minutes")).alias("minutes"),
        pl.when(pl.col("game_date") >= D).then(99).otherwise(pl.col("pts")).alias("pts"),
        pl.when(pl.col("game_date") >= D).then(True).otherwise(pl.col("starter")).alias("starter"),
    )
    b = PS.prod_state_rows(nonsense, q)
    for c in PS.ALL_STATE_COLS:
        assert np.allclose(a[c].to_numpy(), b[c].to_numpy(), atol=1e-12, equal_nan=True), c
    prev = sorted(box["game_date"].unique().to_list())[199]
    edited = box.with_columns(
        pl.when(pl.col("game_date") == prev)
        .then(50.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    c2 = PS.prod_state_rows(edited, q)
    assert not np.allclose(
        a["min5"].to_numpy(), c2["min5"].to_numpy(), equal_nan=True
    )  # the control can move


def test_player_with_no_history_has_zero_prior_and_nan_state() -> None:
    gdf, pdf, _ = _league()
    box = _box(pdf, gdf)
    q = pl.DataFrame(
        {
            "game_id": ["x"],
            "player_id": [999_999],
            "team_id": [1],
            "season": [2022],
            "game_date": [dt.date(2023, 1, 1)],
        }
    )
    out = PS.prod_state_rows(box, q)
    assert out["n_prior"][0] == 0.0 and np.isnan(out["min5"][0]) and np.isnan(out["gap_days"][0])
