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

from nba.daily.avg_shadow import store as avg_store
from nba.export import rung_cal_fields as rcf
from nba.export.build import DB_NAME, build_export
from nba.parlay import rung_cal as rc
from tests.export import test_best_model as tb
from tests.export.test_best_model import db as db  # noqa: F401 - fixture re-export

TIP = datetime(2026, 11, 10, 23, 30)
MADE = TIP - timedelta(minutes=90)
PMF = [float(v) for v in np.repeat(np.arange(0, 40), 5)[:199]]
SURV_N = 199


def _counts(pmf: list[float] = PMF) -> list[int]:
    s = np.sort(np.asarray(pmf))
    return [int(SURV_N - np.searchsorted(s, k - 0.5, side="right")) for k in range(1, 41)]


def _prop(**kw: Any) -> dict[str, Any]:
    row = {
        "run_id": "r1", "lines_run_id": "r1", "game_id": "G", "player_id": 7, "stat": "pts",
        "model_name": "props_avg_live2", "made_at": MADE, "tipoff": TIP, "comparison_only": False,
        "p_ge_full_counts": _counts(), "p_ge_full_n": SURV_N, "_pmf199": PMF,
        "prediction_json": "{}",
    }  # fmt: skip
    return row | kw


def _prod(**kw: Any) -> dict[str, Any]:
    row = _prop(
        model_name=rcf.PRODUCTION_PROP_MODEL, prediction_json=json.dumps({"starter_rate": 0.9})
    )
    row.pop("_pmf199")
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


def test_prop_with_book_line_gets_monotone_supported_lines() -> None:
    props = [_prop(), _prod()]
    out = rcf.attach(props, _board(19.5, EARLY))
    p = props[0]
    assert p["book_main_line"] == 19.5 and "_pmf199" not in p
    rung = p["rung_cal"]
    assert rung["book"] == "draftkings" and rung["main_line"] == 19.5
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
    assert out["n_rung_cal"] == 1 and out["n_book_main_line"] == 2
    # the production row has the line but no continuous grid: NULL with the reason counted
    assert props[1]["book_main_line"] == 19.5 and props[1]["rung_cal"] is None
    assert out["null_reasons"]["no_continuous_grid"] == 1


def test_no_book_line_gives_null() -> None:
    props = [_prop(), _prod()]
    out = rcf.attach(props, [])
    assert all(p["book_main_line"] is None and p["rung_cal"] is None for p in props)
    assert out["null_reasons"]["no_book_line"] == 2 and out["n_rung_cal"] == 0


def test_book_line_stamped_after_made_at_gives_null() -> None:
    late = _board(19.5, MADE + timedelta(minutes=1))
    props = [_prop(), _prod()]
    rcf.attach(props, late)
    assert all(p["book_main_line"] is None and p["rung_cal"] is None for p in props)
    at_made = _board(19.5, MADE)  # strictly before made_at
    rcf.attach(props, at_made)
    assert props[0]["book_main_line"] is None


def test_one_sided_alternate_other_book_and_unusable_are_not_main_lines() -> None:
    q = [_q("over", 19.5, EARLY)]  # one side only
    q += _board(25.5, EARLY) and [_q(s, 25.5, EARLY, is_alternate=True) for s in ("over", "under")]
    q += [_q(s, 18.5, EARLY, book="fanduel") for s in ("over", "under")]
    q += [_q(s, 17.5, EARLY, unusable=True) for s in ("over", "under")]
    p = [_prop()]
    rcf.attach(p, q)
    assert p[0]["book_main_line"] is None


def test_newest_two_sided_line_wins_and_a_tie_is_ambiguous() -> None:
    older = _board(18.5, EARLY - timedelta(minutes=20))
    p = [_prop()]
    rcf.attach(p, older + _board(19.5, EARLY))
    assert p[0]["book_main_line"] == 19.5
    p = [_prop()]
    out = rcf.attach(p, _board(18.5, EARLY) + _board(19.5, EARLY))
    assert p[0]["book_main_line"] is None and out["null_reasons"]["ambiguous_main_line"] == 1


def test_missing_starter_rate_or_survival_gives_null_rung_cal_not_a_guess() -> None:
    p = [_prop()]  # no production row: no starter_rate
    out = rcf.attach(p, _board(19.5, EARLY))
    assert p[0]["book_main_line"] == 19.5 and p[0]["rung_cal"] is None
    assert out["null_reasons"]["no_starter_rate"] == 1
    p = [_prop(p_ge_full_counts=None), _prod()]
    out = rcf.attach(p, _board(19.5, EARLY))
    assert p[0]["rung_cal"] is None and out["null_reasons"]["no_survival"] == 1


def test_values_equal_the_serving_function_on_the_grid_median_and_spread() -> None:
    props = [_prop(), _prod()]
    rcf.attach(props, _board(19.5, EARLY))
    med, spr = rc.grid_med_spr(np.asarray(PMF))
    full = rc.rung_cal_lines(np.asarray(_counts()) / SURV_N, 19.5, 0.9, "pts", med, spr)
    assert full is not None
    served = {x["line"]: x["p_over"] for x in props[0]["rung_cal"]["lines"]}
    assert served == {x["line"]: x["p_over"] for x in full if x["line"] in served}


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


def test_end_to_end_build_writes_columns_and_manifest_counts(
    db: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    con = duckdb.connect(str(db))
    tb._insert_avg(con, tb._avg_row())
    avg_store.ensure_tables(con)
    con.execute(
        "UPDATE forward_predictions SET prediction = json_merge_patch(prediction::JSON, "
        "'{\"starter_rate\": 0.95}')::VARCHAR WHERE model_name = ?",
        [tb.PROD],
    )
    con.close()
    q = TIP - timedelta(minutes=150)
    odds = _odds_db(tmp_path / "market_asof.duckdb", q)
    out = tmp_path / "out"
    m = build_export(
        db, out, tb.DAY, git=lambda: {"git_sha": "x", "git_dirty": False}, version=lambda: "dv",
        now=lambda: datetime(2026, 11, 10, 20, 0), parlay_dir=tmp_path / "np", odds_db=odds,
    )  # fmt: skip
    c = duckdb.connect(str(out / DB_NAME), read_only=True)
    row = c.execute(
        "SELECT book_main_line, rung_cal FROM prop_grids WHERE is_primary AND game_id = ? "
        "AND player_id = 1000",
        [tb.G_REG],
    ).fetchone()
    assert row is not None and row[0] == 19.5
    rung = json.loads(row[1])
    assert rung["map_sha8"] == "116f4480" and rung["lines"]
    assert m["rung_cal"]["n_rung_cal"] == 1 and m["rung_cal"]["n_book_main_line"] >= 1
    nulls = c.execute("SELECT count(*) FROM prop_grids WHERE rung_cal IS NULL").fetchone()
    assert nulls is not None and nulls[0] > 0
