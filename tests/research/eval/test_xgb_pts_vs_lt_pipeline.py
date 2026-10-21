# ruff: noqa: E501
"""XGB_PTS_VS_LT: the evaluator's input pins, loaders, gate audits, alignment, scoring and report.

Complements ``test_xgb_pts_vs_lt_eval.py`` (freeze refusal, rule logic). Everything here runs on
small synthetic arrays and tmp files; the engine, refits and real DB are the maintainer's.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from nba.eval.context_residual_eval import crps_from_quantiles
from nba.eval.lower_tail_eval import EPS
from research.eval import xgb_pts_vs_lt_eval as ev

TAUS = (np.arange(1, 200) - 0.5) / 199


def _grid(rng: np.random.Generator, n: int, lam: float = 12.0) -> np.ndarray:
    """Integer 199-grid from a Poisson sample (the production support)."""
    base = np.floor(np.quantile(rng.poisson(lam, 4000), TAUS))
    return np.repeat(base[None, :], n, axis=0)


# ------------------------------------------------------------------------------ pins and hashes


def test_doc_status_parses_first_status_line(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text("# t\nStatus: FROZEN.\nStatus: DRAFT\n")
    assert ev.doc_status(p) == "FROZEN"
    p.write_text("# nothing here\n")
    assert ev.doc_status(p) == "MISSING"


def test_sha_file_streams_and_sha_files_depends_on_names(tmp_path: Path) -> None:
    a, b = tmp_path / "a.npz", tmp_path / "b.npz"
    a.write_bytes(b"x" * (3 << 20))  # larger than one 1 MiB read chunk
    b.write_bytes(b"y")
    assert ev.sha_file(a) == hashlib.sha256(b"x" * (3 << 20)).hexdigest()
    assert ev.sha_files([b, a]) == ev.sha_files([a, b])  # order-independent (sorted by name)
    renamed = tmp_path / "c.npz"
    renamed.write_bytes(b"y")
    assert ev.sha_files([a, renamed]) != ev.sha_files([a, b])  # the file name is part of the pin
    b.write_bytes(b"z")
    assert ev.sha_files([a, b]) != ev.sha_files([a, renamed])  # so is the content


def test_frozen_inputs_hashes_every_pin_and_flags_missing_dk_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    lt = root / "reports/lower_tail"
    lt.mkdir(parents=True)
    for name in ("components_pts.npz", "components_pi.npz", "candidates.json"):
        (lt / name).write_bytes(name.encode())
    for rel in (
        "data/colab/ctxres_v2/ctxres_v2.parquet",
        "data/colab/ridge_v2/ridge_v2.parquet",
        "data/colab/ridge_v2/base_config.json",
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode())
    run = root / "run"
    for v in ("hybrid_lf", "v1_prod"):
        d = run / "checkpoints/units" / v / "pts"
        d.mkdir(parents=True)
        (d / "1.npz").write_bytes(v.encode())
    rt = root / "rt"
    for v in ("fixsched", "v1only"):
        d = rt / v / "pts"
        d.mkdir(parents=True)
        (d / "1.npz").write_bytes(v.encode())
    for name, val in (
        ("ROOT", root),
        ("LT_DIR", lt),
        ("LOWER_TAIL_JSON", lt / "candidates.json"),
        ("RUN_DIR", run),
        ("RT_UNITS", rt),
        ("DK_CACHE", root / "dk.parquet"),
    ):
        monkeypatch.setattr(ev, name, val)
    pins = ev.frozen_inputs()
    assert pins["components_pts.npz"] == hashlib.sha256(b"components_pts.npz").hexdigest()
    assert pins["dk_t60_pts.parquet"] == "MISSING"
    assert len(pins) == 11
    (root / "dk.parquet").write_bytes(b"dk")
    assert ev.frozen_inputs()["dk_t60_pts.parquet"] == hashlib.sha256(b"dk").hexdigest()
    # changing one unit file changes exactly that pin
    (run / "checkpoints/units/hybrid_lf/pts/1.npz").write_bytes(b"changed")
    after = ev.frozen_inputs()
    assert {k for k in pins if after[k] != pins[k]} == {"units_hybrid_lf_pts", "dk_t60_pts.parquet"}


# ------------------------------------------------------------------------------ scoring helpers


def test_clustered_ci_brackets_the_mean_delta() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.arange(100), 5)
    delta = rng.normal(-0.05, 0.2, 500)
    c = ev.clustered(delta, gid, n_boot=300)
    assert c["point"] == pytest.approx(delta.mean())
    assert c["lo"] < c["point"] < c["hi"]
    assert ev.clustered(delta, gid, n_boot=300) == c  # seeded: deterministic
    zero = ev.clustered(np.zeros(10), np.arange(10), n_boot=50)
    assert zero == {"point": 0.0, "lo": 0.0, "hi": 0.0}


def test_ll_rows_clips_extreme_probabilities() -> None:
    p = np.array([0.0, 1.0, 0.5, 0.5])
    ev_ = np.array([True, False, True, False])
    ll = ev.ll_rows(p, ev_)
    assert np.all(np.isfinite(ll)) and np.all(ll > 0)
    assert ll[2] == pytest.approx(np.log(2)) and ll[3] == pytest.approx(np.log(2))
    assert ll[0] == pytest.approx(-np.log(EPS))  # a certain miss is capped, not infinite


def test_thr_ll_uses_the_share_of_grid_at_or_above_the_threshold() -> None:
    qi = np.repeat(np.arange(199, dtype=float)[None, :], 2, axis=0)  # P(Y >= 100) = 99/199
    y = np.array([150.0, 10.0])
    n = np.array([100.0, 100.0])
    ll = ev.thr_ll(qi, y, n)
    p = 99 / 199
    assert ll[0] == pytest.approx(-np.log(p)) and ll[1] == pytest.approx(-np.log(1 - p))


def test_crps_int_is_score_of_integer_mapped_grid() -> None:
    q = np.full((1, 199), 10.2)
    # continuous 10.2 -> 10; the helper must score the mapped grid, not the raw one
    mapped = ev.to_arm_int(q)
    assert set(mapped[0].tolist()) == {10.0}
    assert ev.crps_int(q, np.array([10.0]))[0] == pytest.approx(0.0)


def test_row_calibration_flags_a_biased_forecast() -> None:
    rng = np.random.default_rng(1)
    n = 3000
    y = rng.poisson(12, n).astype(float)
    good = ev.row_calibration(_grid(rng, n), y)
    assert abs(good["bias"]) < 0.3 and 0.75 < good["cov80"] < 0.85 and good["guard"]["ok"]
    bad = ev.row_calibration(_grid(rng, n, lam=18.0), y)
    assert bad["bias"] < -4 and bad["guard"]["ok"] is False


def test_build_masks_partition_rows() -> None:
    meta = pl.DataFrame(
        {
            "game_id": ["0022300001", "0042300001", "0022300002", "0012300001"],
            "minutes": [30.0, 2.0, 5.0, 4.9],
            "starter10": [1.0, 0.0, 0.5, 0.49],
            "team_game_no": [3.0, 14.0, 15.0, 70.0],
            "team_id": [1, ev.PHI, 3, 4],
            "starter_act": [1.0, 0.0, 1.0, 0.0],
            "tip_hour": [20.0, None, 18.5, 19.0],
        }
    )
    m = ev.build_masks(meta)
    assert m["regular"].tolist() == [True, False, True, False]
    assert (m["regular"] | m["postseason"]).all() and not (m["regular"] & m["postseason"]).any()
    assert m["minutes>=5"].tolist() == [True, False, True, False]
    assert m["starter"].tolist() == [True, False, True, False]
    assert m["first15"].tolist() == [True, True, False, False]
    assert m["without_PHI"].tolist() == [True, False, True, True]
    # a missing tip time is in neither tip slice (it cannot be called safe or exposed)
    assert m["tip>=19ET(proxy-safe)"].tolist() == [True, False, False, True]
    assert m["tip<19ET(proxy-exposed)"].tolist() == [False, False, True, False]


def test_check_a0_repro_requires_both_values_and_row_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(2)
    n = 40
    y = rng.poisson(12, 2 * n).astype(float)
    season = np.array([2023] * n + [2024] * n)
    qi = _grid(rng, 2 * n).astype(np.int16)
    a0 = {"meta": pl.DataFrame({"season": season, "y": y}), "qi": {"off": qi, "mixture": qi}}
    stored = {}
    for s in ("2023", "2024"):
        m = season == int(s)
        crps = float(crps_from_quantiles(qi[m].astype(float), y[m]).mean())
        stored[s] = {"off": {"crps_int": crps, "n": int(m.sum())}, "mixture": {"crps_int": crps}}
    cand = tmp_path / "candidates.json"
    cand.write_text(json.dumps({"pts": stored}))
    monkeypatch.setattr(ev, "LOWER_TAIL_JSON", cand)
    r = ev.check_a0_repro(a0)
    assert r["ok"] is True and r["detail"]["2023_mixture"]["delta"] == pytest.approx(0.0)
    stored["2024"]["mixture"]["crps_int"] += 1e-3
    cand.write_text(json.dumps({"pts": stored}))
    r = ev.check_a0_repro(a0)
    assert r["ok"] is False and r["detail"]["2024_mixture"]["ok"] is False
    assert r["detail"]["2024_off"]["ok"] is True
    stored["2024"]["mixture"]["crps_int"] -= 1e-3
    stored["2024"]["off"]["n"] += 1  # one row missing from A0 is a failure even if CRPS matches
    cand.write_text(json.dumps({"pts": stored}))
    assert ev.check_a0_repro(a0)["ok"] is False


# ------------------------------------------------------------------------------ alignment and ctx


def _frame_ctx(n_train: int = 5, n_test: int = 12) -> SimpleNamespace:
    n = n_train + n_test
    season = [2022] * n_train + [2023] * (n_test // 2) + [2024] * (n_test - n_test // 2)
    df = pd.DataFrame(
        {
            "game_id": [f"002{i // 3:07d}" for i in range(n)],
            "player_id": np.arange(n) + 1,
            "season": season,
            "team_id": (np.arange(n) % 3) + 1,
            "team_game_no": np.arange(n) % 82,
            "starter10": (np.arange(n) % 2).astype(float),
            "n_out_rot": np.zeros(n),
            "has_report": np.ones(n),
            "min_gap": np.arange(n) * 0.5,
        }
    )
    return SimpleNamespace(
        df=df, t0i=n_train, y={"pts": np.random.default_rng(11).poisson(12, n).astype(float)}
    )


def test_frame_meta_slices_the_scored_rows_in_engine_order() -> None:
    ctx = _frame_ctx()
    fm = ev.frame_meta(ctx)
    assert fm.height == 12
    assert fm["season"].unique().sort().to_list() == [2023, 2024]
    assert fm["y"].to_list() == ctx.y["pts"][5:].tolist()
    assert fm["game_id"].dtype == pl.Utf8 and fm["player_id"].dtype == pl.Int64
    assert fm["player_id"][0] == 6


def test_load_units_fills_blocks_and_reports_incomplete(tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        nt=6,
        t0i=10,
        blocks=[
            {"fold": 1, "a": 10, "b": 13, "ok": True},
            {"fold": 2, "a": 13, "b": 16, "ok": False},
        ],
    )
    d = tmp_path / "hybrid_lf" / "pts"
    d.mkdir(parents=True)
    np.savez(d / "1.npz", q=np.arange(3 * 199, dtype=np.float32).reshape(3, 199))
    q, ok = ev.load_units(ctx, tmp_path, "hybrid_lf")
    assert q.shape == (6, 199)
    assert ok.tolist() == [True, True, True, False, False, False]
    assert q[1, 0] == 199.0 and np.isnan(q[3:]).all()
    with pytest.raises(RuntimeError, match="incomplete"):
        ev.load_arm_units(ctx, tmp_path, "hybrid_lf")
    ctx.blocks[1]["ok"] = True
    np.savez(d / "2.npz", q=np.zeros((3, 199), np.float32))
    assert ev.load_arm_units(ctx, tmp_path, "hybrid_lf").shape == (6, 199)


def test_reproduce_stored_a1_fails_closed_on_incomplete_checkpoints_and_row_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(3)
    n_test = 40
    ctx = SimpleNamespace(
        nt=n_test,
        t0i=0,
        blocks=[{"fold": 1, "a": 0, "b": n_test, "ok": True}],
        y={"pts": rng.poisson(12, n_test).astype(float)},
        gid=np.repeat(np.arange(n_test // 4), 4),
        test_season=np.array([2023] * 20 + [2024] * 20),
    )
    run, rt = tmp_path / "run", tmp_path / "rt"
    monkeypatch.setattr(ev, "RUN_DIR", run)
    monkeypatch.setattr(ev, "RT_UNITS", rt)
    for base, v in (
        (run / "checkpoints/units", "hybrid_lf"),
        (run / "checkpoints/units", "v1_prod"),
        (rt, "fixsched"),
        (rt, "v1only"),
    ):
        d = base / v / "pts"
        d.mkdir(parents=True)
        shift = 0.0 if v == "v1_prod" else 0.6
        q = np.repeat(
            (np.floor(np.quantile(rng.poisson(12, 4000), TAUS)) + shift)[None, :], n_test, 0
        )
        np.savez(d / "1.npz", q=q.astype(np.float32))
    out = ev.reproduce_stored_a1(ctx)
    # the frozen row counts (27,619 / 27,583) cannot match 20 rows: the check must say so
    assert out["ok"] is False
    assert out["seasons"]["2023"]["n"] == 20 and out["seasons"]["2023"]["ok"] is False
    assert out["seasons"]["2024"]["red_team_reference"] == ev.REF_FAIR_2024
    # shifted-by-0.6 grid ceil(q - 0.5) differs from the unmapped map by at most one unit
    assert 0.0 <= out["integer_map_max_abs_diff_vs_ceil_m05"] <= 1.0
    assert set(out["descriptive_stored"]) == {
        f"{n}_vs_{b}_{s}"
        for n in ("fixsched", "v1only")
        for b in ("v1_prod", "hybrid_lf")
        for s in (2023, 2024)
    }
    # an incomplete stored checkpoint is an error, never a partial pass
    ctx.blocks[0]["ok"] = False
    with pytest.raises(RuntimeError, match="incomplete"):
        ev.reproduce_stored_a1(ctx)


# ------------------------------------------------------------------------------ gate audits


def test_align_keys_reports_dropped_and_extra_keys() -> None:
    a0 = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g3"],
            "player_id": [1, 2, 1, 1],
            "season": [2023, 2023, 2023, 2024],
        }
    )
    fr = pl.DataFrame(
        {"game_id": ["g1", "g2", "g9"], "player_id": [1, 1, 5], "season": [2023, 2023, 2024]}
    )
    k = ev.align_keys(a0, fr)
    assert k["i1"].tolist() == [0, 1] and k["i0"].tolist() == [0, 2]
    a23, a24 = k["acct"]["2023"], k["acct"]["2024"]
    assert (a23["n_a0"], a23["n_frame"], a23["n_intersection"]) == (3, 2, 2)
    assert a23["a0_only"] == 1 and a23["frame_only"] == 0 and a23["identical"] is False
    assert a23["a0_only_share"] == pytest.approx(1 / 3) and a23["a0_only_games"] == ["g1"]
    assert a24["frame_only"] == 1 and a24["a0_only"] == 1


def test_minutes_from_db_is_bounded_by_the_last_open_season(tmp_path: Path) -> None:
    db = tmp_path / "m.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table games (game_id varchar, season int)")
    con.execute("create table player_game_stats (game_id varchar, player_id int, minutes double)")
    con.execute("insert into games values ('a', 2024), ('b', 2025)")
    con.execute("insert into player_game_stats values ('a', 1, 30.0), ('b', 1, 20.0)")
    con.close()
    d = ev.minutes_from_db(str(db))
    assert d["game_id"].to_list() == ["a"] and d["player_id"].dtype == pl.Int64


def _missingness_world(tmp_path: Path, with_bad_col: bool) -> tuple[SimpleNamespace, str]:
    rng = np.random.default_rng(4)
    rows, mins = [], []
    for pid in range(1, 41):
        for g in range(10):
            season = 2022 if g < 3 else 2023
            minutes = 2.0 if pid == 1 else float(rng.uniform(0, 40))
            month = 1 + (g + pid) % 6
            rows.append(
                {
                    "game_id": f"g{pid}_{g}",
                    "player_id": pid,
                    "season": season,
                    "game_date": dt.date(2023, month, 1 + g),
                    "f_clean": 1.0,
                    "f_static": np.nan if pid == 1 else 1.0,
                    "f_bad": np.nan if (with_bad_col and minutes < 5 and pid != 1) else 1.0,
                }
            )
            mins.append((f"g{pid}_{g}", pid, minutes))
    df = pd.DataFrame(rows)
    db = tmp_path / f"miss_{with_bad_col}.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table games (game_id varchar, season int)")
    con.execute("create table player_game_stats (game_id varchar, player_id int, minutes double)")
    con.executemany("insert into games values (?, ?)", [(r["game_id"], r["season"]) for r in rows])
    con.executemany("insert into player_game_stats values (?, ?, ?)", mins)
    con.close()
    ctx = SimpleNamespace(
        df=df,
        base_cols={"pts": ["f_clean", "f_static"]},
        arms={"ref_fixed": {"pts": ["f_bad"]}},
    )
    return ctx, str(db)


def test_check_3_missingness_exempts_player_constant_nulls_only(tmp_path: Path) -> None:
    ctx, db = _missingness_world(tmp_path, with_bad_col=False)
    r = ev.check_3_missingness(ctx, db)
    assert r["n_rows"] == 400 and r["n_columns"] == 3
    # the static column's NULLs sit in one player who plays <5 minutes: a gap, but not an encoding of tonight
    assert r["columns"]["f_static"]["player_constant"] is True
    assert r["columns"]["f_static"]["all_gap_pp"] > ev.NULL_GAP_PP
    assert "f_static" in r["columns_over_2pp_all"]
    assert r["columns_over_2pp_not_exempt"] == {}
    assert r["as_drafted_ok"] is False and r["amended_ok"] is True
    assert r["columns"]["f_clean"]["all_gap_pp"] == 0.0
    assert r["columns"]["f_clean"]["player_constant"] is True  # no NULLs at all
    assert r["all_bucket_n"]["<5"] > 0 and sum(r["scored_bucket_n"].values()) < 400
    # a column NULL exactly when the player played little is the leak the audit exists to catch
    ctx, db = _missingness_world(tmp_path, with_bad_col=True)
    r = ev.check_3_missingness(ctx, db)
    assert "f_bad" in r["columns_over_2pp_not_exempt"]
    assert r["columns"]["f_bad"]["player_constant"] is False
    assert r["amended_ok"] is False and r["max_gap_pp_all"] >= r["columns"]["f_bad"]["all_gap_pp"]
    assert r["max_gap_pp_scored"] > ev.NULL_GAP_PP


def test_tip_exposure_counts_games_before_the_report_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    tips = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "tip_et": [
                dt.datetime(2024, 1, 1, 13, 0),
                dt.datetime(2024, 1, 1, 19, 0),
                dt.datetime(2024, 1, 1, 20, 30),
            ],
        }
    )
    monkeypatch.setattr(ev, "build_game_tipoff", lambda **_k: tips)
    meta = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "zz", "a", "c"],
            "season": [2023, 2023, 2023, 2023, 2024, 2024],
        }
    )
    out = ev.tip_exposure(meta)
    assert out["2023"] == {
        "n": 4,
        "tip_missing": 1,
        "tip_lt_19": 1,
        "tip_ge_19": 2,
        "share_lt_19": 0.25,
    }
    assert out["2024"]["tip_lt_19"] == 1 and out["2024"]["tip_ge_19"] == 1


def _pairs(path: Path, season: int, rows: list[dict[str, Any]]) -> None:
    pl.DataFrame(rows).write_parquet(path / f"pairs_{season}.parquet")


def _pair_row(gid: str, pid: int, line: float, **kw: Any) -> dict[str, Any]:
    base = {
        "game_id": gid,
        "player_id": pid,
        "stat": "pts",
        "book": "draftkings",
        "snapshot_kind": "t60",
        "line": line,
        "season": 2023,
        "_dnp": False,
        "status": 0,
    }
    return {**base, **kw}


def test_prepare_dk_filters_main_lines_and_reuses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ev, "DK_CACHE", tmp_path / "cache" / "dk.parquet")
    for s in ev.SEASONS:
        _pairs(
            tmp_path,
            s,
            [
                _pair_row(f"g{s}", 1, 10.5, season=s),
                _pair_row(f"g{s}", 2, 12.5, season=s, _dnp=True),  # DNP: dropped
                _pair_row(f"g{s}", 3, 9.5, season=s, book="fanduel"),
                _pair_row(f"g{s}", 4, 9.5, season=s, snapshot_kind="t30"),
                _pair_row(f"g{s}", 5, 9.5, season=s, stat="reb"),
                _pair_row(f"g{s}", 6, 9.0, season=s, status=1),  # push-status: dropped
            ],
        )
    info = ev.prepare_dk(tmp_path)
    assert info["rows"] == {"2023": 1, "2024": 1}
    assert set(info["source_sha256"]) == {"pairs_2023.parquet", "pairs_2024.parquet"}
    cached = pl.read_parquet(ev.DK_CACHE)
    assert cached.columns == ["game_id", "player_id", "line", "season"] and cached.height == 2
    again = ev.prepare_dk(tmp_path / "missing_dir")  # cache hit: the source is not read
    assert (
        again["source_sha256"] == "reused cache" and again["cache_sha256"] == info["cache_sha256"]
    )


def test_prepare_dk_refuses_duplicate_lines_and_non_half_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ev, "DK_CACHE", tmp_path / "cache" / "dk.parquet")
    _pairs(tmp_path, 2024, [_pair_row("g24", 1, 10.5, season=2024)])
    _pairs(tmp_path, 2023, [_pair_row("g1", 1, 10.5), _pair_row("g1", 1, 11.5)])
    with pytest.raises(RuntimeError, match="more than one"):
        ev.prepare_dk(tmp_path, refresh=True)
    _pairs(tmp_path, 2023, [_pair_row("g23", 1, 10.0)])
    with pytest.raises(RuntimeError, match="non x.5"):
        ev.prepare_dk(tmp_path, refresh=True)
    _pairs(tmp_path, 2023, [_pair_row("g23", 1, 10.5)])
    _pairs(tmp_path, 2024, [_pair_row("g24", 1, 10.5, season=2025)])
    with pytest.raises(RuntimeError, match="season > 2024"):
        ev.prepare_dk(tmp_path, refresh=True)
    assert not ev.DK_CACHE.exists()  # nothing is cached from a refused build


# ------------------------------------------------------------------------------ assemble, rule, report


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a1_shift: float = 0.0) -> Any:
    rng = np.random.default_rng(7)
    ctx = _frame_ctx(n_train=5, n_test=2000)
    fm = ev.frame_meta(ctx)
    n = fm.height
    # A0 has all frame keys and 6 extra rows (a clean-up artefact), labels identical
    extra = pl.DataFrame(
        {
            "game_id": ["0029999999"] * 6,
            "player_id": np.arange(6) + 10_000,
            "season": [2024] * 6,
            "y": [3.0] * 6,
        }
    )
    a0_meta = pl.concat([fm.select("game_id", "player_id", "season", "y"), extra]).with_columns(
        pl.Series("minutes", rng.uniform(0, 10, n + 6)),
        pl.Series("starter_act", (rng.random(n + 6) > 0.5).astype(float)),
    )
    g0 = _grid(rng, n + 6)
    a0 = {"meta": a0_meta, "qi": {"mixture": g0.astype(np.int16)}}
    full = g0[:n].copy()  # arm grids cover the test rows only, in frame_meta order
    q1 = full + a1_shift
    tips = pl.DataFrame(
        {
            "game_id": fm["game_id"].unique().to_list(),
            "tip_et": [
                dt.datetime(2024, 1, 1, 18 + i % 4, 0) for i in range(fm["game_id"].n_unique())
            ],
        }
    )
    monkeypatch.setattr(ev, "build_game_tipoff", lambda **_k: tips)
    monkeypatch.setattr(ev, "N_BOOT", 100)
    monkeypatch.setattr(ev, "DK_CACHE", tmp_path / "dk.parquet")
    dk_rows = fm.filter(pl.col("player_id") % 2 == 0).select("game_id", "player_id", "season")
    dk_rows.with_columns(pl.lit(11.5).alias("line")).write_parquet(tmp_path / "dk.parquet")
    return ctx, a0, q1, full, fm


def test_assemble_and_score_aligns_on_the_key_intersection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, a0, q1, full, fm = _world(tmp_path, monkeypatch)
    res, keys = ev.assemble_and_score(ctx, a0, q1, full, q1, q1)
    n23 = int((fm["season"] == 2023).sum())
    assert res["seasons"]["2023"]["n"] == n23
    assert res["seasons"]["2024"]["n"] == fm.height - n23  # the 6 A0-only rows are not scored
    assert keys["acct"]["2024"]["a0_only"] == 6 and keys["acct"]["2024"]["frame_only"] == 0
    # identical A0 and A1 grids (same arrays) give a zero primary delta and the extra arms appear
    for s in ("2023", "2024"):
        r = res["seasons"][s]
        assert r["primary"]["point"] == pytest.approx(0.0, abs=1e-12)
        assert "A1_stored-A0" in r and "A1_fixsched-A0" in r and "A2-A0" in r
        assert r["tll_dk"]["n"] > 0
        assert (
            r["slices"]["tip>=19ET(proxy-safe)"]["n"] + r["slices"]["tip<19ET(proxy-exposed)"]["n"]
            == r["n"]
        )
    # a worse A1 gives a positive delta and the rule closes
    ctx, a0, q1, full, fm = _world(tmp_path, monkeypatch, a1_shift=4.0)
    res, _ = ev.assemble_and_score(ctx, a0, q1, full, q1, q1)
    assert res["seasons"]["2024"]["primary"]["point"] > 0.5
    assert ev.evaluate_rule(res, True, True)["verdict"] == "CLOSED"


def test_assemble_and_score_refuses_label_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, a0, q1, full, _ = _world(tmp_path, monkeypatch)
    y = a0["meta"]["y"].to_numpy().copy()
    y[3] += 1.0
    a0["meta"] = a0["meta"].with_columns(pl.Series("y", y))
    with pytest.raises(RuntimeError, match="labels differ"):
        ev.assemble_and_score(ctx, a0, q1, full, q1, q1)


def test_report_and_ledger_render_from_results_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, a0, q1, full, _ = _world(tmp_path, monkeypatch, a1_shift=-0.0)
    res, keys = ev.assemble_and_score(ctx, a0, q1, full, q1, q1)
    rule = ev.evaluate_rule(res, True, True)
    out = tmp_path / "out"
    out.mkdir()
    (out / "results.json").write_text(
        json.dumps(
            {**res, "rule": rule, "sha256": "ab" * 32, "key_accounting": keys["acct"]},
            default=ev._jd,
        )
    )
    md = ev.render_report(out)
    for needle in (
        "# XGB_PTS_VS_LT results",
        "## Secondary and descriptive contrasts",
        "## Slices (A1 - A0)",
        "## Calibration (integer support)",
        "| 2023 | A2-A0 |",
        "threshold log loss A1 - A0 at pts >= 15",
        "drop the top-share team",
    ):
        assert needle in md
    assert md.count("| minutes<5 |") == 2  # one row per season
    p = ev.write_ledger(out)
    body = p.read_text().splitlines()
    assert body[0].startswith("| id |") and p.name == "ledger_rows.md"
    assert sum(ln.startswith("| T###") for ln in body) == 2 + 2 + 3
    assert body[-1].count(rule["verdict"]) == 1 and "CLOSED" in body[-1]
    assert f"p_bh {res['bh']['2023']:.4f}" in body[2]


def test_numeric_formatting_helpers_and_json_default() -> None:
    assert ev._f(None) == "n/a"
    assert ev._f(0.12345) == "+0.1235" and ev._f(0.12345, 2, sign=False) == "0.12"
    assert ev._ci_s({"point": -0.01, "lo": -0.02, "hi": 0.0}) == "-0.0100 [-0.0200, +0.0000]"
    assert ev._jd(np.float32(1.5)) == 1.5
    assert ev._jd(np.arange(3)) == [0, 1, 2]
    assert ev._jd(Path("x")) == "x"


# ------------------------------------------------------------------------------ run refusals


def test_run_arms_refuses_without_a_passing_gate_for_this_sha(tmp_path: Path) -> None:
    out = tmp_path
    doc = tmp_path / "D.md"
    doc.write_text("x")
    for gate in (
        {"draft": True, "gate_ok": True, "sha256": "s"},
        {"draft": False, "gate_ok": False, "sha256": "s"},
        {"draft": False, "gate_ok": True, "sha256": "other"},
    ):
        (out / "gate.json").write_text(json.dumps(gate))
        with pytest.raises(RuntimeError, match="no passing non-draft gate"):
            ev.run_arms(doc, "s", tmp_path, out)


def test_run_arms_refuses_when_stored_inputs_drift_from_the_document_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "gate.json").write_text(
        json.dumps({"draft": False, "gate_ok": True, "sha256": "s"})
    )
    doc = tmp_path / "D.md"
    doc.write_text(f"- input components_pts.npz: {'a' * 64}\n")
    monkeypatch.setattr(
        ev, "frozen_inputs", lambda: {"components_pts.npz": "b" * 64, "x": "MISSING"}
    )
    with pytest.raises(RuntimeError, match="stored inputs differ"):
        ev.run_arms(doc, "s", tmp_path, tmp_path)


def test_refit_arm_refuses_leaky_columns_and_resumes_from_checkpoints(tmp_path: Path) -> None:
    calls: list[int] = []

    class Rs:
        def stat_pred(
            self, blk: dict[str, Any], arm: str, stat: str, params: Any, rounds: Any
        ) -> dict[str, Any]:
            calls.append(blk["fold"])
            return {"q": np.full((2, 199), float(blk["fold"]))}

    def ctx(cols: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            base_cfg={"params": {"eta": 0.1}},
            njobs=1,
            spec={"v1_features": {"pts": ["a"]}},
            base_cols={"pts": cols},
            arms={"ref_fixed": {"pts": ["b"]}},
            blocks=[{"fold": 1, "ok": True}, {"fold": 2, "ok": True}, {"fold": 3, "ok": False}],
            B={"rounds": 5},
            ci={"a": 0},
        )

    with pytest.raises(RuntimeError, match="leaky ridge_"):
        ev.refit_arm(None, Rs(), ctx(["ridge_f3b8321a"]), "A1", tmp_path)
    assert calls == []
    done = ev.refit_arm(None, Rs(), ctx(["c"]), "A1", tmp_path)
    assert calls == [1, 2] and set(done) == {"1", "2"}  # the not-ok block is skipped
    assert np.load(tmp_path / "A1" / "pts" / "2.npz")["q"].dtype == np.float32
    # rerun resumes: nothing is refitted when every checkpoint exists
    assert ev.refit_arm(None, Rs(), ctx(["c"]), "A1", tmp_path) == {}
    assert calls == [1, 2]
    # `only` selects blocks by index into the ok list
    (tmp_path / "A1" / "pts" / "1.npz").unlink()
    assert set(ev.refit_arm(None, Rs(), ctx(["c"]), "A1", tmp_path, only=[0])) == {"1"}
