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
| 2026-10-12 | ERROR_FEEDBACK production proposal | about 0.30 (P(report pass) 0.98 / 0.41 by persistence) | ERROR_FEEDBACK.md draft | Stage 1 FAIL (T481: slice ef_n<3 +0.0107; primary -0.0056 passed) | no (0.30 stated) |
| 2026-10-12 | SEQ_FULL_MIX_RT 2025 primary clears | 0.60 before run 1; restated after run 1 and the gain attack: 0.80 (full pass 0.62), AVG-FULL about -0.010, range -0.006 to -0.014 | SEQ_FULL_MIX_RT.md draft | PASS, AVG-FULL -0.0133 (inside the stated range, slightly better than the -0.010 centre) | yes |
| 2026-10-12 | LINE_CALIBRATION reaches coin-flip log loss or better | Claude: likely; beats Pinnacle: unlikely | session | pending | |
| 2026-10-12 | SEQ_LEARNING_CURVE: still rising past W_ALL (4 to 8 seasons) | modeler: P(RISING) 0.12; P(A <= -0.003, CI upper < 0) 0.38; A about -0.0025 (80% -0.007 to +0.002); B about -0.0006; P(HURTS) 0.10; P(1 to 4 falls >= 0.008) 0.90; P(reproduction gates pass) 0.85 | SEQ_LEARNING_CURVE.md draft | RISING (T485: A -0.0062, B -0.0029) | **no** (P(RISING) 0.12; expected A -0.0025) |
| 2026-10-13 | SEQ_LEARNING_CURVE_2: data vs steps and the curve at 9-10 seasons | modeler: A_m (step-matched 8 vs 4) DATA 0.50 / MIXED 0.35 / STEPS 0.15, expected A_m -0.0040 (80% -0.0075 to -0.0005) vs unmatched -0.0062; E (10 vs 8, matched) expected -0.0010 (80% -0.0040 to +0.0015); RISING_10 0.15, FLAT 0.50, AMBIGUOUS 0.27, HURTS 0.08; D1-D3 pass 0.85, D4 by fetch 0.70; UP(4)-U(4) <= -0.002 0.40 | SEQ_LEARNING_CURVE_2.md draft | pending | |
| 2026-10-13 | AVG_SHADOW_LIVE final look (pts AVG minus FULL, live 2026-27) | modeler: expected -0.0090 (80% -0.0150 to -0.0030); P(PASS) 0.45, P(FAIL) 0.15, P(INCONCLUSIVE incl. integrity/coverage/stale) 0.35, P(killed early) 0.05; P(P1-P4 proofs pass before opening night) not stated | AVG_SHADOW_LIVE.md draft | pending (final look about Sun 2027-01-10) | |
| 2026-10-14 | AVG_SHADOW_LIVE_2: G1 repair on the 199-point pmf | modeler: P(G1 passes at the final look, shadow otherwise readable) 0.85; P(rule frozen with no further change) 0.85; P(FULL has the same 19-atom failure) 0.05 stated before the check (measured: no, FULL has no calibration guard) | docs/prereg/AVG_SHADOW_LIVE_2.md (draft) | pending |
| 2026-10-14 | ALT_LINE_70 (Devin: over at P >= 0.70 on all alt lines, $20 a week) | modeler: Part 1 P(all 8 stat x season headline cells CALIBRATED, i.e. 'calibrated at 70%+') 0.20; P(>= 1 stat OVERCONFIDENT in both seasons) 0.55; expected pts headline-band gap -0.015 (80% -0.05 to +0.015). Part 2 (BetRivers t60, pooled 2023-24, ROI floor +0.02): P(PROFITABLE) 0.03, P(NOT PROFITABLE) 0.60, P(INCONCLUSIVE) 0.35, P(gate or integrity stop) 0.02; expected pooled ROI -0.06 (80% -0.15 to +0.03); the pooled conservative ROI half-width 0.041 (independent 0.0185) is a gate fact, not a prior | docs/prereg/ALT_LINE_70.md (draft, branch agent/modeler-alt-line-70) | pending (no outcome computed; scored when the frozen harness runs) | |
