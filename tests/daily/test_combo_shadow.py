# ruff: noqa: E501, SIM300
"""COMBO_STOCKS shadow (docs/prereg/COMBO_STOCKS_SHADOW.md): frozen config, P1 through the writer's own code path, planted-future guards,
dedup, tip refusal, the same-date cache, settle (exact integer CRPS, T-60 rule), heartbeat, identical primary rows, canonical-dump exclusion."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
import yaml
from scipy import stats

from nba.daily import pipeline
from nba.daily.canonical_dump import canonical_frames
from nba.daily.combo_shadow import config as CFG
from nba.daily.combo_shadow import run as R
from nba.daily.combo_shadow import settle as S
from nba.daily.combo_shadow import store
from nba.daily.pipeline import run_daily
from nba.daily.pplay_shadow.features import SlateGame
from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME, load_elo_params
from nba.ingest.cache import insert_rows
from nba.props import combo_copula as C
from nba.props import full_support
from nba.props.context_residual import CRPS_TAUS, ContextResidualConfig
from tests.daily.conftest import RUN_DATE, STAT_COLS, T1, T2, T3, T4, TIPOFF, _box, slate_games
from tests.daily.test_pplay_shadow import GID, _wide

ELO = load_elo_params()
MADE = TIPOFF - timedelta(minutes=120)
PIDS = [*range(1000, 1006), *range(1100, 1106)]
ROOT = Path(__file__).resolve().parents[2]
CCFG = ContextResidualConfig(n_estimators=25, min_child_samples=20, min_cal_rows=300, n_jobs=1)
PROD_MEAN = {"pts": 12.0, "reb": 4.0, "ast": 3.0, "fg3m": 1.2}


def _slate() -> list[SlateGame]:
    return [SlateGame(GID, T1, T2, TIPOFF)]


def history(con: duckdb.DuckDBPyConnection, n: int = 90, seed: int = 5) -> None:
    """n completed games per pair (T1-T2, T3-T4), one every 2 days; 6 players a team; blk and stl are Poisson with a per-player rate."""
    rng = np.random.default_rng(seed)
    games, rows = [], []
    for i in range(n):
        d = date(2026, 1, 1) + timedelta(days=2 * i)
        for k, (h, a, hb) in enumerate([(T1, T2, 1000), (T3, T4, 3000)]):
            gid = f"00225{k}{i:04d}"
            games.append({"game_id": gid, "game_date": d, "season": 2025, "home_team": h, "away_team": a, "home_pts": 100 + i % 7, "away_pts": 98})  # fmt: skip
            for team, base in ((h, hb), (a, hb + 100)):
                for j in range(6):
                    mins = float(34.0 - 4.0 * j + rng.normal(0, 3))
                    mins = max(mins, 0.0) if j < 5 or rng.random() < 0.5 else 0.0
                    rows.append(
                        {
                            "game_id": gid, "player_id": base + j, "team_id": team, "minutes": mins,
                            "pts": int(rng.poisson(0.35 * mins)), "reb": int(rng.poisson(0.12 * mins)),
                            "ast": int(rng.poisson(0.09 * mins)), "fg3m": int(rng.poisson(0.04 * mins)),
                            "stl": int(rng.poisson(0.03 * (1 + j % 3) * mins)), "blk": int(rng.poisson(0.02 * (1 + j % 2) * mins)),
                            "tov": 1, "starter": j < 3, "fgm": 3, "fga": 8, "fg3a": 3, "ftm": 1, "fta": 2, "oreb": 1, "dreb": 3, "pf": 2,
                        }
                    )  # fmt: skip
    g = pl.DataFrame(games)
    insert_rows(con, "games", g.columns, g)
    s = pl.DataFrame(rows)
    insert_rows(con, "player_game_stats", STAT_COLS, s.select(STAT_COLS))


@pytest.fixture
def bcon(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    con.execute("DELETE FROM player_game_stats WHERE game_id LIKE '00225%'")
    con.execute("DELETE FROM games WHERE game_id LIKE '00225%'")
    history(con)
    return con


def seed_prod(
    con: duckdb.DuckDBPyConnection,
    made_at: datetime = MADE - timedelta(minutes=10),
    run_id: str = "prod1",
    drop: set[tuple[int, str]] | None = None,
) -> None:
    """Production-shaped context rows (pts from the lt variant) with a real ``p_ge_full`` (n = 199) for every player of the slate game."""
    rows = []
    for pid in PIDS:
        scale = 1.0 + 0.1 * (pid % 100) / 5
        for stat, mean in PROD_MEAN.items():
            if drop and (pid, stat) in drop:
                continue
            q = np.asarray(stats.poisson.ppf(CRPS_TAUS, mean * scale), dtype=float)
            fs = full_support.from_samples(q, stat)
            model = CONTEXT_LT_PROPS_MODEL_NAME if stat == "pts" else CONTEXT_PROPS_MODEL_NAME
            rows.append(
                (
                    run_id,
                    made_at,
                    GID,
                    TIPOFF,
                    model,
                    "v1",
                    stat,
                    pid,
                    json.dumps({"p_ge_full": fs, "q_grid": [float(v) for v in q[9::10]][:19]}),
                )
            )
    con.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", rows)


@pytest.fixture
def hb(tmp_path: Path) -> Path:
    return tmp_path / "hb"


def _rows(c: duckdb.DuckDBPyConnection, where: str = "TRUE") -> list[tuple[Any, ...]]:
    return c.execute(f"SELECT player_id, target, CAST(s_pmf199 AS VARCHAR), CAST(indep_pmf199 AS VARCHAR), CAST(recent_pmf199 AS VARCHAR), run_id, n_prior FROM forward_combo_shadow WHERE {where} ORDER BY run_id, player_id, target").fetchall()  # fmt: skip


def go(c: duckdb.DuckDBPyConnection, hbd: Path, **kw: Any) -> dict[str, Any]:
    made_at = kw.pop("made_at", MADE)
    run_id = kw.pop("run_id", "r1")
    return R.run_combo_shadow(
        c, RUN_DATE, _slate(), made_at, run_id, _wide(), {}, ELO, hb_dir=hbd, ccfg=CCFG, **kw
    )


# --------------------------------------------------------------------------- the frozen config
def test_config_is_the_frozen_one() -> None:
    cfg = CFG.load_config()
    assert cfg.config_sha == CFG.FROZEN_CONFIG_SHA and cfg.base_id == CFG.FROZEN_BASE_SHA
    assert (
        cfg.r.shape == (6, 6)
        and np.allclose(np.diag(cfg.r), 1.0)
        and np.linalg.eigvalsh(cfg.r).min() > 0.2
    )
    assert cfg.floors["pra"] == 0.0438 and cfg.n_min == 3000


def test_a_changed_config_is_refused(tmp_path: Path) -> None:
    raw = yaml.safe_load((ROOT / "configs/combo_stocks_shadow.yaml").read_text())
    raw["floors"]["blk"] = 0.0051
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(CFG.ComboConfigError):
        CFG.load_config(p)
    assert CFG.load_config(p, verify=False).floors["blk"] == 0.0051


def test_p1_reference_is_reproduced_through_the_writers_code_path() -> None:
    """P1 through run.py: the committed reference's marginal CDFs give the committed grids bit for bit in two chunkings."""
    cfg = CFG.load_config()
    ref = np.load(ROOT / "reports/prereg_combo_stocks_shadow/p1_reference.npz")
    assert np.array_equal(ref["R"], cfg.r) and str(ref["base_sha256"]) == cfg.base_id
    cdfs = {s: ref[f"cdf_{s}"] for s in C.STAT_ORDER}
    for chunk in (200, 17):
        got, ind = R.joint_from_cdfs(cdfs, cfg, chunk=chunk)
        for c in C.COMBOS:
            assert np.array_equal(got[c], ref[f"expected_{c}"]), (c, chunk)
            assert np.array_equal(ind[c], ref[f"expected_indep_{c}"]), (c, chunk)


# --------------------------------------------------------------------------- the writer
def test_writes_seven_targets_for_servable_players(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    info = go(bcon, hb)
    assert info["status"] == "ok" and info["written"] == info["rows"] == 7 * len(PIDS), info
    rows = _rows(bcon)
    assert {r[1] for r in rows} == set(CFG.TARGETS)
    for _pid, target, s, ind, rec, _rid, n_prior in rows:
        s_, r_ = json.loads(s), json.loads(rec)
        assert len(s_) == len(r_) == 199 and s_ == sorted(s_) and min(s_) >= 0 and n_prior >= 5
        assert (ind is not None) == (target in CFG.COMBO_TARGETS)
        if ind is not None:
            assert len(json.loads(ind)) == 199
    meta = bcon.execute(
        "SELECT DISTINCT tip_source, in_prod_log, r_id, base_id, model_id, fit_through FROM forward_combo_shadow"
    ).fetchall()
    cfg = CFG.load_config()
    assert len(meta) == 1 and meta[0][:4] == ("real", True, cfg.r_id, cfg.base_id)
    assert meta[0][4] == R.model_id_of(cfg, CCFG) and meta[0][5] < RUN_DATE
    q19 = bcon.execute(
        "SELECT CAST(s_pmf199 AS VARCHAR), CAST(s_q19 AS VARCHAR) FROM forward_combo_shadow"
    ).fetchall()
    for s, q in q19:
        assert C.q19_of_grid199(np.array([json.loads(s)]))[0].tolist() == json.loads(q)


def test_combos_are_coherent_draw_by_draw(bcon: duckdb.DuckDBPyConnection, hb: Path) -> None:
    """PRA >= PR, PA, RA and PRA >= RA >= 0 hold at every quantile, because every combo is a sum of the same simulated games."""
    seed_prod(bcon)
    go(bcon, hb)
    by: dict[tuple[int, str], np.ndarray] = {
        (r[0], r[1]): np.array(json.loads(r[2])) for r in _rows(bcon)
    }
    for pid in PIDS:
        for small in ("pr", "pa", "ra"):
            assert (by[(pid, "pra")] >= by[(pid, small)]).all()
    s = {pid: by[(pid, "pra")].mean() for pid in PIDS}
    assert all(v > 0 for v in s.values())


def test_the_heartbeat_file_and_table_record_the_run(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    info = go(bcon, hb)
    f = json.loads((hb / "combo_shadow.json").read_text())
    assert (
        f["job"] == "combo_shadow"
        and f["status"] == "ok"
        and f["rc"] == 0
        and f["end_ts"]
        and f["start_ts"]
    )
    assert (
        f["rows_written"] == info["written"] > 0
        and f["consecutive_bad"] == 0
        and f["model_id"] == info["model_id"]
    )
    row = bcon.execute(
        "SELECT status, rc, rows_written, model_id FROM forward_combo_heartbeat"
    ).fetchall()
    assert row == [("ok", 0, info["written"], info["model_id"])]


def test_a_failure_is_recorded_not_raised_and_counts(
    bcon: duckdb.DuckDBPyConnection, hb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_prod(bcon)

    def boom(*a: object, **k: object) -> object:
        raise RuntimeError("fit exploded")

    monkeypatch.setattr(R, "fit_nb", boom)
    n_before = bcon.execute("SELECT count(*) FROM forward_predictions").fetchone()
    info = go(bcon, hb)
    assert info["status"] == "failed" and "fit exploded" in info["detail"]
    f = json.loads((hb / "combo_shadow.json").read_text())
    assert f["status"] == "failed" and f["rc"] == 1 and f["consecutive_bad"] == 1
    go(bcon, hb, run_id="r2")
    assert json.loads((hb / "combo_shadow.json").read_text())["consecutive_bad"] == 2
    assert bcon.execute("SELECT count(*) FROM forward_predictions").fetchone() == n_before
    assert bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone() == (0,)


def test_a_player_without_all_four_production_rows_is_not_servable(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon, drop={(1001, "fg3m"), (1100, "pts")})
    info = go(bcon, hb)
    got = {r[0] for r in _rows(bcon)}
    assert 1001 not in got and 1100 not in got and len(got) == len(PIDS) - 2
    assert info["missing_stat_row"] == 2


def test_no_production_rows_is_a_skip_not_a_failure(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    info = go(bcon, hb)
    assert info["status"] == "skipped" and "no production rows" in info["detail"]
    # review M7: on a regular-season slate this is a broken join, not a quiet day: it counts toward the watchdog's 3
    assert json.loads((hb / "combo_shadow.json").read_text())["consecutive_bad"] == 1


def test_a_preseason_slate_is_skipped(bcon: duckdb.DuckDBPyConnection, hb: Path) -> None:
    seed_prod(bcon)
    pre = [SlateGame("0012600001", T1, T2, TIPOFF)]
    info = R.run_combo_shadow(
        bcon, RUN_DATE, pre, MADE, "r1", _wide(), {}, ELO, hb_dir=hb, ccfg=CCFG
    )
    assert info["status"] == "skipped" and "regular-season" in info["detail"]
    assert bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone() == (0,)


def test_after_tip_is_refused_and_dedup_skips_repeats(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    late = go(bcon, hb, made_at=TIPOFF + timedelta(minutes=1))
    assert bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone() == (0,)
    assert late["status"] == "skipped"  # the game has tipped: nothing is passed to the writer
    first = go(bcon, hb, run_id="x")["written"]
    again = go(bcon, hb, run_id="y")
    assert first > 0 and again["written"] == 0 and again["deduped"] == first
    n, after = bcon.execute(
        "SELECT count(*), count(*) FILTER (WHERE made_at >= tipoff_utc) FROM forward_combo_shadow"
    ).fetchone() or (0, 0)
    assert n == first and after == 0


def test_the_store_itself_refuses_a_row_at_or_after_tip(bcon: duckdb.DuckDBPyConnection) -> None:
    row = {c: None for c in store.COLUMNS} | {
        "made_at": TIPOFF,
        "tipoff_utc": TIPOFF,
        "game_id": GID,
        "player_id": 1,
        "target": "blk",
    }
    assert store.append_rows(bcon, [row], TIPOFF) == (0, 0, 1)


def test_same_date_cache_reuses_the_fit_and_matches(
    bcon: duckdb.DuckDBPyConnection, hb: Path, tmp_path: Path
) -> None:
    seed_prod(bcon)
    a = go(bcon, hb, run_id="a", cache_root=tmp_path / "cache")
    assert a["cache_hit"] is False
    b = go(bcon, hb, run_id="b", cache_root=tmp_path / "cache")
    assert b["cache_hit"] is True and b["written"] == 0 and b["deduped"] == a["written"]
    assert (tmp_path / "cache" / f"{RUN_DATE.isoformat()}_combo" / "models.pkl").exists()


def test_planted_future_changes_nothing_and_a_past_edit_does(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    """P2: a later game, tonight's own (leaked) box and a report after the run do not move a grid; editing a PRIOR game does."""
    seed_prod(bcon)
    go(bcon, hb, run_id="a")
    base = {(r[0], r[1]): (r[2], r[3], r[4]) for r in _rows(bcon)}
    later = RUN_DATE + timedelta(days=3)
    bcon.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES ('{GID}x', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 90, 99), ('0022600099', DATE '{later}', 2026, {T1}, {T3}, 80, 70)"
    )
    box = pl.DataFrame(
        _box(f"{GID}x", T1, 1000, 40) + _box("0022600099", T1, 1000, 30)
    ).with_columns(pl.lit(11).alias("blk"), pl.lit(9).alias("stl"))
    insert_rows(bcon, "player_game_stats", STAT_COLS, box.select(STAT_COLS))
    info = go(bcon, hb, run_id="b")
    assert info["written"] == 0 and info["deduped"] == len(base)
    bcon.execute(
        "UPDATE player_game_stats SET blk = 15, stl = 12, pts = 60 WHERE game_id = '0022500089' AND player_id = 1000"
    )
    go(bcon, hb, run_id="c")
    changed = _rows(bcon, "run_id = 'c' AND player_id = 1000")
    assert changed and {r[1] for r in changed} >= {
        "blk",
        "pra",
    }  # the recent windows (and the heads) moved for him
    assert all(r[4] != base[(r[0], r[1])][2] for r in changed if r[1] in ("blk", "stl", "pra"))


def test_a_report_out_before_the_run_is_an_input(bcon: duckdb.DuckDBPyConnection) -> None:
    """A usable OUT report passed to the run moves has_report / vac_blk for the slate rows (the integer grids may not move: only the mean does)."""
    base = R.build_combo_frame(bcon, RUN_DATE, _slate(), _wide(), {}, ELO, CCFG).filter(
        pl.col("game_id") == GID
    )
    rep = R.build_combo_frame(bcon, RUN_DATE, _slate(), _wide(), {GID: {1001}}, ELO, CCFG).filter(
        pl.col("game_id") == GID
    )
    assert base["has_report"].sum() == 0 and rep["has_report"].sum() > 0
    t1 = pl.col("team_id") == T1
    assert (
        rep.filter(t1)["vac_blk"].drop_nulls().sum() > 0
        and base.filter(t1)["vac_blk"].drop_nulls().sum() == 0
    )


def test_production_state_is_untouched(bcon: duckdb.DuckDBPyConnection, hb: Path) -> None:
    seed_prod(bcon)
    before = canonical_frames(bcon)
    n = bcon.execute("SELECT count(*) FROM forward_predictions").fetchone()
    go(bcon, hb)
    after = canonical_frames(bcon)
    assert before[0].equals(after[0]) and before[1].equals(after[1])
    assert bcon.execute("SELECT count(*) FROM forward_predictions").fetchone() == n


# --------------------------------------------------------------------------- settle
def _final(c: duckdb.DuckDBPyConnection, play: set[int]) -> None:
    c.execute(
        f"INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) VALUES ('{GID}', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 101, 99)"
    )
    box = pl.DataFrame(_box(GID, T1, 1000, 20) + _box(GID, T2, 1100, 18))
    box = box.with_columns(
        pl.when(pl.col("player_id").is_in(list(play))).then(30.0).otherwise(0.0).alias("minutes"),
        pl.lit(2).alias("blk"),
        pl.lit(1).alias("stl"),
    )
    insert_rows(c, "player_game_stats", STAT_COLS, box.select(STAT_COLS))


def test_integer_crps_is_exact() -> None:
    g = np.repeat(np.arange(0, 199 // 3 + 1), 3)[:199]
    for y in (0.0, 3.0, 40.0, 100.0):
        # brute force: CRPS of the empirical pmf of g, sum_k (F(k) - 1[y <= k])^2 over the integers
        ks = np.arange(0, 200)
        f = np.array([(g <= k).mean() for k in ks])
        brute = float(np.sum((f - (y <= ks)) ** 2))
        assert S.integer_crps_grid(g, y) == pytest.approx(brute, abs=1e-9)
    q = np.vstack([g, g[::-1] * 0 + 5])
    assert S.crps_rows(q, np.array([3.0, 5.0])).tolist() == pytest.approx(
        [S.integer_crps_grid(q[0], 3.0), S.integer_crps_grid(q[1], 5.0)]
    )


def test_settle_scores_each_target_once_with_the_right_y(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    go(bcon, hb)
    assert S.settle_combo_shadow(bcon, TIPOFF)["combo_scored"] == 0  # not final
    _final(bcon, {1000, 1001, 1002, 1100})
    out = S.settle_combo_shadow(bcon, TIPOFF + timedelta(hours=3))
    assert out["combo_scored"] > 0 and out["combo_not_in_box"] == 0
    rows = {
        r[0]: r[1:]
        for r in bcon.execute(
            "SELECT target, played, y, crps_s, crps_recent, crps_indep, status FROM forward_combo_scores WHERE player_id = 1000"
        ).fetchall()
    }
    # 1000: pts 20, reb 4, ast 3, blk 2, stl 1 in the _box fixture
    assert {t: v[1] for t, v in rows.items()} == {
        "blk": 2.0,
        "stl": 1.0,
        "pra": 27.0,
        "pr": 24.0,
        "pa": 23.0,
        "ra": 7.0,
        "bs": 3.0,
    }
    assert (
        rows["blk"][4] is None
        and rows["pra"][4] is not None
        and all(v[5] == "scored" for v in rows.values())
    )
    s = bcon.execute(
        "SELECT CAST(s_pmf199 AS VARCHAR) FROM forward_combo_shadow WHERE player_id = 1000 AND target = 'pra'"
    ).fetchone()
    assert s is not None and rows["pra"][2] == pytest.approx(
        S.integer_crps_grid(json.loads(s[0]), 27.0)
    )
    dnp = bcon.execute(
        "SELECT played, y, crps_s FROM forward_combo_scores WHERE player_id = 1004 AND target = 'pra'"
    ).fetchone()
    assert dnp == (False, None, None)
    assert S.settle_combo_shadow(bcon, TIPOFF + timedelta(hours=4)) == {
        "combo_scored": 0,
        "combo_not_in_box": 0,
    }


def test_settle_uses_the_latest_pre_tip_row_not_a_later_one(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    go(bcon, hb, run_id="early")
    bcon.execute(
        "INSERT INTO forward_combo_shadow SELECT ?, 'late', game_id, player_id, target, tipoff_utc, tip_source, in_prod_log, n_prior, model_id, fit_through, r_id, base_id, "
        "s_pmf199, s_q19, indep_pmf199, recent_pmf199, direct_pmf199, recent_cal_pmf199, src FROM forward_combo_shadow WHERE run_id = 'early' AND player_id = 1000",
        [TIPOFF - timedelta(minutes=30)],
    )
    _final(bcon, {1000})
    S.settle_combo_shadow(bcon, TIPOFF + timedelta(hours=3))
    got = bcon.execute(
        "SELECT DISTINCT run_id FROM forward_combo_scores WHERE player_id = 1000"
    ).fetchall()
    assert got == [("early",)]


# --------------------------------------------------------------------------- wiring
def test_counting_start_needs_every_proof(bcon: duckdb.DuckDBPyConnection) -> None:
    store.ensure_tables(bcon)
    with pytest.raises(PermissionError):
        store.set_counting_start(bcon, "2026-10-20", TIPOFF)
    for p in store.PROOFS[:-1]:
        store.set_meta(bcon, f"proof_{p}", "pass", TIPOFF)
    with pytest.raises(PermissionError):
        store.set_counting_start(bcon, "2026-10-20", TIPOFF)
    store.set_meta(bcon, "proof_P4", "pass", TIPOFF)
    store.set_counting_start(bcon, "2026-10-20", TIPOFF)
    assert store.get_meta(bcon)["counting_start"] == "2026-10-20"


def test_primary_rows_are_identical_with_the_shadow_on(
    bcon: duckdb.DuckDBPyConnection, hb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process oracle: the same run with and without ``with_combo_shadow`` leaves both canonical tables identical; the shadow ran (rows > 0)."""
    from nba.props import forward
    from tests.daily.conftest import add_history
    from tests.fixtures.loader import build_fixture_db

    calls: list[object] = []

    def fake(*a: object, **k: object) -> object:
        calls.append(k.get("config"))
        return type("R", (), {"primary": _wide()})()

    monkeypatch.setattr(forward, "predict_slate_context", fake)
    monkeypatch.setattr(
        R, "ContextResidualConfig", lambda: CCFG
    )  # the fixture is too small for production's calibration window

    def go_daily(c: duckdb.DuckDBPyConnection, on: bool) -> pipeline.RunSummary:
        return run_daily(c, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True, skip_injury=True, with_combo_shadow=on, combo_heartbeat_dir=hb)  # fmt: skip

    seed_prod(bcon)
    off = go_daily(bcon, False)
    other = build_fixture_db(":memory:")
    add_history(other)
    other.execute("DELETE FROM player_game_stats WHERE game_id LIKE '00225%'")
    other.execute("DELETE FROM games WHERE game_id LIKE '00225%'")
    history(other)
    seed_prod(other)
    on = go_daily(other, True)
    a, b = canonical_frames(bcon), canonical_frames(other)
    assert a[0].equals(b[0]) and a[1].equals(b[1])
    assert off.n_rows_written == on.n_rows_written and not on.shadow_errors, on.shadow_errors
    assert "combo_shadow" in on.model_status and "'status': 'ok'" in on.model_status["combo_shadow"]
    n = other.execute("SELECT count(*) FROM forward_combo_shadow").fetchone()
    assert n is not None and n[0] > 0
    assert json.loads((hb / "combo_shadow.json").read_text())["rows_written"] == n[0]


def _daily_with_combo(c: duckdb.DuckDBPyConnection, hb: Path) -> pipeline.RunSummary:
    return run_daily(c, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True, skip_injury=True, with_combo_shadow=True, combo_heartbeat_dir=hb)  # fmt: skip


def test_shadow_failure_is_recorded_and_never_raises(
    bcon: duckdb.DuckDBPyConnection, hb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T1: fails if ``_combo_shadow`` is a no-op (the step must leave a status AND, when it raises, an ALERTS line) and the primary rows are still written."""
    monkeypatch.setattr(pipeline, "_wide_roster_frame", lambda *a, **k: ({}, {}, _wide()))
    ok = _daily_with_combo(
        bcon, hb
    )  # no production context rows seeded: the step runs and records a skip
    assert ok.n_rows_written > 0 and not ok.shadow_errors, ok.shadow_errors
    assert "'status': 'skipped'" in ok.model_status["combo_shadow"]
    assert "combo_shadow_settle" in ok.model_status
    assert json.loads((hb / "combo_shadow.json").read_text())["status"] == "skipped"

    def boom(*a: object, **k: object) -> object:
        raise RuntimeError("fit exploded")

    monkeypatch.setattr(R, "run_combo_shadow", boom)
    bad = _daily_with_combo(bcon, hb)
    assert [
        e for e in bad.shadow_errors if e.startswith("combo shadow: RuntimeError: fit exploded")
    ]
    assert (
        "combo_shadow_settle" not in bad.model_status
    )  # the failure is the step's own; the primary rows were written before it


def test_cli_flag_is_off_by_default_and_settle_is_registered() -> None:
    from nba.daily.__main__ import SHADOW_SETTLERS, _parser

    p = _parser()
    assert p.parse_args(["run", "--date", "2026-10-28"]).log_combo_shadow is False
    assert (
        p.parse_args(["run", "--date", "2026-10-28", "--log-combo-shadow"]).log_combo_shadow is True
    )
    assert (
        "combo",
        "nba.daily.combo_shadow.settle",
        "settle_combo_shadow",
        "forward_combo_shadow",
    ) in SHADOW_SETTLERS


def test_report_section_appears_once_rows_exist(bcon: duckdb.DuckDBPyConnection, hb: Path) -> None:
    from nba.daily.report import _combo_shadow_section

    assert _combo_shadow_section(bcon) == []  # no table yet: no briefing line
    seed_prod(bcon)
    go(bcon, hb)
    text = "\n".join(_combo_shadow_section(bcon))
    assert "COMBO_STOCKS shadow: counts and gates only" in text and "NOT SET" in text
    assert "crps" not in text.lower() and "delta" not in text.lower().replace(
        "no delta is published", ""
    )


def test_replay_flag_routes_the_heartbeat_into_the_replay_dir() -> None:
    from nba.daily.replay_season import _config, _parser

    a = _parser().parse_args(
        [
            "--season",
            "2025",
            "--db",
            "x.duckdb",
            "--out-dir",
            "/tmp/o",
            "--combo-shadow",
            "--lineups-db",
            "l",
        ]
    )
    cfg = _config(a)
    assert cfg.combo_shadow is True and cfg.out_dir == Path("/tmp/o")
    assert (
        _config(_parser().parse_args(["--season", "2025", "--db", "x.duckdb"])).combo_shadow
        is False
    )


def test_the_layering_of_the_writer() -> None:
    """The live writer imports nba.props / nba.daily only (rule 'What the steward builds' 1): never nba.eval, nba.sim, research."""
    import ast

    for f in (ROOT / "nba/daily/combo_shadow").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for nme in names:
                assert not nme.startswith(("nba.eval", "nba.sim", "research")), (f.name, nme)


# --------------------------------------------------------------------------- review fixes S1, M5, M6
def test_budget_skip_when_the_earliest_tip_is_within_35_minutes(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    near = go(bcon, hb, made_at=TIPOFF - timedelta(minutes=30))
    assert near["status"] == "skipped" and near["detail"] == "budget"
    assert bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone() == (0,)
    f = json.loads((hb / "combo_shadow.json").read_text())
    assert f["status"] == "skipped" and f["consecutive_bad"] == 0 and f["rc"] == 0
    assert bcon.execute(
        "SELECT status, rows_written, detail FROM forward_combo_heartbeat"
    ).fetchall() == [("skipped", 0, "budget")]
    ok = go(
        bcon, hb, made_at=TIPOFF - timedelta(minutes=36), run_id="r2"
    )  # control: just outside the window
    assert ok["status"] == "ok" and ok["written"] > 0


def test_budget_skip_when_the_step_passes_its_wall_time_between_chunks(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    ticks = iter(range(10_000))
    info = go(
        bcon, hb, budget_s=2.5, clock=lambda: float(next(ticks))
    )  # one unit of "time" per clock read
    assert (
        info["status"] == "skipped"
        and info["detail"] == "budget"
        and "budget" in info["budget_reason"]
    )
    assert bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone() == (
        0,
    )  # nothing was written
    f = json.loads((hb / "combo_shadow.json").read_text())
    assert f["status"] == "skipped" and f["consecutive_bad"] == 0
    assert go(bcon, hb, run_id="r2")["written"] > 0  # the same run with the real budget writes


def test_the_budget_checks_run_between_sampler_batches_and_do_not_change_the_grids() -> None:
    cfg = CFG.load_config()
    ref = np.load(ROOT / "reports/prereg_combo_stocks_shadow/p1_reference.npz")
    cdfs = {s: ref[f"cdf_{s}"] for s in C.STAT_ORDER}  # 200 rows -> 3 batches of 96
    calls: list[int] = []
    s_b, i_b = R.joint_from_cdfs(cdfs, cfg, check=lambda: calls.append(1))
    assert len(calls) == 3
    for c in C.COMBOS:
        assert np.array_equal(s_b[c], ref[f"expected_{c}"]) and np.array_equal(
            i_b[c], ref[f"expected_indep_{c}"]
        )

    def stop() -> None:
        if len(calls) >= 4:
            raise R.BudgetExceeded("budget")
        calls.append(1)

    with pytest.raises(R.BudgetExceeded):
        R.joint_from_cdfs(cdfs, cfg, check=stop)


def test_the_tip_source_is_passed_through_and_a_moved_tip_is_news(
    bcon: duckdb.DuckDBPyConnection, hb: Path
) -> None:
    seed_prod(bcon)
    go(bcon, hb, tip_source="proxy")
    assert bcon.execute("SELECT DISTINCT tip_source FROM forward_combo_shadow").fetchall() == [
        ("proxy",)
    ]  # M5
    n = bcon.execute("SELECT count(*) FROM forward_combo_shadow").fetchone()
    assert n is not None
    moved = [SlateGame(GID, T1, T2, TIPOFF + timedelta(minutes=30))]
    info = R.run_combo_shadow(
        bcon, RUN_DATE, moved, MADE, "r2", _wide(), {}, ELO, hb_dir=hb, ccfg=CCFG
    )
    assert (
        info["written"] == n[0] and info["deduped"] == 0
    )  # M6: identical grids, different tip -> a new row
