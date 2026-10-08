"""Sample-size guardrail: refuse to claim superiority from thin history."""

from __future__ import annotations

import pytest

from nba.kalshi.sampling import (
    DEFAULT_MIN_SETTLED_MARKETS,
    InsufficientSampleError,
    check_sample_size,
    describe,
)


def test_sufficient_sample_passes() -> None:
    verdict = check_sample_size(DEFAULT_MIN_SETTLED_MARKETS, minimum=DEFAULT_MIN_SETTLED_MARKETS)
    assert verdict.sufficient
    verdict.require_sufficient()  # must not raise


def test_insufficient_sample_flagged() -> None:
    verdict = check_sample_size(5, minimum=30)
    assert not verdict.sufficient


def test_insufficient_sample_raises_on_require() -> None:
    verdict = check_sample_size(5, minimum=30)
    with pytest.raises(InsufficientSampleError, match="n=5"):
        verdict.require_sufficient("model vs market comparison")


def test_negative_n_rejected() -> None:
    with pytest.raises(ValueError):
        check_sample_size(-1)


def test_describe_always_states_n_and_minimum() -> None:
    small = describe(3, minimum=30)
    big = describe(50, minimum=30)
    assert "n=3" in small and "30" in small
    assert "n=50" in big and "30" in big
