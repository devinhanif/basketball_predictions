"""Dump what the public pages need into one JSON that the artifact build inlines.

Every response is produced by the API's own code (FastAPI TestClient over the same routes and the
same export store), so the wall holds: this file imports nothing from ``nba``. The export is opened
read-only by the store, exactly as when the API serves it. Nothing is computed here: a response is
stored as the API answered it, keyed by the path the page requests.

    APP_EXPORT_DIR=<export dir> uv run python -m parlayhitters_api.bake_artifact --out <file>

Output layout (``format`` 1):
    simple : path -> {"s": status, "b": body}        small answers, plain JSON
    shards : name -> base64(gzip(JSON {path: {"s", "b"}}))   one shard per game and per night
The page finds a path's shard by rule (bakedStore.ts shardOf) and decodes it on first use.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient

from .main import STATS, create_app, get_store, reset_store

Json = dict[str, Any]
SIDES = ("over", "under")
FORMAT = 1


def _shard(entries: dict[str, Json]) -> str:
    raw = json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode("ascii")


class Baker:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.simple: dict[str, Json] = {}
        self.groups: dict[str, dict[str, Json]] = {}

    def fetch(self, path: str) -> Json:
        r = self.c.get(path)
        try:
            body: Any = r.json()
        except ValueError:
            body = None
        return {"s": r.status_code, "b": body}

    def put_simple(self, path: str) -> Json:
        e = self.fetch(path)
        self.simple[path] = e
        return e

    def put_group(self, group: str, path: str) -> Json:
        e = self.fetch(path)
        if e["s"] == 200 or e["s"] == 404:
            self.groups.setdefault(group, {})[path] = e
        return e


def _card_pool(client: TestClient, date: str, max_seeds: int = 60) -> list[Json]:
    """Every swipe card of a night: the API caps a deck at 60, so draw with several seeds until a
    run of draws adds nothing new."""
    seen: dict[str, Json] = {}
    quiet = 0
    for seed in range(max_seeds):
        r = client.get(f"/api/slates/{date}/swipe?seed={seed}&n=60")
        if r.status_code != 200:
            break
        added = 0
        for card in r.json()["cards"]:
            if card["prop_id"] not in seen:
                seen[card["prop_id"]] = card
                added += 1
        quiet = quiet + 1 if added == 0 else 0
        if quiet >= 4:
            break
    return list(seen.values())


def bake(
    client: TestClient,
    n_dates: int = 3,
    players_per_team: int = 4,
    dates: list[str] | None = None,
) -> Json:
    store = get_store()
    b = Baker(client)
    meta = client.get("/api/meta").json()
    all_dates = [s["date"] for s in client.get("/api/slates").json()]
    want = dates if dates is not None else sorted(all_dates, reverse=True)[:n_dates]
    want = sorted(d for d in want if d in all_dates)
    slates_all = client.get("/api/slates").json()
    meta = dict(meta) | {"slates": sorted(want, reverse=True)}
    b.simple["/api/meta"] = {"s": 200, "b": meta}
    b.simple["/api/slates"] = {"s": 200, "b": [s for s in slates_all if s["date"] in want]}
    for path in ("/api/parlay-of-the-day", "/api/evidence"):
        b.put_simple(path)

    swipe_nights: list[str] = []
    n_breakdowns = 0
    for d in want:
        slate = b.put_simple(f"/api/slates/{d}")["b"]
        b.put_simple(f"/api/top-parlays/{d}")
        results = b.fetch(f"/api/slates/{d}/results")
        night = f"d:{d}"
        b.groups.setdefault(night, {})[f"/api/slates/{d}/results"] = results
        rows = results["b"]["rows"] if results["s"] == 200 else []

        # The deck: a replay of a played night, the clock pinned 30 minutes before the first tip.
        tips = sorted(g["tip_utc"] for g in slate["games"])
        pool: list[Json] = []
        clock = ""
        if rows and tips:
            first = datetime.fromisoformat(tips[0].replace("Z", "+00:00"))
            clock = (first - timedelta(minutes=30)).astimezone(UTC).isoformat(timespec="seconds")

            def pinned(_d: str, _c: str = clock) -> str:
                return _c

            store.slate_clock = pinned  # type: ignore[method-assign,assignment]
            pool = _card_pool(client, d)
            del store.slate_clock  # back to the class method (the real clock)
        if pool:
            swipe_nights.append(d)
            note = (
                "Blind cards: the line and the price only. Drawn at random from every posted line; "
                "players who do not play are void."
            )
            b.groups[night][f"/api/slates/{d}/swipe"] = {
                "s": 200,
                "b": {"date": d, "clock_utc": clock, "cards": pool, "note": note},
            }
        by_game: dict[str, list[Json]] = {}
        for card in pool:
            by_game.setdefault(card["game_id"], []).append(card)

        for g in slate["games"]:
            gid = g["game_id"]
            grp = f"g:{gid}"
            detail = b.put_group(grp, f"/api/games/{gid}")["b"]
            if detail.get("final"):
                b.put_group(grp, f"/api/games/{gid}/result")
            for side in ("away", "home"):
                top = [p for p in detail["players"] if p["side"] == side][:players_per_team]
                for pl in top:
                    pid = pl["player_id"]
                    for stat in STATS:
                        if stat in pl["props"]:
                            b.put_group(grp, f"/api/edges/{gid}/{pid}/{stat}")
                    first_stat = next(iter(pl["props"]), None)
                    if first_stat is None:
                        continue
                    tabs = b.fetch(f"/api/breakdown/{gid}/{pid}/{first_stat}")["b"]
                    for t in tabs.get("tabs", []):
                        for s in SIDES:
                            path = f"/api/breakdown/{gid}/{pid}/{t['stat']}?side={s}"
                            e = b.put_group(grp, path)
                            n_breakdowns += 1 if e["s"] == 200 else 0
            for card in by_game.get(gid, []):
                b.put_group(
                    grp, f"/api/card/{gid}/{card['player_id']}/{quote(card['stat'], safe='')}"
                )

    shards = {name: _shard(entries) for name, entries in sorted(b.groups.items())}
    return {
        "format": FORMAT,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_as_of": meta["data_as_of"],
        "export_date": meta.get("export_date"),
        "data_version": meta.get("data_version"),
        "dates": want,
        "swipe_nights": swipe_nights,
        "counts": {
            "games": sum(1 for g in b.groups if g.startswith("g:")),
            "breakdowns": n_breakdowns,
            "cards": sum(1 for g in b.groups.values() for p in g if p.startswith("/api/card/")),
        },
        "simple": b.simple,
        "shards": shards,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dates", type=int, default=3, help="how many of the newest slate dates")
    ap.add_argument("--players-per-team", type=int, default=4)
    args = ap.parse_args(argv)
    reset_store()
    with TestClient(create_app()) as client:
        baked = bake(client, n_dates=args.dates, players_per_team=args.players_per_team)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(baked, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    size = args.out.stat().st_size
    print(
        f"wrote {args.out} ({size / 1e6:.2f} MB): data as of {baked['data_as_of']}, "  # noqa: T201
        f"dates {baked['dates']}, {baked['counts']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
