"""Parse Kalshi "N+" prop-market titles into (player_name, stat, threshold).

Kalshi NBA player-prop markets are titled along the lines of
``"LeBron James Points 25+"`` or ``"Stephen Curry 3PM 4+"`` -- a player
display name, a stat label, and an integer-or-half-point threshold followed
by ``+``. This module turns that free-text title into the structured
``(player_name, stat, threshold)`` tuple CLAUDE.md asks for; name ->
``player_id`` resolution is deliberately a separate step
(``nba.kalshi.aliases.resolve_kalshi_player_name``) so a bad title parse and
a bad name match fail with distinguishable, specific errors.

Pure string parsing -- no network, no DB.
"""

from __future__ import annotations

import re

#: Kalshi stat labels (as they appear in titles) -> this project's
#: ``player_rates``/``prop_predictions`` stat vocabulary.
_STAT_ALIASES: dict[str, str] = {
    "points": "pts",
    "pts": "pts",
    "rebounds": "reb",
    "reb": "reb",
    "rebs": "reb",
    "assists": "ast",
    "ast": "ast",
    "asts": "ast",
    "3pm": "fg3m",
    "threes": "fg3m",
    "three-pointers": "fg3m",
    "fg3m": "fg3m",
    "pra": "pra",
    "points+rebounds+assists": "pra",
}

_STAT_PATTERN = "|".join(sorted((re.escape(k) for k in _STAT_ALIASES), key=len, reverse=True))

#: ``"<Player Name> <Stat> <N>+"``, e.g. "LeBron James Points 25+",
#: "Stephen Curry 3PM 4+". Threshold may be an integer or half-point
#: (Kalshi lines are sometimes posted at X.5 to avoid push/tie markets).
_TITLE_RE = re.compile(
    rf"^(?P<name>[A-Za-z.'\-À-ɏ ]+?)\s+"
    rf"(?P<stat>{_STAT_PATTERN})\s+"
    rf"(?P<threshold>\d+(?:\.\d+)?)\s*\+\s*$",
    re.IGNORECASE,
)


class UnparseableTitleError(ValueError):
    """Raised when a market title doesn't match the expected "N+" shape.

    Loud by design: a market whose threshold can't be parsed must never be
    silently dropped or inserted with a null threshold -- callers skip (and
    log) it, they don't guess.
    """


def parse_threshold_title(title: str) -> tuple[str, str, float]:
    """Parse a Kalshi "N+" prop title into ``(player_name, stat, threshold)``.

    ``stat`` is normalized to this project's vocabulary (``pts|reb|ast|
    fg3m|pra``) via ``_STAT_ALIASES``. Raises ``UnparseableTitleError`` for
    any title that isn't a recognized "<player> <stat> <N>+" shape (e.g.
    game-level markets, which this parser is not responsible for).
    """
    match = _TITLE_RE.match(title.strip())
    if match is None:
        raise UnparseableTitleError(
            f"title {title!r} does not match the expected '<player> <stat> <N>+' shape"
        )
    name = " ".join(match.group("name").split())
    stat_raw = match.group("stat").lower()
    stat = _STAT_ALIASES[stat_raw]
    threshold = float(match.group("threshold"))
    return name, stat, threshold
