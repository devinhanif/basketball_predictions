"""Static NBA team reference data -- no network, no nba_api import.

``TEAM_ABBREV_TO_ID`` is nba_api's own static team-id scheme
(``nba_api.stats.static.teams``), copied here as a plain literal rather
than imported, because:

1. It is stable (these ids have not changed in nba_api's history and are
   unlikely to -- franchise ids are permanent even through relocations
   like the Seattle -> OKC move, which kept 1610612760).
2. Copying it in means every module that needs a team-abbrev lookup (e.g.
   resolving an injury report's "BOS@DET" matchup to a ``games.game_id``)
   can do so with zero import-time dependency on ``nba_api`` and zero
   network access -- consistent with every other ``nba.ingest`` module's
   "safe to import with no network access" discipline.

Cross-checked against this project's own ``games`` table: ``ATL`` ==
``1610612737`` matches every ``home_team``/``away_team`` id already loaded
for Atlanta games.
"""

from __future__ import annotations

#: nba_api static team ids (verified stable, see module docstring).
TEAM_ABBREV_TO_ID: dict[str, int] = {
    "ATL": 1610612737,
    "BOS": 1610612738,
    "CLE": 1610612739,
    "NOP": 1610612740,
    "CHI": 1610612741,
    "DAL": 1610612742,
    "DEN": 1610612743,
    "GSW": 1610612744,
    "HOU": 1610612745,
    "LAC": 1610612746,
    "LAL": 1610612747,
    "MIA": 1610612748,
    "MIL": 1610612749,
    "MIN": 1610612750,
    "BKN": 1610612751,
    "NYK": 1610612752,
    "ORL": 1610612753,
    "IND": 1610612754,
    "PHI": 1610612755,
    "PHX": 1610612756,
    "POR": 1610612757,
    "SAC": 1610612758,
    "SAS": 1610612759,
    "OKC": 1610612760,
    "TOR": 1610612761,
    "UTA": 1610612762,
    "MEM": 1610612763,
    "WAS": 1610612764,
    "DET": 1610612765,
    "CHA": 1610612766,
}
