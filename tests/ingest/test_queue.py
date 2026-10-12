"""Pure-logic tests for the ingest queue (no subprocesses, no network)."""

from __future__ import annotations

from nba.ingest import queue as q


def test_step_order_and_rate_floor() -> None:
    names = [s.name for s in q.build_steps()]
    assert names[0] == "handover" and names[1:3] == ["cur:tracking", "cur:hustle"]
    # officials 2022-24 (+ load) come first after hustle, then history games, box scores
    assert names[3:5] == ["cur:officials", "load:officials"]
    games = [n for n in names if n.startswith("hist:games")]
    assert games == ["hist:games:2021", "hist:games:2020", "hist:games:2019"]
    box = [n for n in names if n.startswith("hist:boxscore")]
    assert box == ["hist:boxscore:2021", "hist:boxscore:2020", "hist:boxscore:2019"]
    assert names.index("load:officials") < names.index(games[0])
    assert names.index(games[-1]) < names.index(box[0])
    assert names.index(box[-1]) < names.index("hist:pbp:2021")
    assert (
        names.index("hist:pbp:2021") < names.index("hist:parse:2021") < names.index("hist:pbp:2020")
    )
    assert names.index("hist:parse:2019") < names.index("cur:officials:2025")
    assert names.index("cur:officials:2025") < names.index("cur:shots")
    # 2026-10-11: history tracking/hustle run before shots/matchups (Devin's priority)
    first = ("load:officials:2025", "hist:tracking", "hist:hustle")
    i_off, i_tr, i_hu = (names.index(n) for n in first)
    assert i_off < i_tr < i_hu
    assert names.index("cur:hustle:2025") < names.index("cur:shots") < names.index("cur:matchups")
    assert float(q.RATE) >= 2.5


def test_seasons_before_2019_are_not_queued() -> None:
    steps = q.build_steps()
    assert q.HISTORY_SEASONS == [2021, 2020, 2019]
    assert not any(s.season is not None and s.season < 2019 for s in steps)
    assert not any(n.endswith(("2018", "2013")) for n in (s.name for s in steps))


def test_officials_seasons_split() -> None:
    steps = {s.name: s for s in q.build_steps()}

    def seasons(argv: list[str]) -> list[str]:
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--seasons"]

    assert seasons(steps["cur:officials"].argv) == ["2022", "2023", "2024"]
    assert seasons(steps["cur:officials:2025"].argv) == ["2025"]


def test_every_history_step_targets_history_db_and_current_steps_do_not() -> None:
    for s in q.build_steps():
        if s.name.startswith(
            ("hist:games", "hist:boxscore", "hist:pbp", "hist:tracking", "hist:hustle", "hist:load")
        ):
            assert "--history" in s.argv, s.name
        if s.name.startswith(("cur:", "load:")):
            assert "--history" not in s.argv, s.name


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
    assert names[-2:] == ["cur:matchups", "load:matchups"]
    assert names.index("hist:load:hustle") < names.index("cur:tracking:2025")
    assert float(q.RATE) >= 6.0


def test_boxsample_step_only_when_ids_exist_and_targets_its_own_db(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(q, "BOXSAMPLE_IDS", tmp_path / "missing.txt")
    assert "hist:boxsample" not in [s.name for s in q.build_steps()]
    ids = tmp_path / "ids.txt"
    ids.write_text("0021500001\n")
    monkeypatch.setattr(q, "BOXSAMPLE_IDS", ids)
    steps = {s.name: s for s in q.build_steps()}
    names = list(steps)
    step = steps["hist:boxsample"]
    assert step.argv[0] == "nba.ingest.boxsample" and "--history" not in step.argv
    assert names.index("hist:parse:2019") < names.index("hist:boxsample")
    assert names.index("hist:boxsample") < names.index("cur:officials:2025")
    assert float(step.argv[step.argv.index("--rate-limit-s") + 1]) >= 2.5
