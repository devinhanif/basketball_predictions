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
from datetime import date, datetime

import polars as pl

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

_VALID_STATUSES = {"out", "questionable", "probable", "available", "inactive"}

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
