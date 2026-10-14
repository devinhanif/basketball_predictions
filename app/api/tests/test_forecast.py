from __future__ import annotations

import pytest

from parlayhitters_api.forecast import TAUS, cdf, devig_over, line_probs

# A uniform-on-[0, 20] distribution: quantile at tau is 20 * tau.
UNIFORM = [20 * t for t in TAUS]


def test_cdf_interpolates_between_grid_points() -> None:
    f, extrapolated = cdf(UNIFORM, 10.0)
    assert f == pytest.approx(0.5)
    assert not extrapolated


def test_cdf_clamps_outside_the_stored_range() -> None:
    assert cdf(UNIFORM, 0.5) == (0.05, True)
    assert cdf(UNIFORM, 19.5) == (0.95, True)


def test_half_point_line_has_no_push() -> None:
    lp = line_probs(UNIFORM, 10.5)
    assert lp.p_over == pytest.approx(0.475)
    assert lp.p_under == pytest.approx(0.525)
    assert lp.p_push == pytest.approx(0.0, abs=1e-12)
    assert not lp.extrapolated


def test_whole_line_has_push_mass_and_sums_to_one() -> None:
    lp = line_probs(UNIFORM, 10.0)
    assert lp.p_push == pytest.approx(0.05)
    assert lp.p_over + lp.p_under + lp.p_push == pytest.approx(1.0)


def test_probabilities_are_monotone_in_the_line() -> None:
    overs = [line_probs(UNIFORM, x + 0.5).p_over for x in range(3, 17)]
    assert overs == sorted(overs, reverse=True)


def test_tied_quantiles_do_not_divide_by_zero() -> None:
    q = [0.0] * 6 + [1.0] * 8 + [2.0] * 5  # a count stat with plateaus
    lp = line_probs(q, 0.5)
    assert 0.0 <= lp.p_over <= 1.0
    assert lp.p_over + lp.p_under + lp.p_push == pytest.approx(1.0)


def test_rejects_unsorted_or_wrong_length_grids() -> None:
    with pytest.raises(ValueError):
        cdf([1.0, 0.0] + [2.0] * 17, 1.0)
    with pytest.raises(ValueError):
        cdf([1.0, 2.0], 1.0)


def test_devig_removes_the_overround() -> None:
    p = devig_over(-110, -110)
    assert p == pytest.approx(0.5)
    assert devig_over(-150, 130) > 0.5
    assert devig_over(-110, -120) < 0.5


def test_devig_rejects_invalid_prices() -> None:
    with pytest.raises(ValueError):
        devig_over(50, -110)
