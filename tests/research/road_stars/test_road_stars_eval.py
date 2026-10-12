# mypy: disable-error-code="no-any-return,no-untyped-call,func-returns-value,attr-defined,unused-ignore,index,arg-type,assignment"
# ruff: noqa: E501
"""ROAD_STARS harness: freeze refusals, the star / away columns, the coin and past-only placebo constructions, the walk's as-of property
with the road columns planted, the pass rule failing each way, the null-before-ROAD order, and a planted road effect the arm can find."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import road_stars_eval as E
from research.recent_role import placebo as PB

CFG = ContextResidualConfig(n_estimators=15)


def _doc(tmp_path: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp_path / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    good = lambda *a: f"abc\tprereg(ROAD_STARS): {sha}\n"  # noqa: E731
    other = lambda *a: f"abc\tprereg(RECENT_ROLE_FULL): {sha}\n"  # noqa: E731
    assert E.require_frozen(sha, p, good) == sha
    with pytest.raises(RuntimeError, match="no commit"):
        E.require_frozen(sha, p, other)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen("b" * 64, p, good)
    with pytest.raises(RuntimeError, match="required"):
        E.require_frozen(None, p, good)
    p.write_text(p.read_text().replace("rule", "rule edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen(sha, p, good)


def test_every_stage_refuses_without_the_freeze_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = E.WE.frozen_sha(E.DOC)
    monkeypatch.setattr(
        E, "require_gate", lambda s: pytest.fail("reached the gate check before the freeze check")
    )
    nogit = lambda *a: ""  # noqa: E731
    monkeypatch.setattr(E, "freeze_commit", lambda s, git=nogit: None)
    for stage in (E.run_arms, E.run_null, E.run_score):
        with pytest.raises(RuntimeError, match="no commit|not frozen"):
            stage(sha)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no commit|not frozen"):
        E.run_gate(sha)
    with pytest.raises(RuntimeError, match="required"):
        E.main(["arms"])  # no --sha


def test_checkpoint_older_than_freeze_is_refused(tmp_path: Path) -> None:
    import os

    p = tmp_path / "c.npz"
    assert not E.check_checkpoint(p, 1e12)
    p.write_bytes(b"x")
    os.utime(p, (100.0, 100.0))
    with pytest.raises(RuntimeError, match="predates"):
        E.check_checkpoint(p, 200.0)
    assert E.check_checkpoint(p, 50.0) and E.check_checkpoint(p, None)


def test_no_2025_and_no_stage2_code() -> None:
    assert 2025 not in E.build_frame.__code__.co_consts
    assert not hasattr(E, "stage2") and not hasattr(E, "holdout")
    assert E.SEASONS == (2023, 2024) and E.MAX_SEASON == 2024


def test_rule_constants_are_the_drafts() -> None:
    assert E.STAR_THR == 20.0 and E.FLOOR == -0.005 and E.PLACEBO_MARGIN == -0.003
    assert E.OVERALL_POINT_TOL == 0.0005 and E.OVERALL_CI_TOL == 0.001
    assert E.COV_TAUS == (0.10, 0.20, 0.80, 0.90) and E.COV_TOL == 0.01 and E.SLICE_TOL == 0.01
    assert E.K_NULL == 50 and E.NULL_SD_MULT == 2.0 and E.MIN_SLICE_N == 300
    assert set(E.GUARD_SLICES) == {
        "non_star",
        "star_home",
        "star_road",
        "bench",
        "playoffs",
        "min<5",
        "cold_start",
    }
    assert E.ROAD_COLS == ("star10", "star10_away") and E.SELECT_SEASON == 2023
    assert E.names_for("pts", "ROAD")[-2:] == ["star10", "star10_away"]
    assert E.names_for("pts", "PL")[-1] == "pl_star10_away" and E.names_for(
        "pts", "CTRL"
    ) == E.stat_feature_names("pts")


def _frame(seed: int = 0, road_bump: float = 0.0) -> pl.DataFrame:
    """40 players on 4 teams, one game per team-pair per 3 days; players 0-9 are stars (m10_pts 25); is_home_f alternates by team and day."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    d0 = dt.date(2022, 11, 1)
    for gi, day in enumerate(range(0, 720, 3)):
        d = d0 + dt.timedelta(days=day)
        season = d.year if d.month >= 8 else d.year - 1
        for pid in range(40):
            team = pid // 10
            home = 1 if (team + gi) % 2 == 0 else 0
            star = pid % 10 < 4
            m10 = 25.0 if star else 8.0
            away_star = star and home == 0
            pts = m10 + rng.normal(0, 2.0) + (road_bump if away_star else 0.0)
            rows.append(
                {
                    "game_id": f"g{gi:05d}{team // 2}", "player_id": pid, "team_id": team, "season": season, "game_date": d, "n_prior": 20,
                    "minutes": 25.0, "min10": 25.0, "min40": 25.0, "starter10": 0.5, "starter_act": 0.0, "abs_margin": 3.0, "final_margin": 4.0,
                    "b2b": 0.0, "gap_days": 2.0, "since_flag": 5.0, "pts": pts, "m10_pts": m10, "sd10_pts": 2.0, "is_home_f": home, "x": float(rng.normal()),
                }
            )  # fmt: skip
    return pl.DataFrame(rows)


@pytest.fixture(autouse=True)
def _small_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(E, "stat_feature_names", lambda stat: ["x", "is_home_f"])


def test_star_and_away_columns_are_as_of_and_null_safe() -> None:
    fr = _frame().head(40)
    fr = fr.with_columns(
        pl.when(pl.col("player_id") == 0).then(None).otherwise(pl.col("m10_pts")).alias("m10_pts")
    )
    out = E.add_road_columns(fr)
    s, a = out["star10"].to_numpy(), out["star10_away"].to_numpy()
    assert s[0] == 0.0  # null m10_pts is not a star
    assert np.array_equal(s, ((fr["m10_pts"].fill_null(0.0) >= 20).cast(pl.Float64)).to_numpy())
    assert np.array_equal(a, s * (1.0 - fr["is_home_f"].to_numpy()))
    with pytest.raises(ValueError, match="already"):
        E.add_road_columns(out)


def test_coin_is_shared_by_teammates_deterministic_per_arm_and_half_away() -> None:
    fr = E.add_road_columns(_frame())
    a, a2, b = E.coin_frame(fr, 0), E.coin_frame(fr, 0), E.coin_frame(fr, 1)
    assert a.height == fr.height and np.array_equal(
        a["player_id"].to_numpy(), fr["player_id"].to_numpy()
    )
    assert np.array_equal(a["star10_away"].to_numpy(), a2["star10_away"].to_numpy())
    assert not np.array_equal(a["star10_away"].to_numpy(), b["star10_away"].to_numpy())
    g = a.group_by(["game_id", "team_id"]).agg(pl.col("star10").mul(pl.col("star10_away")).max().alias("m"), pl.col("star10_away").filter(pl.col("star10") == 1.0).n_unique().alias("nu"))  # fmt: skip
    assert (g["nu"] <= 1).all()  # every star of one team-game carries the same coin
    share = a["star10_away"].to_numpy()[fr["star10"].to_numpy() == 1.0].mean()
    assert 0.4 < share < 0.6
    assert (
        float(np.abs(a["star10_away"].to_numpy() - fr["star10_away"].to_numpy()).sum()) > 0
    )  # not the real flag


def test_past_only_placebo_takes_a_strictly_earlier_row_of_the_player_season() -> None:
    fr = E.add_road_columns(_frame())
    fr = PB.permute_past_only(fr, ("star10_away",), 0, "pl_")
    src = PB.past_only_source(fr, 0)
    d = fr["game_date"].to_list()
    pid, season = fr["player_id"].to_list(), fr["season"].to_list()
    for i in range(0, fr.height, 97):
        if src[i] >= 0:
            assert pid[src[i]] == pid[i] and season[src[i]] == season[i] and d[src[i]] < d[i]
    assert (
        fr[E.PL_COL].is_nan().sum() + fr[E.PL_COL].null_count() > 0
    )  # first row of a player-season has no earlier row


def _walk(fr: pl.DataFrame, arm: str, seasons: tuple[int, ...] = (2023, 2024)) -> dict[str, Any]:
    return E.RRF.collect_heads(
        fr, "pts", CFG, E.names_for("pts", arm), test_seasons=seasons, min_train=1500, log=False
    )


def test_walk_with_road_columns_is_as_of_planted_future_and_non_inert() -> None:
    fr = E.add_road_columns(_frame())
    cut = dt.date(2024, 2, 15)
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(99.0).otherwise(pl.col("pts")).alias("pts"),
        *[pl.when(late).then(1.0).otherwise(pl.col(c)).alias(c) for c in E.ROAD_COLS],
    )
    a, b = _walk(fr, "ROAD", (2023,)), _walk(edited, "ROAD", (2023,))
    ja = pl.DataFrame({"g": a["row_gid"], "p": a["row_pid"], "ca": a["row_c"]})
    jb = pl.DataFrame({"g": b["row_gid"], "p": b["row_pid"], "cb": b["row_c"]})
    date = fr.select(pl.col("game_id").alias("g"), pl.col("player_id").alias("p"), "game_date")
    j = ja.join(jb, on=["g", "p"]).join(date, on=["g", "p"])
    pre = j.filter(pl.col("game_date") < dt.date(2024, 2, 1))
    nxt = j.filter(pl.col("game_date") >= dt.date(2024, 3, 1))
    assert pre.height > 100 and nxt.height > 100
    assert float(np.abs(pre["ca"].to_numpy() - pre["cb"].to_numpy()).max()) == 0.0
    assert float(np.abs(nxt["ca"].to_numpy() - nxt["cb"].to_numpy()).max()) > 0.0


def test_season_only_control_walk_equals_the_two_season_walk_on_that_season() -> None:
    """Equivalence proof for the null arms: a 2023-only walk is bit-identical to the 2023 rows of the 2-season walk (same blocks, same training)."""
    fr = E.add_road_columns(_frame())
    full, one = _walk(fr, "CTRL"), _walk(fr, "CTRL", (2023,))
    m = full["row_season"] == 2023
    assert one["row_c"].shape[0] == int(m.sum())
    for k in ("row_c", "row_s", "row_y", "row_blk"):
        assert np.array_equal(full[k][m], one[k]), k


def _abs_err(h: dict[str, Any], mask: np.ndarray) -> float:
    return float(np.abs(h["row_y"] - h["row_c"])[mask].mean())


def test_planted_road_effect_is_found_by_road_and_not_by_coin_or_past_only_arms() -> None:
    """The arm can win: with a planted +6 pts bump for away stars, ROAD beats CTRL on the star cell; a coin flag does not recover it."""
    fr = E.add_road_columns(_frame(road_bump=6.0))
    fr = PB.permute_past_only(fr, ("star10_away",), 0, "pl_")
    ctrl, road = _walk(fr, "CTRL", (2024,)), _walk(fr, "ROAD", (2024,))
    star = np.isin(ctrl["row_pid"], np.arange(40)) & ((ctrl["row_pid"] % 10) < 4)
    gain_road = _abs_err(road, star) - _abs_err(ctrl, star)
    coin = _walk(E.coin_frame(fr, 3), "ROAD", (2024,))
    gain_coin = _abs_err(coin, star) - _abs_err(ctrl, star)
    # is_home_f is in the names, so CTRL already sees home; the planted effect is star x away, which only the explicit term (or a deep split) finds
    assert gain_road < gain_coin
    assert gain_road < 0


def test_null_summary_threshold_and_carry_over() -> None:
    s = E.null_summary(np.array([0.001, -0.001, 0.0, 0.002, -0.002]))
    assert s["K"] == 5 and abs(s["mean"]) < 1e-12
    assert s["threshold"] == pytest.approx(s["mean"] - 2 * s["sd"])
    null = {"seasons": {"2023": s}}
    assert E.threshold_for(null, 2024) is s  # carried over
    own = {"K": 50, "mean": 0.1, "sd": 0.2, "threshold": -0.3}
    assert E.threshold_for({"seasons": {"2023": s, "2024": own}}, 2024) is own


def test_arms_refuse_without_a_null_threshold_for_this_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "OUT_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="null arms are scored BEFORE ROAD"):
        E.require_null("a" * 64)
    E.WE.write_json(E.null_path(), {"sha256": "a" * 64, "seasons": {"2023": {"K": 10}}})
    with pytest.raises(RuntimeError, match="K < 50"):
        E.require_null("a" * 64)
    E.WE.write_json(E.null_path(), {"sha256": "b" * 64, "seasons": {"2023": {"K": 50}}})
    with pytest.raises(RuntimeError, match="another rule"):
        E.require_null("a" * 64)
    E.WE.write_json(E.null_path(), {"sha256": "a" * 64, "seasons": {"2023": {"K": 50}}})
    assert E.require_null("a" * 64)["sha256"] == "a" * 64


def test_null_stage_refuses_fewer_than_50_arms() -> None:
    with pytest.raises(RuntimeError, match="K >= 50"):
        E.run_null("a" * 64, k_total=10)


def _res(d23: float, d24: float, **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    over = kw.get("over", 0.0)
    plc = kw.get("plc", -0.01)
    p = kw.get("p", 0.001)
    cells: list[dict[str, Any]] = []
    for s, d in ((2023, d23), (2024, d24)):
        base = {"season": s, "n_rows": 3000, "n_games": 1200, "mde": 0.003}
        cells.append({**base, "contrast": "ROAD_minus_A0_star", "cell": "star", "delta": d, "ci_lo": d - 0.003, "ci_hi": d + 0.003 if "ci_hi" not in kw else kw["ci_hi"], "p": p})  # fmt: skip
        cells.append({**base, "contrast": "ROAD_minus_A0_all", "cell": "all", "delta": over, "ci_lo": over - 0.0005, "ci_hi": over + 0.0005, "p": 0.5})  # fmt: skip
        cells.append({**base, "contrast": "ROAD_minus_PL_star", "cell": "star", "delta": plc, "ci_lo": plc - 0.003, "ci_hi": plc + 0.003, "p": 0.5})  # fmt: skip
    slices = [{"season": s, "slice": "star_road", "n": 1700, "delta": -0.01} for s in (2023, 2024)]
    taus = {f"{t:.2f}": t for t in E.COV_TAUS}
    cov = {
        str(s): {
            c: {"A0": taus, "ROAD": {k: v + kw.get("cov", 0.0) for k, v in taus.items()}}
            for c in ("star", "all")
        }
        for s in (2023, 2024)
    }
    null = {
        "seasons": {
            "2023": {"K": 50, "mean": 0.0, "sd": 0.0015, "threshold": kw.get("thr", -0.003)}
        }
    }
    return {"cells": cells, "slices": slices, "coverage": cov}, null


def test_decide_pass_and_each_way_to_fail() -> None:
    res, null = _res(-0.02, -0.02, ci_hi=-0.001)
    assert E.decide(res, null, True)["pass"]
    assert not E.decide(res, null, False)["pass"]  # gate
    r, n = _res(-0.02, -0.003, ci_hi=-0.001)
    assert not E.decide(r, n, True)["pass"]  # report season above the floor
    r, n = _res(-0.003, -0.02, ci_hi=-0.001)
    assert not E.decide(r, n, True)["pass"]  # select season above the floor
    r, n = _res(-0.02, -0.02)  # CI upper = d + 0.003 < 0 still: fine; make it cross zero
    assert E.decide(r, n, True)["pass"]
    r, n = _res(-0.02, -0.02, ci_hi=0.001)
    assert not E.decide(r, n, True)["pass"]  # CI upper >= 0
    r, n = _res(-0.02, -0.02, p=0.2)
    assert not E.decide(r, n, True)["pass"]  # p
    r, n = _res(-0.02, -0.02, over=0.002)
    assert not E.decide(r, n, True)["pass"]  # overall harm
    r, n = _res(-0.02, -0.02, plc=-0.001)
    assert not E.decide(r, n, True)["pass"]  # past-only placebo
    r, n = _res(-0.02, -0.02, thr=-0.03)
    assert not E.decide(r, n, True)["pass"]  # inside the permutation-null noise
    r, n = _res(-0.02, -0.02, cov=0.02)
    assert not E.decide(r, n, True)["pass"]  # coverage
    r, n = _res(-0.02, -0.02, cov=0.005)
    assert E.decide(r, n, True)["pass"]  # inside tolerance
    r, n = _res(-0.02, -0.02)
    r["slices"][0]["delta"] = 0.02
    assert not E.decide(r, n, True)["pass"]  # guard slice
    r, n = _res(-0.02, -0.02)
    r["slices"][0].update(n=100, delta=0.5)
    assert E.decide(r, n, True)["pass"]  # a slice under 300 cannot fail


def test_masks_and_meta_partition_the_rows() -> None:
    n = 6
    comp = {
        "row_gid": np.array([f"002{i}" for i in range(n)]), "row_pid": np.arange(n), "row_minutes": np.array([30.0, 3, 20, 20, 20, 20]),
        "row_min10": np.full(n, 30.0), "row_starter10": np.array([1.0, 0, 0, 1, 1, 0]), "row_n_prior": np.array([20, 20, 5, 20, 20, 20]),
    }  # fmt: skip
    fr = pl.DataFrame({"game_id": comp["row_gid"], "player_id": comp["row_pid"].astype(np.int64), "m10_pts": [25.0, 25, 10, 21, 3, 20], "is_home_f": [1, 0, 1, 0, 1, 0]})  # fmt: skip
    meta = E.meta_for(fr, comp)
    m = E.masks_for(comp, meta)
    assert m["star"].tolist() == [True, True, False, True, False, True]
    assert m["star_home"].sum() + m["star_road"].sum() == m["star"].sum()
    assert (m["star"] ^ m["non_star"]).all()
    assert m["star_road"].tolist() == [False, True, False, True, False, True]
    bad = fr.with_columns(
        pl.when(pl.col("player_id") == 2).then(None).otherwise(pl.col("m10_pts")).alias("m10_pts")
    )
    with pytest.raises(RuntimeError, match="null"):
        E.meta_for(bad, comp)


def test_coverage_ok_flags_worse_than_a0() -> None:
    taus = {f"{t:.2f}": t for t in E.COV_TAUS}
    good = {"2023": {"star": {"A0": taus, "ROAD": taus}}}
    assert E.coverage_ok(good)[0]
    bad = {"2023": {"star": {"A0": taus, "ROAD": {k: v + 0.03 for k, v in taus.items()}}}}
    assert not E.coverage_ok(bad)[0]
