# ruff: noqa: E501
"""Scorecard tests on a small synthetic DuckDB (no production data)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from nba.forward import scorecard as sc

TIP = datetime(2026, 10, 21, 23, 0)
MADE = datetime(2026, 10, 21, 20, 0)


def _pred(mean: float, proj_min: float, c: list[int] | None = None) -> str:
    return json.dumps({
        "mean": mean, "proj_minutes": proj_min, "p_play": 0.95, "starter_rate": 0.5,
        "p_ge_full": {"c": c or [190, 150, 100, 50, 10, 2], "n": 200},
    })  # fmt: skip


def make_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT)"
    )
    con.execute(
        "CREATE TABLE forward_scores (scored_at TIMESTAMP, game_id VARCHAR, game_date DATE, season INT,"
        " model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, made_at TIMESTAMP,"
        " y DOUBLE, pred DOUBLE, log_loss DOUBLE, brier DOUBLE, crps DOUBLE, status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT,"
        " prediction JSON)"
    )
    games = [
        ("G1", date(2026, 10, 21), 2026, 1, 2),
        ("G2", date(2026, 10, 22), 2026, 3, 4),
        ("OLD", date(2025, 11, 1), 2025, 1, 2),  # the 2025-26 holdout season
    ]
    for g in games:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?)", list(g))
    # player 10: last pre-season team 1, plays for team 5 tonight (new team); player 11 stays on 1.
    box = [("OLD", 10, 1, 30.0), ("OLD", 11, 1, 30.0), ("G1", 10, 5, 30.0), ("G1", 11, 1, 20.0),
           ("G2", 10, 5, 36.0), ("G2", 11, 3, 10.0)]  # fmt: skip
    for b in box:
        con.execute("INSERT INTO player_game_stats VALUES (?,?,?,?)", list(b))
    rows = [
        # game, player, y, mean, proj_min
        ("G1", 10, 30.0, 20.0, 30.0), ("G1", 11, 8.0, 10.0, 25.0),
        ("G2", 10, 24.0, 18.0, 30.0), ("G2", 11, 2.0, 6.0, 18.0),
        ("OLD", 10, 99.0, 1.0, 30.0),  # season 2025 row: must never be read
    ]  # fmt: skip
    for g, p, y, mean, pm in rows:
        gd = {"G1": date(2026, 10, 21), "G2": date(2026, 10, 22), "OLD": date(2025, 11, 1)}[g]
        con.execute(
            "INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [TIP, g, gd, 2026, sc.PROD, "v", "pts", p, MADE, y, mean, None, None, 1.5, "scored"],
        )
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ["r1", MADE, g, TIP, sc.PROD, "v", "pts", p, _pred(mean, pm)],
        )
    for g, m, b, ll in [("G1", "rung0_injury_elo", 0.2, 0.6), ("G2", "rung0_injury_elo", 0.1, 0.4)]:
        con.execute(
            "INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [TIP, g, date(2026, 10, 21), 2026, m, "v", "win_prob_home", -1, MADE, 1.0, 0.6, ll, b,
             None, "scored"],
        )  # fmt: skip
    con.close()


def make_odds(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, slate_date DATE, snapshot_kind VARCHAR,"
        " received_at TIMESTAMP, tipoff TIMESTAMP, book VARCHAR, market VARCHAR, player_id INT,"
        " side VARCHAR, point DOUBLE, price_american INT)"
    )
    rec = datetime(2026, 10, 21, 22, 58)

    def add(book: str, kind: str, side: str, pt: float, price: int, pid: int = 10) -> None:
        con.execute(
            "INSERT INTO live_book_odds VALUES ('G1', ?, ?, ?, ?, ?, 'player_points', ?, ?, ?, ?)",
            [date(2026, 10, 21), kind, rec, TIP, book, pid, side, pt, price],
        )

    for b in ("a", "b", "c"):
        add(b, "close", "over", 25.5, -110)
        add(b, "close", "under", 25.5, -110)
    add("d", "close", "over", 26.5, 100)  # a minority point
    add("d", "close", "under", 26.5, -120)
    add("a", "t90", "over", 22.5, -105)  # an earlier snapshot, ignored when a close exists
    add("a", "t90", "under", 22.5, -115)
    # player 11: no close, only a pre-tip snapshot
    add("a", "t10", "over", 9.5, -130, 11)
    add("a", "t10", "under", 9.5, 110, 11)
    con.close()


# ------------------------------------------------------------------ pure helpers


def test_clustering_two_games() -> None:
    gids = ["g1", "g1", "g2", "g2"]
    vals = np.array([[1.0, 1.0], [3.0, 1.0], [10.0, 1.0], [14.0, 1.0]])
    v, lo, hi = sc.cluster_ci(gids, vals, sc.mean_fn)
    assert v == 7.0
    # resampling whole games: every draw is a mean over {g1, g2} with weights, so within [2, 12]
    assert 2.0 <= lo <= hi <= 12.0
    assert lo < v < hi
    # deterministic with the fixed seed
    assert sc.cluster_ci(gids, vals, sc.mean_fn) == (v, lo, hi)
    # one cluster: no CI
    v1, lo1, hi1 = sc.cluster_ci(["g1", "g1"], vals[:2], sc.mean_fn)
    assert v1 == 2.0 and np.isnan(lo1) and np.isnan(hi1)


def test_decomposition_identity() -> None:
    for y, mean, pm, am in [
        (38.0, 15.2, 32.4, 30.6),
        (0.0, 3.0, 20.0, 5.0),
        (7.0, 2.2, 10.0, 40.0),
    ]:
        d = sc.decompose(y, mean, pm, am)
        assert abs(d["miss_minutes"] + d["miss_rate"] + d["cross"] - (y - mean)) < 1e-12
        assert abs(d["cross"]) < 1e-12
        assert abs(d["miss_minutes"] - (am - pm) * (mean / pm)) < 1e-12


def test_team_flag() -> None:
    assert sc.team_flag(1, 5) == "new team"
    assert sc.team_flag(1, 1) == "same team"
    assert sc.team_flag(None, 1) == "no history"


def test_devig_and_prices() -> None:
    assert abs(sc.american_to_prob(-110) - 110 / 210) < 1e-12
    assert abs(sc.american_to_prob(100) - 0.5) < 1e-12
    p = sc.devig(sc.american_to_prob(-130), sc.american_to_prob(110))
    po, pu = 130 / 230, 100 / 210
    assert abs(p - po / (po + pu)) < 1e-12
    assert abs(sc.devig(0.55, 0.55) - 0.5) < 1e-12


def test_grid_convention_half_line() -> None:
    from nba.parlay.top_source import p_over

    pred = {"p_ge_full": {"c": [190, 150, 100, 50], "n": 200}}  # c[k-1] = P(X >= k) * n
    assert p_over(pred, 2.5) == 100 / 200  # 2.5 reads P(X >= 3)
    row = sc.build_rows(
        [{"game_id": "g", "game_date": date(2026, 10, 21), "stat": "pts", "player_id": 1, "y": 3.0,
          "crps": 1.0, "mean": 3.1, "proj_min": 20.0, "p_play": 0.9, "starter_rate": 0.5,
          "pred": pred}],
        {("g", 1): (20.0, 1)},
        {("g", 1, "pts"): {"line": 2.5, "p_over_devig": 0.5, "n_books": 3, "kind": "close"}},
        {1: 1}, {},
    )[0]  # fmt: skip
    assert row["lean"] == "over" and row["mkt"]["p_model"] == 0.5 and row["mkt"]["hit"] == 1.0
    assert sc.lean_side(2.4, 2.5) == "under" and sc.lean_side(2.4, None) == sc.NOLINE


def test_buckets() -> None:
    assert [sc.bucket_minutes(x) for x in (None, 10, 15, 25, 32)] == [
        "no proj minutes", "<15", "15-25", "25-32", "32+",
    ]  # fmt: skip
    assert [sc.bucket_pplay(x) for x in (0.5, 0.75, 0.9)] == ["<0.75", "0.75-0.9", "0.9+"]
    assert [sc.bucket_prior(n) for n in (0, 1, 5, 6)] == ["0", "1-5", "1-5", "6+"]


def test_fmt_cell_too_few() -> None:
    r = {"value": 1.0, "lo": 0.5, "hi": 1.5, "n": 3, "games": 2}
    assert sc.fmt_cell(r, 2) == "1.00 [0.50, 1.50] n=3 (2g) too few"
    r = {"value": 1.0, "lo": float("nan"), "hi": float("nan"), "n": 9, "games": 1}
    assert "[n/a]" in sc.fmt_cell(r, 2) and "too few" not in sc.fmt_cell(r, 2)


# ------------------------------------------------------------------ database paths


def _report(tmp_path: Path, **kw: Any) -> dict[str, Any]:
    make_db(tmp_path / "nba.duckdb")
    make_odds(tmp_path / "odds.duckdb")
    con = duckdb.connect(str(tmp_path / "nba.duckdb"), read_only=True)
    odds = duckdb.connect(str(tmp_path / "odds.duckdb"), read_only=True)
    return sc.build_report(
        con, odds, date(2026, 10, 22), date(2026, 10, 21), made_at=datetime(2026, 10, 22), **kw
    )


def test_season_2025_never_read(tmp_path: Path) -> None:
    # even asked for since=2025-01-01, the fixture OLD row (2025-11-01) is excluded everywhere
    rep = _report(tmp_path)
    assert rep["counts"]["rows_scored"] == 4
    (tmp_path / "second").mkdir()
    con = duckdb.connect(str(tmp_path / "nba.duckdb"), read_only=True)
    rows, _ = sc.load_scored(con, date(2025, 1, 1), date(2026, 10, 22))
    assert {r["game_id"] for r in rows} == {"G1", "G2"}
    assert 99.0 not in [r["y"] for r in rows]
    assert set(sc.load_box(con, date(2025, 1, 1), date(2026, 10, 22))) <= {
        ("G1", 10), ("G1", 11), ("G2", 10), ("G2", 11),
    }  # fmt: skip
    assert all(
        w["game_id"] != "OLD" for w in sc.load_win(con, date(2025, 1, 1), date(2026, 10, 22))
    )
    # same-season games played exclude the 2025 game
    assert all(
        d >= sc.SEASON_START
        for ds in sc.season_games_before(con, date(2026, 10, 22)).values()
        for d in ds
    )
    # --no-prior-team: nothing outside this season is touched
    rep2 = _report(tmp_path / "second", use_prior_team=False)
    assert rep2["fields"]["prior_team"] == "disabled"


def test_report_end_to_end(tmp_path: Path) -> None:
    rep = _report(tmp_path)
    assert rep["counts"]["games"] == 2
    flags = rep["part1"]["slices"]["team_flag"]
    # player 10 is on team 5 (last team 1) in both games; player 11 is on team 1 in G1 (same) and
    # on team 3 in G2 (new)
    assert flags["new team"]["bias"]["n"] == 3
    assert flags["same team"]["bias"]["n"] == 1
    assert "no history" not in flags
    assert rep["part1"]["slices"]["prior_games"]["0"]["bias"]["n"] == 2  # first game for both
    assert rep["part1"]["slices"]["prior_games"]["1-5"]["bias"]["n"] == 2
    # decomposition identity over the whole report: shares add to 1
    sh = rep["part2"]["all"]
    tot = sh["minutes_share"]["value"] + sh["rate_share"]["value"] + sh["cross_share"]["value"]
    assert abs(tot - 1.0) < 1e-9
    # lines: player 10 in G1 -> consensus 25.5 (3 books) not 26.5; player 11 -> pre-tip snapshot 9.5
    assert rep["counts"]["rows_with_closing_line"] == 2
    assert rep["counts"]["rows_with_box_minutes"] == 4
    p3 = rep["part3"]["all"]
    assert p3["hit_rate"]["n"] == 2
    # p10 (G1): mean 20 < 25.5 -> under; y=30 -> miss; k=26 > len(c) so P(over)=0, model P(under)=1,
    # market 0.5. p11 (G1): mean 10 > 9.5 -> over; y=8 -> miss; k=10 > len(c) so model P(over)=0.
    assert p3["hit_rate"]["value"] == 0.0
    assert abs(p3["brier_model"]["value"] - 0.5) < 1e-12  # (1-0)^2 and (0-0)^2
    assert 0.2 < p3["brier_market"]["value"] < 0.45
    json.dumps(rep, default=str)
    assert "Live forward scorecard" in sc.render(rep)


def test_closing_line_consensus_and_devig(tmp_path: Path) -> None:
    make_odds(tmp_path / "odds.duckdb")
    odds = duckdb.connect(str(tmp_path / "odds.duckdb"), read_only=True)
    ln = sc.load_closing_lines(odds, ["G1"], date(2026, 10, 22))
    a = ln[("G1", 10, "pts")]
    assert a["line"] == 25.5 and a["n_books"] == 3 and a["kind"] == "close"
    assert abs(a["p_over_devig"] - 0.5) < 1e-12
    b = ln[("G1", 11, "pts")]
    assert b["line"] == 9.5 and b["kind"] == "latest_pre_tip"
    po, pu = sc.american_to_prob(-130), sc.american_to_prob(110)
    assert abs(b["p_over_devig"] - po / (po + pu)) < 1e-12
