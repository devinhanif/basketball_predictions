"""Build a structural + content manifest of the DuckDB tables and parquet caches.

Read-only: the database is opened with ``read_only=True`` (retrying with a bounded backoff
when a writer holds the lock) and nothing is ever written to it.

Per table: schema hash, row count, primary/natural-key duplicates, date ranges, per-column
null rate overall / per season, and (for player-game tables) null rate split by played
(minutes >= threshold) vs not. A column whose NULL pattern depends on whether the player
played is the fingerprint of a leaked feature (see docs/BEST_PRACTICES.md, "Audit missingness").
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "datamanifest.yaml"
MANIFEST_VERSION = 1


class DataManifestLocked(RuntimeError):
    """The database is locked by a writer and the bounded retry budget ran out."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load the datamanifest config (thresholds, keys, exclusions)."""
    cfg: dict[str, Any] = yaml.safe_load((path or DEFAULT_CONFIG).read_text())
    return cfg


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(db_path: Path, cfg: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    retry = cfg.get("lock_retry", {})
    budget = float(retry.get("max_wait_s", 30))
    delay = float(retry.get("first_delay_s", 1.0))
    waited = 0.0
    while True:
        try:
            return duckdb.connect(str(db_path), read_only=True)
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower() or waited + delay > budget:
                raise DataManifestLocked(f"{db_path}: still locked after {waited:.0f}s") from exc
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 8.0)


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"no row returned: {sql[:80]}")
    return row


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 6)


def _describe(con: duckdb.DuckDBPyConnection, rel: str) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]


def schema_hash(columns: list[tuple[str, str]]) -> str:
    """Order-independent hash of (column name, type) pairs."""
    blob = "|".join(f"{n}:{t}" for n, t in sorted(columns))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _null_counts_sql(cols: list[str]) -> str:
    return ", ".join(f"count({_q(c)})" for c in cols)


def _table_manifest(
    con: duckdb.DuckDBPyConnection,
    name: str,
    rel: str,
    cfg: dict[str, Any],
    *,
    pk_cols: list[str] | None,
    have_games: bool,
    have_pgs: bool,
    fingerprint: bool,
) -> dict[str, Any]:
    columns = _describe(con, rel)
    names = [c for c, _ in columns]
    types = dict(columns)
    minutes = float(cfg.get("played_minutes", 5.0))
    has_game = "game_id" in names
    joins = ""
    sel = ["t.*"]
    if "season" in names:
        sel.append("t.season AS __season")
    elif has_game and have_games and name != "games":
        joins += " LEFT JOIN main.games g ON g.game_id = t.game_id"
        sel.append("g.season AS __season")
    played = False
    if has_game and "player_id" in names:
        if "minutes" in names:
            sel.append(f"(coalesce(t.minutes, 0) >= {minutes}) AS __played")
            played = True
        elif have_pgs:
            joins += (
                " LEFT JOIN (SELECT game_id, player_id, max(coalesce(minutes, 0)) AS mn"
                " FROM main.player_game_stats GROUP BY 1, 2) p"
                " ON p.game_id = t.game_id AND p.player_id = t.player_id"
            )
            sel.append(f"CASE WHEN p.mn IS NULL THEN NULL ELSE p.mn >= {minutes} END AS __played")
            played = True
    src = f"(SELECT {', '.join(sel)} FROM {rel} t{joins})"
    has_season = any(s.endswith("__season") for s in sel)

    n_rows = int(_one(con, f"SELECT count(*) FROM {rel}")[0])
    out: dict[str, Any] = {
        "schema_hash": schema_hash(columns),
        "row_count": n_rows,
        "primary_key": pk_cols,
    }
    if pk_cols and n_rows:
        key = ", ".join(_q(c) for c in pk_cols)
        distinct = _one(con, f"SELECT count(*) FROM (SELECT DISTINCT {key} FROM {rel})")[0]
        out["pk_duplicates"] = n_rows - int(distinct)
    else:
        out["pk_duplicates"] = None
    if fingerprint:
        h = _one(con, f"SELECT bit_xor(hash(t)), sum(hash(t)::HUGEINT) FROM {rel} t")
        out["fingerprint"] = "empty" if not n_rows else f"{int(h[0]):016x}-{int(h[1]) % 2**64:016x}"
    else:
        out["fingerprint"] = None

    date_cols = [c for c, t in columns if t in ("DATE", "TIMESTAMP") or t.startswith("TIMESTAMP")]
    out["date_range"] = {}
    for c in date_cols:
        lo, hi = _one(con, f"SELECT min({_q(c)}), max({_q(c)}) FROM {rel}")
        out["date_range"][c] = [str(lo) if lo is not None else None, str(hi) if hi else None]

    # overall null rates
    row = _one(con, f"SELECT count(*), {_null_counts_sql(names)} FROM {src}")
    total = int(row[0])
    col_info: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(names):
        nn = int(row[i + 1])
        col_info[c] = {"type": types[c], "null_rate": _r(1 - nn / total) if total else None}

    if has_season:
        per_season: dict[str, Any] = {}
        game_sql = "count(DISTINCT game_id)" if has_game else "NULL"
        rows = con.execute(
            f"SELECT __season, count(*), {game_sql}, {_null_counts_sql(names)} FROM {src} "
            "WHERE __season IS NOT NULL GROUP BY 1 ORDER BY 1"
        ).fetchall()
        games_by_season: dict[int, int] = {}
        if have_games:
            games_by_season = {
                int(s): int(n)
                for s, n in con.execute(
                    "SELECT season, count(*) FROM main.games GROUP BY 1"
                ).fetchall()
            }
        out["seasons"] = {}
        for r in rows:
            s = str(r[0])
            n = int(r[1])
            ent: dict[str, Any] = {"rows": n}
            if has_game:
                ent["games"] = int(r[2])
                of = games_by_season.get(int(r[0]))
                ent["coverage"] = _r(int(r[2]) / of) if of else None
            out["seasons"][s] = ent
            for i, c in enumerate(names):
                per_season.setdefault(c, {})[s] = _r(1 - int(r[3 + i]) / n)
        for c in names:
            col_info[c]["null_rate_by_season"] = per_season.get(c, {})

    if played:
        rows = con.execute(
            f"SELECT __played, count(*), {_null_counts_sql(names)} FROM {src} "
            "WHERE __played IS NOT NULL GROUP BY 1"
        ).fetchall()
        groups = {bool(r[0]): r for r in rows}
        n_p = int(groups[True][1]) if True in groups else 0
        n_u = int(groups[False][1]) if False in groups else 0
        out["played_split"] = {"n_played": n_p, "n_unplayed": n_u, "minutes_threshold": minutes}
        for i, c in enumerate(names):
            if c == "minutes":
                continue
            rp = _r(1 - int(groups[True][2 + i]) / n_p) if n_p else None
            ru = _r(1 - int(groups[False][2 + i]) / n_u) if n_u else None
            col_info[c]["null_rate_played"] = rp
            col_info[c]["null_rate_unplayed"] = ru
            ok = min(n_p, n_u) >= int(cfg["thresholds"]["min_group_rows"])
            col_info[c]["asymmetry"] = (
                _r(abs(rp - ru)) if ok and rp is not None and ru is not None else None
            )
    out["columns"] = col_info
    return out


def _dir_caches(data_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not data_dir.is_dir():
        return out
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir() and p.name != "manifests"):
        files = size = 0
        for root, _dirs, fnames in os.walk(d):
            for fn in fnames:
                try:
                    size += os.stat(os.path.join(root, fn)).st_size
                    files += 1
                except OSError:
                    continue
        out[d.name] = {"files": files, "bytes": size}
    return out


def compute_data_version(tables: dict[str, Any], cfg: dict[str, Any]) -> str:
    """Short hash over schema + content fingerprints of the DB input tables."""
    skip = set(cfg.get("exclude_from_version", []))
    parts = {
        n: [t["schema_hash"], t["fingerprint"]]
        for n, t in sorted(tables.items())
        if n not in skip and not n.startswith("parquet:")
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def build_manifest(
    db_path: Path | str,
    cfg: dict[str, Any] | None = None,
    *,
    parquet_paths: list[Path] | None = None,
    fingerprint: bool = True,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the manifest dict for ``db_path`` (read-only) plus optional parquet files."""
    cfg = cfg or load_config()
    con = _connect(Path(db_path), cfg)
    try:
        names = [
            str(r[0])
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY 1"
            ).fetchall()
        ]
        pks: dict[str, list[str]] = {}
        for tname, cnames in con.execute(
            "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
            "WHERE constraint_type = 'PRIMARY KEY'"
        ).fetchall():
            pks[str(tname)] = [str(c) for c in cnames]
        have_games = "games" in names
        have_pgs = "player_game_stats" in names
        tables: dict[str, Any] = {}
        for n in names:
            pk = pks.get(n) or cfg.get("natural_keys", {}).get(n)
            tables[n] = _table_manifest(
                con,
                n,
                f"main.{_q(n)}",
                cfg,
                pk_cols=pk,
                have_games=have_games,
                have_pgs=have_pgs,
                fingerprint=fingerprint,
            )
        for pq in parquet_paths or []:
            rel = f"read_parquet('{str(pq).replace(chr(39), '')}')"
            cols = [c for c, _ in _describe(con, rel)]
            key = list(cfg.get("parquet_key", []))
            tables[f"parquet:{pq.name}"] = _table_manifest(
                con,
                pq.stem,
                rel,
                cfg,
                pk_cols=key if all(k in cols for k in key) else None,
                have_games=have_games,
                have_pgs=have_pgs,
                fingerprint=fingerprint,
            )
    finally:
        con.close()
    return {
        "manifest_version": MANIFEST_VERSION,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "db_path": str(db_path),
        "data_version": compute_data_version(tables, cfg),
        "tables": tables,
        "dir_caches": _dir_caches(data_dir) if data_dir else {},
    }


def summary_view(m: dict[str, Any]) -> dict[str, Any]:
    """Small committed summary: schema + counts + null rates, no data values."""
    return m


def latest_data_version(
    manifest_dir: Path | None = None, summary_path: Path | None = None
) -> str | None:
    """Data version of the newest manifest on disk (no recompute); ``None`` if none exists."""
    mdir = manifest_dir or REPO_ROOT / "data" / "manifests"
    files = sorted(mdir.glob("*.json")) if mdir.is_dir() else []
    cand = files[-1] if files else (summary_path or REPO_ROOT / "docs/data_manifests/latest.json")
    try:
        v = json.loads(cand.read_text()).get("data_version")
    except (OSError, ValueError):
        return None
    return str(v) if v else None
