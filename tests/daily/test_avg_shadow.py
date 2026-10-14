# ruff: noqa: E501
"""AVG_SHADOW_LIVE_2 shadow (docs/prereg/AVG_SHADOW_LIVE_2.md): grid arithmetic pinned to the offline originals, live exporter pinned to the
T478 exporter on fixture data, tip refusal, weights bundles and staleness, inference in a child process, heartbeat statuses, serving rule,
settle, and that the primary rows (and so the replay hash) do not move."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from nba.daily.avg_shadow import bundle as B
from nba.daily.avg_shadow import grids, sequences, store
from nba.daily.avg_shadow.run import run_avg_shadow
from nba.daily.avg_shadow.settle import settle_avg_shadow
from nba.daily.canonical_dump import canonical_csv, canonical_frames
from nba.daily.full_shadow import store as full_store
from nba.daily.lt_live import integer_crps
from nba.daily.pipeline import run_daily
from nba.daily.pplay_shadow.features import SlateGame
from nba.ingest.cache import insert_rows
from tests.daily.conftest import RUN_DATE, STAT_COLS, T1, T2, TIPOFF, _box, slate_games

GID = "0022600001"
MADE = TIPOFF - timedelta(minutes=70)
STATS = ("pts", "reb", "ast", "fg3m")
NAIVE_UTC_NOW = datetime(2026, 10, 27, 12, 0)


def _slate(gid: str = GID) -> list[SlateGame]:
    return [SlateGame(gid, T1, T2, TIPOFF)]


def _child(args: list[str]) -> str:
    cp = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stderr[-800:]
    return cp.stdout


def make_bundle(
    root: Path,
    name: str,
    month_block: str = "2000-01",
    fit_through: str = "1999-12-31",
    installed: datetime | None = NAIVE_UTC_NOW,
) -> Path:
    out = root / name
    args = [
        "-m",
        "nba.daily.avg_shadow.bundle",
        "fixture",
        str(out),
        "--month-block",
        month_block,
        "--fit-through",
        fit_through,
    ]
    if installed is not None:
        args += ["--installed-at", installed.isoformat()]
    _child(args)
    return out


def seed_inputs(
    con: duckdb.DuckDBPyConnection,
    made_at: datetime = MADE - timedelta(minutes=5),
    gid: str = GID,
    players: tuple[int, ...] = (1000, 1001, 1002, 1003, 1100, 1101),
) -> None:
    """Production rows (A0) and FULL shadow rows for the slate, as the earlier steps of the same pretip pass leave them."""
    full_store.ensure_tables(con)
    for pid in players:
        for stat in STATS:
            grid = [float(i) * (1 + 0.3 * STATS.index(stat)) for i in range(19)]
            model = "props_context_residual_lt" if stat == "pts" else "props_context_residual"
            con.execute(
                "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    "prod",
                    made_at,
                    gid,
                    TIPOFF,
                    model,
                    "v1",
                    stat,
                    pid,
                    json.dumps({"q_grid": grid}),
                ],
            )
            fgrid = [round(v + 0.5) for v in grid]
            con.execute(
                "INSERT INTO forward_full_shadow (run_id, made_at, game_id, tipoff, player_id, team_id, stat, model_version, q_grid, q_grid_prod) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    "prod",
                    made_at,
                    gid,
                    TIPOFF,
                    pid,
                    T1 if pid < 1100 else T2,
                    stat,
                    "full_v1",
                    json.dumps(fgrid),
                    json.dumps(grid),
                ],
            )


# --------------------------------------------------------------------------- grids pinned to the offline originals


def test_grids_equal_the_research_originals() -> None:
    from research.eval import ensemble_eval as E

    rng = np.random.default_rng(0)
    q = np.sort(rng.gamma(2.0, 3.0, size=(50, 19)), axis=1)
    assert np.array_equal(grids.ext19(q), E.ext19(q))
    a, b = (rng.integers(0, 40, size=(30, 199)) for _ in range(2))
    a, b = np.sort(a, axis=1), np.sort(b, axis=1)
    w = np.full(30, 0.5)
    assert np.array_equal(grids.mix_integer_quantiles(a, b, w), E.mix_integer_quantiles(a, b, w))
    assert np.array_equal(grids.int_pmf(a, 50), E.int_pmf(a, 50))


def test_q19_of_grid199_is_reproducible_and_monotone() -> None:
    rng = np.random.default_rng(1)
    g = np.sort(rng.integers(0, 35, size=(40, 199)), axis=1)
    q = grids.q19_of_grid199(g)
    assert q.shape == (40, 19) and (np.diff(q, axis=1) >= 0).all()
    again = grids.q19_of_grid199(g.copy())
    assert np.array_equal(q, again)
    # a constant pmf maps to a constant quantile
    assert (grids.q19_of_grid199(np.full((2, 199), 7)) == 7).all()


def test_gate_matches_the_research_softmax_gate() -> None:
    from research.stack.router import ContextEncoder, SoftmaxGate

    rng = np.random.default_rng(2)
    x = pd.DataFrame(rng.normal(size=(60, 5)), columns=list(grids.GATE_COLUMNS))
    x.iloc[3, 1] = np.nan
    enc = ContextEncoder().fit(x)
    gate = SoftmaxGate()
    gate.coef = rng.normal(size=(6, 2))
    want = gate.weights(enc.transform(x))
    got = grids.softmax_gate(x.to_numpy(), enc.med, enc.mu, enc.sd, gate.coef)
    assert np.allclose(got, want, atol=1e-12) and np.allclose(got.sum(axis=1), 1.0)


def test_build_arms_serving_route_and_mixture() -> None:
    rng = np.random.default_rng(3)
    a0 = np.sort(rng.gamma(2.0, 3.0, size=(20, 19)), axis=1)
    full = np.round(np.sort(rng.gamma(2.0, 3.0, size=(20, 19)), axis=1))
    seq = np.sort(rng.gamma(2.0, 3.0, size=(20, 19)), axis=1)
    w = np.tile([0.3, 0.7], (20, 1))
    arms = grids.build_arms(a0, full, seq, w, np.ones(20, dtype=bool))
    assert set(arms) == {"A0", "FULL", "SEQc", "AVG"}
    # AVG is the 50/50 pmf mixture: its mean lies between the two members' means
    m = {k: v.mean(axis=1) for k, v in arms.items()}
    lo, hi = np.minimum(m["SEQc"], m["FULL"]) - 0.1, np.maximum(m["SEQc"], m["FULL"]) + 0.1
    assert ((m["AVG"] >= lo) & (m["AVG"] <= hi)).all()
    off = grids.build_arms(a0, full, seq, w, np.zeros(20, dtype=bool))
    assert np.array_equal(
        off["SEQc"], off["A0"]
    )  # routed to A0 when the row is not served by the sequence


# --------------------------------------------------------------------------- the exporter equals the T478 exporter


def _raw(con: duckdb.DuckDBPyConnection, before: str | None = None) -> sequences.RawInputs:
    raw = sequences.load_raw(None, max_season=2025, before_date=before, con=con, with_avail=True)
    return raw


def test_exporter_equals_the_research_exporter_on_the_same_inputs(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Same RawInputs through both builders (the fixture seasons relabelled to 2024 because the research builder refuses 2025)."""
    from research.features import player_sequences as P

    raw = _raw(con)
    raw.games["season"] = 2024
    raw.tips = pl.DataFrame(schema={"game_id": pl.Utf8, "tip_et": pl.Datetime("us")})
    cfg_mine = sequences.SeqConfig(
        report=sequences.ReportTriggerConfig(tip_source="real"), max_season=2024
    )
    mine = sequences.build_export(raw, cfg_mine)
    cfg_theirs = P.SeqConfig(
        report=sequences.ReportTriggerConfig(tip_source="real"), max_season=2024
    )
    theirs = P.build_export(raw, cfg_theirs)  # type: ignore[arg-type]
    assert len(mine.meta) > 20
    for k in mine.arrays:
        a, b = np.asarray(mine.arrays[k]), np.asarray(theirs.arrays[k])
        assert a.tobytes() == b.tobytes(), k


def test_live_placeholder_rows_equal_the_played_rows_and_the_future_is_invisible(
    con: duckdb.DuckDBPyConnection,
) -> None:
    full = _raw(con)
    full.tips = pl.DataFrame(schema={"game_id": pl.Utf8, "tip_et": pl.Datetime("us")})
    cfg = sequences.SeqConfig(
        report=sequences.ReportTriggerConfig(tip_source="real"), max_season=2025
    )
    from nba.props.context_residual import asof_elo_margin

    g = pl.from_pandas(full.games).with_columns(pl.col("game_date").cast(pl.Date))
    full.elo = asof_elo_margin(g, sequences.load_elo()).to_pandas()
    ref = sequences.build_export(full, cfg, target_game_ids=None)
    day = pd.Timestamp(full.games["game_date"].max()) - pd.Timedelta(days=6)
    played = ref.meta[pd.to_datetime(ref.meta["game_date"]) == day]
    assert len(played) > 8
    games = full.games[pd.to_datetime(full.games["game_date"]) == day][
        ["game_id", "home_team", "away_team"]
    ]
    live = sequences.build_live_export(
        con,
        day.date(),
        2025,
        games,
        {},
        played[["game_id", "player_id", "team_id"]],
        pd.Timestamp("2100-01-01"),
        sequences.load_elo(),
    )

    def key(e: sequences.SeqExport) -> pd.MultiIndex:
        return pd.MultiIndex.from_arrays([e.arrays["game_id"].astype(str), e.arrays["player_id"]])

    pos = key(ref).get_indexer(key(live))
    assert (pos >= 0).all()
    for k in (
        "seq",
        "mask",
        "opp_step",
        "ctx",
        "team_idx",
        "opp_idx",
        "date_days",
        "season",
        "base",
        "n_prior",
    ):
        assert np.asarray(live.arrays[k]).tobytes() == np.asarray(ref.arrays[k])[pos].tobytes(), k


# --------------------------------------------------------------------------- store: tip refusal, write-on-change, counting start


def _row(**kw: Any) -> dict[str, object]:
    base: dict[str, object] = {c: None for c in store.COLUMNS}
    base.update(
        made_at=MADE, run_id="r", game_id=GID, player_id=1, stat="pts", tipoff_utc=TIPOFF, tip_source="real", in_prod_log=True, n_prior=9,
        model_id="m", month_block="2026-10", stale_model=False, avg_q19="[1,2]", full_q19="[1,2]", a0_q19="[1,2]", seq_q19="[1,2]", seqc_q19="[1,2]",
    )  # fmt: skip
    base.update(kw)
    return base


def test_writer_refuses_at_or_after_tip_and_dedups(con: duckdb.DuckDBPyConnection) -> None:
    assert store.append_rows(con, [_row()], MADE) == (1, 0, 0)
    assert store.append_rows(con, [_row(run_id="r2")], MADE) == (
        0,
        1,
        0,
    )  # same content: write-on-change
    assert store.append_rows(con, [_row(run_id="r3", avg_q19="[1,3]")], MADE) == (1, 0, 0)
    assert store.append_rows(con, [_row(run_id="r4")], TIPOFF) == (0, 0, 1)  # made_at == tipoff
    assert store.append_rows(con, [_row(run_id="r5")], TIPOFF + timedelta(minutes=1)) == (0, 0, 1)
    assert con.execute("SELECT count(*) FROM forward_avg_shadow").fetchone() == (2,)


def test_counting_start_needs_every_proof(con: duckdb.DuckDBPyConnection) -> None:
    store.ensure_tables(con)
    for p in ("P1a", "P1b", "P2", "P3"):
        store.set_meta(con, f"proof_{p}", "pass", NAIVE_UTC_NOW)
    with pytest.raises(PermissionError, match="P4"):
        store.set_counting_start(con, "2026-10-20", NAIVE_UTC_NOW)
    assert "counting_start" not in store.get_meta(con)
    store.set_meta(con, "proof_P4", "pass", NAIVE_UTC_NOW)
    store.set_counting_start(con, "2026-10-20", NAIVE_UTC_NOW)
    assert store.get_meta(con)["counting_start"] == "2026-10-20"


# --------------------------------------------------------------------------- bundles and staleness


def test_bundle_selection_valid_stale_and_missing(tmp_path: Path) -> None:
    root = tmp_path / "bundles"
    assert B.select_bundle(root, RUN_DATE, MADE) == (None, False)
    make_bundle(
        root,
        "2026-09",
        month_block="2026-09",
        fit_through="2026-08-31",
        installed=datetime(2026, 9, 1, 12),
    )
    b, stale = B.select_bundle(root, RUN_DATE, MADE)
    assert (
        b is not None and stale is True and b.month_block == "2026-09"
    )  # a missed October refit serves stale
    make_bundle(
        root,
        "2026-10",
        month_block="2026-10",
        fit_through="2026-09-30",
        installed=datetime(2026, 10, 1, 12),
    )
    b, stale = B.select_bundle(root, RUN_DATE, MADE)
    assert b is not None and stale is False and b.month_block == "2026-10"
    # installed after the run: not yet usable
    b, stale = B.select_bundle(root, RUN_DATE, datetime(2026, 9, 30))
    assert b is not None and b.month_block == "2026-09"
    # fit_through not strictly before the month: never valid
    make_bundle(
        root,
        "2026-11",
        month_block="2026-11",
        fit_through="2026-11-01",
        installed=datetime(2026, 10, 2),
    )
    b, stale = B.select_bundle(root, date(2026, 11, 5), datetime(2026, 11, 5, 18))
    assert stale is True


def test_tampered_weights_fail_verification(tmp_path: Path) -> None:
    d = make_bundle(tmp_path, "b")
    (d / "seed0.pt").write_bytes(b"x" + (d / "seed0.pt").read_bytes())
    with pytest.raises(ValueError, match="sha256"):
        B.verify(d)


# --------------------------------------------------------------------------- the step


def _run(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    root: Path,
    made_at: datetime = MADE,
    slate: list[SlateGame] | None = None,
) -> dict[str, Any]:
    return run_avg_shadow(
        con,
        RUN_DATE,
        slate or _slate(),
        made_at,
        "run1",
        season=2026,
        bundle_root=root,
        hb_dir=tmp_path / "hb",
        work_dir=tmp_path,
    )


def test_no_weights_is_skipped_with_a_heartbeat(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    info = _run(con, tmp_path, tmp_path / "empty")
    assert info["status"] == "skipped"
    assert con.execute(
        "SELECT status, rc, rows_written, games_seen FROM forward_avg_heartbeat"
    ).fetchall() == [("skipped", 0, 0, 1)]
    assert con.execute("SELECT count(*) FROM forward_avg_shadow").fetchone() == (0,)
    hb = json.loads((tmp_path / "hb" / "avg_shadow.json").read_text())
    assert hb["status"] == "skipped" and hb["consecutive_bad"] == 1 and hb["end_ts"] is not None


def test_inference_runs_and_rows_follow_the_rule(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    info = _run(con, tmp_path, root)
    assert info["status"] == "ok", info
    assert info["stale_model"] is True  # the fixture bundle is for 2000-01
    cur = con.execute(
        "SELECT stat, n_prior, stale_model, tip_source, in_prod_log, CAST(avg_q19 AS VARCHAR) AS avg_q19, CAST(avg_pmf199 AS VARCHAR) AS avg_pmf199, "
        "CAST(seqc_q19 AS VARCHAR) AS seqc_q19, CAST(seqc_pmf199 AS VARCHAR) AS seqc_pmf199, CAST(full_q19 AS VARCHAR) AS full_q19, a0_source, made_at, tipoff_utc "
        "FROM forward_avg_shadow ORDER BY player_id, stat"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    assert len(rows) == 6 * 4 and info["seq_rows"] >= 6
    for r in rows:
        assert (
            r["n_prior"] >= 3
            and r["stale_model"]
            and r["tip_source"] == "real"
            and r["in_prod_log"]
        )
        assert r["made_at"] < r["tipoff_utc"]
        assert r["a0_source"] == (
            "props_context_residual_lt" if r["stat"] == "pts" else "props_context_residual"
        )
        aq, ap, sq, sp = (
            json.loads(r[k]) for k in ("avg_q19", "avg_pmf199", "seqc_q19", "seqc_pmf199")
        )
        # the stored AVG grid is reproducible from the stored 199 values (exact) and sits between its two members (+-1 on 95% of cells)
        assert np.array_equal(grids.q19_of_grid199(np.array([ap])), np.array([aq]))
        assert np.array_equal(grids.q19_of_grid199(np.array([sp])), np.array([sq]))
        assert len(ap) == 199 and (np.diff(aq) >= 0).all()
        sq_i, fq_i, aq_i = np.array(sq), np.array(json.loads(r["full_q19"])), np.array(aq)
        assert (
            (aq_i >= np.minimum(sq_i, fq_i) - 1) & (aq_i <= np.maximum(sq_i, fq_i) + 1)
        ).mean() >= 0.95
    assert con.execute("SELECT status, rc, rows_written FROM forward_avg_heartbeat").fetchall() == [
        ("ok", 0, 24)
    ]
    # a second identical run at the same inputs writes nothing new (write-on-change)
    info2 = run_avg_shadow(
        con,
        RUN_DATE,
        _slate(),
        MADE + timedelta(minutes=1),
        "run2",
        season=2026,
        bundle_root=root,
        hb_dir=tmp_path / "hb",
        work_dir=tmp_path,
    )
    assert info2["written"] == 0 and info2["deduped"] == 24


def test_valid_bundle_is_not_stale(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(
        root,
        "oct",
        month_block="2026-10",
        fit_through="2026-09-30",
        installed=datetime(2026, 10, 1, 12),
    )
    info = _run(con, tmp_path, root)
    assert info["status"] == "ok" and info["stale_model"] is False
    assert con.execute(
        "SELECT DISTINCT stale_model, month_block FROM forward_avg_shadow"
    ).fetchall() == [(False, "2026-10")]


def test_serving_rule_non_regular_and_short_history_are_routed_to_full(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    # a preseason game id (001) and a play-in (005): never an AVG row
    seed_inputs(con, gid="0012600001")
    info = _run(con, tmp_path, root, slate=_slate("0012600001"))
    assert info["status"] == "skipped" and info["routed_full_not_regular"] == 1
    assert (
        json.loads((tmp_path / "hb" / "avg_shadow.json").read_text())["consecutive_bad"] == 0
    )  # a preseason slate is not a failure
    # a debut (no earlier played game) in a regular-season game: no AVG row for him
    con.execute("DELETE FROM forward_full_shadow")
    con.execute("DELETE FROM forward_predictions")
    seed_inputs(con, players=(1000, 1001, 4242))
    con.execute("UPDATE forward_full_shadow SET team_id = ? WHERE player_id = 4242", [T1])
    info = _run(con, tmp_path, root)
    assert info["status"] == "ok" and info["n_prior_lt3"] >= 0
    assert con.execute(
        "SELECT count(*) FROM forward_avg_shadow WHERE player_id = 4242"
    ).fetchone() == (0,)
    assert con.execute("SELECT count(DISTINCT player_id) FROM forward_avg_shadow").fetchone() == (
        2,
    )


def test_missing_full_grid_writes_no_row(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    seed_inputs(con)
    con.execute("DELETE FROM forward_full_shadow WHERE stat = 'reb'")
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    info = _run(con, tmp_path, root)
    assert info["status"] == "ok" and info["no_full"] == 6
    assert con.execute("SELECT count(*) FROM forward_avg_shadow WHERE stat = 'reb'").fetchone() == (
        0,
    )


def test_after_tip_nothing_is_written_and_a_crash_is_a_failed_heartbeat(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    info = _run(con, tmp_path, root, made_at=TIPOFF + timedelta(minutes=1))
    assert info["status"] == "skipped"
    assert con.execute("SELECT count(*) FROM forward_avg_shadow").fetchone() == (0,)
    (root / "fix" / "seed1.pt").write_bytes(
        b"corrupt"
    )  # sha mismatch => the step fails, loudly, and writes nothing
    info = _run(con, tmp_path, root)
    assert info["status"] == "failed" and "sha256" in info["detail"]
    assert con.execute(
        "SELECT status, rc FROM forward_avg_heartbeat ORDER BY started_at DESC LIMIT 1"
    ).fetchone() == ("failed", 1)
    hb = json.loads((tmp_path / "hb" / "avg_shadow.json").read_text())
    assert (
        hb["consecutive_bad"] == 1
    )  # nothing-to-serve (after tip) does not count; the failed run does


def test_inference_failure_in_the_child_is_a_failed_heartbeat_not_an_exception(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    d = make_bundle(root, "fix")
    m = json.loads((d / "manifest.json").read_text())
    m["seeds"] = [0, 1, 2, 3]  # seed3.pt does not exist: the child dies, the parent records it
    (d / "manifest.json").write_text(json.dumps(m))
    info = _run(con, tmp_path, root)
    assert info["status"] == "failed" and "inference rc" in info["detail"]
    assert con.execute("SELECT count(*) FROM forward_avg_shadow").fetchone() == (0,)


# --------------------------------------------------------------------------- settle reproduces the stored grids


def test_settle_scores_the_latest_pretip_row_and_reproduces_crps(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    _run(con, tmp_path, root)
    assert settle_avg_shadow(con, TIPOFF + timedelta(hours=3)) == {
        "avg_scored": 0,
        "avg_not_in_box": 0,
    }  # game not final
    g = pl.DataFrame(
        [
            {
                "game_id": GID,
                "game_date": RUN_DATE,
                "season": 2026,
                "home_team": T1,
                "away_team": T2,
                "home_pts": 101,
                "away_pts": 99,
            }
        ]
    )
    insert_rows(con, "games", g.columns, g)
    s = pl.DataFrame(_box(GID, T1, 1000, 10) + _box(GID, T2, 1100, 8))
    insert_rows(con, "player_game_stats", STAT_COLS, s.select(STAT_COLS))
    out = settle_avg_shadow(con, TIPOFF + timedelta(hours=4))
    assert out["avg_scored"] == 24
    assert settle_avg_shadow(con, TIPOFF + timedelta(hours=5)) == {
        "avg_scored": 0,
        "avg_not_in_box": 0,
    }  # idempotent
    for _stat, y, qa, qf, ca, cf in con.execute(
        "SELECT s.stat, s.y, CAST(a.avg_q19 AS VARCHAR), CAST(a.full_q19 AS VARCHAR), s.crps_avg, s.crps_full FROM forward_avg_scores s "
        "JOIN forward_avg_shadow a USING (game_id, player_id, stat, run_id) WHERE s.played"
    ).fetchall():
        assert (
            abs(integer_crps(json.loads(qa), y) - ca) < 1e-9
            and abs(integer_crps(json.loads(qf), y) - cf) < 1e-9
        )


# --------------------------------------------------------------------------- the primary rows do not move


def test_flag_off_by_default_and_primary_rows_identical_with_it_on(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    from nba.daily.__main__ import _parser

    assert _parser().parse_args(["run", "--date", "2026-10-28"]).log_avg_shadow is False
    assert (
        _parser().parse_args(["run", "--date", "2026-10-28", "--log-avg-shadow"]).log_avg_shadow
        is True
    )
    from tests.daily.conftest import add_history
    from tests.fixtures.loader import build_fixture_db

    other = build_fixture_db(":memory:")
    add_history(other)
    kw: dict[str, Any] = dict(
        schedule_fn=lambda season: slate_games(), now=TIPOFF - timedelta(hours=2), skip_ingest=True, skip_injury=True, with_props=False,
    )  # fmt: skip
    off = run_daily(con, RUN_DATE, **kw)
    on = run_daily(
        other,
        RUN_DATE,
        with_avg_shadow=True,
        avg_bundle_root=tmp_path / "none",
        avg_heartbeat_dir=tmp_path / "hb",
        **kw,
    )
    assert "skipped" in on.model_status["avg_shadow"]
    assert off.shadow_errors == [] and on.shadow_errors == []
    for fa, fb in zip(canonical_frames(con), canonical_frames(other), strict=True):
        assert fa.equals(fb)
    assert canonical_csv(con) == canonical_csv(other)


def test_avg_tables_are_outside_the_canonical_dump(con: duckdb.DuckDBPyConnection) -> None:
    before = canonical_csv(con)
    store.append_rows(con, [_row()], MADE)
    store.write_heartbeat(
        con,
        slate_date=RUN_DATE,
        run_id="r",
        started_at=MADE,
        finished_at=MADE,
        games_seen=1,
        rows_written=1,
        model_id="m",
        status="ok",
        rc=0,
    )
    assert canonical_csv(con) == before
    assert "avg" not in before


def test_inference_network_equals_the_colab_network() -> None:
    """``infer.build_net`` and the Colab ``SeqNet`` (the class T478 trained): same parameter names and the same forward in eval mode (child process: torch stays out of this one)."""
    script = """
import importlib.util, numpy as np, torch
spec = importlib.util.spec_from_file_location("sp", "research/colab/jobs/seq_props/seq_props_train.py")
sp = importlib.util.module_from_spec(spec); spec.loader.exec_module(sp)
from nba.daily.avg_shadow.infer import build_net
scale = np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32)
torch.manual_seed(0)
a = sp.SeqNet(27, 34, 40, scale, "transformer"); a.eval()
b = build_net(27, 34, 40, scale); b.load_state_dict(a.state_dict()); b.eval()
g = torch.Generator().manual_seed(1)
n = 16
mask = torch.rand(n, 20, generator=g) > 0.3
batch = dict(seq=torch.randn(n, 20, 27, generator=g), mask=mask, opp_step=torch.randint(0, 31, (n, 20), generator=g),
             ctx=torch.randn(n, 34, generator=g), pid=torch.randint(0, 41, (n,), generator=g), team=torch.randint(0, 31, (n,), generator=g),
             opp=torch.randint(0, 31, (n,), generator=g), base=torch.rand(n, 4, generator=g) * 10, y=torch.zeros(n, 4))
with torch.no_grad():
    ma, qa = a(batch); mb, qb = b(batch)
assert torch.equal(ma, mb) and torch.equal(qa, qb)
assert list(a.state_dict()) == list(b.state_dict())
print("EQUAL")
"""
    assert "EQUAL" in _child(["-c", script])


# --------------------------------------------------------------------------- Mac preflight and watchdog


def test_pmset_preflight_parses_the_ac_block() -> None:
    from nba.daily.avg_shadow import preflight as PF

    ok = "Battery Power:\n sleep                1\nAC Power:\n displaysleep         0\n sleep                0\n disksleep            10\n"
    assert PF.parse_ac_sleep(ok) == 0 and PF.sleep_warning(lambda: ok, "darwin") is None
    bad = ok.replace(" sleep                0\n", " sleep                10\n")
    assert PF.parse_ac_sleep(bad) == 10 and "sleeps after 10 min" in (
        PF.sleep_warning(lambda: bad, "darwin") or ""
    )
    assert "no AC sleep" in (PF.sleep_warning(lambda: "Battery Power:\n sleep 1\n", "darwin") or "")
    assert PF.sleep_warning(lambda: bad, "linux") is None  # silent off macOS


def test_watchdog_alerts_after_three_bad_slate_runs_and_for_a_missed_install(
    tmp_path: Path,
) -> None:
    from zoneinfo import ZoneInfo

    from nba.ops.watchdog import SHADOW_JOBS, evaluate_shadow_jobs

    tz = ZoneInfo("America/Chicago")
    assert [j.name for j in SHADOW_JOBS] == ["avg_shadow"]
    now = datetime(2026, 11, 2, 18, 0, tzinfo=tz)
    assert (
        evaluate_shadow_jobs(now, tmp_path, tz) == []
    )  # no heartbeat file: the flag is off, silent
    hb = tmp_path / "avg_shadow.json"
    hb.write_text(
        json.dumps(
            {
                "job": "avg_shadow",
                "start_ts": "2026-11-02T22:00:00",
                "end_ts": "2026-11-02T22:01:00",
                "status": "failed",
                "consecutive_bad": 2,
                "month_block": "2026-11",
            }
        )
    )
    assert evaluate_shadow_jobs(now, tmp_path, tz) == []
    hb.write_text(
        json.dumps(
            {
                "job": "avg_shadow",
                "start_ts": "2026-11-02T22:00:00",
                "end_ts": "2026-11-02T22:01:00",
                "status": "failed",
                "consecutive_bad": 3,
                "month_block": "2026-11",
            }
        )
    )
    assert [a.key for a in evaluate_shadow_jobs(now, tmp_path, tz)] == ["avg_shadow:failed"]
    # installed through October only, on Nov 4: one miss is enough; on Nov 2 (before the 3rd) it is not yet
    hb.write_text(
        json.dumps(
            {
                "job": "avg_shadow",
                "start_ts": "2026-11-02T22:00:00",
                "end_ts": "2026-11-02T22:01:00",
                "status": "ok",
                "consecutive_bad": 0,
                "month_block": "2026-10",
            }
        )
    )
    assert evaluate_shadow_jobs(now, tmp_path, tz) == []
    late = datetime(2026, 11, 4, 18, 0, tzinfo=tz)
    assert [a.key for a in evaluate_shadow_jobs(late, tmp_path, tz)] == ["avg_shadow:install"]


def test_shadow_alert_reaches_the_pipeline_when_the_step_fails(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    kw: dict[str, Any] = dict(
        schedule_fn=lambda season: slate_games(),
        now=TIPOFF - timedelta(hours=2),
        skip_ingest=True,
        skip_injury=True,
        with_props=False,
    )
    s = run_daily(
        con,
        RUN_DATE,
        with_avg_shadow=True,
        avg_bundle_root=tmp_path / "none",
        avg_heartbeat_dir=tmp_path / "hb",
        **kw,
    )
    assert (tmp_path / "hb" / "avg_shadow.json").exists()
    assert json.loads((tmp_path / "hb" / "avg_shadow.json").read_text())["status"] == "skipped"
    assert s.n_predicted_games == 2 or s.n_predicted_games >= 1


def test_report_out_teammate_with_int64_roster_does_not_break_the_merge(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Regression (found in the first real-data replay): the DB's integer keys are int32, the roster built in the step is int64; the as-of merges of the OUT-teammate
    context need equal key dtypes. A reported-OUT rotation player exercises that branch; the export must also carry the report (has_report, n_rep_out)."""
    out_pid = 1000  # a rotation player of T1 (30 minutes in every fixture game)
    con.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source, pulled_at) VALUES (?,?,?,?,?,?)",
        [
            out_pid,
            datetime(2026, 10, 28, 12, 0),
            GID,
            "out",
            "nba_official_report",
            datetime(2026, 10, 28, 12, 1),
        ],
    )
    slate = pd.DataFrame({"game_id": [GID], "home_team": [T1], "away_team": [T2]}).astype(
        {"home_team": "int64", "away_team": "int64"}
    )
    roster = pd.DataFrame(
        {"game_id": [GID, GID], "player_id": [1001, 1101], "team_id": [T1, T2]}
    ).astype("int64", errors="ignore")
    roster["game_id"] = GID
    tip_et = pd.Timestamp("2026-10-28 19:30")
    exp = sequences.build_live_export(
        con,
        RUN_DATE,
        2026,
        slate,
        {GID: tip_et.to_pydatetime()},
        roster,
        pd.Timestamp("2026-10-28 17:00"),
        sequences.load_elo(),
    )
    cx = dict(zip(sequences.CTX_FEATURES, exp.arrays["ctx"].T, strict=True))
    assert (cx["has_report"] == 1).all() and (
        cx["n_rep_out"][exp.arrays["player_id"] == 1001] > 0
    ).all()
    # a snapshot stamped after tip - 60 is invisible to the export (real tip, never a proxy)
    con.execute("UPDATE player_availability SET as_of = ?", [datetime(2026, 10, 28, 18, 45)])
    exp = sequences.build_live_export(
        con,
        RUN_DATE,
        2026,
        slate,
        {GID: tip_et.to_pydatetime()},
        roster,
        pd.Timestamp("2026-10-28 19:00"),
        sequences.load_elo(),
    )
    assert (exp.arrays["ctx"][:, sequences.CTX_FEATURES.index("has_report")] == 0).all()


# --------------------------------------------------------------------------- the steward's meta writes and the daily display


def test_meta_records_bundle_and_proofs_and_refuses_an_early_counting_start(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    from nba.daily.avg_shadow import meta as M

    b = B.verify(make_bundle(tmp_path, "b", month_block="2026-10", fit_through="2026-09-30"))
    M.record_bundle(con, b, NAIVE_UTC_NOW)
    got = store.get_meta(con)
    assert (
        got["bundle_2026-10.model_id"] == b.model_id
        and got["bundle_2026-10.fit_through"] == "2026-09-30"
        and got["bundle_2026-10.gate_id"] == b.gate_id
    )
    ev = tmp_path / "p2.json"
    ev.write_text('{"pass": true}')
    for name in ("P1a", "P1b", "P2", "P3"):
        M.record_proof(con, name, "pass", ev, NAIVE_UTC_NOW)
    with pytest.raises(PermissionError):
        store.set_counting_start(con, "2026-10-20", NAIVE_UTC_NOW)
    M.record_proof(con, "P4", "fail", None, NAIVE_UTC_NOW)  # a failed proof is not a pass
    with pytest.raises(PermissionError):
        store.set_counting_start(con, "2026-10-20", NAIVE_UTC_NOW)
    assert "counting_start" not in store.get_meta(con) and store.get_meta(con)[
        "proof_P2_evidence"
    ].endswith(__import__("hashlib").sha256(ev.read_bytes()).hexdigest())


def test_daily_display_has_counts_and_no_scores(con: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.avg_shadow import display
    from nba.daily.report import _avg_shadow_section

    assert (
        display.counts(con) is None and _avg_shadow_section(con) == []
    )  # never ran: no briefing line
    seed_inputs(con)
    store.append_rows(con, [_row()], MADE)
    store.write_heartbeat(
        con,
        slate_date=date.today(),
        run_id="r",
        started_at=MADE,
        finished_at=MADE,
        games_seen=1,
        rows_written=1,
        model_id="m",
        status="ok",
        rc=0,
    )
    text = "\n".join(_avg_shadow_section(con))
    assert "AVG shadow" in text and "NOT SET" in text and "rows 1" in text
    assert "crps" not in text.lower() and "delta" not in text.lower().replace(
        "no delta is published", ""
    )


# --------------------------------------------------------------------------- review fixes


def _hb(tmp: Path) -> dict[str, Any]:
    return json.loads((tmp / "hb" / "avg_shadow.json").read_text())  # type: ignore[no-any-return]


def test_a_shadow_section_that_raises_never_breaks_the_briefing(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.daily import report

    def boom(*a: Any) -> list[str]:
        raise RuntimeError("shadow exploded")

    for name in ("_avg_shadow_section", "_full_shadow_section", "_pplay_shadow_section"):
        monkeypatch.setattr(report, name, boom)
    text = report.build_report(con)
    assert "unavailable: RuntimeError: shadow exploded" in text and text.count("unavailable") == 3


def test_no_full_rows_is_no_full_and_counts_as_bad(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    con.execute("DELETE FROM forward_full_shadow")  # flag off or the FULL step failed
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    info = _run(con, tmp_path, root)
    assert info["status"] == "no_full" and info["seq_rows"] > 0  # inference ran, nothing to mix
    assert _hb(tmp_path)["status"] == "no_full" and _hb(tmp_path)["consecutive_bad"] == 1
    assert con.execute("SELECT status FROM forward_avg_heartbeat").fetchall() == [("no_full",)]
    _run(con, tmp_path, root)
    assert _hb(tmp_path)["consecutive_bad"] == 2  # never reset by a run that served nothing


def test_nothing_to_serve_is_checked_before_missing_weights(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    info = _run(
        con, tmp_path, tmp_path / "empty", slate=_slate("0012600001")
    )  # preseason, no weights installed
    assert (
        info["detail"] == "no regular-season game to serve"
        and _hb(tmp_path)["consecutive_bad"] == 0
    )


def test_a_non_serving_run_keeps_the_last_bundle_identity_in_the_heartbeat_file(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    assert _run(con, tmp_path, root)["status"] == "ok"
    first = _hb(tmp_path)
    assert first["model_id"] and first["month_block"] == "2000-01" and first["stale_model"] is True
    _run(con, tmp_path, root, slate=_slate("0012600001"))  # nothing to serve
    again = _hb(tmp_path)
    assert (
        again["model_id"] == first["model_id"]
        and again["month_block"] == "2000-01"
        and again["stale_model"] is True
    )


def test_watchdog_alerts_on_stale_serving_and_ignores_idle_or_flags_hung_files(
    tmp_path: Path,
) -> None:
    from zoneinfo import ZoneInfo

    from nba.ops.watchdog import evaluate_shadow_jobs

    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 11, 4, 17, 0, tzinfo=tz)  # 23:00 UTC
    hb = tmp_path / "avg_shadow.json"

    def write(**kw: Any) -> None:
        rec = {
            "job": "avg_shadow",
            "status": "ok",
            "consecutive_bad": 0,
            "start_ts": "2026-11-04T22:00:00",
            "end_ts": "2026-11-04T22:01:00",
        }
        hb.write_text(json.dumps({**rec, **kw}))

    # serving a stale bundle although month_block names the current month: alert on the serving flag
    write(month_block="2026-11", stale_model=True)
    assert [a.key for a in evaluate_shadow_jobs(now, tmp_path, tz)] == ["avg_shadow:install"]
    write(month_block="2026-11", stale_model=False)
    assert evaluate_shadow_jobs(now, tmp_path, tz) == []
    # flag switched off: old file, even with consecutive_bad, stays silent
    write(consecutive_bad=9, start_ts="2026-10-20T22:00:00", end_ts="2026-10-20T22:01:00")
    assert evaluate_shadow_jobs(now, tmp_path, tz) == []
    # a START with no END past the timeout: the run died
    write(end_ts=None, start_ts="2026-11-04T22:00:00", status="running")
    assert [a.key for a in evaluate_shadow_jobs(now, tmp_path, tz)] == ["avg_shadow:hung"]
    write(
        end_ts=None, start_ts="2026-11-04T22:50:00", status="running"
    )  # 10 minutes in: still running
    assert evaluate_shadow_jobs(now, tmp_path, tz) == []


def test_display_uses_the_reports_season(con: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.avg_shadow import display

    gid = con.execute(
        "SELECT game_id FROM games WHERE season = 2025 AND home_pts > 0 LIMIT 1"
    ).fetchone()[0]  # type: ignore[index]
    tip = datetime(2026, 1, 1, 23, 0)
    store.append_rows(
        con,
        [_row(game_id=gid, tipoff_utc=tip, made_at=tip - timedelta(hours=3))],
        tip - timedelta(hours=3),
    )
    assert display.counts(con, 2025)["avg_triples"] == 1  # type: ignore[index]
    assert display.counts(con, 2026)["avg_triples"] == 0  # type: ignore[index]
    assert "on-time triples 1" in "\n".join(display.section(con, 2025))


def test_inference_output_file_is_closed(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.daily.avg_shadow import run as R

    opened: list[Any] = []
    real = np.load

    def spy(*a: Any, **k: Any) -> Any:
        z = real(*a, **k)
        opened.append(z)
        return z

    monkeypatch.setattr(R.np, "load", spy)
    seed_inputs(con)
    root = tmp_path / "bundles"
    make_bundle(root, "fix")
    assert _run(con, tmp_path, root)["status"] == "ok"
    assert opened and all(z.zip is None for z in opened)  # NpzFile.close() clears .zip


def test_ctx_schema_mismatch_fails_with_a_named_message(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    seed_inputs(con)
    root = tmp_path / "bundles"
    d = make_bundle(root, "fix")
    m = json.loads((d / "manifest.json").read_text())
    m["n_ctx"] = 40
    (d / "manifest.json").write_text(json.dumps(m))
    info = _run(con, tmp_path, root)
    assert (
        info["status"] == "failed"
        and "schema mismatch" in info["detail"]
        and "40" in info["detail"]
    )


def test_every_shadow_settle_is_isolated_even_when_its_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nba.daily import __main__ as M
    from tests.fixtures.loader import build_fixture_db

    db = tmp_path / "t.duckdb"
    build_fixture_db(db).close()
    monkeypatch.setattr(
        M,
        "SHADOW_SETTLERS",
        (("ghost", "nba.daily.no_such_shadow", "settle", "games"), *M.SHADOW_SETTLERS),
    )
    assert M.main(["--db-path", str(db), "settle"]) == 0
    out = capsys.readouterr().out
    assert "ghost shadow settle failed (primary unaffected): ModuleNotFoundError" in out
