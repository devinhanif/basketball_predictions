"""Map Kalshi NBA tickers/markets to parlay legs (read-only; pure functions)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from nba.parlay.legs import Leg

TEAM_MARKETS = Path(__file__).resolve().parents[2] / "configs" / "team_markets.yaml"
PROP_STATS = {"pts", "reb", "ast", "fg3m"}
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )  # noqa: E501
}
_EVENT_RE = re.compile(
    r"^(?P<series>KX[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<away>[A-Z]{3})(?P<home>[A-Z]{3})$"
)
_SPREAD_RE = re.compile(r"^(?P<team>[A-Z]{2,3}?)(?P<n>\d+)$")


def load_team_abbr(path: Path = TEAM_MARKETS) -> dict[str, int]:
    raw = yaml.safe_load(path.read_text())
    return {str(v["abbr"]): int(k) for k, v in raw["teams"].items()}


def parse_event(event_ticker: str) -> tuple[date, str, str] | None:
    """``KXNBAGAME-26OCT20OKCSAS`` -> (2026-10-20, away 'OKC', home 'SAS')."""
    m = _EVENT_RE.match(event_ticker)
    if m is None or m["mon"] not in _MONTHS:
        return None
    return date(2000 + int(m["yy"]), _MONTHS[m["mon"]], int(m["dd"])), m["away"], m["home"]


@dataclass(frozen=True)
class MarketRow:
    ticker: str
    series: str
    event: str
    title: str
    player_id: int | None
    stat: str | None
    threshold: float | None
    yes_bid: float | None
    yes_ask: float | None
    price_ts: str | None

    @staticmethod
    def from_row(r: dict[str, Any]) -> MarketRow:
        return MarketRow(
            str(r["ticker"]),
            str(r["series_ticker"]),
            str(r["event_ticker"]),
            str(r["title"]),
            None if r["player_id"] is None else int(r["player_id"]),
            None if r["stat"] is None else str(r["stat"]),
            None if r["threshold"] is None else float(r["threshold"]),
            None if r["yes_bid"] is None else float(r["yes_bid"]),
            None if r["yes_ask"] is None else float(r["yes_ask"]),
            None if r["ts"] is None else str(r["ts"]),
        )


def map_market(
    m: MarketRow,
    abbr: dict[str, int],
    games_by_key: dict[tuple[date, int, int], str],
    team_of_player: dict[tuple[str, int], int],
) -> tuple[Leg | None, str | None]:
    """(leg, None) or (None, reason). The leg is the contract's YES condition."""
    ev = parse_event(m.event)
    if ev is None:
        return None, "unparseable_event"
    d, away_a, home_a = ev
    away, home = abbr.get(away_a), abbr.get(home_a)
    if away is None or home is None:
        return None, "unknown_team_abbr"
    gid = games_by_key.get((d, away, home))
    if gid is None:
        return None, "game_not_in_slate"
    suffix = m.ticker[len(m.event) + 1 :]
    if m.series == "KXNBAGAME":
        t = abbr.get(suffix)
        return (Leg(gid, 0, "win", 0.0, "yes", t), None) if t else (None, "unknown_team_abbr")
    if m.series == "KXNBASPREAD":
        sm = _SPREAD_RE.match(suffix)
        t = abbr.get(sm["team"]) if sm else None
        if t is None or m.threshold is None:
            return None, "unparseable_spread"
        return Leg(gid, 0, "spread", m.threshold, "yes", t), None
    if m.series == "KXNBATOTAL":
        if m.threshold is None:
            return None, "unparseable_total"
        return Leg(gid, 0, "total", m.threshold, "yes", None), None
    if m.stat in PROP_STATS and m.threshold is not None:
        if m.player_id is None:
            return None, "unmatched_player"
        t = team_of_player.get((gid, m.player_id))
        if t is None:
            return None, "no_model_for_player"
        return Leg(gid, m.player_id, m.stat, m.threshold, "yes", t), None
    return None, f"unsupported_series_or_stat:{m.series}"
