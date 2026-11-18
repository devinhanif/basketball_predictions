"""Writes live_card/stored_game.json: ONE synthetic stored game for the live card's tests.

Synthetic on purpose: the names are the ones in the committed live capture fixture (live/live_2026-11-09,
LAL at ATL, event 401909952) so the card can join them; every line, chance and price below is made up
for the test and means nothing. Player ids are fake (9000001 up) so no real photo is implied. No
season-2025 data and nothing from the nightly export is read.

Run from the repo root:  uv run --script app/fixtures/build_live_card_fixture.py
"""
# /// script
# requires-python = ">=3.12"
# ///

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "live_card" / "stored_game.json"

# (name, side, stat, line, over, under, calibrated p_over or None)
ROWS = [
    ("Luka Doncic", "away", "pts", 30.5, -115, -105, 0.54),
    ("Luka Doncic", "away", "reb", 8.5, -110, -110, None),
    ("Luka Doncic", "away", "ast", 8.5, -120, 100, 0.47),
    ("Luka Doncic", "away", "fg3m", 2.5, 105, -125, None),
    ("Walker Kessler", "away", "reb", 14.5, -110, -110, 0.41),
    ("Jarred Vanderbilt", "away", "pts", 3.5, -105, -115, None),
    ("Collin Sexton", "away", "pts", 15.5, -110, -110, 0.5),
    ("Jaden Hardy", "away", "pts", 9.5, -110, -110, None),
    ("Jalen Johnson", "home", "pts", 21.5, -110, -110, 0.52),
    ("Jalen Johnson", "home", "reb", 5.5, -115, -105, None),
    ("Onyeka Okongwu", "home", "reb", 11.5, -110, -110, 0.44),
    ("Nickeil Alexander-Walker", "home", "pts", 17.5, -110, -110, None),
    ("Dyson Daniels", "home", "ast", 5.5, 110, -130, None),
    ("Corey Kispert", "home", "pts", 8.5, -110, -110, None),
    ("Synthetic Nobody", "home", "pts", 12.5, -110, -110, None),
]
REFERENCE_ONLY = [("Cameron Carr", "away", "pts", 5.5), ("Zuby Ejiofor", "home", "reb", 3.5)]


def forecast(line: float) -> dict[str, object]:
    q = [round(line - 8 + i * (16 / 18), 2) for i in range(19)]
    return {"mean": line, "q": q, "stamp": "synthetic fixture for the live card tests", "model": None}


def prop(pid: int, stat: str, line: float, over: int | None, under: int | None, cal: float | None, ref: bool = False) -> dict[str, object]:
    p: dict[str, object] = {
        "prop_id": f"SYN:{pid}:{stat}",
        "line": line,
        "line_source": "reference" if ref else "posted",
        "over_price": None if ref else over,
        "under_price": None if ref else under,
        "price_book": None if ref else "draftkings",
        "line_snapshot_utc": None if ref else "2026-11-09T22:00:00+00:00",
        "forecast": forecast(line),
        "actual": None,
    }
    if cal is not None:
        p["line_cal"] = {
            "book": "draftkings",
            "main_line": line,
            "price_at": None,
            "p_over_raw": min(0.99, cal + 0.04),
            "p_over_cal": cal,
            "map": "SYNTHETIC",
            "map_sha8": "00000000",
            "fit_through": None,
        }
    return p


def main() -> None:
    names: dict[str, int] = {}
    players: dict[str, dict[str, object]] = {}
    for name, side, stat, line, over, under, cal in ROWS:
        pid = names.setdefault(name, 9000001 + len(names))
        pl = players.setdefault(name, {"player_id": pid, "name": name, "side": side, "result": None, "props": {}})
        pl["props"][stat] = prop(pid, stat, line, over, under, cal)  # type: ignore[index]
    for name, side, stat, line in REFERENCE_ONLY:
        pid = names.setdefault(name, 9000001 + len(names))
        pl = players.setdefault(name, {"player_id": pid, "name": name, "side": side, "result": None, "props": {}})
        pl["props"][stat] = prop(pid, stat, line, None, None, None, ref=True)  # type: ignore[index]
    game = {
        "game_id": "SYN-LAL-ATL",
        "date": "2026-11-09",
        "season": 2024,
        "tip_utc": "2026-11-10T00:00:00+00:00",
        "arena": None,
        "home": {"id": 1, "abbr": "ATL", "city": "Atlanta", "name": "Hawks"},
        "away": {"id": 2, "abbr": "LAL", "city": "Los Angeles", "name": "Lakers"},
        "win_probability": {},
        "result": None,
        "final": False,
        "SYNTHETIC": "made up for tests; no line, chance or price here is real",
        "players": list(players.values()),
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(game, indent=1) + "\n")


if __name__ == "__main__":
    main()
