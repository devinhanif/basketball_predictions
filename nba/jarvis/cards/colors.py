"""Local team table: full name and the embed stripe colour (a display choice, not data).

Colours are each club's primary brand colour, rounded to a hex value. Discord wants the embed
``color`` as a decimal integer. ``NEUTRAL`` is used when a team is unknown.
"""

from __future__ import annotations

NEUTRAL = 0x607D8B

TEAMS: dict[str, tuple[str, int]] = {
    "ATL": ("Atlanta Hawks", 0xE03A3E),
    "BKN": ("Brooklyn Nets", 0x000000),
    "BOS": ("Boston Celtics", 0x007A33),
    "CHA": ("Charlotte Hornets", 0x1D1160),
    "CHI": ("Chicago Bulls", 0xCE1141),
    "CLE": ("Cleveland Cavaliers", 0x860038),
    "DAL": ("Dallas Mavericks", 0x00538C),
    "DEN": ("Denver Nuggets", 0x0E2240),
    "DET": ("Detroit Pistons", 0xC8102E),
    "GSW": ("Golden State Warriors", 0x1D428A),
    "HOU": ("Houston Rockets", 0xCE1141),
    "IND": ("Indiana Pacers", 0xFDBB30),
    "LAC": ("LA Clippers", 0xC8102E),
    "LAL": ("Los Angeles Lakers", 0x552583),
    "MEM": ("Memphis Grizzlies", 0x5D76A9),
    "MIA": ("Miami Heat", 0x98002E),
    "MIL": ("Milwaukee Bucks", 0x00471B),
    "MIN": ("Minnesota Timberwolves", 0x236192),
    "NOP": ("New Orleans Pelicans", 0x0C2340),
    "NYK": ("New York Knicks", 0xF58426),
    "OKC": ("Oklahoma City Thunder", 0x007AC1),
    "ORL": ("Orlando Magic", 0x0077C0),
    "PHI": ("Philadelphia 76ers", 0x006BB6),
    "PHX": ("Phoenix Suns", 0xE56020),
    "POR": ("Portland Trail Blazers", 0xE03A3E),
    "SAC": ("Sacramento Kings", 0x5A2D81),
    "SAS": ("San Antonio Spurs", 0xC4CED4),
    "TOR": ("Toronto Raptors", 0xCE1141),
    "UTA": ("Utah Jazz", 0x4E008E),
    "WAS": ("Washington Wizards", 0x002B5C),
}

ABBRS: tuple[str, ...] = tuple(sorted(TEAMS))


def color(team: str) -> int:
    return TEAMS.get(team, ("", NEUTRAL))[1]


def full_name(team: str) -> str:
    return TEAMS.get(team, (team, NEUTRAL))[0]
