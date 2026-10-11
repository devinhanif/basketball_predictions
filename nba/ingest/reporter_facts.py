"""Claimed facts from reporter posts -> the fact store (``nba/facts``), and nothing else.

Each resolved, non-``unknown`` claim becomes one fact::

    subject    player:<id>
    predicate  claimed_<claim_type>          (claimed_minutes_restriction, claimed_out, ...)
    object     game:<id>                     the team's next game at/after the post (schedule)
    value      minutes_limit (or NULL)
    value_text the matched span (evidence)
    known_at   the post's created_at (naive UTC)
    source     x:<handle>
    confidence from the parser (< 1: it is a claim, not an observation)

Rules
-----
* This module writes to ``facts`` and ``entities`` in the facts DuckDB only. It refuses a
  database that holds any other table, so it can never be pointed at ``nba.duckdb``.
* A handle that is not allow-listed is refused (``HandleNotAllowed``) even on replay.
* Retweets are ignored (an allow-listed original arrives through its own feed).
* Unresolved player names are never written; they are reported with the raw name so Devin can
  add a reviewed alias. ``unknown`` claims are reported, never written.
* Claimed facts are shown in the briefing and reach no model input until a pre-registered rule
  says how (DECISIONS 2026-10-10, 2026-10-11; docs/REPORTER_FEED.md).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.daily.schedule import ScheduledGame, load_schedule_cache
from nba.facts.build import SCHEMA_PATH
from nba.ingest.reporter_parse import Claim, parse_post, resolve_claims, retweeted_handle
from nba.ingest.reporters import Allowlist, Post, Reporter
from nba.kalshi.aliases import load_reviewed_aliases
from nba.props.rosters import roster_names

PREDICATE_PREFIX = "claimed_"
#: A post attaches to a game that tips within this window after it ...
MAX_AHEAD = timedelta(hours=36)
#: ... or that tipped at most this long before it (a post during the game is about that game).
IN_PROGRESS = timedelta(hours=3)
FACTS_TABLES = {"entities", "facts"}


class FactsDbError(Exception):
    """The facts DB is locked, missing, or is not a facts DB."""


@dataclass(frozen=True)
class ClaimedFact:
    post: Post
    reporter: Reporter
    claim: Claim
    team_id: int | None
    game: ScheduledGame | None

    @property
    def predicate(self) -> str:
        return PREDICATE_PREFIX + self.claim.claim_type

    @property
    def writable(self) -> bool:
        return (
            self.claim.claim_type != "unknown"
            and self.claim.player_id is not None
            and self.game is not None
        )


# ----------------------------------------------------------------------------------------------
# Context: rosters, aliases, schedule (all read-only loads of reviewed/cached files)
# ----------------------------------------------------------------------------------------------


def season_for(at: datetime) -> str:
    """``2026-27`` for any date from Aug 2026 to Jul 2027."""
    y = at.year if at.month >= 8 else at.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def load_roster_index(roster_dir: Path) -> dict[int, list[tuple[str, int]]]:
    """``team_id -> [(display name, player_id)]`` from the newest ``<roster_dir>/<date>/`` cache."""
    import polars as pl

    days = sorted(d for d in roster_dir.glob("*") if d.is_dir()) if roster_dir.exists() else []
    if not days:
        return {}
    out: dict[int, list[tuple[str, int]]] = {}
    for f in sorted(days[-1].glob("*.parquet")):
        df = pl.read_parquet(f)
        if "team_id" not in df.columns or df.is_empty():
            continue
        out[int(df["team_id"][0])] = roster_names(df)
    return out


@dataclass(frozen=True)
class Context:
    allowlist: Allowlist
    rosters: dict[int, list[tuple[str, int]]]
    aliases: dict[str, int]
    schedule: list[ScheduledGame]

    @classmethod
    def load(
        cls,
        allowlist: Allowlist,
        *,
        rosters_dir: Path,
        schedule_cache: Path,
        aliases_path: Path,
        at: datetime,
    ) -> Context:
        sched = load_schedule_cache(season_for(at), schedule_cache) or []
        return cls(
            allowlist,
            load_roster_index(rosters_dir),
            load_reviewed_aliases(aliases_path),
            sched,
        )

    def team_of(self, player_id: int) -> int | None:
        hits = {t for t, names in self.rosters.items() if any(p == player_id for _, p in names)}
        return hits.pop() if len(hits) == 1 else None

    def player_name(self, player_id: int) -> str | None:
        for names in self.rosters.values():
            for n, p in names:
                if p == player_id:
                    return n
        return None


def next_game(
    schedule: list[ScheduledGame],
    team_id: int,
    at: datetime,
    *,
    max_ahead: timedelta = MAX_AHEAD,
    in_progress: timedelta = IN_PROGRESS,
) -> ScheduledGame | None:
    """The team's game in progress at ``at`` or its next one within ``max_ahead``; else None."""
    at = at.astimezone(UTC).replace(tzinfo=None) if at.tzinfo is not None else at
    cands = [
        g
        for g in schedule
        if team_id in (g.home_team, g.away_team)
        and g.tipoff + in_progress >= at
        and g.tipoff <= at + max_ahead
    ]
    return min(cands, key=lambda g: (g.tipoff, g.game_id)) if cands else None


# ----------------------------------------------------------------------------------------------
# Posts -> claimed facts
# ----------------------------------------------------------------------------------------------


def claims_for_posts(posts: list[Post], ctx: Context) -> tuple[list[ClaimedFact], Counter[str]]:
    """Parse and resolve every post. Raises ``HandleNotAllowed`` for a handle off the list."""
    out: list[ClaimedFact] = []
    counts: Counter[str] = Counter()
    for post in posts:
        rep = ctx.allowlist.require(post.handle)
        if retweeted_handle(post.text):
            counts["retweet_ignored"] += 1
            continue
        roster = ctx.rosters.get(rep.team_id) if rep.team_id is not None else None
        for claim in resolve_claims(parse_post(post.text), roster, ctx.aliases):
            team_id = rep.team_id
            if team_id is None and claim.player_id is not None:
                team_id = ctx.team_of(claim.player_id)
            game = None
            if team_id is not None and claim.claim_type != "unknown":
                game = next_game(ctx.schedule, team_id, post.created_at)
            cf = ClaimedFact(post, rep, claim, team_id, game)
            if claim.claim_type == "unknown":
                counts["unknown"] += 1
            elif claim.player_id is None:
                counts["unresolved_player"] += 1
            elif game is None:
                counts["no_game_in_window"] += 1
            else:
                counts["claims"] += 1
            out.append(cf)
    return out, counts


# ----------------------------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------------------------


def open_facts_db(path: Path) -> duckdb.DuckDBPyConnection:
    """Open (or create, with the facts schema) ``path``. Refuses a DB with foreign tables."""
    try:
        con = duckdb.connect(str(path))
    except duckdb.IOException as exc:
        raise FactsDbError(f"LOCKED or unreadable: {exc}") from exc
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if not tables:
        con.execute(SCHEMA_PATH.read_text())
    elif not tables <= FACTS_TABLES:
        con.close()
        raise FactsDbError(
            f"{path} holds tables {sorted(tables - FACTS_TABLES)}; this writer only touches a "
            "facts store (entities, facts)"
        )
    return con


@dataclass
class WriteResult:
    written: int = 0
    duplicates: int = 0
    entities_added: int = 0


def write_claimed_facts(
    con: duckdb.DuckDBPyConnection,
    claimed: list[ClaimedFact],
    *,
    ctx: Context | None = None,
) -> WriteResult:
    """Insert writable claims as facts (idempotent on subject, predicate, object, source,
    known_at). Adds a bare ``player:<id>`` entity when the store has none."""
    res = WriteResult()
    rows = [c for c in claimed if c.writable]
    if not rows:
        return res
    next_id = con.execute("SELECT coalesce(max(fact_id), 0) + 1 FROM facts").fetchone()
    fact_id = int(next_id[0]) if next_id else 1
    for c in rows:
        assert c.game is not None and c.claim.player_id is not None  # writable
        subject = f"player:{c.claim.player_id}"
        obj = f"game:{c.game.game_id}"
        known_at = c.post.created_at
        dup = con.execute(
            "SELECT 1 FROM facts WHERE subject = ? AND predicate = ? AND object = ? "
            "AND source = ? AND known_at = ? LIMIT 1",
            [subject, c.predicate, obj, c.reporter.source, known_at],
        ).fetchone()
        if dup:
            res.duplicates += 1
            continue
        if not con.execute("SELECT 1 FROM entities WHERE entity_id = ?", [subject]).fetchone():
            name = ctx.player_name(c.claim.player_id) if ctx else None
            con.execute(
                "INSERT INTO entities VALUES (?, 'player', ?, NULL)",
                [subject, name or c.claim.player_name],
            )
            res.entities_added += 1
        params: list[Any] = [
            fact_id,
            subject,
            c.predicate,
            obj,
            float(c.claim.minutes_limit) if c.claim.minutes_limit is not None else None,
            c.claim.evidence,
            known_at,
            None,
            None,
            c.reporter.source,
            float(c.claim.confidence),
        ]
        con.execute("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", params)
        fact_id += 1
        res.written += 1
    return res


def format_report(
    claimed: list[ClaimedFact], counts: Counter[str], written: WriteResult | None
) -> str:
    """Plain text for the terminal / briefing: one line per claim, evidence quoted."""
    lines = [
        f"claims: {counts.get('claims', 0)}  unresolved: {counts.get('unresolved_player', 0)}  "
        f"no game in window: {counts.get('no_game_in_window', 0)}  unknown: "
        f"{counts.get('unknown', 0)}  retweets ignored: {counts.get('retweet_ignored', 0)}"
    ]
    if written is not None:
        lines.append(
            f"facts written: {written.written}  duplicates skipped: {written.duplicates}  "
            f"entities added: {written.entities_added}"
        )
    for c in claimed:
        if c.claim.claim_type == "unknown":
            tag = "unknown"
        elif c.claim.player_id is None:
            tag = f"UNRESOLVED {c.claim.player_name!r}"
        elif c.game is None:
            tag = "no game in window"
        else:
            tag = f"{c.predicate} player:{c.claim.player_id} game:{c.game.game_id}"
        lim = f" limit={c.claim.minutes_limit}" if c.claim.minutes_limit is not None else ""
        lines.append(
            f"  {c.post.created_at:%Y-%m-%d %H:%M}Z @{c.reporter.handle} {tag}{lim} "
            f"conf={c.claim.confidence:.1f} | {c.claim.evidence!r}"
        )
    return "\n".join(lines)
