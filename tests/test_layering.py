"""Layering ratchet: the live path may not import research code, except where listed.

The live path is the static import closure of the scheduler entrypoints (``ops/*.sh``,
``ops/install_launchd.sh``). Lazy imports inside functions count: they are edges the live
path can take at 7 pm. ``KNOWN_EDGES`` is the entanglement list of docs/DESIGN_RESTRUCTURE.md
s3.3 as measured on 2026-10-09; each cut removes its rows. The test fails both ways: a new
edge is a regression, a vanished edge means this list is stale.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ENTRYPOINTS = (
    "nba.daily.__main__",
    "nba.datamanifest.__main__",
    "nba.ingest.__main__",
    "nba.ingest.queue",
    "nba.ingest.referees",
    "nba.lineups.__main__",
    "nba.markets.__main__",
    "nba.parlay.__main__",
    "nba.ops.watchdog",
)

#: Packages that hold research: experiments that reached a verdict, archived under the ledger.
RESEARCH_PREFIXES = (
    "nba.sim",
    "nba.stack",
    "nba.coldstart",
    "nba.eval",
    "nba.colab",
    "nba.preprocess",
)

#: (live module, research module) edges still present. Shrinks with every cut; never grows.
KNOWN_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("nba.daily.predict", "nba.eval.injury_elo_eval"),
        ("nba.daily.predict", "nba.sim.usage_redistribution"),
        ("nba.daily.predict", "nba.stack.frozen"),
        ("nba.daily.predict", "nba.stack.populate"),
        ("nba.daily.settle", "nba.eval.metrics"),
        ("nba.features.player_possession_features", "nba.coldstart.shrinkage"),
        ("nba.features.player_rebound_assist_features", "nba.coldstart.shrinkage"),
        ("nba.features.possession_features", "nba.coldstart.shrinkage"),
        ("nba.features.time_decay", "nba.coldstart.carryover"),
        ("nba.features.time_decay", "nba.coldstart.shrinkage"),
        ("nba.models.injury_elo", "nba.sim.usage_redistribution"),
        ("nba.props.context_residual", "nba.sim.usage_redistribution"),
        ("nba.props.forward", "nba.coldstart.sb_classification"),
        ("nba.props.forward", "nba.sim.player_attribution"),
        ("nba.props.metrics", "nba.eval.metrics"),
        ("nba.props.minutes", "nba.coldstart.shrinkage"),
        ("nba.props.minutes", "nba.eval.metrics"),
        ("nba.props.opponent", "nba.coldstart.archetypes"),
    }
)


def module_path(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return p.with_suffix(".py") if p.with_suffix(".py").exists() else None


def imports_of(mod: str) -> set[str]:
    """Every ``nba.*`` module ``mod`` imports, at any depth, including inside functions."""
    path = module_path(mod)
    if path is None:
        return set()
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("nba."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nba"):
            base = node.module
            if node.level:
                pkg = mod.rsplit(".", node.level)[0]
                base = f"{pkg}.{node.module}"
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names if module_path(f"{base}.{a.name}"))
    return out


def live_closure() -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    queue = deque(ENTRYPOINTS)
    while queue:
        mod = queue.popleft()
        if mod in seen or module_path(mod) is None:
            continue
        seen[mod] = imports_of(mod)
        queue.extend(seen[mod])
    return seen


def research_edges() -> set[tuple[str, str]]:
    return {
        (mod, target)
        for mod, targets in live_closure().items()
        if not mod.startswith(RESEARCH_PREFIXES)
        for target in targets
        if target.startswith(RESEARCH_PREFIXES)
    }


def test_entrypoints_exist() -> None:
    missing = [m for m in ENTRYPOINTS if module_path(m) is None]
    assert not missing, f"scheduler entrypoints not found: {missing}"


def test_live_path_imports_only_known_research_edges() -> None:
    edges = research_edges()
    new = sorted(edges - KNOWN_EDGES)
    assert not new, f"new live -> research import edges (cut them or justify in KNOWN_EDGES): {new}"


def test_known_edges_list_is_current() -> None:
    gone = sorted(KNOWN_EDGES - research_edges())
    assert not gone, f"edges no longer present; remove them from KNOWN_EDGES: {gone}"
