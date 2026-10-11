"""Claimed facts -> the fact store: game attachment, known_at, provenance, refusals, replay CLI."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.daily.schedule import ScheduledGame, save_schedule_cache
from nba.ingest import reporter_facts as rf
from nba.ingest import reporters as r
from nba.ingest.reporters import Allowlist, Post, Reporter
from tests.ingest.test_reporter_parse import HOU_ROSTER, POSTS

HOU, DAL, MEM = 1610612745, 1610612742, 1610612763
G_A = ScheduledGame("0022600031", datetime(2026, 10, 21, 23, 30), HOU, DAL)  # 19:30 ET
G_B = ScheduledGame("0022600045", datetime(2026, 10, 23, 0, 30), MEM, HOU)
G_OTHER = ScheduledGame("0022600032", datetime(2026, 10, 21, 23, 0), DAL, MEM)  # not HOU
SCHEDULE = [G_B, G_OTHER, G_A]
ALLOW = Allowlist(
    {"rocketsbeat": Reporter("RocketsBeat", "HOU"), "league": Reporter("League", None)}
)
SENGUN, THOMPSON, VANVLEET, ADAMS = 1630578, 1641708, 1627832, 203500


def ctx(
    schedule: list[ScheduledGame] = SCHEDULE,
    rosters: dict[int, list[tuple[str, int]]] | None = None,
    aliases: dict[str, int] | None = None,
) -> rf.Context:
    return rf.Context(
        ALLOW, rosters if rosters is not None else {HOU: HOU_ROSTER}, aliases or {}, schedule
    )


def fixture_posts() -> list[Post]:
    fetched = datetime(2026, 10, 21, 23, 0)
    return [
        Post(
            p["post_id"],
            p["handle"],
            datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(tzinfo=None),
            p["text"],
            fetched,
            r.post_url(p["handle"], p["post_id"]),
        )
        for p in POSTS
    ]


def test_next_game_window() -> None:
    assert rf.next_game(SCHEDULE, HOU, datetime(2026, 10, 21, 15, 0)) == G_A
    assert rf.next_game(SCHEDULE, HOU, datetime(2026, 10, 22, 1, 0)) == G_A  # during the game
    assert rf.next_game(SCHEDULE, HOU, datetime(2026, 10, 22, 3, 0)) == G_B  # after it ended
    assert rf.next_game(SCHEDULE, HOU, datetime(2026, 10, 16, 12, 0)) is None  # > 36 h ahead
    assert rf.next_game(SCHEDULE, HOU, datetime(2026, 10, 21, 15, 0, tzinfo=UTC)) == G_A
    assert (
        rf.next_game(SCHEDULE, DAL, datetime(2026, 10, 21, 15, 0)) == G_OTHER
    )  # earliest DAL game


def test_season_for() -> None:
    assert rf.season_for(datetime(2026, 10, 10)) == "2026-27"
    assert rf.season_for(datetime(2027, 3, 1)) == "2026-27"
    assert rf.season_for(datetime(2027, 8, 1)) == "2027-28"


def test_claims_for_fixture_posts() -> None:
    claimed, counts = rf.claims_for_posts(fixture_posts(), ctx())
    assert counts["retweet_ignored"] == 1  # post 1007
    assert counts["unknown"] == 4  # 1005 sarcasm, 1006 injection, 1011 question, 1014 hedge
    assert counts["unresolved_player"] == 1  # Dillon Brooks is not on the cached roster
    assert counts["claims"] == 11 and counts["no_game_in_window"] == 0
    by_post: dict[str, list[rf.ClaimedFact]] = {}
    for c in claimed:
        by_post.setdefault(c.post.post_id, []).append(c)
    first = by_post["1001"][0]
    assert first.predicate == "claimed_minutes_restriction"
    assert first.claim.player_id == SENGUN and first.game == G_A and first.claim.minutes_limit == 24
    assert first.writable and first.reporter.source == "x:RocketsBeat"
    assert {c.predicate for c in by_post["1015"]} == {
        "claimed_out",
        "claimed_available",
        "claimed_minutes_restriction",
    }
    unresolved = [
        c for c in claimed if c.claim.player_id is None and c.claim.claim_type != "unknown"
    ]
    assert [c.claim.player_name for c in unresolved] == ["Dillon Brooks"]
    assert not any(c.writable for c in claimed if c.claim.claim_type == "unknown")


def test_league_wide_handle_uses_the_players_roster_team() -> None:
    post = Post(
        "9",
        "League",
        datetime(2026, 10, 21, 15, 0),
        "Alperen Sengun is OUT tonight.",
        datetime(2026, 10, 21, 16, 0),
        "u",
    )
    claimed, counts = rf.claims_for_posts([post], ctx(aliases={"alperen sengun": SENGUN}))
    assert counts["claims"] == 1 and claimed[0].team_id == HOU and claimed[0].game == G_A


def test_handle_off_the_list_is_refused_on_replay_too() -> None:
    post = Post(
        "9",
        "Imposter",
        datetime(2026, 10, 21, 15, 0),
        "Alperen Sengun is OUT tonight.",
        datetime(2026, 10, 21, 16, 0),
        "u",
    )
    with pytest.raises(r.HandleNotAllowed):
        rf.claims_for_posts([post], ctx())


def test_write_facts_known_at_game_and_provenance(tmp_path: Path) -> None:
    con = rf.open_facts_db(tmp_path / "facts.duckdb")
    claimed, _ = rf.claims_for_posts(fixture_posts(), ctx())
    res = rf.write_claimed_facts(con, claimed, ctx=ctx())
    assert (res.written, res.duplicates) == (11, 0)
    rows = con.execute(
        "SELECT subject, predicate, object, value, value_text, known_at, valid_from, valid_to, "
        "source, confidence FROM facts WHERE value_text LIKE 'Udoka says Alperen Sengun is on%'"
    ).fetchall()
    assert rows == [
        (
            f"player:{SENGUN}",
            "claimed_minutes_restriction",
            f"game:{G_A.game_id}",
            24.0,
            "Udoka says Alperen Sengun is on a 24-minute restriction tonight vs.",
            datetime(2026, 10, 21, 15, 2),  # = the post's created_at, naive UTC
            None,
            None,
            "x:RocketsBeat",
            0.8,
        )
    ]
    preds = dict(
        con.execute("SELECT predicate, count(*) FROM facts GROUP BY 1 ORDER BY 1").fetchall()
    )
    assert preds == {
        "claimed_available": 2,
        "claimed_game_time_decision": 1,
        "claimed_minutes_restriction": 4,
        "claimed_out": 3,
        "claimed_will_play": 1,
    }
    assert con.execute(
        "SELECT count(*) FROM facts WHERE predicate = 'claimed_unknown'"
    ).fetchone() == (0,)
    assert con.execute("SELECT max(confidence) FROM facts").fetchone() == (
        0.8,
    )  # claims, never 1.0
    ents = con.execute("SELECT entity_id, kind, canonical_name FROM entities ORDER BY 1").fetchall()
    assert (f"player:{SENGUN}", "player", "Alperen Sengun") in ents and res.entities_added == len(
        ents
    )
    # replay of the same raw posts is idempotent
    again = rf.write_claimed_facts(con, claimed, ctx=ctx())
    assert (again.written, again.duplicates, again.entities_added) == (0, 11, 0)
    assert con.execute("SELECT count(*) FROM facts").fetchone() == (11,)
    ids = [x[0] for x in con.execute("SELECT fact_id FROM facts ORDER BY fact_id").fetchall()]
    assert ids == list(range(1, 12))
    con.close()


def test_writer_refuses_a_database_with_model_tables(tmp_path: Path) -> None:
    p = tmp_path / "nba.duckdb"
    c = duckdb.connect(str(p))
    c.execute("CREATE TABLE games (game_id VARCHAR)")
    c.execute("CREATE TABLE prop_predictions (x INTEGER)")
    c.close()
    with pytest.raises(rf.FactsDbError, match="games"):
        rf.open_facts_db(p)
    con = duckdb.connect(str(p), read_only=True)
    assert {t[0] for t in con.execute("SHOW TABLES").fetchall()} == {"games", "prop_predictions"}
    con.close()


def test_writer_sql_names_only_facts_tables() -> None:
    src = Path(rf.__file__).read_text()
    tables = set(re.findall(r"\b(?:INTO|FROM|UPDATE|JOIN)\s+([a-z_]+)", src))
    assert tables <= {"facts", "entities"}, tables


def test_as_of_sees_claims_before_tip_only(tmp_path: Path) -> None:
    from nba.facts.query import PostTipError, as_of

    con = rf.open_facts_db(tmp_path / "facts.duckdb")
    con.execute(
        "INSERT INTO facts VALUES (1, ?, 'tips_at', NULL, NULL, ?, ?, ?, NULL, 'schedule', 1.0)",
        [f"game:{G_A.game_id}", G_A.tipoff.isoformat(), G_A.tipoff - timedelta(days=1), G_A.tipoff],
    )
    claimed, _ = rf.claims_for_posts(fixture_posts(), ctx())
    rf.write_claimed_facts(con, claimed, ctx=ctx())
    t60 = as_of(con, G_A.game_id, G_A.tipoff - timedelta(minutes=60))
    claims = t60.filter(pl.col("predicate").str.starts_with("claimed_"))
    assert claims.height == 6  # posts up to 22:30 UTC; the 22:31-22:45 posts are after T-60
    assert claims["known_at"].max() == datetime(2026, 10, 21, 19, 20)  # 19:25 is unresolved
    with pytest.raises(PostTipError):
        as_of(con, G_A.game_id, G_A.tipoff + timedelta(minutes=1))
    con.close()


def test_parse_only_cli_replays_raw_into_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw.parquet"
    r.posts_frame(fixture_posts()).write_parquet(raw)
    allow = tmp_path / "reporters.yaml"
    allow.write_text(json.dumps({"handles": [{"handle": "RocketsBeat", "team": "HOU"}]}))
    rosters = tmp_path / "rosters" / "2026-10-10"
    rosters.mkdir(parents=True)
    pl.DataFrame(
        {
            "team_id": [HOU] * len(HOU_ROSTER),
            "player_id": [p for _, p in HOU_ROSTER],
            "exp": ["3"] * len(HOU_ROSTER),
            "player_name": [n for n, _ in HOU_ROSTER],
        }
    ).write_parquet(rosters / f"{HOU}.parquet")
    cache = tmp_path / "schedule_cache"
    save_schedule_cache(SCHEDULE, rf.season_for(datetime.now(UTC)), cache)
    db = tmp_path / "facts.duckdb"
    rc = r.main(
        [
            "parse-only",
            "--file",
            str(raw),
            "--allowlist",
            str(allow),
            "--rosters",
            str(tmp_path / "rosters"),
            "--schedule-cache",
            str(cache),
            "--aliases",
            str(tmp_path / "none.yaml"),
            "--facts-db",
            str(db),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "facts written: 11" in out and "UNRESOLVED 'Dillon Brooks'" in out
    assert "retweets ignored: 1" in out and "unknown: 4" in out
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM facts WHERE source = 'x:RocketsBeat'").fetchone() == (
        11,
    )
    con.close()
    # an imposter in the raw file is refused on replay, exit 2, nothing more written
    bad = tmp_path / "bad.parquet"
    imp = Post(
        "7",
        "Imposter",
        datetime(2026, 10, 21, 15, 0),
        "Alperen Sengun is OUT.",
        datetime(2026, 10, 21, 16, 0),
        "u",
    )
    r.posts_frame([imp]).write_parquet(bad)
    rc = r.main(
        [
            "parse-only",
            "--file",
            str(bad),
            "--allowlist",
            str(allow),
            "--facts-db",
            str(db),
            "--rosters",
            str(tmp_path / "rosters"),
            "--schedule-cache",
            str(cache),
            "--aliases",
            str(tmp_path / "none.yaml"),
        ]
    )
    assert rc == 2 and "not in configs/reporters.yaml" in capsys.readouterr().err


def test_load_roster_index_takes_newest_day(tmp_path: Path) -> None:
    for day, name in (("2026-10-09", "Old Name"), ("2026-10-10", "New Name")):
        d = tmp_path / day
        d.mkdir()
        pl.DataFrame(
            {"team_id": [HOU], "player_id": [1], "exp": ["R"], "player_name": [name]}
        ).write_parquet(d / "x.parquet")
    assert rf.load_roster_index(tmp_path) == {HOU: [("New Name", 1)]}
    assert rf.load_roster_index(tmp_path / "missing") == {}
