"""Unders (Kalshi prop No) on the cards (Devin, 2026-11-10): ranked with the overs, logged with
side=no. With the DNP rule verified (the real config) a No is priced on P(under | plays) like an
over and follows the same alert rules; the unverified fallback prices the worst case (a DNP is a No
loss), shows it, and never alerts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.config import JarvisConfig, load_config
from nba.jarvis.models import Game, Play
from nba.jarvis.runner import supports_alert
from nba.jarvis.slate import SlateState, gather, single_candidate
from tests.jarvis.cards.test_embed_text import STYLE, card, over
from tests.jarvis.conftest import G1, NOW, SLATE, StubModel


def _state(c: JarvisConfig, yes_bid: float, yes_ask: float) -> tuple[SlateState, Play]:
    st = gather(c, SLATE, NOW, use_polymarket=False, model=StubModel())
    i, prop = next(
        (i, q) for i, q in enumerate(st.quotes) if q.kind == "prop" and q.venue == "kalshi"
    )
    st.quotes[i] = replace(prop, yes_bid=yes_bid, yes_ask=yes_ask)
    p = single_candidate(c, st, st.quotes[i], "no")
    assert p is not None
    return st, p


def test_the_real_config_has_the_rule_verified(root) -> None:  # type: ignore[no-untyped-def]
    assert load_config(root=root).kalshi_prop_dnp_rule_verified is True


# ---------------------------------------------------------------------------- verified (real rule)


def test_verified_no_is_priced_on_p_under_given_plays_and_not_capped(
    cfg_verified: JarvisConfig,
) -> None:
    st, p = _state(cfg_verified, 0.70, 0.72)
    assert p.fair_prob == pytest.approx(1.0 - 0.62 / 1.04)  # no P(plays) factor
    assert not any("P(plays)" in n or "capped" in n for n in p.notes)
    assert p.label == "strong" and supports_alert(cfg_verified, p)  # same rules as an over
    assert F.under_display_ok(cfg_verified, p)
    unders, _ = F.pregame_unders(cfg_verified, st, G1, 3, "t")
    assert unders and unders[0].side == "no" and not unders[0].includes_p_plays
    assert unders[0].model is None


# ---------------------------------------------------------------------------- unverified fallback


def test_unverified_no_is_worst_case_shown_but_stays_thin_and_never_alerts(
    cfg: JarvisConfig,
) -> None:
    st, p = _state(cfg, 0.70, 0.72)
    assert p.label == "thin" and not supports_alert(cfg, p)
    assert F.under_display_ok(cfg, p)
    assert p.fair_prob is not None and p.fair_prob == pytest.approx(p.pin_prob)
    assert p.fair_prob < 1.0 - 0.62 / 1.04  # the 0.97 P(plays) factor: a DNP is a No loss
    unders, priced = F.pregame_unders(cfg, st, G1, 3, "t")
    assert priced >= 1 and unders and unders[0].edge_c > 0 and unders[0].includes_p_plays
    assert unders[0].pick_id != F.pick_id(SLATE, G1, unders[0].ticker)
    _, huge = _state(cfg, 0.90, 0.92)  # an enormous measured edge still never reaches an alert
    assert huge.edge_cents > cfg.strong_cents and huge.label == "thin"
    assert not supports_alert(cfg, huge)


def test_unverified_no_with_a_blend_above_the_worst_case_or_a_weak_input_is_not_shown(
    cfg: JarvisConfig,
) -> None:
    _, p = _state(cfg, 0.70, 0.72)
    assert not F.under_display_ok(cfg, replace(p, fair_prob=(p.pin_prob or 0) + 0.02))
    assert not F.under_display_ok(
        cfg, replace(p, notes=("prop DNP rule unverified and no P(plays)",))
    )
    assert not F.under_display_ok(cfg, replace(p, notes=("Pinnacle line is 200 min old; x",)))
    assert not F.under_display_ok(cfg, replace(p, notes=()))


# ---------------------------------------------------------------------------- text and log


def test_merged_ranking_text_and_notes() -> None:
    ov = over(player="Over Guy", edge_c=4.0)
    un = replace(
        over(player="Under Guy", edge_c=6.0, ticker="T-U"),
        side="no",
        model=None,
        includes_p_plays=False,
    )
    merged = F.merge_ranked([ov], [un], 3)
    assert [r.side for r in merged] == ["no", "yes"]
    name, value = T.overs_field(card(overs=tuple(merged)), STYLE)
    assert name == "Top overs and unders by EV after fees"
    assert "under 25 pts (Kalshi No)" in value and "settles at the fair price" in value
    worst = replace(un, includes_p_plays=True)
    assert "worst case" in T.overs_field(card(overs=(worst,)), STYLE)[1]
    assert T.overs_field(card(overs=(ov,)), STYLE)[0] == "Top overs by EV after fees"


def test_pick_record_carries_the_side_and_round_trips(cfg_verified: JarvisConfig) -> None:
    st, _ = _state(cfg_verified, 0.70, 0.72)
    u = F.pregame_unders(cfg_verified, st, G1, 1, "t")[0][0]
    g = next(x for x in st.games if x.game_id == G1)
    assert isinstance(g, Game)
    rec = F.pick_record(u, SLATE, g)
    assert rec["side"] == "no"
    back = F.overs_from_picks([rec])[0]
    assert back.side == "no" and back.pick_id == u.pick_id
