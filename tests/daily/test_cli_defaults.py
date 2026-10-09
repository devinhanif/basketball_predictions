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
