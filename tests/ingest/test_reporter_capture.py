"""The four capture fixes for REPORTER_FEED_LIVE, on recorded fixtures only (no network, no token).

1. known_at is our first read of a post, not its created_at.
2. reporter facts live in their own write-once store and survive ``nba.facts build``.
3. a page cut short never advances the cursor; the lookback reaches the morning shootaround.
4. a poll lands inside [T-90, T-60] of every team-game; coverage is measured, not assumed.
Plus the ``tick`` job and the ``counts`` subcommand (dry run, budget guard).
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from nba.daily.schedule import ScheduledGame
from nba.facts.build import build, reporter_facts_frame
from nba.facts.query import as_of
from nba.ingest import reporter_counts as rcount
from nba.ingest import reporter_facts as rf
from nba.ingest import reporter_store as rs
from nba.ingest import reporter_tick as rt
from nba.ingest import reporters as r
from nba.ingest.reporters import Allowlist, Post, Reporter
from tests.facts.conftest import G1, TIP1, make_lineups, make_source
from tests.ingest.test_reporter_parse import HOU_ROSTER
from tests.ingest.test_reporters import ENV, TOKEN, Server, allowlist_yaml, client, tweet

HOU, DAL = 1610612745, 1610612742
PREV = ScheduledGame("0022600011", datetime(2026, 10, 27, 0, 30), DAL, HOU)  # Oct 26 20:30 ET
TONIGHT = ScheduledGame("0022600031", datetime(2026, 10, 28, 23, 30), HOU, DAL)  # Wed 19:30 ET
TIP = TONIGHT.tipoff
TIPA = TIP.replace(tzinfo=UTC)
ALLOW = Allowlist(
    {
        "rocketsbeat": Reporter("RocketsBeat", "HOU"),
        "mavsbeat": Reporter("MavsBeat", "DAL"),
        "league": Reporter("League", None),
    }
)
CAP_TEXT = "Alperen Sengun will be on a 24-minute restriction tonight."


def ctx(schedule: list[ScheduledGame] | None = None) -> rf.Context:
    return rf.Context(ALLOW, {HOU: HOU_ROSTER}, {}, schedule or [PREV, TONIGHT])


def post(pid: str, created: datetime, fetched: datetime, text: str = CAP_TEXT) -> Post:
    return Post(pid, "RocketsBeat", created, text, fetched, r.post_url("RocketsBeat", pid))


def live(posts: list[Post]) -> dict[str, tuple[datetime, str]]:
    """The raw-store index as if every post had been read live at its fetched_at."""
    return {p.post_id: (p.fetched_at, "live") for p in posts}


def derive(tmp_path: Path, posts: list[Post], name: str = "s", *, raw: str = "raw") -> Path:
    """Write ``posts`` to the immutable raw store, then derive facts from it into a store."""
    raw_dir = tmp_path / raw
    for fetched in sorted({p.fetched_at for p in posts}):
        r.write_raw(raw_dir, "RocketsBeat", [p for p in posts if p.fetched_at == fetched], fetched)
    db = tmp_path / f"{name}.duckdb"
    con = rs.open_store(db)
    try:
        rt.derive_facts(con, raw_dir, max(p.fetched_at for p in posts).replace(tzinfo=UTC), ctx())
    finally:
        con.close()
    return db


def rows(db: Path, sql: str = "SELECT * FROM reporter_facts ORDER BY fact_id") -> list[Any]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ------------------------------------------------------------------ 1. known_at = first_seen_at


def test_known_at_is_our_first_read_and_created_at_is_a_separate_column(tmp_path: Path) -> None:
    created, seen = datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 22, 45)
    db = derive(tmp_path, [post("9001", created, seen)])
    got = rows(
        db,
        "SELECT predicate, value, known_at, created_at, first_seen_at, capture_mode, source "
        "FROM reporter_facts WHERE predicate LIKE 'reporter_%'",
    )
    assert got == [("reporter_minutes_cap", 24.0, seen, created, seen, "live", "x:RocketsBeat")]
    claimed = rows(
        db, "SELECT known_at, created_at FROM reporter_facts WHERE predicate LIKE 'claimed_%'"
    )
    assert claimed == [(seen, created)]


def test_a_post_written_before_t60_but_read_after_is_not_t60_eligible(tmp_path: Path) -> None:
    created, late = datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 22, 45)  # T-45
    db = derive(tmp_path, [post("9001", created, late)])
    t60 = TIP - timedelta(minutes=60)
    q = "SELECT count(*) FROM reporter_facts WHERE known_at <= ?"
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute(q, [t60]).fetchone() == (0,)
    assert con.execute(q, [TIP]).fetchone() == (2,)  # the claim and the briefing's claimed_ copy
    con.close()


def test_shifting_every_first_seen_by_24h_empties_the_t60_set(tmp_path: Path) -> None:
    created = datetime(2026, 10, 28, 15, 0)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    early = derive(tmp_path / "a", [post("9001", created, datetime(2026, 10, 28, 21, 0))])
    shifted = derive(tmp_path / "b", [post("9001", created, datetime(2026, 10, 29, 21, 0))])
    t60 = TIP - timedelta(minutes=60)
    q = "SELECT count(*) FROM reporter_facts WHERE predicate LIKE 'reporter_%' AND known_at <= ?"
    for db, n in ((early, 1), (shifted, 0)):
        con = duckdb.connect(str(db), read_only=True)
        assert con.execute(q, [t60]).fetchone() == (n,)
        con.close()


def test_replay_of_the_same_raw_files_gives_identical_facts(tmp_path: Path) -> None:
    posts = [
        post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0)),
        post(
            "9002",
            datetime(2026, 10, 28, 16, 0),
            datetime(2026, 10, 28, 21, 0),
            "Alperen Sengun is out tonight.",
        ),
    ]
    a = derive(tmp_path, posts, "a")
    raw_dir = tmp_path / "raw"
    con = rs.open_store(tmp_path / "b.duckdb")
    rt.derive_facts(con, raw_dir, datetime(2026, 10, 28, 22, 0, tzinfo=UTC), ctx())
    rt.derive_facts(con, raw_dir, datetime(2026, 10, 28, 23, 0, tzinfo=UTC), ctx())  # idempotent
    con.close()
    sel = (
        "SELECT subject, predicate, object, value, value_text, known_at, source, post_id "
        "FROM reporter_facts ORDER BY 1, 2, 8"
    )
    assert rows(a, sel) == rows(tmp_path / "b.duckdb", sel)
    digest = lambda db: hashlib.sha256(repr(rows(db, sel)).encode()).hexdigest()  # noqa: E731
    assert digest(a) == digest(tmp_path / "b.duckdb")


def test_a_backfill_raw_file_creates_no_fact(tmp_path: Path) -> None:
    p = post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0))
    r.write_raw(tmp_path / "raw_backfill", p.handle, [p], p.fetched_at)
    seen = rs.first_seen_index([tmp_path / "raw", tmp_path / "raw_backfill"])
    assert seen["9001"][1] == "backfill"
    facts, _, counts = rf.pretip_facts_for_posts([p], ctx(), seen)
    assert facts == [] and counts["backfill_ineligible"] == 1
    assert rf.claimed_store_facts([], seen) == []
    # a post read live first keeps its live stamp even if a backfill pulled it later
    r.write_raw(tmp_path / "raw", p.handle, [p], p.fetched_at)
    later = Post(p.post_id, p.handle, p.created_at, p.text, p.fetched_at + timedelta(days=9), p.url)
    r.write_raw(tmp_path / "raw_backfill", p.handle, [later], later.fetched_at)
    seen = rs.first_seen_index([tmp_path / "raw", tmp_path / "raw_backfill"])
    assert seen["9001"] == (p.fetched_at, "live")


def test_first_seen_is_the_earliest_raw_file_holding_the_post(tmp_path: Path) -> None:
    created = datetime(2026, 10, 28, 15, 0)
    first = post("9001", created, datetime(2026, 10, 28, 21, 0))
    again = post("9001", created, datetime(2026, 10, 28, 22, 0))
    r.write_raw(tmp_path / "raw", "RocketsBeat", [again], again.fetched_at)
    r.write_raw(tmp_path / "raw", "RocketsBeat", [first], first.fetched_at)
    assert rs.first_seen_index([tmp_path / "raw"])["9001"][0] == first.fetched_at
    recent = rt.recent_raw_posts(tmp_path / "raw", datetime(2026, 10, 28, 23, 0, tzinfo=UTC))
    assert [p.fetched_at for p in recent] == [first.fetched_at]


def test_clock_fault_post_is_dropped_and_logged(tmp_path: Path) -> None:
    p = post("9001", datetime(2026, 10, 28, 21, 5), datetime(2026, 10, 28, 21, 0))  # created > read
    facts, rejects, counts = rf.pretip_facts_for_posts([p], ctx(), live([p]))
    assert facts == [] and counts["clock_fault"] == 1
    assert rejects[0][:3] == ("9001", "RocketsBeat", "clock_fault")


def test_post_written_during_or_just_after_the_previous_game_is_not_tonights() -> None:
    # PREV tipped Oct 27 00:30Z, its window ends 03:30Z; the next game tips Oct 27 23:30Z.
    nxt = ScheduledGame("0022600021", datetime(2026, 10, 27, 23, 30), HOU, DAL)
    text = "Alperen Sengun will be on a 24-minute restriction against Denver."
    during = post("9001", datetime(2026, 10, 27, 2, 0), datetime(2026, 10, 27, 2, 1), text)
    facts, _, counts = rf.pretip_facts_for_posts([during], ctx([PREV, nxt]), live([during]))
    assert facts == [] and counts["drop_f4_prev_game"] == 1
    # after the window, with no day word, the postgame rule (round 3) still drops it ...
    after = post("9002", datetime(2026, 10, 27, 3, 31), datetime(2026, 10, 27, 3, 32), text)
    facts, _, counts = rf.pretip_facts_for_posts([after], ctx([PREV, nxt]), live([after]))
    assert facts == [] and counts["drop_f3_postgame_no_day"] == 1
    # ... unless it names the next game's day; and from 06:00 ET on nothing is postgame any more
    named = post(
        "9003",
        datetime(2026, 10, 27, 3, 31),
        datetime(2026, 10, 27, 3, 32),
        "Alperen Sengun will be on a 24-minute restriction against Denver tomorrow.",
    )
    facts, _, _ = rf.pretip_facts_for_posts([named], ctx([PREV, nxt]), live([named]))
    assert [f.object for f in facts] == [f"game:{nxt.game_id}"]
    assert json.loads(facts[0].value_text or "{}")["day_ref"] == "tomorrow"
    morning = post("9004", datetime(2026, 10, 27, 10, 5), datetime(2026, 10, 27, 10, 6), text)
    facts, _, _ = rf.pretip_facts_for_posts([morning], ctx([PREV, nxt]), live([morning]))
    assert [f.object for f in facts] == [f"game:{nxt.game_id}"]


def test_unresolved_unknown_game_and_hedged_counts(tmp_path: Path) -> None:
    hedged = post(
        "9003",
        datetime(2026, 10, 28, 15, 0),
        datetime(2026, 10, 28, 15, 1),
        "Alperen Sengun is expected to be on a 24-minute restriction tonight, if he plays.",
    )
    nobody = post(
        "9004",
        datetime(2026, 10, 28, 15, 0),
        datetime(2026, 10, 28, 15, 1),
        "Zzyzx Nonplayer will be on a 24-minute restriction tonight.",
    )
    facts, _, counts = rf.pretip_facts_for_posts([hedged, nobody], ctx(), live([hedged, nobody]))
    assert counts["hedged"] == 1 and counts["unresolved_player"] == 1
    assert json.loads(facts[0].value_text or "{}")["hedged"] is True and facts[0].confidence < 0.8


def test_store_is_write_once_and_has_no_update_or_delete_path(tmp_path: Path) -> None:
    con = rs.open_store(tmp_path / "s.duckdb")
    t = datetime(2026, 10, 28, 21, 0)
    base = dict(
        subject="player:1",
        predicate="reporter_status",
        object="game:g",
        value=None,
        known_at=t,
        source="x:A",
        confidence=0.7,
        post_id="p",
        created_at=t,
        first_seen_at=t,
        capture_mode="live",
        parser_version="v",
    )
    assert rs.insert_facts(con, [rs.StoreFact(value_text="first", **base)]) == (1, 0)
    assert rs.insert_facts(con, [rs.StoreFact(value_text="second", **base)]) == (0, 1)
    assert con.execute("SELECT value_text FROM reporter_facts").fetchall() == [("first",)]
    con.close()
    src = Path(rs.__file__).read_text().upper()
    for word in ("UPDATE ", "DELETE ", "DROP ", "TRUNCATE", "INSERT OR REPLACE"):
        assert word not in src, word


# --------------------------------------------------------- 2. survive `nba.facts build`


def _store_with_facts(tmp_path: Path) -> Path:
    t = datetime(2026, 10, 28, 21, 0)
    db = tmp_path / "reporter_facts.duckdb"
    con = rs.open_store(db)
    mk = lambda gid, pid: rs.StoreFact(  # noqa: E731
        "player:2",
        "reporter_minutes_cap",
        f"game:{gid}",
        24.0,
        "{}",
        t,
        "x:A",
        0.8,
        pid,
        t,
        t,
        "live",
        "pretip-v1",
    )
    # G1 is a loaded 2022 game; the other is the 2025 holdout, which a build never carries
    rs.insert_facts(con, [mk(G1, "p1"), mk("0022500001", "p2")])
    con.close()
    return db


def test_reporter_facts_survive_a_facts_rebuild_and_the_store_is_untouched(tmp_path: Path) -> None:
    store = _store_with_facts(tmp_path)
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    out = tmp_path / "facts.duckdb"
    kw = dict(
        odds_aliases=tmp_path / "n1.yaml",
        kalshi_aliases=tmp_path / "n2.yaml",
        schedule_dir=_sched(tmp_path),
        reporter_store=store,
    )
    for _ in range(2):  # the build drops and recreates `out`; the facts come back each time
        counts = build(make_source(), make_lineups(), out, **kw)  # type: ignore[arg-type]
        con = duckdb.connect(str(out), read_only=True)
        got = con.execute(
            "SELECT subject, predicate, object, known_at FROM facts "
            "WHERE predicate LIKE 'reporter_%'"
        ).fetchall()
        con.close()
        assert got == [
            ("player:2", "reporter_minutes_cap", f"game:{G1}", datetime(2026, 10, 28, 21, 0))
        ]
        assert any("1 facts read in" in n for n in counts["notes"])
    assert hashlib.sha256(store.read_bytes()).hexdigest() == before  # never written by a build
    out.unlink()
    build(make_source(), make_lineups(), out, **{**kw, "reporter_store": None})  # type: ignore[arg-type]
    con = duckdb.connect(str(out), read_only=True)
    assert con.execute(
        "SELECT count(*) FROM facts WHERE predicate LIKE 'reporter_%'"
    ).fetchone() == (0,)
    con.close()
    assert len(rows(store)) == 2  # the store still holds both


def _sched(tmp_path: Path) -> Path:
    import polars as pl

    d = tmp_path / "schedule"
    d.mkdir(exist_ok=True)
    pl.DataFrame(
        {
            "gameId": [G1],
            "gameDateTimeUTC": ["2022-10-19T23:30:00Z"],
            "gameDateTimeEst": ["2022-10-19T19:30:00Z"],
        }
    ).write_parquet(d / "raw_2022-23.parquet")
    return d


def test_missing_or_locked_reporter_store_never_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame, note = reporter_facts_frame(tmp_path / "absent.duckdb", 2024, ())
    assert frame.is_empty() and note is None
    store = _store_with_facts(tmp_path)
    real = duckdb.connect
    monkeypatch.setattr("nba.facts.build.time.sleep", lambda s: None)

    def locked(path: str, *a: Any, **k: Any) -> Any:
        if str(path) == str(store):
            raise duckdb.IOException("Could not set lock on file")
        return real(path, *a, **k)

    monkeypatch.setattr("nba.facts.build.duckdb.connect", locked)
    frame, note = reporter_facts_frame(store, 2024, ())
    assert frame.is_empty() and note is not None and "not dropped" in note
    monkeypatch.undo()
    assert len(rows(store)) == 2


def test_as_of_returns_reporter_facts_only_after_our_first_read(tmp_path: Path) -> None:
    store = _store_with_facts(tmp_path)
    out = tmp_path / "facts.duckdb"
    build(
        make_source(),
        make_lineups(),
        out,
        odds_aliases=tmp_path / "n1.yaml",
        kalshi_aliases=tmp_path / "n2.yaml",
        schedule_dir=_sched(tmp_path),
        reporter_store=store,
    )
    con = duckdb.connect(str(out), read_only=True)
    # the fact was first read 2026-10-28, long after this 2022 game: not known at T-60
    df = as_of(con, G1, TIP1 - timedelta(minutes=60))
    assert df.filter(df["predicate"].str.starts_with("reporter_")).is_empty()
    df = as_of(con, G1, TIP1 + timedelta(minutes=1), allow_post_tip=True)
    assert df.filter(df["predicate"].str.starts_with("reporter_")).is_empty()  # known_at 2026
    con.close()


# --------------------------------------------- 3. cursor, pagination, shootaround lookback


def paged(posts: list[dict[str, Any]], size: int) -> Any:
    """A fake timeline that honours max_results and serves pages with next_token."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.startswith("/2/users/by/username/"):
            return httpx.Response(200, json={"data": {"id": "42", "username": "x"}})
        n = min(size, int(req.url.params.get("max_results", "100")))
        start = int(req.url.params.get("pagination_token", "0"))
        chunk = posts[start : start + n]
        meta: dict[str, Any] = {"result_count": len(chunk)}
        if start + n < len(posts):
            meta["next_token"] = str(start + n)
        return httpx.Response(200, json={"data": chunk, "meta": meta})

    handler.requests = seen  # type: ignore[attr-defined]
    return handler


class Paged(Server):
    def __init__(self, posts: list[dict[str, Any]], size: int) -> None:
        super().__init__(posts)
        self.handler = paged(posts, size)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        return self.handler(req)  # type: ignore[no-any-return]


NOW = datetime(2026, 10, 28, 17, 30, tzinfo=UTC)  # 13:30 ET, the sweep


def many(n: int, created: str = "2026-10-28T14:41:00.000Z") -> list[dict[str, Any]]:
    return [tweet(str(5000 - i), f"chatter {i}", created) for i in range(n)]


def test_pagination_follows_next_token_to_the_start_and_then_advances_the_cursor(
    tmp_path: Path,
) -> None:
    srv = Paged(many(12), size=5)
    c = client(tmp_path, srv, clock=lambda: NOW)
    res = c.poll()
    assert len(res.posts) == 12 and res.outcomes[0].status == "ok" and res.outcomes[0].pages == 3
    tweets = [q for q in srv.requests if q.url.path.endswith("/tweets")]
    assert [q.url.params.get("pagination_token") for q in tweets] == [None, "5", "10"]
    st = json.loads((tmp_path / "data" / "state.json").read_text())
    assert st["rocketsbeat"]["last_ok_poll_at"] == "2026-10-28T17:30:00"


def test_a_page_cut_short_by_the_budget_does_not_advance_the_cursor(tmp_path: Path) -> None:
    srv = Paged(many(12), size=5)
    c = client(tmp_path, srv, clock=lambda: NOW, budget_posts=7)
    res = c.poll()
    out = res.outcomes[0]
    assert out.status == "short:budget" and len(out.posts) == 5 and res.stopped
    assert not (tmp_path / "data" / "state.json").exists() or "last_ok_poll_at" not in json.loads(
        (tmp_path / "data" / "state.json").read_text()
    ).get("rocketsbeat", {})
    # the next run, with budget, starts from the SAME lower bound and reads the whole gap
    c2 = client(tmp_path, srv, clock=lambda: NOW + timedelta(minutes=30))
    res2 = c2.poll()
    assert res2.outcomes[0].status == "ok" and len(res2.outcomes[0].posts) == 12
    starts = [q.url.params["start_time"] for q in srv.requests if q.url.path.endswith("/tweets")]
    assert starts[0] == starts[-1] == "2026-10-28T10:00:00Z"  # 06:00 ET, never moved


def test_runaway_timeline_stops_at_max_pages_as_short(tmp_path: Path) -> None:
    srv = Paged(many(60), size=5)
    c = client(tmp_path, srv, clock=lambda: NOW)
    out = c.poll().outcomes[0]
    assert out.status == "short:max_pages" and out.pages == r.MAX_PAGES


def test_morning_shootaround_post_is_inside_the_first_polls_window(tmp_path: Path) -> None:
    first_poll = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)  # T-90 of a 19:30 ET game
    srv = Paged(many(2), size=100)
    c = client(tmp_path, srv, clock=lambda: first_poll)
    c.poll()
    req = [q for q in srv.requests if q.url.path.endswith("/tweets")][0]
    start = datetime.fromisoformat(req.url.params["start_time"].replace("Z", "+00:00"))
    shootaround = datetime(2026, 10, 28, 15, 41, tzinfo=UTC)  # 11:41 ET, 6 h 19 min before
    assert start == datetime(2026, 10, 28, 10, 0, tzinfo=UTC) and start <= shootaround
    # the old 6 h lookback started at 16:00Z, after this post: it was never read
    assert first_poll - timedelta(hours=6) > shootaround


def test_cursor_is_the_last_ok_poll_minus_overlap_capped_by_max_lookback(tmp_path: Path) -> None:
    c = client(tmp_path, Paged([], 5), clock=lambda: NOW)
    c.state.update(
        "RocketsBeat", last_ok_poll_at=(NOW - timedelta(hours=3)).replace(tzinfo=None).isoformat()
    )
    assert c.poll_start("RocketsBeat", NOW) == NOW - timedelta(hours=3, seconds=r.CURSOR_OVERLAP_S)
    c.state.update(
        "RocketsBeat", last_ok_poll_at=(NOW - timedelta(days=5)).replace(tzinfo=None).isoformat()
    )
    assert c.poll_start("RocketsBeat", NOW) == NOW - timedelta(hours=c.allowlist.max_lookback_hours)
    assert c.poll_start("RocketsBeat", NOW, lookback_hours=2) == NOW - timedelta(hours=2)


def test_anchor_before_6am_et_is_the_previous_days_6am() -> None:
    assert r.anchor_et(datetime(2026, 10, 28, 8, 0, tzinfo=UTC)) == datetime(
        2026, 10, 27, 10, 0, tzinfo=UTC
    )
    assert r.anchor_et(datetime(2026, 10, 28, 12, 0, tzinfo=UTC)) == datetime(
        2026, 10, 28, 10, 0, tzinfo=UTC
    )


# ---------------------------------------------------------- 4. cadence and coverage


def simulate(handle_ok: Any = None, step: int = 5) -> dict[str, list[datetime]]:
    """Run plan_due every 5 minutes through the game day, every due poll ending ok."""
    polls: list[rs.PollRecord] = []
    when: dict[str, list[datetime]] = {}
    t = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    while t < TIPA + timedelta(minutes=10):
        for d in rt.plan_due(ALLOW, [TONIGHT], t, polls):
            ok = handle_ok is None or handle_ok(d, t)
            polls.append(
                rs.PollRecord(
                    d.label,
                    d.handle,
                    d.team_id,
                    t.replace(tzinfo=None),
                    t.replace(tzinfo=None),
                    "ok" if ok else "short:budget",
                )
            )
            when.setdefault(d.handle, []).append(t)
        t += timedelta(minutes=step)
    return when


def test_each_team_game_gets_a_poll_inside_t90_t60_and_the_planned_ones() -> None:
    when = simulate()
    expect = [
        datetime(2026, 10, 28, 17, 30, tzinfo=UTC),  # 13:30 ET sweep
        TIPA - timedelta(minutes=90),
        TIPA - timedelta(minutes=70),
        TIPA - timedelta(minutes=40),
    ]
    assert when["RocketsBeat"] == expect and when["MavsBeat"] == expect
    lo, hi = TIPA - timedelta(minutes=90), TIPA - timedelta(minutes=60)
    in_window = [t for t in when["RocketsBeat"] if lo <= t <= hi]
    assert len(in_window) == 2  # T-90 and the new T-70
    # the league-wide handle follows the same slate times
    assert when["League"] == expect


def test_after_a_sleep_only_the_latest_due_poll_runs() -> None:
    now = TIPA - timedelta(minutes=65)  # the Mac woke up here; T-90 and T-70 were both missed
    due = rt.plan_due(ALLOW, [TONIGHT], now, [])
    assert {d.label for d in due} == {"T-70"} and len(due) == 3
    assert (
        rt.plan_due(ALLOW, [TONIGHT], TIPA + timedelta(minutes=1), []) == []
    )  # after tip: nothing


def test_a_poll_that_ends_short_is_retried_up_to_three_times() -> None:
    fails = simulate(handle_ok=lambda d, t: False)
    assert all(len(v) <= rt.MAX_TRIES * 4 for v in fails.values())
    t_70 = [
        t
        for t in fails["RocketsBeat"]
        if TIPA - timedelta(minutes=70) <= t < TIPA - timedelta(minutes=40)
    ]
    assert len(t_70) == rt.MAX_TRIES


def test_no_games_today_means_nothing_is_due() -> None:
    assert rt.plan_due(ALLOW, [TONIGHT], datetime(2026, 10, 29, 20, 0, tzinfo=UTC), []) == []


def poll(handle: str, minutes_before: int, status: str = "ok") -> rs.PollRecord:
    t = TIP - timedelta(minutes=minutes_before)
    return rs.PollRecord("T", handle, None, t, t, status)


@pytest.mark.parametrize(
    ("polls", "covered"),
    [
        ([poll("RocketsBeat", 90)], True),  # the window is inclusive at T-90 ...
        ([poll("RocketsBeat", 60)], True),  # ... and at T-60
        ([poll("RocketsBeat", 91)], False),
        ([poll("RocketsBeat", 59)], False),
        ([poll("RocketsBeat", 120), poll("RocketsBeat", 40)], False),
        ([poll("RocketsBeat", 75, "short:budget")], False),  # a short page is not ok
        ([poll("RocketsBeat", 75, "error:RateLimited")], False),
        ([poll("RocketsBeat", 75, "refused:budget")], False),
        ([poll("MavsBeat", 75)], False),  # someone else's handle
        ([], False),
    ],
)
def test_coverage_needs_an_ok_poll_inside_the_window(
    polls: list[rs.PollRecord], covered: bool
) -> None:
    cov = rs.compute_coverage("g", HOU, TIP, rt.required_handles(ALLOW, HOU), polls)
    assert cov.covered is covered and cov.required == ("RocketsBeat",)


def test_every_required_handle_must_be_ok_and_league_wide_covers_teamless_games() -> None:
    allow = Allowlist(
        {
            "a": Reporter("A", "HOU"),
            "b": Reporter("B", "HOU"),
            "league": Reporter("League", None),
        }
    )
    two = [poll("A", 75)]
    assert not rs.compute_coverage("g", HOU, TIP, rt.required_handles(allow, HOU), two).covered
    assert rs.compute_coverage(
        "g", HOU, TIP, rt.required_handles(allow, HOU), [poll("A", 75), poll("B", 70)]
    ).covered
    assert rt.required_handles(allow, DAL) == ("League",)  # no own handle: the league-wide one
    assert rs.compute_coverage("g", DAL, TIP, ("League",), [poll("League", 80)]).covered
    assert rt.required_handles(Allowlist({"a": Reporter("A", "HOU")}), DAL) == ()  # nobody
    assert not rs.compute_coverage("g", DAL, TIP, (), []).covered


# --------------------------------------------------------------- the tick, end to end


def tick_env(tmp_path: Path, posts: list[dict[str, Any]], clock: list[datetime], **kw: Any) -> Any:
    allow = allowlist_yaml(tmp_path, [{"handle": "RocketsBeat", "team": "HOU"}])
    srv = Paged(posts, size=100)
    c = client(tmp_path, srv, allow=allow, clock=lambda: clock[0], **kw)
    store = tmp_path / "facts" / "reporter_facts.duckdb"

    def run(now: datetime) -> rt.TickResult:
        clock[0] = now
        return rt.run_tick(
            now=now,
            allow=c.allowlist,
            games=[PREV, TONIGHT],
            make_client=lambda: c,
            make_ctx=lambda: rf.Context(c.allowlist, {HOU: HOU_ROSTER}, {}, [PREV, TONIGHT]),
            store_path=store,
            data_dir=tmp_path / "data",
            ops_dir=tmp_path / "ops",
            alerts_path=tmp_path / "ops" / "ALERTS.md",
        )

    return run, store, srv


def test_tick_polls_logs_derives_facts_and_finalises_coverage(tmp_path: Path) -> None:
    posts = [tweet("7001", CAP_TEXT, "2026-10-28T15:00:00.000Z")]
    clock = [NOW]
    run, store, srv = tick_env(tmp_path, posts, clock)
    for minutes in (90, 70):
        res = run(TIP - timedelta(minutes=minutes))
        assert res.rc == 0 and len(res.due) == 1, res.detail
    res = run(TIP - timedelta(minutes=55))
    assert res.rc == 0 and res.due == [] and res.coverage_rows == 2
    logged = rows(store, "SELECT label, handle, status FROM poll_log ORDER BY poll_id")
    assert [x[0] for x in logged] == ["T-90", "T-70"]
    assert {x[2] for x in logged} == {"ok"}
    cov = rows(
        store,
        "SELECT team_id, covered, required, ok_handles FROM team_game_coverage ORDER BY team_id",
    )
    # DAL has no handle and there is no league-wide one: counted as uncovered, never alerted
    assert cov == [(DAL, False, "", ""), (HOU, True, "RocketsBeat", "RocketsBeat")]
    facts = rows(
        store,
        "SELECT predicate, value, known_at FROM reporter_facts WHERE predicate LIKE 'reporter_%'",
    )
    assert [f[:2] for f in facts] == [("reporter_minutes_cap", 24.0)]
    assert facts[0][2] == TIP - timedelta(minutes=90)  # first read at the T-90 tick
    hb = json.loads((tmp_path / "ops" / "heartbeat" / "reporters.json").read_text())
    assert hb["job"] == "reporters" and hb["rc"] == 0 and hb["end_ts"] and hb["start_ts"]
    assert not (tmp_path / "data" / "poll.lock").exists()  # released
    assert not (tmp_path / "ops" / "ALERTS.md").exists()


def test_tick_alerts_once_when_a_team_game_has_no_ok_poll_by_t55(tmp_path: Path) -> None:
    clock = [NOW]
    store = tmp_path / "facts" / "reporter_facts.duckdb"
    # a transport that always fails: every poll ends error, nothing is covered, one alert per miss
    c = client(tmp_path, Server(lambda n: httpx.Response(500)), clock=lambda: clock[0])
    c.user_ids.put("RocketsBeat", "42")

    def go(now: datetime) -> rt.TickResult:
        clock[0] = now
        return rt.run_tick(
            now=now,
            allow=c.allowlist,
            games=[PREV, TONIGHT],
            make_client=lambda: c,
            make_ctx=lambda: ctx(),
            store_path=store,
            data_dir=tmp_path / "data",
            ops_dir=tmp_path / "ops",
            alerts_path=tmp_path / "ops" / "ALERTS.md",
        )

    go(TIP - timedelta(minutes=90))
    res = go(TIP - timedelta(minutes=55))
    assert res.alerts == 1
    again = go(TIP - timedelta(minutes=54))
    assert again.alerts == 0  # not repeated
    text = (tmp_path / "ops" / "ALERTS.md").read_text()
    assert text.count("[reporters] coverage") == 1 and TONIGHT.game_id in text
    statuses = {x[0] for x in rows(store, "SELECT status FROM poll_log")}
    assert statuses and all(s.startswith(("error", "refused")) for s in statuses)
    cov = rows(store, "SELECT covered FROM team_game_coverage")
    assert cov and not any(x[0] for x in cov)


def test_tick_without_a_token_leaves_a_nonzero_heartbeat(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path))

    def no_client() -> r.XClient:
        return r.XClient(allow, environ={}, data_dir=tmp_path / "data")

    res = rt.run_tick(
        now=TIP - timedelta(minutes=90),
        allow=allow,
        games=[PREV, TONIGHT],
        make_client=no_client,
        make_ctx=lambda: ctx(),
        store_path=tmp_path / "f" / "s.duckdb",
        data_dir=tmp_path / "data",
        ops_dir=tmp_path / "ops",
    )
    assert res.rc == rt.RC_REFUSED
    hb = json.loads((tmp_path / "ops" / "heartbeat" / "reporters.json").read_text())
    assert hb["rc"] == rt.RC_REFUSED and "X_BEARER_TOKEN" in hb["detail"]


def test_tick_idle_day_and_missing_schedule_still_write_heartbeats(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path))
    common = dict(
        allow=allow,
        make_client=lambda: None,
        make_ctx=lambda: ctx(),  # type: ignore[arg-type,return-value]
        store_path=tmp_path / "f" / "s.duckdb",
        data_dir=tmp_path / "data",
        ops_dir=tmp_path / "ops",
    )
    res = rt.run_tick(now=datetime(2026, 11, 30, 18, 0, tzinfo=UTC), games=[TONIGHT], **common)  # type: ignore[arg-type]
    assert res.rc == 0 and "no games" in res.detail
    res = rt.run_tick(now=TIP - timedelta(minutes=90), games=None, **common)  # type: ignore[arg-type]
    assert res.rc == rt.RC_NO_SCHEDULE
    assert (
        json.loads((tmp_path / "ops" / "heartbeat" / "reporters.json").read_text())["rc"]
        == rt.RC_NO_SCHEDULE
    )


def test_a_second_tick_cannot_start_while_one_holds_the_lock(tmp_path: Path) -> None:
    lock = tmp_path / "data" / "poll.lock"
    assert rt.acquire_lock(lock) and not rt.acquire_lock(lock)
    rt.release_lock(lock)
    assert rt.acquire_lock(lock)
    (lock / "pid").write_text("999999999")  # a dead owner
    assert rt.acquire_lock(lock)  # stale lock cleared
    rt.release_lock(lock)


# ------------------------------------------------------------------------- counts


def test_counts_dry_run_prints_cost_and_sends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = allowlist_yaml(tmp_path)
    rc = r.main(
        [
            "counts",
            "--dry-run",
            "--allowlist",
            str(allow),
            "--data-dir",
            str(tmp_path / "d"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "expected 24 requests ($0.24); worst case (hard bound) 28 requests" in out
    assert not (tmp_path / "out").exists() and not (tmp_path / "d").exists()  # nothing written


def test_counts_needs_confirm_and_a_token_and_respects_max_usd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    allow = str(allowlist_yaml(tmp_path))
    base = [
        "counts",
        "--allowlist",
        allow,
        "--data-dir",
        str(tmp_path / "d"),
        "--out-dir",
        str(tmp_path / "o"),
    ]
    assert r.main(base) == 2 and "--confirm" in capsys.readouterr().err
    monkeypatch.delenv(r.KEY_ENV, raising=False)
    assert r.main([*base, "--confirm"]) == 2 and r.KEY_ENV in capsys.readouterr().err
    monkeypatch.setenv(r.KEY_ENV, "tok")
    assert r.main([*base, "--confirm", "--max-usd", "0.10"]) == 2
    assert "> --max-usd" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_counts_never_queries_season_2025_26(monkeypatch: pytest.MonkeyPatch) -> None:
    assert all(rcount.window(s)[1].date() <= rcount.NOT_AFTER for s in rcount.SEASONS)
    monkeypatch.setitem(
        rcount.SEASONS, "2025-26", (datetime(2025, 10, 21).date(), datetime(2026, 4, 12).date())
    )
    with pytest.raises(r.PollRefused, match="never queried"):
        rcount.window("2025-26")
    with pytest.raises(r.PollRefused):
        rcount.make_plan(r.load_allowlist(r.ALLOWLIST_PATH), ["2025-26"])


def test_counts_queries_for_the_real_allowlist_fit_the_1024_char_limit() -> None:
    allow = r.load_allowlist(r.ALLOWLIST_PATH)
    qs = rcount.build_queries(allow)
    assert {q.qid for q in qs} == {"restriction", "status"}
    assert all(len(q.text) <= rcount.QUERY_MAX_CHARS for q in qs)
    assert all(f"from:{h}" in q.text for q in qs for h in allow.handles())


def test_counts_run_writes_pages_once_charges_the_ledger_and_resumes(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path))
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.url.path == "/2/tweets/counts/all"
        page = 1 + int(req.url.params.get("next_token", "0"))
        meta: dict[str, Any] = {"total_tweet_count": 3}
        if page < 2:
            meta["next_token"] = str(page)
        body = {
            "data": [
                {"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z", "tweet_count": 3}
            ],
            "meta": meta,
        }
        return httpx.Response(200, json=body)

    c = r.XClient(
        allow,
        environ=ENV,
        data_dir=tmp_path / "d",
        clock=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    plan = rcount.make_plan(allow, ["2023-24"])
    out = tmp_path / "out"
    summary = rcount.run_counts(c, plan, out)
    assert summary["requests"] == 4 and summary["totals"] == {
        "restriction/2023-24": 6,
        "status/2023-24": 6,
    }
    assert len(list(out.glob("*_p*.json"))) == 4
    ledger = json.loads((tmp_path / "d" / "budget.json").read_text())
    assert ledger["days"]["2026-10-28"]["counts_all"] == 4
    assert c.budget.month_usd(NOW) == pytest.approx(0.04)
    assert all(q.url.params["granularity"] == "day" for q in calls)
    assert all("2025" not in q.url.params["end_time"][:4] for q in calls)
    # resume: pages already on disk are not requested again, files are never rewritten
    calls.clear()
    again = rcount.run_counts(c, plan, out)
    assert again["requests"] == 0 and calls == []
    assert TOKEN not in "".join(p.read_text() for p in out.glob("*.json"))


def test_counts_month_cap_refuses_before_any_request(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, budget={"usd_per_month": 0.05}))
    c = r.XClient(
        allow,
        environ=ENV,
        data_dir=tmp_path / "d",
        clock=lambda: NOW,
        transport=httpx.MockTransport(lambda req: pytest.fail("a request was sent")),
    )
    with pytest.raises(r.BudgetExceeded, match="cap"):
        rcount.run_counts(c, rcount.make_plan(allow), tmp_path / "o")


# ---------------------------------------------------------- not on the prediction path


def test_prediction_path_does_not_import_the_reporter_modules() -> None:
    from collections import deque

    from tests.test_layering import imports_of, module_path

    seen: set[str] = set()
    queue = deque(["nba.daily.__main__"])
    while queue:
        mod = queue.popleft()
        if mod in seen or module_path(mod) is None:
            continue
        seen.add(mod)
        queue.extend(imports_of(mod))
    assert len(seen) > 50  # the closure was really walked
    assert not [
        m for m in seen if "reporter" in m or m == "nba.facts" or m.startswith("nba.facts.")
    ]


def test_new_modules_import_no_research_and_no_model_code() -> None:
    root = Path(r.__file__).resolve().parent
    for name in ("reporter_pretip", "reporter_store", "reporter_tick", "reporter_counts"):
        tree = ast.parse((root / f"{name}.py").read_text())
        mods = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.Import | ast.ImportFrom)
            for a in (n.names if isinstance(n, ast.Import) else [None])
            if (n.module if isinstance(n, ast.ImportFrom) else a.name)
        }
        assert not [
            m for m in mods if m.startswith(("research", "nba.models", "nba.props", "nba.sim"))
        ], name


# ------------------------------------------------------------------------------ the CLI


def test_cli_tick_with_no_schedule_exits_5_and_leaves_a_heartbeat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = allowlist_yaml(tmp_path)
    rc = r.main(
        [
            "tick",
            "--allowlist",
            str(allow),
            "--data-dir",
            str(tmp_path / "d"),
            "--ops-dir",
            str(tmp_path / "ops"),
            "--schedule-cache",
            str(tmp_path / "no_cache"),
            "--rosters",
            str(tmp_path / "no_rosters"),
            "--aliases",
            str(tmp_path / "none.yaml"),
        ]
    )
    assert rc == rt.RC_NO_SCHEDULE and "schedule cache missing" in capsys.readouterr().out
    hb = json.loads((tmp_path / "ops" / "heartbeat" / "reporters.json").read_text())
    assert hb["rc"] == rt.RC_NO_SCHEDULE and hb["end_ts"]
    assert not (tmp_path / "facts").exists()  # an idle tick creates no store


def _parse_only(tmp_path: Path, raw_root: str | None) -> tuple[int, Path, str]:
    """Run ``parse-only --store`` on one HOU post; the raw file is written under
    ``tmp/d/<raw_root>/<date>/`` (None: outside every raw root)."""
    import contextlib
    import io

    import polars as pl

    from nba.daily.schedule import save_schedule_cache

    created, fetched = datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 22, 45)
    if raw_root is None:
        raw = tmp_path / "elsewhere.parquet"
        r.posts_frame([post("9001", created, fetched)]).write_parquet(raw)
    else:
        written = r.write_raw(
            tmp_path / "d" / raw_root, "RocketsBeat", [post("9001", created, fetched)], fetched
        )
        assert written is not None
        raw = written
    allow = tmp_path / "reporters.yaml"
    allow.write_text(json.dumps({"handles": [{"handle": "RocketsBeat", "team": "HOU"}]}))
    ros = tmp_path / "rosters" / "2026-10-10"
    ros.mkdir(parents=True)
    pl.DataFrame(
        {
            "team_id": [HOU] * len(HOU_ROSTER),
            "player_id": [p for _, p in HOU_ROSTER],
            "exp": ["3"] * len(HOU_ROSTER),
            "player_name": [n for n, _ in HOU_ROSTER],
        }
    ).write_parquet(ros / f"{HOU}.parquet")
    cache = tmp_path / "schedule_cache"
    save_schedule_cache([PREV, TONIGHT], rf.season_for(datetime(2026, 10, 28)), cache)
    store = tmp_path / "facts" / "reporter_facts.duckdb"
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = r.main(
            [
                "parse-only", "--file", str(raw), "--allowlist", str(allow),
                "--data-dir", str(tmp_path / "d"), "--rosters", str(tmp_path / "rosters"),
                "--schedule-cache", str(cache), "--aliases", str(tmp_path / "none.yaml"),
                "--store", str(store),
            ]
        )  # fmt: skip
    return rc, store, err.getvalue()


def test_cli_parse_only_of_a_live_raw_file_stores_fetch_time_as_known_at(tmp_path: Path) -> None:
    rc, store, _ = _parse_only(tmp_path, "raw")
    assert rc == 0
    got = rows(
        store, "SELECT predicate, known_at, created_at, capture_mode FROM reporter_facts ORDER BY 1"
    )
    created, fetched = datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 22, 45)
    assert got == [
        ("claimed_minutes_restriction", fetched, created, "live"),
        ("reporter_minutes_cap", fetched, created, "live"),
    ]


def test_cli_parse_only_of_a_backfill_raw_file_writes_no_fact_m2(tmp_path: Path) -> None:
    rc, store, _ = _parse_only(tmp_path, "raw_backfill")
    assert rc == 0 and rows(store, "SELECT * FROM reporter_facts") == []


def test_cli_parse_only_of_a_file_outside_the_raw_store_writes_nothing_m2(tmp_path: Path) -> None:
    rc, store, err = _parse_only(tmp_path, None)
    assert rc == 0 and rows(store, "SELECT * FROM reporter_facts") == []
    assert "nothing is written" in err


def test_a_post_missing_from_the_index_defaults_to_backfill_m2() -> None:
    p = post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0))
    facts, _, counts = rf.pretip_facts_for_posts([p], ctx())  # no index at all
    assert facts == [] and counts["backfill_ineligible"] == 1
    assert rf.capture_mode_of_post(p, {}) == "backfill"
    claimed, _ = rf.claims_for_posts([p], ctx())
    assert rf.claimed_store_facts(claimed, {}) == []


# ---------------------------------------------------- adversary review M1, M3, M4, M7, minors


def test_a_429_wait_does_not_backdate_the_stamp_m1(tmp_path: Path) -> None:
    clock = [datetime(2026, 10, 28, 22, 29, 0, tzinfo=UTC)]  # T-61
    reset = clock[0] + timedelta(seconds=110)  # the wait ends at 22:30:50, after T-60
    calls: list[int] = []

    def script(n: int) -> httpx.Response:
        calls.append(n)
        if n == 1:
            return httpx.Response(200, json={"data": {"id": "42"}})
        if n == 2:
            return httpx.Response(429, headers={"x-rate-limit-reset": str(int(reset.timestamp()))})
        return httpx.Response(
            200, json={"data": [tweet("1", CAP_TEXT, "2026-10-28T15:00:00.000Z")]}
        )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    c = r.XClient(
        r.load_allowlist(allowlist_yaml(tmp_path)), environ=ENV, data_dir=tmp_path / "d",
        clock=lambda: clock[0], sleep=sleep, transport=Server(script).transport,
    )  # fmt: skip
    res = c.poll()
    (p,) = res.posts
    assert p.fetched_at == datetime(2026, 10, 28, 22, 30, 50)  # when the answer came, not 22:29:00
    assert p.fetched_at > TIPA.replace(tzinfo=None) - timedelta(minutes=60)
    out = res.outcomes[0]
    # the poll started in the window but ended after T-60: it does NOT cover the team-game
    assert out.started_at == datetime(2026, 10, 28, 22, 29, tzinfo=UTC)
    rec = rs.PollRecord(
        "T-70",
        "RocketsBeat",
        HOU,
        rt._naive(out.started_at),
        rt._naive(out.ended_at or out.started_at),
        "ok",
    )
    assert not rs.compute_coverage("g", HOU, TIP, ("RocketsBeat",), [rec]).covered
    first = rs.first_seen_index([tmp_path / "d" / "raw"])
    assert first["1"][0] == datetime(2026, 10, 28, 22, 30, 50)


def test_stale_schedule_cache_stops_the_tick_with_rc5_m3(tmp_path: Path) -> None:
    import os

    from nba.daily.schedule import MAX_SCHEDULE_CACHE_AGE, load_schedule_cache, save_schedule_cache

    cache = tmp_path / "cache"
    save_schedule_cache([PREV, TONIGHT], "2026-27", cache)
    assert load_schedule_cache("2026-27", cache, max_age=MAX_SCHEDULE_CACHE_AGE) is not None
    old = datetime.now().timestamp() - 40 * 3600
    os.utime(cache / "schedule_2026-27.json", (old, old))
    assert load_schedule_cache("2026-27", cache, max_age=MAX_SCHEDULE_CACHE_AGE) is None
    src = Path(r.__file__).read_text()
    assert (
        "max_age=MAX_SCHEDULE_CACHE_AGE"
        in src.split('if args.cmd == "tick"')[1].split("report-test")[0]
    )


def test_coverage_is_keyed_on_the_tip_so_a_made_up_game_gets_its_own_row_m3(tmp_path: Path) -> None:
    con = rs.open_store(tmp_path / "s.duckdb")
    old_tip = TIP
    new_tip = TIP + timedelta(days=20)  # postponed, same game id
    now = old_tip - timedelta(minutes=30)
    polls = [poll("RocketsBeat", 75)]
    cov_old = rs.compute_coverage(TONIGHT.game_id, HOU, old_tip, ("RocketsBeat",), polls)
    assert cov_old.covered and rs.write_coverage(con, cov_old, now)
    cov_new = rs.compute_coverage(TONIGHT.game_id, HOU, new_tip, ("RocketsBeat",), polls)
    assert not cov_new.covered  # the poll was not in the NEW game's window
    assert rs.write_coverage(con, cov_new, now)  # not swallowed by the old row
    got = con.execute("SELECT tip, covered FROM team_game_coverage ORDER BY tip").fetchall()
    assert got == [(old_tip, True), (new_tip, False)]
    con.close()


def test_two_caps_in_one_post_keep_the_numbered_one_m4() -> None:
    text = (
        "Alperen Sengun is on a minutes restriction tonight. "
        "Alperen Sengun will be on a 20-minute restriction tonight."
    )
    p = post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0), text)
    both = post("9002", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0),
                "Alperen Sengun is a game-time decision tonight. "
                "Alperen Sengun is out tonight.")  # fmt: skip
    facts, _, counts = rf.pretip_facts_for_posts([p, both], ctx(), live([p, both]))
    caps = [f for f in facts if f.predicate == "reporter_minutes_cap"]
    assert [f.value for f in caps] == [20.0]
    status = [
        json.loads(f.value_text or "{}")["status"]
        for f in facts
        if f.predicate == "reporter_status"
    ]
    assert status == ["out"]  # out beats game_time_decision in the same post
    assert counts["merged_same_post_player"] >= 2


def test_claimed_facts_keep_no_post_text_m6() -> None:
    p = post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0))
    claimed, _ = rf.claims_for_posts([p], ctx())
    (f,) = rf.claimed_store_facts(claimed, live([p]))
    assert "Sengun" not in (f.value_text or "") and "restriction" not in (f.value_text or "")
    meta = json.loads(f.value_text or "{}")
    assert set(meta) == {"post_id", "evidence_sha256"} and len(meta["evidence_sha256"]) == 64


def test_opponent_player_gets_the_opponents_team_id_m7() -> None:
    dal_roster = [("Zed Mavsman", 777)]
    # DAL also has an EARLIER game than the HOU-DAL one; a fact about a DAL player must follow DAL
    early = ScheduledGame("0022600030", datetime(2026, 10, 28, 20, 0), DAL, 1610612763)
    c = rf.Context(ALLOW, {HOU: HOU_ROSTER, DAL: dal_roster}, {}, [PREV, TONIGHT, early])
    p = post(
        "9001",
        datetime(2026, 10, 28, 15, 0),
        datetime(2026, 10, 28, 15, 1),
        "Zed Mavsman is out tonight.",
    )
    facts, _, _ = rf.pretip_facts_for_posts([p], c, live([p]))
    assert [(f.subject, f.object) for f in facts] == [("player:777", f"game:{early.game_id}")]


def test_poll_start_reaches_the_previous_tip_plus_3h_not_18h_m7(tmp_path: Path) -> None:
    srv = Paged([], size=5)
    now = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)  # T-90 of a Wednesday game
    c = client(tmp_path, srv, clock=lambda: now)
    prev = datetime(2026, 10, 27, 0, 30)  # Monday 20:30 ET game; Tuesday idle
    assert c.poll_start("RocketsBeat", now, prev_tip=prev) == datetime(
        2026, 10, 27, 3, 30, tzinfo=UTC
    )
    # the 18 h rule (no previous game known) would have started Wednesday 04:00Z, losing a
    # Tuesday 13:00 ET (17:00Z) post about Wednesday's game
    assert c.poll_start("RocketsBeat", now) == datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    far = datetime(2026, 10, 1, 0, 30)  # long ago: capped at 48 h
    assert c.poll_start("RocketsBeat", now, prev_tip=far) == now - timedelta(hours=48)
    c.state.update("RocketsBeat", last_ok_poll_at=datetime(2026, 10, 28, 17, 30).isoformat())
    got = c.poll_start("RocketsBeat", now, prev_tip=prev)
    assert got == datetime(2026, 10, 28, 17, 28, tzinfo=UTC)  # the cursor minus 120 s


def test_tick_asks_x_from_the_previous_tip_plus_3h_m7(tmp_path: Path) -> None:
    posts = [tweet("7001", CAP_TEXT, "2026-10-27T17:00:00.000Z")]  # Tuesday 13:00 ET
    clock = [NOW]
    run, store, srv = tick_env(tmp_path, posts, clock)
    run(TIP - timedelta(minutes=90))
    starts = [q.url.params["start_time"] for q in srv.requests if q.url.path.endswith("/tweets")]
    assert starts == ["2026-10-27T03:30:00Z"]


def test_each_page_is_written_to_raw_as_it_arrives_m2(tmp_path: Path) -> None:
    pages = {"n": 0}
    posts = many(12)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/2/users/by/username/"):
            return httpx.Response(200, json={"data": {"id": "42"}})
        pages["n"] += 1
        if pages["n"] in (2, 3):  # page 2 and its one retry
            return httpx.Response(500)
        return httpx.Response(200, json={"data": posts[:5], "meta": {"next_token": "5"}})

    c = r.XClient(
        r.load_allowlist(allowlist_yaml(tmp_path)), environ=ENV, data_dir=tmp_path / "d",
        clock=lambda: NOW, sleep=lambda s: None, transport=httpx.MockTransport(handler),
    )  # fmt: skip
    res = c.poll(isolate_errors=True)
    out = res.outcomes[0]
    assert out.status.startswith("error:") and len(out.posts) == 5 and not out.ok
    files = sorted((tmp_path / "d" / "raw").rglob("*.parquet"))
    assert len(files) == 1 and sum(len(r.read_raw(f)) for f in files) == 5  # billed and kept
    st = tmp_path / "d" / "state.json"
    assert not st.exists() or "last_ok_poll_at" not in json.loads(st.read_text()).get(
        "rocketsbeat", {}
    )


def test_counts_worst_case_is_a_hard_bound_m1(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path))
    plan = rcount.make_plan(allow)
    assert plan.max_requests == 28 and plan.max_usd == pytest.approx(0.28)
    sent: list[int] = []

    def endless(req: httpx.Request) -> httpx.Response:  # a server that never stops paging
        sent.append(1)
        body = {"data": [], "meta": {"next_token": "more"}}
        return httpx.Response(200, json=body)

    c = r.XClient(allow, environ=ENV, data_dir=tmp_path / "d", clock=lambda: NOW,
                  transport=httpx.MockTransport(endless))  # fmt: skip
    summary = rcount.run_counts(c, plan, tmp_path / "o", max_usd=0.28)
    assert len(sent) <= plan.max_requests and summary["requests"] == len(sent) == 28
    assert summary["truncated"]  # the page cap was hit, and it says so


def test_a_stuck_tick_lock_is_an_alert_not_a_quiet_skip_m3(tmp_path: Path) -> None:
    import os

    lock = tmp_path / "data" / "poll.lock"
    assert rt.acquire_lock(lock)
    allow = r.load_allowlist(allowlist_yaml(tmp_path))
    common = dict(
        allow=allow, games=[PREV, TONIGHT], make_client=lambda: None, make_ctx=lambda: ctx(),
        store_path=tmp_path / "f" / "s.duckdb", data_dir=tmp_path / "data",
        ops_dir=tmp_path / "ops",
    )  # fmt: skip
    # a second pid cannot own it: pretend another process holds it (acquire sees a live pid)
    (lock / "pid").write_text(str(os.getppid()))
    res = rt.run_tick(now=TIPA - timedelta(minutes=90), **common)  # type: ignore[arg-type]
    assert res.rc == 0 and "lock held" in res.detail  # held for seconds: quiet skip
    old = datetime.now().timestamp() - 700
    os.utime(lock, (old, old))
    res = rt.run_tick(now=TIPA - timedelta(minutes=90), **common)  # type: ignore[arg-type]
    assert res.rc == rt.RC_REFUSED and "stuck" in res.detail
    hb = json.loads((tmp_path / "ops" / "heartbeat" / "reporters.json").read_text())
    assert hb["rc"] == rt.RC_REFUSED


# ------------------------------------------------------------------ adversary round 2 (N1-N8)


def test_postgame_tonight_after_midnight_et_is_not_the_next_games_pre_tip_cap_n1() -> None:
    """22:30 ET tip (02:30Z), a back-to-back at 19:30 ET the next day; a postgame 'tonight' post
    written after the F4 window (tip + 3 h 15 m = 01:45 ET) used to become a LIVE cap on the NEXT
    game, stamped about 18 h before its T-60."""
    late = ScheduledGame("0022600040", datetime(2026, 10, 29, 2, 30), DAL, HOU)  # Oct 28 22:30 ET
    b2b = ScheduledGame("0022600041", datetime(2026, 10, 29, 23, 30), HOU, DAL)  # Oct 29 19:30 ET
    text = "Alperen Sengun will be on a 24-minute restriction tonight."
    post_at = late.tipoff + timedelta(hours=3, minutes=15)  # 05:45Z = 01:45 ET Oct 29
    p = post("9001", post_at, post_at + timedelta(seconds=30), text)
    facts, _, counts = rf.pretip_facts_for_posts([p], ctx([late, b2b]), live([p]))
    assert facts == [] and counts["drop_f3_day"] == 1
    # the same words written that morning at 11:00 ET are the next game's pre-tip claim
    ok = post("9002", datetime(2026, 10, 29, 15, 0), datetime(2026, 10, 29, 15, 1), text)
    facts, _, _ = rf.pretip_facts_for_posts([ok], ctx([late, b2b]), live([ok]))
    assert [f.object for f in facts] == [f"game:{b2b.game_id}"]
    # a post just after 06:00 ET (10:00Z) already belongs to the new day
    early = post("9003", datetime(2026, 10, 29, 10, 5), datetime(2026, 10, 29, 10, 6), text)
    facts, _, _ = rf.pretip_facts_for_posts([early], ctx([late, b2b]), live([early]))
    assert len(facts) == 1
    # "tomorrow" written at 01:45 ET after the late game means the evening of the same ET date
    tmw = post("9004", post_at, post_at + timedelta(seconds=30), "Alperen Sengun is out tomorrow.")
    facts, _, _ = rf.pretip_facts_for_posts([tmw], ctx([late, b2b]), live([tmw]))
    assert len(facts) == 1


def test_claimed_facts_merge_like_the_reporter_facts_n2() -> None:
    text = (
        "Alperen Sengun is on a minutes restriction tonight. "
        "Alperen Sengun will be on a 20-minute restriction tonight."
    )
    p = post("9001", datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 21, 0), text)
    claimed, _ = rf.claims_for_posts([p], ctx())
    rows_ = [
        f for f in rf.claimed_store_facts(claimed, live([p])) if f.predicate.startswith("claimed_")
    ]
    assert len(rows_) == 1 and rows_[0].value == 20.0


def test_league_wide_handle_across_an_idle_day_is_not_a_silent_gap_n3(tmp_path: Path) -> None:
    now = datetime(2026, 11, 26, 15, 30, tzinfo=UTC)  # Thanksgiving, 10:30 ET
    tue_tip = datetime(2026, 11, 25, 1, 30)  # the league's last games, Tuesday 20:30 ET
    srv = Paged(many(2), size=100)
    c = client(tmp_path, srv, clock=lambda: now)
    c.state.update("RocketsBeat", last_ok_poll_at=datetime(2026, 11, 24, 23, 0).isoformat())
    # no previous tip known (manual poll): the 18 h cap cuts the cursor's 40 h gap -> not "ok"
    out = c.poll().outcomes[0]
    assert out.status == "short:lookback_cap" and not out.ok
    st = json.loads((tmp_path / "data" / "state.json").read_text())["rocketsbeat"]
    assert st["last_ok_poll_at"] == "2026-11-26T15:30:00"  # the gap is unrecoverable: move on
    # with the league's latest tip (what the tick passes) the gap is read from the cursor, ok
    c2 = client(tmp_path, Paged(many(2), size=100), clock=lambda: now)
    c2.state.update("RocketsBeat", last_ok_poll_at=datetime(2026, 11, 24, 23, 0).isoformat())
    start, clipped = c2.poll_bounds("RocketsBeat", now, prev_tip=tue_tip)
    # the league's last tip + 3 h (posts before it belong to that game, F4), nothing older
    assert not clipped and start == datetime(2026, 11, 25, 4, 30, tzinfo=UTC)


def test_league_wide_handle_overnight_window_is_read_not_skipped_n10(tmp_path: Path) -> None:
    """Wed 19:00 + 22:00 ET, Thu 19:30 ET slate, league handle only. The Thursday 13:30 ET sweep
    must start no later than Wednesday's last ok poll, so the Wed 22:10 and 23:30 ET posts (about
    Thursday's game) are inside the requested window. (Before the fix: Thu 01:00 ET.)"""
    wed_early = ScheduledGame("0022600070", datetime(2026, 10, 28, 23, 0), 1610612738, 1610612752)
    wed_late = ScheduledGame("0022600071", datetime(2026, 10, 29, 2, 0), 1610612747, 1610612756)
    thu = ScheduledGame("0022600072", datetime(2026, 10, 29, 23, 30), HOU, DAL)
    games = [wed_early, wed_late, thu]
    allow = allowlist_yaml(tmp_path, [{"handle": "ShamsC", "team": None}])
    srv = Paged([], size=100)
    clock = [datetime(2026, 10, 28, 17, 30, tzinfo=UTC)]
    c = client(tmp_path, srv, allow=allow, clock=lambda: clock[0])
    store = tmp_path / "f" / "s.duckdb"

    def tick(t: datetime) -> None:
        clock[0] = t
        rt.run_tick(
            now=t,
            allow=c.allowlist,
            games=games,
            make_client=lambda: c,
            make_ctx=lambda: ctx(games),
            store_path=store,
            data_dir=tmp_path / "data",
            ops_dir=tmp_path / "ops",
        )

    t = datetime(2026, 10, 28, 17, 30, tzinfo=UTC)
    while t <= datetime(2026, 10, 29, 2, 5, tzinfo=UTC):  # all of Wednesday's slate
        tick(t)
        t += timedelta(minutes=5)
    thursday_sweep = datetime(2026, 10, 29, 17, 30, tzinfo=UTC)  # 13:30 ET
    tick(thursday_sweep)
    polls = rows(
        store,
        "SELECT started_at, start_time, status FROM poll_log WHERE status = 'ok' ORDER BY poll_id",
    )
    wed_last = [p for p in polls if p[0] < datetime(2026, 10, 29, 12, 0)][-1]
    thu_first = [p for p in polls if p[0] >= datetime(2026, 10, 29, 12, 0)][0]
    assert thu_first[0] == thursday_sweep.replace(tzinfo=None)
    assert thu_first[1] <= wed_last[0]  # the windows chain: nothing between them is skipped
    assert thu_first[1] <= datetime(2026, 10, 29, 2, 10)  # the Wed 22:10 ET post is requested
    assert thu_first[1] <= datetime(2026, 10, 29, 3, 30)  # ... and the 23:30 ET one
    for started, start_time, _ in polls:
        assert start_time <= started - timedelta(seconds=r.CURSOR_OVERLAP_S)


def test_every_ok_poll_of_a_handle_chains_to_the_previous_one_n10(tmp_path: Path) -> None:
    """The scorer's chain check, as a property of the poll_log the tick writes."""
    games = [ScheduledGame("0022600080", datetime(2026, 10, 29, 2, 0), 1610612747, 1610612756)]
    allow = allowlist_yaml(tmp_path, [{"handle": "ShamsC", "team": None}])
    clock = [datetime(2026, 10, 28, 17, 30, tzinfo=UTC)]
    c = client(tmp_path, Paged([], size=100), allow=allow, clock=lambda: clock[0])
    store = tmp_path / "f" / "s.duckdb"
    t = datetime(2026, 10, 28, 17, 30, tzinfo=UTC)
    while t <= datetime(2026, 10, 29, 2, 5, tzinfo=UTC):
        clock[0] = t
        rt.run_tick(
            now=t,
            allow=c.allowlist,
            games=games,
            make_client=lambda: c,
            make_ctx=lambda: ctx(games),
            store_path=store,
            data_dir=tmp_path / "data",
            ops_dir=tmp_path / "ops",
        )
        t += timedelta(minutes=5)
    polls = rows(
        store, "SELECT started_at, start_time FROM poll_log WHERE status = 'ok' ORDER BY poll_id"
    )
    assert len(polls) >= 3
    for i in range(1, len(polls)):
        assert polls[i][1] <= polls[i - 1][0]  # this poll's window starts by the last one's start


def test_day_ref_is_per_clause_not_per_sentence_n11() -> None:
    first, nxt = _b2b(datetime(2026, 10, 28, 23, 30))
    at = first.tipoff + timedelta(minutes=195)  # 22:45 ET, inside the postgame window
    text = "Alperen Sengun is on a 20-minute restriction, and Zed Mavsman will be out tomorrow."
    p = post("9001", at, at + timedelta(seconds=20), text)
    c = rf.Context(ALLOW, {HOU: HOU_ROSTER, DAL: [("Zed Mavsman", 777)]}, {}, [first, nxt])
    facts, _, counts = rf.pretip_facts_for_posts([p], c, live([p]))
    assert [f.subject for f in facts] == [
        "player:777"
    ]  # Zed names the day; Sengun's clause does not
    assert json.loads(facts[0].value_text or "{}")["day_ref"] == "tomorrow"
    assert counts["drop_f3_postgame_no_day"] == 1


def test_text_sha256_of_none_is_the_empty_hash_m12() -> None:
    assert rs.text_sha256(None) == rs.text_sha256("")


def test_store_copy_is_restricted_to_reporter_predicates_n5(tmp_path: Path) -> None:
    store = _store_with_facts(tmp_path)
    con = duckdb.connect(str(store))
    t = datetime(2026, 10, 28, 21, 0)
    con.execute(
        "INSERT INTO reporter_facts VALUES (900, 'player:2', 'listed_status', ?, NULL, 'out', ?, "
        "NULL, NULL, 'x:A', 0.9, 'p9', ?, ?, 'live', 'v')",
        [f"game:{G1}", t, t, t],
    )
    con.close()
    frame, _ = reporter_facts_frame(store, 2024, ())
    assert set(frame["predicate"].to_list()) == {"reporter_minutes_cap"}


def test_parse_only_outside_file_with_an_honest_raw_copy_keeps_the_honest_stamp_n8(
    tmp_path: Path,
) -> None:
    """A planted file outside the raw store claims it was fetched at T-15; the raw store holds the
    same post first read at 22:45. The stored stamp is the raw store's, not the planted one."""
    import polars as pl

    from nba.daily.schedule import save_schedule_cache

    created = datetime(2026, 10, 28, 15, 0)
    honest, planted = datetime(2026, 10, 28, 22, 45), datetime(2026, 10, 28, 23, 15)
    r.write_raw(tmp_path / "d" / "raw", "RocketsBeat", [post("9001", created, honest)], honest)
    outside = tmp_path / "planted.parquet"
    r.posts_frame([post("9001", created, planted)]).write_parquet(outside)
    allow = tmp_path / "reporters.yaml"
    allow.write_text(json.dumps({"handles": [{"handle": "RocketsBeat", "team": "HOU"}]}))
    ros = tmp_path / "rosters" / "2026-10-10"
    ros.mkdir(parents=True)
    pl.DataFrame(
        {
            "team_id": [HOU] * len(HOU_ROSTER),
            "player_id": [p for _, p in HOU_ROSTER],
            "exp": ["3"] * len(HOU_ROSTER),
            "player_name": [n for n, _ in HOU_ROSTER],
        }
    ).write_parquet(ros / f"{HOU}.parquet")
    cache = tmp_path / "schedule_cache"
    save_schedule_cache([PREV, TONIGHT], rf.season_for(datetime(2026, 10, 28)), cache)
    store = tmp_path / "facts" / "reporter_facts.duckdb"
    rc = r.main(
        [
            "parse-only", "--file", str(outside), "--allowlist", str(allow),
            "--data-dir", str(tmp_path / "d"), "--rosters", str(tmp_path / "rosters"),
            "--schedule-cache", str(cache), "--aliases", str(tmp_path / "none.yaml"),
            "--store", str(store),
        ]
    )  # fmt: skip
    assert rc == 0
    got = rows(store, "SELECT DISTINCT known_at, first_seen_at FROM reporter_facts")
    assert got == [(honest, honest)]


# ------------------------------------------------------------------ adversary round 3


def _b2b(late_tip: datetime) -> tuple[ScheduledGame, ScheduledGame]:
    first = ScheduledGame("0022600050", late_tip, DAL, HOU)
    nxt = ScheduledGame("0022600051", late_tip + timedelta(hours=21), HOU, DAL)
    return first, nxt


@pytest.mark.parametrize(
    ("tip_utc", "minutes_after_tip"),
    [
        (datetime(2026, 10, 29, 2, 30), 195),  # 22:30 ET tip, post 01:45 ET
        (datetime(2026, 10, 28, 23, 30), 195),  # 19:30 ET tip, post 22:45 ET
        (datetime(2026, 10, 28, 23, 30), 330),  # 19:30 ET tip, post 01:00 ET
    ],
)
def test_postgame_sentence_without_a_day_word_is_dropped_n1r(
    tip_utc: datetime, minutes_after_tip: int
) -> None:
    first, nxt = _b2b(tip_utc)
    texts = [
        "Alperen Sengun is on a 20-minute restriction, Udoka said.",
        "Alperen Sengun will be on a 24-minute restriction, per Udoka.",
        "Alperen Sengun is out vs. Dallas, Udoka said postgame.",
    ]
    at = tip_utc + timedelta(minutes=minutes_after_tip)
    posts = [post(f"90{i}", at, at + timedelta(seconds=20), t) for i, t in enumerate(texts)]
    facts, _, counts = rf.pretip_facts_for_posts(posts, ctx([first, nxt]), live(posts))
    assert facts == [] and counts["drop_f3_postgame_no_day"] == 3
    # the same sentences naming tomorrow / the weekday are the next game's, with day_ref recorded
    named = [
        post(
            "9101",
            at,
            at + timedelta(seconds=20),
            "Alperen Sengun will be on a 20-minute restriction tomorrow.",
        ),
        post(
            "9102",
            at,
            at + timedelta(seconds=20),
            f"Alperen Sengun is out on {nxt.game_date_et:%A}.",
        ),
    ]
    facts, _, _ = rf.pretip_facts_for_posts(named, ctx([first, nxt]), live(named))
    assert sorted(json.loads(f.value_text or "{}")["day_ref"] for f in facts) == [
        "tomorrow",
        "weekday",
    ]
    assert all(json.loads(f.value_text or "{}")["postgame"] for f in facts)


def test_postgame_window_end_is_6am_local_et_across_dst_m10() -> None:
    # DST ends 2026-11-01: a 22:30 EDT tip on 10-31; the window ends 06:00 EST on 11-01 (11:00Z)
    assert rf.postgame_window_end(datetime(2026, 11, 1, 2, 30)) == datetime(2026, 11, 1, 11, 0)
    assert rf.postgame_window_end(datetime(2026, 10, 30, 2, 30)) == datetime(2026, 10, 30, 10, 0)
    assert (
        rf.evening_date(datetime(2026, 11, 1, 10, 30)) == datetime(2026, 10, 31).date()
    )  # 05:30 EST
    assert (
        rf.evening_date(datetime(2026, 11, 1, 11, 30)) == datetime(2026, 11, 1).date()
    )  # 06:30 EST
    assert (
        rf.evening_date(datetime(2026, 10, 30, 9, 30)) == datetime(2026, 10, 29).date()
    )  # 05:30 EDT
    assert (
        rf.evening_date(datetime(2026, 10, 30, 10, 30)) == datetime(2026, 10, 30).date()
    )  # 06:30 EDT


def test_tonight_posted_between_midnight_and_6am_et_is_dropped_by_design_m9() -> None:
    nxt = ScheduledGame("0022600060", datetime(2026, 10, 29, 23, 30), HOU, DAL)  # 19:30 ET Oct 29
    text = "Alperen Sengun will be on a 24-minute restriction tonight."
    at = datetime(2026, 10, 29, 4, 30)  # 00:30 ET the same calendar day
    p = post("9001", at, at + timedelta(seconds=20), text)
    facts, _, counts = rf.pretip_facts_for_posts([p], ctx([PREV, nxt]), live([p]))
    assert facts == [] and counts["drop_f3_day"] == 1  # a recall cost, documented
    named = post("9002", at, at + timedelta(seconds=20), "Alperen Sengun is out on Thursday.")
    facts, _, _ = rf.pretip_facts_for_posts([named], ctx([PREV, nxt]), live([named]))
    assert len(facts) == 1


def _staggered_run(tmp_path: Path, posts: list[dict[str, Any]]) -> tuple[Any, list[httpx.Request]]:
    """League-wide handle only; slate 19:00 ET (23:00Z) and 22:00 ET (02:00Z); ticks every 5 min."""
    early = ScheduledGame("0022600070", datetime(2026, 10, 28, 23, 0), 1610612738, 1610612752)
    late = ScheduledGame("0022600071", datetime(2026, 10, 29, 2, 0), HOU, DAL)
    last_night = ScheduledGame("0022600069", datetime(2026, 10, 28, 1, 0), 1610612738, DAL)
    games = [last_night, early, late]
    allow = allowlist_yaml(tmp_path, [{"handle": "ShamsC", "team": None}])
    srv = Paged(posts, size=100)
    clock = [datetime(2026, 10, 28, 17, 30, tzinfo=UTC)]
    c = client(tmp_path, srv, allow=allow, clock=lambda: clock[0])
    t = datetime(2026, 10, 28, 17, 30, tzinfo=UTC)
    while t <= datetime(2026, 10, 29, 2, 5, tzinfo=UTC):
        clock[0] = t
        rt.run_tick(
            now=t,
            allow=c.allowlist,
            games=games,
            make_client=lambda: c,
            make_ctx=lambda: ctx(games),
            store_path=tmp_path / "f" / "s.duckdb",
            data_dir=tmp_path / "data",
            ops_dir=tmp_path / "ops",
        )
        t += timedelta(minutes=5)
    return c, [q for q in srv.requests if q.url.path.endswith("/tweets")]


def test_staggered_slate_league_handle_never_asks_for_the_future_n3r(tmp_path: Path) -> None:
    # the 19:40 ET (23:40Z) and 20:40 ET (00:40Z) posts are about the 22:00 ET game
    p1 = tweet("8001", "chatter one", "2026-10-28T23:40:00.000Z")
    p2 = tweet("8002", "chatter two", "2026-10-29T00:40:00.000Z")
    c, reqs = _staggered_run(tmp_path, [p2, p1])
    assert reqs
    polls = rows(
        tmp_path / "f" / "s.duckdb",
        "SELECT started_at, start_time, status, n_posts FROM poll_log ORDER BY poll_id",
    )
    assert len(polls) >= 4
    for started, start_time, status, _ in polls:  # no poll ever asks for a start after "now"
        assert start_time <= started, (started, start_time, status)
    # a poll after the first tip (23:00Z, 19:00 ET) still reaches back over the 19:40 ET post,
    # and the 20:40 ET post about the 22:00 ET game is read
    after_first_tip = [p for p in polls if p[0] > datetime(2026, 10, 28, 23, 0)]
    assert after_first_tip and any(p[1] <= datetime(2026, 10, 28, 23, 40) for p in after_first_tip)
    raw_ids = {
        post_.post_id
        for f in (tmp_path / "data" / "raw").rglob("*.parquet")
        for post_ in r.read_raw(f)
    }
    assert {"8001", "8002"} <= raw_ids


def test_poll_bounds_never_return_a_start_later_than_now_n3r(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 20, 50, tzinfo=UTC)
    c = client(tmp_path, Paged([], 5), clock=lambda: now)
    same_evening = datetime(2026, 10, 28, 23, 0)  # prev tip + 3 h is in the future
    start, _ = c.poll_bounds("RocketsBeat", now, prev_tip=same_evening)
    assert start <= now - timedelta(seconds=r.CURSOR_OVERLAP_S)
    far_future_cursor = (now + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    c.state.update("RocketsBeat", last_ok_poll_at=far_future_cursor)
    assert c.poll_bounds("RocketsBeat", now)[0] <= now - timedelta(seconds=r.CURSOR_OVERLAP_S)


def test_parse_only_drops_a_post_whose_text_differs_from_the_indexed_capture_n9(
    tmp_path: Path,
) -> None:
    import polars as pl

    from nba.daily.schedule import save_schedule_cache

    created, honest = datetime(2026, 10, 28, 15, 0), datetime(2026, 10, 28, 22, 45)
    r.write_raw(tmp_path / "d" / "raw", "RocketsBeat", [post("9001", created, honest)], honest)
    # a later backfill fetch holds the SAME post id with other text (a substituted post)
    late = datetime(2026, 10, 29, 3, 0)
    swapped = post("9001", created, late, "Alperen Sengun is out tonight.")
    bf = r.write_raw(tmp_path / "d" / "raw_backfill", "RocketsBeat", [swapped], late)
    assert bf is not None
    allow = tmp_path / "reporters.yaml"
    allow.write_text(json.dumps({"handles": [{"handle": "RocketsBeat", "team": "HOU"}]}))
    ros = tmp_path / "rosters" / "2026-10-10"
    ros.mkdir(parents=True)
    pl.DataFrame(
        {
            "team_id": [HOU] * len(HOU_ROSTER),
            "player_id": [p for _, p in HOU_ROSTER],
            "exp": ["3"] * len(HOU_ROSTER),
            "player_name": [n for n, _ in HOU_ROSTER],
        }
    ).write_parquet(ros / f"{HOU}.parquet")
    cache = tmp_path / "schedule_cache"
    save_schedule_cache([PREV, TONIGHT], rf.season_for(datetime(2026, 10, 28)), cache)
    store = tmp_path / "facts" / "reporter_facts.duckdb"
    rc = r.main(
        [
            "parse-only", "--file", str(bf), "--allowlist", str(allow),
            "--data-dir", str(tmp_path / "d"), "--rosters", str(tmp_path / "rosters"),
            "--schedule-cache", str(cache), "--aliases", str(tmp_path / "none.yaml"),
            "--store", str(store),
        ]
    )  # fmt: skip
    assert rc == 0 and rows(store, "SELECT * FROM reporter_facts") == []
    # the honest copy of the same post, replayed from the live raw file, is written
    live_file = next((tmp_path / "d" / "raw").rglob("*.parquet"))
    rc = r.main(
        [
            "parse-only", "--file", str(live_file), "--allowlist", str(allow),
            "--data-dir", str(tmp_path / "d"), "--rosters", str(tmp_path / "rosters"),
            "--schedule-cache", str(cache), "--aliases", str(tmp_path / "none.yaml"),
            "--store", str(store),
        ]
    )  # fmt: skip
    got = rows(store, "SELECT DISTINCT predicate FROM reporter_facts ORDER BY 1")
    assert rc == 0 and ("reporter_minutes_cap",) in got and ("reporter_status",) not in got
