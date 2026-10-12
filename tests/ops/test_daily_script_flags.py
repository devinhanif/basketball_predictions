"""ops/nba_daily.sh pretip: shadow flag on, and one roster source shared by predict and parlay."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "ops" / "nba_daily.sh").read_text()


def _step(name: str) -> str:
    """The (continued) shell command of ``step <name> ...`` in the script."""
    m = re.search(rf"^\s*step {name} (.*?)(?<!\\)\n", SCRIPT, re.M | re.S)
    assert m, name
    return m.group(1).replace("\\\n", " ")


def test_pplay_shadow_flag_in_predict_step() -> None:
    assert "--log-pplay-shadow" in _step("predict")


def test_parlay_steps_follow_the_predict_roster_switch() -> None:
    # one ROSTER variable (official until 2026-11-04, then recent) feeds all three steps
    assert len(re.findall(r"\bROSTER=", SCRIPT)) == 2  # default + the one date switch
    for name in ("predict", "parlay_shadow", "props_analysis"):
        assert '--roster-source "$ROSTER"' in _step(name), name
