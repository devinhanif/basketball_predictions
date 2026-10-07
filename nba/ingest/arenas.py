"""Static lookup of current NBA franchise home-arena coordinates.

Committed, hand-maintained data -- no network, no nba_api import. Used for
travel-distance features (``team_context.travel_miles`` in CLAUDE.md's
schema): the haversine distance between a team's previous game's arena and
its next game's arena.

Coordinates are approximate home-city/arena lat/lon (public knowledge,
sufficient precision for travel-distance features -- we do not need
rooftop-exact arena coordinates). Keyed by the standard NBA franchise
``team_id`` range (1610612737..1610612766), same ids used throughout
``nba/ingest`` and the ``games``/``team_context`` tables.
"""

from __future__ import annotations

import math

#: team_id -> (latitude, longitude) of the team's home arena/city.
ARENA_COORDS: dict[int, tuple[float, float]] = {
    1610612737: (33.7573, -84.3963),  # Atlanta Hawks (State Farm Arena)
    1610612738: (42.3663, -71.0622),  # Boston Celtics (TD Garden)
    1610612739: (41.4965, -81.6882),  # Cleveland Cavaliers (Rocket Mortgage FieldHouse)
    1610612740: (29.9490, -90.0821),  # New Orleans Pelicans (Smoothie King Center)
    1610612741: (41.8807, -87.6742),  # Chicago Bulls (United Center)
    1610612742: (32.7905, -96.8103),  # Dallas Mavericks (American Airlines Center)
    1610612743: (39.7487, -105.0077),  # Denver Nuggets (Ball Arena)
    1610612744: (37.7680, -122.3877),  # Golden State Warriors (Chase Center)
    1610612745: (29.7508, -95.3621),  # Houston Rockets (Toyota Center)
    1610612746: (34.0430, -118.2673),  # LA Clippers (Intuit Dome)
    1610612747: (34.0430, -118.2673),  # LA Lakers (Crypto.com Arena)
    1610612748: (25.7814, -80.1870),  # Miami Heat (Kaseya Center)
    1610612749: (43.0451, -87.9172),  # Milwaukee Bucks (Fiserv Forum)
    1610612750: (44.9795, -93.2760),  # Minnesota Timberwolves (Target Center)
    1610612751: (40.6826, -73.9754),  # Brooklyn Nets (Barclays Center)
    1610612752: (40.7505, -73.9934),  # New York Knicks (Madison Square Garden)
    1610612753: (28.5392, -81.3839),  # Orlando Magic (Kia Center)
    1610612754: (39.7640, -86.1555),  # Indiana Pacers (Gainbridge Fieldhouse)
    1610612755: (39.9012, -75.1720),  # Philadelphia 76ers (Wells Fargo Center)
    1610612756: (33.4457, -112.0712),  # Phoenix Suns (Footprint Center)
    1610612757: (45.5316, -122.6668),  # Portland Trail Blazers (Moda Center)
    1610612758: (38.6494, -121.5181),  # Sacramento Kings (Golden 1 Center)
    1610612759: (29.4269, -98.4375),  # San Antonio Spurs (Frost Bank Center)
    1610612760: (35.4634, -97.5151),  # Oklahoma City Thunder (Paycom Center)
    1610612761: (43.6435, -79.3791),  # Toronto Raptors (Scotiabank Arena)
    1610612762: (40.7683, -111.9011),  # Utah Jazz (Delta Center)
    1610612763: (35.1382, -90.0506),  # Memphis Grizzlies (FedExForum)
    1610612764: (38.8981, -77.0209),  # Washington Wizards (Capital One Arena)
    1610612765: (42.3410, -83.0552),  # Detroit Pistons (Little Caesars Arena)
    1610612766: (35.2251, -80.8392),  # Charlotte Hornets (Spectrum Center)
}

MIN_VALID_TEAM_ID = 1610612737
MAX_VALID_TEAM_ID = 1610612766


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in miles."""
    earth_radius_mi = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_mi * math.asin(math.sqrt(a))


def arena_distance_miles(team_id_a: int, team_id_b: int) -> float:
    """Haversine distance between two teams' home arenas, in miles."""
    lat1, lon1 = ARENA_COORDS[team_id_a]
    lat2, lon2 = ARENA_COORDS[team_id_b]
    return haversine_miles(lat1, lon1, lat2, lon2)
