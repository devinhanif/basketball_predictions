"""F1_WARM_WINDOW gate helpers: the power formula and the declared sizing (no data, no fit)."""

from __future__ import annotations

from research.eval import f1_warm_window_gate as G


def test_power_is_half_when_the_effect_equals_the_need() -> None:
    assert abs(G.power_at(0.005, 0.001, 0.005) - 0.5) < 1e-9  # need = max(0.005, 0.00196) = 0.005


def test_power_rises_with_the_effect_and_falls_with_the_noise() -> None:
    assert G.power_at(0.01, 0.002, 0.005) > G.power_at(0.007, 0.002, 0.005)
    assert G.power_at(0.01, 0.002, 0.005) > G.power_at(0.01, 0.004, 0.005)


def test_interval_requirement_binds_when_the_se_is_large() -> None:
    # 1.96 * 0.0031 = 0.0061 > floor 0.005: an estimate at the floor is not enough
    assert G.power_at(0.005, 0.0031, 0.005) < 0.5


def test_declared_sizing_clears_the_gate_line() -> None:
    s = G.sizing()
    assert len(s) == 8
    assert all(v["power_at_1.5_floor"] >= 0.5 for v in s.values())
    assert s["pts_2023"]["floor"] == 0.005 and s["fg3m_2024"]["floor"] == 0.0025
