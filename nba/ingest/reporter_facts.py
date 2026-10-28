"""Claimed facts from reporter posts -> the fact store (``nba/facts``), and nothing else.

Each resolved, non-``unknown`` claim becomes one fact::

    subject    player:<id>
    predicate  claimed_<claim_type>          (claimed_minutes_restriction, claimed_out, ...)
    object     game:<id>                     the team's next game at/after the post (schedule)
    value      minutes_limit (or NULL)
    value_text the matched span (evidence)
    known_at   OUR first-read time of the post (``first_seen_at``, naive UTC), never its created_at
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

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.daily.schedule import ScheduledGame, load_schedule_cache
from nba.facts.build import SCHEMA_PATH
from nba.ingest import reporter_pretip as pretip
from nba.ingest.reporter_parse import (
    Claim,
    parse_post,
    resolve_claims,
    resolve_player,
    retweeted_handle,
)
from nba.ingest.reporter_store import BACKFILL, LIVE, StoreFact, value_text
from nba.ingest.reporters import Allowlist, Post, Reporter
from nba.kalshi.aliases import load_reviewed_aliases
from nba.parse.availability import normalize_name
from nba.props.rosters import roster_names

log = logging.getLogger("nba.reporter_facts")
ET = ZoneInfo("America/New_York")
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


def load_nicknames(path: Path) -> dict[str, str]:
    """``normalised nickname -> full name`` from the reviewed file; a missing file is empty."""
    import yaml

    if not path.exists():
        return {}
    raw = (yaml.safe_load(path.read_text()) or {}).get("nicknames") or {}
    return {normalize_name(str(k)): str(v) for k, v in raw.items()}


@dataclass(frozen=True)
class Context:
    allowlist: Allowlist
    rosters: dict[int, list[tuple[str, int]]]
    aliases: dict[str, int]
    schedule: list[ScheduledGame]
    nicknames: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        allowlist: Allowlist,
        *,
        rosters_dir: Path,
        schedule_cache: Path,
        aliases_path: Path,
        at: datetime,
        nicknames_path: Path | None = None,
    ) -> Context:
        sched = load_schedule_cache(season_for(at), schedule_cache) or []
        return cls(
            allowlist,
            load_roster_index(rosters_dir),
            load_reviewed_aliases(aliases_path),
            sched,
            load_nicknames(nicknames_path) if nicknames_path else {},
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
        scope: list[list[tuple[str, int]]] = []
        if rep.team_id is not None:
            tonight = next_game(ctx.schedule, rep.team_id, post.created_at)
            if tonight is not None:
                opp = tonight.away_team if tonight.home_team == rep.team_id else tonight.home_team
                scope = [ctx.rosters[opp]] if opp in ctx.rosters else []
        parsed = parse_post(post.text)
        for claim in resolve_claims(parsed, roster, ctx.aliases, ctx.nicknames, scope):
            team_id = rep.team_id
            if claim.player_id is not None and (team_id is None or claim.claim_type == "starting"):
                team_id = ctx.team_of(claim.player_id) or team_id
            game = None
            if team_id is not None and claim.claim_type != "unknown":
                game = next_game(ctx.schedule, team_id, post.created_at)
            cf = ClaimedFact(post, rep, claim, team_id, game)
            if claim.claim_type == "unknown":
                counts["unknown"] += 1
            elif claim.claim_type == "starting" and claim.group_status != "full":
                counts["lineup_not_full"] += 1
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
    first_seen: Mapping[str, tuple[datetime, str]] | None = None,
) -> WriteResult:
    """Insert writable claims as facts into a facts-schema DB (idempotent on subject, predicate,
    object, source, known_at). Adds a bare ``player:<id>`` entity when the store has none.

    ``known_at`` is the post's first-read time: ``first_seen[post_id]`` (from
    :func:`nba.ingest.reporter_store.first_seen_index`) else the raw file's ``fetched_at`` (never
    earlier than the true first read), and NEVER ``created_at``. This writer targets a facts DB that
    ``nba.facts build`` recreates; the live path writes the durable
    ``reporter_facts.duckdb`` through :func:`claimed_store_facts` instead."""
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
        known_at = first_seen_at_of(c.post, first_seen)
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
        f"{counts.get('unknown', 0)}  retweets ignored: {counts.get('retweet_ignored', 0)}  "
        f"lineup names not in a full 5: {counts.get('lineup_not_full', 0)}"
    ]
    if written is not None:
        lines.append(
            f"facts written: {written.written}  duplicates skipped: {written.duplicates}  "
            f"entities added: {written.entities_added}"
        )
    for c in claimed:
        if c.claim.claim_type == "unknown":
            tag = "unknown"
        elif c.claim.claim_type == "starting" and c.claim.group_status != "full":
            tag = f"LINEUP {c.claim.group_status} {c.claim.player_name!r}"
        elif c.claim.player_id is None:
            tag = f"UNRESOLVED {c.claim.player_name!r}"
        elif c.game is None:
            tag = f"no game in window ({c.claim.player_name!r})"
        else:
            tag = f"{c.predicate} player:{c.claim.player_id} game:{c.game.game_id}"
        lim = f" limit={c.claim.minutes_limit}" if c.claim.minutes_limit is not None else ""
        lines.append(
            f"  {c.post.created_at:%Y-%m-%d %H:%M}Z @{c.reporter.handle} {tag}{lim} "
            f"conf={c.claim.confidence:.1f} | {c.claim.evidence!r}"
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# The durable store (nba.ingest.reporter_store): known_at = first_seen_at
# ----------------------------------------------------------------------------------------------


def first_seen_at_of(post: Post, first_seen: Mapping[str, tuple[datetime, str]] | None) -> datetime:
    """Our first read of ``post``: the earliest raw capture, else this file's ``fetched_at``."""
    got = (first_seen or {}).get(post.post_id)
    return got[0] if got is not None else post.fetched_at


def capture_mode_of_post(post: Post, first_seen: Mapping[str, tuple[datetime, str]] | None) -> str:
    """The capture mode of the earliest raw file holding the post. A post that is in no indexed raw
    file is NOT known to have been read live, so it defaults to backfill (never eligible)."""
    got = (first_seen or {}).get(post.post_id)
    return got[1] if got is not None else BACKFILL


def claimed_store_facts(
    claimed: list[ClaimedFact], first_seen: Mapping[str, tuple[datetime, str]] | None = None
) -> list[StoreFact]:
    """The briefing's ``claimed_*`` facts as store rows (known_at = first_seen_at). Only posts the
    raw index says were read live are written. The store keeps the post id and the evidence hash,
    never the post text (X terms: stored content must follow deletions; the text stays in raw)."""
    out: list[StoreFact] = []
    for c in claimed:
        if not c.writable:
            continue
        assert c.game is not None and c.claim.player_id is not None
        seen = first_seen_at_of(c.post, first_seen)
        if capture_mode_of_post(c.post, first_seen) != LIVE or c.post.created_at > seen:
            continue
        out.append(
            StoreFact(
                f"player:{c.claim.player_id}",
                c.predicate,
                f"game:{c.game.game_id}",
                float(c.claim.minutes_limit) if c.claim.minutes_limit is not None else None,
                value_text(
                    post_id=c.post.post_id,
                    evidence_sha256=hashlib.sha256(
                        " ".join(c.claim.evidence.split()).encode()
                    ).hexdigest(),
                ),
                seen,
                c.reporter.source,
                float(c.claim.confidence),
                c.post.post_id,
                c.post.created_at,
                seen,
                capture_mode_of_post(c.post, first_seen),
                "claimed-v1",
            )
        )
    return out


def team_game_around(
    schedule: list[ScheduledGame], team_id: int, at: datetime
) -> tuple[ScheduledGame | None, ScheduledGame | None]:
    """``(previous, next)``: the team's latest game that tipped at/before ``at`` and its first game
    tipping after ``at`` within :data:`MAX_AHEAD`."""
    mine = [g for g in schedule if team_id in (g.home_team, g.away_team)]
    prev = max(
        (g for g in mine if g.tipoff <= at), key=lambda g: (g.tipoff, g.game_id), default=None
    )
    nxt = min(
        (g for g in mine if at < g.tipoff <= at + MAX_AHEAD),
        key=lambda g: (g.tipoff, g.game_id),
        default=None,
    )
    return prev, nxt


@dataclass(frozen=True)
class _Resolved:
    player_id: int
    team_id: int
    game: ScheduledGame | None
    reject: str | None  # f4_in_progress | f4_prev_game | no_game_in_window | None


def pretip_facts_for_posts(
    posts: list[Post],
    ctx: Context,
    first_seen: Mapping[str, tuple[datetime, str]] | None = None,
) -> tuple[list[StoreFact], list[tuple[str, str | None, str, str]], Counter[str]]:
    """Posts -> ``reporter_minutes_cap`` / ``reporter_status`` store rows (filters F1-F8).

    F4 lives here: a post is dropped when ``created_at`` is after ``first_seen_at`` (a clock fault),
    when it was written while the team's previous game was on or within 3 h after its tip, or when
    its game has already tipped. Only posts the raw index says were read live are written: a
    backfill post, and a post in no indexed raw file (``first_seen`` missing), are counted and never
    written. Several claims about one player in one post are merged to one row per predicate.
    Returns ``(facts, rejects, counts)``; ``rejects`` are ``(post_id, handle, reason, detail)``.
    """
    facts: list[StoreFact] = []
    rejects: list[tuple[str, str | None, str, str]] = []
    counts: Counter[str] = Counter()
    for post in posts:
        rep = ctx.allowlist.require(post.handle)
        if retweeted_handle(post.text):
            counts["retweet_ignored"] += 1
            continue
        seen = first_seen_at_of(post, first_seen)
        mode = capture_mode_of_post(post, first_seen)
        if mode != LIVE:
            counts["backfill_ineligible"] += 1
            continue
        if post.created_at > seen:
            counts["clock_fault"] += 1
            detail = f"created_at {post.created_at.isoformat()} > first_seen {seen.isoformat()}"
            rejects.append((post.post_id, post.handle, "clock_fault", detail))
            log.warning("reporter post %s: clock fault, %s", post.post_id, detail)
            continue
        roster = ctx.rosters.get(rep.team_id) if rep.team_id is not None else None
        scope: list[list[tuple[str, int]]] = []
        opp_id: int | None = None
        if rep.team_id is not None:
            _, nxt = team_game_around(ctx.schedule, rep.team_id, post.created_at)
            if nxt is not None:
                opp = nxt.away_team if nxt.home_team == rep.team_id else nxt.home_team
                scope = [ctx.rosters[opp]] if opp in ctx.rosters else []
                opp_id = opp if scope else None
        cache: dict[str, _Resolved | None] = {}

        def resolve(
            name: str,
            rep: Reporter = rep,
            post: Post = post,
            roster: list[tuple[str, int]] | None = roster,
            scope: list[list[tuple[str, int]]] = scope,
            opp_id: int | None = opp_id,
            cache: dict[str, _Resolved | None] = cache,
        ) -> _Resolved | None:
            if name in cache:
                return cache[name]
            pid = resolve_player(name, roster, ctx.aliases, ctx.nicknames)
            team_of_pid = rep.team_id
            if pid is None and scope and roster is not None:
                hits = {resolve_player(name, r, ctx.aliases, ctx.nicknames) for r in scope}
                hits.discard(None)
                pid = hits.pop() if len(hits) == 1 else None
                team_of_pid = (
                    opp_id  # found on the opponent's roster: his team is not the reporter's
                )
            res: _Resolved | None = None
            if pid is not None:
                team_id = team_of_pid if rep.team_id is not None else ctx.team_of(pid)
                if team_id is None:
                    res = None
                else:
                    prev, nxt = team_game_around(ctx.schedule, team_id, post.created_at)
                    reject = None
                    if nxt is None:
                        reject = "no_game_in_window"
                    elif prev is not None and post.created_at <= prev.tipoff + IN_PROGRESS:
                        reject = "f4_prev_game"
                    res = _Resolved(pid, team_id, nxt, reject)
            cache[name] = res
            return res

        def game_date_of(
            name: str, resolve: Callable[[str], _Resolved | None] = resolve
        ) -> date | None:
            r = resolve(name)
            return r.game.game_date_et if r is not None and r.game is not None else None

        posted = post.created_at.replace(tzinfo=UTC).astimezone(ET).date()
        for c in pretip.parse_pretip(post.text, game_date_et=game_date_of, posted_date_et=posted):
            counts["claims_seen"] += 1
            res = resolve(c.player_name)
            if res is None:
                counts["unresolved_player"] += 1
                continue
            if not c.kept:
                counts[f"drop_{c.reject}"] += 1
                continue
            if res.reject is not None or res.game is None:
                counts[f"drop_{res.reject}"] += 1
                continue
            counts["facts"] += 1
            if c.hedged:
                counts["hedged"] += 1
            facts.append(
                StoreFact(
                    f"player:{res.player_id}",
                    c.predicate,
                    f"game:{res.game.game_id}",
                    float(c.n) if c.kind == "cap" and c.n is not None else None,
                    value_text(
                        post_id=post.post_id,
                        platform="x",
                        handle=post.handle,
                        created_at=post.created_at.isoformat(),
                        first_seen_at=seen.isoformat(),
                        parser_version=pretip.PARSER_VERSION,
                        evidence_sha256=c.evidence_sha256,
                        status=c.status,
                        hedged=c.hedged,
                        multi_game=c.multi_game,
                        n_lo=c.n_lo,
                        n_hi=c.n_hi,
                        capture_mode=mode,
                    ),
                    seen,
                    rep.source,
                    (0.8 if c.kind == "cap" else 0.7) - (0.2 if c.hedged else 0.0),
                    post.post_id,
                    post.created_at,
                    seen,
                    mode,
                    pretip.PARSER_VERSION,
                )
            )
    return _merge_per_post_player(facts, counts), rejects, counts


_STATUS_RANK = {"out": 0, "game_time_decision": 1, "will_play": 2, "available": 2, "cap_lifted": 3}


def _merge_per_post_player(facts: list[StoreFact], counts: Counter[str]) -> list[StoreFact]:
    """One row per (post, player, predicate, game): the store's key has room for one. A numbered,
    unhedged cap beats an unnumbered or hedged one in the same post; among statuses an unhedged one
    beats a hedged one, then out > game_time_decision > will_play/available > cap_lifted. Ties keep
    the first in sentence order. (Adversary review M4: the second claim used to be deduped away
    blind, losing a numbered cap behind an unnumbered one.)"""
    best: dict[tuple[str, str, str, str, str], tuple[tuple[int, int, int], StoreFact]] = {}
    for f in facts:
        meta = json.loads(f.value_text or "{}")
        rank = (
            1 if meta.get("hedged") else 0,
            0 if f.value is not None else 1,
            _STATUS_RANK.get(str(meta.get("status")), 9) if f.predicate == "reporter_status" else 0,
        )
        key = (f.subject, f.predicate, f.object, f.source, f.post_id)
        cur = best.get(key)
        if cur is None:
            best[key] = (rank, f)
        else:
            counts["merged_same_post_player"] += 1
            if rank < cur[0]:
                best[key] = (rank, f)
    return [f for _, f in best.values()]
