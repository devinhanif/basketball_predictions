"""The live score card: the live box score the hub holds, joined to the export's stored lines.

Facts only. Every number is a stored field or the live box; the only arithmetic is the count a
player still needs (floor(line) + 1 minus the count) and the regulation game time left (48 minutes
minus the game's elapsed minutes). Nothing here is a chance computed in the app. A chance appears
only where the export stored a calibrated one (``forecast.shown_probs`` source ``line_cal`` or
``rung_cal``; a raw model number is never passed on), only before tip, or in play on a night
outside the LINE_CAL_LIVE lock (``trackrecord.MODEL_LOCK_FROM``), never for a final game. That
mirrors the web's ``chanceLocked``.

Step 2, behind ``INGAME_MODEL_CHANCE_ON`` (off): the in-game model's chance for the same prop, read
from the version-2 checkpoint snapshot (``docs/contracts/ingame_checkpoint_snapshot_v2.md``), with
the age check, labelled ``INGAME_LABEL``. The model's running score is never computed or shown
anywhere. The version-1 reader in ``live.py`` is not touched.

Nothing here opens a DuckDB file or a network socket. The export store is read through
``deps.get_store`` (read-only, verified); a store that has not finished its first load is never
waited on, the card answers with the live facts and ``lines_state: "loading"``.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import deps
from .forecast import shown_probs
from .live import GameState, checkpoint_snapshot_path
from .store import store_key
from .trackrecord import MODEL_LOCK_FROM

#: Stats the card tracks, in the order a row set lists them (the box carries each of these).
TRACK_STATS = ("pts", "reb", "ast", "fg3m")
REGULATION_MIN = 48.0
FEED_NOTE = (
    "The live feed runs about a minute behind the game, so the count and the clock here can trail "
    "what is on TV."
)

# --------------------------------------------------------------------------- step 2 (off)

#: The one switch for the in-game model's chance. OFF until the main session turns it on after
#: ``agent/market-game-scripts-2`` merges and Devin has looked. With it off the card payload carries
#: no ``in_game`` field at all and the version-2 file is never read.
INGAME_MODEL_CHANCE_ON = False
#: The exact words shown beside the in-game model's chance.
INGAME_LABEL = "Experimental, not yet tested"
#: Contract v2: the writer rewrites the file every tick and drops state older than 180 s; a file
#: whose ``generated_at`` is older than this means the writer stopped, so it shows nothing.
SNAPSHOT_STATE_MAX_AGE_S = 180.0
SNAPSHOT_VERSION = 2
_SNAPSHOT_TTL_S = 15.0
_SNAPSHOT_MAX_BYTES = 5_000_000


def ingame_model_on() -> bool:
    return INGAME_MODEL_CHANCE_ON


# --------------------------------------------------------------------------- names

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def norm_name(name: str) -> str:
    """Same rule as the web's ``normName``: accents, case, punctuation and suffixes dropped."""
    nfd = unicodedata.normalize("NFD", name)
    s = "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[.'’-]", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_row(name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A player's live box row by name inside one team: the exact normalised name, else a unique
    last name plus first initial. Never a guess between two."""
    want = norm_name(name)
    exact = [r for r in rows if norm_name(str(r["name"])) == want]
    if len(exact) == 1:
        return exact[0]
    parts = want.split(" ")
    last, first = parts[-1], parts[0][:1]
    loose = []
    for r in rows:
        p = norm_name(str(r["name"])).split(" ")
        if p[-1] == last and p[0][:1] == first:
            loose.append(r)
    return loose[0] if len(loose) == 1 else None


# --------------------------------------------------------------------------- arithmetic


def needed(line: float, cur: int) -> int:
    """An over of ``line`` needs floor(line) + 1 in all."""
    return max(0, math.floor(line) + 1 - cur)


def regulation_left_min(state: str, period: int, elapsed: float | None) -> float | None:
    """Game minutes left in regulation from the game clock alone (None with no clock)."""
    if state == "pre":
        return REGULATION_MIN
    if state == "post":
        return 0.0
    if elapsed is None:
        return None
    return round(max(0.0, REGULATION_MIN - elapsed), 2)


def clock_label(period: int, clock: str) -> str:
    return f"Q{period} {clock}" if period <= 4 else f"OT{period - 4} {clock}"


# --------------------------------------------------------------------------- the stored side


def _static_rows(game: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every tracked (player, stat) of one stored game: the posted line, the calibrated chance where
    the export has one, the stored price. No live data here, so it is cached per export."""
    rows: list[dict[str, Any]] = []
    counts = {"players": 0, "rows": 0, "reference_only": 0}
    for pl in game["players"]:
        has = False
        for st in TRACK_STATS:
            pr = pl["props"].get(st)
            if pr is None:
                continue
            if pr.get("line_source", "posted") != "posted" or pr.get("line") is None:
                counts["reference_only"] += 1  # a reference line is not a line anyone set
                continue
            line = float(pr["line"])
            sp = shown_probs(pr, line)
            calibrated = sp.source in ("line_cal", "rung_cal")
            price = None
            if pr.get("over_price") is not None and pr.get("under_price") is not None:
                price = {
                    "over": int(pr["over_price"]),
                    "under": int(pr["under_price"]),
                    "book": pr.get("price_book"),
                    "snapshot_utc": pr.get("line_snapshot_utc"),
                }
            rows.append(
                {
                    "player_id": int(pl["player_id"]),
                    "name": str(pl["name"]),
                    "side": str(pl["side"]),
                    "stat": st,
                    "line": line,
                    "calibrated": None if not calibrated else float(sp.p_over),
                    "chance_source": sp.source if calibrated else None,
                    "price": price,
                }
            )
            has = True
        counts["players"] += int(has)
    counts["rows"] = len(rows)
    return rows, counts


_static_cache: dict[tuple[Any, str], tuple[list[dict[str, Any]], dict[str, int]]] = {}
_static_lock = threading.Lock()


def static_for(key: Any, game: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ck = (key, str(game["game_id"]))
    with _static_lock:
        hit = _static_cache.get(ck)
    if hit is not None:
        return hit
    built = _static_rows(game)
    with _static_lock:
        if len(_static_cache) > 64:
            _static_cache.clear()
        _static_cache[ck] = built
    return built


def find_stored_game(store: Any, date: str, home: str, away: str) -> dict[str, Any] | None:
    """The export's game for this night and these two teams, or None."""
    slate = store.slate(date)
    if slate is None:
        return None
    for g in slate["games"]:
        if g["home"]["abbr"] == home and g["away"]["abbr"] == away:
            found: dict[str, Any] = g
            return found
    return None


# ------------------------------------------------------------------ the store, never waited on

_load_started: dict[str, bool] = {}
_load_failed: dict[str, str] = {}


def store_if_ready() -> tuple[Any, str] | tuple[None, str]:
    """(store, "ok") when the export store is loaded for the current export; else start loading it
    in a worker thread and return (None, "loading" | "unavailable"). Never blocks a request."""
    key = store_key()
    if deps._cache.get("key") == key and "store" in deps._cache:
        return deps._cache["store"], "ok"
    ks = repr(key)
    if ks in _load_failed:
        return None, "unavailable"
    if not _load_started.get(ks):
        _load_started[ks] = True

        def work() -> None:
            try:
                deps.get_store()
            except Exception as exc:  # a refused export: say so, never fall back to a sample
                _load_failed[ks] = type(exc).__name__

        threading.Thread(target=work, name="live-card-store", daemon=True).start()
    return None, "loading"


# ------------------------------------------------------------------ the version-2 snapshot (step 2)

_snap_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_snap_lock = threading.Lock()


def _parse_ts(v: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _load_snapshot_v2(path: Path) -> dict[str, Any] | None:
    """The published snapshot when it is version 2 and well formed, else None. Cached 15 s."""
    key = str(path)
    with _snap_lock:
        hit = _snap_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _SNAPSHOT_TTL_S:
            return hit[1]
    data: dict[str, Any] | None = None
    try:
        raw = path.read_bytes()
        if len(raw) <= _SNAPSHOT_MAX_BYTES:
            parsed = json.loads(raw)
            if (
                isinstance(parsed, dict)
                and parsed.get("version") == SNAPSHOT_VERSION
                and isinstance(parsed.get("games"), dict)
            ):
                data = parsed
    except (OSError, ValueError):
        data = None
    with _snap_lock:
        _snap_cache[key] = (time.monotonic(), data)
    return data


def read_ingame_v2(
    event_id: str, game_state: str, now: datetime, path: Path | None = None
) -> dict[str, Any]:
    """The in-game model's rows for one game from the version-2 snapshot, with an age check.

    ``{"available": False, "reason": ...}`` when anything is missing, too old, or the game is not in
    play. Otherwise ``{"available": True, "known_at", "age_s", "period", "clock", "by_key": {(norm
    player name, stat, k): p_model}}``. Only these fields are copied; no price or edge is read.
    A snapshot whose ``generated_at`` is older than ``SNAPSHOT_STATE_MAX_AGE_S`` shows nothing."""
    base: dict[str, Any] = {"available": False}
    if game_state != "in":
        return {**base, "reason": "only for a game in play"}
    snap = _load_snapshot_v2(path or checkpoint_snapshot_path())
    if snap is None:
        return {**base, "reason": "no version-2 snapshot"}
    made = _parse_ts(snap.get("generated_at"))
    if made is None or (now - made).total_seconds() > SNAPSHOT_STATE_MAX_AGE_S:
        return {**base, "reason": "snapshot too old"}
    g = snap["games"].get(event_id)
    if not isinstance(g, dict) or not g.get("rows"):
        return {**base, "reason": "no rows for this game"}
    known = _parse_ts(g.get("known_at"))
    try:
        by_key = {
            (norm_name(str(r["player"])), str(r["stat"]), int(r["k"])): float(r["p_model"])
            for r in g["rows"][:200]
            if 0.0 <= float(r["p_model"]) <= 1.0
        }
        period, clock = int(g["period"]), str(g["clock"])
    except (KeyError, TypeError, ValueError):
        return {**base, "reason": "snapshot not readable"}
    if known is None:
        return {**base, "reason": "snapshot not readable"}
    return {
        "available": True,
        "known_at": known.isoformat(),
        "age_s": round(max(0.0, (now - known).total_seconds()), 1),
        "period": period,
        "clock": clock,
        "by_key": by_key,
    }


# --------------------------------------------------------------------------- the card


def _status(row: dict[str, Any] | None, state: str) -> tuple[str, str | None]:
    if row is None:
        return "no_box", None
    if row.get("dnp"):
        return "dnp", str(row["dnp"])
    if state == "pre":
        return "pregame", None
    mins = row.get("min") or 0
    if mins == 0 and state == "post":
        return "dnp", "did not play"
    if mins == 0:
        return "not_entered", None
    if int(row.get("pf") or 0) >= 6:
        return "fouled_out", None
    return "playing", None


def _show_chance(state: str, date: str) -> bool:
    """Before tip; in play only outside the LINE_CAL_LIVE lock (MODEL_LOCK_FROM); never final."""
    if state == "pre":
        return True
    return state == "in" and date < MODEL_LOCK_FROM


def build_card(
    gs: GameState,
    stored: tuple[list[dict[str, Any]], dict[str, int]] | None,
    lines_state: str,
    now: datetime,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One game's card. ``stored`` is the cached static side (None when no stored game)."""
    period = int(gs.status.get("period") or 0)
    clock = str(gs.status.get("clock") or "")
    elapsed = gs.status.get("elapsed_min")
    left = regulation_left_min(gs.state, period, elapsed)
    overtime = gs.state == "in" and period > 4
    locked = gs.date >= MODEL_LOCK_FROM
    chance_ok = _show_chance(gs.state, gs.date)
    box_by_team: dict[str, list[dict[str, Any]]] = {gs.home["abbr"]: [], gs.away["abbr"]: []}
    for r in gs.box.values():
        box_by_team.setdefault(r["team"], []).append(r)
    rows: list[dict[str, Any]] = []
    counts = {"players": 0, "rows": 0, "reference_only": 0}
    if stored is not None:
        static_rows, counts = stored
        for s in static_rows:
            team = gs.home["abbr"] if s["side"] == "home" else gs.away["abbr"]
            box = match_row(s["name"], box_by_team.get(team, []))
            status, reason = _status(box, gs.state)
            cur = None if box is None else int(box[s["stat"]])
            need = None if cur is None else needed(s["line"], cur)
            row = {
                "key": f"{s['player_id']}|{s['stat']}",
                "player_id": s["player_id"],
                "name": s["name"],
                "team": team,
                "side": s["side"],
                "stat": s["stat"],
                "line": s["line"],
                "cur": cur,
                "need": need,
                "cleared": cur is not None and cur > s["line"],
                "minutes": None if box is None else box.get("min"),
                "fouls": None if box is None else int(box.get("pf") or 0),
                "starter": False if box is None else bool(box.get("starter")),
                "status": status,
                "reason": reason,
                "chance": s["calibrated"] if chance_ok else None,
                "chance_source": s["chance_source"]
                if chance_ok and s["calibrated"] is not None
                else None,
                "price": s["price"],
            }
            if model is not None and model.get("available") and box is not None:
                k = math.floor(s["line"]) + 1
                p = model["by_key"].get((norm_name(s["name"]), s["stat"], k))
                if p is not None:
                    row["in_game"] = {
                        "chance": p,
                        "label": INGAME_LABEL,
                        "known_at": model["known_at"],
                        "age_s": model["age_s"],
                        "as_of": clock_label(model["period"], model["clock"]),
                    }
            rows.append(row)
    if lines_state == "ok" and stored is None:
        lines_state = "no_stored_game"
    return {
        "game_id": gs.event_id,
        "date": gs.date,
        "state": gs.state,
        "tip_utc": gs.tip_utc,
        "away": gs.away,
        "home": gs.home,
        "status": gs.status,
        "as_of": {
            "label": clock_label(period, clock) if gs.state == "in" and period else None,
            "source": gs.source,
            "fetched_at": gs.fetched_at,
            "server_now": now.isoformat(),
            "age_s": gs.meta(now)["age_s"],
        },
        "feed_note": FEED_NOTE,
        "regulation_left_min": left,
        "overtime": overtime,
        "lines_state": lines_state,
        "chance_locked": locked and gs.state != "pre",
        "tracked": {
            "rows": len(rows),
            "players": counts["players"],
            "reference_only": counts["reference_only"],
            "text": tracked_text(lines_state, len(rows), counts),
        },
        "model_on": bool(model and model.get("available")),
        "rows": rows,
    }


def tracked_text(lines_state: str, n_rows: int, counts: dict[str, int]) -> str:
    """One plain line: how many lines are tracked and why others are not."""
    if lines_state == "loading":
        return "Loading tonight's stored lines. The live score and box score are already below."
    if lines_state == "unavailable":
        return "The stored lines could not be read, so no player is tracked. The score updates."
    if lines_state == "no_stored_game":
        return "No stored lines for this game in tonight's data, so no player is tracked."
    why = (
        f" {counts['reference_only']} more stored lines are the model's own reference point, not a "
        "sportsbook line, so they are not tracked."
        if counts["reference_only"]
        else ""
    )
    if n_rows == 0:
        return "No player in this game has a posted line in the stored data." + why
    return f"Tracking {n_rows} posted lines for {counts['players']} players." + why
