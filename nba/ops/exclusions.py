# ruff: noqa: E501
"""Dated data-quality exclusions for the forward log (read-only).

``ops/forward_exclusions.yaml`` lists entries; this module turns an entry into the exact
``market_at_prediction`` keys it excludes, by rule, against ``market_asof.duckdb`` opened read-only.
Nothing here writes, withdraws a forecast or touches an outcome score: an exclusion removes a row
from the model-versus-market comparison only (docs/DAILY_PIPELINE.md, "Data-quality exclusions").

    python -m nba.ops.exclusions check            # per entry: rows selected, by kind and game
    python -m nba.ops.exclusions keys --id ID     # the keys, one JSON object per line

Readers wired to it (2026-10-24, Devin's go): the parlay shadow track record (``nba.parlay.shadow.track_record``,
used by ``evaluate``, ``analyze`` and the assistant's ``track_record`` tool) drops the shadow rows of a
stale-priced slate; the AVG shadow's daily display splits its scored rows at the stints merge
(``avg_split``); the forward scorecard prints the rules in force and what it dropped (nothing: it compares to
sportsbook closing odds, not Kalshi). The weekly monitor's model-vs-Kalshi table reads ``market_at_prediction``
through ``market_key_set`` / ``drop_excluded``. Outcome scoring (forward_predictions, forward_scores, settle)
never imports this module.
"""

from __future__ import annotations

import argparse
import bisect
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXCLUSIONS = ROOT / "ops" / "forward_exclusions.yaml"
MARKET_DB = ROOT / "data" / "markets" / "market_asof.duckdb"
KALSHI_DB = ROOT / "data" / "kalshi" / "kalshi.duckdb"
_T = TypeVar("_T")
GAME_TARGET = "win_prob_home"
#: A Kalshi snapshot with at least this many tickers is a FULL snapshot (full ones hold ~3,700; the
#: partial 2026-10-23 21:47Z one held 274). A constant, not a YAML field: entries are append-only.
FULL_SNAPSHOT_MIN_TICKERS = 1000


@dataclass(frozen=True)
class Exclusion:
    id: str
    reason: str
    slate_date: date
    max_quote_age_s: float
    applies_to: tuple[str, ...]


@dataclass(frozen=True)
class Split:
    """A dated split of a summary (reported, nothing dropped): rows before ``split_at_utc`` ran on bad inputs."""

    id: str
    split_at_utc: datetime  # naive UTC, like every stored timestamp
    applies_to: tuple[str, ...]


def load_splits(path: Path = EXCLUSIONS) -> list[Split]:
    raw = yaml.safe_load(path.read_text()) or {}
    out = []
    for e in raw.get("splits", []):
        at = e["split_at_utc"]
        out.append(
            Split(
                id=str(e["id"]),
                split_at_utc=at if isinstance(at, datetime) else datetime.fromisoformat(str(at)),
                applies_to=tuple(e.get("applies_to", ())),
            )
        )
    return out


def load_exclusions(path: Path = EXCLUSIONS) -> list[Exclusion]:
    raw = yaml.safe_load(path.read_text()) or {}
    out = []
    for e in raw.get("exclusions", []):
        out.append(
            Exclusion(
                id=str(e["id"]),
                reason=str(e["reason"]),
                slate_date=e["slate_date"]
                if isinstance(e["slate_date"], date)
                else date.fromisoformat(str(e["slate_date"])),
                max_quote_age_s=float(e["max_quote_age_s"]),
                applies_to=tuple(e.get("applies_to", ())),
            )
        )
    ids = [e.id for e in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate exclusion ids in {path}")
    return out


def _min_prop_age(prop_json: str | None) -> float | None:
    if not prop_json:
        return None
    ages = [q["age_s"] for q in json.loads(prop_json) if q.get("age_s") is not None]
    return min(ages) if ages else None


def stale_price_keys(exclusion: Exclusion, market_db: Path = MARKET_DB) -> list[dict[str, Any]]:
    """Keys of the forward ``market_at_prediction`` rows of the slate whose newest quote is too old.

    Rows with no quote at all are not "stale": they carry their own ``reason`` and are not priced.
    """
    con = duckdb.connect(str(market_db), read_only=True)
    try:
        rows = con.execute(
            """SELECT run_id, game_id, model_name, version, target, player_id, made_at, ml_age_s,
                      prop_json
               FROM market_at_prediction
               WHERE capture_kind = 'forward' AND event_date = ?
               ORDER BY game_id, made_at, model_name, target, player_id""",
            [exclusion.slate_date],
        ).fetchall()
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for run_id, gid, model, version, target, pid, made_at, ml_age, prop_json in rows:
        age = ml_age if target == GAME_TARGET else _min_prop_age(prop_json)
        if age is not None and age > exclusion.max_quote_age_s:
            out.append(
                {
                    "exclusion": exclusion.id,
                    "reason": exclusion.reason,
                    "run_id": run_id,
                    "game_id": gid,
                    "model_name": model,
                    "version": version,
                    "target": target,
                    "player_id": pid,
                    "made_at": made_at.isoformat(),
                    "quote_age_s": round(float(age), 1),
                }
            )
    return out


def market_key_tuple(k: dict[str, Any]) -> tuple[str, str, str, str, int, str]:
    return (
        str(k["game_id"]),
        str(k["model_name"]),
        str(k["version"]),
        str(k["target"]),
        int(k["player_id"]),
        str(k["made_at"]),
    )


def market_key_set(
    market_db: Path = MARKET_DB, path: Path = EXCLUSIONS
) -> dict[str, set[tuple[str, str, str, str, int, str]]]:
    """``{rule id: keys}`` for every stale-price entry, key = (game_id, model_name, version, target,
    player_id, made_at ISO). Empty when ``market_db`` does not exist (nothing to compare either)."""
    if not Path(market_db).exists():
        return {}
    return {
        e.id: {market_key_tuple(k) for k in stale_price_keys(e, Path(market_db))}
        for e in load_exclusions(path)
        if e.reason == "stale_price" and "market_at_prediction" in e.applies_to
    }


def drop_excluded(
    rows: Iterable[_T],
    key_of: Callable[[_T], tuple[str, str, str, str, int, str]],
    excluded: dict[str, set[tuple[str, str, str, str, int, str]]],
) -> tuple[list[_T], dict[str, int]]:
    """(kept rows, {rule id: rows dropped}); a row hit by two rules counts for the first."""
    kept: list[_T] = []
    dropped: dict[str, int] = {}
    for r in rows:
        k = key_of(r)
        hit = next((rid for rid, ks in excluded.items() if k in ks), None)
        if hit is None:
            kept.append(r)
        else:
            dropped[hit] = dropped.get(hit, 0) + 1
    return kept, dropped


def drop_by_id(
    rows: Iterable[_T], id_of: Callable[[_T], str], excluded: dict[str, set[str]]
) -> tuple[list[_T], dict[str, int]]:
    """(kept rows, {rule id: rows dropped}) for id-keyed readers (shadow_predictions)."""
    kept: list[_T] = []
    dropped: dict[str, int] = {}
    for r in rows:
        sid = id_of(r)
        hit = next((rid for rid, ids in sorted(excluded.items()) if sid in ids), None)
        if hit is None:
            kept.append(r)
        else:
            dropped[hit] = dropped.get(hit, 0) + 1
    return kept, dropped


def _full_snapshot_times(kalshi_db: Path, lo: datetime, hi: datetime) -> list[datetime]:
    from nba.markets.asof import open_read_only

    con = open_read_only(kalshi_db, copy_fallback=True)
    try:
        rows = con.execute(
            """SELECT ts FROM kalshi_prices WHERE ts >= ? AND ts <= ?
               GROUP BY ts HAVING count(*) >= ? ORDER BY ts""",
            [lo, hi, FULL_SNAPSHOT_MIN_TICKERS],
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def stale_shadow_ids(
    exclusion: Exclusion, paper_con: duckdb.DuckDBPyConnection, kalshi_db: Path = KALSHI_DB
) -> list[str]:
    """``shadow_id`` of the slate's ``shadow_predictions`` rows (both sides, settled or not) logged more
    than ``max_quote_age_s`` after the newest FULL Kalshi snapshot. Read-only. [] when the shadow table,
    the slate's rows or ``kalshi_db`` are absent (the Kalshi file is opened only if candidate rows exist)."""
    try:
        rows = paper_con.execute(
            "SELECT shadow_id, created_at FROM shadow_predictions WHERE shadow_id LIKE ?",
            [f"{exclusion.slate_date.isoformat()}|%"],
        ).fetchall()
    except duckdb.CatalogException:
        return []
    if not rows or not Path(kalshi_db).exists():
        return []
    times = _full_snapshot_times(
        Path(kalshi_db),
        min(r[1] for r in rows) - timedelta(days=2),
        max(r[1] for r in rows),
    )
    out = []
    for sid, created in rows:
        i = bisect.bisect_left(times, created)  # snapshots strictly before the row
        age = (created - times[i - 1]).total_seconds() if i else float("inf")
        if age > exclusion.max_quote_age_s:
            out.append(str(sid))
    return sorted(out)


def shadow_exclusions(
    paper_con: duckdb.DuckDBPyConnection,
    kalshi_db: Path = KALSHI_DB,
    path: Path = EXCLUSIONS,
) -> dict[str, set[str]]:
    """``{rule id: shadow_ids}`` for every stale-price entry that applies to ``shadow_predictions``."""
    return {
        e.id: set(stale_shadow_ids(e, paper_con, kalshi_db))
        for e in load_exclusions(path)
        if e.reason == "stale_price" and "shadow_predictions" in e.applies_to
    }


def avg_split(con: duckdb.DuckDBPyConnection, path: Path = EXCLUSIONS) -> list[dict[str, Any]]:
    """Scored AVG rows (``forward_avg_scores``, status scored) split by ``made_at`` at each AVG split.
    The scored row is the latest pre-tip row, so ``made_at`` is the moment its inputs were built.
    Read-only; [] when the table is absent."""
    out = []
    for sp in load_splits(path):
        if not any(a.startswith("AVG_SHADOW_LIVE_2") for a in sp.applies_to):
            continue
        try:
            before, after = con.execute(
                "SELECT count(*) FILTER (WHERE made_at < ?), count(*) FILTER (WHERE made_at >= ?) "
                "FROM forward_avg_scores WHERE status = 'scored'",
                [sp.split_at_utc, sp.split_at_utc],
            ).fetchone()  # type: ignore[misc]
        except duckdb.CatalogException:
            return []
        out.append(
            {
                "id": sp.id,
                "split_at_utc": sp.split_at_utc,
                "before": int(before),
                "after": int(after),
            }
        )
    return out


def describe_dropped(by_rule: dict[str, int], what: str) -> str:
    if not by_rule:
        return f"data-quality exclusions: 0 {what} dropped"
    parts = ", ".join(f"{n} by {rid}" for rid, n in sorted(by_rule.items()))
    return f"data-quality exclusions: {sum(by_rule.values())} {what} dropped ({parts})"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["check", "keys"])
    ap.add_argument("--paper-db", default=str(ROOT / "data" / "parlay" / "paper_trades.duckdb"))
    ap.add_argument("--kalshi-db", default=str(KALSHI_DB))
    ap.add_argument("--id", default=None)
    ap.add_argument("--file", default=str(EXCLUSIONS))
    ap.add_argument("--market-db", default=str(MARKET_DB))
    a = ap.parse_args(argv)
    entries = [e for e in load_exclusions(Path(a.file)) if a.id in (None, e.id)]
    if not entries:
        print("no matching exclusion")
        return 1
    for e in entries:
        keys = stale_price_keys(e, Path(a.market_db))
        if a.cmd == "keys":
            for k in keys:
                print(json.dumps(k, sort_keys=True))
            continue
        games = sorted({k["game_id"] for k in keys})
        kinds = {
            "game": sum(1 for k in keys if k["target"] == GAME_TARGET),
            "prop": sum(1 for k in keys if k["target"] != GAME_TARGET),
        }
        print(f"{e.id} ({e.reason}, slate {e.slate_date}): {len(keys)} rows {kinds} games={games}")
        if "shadow_predictions" in e.applies_to and Path(a.paper_db).exists():
            pcon = duckdb.connect(a.paper_db, read_only=True)
            try:
                ids = stale_shadow_ids(e, pcon, Path(a.kalshi_db))
            finally:
                pcon.close()
            print(
                f"  shadow_predictions: {len(ids)} rows ({sum(1 for i in ids if i.endswith('|yes'))} yes)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
