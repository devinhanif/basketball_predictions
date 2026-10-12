# Overnight loop, 2026-10-12 (started 01:53 CDT)

## Needs Devin (morning)
1. Mac sleep: the 10-11 collector hole was the LID closing on battery at 18:33 CDT (clamshell sleep); caffeinate does not stop that. On game nights keep it on AC with the lid open (or an external display), and run `sudo pmset -c sleep 0`; `sudo pmset -a disablesleep 1` also stops lid sleep but drains battery.
2. the-odds-api plan: live capture now asks for 89 markets x 12 points (~2,136 credits/game, ~459k in a peak month, ~2.63M a season). 2.30M remain after tonight's 1.14M back-fill. When does the billing period reset? Cancelling START 5M stops all captures; with 89 markets the smallest fitting plan is no longer START 100K — tell me which plan and I'll size the market groups to it (groups can be switched off in configs/odds_live.yaml). Also: check the first live raw file on 10-20 (new market shapes parsed from vendor docs).
3. Any 2025 holdout touch the loop finds a case for (none taken overnight without your words).
4. SEQ network blend on regular-season rows: real -0.011/-0.012 points gain, guard-clean without playoffs (T466). Test it as a new rule on the 2025 holdout (SEQ untouched) or the live log? Your call.
5. Open questions listed in docs/reviews/ledger_audit_2026-10-12_pass2.md (N14).

## In flight at start
- Adversary attack on RECENT_ROLE_HOLDOUT (the one holdout pass; Devin worried).
- Steward live batch: P(play) shadow on, T-15 arm, rivalry flags, real-tip default, run metadata, collector hole, parlay roster source.
- ROUTER_G2 draft (last modelling item from the audit).
- Coverage tests (68.5% -> >= 70%).

## Queue added by Devin ("we missed something and many tests were close")
- Q1 Diagnostic (descriptive, no claim): why a better pi moves pts CRPS only ~-0.005 when the oracle pi is worth -0.15 — check pi log-loss gain vs CRPS transfer, the short-component quantiles w_q, the mixture weight path; look for a structural cap.
- Q2 RECENT_ROLE_FULL: one rule putting recent-role minutes into P(play), pi and the props heads together; select 2023, report 2024; floor unchanged (-0.005); a 2025 touch only on Devin's words.
- Q3 Pace diagnostic (descriptive, no claim): do production props residuals (2023/24 OOF) correlate with as-of expected game pace (both teams' recent possessions per 48) beyond what r20 points-for/against carries? Slice by pace decile.
- Q4 PACE_TOTALS draft: a game-total model = expected possessions (both teams' as-of pace) x each team's as-of efficiency vs the opponent, scored against the market total (log loss / CRPS on total points, decision score); select 2023, report 2024; Devin confirms before freeze ("no way fitting the games to the pace model doesn't work").
- Floor: not changed; any floor question is a daylight decision.

## Log
- 01:53 loop started; limits: no 2025 holdout, no production promotion, no purchases, <= 2 new agents at a time.
- 02:03 RECENT_ROLE_HOLDOUT attacked by the adversary: SURVIVES (T462). Attribution corrected (T463): the gain is recent minutes, 96% from the 5 recent-role columns. Against the deployed P(play): -0.097. Shadow will also log C and C' (steward item 8). Diagnostic 2025 arms disclosed in the holdout log.
- 02:22 Coverage restored to 71.99% with real tests for 7 research modules (no omits beyond the Colab jobs, threshold unchanged). Fragility noted, not fixed: xgb_pts_vs_lt_eval.write_ledger KeyError when a minutes slice has < 300 rows (cannot trigger on real data).
- 02:23 ROUTER_G2: no pass (T464-T466). The gate adds nothing; the SEQ network blend improves points by -0.011/-0.012 and passes the guards once playoffs go to production. NEEDS DEVIN: whether to test 'SEQ blend on regular-season rows' as a new rule (2025 holdout or live).
- 02:37 Live batch committed (6036c97, 4ea569a, 5b806d5): shadow on, T-15 arm, rivalry flags, real-tip default, run metadata, parlay roster source, C/C' comparators + parity check; oracle IDENTICAL.
- 02:53 Feature significance + half points (81de744): injury/vacated is the top input for every prop (pts +0.0245 when removed); recent-role columns ADDED to the props heads cut pts CRPS by -0.0086 [-0.0111, -0.0062] pooled 2023-24 (descriptive; supports the queued combined rule). Halves: direct model beats naive by -0.043 (1H), -0.052 (2H), -0.130 (Q1); 2H ~11% harder, worse in expected blowouts. No 1H/2H player market exists; Q1 pending the back-fill. Started Q1 transfer diagnostic.
- 02:56 Odds: live capture widened to 89 markets (19ff991); 2023-24 period lines back-filled for every game (106f0f7, 1.14M credits): quarter/half moneyline, spread, total from 10-16 books; Q1 player props only in 2024 (1-2 books).
- 02:58 Q1 transfer diagnostic (e019376, 286c7b9): no structural cap — CRPS gain tracks how close pi gets to perfect; the recent-role pi closed 2.4% of the gap (AUC 0.855 -> 0.864); oracle-like gains need AUC > 0.95, i.e. lineup-time information. Short component, clip and mixing formula worth <= 0.003 combined. Candidate (not run): refit pi on T-15/T-30 information. Started Q2 RECENT_ROLE_FULL draft.
- 02:58 Full test suite after the live batch: 2,723 passed, 1 skipped, coverage 72.57%.
