"""Guard: the ingest package must be importable with no network access and
without nba_api being touched at import/collection time. nba_api is only
imported lazily inside the fetch functions that make the actual calls.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

INGEST_DIR = Path(__file__).resolve().parents[2] / "nba" / "ingest"


def test_nba_api_not_imported_at_module_top_level() -> None:
    """Static check: no top-level `import nba_api` / `from nba_api import ...`."""
    for path in INGEST_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:  # only top-level statements, not inside functions
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                assert not any(n.startswith("nba_api") for n in names), (
                    f"{path} imports nba_api at module top level"
                )
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("nba_api"), (
                    f"{path} imports from nba_api at module top level"
                )


def test_importing_ingest_package_does_not_load_nba_api() -> None:
    """Importing nba.ingest and its submodules must not pull nba_api into sys.modules."""
    sys.modules.pop("nba_api", None)
    import nba.ingest  # noqa: F401
    import nba.ingest.boxscores  # noqa: F401
    import nba.ingest.cache  # noqa: F401
    import nba.ingest.games  # noqa: F401
    import nba.ingest.pbp  # noqa: F401

    assert "nba_api" not in sys.modules
