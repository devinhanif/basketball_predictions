"""Fixes from the adversary review (docs/reviews/jarvis_team_cards_2026-11-10.md): one wall-clock
budget and a circuit for the card pass, the slate's own gates on card overs, the scorer's own
top_overs list as the Enter rule, no flip to "no over" when a price ages, a torn or malformed
state line, and the per-slot failure isolation."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import timedelta
from itertools import product
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from nba.jarvis import halftime_overs as HO
from nba.jarvis.cards import facts as F
from nba.jarvis.cards import run as R
from nba.jarvis.cards.state import CardRecord, CardState
from nba.jarvis.config import JarvisConfig
from nba.jarvis.runner import Runtime, run_tick
from tests.jarvis.cards.test_facts_recap_safety import HO_SET, over, row
from tests.jarvis.cards.test_run import Ids, at, make_rt, run
from tests.jarvis.cards.test_webhooks_and_client import Resp
from tests.jarvis.conftest import G1, NOW, SLATE, TIP1

KEY = "KXNBAPTS-26OCT28LALLAC-LALLJAMES23-25"


# ------------------------------------------------------------------------------ B1


class Slow(Ids):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    def request(self, method: str, target: str, json: dict[str, Any] | None = None) -> Resp:
        time.sleep(self.delay)
        return super().request(method, target, json)


class Storm(Ids):
    def request(self, method: str, target: str, json: dict[str, Any] | None = None) -> Resp:
        self.calls.append((method, target, json or {}))
        raise httpx.ReadTimeout(f"timed out talking to {target}")


def test_a_slow_transport_is_bounded_by_one_wall_clock_budget(rt: Runtime, tmp_path: Path) -> None:
    http = Slow(0.25)
    make_rt(rt, tmp_path, http, tick_budget_s=0.4)
    t0 = time.monotonic()
    rep = run(rt)
    took = time.monotonic() - t0
    assert took < 1.5  # 4 slots at 0.25 s each would be 1.0 s plus the work: the budget cut it
    assert len(http.calls) <= 3 and rep["cards"].get("deferred", 0) >= 1
    assert rep["stopped"] == "tick budget"


def test_the_first_network_error_stops_the_pass_for_the_rest_of_the_tick(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Storm()
    make_rt(rt, tmp_path, http)
    rep = run(rt)
    assert len(http.calls) == 1  # not one per slot
    assert rep["cards"] == {"deferred": 4} and rep["stopped"] == "broken"
    assert "ReadTimeout" not in json.dumps(rep) and "timed out talking" not in json.dumps(rep)
    run(rt)  # the next tick tries again, one request, no state written
    assert len(http.calls) == 2 and not (tmp_path / "c" / "state.jsonl").exists()


def test_a_server_error_also_opens_the_circuit(rt: Runtime, tmp_path: Path) -> None:
    http = Ids(Resp(503))
    make_rt(rt, tmp_path, http)
    rep = run(rt)
    assert len(http.calls) == 1 and rep["stopped"] == "broken"


def test_cards_run_after_alerts_halftime_and_the_in_game_pass(rt: Runtime, tmp_path: Path) -> None:
    make_rt(rt, tmp_path, Ids())
    rep = run_tick(rt, SLATE)
    keys = list(rep)
    assert keys.index("halftime") < keys.index("ingame") < keys.index("cards")


# ------------------------------------------------------------------------------ B2


def _fresh_kalshi(rt: Runtime, when: Any, ask: float = 0.44) -> None:
    con = duckdb.connect(str(rt.cfg.paths["kalshi_db"]))
    con.execute(
        "INSERT INTO kalshi_prices VALUES (?,?,?,?,NULL,0,0,'live')",
        [KEY, when, ask - 0.04, ask],
    )
    con.close()


def test_an_over_on_a_stale_pinnacle_line_is_neither_shown_nor_logged(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    later = NOW + timedelta(minutes=150)  # Pinnacle read at 19:30 is now 180 min old (> 120)
    _fresh_kalshi(rt, later - timedelta(minutes=1))
    run(at(rt, later))
    post = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    assert "LeBron" not in post["fields"][0]["value"]
    assert post["fields"][0]["value"] == "No fresh price to judge an over right now."
    assert R.state_of(rt.cfg).picks(SLATE) == []


def test_the_slate_previews_stale_reference_hold_applies_to_card_overs(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.jarvis import runner

    class Gate:
        limit = 15.0  # Pinnacle read 30 min ago is older than the pull rule

        def released(self, game_id: str, slate: Any) -> None:
            return None

    monkeypatch.setattr(runner, "_gate", lambda rt, now, flag: Gate())
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    post = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    assert "LeBron" not in post["fields"][0]["value"]
    assert R.state_of(rt.cfg).picks(SLATE) == []
    # a released hold (the pull ended without data) shows it again, as the preview does
    Gate.released = lambda self, g, s: object()  # type: ignore[method-assign,assignment]
    _fresh_kalshi(rt, NOW + timedelta(minutes=15))
    run(at(rt, NOW + timedelta(minutes=16)))
    assert [p["player"] for p in R.state_of(rt.cfg).picks(SLATE)] == ["LeBron James"]


def test_a_play_capped_at_thin_for_a_weak_input_is_recognised() -> None:
    from nba.jarvis.models import Play, PlayLeg

    def play(*notes: str) -> Play:
        leg = PlayLeg("kalshi", "T", "yes", 0.4, "prop", "x")
        return Play("g", "A@B", None, (leg,), "thin", 1.0, 0.5, 2.0, 0.4, 0, 0.0, "r", notes=notes)

    assert F.is_capped(play("Pinnacle line is 180 min old; label capped at thin"))
    assert F.is_capped(play("Pinnacle is 5 min newer than this price; capped at thin"))
    assert F.is_capped(play("prop DNP rule unverified and no P(plays): capped at thin"))
    assert not F.is_capped(
        play("fair price includes P(plays) because Kalshi's DNP rule is unverified")
    )
    assert not F.is_capped(play())


# ------------------------------------------------------------------------------ M1


def _ho(cfg: JarvisConfig) -> JarvisConfig:
    return replace(cfg, halftime_overs=dict(HO_SET))


def test_enter_needs_the_scorers_top_list_not_just_the_row_rule(cfg: JarvisConfig) -> None:
    c = _ho(cfg)
    rows = [
        row("Player A", 20, edge=0.20), row("Player B", 20, edge=0.15),
        row("Player C", 20, edge=0.10), row("LeBron James", 25, edge=0.06),
    ]  # fmt: skip
    legs = F.half_legs(c, [over()], rows, "LAL@LAC", ())
    lebron = next(leg for leg in legs if leg.player == "LeBron James")
    assert lebron.verdict == "skip" and "not among the scorer's top 3" in lebron.why
    assert [leg.player for leg in legs if leg.verdict == "enter"] == [
        "Player A",
        "Player B",
        "Player C",
    ]
    # two strikes of one player: only the better one is listed
    rows2 = [row("LeBron James", 20, edge=0.15), row("LeBron James", 25, edge=0.06)]
    legs2 = F.half_legs(c, [over(line=25.0), over(pick_id="p2", line=20.0)], rows2, "LAL@LAC", ())
    by = {leg.line: leg for leg in legs2}
    assert by[20].verdict == "enter"
    assert by[25].verdict == "skip" and "a better strike for this player" in by[25].why


def test_enter_equals_top_overs_on_multi_row_games(cfg: JarvisConfig) -> None:
    c = _ho(cfg)
    edges = (0.03, 0.06, 0.12)
    for combo in product(edges, repeat=4):
        rows = [
            row("P1", 20, edge=combo[0]), row("P1", 25, edge=combo[1]),
            row("P2", 10, edge=combo[2]), row("P3", 5, edge=combo[3]),
        ]  # fmt: skip
        picks = [over(player=n, pick_id=n + str(k), line=float(k)) for n, k in
                 (("P1", 20), ("P1", 25), ("P2", 10), ("P3", 5))]  # fmt: skip
        listed = {
            (r["player_name"], int(r["k"])) for r in HO.top_overs(rows, "LAL@LAC", 0.05, 3, 1, 10)
        }
        legs = F.half_legs(c, picks, rows, "LAL@LAC", (), limit=10)
        entered = {(leg.player, leg.line) for leg in legs if leg.verdict == "enter"}
        assert entered == listed, combo


# ------------------------------------------------------------------------------ M2


def test_an_over_already_shown_stays_marked_stale_when_its_quote_ages_out(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    assert [p["player"] for p in R.state_of(rt.cfg).picks(SLATE)] == ["LeBron James"]
    run(at(rt, NOW + timedelta(minutes=35)))  # the 19:58 quote is now 37 min old (> 30)
    e = http.of("PATCH")[-1][2]["embeds"][0]["fields"][0]["value"]
    assert "LeBron James" in e and "(price now stale)" in e
    assert "No over has positive EV" not in e


def test_the_t_minus_3_cutoff_does_not_flip_the_card_either(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    t = TIP1 - timedelta(minutes=2)
    _fresh_kalshi(rt, t - timedelta(minutes=1))
    run(at(rt, t))
    e = http.of("PATCH")[-1][2]["embeds"][0]["fields"][0]["value"]
    assert "LeBron James" in e and "(price now stale)" in e


# ------------------------------------------------------------------------------ M3, M7


def test_a_torn_last_line_does_not_swallow_the_next_record(tmp_path: Path) -> None:
    s = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    s.record(CardRecord("2026-10-28", "G1", "LAL", "m1", "pretip", "h", "fp", "post", "t"))
    with s.state_path.open("a") as fh:
        fh.write('{"slate": "2026-10-28", "game_id": "G1", "te')  # killed mid-write
    s2 = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    s2.record(CardRecord("2026-10-28", "G1", "LAC", "m2", "pretip", "h", "fp", "post", "t"))
    s3 = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    assert s3.latest("2026-10-28", "G1", "LAC") is not None  # not glued to the torn line
    assert s3.latest("2026-10-28", "G1", "LAL") is not None and s3.skipped == 1
    p = {"pick_id": "a", "slate": "d", "game_id": "G1", "ticker": "T", "venue": "kalshi",
         "player": "A", "stat": "pts", "line": 1.0, "ask": 0.4, "fee_c": 2.0,
         "edge_c": 1.0}  # fmt: skip
    s3.add_pick(p)
    with s3.picks_path.open("a") as fh:
        fh.write('{"rec": "pick", "pick_id": "b')
    s4 = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    s4.add_pick({**p, "pick_id": "c"})
    s5 = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    assert {x["pick_id"] for x in s5.picks()} == {"a", "c"}
    assert s5.add_pick({**p, "ask": 0.9}) is False  # a re-sight never re-logs at a later price


def test_a_malformed_record_is_skipped_and_counted_never_fatal(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    d = tmp_path / "c"
    d.mkdir()
    (d / "state.jsonl").write_text('{"slate": "2026-10-28", "game_id": "G", "team": "LAL"}\n[1]\n')
    rep = run(rt)
    assert rep["cards"] == {"posted": 4} and rep["state_skipped"] == 2


def test_a_torn_line_between_ticks_causes_no_duplicate_post(rt: Runtime, tmp_path: Path) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    run(rt)
    with (tmp_path / "c" / "state.jsonl").open("a") as fh:
        fh.write('{"slate": "2026-10-28", "gam')
    n = len(http.of("POST"))
    run(at(rt, NOW + timedelta(minutes=1)))
    assert len(http.of("POST")) == n
    from tests.jarvis.cards.test_run import scoreboard

    t_half = TIP1 + timedelta(minutes=70)
    scoreboard(rt.cfg.root, "STATUS_HALFTIME", 55, 48, t_half)
    run(at(rt, t_half))  # an edit after the torn line is recorded, and found again
    rec = R.state_of(rt.cfg).latest(SLATE, G1, "LAL")
    assert rec is not None and rec.phase == "halftime"
    assert len(http.of("POST")) == n


def test_the_state_is_indexed_once_per_pass(tmp_path: Path) -> None:
    s = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    for i in range(300):
        s.record(
            CardRecord("2026-10-28", f"G{i}", "LAL", f"m{i}", "pretip", "h", "fp", "post", "t")
        )
    reads = {"n": 0}
    real = Path.read_text

    def counting(self: Path, *a: Any, **k: Any) -> str:
        reads["n"] += 1
        return real(self, *a, **k)

    s2 = CardState(tmp_path / "s.jsonl", tmp_path / "p.jsonl")
    Path.read_text = counting  # type: ignore[method-assign]
    try:
        for i in range(300):
            assert s2.latest("2026-10-28", f"G{i}", "LAL") is not None
            s2.picks()
    finally:
        Path.read_text = real  # type: ignore[method-assign]
    assert reads["n"] <= 2  # one read per file, however many lookups


# ------------------------------------------------------------------------------ minors


def test_an_accepted_post_without_an_id_is_recorded_and_never_re_posted(
    rt: Runtime, tmp_path: Path
) -> None:
    http = Ids(*[Resp(204) for _ in range(4)])
    make_rt(rt, tmp_path, http)
    rep = run(rt)
    assert rep["cards"] == {"lost_id": 4}
    n = len(http.calls)
    assert run(rt)["cards"] == {"unchanged": 4} and len(http.calls) == n


def test_one_teams_failure_never_costs_the_other_teams_card(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = Ids()
    make_rt(rt, tmp_path, http)
    real = R.build_embed

    def pick(card: Any, team: str, style: Any) -> Any:
        if team == "LAL":
            raise RuntimeError("boom")
        return real(card, team, style)

    monkeypatch.setattr(R, "build_embed", pick)
    rep = run(rt)
    assert rep["cards"]["posted"] == 3 and len(rep["errors"]) == 1
    assert any("LAC" in c[1] or "OKC" in c[1] for c in http.of("POST"))


def test_no_webhooks_while_enabled_shows_in_the_tick_line(rt: Runtime, tmp_path: Path) -> None:
    from nba.jarvis.runner import tick_line

    make_rt(rt, tmp_path, Ids())
    rt.env = {}
    assert "cards=off(no webhooks)" in tick_line(run_tick(rt, SLATE))
