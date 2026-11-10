"""Trim a few recorded ESPN summary bodies from the live capture into app/fixtures/live/.

Usage: python build_live_fixture.py <path to data/raw/live_2026-11-09>
Keeps header (without logos), plays and boxscore.players only. The bodies are the capture's own,
dated 2026-11-09 (live-capture rows from the 2026-27 season; no forecast and no price in them).
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PICK = {
    "401909952": [
        "20261110T000111Z",
        "20261110T002113Z",
        "20261110T004116Z",
        "20261110T014124Z",
        "20261110T023731Z",
    ],
    "401909955": ["20261110T000111Z"],
}
OUT = Path(__file__).resolve().parent / "live" / "live_2026-11-09" / "espn_summary"
DROP = ("coordinate", "participants", "shortDescription")


def trim(body: dict[str, Any]) -> dict[str, Any]:
    for c in body["header"]["competitions"][0]["competitors"]:
        c["team"].pop("logos", None)
        c.pop("linescores", None)
    out: dict[str, Any] = {
        "header": body["header"],
        "boxscore": {"players": body.get("boxscore", {}).get("players", [])},
    }
    if "plays" in body:
        out["plays"] = [{k: v for k, v in p.items() if k not in DROP} for p in body["plays"]]
    return out


def main(src: Path) -> None:
    if OUT.parent.exists():
        shutil.rmtree(OUT.parent)
    for eid, stamps in PICK.items():
        d = OUT / eid
        d.mkdir(parents=True)
        for s in stamps:
            raw = gzip.decompress((src / "espn_summary" / eid / f"{s}.json.gz").read_bytes())
            blob = json.dumps(trim(json.loads(raw)), separators=(",", ":")).encode()
            (d / f"{s}.json.gz").write_bytes(gzip.compress(blob))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
