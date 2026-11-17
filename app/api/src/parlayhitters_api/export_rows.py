"""Pure helpers that turn export rows into what the app keeps: timestamps, quotes and the posted
main line, names, model stamps, and the selective prop_lines read. No state."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

Json = dict[str, Any]


ET = ZoneInfo("America/New_York")


TWO_WORD_NICKNAMES = ("Trail Blazers",)


#: Sportsbooks first, in this order, for the one price shown beside a prop; any other book
#: (exchanges such as kalshi, novig, polymarket) follows alphabetically. Every book's quote is
#: still shown on the leg screen.
BOOK_ORDER = ("draftkings", "fanduel", "betmgm", "caesars", "espnbet", "fanatics")


def _schema_at_least_1(value: Any) -> bool:
    """meta best_model_schema as an integer >= 1 (a later schema only adds to this one)."""
    try:
        return int(str(value).strip()) >= 1
    except ValueError:
        return False


def _utc_iso(ts: datetime) -> str:
    """A naive-UTC timestamp (as the export stores them) as an ISO string with Z."""
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _et_to_utc_iso(ts: datetime) -> str:
    return ts.replace(tzinfo=ET).astimezone(UTC).isoformat(timespec="seconds")


def _table(con: duckdb.DuckDBPyConnection, name: str) -> list[Json]:
    cur = con.execute(f'SELECT * FROM "{name}"')  # noqa: S608 - fixed table names
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


#: The prop_lines columns the app reads (``_quote`` and the run/prop key); nothing else is kept.
PROP_LINE_COLS = (
    "run_id", "game_id", "player_id", "stat", "book", "line", "side", "price_american",
    "unusable", "unusable_reason", "is_alternate", "minutes_before_tip", "snapshot_ts",
)  # fmt: skip


def _prop_lines_for(
    con: duckdb.DuckDBPyConnection, props: dict[tuple[str, int, str], Json]
) -> list[Json]:
    """Only the prop_lines rows a shown prop uses: its quote run (``lines_run_id``, else its own
    run_id) and its game, player and stat; only the columns the app reads.

    The same rows ``_quotes_by_prop`` would pick out of the whole table, selected in DuckDB
    instead: the real table has about a million rows, and reading all of them as Python dicts
    held about 1 GB at peak for a few hundred thousand used ones."""
    keys = sorted({(pr.get("lines_run_id") or pr["run_id"], *k) for k, pr in props.items()})
    if not keys:
        return []
    runs, gids, pids, stats = (list(col) for col in zip(*keys, strict=True))
    cols = ", ".join(f'p."{c}"' for c in PROP_LINE_COLS)
    cur = con.execute(
        f"SELECT {cols} FROM prop_lines p JOIN ("  # noqa: S608 - fixed column names
        "  SELECT unnest(?::VARCHAR[]) AS r, unnest(?::VARCHAR[]) AS g,"
        "         unnest(?::INTEGER[]) AS pl, unnest(?::VARCHAR[]) AS s"
        ") w ON p.run_id = w.r AND p.game_id = w.g AND p.player_id = w.pl AND p.stat = w.s"
        " ORDER BY p.rowid",  # the table's own order, as a full read returns it
        [runs, gids, [int(x) for x in pids], stats],
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _lead_mean(player: Json) -> float:
    """The forecast mean used to order a team's players: points if he has them, else any stat."""
    props = player["props"]
    lead = props.get("pts") or next(iter(props.values()))
    return float(lead["forecast"]["mean"] or 0.0)


def _book_rank(book: str) -> tuple[int, str]:
    return (BOOK_ORDER.index(book) if book in BOOK_ORDER else len(BOOK_ORDER), book)


def _quote(r: Json) -> Json:
    """A prop_lines row as the app keeps it. An unusable quote keeps no price at all."""
    price = int(r["price_american"])
    # Beyond the exporter's own flag: a number that is not an American price cannot be shown.
    bad = r["unusable"] or price == 0 or -100 < price < 100
    return {
        "book": r["book"],
        "line": float(r["line"]),
        "side": r["side"],
        "price": None if bad else price,
        "unusable": bool(bad),
        "reason": r["unusable_reason"] or ("not_american_odds" if bad else None),
        "alt": bool(r["is_alternate"]),
        "minutes_before_tip": r["minutes_before_tip"],
        "snapshot_utc": None if r["snapshot_ts"] is None else _utc_iso(r["snapshot_ts"]),
    }


def main_line(quotes: list[Json]) -> float | None:
    """The posted main line: among usable non-alternate quotes, the line the most books carry;
    a tie goes to the line nearest the median of the tied lines, then the lower one. None when no
    book posted a usable main line."""
    books: dict[float, set[str]] = defaultdict(set)
    for q in quotes:
        if not q["alt"] and not q["unusable"]:
            books[q["line"]].add(q["book"])
    if not books:
        return None
    top = max(len(b) for b in books.values())
    tied = sorted(ln for ln, b in books.items() if len(b) == top)
    mid = statistics.median(tied)
    return min(tied, key=lambda ln: (abs(ln - mid), ln))


def line_entries(quotes: list[Json], main: float | None) -> list[Json]:
    """One price entry per line for the breakdown's ladder: for each side, the best-ranked book
    with a usable price there, preferring one book that has both sides."""
    by_line: dict[float, dict[str, dict[str, Json]]] = defaultdict(lambda: defaultdict(dict))
    for q in quotes:
        if not q["unusable"]:
            by_line[q["line"]][q["book"]][q["side"]] = q
    out: list[Json] = []
    for ln in sorted(by_line):
        books = by_line[ln]
        both = sorted((b for b, s in books.items() if "over" in s and "under" in s), key=_book_rank)
        pick: dict[str, Json] = {}
        for side in ("over", "under"):
            if both:
                pick[side] = books[both[0]][side]
            else:
                cands = sorted((b for b, s in books.items() if side in s), key=_book_rank)
                if cands:
                    pick[side] = books[cands[0]][side]
        ov, un = pick.get("over"), pick.get("under")
        out.append(
            {
                "main": ln == main,
                "point": ln,
                "over_price": None if ov is None else ov["price"],
                "under_price": None if un is None else un["price"],
                "over_book": None if ov is None else ov["book"],
                "under_book": None if un is None else un["book"],
                "snapshot_utc": (ov or un or {}).get("snapshot_utc"),
            }
        )
    return out


def _split_name(full: str) -> tuple[str, str]:
    for nick in TWO_WORD_NICKNAMES:
        if full.endswith(" " + nick):
            return full[: -len(nick) - 1], nick
    head, _, tail = full.rpartition(" ")
    return (head, tail) if head else ("", full)


def _stamp_text(row: Json) -> str:
    bits = [f"{row['model_name']} {row['version']}"]
    if row.get("stamp_fit_through"):
        bits.append(f"fit through {row['stamp_fit_through']}")
    if row.get("stamp_n_train"):
        bits.append(f"n_train {row['stamp_n_train']}")
    if row.get("stamp_weights_sha256"):
        bits.append(f"weights {str(row['stamp_weights_sha256'])[:12]}")
    if row.get("bundle_month"):
        bits.append(f"bundle {row['bundle_month']}")
    bits.append(f"made {_utc_iso(row['made_at'])}")
    return "; ".join(bits)


def _pick_latest(
    rows: list[Json], key: Callable[[Json], Any], prefer: Callable[[Json], bool]
) -> dict[Any, Json]:
    """Per key, the usable row: the preferred kind first, then the newest made_at."""
    best: dict[Any, tuple[tuple[bool, datetime, str], Json]] = {}
    for r in rows:
        k = key(r)
        rank = (prefer(r), r["made_at"], r["run_id"])
        cur = best.get(k)
        if cur is None or rank > cur[0]:
            best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


def _display_name(pid: int, name: object) -> str:
    text = str(name or "").strip()
    if not text or re.fullmatch(r"Player \d+", text):
        return f"Unnamed player (id {pid})"
    return text
