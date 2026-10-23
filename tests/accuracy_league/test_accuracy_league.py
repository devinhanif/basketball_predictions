# ruff: noqa: E501
"""Accuracy league tests on synthetic data (no production data, no network)."""

from __future__ import annotations

import gzip
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb

from nba.accuracy_league import report, scoring, sources

TIP_NAIVE = datetime(2026, 10, 21, 23, 0)  # 19:00 EDT on 10-21
TIP = TIP_NAIVE.replace(tzinfo=UTC)
GAME = sources.Game("G1", TIP, date(2026, 10, 21), (1, 2), True)
PRED = json.dumps({"p_ge_full": {"c": [190, 150, 100, 50, 10, 2], "n": 200}})


def _market(
    name: str,
    k: int,
    bid: str,
    ask: str,
    token: str = "26OCT21",
    word: str = "points",
    status: str = "active",
) -> dict[str, Any]:
    return {"event_ticker": f"KXNBAPTS-{token}AAABBB", "title": f"{name}: {k}+ {word}", "status": status,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask}  # fmt: skip


def _snap(root: Path, stamp: str, markets: list[dict[str, Any]]) -> None:
    d = root / "live_2026-10-21" / "kalshi" / "KXNBAPTS_p0"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{stamp}.json.gz", "wt") as f:
        json.dump({"markets": markets}, f)


def test_scoring_basics() -> None:
    assert math.isclose(scoring.brier(0.7, 1.0), 0.09)
    assert scoring.logloss(0.0, 1.0) == scoring.logloss(scoring.EPS, 1.0)  # clipped, finite
    rel = scoring.reliability([0.05, 0.05, 1.0], [0.0, 1.0, 1.0])
    assert rel[0]["n"] == 2 and rel[0]["obs"] == 0.5 and rel[9]["n"] == 1
    c = scoring.clustered_mean(["a", "a", "b", "b"], [1.0, 3.0, 2.0, 2.0])
    assert c["value"] == 2.0 and c["dates"] == 2 and c["lo"] <= 2.0 <= c["hi"]
    one = scoring.clustered_mean(["a", "a"], [1.0, 2.0])
    assert one["lo"] != one["lo"]  # a single date has no CI


def test_half_line_k() -> None:
    assert (
        sources.half_line_k(24.5) == 25
        and sources.half_line_k(24.0) is None
        and sources.half_line_k(None) is None
    )


def test_kalshi_last_snapshot_in_window_and_matching(tmp_path: Path) -> None:
    nm = {"anna": {10}, "bob": {11}, "cy": {12, 13}}
    mem = {10: {1}, 11: {9}, 12: {1}, 13: {1}}
    _snap(
        tmp_path, "20261021T224000Z", [_market("Anna", 10, "0.10", "0.20")]
    )  # before window: ignored
    _snap(
        tmp_path, "20261021T225100Z", [_market("Anna", 10, "0.30", "0.40")]
    )  # in window, not last
    _snap(tmp_path, "20261021T225900Z", [
        _market("Anna", 10, "0.40", "0.50"),
        _market("Anna", 15, "0.50", "0.50"),  # bid == ask: dropped
        _market("Anna", 20, "0.10", "0.20", token="26OCT22"),  # wrong date token: ignored
        _market("Bob", 10, "0.10", "0.20"),  # not on this game's team
        _market("Cy", 10, "0.10", "0.20"),  # ambiguous name
        _market("Zed", 10, "0.10", "0.20"),  # not in this game
        _market("Anna", 5, "0.10", "0.20", status="finalized"),
    ])  # fmt: skip
    _snap(tmp_path, "20261021T230000Z", [_market("Anna", 10, "0.01", "0.02")])  # at tip: excluded
    lines, drops = sources.kalshi_lines(GAME, tmp_path, nm, mem)
    assert list(lines) == [("G1", 10, "pts", 10)]
    assert math.isclose(lines[("G1", 10, "pts", 10)]["mid"], 0.45)
    assert (
        drops["bad_price"] == 1 and drops["not_on_game_team"] == 1 and drops["name_ambiguous"] == 1
    )
    assert (
        drops["name_not_in_this_game"] == 1 and drops["no_snapshot_in_window"] == 3
    )  # 3 series without files


def test_reads_pretip_only_and_semantics(tmp_path: Path) -> None:
    f = tmp_path / "reads.jsonl"
    base = {
        "game_id": "G1",
        "player": "Anna",
        "stat": "pts",
        "k": 10,
        "price_seen": 0.4,
        "note": "",
    }
    rows = [
        {**base, "ts_utc": "2026-10-21T20:00:00Z", "side": "no", "p_or_call": 0.8},  # P(>=10) = 0.2
        {**base, "ts_utc": "2026-10-21T23:00:01Z", "side": "yes", "p_or_call": 0.9},  # after tip
        {
            **base,
            "ts_utc": "2026-10-21T21:00:00Z",
            "k": 12,
            "side": "yes",
            "p_or_call": "no",
        },  # a call
        {
            **base,
            "ts_utc": "2026-10-21T21:00:00Z",
            "k": 14,
            "side": "over",
            "p_or_call": 62,
        },  # percent
        {**base, "ts_utc": "bad"},
        {
            **base,
            "ts_utc": "2026-10-21T21:00:00Z",
            "player": "Nobody",
            "side": "yes",
            "p_or_call": 0.5,
        },
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    got, drops = sources.load_reads(f, {"G1": GAME}, {"G1": {"anna": {10}}})
    assert (
        math.isclose(got[("G1", 10, "pts", 10)]["p"], 0.2)
        and got[("G1", 10, "pts", 10)]["kind"] == "p"
    )
    assert got[("G1", 10, "pts", 12)]["kind"] == "call" and got[("G1", 10, "pts", 12)]["p"] == 0.0
    assert math.isclose(got[("G1", 10, "pts", 14)]["p"], 0.62)
    assert drops == {"after_tip_rejected": 1, "malformed": 1, "name_not_in_this_game": 1}
    assert sources.load_reads(tmp_path / "missing.jsonl", {}, {}) == (
        {},
        {},
    )  # empty / absent is fine


def _db() -> Any:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes FLOAT, pts INT, reb INT, ast INT, fg3m INT)"
    )
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, prediction JSON)"
    )
    con.execute(
        "INSERT INTO games VALUES ('G1', DATE '2026-10-21', 2026, 1, 2, 100, 90), ('G2', DATE '2026-10-21', 2026, 3, 4, NULL, NULL)"
    )
    for gid in ("G1", "G2"):
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r1', TIMESTAMP '2026-10-21 20:00:00', ?, ?, 'props_context_residual', 'v', 'pts', 10, ?)",
            [gid, TIP_NAIVE, PRED],
        )
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r2', TIMESTAMP '2026-10-21 23:30:00', ?, ?, 'props_context_residual', 'v', 'pts', 10, ?)",
            [gid, TIP_NAIVE, json.dumps({"p_ge_full": {"c": [1], "n": 1}})],
        )  # post-tip: ignored
    con.execute("INSERT INTO player_game_stats VALUES ('G1', 10, 30.0, 12, 0, 0, 0)")
    return con


def test_collect_and_report_end_to_end(tmp_path: Path) -> None:
    con = _db()
    _snap(
        tmp_path,
        "20261021T225900Z",
        [_market("Anna", 3, "0.70", "0.80"), _market("Anna", 12, "0.40", "0.50")],
    )
    ros = lambda *_a: SimpleNamespace(membership={10: {1, 3}}, names={10: "Anna"})  # noqa: E731
    rows, meta = report.collect(
        con, None, date(2026, 10, 21), date(2026, 10, 21), kalshi_root=tmp_path,
        reads_path=tmp_path / "none.jsonl", names=lambda pid, fb: fb[pid], read_rosters=ros, roster_dir=tmp_path,
    )  # fmt: skip
    by = {(r["game_id"], r["k"]): r for r in rows}
    r = by[("G1", 3)]
    assert r["status"] == "scored" and r["y"] == 1.0
    assert math.isclose(r["p"]["kalshi"], 0.75) and math.isclose(
        r["p"]["model"], 100 / 200
    )  # c[2] = 100 of 200
    assert math.isclose(r["p"]["blend"], 0.25 * 0.5 + 0.75 * 0.75)
    assert by[("G1", 12)]["y"] == 1.0 and by[("G1", 12)]["p"]["model"] == 0.0  # k beyond the grid
    assert by[("G2", 3)]["status"] == "unsettled" and meta["unsettled_games"] == ["G2"]
    rep = report.build_report(rows, meta, date(2026, 10, 21), date(2026, 10, 21))
    assert rep["coverage"]["kalshi"] == {
        **rep["coverage"]["kalshi"],
        "priced": 4,
        "scored": 2,
        "unsettled": 2,
    }
    assert rep["cumulative"]["sources"]["model"]["vs_kalshi"]["brier"]["n"] == 2
    text = report.render(rep)
    assert "monitor, not a claim" in text and "frozen rules" in text
    json.dumps(rep, default=str)
