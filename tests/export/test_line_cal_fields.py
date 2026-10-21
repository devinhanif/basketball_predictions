# ruff: noqa: E501
"""``line_cal`` export field (docs/contracts/line_cal_export_v1.md): equals the parlay producer's p_cal
for the same DraftKings leg; NULL with a counted, per-row reason otherwise."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.export import line_cal_fields as lcf
from nba.export import rung_cal_fields as rcf
from nba.export.build import DB_NAME, build_export
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts
from nba.props import full_support
from tests.export import test_best_model as tb
from tests.export import test_rung_cal_fields as tr
from tests.export.test_best_model import db as db  # noqa: F401 - fixture re-export
from tests.parlay import test_top_source as tp

CAL = lc.load_map()
S199 = np.interp(np.linspace(0, 18, 199), np.arange(19), np.array(tp.q_grid()))
GRIDS = {(tp.GID, tp.PID, "pts"): (tp.MADE, "r1", [float(v) for v in S199])}
EARLY = tp.MADE - timedelta(minutes=30)


def _prod(**kw: Any) -> dict[str, Any]:
    surv = full_support.from_samples(S199, "pts")
    row = {
        "run_id": "r1", "lines_run_id": "r1", "game_id": tp.GID, "player_id": tp.PID, "stat": "pts",
        "model_name": rcf.PRODUCTION_PROP_MODEL, "made_at": tp.MADE, "tipoff": tp.TIP,
        "comparison_only": False, "p_ge_full_counts": list(surv["c"]), "p_ge_full_n": surv["n"],
        "prediction_json": json.dumps(tp.pred()),
    }  # fmt: skip
    return row | kw


def _board(line: float, at: datetime = EARLY) -> list[dict[str, Any]]:
    return [
        {
            "run_id": "r1",
            "game_id": tp.GID,
            "player_id": tp.PID,
            "stat": "pts",
            "book": "draftkings",
            "is_alternate": False,
            "unusable": False,
            "side": s,
            "line": line,
            "snapshot_ts": at,
        }  # fmt: skip
        for s in ("over", "under")
    ]


def test_p_over_cal_equals_the_parlay_producers_p_cal_for_the_same_leg() -> None:
    odds = tp.build_odds(
        [tp.quote("draftkings", "over", -115), tp.quote("draftkings", "under", -105)]
    )
    legs = ts.load_legs(
        tp.build_nba(), odds, tp.SLATE, now=tp.MADE + timedelta(minutes=5), calmap=CAL
    ).legs
    over = next(x for x in legs if x.side == "over" and x.book == "draftkings")
    row = [_prod()]
    out = lcf.attach(row, _board(22.5), GRIDS, CAL)
    cal = row[0]["line_cal"]
    assert cal is not None and row[0]["line_cal_null_reason"] is None
    assert cal["p_over_cal"] == over.p_cal and cal["p_over_raw"] == over.p_raw  # bit equal
    print("fixture row p_over_raw / p_over_cal:", cal["p_over_raw"], cal["p_over_cal"])
    assert (
        cal["book"] == "draftkings"
        and cal["main_line"] == 22.5
        and cal["map"] == "LINE_CALIBRATION"
    )
    assert cal["map_sha8"] == "443743a1" and cal["fit_through"] == "2025-06-30"
    assert cal["price_at"] == EARLY.isoformat(timespec="seconds") + "Z"
    assert out["n_line_cal"] == 1 and out["map_sha8"] == "443743a1"


def test_no_support_restriction_below_one_half() -> None:
    row = [_prod()]
    lcf.attach(row, _board(30.5), GRIDS, CAL)  # raw P(over) far below 0.5: rung_cal would omit it
    cal = row[0]["line_cal"]
    assert cal is not None and cal["p_over_raw"] < 0.5


def test_null_reasons_are_per_row_and_counted() -> None:
    out = lcf.attach(rows := [_prod()], [], GRIDS, CAL)
    assert rows[0]["line_cal"] is None and rows[0]["line_cal_null_reason"] == "no_book_line"
    assert out["null_reasons"]["no_book_line"] == 1 and out["n_line_cal"] == 0
    other = {(tp.GID, tp.PID, "pts"): (tp.MADE, "r1", [float(v) + 0.6 for v in S199])}
    out = lcf.attach(rows := [_prod()], _board(22.5), other, CAL)
    assert (
        rows[0]["line_cal_null_reason"] == "grid_mismatch"
        and out["null_reasons"]["grid_mismatch"] == 1
    )
    cases = {
        "no_production_grid": (_prod(), {}),
        "no_survival": (_prod(p_ge_full_counts=None), GRIDS),
        "no_starter_rate": (_prod(prediction_json="{}"), GRIDS),
        "whole_number_line": (_prod(), GRIDS),
        "no_quartiles": (_prod(prediction_json=json.dumps(tp.pred() | {"q_grid": [1.0]})), GRIDS),
    }
    for reason, (r, g) in cases.items():
        board = _board(22.0 if reason == "whole_number_line" else 22.5)
        out = lcf.attach(rows := [r], board, g, CAL)
        assert rows[0]["line_cal_null_reason"] == reason and out["null_reasons"][reason] == 1, (
            reason
        )
    rows = [_prod(model_name="props_avg_live2")]
    assert lcf.attach(rows, _board(22.5), GRIDS, CAL)["null_reasons"]["no_production_row"] == 1
    amb = _board(21.5) + _board(22.5)
    out = lcf.attach(rows := [_prod()], amb, GRIDS, CAL)
    assert rows[0]["line_cal_null_reason"] == "ambiguous_main_line"
    bad = lc.CalMap(CAL.meta, {})
    out = lcf.attach(rows := [_prod()], _board(22.5), GRIDS, bad)
    assert rows[0]["line_cal_null_reason"] == "map_unavailable"
    assert set(out["null_reasons"]) == set(lcf.REASONS)


def test_end_to_end_build_writes_fields_and_manifest_counts_match_rows(
    db: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    tr._db_with_grids(db)
    odds = tr._odds_db(tmp_path / "market_asof.duckdb", tb.TIP - timedelta(minutes=150))
    m = build_export(
        db, tmp_path / "out", tb.DAY, git=lambda: {"git_sha": "x", "git_dirty": False},
        version=lambda: "dv", now=lambda: datetime(2026, 11, 10, 20, 0),
        parlay_dir=tmp_path / "np", odds_db=odds,
    )  # fmt: skip
    c = duckdb.connect(str(tmp_path / "out" / DB_NAME), read_only=True)
    rows = c.execute("SELECT line_cal, line_cal_null_reason FROM prop_grids").fetchall()
    ok = [json.loads(r[0]) for r in rows if r[0] is not None]
    assert ok and m["line_cal"]["n_line_cal"] == len(ok)
    assert all(r[1] is None for r in rows if r[0] is not None)
    assert all(r[1] in lcf.REASONS for r in rows if r[0] is None)
    reasons = m["line_cal"]["null_reasons"]
    for k, n in reasons.items():
        assert n == sum(1 for r in rows if r[0] is None and r[1] == k), k
    assert sum(reasons.values()) + len(ok) == m["line_cal"]["n_props"] == len(rows)
    assert ok[0]["main_line"] == 19.5 and ok[0]["map_sha8"] == "443743a1"
    assert m["rung_cal"]["n_rung_cal"] >= 1  # rung_cal untouched


def test_a_later_row_of_the_prop_gets_the_production_rows_line_never_a_later_one() -> None:
    t15_made = tp.MADE + timedelta(minutes=30)
    late = t15_made - timedelta(minutes=5)  # after production's made_at, before the t15 row's
    board = _board(22.5, EARLY) + [
        q | {"run_id": "r2", "line": 24.5, "snapshot_ts": late} for q in _board(24.5, late)
    ]
    t15 = _prod(
        model_name="props_context_residual_t15", run_id="r2", lines_run_id="r2", made_at=t15_made
    )
    prod = _prod()
    rows = [t15, prod]
    out = lcf.attach(rows, board, GRIDS, CAL)
    assert rows[0]["line_cal"] == rows[1]["line_cal"] and rows[1]["line_cal"] is not None
    assert rows[1]["line_cal"]["main_line"] == 22.5  # never the 24.5 that postdates production
    # production's own run has no line at or before its made_at: null with that reason, on both rows
    rows = [_prod(), t15 | {"model_name": "props_context_residual_t15"}]
    out = lcf.attach(
        rows, _board(24.5, late) and [q | {"run_id": "r2"} for q in _board(24.5, late)], GRIDS, CAL
    )
    assert all(r["line_cal"] is None and r["line_cal_null_reason"] == "no_book_line" for r in rows)
    assert out["n_props"] == 2 and out["n_props_distinct"] == 1 and out["n_line_cal_distinct"] == 0
    out = lcf.attach(rows := [_prod(), t15], board, GRIDS, CAL)  # `<=`: a quote AT made_at counts
    assert out["n_line_cal"] == 2 and out["n_line_cal_distinct"] == 1
    at = _board(23.5, tp.MADE)
    out = lcf.attach(rows := [_prod()], at, GRIDS, CAL)
    assert rows[0]["line_cal"]["main_line"] == 23.5
