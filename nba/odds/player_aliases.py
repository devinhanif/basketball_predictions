"""Generated player-name table for the historical odds pull (exact normalised match only).

The reviewed Kalshi alias table covers about 25 players, so ~90% of historical prop rows had no
``player_id``. This module builds a second, GENERATED table from ONE stats.nba.com
``commonallplayers`` response (cached as parquet; nothing is fetched when the cache exists):

* Names are normalised (NFKD accent fold, lower case, apostrophes and periods removed without a
  space so ``O.G.`` and ``OG`` agree, other punctuation to a space, trailing ``Jr/Sr/II/III/IV``
  stripped, spaces collapsed).
* A normalised name that maps to exactly one ``player_id`` among players whose career overlaps the
  odds window (``to_year >= min_to_year``) is an alias.
* A normalised name that maps to more than one id (``Michael Porter`` vs ``Michael Porter Jr.``
  once the suffix is stripped, or two real namesakes) goes in ``collisions`` and STAYS UNRESOLVED
  until a human reviews it. Collisions are also counted over the all-time list, for transparency.
* ``unmatched`` lists vendor names found in ``odds_history`` that match neither this table nor the
  reviewed Kalshi table.

The resolution order in :class:`HistoryResolver` is: reviewed Kalshi table first (a human
assertion), then this generated table, never fuzzy, collisions never.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from nba.odds.theoddsapi import NameResolver

log = logging.getLogger("nba.odds.history")

ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = ROOT / "configs" / "odds_player_aliases.yaml"
PLAYERS_CACHE = ROOT / "data" / "players_static" / "commonallplayers_2025-26.parquet"
FETCH_PACING_S = 6.0

_DROP = re.compile(r"['`’.]")  # joined, not spaced: O.G. -> og, De'Aaron -> deaaron
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_SUFFIX = re.compile(r"\s(?:jr|sr|ii|iii|iv)$")
_FOLD = str.maketrans({"ł": "l", "đ": "d", "ø": "o", "ı": "i", "ß": "ss", "æ": "ae", "œ": "oe"})


def normalize_player_name(raw: str) -> str:
    """``"Luka Dončić"`` -> ``"luka doncic"``; ``"Jaren Jackson Jr."`` -> ``"jaren jackson"``."""
    # ł/đ/ø have no NFKD decomposition, so fold them explicitly before the accent strip
    s = unicodedata.normalize("NFKD", raw.lower().translate(_FOLD))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _DROP.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    s = " ".join(s.split())
    return _SUFFIX.sub("", s).strip()


# ----------------------------------------------------------------------------------------------
# The one stats.nba.com request (cached)
# ----------------------------------------------------------------------------------------------


def load_players(
    cache_path: Path | None = None,
    *,
    refresh: bool = False,
    fetch: Callable[[], pl.DataFrame] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> pl.DataFrame:
    """``person_id, display_first_last, from_year, to_year`` for every player ever listed.

    Reads the parquet cache; otherwise makes exactly ONE ``commonallplayers`` request
    (``IsOnlyCurrentSeason=0``, ``LeagueID=00``, ``Season=2025-26``) after a 6 s courtesy wait.
    """
    path = cache_path or PLAYERS_CACHE
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    sleep(FETCH_PACING_S)
    df = (fetch or _fetch_commonallplayers)()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(path)
    return df


def _fetch_commonallplayers() -> pl.DataFrame:
    from nba_api.stats.endpoints import commonallplayers

    raw = commonallplayers.CommonAllPlayers(
        is_only_current_season=0, league_id="00", season="2025-26", timeout=60
    ).get_data_frames()[0]
    return pl.DataFrame(
        {
            "person_id": [int(x) for x in raw["PERSON_ID"]],
            "display_first_last": [str(x) for x in raw["DISPLAY_FIRST_LAST"]],
            "from_year": [int(x) if str(x).isdigit() else None for x in raw["FROM_YEAR"]],
            "to_year": [int(x) if str(x).isdigit() else None for x in raw["TO_YEAR"]],
        },
        schema={
            "person_id": pl.Int64,
            "display_first_last": pl.String,
            "from_year": pl.Int64,
            "to_year": pl.Int64,
        },
    )


# ----------------------------------------------------------------------------------------------
# Table build / io
# ----------------------------------------------------------------------------------------------


@dataclass
class AliasTable:
    aliases: dict[str, int] = field(default_factory=dict)
    collisions: dict[str, list[int]] = field(default_factory=dict)
    all_time_collisions: int = 0


def build_table(players: pl.DataFrame, min_to_year: int = 2022) -> AliasTable:
    """Aliases for players active in or after ``min_to_year``; collisions listed, not resolved."""
    by_name: dict[str, set[int]] = {}
    by_name_all: dict[str, set[int]] = {}
    for r in players.iter_rows(named=True):
        key = normalize_player_name(str(r["display_first_last"] or ""))
        if not key:
            continue
        pid = int(r["person_id"])
        by_name_all.setdefault(key, set()).add(pid)
        if (r["to_year"] or 0) >= min_to_year:
            by_name.setdefault(key, set()).add(pid)
    t = AliasTable()
    for key, ids in sorted(by_name.items()):
        if len(ids) == 1:
            t.aliases[key] = next(iter(ids))
        else:
            t.collisions[key] = sorted(ids)
    t.all_time_collisions = sum(1 for v in by_name_all.values() if len(v) > 1)
    return t


def write_table(
    path: Path, table: AliasTable, unmatched: list[tuple[str, int]], min_to_year: int
) -> None:
    doc: dict[str, Any] = {
        "aliases": dict(table.aliases),
        "collisions": {k: list(v) for k, v in table.collisions.items()},
        "unmatched": [{"name": n, "rows": c} for n, c in unmatched],
    }
    header = (
        "# GENERATED by `python -m nba.odds build-aliases` from one stats.nba.com\n"
        "# commonallplayers response (cached under data/players_static/), players with\n"
        f"# to_year >= {min_to_year}. Keys are normalised names (nba.odds.player_aliases).\n"
        "# NOT hand-reviewed: an alias is a unique normalised-name match, nothing more.\n"
        "# `collisions` names map to several ids and stay UNRESOLVED until a human reviews them.\n"
        f"# all-time collisions in the full list (diagnostic): {table.all_time_collisions}\n"
        "# `unmatched` = vendor names in odds_history that match neither this table nor the\n"
        "# reviewed Kalshi table (rows = how many odds rows carry the name).\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(doc, sort_keys=True, allow_unicode=False, width=100))


def load_generated(path: Path | None = None) -> tuple[dict[str, int], set[str]]:
    p = path or ALIASES_PATH
    if not p.exists():
        return {}, set()
    doc = yaml.safe_load(p.read_text()) or {}
    aliases = {str(k): int(v) for k, v in (doc.get("aliases") or {}).items()}
    return aliases, {str(k) for k in (doc.get("collisions") or {})}


# ----------------------------------------------------------------------------------------------
# Resolver
# ----------------------------------------------------------------------------------------------


@dataclass
class HistoryResolver(NameResolver):
    """Reviewed Kalshi aliases first, then the generated table; exact normalised match only."""

    generated: dict[str, int] = field(default_factory=dict)
    collisions: set[str] = field(default_factory=set)

    def player_id(self, name: str | None) -> int | None:
        if not name:
            return None
        reviewed = super().player_id(name)
        if reviewed is not None:
            return reviewed
        key = normalize_player_name(name)
        if key in self.collisions:
            return None
        hit = self.generated.get(key)
        if hit is not None:
            return hit
        # One 2023-24 book writes "Irving, Kyrie" or "Murray Dejounte" (surname first). Try the
        # reversed word order; it counts only when the reversed form is itself a unique alias.
        words = normalize_player_name(name.replace(",", " ")).split()
        if len(words) >= 2:
            swapped = " ".join(words[1:] + words[:1])
            if swapped not in self.collisions:
                return self.generated.get(swapped)
        return None


def history_resolver(base: NameResolver, alias_path: Path | None = None) -> HistoryResolver:
    gen, coll = load_generated(alias_path)
    return HistoryResolver(teams=base.teams, players=base.players, generated=gen, collisions=coll)


def unmatched_names(rows_by_name: Counter[str], resolver: NameResolver) -> list[tuple[str, int]]:
    """Vendor names (with row counts) that ``resolver`` cannot map, most frequent first."""
    miss = [(n, c) for n, c in rows_by_name.items() if resolver.player_id(n) is None]
    return sorted(miss, key=lambda x: (-x[1], x[0]))
