"""The daily CLI must default to the production props model (context-residual)."""

from __future__ import annotations

import inspect

from nba.daily.__main__ import _parser
from nba.daily.pipeline import run_daily


def test_cli_props_model_defaults_to_production_context() -> None:
    args = _parser().parse_args(["run", "--date", "2026-10-20"])
    assert args.props_model == "context"


def test_cli_default_matches_pipeline_default() -> None:
    default = inspect.signature(run_daily).parameters["props_model"].default
    assert _parser().parse_args(["run", "--date", "2026-10-20"]).props_model == default


def test_cli_rehearsal_flags_default_off() -> None:
    a = _parser().parse_args(["run", "--date", "2026-10-20"])
    assert a.schedule_from_db is False and a.now is None and a.model_cache is None


def test_cli_now_parses_as_naive_utc() -> None:
    from datetime import datetime

    a = _parser().parse_args(["run", "--date", "2024-10-22", "--now", "2024-10-22T17:50:00-04:00"])
    assert a.now == datetime(2024, 10, 22, 21, 50)
    assert _parser().parse_args(["settle", "--now", "2024-10-23T12:00"]).now == datetime(
        2024, 10, 23, 12, 0
    )


def test_cli_roster_source_defaults_to_recent_and_matches_pipeline() -> None:
    a = _parser().parse_args(["run", "--date", "2026-10-20"])
    assert a.roster_source == "recent"
    assert inspect.signature(run_daily).parameters["roster_source"].default == "recent"
    assert (
        _parser()
        .parse_args(["run", "--date", "2026-10-20", "--roster-source", "official"])
        .roster_source
        == "official"
    )
