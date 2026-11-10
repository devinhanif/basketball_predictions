"""Halftime legs through the full card pass, and the alert mention."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from nba.jarvis.cards import run as R
from nba.jarvis.runner import Runtime, build_state
from tests.jarvis.cards.test_run import Ids, at, make_rt, run, scoreboard
from tests.jarvis.conftest import G1, NOW, SLATE, TIP1


def test_the_halftime_card_applies_the_scorers_rule_to_each_leg(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    rt.cfg = replace(
        rt.cfg,
        halftime_overs={
            "enabled": True, "min_edge": 0.05, "top_n": 3, "display_min_bid_c": 1,
            "display_max_spread_c": 10, "artifact_digest": "x",
        },
    )  # fmt: skip
    rt.ht_scored = {
        "ok": True,
        "rows": [
            {
                "game": "LAL@LAC", "player_name": "LeBron James", "stat": "pts", "k": 25,
                "ask_c": 52, "bid_c": 48, "fee_c": 2, "p_model": 0.61, "edge_model": 0.07,
                "cur": 14,
            }
        ],
    }  # fmt: skip
    t_half = TIP1 + timedelta(minutes=70)
    scoreboard(rt.cfg.root, "STATUS_HALFTIME", 55, 48, t_half)
    st = build_state(at(rt, t_half), SLATE)
    R.run_cards(rt, st, SLATE, t_half, None)
    e = http.of("PATCH")[-1][2]["embeds"][0]
    leg = next(f for f in e["fields"] if f["name"] == "Halftime entry check")["value"]
    assert "14 of 25" in leg and "needs 11 more, on pace" in leg
    assert "live ask 52c (bid 48c) vs model 61%" in leg and "**Enter**" in leg
    assert "+7c after the fee, over the 5c bar" in leg


def test_a_strong_alert_for_the_game_is_mentioned_on_the_card_not_re_sent(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    rt.book.record_send(
        kind="alert", mode="sent", slate=SLATE, keys=[f"{G1}/kalshi:T:yes/strong"], play_ids=[],
        text="t", inputs=[], note="", now=NOW,
    )  # fmt: skip
    run(rt)
    g1 = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    assert "🔔 A strong-edge alert for this game went to the main channel." in g1["description"]
    g2 = next(c for c in http.of("POST") if "OKC" in c[1])[2]["embeds"][0]
    assert "🔔" not in g2["description"]


def test_a_quiet_tick_never_reads_the_knowledge_stores(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live games (calm: no edits) and a pre-tip card younger than the refresh interval need no
    card at all, so the 2-minute job does not open nba.duckdb or the lineup store for them."""
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    calls: list[str] = []
    real = R.build_card
    monkeypatch.setattr(R, "build_card", lambda *a, **k: calls.append("built") or real(*a, **k))
    run(at(rt, NOW + timedelta(minutes=2)))  # pre-tip, card 2 minutes old
    assert calls == []
    t_live = TIP1 + timedelta(minutes=40)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 30, 28, t_live)
    rep = run(at(rt, t_live))
    assert calls == ["built"]  # only the OTHER game (still pre-tip, card older than 15 min)
    assert rep["cards"]["unchanged"] >= 2 and not any(
        p[2]["embeds"][0]["description"].startswith("🔴") for p in http.of("PATCH")
    )


def test_a_game_that_ends_after_the_et_date_rolls_still_gets_its_final_card(
    rt: Runtime, tmp_path: Path
) -> None:
    from datetime import date, datetime

    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)  # pre-tip cards on the 28th
    t_fin = datetime(2026, 10, 29, 3, 30)  # 11:30 PM ET on the 28th
    scoreboard(rt.cfg.root, "STATUS_FINAL", 110, 104, t_fin)
    morning = datetime(2026, 10, 29, 12, 0)  # 8 AM ET: the slate is now the 29th
    nxt = date(2026, 10, 29)
    st = build_state(at(rt, morning), nxt)
    rep = R.run_cards(rt, st, nxt, morning, None)
    assert rep["cards"]["edited"] == 2
    fin = http.of("PATCH")[-1][2]["embeds"][0]
    assert fin["description"].startswith("🏁 **Final** — LAL 110, LAC 104")
    again = R.run_cards(rt, st, nxt, morning, None)  # nothing left to do: no edits
    assert again["cards"].get("edited", 0) == 0


def test_an_over_is_logged_only_once_a_card_showed_it(rt: Runtime, tmp_path: Path) -> None:
    from tests.jarvis.cards.test_webhooks_and_client import Resp

    http = Ids(*[Resp(429, {"retry_after": 99.0}) for _ in range(4)])  # every post is deferred
    make_rt(rt, tmp_path, http)
    rep = run(rt)
    st = R.state_of(rt.cfg)
    assert st.picks(SLATE) == []
    assert rep["cards"].get("deferred", 0) == 4 and "posted" not in rep["cards"]
    run(rt)  # next tick: the posts go through, and now the over is logged at the price shown
    assert [p["player"] for p in st.picks(SLATE)] == ["LeBron James"]
