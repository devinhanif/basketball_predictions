# ruff: noqa: E501
"""``book_main_line`` / ``rung_cal`` export fields (docs/contracts/rung_cal_export_v1.md, amended
2026-10-15): monotone supported lines only, NULL without an exact input, nothing post made_at."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.daily import grid199
from nba.daily.avg_shadow import store as avg_store
from nba.export import rung_cal_fields as rcf
from nba.export.build import DB_NAME, build_export
from nba.parlay import rung_cal as rc
from nba.props import full_support
from tests.export import test_best_model as tb
from tests.export.test_best_model import db as db  # noqa: F401 - fixture re-export

TIP = datetime(2026, 11, 10, 23, 30)
MADE = TIP - timedelta(minutes=90)
PMF = [float(v) for v in np.repeat(np.arange(0, 40), 5)[:199]]
SURV_N = 199


def _counts(pmf: list[float] = PMF) -> list[int]:
    return list(full_support.from_samples(np.asarray(pmf), "pts")["c"])


GRID = (MADE, "r1", PMF)
GRIDS = {("G", 7, "pts"): GRID}


def _prop(**kw: Any) -> dict[str, Any]:
    row = {
        "run_id": "r1", "lines_run_id": "r1", "game_id": "G", "player_id": 7, "stat": "pts",
        "model_name": "props_avg_live2", "made_at": MADE, "tipoff": TIP, "comparison_only": False,
        "p_ge_full_counts": _counts(), "p_ge_full_n": SURV_N, "prediction_json": "{}",
    }  # fmt: skip
    return row | kw


def _prod(**kw: Any) -> dict[str, Any]:
    row = _prop(
        model_name=rcf.PRODUCTION_PROP_MODEL, prediction_json=json.dumps({"starter_rate": 0.9})
    )
    return row | kw


def _q(side: str, line: float, at: datetime, book: str = "draftkings", **kw: Any) -> dict[str, Any]:
    row = {
        "run_id": "r1", "game_id": "G", "player_id": 7, "stat": "pts", "book": book,
        "is_alternate": False, "unusable": False, "side": side, "line": line, "snapshot_ts": at,
    }  # fmt: skip
    return row | kw


def _board(line: float, at: datetime) -> list[dict[str, Any]]:
    return [_q("over", line, at), _q("under", line, at)]


EARLY = MADE - timedelta(minutes=30)


def test_prop_with_book_line_gets_monotone_supported_lines_from_the_production_grid() -> None:
    avg, prod = _prop(), _prod()  # the displayed row is AVG; production supplies the inputs
    out = rcf.attach([avg, prod], _board(19.5, EARLY), GRIDS)
    assert avg["book_main_line"] == 19.5
    rung = avg["rung_cal"]
    assert rung["book"] == "draftkings" and rung["main_line"] == 19.5
    assert rung["source"] == "props_context_residual"
    assert rung["map_version"] == "rung_cal_map_v1" and rung["map_sha8"] == "116f4480"
    assert rung["price_at"] == EARLY.isoformat(timespec="seconds") + "Z"
    lines = rung["lines"]
    assert lines and all(
        b["line"] - a["line"] == 1.0 for a, b in zip(lines, lines[1:], strict=False)
    )
    ps = [x["p_over"] for x in lines]
    assert ps == sorted(ps, reverse=True)
    counts = _counts()
    for x in lines:  # every served line has raw P(over) >= 0.5 (the fit support)
        assert counts[int(x["line"] + 0.5) - 1] / SURV_N >= 0.5
    assert max(x["line"] for x in lines) < 20  # median is ~19.5: nothing above it is served
    assert prod["rung_cal"] == rung  # the same production inputs on the production row
    assert (
        out["n_rung_cal"] == 2
        and out["n_book_main_line"] == 2
        and out["source"] == "props_context_residual"
    )


def test_values_equal_the_serving_function_on_the_persisted_grid() -> None:
    props = [_prop(), _prod()]
    rcf.attach(props, _board(19.5, EARLY), GRIDS)
    med, spr = rc.grid_med_spr(np.asarray(PMF))
    full = rc.rung_cal_lines(np.asarray(_counts()) / SURV_N, 19.5, 0.9, "pts", med, spr)
    assert full is not None and props[0]["rung_cal"]["lines"] == full


def test_an_avg_grid_is_never_used() -> None:
    avg = _prop(_pmf199=PMF)  # even a stray AVG grid on the row is ignored
    out = rcf.attach([avg, _prod()], _board(19.5, EARLY), {})
    assert avg["rung_cal"] is None and avg["book_main_line"] == 19.5
    assert out["null_reasons"]["no_production_grid"] == 2


def test_no_book_line_gives_null() -> None:
    props = [_prop(), _prod()]
    out = rcf.attach(props, [], GRIDS)
    assert all(p["book_main_line"] is None and p["rung_cal"] is None for p in props)
    assert out["null_reasons"]["no_book_line"] == 2 and out["n_rung_cal"] == 0


def test_book_line_stamped_after_made_at_gives_null() -> None:
    props = [_prop(), _prod()]
    rcf.attach(props, _board(19.5, MADE + timedelta(minutes=1)), GRIDS)
    assert all(p["book_main_line"] is None and p["rung_cal"] is None for p in props)
    rcf.attach(props, _board(19.5, MADE), GRIDS)  # strictly before made_at
    assert props[0]["book_main_line"] is None


def test_one_sided_alternate_other_book_and_unusable_are_not_main_lines() -> None:
    q = [_q("over", 19.5, EARLY)]  # one side only
    q += [_q(s, 25.5, EARLY, is_alternate=True) for s in ("over", "under")]
    q += [_q(s, 18.5, EARLY, book="fanduel") for s in ("over", "under")]
    q += [_q(s, 17.5, EARLY, unusable=True) for s in ("over", "under")]
    p = [_prop()]
    rcf.attach(p, q, GRIDS)
    assert p[0]["book_main_line"] is None


def test_newest_two_sided_line_wins_and_a_tie_is_ambiguous() -> None:
    older = _board(18.5, EARLY - timedelta(minutes=20))
    p = [_prop()]
    rcf.attach(p, older + _board(19.5, EARLY), GRIDS)
    assert p[0]["book_main_line"] == 19.5
    p = [_prop()]
    out = rcf.attach(p, _board(18.5, EARLY) + _board(19.5, EARLY), GRIDS)
    assert p[0]["book_main_line"] is None and out["null_reasons"]["ambiguous_main_line"] == 1


def test_missing_inputs_give_null_rung_cal_with_a_counted_reason() -> None:
    q = _board(19.5, EARLY)
    p = [_prop()]  # no production row
    out = rcf.attach(p, q, GRIDS)
    assert p[0]["book_main_line"] == 19.5 and p[0]["rung_cal"] is None
    assert out["null_reasons"]["no_production_row"] == 1
    p = [_prop(), _prod()]  # no persisted grid (a run before the persistence, or a recency row)
    assert rcf.attach(p, q, {})["null_reasons"]["no_production_grid"] == 2
    p = [_prop(), _prod(prediction_json="{}")]  # no stored starter_rate: no guess
    assert rcf.attach(p, q, GRIDS)["null_reasons"]["no_starter_rate"] == 2
    p = [_prop(), _prod(p_ge_full_counts=None)]
    assert rcf.attach(p, q, GRIDS)["null_reasons"]["no_survival"] == 2
    other = {
        ("G", 7, "pts"): (MADE, "r1", [v + 0.6 for v in PMF])
    }  # not the grid the row was cut from
    p = [_prop(), _prod()]
    assert rcf.attach(p, q, other)["null_reasons"]["grid_mismatch"] == 2
    assert all(x["rung_cal"] is None for x in p)


def _db_with_grids(db: Path) -> None:  # noqa: F811
    con = duckdb.connect(str(db))
    tb._insert_avg(con, tb._avg_row())
    avg_store.ensure_tables(con)
    surv = full_support.from_samples(np.asarray(PMF), "pts")
    patch = json.dumps({"starter_rate": 0.95, "p_ge_full": surv})
    con.execute(
        "UPDATE forward_predictions SET prediction = json_merge_patch(prediction::JSON, ?::JSON)::VARCHAR "
        "WHERE model_name = ? AND target = 'pts'",
        [patch, tb.PROD],
    )
    grid199.ensure_table(con)
    for gid in (tb.G_REG, tb.G_POST):
        for pid in (1000, 1001, 1002):
            con.execute(
                f"INSERT INTO {grid199.TABLE} VALUES ('r_prod', ?, ?, ?, ?, 'pts', ?, ?, ?)",
                [tb.MADE, gid, tb.TIP, pid, tb.PROD, PMF, grid199.grid_sha(PMF)],
            )
    con.close()


def _odds_db(path: Path, recv: datetime) -> Path:
    """A capture store (the real DDL) with one two-sided DraftKings Points board at 19.5."""
    from nba.odds import live_close as lc

    con = duckdb.connect(str(path))
    con.execute(lc.DDL)
    for side in ("over", "under"):
        con.execute(
            "INSERT INTO live_book_odds VALUES ('the_odds_api_live', ?, ?, 't180', ?, ?, ?, ?, 'ev', "
            "'draftkings', 'player_points', 'N', 'P', ?, ?, 19.5, -110, NULL, NULL, NULL, 'raw')",
            [tb.G_REG, tb.DAY, recv, recv, tb.TIP, tb.TIP, 1000, side],
        )
    con.close()
    return path


def test_end_to_end_build_primary_avg_row_carries_production_calibration(
    db: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    _db_with_grids(db)
    odds = _odds_db(tmp_path / "market_asof.duckdb", TIP - timedelta(minutes=150))
    out = tmp_path / "out"
    m = build_export(
        db, out, tb.DAY, git=lambda: {"git_sha": "x", "git_dirty": False}, version=lambda: "dv",
        now=lambda: datetime(2026, 11, 10, 20, 0), parlay_dir=tmp_path / "np", odds_db=odds,
    )  # fmt: skip
    c = duckdb.connect(str(out / DB_NAME), read_only=True)
    row = c.execute(
        "SELECT model_name, book_main_line, rung_cal FROM prop_grids WHERE is_primary AND "
        "game_id = ? AND player_id = 1000 AND stat = 'pts'",
        [tb.G_REG],
    ).fetchone()
    assert row is not None and row[0] == tb.AVG_MODEL_NAME and row[1] == 19.5  # AVG is displayed
    rung = json.loads(row[2])
    assert rung["source"] == "props_context_residual" and rung["map_sha8"] == "116f4480"
    assert rung["lines"]
    assert m["rung_cal"]["n_rung_cal"] >= 1 and m["rung_cal"]["source"] == "props_context_residual"
    nulls = c.execute("SELECT count(*) FROM prop_grids WHERE rung_cal IS NULL").fetchone()
    assert nulls is not None and nulls[0] > 0
    # an export of a database without the grid table: nothing is approximated, all NULL, no crash
    con = duckdb.connect(str(db))
    con.execute(f"DROP TABLE {grid199.TABLE}")
    con.close()
    m2 = build_export(
        db, tmp_path / "out2", tb.DAY, git=lambda: {"git_sha": "x", "git_dirty": False},
        version=lambda: "dv", now=lambda: datetime(2026, 11, 10, 20, 0),
        parlay_dir=tmp_path / "np", odds_db=odds,
    )  # fmt: skip
    assert (
        m2["rung_cal"]["n_rung_cal"] == 0
        and m2["rung_cal"]["null_reasons"]["no_production_grid"] > 0
    )
