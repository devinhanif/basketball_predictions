"""Player prop stat distributions (CLAUDE.md "Phase 2 -- Props").

Minutes model first (hurdle: DNP branch + minutes|plays), then per-stat
frequency-severity distributions for points/rebounds/assists/3PM, scored
with CRPS, threshold log loss, calibration, mean-bias CIs, and 80%
interval coverage against season-average / last-10-average baselines.

Combos (PRA etc.), hierarchical team-total coherence, role-change
detection, and conformal intervals are a separate, later milestone.
"""

from __future__ import annotations
