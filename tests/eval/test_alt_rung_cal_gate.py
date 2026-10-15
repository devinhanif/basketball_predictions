# ruff: noqa: E501
"""Unit tests for the ALT_RUNG_CAL gate helpers (docs/prereg/ALT_RUNG_CAL.md; DRAFT). No data files."""

from __future__ import annotations

import numpy as np

from research.eval import alt_rung_cal_gate as g


def test_bin_label_edges() -> None:
    p = np.array([0.49, 0.50, 0.59, 0.60, 0.70, 0.8, 0.9, 0.999, 1.0])
    assert list(g.bin_label(p)) == ["", "55", "55", "65", "75", "85", "95", "95", "95"]


def test_oracle_gap_cells_and_top() -> None:
    pm = np.array([0.55, 0.85, 0.92, 0.97])
    stat = np.array(["ast", "fg3m", "pts", "reb"])
    gap = g.oracle_gap(pm, stat)
    assert gap[0] == g.PUBLISHED_GAP["55"]["ast"]
    assert gap[1] == g.PUBLISHED_GAP["85"]["fg3m"]
    assert np.isclose(gap[2], 0.869 - 0.925)
    assert np.isclose(gap[3], 0.895 - 0.968)


def test_oracle_effect_matches_closed_form_kl() -> None:
    pm = np.array([0.85])
    out = g.oracle_effect(pm, np.array(["pts"]), np.array(["g1"]))
    a, t = 0.85, 0.85 - 0.0321
    kl = t * np.log(t / a) + (1 - t) * np.log((1 - t) / (1 - a))
    assert abs(out["mean_kl"] - round(float(kl), 5)) < 1e-9


def test_synthetic_projection_is_seeded_and_label_free() -> None:
    rng = np.random.default_rng(3)
    n = 2000
    pm = rng.uniform(0.5, 0.99, n)
    stat = rng.choice(np.array(["pts", "reb", "ast", "fg3m"]), n)
    game = np.array([f"g{i // 40}" for i in range(n)])
    unit = np.array([f"u{i // 3}" for i in range(n)])
    a = g.synthetic_projection(pm, stat, game, unit, seed=0, n_boot=100)
    b = g.synthetic_projection(pm, stat, game, unit, seed=0, n_boot=100)
    assert a == b
    assert a["synthetic_mean_gain"] > 0  # the oracle truth beats A0 on its own synthetic labels
    assert a["hw_game_bootstrap"] > 0


def test_conditioning_flags_constant_and_collinear_columns() -> None:
    x = np.linspace(-1, 1, 100)
    X = np.column_stack([np.ones(100), x, 2 * x + 1e-9 * np.sin(x), np.zeros(100)])
    c = g.conditioning(X)
    assert c["constant_columns"] == 1
    assert c["max_abs_corr"] > 0.999


def _toy_rungs() -> dict[str, np.ndarray]:
    dates = np.array(
        ["2024-01-05", "2024-02-10", "2024-02-20", "2024-03-05"], dtype="datetime64[D]"
    )
    n = dates.size
    return {
        "season": np.full(n, 2023),
        "pm": np.array([0.55, 0.65, 0.75, 0.95]),
        "stat": np.array(["pts"] * n),
        "game": np.array([f"g{i}" for i in range(n)]),
        "player": np.arange(n),
        "date": dates,
        "x": np.zeros(n),
        "z": np.zeros(n),
        "r": np.zeros(n),
        "has_main": np.ones(n, dtype=bool),
        "bench": np.zeros(n, dtype=np.int64),
        "lead": np.full(n, 64.0),
    }


def test_stage_block_marks_blocks_without_enough_earlier_rows_as_identity() -> None:
    r = _toy_rungs()
    legs = {
        "stat": np.array(["pts"]),
        "date": np.array(["2023-11-01"], dtype="datetime64[D]"),
        "x": np.zeros(1),
        "z": np.zeros(1),
        "game": np.array(["l0"]),
    }
    out = g.stage_block((2023,), r, legs)
    assert out["rungs_common_to_all_arms"] == 0  # fewer than MIN_FIT earlier rows in every block
    assert out["identity_blocks"] == out["blocks_total"] == 3


def test_stage_block_fit_rows_are_strictly_earlier() -> None:
    r = _toy_rungs()
    n = 2100
    r2 = {k: np.concatenate([v, np.repeat(v[:1], n)]) for k, v in r.items()}
    r2["date"][4:] = np.datetime64("2024-01-05")
    r2["season"][4:] = 2023
    legs = {
        "stat": np.array(["pts"] * n),
        "date": np.full(n, np.datetime64("2023-12-20")),
        "x": np.zeros(n),
        "z": np.zeros(n),
        "game": np.array(["l"] * n),
    }
    out = g.stage_block((2023,), r2, legs)
    jan = next(b for b in out["blocks"] if b["block"] == "2024-01")
    feb = next(b for b in out["blocks"] if b["block"] == "2024-02")
    assert jan["n_fit_rungs"] == 0  # the January rungs are not their own fit rows
    assert feb["n_fit_rungs"] == n + 1  # the 2,100 + 1 January rungs, strictly earlier
    assert feb["all_arms_fit"] is True
