# Predictions ledger

Every prior stated before a result (by Claude or an agent), scored when the result lands. Purpose: measure our own
calibration (Devin, 2026-10-12). A prior is recorded with where it was written; the outcome column is filled only from
the frozen result. Brier over resolved rows is reported in each session retro.

| Date | Rule / question | Prior stated before the result | Where written | Outcome | Hit? |
|---|---|---|---|---|---|
| 2026-10-12 | ROAD_STARS pass | P(pass) about 0.02 (expected null) | DECISIONS 12:28 | NULL (T475) | yes |
| 2026-10-12 | Six drafts (RT, EA, RC, QS, B2B, EF) pass their gate on 2023-24 | not stated as a number; Claude expected most to fail | session | all six failed the gate | yes (qualitative) |
| 2026-10-12 | Q7.3 lower-only widening freezable | power about 0.01 at -0.002 | T474 / DECISIONS | not frozen | n/a |
| 2026-10-12 | SEQ_FULL_MIX_RT run 1: W_ALL makes "a small change" vs W22 | small change (draft "Expectation") | SEQ_FULL_MIX_RT.md draft | history step -0.0120 / -0.0072 (large) | **no** |
| 2026-10-12 | FULL_SHADOW_LIVE pass at the final look | 0.78 at the 2023/24 effect, 0.97 at the 2025 effect | prereg 13831b52 | pending (about Feb 2027) | |
| 2026-10-12 | ERROR_FEEDBACK production proposal | about 0.30 (P(report pass) 0.98 / 0.41 by persistence) | ERROR_FEEDBACK.md draft | pending | |
| 2026-10-12 | SEQ_FULL_MIX_RT 2025 primary clears | 0.60 before run 1; restated after run 1 and the gain attack: 0.80 (full pass 0.62), AVG-FULL about -0.010, range -0.006 to -0.014 | SEQ_FULL_MIX_RT.md draft | PASS, AVG-FULL -0.0133 (inside the stated range, slightly better than the -0.010 centre) | yes |
| 2026-10-12 | LINE_CALIBRATION reaches coin-flip log loss or better | Claude: likely; beats Pinnacle: unlikely | session | pending | |
