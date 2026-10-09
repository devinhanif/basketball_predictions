"""T-30 ("lineups known") feature builder: values, as-of discipline, DNP handling, and the
guarantee that the production (flag-off) path is unchanged and never sees tonight's starters."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import (
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    stat_feature_names,
)
from nba.props.lineup_features import T30_FEATURES, build_lineup_features
from tests.props.test_context_residual import ELO, _league


def _mini() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Team 1, 7 games. Games 1-5 start {1..5}; game 6 starts {1,2,3,4,6} and player 5 is a DNP
    row; game 7 (next season) starts {1..5} again."""
    games, rows = [], []
    for i in range(1, 8):
        season = 2022 if i < 7 else 2023
        d = dt.date(2022, 11, 1) + dt.timedelta(days=2 * i) if i < 7 else dt.date(2023, 11, 1)
        games.append((f"G{i}", d, season, 1, 2, 100, 100))
        starters = {1, 2, 3, 4, 6} if i == 6 else {1, 2, 3, 4, 5}
        for p in range(1, 8):
            dnp = i == 6 and p == 5
            mins = None if dnp else (30.0 if p in starters else 10.0)
            rows.append((f"G{i}", p, 1, mins, 5, 2, 1, 0, p in starters))
    g = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )
    p = pl.DataFrame(
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
    return g, p


def _row(df: pl.DataFrame, g: str, p: int) -> dict[str, float | None]:
    return df.filter((pl.col("game_id") == g) & (pl.col("player_id") == p)).row(0, named=True)


def test_values_dnp_and_season_scope() -> None:
    g, p = _mini()
    f = build_lineup_features(g, p)
    assert f.height == p.height
    # first game of the season: no previous game, no window
    r1 = _row(f, "G1", 1)
    assert r1["t30_n_changed"] is None and r1["t30_n_usual_absent"] is None
    assert r1["t30_start_delta"] is None
    # steady lineup after a 3-game window exists
    r5 = _row(f, "G5", 1)
    assert r5["t30_n_changed"] == 0 and r5["t30_n_usual_absent"] == 0
    # game 6: player 6 replaces player 5, who is a DNP row (minutes NULL)
    r6 = _row(f, "G6", 6)
    assert r6["t30_starter"] == 1.0 and r6["t30_start_delta"] == 1.0
    assert r6["t30_n_changed"] == 1 and r6["t30_n_usual_absent"] == 1
    assert r6["t30_vac_starter_min"] == 30.0  # mean over the games player 5 played
    dnp = _row(f, "G6", 5)
    assert dnp["t30_starter"] == 0.0 and dnp["t30_start_delta"] == -1.0
    # new season: team features NULL again, player delta NULL (season scope)
    r7 = _row(f, "G7", 5)
    assert r7["t30_n_changed"] is None and r7["t30_start_delta"] is None


def test_future_games_never_change_earlier_t30_features() -> None:
    g, p = _mini()
    base = build_lineup_features(g, p)
    mut = p.with_columns(
        pl.when(pl.col("game_id").is_in(["G6", "G7"]))
        .then(pl.col("player_id") > 3)  # planted future: totally different starters
        .otherwise(pl.col("starter"))
        .alias("starter"),
        pl.when(pl.col("game_id").is_in(["G6", "G7"]))
        .then(99.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    )
    new = build_lineup_features(g, mut)
    before = ["G1", "G2", "G3", "G4", "G5"]
    a = base.filter(pl.col("game_id").is_in(before))
    b = new.filter(pl.col("game_id").is_in(before))
    assert a.equals(b)


def test_scratch_contamination_is_deterministic_and_rate_bounded() -> None:
    g, p = _mini()
    a = build_lineup_features(g, p, scratch_prob=0.5, seed=3)
    b = build_lineup_features(g, p, scratch_prob=0.5, seed=3)
    assert a.equals(b)
    clean = build_lineup_features(g, p)
    assert a["t30_starter"].sum() < clean["t30_starter"].sum()
    assert build_lineup_features(g, p, scratch_prob=0.0).equals(clean)


def test_flag_off_is_byte_identical_and_never_reads_tonights_starters() -> None:
    gdf, pdf, static = _league()
    flagged, _ = flagged_from_availability(
        pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "status": pl.Utf8,
                "as_of": pl.Datetime("us"),
                "source": pl.Utf8,
            }
        ),
        gdf,
        ContextResidualConfig().report,
    )
    off = build_features(gdf, pdf, static, flagged, ELO)
    off2 = build_features(gdf, pdf, static, flagged, ELO, lineups_known=False)
    assert off.equals(off2) and not [c for c in off.columns if c.startswith("t30_")]
    on = build_features(gdf, pdf, static, flagged, ELO, lineups_known=True)
    assert set(T30_FEATURES) <= set(on.columns)
    # the T-30 columns are the ONLY difference: flag-on minus t30 == flag-off
    assert on.drop(list(T30_FEATURES)).equals(off)
    assert stat_feature_names("pts") == stat_feature_names("pts", lineups_known=False)
    assert not any(n.startswith("t30_") for n in stat_feature_names("pts"))
    assert stat_feature_names("pts", lineups_known=True)[-5:] == list(T30_FEATURES)
    # planted tonight: flipping every starter flag of one game changes nothing off-path
    gid = gdf.sort(["game_date", "game_id"])["game_id"][100]
    pm = pdf.with_columns(
        pl.when(pl.col("game_id") == gid)
        .then(~pl.col("starter"))
        .otherwise(pl.col("starter"))
        .alias("starter")
    )
    off_m = build_features(gdf, pm, static, flagged, ELO)
    cur = off.filter(pl.col("game_id") == gid).drop("last_starter")
    cur_m = off_m.filter(pl.col("game_id") == gid).drop("last_starter")
    assert cur.equals(cur_m)  # tonight's flag is invisible to the production features
    on_m = build_features(gdf, pm, static, flagged, ELO, lineups_known=True)
    a = on.filter(pl.col("game_id") == gid)["t30_starter"].to_numpy()
    b = on_m.filter(pl.col("game_id") == gid)["t30_starter"].to_numpy()
    assert not np.array_equal(a, b)  # ...and only visible under the T-30 flag
