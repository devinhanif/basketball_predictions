# ruff: noqa: E501
"""Freshness view: the newest Pinnacle row per game, and its age.

Read-only. Age is measured on ``requested_at`` (when we asked the vendor), the same clock JARVIS uses
for a reference's age (``nba.jarvis.sources._latest_rows``), so the two always agree on "how old".
Every ``snapshot_kind`` counts, including ``ondemand<N>``: the question is the newest price, not a
benchmark. Benchmarks (``close``, ``t60``) are selected by kind elsewhere and never see on-demand rows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from nba.pulls.config import HEARTBEAT_NAME, PullsConfig
from nba.pulls.store import utc_naive

BOOK = "pinnacle"


@dataclass(frozen=True)
class Newest:
    requested_at: datetime
    kind: str


@dataclass(frozen=True)
class FreshRow:
    game_id: str
    tipoff: datetime | None
    newest: Newest | None
    age_min: float | None


def newest_pinnacle(
    con: duckdb.DuckDBPyConnection, game_ids: Sequence[str], now: datetime
) -> dict[str, Newest]:
    """Newest Pinnacle ``requested_at`` (and its kind) per game, at or before ``now``."""
    if not game_ids:
        return {}
    ph = ",".join("?" for _ in game_ids)
    try:
        rows = con.execute(
            "SELECT game_id, max(requested_at), arg_max(snapshot_kind, requested_at) "  # noqa: S608
            f"FROM live_book_odds WHERE book = ? AND game_id IN ({ph}) AND requested_at <= ? "
            "GROUP BY game_id",
            [BOOK, *game_ids, utc_naive(now)],
        ).fetchall()
    except duckdb.CatalogException:  # the capture has never written the table
        return {}
    return {str(g): Newest(t, str(k)) for g, t, k in rows}


def newest_pinnacle_ro(
    market_db: Path,
    game_ids: Sequence[str],
    now: datetime,
    *,
    retries: int = 3,
    wait_s: float = 1.0,
) -> dict[str, Newest] | None:
    """Same, from a read-only open of the store. ``None`` = could not read (missing file or a writer
    holds it): unknown, which callers must not treat as "stale"."""
    if not market_db.exists():
        return None
    for _ in range(max(1, retries)):
        try:
            con = duckdb.connect(str(market_db), read_only=True)
        except duckdb.IOException:
            time.sleep(wait_s)
            continue
        try:
            return newest_pinnacle(con, game_ids, now)
        finally:
            con.close()
    return None


def freshness_view(
    market_db: Path, games: Sequence[tuple[str, datetime | None]], now: datetime
) -> list[FreshRow] | None:
    """One row per ``(game_id, tipoff)``; ``None`` when the store cannot be read."""
    newest = newest_pinnacle_ro(market_db, [g for g, _ in games], now)
    if newest is None:
        return None
    n = utc_naive(now)
    out = []
    for gid, tip in games:
        nw = newest.get(gid)
        out.append(
            FreshRow(
                gid, tip, nw, None if nw is None else (n - nw.requested_at).total_seconds() / 60
            )
        )
    return out


# --------------------------------------------------------------------------------- watchdog


def beat_time(hb: object) -> datetime | None:
    if not isinstance(hb, dict):
        return None
    stamps = []
    for k in ("end_ts", "start_ts"):
        v = hb.get(k)
        if isinstance(v, str):
            try:
                stamps.append(datetime.fromisoformat(v))
            except ValueError:
                continue
    if not stamps:
        return None
    best = max(t.astimezone() if t.tzinfo is None else t for t in stamps)
    return best


def pulls_alive(cfg: PullsConfig, now: datetime) -> tuple[bool, str]:
    """Is the orchestrator job running? Enabled in ``configs/pulls.yaml``, a heartbeat no older than
    ``wd_heartbeat_max_age_min``, and not the "disabled" beat the script writes with the env gate off.
    JARVIS holds a play only while this is true: a hold nobody can serve is a silent outage."""
    if not cfg.enabled:
        return False, "disabled in configs/pulls.yaml"
    path = cfg.ops_dir / "heartbeat" / f"{HEARTBEAT_NAME}.json"
    try:
        hb = json.loads(path.read_text())
    except (OSError, ValueError):
        return False, "no pulls heartbeat"
    beat = beat_time(hb)
    if beat is None:
        return False, "pulls heartbeat unreadable"
    rc = hb.get("rc")
    if rc not in (0, 4, None):
        return False, f"pulls tick failing (rc {rc})"
    if "disabled" in str(hb.get("detail", "")):
        return False, "pulls job is gated off (NBA_CLOSE_CAPTURE != 1)"
    n = now if now.tzinfo else now.replace(tzinfo=UTC)
    age = (n - beat).total_seconds() / 60
    if age > cfg.wd_heartbeat_max_age_min:
        return False, f"pulls heartbeat {age:.0f} min old"
    return True, ""


def pulls_problems(
    cfg: PullsConfig,
    ops_dir: Path,
    now: datetime,
    tips: dict[str, datetime],
    market_db: Path | None = None,
    check_pinnacle: bool = True,
) -> list[str]:
    """Game-day problems for ``nba.ops.watchdog`` (empty = fine or unknowable).

    1. Every game with a tip in the next ``wd_window_h`` hours must have a Pinnacle row no older than
       ``wd_warn_pinnacle_age_min`` (+ the grace for a late scheduled pull). A store that cannot be
       read, or does not exist, is unknown, not stale.
    2. When enabled and the job has ever left a heartbeat, that heartbeat must be fresh. A job never
       installed stays silent (the same stance as the live-capture check)."""
    if not cfg.enabled:
        return []
    out: list[str] = []
    soon = {
        g: t for g, t in tips.items() if 0 < (t - now).total_seconds() <= cfg.wd_window_h * 3600
    }
    mdb = market_db or ops_dir.parent / "markets" / "market_asof.duckdb"
    if soon and check_pinnacle:
        newest = newest_pinnacle_ro(mdb, list(soon), now)
        if newest is not None:
            limit = cfg.wd_warn_pinnacle_age_min + cfg.wd_warn_grace_min
            late = []
            for gid, _tip in sorted(soon.items(), key=lambda kv: kv[1]):
                nw = newest.get(gid)
                if nw is None:
                    late.append(f"{gid} none")
                    continue
                age = (utc_naive(now) - nw.requested_at).total_seconds() / 60
                if age > limit:
                    late.append(f"{gid} {age:.0f} min")
            if late:
                out.append(
                    f"newest Pinnacle row older than {cfg.wd_warn_pinnacle_age_min:g} min for "
                    f"game(s) within {cfg.wd_window_h:g} h of tip: {', '.join(late[:4])}"
                    + (f" (+{len(late) - 4} more)" if len(late) > 4 else "")
                )
    path = ops_dir / "heartbeat" / f"{HEARTBEAT_NAME}.json"
    try:
        hb = json.loads(path.read_text())
    except (OSError, ValueError):
        hb = None
    beat = beat_time(hb)
    if hb is not None and beat is not None:
        age_min = (now - beat).total_seconds() / 60
        if age_min > cfg.wd_heartbeat_max_age_min:
            out.append(
                f"pulls job heartbeat is {age_min:.0f} min old (limit "
                f"{cfg.wd_heartbeat_max_age_min:g}); on-demand Pinnacle pulls are not running"
            )
    return out
