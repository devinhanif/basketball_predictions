# ruff: noqa: E501
"""The in-game checkpoint snapshot the app reads (``docs/contracts/ingame_checkpoint_snapshot_v2.md``).

The app never opens ``data/jarvis/ingame_checkpoints.duckdb`` (a read-only connection takes a shared
file lock and the live writer connects without retry); it reads ONE JSON file this module writes: to a
temp file in the same directory, then ``os.replace`` (atomic). Written only when the in-game checkpoint
flag is on, by the JARVIS tick AFTER the checkpoint rows are stored, inside its own guard: it never
raises and never blocks the tick (a failure is returned as ``{"ok": False, ...}``).

v2 adds, per game, a ``script`` block: P(blowout / close / shootout / grind) given the live score and the
clock (``nba.parlay.game_scripts.live_script_probs``, independent increments on the game model's
pre-game normals). If the pre-game numbers for the game are not on disk, the block is simply absent:
the snapshot ships without it rather than guessing. No price, ask, bid or edge is ever written.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("nba.markets.checkpoint_snapshot")

VERSION = 2
MAX_BYTES = 5_000_000
STATE_MAX_AGE_S = 180.0  # a state older than this at write time carries no script block
MAX_ROWS_PER_GAME = 200
ROW_KEYS = ("stat", "k", "p_model", "cur", "minutes", "fouls")
METHOD = "independent_increments_v1"
SCRIPT_LABEL = (
    "EXPLORATORY and UNPROVEN: script chances from the live score and clock on the pre-game game model "
    "(independent increments; pace is not extrapolated; overtime is not modelled, so a tied game late in "
    "regulation can still end outside +-5). No live record yet."
)

_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


def iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        t = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
        return t.astimezone(UTC).isoformat(timespec="seconds")
    return str(ts)


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def load_pregame(path: Path) -> dict[str, dict[str, Any]]:
    """``"AWAY@HOME"`` -> the game block of ``combo_<night>.json`` (cached by mtime) plus ``_shared``.
    Empty when the file is absent or unreadable (the snapshot then has no script block)."""
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns)
    if key not in _CACHE:
        try:
            doc = json.loads(path.read_text())
            shared = {
                "scripts": {"definitions": doc["shared"]["scripts"]["definitions"]},
                "corr": [],
            }
            games = {
                f"{g['away_abbr']}@{g['home_abbr']}": {**g, "_shared": shared}
                for g in doc["games"]
                if g.get("away_abbr") and g.get("home_abbr")
            }
        except (OSError, ValueError, KeyError, TypeError):
            games = {}
        _CACHE.clear()
        _CACHE[key] = games
    return _CACHE[key]


def script_block(
    pre: Mapping[str, Any], state: Any, state_at: datetime | None, now: datetime | None = None
) -> dict[str, Any] | None:
    """The live script block for one game, or None when the state cannot be used (no score, overtime,
    unreadable clock). ``state`` is a ``checkpoint_scorer.State``."""
    from nba.parlay import game_scripts as GS

    if (
        now is not None
        and state_at is not None
        and (_aware(now) - _aware(state_at)).total_seconds() > STATE_MAX_AGE_S
    ):
        return None  # a stale state is not shown as live
    if (
        state is None
        or state.elapsed_s is None
        or state.home_score is None
        or state.away_score is None
    ):
        return None
    if state.period > 4 or state.elapsed_s >= GS.REGULATION_MIN * 60.0:
        return None  # overtime is out of scope, as for the checkpoints
    e = state.elapsed_s / 60.0
    margin = int(state.home_score) - int(state.away_score)
    total = int(state.home_score) + int(state.away_score)
    shared = pre["_shared"]
    p = GS.live_script_probs(pre, shared, e, margin, total)
    prior = GS.script_priors(pre, shared)
    return {
        "status": GS.STATUS, "method": METHOD, "label": SCRIPT_LABEL,
        "elapsed_min": round(e, 2), "margin": margin, "total": total,
        "state_at": iso(state_at), "home_abbr": pre["home_abbr"], "away_abbr": pre["away_abbr"],
        "pregame": dict(pre["game"]),
        "p": {s: round(p[s], 4) for s in GS.SCRIPTS},
        "prior": {s: round(prior[s], 4) for s in GS.SCRIPTS},
        "rules": {k: v["rule"] for k, v in GS.script_rules(pre, shared).items()},
    }  # fmt: skip


def build_snapshot(
    rows_by_game: Mapping[str, Sequence[Mapping[str, Any]]],
    states: Mapping[str, Any],
    pregame: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """One entry per game with stored checkpoint rows (key = ESPN event id), v1 fields exactly, plus the
    v2 ``script`` block where the game's pre-game numbers and a usable live state exist."""
    games: dict[str, Any] = {}
    for game, rows in rows_by_game.items():
        if not rows:
            continue
        r0 = max(rows, key=lambda r: (r["checkpoint"], str(r["known_at"])))
        eid = str(r0["event_id"])
        cur_rows = [r for r in rows if r["checkpoint"] == r0["checkpoint"]][:MAX_ROWS_PER_GAME]
        entry: dict[str, Any] = {
            "checkpoint": int(r0["checkpoint"]), "e_used": float(r0["e_used"]),
            "support": str(r0["model_support"]), "period": int(r0["period"]),
            "clock": str(r0["clock"]), "known_at": iso(r0["known_at"]),
            "rows": [
                {"player": str(r["player_name"]), **{k: r[k] for k in ROW_KEYS}} for r in cur_rows
            ],
        }  # fmt: skip
        pre = pregame.get(game)
        st = states.get(eid)
        if pre is not None and st is not None:
            blk = script_block(pre, st, getattr(st, "received_at", None), now)
            if blk is not None:
                entry["script"] = blk
        games[eid] = entry
    return {"version": VERSION, "generated_at": iso(now), "games": games}


def write_snapshot(path: Path, snap: Mapping[str, Any]) -> int:
    """Atomic write (temp file in the same directory, then rename). Returns the bytes written; refuses a
    document over ``MAX_BYTES`` (the app's limit) rather than writing a truncated one."""
    text = json.dumps(snap, separators=(",", ":"), sort_keys=True, default=str, allow_nan=False)
    if len(text) > MAX_BYTES:
        raise ValueError(f"snapshot {len(text)} bytes exceeds {MAX_BYTES}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(text)


def publish(
    path: Path,
    pregame_path: Path,
    rows_by_game: Mapping[str, Sequence[Mapping[str, Any]]],
    states: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Build and write; never raises (a snapshot problem costs the snapshot, not the tick)."""
    try:
        snap = build_snapshot(rows_by_game, states, load_pregame(pregame_path), now)
        n = write_snapshot(path, snap)
        return {
            "ok": True, "bytes": n, "games": len(snap["games"]),
            "scripts": sum(1 for g in snap["games"].values() if "script" in g),
        }  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        log.warning("checkpoint snapshot failed (%s: %s)", type(exc).__name__, str(exc)[:120])
        return {"ok": False, "error": type(exc).__name__}


def pregame_path(root: Path, slate: date, directory: str = "data/export/latest") -> Path:
    return root / directory / f"combo_{slate.isoformat()}.json"
