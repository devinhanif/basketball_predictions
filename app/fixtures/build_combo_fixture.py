# /// script
# requires-python = ">=3.12"
# ///
"""Writes app/fixtures/combo/combo_2024-11-12.json: a SYNTHETIC night for combo mode (contract
docs/contracts/combo_export_v1.md). Standard library only; reads nothing, imports nothing from nba.

The numbers are Poisson-shaped grids with round correlations, taken from the same recipe as the
reference fixture game (tests/parlay/combo_fixture.py), placed on three real 2024-11-12 fixture
games (ids, team ids, tips from app/fixtures/slates). They are not forecasts of any real player.
`uv run --script app/fixtures/build_combo_fixture.py` rewrites the file; the output is deterministic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "combo" / "combo_2024-11-12.json"
NIGHT = "2024-11-12"
MADE_AT = "2024-11-12T21:00:00Z"

# (game_id, home team id, away team id, tip_utc, mu_m, mu_t, per-game scale of the stat means)
GAMES = [
    ("0022400001", 1610612738, 1610612737, "2024-11-13T00:00:00Z", 6.5, 226.5, 1.00),
    ("0022400002", 1610612765, 1610612748, "2024-11-13T00:00:00Z", -2.0, 217.0, 0.92),
    ("0022400004", 1610612755, 1610612752, "2024-11-13T00:30:00Z", 1.5, 231.0, 1.06),
]
# (name suffix, p_play, starter, {stat: (mean, main line)})
ROSTER = [
    ("Home Star", 0.98, 1, {"pts": (25.0, 24.5), "reb": (7.0, 6.5), "ast": (6.0, 5.5), "fg3m": (3.0, 2.5)}),
    ("Home Bench", 0.90, 0, {"pts": (9.0, 8.5), "reb": (3.0, 2.5), "ast": (2.0, 1.5), "fg3m": (1.2, 0.5)}),
    ("Away Star", 0.97, 1, {"pts": (22.0, 21.5), "reb": (9.0, 8.5), "ast": (4.0, 3.5), "fg3m": (2.0, 1.5)}),
    ("Away Bench", None, 0, {"pts": (8.0, 7.5), "reb": (2.5, 2.5), "ast": (3.0, 2.5), "fg3m": (1.0, 0.5)}),
]
# Posted main lines that opened a half point away (fixture values, to exercise the optional key).
OPENED = {("0022400001", "Home Star", "pts"): 23.5, ("0022400002", "Away Star", "reb"): 9.5}


def poisson_surv(mu: float, k_max: int = 60) -> list[float]:
    pmf = [math.exp(-mu) * mu**i / math.factorial(i) for i in range(k_max + 1)]
    out, cum = [], 0.0
    for k in range(1, k_max + 1):
        cum += pmf[k - 1]
        out.append(round(1.0 - cum, 4))
    return out


def entry(mu: float, main: float) -> dict[str, Any]:
    full = poisson_surv(mu)
    first = next((i for i, v in enumerate(full) if v < 1.0), len(full))
    last = max((i for i, v in enumerate(full) if v > 0.0), default=-1)
    k0 = first + 1
    surv = full[first : last + 1]
    sd = math.sqrt(mu)
    lo, hi = max(1, math.floor(mu - sd)), math.ceil(mu + sd) + 1
    at_main = surv[int(main + 0.5) - k0] if 0 <= int(main + 0.5) - k0 < len(surv) else 0.0
    return {
        "k0": k0,
        "surv": surv,
        "main_line": main,
        "raw_at_main": round(min(0.97, at_main + 0.03), 4),
        "cal_k": [lo, hi],
    }


def build() -> dict[str, Any]:
    corr = [
        ["game", "ast", "total", 0.0862, 55202], ["game", "fg3m", "total", 0.0931, 55202],
        ["game", "margin", "total", -0.0161, 2633], ["game", "pts", "total", 0.1187, 55202],
        ["game", "reb", "total", -0.0348, 55202],
        ["opponent", "pts", "pts", 0.0105, 292165], ["opponent", "reb", "reb", 0.0071, 292165],
        ["own_margin", "ast", "margin", 0.0829, 55202], ["own_margin", "fg3m", "margin", 0.0813, 55202],
        ["own_margin", "margin", "pts", 0.0881, 55202], ["own_margin", "margin", "reb", 0.0719, 55202],
        ["same_player", "ast", "fg3m", 0.0998, 55202], ["same_player", "ast", "pts", 0.1829, 55202],
        ["same_player", "ast", "reb", 0.2049, 55202], ["same_player", "fg3m", "pts", 0.5399, 55202],
        ["same_player", "fg3m", "reb", 0.1230, 55202], ["same_player", "pts", "reb", 0.2957, 55202],
        ["teammate", "ast", "pts", 0.0304, 537220], ["teammate", "pts", "pts", -0.0166, 268610],
    ]  # fmt: skip
    params = []
    for sc, mu_s, mu_b, sg in (
        ("blowout", -0.20, 0.15, 1.02),
        ("close", 0.05, -0.04, 0.98),
        ("shootout", 0.12, 0.05, 1.03),
        ("grind", -0.10, -0.05, 0.97),
    ):
        for stat in ("pts", "reb", "ast", "fg3m"):
            params.append([sc, stat, "starter", mu_s, sg, 1000])
            params.append([sc, stat, "bench", mu_b, sg, 1000])
    shared = {
        "corr": corr,
        "scripts": {
            "definitions": {"blowout_abs_margin": 15, "close_abs_margin": 5, "total_delta": 12.0},
            "params": params,
            "status": {
                s: {"status": "exploratory", "why_not": ["review override 2026-11-10"]}
                for s in ("blowout", "close", "shootout", "grind")
            },
        },
        "provenance": {
            "map": "LINE_CALIBRATION", "map_sha8": "00000000", "fit_through": "2024-06-30",
            "corr_fit_seasons": [2023, 2024], "corr_n_games": 2638,
            "combo_params_sha8": "00000000", "rule_sha8": "00000000", "code_version": "fixture",
        },
    }  # fmt: skip
    games = []
    for gi, (gid, home, away, tip, mu_m, mu_t, scale) in enumerate(GAMES, start=1):
        players = []
        for pi, (name, p_play, starter, stats) in enumerate(ROSTER, start=1):
            team = home if pi <= 2 else away
            ents: dict[str, Any] = {}
            for stat, (mu, main) in stats.items():
                m = max(0.6, round(mu * scale * 2) / 2)
                main_l = max(0.5, round((main * scale) - 0.5) + 0.5)
                e = entry(m, main_l)
                o = OPENED.get((gid, name, stat))
                if o is not None:
                    e["main_line_open"] = o
                ents[stat] = e
            players.append({
                "player_id": gi * 100 + pi, "name": f"Fixture {name} {gi}", "team": team,
                "p_play": p_play, "starter": starter, "stats": ents,
            })  # fmt: skip
        games.append({
            "game_id": gid, "date": NIGHT, "home_team": home, "away_team": away, "tip_utc": tip,
            "made_at": MADE_AT,
            "game": {"mu_m": mu_m, "sd_m": 13.63, "mu_t": mu_t, "sd_t": 18.82},
            "players": players,
        })  # fmt: skip
    return {
        "schema": "combo_export_v1",
        "night": NIGHT,
        "fixture": {"label": "Fixture data: synthetic numbers on real 2024-11-12 games, not forecasts of anyone."},
        "shared": shared,
        "games": games,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), separators=(",", ":"), sort_keys=True) + "\n")
    print("wrote", OUT, OUT.stat().st_size, "bytes")  # noqa: T201
