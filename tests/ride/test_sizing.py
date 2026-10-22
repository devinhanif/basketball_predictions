from __future__ import annotations

import math

import pytest

from nba.parlay.config import FeeModel
from nba.ride import sizing


def fee_model() -> FeeModel:
    return sizing.load_fee()  # from configs/parlay.yaml: nothing hardcoded here


def test_credit_slice_splits_remaining_evenly() -> None:
    assert sizing.credit_slice(10.84, 0.0, 2).slice_usd == 5.42
    assert sizing.credit_slice(10.84, 5.42, 1).slice_usd == 5.42
    assert sizing.credit_slice(10.84, 0.0, 0).slice_usd == 10.84  # never divides by zero
    assert sizing.credit_slice(10.84, 20.0, 3).slice_usd == 0.0


def test_fee_matches_config_formula() -> None:
    f = fee_model()
    expected = (
        math.ceil(round(f.multiplier("KXNBAGAME") * f.rate("taker") * 5 * 0.97 * 0.03 * 100, 9))
        / 100
    )
    assert sizing.fee_for(0.97, 5, f) == expected
    assert sizing.fee_for(0.97, 5, f) >= 0.01  # rounded up once per order


def test_size_order_fits_slice_and_pays_one_dollar_per_contract() -> None:
    f = fee_model()
    s = sizing.size_order(5.42, 0.97, 0.985, f)
    assert s.fits and s.cost <= 5.42
    assert s.contracts == 5
    assert s.payout_if_hit == 5.0
    assert s.cost == pytest.approx(5 * 0.97 + s.fee)
    assert s.profit_if_hit == pytest.approx(5.0 - s.cost)
    assert s.expected_payout == pytest.approx(5 * 0.985)


def test_slice_below_one_contract_is_flagged() -> None:
    s = sizing.size_order(0.50, 0.97, 0.985, fee_model())
    assert s.contracts == 1 and not s.fits


def test_bad_ask_rejected() -> None:
    with pytest.raises(ValueError):
        sizing.size_order(5.0, 1.0, 0.99, fee_model())
