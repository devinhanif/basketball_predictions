"""Append-only card state: which Discord message is each game's card, and the overs we showed.

Two JSON-lines files, written one line at a time and flushed, never rewritten:

* ``cards_state.jsonl``: one record per post / edit / repost, keyed (slate, game, team) with the
  message id, the phase it shows, a hash of the card content and a short fingerprint of the webhook
  (a rotated webhook cannot edit the old message). The latest record per key wins, so a restart
  (launchd starts a fresh process every tick) edits the same message instead of posting again.
* ``card_picks.jsonl``: every over a card showed, at the price shown FIRST (prediction time; a later
  price never overwrites it), plus its settlement once the box score exists. This is the paper log
  the recap and the season record are read from.

A dry run writes nothing. Message ids and fingerprints are not secrets; no URL is ever stored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PHASE_RANK = {"pretip": 0, "live": 1, "halftime": 2, "final": 3}


def _append(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    with path.open("ab") as fh:
        if fh.tell() > 0:
            with path.open("rb") as rd:
                rd.seek(-1, os.SEEK_END)
                torn = rd.read(1) != b"\n"
            if (
                torn
            ):  # a killed process left a partial line: end it so this record is not glued to it
                fh.write(b"\n")
        fh.write(line.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def _read(path: Path) -> tuple[list[dict[str, Any]], int]:
    """(records, lines skipped). A torn or non-object line is skipped and counted, never fatal."""
    if not path.exists():
        return [], 0
    out: list[dict[str, Any]] = []
    bad = 0
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            bad += 1
    return out, bad


@dataclass(frozen=True)
class CardRecord:
    slate: str
    game_id: str
    team: str
    message_id: str  # "" = Discord accepted the post but the reply had no id (cannot be edited)
    phase: str
    content_hash: str
    fp: str  # webhook fingerprint
    kind: str  # post | edit | repost | lost
    ts: str


_REQUIRED = ("slate", "game_id", "team", "message_id", "phase", "content_hash")


class CardState:
    """Both files are read ONCE (first use) into dictionaries and kept current by this object's own
    writes, so a lookup is O(1) however long the season gets. One instance per card pass."""

    def __init__(self, state_path: Path, picks_path: Path) -> None:
        self.state_path = state_path
        self.picks_path = picks_path
        self.skipped = 0  # torn / malformed lines seen (reported, never raised)
        self._latest: dict[tuple[str, str, str], CardRecord] | None = None
        self._picks: dict[str, dict[str, Any]] | None = None

    def _load(self) -> None:
        if self._latest is not None and self._picks is not None:
            return
        latest: dict[tuple[str, str, str], CardRecord] = {}
        recs, bad = _read(self.state_path)
        self.skipped += bad
        for r in recs:
            if any(k not in r for k in _REQUIRED):
                self.skipped += 1
                continue
            latest[(str(r["slate"]), str(r["game_id"]), str(r["team"]))] = _card(r)
        picks: dict[str, dict[str, Any]] = {}
        prs, bad = _read(self.picks_path)
        self.skipped += bad
        for r in prs:
            if "pick_id" not in r:
                self.skipped += 1
                continue
            if r.get("rec") == "pick" and all(k in r for k in _PICK_REQUIRED):
                picks.setdefault(str(r["pick_id"]), dict(r))
        for r in prs:
            if r.get("rec") == "settled" and str(r.get("pick_id")) in picks:
                picks[str(r["pick_id"])]["settled"] = r
        self._latest, self._picks = latest, picks

    # ------------------------------------------------------------------ messages
    def latest(self, slate: date | str, game_id: str, team: str) -> CardRecord | None:
        self._load()
        assert self._latest is not None
        return self._latest.get((str(slate), game_id, team))

    def record(self, rec: CardRecord) -> None:
        self._load()
        assert self._latest is not None
        _append(self.state_path, rec.__dict__)
        self._latest[(rec.slate, rec.game_id, rec.team)] = rec

    # ------------------------------------------------------------------ picks
    def picks(self, slate: date | str | None = None) -> list[dict[str, Any]]:
        """Pick records (first sight of each over) with settlements folded in, oldest first."""
        self._load()
        assert self._picks is not None
        out = list(self._picks.values())
        if slate is not None:
            out = [p for p in out if p.get("slate") == str(slate)]
        return out

    def add_pick(self, pick: dict[str, Any]) -> bool:
        """Log an over the first time a card shows it; False if it was already logged."""
        self._load()
        assert self._picks is not None
        pid = str(pick["pick_id"])
        if pid in self._picks:
            return False
        _append(self.picks_path, {"rec": "pick", **pick})
        self._picks[pid] = {"rec": "pick", **pick}
        return True

    def add_settlement(self, pick_id: str, rec: dict[str, Any]) -> None:
        self._load()
        assert self._picks is not None
        row = {"rec": "settled", "pick_id": pick_id, **rec}
        _append(self.picks_path, row)
        if pick_id in self._picks:
            self._picks[pick_id]["settled"] = row


_PICK_REQUIRED = (
    "slate", "game_id", "ticker", "venue", "player", "stat", "line", "ask", "fee_c", "edge_c",
)  # fmt: skip


def _card(r: dict[str, Any]) -> CardRecord:
    return CardRecord(
        str(r["slate"]), str(r["game_id"]), str(r["team"]), str(r["message_id"]),
        str(r["phase"]), str(r["content_hash"]), str(r.get("fp", "")),
        str(r.get("kind", "")), str(r.get("ts", "")),
    )  # fmt: skip
