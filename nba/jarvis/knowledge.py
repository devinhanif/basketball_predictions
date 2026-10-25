"""Basketball knowledge for JARVIS: injuries, lineups, fan knowledge, 2026 usage / pace / units.

Everything returned is plain JSON-able data. Text that came from outside (injury reasons, lineup
labels) is DATA: it is sanitised (printable subset, short) before it can reach a prompt, and it
never feeds a number, a label, a stake or a send decision.

``nba.duckdb`` is opened read-only; the 2026 filters (``games.season = 2026``) are in the SQL, so
season 2025 (the frozen holdout) and earlier are never loaded here.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

from nba.jarvis.config import JarvisConfig
from nba.jarvis.sources import open_ro, team_tables, utcnow

_SAFE = re.compile(r"[^A-Za-z0-9 /,.()'\-]")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "who",
    "how",
    "his",
    "her",
    "are",
    "was",
    "will",
    "that",
    "this",
    "tonight",
    "game",
    "about",
    "tell",
    "should",
    "bet",
    "play",
    "best",
}


def sanitize(text: object, limit: int = 80) -> str:
    """Fetched text -> short printable data. Strips URLs, control characters and markup."""
    s = re.sub(r"https?://\S+", "", str(text))
    s = _SAFE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


class Knowledge:
    def __init__(self, cfg: JarvisConfig, display: Any = None) -> None:
        self.cfg = cfg
        self._display = display  # callable player_id -> name, or None
        self.abbr, self.abbr_of, self.names = team_tables()

    # ------------------------------------------------------------------ names
    def name(self, pid: int) -> str:
        if self._display is not None:
            return str(self._display(int(pid)))
        return f"player {pid}"

    def _team_id(self, team: str | int | None) -> int | None:
        if team is None:
            return None
        if isinstance(team, int) or str(team).isdigit():
            return int(team)
        return self.abbr.get(str(team).upper())

    def top_usage(self, team_id: int, n: int, min_possessions: int) -> list[dict[str, Any]]:
        """The team's highest-usage players in 2026: the share of its offensive possessions with the
        player on court that end with his shot or foul trip (not the official USG%). Players with
        fewer than ``min_possessions`` on-court possessions are not ranked."""
        path = self.cfg.paths["nba_db"]
        if not path.exists():
            return []
        con = open_ro(path)
        try:
            rows = con.execute(
                "SELECT pid, count(*) AS on_court, "
                "sum(CASE WHEN shooter_id = pid THEN 1 ELSE 0 END) AS used "
                "FROM (SELECT p.shooter_id, unnest(p.off_players) AS pid FROM possessions p "
                "JOIN games g USING (game_id) WHERE g.season = 2026 AND p.off_team = ?) "
                "GROUP BY pid HAVING count(*) >= ? ORDER BY used * 1.0 / count(*) DESC LIMIT ?",
                [team_id, min_possessions, n],
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "player_id": int(r[0]),
                "on_court": int(r[1]),
                "used": int(r[2]),
                "share": int(r[2]) / int(r[1]),
            }
            for r in rows
        ]

    def player_teams(self, pids: set[int]) -> dict[int, int]:
        """player_id -> team_id from the player's latest 2026 box score (2026 only)."""
        path = self.cfg.paths["nba_db"]
        if not pids or not path.exists():
            return {}
        con = open_ro(path)
        try:
            ph = ",".join("?" for _ in pids)
            rows = con.execute(
                "SELECT s.player_id, arg_max(s.team_id, g.game_date) FROM player_game_stats s "
                f"JOIN games g USING (game_id) WHERE g.season = 2026 AND s.player_id IN ({ph}) "
                "GROUP BY s.player_id",
                sorted(pids),
            ).fetchall()
        finally:
            con.close()
        return {int(r[0]): int(r[1]) for r in rows}

    # ------------------------------------------------------------------ injuries
    def injuries(self, game_id: str) -> dict[str, Any]:
        """Latest official-report status per player for a game, grouped by team. Includes the
        report's own timestamp; the report can be hours old."""
        path = self.cfg.paths["nba_db"]
        if not path.exists():
            return {"error": "nba.duckdb not found"}
        con = open_ro(path)
        try:
            rows = con.execute(
                "SELECT a.player_id, a.status, a.reason, a.as_of, "
                "(SELECT s.team_id FROM player_game_stats s JOIN games g USING (game_id) "
                " WHERE s.player_id = a.player_id AND g.season = 2026 "
                " ORDER BY g.game_date DESC LIMIT 1), "
                "(SELECT avg(s.minutes) FROM player_game_stats s JOIN games g USING (game_id) "
                " WHERE s.player_id = a.player_id AND g.season = 2026 AND s.minutes > 0) "
                "FROM player_availability a JOIN (SELECT player_id, max(as_of) m FROM "
                "player_availability WHERE game_id = ? GROUP BY player_id) l "
                "ON a.player_id = l.player_id AND a.as_of = l.m WHERE a.game_id = ?",
                [game_id, game_id],
            ).fetchall()
        finally:
            con.close()
        team_of_lineup = self._lineup_teams(game_id)
        teams: dict[str, list[dict[str, Any]]] = {}
        newest: datetime | None = None
        for pid, status, reason, as_of, tid, mpg in rows:
            if status not in ("out", "doubtful", "questionable", "probable"):
                continue
            ab = self.abbr_of.get(int(tid)) if tid is not None else team_of_lineup.get(int(pid))
            newest = as_of if newest is None or as_of > newest else newest
            teams.setdefault(ab or "?", []).append(
                {
                    "player": self.name(int(pid)),
                    "status": str(status),
                    "reason": sanitize(reason, 48),
                    "mpg": None if mpg is None else round(float(mpg), 1),
                }
            )
        order = {"out": 0, "doubtful": 1, "questionable": 2, "probable": 3}
        for lst in teams.values():
            lst.sort(key=lambda r: order.get(r["status"], 9))
        return {
            "game_id": game_id,
            "report_as_of_utc": None if newest is None else str(newest),
            "report_age_min": None
            if newest is None
            else round((utcnow() - newest).total_seconds() / 60.0),
            "teams": teams,
        }

    def _lineup_teams(self, game_id: str) -> dict[int, str]:
        lp = self.cfg.paths["lineups_db"]
        if not lp.exists():
            return {}
        con = open_ro(lp, copy_ok=True)
        try:
            return {
                int(p): str(t)
                for p, t in con.execute(
                    "SELECT player_id, team_abbr FROM lineup_snapshots_latest WHERE game_id = ?",
                    [game_id],
                ).fetchall()
            }
        except duckdb.Error:
            return {}
        finally:
            con.close()

    # ------------------------------------------------------------------ lineups
    def lineups(self, game_id: str) -> dict[str, Any]:
        lp = self.cfg.paths["lineups_db"]
        if not lp.exists():
            return {"error": "lineups store not found"}
        con = open_ro(lp, copy_ok=True)
        try:
            rows = con.execute(
                "SELECT team_abbr, player_name, lineup_status, announced_starter, position, "
                "fetched_at FROM lineup_snapshots_latest WHERE game_id = ? ORDER BY team_abbr",
                [game_id],
            ).fetchall()
        except duckdb.Error as exc:
            return {"error": f"lineups unavailable ({type(exc).__name__})"}
        finally:
            con.close()
        if not rows:
            return {
                "game_id": game_id,
                "starters": {},
                "note": "no lineup snapshot yet (the collector runs about 30 minutes before tip)",
            }
        starters: dict[str, list[dict[str, str]]] = {}
        newest = max(r[5] for r in rows)
        for team, pname, status, ann, pos, _ts in rows:
            if ann:
                starters.setdefault(str(team), []).append(
                    {
                        "player": sanitize(pname, 40),
                        "pos": sanitize(pos, 4),
                        "status": sanitize(status, 12),
                    }
                )
        return {
            "game_id": game_id,
            "starters": starters,
            "snapshot_utc": str(newest),
            "snapshot_age_min": round((utcnow() - newest).total_seconds() / 60.0),
        }

    # ------------------------------------------------------------------ fan knowledge
    @lru_cache(maxsize=1)  # noqa: B019 - one instance per process
    def _sections(self) -> tuple[tuple[str, str], ...]:
        p = Path(self.cfg.paths["fan_knowledge"])
        if not p.exists():
            return ()
        out: list[tuple[str, str]] = []
        head = ""
        buf: list[str] = []
        for line in p.read_text().splitlines():
            if line.startswith("## ") or line.startswith("### "):
                if head and buf:
                    out.append((head, "\n".join(buf)))
                head, buf = line.lstrip("# ").strip(), []
            else:
                buf.append(line)
        if head and buf:
            out.append((head, "\n".join(buf)))
        return tuple(out)

    def fan_knowledge(self, query: str, k: int = 3, chars: int = 520) -> dict[str, Any]:
        """Excerpts of docs/FAN_KNOWLEDGE.md ranked by keyword overlap with a player, team or topic.
        These are Devin's hypotheses, not tested facts; the excerpt says so by being quoted."""
        toks = {
            t for t in re.findall(r"[a-z0-9']+", query.lower()) if len(t) >= 3 and t not in _STOP
        }
        scored = []
        for head, body in self._sections():
            hl, bl = head.lower(), body.lower()
            s = sum((3 if t in hl else 0) + min(bl.count(t), 4) for t in toks)
            if s > 0:
                scored.append((s, head, body))
        scored.sort(key=lambda r: -r[0])
        return {
            "query": query,
            "note": "hypotheses from Devin's watching, not tested facts",
            "excerpts": [
                {"section": h, "text": re.sub(r"\s+", " ", b).strip()[:chars]}
                for _s, h, b in scored[:k]
            ],
        }

    # ------------------------------------------------------------------ usage / pace / units
    def usage_pace(
        self, team: str | int | None = None, player_id: int | None = None
    ) -> dict[str, Any]:
        """2026 stints and possessions: team pace, top five-man units, a player's usage."""
        path = self.cfg.paths["nba_db"]
        if not path.exists():
            return {"error": "nba.duckdb not found"}
        con = open_ro(path)
        out: dict[str, Any] = {"season": 2026, "source": "stints and possessions, season 2026 only"}
        try:
            tid = self._team_id(team)
            league = con.execute(
                "SELECT count(*), count(DISTINCT p.game_id) FROM possessions p "
                "JOIN games g USING (game_id) "
                "WHERE g.season = 2026"
            ).fetchone()
            n_games_l = int(league[1]) if league else 0
            out["league_possessions_per_team_game"] = (
                round(int(league[0]) / (2 * n_games_l), 1) if league and n_games_l else None
            )
            out["league_games"] = n_games_l
            if tid is not None:
                tp = con.execute(
                    "SELECT count(*), count(DISTINCT p.game_id) FROM possessions p "
                    "JOIN games g USING (game_id) "
                    "WHERE g.season = 2026 AND p.off_team = ?",
                    [tid],
                ).fetchone()
                out["team"] = self.abbr_of.get(tid, str(tid))
                out["team_games"] = int(tp[1]) if tp else 0
                out["team_possessions_per_game"] = (
                    round(int(tp[0]) / int(tp[1]), 1) if tp and int(tp[1]) else None
                )
                units = con.execute(
                    "SELECT list_sort(s.players) u, sum(s.start_clock - s.end_clock) / 60.0 mins, "
                    "count(DISTINCT s.game_id) FROM stints s JOIN games g USING (game_id) "
                    "WHERE g.season = 2026 AND s.team_id = ? GROUP BY u ORDER BY mins DESC LIMIT 3",
                    [tid],
                ).fetchall()
                out["top_units"] = [
                    {
                        "players": [self.name(int(x)) for x in u],
                        "minutes": round(float(m), 1),
                        "games": int(ng),
                    }
                    for u, m, ng in units
                ]
            if player_id is not None:
                pid = int(player_id)
                r = con.execute(
                    "SELECT count(*) FILTER (WHERE p.shooter_id = ?), "
                    "count(*) FILTER (WHERE list_contains(p.off_players, ?)) "
                    "FROM possessions p JOIN games g USING (game_id) WHERE g.season = 2026",
                    [pid, pid],
                ).fetchone()
                m = con.execute(
                    "SELECT sum(s.start_clock - s.end_clock) / 60.0, count(DISTINCT s.game_id) "
                    "FROM stints s JOIN games g USING (game_id) "
                    "WHERE g.season = 2026 AND list_contains(s.players, ?)",
                    [pid],
                ).fetchone()
                used, on = (int(r[0]), int(r[1])) if r else (0, 0)
                out["player"] = self.name(pid)
                out["player_possessions_used"] = used
                out["player_offensive_possessions_on_court"] = on
                out["player_usage_share"] = round(used / on, 3) if on else None
                out["player_minutes_per_game"] = (
                    round(float(m[0]) / int(m[1]), 1) if m and m[1] else None
                )
                out["player_games"] = int(m[1]) if m and m[1] else 0
                out["usage_note"] = (
                    "share of own team's possessions on court that ended with this player shooting "
                    "or drawing the foul trip; not the official USG%"
                )
        finally:
            con.close()
        return out
