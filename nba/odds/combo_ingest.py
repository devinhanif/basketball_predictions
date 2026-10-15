"""Ingest the raw COMBO_LINE_CAL R1 pull into ``odds_history.duckdb`` (six new markets only).

Reads the write-once raw files (no network, no key), parses each with ``parse_event_odds`` (the
same parser as every other history phase), and writes ONLY the rows of the six combo / defensive
markets. Idempotent: it deletes those markets' rows (and nothing else) and inserts, in one
transaction. Refuses any season-2025 row. Takes ``data/ops/heavy.lock`` (mkdir, own pid) and
waits for it; backs the DB up first and records both shas. Other markets' row counts are
checked unchanged.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.odds.combo_pull import (
    SHORT,
    ComboConfig,
    HeavyLock,
    git_sha,
    load_combo_config,
    sha256_file,
)
from nba.odds.history_pull import DDL
from nba.odds.the_odds_api import (
    HOLDOUT_SEASON,
    ROW_SCHEMA,
    HistoryError,
    HoldoutRefused,
    default_resolver,
    load_raw,
    parse_event_odds,
    parse_ts,
)
from nba.odds.theoddsapi import NameResolver


def parse_raw_dir(
    cc: ComboConfig, raw_dir: Path, resolver: NameResolver
) -> tuple[pl.DataFrame, Counter[str], Counter[str], int]:
    """``(rows, reasons, unresolved names, n odds files)`` for every ``odds_*`` file in raw_dir."""
    frames: list[pl.DataFrame] = []
    reasons: Counter[str] = Counter()
    unresolved: Counter[str] = Counter()
    n = 0
    for p in sorted(raw_dir.glob("*/*/odds_*.json.gz")):
        blob = load_raw(p)
        season = int(blob["season"])
        if season == HOLDOUT_SEASON or season not in cc.seasons:
            raise HoldoutRefused(f"{p} is season {season}; refusing")
        at = parse_ts(blob["params"]["date"])
        if at is None:
            raise HistoryError(f"{p}: no params.date")
        n += 1
        res = parse_event_odds(
            blob.get("body"),
            season=season,
            game_id=str(blob["game_id"]),
            snapshot_kind=str(blob["snapshot_kind"]),
            requested_at=at,
            raw_file=f"{raw_dir.name}/{p.relative_to(raw_dir)}",
            resolver=resolver,
        )
        reasons.update(res.reasons)
        unresolved.update(res.unresolved)
        if res.rows.height:
            frames.append(res.rows)
    rows = pl.concat(frames) if frames else pl.DataFrame(schema=ROW_SCHEMA)
    bad = rows.filter(~pl.col("market").is_in(list(cc.markets)))
    if bad.height:
        raise HistoryError(f"{bad.height} parsed rows are outside the six markets")
    if rows.filter(pl.col("season") >= HOLDOUT_SEASON).height:
        raise HoldoutRefused("a parsed row is season >= 2025")
    return rows, reasons, unresolved, n


def ingest(
    cc: ComboConfig,
    raw_dir: Path,
    db: Path,
    backup_dir: Path,
    out_json: Path | None = None,
    *,
    lock: bool = True,
    lock_wait_s: float = 14_400.0,
    lock_retry_s: float = 60.0,
    log: Callable[[str], None] = print,
) -> list[str]:
    resolver = default_resolver()
    rows, reasons, unresolved, n_files = parse_raw_dir(cc, raw_dir, resolver)  # no lock needed
    out = [f"parsed {n_files} odds files -> {rows.height:,} rows (six markets only)"]
    mk = list(cc.markets)

    def go() -> dict[str, Any]:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        bak = backup_dir / f"odds_history_{stamp}.duckdb"
        sha_before = sha256_file(db)
        shutil.copy2(db, bak)
        if sha256_file(bak) != sha_before:
            raise HistoryError("backup sha differs from the source")
        con = duckdb.connect(str(db))
        try:
            con.execute(DDL)
            ph = ",".join("?" for _ in mk)
            other_before = con.execute(
                f"SELECT count(*) FROM odds_history WHERE market NOT IN ({ph})", mk
            ).fetchone()
            prior = con.execute(
                f"SELECT count(*) FROM odds_history WHERE market IN ({ph})", mk
            ).fetchone()
            con.execute("BEGIN")
            con.execute(f"DELETE FROM odds_history WHERE market IN ({ph})", mk)
            if rows.height:
                con.register("new_rows", rows.select(list(ROW_SCHEMA)).to_arrow())
                con.execute("INSERT INTO odds_history SELECT * FROM new_rows")
                con.unregister("new_rows")
            other_after = con.execute(
                f"SELECT count(*) FROM odds_history WHERE market NOT IN ({ph})", mk
            ).fetchone()
            if other_before != other_after:
                con.execute("ROLLBACK")
                raise HistoryError(f"other markets changed {other_before} -> {other_after}")
            n25 = con.execute(
                f"SELECT count(*) FROM odds_history WHERE season >= ? AND market IN ({ph}) ",
                [HOLDOUT_SEASON, *mk],
            ).fetchone()
            if n25 and n25[0]:
                con.execute("ROLLBACK")
                raise HoldoutRefused("season-2025 rows present for the six markets")
            con.execute("COMMIT")
            counts = con.execute(
                f"SELECT market, book, season, snapshot_kind, count(*), "
                f"count(DISTINCT game_id), count(player_id), count(*) - count(player_id) "
                f"FROM odds_history WHERE market IN ({ph}) GROUP BY ALL ORDER BY ALL",
                mk,
            ).fetchall()
        finally:
            con.close()
        return {
            "backup": str(bak),
            "sha_before": sha_before,
            "sha_after": sha256_file(db),
            "other_rows": other_after[0] if other_after else None,
            "prior_rows_in_six": prior[0] if prior else None,
            "counts": counts,
        }

    if lock:
        with HeavyLock(cc.lock_dir, "combo ingest odds_history", lock_wait_s, lock_retry_s):
            res = go()
    else:
        res = go()
    out.append(f"backup {res['backup']} sha256 {res['sha_before']}")
    out.append(
        f"db sha256 after {res['sha_after']}; other-market rows unchanged: {res['other_rows']:,}"
    )
    out.append(f"rows previously present for the six markets: {res['prior_rows_in_six']}")
    out.append("market | book | season | snapshot | rows | games | resolved | unresolved")
    out += [
        f"{SHORT[m]} | {b} | {s} | {k} | {r:,} | {g} | {ok:,} | {bad:,}"
        for m, b, s, k, r, g, ok, bad in res["counts"]
    ]
    out.append(f"parse reasons: {dict(reasons)}")
    out.append(
        f"unresolved names: distinct={len(unresolved)} rows={sum(unresolved.values())}; top: "
        + ", ".join(f"{k}={v}" for k, v in unresolved.most_common(15))
    )
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(
                {
                    "code_git_sha": git_sha(),
                    "raw_dir": raw_dir.name,
                    "n_odds_files": n_files,
                    "rows": rows.height,
                    "backup": res["backup"],
                    "sha_before": res["sha_before"],
                    "sha_after": res["sha_after"],
                    "counts": [list(c) for c in res["counts"]],
                    "reasons": dict(reasons),
                    "unresolved": dict(unresolved),
                },
                indent=1,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m nba.odds.combo_ingest")
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--out-json", type=Path, default=None)
    a = ap.parse_args(argv)
    cc = load_combo_config()
    db = a.db or cc.data_root / "data" / "odds" / "odds_history.duckdb"
    for line in ingest(cc, a.raw_dir, db, cc.data_root / "data" / "backups", a.out_json):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
