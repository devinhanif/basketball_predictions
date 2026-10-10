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
("validate hard"). "Exactly five on the floor" is true by construction and
says nothing about *identity*; the validation that matters is stint minutes
vs. box minutes (``minutes_within_share``) and the per-period opening-five
invariant below.

## Design (v2, 2026-10-09; see docs/reviews/stint_reconciliation_2026-10-09.md)

v1 carried the previous period's closing five into the next period and
"corrected" it by evicting the first unconfirmed player whenever someone
unseen acted. That guess was wrong for ~80% of Q2/Q3/Q4 team-periods and
credited the wrong player for the first 1.5-2 minutes. v2:

1. **Period openers by look-ahead.** Q1 is seeded from the box-score
   starters. For every later period the opening five is the first five
   players whose *first appearance in that period* is an action (shot,
   rebound, turnover, foul, free throw, violation, jump ball) or the *out*
   side of a substitution, i.e. before they were subbed in. Technical /
   delay fouls and technical free throws are ignored as evidence (a bench
   player can draw them). If fewer than five are found, the remainder is
   filled from the previous closing five (skipping players already seen) and
   the team-period is flagged ``open_filled`` in the ``LineupReport``.
2. **No eviction heuristic.** Mid-period the five changes only through
   substitutions. An actor who is not on the tracked floor is counted
   (``actor_off_court``) and surfaced in the report, never "fixed" silently.
3. **Substitution name resolution.** Sub text is ``SUB: <in> FOR <out>``; the
   event's ``player_id`` is the *out* player (exact). The *in* name is last
   name only, or ``"A. Holiday"`` / ``"Jal. Green"`` when two teammates share
   a surname. Resolution ladder (``_NameBook.resolve``): (a) initial+surname
   learned game-wide from the out side of other subs (whose id is exact);
   (b) unique surname among the team's PBP-named players; (c) among several,
   the one not currently on the floor; (d) roster elimination: box-score
   players who never appear by name in the PBP (they never act) are the pool
   for names that match nobody, narrowed by who is not on court. Anything
   still ambiguous is skipped and recorded in ``LineupReport.unresolved``
   (fail loud in the report, never silent).

### Remaining known failure modes
- Both same-surname teammates off the floor with no initial evidence.
- Sub whose *out* player is not on the tracked floor (desync from an earlier
  unresolved sub): skipped and logged in ``LineupReport.desync``.
- Events are placed by (period, clock), not ``action_number``: the feed appends
  post-hoc corrections at the end of the game (see ``nba.parse.ordering.in_game_order``). A
  correction with a wrong clock would still land in the wrong place.
- Team-level events (``team_id == 0``) and the unresolved side of a jump
  ball carry no lineup signal.
- Ejection / disqualification edge cases get no special handling.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from nba.parse.ordering import CLOCK_RE as _CLOCK_RE
from nba.parse.ordering import in_game_order

STINTS_COLUMNS = ["game_id", "team_id", "period", "start_clock", "end_clock", "players"]

#: action_type values whose player_id, when team_id != 0, shows that
#: player is on the court right now.
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

_SUB_RE = re.compile(r"^SUB:\s*(.+?)\s+FOR\s+(.+)$")
#: ``"A. Holiday"`` / ``"Jal. Green"``: 1-3 letter prefix (capital first) then a dot.
_INITIAL_RE = re.compile(r"^([A-Z][A-Za-z]{0,2})\.\s+(.+)$")


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

    Substitution free text doesn't always match ``player_name`` byte-for-byte
    (diacritics dropped, ``Jr.`` dropped).
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    for suffix in _NAME_SUFFIXES:
        if folded.endswith(suffix):
            folded = folded[: -len(suffix)]
            break
    return folded.strip().lower()


def _fold(name: str) -> str:
    """Diacritic-folded lowercase name, suffix kept (``"jackson jr."`` stays distinct)."""
    folded = unicodedata.normalize("NFKD", name)
    return "".join(c for c in folded if not unicodedata.combining(c)).strip().lower()


def _split_initial(name: str) -> tuple[str | None, str]:
    """``"A. Holiday"`` -> ``("a", "holiday")``; ``"Holiday"`` -> ``(None, "holiday")``."""
    m = _INITIAL_RE.match(name.strip())
    if m:
        return m.group(1).lower(), _normalize_name(m.group(2))
    return None, _normalize_name(name)


@dataclass
class LineupReport:
    """Everything the tracker could not settle silently, for one game.

    ``unresolved``: substitutions whose *in* name could not be tied to one
    player (skipped). ``desync``: substitutions skipped because the tracked
    floor contradicted them. ``open_filled``: (team, period) look-aheads that
    found fewer than five openers. ``q1_mismatch``: teams whose Q1 look-ahead
    openers differ from the box starters. ``actor_off_court``: count of
    in-game actions by a player the tracker believes is on the bench.
    ``openings``: (team, period) -> opening five used.
    """

    game_id: str = ""
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    desync: list[dict[str, Any]] = field(default_factory=list)
    open_filled: list[tuple[int, int]] = field(default_factory=list)
    q1_mismatch: list[int] = field(default_factory=list)
    actor_off_court: int = 0
    openings: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not (self.unresolved or self.desync or self.open_filled or self.q1_mismatch)


class _NameBook:
    """Per-game name -> player_id resolver for substitution text (see module docstring)."""

    def __init__(self, pbp: pl.DataFrame, roster_by_team: dict[int, list[int]] | None) -> None:
        self.by_surname: dict[int, dict[str, set[int]]] = {}
        self.by_full: dict[int, dict[str, set[int]]] = {}
        self.named: dict[int, set[int]] = {}
        self.initial_ids: dict[tuple[int, str, str], int] = {}
        self.initial_of: dict[tuple[int, int], str] = {}
        self.unnamed_assign: dict[tuple[int, str], int] = {}
        named_rows = (
            pbp.filter((pl.col("player_id") != 0) & (pl.col("player_name") != ""))
            .select(["team_id", "player_name", "player_id"])
            .unique()
        )
        for team_id, name, pid in named_rows.iter_rows():
            sur = _normalize_name(name)
            self.by_surname.setdefault(team_id, {}).setdefault(sur, set()).add(pid)
            self.by_full.setdefault(team_id, {}).setdefault(_fold(name), set()).add(pid)
            self.named.setdefault(team_id, set()).add(pid)
        # Initial+surname -> id from the exact (player_id) out side of subs.
        for team_id, pid, desc in (
            pbp.filter(pl.col("action_type") == "Substitution")
            .select(["team_id", "player_id", "description"])
            .iter_rows()
        ):
            m = _SUB_RE.match((desc or "").strip())
            if not m or not pid:
                continue
            initial, sur = _split_initial(m.group(2))
            if initial is not None:
                self.initial_ids[(team_id, initial, sur)] = pid
                self.initial_of[(team_id, pid)] = initial
        self.unnamed: dict[int, set[int]] = {
            t: {p for p in ids if p not in self.named.get(t, set())}
            for t, ids in (roster_by_team or {}).items()
        }

    def resolve(self, team: int, name: str, on_court: set[int]) -> int | None:
        initial, sur = _split_initial(name)
        if initial is not None:
            hit = self.initial_ids.get((team, initial, sur))
            if hit is not None:
                return hit
        cands = set(self.by_surname.get(team, {}).get(sur, set()))
        if initial is not None:
            cands = {c for c in cands if self.initial_of.get((team, c), initial) == initial}
        if len(cands) > 1:
            # Same surname after suffix stripping ("Jackson" vs "Jackson Jr."): the
            # suffix-preserving name narrows it when it hits.
            rest = _INITIAL_RE.match(name.strip())
            full = self.by_full.get(team, {}).get(_fold(rest.group(2) if rest else name), set())
            narrowed = cands & full
            if narrowed:
                cands = narrowed
        if initial is None and len(cands) == 1:
            return next(iter(cands))
        off = cands - on_court
        if len(off) == 1:
            return next(iter(off))
        if len(off) > 1:
            return None
        # No bench candidate by name: players who never appear by name in the
        # PBP are the elimination pool.
        key = (team, f"{initial or ''}.{sur}")
        if key in self.unnamed_assign:
            return self.unnamed_assign[key]
        claimed = {v for (t, _), v in self.unnamed_assign.items() if t == team}
        pool = self.unnamed.get(team, set()) - on_court - claimed
        if len(pool) == 1:
            pid = next(iter(pool))
            self.unnamed_assign[key] = pid
            return pid
        return None


def _is_evidence(ev: dict[str, Any]) -> bool:
    """True if this event shows ``ev['player_id']`` is on the floor right now."""
    if ev["action_type"] not in _CONFIRM_ACTION_TYPES or not ev["player_id"]:
        return False
    sub_type = ev["sub_type"] or ""
    # Technical / delay / ejection events can be drawn by bench players.
    return not ("Technical" in sub_type or sub_type == "Delay Of Game")


def _look_ahead_openers(
    events: list[dict[str, Any]], team: int, book: _NameBook
) -> tuple[list[int], set[int]]:
    """First five players seen in this period before being subbed in, in order."""
    openers: list[int] = []
    seen: set[int] = set()
    present: set[int] = set()
    for ev in events:
        if ev["team_id"] != team:
            continue
        if ev["action_type"] == "Substitution":
            out_id = ev["player_id"]
            m = _SUB_RE.match((ev["description"] or "").strip())
            if out_id and out_id not in seen:
                openers.append(out_id)
                seen.add(out_id)
            present.discard(out_id)
            if m:
                in_id = book.resolve(team, m.group(1).strip(), present)
                if in_id is not None:
                    seen.add(in_id)
                    present.add(in_id)
        elif _is_evidence(ev):
            pid = ev["player_id"]
            if pid not in seen:
                openers.append(pid)
                seen.add(pid)
            present.add(pid)
        if len(openers) >= 5:
            break
    return openers[:5], seen


def _empty_stints() -> pl.DataFrame:
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


def _stint_row(
    game_id: str, team: int, period: int, start: float, end: float, players: list[int]
) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "team_id": team,
        "period": period,
        "start_clock": start,
        "end_clock": end,
        "players": sorted(players),
    }


def track_lineups_with_report(
    pbp: pl.DataFrame,
    starters_by_team: dict[int, list[int]],
    roster_by_team: dict[int, list[int]] | None = None,
) -> tuple[pl.DataFrame, LineupReport]:
    """Track each team's on-court five through one game; also return what was unsure.

    ``pbp`` is a single game's V3-schema frame. ``starters_by_team`` maps
    team_id -> 5 player_ids (Q1 seed; ``player_game_stats.starter`` in the real
    pipeline). ``roster_by_team`` (optional) lists box-score players who played,
    used to resolve substitution names for players who never act in the PBP.
    Returns ``(stints, report)``; see the module docstring.
    """
    report = LineupReport()
    if pbp.is_empty():
        return _empty_stints(), report

    game_ids = pbp["game_id"].unique().to_list()
    if len(game_ids) != 1:
        raise ValueError(f"track_lineups expects a single game_id, got {game_ids}")
    game_id = game_ids[0]
    report.game_id = game_id

    for team_id, roster in starters_by_team.items():
        if len(set(roster)) != 5:
            raise ValueError(f"starters_by_team[{team_id}] must have exactly 5 unique players")

    pbp = in_game_order(pbp)
    book = _NameBook(pbp, roster_by_team)
    teams = sorted(starters_by_team.keys())
    rows = list(pbp.iter_rows(named=True))
    periods = sorted({r["period"] for r in rows})

    stint_rows: list[dict[str, Any]] = []
    prev_close: dict[int, list[int]] = {t: list(starters_by_team[t]) for t in teams}

    for period in periods:
        events = [r for r in rows if r["period"] == period]
        players: dict[int, list[int]] = {}
        start_clock: dict[int, float] = {}
        for t in teams:
            openers, seen = _look_ahead_openers(events, t, book)
            if period == 1:
                if set(openers) != set(starters_by_team[t]) and len(openers) == 5:
                    report.q1_mismatch.append(t)
                five = list(starters_by_team[t])
            elif len(openers) == 5:
                five = openers
            else:
                five = openers + [p for p in prev_close[t] if p not in seen]
                five = five[:5]
                report.open_filled.append((t, period))
                if len(five) < 5:
                    five = list(prev_close[t])
            players[t] = five
            start_clock[t] = _period_start_clock(period)
            report.openings[(t, period)] = sorted(five)

        for ev in events:
            team_id = ev["team_id"]
            if team_id not in players:
                continue
            clock_s = _clock_to_seconds(ev["clock"])
            if ev["action_type"] == "Substitution":
                m = _SUB_RE.match((ev["description"] or "").strip())
                if not m:
                    continue
                on_court = set(players[team_id])
                in_id = book.resolve(team_id, m.group(1).strip(), on_court)
                out_id = ev["player_id"]
                where = {
                    "team_id": team_id,
                    "period": period,
                    "clock": clock_s,
                    "in_name": m.group(1).strip(),
                    "out_id": out_id,
                }
                if in_id is None:
                    report.unresolved.append(where)
                    continue
                if in_id in on_court:
                    report.desync.append({**where, "in_id": in_id, "reason": "in_on_court"})
                    continue
                if out_id not in on_court:
                    report.desync.append({**where, "in_id": in_id, "reason": "out_off_court"})
                    continue
                stint_rows.append(
                    _stint_row(
                        game_id, team_id, period, start_clock[team_id], clock_s, players[team_id]
                    )
                )
                players[team_id] = [p for p in players[team_id] if p != out_id] + [in_id]
                start_clock[team_id] = clock_s
            elif _is_evidence(ev) and ev["player_id"] not in players[team_id]:
                report.actor_off_court += 1

        for t in teams:
            stint_rows.append(_stint_row(game_id, t, period, start_clock[t], 0.0, players[t]))
            prev_close[t] = list(players[t])

    if not stint_rows:
        return _empty_stints(), report
    return pl.DataFrame(stint_rows).select(STINTS_COLUMNS), report


def track_lineups(
    pbp: pl.DataFrame,
    starters_by_team: dict[int, list[int]],
    roster_by_team: dict[int, list[int]] | None = None,
) -> pl.DataFrame:
    """``track_lineups_with_report`` without the report (stints only)."""
    return track_lineups_with_report(pbp, starters_by_team, roster_by_team)[0]


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


#: Reconciliation bar for the stint-minutes gate: |stint - box| <= 1.0 minute.
MINUTES_WITHIN_TOL = 1.0
#: Target / floor for the share of player-games within ``MINUTES_WITHIN_TOL``.
MINUTES_SHARE_TARGET = 0.95
MINUTES_SHARE_FLOOR = 0.91


def minutes_within_share(
    stints: pl.DataFrame, box_minutes: pl.DataFrame, tol: float = MINUTES_WITHIN_TOL
) -> tuple[float, int]:
    """Share of player-games (box minutes > 0) whose stint minutes are within ``tol`` of box.

    Returns ``(share, n_player_games)``. This is the identity-sensitive
    reconciliation metric for the tracker (``clean_lineup_rate`` only checks
    set size). ``box_minutes`` needs ``game_id``, ``player_id``, ``minutes``.
    """
    rec = reconcile_stint_minutes(stints, box_minutes).filter(pl.col("box_minutes") > 0)
    if rec.is_empty():
        return float("nan"), 0
    return float((rec["abs_diff"] <= tol).mean()), rec.height  # type: ignore[arg-type]
