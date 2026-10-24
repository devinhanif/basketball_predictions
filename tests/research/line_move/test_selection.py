"""T510 selection (shared with T510b): snapshots, one rung, no fallback, valuation."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from research.line_move import spec
from research.line_move.selection import (
    PSG,
    Forecast,
    Rung,
    SelectionLog,
    good_quote,
    select_psg,
    valuation,
)

TIP = 1_800_000_000
MIN = 60


def rung(k: int, rows: list[tuple[int, float, float]], ticker: str | None = None) -> Rung:
    a = np.array(rows, dtype=float)
    return Rung(ticker or f"T{k}", k, a[:, 0].astype(np.int64), a[:, 1], a[:, 2])


def snaps(
    mid: float, half: float = 0.02, start: int = -240, stop: int = 0, step: int = 5
) -> list[tuple[int, float, float]]:
    return [(TIP + m * MIN, mid - half, mid + half) for m in range(start, stop, step)]


def fc(
    counts: tuple[int, ...] | None = (100, 80, 60, 40, 20), n: int = 100, play: float = 1.0
) -> Forecast:
    return Forecast(counts, n, play)


def psg(
    rungs: list[Rung], m: Forecast | None = None, b: Forecast | None = None, made_min: int = -600
) -> PSG:
    return PSG(
        1, "pts", date(2026, 10, 22), "G1", TIP, TIP + made_min * MIN, m or fc(), b or fc(), rungs
    )


def test_picks_the_rung_closest_to_half_ties_to_lower_k() -> None:
    p = psg([rung(2, snaps(0.60)), rung(3, snaps(0.40)), rung(4, snaps(0.20))])
    s = select_psg(p, SelectionLog())
    assert s is not None and s.k == 2  # 0.60 and 0.40 tie at distance 0.10 -> lower k
    p = psg([rung(2, snaps(0.60)), rung(3, snaps(0.52)), rung(4, snaps(0.20))])
    s2 = select_psg(p, SelectionLog())
    assert s2 is not None and s2.k == 3


def test_s0_is_first_snapshot_at_or_after_max_made_at_and_tip_minus_150() -> None:
    rows = [(TIP + m * MIN, 0.4 + m / 1e4, 0.44 + m / 1e4) for m in range(-240, 0, 5)]
    s = select_psg(psg([rung(2, rows)]), SelectionLog())
    assert s is not None and s.s0_s == TIP - 150 * MIN
    # a forecast made later than tip - 150 moves S0 to its made_at snapshot
    s2 = select_psg(psg([rung(2, rows)], made_min=-100), SelectionLog())
    assert s2 is not None and s2.s0_s == TIP - 100 * MIN
    # S1 = first snapshot >= S0 + 15 min; close = last strictly before tipoff
    assert s.s1_s == s.s0_s + 15 * MIN and s.close_s == TIP - 5 * MIN


def test_close_gap_and_filters_drop_without_rung_fallback_and_count_by_sign() -> None:
    bad_close = snaps(0.50, step=5)[:-1] + [(TIP - 1 * MIN, 0.10, 0.60)]  # wide close quote
    good = snaps(0.70)
    p = psg([rung(2, bad_close), rung(3, good)])  # chosen rung (0.50) fails close; 0.70 is fine
    log = SelectionLog()
    assert select_psg(p, log) is None  # no fallback to the other rung
    assert log.drops["drop_S1_close_filter"] == 1
    assert (
        log.drop_signs[("drop_S1_close_filter", "T510")][-1]
        + log.drop_signs[("drop_S1_close_filter", "T510")][1]
        == 1
    )
    # close - S1 < 30 min is a drop as well
    short = [(TIP + m * MIN, 0.48, 0.52) for m in (-150, -135, -120)]
    log2 = SelectionLog()
    assert select_psg(psg([rung(2, short + [(TIP - 110 * MIN, 0.48, 0.52)])]), log2) is None
    assert log2.drops["drop_S1_close_filter"] == 1


def test_selection_ignores_quotes_after_s0_when_choosing_the_rung() -> None:
    a = snaps(0.55)
    b = snaps(0.40)
    p0 = psg([rung(2, a), rung(3, b)])
    # planted future: rung 3 swings to 0.50 after S0; the choice must not change
    b2 = [(t, 0.48, 0.52) if t > TIP - 140 * MIN else (t, lo, hi) for t, lo, hi in b]
    p1 = psg([rung(2, a), rung(3, b2)])
    s0, s1 = select_psg(p0, SelectionLog()), select_psg(p1, SelectionLog())
    assert s0 is not None and s1 is not None and s0.k == s1.k == 2 and s0.mid0 == s1.mid0


def test_exact_ten_cent_spread_is_accepted_in_cents_float_compat_may_reject() -> None:
    assert good_quote(0.45, 0.55)  # 55 - 45 = 10 cents
    assert not good_quote(0.45, 0.56)
    assert not good_quote(0.0, 0.05) and not good_quote(0.95, 1.0) and not good_quote(0.5, 0.5)
    assert not good_quote(float("nan"), 0.5)
    # the float expression 0.55 - 0.45 is 0.10000000000000003: the gate scripts' comparison rejects
    # it
    assert not good_quote(0.45, 0.55, float_compat=True)


def test_null_p_ge_full_and_missing_b_drop_the_market() -> None:
    log = SelectionLog()
    assert select_psg(psg([rung(2, snaps(0.5))], m=fc(None)), log) is None
    assert (
        log.drops["drop_null_p_ge_full"] == 1
        and log.drop_signs[("drop_null_p_ge_full", "T510")][None] == 1
    )
    p = psg([rung(2, snaps(0.5))])
    p.b = None
    log2 = SelectionLog()
    assert select_psg(p, log2) is None and log2.drops["drop_no_B"] == 1
    assert select_psg(psg([rung(2, snaps(0.5))], b=fc(None)), SelectionLog()) is None


def test_p_cond_is_count_over_n_and_zero_past_the_grid() -> None:
    f = Forecast((9, 5, 2), 10, 1.0)
    assert f.p_cond(1) == 0.9 and f.p_cond(3) == 0.2 and f.p_cond(4) == 0.0
    assert Forecast(None, 0, 1.0).p_cond(1) is None


def test_valuations_and_the_t510b_algebra() -> None:
    assert valuation("T510", 0.6, 0.8, 0.5) == pytest.approx(0.48)
    assert valuation("T510b", 0.6, 0.8, 0.5) == pytest.approx(0.48 + 0.2 * 0.5)
    for pc, pl, m in [(0.7, 0.9, 0.4), (0.2, 0.5, 0.55)]:
        # p_M' - mid0 = p_play * (p_cond - mid0): the sign no longer depends on p_play
        assert valuation("T510b", pc, pl, m) - m == pytest.approx(pl * (pc - m))
    with pytest.raises(ValueError):
        valuation("T511", 0.1, 0.1, 0.1)


def test_selection_does_not_depend_on_the_rule_valuation() -> None:
    # T510b "imports T510's selection": there is one select function and it never reads a valuation
    import inspect

    src = inspect.getsource(select_psg)
    assert "valuation(" in src  # only for the drop sign tables
    s = select_psg(psg([rung(2, snaps(0.5))]), SelectionLog())
    assert s is not None and s.pc_m == 0.8 and s.play_m == 1.0
    assert spec.S0_LEAD_MIN == 150 and spec.S1_GAP_MIN == 15 and spec.CLOSE_GAP_MIN == 30
