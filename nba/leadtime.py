"""The production prediction lead: a forecast counts only if made at or before real tip-off
minus 60 minutes.

One name, one home (2026-11-17). ``LEAD_MINUTES`` is the int that SQL intervals and dataclass
defaults use; ``LEAD`` is the same lead as a ``timedelta``. The scoring rule itself is
``docs/FORWARD_PREREG_2026_27.md`` (rule 3); this module only names the number. The T-30 / T-15
shadow arms keep their own leads (``nba.lineups.store.T30_LEAD_MINUTES`` / ``T15_LEAD_MINUTES``).

Standard library only, so any layer can import it. ``tests/test_leadtime.py`` keeps every remaining
literal 60-minute lead equal to this value.
"""

from __future__ import annotations

from datetime import timedelta

#: The production T-60 lead, in minutes (an int: SQL ``? * INTERVAL 1 MINUTE``, config defaults).
LEAD_MINUTES: int = 60

#: The same lead as a ``timedelta``.
LEAD: timedelta = timedelta(minutes=LEAD_MINUTES)
