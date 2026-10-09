"""Tool specs (Ollama /api/chat format) and a strict argument validator."""

from __future__ import annotations

from typing import Any

STATS = ["pts", "reb", "ast", "fg3m", "win", "spread", "total"]
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD (slate day, US/Eastern)."}

LEG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stat": {"type": "string", "enum": STATS},
        "threshold": {
            "type": "number",
            "description": "N for 'stat >= N' (props); points margin for spread; total line.",
        },
        "side": {"type": "string", "enum": ["yes", "no"]},
        "player": {"type": "string", "description": "Player name or id (props only)."},
        "team": {"type": "string", "description": "Team abbreviation (win/spread legs)."},
        "game": {"type": "string", "description": "Optional 'AWY@HOM' or game_id to disambiguate."},
    },
    "required": ["stat"],
    "additionalProperties": False,
}
_LEGS = {"type": "array", "items": LEG_SCHEMA, "minItems": 1, "maxItems": 6}


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "list_slate": _fn(
        "list_slate",
        "Games and players that have forward predictions for a date.",
        {"date": _DATE},
        [],
    ),
    "evaluate_slate": _fn(
        "evaluate_slate",
        "Price every mapped open Kalshi single-leg contract (both sides) for a date; "
        "returns the top contracts with verdicts, and summary counts.",
        {
            "date": _DATE,
            "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
            "stat": {"type": "string", "enum": STATS},
        },
        [],
    ),
    "price_parlay": _fn(
        "price_parlay",
        "Joint probability, probability interval, fees, EV, EV at the low bound and verdict "
        "for one or more legs (same-game legs use the copula; different games are independent).",
        {
            "legs": _LEGS,
            "date": _DATE,
            "ask": {
                "type": "number",
                "description": "Optional user-supplied ask in (0,1) when no Kalshi market exists.",
            },
        },
        ["legs"],
    ),
    "explain_leg": _fn(
        "explain_leg",
        "Stored forward prediction for one player stat (mean, quantiles, P(>=N), p_play, routing) "
        "plus the Kalshi price if a matching market is mapped.",
        {
            "player": {"type": "string"},
            "stat": {"type": "string", "enum": ["pts", "reb", "ast", "fg3m"]},
            "threshold": {"type": "number"},
            "date": _DATE,
        },
        ["player", "stat", "threshold"],
    ),
    "what_if": _fn(
        "what_if",
        "Change in joint probability and EV when adding and/or removing legs, including the "
        "correlation effect versus independence.",
        {
            "legs": _LEGS,
            "add": LEG_SCHEMA,
            "remove": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "description": "0-based indexes into legs to drop.",
            },
            "date": _DATE,
            "ask": {"type": "number"},
        },
        ["legs"],
    ),
    "best_for_budget": _fn(
        "best_for_budget",
        "Best use of a dollar budget: singles and engine-priced parlays in whole contracts with "
        "per-order fees, ranked by EV at the conservative low bound. Answers "
        "'keep your money' when no bet is positive at that bound.",
        {"budget_usd": {"type": "number", "minimum": 1, "maximum": 100000}, "date": _DATE},
        ["budget_usd"],
    ),
    "best_for_target": _fn(
        "best_for_target",
        "Options (singles and parlays of at most 4 legs) that can win at least a target profit "
        "within a budget after fees, ranked by EV at the low bound, plus the risk/EV frontier "
        "across target levels.",
        {
            "budget_usd": {"type": "number", "minimum": 1, "maximum": 100000},
            "target_profit_usd": {"type": "number", "minimum": 1, "maximum": 1000000},
            "date": _DATE,
        },
        ["budget_usd", "target_profit_usd"],
    ),
    "track_record": _fn(
        "track_record",
        "Shadow-log realized vs expected for a stat (or 'all'); says 'insufficient sample' "
        "below the configured minimum number of settled rows.",
        {"stat": {"type": "string", "enum": [*STATS, "all"]}},
        [],
    ),
}

PAPER_LOG_SPEC = _fn(
    "log_paper_trade",
    "Log ONE priced parlay to the paper-trade table (no money involved). Only call when the "
    "user explicitly asks to log a paper trade; requires confirm=true.",
    {"legs": _LEGS, "date": _DATE, "confirm": {"type": "boolean"}},
    ["legs", "confirm"],
)


class ArgError(ValueError):
    pass


def validate(schema: dict[str, Any], value: Any, path: str = "args") -> None:
    """Strict JSON-schema subset check: types, enum, required, extra keys, bounds, array size."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            raise ArgError(f"{path} must be an object")
        props: dict[str, Any] = schema.get("properties", {})
        for k in value:
            if k not in props:
                raise ArgError(f"{path}: unknown argument {k!r} (allowed: {sorted(props)})")
        for k in schema.get("required", []):
            if k not in value:
                raise ArgError(f"{path}: missing required argument {k!r}")
        for k, v in value.items():
            validate(props[k], v, f"{path}.{k}")
        return
    if t == "array":
        if not isinstance(value, list):
            raise ArgError(f"{path} must be an array")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            raise ArgError(
                f"{path} must have {schema.get('minItems', 0)}-{schema.get('maxItems')} items"
            )
        for i, v in enumerate(value):
            validate(schema["items"], v, f"{path}[{i}]")
        return
    if t == "string":
        if not isinstance(value, str) or not value.strip():
            raise ArgError(f"{path} must be a non-empty string")
    elif t in ("number", "integer"):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        if not ok or value != value or abs(value) == float("inf"):
            raise ArgError(f"{path} must be a finite number")
        if t == "integer" and float(value) != int(value):
            raise ArgError(f"{path} must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ArgError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ArgError(f"{path} must be <= {schema['maximum']}")
    elif t == "boolean" and not isinstance(value, bool):
        raise ArgError(f"{path} must be a boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ArgError(f"{path} must be one of {schema['enum']}, got {value!r}")
