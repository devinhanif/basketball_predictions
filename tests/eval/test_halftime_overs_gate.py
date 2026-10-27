"""Label-free gate for HALFTIME_OVERS_VS_PRICE on a synthetic capture folder."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.eval import halftime_overs_gate as g

T0 = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)


def _put(
    root: Path,
    rows: list[dict[str, Any]],
    source: str,
    key: str,
    ts: datetime,
    body: dict[str, Any],
) -> None:
    rel = Path(source) / key / f"{ts:%Y%m%dT%H%M%S}Z.json.gz"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(gzip.compress(json.dumps(body).encode()))
    rows.append(
        {
            "path": str(rel),
            "source": source,
            "key": key,
            "received_at": ts.isoformat(),
            "http_status": 200,
        }
    )


def _scoreboard(name: str, period: int) -> dict[str, Any]:
    comp = {
        "competitors": [
            {"homeAway": "away", "team": {"abbreviation": "GS"}, "score": "40"},
            {"homeAway": "home", "team": {"abbreviation": "LAL"}, "score": "60"},
        ]
    }
    return {
        "events": [
            {
                "id": "9",
                "status": {"type": {"name": name}, "period": period},
                "competitions": [comp],
            }
        ]
    }


def _summary(name: str) -> dict[str, Any]:
    labels = ["MIN", "PTS", "FG", "3PT", "REB", "AST", "PF"]
    ath = [
        {
            "athlete": {"displayName": "Stephen Curry"},
            "starter": True,
            "stats": ["18", "12", "4-14", "2-8", "3", "2", "3"],
        },
        {
            "athlete": {"displayName": "Bench Guy Jr."},
            "starter": False,
            "stats": ["0", "0", "0-0", "0-0", "0", "0", "0"],
        },
    ]
    return {
        "header": {"competitions": [{"status": {"type": {"name": name}}}]},
        "boxscore": {
            "players": [
                {
                    "team": {"abbreviation": "GS"},
                    "statistics": [{"labels": labels, "athletes": ath}],
                }
            ]
        },
    }


def _market(title: str, strike: float, ask: str, size: str = "100") -> dict[str, Any]:
    return {
        "event_ticker": "KXNBAPTS-26NOV01GSWLAL",
        "title": title,
        "floor_strike": strike,
        "strike_type": "greater",
        "status": "active",
        "yes_ask_dollars": ask,
        "yes_ask_size_fp": size,
    }


def _night(tmp: Path, quote_delay_s: int = 150) -> Path:
    night = tmp / "live_2026-11-01"
    rows: list[dict[str, Any]] = []
    _put(night, rows, "espn_scoreboard", "d", T0, _scoreboard("STATUS_IN_PROGRESS", 2))
    _put(
        night,
        rows,
        "espn_scoreboard",
        "d",
        T0 + timedelta(seconds=60),
        _scoreboard("STATUS_HALFTIME", 2),
    )
    _put(
        night,
        rows,
        "espn_scoreboard",
        "d",
        T0 + timedelta(seconds=900),
        _scoreboard("STATUS_IN_PROGRESS", 3),
    )
    _put(night, rows, "espn_summary", "9", T0 + timedelta(seconds=100), _summary("STATUS_HALFTIME"))
    markets = [
        _market("Stephen Curry: 20+ points", 19.5, "0.3000"),  # eligible, on grid
        _market("Stephen Curry: 35+ points", 34.5, "0.0500"),  # off grid
        _market("Stephen Curry: 10+ points", 9.5, "0.9900"),  # no tradeable ask
        _market("Bench Guy Jr.: 10+ points", 9.5, "0.2000"),  # DNP at halftime
        _market("Nobody Known: 10+ points", 9.5, "0.2000"),  # unmatched
    ]
    # a quote from BEFORE the decision time must be ignored
    _put(night, rows, "kalshi", "KXNBAPTS_p0", T0 + timedelta(seconds=90), {"markets": markets[:1]})
    _put(
        night,
        rows,
        "kalshi",
        "KXNBAPTS_p0",
        T0 + timedelta(seconds=60 + quote_delay_s),
        {"markets": markets},
    )
    with (night / "manifest.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return night


def test_fee_one_contract_rounds_up_once() -> None:
    assert g.fee_one_contract(0.50) == 0.02  # 0.0175 -> 2 c
    assert g.fee_one_contract(0.05) == 0.01  # 0.003325 -> 1 c
    assert g.fee_one_contract(0.10) == 0.01  # 0.0063 -> 1 c


def test_fold_and_strike_and_cold() -> None:
    assert g.fold("Luka Dončić") == "lukadoncic"
    assert g.fold("Jaren Jackson Jr.") == g.fold("Jaren Jackson")
    assert g.strike_of({"floor_strike": 19.5, "strike_type": "greater"}) == 20
    assert g.strike_of({"floor_strike": 19.5, "strike_type": "less"}) is None
    assert g.is_cold({"FG": "1-5"})
    assert not g.is_cold({"FG": "2-5"})
    assert not g.is_cold({"FG": "0-4"})


def test_night_counts_mechanics(tmp_path: Path) -> None:
    r = g.night_counts(_night(tmp_path))
    pts = r["by_stat"]["pts"]
    assert r["counts"]["games_with_snapshot"] == 1
    assert pts["markets_at_snapshot"] == 5
    assert pts["entry_candidates"] == 1
    assert pts["x_off_t508_grid"] == 1
    assert pts["x_no_tradeable_ask"] == 1
    assert pts["x_dnp_first_half"] == 1
    assert pts["x_player_unmatched"] == 1
    assert pts["slice_blowout15"] == 1 and pts["slice_fouls3"] == 1 and pts["slice_cold"] == 1


def test_quote_outside_window_is_not_used(tmp_path: Path) -> None:
    # page received 400 s after halftime start: t* = +120 s, window ends at +300 s
    r = g.night_counts(_night(tmp_path, quote_delay_s=400))
    assert (
        r["counts"].get("series_no_page_in_window", 0) == 4
    )  # no page in window for any of the four series
    assert "pts" not in r["by_stat"]
