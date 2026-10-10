# ruff: noqa: E501
"""Tests for the joint game-set pipeline: as-of features, PIT targets, leg mapping, the Colab job.

Everything runs on synthetic data (a small invented league, no real DB, no network). The Colab job
module is imported by path and run in its CPU ``smoke`` budget, including a forced-crash / resume
sequence that must reproduce an uninterrupted run exactly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from scipy import stats

from nba.parlay.game_model import GameModelParams
from nba.parlay.joint import GameCtx, leg_hits
from nba.parlay.legs import Leg
from nba.parlay.qdist import QuantileGridDist
from research.eval import joint_game_set_eval as ev
from research.features import game_sets as gsm

REPO = Path(__file__).resolve().parents[3]
JOB_DIR = REPO / "research" / "colab" / "jobs" / "joint_game_set"


# --------------------------------------------------------------------------- synthetic league


def synth_league(
    seed: int = 0, n_days: int = 14, n_teams: int = 6, per_team: int = 9
) -> dict[str, Any]:
    """Three seasons of an invented league. Returns games, pgs, static, avail, oof, elo, params."""
    rng = np.random.default_rng(seed)
    grows: list[dict[str, Any]] = []
    prows: list[dict[str, Any]] = []
    gid = 0
    for season in (2022, 2023, 2024):
        start = date(season, 10, 20)
        for d in range(n_days):
            order = rng.permutation(n_teams)
            for k in range(0, n_teams, 2):
                h, a = int(order[k]), int(order[k + 1])
                gid += 1
                game_id = f"002{season % 100:02d}{gid:05d}"
                grows.append(
                    {
                        "game_id": game_id,
                        "game_date": start + timedelta(days=d),
                        "season": season,
                        "home_team": 1610612700 + h,
                        "away_team": 1610612700 + a,
                        "home_pts": int(rng.integers(95, 125)),
                        "away_pts": int(rng.integers(95, 125)),
                    }
                )
                for t in (h, a):
                    for j in range(per_team):
                        played = rng.random() < 0.8 or j < 4
                        mins = float(rng.uniform(10, 36)) if played else None
                        lam = 0.3 * (mins or 0.0)
                        prows.append(
                            {
                                "game_id": game_id,
                                "player_id": 1000 * (t + 1) + j,
                                "team_id": 1610612700 + t,
                                "minutes": mins,
                                "starter": j < 5 if played else False,
                                "pts": int(rng.poisson(lam)) if played else 0,
                                "reb": int(rng.poisson(lam / 3)) if played else 0,
                                "ast": int(rng.poisson(lam / 4)) if played else 0,
                                "fg3m": int(rng.poisson(lam / 6)) if played else 0,
                            }
                        )
    games = pl.DataFrame(grows).with_columns(pl.col("season").cast(pl.Int32))
    pgs = pl.DataFrame(prows)
    pids = sorted(set(pgs["player_id"].to_list()))
    static = pl.DataFrame(
        {
            "player_id": pids,
            "position": [("Guard", "Forward", "Center")[i % 3] for i in range(len(pids))],
            "height_in": [float(72 + i % 12) for i in range(len(pids))],
            "draft_year": [2015 + i % 8 if i % 5 else None for i in range(len(pids))],
            "draft_pick": [float(1 + i % 60) if i % 5 else None for i in range(len(pids))],
        }
    )
    avail = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "source": pl.Utf8,
        }
    )
    qrows: list[dict[str, Any]] = []
    gd = dict(zip(games["game_id"].to_list(), games["season"].to_list(), strict=True))
    for r in pgs.iter_rows(named=True):
        if r["minutes"] is None or gd[r["game_id"]] < 2023:
            continue
        for st in gsm.STATS:
            m = max(0.5, 0.3 * r["minutes"] / {"pts": 1, "reb": 3, "ast": 4, "fg3m": 6}[st])
            qrows.append(
                {
                    "game_id": r["game_id"],
                    "player_id": r["player_id"],
                    "target": st,
                    "q_grid": [
                        float(max(0.0, v)) for v in np.linspace(m - 2 * m**0.5, m + 2 * m**0.5, 19)
                    ],
                }
            )
    oof = pl.DataFrame(qrows)
    elo = pl.DataFrame({"game_id": games["game_id"], "p": rng.uniform(0.2, 0.8, games.height)})
    params = GameModelParams(
        margin_b=0.3,
        margin_s=2.5,
        margin_sd=12.0,
        total_a=100.0,
        total_c=0.5,
        total_sd=18.0,
        league_total=222.0,
        halflife=20.0,
        prior_k=8.0,
        n_margin=100,
        n_total=100,
        fit_seasons=(2022, 2023),
    )
    return {
        "games": games,
        "pgs": pgs,
        "static": static,
        "avail": avail,
        "oof": oof,
        "elo": elo,
        "params": params,
    }


@pytest.fixture(scope="module")
def league() -> dict[str, Any]:
    return synth_league()


def _feat_frame(tok: pl.DataFrame) -> pl.DataFrame:
    return tok.select("game_id", "player_id", *gsm.TOK_FEATURES).sort("game_id", "player_id")


# --------------------------------------------------------------------------- leakage / as-of


def test_features_are_strictly_asof_planted_future(league: dict[str, Any]) -> None:
    games, pgs, static, avail = league["games"], league["pgs"], league["static"], league["avail"]
    cut = date(2023, 10, 28)
    base = gsm.token_frame(games, pgs, static, avail)
    rng = np.random.default_rng(5)
    gd = games.select("game_id", "game_date")
    p2 = pgs.join(gd, on="game_id")
    late = (p2["game_date"] >= cut).to_numpy()
    n = pgs.height
    pgs2 = pgs.with_columns(
        pl.Series(
            "minutes", np.where(late, rng.uniform(1, 48, n), pgs["minutes"].fill_null(np.nan))
        ).fill_nan(None),
        pl.Series("pts", np.where(late, rng.integers(0, 60, n), pgs["pts"])),
        pl.Series("reb", np.where(late, rng.integers(0, 25, n), pgs["reb"])),
        pl.Series("starter", np.where(late, rng.random(n) < 0.5, pgs["starter"])),
    )
    pert = gsm.token_frame(games, pgs2, static, avail)
    a, b = _feat_frame(base), _feat_frame(pert)
    gdates = base.select("game_id", "game_date").unique()
    upto = gdates.filter(pl.col("game_date") <= cut)["game_id"].to_list()
    # features of every row dated on or before the cut are identical: nothing at/after the cut leaks in
    assert_frame_equal(
        a.filter(pl.col("game_id").is_in(upto)), b.filter(pl.col("game_id").is_in(upto))
    )
    after = [g for g in a["game_id"].unique().to_list() if g not in set(upto)]
    assert not a.filter(pl.col("game_id").is_in(after)).equals(
        b.filter(pl.col("game_id").is_in(after))
    )


def test_features_do_not_reveal_who_played(league: dict[str, Any]) -> None:
    """Flipping played / DNP and every stat of one game must not change ANY token feature of that
    game (the set must not leak the played flag, minutes or stat line)."""
    games, pgs, static, avail = league["games"], league["pgs"], league["static"], league["avail"]
    target = games.sort("game_date")["game_id"][len(games) // 2]
    base = gsm.token_frame(games, pgs, static, avail)
    is_g = pl.col("game_id") == target
    pgs2 = pgs.with_columns(
        pl.when(is_g)
        .then(pl.when(pl.col("minutes").is_null()).then(25.0).otherwise(None))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
        pl.when(is_g).then(pl.col("pts") + 9).otherwise(pl.col("pts")).alias("pts"),
        pl.when(is_g).then(~pl.col("starter")).otherwise(pl.col("starter")).alias("starter"),
    )
    pert = gsm.token_frame(games, pgs2, static, avail)
    a = _feat_frame(base).filter(pl.col("game_id") == target)
    b = _feat_frame(pert).filter(pl.col("game_id") == target)
    assert a.height > 10
    assert_frame_equal(a, b)
    # sanity: the labels did change
    la = base.filter(pl.col("game_id") == target).sort("player_id")["played"]
    lb = pert.filter(pl.col("game_id") == target).sort("player_id")["played"]
    assert la.to_list() != lb.to_list()


def test_report_status_respects_pretip_cutoff(league: dict[str, Any]) -> None:
    games, pgs, static = league["games"], league["pgs"], league["static"]
    g = games.sort("game_date").row(30, named=True)
    rows = pgs.filter(pl.col("game_id") == g["game_id"])["player_id"].to_list()
    d0 = datetime.combine(g["game_date"], datetime.min.time())
    avail = pl.DataFrame(
        {
            "game_id": [g["game_id"]] * 3,
            "player_id": [rows[0], rows[1], rows[2]],
            "status": ["out", "out", "questionable"],
            "as_of": [d0 + timedelta(hours=11), d0 + timedelta(hours=21), d0 + timedelta(hours=11)],
            "source": ["nba_official_report"] * 3,
        }
    ).with_columns(pl.col("as_of").cast(pl.Datetime("us")), pl.col("player_id").cast(pl.Int64))
    tok = gsm.token_frame(games, pgs, static, avail).filter(pl.col("game_id") == g["game_id"])
    by = {r["player_id"]: r for r in tok.iter_rows(named=True)}
    assert by[rows[0]]["own_status_ord"] == 5.0  # reported before the cutoff
    assert by[rows[1]]["own_status_ord"] == 0.0  # stamped after the tip-off proxy: ignored
    assert by[rows[2]]["own_status_ord"] == 3.0
    assert by[rows[3]]["has_report"] == 1.0


def test_season_2025_is_refused(league: dict[str, Any]) -> None:
    g = league["games"]
    bad = pl.concat(
        [
            g,
            g.head(1).with_columns(
                pl.lit(2025).cast(pl.Int32).alias("season"), pl.lit("0022500001").alias("game_id")
            ),
        ]
    )
    with pytest.raises(ValueError, match="holdout"):
        gsm.token_frame(bad, league["pgs"], league["static"], league["avail"])


# --------------------------------------------------------------------------- PIT / assembly


def test_grid_pit_bounds_match_quantile_dist() -> None:
    q = list(np.linspace(2.0, 20.0, 19))
    d = QuantileGridDist.from_grid(q)
    for y in (0.0, 3.0, 10.0, 21.0, 40.0):
        assert gsm.grid_pit_bounds(q, y) == d.pit_bounds(y)


def test_randomized_z_spreads_out_of_grid_outcomes() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    lo = np.ones(n)
    hi = np.ones(n)  # outcome beyond the top of the grid: lo == hi == 1
    z = gsm.randomized_z(lo, hi, rng.random(n), 1e-6, rng.random(n))
    assert np.isfinite(z).all()
    assert z.min() >= stats.norm.ppf(1 - gsm.TAIL_W) - 1e-6  # in the outer tail window ...
    assert len(np.unique(np.round(z, 4))) > 1000  # ... but not a point mass at the clip
    z0 = gsm.randomized_z(np.zeros(n), np.zeros(n), rng.random(n), 1e-6, rng.random(n))
    assert z0.max() <= stats.norm.ppf(gsm.TAIL_W) + 1e-6
    # an ordinary interval stays uniform inside [lo, hi]
    zi = gsm.randomized_z(np.full(n, 0.4), np.full(n, 0.6), rng.random(n), 1e-6, rng.random(n))
    assert zi.min() >= stats.norm.ppf(0.4) - 1e-6 and zi.max() <= stats.norm.ppf(0.6) + 1e-6


def test_normal_pit_bounds_fold_mass_below_zero() -> None:
    lo, hi = gsm.normal_pit_bounds(np.array([0.0, 5.0]), np.array([3.0, 3.0]), np.array([2.0, 2.0]))
    assert lo[0] == 0.0 and hi[0] == pytest.approx(stats.norm.cdf(-1.25))
    assert lo[1] < hi[1]


@pytest.fixture(scope="module")
def built(league: dict[str, Any]) -> gsm.GameSets:
    return gsm.build_game_sets(
        league["games"],
        league["pgs"],
        league["static"],
        league["avail"],
        league["oof"],
        league["elo"],
        league["params"],
    )


def test_build_game_sets_shapes_masks_and_alignment(
    built: gsm.GameSets, league: dict[str, Any]
) -> None:
    a = built.arrays
    g = len(a["game_id"])
    assert g == league["games"].height
    assert a["tok_x"].shape == (g, gsm.N_TOK, len(gsm.TOK_FEATURES))
    assert a["game_x"].shape == (g, len(gsm.GAME_FEATURES))
    assert np.isfinite(a["tok_x"]).all() and np.isfinite(a["game_x"]).all()
    assert (a["tok_mask"].sum(1) == 18).all()  # 9 players per team in the invented league
    # padded slots carry nothing
    assert not a["tok_obs"][~a["tok_mask"]].any() and not a["tok_played"][~a["tok_mask"]].any()
    # observed dims exist only for played players, only where a marginal exists (2022 needs history)
    assert (a["tok_played"][a["tok_obs"].any(-1)] == 1).all()
    assert a["tok_obs"][a["season"] >= 2023][a["tok_played"][a["season"] >= 2023] == 1].all()
    # the game token is aligned with the right game (regression: joins do not preserve row order)
    elo = dict(zip(league["elo"]["game_id"].to_list(), league["elo"]["p"].to_list(), strict=True))
    p = league["params"]
    for i in (0, 7, 40, g - 1):
        pp = np.clip(elo[str(a["game_id"][i])], 1e-6, 1 - 1e-6)
        assert a["mu_margin"][i] == pytest.approx(p.margin_b + p.margin_s * np.log(pp / (1 - pp)))
    # rows of the exported arrays carry the right labels
    row = league["games"].filter(pl.col("game_id") == str(a["game_id"][3])).row(0, named=True)
    assert a["margin"][3] == row["home_pts"] - row["away_pts"]
    assert a["total"][3] == row["home_pts"] + row["away_pts"]
    # home tokens first, away tokens after
    assert (a["tok_side"][:, :9][a["tok_mask"][:, :9]] == 1).all()
    assert (a["tok_side"][:, gsm.MAX_PER_TEAM :][a["tok_mask"][:, gsm.MAX_PER_TEAM :]] == 0).all()
    assert built.meta["seasons"] == [2022, 2023, 2024]


def test_game_sets_roundtrip(built: gsm.GameSets, tmp_path: Path) -> None:
    built.save(tmp_path)
    back = gsm.GameSets.load(tmp_path)
    assert set(back.arrays) == set(built.arrays)
    np.testing.assert_array_equal(back.arrays["tok_x"], built.arrays["tok_x"])
    assert back.meta["tok_features"] == list(gsm.TOK_FEATURES)


# --------------------------------------------------------------------------- leg mapping / eval helpers


def _ctx() -> GameCtx:
    q = tuple(float(v) for v in np.linspace(4.0, 30.0, 19))
    return GameCtx(
        "g",
        10,
        20,
        3.0,
        12.0,
        222.0,
        18.0,
        {(7, "pts"): QuantileGridDist(q), (8, "pts"): QuantileGridDist(q)},
        {7: 10, 8: 20},
        {},
    )


def test_leg_dim_reproduces_leg_hits_for_every_leg_type() -> None:
    ctx = _ctx()
    u = np.random.default_rng(1).random(5000)
    z = stats.norm.ppf(u)
    legs = [
        Leg("g", 0, "win", 0.0, "yes", 10),
        Leg("g", 0, "win", 0.0, "no", 20),
        Leg("g", 0, "spread", 6.5, "yes", 10),
        Leg("g", 0, "spread", 3.5, "yes", 20),
        Leg("g", 0, "total", 224.5, "yes", None),
        Leg("g", 0, "total", 214.5, "no", None),
        Leg("g", 7, "pts", 15.0, "yes", 10),
        Leg("g", 8, "pts", 25.0, "no", 20),
    ]
    for leg in legs:
        dim, dr, zthr = ev.leg_dim(leg, ctx, {7: 0, 8: 1})
        yes = dr * (z - zthr) > 0
        got = yes if leg.side == "yes" else ~yes
        np.testing.assert_array_equal(got, leg_hits(leg, u, ctx))
        assert dim in (ev.DIM_MARGIN, ev.DIM_TOTAL, 0, 4)


def test_parlay_mix_labels() -> None:
    ctx = _ctx()
    a = Leg("g", 7, "pts", 15.0, "yes", 10)
    b = Leg("g", 7, "reb", 5.0, "yes", 10)
    c = Leg("g", 8, "pts", 15.0, "yes", 20)
    w = Leg("g", 0, "win", 0.0, "yes", 10)
    assert ev.parlay_mix([a, b], ctx)["same_player"]
    assert ev.parlay_mix([a, c], ctx)["opponents"]
    assert ev.parlay_mix([a, w], ctx)["mix"] == "prop_plus_result"
    ctx2 = GameCtx("g", 10, 20, 0, 1, 0, 1, {}, {7: 10, 9: 10}, {})
    m = ev.parlay_mix([a, Leg("g", 9, "pts", 12.0, "yes", 10)], ctx2)
    assert m["teammates"] and not m["opponents"] and m["mix"] == "props_only"


def test_ece_and_rule() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.2, 0.8, 20000)
    y = (rng.random(20000) < p).astype(int)
    assert ev.ece(p, y) < 0.02
    assert ev.ece(np.clip(p + 0.2, 0, 0.999), y) > 0.15
    d = ev.ece_diff_bootstrap(
        p, np.clip(p + 0.2, 0, 0.999), y, rng.integers(0, 500, 20000).astype(str), n_boot=200
    )
    assert d["diff"] < -0.1 and d["ci_hi"] < 0
    ok = ev.apply_rule(-0.001, -0.002, 0.004, 0.01, 0.0)
    assert ok.keep
    assert not ev.apply_rule(0.001, -0.002, 0.004, 0.01, 0.0).keep  # R1 CI includes 0
    assert not ev.apply_rule(-0.001, -0.002, 0.006, 0.01, 0.0).keep  # R2 ECE worse
    assert not ev.apply_rule(-0.001, -0.002, 0.004, 0.06, 0.0).keep  # R3 CRPS worse
    assert ev.normal_crps(0.0, 0.0, 1.0) == pytest.approx(
        2 * stats.norm.pdf(0) - 1 / np.sqrt(np.pi)
    )


# --------------------------------------------------------------------------- job module


def _load_job() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "jgs_job_under_test", JOB_DIR / "joint_game_set.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def job() -> ModuleType:
    torch = pytest.importorskip("torch")
    torch.set_num_threads(
        1
    )  # torch + lightgbm each ship an OpenMP runtime; one thread avoids the clash
    return _load_job()


def make_stage(path: Path, seed: int = 0, g: int = 66, t: int = 8, f: int = 6) -> Path:
    """A synthetic staged input folder with a real shared-latent dependence between players."""
    rng = np.random.default_rng(seed)
    path.mkdir(parents=True, exist_ok=True)
    season = np.repeat([2022, 2023, 2024], g // 3).astype(np.int16)
    dates = (19000 + np.arange(g)).astype(np.int32)
    n_valid = rng.integers(6, t + 1, g)
    mask = np.arange(t)[None, :] < n_valid[:, None]
    tok_x = (rng.normal(size=(g, t, f)) * mask[..., None]).astype(np.float32)
    played = mask & (rng.random((g, t)) > 0.2)
    state = rng.normal(size=g)
    z = (0.6 * state[:, None, None] + 0.8 * rng.normal(size=(g, t, 4))).astype(np.float32)
    mid = np.clip(stats.norm.cdf(z), 0.02, 0.98)
    obs = played[..., None] & (rng.random((g, t, 4)) > 0.05)
    lo = np.where(obs, mid - 0.01, 0.0).astype(np.float32)
    hi = np.where(obs, mid + 0.01, 0.0).astype(np.float32)
    zfix = np.where(obs, stats.norm.ppf(mid), 0.0).astype(np.float32)
    zm = (0.7 * state + 0.7 * rng.normal(size=g)).astype(np.float64)
    zt = rng.normal(size=g)
    arrays = {
        "tok_x": tok_x,
        "tok_mask": mask,
        "tok_pid": np.where(mask, 1000 + np.arange(t)[None, :], -1).astype(np.int64),
        "tok_team": np.where(mask, 1, -1).astype(np.int64),
        "tok_side": (np.arange(t)[None, :] < t // 2).repeat(g, 0).astype(np.int8),
        "tok_played": played.astype(np.int8),
        "tok_src": obs.any(-1).astype(np.int8),
        "tok_y": np.zeros((g, t, 4), np.float32),
        "tok_pit_lo": lo,
        "tok_pit_hi": hi,
        "tok_obs": obs,
        "tok_z": zfix,
        "game_id": np.array([f"0022{i:06d}" for i in range(g)]),
        "game_x": rng.normal(size=(g, 4)).astype(np.float32),
        "game_date": dates,
        "season": season,
        "home_team": np.full(g, 1, np.int64),
        "margin": 12.0 * zm + 3.0,
        "total": 18.0 * zt + 222.0,
        "mu_margin": np.full(g, 3.0),
        "sd_margin": np.full(g, 12.0),
        "mu_total": np.full(g, 222.0),
        "sd_total": np.full(g, 18.0),
        "z_margin": zm,
        "z_total": zt,
    }
    np.savez_compressed(path / "game_sets.npz", **arrays)
    names = ["is_home", "play20"] + [f"f{i}" for i in range(f - 2)]
    (path / "game_sets_meta.json").write_text(
        json.dumps({"tok_features": names, "built_at": "test"})
    )
    idx24 = np.nonzero(season == 2024)[0]
    parl, legs, sing, games = [], [], [], []
    pid_n = 0
    for gi in idx24:
        slots = np.argwhere(obs[gi])
        for _ in range(2):
            sel = slots[rng.choice(len(slots), 2, replace=False)]
            for pos, (s, k) in enumerate(sel):
                legs.append(
                    {
                        "parlay_id": pid_n,
                        "leg_pos": pos,
                        "game_idx": int(gi),
                        "dim": int(s * 4 + k),
                        "dir": 1,
                        "zthr": float(rng.normal() * 0.3),
                        "side_no": int(rng.random() < 0.3),
                        "p_marg": 0.5,
                        "y_leg": 1,
                        "leg_type": "player",
                    }
                )
            parl.append(
                {
                    "parlay_id": pid_n,
                    "game_idx": int(gi),
                    "game_id": str(arrays["game_id"][gi]),
                    "n_legs": 2,
                    "kind": "has_player",
                    "y": int(rng.random() < 0.3),
                    "p_independence": 0.25,
                    "p_gaussian": 0.26,
                    "p_t": 0.26,
                    "p_gaussian_hi": 0.26,
                    "n_player_legs": 2,
                    "n_game_legs": 0,
                    "mix": "props_only",
                    "teammates": True,
                    "opponents": False,
                    "same_player": False,
                }
            )
            pid_n += 1
        for _ in range(3):
            s, k = slots[rng.integers(len(slots))]
            sing.append(
                {
                    "single_id": len(sing),
                    "game_idx": int(gi),
                    "dim": int(s * 4 + k),
                    "zthr": float(rng.normal() * 0.4),
                    "p_marg": 0.5,
                    "y": int(rng.random() < 0.5),
                    "stat": "pts",
                }
            )
        games.append(
            {
                "game_idx": int(gi),
                "game_id": str(arrays["game_id"][gi]),
                "nll_copula": 40.0,
                "nll_indep": 42.0,
                "crps_margin_normal": 6.0,
                "crps_total_normal": 8.0,
            }
        )
    pl.DataFrame(parl).write_parquet(path / "eval_parlays.parquet")
    pl.DataFrame(legs).write_parquet(path / "eval_legs.parquet")
    pl.DataFrame(sing).write_parquet(path / "eval_singles.parquet")
    pl.DataFrame(games).write_parquet(path / "eval_games.parquet")
    (path / "eval_meta.json").write_text("{}")
    return path


def _run_job(
    job: ModuleType,
    stage: Path,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash: str | None = None,
) -> Path:
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(stage / "game_sets.npz"))
    monkeypatch.setenv("NBA_ARTIFACT_ROOT", str(root / "artifacts"))
    if crash:
        monkeypatch.setenv("NBA_FORCE_CRASH_AT", crash)
    else:
        monkeypatch.delenv("NBA_FORCE_CRASH_AT", raising=False)
    job.main()
    return sorted((root / "artifacts").glob("*"))[-1]


def test_mixture_logpdf_matches_scipy(job: ModuleType) -> None:
    torch = pytest.importorskip("torch")
    from scipy.stats import multivariate_normal

    job.C.torch = torch
    job.C.device = "cpu"
    rng = np.random.default_rng(0)
    b, m, d, r = 3, 2, 7, 3
    x = rng.normal(size=(b, d))
    mask = (rng.random((b, d)) > 0.3).astype(np.float64)
    mu = rng.normal(size=(b, m, d)) * 0.3
    dd = rng.uniform(0.3, 1.2, size=(b, m, d))
    L = rng.normal(size=(b, m, d, r)) * 0.4
    logpi = np.log(np.array([[0.3, 0.7]] * b))
    got = job.mixture_logpdf(
        *(torch.tensor(v, dtype=torch.float64) for v in (x, mask, mu, dd, L, logpi))
    ).numpy()
    for i in range(b):
        o = mask[i] > 0
        comps = []
        for k in range(m):
            cov = np.diag(dd[i, k, o] ** 2) + L[i, k, o] @ L[i, k, o].T
            comps.append(
                np.log(np.exp(logpi[i, k])) + multivariate_normal(mu[i, k, o], cov).logpdf(x[i, o])
            )
        assert got[i] == pytest.approx(np.logaddexp.reduce(comps), rel=1e-8, abs=1e-8)


def test_sampler_recovers_mean_and_covariance(job: ModuleType) -> None:
    torch = pytest.importorskip("torch")
    job.C.torch = torch
    job.C.device = "cpu"
    rng = np.random.default_rng(1)
    d, r = 5, 2
    L = torch.tensor(rng.normal(size=(1, d, r)) * 0.6, dtype=torch.float32)
    dd = torch.tensor(rng.uniform(0.5, 1.0, size=(1, d)), dtype=torch.float32)
    mu = torch.tensor(rng.normal(size=(1, d)), dtype=torch.float32)
    p = {"mu": mu, "d": dd, "L": L, "logpi": torch.zeros(1)}
    gen = torch.Generator().manual_seed(0)
    z = job.sample_z(p, 200000, gen).numpy()
    cov = np.diag(dd[0].numpy() ** 2) + L[0].numpy() @ L[0].numpy().T
    np.testing.assert_allclose(z.mean(0), mu[0].numpy(), atol=0.02)
    np.testing.assert_allclose(np.cov(z.T), cov, atol=0.03)
    # the analytic mixture marginal agrees with the samples
    dims = torch.tensor([0, 3])
    thr = torch.tensor([0.2, -0.4])
    sf = job.mix_sf(p, dims, thr).numpy()
    emp = np.array([(z[:, 0] > 0.2).mean(), (z[:, 3] > -0.4).mean()])
    np.testing.assert_allclose(sf, emp, atol=0.01)


def test_model_is_permutation_equivariant_and_pad_invariant(job: ModuleType) -> None:
    torch = pytest.importorskip("torch")
    job.C.torch = torch
    job.C.device = "cpu"
    cfg = dict(d_model=32, layers=2, heads=4, ff=2, dropout=0.3, M=3, R=2, init_seed=0)
    model = job.make_model(cfg, 6, 4).eval()
    g = torch.Generator().manual_seed(0)
    b, t = 2, 8
    x = torch.randn(b, t, 6, generator=g)
    mask = torch.ones(b, t, dtype=torch.bool)
    mask[:, 6:] = False
    side = torch.randint(0, 2, (b, t), generator=g)
    src = torch.ones(b, t, dtype=torch.long)
    gx = torch.randn(b, 4, generator=g)
    with torch.no_grad():
        o1 = model(x, mask, side, src, gx)
        perm = torch.tensor([3, 1, 0, 5, 2, 4, 6, 7])
        o2 = model(x[:, perm], mask[:, perm], side[:, perm], src[:, perm], gx)
        # changing padded tokens does not change valid outputs
        x3 = x.clone()
        x3[:, 6:] = 99.0
        o3 = model(x3, mask, side, src, gx)
    np.testing.assert_allclose(o1["logpi"].numpy(), o2["logpi"].numpy(), atol=1e-5)
    np.testing.assert_allclose(o1["play"][:, perm].numpy(), o2["play"].numpy(), atol=1e-5)
    np.testing.assert_allclose(o1["play"][:, :6].numpy(), o3["play"][:, :6].numpy(), atol=1e-5)
    np.testing.assert_allclose(o1["logpi"].numpy(), o3["logpi"].numpy(), atol=1e-5)


def test_model_inputs_exclude_labels(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The played flag, PIT bounds and stats are targets only: changing them cannot change outputs."""
    torch = pytest.importorskip("torch")
    stage = make_stage(tmp_path / "stage")
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(stage / "game_sets.npz"))
    job.setup()
    job.load()
    assert set(job.MODEL_INPUT_KEYS) == {"X", "mask", "side", "src", "GX"}
    model = job.make_model(
        dict(d_model=32, layers=1, heads=4, ff=2, dropout=0.3, M=3, R=2, init_seed=0),
        job.C.data["X"].shape[-1],
        job.C.data["GX"].shape[-1],
    ).eval()
    idx = torch.arange(6)
    a = model(**job.batch_inputs(job.C.data, idx))
    for k in ("played", "lo", "hi", "z", "zm", "zt"):
        job.C.data[k] = (
            job.C.data[k] * 0 + 1 if job.C.data[k].dtype != torch.bool else ~job.C.data[k]
        )
    b = model(**job.batch_inputs(job.C.data, idx))
    for k in ("mu", "d", "L", "logpi", "play"):
        np.testing.assert_array_equal(a[k].detach().numpy(), b[k].detach().numpy())


def test_zero_model_reproduces_independence_plumbing(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With mu=0, d=1, L=0 the staged leg/threshold plumbing must give exactly the product of the
    staged marginals (singles) and a sampled product for parlays."""
    torch = pytest.importorskip("torch")
    stage = make_stage(tmp_path / "stage")
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(stage / "game_sets.npz"))
    job.setup()
    job.load()
    job.prepare_eval_lookup()
    ch = np.array(sorted(job.C.legs_by_game))[:5]
    d_ = job.C.D

    def zero() -> dict[str, Any]:
        return {
            "mu": torch.zeros(1, d_),
            "d": torch.ones(1, d_),
            "L": torch.zeros(1, d_, 2),
            "logpi": torch.zeros(1),
            "play": torch.zeros(job.C.T),
        }

    par, sing, game, _ = job.predict_chunk([None], [[zero() for _ in ch]], ch, 0, 100000, "")
    leg = job.C.legs
    for r in par:
        rows = leg[leg["parlay_id"] == r["parlay_id"]]
        p = 1.0
        for _, lg in rows.iterrows():
            pyes = 1.0 - stats.norm.cdf(lg["zthr"])
            p *= 1.0 - pyes if lg["side_no"] else pyes
        assert r["p_set_full"] == pytest.approx(p, abs=0.01)
        assert r["p_set_copula"] == pytest.approx(p, abs=0.01)
    s = job.C.sing.set_index("single_id")
    for r in sing:
        assert r["p_set_full"] == pytest.approx(
            1.0 - stats.norm.cdf(s.loc[r["single_id"], "zthr"]), abs=1e-5
        )
    for r in game:
        assert r["nll_set"] == pytest.approx(r["nll_ind"], abs=1e-3)


def test_job_smoke_forced_crash_and_resume_reproduces_uninterrupted(
    job: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stage = make_stage(tmp_path / "stage")
    clean = _run_job(job, stage, tmp_path / "clean", monkeypatch)
    out = capsys.readouterr().out
    assert "DEVICE:" in out and "expected runtime" in out
    metrics = json.loads((clean / "metrics.json").read_text())
    assert metrics["complete"] is True and metrics["touches_holdout"] is False
    assert not (clean / "metrics_partial.json").exists()  # metrics.json is written last
    for f in (
        "best_config.json",
        "search_log.json",
        "training_log.json",
        "data_summary.json",
        "weights.pt",
        "parlay_preds.parquet",
        "single_preds.parquet",
        "game_preds.parquet",
        "play_preds.parquet",
    ):
        assert (clean / f).exists(), f
    # crash at every checkpointed stage, one after another, then finish
    root = tmp_path / "crashy"
    crashes = [
        "epoch_ckpt=2",
        "trial_done=1",
        "final_done=1",
        "pred_chunk=1",
        "wf_fit_done=1",
        "wf_block_done=1",
    ]
    for c in crashes:
        with pytest.raises(job.ForcedCrash):
            _run_job(job, stage, root, monkeypatch, crash=c)
        # small artifacts are already on disk after the first stages
        assert list((root / "artifacts").glob("*/data_summary.json"))
    capsys.readouterr()
    resumed = _run_job(job, stage, root, monkeypatch)
    log = capsys.readouterr().out
    assert "RESUMED" in log
    m2 = json.loads((resumed / "metrics.json").read_text())
    assert m2["complete"] is True
    assert m2["best_cfg"] == metrics["best_cfg"]
    for f in ("parlay_preds", "single_preds", "game_preds", "play_preds"):
        a = pl.read_parquet(clean / f"{f}.parquet").sort(
            pl.read_parquet(clean / f"{f}.parquet").columns[0]
        )
        b = pl.read_parquet(resumed / f"{f}.parquet").sort(
            pl.read_parquet(resumed / f"{f}.parquet").columns[0]
        )
        assert a.columns == b.columns and a.height == b.height
        for c in a.columns:
            if a[c].dtype in (pl.Float32, pl.Float64):
                np.testing.assert_allclose(a[c].to_numpy(), b[c].to_numpy(), rtol=1e-6, atol=1e-6)
    # the pulled-run scorer works on these artifacts and produces a report with the three rules
    res = ev.score_run(clean, stage, tmp_path / "score", n_boot=100)
    assert res["verdict"] in ("KEEP", "NOT KEPT")
    assert (tmp_path / "score" / "report.md").exists()
    assert set(res["rules"]) == {
        "R1_joint_logloss_vs_gaussian",
        "R2_single_leg_ece",
        "R3_game_crps_noninferior",
    }


def test_job_refuses_season_2025(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = make_stage(tmp_path / "stage")
    with np.load(stage / "game_sets.npz") as z:
        arr = {k: z[k] for k in z.files}
    arr["season"] = arr["season"].copy()
    arr["season"][-1] = 2025
    np.savez_compressed(stage / "game_sets.npz", **arr)
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(stage / "game_sets.npz"))
    job.setup()
    with pytest.raises(ValueError, match="holdout"):
        job.load()


def test_notebook_in_sync_and_job_yaml_valid() -> None:
    spec = importlib.util.spec_from_file_location("jgs_build_nb", JOB_DIR / "build_notebook.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    on_disk = json.loads((JOB_DIR / "joint_game_set.ipynb").read_text())
    assert on_disk == mod.build(), (
        "run: uv run python research/colab/jobs/joint_game_set/build_notebook.py"
    )
    from research.colab.jobs import load_job

    job_def = load_job("joint_game_set")
    assert not job_def.touches_holdout and job_def.preregistration_id is None
    assert "metrics.json" in job_def.artifacts
    assert job_def.inputs[0].path.endswith("game_sets.npz")
    assert (REPO / job_def.notebook.relative_to(REPO)).exists()
