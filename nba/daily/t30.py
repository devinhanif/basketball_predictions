"""T-30 shadow prediction path ("lineups known", docs/LINEUPS_KNOWN.md).

SHADOW ONLY. Production stays at T-60 (``props_context_residual``); this logs the
comparison model ``props_context_residual_t30`` for games whose confirmed starting five
was published before tip-off minus 30 minutes. It is never primary and nothing reads it
for parlays.

Leak discipline
---------------
* The lineup snapshot used for a game is the latest one FETCHED strictly before
  ``tip - 30 min`` (``nba.lineups.store.latest_snapshot_before``), and
  ``made_at = now`` must be before tip-off or ``append_predictions`` raises
  :class:`nba.daily.store.LeakageError` (whole batch refused).
* Everything else is the T-60 information set: the official injury report rule is the same
  ``min(now, tip - 60 min)`` cutoff, so the only difference between the arms is tonight's
  announced five and active/inactive list.
* Train/serve skew (documented, monitored by the report): the model is FIT on box-score
  starters (``player_game_stats.starter``) and SERVED on announced starters.

A game with no usable confirmed lineup is not predicted; the reason is recorded in
``forward_t30_decisions`` (one row per game, idempotent).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.daily.pipeline import RunSummary, _frame_preds, _official_roster, utcnow
from nba.daily.predict import REPO_ROOT, load_elo_params, slate_report_outs
from nba.daily.schedule import ScheduledGame, ScheduleFn, slate_for_date
from nba.daily.season import season_str_for_date
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions
from nba.ingest.cache import RateLimiter
from nba.lineups.source import CONFIRMED
from nba.lineups.store import T30_LEAD_MINUTES, games_with_tips, latest_snapshot_before
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    predict_rows,
)
from nba.props.forward import (
    OUTPUT_COLUMNS,
    PROP_STATS,
    QUANTILE_TAUS,
    ForwardConfig,
    _ctx_row,
    _history_frames,
    _load_or_fit_models,
    _with_slate_placeholders,
    cfg_thresholds,
    predict_slate,
)
from nba.props.rosters import RosterFetcher

#: floor on the gap between metered nba_api calls made from the 5-minute T-30 tick
T30_MIN_INTERVAL_S = 2.5
T30_MODEL_NAME = "props_context_residual_t30"
T30_VERSION = "ctxres-v1-t30"
DEFAULT_T30_CACHE = REPO_ROOT / "data" / "models" / "context_residual_t30"

DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS forward_t30_decisions (
    game_id VARCHAR PRIMARY KEY,
    decided_at TIMESTAMP,
    tipoff TIMESTAMP,
    cutoff TIMESTAMP,          -- tip-off minus 30 min
    snapshot_id VARCHAR,
    snapshot_fetched_at TIMESTAMP,
    outcome VARCHAR,           -- 'logged' | 'skipped'
    reason VARCHAR,
    n_rows INT
);
"""


@dataclass(frozen=True)
class TeamLineup:
    team_id: int
    lineup_status: str
    starters: frozenset[int]
    inactive: frozenset[int]
    active: frozenset[int]
    source_ts: datetime | None


@dataclass(frozen=True)
class GameLineup:
    game_id: str
    snapshot_id: str
    fetched_at: datetime
    home: TeamLineup
    away: TeamLineup

    @property
    def starters(self) -> frozenset[int]:
        return self.home.starters | self.away.starters

    @property
    def inactive(self) -> frozenset[int]:
        return self.home.inactive | self.away.inactive


@dataclass
class T30Summary:
    run_id: str = ""
    n_slate: int = 0
    n_not_yet: int = 0
    n_already_decided: int = 0
    n_logged_games: int = 0
    n_skipped_games: int = 0
    n_rows_written: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def schedule_from_lineups(lcon: duckdb.DuckDBPyConnection) -> ScheduleFn:
    """Slate from the collector's own store (tips from the schedule or the feed's status
    text; teams from the snapshots). No network, so a 5-minute tick never touches the
    stats.nba.com quota."""

    def fn(season: str) -> list[ScheduledGame]:
        return [
            ScheduledGame(g, tip, h, a)
            for g, tip, h, a in games_with_tips(lcon, include_unseen=True)
        ]

    return fn


def ensure_decisions_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DECISIONS_DDL)


def load_game_lineup(
    lcon: duckdb.DuckDBPyConnection, g: ScheduledGame, cutoff: datetime
) -> tuple[GameLineup | None, str, tuple[str, datetime] | None]:
    """(lineup or None, reason, snapshot id/time). ``reason`` is '' when usable."""
    snap = latest_snapshot_before(lcon, g.game_id, cutoff)
    if snap is None:
        return None, "no_snapshot_before_t30", None
    sid, fetched = snap
    rows = lcon.execute(
        "SELECT team_id, lineup_status, announced_starter, roster_status, player_id, source_ts "
        "FROM lineup_snapshots WHERE snapshot_id = ? AND game_id = ?",
        [sid, g.game_id],
    ).fetchall()
    teams: dict[int, list[tuple[Any, ...]]] = {}
    for r in rows:
        teams.setdefault(int(r[0]), []).append(r)
    built: dict[int, TeamLineup] = {}
    for tid in (g.home_team, g.away_team):
        tr = teams.get(tid)
        if not tr:
            return None, f"team_{tid}_missing_in_snapshot", snap
        statuses = {str(r[1]) for r in tr}
        status = CONFIRMED if statuses == {CONFIRMED} else "/".join(sorted(statuses))
        ts = [r[5] for r in tr if r[5] is not None]
        built[tid] = TeamLineup(
            tid,
            status,
            frozenset(int(r[4]) for r in tr if r[2]),
            frozenset(int(r[4]) for r in tr if str(r[3]).lower() == "inactive"),
            frozenset(int(r[4]) for r in tr if str(r[3]).lower() == "active"),
            max(ts) if ts else None,
        )
    h, a = built[g.home_team], built[g.away_team]
    if h.lineup_status != CONFIRMED or a.lineup_status != CONFIRMED:
        return (
            None,
            f"lineup_not_confirmed:home={h.lineup_status},away={a.lineup_status}",
            snap,
        )
    if len(h.starters) != 5 or len(a.starters) != 5:
        return None, f"starters_not_5:home={len(h.starters)},away={len(a.starters)}", snap
    return GameLineup(g.game_id, sid, fetched, h, a), "", snap


def _slate_pgs_with_starters(
    a_pgs: pl.DataFrame,
    slate_ids: list[str],
    lineups: dict[str, GameLineup],
    team_of: dict[tuple[str, int], int],
) -> pl.DataFrame:
    """Overwrite the placeholder ``starter`` flag of slate rows with the ANNOUNCED five; an
    announced starter missing from the projected roster is appended so the five-man
    comparisons (``t30_n_changed`` ...) see all five (it is not predicted: no history)."""
    keys = [(gid, pid) for gid, gl in lineups.items() for pid in sorted(gl.starters)]
    st = pl.DataFrame(
        {"game_id": [k[0] for k in keys], "player_id": [k[1] for k in keys]},
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
    ).with_columns(pl.lit(True).alias("_ann"))
    hist = a_pgs.filter(~pl.col("game_id").is_in(slate_ids))
    sl = a_pgs.filter(pl.col("game_id").is_in(slate_ids))
    sl = (
        sl.join(st, on=["game_id", "player_id"], how="left")
        .with_columns(pl.col("_ann").fill_null(False).alias("starter"))
        .drop("_ann")
    )
    have = {(str(r[0]), int(r[1])) for r in sl.select("game_id", "player_id").iter_rows()}
    extra = [
        {
            "game_id": gid, "player_id": pid, "team_id": team_of[(gid, pid)], "minutes": 1.0,
            "pts": 0, "reb": 0, "ast": 0, "fg3m": 0, "starter": True,
        }
        for gid, pid in keys
        if (gid, pid) not in have
    ]  # fmt: skip
    parts = [hist, sl]
    if extra:
        parts.append(pl.DataFrame(extra))
    return pl.concat(parts, how="diagonal_relaxed")


def t30_prop_predictions(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    games: list[ScheduledGame],
    lineups: dict[str, GameLineup],
    report_out: dict[str, set[int]],
    elo_params: dict[str, float],
    *,
    cache_root: Path | None = None,
    cfg: ContextResidualConfig | None = None,
    official_roster: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Context-residual rows (context model only; no recency-fallback rows) for ``games``
    with the T-30 feature set. Fit on played rows strictly before ``slate``."""
    cfg = cfg or ContextResidualConfig()
    frame = pl.DataFrame(
        {
            "game_id": [g.game_id for g in games],
            "home_team": [g.home_team for g in games],
            "away_team": [g.away_team for g in games],
        },
        schema={"game_id": pl.Utf8, "home_team": pl.Int64, "away_team": pl.Int64},
    )
    exclude: set[int] = set()
    for s in report_out.values():
        exclude |= s
    for gl in lineups.values():
        exclude |= gl.inactive  # listed inactive at T-30: certainly not playing
    recency = predict_slate(
        con, slate, frame, exclude, config=ForwardConfig(),
        official_roster=official_roster,
    )  # fmt: skip
    if recency.is_empty():
        return recency, {"cache_hit": False, "n_context_rows": 0}
    h_games, h_pgs, static, avail = _history_frames(con, slate)
    flagged, _ = flagged_from_availability(avail, h_games, cfg.report)
    flagged = {**flagged, **{str(g): set(v) for g, v in report_out.items()}}
    roster = recency.select("game_id", "player_id", "team_id").unique()
    a_games, a_pgs = _with_slate_placeholders(h_games, h_pgs, slate, frame, roster)
    team_of = {(g.game_id, p): t for g in games for t, ps in
               ((g.home_team, lineups[g.game_id].home.starters),
                (g.away_team, lineups[g.game_id].away.starters)) for p in ps}  # fmt: skip
    a_pgs = _slate_pgs_with_starters(a_pgs, frame["game_id"].to_list(), lineups, team_of)
    feats = build_features(a_games, a_pgs, static, flagged, elo_params, lineups_known=True)
    cache_dir = None if cache_root is None else cache_root / slate.isoformat()
    models, info = _load_or_fit_models(feats, slate, cfg, cache_dir, lineups_known=True)
    slate_feats = feats.filter(pl.col("game_id").is_in(frame["game_id"].to_list()))
    taus19 = np.array(QUANTILE_TAUS)
    by_key = {(r["game_id"], r["player_id"], r["stat"]): r for r in recency.iter_rows(named=True)}
    out_rows: list[dict[str, Any]] = []
    for stat in PROP_STATS:
        rows, mean, q199 = predict_rows(models, slate_feats, stat, CRPS_TAUS)
        if rows.is_empty():
            continue
        _, _, q19 = predict_rows(models, slate_feats, stat, taus19)
        for i, r in enumerate(rows.iter_rows(named=True)):
            base = by_key.get((str(r["game_id"]), int(r["player_id"]), stat))
            if base is not None:
                out_rows.append(
                    _ctx_row(base, float(mean[i]), q19[i], q199[i], cfg_thresholds(None, stat))
                )
    info["n_context_rows"] = len(out_rows)
    if not out_rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS}), info
    out = (
        pl.DataFrame(out_rows, infer_schema_length=None)
        .select(OUTPUT_COLUMNS)
        .sort(["game_id", "team_id", "player_id", "stat"])
    )
    return out, info


def _record(
    con: duckdb.DuckDBPyConnection,
    g: ScheduledGame,
    now: datetime,
    cutoff: datetime,
    snap: tuple[str, datetime] | None,
    outcome: str,
    reason: str,
    n_rows: int,
) -> None:
    con.execute(
        "INSERT INTO forward_t30_decisions VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        [g.game_id, now, g.tipoff, cutoff, snap[0] if snap else None,
         snap[1] if snap else None, outcome, reason, n_rows],
    )  # fmt: skip


def run_t30(
    con: duckdb.DuckDBPyConnection,
    lcon: duckdb.DuckDBPyConnection,
    run_date: date,
    *,
    schedule_fn: ScheduleFn,
    now: datetime | None = None,
    model_cache: Path | None = None,
    roster_source: str = "recent",
    elo_config: Path | None = None,
    rate_limiter: RateLimiter | None = None,
    roster_dir: Path | None = None,
    roster_fetch: RosterFetcher | None = None,
    static_fetch: Callable[[int], pl.DataFrame] | None = None,
) -> T30Summary:
    """Log T-30 shadow props for slate games that are at/after their T-30 decision time and
    not yet tipped. Idempotent per game (``forward_t30_decisions``). ``now`` is naive UTC."""
    made_at = now if now is not None else utcnow()
    ensure_decisions_table(con)
    summ = T30Summary(run_id="t30_" + made_at.strftime("%Y%m%dT%H%M%S"))
    slate = slate_for_date(schedule_fn(season_str_for_date(run_date)), run_date)
    summ.n_slate = len(slate)
    decided = {
        str(r[0]) for r in con.execute("SELECT game_id FROM forward_t30_decisions").fetchall()
    }
    lead = timedelta(minutes=T30_LEAD_MINUTES)
    ready: dict[str, GameLineup] = {}
    ready_games: list[ScheduledGame] = []
    for g in slate:
        cutoff = g.tipoff - lead
        if g.game_id in decided:
            summ.n_already_decided += 1
            continue
        if made_at < cutoff:
            summ.n_not_yet += 1
            continue
        if not made_at < g.tipoff:
            _record(con, g, made_at, cutoff, None, "skipped", "run_after_tipoff", 0)
            summ.skipped[g.game_id] = "run_after_tipoff"
            summ.n_skipped_games += 1
            continue
        gl, reason, snap = load_game_lineup(lcon, g, cutoff)
        if gl is None:
            _record(con, g, made_at, cutoff, snap, "skipped", reason, 0)
            summ.skipped[g.game_id] = reason
            summ.n_skipped_games += 1
            continue
        ready[g.game_id] = gl
        ready_games.append(g)
    if not ready_games:
        return summ

    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    report_out = slate_report_outs(con, [(g.game_id, g.tipoff) for g in ready_games], made_at)
    official = None
    if roster_source == "official":
        # same limiter + per-date roster cache as `nba.daily run`: a tick after the pretip
        # run is a pure cache hit; an uncached team is fetched through the limiter (>= 2.5 s)
        official = _official_roster(
            con, run_date, season_str_for_date(run_date), roster_source, roster_dir,
            roster_fetch, rate_limiter or RateLimiter(T30_MIN_INTERVAL_S),
            RunSummary(run_id="t30", run_date=run_date), static_fetch=static_fetch,
        )  # fmt: skip
    df, info = t30_prop_predictions(
        con, run_date, ready_games, ready, report_out, params,
        cache_root=model_cache or DEFAULT_T30_CACHE, official_roster=official,
    )  # fmt: skip
    preds: list[ForwardPrediction] = []
    per_game: dict[str, int] = {}
    for g in ready_games:
        sub = df.filter(pl.col("game_id") == g.game_id) if not df.is_empty() else df
        gl = ready[g.game_id]
        extra = {
            "lineups_known": True,
            "snapshot_id": gl.snapshot_id,
            "snapshot_fetched_at": gl.fetched_at.isoformat(),
            "snapshot_age_min_before_tip": (g.tipoff - gl.fetched_at).total_seconds() / 60.0,
            "home_lineup_source_ts": gl.home.source_ts.isoformat() if gl.home.source_ts else None,
            "away_lineup_source_ts": gl.away.source_ts.isoformat() if gl.away.source_ts else None,
            "fallback_reason": None,
            "train_through": info.get("train_through"),
            "comparison_only": True,
        }
        rows = _frame_preds(sub, [g], T30_MODEL_NAME, T30_VERSION, run_date, extra)
        per_game[g.game_id] = len(rows)
        preds.extend(rows)
    try:
        summ.n_rows_written = append_predictions(con, summ.run_id, made_at, preds)
    except LeakageError:
        summ.notes.append("LeakageError: batch refused")
        raise
    for g in ready_games:
        n = per_game[g.game_id]
        gl = ready[g.game_id]
        if n == 0:
            _record(con, g, made_at, g.tipoff - lead, (gl.snapshot_id, gl.fetched_at),
                    "skipped", "no_model_rows", 0)  # fmt: skip
            summ.skipped[g.game_id] = "no_model_rows"
            summ.n_skipped_games += 1
        else:
            _record(con, g, made_at, g.tipoff - lead, (gl.snapshot_id, gl.fetched_at),
                    "logged", f"ok; {len(gl.inactive)} listed-inactive excluded", n)  # fmt: skip
            summ.n_logged_games += 1
    return summ
