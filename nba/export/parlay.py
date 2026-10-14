"""The parlay-of-the-day artifact: the shadow engine's evaluation, passed through untouched.

Source: ``reports/parlay/evaluate_<date>.json`` written by ``python -m nba.parlay evaluate`` during
the pretip job (a JSON list; one record per priced contract, each with ``verdict``). This module
neither recomputes nor reorders nor softens anything: records are copied verbatim, and the day
verdict is the engine's own string. ``no_positive_ev_found`` is a first-class, normal answer.

``status``:
  * ``evaluated``  the file exists and parsed; ``verdict`` is ``no_positive_ev_found`` when no
                   record carries another verdict (an empty list means nothing was priced, and
                   ``n_evaluated = 0`` says so), else ``positive_ev_at_conservative_bound``.
  * ``not_run``    no file for that date. ``verdict`` is None: absence is not a verdict.
  * ``unreadable`` the file exists but is not a JSON list of objects. ``verdict`` is None.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Mirrors nba.parlay.ev.NO_POSITIVE_EV / POSITIVE_EV (pinned equal by a test; no import of the
#: engine so the export stays small and the wall stays simple).
NO_POSITIVE_EV = "no_positive_ev_found"
POSITIVE_EV = "positive_ev_at_conservative_bound"


def parlay_of_day(parlay_dir: Path, day: str) -> dict[str, Any]:
    path = parlay_dir / f"evaluate_{day}.json"
    base: dict[str, Any] = {
        "date": day, "source": path.name, "status": "not_run", "verdict": None,
        "n_evaluated": 0, "n_positive": 0, "records": [],
    }  # fmt: skip
    if not path.is_file():
        return base
    try:
        records = json.loads(path.read_text())
    except (OSError, ValueError):
        return base | {"status": "unreadable"}
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return base | {"status": "unreadable"}
    positives = [r for r in records if r.get("verdict") not in (None, NO_POSITIVE_EV)]
    return base | {
        "status": "evaluated",
        "verdict": POSITIVE_EV if positives else NO_POSITIVE_EV,
        "n_evaluated": len(records),
        "n_positive": len(positives),
        "records": records,
    }
