"""MINUTES_HAZARD (docs/prereg/MINUTES_HAZARD.md): label builders, as-of discipline, fit, sim."""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.props.context_residual import CRPS_TAUS
from research.eval.minutes_hazard_eval import FROZEN_SHA, frozen_sha
from research.minutes import hazard as hz


def _keys(n: int = 3) -> pl.DataFrame:
    return pl.DataFrame({"game_id": ["g1"] * n, "player_id": list(range(1, n + 1))})


def test_frozen_rule_hash_matches_the_committed_constant() -> None:
    assert frozen_sha() == FROZEN_SHA


def test_build_y_overlaps_and_overtime() -> None:
    stints = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g1"],
            "team_id": [1, 1, 1],
            "period": [1, 1, 5],
            "start_clock": [720.0, 630.0, 300.0],
            "end_clock": [630.0, 0.0, 240.0],
            "players": [[1, 2], [1, 3], [2, 3]],
        }
    )
    y = hz.build_y(stints, _keys())
    assert y.shape == (3, hz.NSLOT)
    # player 1: on 0-90s then 90s-720s of period 1 => all 12 slots fully on
    assert np.allclose(y[0, :12], 1.0)
    # player 2: first 90 s => slot 0 full, slot 1 half; then OT first minute
    assert np.isclose(y[1, 0], 1.0) and np.isclose(y[1, 1], 0.5) and np.isclose(y[1, 2], 0.0)
    assert np.isclose(y[1, hz.NREG], 1.0)
    # player 3: 90 s .. 720 s of period 1 and the OT minute
    assert np.isclose(y[2, 0], 0.0) and np.isclose(y[2, 1], 0.5) and np.isclose(y[2, 2], 1.0)
    assert np.isclose(y[2, hz.NREG], 1.0)
    # minutes = sum of Y
    assert np.isclose(y[1].sum(), 1.0 + 0.5 + 1.0)


def test_build_fouls_parsed_and_fallback() -> None:
    keys = _keys(2)
    y = np.zeros((2, hz.NSLOT), dtype=np.float32)
    y[:, :4] = 1.0
    ev = pl.DataFrame(
        {
            "game_id": ["g1", "g1"],
            "player_id": [1, 1],
            "period": [1, 1],
            "remaining": [650.0, 200.0],  # elapsed 70 s (slot 1) and 520 s (slot 8)
        }
    )
    pf = np.array([2.0, 2.0])
    f, ok = hz.build_fouls(ev, keys, pf, y)
    assert ok.tolist() == [True, False]
    assert f[0, 1] == 0 and f[0, 2] == 1 and f[0, 8] == 1 and f[0, 9] == 2
    # fallback: pf=2 spread over four on-court slots, cumulative before each slot
    assert np.allclose(f[1, :5], [0.0, 0.5, 1.0, 1.5, 2.0])


def test_build_margins_orients_to_home_and_counts_overtime() -> None:
    games = pl.DataFrame({"game_id": ["g1"], "home_team": [10]})
    poss = pl.DataFrame(
        {
            "game_id": ["g1"] * 3,
            "poss_idx": [0, 1, 2],
            "period": [1, 1, 5],
            "clock_start": [700.0, 600.0, 300.0],
            "off_team": [10, 20, 10],
            "score_diff": [0, -3, 4],
        }
    )
    idx, m, n_ot = hz.build_margins(poss, games)
    row = m[idx["g1"]]
    assert row[0] == 0.0 and row[1] == 0.0
    assert row[2] == 3.0  # possession 1 (away on offence, -3) starts at 120 s -> home +3
    assert row[hz.NREG] == 4.0 and n_ot[idx["g1"]] == 1


def _league(n_players: int = 12, n_games: int = 30, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = n_players * n_games
    pid = np.tile(np.arange(n_players), n_games).astype(np.int64)
    gid = np.repeat(np.arange(n_games), n_players).astype(np.int64) + 1000
    date = gid - 1000 + 19000
    tid = (pid % 2).astype(np.int64)
    season = np.zeros(n, dtype=np.int64)
    role = (pid % 3).astype(np.int64)
    y = (rng.random((n, hz.NSLOT)) < 0.6).astype(np.float32)
    minutes = y.sum(axis=1).astype(np.float64)
    pf = rng.integers(0, 6, n).astype(np.float64)
    return {
        "pid": pid,
        "tid": tid,
        "season": season,
        "gid": gid,
        "date": date,
        "minutes": minutes,
        "pf": pf,
        "role": role,
        "y": y,
    }


def _asof(d: dict[str, np.ndarray]) -> hz.AsOf:
    return hz.asof_inputs(
        d["pid"],
        d["tid"],
        d["season"],
        d["gid"],
        d["date"],
        d["minutes"],
        d["pf"],
        d["role"],
        d["y"],
    )


def test_asof_inputs_ignore_tonight_and_the_future() -> None:
    d = _league()
    base = _asof(d)
    rng = np.random.default_rng(1)
    # same game: edit the rows of games 1015 only
    same = d["gid"] == 1015
    d2 = {k: v.copy() for k, v in d.items()}
    d2["y"][same] = rng.random((int(same.sum()), hz.NSLOT)).astype(np.float32)
    d2["minutes"][same] = 99.0
    d2["pf"][same] = 6.0
    a2 = _asof(d2)
    assert np.array_equal(base.habit_logit[same], a2.habit_logit[same])
    assert np.array_equal(base.fnum40[same], a2.fnum40[same])
    assert np.array_equal(base.fden40[same], a2.fden40[same])
    # future: edit everything on/after game 1020; earlier rows are byte-identical
    fut = d["gid"] >= 1020
    d3 = {k: v.copy() for k, v in d.items()}
    d3["y"][fut] = 1.0 - d3["y"][fut]
    d3["minutes"][fut] = 0.0
    d3["pf"][fut] = 0.0
    a3 = _asof(d3)
    assert np.array_equal(base.habit_logit[~fut], a3.habit_logit[~fut])
    assert np.array_equal(base.fnum40[~fut], a3.fnum40[~fut])
    # and the edit does reach later rows (the planted change is visible)
    assert not np.array_equal(base.habit_logit[d["gid"] >= 1022], a3.habit_logit[d["gid"] >= 1022])


def test_habit_is_the_prior_mean_shrunk_to_the_profile() -> None:
    d = _league(n_players=2, n_games=4)
    d["tid"][:] = 0
    d["role"][:] = 0
    a = _asof(d)
    row = np.nonzero((d["pid"] == 0) & (d["gid"] == 1002))[0][0]
    prior = np.nonzero((d["pid"] == 0) & (d["gid"] < 1002))[0]
    s = d["y"][prior, : hz.NREG].astype(float).sum(axis=0)
    assert a.k_habit[row] == 2
    h = 1.0 / (1.0 + np.exp(-a.habit_logit[row, : hz.NREG].astype(float)))
    prof = d["y"][(d["gid"] < 1002), : hz.NREG].astype(float).sum(axis=0) / (
        (d["gid"] < 1002).sum()
    )
    want = np.clip((s + hz.HABIT_PC * prof) / (2 + hz.HABIT_PC), hz.P_EPS, 1 - hz.P_EPS)
    assert np.allclose(h, want, atol=1e-5)


def test_fit_recovers_foul_out_and_blowout_directions() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    logit = np.zeros((n, hz.NSLOT), dtype=np.float32)
    fouls = rng.integers(0, 7, (n, hz.NSLOT)).astype(np.float32)
    margin = rng.normal(0, 14, (n, hz.NSLOT)).astype(np.float32)
    role = rng.integers(0, 3, n)
    z = np.where(fouls >= 6, -6.0, 0.0) + np.where(np.abs(margin) >= 20, -1.0, 0.0)
    y = (rng.random((n, hz.NSLOT)) < 1 / (1 + np.exp(-z))).astype(np.float32)
    valid = np.ones((n, hz.NSLOT), dtype=bool)
    p = hz.fit_hazard(logit, y, valid, fouls, margin, role, np.zeros(n))
    assert p.foul[6, 0] < p.foul[0, 0] - 3.0
    assert p.margin[2, 1, 0] < p.margin[0, 1, 0] - 0.5


def _params() -> hz.HazardParams:
    return hz.HazardParams(
        np.zeros((hz.N_FOUL, hz.N_PER)),
        np.zeros((hz.N_BUCKET, hz.N_PER, hz.N_ROLE)),
        0.0,
        1,
        1,
        True,
    )


def _sim(mode: str, **kw: object) -> np.ndarray:
    logit = np.full((4, hz.NSLOT), 0.4, dtype=np.float32)
    args = dict(
        role=np.array([0, 1, 2, 2]),
        vac=np.zeros(4),
        rate=np.full(4, 0.06),
        mu_home=2.0,
        sd=12.0,
        params=_params(),
        rng_margin=np.random.default_rng([0, 1, 1]),
        rng_play=np.random.default_rng([0, 1, 2]),
        n_paths=300,
    )
    args.update(kw)
    return hz.simulate_game(mode, logit, **args)  # type: ignore[arg-type]


def test_sim_with_null_state_matches_the_habit_mean_and_is_deterministic() -> None:
    a = _sim("h1")
    b = _sim("h1")
    assert np.array_equal(a, b)
    expect = hz.NREG / (1 + np.exp(-0.4))
    assert abs(a.mean() - expect) < 0.6  # a little extra from the rare overtime
    h0 = _sim("h0")
    assert abs(h0.mean() - expect) < 0.5


def test_h1_ignores_the_actual_margin_path_but_h2_uses_it() -> None:
    m_a = np.zeros(hz.NSLOT)
    m_b = np.full(hz.NSLOT, 30.0)
    assert np.array_equal(_sim("h1", margin_actual=m_a), _sim("h1", margin_actual=m_b))
    p = _params()
    p.margin[2, :, :] = -3.0  # 20+ margins sharply cut the probability of being on
    x = _sim("h2", params=p, margin_actual=m_a, n_ot_actual=0)
    y = _sim("h2", params=p, margin_actual=m_b, n_ot_actual=0)
    assert y.mean() < x.mean() - 5.0


def test_foul_state_removes_fouled_out_players() -> None:
    p = _params()
    p.foul[hz.FOUL_CAP, :] = -12.0
    hi = _sim("h1", params=p, rate=np.full(4, 0.5))
    lo = _sim("h1", params=p, rate=np.full(4, 0.0))
    assert hi.mean() < lo.mean() - 5.0


def test_quantile_grid_shape_and_clip() -> None:
    q = hz.quantile_grid(np.array([[70.0, 80.0, 90.0]]), CRPS_TAUS)
    assert q.shape == (1, len(CRPS_TAUS)) and q.max() == 60.0
