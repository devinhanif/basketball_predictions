# ruff: noqa: E501
"""WORKLOAD harness: cached-arm loading, per-cell scoring, BH, oracle bound, report and attack stages.

Builds a small synthetic cache (three cells: pts, M, P) in a tmp tree and redirects the module's
path constants to it. Complements ``test_workload_eval.py`` (freeze, lock, pass-rule logic, walks).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
from scipy import stats as sps

from nba.eval.context_residual_eval import bh_adjust, crps_from_quantiles
from research.eval import workload_eval as E

TEAMS = [1610612737, 1610612738, 1610612739, 1610612740, 1610612741, 1610612742]
G_PER_SEASON = 60
PER_GAME = 10
TAUS = (np.arange(1, 200) - 0.5) / 199


def _make_meta_and_keys() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(0)
    rows, games = [], []
    for si, season in enumerate(E.SEASONS):
        for g in range(G_PER_SEASON):
            gid = f"002{season % 100}{g:05d}"
            h, a = TEAMS[g % 6], TEAMS[(g + 1 + g // 6) % 6]
            if h == a:
                a = TEAMS[(g + 2) % 6]
            games.append(
                {
                    "game_id": gid,
                    "game_date": __import__("datetime").date(2023 + si, 11, 1)
                    + __import__("datetime").timedelta(days=g),
                    "season": season,
                    "home_team": h,
                    "away_team": a,
                    "home_pts": int(100 + rng.integers(0, 20)),
                    "away_pts": int(100 + rng.integers(0, 20)),
                }
            )
            for k in range(PER_GAME):
                side = k // 5
                rows.append(
                    {
                        "game_id": gid,
                        "player_id": 1000 * (side + 1) + k,
                        "team_id": h if side == 0 else a,
                        "season": season,
                        "starter10": 1.0 if k % 5 < 2 else 0.0,
                        "min10": float(34 - 6 * (k % 5) + rng.normal(0, 1)),
                        "n_prior": int(rng.integers(0, 40)),
                        "team_game_no": g,
                        "wl_b2b_second": int(rng.random() < 0.2),
                        "wl_three_in_four": int(rng.random() < 0.2),
                        "wl_road_trip4plus": int(rng.random() < 0.2),
                        "wl_tz_cross_hr": float(rng.integers(-3, 4)),
                        "wl_miles_from_prev": float(rng.uniform(0, 2500)),
                        "wl_early_tip": int(rng.random() < 0.2),
                        "wl_speed_decline": float(rng.normal()),
                    }
                )
    meta = pl.DataFrame(rows)
    status = pl.DataFrame(
        {
            "game_id": [r["game_id"] for r in rows],
            "team_id": [r["team_id"] for r in rows],
        }
    ).unique()
    status = status.with_columns(
        pl.Series(
            "status",
            np.random.default_rng(1).choice(["contending", "middle", "out"], status.height),
        ),
        pl.Series("late", np.random.default_rng(2).random(status.height) < 0.3),
    )
    return meta, status, pl.DataFrame(games)


@pytest.fixture()
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rng = np.random.default_rng(5)
    meta, status, games = _make_meta_and_keys()
    cdir, odir, mdir = tmp_path / "cache", tmp_path / "out", tmp_path / "mv2"
    for d in (cdir, odir, mdir):
        d.mkdir()
    monkeypatch.setattr(E, "CACHE", cdir)
    monkeypatch.setattr(E, "OUT_DIR", odir)
    monkeypatch.setattr(E, "REPORT_MD", tmp_path / "report.md")
    monkeypatch.setattr(E, "MV2_DIR", mdir)
    monkeypatch.setattr(E, "PAIRS_DIR", tmp_path / "no_pairs")
    monkeypatch.setattr(E, "DB", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(E, "require_frozen", lambda *a, **k: E.FROZEN_FULL)
    for kind in ("props", "pplay", "minutes"):
        meta.write_parquet(cdir / f"meta_{kind}.parquet")
    n = meta.height
    gid, pid, season = (
        meta["game_id"].to_numpy(),
        meta["player_id"].to_numpy(),
        meta["season"].to_numpy(),
    )
    # --- pts cell: integer 199-grids; B conditions on a noisy view of the outcome (a real gain)
    y = rng.poisson(12, n).astype(float)
    lam_b = np.clip(12 + 0.6 * (y - 12) + rng.normal(0, 1, n), 1, None)
    base_q = np.repeat(np.floor(np.quantile(rng.poisson(12, 4000), TAUS))[None, :], n, 0)
    b_q = sps.poisson.ppf(TAUS[None, :], lam_b[:, None])
    shift = np.where(rng.random(n) < 0.5, 0, 1)[:, None]
    arms_q = {
        "base": base_q,
        "B": b_q,
        "PL": base_q + shift,
        "KO_LOAD": b_q,
        "KO_SCHEDULE": b_q,
        "KO_TRAVEL": base_q,
        "NT": b_q,
        "A3": b_q - 1,
        "A3W": b_q,
        "SHUF": base_q,
    }
    for arm, q in arms_q.items():
        np.savez(
            E.cache_path("pts", arm),
            qi=q.astype(np.int16),
            y=y,
            gid=gid,
            pid=pid,
            season=season,
        )
    # --- M cell: stored per-row scores
    ym = rng.uniform(0, 40, n)
    mscores: dict[str, np.ndarray] = {"gid": gid, "pid": pid, "season": season, "y": ym}
    base_crps = rng.uniform(2, 4, n)
    for arm, gain in (
        ("base", 0.0),
        ("B", 0.3),
        ("PL", 0.0),
        ("KO_LOAD", 0.3),
        ("KO_SCHEDULE", 0.0),
        ("KO_TRAVEL", 0.0),
        ("NT", 0.3),
    ):
        mscores[f"{arm}_crps"] = base_crps - gain + rng.normal(0, 0.05, n)
        mscores[f"{arm}_pit"] = rng.random(n)
        mscores[f"{arm}_mean"] = ym + rng.normal(0, 0.1, n)
    np.savez(cdir / "M__scores.npz", **mscores)  # type: ignore[arg-type]
    np.savez(
        cdir / "M__shuffle.npz",
        **{
            k: v
            for k, v in mscores.items()
            if k.startswith("B_") or k in ("gid", "pid", "season", "y")
        },  # type: ignore[arg-type]
    )
    # --- P cell: probabilities of playing
    yp = (rng.random(n) < 0.8).astype(float)
    pscores: dict[str, np.ndarray] = {"gid": gid, "pid": pid, "season": season, "y": yp}
    for arm, w in (("base", 0.0), ("B", 0.5), ("PL", 0.0), ("NT", 0.5)):
        pscores[f"p_{arm}"] = np.clip(0.8 + w * (yp - 0.8) + rng.normal(0, 0.05, n), 0.02, 0.98)
    np.savez(cdir / "P__scores.npz", **pscores)  # type: ignore[arg-type]
    # --- stored MINUTES_V2 oracle for pts: a tighter score than B's
    np.savez(mdir / "props_pts_A3.npz", crps_int=np.full(n, 0.2), season=season)
    # --- gate + games for the team-status table
    (odir / "gate.json").write_text(json.dumps({"gate_ok": True, "sha256": E.FROZEN_FULL}))
    (odir / "gates.json").write_text(json.dumps({"rest": {"rest_events": [{"k": 1}]}}))
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.register("g_df", games.to_arrow())
    con.execute("create table games as select * from g_df")
    con.close()
    return {
        "meta": meta,
        "status": status,
        "n": n,
        "y": y,
        "arms_q": arms_q,
        "gid": gid,
        "season": season,
        "tmp": tmp_path,
    }


# ------------------------------------------------------------------------------ small pure helpers


def test_ll_and_ece_scoring_helpers() -> None:
    p = np.array([0.9, 0.1, 0.0, 1.0])
    ev = np.array([1.0, 0.0, 1.0, 0.0])
    ll = E._ll(p, ev)
    assert ll[0] == pytest.approx(-np.log(0.9)) and ll[1] == pytest.approx(-np.log(0.9))
    assert np.isfinite(ll).all() and ll[2] == pytest.approx(-np.log(1e-6))  # clipped, finite
    # perfectly calibrated bins have zero ECE; a constant overconfident forecast does not
    y = np.array([1.0] * 8 + [0.0] * 2)
    assert E.ece(np.full(10, 0.8), y) == pytest.approx(0.0)
    assert E.ece(np.full(10, 0.99), y) == pytest.approx(0.19)


def test_write_json_creates_parents_and_serialises_numpy(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b.json"
    E.write_json(p, {"x": np.float64(1.5)})
    assert json.loads(p.read_text()) == {"x": 1.5}


def test_assert_seasons_refuses_the_holdout() -> None:
    E.assert_seasons(pl.DataFrame({"season": [2022, 2024]}))
    with pytest.raises(ValueError, match="holdout"):
        E.assert_seasons(pl.DataFrame({"season": [2024, 2025]}))


def test_require_gate_demands_a_passing_gate_for_the_frozen_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "OUT_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="no gate.json"):
        E.require_gate()
    for gate in ({"gate_ok": False, "sha256": E.FROZEN_FULL}, {"gate_ok": True, "sha256": "x"}):
        (tmp_path / "gate.json").write_text(json.dumps(gate))
        with pytest.raises(RuntimeError, match="has not passed"):
            E.require_gate()
    (tmp_path / "gate.json").write_text(json.dumps({"gate_ok": True, "sha256": E.FROZEN_FULL}))
    E.require_gate()


def test_oracle_bound_reads_the_stored_a3_means(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "MV2_DIR", tmp_path)
    assert E.oracle_bound("pts") is None  # no stored file
    np.savez(tmp_path / "props_pts_A3.npz", season=np.array([2023, 2023, 2024]), x=np.zeros(3))
    assert E.oracle_bound("pts") is None  # a file without crps_int is not an oracle
    np.savez(
        tmp_path / "props_reb_A3.npz",
        season=np.array([2023, 2023, 2024]),
        crps_int=np.array([1.0, 3.0, 5.0]),
    )
    assert E.oracle_bound("reb") == {2023: 2.0, 2024: 5.0}


def test_d3_verdict_reading_for_each_branch() -> None:
    def rows(stat: str, b: float, m3: float, m3w: float) -> list[dict[str, Any]]:
        return [
            {"cell": stat, "season": 2024, "arm": "B", "delta": b},
            {"cell": stat, "season": 2024, "arm": "A3", "metric": m3},
            {"cell": stat, "season": 2024, "arm": "A3W", "metric": m3w},
        ]

    res = {"rows": rows("pts", -0.02, 1.0, 1.0)}  # B gains, oracle minutes absorb nothing => flat
    assert E.d3_verdict(res)["pts"]["reading"] == "through minutes"
    res = {"rows": rows("pts", -0.02, 1.0, 0.99)}  # A3W also gains by more than floor/2
    assert "contradicts" in E.d3_verdict(res)["pts"]["reading"]
    res = {"rows": rows("pts", 0.0, 1.0, 1.0)}
    assert E.d3_verdict(res)["pts"]["reading"] == "no B gain"
    assert (
        E.d3_verdict({"rows": rows("ast", -0.1, 1.0, 1.0)}) == {}
    )  # only pts and reb have the pair
    assert (
        E.d3_verdict({"rows": [{"cell": "pts", "season": 2024, "arm": "B", "delta": -1.0}]}) == {}
    )


def test_add_bh_adjusts_within_each_season_over_the_b_arm() -> None:
    rows: list[dict[str, Any]] = []
    for s in E.SEASONS:
        for cell, p in (("pts", 0.001), ("reb", 0.02), ("M", 0.5)):
            rows.append({"cell": cell, "season": s, "arm": "B", "p": p})
            rows.append({"cell": cell, "season": s, "arm": "PL", "p": 0.0001})
    E.add_bh(rows)
    for s in E.SEASONS:
        b = [r for r in rows if r["season"] == s and r["arm"] == "B"]
        assert [r["p_bh"] for r in b] == pytest.approx(
            list(bh_adjust(np.array([0.001, 0.02, 0.5])))
        )
        assert all(r["p_bh"] >= r["p"] for r in b)
    assert all("p_bh" not in r for r in rows if r["arm"] == "PL")


def test_mixture_qi_refuses_misaligned_components() -> None:
    comp = {"row_gid": np.array(["a", "b"]), "row_pid": np.array([1, 2])}
    pic = {"row_gid": np.array(["a", "b"]), "row_pid": np.array([1, 3])}
    with pytest.raises(RuntimeError, match="not aligned"):
        E.mixture_qi(comp, pic, "pts")


def test_cache_path_and_pic_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(E, "CACHE", tmp_path)
    assert E.cache_path("pts", "B") == tmp_path / "pts__B.npz"
    monkeypatch.setattr(E, "LT_DIR", tmp_path)
    np.savez(tmp_path / "components_pi.npz", a=np.arange(3))
    assert E.load_pic()["a"].tolist() == [0, 1, 2]


# ------------------------------------------------------------------------------ cached arms and scoring


def test_cell_arrays_scores_each_cached_arm_on_one_row_order(cache: dict[str, Any]) -> None:
    arr = E.cell_arrays("pts")
    assert set(arr["arms"]) == {
        "base",
        "B",
        "PL",
        "KO_LOAD",
        "KO_SCHEDULE",
        "KO_TRAVEL",
        "NT",
        "A3",
        "A3W",
        "SHUF",
    }
    assert len(arr["gid"]) == cache["n"] and arr["season"].tolist() == cache["season"].tolist()
    want = crps_from_quantiles(cache["arms_q"]["B"].astype(np.int16).astype(float), cache["y"])
    assert np.allclose(arr["arms"]["B"]["m"], want)
    assert (
        arr["arms"]["B"]["m"].mean() < arr["arms"]["base"]["m"].mean()
    )  # the conditioned arm is sharper
    # M and P cells expose their stored per-row scores; shuffle arms get a suffix
    m = E.cell_arrays("M")
    assert {"base", "B", "B_shuf"} <= set(m["arms"]) and "pit" in m["arms"]["B"]
    assert np.allclose(
        m["arms"]["B"]["bias"], np.load(E.CACHE / "M__scores.npz")["B_mean"] - m["y"]
    )
    p = E.cell_arrays("P")
    assert {"base", "B", "PL", "NT"} <= set(p["arms"]) and "p" in p["arms"]["B"]
    assert p["arms"]["base"]["m"].mean() > p["arms"]["B"]["m"].mean()  # lower log loss for B


def test_cell_arrays_refuses_a_misaligned_arm(cache: dict[str, Any]) -> None:
    z = dict(np.load(E.cache_path("pts", "B"), allow_pickle=True))
    z["gid"] = z["gid"][::-1].copy()
    np.savez(E.cache_path("pts", "B"), **z)
    with pytest.raises(RuntimeError, match="rows not aligned"):
        E.cell_arrays("pts")


def test_score_cell_rows_slices_and_consistency(cache: dict[str, Any]) -> None:
    res = E.score_cell("pts", cache["status"])
    tab = {(r["season"], r["arm"]): r for r in res["rows"]}
    sl = {(s["season"], s["slice"]): s for s in res["slices"]}
    for season in E.SEASONS:
        sm = cache["season"] == season
        b, base = tab[(season, "B")], tab[(season, "base")]
        assert b["n_rows"] == int(sm.sum()) and b["n_games"] == G_PER_SEASON
        assert b["metric"] == pytest.approx(
            float(
                crps_from_quantiles(
                    cache["arms_q"]["B"].astype(np.int16).astype(float)[sm], cache["y"][sm]
                ).mean()
            )
        )
        assert b["delta"] == pytest.approx(
            b["metric"] - base["metric"]
        )  # mean of the paired row delta
        assert b["ci_lo"] <= b["delta"] <= b["ci_hi"] and b["delta"] < -0.1 and b["ci_hi"] < 0
        assert b["mde"] == pytest.approx((b["ci_hi"] - b["ci_lo"]) / 2)
        assert 0 <= b["cov80"] <= 1 and "delta" not in base
        assert (season, "B_minus_PL") in tab and (season, "B_minus_NT") in tab
        assert tab[(season, "B_minus_PL")]["delta"] < 0  # B beats the placebo
        assert abs(tab[(season, "B_minus_NT")]["delta"]) < 1e-12  # NT is B here
        assert sl[(season, "all")]["n"] == int(sm.sum())
        assert sl[(season, "starter")]["n"] + sl[(season, "bench")]["n"] == int(sm.sum())
        assert sl[(season, "all")]["delta"] == pytest.approx(b["delta"])
        # slices with fewer than the minimum rows are listed without a delta (they cannot be judged)
        small = [
            s
            for (se, _), s in sl.items()
            if se == season and s["n"] < E.MIN_SLICE_N and s["slice"] != "all"
        ]
        assert small and all("delta" not in s for s in small)
        big = [s for (se, _), s in sl.items() if se == season and s["n"] >= E.MIN_SLICE_N]
        assert all("delta" in s for s in big)


def test_score_cell_for_log_loss_and_minutes_cells(cache: dict[str, Any]) -> None:
    p = E.score_cell("P", cache["status"])
    pb = next(r for r in p["rows"] if r["season"] == 2024 and r["arm"] == "B")
    assert "ece" in pb and pb["unit"] == "log loss" and pb["delta"] < 0
    m = E.score_cell("M", cache["status"])
    mb = next(r for r in m["rows"] if r["season"] == 2024 and r["arm"] == "B")
    assert mb["unit"] == "minutes CRPS" and mb["delta"] == pytest.approx(-0.3, abs=0.02)
    assert "cov80" in mb and "bias" in mb
    assert any(r["arm"] == "B_shuf" for r in m["rows"])


def test_score_cell_refuses_a_meta_join_that_changes_the_row_count(cache: dict[str, Any]) -> None:
    dup = pl.concat([cache["meta"], cache["meta"].head(1)])
    # unique(KEYS) in the module protects the join: duplicated meta rows must not fan out
    dup.write_parquet(E.CACHE / "meta_props.parquet")
    res = E.score_cell("pts", cache["status"])
    assert res["rows"][0]["n_rows"] == int((cache["season"] == 2023).sum())


def test_team_status_table_reads_games_up_to_the_last_open_season(cache: dict[str, Any]) -> None:
    st = E.team_status_table()
    assert st.columns == ["game_id", "team_id", "status", "late"]
    assert st["game_id"].n_unique() == 2 * G_PER_SEASON
    assert set(st["status"].unique().to_list()) <= {"contending", "middle", "out"}


# ------------------------------------------------------------------------------ report and attack


def test_run_report_end_to_end_writes_results_and_markdown(cache: dict[str, Any]) -> None:
    res = E.run_report()
    cells = set(res["verdict"]["cells"])
    assert cells == {"P", "M", "pts"}  # cells with no cached B arm are skipped, not failed
    assert res["verdict"]["close"] is False
    v = res["verdict"]["cells"]["pts"]
    assert v["select_2023"] and v["rule1_2024"] and v["beats_placebo"] is True
    assert v["oracle_bound_ok"] is True and v["gate_ok"] is True
    assert set(v["knockout_drop"]) == {"LOAD", "SCHEDULE", "TRAVEL"}
    # dropping TRAVEL (== base here) removes the whole gain: one family carries it
    assert v["knockout_drop"]["TRAVEL"]["carries_by_drop"] is True
    assert v["tracking_claim"] is False  # B - NT == 0 is not <= floor/2: tracking adds nothing
    assert res["d1_rest_dnp"] == [{"k": 1}]
    assert (
        "error" in res["d2_decision_score"]
    )  # the secondary score records its failure, never raises
    for r in res["rows"]:
        if r["arm"] == "B":
            assert "p_bh" in r
    on_disk = json.loads((E.OUT_DIR / "results.json").read_text())
    assert on_disk["sha256"] == E.FROZEN_FULL
    md = E.REPORT_MD.read_text()
    assert md.startswith("# WORKLOAD results") and "## Verdict by cell" in md
    assert "| pts (integer CRPS) | 2024 |" in md and "| M (minutes CRPS) | 2023 |" in md
    assert md.count("* Rule:") == len(E.AMBIGUITIES)


def test_run_attack_without_passing_cells_writes_an_empty_attack(cache: dict[str, Any]) -> None:
    (E.OUT_DIR / "results.json").write_text(
        json.dumps({"verdict": {"cells": {"pts": {"pass_before_attack": False}}}})
    )
    out = E.run_attack()
    assert out == {"cells": {}, "passing": []}
    assert json.loads((E.OUT_DIR / "attack.json").read_text()) == out


def test_run_attack_scores_the_stored_attack_arms_for_a_passing_cell(
    cache: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    res = E.run_report()
    v = res["verdict"]["cells"]["M"]
    v["pass_before_attack"] = True  # force the attack on a cell whose arms are already cached
    (E.OUT_DIR / "results.json").write_text(json.dumps(res, default=float))
    # M refits are the maintainer's: replace the heavy frame loader/walk with the cached scores
    monkeypatch.setattr(E, "load_m_frame", lambda: (None, None, None))
    monkeypatch.setattr(
        E, "m_walk", lambda *a, **k: dict(np.load(E.CACHE / "M__shuffle.npz", allow_pickle=True))
    )
    out = E.run_attack()
    c = out["cells"]["M"]
    assert out["passing"] == ["M"]
    assert c["gain_full"] == pytest.approx(-0.3, abs=0.02)
    assert c["mde"] > 0 and c["shuffled_ok"] in (True, False)
    assert (
        set(c["gain_alone"]) == {"LOAD", "SCHEDULE", "TRAVEL"} and c["gain_alone"]["LOAD"] is None
    )


def test_main_report_runs_the_report_stage(cache: dict[str, Any]) -> None:
    assert E.main(["report"]) == 0
    assert E.REPORT_MD.exists()


def test_run_report_requires_a_passing_gate(cache: dict[str, Any]) -> None:
    (E.OUT_DIR / "gate.json").write_text(json.dumps({"gate_ok": False, "sha256": E.FROZEN_FULL}))
    with pytest.raises(RuntimeError, match="has not passed"):
        E.run_report()


def test_render_shows_na_for_missing_numbers() -> None:
    assert E._f(None) == "n/a" and E._f(float("nan")) == "n/a" and E._f(-0.12345) == "-0.1235"
