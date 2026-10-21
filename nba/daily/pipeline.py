"""Orchestrates one daily run. All side-effecting collaborators are injectable
so the fixture tests never touch the network or the real database."""

from __future__ import annotations

import json
import math
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.daily import grid199, stamps
from nba.daily.ingest_step import FetchGames, PullBox, incremental_ingest
from nba.daily.injury import ProbeFn, PullFn, out_players, pull_latest_report
from nba.daily.predict import (
    CONTEXT_INT_PROPS_MODEL_NAME,
    CONTEXT_LT_PROPS_MODEL_NAME,
    CONTEXT_PROPS_MODEL_NAME,
    CONTEXT_PROPS_VERSION,
    ELO_MODEL_NAME,
    INJURY_ELO_MODEL_NAME,
    PROP_STATS,
    PROPS_MODEL_NAME,
    PROPS_VERSION,
    RECENCY_PROPS_MODEL_NAME,
    RECENCY_PROPS_VERSION,
    ResolvedModel,
    context_prop_predictions,
    fit_mov_elo,
    injury_report_rows,
    load_elo_params,
    load_injury_settings,
    predict_win_probs,
    recency_prop_predictions,
    resolve_production,
    rolling_prop_baseline,
    slate_report_outs,
)
from nba.daily.runmeta import git_state
from nba.daily.schedule import (
    MAX_SCHEDULE_CACHE_AGE,
    ScheduledGame,
    ScheduleFn,
    load_schedule_cache,
    n_unscorable_on_date,
    save_schedule_cache,
    slate_for_date,
)
from nba.daily.season import season_int_for_date, season_str_for_date
from nba.daily.settle import settle_pending
from nba.daily.store import (
    ForwardPrediction,
    LeakageError,
    append_predictions_ex,
    ensure_tables,
    withdraw_misplaced,
)
from nba.features.game_tipoff import persist_live_tips
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter
from nba.ingest.games import MAX_VALID_TEAM_ID, MIN_VALID_TEAM_ID
from nba.ingest.players_static import autofill_players_static
from nba.models.injury_elo import predict_games
from nba.props.forward import OFFICIAL_ROSTER_FLOOR, ForwardConfig
from nba.props.rosters import (
    DEFAULT_ROSTER_DIR,
    RosterFetcher,
    load_official_rosters,
    missing_static_ids,
    roster_names,
)
from nba.registry.protocol import RegistryAdapter


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class RunSummary:
    run_id: str
    run_date: date
    n_slate: int = 0
    #: preseason / all-star games on the date, skipped (never scorable)
    n_preseason_skipped: int = 0
    n_predicted_games: int = 0
    n_refused_after_tipoff: int = 0
    n_rows_written: int = 0
    #: rows identical to the latest stored row of their key (write-on-change): not re-inserted
    n_rows_deduped: int = 0
    n_props_rows: int = 0
    injury_report_et: str | None = None
    injury_status: str = "skipped"
    n_out_excluded: int = 0
    model_status: dict[str, str] = field(default_factory=dict)
    ingest: dict[str, int] = field(default_factory=dict)
    settle: dict[str, int] = field(default_factory=dict)
    static_autofill: dict[str, int] = field(default_factory=dict)
    #: non-primary dependency failures the run degraded around (ingest, schedule): alerting
    errors: list[str] = field(default_factory=list)
    #: shadow-arm (_int, _lt, recency extras) failures; primary rows are unaffected: informational
    shadow_errors: list[str] = field(default_factory=list)
    #: code identity of this run (nba.daily.runmeta): git HEAD, tracked-file dirty flag + count
    git_head: str = "unknown"
    git_dirty: bool | None = None
    git_n_dirty: int = 0
    #: model stamp write (nba.daily.stamps): "ok: N rows" | "failed: ..." | "skipped"
    stamp_status: str = "skipped"
    #: fit facts the run hands to the stamp writer (Elo ratings, props fit info); never persisted
    stamp_inputs: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: production's 199-point grids per (game, player, stat) for nba.daily.grid199
    grids199: dict[tuple[str, int, str], Any] = field(
        default_factory=dict, repr=False, compare=False
    )
    #: forward_prop_grid199 write: "ok: ..." | "failed: ..." | "skipped"
    grid_status: str = "skipped"


def run_daily(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    *,
    schedule_fn: ScheduleFn,
    registry: RegistryAdapter | None = None,
    now: datetime | None = None,
    skip_ingest: bool = False,
    skip_injury: bool = False,
    with_props: bool = True,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    fetch_games: FetchGames | None = None,
    pull_box: PullBox | None = None,
    probe: ProbeFn | None = None,
    pull_report: PullFn | None = None,
    name_index: dict[str, int] | None = None,
    elo_config: Path | None = None,
    props_model: str = "context",
    model_cache: Path | None = None,
    roster_source: str = "recent",
    roster_dir: Path | None = None,
    roster_fetch: RosterFetcher | None = None,
    log_int_variant: bool = False,
    log_lower_tail_variant: bool = False,
    tips_dir: Path | None = None,
    static_fetch: Callable[[int], pl.DataFrame] | None = None,
    schedule_cache_dir: Path | None = None,
    dedup: bool = True,
    with_pplay_shadow: bool = False,
    with_full_shadow: bool = False,
    with_avg_shadow: bool = False,
    avg_bundle_root: Path | None = None,
    avg_heartbeat_dir: Path | None = None,
    with_combo_shadow: bool = False,
    combo_heartbeat_dir: Path | None = None,
    tip_source: str = "real",
) -> RunSummary:
    """Ingest -> injury report -> predict -> append -> settle. ``now`` is naive UTC.

    A failed ingest or schedule fetch degrades instead of aborting: ingest is skipped (features
    are strictly pre-slate) and the schedule falls back to the last good copy in
    ``schedule_cache_dir``; both are recorded in ``summary.errors``. With no cached schedule the
    schedule error is re-raised.

    ``dedup`` (default on) is write-on-change for ``forward_predictions``: rows identical to the
    latest stored row of their key are not re-inserted (``summary.n_rows_deduped``); see
    docs/DAILY_PIPELINE.md."""
    made_at = now if now is not None else utcnow()
    ensure_tables(con)
    summary = RunSummary(run_id=uuid.uuid4().hex[:12], run_date=run_date)
    summary.git_head, summary.git_dirty, summary.git_n_dirty = git_state()
    season_i, season_s = season_int_for_date(run_date), season_str_for_date(run_date)

    if not skip_ingest:
        try:
            summary.ingest = incremental_ingest(
                con,
                season_s,
                season_i,
                data_dir=data_dir,
                rate_limiter=rate_limiter,
                fetch_games=fetch_games,
                pull_box=pull_box,
            )
        except Exception as exc:  # ingest: failed -> predict from the DB as it is
            summary.errors.append(f"ingest: failed ({type(exc).__name__}: {exc})"[:300])

    schedule = _schedule_with_fallback(schedule_fn, season_s, schedule_cache_dir, summary)
    if tips_dir is not None and schedule:
        # keep real tips for this season's games so later training gates on them
        persist_live_tips([(g.game_id, g.tipoff) for g in schedule], season_s, tips_dir)
    slate = slate_for_date(schedule, run_date)
    summary.n_slate = len(slate)
    summary.n_preseason_skipped = n_unscorable_on_date(schedule, run_date)
    upcoming: list[ScheduledGame] = [g for g in slate if made_at < g.tipoff]
    summary.n_refused_after_tipoff = len(slate) - len(upcoming)

    # official rosters first: their names let the injury-report resolver see debutants
    official = (
        _official_roster(
            con, run_date, season_s, roster_source, roster_dir, roster_fetch,
            rate_limiter, summary, data_dir, static_fetch,
        )
        if upcoming and with_props
        else None
    )  # fmt: skip

    if upcoming and not skip_injury:
        not_after = min(made_at, min(g.tipoff for g in upcoming))
        try:
            slot = pull_latest_report(
                con,
                not_after,
                data_dir=data_dir,
                rate_limiter=rate_limiter,
                probe=probe,
                pull=pull_report,
                name_index=name_index,
                extra_games={(g.game_date_et, g.home_team, g.away_team): g.game_id for g in slate},
                extra_names=roster_names(official) if official is not None else (),
            )
            summary.injury_report_et = slot.isoformat() if slot else None
            summary.injury_status = "ok" if slot else "no_report_found"
        except Exception as exc:  # injury feed must never block the Elo forecast
            summary.injury_status = f"failed: {type(exc).__name__}: {exc}"[:200]

    preds: list[ForwardPrediction] = []
    if upcoming:
        preds = _build_predictions(
            con, run_date, season_i, upcoming, made_at, registry,
            with_props, elo_config, summary, props_model, model_cache,
            official,
            log_int_variant, log_lower_tail_variant,
        )  # fmt: skip
        try:
            summary.n_rows_written, summary.n_rows_deduped = append_predictions_ex(
                con, summary.run_id, made_at, preds, dedup=dedup
            )
        except LeakageError:
            summary.n_refused_after_tipoff += len(upcoming)
            raise
        summary.n_predicted_games = len(upcoming)
        _withdraw_misplaced(con, summary, made_at, upcoming, official)
        _stamp_models(con, run_date, made_at, summary, preds, model_cache)
        _persist_grids(con, made_at, summary, upcoming)

    summary.settle = settle_pending(con, made_at)
    shared: dict[str, Any] = {}  # the wide-roster production frame, computed once for both shadows
    if with_pplay_shadow:
        # SHADOW, after every primary row is written and settled: a failure here is recorded and
        # alerts, and cannot change or delay a primary row (own tables, own try/except).
        _pplay_shadow(
            con, run_date, upcoming, made_at, summary, preds,
            official, elo_config, model_cache, tips_dir, shared,
        )  # fmt: skip
    if with_full_shadow:
        # SHADOW (RECENT_ROLE_FULL), same contract as the P(play) shadow: own tables and try/except.
        _full_shadow(
            con, run_date, upcoming, made_at, summary, preds,
            official, elo_config, model_cache, shared,
        )  # fmt: skip
    if with_avg_shadow:
        # SHADOW (AVG_SHADOW_LIVE_2), after the FULL shadow it mixes with: own tables,
        # own try/except, own heartbeat.
        _avg_shadow(
            con, run_date, upcoming, made_at, summary, shared, avg_bundle_root, avg_heartbeat_dir
        )
    if with_combo_shadow:
        # SHADOW (COMBO_STOCKS_SHADOW), after the AVG shadow: own tables, own
        # try/except, own heartbeat.
        _combo_shadow(
            con, run_date, upcoming, made_at, summary, preds,
            official, elo_config, model_cache, shared, combo_heartbeat_dir, tip_source,
        )  # fmt: skip
    return summary


def _stamp_models(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    made_at: datetime,
    summary: RunSummary,
    preds: list[ForwardPrediction],
    model_cache: Path | None,
) -> None:
    """Write one ``forward_model_stamps`` row per model of this run (own table, own try/except).
    A failure is recorded in ``summary.stamp_status`` and ``shadow_errors`` (an ALERTS line); it can
    never change, delay or fail a prediction row, which are already written."""
    try:
        found = stamps.collect_stamps(con, run_date, preds, summary, model_cache)
        summary.stamp_status = stamps.write_stamps(
            con, summary.run_id, made_at, found,
            git_sha=summary.git_head, git_dirty=summary.git_dirty,
            data_version=stamps.current_data_version(), stamped_at=utcnow(),
        )  # fmt: skip
    except Exception as exc:
        summary.stamp_status = f"failed: {type(exc).__name__}: {exc}"[:200]
    if summary.stamp_status.startswith("failed"):
        summary.shadow_errors.append(f"model stamps: {summary.stamp_status}"[:200])


def _persist_grids(
    con: duckdb.DuckDBPyConnection,
    made_at: datetime,
    summary: RunSummary,
    upcoming: list[ScheduledGame],
) -> None:
    """Write production's 199-point grids (own table, own try/except; ``grid199.persist`` never
    raises). A failure is recorded in ``summary.grid_status`` and ``shadow_errors`` (an ALERTS
    line); it can never change, delay or fail a prediction row, which are already written."""
    try:
        summary.grid_status = grid199.persist(
            con, summary.run_id, made_at, summary.grids199, {g.game_id: g.tipoff for g in upcoming}
        )
    except Exception as exc:  # belt: persist() already catches
        summary.grid_status = f"failed: {type(exc).__name__}: {exc}"[:200]
    if summary.grid_status.startswith("failed"):
        summary.shadow_errors.append(f"prop grid199: {summary.grid_status}"[:200])


#: Production thresholds off: every rostered not-OUT player gets a row (the shadow's coverage).
_WIDE_CONFIG = ForwardConfig(
    min_proj_minutes=-1.0, min_p_play=0.0, max_roster=1000, official_max_roster=1000
)


def _wide_roster_frame(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    official_roster: pl.DataFrame | None,
    elo_config: Path | None,
    model_cache: Path | None,
    shared: dict[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, set[int]], pl.DataFrame]:
    """(elo params, report-OUT map, production's wide-roster rows) for the shadows; computed once
    per run when ``shared`` is given. Production's own ``predict_slate_context`` with its thresholds
    relaxed (same report rule, same cached models): the shadows see the players production drops
    with the same feature code, and nothing is written."""
    from nba.daily.predict import DEFAULT_CONTEXT_CACHE
    from nba.props.forward import predict_slate_context

    if shared is not None and "wide" in shared:
        return shared["params"], shared["report_out"], shared["wide"]
    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    report_out = slate_report_outs(con, [(g.game_id, g.tipoff) for g in upcoming], made_at)
    games = pl.DataFrame(
        {
            "game_id": [g.game_id for g in upcoming],
            "home_team": [g.home_team for g in upcoming],
            "away_team": [g.away_team for g in upcoming],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    wide = predict_slate_context(
        con, run_date, games, report_out, params,
        cache_root=model_cache or DEFAULT_CONTEXT_CACHE,
        official_roster=official_roster, config=_WIDE_CONFIG,
    ).primary  # fmt: skip
    if shared is not None:
        shared.update(params=params, report_out=report_out, wide=wide)
    return params, report_out, wide


def _full_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    summary: RunSummary,
    preds: list[ForwardPrediction],
    official_roster: pl.DataFrame | None,
    elo_config: Path | None,
    model_cache: Path | None,
    shared: dict[str, Any] | None = None,
) -> None:
    """Log the RECENT_ROLE_FULL conditional grids (props heads + lower-tail pi refit with the five
    recent-role columns) for every context-model player, then score FULL vs production nightly.
    Own tables (``forward_full_shadow`` / ``forward_full_scores``); ``forward_predictions`` and
    every props output are untouched; a failure is recorded in ``shadow_errors``, no primary row
    changes."""
    import time

    from nba.daily.full_shadow.run import run_full_shadow
    from nba.daily.full_shadow.settle import settle_full_shadow
    from nba.daily.pplay_shadow.features import SlateGame
    from nba.daily.predict import DEFAULT_CONTEXT_CACHE

    name = "full_shadow"
    t0 = time.perf_counter()
    try:
        if upcoming:
            params, report_out, wide = _wide_roster_frame(
                con, run_date, upcoming, made_at, official_roster, elo_config, model_cache, shared
            )
            logged = {
                (p.game_id, p.player_id)
                for p in preds
                if p.model_name == CONTEXT_PROPS_MODEL_NAME and p.player_id >= 0
            }
            info = run_full_shadow(
                con, run_date,
                [SlateGame(g.game_id, g.home_team, g.away_team, g.tipoff) for g in upcoming],
                made_at, summary.run_id, wide, report_out, logged, params,
                cache_root=model_cache or DEFAULT_CONTEXT_CACHE,
            )  # fmt: skip
            summary.model_status[name] = f"{info}"
        info_s = settle_full_shadow(con, made_at)
        summary.model_status[name + "_settle"] = f"{info_s}"
    except Exception as exc:
        summary.shadow_errors.append(
            f"full shadow: {type(exc).__name__}: {exc} ({time.perf_counter() - t0:.0f}s)"[:300]
        )


def _avg_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    summary: RunSummary,
    shared: dict[str, Any],
    bundle_root: Path | None = None,
    hb_dir: Path | None = None,
) -> None:
    """Log AVG = 0.5 pmf(SEQc) + 0.5 pmf(FULL) for the slate group (CPU inference in a child
    process), then settle.

    Own tables (``forward_avg_*``); ``forward_predictions`` and every scored production table
    are untouched. Statuses ok / skipped (no weights installed) / failed go to the heartbeat
    table and ``data/ops/heartbeat/avg_shadow.json``; an exception is recorded in
    ``shadow_errors`` and nothing else changes. Never retried after tip: only games with
    ``made_at < tipoff`` are passed."""
    import time

    from nba.daily.avg_shadow import bundle as avg_bundle
    from nba.daily.avg_shadow import run as avg_run
    from nba.daily.avg_shadow.preflight import sleep_warning
    from nba.daily.avg_shadow.run import run_avg_shadow
    from nba.daily.avg_shadow.settle import settle_avg_shadow
    from nba.daily.pplay_shadow.features import SlateGame

    name = "avg_shadow"
    t0 = time.perf_counter()
    try:
        if upcoming:
            warn = sleep_warning()
            if warn:  # a warning only: it alerts, it never blocks or alters the step
                summary.shadow_errors.append(f"avg shadow: {warn}"[:300])
            wide = shared.get("wide")
            hint: dict[tuple[str, int], int] = {}
            if wide is not None:
                rows = wide.select("game_id", "player_id", "team_id").unique()
                hint = {
                    (str(r["game_id"]), int(r["player_id"])): int(r["team_id"])
                    for r in rows.iter_rows(named=True)
                }
            info = run_avg_shadow(
                con, run_date,
                [SlateGame(g.game_id, g.home_team, g.away_team, g.tipoff) for g in upcoming],
                made_at, summary.run_id, season=season_int_for_date(run_date),
                bundle_root=bundle_root or avg_bundle.DEFAULT_ROOT, team_hint=hint,
                hb_dir=hb_dir or avg_run.HB_DIR,
            )  # fmt: skip
            summary.model_status[name] = f"{info}"
            if info.get("status") == "failed":
                summary.shadow_errors.append(f"avg shadow: failed: {info.get('detail')}"[:300])
        info_s = settle_avg_shadow(con, made_at)
        summary.model_status[name + "_settle"] = f"{info_s}"
    except Exception as exc:
        summary.shadow_errors.append(
            f"avg shadow: {type(exc).__name__}: {exc} ({time.perf_counter() - t0:.0f}s)"[:300]
        )


def _combo_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    summary: RunSummary,
    preds: list[ForwardPrediction],
    official_roster: pl.DataFrame | None,
    elo_config: Path | None,
    model_cache: Path | None,
    shared: dict[str, Any],
    hb_dir: Path | None = None,
    tip_source: str = "real",
) -> None:
    """Log the COMBO_STOCKS grids for the slate group, then settle.

    blk / stl NB heads, the six-stat copula joint for PRA, PR, PA, RA and blk+stl, RECENT and INDEP
    comparators. Own tables (``forward_combo_*``); ``forward_predictions`` and every scored
    production table are untouched. Statuses ok / skipped / failed go to the heartbeat table and
    ``data/ops/heartbeat/combo_shadow.json``; a failure is recorded in ``shadow_errors`` and nothing
    else changes. Never retried after tip: only games with ``made_at < tipoff`` are passed.
    Wall-time budget (420 s, or the earliest tip within 35 min): ``run_combo_shadow`` records
    ``skipped`` / ``budget`` and writes nothing."""
    import time

    from nba.daily.combo_shadow import run as combo_run
    from nba.daily.combo_shadow.run import run_combo_shadow
    from nba.daily.combo_shadow.settle import settle_combo_shadow
    from nba.daily.pplay_shadow.features import SlateGame
    from nba.daily.predict import DEFAULT_CONTEXT_CACHE

    name = "combo_shadow"
    t0 = time.perf_counter()
    try:
        if upcoming:
            params, report_out, wide = _wide_roster_frame(
                con, run_date, upcoming, made_at, official_roster, elo_config, model_cache, shared
            )
            info = run_combo_shadow(
                con, run_date,
                [SlateGame(g.game_id, g.home_team, g.away_team, g.tipoff) for g in upcoming],
                made_at, summary.run_id, wide, report_out, params,
                hb_dir=hb_dir or combo_run.HB_DIR, cache_root=model_cache or DEFAULT_CONTEXT_CACHE,
                tip_source=tip_source,
            )  # fmt: skip
            summary.model_status[name] = f"{info}"
            if info.get("status") == "failed":
                summary.shadow_errors.append(f"combo shadow: failed: {info.get('detail')}"[:300])
        info_s = settle_combo_shadow(con, made_at)
        summary.model_status[name + "_settle"] = f"{info_s}"
    except Exception as exc:
        summary.shadow_errors.append(
            f"combo shadow: {type(exc).__name__}: {exc} ({time.perf_counter() - t0:.0f}s)"[:300]
        )


def _pplay_shadow(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    summary: RunSummary,
    preds: list[ForwardPrediction],
    official_roster: pl.DataFrame | None,
    elo_config: Path | None,
    model_cache: Path | None,
    tips_dir: Path | None,
    shared: dict[str, Any] | None = None,
) -> None:
    """Log p_play_r next to production's p_play for every rostered not-OUT player.

    Production's own ``predict_slate_context`` is called a second time with its thresholds relaxed
    (same report rule, same cached models), so the shadow sees the players production drops with the
    same feature code; ``forward_predictions`` and all props outputs are not touched."""
    import time

    from nba.daily.pplay_shadow.features import SlateGame
    from nba.daily.pplay_shadow.run import run_shadow
    from nba.daily.pplay_shadow.settle import settle_pplay_shadow

    name = "pplay_r_shadow"
    t0 = time.perf_counter()
    try:
        if upcoming:
            params, report_out, wide = _wide_roster_frame(
                con, run_date, upcoming, made_at, official_roster, elo_config, model_cache, shared
            )
            logged = {
                (p.game_id, p.player_id)
                for p in preds
                if p.model_name == CONTEXT_PROPS_MODEL_NAME and p.player_id >= 0
            }
            info = run_shadow(
                con, run_date,
                [SlateGame(g.game_id, g.home_team, g.away_team, g.tipoff) for g in upcoming],
                made_at, summary.run_id, wide, logged, params,
                tips_dir=tips_dir if tips_dir is not None else None,
            )  # fmt: skip
            summary.model_status[name] = f"{info}"
        info_s = settle_pplay_shadow(con, made_at)
        summary.model_status[name + "_settle"] = f"{info_s}"
    except Exception as exc:
        summary.shadow_errors.append(
            f"pplay_r shadow: {type(exc).__name__}: {exc} ({time.perf_counter() - t0:.0f}s)"[:300]
        )


def _schedule_with_fallback(
    schedule_fn: ScheduleFn, season_s: str, cache_dir: Path | None, summary: RunSummary
) -> list[ScheduledGame]:
    """Fetch the schedule and cache it; on failure use the cached copy (error recorded)."""
    try:
        schedule = schedule_fn(season_s)
    except Exception as exc:
        cached = (
            load_schedule_cache(season_s, cache_dir, max_age=MAX_SCHEDULE_CACHE_AGE)
            if cache_dir is not None
            else None
        )
        if cached is None:
            raise
        summary.errors.append(
            f"schedule: fetch failed ({type(exc).__name__}: {exc}); using cached schedule"[:300]
        )
        return cached
    if cache_dir is not None and schedule:
        try:
            save_schedule_cache(schedule, season_s, cache_dir)
        except OSError as exc:
            summary.shadow_errors.append(f"schedule cache write failed: {exc}"[:200])
    return schedule


def _withdraw_misplaced(
    con: duckdb.DuckDBPyConnection,
    summary: RunSummary,
    made_at: datetime,
    upcoming: list[ScheduledGame],
    official: pl.DataFrame | None,
) -> None:
    """Withdraw forecasts for players the official roster puts on a team outside the game.

    Only with a fetched official roster (``official`` holds just the teams whose roster came back;
    ``recent`` runs never withdraw). Its own guard: a failure is recorded in ``summary.errors``
    (exit 5, alert) and never blocks the settle or the shadows below."""
    if official is None or official.is_empty():
        return
    try:
        teams: dict[int, set[int]] = {}
        for tid, pid in official.select("team_id", "player_id").unique().iter_rows():
            teams.setdefault(int(pid), set()).add(int(tid))
        n = withdraw_misplaced(
            con, summary.run_id, made_at,
            {g.game_id: (g.home_team, g.away_team) for g in upcoming}, teams,
        )  # fmt: skip
        if n:
            summary.model_status["withdrawals"] = f"{n} forecast keys withdrawn (misplaced players)"
    except Exception as exc:
        summary.errors.append(f"withdrawals: failed ({type(exc).__name__}: {exc})"[:300])


def _official_roster(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    season_s: str,
    roster_source: str,
    roster_dir: Path | None,
    fetch: RosterFetcher | None,
    rate_limiter: RateLimiter | None,
    summary: RunSummary,
    data_dir: Path = DEFAULT_DATA_DIR,
    static_fetch: Callable[[int], pl.DataFrame] | None = None,
) -> pl.DataFrame | None:
    """Pre-tip official rosters (30 cached-per-date calls) when ``roster_source='official'``;
    None (recent-games roster) otherwise or when nothing could be fetched. Never raises."""
    if roster_source == "recent":
        return None
    if roster_source != "official":
        raise ValueError(f"unknown roster_source {roster_source!r}")
    teams = list(range(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID + 1))
    frame, missing = load_official_rosters(
        run_date,
        season_s,
        teams,
        cache_root=roster_dir or DEFAULT_ROSTER_DIR,
        fetch=fetch,
        rate_limiter=rate_limiter,
    )
    if frame.is_empty():
        summary.model_status["roster_source"] = "official FAILED (no roster fetched) -> recent"
        return None
    note = f"official rosters for {len(teams) - len(missing)}/{len(teams)} teams"
    if missing:
        note += f" (missing {missing} -> recent-games roster for those)"
    biggest = max(Counter(frame["team_id"].to_list()).values(), default=0)
    cap = ForwardConfig().official_max_roster
    note += (
        f"; roster cap {'full official roster' if cap is None else cap} "
        f"(largest team lists {biggest}, floor {OFFICIAL_ROSTER_FLOOR})"
    )
    no_static = missing_static_ids(con, frame)
    if no_static:  # debutants: pull pedigree now so the draft-slot minutes prior applies today
        try:
            summary.static_autofill = autofill_players_static(
                con, no_static, data_dir=data_dir, rate_limiter=rate_limiter, fetch=static_fetch
            )
            no_static = missing_static_ids(con, frame)
        except Exception as exc:  # never fail the run
            summary.static_autofill = {"error": 1}
            print(f"players_static autofill skipped: {type(exc).__name__}: {exc}"[:200])
    if no_static:
        note += f"; {len(no_static)} rostered players lack players_static (no rookie prior)"
    summary.model_status["roster_source"] = note
    return frame


def _build_predictions(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    season_i: int,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    registry: RegistryAdapter | None,
    with_props: bool,
    elo_config: Path | None,
    summary: RunSummary,
    props_model: str = "context",
    model_cache: Path | None = None,
    official_roster: pl.DataFrame | None = None,
    log_int_variant: bool = False,
    log_lower_tail_variant: bool = False,
) -> list[ForwardPrediction]:
    out: list[ForwardPrediction] = []
    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    elo_model = resolve_production(registry, ELO_MODEL_NAME)
    summary.model_status[ELO_MODEL_NAME] = f"{elo_model.version} ({elo_model.status})"
    elo, n_train = fit_mov_elo(con, run_date, season_i, params)
    summary.stamp_inputs["elo"] = {
        "ratings": dict(elo.ratings_), "params": dict(params), "n_train": n_train,
    }  # fmt: skip
    probs = predict_win_probs(elo, [(g.home_team, g.away_team) for g in upcoming])
    inj_model = resolve_production(registry, INJURY_ELO_MODEL_NAME)
    inj = _injury_frame(con, run_date, season_i, upcoming, made_at, params, inj_model, summary)
    for g, p in zip(upcoming, probs, strict=True):
        row = inj.get(g.game_id) if inj is not None else None
        reason = "injury model unavailable" if row is None else row["fallback_reason"]
        out.append(
            ForwardPrediction(
                g.game_id,
                g.tipoff,
                ELO_MODEL_NAME,
                elo_model.version,
                "win_prob_home",
                {
                    "p_home": p,
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "n_train_games": n_train,
                    "features_as_of_before": run_date.isoformat(),
                    "artifact_path": elo_model.artifact_path,
                    "params": params,
                    "primary": reason is not None,
                    "fallback_reason": reason,
                },
            )
        )
        if row is not None and reason is None:
            out.append(
                ForwardPrediction(
                    g.game_id,
                    g.tipoff,
                    INJURY_ELO_MODEL_NAME,
                    inj_model.version,
                    "win_prob_home",
                    {
                        "p_home": float(row["p_injury_elo"]),
                        "p_mov_elo": float(row["p_mov_elo"]),
                        "home_team": g.home_team,
                        "away_team": g.away_team,
                        "d_out": float(row["d_out"]),
                        "d_doubt": float(row["d_doubt"]),
                        "report_as_of_et": str(row["report_as_of"]),
                        "coef_out": float(row["coef_out"]),
                        "coef_doubt": float(row["coef_doubt"]),
                        "n_train_games": int(row["n_train_games"]),
                        "n_signal_games": int(row["n_signal_games"]),
                        "features_as_of_before": run_date.isoformat(),
                        "artifact_path": inj_model.artifact_path,
                        "primary": True,
                        "fallback_reason": None,
                    },
                )
            )
    if not with_props:
        return out

    out_set = out_players(con, min(made_at, min(g.tipoff for g in upcoming)))
    summary.n_out_excluded = len(out_set)
    if props_model == "context":
        try:
            out.extend(
                _context_props(
                    con,
                    run_date,
                    upcoming,
                    made_at,
                    params,
                    model_cache,
                    summary,
                    official_roster,
                    log_int_variant,
                    log_lower_tail_variant,
                )
            )
            return out
        except Exception as exc:  # recency model is the loud fallback
            summary.n_props_rows = 0
            reason = f"{type(exc).__name__}: {exc}"[:200]
            summary.model_status[CONTEXT_PROPS_MODEL_NAME] = f"FAILED ({reason}) -> recency"
            try:
                out.extend(
                    _recency_only_props(
                        con, run_date, upcoming, out_set, reason, summary, official_roster
                    )
                )
                return out
            except Exception as exc2:
                summary.n_props_rows = 0
                summary.model_status[RECENCY_PROPS_MODEL_NAME] = (
                    f"FAILED ({type(exc2).__name__}: {exc2})"[:200] + " -> rolling baseline"
                )
    summary.model_status[PROPS_MODEL_NAME] = f"{PROPS_VERSION} (rolling-average baseline)"
    teams = sorted({t for g in upcoming for t in (g.home_team, g.away_team)})
    base = rolling_prop_baseline(con, run_date, teams, out_set)
    team_game = {t: g for g in upcoming for t in (g.home_team, g.away_team)}
    for r in base.iter_rows(named=True):
        tg = team_game.get(int(r["team_id"]))
        if tg is None:
            continue
        g = tg
        for stat in PROP_STATS:
            out.append(
                ForwardPrediction(
                    g.game_id,
                    g.tipoff,
                    PROPS_MODEL_NAME,
                    PROPS_VERSION,
                    stat,
                    {
                        "mean": float(r[f"{stat}_mean"]),
                        "std": float(r[f"{stat}_std"]),
                        "family": "normal",
                        "n_games": int(r["n_games"]),
                        "features_as_of_before": run_date.isoformat(),
                    },
                    player_id=int(r["player_id"]),
                )
            )
            summary.n_props_rows += 1
    return out


def _injury_frame(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    season_i: int,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    params: dict[str, float],
    model: ResolvedModel,
    summary: RunSummary,
) -> dict[str, dict[str, Any]] | None:
    """Injury-Elo forward frame keyed by game_id, or None (loud) on failure.
    Real tip-off times drive the report leakage filter inside ``predict_games``."""
    try:
        cfg, ridge, min_sig = load_injury_settings(model.artifact_path)
        games = pl.DataFrame(
            {
                "game_id": [g.game_id for g in upcoming],
                "game_date": [run_date] * len(upcoming),
                "season": [season_i] * len(upcoming),
                "home_team": [g.home_team for g in upcoming],
                "away_team": [g.away_team for g in upcoming],
                "tipoff": [g.tipoff for g in upcoming],
            }
        )
        rows = injury_report_rows(
            con,
            games["game_id"].to_list(),
            cfg.report_table,
            game_dates=dict(
                zip(games["game_id"].to_list(), games["game_date"].to_list(), strict=True)
            ),
        )
        frame = predict_games(
            con, made_at, games, rows,
            elo_params=params, cfg=cfg, ridge_lambda=ridge, min_signal_games=min_sig,
        )  # fmt: skip
    except Exception as exc:  # never block the MOV-Elo forecast
        summary.model_status[INJURY_ELO_MODEL_NAME] = (
            f"FAILED ({type(exc).__name__}: {exc})"[:200] + " -> rung0_mov_elo"
        )
        return None
    recs = {str(r["game_id"]): r for r in frame.iter_rows(named=True)}
    n_fb = sum(1 for r in recs.values() if r["fallback_reason"] is not None)
    summary.model_status[INJURY_ELO_MODEL_NAME] = (
        f"{model.version} ({model.status}); {len(recs) - n_fb} games with a report "
        f">={cfg.lead_minutes} min before tip, {n_fb} fell back to rung0_mov_elo "
        "(no usable report)"
    )
    return recs


def _additive_fields(r: dict[str, Any]) -> dict[str, object]:
    """Additive prediction fields (never alter an existing one): ``p_ge_full`` = full-support
    P(Y >= k) for integer-support re-scoring (nba.props.full_support), ``starter_rate`` for the
    pre-registered starter/bench slice. Absent when the producing frame does not carry them."""
    out: dict[str, object] = {}
    if r.get("p_ge_full") is not None:
        out["p_ge_full"] = json.loads(str(r["p_ge_full"]))
    sr = r.get("starter_rate")
    if sr is not None and math.isfinite(float(sr)):
        out["starter_rate"] = float(sr)
    return out


def _frame_preds(
    df: pl.DataFrame,
    upcoming: list[ScheduledGame],
    model_name: str,
    version: str,
    run_date: date,
    extra: dict[str, Any],
) -> list[ForwardPrediction]:
    """One ForwardPrediction per (player, stat) row of a ``forward`` output frame."""
    by_game = {g.game_id: g for g in upcoming}
    rows: list[ForwardPrediction] = []
    for r in df.iter_rows(named=True):
        g = by_game[str(r["game_id"])]
        rows.append(
            ForwardPrediction(
                g.game_id,
                g.tipoff,
                model_name,
                version,
                str(r["stat"]),
                {
                    "mean": float(r["mean"]),
                    "std": float(r["std"]),
                    "family": str(r["dist_family"]),
                    "dist_params": json.loads(r["dist_params"]),
                    "p_ge": json.loads(r["p_ge"]),
                    "q10": float(r["q10"]),
                    "q50": float(r["q50"]),
                    "q90": float(r["q90"]),
                    "q_grid": json.loads(r["q_grid"]),
                    "p_play": float(r["p_play"]),
                    "mean_uncond": float(r["mean_uncond"]),
                    "p_ge_uncond": json.loads(r["p_ge_uncond"]),
                    "routed_to": str(r["model"]),
                    "bucket": str(r["bucket"]),
                    "mean_sim": r["mean_sim"],
                    "mean_recency": float(r["mean_season_avg"]),
                    "proj_minutes": float(r["proj_minutes"]),
                    "n_games": int(r["n_games_prior"]),
                    **_additive_fields(r),
                    "features_as_of_before": run_date.isoformat(),
                    **extra,
                },
                player_id=int(r["player_id"]),
            )
        )
    return rows


def _context_props(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    made_at: datetime,
    elo_params: dict[str, float],
    model_cache: Path | None,
    summary: RunSummary,
    official_roster: pl.DataFrame | None = None,
    log_int_variant: bool = False,
    log_lower_tail_variant: bool = False,
) -> list[ForwardPrediction]:
    """Context-residual primary + recency comparison. Report rule: the latest
    official report stamped <= min(now, real tip-off - 60 min) per game."""
    report_out = slate_report_outs(con, [(g.game_id, g.tipoff) for g in upcoming], made_at)
    summary.n_out_excluded = len(set().union(*report_out.values())) if report_out else 0
    res = context_prop_predictions(
        con, run_date, [(g.game_id, g.home_team, g.away_team) for g in upcoming],
        report_out, elo_params, cache_root=model_cache, official_roster=official_roster,
        int_variant=log_int_variant, lower_tail_variant=log_lower_tail_variant,
    )  # fmt: skip
    info = res.info
    summary.grids199 = res.grids199
    summary.stamp_inputs["props_info"] = {
        "train_through": info.get("train_through"), "n_train_rows": info.get("n_train_rows"),
    }  # fmt: skip
    n_rep = len(report_out)
    common = {"n_games_with_report": n_rep, "n_slate_games": len(upcoming)}
    primary = _frame_preds(
        res.primary, upcoming, CONTEXT_PROPS_MODEL_NAME, CONTEXT_PROPS_VERSION, run_date,
        {**common, "fallback_reason": None, "train_through": info.get("train_through")},
    )  # fmt: skip
    for p in primary:
        if p.prediction["routed_to"] == "recency_fallback":
            p.prediction["fallback_reason"] = "fewer than 5 prior played games"
    summary.n_props_rows += len(primary)
    # everything below is comparison/shadow: a failure drops only that arm (recorded), never
    # the finished primary rows
    recency: list[ForwardPrediction] = []
    try:
        recency = _frame_preds(
            res.recency, upcoming, RECENCY_PROPS_MODEL_NAME, RECENCY_PROPS_VERSION, run_date,
            common,
        )  # fmt: skip
    except Exception as exc:
        summary.shadow_errors.append(f"recency comparison: {type(exc).__name__}: {exc}"[:200])
    variant: list[ForwardPrediction] = []
    if res.integer_variant is not None:
        try:
            variant = _frame_preds(
                res.integer_variant, upcoming, CONTEXT_INT_PROPS_MODEL_NAME,
                CONTEXT_PROPS_VERSION, run_date, common,
            )  # fmt: skip
            summary.model_status[CONTEXT_INT_PROPS_MODEL_NAME] = (
                f"{CONTEXT_PROPS_VERSION} (comparison: integer-support quantiles, "
                f"{len(variant)} rows)"
            )
        except Exception as exc:
            variant = []
            summary.shadow_errors.append(f"_int arm: {type(exc).__name__}: {exc}"[:200])
    elif info.get("int_variant_error"):
        summary.shadow_errors.append(f"_int arm: {info['int_variant_error']}"[:200])
    lt_rows: list[ForwardPrediction] = []
    if res.lower_tail_variant is not None:
        try:
            lt_rows = _frame_preds(
                res.lower_tail_variant, upcoming, CONTEXT_LT_PROPS_MODEL_NAME,
                CONTEXT_PROPS_VERSION, run_date, common,
            )  # fmt: skip
            summary.model_status[CONTEXT_LT_PROPS_MODEL_NAME] = (
                f"{CONTEXT_PROPS_VERSION} (SHADOW comparison: pts short-minutes mixture, "
                f"integer-support quantiles, {len(lt_rows)} rows)"
            )
        except Exception as exc:
            lt_rows = []
            summary.shadow_errors.append(f"_lt arm: {type(exc).__name__}: {exc}"[:200])
    elif log_lower_tail_variant:
        summary.model_status[CONTEXT_LT_PROPS_MODEL_NAME] = (
            f"SHADOW wrote no rows ({info.get('lower_tail_error', 'no eligible pts rows')})"
        )
        if info.get("lower_tail_error"):
            summary.shadow_errors.append(f"_lt arm: {info['lower_tail_error']}"[:200])
    cache = "cached" if info.get("cache_hit") else f"fit {info.get('fit_seconds', 0.0):.0f}s"
    summary.model_status[CONTEXT_PROPS_MODEL_NAME] = (
        f"{CONTEXT_PROPS_VERSION} (LightGBM residual over recency average; trained through "
        f"{info.get('train_through')}, n={info.get('n_train_rows')}, {cache}; "
        f"{info.get('n_context_rows', 0)} model rows, {info.get('n_fallback_rows', 0)} recency "
        f"fallback rows; {n_rep}/{len(upcoming)} games had a report >=60 min before tip)"
    )
    summary.model_status[RECENCY_PROPS_MODEL_NAME] = (
        f"{RECENCY_PROPS_VERSION} (comparison: recency-weighted played-games average)"
    )
    return primary + recency + variant + lt_rows


def _recency_only_props(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    out_set: set[int],
    reason: str,
    summary: RunSummary,
    official_roster: pl.DataFrame | None = None,
) -> list[ForwardPrediction]:
    """Context model failed: the recency model is written under BOTH the primary
    and the comparison names, the primary rows carrying the failure reason."""
    df = recency_prop_predictions(
        con, run_date, [(g.game_id, g.home_team, g.away_team) for g in upcoming], out_set,
        official_roster,
    )  # fmt: skip
    common = {"n_slate_games": len(upcoming)}
    primary = _frame_preds(
        df, upcoming, CONTEXT_PROPS_MODEL_NAME, CONTEXT_PROPS_VERSION, run_date,
        {**common, "fallback_reason": f"context model failed: {reason}"},
    )  # fmt: skip
    for p in primary:
        p.prediction["routed_to"] = "recency_fallback"
    summary.n_props_rows += len(primary)
    summary.model_status[RECENCY_PROPS_MODEL_NAME] = (
        f"{RECENCY_PROPS_VERSION} (used as primary for this slate)"
    )
    return primary + _frame_preds(
        df, upcoming, RECENCY_PROPS_MODEL_NAME, RECENCY_PROPS_VERSION, run_date, common
    )
