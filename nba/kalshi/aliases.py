"""Reviewed player-name alias table for Kalshi market titles.

Kalshi market/event titles reference players by free-text display name
(e.g. ``"LeBron James"``), never by this project's ``player_id``. CLAUDE.md
requires that matching be "fuzzy" but backed by "a reviewed alias table"
that "fails loudly on unmatched names" -- i.e. the opposite of silently
guessing. This module is the Kalshi-specific half of that contract; the
nba_inactive_list / manual-announcement half lives in
``nba.parse.availability`` (same discipline, different data source -- see
the docstring on ``UnmatchedPlayerNameError`` there).

``_ALIASES`` is intentionally tiny and must be hand-reviewed before any
entry is added: every row is a human assertion that a given Kalshi display
name maps to a given ``player_id``. Nothing here is inferred automatically.
No nba_api / network import here -- pure string matching, safe to unit-test
in isolation.
"""

from __future__ import annotations

import difflib

from nba.parse.availability import normalize_name

#: Reviewed display-name -> player_id aliases. Keyed on the *normalized*
#: name (see ``normalize_name``) so punctuation/accents/suffix variants in
#: Kalshi titles ("LeBron James", "lebron james jr.") resolve identically.
#: Real nba_api player_ids; add new rows only after a human confirms the
#: mapping (this is the "reviewed" part of the alias table).
_ALIASES: dict[str, int] = {
    "lebron james": 2544,
    "stephen curry": 201939,
    "nikola jokic": 203999,
    "giannis antetokounmpo": 203507,
    "luka doncic": 1629029,
    "jayson tatum": 1628369,
    "shai gilgeous alexander": 1628983,
}

#: Below this ``difflib`` similarity ratio, a fuzzy match is not trusted
#: (same cutoff rationale/value as ``nba.parse.availability``: tolerate
#: minor Kalshi title typos/diacritics drift, never guess a weak match).
_FUZZY_MATCH_CUTOFF = 0.84


class UnmatchedKalshiNameError(ValueError):
    """Raised when a Kalshi market title's player name can't be resolved.

    Deliberately loud, per CLAUDE.md ("fail loudly on unmatched names") --
    a wrong silent match would attach a market/price history to the wrong
    player's ``player_rates``/``prop_predictions`` comparison.
    """


def resolve_kalshi_player_name(raw_name: str, aliases: dict[str, int] | None = None) -> int:
    """Resolve a Kalshi title's player display name to a ``player_id``.

    Exact normalized match against the reviewed alias table first; falls
    back to a high-confidence fuzzy match to tolerate trivial formatting
    drift. Anything below that confidence (or an alias table with no
    plausible candidate) raises ``UnmatchedKalshiNameError`` rather than
    guessing -- callers must add a reviewed row, not silently proceed.
    """
    table = _ALIASES if aliases is None else aliases
    key = normalize_name(raw_name)
    if key in table:
        return table[key]
    candidates = difflib.get_close_matches(key, table.keys(), n=3, cutoff=_FUZZY_MATCH_CUTOFF)
    if len(candidates) == 1:
        return table[candidates[0]]
    suggestion = (
        f" closest reviewed candidates: {candidates}" if candidates else " no close candidates"
    )
    raise UnmatchedKalshiNameError(
        f"could not resolve Kalshi player name {raw_name!r} (normalized {key!r}) "
        f"to a player_id via the reviewed alias table.{suggestion} "
        "Add a reviewed entry to nba/kalshi/aliases.py before ingesting this market."
    )
