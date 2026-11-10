"""The public door for the desk export: the shared cut, read by allowlist.

The keys come from docs/contracts/desk_export_v1.md plus the extra fields the producer writes
(checked against the real file). Anything unknown, or any non-null value under a money-sounding
key, refuses the file. A new producer field therefore fails closed until it is added here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .desk import DeskRefused, Json, load_desk, money_fields, shared_desk_path

_NULL = "null"  # present, but must be null in a shared cut
_LEAF = "leaf"  # a scalar, or a list of scalars
_MONEY_KEY = re.compile(r"usd|stake|contract|bankroll|balance|pnl|units|kelly", re.IGNORECASE)


def _leaves(*names: str) -> dict[str, Any]:
    return dict.fromkeys(names, _LEAF)


_PLAY: dict[str, Any] = {
    **_leaves(
        *(
            "play_id bundle_id slate_date ts game_id matchup tipoff source venue kind side "
            "player_id "
            "player_name team stat threshold leg_desc price_cents fair_prob model_prob edge_cents "
            "edge_label reason explanation delivered"
        ).split()
    ),
    "fee_cents": _NULL,
    "contracts": _NULL,
    "stake_usd": _NULL,
    "result": {**_leaves("status", "won", "close_cents", "clv_cents"), "pnl_usd": _NULL},
}

_SPEC: dict[str, Any] = {
    **_leaves("schema", "privacy", "built_at", "data_as_of", "_fixture", "book_hold_seconds"),
    "balance": _NULL,
    "sources": ("map", _LEAF),
    "section_notes": ("map", _LEAF),
    "status": {
        "kill_switch": _leaves("on", "since", "n", "mean_clv_cents", "note"),
        "model_review": _LEAF,
        "w_model": _LEAF,
        "w_model_by_stat": ("map", _LEAF),
    },
    "plays": ("list", _PLAY),
    "clv_summary": {
        **_leaves("n", "mean_cents", "ci95", "clustered_on", "reason", "n_dates"),
        "bootstrap": _leaves("method", "reps", "seed"),
    },
    "record": _leaves("n_settled", "n_won", "n_lost", "n_void"),
    "daily": ("list", {**_leaves("slate_date", "n", "n_won", "clv_mean_cents"), "pnl_usd": _NULL}),
    "observations": (
        "list",
        _leaves(
            "id",
            "slate_date",
            "ts",
            "game_id",
            "matchup",
            "when_text",
            "kind",
            "title",
            "text",
            "certainty",
            "corrects",
        ),  # fmt: skip
    ),
    "alerts": (
        "list",
        _leaves(
            "id", "ts", "slate_date", "kind", "mode", "title", "text", "play_ids", "observation_ids"
        ),
    ),
    "halftime": (
        "list",
        _leaves(
            "slate_date",
            "game_id",
            "matchup",
            "player_id",
            "player_name",
            "stat",
            "threshold",
            "price_cents",
            "model_prob",
            "edge_cents",
            "result",
        ),  # fmt: skip
    ),
    "track_record": {
        "scorecard_date": _LEAF,
        "model_vs_market": _leaves(
            "n", "metric", "diff", "ci95", "clustered_on", "reason", "games", "note"
        ),
        "calibration": ("list", _leaves("bin_lo", "bin_hi", "n", "mean_pred", "hit_rate")),
    },
}


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, str | int | float | bool)


def _check(v: Any, spec: Any, where: str, bad: list[str]) -> None:
    if spec == _NULL:
        if v is not None:
            bad.append(f"{where} must be null")
    elif spec == _LEAF:
        if not (_is_scalar(v) or (isinstance(v, list) and all(_is_scalar(x) for x in v))):
            bad.append(f"{where} has an unexpected shape")
    elif isinstance(spec, tuple):
        kind, inner = spec
        if kind == "list":
            if not isinstance(v, list):
                bad.append(f"{where} must be a list")
                return
            for i, row in enumerate(v):
                _check(row, inner, f"{where}[{i}]", bad)
        else:
            if not isinstance(v, dict):
                bad.append(f"{where} must be an object")
                return
            for k, x in v.items():
                _check(x, inner, f"{where}.{k}", bad)
    elif v is not None:
        if not isinstance(v, dict):
            bad.append(f"{where} must be an object")
            return
        for k, x in v.items():
            if k not in spec:
                bad.append(f"{where}.{k} is not a known field")
            else:
                _check(x, spec[k], f"{where}.{k}", bad)


def _money_named(x: Any, where: str, bad: list[str]) -> None:
    if isinstance(x, dict):
        for k, v in x.items():
            if v is not None and _MONEY_KEY.search(str(k)):
                bad.append(f"{where}.{k} looks like a money field")
            _money_named(v, f"{where}.{k}", bad)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _money_named(v, f"{where}[{i}]", bad)


def check_shared(d: Json) -> Json:
    """Validate a parsed desk against the public allowlist; return it without ``sources``.

    ``sources`` (the producer's internal file names) is not served: the client reads it only
    on the private Admin screen."""
    if d.get("privacy") != "shared":
        raise DeskRefused("the public desk file must be the shared cut")
    out = {**d, "sources": {}}
    bad: list[str] = []
    _check(out, _SPEC, "desk", bad)
    _money_named(out, "desk", bad)
    leaked = money_fields(d)
    if bad or leaked:
        raise DeskRefused(
            "a shared cut must hold only known fields and no money: " + "; ".join(bad + leaked)
        )
    return out


def load_shared_desk(path: Path | None = None) -> Json:
    return check_shared(load_desk(path or shared_desk_path()))
