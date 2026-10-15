# ruff: noqa: E501
"""REPLACEMENT_DELTA feature tests (rule: docs/prereg/REPLACEMENT_DELTA.md): as-of logic, planted future, parity with
production's ``vacated_features``, the NULL rule and the placebo. Synthetic league only (no database)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl
import pytest

import nba.props.context_residual as CR
from nba.props import repl_delta as RD

TEAMS = (1, 2, 3, 4)
N_PER_TEAM = 11
SEASONS = (2020, 2021, 2022, 2023)
GAMES_PER_SEASON = 30


def synthetic_box(seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(box with the columns repl_delta needs + starter, games). Players have a persistent skill; ranks 1-8 play 14-36
    minutes, ranks 9-11 play 0-9 (fringe)."""
    rng = np.random.default_rng(seed)
    skill: dict[int, float] = {}
    rows: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    gid = 0
    for si, season in enumerate(SEASONS):
        start = dt.date(2019 + si, 10, 20)
        for g in range(GAMES_PER_SEASON):
            d = start + dt.timedelta(days=2 * g)
            order = list(TEAMS)
            rng.shuffle(order)
            for a, h in ((order[0], order[1]), (order[2], order[3])):
                gid += 1
                gs = f"002{season % 100:02d}{gid:05d}"
                games.append(
                    {
                        "game_id": gs,
                        "game_date": d,
                        "season": season,
                        "home_team": h,
                        "away_team": a,
                        "home_pts": 100,
                        "away_pts": 98,
                    }
                )
                for tm in (a, h):
                    for rank in range(N_PER_TEAM):
                        pid = tm * 100 + rank
                        sk = skill.setdefault(pid, 1.4 - 0.11 * rank + 0.05 * rng.normal())
                        if rank < 8:
                            mins = float(np.clip(rng.normal(34 - 2.5 * rank, 3), 12, 40))
                        else:
                            mins = float(rng.choice([0.0, 0.0, 3.0, 8.0]))
                        if mins <= 0:
                            continue
                        rate = max(sk, 0.2)
                        fga = float(rng.poisson(0.42 * mins * rate))
                        rows.append(
                            {
                                "game_id": gs,
                                "team_id": tm,
                                "player_id": pid,
                                "minutes": mins,
                                "pts": float(rng.poisson(0.5 * mins * rate)),
                                "reb": float(rng.poisson(0.18 * mins)),
                                "ast": float(rng.poisson(0.12 * mins * rate)),
                                "fg3m": float(rng.poisson(0.05 * mins * rate)),
                                "fga": fga,
                                "fta": float(rng.poisson(0.12 * mins * rate)),
                                "tov": float(rng.poisson(0.06 * mins)),
                                "oreb": float(rng.poisson(0.04 * mins)),
                                "starter": rank < 5,
                                "game_date": d,
                                "season": season,
                            }
                        )
    return pl.DataFrame(rows), pl.DataFrame(games)


def world(
    box: pl.DataFrame, games: pl.DataFrame, out_by_game: dict[str, set[int]], reported: set[str]
) -> dict[str, Any]:
    b = RD.prep_box(box)
    played = (
        box.join(games.select("game_id", "home_team"), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    states = CR.player_states(played)
    st = RD.state_table(played, states, CR._DI, CR._CI)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    flagged = {g: out_by_game.get(g, set()) for g in reported}
    out_rows = RD.out_rotation_rows(flagged, info, st, CR.MIN_PRIOR_PLAYED, CR.ROTATION_MIN40)
    out_all = pl.DataFrame(
        [(g, p) for g, ps in flagged.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    )
    scored = b.select("game_id", "team_id", "player_id", "game_date", "season")
    repl = RD.replacement_rates(b)
    k = {x: 800.0 for x in RD.RATE_STATS}
    return {
        "b": b,
        "st": st,
        "out_rows": out_rows,
        "out_all": out_all,
        "scored": scored,
        "repl": repl,
        "k": k,
        "flagged": flagged,
        "info": info,
        "played": played,
        "states": states,
    }


def pick_outs(
    box: pl.DataFrame, games: pl.DataFrame, seed: int = 1
) -> tuple[dict[str, set[int]], set[str]]:
    rng = np.random.default_rng(seed)
    out: dict[str, set[int]] = {}
    reported: set[str] = set()
    late = games.filter(pl.col("season") >= 2022)
    for gid, h, a in late.select("game_id", "home_team", "away_team").iter_rows():
        if rng.random() < 0.15:
            continue  # no usable report
        reported.add(gid)
        s: set[int] = set()
        for tm in (h, a):
            if rng.random() < 0.7:
                s.add(tm * 100 + int(rng.integers(0, 6)))
        if s:
            out[gid] = s
    return out, reported


def build(w: dict[str, Any]) -> pl.DataFrame:
    return RD.build_repl_delta(
        w["b"],
        w["st"],
        w["out_rows"],
        w["out_all"],
        w["scored"],
        set(w["flagged"]),
        w["repl"],
        w["k"],
        placebo_seed=0,
    )


@pytest.fixture(scope="module")
def base() -> dict[str, Any]:
    box, games = synthetic_box(0)
    outs, rep = pick_outs(box, games)
    w = world(box, games, outs, rep)
    w["box"], w["games"], w["outs"], w["rep"] = box, games, outs, rep
    w["feat"] = build(w)
    return w


def test_possessions_on_floor_sum_to_game_possessions(base: dict[str, Any]) -> None:
    b = base["b"]
    s = b.group_by(["game_id", "team_id"]).agg(
        pl.col("P").sum().alias("p"), pl.col("poss_g").first()
    )
    assert np.allclose(
        s["p"].to_numpy(), 5.0 * s["poss_g"].to_numpy()
    )  # five players on the floor per possession


def test_replacement_rate_uses_only_prior_seasons(base: dict[str, Any]) -> None:
    b = base["b"]
    r0 = RD.replacement_rates(b)
    assert 2020 not in r0 and 2021 in r0
    bump = b.with_columns(
        pl.when(pl.col("season") == 2023)
        .then(pl.col("pts") * 5)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    r1 = RD.replacement_rates(bump)
    for s in (2021, 2022, 2023):
        assert r1[s]["pts"] == pytest.approx(
            r0[s]["pts"], abs=0.0
        )  # season 2023 never enters 2023 or earlier
    bump2 = b.with_columns(
        pl.when(pl.col("season") == 2021)
        .then(pl.col("pts") * 5)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    assert RD.replacement_rates(bump2)[2022]["pts"] != pytest.approx(r0[2022]["pts"])


def test_fringe_is_rank_above_nine_by_team_season_minutes(base: dict[str, Any]) -> None:
    fr = RD.fringe_player_seasons(base["b"])
    assert fr.height == len(TEAMS) * len(SEASONS) * (N_PER_TEAM - RD.FRINGE_RANK)


def test_planted_future_columns_of_earlier_rows_do_not_move(base: dict[str, Any]) -> None:
    cut = dt.date(2022, 3, 1)
    box, games = base["box"], base["games"]
    rng = np.random.default_rng(5)
    late = box["game_date"] >= cut
    idx = np.flatnonzero(late.to_numpy())
    perm = rng.permutation(idx)
    scr = box.clone()
    for c in ("minutes", "pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "oreb", "starter"):
        v = scr[c].to_numpy().copy()
        v[idx] = v[perm]
        scr = scr.with_columns(pl.Series(c, v))
    w2 = world(scr, games, base["outs"], base["rep"])
    f1, f2 = base["feat"], build(w2)
    sc = base["scored"].select("game_id", "player_id", "game_date")
    j = f1.join(f2, on=["game_id", "player_id"], suffix="_2").join(sc, on=["game_id", "player_id"])
    early = j.filter(pl.col("game_date") < cut)
    assert early.height > 500
    for c in (*RD.COLUMNS, *RD.PLACEBO_COLUMNS):
        a, b2 = early[c].to_numpy().astype(float), early[c + "_2"].to_numpy().astype(float)
        assert np.array_equal(np.isnan(a), np.isnan(b2)), c
        assert np.nanmax(np.abs(a - b2), initial=0.0) <= 1e-9, c
    late_j = j.filter(pl.col("game_date") >= cut)
    moved = np.nanmax(np.abs(late_j["qv_pts"].to_numpy() - late_j["qv_pts_2"].to_numpy()))
    assert moved > 0.0  # the scramble is not inert: later rows do move


def test_out_rows_reproduce_production_vac_min(base: dict[str, Any]) -> None:
    flagged, info = base["flagged"], base["info"]
    vac = CR.vacated_features(base["played"], base["states"], flagged, info)
    mine = (
        base["out_rows"]
        .group_by(["game_id", "team_id"])
        .agg(pl.col("min10").sum().alias("vac_min_rd"), pl.len().alias("n"))
    )
    j = vac.join(mine, on=["game_id", "team_id"], how="full", coalesce=True)
    assert j.height > 50
    assert np.allclose(
        j["vac_min"].fill_null(0).to_numpy(), j["vac_min_rd"].fill_null(0).to_numpy(), atol=1e-12
    )
    assert (j["n_out_rot"].fill_null(0).to_numpy() == j["n"].fill_null(0).to_numpy()).all()


def test_null_rule_no_report_is_null_and_reported_nobody_out_is_zero(base: dict[str, Any]) -> None:
    f = base["feat"].join(
        base["scored"].select("game_id", "player_id", "season"), on=["game_id", "player_id"]
    )
    unrep = f.filter(~pl.col("game_id").is_in(sorted(base["rep"])) & (pl.col("season") >= 2022))
    assert unrep.height > 0 and unrep["qv_pts"].is_null().all() and unrep["n_out"].is_null().all()
    zero = f.filter(pl.col("game_id").is_in(sorted(base["rep"])) & (pl.col("n_out") == 0))
    assert zero.height > 0
    assert (zero["qv_pts"] == 0).all() and (zero["rd_pts"].drop_nulls() == 0).all()


def test_rd_is_qv_times_share_and_v0_exceeds_qv_by_replacement_term(base: dict[str, Any]) -> None:
    f = base["feat"].filter(pl.col("n_out") > 0).drop_nulls(["share", "qv_pts"])
    assert f.height > 50
    assert np.allclose(f["rd_pts"], f["qv_pts"] * f["share"])
    assert np.allclose(f["v0d_pts"], f["v0_pts"] * f["share"])
    assert (
        f["v0_pts"] >= f["qv_pts"] - 1e-12
    ).all()  # replacement rate is positive, so subtraction lowers the sum


def test_shares_are_in_unit_interval(base: dict[str, Any]) -> None:
    sh = base["feat"]["share"].drop_nulls()
    assert sh.len() > 500 and float(sh.min()) > 0.0 and float(sh.max()) <= 1.0 + 1e-12


def test_unseen_out_player_contributes_replacement_rate_only() -> None:
    """A rotation OUT player with no earlier game would get rate = repl, so his quality-weighted vacancy is exactly 0."""
    frame = pl.DataFrame(
        {
            "Qc": [None],
            "S_pts": [None],
            "S_reb": [None],
            "S_ast": [None],
            "S_fg3m": [None],
            "S_usage": [None],
            "season": [2022],
        },
        schema={
            "Qc": pl.Float64,
            **{f"S_{x}": pl.Float64 for x in RD.RATE_STATS},
            "season": pl.Int64,
        },
    )
    repl = {2022: {x: 0.1 for x in RD.RATE_STATS}}
    out = RD.shrunk_rates(frame, repl, {x: 500.0 for x in RD.RATE_STATS})
    for x in RD.RATE_STATS:
        assert out[f"r_{x}"][0] == pytest.approx(0.1)


def test_shrinkage_weight_is_q_over_q_plus_k() -> None:
    frame = pl.DataFrame(
        {
            "Qc": [500.0],
            "S_pts": [100.0],
            "S_reb": [0.0],
            "S_ast": [0.0],
            "S_fg3m": [0.0],
            "S_usage": [0.0],
            "season": [2022],
        }
    )
    repl = {2022: {x: 0.1 for x in RD.RATE_STATS}}
    out = RD.shrunk_rates(frame, repl, {x: 500.0 for x in RD.RATE_STATS})
    assert out["r_pts"][0] == pytest.approx((100.0 + 500.0 * 0.1) / 1000.0)


def test_estimate_k_recovers_a_known_prior() -> None:
    """Rates drawn around a known centre with known between-player sd: k_hat = sigma2/tau2 lands within 40%."""
    rng = np.random.default_rng(0)
    rows = []
    n_pl, n_g = 300, 40
    tau = 0.02
    for p in range(n_pl):
        true = 0.2 + tau * rng.normal()
        for _g in range(n_g):
            poss = 60.0
            rows.append(
                {
                    "season": 2022,
                    "player_id": p,
                    "P": poss,
                    "pts": float(rng.normal(true * poss, 3.0)),
                    "usage_n": 0.0,
                }
            )
    b = pl.DataFrame(rows).with_columns(pl.lit(0.0).alias(x) for x in ("reb", "ast", "fg3m"))
    repl = {2022: {x: 0.2 for x in RD.RATE_STATS}}
    res = RD.estimate_k(b, repl, (2022,))["pts"]
    # Q = 40 x 60 = 2400; sampling variance of a rate = 9 / (40 x 60^2); sigma2 = Q x that = 0.15; k = sigma2 / tau^2 = 375
    assert res["sigma2"] == pytest.approx(0.15, rel=0.1)
    assert res["tau2"] == pytest.approx(tau**2, rel=0.4)
    assert res["k"] == pytest.approx(375.0, rel=0.4)


def test_placebo_is_deterministic_and_draws_only_from_the_pool(base: dict[str, Any]) -> None:
    a = build(base).sort(
        ["game_id", "player_id"]
    )  # row order is not part of the contract (joins are on keys)
    b2 = build(base).sort(["game_id", "player_id"])
    for c in RD.PLACEBO_COLUMNS:
        assert a[c].equals(b2[c])
    f = a.filter(pl.col("n_out") > 0).drop_nulls("pq_qv_pts")
    assert f.height > 50
    assert not np.allclose(f["pq_qv_pts"].to_numpy(), f["qv_pts"].to_numpy())


def test_one_feature_row_per_scored_row(base: dict[str, Any]) -> None:
    f = base["feat"]
    assert f.height == base["scored"].height
    assert f.select("game_id", "player_id").unique().height == f.height
