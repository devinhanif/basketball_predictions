"""F8 lineup rebounding (docs/prereg/F8_LINEUP_REBOUNDING.md): pure helpers and guards."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import f8_lineup_rebounding as f8
from research.eval.f11_lineup_context import frozen_sha256
from research.features import lineup_rebounding as lr

DOC = Path(__file__).resolve().parents[3] / "docs/prereg/F8_LINEUP_REBOUNDING.md"


def test_frozen_prefix_matches_doc() -> None:
    assert frozen_sha256(DOC).startswith(f8.FROZEN_PREFIX)


def test_top_s_orders_by_weight_then_starts_then_id() -> None:
    idx = np.array([3, 5, 7, 9, 11])
    c = np.array([1.0, 1.0, 0.5, 1.0, 0.2])
    starts = np.zeros(20)
    starts[5] = 4.0
    assert list(lr._top_s(idx, c, starts, 3)) == [5, 3, 9]


def test_struct_double_big_and_heights() -> None:
    big = np.zeros(10)
    big[[0, 2]] = 1.0
    h = np.array([83.0, 75.0, 82.0, 77.0, np.nan, 78.0, 0, 0, 0, 0])
    dbl, h5, h2 = lr._struct(np.array([1, 2, 3, 4]), 0, big, h)
    assert dbl == 1.0
    assert h5 == pytest.approx((83 + 75 + 82 + 77) / 4)
    assert h2 == pytest.approx(83 + 82)


def test_is_big_definition() -> None:
    d = pl.DataFrame(
        {
            "height_in": [82.0, 81.0, 80.0, None],
            "position": ["Forward", "Center", "Guard", "Center-Forward"],
        }
    )
    assert d.select(lr.is_big_expr())["height_in"].to_list() == [True, True, False, True]


def test_placebo_keeps_marginals_within_team_season() -> None:
    n = 40
    d = pl.DataFrame(
        {
            "team_id": [1] * 20 + [2] * 20,
            "season": [2023] * n,
            "game_id": [str(i) for i in range(n)],
            "is_big_p": [float(i % 2) for i in range(n)],
            **{c: np.arange(n, dtype=float) + k for k, c in enumerate([*lr.LR_COLS, *lr.LR30])},
        }
    )
    p = f8.placebo_frame(d)
    assert p["is_big_p"].to_list() == d["is_big_p"].to_list()
    for t in (1, 2):
        a = d.filter(pl.col("team_id") == t)["lr_nbig"].sort().to_list()
        b = p.filter(pl.col("team_id") == t)["lr_nbig"].sort().to_list()
        assert a == b
    assert p["lr_nbig"].to_list() != d["lr_nbig"].to_list()
    # the columns move together: one permutation for all of them
    assert np.allclose(
        p["lr_h5"].to_numpy() - p["lr_nbig"].to_numpy(), d["lr_h5"][0] - d["lr_nbig"][0]
    )


def test_season_2025_is_refused() -> None:
    with pytest.raises(ValueError):
        f8.collect_arm(pl.DataFrame(), "reb", ContextResidualConfig(), [], (2025,))


def test_t30_names_swap_three_columns() -> None:
    assert set(lr.LR_COLS_T30) - set(lr.LR_COLS) == set(lr.LR30)
    assert len(lr.LR_COLS_T30) == len(lr.LR_COLS)
    assert f8.arm_names("reb", "A2")[-2:] == list(lr.LR_A)
