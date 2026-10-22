# ruff: noqa: E501
"""Pure parsers from an ESPN ``summary`` body to the in-game state v2 needs (no fetch, no write).

Fields used, all present in the 2026-10-21 capture (``data/raw/live_2026-10-21/espn_summary``):
* ``header.competitions[0].competitors[i]``: ``homeAway``, ``score``, ``timeoutsRemaining``, ``timeoutsUsed``,
  ``fouls.teamFoulsCurrent`` (fouls in the current period), ``fouls.foulsToGive``, ``fouls.bonusState``.
  ``possession`` is always False in the capture (0 of 662 in-progress team rows): never read it.
* ``header.competitions[0].status``: ``type.state`` (pre/in/post), ``period``, ``displayClock``.
* ``boxscore.players[t].statistics[0].athletes[]``: ``active`` (on court; exactly 5 per team in 98 of 98
  in-progress snapshots of one game), ``starter``, ``stats`` aligned to ``names`` (``PF`` = personal fouls).
* ``plays[]``: ``type.text``, ``team.id`` (the acting team; for a foul the committer), ``participants`` (substitution: entering player first), ``scoringPlay``,
  ``shootingPlay``, ``period.number``, ``clock.displayValue``.
* ``winprobability[]``: ESPN's own home win probability (the shadow benchmark, never an input).

Possession is derived from the plays by the same rule that is applied to the nba.com play-by-play in history
(``update_possession``), so training and serving share one definition.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from nba.ride.winprob import parse_clock, seconds_left

Kind = str  # made_fg | miss_fg | ft | oreb | dreb | tov | foul | jump | reset | other

_FT_OF = re.compile(r"(\d)\s+of\s+(\d)")
_TOV_TYPES = {"traveling", "offensive charge", "offensive foul"}
_FOUL_TYPES = {
    "shooting foul",
    "personal foul",
    "loose ball foul",
    "personal take foul",
    "flagrant foul type 1",
    "flagrant foul type 2",
}


@dataclass(frozen=True)
class Event:
    """A play reduced to what the possession rule needs."""

    kind: Kind
    team: str  # acting team id as a string, "" when the play has none
    made: bool = False
    last_ft: bool = False


def other(team: str, home: str, away: str) -> str | None:
    if team == home:
        return away
    if team == away:
        return home
    return None


def update_possession(poss: str | None, ev: Event, home: str, away: str) -> str | None:
    """Team holding the ball after ``ev`` (None = loose ball or unknown). Assignments, never toggles, so a
    duplicated report of the same stoppage (ESPN lists a foul and its turnover separately) is harmless."""
    if ev.kind == "made_fg":
        return other(ev.team, home, away)
    if ev.kind == "miss_fg":
        return None
    if ev.kind == "ft":
        if not ev.last_ft:
            return ev.team or poss
        return other(ev.team, home, away) if ev.made else None
    if ev.kind in ("oreb", "dreb", "jump"):
        return ev.team or poss
    if ev.kind in ("tov", "foul"):
        return other(ev.team, home, away) or poss
    if ev.kind == "reset":
        return None
    return poss


def classify_play(p: dict[str, Any]) -> Event:
    """ESPN play -> Event. Unknown plays are ``other`` and leave possession unchanged."""
    t = str((p.get("type") or {}).get("text", "")).strip()
    tl = t.lower()
    text = str(p.get("text", "")).lower()
    team = str((p.get("team") or {}).get("id", ""))
    scoring = bool(p.get("scoringPlay"))
    if tl in ("end period", "end game", "start period"):
        return Event("reset", "")
    if tl.startswith("free throw"):
        if "technical" in tl:
            return Event("other", team)
        m = _FT_OF.search(tl)
        last = m is not None and m.group(1) == m.group(2)
        return Event("ft", team, made=scoring, last_ft=last)
    if tl == "jumpball":
        return Event(
            "other", team
        )  # history cannot tell who won the tip: same rule both sides (review S2)
    if tl == "offensive rebound":
        return Event("oreb", team)
    if tl == "defensive rebound":
        return Event("dreb", team)
    if "turnover" in tl or "turnover" in text or tl in _TOV_TYPES:
        return Event("tov", team)
    if tl in _FOUL_TYPES:
        return Event("foul", team)
    if p.get("shootingPlay") and "foul" not in tl and tl != "substitution":
        return Event("made_fg" if scoring else "miss_fg", team, made=scoring)
    return Event("other", team)


def possession_from_plays(plays: Iterable[dict[str, Any]], home: str, away: str) -> str | None:
    poss: str | None = None
    for p in plays:
        poss = update_possession(poss, classify_play(p), home, away)
    return poss


@dataclass(frozen=True)
class TeamLive:
    team_id: str
    abbr: str
    score: int
    timeouts_remaining: int | None
    team_fouls_period: int | None
    fouls_to_give: int | None
    bonus_state: str | None
    on_court: tuple[str, ...]
    starters: tuple[str, ...]
    player_fouls: dict[str, int]


@dataclass(frozen=True)
class LiveState:
    event_id: str
    state: str
    period: int
    clock_secs: float
    secs_left: float
    home: TeamLive
    away: TeamLive
    possession: str | None  # "home" | "away" | None
    n_plays: int
    espn_home_wp: float | None  # ESPN's value at the latest play (benchmark only)


def _int(x: Any) -> int | None:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def parse_team(comp: dict[str, Any], players: dict[str, Any] | None) -> TeamLive:
    fouls = comp.get("fouls") or {}
    on: list[str] = []
    starters: list[str] = []
    pf: dict[str, int] = {}
    if players:
        block = (players.get("statistics") or [{}])[0]
        names = list(block.get("names") or [])
        pf_ix = names.index("PF") if "PF" in names else None
        for a in block.get("athletes") or []:
            aid = str((a.get("athlete") or {}).get("id", ""))
            if not aid:
                continue
            if a.get("active"):
                on.append(aid)
            if a.get("starter"):
                starters.append(aid)
            stats = a.get("stats") or []
            if pf_ix is not None and pf_ix < len(stats):
                v = _int(stats[pf_ix])
                if v is not None:
                    pf[aid] = v
    team = comp.get("team") or {}
    return TeamLive(
        team_id=str(team.get("id", "")),
        abbr=str(team.get("abbreviation", "")),
        score=_int(comp.get("score")) or 0,
        timeouts_remaining=_int(comp.get("timeoutsRemaining")),
        team_fouls_period=_int(fouls.get("teamFoulsCurrent")),
        fouls_to_give=_int(fouls.get("foulsToGive")),
        bonus_state=fouls.get("bonusState"),
        on_court=tuple(on),
        starters=tuple(starters),
        player_fouls=pf,
    )


def latest_espn_wp(body: dict[str, Any]) -> float | None:
    """ESPN home win probability at the newest play (entries after the last play are post-game junk)."""
    ids = [_int(p.get("id")) for p in body.get("plays") or []]
    ids_ok = [i for i in ids if i is not None]
    if not ids_ok:
        return None
    last = max(ids_ok)
    best, wp = -1, None
    for w in body.get("winprobability") or []:
        pid = _int(w.get("playId"))
        if pid is not None and best < pid <= last and w.get("homeWinPercentage") is not None:
            best, wp = pid, float(w["homeWinPercentage"])
    return wp


def parse_state(body: dict[str, Any], event_id: str) -> LiveState | None:
    """Summary body -> LiveState, or None when the structure is not recognised (never guess)."""
    try:
        comp = body["header"]["competitions"][0]
        status = comp["status"]
        by_side = {c["homeAway"]: c for c in comp["competitors"]}
        home_c, away_c = by_side["home"], by_side["away"]
        period = _int(status.get("period")) or 0
        clock = parse_clock(str(status.get("displayClock", "0")))
        state = str(status["type"]["state"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    box = {
        str((t.get("team") or {}).get("id", "")): t
        for t in (body.get("boxscore") or {}).get("players") or []
    }
    home = parse_team(home_c, box.get(str((home_c.get("team") or {}).get("id", ""))))
    away = parse_team(away_c, box.get(str((away_c.get("team") or {}).get("id", ""))))
    plays = body.get("plays") or []
    poss_id = possession_from_plays(plays, home.team_id, away.team_id)
    poss = "home" if poss_id == home.team_id else "away" if poss_id == away.team_id else None
    return LiveState(
        event_id=event_id,
        state=state,
        period=period,
        clock_secs=clock,
        secs_left=seconds_left(period, clock) if period else 2880.0,
        home=home,
        away=away,
        possession=poss,
        n_plays=len(plays),
        espn_home_wp=latest_espn_wp(body),
    )


def substitution(p: dict[str, Any]) -> tuple[str, str, str] | None:
    """(team id, entering athlete id, leaving athlete id) of a Substitution play, else None."""
    if str((p.get("type") or {}).get("text", "")) != "Substitution":
        return None
    parts = [str((x.get("athlete") or {}).get("id", "")) for x in p.get("participants") or []]
    if len(parts) != 2 or not all(parts):
        return None
    return str((p.get("team") or {}).get("id", "")), parts[0], parts[1]


def timeout_team(p: dict[str, Any]) -> str | None:
    if str((p.get("type") or {}).get("text", "")) != "Full Timeout":
        return None
    return str((p.get("team") or {}).get("id", "")) or None
