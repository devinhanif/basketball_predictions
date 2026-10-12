# ruff: noqa: E501
"""team_coach_games: allocation by game number, post-season carry, per-game known_at, gates."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from nba.data.coaches import (
    HANDCOPIED_PATH,
    REFERENCE_PATH,
    ReferenceMismatch,
    build_team_coach_games,
    coach_changes,
    compare_references,
    load_reference,
    norm,
    reference_problems,
)

A, B = 1610612737, 1610612738
STAFF = pl.DataFrame({"coach_id": [7, 8], "name": ["Jordi Fernández", "Steady"]})


def _games(n: int) -> pl.DataFrame:
    rows = [
        {"game_id": f"00224{i:05d}", "game_date": date(2024, 11, 1) + timedelta(days=i), "season": 2024,
         "home_team": A if i % 2 else B, "away_team": B if i % 2 else A}
        for i in range(1, n + 1)
    ]  # fmt: skip
    rows.append({"game_id": "0042400101", "game_date": date(2025, 4, 20), "season": 2024,
                 "home_team": A, "away_team": B})  # fmt: skip
    rows.append({"game_id": "0052400001", "game_date": date(2025, 4, 15), "season": 2024,
                 "home_team": B, "away_team": A})  # fmt: skip
    return pl.DataFrame(rows)


def _tips(g: pl.DataFrame) -> pl.DataFrame:
    return g.select(
        "game_id",
        (pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(hours=23)).alias("tipoff_utc"),
    )


def _ref(cut: int = 3) -> pl.DataFrame:
    n = 6
    return pl.DataFrame(
        {"season": [2024] * 3, "team_id": [A, A, B], "team_abbr": ["A", "A", "B"],
         "seq": [0, 1, 0], "coach_name": ["Old Coach", "Jordi Fernandez", "Steady"],
         "first_game": [1, cut + 1, 1], "last_game": [cut, n, n], "games": [cut, n - cut, n],
         "wins": [1, 1, 1], "losses": [0, 0, 0], "source_url": ["u/A", "u/A", "u/B"],
         "fetched_date": ["2026-10-11"] * 3}
    )  # fmt: skip


def test_change_takes_effect_on_the_first_game_he_ran_and_postseason_uses_last_coach() -> None:
    g = _games(6)
    out = build_team_coach_games(g, _ref(), _tips(g), STAFF)
    a = out.filter(pl.col("team_id") == A).sort("game_id")
    # 6 regular-season games, play-in (005...) then playoff (004...) after the last regular game
    assert (
        a["coach_name"].to_list()
        == ["Old Coach"] * 3 + ["Jordi Fernandez"] * 3 + ["Jordi Fernandez"] * 2
    )
    assert (
        a["coach_id"].to_list()[:3] == [None] * 3 and a["coach_id"].to_list()[3] == 7
    )  # diacritic-free match
    assert set(out.filter(pl.col("team_id") == B)["coach_name"]) == {"Steady"}
    assert out.group_by("game_id").len()["len"].to_list() == [2] * out["game_id"].n_unique()
    assert out.select("game_id", "team_id").n_unique() == out.height


def test_known_at_is_the_games_own_tip_and_planted_future_never_leaks() -> None:
    g = _games(6)
    tips = _tips(g)
    out = build_team_coach_games(g, _ref(), tips, STAFF)
    j = out.join(tips, on="game_id")
    assert (j["known_at"] == j["tipoff_utc"]).all()  # per game, never a season start
    # the new coach's first game is game 4; nothing dated before that tip carries him, even when the
    # input rows arrive shuffled
    first = g.filter(pl.col("game_id").str.starts_with("002")).sort("game_date")["game_id"][3]
    shuffled = g.sample(fraction=1.0, shuffle=True, seed=3)
    out2 = build_team_coach_games(shuffled, _ref(), _tips(shuffled), STAFF)
    new = out2.filter(pl.col("coach_name") == "Jordi Fernandez")
    assert new["known_at"].min() == tips.filter(pl.col("game_id") == first)["tipoff_utc"][0]
    before = tips.filter(pl.col("tipoff_utc") < new["known_at"].min())["game_id"]
    assert not new.filter(pl.col("game_id").is_in(before.to_list())).height


def test_counts_that_do_not_tile_the_season_are_listed_not_guessed() -> None:
    g = _games(6)
    bad = _ref().with_columns(
        pl.when(pl.col("coach_name") == "Steady")
        .then(5)
        .otherwise(pl.col("last_game"))
        .alias("last_game"),
        pl.when(pl.col("coach_name") == "Steady").then(5).otherwise(pl.col("games")).alias("games"),
    )
    probs = reference_problems(g, bad)
    assert len(probs) == 1 and "sum to 5" in probs[0]
    with pytest.raises(ReferenceMismatch):
        build_team_coach_games(g, bad, _tips(g), STAFF)
    gap = _ref().with_columns(
        pl.when(pl.col("seq") == 1).then(5).otherwise(pl.col("first_game")).alias("first_game"),
        pl.when(pl.col("seq") == 1).then(2).otherwise(pl.col("games")).alias("games"),
    )
    assert any("do not follow" in p for p in reference_problems(g, gap))


def test_coach_changes_report_the_effective_date() -> None:
    g = _games(6)
    ch = coach_changes(g, _ref())
    assert ch.rows() == [(2024, "A", "Jordi Fernandez", 4, "0022400004", date(2024, 11, 5))]


def test_norm_strips_diacritics_and_punctuation() -> None:
    assert norm("Jordi Fernández") == norm("Jordi Fernandez") == "jordifernandez"
    assert norm("J.B. Bickerstaff") == norm("JB Bickerstaff")


# The 14 team-seasons where team_coaches names someone other than the coach with the most games
# (data_audit_2026-10-11.md 7b): (season, team, team_coaches head coach, reference main coach).
DISAGREEMENTS = [
    (2022, "ATL", "Quin Snyder", "Nate McMillan"), (2022, "HOU", "Ime Udoka", "Stephen Silas"),
    (2022, "MIL", "Adrian Griffin", "Mike Budenholzer"), (2022, "PHI", "Nick Nurse", "Doc Rivers"),
    (2022, "PHX", "Frank Vogel", "Monty Williams"), (2022, "TOR", "Darko Rajakovic", "Nick Nurse"),
    (2022, "DET", "Monty Williams", "Dwane Casey"), (2023, "MIL", "Doc Rivers", "Adrian Griffin"),
    (2023, "BKN", "Jordi Fernandez", "Jacque Vaughn"), (2023, "PHX", "Mike Budenholzer", "Frank Vogel"),
    (2023, "WAS", "Brian Keefe", "Wes Unseld"), (2024, "DEN", "David Adelman", "Michael Malone"),
    (2024, "PHX", "Mitch Ott", "Mike Budenholzer"), (2024, "MEM", "Tuomas Iisalo", "Taylor Jenkins"),
]  # fmt: skip


def _main_coach(ref: pl.DataFrame, season: int, abbr: str) -> str:
    d = ref.filter((pl.col("season") == season) & (pl.col("team_abbr") == abbr))
    return d.sort("games", descending=True)["coach_name"][0]


@pytest.mark.parametrize(
    "path", [HANDCOPIED_PATH, REFERENCE_PATH], ids=["handcopied", "br_fetched"]
)
def test_the_14_team_coaches_disagreements_rederive(path) -> None:  # type: ignore[no-untyped-def]
    if not path.exists():
        pytest.skip(f"{path.name} not built yet: run the fetch and build-csv (nba.data.coaches_br)")
    ref = load_reference(path)
    assert len(DISAGREEMENTS) == 14
    for season, abbr, db_coach, main in DISAGREEMENTS:
        got = _main_coach(ref, season, abbr)
        assert got.split()[-1] == main.split()[-1], (
            season,
            abbr,
            got,
        )  # first names vary (Doc, Mike)
        assert norm(got) != norm(db_coach), (
            season,
            abbr,
        )  # the team_coaches name is not the main coach


def test_reference_files_sum_to_82_and_the_fetched_one_agrees_with_the_handcopied() -> None:
    old = load_reference(HANDCOPIED_PATH)
    sums = old.group_by("season", "team_id").agg(pl.col("games").sum().alias("n"))
    assert sums.height == 90 and (sums["n"] == 82).all()
    if not REFERENCE_PATH.exists():
        pytest.skip("head_coach_games_reference.csv not built yet")
    new = load_reference(REFERENCE_PATH)
    tot = new.group_by("season", "team_id").agg(pl.col("games").sum())
    assert tot.height == 120 and (tot["games"] == 82).all()
    assert compare_references(new, old) == []  # 100% agreement for 2022-24
