"""desk_export v1 producer: synthetic duckdb stores, no live files."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.export import desk

PLAYS_DDL = (
    "CREATE TABLE plays (play_id VARCHAR, bundle_id VARCHAR, leg_idx INTEGER, ts TIMESTAMP, "
    "slate_date DATE, game_id VARCHAR, matchup VARCHAR, tipoff TIMESTAMP, source VARCHAR, "
    "venue VARCHAR, ticker VARCHAR, side VARCHAR, kind VARCHAR, team_id INTEGER, "
    "player_id INTEGER, stat VARCHAR, threshold DOUBLE, price DOUBLE, max_price DOUBLE, "
    "contracts INTEGER, stake_usd DOUBLE, edge_label VARCHAR, edge_cents DOUBLE, "
    "fair_prob DOUBLE, fee_cents DOUBLE, model_prob DOUBLE, reason VARCHAR, "
    "explanation VARCHAR, dedupe_key VARCHAR, input_hash VARCHAR, delivered VARCHAR, "
    "pin_prob DOUBLE, w_model DOUBLE, leg_desc VARCHAR)"
)


def _mk_book(path: Path, dates: tuple[str, ...] = ("2026-11-01", "2026-11-02")) -> None:
    con = duckdb.connect(str(path))
    con.execute(PLAYS_DDL)
    con.execute(
        "CREATE TABLE scores (play_id VARCHAR, scored_at TIMESTAMP, status VARCHAR, won BOOLEAN, "
        "pnl_usd DOUBLE, close_ts TIMESTAMP, close_mid DOUBLE, close_ask DOUBLE, "
        "close_source VARCHAR, clv_cents DOUBLE, clv_vs_ask_cents DOUBLE)"
    )
    con.execute(
        "CREATE TABLE killswitch_log (ts TIMESTAMP, log_date DATE, action VARCHAR, n INTEGER, "
        "mean_clv DOUBLE, note VARCHAR)"
    )
    con.execute(
        "CREATE TABLE ingame_flags (slate_date DATE, flag_key VARCHAR, minutes INTEGER, "
        "when_text VARCHAR, ts TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE sends (send_id VARCHAR, ts TIMESTAMP, kind VARCHAR, mode VARCHAR, "
        "slate_date DATE, dedupe_keys VARCHAR, play_ids VARCHAR, text_hash VARCHAR, "
        "input_hash VARCHAR, chars INTEGER, note VARCHAR)"
    )
    for i, d in enumerate(dates):
        for j in range(2):
            pid = f"p{i}{j}"
            prop = j == 0
            con.execute(
                "INSERT INTO plays VALUES (?,?,0,?,?,?,?,?,'slate','kalshi','T','yes',?,NULL,?,"
                "'pts',20,0.5,0.5,2,1.0,'thin',1.5,0.5,2.0,0.55,'r','e','k','h','sent',0.5,0.0,?)",
                [
                    pid, f"b{i}{j}", datetime(2026, 11, 1 + i, 20), date.fromisoformat(d),
                    f"00226{i}", "AAA@BBB", datetime(2026, 11, 1 + i, 23),
                    "prop" if prop else "game_win", 123 if prop else None,
                    "Nikola Jokic: 20+ pts" if prop else "AAA to win",
                ],
            )  # fmt: skip
            con.execute(
                "INSERT INTO scores VALUES (?,?, 'settled', ?, ?, NULL, 0.5, 0.5, 's', ?, NULL)",
                [pid, datetime(2026, 11, 3), j == 0, 0.5 if j == 0 else -1.0, 1.0 + i + j],
            )
    con.execute("INSERT INTO plays SELECT * REPLACE ('pv' AS play_id) FROM plays LIMIT 1")
    con.execute(
        "INSERT INTO scores VALUES ('pv', TIMESTAMP '2026-11-03', 'void', NULL, 0, "
        "NULL, NULL, NULL, '', NULL, NULL)"
    )
    con.execute(
        "INSERT INTO plays SELECT * REPLACE ('pdd' AS play_id, 'daily_dollar' AS edge_label, "
        "4.0/47 AS fee_cents, 0.07 AS edge_cents, "
        "'staked $12.40; product of asks 0.70c, edge per $1' AS reason, "
        "'/Users/devin/x $3.50' AS explanation) FROM plays WHERE play_id = 'p00'"
    )
    con.execute(
        "INSERT INTO killswitch_log VALUES (TIMESTAMP '2026-11-03 13:00:00', DATE '2026-11-03', "
        "'tripped', 30, -1.28, 'mean CLV below 0')"
    )
    con.execute(
        "INSERT INTO ingame_flags VALUES (DATE '2026-11-01', '401:99', 20, 'Q4 0:00', "
        "TIMESTAMP '2026-11-02 00:05:00')"
    )
    sends = [
        ("s1", "ingame_benched", '["401:99"]', "[]"),
        ("s2", "ingame_returned", '["401:99"]', "[]"),
        ("s3", "slate", '["x"]', '["p00"]'),
        ("s4", "halftime", '["002260"]', "[]"),
        ("s5", "ingame_back", '["401"]', "[]"),
    ]
    for sid, kind, keys, pids in sends:
        con.execute(
            "INSERT INTO sends VALUES (?, TIMESTAMP '2026-11-02 01:00:00', ?, 'sent', "
            "DATE '2026-11-01', ?, ?, 'hash', 'ih', 10, '')",
            [sid, kind, keys, pids],
        )
    con.close()


def _mk_ht(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE halftime_scored (written_at TIMESTAMP, made_at TIMESTAMP, slate_date DATE, "
        "event_id VARCHAR, game VARCHAR, ticker VARCHAR, player_name VARCHAR, player_id INTEGER, "
        "stat VARCHAR, k INTEGER, ask_c INTEGER, p_model DOUBLE, edge_model DOUBLE)"
    )
    con.execute(
        "INSERT INTO halftime_scored VALUES (TIMESTAMP '2026-11-02 02:00:00', "
        "TIMESTAMP '2026-11-02 02:00:00', DATE '2026-11-01', 'e1', 'A@B', 't1', 'X Y', 5, 'pts', "
        "10, 40, 0.6, 0.15), (TIMESTAMP '2026-11-02 02:00:00', TIMESTAMP '2026-11-02 02:00:00', "
        "DATE '2026-11-01', 'e1', 'A@B', 't2', 'X Y', 5, 'pts', 12, 40, 0.3, -0.1)"
    )
    con.close()


def _root(tmp_path: Path, dates: tuple[str, ...] = ("2026-11-01", "2026-11-02")) -> Path:
    r = tmp_path / "repo"
    (r / "data" / "jarvis").mkdir(parents=True)
    (r / "configs").mkdir()
    (r / "reports" / "forward_scorecard").mkdir(parents=True)
    _mk_book(r / "data" / "jarvis" / "paper_book.duckdb", dates)
    _mk_ht(r / "data" / "jarvis" / "halftime_overs.duckdb")
    (r / "data" / "jarvis" / "model_weights.jsonl").write_text(
        json.dumps({"ts": "2026-11-02T13:00:01", "stat": "pts", "w_after": 0.0}) + "\n"
    )
    (r / "configs" / "jarvis.yaml").write_text(
        "balance:\n  start_usd: 70.0\n  current_usd: 69.0\n  floor_usd: 20.0\n"
    )
    (r / "reports" / "forward_scorecard" / "2026-10-20.json").write_text(
        json.dumps(
            {
                "date": "2026-10-20",
                "part3": {
                    "all": {
                        "brier_diff": {
                            "n": 53,
                            "games": 1,
                            "value": 0.015,
                            "lo": float("nan"),
                            "hi": float("nan"),
                        }
                    }
                },
            }
        )  # fmt: skip
    )
    return r


def _money_free(doc: dict[str, Any]) -> bool:
    return (
        doc["balance"] is None
        and all(p["stake_usd"] is None and p["contracts"] is None for p in doc["plays"])
        and all(p["result"] is None or p["result"]["pnl_usd"] is None for p in doc["plays"])
        and all(d["pnl_usd"] is None for d in doc["daily"])
    )


def test_shape_and_values(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=200)
    assert doc["schema"] == 1 and doc["privacy"] == "private"
    assert doc["built_at"].endswith("Z") and doc["data_as_of"].endswith("Z")
    assert len(doc["plays"]) == 6
    p = next(x for x in doc["plays"] if x["play_id"] == "p00")
    assert p["player_name"] == "Nikola Jokic" and p["price_cents"] == 50.0
    assert p["result"]["clv_cents"] == 1.0 and p["result"]["close_cents"] == 50.0
    assert doc["record"] == {"n_settled": 4, "n_won": 2, "n_lost": 2, "n_void": 1}
    assert [d["slate_date"] for d in doc["daily"]] == ["2026-11-01", "2026-11-02"]
    ks = doc["status"]["kill_switch"]
    assert ks["on"] is True and ks["n"] == 30 and doc["status"]["model_review"] is True
    assert doc["status"]["w_model"] == 0.0
    assert doc["balance"] == {"start_usd": 70.0, "current_usd": 69.0, "floor_usd": 20.0}
    assert len(doc["halftime"]) == 1 and doc["halftime"][0]["edge_cents"] == pytest.approx(15.0)
    assert all(a["text"] is None for a in doc["alerts"]) and len(doc["alerts"]) == 5
    kinds = {o["kind"] for o in doc["observations"]}
    assert {"rotation", "correction", "halftime", "risk"} <= kinds
    corr = next(o for o in doc["observations"] if o["kind"] == "correction")
    assert corr["corrects"] == "flag:2026-11-01:401:99" and corr["certainty"] == "correction"
    benched = next(a for a in doc["alerts"] if a["kind"] == "ingame_benched")
    assert benched["observation_ids"] == ["flag:2026-11-01:401:99"]
    tr = doc["track_record"]
    assert tr["scorecard_date"] == "2026-10-20" and tr["model_vs_market"]["ci95"] is None
    assert tr["model_vs_market"]["reason"]
    json.dumps(doc, allow_nan=False)  # strict JSON after scrub


def test_clv_ci_and_null_when_few_dates(tmp_path: Path) -> None:
    two = desk.build(_root(tmp_path), reps=300)["clv_summary"]
    assert two["n"] == 4 and two["mean_cents"] == pytest.approx(2.0)
    lo, hi = two["ci95"]
    assert lo <= 2.0 <= hi and two["clustered_on"] == "slate_date"
    assert two["bootstrap"]["seed"] == desk.BOOTSTRAP_SEED
    one = desk.build(_root(tmp_path / "x", ("2026-11-01", "2026-11-01")), reps=50)["clv_summary"]
    assert one["ci95"] is None and "needs at least 2" in one["reason"]
    assert desk.clv_summary([])["mean_cents"] is None


def test_clv_deterministic() -> None:
    rows = [("a", 1.0), ("a", 2.0), ("b", -1.0), ("c", 3.0)]
    assert desk.clv_summary(rows, 100) == desk.clv_summary(rows, 100)


def test_shared_cut_nulls_money(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    assert not _money_free(doc)
    sh = desk.shared_cut(doc)
    assert sh["privacy"] == "shared" and _money_free(sh)
    assert doc["privacy"] == "private" and doc["balance"] is not None  # source not mutated


def test_atomic_write_and_failure_keeps_old(tmp_path: Path) -> None:
    root = _root(tmp_path)
    out = tmp_path / "out"
    desk.run(root, out, reps=50)
    before = {n: (out / n).read_text() for n in ("desk.json", "desk_shared.json")}
    assert json.loads(before["desk_shared.json"])["privacy"] == "shared"
    (root / "data/jarvis/paper_book.duckdb").unlink()
    with pytest.raises(desk.DeskExportError):
        desk.run(root, out, reps=50)
    assert {n: (out / n).read_text() for n in before} == before
    assert sorted(p.name for p in out.iterdir()) == ["desk.json", "desk_shared.json"]
    assert desk.main(["--root", str(root), "--out", str(out)]) == 1


def test_serialisation_failure_leaves_old(tmp_path: Path) -> None:
    out = tmp_path / "o"
    desk.write_atomic(out, {"desk.json": {"a": 1}})
    with pytest.raises(TypeError):
        desk.write_atomic(out, {"desk.json": {"a": object()}})
    assert json.loads((out / "desk.json").read_text()) == {"a": 1}


def test_read_only_open_and_no_writes(tmp_path: Path) -> None:
    p = tmp_path / "x.duckdb"
    duckdb.connect(str(p)).close()
    con = desk.open_ro(p)
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE t (a INTEGER)")
    con.close()
    root = _root(tmp_path / "r")
    book = root / "data/jarvis/paper_book.duckdb"
    before = book.read_bytes()
    desk.build(root, reps=20)
    assert book.read_bytes() == before


def test_lock_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.duckdb"
    duckdb.connect(str(p)).close()
    real = duckdb.connect
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] < 4:
            raise duckdb.IOException("Could not set lock on file")
        return real(*a, **k)

    monkeypatch.setattr(desk.duckdb, "connect", flaky)
    desk.open_ro(p, tries=10, sleep_s=3.0, sleep=sleeps.append).close()
    assert calls["n"] == 4 and sleeps == [3.0, 3.0, 3.0]

    calls["n"] = -100
    sleeps.clear()
    with pytest.raises(desk.DeskExportError, match="after 3 tries"):
        desk.open_ro(p, tries=3, sleep_s=3.0, sleep=sleeps.append)
    assert len(sleeps) == 2


def test_no_execution_surface() -> None:
    src = Path(desk.__file__).read_text().lower()
    for bad in ("nba.jarvis", "nba.duckdb", "heavy.lock", "requests", "urllib", "httpx"):
        assert f"import {bad}" not in src and f"from {bad}" not in src
    assert "read_only=true" in src


def test_shared_cut_blocks_derivable_money(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    sh = desk.shared_cut(doc)
    assert all(p["fee_cents"] is None for p in sh["plays"])
    dd = next(p for p in sh["plays"] if p["play_id"] == "pdd")
    assert dd["edge_cents"] is None and dd["reason"] is None and dd["explanation"] is None
    priv = next(p for p in doc["plays"] if p["play_id"] == "pdd")
    assert priv["fee_cents"] == pytest.approx(4 / 47)  # the private cut keeps it
    # model_review in the shared cut is the kill switch alone, never the balance floor
    low = json.loads(json.dumps(doc))
    low["status"]["kill_switch"]["on"] = False
    low["status"]["model_review"] = True  # floor-closed in the private cut
    assert desk.shared_cut(low)["status"]["model_review"] is False


def _strings(x: Any) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return []


def test_shared_json_scan(tmp_path: Path) -> None:
    import re

    root = _root(tmp_path)
    out = tmp_path / "o"
    desk.run(root, out, reps=50)
    for name in ("desk.json", "desk_shared.json"):
        doc = json.loads((out / name).read_text())
        assert all(not s.startswith("/") and "/Users/" not in s for s in _strings(doc["sources"]))
    raw = (out / "desk_shared.json").read_text()
    assert "/Users/" not in raw and str(tmp_path) not in raw
    for s in _strings(json.loads(raw)):
        assert not re.search(r"\$\s?\d", s.replace("per $1", "")), s
    assert "12.40" not in raw


def test_absolute_path_fails_shared_build(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    doc["sources"]["x"] = "/Users/devin/secret"
    with pytest.raises(desk.DeskExportError):
        desk.shared_cut(doc)


def test_ingame_back_is_inference_not_correction(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    back = next(o for o in doc["observations"] if o["id"].startswith("s5:"))
    assert back["kind"] == "rotation" and back["certainty"] == "inference"
    assert back["corrects"] is None
    assert sum(o["kind"] == "correction" for o in doc["observations"]) == 1


def test_book_closed_before_name_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    book = root / "data/jarvis/paper_book.duckdb"
    seen: list[bool] = []
    real = desk.EspnNames.player

    def spy(self: Any, slate: str, game: str, pid: str) -> Any:
        try:  # a writer can open the file only if our read-only handle is closed
            duckdb.connect(str(book)).close()
            seen.append(True)
        except duckdb.Error:
            seen.append(False)
        return real(self, slate, game, pid)

    monkeypatch.setattr(desk.EspnNames, "player", spy)
    doc = desk.build(root, reps=20)
    assert seen and all(seen)
    assert doc["book_hold_seconds"] >= 0


def test_shared_scrub_covers_every_string(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    doc["status"]["kill_switch"]["note"] = "bonus $5.00 used"
    doc["observations"][0]["title"] = "stake $12.40 flagged"
    doc["alerts"][0]["title"] = "sent $3"
    sh = desk.shared_cut(doc)
    assert sh["status"]["kill_switch"]["note"] is None
    assert sh["observations"][0]["title"] == "[amount removed]"
    assert sh["alerts"][0]["title"] == "[amount removed]"
    doc["plays"][0]["reason"] = "edge per $1 after fee"
    assert desk.shared_cut(doc)["plays"][0]["reason"] == "edge per $1 after fee"


def test_daily_dollar_reason_null_in_shared(tmp_path: Path) -> None:
    doc = desk.build(_root(tmp_path), reps=50)
    dd = next(p for p in doc["plays"] if p["play_id"] == "pdd")
    dd["reason"] = "product of asks 0.70c, assumed fair 0.82c"
    sh = desk.shared_cut(doc)
    assert next(p for p in sh["plays"] if p["play_id"] == "pdd")["reason"] is None
