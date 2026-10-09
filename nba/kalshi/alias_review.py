"""Review-file builder for Kalshi player-name aliases (read-only, offline).

Produces a REVIEW document only. It never writes ``configs/kalshi_aliases.yaml``
(the reviewed table); a human copies confirmed rows across. Inputs are two
DuckDB files opened ``read_only`` plus nba_api's bundled static player list
(a local file, no network). Nothing here calls stats.nba.com or Kalshi.

Confidence policy: ``high`` is reserved for a candidate whose Kalshi name equals
the static full name exactly (case-insensitive, same characters), is the only
player in the static list with that normalized name, and who has box-score rows
in the latest season of the project DB (current-team proxy). Everything else is
``medium`` or ``low`` and needs a human look.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import yaml

from nba.kalshi.aliases import _FUZZY_MATCH_CUTOFF
from nba.kalshi.thresholds import SERIES_STAT, UnparseableTitleError, parse_prop_title
from nba.parse.availability import normalize_name

TEAM_MARKETS = Path(__file__).resolve().parents[2] / "configs" / "team_markets.yaml"


def open_read_only(path: str | Path, retries: int = 5, wait_s: float = 2.0) -> Any:
    """Open a DuckDB file read-only, retrying if a writer holds the lock."""
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:  # lock held by the snapshot job
            last = exc
            time.sleep(wait_s)
    assert last is not None
    raise last


@dataclass(frozen=True)
class PlayerRecord:
    player_id: int
    full_name: str
    is_active: bool
    team: str | None = None  # abbr of latest-season team, None = no latest-season rows
    last_game: str | None = None
    n_games: int = 0  # career box-score rows in the project DB


@dataclass
class NameStats:
    name: str
    n_markets: int = 0
    n_open: int = 0
    n_with_player_id: int = 0
    series: set[str] = field(default_factory=set)


def collect_kalshi_names(con: Any, now: Any = None) -> dict[str, NameStats]:
    """Distinct player names in prop-series markets, with market counts.

    ``n_open`` counts markets not yet settled whose close_time is in the future
    (or unknown) relative to ``now`` (naive UTC datetime; None = ignore time).
    """
    rows = con.execute(
        "SELECT series_ticker, title, player_id, settled_ts, close_time FROM kalshi_markets"
    ).fetchall()
    out: dict[str, NameStats] = {}
    for series, title, pid, settled, close in rows:
        if series not in SERIES_STAT or not title:
            continue
        try:
            name, _ = parse_prop_title(str(title))
        except UnparseableTitleError:
            continue
        s = out.setdefault(name, NameStats(name))
        s.n_markets += 1
        s.series.add(str(series))
        if pid is not None:
            s.n_with_player_id += 1
        if settled is None and (now is None or close is None or close > now):
            s.n_open += 1
    return out


def classify_name(name: str, aliases: dict[str, int]) -> tuple[str, int | None, str]:
    """(status, player_id, method) under the reviewed alias table.

    status is ``matched`` (exact normalized key, or a single fuzzy neighbour --
    the same rule as ``resolve_kalshi_player_name``), ``ambiguous`` (several
    fuzzy neighbours) or ``unmatched``.
    """
    key = normalize_name(name)
    if key in aliases:
        return "matched", aliases[key], "alias_exact"
    near = difflib.get_close_matches(key, aliases.keys(), n=3, cutoff=_FUZZY_MATCH_CUTOFF)
    if len(near) == 1:
        return "matched", aliases[near[0]], "alias_fuzzy"
    if len(near) > 1:
        return "ambiguous", None, "alias_fuzzy_multi"
    return "unmatched", None, "none"


def load_team_names(path: Path = TEAM_MARKETS) -> dict[int, str]:
    raw = yaml.safe_load(path.read_text())
    return {int(k): str(v["abbr"]) for k, v in raw["teams"].items()}


def load_player_records(nba_con: Any, static_players: list[dict[str, Any]]) -> list[PlayerRecord]:
    """Static list joined with latest-season team and career row count from the project DB."""
    season = nba_con.execute("SELECT max(season) FROM games").fetchone()[0]
    teams = load_team_names()
    latest = {
        int(r[0]): (int(r[1]), str(r[2]))
        for r in nba_con.execute(
            """
            SELECT player_id, arg_max(team_id, game_date), max(game_date)::VARCHAR
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE g.season = ? GROUP BY player_id
            """,
            [season],
        ).fetchall()
    }
    counts = {
        int(r[0]): int(r[1])
        for r in nba_con.execute(
            "SELECT player_id, count(*) FROM player_game_stats GROUP BY player_id"
        ).fetchall()
    }
    recs = []
    for p in static_players:
        pid = int(p["id"])
        team_id, last = latest.get(pid, (None, None))
        recs.append(
            PlayerRecord(
                pid,
                str(p["full_name"]),
                bool(p["is_active"]),
                teams.get(team_id) if team_id is not None else None,
                last,
                counts.get(pid, 0),
            )
        )
    return recs


def build_name_index(records: list[PlayerRecord]) -> dict[str, list[PlayerRecord]]:
    idx: dict[str, list[PlayerRecord]] = {}
    for r in records:
        idx.setdefault(normalize_name(r.full_name), []).append(r)
    return idx


def propose(name: str, index: dict[str, list[PlayerRecord]]) -> list[dict[str, Any]]:
    """Ranked candidate list for one name (possibly empty)."""
    key = normalize_name(name)
    hits = index.get(key, [])
    unique = len(hits) == 1
    props: list[dict[str, Any]] = []
    for r in hits:
        exact = r.full_name.casefold() == name.casefold()
        current = r.team is not None
        if exact and unique and current:
            conf, why = "high", "exact full name, unique in static list, played latest season"
        elif unique:
            conf = "medium"
            why = ("exact" if exact else "accent/punctuation/suffix-normalized") + " name, unique"
            why += "" if current else "; no latest-season games (retired, injured, or rookie)"
        else:
            conf, why = "low", f"{len(hits)} players share this normalized name"
        props.append(_entry(r, "exact_full_name" if exact else "normalized_exact", conf, why))
    if not hits:
        names = list(index)
        for m in difflib.get_close_matches(key, names, n=3, cutoff=_FUZZY_MATCH_CUTOFF):
            for r in index[m]:
                props.append(_entry(r, "fuzzy", "low", f"difflib neighbour {m!r}; verify manually"))
    return props


def _entry(r: PlayerRecord, method: str, conf: str, why: str) -> dict[str, Any]:
    return {
        "player_id": r.player_id,
        "matched_name": r.full_name,
        "team": r.team,
        "last_game_in_db": r.last_game,
        "career_games_in_db": r.n_games,
        "method": method,
        "confidence": conf,
        "why": why,
    }


def rookie_candidates(
    records: list[PlayerRecord], static_ids: set[int], flagged_ids: set[int]
) -> list[dict[str, Any]]:
    """Active in nba_api's static list but with no box-score rows and no players_static row.

    These cannot be matched to model output until they play (no player_rates /
    history). Reported so the maintainer knows which prop names will be un-priceable.
    """
    out = []
    for r in sorted(records, key=lambda x: x.full_name):
        if r.is_active and r.n_games == 0 and r.player_id not in static_ids:
            out.append(
                {
                    "player_id": r.player_id,
                    "name": r.full_name,
                    "also_on_kalshi": r.player_id in flagged_ids,
                }
            )
    return out


def preseed_roster(
    records: list[PlayerRecord], index: dict[str, list[PlayerRecord]], aliases: dict[str, int]
) -> list[dict[str, Any]]:
    """Pre-staged proposals for every active player with latest-season games.

    Excludes names already in the reviewed table. Lets the maintainer promote
    in bulk once Kalshi's actual display names are known (keys normalize, so
    "Nikola Jokic" and "Nikola Jokić" are the same alias).
    """
    out = []
    for r in sorted(records, key=lambda x: x.full_name):
        if not (r.is_active and r.team is not None):
            continue
        if normalize_name(r.full_name) in aliases:
            continue
        top = propose(r.full_name, index)
        if top:
            # On a normalized-name collision (Jr./II, namesakes) the first hit can be the
            # retired namesake; the row being staged is THIS active player, so lead with it.
            own = [p for p in top if p["player_id"] == r.player_id]
            out.append((own or top)[0] | {"kalshi_name": r.full_name})
    return out


def build_review(
    names: dict[str, NameStats],
    aliases: dict[str, int],
    records: list[PlayerRecord],
    static_ids: set[int],
    generated: str,
) -> dict[str, Any]:
    index = build_name_index(records)
    resolution: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for n in sorted(names):
        st = names[n]
        status, pid, method = classify_name(n, aliases)
        resolution.append(
            {
                "name": n,
                "status": status,
                "player_id": pid,
                "method": method,
                "n_markets": st.n_markets,
                "n_open": st.n_open,
                "n_markets_with_stored_player_id": st.n_with_player_id,
                "series": sorted(st.series),
            }
        )
        if status != "matched":
            unmatched.append(
                {
                    "name": n,
                    "status": status,
                    "n_markets": st.n_markets,
                    "proposals": propose(n, index),
                }
            )
    flagged = {p["player_id"] for u in unmatched for p in u["proposals"]}
    pre = preseed_roster(records, index, aliases)
    counts = {
        "distinct_names": len(names),
        "matched": sum(r["status"] == "matched" for r in resolution),
        "unmatched": sum(r["status"] == "unmatched" for r in resolution),
        "ambiguous": sum(r["status"] == "ambiguous" for r in resolution),
        "unmatched_with_high_confidence_proposal": sum(
            any(p["confidence"] == "high" for p in u["proposals"]) for u in unmatched
        ),
        "preseed_total": len(pre),
        "preseed_high": sum(p["confidence"] == "high" for p in pre),
    }
    return {
        "review_only": "REVIEW ONLY; promote rows by hand into configs/kalshi_aliases.yaml",
        "generated": generated,
        "counts": counts,
        "resolution": resolution,
        "unmatched": unmatched,
        "rookies_not_in_players_static": rookie_candidates(records, static_ids, flagged),
        "preseed_roster": pre,
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m nba.kalshi.alias_review --out configs/kalshi_aliases_candidates_<date>.yaml``."""
    import argparse
    from datetime import UTC, datetime

    from nba_api.stats.static import players

    from nba.kalshi.aliases import load_reviewed_aliases

    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(prog="python -m nba.kalshi.alias_review")
    ap.add_argument("--kalshi-db", default=str(root / "data" / "kalshi" / "kalshi.duckdb"))
    ap.add_argument("--nba-db", default=str(root / "nba.duckdb"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    now = datetime.now(UTC).replace(tzinfo=None)
    kcon = open_read_only(args.kalshi_db)
    names = collect_kalshi_names(kcon, now=now)
    kcon.close()
    ncon = open_read_only(args.nba_db)
    records = load_player_records(ncon, players.get_players())
    static_ids = {
        int(r[0]) for r in ncon.execute("SELECT player_id FROM players_static").fetchall()
    }
    ncon.close()
    review = build_review(names, load_reviewed_aliases(), records, static_ids, now.isoformat())
    Path(args.out).write_text(
        yaml.safe_dump(review, sort_keys=False, allow_unicode=True, width=100)
    )
    print(yaml.safe_dump(review["counts"], sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
