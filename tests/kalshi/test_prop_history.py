"""Prop price history export: synthetic inputs written to tmp_path, no network, no live store."""

from __future__ import annotations

import ast
import gzip
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.kalshi import prop_history as ph

DAY = date(2026, 11, 16)
TIP = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
T0 = int(datetime(2026, 11, 16, 23, 0, tzinfo=UTC).timestamp())
ALIASES = {"known player": 101}


def _market(
    ticker: str,
    title: str,
    *,
    bid: str = "0.4000",
    ask: str = "0.4500",
    last: str = "0.4200",
    vol: str = "10.00",
    oi: str = "5.00",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": "-".join(ticker.split("-")[:2]),
        "title": title,
        "floor_strike": 24.5,
        "custom_strike": {"basketball_player": "uuid-1"},
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "last_price_dollars": last,
        "volume_fp": vol,
        "open_interest_fp": oi,
        "status": "active",
    }


KNOWN = "KXNBAPTS-26NOV16AAABBB-AAAKNOWN1-25"
UNKNOWN = "KXNBAREB-26NOV16AAABBB-BBBNEW2-8"
OTHER_DAY = "KXNBAPTS-26NOV17AAABBB-AAAKNOWN1-25"


def _gz(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(obj).encode()))


def _stamp(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y%m%dT%H%M%SZ")


def _capture(root: Path, poll_times: list[int], *, vol_by_poll: list[str] | None = None) -> Path:
    cap = root / "live_2026-11-16"
    manifest = []
    for i, t in enumerate(poll_times):
        vol = (vol_by_poll or ["10.00"] * len(poll_times))[i]
        for series, ms in (
            (
                "KXNBAPTS",
                [
                    _market(KNOWN, "Known Player: 25+ points", vol=vol),
                    _market(OTHER_DAY, "Known Player: 25+ points"),
                ],
            ),
            ("KXNBAREB", [_market(UNKNOWN, "New Rookie: 8+ rebounds")]),
            ("KXNBAPTSPROT", [_market("KXNBAPTSPROT-26NOV16AAABBB-X-1", "Out of scope")]),
        ):
            rel = f"kalshi/{series}_p0/{_stamp(t)}.json.gz"
            _gz(cap / rel, {"cursor": "", "markets": ms})
            received = datetime.fromtimestamp(t, UTC).replace(microsecond=250_000)
            manifest.append({"path": rel, "received_at": received.isoformat()})
    (cap / "manifest.jsonl").write_text("\n".join(json.dumps(m) for m in manifest) + "\n")
    _gz(
        cap / "espn_scoreboard/2026-11-16/20261116T230000Z.json.gz",
        {
            "events": [
                {
                    "date": "2026-11-17T00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "away", "team": {"abbreviation": "AAA"}},
                                {"homeAway": "home", "team": {"abbreviation": "BBB"}},
                            ]
                        }
                    ],
                }
            ]
        },
    )
    return cap


def _trade(tid: str, t: int, price: str, count: str, us: int = 0) -> dict[str, Any]:
    ct = datetime.fromtimestamp(t, UTC).replace(microsecond=us).isoformat().replace("+00:00", "Z")
    return {
        "trade_id": tid,
        "created_time": ct,
        "yes_price_dollars": price,
        "count_fp": count,
        "ticker": KNOWN,
    }


def _snapshot_db(path: Path, rows: list[tuple[str, datetime, float]]) -> Path:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE kalshi_markets (ticker VARCHAR, title VARCHAR, event_ticker VARCHAR)")
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT, "
        "last FLOAT, volume INTEGER, open_interest INTEGER, source VARCHAR)"
    )
    con.execute(
        "INSERT INTO kalshi_markets VALUES (?, ?, ?)",
        [KNOWN, "Known Player: 25+ points", "KXNBAPTS-26NOV16AAABBB"],
    )
    for tk, ts, bid in rows:
        con.execute(
            "INSERT INTO kalshi_prices VALUES (?, ?, ?, 0.5, 0.45, 10, 5, 'live')",
            [tk, ts.replace(tzinfo=None), bid],
        )
    con.execute(
        "INSERT INTO kalshi_prices VALUES (?, ?, 0.1, 0.2, 0.1, 1, 1, 'historical')",
        [KNOWN, datetime(2026, 11, 16, 12, 0)],
    )
    con.close()
    return path


# ------------------------------------------------------------------ parsing
def test_event_code_date_and_teams() -> None:
    assert ph.event_code("KXNBAPTS-26NOV16MEMWAS") == "26NOV16MEMWAS"
    assert ph.event_date("26NOV16MEMWAS") == date(2026, 11, 16)
    assert ph.event_teams("26NOV16MEMWAS") == ("MEM", "WAS")
    assert ph.event_teams("26NOV16ABCDEFG") == (None, None)  # ambiguous: never guessed
    assert ph.event_code("garbage") is None


def test_describe_market_resolved_unmatched_and_unparsed() -> None:
    ok = ph.describe_market(_market(KNOWN, "Known Player: 25+ points"), ALIASES)
    assert ok["resolved"] and ok["player_id"] == 101 and ok["stat"] == "pts"
    assert ok["threshold"] == 25.0 and ok["floor_strike"] == 24.5
    new = ph.describe_market(_market(UNKNOWN, "New Rookie: 8+ rebounds"), ALIASES)
    assert not new["resolved"] and new["player_id"] is None
    assert new["player_name"] == "New Rookie"
    assert new["unresolved_reason"] == "name_not_in_reviewed_alias_table"
    bad = ph.describe_market(_market(KNOWN, "Something else entirely"), ALIASES)
    assert bad["unresolved_reason"] == "title_unparsed" and bad["player_id"] is None


# ------------------------------------------------------------------ quotes: run length, gaps
def _q(t: int, bid: float = 0.4) -> ph.QuotePoint:
    return ph.QuotePoint("T", t, bid, 0.45, 0.42, 10.0, 5.0)


def test_run_length_collapses_identical_polls_and_never_spans_a_gap() -> None:
    pts = [_q(T0), _q(T0 + 60), _q(T0 + 120), _q(T0 + 180, 0.41), _q(T0 + 600, 0.41)]
    rows = ph.run_length(pts, threshold_s=150)
    assert rows == [
        [T0, T0 + 120, 3, 0.4, 0.45, 0.42, 10.0, 5.0],
        [T0 + 180, T0 + 180, 1, 0.41, 0.45, 0.42, 10.0, 5.0],
        [T0 + 600, T0 + 600, 1, 0.41, 0.45, 0.42, 10.0, 5.0],  # same values, but after a hole
    ]
    assert ph.gaps([p.t for p in pts], 150) == [[T0 + 180, T0 + 600]]


def test_quote_series_labels_resolution_per_source() -> None:
    cap = ph.quote_series("capture", [_q(T0), _q(T0 + 60)])
    snap = ph.quote_series("snapshot", [_q(T0), _q(T0 + 900), _q(T0 + 4000)])
    assert cap is not None and snap is not None
    assert cap["nominal_resolution_s"] == 60 and cap["kind"] == "quote_points"
    assert cap["gap_threshold_s"] == 150 and cap["gaps"] == []
    assert snap["nominal_resolution_s"] == 900 and snap["gaps"] == [[T0 + 900, T0 + 4000]]
    assert snap["median_gap_s"] == pytest.approx((900 + 3100) / 2)
    assert ph.quote_series("capture", []) is None


# ------------------------------------------------------------------ candles from prints only
def test_candles_are_true_ohlc_from_prints_with_ties_broken_by_trade_id() -> None:
    base = T0 * 1_000_000
    prints = [
        ph.Print("b", "T", base + 5, 0.50, 2.0),
        ph.Print("a", "T", base + 5, 0.40, 1.0),  # same microsecond: "a" sorts first
        ph.Print("c", "T", base + 30_000_000, 0.60, 3.0),
        ph.Print("d", "T", base + 200_000_000, 0.55, 1.0),  # 200 s later: a new bucket, no fill
    ]
    rows = ph.candles(prints, 60)
    assert rows == [
        [T0 // 60 * 60, 0.40, 0.60, 0.40, 0.60, 6.0, 3],
        [(T0 + 200) // 60 * 60, 0.55, 0.55, 0.55, 0.55, 1.0, 1],
    ]
    assert ph.candles([], 60) == []


def test_print_coverage_against_book_volume() -> None:
    pts = [
        ph.QuotePoint("T", T0, 0.4, 0.5, 0.4, 10.0, 1.0),
        ph.QuotePoint("T", T0 + 120, 0.4, 0.5, 0.4, 20.0, 1.0),
    ]
    prints = [
        ph.Print("a", "T", (T0 + 60) * 1_000_000, 0.4, 5.0),
        ph.Print("b", "T", (T0 - 60) * 1_000_000, 0.4, 7.0),
    ]  # before our first poll
    cov = ph.print_coverage(prints, pts)
    assert cov == {"print_volume": 5.0, "book_volume_delta": 10.0, "print_coverage": 0.5}
    assert ph.print_coverage(prints, pts[:1])["print_coverage"] is None


# ------------------------------------------------------------------ end to end
def test_end_to_end_capture_snapshot_and_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polls = [T0, T0 + 60, T0 + 120, T0 + 420]  # a 300 s hole in the capture
    cap = _capture(tmp_path, polls, vol_by_poll=["10.00", "10.00", "13.00", "13.00"])
    trades = {
        KNOWN: [_trade("t1", T0 + 70, "0.4300", "2.00"), _trade("t2", T0 + 80, "0.4400", "1.00")],
        OTHER_DAY: [_trade("t9", T0 + 70, "0.5", "1")],
    }
    _gz(cap / "kalshi_trades/2026-11-16/a.json.gz", trades)
    _gz(cap / "kalshi_trades/2026-11-16/b.json.gz", {KNOWN: trades[KNOWN][:1]})  # duplicate id
    (cap / "kalshi_trades/2026-11-16/broken.json.gz").write_bytes(b"not gzip")
    db = _snapshot_db(
        tmp_path / "copy.duckdb",
        [
            (KNOWN, datetime(2026, 11, 16, 20, 0), 0.38),
            (KNOWN, datetime(2026, 11, 16, 20, 15), 0.38),
        ],
    )
    monkeypatch.setattr(ph, "load_reviewed_aliases", lambda: ALIASES)
    out = tmp_path / "out"
    rc = ph.main(
        [
            "--date",
            "2026-11-16",
            "--capture-dir",
            str(cap),
            "--snapshot-db",
            str(db),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    index = json.loads((out / "2026-11-16" / "index.json").read_text())
    assert index["display_only"] is True and index["n_markets"] == 2 and index["n_resolved"] == 1
    assert [u["ticker"] for u in index["unresolved"]] == [UNKNOWN]
    assert index["unreadable_inputs"] == ["kalshi_trades/2026-11-16/broken.json.gz"]
    assert index["games"][0]["file"] == "26NOV16AAABBB.json" and index["games"][0]["bytes"] > 0
    assert index["games"][0]["over_budget"] is False
    game = json.loads((out / "2026-11-16" / "26NOV16AAABBB.json").read_text())
    assert game["scheduled_tip"] == "2026-11-17T00:00:00Z"
    assert game["status"] == "final"  # built now, long after tip + 4 h
    assert (game["away"], game["home"]) == ("AAA", "BBB")
    by = {m["ticker"]: m for m in game["markets"]}
    assert OTHER_DAY not in by and not any("PROT" in t for t in by)
    known = by[KNOWN]
    srcs = {q["source"]: q for q in known["quotes"]}
    assert srcs["capture"]["nominal_resolution_s"] == 60 and srcs["capture"]["n_polls"] == 4
    assert srcs["capture"]["gaps"] == [[T0 + 120, T0 + 420]]
    assert [r[:3] for r in srcs["capture"]["rows"]] == [
        [T0, T0 + 60, 2],
        [T0 + 120, T0 + 120, 1],
        [T0 + 420, T0 + 420, 1],
    ]
    assert srcs["snapshot"]["nominal_resolution_s"] == 900
    assert srcs["snapshot"]["rows"][0][3] == 0.38  # float32 in the store, back to 4 decimals
    assert srcs["snapshot"]["n_polls"] == 2  # the 'historical' row is not a live quote
    c = known["candles"]
    assert c["kind"] == "trade_candles" and c["n_trades"] == 2 and c["bucket_s"] == 60
    assert c["rows"] == [[(T0 + 60) // 60 * 60, 0.43, 0.44, 0.43, 0.44, 3.0, 2]]
    assert c["print_coverage"] == 1.0  # 3 contracts printed, book volume 10 -> 13
    assert by[UNKNOWN]["candles"]["rows"] == [] and by[UNKNOWN]["player_id"] is None


def test_status_final_after_the_capture_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = _capture(tmp_path, [T0])
    inputs = ph.SlateInputs(DAY)
    ph.read_capture(cap, DAY, inputs)
    ph.read_scoreboard(cap, inputs)
    _, idx = ph.build_games(inputs, ALIASES, datetime(2026, 11, 17, 4, 1, tzinfo=UTC))
    assert idx["status"] == "final"
    _, idx = ph.build_games(inputs, ALIASES, datetime(2026, 11, 17, 3, 59, tzinfo=UTC))
    assert idx["status"] == "partial"


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("rel", ["data/kalshi/kalshi.duckdb", "data/nba.duckdb", "x/nba.duckdb"])
def test_refuses_live_stores_by_path(tmp_path: Path, rel: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    with pytest.raises(ph.LiveStoreRefused):
        ph.refuse_live_db(p)
    rc = ph.main(["--date", "2026-11-16", "--snapshot-db", str(p), "--out", str(tmp_path / "o")])
    assert rc == 2 and not (tmp_path / "o").exists()


def test_refuses_holdout_and_earlier_slates(tmp_path: Path) -> None:
    rc = ph.main(
        ["--date", "2026-04-01", "--capture-dir", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert rc == 2


# ------------------------------------------------------------------ read-only by construction
SRC = Path(ph.__file__)


def test_no_network_order_or_credential_code() -> None:
    forbidden = re.compile(
        r"portfolio|/orders|order_group|create_order|place_order|cancel_order|amend_order|/fills"
        r"|Authorization|KALSHI-ACCESS|api[_-]?key|private[_-]?key|bearer|websocket|wss://"
        r"|os\.environ|getenv|https?://|\.post\(|\.put\(|\.delete\(|\.patch\(",
        re.IGNORECASE,
    )
    code = re.sub(r'"""[\s\S]*?"""', "", SRC.read_text())
    code = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
    assert not forbidden.search(code), forbidden.search(code)


def test_imports_no_http_client_and_no_model_or_live_path() -> None:
    banned = (
        "httpx",
        "requests",
        "urllib",
        "socket",
        "http",
        "nba.kalshi.client",
        "nba.live",
        "nba.models",
        "nba.daily",
        "nba.truth",
        "nba.parlay",
        "nba.papertrade",
        "nba.db",
    )
    found: list[str] = []
    for node in ast.walk(ast.parse(SRC.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    bad = [n for n in found if n.split(".")[0] in banned or n.startswith(banned)]
    assert not bad, bad


def test_export_carries_no_model_number_or_indicator_keys(tmp_path: Path) -> None:
    cap = _capture(tmp_path, [T0, T0 + 60])
    inputs = ph.SlateInputs(DAY)
    ph.read_capture(cap, DAY, inputs)
    games, idx = ph.build_games(inputs, ALIASES, datetime(2026, 11, 17, 1, 0, tzinfo=UTC))
    keys: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            keys.update(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(games)
    walk(idx)
    pat = re.compile(
        r"prob|model|forecast|edge|^ev$|signal|trend|momentum|fair|kelly|stake", re.IGNORECASE
    )
    assert not [k for k in keys if pat.search(k)]
