"""FOUR_FACTORS (docs/prereg/FOUR_FACTORS.md): builder, planted tests, placebo, pass rule."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl

from research.eval import four_factors_eval as ev
from research.eval.f11_lineup_context import frozen_sha256
from research.features import four_factors as ff

DOC = Path(__file__).resolve().parents[3] / "docs/prereg/FOUR_FACTORS.md"


def _synth(n_days: int = 40, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    teams = [1, 2, 3, 4]
    games, box, poss = [], [], []
    gid = 0
    for d in range(n_days):
        date = dt.date(2023, 11, 1) + dt.timedelta(days=d)
        order = rng.permutation(teams)
        for h, a in ((order[0], order[1]), (order[2], order[3])):
            g = f"002{gid:07d}"
            gid += 1
            games.append(
                {
                    "game_id": g,
                    "game_date": date,
                    "season": 2023,
                    "home_team": int(h),
                    "away_team": int(a),
                    "home_pts": 100,
                    "away_pts": 99,
                }
            )
            for t in (h, a):
                box.append(
                    {
                        "game_id": g,
                        "player_id": int(t) * 10,
                        "team_id": int(t),
                        "minutes": 30.0,
                        "pts": 100,
                        "fga": int(rng.integers(80, 95)),
                        "fgm": int(rng.integers(35, 45)),
                        "fg3m": int(rng.integers(8, 15)),
                        "fta": int(rng.integers(15, 30)),
                        "tov": int(rng.integers(8, 18)),
                        "oreb": int(rng.integers(6, 14)),
                        "dreb": int(rng.integers(28, 38)),
                    }
                )
                for i in range(int(rng.integers(92, 108))):
                    poss.append(
                        {
                            "game_id": g,
                            "period": 1 + (i * 4) // 100,
                            "off_team": int(t),
                            "def_team": 0,
                            "pts": 1 if i % 100 == 0 else 0,
                        }
                    )
    return pl.DataFrame(games), pl.DataFrame(box), pl.DataFrame(poss)


def test_frozen_prefix_matches_doc() -> None:
    assert frozen_sha256(DOC) == ev.FROZEN_FULL
    assert frozen_sha256(DOC).startswith(ev.FROZEN_PREFIX)


def test_builder_shape_and_zero_start() -> None:
    g, b, p = _synth()
    out, tg = ff.build_four_factors(g, b, p)
    assert out.height == 2 * g.height == tg.height
    assert out.columns == ["game_id", "team_id", *ff.FF_COLS]
    first = g.sort("game_date")["game_id"].to_list()[:2]
    assert out.filter(pl.col("game_id").is_in(first)).select(ff.FF_COLS).to_numpy().sum() == 0.0
    assert all(np.isfinite(out[c].to_numpy()).all() for c in ff.FF_COLS)


def test_overtime_minutes_scale_pace() -> None:
    g, b, p = _synth(3)
    gid = g["game_id"][0]
    p2 = p.with_columns(
        pl.when((pl.col("game_id") == gid) & (pl.col("period") == 4))
        .then(6)
        .otherwise(pl.col("period"))
        .alias("period")
    )
    _, tg = ff.build_four_factors(g, b, p2)
    row = tg.filter(pl.col("game_id") == gid)
    assert row["n_ot"].min() == 2 and row["minutes_played"].min() == 58.0


def test_planted_same_game_and_future() -> None:
    g, b, p = _synth()
    base, _ = ff.build_four_factors(g, b, p)
    day = g.sort("game_date")["game_date"][40]
    targets = set(g.filter(pl.col("game_date") == day)["game_id"].to_list())
    upto = set(g.filter(pl.col("game_date") <= day)["game_id"].to_list())
    later = set(g["game_id"].to_list()) - upto
    b3, p3 = ev.edit_same_game(b, p, targets)
    f3, _ = ff.build_four_factors(g, b3, p3)
    assert ev.ff_equal(base, f3, upto)
    assert not ev.ff_equal(base, f3, later)
    cutoff = dt.date(2023, 11, 20)
    before = set(g.filter(pl.col("game_date") <= cutoff)["game_id"].to_list())
    b4, p4 = ev.edit_future(g, b, p, cutoff)
    f4, _ = ff.build_four_factors(g, b4, p4)
    assert ev.ff_equal(base, f4, before)
    assert not ev.ff_equal(base, f4, set(g["game_id"].to_list()) - before)


def test_out_of_range_possessions_take_league_mean() -> None:
    g, b, p = _synth()
    gid = g.sort("game_date")["game_id"][30]
    p2 = p.filter(
        ~(
            (pl.col("game_id") == gid)
            & (pl.col("off_team") == g.filter(pl.col("game_id") == gid)["home_team"][0])
            & (pl.col("period") >= 2)
        )
    )
    _, tg = ff.build_four_factors(g, b, p2)
    assert int(tg["bad_poss"].sum()) >= 1


def test_placebo_keeps_marginals_within_team_season() -> None:
    n = 40
    d = pl.DataFrame(
        {
            "team_id": [1] * 20 + [2] * 20,
            "season": [2023] * n,
            **{c: np.arange(n, dtype=float) + k for k, c in enumerate(ff.FF_COLS)},
        }
    )
    out = ff.placebo_ff(d, 0)
    for c in ff.FF_COLS:
        assert sorted(out[c].to_list()[:20]) == sorted(d[c].to_list()[:20])
    assert not np.array_equal(out[ff.FF_COLS[0]].to_numpy(), d[ff.FF_COLS[0]].to_numpy())
    # joint permutation: the rows keep their column relationship
    diff = (out[ff.FF_COLS[1]] - out[ff.FF_COLS[0]]).to_numpy()
    assert np.allclose(diff, 1.0)


def test_arm_names_and_a0_unchanged() -> None:
    from nba.props.context_residual import stat_feature_names

    for s in ev.STATS:
        assert ev.arm_names(s, "A0") == stat_feature_names(s)
        assert ev.arm_names(s, "A1")[-1] == "ff_pace"
        assert ev.arm_names(s, "A2")[-9:] == list(ff.FF_COLS)
        assert (
            "ff_pace" not in ev.arm_names(s, "A4")
            and len(ev.arm_names(s, "A4")) == len(ev.arm_names(s, "A0")) + 8
        )


def test_collect_arm_refuses_holdout() -> None:
    import pytest

    with pytest.raises(ValueError):
        ev.collect_arm(pl.DataFrame(), "pts", None, [], (2025,))  # type: ignore[arg-type]
