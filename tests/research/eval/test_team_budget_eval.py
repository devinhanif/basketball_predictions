# ruff: noqa: E501
"""TEAM_BUDGET harness: freeze refusal, lock ownership, as-of means, the constraint inside the arms, the pass rule."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig, to_integer_support
from research.eval import team_budget_eval as T


def test_frozen_doc_matches_the_committed_constant_and_refuses_edits(tmp_path: Path) -> None:
    T.require_frozen()  # the real doc at the frozen sha
    doc = tmp_path / "doc.md"
    doc.write_text(Path(T.DOC).read_text() + "")
    T.require_frozen(doc)
    doc.write_text("# edited rule\n=== RESULTS BELOW ===\n")
    with pytest.raises(RuntimeError):
        T.require_frozen(doc)
    with pytest.raises(RuntimeError):
        T.require_frozen(Path(T.DOC), sha="")


def test_cli_refuses_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(T, "FROZEN_FULL", "0" * 64)
    for cmd in ("gate", "arms", "attack", "run"):
        with pytest.raises(RuntimeError):
            T.main([cmd])


def test_heavy_lock_writes_pid_and_removes_only_its_own(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    with T.heavy_lock(lock):
        assert (lock / "pid").read_text().strip().isdigit()
    assert not lock.exists()
    with T.heavy_lock(lock):
        (lock / "pid").write_text("1")  # someone else took over: we must not delete it
    assert lock.exists()


def test_heavy_lock_waits_then_times_out(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    lock.mkdir()
    with pytest.raises(TimeoutError), T.heavy_lock(lock, retry_s=0.01, max_wait_s=0.05):
        pass
    assert lock.exists()  # never removed by a waiter


def test_asof_cum_mean_is_strict_and_default_before_history() -> None:
    d = np.array([1, 1, 2, 3, 5], dtype=np.int64)
    v = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    q = np.array([1, 2, 3, 6], dtype=np.int64)
    out = T.asof_cum_mean(d, v, q, default=-1.0)
    assert out[0] == -1.0  # nothing strictly earlier than day 1
    assert out[1] == 15.0  # same-day rows of day 2 not seen, both day-1 rows are
    assert out[2] == 20.0
    assert out[3] == 30.0
    planted = v.copy()
    planted[3:] = 999.0
    out2 = T.asof_cum_mean(d, planted, q, default=-1.0)
    assert np.array_equal(out[:3], out2[:3])  # future values never reach earlier queries
    assert T.asof_cum_mean(d, v, q, default=-1.0, min_n=4)[2] == -1.0


def test_ratio_factor_clips_and_floors_the_denominator() -> None:
    r = T.ratio_factor(np.array([40.0, 5.0, 20.0]), np.array([20.0, 20.0, 0.0]), np.ones(3))
    assert np.allclose(r, [1.25, 0.8, 1.25])
    assert np.allclose(
        T.ratio_factor(np.array([40.0]), np.array([20.0]), np.ones(1), gamma=0.5), 1.25**0.5
    )


def test_flag_off_scale_reproduces_the_integer_grid_and_stays_monotone() -> None:
    rng = np.random.default_rng(0)
    g = np.sort(rng.gamma(3.0, 3.0, size=(50, 199)), axis=1)
    assert np.array_equal(T.scale_to_int(g, np.ones(50)), to_integer_support(g).astype(np.int16))
    q = T.scale_to_int(g, rng.uniform(0.8, 1.25, 50))
    assert (np.diff(q.astype(int), axis=1) >= 0).all()


def test_permute_within_keeps_group_marginals_and_breaks_the_link() -> None:
    df = pl.DataFrame(
        {
            "team_id": [1] * 6 + [2] * 6,
            "season": [2023] * 12,
            "a": np.arange(12.0),
            "b": np.arange(12.0) * 10,
        }
    )
    out = T.permute_within(df, ["a", "b"], ["team_id", "season"], 0)
    assert sorted(out.filter(pl.col("team_id") == 1)["a"].to_list()) == list(range(6))
    assert np.allclose(out["b"].to_numpy(), out["a"].to_numpy() * 10)  # joint permutation
    assert not np.array_equal(out["a"].to_numpy(), df["a"].to_numpy())


def test_p_over_from_grid_on_half_lines() -> None:
    qi = np.tile(np.arange(10), (2, 20))[:, :199]
    p = T.p_over_from_grid(qi, np.array([4.5, 9.5]))
    assert abs(p[0] - 0.5) < 0.02 and p[1] == 0.0


def test_window_sum_feeds_only_later_games() -> None:
    t = pl.DataFrame(
        {"team_id": [1, 1, 1], "_k": [0, 1, 2], "player_id": [7, 7, 7], "v": [1.0, 2.0, 4.0]}
    )
    w = T.window_sum(t, ["v"], 2).sort("_t")
    got = dict(zip(w["_t"].to_list(), w["v"].to_list(), strict=True))
    assert got[1] == 1.0 and got[2] == 3.0 and got[3] == 6.0 and got[4] == 4.0
    assert 0 not in got  # a game never sees itself


def _roster(n_games: int = 60, n_pl: int = 9) -> pl.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    d0 = date(2023, 11, 1)
    for k in range(n_games):
        base = np.sort(rng.uniform(2, 36, n_pl))[::-1]
        tot = rng.uniform(0.9, 1.2) * 240.0
        mins = base / base.sum() * tot
        for i in range(n_pl):
            rows.append(
                {"game_id": f"g{k:03d}", "team_id": 1, "player_id": i, "game_date": d0 + timedelta(days=k), "season": 2023,
                 "e": float(base[i]), "B_roster": 238.0, "m_roster": float(mins.sum()), "minutes": float(mins[i]),
                 "share": float(mins[i] / mins.sum()), "elig": True}
            )  # fmt: skip
    return pl.DataFrame(rows)


def test_t1_sums_to_budget_and_kbar_is_as_of() -> None:
    r = _roster()
    v = T.t1_values(r)
    gids, b = T.group_ids(r)
    assert np.abs(np.bincount(gids, weights=v["e_new"]) - b).max() < 1e-9
    assert v["r"].min() >= T.R_LO - 1e-12 and v["r"].max() <= T.R_HI + 1e-12
    first = r["game_date"].min()
    assert (v["kbar"][r["game_date"].to_numpy() == first] == 1.0).all()  # nothing earlier yet
    r2 = r.with_columns(
        pl.when(pl.col("game_id") >= "g040")
        .then(pl.col("e") * 0.5)
        .otherwise(pl.col("e"))
        .alias("e")
    )
    v2 = T.t1_values(r2)
    early = (r["game_id"] < "g040").to_numpy()
    assert np.allclose(v["kbar"][early], v2["kbar"][early])  # future e never reaches earlier kbar
    assert not np.allclose(v["kbar"][~early], v2["kbar"][~early])


def test_oracle_o1_uses_realised_budget_and_o2_realised_minutes() -> None:
    r = _roster()
    o1 = T.t1_values(r, "m_roster")
    gids, _ = T.group_ids(r)
    assert np.allclose(
        np.bincount(gids, weights=o1["e_new"]),
        r.group_by("game_id", maintain_order=True)
        .agg(pl.col("m_roster").first())["m_roster"]
        .to_numpy(),
    )
    assert np.allclose(T.o2_values(r)["e_new"], r["minutes"].to_numpy())


def test_share_model_walk_forward_is_blind_to_the_blocks_own_labels_and_t2_sums_exact() -> None:
    rng = np.random.default_rng(2)
    rows = []
    d0 = date(2022, 10, 20)
    for k in range(320):
        for i in range(10):
            x = rng.normal()
            rows.append(
                {"game_id": f"g{k:03d}", "team_id": 1 + (k % 2), "player_id": i, "game_date": d0 + timedelta(days=k), "season": 2022 if k < 200 else 2023,
                 "x": x, "e": 24.0 - 2 * i, "B_roster": 238.0, "m_roster": 240.0, "minutes": 20.0, "elig": True,
                 "share": float(np.clip(0.1 + 0.03 * x, 0.01, None))}
            )  # fmt: skip
    r = pl.DataFrame(rows)
    cfg = ContextResidualConfig(n_estimators=20, num_leaves=4, min_child_samples=20, n_jobs=1)
    s = T.share_walk_forward(r, ["x"], cfg, seasons=(2023,))
    assert np.isnan(s[(r["season"] == 2022).to_numpy()]).all()
    assert not np.isnan(s[(r["season"] == 2023).to_numpy()]).any()
    first_block_end = date(2023, 6, 1)
    planted = r.with_columns(
        pl.when(pl.col("game_date") >= first_block_end)
        .then(pl.col("share") * 0 + 0.5)
        .otherwise(pl.col("share"))
        .alias("share")
    )
    s2 = T.share_walk_forward(planted, ["x"], cfg, seasons=(2023,))
    early = ((r["season"] == 2023) & (r["game_date"] < first_block_end)).to_numpy()
    assert np.allclose(s[early], s2[early])  # labels of a block and later never touch it
    assert not np.allclose(
        s[(r["game_date"] >= date(2023, 7, 1)).to_numpy()],
        s2[(r["game_date"] >= date(2023, 7, 1)).to_numpy()],
    )
    v = T.t2_values(r, s)
    ok = ~np.isnan(s)
    gids, b = T.group_ids(r)
    sums = np.bincount(gids, weights=np.where(ok, v["e_new"], 0.0))
    have = np.bincount(gids, weights=ok.astype(float)) > 0
    assert np.abs(sums - b)[have].max() < 1e-9
    assert (v["r"][~ok] == 1.0).all()


def _rows(
    stat: str, season: int, arm: str, d: float, hi: float, p: float = 0.001, **kw: float
) -> dict[str, float | str | int]:
    base = {
        "stat": stat,
        "season": season,
        "arm": arm,
        "n_rows": 100,
        "crps_int": 1.0,
        "bias": 0.0,
        "cov80": 0.8,
        "dcrps": d,
        "ci_lo": d - 0.01,
        "ci_hi": hi,
        "p": p,
        "p_bh": p,
        "mde": 0.002,
    }
    base.update(
        {
            "minus_P1": -0.004,
            "minus_P2": -0.004,
            "minus_T1": -0.004,
            "minus_T2nc": -0.003,
            "minus_O2": 0.1,
        }
    )
    base.update(kw)
    return base


def test_pass_rule_requires_both_seasons_floor_ci_and_attack_gates() -> None:
    rows = []
    for stat in T.STATS:
        rows.append(_rows(stat, 2023, "A0", 0.0, 0.0))
        rows.append(_rows(stat, 2024, "A0", 0.0, 0.0))
        for arm in ("T1", "T2"):
            rows += [_rows(stat, 2023, arm, 0.0, 0.003), _rows(stat, 2024, arm, 0.0, 0.003)]
        rows.append(_rows(stat, 2024, "O2", -0.5, -0.4))
        rows.append(_rows(stat, 2023, "O2", -0.5, -0.4))
    rows = [x for x in rows if not (x["arm"] == "O2" and x["season"] == 2023)] + []
    for x in rows:
        if x["stat"] == "pts" and x["arm"] == "T2":
            x.update(dcrps=-0.008, ci_lo=-0.012, ci_hi=-0.002)
    res = {"rows": rows, "slices": []}
    v = T.evaluate(res, True, True)
    assert v["cells"]["T2/pts"]["pass_before_attack"] is True
    assert v["cells"]["T2/reb"]["pass_before_attack"] is False
    assert v["cells"]["T1/pts"]["pass_before_attack"] is False
    assert v["close"] is False
    assert (
        T.evaluate(res, False, True)["cells"]["T2/pts"]["pass_before_attack"] is False
    )  # failed planted tests
    for x in rows:
        if x["stat"] == "pts" and x["arm"] == "T2" and x["season"] == 2024:
            x["minus_P2"] = -0.001  # placebo not beaten by 0.003
    assert T.evaluate(res, True, True)["cells"]["T2/pts"]["pass_before_attack"] is False


def test_attack_gates_complete_the_verdict() -> None:
    rows = []
    for stat in T.STATS:
        for s in (2023, 2024):
            rows.append(_rows(stat, s, "A0", 0.0, 0.0))
            for arm in ("T1", "T2"):
                rows.append(_rows(stat, s, arm, -0.008, -0.002))
            rows.append(_rows(stat, s, "O2", -0.5, -0.4))
    res = {"rows": rows, "slices": []}
    atk = {"T2": {st: {"shuffled_ok": True, "one_coach_column_carries": False} for st in T.STATS}}
    v = T.evaluate(res, True, True, atk)
    assert v["cells"]["T2/pts"]["PASS"] is True and v["cells"]["T2/pts"]["coach_claim"] is True
    atk["T2"]["pts"]["shuffled_ok"] = False
    assert T.evaluate(res, True, True, atk)["cells"]["T2/pts"]["PASS"] is False


def test_carries_rule() -> None:
    assert T.carries(-0.01, -0.001, -0.002) is True  # dropping it removes 90%
    assert T.carries(-0.01, -0.009, -0.002) is False
    assert T.carries(0.01, 0.0, 0.0) is False


def test_legs_with_p_recomputes_side_ev_and_outcome() -> None:
    legs = {
        "price_over": np.array([-110, 120]),
        "price_under": np.array([-110, -140]),
        "went_over": np.array([1.0, 0.0]),
        "mult": np.array([0.5, 0.45]),
        "line": np.array([20.5, 4.5]),
    }
    out = T.legs_with_p(legs, np.array([0.7, 0.2]))
    assert out["is_over"].tolist() == [True, False]
    assert out["hit"].tolist() == [1.0, 1.0]
    assert out["ev"][0] > 0


def _world(n_games: int = 40) -> tuple[dict, pl.DataFrame]:  # type: ignore[type-arg]
    t1, t2 = 1610612737, 1610612738
    d0 = date(2023, 11, 1)
    rng = np.random.default_rng(3)
    games, box, coach, pm, close = [], [], [], [], []
    for k in range(n_games):
        gid = f"002230{k:04d}"
        h, a = (t1, t2) if k % 2 == 0 else (t2, t1)
        d = d0 + timedelta(days=k)
        games.append(
            {
                "game_id": gid,
                "game_date": d,
                "season": 2023,
                "home_team": h,
                "away_team": a,
                "home_pts": 100 + k % 7,
                "away_pts": 98,
            }
        )
        for team in (t1, t2):
            coach.append({"game_id": gid, "team_id": team, "coach_key": f"C{team}"})
            mins = np.sort(rng.uniform(0, 36, 10))[::-1]
            mins = mins / mins.sum() * 240.0
            for i, m in enumerate(mins):
                pid = team % 100 * 100 + i
                box.append(
                    {
                        "game_id": gid,
                        "team_id": team,
                        "player_id": pid,
                        "minutes": float(m) if i < 9 else 0.0,
                        "starter": i < 5,
                    }
                )
                pm.append(
                    {
                        "game_id": gid,
                        "player_id": pid,
                        "game_date": d,
                        "p": 0.9,
                        "mu": float(25 - 2 * i),
                    }
                )
                if i < 5:
                    close.append({"game_id": gid, "team_id": team, "player_id": pid})
    world = {
        "games": pl.DataFrame(games), "box": pl.DataFrame(box).with_columns(pl.col("player_id").cast(pl.Int32)),
        "coach": pl.DataFrame(coach), "flagged": {}, "reasons": pl.DataFrame(schema={"game_id": pl.Utf8, "player_id": pl.Int64, "reason": pl.Utf8}),
        "close": pl.DataFrame(close).with_columns(pl.col("player_id").cast(pl.Int32)),
    }  # fmt: skip
    return world, pl.DataFrame(pm).with_columns(pl.col("player_id").cast(pl.Int32))


def test_build_frame_is_as_of_and_budget_sums_exact_on_a_synthetic_league() -> None:
    world, pm = _world()
    fr = T.build_frame(world, pm)
    r = fr["roster"]
    assert r.select("game_id", "team_id", "player_id").unique().height == r.height
    v = T.t1_values(r)
    gids, b = T.group_ids(r)
    assert np.abs(np.bincount(gids, weights=v["e_new"]) - b).max() < 1e-9
    # plant the 30th game's box, score, stints: every feature of games up to it is identical, later ones move
    target = "0022300030"
    w2 = dict(world)
    w2["box"] = world["box"].with_columns(
        pl.when(pl.col("game_id") == target)
        .then(pl.col("minutes").reverse().over("game_id", "team_id"))
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    w2["games"] = world["games"].with_columns(
        pl.when(pl.col("game_id") == target)
        .then(pl.col("home_pts") + 40)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    w2["close"] = world["close"].filter(pl.col("game_id") != target)
    r2 = T.build_frame(w2, pm)["roster"]
    cols = [*T.ROLE_COLS, *T.COACH_COLS]
    a = r.filter(pl.col("game_id") <= target).sort("game_id", "team_id", "player_id")
    b2 = r2.filter(pl.col("game_id") <= target).sort("game_id", "team_id", "player_id")
    assert a.shape == b2.shape
    for c in cols:
        if c == "abs_margin":
            continue
        assert np.allclose(
            np.nan_to_num(a[c].to_numpy().astype(float)),
            np.nan_to_num(b2[c].to_numpy().astype(float)),
        ), c
    later = r.filter(pl.col("game_id") > target).sort("game_id", "team_id", "player_id")
    later2 = r2.filter(pl.col("game_id") > target).sort("game_id", "team_id", "player_id")
    moved = later.shape != later2.shape or any(
        not np.allclose(
            np.nan_to_num(later[c].to_numpy().astype(float)),
            np.nan_to_num(later2[c].to_numpy().astype(float)),
        )
        for c in cols
        if c != "abs_margin"
    )
    assert moved  # non-inert control
    # the 10th player never played: he is never on an as-of roster (the box lists him, the roster must not)
    assert (r["player_id"] % 100 != 9).all()
