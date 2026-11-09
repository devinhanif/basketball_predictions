"""Per-leg edge arithmetic from the engine's stored single-contract rows.

``parlay_of_day.json`` (passed through the export untouched) holds one row per priced
contract: the model's chance with its interval, the ask, the fee, and the engine's own
edge after fees. This module only selects and cleans those rows for one player and stat.
Every number is a stored field, copied as is; nothing is computed here.
"""

from __future__ import annotations

import math
from typing import Any

Json = dict[str, Any]

_SIDES = {"yes": "over", "no": "under"}


def _num(x: object) -> float | None:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    f = float(x)
    return f if math.isfinite(f) else None


def _row(rec: Json, leg: Json) -> Json | None:
    side = _SIDES.get(str(rec.get("side", leg.get("side"))))
    threshold = _num(leg.get("threshold"))
    price, fees, ev = _num(rec.get("price")), _num(rec.get("fees")), _num(rec.get("ev"))
    prob = _num(rec.get("model_prob"))
    if side is None or threshold is None or price is None or fees is None:
        return None
    if ev is None or prob is None:
        return None
    interval = rec.get("prob_interval")
    lo = hi = None
    if isinstance(interval, list) and len(interval) == 2:
        lo, hi = _num(interval[0]), _num(interval[1])
    return {
        "side": side,
        "threshold": threshold,
        "title": str(rec.get("title", ""))[:120],
        "ticker": str(rec.get("ticker", ""))[:80],
        "price": price,
        "fees": fees,
        "ev": ev,
        "ev_low": _num(rec.get("ev_low")),
        "model_prob": prob,
        "prob_lo": lo,
        "prob_hi": hi,
        "order_type": str(rec.get("order_type", ""))[:20],
        "price_ts": str(rec.get("price_ts", ""))[:32],
    }


def leg_edges(records: list[Json], game_id: str, player_id: int, stat: str) -> list[Json]:
    """Stored contract rows for one player and stat, sorted by threshold then side."""
    out: list[Json] = []
    for rec in records:
        legs = rec.get("legs")
        if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
            continue
        leg = legs[0]
        if (
            str(leg.get("game_id")) == game_id
            and leg.get("player_id") == player_id
            and leg.get("stat") == stat
        ):
            row = _row(rec, leg)
            if row is not None:
                out.append(row)
    out.sort(key=lambda r: (r["threshold"], r["side"]))
    return out


def top_contracts(
    records: list[Json],
    names: dict[int, str],
    game_of: Any,
    n: int = 12,
) -> Json:
    """The engine's best-priced stored contracts (highest edge after fees first), with names.

    ``game_of(game_id)`` returns the export's game dict or None. Contracts of games the export does
    not hold are skipped: the page could not say who plays whom. Every number is a stored field.
    """
    rows: list[Json] = []
    for rec in records:
        legs = rec.get("legs")
        if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
            continue
        leg = legs[0]
        row = _row(rec, leg)
        pid = leg.get("player_id")
        if row is None or not isinstance(pid, int):
            continue
        g = game_of(str(leg.get("game_id")))
        if g is None:
            continue
        pl = next((p for p in g.get("players", []) if p.get("player_id") == pid), None)
        team = None
        if pl is not None:
            side_team = g["home"] if pl.get("side") == "home" else g["away"]
            team = side_team.get("abbr")
        rows.append(
            row
            | {
                "game_id": str(leg.get("game_id")),
                "player_id": pid,
                "player": names.get(pid, f"Unnamed player (id {pid})"),
                "team": team,
                "stat": leg.get("stat"),
                "matchup": f"{g['away'].get('abbr')} @ {g['home'].get('abbr')}",
                "tip_utc": g.get("tip_utc"),
            }
        )
    rows.sort(key=lambda r: (-r["ev"], -r["model_prob"], r["ticker"]))
    return {
        "n_priced": len(rows),
        "n_positive": sum(1 for r in rows if r["ev"] > 0),
        "contracts": rows[:n],
    }
