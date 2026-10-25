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
from pathlib import Path

from nba.parse.availability import normalize_name

DEFAULT_ALIAS_FILE = Path(__file__).resolve().parents[2] / "configs" / "kalshi_aliases.yaml"

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

#: ``difflib`` ratio for listing near neighbours in error messages and alias
#: candidate reports only. Never used to accept a match.
_FUZZY_MATCH_CUTOFF = 0.84


class UnmatchedKalshiNameError(ValueError):
    """Raised when a Kalshi market title's player name can't be resolved.

    Deliberately loud, per CLAUDE.md ("fail loudly on unmatched names") --
    a wrong silent match would attach a market/price history to the wrong
    player's ``player_rates``/``prop_predictions`` comparison.
    """


def resolve_kalshi_player_name(raw_name: str, aliases: dict[str, int] | None = None) -> int:
    """Resolve a Kalshi title's player display name to a ``player_id``.

    Exact normalized match against the reviewed alias table only. There is
    no fuzzy accept (removed 2026-10-24, the table is exact for every live
    name); anything else raises ``UnmatchedKalshiNameError`` with near
    neighbours listed as a hint, never used -- callers must add a reviewed row.
    """
    table = _ALIASES if aliases is None else aliases
    key = normalize_name(raw_name)
    if key in table:
        return table[key]
    # Suggestions only (never accepted): an unknown name fails loudly.
    candidates = difflib.get_close_matches(key, table.keys(), n=3, cutoff=_FUZZY_MATCH_CUTOFF)
    suggestion = (
        f" closest reviewed candidates: {candidates}" if candidates else " no close candidates"
    )
    raise UnmatchedKalshiNameError(
        f"could not resolve Kalshi player name {raw_name!r} (normalized {key!r}) "
        f"to a player_id via the reviewed alias table.{suggestion} "
        "Add a reviewed entry to nba/kalshi/aliases.py before ingesting this market."
    )


def load_reviewed_aliases(path: Path | None = None) -> dict[str, int]:
    """Built-in aliases merged with the human-edited ``configs/kalshi_aliases.yaml``.

    The YAML is ``aliases: {"display name": player_id}``; keys are normalized
    here. Entries are assertions made by a human reviewer -- nothing in this
    package writes to that file.
    """
    import yaml

    merged = dict(_ALIASES)
    p = path or DEFAULT_ALIAS_FILE
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        for name, pid in (loaded.get("aliases") or {}).items():
            merged[normalize_name(str(name))] = int(pid)
    return merged


def propose_alias_candidates(names: list[str]) -> dict[str, list[int]]:
    """For REVIEW only: unique exact-normalized matches against nba_api's
    bundled static player list (a local file, no network). Never applied
    automatically; a human copies confirmed rows into the aliases YAML."""
    from nba_api.stats.static import players

    index: dict[str, list[int]] = {}
    for p in players.get_players():
        index.setdefault(normalize_name(p["full_name"]), []).append(int(p["id"]))
    return {n: index.get(normalize_name(n), []) for n in sorted(set(names))}
