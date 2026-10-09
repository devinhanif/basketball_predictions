"""Optional plain-English slate summary from a LOCAL Ollama model, fed only the computed tables.

The model sees a compact JSON of numbers already in the report. Its prose is then passed through
``numguard.check_prose`` against those same numbers, so any figure not in the tables is replaced
and recommendation sentences are removed. No hosted/paid API; if Ollama is absent the step is
skipped with a note and the report is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from nba.parlay.assistant.backend import LLMBackend, OllamaUnavailable
from nba.parlay.assistant.numguard import check_prose, collect_numbers

SYSTEM = (
    "You write a short plain-English summary (3 to 5 sentences) of a daily NBA props analysis. "
    "You are given a JSON of already-computed numbers. Use ONLY numbers that appear in the JSON, "
    "copied exactly; never compute, estimate or round new ones. If verdict is "
    "no_positive_ev_found say so plainly, and say the model currently defers to the market. "
    "State the sample sizes. Never tell the reader to bet. End by saying this is analysis only, "
    "not financial advice."
)


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def narration_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Compact, pre-rounded view of the report dict: the ONLY thing the LLM may cite."""
    rows = sorted(report["contracts"], key=lambda r: -r["ev_low"])[:5]
    pay: dict[str, Any] = {
        "date": report["date"],
        "verdict": report["headline_verdict"],
        "contracts_priced": report["n_contracts"],
        "n_games": report["n_games"],
        "n_flagged_positive": report["n_flagged_positive"],
        "track_record_settled_rows": report["gate"]["n_settled"],
        "track_record_dates": report["gate"]["n_dates"],
        "rows_needed": report["gate"]["min_settled"],
        "dates_needed": report["gate"]["min_settled_dates"],
        "model_weight": _r(rows[0]["model_weight"], 2) if rows else 0.0,
        "least_bad_contracts": [
            {
                "what": r["text"],
                "ask": _r(r["ask"]),
                "model_prob_raw": _r(r["raw_model_prob"]),
                "ev_low": _r(r["ev_low"]),
                "raw_ev_low_hypothesis": _r(r["raw_ev_low"]),
            }
            for r in rows
        ],
    }
    if report.get("budget"):
        b = report["budget"]
        pay["budget_usd"] = b["budget_usd"]
        pay["budget_answer"] = b["answer"]
    return pay


def narrate(report: dict[str, Any], backend: LLMBackend | None) -> tuple[str | None, str]:
    """(prose, note). ``prose`` is None when no local LLM is available (note says why)."""
    if backend is None:
        return None, "narration skipped: no local LLM backend available"
    payload = narration_payload(report)
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(payload, indent=1)},
    ]
    try:
        reply = backend.chat(msgs, [])
    except OllamaUnavailable as e:
        return None, f"narration skipped: {str(e).splitlines()[0]}"
    allowed = collect_numbers(payload)
    prose, removed = check_prose(reply.content.strip(), allowed)
    note = f"numguard removed {removed} item(s) not in the tables" if removed else "numguard clean"
    return prose, note
