"""Orchestrates one daily run. All side-effecting collaborators are injectable
so the fixture tests never touch the network or the real database."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from nba.daily.ingest_step import FetchGames, PullBox, incremental_ingest
from nba.daily.injury import ProbeFn, PullFn, out_players, pull_latest_report
from nba.daily.predict import (
    ELO_MODEL_NAME,
    PROP_STATS,
    PROPS_MODEL_NAME,
    PROPS_VERSION,
    fit_mov_elo,
    load_elo_params,
    predict_win_probs,
    resolve_production,
    rolling_prop_baseline,
)
from nba.daily.schedule import ScheduledGame, ScheduleFn, slate_for_date
from nba.daily.season import season_int_for_date, season_str_for_date
from nba.daily.settle import settle_pending
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions, ensure_tables
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter
from nba.registry.protocol import RegistryAdapter


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class RunSummary:
    run_id: str
    run_date: date
    n_slate: int = 0
    n_predicted_games: int = 0
    n_refused_after_tipoff: int = 0
    n_rows_written: int = 0
    n_props_rows: int = 0
    injury_report_et: str | None = None
    injury_status: str = "skipped"
    n_out_excluded: int = 0
    model_status: dict[str, str] = field(default_factory=dict)
    ingest: dict[str, int] = field(default_factory=dict)
    settle: dict[str, int] = field(default_factory=dict)


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
) -> RunSummary:
    """Ingest -> injury report -> predict -> append -> settle. ``now`` is naive UTC."""
    made_at = now if now is not None else utcnow()
    ensure_tables(con)
    summary = RunSummary(run_id=uuid.uuid4().hex[:12], run_date=run_date)
    season_i, season_s = season_int_for_date(run_date), season_str_for_date(run_date)

    if not skip_ingest:
        summary.ingest = incremental_ingest(
            con,
            season_s,
            season_i,
            data_dir=data_dir,
            rate_limiter=rate_limiter,
            fetch_games=fetch_games,
            pull_box=pull_box,
        )

    slate = slate_for_date(schedule_fn(season_s), run_date)
    summary.n_slate = len(slate)
    upcoming: list[ScheduledGame] = [g for g in slate if made_at < g.tipoff]
    summary.n_refused_after_tipoff = len(slate) - len(upcoming)

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
            )
            summary.injury_report_et = slot.isoformat() if slot else None
            summary.injury_status = "ok" if slot else "no_report_found"
        except Exception as exc:  # injury feed must never block the Elo forecast
            summary.injury_status = f"failed: {type(exc).__name__}: {exc}"[:200]

    if upcoming:
        preds = _build_predictions(con, run_date, season_i, upcoming, made_at, registry,
                                   with_props, elo_config, summary)  # fmt: skip
        try:
            summary.n_rows_written = append_predictions(con, summary.run_id, made_at, preds)
        except LeakageError:
            summary.n_refused_after_tipoff += len(upcoming)
            raise
        summary.n_predicted_games = len(upcoming)

    summary.settle = settle_pending(con, made_at)
    return summary


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
) -> list[ForwardPrediction]:
    out: list[ForwardPrediction] = []
    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    elo_model = resolve_production(registry, ELO_MODEL_NAME)
    summary.model_status[ELO_MODEL_NAME] = f"{elo_model.version} ({elo_model.status})"
    elo, n_train = fit_mov_elo(con, run_date, season_i, params)
    probs = predict_win_probs(elo, [(g.home_team, g.away_team) for g in upcoming])
    for g, p in zip(upcoming, probs, strict=True):
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
                },
            )
        )
    if not with_props:
        return out

    summary.model_status[PROPS_MODEL_NAME] = f"{PROPS_VERSION} (rolling-average baseline)"
    out_set = out_players(con, min(made_at, min(g.tipoff for g in upcoming)))
    teams = sorted({t for g in upcoming for t in (g.home_team, g.away_team)})
    base = rolling_prop_baseline(con, run_date, teams, out_set)
    summary.n_out_excluded = len(out_set)
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
