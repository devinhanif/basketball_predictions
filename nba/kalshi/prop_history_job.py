"""Scheduled, incremental build of the Kalshi prop price history (display only).

Display data only, like ``nba.kalshi.prop_history`` (whose functions build every payload): no
alert, EV, JARVIS or paper-trade path reads it, it carries no model number and derives no
indicator. This module sends no request (it imports no HTTP client), opens no database and
writes only under its own two directories:

* ``data/derived/kalshi_prop_history/<slate>/``: rebuildable working state (compact points
  appended per run, the list of pages already read, offsets), deleted once the night is final;
* ``data/export/kalshi_prop_history/``: ``latest/`` (index plus one file per game, rewritten every
  run while the slate is live) and ``<slate>/`` (the finished night, written once, as a whole
  directory rename).

Inputs are read in place and never written: the live capture's write-once pages
(``data/raw/live_<slate>/``: prop ``/markets`` pages, trade prints, ESPN scoreboard, manifest) and
the 15-minute snapshot job's raw pages (``data/kalshi/raw/<UTC date>/``) instead of its DuckDB.

Incremental: a page is parsed once. Each run lists the input trees, parses only pages it has not
read, appends their compact points to per-game files and then rebuilds each game's payload from
those points one game at a time (so memory is one game's worth, not the night's). A full build is
the same code from an empty state, and equals ``prop_history.build_games`` on the same inputs
(tested). Anything that would make the incremental result differ from a full build (a page that
arrives out of time order, a truncated manifest, a state from another version) starts the state
over from the raw pages.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

STATE_VERSION = 1
#: The snapshot job writes its pages in place (gzip stream, not tmp + rename), so a run is read
#: only once it is this old; a whole run takes well under a minute.
SNAPSHOT_SETTLE_S = 600
#: A capture page without its manifest line yet, or an unreadable file, younger than this is left
#: for the next run (the capture renames a page into place, then appends the manifest line).
YOUNG_FILE_S = 120
#: Pages parsed per batch before the points are appended and the state committed (bounds memory
#: on a first run that catches up a whole night).
BATCH_FILES = 120
#: The dated set is written this long after the capture's window closes (last tip + 4 h), so the
#: capture's last pages have landed.
FINAL_MARGIN = timedelta(minutes=10)
CAPTURE_TAIL = timedelta(hours=4)
#: Watchdog thresholds (``problem``): a live build older than this while games can be on, and a
#: night with no final set this long after its last tip.
LIVE_STALE = timedelta(minutes=40)
FINAL_OVERDUE = timedelta(hours=5, minutes=30)

DECISIONS = ("done", "no_capture", "built", "final")
#: Same as ``prop_history.FIRST_SLATE`` (a test holds them equal); repeated so that a no-op run
#: does not import the parser and its dataframe library.
FIRST_SLATE = date(2026, 10, 20)


@dataclass(frozen=True)
class Paths:
    capture_dir: Path  # data/raw/live_<slate>
    snapshot_raw: Path | None  # data/kalshi/raw (contains <UTC date>/ directories)
    out: Path  # data/export/kalshi_prop_history
    state_root: Path  # data/derived/kalshi_prop_history

    def state(self, day: date) -> Path:
        return self.state_root / day.isoformat()


def default_paths(root: Path, day: date) -> Paths:
    return Paths(
        capture_dir=root / "data" / "raw" / f"live_{day.isoformat()}",
        snapshot_raw=root / "data" / "kalshi" / "raw",
        out=root / "data" / "export" / "kalshi_prop_history",
        state_root=root / "data" / "derived" / "kalshi_prop_history",
    )


class RebuildNeeded(Exception):
    """The saved state cannot be extended to equal a full build: start it over."""


# ---------------------------------------------------------------------------------------------
# small file helpers


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _write_json_atomic(path: Path, obj: Any) -> None:
    _write_bytes_atomic(path, json.dumps(obj, separators=(",", ":")).encode())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _age_s(path: Path, now_ts: float) -> float:
    try:
        return now_ts - path.stat().st_mtime
    except OSError:
        return 0.0


def _gz_json(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()))


# ---------------------------------------------------------------------------------------------
# state


def _empty_meta(day: date) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "slate": day.isoformat(),
        "manifest_offset": 0,
        "pending_received": {},  # capture page rel -> received_at, for pages not read yet
        "done": {"capture": [], "trades": [], "snapshot": []},
        "last_t": {"capture": {}, "snapshot": {}},  # per series, latest page time applied
        "capture_page_times": {},
        "capture_as_of": None,
        "snapshot_times": [],
        "unreadable": [],
        "chunks": {},  # "<code>/<kind>.jsonl" -> committed bytes
        "market_sig": {},  # "<src>|<ticker>" -> [t, sig] of the last stored market fields
    }


class State:
    """Working state of one slate on disk. Points are appended to ``games/<code>/<kind>.jsonl``
    (one JSON list of rows per line); ``meta.json`` is replaced atomically after the appends and
    records each chunk's committed length, so a run killed between the two leaves bytes beyond
    the committed length, which the next run cuts off before reading."""

    KINDS = ("cap", "snap", "prints", "mkt")

    def __init__(self, root: Path, day: date) -> None:
        self.root = root
        self.day = day
        self.meta = _empty_meta(day)
        self.done: dict[str, set[str]] = {k: set() for k in ("capture", "trades", "snapshot")}

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def chunk_path(self, rel: str) -> Path:
        return self.root / "games" / rel

    @classmethod
    def load(cls, root: Path, day: date) -> State:
        st = cls(root, day)
        meta = _read_json(st.meta_path)
        if meta is None:
            if root.exists() and any(root.iterdir()):
                raise RebuildNeeded("state without a readable meta.json")
            return st
        if meta.get("version") != STATE_VERSION or meta.get("slate") != day.isoformat():
            raise RebuildNeeded("state from another version or slate")
        for rel, n in meta["chunks"].items():
            p = st.chunk_path(rel)
            size = p.stat().st_size if p.exists() else -1
            if size < n:
                raise RebuildNeeded(f"chunk {rel} shorter than committed")
            if size > n:  # a run died after appending, before committing: drop the tail
                with p.open("r+b") as fh:
                    fh.truncate(n)
        st.meta = meta
        st.done = {k: set(v) for k, v in meta["done"].items()}
        return st

    def append(self, code: str, kind: str, rows: list[Any]) -> None:
        if not rows:
            return
        rel = f"{code}/{kind}.jsonl"
        p = self.chunk_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(rows, separators=(",", ":")) + "\n").encode()
        with p.open("ab") as fh:
            fh.write(line)
        self.meta["chunks"][rel] = self.meta["chunks"].get(rel, 0) + len(line)

    def commit(self) -> None:
        self.meta["done"] = {k: sorted(v) for k, v in self.done.items()}
        for rel in self.meta["chunks"]:
            with self.chunk_path(rel).open("ab") as fh:
                os.fsync(fh.fileno())
        _write_json_atomic(self.meta_path, self.meta)

    def codes(self) -> list[str]:
        return sorted({rel.split("/", 1)[0] for rel in self.meta["chunks"]})

    def rows(self, code: str, kind: str) -> Iterator[Any]:
        """The committed rows of one game and kind, streamed one appended line at a time."""
        rel = f"{code}/{kind}.jsonl"
        left = self.meta["chunks"].get(rel, 0)
        if not left:
            return
        with self.chunk_path(rel).open("rb") as fh:
            for line in fh:
                if left <= 0:
                    break
                left -= len(line)
                yield from json.loads(line)


# ---------------------------------------------------------------------------------------------
# ingest (only pages not read before)


def _code_of(ticker: str) -> str:
    from nba.kalshi.prop_history import event_code

    return event_code("-".join(ticker.split("-")[:2])) or "unknown"


def _sig(m: Mapping[str, Any]) -> str:
    return hashlib.sha1(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _read_manifest(st: State, capture_dir: Path, series: Sequence[str]) -> None:
    """New complete manifest lines since the last run; keep receive times of prop pages."""
    mf = capture_dir / "manifest.jsonl"
    if not mf.exists():
        return
    off = int(st.meta["manifest_offset"])
    size = mf.stat().st_size
    if size < off:
        raise RebuildNeeded("manifest shorter than the offset already read")
    with mf.open("rb") as fh:
        fh.seek(off)
        data = fh.read(size - off)
    end = data.rfind(b"\n") + 1  # complete lines only; a line being appended waits
    pending = st.meta["pending_received"]
    for line in data[:end].splitlines():
        try:
            row = json.loads(line)
            path = str(row["path"])
            when = str(row["received_at"])
        except (ValueError, KeyError, TypeError):
            continue
        prop = path.startswith("kalshi/") and path.split("/")[1].rsplit("_p", 1)[0] in series
        if prop and path not in st.done["capture"]:
            pending[path] = when
    st.meta["manifest_offset"] = off + end


@dataclass(frozen=True)
class _CapPage:
    t: int
    dir: str
    name: str
    series: str
    rel: str
    path: Path


def _new_capture_pages(st: State, capture_dir: Path, now_ts: float) -> list[_CapPage]:
    from nba.kalshi.prop_history import PROP_SERIES, _epoch, stamp_from_name

    root = capture_dir / "kalshi"
    if not root.exists():
        return []
    pending = st.meta["pending_received"]
    out: list[_CapPage] = []
    held: dict[str, int] = {}  # series -> time of its first page left for the next run
    for d in sorted(root.iterdir()):
        series = d.name.rsplit("_p", 1)[0]
        if series not in PROP_SERIES or not d.is_dir():
            continue
        for f in sorted(d.glob("*.json.gz")):
            rel = str(f.relative_to(capture_dir))
            if rel in st.done["capture"]:
                continue
            got = pending.get(rel)
            when = datetime.fromisoformat(got) if got else None
            if when is None:
                when = stamp_from_name(f.name)
                if _age_s(f, now_ts) < YOUNG_FILE_S:
                    # its manifest line is on its way; later pages of the series wait with it, so
                    # pages are always applied in time order
                    if when is not None:
                        held[series] = min(held.get(series, _epoch(when)), _epoch(when))
                    continue
            if when is None:
                st.meta["unreadable"].append(rel)
                st.done["capture"].add(rel)
                continue
            out.append(_CapPage(_epoch(when), d.name, f.name, series, rel, f))
    out = [p for p in out if p.t < held.get(p.series, p.t + 1)]
    out.sort(key=lambda p: (p.t, p.dir, p.name))
    return out


def _ingest_capture(st: State, pages: list[_CapPage], day: date, now_ts: float) -> None:
    from nba.kalshi.prop_history import capture_page_points, market_subset

    last = st.meta["last_t"]["capture"]
    start_last = dict(last)
    for p in pages:
        if p.t <= start_last.get(p.series, -1):
            raise RebuildNeeded(f"capture page {p.rel} is older than pages already applied")
    times = st.meta["capture_page_times"]
    sig = st.meta["market_sig"]
    for i in range(0, len(pages), BATCH_FILES):
        cap: dict[str, list[Any]] = {}
        mkt: dict[str, list[Any]] = {}
        for p in pages[i : i + BATCH_FILES]:
            try:
                body = _gz_json(p.path)
            except (OSError, ValueError, EOFError):
                if _age_s(p.path, now_ts) < YOUNG_FILE_S:
                    continue
                st.meta["unreadable"].append(p.rel)
                st.done["capture"].add(p.rel)
                continue
            times.setdefault(p.series, []).append(p.t)
            st.meta["capture_as_of"] = max(st.meta["capture_as_of"] or p.t, p.t)
            last[p.series] = max(last.get(p.series, p.t), p.t)
            for m, q in capture_page_points(body, p.t, day):
                code = _code_of(q.ticker)
                cap.setdefault(code, []).append(
                    [q.ticker, q.t, q.bid, q.ask, q.last, q.volume, q.open_interest]
                )
                sub = market_subset(m)
                s = _sig(sub)
                key = f"capture|{q.ticker}"
                if sig.get(key, [None, None])[1] != s:
                    mkt.setdefault(code, []).append(["capture", q.ticker, q.t, sub])
                    sig[key] = [q.t, s]
            st.done["capture"].add(p.rel)
            st.meta["pending_received"].pop(p.rel, None)
        for code, rows in cap.items():
            st.append(code, "cap", rows)
        for code, rows in mkt.items():
            st.append(code, "mkt", rows)
        st.commit()


def _ingest_trades(st: State, capture_dir: Path, day: date, now_ts: float) -> None:
    from nba.kalshi.prop_history import trade_file_prints

    files = [
        f
        for f in sorted((capture_dir / "kalshi_trades").glob("*/*.json.gz"))
        if str(f.relative_to(capture_dir)) not in st.done["trades"]
    ]
    for i in range(0, len(files), BATCH_FILES):
        rows: dict[str, list[Any]] = {}
        for f in files[i : i + BATCH_FILES]:
            rel = str(f.relative_to(capture_dir))
            try:
                body = _gz_json(f)
            except (OSError, ValueError, EOFError):
                if _age_s(f, now_ts) < YOUNG_FILE_S:
                    continue
                st.meta["unreadable"].append(rel)
                st.done["trades"].add(rel)
                continue
            for pr in trade_file_prints(body, day):
                rows.setdefault(_code_of(pr.ticker), []).append(
                    [pr.ticker, pr.t_us, pr.trade_id, pr.yes_price, pr.count]
                )
            st.done["trades"].add(rel)
        for code, rs in rows.items():
            st.append(code, "prints", rs)
        st.commit()


def _ingest_snapshot(st: State, raw_root: Path, day: date, now_ts: float) -> None:
    from nba.kalshi.prop_history import (
        SnapshotFilter,
        market_subset,
        snapshot_page_points,
        snapshot_pages,
    )

    settled_before = int(now_ts) - SNAPSHOT_SETTLE_S
    pages = [
        p
        for p in snapshot_pages(raw_root, day)
        if p.rel not in st.done["snapshot"] and p.t < settled_before
    ]
    last = st.meta["last_t"]["snapshot"]
    for p in pages:
        if p.t <= last.get(p.series, -1):
            raise RebuildNeeded(f"snapshot page {p.rel} is older than pages already applied")
    filt = SnapshotFilter()
    sig = st.meta["market_sig"]
    times = set(st.meta["snapshot_times"])
    for i in range(0, len(pages), BATCH_FILES):
        snap: dict[str, list[Any]] = {}
        mkt: dict[str, list[Any]] = {}
        for p in pages[i : i + BATCH_FILES]:
            try:
                body = _gz_json(p.path)
            except (OSError, ValueError, EOFError):
                st.meta["unreadable"].append(f"kalshi_raw/{p.rel}")
                st.done["snapshot"].add(p.rel)
                continue
            for m, q in snapshot_page_points(body, p.t, day, filt):
                code = _code_of(q.ticker)
                snap.setdefault(code, []).append(
                    [q.ticker, q.t, q.bid, q.ask, q.last, q.volume, q.open_interest]
                )
                times.add(q.t)
                sub = market_subset(m)
                s = _sig(sub)
                key = f"snapshot|{q.ticker}"
                if sig.get(key, [None, None])[1] != s:
                    mkt.setdefault(code, []).append(["snapshot", q.ticker, q.t, sub])
                    sig[key] = [q.t, s]
            st.done["snapshot"].add(p.rel)
        # the last t of a series moves only after its whole batch is read: pages of one run that
        # share a time are applied together
        for p in pages[i : i + BATCH_FILES]:
            last[p.series] = max(last.get(p.series, p.t), p.t)
        for code, rows in snap.items():
            st.append(code, "snap", rows)
        for code, rows in mkt.items():
            st.append(code, "mkt", rows)
        st.meta["snapshot_times"] = sorted(times)
        st.commit()


# ---------------------------------------------------------------------------------------------
# build (one game at a time)


def _game_inputs(st: State, code: str, base: Any) -> Any:
    from nba.kalshi.prop_history import Print, QuotePoint, SlateInputs, dedupe_snapshot

    inp = SlateInputs(st.day)
    inp.capture_page_times = base.capture_page_times
    inp.capture_as_of = base.capture_as_of
    inp.snapshot_times = base.snapshot_times
    inp.snapshot_as_of = base.snapshot_as_of
    inp.tips = base.tips
    inp.unreadable = base.unreadable
    inp.capture_points = [QuotePoint(*r) for r in st.rows(code, "cap")]
    inp.snapshot_points = dedupe_snapshot(QuotePoint(*r) for r in st.rows(code, "snap"))
    seen: set[str] = set()
    for tk, t_us, tid, price, count in st.rows(code, "prints"):
        if tid in seen:
            continue
        seen.add(tid)
        inp.prints.append(Print(tid, tk, t_us, price, count))
    newest: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {"capture": {}, "snapshot": {}}
    for src, tk, t, sub in st.rows(code, "mkt"):
        cur = newest[src].get(tk)
        if cur is None or t >= cur[0]:
            newest[src][tk] = (t, sub)
    inp.markets = {tk: sub for tk, (_, sub) in newest["capture"].items()}
    for tk, (_, sub) in newest["snapshot"].items():
        inp.markets.setdefault(tk, sub)
    return inp


def _slate_base(st: State, capture_dir: Path) -> Any:
    from nba.kalshi.prop_history import SlateInputs, read_scoreboard

    base = SlateInputs(st.day)
    base.capture_page_times = {s: list(v) for s, v in st.meta["capture_page_times"].items()}
    base.capture_as_of = st.meta["capture_as_of"]
    base.snapshot_times = list(st.meta["snapshot_times"])
    base.snapshot_as_of = max(base.snapshot_times) if base.snapshot_times else None
    base.unreadable = list(st.meta["unreadable"])
    if capture_dir.exists():
        read_scoreboard(capture_dir, base)
    return base


def build_payloads(
    st: State, capture_dir: Path, aliases: Mapping[str, int], built_at: datetime
) -> tuple[list[tuple[str, bytes]], dict[str, Any], Any]:
    """Every game file's bytes (built one game at a time) and the slate index, exactly as
    ``prop_history.build_games`` + ``write_slate`` would build them from the same inputs."""
    from nba.kalshi.prop_history import (
        GAME_BUDGET_BYTES,
        GAME_BUDGET_GZ_BYTES,
        build_games,
    )

    base = _slate_base(st, capture_dir)
    _, index = build_games(base, aliases, built_at)  # the slate skeleton (no markets)
    files: list[tuple[str, bytes]] = []
    games: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    n_markets = n_resolved = 0
    for code in st.codes():
        g_games, g_idx = build_games(_game_inputs(st, code, base), aliases, built_at)
        for gcode, payload in g_games.items():
            data = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()
            files.append((gcode, data))
            gz = len(gzip.compress(data, 6))
            games.append(
                {
                    "event_code": gcode,
                    "file": f"{gcode}.json",
                    "n_markets": payload["n_markets"],
                    "scheduled_tip": payload["scheduled_tip"],
                    "bytes": len(data),
                    "gz_bytes": gz,
                    "over_budget": len(data) > GAME_BUDGET_BYTES or gz > GAME_BUDGET_GZ_BYTES,
                }
            )
        n_markets += g_idx["n_markets"]
        n_resolved += g_idx["n_resolved"]
        unresolved.extend(g_idx["unresolved"])
        del g_games
    games.sort(key=lambda g: g["event_code"])
    files.sort(key=lambda f: f[0])
    index["n_markets"] = n_markets
    index["n_resolved"] = n_resolved
    index["games"] = games
    index["unresolved"] = sorted(unresolved, key=lambda u: str(u["ticker"]))
    index["budget"] = {"game_bytes": GAME_BUDGET_BYTES, "game_gz_bytes": GAME_BUDGET_GZ_BYTES}
    return files, index, base


# ---------------------------------------------------------------------------------------------
# output


def _index_bytes(index: Mapping[str, Any]) -> bytes:
    return json.dumps(index, separators=(",", ":"), sort_keys=False).encode()


def write_latest(out: Path, files: Sequence[tuple[str, bytes]], index: Mapping[str, Any]) -> None:
    """``latest/``: each game file replaced atomically, then the index, then files of another
    slate removed. A reader that opens the index and then the files it lists always gets whole
    files; a game file may be one build newer than the index, never from another slate."""
    d = out / "latest"
    for code, data in files:
        _write_bytes_atomic(d / f"{code}.json", data)
    _write_bytes_atomic(d / "index.json", _index_bytes(index))
    keep = {f"{c}.json" for c, _ in files} | {"index.json"}
    for f in d.iterdir():
        if f.name not in keep and (f.suffix == ".json" or f.name.endswith(".tmp")):
            f.unlink(missing_ok=True)


def write_final(
    out: Path, day: date, files: Sequence[tuple[str, bytes]], index: Mapping[str, Any]
) -> bool:
    """``<slate>/``, once: built in a hidden directory, fsynced, then renamed into place (a
    directory rename onto a missing name is atomic). Never replaces an existing night."""
    final = out / day.isoformat()
    if final.exists():
        return False
    tmp = out / f".{day.isoformat()}.partial-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    for code, data in files:
        _write_bytes_atomic(tmp / f"{code}.json", data)
    _write_bytes_atomic(tmp / "index.json", _index_bytes(index))
    os.rename(tmp, final)
    return True


# ---------------------------------------------------------------------------------------------
# one run


@dataclass
class RunResult:
    decision: str
    slate: str
    status: str | None = None
    games: int = 0
    n_markets: int = 0
    new_pages: int = 0
    rebuilt: str | None = None
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _final_written(paths: Paths, day: date) -> bool:
    idx = _read_json(paths.out / day.isoformat() / "index.json")
    return isinstance(idx, dict) and idx.get("status") == "final"


def _ingest(st: State, paths: Paths, day: date, now_ts: float) -> int:
    from nba.kalshi.prop_history import PROP_SERIES

    before = sum(len(v) for v in st.done.values())
    if paths.capture_dir.exists():
        _read_manifest(st, paths.capture_dir, PROP_SERIES)
        _ingest_capture(st, _new_capture_pages(st, paths.capture_dir, now_ts), day, now_ts)
        _ingest_trades(st, paths.capture_dir, day, now_ts)
    if paths.snapshot_raw is not None and paths.snapshot_raw.exists():
        _ingest_snapshot(st, paths.snapshot_raw, day, now_ts)
    st.commit()
    return sum(len(v) for v in st.done.values()) - before


def run_slate(
    day: date,
    paths: Paths,
    *,
    now: datetime,
    aliases: Mapping[str, int] | None = None,
    allow_no_capture: bool = False,
    keep_state: bool = False,
    rebuild: bool = False,
) -> RunResult:
    """One scheduled run for one slate: no-op when the night is already final or its capture
    has not started; otherwise read the new pages, rewrite ``latest/`` and, once the capture's
    window is over (last scheduled tip + 4 h + margin), write the dated set and drop the state."""
    t0 = time.monotonic()
    res = RunResult("done", day.isoformat())
    state_dir = paths.state(day)
    if _final_written(paths, day):
        if state_dir.exists() and not keep_state:
            shutil.rmtree(state_dir)  # a run died between the rename and this cleanup
        return res
    if not paths.capture_dir.exists() and not allow_no_capture:
        res.decision = "no_capture"
        return res
    if aliases is None:
        from nba.kalshi.aliases import load_reviewed_aliases

        aliases = load_reviewed_aliases()
    now_ts = now.timestamp()
    if rebuild and state_dir.exists():
        shutil.rmtree(state_dir)
        res.rebuilt = "requested"
    try:
        st = State.load(state_dir, day)
        res.new_pages = _ingest(st, paths, day, now_ts)
    except RebuildNeeded as why:
        shutil.rmtree(state_dir, ignore_errors=True)
        res.rebuilt = str(why)
        st = State.load(state_dir, day)
        res.new_pages = _ingest(st, paths, day, now_ts)
    files, index, base = build_payloads(st, paths.capture_dir, aliases, now)
    write_latest(paths.out, files, index)
    res.decision = "built"
    res.status = str(index["status"])
    res.games = len(files)
    res.n_markets = int(index["n_markets"])
    last_tip = max(base.tips.values()) if base.tips else None
    if last_tip is not None and now >= last_tip + CAPTURE_TAIL + FINAL_MARGIN:
        write_final(paths.out, day, files, index)
        res.decision = "final"
        if not keep_state:
            shutil.rmtree(state_dir, ignore_errors=True)
    res.seconds = round(time.monotonic() - t0, 3)
    return res


# ---------------------------------------------------------------------------------------------
# watchdog check (pure; wiring it into nba.ops.watchdog is the ops owner's change)


def _ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def problem(
    now: datetime,
    hb: Mapping[str, Any] | None,
    latest_index: Mapping[str, Any] | None,
    tips: Sequence[datetime],
    day: date,
    final_index: Mapping[str, Any] | None,
) -> str | None:
    """One line for the independent watchdog, or None. Never installed (no heartbeat): None.
    Problems: the last run failed; while games can be on (first tip to last tip + 4 h) the live
    set is older than ``LIVE_STALE`` or of another slate; ``FINAL_OVERDUE`` after the last tip
    the night has no final set."""
    if hb is None:
        return None
    rc = hb.get("rc")
    if hb.get("end_ts") is not None and rc not in (0, None):
        return f"kalshi-prop-history last run exited rc={rc}"
    if not tips:
        return None
    first, last = min(tips), max(tips)
    if first <= now <= last + CAPTURE_TAIL:
        if latest_index is None or latest_index.get("slate_date") != day.isoformat():
            if now >= first + LIVE_STALE:
                return f"kalshi-prop-history: no live set for {day} since first tip"
            return None
        built = _ts(latest_index.get("built_at"))
        if built is None or now - built > LIVE_STALE:
            return f"kalshi-prop-history: live set for {day} older than {LIVE_STALE}"
    if now >= last + FINAL_OVERDUE and (
        final_index is None or final_index.get("status") != "final"
    ):
        return f"kalshi-prop-history: no final set for {day}"
    return None


# ---------------------------------------------------------------------------------------------
# CLI


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m nba.kalshi.prop_history_job",
        description="Incremental, scheduled build of the display-only prop price history from "
        "the capture's and the snapshot's raw pages (read in place). Sends no request, opens no "
        "database.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one scheduled run (slate D, and a missed final of D-1)")
    r.add_argument("--date", required=True, help="slate date (ET), YYYY-MM-DD")
    r.add_argument("--root", type=Path, default=Path("."), help="repo root (default: cwd)")
    r.add_argument("--capture-dir", type=Path, help="override data/raw/live_<date>")
    r.add_argument("--snapshot-raw", type=Path, help="override data/kalshi/raw")
    r.add_argument("--no-snapshot", action="store_true", help="capture only")
    r.add_argument("--out", type=Path, help="override data/export/kalshi_prop_history")
    r.add_argument("--state", type=Path, help="override data/derived/kalshi_prop_history")
    r.add_argument("--now", help="ISO time to act as now (tests and replays)")
    r.add_argument("--allow-no-capture", action="store_true", help="build from snapshot only")
    r.add_argument("--keep-state", action="store_true")
    r.add_argument("--rebuild", action="store_true", help="drop the state and read every page")
    r.add_argument("--no-previous", action="store_true", help="do not finish a missed D-1")
    return p.parse_args(argv)


def _paths(a: argparse.Namespace, day: date) -> Paths:
    d = default_paths(a.root, day)
    return Paths(
        capture_dir=(
            a.capture_dir
            if a.capture_dir is not None and day == date.fromisoformat(a.date)
            else d.capture_dir
        ),
        snapshot_raw=None if a.no_snapshot else (a.snapshot_raw or d.snapshot_raw),
        out=a.out or d.out,
        state_root=a.state or d.state_root,
    )


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] | None = None) -> int:
    a = parse_args(argv)
    day = date.fromisoformat(a.date)
    if day < FIRST_SLATE:
        print(
            f"refused: {day} is before {FIRST_SLATE} (current-season slates only)", file=sys.stderr
        )
        return 2
    now = _ts(a.now) if a.now else (clock or (lambda: datetime.now(UTC)))()
    assert now is not None
    results: list[dict[str, Any]] = []
    prev = day - timedelta(days=1)
    if not a.no_previous and prev >= FIRST_SLATE:
        pp = _paths(a, prev)
        # a night the scheduled runs did not finish (Mac asleep, a failed run): finish it first,
        # only when its state exists (it was live here) and its final set is missing
        if pp.state(prev).exists() and not _final_written(pp, prev):
            results.append(run_slate(prev, pp, now=now, keep_state=a.keep_state).as_dict())
    results.append(
        run_slate(
            day,
            _paths(a, day),
            now=now,
            allow_no_capture=a.allow_no_capture,
            keep_state=a.keep_state,
            rebuild=a.rebuild,
        ).as_dict()
    )
    out = results[-1] | ({"previous": results[0]} if len(results) > 1 else {})
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
