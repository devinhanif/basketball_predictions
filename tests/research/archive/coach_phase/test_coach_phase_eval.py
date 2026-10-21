# ruff: noqa: E501
"""COACH_PHASE harness pieces on synthetic data: freeze refusal, lock ownership, ridge, walk-forward as-of, factors, pass rule."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.archive.coach_phase import coach_phase_eval as E
from research.eval.team_budget_eval import heavy_lock
from research.features import coach_habits as CH


def test_require_frozen_refuses_wrong_sha_and_edited_doc(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("rule\n=== RESULTS BELOW ===\nnot run\n")
    with pytest.raises(RuntimeError):
        E.require_frozen(doc, "0" * 64)
    from research.eval.f11_lineup_context import frozen_sha256

    sha = frozen_sha256(doc)
    E.require_frozen(doc, sha)  # frozen text unchanged: passes
    doc.write_text("rule changed\n=== RESULTS BELOW ===\nnot run\n")
    with pytest.raises(RuntimeError):
        E.require_frozen(doc, sha)
    doc.write_text("rule\n=== RESULTS BELOW ===\nresults appended later are fine\n")
    E.require_frozen(doc, sha)


def test_committed_doc_matches_frozen_constant() -> None:
    E.require_frozen()  # the repo copy of docs/prereg/COACH_PHASE.md is the frozen one


def test_heavy_lock_removes_only_its_own(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    with heavy_lock(lock, retry_s=0.0, max_wait_s=0.0):
        assert (lock / "pid").read_text() == str(os.getpid())
        with pytest.raises(TimeoutError), heavy_lock(lock, retry_s=0.0, max_wait_s=0.0):
            pass
        assert lock.exists()  # the failed second taker did not remove ours
    assert not lock.exists()
    lock.mkdir()
    (lock / "pid").write_text("1")
    with pytest.raises(TimeoutError), heavy_lock(lock, retry_s=0.0, max_wait_s=0.0):
        pass
    assert lock.exists()  # someone else's lock survives


def test_ridge_recovers_linear_signal_and_standardizes() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(4000, 3)) * np.array([1.0, 50.0, 0.01])
    y = 2.0 * x[:, 0] + 0.03 * x[:, 1] + 100 * x[:, 2] + 5.0 + rng.normal(0, 0.1, 4000)
    m = E.ridge_fit(x, y, alpha=1.0)
    assert np.abs(E.ridge_predict(m, x) - y).mean() < 0.2
    const = np.column_stack([x[:, 0], np.ones(4000)])  # a zero-variance column must not blow up
    assert np.isfinite(E.ridge_predict(E.ridge_fit(const, y), const)).all()


def _wf_data(
    n: int = 600,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]]]:
    rng = np.random.default_rng(1)
    day = np.sort(rng.integers(19000, 19400, n))
    season = np.where(day < 19200, 2022, 2023)
    x = rng.normal(size=(n, 2))
    y = x[:, 0] * 2.0 + rng.normal(0, 0.1, n)
    edges = [(19200 + 30 * k, 19230 + 30 * k) for k in range(7)]
    return x, y, day, season, edges


def test_walk_forward_is_strictly_as_of_and_planted_future() -> None:
    x, y, day, season, edges = _wf_data()
    ok = np.ones(len(y), bool)
    a = E.walk_forward_delta(x, y, day, season, ok, edges, seasons=(2023,), min_train=50)
    assert np.isnan(a[season == 2022]).all() and np.isfinite(a[season == 2023]).all()
    y2 = y.copy()
    y2[day >= 19290] += 1000.0  # plant the future
    b = E.walk_forward_delta(x, y2, day, season, ok, edges, seasons=(2023,), min_train=50)
    early = (day < 19290) & (season == 2023)
    assert np.allclose(a[early], b[early])  # blocks before the plant are untouched
    assert not np.allclose(a[day >= 19320], b[day >= 19320])  # control: later blocks do move
    p = E.walk_forward_delta(
        x, y, day, season, ok, edges, seasons=(2023,), min_train=50, permute=True
    )
    assert (
        np.abs(p[early] - y[early]).mean() > np.abs(a[early] - y[early]).mean()
    )  # shuffled labels lose the signal
    assert np.isnan(
        E.walk_forward_delta(x, y, day, season, ok, edges, seasons=(2023,), min_train=10_000)
    ).all()


def test_factors_and_columns() -> None:
    p = np.array([30.0, 10.0, 20.0, 20.0])
    d = np.array([3.0, 10.0, -10.0, np.nan])
    r = E.prop_factor(p, d)
    assert r[0] == pytest.approx(1.1) and r[1] == 1.25 and r[2] == 0.8 and r[3] == 1.0
    assert E.minutes_mu(p, np.array([30.0, 0, 0, 0]))[0] == 48.0
    base, plain = E.arm_columns("CP", ("top8",))
    assert not any(c.endswith("_top8") for c in base + plain) and "h_reg_depth" in base
    assert E.arm_columns("PH") == (list(E.BASE_COLS), [])
    assert E.arm_columns("PL", ())[0][-1].startswith("pl_")
    assert all(not c.endswith("_blowout") for c in E.arm_columns("CP", (), "blowout")[0])
    assert E.arm_columns("S0") == (["po", "late15"], [])


def test_design_matrix_triples_and_dropping() -> None:
    f = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b_top8": [1.0, 1.0, 2.0], "c": [0.0, 1.0, 0.0]})
    z, s = np.array([1.0, 2.0, 3.0]), np.array([0.0, 1.0, 1.0])
    x, names = E.design_matrix(f, ["c"], ["a", "b_top8"], z, s, ("top8",))
    assert names == ["a", "a*z", "a*s", "c"]
    assert np.allclose(x[:, 1], f["a"].to_numpy() * z) and np.allclose(
        x[:, 2], f["a"].to_numpy() * s
    )


def test_block_edges_cover_month_blocks() -> None:
    dates = [dt.date(2023, 10, 24), dt.date(2023, 10, 30), dt.date(2023, 12, 2)]
    e = E.block_day_edges(dates, np.array([True, True, True]))
    assert len(e) == 2 and e[0][0] < e[0][1] <= e[1][0]


def _meta(n: int) -> pl.DataFrame:
    rng = np.random.default_rng(2)
    return pl.DataFrame(
        {
            "po": (np.arange(n) % 5 == 0).astype(float),
            "late15": (np.arange(n) % 3 == 0).astype(float),
            "stage": ["004" if i % 5 == 0 else "002" for i in range(n)],
            "s10": rng.random(n),
            "series_game": (np.arange(n) % 7 + 1).astype(float),
            "elim_game": (np.arange(n) % 4 == 0).astype(float),
            "home_game": (np.arange(n) % 2).astype(float),
            "round": (np.arange(n) % 4 + 1).astype(float),
            "clinched_6": np.zeros(n),
            "elim_6": np.ones(n),
            "elim_10": np.zeros(n),
            "coach_n_reg": np.full(n, 30.0),
            "coach_n_po": np.zeros(n),
            "b2b": np.zeros(n),
        }
    )


def test_slice_masks_definitions() -> None:
    m = E.slice_masks(_meta(60))
    assert m["phase"].sum() == (m["po"] | m["late15"]).sum()
    assert not (m["po"] & m["early"]).any() and not (m["late15"] & m["early"]).any()
    assert m["all"].all() and (m["starter"] ^ m["bench"]).all()
    assert m["series_game_1"].sum() + m["series_game_2"].sum() <= m["po"].sum()


def _scores(n: int, shift: float, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {"crps": rng.random(n) + shift, "pit": rng.random(n), "resid": rng.normal(size=n)}


def test_score_target_bh_and_pass_rule_end_to_end() -> None:
    n = 2400
    meta = _meta(n)
    gid = np.array([f"g{i // 6}" for i in range(n)])
    season = np.where(np.arange(n) < n // 2, 2023, 2024)
    base = _scores(n, 0.0, 3)
    good = {**base, "crps": base["crps"] - 0.5}  # a large genuine gain on every slice
    noise = {**base, "crps": base["crps"] + np.random.default_rng(4).normal(0, 0.01, n)}
    masks = E.slice_masks(meta)
    rows: list[dict[str, Any]] = []
    for target, floor in (
        ("minutes", 0.0),
        ("pts", 0.0),
        ("reb", 0.0),
        ("ast", 0.0),
        ("fg3m", 0.0),
    ):
        sc = {
            "P" if target == "minutes" else "A0": base,
            "PH": good,
            "CP": good,
            "PL": noise,
            "S0": noise,
            "O1": {**base, "crps": base["crps"] - 0.9},
        }
        rows += E.score_target(
            target, sc, gid, season, masks, "P" if target == "minutes" else "A0", floor
        )
    E.add_bh(rows)
    assert any("p_bh" in r for r in rows)
    res = {"rows": rows}
    v = E.evaluate(res, True, True, None, True)
    assert v["cells"]["PH/minutes/po"]["select_2023"] and v["cells"]["PH/pts/phase"]["rule1_2024"]
    assert (
        v["cells"]["PH/pts/phase"]["beats_placebo"]
        and v["cells"]["PH/pts/phase"]["not_beating_oracle"]
    )
    assert v["cells"]["PH/pts/phase"]["pass_before_attack"] and not v["close"]
    attack = {
        "shuffled": {a: {t: {"gain": 0.0} for t in ("minutes", *E.STATS)} for a in ("PH", "CP")},
        "knockout": {"depth": {t: {"carries": False} for t in ("minutes", *E.STATS)}},
    }
    v2 = E.evaluate(res, True, True, attack, True)
    assert v2["cells"]["PH/pts/phase"]["PASS"]
    assert v2["coach_claim"]["status"] in ("none", "SUGGESTIVE (report season only; never PASS)")
    assert "PASS" not in v2["coach_claim"]["status"].replace("never PASS", "")
    bad = E.evaluate(res, False, True, None, True)  # planted tests failed: nothing passes
    assert not bad["cells"]["PH/pts/phase"]["pass_before_attack"]


def test_carries_and_coach_claim_threshold() -> None:
    assert E.carries(1.0, 0.1) and not E.carries(1.0, 0.5) and not E.carries(-1.0, 0.0)
    tab = {
        ("pts", 2024, "CP", "phase"): {"minus_PH": -0.01},
        ("minutes", 2024, "CP", "phase"): {"minus_PH": -0.001},
    }
    c = E.coach_claim(tab, {"knockout": {"depth": {"pts": {"carries": False}}}}, True)
    assert (
        c["metrics"]["pts"]["meets"]
        and not c["metrics"]["minutes"]["meets"]
        and c["status"].startswith("SUGGESTIVE")
    )
    assert (
        E.coach_claim(tab, {"knockout": {"depth": {"pts": {"carries": True}}}}, True)["status"]
        == "none"
    )
    assert E.coach_claim(tab, None, True)["status"] == "none"
    assert E.coach_claim(tab, {"knockout": {}}, False)["status"] == "none"


def test_audit_missingness_gap() -> None:
    n = 400
    rng = np.random.default_rng(5)
    y = rng.uniform(0, 40, n)
    meta = pl.DataFrame(
        {
            "coach_n_reg": np.full(n, 10.0),
            "coach_n_po": np.where(rng.random(n) < 0.1, 0.0, 5.0),
            "po": np.ones(n),
            "prev_series_missing": (rng.random(n) < 0.18).astype(float),
        }
    )
    out = E.audit_missingness(pl.DataFrame(), {"meta": meta, "y": y})
    assert out["gaps"]["coach_fallback_reg"] == 0.0
    meta2 = meta.with_columns(pl.Series("coach_n_reg", np.where(y < 5, 0.0, 10.0)))
    assert not E.audit_missingness(pl.DataFrame(), {"meta": meta2, "y": y})["pass_gap_lt_2pp"]


def test_render_report_and_main_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    row = {
        "target": "pts",
        "season": 2024,
        "arm": "PH",
        "slice": "phase",
        "n_rows": 10,
        "n_games": 3,
        "crps": 3.0,
        "bias": 0.0,
        "cov80": 0.8,
        "dcrps": -0.01,
        "ci_lo": -0.02,
        "ci_hi": 0.0,
        "p": 0.1,
        "mde": 0.01,
    }
    (tmp_path / "results.json").write_text(
        json.dumps({"rows": [row], "verdict": {"cells": {}}, "ambiguities": {"k": "v"}})
    )
    txt = E.render_report(tmp_path)
    assert "COACH_PHASE results" in txt and "| pts | 2024 | PH | phase |" in txt and "* k: v" in txt
    monkeypatch.setattr(E, "render_report", lambda: "ok")
    monkeypatch.setattr(E, "REPORT_MD", tmp_path / "r.md")
    assert E.main(["report"]) == 0 and (tmp_path / "r.md").exists()


# --------------------------------------------------------------------------- synthetic world: frame, planted checks, arms, attack
T8 = [
    1610612738,
    1610612752,
    1610612755,
    1610612761,
    1610612747,
    1610612743,
    1610612744,
    1610612742,
]  # BOS NYK PHI TOR LAL DEN GSW DAL


def _world() -> dict[str, Any]:
    rng = np.random.default_rng(7)
    games, box, coach = [], [], []

    def add(gid: str, d: dt.date, season: int, h: int, a: int) -> None:
        games.append(
            {
                "game_id": gid,
                "game_date": d,
                "season": season,
                "home_team": h,
                "away_team": a,
                "home_pts": 100 + int(rng.integers(-15, 15)),
                "away_pts": 100,
            }
        )
        for t in (h, a):
            coach.append(
                {
                    "game_id": gid,
                    "team_id": t,
                    "coach_key": f"c{T8.index(t) // 2}",
                    "coach_id": 1,
                    "coach_name": "x",
                }
            )
            mins = np.sort(rng.uniform(4, 38, 8))[::-1]
            for i, m in enumerate(mins):
                box.append(
                    {
                        "game_id": gid,
                        "team_id": t,
                        "player_id": T8.index(t) * 100 + i,
                        "minutes": float(m),
                        "starter": i < 5,
                    }
                )

    for si, season in enumerate((2022, 2023, 2024)):
        d0 = dt.date(2022 + si, 10, 20)
        k = 0
        for day in range(80):
            order = np.roll(T8, day)
            for j in range(4):
                add(
                    f"002{23 + si}{k:05d}",
                    d0 + dt.timedelta(days=day),
                    season,
                    int(order[2 * j]),
                    int(order[2 * j + 1]),
                )
                k += 1
        for g in range(5):
            add(
                f"004{23 + si}00{g + 1:03d}1",
                dt.date(2023 + si, 4, 20) + dt.timedelta(days=2 * g),
                season,
                T8[g % 2],
                T8[1 - g % 2],
            )
    return {
        "games": pl.DataFrame(games),
        "box": pl.DataFrame(box).with_columns(pl.col("player_id").cast(pl.Int32)),
        "coach": pl.DataFrame(coach),
    }


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    w = _world()
    rng = np.random.default_rng(8)
    pm = (
        w["box"]
        .select("game_id", "player_id")
        .with_columns(
            pl.Series("p_mu", rng.uniform(8, 32, w["box"].height)),
            pl.Series("p_sigma", rng.uniform(3, 6, w["box"].height)),
        )
    )
    monkeypatch.setattr(E, "load_world", lambda: dict(w, sc=None))
    monkeypatch.setattr("research.eval.minutes_v2_eval.production_minutes_table", lambda sc: pm)
    monkeypatch.setattr(E, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(E, "CACHE_DIR", tmp_path / "cache")
    return w


def _fake_rows(u: pl.DataFrame, kind: str) -> dict[str, Any]:
    idx = np.flatnonzero(u["season"].is_in(list(E.SEASONS)).to_numpy())
    rows = u[idx]
    meta = rows.with_columns(pl.Series("b2b", np.zeros(rows.height)))
    d = {
        "uidx": idx,
        "y": rows["minutes"].to_numpy().astype(float),
        "gid": rows["game_id"].to_numpy().astype(str),
        "season": rows["season"].to_numpy().astype(int),
        "meta": meta,
    }
    if kind == "minutes":
        d["p_crps"] = np.zeros(rows.height)
    else:
        q = np.linspace(0.0, 1.0, 199)
        d["grid"] = rows["p_mu"].to_numpy()[:, None] * 0.5 * (0.2 + 1.6 * q[None, :])
        d["y"] = np.round(rows["minutes"].to_numpy().astype(float) * 0.5)
        d["ok"] = np.ones(rows.height, bool)
    return d


def test_frame_planted_arms_attack_report_on_synthetic_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    w = _install(monkeypatch, tmp_path)
    fr = E.build_all()
    u = fr["universe"]
    assert u.height == w["box"].height and u["prev_series_missing"].sum() > 0
    E.run_frame()
    assert "by_season_stage" in capsys.readouterr().out
    planted = E.planted_checks(fr["world"], n_games=12)
    assert planted["ok"], planted
    monkeypatch.setattr(
        E,
        "minutes_rows",
        lambda u_: dict(_fake_rows(u_, "minutes"), gid=_fake_rows(u_, "minutes")["gid"]),
    )
    monkeypatch.setattr(E, "prop_rows", lambda u_, s: _fake_rows(u_, "props"))
    monkeypatch.setattr(E, "decision_scores", lambda *a, **k: {"A0": {"n_legs": 0}})
    E.OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = {
        "gate_ok": True,
        "frozen_sha256": E.FROZEN_FULL,
        "planted": planted,
        "missingness_audit": {"pass_gap_lt_2pp": True},
        "g4_coach": {"pass_report_season_coaches": True},
        "habit_screen": {"top8": {"keep": False}, "depth": {"keep": True}},
    }
    (E.OUT_DIR / "gate_frozen.json").write_text(json.dumps(gate))
    assert E._screen_dropped() == ("top8",)
    res = E.run_arms()
    assert res["dropped_habits"] == ["top8"] and any(
        r["arm"] == "CP" and r["slice"] == "phase" for r in res["rows"]
    )
    assert "cells" in res["verdict"] and res["decision_score"] == {"A0": {"n_legs": 0}}
    att = E.run_attack()
    assert (
        set(att["shuffled"]) == {"PH", "CP"}
        and "depth" in att["knockout"]
        and "top8" not in att["knockout"]
    )
    assert "COACH_PHASE results" in E.render_report()
    # the cached deltas are reused (same numbers) on a second call
    again = E._cached(
        "delta_PH", u.height, lambda: (_ for _ in ()).throw(AssertionError("should be cached"))
    )
    assert len(again) == u.height


def test_main_refuses_without_gate_and_lock_dir_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(E, "OUT_DIR", tmp_path / "out")
    with pytest.raises(FileNotFoundError):
        E.run_arms()  # no gate file: nothing may be fitted
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "gate_frozen.json").write_text('{"gate_ok": false}')
    with pytest.raises(RuntimeError):
        E.run_arms()


def test_run_gate_and_main_dispatch_on_synthetic_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import contextlib
    import json

    import duckdb

    from research.archive.coach_phase import coach_phase_gates as G

    _install(monkeypatch, tmp_path)
    coach = {
        "2023": {"coaches_with_prior_po_ge20": 9},
        "2024": {"coaches_with_prior_po_ge20": 9},
        "pass_report_season_coaches": True,
        "pass_select_season_coaches": False,
    }
    sc = {"playoff_team_games": 1}
    ref = tmp_path / "gates.json"
    ref.write_text(json.dumps({"coach": coach, "series_clinch": sc}))
    monkeypatch.setattr(E, "GATES_JSON", ref)
    monkeypatch.setattr(G, "gate_a0", lambda: {"max_abs_diff": 0.0, "pass": True})
    monkeypatch.setattr(G, "gate_coach", lambda con: coach)
    monkeypatch.setattr(G, "gate_series_and_clinch", lambda con: sc)
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: real_connect(":memory:"))

    def fake_minutes(u: pl.DataFrame) -> dict[str, Any]:
        d = _fake_rows(u, "minutes")
        d["p_crps"] = E.minutes_scores_for(d, u, {})["P"]["crps"]
        return d

    monkeypatch.setattr(E, "minutes_rows", fake_minutes)
    res = E.run_gate()
    assert (
        res["p_reproduction"]["pass"]
        and res["planted"]["ok"]
        and "alt_reading_po_rows_only" in res["missingness_audit"]
    )
    assert set(res["habit_screen"]) == set(CH.HABITS)
    assert (E.OUT_DIR / "gate_frozen.json").exists()

    @contextlib.contextmanager
    def lock() -> Any:
        yield

    monkeypatch.setattr(E, "heavy_lock", lock)
    monkeypatch.setattr(E, "run_gate", lambda: {"gate_ok": False})
    assert E.main(["gate"]) == 1  # a failed gate stops the run
    monkeypatch.setattr(E, "run_gate", lambda: {"gate_ok": True})
    calls: list[str] = []

    def arms() -> dict[str, int]:
        calls.append("arms")
        return {"x": 1}

    def attack() -> dict[str, int]:
        calls.append("attack")
        return {"x": 1}

    monkeypatch.setattr(E, "run_arms", arms)
    monkeypatch.setattr(E, "run_attack", attack)
    monkeypatch.setattr(E, "render_report", lambda: "r")
    monkeypatch.setattr(E, "REPORT_MD", tmp_path / "r.md")
    assert E.main(["run"]) == 0 and calls == ["arms", "attack"]
    assert E.main(["frame"]) == 0
