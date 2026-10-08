"""Title -> (player_name, stat, threshold) parsing."""

from __future__ import annotations

import pytest

from nba.kalshi.thresholds import UnparseableTitleError, parse_threshold_title


@pytest.mark.parametrize(
    "title,expected",
    [
        ("LeBron James Points 25+", ("LeBron James", "pts", 25.0)),
        ("Stephen Curry 3PM 4+", ("Stephen Curry", "fg3m", 4.0)),
        ("Nikola Jokic Rebounds 12.5+", ("Nikola Jokic", "reb", 12.5)),
        ("Luka Doncic Assists 8+", ("Luka Doncic", "ast", 8.0)),
        ("  Giannis Antetokounmpo   PRA   45+  ", ("Giannis Antetokounmpo", "pra", 45.0)),
    ],
)
def test_parse_threshold_title(title: str, expected: tuple[str, str, float]) -> None:
    assert parse_threshold_title(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Lakers to win",
        "LeBron James Points",  # no threshold
        "25+ Points LeBron James",  # wrong order
        "",
    ],
)
def test_unparseable_titles_raise(title: str) -> None:
    with pytest.raises(UnparseableTitleError):
        parse_threshold_title(title)


def test_threshold_is_non_increasing_property_shape() -> None:
    """Sanity check on the parsed threshold itself (full P(>=N) monotonicity
    is enforced in the props distribution layer, not here) -- just confirms
    larger printed thresholds parse to larger floats, i.e. no off-by-type
    string/float confusion creeps into comparisons downstream."""
    _, _, low = parse_threshold_title("LeBron James Points 10+")
    _, _, high = parse_threshold_title("LeBron James Points 30+")
    assert low < high
