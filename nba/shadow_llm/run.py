"""One tick of the LLM shadow forecaster: games whose real tip is in the request window.

Flag check first (nothing else happens when it is off). A read-only pass over
``forward_predictions`` finds due games (production T-60 rows exist, no ``llm_shadow`` row yet,
tip in [now+lo, now+hi] minutes), then :func:`nba.shadow_llm.runner.run_game` runs for each,
earliest tip first, sequentially (so a budget ceiling skips the LATEST tips) and stops starting
games after ``max_wall_s``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.db.connect import DEFAULT_DB_PATH, connect_with_retry, is_lock_error
from nba.ops.exitcodes import RC_DB_BUSY, RC_DEGRADED, RC_INFORMATIONAL
from nba.shadow_llm.config import ShadowConfig
from nba.shadow_llm.payload import WIN_MODEL, WIN_TARGET, et_date, naive_utc
from nba.shadow_llm.provider import Provider
from nba.shadow_llm.runner import MODEL_NAME, run_game, utcnow

log = logging.getLogger("nba.shadow_llm")

FLAG_ENV = "NBA_LLM_SHADOW"

#: statuses that make the step exit 4 (informational) / 5 (degraded); skips like no_legs are normal
INFORMATIONAL = frozenset(
    {
        "key_missing",
        "monthly_cap_usd_not_set",
        "budget_skip",
        "monthly_backstop",
        "schema_violation",
        "post_tip_refused",
        "input_cap",
    }
)
DEGRADED = frozenset({"provider_error", "as_of_violation"})


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get(FLAG_ENV) or "").strip() == "1"


def default_paths(cfg: ShadowConfig, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Path]:
    return {
        "nba": db_path,
        "forward": db_path,
        "facts": cfg.facts_db,
        "market": cfg.odds_db,
        "shadow": cfg.shadow_db,
    }


@dataclass
class GameResult:
    game_id: str
    status: str
    detail: str = ""
    rows_written: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class RunSummary:
    slate: date
    results: list[GameResult] = field(default_factory=list)
    rc: int = 0
    notes: list[str] = field(default_factory=list)

    def finish(self) -> RunSummary:
        if self.rc == 0:
            statuses = {r.status for r in self.results}
            if statuses & DEGRADED:
                self.rc = RC_DEGRADED
            elif statuses & INFORMATIONAL:
                self.rc = RC_INFORMATIONAL
        return self


def due_games(
    con: duckdb.DuckDBPyConnection, slate: date, now: datetime, window: tuple[int, int]
) -> list[tuple[str, datetime]]:
    """(game_id, tip) of the ET ``slate`` with now+lo <= tip <= now+hi, tip > now, production's
    injury-Elo row present and no ``llm_shadow`` row yet. Ordered by tip, game id."""
    rows = con.execute(
        """
        SELECT game_id, max(tipoff) FROM forward_predictions
        WHERE model_name = ? AND target = ?
          AND game_id NOT IN (SELECT game_id FROM forward_predictions WHERE model_name = ?)
        GROUP BY game_id ORDER BY max(tipoff), game_id
        """,
        [WIN_MODEL, WIN_TARGET, MODEL_NAME],
    ).fetchall()
    lo, hi = window
    return [
        (str(g), tip)
        for g, tip in rows
        if tip > now
        and et_date(tip) == slate
        and now + timedelta(minutes=lo) <= tip <= now + timedelta(minutes=hi)
    ]


def _acquire(lock_dir: Path) -> bool:
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir()
        return True
    except FileExistsError:
        if time.time() - lock_dir.stat().st_mtime > 20 * 60:  # a dead run: clear and retry once
            try:
                lock_dir.rmdir()
                lock_dir.mkdir()
                return True
            except OSError:
                return False
        return False


def run(
    slate: date,
    *,
    provider: Provider,
    cfg: ShadowConfig,
    paths: Mapping[str, Path] | None = None,
    dry_run: bool = False,
    allow_stub_write: bool = False,
    now: datetime | None = None,
    clock: Callable[[], datetime] = utcnow,
    max_season: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> RunSummary:
    env = os.environ if environ is None else environ
    summary = RunSummary(slate)
    if not enabled(env):
        summary.notes.append(f"llm_shadow disabled ({FLAG_ENV} != 1): nothing to do")
        return summary
    is_real = bool(getattr(provider, "is_real", True))
    if not is_real and not dry_run and not allow_stub_write:
        summary.notes.append(
            "stub provider refuses to write rows to the real database; use --dry-run"
        )
        summary.rc = 2
        return summary
    paths = paths or default_paths(cfg)
    if not _acquire(cfg.lock_dir):
        summary.notes.append("another llm_shadow run holds the lock; skipping this tick")
        return summary
    try:
        t0 = naive_utc(now) if now is not None else clock()
        try:
            con = connect_with_retry(
                Path(paths["forward"]), read_only=True, max_wait_s=cfg.db_wait_s
            )
        except duckdb.IOException as exc:
            if not is_lock_error(exc):
                raise
            summary.notes.append("nba.duckdb is write-locked after the bounded wait; next tick")
            summary.rc = RC_DB_BUSY
            return summary
        try:
            due = due_games(con, slate, t0, cfg.window_min)
        finally:
            con.close()
        deadline = time.monotonic() + cfg.max_wall_s
        for gid, _tip in due:
            if time.monotonic() > deadline:
                summary.results.append(GameResult(gid, "deferred_wall"))
                continue
            out: dict[str, Any] = run_game(
                gid,
                provider=provider,
                now=t0 if now is not None else clock(),
                paths=paths,
                cfg=cfg,
                dry_run=dry_run,
                max_season=max_season,
                clock=clock,
                environ=env,
            )
            summary.results.append(
                GameResult(
                    gid,
                    str(out["status"]),
                    str(out.get("detail", "")),
                    int(out.get("rows_written", 0)),
                    int(out.get("tokens_in", 0)),
                    int(out.get("tokens_out", 0)),
                )
            )
            if out["status"] == "db_busy_not_written":
                summary.rc = RC_DB_BUSY
        return summary.finish()
    finally:
        with contextlib.suppress(OSError):
            cfg.lock_dir.rmdir()
