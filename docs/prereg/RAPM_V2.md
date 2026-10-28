# RAPM_V2: as-of RAPM player value inside injury-Elo, on rebuilt stints with a 2019-21 history prior (pre-registration DRAFT)

Status: **DRAFT 2026-10-27, NOT frozen, NOT freezable while its gate fails (see "Gate result").** Everything above
`=== RESULTS BELOW ===` is the rule. Freeze evidence would be the commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RAPM_V2.md | shasum -a 256`. No RAPM has been fitted for
this id, no log loss computed, season 2025 never loaded, every connection `read_only=True`.

- **Advice or momentum:** Devin's request (DECISIONS 2026-10-27 18:32, "redo all tests that didn't work before", wave 1).
  Claude's earlier advice on the same question (docs/prereg/RAPM_RETEST.md, 2026-10-14, option O1) was to close T176
  as "untestable at this n"; flagged here because this draft re-asks it.
- **Model class:** RAPM / lineup-based ratings (ridge on 5-on-5 possessions) feeding a ratings host (MOV-Elo plus the
  2-coefficient ridge injury term, `rung0_injury_elo` v2). Playbook sections "RAPM and lineup-based ratings",
  "Ratings", "Linear and logistic models".
- **Re-tests:** T176 (INVALID, validity audit F7: RAPM built on stints that matched box minutes on 47-49% of
  player-games; F6: CI spans 0, MDE ~0.0035). Redo rank 7 in docs/reviews/validity_audit_2026-10-12.md section 3.
- **Lineage:** docs/RAPM.md (frozen 2026-10-08), T176; RAPM_RETEST gate record (2026-10-14, power gate failed);
  T518 REPLACEMENT_VALUE_ELO (same host, gate failed on power 0.21 per season at -0.002).

## Hypothesis
H1: replacing the box-composite value of players OUT on the pre-tip official report with an as-of RAPM value, built
on the current (v2) lineups with 2019-21 possessions as a prior, lowers injury-Elo win log loss on 2022-24.
H0: no difference larger than the floor. Mechanism: RAPM credits defence and off-ball value the box value misses, so
an absent non-scorer would move the line more. Prior evidence against: T176 variant (c) (both values together) was
-0.00004 [-0.00038, +0.00028], i.e. the old RAPM carried no information the box value lacked; that run is invalid
(wrong lineups attenuate toward zero), so it bounds nothing now.

## What changes from T176 (and only this)
1. Possessions and stints at the current data_version (v2 tracker, possessions in game-clock order, restored short
   appearances). Stints reconcile with box minutes on 99.97-99.98% of player-games 2022-24, 98.7-99.4% 2019-21.
2. History: 2019, 2020 and 2021 possessions from `data/history/nba_history.duckdb` enter the Gram matrix with the
   original `history_decay = 0.5 ** (season gap)` (pre-set, not tuned). Protects against: RAPM's data hunger (playbook:
   "needs multiple seasons or a prior"); the 2022 snapshots in T176 had at most a few months of possessions.
3. Lambda chosen by walk-forward possession-MSE CV on season 2021 (history), NOT 2022. Protects against: T176 tuned
   lambda on 2022 possessions, whose points sum to the 2022 margins it then scored.
4. Both arms from ONE flagged-set object with real tips (`tip_source="real"`, `attach_real_tips`). Protects against the
   harness defect found 2026-10-14 (RAPM arm on the 19:00 proxy vs box arm on real tips: an unfair pair).
5. Arm (a) is rebuilt in the harness, never read from `data/injury_elo_v2` (T518 G4b: today's data no longer
   reproduces the stored OOF on 29% of games, max 0.016 logit).

## Fixed design (constants final)
- Possessions: lineup-complete (5 + 5 non-null) possessions, seasons 2019-2024 only (`season <= 2024` in SQL on both
  DBs). Row: +1 per offensive player, -1 per defensive player, unpenalized intercept and home-offence column;
  target points x 100; ridge toward 0; `rapm = 2 * beta`. `prior_carry = 0` (unchanged).
- Snapshot for month M uses possessions with game_date strictly before the first day of M. Lambda grid as in
  `configs/injury_elo.yaml` / the T176 config; one lambda chosen on 2021, used for every snapshot.
- Plug-in (unchanged): `value = max(rapm, 0) * as_of_minutes / 48`, OUT weight 1, DOUBTFUL 0.5, latest official
  report at least 60 min before the REAL tip, summed per side, away minus home, / VALUE_SCALE 10. MOV-Elo parameters
  frozen (production). Two injury coefficients refit monthly walk-forward exactly as production.
- Arms: (a) production box value; (b) RAPM value (the candidate); (c) both, 4 features (descriptive only; cannot be
  substituted for b).
- Scored rows: all 2022-24 regular-season games with a usable report, one game = one row (expected 3,951).
- One target only: win log loss. RAPM as a prop context feature was considered and NOT included: it needs a separate
  tree refit on 4 stats x 2 seasons and integer CRPS scoring (hours, not minutes), and the prop context lineage
  (opponent ridge leak, F11 / T509 nulls) puts its prior below this one.

## Metric, inference, multiplicity
- Primary: pooled 2022-24 paired log-loss delta (b - a), mean over games; game-clustered paired bootstrap
  (B 2,000, seed 0); one game is one row. Date-clustered bootstrap reported alongside; the WIDER CI binds.
- Family: one primary test (b vs a), no BH needed (family size 1); Brier delta and ECE reported, not gating.
- Select / report: no selection step exists (one arm, constants fixed before any fit), so pooled 2022-24 is the single
  evaluation, as in T176 and audit rank 7. Per-season deltas are reported as descriptive slices.
  Protects against: splitting an already underpowered single test into two weaker ones; the select/report split
  guards against choosing among variants, and there are none to choose.
- Slices (descriptive, n with each): rotation OUT 0 / 1 / 2+; season 2022 / 2023 / 2024; the OUT player's box value
  bottom / top half (where RAPM and box disagree most).

## Decision rule
KEEP iff all of: (1) point delta <= -0.002 (the practical floor; playbook ratings check "plausibly larger than about
0.002"; T160 / T518 precedent); (2) binding 95% CI upper < 0; (3) no rotation-OUT slice with n >= 100 worse than (a)
by more than 0.005 (T176 slice veto, carried); (4) Brier point delta <= 0.
REJECT iff CI lower > 0 or a slice veto fires. Otherwise INCONCLUSIVE, recorded as "not worth >= the CI's lower bound".
KEEP earns one logged 2025 touch (archivist row first), nothing in production.
Kill: REJECT or INCONCLUSIVE closes RAPM-in-injury-Elo; no other lambda, decay, prior or plug-in afterwards.

## Minimum-data gate (label-free; must pass before freeze; non-negotiable 8)
Written before any gate number was computed. All checks on data_version `49a2fc3d3dff` (manifest 2026-10-27 12:09 UTC)
plus the history DB, `season <= 2024`.
- G-D1 scored games with a usable report at tip-60 (real tips), 2022-24 pooled >= 3,900.
- G-D2 lineup-complete possessions >= 99% of possessions in every season 2019-2024.
- G-D3 stints vs box minutes within 1.0 min on >= 98% of player-games in every season 2019-2024.
- G-D4 real tip coverage 100% of scored games.
- G-P power: if the candidate is exactly true and its expected gain equals the floor (-0.002), the per-game gain has
  variance about 2|G| (T518 formula; the MOST optimistic SD, since any noise in b inflates it), so
  P(CI upper < 0) = Phi(sqrt(n |G| / 2) - 1.96). Must be >= 0.80 at the scored n. Also reported with the only measured
  SD(d) (T176, pre-v2: 0.1110).
- G-C ceiling (oracle movement bound): K = mean KL(injury-Elo || MOV-Elo) per game from the stored 2022-24 OOF
  probabilities (no labels). An equal-variance swap of the player value with correlation rho to the box shift moves
  the logit by about sqrt(2 (1 - rho)) times the injury shift, so a perfectly true RAPM arm gains at most about
  2 (1 - rho) K. At rho = 0.69 (T176's measured RAPM-box value correlation, the only one held) the ceiling must be
  >= 0.002 AND the power to detect a gain equal to the ceiling must be >= 0.80. Protects against: freezing a test
  whose floor a perfect candidate cannot reach (T518 G6).
Any gate failure = not frozen; recorded, not chased.

## Priors (stated before the gate script ran; drafted PREDICTIONS_LEDGER row in the report)
- P(G-D1..D4 all pass) 0.95 (counts are already known from RAPM_RETEST / T518 on earlier data_versions).
- P(G-P passes at -0.002, pooled 2022-24) 0.02 (the formula gives about 0.51 at n 3,951; only a count far above
  3,951 could change it).
- K pooled 2022-24: expected 0.008 (80% interval 0.005-0.013). P(G-C ceiling at rho 0.69 >= 0.002) 0.55;
  P(power at the ceiling >= 0.80) 0.15.
- If Devin overrides and the rule runs anyway: P(KEEP) 0.04; expected delta +0.0005 (80% interval -0.002 to +0.003);
  P(INCONCLUSIVE) 0.65; P(REJECT) 0.31.

## Gate result (label-free; run 2026-10-27 18:40 CDT; STOPPED AT THE GATE on G-P)
Script `nba/eval/rapm_v2_gate.py` (this branch), output `reports/prereg_rapm_v2/gate.json`, sha256 `8ccdb09f467af3d6`,
identical bytes on repeat runs; 0.8 s wall, 0.5 GB peak. Data: `nba.duckdb` data_version `49a2fc3d3dff` (manifest
2026-10-27 12:09 UTC) and `data/history/nba_history.duckdb`; `season <= 2024`; schedule files of later seasons not
linked; read-only. No RAPM fitted; the only probabilities read are the stored MOV-Elo / injury-Elo OOF (no labels).

| check | value | pass |
|---|---|---|
| G-D1 scored games 2022-24 (stored OOF rows; with report 1,318 / 1,317 / 1,317) | 3,951 | yes |
| G-D2 lineup-complete possessions, min over 2019-24 (263,457 / 260,449 / 262,152 in 2022-24; 230,560 / 233,045 / 260,845 in 2019-21) | 1.000 | yes |
| G-D3 stints vs box within 1 min, min over 2019-24 (2022-24 0.9998 / 0.9997 / 0.9998; 2019-21 0.9892 / 0.9866 / 0.9938) | 0.9866 | yes |
| G-D4 real tips (prod 3,958 / 3,958; history 3,637 / 3,637) | 100% | yes |
| **G-P power at the -0.002 floor, pooled 2022-24, candidate exactly true (most optimistic SD)** | **0.511** | **no** |
| G-C1 swap ceiling 2 (1 - rho) K at rho 0.69 (K = 0.00981, n 3,951) | 0.00608 | yes |
| G-C2 power at that ceiling, pooled 2022-24 | 0.934 | yes |

Power by design (P(CI upper < 0); "self-consistent" = candidate exactly true, SE = sqrt(2|G|/n); "T176 SD" = 0.1110):

| design | n | at -0.002 self-consistent | at -0.002 T176 SD | at -0.004 self-consistent | at -0.004 T176 SD |
|---|---|---|---|---|---|
| select 2023 alone (wave-plan design) | 1,318 | 0.21 | 0.10 | 0.37 | 0.26 |
| report 2024 alone | 1,315 | 0.21 | 0.10 | 0.37 | 0.26 |
| pooled 2023 + 2024 | 2,633 | 0.37 | 0.15 | 0.63 | 0.46 |
| **pooled 2022-24 (this rule)** | **3,951** | **0.51** | 0.20 | 0.80 | 0.62 |
| pooled 2019-24, history scored (not this rule: history is the prior) | 7,585 | 0.79 | 0.35 | 0.97 | 0.88 |

Movement ceiling by season (K = mean KL(injury-Elo || MOV-Elo); RMS logit shift of the injury term): 2022 K 0.00969
(RMS 0.306), 2023 0.01042 (0.333), 2024 0.00931 (0.320). Swap ceiling pooled at rho 0.5 / 0.69 / 0.8 / 0.9:
0.0098 / 0.0061 / 0.0039 / 0.0020. Reading: the ceiling says how far a value swap CAN move forecasts, not how much
information it carries (T518's placebo had a larger bound than its candidate). The rebuilt RAPM should correlate MORE
with the box value than the broken one did (wrong lineups decorrelate), so rho 0.69 is a generous assumption; at
rho 0.8 a perfect RAPM sits at -0.0039, at rho 0.9 it sits at the floor.

**Verdict: the gate FAILS on G-P. RAPM_V2 has the same power problem as RAPM_RETEST and T518.** At the plausible
floor (-0.002) the best available design detects a true effect half the time even if RAPM were the exact truth, and
one in five times with the noise T176 actually showed. The wave-plan design (select 2023 / report 2024) is worse
(0.21 per season). Not frozen.

Data-capture audit (non-negotiable 8; a gate fails on data only after it): every season held is counted above.
2013-2018 history holds possessions (100% lineup-complete) but no official injury reports, so it can feed the RAPM
prior, not the scored set; the 2018-19 report repair (RAPM_RETEST: about 30 min of fetch plus parser work, start of
the archive unverified) adds at most about 750 scored games. Scoring 2019-24 (n 7,585) reaches 0.79 only by using the
prior seasons as scored seasons (their RAPM snapshots would have no prior of their own) and still misses 0.80; with
2025 (one touch) it would reach about 0.85, but that spends the holdout on a selection test. No source adds NBA games.
Repairable gaps that change the verdict: none. **The gate fails on power after the audit.**

## Options for Devin (none drafted as a rule)
- **A (recommended): close T176 as "untestable at the plausible effect size", not as a null.** Ledger wording: "RAPM
  in injury-Elo: old negative INVALID and not citable; on valid lineups a test at n 3,951 cannot see 0.002 (power
  0.51 at best). No live arm." Cost zero.
- **B: freeze a bounded-null variant RAPM_V2B at floor -0.004** (pooled 2022-24, everything else as above). Power
  0.80 self-consistent, 0.62 with the T176 SD. It can only answer "is RAPM worth at least half the whole injury
  term", which the movement ceiling says a perfect RAPM reaches only if rho <= 0.8. A null would exclude effects
  >= about 0.004, nothing smaller. Modeler advises against: the KEEP outcome needs an implausible effect, and the
  INCONCLUSIVE outcome repeats the RAPM_RETEST conclusion at an hour's cost.
- **C: score 2019-24 with 2013-18 as the prior** (n 7,585, power 0.79 at -0.002 if exactly true, 0.35 with T176 SD).
  A different design (needs era flags for the 2020 bubble and 2020-21) and a new id. Not recommended.

## What would reverse this
A design with materially more scored games (none exists before 2025), or evidence that the rebuilt RAPM's per-game
noise is well below the self-consistent bound (impossible by construction: the self-consistent SD is the minimum).

## Compute estimate (if B or C were chosen)
Mac CPU, single process. T176 took about 6 s wall for 786,814 possessions and 26 monthly refits at 0.03 s; 2019-24 is
about 1.5 M possessions (2x), lambda CV on 2021 adds about 12 refits: about 1-2 min of fitting plus feature build,
under 10 min end to end, well under 2 GB. Run under `mkdir data/ops/heavy.lock` (retry 120 s, `rmdir` in finally),
and NOT tonight after 21:05 CDT (replay oracles hold the lock). The harness (`research/eval/rapm_injury_elo_eval.py`)
needs the real-tip fix and the one-flagged-set refactor first (about an hour of code plus tests on the fixture DB).

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: RAPM / lineup-based ratings feeding a ratings host (injury-Elo); the injury coefficients are a linear model.
1. **Information or capacity?** Information quality: same pre-tip report, a different valuation of the absent player.
   Correct lineups are new relative to T176; the report information is not new. The movement ceiling bounds the
   possible gain at 0.002-0.006 depending on how different RAPM is from the box value.
2. **Enough data for the class?** RAPM: yes, now (1.5 M lineup-complete possessions 2019-24, history prior,
   reconciliation >= 98.7% every season). The shortage is on the evaluation side: about 1,300 games a season.
3. **Test valid?** T176 invalid (F7 lineups, lambda tuned on a scored season, proxy-vs-real tip mismatch in the
   harness). This draft fixes all three; it is invalid as a decider only because of power (F6). An unpowered test is
   not a null, so the honest verdict is "untestable", not "RAPM lost".
4. **Lineage and inherited guards:** docs/RAPM.md variants a/b/c, slice veto 0.005, max(rapm, 0) plug-in, history
   decay 0.5 carried. Added: real tips in both arms from one flagged set; arm (a) rebuilt, not read from the stored
   OOF (T518 G4b); lambda tuned on history. Planted-future test on the snapshot (`tests/features/test_rapm.py`) carried.
5. **Scored against:** production injury-Elo box arm (log loss, Brier); plain MOV-Elo as context. Not the market:
   the market beats both on game winners (2025-26 replay, Brier 0.0064), and nothing here changes that.

## Prior scoring (gate priors, stated above before the script ran)
G-D all pass: P 0.95, happened. G-P pass: P 0.02, failed (hit). K: expected 0.008 (80% 0.005-0.013), measured 0.0098
(inside). G-C1: P 0.55, passed. G-C2: P 0.15, passed (miss: the ceiling at rho 0.69 is larger than I expected because
the injury term moves the logit by 0.32 RMS, more than I assumed).

=== RESULTS BELOW ===

(not run)
