"""The production T-60 lead has one name and one home (``nba.leadtime``); every other 60 equals it.

Two guards (2026-11-17):

1. Every module-level alias of the lead still equals ``nba.leadtime`` in value AND type (an int
   stays an int, a ``timedelta`` stays a ``timedelta``). Frozen scorers, settle steps and
   sha-pinned files keep their own literal (they are not edited); this test ties them to the
   one constant.
2. An AST scan of ``nba/`` for literal 60-minute leads (``timedelta(minutes=60)``, a ``*lead*`` name
   or argument bound to 60, ``60 * INTERVAL`` in SQL text). Every hit must be on the allow-list
   below with its reason; like ``tests/test_layering.py`` it fails both ways (a new literal is a
   regression, a vanished one means the list is stale). T-30 / T-15 leads are out of scope.
"""

from __future__ import annotations

import ast
import importlib
import re
from datetime import timedelta
from pathlib import Path

import pytest

from nba import leadtime

ROOT = Path(__file__).resolve().parents[1]


def test_the_constant() -> None:
    assert leadtime.LEAD_MINUTES == 60 and type(leadtime.LEAD_MINUTES) is int
    assert timedelta(minutes=60) == leadtime.LEAD and type(leadtime.LEAD) is timedelta


#: (module, attribute) -> the type the use site expects. Imported lazily, one module per case.
ALIASES: dict[tuple[str, str], type] = {
    # importing nba.leadtime (changed 2026-11-17)
    ("nba.daily.predict", "REPORT_LEAD_MINUTES"): int,
    ("nba.explain.data", "LEAD_MINUTES"): int,
    ("nba.shadow_llm.payload", "LEAD_MINUTES"): int,
    ("nba.daily.avg_shadow.display", "LEAD"): int,
    ("nba.daily.combo_shadow.display", "LEAD"): int,
    ("nba.lineups.analysis", "T60"): timedelta,
    # own literal, left alone: scoring / settle code of a frozen rule
    ("nba.daily.settle", "DEFAULT_LEAD_MINUTES"): int,
    ("nba.daily.avg_shadow.settle", "LEAD_MINUTES"): int,
    ("nba.daily.combo_shadow.settle", "LEAD_MINUTES"): int,
    ("nba.daily.full_shadow.settle", "LEAD_MINUTES"): int,
    ("nba.daily.pplay_shadow.settle", "LEAD_MINUTES"): int,
    ("nba.eval.avg_shadow_live", "LEAD_MINUTES"): int,
    ("nba.eval.full_shadow_live", "LEAD_MINUTES"): int,
    ("nba.eval.combo_shadow_live", "LEAD_MINUTES"): int,
}


@pytest.mark.parametrize(("mod", "attr"), sorted(ALIASES))
def test_alias_equals_the_constant(mod: str, attr: str) -> None:
    value = getattr(importlib.import_module(mod), attr)
    want = leadtime.LEAD if ALIASES[(mod, attr)] is timedelta else leadtime.LEAD_MINUTES
    assert type(value) is ALIASES[(mod, attr)], f"{mod}.{attr} changed type: {type(value)}"
    assert value == want, f"{mod}.{attr} = {value!r}, nba.leadtime says {want!r}"


def test_dataclass_defaults_equal_the_constant() -> None:
    """Defaults in files this lane does not edit (sha-pinned or another owner's directory)."""
    from nba.features.injury_report import ReportTriggerConfig
    from nba.models.injury_elo import InjuryFeatureConfig

    for cfg in (ReportTriggerConfig(), InjuryFeatureConfig()):
        assert type(cfg.lead_minutes) is int and cfg.lead_minutes == leadtime.LEAD_MINUTES


_LEADISH = re.compile(r"lead", re.IGNORECASE)
_SQL_60 = re.compile(r"\b60\s*\*\s*INTERVAL|INTERVAL\s*'?60\s*MINUTE", re.IGNORECASE)


def _is_60(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value == 60


def _names(target: ast.AST) -> list[str]:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)] + [
        n.attr for n in ast.walk(target) if isinstance(n, ast.Attribute)
    ]


def literal_leads() -> set[tuple[str, str]]:
    """(path relative to the repo, what) for every literal 60-minute lead under ``nba/``."""
    out: set[tuple[str, str]] = set()
    for p in sorted((ROOT / "nba").rglob("*.py")):
        rel = str(p.relative_to(ROOT))
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name == "timedelta":
                    kw = {k.arg: k.value for k in node.keywords}
                    if _is_60(kw.get("minutes")):
                        out.add((rel, "timedelta(minutes=60)"))
                    hrs = kw.get("hours")
                    if isinstance(hrs, ast.Constant) and hrs.value == 1 and len(kw) == 1:
                        out.add((rel, "timedelta(hours=1)"))
                for k in node.keywords:
                    if k.arg and _LEADISH.search(k.arg) and _is_60(k.value):
                        out.add((rel, f"call {k.arg}=60"))
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _is_60(node.value):
                    for t in targets:
                        for n in _names(t):
                            if _LEADISH.search(n):
                                out.add((rel, f"{n} = 60"))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                a = node.args
                pos = a.posonlyargs + a.args
                pairs = list(zip(pos[len(pos) - len(a.defaults) :], a.defaults, strict=True))
                pairs += [(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d]
                for arg, default in pairs:
                    if _LEADISH.search(arg.arg) and _is_60(default):
                        out.add((rel, f"def {node.name}({arg.arg}=60)"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _SQL_60.search(node.value):
                    out.add((rel, "SQL 60 * INTERVAL"))
    return out


FROZEN = "settle / scoring code of a frozen rule, not edited; alias test above"
PINNED = "sha-pinned by research rules (f1_warm_window, injury_timing, ...); default test above"
OTHER = "another lane's directory; proposal in docs/handoff_2026-11-17/lane_live_daily.md"
UNRELATED = "unrelated to the lead"
_M = "LEAD_MINUTES = 60"

#: Every literal the scan may find, with why it stays a literal.
ALLOWED: dict[tuple[str, str], str] = {
    ("nba/leadtime.py", _M): "the one home",
    ("nba/daily/settle.py", "DEFAULT_LEAD_MINUTES = 60"): FROZEN + " (FORWARD_PREREG rule 3)",
    ("nba/daily/avg_shadow/settle.py", _M): FROZEN + " (AVG_SHADOW_LIVE)",
    ("nba/daily/combo_shadow/settle.py", _M): FROZEN + " (COMBO_STOCKS_SHADOW)",
    ("nba/daily/full_shadow/settle.py", _M): FROZEN + " (FULL_SHADOW_LIVE)",
    ("nba/daily/pplay_shadow/settle.py", _M): FROZEN + " (PPLAY_INTO_PROPS_LIVE)",
    ("nba/eval/avg_shadow_live.py", _M): FROZEN + " (AVG_SHADOW_LIVE reader)",
    ("nba/eval/full_shadow_live.py", _M): FROZEN + " (FULL_SHADOW_LIVE reader)",
    ("nba/eval/combo_shadow_live.py", _M): FROZEN + " (COMBO_STOCKS_SHADOW reader)",
    ("nba/features/injury_report.py", "lead_minutes = 60"): PINNED,
    ("nba/models/injury_elo.py", "lead_minutes = 60"): OTHER + "; default test above",
    ("nba/ingest/reporter_tick.py", "timedelta(minutes=60)"): OTHER,
    ("nba/odds/combo_pull.py", "timedelta(minutes=60)"): OTHER,
    ("nba/data/report_freshness.py", "def freshness(lead_minutes=60)"): OTHER,
    ("nba/daily/replay_season.py", "timedelta(hours=1)"): UNRELATED + ": previous hour's run slots",
    ("nba/ops/watchdog.py", "timedelta(hours=1)"): UNRELATED + ": a drill heartbeat's duration",
}


def test_every_literal_lead_is_allowed() -> None:
    found = literal_leads()
    new = sorted(found - set(ALLOWED))
    assert not new, f"literal 60-minute lead outside nba.leadtime (import it, or allow it): {new}"


def test_allow_list_is_current() -> None:
    gone = sorted(set(ALLOWED) - literal_leads())
    assert not gone, f"allow-listed literals no longer present; remove them from ALLOWED: {gone}"
