"""Lineup / stint tracker: who is on the court, possession by possession.

Deferred "parser v2" from ``nba/parse/possessions.py`` (which leaves
``off_players``/``def_players`` NULL). This module tracks each team's
on-court five through a game from ``Substitution`` events in the raw V3
play-by-play (see ``nba/ingest/pbp.py``), seeded with the real starters,
and turns that into:

  - ``stints`` rows (``nba/db/schema.sql``): contiguous (period, clock)
    spans where a team's 5-man unit is unchanged.
  - ``off_players``/``def_players`` on each ``possessions`` row, by
    matching the possession's (period, clock_start) against the stint
    covering that instant for the offense and defense team.

CLAUDE.md flags the parser family as the highest-risk component
("validate hard"); this module is additionally time-boxed per the task
that commissioned it. See "Period-boundary heuristic" below for the one
genuinely hard sub-problem and its documented failure modes.

## Period-boundary heuristic

The on-court five at the start of Q2/Q3/Q4/OT is **not** necessarily the
starters, and real play-by-play frequently has **no substitution event
at all** marking the reset (checked against real game 0022200001: Tatum
is subbed out mid-Q1 and is back on the floor shooting the first shot of
Q3, with zero ``Substitution`` rows between). The heuristic:

1. Carry over the five players who closed the previous period as the
   *default* hypothesis for the new period's opening five.
2. Track a per-team, per-period "confirmed" set: a player is confirmed
   the moment they appear in a real action (shot/rebound/foul/turnover/
   free throw/violation/jump ball) or as the explicit "in" side of a
   ``Substitution``.
3. Whenever a player acts for their team and is **not** currently in the
   tracked on-court five, treat that as a correction: evict one
   unconfirmed player (first one in insertion order -- i.e. prefer
   evicting a player from the stale carried-over guess who hasn't done
   anything yet this period) and add the player who just acted. This
   closes the current stint and opens a new one at that instant.
4. Substitutions mid-period are applied directly (remove the named
   "out" player, add the named "in" player); if removing "out" leaves
   the five short the "in" player still joins first and a size-6
   overflow is resolved with the same unconfirmed-eviction rule, so the
   invariant "exactly 5 players at all times" never breaks.

### Known failure modes (documented, not fixed -- time-boxed)
- **Ambiguous/unresolved sub names.** The "in" player of a
  ``Substitution`` row is parsed from free text (``"SUB: X FOR Y"``) and
  resolved to a player_id via a per-game (team, last_name) lookup built
  from other rows in the same game. The free-text name and the
  canonical ``player_name`` for the same player are not always
  byte-identical -- checked against real data: diacritics are dropped
  inconsistently (``"Doncic"`` in sub text vs. ``"Dončić"`` in
  ``player_name``) and name suffixes are sometimes dropped
  (``"Bullock"`` vs. ``"Bullock Jr."``). Resolution therefore tries an
  exact match first, then a normalized match (NFKD-fold diacritics,
  strip a trailing ``Jr./Sr./II/III/IV`` suffix, lowercase). If *that*
  still doesn't resolve (a genuine typo, or two players on the same
  team sharing a last name even after normalization) the substitution
  is skipped entirely (left as a no-op) rather than corrupting the five
  -- the lineup then self-corrects on the next confirming action for
  the real player, so the error window is at most a handful of events.
- **Simultaneous unconfirmed errors.** If the previous period ended with
  the tracker's guess already wrong in *two* slots, the first
  action-based correction fixes one slot; the second wrong slot is only
  fixed once that specific player also acts. Until then, one wrong
  player is attributed to on-court possessions in that window. This is
  the main source of imperfect 5-5 possessions reported in the
  validation summary.
- **Team-level events carry no lineup signal.** Team rebounds/turnovers
  (``team_id == 0``, see ``nba/parse/possessions.py``) and the
  unresolved side of a ``Jump Ball`` (only one team's jumper is a
  distinct row) give no information and are skipped.
- **Garbage-time/ejection edge cases** (a team briefly playing with a
  reconstructed five after a disqualification) are not specially
  handled; they fall under the general correction rule above.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import polars as pl

STINTS_COLUMNS = ["game_id", "team_id", "period", "start_clock", "end_clock", "players"]

#: action_type values whose player_id, when team_id != 0, confirms that
#: player is on the court right now. Matches the set of non-skipped
#: action types in nba/parse/possessions.py plus Foul/Violation/Jump Ball,
#: which possessions.py ignores but which still carry useful on-court
#: signal for lineup tracking.
_CONFIRM_ACTION_TYPES = {
    "Made Shot",
    "Missed Shot",
    "Free Throw",
    "Rebound",
    "Turnover",
    "Foul",
    "Violation",
    "Jump Ball",
}

_CLOCK_RE = re.compile(r"PT(\d+)M([\d.]+)S")
_SUB_RE = re.compile(r"^SUB:\s*(.+?)\s+FOR\s+(.+)$")


def _clock_to_seconds(clock: str) -> float:
    m = _CLOCK_RE.match(clock)
    if not m:
        return 0.0
    return int(m.group(1)) * 60.0 + float(m.group(2))


def _period_start_clock(period: int) -> float:
    """Regulation periods are 12 minutes; OT periods are 5 minutes."""
    return 720.0 if period <= 4 else 300.0


_NAME_SUFFIXES = (" Jr.", " Jr", " Sr.", " Sr", " II", " III", " IV")


def _normalize_name(name: str) -> str:
    """Fold diacritics and strip a trailing suffix for fuzzy name matching.

    See module docstring "Known failure modes" -- substitution free text
    doesn't always match ``player_name`` byte-for-byte.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    for suffix in _NAME_SUFFIXES:
        if folded.endswith(suffix):
            folded = folded[: -len(suffix)]
            break
    return folded.strip().lower()


def _build_name_lookup(
    pbp: pl.DataFrame,
) -> tuple[dict[tuple[int, str], int | None], dict[tuple[int, str], int | None]]:
    """Build (team_id, name) -> player_id lookups, exact and normalized.

    Returns ``(exact, normalized)``; each maps to ``None`` if ambiguous
    within a team (two players collide on that key). Deliberately
    duplicated from ``nba/parse/possessions.py`` (same heuristic, used
    there for assist-name resolution) to keep the two parser modules
    independently readable rather than sharing a private helper across
    module boundaries.
    """
    pairs = (
        pbp.filter((pl.col("player_id") != 0) & (pl.col("player_name") != ""))
        .select(["team_id", "player_name", "player_id"])
        .unique()
    )
    exact: dict[tuple[int, str], int | None] = {}
    normalized: dict[tuple[int, str], int | None] = {}
    for team_id, name, player_id in pairs.iter_rows():
        key = (team_id, name)
        exact[key] = None if key in exact else player_id
        norm_key = (team_id, _normalize_name(name))
        normalized[norm_key] = None if norm_key in normalized else player_id
    return exact, normalized


def _resolve_name(
    team_id: int,
    name: str,
    exact: dict[tuple[int, str], int | None],
    normalized: dict[tuple[int, str], int | None],
) -> int | None:
    resolved = exact.get((team_id, name))
    if resolved is not None:
        return resolved
    return normalized.get((team_id, _normalize_name(name)))


def _evict_one(players: list[int], confirmed: set[int], protect: int | None = None) -> int:
    """Pick a player to evict when the on-court five must shrink by one.

    Prefers a player who hasn't been confirmed active yet this period
    (i.e. likely the stale half of a wrong carry-over guess), in
    insertion order for determinism. Falls back to the first non-
    protected player if everyone is already confirmed.
    """
    candidates = [p for p in players if p not in confirmed and p != protect]
    if not candidates:
        candidates = [p for p in players if p != protect]
    return candidates[0]


@dataclass
class _TeamState:
    players: list[int]
    confirmed: set[int] = field(default_factory=set)
    stint_start_clock: float = 0.0
    stint_start_period: int = 1


def track_lineups(pbp: pl.DataFrame, starters_by_team: dict[int, list[int]]) -> pl.DataFrame:
    """Track each team's on-court five through one game's play-by-play.

    ``pbp`` is a single game's V3-schema frame (see
    ``nba/ingest/pbp.py``). ``starters_by_team`` maps team_id -> list of
    5 player_ids (``player_game_stats.starter`` in the real pipeline; a
    synthetic roster in tests). Returns a ``stints`` frame matching
    ``STINTS_COLUMNS`` -- one row per contiguous (period, clock) span
    where a team's five is unchanged. See the module docstring for the
    period-boundary heuristic.
    """
    if pbp.is_empty():
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "team_id": pl.Int64,
                "period": pl.Int64,
                "start_clock": pl.Float64,
                "end_clock": pl.Float64,
                "players": pl.List(pl.Int64),
            }
        )

    game_ids = pbp["game_id"].unique().to_list()
    if len(game_ids) != 1:
        raise ValueError(f"track_lineups expects a single game_id, got {game_ids}")
    game_id = game_ids[0]

    for team_id, roster in starters_by_team.items():
        if len(set(roster)) != 5:
            raise ValueError(f"starters_by_team[{team_id}] must have exactly 5 unique players")

    pbp = pbp.sort(["period", "action_number"])
    exact_lookup, normalized_lookup = _build_name_lookup(pbp)
    teams = sorted(starters_by_team.keys())

    state: dict[int, _TeamState] = {
        t: _TeamState(
            players=list(starters_by_team[t]),
            stint_start_clock=_period_start_clock(1),
            stint_start_period=1,
        )
        for t in teams
    }
    stint_rows: list[dict[str, Any]] = []
    cur_period = 1

    def close_stint(team: int, end_clock: float) -> None:
        st = state[team]
        stint_rows.append(
            {
                "game_id": game_id,
                "team_id": team,
                "period": st.stint_start_period,
                "start_clock": st.stint_start_clock,
                "end_clock": end_clock,
                "players": sorted(st.players),
            }
        )

    def change_lineup(team: int, new_players: list[int], clock: float, period: int) -> None:
        close_stint(team, clock)
        st = state[team]
        st.players = new_players
        st.stint_start_clock = clock
        st.stint_start_period = period

    for ev in pbp.iter_rows(named=True):
        period = ev["period"]
        if period != cur_period:
            for t in teams:
                close_stint(t, 0.0)
                st = state[t]
                st.stint_start_clock = _period_start_clock(period)
                st.stint_start_period = period
                st.confirmed = set()
            cur_period = period

        team_id = ev["team_id"]
        atype = ev["action_type"]
        clock_s = _clock_to_seconds(ev["clock"])

        if atype == "Substitution":
            if team_id not in state:
                continue
            m = _SUB_RE.match((ev["description"] or "").strip())
            if not m:
                continue
            in_name = m.group(1).strip()
            in_id = _resolve_name(team_id, in_name, exact_lookup, normalized_lookup)
            if in_id is None:
                # Unresolved "in" name -- skip rather than corrupt the
                # five; see module docstring "Known failure modes".
                continue
            out_id = ev["player_id"]
            st = state[team_id]
            if in_id in st.players:
                # `in_id` is already tracked as on-court -- this sub is
                # inconsistent with our current state (an earlier
                # unresolved/miscorrected event desynced us). Treat as a
                # no-op rather than risk introducing a duplicate and
                # shrinking the five on a later correction; see module
                # docstring "Known failure modes".
                st.confirmed.add(in_id)
                continue
            players = [p for p in st.players if p != out_id]
            players.append(in_id)
            if len(players) > 5:
                evict = _evict_one([p for p in players if p != in_id], st.confirmed, protect=in_id)
                players.remove(evict)
            st.confirmed.add(in_id)
            if sorted(players) != sorted(st.players):
                change_lineup(team_id, players, clock_s, period)
            continue

        if team_id == 0 or team_id not in state or atype not in _CONFIRM_ACTION_TYPES:
            continue
        player_id = ev["player_id"]
        if not player_id:
            continue
        st = state[team_id]
        if player_id not in st.players:
            evict = _evict_one(st.players, st.confirmed)
            players = [p for p in st.players if p != evict]
            players.append(player_id)
            change_lineup(team_id, players, clock_s, period)
        state[team_id].confirmed.add(player_id)

    for t in teams:
        close_stint(t, 0.0)

    if not stint_rows:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "team_id": pl.Int64,
                "period": pl.Int64,
                "start_clock": pl.Float64,
                "end_clock": pl.Float64,
                "players": pl.List(pl.Int64),
            }
        )
    return pl.DataFrame(stint_rows).select(STINTS_COLUMNS)


def _covering_stint(
    stints_by_team: dict[int, list[dict[str, Any]]], team: int, period: int, clock: float
) -> list[int] | None:
    matches = [
        row
        for row in stints_by_team.get(team, [])
        if row["period"] == period and row["end_clock"] <= clock <= row["start_clock"]
    ]
    if not matches:
        return None
    # Prefer the tightest-covering (most recently started) stint, so a
    # possession beginning exactly at a substitution's clock value is
    # attributed to the lineup that took effect at that instant.
    best = min(matches, key=lambda r: r["start_clock"])
    return list(best["players"])


def attach_lineups_to_possessions(possessions: pl.DataFrame, stints: pl.DataFrame) -> pl.DataFrame:
    """Fill ``off_players``/``def_players`` on ``possessions`` rows from ``stints``.

    Matches each possession's (period, clock_start) against the stint
    covering that instant for its offense and defense team. Returns
    ``possessions`` with the two columns added/replaced; a possession
    whose team has no covering stint (shouldn't happen on real data
    once starters are correct, but can on malformed fixtures) gets
    ``None`` for that side rather than raising.
    """
    if possessions.is_empty():
        return possessions.with_columns(
            pl.lit(None, dtype=pl.List(pl.Int64)).alias("off_players"),
            pl.lit(None, dtype=pl.List(pl.Int64)).alias("def_players"),
        )

    stints_by_team: dict[int, list[dict[str, Any]]] = {}
    for row in stints.iter_rows(named=True):
        stints_by_team.setdefault(row["team_id"], []).append(row)

    off_list: list[list[int] | None] = []
    def_list: list[list[int] | None] = []
    for row in possessions.iter_rows(named=True):
        off_list.append(
            _covering_stint(stints_by_team, row["off_team"], row["period"], row["clock_start"])
        )
        def_list.append(
            _covering_stint(stints_by_team, row["def_team"], row["period"], row["clock_start"])
        )

    return possessions.with_columns(
        pl.Series("off_players", off_list, dtype=pl.List(pl.Int64)),
        pl.Series("def_players", def_list, dtype=pl.List(pl.Int64)),
    )


def stint_minutes(stints: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id, player_id): total minutes from stint spans.

    Explodes each stint's 5-man unit and sums ``(start_clock -
    end_clock)`` across all stints that player appears in. Feeds the
    stint-minutes reconciliation sanity check below.
    """
    schema = {
        "game_id": pl.Utf8,
        "team_id": pl.Int64,
        "player_id": pl.Int64,
        "minutes": pl.Float64,
    }
    if stints.is_empty():
        return pl.DataFrame(schema=schema)
    exploded = (
        stints.with_columns((pl.col("start_clock") - pl.col("end_clock")).alias("seconds"))
        .explode("players", empty_as_null=True)
        .rename({"players": "player_id"})
    )
    return (
        exploded.group_by(["game_id", "team_id", "player_id"])
        .agg((pl.col("seconds").sum() / 60.0).alias("minutes"))
        .sort(["game_id", "team_id", "player_id"])
    )


#: Documented tolerance for the stint-minutes vs. box-score sanity check.
#: Generous because this heuristic's error compounds with every
#: unresolved/ambiguous substitution in a game (see module docstring,
#: "Known failure modes"); tightening this requires re-validating
#: against real ``player_game_stats``, which is DB access this agent
#: deliberately does not have (see task note: "do NOT open nba.duckdb").
#: The maintainer should re-tune this once run against real data.
MINUTES_RECONCILE_TOLERANCE = 3.0


def reconcile_stint_minutes(stints: pl.DataFrame, box_minutes: pl.DataFrame) -> pl.DataFrame:
    """Compare stint-derived minutes to box-score minutes per player-game.

    ``box_minutes`` needs columns ``game_id``, ``player_id``, ``minutes``
    (e.g. ``player_game_stats``). Returns ``game_id``, ``player_id``,
    ``stint_minutes``, ``box_minutes``, ``diff``, ``abs_diff``; flag rows
    where ``abs_diff`` exceeds ``MINUTES_RECONCILE_TOLERANCE``. Design is
    unit-tested on synthetic games (``tests/parse/test_lineups.py``) --
    not run against real ``player_game_stats`` by this agent.
    """
    computed = stint_minutes(stints).rename({"minutes": "stint_minutes"})
    out = (
        box_minutes.select(["game_id", "player_id", "minutes"])
        .rename({"minutes": "box_minutes"})
        .join(
            computed.select(["game_id", "player_id", "stint_minutes"]),
            on=["game_id", "player_id"],
            how="left",
        )
        .with_columns(pl.col("stint_minutes").fill_null(0.0))
    )
    return out.with_columns(
        (pl.col("stint_minutes") - pl.col("box_minutes")).alias("diff"),
    ).with_columns(pl.col("diff").abs().alias("abs_diff"))


def clean_lineup_rate(possessions: pl.DataFrame) -> float:
    """Fraction of rows with exactly 5 offensive AND 5 defensive players.

    Used by the validation summary ("% of possessions getting a clean
    5-5 lineup") -- see tests/parse/test_lineups.py.
    """
    if possessions.is_empty():
        return 1.0

    def _is_five(col: pl.Series) -> pl.Series:
        return col.is_not_null() & (col.list.len() == 5)

    clean = _is_five(possessions["off_players"]) & _is_five(possessions["def_players"])
    return float(clean.sum()) / possessions.height
