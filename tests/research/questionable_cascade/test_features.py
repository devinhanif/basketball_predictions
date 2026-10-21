# ruff: noqa: E501
"""QUESTIONABLE_CASCADE features: reason classes, the snapshot's known-at rule, as-of rotation, self removal, the production OUT tie, NULL pattern."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import vacated_features
from research.eval import questionable_cascade_features as F

D0 = dt.date(2023, 11, 1)
N_GAMES = 8
PLAYERS = {1: (10, 11, 12, 13), 2: (20, 21, 22, 23)}  # team -> players


def world(minutes: dict[int, float] | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    minutes = minutes or {}
    games = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(N_GAMES)],
            "game_date": [D0 + dt.timedelta(days=2 * i) for i in range(N_GAMES)],
            "season": [2023] * N_GAMES,
            "home_team": [1] * N_GAMES,
            "away_team": [2] * N_GAMES,
        }
    ).with_columns(
        (pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(hours=19)).alias("tip")
    )
    rows = []
    for i in range(N_GAMES):
        for t, ps in PLAYERS.items():
            for p in ps:
                rows.append(
                    {
                        "game_id": f"g{i}",
                        "player_id": p,
                        "team_id": t,
                        "minutes": minutes.get(p, 30.0),
                        "pts": 15,
                        "reb": 5,
                        "ast": 4,
                        "fg3m": 2,
                        "starter": True,
                    }
                )
    return games, pl.DataFrame(rows)


def report(rows: list[tuple[str, int, str, str, dt.datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "reason": pl.Utf8,
            "as_of": pl.Datetime("us"),
        },
        orient="row",
    )


def tip(i: int) -> dt.datetime:
    return dt.datetime.combine(D0 + dt.timedelta(days=2 * i), dt.time(19, 0))


def test_reason_class() -> None:
    assert F.reason_class("Injury/Illness-LeftAnkle;Sprain") == "injury"
    assert F.reason_class("Injury/Illness - Left Ankle; Sprain") == "injury"
    assert F.reason_class("GLeague-Two-Way") == "gleague"
    assert F.reason_class("G League - Two-Way") == "gleague"
    assert F.reason_class("GLeague-OnAssignment") == "gleague"
    assert F.reason_class("Rest") == "rest"
    assert F.reason_class("PersonalReasons") == "personal"
    assert F.reason_class("Not With Team") == "other"
    assert F.reason_class(None) == "other"


def test_snapshot_ignores_rows_after_the_cutoff_and_uses_only_the_latest_usable() -> None:
    games, _ = world()
    t = tip(7)
    rep = report(
        [
            ("g7", 10, "questionable", "Injury/Illness-Knee", t - dt.timedelta(hours=5)),
            ("g7", 11, "out", "Injury/Illness-Knee", t - dt.timedelta(hours=5)),
            # the later usable snapshot lists only player 10 (11 is no longer on the report)
            ("g7", 10, "doubtful", "Injury/Illness-Knee", t - dt.timedelta(hours=3)),
            # PLANTED FUTURE: stamped 30 minutes before tip, after the T-60 cutoff
            ("g7", 12, "out", "Injury/Illness-Knee", t - dt.timedelta(minutes=30)),
            ("g7", 10, "out", "Injury/Illness-Knee", t - dt.timedelta(minutes=30)),
        ]
    )
    snap = F.snapshot_with_reason(rep, games)
    got = {(g, p): s for g, p, s in snap.select("game_id", "player_id", "status").iter_rows()}
    assert got == {("g7", 10): "doubtful"}
    assert snap["lead_min"].to_list() == [180.0]
    # exactly at the cutoff is usable; one minute later is not
    at = report([("g7", 10, "questionable", "x", t - dt.timedelta(minutes=60))])
    assert F.snapshot_with_reason(at, games).height == 1
    late = report([("g7", 10, "questionable", "x", t - dt.timedelta(minutes=59))])
    assert F.snapshot_with_reason(late, games).height == 0


def test_rotation_and_evac_columns_with_self_removal() -> None:
    games, pgs = world({10: 34.0, 11: 20.0, 13: 5.0})  # 13 is not a rotation player (min40 < 12)
    t = tip(7)
    rep = report(
        [
            ("g7", 10, "questionable", "Injury/Illness-Knee", t - dt.timedelta(hours=3)),
            ("g7", 11, "doubtful", "Rest", t - dt.timedelta(hours=3)),
            ("g7", 13, "questionable", "Injury/Illness-Knee", t - dt.timedelta(hours=3)),
            ("g7", 20, "probable", "Injury/Illness-Knee", t - dt.timedelta(hours=3)),
        ]
    )
    snap = F.snapshot_with_reason(rep, games)
    played, states = F.played_states(games, pgs)
    tagged = F.tagged_table(played, states, snap, games)
    assert set(zip(tagged["player_id"], tagged["status"], strict=True)) == {
        (10, "questionable"),
        (11, "doubtful"),
        (20, "probable"),
    }
    r = {"questionable": 0.5, "doubtful": 0.0}
    rows = pl.DataFrame(
        {"game_id": ["g7"] * 4, "team_id": [1, 1, 1, 2], "player_id": [10, 11, 12, 21]}
    )
    cols = F.build_columns(rows, snap, tagged, r, {"g7"}).sort("player_id")
    by = {int(p): row for p, row in zip(cols["player_id"], cols.iter_rows(named=True), strict=True)}
    # row 12 (untagged teammate): both 10 (q, 34 min, weight 0.5) and 11 (d, 20 min, weight 1.0) count
    assert by[12]["qc_nq"] == 1 and by[12]["qc_nd"] == 1
    assert by[12]["qc_evac_min"] == pytest.approx(0.5 * 34.0 + 1.0 * 20.0, abs=0.05)
    assert by[12]["qc_status"] == 0.0 and by[12]["qc_reason"] == 0.0
    # row 10 (himself questionable): self removed, only 11 counts
    assert by[10]["qc_nq"] == 0 and by[10]["qc_nd"] == 1
    assert by[10]["qc_evac_min"] == pytest.approx(20.0, abs=0.05)
    assert by[10]["qc_status"] == 3.0 and by[10]["qc_reason"] == 1.0
    assert by[11]["qc_status"] == 4.0 and by[11]["qc_reason"] == 3.0
    # the other team has nobody questionable or doubtful: explicit zeros, not NULL
    assert by[21]["qc_nq"] == 0 and by[21]["qc_evac_min"] == 0.0
    # a game with no usable snapshot is NULL in every column (never "nobody listed")
    none = F.build_columns(rows, snap, tagged, r, set())
    assert all(none[c].null_count() == none.height for c in F.C2_FEATURES)


def test_tagged_minutes_are_as_of_not_tonight() -> None:
    games, pgs = world({10: 30.0})
    t = tip(7)
    rep = report([("g7", 10, "questionable", "x", t - dt.timedelta(hours=3))])
    snap = F.snapshot_with_reason(rep, games)
    p0, s0 = F.played_states(games, pgs)
    a = F.tagged_table(p0, s0, snap, games)["min10"].to_list()
    # change what player 10 does ON and AFTER the game: the as-of value must not move
    pgs2 = pgs.with_columns(
        pl.when((pl.col("player_id") == 10) & (pl.col("game_id") == "g7"))
        .then(48.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    p1, s1 = F.played_states(games, pgs2)
    b = F.tagged_table(p1, s1, snap, games)["min10"].to_list()
    assert a == b


def test_out_set_reproduces_production_vacancy() -> None:
    games, pgs = world({10: 34.0, 11: 25.0})
    t = tip(7)
    rep = report(
        [
            ("g7", 10, "out", "x", t - dt.timedelta(hours=3)),
            ("g7", 11, "out", "x", t - dt.timedelta(hours=3)),
            ("g7", 21, "out", "x", t - dt.timedelta(hours=3)),
            ("g7", 12, "questionable", "x", t - dt.timedelta(hours=3)),
        ]
    )
    snap = F.snapshot_with_reason(rep, games)
    played, states = F.played_states(games, pgs)
    mine = (
        F.tagged_table(played, states, snap, games, ("out",))
        .group_by(["game_id", "team_id"])
        .agg(pl.len().alias("n"), pl.col("min10").sum().alias("v"))
    )
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    prod = vacated_features(played, states, {"g7": {10, 11, 21}}, info)
    j = prod.join(mine, on=["game_id", "team_id"], how="full", coalesce=True)
    assert j.height == 2
    assert (j["n_out_rot"].cast(pl.Int64) == j["n"].cast(pl.Int64)).all()
    assert np.allclose(j["vac_min"].to_numpy(), j["v"].to_numpy(), atol=1e-9)


def test_play_rates_and_fallback() -> None:
    games, pgs = world()
    pgs = pgs.filter(
        ~((pl.col("game_id") == "g7") & (pl.col("player_id") == 11))
    )  # 11 does not play in g7
    t = tip(7)
    rep = report(
        [
            ("g7", 10, "questionable", "x", t - dt.timedelta(hours=3)),
            ("g7", 11, "questionable", "x", t - dt.timedelta(hours=3)),
        ]
    )
    snap = F.snapshot_with_reason(rep, games)
    played, states = F.played_states(games, pgs)
    tagged = F.tagged_table(played, states, snap, games)
    rates = F.play_rates(tagged, pgs, {f"g{i}": 2023 for i in range(N_GAMES)}, 2023)
    assert rates["questionable"] == {"rate": 0.5, "n": 2.0}
    r = F.rates_for_cascade(rates)
    assert r["questionable"] == 0.5 and r["doubtful"] == F.FALLBACK_RATES["doubtful"]
