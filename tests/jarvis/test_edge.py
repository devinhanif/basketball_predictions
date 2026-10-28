from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nba.jarvis import edge as E
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Quote

TS = datetime(2026, 10, 28, 19, 50)


def q(venue: str, ticker: str, team: int, bid: float, ask: float, gid: str = "G") -> Quote:
    return Quote(
        venue, ticker, gid, "game_win", TS, bid, ask, team_id=team, label=f"T{team} to win"
    )


def test_fee_comes_from_the_verified_config_object(cfg: JarvisConfig) -> None:
    # Kalshi rate from configs/parlay.yaml (0.07 taker). Labels use the fee on a ONE-contract order,
    # which Kalshi rounds up to the cent: 0.07 x .25 = 1.75c -> 2c (conservative for larger orders).
    assert cfg.fee("kalshi").verified is True
    assert E.fee_per_contract_raw(cfg, "kalshi", 0.5) == pytest.approx(0.02)
    assert E.order_cost(cfg, "kalshi", 0.5, 100) == pytest.approx(50 + 1.75)  # real 100-lot order
    assert cfg.fee("polymarket").verified is True  # Polymarket US object (fees.md + feeCoefficient)
    assert E.fee_per_contract_raw(cfg, "polymarket", 0.5) == pytest.approx(
        0.02
    )  # 0.017375, half-even


def test_no_rate_or_threshold_is_hardcoded_in_the_package() -> None:
    src = "\n".join(p.read_text() for p in Path("nba/jarvis").glob("*.py"))
    for lit in ("0.07", "0.0175", "0.05 *", "* 0.05"):
        assert lit not in src, lit


def test_edge_includes_fee_and_label_thresholds(cfg: JarvisConfig) -> None:
    e = E.edge_cents(cfg, "kalshi", 0.55, 0.50)  # 5c gross - 2c one-contract fee
    assert e == pytest.approx(3.0, abs=0.01)
    assert E.classify(cfg, e) == "strong"
    assert E.classify(cfg, 2.99) == "thin"
    assert E.classify(cfg, 0.0) == "thin"
    assert E.classify(cfg, -0.01) == "none"
    assert E.classify(cfg, None) == "none"
    assert (
        E.classify(cfg, 9.0, capped=True) == "thin"
    )  # stale or skewed prices cannot make it strong


def test_a_tiny_edge_at_one_contract_is_none_not_thin(cfg: JarvisConfig) -> None:
    # +0.3c on a 100-lot (0.07 x p(1-p) = 1.75c) would be thin; at a 1-contract order the fee rounds
    # up to 2c, so the label that holds at the size actually staked is none
    fair, ask = 0.5190, 0.50
    assert E.edge_cents(cfg, "kalshi", fair, ask) < 0
    assert E.classify(cfg, E.edge_cents(cfg, "kalshi", fair, ask)) == "none"


def test_fair_price_is_never_labelled_an_edge(cfg: JarvisConfig) -> None:
    fair = 0.50
    assert E.edge_cents(cfg, "kalshi", fair, 0.50) < 0
    assert E.classify(cfg, E.edge_cents(cfg, "kalshi", fair, 0.50)) == "none"


def test_devig_and_american() -> None:
    a, b = E.devig_pair(0.56, 0.49)
    assert a + b == pytest.approx(1.0)
    assert a == pytest.approx(0.56 / 1.05)
    assert E.american_to_prob(-200) == pytest.approx(2 / 3)
    assert E.american_to_prob(150) == pytest.approx(0.4)


def test_max_price_keeps_the_label(cfg: JarvisConfig) -> None:
    fair, ask = 0.60, 0.50
    mp = E.max_price(cfg, "kalshi", fair, ask, "strong")
    assert mp >= ask
    assert E.edge_cents(cfg, "kalshi", fair, mp) >= cfg.strong_cents - 1e-9
    assert E.edge_cents(cfg, "kalshi", fair, mp + 0.01) < cfg.strong_cents
    assert E.max_price(cfg, "kalshi", fair, ask, "none") == ask
    assert E.max_price(cfg, "kalshi", None, ask, "thin") == ask


def test_stake_ladder_is_monotone_in_the_label(cfg: JarvisConfig) -> None:
    steps = [E.stake_for(cfg, k) for k in ("none", "thin", "strong", "arbitrage")]
    assert steps == [1.0, 3.0, 5.0, 15.0]
    assert steps == sorted(steps) and len(set(steps)) == 4
    assert E.stake_for(cfg, "arb_candidate") == E.stake_for(
        cfg, "thin"
    )  # unconfirmed: thin at most
    for k in ("strong", "arbitrage", "thin"):  # the kill switch: everything at the none size
        assert E.stake_for(cfg, k, none_level=True) == 1.0


def test_none_plays_are_one_or_two_contracts(cfg: JarvisConfig) -> None:
    for price in (0.10, 0.50, 0.91):
        n = E.contracts_for(cfg, "kalshi", price, 1.0, 20.0, max_contracts=cfg.none_max_contracts)
        assert 0 <= n <= 2
    assert E.contracts_for(cfg, "kalshi", 0.5, 1.0, 20.0, max_contracts=2) >= 1


def test_contracts_fit_budget_and_cap(cfg: JarvisConfig) -> None:
    n = E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=20.0)
    assert n >= 1 and E.order_cost(cfg, "kalshi", 0.50, n) <= 3.0 + 1e-9
    assert E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=0.4) == 0  # nightly cap bites
    assert E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=0.0) == 0


def test_arbitrage_needs_cost_below_a_dollar_after_fees(cfg: JarvisConfig) -> None:
    # YES A at 44c (kalshi) + YES B at 48c (kalshi): 92c + fees < $1
    a, b = q("kalshi", "A", 1, 0.40, 0.44), q("kalshi", "B", 2, 0.44, 0.48)
    got = E.best_arbitrage(cfg, [a, b])
    assert got is not None
    assert got.profit_cents == pytest.approx(
        100
        - 44
        - 48
        - 100
        * (
            E.fee_per_contract_raw(cfg, "kalshi", 0.44)
            + E.fee_per_contract_raw(cfg, "kalshi", 0.48)
        ),
        abs=1e-6,
    )
    assert got.fees_verified is True and got.confirmed is False  # snapshot reads never confirm
    # asks that sum to 1.01 are not an arbitrage
    assert (
        E.best_arbitrage(cfg, [q("kalshi", "A", 1, 0.48, 0.50), q("kalshi", "B", 2, 0.49, 0.51)])
        is None
    )


def test_a_cover_is_confirmed_only_by_a_live_source_with_legs_close_in_time(
    cfg: JarvisConfig,
) -> None:
    from dataclasses import replace
    from datetime import timedelta

    a = replace(q("kalshi", "A", 1, 0.40, 0.44), confirmed=True)
    b = replace(q("polymarket", "P", 2, 0.44, 0.47), confirmed=True)
    assert E.best_arbitrage(cfg, [a, b]).confirmed is True  # type: ignore[union-attr]
    late = replace(b, ts=TS + timedelta(seconds=cfg.confirmed_max_gap_s + 1))
    assert E.best_arbitrage(cfg, [a, late]).confirmed is False  # type: ignore[union-attr]
    assert E.best_arbitrage(cfg, [a, replace(b, confirmed=False)]).confirmed is False  # type: ignore[union-attr]


def test_cover_fee_verification_follows_the_config(cfg: JarvisConfig) -> None:
    a, b = q("kalshi", "A", 1, 0.40, 0.44), q("polymarket", "P", 2, 0.44, 0.47)
    got = E.best_arbitrage(cfg, [a, b])
    assert got is not None and got.fees_verified is True
    unverified = dict(cfg.fees)
    unverified["polymarket"] = type(cfg.fees["polymarket"])(
        **{**cfg.fees["polymarket"].__dict__, "verified": False}
    )
    cfg2 = type(cfg)(**{**cfg.__dict__, "fees": unverified})
    got2 = E.best_arbitrage(cfg2, [a, b])
    assert got2 is not None and got2.fees_verified is False


def test_same_team_yes_and_no_cover_across_venues(cfg: JarvisConfig) -> None:
    # NO A on kalshi costs 1 - 0.60 = 40c; YES A on polymarket costs 52c: the same event both ways
    a_k, a_p = q("kalshi", "A", 1, 0.60, 0.64), q("polymarket", "P", 1, 0.50, 0.52)
    got = E.best_arbitrage(cfg, [a_k, a_p])
    assert got is not None and (got.s1, got.s2) == ("no", "yes")
    # a Polymarket US NO leg is never built (the other team's YES token is the same bet)
    only_poly_no = E.best_arbitrage(
        cfg, [q("kalshi", "A", 1, 0.40, 0.44), q("polymarket", "P", 1, 0.50, 0.52)]
    )
    assert only_poly_no is None
    # one market never covers itself
    assert E.best_arbitrage(cfg, [a_k]) is None


def test_thin_book_prices_are_not_offered(cfg: JarvisConfig) -> None:
    assert q("kalshi", "A", 1, 0.0, 0.0).ask("no") == 1.0
    assert (
        E.best_arbitrage(cfg, [Quote("kalshi", "A", "G", "game_win", TS, None, None, team_id=1)])
        is None
    )
