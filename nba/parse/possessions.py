"""Play-by-play -> possessions parser.

Turns one game's raw play-by-play (nba_api ``PlayByPlayV3`` schema, see
``nba/ingest/pbp.py``) into rows matching the ``possessions`` table in
CLAUDE.md's schema (``nba/db/schema.sql``).

CLAUDE.md flags this as the highest-risk component ("validate hard"). It
is time-boxed: the segmentation rules below cover the overwhelming
majority of real possessions (checked against several real games in
``data/pbp/*.parquet``) and are validated by the reconciliation gate in
``nba/parse/reconcile.py``. A handful of rare edge cases are deliberately
NOT handled -- see "Known limitations" below -- because they move the
per-team-game possession count by at most a fraction of a possession,
well inside the <=1 mean-error tolerance.

Event order: ``nba.parse.ordering.in_game_order`` (period, clock descending, substitutions
first at a tie, then ``action_number``), NOT ``action_number``. The feed appends post-hoc
corrections after the end of the game with the right period and clock but a late number
(ADR 0002 item 5; docs/reviews/possession_order_2026-10-10.md). Sorted by action number, a
correction landed at the end of its period and produced possessions that end after they
start. Residual limitation: corrections that share a clock with the events around them are
placed by ``action_number`` within that tie, so a late-numbered rebound stamped at the same
second as a putback sorts after it.

Segmentation rules (standard):
  - A possession/"trip" ends on: a made field goal that is NOT followed
    by bonus free throws (no and-one), a defensive rebound, a turnover,
    the made last free throw of a free-throw trip, or the end of a
    period.
  - A rebound by the offense continues the current trip (does not flip
    offense); the trip keeps accumulating until a real possession-ending
    event occurs. This segmentation is unchanged by the ``oreb`` rule below.
  - An and-one (made basket + continuation free throw(s)) stays in the
    same trip; the row's outcome is the made shot (FGM2/FGM3), not
    FT_trip, and ``pts``/``fta`` include the free throw(s).

The ``oreb`` flag (corrected 2026-10-10; docs/prereg/F8_LINEUP_REBOUNDING.md gate G3):

  ``oreb`` is True iff the trip was extended by at least one **player-credited** offensive
  rebound, i.e. a ``Rebound`` event of the offensive team that carries a ``team_id``. A trip's
  outcome is still its final event, so a miss + offensive rebound + made putback is
  (``FGM2``, ``oreb`` True), and a make or a turnover with no earlier offensive rebound in its
  trip is always False. The flag marks "this trip was extended", not "how many times": a
  trip with two offensive rebounds carries one flag (``n_oreb``, available with
  ``parse_possessions(..., with_counts=True)``, counts them).

  Team rebounds (``team_id`` 0, the team parked in ``player_id``; "PACERS Rebound") are not
  flagged: they are not credited to a player, so the box-score ``OREB`` the flag is reconciled
  against does not include them, and 1,021 of 2,498 offense-side team rebounds in a 211-game
  sample follow a non-final missed free throw, where they mark a dead ball between two free
  throws and extend nothing (the second free throw is the same trip). The feed's
  ``(Off:n Def:m)`` counter in player rebounds' descriptions reproduces box OREB exactly
  (4,571 vs 4,571 in that sample), so the player-credited event is the reconcilable one. The
  old rule (any rebound of the offense) flagged those team rebounds and summed to 1.35x box
  OREB; it is why G3 failed.

Scope exclusions for this milestone (see CLAUDE.md "stints" milestone):
  - ``off_players`` / ``def_players`` are left NULL. Lineup/stint
    tracking from substitution events is a separate, later milestone.

Known limitations (time-boxed; documented rather than handled):
  - Technical fouls: the resulting free throw does not actually turn
    the ball over in real NBA rules (play resumes where it left off).
    This parser treats it like any other free-throw trip, i.e. it
    closes a (correct, 1-shot) trip and attributes the next live-ball
    event to a fresh trip. This can at most add one spurious trip per
    technical foul (rare: 0-2 per game leaguewide).
  - Corner3 vs above3: the V3 play-by-play schema pulled by
    ``nba/ingest/pbp.py`` carries no shot x/y coordinates, and
    ``sub_type``/``description`` do not reliably say "Corner" (checked
    against real game 0022200001: zero matches across 69 threes). All
    3-point attempts are classified ``above3`` unless the text
    explicitly says "Corner" (kept as a fallback for rows that do carry
    it).
  - Assister matching: ``assister_id`` is resolved from the
    "(<Last Name> N AST)" suffix in ``description`` via a per-game,
    per-team last-name -> player_id lookup (``player_name`` in the V3
    schema is already last-name only). Left NULL on no match or an
    ambiguous match (two players on the same team sharing a last name
    in the same game).
  - Jump-ball possession start: the team awarded the jump ball is
    inferred from the team of the first live-ball action (shot,
    turnover, free throw) rather than parsed from the jump-ball event
    itself. In the overwhelming majority of games these agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from nba.parse.ordering import CLOCK_RE as _CLOCK_RE
from nba.parse.ordering import in_game_order

#: action_type values with no effect on possession state.
_SKIP_ACTION_TYPES = {
    "Substitution",
    "Timeout",
    "Jump Ball",
    "Instant Replay",
    "Foul",
    "period",
    "",
}

_FGM_OUTCOME = {2: "FGM2", 3: "FGM3"}

_RIM_KEYWORDS = ("Dunk", "Layup")

POSSESSIONS_COLUMNS = [
    "game_id",
    "poss_idx",
    "period",
    "clock_start",
    "clock_end",
    "off_team",
    "def_team",
    "score_diff",
    "outcome",
    "shooter_id",
    "shot_zone",
    "assister_id",
    "oreb",
    "fta",
    "pts",
]

_AST_RE = re.compile(r"\(([A-Za-z.'\- ]+?) (\d+) AST\)\s*$")


def _clock_to_seconds(clock: str) -> float:
    """Parse an ISO-8601 duration like ``PT11M38.00S`` into seconds."""
    m = _CLOCK_RE.match(clock)
    if not m:
        return 0.0
    minutes = int(m.group(1))
    seconds = float(m.group(2))
    return minutes * 60.0 + seconds


def _is_miss(description: str) -> bool:
    return description.strip().upper().startswith("MISS")


def _is_last_ft(sub_type: str) -> bool:
    """True if ``sub_type`` describes the final free throw of its trip."""
    if "Technical" in sub_type:
        return True
    m = re.search(r"(\d+) of (\d+)", sub_type)
    if not m:
        # Unknown shape (shouldn't happen on real data) -- close
        # conservatively rather than leaving a trip open forever.
        return True
    return m.group(1) == m.group(2)


def _shot_zone(shot_value: int, shot_distance: int, sub_type: str, description: str) -> str:
    """Classify a shot attempt into rim/mid/corner3/above3.

    See module docstring "Known limitations" for the corner3 caveat.
    """
    if shot_value == 3:
        text = f"{sub_type} {description}".lower()
        return "corner3" if "corner" in text else "above3"
    if shot_distance < 4 or any(k in sub_type for k in _RIM_KEYWORDS):
        return "rim"
    return "mid"


def _home_away_teams(pbp: pl.DataFrame) -> tuple[int, int]:
    """Derive (home_team_id, away_team_id) from the ``location`` column."""
    home_ids = [t for t in pbp.filter(pl.col("location") == "h")["team_id"].unique().to_list() if t]
    away_ids = [t for t in pbp.filter(pl.col("location") == "v")["team_id"].unique().to_list() if t]
    if not home_ids or not away_ids:
        raise ValueError("could not derive home/away team ids from `location` column")
    return home_ids[0], away_ids[0]


def _build_name_lookup(pbp: pl.DataFrame) -> dict[tuple[int, str], int | None]:
    """(team_id, last_name) -> player_id, or None if ambiguous within a team."""
    pairs = (
        pbp.filter((pl.col("player_id") != 0) & (pl.col("player_name") != ""))
        .select(["team_id", "player_name", "player_id"])
        .unique()
    )
    lookup: dict[tuple[int, str], int | None] = {}
    for team_id, name, player_id in pairs.iter_rows():
        key = (team_id, name)
        if key in lookup:
            lookup[key] = None  # ambiguous: two players, same team, same last name
        else:
            lookup[key] = player_id
    return lookup


def _parse_assist(
    description: str, team_id: int, name_lookup: dict[tuple[int, str], int | None]
) -> int | None:
    m = _AST_RE.search(description)
    if not m:
        return None
    return name_lookup.get((team_id, m.group(1).strip()))


@dataclass
class _Trip:
    period: int
    off_team: int
    def_team: int
    clock_start: float
    had_fgm: bool = False
    oreb: bool = False
    n_oreb: int = 0
    fta: int = 0
    pts: int = 0
    shooter_id: int | None = None
    assister_id: int | None = None
    shot_zone: str | None = None
    outcome: str = "other"
    score_home: int = 0
    score_away: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def parse_possessions(pbp: pl.DataFrame, *, with_counts: bool = False) -> pl.DataFrame:
    """Parse one game's raw play-by-play into ``possessions`` rows.

    ``pbp`` is a single game's frame in the V3 schema produced by
    ``nba/ingest/pbp.py`` (or an equivalent frame, e.g. a fixture). See
    the module docstring for segmentation rules and known limitations.
    ``with_counts=True`` appends ``n_oreb`` (player-credited offensive rebounds in the trip);
    the stored table does not carry it.
    """
    schema = [*POSSESSIONS_COLUMNS, "n_oreb"] if with_counts else POSSESSIONS_COLUMNS
    if pbp.is_empty():
        return pl.DataFrame(schema=schema)

    game_ids = pbp["game_id"].unique().to_list()
    if len(game_ids) != 1:
        raise ValueError(f"parse_possessions expects a single game_id, got {game_ids}")
    game_id = game_ids[0]

    pbp = in_game_order(pbp)
    home_team, away_team = _home_away_teams(pbp)
    name_lookup = _build_name_lookup(pbp)

    def other_team(team_id: int) -> int:
        return away_team if team_id == home_team else home_team

    rows: list[dict[str, Any]] = []
    poss_idx = 0
    trip: _Trip | None = None
    cur_period: int | None = None
    last_score = (0, 0)

    def finalize(clock_end: float) -> None:
        nonlocal poss_idx, trip
        if trip is None:
            return
        off_score = trip.score_home if trip.off_team == home_team else trip.score_away
        def_score = trip.score_away if trip.off_team == home_team else trip.score_home
        row = {
            "game_id": game_id,
            "poss_idx": poss_idx,
            "period": trip.period,
            "clock_start": trip.clock_start,
            "clock_end": clock_end,
            "off_team": trip.off_team,
            "def_team": trip.def_team,
            "score_diff": off_score - def_score,
            "outcome": trip.outcome,
            "shooter_id": trip.shooter_id,
            "shot_zone": trip.shot_zone,
            "assister_id": trip.assister_id,
            "oreb": trip.oreb,
            "fta": trip.fta,
            "pts": trip.pts,
        }
        if with_counts:
            row["n_oreb"] = trip.n_oreb
        rows.append(row)
        poss_idx += 1
        trip = None

    def start_trip(period: int, off_team: int, clock_start: float) -> _Trip:
        return _Trip(
            period=period,
            off_team=off_team,
            def_team=other_team(off_team),
            clock_start=clock_start,
            score_home=last_score[0],
            score_away=last_score[1],
        )

    for ev in pbp.iter_rows(named=True):
        period = ev["period"]
        if cur_period is not None and period != cur_period:
            # Force-close a dangling trip at the previous period's buzzer.
            finalize(0.0)
        cur_period = period

        sh, sa = ev["score_home"], ev["score_away"]
        if sh not in (None, "") and sa not in (None, ""):
            last_score = (int(sh), int(sa))
        if trip is not None:
            trip.score_home, trip.score_away = last_score

        atype = ev["action_type"]
        if atype in _SKIP_ACTION_TYPES:
            continue

        # Team-level actions (team turnovers, team rebounds -- e.g. shot
        # clock violations, out-of-bounds team rebounds) carry team_id=0
        # with the real team id parked in player_id instead. Checked
        # against real data (game 0022200001): "CELTICS Turnover: Shot
        # Clock", "76ers Rebound" both show this pattern.
        team = ev["team_id"] or ev["player_id"]
        clock_s = _clock_to_seconds(ev["clock"])

        if atype == "Missed Shot":
            if trip is None or trip.off_team != team or trip.had_fgm:
                finalize(clock_s)
                trip = start_trip(period, team, clock_s)
            trip.had_fgm = False
            trip.outcome = "FGA_miss"
            trip.shooter_id = ev["player_id"]
            trip.shot_zone = _shot_zone(
                ev["shot_value"], ev["shot_distance"], ev["sub_type"], ev["description"]
            )

        elif atype == "Made Shot":
            if trip is None or trip.off_team != team:
                finalize(clock_s)
                trip = start_trip(period, team, clock_s)
            trip.had_fgm = True
            trip.pts += ev["shot_value"]
            trip.outcome = _FGM_OUTCOME.get(ev["shot_value"], "other")
            trip.shooter_id = ev["player_id"]
            trip.shot_zone = _shot_zone(
                ev["shot_value"], ev["shot_distance"], ev["sub_type"], ev["description"]
            )
            trip.assister_id = _parse_assist(ev["description"], team, name_lookup)

        elif atype == "Free Throw":
            if trip is None or trip.off_team != team:
                finalize(clock_s)
                trip = start_trip(period, team, clock_s)
            trip.fta += 1
            made = not _is_miss(ev["description"])
            if not trip.had_fgm:
                # No field goal preceded this trip (or hasn't yet) -- the
                # free throw(s) are the primary outcome regardless of
                # make/miss. An and-one keeps `had_fgm` True and the FGM
                # outcome set above, untouched here.
                trip.outcome = "FT_trip"
            if made:
                trip.pts += 1
            if _is_last_ft(ev["sub_type"]) and made:
                finalize(clock_s)
            # else (missed last FT): trip stays open, waiting for the rebound event.

        elif atype == "Turnover":
            if trip is None or trip.off_team != team:
                finalize(clock_s)
                trip = start_trip(period, team, clock_s)
            trip.outcome = "TOV"
            finalize(clock_s)

        elif atype == "Rebound":
            if trip is None:
                continue
            if team == trip.off_team:
                # Player-credited only (module docstring, "The oreb flag"). A team rebound
                # (team_id 0) leaves the trip open exactly as before but is not flagged.
                if ev["team_id"]:
                    trip.oreb = True
                    trip.n_oreb += 1
            else:
                finalize(clock_s)

        # Anything else (shouldn't occur given _SKIP_ACTION_TYPES) is ignored.

    finalize(0.0)
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema_overrides={"outcome": pl.Utf8, "shot_zone": pl.Utf8})
