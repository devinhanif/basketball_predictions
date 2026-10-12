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
    # one ROSTER variable feeds all three steps; the date switch lives only in ops/roster_source.sh
    assert len(re.findall(r"\bROSTER=", SCRIPT)) == 0
    assert "/roster_source.sh" in SCRIPT
    for name in ("predict", "parlay_shadow", "props_analysis"):
        assert '--roster-source "$ROSTER"' in _step(name), name


_INCLUDE = Path(__file__).resolve().parents[2] / "ops" / "roster_source.sh"


def _roster_on(day: str) -> str:
    import subprocess

    out = subprocess.run(
        ["sh", "-c", f'TODAY_ET={day}; . "{_INCLUDE}"; echo $ROSTER'],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_official_roster_window_runs_through_2026_11_30_inclusive() -> None:
    assert _roster_on("2026-10-12") == "official"
    assert _roster_on("2026-11-04") == "official"
    assert _roster_on("2026-11-30") == "official"
    assert _roster_on("2026-12-01") == "recent"


def test_both_entrypoints_use_the_single_switch() -> None:
    lineups = (_INCLUDE.parent / "nba_lineups.sh").read_text()
    assert "/roster_source.sh" in lineups
    for text in (SCRIPT, lineups):
        assert "2026-11-04" not in text and "2026-11-03" not in text
