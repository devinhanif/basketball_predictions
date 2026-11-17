# PARLAY_SPECTRAL_DIAGNOSTIC: does the leg-correlation graph's spectrum flag miscalibrated parlay joints beyond the copula's lift? (pre-registration)

Status: **DRAFT, NOT FROZEN, and it FAILS its own gates (below).** Written by the modeler, Mon 2026-11-16 23:31 CST (`date`),
branch `agent/modeler-1117-math-diagnostics`. No fit, no score, no outcome read. Reading-log note:
`docs/research/parlay_spectral_diagnostic_2026-11-17.md`. The modeler recommends NOT freezing it (DROP).

**Devin must confirm before any freeze (only if he overrides the DROP):**
1. The spectral score (Fiedler value of the |r|-weighted leg Laplacian, divided by k) and the baselines B1-B3.
2. Floor 0.02 calibration-error difference; flag rate 10% within (season, leg count).
3. Population: `joint_eval` synthetic same-game 2-4-leg parlays, 2023 select / 2024 report; no 2025 touch ever.
4. That the information precheck I0 is decisive: if it stops the rule, the question is closed, not re-tried with a new score.

How it would become the rule: `prereg(PARLAY_SPECTRAL_DIAGNOSTIC): <sha256>` of everything above `=== RESULTS BELOW ===`
(`awk '/^=== RESULTS BELOW ===$/{exit} {print}' <file> | shasum -a 256`).

## Question
Among same-game parlays priced by the Gaussian copula, does a spectral summary of the legs' correlation graph pick parlays whose
copula joint is miscalibrated more than parlays picked, at the same rate, by simple statistics the engine already has?

## Unit and population
One synthetic same-game parlay of 2 to 4 legs as built by `nba/parlay/joint_eval` (player legs conditional on playing, game legs
from the game latents), seasons 2023 (select) and 2024 (report), `season <= 2024` in SQL; cross-fit parameters as in
`docs/PARLAY_ENGINE.md:46-48`. Documented size: 15,798 parlays over 2,633 games.

## Scores
- **S:** W_ij = |r_ij| from the copula's pooled, shrunk, Higham-repaired correlation for legs i, j; L = D - W; S = lambda_2(L) / k.
- **B1:** copula lift = p_copula / product of marginals, ranked by |log lift|.
- **B2:** max over pairs |r_ij|.
- **B3:** relation pattern: the isomorphism class of the labelled leg graph (labels = (relation, stat pair)); ranked by the
  B1 value of the class mean (so it is a ranking, fixed before outcomes).
- Flag: top 10% within (season, k), ties by parlay id.

## Primary metric and pass rule
- CE(flag) = |mean(y) - mean(p_copula)| over flagged parlays. D_b = CE(S) - CE(B_b), b = 1, 2, 3.
- CI: paired percentile bootstrap over games, B = 2,000, seed 0, 95%. Multiplicity: 3 sizes x 3 baselines = 9, BH q = 0.05.
- Pass: in BOTH seasons, for some k, every D_b >= 0.02 with CI lower bound > 0, BH-significant.

## Prechecks (label-free, before any outcome)
- **I0 information:** regress S on dummies of the B3 pattern cell, per (season, k). If R^2 >= 0.99, STOP: S is a function of the
  relation pattern and cannot carry information B3 lacks.
- I1 support: S must take >= 10 distinct values per (season, k), else STOP.

## Kill criteria and stopping
Any stop or fail closes the question; no other spectral statistic (lambda_max, normalised Laplacian, eigenvector split) is
tried afterwards. No 2025 touch is ever earned.

## What each choice protects against
Same flag rate: a score that flags more parlays "finding" more error. Intersection over baselines: beating only the weakest.
I0: an expensive renaming of the copula's own input. Leg count strata: k drives both the score and the compounding error
(T490/T492). Game clustering: parlays from one game share every leg outcome.

## Gate check (non-negotiable 8)
- **I0 (decided by design, not by data):** the engine's correlations are pooled by (relation, stat pair) with fixed shrinkage
  k = 200 and are not per player (`docs/PARLAY_ENGINE.md:12-14, 85`), so W, L and S are deterministic functions of the B3
  cell: R^2 = 1 by construction. For k = 2, S = |r| = B2 exactly (up to the constant 2/k). **FAILS.** The script would only
  confirm the identity.
- **I1:** with 35 pooled pairs (`docs/PARLAY_ENGINE.md:14`), the number of distinct S values per k is bounded by the number of
  labelled patterns; for k = 2 it is at most 35. Not measured.
- **Data, historical:** 15,798 parlays / 2,633 games would give about 1,580 flagged parlays: enough by size.
- **Data, forward (frozen export 2026-11-16, data_version 764432af8422, counts only):** 1,355 parlay records, all
  `engine = marginal`, all 1 leg, 3 games (403 / 477 / 475 legs); 0 multi-leg records; no independent or copula joint exported.
  Forward units: **0.** The rule cannot be run forward.

## Prior (to PREDICTIONS_LEDGER before any result)
P(I0 stops the rule) 0.97; P(pass) 0.01; expected D vs B1 0.000, 80% interval [-0.005, +0.005].

=== RESULTS BELOW ===
