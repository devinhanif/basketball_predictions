"""Cash-out offer log: one JSON line per offer Devin observes, with the legs' public quotes.

``python -m nba.live offer --combo T --offer 12.50 --contracts 3`` appends a line to
``data/raw/live_<date>/cashout_offers.jsonl`` so an offer can later be compared with fair value.
Each leg (and the combo itself) is fetched once, with GET on the public market route. Legs come
from the combo market's ``mve_selected_legs`` when the API lists them, else from ``--leg``. A leg
that cannot be fetched is recorded as such, never guessed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher

FILE = "cashout_offers.jsonl"


def leg_tickers_of(market: dict[str, Any] | None) -> list[str]:
    legs = (market or {}).get("mve_selected_legs") or []
    return [str(x["market_ticker"]) for x in legs if isinstance(x, dict) and x.get("market_ticker")]


def log_offer(
    out_dir: Path,
    *,
    combo: str,
    offer: float,
    contracts: int,
    fetcher: Fetcher,
    legs: list[str] | None = None,
    note: str | None = None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    received = now_fn()
    fetcher.begin_tick(fetcher.budget_for(30.0, 40), 30.0)
    combo_m = kp.get_market(fetcher, combo)
    tickers = list(dict.fromkeys([*leg_tickers_of(combo_m), *(legs or [])]))
    leg_rows: list[dict[str, Any]] = []
    for t in tickers:
        m = kp.get_market(fetcher, t)
        side = next(
            (
                x.get("side")
                for x in (combo_m or {}).get("mve_selected_legs") or []
                if isinstance(x, dict) and x.get("market_ticker") == t
            ),
            None,
        )
        leg_rows.append(
            {
                "ticker": t,
                "side": side,
                "quote": kp.quote(m) if m else None,
                "fetched": m is not None,
            }
        )
    row: dict[str, Any] = {
        "received_at": received.isoformat(),
        "combo": combo,
        "offer": offer,
        "contracts": contracts,
        "combo_quote": kp.quote(combo_m) if combo_m else None,
        "legs": leg_rows,
        "legs_listed_by_api": bool(leg_tickers_of(combo_m)),
    }
    if note:
        row["note"] = note
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / FILE).open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row
