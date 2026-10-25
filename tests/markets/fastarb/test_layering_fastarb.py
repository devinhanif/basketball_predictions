"""Layering and safety ratchet for nba.markets.fastarb (the standing market-agent rules, as tests).

* the static import closure of the package never reaches research code or the app;
* the package holds no order / portfolio / execution endpoint and no credential handling;
* the only HTTP verb is GET; no module opens ``nba.duckdb`` for writing (it opens none);
* the Underdog module cannot reach the network (their terms forbid automated access).
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

from tests.test_layering import RESEARCH_PREFIXES, imports_of, module_path

PKG = Path(__file__).resolve().parents[3] / "nba" / "markets" / "fastarb"
ENTRY = "nba.markets.fastarb.__main__"


def closure() -> set[str]:
    seen: set[str] = set()
    queue = deque([ENTRY])
    while queue:
        mod = queue.popleft()
        if mod in seen or module_path(mod) is None:
            continue
        seen.add(mod)
        queue.extend(imports_of(mod))
    return seen


def test_closure_reaches_no_research_or_app() -> None:
    bad = sorted(m for m in closure() if m.startswith((*RESEARCH_PREFIXES, "app", "nba.export")))
    assert not bad, f"fastarb imports research/app code: {bad}"


def test_package_files_exist() -> None:
    assert (PKG / "runner.py").exists() and len(list(PKG.glob("*.py"))) >= 8


def _sources() -> dict[str, str]:
    return {p.name: p.read_text() for p in PKG.glob("*.py")}


def test_no_order_portfolio_or_credential_code() -> None:
    banned = (
        "place_order",
        "create_order",
        "cancel_order",
        "submit_order",
        "/portfolio",
        "/orders",
        "/v1/orders",
        "/v1/combos",
        "/v1/rfqs",
        "authorization",
        "bearer",
        "api_key",
        "apikey",
        "secret_key",
        "private_key",
        ".post(",
        ".put(",
        ".delete(",
        ".patch(",
    )
    for name, src in _sources().items():
        # the one allowed credential is the EXISTING odds client's env var name, read by that client
        low = src.lower().replace("the_odds_api_key", "")
        for token in banned:
            assert token not in low, f"{name} contains {token!r}"


def test_only_get_requests_and_no_headers_beyond_user_agent() -> None:
    for name, src in _sources().items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "post",
                    "put",
                    "delete",
                    "patch",
                    "request",
                    "send",
                }, name
    assert (
        "User-Agent" in (PKG / "__main__.py").read_text()
        or "USER_AGENT" in (PKG / "__main__.py").read_text()
    )


def test_no_duckdb_write_connection_to_nba_db() -> None:
    for name, src in _sources().items():
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
            ):
                kw = {k.arg: k.value for k in node.keywords}
                ro = kw.get("read_only")
                assert isinstance(ro, ast.Constant) and ro.value is True, (
                    f"{name}: connect must be read_only=True"
                )
    assert "nba.duckdb" not in "".join(_sources().values())


def test_underdog_module_has_no_network_imports() -> None:
    tree = ast.parse((PKG / "underdog.py").read_text())
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    assert not {
        m
        for m in mods
        if m and m.split(".")[0] in {"httpx", "requests", "urllib", "socket", "aiohttp"}
    }
