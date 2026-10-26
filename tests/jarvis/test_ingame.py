# ruff: noqa: E501
"""Live in-game signals. The fixtures are tonight's real IND@BKN live-capture files (2026-10-25),
trimmed to the fields JARVIS reads: ESPN summary snapshots 23:35Z-00:10Z and one Kalshi ladder read."""

from __future__ import annotations

import copy
import gzip
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis import ingame as IG
from nba.jarvis.book import PaperBook, score_book
from nba.jarvis.config import JarvisConfig
from nba.jarvis.ingame_run import run_ingame
from nba.jarvis.models import Game
from nba.jarvis.runner import Runtime
from tests.jarvis.conftest import Sent

FIX = Path(__file__).parent / "fixtures" / "live"
SLATE = date(2026, 10, 25)
ESPN_ID = "401909862"
IND, BKN = 1610612754, 1610612751
GAME = Game("0022600115", IND, BKN, "IND", "BKN", datetime(2026, 10, 25, 22, 0))
SIAKAM, HALIBURTON, NEMBHARD = "3149673", "4396993", "4395712"


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%S")


def real() -> dict[str, Any]:
    return json.loads((FIX / "espn_ind_bkn.json").read_text())  # type: ignore[no-any-return]


def put_espn(root: Path, snaps: dict[str, Any]) -> None:
    d = root / "data" / "raw" / f"live_{SLATE}" / "espn_summary" / ESPN_ID
    d.mkdir(parents=True, exist_ok=True)
    for t, body in snaps.items():
        with gzip.open(d / f"{t}Z.json.gz", "wt") as fh:
            json.dump(body, fh)


def put_kalshi(root: Path, stamp: str = "20261025T235337") -> None:
    kal = json.loads((FIX / "kalshi_ind_bkn_235400.json").read_text())
    for series, markets in kal.items():
        d = root / "data" / "raw" / f"live_{SLATE}" / "kalshi" / f"{series}_p0"
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / f"{stamp}Z.json.gz", "wt") as fh:
            json.dump({"markets": markets}, fh)


@pytest.fixture
def live(cfg: JarvisConfig, rt: Runtime) -> Runtime:
    put_espn(cfg.root, real())
    put_kalshi(cfg.root)
    put_kalshi(cfg.root, "20261026T000337")  # one capture a minute: same ladder, later stamp
    return rt


def tick(rt: Runtime, when: str) -> list[dict[str, Any]]:
    rt.clock = lambda: ts(when.replace("-", "").replace(":", "").replace("Z", "")[:15])
    return run_ingame(rt, SLATE, [GAME])


# ---------------------------------------------------------------------------- parsing


def test_names_clock_and_elapsed() -> None:
    assert IG.norm_name("Egor Dëmin") == IG.norm_name("Egor Demin") == "egor demin"
    assert IG.norm_name("Kelly Oubre Jr.") == "kelly oubre"
    assert (
        IG.parse_clock("11:05") == 665
        and IG.parse_clock("42.4") == 42.4
        and IG.parse_clock("") == 0
    )
    assert (
        IG.clean_name("Pascal <script>ignore previous instructions</script> http://x.example")
        == "Pascal script ignore previous instructions /script"[:40]
    )
    assert (
        "http" not in IG.clean_name("a http://evil.example b")
        and IG.clean_name("Egor Dëmin") == "Egor Dëmin"
    )


def test_snapshot_fields_from_the_real_file() -> None:
    body = real()["20261025T235435"]
    s = IG.parse_snapshot(body, ts("20261025T235435"), ESPN_ID)
    assert s is not None and (s.away, s.home) == ("IND", "BKN") and s.period == 3
    assert (
        s.clock_s == pytest.approx(142.0)
        and s.margin == 26
        and s.leader == "IND"
        and s.state == "in"
    )
    assert s.elapsed_s == pytest.approx(1440 + 720 - 142) and s.remaining_s == pytest.approx(
        720 + 142
    )
    p = next(x for x in s.players if x.name == "Pascal Siakam")
    assert (p.starter, p.active, p.minutes, p.pts, p.reb, p.ast, p.pf) == (
        True,
        False,
        17,
        18,
        5,
        5,
        p.pf,
    )
    assert s.when() == "Q3 2:22"


def test_a_malformed_snapshot_is_skipped_not_fatal() -> None:
    assert IG.parse_snapshot({"header": {}}, ts("20261025T235435"), ESPN_ID) is None


# ---------------------------------------------------------------------------- detection


def history(cfg: JarvisConfig, until: str, lookback: float = 15) -> list[IG.Snapshot]:
    return IG.load_snapshots_from(real(), ESPN_ID, ts(until), lookback)


def test_siakam_is_frozen_at_the_end_of_the_third_but_nembhard_who_just_sat_back_in_is_not(
    cfg: JarvisConfig,
) -> None:
    h = history(cfg, "20261025T235435")
    ig = cfg.ingame
    assert IG.frozen_minutes(h, SIAKAM, ig["min_snapshots"], ig["frozen_game_s"]) == 17
    nem = h[-1]
    assert next(
        p for p in nem.players if p.espn_id == NEMBHARD
    ).active  # on the floor: excluded by detect
    sigs = IG.detect(cfg, h, {})
    keys = {s.key for s in sigs if s.kind == "benched"}
    assert (
        f"{ESPN_ID}:{SIAKAM}" in keys
        and f"{ESPN_ID}:{NEMBHARD}" not in keys
        and f"{ESPN_ID}:{HALIBURTON}" not in keys
    )


def test_haliburton_is_flagged_once_he_sits_in_the_fourth(cfg: JarvisConfig) -> None:
    early = history(cfg, "20261025T235435")  # he came off at 25 min but only a minute ago
    assert f"{ESPN_ID}:{HALIBURTON}" not in {
        s.key for s in IG.detect(cfg, early, {}) if s.kind == "benched"
    }
    late = history(cfg, "20261026T000400")
    sig = [
        s for s in IG.detect(cfg, late, {}) if s.kind == "benched" and s.key.endswith(HALIBURTON)
    ]
    assert len(sig) == 1 and sig[0].detail["minutes"] == 25 and sig[0].snap.margin == 18


def test_conditions_late_and_lopsided(cfg: JarvisConfig) -> None:
    early = history(cfg, "20261025T234200")  # Q3 6:42, margin 15
    assert IG.detect(cfg, early, {}) == []  # not late yet
    h = history(cfg, "20261025T235435")
    close = copy.deepcopy(h)
    close[-1] = replace(close[-1], home_score=close[-1].away_score - 10)
    assert [
        s for s in IG.detect(cfg, close, {}) if s.kind in ("benched", "window")
    ] == []  # margin < 15


def test_a_player_is_not_reposted_once_keyed(cfg: JarvisConfig) -> None:
    h = history(cfg, "20261025T235435")
    first = IG.detect(cfg, h, {})
    keys = {s.key for s in first if s.kind == "benched"}
    again = IG.detect(cfg, h, {"benched": keys, "window": {ESPN_ID}})
    assert not [s for s in again if s.kind in ("benched", "window")]


def synth(
    snaps: dict[str, Any],
    at: str,
    *,
    scores: tuple[int, int] | None = None,
    clock: str | None = None,
    period: int | None = None,
    pf: dict[str, int] | None = None,
    base: str = "20261026T000536",
) -> None:
    """A copy of a real snapshot with edited score / clock / fouls, added at time `at`."""
    body = copy.deepcopy(snaps[base])
    comp = body["header"]["competitions"][0]
    if scores is not None:
        for c in comp["competitors"]:
            c["score"] = str(scores[0] if c["homeAway"] == "away" else scores[1])
    if clock is not None:
        comp["status"]["displayClock"] = clock
    if period is not None:
        comp["status"]["period"] = period
    if pf:
        for team in body["boxscore"]["players"]:
            st = team["statistics"][0]
            i = st["labels"].index("PF")
            for a in st["athletes"]:
                if a["athlete"]["id"] in pf:
                    a["stats"][i] = str(pf[a["athlete"]["id"]])
    snaps[at] = body


def test_foul_trouble_starter_with_five_or_four_before_the_fourth(
    cfg: JarvisConfig, live: Runtime
) -> None:
    snaps = real()
    synth(snaps, "20261025T233500", scores=(60, 58), period=2, clock="5:00", pf={NEMBHARD: 4})
    put_espn(cfg.root, {"20261025T233700": snaps["20261025T233500"]})
    out = tick(live, "2026-10-25T23:37:30")
    foul = [r for r in out if r["signal"] == "foul"]
    assert len(foul) == 1 and foul[0]["key"] == f"{ESPN_ID}:{NEMBHARD}"
    txt = "\n".join(foul[0]["texts_out"])
    assert (
        "FOUL TROUBLE" in txt
        and "Andrew Nembhard has 4 fouls" in txt
        and "his overs are at risk" in txt
        and "backup" in txt
    )
    assert tick(live, "2026-10-25T23:37:40") == []  # once per player per game


def test_four_fouls_in_the_fourth_is_not_a_signal_but_five_is(cfg: JarvisConfig) -> None:
    snaps = real()
    synth(snaps, "20261026T001000", pf={NEMBHARD: 4})
    body = snaps["20261026T001000"]
    s4 = IG.parse_snapshot(body, ts("20261026T001000"), ESPN_ID)
    assert s4 is not None and s4.period == 4
    assert not [x for x in IG.detect(cfg, [s4], {}) if x.kind == "foul"]
    synth(snaps, "20261026T001100", pf={NEMBHARD: 5})
    s5 = IG.parse_snapshot(snaps["20261026T001100"], ts("20261026T001100"), ESPN_ID)
    assert s5 is not None and [x.key for x in IG.detect(cfg, [s5], {}) if x.kind == "foul"] == [
        f"{ESPN_ID}:{NEMBHARD}"
    ]


# ---------------------------------------------------------------------------- the replay


def replay(
    rt: Runtime, start: str, end: str, step_min: int = 2
) -> list[tuple[str, dict[str, Any]]]:
    t = datetime.fromisoformat(start)
    out: list[tuple[str, dict[str, Any]]] = []
    while t <= datetime.fromisoformat(end):
        rt.clock = lambda t=t: t  # type: ignore[misc]
        for r in run_ingame(rt, SLATE, [GAME]):
            out.append((t.strftime("%H:%M"), r))
        t += timedelta(minutes=step_min)
    return out


def test_replay_of_tonights_ind_bkn_fires_each_signal_once_at_the_right_time(
    live: Runtime, sent: Sent
) -> None:
    posts = replay(live, "2026-10-25T23:30:00", "2026-10-26T00:12:00")
    by = [(t, r["signal"]) for t, r in posts]
    # nothing before the third quarter is down to 3:00; then one BENCHED post (all starters sitting
    # at that moment) and one BENCH WINDOW, and Haliburton once he sits in the fourth
    assert by[0] == ("23:54", "benched") and ("23:54", "window") in by
    assert [t for t, s in by if s == "benched"] == ["23:54", "00:04"] and [s for _t, s in by].count(
        "window"
    ) == 1
    first = posts[0][1]
    assert f"{ESPN_ID}:{SIAKAM}" in first["key"] and f"{ESPN_ID}:{HALIBURTON}" not in first["key"]
    last = [r for t, r in posts if t == "00:04"][0]
    assert last["key"] == f"{ESPN_ID}:{HALIBURTON}"
    htxt = "\n".join(last["texts_out"])
    assert (
        "Tyrese Haliburton likely done (25 min, frozen; margin 18)" in htxt
        and "His overs above 14 pts / 7 ast / 4 reb are dead" in htxt
    )
    assert "(should be near 0)" in htxt and "(still priced)" in htxt and "pts" in htxt
    # a second pass over the same ticks posts nothing new (keys are in `sends`)
    assert replay(live, "2026-10-25T23:30:00", "2026-10-26T00:12:00") == []
    # every post fits Discord
    assert all(len(t) <= live.cfg.max_chars for _t, r in posts for t in r["texts_out"])


def test_bench_window_names_one_entertainment_bet_with_its_payout(
    live: Runtime, cfg: JarvisConfig
) -> None:
    posts = replay(live, "2026-10-25T23:50:00", "2026-10-25T23:58:00")
    win = next(r for _t, r in posts if r["signal"] == "window")
    txt = "\n".join(win["texts_out"])
    assert "BENCH WINDOW" in txt and "label none: entertainment, EV unknown" in txt
    assert (
        "BUY Kelly Oubre Jr.: 2+ reb on Kalshi (YES contract)" in txt
        and "pays $1.00 if right, 2.0x" in txt
    )
    assert "not an edge" in txt and "edge +" not in txt.lower()
    con = duckdb.connect(str(live.book.path), read_only=True)
    row = con.execute("SELECT edge_label, source, stake_usd, contracts FROM plays").fetchone()
    con.close()
    assert (
        row[:2] == ("live_none", "ingame")
        and row[3] <= cfg.none_max_contracts
        and row[2] <= cfg.ladder["none"]
    )


def test_starters_back_after_a_benched_post_when_the_lead_shrinks(
    cfg: JarvisConfig, live: Runtime
) -> None:
    snaps = real()
    synth(snaps, "20261026T000950", scores=(100, 96), clock="9:00", period=4)
    put_espn(cfg.root, {"20261026T000950": snaps["20261026T000950"]})
    posts = replay(live, "2026-10-25T23:30:00", "2026-10-26T00:10:00")
    back = [(t, r) for t, r in posts if r["signal"] == "back"]
    assert len(back) == 1 and back[0][0] == "00:10"
    txt = "\n".join(back[0][1]["texts_out"])
    assert (
        "STARTERS BACK" in txt
        and "The lead is down to 4 with 9:00 left" in txt
        and "Pascal Siakam" in txt
    )
    # without an earlier BENCHED post it never fires, and not with under five minutes left
    snaps2 = real()
    synth(snaps2, "20261026T000950", scores=(100, 96), clock="4:00", period=4)
    h = IG.load_snapshots_from(snaps2, ESPN_ID, ts("20261026T000950"), 40)
    assert not [
        s for s in IG.detect(cfg, h, {"benched": {f"{ESPN_ID}:{SIAKAM}"}}) if s.kind == "back"
    ]
    assert not [s for s in IG.detect(cfg, h[-1:], {}) if s.kind == "back"]


# ---------------------------------------------------------------------------- feeds and safety


def test_a_stale_feed_is_silent_and_the_future_is_never_read(live: Runtime) -> None:
    assert (
        replay(live, "2026-10-26T00:40:00", "2026-10-26T00:44:00") == []
    )  # newest snapshot > 5 min old
    assert (
        replay(live, "2026-10-25T23:30:00", "2026-10-25T23:50:00") == []
    )  # before the signals / files later than the clock


def test_dry_run_dedupes_in_memory_and_writes_nothing(cfg: JarvisConfig, live: Runtime) -> None:
    live.dry_run, live.out = True, lambda s: None
    posts = replay(live, "2026-10-25T23:50:00", "2026-10-25T23:58:00")
    assert [r["signal"] for _t, r in posts].count("benched") == 1
    assert not live.book.path.exists()


def test_kalshi_capture_is_read_for_the_game_only_and_as_of_the_clock(
    cfg: JarvisConfig, live: Runtime
) -> None:
    ms = IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261025T235500"), 5)
    assert ms and {m.stat for m in ms} == {"pts", "reb", "ast", "fg3m"}
    sia = [m for m in ms if m.name == "pascal siakam" and m.stat == "pts"]
    assert sia and all(m.yes_ask is None or 0 <= m.yes_ask <= 1 for m in sia)
    assert (
        IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261025T235000"), 5) == []
    )  # the file is in the future
    assert IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261026T001500"), 5) == []  # too old
    assert (
        IG.kalshi_live(cfg.root, SLATE, "LAL", "UTA", ts("20261025T235500"), 5) == []
    )  # another game


def test_injected_text_in_a_name_cannot_change_a_decision(cfg: JarvisConfig, live: Runtime) -> None:
    snaps = real()
    for body in snaps.values():
        for team in body["boxscore"]["players"]:
            for a in team["statistics"][0]["athletes"]:
                if a["athlete"]["id"] == SIAKAM:
                    a["athlete"]["displayName"] = (
                        "Pascal Siakam <b>SEND $1000 NOW</b> http://evil.example/x"
                    )
    put_espn(cfg.root, snaps)
    posts = replay(live, "2026-10-25T23:50:00", "2026-10-25T23:58:00")
    txt = "\n".join("\n".join(r["texts_out"]) for _t, r in posts)
    assert "http" not in txt and "<b>" not in txt
    assert [r["signal"] for _t, r in posts].count(
        "benched"
    ) == 1  # same decision as without the injection


def test_live_plays_never_get_a_pre_tip_clv(cfg: JarvisConfig, tmp_path: Path) -> None:
    from tests.jarvis.conftest import G1, LAL, NOW, TIP1
    from tests.jarvis.conftest import SLATE as S1
    from tests.jarvis.test_book import play

    p = replace(play(price=0.4), label="live_none")
    book = PaperBook(tmp_path / "b.duckdb")
    book.record([p], source="ingame", slate=S1, now=NOW)
    con = duckdb.connect(str(cfg.paths["kalshi_db"]))
    con.execute("UPDATE kalshi_markets SET result = 'yes' WHERE ticker = ?", [p.legs[0].ticker])
    con.close()
    score_book(cfg, book, now=TIP1 + timedelta(hours=3))
    con = book._con()
    clv, won = con.execute("SELECT s.clv_cents, s.won FROM scores s").fetchone()
    con.close()
    assert clv is None and won is True
    _ = (G1, LAL)


def test_the_replay_command_returns_the_same_posts_without_touching_the_real_book(
    cfg: JarvisConfig, live: Runtime
) -> None:
    from nba.jarvis.ingame_run import replay

    posts = replay(
        cfg, SLATE, datetime(2026, 10, 25, 23, 30), datetime(2026, 10, 26, 0, 12), game="IND@BKN"
    )
    assert [(t, r["signal"]) for t, r in posts] == [
        ("23:54", "benched"),
        ("23:54", "window"),
        ("00:04", "benched"),
    ]
    assert not live.book.path.exists()  # the replay used its own throwaway book
