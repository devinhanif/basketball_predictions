# ruff: noqa: E501
"""Teammate-sits scenarios: the scenario equals production with the sitter on the report (synthetic league, no DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    fit_for_date,
    player_states,
    vacated_features,
)
from research.teammate_sits import scenario as S

ELO = {
    "k_factor": 10.0,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 0.00067,
}


def _league(
    seed: int = 0, n_days: int = 200, n_teams: int = 6, per_team: int = 9
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, set[int]]]:
    """Synthetic league; every game has a report; a DNP is listed OUT half of the time."""
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 10, 20)
    games, rows = [], []
    flagged: dict[str, set[int]] = {}
    gid = 0
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 2)
        season = d.year if d.month >= 8 else d.year - 1
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            h, a = int(order[k]), int(order[k + 1])
            gid += 1
            g = f"{gid:07d}"
            flagged[g] = set()
            games.append(
                (g, d, season, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125)))
            )
            for t in (h, a):
                for p in range(per_team):
                    pid = t * 100 + p
                    played = rng.random() > (0.08 if p < 6 else 0.4)
                    if not played and rng.random() < 0.5:
                        flagged[g].add(pid)
                    mins = float(max(34 - 3 * p + rng.normal(0, 3), 4)) if played else None
                    lam = (mins or 0.0) / 34.0
                    rows.append(
                        (
                            g,
                            pid,
                            t,
                            mins,
                            int(rng.poisson(14 * lam * (1.3 - p * 0.1))) if played else 0,
                            int(rng.poisson(5 * lam)) if played else 0,
                            int(rng.poisson(3 * lam)) if played else 0,
                            int(rng.poisson(1.5 * lam)) if played else 0,
                            bool(p < 5),
                        )
                    )
    gdf = pl.DataFrame(
        games,
        schema=["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"],
        orient="row",
    )
    cols = ["game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "starter"]
    pdf = pl.DataFrame(rows, schema=cols, orient="row")
    static = pl.DataFrame(
        {"player_id": sorted(pdf["player_id"].unique().to_list()), "position": "G"}
    )
    return gdf, pdf, static, flagged


@pytest.fixture(scope="module")
def world() -> dict[str, object]:
    gdf, pdf, static, flagged = _league()
    played = S.played_frame(gdf, pdf)
    return {
        "games": gdf,
        "pgs": pdf,
        "static": static,
        "flagged": flagged,
        "played": played,
        "states": player_states(played),
        "info": S.game_info_of(gdf),
    }


def _late_game(w: dict[str, object]) -> tuple[str, int, int]:
    """A late game and a starter of the home team who is not OUT there."""
    gdf = w["games"]
    assert isinstance(gdf, pl.DataFrame)
    flagged = w["flagged"]
    assert isinstance(flagged, dict)
    for g, h in gdf.sort("game_date", descending=True).select("game_id", "home_team").rows():
        pid = int(h) * 100  # player 0: always a starter, ~34 min
        if pid not in flagged[g]:
            return str(g), pid, int(h)
    raise AssertionError("no candidate game")


def test_sitter_rows_tie_to_production_vacancy(world: dict[str, object]) -> None:
    w = world
    s = S.sitter_rows(w["played"], w["states"], w["flagged"], w["info"])  # type: ignore[arg-type]
    mine = s.group_by("game_id", "team_id").agg(
        pl.len().alias("n"), pl.col("min10").sum().alias("vm"), pl.col("v_pts").sum().alias("vp")
    )
    prod = vacated_features(w["played"], w["states"], w["flagged"], w["info"])  # type: ignore[arg-type]
    j = prod.join(mine, on=["game_id", "team_id"], how="full", coalesce=True)
    assert j.height == prod.height == mine.height > 50
    assert (j["n"].cast(pl.Int64) == j["n_out_rot"].cast(pl.Int64)).all()
    assert float((j["vm"] - j["vac_min"]).abs().max()) < 1e-9  # type: ignore[arg-type]
    assert float((j["vp"] - j["vac_pts"]).abs().max()) < 1e-9  # type: ignore[arg-type]


def test_scenario_equals_rebuild_with_sitter_on_report(world: dict[str, object]) -> None:
    """The defining property: a scenario row is byte-for-byte the production row with the sitter OUT."""
    w = world
    g, pid, team = _late_game(w)
    flagged = w["flagged"]
    assert isinstance(flagged, dict)
    base = build_features(w["games"], w["pgs"], w["static"], flagged, ELO)  # type: ignore[arg-type]
    with_out = {**flagged, g: set(flagged[g]) | {pid}}
    full = build_features(w["games"], w["pgs"], w["static"], with_out, ELO)  # type: ignore[arg-type]
    vac = S.scenario_vacancy(w["played"], w["states"], flagged, w["info"], [(g, pid)])  # type: ignore[arg-type]
    sitters = pl.DataFrame({"game_id": [g], "sitter_id": [pid], "team_id": [team]})
    scn = S.scenario_features(base.filter(pl.col("game_id") == g), vac, sitters)
    want = full.filter(
        (pl.col("game_id") == g) & (pl.col("team_id") == team) & (pl.col("player_id") != pid)
    )
    assert scn.height == want.height > 3
    got = scn.drop("sitter_id").sort("player_id")
    assert got.equals(want.sort("player_id"))
    # the change is only the vacancy columns, and it is an increase
    b = base.filter(pl.col("game_id") == g, pl.col("team_id") == team, pl.col("player_id") != pid)
    assert (
        got["n_out_rot"].cast(pl.Int64) == b.sort("player_id")["n_out_rot"].cast(pl.Int64) + 1
    ).all()
    assert (got["vac_min"] > b.sort("player_id")["vac_min"]).all()
    other = [c for c in base.columns if c not in S.SCN_COLS]
    assert got.select(other).equals(b.sort("player_id").select(other))


def test_opponent_vacancy_unchanged(world: dict[str, object]) -> None:
    w = world
    g, pid, team = _late_game(w)
    vac = S.scenario_vacancy(w["played"], w["states"], w["flagged"], w["info"], [(g, pid)])  # type: ignore[arg-type]
    prod = vacated_features(w["played"], w["states"], w["flagged"], w["info"])  # type: ignore[arg-type]
    opp = vac.filter(pl.col("team_id") != team)
    if opp.height:
        p = prod.filter((pl.col("game_id") == g) & (pl.col("team_id") != team))
        assert p.height == 1
        assert opp.select(S.SCN_COLS).equals(
            p.select(S.SCN_COLS).cast(opp.select(S.SCN_COLS).schema)
        )


def test_candidates_exclude_out_players_and_unreported_games(world: dict[str, object]) -> None:
    w = world
    g, pid, team = _late_game(w)
    flagged = dict(w["flagged"])  # type: ignore[call-overload]
    roster = pl.DataFrame({"game_id": [g] * 9, "player_id": [team * 100 + p for p in range(9)]})
    c = S.candidates(w["played"], w["states"], roster, flagged, w["info"])  # type: ignore[arg-type]
    assert pid in c["player_id"].to_list()
    assert (c["starter10"] >= S.REGULAR_STARTER10).all()
    assert not set(c["player_id"].to_list()) & {team * 100 + p for p in range(5, 9)}  # bench never
    c2 = S.candidates(w["played"], w["states"], roster, {**flagged, g: {pid}}, w["info"])  # type: ignore[arg-type]
    assert pid not in c2["player_id"].to_list()
    no_rep = {k: v for k, v in flagged.items() if k != g}
    c3 = S.candidates(w["played"], w["states"], roster, no_rep, w["info"])  # type: ignore[arg-type]
    assert c3.is_empty()


def test_planted_future_does_not_move_the_scenario(world: dict[str, object]) -> None:
    """The sitter's stats on and after the game date never reach the scenario."""
    w = world
    g, pid, _ = _late_game(w)
    gdf, pdf = w["games"], w["pgs"]
    assert isinstance(gdf, pl.DataFrame) and isinstance(pdf, pl.DataFrame)
    day = gdf.filter(pl.col("game_id") == g)["game_date"].item()
    later = gdf.filter(pl.col("game_date") >= day)["game_id"].to_list()
    planted = pdf.with_columns(
        pl.when(pl.col("game_id").is_in(later) & (pl.col("player_id") == pid))
        .then(pl.lit(99))
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    p2 = S.played_frame(gdf, planted)
    v1 = S.scenario_vacancy(w["played"], w["states"], w["flagged"], w["info"], [(g, pid)])  # type: ignore[arg-type]
    v2 = S.scenario_vacancy(p2, player_states(p2), w["flagged"], w["info"], [(g, pid)])  # type: ignore[arg-type]
    assert v1.sort("team_id").equals(v2.sort("team_id"))


def test_forecasts_paired_and_equal_to_production_with_sitter_out(world: dict[str, object]) -> None:
    w = world
    g, pid, team = _late_game(w)
    flagged = w["flagged"]
    assert isinstance(flagged, dict)
    gdf = w["games"]
    assert isinstance(gdf, pl.DataFrame)
    day = gdf.filter(pl.col("game_id") == g)["game_date"].item()
    base = build_features(gdf, w["pgs"], w["static"], flagged, ELO)  # type: ignore[arg-type]
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300)
    models = fit_for_date(base, day, cfg)
    ids = gdf.filter(pl.col("game_date") == day)["game_id"].to_list()
    roster = base.filter(pl.col("game_id").is_in(ids)).select("game_id", "player_id")
    out = S.slate_scenarios(gdf, w["pgs"], base, flagged, models, ids, roster)  # type: ignore[arg-type]
    assert out.height > 0
    assert set(out["stat"].unique().to_list()) == set(PROP_STATS)
    # opponents never appear; the sitter never appears as his own teammate
    assert (out["player_id"] != out["sitter_id"]).all()
    one = out.filter((pl.col("game_id") == g) & (pl.col("sitter_id") == pid))
    assert one.height and (one["team_id"] == team).all()
    # the scenario equals production's forecast with the sitter on the report
    full = build_features(gdf, w["pgs"], w["static"], {**flagged, g: set(flagged[g]) | {pid}}, ELO)  # type: ignore[arg-type]
    from nba.props.context_residual import QUANTILE_TAUS, predict_rows

    rows = full.filter(
        (pl.col("game_id") == g) & (pl.col("team_id") == team) & (pl.col("player_id") != pid)
    )
    r, mean, _ = predict_rows(models, rows, "pts", np.array(QUANTILE_TAUS))
    want = dict(zip(r["player_id"].to_list(), mean.tolist(), strict=True))
    got = dict(one.filter(pl.col("stat") == "pts").select("player_id", "scn_mean").rows())
    assert want.keys() == got.keys()
    assert max(abs(want[k] - got[k]) for k in want) < 1e-12
    # determinism
    again = S.slate_scenarios(gdf, w["pgs"], base, flagged, models, ids, roster)  # type: ignore[arg-type]
    assert again.equals(out)


def test_remove_mode_equals_rebuild_without_sitter(world: dict[str, object]) -> None:
    """The check's 'before the news' arm: a real OUT starter taken off the report equals a rebuild without him."""
    w = world
    g, pid, team = _late_game(w)
    flagged = w["flagged"]
    assert isinstance(flagged, dict)
    with_out = {**flagged, g: set(flagged[g]) | {pid}}
    real = build_features(w["games"], w["pgs"], w["static"], with_out, ELO)  # type: ignore[arg-type]
    before = build_features(w["games"], w["pgs"], w["static"], flagged, ELO)  # type: ignore[arg-type]
    vac = S.scenario_vacancy(w["played"], w["states"], with_out, w["info"], [(g, pid)], remove=True)  # type: ignore[arg-type]
    sitters = pl.DataFrame({"game_id": [g], "sitter_id": [pid], "team_id": [team]})
    got = S.scenario_features(real.filter(pl.col("game_id") == g), vac, sitters).drop("sitter_id")
    want = before.filter(
        (pl.col("game_id") == g) & (pl.col("team_id") == team) & (pl.col("player_id") != pid)
    )
    assert got.sort("player_id").equals(want.sort("player_id"))
