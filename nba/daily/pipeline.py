"""Orchestrates one daily run. All side-effecting collaborators are injectable
so the fixture tests never touch the network or the real database."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.daily.ingest_step import FetchGames, PullBox, incremental_ingest
from nba.daily.injury import ProbeFn, PullFn, out_players, pull_latest_report
from nba.daily.predict import (
    CONTEXT_PROPS_MODEL_NAME,
    CONTEXT_PROPS_VERSION,
    ELO_MODEL_NAME,
    INJURY_ELO_MODEL_NAME,
    PROP_STATS,
    PROPS_MODEL_NAME,
    PROPS_VERSION,
    RECENCY_PROPS_MODEL_NAME,
    RECENCY_PROPS_VERSION,
    ROUTED_N_SIMS,
    ROUTED_PROPS_MODEL_NAME,
    ROUTED_PROPS_VERSION,
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
    routed_prop_predictions,
    slate_report_outs,
)
from nba.daily.schedule import ScheduledGame, ScheduleFn, slate_for_date
from nba.daily.season import season_int_for_date, season_str_for_date
from nba.daily.settle import settle_pending
from nba.daily.store import ForwardPrediction, LeakageError, append_predictions, ensure_tables
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter
from nba.models.injury_elo import predict_games
from nba.props.forward import SIM_STATS
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
    props_model: str = "context",
    n_sims: int = ROUTED_N_SIMS,
    model_cache: Path | None = None,
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
        preds = _build_predictions(
            con, run_date, season_i, upcoming, made_at, registry,
            with_props, elo_config, summary, props_model, n_sims, model_cache,
        )  # fmt: skip
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
    props_model: str = "context",
    n_sims: int = ROUTED_N_SIMS,
    model_cache: Path | None = None,
) -> list[ForwardPrediction]:
    out: list[ForwardPrediction] = []
    params = load_elo_params(elo_config) if elo_config else load_elo_params()
    elo_model = resolve_production(registry, ELO_MODEL_NAME)
    summary.model_status[ELO_MODEL_NAME] = f"{elo_model.version} ({elo_model.status})"
    elo, n_train = fit_mov_elo(con, run_date, season_i, params)
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
                _context_props(con, run_date, upcoming, made_at, params, model_cache, summary)
            )
            return out
        except Exception as exc:  # recency model is the loud fallback
            summary.n_props_rows = 0
            reason = f"{type(exc).__name__}: {exc}"[:200]
            summary.model_status[CONTEXT_PROPS_MODEL_NAME] = f"FAILED ({reason}) -> recency"
            try:
                out.extend(_recency_only_props(con, run_date, upcoming, out_set, reason, summary))
                return out
            except Exception as exc2:
                summary.n_props_rows = 0
                summary.model_status[RECENCY_PROPS_MODEL_NAME] = (
                    f"FAILED ({type(exc2).__name__}: {exc2})"[:200] + " -> rolling baseline"
                )
    if props_model == "routed":
        try:
            out.extend(_routed_props(con, run_date, upcoming, out_set, n_sims, summary))
            return out
        except Exception as exc:  # loud, recorded fallback; never blocks the Elo forecast
            summary.n_props_rows = 0
            summary.model_status[ROUTED_PROPS_MODEL_NAME] = (
                f"FAILED ({type(exc).__name__}: {exc})"[:200] + " -> rolling baseline"
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
        rows = injury_report_rows(con, games["game_id"].to_list(), cfg.report_table)
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
) -> list[ForwardPrediction]:
    """Context-residual primary + recency comparison. Report rule: the latest
    official report stamped <= min(now, real tip-off - 60 min) per game."""
    report_out = slate_report_outs(con, [(g.game_id, g.tipoff) for g in upcoming], made_at)
    summary.n_out_excluded = len(set().union(*report_out.values())) if report_out else 0
    res = context_prop_predictions(
        con, run_date, [(g.game_id, g.home_team, g.away_team) for g in upcoming],
        report_out, elo_params, cache_root=model_cache,
    )  # fmt: skip
    info = res.info
    n_rep = len(report_out)
    common = {"n_games_with_report": n_rep, "n_slate_games": len(upcoming)}
    primary = _frame_preds(
        res.primary, upcoming, CONTEXT_PROPS_MODEL_NAME, CONTEXT_PROPS_VERSION, run_date,
        {**common, "fallback_reason": None, "train_through": info.get("train_through")},
    )  # fmt: skip
    for p in primary:
        if p.prediction["routed_to"] == "recency_fallback":
            p.prediction["fallback_reason"] = "fewer than 5 prior played games"
    recency = _frame_preds(
        res.recency, upcoming, RECENCY_PROPS_MODEL_NAME, RECENCY_PROPS_VERSION, run_date, common
    )
    summary.n_props_rows += len(primary)
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
    return primary + recency


def _recency_only_props(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    out_set: set[int],
    reason: str,
    summary: RunSummary,
) -> list[ForwardPrediction]:
    """Context model failed: the recency model is written under BOTH the primary
    and the comparison names, the primary rows carrying the failure reason."""
    df = recency_prop_predictions(
        con, run_date, [(g.game_id, g.home_team, g.away_team) for g in upcoming], out_set
    )
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


def _routed_props(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    upcoming: list[ScheduledGame],
    out_set: set[int],
    n_sims: int,
    summary: RunSummary,
) -> list[ForwardPrediction]:
    df = routed_prop_predictions(
        con, run_date, [(g.game_id, g.home_team, g.away_team) for g in upcoming], out_set,
        n_sims=n_sims,
    )  # fmt: skip
    if SIM_STATS:
        routing = (
            f"sim for {'/'.join(SIM_STATS)} of cold-start/intermittent/erratic players, "
            "else recency-weighted played-games average"
        )
    else:
        routing = "sim routing disabled (SIM_STATS empty): recency-weighted played-games average"
    summary.model_status[ROUTED_PROPS_MODEL_NAME] = (
        f"{ROUTED_PROPS_VERSION} (n_sims={n_sims}; {routing})"
    )
    rows = _frame_preds(
        df, upcoming, ROUTED_PROPS_MODEL_NAME, ROUTED_PROPS_VERSION, run_date,
        {"n_sims": n_sims},
    )  # fmt: skip
    summary.n_props_rows += len(rows)
    return rows
