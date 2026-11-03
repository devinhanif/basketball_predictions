"""LATE_REPORT shadow (DECISIONS 2026-11-02 19:44): the props forecast re-run at T-15 with the
official injury report as of the run time.

SHADOW ONLY, paper only. Production stays ``props_context_residual`` at its T-60 report rule; this
logs the comparison model ``props_context_residual_late`` once per game, from the T-15 lineups
tick (``nba.daily run-t15 --log-late-shadow``), after the frozen T-15 arm has written its rows.
Nothing reads it for parlays or the app's primary number; JARVIS shows a display-only line.

Information set, per game (decided once, at the first tick at/after tip - 15 min, before tip):

* **Report**: the latest official-report snapshot of THAT game stamped (US-Eastern) at or before
  ``made_at`` (``serve_pretip_flagged`` with ``lead_minutes=0``; the snapshot's own ``as_of`` is
  re-checked against ``made_at`` and a violation refuses the batch), <= 36 h old. Statuses OUT and
  DOUBTFUL both exclude the player and both count as "teammate out" in the features. Production
  and the training rows use OUT only at T-60: treating DOUBTFUL as out is a deliberate, recorded
  train/serve difference (docs: the shadow's rows carry ``report_statuses``).
* **Lineup**: if the game's confirmed five is in a lineup snapshot fetched strictly before
  tip - 15 min (exactly the T-15 arm's rule, ``load_game_lineup``), the lineups-known model is
  used (``lineups_known=True``, the T-15 arm's features); otherwise the production context model.
  So on confirmed-lineup games this arm differs from ``props_context_residual_t15`` only by the
  report, and elsewhere it differs from production only by the report.
* **Models**: never a new kind of fit. The model caches are seeded by copying today's production
  and T-30/T-15 caches into this arm's own cache root (``data/models/context_residual_late``), so a
  hit costs nothing and a miss (late-ingested history) refits into the shadow's own directory,
  never into production's.

Rows: same schema and stamping as the other arms (``_frame_preds``), ``comparison_only`` True,
written with the write-on-change dedup (``append_predictions_ex(dedup=True)``) and refused whole if
any ``made_at >= tipoff`` (``LeakageError``). One ``forward_model_stamps`` row per run. One row per
game in ``forward_late_decisions`` (idempotent). The replay (``nba.daily.replay_season``) never
calls this module, so the canonical dump (replay oracle) cannot see it, exactly like the T-15 arm.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.daily import stamps
from nba.daily.injury import MAX_REPORT_AGE_HOURS, to_report_dt
from nba.daily.pipeline import RunSummary, _frame_preds, _official_roster, utcnow
from nba.daily.predict import (
    DEFAULT_CONTEXT_CACHE,
    REPO_ROOT,
    context_prop_predictions,
    injury_report_rows,
    load_elo_params,
)
from nba.daily.runmeta import git_state
from nba.daily.schedule import ScheduledGame, ScheduleFn, slate_for_date
from nba.daily.season import season_str_for_date
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions_ex
from nba.daily.t30 import (
    DEFAULT_T30_CACHE,
    T15_NO_SNAPSHOT_REASON,
    T30_MIN_INTERVAL_S,
    GameLineup,
    load_game_lineup,
    t30_prop_predictions,
)
from nba.features.injury_report import ReportTriggerConfig, serve_pretip_flagged
from nba.ingest.cache import RateLimiter
from nba.lineups.store import T15_LEAD_MINUTES
from nba.props.rosters import RosterFetcher

LATE_MODEL_NAME = "props_context_residual_late"
LATE_VERSION = "ctxres-v1-late"
LATE_DECISIONS_TABLE = "forward_late_decisions"
#: decided at the first tick at/after tip - 15 min (the T-15 slot), always before tip
LATE_LEAD_MINUTES = T15_LEAD_MINUTES
#: official-report statuses that exclude a player (and flag him "out" for teammates' features)
LATE_STATUSES: tuple[str, ...] = ("out", "doubtful")
DEFAULT_LATE_CACHE = REPO_ROOT / "data" / "models" / "context_residual_late"
_CTX_SUB, _T30_SUB = "ctx", "t30"

LATE_DECISIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {LATE_DECISIONS_TABLE} (
    game_id VARCHAR PRIMARY KEY,
    decided_at TIMESTAMP,          -- made_at of the run (naive UTC)
    tipoff TIMESTAMP,
    cutoff TIMESTAMP,              -- tip-off minus 15 min: the decision is due at/after it
    report_as_of TIMESTAMP,        -- official-report snapshot used (naive US-Eastern), NULL = none
    report_excluded VARCHAR,       -- JSON list of player ids OUT/DOUBTFUL on that snapshot
    lineups_known BOOLEAN,         -- confirmed five fetched before tip - 15 min was used
    snapshot_id VARCHAR,           -- lineup snapshot when lineups_known
    outcome VARCHAR,               -- 'logged' | 'skipped'
    reason VARCHAR,
    n_rows INT
);
"""


@dataclass
class LateSummary:
    run_id: str = ""
    n_slate: int = 0
    n_not_yet: int = 0
    n_already_decided: int = 0
    n_logged_games: int = 0
    n_skipped_games: int = 0
    n_rows_written: int = 0
    n_rows_deduped: int = 0
    n_lineup_games: int = 0
    n_report_games: int = 0
    stamp_status: str = "skipped"
    skipped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GameLate:
    """What the late arm knew about one game at ``made_at``."""

    game_id: str
    report_as_of: datetime | None  # naive ET
    excluded: frozenset[int]
    lineup: GameLineup | None
    lineup_reason: str


def ensure_late_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(LATE_DECISIONS_DDL)


def late_report_outs(
    con: duckdb.DuckDBPyConnection,
    games: list[tuple[str, datetime]],
    made_at: datetime,
) -> tuple[dict[str, set[int]], dict[str, datetime]]:
    """``(game_id -> players OUT or DOUBTFUL, game_id -> snapshot as_of)`` on the latest official
    report of each game stamped at or before ``made_at`` (naive UTC; compared on the report's
    US-Eastern clock) and <= 36 h old. ``games`` = ``[(game_id, tipoff_naive_utc)]``; ``made_at``
    must be before every tip. A game with no usable report is absent (never "nobody out")."""
    if not games:
        return {}, {}
    tips_et = {gid: to_report_dt(tip) for gid, tip in games}
    now_et = to_report_dt(made_at)
    rows = injury_report_rows(
        con, list(tips_et), game_dates={gid: t.date() for gid, t in tips_et.items()}
    )
    cfg = ReportTriggerConfig(statuses=LATE_STATUSES, lead_minutes=0, tip_source="real")
    flagged, used = serve_pretip_flagged(
        rows, tips_et, now_et, cfg, max_age_hours=MAX_REPORT_AGE_HOURS
    )
    bad = sorted(g for g, t in used.items() if t > now_et or t >= tips_et[g])
    if bad:  # cannot happen through serve_pretip_flagged; a planted-future guard, not a filter
        raise LeakageError(f"late report snapshot after made_at or tip-off for games {bad[:5]}")
    return flagged, used


def _seed_cache(src_root: Path | None, dst: Path, run_date: date) -> bool:
    """Copy today's ``meta.json`` + ``models.pkl`` from ``src_root/<date>`` into ``dst/<date>`` when
    the destination has none (never overwrites; the source is only read). True if copied."""
    if src_root is None:
        return False
    src, out = src_root / run_date.isoformat(), dst / run_date.isoformat()
    names = ("meta.json", "models.pkl")
    if all((out / n).exists() for n in names) or not all((src / n).exists() for n in names):
        return False
    out.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copyfile(src / n, out / n)
    return True


def late_game_info(
    con: duckdb.DuckDBPyConnection,
    lcon: duckdb.DuckDBPyConnection | None,
    games: list[ScheduledGame],
    made_at: datetime,
) -> dict[str, GameLate]:
    """Report snapshot and (T-15 rule) confirmed lineup per game, as known at ``made_at``."""
    flagged, used = late_report_outs(con, [(g.game_id, g.tipoff) for g in games], made_at)
    out: dict[str, GameLate] = {}
    lead = timedelta(minutes=LATE_LEAD_MINUTES)
    for g in games:
        gl, reason = None, "no_lineups_db"
        if lcon is not None:
            gl, reason, _snap = load_game_lineup(lcon, g, g.tipoff - lead, T15_NO_SNAPSHOT_REASON)
        out[g.game_id] = GameLate(
            g.game_id,
            used.get(g.game_id),
            frozenset(flagged.get(g.game_id, set())),
            gl,
            reason,
        )
    return out


def late_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    games: list[ScheduledGame],
    info: dict[str, GameLate],
    elo_params: dict[str, float],
    *,
    cache_root: Path,
    official_roster: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """The late arm's prop rows for ``games``: confirmed-lineup games through the T-15 arm's
    lineups-known path, the rest through production's context path, both with the late report.
    Read-only on ``con``; models come from (and a miss fits into) ``cache_root``."""
    report_out = {gid: set(gi.excluded) for gid, gi in info.items() if gi.report_as_of is not None}
    lineups: dict[str, GameLineup] = {
        gid: gi.lineup for gid, gi in info.items() if gi.lineup is not None
    }
    lu_games = [g for g in games if g.game_id in lineups]
    plain = [g for g in games if g.game_id not in lineups]
    frames: list[pl.DataFrame] = []
    meta: dict[str, Any] = {"cache_hit": {}, "train_through": None, "n_train_rows": None}
    if lu_games:
        df, inf = t30_prop_predictions(
            con, run_date, lu_games, lineups, report_out, elo_params,
            cache_root=cache_root / _T30_SUB, official_roster=official_roster,
        )  # fmt: skip
        meta["cache_hit"][_T30_SUB] = bool(inf.get("cache_hit"))
        meta["train_through"] = inf.get("train_through")
        meta["n_train_rows"] = inf.get("n_train_rows")
        if not df.is_empty():
            frames.append(df)
    if plain:
        res = context_prop_predictions(
            con, run_date, [(g.game_id, g.home_team, g.away_team) for g in plain],
            report_out, elo_params, cache_root=cache_root / _CTX_SUB,
            official_roster=official_roster,
        )  # fmt: skip
        meta["cache_hit"][_CTX_SUB] = bool(res.info.get("cache_hit"))
        meta["train_through"] = meta["train_through"] or res.info.get("train_through")
        meta["n_train_rows"] = meta["n_train_rows"] or res.info.get("n_train_rows")
        if not res.primary.is_empty():
            frames.append(res.primary)
    if not frames:
        return pl.DataFrame(), meta
    return pl.concat(frames, how="vertical_relaxed"), meta


def _record(
    con: duckdb.DuckDBPyConnection,
    g: ScheduledGame,
    now: datetime,
    gi: GameLate | None,
    outcome: str,
    reason: str,
    n_rows: int,
) -> None:
    con.execute(
        f"INSERT INTO {LATE_DECISIONS_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?) "  # noqa: S608
        "ON CONFLICT DO NOTHING",
        [
            g.game_id, now, g.tipoff, g.tipoff - timedelta(minutes=LATE_LEAD_MINUTES),
            gi.report_as_of if gi else None,
            json.dumps(sorted(gi.excluded)) if gi else None,
            bool(gi and gi.lineup is not None),
            gi.lineup.snapshot_id if gi and gi.lineup else None,
            outcome, reason, n_rows,
        ],
    )  # fmt: skip


def _extra(g: ScheduledGame, gi: GameLate, meta: dict[str, Any]) -> dict[str, Any]:
    gl = gi.lineup
    extra: dict[str, Any] = {
        "comparison_only": True,
        "late_report": True,
        "report_statuses": list(LATE_STATUSES),
        "report_as_of_et": gi.report_as_of.isoformat() if gi.report_as_of else None,
        "n_report_excluded": len(gi.excluded),
        "lineups_known": gl is not None,
        "fallback_reason": None,
        "train_through": meta.get("train_through"),
    }
    if gl is not None:  # the T-15 arm's lineup fields, so the two arms pair on one snapshot
        extra.update(
            {
                "snapshot_id": gl.snapshot_id,
                "snapshot_fetched_at": gl.fetched_at.isoformat(),
                "snapshot_age_min_before_tip": (g.tipoff - gl.fetched_at).total_seconds() / 60.0,
            }
        )
    return extra


def _stamp(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    run_date: date,
    cache_root: Path,
    meta: dict[str, Any],
) -> str:
    """One ``forward_model_stamps`` row: digest over the cached models this run used."""
    try:
        digests: dict[str, str] = {}
        for sub in sorted(meta.get("cache_hit", {})):
            models = stamps.load_cached_models(cache_root / sub / run_date.isoformat())
            digests[sub] = "unavailable" if models is None else stamps.models_digest(models)[0]
        sha, _ = stamps.canonical_digest({"kind": "late_report_shadow", **digests})
        tt = meta.get("train_through")
        n = meta.get("n_train_rows")
        st = stamps.Stamp(
            LATE_MODEL_NAME, LATE_VERSION, sha,
            "late shadow: digest over the per-stat context-residual caches it used "
            f"({', '.join(f'{k}={v[:12]}' for k, v in digests.items()) or 'none'})",
            date.fromisoformat(str(tt)[:10]) if tt else None, int(n) if n is not None else None,
        )  # fmt: skip
        head, dirty, _n = git_state()
        return stamps.write_stamps(
            con, run_id, made_at, [st], git_sha=head, git_dirty=dirty,
            data_version=stamps.current_data_version(), stamped_at=utcnow(),
        )  # fmt: skip
    except Exception as exc:  # a stamp never blocks or fails the shadow's rows
        return f"failed: {type(exc).__name__}: {exc}"[:200]


def run_late_shadow(
    con: duckdb.DuckDBPyConnection,
    lcon: duckdb.DuckDBPyConnection | None,
    run_date: date,
    *,
    schedule_fn: ScheduleFn,
    now: datetime | None = None,
    model_cache: Path | None = None,
    seed_ctx_cache: Path | None = DEFAULT_CONTEXT_CACHE,
    seed_t30_cache: Path | None = DEFAULT_T30_CACHE,
    roster_source: str = "recent",
    elo_config: Path | None = None,
    rate_limiter: RateLimiter | None = None,
    roster_dir: Path | None = None,
    roster_fetch: RosterFetcher | None = None,
    static_fetch: Callable[[int], pl.DataFrame] | None = None,
) -> LateSummary:
    """Log the late-report shadow for slate games at/after tip - 15 min and not yet tipped.
    Idempotent per game (``forward_late_decisions``). ``now`` is naive UTC (default: the clock)."""
    made_at = now if now is not None else utcnow()
    ensure_late_table(con)
    summ = LateSummary(run_id="late_" + made_at.strftime("%Y%m%dT%H%M%S"))
    slate = slate_for_date(schedule_fn(season_str_for_date(run_date)), run_date)
    summ.n_slate = len(slate)
    decided = {
        str(r[0])
        for r in con.execute(f"SELECT game_id FROM {LATE_DECISIONS_TABLE}").fetchall()  # noqa: S608
    }
    lead = timedelta(minutes=LATE_LEAD_MINUTES)
    ready: list[ScheduledGame] = []
    for g in slate:
        if g.game_id in decided:
            summ.n_already_decided += 1
        elif made_at < g.tipoff - lead:
            summ.n_not_yet += 1
        elif not made_at < g.tipoff:
            _record(con, g, made_at, None, "skipped", "run_after_tipoff", 0)
            summ.skipped[g.game_id] = "run_after_tipoff"
            summ.n_skipped_games += 1
        else:
            ready.append(g)
    if not ready:
        return summ

    info = late_game_info(con, lcon, ready, made_at)
    summ.n_lineup_games = sum(1 for gi in info.values() if gi.lineup is not None)
    summ.n_report_games = sum(1 for gi in info.values() if gi.report_as_of is not None)
    cache_root = model_cache or DEFAULT_LATE_CACHE
    for src, kind in ((seed_ctx_cache, _CTX_SUB), (seed_t30_cache, _T30_SUB)):
        if _seed_cache(src, cache_root / kind, run_date):
            summ.notes.append(f"seeded {kind} cache from {src}")
    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    official = None
    if roster_source == "official":  # the per-date cache the T-15 arm just used: a pure hit
        official = _official_roster(
            con, run_date, season_str_for_date(run_date), roster_source, roster_dir,
            roster_fetch, rate_limiter or RateLimiter(T30_MIN_INTERVAL_S),
            RunSummary(run_id="late", run_date=run_date), static_fetch=static_fetch,
        )  # fmt: skip
    df, meta = late_prop_predictions(
        con, run_date, ready, info, params, cache_root=cache_root, official_roster=official
    )
    summ.notes.append(f"cache_hit={meta.get('cache_hit')}")
    preds: list[ForwardPrediction] = []
    per_game: dict[str, int] = {}
    for g in ready:
        sub = df.filter(pl.col("game_id") == g.game_id) if not df.is_empty() else df
        rows = _frame_preds(
            sub, [g], LATE_MODEL_NAME, LATE_VERSION, run_date, _extra(g, info[g.game_id], meta)
        )
        per_game[g.game_id] = len(rows)
        preds.extend(rows)
    try:
        summ.n_rows_written, summ.n_rows_deduped = append_predictions_ex(
            con, summ.run_id, made_at, preds, dedup=True
        )
    except LeakageError:
        summ.notes.append("LeakageError: batch refused")
        raise
    for g in ready:
        n, gi = per_game[g.game_id], info[g.game_id]
        if n == 0:
            _record(con, g, made_at, gi, "skipped", "no_model_rows", 0)
            summ.skipped[g.game_id] = "no_model_rows"
            summ.n_skipped_games += 1
            continue
        how = "lineups_known" if gi.lineup is not None else f"no lineup ({gi.lineup_reason})"
        rep = "no report" if gi.report_as_of is None else f"{len(gi.excluded)} out/doubtful"
        _record(con, g, made_at, gi, "logged", f"ok; {how}; {rep}", n)
        summ.n_logged_games += 1
    if preds:
        summ.stamp_status = _stamp(con, summ.run_id, made_at, run_date, cache_root, meta)
    return summ
