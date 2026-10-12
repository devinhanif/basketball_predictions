# ruff: noqa: E501, SIM300
"""RECENT_ROLE_FULL shadow: construction equals production, planted-future guards, dedup, tip refusal,
settle (conditional and joint), identical primary rows."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.daily import pipeline
from nba.daily.canonical_dump import canonical_frames
from nba.daily.full_shadow import model as M
from nba.daily.full_shadow.run import build_full_frame, run_full_shadow
from nba.daily.full_shadow.settle import mixture_crps, settle_full_shadow
from nba.daily.pipeline import run_daily
from nba.daily.pplay_shadow.features import R_COLS, SlateGame
from nba.daily.predict import load_elo_params
from nba.ingest.cache import insert_rows
from nba.props.context_residual import (
    COMMON_FEATURES,
    PROP_STATS,
    ContextResidualConfig,
    fit_for_date,
    predict_rows,
    stat_feature_names,
    to_integer_support,
)
from nba.props.forward import QUANTILE_TAUS
from tests.daily.conftest import RUN_DATE, STAT_COLS, T1, T2, T3, T4, TIPOFF, _box, slate_games
from tests.daily.test_pplay_shadow import GID, _wide

ELO = load_elo_params()
MADE = TIPOFF - timedelta(minutes=120)
CFG = ContextResidualConfig(n_estimators=25, min_child_samples=20, min_cal_rows=300, n_jobs=1)
STATS = ("pts", "reb", "ast", "fg3m")


def _slate() -> list[SlateGame]:
    return [SlateGame(GID, T1, T2, TIPOFF)]


def big_history(con: duckdb.DuckDBPyConnection, n: int = 90, seed: int = 3) -> None:
    """n completed games per pair (T1-T2, T3-T4), one every 2 days; 8 players a team with random minutes,
    about 12% of the rotation games short (< half the usual minutes). Players 1000-1005 / 1100-1105 as _wide()."""
    rng = np.random.default_rng(seed)
    games, stats = [], []
    for i in range(n):
        d = date(2026, 1, 1) + timedelta(days=2 * i)
        for k, (h, a, hb) in enumerate([(T1, T2, 1000), (T3, T4, 3000)]):
            gid = f"00225{k}{i:04d}"
            games.append(
                {"game_id": gid, "game_date": d, "season": 2025, "home_team": h,
                 "away_team": a, "home_pts": 100 + i % 7, "away_pts": 98}
            )  # fmt: skip
            for team, base in ((h, hb), (a, hb + 100)):
                for j in range(6):
                    usual = 34.0 - 4.0 * j
                    short = rng.random() < 0.12
                    mins = (
                        float(rng.uniform(1, 0.4 * usual))
                        if short
                        else float(usual + rng.normal(0, 2))
                    )
                    mins = max(mins, 0.0) if j < 5 or rng.random() < 0.5 else 0.0
                    rate = 0.5 + 0.1 * (5 - j) / 5
                    stats.append(
                        {
                            "game_id": gid, "player_id": base + j, "team_id": team, "minutes": mins,
                            "pts": int(rng.poisson(rate * mins * 0.6)), "reb": int(rng.poisson(0.15 * mins)),
                            "ast": int(rng.poisson(0.1 * mins)), "fg3m": int(rng.poisson(0.05 * mins)),
                            "stl": 0, "blk": 0, "tov": 1, "starter": j < 3, "fgm": 3, "fga": 8,
                            "fg3a": 3, "ftm": 1, "fta": 2, "oreb": 1, "dreb": 3, "pf": 2,
                        }
                    )  # fmt: skip
    g = pl.DataFrame(games)
    insert_rows(con, "games", g.columns, g)
    s = pl.DataFrame(stats)
    insert_rows(con, "player_game_stats", STAT_COLS, s.select(STAT_COLS))


@pytest.fixture
def bcon(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    # `con` holds the 12-game fixture history; replace it with the larger random one
    con.execute("DELETE FROM player_game_stats WHERE game_id LIKE '00225%'")
    con.execute("DELETE FROM games WHERE game_id LIKE '00225%'")
    big_history(con)
    return con


def _run(
    c: duckdb.DuckDBPyConnection, made_at: datetime = MADE, run_id: str = "r1", cache: Any = None
) -> dict[str, Any]:
    return run_full_shadow(
        c, RUN_DATE, _slate(), made_at, run_id, _wide((1099,)), {}, {(GID, 1000)}, ELO,
        cache_root=cache, cfg=CFG,
    )  # fmt: skip


def _rows(c: duckdb.DuckDBPyConnection, where: str = "TRUE") -> list[tuple[Any, ...]]:
    return c.execute(
        "SELECT player_id, stat, CAST(q_grid AS VARCHAR), pi_full, mean_full, features_sha, run_id "
        f"FROM forward_full_shadow WHERE {where} ORDER BY run_id, player_id, stat"
    ).fetchall()


def test_feature_lists_are_the_frozen_full_set() -> None:
    assert M.PI_FEATURES == (*COMMON_FEATURES, *R_COLS)
    assert R_COLS == ("wl_min_last", "wl_mean_min_l3", "wl_min_3d", "wl_min_7d", "wl_mean_min_l5")
    for s in STATS:
        assert M.head_names(s) == [*stat_feature_names(s), *R_COLS]
        assert len(set(M.head_names(s))) == len(M.head_names(s))


def test_pinned_to_the_research_harness() -> None:
    from research.eval import recent_role_full_eval as RF

    assert tuple(RF.R_COLS) == R_COLS
    for s in STATS:
        assert RF.heads_names(s, "R") == M.head_names(s)
    assert list(RF.ARMS_PI["R"][0]) == list(M.PI_FEATURES)


def test_without_the_five_columns_it_is_production_mixture_bit_identical(
    bcon: duckdb.DuckDBPyConnection,
) -> None:
    """The equivalence proof: FullModel with the production feature lists == ContextResidualModel(lower_tail=mixture)."""
    cfg = M.full_config(CFG)
    feats = build_full_frame(bcon, RUN_DATE, _slate(), _wide(), {}, ELO, cfg)
    prod = fit_for_date(feats, RUN_DATE, cfg)  # production mixture, stats = all four
    short = feats.filter(pl.col("game_date") < RUN_DATE)
    assert prod["pts"].lt_ is not None and prod["pts"].lt_["pi_model"] is not None
    assert int(short["minutes"].is_null().sum()) == 0
    # same construction through FullModel with the production lists and the production classifier input
    orig_head, orig_pi = M.head_names, M.PI_FEATURES
    try:
        M.head_names = stat_feature_names
        M.PI_FEATURES = tuple(COMMON_FEATURES)
        full = M.fit(feats, RUN_DATE, cfg)
        for m in full.values():
            assert isinstance(m, M.FullModel)
    finally:
        M.head_names, M.PI_FEATURES = orig_head, orig_pi
    slate = feats.filter(pl.col("game_id") == GID)
    taus = np.array(QUANTILE_TAUS)
    for s in STATS:
        e1, m1, q1 = predict_rows(prod, slate, s, taus)
        e2, m2, q2 = predict_rows(full, slate, s, taus)
        assert e1.height > 0 and e1.equals(e2)
        assert np.array_equal(m1, m2) and np.array_equal(q1, q2)


def test_writes_integer_grids_for_context_players_only(bcon: duckdb.DuckDBPyConnection) -> None:
    info = _run(bcon)
    rows = _rows(bcon)
    assert info["written"] == len(rows) > 0
    got = {(r[0], r[1]) for r in rows}
    assert all((pid, s) in got for pid in (1000, 1001, 1100) for s in STATS)
    assert not any(
        r[0] == 1099 for r in rows
    )  # no prior games: production routes him to the recency fallback
    for _pid, _s, qg, pi, mean, sha, _rid in rows:
        q = json.loads(qg)
        assert len(q) == 19 and q == sorted(q) and all(float(v).is_integer() and v >= 0 for v in q)
        assert 0.0 <= pi <= 0.5 and mean >= 0 and len(sha) == 16
    meta = bcon.execute(
        "SELECT DISTINCT model_version, train_through, features_through, n_train, in_prod_log "
        "FROM forward_full_shadow WHERE player_id = 1000"
    ).fetchall()
    assert meta[0][0] == "full_v1" and meta[0][1] < RUN_DATE and meta[0][2] == RUN_DATE
    assert meta[0][4] is True


def test_the_five_columns_reach_the_model(bcon: duckdb.DuckDBPyConnection) -> None:
    cfg = M.full_config(CFG)
    feats = build_full_frame(bcon, RUN_DATE, _slate(), _wide(), {}, ELO, cfg)
    for c in R_COLS:
        assert feats[c].null_count() < feats.height  # present
    row = feats.filter((pl.col("game_id") == GID) & (pl.col("player_id") == 1000)).row(
        0, named=True
    )
    last = bcon.execute(
        "SELECT s.minutes FROM player_game_stats s JOIN games g USING (game_id) "
        "WHERE s.player_id = 1000 AND s.minutes > 0 ORDER BY g.game_date DESC LIMIT 5"
    ).fetchall()
    mins = [float(m[0]) for m in last]
    assert row["wl_min_last"] == pytest.approx(mins[0])
    assert row["wl_mean_min_l5"] == pytest.approx(np.mean(mins))
    assert row["wl_mean_min_l3"] == pytest.approx(np.mean(mins[:3]))


def test_planted_future_changes_nothing_and_a_past_edit_does(
    bcon: duckdb.DuckDBPyConnection,
) -> None:
    _run(bcon, run_id="a")
    base = {(r[0], r[1]): (r[2], r[5]) for r in _rows(bcon)}
    later = RUN_DATE + timedelta(days=3)
    bcon.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES ('{GID}x', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 90, 99), "
        f"('0022600099', DATE '{later}', 2026, {T1}, {T3}, 80, 70)"
    )
    box = pl.DataFrame(_box(f"{GID}x", T1, 1000, 40) + _box("0022600099", T1, 1000, 30))
    box = box.with_columns(
        pl.when(pl.col("player_id") == 1000)
        .then(48.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    insert_rows(bcon, "player_game_stats", STAT_COLS, box.select(STAT_COLS))
    bcon.execute(
        "INSERT INTO player_availability (player_id, as_of, game_id, status, source) VALUES "
        f"(1001, TIMESTAMP '{RUN_DATE} 23:00:00', '{GID}', 'out', 'nba_official_report')"
    )
    info = _run(bcon, run_id="b")
    assert info["written"] == 0 and info["deduped"] == len(base)  # every row identical to run a
    # control: edit a PRIOR game's minutes for player 1000 -> that player's fingerprints must change
    bcon.execute(
        "UPDATE player_game_stats SET minutes = 3.0 WHERE game_id = '002250"
        "0089' AND player_id = 1000"
    )
    _run(bcon, run_id="c")
    changed = _rows(bcon, "run_id = 'c' AND player_id = 1000")
    assert changed and all(r[5] != base[(r[0], r[1])][1] for r in changed)


def test_report_out_before_the_run_changes_features_report_after_does_not(
    bcon: duckdb.DuckDBPyConnection,
) -> None:
    _run(bcon, run_id="a")
    n0 = len(_rows(bcon))
    wide = _wide()
    info = run_full_shadow(
        bcon, RUN_DATE, _slate(), MADE, "b", wide, {GID: {1001}}, {(GID, 1000)}, ELO, cfg=CFG
    )
    assert (
        info["written"] > 0
    )  # a usable report marks 1001 OUT: has_report / n_out_rot move for teammates
    assert len(_rows(bcon)) > n0


def test_after_tip_is_refused_and_dedup_skips_repeats(bcon: duckdb.DuckDBPyConnection) -> None:
    info = _run(bcon, made_at=TIPOFF + timedelta(minutes=1))
    assert info["written"] == 0 and info["refused_after_tip"] == info["rows"] > 0
    assert _rows(bcon) == []
    first = _run(bcon, run_id="x")["written"]
    again = _run(bcon, run_id="y")
    assert first > 0 and again["written"] == 0 and again["deduped"] == first


def test_same_date_cache_reuses_the_fit_and_matches(
    bcon: duckdb.DuckDBPyConnection, tmp_path: Any
) -> None:
    a = _run(bcon, run_id="a", cache=tmp_path)
    assert a["cache_hit"] is False
    b = _run(bcon, run_id="b", cache=tmp_path)
    assert b["cache_hit"] is True and b["written"] == 0  # cached models give the same rows
    assert (tmp_path / f"{RUN_DATE.isoformat()}_full" / "models.pkl").exists()


def test_mixture_crps_is_exact() -> None:
    g = [float(i) for i in range(19)]
    # p = 1: plain empirical CRPS of the atoms; p = 0: all mass at zero -> |y|
    assert mixture_crps(g, 0.0, 7.0) == pytest.approx(7.0)
    x = to_integer_support(np.array(g, dtype=float))
    for p, y in ((1.0, 4.0), (0.6, 0.0), (0.6, 9.0)):
        atoms = np.concatenate([[0.0], x])
        w = np.concatenate([[1 - p], np.full(19, p / 19)])
        grid = np.linspace(-1, 30, 62001)
        cdf = np.array([w[atoms <= t].sum() for t in grid[::20]])
        step = (grid[1] - grid[0]) * 20
        brute = float(np.sum((cdf - (grid[::20] >= y)) ** 2) * step)
        assert mixture_crps(g, p, y) == pytest.approx(brute, abs=0.02)
    assert mixture_crps(g, 0.5, 3.0) >= 0.0


def _final(c: duckdb.DuckDBPyConnection, play: set[int]) -> None:
    c.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        f"VALUES ('{GID}', DATE '{RUN_DATE}', 2026, {T1}, {T2}, 101, 99)"
    )
    box = pl.DataFrame(_box(GID, T1, 1000, 20) + _box(GID, T2, 1100, 18))
    box = box.with_columns(
        pl.when(pl.col("player_id").is_in(list(play))).then(30.0).otherwise(0.0).alias("minutes")
    )
    insert_rows(c, "player_game_stats", STAT_COLS, box.select(STAT_COLS))


def test_settle_scores_conditional_and_joint_once(bcon: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.pplay_shadow.run import run_shadow

    _run(bcon)
    run_shadow(bcon, RUN_DATE, _slate(), MADE, "r1", _wide(), {(GID, 1000)}, ELO, min_train=50)
    assert settle_full_shadow(bcon, TIPOFF)["full_scored"] == 0  # game not final
    _final(bcon, {1000, 1001, 1002, 1100})
    out = settle_full_shadow(bcon, TIPOFF + timedelta(hours=3))
    assert out["full_scored"] > 0 and out["full_not_in_box"] == 0
    row = bcon.execute(
        "SELECT played, y, crps_prod, crps_full, p_play_prod, p_play_r, y_uncond, crps_joint_prod, "
        "crps_joint_full, crps_joint_rprod, status FROM forward_full_scores "
        "WHERE player_id = 1000 AND stat = 'pts'"
    ).fetchone()
    assert row is not None and row[0] is True and row[1] == 20.0 and row[10] == "scored"
    assert row[2] > 0 and row[3] > 0 and row[5] is not None and row[8] is not None
    dnp = bcon.execute(
        "SELECT played, y, crps_prod, crps_full, y_uncond, crps_joint_prod, crps_joint_full "
        "FROM forward_full_scores WHERE player_id = 1004 AND stat = 'pts'"
    ).fetchone()
    assert (
        dnp is not None and dnp[0] is False and dnp[1] is None and dnp[2] is None and dnp[3] is None
    )
    assert (
        dnp[4] == 0.0 and dnp[5] > 0 and dnp[6] > 0
    )  # a listed DNP still scores the joint forecast
    again = settle_full_shadow(bcon, TIPOFF + timedelta(hours=4))
    assert again == {"full_scored": 0, "full_not_in_box": 0}  # idempotent


def test_settle_uses_the_latest_pre_tip_row_not_a_later_one(
    bcon: duckdb.DuckDBPyConnection,
) -> None:
    _run(bcon, run_id="early")
    # a row stamped inside the last hour before tip must not be scored (T-60 rule)
    late = bcon.execute(
        "SELECT * FROM forward_full_shadow WHERE run_id = 'early' AND player_id = 1000 AND stat = 'pts'"
    ).fetchone()
    assert late is not None
    bcon.execute(
        "INSERT INTO forward_full_shadow SELECT 'late', ?, game_id, tipoff, player_id, team_id, stat, "
        "model_version, '[99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99]', 99.0, pi_full, "
        "q_grid_prod, mean_prod, p_play_prod, in_prod_log, train_through, n_train, features_through, "
        "feature_set_sha, features_sha FROM forward_full_shadow WHERE run_id = 'early' "
        "AND player_id = 1000 AND stat = 'pts'",
        [TIPOFF - timedelta(minutes=30)],
    )
    _final(bcon, {1000})
    settle_full_shadow(bcon, TIPOFF + timedelta(hours=3))
    got = bcon.execute(
        "SELECT run_id FROM forward_full_scores WHERE player_id = 1000 AND stat = 'pts'"
    ).fetchall()
    assert got == [("early",)]


def test_primary_rows_are_byte_identical_with_the_shadow_on(
    bcon: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.props import forward
    from tests.daily.conftest import add_history
    from tests.fixtures.loader import build_fixture_db

    calls: list[object] = []

    def fake(*a: object, **k: object) -> object:
        calls.append(k.get("config"))
        return type("R", (), {"primary": _wide()})()

    monkeypatch.setattr(forward, "predict_slate_context", fake)
    monkeypatch.setattr(
        M,
        "full_config",
        lambda cfg=None: replace(CFG, lower_tail="mixture", lower_tail_stats=PROP_STATS),
    )

    def go(
        c: duckdb.DuckDBPyConnection, pplay: bool = False, full: bool = False
    ) -> pipeline.RunSummary:
        return run_daily(
            c, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True,
            skip_injury=True, with_pplay_shadow=pplay, with_full_shadow=full,
        )  # fmt: skip

    off = go(bcon)
    other = build_fixture_db(":memory:")
    add_history(other)
    other.execute("DELETE FROM player_game_stats WHERE game_id LIKE '00225%'")
    other.execute("DELETE FROM games WHERE game_id LIKE '00225%'")
    big_history(other)
    on = go(other, pplay=True, full=True)
    a, b = canonical_frames(bcon), canonical_frames(other)
    assert a[0].equals(b[0]) and a[1].equals(b[1])
    assert off.n_rows_written == on.n_rows_written
    assert not on.shadow_errors, on.shadow_errors
    assert calls and all(c is pipeline._WIDE_CONFIG for c in calls)
    assert len(calls) == 1  # the wide frame is computed once for both shadows
    n = other.execute("SELECT count(*) FROM forward_full_shadow").fetchone()
    assert n is not None and n[0] > 0
    assert "full_shadow" in on.model_status


def test_shadow_failure_is_recorded_and_never_raises(con: duckdb.DuckDBPyConnection) -> None:
    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda s: slate_games(), now=MADE, skip_ingest=True,
        skip_injury=True, with_full_shadow=True,
    )  # fmt: skip  # the 12-game fixture cannot fit the context model
    assert s.n_rows_written > 0
    assert any(e.startswith("full shadow:") for e in s.shadow_errors)


def test_cli_flag_is_off_by_default() -> None:
    from nba.daily.__main__ import _parser

    p = _parser()
    assert p.parse_args(["run", "--date", "2026-10-28"]).log_full_shadow is False
    assert (
        p.parse_args(["run", "--date", "2026-10-28", "--log-full-shadow"]).log_full_shadow is True
    )
    assert T3 != T4


def test_report_section_appears_once_rows_exist(bcon: duckdb.DuckDBPyConnection) -> None:
    from nba.daily.pplay_shadow.run import run_shadow
    from nba.daily.report import _full_shadow_section

    assert _full_shadow_section(bcon) == []  # no table yet: no briefing line
    _run(bcon)
    run_shadow(bcon, RUN_DATE, _slate(), MADE, "r1", _wide(), {(GID, 1000)}, ELO, min_train=50)
    assert "none settled yet" in _full_shadow_section(bcon)[-1]
    _final(bcon, {1000, 1001, 1002, 1100})
    settle_full_shadow(bcon, TIPOFF + timedelta(hours=3))
    text = "\n".join(_full_shadow_section(bcon))
    assert "pts conditional on playing: FULL minus production CRPS" in text
    assert "R x FULL minus production" in text
