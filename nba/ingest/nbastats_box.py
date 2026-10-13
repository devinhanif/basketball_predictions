"""Player box lines and Q1 starters derived from stats.nba.com-format play-by-play.

For the 2015-16 .. 2018-19 history there is no box-score fetch (DECISIONS 2026-10-12 17:41); points,
rebounds, assists, three-pointers, free throws, turnovers, steals, blocks and fouls are counted
from the
event rows, whose ``PLAYER1/2/3`` ids are exact. Minutes come from the lineup tracker's stints
(``nba.parse.lineups``), not from the clock arithmetic here.

What this cannot know (documented in docs/DATA_CARDS.md): a player who never appears in any event or
substitution has no row, so DNP/inactive rows are absent for these seasons
(``game_era_flags.box_derived``
marks them). Reconciliation against an independent stats.nba.com box sample is in
docs/reviews/history_2015_2019_ingest_2026-10-12.md.

Stat attribution (stats.nba.com semantics):

* made/missed field goal -> ``PLAYER1`` shooter, 3-pointer iff the text says ``3PT``; made FG
  ``PLAYER2``
  is the assist; missed FG ``PLAYER3`` is the block;
* free throw -> ``PLAYER1``, missed iff the text starts with ``MISS``;
* rebound -> ``PLAYER1`` when it carries a team id (a team rebound has none); offensive vs
  defensive from the
  per-player ``(Off:a Def:b)`` counters in the text;
* turnover -> ``PLAYER1`` (team turnovers have no team id and are skipped), ``PLAYER2`` the steal;
* personal fouls -> ``PLAYER1`` of a foul event (``PLAYER2`` too on a double personal), technical
  and delay
  fouls excluded.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from nba.ingest.boxscores import _STATS_SCHEMA

_REB_RE = re.compile(r"\(Off:(\d+) Def:(\d+)\)")
_TECH_TEXT = re.compile(r"(?<![A-Za-z])T\.FOUL")
_TECH_SUBTYPES = ("Technical", "Defense 3 Second")
_MADE_PTS_RE = re.compile(r"\(\d+ PTS\)")
# events whose actor shows the player is on the floor (technical/delay excluded, as in the tracker)
_EVIDENCE = {1, 2, 3, 4, 5, 6, 7, 10}


def _i(v: Any) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def _desc(r: dict[str, Any]) -> str:
    return r["HOMEDESCRIPTION"] or r["VISITORDESCRIPTION"] or r["NEUTRALDESCRIPTION"] or ""


def ft_made(desc: str) -> bool:
    """True if a free-throw event's text records a make.

    A make carries the shooter's running total ("Hayes Free Throw 2 of 2 (17 PTS)"), a miss starts
    with "MISS ". In 2013-14 .. 2016-17 a few misses have neither (a bare "Free Throw 2 of 2", no
    name, no score change), which the old "not MISS" test counted as makes: pts and ftm one too
    high for the shooter (SEQ_LEARNING_CURVE_2 gate D3).
    """
    return not desc.startswith("MISS") and _MADE_PTS_RE.search(desc) is not None


def _clock_s(r: dict[str, Any]) -> int:
    m = re.match(r"^(\d+):(\d+)", r["PCTIMESTRING"] or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def derive_starters(
    rows: list[dict[str, Any]], subtype_of: dict[tuple[int, int], str]
) -> dict[int, list[int]] | None:
    """First five players of each team in period 1: acted, or were subbed out, before subbing in.

    Uses exact ids (``PLAYER1`` out, ``PLAYER2`` in). Returns None unless both teams yield five.
    """
    p1 = [r for r in rows if _i(r["PERIOD"]) == 1]
    p1.sort(key=lambda r: (-_clock_s(r), _i(r["EVENTMSGTYPE"]) != 8, _i(r["EVENTNUM"])))
    opened: dict[int, list[int]] = {}
    seen: dict[int, set[int]] = {}
    for r in p1:
        et = _i(r["EVENTMSGTYPE"])
        tid = _i(r["PLAYER1_TEAM_ID"])
        pid = _i(r["PLAYER1_ID"])
        if not tid or not pid:
            continue
        s = seen.setdefault(tid, set())
        o = opened.setdefault(tid, [])
        if et == 8:
            if pid not in s:
                s.add(pid)
                o.append(pid)
            s.add(_i(r["PLAYER2_ID"]))
        elif et in _EVIDENCE:
            sub = subtype_of.get((et, _i(r["EVENTMSGACTIONTYPE"])), "")
            if "Technical" in sub or sub == "Delay Of Game":
                continue
            if pid not in s:
                s.add(pid)
                o.append(pid)
    out = {t: v[:5] for t, v in opened.items() if len(v) >= 5}
    return out if len(out) == 2 else None


def derive_box(
    rows: list[dict[str, Any]],
    subtype_of: dict[tuple[int, int], str],
    minutes: dict[int, float],
    starters: dict[int, list[int]] | None,
) -> pl.DataFrame:
    """One row per player seen in the game's events or stints, in ``boxscores._STATS_SCHEMA``."""
    gid = f"{_i(rows[0]['GAME_ID']):010d}"
    acc: dict[int, dict[str, int]] = {}
    team_of: dict[int, int] = {}
    reb_ctr: dict[int, tuple[int, int]] = {}
    keys = ["pts", "ast", "fg3m", "stl", "blk", "tov", "fgm", "fga", "fg3a", "ftm", "fta", "pf"]

    def line(pid: int, tid: int) -> dict[str, int]:
        if pid not in acc:
            acc[pid] = dict.fromkeys([*keys, "oreb", "dreb"], 0)
            team_of[pid] = tid
        return acc[pid]

    for r in rows:
        et = _i(r["EVENTMSGTYPE"])
        p1, t1 = _i(r["PLAYER1_ID"]), _i(r["PLAYER1_TEAM_ID"])
        p2, t2 = _i(r["PLAYER2_ID"]), _i(r["PLAYER2_TEAM_ID"])
        p3, t3 = _i(r["PLAYER3_ID"]), _i(r["PLAYER3_TEAM_ID"])
        d = _desc(r)
        if et in (1, 2) and p1 and t1:
            a = line(p1, t1)
            a["fga"] += 1
            three = "3PT" in d
            a["fg3a"] += int(three)
            if et == 1:
                a["fgm"] += 1
                a["fg3m"] += int(three)
                a["pts"] += 3 if three else 2
                if p2 and t2:
                    line(p2, t2)["ast"] += 1
            elif p3 and t3:
                line(p3, t3)["blk"] += 1
        elif et == 3 and p1 and t1:
            a = line(p1, t1)
            a["fta"] += 1
            if ft_made(d):
                a["ftm"] += 1
                a["pts"] += 1
        elif et == 4 and p1 and t1:
            a = line(p1, t1)
            m = _REB_RE.search(d)
            if m:
                off, dfn = int(m.group(1)), int(m.group(2))
                po, pd = reb_ctr.get(p1, (0, 0))
                a["oreb"] += max(off - po, 0)
                a["dreb"] += max(dfn - pd, 0)
                reb_ctr[p1] = (max(off, po), max(dfn, pd))
        elif et == 5 and p1 and t1:
            line(p1, t1)["tov"] += 1
            if p2 and t2:
                line(p2, t2)["stl"] += 1
        elif et == 6 and p1 and t1:
            sub = subtype_of.get((et, _i(r["EVENTMSGACTIONTYPE"])), "")
            if not any(k in sub for k in _TECH_SUBTYPES) and not _TECH_TEXT.search(d):
                line(p1, t1)["pf"] += 1
                if sub == "Double Personal" and p2 and t2:
                    line(p2, t2)["pf"] += 1
            else:
                line(p1, t1)
        elif et == 8:
            if p1 and t1:
                line(p1, t1)
            if p2 and t2:
                line(p2, t2)
    for pid in minutes:
        if pid not in acc:
            raise KeyError(f"stint player {pid} has no event row in game {gid}")
    start_ids = {p for v in (starters or {}).values() for p in v}
    out = []
    for pid, a in acc.items():
        out.append(
            {
                "game_id": gid,
                "player_id": pid,
                "team_id": team_of[pid],
                "minutes": float(minutes.get(pid, 0.0)),
                "pts": a["pts"],
                "reb": a["oreb"] + a["dreb"],
                "ast": a["ast"],
                "fg3m": a["fg3m"],
                "stl": a["stl"],
                "blk": a["blk"],
                "tov": a["tov"],
                "starter": pid in start_ids,
                "fgm": a["fgm"],
                "fga": a["fga"],
                "fg3a": a["fg3a"],
                "ftm": a["ftm"],
                "fta": a["fta"],
                "oreb": a["oreb"],
                "dreb": a["dreb"],
                "pf": a["pf"],
            }
        )
    return pl.DataFrame(out).select(_STATS_SCHEMA)
