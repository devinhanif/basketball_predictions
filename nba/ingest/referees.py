"""Forward referee assignments: read-only collector for official.nba.com/referee-assignments.

The page (published ~9:00 am ET each game day) lists, per NBA game, the crew chief, referee,
umpire and (sometimes) an alternate as ``Name (#jersey)``. It also carries a WNBA table in a
separate ``wnba-refs-content`` block, which is ignored.

Storage: ``data/refs/refs.duckdb`` (a separate DB; never ``nba.duckdb``), append-only snapshots:

* ``ref_snapshots``  one row per distinct page content per page date (``fetched_at``, sha256,
  raw HTML saved under ``data/refs/raw/``).
* ``ref_assignments`` one row per (snapshot, game) with the four roles, jersey and a mapped
  ``official_id`` (from ``game_officials`` history in ``nba.duckdb``, opened READ-ONLY; if it is
  locked, the last cached ``data/refs/official_ids.json`` is used).

As-of contract: an assignment may be used for a game only if ``fetched_at`` precedes its tip.
Unmapped officials / teams are kept with NULL ids and reported (non-zero CLI exit), never dropped.
No credentials, GET only.

Run: ``uv run python -m nba.ingest.referees collect`` (once a day after 9:30 am ET).
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path

import duckdb

URL = "https://official.nba.com/referee-assignments/"
REFS_DIR = Path(__file__).resolve().parents[2] / "data" / "refs"
REFS_DB = REFS_DIR / "refs.duckdb"
ID_CACHE = REFS_DIR / "official_ids.json"
ROLES = ("crew_chief", "referee", "umpire", "alternate")

#: City / short name as printed on the page -> NBA team_id. LA teams are split on the nickname.
TEAM_IDS: dict[str, int] = {
    "atlanta": 1610612737,
    "boston": 1610612738,
    "brooklyn": 1610612751,
    "charlotte": 1610612766,
    "chicago": 1610612741,
    "cleveland": 1610612739,
    "dallas": 1610612742,
    "denver": 1610612743,
    "detroit": 1610612765,
    "golden state": 1610612744,
    "houston": 1610612745,
    "indiana": 1610612754,
    "la clippers": 1610612746,
    "la lakers": 1610612747,
    "memphis": 1610612763,
    "miami": 1610612748,
    "milwaukee": 1610612749,
    "minnesota": 1610612750,
    "new orleans": 1610612740,
    "new york": 1610612752,
    "oklahoma city": 1610612760,
    "orlando": 1610612753,
    "philadelphia": 1610612755,
    "phoenix": 1610612756,
    "portland": 1610612757,
    "sacramento": 1610612758,
    "san antonio": 1610612759,
    "toronto": 1610612761,
    "utah": 1610612762,
    "washington": 1610612764,
}
_TEAM_ALIASES = {
    "los angeles clippers": "la clippers",
    "l.a. clippers": "la clippers",
    "clippers": "la clippers",
    "los angeles lakers": "la lakers",
    "l.a. lakers": "la lakers",
    "lakers": "la lakers",
    "gs": "golden state",
    "okc": "oklahoma city",
}
_PERSON = re.compile(r"^(?P<name>.*?)\s*\(#\s*(?P<jersey>[^)]*)\)\s*$")


def team_id_from_page_name(raw: str) -> int | None:
    k = " ".join(raw.lower().replace(".", "").split())
    k = _TEAM_ALIASES.get(k, k)
    return TEAM_IDS.get(k)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(parts)


class _Table(HTMLParser):
    """Rows of ``<td>`` texts inside the (single) ``nba-refs-content`` div; also the page date."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.page_date_text = ""
        self._div_depth = 0  # >0 while inside nba-refs-content
        self._in_meta = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = (dict(attrs).get("class") or "").split()
        if tag == "div":
            if self._div_depth:
                self._div_depth += 1
            elif "nba-refs-content" in cls:
                self._div_depth = 1
            if "entry-meta" in cls:
                self._in_meta = True
        elif self._div_depth and tag == "tr":
            self._row = []
        elif self._div_depth and tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._div_depth:
                self._div_depth -= 1
            self._in_meta = False
        elif tag == "td" and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_meta and not self.page_date_text:
            self.page_date_text = data.strip()
        if self._cell is not None:
            self._cell.append(data)


@dataclass
class Assignment:
    game_date: date
    away_name: str
    home_name: str
    away_team_id: int | None
    home_team_id: int | None
    #: role -> (name, jersey) ; absent / empty roles map to ("", "")
    officials: dict[str, tuple[str, str]]


def parse_assignments(html: str) -> tuple[date | None, list[Assignment]]:
    """(page date, NBA assignments). Raises ``ValueError`` on a malformed game cell."""
    p = _Table()
    p.feed(html)
    page_date: date | None = None
    if p.page_date_text:
        try:
            page_date = datetime.strptime(p.page_date_text, "%B %d, %Y").date()
        except ValueError:
            page_date = None
    out: list[Assignment] = []
    for row in p.rows:
        if len(row) < 4:
            continue
        game = row[0]
        if "@" not in game:
            raise ValueError(f"unparseable game cell {game!r}")
        away, home = (x.strip() for x in game.split("@", 1))
        offs: dict[str, tuple[str, str]] = {}
        for role, cell in zip(ROLES, row[1:5] + [""] * (4 - len(row[1:5])), strict=False):
            m = _PERSON.match(cell)
            offs[role] = (m["name"].strip(), m["jersey"].strip()) if m else (cell.strip(), "")
        out.append(
            Assignment(
                page_date or date.min,
                away,
                home,
                team_id_from_page_name(away),
                team_id_from_page_name(home),
                offs,
            )
        )
    return page_date, out


# --------------------------------------------------------------------------
# name -> official_id
# --------------------------------------------------------------------------


@dataclass
class OfficialIndex:
    by_name: dict[str, set[int]] = field(default_factory=dict)
    by_jersey: dict[str, set[int]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: Sequence[tuple[int, str, str | None]]) -> OfficialIndex:
        idx = cls()
        for oid, name, jersey in rows:
            idx.by_name.setdefault(_norm(name), set()).add(int(oid))
            if jersey:
                idx.by_jersey.setdefault(str(jersey).strip(), set()).add(int(oid))
        return idx

    def resolve(self, name: str, jersey: str) -> int | None:
        if not name:
            return None
        k = _norm(name)
        cands = self.by_name.get(k, set())
        if len(cands) > 1 and jersey:
            cands = cands & self.by_jersey.get(jersey, set()) or cands
        if len(cands) == 1:
            return next(iter(cands))
        if not cands:
            close = difflib.get_close_matches(k, list(self.by_name), n=1, cutoff=0.88)
            if close and len(self.by_name[close[0]]) == 1:
                return next(iter(self.by_name[close[0]]))
        return None


def load_official_rows(
    nba_db: Path | None = None, cache: Path = ID_CACHE
) -> list[tuple[int, str, str | None]]:
    """(official_id, name, jersey) from ``game_officials`` (READ-ONLY); cached JSON if locked."""
    from nba.db.connect import DEFAULT_DB_PATH

    try:
        con = duckdb.connect(str(nba_db or DEFAULT_DB_PATH), read_only=True)
        try:
            rows = con.execute(
                "SELECT official_id, any_value(name), any_value(jersey) FROM game_officials "
                "GROUP BY official_id, jersey ORDER BY official_id"
            ).fetchall()
        finally:
            con.close()
        out = [(int(a), str(b), None if c is None else str(c)) for a, b, c in rows]
        if out:  # never overwrite a good cache with an empty table (officials not loaded yet)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out))
            return out
        if cache.exists():
            return [(int(a), str(b), c) for a, b, c in json.loads(cache.read_text())]
        return out
    except duckdb.Error:
        if cache.exists():
            return [(int(a), str(b), c) for a, b, c in json.loads(cache.read_text())]
        raise


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

_DDL = [
    "CREATE TABLE IF NOT EXISTS ref_snapshots (fetched_at TIMESTAMP, page_date DATE, "
    "source_url VARCHAR, n_games INT, html_sha256 VARCHAR, raw_path VARCHAR, "
    "PRIMARY KEY (fetched_at))",
    "CREATE TABLE IF NOT EXISTS ref_assignments (fetched_at TIMESTAMP, game_date DATE, "
    "away_team VARCHAR, home_team VARCHAR, away_team_id INT, home_team_id INT, "
    + ", ".join(f"{r} VARCHAR, {r}_jersey VARCHAR, {r}_id INT" for r in ROLES)
    + ", PRIMARY KEY (fetched_at, away_team, home_team))",
]


def open_refs_db(path: Path = REFS_DB) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    for ddl in _DDL:
        con.execute(ddl)
    return con


@dataclass
class CollectResult:
    page_date: date | None
    n_games: int
    stored: bool
    unmatched_officials: list[str]
    unmatched_teams: list[str]


def store_snapshot(
    con: duckdb.DuckDBPyConnection,
    html: str,
    *,
    fetched_at: datetime,
    index: OfficialIndex,
    raw_dir: Path = REFS_DIR / "raw",
) -> CollectResult:
    """Parse + append one snapshot. Identical content for the same page date is not re-stored."""
    page_date, games = parse_assignments(html)
    sha = hashlib.sha256(html.encode()).hexdigest()
    dup = con.execute(
        "SELECT 1 FROM ref_snapshots WHERE html_sha256 = ? AND page_date IS NOT DISTINCT FROM ?",
        [sha, page_date],
    ).fetchone()
    bad_off: list[str] = []
    bad_team: list[str] = []
    rows: list[list[object]] = []
    for g in games:
        for nm, tid in ((g.away_name, g.away_team_id), (g.home_name, g.home_team_id)):
            if tid is None:
                bad_team.append(nm)
        row: list[object] = [
            fetched_at,
            page_date,
            g.away_name,
            g.home_name,
            g.away_team_id,
            g.home_team_id,
        ]
        for role in ROLES:
            name, jersey = g.officials[role]
            oid = index.resolve(name, jersey)
            if name and oid is None:
                bad_off.append(f"{name} (#{jersey})")
            row += [name or None, jersey or None, oid]
        rows.append(row)
    if dup is not None:
        return CollectResult(
            page_date, len(games), False, sorted(set(bad_off)), sorted(set(bad_team))
        )
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / f"{page_date or 'nodate'}_{fetched_at:%Y%m%dT%H%M%S}.html"
    raw.write_text(html)
    con.execute(
        "INSERT INTO ref_snapshots VALUES (?,?,?,?,?,?)",
        [fetched_at, page_date, URL, len(games), sha, str(raw)],
    )
    if rows:
        ph = ",".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO ref_assignments VALUES ({ph})", rows)
    return CollectResult(page_date, len(games), True, sorted(set(bad_off)), sorted(set(bad_team)))


def fetch_page(url: str = URL, *, client: object | None = None) -> str:
    import httpx

    c = client if client is not None else httpx.Client(follow_redirects=True)
    try:
        resp = c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30.0)  # type: ignore[attr-defined]
        resp.raise_for_status()
        return str(resp.text)
    finally:
        if client is None:
            c.close()  # type: ignore[attr-defined]


def collect(
    *,
    refs_db: Path = REFS_DB,
    html: str | None = None,
    now: datetime | None = None,
    official_rows: Sequence[tuple[int, str, str | None]] | None = None,
) -> CollectResult:
    page = html if html is not None else fetch_page()
    rows = official_rows if official_rows is not None else load_official_rows()
    index = OfficialIndex.from_rows(rows)
    con = open_refs_db(refs_db)
    try:
        return store_snapshot(
            con, page, fetched_at=now or datetime.now(UTC).replace(tzinfo=None), index=index
        )
    finally:
        con.close()


def remap_official_ids(con: duckdb.DuckDBPyConnection, index: OfficialIndex) -> int:
    """Fill NULL ``*_id`` of stored assignments (e.g. after ``game_officials`` is loaded).

    Only derived id columns change; names, jerseys and ``fetched_at`` are untouched.
    """
    n = 0
    for role in ROLES:
        rows = con.execute(
            f"SELECT DISTINCT {role}, {role}_jersey FROM ref_assignments "
            f"WHERE {role} IS NOT NULL AND {role}_id IS NULL"
        ).fetchall()
        for name, jersey in rows:
            oid = index.resolve(str(name), str(jersey or ""))
            if oid is not None:
                con.execute(
                    f"UPDATE ref_assignments SET {role}_id = ? WHERE {role} = ? "
                    f"AND {role}_jersey IS NOT DISTINCT FROM ? AND {role}_id IS NULL",
                    [oid, name, jersey],
                )
                n += 1
    return n


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.referees")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="fetch today's assignments page and store a snapshot")
    c.add_argument("--refs-db", type=Path, default=REFS_DB)
    rm = sub.add_parser("remap", help="re-resolve NULL official ids from game_officials")
    rm.add_argument("--refs-db", type=Path, default=REFS_DB)
    args = ap.parse_args(argv)
    if args.cmd == "remap":
        con = open_refs_db(args.refs_db)
        try:
            n = remap_official_ids(con, OfficialIndex.from_rows(load_official_rows()))
        finally:
            con.close()
        print(f"referees remap: {n} (name, jersey) pairs newly mapped")
        return 0
    res = collect(refs_db=args.refs_db)
    print(
        f"referees: page_date={res.page_date} games={res.n_games} stored={res.stored} "
        f"unmatched_officials={res.unmatched_officials} unmatched_teams={res.unmatched_teams}"
    )
    return 2 if (res.unmatched_officials or res.unmatched_teams) else 0


if __name__ == "__main__":
    sys.exit(main())
