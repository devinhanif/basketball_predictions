"""Serve the live card from the committed fixtures, for the browser walk and for looking at it.

The live capture is the trimmed 2026-11-09 fixture (LAL at ATL) re-dated to --date and re-stamped
to now, so the screen reads "as of Q3 3:39, a few seconds ago". The stored lines are the SYNTHETIC
game in fixtures/live_card/stored_game.json (made-up lines, fake player ids). Nothing here reads
tonight's capture or the export. With --replay later bodies are landed a few seconds apart so the
changed-row highlight can be seen.

  cd app/api && PYTHONPATH=src:tests uv run python tests/live_card_demo.py --port 8031 [--replay]
  cd app/web && API_ORIGIN=http://127.0.0.1:8031 npx vite --port 5231   (then open #/live)
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import uvicorn
from live_harness import DATE, EVENT, FIXTURE, copy_fixture

from parlayhitters_api import deps
from parlayhitters_api import live as L
from parlayhitters_api.main import create_app
from parlayhitters_api.store import store_key

CARD_FIX = FIXTURE.parent / "live_card"


class DemoStore:
    def __init__(self, date: str) -> None:
        self.game: dict[str, Any] = copy.deepcopy(
            json.loads((CARD_FIX / "stored_game.json").read_text())
        )
        self.game["date"] = date

    def slate(self, date: str) -> dict[str, Any] | None:
        return {"date": date, "games": [self.game]} if date == self.game["date"] else None


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--date", default=today)
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--phase", choices=["early", "mid", "final"], default="mid")
    a = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="live_card_demo_"))
    copy_fixture(root)
    dest = root / f"live_{a.date}"
    if a.date != DATE:
        (root / f"live_{DATE}").rename(dest)
    bodies = dest / "espn_summary" / EVENT
    names = sorted(p.name for p in bodies.iterdir())  # pre, early, early, mid, final
    keep = {"early": names[2], "mid": names[3], "final": names[4]}
    pool = {n: (bodies / n).read_bytes() for n in names}
    for p in bodies.iterdir():
        p.unlink()

    def land(name: str) -> None:
        tmp = bodies / f"{stamp()}.json.gz.tmp"
        tmp.write_bytes(pool[name])
        tmp.rename(tmp.with_name(tmp.name.removesuffix(".tmp")))

    land(keep[a.phase])
    if a.replay:
        order = [names[3], names[4]] if a.phase == "early" else [names[4]]

        def feed() -> None:
            for n in order:
                time.sleep(25)
                land(n)

        threading.Thread(target=feed, daemon=True).start()
    os.environ[L.LIVE_ENV] = str(root)
    os.environ[L.CHECKPOINT_ENV] = str(root / "none.json")
    deps._cache["store"] = DemoStore(a.date)
    deps._cache["key"] = store_key()
    print(f"demo: capture {root} date {a.date} phase {a.phase} port {a.port}", flush=True)
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
