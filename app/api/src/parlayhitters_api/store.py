"""Read-only stores. The files are only ever opened for reading.

``Store`` serves the committed fixtures (two real 2024-25 nights, built from stored data).
``load_store`` returns an ``ExportStore`` instead when the configured export directory holds a
manifest (``APP_EXPORT_DIR``), and the fixtures otherwise. Both expose the same surface:
``slates``, ``games``, ``game_slate``, ``history``, ``parlay``, ``evidence``, ``slate()``,
``game()``, ``clock()``, ``slate_clock()`` and ``meta()``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .export_store import ExportStore, has_export, manifest_path
from .history import History

Json = dict[str, Any]

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
EXPORT_ENV = "APP_EXPORT_DIR"
FIXTURE_MODE = "fixture_replay"
log = logging.getLogger("parlayhitters")


class Store:
    mode = FIXTURE_MODE

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(os.environ.get("APP_FIXTURES_DIR") or root or DEFAULT_FIXTURES)
        self.manifest: Json = self._load("manifest.json")
        self.evidence: Json = self._load("evidence.json")
        self.parlay: Json = self._load("parlay_of_day.json")
        self.slates: dict[str, Json] = {}
        self.games: dict[str, Json] = {}
        self.game_slate: dict[str, str] = {}
        self.history: History | None = None
        if (self.root / "breakdown.json").exists():
            self.history = History(self._load("breakdown.json"))
            if any(v[1] > int(self.manifest["max_season"]) for v in self.history.games.values()):
                raise ValueError("breakdown history is beyond the season cap")
        for entry in self.manifest["slates"]:
            slate = self._load(entry["file"])
            if int(slate["season"]) > int(self.manifest["max_season"]):
                raise ValueError(f"fixture slate {slate['date']} is beyond the season cap")
            self.slates[slate["date"]] = slate
            for g in slate["games"]:
                self.games[g["game_id"]] = g
                self.game_slate[g["game_id"]] = slate["date"]

    def _load(self, rel: str) -> Json:
        with (self.root / rel).open("r", encoding="utf-8") as fh:  # read-only by construction
            data: Json = json.load(fh)
        return data

    def slate(self, date: str) -> Json | None:
        return self.slates.get(date)

    def game(self, game_id: str) -> Json | None:
        return self.games.get(game_id)

    def clock(self, game_id: str) -> str:
        """Replay mode: the clock is frozen on the slate (see the README)."""
        return str(self.slates[self.game_slate[game_id]]["demo_clock_utc"])

    def slate_clock(self, date: str) -> str:
        return str(self.slates[date]["demo_clock_utc"])

    def meta(self) -> Json:
        m = self.manifest
        return {
            "mode": self.mode,
            "mode_note": (
                "A replay of real 2024-25 nights, built from stored data. Nothing here is live, "
                "and nothing here is advice."
            ),
            "data_version": m["source_data_version"],
            "built_at": m["built_at"],
            "data_as_of": m["built_at"],
            "export_date": None,
            "max_season": m["max_season"],
            "slates": [s["date"] for s in m["slates"]],
            "stubs": m["stubs"],
            "sources": m["sources"],
            "queries": m["queries"],
            "export": None,
        }


AnyStore = Store | ExportStore


def export_dir() -> Path | None:
    raw = os.environ.get(EXPORT_ENV)
    return Path(raw) if raw else None


def store_key() -> tuple[str, int, int]:
    """What identifies the data on disk now: changes when a new export lands (its manifest is
    written last, atomically), so the API picks it up without a restart."""
    d = export_dir()
    if has_export(d) and d is not None:
        st = manifest_path(d).stat()
        return (str(d), st.st_mtime_ns, st.st_size)
    return ("", 0, 0)


def load_store() -> AnyStore:
    """The export when one exists (verified; ``ExportRefused`` if it cannot be trusted), else the
    fixtures. A configured directory with no manifest falls back, with a warning in the log."""
    d = export_dir()
    if d is not None and has_export(d):
        return ExportStore(d)
    if d is not None:
        log.warning("%s=%s has no manifest.json; serving the fixtures", EXPORT_ENV, d)
    return Store()
