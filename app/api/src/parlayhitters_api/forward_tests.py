"""The frozen forward tests: a static, versioned list (published/forward_tests_v1.json, each entry
citing file:line), with ``n_so_far`` counted from settled_scores where the test's arms are stored
there. Counts only: never a score, a difference or a verdict. Contract:
docs/contracts/forward_tests_v1.md.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from .honesty_data import Scores
from .honesty_models import ForwardTest, ForwardTests

Json = dict[str, Any]
PUBLISHED = Path(__file__).parent / "published"
FORWARD_TESTS_FILE = PUBLISHED / "forward_tests_v1.json"


@cache
def _published_tests() -> Json:
    data: Json = json.loads(FORWARD_TESTS_FILE.read_text(encoding="utf-8"))
    return data


def forward_tests(scores: Scores | None) -> ForwardTests:
    data = _published_tests()
    tests: list[ForwardTest] = []
    for t in data["tests"]:
        arms: list[str] = list(t.get("arms") or [])
        counts: dict[str, dict[str, int]] | None = None
        reason: str | None
        if not arms:
            reason = (
                "this test does not score rows of settled_scores; the export holds no count for it"
            )
        elif scores is None or not scores.available:
            reason = "this data source has no settled_scores" + (
                "" if scores is None or scores.reason is None else f" ({scores.reason})"
            )
        else:
            counts = {}
            for arm in arms:
                rows = [r for (m, _t), rr in scores.single.items() if m == arm for r in rr]
                counts[arm] = {
                    "scored": sum(int(r[1]) + int(r[3]) for r in rows),
                    "dnp": sum(int(r[2]) for r in rows),
                    "dates": len({str(r[0]) for r in rows if int(r[1]) + int(r[3]) > 0}),
                }
            reason = None
            if all(c["scored"] == 0 and c["dnp"] == 0 for c in counts.values()):
                reason = "no settled row for this arm in the export"
        tests.append(ForwardTest(
            id=t["id"], title=t["title"], source=t["source"], as_of=t["as_of"],
            scheduled_read=t["scheduled_read"], n_needed=t["n_needed"], arms=arms,
            n_so_far=counts, n_so_far_reason=reason,
        ))  # fmt: skip
    return ForwardTests(
        version=int(data["version"]),
        source=str(data["source"]),
        as_of=str(data["as_of"]),
        note=str(data["note"]),
        count_rule=(
            "n_so_far counts rows of settled_scores (forward_scores: latest row per key, every "
            "stat and date of the export's forward seasons, regular season) for the test's arms. "
            "It is not the test's own paired, eligible count, which only its checkpoint states."
        ),
        tests=tests,
    )
