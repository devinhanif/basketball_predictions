"""Every script under reports/prereg_combo_stocks_shadow/ takes the heavy lock; none can skip it."""

from __future__ import annotations

from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "reports/prereg_combo_stocks_shadow"


def test_every_script_takes_the_lock_and_none_bypasses_it() -> None:
    scripts = sorted(DIR.glob("*.py"))
    assert len(scripts) >= 5
    for p in scripts:
        text = p.read_text()
        assert "WorkloadLock(" in text, f"{p.name} does not take data/ops/heavy.lock"
        assert "NO_LOCK" not in text and "nullcontext" not in text, f"{p.name} can bypass the lock"
