"""Pure-logic tests for the ingest queue (no subprocesses, no network)."""

from __future__ import annotations

from nba.ingest import queue as q


def test_step_order_and_rate_floor() -> None:
    names = [s.name for s in q.build_steps()]
    assert names[0] == "handover" and names[1:3] == ["cur:tracking", "cur:hustle"]
    games = [n for n in names if n.startswith("hist:games")]
    assert games[0].endswith("2021") and games[-1].endswith("2013")
    box = [n for n in names if n.startswith("hist:boxscore")]
    assert box[0].endswith("2021") and names.index(box[-1]) < names.index("cur:officials")
    assert names.index("cur:matchups") < names.index("hist:pbp:2021")
    assert (
        names.index("hist:pbp:2021") < names.index("hist:parse:2021") < names.index("hist:pbp:2020")
    )
    assert (
        names.index("hist:parse:2013") < names.index("hist:tracking") < names.index("hist:hustle")
    )
    assert float(q.RATE) >= 2.5


def test_every_history_step_targets_history_db_and_current_steps_do_not() -> None:
    for s in q.build_steps():
        if s.name.startswith(
            ("hist:games", "hist:boxscore", "hist:pbp", "hist:tracking", "hist:hustle", "hist:load")
        ):
            assert "--history" in s.argv, s.name
        if s.name.startswith(("cur:", "load:")):
            assert "--history" not in s.argv, s.name
    assert not any(s.season is not None and s.season < 2013 for s in q.build_steps())


def test_parse_progress() -> None:
    p = q.parse_progress("tracking: 1900/5269 cached=1327 fetched=573 failed=0 0.40/s")
    assert p == (1900, 5269, 573, 0, 0.40)
    p = q.parse_progress(
        "PROGRESS boxscore season=2021-22 100/1320 cached=0 fetched=100 failed=2 rate=0.390/s"
    )
    assert p == (100, 1320, 100, 2, 0.39)
    assert q.parse_progress("FAILED tracking[0022300701]: ReadTimeout") is None
    assert q.fmt_eta(0, 3600, 1.0) == "1.0 h"


def test_screen_seasons_first_and_2025_deferred_to_end() -> None:
    steps = {s.name: s for s in q.build_steps()}
    names = list(steps)
    for src in ("tracking", "hustle"):
        argv = steps[f"cur:{src}"].argv
        assert [argv[i + 1] for i, a in enumerate(argv) if a == "--seasons"] == [
            "2022",
            "2023",
            "2024",
        ]
        late = steps[f"cur:{src}:2025"].argv
        assert late[late.index("--seasons") + 1] == "2025"
    assert names[-2:] == ["cur:tracking:2025", "cur:hustle:2025"]
    assert names.index("hist:load:hustle") < names.index("cur:tracking:2025")
    assert float(q.RATE) >= 6.0
