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
    # Kalshi rate from configs/parlay.yaml (0.07 taker): fee at 50c = 0.07 * 0.25 = 1.75c
    assert cfg.fee("kalshi").verified is True
    assert E.fee_per_contract_raw(cfg, "kalshi", 0.5) == pytest.approx(0.0175, abs=1e-4)
    assert cfg.fee("polymarket").verified is False  # docs-sourced, not yet confirmed by an order
    assert E.fee_per_contract_raw(cfg, "polymarket", 0.5) == pytest.approx(0.0125, abs=1e-4)


def test_no_rate_or_threshold_is_hardcoded_in_the_package() -> None:
    src = "\n".join(p.read_text() for p in Path("nba/jarvis").glob("*.py"))
    for lit in ("0.07", "0.0175", "0.05 *", "* 0.05"):
        assert lit not in src, lit


def test_edge_includes_fee_and_label_thresholds(cfg: JarvisConfig) -> None:
    e = E.edge_cents(cfg, "kalshi", 0.55, 0.50)  # 5c gross - 1.75c fee
    assert e == pytest.approx(3.25, abs=0.01)
    assert E.classify(cfg, e) == "strong"
    assert E.classify(cfg, 2.99) == "thin"
    assert E.classify(cfg, 0.0) == "thin"
    assert E.classify(cfg, -0.01) == "none"
    assert E.classify(cfg, None) == "none"
    assert E.classify(cfg, 9.0, ref_stale=True) == "thin"  # a stale line cannot make a strong play


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


def test_kelly_and_stake_scale_with_edge(cfg: JarvisConfig) -> None:
    assert E.kelly_fraction_of_bankroll(0.5, 0.5) == 0.0
    assert E.kelly_fraction_of_bankroll(0.6, 0.5) == pytest.approx(0.2)
    small = E.budget_for(cfg, "thin", "kalshi", 0.52, 0.50)
    big = E.budget_for(cfg, "strong", "kalshi", 0.70, 0.50)
    assert small == cfg.min_bet  # thin is $1-3
    assert cfg.min_bet <= big <= cfg.per_label_cap["strong"]
    assert big > small


def test_none_stake_is_flat_one_to_three(cfg: JarvisConfig) -> None:
    for e in (-0.2, -1.5, -5.0, None):
        assert 1.0 <= E.none_budget(cfg, e) <= 3.0
    assert E.none_budget(cfg, -0.2) >= E.none_budget(cfg, -5.0)


def test_contracts_fit_budget_and_cap(cfg: JarvisConfig) -> None:
    n = E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=20.0)
    assert n >= 1 and E.order_cost(cfg, "kalshi", 0.50, n) <= 3.0 + 1e-9
    assert E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=0.4) == 0  # nightly cap bites
    assert E.contracts_for(cfg, "kalshi", 0.50, 3.0, remaining=0.0) == 0


def test_arbitrage_needs_cost_below_a_dollar_after_fees(cfg: JarvisConfig) -> None:
    # YES A at 44c (kalshi) + YES B at 48c (kalshi): 92c + fees ~3.5c < $1 -> +4.5c per pair
    a, b = q("kalshi", "A", 1, 0.40, 0.44), q("kalshi", "B", 2, 0.44, 0.48)
    got = E.best_arbitrage(cfg, [a, b])
    assert got is not None
    _q1, _s1, _q2, _s2, profit, verified = got
    assert profit == pytest.approx(
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
    assert verified is True
    # asks that sum to 1.01 are not an arbitrage
    assert (
        E.best_arbitrage(cfg, [q("kalshi", "A", 1, 0.48, 0.50), q("kalshi", "B", 2, 0.49, 0.51)])
        is None
    )


def test_arbitrage_across_venues_flags_unverified_fee(cfg: JarvisConfig) -> None:
    a, b = q("kalshi", "A", 1, 0.40, 0.44), q("polymarket", "P", 2, 0.44, 0.47)
    got = E.best_arbitrage(cfg, [a, b])
    assert got is not None and got[5] is False  # polymarket fee is not verified


def test_same_team_yes_and_no_cover_across_venues(cfg: JarvisConfig) -> None:
    # YES A on kalshi at 44c and NO A on polymarket at 1 - 0.50 = 50c: the same event both ways
    a_k, a_p = q("kalshi", "A", 1, 0.40, 0.44), q("polymarket", "P", 1, 0.50, 0.52)
    got = E.best_arbitrage(cfg, [a_k, a_p])
    assert got is not None and (got[1], got[3]) == ("yes", "no") and got[5] is False
    # consistent markets (NO A costs 1 - 0.43 = 57c): 44 + 57 + fees > $1, no arbitrage
    assert E.best_arbitrage(cfg, [a_k, q("polymarket", "P", 1, 0.43, 0.45)]) is None
    # one market never covers itself
    assert E.best_arbitrage(cfg, [a_k]) is None


def test_thin_book_prices_are_not_offered(cfg: JarvisConfig) -> None:
    assert q("kalshi", "A", 1, 0.0, 0.0).ask("no") == 1.0
    assert (
        E.best_arbitrage(cfg, [Quote("kalshi", "A", "G", "game_win", TS, None, None, team_id=1)])
        is None
    )
