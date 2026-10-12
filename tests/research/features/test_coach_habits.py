# ruff: noqa: E501
"""COACH_PHASE features: as-of, strict-earlier, planted-future, placebo (research/features/coach_habits.py)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from research.features import coach_habits as CH

BOS, NYK, LAL, DEN = 1610612738, 1610612752, 1610612747, 1610612743


def _games(rows: list[tuple[str, dt.date, int, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "game_id": g,
                "game_date": d,
                "season": 2023,
                "home_team": h,
                "away_team": a,
                "home_pts": hp,
                "away_pts": ap,
            }
            for g, d, h, a, hp, ap in rows
        ]
    )


def _box(game_ids: list[str], teams: dict[str, tuple[int, int]], mins: list[float]) -> pl.DataFrame:
    rows = []
    for g in game_ids:
        for t in teams[g]:
            for i, m in enumerate(mins):
                rows.append(
                    {
                        "game_id": g,
                        "team_id": t,
                        "player_id": t * 100 + i,
                        "minutes": m,
                        "starter": i < 5,
                    }
                )
    return pl.DataFrame(rows)


MINS = [36.0, 34.0, 32.0, 30.0, 28.0, 20.0, 15.0, 12.0, 8.0, 5.0]


def test_team_game_inputs_values() -> None:
    g = _games([("0022300001", dt.date(2023, 10, 24), BOS, NYK, 110, 90)])
    box = _box(["0022300001"], {"0022300001": (BOS, NYK)}, MINS)
    x = CH.team_game_inputs(box, g).filter(pl.col("team_id") == BOS).row(0, named=True)
    tot = sum(MINS)
    assert x["depth"] == 8.0  # 36,34,32,30,28,20,15,12 are >= 10
    assert x["top5"] == pytest.approx(sum(MINS[:5]) / tot)
    assert x["top8"] == pytest.approx(sum(MINS[:8]) / tot)
    assert x["starter_min"] == pytest.approx(np.mean(MINS[:5]))
    assert x["bench_share"] == pytest.approx(sum(MINS[5:]) / tot)
    assert x["abs_margin"] == 20.0


def test_previous_game_coach_shift() -> None:
    tg = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "team_id": [BOS, BOS, BOS],
            "game_date": [dt.date(2023, 10, 24), dt.date(2023, 10, 26), dt.date(2023, 10, 28)],
            "coach_key": ["x", "x", "y"],
        }
    )
    p = CH.previous_game_coach(tg).sort("game_id")
    assert p["prev_coach"].to_list() == [
        None,
        "x",
        "x",
    ]  # a new coach's first game gets the predecessor


def _inputs(rows: list[tuple[str, dt.date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "game_id": g,
                "team_id": BOS,
                "game_date": d,
                "coach_key": c,
                "depth": v,
                "top5": 0.6,
                "top8": 0.85,
                "starter_min": 30.0,
                "bench_share": 0.3,
                "top5_min": 150.0,
                "abs_margin": 5.0,
            }
            for g, d, c, v in rows
        ]
    )


def test_habit_lookup_is_shrunk_ewma_and_strictly_earlier() -> None:
    inp = _inputs(
        [
            ("0022300001", dt.date(2023, 10, 24), "c", 8.0),
            ("0022300002", dt.date(2023, 10, 26), "c", 10.0),
            ("0022300003", dt.date(2023, 10, 28), "c", 9.0),
        ]
    )
    t = CH.HabitTable(inp)
    h = t.lookup(["c"], [dt.date(2023, 10, 28)])
    # two earlier REG games, newest first weight 1, older weight DEC; league mean of the two earlier events = 9.0
    s = 10.0 + 8.0 * CH.DEC
    w = 1.0 + CH.DEC
    assert h["h_reg_depth"][0] == pytest.approx((s + CH.K_PSEUDO * 9.0) / (w + CH.K_PSEUDO))
    assert h["coach_n_reg"][0] == 2
    same_day = t.lookup(["c"], [dt.date(2023, 10, 24)])  # nothing earlier: pure league default
    assert same_day["coach_n_reg"][0] == 0
    assert same_day["h_reg_depth"][0] == pytest.approx(CH.DEFAULT_LEAGUE["depth"])
    # no playoff history: the PO habit equals the REG league fallback and the delta is the difference
    assert h["h_po_depth"][0] == pytest.approx(9.0)
    assert h["d_depth"][0] == pytest.approx(h["h_po_depth"][0] - h["h_reg_depth"][0])


def test_planted_future_does_not_move_earlier_lookups() -> None:
    base = [
        ("0022300001", dt.date(2023, 10, 24), "c", 8.0),
        ("0022300002", dt.date(2023, 10, 26), "c", 10.0),
    ]
    a = CH.HabitTable(_inputs(base)).lookup(
        ["c", "c"], [dt.date(2023, 10, 26), dt.date(2023, 10, 27)]
    )
    b = CH.HabitTable(_inputs([*base, ("0022300003", dt.date(2023, 11, 20), "c", 1.0)])).lookup(
        ["c", "c"], [dt.date(2023, 10, 26), dt.date(2023, 10, 27)]
    )
    assert a.equals(b)


def test_null_coach_gets_league_means() -> None:
    inp = _inputs([("0022300001", dt.date(2023, 10, 24), "c", 8.0)])
    h = CH.HabitTable(inp).lookup([None], [dt.date(2023, 11, 1)])
    assert h["coach_n_reg"][0] == 0
    assert h["h_reg_depth"][0] == pytest.approx(8.0)


def test_reliability_screen_stable_vs_noise() -> None:
    rng = np.random.default_rng(0)
    rows = []
    for c in range(12):
        base = 8 + c * 0.4
        for i in range(70):
            rows.append(
                {
                    "game_id": f"002220{c:02d}{i:03d}",
                    "team_id": c,
                    "game_date": dt.date(2022, 10, 20) + dt.timedelta(days=i),
                    "season": 2022,
                    "coach_key": f"k{c}",
                    "depth": base + rng.normal(0, 0.3),
                    "top5": rng.normal(0.6, 0.05),
                    "top8": 0.85,
                    "starter_min": 30.0,
                    "bench_share": 0.3,
                    "top5_min": 150.0 + rng.normal(0, 5),
                    "abs_margin": float(rng.integers(0, 30)),
                }
            )
    out = CH.reliability_screen(pl.DataFrame(rows))
    assert out["depth"]["keep"] and out["depth"]["corrected"] > 0.9
    assert out["top5"]["coaches"] == 12 and not out["top5"]["keep"]  # pure noise across coaches


def test_series_prev_game_and_placebo_map() -> None:
    g = _games(
        [
            ("0042300101", dt.date(2024, 4, 20), BOS, NYK, 1, 0),
            ("0042300102", dt.date(2024, 4, 22), BOS, NYK, 1, 0),
            ("0042300201", dt.date(2024, 4, 21), LAL, DEN, 1, 0),
        ]
    )
    p = CH.series_prev_game(g).sort("game_id")
    assert p["prev_game_id"].to_list() == [None, "0042300101", None]
    keys = pl.DataFrame({"season": [2023] * 4, "phase": ["PO"] * 4, "coach": ["a", "b", "c", "d"]})
    m = CH.placebo_map(keys, 0)
    assert sorted(m.values()) == ["a", "b", "c", "d"]  # a permutation within season and phase
    assert m == CH.placebo_map(keys, 0)  # deterministic


def test_team_game_features_columns_and_asof() -> None:
    games = _games(
        [
            ("0022300001", dt.date(2023, 10, 24), BOS, NYK, 100, 90),
            ("0022300002", dt.date(2023, 10, 26), LAL, DEN, 100, 90),
            ("0022300003", dt.date(2023, 10, 28), BOS, NYK, 100, 90),
            ("0042300101", dt.date(2024, 4, 20), BOS, NYK, 100, 90),
        ]
    )
    ids = games["game_id"].to_list()
    teams = {r["game_id"]: (r["home_team"], r["away_team"]) for r in games.iter_rows(named=True)}
    box = _box(ids, teams, MINS)
    coach = pl.DataFrame(
        [{"game_id": g, "team_id": t, "coach_key": f"c{t}"} for g in ids for t in teams[g]]
    )
    f = CH.team_game_features(games, coach, box)
    assert f.height == 8
    for c in (*CH.PHASE_COLS, *CH.HABIT_COLS, *CH.INTER_COLS):
        assert c in f.columns
        assert f[c].null_count() == 0
    po = f.filter(pl.col("game_id") == "0042300101")
    assert (
        po["po"].to_list() == [1.0, 1.0]
        and po["series_game"].to_list() == [1.0, 1.0]
        and po["gp_frac"].to_list() == [1.0, 1.0]
    )
    first = f.filter((pl.col("game_id") == "0022300001") & (pl.col("team_id") == BOS)).row(
        0, named=True
    )
    assert first["coach_n_reg"] == 0 and first["prev_coach"] is None
    third = f.filter((pl.col("game_id") == "0022300003") & (pl.col("team_id") == BOS)).row(
        0, named=True
    )
    assert (
        third["coach_n_reg"] == 2 - 1
    )  # only game 1 is earlier than game 3 for his team's previous coach key
    pl_f = CH.team_game_features(games, coach, box, placebo_seed=0)
    assert pl_f.shape == f.shape
