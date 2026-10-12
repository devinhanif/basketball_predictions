# ruff: noqa: E501
"""Behaviour tests for ``research.eval.confusion_report`` on a small synthetic world.

The module reads production-sized files; here every path constant is redirected to a tmp tree
holding ~70 games per season, so the pure helpers, the three sections and the markdown renderer
run end to end and are checked against independent numpy computations.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
from scipy import stats as sps

from research.eval import confusion_report as cr

# ------------------------------------------------------------------------------ pure helpers


def test_ci_percentiles_and_all_nan_guard() -> None:
    lo, hi = cr.ci(np.arange(101, dtype=float))
    assert lo == pytest.approx(2.5) and hi == pytest.approx(97.5)
    lo, hi = cr.ci(np.array([np.nan, np.inf]))
    assert np.isnan(lo) and np.isnan(hi)
    # non-finite draws are dropped, not propagated
    lo, hi = cr.ci(np.array([np.nan, 1.0, 1.0, 1.0]))
    assert (lo, hi) == (1.0, 1.0)


def test_boot_ratios_point_estimate_and_constant_draws() -> None:
    codes = np.array([1, 1, 2, 2, 3])
    num = np.array([1.0, 0.0, 1.0, 1.0, 0.0])
    den = np.ones(5)
    point, draws = cr.boot_ratios(codes, [(num, den)], b=300, seed=3)
    assert point[0] == pytest.approx(3 / 5)
    assert draws.shape == (300, 1)
    assert np.all((draws >= 0) & (draws <= 1))
    # an all-ones numerator gives a degenerate bootstrap at exactly 1
    p2, d2 = cr.boot_ratios(codes, [(den, den)], b=50, seed=3)
    assert p2[0] == 1.0 and np.all(d2 == 1.0)


def test_boot_ratios_is_seeded_and_clusters_by_game() -> None:
    codes = np.repeat(np.arange(20), 5)
    num = (np.arange(100) % 3 == 0).astype(float)
    a = cr.boot_ratios(codes, [(num, np.ones(100))], b=100, seed=11)[1]
    b = cr.boot_ratios(codes, [(num, np.ones(100))], b=100, seed=11)[1]
    c = cr.boot_ratios(codes, [(num, np.ones(100))], b=100, seed=12)[1]
    assert np.array_equal(a, b) and not np.array_equal(a, c)
    # cluster resampling is wider than an i.i.d. bootstrap when rows within a game are identical
    g_codes = np.repeat(np.arange(20), 10)
    g_hit = np.repeat(np.tile([1.0, 0.0], 10), 10)
    clustered = cr.rate(g_codes, g_hit)
    assert clustered["rate"] == pytest.approx(0.5)
    assert clustered["games"] == 20 and clustered["n"] == 200
    assert clustered["hi"] - clustered["lo"] > 0.25  # 20 games, not 200 rows


def test_rate_with_mask_and_empty() -> None:
    codes = np.array([1, 1, 2, 2, 3, 3])
    hit = np.array([1, 1, 1, 0, 0, 0])
    r = cr.rate(codes, hit, np.array([True, True, True, True, False, False]))
    assert r["n"] == 4 and r["rate"] == pytest.approx(0.75) and r["games"] == 2
    assert r["lo"] <= r["rate"] <= r["hi"]
    empty = cr.rate(codes, hit, np.zeros(6, dtype=bool))
    assert empty == {"rate": None, "lo": None, "hi": None, "n": 0}


def test_diff_rate_is_paired_difference() -> None:
    codes = np.arange(40)
    a = np.ones(40, dtype=bool)
    b = np.tile([True, False], 20)
    d = cr.diff_rate(codes, a, b)
    assert d["diff"] == pytest.approx(0.5) and d["n"] == 40
    assert d["lo"] > 0  # every pair has a >= b, so the CI excludes zero
    masked = cr.diff_rate(codes, a, b, np.arange(40) < 10)
    assert masked["n"] == 10 and masked["diff"] == pytest.approx(0.5)
    assert cr.diff_rate(codes, a, b, np.zeros(40, dtype=bool))["diff"] is None


def test_fmt_and_fmt_diff_strings() -> None:
    assert cr.fmt(None) == "n/a"
    assert cr.fmt({"rate": None}) == "n/a"
    assert cr.fmt({"rate": 0.5, "lo": 0.4, "hi": 0.6}) == "50.0% [40.0, 60.0]"
    assert cr.fmt({"rate": 0.5, "lo": 0.4, "hi": 0.6}, pct=False) == "0.5% [0.4, 0.6]"
    assert cr.fmt_diff({"diff": None}) == "n/a"
    assert cr.fmt_diff({"diff": 0.012, "lo": -0.01, "hi": 0.03}) == "+1.2 pts [-1.0, +3.0]"


def test_binary_matrix_counts_and_derived_rates() -> None:
    codes = np.arange(8)
    pred = np.array([1, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
    over = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    m = cr.binary_matrix(codes, pred, over)
    assert (m["tp"], m["fp"], m["fn"], m["tn"], m["n"]) == (2, 2, 1, 3, 8)
    assert m["accuracy"]["rate"] == pytest.approx(5 / 8)
    assert m["precision_over"]["rate"] == pytest.approx(2 / 4)
    assert m["recall_over"]["rate"] == pytest.approx(2 / 3)
    assert m["pred_over_rate"]["rate"] == pytest.approx(4 / 8)
    assert m["base_over"]["rate"] == pytest.approx(3 / 8)
    masked = cr.binary_matrix(codes, pred, over, np.arange(8) < 3)
    assert (masked["tp"], masked["fp"], masked["n"]) == (2, 1, 3)
    empty = cr.binary_matrix(codes, pred, over, np.zeros(8, dtype=bool))
    assert empty == {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "n": 0}


def test_md_matrix_layout() -> None:
    m = {"tp": 1, "fp": 2, "fn": 3, "tn": 4}
    out = cr.md_matrix(m, ("P1", "P2"), ("A1", "A2"))
    assert "| P1 | 1 | 2 |" in out and "| P2 | 3 | 4 |" in out and "| A1 | A2 |" in out
    assert cr.mat_line(m) == "TP 1 / FP 2 / FN 3 / TN 4"


def test_recency_pge_is_a_valid_survival_vector() -> None:
    mean, sd = np.array([15.0, 25.0]), np.array([6.0, 7.0])
    p = cr.recency_pge("pts", mean, sd, 60)
    assert p.shape == (2, 60)
    assert np.all(np.diff(p, axis=1) <= 1e-12)  # P(Y>=k) is non-increasing
    # rounded Gaussian: P(Y >= 15) for mean 15 is about P(Z > -0.5/6) ~ 0.53
    assert p[0, 14] == pytest.approx(1 - sps.norm.cdf(-0.5 / 6.0))
    assert p[1, 14] > p[0, 14]  # higher mean => heavier tail
    for stat in ("reb", "ast", "fg3m"):
        q = cr.recency_pge(stat, np.array([5.0]), np.array([2.0]), 30)
        assert np.all((q >= 0) & (q <= 1)) and np.all(np.diff(q, axis=1) <= 1e-12)
        assert q[0, 0] > 0.9  # P(Y>=1) for mean 5
    # under-dispersed sd falls back to a near-Poisson variance, never NaN
    under = cr.recency_pge("reb", np.array([6.0]), np.array([1.0]), 20)
    assert np.all(np.isfinite(under))
    pois = sps.poisson.sf(np.arange(0, 20), 6.0)
    assert np.allclose(under[0], pois, atol=0.02)


def test_phi_logit_and_american_prices() -> None:
    assert cr.phi(np.array([0.0]))[0] == pytest.approx(0.5)
    assert cr.logit(np.array([0.5]))[0] == pytest.approx(0.0)
    assert np.isfinite(cr.logit(np.array([0.0, 1.0]))).all()  # clipped
    assert cr.amer_prob(np.array([-110.0]))[0] == pytest.approx(110 / 210)
    assert cr.amer_prob(np.array([150.0]))[0] == pytest.approx(100 / 250)
    assert cr.devig2(np.array([-110.0]), np.array([-110.0]))[0] == pytest.approx(0.5)
    p = cr.devig2(np.array([-200.0]), np.array([170.0]))[0]
    assert 0.6 < p < 0.7 and p == pytest.approx((200 / 300) / (200 / 300 + 100 / 270))


def test_ols_fit_recovers_line_and_residual_sd() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = 2.0 + 3.0 * x + rng.normal(scale=0.5, size=400)
    a, b, sig = cr.ols_fit(x, y)
    assert a == pytest.approx(2.0, abs=0.1) and b == pytest.approx(3.0, abs=0.1)
    assert sig == pytest.approx(0.5, abs=0.07)


def test_team_totals_baseline_waits_for_prior_games_and_is_asof() -> None:
    rows = []
    for i in range(30):  # two teams playing each other repeatedly: 100-90 then 110-100
        pts = (100, 90) if i < 20 else (110, 100)
        rows.append(
            {
                "game_id": i,
                "game_date": dt.date(2024, 1, 1) + dt.timedelta(days=i),
                "home_team": 1 if i % 2 == 0 else 2,
                "away_team": 2 if i % 2 == 0 else 1,
                "home_pts": pts[0] if i % 2 == 0 else pts[1],
                "away_pts": pts[1] if i % 2 == 0 else pts[0],
            }
        )
    out = cr.team_totals_baseline(pl.DataFrame(rows)).sort("game_id")
    th = out["total_hat"].to_list()
    assert all(v is None for v in th[: cr.MIN_PRIOR])
    assert th[cr.MIN_PRIOR] == pytest.approx(190.0)  # only the 100/90 games are visible
    # game 20 is the first 110/100 game: its baseline must not include it
    assert th[20] == pytest.approx(190.0)
    assert th[29] > 190.0  # later games see the higher-scoring history


def test_wait_lock_returns_when_free_and_refuses_to_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "heavy.lock"
    monkeypatch.setattr(cr, "LOCK", lock)
    cr.wait_lock(retry_s=0.0, max_wait_s=1.0)  # no lock: immediate
    lock.mkdir()
    with pytest.raises(RuntimeError, match="not breaking it"):
        cr.wait_lock(retry_s=0.0, max_wait_s=0.0)
    assert lock.exists()  # never removed


# ------------------------------------------------------------------------------ synthetic world

N_TEAMS = 8
GAMES_PER_SEASON = 72
SEASONS = (2022, 2023, 2024)
PROP_MEANS = {"pts": 15.0, "reb": 5.0, "ast": 4.0, "fg3m": 1.6}
WIDTH = 40


def _build_world(root: Path) -> dict[str, Any]:
    rng = np.random.default_rng(2026)
    strength = rng.normal(scale=0.8, size=N_TEAMS + 1)
    games, poss, pbp_files = [], [], {}
    oof, gm_rows, odds = [], [], []
    gid = 0
    for si, season in enumerate(SEASONS):
        for i in range(GAMES_PER_SEASON):
            gid += 1
            h = int(rng.integers(1, N_TEAMS + 1))
            a = int(rng.choice([t for t in range(1, N_TEAMS + 1) if t != h]))
            x = strength[h] - strength[a] + 0.3
            hq = np.round(27 + 1.5 * x + rng.normal(scale=4, size=4)).astype(int)
            aq = np.round(27 - 1.5 * x + rng.normal(scale=4, size=4)).astype(int)
            if hq.sum() == aq.sum():
                hq[3] += 1
            date = dt.date(2021 + si, 11, 1) + dt.timedelta(days=i)
            games.append(
                {
                    "game_id": gid,
                    "season": season,
                    "game_date": date,
                    "home_team": h,
                    "away_team": a,
                    "home_pts": int(hq.sum()),
                    "away_pts": int(aq.sum()),
                }
            )
            for p in range(4):
                poss.append((gid, 2 * p, p + 1, h, int(hq[p])))
                poss.append((gid, 2 * p + 1, p + 1, a, int(aq[p])))
            cum_h, cum_a = np.cumsum(hq), np.cumsum(aq)
            pbp_files[gid] = pl.DataFrame(
                {
                    "period": [1, 1, 2, 3, 4],
                    "score_home": [
                        None,
                        str(cum_h[0]),
                        str(cum_h[1]),
                        str(cum_h[2]),
                        str(cum_h[3]),
                    ],
                    "score_away": [
                        None,
                        str(cum_a[0]),
                        str(cum_a[1]),
                        str(cum_a[2]),
                        str(cum_a[3]),
                    ],
                }
            )
            p_inj = float(1 / (1 + np.exp(-(0.9 * x + rng.normal(scale=0.3)))))
            p_mov = float(1 / (1 + np.exp(-(0.8 * x + rng.normal(scale=0.4)))))
            oof.append({"game_id": gid, "model": "rung0_injury_elo", "p": p_inj})
            oof.append({"game_id": gid, "model": "rung0_mov_elo", "p": p_mov})
            if season >= 2023:
                p_mkt = float(np.clip(p_inj + rng.normal(scale=0.04), 0.05, 0.95))
                gm_rows.append((season, gid, "consensus", p_mkt + 0.01))
                if i % 9 != 0:  # every ninth game has no Pinnacle line: consensus fallback
                    gm_rows.append((season, gid, "pinnacle", p_mkt))
            spread = -float(np.round(2 * (6 * x + rng.normal(scale=2))) / 2)
            if i % 6 == 0:
                spread = float(np.round(spread))  # whole-number line: pushes possible
            home_px, away_px = (-110, -110) if i % 4 == 0 else (-120, 100)
            tline = 220.0 + float(np.round(rng.normal(scale=6)))
            if i % 5 != 0:
                tline += 0.5
            over_px, under_px = (-110, -110) if i % 3 == 0 else (-115, -105)
            for side, px, pt, nm in (
                ("home", home_px, spread, f"T{h}"),
                ("away", away_px, -spread, f"T{a}"),
            ):
                odds.append(
                    (gid, side, pt, px, nm, "draftkings", "t60", "spreads", season, None, None)
                )
            for side, px in (("over", over_px), ("under", under_px)):
                odds.append(
                    (gid, side, tline, px, side, "draftkings", "t60", "totals", season, None, None)
                )

    # sprinkle data-quality cases on the pbp scoreboard and possessions
    by_season: dict[int, list[int]] = {
        s: [int(g["game_id"]) for g in games if g["season"] == s]  # type: ignore[call-overload]
        for s in SEASONS
    }
    no_pbp = by_season[2023][3]
    final_mismatch = by_season[2024][4]
    poss_mismatch = by_season[2024][7]
    del pbp_files[no_pbp]
    bad = pbp_files[final_mismatch].with_columns(
        pl.when(pl.col("score_home").is_not_null() & (pl.col("period") == 4))
        .then((pl.col("score_home").cast(pl.Int64) + 1).cast(pl.Utf8))
        .otherwise(pl.col("score_home"))
        .alias("score_home")
    )
    pbp_files[final_mismatch] = bad
    poss = [
        (g, i, p, t, pts + 1 if (g == poss_mismatch and p == 2 and i == 2) else pts)
        for g, i, p, t, pts in poss
    ]

    # ---- write production-shaped files under the tmp root
    (root / "data" / "pbp").mkdir(parents=True)
    for g, df in pbp_files.items():
        df.write_parquet(root / "data" / "pbp" / f"{g}.parquet")
    (root / "data" / "injury_elo_realtip").mkdir(parents=True)
    pl.DataFrame(oof).write_parquet(
        root / "data" / "injury_elo_realtip" / "oof_predictions.parquet"
    )
    oh = root / "reports" / "odds_history"
    oh.mkdir(parents=True)
    for season in (2023, 2024):
        rows = [(g, s, p) for (sea, g, s, p) in gm_rows if sea == season]
        pl.DataFrame(
            {
                "game_id": [r[0] for r in rows],
                "source": [r[1] for r in rows],
                "p_home_mult": [r[2] for r in rows],
                "snapshot_kind": ["t60"] * len(rows),
            }
        ).write_parquet(oh / f"game_market_{season}.parquet")

    names: dict[int, str] = {}
    for season in (2023, 2024):
        gids = by_season[season]
        mdl_rows, mk_rows = [], []
        for stat, base in PROP_MEANS.items():
            for j in range(60):
                pid = 100 + j % 10
                names[pid] = f"Player {pid}"
                mean = base * (0.7 + 0.06 * (pid - 100))
                y = int(rng.poisson(mean))
                line = float(np.floor(mean)) + 0.5
                if j % 15 == 0:
                    line = float(np.round(mean))  # whole-number line
                if j % 30 == 0:
                    y = int(line)  # lands exactly on the whole-number line: a push
                if j == 1:
                    line = line + 0.25  # odd line
                pge = sps.poisson.sf(np.arange(0, WIDTH), mean)
                p_mult = (
                    0.5 if j % 7 == 0 else float(np.clip(0.5 + rng.normal(scale=0.08), 0.1, 0.9))
                )
                game = gids[j]
                mdl_rows.append(
                    {
                        "game_id": game,
                        "player_id": pid,
                        "stat": stat,
                        "p_ge": [float(v) for v in pge],
                        "m_recency": mean + float(rng.normal(scale=0.5)),
                        "sd_recency": float(np.sqrt(mean) * 1.2),
                        "y": y,
                    }
                )
                mk_rows.append(
                    (game, pid, stat, "draftkings", "t60", line, p_mult, str(dt.date(2023, 1, 1)))
                )
            for j in range(4):  # quotes with no model row (DNP)
                mk_rows.append(
                    (gids[j], 999, stat, "draftkings", "t60", 10.5, 0.5, str(dt.date(2023, 1, 1)))
                )
            mk_rows.append((gids[0], 100, stat, "fanduel", "t60", 9.5, 0.5, "2023-01-01"))
        pl.DataFrame(mdl_rows).write_parquet(oh / f"model_rows_{season}.parquet")
        pl.DataFrame(
            mk_rows,
            schema=[
                "game_id",
                "player_id",
                "stat",
                "book",
                "snapshot_kind",
                "line",
                "p_over_mult",
                "game_date",
            ],
            orient="row",
        ).with_columns(pl.col("player_id").cast(pl.Int64)).write_parquet(
            oh / f"market_rows_{season}.parquet"
        )

    (root / "data" / "odds").mkdir(parents=True)
    od = duckdb.connect(str(root / "data" / "odds" / "odds_history.duckdb"))
    od.execute(
        "create table odds_history (game_id bigint, side varchar, point double, "
        "price_american integer, outcome_name varchar, book varchar, snapshot_kind varchar, "
        "market varchar, season integer, player_id bigint, player_name varchar)"
    )
    od.executemany("insert into odds_history values (?,?,?,?,?,?,?,?,?,?,?)", odds)
    od.executemany(
        "insert into odds_history values (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "over", 9.5, -110, "o", "draftkings", "t60", "player_points", 2023, pid, nm)
            for pid, nm in names.items()
        ],
    )
    od.close()

    nba = duckdb.connect(str(root / "nba.duckdb"))
    gdf = pl.DataFrame(games)  # noqa: F841 - referenced by duckdb replacement scan
    nba.execute("create table games as select * from gdf")
    pdf = pl.DataFrame(  # noqa: F841
        poss, schema=["game_id", "poss_idx", "period", "off_team", "pts"], orient="row"
    )
    nba.execute("create table possessions as select * from pdf")
    nba.close()
    return {
        "games": games,
        "no_pbp": no_pbp,
        "final_mismatch": final_mismatch,
        "poss_mismatch": poss_mismatch,
        "names": names,
    }


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("confusion")
    info = _build_world(root)
    mp = pytest.MonkeyPatch()
    scratch = root / "data" / "scratch"
    mp.setattr(cr, "ROOT", root)
    mp.setattr(cr, "OH", root / "reports" / "odds_history")
    mp.setattr(cr, "ODDS_DB", root / "data" / "odds" / "odds_history.duckdb")
    mp.setattr(cr, "NBA_DB", root / "nba.duckdb")
    mp.setattr(cr, "SCRATCH", scratch)
    mp.setattr(cr, "SNAP", scratch / "confusion_snap.duckdb")
    mp.setattr(cr, "PERIODS_CACHE", scratch / "confusion_periods.parquet")
    mp.setattr(cr, "LOCK", root / "data" / "ops" / "heavy.lock")
    mp.setattr(cr, "OUT_MD", root / "reports" / "confusion.md")
    mp.setattr(cr, "OUT_JSON", root / "reports" / "confusion" / "results.json")
    mp.setattr(cr, "B", 200)
    # the helper default arguments captured B/SEED at definition time; keep runs small anyway
    orig = cr.boot_ratios
    mp.setattr(cr, "boot_ratios", lambda c, p, b=200, seed=cr.SEED: orig(c, p, b, seed))
    info["root"] = root
    yield info
    mp.undo()


# ------------------------------------------------------------------------------ pipeline pieces


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def test_build_snapshot_copies_pre_holdout_tables_and_is_idempotent(world: Any) -> None:
    meta = cr.build_snapshot()
    n = len(world["games"])
    assert meta["games_rows"] == n and meta["git_head"] is not None
    snap = duckdb.connect(str(cr.SNAP), read_only=True)
    try:
        assert _scalar(snap, "select count(*) from games") == n
        assert _scalar(snap, "select count(*) from poss") == n * 8
        assert _scalar(snap, "select max(season) from games") == 2024
    finally:
        snap.close()
    # second call reads the stored metadata instead of re-copying
    assert cr.build_snapshot() == meta


def test_build_snapshot_waits_for_the_lock_before_copying(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cr, "wait_lock", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(cr, "SNAP", cr.SCRATCH / "other_snap.duckdb")
    cr.build_snapshot()
    assert calls == [1]
    assert (cr.SCRATCH / "other_snap.duckdb").exists()


def test_load_game_frame_joins_models_market_and_lines(world: Any) -> None:
    cr.build_snapshot()
    snap = duckdb.connect(str(cr.SNAP), read_only=True)
    try:
        g = cr.load_game_frame(snap)
    finally:
        snap.close()
    assert g.height == len(world["games"])
    assert g["game_id"].n_unique() == g.height  # no join fan-out
    assert (g["margin"] == g["home_pts"] - g["away_pts"]).all()
    assert g["p_inj"].null_count() == 0 and g["p_mov"].null_count() == 0
    assert (
        g.filter(pl.col("season") == 2022)["p_mkt"].null_count()
        == g.filter(pl.col("season") == 2022).height
    )  # no market before 2023
    ev = g.filter(pl.col("season") >= 2023)
    assert ev["p_mkt"].null_count() == 0
    n_fallback = int(ev["mkt_is_fallback"].sum())
    assert n_fallback == 2 * len([i for i in range(GAMES_PER_SEASON) if i % 9 == 0])
    assert g["home_spread"].null_count() == 0 and g["total_line"].null_count() == 0
    assert g["total_hat"].null_count() < g.height  # baseline defined after warm-up


def test_props_section_confusion_and_status_accounting(world: Any) -> None:
    cr.build_snapshot()
    snap = duckdb.connect(str(cr.SNAP), read_only=True)
    try:
        res, arrs = cr.props_section(snap)
    finally:
        snap.close()
    # DraftKings only: 4 stats x 2 seasons x (60 modelled + 4 DNP quotes)
    assert res["quotes_total"] == 4 * 2 * 64
    assert res["quotes_without_model_row_(dnp_or_no_row)"] == 4 * 2 * 4
    for stat in cr.STATS:
        c = res["status_counts"][stat]
        assert c["quotes_with_model"] == 120
        assert c["scored"] + c["push"] + c["odd_line"] + c["model_push_certain"] == 120
        assert c["push"] > 0 and c["odd_line"] == 2  # j == 1 in each season
    pooled = res["pooled"]["all"]
    per_stat_n = sum(res["per_stat"][s]["n"] for s in cr.STATS)
    assert pooled["n"] == per_stat_n == len(arrs["sc"])
    m = pooled["matrices_all_rows"]["model"]
    assert m["tp"] + m["fp"] + m["fn"] + m["tn"] == pooled["n"]
    assert 0 <= m["accuracy"]["lo"] <= m["accuracy"]["rate"] <= m["accuracy"]["hi"] <= 1
    # accuracy re-derived independently from the scored frame
    sc = arrs["sc"]
    acc = float(((sc["p_model"].to_numpy() > 0.5) == (sc["over"].to_numpy() > 0.5)).mean())
    assert m["accuracy"]["rate"] == pytest.approx(acc)
    # market-lean rows exclude exact 0.5 prices
    lean = int((sc["p_over_mult"].to_numpy() != 0.5).sum())
    assert pooled["market_lean_n"] == lean < pooled["n"]
    assert pooled["matrices_market_lean_rows"]["market"]["n"] == lean
    assert sum(r["n"] for r in pooled["confidence_bins"]["market"]) <= lean
    assert set(res["examples"]) == {"hit", "miss"}
    assert res["examples"]["hit"]["player"].startswith("Player ")
    assert res["push_rate_overall"] > 0


def test_games_section_accuracy_matches_independent_computation(world: Any) -> None:
    cr.build_snapshot()
    snap = duckdb.connect(str(cr.SNAP), read_only=True)
    try:
        g = cr.load_game_frame(snap)
    finally:
        snap.close()
    out = cr.games_section(g)
    w = out["winner"]
    ev = g.filter(pl.col("season").is_in([2023, 2024]))
    assert w["n"] == ev.height
    home_win = (ev["margin"] > 0).to_numpy()
    expect = float(((ev["p_inj"] > 0.5).to_numpy() == home_win).mean())
    assert w["acc"]["injury_elo"]["rate"] == pytest.approx(expect)
    assert w["market_fallback_to_consensus"] == int(ev["mkt_is_fallback"].sum())
    d = w["inj_minus_market"]
    exp_mkt = float(((ev["p_mkt"] > 0.5).to_numpy() == home_win).mean())
    assert d["diff"] == pytest.approx(expect - exp_mkt)
    assert sum(r["market"]["n"] for r in w["by_market_confidence"]) == w["n"]
    assert set(out["winner_examples"]) == {"hit", "miss"}
    for tag in ("mov_elo", "injury_elo"):
        s = out["spread"][tag]
        assert (
            s["n"] + s["pushes_excluded"] == ev.filter(pl.col("home_spread").is_not_null()).height
        )
        assert s["pushes_excluded"] > 0  # whole-number spreads were seeded
        assert s["matrix_model"]["n"] == s["n"]
        assert s["mae_model_vs_margin"] > 0
    tot = out["total"]
    assert tot["2023"]["fit_n"] > 0 and tot["2024"]["fit_n"] > tot["2023"]["fit_n"]
    o = tot["overall"]
    assert o["n"] + o["pushes_excluded"] > 0 and o["pushes_excluded"] > 0
    assert 0 <= o["over_rate"]["rate"] <= 1


def test_build_periods_reads_scoreboard_and_flags_missing_files(world: Any) -> None:
    cr.build_snapshot()
    snap = duckdb.connect(str(cr.SNAP), read_only=True)
    try:
        if cr.PERIODS_CACHE.exists():
            cr.PERIODS_CACHE.unlink()
        pb = cr.build_periods(snap)
        assert cr.PERIODS_CACHE.exists()
        row = pb.filter(pl.col("game_id") == world["no_pbp"]).row(0, named=True)
        assert row["h_fin"] is None and row["h_c1"] is None  # no file => all-null scoreboard
        g0 = world["games"][0]
        r0 = pb.filter(pl.col("game_id") == g0["game_id"]).row(0, named=True)
        assert r0["h_fin"] == g0["home_pts"] and r0["a_fin"] == g0["away_pts"]
        assert r0["h_c4"] == r0["h_fin"] and r0["h_c1"] <= r0["h_c2"] <= r0["h_c3"] <= r0["h_c4"]
        assert r0["max_period"] == 4
        # cache hit returns the stored frame without reading pbp again
        f = cr.ROOT / "data" / "pbp" / f"{g0['game_id']}.parquet"
        hidden = f.with_suffix(".hidden")
        f.rename(hidden)
        try:
            again = cr.build_periods(snap)
            assert again.filter(pl.col("game_id") == g0["game_id"])["h_fin"][0] == g0["home_pts"]
            redo = cr.build_periods(snap, force=True)
            assert redo.filter(pl.col("game_id") == g0["game_id"])["h_fin"][0] is None
        finally:
            hidden.rename(f)
            cr.PERIODS_CACHE.unlink()
    finally:
        snap.close()


def test_main_end_to_end_writes_consistent_json_and_markdown(world: Any) -> None:
    # fresh snapshot + period cache so main() exercises every stage
    for p in (cr.SNAP, cr.PERIODS_CACHE, cr.SCRATCH / "confusion_snap.json"):
        if p.exists():
            p.unlink()
    assert cr.main() == 0
    res = json.loads(cr.OUT_JSON.read_text())
    assert set(res) >= {"snapshot", "rule", "props", "games", "periods"}
    rec = res["periods"]["reconciliation"]
    assert rec["games_total"] == len(world["games"])
    assert rec["no_pbp_rows"] == 1
    # a missing file and a final-score disagreement are both counted as final mismatches
    assert rec["pbp_final_vs_box_mismatch"] == 2
    # the seeded possession edit (Q2) and the seeded pbp edit (Q4) are the only disagreements
    assert set(rec["poss_vs_pbp_mismatch_ids"]) == {world["poss_mismatch"], world["final_mismatch"]}
    assert rec["poss_vs_pbp_mismatch_by_period"] == {"Q1": 0, "Q2": 1, "Q3": 0, "Q4": 1}
    assert world["no_pbp"] in rec["excluded_game_ids"]
    assert world["final_mismatch"] in rec["excluded_game_ids"]
    assert rec["excluded_n"] == 2
    q = res["periods"]["by_model"]["injury_elo"]
    for tk in ("q1", "q2", "q3", "q4", "h1", "h2"):
        m = np.array(q[tk]["matrix_pred_by_actual_[H,T,A]"])
        assert m.sum() == q[tk]["n"] > 0
        assert q[tk]["logloss_3class"] > 0
        assert set(q[tk]["sigma"]) == {"2023", "2024"}
    # the period margin slope is positive for a model correlated with the margin
    assert q["h1"]["share_vs_game_slope"]["2024"] > 0
    assert set(res["periods"]["period_totals_vs_outcome"]) == {"q1", "q2", "h1"}
    md = cr.OUT_MD.read_text()
    for needle in (
        "# Confusion matrices",
        "## 1. Player props",
        "## 2. Game lines",
        "## 3. Quarters and halves",
        "## Caveats",
        "### pooled",
        "| Q1 winner (3-class H/tie/A)",
        "NO MARKET",
    ):
        assert needle in md
    assert "nan" not in md.lower().replace("financ", "")
