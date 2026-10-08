# ctxres_v2 experiment 2 -- ANALYSIS PLAN (written BEFORE any 2024 result exists)

Timestamp: 2026-10-08 17:20 CDT (local). Experiment 2 was running on Colab at this time; the run
dir has not been pulled and no experiment-2 2024 number has been viewed by the author of this plan.
Companion to `docs/CTXRES_V2.md` (pre-registration, amendments A1/A1.1, recorded selection).
Nothing here changes `nba/eval/ctxres_v2_eval.py` or its decision logic. If this plan and the
pre-registration ever disagree, the pre-registration wins.

## 1. Two tiers, kept apart

| tier | what | how produced | allowed language |
|---|---|---|---|
| CONFIRMATORY | the pre-registered keep rule on the RECORDED candidate `xgb_v12_poisson_nb` vs `v1_prod`, 2024 played rows, 6 checks per stat, BH m=4 across stats, A1.1 PIT coverage, leak audit; plus the calibrator rule on that candidate | `uv run python -m nba.eval.ctxres_v2_eval --run <run_dir> --out reports/ctxres_v2_exp2_verdict.json` | "KEPT / NOT KEPT per the pre-registered rule" |
| DESCRIPTIVE | everything else: the 2024 leaderboard of every arm, arms vs candidate, blend vs best single, all calibrator variants, ablation, drift, zero-inflation, slices, coverage per arm | `uv run python -m nba.eval.ctxres_v2_descriptive --run <run_dir> --verdict reports/ctxres_v2_exp2_verdict.json --prev-metrics <exp1 metrics.json> --slice-arms xgb_v12_poisson_nb,blend_top3 --out reports/ctxres_v2_exp2_descriptive.md` | "would survive BH within family F*", "hypothesis for 2025 / forward", never "wins" or "kept" |

Rules of engagement:
1. The confirmatory command is run FIRST and its output saved unaltered. The descriptive report
   embeds it (section 0) without recomputation of the decision.
2. If this run's own 2023 best differs from the recorded candidate (GPU nondeterminism), the rule
   is applied to the recorded candidate; the other is reported by the eval as
   `verdict_rerun_best_arm` and treated as descriptive.
3. A run without `leak_audit`, with a missing candidate/v1_prod OOF, or with NaN candidate CRPS is
   INVALID: no verdict, no descriptive claims; fix and rerun (never "partial" reporting).
4. No arm other than the recorded candidate may be promoted on the strength of 2024. No second
   candidate is chosen after seeing 2024, no threshold or floor is re-tuned, no stat is dropped.
5. Reconciliation first: the descriptive script recomputes every arm's mean CRPS from the parquets
   and compares with `metrics.json`; any gap > 1e-4 is investigated before anything is interpreted.

## 2. Confirmatory test: what it can and cannot tell us (power and information)

* Candidate: `xgb_v12_poisson_nb` (2023 selection ratio 0.9331; next best `xgb_v12_quantile` 0.9384;
  top five span 0.9331-0.9490, a gap the 2023 data cannot cleanly separate). 2024 is untouched by that
  choice, so the candidate's 2024 estimate carries no selection (winner's-curse) bias.
* n per stat is the number of 2024 played player-games (about 25-27k per stat; game-clustered
  effective n is smaller, roughly the number of 2024 games times the within-game design effect).
  Power is reported by the descriptive script as `MDE80` = 2.8 x clustered SE (SE recovered from the
  bootstrap CI), per arm and stat. The floor (-0.005) should be compared with MDE80: if MDE80 for a
  stat exceeds 0.005, a non-pass there is "underpowered for the floor", not "no effect".
* Checks 1-3 (floor, CI, BH over 4 stats) are NOT informative about whether v12 features help:
  experiment 1 already showed `xgb_v12_quantile` beating `v1_prod` on 2024 by -0.0744 / -0.0376 /
  -0.0154 / -0.0205 CRPS (pts/reb/ast/fg3m, T073-T076, CIs about +/-0.001 to 0.007, i.e. z > 10)
  on exactly the same rows with the same reference. A v12-feature arm clearing these checks again is
  the expected outcome and adds approximately no evidence. Their only role is to catch the new model
  family (Poisson mean + NegBin dispersion) failing badly.
* Checks 4-6 (slice regression, bias, coverage) are where the new information is. Each is a
  point-estimate threshold, so noise matters: the slice check (point delta > +0.01 at n >= 300) has
  no CI; small slices have SE far above 0.01, so a marginal slice failure is read together with its
  n and the descriptive slice table, but the rule is applied literally.
* Coverage caveat (A1.1, binding): the candidate emits integer NegBin `ppf` grids, for which no
  construction in the simulation reached +/-0.01 (c2 error up to 0.032). A PIT coverage within
  [0.715, 0.75) or (0.85, 0.885] is "estimator-limited": the verdict stays literal (check 6 fails) but
  the report must say the coverage failure is not distinguishable from correct calibration.
* Calibrator rule (candidate, 4 kinds x 4 stats = 16 cells): pre-registered WITHOUT multiplicity
  control. With ~25k rows nearly any threshold-LL improvement has CI excluding 0, so "kept" under
  that rule is weak. Report each kept cell as `KEPT (uncorrected, 16 cells)` and show the same cells
  in family F4. Experiment 1 already found isotonic kept on all 4 stats, so a repeat on iso is a
  replication on the same rows (see section 4), not new evidence.

## 3. Multiplicity: families and corrections for descriptive claims

Only the candidate's pre-registered verdict is confirmatory. Descriptive p-values come from the
game-clustered bootstrap (`boot_p_value`, two-sided; floor 1/(n_boot+1)) or, where only a CI exists in
`metrics.json`, a CI-implied normal approximation (marked approx). Each family gets its own BH at
q = 0.05; families are NOT pooled with each other (pooling hundreds of tests would only make the
correction vacuous), and a cell is never reported as significant on a per-cell basis.

| family | cells (expected) | contrast | source | correction |
|---|---|---|---|---|
| C (confirmatory) | 4 | candidate - v1_prod CRPS | eval | BH m=4 as registered |
| F1 | 13 arms x 4 stats = 52 | arm - v1_prod CRPS (excl. v1_prod, logit_thr) | parquet | BH, plus per-stat q shown |
| F2 | 14 x 4 = 56 | arm - v1_prod threshold log loss (incl. logit_thr) | parquet | BH |
| F3 | 12 x 4 = 48 | arm - candidate CRPS (non-inferiority screen; "close" = CI upper <= +0.005) | parquet | BH |
| F4 | about 14 arms x 4 kinds x 4 stats (about 200+) | calibrated variant - own arm, threshold LL | metrics.json | BH (approx p) |
| F5 | 10 groups x 4 stats = 40 | drop-one ablation vs full `xgb_v12_tuned` | metrics.json | BH (approx p; CI from 200 resamples) |
| F6 | 4 | blend_top3 - candidate CRPS | parquet | BH m=4 |

Notes on honesty of the correction:
* BH controls FDR under independence/PRDS. All cells share the same 2024 rows and the same reference,
  so they are strongly positively dependent (PRDS plausible; BH is conservative, not anti-conservative).
* With about 25k rows nearly every F1 cell has q << 0.05; BH therefore does not discriminate. The
  effective filter is the effect floor: report `gate` = delta <= -0.005 AND CI < 0 AND q_fam <= 0.05
  (an analogue of checks 1-3 only, never the keep rule).
* A shared-reference warning: every F1/F2 cell uses the same `v1_prod` errors, so a lucky or unlucky
  reference moves all cells together. Month-fold consistency (`folds`: months with negative delta /
  months) is shown beside every CRPS delta as a cheap robustness read on temporal dependence that the
  game bootstrap ignores (games on the same night and a player's consecutive games are correlated).
* The CI/bootstrap is paired per player-game and resampled by game_id (games are the dependence unit);
  seeds are logged. It does not resample players or dates, so it still understates uncertainty for
  claims about future seasons.
* Slices (section 8 of the report) are descriptive only: no per-slice significance claims.
* Claim wording: an F-family survivor is "a hypothesis that survived within-family BH on 2024 rows
  that are not independent of earlier looks"; it is not a result.

## 4. Cross-experiment honesty (quantified)

What was seen before this experiment existed: experiment-1 2024 results for `v1_prod`,
`xgb_v12_prodcfg`, `xgb_v12_tuned`, `xgb_v1_tuned`, `xgb_v12_rel`, `xgb_v12_both`, `xgb_v12_pruned`,
`xgb_v12_quantile`, `blend_top3` (and, being in the same sweep, `xgb_v1_prodcfg`, treated as seen).

* Cell accounting for F1: 9 seen arms x 4 = 36 of 52 cells (69%) re-use rows and arms whose 2024
  outcomes were already viewed; only `xgb_v12_poisson_nb`, `glm_poisson_nb`, `mlp_quantile`,
  `catboost_v12` (16 cells, 31%) are new arms. The report marks `seen1 = Y/N` on every row.
* The 2024 rows are identical and `v1_prod` is the same code on the same rows, so for seen arms the
  exp-2 numbers should reproduce exp-1 up to GPU nondeterminism/hyper-parameter retuning. The
  reproducibility table (`--prev-metrics`) shows exp2 - exp1 CRPS; agreement is a data-pipeline sanity
  check, NOT independent confirmation. Treat exp-1 and exp-2 on 2024 as one look at one sample.
* The v12-features-help question is answered from exp-1 (z > 10, T073-T076); exp 2 cannot raise or
  lower confidence in it by more than the reproducibility check.
* The new arm family's 2024 numbers (poisson_nb, glm, mlp, catboost) ARE unseen, but they inherit the
  design knowledge from 2024 in two ways: (i) count-family arms were added after exp-1's fg3m
  coverage failure on 2024; (ii) amendments A1/A1.1 changed check 6 after that failure (A1.1 was chosen
  by known-truth simulation with no model output; disclosed in the pre-registration). These are
  design-level researcher degrees of freedom; they cannot be removed, only disclosed. They mean 2024
  is "tuning-adjacent" for the design, and the candidate choice itself (2023-only) is clean.
* Design-level looks at 2024 so far: exp-1 first run, exp-1 completion, A1/A1.1 motivation, exp-2.
  Any 2024 conclusion is therefore a hypothesis for the single clean 2025 touch (separate
  pre-registration, with a `HOLDOUT_ACCESS_LOG.md` row written before running) and the forward
  2026-27 season. Note `HOLDOUT_ACCESS_LOG.md` already records season 2025 as non-virgin for earlier
  work; the v2 models themselves have never touched 2025, but the "clean" qualifier is limited to
  v2, not to the 2025 data in general.
* Ledger: after the run append the 4 confirmatory rows (exp2) to `TEST_LEDGER.md` with the family
  counts of F1-F6 (descriptive cells are logged as family counts, not as individual passes).

## 5. Descriptive report contents (script `nba/eval/ctxres_v2_descriptive.py`)

Section: 0 confirmatory copy; 1 leaderboard per stat (CRPS, delta vs v1 [clustered CI], q_family,
q_stat, MDE80, gate, month folds, bias, PIT coverage with integer-grid asterisk); 2 threshold-LL
leaderboard; 3 arms vs candidate + blend vs candidate (F3, F6); 4 ablation (F5); 5 calibrators (rule
recompute on the candidate + F4 survivors); 6 fg3m zero-inflation (observed vs predicted P(0)); 7 drift
table; 8 slices (`--slice-arms`), 8b reproducibility vs exp-1; 9 reconciliation; 10 2023 selection scores.
Memory: one arm at a time; q19 is read only for coverage; arms stored without q19 (light arms, per
`write_oof_variant`) report CRPS/bias/threshold-LL only and use `metrics.json` naive coverage.

## 6. KEY DECISION TABLE (what changes our next step)

All rows are conditional on a valid run (rule 3 above). "Pre-reg" rows are mechanical; "Descriptive"
rows only set the agenda for the NEXT pre-registered experiment.

| outcome | tier | interpretation | next step | not allowed |
|---|---|---|---|---|
| Candidate KEPT on all 4 stats | pre-reg | v2 replaces production per the rule | register as `candidate` (manual promote), write the 2025 confirmatory pre-registration + holdout log row first, then one touch; start forward 2026-27 paper tracking | claiming out-of-sample confirmation of v12 features |
| Candidate fails ONLY coverage (any stat, again) | pre-reg | NOT KEPT, literal. Check estimator band: if PIT cov is within 0.035 of the window, "estimator-limited" | amendment A2 (before any rerun): store NegBin (mu, alpha) per row so an exact pmf PIT is computable; and/or consider `xgb_v12_quantile` or a calibrated candidate as a NEW pre-registered candidate for 2025 | switching to another arm or loosening 0.75-0.85 on the strength of 2024 |
| Candidate fails floor/CI/BH on one stat (esp. fg3m/ast) | pre-reg | v12 not kept for that stat | per-stat routing (v2 where kept, v1 elsewhere) is a NEW hypothesis: pre-register in `nba/stack` router, evaluate on 2025/forward | declaring per-stat "partial keep" as production |
| Candidate fails a slice | pre-reg | NOT KEPT; identify slice and n | route that slice to v1 in a router experiment; check slice in descriptive F-table for other arms | dropping the slice check |
| Candidate fails bias | pre-reg | NOT KEPT; Poisson mean should be unbiased, so suspect train/test shift (see drift table) | inspect drift section; fix and re-pre-register | re-centring on 2024 |
| Candidate fine on checks 1-3 but another arm is better than it (F3 upper < 0) | descriptive | rank instability between 2023 and 2024 | list as hypothesis for 2025; do NOT swap candidate | post-hoc candidate change |
| `catboost_v12` or `mlp_quantile` "close" (F3 CI upper <= +0.005) or better | descriptive | diversity source | add as a router/stack candidate (`nba/stack`) and test blend on 2025/forward; check slice complementarity | adopting on 2024 numbers alone |
| `blend_top3` beats candidate (F6 q <= 0.05 and delta <= -0.005) | descriptive | stacking has value | pre-register a stacked candidate with weights fit on 2023 only | claiming blend "won" |
| blend within +/- floor of candidate | descriptive | single model is enough | keep the single model (simpler) | |
| a feature group useless (ablation CI includes 0 or < 0.005 on all 4 stats, F5) | descriptive | candidate for pruning | schedule a pre-registered prune test (ablation is on `xgb_v12_tuned`, not the candidate); keep until then | deleting the group now |
| a feature group harmful (drop - full < 0 with CI < 0 on a stat) | descriptive | possible overfit/leak surface | review its as-of logic (leak tests) before any further run | |
| calibrator kept by rule | pre-reg (uncorrected) | iso was already kept in exp-1 on these rows | adopt only after 2025; show F4 context | calling it confirmed |
| `v1_prod` 2024 CRPS differs from exp-1 by > 0.001 | diagnostic | pipeline/nondeterminism drift | investigate before interpreting anything | |
| fg3m predicted P(0) off by > 0.03 for poisson_nb | descriptive | zero inflation not captured | zero-inflated NegBin arm as next experiment | |
| rerun's own 2023 best != recorded candidate | pre-reg | GPU nondeterminism | rule on recorded; report both | |

## 7. Order of operations when the run is pulled

1. `make colab-pull JOB=ctxres_v2_sweep`; confirm `metrics.json` has `complete: true`, `leak_audit`,
   `catboost_available`, and that `oof/` has all variants (no arm dropped; budget `full` is strict).
2. Confirmatory command (section 1); save JSON and print table.
3. Descriptive command; read section 9 (reconciliation) first.
4. Write results into `docs/CTXRES_V2.md` as a new dated section (confirmatory result first, descriptive
   second, labelled), append ledger rows; do not edit earlier sections or this plan.
