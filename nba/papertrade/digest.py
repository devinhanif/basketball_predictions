"""Rule-integrity digest: what defines the rule, hashed, and checked before any scored entry.

Covered: the prereg text, configs/papertrade.yaml, the ``fee_model`` block of configs/parlay.yaml,
every nba/papertrade/*.py, nba/live/kalshi_pull.py, nba/live/http.py and the reviewed alias table.
``python -m nba.papertrade digest --write`` writes the freeze record (main session, at the freeze
commit); ``scan`` recomputes and refuses to open a scored position on any mismatch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from nba.papertrade import config as C


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fee_block_bytes(parlay_path: Path) -> bytes:
    block = yaml.safe_load(parlay_path.read_text())["fee_model"]
    return json.dumps(block, sort_keys=True, default=str).encode()


def compute(root: Path = C.ROOT) -> dict[str, str]:
    files: dict[str, Path] = {
        "prereg": root / "docs" / "prereg" / "PAPER_TRADER.md",
        "config": root / "configs" / "papertrade.yaml",
        "kalshi_pull": root / "nba" / "live" / "kalshi_pull.py",
        "http": root / "nba" / "live" / "http.py",
        "aliases": root / "configs" / "kalshi_aliases.yaml",
    }
    out = {k: _sha(p.read_bytes()) if p.exists() else "missing" for k, p in files.items()}
    out["fee_model"] = _sha(fee_block_bytes(root / "configs" / "parlay.yaml"))
    code = sorted((root / "nba" / "papertrade").glob("*.py"))
    out["papertrade_code"] = _sha(b"".join(p.name.encode() + p.read_bytes() for p in code))
    out["combined"] = _sha(json.dumps(out, sort_keys=True).encode())
    return out


def alias_sha(root: Path = C.ROOT) -> str:
    return compute(root)["aliases"]


def check(freeze_path: Path = C.FREEZE_PATH, root: Path = C.ROOT) -> tuple[bool, str]:
    """(ok, reason). Fails when the freeze record is absent or any covered file changed."""
    if not freeze_path.exists():
        return False, "no freeze record (configs/papertrade_freeze.json)"
    frozen: dict[str, Any] = json.loads(freeze_path.read_text())
    now = compute(root)
    bad = [k for k, v in now.items() if k != "combined" and frozen.get("digests", {}).get(k) != v]
    return (not bad), ("ok" if not bad else "changed since freeze: " + ", ".join(bad))


def write_freeze(
    rule_version: str, freeze_date: str, path: Path = C.FREEZE_PATH, root: Path = C.ROOT
) -> dict[str, Any]:
    rec = {"rule_version": rule_version, "freeze_date": freeze_date, "digests": compute(root)}
    path.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    return rec
