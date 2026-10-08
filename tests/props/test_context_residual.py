"""Tests for the context-residual props candidate (synthetic league, no real DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    CRPS_TAUS,
    bh_adjust,
    crps_from_quantiles,
    run_eval_frames,
)
from nba.props.context_residual import (
    ContextResidualConfig,
    asof_elo_margin,
    build_features,
    flagged_from_availability,
    player_states,
)
from nba.props.distributions import NormalDist
from nba.props.forward import recency_weighted_dist
from nba.props.metrics import crps_from_dist

ELO = {
    "k_factor": 10.0,
    "home_advantage_elo": 40.0,
    "season_carryover": 0.6,
    "mov_c": 3.7,
    "mov_div": 0.00067,
}


def _league(seed: int = 0, n_days: int = 260, n_teams: int = 6, per_team: int = 9):
    rng = np.random.default_rng(seed)
    start = dt.date(2022, 10, 20)
    games, rows = [], []
    gid = 0
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 2)
        season = d.year if d.month >= 8 else d.year - 1
        order = rng.permutation(n_teams)
        for k in range(0, n_teams, 2):
            h, a = int(order[k]), int(order[k + 1])
            gid += 1
            g = f"{gid:07d}"
            games.append(
                (g, d, season, h, a, int(rng.integers(95, 125)), int(rng.integers(95, 125)))
            )
            for t in (h, a):
                for p in range(per_team):
                    pid = t * 100 + p
                    played = rng.random() > (0.05 if p < 6 else 0.4)
                    base_min = 34 - 3 * p
                    mins = float(max(base_min + rng.normal(0, 3), 4)) if played else None
                    lam = max(mins or 0, 0) / 34.0
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
    pdf = pl.DataFrame(
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
    static = pl.DataFrame(
        {"player_id": sorted(pdf["player_id"].unique().to_list()), "position": "G"}
    )
    return gdf, pdf, static


def _played_sorted(pdf: pl.DataFrame, gdf: pl.DataFrame) -> pl.DataFrame:
    return (
        pdf.join(gdf.select(["game_id", "game_date", "season"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )


def test_recency_state_matches_forward_recency_mean_and_excludes_dnp() -> None:
    gdf, pdf, _ = _league()
    played = _played_sorted(pdf, gdf)
    st = player_states(played)
    pid = played["player_id"].to_numpy()
    i = int(np.where(pid == 3)[0][25])  # 26th played game of player 3
    prior = played.filter(pl.col("player_id") == 3)["pts"].to_numpy().astype(float)[:25]
    want = recency_weighted_dist(prior, "pts", 10.0)
    got = st["pre"][i, 1, 0]
    assert abs(got - want.mean()) < 1e-9
    assert (played["minutes"] > 0).all()  # DNP rows (NULL minutes) never enter


def test_state_uses_only_prior_games() -> None:
    gdf, pdf, _ = _league()
    played = _played_sorted(pdf, gdf)
    a = player_states(played)["pre"]
    mod = played.with_columns(
        pl.when(pl.col("game_date") == pl.col("game_date").max())
        .then(500)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    b = player_states(mod)["pre"]
    last = (played["game_date"] == played["game_date"].max()).to_numpy()
    assert np.allclose(
        a[last][:, :, :], b[last][:, :, :], equal_nan=True
    )  # own label never in own pre-state


def test_elo_margin_is_asof_no_leak() -> None:
    gdf, _, _ = _league()
    base = asof_elo_margin(gdf, ELO)
    last = gdf.sort(["game_date", "game_id"]).row(-1, named=True)
    mod = gdf.with_columns(
        pl.when(pl.col("game_id") == last["game_id"])
        .then(200)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    alt = asof_elo_margin(mod, ELO)
    assert base.equals(alt)  # the final game's own score cannot move its pre-game margin


def test_features_asof_vacated_usage_and_no_report_nulls() -> None:
    gdf, pdf, static = _league()
    g1 = gdf.sort(["game_date", "game_id"]).row(120, named=True)
    out_pid = g1["home_team"] * 100 + 0
    ts = dt.datetime.combine(g1["game_date"], dt.time(12, 0))
    ts_late = dt.datetime.combine(g1["game_date"], dt.time(21, 0))
    avail = pl.DataFrame(
        {
            "game_id": [g1["game_id"], g1["game_id"]],
            "player_id": [out_pid, g1["home_team"] * 100 + 1],
            "status": ["Out", "Out"],
            "as_of": [ts, ts_late],  # second row stamped after the tip-off proxy -> ignored
            "source": ["nba_official_report"] * 2,
        }
    )
    flagged, _ = flagged_from_availability(avail, gdf, ContextResidualConfig().report)
    assert flagged[g1["game_id"]] == {out_pid} or flagged == {}  # late-stamped row never used
    # strictly: the 12:00 snapshot is the latest usable one
    assert flagged[g1["game_id"]] == {out_pid}
    feats = build_features(gdf, pdf, static, flagged, ELO)
    sub = feats.filter(
        (pl.col("game_id") == g1["game_id"]) & (pl.col("team_id") == g1["home_team"])
    )
    assert sub["has_report"].to_list()[0] == 1 and sub["n_out_rot"].to_list()[0] == 1
    assert sub["vac_min"].to_list()[0] > 20  # starter-level minutes vacated
    other = feats.filter(pl.col("game_id") != g1["game_id"])
    assert other["has_report"].sum() == 0 and other["vac_min"].null_count() == other.height
    assert feats["n_prior"].min() == 0 and feats["rest_days"].null_count() < feats.height


def test_crps_matches_metrics_module_and_bh() -> None:
    m = np.array([10.0, 5.0])
    s = np.array([4.0, 2.0])
    y = np.array([12.0, 3.0])
    from scipy.stats import norm

    q = m[:, None] + s[:, None] * norm.ppf(CRPS_TAUS)[None, :]
    got = crps_from_quantiles(q, y)
    want = [crps_from_dist(NormalDist(a, b), float(c)) for a, b, c in zip(m, s, y, strict=True)]
    assert np.allclose(got, want, atol=1e-9)
    adj = bh_adjust(np.array([0.01, 0.04, 0.03, 0.5]))
    assert np.all(adj >= np.array([0.01, 0.04, 0.03, 0.5]) - 1e-12) and adj.max() <= 1.0


def test_walk_forward_end_to_end_small_and_season_2025_guard() -> None:
    gdf, pdf, static = _league(n_days=330)
    avail = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "source": pl.Utf8,
        }
    )
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300)
    res = run_eval_frames(
        gdf, pdf, static, avail, ELO, cfg, test_seasons=(2023,), stats=("pts", "reb"), n_boot=100
    )
    sm = res["summaries"]
    assert set(sm) == {"pts", "reb"}  # type: ignore[arg-type]
    oof = res["oof"]
    assert isinstance(oof, pl.DataFrame) and oof.height == sm["pts"]["n"] + sm["reb"]["n"]  # type: ignore[index]
    assert (oof["game_date"] > oof["made_with_data_through"]).all()  # trained strictly before
    assert len(oof["q_grid"][0]) == 19
    qg = np.array(oof["q_grid"].to_list())
    assert (np.diff(qg, axis=1) >= -1e-9).all()
    assert np.isfinite(sm["pts"]["crps_cand"]) and 0.0 <= sm["pts"]["cov80_cand"] <= 1.0  # type: ignore[index]
    bad = gdf.with_columns(pl.lit(2025).alias("season"))
    try:
        run_eval_frames(bad, pdf, static, avail, ELO, cfg, test_seasons=(2023,), stats=("pts",))
    except ValueError as e:
        assert "holdout" in str(e)
    else:
        raise AssertionError("season 2025 must be rejected")
