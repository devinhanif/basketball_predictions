"""Reader for the Jarvis Desk export (docs/contracts/desk_export_v1.md).

The file is written by the market agent's ``nba/export/desk.py``; this module only reads it.
The API serves it at ``/api/desk`` only when started with ``PH_PRIVATE=1``. A shared cut must hold
no money field: if it does, the file is refused rather than shown.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

Json = dict[str, Any]
MAX_BYTES = 25 * 1024 * 1024
LIST_KEYS = ("plays", "observations", "alerts", "halftime", "daily")


class DeskMissing(FileNotFoundError):
    """No desk file exists yet."""


class DeskRefused(ValueError):
    """The file is there but is not something we can vouch for."""


def desk_path() -> Path:
    env = os.environ.get("APP_DESK_FILE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "data" / "export_private" / "desk.json"


_DOLLAR = re.compile(r"\$\s?\d")
_PER_ONE = re.compile(r"per \$1\b")
_PATH = re.compile(r"(^|[\s(\"'=])(/(Users|home|private|var|tmp|etc)/|[A-Za-z]:\\)")


def _strings(x: Any) -> Iterator[str]:
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def money_fields(d: Json) -> list[str]:
    """What a shared cut must not carry: a money field, a dollar amount or an absolute path.

    Checked here as well as by the producer, so a field that returns is refused at the door."""
    found: list[str] = []
    if d.get("balance") is not None:
        found.append("balance")
    for p in d.get("plays", []):
        for k in ("stake_usd", "contracts", "fee_cents"):
            if p.get(k) is not None:
                found.append(f"plays.{k}")
        r = p.get("result")
        if isinstance(r, dict) and r.get("pnl_usd") is not None:
            found.append("plays.result.pnl_usd")
    for day in d.get("daily", []):
        if day.get("pnl_usd") is not None:
            found.append("daily.pnl_usd")
    for s in _strings(d):
        if _DOLLAR.search(_PER_ONE.sub("", s)):
            found.append("a string with a dollar amount")
        if _PATH.search(s):
            found.append("a string with an absolute path")
    return sorted(set(found))


def check_desk(d: object) -> Json:
    if not isinstance(d, dict):
        raise DeskRefused("the file is not a JSON object")
    if d.get("schema") != 1:
        raise DeskRefused(f"schema {d.get('schema')!r} is not 1")
    if d.get("privacy") not in ("private", "shared"):
        raise DeskRefused("privacy must be 'private' or 'shared'")
    if not isinstance(d.get("built_at"), str) or not isinstance(d.get("data_as_of"), str):
        raise DeskRefused("built_at and data_as_of are required")
    if not isinstance(d.get("status"), dict):
        raise DeskRefused("status is required")
    for k in LIST_KEYS:
        if not isinstance(d.get(k, []), list):
            raise DeskRefused(f"{k} must be a list")
    if d["privacy"] == "shared":
        leaked = money_fields(d)
        if leaked:
            raise DeskRefused(f"a shared cut must not hold money fields: {', '.join(leaked)}")
    return d


def load_desk(path: Path | None = None) -> Json:
    p = path or desk_path()
    if not p.is_file():
        raise DeskMissing(str(p))
    if p.stat().st_size > MAX_BYTES:
        raise DeskRefused("the file is too large")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DeskRefused(f"unreadable: {e}") from None
    return check_desk(data)
