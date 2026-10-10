"""Layering ratchet: the live path may not import research code, except where listed.

The live path is the static import closure of the scheduler entrypoints (``ops/*.sh``,
``ops/install_launchd.sh``). Lazy imports inside functions count: they are edges the live
path can take at 7 pm. ``KNOWN_EDGES`` is the entanglement list of docs/DESIGN_RESTRUCTURE.md
s3.3 as measured on 2026-10-09; each cut removes its rows. The test fails both ways: a new
edge is a regression, a vanished edge means this list is stale.

Since Wave A (2026-10-10) research lives in the top-level ``research/`` package, which may import
``nba.*`` freely; nothing under ``nba/`` may import ``research.*``, live path or not
(``test_nba_never_imports_research``). The ``nba.*`` prefixes below are what is still waiting to
leave ``nba/`` (anchored by ``nba.registry.ops -> model_gate -> eval.run``; see research/INDEX.md).
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
    "nba.odds.__main__",
    "nba.parlay.__main__",
    "nba.ops.watchdog",
)

#: Packages that hold research: experiments that reached a verdict, archived under the ledger.
RESEARCH_PREFIXES = (
    "research",
    "nba.sim",
    "nba.stack",
    "nba.coldstart",
    "nba.eval",
    "nba.colab",
    "nba.preprocess",
)

#: Top-level packages whose imports the graph follows.
FIRST_PARTY = ("nba", "research")

#: (live module, research module) edges still present. Reached zero on 2026-10-09 after eight
#: cuts (docs/reviews/replay_oracle_2026-10-09.md); it never grows again.
KNOWN_EDGES: frozenset[tuple[str, str]] = frozenset()


def module_path(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return p.with_suffix(".py") if p.with_suffix(".py").exists() else None


def _first_party(name: str) -> bool:
    return any(name == fp or name.startswith(fp + ".") for fp in FIRST_PARTY)


def imports_of(mod: str, path: Path | None = None) -> set[str]:
    """Every first-party module ``mod`` imports, at any depth, including inside functions."""
    path = path or module_path(mod)
    if path is None:
        return set()
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if _first_party(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if node.level:
                pkg = mod.rsplit(".", node.level)[0]
                base = f"{pkg}.{node.module}"
            if not _first_party(base):
                continue
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


def nba_modules() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in (ROOT / "nba").rglob("*.py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


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


def test_nba_never_imports_research() -> None:
    """nba/ never imports research.*, not even lazily; research imports nba, never back."""
    bad = sorted(
        (mod, target)
        for mod, path in nba_modules().items()
        for target in imports_of(mod, path)
        if target == "research" or target.startswith("research.")
    )
    assert not bad, f"nba/ imports research/: {bad}"


def test_research_package_is_importable() -> None:
    assert module_path("research") is not None
    assert (ROOT / "research" / "INDEX.md").exists(), "research/INDEX.md maps every archived module"
