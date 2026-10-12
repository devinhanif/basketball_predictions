"""N15: tip_source="real" is the default everywhere a ReportTriggerConfig is constructed; the 19:00
proxy is opt-in and every place that asks for it is named here, so a new one is a conscious edit."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import polars as pl

from nba.features.injury_report import ReportTriggerConfig
from nba.models.injury_elo import InjuryFeatureConfig
from nba.props.context_residual import ContextResidualConfig

ROOT = Path(__file__).resolve().parents[2]

#: files that may spell tip_source="proxy19" (docstrings, the opt-in switch, closed experiments)
NBA_ALLOWED = {
    "nba/features/injury_report.py",  # the switch itself and its docstring
    "nba/props/context_residual.py",  # comment: how to reproduce the old runs
    "nba/models/injury_elo.py",  # the explicit no-cutoff config of the ablation feature builder
}
RESEARCH_ALLOWED = {
    "research/eval/usage_redistribution_eval.py",  # closed; frame built on the proxy, no tip table
    "research/features/player_sequences.py",  # closed sequence harness, explicit pin
    "research/eval/xgb_pts_vs_lt_eval.py",  # closed; planted tests rebuild the frozen proxy frame
}


def test_defaults_are_real() -> None:
    assert ReportTriggerConfig().tip_source == "real"
    assert ContextResidualConfig().report.tip_source == "real"
    assert InjuryFeatureConfig().tip_source == "real"
    assert ReportTriggerConfig(tip_source="proxy19").tip_source == "proxy19"  # opt-in still works


def _files_naming_proxy(sub: str) -> set[str]:
    pat = re.compile(r"""tip_source\s*=\s*["']proxy19["']""")
    return {
        str(p.relative_to(ROOT))
        for p in (ROOT / sub).rglob("*.py")
        if pat.search(p.read_text(errors="ignore"))
    }


def test_proxy_is_requested_only_where_named() -> None:
    assert _files_naming_proxy("nba") <= NBA_ALLOWED
    assert _files_naming_proxy("research") <= RESEARCH_ALLOWED


def test_live_pretip_gates_force_real() -> None:
    # the live serving rule overrides whatever the caller's config says
    from nba.features.injury_report import serve_pretip_flagged

    rows = pl.DataFrame(
        {
            "game_id": ["g"],
            "player_id": [1],
            "status": ["out"],
            "as_of": [dt.datetime(2026, 10, 28, 12, 0)],
            "game_date": [dt.date(2026, 10, 28)],
        }
    )
    tip = {"g": dt.datetime(2026, 10, 28, 12, 30)}  # report is 30 min before tip: not usable
    flagged, used = serve_pretip_flagged(
        rows, tip, dt.datetime(2026, 10, 28, 12, 20), ReportTriggerConfig(tip_source="proxy19")
    )
    assert flagged == {} and used == {}
