"""Section 5: one true sentence from docs/FAN_KNOWLEDGE.md, chosen by simple documented rules.

Rules, first match wins (the rule id is printed on the page so a reader can see why):

``blowout``  final margin >= 25 points. Devin's blowout rule and its measured size.
``star``     a player from Devin's watch-list (Doncic, Leonard, Brown, LeBron) has a stored
             points forecast in this game. The sentence carries that player's own replay bias
             with n and a game-resampled CI (computed from ``forward_scores`` by the caller).
``default``  nothing above applies: the most-knowable-thing sentence (stars' minutes vs bench).

A sentence never goes beyond what the source file records; descriptive numbers are labelled so.
"""

from __future__ import annotations

from dataclasses import dataclass

from nba.explain.data import PlayerBias

#: player_id -> (name, direction noted in FAN_KNOWLEDGE: "under" = actual above forecast)
WATCH_LIST: dict[int, tuple[str, str]] = {
    1629029: ("Luka Dončić", "under"),
    202695: ("Kawhi Leonard", "under"),
    1627759: ("Jaylen Brown", "under"),
    2544: ("LeBron James", "over"),
}
BLOWOUT_MARGIN = 25
SRC_BLOWOUT = "docs/FAN_KNOWLEDGE.md, 'Recomputed on the fixed lineups' (blowout rule row)"
SRC_STAR = "docs/FAN_KNOWLEDGE.md, 'Where the points model is good and bad'"
SRC_DEFAULT = "docs/FAN_KNOWLEDGE.md, 'Where the points model is good and bad'"


@dataclass
class Taught:
    rule: str
    sentence: str
    source: str


def choose(
    *,
    margin: int | None,
    forecast_players: set[int],
    bias: dict[int, PlayerBias | None],
    starter_minutes: dict[str, float | None] | None = None,
    names: dict[int, str] | None = None,
) -> Taught:
    """Pick the footer sentence. ``bias`` maps watch-list ids to their replay bias (or None)."""
    if margin is not None and margin >= BLOWOUT_MARGIN:
        extra = ""
        if starter_minutes:
            parts = [
                f"{k} starters averaged {v:.0f} minutes" for k, v in starter_minutes.items() if v
            ]
            if parts:
                extra = " Here, " + " and ".join(parts) + "."
        return Taught(
            "blowout",
            f"This one ended in a {margin}-point margin, and Devin's blowout rule is that once a "
            "team is up 25 late the bench plays for good: in 2022-25, after the gap first "
            "reached 25 in the fourth quarter, all five starters were off the floor on 45.4% of "
            "the remaining possessions (n = 40,308 possessions; descriptive, no interval was "
            f"computed).{extra}",
            SRC_BLOWOUT,
        )
    for pid, (name, _direction) in WATCH_LIST.items():
        if pid in forecast_players:
            b = bias.get(pid)
            nm = (names or {}).get(pid, name)
            lead = (
                "Devin's note: the points model runs under on Dončić, Leonard and Brown, and "
                "over on LeBron (the stars-under-forecast pattern)."
            )
            if b is None:
                return Taught(
                    "star",
                    f"{lead} {nm} played, but fewer than 20 scored games are stored for "
                    "him, so no bias is shown.",
                    SRC_STAR,
                )
            spans = b.lo <= 0.0 <= b.hi
            tail = (
                "the interval spans zero, so on this evidence it is not distinguishable "
                "from no bias"
                if spans
                else "the interval excludes zero"
            )
            return Taught(
                "star",
                f"{lead} For {nm} in the 2025-26 replay, actual points minus our forecast mean "
                f"averaged {b.mean:+.1f} over n = {b.n} scored games (95% CI {b.lo:+.1f} to "
                f"{b.hi:+.1f}, resampling games); {tail}. Descriptive, from the replay, not a "
                "frozen test.",
                SRC_STAR,
            )
    return Taught(
        "default",
        "A star's role and minutes are the most knowable thing in the league; the bench's "
        "minutes are decided after tip. That is why our forecasts are tightest, relative to "
        "scoring, for the stars and loosest for five-point-a-night bench players (replay, "
        "n = 26,460 scored player-games; descriptive).",
        SRC_DEFAULT,
    )
