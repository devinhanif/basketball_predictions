"""The card pass end to end on the fixture world: one card per game per team channel, edited in
place at halftime and the final, idempotent across restarts, calm, rate-limit aware, redacted.

Every webhook URL is made up and every HTTP call goes to a scripted stand-in.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis.cards import run as R
from nba.jarvis.cards.state import CardState
from nba.jarvis.config import JarvisConfig
from nba.jarvis.runner import Runtime, build_state, run_tick, tick_line
from tests.jarvis.cards.test_webhooks_and_client import Http, Resp
from tests.jarvis.conftest import G1, G2, NOW, SLATE, TIP1, TIP2, Sent

SECRET = "SECRETSECRET"
TEAMS4 = ("LAL", "LAC", "OKC", "DAL")


def wurl(team: str) -> str:
    n = 1000 + sum(map(ord, team))
    return f"https://discord.com/api/webhooks/{n}/{SECRET}{team}"


def env(*teams: str) -> dict[str, str]:
    return {"JARVIS_DISCORD_TEAM_WEBHOOKS": json.dumps({t: wurl(t) for t in teams})}


class Ids(Http):
    """Hands out a new message id per POST; PATCH echoes the id in its URL."""

    def __init__(self, *script: Any) -> None:
        super().__init__(*script)
        self.n = 0

    def request(self, method: str, target: str, json: dict[str, Any] | None = None) -> Resp:
        if self.script:
            return super().request(method, target, json)
        self.calls.append((method, target, json or {}))
        if method == "POST":
            self.n += 1
            return Resp(200, {"id": f"m{self.n}"})
        return Resp(200, {"id": target.rsplit("/", 1)[-1]})

    def of(self, method: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[0] == method]


def cards_cfg(cfg: JarvisConfig, tmp: Path, **over: Any) -> JarvisConfig:
    tc = {**cfg.team_cards, "post_lead_min": 600, "state": str(tmp / "c" / "state.jsonl"),
          "picks": str(tmp / "c" / "picks.jsonl"), **over}  # fmt: skip
    return replace(cfg, team_cards=tc)


def make_rt(
    rt: Runtime, tmp: Path, http: Ids, teams: tuple[str, ...] = TEAMS4, **over: Any
) -> Runtime:
    rt.cfg = cards_cfg(rt.cfg, tmp, **over)
    rt.env = env(*teams)
    rt.card_client = http
    rt.card_sleep = lambda s: None
    return rt


def at(rt: Runtime, t: datetime) -> Runtime:
    rt.clock = lambda: t
    return rt


def scoreboard(root: Path, status: str, away: int, home: int, when: datetime) -> None:
    sb = root / "data" / "raw" / f"live_{SLATE.isoformat()}" / "espn_scoreboard" / SLATE.isoformat()
    sb.mkdir(parents=True, exist_ok=True)

    def team(side: str, ab: str, sc: int) -> dict[str, Any]:
        return {"homeAway": side, "team": {"abbreviation": ab}, "score": str(sc)}

    body = {
        "events": [
            {
                "status": {"period": 2, "displayClock": "0.0", "type": {"name": status}},
                "competitions": [
                    {"competitors": [team("home", "LAC", home), team("away", "LAL", away)]}
                ],
            }
        ]
    }
    with gzip.open(sb / f"{when.strftime('%Y%m%dT%H%M%S')}Z.json.gz", "wt") as fh:
        json.dump(body, fh)


def state_of(rt: Runtime) -> CardState:
    return R.state_of(rt.cfg)


def run(rt: Runtime) -> dict[str, Any]:
    st = build_state(rt, SLATE)
    return R.run_cards(rt, st, SLATE, rt.clock(), None)


# ------------------------------------------------------------------------------ pre-tip


def test_a_card_posts_once_to_both_team_channels_with_a_message_id_recorded(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    rep = run(rt)
    posts = http.of("POST")
    assert rep["cards"] == {"posted": 4}  # two games, two channels each
    assert sorted(c[1] for c in posts) == sorted(wurl(t) + "?wait=true" for t in TEAMS4)
    by_game = {
        t: state_of(rt).latest(SLATE, g, t)
        for g, ts in ((G1, ("LAL", "LAC")), (G2, ("OKC", "DAL")))
        for t in ts
    }
    assert all(
        r is not None and r.message_id.startswith("m") and r.phase == "pretip"
        for r in by_game.values()
    )
    # the same game's card goes to both channels, with each channel's own stripe
    lal = next(c for c in posts if "LAL" in c[1])[2]["embeds"][0]
    lac = next(c for c in posts if "LAC" in c[1])[2]["embeds"][0]
    assert lal["title"] == lac["title"] and lal["color"] != lac["color"]
    assert all(c[2]["flags"] == 4096 for c in posts)  # silent


def test_the_pretip_card_shows_the_over_with_ev_and_the_other_game_says_keep_your_money(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    g1 = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    overs = g1["fields"][0]["value"]
    assert "LeBron James" in overs and "25+ pts" in overs and "EV +" in overs and "ask 44c" in overs
    g2 = next(c for c in http.of("POST") if "OKC" in c[1])[2]["embeds"][0]
    assert g2["fields"][0]["value"] == "No over is worth it tonight — keep your money."
    assert g1["footer"]["text"].endswith("Paper only. Not advice.")


def test_picks_are_logged_once_at_the_price_first_shown(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    run(rt)
    picks = state_of(rt).picks(SLATE)
    assert len(picks) == 1 and picks[0]["player"] == "LeBron James"
    assert picks[0]["ask"] == pytest.approx(0.44)


def test_a_restart_edits_nothing_and_posts_nothing_when_the_card_is_unchanged(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    first = len(http.calls)
    rep = run(rt)  # a fresh process would re-read the same state files
    assert rep["cards"] == {"unchanged": 4} and len(http.calls) == first


def test_a_pretip_refresh_waits_for_the_interval_then_edits_the_same_message(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    mid = state_of(rt).latest(SLATE, G1, "LAL").message_id  # type: ignore[union-attr]
    # a price moves: the card content changes
    import duckdb

    con = duckdb.connect(str(rt.cfg.paths["kalshi_db"]))
    con.execute("INSERT INTO kalshi_prices VALUES (?,?,0.41,0.43,NULL,0,0,'live')",
                ["KXNBAPTS-26OCT28LALLAC-LALLJAMES23-25", NOW + timedelta(minutes=5)])  # fmt: skip
    con.close()
    at(rt, NOW + timedelta(minutes=6))
    assert run(rt)["cards"] == {"unchanged": 4}  # inside the 15-minute interval: calm
    at(rt, NOW + timedelta(minutes=16))
    rep = run(rt)
    assert rep["cards"]["edited"] == 2 and rep["cards"]["unchanged"] == 2
    patches = http.of("PATCH")
    assert len(patches) == 2 and all(
        p[1].endswith(f"/messages/{mid}") or "/messages/m" in p[1] for p in patches
    )
    assert "flags" not in patches[0][2]  # an edit is silent by nature, never re-flagged


# ------------------------------------------------------------------------------ halftime, final


def test_halftime_and_final_edit_the_same_message_and_live_does_not(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    ids = {t: state_of(rt).latest(SLATE, G1, t).message_id for t in ("LAL", "LAC")}  # type: ignore[union-attr]
    n_post = len(http.of("POST"))
    t_live = TIP1 + timedelta(minutes=40)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 30, 28, t_live)
    assert run(at(rt, t_live))["cards"].get("edited", 0) == 0  # calm: no edit while live
    t_half = TIP1 + timedelta(minutes=70)
    scoreboard(rt.cfg.root, "STATUS_HALFTIME", 55, 48, t_half)
    rep = run(at(rt, t_half))
    assert rep["cards"]["edited"] == 2
    half = [p for p in http.of("PATCH") if p[2]["embeds"][0]["description"].startswith("🟡")]
    assert {p[1].rsplit("/", 1)[-1] for p in half} == set(ids.values())
    assert (
        "Halftime" in half[0][2]["embeds"][0]["description"]
        and "LAL 55, LAC 48" in half[0][2]["embeds"][0]["description"]
    )
    assert [f["name"] for f in half[0][2]["embeds"][0]["fields"]][:2] == [
        "Top overs by EV after fees (as shown pre-tip)", "Halftime entry check"]  # fmt: skip
    t_fin = TIP1 + timedelta(minutes=150)
    scoreboard(rt.cfg.root, "STATUS_FINAL", 110, 104, t_fin)
    rep = run(at(rt, t_fin))
    assert rep["cards"]["edited"] == 2
    fin = http.of("PATCH")[-1][2]["embeds"][0]
    assert fin["description"].startswith("🏁 **Final** — LAL 110, LAC 104")
    assert "Recap" in [f["name"] for f in fin["fields"]]
    assert len(http.of("POST")) == n_post  # never a second card for the same game
    assert run(at(rt, t_fin))["cards"].get("edited", 0) == 0  # stable: no more edits


def test_the_final_card_updates_again_when_the_box_score_settles_the_over(
    rt: Runtime, tmp_path: Path
) -> None:
    import duckdb

    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    t_fin = TIP1 + timedelta(minutes=150)
    scoreboard(rt.cfg.root, "STATUS_FINAL", 110, 104, t_fin)
    run(at(rt, t_fin))
    first = http.of("PATCH")[-1][2]["embeds"][0]
    assert "Waiting on the box score" in first["fields"][-2]["value"] or any(
        "Waiting on the box score" in f["value"] for f in first["fields"]
    )
    con = duckdb.connect(str(rt.cfg.root / "nba.duckdb"))
    con.execute("UPDATE games SET home_pts = 104, away_pts = 110 WHERE game_id = ?", [G1])
    con.execute("INSERT INTO player_game_stats VALUES (?, 2544, 1610612747, 35, 31, 6, 7, 2)", [G1])
    con.close()
    rep = run(at(rt, t_fin + timedelta(minutes=30)))
    assert rep["cards"]["edited"] == 2
    last = http.of("PATCH")[-1][2]["embeds"][0]
    rec = next(f for f in last["fields"] if f["name"] == "Recap")["value"]
    assert "this one hit; it was still a" in rec and "1 hit, 0 miss" in rec
    assert "Insufficient sample" in rec and "n = 1" in rec


def test_a_missing_edit_target_is_replaced_by_a_new_post_and_recorded(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    old = state_of(rt).latest(SLATE, G1, "LAL").message_id  # type: ignore[union-attr]
    http.script = [Resp(404, {"code": 10008})]  # the message was deleted in Discord
    t_half = TIP1 + timedelta(minutes=70)
    scoreboard(rt.cfg.root, "STATUS_HALFTIME", 55, 48, t_half)
    run(at(rt, t_half))
    new = state_of(rt).latest(SLATE, G1, "LAL")
    assert new is not None and new.message_id != old and new.kind == "repost"
    assert new.phase == "halftime"


def test_a_rotated_webhook_posts_a_fresh_card(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    rt.env = {"JARVIS_DISCORD_TEAM_WEBHOOKS": json.dumps({"LAL": wurl("LAL").replace("1", "2", 1)})}
    rep = run(rt)
    assert rep["cards"].get("reposted") == 1


def test_a_rate_limit_inside_the_budget_waits_and_one_past_it_defers_to_the_next_tick(
    rt: Runtime, tmp_path: Path
) -> None:
    slept: list[float] = []
    http = Ids(Resp(429, {"retry_after": 2.0}), Resp(200, {"id": "m1"}))
    make_rt(rt, tmp_path, http)
    rt.card_sleep = slept.append
    rep = run(rt)
    assert slept[0] == 2.0 and rep["rate_429"] == 1 and rep["cards"]["posted"] == 4
    # past the budget: nothing recorded, nothing failed, the next tick tries again
    tmp2 = tmp_path / "second"
    http2 = Ids(Resp(429, {"retry_after": 60.0}))
    rt2 = make_rt(rt, tmp2, http2)
    rep2 = run(rt2)
    assert rep2["cards"].get("deferred", 0) >= 1
    assert state_of(rt2).latest(SLATE, G1, "LAL") is None
    assert run(rt2)["cards"]["posted"] >= 1


# ------------------------------------------------------------------------------ safety and calm


def test_teams_without_a_webhook_are_skipped_counted_and_never_fail(
    rt: Runtime, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http, teams=("LAL", "OKC"))
    with caplog.at_level(logging.WARNING, logger="nba.jarvis.cards"):
        rep = run(rt)
    assert rep["cards"] == {"posted": 2} and rep["no_webhook_teams"] == ["DAL", "LAC"]
    assert any("2 team(s) have no webhook" in r.message for r in caplog.records)
    blob = " ".join(r.getMessage() for r in caplog.records) + json.dumps(rep, default=str)
    assert SECRET not in blob and "api/webhooks" not in blob


def test_with_no_webhooks_the_pass_is_a_silent_no_op(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    rt.env = {}
    rep = run(rt)
    assert rep == {"webhooks": 0, "skipped": "no team webhooks configured"}
    assert not http.calls and not (tmp_path / "c").exists()


def test_a_dry_run_sends_nothing_and_writes_nothing(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    rt.dry_run = True
    rep = run(rt)
    assert rep["cards"] == {"would_post": 4} and not http.calls
    assert not (tmp_path / "c").exists()


def test_a_card_failure_never_breaks_the_tick_and_the_log_has_no_url(
    rt: Runtime, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError(f"exploded near {wurl('LAL')}")

    monkeypatch.setattr(R, "build_card", boom)
    with caplog.at_level(logging.WARNING):
        rep = run_tick(rt, SLATE)
    assert "cards" in rep and rep["cards"]["errors"]
    assert "cards_error=" in tick_line(rep)
    assert SECRET not in caplog.text and SECRET not in json.dumps(rep, default=str)


def test_tick_line_counts_card_moves(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    rep = run_tick(rt, SLATE)
    assert "cards=4posted" in tick_line(rep)


def test_the_tick_never_adds_an_alert_condition(rt: Runtime, tmp_path: Path, sent: Sent) -> None:
    """Cards post to team channels via their own client; the main sender only gets what it got
    before (alerts, slate, halftime, recap) plus the one-time overview."""
    http = Ids()
    make_rt(rt, tmp_path, http)
    run_tick(rt, SLATE)
    kinds_before = list(sent.texts)
    rt2_dir = tmp_path / "off"
    rt_off = Runtime(
        **{**rt.__dict__, "sender": Sent(), "book": type(rt.book)(rt2_dir / "book.duckdb")}
    )
    rt_off.cfg = replace(rt.cfg, team_cards={"enabled": False})
    run_tick(rt_off, SLATE)
    off_texts = rt_off.sender.texts  # type: ignore[union-attr]
    extra = [t for t in kinds_before if t not in off_texts]
    assert all("JARVIS cards" in t for t in extra) and len(extra) <= 1


def test_the_overview_is_posted_once_and_is_not_an_alert(
    rt: Runtime, tmp_path: Path, sent: Sent
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run_tick(rt, SLATE)
    run_tick(rt, SLATE)
    ov = [t for t in sent.texts if t.startswith("**JARVIS cards**")]
    assert len(ov) == 1 and "Top over by EV after fees: LeBron James 25+ pts" in ov[0]
    assert ov[0].endswith("Paper only. Not advice.") and "ALERT" not in ov[0]


def test_main_halftime_posts_are_kept_by_default_and_skipped_only_when_asked_and_covered(
    rt: Runtime, tmp_path: Path
) -> None:
    from nba.jarvis.runner import _main_halftime_wanted

    http = Ids()
    make_rt(rt, tmp_path, http)
    g = next(x for x in build_state(rt, SLATE).games if x.game_id == G1)
    assert _main_halftime_wanted(rt, g) is True  # default keep_halftime_posts: true
    off = {"main_channel": {"keep_halftime_posts": False}}
    rt.cfg = cards_cfg(rt.cfg, tmp_path, **off)
    assert _main_halftime_wanted(rt, g) is False  # both team channels have a webhook
    rt.env = env("LAL")  # LAC missing: the card would not reach everyone, so the post stays
    assert _main_halftime_wanted(rt, g) is True


def test_no_card_text_or_state_holds_a_url(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    t_half = TIP1 + timedelta(minutes=70)
    scoreboard(rt.cfg.root, "STATUS_HALFTIME", 55, 48, t_half)
    run(at(rt, t_half))
    for p in (tmp_path / "c").iterdir():
        txt = p.read_text()
        assert SECRET not in txt and "discord.com" not in txt
    for _m, _u, body in http.calls:
        assert SECRET not in json.dumps(body)


def test_a_late_start_posts_a_live_card_from_the_frozen_log_or_says_it_cannot_know(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    t = TIP1 + timedelta(minutes=40)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 30, 28, t)
    run(at(rt, t))  # JARVIS was not running pre-tip
    post = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    assert post["description"].startswith("🔴 **Live**")
    assert (
        post["fields"][0]["value"]
        == "No pre-tip card was posted for this game, so there is no over to review."
    )


def test_game_tips_after_the_lead_window_get_no_card_yet(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http, post_lead_min=60)
    rep = run(rt)  # tips are 4.5 h and 6 h away
    assert rep.get("cards", {}) == {} and not http.calls
    at(rt, TIP2 - timedelta(minutes=30))
    rep = run(rt)
    assert rep["cards"]["posted"] >= 2
