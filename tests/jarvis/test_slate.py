from __future__ import annotations

import math
from datetime import timedelta

import pytest

from nba.jarvis.book import PaperBook
from nba.jarvis.config import JarvisConfig
from nba.jarvis.slate import (
    best_per_game,
    gather,
    plan_slate,
    single_candidate,
)
from tests.jarvis.conftest import G1, G2, LAC, LAL, LEBRON, NOW, SLATE, TIP1, StubModel


def test_gather_reads_games_tips_quotes_and_pinnacle(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert [g.matchup for g in st.games] == ["LAL@LAC", "OKC@DAL"]
    assert st.game(G1).tipoff == TIP1  # type: ignore[union-attr]
    kinds = {(q.kind, q.venue) for q in st.quotes}
    assert ("game_win", "kalshi") in kinds and ("prop", "kalshi") in kinds
    assert st.refs.win[(G1, LAL)].p_yes == pytest.approx(0.56 / 1.05)
    assert (G1, LEBRON, "pts", 24.5) in st.refs.prop
    assert (G2, LAL) not in st.refs.win and not any(g == G2 for g, *_ in st.refs.prop)


def test_season_2025_is_never_loaded(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    assert all(g.game_id != "0022500001" for g in st.games)


def test_kalshi_n_plus_threshold_matches_pinnacle_half_line(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop")
    assert prop.threshold == 25.0  # "25+" == over 24.5
    ref = st.refs.for_quote(prop)
    assert ref is not None and ref.line == "o24.5"
    yes = single_candidate(cfg, st, prop, "yes")
    assert yes is not None
    assert yes.fair_prob == pytest.approx(
        0.62 / 1.04 * 0.97
    )  # x P(plays): Kalshi's DNP rule is unverified
    assert yes.label == "strong"  # 59.6c fair vs 44c ask + fee
    assert yes.edge_cents > cfg.strong_cents
    no = single_candidate(cfg, st, prop, "no")
    assert no is not None and no.label == "none" and no.edge_cents < 0


def test_every_game_with_a_price_gets_a_play(cfg: JarvisConfig) -> None:
    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    best = best_per_game(cfg, st)
    assert set(best) == {G1, G2}
    assert best[G1].label == "strong"  # the LeBron over, 15c of edge
    assert best[G2].label == "none"  # no Pinnacle line: unmeasured, entertainment
    assert math.isnan(best[G2].edge_cents)


def test_plan_sizes_and_labels_are_computed(cfg: JarvisConfig, tmp_path) -> None:  # type: ignore[no-untyped-def]
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    plays = plan_slate(cfg, st, PaperBook(tmp_path / "b.duckdb"))
    by = {p.game_id: p for p in plays}
    strong, none = by[G1], by[G2]
    assert strong.contracts >= 1 and strong.stake_usd <= cfg.per_label_cap["strong"] + 1e-9
    assert 1.0 - 1e-9 <= none.stake_usd <= cfg.per_label_cap["none"] + 1e-9 or none.contracts >= 1
    assert strong.max_price >= strong.legs[0].price
    assert sum(p.stake_usd for p in plays) <= cfg.nightly_cap


def test_nightly_cap_binds_and_still_names_the_play(cfg: JarvisConfig, tmp_path) -> None:  # type: ignore[no-untyped-def]
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    book = PaperBook(tmp_path / "b.duckdb")
    tight = type(cfg)(**{**cfg.__dict__, "nightly_cap": 1.0})
    plays = plan_slate(tight, st, book)
    assert len(plays) == 2  # always a play for each game
    assert sum(p.stake_usd for p in plays) <= 1.0 + 1e-9
    assert any(p.contracts == 0 for p in plays)
    assert any("nightly cap" in n for p in plays for n in p.notes)


def test_stale_and_tipped_games_are_not_offered(cfg: JarvisConfig) -> None:
    late = gather(cfg, SLATE, NOW + timedelta(hours=5, minutes=15), use_polymarket=False)
    assert G1 not in best_per_game(cfg, late)  # tipped / inside min_minutes_to_tip
    stale = gather(cfg, SLATE, NOW + timedelta(minutes=45), use_polymarket=False)
    assert (
        best_per_game(cfg, stale) == {}
    )  # snapshot older than max_quote_age_min: nothing tradable


def test_reference_staleness_caps_label_at_thin(cfg: JarvisConfig) -> None:
    now = NOW + timedelta(
        minutes=20
    )  # Pinnacle read 19:30, kalshi 19:50 -> ref 50 min; make it older
    old = type(cfg)(**{**cfg.__dict__, "max_ref_age_min": 10.0, "max_quote_age_min": 60.0})
    st = gather(old, SLATE, now, use_polymarket=False)
    prop = next(q for q in st.quotes if q.kind == "prop")
    p = single_candidate(old, st, prop, "yes")
    assert p is not None and p.label == "thin" and any("capped at thin" in n for n in p.notes)


def test_first_recorded_size_is_kept_on_reemission(cfg: JarvisConfig, tmp_path) -> None:  # type: ignore[no-untyped-def]
    st = gather(cfg, SLATE, NOW, use_polymarket=False)
    book = PaperBook(tmp_path / "b.duckdb")
    first = plan_slate(cfg, st, book)
    book.record(first, source="slate", slate=SLATE, now=NOW)
    again = plan_slate(cfg, st, book)
    assert {p.key: p.contracts for p in again} == {p.key: p.contracts for p in first}
    assert book.spent_tonight(SLATE) == pytest.approx(sum(p.stake_usd for p in first))
    _ = (LAC,)
