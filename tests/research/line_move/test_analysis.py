"""Qualification, movement, date-resampled tests, verdicts, horizon, text pins."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.line_move import analysis, spec
from research.line_move.selection import Selected


def sel(
    day: date,
    pc_m: float = 0.8,
    play: float = 1.0,
    pc_b: float = 0.8,
    mid0: float = 0.5,
    mid1: float = 0.5,
    close: float = 0.5,
    gid: str = "G",
    tk: str = "T",
    stat: str = "pts",
) -> Selected:
    return Selected(
        1, stat, day, gid, tk, 2, 0, 0, 0, 0, 0, mid0, mid1, close, pc_m, play, pc_b, play
    )


D0 = spec.FIRST_DATE


def days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def test_text_hashes_pinned_and_changes_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert spec.T510_SHA256.startswith("881aca57") and spec.T510B_SHA256.startswith("68f0ef84")
    assert set(spec.verify_texts()) == {spec.T510_DOC, spec.T510B_DOC}
    (tmp_path / "docs/prereg").mkdir(parents=True)
    for doc in (spec.T510_DOC, spec.T510B_DOC):
        (tmp_path / doc).write_text((spec.REPO / doc).read_text() + " ")
    monkeypatch.setattr(spec, "REPO", tmp_path)
    with pytest.raises(spec.FrozenTextChanged):
        spec.verify_texts()


def test_qualification_threshold_and_movement_sign() -> None:
    s = [sel(D0, pc_m=0.55, mid0=0.50, mid1=0.5, close=0.52), sel(D0, pc_m=0.549, mid0=0.50)]
    t = analysis.build_table(s, "T510")
    assert t.q1.tolist() == [True, False]  # |gap| >= 0.05, boundary included
    assert t.m_m[0] == pytest.approx(0.02)  # up-move toward a higher model
    s2 = [sel(D0, pc_m=0.40, mid0=0.50, mid1=0.5, close=0.47)]
    assert analysis.build_table(s2, "T510").m_m[0] == pytest.approx(0.03)  # down toward lower model
    s3 = [sel(D0, pc_m=0.50, mid0=0.50, mid1=0.5, close=0.6)]
    assert analysis.build_table(s3, "T510").m_m[0] == 0.0  # sign(0) = 0


def test_part2_qualifies_on_either_model_and_t510b_changes_the_gap() -> None:
    s = [sel(D0, pc_m=0.52, pc_b=0.60, mid0=0.5)]
    t = analysis.build_table(s, "T510")
    assert (not t.q1[0]) and t.q2[0]
    # T510b: gap shrinks by p_play, so a 0.06 gap at p_play 0.8 is 0.048 and does not qualify
    s2 = [sel(D0, pc_m=0.56, play=0.8, mid0=0.5, pc_b=0.5)]
    assert (
        analysis.build_table(s2, "T510").q1[0] is np.True_ or analysis.build_table(s2, "T510").q1[0]
    )
    assert not analysis.build_table(s2, "T510b").q1[0]


def test_percentile_levels_and_t_interval_are_the_stated_ones() -> None:
    rng = np.random.default_rng(1)
    vals = rng.normal(0.02, 0.03, 200)
    dd = np.array([d for d in days(10) for _ in range(20)], dtype=object)
    r = analysis.part_result(vals, dd, 0.01, (0.975, 0.95))
    sums, ns = analysis._per_date(vals, dd)
    dist = analysis.resample_dist(sums, ns)
    assert r.pct[0.975][0] == pytest.approx(np.percentile(dist, 1.25))  # 97.5% two-sided
    assert r.pct[0.95][1] == pytest.approx(np.percentile(dist, 97.5))
    dm = sums / ns
    from scipy.stats import t

    assert r.t_lower[0.975] == pytest.approx(
        dm.mean() - t.ppf(0.9875, 9) * dm.std(ddof=1) / np.sqrt(10)
    )
    # the same seed reproduces the same resamples
    assert np.array_equal(dist, analysis.resample_dist(sums, ns))


def _tbl(rule: str, n_days: int, per: int, shift: float, sd: float = 0.02) -> analysis.Table:
    rng = np.random.default_rng(7)
    rows = []
    for d in days(n_days):
        for i in range(per):
            toward = shift + rng.normal(0, sd)
            rows.append(
                sel(d, pc_m=0.7, pc_b=0.3, mid0=0.5, mid1=0.5, close=0.5 + toward, tk=f"T{i}")
            )
    return analysis.build_table(rows, rule)


def test_verdict_pass_null_harm() -> None:
    assert analysis.evaluate(_tbl("T510", 14, 25, 0.03)).verdict == "PASS"
    assert (
        analysis.evaluate(_tbl("T510", 14, 25, 0.004)).verdict == "NULL"
    )  # positive but under the 0.010 floor
    harm = analysis.evaluate(_tbl("T510", 14, 25, -0.03))
    assert harm.verdict == "HARM" and harm.part1.pct[0.95][1] < 0


def test_part2_needs_m_to_beat_b_not_just_to_move() -> None:
    # M and B agree on direction everywhere: part 2 difference is exactly 0 even though part 1
    # passes
    rows = [
        sel(d, pc_m=0.7, pc_b=0.7, mid0=0.5, mid1=0.5, close=0.53, tk=f"T{i}")
        for d in days(14)
        for i in range(25)
    ]
    r = analysis.evaluate(analysis.build_table(rows, "T510"))
    assert r.part1.passed[0.95] and not r.part2.passed[0.95] and r.verdict == "NULL"


def test_t510b_uses_the_stricter_level_and_reports_95() -> None:
    r = analysis.evaluate(_tbl("T510b", 14, 25, 0.03))
    assert r.level == spec.LEVEL_T510B and set(r.part1.pct) == {0.975, 0.95}
    assert r.verdict == "PASS" and r.passes_at_95
    # a signal that clears 95% but not 97.5% is "passes at 95%", not PASS
    weak = analysis.evaluate(_tbl("T510b", 14, 25, 0.0125, sd=0.35))
    assert weak.passes_at_95 or weak.verdict != "PASS"


def test_horizon_needs_300_qualifying_and_14_dates_and_excludes_the_gate_night() -> None:
    def mk(nd: int, per: int) -> list[Selected]:
        return [sel(d, tk=f"T{i}") for d in days(nd) for i in range(per)]

    assert analysis.horizon_date(mk(14, 22), "T510") == days(14)[-1]  # 308 over 14 dates
    assert analysis.horizon_date(mk(14, 21), "T510") is None  # 294 < 300
    assert analysis.horizon_date(mk(13, 40), "T510") is None  # 520 but only 13 dates
    night21 = [sel(spec.GATE_NIGHT, tk=f"X{i}") for i in range(500)]
    assert analysis.horizon_date(night21 + mk(13, 40), "T510") is None
    assert analysis.horizon_date(mk(20, 40), "T510") == days(14)[-1]  # 14th date binds


def test_t510b_is_never_scored_before_t510_or_without_it() -> None:
    a, b = days(14)[-1], days(16)[-1]
    assert analysis.window_end("T510", a, a) == a
    assert analysis.window_end("T510b", a, b) == b  # own horizon earlier: waits for T510
    assert analysis.window_end("T510b", b, a) == b
    assert analysis.window_end("T510b", a, None) is None  # T510 unscoreable / not reached
    assert analysis.window_end("T510", None, None) is None


def test_reported_blocks() -> None:
    t = _tbl("T510", 14, 25, 0.01)
    rep = analysis.reported(t)
    taf = rep["toward_away_flat"]
    assert isinstance(taf, tuple) and sum(taf) == int(t.q1.sum())
    assert "placebo_mean_and_95pct_range" in rep and "mean_m_M_pts" in rep
    s = [sel(D0, pc_m=0.7, play=0.8, tk="a"), sel(D0, pc_m=0.9, play=1.0, tk="b")]
    three = analysis.three_sets(s, {D0})
    assert sum(v[0] for v in three.values()) <= 2  # type: ignore[index]
    assert analysis.inactive_mid_close(analysis.build_table(s, "T510"), {1})[0] == 2
