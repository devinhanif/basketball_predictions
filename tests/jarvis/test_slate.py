from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta

import pytest

from nba.jarvis.book import PaperBook
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import PlayLeg
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
    assert single_candidate(cfg, st, prop, "no") is None  # props are Over / Yes only (2026-10-27)
    # the flag, not hardcoding: with both sides configured the No is priced again
    both = type(cfg)(**{**cfg.__dict__, "props_sides": ("yes", "no")})
    no = single_candidate(both, st, prop, "no")
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


def test_no_prop_no_in_any_slate_output_but_game_no_still_offered(cfg: JarvisConfig) -> None:
    from nba.jarvis import edge as E

    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    assert cfg.props_sides == ("yes",)
    plays = plan_slate(cfg, st)
    assert plays
    for p in plays:
        for leg in p.legs:
            assert not (leg.kind == "prop" and leg.side == "no")
    # the single-candidate path: every prop quote, No side
    for q in (q for q in st.quotes if q.kind == "prop"):
        assert single_candidate(cfg, st, q, "no") is None
        assert not E.side_allowed(cfg, "prop", "no") and E.side_allowed(cfg, "prop", "yes")
    # game markets keep both sides
    g = next(q for q in st.quotes if q.kind == "game_win" and q.venue == "kalshi" and q.ask("no"))
    assert E.side_allowed(cfg, "game_win", "no")
    assert single_candidate(cfg, st, g, "no") is not None
    # a ladder arbitrage never uses a prop No, even if one is priced to look like a cover
    prop = next(q for q in st.quotes if q.kind == "prop")
    rigged = replace(prop, yes_bid=0.97, yes_ask=0.98)
    assert E.best_arbitrage(cfg, [rigged]) is None


def test_arbitrage_stake_15_fits_the_cap_and_reserve(cfg: JarvisConfig, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from nba.jarvis import edge as E
    from nba.jarvis.slate import size_plays
    from tests.jarvis.test_review_fixes import mkplay

    assert E.stake_for(cfg, "arbitrage") == 15.0
    assert cfg.per_label_cap["arbitrage"] == 15.0 and cfg.per_bet_cap == 15.0
    assert cfg.nightly_cap == 20.0 and cfg.reserve_for_strong == 10.0
    assert cfg.nightly_cap >= 15.0  # one confirmed arbitrage fits the nightly cap on an empty night
    legs = (
        PlayLeg("kalshi", "A", "yes", 0.46, "game_win", "LAL to win", team_id=LAL),
        PlayLeg("polymarket", "B", "yes", 0.44, "game_win", "LAC to win", team_id=LAC),
    )
    arb = replace(mkplay("arbitrage", "ARB", 8.0), legs=legs)
    book = PaperBook(tmp_path / "b.duckdb")
    sized = size_plays(cfg, [arb], book, SLATE)[0]
    assert 13.0 < sized.stake_usd <= 15.0 + 1e-9 and sized.contracts > 0  # both legs together
    # reserve logic unchanged: $10 of entertainment spend (the whole non-reserved pool) leaves
    # exactly the reserve, and the arbitrage is sized into it (never over the nightly cap)
    nones = [
        replace(
            mkplay("none", f"N{i}", -0.2), inputs={"leg_costs": [1.25]}, contracts=2, stake_usd=1.25
        )
        for i in range(8)
    ]
    book.record(nones, source="slate", slate=SLATE, now=NOW)
    after = size_plays(cfg, [arb], book, SLATE)[0]
    spent = 10.0
    assert 0 < after.stake_usd <= cfg.nightly_cap - spent + 1e-9


def test_sides_props_config_fails_closed(cfg: JarvisConfig, root, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from nba.jarvis.config import DEFAULT_PATH, load_config

    base = DEFAULT_PATH.read_text()
    good = 'props: ["yes"]'
    assert good in base

    def load(text: str) -> JarvisConfig:
        f = tmp_path / "j.yaml"
        f.write_text(text)
        return load_config(f, root=root)

    for bad in (
        'props: ["no"]',
        "props: []",
        "props: yes_only",
        "props:",
        'props: ["yes", "yes"]',
        'props: ["yes", "no", "no"]',
        'props: ["yes", "maybe"]',
        'props: ["yes", "no", "push"]',
    ):
        with pytest.raises(ValueError):
            load(base.replace(good, bad))
    with pytest.raises(Exception, match="duplicate key"):
        load(base.replace(good, good + '\n  props: ["yes", "no"]'))
    assert load(base).props_sides == ("yes",)
    assert load(base).props_by_venue == {}  # shipped default: Yes only everywhere
    # both accepted shapes (bare yes/no are YAML booleans: also read)
    assert load(base.replace(good, 'props: ["yes", "no"]')).props_sides == ("yes", "no")
    assert load(base.replace(good, "props: [no, yes]")).props_sides == ("yes", "no")
    assert load(base.replace(good, "props: [yes]")).props_sides == ("yes",)


def test_props_by_venue_config_loads_and_fails_closed(cfg: JarvisConfig, root, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from nba.jarvis.config import DEFAULT_PATH, load_config

    base = DEFAULT_PATH.read_text()
    good = 'props: ["yes"]'

    def load(extra: str) -> JarvisConfig:
        f = tmp_path / "j.yaml"
        f.write_text(base.replace(good, good + "\n  " + extra))
        return load_config(f, root=root)

    c = load("props_by_venue: {kalshi: [yes, no], polymarket: [yes]}")
    assert c.props_sides == ("yes",)
    assert c.props_by_venue == {"kalshi": ("yes", "no"), "polymarket": ("yes",)}
    assert c.props_sides_for("kalshi") == ("yes", "no")
    assert c.props_sides_for("polymarket") == ("yes",)
    only = load('props_by_venue: {kalshi: ["yes", "no"]}')
    assert only.props_sides_for("polymarket") == ("yes",)  # not listed: the global default
    assert only.props_sides_for(None) == ("yes",)
    assert load("props_by_venue: {}").props_by_venue == {}
    for bad in (
        'props_by_venue: {kalshi: ["no"]}',
        "props_by_venue: {kalshi: []}",
        "props_by_venue: {kalshi: yes}",
        'props_by_venue: {kalshi: ["yes", "yes"]}',
        'props_by_venue: {kalshi: ["yes", "maybe"]}',
        'props_by_venue: {draftkings: ["yes", "no"]}',  # unknown venue
        "props_by_venue: [kalshi]",
        "props_by_venue:",
        'props_by_venues: {kalshi: ["yes", "no"]}',  # a typo must not be silently ignored
    ):
        with pytest.raises(ValueError):
            load(bad)
    with pytest.raises(Exception, match="duplicate key"):
        load('props_by_venue: {kalshi: ["yes"], kalshi: ["yes", "no"]}')
    with pytest.raises(Exception, match="duplicate key"):
        load('props_by_venue: {kalshi: ["yes"]}\n  props_by_venue: {kalshi: ["yes", "no"]}')


def _with_venues(cfg: JarvisConfig, **by_venue: tuple[str, ...]) -> JarvisConfig:
    return replace(cfg, props_by_venue=dict(by_venue))


def test_prop_no_appears_only_where_its_venue_allows_it(cfg: JarvisConfig) -> None:
    from nba.jarvis import edge as E

    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop" and q.venue == "kalshi")
    poly_prop = replace(prop, venue="polymarket", ticker="POLYUS:synthetic:prop")
    both = _with_venues(cfg, kalshi=("yes", "no"), polymarket=("yes",))
    # shipped default: neither venue
    assert not E.side_allowed(cfg, "prop", "no", "kalshi")
    assert single_candidate(cfg, st, prop, "no") is None
    # kalshi opened, polymarket not
    assert E.side_allowed(both, "prop", "no", "kalshi") and E.side_allowed(
        both, "prop", "yes", "kalshi"
    )
    assert not E.side_allowed(both, "prop", "no", "polymarket")
    assert E.side_allowed(both, "prop", "yes", "polymarket")
    no = single_candidate(both, st, prop, "no")
    assert no is not None and no.legs[0].side == "no" and no.legs[0].kind == "prop"
    assert no.edge_cents < 0 and no.label == "none"  # the stub model prices the LeBron over rich
    assert single_candidate(both, st, poly_prop, "no") is None
    assert single_candidate(both, st, poly_prop, "yes") is not None
    # the other direction: kalshi closed by the override even when the global default is open
    inverted = replace(cfg, props_sides=("yes", "no"), props_by_venue={"kalshi": ("yes",)})
    assert single_candidate(inverted, st, prop, "no") is None
    assert single_candidate(inverted, st, poly_prop, "no") is not None
    # the Polymarket No skip in the per-game search is the fallback and still holds
    poly_all = _with_venues(cfg, kalshi=("yes", "no"), polymarket=("yes", "no"))
    assert E.side_allowed(poly_all, "prop", "no", "polymarket")  # config says yes ...
    plays = best_per_game(poly_all, replace(st, quotes=[poly_prop]))  # ... slate.py still skips
    assert all(leg.side != "no" for p in plays.values() for leg in p.legs)
    # the plan under the shipped default never carries a prop No
    for pl in plan_slate(cfg, st):
        assert not any(leg.kind == "prop" and leg.side == "no" for leg in pl.legs)
    opened = _with_venues(cfg, kalshi=("yes", "no"))
    cands = [
        single_candidate(opened, st, q, "no")
        for q in st.quotes
        if q.kind == "prop" and q.venue == "kalshi"
    ]
    assert any(c is not None for c in cands)
    closed = [single_candidate(cfg, st, q, "no") for q in st.quotes if q.kind == "prop"]
    assert all(c is None for c in closed)


def test_game_markets_are_unaffected_by_the_props_keys(cfg: JarvisConfig) -> None:
    from nba.jarvis import edge as E

    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    g = next(q for q in st.quotes if q.kind == "game_win" and q.venue == "kalshi" and q.ask("no"))
    for variant in (
        cfg,
        _with_venues(cfg, kalshi=("yes", "no")),
        _with_venues(cfg, kalshi=("yes",), polymarket=("yes",)),
        replace(cfg, props_sides=("yes", "no")),
    ):
        for v in ("kalshi", "polymarket", None):
            assert E.side_allowed(variant, "game_win", "no", v) and E.side_allowed(
                variant, "game_win", "yes", v
            )
        c = single_candidate(variant, st, g, "no")
        assert c is not None
        base = single_candidate(cfg, st, g, "no")
        assert base is not None
        assert (c.edge_cents == base.edge_cents) or (
            math.isnan(c.edge_cents) and math.isnan(base.edge_cents)
        )
    # a Polymarket game No is still skipped by the per-game search
    poly_g = replace(g, venue="polymarket", ticker="POLYUS:synthetic:g")
    plays = best_per_game(_with_venues(cfg, polymarket=("yes", "no")), replace(st, quotes=[poly_g]))
    assert all(leg.side != "no" for p in plays.values() for leg in p.legs)


def test_pick_follows_the_venue_rule(cfg: JarvisConfig) -> None:
    from nba.jarvis.picks import PickError, build_pick

    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop" and q.venue == "kalshi")
    opened = _with_venues(cfg, kalshi=("yes", "no"), polymarket=("yes",))

    def pick(c: JarvisConfig, side: str):  # type: ignore[no-untyped-def]
        return build_pick(
            c, st, "x", venue="kalshi", price=0.5, stake=2.0, ticker=prop.ticker, side=side
        )

    with pytest.raises(PickError, match="Over / Yes only"):
        pick(cfg, "no")
    assert pick(cfg, "yes")
    res = pick(opened, "no")
    assert res.play.legs[0].side == "no" and res.play.legs[0].kind == "prop"
    assert pick(opened, "yes")
    # polymarket stays Yes-only: a pick naming a polymarket prop No is refused, game picks are not
    poly_prop = replace(prop, venue="polymarket", ticker="POLYUS:synthetic:prop")
    st_poly = replace(st, quotes=[*st.quotes, poly_prop])
    with pytest.raises(PickError, match="Over / Yes only"):
        build_pick(
            opened,
            st_poly,
            "x",
            venue="polymarket",
            price=0.5,
            stake=2.0,
            ticker=poly_prop.ticker,
            side="no",
        )
    g = next(q for q in st.quotes if q.kind == "game_win" and q.venue == "kalshi")
    assert build_pick(
        opened, st, "x", venue="kalshi", price=0.5, stake=2.0, ticker=g.ticker, side="no"
    )


def test_pick_on_a_prop_no_is_refused(cfg: JarvisConfig) -> None:
    from nba.jarvis.picks import PickError, build_pick

    st = gather(cfg, SLATE, NOW, use_polymarket=False, model=StubModel())
    prop = next(q for q in st.quotes if q.kind == "prop" and q.venue == "kalshi")
    with pytest.raises(PickError, match="Over / Yes only"):
        build_pick(
            cfg, st, "x", venue="kalshi", price=0.5, stake=2.0, ticker=prop.ticker, side="no"
        )
    g = next(q for q in st.quotes if q.kind == "game_win" and q.venue == "kalshi")
    assert build_pick(
        cfg, st, "x", venue="kalshi", price=0.5, stake=2.0, ticker=g.ticker, side="no"
    )


def test_fastarb_keeps_player_props_excluded() -> None:
    import yaml

    from nba.jarvis.config import ROOT

    raw = yaml.safe_load((ROOT / "configs" / "fastarb.yaml").read_text())
    found = []

    def walk(o: object) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "include_player_props":
                    found.append(v)
                walk(v)

    walk(raw)
    assert found and all(v is False for v in found)
