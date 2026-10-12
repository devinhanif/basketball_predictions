# Overnight loop, 2026-10-12 (started 01:53 CDT)

## Needs Devin (morning)
1. Mac sleep: run `sudo pmset -c sleep 0` and keep it plugged in (the 10-11 18:04-23:03 CDT collector hole matches a battery sleep).
2. the-odds-api plan before ~11-04: cancelling START 5M stops captures; START 100K ($59/mo) carries the full schedule.
3. Any 2025 holdout touch the loop finds a case for (none taken overnight without your words).
4. Open questions listed in docs/reviews/ledger_audit_2026-10-12_pass2.md (N14).

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
