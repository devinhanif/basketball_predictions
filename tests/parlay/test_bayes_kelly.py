"""Bayesian-Kelly sizer: no network, no database; configs/parlay.yaml supplies the fee object."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from nba.parlay.__main__ import main
from nba.parlay.bayes_kelly import (
    Bet,
    load_bk_config,
    order_cost,
    parse_bets,
    posterior,
    size_bets,
    soft_floor,
)
from nba.parlay.config import DEFAULT_PATH, load_config
from nba.parlay.ev import NO_POSITIVE_EV

FIXTURE = Path(__file__).parent / "fixtures" / "bayes_kelly_tonight.json"
CFG = load_bk_config()
FEE = load_config().fee


def one(price: float, p: float, sd: float, **kw: Any) -> Bet:
    return Bet(
        ticker="KXT-A", side="no", price=price, p_center=p, p_sd=sd, order_type="maker", **kw
    )


def stake(b: Bet, bankroll: float = 100.0, cap: float = 50.0) -> int:
    r = size_bets([b], bankroll=bankroll, floor=25.0, cap=cap)
    return r.bets[0].contracts


def test_zero_edge_zero_stake() -> None:
    for p in (0.50, 0.70, 0.82):
        r = size_bets([one(p, p, 0.0)], bankroll=100.0, floor=25.0, cap=50.0)
        assert r.bets[0].contracts == 0
        assert r.verdict == NO_POSITIVE_EV
    # an edge smaller than the fee is also zero
    assert stake(one(0.50, 0.505, 0.0)) == 0


def test_more_uncertainty_smaller_stake() -> None:
    sizes = [stake(one(0.50, 0.58, sd)) for sd in (0.0, 0.02, 0.05, 0.10)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] > sizes[-1]


def test_plus3_with_5c_uncertainty_is_much_smaller_than_sure_plus3() -> None:
    sure = stake(one(0.50, 0.53, 0.0), bankroll=1000.0, cap=500.0)
    unsure = stake(one(0.50, 0.53, 0.05), bankroll=1000.0, cap=500.0)
    assert sure >= 10
    assert unsure <= sure // 3
    post = posterior(one(0.50, 0.53, 0.05), CFG)
    assert 0.50 < post.mean_p < 0.512  # the +3c is shrunk to about +1c


def test_matches_closed_form_without_fee_or_uncertainty(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw["fee_model"]["coefficient"] = 0.0
    raw["fee_model"]["maker_coefficient"] = 0.0
    cfg_path = tmp_path / "parlay_nofee.yaml"
    cfg_path.write_text(yaml.safe_dump(raw))
    price, q, bank = 0.60, 0.66, 100.0
    r = size_bets(
        [one(price, q, 0.0)], bankroll=bank, floor=25.0, cap=bank, parlay_cfg_path=cfg_path
    )
    n_star = bank * (q - price) / (price * (1 - price))  # n* = B (q - c) / (c (1 - c))
    assert abs(r.bets[0].contracts - n_star) <= 1.0
    assert r.bets[0].contracts_if_certain == r.bets[0].contracts


def test_fee_rounds_up_once_per_order_from_config() -> None:
    b = one(0.82, 0.9, 0.0)
    assert FEE.verified
    c1, f1 = order_cost(b, 1, FEE, "taker")
    c10, f10 = order_cost(b, 10, FEE, "taker")
    assert c1 == round(0.82 + f1, 2)
    assert f1 == 0.02  # ceil(0.07 * 0.82 * 0.18 = 1.03 cents) = 2 cents
    assert f10 == 0.11  # ceil(10.33 cents), not 10 * 2 cents
    assert c10 == round(8.2 + 0.11, 2)
    _, fm = order_cost(b, 10, FEE, "maker")
    assert fm < f10  # maker coefficient is a quarter of taker, from the same object
    assert order_cost(b, 0, FEE, "taker") == (0.0, 0.0)


def test_integer_contracts_and_costs_add_up() -> None:
    bets, corr = parse_bets(json.loads(FIXTURE.read_text()))
    bets = [replace(b, order_type="maker") for b in bets]
    r = size_bets(bets, bankroll=33.91, floor=25.25, cap=20.0, corr=corr)
    for row, b in zip(r.bets, bets, strict=True):
        assert isinstance(row.contracts, int)
        assert row.cost == order_cost(b, row.contracts, FEE, "maker")[0]
    assert r.total_cost == pytest.approx(sum(x.cost for x in r.bets))
    assert r.total_cost <= 20.0 + 1e-9


def test_floor_warns_but_does_not_block() -> None:
    b = one(0.50, 0.65, 0.0)
    low = size_bets([b], bankroll=30.0, floor=29.0, cap=20.0)
    assert low.total_cost > 1.0  # still sized: the floor does not block
    assert low.warning.startswith("WARNING")
    assert "floor" in low.warning
    safe = size_bets([b], bankroll=30.0, floor=5.0, cap=20.0)
    assert safe.warning.startswith("floor safe")
    assert soft_floor(CFG, 1) == pytest.approx(25.25)


def test_correlated_same_game_legs_reduce_combined_stake() -> None:
    a = one(0.50, 0.60, 0.0)
    c = replace(a, ticker="KXT-B")
    indep = size_bets([a, c], bankroll=100.0, floor=25.0, cap=100.0)
    same = size_bets(
        [replace(a, game="G"), replace(c, game="G")],
        bankroll=100.0,
        floor=25.0,
        cap=100.0,
        corr=[("KXT-A", "KXT-B", 0.8)],
    )
    assert same.total_cost < indep.total_cost
    assert same.expected_log_growth <= indep.expected_log_growth + 1e-9


def test_cap_and_tier_ceiling_hold() -> None:
    steady = replace(one(0.80, 0.95, 0.0), tier="steady")
    r = size_bets([steady], bankroll=100.0, floor=25.0, cap=20.0)
    assert r.total_cost <= 10.0 + 1e-9  # 50% steady ceiling
    swing = replace(one(0.50, 0.70, 0.0), tier="swing")
    r2 = size_bets([swing], bankroll=100.0, floor=25.0, cap=20.0)
    assert 9.0 < r2.total_cost <= 10.0 + 1e-9


def test_sources_pinnacle_centre_and_spread() -> None:
    b = Bet(
        ticker="KXT-S",
        side="no",
        price=0.60,
        sources={"pinnacle": 0.65, "model": 0.70, "kalshi_mid": 0.60},
    )
    p = posterior(b, CFG)
    assert p.centre == 0.65
    assert p.sd_p == pytest.approx(((0.05**2 + 0.05**2) / 2) ** 0.5)
    wide = replace(b, sources={"pinnacle": 0.65, "model": 0.80, "kalshi_mid": 0.60})
    assert posterior(wide, CFG).sd_p > p.sd_p
    with pytest.raises(ValueError):
        posterior(replace(b, sources={}), CFG)


def test_n_shrink_moves_estimate_toward_market() -> None:
    base = posterior(one(0.60, 0.70, 0.0), CFG)
    shrunk = posterior(one(0.60, 0.70, 0.0, n_settled=50), CFG)
    assert 0.60 < shrunk.centre < base.centre


def test_parse_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        parse_bets([{"ticker": "A", "price": 1.2, "p_center": 0.5}])
    with pytest.raises(ValueError):
        parse_bets([{"ticker": "A", "price": 0.5}, {"ticker": "A", "price": 0.4}])


def test_cli_tonight_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        ["size", "--bankroll", "33.91", "--floor", "25.25", "--cap", "20", "--bets", str(FIXTURE)]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict:" in out and "advice only" in out
    assert "KXNBATEAMPTS-GSW-O16.5" in out


def test_module_has_no_execution_surface() -> None:
    src = (Path(__file__).parents[2] / "nba" / "parlay" / "bayes_kelly.py").read_text().lower()
    for word in ("requests", "httpx", "urllib", "api_key", "/portfolio", "create_order", "socket"):
        assert word not in src
