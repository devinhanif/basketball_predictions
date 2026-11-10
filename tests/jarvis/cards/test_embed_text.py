"""The card text: deterministic templates, no invented number, Discord limits, hit/miss symmetry."""

from __future__ import annotations

import json
import re
from datetime import datetime

from nba.jarvis.cards import embed as E
from nba.jarvis.cards import text as T
from nba.jarvis.cards.model import (
    CardData,
    HalfLeg,
    Knows,
    OverRow,
    Recap,
    SettledRow,
)

TIP = datetime(2026, 10, 29, 0, 30)  # 8:30 PM ET on the 28th
NOW = datetime(2026, 10, 28, 23, 12)
STYLE = T.Style()


def over(**kw: object) -> OverRow:
    base: dict[str, object] = dict(
        pick_id="p1", player="LeBron James", stat="pts", line=25.0, ask=0.44, fee_c=2.0,
        fair=0.60, pin=0.62, model=0.58, edge_c=14.0, ref_age_min=12.0, label="strong",
        ticker="T1", venue="kalshi", player_id=2544, includes_p_plays=True,
    )  # fmt: skip
    base.update(kw)
    return OverRow(**base)  # type: ignore[arg-type]


def card(**kw: object) -> CardData:
    base: dict[str, object] = dict(
        game_id="G1", away="LAL", home="LAC", tip=TIP, phase="pretip", as_of=NOW,
        overs=(over(),), knows=Knows(
            out={"LAC": ["Kawhi Leonard (knee)"]}, gtd={"LAL": ["Austin Reaves (questionable)"]},
            starters={"LAL": ["A", "B", "C", "D", "E"]}, report_age_min=45.0, lineup_age_min=3.0,
        ),
    )  # fmt: skip
    base.update(kw)
    return CardData(**base)  # type: ignore[arg-type]


def nums(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", s))


def test_title_is_away_at_home_with_the_tip_in_eastern_time() -> None:
    assert T.title(card(), STYLE) == "LAL @ LAC — 8:30 PM ET"
    assert T.title(card(tip=None), STYLE) == "LAL @ LAC — tip time not set"


def test_overs_field_names_line_price_fair_ev_and_a_plain_why() -> None:
    name, value = T.overs_field(card(), STYLE)
    assert name == "Top overs by EV after fees"
    assert "LeBron James 25+ pts" in value
    assert "ask 44c" in value and "fair 60c" in value
    assert "EV +0.14 per $1 contract (+30%) after the 2c fee" in value
    assert "Pinnacle" in value and "chance he plays" in value and "12 min old" in value
    assert "58%" in value and "4 percentage points apart (some gap)" in value
    assert "Kalshi asks 44c" in value and "puts fair at 62c; JARVIS fair 60c" in value
    assert "range" not in value and " pts from" not in value


def test_the_model_range_words_follow_the_configured_gap() -> None:
    assert "(close)" in T.confidence_words(over(model=0.61, pin=0.62), STYLE)
    assert "(some gap)" in T.confidence_words(over(model=0.58, pin=0.62), STYLE)
    assert "(far apart)" in T.confidence_words(over(model=0.50, pin=0.62), STYLE)
    assert "No model number" in T.confidence_words(over(model=None), STYLE)


def test_no_number_on_an_over_line_is_invented() -> None:
    o = over()
    _, value = T.overs_field(card(overs=(o,)), STYLE)
    assert o.fair is not None and o.model is not None and o.pin is not None
    allowed = {
        "1",  # the rank, and the "$1" contract
        f"{o.line:g}",
        f"{o.ask * 100:.0f}",
        f"{o.fair * 100:.0f}",
        f"{o.pin * 100:.0f}",
        f"{o.edge_c / 100:.2f}",
        f"{o.edge_c / (o.ask * 100 + o.fee_c) * 100:.0f}",
        f"{o.fee_c:.0f}",
        f"{o.ref_age_min:.0f}",
        f"{o.model * 100:.0f}",
        f"{abs(o.model - o.pin) * 100:.0f}",
    }
    assert nums(value) <= allowed, nums(value) - allowed


def test_no_over_says_the_honest_sentence() -> None:
    _, value = T.overs_field(card(overs=()), STYLE)
    assert value == "No over has positive EV after fees tonight."
    assert "keep your money" not in value
    _, value = T.overs_field(card(overs=(), overs_note="No pre-tip card was posted."), STYLE)
    assert value == "No pre-tip card was posted."  # when we cannot know, we do not claim "no over"


def test_frozen_overs_say_they_are_as_shown_pre_tip() -> None:
    name, _ = T.overs_field(card(overs_frozen=True), STYLE)
    assert "as shown pre-tip" in name


def test_halftime_leg_on_pace_behind_and_already_there() -> None:
    def leg(cur: int | None, **kw: object) -> HalfLeg:
        base: dict[str, object] = dict(
            player="LeBron James", stat="pts", line=25, cur=cur, ask_c=52, bid_c=48, fee_c=2,
            p_model=0.61, edge_c=7.0, verdict="enter", why="model 61% vs ask 52c", from_pick=True,
        )  # fmt: skip
        base.update(kw)
        return HalfLeg(**base)  # type: ignore[arg-type]

    def render(h: HalfLeg) -> str:
        c = card(phase="halftime", half=(h,), away_score=54, home_score=49)
        field = T.half_field(c, STYLE)
        assert field is not None
        return field[1]

    on = render(leg(14))
    assert T.ON_PACE in on and "14 of 25" in on and "needs 11 more, on pace" in on
    assert "▰▰▰▰▰▰▱▱▱▱" in on and "**Enter**" in on and "live ask 52c (bid 48c) vs model 61%" in on
    behind = render(leg(8, verdict="skip", why="the edge is +3c after the fee, under the 5c bar"))
    assert T.BEHIND in behind and "needs 17 more, behind pace (12.5 expected by now)" in behind
    assert "**Skip**" in behind
    assert "already there" in render(leg(26))
    unknown = render(leg(None))
    assert T.NEUTRAL in unknown and "no pace call" in unknown


def test_halftime_with_nothing_to_enter_says_so() -> None:
    f = T.half_field(card(phase="halftime", half=()), STYLE)
    assert f is not None and "Nothing to enter at the half" in f[1]
    assert T.half_field(card(phase="pretip"), STYLE) is None


def test_a_hit_and_a_miss_have_the_same_shape_and_only_the_hit_is_annotated() -> None:
    hit = SettledRow("LeBron James", "pts", 25.0, 0.44, 2.0, 14.0, "settled", True, 1.17)
    miss = SettledRow("Austin Reaves", "ast", 6.0, 0.44, 2.0, 14.0, "settled", False, -1.0)
    h, m = T.settled_line(hit), T.settled_line(miss)
    assert h.startswith(T.HIT) and m.startswith(T.MISS)
    assert "this one hit; it was a +30% EV bet when shown" in h
    assert "this one hit" not in m and "bet" not in m
    shape = re.compile(r"^. \d+\+ \w+ [\w ]+: bought at 44c \+ 2c fee: [+-]\d\.\d\d units")
    assert shape.match(h) and shape.match(m)
    void = T.settled_line(SettledRow("X Y", "reb", 8.0, 0.4, 2.0, 5.0, "void", None, 0.0))
    assert "did not play" in void and "not counted" in void


def test_the_recap_reports_units_and_withholds_a_claim_below_the_minimum() -> None:
    rc = Recap(
        rows=(SettledRow("A B", "pts", 20.0, 0.5, 2.0, 9.0, "settled", True, 0.92),),
        waiting=1, n_hit=1, n_miss=0, units=0.92, season_n=7, season_hit=3, season_units=-0.8,
        season_dates=4,
    )  # fmt: skip
    f = T.recap_field(card(phase="final", recap=rc, away_score=110, home_score=104))
    assert f is not None
    name, value = f
    assert name == "Recap"
    assert "Waiting on the box score for 1 over" in value
    assert "**This game:** 1 hit, 0 miss, +0.92 units on paper" in value
    assert "n = 7 settled over 4 dates, 3 hit, 4 miss, -0.80 units" in value
    assert "descriptive, not a tested claim" in value and "CI" not in value
    assert T.recap_field(card(phase="pretip", recap=rc)) is None
    big = Recap(season_n=400, season_hit=180, season_units=15.5, season_dates=40)
    line = T.season_line(big)  # however large n gets, no interval and no claim is printed
    assert "n = 400" in line and "CI" not in line and "descriptive" in line


def test_knowledge_lists_the_channel_team_first_and_says_when_nothing_is_open() -> None:
    k = card().knows
    _, v = T.knows_field(card(), STYLE, "LAC")
    assert "**Out:** LAC: Kawhi Leonard (knee)." in v
    assert "**Game-time decisions:** LAL: Austin Reaves (questionable)." in v
    assert "**Starters:** LAL: A, B, C, D, E. Lineups read 3 min ago." in v
    assert "injury report is 45 min old" in v
    assert "**Model vs sharp line** (agreement, not a confidence interval)" in v
    assert k.report_age_min == 45.0
    empty = card(knows=Knows())
    _, v = T.knows_field(empty, STYLE)
    assert "no rotation player is listed out" in v and "none open" in v
    assert "not out yet" in v


def test_progress_bar_is_clamped() -> None:
    assert T.bar(0, 25, 10) == "▱" * 10 and T.bar(99, 25, 10) == "▰" * 10
    assert T.bar(None, 25, 10) == "▱" * 10 and len(T.bar(12, 25, 10)) == 10


# ------------------------------------------------------------------------------ embed + limits


def test_embed_has_stripe_title_fields_and_the_footer() -> None:
    e = E.build_embed(card(), "LAL", STYLE)
    assert e["color"] == 0x552583 and E.build_embed(card(), "LAC", STYLE)["color"] == 0xC8102E
    assert e["title"].startswith("LAL @ LAC") and e["description"].startswith("⏳ **Pre-tip**")
    assert [f["name"] for f in e["fields"]] == ["Top overs by EV after fees", "What the game knows"]
    assert e["footer"]["text"] == "Data as of 7:12 PM ET · Paper only. Not advice."
    assert all(f["inline"] is False for f in e["fields"])


def test_an_alert_note_and_a_stale_feed_are_said() -> None:
    c = card(phase="halftime", away_score=1, home_score=2, alert_note="Alert sent.", feed_age_min=9)
    d = E.build_embed(c, "LAL", STYLE)["description"]
    assert "🔔 Alert sent." in d and "9 min old" in d


def test_content_hash_ignores_the_as_of_footer_only() -> None:
    a = E.build_embed(card(), "LAL", STYLE)
    b = E.build_embed(card(as_of=datetime(2026, 10, 28, 23, 40)), "LAL", STYLE)
    assert a["footer"] != b["footer"] and E.content_hash(a) == E.content_hash(b)
    c = E.build_embed(card(overs=(over(ask=0.45),)), "LAL", STYLE)
    assert E.content_hash(a) != E.content_hash(c)


def test_limits_hold_for_an_absurdly_large_card() -> None:
    big = "x" * 5000
    e = E.enforce(
        {
            "title": big,
            "description": big,
            "color": 1,
            "fields": [{"name": big, "value": "\n".join([big[:300]] * 40), "inline": False}] * 40,
            "footer": {"text": big},
        }
    )
    assert len(e["title"]) <= E.TITLE_MAX and len(e["description"]) <= E.DESC_MAX
    assert len(e["fields"]) <= E.FIELDS_MAX
    assert all(len(f["name"]) <= E.NAME_MAX and len(f["value"]) <= E.VALUE_MAX for f in e["fields"])
    assert len(e["footer"]["text"]) <= E.FOOTER_MAX
    assert E.total_chars(e) <= E.TOTAL_MAX


def test_a_long_field_is_cut_at_a_line_with_a_count() -> None:
    value = "\n".join(f"line number {i:03d} " + "y" * 40 for i in range(60))
    cut = E._cut_lines(value, E.VALUE_MAX)
    assert len(cut) <= E.VALUE_MAX and "more lines)" in cut
    assert all(ln.startswith("line number") or ln.startswith("(+") for ln in cut.split("\n"))


def test_a_full_real_shaped_card_is_inside_every_limit_in_every_phase() -> None:
    overs = tuple(
        over(pick_id=f"p{i}", player=f"Player Number{i}", edge_c=14.0 - i) for i in range(3)
    )
    legs = tuple(
        HalfLeg(f"Player Number{i}", "pts", 25, 12, 52, 48, 2, 0.61, 7.0, "enter",
                "model 61% vs ask 52c plus a 2c fee: +7c after the fee, over the 5c bar", True)
        for i in range(4)
    )  # fmt: skip
    rows = tuple(
        SettledRow(f"Player Number{i}", "pts", 25.0, 0.44, 2.0, 14.0, "settled", i % 2 == 0, 0.5)
        for i in range(3)
    )
    rc = Recap(rows=rows, n_hit=2, n_miss=1, units=0.4, season_n=9, season_hit=4)
    for phase in ("pretip", "live", "halftime", "final"):
        c = card(
            phase=phase, overs=overs, half=legs if phase == "halftime" else (),
            recap=rc if phase == "final" else None, away_score=50, home_score=48, clock="Q3 4:10",
        )  # fmt: skip
        e = E.build_embed(c, "LAL", STYLE)
        assert E.total_chars(e) <= E.TOTAL_MAX, phase
        assert len(e["fields"]) <= E.FIELDS_MAX
        assert all(len(f["value"]) <= E.VALUE_MAX for f in e["fields"]), phase
        json.dumps(e)  # serialisable
