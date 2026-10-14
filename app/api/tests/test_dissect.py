"""The dissection scaffold: fact packet, template writer, numguard, providers and routing.

No model is called anywhere: the Claude provider is a disabled stub and these tests only prove it
stays that way. Fixture legs are real 2024-25 box-score history (season <= 2024)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import breakdown as bd
from parlayhitters_api.dissect import providers as pv
from parlayhitters_api.dissect import template
from parlayhitters_api.dissect.numguard import check_text, collect_numbers
from parlayhitters_api.dissect.packet import build_packet
from parlayhitters_api.dissect.service import dissect, dissect_leg, dissect_parlay, parlay_packet
from parlayhitters_api.history import MODEL_STATS
from parlayhitters_api.store import Store


def _legs() -> list[bd.Breakdown]:
    """Every fixture player x model stat x side, built from the committed fixtures."""
    store = Store()
    assert store.history is not None
    out: list[bd.Breakdown] = []
    for g in store.games.values():
        for pl in g["players"][:6]:
            for st in sorted(MODEL_STATS & set(pl["props"])):
                for side in ("over", "under"):
                    out.append(bd.build(store.history, g, pl, st, None, side))
    assert len(out) > 40
    return out


LEGS = _legs()


# ----------------------------------------------------------------------------- numguard


PACKET: dict[str, Any] = {
    "player": "Pat Player",
    "line": 24.5,
    "stat_label": "3-pointers",
    "last10": {"n": 10, "hits": 7, "rate": 0.7, "avg": 25.31},
    "model": {"p_hit": 0.615, "typical": 24.62},
    "date": "2024-11-12",
}


def test_numguard_passes_numbers_that_are_in_the_packet() -> None:
    text = (
        "Pat Player went over 24.5 3-pointers in 7 of his last 10 games, averaging 25.3. "
        "Our model gives 62% (61.5%), a typical night near 24.6, on 2024-11-12."
    )
    r = check_text(text, PACKET)
    assert r.ok, r.reason()
    assert r.checked >= 7


def test_numguard_rejects_a_planted_invented_number() -> None:
    r = check_text("Pat Player went over 24.5 in 8 of his last 10 games.", PACKET)
    assert not r.ok and r.unmatched == ["8"]  # the packet says 7
    r = check_text("Our model gives 71% to go over.", PACKET)
    assert not r.ok and r.unmatched == ["71%"]
    r = check_text("A typical night is near 31.2.", PACKET)
    assert not r.ok and r.unmatched == ["31.2"]


def test_numguard_has_no_free_pass_for_small_numbers_or_wrong_precision() -> None:
    assert not check_text("He hit 3 of them.", PACKET).ok  # no packet field is 3
    assert not check_text("He averaged 25.4.", PACKET).ok  # 25.31 rounds to 25.3
    assert check_text("He averaged 25.3.", PACKET).ok
    assert not check_text("Our model gives 7% to go over.", PACKET).ok  # 7 is a count, not 7%
    assert not check_text("The line is 24.", PACKET).ok  # an integer must match exactly


def test_numguard_does_not_read_a_copied_name_or_label_as_a_number() -> None:
    packet = {"player": "Player 990001", "stat_label": "3-pointers", "n": 4}
    assert check_text("Player 990001 hit 4 3-pointers.", packet).ok
    assert not check_text("Player 990002 hit 4 3-pointers.", packet).ok  # a different name


def test_numguard_rejects_recommendations_and_hype() -> None:
    for bad in (
        "You should bet the over.",
        "This is a lock.",
        "A guaranteed winner.",
        "Free money.",
    ):
        r = check_text(bad, PACKET)
        assert not r.ok and r.banned, bad


def test_collect_numbers_ignores_booleans_and_none() -> None:
    assert collect_numbers({"a": True, "b": None, "c": [1, 2.5], "d": {"e": 3}}) == [1.0, 2.5, 3.0]


# ----------------------------------------------------------------------------- template


def test_template_writes_two_to_four_sentences_that_pass_the_guard_on_every_fixture_leg() -> None:
    for b in LEGS:
        p = build_packet(b)
        parts = template.leg_sentences(p)
        text = template.write_leg(p)
        assert 2 <= len(parts) <= 4, (b.player, b.stat, text)
        assert all(s.endswith(".") for s in parts) and " ".join(parts) == text
        r = check_text(text, p)
        assert r.ok, (b.player, b.stat, r.reason(), text)
        assert r.checked >= 3
        assert not re.search(r"\b(should|recommend|bet on|take the)\b", text, re.I), text


def test_template_numbers_trace_to_the_packet_and_the_screen() -> None:
    b = next(x for x in LEGS if x.last10.n >= 10 and x.model.available)
    p = build_packet(b)
    text = template.write_leg(p)
    assert f"{b.last10.hits} of his last {b.last10.n} games" in text
    assert f"{round((b.model.p_hit or 0) * 100)}%" in text
    assert p["last10"]["hits"] == b.last10.hits and p["model"]["p_hit"] == b.model.p_hit


def test_template_says_so_when_there_is_no_model_or_no_history() -> None:
    no_model = next(x for x in LEGS if x.stat in MODEL_STATS and x.model.available)
    p = build_packet(no_model)
    p["model"] = {**p["model"], "available": False, "p_hit": None}
    p["flags"] = {**p["flags"], "no_model": True, "disagree": False, "gap_points": None}
    text = template.write_leg(p)
    assert "no model forecast" in text and check_text(text, p).ok
    p2 = copy.deepcopy(build_packet(no_model))
    p2["flags"]["no_history"] = True
    p2["minutes"]["avg"] = None
    t2 = template.write_leg(p2)
    assert "no earlier games" in t2 and check_text(t2, p2).ok


def test_small_sample_and_disagreement_are_flagged_in_the_words() -> None:
    p = build_packet(LEGS[0])
    p["flags"].update(small_last10=True, disagree=True, gap_points=31, recent_above_model=True)
    p["model"].update(available=True, p_hit=0.4)
    text = template.write_leg(p)
    assert "a small sample" in text and "31 points above" in text
    assert check_text(text, p).ok


def test_a_reference_line_is_labelled_in_the_text() -> None:
    p = build_packet(LEGS[0])
    p["line_kind"] = "reference"
    assert "reference line of ours, not a sportsbook line" in template.write_leg(p)


# ----------------------------------------------------------------------------- providers


def test_claude_provider_is_disabled_without_a_key_and_never_calls_out() -> None:
    cp = pv.ClaudeProvider({})
    assert not cp.enabled and not cp.enabled_for("leg") and not cp.enabled_for("parlay")
    p = build_packet(LEGS[0])
    with pytest.raises(pv.ProviderError, match="disabled"):
        cp.write(p, "leg")
    # a key alone is not enough: no model chosen for the tier, so it still does not route
    keyed = pv.ClaudeProvider({pv.KEY_ENV: "k"})
    assert keyed.enabled and not keyed.enabled_for("leg")
    with pytest.raises(pv.ProviderError, match="no model configured"):
        keyed.write(p, "leg")


def test_routing_by_difficulty_one_leg_cheap_a_ticket_strong() -> None:
    env = {
        pv.KEY_ENV: "k",
        pv.MODEL_ENV["cheap"]: "model-cheap",
        pv.MODEL_ENV["strong"]: "model-strong",
    }
    cp = pv.ClaudeProvider(env)
    assert cp.model_for("leg") == "model-cheap" and cp.model_for("parlay") == "model-strong"
    assert pv.TIER_ROUTE == {"leg": "cheap", "parlay": "strong"}
    only_cheap = pv.ClaudeProvider({pv.KEY_ENV: "k", pv.MODEL_ENV["cheap"]: "model-cheap"})
    assert only_cheap.enabled_for("leg") and not only_cheap.enabled_for("parlay")
    router = pv.default_router(env)
    assert router.pick("leg").name == "claude" and router.pick("parlay").name == "claude"
    assert pv.default_router({}).pick("leg").name == "template"
    assert pv.default_router({pv.KEY_ENV: "k"}).pick("parlay").name == "template"
    with pytest.raises(pv.NotWired):  # even fully configured, no call is made in this build
        cp.write(build_packet(LEGS[0]), "leg")


def test_a_provider_that_cannot_run_falls_back_to_the_template_and_says_why() -> None:
    env = {pv.KEY_ENV: "k", pv.MODEL_ENV["cheap"]: "model-cheap"}
    d = dissect_leg(LEGS[0], pv.default_router(env))
    assert d.provider == "template" and d.fell_back and d.guard_ok and d.text
    assert d.model is None and "claude" in d.rejected[0] and "not turned on" in d.rejected[0]


class _Inventor:
    """A provider that writes a plausible sentence with a number the packet does not have."""

    name = "inventor"

    def enabled_for(self, tier: str) -> bool:
        return True

    def write(self, packet: dict[str, Any], tier: str) -> pv.Written:
        return pv.Written(
            text=f"{packet['player']} will score 99 points. Our model gives 3% to go over.",
            model="made-up",
        )


def test_invented_numbers_from_a_provider_are_rejected_and_the_template_answers() -> None:
    router = pv.Router((_Inventor(), pv.TemplateProvider()))
    d = dissect_leg(LEGS[0], router)
    assert d.provider == "template" and d.fell_back and d.guard_ok
    assert d.model is None  # the rejected provider's model is not claimed
    assert (
        d.rejected and "inventor: rejected by numguard" in d.rejected[0] and "99" in d.rejected[0]
    )
    assert "99" not in d.text


def test_if_even_the_template_fails_the_guard_the_text_is_empty_not_wrong() -> None:
    p = build_packet(LEGS[0])
    p["last10"] = {**p["last10"], "hits": 3}

    # a packet whose own numbers are removed after writing: simulate by checking a bad writer
    class Bad:
        name = "template"

        def enabled_for(self, tier: str) -> bool:
            return True

        def write(self, packet: dict[str, Any], tier: str) -> pv.Written:
            return pv.Written("He scored 12345 points.", None)

    d = dissect(p, "leg", pv.Router((Bad(),)))
    assert d.text == "" and d.guard_ok is False and d.rejected


# ----------------------------------------------------------------------------- full ticket


def test_parlay_text_uses_the_strong_tier_and_never_multiplies_the_legs() -> None:
    packets = [build_packet(b) for b in LEGS[:3]]
    pp = parlay_packet(packets)
    assert pp["n_legs"] == 3 and pp["n_with_model"] + pp["n_without_model"] == 3
    d = dissect_parlay(packets)
    assert d.tier == "parlay" and d.guard_ok and d.provider == "template"
    assert 2 <= len(template.parlay_sentences(pp)) <= 4
    assert "do not multiply" in d.text
    assert check_text(d.text, pp).ok
    bad = check_text(d.text + " Together that is 12%.", pp)
    assert not bad.ok


def test_parlay_routing_picks_the_strong_model() -> None:
    env = {pv.KEY_ENV: "k", pv.MODEL_ENV["strong"]: "model-strong"}
    d = dissect_parlay([build_packet(LEGS[0])], pv.default_router(env))
    assert d.tier == "parlay" and d.fell_back and "claude" in d.rejected[0]


# ----------------------------------------------------------------------------- the endpoint


def test_the_fixture_breakdown_endpoint_carries_a_checked_dissection(client: TestClient) -> None:
    s = client.get("/api/slates/2024-11-12").json()
    g = client.get(f"/api/games/{s['games'][0]['game_id']}").json()
    p = g["players"][0]
    stat = next(iter(p["props"]))
    r = client.get(f"/api/breakdown/{g['game_id']}/{p['player_id']}/{stat}").json()
    d = r["dissection"]
    assert d["guard_ok"] and d["provider"] == "template" and d["text"]
    assert "Written by an automated system" in d["disclosure"]
    assert re.search(r"\b\d+ of his last \d+ games\b", d["text"])


def test_no_key_is_ever_read_from_a_file() -> None:
    src = Path(pv.__file__).read_text()
    assert (
        "open(" not in src and "read_text" not in src and ".env" not in src.replace("environ", "")
    )
