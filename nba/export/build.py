"""Build ``export.duckdb`` + ``parlay_of_day.json`` + ``manifest.json`` from ``nba.duckdb``.

Source is opened ``read_only=True`` (``reader.open_read_only``); the only files written are in the
output directory, each to a temp name in the same directory and then ``os.replace``d, the manifest
last. A failed build leaves yesterday's files untouched and removes its temp files.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.export import guard, reader
from nba.export.parlay import parlay_of_day

SCHEMA_VERSION = 2  # additive changes keep 2: the app refuses unknown versions
BEST_MODEL_SCHEMA = 1  # meta best_model_schema: prop_grids primary/bundle columns + p_play table
DB_NAME = "export.duckdb"
PARLAY_NAME = "parlay_of_day.json"
MANIFEST_NAME = "manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: table -> ordered (column, DuckDB type): the schema the app reads (docs/APP_ARCHITECTURE.md).
TABLES: dict[str, list[tuple[str, str]]] = {
    "meta": [("key", "VARCHAR"), ("value", "VARCHAR")],
    "games": [
        ("game_id", "VARCHAR"),
        ("season", "INTEGER"),
        ("game_date", "DATE"),
        ("tipoff", "TIMESTAMP"),
        ("home_team", "INTEGER"),
        ("away_team", "INTEGER"),
        ("final", "BOOLEAN"),
        ("home_pts", "INTEGER"),
        ("away_pts", "INTEGER"),
    ],
    "win_probs": [
        ("run_id", "VARCHAR"),
        ("made_at", "TIMESTAMP"),
        ("game_id", "VARCHAR"),
        ("tipoff", "TIMESTAMP"),
        ("lead_minutes", "DOUBLE"),
        ("model_name", "VARCHAR"),
        ("version", "VARCHAR"),
        ("p_home", "DOUBLE"),
        ("home_team", "INTEGER"),
        ("away_team", "INTEGER"),
        ("is_primary", "BOOLEAN"),
        ("fallback_reason", "VARCHAR"),
        ("is_latest", "BOOLEAN"),
        ("comparison_only", "BOOLEAN"),
        ("stamp_weights_sha256", "VARCHAR"),
        ("stamp_weights_scope", "VARCHAR"),
        ("stamp_data_version", "VARCHAR"),
        ("stamp_fit_through", "DATE"),
        ("stamp_n_train", "BIGINT"),
        ("stamp_git_sha", "VARCHAR"),
        ("stamp_git_dirty", "BOOLEAN"),
        ("stamp_stamped_at", "TIMESTAMP"),
        ("prediction_json", "VARCHAR"),
    ],
    "prop_grids": [
        ("run_id", "VARCHAR"),
        ("made_at", "TIMESTAMP"),
        ("game_id", "VARCHAR"),
        ("tipoff", "TIMESTAMP"),
        ("lead_minutes", "DOUBLE"),
        ("model_name", "VARCHAR"),
        ("version", "VARCHAR"),
        ("stat", "VARCHAR"),
        ("player_id", "INTEGER"),
        ("team_id", "INTEGER"),
        ("team_source", "VARCHAR"),
        ("mean", "DOUBLE"),
        ("std", "DOUBLE"),
        ("p_play", "DOUBLE"),
        ("proj_minutes", "DOUBLE"),
        ("q10", "DOUBLE"),
        ("q50", "DOUBLE"),
        ("q90", "DOUBLE"),
        ("q_grid", "DOUBLE[]"),
        ("p_ge", "VARCHAR"),
        ("p_ge_full_counts", "BIGINT[]"),
        ("p_ge_full_n", "BIGINT"),
        ("is_latest", "BOOLEAN"),
        ("comparison_only", "BOOLEAN"),
        ("stamp_weights_sha256", "VARCHAR"),
        ("stamp_weights_scope", "VARCHAR"),
        ("stamp_data_version", "VARCHAR"),
        ("stamp_fit_through", "DATE"),
        ("stamp_n_train", "BIGINT"),
        ("stamp_git_sha", "VARCHAR"),
        ("stamp_git_dirty", "BOOLEAN"),
        ("stamp_stamped_at", "TIMESTAMP"),
        ("prediction_json", "VARCHAR"),
        # best_model_schema 1 (DECISIONS 2026-10-14 21:30): which row the app shows
        ("source_table", "VARCHAR"),
        ("is_primary", "BOOLEAN"),
        ("primary_source", "VARCHAR"),
        ("primary_detail", "VARCHAR"),
        ("bundle_id", "VARCHAR"),
        ("bundle_made_at", "TIMESTAMP"),
        ("bundle_month", "VARCHAR"),
        ("freshness", "VARCHAR"),
        ("freshness_reason", "VARCHAR"),
        ("p_play_primary", "DOUBLE"),
        ("p_play_source", "VARCHAR"),
        ("lines_run_id", "VARCHAR"),  # run_id whose prop_lines belong to this row
    ],
    "p_play": [
        ("game_id", "VARCHAR"),
        ("player_id", "INTEGER"),
        ("team_id", "INTEGER"),
        ("run_id", "VARCHAR"),
        ("made_at", "TIMESTAMP"),
        ("tipoff", "TIMESTAMP"),
        ("lead_minutes", "DOUBLE"),
        ("p_play_primary", "DOUBLE"),
        ("p_play_source", "VARCHAR"),
        ("p_play_detail", "VARCHAR"),
        ("p_play_r", "DOUBLE"),
        ("p_play_prod", "DOUBLE"),
        ("model_version", "VARCHAR"),
        ("proj_minutes", "DOUBLE"),
        ("r_train_through", "DATE"),
        ("r_n_train", "INTEGER"),
    ],
    "model_stamps": [
        ("run_id", "VARCHAR"),
        ("model_name", "VARCHAR"),
        ("version", "VARCHAR"),
        ("made_at", "TIMESTAMP"),
        ("weights_sha256", "VARCHAR"),
        ("weights_scope", "VARCHAR"),
        ("data_version", "VARCHAR"),
        ("fit_through", "DATE"),
        ("n_train", "BIGINT"),
        ("git_sha", "VARCHAR"),
        ("git_dirty", "BOOLEAN"),
        ("stamped_at", "TIMESTAMP"),
    ],
    "settled_scores": [
        ("scored_at", "TIMESTAMP"),
        ("game_id", "VARCHAR"),
        ("game_date", "DATE"),
        ("season", "INTEGER"),
        ("model_name", "VARCHAR"),
        ("version", "VARCHAR"),
        ("target", "VARCHAR"),
        ("player_id", "INTEGER"),
        ("made_at", "TIMESTAMP"),
        ("y", "DOUBLE"),
        ("pred", "DOUBLE"),
        ("log_loss", "DOUBLE"),
        ("brier", "DOUBLE"),
        ("crps", "DOUBLE"),
        ("status", "VARCHAR"),
    ],
    "box_scores": [
        ("game_id", "VARCHAR"),
        ("player_id", "INTEGER"),
        ("team_id", "INTEGER"),
        ("minutes", "DOUBLE"),
        ("starter", "BOOLEAN"),
        ("pts", "INTEGER"),
        ("reb", "INTEGER"),
        ("ast", "INTEGER"),
        ("fg3m", "INTEGER"),
        ("stl", "INTEGER"),
        ("blk", "INTEGER"),
        ("tov", "INTEGER"),
        ("game_date", "DATE"),
        ("season", "INTEGER"),
    ],
    "prop_lines": [
        ("run_id", "VARCHAR"),
        ("made_at", "TIMESTAMP"),
        ("game_id", "VARCHAR"),
        ("player_id", "INTEGER"),
        ("stat", "VARCHAR"),
        ("book", "VARCHAR"),
        ("market", "VARCHAR"),
        ("is_alternate", "BOOLEAN"),
        ("line", "DOUBLE"),
        ("side", "VARCHAR"),
        ("price_american", "INTEGER"),
        ("implied_prob_raw", "DOUBLE"),
        ("snapshot_ts", "TIMESTAMP"),
        ("snapshot_kind", "VARCHAR"),
        ("last_update", "TIMESTAMP"),
        ("minutes_before_tip", "DOUBLE"),
        ("unusable", "BOOLEAN"),
        ("unusable_reason", "VARCHAR"),
    ],
    "injury_report": [
        ("game_id", "VARCHAR"),
        ("report_as_of_et", "TIMESTAMP"),
        ("player_id", "INTEGER"),
        ("team_id", "INTEGER"),
        ("status", "VARCHAR"),
        ("reason", "VARCHAR"),
    ],
    "players": [("player_id", "INTEGER"), ("full_name", "VARCHAR")],
    "teams": [("team_id", "INTEGER"), ("abbr", "VARCHAR"), ("full_name", "VARCHAR")],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_info(repo: Path = REPO_ROOT) -> dict[str, Any]:
    """HEAD sha and dirty flag from the local repo (no network); Nones if git is unavailable."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout.strip()
        )  # fmt: skip
        return {"git_sha": sha, "git_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"git_sha": None, "git_dirty": None}


def data_version() -> str | None:
    """Newest manifest's data_version, read from disk (no recompute)."""
    try:
        from nba.datamanifest.manifest import latest_data_version

        return latest_data_version()
    except Exception:
        return None


def _ids(rows: Iterable[dict[str, Any]], *keys: str) -> set[int]:
    return {int(r[k]) for r in rows for k in keys if r.get(k) is not None}


def _names(
    player_ids: set[int], team_ids: set[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Static nba_api lists (local data, no network); unknown ids keep the 'Player <id>' form."""
    from nba.explain import names

    players = [{"player_id": p, "full_name": names.player_name(p)} for p in sorted(player_ids)]
    teams = [
        {"team_id": t, "abbr": names.team_abbr(t), "full_name": names.team_name(t)}
        for t in sorted(team_ids)
    ]
    return players, teams


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: Sequence[dict[str, Any]]) -> int:
    cols = [c for c, _ in TABLES[table]]
    if rows:
        q = ",".join("?" for _ in cols)
        quoted = ",".join(f'"{c}"' for c in cols)
        con.executemany(
            f'INSERT INTO "{table}" ({quoted}) VALUES ({q})',
            [[_cell(r.get(c)) for c in cols] for r in rows],
        )
    return len(rows)


def _cell(v: Any) -> Any:
    return json.dumps(v, sort_keys=True) if isinstance(v, dict) else v


def _write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_lines(
    props: list[dict[str, Any]],
    window: reader.Window,
    odds_db: Path | None,
    odds_con: duckdb.DuckDBPyConnection | None,
) -> reader.LinesBundle:
    """Quotes for ``props`` from the capture store, read-only. A missing store or table, or a
    lock that outlasts the retry, yields no rows and a status the manifest carries; the export
    itself never fails on the capture's file."""
    if odds_con is not None:
        return reader.prop_lines(odds_con, props, window)
    if odds_db is None:
        return reader.LinesBundle([], 0, "not_requested")
    try:
        con = reader.open_odds_read_only(odds_db)
    except duckdb.IOException as exc:
        from nba.db.connect import is_lock_error

        if not is_lock_error(exc):
            raise
        return reader.LinesBundle([], 0, "locked")
    try:
        return reader.prop_lines(con, props, window)
    finally:
        if con is not None:
            con.close()


def _primary_counts(props: list[dict[str, Any]]) -> dict[str, int]:
    """How many primary prop rows came from each source (avg / fallback_* / postseason)."""
    out: dict[str, int] = {}
    for p in props:
        if p.get("is_primary"):
            out[str(p["primary_source"])] = out.get(str(p["primary_source"]), 0) + 1
    return dict(sorted(out.items()))


def build_export(
    src_db: Path,
    out_dir: Path,
    day: date,
    *,
    lookback_days: int = 7,
    seasons: Iterable[int] | None = None,
    parlay_dir: Path | None = None,
    odds_db: Path | None = None,
    odds_con: duckdb.DuckDBPyConnection | None = None,
    now: Callable[[], datetime] | None = None,
    git: Callable[[], dict[str, Any]] = git_info,
    version: Callable[[], str | None] = data_version,
    con: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any]:
    """Build the export for ``day``; returns the manifest dict (also written to ``manifest.json``).

    ``con`` lets a test pass an open read-only connection; production passes ``src_db`` only.
    ``odds_db`` is the live capture store (``data/markets/market_asof.duckdb``), opened read-only
    for ``prop_lines``; None (or an absent file) exports no lines and says so in the manifest.
    Raises ``guard.ExportRefused`` before reading anything when a holdout season is requested."""
    allowed = guard.resolve_seasons(seasons)
    out_dir = Path(out_dir)
    outs = {out_dir / n for n in (DB_NAME, PARLAY_NAME, MANIFEST_NAME)}
    if Path(src_db).resolve() in {o.resolve() for o in outs}:
        raise guard.ExportRefused("output path equals the source database")
    window = reader.Window(day, lookback_days, allowed)
    made_at = (now or (lambda: datetime.now(UTC)))().replace(tzinfo=None)
    own = con is None
    src = con if con is not None else reader.open_read_only(src_db)
    try:
        fwd = reader.forward_predictions(src, window)
        scores = reader.settled_scores(src, window)
        box = reader.box_scores(src, window)
        report = reader.explainer_rows(src, fwd.games, window)
        teams_of = reader.player_teams(src, fwd.prop_grids, fwd.games, window)
    finally:
        if own:
            src.close()
    for prow in fwd.prop_grids:
        tid, how = teams_of.get((prow["game_id"], prow["player_id"]), (None, None))
        prow["team_id"], prow["team_source"] = tid, how
    lines = _read_lines(fwd.prop_grids, window, odds_db, odds_con)
    parlay = parlay_of_day(parlay_dir or REPO_ROOT / "reports" / "parlay", day.isoformat())

    players, teams = _names(
        (_ids(fwd.prop_grids, "player_id") | _ids(box, "player_id") | _ids(report, "player_id")
         | _ids(scores, "player_id")) - {reader.NO_PLAYER},
        _ids(fwd.games, "home_team", "away_team") | _ids(box, "team_id") | _ids(report, "team_id"),
    )  # fmt: skip
    gi = git()
    dv = version()
    tables: dict[str, list[dict[str, Any]]] = {
        "games": fwd.games, "win_probs": fwd.win_probs, "prop_grids": fwd.prop_grids,
        "model_stamps": fwd.stamps, "p_play": fwd.p_play, "settled_scores": scores,
        "box_scores": box,
        "injury_report": report, "players": players, "teams": teams,
        "prop_lines": lines.rows,
    }  # fmt: skip
    meta = {
        "schema_version": str(SCHEMA_VERSION), "export_date": day.isoformat(),
        "made_at": made_at.isoformat() + "Z", "seasons": ",".join(map(str, allowed)),
        "lookback_days": str(lookback_days), "data_version": dv or "",
        "lines_status": lines.status, "best_model_schema": str(BEST_MODEL_SCHEMA),
        "git_sha": gi["git_sha"] or "", "timestamps": "naive UTC unless a column says _et",
    }  # fmt: skip

    out_dir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    tmp_db = out_dir / f".{DB_NAME}.tmp.{pid}"
    try:
        for p in (tmp_db, Path(str(tmp_db) + ".wal")):
            p.unlink(missing_ok=True)
        out = duckdb.connect(str(tmp_db))
        try:
            for t, cols in TABLES.items():
                defs = ", ".join(f'"{c}" {ty}' for c, ty in cols)
                out.execute(f'CREATE TABLE "{t}" ({defs})')
            _insert(out, "meta", [{"key": k, "value": v} for k, v in meta.items()])
            counts = {t: _insert(out, t, rows) for t, rows in tables.items()}
            out.execute("CHECKPOINT")
        finally:
            out.close()
        chk = duckdb.connect(
            str(tmp_db), read_only=True
        )  # verify the file reopens and counts agree
        try:
            for t, n in counts.items():
                got = chk.execute(f'SELECT count(*) FROM "{t}"').fetchone()
                if not got or got[0] != n:
                    raise RuntimeError(f"export verify failed for {t}: wrote {n}, read {got}")
        finally:
            chk.close()
        os.replace(tmp_db, out_dir / DB_NAME)
    finally:
        for p in (tmp_db, Path(str(tmp_db) + ".wal")):
            p.unlink(missing_ok=True)
    _write_json_atomic(out_dir / PARLAY_NAME, parlay)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "export_date": day.isoformat(),
        "made_at": meta["made_at"], "data_version": dv, **gi,
        "seasons": list(allowed), "lookback_days": lookback_days,
        "row_counts": counts,
        "excluded": {"post_tip": fwd.excluded_post_tip,
                     "avg_post_tip": fwd.excluded_avg_post_tip,
                     "avg_unreadable": fwd.excluded_avg_unreadable,
                     "avg_no_game_teams": fwd.excluded_avg_no_game,
                     "unresolved_or_disallowed_season": fwd.excluded_unresolved_season,
                     "lines_post_tip": lines.excluded_post_tip},
        "lines": {"status": lines.status, "n_quotes": len(lines.rows),
                  "n_unusable": sum(1 for q in lines.rows if q["unusable"])},
        "primary_sources": _primary_counts(fwd.prop_grids),
        "parlay": {"status": parlay["status"], "verdict": parlay["verdict"],
                   "n_evaluated": parlay["n_evaluated"]},
        "files": {
            n: {"sha256": sha256_file(out_dir / n), "bytes": (out_dir / n).stat().st_size}
            for n in (DB_NAME, PARLAY_NAME)
        },
    }  # fmt: skip
    _write_json_atomic(out_dir / MANIFEST_NAME, manifest)
    return manifest
