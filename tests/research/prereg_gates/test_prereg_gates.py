# ruff: noqa: E501
"""Minimum-data gates of 2026-10-12 (RETURNING_TEAMMATE, EXPECTED_ABSENCE, ROSTER_CONTINUITY, QUESTIONABLE_STREAK):
as-of correctness of the new feature builders (a planted future must not change a feature; a planted past must),
the T-60 snapshot rule, the sizing arithmetic, and the Elo replay equivalence with production's replay."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from research.eval import expected_absence_gate as EA
from research.eval import prereg_gates_common as C
from research.eval import questionable_streak_gate as QS
from research.eval import returning_teammate_gate as RT
from research.eval import roster_continuity_gate as RC

D0 = dt.date(2023, 11, 1)


def _games(n: int, team: int = 1, opp: int = 2) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [f"00223{i:05d}" for i in range(n)],
            "game_date": [D0 + dt.timedelta(days=2 * i) for i in range(n)],
            "season": [2023] * n,
            "home_team": [team] * n,
            "away_team": [opp] * n,
            "tip": [
                dt.datetime.combine(D0 + dt.timedelta(days=2 * i), dt.time(19, 0)) for i in range(n)
            ],
        }
    )


def _box(games: pl.DataFrame, mins: dict[int, list[float | None]], team: int = 1) -> pl.DataFrame:
    rows = []
    for pid, ms in mins.items():
        for (gid, d), m in zip(games.select("game_id", "game_date").rows(), ms, strict=True):
            rows.append((gid, team, pid, m, d, 2023))
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "minutes": pl.Float64,
            "game_date": pl.Date,
            "season": pl.Int64,
        },
        orient="row",
    )


def _status(rows: list[tuple[str, int, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema={"game_id": pl.Utf8, "player_id": pl.Int64, "status": pl.Utf8}, orient="row"
    )


# ---------------------------------------------------------------- common


def test_latest_t60_ignores_snapshots_after_the_cutoff_and_uses_only_the_latest_usable() -> None:
    g = _games(1)
    gid = g["game_id"][0]
    tip = g["tip"][0]
    rep = pl.DataFrame(
        {
            "game_id": [gid, gid, gid, gid],
            "player_id": [7, 7, 8, 9],
            "status": ["questionable", "out", "out", "doubtful"],
            "as_of": [
                tip - dt.timedelta(hours=5),
                tip - dt.timedelta(minutes=60),
                tip - dt.timedelta(hours=5),
                tip - dt.timedelta(minutes=30),
            ],
        }
    )
    s = C.latest_t60(rep, g)
    got = {p: st for p, st in s.select("player_id", "status").rows()}
    assert got == {
        7: "out"
    }  # 8 only on an earlier snapshot (unlisted on the latest); 9 stamped after tip - 60


def test_query_refuses_sql_without_the_season_cap() -> None:
    with pytest.raises(RuntimeError):
        C.query(None, "SELECT * FROM games")  # type: ignore[arg-type]


def test_sizing_helpers() -> None:
    rng = np.random.default_rng(0)
    d = rng.normal(size=4000)
    st = C.cluster_stats(d, np.arange(4000))
    assert abs(st["deff"] - 1.0) < 1e-9
    clustered = C.cluster_stats(np.repeat(rng.normal(size=1000), 4), np.repeat(np.arange(1000), 4))
    assert clustered["deff"] > 3.5
    q = np.tile(np.arange(0, 199, dtype=float) / 10, (5, 1))
    assert np.allclose(C.expected_crps_gain(q, q), 0.0)
    assert (C.expected_crps_gain(q, q + 3.0) > 0).all()
    b = C.bernoulli_gain(np.array([0.5, 0.3]), np.array([0.5, 0.6]))
    assert b["kl"][0] == 0.0 and b["kl"][1] > 0
    r = C.sizing_row("x", 0.3, 1.0, 30000, -0.005, -0.01)
    assert r["hw_le_floor"] and r["power_one_season"] > 0.9
    wide = C.sizing_row("x", 0.3, 1.0, 1000, -0.005, -0.01)
    assert wide["power_one_season"] < C.pass_power(
        -0.01, -0.005, wide["se"]
    )  # the CI condition bites when hw > |floor|


# ---------------------------------------------------------------- RETURNING_TEAMMATE

# player 10 = the returner (30 min, misses games 2-4), 11 and 12 = teammates
MINS: dict[int, list[float | None]] = {
    10: [32, 34, None, None, None, 30, 31],
    11: [20, 22, 28, 30, 29, 21, 20],
    12: [10, 12, 15, 14, 16, 11, 9],
}


def _rt_setup(
    mins: dict[int, list[float | None]] = MINS,
    tonight: str | None = "questionable",
    prev_listed: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    g = _games(7)
    box = _box(g, mins)
    tg = C.team_game_index(box, g)
    gids = g["game_id"].to_list()
    st = [(gids[2], 10, "out"), (gids[3], 10, "out")]
    if prev_listed:
        st.append((gids[4], 10, "out"))
    if tonight:
        st.append((gids[5], 10, tonight))
    return g, box, tg, _status(st)


def _feat(f: pl.DataFrame, gid: str, pid: int) -> dict[str, float]:
    r = f.filter((pl.col("game_id") == gid) & (pl.col("player_id") == pid))
    return {k: r[k][0] for k in RT.FEATURES}


def test_returning_flags_the_listed_returner_and_values_his_pre_absence_minutes() -> None:
    g, box, tg, st = _rt_setup()
    f, r = RT.build_returning(box, tg, st)
    gid5 = g["game_id"][5]
    assert r.filter(pl.col("game_id") == gid5)["returner_id"].to_list() == [10]
    x = _feat(f, gid5, 11)
    assert (
        x["ret_min"] == pytest.approx(33.0)
        and x["ret_min_star"] == pytest.approx(33.0)
        and x["n_ret"] == 1
    )
    # 11's window minutes (games 2-4: 28, 30, 29) minus his pre-window mean (20, 22)
    assert x["ret_own_gain"] == pytest.approx(29.0 - 21.0)
    assert _feat(f, gid5, 10)["ret_min"] == 0.0  # the returner never counts himself
    assert _feat(f, g["game_id"][4], 11)["ret_min"] == 0.0  # streak is 2 before game 4
    assert _feat(f, g["game_id"][6], 11)["ret_min"] == 0.0  # he played game 5: no longer returning


@pytest.mark.parametrize("tonight", ["out", "doubtful"])
def test_returning_excludes_out_and_doubtful_tonight(tonight: str) -> None:
    g, box, tg, st = _rt_setup(tonight=tonight)
    f, _ = RT.build_returning(box, tg, st)
    assert _feat(f, g["game_id"][5], 11)["ret_min"] == 0.0


def test_returning_variant_b_needs_the_previous_game_listing_variant_a_does_not() -> None:
    g, box, tg, st = _rt_setup(prev_listed=False)
    assert _feat(RT.build_returning(box, tg, st)[0], g["game_id"][5], 11)["ret_min"] == 0.0
    assert _feat(RT.build_returning(box, tg, st, require_listed=False)[0], g["game_id"][5], 11)[
        "ret_min"
    ] == pytest.approx(33.0)


def test_returning_drops_a_player_who_played_for_another_team_since() -> None:
    g, box, tg, st = _rt_setup()
    other = pl.DataFrame(
        {
            "game_id": ["0022399999"],
            "team_id": [3],
            "player_id": [10],
            "minutes": [25.0],
            "game_date": [D0 + dt.timedelta(days=7)],
            "season": [2023],
        }
    )
    tg2 = C.team_game_index(
        pl.concat([box, other]),
        pl.concat(
            [
                g,
                pl.DataFrame(
                    {
                        "game_id": ["0022399999"],
                        "game_date": [D0 + dt.timedelta(days=7)],
                        "season": [2023],
                        "home_team": [3],
                        "away_team": [4],
                        "tip": [dt.datetime(2023, 11, 8, 19)],
                    }
                ),
            ]
        ),
    )
    f, _ = RT.build_returning(pl.concat([box, other]), tg2, st)
    assert _feat(f, g["game_id"][5], 11)["ret_min"] == 0.0


def test_returning_planted_future_changes_nothing_and_a_planted_past_does() -> None:
    g, box, tg, st = _rt_setup()
    gid5 = g["game_id"][5]
    base = _feat(RT.build_returning(box, tg, st)[0], gid5, 11)
    fut = {p: [*m[:5], 48.0, 0.0] for p, m in MINS.items()}  # tonight's and later minutes rewritten
    st_fut = pl.concat([st, _status([(g["game_id"][6], 11, "out"), (gid5, 12, "questionable")])])
    assert _feat(RT.build_returning(_box(g, fut), tg, st_fut)[0], gid5, 11) == base
    past = {
        **MINS,
        10: [12.0, 14.0, None, None, None, 30.0, 31.0],
    }  # control: the returner's past minutes
    moved = _feat(RT.build_returning(_box(g, past), tg, st)[0], gid5, 11)
    assert moved["ret_min"] == pytest.approx(13.0) and moved["ret_min_star"] == 0.0


# ---------------------------------------------------------------- QUESTIONABLE_STREAK


def test_questionable_streak_counts_consecutive_listed_and_played_games_only() -> None:
    g = _games(6)
    gids = g["game_id"].to_list()
    box = _box(g, {7: [30, 28, 0, 31, 29, 30]})
    tg = C.team_game_index(box, g)
    t60 = _status(
        [
            (gids[1], 7, "questionable"),
            (gids[2], 7, "questionable"),
            (gids[3], 7, "questionable"),
            (gids[4], 7, "questionable"),
            (gids[5], 7, "questionable"),
        ]
    )
    first = pl.DataFrame({"game_id": [gids[5]], "player_id": [7], "first_status": ["doubtful"]})
    q = pl.DataFrame({"game_id": gids, "player_id": [7] * 6, "team_id": [1] * 6})
    f = QS.build_questionable_streak(q, tg, t60, first, box)
    assert f["q_streak"].to_list() == [
        0,
        0,
        1,
        0,
        1,
        2,
    ]  # game 2 he sat (0 min): the streak restarts after it
    assert f["q_change"].to_list() == [
        0,
        0,
        0,
        0,
        0,
        -1,
    ]  # doubtful earlier in the day -> questionable at T-60


def test_questionable_streak_planted_future_changes_nothing_and_a_planted_past_does() -> None:
    g = _games(6)
    gids = g["game_id"].to_list()
    box = _box(g, {7: [30, 28, 25, 31, 29, 30]})
    tg = C.team_game_index(box, g)
    t60 = _status([(gids[k], 7, "questionable") for k in range(1, 6)])
    first = pl.DataFrame(
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "first_status": pl.Utf8}
    )
    q = pl.DataFrame({"game_id": [gids[4]], "player_id": [7], "team_id": [1]})
    base = QS.build_questionable_streak(q, tg, t60, first, box)["q_streak"][0]
    assert base == 3
    fut_box = _box(g, {7: [30, 28, 25, 31, 0, 0]})
    fut_st = pl.concat([t60.filter(pl.col("game_id") != gids[5]), _status([(gids[5], 7, "out")])])
    assert QS.build_questionable_streak(q, tg, fut_st, first, fut_box)["q_streak"][0] == base
    past_box = _box(g, {7: [30, 28, 0, 31, 29, 30]})
    assert QS.build_questionable_streak(q, tg, t60, first, past_box)["q_streak"][0] == 1


def test_first_listed_status_uses_only_usable_snapshots() -> None:
    g = _games(1)
    gid, tip = g["game_id"][0], g["tip"][0]
    rep = pl.DataFrame(
        {
            "game_id": [gid, gid],
            "player_id": [7, 7],
            "status": ["out", "questionable"],
            "as_of": [tip - dt.timedelta(minutes=10), tip - dt.timedelta(hours=3)],
        }
    )
    assert QS.first_listed_status(rep, g)["first_status"].to_list() == ["questionable"]


# ---------------------------------------------------------------- EXPECTED_ABSENCE


def test_expected_absence_arithmetic_and_sides() -> None:
    games = pl.DataFrame({"game_id": ["g1"], "home_team": [1], "away_team": [2]})
    listed = pl.DataFrame(
        {
            "game_id": ["g1"] * 4,
            "player_id": [1, 2, 3, 4],
            "status": ["out", "questionable", "doubtful", "questionable"],
            "team_id": [1, 1, 2, 2],
            "value": [10.0, 5.0, 4.0, 2.0],
        }
    )
    pp = pl.DataFrame({"game_id": ["g1", "g1"], "player_id": [2, 3], "p": [0.6, 0.1]})
    r = EA.expected_absence(listed, games, pp).row(0, named=True)
    assert r["d_out"] == pytest.approx(-1.0)  # home OUT value 10 -> away-minus-home -1.0
    assert r["d_doubt"] == pytest.approx(0.4)
    # home: 10 * 1 + 5 * 0.4 = 12; away: 4 * 0.9 + 2 * (1 - 0.55 fallback) = 4.5
    assert r["d_exp"] == pytest.approx((4.5 - 12.0) / 10.0)
    assert r["n_fallback"] == 1


# ---------------------------------------------------------------- ROSTER_CONTINUITY


def _elo_games() -> pl.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    d = dt.date(2022, 10, 20)
    for season in (2022, 2023):
        for i in range(60):
            h, a = rng.choice(6, size=2, replace=False)
            hp = int(rng.integers(90, 125))
            rows.append(
                (
                    f"{season}{i:04d}",
                    d + dt.timedelta(days=i // 3 + 400 * (season - 2022)),
                    season,
                    int(h) + 1,
                    int(a) + 1,
                    hp,
                    hp + int(rng.integers(-15, 15)) or hp + 1,
                )
            )
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "season": pl.Int64,
            "home_team": pl.Int64,
            "away_team": pl.Int64,
            "home_pts": pl.Int64,
            "away_pts": pl.Int64,
        },
        orient="row",
    )


def test_elo_replay_with_constant_carry_equals_production_replay() -> None:
    from nba.models.injury_elo import sequential_elo_logits

    params = {
        "k_factor": 9.5,
        "home_advantage_elo": 40.0,
        "season_carryover": 0.61,
        "mov_c": 3.7,
        "mov_div": 0.00067,
    }
    g = _elo_games()
    a = sequential_elo_logits(g, params)
    b = RC.elo_replay(g, params, lambda t, s: params["season_carryover"])
    assert np.array_equal(a, b)
    c = RC.elo_replay(g, params, lambda t, s: 0.9 if t == 1 else params["season_carryover"])
    s2 = g["season"].to_numpy() == 2023
    assert np.array_equal(a[~s2], c[~s2]) and not np.array_equal(a[s2], c[s2])


def test_continuity_share_counts_previous_season_minutes_for_the_same_team() -> None:
    box = pl.DataFrame(
        {
            "game_id": ["a", "a", "a", "b"],
            "team_id": [1, 1, 1, 2],
            "player_id": [1, 2, 3, 4],
            "minutes": [30.0, 10.0, 20.0, 40.0],
            "season": [2022] * 4,
        }
    )
    roster = pl.DataFrame({"team_id": [1, 1, 1], "season": [2023] * 3, "player_id": [1, 4, 9]})
    out = RC.continuity_share(box, pl.DataFrame(), roster)
    assert out["continuity"][0] == pytest.approx(30.0 / 60.0)  # player 4's minutes were for team 2


# ---------------------------------------------------------------- B2B_ELO


def test_b2b_flags_previous_calendar_day_only_and_ignores_the_future() -> None:
    from research.eval import b2b_elo_gate as BE

    d = dt.date(2023, 12, 1)
    g = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "game_date": [d, d + dt.timedelta(days=1), d + dt.timedelta(days=3)],
            "home_team": [1, 2, 1],
            "away_team": [3, 1, 2],
        }
    )
    base = BE.build_b2b(g)
    rows = {
        r["game_id"]: (r["b2b_home"], r["b2b_away"], r["b2b_diff"])
        for r in base.iter_rows(named=True)
    }
    assert rows == {
        "a": (0, 0, 0),
        "b": (0, 1, -1),
        "c": (0, 0, 0),
    }  # team 1 on the 2nd night in b; c is 2 days later
    future = pl.concat(
        [
            g,
            pl.DataFrame(
                {
                    "game_id": ["z"],
                    "game_date": [d + dt.timedelta(days=2)],
                    "home_team": [2],
                    "away_team": [3],
                }
            ),
        ]
    )
    got = BE.build_b2b(future).filter(pl.col("game_id").is_in(["a", "b"])).sort("game_id")
    assert got.equals(
        base.filter(pl.col("game_id").is_in(["a", "b"])).sort("game_id")
    )  # a later game changes no earlier flag
    assert (
        BE.build_b2b(future).filter(pl.col("game_id") == "c")["b2b_away"][0] == 1
    )  # control: the planted day-before game flags c


# ---------------------------------------------------------------- ERROR_FEEDBACK


def test_error_feedback_uses_only_strictly_earlier_dates_and_moves_with_the_past() -> None:
    from research.eval import error_feedback_gate as EF

    pid = np.array([5, 5, 5, 5, 5, 6])
    date = np.array(
        ["2023-11-01", "2023-11-03", "2023-11-05", "2023-11-05", "2023-11-07", "2023-11-05"],
        dtype="datetime64[D]",
    )
    res = np.array([1.0, 3.0, -2.0, 10.0, 4.0, 7.0])
    pit = np.array([0.6, 0.8, 0.2, 0.99, 0.7, 0.9])
    f = EF.build_error_feedback(pid, date, res, pit)
    assert np.isnan(f["ef_res_l3"][0]) and f["ef_n"][0] == 0
    assert f["ef_res_l3"][2] == pytest.approx(2.0) and f["ef_res_l3"][3] == pytest.approx(
        2.0
    )  # same-date rows never feed each other
    assert f["ef_res_l3"][4] == pytest.approx((3.0 - 2.0 + 10.0) / 3) and f["ef_n"][4] == 4
    assert np.isnan(f["ef_res_l3"][5])  # another player's rows never enter
    fut = res.copy()
    fut[2:5] = [99.0, -99.0, 50.0]  # tonight (D = 11-05) and later rewritten
    g = EF.build_error_feedback(pid, date, fut, pit)
    for k in g:
        assert np.allclose(g[k][:4], f[k][:4], equal_nan=True)
    past = res.copy()
    past[0] = -5.0  # control: an earlier residual moves the feature
    assert EF.build_error_feedback(pid, date, past, pit)["ef_res_l3"][2] == pytest.approx(-1.0)


def test_mid_pit_on_an_integer_grid() -> None:
    from research.eval import error_feedback_gate as EF

    q = np.array([[0.0, 1.0, 2.0, 3.0]])
    assert EF.mid_pit(q, np.array([2.0]))[0] == pytest.approx(0.5 * (0.5 + 0.75))
