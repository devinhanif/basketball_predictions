"""Record small, trimmed copies of the two public feeds as test fixtures (run by hand, needs network).

    cd app/api && PYTHONPATH=src uv run --frozen python ../fixtures/record_live.py

Only the fields the parser reads are kept; the dates are rewritten to a pre-2025 night so no fixture
carries a current-season date. The tests never touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from parlayhitters_api import live

OUT = Path(__file__).resolve().parent / "live"
NIGHT = "2024-11-12"


def redate(text: str) -> str:
    import re

    return re.sub(r"20(2[5-9])-\d\d-\d\d", NIGHT, text).replace("26OCT21", "24NOV12")


def trim_comp(c: dict[str, Any]) -> dict[str, Any]:
    t = c["team"]
    return {
        "homeAway": c["homeAway"],
        "score": c.get("score"),
        "team": {"abbreviation": t["abbreviation"], "displayName": t["displayName"]},
    }


def main() -> None:
    sb = live.http_get(f"{live.ESPN_BASE}/scoreboard")
    events = []
    for ev in sb["events"][:2]:
        comp = ev["competitions"][0]
        events.append(
            {
                "id": ev["id"],
                "date": ev["date"],
                "status": ev["status"],
                "competitions": [{"competitors": [trim_comp(c) for c in comp["competitors"]]}],
            }
        )
    for ev in events:
        ev["status"] = {k: ev["status"][k] for k in ("displayClock", "period", "type")}
        ev["status"]["type"] = {
            k: ev["status"]["type"].get(k) for k in ("state", "completed", "shortDetail")
        }
    (OUT / "espn_scoreboard.json").write_text(redate(json.dumps({"events": events}, indent=1)))
    eid = events[0]["id"]
    sm = live.http_get(f"{live.ESPN_BASE}/summary?event={eid}")
    hc = sm["header"]["competitions"][0]
    header = {
        "id": sm["header"]["id"],
        "competitions": [
            {
                "date": hc["date"],
                "status": {k: hc["status"][k] for k in ("displayClock", "period")}
                | {"type": {k: hc["status"]["type"].get(k) for k in ("state", "completed", "shortDetail")}},
                "competitors": [trim_comp(c) for c in hc["competitors"]],
            }
        ],
    }
    players = []
    for blk in sm["boxscore"]["players"]:
        g = blk["statistics"][0]
        players.append(
            {
                "team": {"abbreviation": blk["team"]["abbreviation"]},
                "statistics": [
                    {
                        "labels": g["labels"],
                        "athletes": [
                            {
                                "athlete": {
                                    "id": a["athlete"]["id"],
                                    "displayName": a["athlete"]["displayName"],
                                },
                                "starter": a["starter"],
                                "didNotPlay": a["didNotPlay"],
                                "stats": a["stats"],
                            }
                            for a in g["athletes"][:4]
                        ],
                    }
                ],
            }
        )
    (OUT / "espn_summary.json").write_text(
        redate(json.dumps({"header": header, "boxscore": {"players": players}}, indent=1))
    )
    tickers = [
        "KXNBAPTS-26OCT21ATLORL-ATLJJOHNSON1-15",
        "KXNBAPTS-26OCT21ATLORL-ORLDBANE3-20",
    ]
    k = live.http_get(f"{live.KALSHI_MARKETS}?tickers={','.join(tickers)}")
    keep = ("ticker", "title", "status", "result", "yes_bid_dollars", "yes_ask_dollars",
            "no_bid_dollars", "no_ask_dollars", "last_price_dollars")  # fmt: skip
    ms = [{x: m[x] for x in keep if x in m} for m in k["markets"]]
    (OUT / "kalshi_markets.json").write_text(redate(json.dumps({"markets": ms}, indent=1)))




def record_series() -> None:
    """Trimmed public market lists used by the screenshot-scan ticker resolver tests."""
    keep = ("ticker", "title", "yes_sub_title", "event_ticker", "status", "floor_strike")
    for series, pick in (("KXNBAPTS", "ATLORL"), ("KXNBAREB", "ATLORL"), ("KXNBAGAME", "")):
        r = live.http_get(f"{live.KALSHI_MARKETS}?series_ticker={series}&status=open&limit=1000")
        ms = [m for m in r["markets"] if pick in m["event_ticker"]] if pick else r["markets"]
        if series == "KXNBAGAME":
            ms = [m for m in ms if "GSWDEN" in m["event_ticker"]] + ms[:0]
        ms = [{k: m.get(k) for k in keep} for m in ms][:40]
        text = json.dumps({"markets": ms, "cursor": ""}, indent=1)
        text = redate(text).replace("26OCT26", "24NOV12")
        (OUT / f"kalshi_series_{series}.json").write_text(text)


if __name__ == "__main__":
    main()
    record_series()
