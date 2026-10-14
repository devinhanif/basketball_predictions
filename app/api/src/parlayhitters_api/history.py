"""Box-score history for the leg breakdown, indexed once at load, read-only.

Everything here is a raw fact from the fixture (`breakdown.json`): which games exist, who played
in them and what they produced. The as-of rule lives in `Breakdown` (strictly before the slate
date); this module only indexes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as Date
from typing import Any

Json = dict[str, Any]

# stat key -> the box-score columns it adds up. A combo is the sum of its parts for one game of
# one player, which is exactly how the books settle PRA, P+R, P+A, R+A and blocks+steals.
STAT_PARTS: dict[str, tuple[str, ...]] = {
    "pts": ("pts",),
    "reb": ("reb",),
    "ast": ("ast",),
    "fg3m": ("fg3m",),
    "blk": ("blk",),
    "stl": ("stl",),
    "pra": ("pts", "reb", "ast"),
    "pr": ("pts", "reb"),
    "pa": ("pts", "ast"),
    "ra": ("reb", "ast"),
    "stocks": ("blk", "stl"),
}
STAT_LABEL: dict[str, str] = {
    "pts": "Points",
    "reb": "Rebounds",
    "ast": "Assists",
    "fg3m": "3-pointers",
    "blk": "Blocks",
    "stl": "Steals",
    "pra": "Pts+Reb+Ast",
    "pr": "Pts+Reb",
    "pa": "Pts+Ast",
    "ra": "Reb+Ast",
    "stocks": "Blocks+Steals",
}
STAT_ORDER: tuple[str, ...] = tuple(STAT_PARTS)
# The production engine forecasts these four and nothing else (docs/PROJECT_STATUS.md).
MODEL_STATS: frozenset[str] = frozenset({"pts", "reb", "ast", "fg3m"})
BASE_COLUMNS = ("pts", "reb", "ast", "fg3m", "stl", "blk")

# Box columns in breakdown.json rows (see "box_columns" there).
_C_PLAYER, _C_TEAM, _C_MIN, _C_PTS, _C_REB, _C_AST, _C_FG3M, _C_STL, _C_BLK, _C_START = range(10)

POSITION_GROUP = {"Guard": "G", "Forward": "F", "Center": "C"}
POSITION_NOUN = {"G": "guards", "F": "forwards", "C": "centers"}


@dataclass(frozen=True)
class Row:
    """One game a player actually played (minutes > 0)."""

    game_id: str
    date: str
    season: int
    team: int
    opp: int
    home: bool
    minutes: float
    starter: bool
    base: tuple[int, int, int, int, int, int]  # pts, reb, ast, fg3m, stl, blk

    def value(self, stat: str) -> int:
        idx = {"pts": 0, "reb": 1, "ast": 2, "fg3m": 3, "stl": 4, "blk": 5}
        return sum(self.base[idx[c]] for c in STAT_PARTS[stat])


def position_group(pos: str | None) -> str | None:
    """Primary position ("Guard-Forward" -> G). None if unknown."""
    if not pos:
        return None
    return POSITION_GROUP.get(pos.split("-")[0])


def days_between(a: str, b: str) -> int:
    return (Date.fromisoformat(b) - Date.fromisoformat(a)).days


class History:
    def __init__(self, data: Json) -> None:
        self.window: Json = data["window"]
        self.abbr: dict[int, str] = {int(t): a for t, a in data["teams"].items()}
        self.players: dict[int, Json] = {int(p): v for p, v in data["players"].items()}
        self.availability: dict[str, list[list[Any]]] = data["availability"]
        self.prices: dict[str, dict[str, dict[str, list[Json]]]] = data["prices"]
        # game_id -> (date, season, home_team, away_team)
        self.games: dict[str, tuple[str, int, int, int]] = {
            gid: (v[0], int(v[1]), int(v[2]), int(v[3])) for gid, v in data["games"].items()
        }
        self.logs: dict[int, list[Row]] = defaultdict(list)
        # game_id -> team_id -> {player_id: Row}
        self.played: dict[str, dict[int, dict[int, Row]]] = {}
        for gid, rows in data["box"].items():
            d, season, home, away = self.games[gid]
            per_team: dict[int, dict[int, Row]] = defaultdict(dict)
            for r in rows:
                team = int(r[_C_TEAM])
                row = Row(
                    game_id=gid,
                    date=d,
                    season=season,
                    team=team,
                    opp=away if team == home else home,
                    home=team == home,
                    minutes=float(r[_C_MIN]),
                    starter=bool(r[_C_START]),
                    base=(
                        int(r[_C_PTS]),
                        int(r[_C_REB]),
                        int(r[_C_AST]),
                        int(r[_C_FG3M]),
                        int(r[_C_STL]),
                        int(r[_C_BLK]),
                    ),
                )
                per_team[team][int(r[_C_PLAYER])] = row
                self.logs[int(r[_C_PLAYER])].append(row)
            self.played[gid] = dict(per_team)
        for rows_ in self.logs.values():
            rows_.sort(key=lambda x: (x.date, x.game_id))
        # team -> chronological (date, game_id) of every game in the window
        self.team_games: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for gid, (d, _, home, away) in self.games.items():
            self.team_games[home].append((d, gid))
            self.team_games[away].append((d, gid))
        for lst in self.team_games.values():
            lst.sort()
        self.team_index: dict[int, dict[str, int]] = {
            t: {gid: i for i, (_, gid) in enumerate(lst)} for t, lst in self.team_games.items()
        }

    def name(self, pid: int) -> str | None:
        p = self.players.get(pid)
        return None if p is None else p.get("name")

    def pos(self, pid: int) -> str | None:
        p = self.players.get(pid)
        return None if p is None else position_group(p.get("pos"))

    def team_abbr(self, tid: int) -> str:
        return self.abbr.get(tid, str(tid))
