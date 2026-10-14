# ruff: noqa: E501
"""Context-conditioned copula sizing: planted effects are recovered, the closed forms are right, and the
2023-25 season wall holds. Synthetic parquet fixtures only (no database, no network, no live path)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy import stats

from nba.parlay import context_corr as C
from nba.parlay import context_data as D
from nba.parlay import context_money as M
from nba.props.context_residual import PROP_STATS, ContextResidualConfig

N_GAMES = 500
N_PER_TEAM = 8


def _write_fixture(
    tmp: Path, rho_ctx: float, rho_ref: float, seed: int = 0, bias_ctx: float = 0.0
) -> None:
    """Games with two teams; same-player (pts, ast) correlation ``rho_ctx`` when the team's leader is OUT, else
    ``rho_ref``. ``bias_ctx`` shifts every z in the leader-out context (a pure marginal bias)."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(N_GAMES):
        gid = f"002200{g:04d}"
        for ti, team in enumerate((1, 2)):
            lead = int(rng.random() < 0.4)
            vac = 30.0 if lead else float(rng.choice([0.0, 10.0]))
            for k in range(N_PER_TEAM):
                rows.append(
                    {
                        "game_id": gid,
                        "player_id": 100 * team + k,
                        "team_id": team,
                        "season": 2020 + g % 3,
                        "game_date": "2021-01-01",
                        "is_home_f": 1 - ti,
                        "n_prior": 20,
                        "min10": 25.0,
                        "min40": 25.0,
                        "starter10": 0.5,
                        "exp_margin": float(rng.normal(0, 5)) * (1 if ti == 0 else -1),
                        "abs_margin": 3.0,
                        "r20_pts_for": 110.0,
                        "opp_r20_pts_for": 110.0,
                        "has_report": 1,
                        "n_out_rot": int(vac > 0),
                        "vac_min": vac,
                        **{f"vac_{s}": 0.0 for s in C.STATS},
                        **{f"m10_{s}": float(10 - k) for s in C.STATS},
                        **{f"lead_out_{s}": lead for s in C.STATS},
                        "home_team": 1,
                        "home_pts": 110,
                        "away_pts": 105,
                    }
                )
    ctx = pl.DataFrame(rows)
    ctx.write_parquet(tmp / "context.parquet")
    lead = ctx["lead_out_pts"].to_numpy()
    n = ctx.height
    rho = np.where(lead == 1, rho_ctx, rho_ref)
    e = rng.standard_normal((n, 4))
    z = {s: e[:, i] for i, s in enumerate(C.STATS)}
    z["ast"] = rho * z["pts"] + np.sqrt(1 - rho**2) * e[:, 1]
    for s in C.STATS:
        zz = z[s] + (bias_ctx * lead if s in ("pts", "ast") else 0.0)
        p = np.full(n, 0.5)
        hit = (zz > 0).astype(np.int8)
        pl.DataFrame(
            {
                "game_id": ctx["game_id"],
                "player_id": ctx["player_id"],
                "z": zz,
                "hit": hit,
                "p_line": p,
            }
        ).write_parquet(tmp / f"pit_{s}.parquet")


def test_planted_context_shift_is_recovered(tmp_path: Path) -> None:
    _write_fixture(tmp_path, rho_ctx=0.55, rho_ref=0.15)
    panel = C.load_panel(tmp_path)
    classes = [c for c in C.pair_classes() if c.name == "same_player:pts-ast"]
    rows = C.run_classes(panel, n_boot=200, classes=classes)
    cell = next(c for c in rows[0]["cells"] if c["ctx"] == "lead_out")
    assert cell["corr_z"]["excludes_0"]
    assert cell["corr_z"]["ctx"] == pytest.approx(0.55, abs=0.08)
    assert cell["corr_z"]["ref"] == pytest.approx(0.15, abs=0.08)
    assert cell["phi"]["diff"] > 0.15
    # a context that carries no planted effect (blowout is constant 3.0 here -> no cell at all) never invents one
    assert all(c["ctx"] != "blowout" for c in rows[0]["cells"])


def test_no_effect_means_difference_ci_spans_zero(tmp_path: Path) -> None:
    _write_fixture(tmp_path, rho_ctx=0.3, rho_ref=0.3)
    panel = C.load_panel(tmp_path)
    classes = [c for c in C.pair_classes() if c.name == "same_player:pts-ast"]
    cell = next(
        c
        for c in C.run_classes(panel, n_boot=200, classes=classes)[0]["cells"]
        if c["ctx"] == "lead_out"
    )
    lo, hi = cell["corr_z"]["ci"]
    assert lo < 0 < hi


def test_marginal_bias_inside_a_context_is_not_counted_as_dependence(tmp_path: Path) -> None:
    """Everybody under-forecast in the context (a mean shift) must not raise the stratum-centred correlation."""
    _write_fixture(tmp_path, rho_ctx=0.3, rho_ref=0.3, bias_ctx=0.8)
    panel = C.load_panel(tmp_path)
    classes = [c for c in C.pair_classes() if c.name == "same_player:pts-ast"]
    cell = next(
        c
        for c in C.run_classes(panel, n_boot=200, classes=classes)[0]["cells"]
        if c["ctx"] == "lead_out"
    )
    assert cell["corr_z"]["ctx"] == pytest.approx(cell["corr_z"]["ref"], abs=0.06)


def test_placebo_shuffle_kills_a_planted_effect(tmp_path: Path) -> None:
    _write_fixture(tmp_path, rho_ctx=0.55, rho_ref=0.15)
    panel = C.shuffle_labels(C.load_panel(tmp_path), seed=3)
    classes = [c for c in C.pair_classes() if c.name == "same_player:pts-ast"]
    cell = next(
        c
        for c in C.run_classes(panel, n_boot=200, classes=classes)[0]["cells"]
        if c["ctx"] == "lead_out"
    )
    assert abs(cell["corr_z"]["diff"]) < 0.08


def test_teammate_pairs_are_unordered_and_cross_stat_ordered(tmp_path: Path) -> None:
    _write_fixture(tmp_path, rho_ctx=0.3, rho_ref=0.3)
    panel = C.load_panel(tmp_path)
    same = C.pair_table(panel, C.PairClass("t", "teammate", "pts", "pts"))
    cross = C.pair_table(panel, C.PairClass("t", "teammate", "pts", "ast", ordered=True))
    per_team = N_PER_TEAM * (N_PER_TEAM - 1)
    assert len(same.g) == N_GAMES * 2 * per_team // 2
    assert len(cross.g) == N_GAMES * 2 * per_team
    opp = C.pair_table(panel, C.PairClass("o", "opponent", "pts", "pts"))
    assert len(opp.g) == N_GAMES * N_PER_TEAM * N_PER_TEAM


def test_sizing_panel_refuses_seasons_after_2022(tmp_path: Path) -> None:
    _write_fixture(tmp_path, rho_ctx=0.3, rho_ref=0.3)
    ctx = pl.read_parquet(tmp_path / "context.parquet").with_columns(
        pl.when(pl.col("game_id") == "0022000000")
        .then(2023)
        .otherwise(pl.col("season"))
        .alias("season")
    )
    ctx.write_parquet(tmp_path / "context.parquet")
    with pytest.raises(ValueError, match="after 2022"):
        C.load_panel(tmp_path)
    with pytest.raises(ValueError, match="2022"):
        D.assert_sizing_seasons(pl.DataFrame({"season": [2022, 2023]}))
    D.assert_sizing_seasons(pl.DataFrame({"season": [2020, 2022]}))
    with pytest.raises(ValueError, match="2022"):
        D._side(Path("/nonexistent.duckdb"), (2022, 2023))
    with pytest.raises(ValueError, match="2022"):
        D.walk_stat(pl.DataFrame(), "pts", ContextResidualConfig(), (2023,))


# --------------------------------------------------------------------------- money arithmetic


def test_sheppard_matches_the_bivariate_normal() -> None:
    rho = 0.4
    phi = 2.0 / math.pi * math.asin(rho)
    assert M.sheppard_rho(phi) == pytest.approx(rho)
    # corr of two median-split indicators of a bivariate normal
    p = stats.multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]]).cdf([0, 0])
    assert (p - 0.25) / 0.25 == pytest.approx(phi, abs=1e-6)


def test_parlay_joint_closed_forms_match_scipy() -> None:
    r = [0.3, 0.2, 0.1]
    cov = np.array([[1, r[0], r[1]], [r[0], 1, r[2]], [r[1], r[2], 1]])
    p3 = stats.multivariate_normal(mean=np.zeros(3), cov=cov).cdf(np.zeros(3))
    assert M.parlay_joint(r) == pytest.approx(float(p3), abs=3e-5)
    p2 = stats.multivariate_normal(mean=[0, 0], cov=[[1, 0.3], [0.3, 1]]).cdf([0, 0])
    assert M.parlay_joint([0.3]) == pytest.approx(float(p2), abs=1e-7)
    assert M.parlay_joint([0.0]) == 0.25
    assert M.parlay_joint([0.0, 0.0, 0.0]) == 0.125
    with pytest.raises(ValueError):
        M.parlay_joint([0.1, 0.2])


def test_required_lift_and_kl() -> None:
    assert M.required_lift(0.129) == pytest.approx(0.1481, abs=1e-4)
    assert M.required_lift(0.0) == 0.0
    with pytest.raises(ValueError):
        M.required_lift(1.0)
    assert M.kl_bernoulli(0.3, 0.3) == 0.0
    assert M.kl_bernoulli(0.31, 0.3) > 0
    assert M.kl_bernoulli(0.31, 0.3) == pytest.approx(0.01**2 / (2 * 0.3 * 0.7), rel=0.05)
    assert M.fair_odds(0.25) == 4.0
    assert M.fee_share(0.28, 0.0143) == pytest.approx(0.05107, abs=1e-4)


def test_money_scenarios_from_synthetic_cells() -> None:
    def cell(ctx: str, c: float, r: float) -> dict[str, object]:
        return {
            "ctx": ctx,
            "phi": {"ctx": c, "ref": r, "diff": c - r, "ci": [c - r - 0.02, c - r + 0.02]},
        }

    def row(cls: str, pooled: float, cells: list[dict[str, object]]) -> dict[str, object]:
        return {"class": cls, "all": {"phi": {"est": pooled}}, "cells": cells}

    rows = [
        row("same_player:pts-ast", 0.15, [cell("lead_out", 0.18, 0.14)]),
        row("same_player:pts-reb", 0.24, [cell("lead_out", 0.25, 0.23)]),
        row("same_player:ast-reb", 0.16, [cell("lead_out", 0.18, 0.157)]),
        row("teammate_cross:pts_i-ast_j", 0.02, [cell("lead_x_compete", 0.05, 0.02)]),
        row("teammate:ast-ast", 0.0, [cell("compete", -0.025, -0.001)]),
        row("own_margin:reb", 0.04, [cell("fav", 0.02, 0.07)]),
    ]
    out = M.money_scenarios(rows)  # type: ignore[arg-type]
    assert len(out) == len(M.SCENARIOS)
    two = out[0]
    assert two["legs"] == 2 and two["p_ctx"] > two["p_ref"]
    assert two["fair_odds_ctx"] < two["fair_odds_pooled"]
    assert out[3]["rel_ctx_vs_ref"] < 0  # competing assisters: less likely than the reference
    assert all(o["log_loss_gain_ctx_segment"] >= 0 for o in out)


# --------------------------------------------------------------------------- data builders


def test_median_line_picks_the_threshold_nearest_one_half() -> None:
    # integer pmf of Y: P(Y=0..4) = .1 .2 .3 .3 .1 -> P(Y>=t) = 1, .9, .7, .4, .1 -> nearest to .5 is t = 3 (.4)? |.7-.5| = .2 vs |.4-.5| = .1
    cdf = np.array([[0.1, 0.3, 0.6, 0.9, 1.0]])
    t, p = D.median_line(cdf)
    assert int(t[0]) == 3
    assert float(p[0]) == pytest.approx(0.4)


def test_leader_out_flags_use_per_36_rate_among_rotation_players() -> None:
    feats = pl.DataFrame(
        {
            "game_id": ["g", "g", "g"],
            "team_id": [1, 1, 1],
            "n_prior": [20, 20, 20],
            "min40": [30.0, 30.0, 30.0],
            "min10": [30.0, 30.0, 30.0],
            **{f"m10_{s}": [10.0, 10.0, 10.0] for s in PROP_STATS},
        }
    )
    out = pl.DataFrame(
        {
            "game_id": ["g"],
            "team_id": [1],
            "min40": [30.0],
            "min10": [30.0],
            **{f"v_{s}": [20.0] for s in PROP_STATS},
        }
    )
    flags = D.leader_out_flags(feats, out)
    assert (
        flags["lead_out_pts"][0] == 1
    )  # the OUT player's rate (20) beats every player who played (10)
    out2 = out.with_columns([pl.lit(5.0).alias(f"v_{s}") for s in PROP_STATS])
    assert D.leader_out_flags(feats, out2)["lead_out_pts"][0] == 0


def test_the_sizing_modules_hold_no_order_or_credential_code() -> None:
    root = Path(__file__).resolve().parents[2] / "nba" / "parlay"
    for name in ("context_data.py", "context_corr.py", "context_money.py", "context_report.py"):
        text = (root / name).read_text().lower()
        for bad in (
            "requests.",
            "httpx",
            "urllib",
            "api_key",
            "apikey",
            "place_order",
            "/orders",
            "portfolio",
        ):
            assert bad not in text, (name, bad)
