"""Layering ratchet: the live path may not import research code, except where listed.

The live path is the static import closure of the scheduler entrypoints (``ops/*.sh``,
``ops/install_launchd.sh``). Lazy imports inside functions count: they are edges the live
path can take at 7 pm. ``KNOWN_EDGES`` is the entanglement list of docs/DESIGN_RESTRUCTURE.md
s3.3 as measured on 2026-10-09; each cut removes its rows. The test fails both ways: a new
edge is a regression, a vanished edge means this list is stale.

Since Wave A (2026-10-10) research lives in the top-level ``research/`` package, which may import
``nba.*`` freely; nothing under ``nba/`` may import ``research.*``, live path or not
(``test_nba_never_imports_research``). The ``nba.*`` prefixes below name the packages that held
research; after the Wave A follow-ups (the rung ladder, ``pts_tail`` and the OOF store moved,
research/INDEX.md) they are empty or hold only live backtests and shims. They stay listed so that an
import of anything left under them from the live path still counts as an edge.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ENTRYPOINTS = (
    "nba.daily.__main__",
    "nba.jarvis.__main__",
    "nba.datamanifest.__main__",
    "nba.ingest.__main__",
    "nba.ingest.queue",
    "nba.ingest.referees",
    "nba.lineups.__main__",
    "nba.live.__main__",
    "nba.markets.__main__",
    "nba.odds.__main__",
    "nba.parlay.__main__",
    "nba.pulls.__main__",
    "nba.shadow_llm.__main__",
    "nba.ops.watchdog",
    "nba.ops.app_export",
    "nba.ops.stints_nightly",
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


# --- the app wall (docs/APP_ARCHITECTURE.md s2): nba never imports app; app reaches nba only
# --- through nba.export.*; the ops/*.sh live path does not import nba.export (2026-10-14).

#: (app file relative to the repo, nba.export module) edges. Both ways, like KNOWN_EDGES: a new
#: edge is a regression to justify here, a vanished edge means this list is stale.
KNOWN_APP_EDGES: frozenset[tuple[str, str]] = frozenset()

APP_SKIP_DIRS = {".venv", "node_modules", "__pycache__", "dist", "build"}


def raw_imports(path: Path) -> set[str]:
    """Absolute module names a file imports, any depth, no first-party filter."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module != "nba":  # ``from nba import export`` is judged by its names
                out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _is(name: str, pkg: str) -> bool:
    return name == pkg or name.startswith(pkg + ".")


def app_files() -> list[Path]:
    return sorted(
        p
        for p in (ROOT / "app").rglob("*.py")
        if not APP_SKIP_DIRS & set(p.relative_to(ROOT / "app").parts)
    )


def app_nba_imports() -> set[tuple[str, str]]:
    return {
        (str(p.relative_to(ROOT)), target)
        for p in app_files()
        for target in raw_imports(p)
        if _is(target, "nba")
    }


def test_nba_never_imports_app() -> None:
    bad = sorted(
        (mod, target)
        for mod, path in nba_modules().items()
        for target in raw_imports(path)
        if _is(target, "app")
    )
    assert not bad, f"nba/ imports app/: {bad}"


def test_app_imports_nba_only_via_export() -> None:
    bad = sorted(e for e in app_nba_imports() if not _is(e[1], "nba.export"))
    assert not bad, f"app/ imports nba outside nba.export.*: {bad}"
    research = sorted(
        (str(p.relative_to(ROOT)), t)
        for p in app_files()
        for t in raw_imports(p)
        if _is(t, "research")
    )
    assert not research, f"app/ imports research/: {research}"


def test_app_export_edges_are_current() -> None:
    edges = {e for e in app_nba_imports() if _is(e[1], "nba.export")}
    assert not edges - KNOWN_APP_EDGES, (
        f"new app -> nba.export edges: {sorted(edges - KNOWN_APP_EDGES)}"
    )
    assert not KNOWN_APP_EDGES - edges, f"stale KNOWN_APP_EDGES: {sorted(KNOWN_APP_EDGES - edges)}"


def test_live_path_does_not_import_export() -> None:
    """The nightly export job is a post-10-20 decision. Until it is added on purpose, no scheduler
    script names it and no module the scheduler runs imports it."""
    reached = sorted(m for m in live_closure() if _is(m, "nba.export"))
    assert not reached, f"ops/*.sh closure reaches nba.export: {reached}"
    wired = sorted(p.name for p in (ROOT / "ops").glob("*.sh") if "nba.export" in p.read_text())
    assert not wired, f"ops scripts run nba.export: {wired}"


def test_export_is_pure_and_read_only() -> None:
    """nba/export: no app/research, no network client, and nba.duckdb is only opened read-only."""
    banned = ("app", "research", "requests", "httpx", "aiohttp", "urllib", "socket", "nba_api")
    mods = {m: p for m, p in nba_modules().items() if _is(m, "nba.export")}
    assert mods, "nba/export is missing"
    bad = sorted(
        (m, t) for m, p in mods.items() for t in raw_imports(p) if any(_is(t, b) for b in banned)
    )
    assert not bad, f"nba/export imports a banned package: {bad}"
    for m, p in mods.items():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name == "connect_with_retry":
                    kw = {k.arg: k.value for k in node.keywords}
                    ro = kw.get("read_only")
                    assert isinstance(ro, ast.Constant) and ro.value is True, (
                        f"{m}: nba.duckdb must be opened read_only=True"
                    )


def test_research_package_is_importable() -> None:
    assert module_path("research") is not None
    assert (ROOT / "research" / "INDEX.md").exists(), "research/INDEX.md maps every archived module"


# --- the betting tools module (behind the betting switch, 2026-11-17) lives wholly inside app/
# --- (app/web/src/betting/, app/api/src/parlayhitters_api/betting/). nba/ and research/ never
# --- import it (or any of app/); it never imports nba or research; its web code stays in app/web.

#: The module's two directories. Listed explicitly so a move or rename fails here, not silently.
BETTING_DIRS = (
    ROOT / "app" / "web" / "src" / "betting",
    ROOT / "app" / "api" / "src" / "parlayhitters_api" / "betting",
)
#: Names by which nba/ or research/ could reach app code (the API's package, the module itself).
APP_PACKAGES = ("app", "parlayhitters_api", "parlayhitters_api.betting")


def research_modules() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in (ROOT / "research").rglob("*.py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


def test_betting_dirs_exist() -> None:
    missing = [str(d.relative_to(ROOT)) for d in BETTING_DIRS if not d.is_dir()]
    assert not missing, f"betting module directories not found: {missing}"


def test_nba_and_research_never_import_app_or_betting() -> None:
    bad = sorted(
        (mod, target)
        for mod, path in {**nba_modules(), **research_modules()}.items()
        for target in raw_imports(path)
        if any(_is(target, pkg) for pkg in APP_PACKAGES)
    )
    assert not bad, f"nba/ or research/ imports app code: {bad}"


def test_betting_api_imports_neither_nba_nor_research() -> None:
    files = sorted(BETTING_DIRS[1].rglob("*.py"))
    assert files, "no Python files under the betting API package"
    banned = ("nba", "research", "requests", "httpx", "aiohttp", "urllib", "socket", "http.client")
    bad = sorted(
        (str(p.relative_to(ROOT)), t)
        for p in files
        for t in raw_imports(p)
        if any(_is(t, b) for b in banned)
    )
    assert not bad, f"the betting API package imports a banned package: {bad}"


_TS_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|^\s*import\s+|\bexport\s+\*\s+from\s*)["']([^"']+)["']""",
    re.MULTILINE,
)


def test_betting_web_imports_stay_inside_app_web() -> None:
    """Every import specifier in app/web/src/betting/ is relative and resolves inside app/web/, or
    is the one runtime package (preact). A test file may also import the test runner and Node's
    built-ins, which never ship."""
    web = (ROOT / "app" / "web").resolve()
    files = sorted(
        p for p in BETTING_DIRS[0].rglob("*") if p.suffix in (".ts", ".tsx", ".js", ".mjs")
    )
    assert files, "no TypeScript files under app/web/src/betting/"
    bad: list[tuple[str, str]] = []
    for p in files:
        for spec in _TS_IMPORT.findall(p.read_text()):
            if spec.startswith("."):
                target = (p.parent / spec).resolve()
                if not target.is_relative_to(web):
                    bad.append((str(p.relative_to(ROOT)), spec))
            elif spec == "preact" or spec.startswith("preact/"):
                continue
            elif ".test." in p.name and (spec == "vitest" or spec.startswith("node:")):
                continue
            else:
                bad.append((str(p.relative_to(ROOT)), spec))
    assert not bad, f"betting web code imports outside app/web/: {bad}"


def test_ts_import_pattern_catches_escapes() -> None:
    """The check above is not vacuous: its pattern sees each import form."""
    text = (
        'import { a } from "../../../nba/x";\nimport "../../../../research/y";\n'
        'const m = await import("/abs/path");\nexport * from "lodash";\n'
    )
    assert _TS_IMPORT.findall(text) == [
        "../../../nba/x",
        "../../../../research/y",
        "/abs/path",
        "lodash",
    ]
