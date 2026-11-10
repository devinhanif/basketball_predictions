"""Unders (Kalshi prop No) on the cards (Devin, 2026-11-10): ranked with the overs, logged with
side=no. With the DNP rule verified (the real config) a No is priced on P(under | plays) like an
over and follows the same alert rules; the unverified fallback prices the worst case (a DNP is a No
loss), shows it, and never alerts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.config import JarvisConfig, load_config
from nba.jarvis.models import Game, Play
from nba.jarvis.runner import Runtime, supports_alert
from nba.jarvis.slate import SlateState, gather, single_candidate
from tests.jarvis.cards.test_embed_text import STYLE, card, over
from tests.jarvis.conftest import G1, NOW, SLATE, Sent, StubModel


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


def test_verified_no_is_priced_on_p_under_given_plays_listed_but_held_at_thin(
    cfg_verified: JarvisConfig,
) -> None:
    st, p = _state(cfg_verified, 0.70, 0.72)
    assert p.fair_prob == pytest.approx(1.0 - 0.62 / 1.04)  # no P(plays) factor
    assert not any("P(plays)" in n for n in p.notes)
    assert any("prop No held at thin" in n for n in p.notes)
    # M1 (adversary 2026-11-10): display yes, alert no. A huge edge is still thin and never alerts.
    assert p.edge_cents > cfg_verified.strong_cents
    assert p.label == "thin" and not supports_alert(cfg_verified, p)
    assert not F.is_capped(p)  # the hold note must not match CAP_NOTES, or the card would drop it
    assert F.under_display_ok(cfg_verified, p)
    unders, _ = F.pregame_unders(cfg_verified, st, G1, 3, "t")
    assert unders and unders[0].side == "no" and not unders[0].includes_p_plays
    assert unders[0].model is None
    _, huge = _state(cfg_verified, 0.90, 0.92)
    assert huge.label == "thin" and not supports_alert(cfg_verified, huge)
    # the Yes side keeps its own rules (a cheap Yes under the verified rule can be strong)
    st2 = gather(cfg_verified, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st2.quotes if q.kind == "prop" and q.venue == "kalshi")
    yes = single_candidate(cfg_verified, st2, prop, "yes")
    assert yes is not None and yes.label == "strong" and supports_alert(cfg_verified, yes)


def test_the_model_side_of_a_verified_prop_is_conditional_on_playing(
    cfg_verified: JarvisConfig, cfg: JarvisConfig
) -> None:
    from nba.jarvis.slate import _model_side

    class M(StubModel):
        def p_yes(self, q):  # type: ignore[no-untyped-def]
            return 0.45  # P(plays) x P(over | plays) with P(plays) = 0.97 (StubModel)

    st = gather(cfg_verified, SLATE, NOW, use_polymarket=False, model=M())
    q = next(x for x in st.quotes if x.kind == "prop" and x.venue == "kalshi")
    assert _model_side(st, q, "yes", True) == pytest.approx(0.45 / 0.97)
    assert _model_side(st, q, "no", True) == pytest.approx(1.0 - 0.45 / 0.97)  # not 1 - 0.45
    assert _model_side(st, q, "no", False) == pytest.approx(
        0.55
    )  # the unverified path is as before


def test_a_card_tick_under_the_verified_config_lists_the_under_and_sends_no_alert(
    rt: Runtime, tmp_path: Path, sent: Sent
) -> None:
    import duckdb

    from tests.jarvis.cards.test_review_fixes import KEY
    from tests.jarvis.cards.test_run import Ids, make_rt, state_of

    http = Ids()
    make_rt(rt, tmp_path, http)
    rt.cfg = replace(rt.cfg, kalshi_prop_dnp_rule_verified=True)
    con = duckdb.connect(str(rt.cfg.paths["kalshi_db"]))
    con.execute(  # No ask 30c against a ~38c sharp under
        "INSERT INTO kalshi_prices VALUES (?,?,0.70,0.72,NULL,0,0,'live')",
        [KEY, NOW - timedelta(minutes=1)],
    )
    con.close()
    from nba.jarvis.runner import run_tick

    run_tick(rt, SLATE)
    g1 = next(c for c in http.of("POST") if "LAL" in c[1])[2]["embeds"][0]
    field = g1["fields"][0]
    assert field["name"].startswith("Top overs and unders")
    assert "(Kalshi No)" in field["value"] and "never alerted" in field["value"]
    picks = state_of(rt).picks(SLATE)
    assert any(p.get("side") == "no" for p in picks)
    assert not any("strong" in t.lower() and "No" in t and "under" in t.lower() for t in sent.texts)


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
    assert "under 25 pts (Kalshi No)" in value and "Kalshi's last fair price" in value
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


class _Source:
    """Mimics ``OutcomeSource``: Yes wins when stat >= threshold; a No wins when it does not."""

    def __init__(self, stat: float) -> None:
        self.stat = stat
        self.seen: list[dict[str, Any]] = []

    def of(self, r: dict[str, Any]) -> Any:
        from nba.jarvis.book import Outcome

        self.seen.append(r)
        yes = self.stat >= float(r["threshold"])
        return Outcome(yes if r["side"] == "yes" else not yes, "settled")


def test_a_no_pick_settles_on_the_under_and_an_old_pick_without_side_settles_as_an_over(
    cfg: JarvisConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.jarvis.cards import recap as RC
    from tests.jarvis.cards.test_facts_recap_safety import pick, state

    monkeypatch.setattr(RC, "box_loaded", lambda c, g: True)
    s = state(tmp_path)
    under = {**pick("u"), "side": "no", "line": 25.0, "ask": 0.48}
    old = pick("o")  # logged before this branch: no "side" key at all
    assert "side" not in old
    s.add_pick(under)
    s.add_pick(old)
    src = _Source(stat=25.0)  # exactly the line: the over wins, the under loses
    assert RC.settle_pending(cfg, s, datetime(2026, 10, 29, 6, 0), source=src) == 2  # type: ignore[arg-type]
    by = {p["pick_id"]: p["settled"] for p in s.picks()}
    assert by["u"]["won"] is False and by["u"]["units"] == -1.0
    assert by["o"]["won"] is True
    assert {r["side"] for r in src.seen} == {"no", "yes"}
    src2 = _Source(stat=24.0)  # one short: the under wins at its own ask + fee
    s.add_pick({**under, "pick_id": "u2"})
    RC.settle_pending(cfg, s, datetime(2026, 10, 29, 6, 0), source=src2)  # type: ignore[arg-type]
    won = next(p for p in s.picks() if p["pick_id"] == "u2")["settled"]
    assert won["won"] is True and won["units"] == pytest.approx((1 - 0.50) / 0.50)
    row = RC.recap_for_game(cfg, s, "G1").rows[0]
    assert row.side in ("no", "yes")
