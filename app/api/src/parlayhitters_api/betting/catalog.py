"""The fixed words and lists of the quote board: provider labels and kinds, what is unknown, notes.

Nothing here is a number about a bet. A provider's id is the export's ``book`` value as stored.
"""

from __future__ import annotations

from typing import Literal

UnknownName = Literal["fee", "settlement_rule_id", "liquidity", "live_status"]
Kind = Literal["sportsbook", "exchange"]

#: Display names for the book ids the export stores; an id not listed shows as itself.
#: ``williamhill_us`` is the vendor's id for Caesars (docs/research/lines_everywhere_2026-10-14.md).
LABELS: dict[str, str] = {
    "betmgm": "BetMGM",
    "betonlineag": "BetOnline.ag",
    "betopenly": "BetOpenly",
    "betrivers": "BetRivers",
    "bovada": "Bovada",
    "caesars": "Caesars",
    "draftkings": "DraftKings",
    "espnbet": "ESPN BET",
    "fanatics": "Fanatics",
    "fanduel": "FanDuel",
    "kalshi": "Kalshi",
    "mybookieag": "MyBookie.ag",
    "novig": "Novig",
    "pinnacle": "Pinnacle",
    "polymarket": "Polymarket",
    "prophetx": "ProphetX",
    "williamhill_us": "Caesars",
}

#: Exchanges: the vendor's ``us_ex`` region as configs/odds_live.yaml (line 4) documents it
#: ("us_ex = Kalshi, Polymarket, Novig, ProphetX, BetOpenly"). Every other id is a sportsbook.
#: A static, documented list; nothing about a provider's kind is inferred from its prices.
EXCHANGES: frozenset[str] = frozenset({"kalshi", "polymarket", "novig", "prophetx", "betopenly"})

#: Never a quote provider here, even if a later export stored a row under the id: an entry with
#: its own payout rules, not a per-pick price, so it has no place beside binary prices.
NOT_PROVIDERS: frozenset[str] = frozenset({"prizepicks"})

#: What the export does not carry about a quote. Served as null and named, never defaulted.
UNKNOWN: tuple[UnknownName, ...] = ("fee", "settlement_rule_id", "liquidity", "live_status")

PROVIDERS_NOTE = (
    "Stored snapshots from the nightly export. Delayed, not live. "
    "Analysis only; this service is read-only."
)

BOARD_NOTE = (
    "Stored snapshots from the nightly export. Delayed, not live. Analysis only; this service is "
    "read-only. For each line, provider and side only the newest snapshot stored before tip-off is "
    "shown; older snapshots are not returned. price_american is the price as stored; "
    "implied_prob_raw is the raw implied probability the export stored, vig included. A quote the "
    "export flagged not usable carries no price. Exchange prices are not sportsbook odds. Fees, "
    "settlement rules, liquidity and live status are not in the export and are unknown. No model "
    "number is served here; model_ref names the routes that serve it, under their own rules."
)

SLATE_NOTE = (
    "Which props have stored quote snapshots that night (the newest stored before tip-off for each "
    "line, provider and side). Delayed, not live. Analysis only; this service is read-only."
)

#: Said when the data source is the fixture replay, which stores no quote snapshots at all.
NO_SNAPSHOTS_NOTE = " This data source (the fixture replay) stores no quote snapshots."


def label(provider_id: str) -> str:
    return LABELS.get(provider_id, provider_id)


def kind(provider_id: str) -> Kind:
    return "exchange" if provider_id in EXCHANGES else "sportsbook"
