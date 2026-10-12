# ADR 0002 — Possession reconciliation: points are the hard gate, the formula is a diagnostic

**Status:** accepted (2026-10-11; Devin, "fix everything", safe default of audit question 3). Supersedes the gate in ADR 0001.

## Context
ADR 0001 kept `FGA + 0.44*FTA + TOV - OREB` as a secondary check with a loosened mean-abs tolerance. The 2026-10-11 data audit (F12)
measured it on all 10,558 team-games: mean (poss - formula) -1.6 to -2.0, MAE 2.0-2.25 in every season, so even the loosened
original gate of 1 was never met, while possession points equal box points in 100% of team-games after the F6/F7 parser fixes.
The formula cannot see team turnovers and uses an approximate 0.44; it is not an identity.

## Decision
1. Hard gate: possession points == box points per team-game; `reconcile_points` / `points_match_rate`, `POINTS_MATCH_MIN = 0.99`
   (real data 1.000).
2. The formula (`reconcile_possessions`, `mean_abs_error`) is reported as a diagnostic, flagged above `FORMULA_MAE_DIAGNOSTIC = 2.5`,
   and never fails a build on its own.

## Would reverse
A source of official team box totals (team turnovers, team rebounds) that makes the formula an identity; then re-tighten it.
