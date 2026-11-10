"""Line pairing for write-on-change forecasts (docs/design_notes/EXPORT_LINE_PAIRING.md).

A latest forecast row may pair quotes up to the ``made_at`` of the newest pretip run that covered
its game, because the store skips an unchanged group and the row is therefore identical at that
run. Never a quote after that run, after the tip, or, for a superseded row, after its own made_at.
Fixture DB only; no network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.daily import stamps
from nba.daily.store import ForwardPrediction, append_predictions_ex
from nba.export import Window, forward_predictions, prop_lines
from nba.export.reader import ForwardBundle, LinesBundle, newest_cover, open_read_only
from tests.fixtures.loader import build_fixture_db

D1, D2 = date(2026, 10, 28), date(2026, 10, 29)
TIP1 = datetime(2026, 10, 28, 23, 30)  # 7:30pm ET on D1
TIP2 = datetime(2026, 10, 29, 23, 30)  # 7:30pm ET on D2
G1, G2 = "0022600101", "0022600102"
CTX, PTS = "props_context_residual", "player_points"
Q = [float(i) for i in range(2, 21)]


def _pred(mean: float) -> dict[str, Any]:
    return {
        "mean": mean, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q10": 4.0, "q50": mean,
        "q90": 14.0, "q_grid": Q, "p_ge": {"10": 0.4}, "p_ge_full": {"c": [198, 150, 0], "n": 199},
    }  # fmt: skip


def _fp(game: str, tip: datetime, pid: int, mean: float) -> ForwardPrediction:
    return ForwardPrediction(
        model_name=CTX, version="v1", target="pts", game_id=game, player_id=pid,
        tipoff=tip, prediction=_pred(mean),
    )  # fmt: skip


def _stamp(con: duckdb.DuckDBPyConnection, run: str, made: datetime) -> None:
    con.execute(
        f"INSERT INTO {stamps.TABLE} VALUES (?, ?, 'v1', ?, 'abc', 'fitted', 'dv', "
        "DATE '2026-10-27', 5000, 'deadbeef', false, ?)",
        [run, CTX, made, made],
    )


def _src(tmp_path: Path) -> Path:
    path = tmp_path / "nba.duckdb"
    con = build_fixture_db(path)
    con.execute(stamps.DDL)
    con.executemany(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES (?,?,?,?,?,?,?)",
        [(G1, D1, 2026, 1610612738, 1610612755, None, None),
         (G2, D2, 2026, 1610612738, 1610612755, None, None)],
    )  # fmt: skip
    t = TIP1
    # D1 runs: r1 (T-180) writes both players; r2 (T-120) and r3 (T-60) re-run the same slate.
    # Player 1000 is unchanged at r2/r3 (deduped, no row); player 1001 changes at r3.
    r1, r2, r3 = t - timedelta(minutes=180), t - timedelta(minutes=120), t - timedelta(minutes=60)
    write = [
        ("r1", r1, [_fp(G1, TIP1, 1000, 12.5), _fp(G1, TIP1, 1001, 8.5)]),
        ("r2", r2, [_fp(G1, TIP1, 1000, 12.5), _fp(G1, TIP1, 1001, 8.5)]),
        ("r3", r3, [_fp(G1, TIP1, 1000, 12.5), _fp(G1, TIP1, 1001, 9.5)]),
    ]
    written = {}
    for run, made, preds in write:
        written[run] = append_predictions_ex(con, run, made, preds, dedup=True)
        _stamp(con, run, made)
    assert written == {"r1": (2, 0), "r2": (0, 2), "r3": (1, 1)}  # the dedup premise
    # D2: only one run, at T-180 (no later run for this game)
    q = TIP2 - timedelta(minutes=180)
    append_predictions_ex(con, "r4", q, [_fp(G2, TIP2, 1000, 15.5)], dedup=True)
    _stamp(con, "r4", q)
    con.close()
    return path


def _odds(path: Path, quotes: list[tuple[str, int, float, int, datetime, datetime]]) -> Path:
    """(game, player, point, price, received_at, tip) -> a capture store with the real DDL."""
    from nba.odds import live_close as lc

    con = duckdb.connect(str(path))
    con.execute(lc.DDL)
    for g, pid, point, price, recv, tip in quotes:
        con.execute(
            "INSERT INTO live_book_odds VALUES ('the_odds_api_live', ?, ?, ?, ?, ?, ?, ?, 'ev', "
            "?, ?, 'N', 'P', ?, ?, ?, ?, NULL, NULL, NULL, 'raw')",
            [g, D1, "t", recv, recv, tip, tip, "draftkings", PTS, pid, "over", point, price],
        )
    con.close()
    return path


def _paired(
    tmp_path: Path,
    mutate: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
    extra: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> tuple[ForwardBundle, LinesBundle]:
    src = _src(tmp_path)
    if mutate is not None:
        wc = duckdb.connect(str(src))
        mutate(wc)
        wc.close()
    t = TIP1
    quotes = [
        (G1, 1000, 10.5, -110, t - timedelta(minutes=170), TIP1),  # between r1 and r2
        (G1, 1000, 11.5, -110, t - timedelta(minutes=100), TIP1),  # after r2, before r3
        (G1, 1000, 12.5, -110, t - timedelta(minutes=50), TIP1),  # after the newest run r3
        (G1, 1000, 13.5, -110, t + timedelta(minutes=5), TIP1),  # after tip
        (G1, 1001, 10.5, -110, t - timedelta(minutes=190), TIP1),  # known at r1
        (G1, 1001, 11.5, -110, t - timedelta(minutes=100), TIP1),  # after r1 (superseded row)
        (G1, 1001, 12.5, -110, t - timedelta(minutes=50), TIP1),  # after r3
        (G2, 1000, 10.5, -110, TIP2 - timedelta(minutes=200), TIP2),  # known at r4
        (G2, 1000, 11.5, -110, TIP2 - timedelta(minutes=100), TIP2),  # after the only run
    ]
    odds = _odds(tmp_path / "market_asof.duckdb", quotes)
    con = open_read_only(src)
    oc = duckdb.connect(str(odds), read_only=True)
    try:
        fwd = forward_predictions(con, Window(D2, 1))
        props = extra(fwd.prop_grids) if extra is not None else fwd.prop_grids
        bundle = prop_lines(oc, props, Window(D2, 1))
    finally:
        oc.close()
        con.close()
    return fwd, bundle


def _by_run(bundle: LinesBundle) -> dict[tuple[str, str, int], list[float]]:
    out: dict[tuple[str, str, int], list[float]] = {}
    for r in bundle.rows:
        out.setdefault((r["run_id"], r["game_id"], r["player_id"]), []).append(r["line"])
    return {k: sorted(v) for k, v in out.items()}


def test_unchanged_latest_row_pairs_through_newest_run_only(tmp_path: Path) -> None:
    fwd, bundle = _paired(tmp_path)
    got = _by_run(bundle)
    # player 1000: row from r1 (deduped at r2, r3); newest covering run is r3 (T-60)
    assert got[("r1", G1, 1000)] == [10.5, 11.5]  # not 12.5 (after r3), not 13.5 (after tip)
    assert all(
        r["paired_through"] == TIP1 - timedelta(minutes=60)
        for r in bundle.rows
        if (r["run_id"], r["player_id"]) == ("r1", 1000)
    )
    rows = {(p["run_id"], p["player_id"]): p for p in fwd.prop_grids}
    assert rows[("r1", 1000)]["is_latest"] and rows[("r1", 1000)][
        "lines_through"
    ] == TIP1 - timedelta(minutes=60)
    assert all(r["minutes_before_tip"] > 0 for r in bundle.rows)  # every quote pre-tip
    assert all(r["snapshot_ts"] <= r["paired_through"] for r in bundle.rows)
    assert bundle.excluded_post_tip == 1  # the planted post-tip quote is counted, never exported


def test_changed_row_pairs_only_up_to_its_own_made_at(tmp_path: Path) -> None:
    _, bundle = _paired(tmp_path)
    got = _by_run(bundle)
    assert got[("r1", G1, 1001)] == [10.5]  # superseded at r3: cutoff stays its own made_at (T-180)
    assert got[("r3", G1, 1001)] == [10.5, 11.5]  # the new row: through r3 = its own made_at (T-60)
    assert 12.5 not in got[("r3", G1, 1001)]  # after the newest run


def test_game_with_no_later_run_is_unchanged(tmp_path: Path) -> None:
    fwd, bundle = _paired(tmp_path)
    got = _by_run(bundle)
    assert got[("r4", G2, 1000)] == [10.5]  # only quotes at or before its own made_at
    p = next(p for p in fwd.prop_grids if p["run_id"] == "r4")
    assert p["lines_through"] == p["made_at"]


def test_newest_cover_ignores_post_tip_other_date_and_empty() -> None:
    r_ok = TIP1 - timedelta(minutes=60)
    r_post = TIP1 + timedelta(minutes=1)
    r_other_date = TIP1 - timedelta(days=1, minutes=60)  # ET date D1 - 1
    assert newest_cover([r_ok, r_post, r_other_date], D1, TIP1) == r_ok
    assert newest_cover([r_post, r_other_date], D1, TIP1) is None
    assert newest_cover([], D1, TIP1) is None
    assert newest_cover([TIP1], D1, TIP1) is None  # made_at == tip is not pre-tip


def test_avg_row_in_the_same_run_does_not_collapse_the_extension(tmp_path: Path) -> None:
    """B1: the AVG shadow shares the production run_id and keeps lines_through = made_at."""

    def with_avg(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prod = next(p for p in props if p["run_id"] == "r1" and p["player_id"] == 1000)
        avg = dict(prod, model_name="props_avg_live2", lines_through=prod["made_at"])
        return [*props, avg]

    _, bundle = _paired(tmp_path, extra=with_avg)
    assert _by_run(bundle)[("r1", G1, 1000)] == [10.5, 11.5]  # still through r3


def test_cal_rows_keep_the_old_rule(tmp_path: Path) -> None:
    """B2: build._read_lines hands rung_cal / line_cal rows cut at each row's own made_at."""
    from nba.export.build import _read_lines

    src = _src(tmp_path)
    odds = _odds(
        tmp_path / "market_asof.duckdb",
        [(G1, 1000, 10.5, -110, TIP1 - timedelta(minutes=190), TIP1),
         (G1, 1000, 10.5, -150, TIP1 - timedelta(minutes=100), TIP1)],
    )  # fmt: skip
    con = open_read_only(src)
    try:
        props = forward_predictions(con, Window(D2, 1)).prop_grids
    finally:
        con.close()
    lines, cal = _read_lines(props, Window(D2, 1), odds, None)
    assert all(r["snapshot_ts"] <= r["made_at"] for r in cal.rows)
    assert all(r["paired_through"] == r["made_at"] for r in cal.rows)
    r1_cal = [r for r in cal.rows if r["run_id"] == "r1" and r["player_id"] == 1000]
    assert [r["price_american"] for r in r1_cal] == [-110]  # the pre-made_at quote survives
    r1_new = [r for r in lines.rows if r["run_id"] == "r1" and r["player_id"] == 1000]
    assert [r["price_american"] for r in r1_new] == [-150]  # prop_lines shows the newer one


def test_player_out_on_the_report_is_not_extended(tmp_path: Path) -> None:
    """M1: OUT at the covering run's time on the official report: the cutoff stays made_at."""
    from nba.daily.injury import to_report_dt

    def out(con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            "INSERT INTO player_availability VALUES "
            "(1000, ?, ?, 'out', 'x', 'nba_official_report', ?)",
            [to_report_dt(TIP1 - timedelta(minutes=65)), G1, TIP1 - timedelta(minutes=65)],
        )

    fwd, bundle = _paired(tmp_path, mutate=out)
    p = next(p for p in fwd.prop_grids if p["run_id"] == "r1" and p["player_id"] == 1000)
    assert p["lines_through"] == p["made_at"]
    assert ("r1", G1, 1000) not in _by_run(bundle)  # nothing before r1's made_at (T-180)
    assert _by_run(bundle)[("r3", G1, 1001)] == [10.5, 11.5]  # 1001 is not out: unaffected


def test_cover_is_per_model_and_ignores_late_runs(tmp_path: Path) -> None:
    """M2: another model's later stamp, a late_ run and an unstamped model are not cover."""

    def mut(con: duckdb.DuckDBPyConnection) -> None:
        con.execute(f"DELETE FROM {stamps.TABLE} WHERE run_id = 'r3'")  # CTX did not run at r3
        t3 = TIP1 - timedelta(minutes=60)
        con.execute(  # the same run stamped a different model: not cover for CTX
            f"INSERT INTO {stamps.TABLE} VALUES ('r3', 'rung0_mov_elo', 'v1', ?, 'a', 'f', 'dv', "
            "DATE '2026-10-27', 1, 'g', false, ?)",
            [t3, t3],
        )
        t5 = TIP1 - timedelta(minutes=30)
        con.execute(  # a late_ run stamped CTX: not cover
            f"INSERT INTO {stamps.TABLE} VALUES ('late_x', ?, 'v1', ?, 'a', 'f', 'dv', "
            "DATE '2026-10-27', 1, 'g', false, ?)",
            [CTX, t5, t5],
        )
        pred = ForwardPrediction(
            G1, TIP1, "props_context_residual_t15", "v1", "pts", _pred(7.5), 1002
        )
        append_predictions_ex(con, "r6", TIP1 - timedelta(minutes=170), [pred])  # never stamped

    fwd, bundle = _paired(tmp_path, mutate=mut)
    rows = {(p["run_id"], p["model_name"], p["player_id"]): p for p in fwd.prop_grids}
    assert rows[("r1", CTX, 1000)]["lines_through"] == TIP1 - timedelta(minutes=120)  # r2, not r3
    t15 = rows[("r6", "props_context_residual_t15", 1002)]
    assert t15["lines_through"] == t15["made_at"]
    assert _by_run(bundle)[("r1", G1, 1000)] == [10.5]  # the T-100 quote is after r2
