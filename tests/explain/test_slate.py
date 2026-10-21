"""The slate briefing: sections, the report-aware header, the OUT and fallback sentences, the
changes section, official-roster placement and its fallback, the exit codes, the lock
and atomic-write behaviour, read-only access, and no betting words on the page.

The fixture is a small DuckDB built here (no real data). Player ids are fake (9000001+): their names
come from the roster files when a test writes one, else read ``Player <id>``. Game 0022600001 is
DET (home) vs BOS (away), tip 2026-10-20 19:00 UTC = 14:00 CDT. Tests pass a roster root that does
not exist unless they write one, so the live ``data/rosters`` cache is never read here.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from nba.explain import slate as S
from nba.explain.__main__ import main
from nba.explain.data import open_ro

CDT = ZoneInfo("America/Chicago")
DET, BOS, LAL, GSW = 1610612765, 1610612738, 1610612747, 1610612744
MIA, NYK = 1610612748, 1610612752
G1, G2, G3, G4 = "0022600001", "0022600002", "0022600003", "0022600004"
TIP1 = datetime(2026, 10, 20, 19, 0)  # 14:00 CDT
RUN1 = datetime(2026, 10, 20, 15, 44, 1)  # 10:44 CDT: the first run of the slate
RUN2 = datetime(2026, 10, 20, 17, 30)  # 12:30 CDT, before G1's cutoff (18:00 UTC)
# words a fan could read as betting vocabulary; \b keeps "overall" and "underway" out of it
BET_WORDS = re.compile(
    r"\b(bet|bets|betting|parlay|parlays|ev|edge|odds|stake|stakes|pick|picks|book|books|"
    r"sportsbook|line|lines|over|under|wager|wagers|lock|value|units|fade)\b",
    re.I,
)
FOOTER = "Generated automatically each night; not individually reviewed."
EARLY_NOW = datetime(2026, 10, 20, 9, 41, tzinfo=CDT)
REPORT_NOW = datetime(2026, 10, 20, 12, 40, tzinfo=CDT)
SLATE = date(2026, 10, 20)
NO_ROSTER = Path("/nonexistent-roster-root-for-tests")  # hermetic: no cache, so the fallback runs
LOCK_MSG = 'IO Error: Could not set lock on file "nba.duckdb": Conflicting lock is held'

SCHEMA = [
    "CREATE TABLE games(game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
    "away_team INTEGER, home_pts INTEGER, away_pts INTEGER)",
    "CREATE TABLE forward_predictions(run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, "
    "tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INTEGER, "
    "prediction JSON)",
    "CREATE TABLE player_availability(player_id INTEGER, as_of TIMESTAMP, game_id VARCHAR, "
    "status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)",
    "CREATE TABLE player_game_stats(game_id VARCHAR, player_id INTEGER, team_id INTEGER, "
    "minutes FLOAT, pts INTEGER, reb INTEGER, ast INTEGER, fg3m INTEGER, starter BOOLEAN)",
    "CREATE TABLE forward_withdrawals(withdrawn_at TIMESTAMP, run_id VARCHAR, game_id VARCHAR, "
    "model_name VARCHAR, target VARCHAR, player_id INTEGER, official_team_id INTEGER, "
    "reason VARCHAR)",
]
INJ, MOV = "rung0_injury_elo", "rung0_mov_elo"
PI, PR = "props_context_residual", "props_recency_v1"


def _prop(mean: float, rec: float, mins: float, play: float) -> dict[str, Any]:
    return {
        "mean": mean,
        "mean_recency": rec,
        "proj_minutes": mins,
        "p_play": play,
        "q10": mean * 0.5,
        "q50": mean,
        "q90": mean * 1.6,
        "bucket": "smooth",
        "routed_to": "context_residual",
        "fallback_reason": None,
    }


def _win(
    home: int, away: int, p: float, p_mov: float, *, fb: str | None = None, rep: str | None = None
) -> dict[str, Any]:
    return {
        "home_team": home,
        "away_team": away,
        "p_home": p,
        "p_mov_elo": p_mov,
        "fallback_reason": fb,
        "report_as_of_et": rep,
        "primary": True,
    }


def _mov(home: int, away: int, p: float) -> dict[str, Any]:
    return {"home_team": home, "away_team": away, "p_home": p, "primary": False}


def make_db(
    path: Path,
    *,
    second_run: bool,
    drop_run2_9000011: bool = False,
    early_report: bool = True,
    report_stamp: str = "2026-10-20 06:45:00",
) -> Path:
    con = duckdb.connect(str(path))
    for ddl in SCHEMA:
        con.execute(ddl)
    # holdout season (2025) and the season before (2024): neither may place a player on a team
    con.execute(
        "INSERT INTO games VALUES ('0022500999', '2025-11-03', 2025, ?, ?, 100, 90)", [DET, BOS]
    )
    con.execute(
        "INSERT INTO games VALUES ('0022400999', '2025-03-01', 2024, ?, ?, 100, 90)", [DET, BOS]
    )
    con.execute(
        "INSERT INTO games VALUES ('0022600000', '2026-10-19', 2026, ?, ?, 110, 105)", [DET, BOS]
    )
    for g, h, a in ((G1, DET, BOS), (G2, LAL, GSW), (G3, MIA, NYK), (G4, 1610612756, 1610612757)):
        con.execute("INSERT INTO games VALUES (?, '2026-10-20', 2026, ?, ?, NULL, NULL)", [g, h, a])
    for pid, tid in (
        (9000001, BOS),
        (9000002, BOS),
        (9000003, BOS),
        (9000004, BOS),
        (9000010, DET),
        (9000011, DET),
        (9000020, DET),
        (9000021, DET),
    ):
        con.execute(
            "INSERT INTO player_game_stats VALUES ('0022600000', ?, ?, 30, 10, 5, 3, 1, true)",
            [pid, tid],
        )
    # the 2025 and 2024 rows put 9000030 on DET; 9000002 was on MIA in 2024. Neither is current.
    con.execute(
        "INSERT INTO player_game_stats VALUES ('0022500999', 9000030, ?, 20, 5, 2, 1, 0, false)",
        [DET],
    )
    con.execute(
        "INSERT INTO player_game_stats VALUES ('0022400999', 9000030, ?, 20, 5, 2, 1, 0, false)",
        [DET],
    )
    con.execute(
        "INSERT INTO player_game_stats VALUES ('0022400999', 9000002, ?, 20, 5, 2, 1, 0, false)",
        [MIA],
    )

    def pred(
        made: datetime,
        game: str,
        tip: datetime,
        model: str,
        target: str,
        pid: int,
        body: dict[str, Any],
    ) -> None:
        con.execute(
            "INSERT INTO forward_predictions VALUES (?, ?, ?, ?, ?, 'v', ?, ?, ?)",
            [f"run{made:%Y%m%d%H%M}", made, game, tip, model, target, pid, json.dumps(body)],
        )

    # run 1 (the early run): every game
    early_rep = report_stamp if early_report else None
    pred(RUN1, G1, TIP1, INJ, "win_prob_home", -1, _win(DET, BOS, 0.5571, 0.5631, rep=early_rep))
    pred(RUN1, G1, TIP1, MOV, "win_prob_home", -1, _mov(DET, BOS, 0.5631))
    pts1 = {
        9000001: (24.5, 23.1, 33, 0.97),
        9000002: (18.0, 17.0, 30, 0.90),
        9000003: (12.0, 12.0, 22, 0.80),
        9000004: (8.0, 7.5, 15, 0.40),
        9000020: (21.0, 20.0, 34, 0.98),
        9000021: (17.0, 18.0, 31, 0.90),
        9000010: (15.0, 14.0, 25, 0.05),
        9000011: (9.0, 10.0, 20, 0.62),
        9000030: (5.0, 5.0, 12, 0.50),
    }
    for pid, (m, r, mins, pp) in pts1.items():
        pred(RUN1, G1, TIP1, PI, "pts", pid, _prop(m, r, mins, pp))
    pred(RUN1, G1, TIP1, PI, "reb", 9000020, _prop(9.1, 8.5, 34, 0.98))
    pred(RUN1, G1, TIP1, PI, "reb", 9000002, _prop(6.0, 6.2, 30, 0.90))
    pred(RUN1, G1, TIP1, PI, "ast", 9000001, _prop(5.2, 4.9, 33, 0.97))
    # 9000003 is withdrawn (the official roster places him elsewhere): never shown or compared
    con.execute(
        "INSERT INTO forward_withdrawals VALUES (?, 'w', ?, ?, 'pts', 9000003, ?, "
        "'official_roster_team_not_in_game')",
        [RUN2, G1, PI, MIA],
    )
    # G2's first run is later than the slate's first run: its baseline is its own first run
    t2 = datetime(2026, 10, 20, 23, 0)
    pred(RUN2, G2, t2, INJ, "win_prob_home", -1, _win(LAL, GSW, 0.6200, 0.6000))
    pred(RUN2, G2, t2, MOV, "win_prob_home", -1, _mov(LAL, GSW, 0.6))
    t3 = datetime(2026, 10, 21, 0, 0)
    pred(
        RUN1,
        G3,
        t3,
        INJ,
        "win_prob_home",
        -1,
        _win(MIA, NYK, 0.4100, 0.4100, fb="stale_elo_params"),
    )
    pred(RUN1, G3, t3, MOV, "win_prob_home", -1, _mov(MIA, NYK, 0.41))
    # G4 tips 01:30 UTC on 10-21 (an ET 10-20 game); its only row was made after its T-60 cutoff
    pred(
        datetime(2026, 10, 21, 0, 40),
        G4,
        datetime(2026, 10, 21, 1, 30),
        INJ,
        "win_prob_home",
        -1,
        _win(1610612757, 1610612756, 0.5, 0.5),
    )
    if early_report:
        for pid, status, why in (
            (9000010, "out", "Injury/Illness-RightKnee;Sprain"),
            (9000011, "doubtful", "Injury/Illness-LeftAnkle;Sprain"),
            (9000050, "out", "Injury/Illness-Hamstring;Strain"),
        ):
            con.execute(
                "INSERT INTO player_availability VALUES (?, '2026-10-20 06:45:00', ?, ?, ?, "
                "'nba_official_report', '2026-10-20 06:45:00')",
                [pid, G1, status, why],
            )
    if second_run:
        # run 2: G1 moves; 9000011 is OUT on the 13:00 ET snapshot; 9000004's P(play) rises
        pred(
            RUN2,
            G1,
            TIP1,
            INJ,
            "win_prob_home",
            -1,
            _win(DET, BOS, 0.5200, 0.5631, rep="2026-10-20 13:00:00"),
        )
        pred(RUN2, G1, TIP1, MOV, "win_prob_home", -1, _mov(DET, BOS, 0.5631))
        pred(RUN2, G1, TIP1, PI, "pts", 9000001, _prop(21.5, 23.1, 30, 0.97))
        pred(RUN2, G1, TIP1, PI, "pts", 9000004, _prop(8.0, 7.5, 15, 0.55))
        if not drop_run2_9000011:  # the live path writes a row only when it changes
            pred(RUN2, G1, TIP1, PI, "pts", 9000011, _prop(9.0, 10.0, 20, 0.31))
        for pid, status, why in (
            (9000010, "out", "Injury/Illness-RightKnee;Sprain"),
            (9000011, "out", "Injury/Illness-LeftAnkle;Sprain"),
        ):
            con.execute(
                "INSERT INTO player_availability VALUES (?, '2026-10-20 13:00:00', ?, ?, ?, "
                "'nba_official_report', '2026-10-20 13:00:00')",
                [pid, G1, status, why],
            )
    con.close()
    return path


def make_roster(root: Path, on: date, team: int, players: list[tuple[int, str]]) -> None:
    d = root / on.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "team_id": [team] * len(players),
            "player_id": [p for p, _ in players],
            "player_name": [n for _, n in players],
        }
    ).write_parquet(d / f"{team}.parquet")


@pytest.fixture
def early_db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "early.duckdb", second_run=False)


@pytest.fixture
def both_db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "both.duckdb", second_run=True)


def _page(
    db: Path,
    edition: str,
    now: datetime = EARLY_NOW,
    slate: date = SLATE,
    roots: Path = NO_ROSTER,
) -> str:
    con = open_ro(db)
    try:
        return S.render(S.build_briefing(con, slate, edition, now, roots))
    finally:
        con.close()


# --------------------------------------------------------------------------------- early


def test_early_edition_header_footer_and_sentence(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert text.startswith("# Slate briefing, Tuesday 2026-10-20 (ET slate)")
    assert (
        "**Early edition**, written 2026-10-20 09:41 CDT. Injury report used: the 06:45 ET report; "
        "later reports can move these numbers."
    ) in text
    assert FOOTER in text
    assert S.SENTENCE in text
    assert "What changed" not in text


def test_early_header_without_any_report_says_so(tmp_path: Path) -> None:
    db = make_db(tmp_path / "noreport.duckdb", second_run=False, early_report=False)
    text = _page(db, "early")
    assert (
        "No official injury report was used for these games yet; numbers move after about 13:00 ET."
        in text
    )
    assert "Injury report used" not in text


def test_early_game_block_names_absences_and_favourite(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "### BOS @ DET, tip 14:00 CDT" in text
    assert (
        "Home (DET) wins 55.7% (model), 56.3% from team strength alone, -0.6 points from the "
        "injury report. OUT on the 06:45 ET report: Player 9000010 (DET, right knee, sprain). "
        "1 more OUT entry on the report not placed on either team."
    ) in text
    assert "Favourite: Detroit Pistons, at home (55.7%)." in text


def test_no_report_yet_says_so(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "### GSW @ LAL, tip 18:00 CDT" in text
    assert "No absences on the report yet." in text


def test_fallback_sentence_names_the_reason(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "The forecast fell back: stale_elo_params." in text
    assert "Favourite: New York Knicks, away (59.0%)." in text


def test_no_cache_note_says_no_roster_cache(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "no official roster cache for 2026-10-20" in text
    assert "latest box score in the 2026-27 season is used" in text


def test_props_lines_format_and_model_if_he_plays(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "Player forecasts (model averages if he plays):" in text
    assert (
        "- Player 9000001: 24.5 points (recency average 23.1, model +1.4; about 33 minutes; "
        "plays 97%)"
    ) in text
    assert "Most rebounds: Player 9000020: 9.1 rebounds" in text
    assert "Most assists: Player 9000001: 5.2 assists" in text
    assert "1 player forecast(s) not shown: on neither team's roster." in text
    assert "Player 9000030" not in text  # 2025 and 2024 team rows never place him


def test_ruled_out_player_has_no_projection_shown(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "OUT on the report, no forecast shown: Player 9000010 (DET, right knee, sprain)." in text
    assert "Player 9000010: 15.0 points" not in text


def test_withdrawn_player_is_not_shown(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "- Player 9000003" not in text


def test_late_only_game_says_no_forecast_before_cutoff(early_db: Path) -> None:
    text = _page(early_db, "early")
    assert "### Game 0022600004, tip 20:30 CDT" in text
    assert "No win forecast was stored for this game before tip minus 60 minutes" in text


def test_report_time_that_matches_no_lines_is_said_plainly(tmp_path: Path) -> None:
    db = make_db(tmp_path / "stamp.duckdb", second_run=False, report_stamp="2026-10-20 06:40:00")
    text = _page(db, "early")
    assert (
        "No report lines were found for this game at 06:40 ET, so no absences are counted." in text
    )


def test_official_roster_places_players_and_names_them(early_db: Path, tmp_path: Path) -> None:
    roots = tmp_path / "rosters"
    make_roster(
        roots,
        SLATE,
        BOS,
        [
            (9000001, "Roster Ann"),
            (9000002, "Roster Bo"),
            (9000003, "Roster Cy"),
            (9000004, "Roster Di"),
        ],
    )
    make_roster(
        roots,
        SLATE,
        DET,
        [
            (9000010, "Roster Dan"),
            (9000011, "Roster Ed"),
            (9000020, "Roster Fay"),
            (9000021, "Roster Gil"),
        ],
    )
    make_roster(roots, SLATE, MIA, [(9000050, "Roster Max")])  # on a team not in G1
    text = _page(early_db, "early", roots=roots)
    assert "on file for 3 of 6 teams on this slate" in text
    assert "OUT on the 06:45 ET report: Roster Dan (DET, right knee, sprain)." in text
    assert "1 more OUT entry on the report not placed on either team." in text
    assert "- Roster Ann: 24.5 points" in text
    assert "1 player forecast(s) not shown: on neither team's roster." in text


def test_full_roster_cache_is_stated_with_its_time(early_db: Path, tmp_path: Path) -> None:
    roots = tmp_path / "rosters"
    make_roster(roots, SLATE, BOS, [(9000001, "A")])
    make_roster(roots, SLATE, DET, [(9000010, "B")])
    make_roster(roots, SLATE, LAL, [(1, "C")])
    make_roster(roots, SLATE, GSW, [(2, "D")])
    make_roster(roots, SLATE, MIA, [(3, "E")])
    make_roster(roots, SLATE, NYK, [(4, "F")])
    text = _page(early_db, "early", roots=roots)
    assert "Team membership: official roster cache for 2026-10-20, written " in text
    assert "(all 6 teams on this slate)." in text


def test_corrupt_roster_file_is_treated_as_absent_and_said_so(
    early_db: Path, tmp_path: Path
) -> None:
    roots = tmp_path / "rosters"
    (roots / SLATE.isoformat()).mkdir(parents=True)
    (roots / SLATE.isoformat() / f"{BOS}.parquet").write_bytes(b"not a parquet file")
    text = _page(early_db, "early", roots=roots)
    assert f"Unreadable cache file(s) for team {BOS} were treated as absent." in text


# ------------------------------------------------------------------------ the fallback season


def test_fallback_never_places_from_an_earlier_season(early_db: Path) -> None:
    con = open_ro(early_db)
    try:
        # 9000030 has only 2025 and 2024 rows; 9000002 has a 2024 row on MIA and a 2026 row on BOS
        assert S.latest_teams(con, {9000030}, SLATE) == {}
        assert S.latest_teams(con, {9000002}, SLATE) == {9000002: BOS}
    finally:
        con.close()


# --------------------------------------------------------------------------------- report


def test_report_edition_header_and_changes(both_db: Path) -> None:
    text = _page(both_db, "report", now=REPORT_NOW)
    assert (
        "**Report edition**, written 2026-10-20 12:40 CDT after the 13:00 ET injury report." in text
    )
    assert "## What changed since the early edition" in text
    assert "(earliest 2026-10-20 10:44 CDT)" in text
    assert "Games compared: 3; games with no earlier run: 0" in text
    assert (
        "BOS @ DET: home win from 55.7% to 52.0% (-3.7 points). New OUT on the report: "
        "Player 9000011 (DET, left ankle, sprain)."
    ) in text
    assert (
        "Player 9000001 (BOS): points projection 24.5 to 21.5 (-3.0). No reason on the report."
        in text
    )
    assert "Player 9000004 (BOS): P(play) from 40% to 55%. No reason on the report." in text
    assert "Player 9000003 (team not known): no longer forecast (withdrawn" in text


def test_player_ruled_out_after_the_early_run_is_flagged(both_db: Path) -> None:
    text = _page(both_db, "report", now=REPORT_NOW)
    assert (
        "Player 9000011 (DET): now OUT on the 13:00 ET report (left ankle, sprain); "
        "his last stored "
        "forecast was 9.0 points, plays 31%."
    ) in text
    assert "OUT on the report, no forecast shown: Player 9000010 (DET, right knee, sprain)" in text
    assert "- Player 9000011: " not in text


def test_player_ruled_out_with_no_row_in_the_latest_run(tmp_path: Path) -> None:
    """The live path drops a player from later runs once he is OUT: his morning row must not show
    as current, and the report edition must still say he went out."""
    db = make_db(tmp_path / "drop.duckdb", second_run=True, drop_run2_9000011=True)
    text = _page(db, "report", now=REPORT_NOW)
    assert (
        "Player 9000011 (DET): now OUT on the 13:00 ET report (left ankle, sprain); "
        "his last stored "
        "forecast was 9.0 points, plays 62%."
    ) in text
    assert "- Player 9000011: " not in text


def test_report_with_one_run_says_nothing_to_compare(early_db: Path) -> None:
    text = _page(early_db, "report", now=REPORT_NOW)
    assert "Only one forecast run is stored for these games so far" in text


# ----------------------------------------------------------------------------- exit codes


def test_no_rows_for_the_date_writes_a_short_page_and_exits_4(
    early_db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "briefing" / "2026-10-21_early.md"
    code = S.write_slate(early_db, date(2026, 10, 21), "early", out, EARLY_NOW, NO_ROSTER)
    assert code == 4 == S.EXIT_NO_ROWS
    text = out.read_text(encoding="utf-8")
    assert "No forecast rows are stored for the 2026-10-21 slate yet" in text
    assert FOOTER in text


def test_missing_database_exits_1_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "x.md"
    out.write_text("PREVIOUS GOOD PAGE\n", "utf-8")
    assert S.write_slate(tmp_path / "nope.duckdb", SLATE, "early", out, EARLY_NOW, NO_ROSTER) == 1
    assert out.read_text("utf-8") == "PREVIOUS GOOD PAGE\n"
    assert "not found" in capsys.readouterr().err


def test_cli_exit_0_on_rows_and_4_without(early_db: Path, tmp_path: Path) -> None:
    ok = tmp_path / "ok.md"
    assert (
        main(
            [
                "slate",
                "--date",
                "2026-10-20",
                "--db",
                str(early_db),
                "--out",
                str(ok),
                "--edition",
                "early",
            ]
        )
        == 0
    )
    assert ok.exists()
    none = tmp_path / "none.md"
    assert (
        main(
            [
                "slate",
                "--date",
                "2026-10-21",
                "--db",
                str(early_db),
                "--out",
                str(none),
                "--edition",
                "report",
            ]
        )
        == 4
    )
    assert "Report edition" in none.read_text(encoding="utf-8")


def test_database_is_byte_identical_after_a_run(early_db: Path, tmp_path: Path) -> None:
    before = hashlib.sha256(early_db.read_bytes()).hexdigest()
    S.write_slate(early_db, SLATE, "report", tmp_path / "r.md", REPORT_NOW, NO_ROSTER)
    assert hashlib.sha256(early_db.read_bytes()).hexdigest() == before


# ------------------------------------------------------------------- lock and atomic publish


def test_locked_database_exits_1_and_keeps_the_previous_page(
    early_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "2026-10-20_report.md"
    out.write_text("PREVIOUS GOOD PAGE\n", "utf-8")
    monkeypatch.setenv("NBA_DB_LOCK_WAIT_S", "0")  # no real wait: the retry gives up at once
    dbc = importlib.import_module("nba.db.connect")

    def locked(*args: Any, **kwargs: Any) -> Any:
        raise duckdb.IOException(LOCK_MSG)

    monkeypatch.setattr(dbc, "connect", locked)
    code = S.write_slate(early_db, SLATE, "report", out, REPORT_NOW, NO_ROSTER)
    assert code == 1
    assert out.read_text("utf-8") == "PREVIOUS GOOD PAGE\n"
    assert not out.with_name(out.name + ".tmp").exists()
    assert "could not be opened read-only" in capsys.readouterr().err


def test_unreadable_rows_exit_1_and_keep_the_previous_page(
    early_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "2026-10-20_early.md"
    out.write_text("PREVIOUS GOOD PAGE\n", "utf-8")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise duckdb.Error("simulated read failure")

    monkeypatch.setattr(S, "build_briefing", broken)
    assert S.write_slate(early_db, SLATE, "early", out, EARLY_NOW, NO_ROSTER) == 1
    assert out.read_text("utf-8") == "PREVIOUS GOOD PAGE\n"
    assert "previous page" in capsys.readouterr().err


def test_non_database_failure_exits_1_and_keeps_the_previous_page(
    early_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "2026-10-20_early.md"
    out.write_text("PREVIOUS GOOD PAGE\n", "utf-8")

    def odd(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("mean")

    monkeypatch.setattr(S, "build_briefing", odd)
    assert S.write_slate(early_db, SLATE, "early", out, EARLY_NOW, NO_ROSTER) == 1
    assert out.read_text("utf-8") == "PREVIOUS GOOD PAGE\n"
    assert "could not build the page" in capsys.readouterr().err


def test_success_replaces_the_previous_page_atomically(early_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "2026-10-20_early.md"
    out.write_text("OLD\n", "utf-8")
    assert S.write_slate(early_db, SLATE, "early", out, EARLY_NOW, NO_ROSTER) == 0
    assert out.read_text("utf-8").startswith("# Slate briefing, Tuesday 2026-10-20")
    assert not out.with_name(out.name + ".tmp").exists()


def test_no_rows_replaces_the_previous_page_with_the_short_page(
    early_db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "2026-10-21_early.md"
    out.write_text("OLD\n", "utf-8")
    assert S.write_slate(early_db, date(2026, 10, 21), "early", out, EARLY_NOW, NO_ROSTER) == 4
    assert "No forecast rows are stored for the 2026-10-21 slate yet" in out.read_text("utf-8")


# ------------------------------------------------------------------------------- wording


@pytest.mark.parametrize("edition", ["early", "report"])
def test_no_betting_words_on_the_page(both_db: Path, edition: str) -> None:
    text = _page(both_db, edition, now=REPORT_NOW if edition == "report" else EARLY_NOW)
    assert BET_WORDS.search(text) is None, BET_WORDS.search(text)


def test_reason_text_is_plain() -> None:
    assert S._reason("Injury/Illness-RightKnee;Sprain") == "right knee, sprain"
    assert S._reason("gleague-two-way") == "G League two way"
    assert S._reason(None) == "no reason given"
