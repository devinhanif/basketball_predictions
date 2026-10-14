"""Read-only fixture store. The files are only ever opened for reading.

When nba.export exists this module is the single seam that changes: same methods, backed by
the nightly export.duckdb opened read_only=True.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .history import History

Json = dict[str, Any]

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


class Store:
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
