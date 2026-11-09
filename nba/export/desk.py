"""Desk export v1 (docs/contracts/desk_export_v1.md): what the app's Jarvis screens read.

``python -m nba.export.desk [--out data/export_private] [--root <repo>]`` writes ``desk.json``
(private) and ``desk_shared.json`` (every money field null) atomically.

Read-only by construction: the two JARVIS DuckDB files are opened ``read_only=True`` (retried on a
lock, then a clean failure that leaves the old files), the scorecard and the config are read as
files, and ESPN player names come from the already-captured ESPN summary snapshots under
``data/raw/live_<slate>/espn_summary`` (gz JSON). Nothing here imports ``nba.jarvis``, opens
``nba.duckdb`` or takes ``data/ops/heavy.lock``. No order, portfolio or execution endpoint exists.

Honesty rules: every value is copied or computed from a stored row; what cannot be derived is
``null`` and ``section_notes`` (additive) says why. ``sends`` keeps only text hashes, so
``alerts[].text`` is always null here.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import math
import os
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml

SCHEMA = 1
BOOTSTRAP_SEED = 20261108
BOOTSTRAP_REPS = 2000
OPEN_TRIES = 10
OPEN_SLEEP_S = 3.0

PAPER_BOOK = Path("data/jarvis/paper_book.duckdb")
HALFTIME = Path("data/jarvis/halftime_overs.duckdb")
WEIGHTS = Path("data/jarvis/model_weights.jsonl")
SCORECARD_DIR = Path("reports/forward_scorecard")
CONFIG = Path("configs/jarvis.yaml")

SEND_OBS_KINDS = ("ingame_foul", "ingame_returned", "ingame_back", "ingame_window", "halftime")


class DeskExportError(RuntimeError):
    """A source could not be read; the previous output files are left untouched."""


# ------------------------------------------------------------------------------------ helpers


def iso(ts: Any) -> str | None:
    """Naive timestamps in the JARVIS stores are UTC (``now_naive``)."""
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    return str(ts.isoformat(timespec="seconds")) + "Z"


def day(d: Any) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def open_ro(
    path: Path,
    tries: int = OPEN_TRIES,
    sleep_s: float = OPEN_SLEEP_S,
    sleep: Callable[[float], None] = time.sleep,
) -> duckdb.DuckDBPyConnection:
    """Open read-only, retrying on a lock (a writer may hold the file for a moment)."""
    if not path.exists():
        raise DeskExportError(f"missing source {path}")
    last: Exception | None = None
    for i in range(tries):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.Error as exc:
            last = exc
            if i < tries - 1:
                sleep(sleep_s)
    raise DeskExportError(f"could not open {path} read-only after {tries} tries: {last}")


def fetch(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------------------------ names


def _espn_snapshot(root: Path, slate: str, game: str) -> dict[str, Any] | None:
    d = root / "data" / "raw" / f"live_{slate}" / "espn_summary" / game
    files = sorted(d.glob("*.json.gz")) if d.is_dir() else []
    for f in reversed(files):  # newest first; fall back if the newest is unreadable
        try:
            with gzip.open(f) as fh:
                body = json.loads(fh.read())
            if isinstance(body, dict):
                return body
        except (OSError, ValueError):
            continue
    return None


class EspnNames:
    """ESPN athlete id -> display name and ESPN game id -> 'AWAY@HOME', from captured snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._games: dict[tuple[str, str], tuple[str | None, dict[str, str]]] = {}

    def _load(self, slate: str, game: str) -> tuple[str | None, dict[str, str]]:
        key = (slate, game)
        if key in self._games:
            return self._games[key]
        body = _espn_snapshot(self.root, slate, game)
        matchup: str | None = None
        names: dict[str, str] = {}
        if body is not None:
            try:
                comp = body["header"]["competitions"][0]
                by = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
                matchup = f"{by['away']}@{by['home']}"
            except (KeyError, IndexError, TypeError):
                matchup = None
            try:
                for team in (body.get("boxscore") or {}).get("players", []):
                    for grp in team.get("statistics", [])[:1]:
                        for a in grp.get("athletes", []):
                            ath = a["athlete"]
                            names[str(ath["id"])] = str(ath["displayName"])
            except (KeyError, TypeError):
                pass
        self._games[key] = (matchup, names)
        return self._games[key]

    def player(self, slate: str, game: str, pid: str) -> str | None:
        return self._load(slate, game)[1].get(pid)

    def matchup(self, slate: str, game: str) -> str | None:
        return self._load(slate, game)[0]


# ------------------------------------------------------------------------------------ CLV


def clv_summary(rows: list[tuple[str, float]], reps: int = BOOTSTRAP_REPS) -> dict[str, Any]:
    """Mean CLV (cents) over settled plays with a stored CLV; 95% percentile bootstrap CI
    clustered on slate_date (resample whole dates with replacement, pooled mean per draw, seed
    ``BOOTSTRAP_SEED``, ``reps`` draws). Null CI with a reason when fewer than 2 dates."""
    out: dict[str, Any] = {
        "n": len(rows),
        "mean_cents": None,
        "ci95": None,
        "clustered_on": "slate_date",
        "reason": None,
        "n_dates": len({d for d, _ in rows}),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "reps": reps, "method": "percentile"},
    }
    if not rows:
        out["reason"] = "no settled plays with a stored CLV"
        return out
    out["mean_cents"] = sum(v for _, v in rows) / len(rows)
    by: dict[str, list[float]] = {}
    for d, v in rows:
        by.setdefault(d, []).append(v)
    if len(by) < 2:
        out["reason"] = f"{len(by)} slate date(s); a date-clustered CI needs at least 2"
        return out
    groups = [(sum(v), len(v)) for v in by.values()]
    rng = random.Random(BOOTSTRAP_SEED)
    k = len(groups)
    means: list[float] = []
    for _ in range(reps):
        s = c = 0.0
        for _i in range(k):
            gs, gn = groups[rng.randrange(k)]
            s += gs
            c += gn
        means.append(s / c)
    means.sort()
    out["ci95"] = [means[int(0.025 * reps)], means[min(reps - 1, int(0.975 * reps))]]
    return out


# ------------------------------------------------------------------------------------ sections


def _plays(book: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = fetch(
        book,
        "SELECT p.*, s.status AS s_status, s.won AS s_won, s.pnl_usd AS s_pnl, "
        "s.close_mid AS s_close, s.clv_cents AS s_clv "
        "FROM plays p LEFT JOIN scores s USING (play_id) ORDER BY p.ts, p.leg_idx",
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        desc = r["leg_desc"]
        player_id = r["player_id"]
        name = None
        if player_id is not None and isinstance(desc, str) and ": " in desc:
            name = desc.split(": ", 1)[0]  # leg_desc is "<Name>: <k>+ <stat>" in book.py
        price = num(r["price"])
        close = num(r["s_close"])
        result = None
        if r["s_status"] is not None:
            result = {
                "status": r["s_status"],
                "won": r["s_won"],
                "close_cents": None if close is None else close * 100.0,
                "clv_cents": num(r["s_clv"]),
                "pnl_usd": num(r["s_pnl"]),
            }
        out.append(
            {
                "play_id": r["play_id"],
                "bundle_id": r["bundle_id"],
                "slate_date": day(r["slate_date"]),
                "ts": iso(r["ts"]),
                "game_id": r["game_id"],
                "matchup": r["matchup"],
                "tipoff": iso(r["tipoff"]),
                "source": r["source"],
                "venue": r["venue"],
                "kind": r["kind"],
                "side": r["side"],
                "player_id": player_id,
                "player_name": name,
                "team": None,  # plays stores team_id only (no id->name table in the book)
                "stat": r["stat"],
                "threshold": num(r["threshold"]),
                "leg_desc": desc,
                "price_cents": None if price is None else price * 100.0,
                "fee_cents": num(r["fee_cents"]),
                "fair_prob": num(r["fair_prob"]),
                "model_prob": num(r["model_prob"]),
                "edge_cents": num(r["edge_cents"]),
                "edge_label": r["edge_label"],
                "reason": r["reason"],
                "explanation": r["explanation"] or None,
                "delivered": r["delivered"],
                "contracts": r["contracts"],
                "stake_usd": num(r["stake_usd"]),
                "result": result,
            }
        )
    return out


def _record_daily(plays: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    rec = {"n_settled": 0, "n_won": 0, "n_lost": 0, "n_void": 0}
    days: dict[str, list[dict[str, Any]]] = {}
    for p in plays:
        res = p["result"]
        if res is None:
            continue
        if res["status"] == "void":
            rec["n_void"] += 1
            continue
        if res["status"] != "settled":
            continue
        rec["n_settled"] += 1
        if res["won"] is True:
            rec["n_won"] += 1
        elif res["won"] is False:
            rec["n_lost"] += 1
        days.setdefault(p["slate_date"], []).append(res)
    daily = []
    for d in sorted(days):
        rs = days[d]
        clvs = [r["clv_cents"] for r in rs if r["clv_cents"] is not None]
        pnls = [r["pnl_usd"] for r in rs if r["pnl_usd"] is not None]
        daily.append(
            {
                "slate_date": d,
                "n": len(rs),
                "n_won": sum(1 for r in rs if r["won"] is True),
                "clv_mean_cents": (sum(clvs) / len(clvs)) if clvs else None,
                "pnl_usd": sum(pnls) if pnls else None,
            }
        )
    return rec, daily


def _kill(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not rows:
        ks: dict[str, Any] = {
            "on": False,
            "since": None,
            "n": None,
            "mean_clv_cents": None,
            "note": "no killswitch_log rows: never tripped or cleared",
        }
        return ks, None
    last = rows[-1]
    on = last["action"] == "tripped"
    ks = {
        "on": on,
        "since": iso(last["ts"]),
        "n": last["n"],
        "mean_clv_cents": num(last["mean_clv"]),
        "note": f"latest log row: {last['action']} on {day(last['log_date'])}"
        + (f"; {last['note']}" if last["note"] else ""),
    }
    obs = None
    if on:
        m = num(last["mean_clv"])
        obs = {
            "id": f"kill:{iso(last['ts'])}",
            "slate_date": day(last["log_date"]),
            "ts": iso(last["ts"]),
            "game_id": None,
            "matchup": None,
            "when_text": None,
            "kind": "risk",
            "title": f"Kill switch tripped: n={last['n']}, mean CLV "
            + ("n/a" if m is None else f"{m:+.2f}c"),
            "text": last["note"] or None,
            "certainty": "reported",
            "corrects": None,
        }
    return ks, obs


def _observations_alerts(
    flags: list[dict[str, Any]],
    sends: list[dict[str, Any]],
    kill_rows: list[dict[str, Any]],
    names: EspnNames,
    plays: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nba_matchup = {p["game_id"]: p["matchup"] for p in plays if p["game_id"] and p["matchup"]}
    obs: list[dict[str, Any]] = []
    flag_ids: dict[tuple[str, str], str] = {}

    for f in flags:
        slate, key = day(f["slate_date"]), f["flag_key"]
        game, _, pid = key.partition(":")
        oid = f"flag:{slate}:{key}"
        flag_ids[(slate, key)] = oid
        who = names.player(slate, game, pid) or f"ESPN player {pid}"
        obs.append(
            {
                "id": oid,
                "slate_date": slate,
                "ts": iso(f["ts"]),
                "game_id": game,
                "matchup": names.matchup(slate, game),
                "when_text": f["when_text"],
                "kind": "rotation",
                "title": f"Flagged as benched: {who} (frozen at {f['minutes']} min)",
                "text": None,
                "certainty": "inference",
                "corrects": None,
            }
        )

    send_obs: dict[str, list[str]] = {}
    for s in sends:
        kind, slate = s["kind"], day(s["slate_date"])
        keys = _json_list(s["dedupe_keys"])
        if kind == "ingame_benched":
            send_obs[s["send_id"]] = [flag_ids[(slate, k)] for k in keys if (slate, k) in flag_ids]
            continue
        if kind not in SEND_OBS_KINDS:
            continue
        for k in keys:
            game, _, pid = k.partition(":")
            oid = f"{s['send_id']}:{k}"
            send_obs.setdefault(s["send_id"], []).append(oid)
            who = (names.player(slate, game, pid) or f"ESPN player {pid}") if pid else ""
            row: dict[str, Any]
            if kind == "halftime":
                mu = nba_matchup.get(k)
                row = {
                    "game_id": k,
                    "matchup": mu,
                    "kind": "halftime",
                    "title": f"Halftime check-in sent for {mu or 'game ' + k}",
                    "certainty": "reported",
                    "corrects": None,
                }
            elif kind == "ingame_foul":
                row = {
                    "game_id": game,
                    "matchup": names.matchup(slate, game),
                    "kind": "foul",
                    "title": f"Foul trouble flagged: {who}",
                    "certainty": "reported",
                    "corrects": None,
                }
            elif kind == "ingame_returned":
                row = {
                    "game_id": game,
                    "matchup": names.matchup(slate, game),
                    "kind": "correction",
                    "title": f"Correction: {who} returned after a benched flag",
                    "certainty": "correction",
                    "corrects": flag_ids.get((slate, k)),
                }
            elif kind == "ingame_back":
                row = {
                    "game_id": k,
                    "matchup": names.matchup(slate, k),
                    "kind": "rotation",
                    "title": "Lead shrank: flagged starters may return (JARVIS 'STARTERS BACK?')",
                    "certainty": "inference",
                    "corrects": None,
                }
            else:  # ingame_window
                row = {
                    "game_id": k,
                    "matchup": names.matchup(slate, k),
                    "kind": "rotation",
                    "title": "Starter minutes frozen (game-level flag, one or more players)",
                    "certainty": "inference",
                    "corrects": None,
                }
            obs.append(
                {
                    "id": oid,
                    "slate_date": slate,
                    "ts": iso(s["ts"]),
                    "when_text": None,
                    "text": None,
                    **row,
                }
            )

    _, kobs = _kill(kill_rows)
    if kobs is not None:
        obs.append(kobs)
    obs.sort(key=lambda o: (o["ts"] or "", o["id"]))

    alerts: list[dict[str, Any]] = []
    for s in sends:
        title = str(s["kind"]).replace("_", " ")
        if s["kind"] == "slate":
            title = f"Slate post: {len(_json_list(s['dedupe_keys']))} bundle(s)"
        elif s["kind"] == "recap":
            title = f"Recap for {day(s['slate_date'])}"
        alerts.append(
            {
                "id": s["send_id"],
                "ts": iso(s["ts"]),
                "slate_date": day(s["slate_date"]),
                "kind": s["kind"],
                "mode": s["mode"],
                "title": title,
                "text": None,  # sends keeps text_hash only
                "play_ids": _json_list(s["play_ids"]),
                "observation_ids": send_obs.get(s["send_id"], []),
            }
        )
    return obs, alerts


def _json_list(raw: Any) -> list[str]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _halftime(ht: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = fetch(
        ht,
        "SELECT slate_date, event_id, game, player_id, player_name, stat, k, ask_c, p_model, "
        "edge_model FROM halftime_scored WHERE edge_model > 0 ORDER BY made_at, event_id, ticker",
    )
    out = []
    for r in rows:
        edge = num(r["edge_model"])
        out.append(
            {
                "slate_date": day(r["slate_date"]),
                "game_id": r["event_id"],
                "matchup": r["game"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "stat": r["stat"],
                "threshold": num(r["k"]),
                "price_cents": num(r["ask_c"]),
                "model_prob": num(r["p_model"]),
                "edge_cents": None if edge is None else edge * 100.0,
                "result": None,  # halftime_scored stores no settlement
            }
        )
    return out


def _scrub(x: Any) -> Any:
    """NaN/inf -> None so the JSON is strict."""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: _scrub(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_scrub(v) for v in x]
    return x


def _track_record(root: Path) -> tuple[dict[str, Any], str | None]:
    files = sorted((root / SCORECARD_DIR).glob("*.json"))
    if not files:
        return {"scorecard_date": None, "model_vs_market": None, "calibration": []}, None
    f = files[-1]
    sc = _scrub(json.loads(f.read_text()))
    bd = ((sc.get("part3") or {}).get("all") or {}).get("brier_diff")
    mvm: dict[str, Any] | None = None
    if isinstance(bd, dict):
        lo, hi = bd.get("lo"), bd.get("hi")
        has_ci = lo is not None and hi is not None
        mvm = {
            "n": bd.get("n", 0),
            "metric": "brier",
            "diff": bd.get("value"),
            "ci95": [lo, hi] if has_ci else None,
            "clustered_on": "game_id",
            "games": bd.get("games"),
            "reason": None
            if has_ci
            else f"{bd.get('games')} game(s) in the scorecard: no clustered CI yet",
            "note": "diff = part3.all.brier_diff = brier_model - brier_market (positive = worse)",
        }
    return (
        {"scorecard_date": sc.get("date"), "model_vs_market": mvm, "calibration": []},
        f.name,
    )


def _weights(root: Path) -> tuple[float | None, dict[str, float], str | None]:
    p = root / WEIGHTS
    if not p.exists():
        return None, {}, None
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    if not rows:
        return None, {}, None
    rows.sort(key=lambda r: str(r.get("ts", "")))
    by: dict[str, float] = {}
    for r in rows:
        w = num(r.get("w_after"))
        if w is not None:
            by[str(r.get("stat"))] = w
    return num(rows[-1].get("w_after")), by, rows[-1].get("ts")


def _balance(root: Path) -> dict[str, float] | None:
    raw = yaml.safe_load((root / CONFIG).read_text())
    b = (raw or {}).get("balance")
    if not isinstance(b, dict):
        return None
    try:
        return {k: float(b[k]) for k in ("start_usd", "current_usd", "floor_usd")}
    except (KeyError, TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------ build


def build(
    root: Path,
    now: datetime | None = None,
    tries: int = OPEN_TRIES,
    sleep_s: float = OPEN_SLEEP_S,
    sleep: Callable[[float], None] = time.sleep,
    reps: int = BOOTSTRAP_REPS,
) -> dict[str, Any]:
    """The private document (a dict). Raises ``DeskExportError``; writes nothing."""
    now = now or datetime.now(UTC)
    book_p, ht_p = root / PAPER_BOOK, root / HALFTIME  # sources show the relative PAPER_BOOK

    stamps: list[datetime] = []
    sources: dict[str, str] = {}

    # Hold the JARVIS book only long enough to copy the rows; ESPN gz parsing happens after close.
    t_open = time.monotonic()
    book = open_ro(book_p, tries, sleep_s, sleep)
    try:
        plays = _plays(book)
        kill_rows = fetch(book, "SELECT * FROM killswitch_log ORDER BY ts")
        flags = fetch(book, "SELECT * FROM ingame_flags ORDER BY ts")
        sends = fetch(book, "SELECT * FROM sends ORDER BY ts")
        for table, col in (
            ("plays", "ts"),
            ("scores", "scored_at"),
            ("sends", "ts"),
            ("ingame_flags", "ts"),
            ("killswitch_log", "ts"),
        ):
            n = scalar(book, f"SELECT count(*) FROM {table}")
            t = scalar(book, f"SELECT max({col}) FROM {table}")
            if isinstance(t, datetime):
                stamps.append(t)
            sources[table] = f"{PAPER_BOOK}: {table} {n} rows, newest {iso(t)}"
    finally:
        book.close()
    hold_s = time.monotonic() - t_open
    ks, _ = _kill(kill_rows)
    obs, alerts = _observations_alerts(flags, sends, kill_rows, EspnNames(root), plays)

    ht = open_ro(ht_p, tries, sleep_s, sleep)
    try:
        halftime = _halftime(ht)
        ht_n = scalar(ht, "SELECT count(*) FROM halftime_scored")
        ht_t = scalar(ht, "SELECT max(written_at) FROM halftime_scored")
    finally:
        ht.close()
    if isinstance(ht_t, datetime):
        stamps.append(ht_t)
    sources["halftime_scored"] = f"{HALFTIME}: {ht_n} rows, newest {iso(ht_t)}"

    rec, daily = _record_daily(plays)
    clv_rows = [
        (p["slate_date"], p["result"]["clv_cents"])
        for p in plays
        if p["result"]
        and p["result"]["status"] == "settled"
        and p["result"]["clv_cents"] is not None
    ]
    clv = clv_summary(clv_rows, reps)
    w_model, w_by_stat, w_ts = _weights(root)
    track, sc_name = _track_record(root)
    balance = _balance(root)

    sources["model_weights"] = f"{WEIGHTS}: newest {w_ts}"
    if w_ts:
        with contextlib.suppress(ValueError):
            stamps.append(datetime.fromisoformat(w_ts))
    sources["forward_scorecard"] = f"{SCORECARD_DIR}/{sc_name}" if sc_name else "none found"
    sources["jarvis_config"] = f"{CONFIG} (balance block only)"
    data_as_of = iso(max(stamps)) if stamps else None

    floor_closed = balance is not None and balance["current_usd"] <= balance["floor_usd"]
    notes = {
        "alerts.text": "sends stores text_hash only; no stored text exists to cite",
        "plays.team": "plays stores team_id only; no id->name table in the book",
        "observations.exit": (
            "exit warnings are computed live and stored nowhere; no 'exit' observations"
        ),
        "observations.risk": "only kill-switch trips are stored; no other risk observations",
        "observations.names": (
            "ESPN athlete ids resolved from captured ESPN summary snapshots; "
            "unresolved ids are shown as ids; observation matchups use ESPN abbreviations"
        ),
        "halftime": (
            "rows with model edge > 0 after fee (halftime_scored keeps every scored strike); "
            "settlement is not stored there so result is null"
        ),
        "kill_switch": (
            "state is the latest killswitch_log row; JARVIS's own latch logic is not re-run here"
        ),
        "model_review": "true while the kill switch is on or balance <= floor",
        "clv": (
            "all settled plays with a stored clv_cents (not the thin/strong/arbitrage bundle rule "
            "JARVIS's kill switch uses); clv_cents is as stored in scores"
        ),
        "track_record.calibration": "the forward scorecard json carries no calibration bins",
        "shared_cut": (
            "balance, stake_usd, contracts, result.pnl_usd and daily.pnl_usd are null when shared"
        ),
    }

    return {
        "schema": SCHEMA,
        "privacy": "private",
        "built_at": iso(now.astimezone(UTC).replace(tzinfo=None)),
        "data_as_of": data_as_of,
        "sources": sources,
        "book_hold_seconds": round(hold_s, 3),
        "status": {
            "kill_switch": ks,
            "model_review": bool(ks["on"]) or floor_closed,
            "w_model": w_model,
            "w_model_by_stat": w_by_stat,
        },
        "balance": balance,
        "plays": plays,
        "clv_summary": clv,
        "record": rec,
        "daily": daily,
        "observations": obs,
        "alerts": alerts,
        "halftime": halftime,
        "track_record": track,
        "section_notes": notes,
    }


# A dollar amount in free text: "$" then a digit, except the unit "per $1".
_DOLLAR = re.compile(r"\$\s?\d")
_PER_ONE = re.compile(r"per \$1(?!\d)")
_FREE_TEXT = ("reason", "explanation", "leg_desc")


def _has_amount(text: str) -> bool:
    return bool(_DOLLAR.search(_PER_ONE.sub("", text)))


def shared_cut(doc: dict[str, Any]) -> dict[str, Any]:
    """Deep copy with every money field null and ``privacy`` = shared.

    Nulled beyond the named fields: ``fee_cents`` (a daily-dollar bundle stores fee/contracts, so it
    reveals the contract count), ``edge_cents`` on daily_dollar plays (edge = fair - ask - fee, so
    it gives the fee back), and ``model_review`` is the kill switch alone (no balance-floor input).
    Free text that contains a dollar amount is SCRUBBED to null (not failed): the build must not
    die because one reason string changed. A leaked absolute path fails the build."""
    out: dict[str, Any] = json.loads(json.dumps(doc))
    out["privacy"] = "shared"
    out["balance"] = None
    out["status"]["model_review"] = bool(out["status"]["kill_switch"]["on"])
    for p in out["plays"]:
        p["stake_usd"] = None
        p["contracts"] = None
        p["fee_cents"] = None
        if p["edge_label"] == "daily_dollar":
            p["edge_cents"] = None
        for k in _FREE_TEXT:
            if isinstance(p[k], str) and _has_amount(p[k]):
                p[k] = None
        if p["result"] is not None:
            p["result"]["pnl_usd"] = None
    for d in out["daily"]:
        d["pnl_usd"] = None
    out["section_notes"]["shared_cut"] = (
        "balance, stake_usd, contracts, fee_cents, result.pnl_usd and daily.pnl_usd are null; "
        "edge_cents is null on daily_dollar plays; model_review = kill switch only; free text "
        "with a dollar amount is nulled"
    )
    blob = json.dumps(out)
    if "/Users/" in blob or re.search(r'"/[A-Za-z]', blob):
        raise DeskExportError("absolute path in the shared cut")
    return out


def write_atomic(out_dir: Path, docs: dict[str, dict[str, Any]]) -> None:
    """Serialise everything first, write temp files, then ``os.replace`` each. A failure before
    the replaces leaves the previous files untouched."""
    blobs = {name: json.dumps(_scrub(d), allow_nan=False, indent=1) for name, d in docs.items()}
    out_dir.mkdir(parents=True, exist_ok=True)
    tmps: dict[str, Path] = {}
    try:
        for name, blob in blobs.items():
            tmp = out_dir / f".{name}.{os.getpid()}.tmp"
            tmp.write_text(blob + "\n")
            tmps[name] = tmp
        for name, tmp in tmps.items():
            os.replace(tmp, out_dir / name)
    finally:
        for tmp in tmps.values():
            if tmp.exists():
                tmp.unlink()


def run(root: Path, out_dir: Path, **kw: Any) -> dict[str, Any]:
    doc = build(root, **kw)
    write_atomic(out_dir, {"desk.json": doc, "desk_shared.json": shared_cut(doc)})
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.export.desk", description=__doc__)
    ap.add_argument("--out", default="data/export_private")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = Path(a.out)
    out = out if out.is_absolute() else root / out
    try:
        doc = run(root, out)
    except DeskExportError as exc:
        print(f"desk export failed, old files kept: {exc}")
        return 1
    print(
        f"desk.json + desk_shared.json -> {out} (data_as_of {doc['data_as_of']}; "
        f"plays {len(doc['plays'])}, observations {len(doc['observations'])}, "
        f"alerts {len(doc['alerts'])}, halftime {len(doc['halftime'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
