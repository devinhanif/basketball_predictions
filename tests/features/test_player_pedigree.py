"""Pedigree features: undrafted/missing semantics, college map, as-of age, and a
planted-future-game leakage check for the screen's recency baseline."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from nba.features.player_pedigree import (
    college_group,
    load_college_map,
    pedigree_as_of,
    static_pedigree,
)
from nba.props.forward import recency_weighted_dist
from research.eval.context_screen_players import (
    add_recency_baseline,
    bh_reject,
    cluster_mean_ci,
    ols_cluster_boot,
)


def _players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": [1, 2, 3, 4],
            "position": ["Guard-Forward", "Center", None, "Forward"],
            "height_in": [78.0, 84.0, None, 80.0],
            "weight_lb": [210.0, 250.0, None, 230.0],
            "birth_date": [date(2003, 3, 1), date(1995, 1, 1), date(1999, 5, 5), date(2001, 2, 2)],
            "draft_year": [2022, None, 2018, 2022],
            "draft_pick": [3, None, None, 40],
            "college": ["Duke", "", "Xavier", "NBA G League Ignite"],
        },
        schema_overrides={"draft_year": pl.Int64, "draft_pick": pl.Int64},
    )


def test_college_map_defaults_and_conflicts(tmp_path) -> None:
    m = load_college_map()
    assert college_group("Duke", m) == "power_conf"
    assert college_group("Gonzaga", m) == "mid_major"
    assert college_group("Real Madrid", m) == "international_pro"
    assert college_group("Overtime Elite", m) == "pathway"
    assert college_group(None, m) == "unknown"
    assert college_group("  ", m) == "unknown"
    bad = tmp_path / "bad.yaml"
    bad.write_text("power_conf: [X]\nmid_major: [X]\n")
    with pytest.raises(ValueError):
        load_college_map(bad)
    bad.write_text("nonsense: [X]\n")
    with pytest.raises(ValueError):
        load_college_map(bad)


def test_static_pedigree_semantics() -> None:
    s = static_pedigree(_players(), load_college_map()).sort("player_id")
    assert s["undrafted"].to_list() == [0, 1, 0, 0]  # row 3: year set, pick NULL = unknown
    assert s["draft_round"].to_list() == [1, 0, None, 2]
    assert s["log_pick"][1] == pytest.approx(np.log(61))
    assert s["log_pick"][2] is None
    assert s["young_high_pick"].to_list() == [1, 0, 0, 0]
    assert s["position_primary"].to_list() == ["Guard", "Center", "Unknown", "Forward"]
    assert s["college_group"].to_list() == ["power_conf", "unknown", "power_conf", "pathway"]
    with pytest.raises(ValueError):
        static_pedigree(pl.concat([_players(), _players()]), load_college_map())


def test_pedigree_as_of_age_and_missing_player() -> None:
    s = static_pedigree(_players(), load_college_map())
    rows = pl.DataFrame({"player_id": [1, 1], "game_date": [date(2023, 3, 1), date(2024, 3, 1)]})
    out = pedigree_as_of(s, rows)
    assert out.height == 2
    assert out["age"][0] == pytest.approx(20.0, abs=0.01)
    assert out["age"][1] > out["age"][0]
    assert out["years_since_draft"][1] > out["years_since_draft"][0]
    with pytest.raises(ValueError):
        pedigree_as_of(s, pl.DataFrame({"player_id": [99], "game_date": [date(2023, 1, 1)]}))


def _series(vals: list[float]) -> pl.DataFrame:
    n = len(vals)
    return pl.DataFrame(
        {
            "player_id": [7] * n,
            "game_id": [f"g{i:02d}" for i in range(n)],
            "game_date": [date(2023, 1, 1 + i) for i in range(n)],
            "season": [2022] * n,
            "minutes": [20.0] * n,
            "pts": vals,
            "reb": [1.0] * n,
            "ast": [1.0] * n,
            "fg3m": [0.0] * n,
        }
    )


def test_recency_baseline_matches_forward_and_has_no_future_leak() -> None:
    vals = [10.0, 12.0, 8.0, 15.0, 11.0, 9.0]
    out = add_recency_baseline(_series(vals))
    assert np.isnan(out["base_pts"][0])
    for i in range(1, len(vals)):
        want = recency_weighted_dist(np.asarray(vals[:i]), "pts", 10.0).mean()
        assert out["base_pts"][i] == pytest.approx(want)
    assert out["n_hist"].to_list() == [0, 1, 2, 3, 4, 5]
    # planted future game: change the last value hugely; earlier baselines unchanged
    planted = add_recency_baseline(_series(vals[:-1] + [500.0]))
    a, b = out["base_pts"].to_numpy(), planted["base_pts"].to_numpy()
    assert np.allclose(a, b, equal_nan=True)
    assert planted["res_pts"][-1] == pytest.approx(500.0 - planted["base_pts"][-1])


def test_pedigree_features_ignore_stats() -> None:
    s = static_pedigree(_players(), load_college_map())
    r1 = pedigree_as_of(s, pl.DataFrame({"player_id": [1], "game_date": [date(2023, 1, 5)]}))
    # features take no stat input at all; same call is identical (pure function)
    r2 = pedigree_as_of(s, pl.DataFrame({"player_id": [1], "game_date": [date(2023, 1, 5)]}))
    assert r1.equals(r2)


def test_bh_and_bootstrap_helpers() -> None:
    p = np.array([0.001, 0.2, 0.04, 0.9])
    assert bh_reject(p, 0.10).tolist() == [True, False, True, False]
    assert not bh_reject(np.array([0.5, 0.6]), 0.10).any()
    rng = np.random.default_rng(0)
    n = 600
    x = rng.normal(size=n)
    y = 2.0 * x + rng.normal(size=n)
    X = np.column_stack([np.ones(n), x])
    codes = np.arange(n) % 60
    beta, boots = ols_cluster_boot(X, y[:, None], codes, 200, rng)
    assert beta[1, 0] == pytest.approx(2.0, abs=0.2)
    assert boots[:, 1, 0].min() > 1.0
    m, lo, hi = cluster_mean_ci(y, codes, rng, 200)
    assert lo <= m <= hi
