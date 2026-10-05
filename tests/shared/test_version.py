from nba.shared.version import VERSION


def test_version_is_a_nonempty_semver_like_string() -> None:
    assert isinstance(VERSION, str)
    assert VERSION.count(".") == 2
    parts = VERSION.split(".")
    assert all(part.isdigit() for part in parts)
