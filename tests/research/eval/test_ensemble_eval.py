# ruff: noqa: E501
"""ENSEMBLE harness: pure helpers, freeze guard, era probes, mixture identities, planted checks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import CRPS_TAUS, to_integer_support
from research.eval import ensemble_eval as E


def _doc(tmp: Path, status: str, body: str = "rule text\n") -> Path:
    p = tmp / "ENSEMBLE.md"
    p.write_text(
        f"# t\n\nStatus: {status} 2026\n{body}=== RESULTS BELOW ===\nresults edited later\n"
    )
    return p


def test_freeze_guard(tmp_path: Path) -> None:
    d = _doc(tmp_path, "DRAFT")
    with pytest.raises(SystemExit):
        E.require_frozen(None, False, d)
    assert E.require_frozen(None, True, d) == E.frozen_sha(d)
    f = _doc(tmp_path, "**FROZEN**")
    sha = E.frozen_sha(f)
    assert E.require_frozen(sha, False, f) == sha
    with pytest.raises(SystemExit):
        E.require_frozen("0" * 64, False, f)
    # text below the marker never changes the sha
    f.write_text(f.read_text() + "more results\n")
    assert E.frozen_sha(f) == sha


def test_ext19_monotone_clipped_and_matches_inside() -> None:
    rng = np.random.default_rng(1)
    q19 = np.sort(rng.gamma(2.0, 3.0, size=(40, 19)), axis=1)
    q = E.ext19(q19)
    assert q.shape == (40, 199) and (np.diff(q, axis=1) >= -1e-12).all() and (q >= 0).all()
    mid = np.argmin(np.abs(CRPS_TAUS - 0.5))
    assert np.allclose(q[:, mid], q19[:, 9], rtol=0.02)
    back = E.grid19(q)
    assert np.allclose(back, q19, atol=0.2 * q19.std())


def test_mixture_identities_and_support() -> None:
    rng = np.random.default_rng(2)
    qa = np.sort(rng.integers(0, 40, size=(30, 199)), axis=1)
    qb = np.sort(rng.integers(0, 40, size=(30, 199)), axis=1)
    assert np.array_equal(E.mix_integer_quantiles(qa, qb, np.ones(30)), qa)
    assert np.array_equal(E.mix_integer_quantiles(qa, qb, np.zeros(30)), qb)
    mid = E.mix_integer_quantiles(qa, qb, np.full(30, 0.5))
    assert (np.diff(mid, axis=1) >= 0).all()
    assert (mid >= np.minimum(qa, qb).min(axis=1, keepdims=True)).all()
    # identical components: mixture equals them at any weight
    same = E.mix_integer_quantiles(qa, qa, rng.random(30))
    assert np.array_equal(same, qa)
    # integer support stays integer
    assert np.array_equal(to_integer_support(mid.astype(float)), mid)


def test_survival_is_a_function_of_mu_sigma_only_and_monotone() -> None:
    mu = np.array([6.0, 12.0, 25.0, 34.0])
    sg = np.full(4, 6.0)
    g = E.survival_at(mu, sg)
    assert (np.diff(g) > 0).all() and ((g > 0) & (g <= 1)).all()
    assert np.allclose(g, E.survival_at(mu.copy(), sg.copy()))


def test_gate_score_ignores_actual_minutes_on_the_fixture_db() -> None:
    """Scramble the minutes of every game on/after the median date (and the games themselves are
    the targets): the as-of minutes path gives identical (mu, sigma) to every earlier row and to the
    same-game row of the scrambled games."""
    from nba.props.minutes import build_minutes_features, predict_minutes
    from tests.fixtures.loader import build_fixture_db

    def table(scramble: bool) -> pl.DataFrame:
        con = build_fixture_db(":memory:")
        ids = [r[0] for r in con.execute("select game_id from games").fetchall()]
        lst = ", ".join(f"'{i}'" for i in ids)
        for k in range(1, 25):  # replicate the three fixture games onto 24 later dates
            con.execute(
                f"insert into games select * replace (game_id || '_{k}' as game_id, "
                f"game_date + {3 * k} as game_date) from games where game_id in ({lst})"
            )
            con.execute(
                f"insert into player_game_stats select * replace (game_id || '_{k}' as game_id) "
                f"from player_game_stats where game_id in ({lst})"
            )
        dates = con.execute("select game_id, game_date from games order by game_date").pl()
        cut = dates["game_date"][dates.height // 2]
        if scramble:
            con.execute(
                "update player_game_stats set minutes = 3.0 where minutes is not null and game_id in "
                f"(select game_id from games where game_date >= DATE '{cut}')"
            )
        mf = build_minutes_features(con, use_game_context=False)
        dists = predict_minutes(mf)
        return mf.select("game_id", "player_id", "game_date").with_columns(
            pl.Series("mu", [d.mu for d in dists]), pl.Series("sg", [d.sigma for d in dists])
        )

    a, b = table(False), table(True)
    j = a.join(b, on=["game_id", "player_id"], suffix="_b")
    cut = a.sort("game_date")["game_date"][a.height // 2]
    early = j.filter(pl.col("game_date") < cut)
    assert early.height > 20
    assert np.allclose(early["mu"].to_numpy(), early["mu_b"].to_numpy())
    assert np.allclose(early["sg"].to_numpy(), early["sg_b"].to_numpy())
    # the first scrambled date's rows see only earlier games: also unchanged (same-game rows)
    first = j.filter(pl.col("game_date") == j.filter(pl.col("game_date") >= cut)["game_date"].min())
    assert np.allclose(first["mu"].to_numpy(), first["mu_b"].to_numpy())


def _era_data(shift: float, n: int = 3000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    season = np.repeat([2019, 2020, 2022, 2023, 2024], n)
    gid = np.array([f"G{i:06d}" for i in range(len(season))])
    x = (rng.random((len(season), 6)) < 0.3).astype(np.float32)
    x[season <= 2020, 0] = (rng.random((season <= 2020).sum()) < 0.3 + shift).astype(np.float32)
    return x, season, gid


def test_p_in_probe_flags_a_planted_pattern_and_not_a_clean_one() -> None:
    clean = E.era_probe(*(lambda t: (t[0], t[1], t[2]))(_era_data(0.0)), "lgbm", "pin")
    assert clean["flagged"] is False
    planted = E.era_probe(*_era_data(0.4), "lgbm", "pin")
    assert planted["flagged"] is True and planted["limit"] >= E.PIN_FLOOR


def test_p_emb_limit_is_relative_to_the_placebo_gap() -> None:
    rng = np.random.default_rng(4)
    season = np.repeat([2019, 2020, 2022, 2023, 2024], 1500)
    z = rng.normal(size=(len(season), 8)).astype(np.float32)
    z[season <= 2020, 0] += 1.5  # strong history-only direction
    gid = np.array([f"G{i:06d}" for i in range(len(season))])
    r = E.era_probe(z, season, gid, "logreg", "pemb")
    assert (
        r["flagged"] is True and abs(r["limit"] - (r["2022v2024"]["auc"] + E.PEMB_MARGIN)) < 1e-12
    )
    z2 = rng.normal(size=(len(season), 8)).astype(np.float32)
    assert E.era_probe(z2, season, gid, "logreg", "pemb")["flagged"] is False


def test_team_share_and_threshold_log_loss() -> None:
    d = np.array([-1.0, -1.0, -3.0, 0.5])
    t = np.array([1, 1, 2, 3])
    r = E.team_share(d, t)
    assert r["top_team"] == 2 and abs(r["top_share"] - 3.0 / 4.5) < 1e-9
    qi = np.tile(np.arange(199) // 10, (3, 1))
    ll = E.thr_ll(qi, np.array([5.0, 25.0, 0.0]), np.array([3.0, 3.0, 3.0]))
    assert (ll >= 0).all() and ll[0] == ll[1] and ll[2] > ll[0]


def test_learned_gate_uses_only_earlier_rows() -> None:
    """Planted future: scrambling outcomes dated on/after a block start leaves that block's weights unchanged."""
    import pandas as pd

    from research.stack.data import build_stack_data
    from research.stack.router import SoftmaxGate, WalkForwardConfig, walk_forward

    rng = np.random.default_rng(5)
    n = 900
    date = pd.to_datetime("2023-11-01") + pd.to_timedelta(np.sort(rng.integers(0, 200, n)), "D")
    y = rng.gamma(3.0, 3.0, n)
    base = np.sort(rng.gamma(3.0, 3.0, (n, 19)), axis=1)
    keys = pd.DataFrame(
        {"game_id": [f"G{i}" for i in range(n)], "player_id": 1, "game_date": date, "season": 2023}
    )

    def run(yy: np.ndarray) -> np.ndarray:
        frames = {
            "a": keys.assign(q_grid=list(base)),
            "b": keys.assign(q_grid=list(base + rng.normal(0, 0.1, base.shape))),
        }
        out = keys[["game_id", "player_id"]].assign(y=yy)
        ctx = keys[["game_id", "player_id"]].assign(x=np.arange(n) % 7)
        data = build_stack_data("pts", frames, out, ctx, max_season=2024)
        return walk_forward(
            data, SoftmaxGate, WalkForwardConfig(block_days=30, min_train_rows=100)
        ).weights

    w1 = run(y)
    cut = date[n // 2]
    y2 = y.copy()
    y2[date >= cut] = rng.gamma(3.0, 9.0, (date >= cut).sum())
    rng = np.random.default_rng(
        5
    )  # same candidate noise draw order is irrelevant: compare weights pre-cut
    w2 = run(y2)
    pre = np.asarray(date < cut - pd.Timedelta(days=30))
    ok = ~np.isnan(w1[:, 0]) & ~np.isnan(w2[:, 0]) & pre
    assert ok.sum() > 50
    assert np.allclose(w1[ok], w2[ok], atol=0.05)
