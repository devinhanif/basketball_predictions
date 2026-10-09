"""Rookie minutes prior: class flag, ridge prior, blend algebra, untouched non-rookies, eval."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist
from nba.props.rookie_minutes import (
    RookieMinutesPrior,
    apply_prior,
    class_member_expr,
    evaluate_rookie_minutes,
    first_seasons,
    position_prior,
    rookie_training_table,
    with_class_flag,
)


def _static(n_per_class: int = 12) -> pl.DataFrame:
    rows = []
    pid = 0
    for cls in (2022, 2023, 2024):
        for i in range(n_per_class):
            pid += 1
            pick = i * 4 + 1
            rows.append(
                {
                    "player_id": pid,
                    "draft_year": cls,
                    "draft_pick": pick,
                    "undrafted": 0,
                    "log_pick": float(np.log(pick)),
                    "height_in": 76.0 + (i % 5),
                    "weight_lb": 200.0 + 3 * (i % 7),
                    "birth_date": date(cls - 20 - (i % 3), 3, 1),
                    "position_primary": ("Guard", "Forward", "Center")[i % 3],
                }
            )
    # veteran (drafted 2015), plays every season
    rows.append(
        {
            "player_id": 999,
            "draft_year": 2015,
            "draft_pick": 10,
            "undrafted": 0,
            "log_pick": float(np.log(10)),
            "height_in": 80.0,
            "weight_lb": 220.0,
            "birth_date": date(1995, 1, 1),
            "position_primary": "Forward",
        }
    )
    return pl.DataFrame(rows)


def _games(static: pl.DataFrame, n_games: int = 30) -> tuple[pl.DataFrame, pl.DataFrame]:
    """played (player_id, season, minutes) + feature frame with as-of prior columns."""
    played_rows, feat_rows = [], []
    gid = 0
    for r in static.iter_rows(named=True):
        first = r["draft_year"] if r["player_id"] != 999 else 2022
        for s in range(first, 2025):
            if r["player_id"] == 999:
                base = 30.0
            else:
                base = max(8.0, 34.0 - 7.0 * r["log_pick"] + (s - first) * 2.0)
            tot = 0.0
            for n_prior in range(n_games):
                g = n_prior
                gid += 1
                m = base + (g % 5) - 2
                played_rows.append({"player_id": r["player_id"], "season": s, "minutes": m})
                avg = tot / n_prior if n_prior else None
                feat_rows.append(
                    {
                        "game_id": f"g{gid}",
                        "player_id": r["player_id"],
                        "season": s,
                        "games_played_prior": n_prior,
                        "n_played_prior": float(n_prior),
                        "play_rate_prior": 1.0 if n_prior else None,
                        "avg_minutes_given_played_prior": avg,
                        "std_minutes_given_played_prior": 1.5 if n_prior else None,
                        "act": m,
                    }
                )
                tot += m
    return pl.DataFrame(played_rows), pl.DataFrame(feat_rows)


def test_class_flag_and_first_seasons() -> None:
    static = _static()
    played, _ = _games(static, 3)
    firsts = first_seasons(played)
    df = with_class_flag(pl.DataFrame({"player_id": [1, 13, 999]}), static, firsts)
    flags = dict(zip(df["player_id"].to_list(), df["is_class"].to_list(), strict=True))
    assert flags[1] and flags[13]
    assert not flags[999]  # 2022 first season but drafted 2015


def test_undrafted_only_identifiable_from_2023() -> None:
    df = pl.DataFrame(
        {
            "first_season": [2022, 2023],
            "draft_year": [None, None],
            "draft_pick": [None, None],
            "undrafted": [1, 1],
        },
        schema_overrides={"draft_year": pl.Int64, "draft_pick": pl.Int64},
    )
    assert df.select(class_member_expr()).to_series().to_list() == [False, True]


def test_ridge_prior_orders_by_pick_and_training_table() -> None:
    static = _static()
    played, _ = _games(static)
    tr = pl.concat([rookie_training_table(played, static, c) for c in (2022, 2023)])
    assert tr.height == 24
    prior = RookieMinutesPrior().fit(tr, tr["mpg"].to_numpy())
    test = rookie_training_table(played, static, 2024).sort("log_pick")
    pred = prior.predict(test)
    assert pred[0] > pred[-1]  # top pick projected above a late pick
    assert np.corrcoef(pred, test["mpg"].to_numpy())[0, 1] > 0.9
    assert prior.get_config()["n_train"] == 24
    pos = position_prior(tr, test)
    assert len(pos) == test.height and np.all(np.isfinite(pos))


def test_apply_prior_algebra_and_untouched_rows() -> None:
    cfg = MinutesModelConfig()
    d = [MinutesHurdleDist(0.9, 20.0, 5.0), MinutesHurdleDist(0.9, 20.0, 5.0)]
    n = np.array([0.0, 0.0])
    out = apply_prior(d, n, np.array([12.0, 12.0]), np.array([True, False]), cfg)
    assert out[0].mu == pytest.approx(20.0 + (12.0 - cfg.default_mu))  # n -> 0: full prior swap
    assert out[1] is d[1]  # non-rookie untouched
    big = apply_prior(
        d, np.array([5000.0, 5000.0]), np.array([12.0, 12.0]), np.array([True, True]), cfg
    )
    assert big[0].mu == pytest.approx(20.0, abs=0.02)  # n -> inf: observed wins
    assert out[0].sigma == 5.0 and out[0].p_play == pytest.approx(0.9)


def test_evaluate_on_synthetic_keeps_prior_and_leaves_nonrookies() -> None:
    static = _static()
    played, feats = _games(static)
    actual = feats.select("game_id", "player_id", pl.col("act").alias("minutes"))
    res = evaluate_rookie_minutes(
        feats.drop("act"), actual, played, static, MinutesModelConfig(), n_boot=200
    )
    assert res["non_rookie_unchanged"] is True
    assert res["pooled"]["n_rows"] > 0
    assert res["pooled"]["delta_ridge_vs_current"][0] < 0
    assert set(res["by_class"]) == {"2023", "2024"}
