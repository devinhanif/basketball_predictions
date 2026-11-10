# ruff: noqa: E501
"""Player checkpoints (T-180 / T-30 / T-5): timing, idempotency, lateness, after-tip refusal, diffing,
rate-limit deferral, Discord length cuts, and the disabled / no-webhook no-op.

Every webhook URL is made up and every HTTP call goes to a scripted stand-in; the forecast and
injury tables are tiny fixtures in tmp_path. The posted messages are read from the stand-in's calls.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.jarvis.cards import players as P
from nba.jarvis.cards import run as R
from nba.jarvis.cards import text as T
from nba.jarvis.config import load_config
from nba.jarvis.runner import Runtime, build_state, tick_line
from tests.jarvis.cards.test_run import Ids, at, make_rt, scoreboard, wurl
from tests.jarvis.cards.test_webhooks_and_client import Resp
from tests.jarvis.conftest import G1, LAC, LEBRON, SLATE, TIP1

TEAMS2 = ("LAL", "LAC")
PL = 1002  # a LAC rotation player
T180, T30, T5 = (TIP1 - timedelta(minutes=m) for m in (180, 30, 5))


def pred(mean: float, minutes: float = 30.0, p_play: float = 0.97) -> str:
    return json.dumps({"mean": mean, "proj_minutes": minutes, "p_play": p_play, "q_grid": [0.0]})


def seed(root: Path, *, later: bool = True) -> None:
    """Forecast rows (props_context_residual) for LeBron, an OUT player with a stale row, and a LAC
    player; plus, when ``later``, a NEWER forecast row and report row that exist only after T-180."""
    con = duckdb.connect(str(root / "nba.duckdb"))
    con.execute(
        "CREATE TABLE forward_predictions (game_id VARCHAR, tipoff TIMESTAMP, model_name VARCHAR, "
        "target VARCHAR, player_id INTEGER, prediction VARCHAR, made_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO player_game_stats VALUES ('0022600001', ?, ?, 28, 12, 4, 3, 1)", [PL, LAC]
    )
    made = datetime(2026, 10, 28, 12, 0)
    for pid, base in ((LEBRON, 25.0), (1000, 10.0), (PL, 12.0)):
        for tgt, mult in (("pts", 1.0), ("reb", 0.25), ("ast", 0.3), ("fg3m", 0.1)):
            con.execute(
                "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?)",
                [G1, TIP1, "props_context_residual", tgt, pid, pred(base * mult), made],
            )
    # a different model's row must never be used
    con.execute(
        "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?)",
        [G1, TIP1, "props_recency_v1", "pts", LEBRON, pred(99.0), made],
    )
    if later:
        for tgt, mult in (("pts", 1.0), ("reb", 0.25), ("ast", 0.3), ("fg3m", 0.1)):
            con.execute(  # LeBron's forecast moves up after T-180 (made 22:00Z, before T-30)
                "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?)",
                [G1, TIP1, "props_context_residual", tgt, LEBRON, pred(30.0 * mult, 35.0, 0.9),
                 datetime(2026, 10, 28, 22, 0)],
            )  # fmt: skip
        con.execute(  # the LAC player is newly ruled OUT at 23:00Z (a status change)
            "INSERT INTO player_availability VALUES (?,?,?,'out','Injury/Illness-Ankle','nba_official_report',?)",
            [
                PL,
                datetime(2026, 10, 28, 19, 0),
                G1,
                datetime(2026, 10, 28, 19, 0),
            ],  # 23:00Z as US-Eastern wall clock
        )
    con.close()


def cfg_pcp(rt: Runtime, tmp: Path, **over: Any) -> dict[str, Any]:
    base = dict(rt.cfg.team_cards["player_checkpoints"])
    return {**base, "state": str(tmp / "p" / "pcp.jsonl"), **over}


def mk(rt: Runtime, tmp: Path, http: Ids, **over: Any) -> Runtime:
    """Cards off (post_lead_min 0) so only player checkpoint posts reach the stand-in."""
    return make_rt(
        rt, tmp, http, TEAMS2, post_lead_min=0, player_checkpoints=cfg_pcp(rt, tmp, **over)
    )


def tick(rt: Runtime, when: datetime) -> dict[str, Any]:
    at(rt, when)
    st = build_state(rt, SLATE)
    return R.run_cards(rt, st, SLATE, rt.clock(), None)["player_checkpoints"]  # type: ignore[no-any-return]


def posted(http: Ids) -> list[tuple[str, dict[str, Any]]]:
    """(webhook url, embed) of every PLAYER CHECKPOINT message posted (cards are not counted)."""
    return [
        (c[1], c[2]["embeds"][0])
        for c in http.of("POST")
        if " players · T-" in c[2]["embeds"][0]["title"]
    ]


# ------------------------------------------------------------------------------ timing (pure)


def test_window_state_boundaries() -> None:
    tip = TIP1
    ws = lambda now, rec=False: P.window_state(now, tip, 30, 6.0, 1.0, rec)  # noqa: E731
    assert ws(T30 - timedelta(seconds=1)) == "wait"
    assert ws(T30) == "due"  # inclusive start
    assert ws(T30 + timedelta(minutes=6)) == "due"  # inclusive end
    assert ws(T30 + timedelta(minutes=6, seconds=1)) == "late"
    assert ws(tip - timedelta(seconds=1)) == "late"
    assert ws(tip) == "over" and ws(tip + timedelta(minutes=5)) == "over"  # never at or after tip
    assert ws(T30 + timedelta(minutes=1), rec=True) == "done"
    # T-5 with a 3 minute tolerance closes at T-2; a 10 minute tolerance is cut by the tip guard
    assert P.window_state(tip - timedelta(minutes=2), tip, 5, 3.0, 1.0, False) == "due"
    assert P.window_state(tip - timedelta(minutes=1, seconds=30), tip, 5, 3.0, 1.0, False) == "late"
    assert P.window_state(tip - timedelta(minutes=1), tip, 5, 10.0, 1.0, False) == "due"
    assert P.window_state(tip - timedelta(seconds=30), tip, 5, 10.0, 1.0, False) == "late"


def test_the_shipped_config_is_enabled_and_valid() -> None:
    cfg = load_config()
    pc = P.pcp_config(cfg)
    assert pc.enabled and pc.minutes == (180, 30, 5) and pc.min_p_play == 0.5
    assert all(pc.tolerance[m] > 0 for m in pc.minutes)
    assert not any(
        pc.tolerance[a] >= a - b for a, b in zip(pc.minutes, pc.minutes[1:], strict=False)
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"minutes": [30, 180], "lateness_tolerance_min": {30: 1, 180: 1}},
        {"minutes": [180, 30], "lateness_tolerance_min": {180: 10}},
        {"minutes": [180, 30], "lateness_tolerance_min": {180: 200, 30: 5}},
        {"minutes": [180, 30], "lateness_tolerance_min": {180: 10, 30: 5}, "min_p_play": 2},
    ],
)
def test_validate_refuses_a_bad_block(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        P.validate({"state": "x", **bad})


# ------------------------------------------------------------------------------ the pass


def test_a_tick_inside_the_window_posts_once_per_team_silently_and_is_idempotent(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    rep = tick(rt, T180 + timedelta(minutes=1))
    assert rep["posted"] == 2
    msgs = posted(http)
    assert sorted(u for u, _ in msgs) == sorted(wurl(t) + "?wait=true" for t in TEAMS2)
    assert all(c[2]["flags"] == 4096 for c in http.of("POST"))  # silent, same flag as cards
    assert all(c[2]["allowed_mentions"] == {"parse": []} for c in http.of("POST"))  # no ping
    lal = next(e for u, e in msgs if "LAL" in u)
    assert lal["title"].startswith("LAL players · T-180") and "First look" in lal["description"]
    # known-at: the as-of time of the forecasts, the injury report and the lineups is in the message
    d = lal["description"]
    assert "Forecasts as of" in d and "injury report as of" in d and "Starters are not flagged" in d
    # the same tick again, and a later tick inside the window: nothing more is posted
    n = len(posted(http))
    assert tick(rt, T180 + timedelta(minutes=1)).get("posted", 0) == 0
    assert tick(rt, T180 + timedelta(minutes=3)).get("posted", 0) == 0
    assert len(posted(http)) == n
    # a fresh process re-reads the state file: still nothing
    rt2 = mk(rt, tmp_path, http)
    assert tick(rt2, T180 + timedelta(minutes=4)).get("posted", 0) == 0 and len(posted(http)) == n


def test_known_at_a_checkpoint_uses_only_rows_made_before_it(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)  # a newer LeBron forecast (22:00Z) and a new OUT (23:00Z) exist in the DB
    http = Ids()
    mk(rt, tmp_path, http)
    tick(rt, T180 + timedelta(minutes=1))  # 21:31Z
    lal = next(e for u, e in posted(http) if "LAL" in u)["description"]
    assert "25.0/6.2/7.5/2.5" in lal and "35 min" not in lal and "99.0" not in lal
    lac = next(e for u, e in posted(http) if "LAC" in u)["description"]
    assert "Player 1002" in lac and "OUT" not in lac.split("Player 1002")[1].split("\n")[0]


def test_a_missed_window_is_skipped_logged_once_and_never_posted_late(
    rt: Runtime, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    with caplog.at_level("WARNING"):
        rep = tick(rt, T180 + timedelta(minutes=11))  # tolerance is 10
    assert rep["skipped_late"] == 2 and rep["posted"] == 0 and not posted(http)
    assert any("missed its window" in r.message for r in caplog.records)
    assert tick(rt, T180 + timedelta(minutes=12)).get("skipped_late", 0) == 0  # logged once
    assert not posted(http)
    recs = [json.loads(x) for x in Path(P.pcp_config(rt.cfg).state).read_text().splitlines()]
    assert sorted((r["team"], r["minute"], r["kind"]) for r in recs) == [
        ("LAC", 180, "skipped_late"),
        ("LAL", 180, "skipped_late"),
    ]


def test_nothing_posts_at_or_after_tip_or_once_the_scoreboard_says_it_began(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    for when in (TIP1, TIP1 + timedelta(minutes=1), TIP1 + timedelta(minutes=90)):
        assert tick(rt, when).get("posted", 0) == 0
    assert not posted(http)
    # the game began early on the scoreboard while the clock still says T-4: refused
    t = TIP1 - timedelta(minutes=4)
    scoreboard(rt.cfg.root, "STATUS_IN_PROGRESS", 2, 0, t - timedelta(minutes=1))
    assert tick(rt, t).get("posted", 0) == 0 and not posted(http)


def test_each_checkpoint_fires_once_and_the_second_diffs_against_the_first(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    assert tick(rt, T180 + timedelta(minutes=2))["posted"] == 2
    assert tick(rt, T30 + timedelta(minutes=1))["posted"] == 2
    second = {("LAL" if "LAL" in u else "LAC"): e for u, e in posted(http)[2:]}
    assert len(http.of("POST")) == 4 and http.of("PATCH") == []  # posts, never edits
    d = second["LAL"]["description"]
    assert "Change since the T-180 post" in d
    # LeBron's forecast moved after T-180 (pts 25 -> 30, minutes 30 -> 35, chance to play 97 -> 90%)
    line = next(x for x in d.split("\n") if x.startswith("**LeBron James**"))
    assert "30.0/7.5/9.0/3.0" in line and "35 min" in line and "plays 90%" in line
    assert "change: " in line and "min up 5" in line and "pts up 5.0" in line
    assert "plays 97% -> 90%" in line
    # the LAC player was newly ruled OUT at 23:00Z: listed (status changed) with the status change
    lac = second["LAC"]["description"]
    out = next(x for x in lac.split("\n") if x.startswith("**Player 1002**"))
    assert "OUT (ankle)" in out and "status not on report -> out" in out
    # T-5 diffs against the nearest posted checkpoint (T-30), not T-180
    assert tick(rt, T5 + timedelta(minutes=1))["posted"] == 2
    third = next(e for u, e in posted(http)[4:] if "LAL" in u)["description"]
    assert "Change since the T-30 post" in third
    assert "change: none" in next(x for x in third.split("\n") if x.startswith("**LeBron James**"))
    assert tick(rt, T5 + timedelta(minutes=2)).get("posted", 0) == 0


def test_a_skipped_checkpoint_is_passed_over_when_diffing(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    assert tick(rt, T180 + timedelta(minutes=1))["posted"] == 2
    assert tick(rt, T30 + timedelta(minutes=8))["skipped_late"] == 2  # tolerance 6
    assert tick(rt, T5 + timedelta(minutes=1))["posted"] == 2
    last = next(e for u, e in posted(http)[2:] if "LAL" in u)["description"]
    assert "Change since the T-180 post" in last


def kinds(rt: Runtime) -> list[tuple[str, int, str]]:
    path = Path(P.pcp_config(rt.cfg).state)
    if not path.exists():
        return []
    recs = [json.loads(x) for x in path.read_text().splitlines()]
    return sorted((r["team"], r["minute"], r["kind"]) for r in recs)


def test_a_429_over_the_budget_defers_without_recording_then_posts_next_tick(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root)
    http = Ids(Resp(429, {"retry_after": 600.0, "global": False}, {}))
    make_rt(rt, tmp_path, http, ("LAL",), post_lead_min=0, player_checkpoints=cfg_pcp(rt, tmp_path))
    rep = tick(rt, T30 + timedelta(minutes=1))
    assert rep.get("posted", 0) == 0 and rep["deferred"] == 1
    assert ("LAL", 30, "posted") not in kinds(rt)  # nothing recorded for T-30: not dropped
    rep2 = tick(rt, T30 + timedelta(minutes=3))  # still inside the 6 minute tolerance
    assert rep2["posted"] == 1 and ("LAL", 30, "posted") in kinds(rt)
    assert [k for k in kinds(rt) if k[1] == 30] == [("LAL", 30, "posted")]  # recorded once
    assert len(posted(http)) == 2  # the 429 attempt, then exactly one accepted post
    # a defer that runs past the window becomes a logged skip, never a late post
    http2 = Ids(Resp(429, {"retry_after": 600.0, "global": False}, {}))
    make_rt(rt, tmp_path / "x", http2, ("LAL",), post_lead_min=0,
            player_checkpoints=cfg_pcp(rt, tmp_path / "x"))  # fmt: skip
    assert tick(rt, T5 + timedelta(minutes=1))["deferred"] == 1
    assert tick(rt, T5 + timedelta(minutes=4))["skipped_late"] >= 1
    assert ("LAL", 5, "skipped_late") in kinds(rt)
    assert len(posted(http2)) == 1  # only the refused attempt; nothing was posted late


def test_the_pass_shares_the_card_budget(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    rt.cfg = replace(rt.cfg, team_cards={**rt.cfg.team_cards, "tick_budget_s": 0})
    rep = tick(rt, T30 + timedelta(minutes=1))
    assert rep.get("posted", 0) == 0 and not posted(http)  # budget spent: deferred to the next tick
    assert rep["deferred"] == 2


def test_disabled_and_no_webhook_are_no_ops(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http, enabled=False)
    assert tick(rt, T30 + timedelta(minutes=1)) == {"skipped": "disabled"}
    assert not posted(http) and not Path(P.pcp_config(rt.cfg).state).exists()
    http2 = Ids()
    rt3 = mk(rt, tmp_path, http2)
    rt3.env = {}  # no webhooks at all
    at(rt3, T30 + timedelta(minutes=1))
    rep = R.run_cards(rt3, build_state(rt3, SLATE), SLATE, rt3.clock(), None)
    assert rep["skipped"] == "no team webhooks configured" and "player_checkpoints" not in rep
    assert not http2.calls
    # a team without a webhook is skipped, not an error
    http3 = Ids()
    make_rt(rt, tmp_path / "y", http3, ("LAL",), post_lead_min=0,
            player_checkpoints=cfg_pcp(rt, tmp_path / "y", enabled=True))  # fmt: skip
    rep3 = tick(rt, T30 + timedelta(minutes=1))
    assert rep3["posted"] == 1 and "LAL" in posted(http3)[0][0]


def test_a_dry_run_posts_and_writes_nothing(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    rt.dry_run = True
    rep = tick(rt, T30 + timedelta(minutes=1))
    assert rep.get("would_post") == 2 and not posted(http)
    assert not Path(P.pcp_config(rt.cfg).state).exists()


# ------------------------------------------------------------------------------ the message


def build(rt: Runtime, minute: int, now: datetime) -> tuple[P.GameData, P.PcpConfig, T.Style]:
    at(rt, now)
    st = build_state(rt, SLATE)
    g = st.game(G1)
    assert g is not None
    pc = P.pcp_config(rt.cfg)
    store = P.read_store(rt.cfg, [G1], now)
    return P.gather_game(rt.cfg, st, rt.kb, g, now, minute, pc, store), pc, R.style_of(rt.cfg)


def test_a_line_carries_projection_status_chance_price_ev_and_the_change(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root, later=False)
    mk(rt, tmp_path, Ids())
    from tests.jarvis.conftest import NOW

    gd, pc, style = build(rt, 30, NOW)  # 20:00Z: the Kalshi price (19:58Z) is fresh
    row = next(r for r in gd.rows["LAL"] if r.pid == LEBRON)
    assert row.means["pts"] == 25.0 and row.minutes == 30.0 and row.p_play == 0.97
    ln = row.lines["pts"]
    assert ln.line == 25.0 and ln.ask_c == pytest.approx(44.0)
    # the EV is the cards' EV: the same single_candidate on the same quote (P(plays) supplied)
    from nba.jarvis import slate as S
    from nba.jarvis.cards import facts as F

    st = build_state(rt, SLATE)
    q = next(x for x in st.tradable(rt.cfg) if x.player_id == LEBRON)
    play = S.single_candidate(
        rt.cfg, replace(st, model=P._WithPPlay(st.model, {LEBRON: 0.97})), q, "yes"
    )
    assert play is not None and ln.ev_c == pytest.approx(play.edge_cents)
    assert F.is_capped(play) is False
    embed, snap = P.build_message(gd, "LAL", 30, NOW, None, pc, style)
    text = next(x for x in embed["description"].split("\n") if x.startswith("**LeBron James**"))
    assert "25.0/6.2/7.5/2.5" in text and "30 min" in text and "plays 97%" in text
    assert "25+ pts @44c" in text and f"EV {ln.ev_c:+.1f}c" in text and "change:" not in text
    assert snap["players"][str(LEBRON)]["lines"]["pts"] == [25.0, 44.0]


def test_confirmed_starters_are_flagged_from_t30_and_only_when_confirmed(
    rt: Runtime, tmp_path: Path
) -> None:
    seed(rt.cfg.root, later=False)
    mk(rt, tmp_path, Ids())
    from tests.jarvis.conftest import NOW

    gd30, pc, style = build(rt, 30, NOW)
    lebron = next(r for r in gd30.rows["LAL"] if r.pid == LEBRON)
    assert lebron.starter is True  # confirmed on the snapshot
    others = [r for r in gd30.rows["LAL"] if r.pid != LEBRON]
    assert others and all(r.starter is None for r in others)  # one name: no bench call
    e30, _ = P.build_message(gd30, "LAL", 30, NOW, None, pc, style)
    assert "starter" in next(x for x in e30["description"].split("\n") if "LeBron" in x)
    gd180, pc, style = build(rt, 180, NOW)
    e180, _ = P.build_message(gd180, "LAL", 180, NOW, None, pc, style)
    assert "Starters are not flagged this early" in e180["description"]
    assert "starter" not in next(
        x for x in e180["description"].split("\n") if "LeBron" in x
    ).replace("starters", "")


def test_diff_tags_name_each_kind_of_move() -> None:
    pc = P.pcp_config(load_config())
    line = P.Line(25.0, 44.0, 0.5, 2.0, 1.0)
    row = P.PRow(1, "A", "LAL", "questionable", "knee", 0.6, 30.0, {"pts": 20.0, "reb": 5.0},
                 True, {"pts": line}, True)  # fmt: skip
    prev = {"minute": 180, "snap": {"report": {"1": "probable"}, "players": {"1": {
        "status": "probable", "p_play": 0.9, "min": 34.0, "mean": {"pts": 22.0, "reb": 5.1},
        "starter": None, "lines": {"pts": [24.0, 40.0]}}}}}  # fmt: skip
    tags = P.diff_tags(row, prev, pc)
    assert "status probable -> questionable" in tags and "min down 4" in tags
    assert "pts down 2.0" in tags and "plays 90% -> 60%" in tags
    assert "confirmed starter" in tags and "pts line 24+ -> 25+" in tags
    assert not any(t.startswith("reb") for t in tags)  # 0.1 is under the move threshold
    same = P.PRow(1, "A", "LAL", "probable", "knee", 0.9, 34.0, {"pts": 22.0, "reb": 5.1},
                  None, {"pts": P.Line(24.0, 41.0, 0.5, 2.0, 1.0)}, True)  # fmt: skip
    assert P.diff_tags(same, prev, pc) == []  # a 1c ask move is not news
    moved = P.PRow(1, "A", "LAL", "probable", "", 0.9, 34.0, {"pts": 22.0, "reb": 5.1},
                   None, {"pts": P.Line(24.0, 44.0, 0.5, 2.0, 1.0)}, True)  # fmt: skip
    assert P.diff_tags(moved, prev, pc) == ["pts ask 40 -> 44c"]


def test_listing_rule_min_p_play_and_status_changes() -> None:
    pc = P.pcp_config(load_config())

    def mk_row(pid: int, pp: float | None, st: str | None) -> P.PRow:
        return P.PRow(pid, f"P{pid}", "LAL", st, "", pp, 20.0, {}, None, {}, pp is not None)

    rows = [
        mk_row(1, 0.9, None),
        mk_row(2, 0.4, None),
        mk_row(3, 0.3, "out"),
        mk_row(4, None, "out"),
    ]
    assert [r.pid for r in P.listed(rows, {3: "out", 4: "out"}, None, pc)] == [1]
    prev = {"snap": {"report": {"3": "questionable"}}}
    # 3 went questionable -> out (changed, listed); 4 is new on the report (changed, listed); 2 stays unlisted
    assert [r.pid for r in P.listed(rows, {3: "out", 4: "out"}, prev, pc)] == [1, 3, 4]


def test_the_message_stays_inside_discords_limits_and_says_how_many_were_cut() -> None:
    from nba.jarvis.cards.embed import DESC_MAX, TOTAL_MAX, total_chars

    pc = P.pcp_config(load_config())
    style = T.Style()
    rows = [
        P.PRow(i, "Very Long Name " * 3 + str(i), "LAL", "questionable", "right finger sprain", 0.8,
               30.0 - i * 0.1, {"pts": 20.0, "reb": 5.0, "ast": 3.0, "fg3m": 2.0}, True,
               {s: P.Line(10.0, 44.0, 0.5, 1.5, 1.0) for s in ("pts", "reb", "ast", "fg3m")}, True)
        for i in range(60)
    ]  # fmt: skip
    from tests.jarvis.conftest import NOW

    class FakeGame:
        away_abbr, home_abbr, tipoff = "LAL", "LAC", TIP1

    gd = P.GameData(FakeGame())  # type: ignore[arg-type]
    gd.rows["LAL"], gd.report["LAL"] = rows, {}
    prev = {"minute": 180, "ts": "2026-10-28T21:30:00", "snap": {"report": {}, "players": {}}}
    embed, _ = P.build_message(gd, "LAL", 30, NOW, prev, pc, style)
    assert len(embed["description"]) <= DESC_MAX and total_chars(embed) <= TOTAL_MAX
    assert "more players" in embed["description"]
    shown = sum(1 for x in embed["description"].split("\n") if x.startswith("**Very Long"))
    cut = int(embed["description"].rsplit("+", 1)[1].split(" ")[0])
    assert shown + cut == 60 and shown > 5


def test_the_preview_renders_and_sends_and_writes_nothing(rt: Runtime, tmp_path: Path) -> None:
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    at(rt, T30 + timedelta(minutes=1))
    st = build_state(rt, SLATE)
    cards = P.render_players(rt, st, SLATE, rt.clock(), 30)
    assert len(cards) == 2 and cards[0]["phase"] == "players T-30"
    assert "PREVIEW, not sent" in cards[0]["embeds"]["LAL"]["description"]
    assert not posted(http) and not Path(P.pcp_config(rt.cfg).state).exists()
    assert len(P.render_players(rt, st, SLATE, rt.clock(), 30, "LAL@LAC")) == 1


# ------------------------------------------------------------------------------ hygiene


def test_the_tick_line_counts_posted_and_late_checkpoints() -> None:
    rep = {"at": "x", "plays": 0, "cards": {"player_checkpoints": {"posted": 4, "skipped_late": 2}}}
    line = tick_line(rep)
    assert "pcp=4posted" in line and "pcp_skipped_late=2" in line
    assert "pcp" not in tick_line(
        {"at": "x", "plays": 0, "cards": {"player_checkpoints": {"skipped": "disabled"}}}
    )


def test_the_module_never_edits_a_message() -> None:
    src = Path(P.__file__).read_text()
    assert ".edit(" not in src and "PATCH" not in src and "edit_message" not in src


def test_a_stale_tick_clock_never_posts_after_tip(rt: Runtime, tmp_path: Path) -> None:
    """Review M1: the tick read its clock at T-2.5 but the card pass runs after tip; re-checked."""
    seed(rt.cfg.root)
    http = Ids()
    mk(rt, tmp_path, http)
    stale = TIP1 - timedelta(minutes=4)
    at(rt, stale)
    st = build_state(rt, SLATE)
    at(rt, TIP1 + timedelta(minutes=1))  # the real clock has moved past tip
    rep = R.run_cards(rt, st, SLATE, stale, None)["player_checkpoints"]
    assert rep.get("posted", 0) == 0 and not posted(http)
    assert rep.get("skipped_late", 0) >= 1


def test_report_as_of_is_eastern_wall_clock() -> None:
    """Review M2: player_availability.as_of is naive US-Eastern; the bound and display are UTC."""
    assert P.to_report_dt(datetime(2026, 11, 10, 17, 15)) == datetime(2026, 11, 10, 12, 15)
    assert P._et_to_utc(datetime(2026, 11, 10, 12, 15)) == datetime(2026, 11, 10, 17, 15)


def test_maybe_delivered_only_after_the_request_could_have_left() -> None:
    from nba.jarvis.cards.discord import CardResult

    assert P._maybe_delivered(CardResult(False, "deferred", note="transport ReadTimeout"))
    assert P._maybe_delivered(CardResult(False, "deferred", code=502, note="discord HTTP 502"))
    assert not P._maybe_delivered(CardResult(False, "deferred", note="transport ConnectError"))
    assert not P._maybe_delivered(CardResult(False, "deferred", code=429, note="rate limited"))
    assert not P._maybe_delivered(
        CardResult(False, "deferred", note="card pass stopped for this tick")
    )
