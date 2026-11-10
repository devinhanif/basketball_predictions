# ruff: noqa: E501
"""Synthetic one-game combo payload and the golden vectors built from it.

``python tests/parlay/combo_fixture.py --write`` regenerates ``golden/combo_golden_v1.json``
from ``nba/parlay/combo.py``. The TypeScript port is tested against that file: exact paths to 2e-6
absolute, QMC paths within 4 reported standard errors plus 1e-4 (``docs/contracts/combo_export_v1.md``).
All numbers here are synthetic (Poisson-shaped grids, round correlations), never a forecast.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden" / "combo_golden_v1.json"
HOME, AWAY = 1610612747, 1610612743


def _poisson_surv(mu: float, k_max: int) -> list[float]:
    """S(k) = P(Y >= k) for k = 1..k_max of a Poisson(mu), rounded to 4 decimals."""
    pmf = [math.exp(-mu) * mu**i / math.factorial(i) for i in range(k_max + 1)]
    out, cum = [], 0.0
    for k in range(1, k_max + 1):
        cum += pmf[k - 1]
        out.append(round(1.0 - cum, 4))
    return out


def _entry(mu: float, main_line: float | None, k_max: int = 60) -> dict[str, Any]:
    full = _poisson_surv(mu, k_max)
    first = next((i for i, v in enumerate(full) if v < 1.0), len(full))
    last = max((i for i, v in enumerate(full) if v > 0.0), default=-1)
    return {"k0": first + 1, "surv": full[first : last + 1], "main_line": main_line}


def _player(
    pid: int,
    name: str,
    team: int,
    p_play: float,
    starter: int,
    mus: dict[str, tuple[float, float | None]],
) -> dict[str, Any]:
    return {
        "player_id": pid,
        "name": name,
        "team": team,
        "p_play": p_play,
        "starter": starter,
        "stats": {s: _entry(mu, ml) for s, (mu, ml) in mus.items()},
    }


def game() -> dict[str, Any]:
    return {
        "game_id": "G1",
        "date": "2026-11-10",
        "home_team": HOME,
        "away_team": AWAY,
        "tip_utc": "2026-11-11T00:30:00Z",
        "made_at": "2026-11-10T21:00:00Z",
        "game": {"mu_m": 3.0, "sd_m": 13.6, "mu_t": 228.0, "sd_t": 18.8},
        "players": [
            _player(
                1,
                "Home Star",
                HOME,
                0.98,
                1,
                {"pts": (25.0, 24.5), "reb": (7.0, 6.5), "ast": (6.0, 5.5), "fg3m": (3.0, 2.5)},
            ),
            _player(
                2,
                "Home Bench",
                HOME,
                0.90,
                0,
                {"pts": (9.0, 8.5), "reb": (3.0, 2.5), "ast": (2.0, 1.5), "fg3m": (1.2, 0.5)},
            ),
            _player(
                3,
                "Away Star",
                AWAY,
                0.97,
                1,
                {"pts": (22.0, 21.5), "reb": (9.0, 8.5), "ast": (4.0, 3.5), "fg3m": (2.0, 1.5)},
            ),
            _player(
                4,
                "Away Bench",
                AWAY,
                0.85,
                0,
                {"pts": (8.0, 7.5), "reb": (2.5, 2.5), "ast": (3.0, 2.5), "fg3m": (1.0, 0.5)},
            ),
        ],
    }


def shared() -> dict[str, Any]:
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
    return {
        "corr": corr,
        "scripts": {
            "definitions": {"blowout_abs_margin": 15, "close_abs_margin": 5, "total_delta": 12.0},
            "params": params,
            "status": {s: "exploratory" for s in ("blowout", "close", "shootout", "grind")},
        },
    }


def _leg(pid: int, stat: str, line: float, side: str = "over") -> dict[str, Any]:
    return {
        "kind": "player",
        "game_id": "G1",
        "player_id": pid,
        "stat": stat,
        "line": line,
        "side": side,
    }


def cases() -> list[dict[str, Any]]:
    return [
        {"name": "single_leg", "legs": [_leg(1, "pts", 24.5)]},
        {"name": "same_player_pts_reb", "legs": [_leg(1, "pts", 24.5), _leg(1, "reb", 6.5)]},
        {"name": "same_player_pts_fg3m", "legs": [_leg(1, "pts", 24.5), _leg(1, "fg3m", 2.5)]},
        {
            "name": "same_player_over_under_mixed",
            "legs": [_leg(1, "pts", 24.5), _leg(1, "reb", 6.5, "under")],
        },
        {
            "name": "same_entity_interval",
            "legs": [_leg(1, "pts", 20.5), _leg(1, "pts", 29.5, "under")],
        },
        {
            "name": "same_entity_empty",
            "legs": [_leg(1, "pts", 29.5), _leg(1, "pts", 20.5, "under")],
        },
        {
            "name": "same_player_three_stats",
            "legs": [_leg(1, "pts", 24.5), _leg(1, "reb", 6.5), _leg(1, "ast", 5.5)],
        },
        {"name": "teammates", "legs": [_leg(1, "pts", 24.5), _leg(2, "pts", 8.5)]},
        {"name": "opponents", "legs": [_leg(1, "pts", 24.5), _leg(3, "pts", 21.5)]},
        {
            "name": "four_legs_two_players",
            "legs": [
                _leg(1, "pts", 24.5),
                _leg(1, "ast", 5.5),
                _leg(3, "reb", 8.5),
                _leg(3, "pts", 21.5),
            ],
        },
        {
            "name": "home_win_and_star_pts",
            "legs": [
                {"kind": "team_margin", "game_id": "G1", "team": HOME, "gt": 0.0},
                _leg(1, "pts", 24.5),
            ],
        },
        {
            "name": "total_over_and_pts",
            "legs": [
                {"kind": "total", "game_id": "G1", "line": 227.5, "side": "over"},
                _leg(1, "pts", 24.5),
            ],
        },
        {
            "name": "script_blowout_pair",
            "legs": [_leg(1, "pts", 24.5), _leg(1, "reb", 6.5)],
            "script": "blowout",
        },
        {
            "name": "script_shootout_bench",
            "legs": [_leg(2, "pts", 8.5), _leg(4, "pts", 7.5)],
            "script": "shootout",
        },
    ]


def build() -> dict[str, Any]:
    sys.path.insert(0, str(HERE.parents[1]))
    from nba.parlay import combo

    g, sh = game(), shared()
    out_cases = []
    for c in cases():
        r = combo.evaluate_combo(c["legs"], {"G1": g}, sh, c.get("script"))
        out_cases.append(
            {
                **c,
                "expect": {
                    "p_joint": r.p_joint,
                    "p_independent": r.p_independent,
                    "lift": r.lift,
                    "marginals": list(r.marginals),
                    "se": r.se,
                    "method": r.method,
                    "p_all_play": r.p_all_play,
                    "p_joint_incl_dnp": r.p_joint_incl_dnp,
                },
            }
        )
    rank = combo.lift_ranking(g, sh, top_n=5)
    tgt = combo.target_search(g, sh, target=0.15, k_legs=2, top_n=3)
    return {
        "schema": combo.SCHEMA,
        "tolerance": {"exact": 2e-6, "qmc": "4*se + 1e-4"},
        "game": g,
        "shared": sh,
        "cases": out_cases,
        "script_priors": {s: combo.script_prior(s, g, sh) for s in combo.SCRIPTS},
        "script_definitions": combo.script_definitions(g, sh),
        "lift_ranking_top5": rank,
        "target_search": tgt,
    }


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n")
        print("wrote", GOLDEN)
