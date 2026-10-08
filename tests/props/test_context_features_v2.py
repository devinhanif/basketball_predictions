# ruff: noqa: E501
"""Tests for the ctxres_v2 feature builder, the sweep job wiring and the eval helpers."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.colab.jobs import load_job
from nba.eval.ctxres_v2_eval import ece_equal_mass
from nba.props.context_features_v2 import (
    ARENAS,
    OUT_ORD,
    V2_GROUPS,
    all_v2_columns,
    assert_feature_allowlist,
    build_v2_features,
    ew_pre_post,
    group_columns,
    haversine_miles,
    possession_rates,
    pretip_status,
    schedule_features,
    v2_feature_names,
)
from nba.props.context_residual import PROP_STATS, build_features, flagged_from_availability
from nba.sim.usage_redistribution import ReportTriggerConfig

ELO = {
    "k_factor": 10.0,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 0.00067,
}
TEAMS = [1610612737 + i for i in range(6)]
JOB_DIR = Path(__file__).resolve().parents[2] / "nba" / "colab" / "jobs" / "ctxres_v2_sweep"


def _league(seed: int = 0, n_days: int = 150):
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 10, 20)
    games, rows, poss, avail = [], [], [], []
    gid = 0
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 2)
        order = rng.permutation(len(TEAMS))
        for k in range(0, len(TEAMS), 2):
            h, a = TEAMS[int(order[k])], TEAMS[int(order[k + 1])]
            gid += 1
            g = f"{gid:07d}"
            tv = "ESPN" if gid % 5 == 0 else None
            games.append(
                (g, d, 2022, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125)), tv)
            )
            for t in (h, a):
                for p in range(9):
                    pid = t * 100 + p
                    played = rng.random() > (0.05 if p < 6 else 0.4)
                    mins = float(max(34 - 3 * p + rng.normal(0, 3), 4)) if played else None
                    lam = (mins or 0) / 34.0
                    rows.append(
                        (g, pid, t, mins, int(rng.poisson(14 * lam)) if played else 0,
                         int(rng.poisson(5 * lam)) if played else 0,
                         int(rng.poisson(3 * lam)) if played else 0,
                         int(rng.poisson(1.5 * lam)) if played else 0, bool(p < 5),
                         int(rng.poisson(lam)) if played else 0, int(rng.poisson(3 * lam)) if played else 0,
                         int(rng.poisson(10 * lam)) if played else 0, int(rng.poisson(2 * lam)) if played else 0,
                         int(rng.poisson(3 * lam)) if played else 0)
                    )  # fmt: skip
                    if played:
                        poss.append((g, pid, int(60 * lam), 2, 2, 1, 2, 3, 1))
                    if not played and p < 6:
                        avail.append((g, pid, "out", dt.datetime(d.year, d.month, d.day, 17, 45),
                                      "nba_official_report"))  # fmt: skip
    games_df = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts",
                "national_tv"],
        orient="row",
    )  # fmt: skip
    pgs = pl.DataFrame(
        rows,
        schema=["game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "starter",
                "oreb", "dreb", "fga", "tov", "fta"],
        orient="row",
    )  # fmt: skip
    pos = pl.DataFrame(
        poss, schema=["game_id", "player_id", "on_poss", "z_rim", "z_mid", "z_c3", "z_a3", "makes",
                      "ast_makes"], orient="row",
    )  # fmt: skip
    av = pl.DataFrame(
        avail,
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "status": pl.Utf8,
                "as_of": pl.Datetime("us"), "source": pl.Utf8},
        orient="row",
    )  # fmt: skip
    static = pl.DataFrame(
        {
            "player_id": sorted({r[1] for r in rows}),
            "position": ["Guard"] * 27 + ["Forward"] * 27,
            "draft_year": [2018] * 54,
            "draft_pick": [10] * 54,
        }
    )
    return games_df, pgs, pos, av, static


def _build(games, pgs, pos, av, static):
    cfg = ReportTriggerConfig()
    flagged, _ = flagged_from_availability(av, games, cfg)
    v1 = build_features(games, pgs, static, flagged, ELO)
    return build_v2_features(v1, games, pgs, static, av, pos, cfg)


@pytest.fixture(scope="module")
def league():
    return _league()


@pytest.fixture(scope="module")
def built(league):
    return _build(*league)


def test_columns_present_and_groups_consistent(built):
    cols = all_v2_columns()
    assert len(cols) == len(set(cols))
    assert set(cols) <= set(built.columns)
    assert set(V2_GROUPS) == set("abcdefghij")
    for s in PROP_STATS:
        assert set(v2_feature_names(s)) <= set(built.columns)
        assert all(f"{s}" in c for c in group_columns("e", s))


def test_future_rows_do_not_change_past_features(league):
    """Plant different box scores / minutes / possessions for every game dated >= T: features of
    rows dated <= T must be identical (strictly as-of)."""
    games, pgs, pos, av, static = league
    base = _build(games, pgs, pos, av, static)
    cut = sorted(games["game_date"].unique().to_list())[80]
    late = set(games.filter(pl.col("game_date") >= cut)["game_id"].to_list())
    in_late = pl.col("game_id").is_in(sorted(late))
    pgs2 = pgs.with_columns(
        [pl.when(in_late).then(pl.col(c) * 3 + 7).otherwise(pl.col(c)).alias(c)
         for c in ("pts", "reb", "ast", "fg3m", "oreb", "dreb", "fga", "tov", "fta")]
        + [pl.when(in_late & pl.col("minutes").is_not_null()).then(pl.col("minutes") * 0.8)
           .otherwise(pl.col("minutes")).alias("minutes")]
    )  # fmt: skip
    pos2 = pos.with_columns(
        [pl.when(pl.col("game_id").is_in(sorted(late))).then(pl.col(c) * 2 + 1).otherwise(pl.col(c)).alias(c)
         for c in ("on_poss", "z_rim", "z_mid", "z_c3", "z_a3", "makes", "ast_makes")]
    )  # fmt: skip
    new = _build(games, pgs2, pos2, av, static)
    cols = [c for c in all_v2_columns() if not c.startswith(("sch_", "nat_"))]
    keys = ["game_id", "player_id"]
    a = base.filter(pl.col("game_date") <= cut).select(keys + cols).sort(keys)
    b = new.filter(pl.col("game_date") <= cut).select(keys + cols).sort(keys)
    assert a.height == b.height > 100
    # opponent / ridge features on date == cut still only use rows dated < cut
    for c in cols:
        x, y = a[c].to_numpy().astype(float), b[c].to_numpy().astype(float)
        assert np.allclose(x, y, equal_nan=True, atol=1e-6), c


def test_report_after_tip_minus_60_is_ignored(league):
    games, pgs, pos, av, static = league
    g = games.sort("game_date")["game_id"][50]
    d = games.filter(pl.col("game_id") == g)["game_date"][0]
    pid = int(pgs.filter(pl.col("game_id") == g)["player_id"][0])
    cfg = ReportTriggerConfig()
    late = pl.DataFrame(
        {"game_id": [g], "player_id": [pid], "status": ["out"],
         "as_of": [dt.datetime(d.year, d.month, d.day, 18, 30)], "source": ["nba_official_report"]},
        schema=av.schema,
    )  # fmt: skip
    early = late.with_columns(pl.lit(dt.datetime(d.year, d.month, d.day, 17, 45)).alias("as_of"))
    base_tab, _ = pretip_status(av, games, cfg)
    late_tab, _ = pretip_status(pl.concat([av, late]), games, cfg)
    early_tab, _ = pretip_status(pl.concat([av, early]), games, cfg)

    def status(tab: pl.DataFrame) -> list[int]:
        return tab.filter((pl.col("game_id") == g) & (pl.col("player_id") == pid))[
            "status_ord"
        ].to_list()

    assert status(late_tab) == status(base_tab)  # stamped 18:30 > 18:00 cutoff: ignored
    assert status(early_tab) == [OUT_ORD]  # control: a pre-cutoff report is used


def test_dnp_rows_excluded_from_possession_rates(league):
    games, pgs, pos, _av, _static = league
    full = pgs.join(games.select("game_id", "game_date"), on="game_id")
    played = full.filter(pl.col("minutes") > 0).sort(["player_id", "game_date", "game_id"])
    assert full.filter(pl.col("minutes").is_null()).height > 0  # DNP rows exist in the input
    with_dnp = pgs.join(games.select("game_id", "game_date"), on="game_id").sort(
        ["player_id", "game_date", "game_id"]
    )
    played2 = with_dnp.filter(pl.col("minutes").fill_null(0.0) > 0)
    r1, u1 = possession_rates(played, pos)
    r2, u2 = possession_rates(played2, pos)
    assert np.allclose(r1["pr_usage"].to_numpy(), r2["pr_usage"].to_numpy())
    assert r1.height == played.height
    assert np.allclose(u1, u2)


def test_ew_pre_excludes_current_row():
    keys = np.array([1, 1, 1, 2])
    x = np.array([[1.0], [2.0], [3.0], [5.0]])
    pre, post = ew_pre_post(keys, x, halflife=1.0)
    assert pre[0, 0] == 0.0 and pre[3, 0] == 0.0
    assert pre[1, 0] == pytest.approx(1.0) and post[1, 0] == pytest.approx(0.5 * 1.0 + 2.0)


def test_allowlist_blocks_same_game_names():
    assert_feature_allowlist(["m10_pts", "rel__m10_pts", "pr_usage", "ridge_player_pts"])
    for bad in ("pts", "minutes", "starter", "reb", "post_tip_usage", "unknown_feature"):
        with pytest.raises(ValueError):
            assert_feature_allowlist(["m10_pts", bad])
    names = [c for s in PROP_STATS for c in v2_feature_names(s)]
    assert_feature_allowlist(names)


def test_schedule_flags_and_travel():
    t = TEAMS[0]
    o = TEAMS[1]
    days = [0, 1, 3, 10]
    games = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(4)],
            "game_date": [dt.date(2022, 11, 1) + dt.timedelta(days=d) for d in days],
            "season": [2022] * 4,
            "home_team": [t, o, t, o],
            "away_team": [o, t, o, t],
        }
    )
    s = schedule_features(games).filter(pl.col("team_id") == t).sort("game_id")
    assert s["sch_b2b_second"].to_list() == [0, 1, 0, 0]
    assert s["sch_b2b_first"].to_list() == [1, 0, 0, 0]
    assert s["sch_three_in_four"].to_list()[2] == 1  # games on day 0,1,3 -> 3 in 4 nights
    assert s["sch_travel_leg"].to_list()[0] == 0.0 and s["sch_travel_leg"].to_list()[1] > 100
    assert haversine_miles(*ARENAS[t], *ARENAS[t]) == pytest.approx(0.0)


def test_ece_equal_mass_perfect_and_biased():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 20000)
    ev = rng.uniform(size=p.size) < p
    assert ece_equal_mass(p, ev) < 0.02
    assert ece_equal_mass(np.clip(p + 0.2, 0, 1), ev) > 0.1


def test_job_yaml_and_notebook_in_sync():
    job = load_job("ctxres_v2_sweep")
    assert job.touches_holdout is False and job.preregistration_id is None
    assert {"best_config.json", "metrics.json", "oof_2024.parquet"} <= set(job.artifacts)
    spec = importlib.util.spec_from_file_location("ctxres_build_nb", JOB_DIR / "build_notebook.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nb = json.loads((JOB_DIR / "ctxres_v2_sweep.ipynb").read_text())
    assert nb == mod.build()  # regenerate with build_notebook.py after editing the script
    src = (JOB_DIR / "ctxres_v2_sweep.py").read_text()
    assert "FROZEN_SEASON" in src and "season 2025 present" in src
