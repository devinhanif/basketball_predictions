# ruff: noqa: E501
"""INJURY_REASON harness (docs/prereg/INJURY_REASON.md, DRAFT): freeze refusal, hash parsing,
offset ridge-logistic, walk-forward on a synthetic frame, placebo keys, rule evaluation.

Synthetic data only (no database, no season 2025)."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from research.eval import injury_reason_eval as ev


def _doc(tmp_path: Path, status: str = "DRAFT") -> Path:
    p = tmp_path / "RULE.md"
    p.write_text(f"# R\n\nStatus: {status}\n\nrule text\n=== RESULTS BELOW ===\nresults\n")
    return p


def test_frozen_sha_is_whole_line_marker_only(tmp_path: Path) -> None:
    p = _doc(tmp_path)
    want = hashlib.sha256(b"# R\n\nStatus: DRAFT\n\nrule text\n").hexdigest()
    assert ev.frozen_sha(p) == want
    # a prose mention of the marker inside a line must not cut the hashed text
    q = tmp_path / "R2.md"
    q.write_text(
        "# R\nthe line `=== RESULTS BELOW ===` is the marker\nbody\n=== RESULTS BELOW ===\nr\n"
    )
    assert (
        ev.frozen_sha(q)
        == hashlib.sha256(
            b"# R\nthe line `=== RESULTS BELOW ===` is the marker\nbody\n"
        ).hexdigest()
    )
    assert ev.parse_results_block(q).strip() == "r"


def test_run_refuses_unless_status_frozen_and_sha_matches(tmp_path: Path) -> None:
    p = _doc(tmp_path, "DRAFT")
    sha = ev.frozen_sha(p)
    with pytest.raises(SystemExit, match="FROZEN"):
        ev.require_frozen(p, sha, allow_draft=False)
    assert ev.require_frozen(p, None, allow_draft=True) == sha
    f = _doc(tmp_path, "FROZEN 2026-10-12")
    good = ev.frozen_sha(f)
    assert ev.require_frozen(f, good, allow_draft=False) == good
    with pytest.raises(SystemExit, match="!="):
        ev.require_frozen(f, "0" * 64, allow_draft=False)
    with pytest.raises(SystemExit, match="64-hex"):
        ev.require_frozen(f, "abc", allow_draft=False)


def test_allow_draft_only_for_gate() -> None:
    with pytest.raises(SystemExit, match="gate stage only"):
        ev.main(["run", "--allow-draft"])


def test_offset_logit_recovers_signal_and_respects_offset() -> None:
    rng = np.random.default_rng(1)
    n = 20000
    x = rng.integers(0, 2, size=(n, 2)).astype(float)
    off = rng.normal(0, 0.5, n)
    z = off + 0.3 + 1.2 * x[:, 0] - 0.8 * x[:, 1]
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
    b = ev.fit_offset_logit(x, y, off, lam=1.0)
    assert b[0] == pytest.approx(0.3, abs=0.1)
    assert b[1] == pytest.approx(1.2, abs=0.1)
    assert b[2] == pytest.approx(-0.8, abs=0.1)
    # with pure-noise x the correction is near zero and predictions stay near sigmoid(offset)
    xn = rng.integers(0, 2, size=(n, 3)).astype(float)
    yn = (rng.random(n) < 1 / (1 + np.exp(-off))).astype(float)
    bn = ev.fit_offset_logit(xn, yn, off, lam=1.0)
    assert np.all(np.abs(bn) < 0.1)


def _flagged_frame(seed: int = 0, per_day: int = 12, days: int = 400) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = dt.date(2022, 11, 1)
    gid = 0
    for d in range(days):
        day = start + dt.timedelta(days=d)
        season = 2022 if day < dt.date(2023, 9, 1) else 2023
        for k in range(per_day):
            gid += 1
            status = int(rng.integers(2, 5))
            cls = "injury" if rng.random() < 0.8 else "illness"
            typ = "surgery" if (cls == "injury" and rng.random() < 0.3) else "soreness"
            p_raw = float(np.clip(rng.normal(0.6, 0.15), 0.05, 0.95))
            truth = (
                0.15 if typ == "surgery" else p_raw
            )  # the reason matters beyond p_raw and status
            rows.append(
                {
                    "game_id": f"g{d}_{k // 4}",
                    "player_id": gid,
                    "game_date": day,
                    "season": season,
                    "p_raw": p_raw,
                    "played": float(rng.random() < truth),
                    "status_code": status,
                    "cls": cls,
                    "body": "knee",
                    "type": typ,
                    "side": "left",
                    "days_since_flag": float(rng.integers(0, 20)),
                }
            )
    f = pl.DataFrame(rows).sort(["game_date", "game_id", "player_id"])
    return f.with_columns(
        pl.Series("day_bin", ev.day_bin(f["days_since_flag"].to_numpy())),
    )


def test_walk_forward_is_causal_and_reason_helps_on_planted_signal() -> None:
    f = _flagged_frame()
    # causal: changing the labels of the LAST block cannot change an earlier block's predictions
    base = ev.walk_t1(f, True, test_seasons=(2023,))
    f2 = f.with_columns(
        pl.when(pl.col("game_date") >= dt.date(2023, 12, 1))
        .then(1.0 - pl.col("played"))
        .otherwise(pl.col("played"))
        .alias("played")
    )
    alt = ev.walk_t1(f2, True, test_seasons=(2023,))
    early = (f["game_date"] < dt.date(2023, 12, 1)).to_numpy() & (f["season"] == 2023).to_numpy()
    assert np.allclose(base[early], alt[early], equal_nan=True)
    ok = ~np.isnan(base)
    assert ok.sum() > 1000
    a0t = ev.walk_t1(f, False, test_seasons=(2023,))
    y = f["played"].to_numpy().astype(bool)
    ll1 = ev._ll(base[ok], y[ok]).mean()
    ll0 = ev._ll(a0t[ok], y[ok]).mean()
    assert ll1 < ll0 - 0.01  # the planted reason signal is found


def test_placebo_t1_breaks_the_signal_and_keeps_keys() -> None:
    f = _flagged_frame(seed=3)
    p = ev.placebo_t1(f, 0)
    assert p.select(ev.KEYS).equals(f.select(ev.KEYS))
    assert sorted(p["type"].to_list()) == sorted(f["type"].to_list())
    a1 = ev.walk_t1(f, True, test_seasons=(2023,))
    pp = ev.walk_t1(p, True, test_seasons=(2023,))
    ok = ~np.isnan(a1) & ~np.isnan(pp)
    y = f["played"].to_numpy().astype(bool)
    assert ev._ll(pp[ok], y[ok]).mean() > ev._ll(a1[ok], y[ok]).mean() + 0.01


def test_placebo_t2_shuffles_only_return_rows_within_date() -> None:
    d = dt.date(2024, 1, 1)
    f = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(8)],
            "player_id": list(range(8)),
            "game_date": [
                d,
                d,
                d,
                d,
                d + dt.timedelta(days=1),
                d + dt.timedelta(days=1),
                d + dt.timedelta(days=1),
                d + dt.timedelta(days=1),
            ],
            **{c: [0.0, 1.0, 2.0, 0.0, 1.0, 3.0, 0.0, 2.0] for c in ev.EXTRA_COLS},
        }
    )
    f = f.with_columns(pl.Series("rb_game_back", [0.0, 1.0, 2.0, 0.0, 1.0, 3.0, 0.0, 2.0]))
    p = ev.placebo_t2(f, 0)
    assert p.select(ev.KEYS).equals(f.select(ev.KEYS))
    assert p.columns == f.columns
    nonret = f["rb_game_back"] == 0
    for c in ev.EXTRA_COLS:
        assert p.filter(nonret)[c].to_list() == [0.0] * int(nonret.sum()) or c != "rb_game_back"
    assert p.filter(nonret)["rb_cls"].to_list() == f.filter(nonret)["rb_cls"].to_list()
    for day in f["game_date"].unique().to_list():
        a = f.filter((pl.col("game_date") == day) & (pl.col("rb_game_back") >= 1))
        b = p.filter(
            (pl.col("game_date") == day) & (pl.col("rb_game_back") >= 1)
            | (pl.col("game_date") == day) & (pl.col("rb_cls") > 0)
        )
        assert sorted(a["rb_type"].to_list()) == sorted(
            b.filter(pl.col("rb_type") > 0)["rb_type"].to_list()
        )


def test_design_columns_and_day_bins() -> None:
    f = _flagged_frame(per_day=2, days=3)
    x1 = ev.design(f, True)
    x0 = ev.design(f, False)
    assert x0.shape[1] == 3
    assert x1.shape[1] == 3 + len(ev.CLASSES) + len(ev.BODY_GROUPS) + len(ev.TYPES) + len(
        ev.DAY_BINS
    )
    assert np.all(x1[:, 3:].sum(axis=1) == 4)  # one class, one body, one type, one day bin per row
    assert list(ev.day_bin(np.array([0.0, 0.9, 1.0, 2.9, 3.0, 6.0, 7.0, 13.0, 14.0, 300.0]))) == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
        4,
        4,
    ]


def test_bh_and_rules_on_planted_rows() -> None:
    rows = []
    for tgt, arm, d in (("t1", "A1-A0t", -0.02), ("pts", "A1-A0", -0.01), ("min", "A1-A0", 0.0)):
        for s in ev.SEASONS:
            rows.append(
                {
                    "target": tgt,
                    "season": s,
                    "arm": arm,
                    "dcrps": d,
                    "ci_lo": d - 0.005,
                    "ci_hi": d + 0.003 if d else 0.02,
                    "p": 0.001 if d else 0.7,
                    "mde": 0.004,
                    "n_rows": 3000,
                }
            )
            rows.append(
                {
                    "target": tgt,
                    "season": s,
                    "arm": "A1-P1",
                    "dcrps": d,
                    "ci_lo": d - 0.005,
                    "ci_hi": 0.0,
                }
            )
            rows.append({"target": tgt, "season": s, "arm": "A1", "ece": 0.02, "n_rows": 3000})
            rows.append({"target": tgt, "season": s, "arm": "A0t", "ece": 0.02, "n_rows": 3000})
    ev.add_bh(rows, {"t1": "A1-A0t", "pts": "A1-A0", "min": "A1-A0"})
    got = [r for r in rows if r["target"] == "t1" and r["arm"] == "A1-A0t" and r["season"] == 2023][
        0
    ]
    assert got["p_bh"] == pytest.approx(
        0.0015, abs=1e-9
    )  # BH over the three primary tests: 0.001 * 3 / 2
    rules = ev.evaluate_rules(rows, [])
    assert rules["t1"]["pass"] is True
    assert rules["pts"]["pass"] is True  # -0.01 <= floor -0.005, CI upper < 0, placebo margin met
    assert rules["min"]["pass"] is False  # a zero effect never passes
    assert rules["close_no_pass_2023"] is False


def test_score_t1_pairs_rows_and_clusters_by_date() -> None:
    f = _flagged_frame(seed=5, days=60)
    f = f.with_columns(pl.lit(2023).alias("season"))
    p0 = f["p_raw"].to_numpy()
    arms = {"A0": p0, "A0t": p0, "A1": np.clip(p0 + 0.01, 0.01, 0.99), "P1": p0}
    out = ev.score_t1(f, arms)
    labels = {r["arm"] for r in out["rows"] if r["season"] == 2023}
    assert {"A1-A0t", "A1-A0", "A1-P1", "P1-A0t"} <= labels
    r = next(r for r in out["rows"] if r["arm"] == "A1-A0t" and r["season"] == 2023)
    assert r["n_rows"] == f.height
    assert r["ci_lo"] <= r["dcrps"] <= r["ci_hi"]
    assert out["reliability"]["2023:A1"]


def test_run_arms_refuses_without_a_passing_gate(tmp_path: Path) -> None:
    (tmp_path / "gate.json").write_text('{"draft": true, "gate_ok": true, "sha256": "x"}')
    with pytest.raises(RuntimeError, match="no passing gate"):
        ev.run_arms("nba.duckdb", "x", out_dir=tmp_path)
    (tmp_path / "gate.json").write_text('{"draft": false, "gate_ok": false, "sha256": "x"}')
    with pytest.raises(RuntimeError, match="no passing gate"):
        ev.run_arms("nba.duckdb", "x", out_dir=tmp_path)
