# ruff: noqa: E501
"""Live in-game signals. The fixtures are tonight's real IND@BKN live-capture files (2026-10-25),
trimmed to the fields JARVIS reads: ESPN summary snapshots 23:35Z-00:10Z and one Kalshi ladder read."""

from __future__ import annotations

import copy
import gzip
import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis import ingame as IG
from nba.jarvis.book import PaperBook, kill_switch, score_book
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
    for stamp in ("20261026T000337", "20261026T002037", "20261026T002537"):
        put_kalshi(cfg.root, stamp)  # one capture a minute: same ladder, later stamps
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


def benched_keys(cfg: JarvisConfig, until: str) -> set[str]:
    return {s.key for s in IG.detect(cfg, history(cfg, until), {}) if s.kind == "benched"}


def test_siakam_at_q3_2_53_is_not_flagged_any_more(cfg: JarvisConfig) -> None:
    """The first rule flagged him here and the adversary's replay found 47% of such flags played again."""
    h = history(cfg, "20261025T235435")
    ig = cfg.ingame
    fm = IG.frozen_minutes(h, SIAKAM, ig["min_snapshots"], ig["frozen_game_s"])
    assert fm is not None and fm[0] == 17 and fm[1] >= 180  # he WAS frozen: that is not enough
    assert h[-1].period == 3
    assert f"{ESPN_ID}:{SIAKAM}" not in benched_keys(cfg, "20261025T235435")
    assert [s for s in IG.detect(cfg, h, {}) if s.kind in ("benched", "window")] == []


def test_haliburton_at_q4_11_05_is_not_flagged_any_more(cfg: JarvisConfig) -> None:
    h = history(cfg, "20261026T000400")
    assert h[-1].when() == "Q4 11:05" and h[-1].margin == 18
    assert f"{ESPN_ID}:{HALIBURTON}" not in benched_keys(cfg, "20261026T000400")


def test_only_the_last_six_minutes_of_the_fourth_can_flag(cfg: JarvisConfig) -> None:
    assert cfg.ingame["benched_max_clock_s"] == 360
    keys = benched_keys(cfg, "20261026T002236")
    assert f"{ESPN_ID}:4017837" in keys  # Zubac, 7 min all night, Q4 5:44, +19
    q = history(cfg, "20261026T002236")[-1]
    assert q.period == 4 and q.clock_s <= 360
    # the same state with 6:01 left, or a 14-point lead, or in the 3rd: no flag
    for kw in ({"clock_s": 361.0}, {"home_score": q.away_score - 14}, {"period": 3}):
        assert not IG.late_and_lopsided(replace(q, **kw), 15, 360)
    assert IG.late_and_lopsided(q, 15, 360) and IG.late_and_lopsided(
        replace(q, period=5, clock_s=200.0), 15, 360
    )


def test_nembhard_who_just_sat_back_in_is_not_flagged(cfg: JarvisConfig) -> None:
    h = history(cfg, "20261025T235435")
    assert next(p for p in h[-1].players if p.espn_id == NEMBHARD).active


def test_conditions_late_and_lopsided(cfg: JarvisConfig) -> None:
    early = history(cfg, "20261025T234200")  # Q3 6:42, margin 15
    assert IG.detect(cfg, early, {}) == []  # not late yet
    h = history(cfg, "20261026T002236")
    close = copy.deepcopy(h)
    close[-1] = replace(close[-1], home_score=close[-1].away_score - 10)
    assert [s for s in IG.detect(cfg, close, {}) if s.kind in ("benched", "window")] == []


def test_a_player_is_not_reposted_once_keyed(cfg: JarvisConfig) -> None:
    h = history(cfg, "20261026T002236")
    first = IG.detect(cfg, h, {})
    keys = {s.key for s in first if s.kind == "benched"}
    assert keys
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
    active: dict[str, bool] | None = None,
    minutes: dict[str, int] | None = None,
    base: str,
) -> None:
    """A copy of a real snapshot with edited score / clock / fouls / floor state, added at `at`."""
    body = copy.deepcopy(snaps[base])
    comp = body["header"]["competitions"][0]
    if scores is not None:
        for c in comp["competitors"]:
            c["score"] = str(scores[0] if c["homeAway"] == "away" else scores[1])
    if clock is not None:
        comp["status"]["displayClock"] = clock
    if period is not None:
        comp["status"]["period"] = period
    for team in body["boxscore"]["players"]:
        st = team["statistics"][0]
        labels = st["labels"]
        for a in st["athletes"]:
            pid = a["athlete"]["id"]
            if pf and pid in pf:
                a["stats"][labels.index("PF")] = str(pf[pid])
            if active and pid in active:
                a["active"] = active[pid]
            if minutes and pid in minutes:
                a["stats"][labels.index("MIN")] = str(minutes[pid])
    snaps[at] = body


def key_at(snaps: dict[str, Any], prefix: str) -> str:
    """The latest real snapshot key at or before `prefix` (keys carry the capture's own seconds)."""
    return max(k for k in snaps if k <= prefix)


def test_foul_trouble_starter_with_four_before_the_fourth(cfg: JarvisConfig, live: Runtime) -> None:
    snaps = real()
    synth(
        snaps,
        "20261025T234200",
        period=2,
        clock="5:00",
        scores=(60, 58),
        pf={NEMBHARD: 4},
        base=key_at(snaps, "20261025T235435"),
    )
    put_espn(cfg.root, {"20261025T234200": snaps["20261025T234200"]})
    out = tick(live, "2026-10-25T23:42:30")
    foul = [r for r in out if r["signal"] == "foul"]
    assert len(foul) == 1 and foul[0]["key"] == f"{ESPN_ID}:{NEMBHARD}"
    txt = "\n".join(foul[0]["texts_out"])
    assert "FOUL TROUBLE" in txt and "Andrew Nembhard has 4 fouls" in txt
    assert "his minutes may be cut" in txt and "may play more minutes" in txt
    assert tick(live, "2026-10-25T23:42:40") == []  # once per player per game


def test_four_fouls_in_the_fourth_is_not_a_signal_but_five_is(cfg: JarvisConfig) -> None:
    snaps = real()
    base = key_at(snaps, "20261026T000536")
    synth(snaps, "20261026T001000", pf={NEMBHARD: 4}, base=base)
    s4 = IG.parse_snapshot(snaps["20261026T001000"], ts("20261026T001000"), ESPN_ID)
    assert s4 is not None and s4.period == 4
    assert not [x for x in IG.detect(cfg, [s4], {}) if x.kind == "foul"]
    synth(snaps, "20261026T001100", pf={NEMBHARD: 5}, base=base)
    s5 = IG.parse_snapshot(snaps["20261026T001100"], ts("20261026T001100"), ESPN_ID)
    assert s5 is not None and [x.key for x in IG.detect(cfg, [s5], {}) if x.kind == "foul"] == [
        f"{ESPN_ID}:{NEMBHARD}"
    ]


# ---------------------------------------------------------------------------- the replay


def replay_ticks(
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


FULL = ("2026-10-25T23:30:00", "2026-10-26T00:44:00")


def test_replay_of_tonights_ind_bkn_after_the_fix(live: Runtime, sent: Sent) -> None:
    posts = replay_ticks(live, *FULL)
    by = [(t, r["signal"]) for t, r in posts]
    # nothing in the 3rd or early 4th (Siakam, Haliburton and the rest sat in ordinary rest); the first
    # flags come with 5:44 left in the 4th, a second one at 4:08
    assert by == [("00:22", "benched"), ("00:22", "window"), ("00:26", "benched")]
    first = posts[0][1]
    assert (
        f"{ESPN_ID}:{SIAKAM}" not in first["key"] and f"{ESPN_ID}:{HALIBURTON}" not in first["key"]
    )
    txt = "\n".join(first["texts_out"])
    assert (
        "Ivica Zubac may be done for the night (7 min played" in txt
        and "Inference from minutes, not a certainty" in txt
    )
    assert replay_ticks(live, *FULL) == []  # a second pass posts nothing new
    assert all(len(t) <= live.cfg.max_chars for _t, r in posts for t in r["texts_out"])


def test_bench_window_names_one_entertainment_bet_with_its_payout(
    live: Runtime, cfg: JarvisConfig
) -> None:
    posts = replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:24:00")
    win = next(r for _t, r in posts if r["signal"] == "window")
    txt = "\n".join(win["texts_out"])
    assert "BENCH WINDOW" in txt and "label none: entertainment, EV unknown" in txt
    assert re.search(r"BUY Kelly Oubre Jr\.: \d\+ \w+ on Kalshi \(YES contract\) at \d+c", txt)
    assert re.search(r"pays \$\d+\.\d{2} if right, \d\.\dx", txt) and "not an edge" in txt
    con = duckdb.connect(str(live.book.path), read_only=True)
    row = con.execute("SELECT edge_label, source, stake_usd, contracts FROM plays").fetchone()
    con.close()
    assert (
        row[:2] == ("live_none", "ingame")
        and 1 <= row[3] <= cfg.none_max_contracts
        and row[2] <= cfg.ladder["none"]
    )


def test_a_bet_that_does_not_fit_is_never_recorded_and_uses_the_markets_own_series(
    live: Runtime, cfg: JarvisConfig
) -> None:
    from nba.jarvis.ingame_run import _bet

    snap = history(cfg, "20261026T002236")[-1]
    p = next(x for x in snap.players if x.name == "Kelly Oubre Jr.")
    m = IG.LiveMarket(
        "KXNBAREB-26OCT25INDBKN-X-2",
        "kelly oubre",
        "Kelly Oubre Jr.",
        "reb",
        2,
        0.4,
        0.5,
        snap.ts,
        "capture",
        "KXNBAREB",
    )
    assert _bet(live, GAME, snap, (0.1, p, m), room=0.0) is None  # $0: not recorded
    bet = _bet(live, GAME, snap, (0.1, p, m), room=20.0)
    assert bet is not None and bet.legs[0].series == "KXNBAREB" and bet.contracts >= 1


def test_a_returning_starter_gets_one_correction_and_the_night_is_counted(
    cfg: JarvisConfig, live: Runtime, sent: Sent
) -> None:
    snaps = real()
    base = key_at(snaps, "20261026T002300")
    # Nembhard (30 min when flagged at 00:22) is back on the floor a minute later
    synth(snaps, "20261026T002359", active={NEMBHARD: True}, minutes={NEMBHARD: 31}, base=base)
    put_espn(cfg.root, {"20261026T002359": snaps["20261026T002359"]})
    posts = replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:26:00")
    sig = [(t, r["signal"]) for t, r in posts]
    assert ("00:22", "benched") in sig
    corr = [r for t, r in posts if r["signal"] == "returned"]
    assert len(corr) == 1 and corr[0]["key"] == f"{ESPN_ID}:{NEMBHARD}"
    txt = "\n".join(corr[0]["texts_out"])
    assert (
        "CORRECTION" in txt
        and "Andrew Nembhard is back" in txt
        and "posted as 'may be done' at Q4 5:44" in txt
    )
    flagged, returned = live.book.ingame_counts(SLATE)
    assert returned == 1 and flagged >= 7
    from nba.jarvis.posts import recap_lines

    lines = recap_lines(cfg, live.book, SLATE, [], kill_switch(cfg, live.book))
    joined = "\n".join(lines)
    assert (
        f"flagged {flagged}, back on the floor 1" in joined
        and "judged on these forward nights" in joined
    )


def test_starters_back_after_a_benched_post_when_the_lead_shrinks(
    cfg: JarvisConfig, live: Runtime
) -> None:
    snaps = real()
    synth(
        snaps,
        "20261026T002400",
        scores=(100, 96),
        clock="5:30",
        period=4,
        base=key_at(snaps, "20261026T002300"),
    )
    put_espn(cfg.root, {"20261026T002400": snaps["20261026T002400"]})
    posts = replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:25:00")
    back = [(t, r) for t, r in posts if r["signal"] == "back"]
    assert len(back) == 1 and back[0][0] == "00:24"
    txt = "\n".join(back[0][1]["texts_out"])
    assert (
        "STARTERS BACK" in txt
        and "The lead is down to 4 with 5:30 left" in txt
        and "may return to the floor" in txt
    )
    # not with under five minutes left, and not without an earlier BENCHED post
    snaps2 = real()
    synth(
        snaps2,
        "20261026T002400",
        scores=(100, 96),
        clock="4:00",
        period=4,
        base=key_at(snaps2, "20261026T002300"),
    )
    h = IG.load_snapshots_from(snaps2, ESPN_ID, ts("20261026T002400"), 40)
    assert not [
        s for s in IG.detect(cfg, h, {"benched": {f"{ESPN_ID}:{SIAKAM}"}}) if s.kind == "back"
    ]
    assert not [s for s in IG.detect(cfg, h[-1:], {}) if s.kind == "back"]


# --------------------------------------------------------------------- wording (no edge language)

BANNED = (
    "still priced",
    "should be near",
    "are dead",
    "dead",
    " up.",
    "value",
    "mispriced",
    "overs are up",
    "alive",
)


def all_post_texts(live: Runtime, cfg: JarvisConfig) -> str:
    texts = [t for _t, r in replay_ticks(live, *FULL) for t in r["texts_out"]]
    snaps = real()
    synth(
        snaps,
        "20261025T234200",
        period=2,
        clock="5:00",
        scores=(60, 58),
        pf={NEMBHARD: 5},
        base=key_at(snaps, "20261025T235435"),
    )
    s = IG.parse_snapshot(snaps["20261025T234200"], ts("20261025T234200"), ESPN_ID)
    assert s is not None
    mk = IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261025T235500"), 5)
    from nba.jarvis import ingame_run as IR

    sig = [x for x in IG.detect(cfg, [s], {}) if x.kind == "foul"][0]
    texts += [
        "\n".join(IR.foul_lines(sig, mk)),
        "\n".join(IR.back_lines(IG.Signal("back", ESPN_ID, s, s.players[:2]))),
    ]
    texts += [
        "\n".join(
            IR.returned_lines(
                IG.Signal("returned", "k", s, s.players[:1], {"was": 30, "when": "Q4 5:44"})
            )
        )
    ]
    return "\n".join(texts)


def test_no_in_game_post_contains_edge_or_value_language(cfg: JarvisConfig, live: Runtime) -> None:
    text = all_post_texts(live, cfg)
    assert text.strip()
    low = text.lower().replace("not an edge", "")
    for w in BANNED:
        assert w not in low, w
    assert "edge" not in low


# ---------------------------------------------------------------------------- minors


def test_a_failed_post_is_retried_and_not_marked_seen(cfg: JarvisConfig, live: Runtime) -> None:
    from tests.jarvis.conftest import Sent

    live.sender = Sent(ok=False)
    first = replay_ticks(live, "2026-10-26T00:22:00", "2026-10-26T00:22:00")
    assert first and all(r["mode"] == "failed" for _t, r in first)
    live.sender = Sent(ok=True)
    again = replay_ticks(live, "2026-10-26T00:24:00", "2026-10-26T00:24:00")
    assert any(
        r["signal"] == "benched" and r["mode"] == "sent" for _t, r in again
    )  # retried, delivered


def test_a_busy_store_in_the_in_game_step_skips_only_that_step(
    cfg: JarvisConfig, rt: Runtime, sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nba.jarvis.ingame_run as IR
    from nba.jarvis.runner import run_tick
    from tests.jarvis.conftest import NOW
    from tests.jarvis.conftest import SLATE as S1

    def locked(*a: Any, **k: Any) -> Any:
        raise duckdb.IOException("Could not set lock")

    monkeypatch.setattr(IR, "run_ingame", locked)
    rep = run_tick(rt, S1)
    assert (
        rep.get("idle", "").startswith("store busy") is False and rep["plays"] >= 1
    )  # pre-tip part ran
    assert rep["ingame"][0]["error"] == "IOException"
    _ = NOW


def test_one_bad_game_does_not_silence_the_others(
    cfg: JarvisConfig, live: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nba.jarvis.ingame_run as IR

    second = real()
    d = cfg.root / "data" / "raw" / f"live_{SLATE}" / "espn_summary" / "401909999"
    d.mkdir(parents=True)
    for t, body in second.items():
        with gzip.open(d / f"{t}Z.json.gz", "wt") as fh:
            json.dump(body, fh)
    orig = IR._one_game
    calls = {"n": 0}

    def flaky(rt: Runtime, slate: date, hist: list[IG.Snapshot], games: Any, now: datetime) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad game")
        return orig(rt, slate, hist, games, now)

    monkeypatch.setattr(IR, "_one_game", flaky)
    live.clock = lambda: datetime(2026, 10, 26, 0, 22, 0)
    out = run_ingame(live, SLATE, [GAME])
    assert out[0]["signal"] == "error" and out[0]["error"] == "RuntimeError"
    assert any(r["signal"] == "benched" for r in out[1:])


def test_espn_to_kalshi_name_mismatches_are_counted_and_reported(
    cfg: JarvisConfig, live: Runtime
) -> None:
    from nba.jarvis.ingame_run import name_mismatches

    snap = history(cfg, "20261026T002236")[-1]
    ms = [
        IG.LiveMarket(
            "T", IG.norm_name("Nic Claxton"), "Nic Claxton", "pts", 10, 0.1, 0.2, snap.ts, "capture"
        )
    ]
    assert name_mismatches(snap, ms) == ["Nic Claxton"]
    assert name_mismatches(snap, []) == []
    posts = replay_ticks(live, "2026-10-26T00:22:00", "2026-10-26T00:22:00")
    assert all("name_mismatches" in r for _t, r in posts)


# ---------------------------------------------------------------------------- feeds and safety


def test_a_stale_feed_is_silent_and_the_future_is_never_read(live: Runtime) -> None:
    assert (
        replay_ticks(live, "2026-10-26T01:00:00", "2026-10-26T01:04:00") == []
    )  # newest snapshot > 5 min old
    assert (
        replay_ticks(live, "2026-10-25T23:30:00", "2026-10-25T23:50:00") == []
    )  # before any signal / files later than the clock


def test_dry_run_dedupes_in_memory_and_writes_nothing(cfg: JarvisConfig, live: Runtime) -> None:
    live.dry_run, live.out = True, lambda s: None
    posts = replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:24:00")
    assert [r["signal"] for _t, r in posts].count("benched") == 1
    assert replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:24:00") == []
    assert not live.book.path.exists()


def test_kalshi_capture_is_read_for_the_game_only_and_as_of_the_clock(
    cfg: JarvisConfig, live: Runtime
) -> None:
    ms = IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261025T235500"), 5)
    assert (
        ms
        and {m.stat for m in ms} == {"pts", "reb", "ast", "fg3m"}
        and all(m.series.startswith("KXNBA") for m in ms)
    )
    sia = [m for m in ms if m.name == "pascal siakam" and m.stat == "pts"]
    assert sia and all(m.yes_ask is None or 0 <= m.yes_ask <= 1 for m in sia)
    assert (
        IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261025T235000"), 5) == []
    )  # the file is in the future
    assert IG.kalshi_live(cfg.root, SLATE, "IND", "BKN", ts("20261026T010000"), 5) == []  # too old
    assert (
        IG.kalshi_live(cfg.root, SLATE, "LAL", "UTA", ts("20261025T235500"), 5) == []
    )  # another game


def test_injected_text_in_a_name_cannot_change_a_decision(cfg: JarvisConfig, live: Runtime) -> None:
    snaps = real()
    for body in snaps.values():
        for team in body["boxscore"]["players"]:
            for a in team["statistics"][0]["athletes"]:
                if a["athlete"]["id"] == "4017837":
                    a["athlete"]["displayName"] = (
                        "Ivica Zubac <b>SEND $1000 NOW</b> http://evil.example/x"
                    )
    put_espn(cfg.root, snaps)
    posts = replay_ticks(live, "2026-10-26T00:20:00", "2026-10-26T00:24:00")
    txt = "\n".join("\n".join(r["texts_out"]) for _t, r in posts)
    assert "http" not in txt and "<b>" not in txt and "Ivica Zubac" in txt
    assert [r["signal"] for _t, r in posts].count(
        "benched"
    ) == 1  # same decision as without the injection


def test_live_plays_never_get_a_pre_tip_clv(cfg: JarvisConfig, tmp_path: Path) -> None:
    from tests.jarvis.conftest import NOW, TIP1
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


def test_the_replay_command_returns_the_same_posts_without_touching_the_real_book(
    cfg: JarvisConfig, live: Runtime
) -> None:
    from nba.jarvis.ingame_run import replay

    posts = replay(
        cfg, SLATE, datetime(2026, 10, 25, 23, 30), datetime(2026, 10, 26, 0, 44), game="IND@BKN"
    )
    assert [(t, r["signal"]) for t, r in posts] == [
        ("00:22", "benched"),
        ("00:22", "window"),
        ("00:26", "benched"),
    ]
    assert not live.book.path.exists()
