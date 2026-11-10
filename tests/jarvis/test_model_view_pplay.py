"""ModelView.p_play (Finding 5, 2026-11-10): the stored P(plays), known-at, one shared lookup, and
the priced path in single_candidate."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from nba.jarvis import slate as slate_mod
from nba.jarvis.config import JarvisConfig
from nba.jarvis.forecasts import ModelView, PPlayView
from nba.jarvis.models import Quote
from nba.jarvis.slate import gather, single_candidate
from nba.parlay.out_gate import OutNotice
from tests.jarvis.conftest import G1, LEBRON, NOW, SLATE


def _view(
    p_play: dict[int, float],
    made: dict[tuple[str, int, str], str],
    tip: datetime,
    out: bool = False,
    raw: dict[tuple[str, int, str], dict[str, float]] | None = None,
) -> ModelView:
    mv = ModelView(JarvisConfig.__new__(JarvisConfig), SLATE)
    ctx = SimpleNamespace(p_play=p_play, team_of={LEBRON: 1})
    info = SimpleNamespace(
        ctxs={G1: ctx},
        tipoff={G1: tip},
        out_on_report={(G1, LEBRON): OutNotice(NOW, None)} if out else {},
    )
    if raw is None:  # a stored row that carries p_play, as the slate loader keeps it
        raw = {(G1, LEBRON, "pts"): {"p_play": p_play.get(LEBRON, 0.0)}}
    mv._sl = SimpleNamespace(info=info, made_at=made, props_raw=raw)
    return mv


def _q() -> Quote:
    return Quote(
        "kalshi", "T", G1, "prop", NOW, 0.4, 0.44, player_id=LEBRON, stat="pts", threshold=25
    )


def test_p_play_returns_the_stored_row_value() -> None:
    made = {(G1, LEBRON, "pts"): str(NOW - timedelta(hours=2))}
    mv = _view({LEBRON: 0.93}, made, NOW + timedelta(hours=1))
    assert mv.p_play(_q()) == pytest.approx(0.93)


def test_p_play_is_none_without_a_row_or_for_a_non_prop() -> None:
    mv = _view({}, {}, NOW + timedelta(hours=1))
    assert mv.p_play(_q()) is None  # never a default of 1.0
    gq = Quote("kalshi", "T", G1, "game_win", NOW, 0.4, 0.44, team_id=1)
    assert _view({LEBRON: 0.9}, {}, NOW).p_play(gq) is None


def test_p_play_refuses_a_row_made_after_tip_and_an_out_player() -> None:
    tip = NOW + timedelta(hours=1)
    late = {(G1, LEBRON, "pts"): str(tip + timedelta(minutes=5))}
    assert _view({LEBRON: 0.93}, late, tip).p_play(_q()) is None
    early = {(G1, LEBRON, "pts"): str(tip - timedelta(minutes=5))}
    assert _view({LEBRON: 0.93}, early, tip).p_play(_q()) == pytest.approx(0.93)
    assert _view({LEBRON: 0.93}, early, tip, out=True).p_play(_q()) is None


def test_p_play_rejects_unusable_numbers() -> None:
    for bad in (0.0, float("nan"), 1.5):
        assert _view({LEBRON: bad}, {}, NOW + timedelta(hours=1)).p_play(_q()) is None


def test_single_candidate_prices_on_p_play_and_caps_without_it(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    q = next(x for x in st.quotes if x.kind == "prop" and x.player_id == LEBRON)
    assert isinstance(st, slate_mod.SlateState)
    with_p = single_candidate(cfg, replace(st, model=PPlayView(st.model, {LEBRON: 0.5})), q, "yes")
    without = single_candidate(cfg, replace(st, model=PPlayView(st.model, {})), q, "yes")
    assert with_p is not None and without is not None
    assert with_p.pin_prob is not None and without.pin_prob is not None
    assert with_p.pin_prob == pytest.approx(0.5 * without.pin_prob)
    assert any("includes P(plays)" in n for n in with_p.notes)
    assert any("no P(plays)" in n for n in without.notes)


def test_p_play_is_none_when_the_row_carries_no_p_play() -> None:
    """Review M2: the slate builder fills 1.0 for a row without p_play; that must cap, not price."""
    made = {(G1, LEBRON, "pts"): str(NOW - timedelta(hours=2))}
    raw = {(G1, LEBRON, "pts"): {"mean": 20.0}}
    mv = _view({LEBRON: 1.0}, made, NOW + timedelta(hours=1), raw=raw)
    assert mv.p_play(_q()) is None


def test_an_unparseable_made_at_caps_and_never_raises() -> None:
    """Review M1: a NULL made_at ('None') must not fail the tick."""
    made = {(G1, LEBRON, "pts"): "None"}
    assert _view({LEBRON: 0.93}, made, NOW + timedelta(hours=1)).p_play(_q()) is None
