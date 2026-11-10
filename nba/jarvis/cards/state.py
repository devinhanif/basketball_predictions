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
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # a torn last line from a killed process is skipped, never fatal
        if isinstance(rec, dict):
            out.append(rec)
    return out


@dataclass(frozen=True)
class CardRecord:
    slate: str
    game_id: str
    team: str
    message_id: str
    phase: str
    content_hash: str
    fp: str  # webhook fingerprint
    kind: str  # post | edit | repost
    ts: str


class CardState:
    def __init__(self, state_path: Path, picks_path: Path) -> None:
        self.state_path = state_path
        self.picks_path = picks_path

    # ------------------------------------------------------------------ messages
    def latest(self, slate: date | str, game_id: str, team: str) -> CardRecord | None:
        found: CardRecord | None = None
        for r in _read(self.state_path):
            if (
                r.get("slate") == str(slate)
                and r.get("game_id") == game_id
                and r.get("team") == team
            ):
                found = CardRecord(
                    str(r["slate"]), str(r["game_id"]), str(r["team"]), str(r["message_id"]),
                    str(r["phase"]), str(r["content_hash"]), str(r.get("fp", "")),
                    str(r.get("kind", "")), str(r.get("ts", "")),
                )  # fmt: skip
        return found

    def record(self, rec: CardRecord) -> None:
        _append(self.state_path, rec.__dict__)

    # ------------------------------------------------------------------ picks
    def picks(self, slate: date | str | None = None) -> list[dict[str, Any]]:
        """Pick records (first sight of each over) with settlements folded in, oldest first."""
        firsts: dict[str, dict[str, Any]] = {}
        for r in _read(self.picks_path):
            if r.get("rec") == "pick":
                firsts.setdefault(str(r["pick_id"]), dict(r))
        for r in _read(self.picks_path):
            if r.get("rec") == "settled" and str(r.get("pick_id")) in firsts:
                firsts[str(r["pick_id"])]["settled"] = r
        out = list(firsts.values())
        if slate is not None:
            out = [p for p in out if p.get("slate") == str(slate)]
        return out

    def add_pick(self, pick: dict[str, Any]) -> bool:
        """Log an over the first time a card shows it; False if it was already logged."""
        pid = str(pick["pick_id"])
        if any(
            str(r.get("pick_id")) == pid for r in _read(self.picks_path) if r.get("rec") == "pick"
        ):
            return False
        _append(self.picks_path, {"rec": "pick", **pick})
        return True

    def add_settlement(self, pick_id: str, rec: dict[str, Any]) -> None:
        _append(self.picks_path, {"rec": "settled", "pick_id": pick_id, **rec})
