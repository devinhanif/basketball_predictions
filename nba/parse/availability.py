"""Parse raw availability snapshots (nba_api inactive lists + manually
curated injury-report announcements) into ``player_availability`` rows.

Two sources, two shapes:

- ``nba_inactive_list``: nba_api's ``BoxScoreSummaryV2`` InactivePlayers
  result set already carries ``PLAYER_ID`` directly -- no name matching
  needed, see ``normalize_inactive_frame``.
- ``manual_announced``: externally curated injury-report snapshots have
  only player names (and a team), never a stable ``player_id`` -- these
  MUST be resolved via ``resolve_player_id``, which fails loudly
  (``UnmatchedPlayerNameError``) on anything that isn't a confident match,
  mirroring the Kalshi name-matching discipline required by CLAUDE.md
  ("fail loudly on unmatched names").

No nba_api import here -- this module is pure parsing/matching logic, safe
to import and unit-test with no network access.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
import warnings
from datetime import date, datetime
from typing import cast

import duckdb
import polars as pl

from nba.ingest.teams import TEAM_ABBREV_TO_ID

#: Columns of the player_availability table this module produces.
AVAILABILITY_SCHEMA = [
    "player_id",
    "as_of",
    "game_id",
    "status",
    "reason",
    "source",
    "pulled_at",
]

_VALID_STATUSES = {"out", "doubtful", "questionable", "probable", "available", "inactive"}

#: Suffixes stripped during name normalization (nba_api / injury reports are
#: inconsistent about including these).
_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")

#: Below this ``difflib`` similarity ratio, a fuzzy match is not trusted and
#: the name is treated as unmatched (fail loudly rather than guess wrong).
_FUZZY_MATCH_CUTOFF = 0.84


class UnmatchedPlayerNameError(ValueError):
    """Raised when a name in a manual availability snapshot can't be resolved.

    Deliberately loud (never silently dropped/guessed) -- the same
    discipline CLAUDE.md requires for Kalshi player-name matching, because a
    wrong silent match would attach an injury status to the wrong player.
    """


def normalize_name(raw: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes, collapse whitespace.

    ``"Jokić, Nikola Jr."`` and ``"nikola jokic"`` both normalize to
    ``"nikola jokic"``.
    """
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace(",", " ")
    s = _NON_ALNUM_RE.sub(" ", s)
    s = " ".join(s.split())
    s = _SUFFIX_RE.sub("", s).strip()
    return s


def build_name_index(name_player_pairs: list[tuple[str, int]]) -> dict[str, int]:
    """Build a normalized-name -> player_id lookup from (name, player_id) pairs.

    Typically sourced from cached play-by-play (``player_name``/``player_id``
    columns) via ``build_name_index_from_pbp_frames`` -- there is no
    dedicated player-name table in this schema yet. Raises ``ValueError`` if
    two distinct player_ids normalize to the same name (an ambiguity that
    must be resolved by the caller, not silently collapsed).
    """
    index: dict[str, int] = {}
    conflicts: dict[str, set[int]] = {}
    for raw_name, player_id in name_player_pairs:
        if not raw_name:
            continue
        key = normalize_name(raw_name)
        if not key:
            continue
        if key in index and index[key] != player_id:
            conflicts.setdefault(key, {index[key]}).add(player_id)
            continue
        index[key] = player_id
    if conflicts:
        raise ValueError(
            "ambiguous player names map to multiple player_ids "
            "(name index must be disambiguated upstream, e.g. by team): "
            f"{conflicts}"
        )
    return index


def build_name_index_from_pbp_frames(frames: dict[str, pl.DataFrame]) -> dict[str, int]:
    """Convenience wrapper: build a name index from cached raw pbp frames.

    ``frames`` maps game_id -> raw play-by-play DataFrame with (at least)
    ``player_name``/``player_id`` columns, the shape cached by
    ``nba.ingest.pbp`` (and ``tests/fixtures/pbp/*.json`` for the fixture).
    Rows with ``player_id == 0`` (period/game markers, no associated player)
    are skipped.
    """
    pairs: list[tuple[str, int]] = []
    for df in frames.values():
        if df.is_empty() or "player_name" not in df.columns or "player_id" not in df.columns:
            continue
        sub = df.filter(pl.col("player_id") != 0).select(["player_name", "player_id"]).unique()
        pairs.extend((str(n), int(p)) for n, p in sub.rows())
    return build_name_index(pairs)


def build_name_index_from_static_players(active_only: bool = True) -> dict[str, int]:
    """Build the name -> player_id index from nba_api's bundled static player list.

    ``nba_api.stats.static.players`` ships a static JSON of every player
    known to the NBA Stats API, bundled in the package itself -- reading it
    is a **local, in-package lookup, not a network call** (unlike every
    ``stats.endpoints`` import in this codebase). This closes the common
    "valid injury-report player isn't in cached play-by-play yet" gap: a
    player can be on tonight's roster (and thus on the official injury
    report) without ever having appeared in a play-by-play file this
    project has pulled.

    ``active_only=True`` (the default) uses ``get_active_players()`` (~530
    players) -- the right scope for an injury report, which only ever lists
    currently rostered players, and small enough that normalized-name
    collisions are rare. Two distinct players can still share a normalized
    name (e.g. two different "Jalen Williams"-shaped collisions across
    eras); unlike ``build_name_index`` (which raises on any collision),
    collisions here are resolved deterministically by keeping the
    **first-seen** ``player_id`` per name (stable because
    ``get_active_players()`` returns players in a fixed order) and emitting
    a ``UserWarning`` naming every collided key -- documented here so this
    is a visible, logged choice, never a silent wrong guess.
    """
    from nba_api.stats.static import players as static_players  # lazy, local lookup only

    roster = static_players.get_active_players() if active_only else static_players.get_players()
    pairs: list[tuple[str, int]] = [(str(p["full_name"]), int(p["id"])) for p in roster]

    index: dict[str, int] = {}
    collisions: set[str] = set()
    for raw_name, player_id in pairs:
        key = normalize_name(raw_name)
        if not key:
            continue
        if key in index:
            collisions.add(key)
            continue
        index[key] = player_id
    if collisions:
        warnings.warn(
            "build_name_index_from_static_players: normalized-name collision(s) "
            f"resolved by keeping the first-seen player_id: {sorted(collisions)}",
            stacklevel=2,
        )
    return index


def _parse_report_gamedate(raw: str | None) -> date | None:
    """Parse the official report's ``GameDate`` column ('MM/DD/YYYY')."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_matchup(raw: str | None) -> tuple[str, str] | None:
    """Split the report's ``Matchup`` column ('AWAY@HOME', e.g. 'BOS@DET')
    into ``(away_abbrev, home_abbrev)``, or ``None`` if malformed."""
    if not raw or "@" not in raw:
        return None
    away, _, home = raw.partition("@")
    away = away.strip()
    home = home.strip()
    if not away or not home:
        return None
    return away, home


def resolve_game_id(
    con: duckdb.DuckDBPyConnection, game_date: date, away_abbrev: str, home_abbrev: str
) -> str | None:
    """Resolve a (date, away team, home team) triple to a ``games.game_id``.

    Team abbreviations are mapped to nba_api's static team ids via
    ``nba.ingest.teams.TEAM_ABBREV_TO_ID`` (bundled, no network). Looks up
    ``games`` for the expected AWAY@HOME orientation first; if that finds
    no row, retries with the orientation swapped (a defensive fallback in
    case a report's own ``@`` convention ever differs from this schema's
    home/away columns). Returns ``None`` -- never raises -- on an unknown
    abbreviation or no match in either orientation: an unresolved game_id
    is a legitimate, expected outcome (e.g. the game hasn't been ingested
    yet), not an error.
    """
    away_id = TEAM_ABBREV_TO_ID.get(away_abbrev)
    home_id = TEAM_ABBREV_TO_ID.get(home_abbrev)
    if away_id is None or home_id is None:
        return None

    row = con.execute(
        "SELECT game_id FROM games WHERE game_date = ? AND home_team = ? AND away_team = ?",
        [game_date, home_id, away_id],
    ).fetchone()
    if row is None:
        row = con.execute(
            "SELECT game_id FROM games WHERE game_date = ? AND home_team = ? AND away_team = ?",
            [game_date, away_id, home_id],
        ).fetchone()
    return cast(str, row[0]) if row is not None else None


def resolve_player_id(raw_name: str, name_index: dict[str, int]) -> int:
    """Resolve a raw player name to a player_id, or raise loudly.

    Exact normalized match first; falls back to a high-confidence
    (``_FUZZY_MATCH_CUTOFF``) ``difflib`` fuzzy match to tolerate minor
    misspellings/diacritics drift in externally sourced injury reports.
    Anything below that confidence raises ``UnmatchedPlayerNameError`` with
    the closest candidates listed, rather than guessing.
    """
    key = normalize_name(raw_name)
    if key in name_index:
        return name_index[key]
    candidates = difflib.get_close_matches(key, name_index.keys(), n=3, cutoff=_FUZZY_MATCH_CUTOFF)
    if len(candidates) == 1:
        return name_index[candidates[0]]
    suggestion = (
        f" closest candidates: {candidates}" if candidates else " no close candidates found"
    )
    raise UnmatchedPlayerNameError(
        f"could not resolve player name {raw_name!r} (normalized {key!r}) "
        f"to a player_id.{suggestion}"
    )


def _as_timestamp(value: str | date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    # Accept plain 'YYYY-MM-DD' or full ISO timestamps.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value + "T00:00:00")


def normalize_inactive_frame(
    inactive_raw: pl.DataFrame,
    *,
    game_id: str,
    tipoff_as_of: str | date | datetime,
    pulled_at: datetime,
) -> pl.DataFrame:
    """Map a raw BoxScoreSummaryV2 InactivePlayers frame to ``AVAILABILITY_SCHEMA``.

    Columns verified against nba_api's InactivePlayers result set:
    ``PLAYER_ID``, ``FIRST_NAME``, ``LAST_NAME`` (plus team columns, unused
    here). ``PLAYER_ID`` is already present -- no name matching needed for
    this source. Empty input (no inactive players for the game, a
    legitimate outcome) returns an empty frame with the right schema.
    """
    as_of = _as_timestamp(tipoff_as_of)
    if inactive_raw.is_empty():
        return pl.DataFrame(schema=dict.fromkeys(AVAILABILITY_SCHEMA, pl.Null))
    n = len(inactive_raw)
    return pl.DataFrame(
        {
            "player_id": inactive_raw["PLAYER_ID"].cast(pl.Int64).to_list(),
            "as_of": [as_of] * n,
            "game_id": [game_id] * n,
            "status": ["inactive"] * n,
            "reason": [None] * n,  # not present on this result set
            "source": ["nba_inactive_list"] * n,
            "pulled_at": [pulled_at] * n,
        }
    )


def parse_manual_announcements(
    records: list[dict[str, object]],
    name_index: dict[str, int],
    *,
    pulled_at: datetime,
) -> pl.DataFrame:
    """Parse a list of manually curated injury-report records into rows.

    Each record is expected to have ``player_name`` (required, resolved via
    ``resolve_player_id`` -- raises loudly on no/ambiguous match),
    ``status`` (required, must be one of ``_VALID_STATUSES``), ``as_of``
    (required, ISO date/timestamp -- the announcement time), and optionally
    ``reason``/``game_id``.
    """
    rows: list[dict[str, object]] = []
    for rec in records:
        name = rec.get("player_name")
        if not name or not isinstance(name, str):
            raise ValueError(f"manual availability record missing player_name: {rec}")
        status = rec.get("status")
        if status not in _VALID_STATUSES:
            raise ValueError(f"manual availability record has invalid status {status!r}: {rec}")
        as_of_raw = rec.get("as_of")
        if not as_of_raw or not isinstance(as_of_raw, str):
            raise ValueError(f"manual availability record missing as_of: {rec}")
        player_id = resolve_player_id(name, name_index)
        rows.append(
            {
                "player_id": player_id,
                "as_of": _as_timestamp(as_of_raw),
                "game_id": rec.get("game_id"),
                "status": status,
                "reason": rec.get("reason"),
                "source": "manual_announced",
                "pulled_at": pulled_at,
            }
        )
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(AVAILABILITY_SCHEMA, pl.Null))
    return pl.DataFrame(rows).select(AVAILABILITY_SCHEMA)


# ---------------------------------------------------------------------------
# Official NBA injury report (PDF) -- the real forward-looking source.
# ---------------------------------------------------------------------------
#
# Teams report status by 5pm local the day before a game, with updates up to
# ~30 minutes before tip (see docs/INJURY_FEED_2026-10-08.md). This is the
# `manual_announced`-shaped source's first real producer: names only, no
# stable player_id, resolved via `resolve_player_id` (fails loudly on any
# unmatched name -- see `UnmatchedPlayerNameError` above).
#
# SECURITY NOTE: the PDF's text content (player names, status, reason
# strings) is untrusted external data. This module only ever does
# string/table parsing on it -- no `eval`, no dynamic dispatch keyed on
# report text, no interpretation of report content as instructions. Keep it
# that way; a malicious or malformed PDF should at worst raise a parse
# error, never execute anything.

#: Left x0 boundaries (points) for each column, verified against the
#: committed fixture PDF. A word is assigned to the column whose boundary is
#: the largest one <= the word's x0.
_COLUMN_BOUNDARIES: list[tuple[float, str]] = [
    (0.0, "gamedate"),
    (119.0, "gametime"),
    (200.0, "matchup"),
    (264.0, "team"),
    (425.0, "player"),
    (585.0, "status"),
    (666.0, "reason"),
]

#: Visual-row bucketing granularity: words on the same printed line can have
#: slightly different ``top`` values; this rounds them into the same bucket.
_ROW_BUCKET_PX = 4.0

#: Status text that means "no report filed yet for this slot" -- not a real
#: status, skip the row entirely (normalized, so "Not Yet Submitted" and
#: "NotYetSubmitted" both match).
_PLACEHOLDER_STATUS = "notyetsubmitted"

#: Maps the parsed report's own status text (lowercased) to the schema's
#: enum. All five real statuses pass through unchanged; kept as an explicit
#: map (not just ``.lower()``) so a future wording drift fails loudly via
#: the KeyError in the caller rather than silently writing a bad enum value.
_STATUS_MAP = {
    "out": "out",
    "doubtful": "doubtful",
    "questionable": "questionable",
    "probable": "probable",
    "available": "available",
}


def _column_for_x0(x0: float) -> str:
    best = _COLUMN_BOUNDARIES[0][1]
    for boundary, name in _COLUMN_BOUNDARIES:
        if x0 >= boundary:
            best = name
    return best


def _is_header_or_footer(row_text: str) -> bool:
    return (
        "GameDate" in row_text
        or row_text.startswith("Injury Report")
        or row_text.startswith("Page")
    )


class _PlayerRecord:
    """Mutable builder for one player row while a page is being parsed."""

    __slots__ = ("gamedate", "gametime", "matchup", "team", "player", "status", "y", "reason_parts")

    def __init__(
        self,
        *,
        gamedate: str | None,
        gametime: str | None,
        matchup: str | None,
        team: str | None,
        player: str,
        status: str | None,
        y: float,
        reason0: str | None,
    ) -> None:
        self.gamedate = gamedate
        self.gametime = gametime
        self.matchup = matchup
        self.team = team
        self.player = player
        self.status = status
        self.y = y
        self.reason_parts: list[tuple[float, str]] = [(y, reason0)] if reason0 else []

    @property
    def reason(self) -> str | None:
        if not self.reason_parts:
            return None
        parts = [text for _, text in sorted(self.reason_parts, key=lambda p: p[0])]
        return " ".join(parts)


def _parse_report_pages(pdf: object) -> list[_PlayerRecord]:
    """Walk every page's words into ``_PlayerRecord``s.

    Implements the validated algorithm exactly: visual rows bucketed by
    ``round(top / _ROW_BUCKET_PX) * _ROW_BUCKET_PX``, columns assigned by
    ``x0``, header/title/footer rows skipped, ``gamedate``/``gametime``/
    ``matchup``/``team`` forward-filled across rows *and* pages (they only
    print on the first player row of each game/team block), and stray
    ``reason`` fragments (wrapped lines, which can sit above or below their
    player's own row) assigned to the nearest player row by y -- but scoped
    to the SAME PAGE only. Pooling fragment assignment across pages was the
    one bug found during validation: y resets per page, so a page-2 reason
    fragment can look "nearest" to a page-1 player if compared globally.
    """
    records: list[_PlayerRecord] = []
    state: dict[str, str | None] = {
        "gamedate": None,
        "gametime": None,
        "matchup": None,
        "team": None,
    }

    for page in pdf.pages:  # type: ignore[attr-defined]
        words = page.extract_words()
        rows: dict[float, list[dict[str, object]]] = {}
        for w in words:
            top = cast(float, w["top"])
            key = round(top / _ROW_BUCKET_PX) * _ROW_BUCKET_PX
            rows.setdefault(key, []).append(w)

        page_start = len(records)
        floating_fragments: list[tuple[float, str]] = []

        for y in sorted(rows):
            ws = sorted(rows[y], key=lambda w: cast(float, w["x0"]))
            row_text = " ".join(str(w["text"]) for w in ws)
            if _is_header_or_footer(row_text):
                continue

            cols: dict[str, str] = {}
            for w in ws:
                col = _column_for_x0(cast(float, w["x0"]))
                text = str(w["text"])
                cols[col] = f"{cols[col]} {text}" if col in cols else text

            for field in ("gamedate", "gametime", "matchup", "team"):
                if field in cols:
                    state[field] = cols[field]

            player = cols.get("player")
            if player:
                records.append(
                    _PlayerRecord(
                        gamedate=state["gamedate"],
                        gametime=state["gametime"],
                        matchup=state["matchup"],
                        team=state["team"],
                        player=player,
                        status=cols.get("status"),
                        y=y,
                        reason0=cols.get("reason"),
                    )
                )
            else:
                reason = cols.get("reason")
                if reason:
                    floating_fragments.append((y, reason))

        page_records = records[page_start:]
        if page_records and floating_fragments:
            for y, text in floating_fragments:
                nearest = min(page_records, key=lambda r: abs(r.y - y))
                nearest.reason_parts.append((y, text))

    return records


def _reorder_last_first(raw_name: str) -> str:
    """``"Last,First"`` -> ``"First Last"`` (the report's own name order)."""
    if "," not in raw_name:
        return raw_name
    last, _, first = raw_name.partition(",")
    return f"{first.strip()} {last.strip()}".strip()


def parse_official_injury_report(
    pdf_path_or_bytes: object,
    name_index: dict[str, int],
    *,
    report_dt: datetime,
    con: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Parse the NBA's official injury-report PDF into ``AVAILABILITY_SCHEMA`` rows.

    ``pdf_path_or_bytes`` is anything ``pdfplumber.open`` accepts (a path,
    ``Path``, or file-like/bytes object). ``name_index`` resolves each
    "Last,First" name (reordered to "First Last" first) to a ``player_id``
    via ``resolve_player_id`` -- same fail-loudly discipline as the manual
    path, except here ALL unmatched names across the whole report are
    accumulated and raised together in one ``UnmatchedPlayerNameError``
    (a report-wide run should tell the caller everything that needs fixing,
    not stop at the first miss and hide the rest).

    ``report_dt`` is this report's own publish timestamp (parsed from its
    filename/title by the caller, e.g. ``official_report_url``'s inverse) --
    used as both ``as_of`` and ``pulled_at``.

    ``game_id`` is resolved via ``resolve_game_id`` from each row's own
    (forward-filled) ``GameDate``/``Matchup`` columns, **only if ``con`` is
    supplied** -- a live DuckDB connection with the ``games`` table loaded.
    This is deliberately best-effort: an unresolved game (unknown
    abbreviation, or the game simply isn't in ``games`` yet) leaves
    ``game_id`` as ``None`` rather than raising -- a report can be, and
    usually is, published for games not yet ingested. If ``con`` is omitted
    (the default), ``game_id`` is always ``None``, preserving this
    function's original no-DB-required contract for callers that only need
    the parsed names/statuses.

    Rows whose status is the "Not Yet Submitted" placeholder (no real report
    filed for that slot yet) are skipped, not written as a row.
    """
    import pdfplumber  # lazy: keep this module network/parse-dependency-free at import time

    pdf = pdfplumber.open(pdf_path_or_bytes)  # type: ignore[arg-type]
    try:
        records = _parse_report_pages(pdf)
    finally:
        pdf.close()

    unmatched: list[str] = []
    rows: list[dict[str, object]] = []
    for rec in records:
        status_raw = (rec.status or "").strip()
        status_key = status_raw.lower()
        if not status_key or status_key.replace(" ", "") == _PLACEHOLDER_STATUS:
            continue
        status = _STATUS_MAP.get(status_key)
        if status is None:
            # Unknown status text: fail loudly rather than silently drop a
            # real row, mirroring parse_manual_announcements's ValueError.
            raise ValueError(f"unrecognized injury-report status {status_raw!r} for {rec.player!r}")

        display_name = _reorder_last_first(rec.player)
        try:
            player_id = resolve_player_id(display_name, name_index)
        except UnmatchedPlayerNameError:
            unmatched.append(display_name)
            continue

        game_id: str | None = None
        if con is not None:
            parsed_date = _parse_report_gamedate(rec.gamedate)
            matchup = parse_matchup(rec.matchup)
            if parsed_date is not None and matchup is not None:
                away_abbrev, home_abbrev = matchup
                game_id = resolve_game_id(con, parsed_date, away_abbrev, home_abbrev)

        rows.append(
            {
                "player_id": player_id,
                "as_of": report_dt,
                "game_id": game_id,
                "status": status,
                "reason": rec.reason,
                "source": "nba_official_report",
                "pulled_at": report_dt,
            }
        )

    if unmatched:
        raise UnmatchedPlayerNameError(
            "could not resolve the following official-injury-report player "
            f"names to a player_id: {unmatched}"
        )

    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(AVAILABILITY_SCHEMA, pl.Null))
    return pl.DataFrame(rows).select(AVAILABILITY_SCHEMA)
