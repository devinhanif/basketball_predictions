# ruff: noqa: E501
"""'If a teammate sits': what the nightly export's scenario rows may say, in the shape the Explore picker shows.

The export (nba.export.teammate_sits, docs/contracts/teammate_sits_v1.md) carries the model's forecast for each
teammate with one regular starter added to the OUT set, for points and rebounds. What the evidence supports
(T526 / T526-a, tested on 2023-25 history) decides what leaves this module, and the server enforces it so no
client can show more:

  * points: a DIRECTION for each teammate and ONE team-level number (the teammates' points forecasts, weighted by
    their chance to play, move by about N in total / per likely player). A constant team scale recovered about 92%
    of the gain, so the split between teammates is not claimed beyond direction: no per-player size is sent.
  * rebounds: a direction only, labelled "uncertain size". No size is sent at all.
  * assists and threes: not shown (the test did not settle them). The export does not carry them.

No price, no probability of an outcome, no stake. On a night inside the LINE_CAL_LIVE lock a finished game gets no
scenario (a model number is never put beside an outcome there).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel

from .trackrecord import MODEL_LOCK_FROM

Json = dict[str, Any]
Direction = Literal["up", "down", "flat"]

#: |change| under this many points (or rebounds) is called flat: it is rounding, not a direction.
FLAT_BELOW = 0.05
#: A teammate "likely plays" at or above this chance to play (the export's production number, unchanged).
LIKELY = 0.5

EVIDENCE = [
    "Tested on 2023-25 history: helps for points. The model's points forecasts for the other players "
    "rise as a group when a starter is out. Only the direction for each player and the size for the "
    "team are claimed; how the extra points split between teammates is not.",
    "Tested on 2023-25 history: rebounds direction only. The size is uncertain.",
    "Assists and threes are not shown: the test did not settle them.",
]
NOT_MODELLED = [
    "Who specifically picks up his role. The model spreads it by each player's own role, learned across the league.",
    "A teammate moving into the starting five, or his own chance to play.",
    "The opponent, the pace, the final score, or two or more players sitting at once.",
    "Team totals are not forced to add up.",
]
LOCK_TEXT = "The model's numbers for this night are part of a locked test until about Dec 1."

REASON_TEXT = {
    "disabled": "Scenarios were switched off for this build.",
    "no_model_cache": "Scenarios were not built: the saved model for tonight was not available.",
    "model_cache_stale": "Scenarios were not built: the saved model no longer matched the latest data.",
    "no_unfinished_games": "Scenarios are built only for games that have not finished.",
    "no_roster_rows": "Scenarios were not built: no players were placed on tonight's games.",
    "no_candidate_sitters": "No regular starter was available to sit tonight.",
    "no_forecast_rows": "Scenarios were not built: no forecasts came out.",
    "over_row_budget": "Scenarios were withheld: too many rows for one night.",
    "over_time_budget": "Scenarios were withheld: they took too long to build tonight.",
}


class TeamBump(BaseModel):
    """Points: the whole team's move. ``total`` weights each teammate by his chance to play; ``per_player`` is the
    mean move of teammates who likely play (chance to play 50% or more)."""

    direction: Direction
    total: float
    per_player: float | None
    n_likely: int


class RebBump(BaseModel):
    direction: Direction
    uncertain_size: bool = True


class ScenarioTeammate(BaseModel):
    player_id: int
    name: str
    likely_plays: bool
    pts: Direction
    reb: Direction | None


class ScenarioSitter(BaseModel):
    player_id: int
    name: str
    team_id: int
    side: Literal["home", "away"] | None
    status: str
    pts: TeamBump
    reb: RebBump | None
    teammates: list[ScenarioTeammate]


class ScenariosView(BaseModel):
    game_id: str
    available: bool
    reason: str | None
    made_at: str | None
    evidence: list[str]
    not_modelled: list[str]
    sitters: list[ScenarioSitter]


def _dir(x: float) -> Direction:
    return "up" if x >= FLAT_BELOW else "down" if x <= -FLAT_BELOW else "flat"


def _view(
    g: Json, available: bool, reason: str | None, made_at: str | None = None
) -> ScenariosView:
    return ScenariosView(
        game_id=str(g["game_id"]),
        available=available,
        reason=reason,
        made_at=made_at,
        evidence=EVIDENCE if available else [],
        not_modelled=NOT_MODELLED if available else [],
        sitters=[],
    )


def _name(store: Any, g: Json, pid: int) -> str:
    for p in g["players"]:
        if int(p["player_id"]) == pid:
            return str(p["name"])
    names = getattr(store, "names", {})
    return str(names.get(pid, f"Player {pid}"))


def game_scenarios(store: Any, g: Json) -> ScenariosView:
    """The picker's data for one game. Never raises for a game with no scenarios: it says why."""
    if bool(g.get("final")) and str(g["date"]) >= MODEL_LOCK_FROM:
        return _view(g, False, LOCK_TEXT)
    info = getattr(store, "sits_info", None)
    if info is None:
        return _view(g, False, "This data has no 'if a teammate sits' scenarios.")
    if info.get("status") != "ok":
        why = str(info.get("reason") or "")
        text = REASON_TEXT.get(
            why, "Scenarios were not built for this data." if why else "Scenarios are absent."
        )
        if why.startswith("error:"):
            text = (
                "Scenarios were not built: that step failed tonight. Everything else is unaffected."
            )
        return _view(g, False, text)
    rows = getattr(store, "sits_rows", {}).get(str(g["game_id"]), [])
    if not rows:
        return _view(
            g,
            False,
            "No scenarios for this game: no usable injury report yet, or no regular starter to sit.",
        )

    by: dict[int, list[Json]] = defaultdict(list)
    for r in rows:
        if r["stat"] in ("pts", "reb"):  # nothing else is ever sent, whatever the export holds
            by[int(r["sitter_id"])].append(r)
    home_id = int(g["home"]["id"])
    away_id = int(g["away"]["id"])
    sitters: list[ScenarioSitter] = []
    for sid, srows in by.items():
        tm: dict[int, dict[str, Json]] = defaultdict(dict)
        for r in srows:
            tm[int(r["player_id"])][str(r["stat"])] = r
        pts_rows = [m["pts"] for m in tm.values() if "pts" in m]
        if not pts_rows:
            continue

        def w(r: Json) -> float:
            return 1.0 if r.get("p_play") is None else float(r["p_play"])

        total = sum(w(r) * float(r["delta_mean"]) for r in pts_rows)
        likely = [float(r["delta_mean"]) for r in pts_rows if w(r) >= LIKELY]
        reb_rows = [m["reb"] for m in tm.values() if "reb" in m]
        reb_total = sum(w(r) * float(r["delta_mean"]) for r in reb_rows)
        team_id = int(srows[0]["team_id"])
        mates = [
            ScenarioTeammate(
                player_id=pid,
                name=_name(store, g, pid),
                likely_plays=w(m.get("pts") or m["reb"]) >= LIKELY,
                pts=_dir(float(m["pts"]["delta_mean"])) if "pts" in m else "flat",
                reb=_dir(float(m["reb"]["delta_mean"])) if "reb" in m else None,
            )
            for pid, m in tm.items()
        ]
        mates.sort(key=lambda t: (not t.likely_plays, t.name))
        sitters.append(
            ScenarioSitter(
                player_id=sid,
                name=_name(store, g, sid),
                team_id=team_id,
                side="home" if team_id == home_id else "away" if team_id == away_id else None,
                status=str(srows[0].get("sitter_status") or "unlisted"),
                pts=TeamBump(
                    direction=_dir(total) if abs(total) >= 0.1 else "flat",
                    total=round(total, 1),
                    per_player=round(sum(likely) / len(likely), 1) if likely else None,
                    n_likely=len(likely),
                ),
                reb=RebBump(direction=_dir(reb_total) if abs(reb_total) >= 0.1 else "flat")
                if reb_rows
                else None,
                teammates=mates,
            )
        )
    sitters.sort(key=lambda s: (s.side != "home", s.team_id, -s.pts.total, s.name))
    raw = rows[0].get("made_at")
    made = (raw.isoformat() + "Z") if hasattr(raw, "isoformat") else (str(raw) if raw else None)
    out = _view(g, bool(sitters), None if sitters else "No scenarios for this game.", made)
    out.sitters = sitters
    return out
