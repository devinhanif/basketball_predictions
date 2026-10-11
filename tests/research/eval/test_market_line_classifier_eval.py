"""MARKET_LINE_CLASSIFIER harness (docs/prereg/MARKET_LINE_CLASSIFIER.md, DRAFT).

Synthetic frames only (no real database, no season 2025). Each guard is proven where the answer is
known: the hash refusal, identical rows across arms, M3 without forecast inputs, the M4 placebo
touching only ``p_grid_over`` and ``z`` within (stat, season), and the planted-future tests
(including the positive controls that must fail).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import market_line_classifier_eval as m

STATS = m.STATS
W = {s: 0.15 for s in STATS}


def make_frame(seed: int = 0, per_stat_season: int = 700) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    parts = []
    for season, start in ((2023, dt.date(2023, 10, 24)), (2024, dt.date(2024, 10, 23))):
        for si, stat in enumerate(STATS):
            n = per_stat_season
            days = rng.integers(0, 150, n)
            date = [start + dt.timedelta(days=int(d)) for d in days]
            p_book = np.clip(rng.normal(0.5, 0.06, n), 0.2, 0.8)
            p_grid = np.clip(p_book + rng.normal(0, 0.05, n), 0.02, 0.98)
            y = (rng.random(n) < p_book).astype(float)
            line = rng.integers(3, 30, n) + 0.5
            gid = [
                f"{season}{int(d):03d}{int(k) % 12:02d}"
                for d, k in zip(days, rng.integers(0, 12, n), strict=True)
            ]
            parts.append(
                pl.DataFrame(
                    {
                        "game_id": gid,
                        "player_id": rng.integers(1, 400, n).astype(np.int64),
                        "team_id": rng.integers(1, 30, n).astype(np.int64),
                        "game_date": date,
                        "season": np.full(n, season, dtype=np.int64),
                        "stat": [stat] * n,
                        "stat_code": np.full(n, float(si)),
                        "line": line,
                        "y": y,
                        "p_book_over": p_book,
                        "p_cons_over": p_book + rng.normal(0, 0.01, n),
                        "cons_line": line,
                        "n_books": rng.integers(3, 12, n).astype(float),
                        "p_grid_over": p_grid,
                        "z": rng.normal(0, 1, n),
                        "tier": rng.integers(0, 4, n).astype(float),
                        "starter10": rng.random(n),
                        "is_b2b": rng.integers(0, 2, n).astype(float),
                        "n_out_rot": rng.integers(0, 3, n).astype(float),
                        "tag": rng.integers(0, 6, n).astype(float),
                        "game_total": rng.normal(225, 8, n),
                        "total_raw": rng.normal(225, 8, n),
                        "total_missing": np.zeros(n),
                        "spread": rng.normal(0, 5, n),
                        "spread_raw": rng.normal(0, 5, n),
                        "spread_missing": np.zeros(n),
                        "price_over": np.full(n, -110, dtype=np.int64),
                        "price_under": np.full(n, -110, dtype=np.int64),
                    }
                )
            )
    return pl.concat(parts).sort(["game_date", "game_id", "player_id", "stat"])


# --------------------------------------------------------------------------- hash refusal


def _doc(tmp_path: Path, body: str = "rule text\n") -> Path:
    p = tmp_path / "MARKET_LINE_CLASSIFIER.md"
    p.write_text(body + "=== RESULTS BELOW ===\nnot run\n")
    return p


def test_hash_refusal(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    m.configure(doc)
    good = hashlib.sha256(b"rule text\n").hexdigest()
    assert m.require_frozen(good, allow_draft=False) == good
    with pytest.raises(SystemExit):
        m.require_frozen(None, allow_draft=False)
    with pytest.raises(SystemExit):
        m.require_frozen("abc", allow_draft=False)
    with pytest.raises(SystemExit):
        m.require_frozen("0" * 64, allow_draft=False)
    assert m.require_frozen(None, allow_draft=True) == good
    doc.write_text("rule text CHANGED\n=== RESULTS BELOW ===\nnot run\n")
    with pytest.raises(SystemExit):
        m.require_frozen(good, allow_draft=False)
    # results below the marker never change the hash
    doc.write_text("rule text\n=== RESULTS BELOW ===\nnumbers\n")
    assert m.require_frozen(good, allow_draft=False) == good


def test_allow_draft_is_gate_only(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    for stage in ("smoke", "run", "report"):
        with pytest.raises(SystemExit):
            m.main([stage, "--doc", str(doc), "--allow-draft"])
        with pytest.raises(SystemExit):
            m.main([stage, "--doc", str(doc), "--sha", "0" * 64])
    m.configure(m.DEFAULT_DOC)


# --------------------------------------------------------------------------- arms


def test_arms_get_identical_rows() -> None:
    fr = make_frame()
    preds = m.walk_forward(fr, W, n_estimators=5, min_train=500)
    n = len(preds["y"])
    assert n > 0
    for arm in m.ARMS:
        assert len(preds[arm]) == n
        assert np.isfinite(preds[arm]).all()
    # the scored rows are exactly the rows of the scored blocks, once
    scored = sum(len(te) for _a, _b, _tr, te, sc in m.block_splits(fr, 500) if sc)
    assert n == scored
    keys = list(zip(preds["gid"], preds["pid"], preds["stat"], strict=True))
    assert len(keys) == n
    # M0 is the book's own probability on exactly those rows
    idx = np.concatenate([te for _a, _b, _tr, te, sc in m.block_splits(fr, 500) if sc])
    assert np.array_equal(preds["M0"], fr["p_book_over"].to_numpy()[idx])
    assert np.array_equal(preds["y"], fr["y"].to_numpy()[idx])


def test_unscored_first_block_reported() -> None:
    fr = make_frame()
    sp = m.block_splits(fr, 500)
    assert sp[0][4] is False and len(sp[0][2]) == 0
    assert any(s[4] for s in sp)


def test_m3_has_no_forecast_inputs() -> None:
    f = m.arm_features()
    forecast = {"p_grid_over", "z", "p_grid_over_perm", "z_perm"}
    assert not forecast & set(f["M3"])
    assert set(f["M3"]) <= set(f["M2"])
    assert not {"starter10", "is_b2b", "n_out_rot", "tag", "p_cons_over"} & set(f["M3"])
    assert {"p_book_over", "line", "stat_code", "tier", "game_total", "spread", "n_books"} <= set(
        f["M3"]
    )
    assert forecast & set(f["M2"]) == {"p_grid_over", "z"}


def test_m4_permutes_only_forecast_inputs_within_stat_season() -> None:
    fr = make_frame()
    pm = m.permute_forecast_inputs(fr, seed=0)
    assert pm.drop([c + m.PERM_SUFFIX for c in m.FORECAST_INPUTS]).equals(fr)
    f = m.arm_features()
    assert f["M4"] == [c + m.PERM_SUFFIX if c in m.FORECAST_INPUTS else c for c in f["M2"]]
    for s in STATS:
        for yr in m.SEASONS:
            sub = pm.filter((pl.col("stat") == s) & (pl.col("season") == yr))
            for c in m.FORECAST_INPUTS:
                assert np.allclose(
                    np.sort(sub[c].to_numpy()), np.sort(sub[c + m.PERM_SUFFIX].to_numpy())
                )
            assert (sub["p_grid_over"] != sub["p_grid_over" + m.PERM_SUFFIX]).mean() > 0.9  # type: ignore[operator]
            # one joint permutation: the (p, z) pairs survive as pairs
            a = set(zip(sub["p_grid_over"].to_list(), sub["z"].to_list(), strict=True))
            b = set(
                zip(
                    sub["p_grid_over" + m.PERM_SUFFIX].to_list(),
                    sub["z" + m.PERM_SUFFIX].to_list(),
                    strict=True,
                )
            )
            assert a == b
    # seeded: identical twice
    assert pm.equals(m.permute_forecast_inputs(fr, seed=0))


def test_permutation_breaks_the_link_to_the_label() -> None:
    fr = make_frame()
    y = fr["y"].to_numpy()
    pg = fr["p_grid_over"].to_numpy()
    pm = m.permute_forecast_inputs(fr)["p_grid_over" + m.PERM_SUFFIX].to_numpy()
    d = fr["p_book_over"].to_numpy()
    assert abs(np.corrcoef(pm - d, y)[0, 1]) < 0.1
    assert abs(np.corrcoef(pg - d, y)[0, 1]) < 0.2  # real column is noise here too; sanity only


# --------------------------------------------------------------------------- planted future


def test_walk_forward_plants_pass_and_controls_fire() -> None:
    fr = make_frame()
    out = m.walk_forward_plants(fr)
    assert all(out.values()), out


def test_planted_future_changes_nothing_before_the_cut() -> None:
    fr = make_frame()
    base = m.walk_forward(fr, W, n_estimators=5, min_train=500)
    cut = dt.date(2024, 12, 1)
    rng = np.random.default_rng(3)
    msk = fr["game_date"] >= cut
    n = fr.height
    edited = fr.with_columns(
        pl.when(msk)
        .then(pl.Series(rng.integers(0, 2, n).astype(float)))
        .otherwise(pl.col("y"))
        .alias("y"),
        pl.when(msk).then(pl.Series(rng.random(n))).otherwise(pl.col("z")).alias("z"),
        pl.when(msk)
        .then(pl.Series(rng.random(n)))
        .otherwise(pl.col("p_grid_over"))
        .alias("p_grid_over"),
    )
    new = m.walk_forward(edited, W, n_estimators=5, min_train=500)
    early = base["date"] < "2024-12-01"
    assert early.sum() > 0
    for arm in ("M0", "M1", "M2", "M3"):
        assert np.array_equal(base[arm][early], new[arm][new["date"] < "2024-12-01"])
    # the control: a block after the cut moves
    late = base["date"] >= "2024-12-01"
    assert not np.array_equal(base["M2"][late], new["M2"][new["date"] >= "2024-12-01"])


def test_m1_blend_matches_odds_history() -> None:
    pk = np.array([0.4, 0.5, 0.6])
    pm = np.array([0.7, 0.5, 0.2])
    from research.eval import odds_history_eval as oh

    got = m._blend_vec(pk, pm, np.full(3, 0.15))
    assert np.allclose(got, oh.blend_p(pk, pm, 0.15))


# --------------------------------------------------------------------------- builders


def test_prior10_tier_is_asof_and_independent_of_the_own_game() -> None:
    d0 = dt.date(2024, 1, 1)
    games = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(12)],
            "game_date": [d0 + dt.timedelta(days=i) for i in range(12)],
        }
    )
    pgs = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(12)],
            "player_id": [1] * 12,
            "minutes": [30.0] * 12,
            "pts": [30.0] * 11 + [0.0],
        }
    )
    keys = pl.DataFrame({"game_id": [f"g{i}" for i in range(12)], "player_id": [1] * 12})
    t = m.prior10_tier(pgs, games, keys).sort("game_id")
    last = t.filter(pl.col("game_id") == "g11")
    assert last["p10"][0] == pytest.approx(30.0)  # the 0-point game itself is not in its prior
    assert last["tier"][0] == 3.0
    assert t.filter(pl.col("game_id") == "g0")["tier"][0] == -1.0
    # editing, or even deleting, the own game's box row changes nothing about that row
    for edited in (
        pgs.with_columns(
            pl.when(pl.col("game_id") == "g11").then(99.0).otherwise(pl.col("pts")).alias("pts")
        ),
        pgs.with_columns(
            pl.when(pl.col("game_id") == "g11")
            .then(0.0)
            .otherwise(pl.col("minutes"))
            .alias("minutes")
        ),
    ):
        e = m.prior10_tier(edited, games, keys).sort("game_id")
        assert e.filter(pl.col("game_id") == "g11").equals(last)
    # editing a past game does move later rows (the control)
    past = pgs.with_columns(
        pl.when(pl.col("game_id") == "g3").then(0.0).otherwise(pl.col("pts")).alias("pts")
    )
    assert m.prior10_tier(past, games, keys).sort("game_id")["p10"][11] < 30.0


def test_fill_total_is_asof() -> None:
    d0 = dt.date(2024, 1, 1)
    df = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "game_date": [d0, d0 + dt.timedelta(days=1), d0 + dt.timedelta(days=2), d0],
            "game_total": [200.0, 220.0, None, None],
        }
    )
    out = m.fill_total_asof(df).sort("game_id")
    assert out["total_missing"].to_list() == [0.0, 0.0, 1.0, 1.0]
    assert out["game_total"][2] == pytest.approx(210.0)  # median of earlier dates only
    assert out["game_total"][3] is None  # nothing earlier than the first date
    future = df.with_columns(
        pl.when(pl.col("game_id") == "b")
        .then(999.0)
        .otherwise(pl.col("game_total"))
        .alias("game_total")
    )
    assert m.fill_total_asof(future).sort("game_id")["game_total"][3] is None


def _quotes() -> tuple[pl.DataFrame, pl.DataFrame]:
    tip = dt.datetime(2024, 1, 2, 0, 0)
    ts_ok = tip - dt.timedelta(minutes=70)
    ts_late = tip - dt.timedelta(minutes=30)
    rows: list[dict[str, Any]] = []

    def add(
        book: str, market: str, side: str, point: float | None, price: int, ts: dt.datetime
    ) -> None:
        rows.append(
            {
                "game_id": "g1",
                "snapshot_kind": "t60",
                "snapshot_ts": ts,
                "book": book,
                "market": market,
                "side": side,
                "point": point,
                "price_american": price,
            }
        )

    for book, tot, ts in (("a", 220.5, ts_ok), ("b", 222.5, ts_ok), ("late", 300.5, ts_late)):
        add(book, "totals", "over", tot, -110, ts)
        add(book, "totals", "under", tot, -110, ts)
        add(book, "spreads", "home", -4.5, -110, ts)
        add(book, "spreads", "away", 4.5, -110, ts)
        add(book, "h2h", "home", None, -200, ts)
        add(book, "h2h", "away", None, 170, ts)
    # an unbalanced alternate total for book a must not become its main line
    add("a", "totals", "over", 250.5, 300, ts_ok)
    add("a", "totals", "under", 250.5, -400, ts_ok)
    tips = pl.DataFrame({"game_id": ["g1"], "tipoff_utc": [tip]})
    return pl.DataFrame(rows), tips


def test_game_line_table_uses_main_lines_and_guards_late_rows() -> None:
    q, tips = _quotes()
    gl, guard = m.game_line_table(q, tips)
    row = gl.row(0, named=True)
    assert row["game_total"] == pytest.approx(221.5)  # median of 220.5, 222.5; late book excluded
    assert row["spread_home"] == pytest.approx(-4.5)
    assert row["n_books_game_total"] == 2
    assert 0.6 < row["p_home_h2h"] < 0.7
    assert guard["late_or_no_tip_rows_excluded"] > 0
    assert row["ts_game_total"] <= dt.datetime(2024, 1, 2) - dt.timedelta(minutes=60)


# --------------------------------------------------------------------------- analysis


def test_analyse_shapes_and_checks() -> None:
    fr = make_frame()
    preds = m.walk_forward(fr, W, n_estimators=5, min_train=500)
    res = m.analyse(preds, n_boot=50)
    cols = {
        "stat",
        "season",
        "arm",
        "n",
        "logloss",
        "brier",
        "dlogloss",
        "ci_lo",
        "ci_hi",
        "p",
        "p_holm",
        "ece",
        "acc",
        "acc_book",
    }
    assert all(cols <= set(r) for r in res["rows"])
    assert len(res["rows"]) == len(STATS) * len(m.SEASONS) * len(m.ARMS)
    m0 = [r for r in res["rows"] if r["arm"] == "M0"]
    assert all(r["dlogloss"] == 0.0 for r in m0)
    assert set(res["pass"]) == set(STATS)
    # pure noise features cannot pass the practical floor
    assert not any(v["pass"] for v in res["pass"].values())
    assert all(r["p_holm"] is not None for r in res["rows"] if r["arm"] == "M2")


def test_check_pairs_counts_and_coverage() -> None:
    fr = make_frame(per_stat_season=m.MIN_PAIRS + 50)
    chk = m.check_pairs(fr, min_train=500)
    assert chk["ok"]
    assert chk["detail"]["pts/2023"]["pairs"] == m.MIN_PAIRS + 50
    thin = fr.with_columns(
        pl.when(pl.col("stat") == "ast")
        .then(None)
        .otherwise(pl.col("total_raw"))
        .alias("total_raw")
    )
    assert not m.check_pairs(thin, min_train=500)["ok"]
    few = make_frame(per_stat_season=100)
    assert not m.check_pairs(few, min_train=500)["ok"]


def test_missingness_gap_flags_a_planted_difference() -> None:
    fr = make_frame(per_stat_season=1500)
    gaps = m.max_gap_pp(m.missingness(fr))
    assert gaps["game_total"]["max_pp"] == 0.0
    bad = fr.with_columns(
        pl.when(pl.col("tier") == 3.0).then(None).otherwise(pl.col("total_raw")).alias("total_raw")
    )
    assert m.max_gap_pp(m.missingness(bad))["game_total"]["max_pp"] > m.GAP_PP


def test_season_2025_refused() -> None:
    fr = make_frame().with_columns(pl.lit(2025).alias("season"))
    with pytest.raises(ValueError):
        m.assert_seasons(fr)
    with pytest.raises(ValueError):
        m.walk_forward(fr, W, n_estimators=2, min_train=10)
