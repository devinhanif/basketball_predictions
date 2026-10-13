# mypy: disable-error-code="no-untyped-def,arg-type,index,assignment,operator,no-any-return,call-overload,attr-defined"
# ruff: noqa: E501
"""The harness on a SYNTHETIC world (real table shapes, tiny): frames, every run-time gate, the planted-future gate that can fail, the lock, the data stamp,
the refusals of the real entrypoint and the CPU smoke. No real database is opened."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import pplay_minutes_valid as H
from research.eval import pplay_minutes_valid_core as C
from research.eval import pplay_minutes_valid_rule as RULE
from research.eval import recent_role_eval as RR

TINY = ContextResidualConfig(n_estimators=12, min_child_samples=10, min_cal_rows=100, num_leaves=7)


@pytest.fixture(scope="module")
def world() -> dict:
    return H.synthetic_world()


@pytest.fixture(scope="module")
def built(world) -> H.Built:
    return H.build_all(world)


# ---------------------------------------------------------------- frames
def test_the_synthetic_frames_obey_the_rule(built: H.Built) -> None:
    p, m = built.p, built.m
    assert (
        p.height > 5000 and p["age"].null_count() == 0
    )  # the excluded player's NULL-age rows are gone, nothing else is NULL
    assert int((p["n_played_prior"] < 5).sum()) == 0  # Cell-P: n_played_prior >= 5
    assert set(np.unique(p["played"].to_numpy())) == {
        0.0,
        1.0,
    }  # DNP rows are in (label played = minutes > 0)
    assert (
        p.filter(pl.col("player_id") == C.EXCLUDED_PLAYER)
        .filter(pl.col("season") == 2019)["played"]
        .sum()
        >= 0
    )
    f21 = p.filter(pl.col("season") == 2021)
    assert all(
        f21[c].null_count() == f21.height for c in C.REPORT_FEATURE_COLS
    )  # rule 4: dropped on every 2021-22 row
    assert (f21["era_report_dropped"] == 1.0).all() and (
        p.filter(pl.col("season") != 2021)["era_report_dropped"] == 0.0
    ).all()
    assert (p.filter(pl.col("season") == 2020)["era_limited"] == 1.0).all() and (
        p.filter(pl.col("season") >= 2022)["era_limited"] == 0.0
    ).all()
    assert p.select(["game_id", "player_id"]).is_duplicated().sum() == 0
    assert p["game_date"].is_sorted()
    assert (
        m["age"].null_count() == 0 and (m.filter(pl.col("season") == 2021)["has_report"] == 0).all()
    )
    assert (p["p0"] > 0).all() and (p["p0"] <= 0.99).all() and (p["mu0"] >= 0).all()


def test_p0_is_the_served_function_on_the_union(world, built: H.Built) -> None:
    """P0 = build_minutes_features(use_game_context=False) -> predict_minutes defaults: shrunk trailing rate, no Platt, no context."""
    from nba.features.shrinkage import shrink_rate
    from nba.props.config import MinutesModelConfig

    cfg = MinutesModelConfig()
    r = built.p.filter(pl.col("season") == 2023).head(200)
    want = np.clip(
        shrink_rate(
            r["play_rate_prior"].fill_null(0.0).to_numpy(),
            r["games_played_prior"].fill_null(0).to_numpy(),
            cfg.default_p_play,
            cfg.k_play,
        ),
        0.02,
        0.99,
    )
    assert np.allclose(r["p0"].to_numpy(), want, atol=1e-12)


def test_arm_columns_are_the_frozen_r_columns_plus_four_era_channels() -> None:
    a = H.arm_sets()
    assert (
        a["R19"] == [*RR.arm_columns()["R"], *C.ERA_COLS]
        and a["Cp19"] == [*RR.arm_columns()["Cp"], *C.ERA_COLS]
        and a["C19"] == [*RR.arm_columns()["base"], *C.ERA_COLS]
    )
    assert C.ERA_COLS == ("era_bubble", "era_nofans", "era_limited", "era_report_dropped")
    assert H.m_inputs()[-4:] == list(C.ERA_COLS)
    assert all(c in C.NULL_CAUSES for c in C.REPORT_FEATURE_COLS) and all(
        c in C.NULL_CAUSES for c in C.M2_REPORT_COLS if c != "has_report"
    )


def test_lightgbm_parameters_are_recent_roles_with_threads_fixed_at_four() -> None:
    from nba.props.context_residual import _params

    cfg = PC_cfg()
    p = _params(cfg, "binary")
    assert (
        p["n_jobs"] == C.LGB_THREADS == 4
        and p["random_state"] == 0
        and p["n_estimators"] == 200
        and p["num_leaves"] == 15
    )


def PC_cfg() -> ContextResidualConfig:
    from research.eval import pplay_context_eval as PC

    return PC._load_cfg()


# ---------------------------------------------------------------- OUT sets: 60 min, and 180 min on 2021-22
def test_out_sets_use_tip_minus_180_on_2021_22_and_tip_minus_60_elsewhere(world) -> None:
    g = world["games_full"]
    g21 = g.filter(pl.col("season") == 2021)["game_id"][0]
    g22 = g.filter(pl.col("season") == 2022)["game_id"][0]
    tip = dt.datetime(2021, 1, 1, 19, 0)
    rows = pl.DataFrame(
        {
            "game_id": [g21, g22],
            "player_id": [555, 555],
            "status": ["out", "out"],
            "as_of": [tip - dt.timedelta(minutes=120)] * 2,  # after tip-180, before tip-60
            "game_date": [dt.date(2021, 1, 1)] * 2,
            "tip_et": [tip, tip],
        }
    ).with_columns(
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("tip_et").cast(pl.Datetime("us")),
        pl.col("game_date").cast(pl.Date),
    )
    w = dict(world)
    w["snaps"] = rows
    f = H.flagged_union(w)
    assert 555 in f.get(g22, set()) and 555 not in f.get(
        g21, set()
    )  # usable under 60 min, not under 180
    rows2 = rows.with_columns(
        pl.col("as_of") - pl.duration(minutes=90)
    )  # 210 min before tip: usable under both
    w["snaps"] = rows2
    f2 = H.flagged_union(w)
    assert 555 in f2[g22] and 555 in f2[g21]


# ---------------------------------------------------------------- G1
def test_planted_future_passes_on_the_clean_builder_and_fails_on_a_leaky_one(
    world, built: H.Built, monkeypatch
) -> None:
    g1 = H.planted_future_gate(world, built)
    assert (
        g1["ok"]
        and g1["future"]["p_inputs"]["identical"]
        and g1["future"]["m2_inputs"]["identical"]
        and g1["future"]["p0c_maps_identical"]
    )
    assert not g1["control"]["p_inputs"][
        "identical"
    ]  # non-inert: the control edit moves values at/before the cutoff
    orig = H.build_p_frame

    def leaky(
        w, lw
    ):  # the target game's own minutes enter an input: exactly what G1 exists to catch
        fr = orig(w, lw)
        own = lw["pgs"].select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            pl.col("minutes").fill_null(0.0).alias("_own"),
        )
        return (
            fr.join(own, on=["game_id", "player_id"], how="left")
            .with_columns(pl.col("_own").alias("wl_min_last"))
            .drop("_own")
        )

    monkeypatch.setattr(H, "build_p_frame", leaky)
    bad = H.build_all(world)
    g1b = H.planted_future_gate(world, bad)
    assert not g1b["ok"] and "wl_min_last" in g1b["future"]["p_inputs"]["differing"]


# ---------------------------------------------------------------- run-time gates
def _slate_from(world, noise: float = 0.0):
    p0n = H.p0_table(H.nba_only_world(world))

    def fn(day: dt.date, slate: pl.DataFrame) -> pl.DataFrame:
        j = p0n.filter(pl.col("game_id").is_in(slate["game_id"].to_list()))
        return j.select(
            "game_id",
            "player_id",
            (pl.col("p0") + noise).alias("p_play_raw"),
            pl.col("mu0").alias("proj_minutes"),
        )

    return fn


def test_parity_gate_passes_on_exact_agreement_and_fails_on_a_1e_minus_9_difference(
    world, built: H.Built
) -> None:
    ok = H.parity_gate(world, built, _slate_from(world), n_dates=4, season=2024)
    assert (
        ok["ok"]
        and ok["value"]["max_abs_dp"] <= 1e-12
        and all(n > 0 for n in ok["value"]["n_shared"])
    )
    assert (
        ok["value"]["data_scope_effect"]["share_changed"] > 0
    )  # union vs nba-only P0 differ on 2023-24 rows (declared effect)
    bad = H.parity_gate(world, built, _slate_from(world, noise=1e-9), n_dates=4, season=2024)
    assert not bad["ok"]
    empty = H.parity_gate(
        world,
        built,
        lambda d, s: pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "p_play_raw": pl.Float64,
                "proj_minutes": pl.Float64,
            }
        ),
        n_dates=3,
        season=2024,
    )
    assert not empty["ok"]  # nothing shared is a failure, not a pass


def test_era_gate_and_holdout_gate(built: H.Built) -> None:
    tests = H.era_and_report_gate(built.p, built.m)
    assert all(t["ok"] for t in tests)
    leaked = built.p.with_columns(
        pl.when(pl.col("season") == 2021).then(1.0).otherwise(pl.col("q60")).alias("q60")
    )
    assert not all(t["ok"] for t in H.era_and_report_gate(leaked, built.m))
    wrong = built.p.with_columns(
        pl.when(pl.col("season") == 2022)
        .then(1.0)
        .otherwise(pl.col("era_report_dropped"))
        .alias("era_report_dropped")
    )
    assert not all(t["ok"] for t in H.era_and_report_gate(wrong, built.m))
    nonzero = built.p.with_columns(
        pl.when(pl.col("season") == 2023)
        .then(1.0)
        .otherwise(pl.col("era_bubble"))
        .alias("era_bubble")
    )
    assert not all(t["ok"] for t in H.era_and_report_gate(nonzero, built.m))
    assert H.holdout_gate({"p": built.p})["ok"]
    assert not H.holdout_gate({"p": built.p.with_columns(pl.lit(2025).alias("season"))})["ok"]


def test_run_gate_end_to_end_on_the_synthetic_world(world, built: H.Built) -> None:
    g = H.run_gate(
        world,
        built,
        slate_fn=_slate_from(world),
        repro=False,
        flagged_parity=False,
        min_rows=100,
        min_games=10,
    )
    assert g["gate_ok"], [t for t in g["tests"] if not t["ok"]]
    assert g["detail"]["g1"]["ok"] and g["detail"]["g2_p"]["ok"] and g["detail"]["g2_m2"]["ok"]
    strict = H.run_gate(
        world, built, slate_fn=_slate_from(world), repro=False, flagged_parity=False, planted=False
    )
    assert not strict[
        "gate_ok"
    ]  # the real thresholds (25,000 rows, 1,000 games) are not met by a tiny world


def test_a_null_age_beyond_the_excluded_player_stops_the_build(world, monkeypatch) -> None:
    w = dict(world)
    st = world["static"]
    victim = [p for p in st["player_id"].to_list() if p != C.EXCLUDED_PLAYER][10]
    w["static"] = st.with_columns(
        pl.when(pl.col("player_id") == victim)
        .then(None)
        .otherwise(pl.col("birth_date"))
        .alias("birth_date")
    )
    with pytest.raises(RuntimeError, match="NULL age beyond player"):
        H.build_all(w)


# ---------------------------------------------------------------- arms and secondaries
def test_arms_g5_and_minutes_run_and_resume_to_identical_numbers(
    built: H.Built, tmp_path: Path
) -> None:
    a, _ = H.run_arms(built, TINY, ckpt=tmp_path / "arms", min_train=300, n_estimators=12)
    b, _ = H.run_arms(built, TINY, ckpt=tmp_path / "arms", min_train=300, n_estimators=12)
    assert all(np.array_equal(a[k], b[k]) for k in a)
    assert set(np.unique(a["season"])) == set(C.SCORED_SEASONS)
    for k in ("p_P0", "p_P0c", "p_R19", "p_C19", "p_Cp19"):
        assert k in a and np.isfinite(a[k]).all()
    g5 = H.run_g5(
        built, TINY, H.observed_d1(a), k=2, ckpt=tmp_path / "g5", min_train=300, n_estimators=12
    )
    g5b = H.run_g5(
        built, TINY, H.observed_d1(a), k=2, ckpt=tmp_path / "g5", min_train=300, n_estimators=12
    )
    assert g5["s_k"] == g5b["s_k"] and len(g5["s_k"]["2023"]) == 2
    z = H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2")
    z2 = H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2")
    assert all(np.array_equal(z[k], z2[k]) for k in z)
    rep = H.minutes_report(z)
    assert (
        set(rep) == {"2023", "2024"}
        and sum(v["n"] for v in rep.values()) == len(z["y"])
        and abs(sum(rep["2023"]["P0_pit_deciles"]) - rep["2023"]["n"]) == 0
    )
    sec = H.secondary_rows(a, None)
    assert {r["contrast"] for r in sec} == {"P0c-P0", "C19-P0c", "Cp19-C19", "R19-Cp19"}
    r22 = {"gid": a["gid"], "pid": a["pid"], "season": a["season"], "p_R": a["p_R19"]}
    assert any(r["contrast"] == "R19-R22" for r in H.secondary_rows(a, r22))


def test_analyze_gives_no_verdict_when_a_guard_fails(built: H.Built) -> None:
    res, _ = H.run_arms(built, TINY, min_train=300, n_estimators=12)
    bad_g5 = {"ok": False, "rows": []}
    out = H.analyze(res, bad_g5, {"gate_ok": True})
    assert out["decision"]["verdict"] == "NO VERDICT" and not out["guards"]["g5"]
    out2 = H.analyze(res, {"ok": True, "rows": []}, {"gate_ok": False})
    assert out2["decision"]["verdict"] == "NO VERDICT"


def test_the_cpu_smoke_runs_every_stage(tmp_path: Path) -> None:
    r = H.smoke(tmp_path)
    assert r["decision"]["verdict"] in {
        "PRODUCTION_BEATEN",
        "PRODUCTION_HOLDS",
        "MIXED",
        "NO VERDICT",
    }
    assert len(r["family"]) == 8 and (tmp_path / "smoke.json").exists()
    json.loads((tmp_path / "smoke.json").read_text())


# ---------------------------------------------------------------- lock, stamps, entrypoint refusals
def test_heavy_lock_waits_for_the_holder_and_removes_only_its_own(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    lock.mkdir()
    threading.Timer(0.2, lock.rmdir).start()
    t0 = time.time()
    with H.HeavyLock(lock, retry_s=0.05) as hl:
        assert hl.held and lock.exists() and time.time() - t0 >= 0.15
    assert not lock.exists()
    # a lock this process never acquired is never removed
    lock.mkdir()
    other = H.HeavyLock(lock, retry_s=0.01)
    other.__exit__(None, None, None)
    assert lock.exists()
    lock.rmdir()
    with pytest.raises(RuntimeError, match="boom"), H.HeavyLock(lock, retry_s=0.01):
        raise RuntimeError("boom")
    assert not lock.exists()  # released in the exceptional path too


def _manifests(tmp_path: Path, prod: str, hist: str, tag: str = "") -> tuple[Path, Path]:
    (tmp_path / tag).mkdir(exist_ok=True)
    a, b = tmp_path / tag / "latest.json", tmp_path / tag / "history_latest.json"
    a.write_text(json.dumps({"data_version": prod}))
    b.write_text(json.dumps({"data_version": hist}))
    return a, b


def test_data_versions_are_asserted_and_drift_must_be_named(tmp_path: Path) -> None:
    exp = H.EXPECTED_DATA_VERSIONS
    assert exp == {"history": "9e66461ad129", "production": "a55164376845"}
    a, b = _manifests(tmp_path, exp["production"], exp["history"])
    st = H.data_stamp(a, b, hash_files=False)
    assert st["data_version_history"] == "9e66461ad129" and st["drift_accepted"] == {}
    a2, b2 = _manifests(tmp_path, "ffffffffffff", exp["history"], "a2")
    with pytest.raises(RuntimeError, match="--accept-data-version"):
        H.data_stamp(a2, b2, hash_files=False)
    with pytest.raises(RuntimeError, match="--accept-data-version"):
        H.data_stamp(a2, b2, accept="000000000000", hash_files=False)
    assert H.data_stamp(a2, b2, accept="ffffffffffff", hash_files=False)["drift_accepted"] == {
        "production": "ffffffffffff"
    }
    a3, b3 = _manifests(tmp_path, exp["production"], "ffffffffffff", "a3")
    with pytest.raises(RuntimeError, match="history data_version"):
        H.data_stamp(
            a3, b3, accept="ffffffffffff", hash_files=False
        )  # history drift is never accepted
    f = tmp_path / "x.duckdb"
    f.write_bytes(b"abc")
    st2 = H.data_stamp(a, b, db=str(f), hdb=str(f))
    assert st2["sha256_nba_duckdb"] == RULE.file_sha256(f)


def test_real_entrypoints_refuse_before_touching_data(monkeypatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(H, "make_world", lambda *a, **k: touched.append("world"))
    monkeypatch.setattr(H, "data_stamp", lambda *a, **k: touched.append("stamp") or {})
    with pytest.raises(RuntimeError, match="64-hex"):
        H.run_real("abc", None)
    with pytest.raises(RuntimeError, match="not --sha"):
        H.run_real("0" * 64, None)
    sha = RULE.frozen_sha()

    def no_review(*a, **k):
        raise RuntimeError("hard stop: no loader review")

    monkeypatch.setattr(RULE, "require_review", no_review)
    with pytest.raises(RuntimeError, match="hard stop"):
        H.run_real(sha, None)
    with pytest.raises(RuntimeError, match="hard stop"):
        H.run_real(sha, None, gate_only=True)
    with pytest.raises(SystemExit):
        H.main(["run"])  # --sha is required
    with pytest.raises(RuntimeError, match="hard stop"):
        H.main(["report", "--sha", sha])
    assert touched == []  # no database was opened, no stamp taken


def test_a_gate_failure_stops_the_pipeline_before_any_arm(
    world, tmp_path: Path, monkeypatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(H, "run_arms", lambda *a, **k: called.append("arms"))
    with pytest.raises(RuntimeError, match="NO VERDICT"):
        H.pipeline(
            world,
            TINY,
            {},
            {},
            out_dir=tmp_path / "o",
            cache=tmp_path / "c",
            min_train=300,
            gate_kwargs={
                "parity": False,
                "repro": False,
                "flagged_parity": False,
                "planted": False,
            },
        )
    assert (
        called == [] and json.loads((tmp_path / "o" / "gate.json").read_text())["gate_ok"] is False
    )  # thresholds 25,000 rows not met


# ---------------------------------------------------------------- the real-data loaders, with the databases stubbed
def _write_dbs(world, tmp_path: Path, extra_holdout: bool = False) -> tuple[str, str]:
    import duckdb

    cur, his = str(tmp_path / "cur.duckdb"), str(tmp_path / "his.duckdb")
    for path, lo, hi in ((cur, 2022, 2025), (his, 2019, 2021)):
        con = duckdb.connect(path)
        g = world["games_full"].filter(pl.col("season").is_between(lo, hi))
        if extra_holdout and lo == 2022:
            h = (
                g.filter(pl.col("season") == 2024)
                .head(1)
                .with_columns(
                    pl.lit("0022500001").alias("game_id"),
                    pl.lit(2025, dtype=pl.Int64).alias("season"),
                )
            )
            g = pl.concat([g, h])
        pg = world["pgs_full"].filter(pl.col("game_id").is_in(g["game_id"].to_list()))
        for name, df in (
            ("games", g),
            ("player_game_stats", pg),
            ("players_static", world["static"]),
        ):
            con.register("_x", df)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM _x")
            con.unregister("_x")
        con.close()
    return cur, his


def test_make_world_reads_both_databases_read_only_with_the_season_filter(
    world, tmp_path: Path, monkeypatch
) -> None:
    cur, his = _write_dbs(world, tmp_path, extra_holdout=True)

    class StubHW:
        cfg = world["cfg"]
        elo_params = world["elo_params"]
        games_all = world["games_full"].select(
            "game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"
        )
        avail_all = pl.DataFrame()
        tips_all = pl.DataFrame()
        hist_ids = world["hist_ids"]

    monkeypatch.setattr(H.HW, "load_world", lambda db, hdb, **k: StubHW())
    monkeypatch.setattr(H.HW, "era_flag_frame", lambda w: world["flags"])
    monkeypatch.setattr(H.HW, "report_rows", lambda *a, **k: world["snaps"])
    monkeypatch.setattr(H.HW, "rows_with_real_tips", lambda rows, tips, ids: rows)
    seen: list[str] = []
    orig = H._full_table
    monkeypatch.setattr(H, "_full_table", lambda path, sql: (seen.append(sql), orig(path, sql))[1])
    w = H.make_world(cur, his)
    assert (
        int(w["games_full"]["season"].max()) == 2024
        and w["games_full"].height == world["games_full"].height
    )  # the planted 2025-season row never loaded
    assert w["pgs_full"].height == world["pgs_full"].height
    assert all("season <= 2024" in q for q in seen if "FROM games" in q or "player_game_stats" in q)
    assert set(w["static"]["player_id"].to_list()) == set(world["static"]["player_id"].to_list())
    # the databases were only read
    import duckdb

    with pytest.raises(duckdb.Error):
        duckdb.connect(cur, read_only=True).execute("INSERT INTO games SELECT * FROM games LIMIT 1")


def test_coalesce_static_takes_the_first_non_null_birth_date() -> None:
    a = pl.DataFrame(
        {"player_id": [1, 2], "birth_date": [None, dt.date(1990, 1, 1)], "position": ["G", "F"]},
        schema_overrides={"birth_date": pl.Date},
    )
    b = pl.DataFrame(
        {
            "player_id": [1, 3],
            "birth_date": [dt.date(1995, 5, 5), dt.date(1999, 9, 9)],
            "position": ["G", "C"],
        }
    )
    out = H._coalesce_static([a, b]).sort("player_id")
    assert out["birth_date"].to_list() == [
        dt.date(1995, 5, 5),
        dt.date(1990, 1, 1),
        dt.date(1999, 9, 9),
    ]


def _avail_db(path: Path) -> Path:
    """A throw-away nba.duckdb with games of 2023, 2024 and the frozen holdout season and report rows for each (one 2024 game with a report dated after the bound)."""
    import duckdb

    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games(game_id VARCHAR, game_date DATE, season INTEGER, home_team BIGINT, away_team BIGINT, home_pts INTEGER, away_pts INTEGER)"
    )
    con.execute(
        "CREATE TABLE player_availability(game_id VARCHAR, player_id BIGINT, status VARCHAR, as_of TIMESTAMP, source VARCHAR)"
    )
    holdout = C.MAX_SEASON + 1
    for gid, d, season in (
        ("g23", "2024-01-10", 2023),
        ("g24", "2025-01-10", 2024),
        ("g25", f"{holdout}-12-10", holdout),
        ("g26", f"{holdout + 1}-01-10", holdout + 1),
    ):
        con.execute("INSERT INTO games VALUES (?, ?, ?, 1, 2, 100, 90)", [gid, d, season])
        con.execute(
            "INSERT INTO player_availability VALUES (?, 7, 'Out', ?, 'nba_official_report')",
            [gid, f"{d} 12:00:00"],
        )
    con.execute(
        "INSERT INTO player_availability VALUES ('g24', 8, 'Out', ?, 'nba_official_report')",
        [f"{holdout}-08-01 12:00:00"],
    )
    con.execute("INSERT INTO player_availability VALUES (NULL, 9, 'Out', '2024-01-01', 'x')")
    con.close()
    return path


def test_f4_the_availability_query_never_loads_a_holdout_row(
    world, tmp_path: Path, monkeypatch
) -> None:
    import inspect

    import duckdb

    import nba.props.context_residual as PCR

    db = _avail_db(tmp_path / "nba.duckdb")
    games, avail = H.load_flag_inputs(str(db))
    assert sorted(games["game_id"].to_list()) == ["g23", "g24"]
    assert sorted(zip(avail["game_id"].to_list(), avail["player_id"].to_list(), strict=True)) == [
        ("g23", 7),
        ("g24", 7),
    ]  # g24's report dated after the bound, g25, g26 and the NULL-game row are never loaded
    assert "load_inputs" not in inspect.getsource(H.flagged_parity_gate)
    seen: dict = {}
    mine = H.flagged_union(world)

    def fake(av, games_, cfg):
        seen["avail"], seen["games"] = av, games_
        return dict(mine), {}

    monkeypatch.setattr(PCR, "flagged_from_availability", fake)
    modes: list = []
    real_connect = duckdb.connect
    monkeypatch.setattr(
        duckdb,
        "connect",
        lambda *a, **k: modes.append(k.get("read_only")) or real_connect(*a, **k),
    )
    H.flagged_parity_gate(world, str(db))
    assert modes == [True, True] or modes == [True]
    assert set(seen["avail"]["game_id"].to_list()) <= {"g23", "g24"}
    assert int(seen["games"]["season"].max()) <= C.MAX_SEASON


def test_flagged_parity_and_repro_gates(world, tmp_path: Path, monkeypatch) -> None:
    import nba.props.context_residual as PCR

    mine = H.flagged_union(world)
    games22 = world["games_full"].filter(pl.col("season") >= 2022)
    monkeypatch.setattr(H, "load_flag_inputs", lambda db: (games22, None))
    monkeypatch.setattr(
        PCR, "flagged_from_availability", lambda avail, games, cfg: (dict(mine), {})
    )
    assert H.flagged_parity_gate(world)["ok"]
    wrong = {g: s | {1} for g, s in mine.items()}
    monkeypatch.setattr(PCR, "flagged_from_availability", lambda avail, games, cfg: (wrong, {}))
    assert not H.flagged_parity_gate(world)["ok"]
    monkeypatch.setattr(H, "CACHE", tmp_path / "cache")

    # R22 reproduction: exact match passes; a 1e-8 difference, a missing row or a different n fails
    n23, n24 = C.REPRO_N[2023], C.REPRO_N[2024]
    season = np.r_[np.full(n23, 2023), np.full(n24, 2024)]
    gid = np.array([f"g{i}" for i in range(len(season))])
    pid = np.arange(len(season)).astype(np.int64)
    p = np.random.default_rng(0).uniform(0.2, 0.9, len(season))
    stored = tmp_path / "scores.npz"
    np.savez(stored, gid=gid, pid=pid, p_R=p)
    monkeypatch.setattr(H, "STORED_RR_SCORES", stored)
    monkeypatch.setattr(H.RR, "build_frame", lambda: (None, None, None))

    def fake_walk(p_mine):
        return lambda fr, arms, cfg, ckpt=None: (
            {"gid": gid, "pid": pid, "season": season, "p_R": p_mine},
            {},
        )

    monkeypatch.setattr(H.RR, "walk", fake_walk(p.copy()))
    assert H.repro_gate()[0]["ok"]
    monkeypatch.setattr(H.RR, "walk", fake_walk(p + 1e-8))
    assert not H.repro_gate()[0]["ok"]
    monkeypatch.setattr(
        H.RR,
        "walk",
        lambda fr, arms, cfg, ckpt=None: (
            {"gid": gid[:-1], "pid": pid[:-1], "season": season[:-1], "p_R": p[:-1]},
            {},
        ),
    )
    assert not H.repro_gate()[0]["ok"]


def test_props_descriptive_assembles_unconditional_crps_on_stored_grids(
    built: H.Built, monkeypatch
) -> None:
    res, _ = H.run_arms(built, TINY, min_train=300, n_estimators=12)
    played = res["y"] > 0.5
    rng = np.random.default_rng(0)
    n = int(played.sum())
    a0 = {
        "qi": np.sort(rng.integers(0, 30, (n, 9)), axis=1).astype(float),
        "y": rng.integers(0, 30, n).astype(float),
        "gid": res["gid"][played],
        "pid": res["pid"][played],
        "season": res["season"][played],
    }
    import research.eval.workload_eval as WE

    monkeypatch.setattr(WE, "load_pic", lambda: {})
    monkeypatch.setattr(WE, "a0_arm", lambda stat, pic: a0)
    out = H.props_descriptive(res, built.p)
    assert set(out) == {f"{s}_{y}" for s in H.STATS for y in C.VERDICT_SEASONS}
    rec = out["pts_2023"]
    assert (
        rec["n_rows"] >= rec["n_dnp_proxy"] >= 0
        and {"R19-P0c", "R19-P0", "P0c-P0"} <= set(rec)
        and rec["R19-P0"]["n_games"] > 0
    )


# ---------------------------------------------------------------- F1: the minutes-head report list is derived, not hand-listed
def test_f1_report_columns_are_derived_from_the_producing_code_and_cover_vac_min_and_vac_pts(
    built: H.Built,
) -> None:
    assert {"vac_min", "vac_pts", "n_out_rot", "vac_reb", "out_max_min40"} <= set(
        built.m_report_cols
    )
    assert "has_report" not in built.m_report_cols and "game_id" not in built.m_report_cols
    m21 = built.m.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)
    assert all(m21[c].null_count() == m21.height for c in built.m_report_cols)


def test_f1_a_2021_22_vac_min_value_fails_the_era_gate_even_with_the_old_hand_list(
    world, built: H.Built, monkeypatch
) -> None:
    old_list = ("n_out_rot", "vac_reb", "vac_ast", "vac_fg3m", "n_out_starters", "out_max_min40")
    leaked = built.m.with_columns(
        pl.when(pl.col("season") == C.REPORT_DROPPED_SEASON)
        .then(3.0)
        .otherwise(pl.col("vac_min"))
        .alias("vac_min")
    )
    monkeypatch.setattr(C, "M2_REPORT_COLS", old_list)  # the pre-fix floor: vac_min is not in it
    pre_fix = H.era_and_report_gate(built.p, leaked)
    assert all(t["ok"] for t in pre_fix)  # the defect: a gate that passes for the wrong reason
    fixed = H.era_and_report_gate(built.p, leaked, built.m_report_cols)
    bad = [t for t in fixed if not t["ok"]]
    assert len(bad) == 1 and bad[0]["value"]["vac_min"] > 0
    # the derivation itself, with the old floor, still carries vac_min and vac_pts into the build
    sc = H.scratch(world)
    try:
        m, cols = H.build_m_frame_ex(world, H.legacy_world(world, sc))
    finally:
        sc.close()
    assert "vac_min" in cols and "vac_pts" in cols
    assert m.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)["vac_min"].null_count() > 0


def test_f1_a_new_report_dependent_column_is_found_without_being_listed(monkeypatch) -> None:
    w = H.synthetic_world()
    real = H.MV.build_frame

    def with_sneaky(lw):
        f = real(lw)
        return f.with_columns(pl.lit(float(len(lw["flagged"]))).alias("zz_sneaky_report_col"))

    monkeypatch.setattr(H.MV, "build_frame", with_sneaky)
    sc = H.scratch(w)
    try:
        lw = H.legacy_world(w, sc)
        m, cols = H.build_m_frame_ex(w, lw)
    finally:
        sc.close()
    assert "zz_sneaky_report_col" in cols
    assert (
        m.filter(pl.col("season") == C.REPORT_DROPPED_SEASON)["zz_sneaky_report_col"]
        .is_null()
        .all()
    )


# ---------------------------------------------------------------- F2: opp_games_prior is exactly the NULL cause of opp_r20_*
def test_f2_opp_r20_is_null_exactly_when_the_opponent_has_fewer_than_five_earlier_games() -> None:
    from nba.props.context_residual import team_game_context

    rng = np.random.default_rng(0)
    rows, box = [], []
    d0 = dt.date(2019, 10, 22)
    gid = 0
    for day in range(40):
        teams = rng.permutation([1, 2, 3, 4, 5, 6])[: 2 * int(rng.integers(1, 4))]
        for a, b in zip(teams[::2], teams[1::2], strict=True):
            gid += 1
            g = f"{gid:010d}"
            rows.append(
                {
                    "game_id": g,
                    "game_date": d0 + dt.timedelta(days=day),
                    "season": 2019,
                    "home_team": int(a),
                    "away_team": int(b),
                    "home_pts": 100 + int(rng.integers(0, 20)),
                    "away_pts": 100 + int(rng.integers(0, 20)),
                }
            )
            for t in (a, b):
                box.append(
                    {"game_id": g, "team_id": int(t), "pts": 100, "reb": 40, "ast": 20, "fg3m": 10}
                )
    games = pl.DataFrame(rows).with_columns(pl.col("game_date").cast(pl.Date))
    pgs = pl.DataFrame(box)
    elo = games.select("game_id", pl.lit(1.0).alias("exp_margin_home"))
    tg = team_game_context(games, pgs, elo)
    frame = tg.select("game_id", "opp_id", "opp_r20_pts_for", "opp_r20_pts_against")
    out = H.opp_games_prior(frame, games)
    assert out.height == frame.height and out["opp_games_prior"].null_count() == 0
    for c in ("opp_r20_pts_for", "opp_r20_pts_against"):
        assert (out[c].is_null() == (out["opp_games_prior"] < 5)).all()
        assert C.cause_mismatch(out, c, C.EXACT_NULL_CAUSE[c]) == {
            "null_without_cause": 0,
            "cause_without_null": 0,
            "cause_unavailable": 0,
        }
    assert int(out["opp_r20_pts_for"].is_null().sum()) > 0  # the case exists in the fixture


# ---------------------------------------------------------------- R4, R2
def test_r4_the_age_check_counts_nan_as_well_as_null(built: H.Built) -> None:
    assert H.age_check(built)["ok"]
    nan_p = built.p.with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(float("nan"))
        .otherwise(pl.col("age"))
        .alias("age")
    )
    chk = H.age_check(H.Built(nan_p, built.m, built.lw_flagged, built.m_report_cols))
    assert not chk["ok"] and chk["value"]["p"] == 1
    null_m = built.m.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(None).otherwise(pl.col("age")).alias("age")
    )
    assert not H.age_check(H.Built(built.p, null_m, built.lw_flagged))["ok"]


# ---------------------------------------------------------------- F6 on the harness checkpoints (arms, G5, M2, R22)
CTX = {"rule_sha256": "r" * 64, "data_version_production": "p1", "code_sha": "c1"}


def test_f6_arms_g5_and_m2_checkpoints_refuse_a_different_manifest(
    built: H.Built, tmp_path: Path
) -> None:
    kw = dict(min_train=300, n_estimators=12)
    a, _ = H.run_arms(built, TINY, ckpt=tmp_path / "arms", manifest_ctx=CTX, **kw)
    b, _ = H.run_arms(built, TINY, ckpt=tmp_path / "arms", manifest_ctx=CTX, **kw)
    assert all(np.array_equal(a[k], b[k]) for k in a)
    other = {
        **CTX,
        "data_version_production": "p2",
    }  # e.g. a resumed run after --accept-data-version
    with pytest.raises(RuntimeError, match="different manifest"):
        H.run_arms(built, TINY, ckpt=tmp_path / "arms", manifest_ctx=other, **kw)

    obs = H.observed_d1(a)
    g = H.run_g5(built, TINY, obs, k=2, ckpt=tmp_path / "g5", manifest_ctx=CTX, **kw)
    g2 = H.run_g5(built, TINY, obs, k=2, ckpt=tmp_path / "g5", manifest_ctx=CTX, **kw)
    assert g["s_k"] == g2["s_k"]
    with pytest.raises(RuntimeError, match="different manifest"):
        H.run_g5(built, TINY, obs, k=2, ckpt=tmp_path / "g5", manifest_ctx=other, **kw)
    rec = tmp_path / "g5" / "perm_001.json"
    d = json.loads(rec.read_text())
    rec.write_text(
        json.dumps({**d, "_manifest": "0" * 64})
    )  # a record from another manifest inside a valid directory
    with pytest.raises(RuntimeError, match="manifest/seed"):
        H.run_g5(built, TINY, obs, k=2, ckpt=tmp_path / "g5", manifest_ctx=CTX, **kw)
    rec.write_text(
        json.dumps({k: v for k, v in d.items() if not k.startswith("_")})
    )  # unkeyed record
    with pytest.raises(RuntimeError, match="manifest/seed"):
        H.run_g5(built, TINY, obs, k=2, ckpt=tmp_path / "g5", manifest_ctx=CTX, **kw)

    z = H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=CTX)
    z2 = H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=CTX)
    assert all(np.array_equal(z[k], z2[k]) for k in z)
    with pytest.raises(RuntimeError, match="different manifest"):
        H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=other)
    blk = sorted((tmp_path / "m2").glob("m2_block_*.npz"))[0]
    arrs = dict(np.load(blk, allow_pickle=False))
    np.savez_compressed(blk, **{**arrs, "_train_hash": np.array("0" * 16)})
    with pytest.raises(RuntimeError, match="training-label hash"):
        H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=CTX)
    np.savez_compressed(blk, **{k: v for k, v in arrs.items() if k != "_train_hash"})
    with pytest.raises(RuntimeError, match="training-label hash"):
        H.m2_walk(built, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=CTX)
    # M2 labels changed under the same directory
    drifted = H.Built(
        built.p,
        built.m.with_columns((pl.col("minutes") + 1.0).alias("minutes")),
        built.lw_flagged,
        built.m_report_cols,
    )
    with pytest.raises(RuntimeError, match="different manifest"):
        H.m2_walk(drifted, TINY, min_train=300, ckpt=tmp_path / "m2", manifest_ctx=CTX)


def test_f6_the_r22_checkpoint_directory_is_keyed_and_unpickled_never(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(H, "CACHE", tmp_path / "cache")
    n23, n24 = C.REPRO_N[2023], C.REPRO_N[2024]
    season = np.r_[np.full(n23, 2023), np.full(n24, 2024)]
    gid = np.array([f"g{i}" for i in range(len(season))])
    pid = np.arange(len(season)).astype(np.int64)
    p = np.random.default_rng(0).uniform(0.2, 0.9, len(season))
    stored = tmp_path / "scores.npz"
    np.savez(stored, gid=gid, pid=pid, p_R=p)
    monkeypatch.setattr(H, "STORED_RR_SCORES", stored)
    monkeypatch.setattr(H.RR, "build_frame", lambda: (None, None, None))
    monkeypatch.setattr(
        H.RR,
        "walk",
        lambda fr, arms, cfg, ckpt=None: (
            {"gid": gid, "pid": pid, "season": season, "p_R": p.copy()},
            {},
        ),
    )
    assert H.repro_gate(manifest_ctx=CTX)[0]["ok"]
    assert H.repro_gate(manifest_ctx=CTX)[0]["ok"]
    with pytest.raises(RuntimeError, match="different manifest"):
        H.repro_gate(manifest_ctx={**CTX, "code_sha": "c2"})
    np.savez(
        tmp_path / "cache" / "r22_blocks" / "block_x.npz",
        gid=np.array([{"a": 1}], dtype=object),
    )
    with pytest.raises(ValueError, match="allow_pickle"):
        H.repro_gate(manifest_ctx=CTX)
    # the stored reference is loaded without pickle too
    obj = tmp_path / "obj.npz"
    np.savez(obj, gid=np.array([{"a": 1}], dtype=object), pid=pid[:1], p_R=p[:1])
    monkeypatch.setattr(H, "STORED_RR_SCORES", obj)
    (tmp_path / "cache" / "r22_blocks" / "block_x.npz").unlink()
    with pytest.raises(ValueError, match="allow_pickle"):
        H.repro_gate(manifest_ctx=CTX)


def test_f6_the_pipeline_keys_every_checkpoint_by_rule_data_and_code(
    world, tmp_path: Path, monkeypatch
) -> None:
    stamp = {"data_version_production": "p1", "data_version_history": "h1"}
    info = {"rule_sha256": "r" * 64, "code_sha": "c1", "review_path": "x"}
    ctx = H.cache_ctx(stamp, info)
    assert ctx == {
        "rule_sha256": "r" * 64,
        "data_version_production": "p1",
        "data_version_history": "h1",
        "code_sha": "c1",
    }
    seen: list = []
    real = C.guard_cache
    monkeypatch.setattr(C, "guard_cache", lambda d, m: seen.append((d.name, m)) or real(d, m))
    monkeypatch.setattr(
        H,
        "run_gate",
        lambda *a, **k: seen.append(("gate", k["manifest_ctx"])) or {"gate_ok": False, "tests": []},
    )
    with pytest.raises(RuntimeError, match="NO VERDICT"):
        H.pipeline(
            world, TINY, stamp, info, out_dir=tmp_path / "o", cache=tmp_path / "c", min_train=300
        )
    assert seen[0] == ("gate", ctx)


# ---------------------------------------------------------------- R1, R2: the review and the data stamp are re-taken after heavy.lock
def test_r1_a_review_that_stops_being_valid_while_waiting_for_the_lock_stops_the_run(
    monkeypatch, tmp_path: Path
) -> None:
    sha = RULE.frozen_sha()
    events: list[str] = []
    state = {"calls": 0}

    def review():
        state["calls"] += 1
        events.append(f"review{state['calls']}")
        if state["calls"] >= 2:
            raise RuntimeError("hard stop: a later BLOCKED review was committed during the wait")
        return {"verdict": "APPROVED"}

    class FakeLock:
        def __enter__(self):
            events.append("lock")
            return self

        def __exit__(self, *e):
            events.append("unlock")

    monkeypatch.setattr(RULE, "require_review", review)
    monkeypatch.setattr(RULE, "module_blobs", lambda *a, **k: {"x.py": "a" * 40})
    monkeypatch.setattr(H, "data_stamp", lambda **k: events.append("stamp") or {})
    monkeypatch.setattr(H, "HeavyLock", FakeLock)
    monkeypatch.setattr(H, "make_world", lambda *a, **k: events.append("world"))
    monkeypatch.setattr(H, "_preload_lazy_modules", lambda: events.append("preload"))
    with pytest.raises(RuntimeError, match="BLOCKED"):
        H.run_real(sha, None, gate_only=True)
    assert events == [
        "review1",
        "stamp",
        "lock",
        "preload",
        "review2",
        "unlock",
    ]  # nothing built, lock released

    # the happy path takes review and stamp after the lock and stamps the code blobs (R2)
    events.clear()
    state["calls"] = -10
    monkeypatch.setattr(H, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(H, "make_world", lambda *a, **k: {})
    monkeypatch.setattr(H, "_cfg", lambda: None)
    monkeypatch.setattr(H, "build_all", lambda w: None)
    got: dict = {}
    monkeypatch.setattr(
        H,
        "run_gate",
        lambda w, b, **k: got.update(k) or {"gate_ok": True, "tests": [], "_r22": {}},
    )
    H.run_real(sha, None, gate_only=True)
    assert events == ["review-9", "stamp", "lock", "preload", "review-8", "stamp", "unlock"]
    out = json.loads((tmp_path / "out" / "gate.json").read_text())
    assert out["code"]["module_blobs"] == {"x.py": "a" * 40} and len(out["code"]["code_sha"]) == 64
    assert got["manifest_ctx"]["code_sha"] == out["code"]["code_sha"]
    assert got["manifest_ctx"]["rule_sha256"] == sha
