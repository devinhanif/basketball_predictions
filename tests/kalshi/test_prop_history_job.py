"""Scheduled prop price history: raw snapshot reader, incremental runs, outputs, launcher, plist.

Synthetic pages in tmp_path only; no network, no live store, no launchctl."""

from __future__ import annotations

import ast
import gzip
import json
import os
import plistlib
import re
import socket
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.kalshi import prop_history as ph
from nba.kalshi import prop_history_job as job

REPO = Path(__file__).resolve().parents[2]
DAY = date(2026, 11, 16)
TIP = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
ALIASES = {"known player": 101}
A = "KXNBAPTS-26NOV16AAABBB-AAAKNOWN1-25"
B = "KXNBAREB-26NOV16AAABBB-BBBNEW2-8"
C = "KXNBAAST-26NOV16CCCDDD-CCCKNOWN3-6"
LATER_SLATE = "KXNBAPTS-26NOV17AAABBB-AAAKNOWN1-25"
TITLES = {
    A: "Known Player: 25+ points",
    B: "New Rookie: 8+ rebounds",
    C: "Known Player: 6+ assists",
}
BUILT = datetime(2026, 11, 17, 1, 30, tzinfo=UTC)  # in game: partial
FINAL_AT = TIP + timedelta(hours=4, minutes=11)


def _ts(at: datetime) -> int:
    return int(at.timestamp())


def _stamp(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y%m%dT%H%M%SZ")


def _gz(path: Path, obj: Any, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(obj).encode()))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _market(tk: str, i: int, title: str | None = None) -> dict[str, Any]:
    return {
        "ticker": tk,
        "event_ticker": "-".join(tk.split("-")[:2]),
        "title": title or TITLES.get(tk, "Known Player: 25+ points"),
        "floor_strike": 24.5,
        "custom_strike": {"basketball_player": "uuid-1"},
        "yes_bid_dollars": f"{0.40 + 0.01 * (i // 3):.4f}",
        "yes_ask_dollars": "0.4500",
        "last_price_dollars": "0.4200",
        "volume_fp": f"{10 + i:.2f}",
        "open_interest_fp": "5.00",
        "status": "active",
    }


#: file mtimes: a day before the slate, so every run below (at fixed times) sees them as settled
OLD = float(int(datetime(2026, 11, 15, tzinfo=UTC).timestamp()))


class Capture:
    """A growing live capture directory (write-once pages, append-only manifest)."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "data" / "raw" / "live_2026-11-16"
        self.lines: list[str] = []
        _gz(
            self.dir / "espn_scoreboard/2026-11-16/20261116T220000Z.json.gz",
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

    def poll(self, i: int, at: datetime, *, manifest: bool = True, mtime: float = OLD) -> None:
        t = _ts(at)
        for series, tks in (
            ("KXNBAPTS", [A, LATER_SLATE]),
            ("KXNBAREB", [B]),
            ("KXNBAAST", [C]),
            ("KXNBAPTSPROT", ["KXNBAPTSPROT-26NOV16AAABBB-X-1"]),
        ):
            rel = f"kalshi/{series}_p0/{_stamp(t)}.json.gz"
            _gz(self.dir / rel, {"cursor": "", "markets": [_market(tk, i) for tk in tks]}, mtime)
            if manifest:
                self.manifest(rel, at)

    def manifest(self, rel: str, at: datetime) -> None:
        rec = (at + timedelta(seconds=1, microseconds=600_000)).isoformat()  # not the name second
        with (self.dir / "manifest.jsonl").open("a") as fh:
            fh.write(json.dumps({"path": rel, "received_at": rec}) + "\n")

    def trades(self, name: str, rows: dict[str, list[dict[str, Any]]]) -> None:
        _gz(self.dir / f"kalshi_trades/2026-11-16/{name}.json.gz", rows, OLD)


def _trade(tk: str, tid: str, at: datetime, price: str, count: str) -> dict[str, Any]:
    return {
        "trade_id": tid,
        "ticker": tk,
        "created_time": at.isoformat().replace("+00:00", "Z"),
        "yes_price_dollars": price,
        "count_fp": count,
    }


def _snap(
    root: Path, at: datetime, series: str, markets: list[dict[str, Any]], page: int = 0
) -> Path:
    p = (
        root
        / "data/kalshi/raw"
        / at.strftime("%Y-%m-%d")
        / (f"{at.strftime('%H%M%S')}_{series}_open_{page}.json.gz")
    )
    _gz(p, {"cursor": "", "markets": markets}, OLD)
    return p


def _paths(root: Path) -> job.Paths:
    return job.default_paths(root, DAY)


def _reference(root: Path, built: datetime, out: Path) -> dict[str, bytes]:
    """The full (non-incremental) build of the same inputs, through prop_history directly."""
    paths = _paths(root)
    inp = ph.SlateInputs(DAY)
    ph.read_capture(paths.capture_dir, DAY, inp)
    ph.read_prints(paths.capture_dir, DAY, inp)
    ph.read_scoreboard(paths.capture_dir, inp)
    assert paths.snapshot_raw is not None
    ph.read_snapshot_raw(
        paths.snapshot_raw, DAY, inp, settled_before=_ts(built) - job.SNAPSHOT_SETTLE_S
    )
    games, index = ph.build_games(inp, ALIASES, built)
    ph.write_slate(out, games, index)
    d = out / DAY.isoformat()
    return {f.name: f.read_bytes() for f in sorted(d.glob("*.json"))}


def _files(d: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted(d.glob("*.json"))}


def _run(root: Path, now: datetime, **kw: Any) -> job.RunResult:
    return job.run_slate(DAY, _paths(root), now=now, aliases=ALIASES, **kw)


def _night(root: Path, polls: range, *, trades: bool = True) -> Capture:
    cap = Capture(root)
    for i in polls:
        cap.poll(i, TIP + timedelta(minutes=i))
    if trades:
        cap.trades(
            f"{_stamp(_ts(TIP + timedelta(minutes=polls.stop)))}",
            {
                A: [
                    _trade(
                        A,
                        f"a{polls.stop}",
                        TIP + timedelta(minutes=polls.stop, seconds=5),
                        "0.43",
                        "2.00",
                    ),
                    _trade(A, "dup", TIP + timedelta(seconds=30), "0.41", "1.00"),
                ],
                LATER_SLATE: [_trade(LATER_SLATE, "x", TIP, "0.5", "1")],
            },
        )
    return cap


# ------------------------------------------------------------ raw snapshot reader vs the store
def _store_like_db(path: Path, rows: list[tuple[str, datetime, dict[str, Any]]]) -> Path:
    """kalshi_prices / kalshi_markets as the snapshot job writes them (FLOAT prices, INTEGER
    counts)."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE kalshi_markets (ticker VARCHAR, title VARCHAR, event_ticker VARCHAR)")
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT, "
        "last FLOAT, volume INTEGER, open_interest INTEGER, source VARCHAR)"
    )
    done: set[tuple[str, datetime]] = set()
    for tk, at, m in reversed(rows):  # later page wins within one run, as _replace_prices does
        if (tk, at) in done:
            continue
        done.add((tk, at))
        con.execute(
            "INSERT INTO kalshi_prices VALUES (?, ?, ?, ?, ?, ?, ?, 'live')",
            [
                tk,
                at.replace(tzinfo=None),
                float(m["yes_bid_dollars"]),
                float(m["yes_ask_dollars"]),
                float(m["last_price_dollars"]),
                round(float(m["volume_fp"])),
                round(float(m["open_interest_fp"])),
            ],
        )
    for tk in {r[0] for r in rows}:
        con.execute("INSERT INTO kalshi_markets VALUES (?, ?, ?)", [tk, TITLES[tk], tk[:22]])
    con.close()
    return path


def test_raw_snapshot_pages_give_the_same_series_as_the_store(tmp_path: Path) -> None:
    raw = tmp_path / "data/kalshi/raw"
    rows: list[tuple[str, datetime, dict[str, Any]]] = []
    for k in range(8):
        at = datetime(2026, 11, 16, 3, 15, 11, tzinfo=UTC) + timedelta(minutes=15 * k)
        if k == 5:
            at += timedelta(hours=2)  # a hole longer than 2.5 periods
        ma, mb = _market(A, k), _market(B, k)
        mb["volume_fp"] = f"{10 + k}.49"  # whole contracts in the store
        bad = _market(A, k, title="Something that does not parse")
        bad["ticker"] = "KXNBAPTS-26NOV16AAABBB-ODD-1"
        _snap(tmp_path, at, "KXNBAPTS", [ma, bad, _market(LATER_SLATE, k)])
        _snap(tmp_path, at, "KXNBAREB", [mb])
        rows += [(A, at, ma), (B, at, mb)]
        if k == 3:  # the same market on two pages of one run: the later page wins
            ma2 = _market(A, k + 30)
            _snap(tmp_path, at, "KXNBAPTS", [ma2], page=1)
            rows.append((A, at, ma2))
    db = _store_like_db(tmp_path / "copy.duckdb", rows)
    via_db, via_raw = ph.SlateInputs(DAY), ph.SlateInputs(DAY)
    ph.read_snapshot(db, DAY, via_db)
    ph.read_snapshot_raw(raw, DAY, via_raw)

    def series(inp: ph.SlateInputs) -> dict[str, Any]:
        by: dict[str, list[ph.QuotePoint]] = {}
        for p in inp.snapshot_points:
            by.setdefault(p.ticker, []).append(p)
        return {tk: ph.quote_series("snapshot", pts) for tk, pts in by.items()}

    assert set(series(via_raw)) == {A, B}  # the unparsed title and the later slate are not kept
    assert series(via_raw) == series(via_db)
    assert via_raw.snapshot_times == via_db.snapshot_times
    assert via_raw.snapshot_as_of == via_db.snapshot_as_of
    assert series(via_raw)[A]["gaps"] and series(via_raw)[B]["rows"][0][6] == 10.0


def test_a_snapshot_run_still_being_written_is_left_for_the_next_build(tmp_path: Path) -> None:
    at = TIP - timedelta(minutes=20)
    _snap(tmp_path, at, "KXNBAPTS", [_market(A, 0)])
    inp = ph.SlateInputs(DAY)
    ph.read_snapshot_raw(tmp_path / "data/kalshi/raw", DAY, inp, settled_before=_ts(at))
    assert inp.snapshot_points == []


# ------------------------------------------------------------------ incremental runs
def test_no_capture_directory_is_a_quiet_no_op(tmp_path: Path) -> None:
    res = _run(tmp_path, BUILT)
    assert res.decision == "no_capture"
    assert not (tmp_path / "data/export").exists() and not (tmp_path / "data/derived").exists()
    rc = job.main(
        ["run", "--date", "2026-11-16", "--root", str(tmp_path), "--now", BUILT.isoformat()]
    )
    assert rc == 0 and not (tmp_path / "data/export").exists()


def test_capture_started_but_empty_writes_an_empty_live_set(tmp_path: Path) -> None:
    (tmp_path / "data/raw/live_2026-11-16").mkdir(parents=True)
    res = _run(tmp_path, BUILT)
    assert res.decision == "built" and res.games == 0
    idx = json.loads((tmp_path / "data/export/kalshi_prop_history/latest/index.json").read_text())
    assert idx["games"] == [] and idx["n_markets"] == 0 and idx["display_only"] is True


def test_a_growing_capture_is_read_incrementally_and_equals_a_full_build(tmp_path: Path) -> None:
    cap = _night(tmp_path, range(0, 6))
    for k in range(3):
        _snap(tmp_path, TIP - timedelta(hours=3, minutes=15 * k), "KXNBAPTS", [_market(A, k)])
    first = _run(tmp_path, BUILT)
    assert first.decision == "built" and first.status == "partial" and first.games == 2
    n_first = first.new_pages
    # the capture grows: more polls (one with a 5-minute hole), more prints, a late snapshot run
    for i in (6, 7, 12):
        cap.poll(i, TIP + timedelta(minutes=i))
    cap.trades("later", {A: [_trade(A, "a99", TIP + timedelta(minutes=7), "0.47", "3.00")]})
    _snap(tmp_path, BUILT - timedelta(minutes=30), "KXNBAPTS", [_market(A, 50)])
    second = _run(tmp_path, BUILT)
    assert second.new_pages == 3 * 3 + 1 + 1  # only the new pages (three prop series a poll)
    assert second.new_pages < n_first and second.rebuilt is None
    live = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert live == _reference(tmp_path, BUILT, tmp_path / "ref")
    # and a third run with nothing new rewrites the same bytes
    assert _run(tmp_path, BUILT).new_pages == 0
    assert _files(tmp_path / "data/export/kalshi_prop_history/latest") == live


def test_full_build_from_empty_state_equals_the_reference(tmp_path: Path) -> None:
    _night(tmp_path, range(0, 9))
    _snap(tmp_path, TIP - timedelta(hours=2), "KXNBAREB", [_market(B, 1)])
    _snap(tmp_path, TIP - timedelta(hours=2), "KXNBAAST", [_market(C, 1)])
    _run(tmp_path, BUILT)
    got = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert got == _reference(tmp_path, BUILT, tmp_path / "ref")
    idx = json.loads(got["index.json"])
    assert [g["event_code"] for g in idx["games"]] == ["26NOV16AAABBB", "26NOV16CCCDDD"]
    assert [u["ticker"] for u in idx["unresolved"]] == [B]


def test_a_page_without_its_manifest_line_waits_with_the_later_pages_of_its_series(
    tmp_path: Path,
) -> None:
    cap = _night(tmp_path, range(0, 3))
    at = TIP + timedelta(minutes=3)
    now = BUILT
    cap.poll(3, at, manifest=False, mtime=now.timestamp() - 10)  # just renamed into place
    r1 = job.run_slate(DAY, _paths(tmp_path), now=now, aliases=ALIASES)
    assert r1.new_pages == 3 * 3 + 1  # the young pages wait
    for s in ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBAPTSPROT"):
        cap.manifest(f"kalshi/{s}_p0/{_stamp(_ts(at))}.json.gz", at)
    r2 = job.run_slate(DAY, _paths(tmp_path), now=now, aliases=ALIASES)
    assert r2.new_pages == 3 and r2.rebuilt is None
    got = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert got == _reference(tmp_path, now, tmp_path / "ref")


def test_a_page_older_than_what_was_applied_starts_the_state_over(tmp_path: Path) -> None:
    cap = _night(tmp_path, range(2, 5))
    _run(tmp_path, BUILT)
    cap.poll(0, TIP)  # arrives late, older than pages already applied
    res = _run(tmp_path, BUILT)
    assert res.rebuilt and "older" in res.rebuilt
    got = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert got == _reference(tmp_path, BUILT, tmp_path / "ref")


def test_a_run_killed_after_appending_leaves_no_trace(tmp_path: Path) -> None:
    cap = _night(tmp_path, range(0, 3))
    _run(tmp_path, BUILT)
    chunk = tmp_path / "data/derived/kalshi_prop_history/2026-11-16/games/26NOV16AAABBB/cap.jsonl"
    with chunk.open("ab") as fh:  # appended, never committed (the run died before meta.json)
        fh.write(b'[["' + A.encode() + b'",1,0.1,0.2,0.1,1.0,1.0]]\n')
    cap.poll(3, TIP + timedelta(minutes=3))
    res = _run(tmp_path, BUILT)
    assert res.rebuilt is None
    got = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert got == _reference(tmp_path, BUILT, tmp_path / "ref")


def test_a_corrupt_state_is_rebuilt_from_the_pages(tmp_path: Path) -> None:
    _night(tmp_path, range(0, 3))
    _run(tmp_path, BUILT)
    meta = tmp_path / "data/derived/kalshi_prop_history/2026-11-16/meta.json"
    meta.write_text("{not json")
    res = _run(tmp_path, BUILT)
    assert res.rebuilt
    got = _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert got == _reference(tmp_path, BUILT, tmp_path / "ref")


# ------------------------------------------------------------------ final night, latest set
def test_the_finished_night_is_written_once_and_the_state_dropped(tmp_path: Path) -> None:
    _night(tmp_path, range(0, 4))
    assert _run(tmp_path, TIP + timedelta(hours=4, minutes=5)).decision == "built"  # inside margin
    assert not (tmp_path / "data/export/kalshi_prop_history/2026-11-16").exists()
    res = _run(tmp_path, FINAL_AT)
    assert res.decision == "final" and res.status == "final"
    night = tmp_path / "data/export/kalshi_prop_history/2026-11-16"
    files = _files(night)
    assert json.loads(files["index.json"])["status"] == "final"
    assert files == _files(tmp_path / "data/export/kalshi_prop_history/latest")
    assert files == _reference(tmp_path, FINAL_AT, tmp_path / "ref")
    assert not (tmp_path / "data/derived/kalshi_prop_history/2026-11-16").exists()
    assert not list((tmp_path / "data/export/kalshi_prop_history").glob(".*partial*"))
    again = _run(tmp_path, FINAL_AT + timedelta(minutes=15))
    assert again.decision == "done" and _files(night) == files


def test_latest_drops_the_files_of_the_previous_slate(tmp_path: Path) -> None:
    latest = tmp_path / "data/export/kalshi_prop_history/latest"
    latest.mkdir(parents=True)
    (latest / "26NOV15XXXYYY.json").write_text("{}")
    (latest / "26NOV15XXXYYY.json.tmp").write_text("{")
    (latest / "README.txt").write_text("not ours to delete")
    _night(tmp_path, range(0, 2))
    _run(tmp_path, BUILT)
    names = sorted(f.name for f in latest.iterdir())
    assert names == ["26NOV16AAABBB.json", "26NOV16CCCDDD.json", "README.txt", "index.json"]


def test_main_finishes_a_missed_night_before_the_new_slate(tmp_path: Path) -> None:
    _night(tmp_path, range(0, 3))
    _run(tmp_path, BUILT)  # live on 11-16; then the Mac slept through the final slot
    later = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
    rc = job.main(
        ["run", "--date", "2026-11-17", "--root", str(tmp_path), "--now", later.isoformat()]
    )
    assert rc == 0
    night = tmp_path / "data/export/kalshi_prop_history/2026-11-16/index.json"
    assert json.loads(night.read_text())["status"] == "final"


def test_main_refuses_earlier_slates_and_first_slate_matches() -> None:
    assert job.FIRST_SLATE == ph.FIRST_SLATE
    assert job.main(["run", "--date", "2026-04-01", "--root", "/nonexistent"]) == 2


# ------------------------------------------------------------------ watchdog check
HB_OK = {"start_ts": "2026-11-17T01:00:00+0000", "end_ts": "2026-11-17T01:00:04+0000", "rc": 0}


def test_problem_silent_until_installed_flags_failure_staleness_missing_final() -> None:
    tips = [TIP, TIP + timedelta(hours=1)]
    live = {"slate_date": "2026-11-16", "built_at": "2026-11-17T01:00:04Z"}
    now = datetime(2026, 11, 17, 1, 10, tzinfo=UTC)
    assert job.problem(now, None, None, tips, DAY, None) is None  # never installed
    assert job.problem(now, HB_OK, live, tips, DAY, None) is None
    assert "rc=1" in str(job.problem(now, {**HB_OK, "rc": 1}, live, tips, DAY, None))
    stale = now + timedelta(minutes=45)
    assert "older" in str(job.problem(stale, HB_OK, live, tips, DAY, None))
    other = {**live, "slate_date": "2026-11-15"}
    assert "no live set" in str(job.problem(now, HB_OK, other, tips, DAY, None))
    late = TIP + timedelta(hours=7)
    assert "no final" in str(job.problem(late, HB_OK, live, tips, DAY, None))
    assert job.problem(late, HB_OK, live, tips, DAY, {"status": "final"}) is None
    assert job.problem(now, HB_OK, None, [], DAY, None) is None  # no games


# ------------------------------------------------------------------ read-only by construction
SRC = Path(job.__file__)


def test_no_network_order_or_credential_code_in_the_job() -> None:
    forbidden = re.compile(
        r"portfolio|/orders|order_group|create_order|place_order|cancel_order|amend_order|/fills"
        r"|Authorization|KALSHI-ACCESS|api[_-]?key|private[_-]?key|bearer|websocket|wss://"
        r"|os\.environ|getenv|https?://|\.post\(|\.put\(|\.delete\(|\.patch\(|duckdb",
        re.IGNORECASE,
    )
    code = re.sub(r'"""[\s\S]*?"""', "", SRC.read_text())
    code = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
    assert not forbidden.search(code), forbidden.search(code)
    banned = ("httpx", "requests", "urllib", "socket", "http", "duckdb", "nba.kalshi.client")
    banned += ("nba.live", "nba.models", "nba.daily", "nba.truth", "nba.parlay", "nba.jarvis")
    found: list[str] = []
    for node in ast.walk(ast.parse(SRC.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    assert not [n for n in found if n.split(".")[0] in banned or n.startswith(banned)]


def test_a_run_makes_no_network_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    _night(tmp_path, range(0, 3))
    _snap(tmp_path, TIP - timedelta(hours=2), "KXNBAPTS", [_market(A, 1)])
    assert _run(tmp_path, BUILT).decision == "built"
    assert _run(tmp_path, FINAL_AT).decision == "final"


def test_outputs_carry_no_model_number_or_indicator_keys(tmp_path: Path) -> None:
    _night(tmp_path, range(0, 3))
    _run(tmp_path, BUILT)
    keys: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            keys.update(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for f in (tmp_path / "data/export/kalshi_prop_history/latest").glob("*.json"):
        walk(json.loads(f.read_text()))
    pat = re.compile(
        r"prob|model|forecast|edge|^ev$|signal|trend|momentum|fair|kelly|stake", re.IGNORECASE
    )
    assert keys and not [k for k in keys if pat.search(k)]


# ------------------------------------------------------------------ launcher and plist
SCRIPT = REPO / "ops" / "nba_kalshi_prop_history.sh"


def _fake_uv(tmp: Path, sleep_s: float = 0.0, rc: int = 0) -> Path:
    uv = tmp / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$FAKE_LOG"\n'
        f"sleep {sleep_s}\n"
        'echo \'{"decision": "built", "slate": "x"}\'\n'
        f"exit {rc}\n"
    )
    uv.chmod(0o755)
    return uv


def _launch(tmp: Path, sleep_s: float = 0.0, rc: int = 0) -> subprocess.Popen[str]:
    (tmp / "root").mkdir(exist_ok=True)
    env = {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(_fake_uv(tmp, sleep_s, rc)),
        "NBA_TEST_DATE_ET": "2026-11-16",
        "FAKE_LOG": str(tmp / "uv.log"),
    }
    return subprocess.Popen(
        ["/bin/sh", str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _calls(tmp: Path) -> list[str]:
    p = tmp / "uv.log"
    return p.read_text().splitlines() if p.exists() else []


def _hb(tmp: Path, name: str = "kalshi-prop-history.json") -> dict[str, Any]:
    return dict(json.loads((tmp / "root/data/ops/heartbeat" / name).read_text()))


def test_launcher_runs_the_build_under_nice_and_releases_its_lock(tmp_path: Path) -> None:
    assert _launch(tmp_path).wait(30) == 0
    (call,) = _calls(tmp_path)
    assert "nba.kalshi.prop_history_job run --date 2026-11-16" in call
    hb = _hb(tmp_path)
    assert hb["rc"] == 0 and hb["detail"] == "built" and hb["end_ts"]
    assert not (tmp_path / "root/data/ops/kalshi_prop_history_lock").exists()
    code = SCRIPT.read_text()
    assert 'nice -n 10 "$UV" run --no-sync python -m nba.kalshi.prop_history_job' in code
    assert "TZ=America/New_York date -v-6H +%Y-%m-%d" in code


def test_two_overlapping_runs_never_build_twice(tmp_path: Path) -> None:
    first = _launch(tmp_path, sleep_s=3)
    lock = tmp_path / "root/data/ops/kalshi_prop_history_lock"
    for _ in range(100):
        if (lock / "start").exists() and (lock / "start").read_text().strip():
            break
        time.sleep(0.05)
    second = _launch(tmp_path)
    assert second.wait(30) == 0
    assert first.wait(30) == 0
    assert len(_calls(tmp_path)) == 1  # the second tick skipped
    skip = _hb(tmp_path, "kalshi-prop-history.skip.json")
    assert skip["status"] == "skipped" and "holds" in skip["reason"]
    assert not lock.exists()


@pytest.mark.parametrize("held", ["heavy.lock", "lock"])
def test_launcher_yields_to_heavy_and_daily_jobs(tmp_path: Path, held: str) -> None:
    (tmp_path / "root/data/ops" / held).mkdir(parents=True)
    assert _launch(tmp_path).wait(30) == 0
    assert _launch(tmp_path).wait(30) == 0
    assert _calls(tmp_path) == []
    skip = _hb(tmp_path, "kalshi-prop-history.skip.json")
    assert skip["consecutive_skips"] == 2
    assert (tmp_path / "root/data/ops" / held).is_dir()  # looked at, never taken or removed


def test_a_failed_build_alerts_and_records_its_rc(tmp_path: Path) -> None:
    assert _launch(tmp_path, rc=3).wait(30) == 0
    assert _hb(tmp_path)["rc"] == 3
    alerts = (tmp_path / "root/data/ops/ALERTS.md").read_text()
    assert "[kalshi-prop-history] build for 2026-11-16 exited rc=3" in alerts


def test_launcher_opens_no_database_and_takes_no_shared_lock() -> None:
    code = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    assert "duckdb" not in code and "launchctl" not in code
    assert "LOCK=$OPS/kalshi_prop_history_lock" in code
    assert 'mkdir "$HEAVY"' not in code and 'mkdir "$DAILY"' not in code


def test_plist_is_valid_launches_through_uv_and_is_not_installed_by_the_repo() -> None:
    p = plistlib.loads((REPO / "ops/launchd/local.nba.kalshi-prop-history.plist").read_bytes())
    assert p["Label"] == "local.nba.kalshi-prop-history"
    args = p["ProgramArguments"]
    assert args[0].endswith("/uv") and args[-1].endswith("ops/nba_kalshi_prop_history.sh")
    assert "sh" in args and args[0] != "/bin/sh"
    assert [d["Minute"] for d in p["StartCalendarInterval"]] == [2, 17, 32, 47]
    assert all(set(d) == {"Minute"} for d in p["StartCalendarInterval"])
    assert p["Nice"] == 10 and p["RunAtLoad"] is True
    install = (REPO / "ops" / "install_launchd.sh").read_text()
    assert "kalshi-prop-history" not in install  # installed by hand after the merge
