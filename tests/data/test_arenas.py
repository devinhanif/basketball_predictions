"""Unit tests for nba.ingest.arenas: static coord lookup + haversine.

No network -- plain data/math.
"""

from __future__ import annotations

import pytest

from nba.ingest.arenas import (
    ARENA_COORDS,
    MAX_VALID_TEAM_ID,
    MIN_VALID_TEAM_ID,
    arena_distance_miles,
    haversine_miles,
)

LAKERS = 1610612747
CELTICS = 1610612738


def test_all_30_franchise_ids_present() -> None:
    expected_ids = set(range(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID + 1))
    assert set(ARENA_COORDS) == expected_ids
    assert len(ARENA_COORDS) == 30


def test_coords_are_plausible_lat_lon() -> None:
    for team_id, (lat, lon) in ARENA_COORDS.items():
        assert -90 <= lat <= 90, team_id
        assert -180 <= lon <= 180, team_id


def test_haversine_same_point_is_zero() -> None:
    assert haversine_miles(34.0, -118.0, 34.0, -118.0) == 0.0


def test_haversine_lal_bos_sanity() -> None:
    lat1, lon1 = ARENA_COORDS[LAKERS]
    lat2, lon2 = ARENA_COORDS[CELTICS]
    dist = haversine_miles(lat1, lon1, lat2, lon2)
    # LA <-> Boston great-circle distance is ~2,600 mi; allow 10% tolerance.
    assert dist == pytest.approx(2600, rel=0.10)


def test_arena_distance_miles_matches_haversine() -> None:
    lat1, lon1 = ARENA_COORDS[LAKERS]
    lat2, lon2 = ARENA_COORDS[CELTICS]
    assert arena_distance_miles(LAKERS, CELTICS) == haversine_miles(lat1, lon1, lat2, lon2)


def test_arena_distance_same_city_is_zero() -> None:
    clippers = 1610612746
    assert arena_distance_miles(LAKERS, clippers) == 0.0
