# Red team: ODDS_HISTORY H2 reb/Pinnacle blend "beats the market" (2026-10-10)

**Verdict: WOUNDED.** I found no leak. The gain is not noise, it reproduces exactly, and it replicates. But it is
not mainly the model's information. About half of it (-0.0012 of -0.0022 at T-60) is a constant, model-free "shade
the over" correction: it survives shuffling the labels within each game, and an intercept-only market correction
recovers it with no model at all. The model's player-level part is about -0.0010 [-0.0022, +0.0001] at T-60
(not significant) and -0.0014 [-0.0026, -0.0002] at T-5. Both are below the rule's 0.002 floor. "Pinnacle only" is
an artifact of which players are sampled: on the players Pinnacle quotes, the same blend beats DraftKings by
-0.0027.

**Single most likely failure mode if the claim is wrong:** the blend is averaging two forecasters that are biased in
opposite directions. The production model sits low on quoted players: mean residual +0.12 reb, and it leans
under on 78% of pairs. Pinnacle's de-vigged reb line overprices the over: 0.4896 implied vs 0.4652 realised. The blend
wins mostly by landing between the two, not by knowing which player will clear the line. If either bias moves (the
book reprices, or the model is recalibrated), most of the gain goes with it.

Claim under attack (reports/odds_history.md, results_2024.json, rule docs/prereg/ODDS_HISTORY.md sha256 d5b8b162):
2024-25 blend `logit p = logit p_mkt + w (logit p_model - logit p_mkt)`, w fit on 2023-24 (0.25 T-60, 0.30 T-5),
vs Pinnacle de-vigged reb: -0.0022 [-0.0035, -0.0009] n 6,645 / 1,257 games (T-60); -0.0022 [-0.0039, -0.0007]
n 7,011 / 1,268 games (T-5); Holm 0.007 / 0.042 within the 16-test per-timing family.

Scope: seasons 2023 and 2024 only; season 2025 was never loaded (every loader filters `season = 2023|2024`, and
`guard_season` plus the asserts in the scorer refuse >2024). I opened all DuckDB connections with `read_only=True`.
Everything ran on CPU, and I wrote nothing under `nba/`, `research/` or `configs/`. Attack scripts are in the session
scratchpad (`$SP` = `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/7ff798e7-932b-4807-b103-45d365238231/scratchpad`).
They import the scorer's own functions (`pairs_for_season`, `blend_p`, `fit_blend_weight`, `boot_ci`,
`ev_cell`), so I re-derived the pairs with the production pairing code rather than a re-implementation.

## Re-derivation (pass)

`cd /Users/devin/Downloads/nba-prediction && .venv/bin/python $SP/build_pairs.py $SP` (exit 0) rebuilds the paired
frames from model_rows/market_rows plus read-only box scores and tips. It gives 425,921 pairs for 2023 and 429,951 for
2024, with 0 leak-guard violations. `.venv/bin/python $SP/a0_rederive.py` (exit 0) refits w from scratch on 2023,
gets w = 0.25 / 0.30, and reproduces both claimed cells to the last digit: -0.0022 [-0.0035, -0.0009] p 0.0008 and
-0.0022 [-0.0039, -0.0007] p 0.0052. Bootstrap seeds 1 and 99 give the same CIs to within 0.0001.

Context the report does not state: on the T-60 cell the realised over rate is 0.4652, against a mean de-vigged
Pinnacle P(over) of 0.4896 and a mean model P(over) of 0.4306. The mean logit(model) - logit(market) is -0.249, and
the model leans under on 78.3% of pairs. T-5 looks the same: 0.4670 / 0.4888 / 0.4266, leaning under on 77.8%.

## Per-check table

| # | Check | Command | Result (n, CI) | Pass/fail |
|---|---|---|---|---|
| 1 | Shuffled labels, within game_id | `$SP/a3_shuffle.py 7` (200 reps; w refit on shuffled 2023, scored on shuffled 2024); `$SP/a6_ev.py` (500 reps, frozen w, seed 11) | With frozen w, the shuffled gain does **not** vanish. T-60 shuffled mean -0.00120 [q2.5 -0.00216, q97.5 -0.00032], and 2.0% of shuffles reach the observed -0.00221. T-5 shuffled mean -0.00064 [-0.00172, +0.00053], 0.4% reach the observed value. Observed minus shuffled mean: -0.0010 (T-60), -0.0016 (T-5). With w refit: T-60 mean -0.0012, T-5 mean -0.0009. | **FAIL (informative)**: about 54% (T-60) of the gain survives with player-level information destroyed. That part is a level/bias channel, not a leak. |
| 1b | Calibrated null (outcome ~ Bernoulli(p_mkt), so the market is exactly right) | `$SP/a3_shuffle.py 7` | reb/Pinnacle null gain at T-60: 2.5-97.5% [-0.0001, +0.0004]. None of the 32 blend cells reached -0.0022 with CI < 0 in any of 200 reps. | PASS: not noise |
| 2a | Planted future, market side | `$SP/a8_timing.py` (raw `odds_history.duckdb`, read_only) | Raw Pinnacle reb T-60 rows: lead to the real tip is 62.6-79.4 min (2023) and 64.3-64.4 min (2024), with 0 rows under 60 min and 0 after tip. The scorer's leak guard finds 0 violations in 196,078 / 206,202 T-60 pairs. T-5 is really T-9 (9.3-9.4 min). | PASS |
| 2b | Planted future, model side | Code read: `collect_stat_rows` trains on `game_date < block start` (month blocks), calibrates on train only, and features are `stat_feature_names` (realised minutes/starter come from `attach_realised` and are labels only). `$SP/a4_missing.py`: recency-update test. `pytest tests/research/eval/test_odds_history_eval.py tests/props/test_context_residual.py` | 45 passed. Exit code 1 comes only from the repo-wide 70% coverage gate on a 2-file run, not a test failure. Recency update: corr(m_{t+1}-m_t, y_t - m_t) = +0.98 (2023 and 2024), corr(m_{t+1}, y_{t+1}) = 0.68-0.70, which is a normal recency predictor, not one that contains tonight. A0: production OOF rows match LOWER_TAIL CRPS with delta 0.0, and every component row matches. | PASS. The one-extra-day lag test was not run because it needs a refit. |
| 3 | Missingness: Pinnacle quote vs outcome / model error | `$SP/a4_missing.py`, `$SP/a5_replicate.py` (3b) | The quote is selected on things knowable before tip: quote rate is 45% for starters vs 8% for bench, and 0.7% for players who end with <5 min (they get no line). Conditional on the model, quoted players outrun its mean: resid gap +0.12 [+0.02, +0.22] within DK-quoted rows in 2024, +0.18 [+0.09, +0.27] in 2023. The blend beats DK on Pinnacle-quoted players (-0.0021 [-0.0039, -0.0003], n 6,252) but not on Pinnacle-unquoted ones (-0.0009 [-0.0022, +0.0004], n 8,496). On the same-line intersection (n 4,926) the blend beats Pinnacle by -0.0022 [-0.0036, -0.0008] **and DK by -0.0027 [-0.0047, -0.0007]**. | PASS on leakage: the quote is posted at or before T-60. FINDING: the selected sample is where the effect lives. It is not a Pinnacle pricing weakness. |
| 4 | Replication | `$SP/a5_replicate.py`, `$SP/a9_swap.py` | Season swap (w fit on 2024 = 0.30, scored on 2023): -0.0022 [-0.0040, -0.0003] (T-60), -0.0028 [-0.0047, -0.0009] (T-5). 2023 at the frozen w is in-sample: -0.0022 / -0.0028. Halves of 2024: -0.0024 [-0.0042, -0.0006] then -0.0020 [-0.0039, -0.0002] (T-60); -0.0022 [-0.0045, -0.0000] then -0.0023 [-0.0044, +0.0001] (T-5). Power de-vig: -0.0021 [-0.0034, -0.0009] / -0.0021 [-0.0037, -0.0005]. Adjacent cells all have a negative sign but are below the floor: DK -0.0014 / -0.0010, FD -0.0008 / -0.0005, consensus -0.0009 / -0.0006. Drop the best month (Nov): -0.0018 [-0.0032, -0.0004] / -0.0018 [-0.0035, -0.0001]. Regular season only (drop 626 / 601 playoff rows): -0.0017 [-0.0031, -0.0003] / -0.0018 [-0.0035, -0.0001]. Drop the top-5 contributing players (31% / 38% of the gain): -0.0016 [-0.0029, -0.0003] / -0.0015 [-0.0031, +0.0002]. | PASS on sign. WOUNDED on size: every cut that removes the playoffs, the best month or the top players lands below the 0.002 floor. |
| 5 | Fragility of w | `$SP/a5_replicate.py` (5) | The 2024 argmin is 0.30 at both timings. The curve is flat from 0.20 to 0.35: T-60 -0.00201 / -0.00221 / -0.00229 / -0.00225, T-5 -0.00207 / -0.00223 / -0.00224 / -0.00212. The 2023-fit w sits on the plateau. w = 0.10 already gives -0.0013 [-0.0018, -0.0007]. | PASS (robust) |
| 6 | Multiplicity across the real family | `$SP/a7_mult.py`; null rate from 1/1b | Rule family (16 per timing): Holm 0.0072 / 0.0416. Both timings plus H3 (m = 33): Holm 0.0144 / **0.0832**. All 64 cells plus H3 (m = 65): Holm 0.0272 / **0.166**; BH 0.0016 / 0.0099. The 32 blend cells alone: Holm 0.026 / 0.161. Chance that any of the 32 blend cells reaches -0.0022 with CI < 0: 0/200 under the calibrated null (95% upper bound about 1.5%), 2/200 under within-game shuffles (both the t60/reb/pinnacle cell). | T-60 survives every family. **T-5 fails Holm in every family wider than the frozen 16.** The section 7 "strongest" label (which needs a T-5 pass) holds only because Holm is run per timing. |
| 7 | Integer support / pushes | `$SP/a5_replicate.py` (7) | 0 whole-number lines in 6,645 / 7,011 pairs (all lines are x.5: 2.5 to 13.5+), so there are 0 pushes. 0 model probabilities sit at the clip bounds, with 139 / 148 distinct values. Dropping clipped rows changes nothing: -0.0022. Both arms are binary forecasts of the same event, so the integer-vs-continuous artifact (T114-T118) cannot apply. | PASS (not a factor) |
| 8 | Effect size / EV | `$SP/a6_ev.py` | -0.0022 is 0.32% of 0.686. Scorer EV (blend edge >= 0.03 at Pinnacle's T-60 price): +4.25% [-0.12%, +8.65%] on 1,567 bets, 1,435 of them unders. Blind under on all 6,645 pairs: -2.16% [-4.33%, +0.09%]. Blind over: -11.4% [-13.7%, -9.1%]. Pinnacle reb overround is 7.0%. With the rule's literal edge (`p_model`, not the blend; section 3 says "model edge"): +0.84% [-1.65%, +3.38%] on 5,121 bets. Intercept-only correction: never reaches a 0.03 edge, so 0 bets. Post hoc and descriptive only: blend-selected unders +4.85% [+0.18%, +9.51%] (n 1,435) vs unselected unders -4.10% [-6.60%, -1.60%] (n 5,210). | No positive-EV claim under either reading. The money rests on unders, and the scorer used the blend's edge where the rule text says the model's edge (minor deviation; see code findings). |

### Decomposition (the core finding)

`$SP/a1_intercept.py` fits b on 2023 and scores on 2024, game-clustered (B = 5000 for the reb rows, 2000 for the
32-cell sweep):

| T-60 reb/Pinnacle | delta vs market | CI | n |
|---|---|---|---|
| Frozen blend (w = 0.25) | -0.0022 | [-0.0035, -0.0009] | 6,645 |
| Intercept only, `logit p_mkt + b` (b = -0.091, no model) | -0.0012 | [-0.0023, -0.0001] | 6,645 |
| (b, w) jointly fit on 2023 (b = -0.006, w = 0.26) | -0.0023 | [-0.0037, -0.0009] | 6,645 |
| (b, w) minus intercept only = the model's player-level part | -0.0011 | [-0.0022, +0.0001] | 6,645 |
| T-5: intercept only (b = -0.107) | -0.0009 | [-0.0021, +0.0003] | 7,011 |
| T-5: model's player-level part over intercept | -0.0014 | [-0.0026, -0.0002] | 7,011 |

When the model term is present the jointly fit intercept goes to about 0. So the model's under-lean is what
supplies the bias correction in 2023 as well. The intercept-only correction helps in every reb book cell
(-0.0005 to -0.0012), and every pts/ast/fg3m cell fits a negative b. The over-bias is market-wide; it is
largest for reb.

## Inference notes

- **Power / MDE.** The SE of the T-60 delta is about 0.00066 (CI width / 3.92). The MDE at 80% power, two-sided 0.05,
  is about 0.0019, which is the size of the floor. The player-level component (-0.0010 to -0.0014) sits below the MDE.
  "Underpowered, cannot conclude" is the honest label for the part that is actually model skill.
- **Corrected family.** Under the frozen rule (Holm per timing, m = 16) both cells pass, and that rule is not
  changed here. Under the family that was actually run (64 cells + H3), only T-60 survives Holm. BH at m = 65 keeps
  both, but BH also admits t60/reb/draftkings and t60/ast/consensus, which sit below the floor.
- **Too good to be true?** No. The effect is small, it is not concentrated in one team, and it is spread across months
  (Oct is positive; 7 of 9 months are negative at both timings). It is somewhat concentrated in the playoffs: May-June
  is -0.005 to -0.009 on about 360 rows, and removing them takes it under the floor.

## Rule integrity and code findings (severity-ranked)

- **minor.** The frozen-block hash re-verifies (`awk ... | shasum -a 256` gives d5b8b162, exit 0). It is identical at
  freeze commit 2d4cda1 and result commit 128d26a. `git diff 2d4cda1 128d26a` touches only the ledger and the Results
  heading; the scorer did not change.
- **minor.** The time labels are inconsistent. The rule header says "FROZEN 13:40 CT" and the void freeze is placed
  "at 13:30". Git records be03f2d at 13:23:57, 2d4cda1 at 13:26:43 and 128d26a at 13:28:46 (-0500), and results_2023.json
  / results_2024.json have mtimes 13:26:56 / 13:27:09. On the machine clock the order is correct (freeze, then 13 s
  later the artifacts). As written, though, the header puts the freeze 13 minutes *after* the results existed. Fix the
  prose, not the rule.
- **cannot verify.** The claim that no score ran under the voided 13:23:57 freeze. Any earlier results file was
  overwritten at 13:26:56, and the window was 2 m 46 s.
- **minor (rule deviation).** The section 3 / CHOICES `ev_gate` text says edge = `p_model(side)` - market. `ev_section`
  uses the blend's probability for H2 passes. Neither reading gives a positive-EV claim (+4.25% [-0.12, +8.65] vs
  +0.84% [-1.65, +3.38]), but the report should print the literal-rule figure, or the rule should say "blend" in a
  dated amendment.
- **minor.** The "T-60" snapshot is really T-64 and the model's report gate is real tip - 60. So the model can see up
  to about 4 minutes of injury reports that the T-60 market had not. This tilts toward the model at T-60 only. The T-5
  (really T-9) cell, where the market is later, shows the same gain, so this is not what drives it.
- **minor.** The CI code is `nba.props.metrics._clustered_boot_means` (a private helper), and `nba/truth/bootstrap.py`
  does not exist. The bootstrap is correct (clustered by game, B = 5000, seed 2026), but it is not the single CI module
  the role spec calls for.
- **minor.** Housekeeping in the report and results: the Results heading cites "ledger T203-T202" (T202 is REFEREES,
  and the ODDS_HISTORY rows are an unnumbered bold paragraph). The report still carries the pre-freeze heading
  "Choices the rule text leaves open (amend before the freeze if wrong)".
- No blocker found. I could not verify: the one-extra-day feature lag (it needs a model refit), and an end-to-end
  planted future row through `model_rows` (also a refit). I relied on the existing tests, the A0 equality with
  LOWER_TAIL, and the recency-update test.

## Recommended changes (not applied)

1. Keep the frozen verdict label for what the rule says ("reb: blend beats Pinnacle under the 16-per-timing Holm"). But
   attach the decomposition: about half is a model-free under correction; the model's player-level part is -0.0010 to
   -0.0014, below the floor, and not significant at T-60.
2. Do **not** treat this as evidence for a "market-as-prior weight" promote-candidate on the model's information. If
   anything gets drafted for the 2025 touch, register it as a new question: either (a) a fixed intercept correction on
   reb (b fit on 2023+2024), or (b) the two-parameter (b, w) blend. Both need a fresh rule, a 2025 holdout row logged
   before any run, and the same floor. Expect (b) to fail the floor on its own.
3. Fix the header times. Add the literal-rule EV figure as a dated amendment note (reporting only, no rule change).
4. Move the scorer's CI onto the planned `nba/truth/bootstrap.py` when it lands (steward, separate task).

## Proposed ledger row (draft; not appended)

| T2xx | 2026-10-10 | ODDS_HISTORY adversary (docs/reviews/redteam_odds_history_reb_blend_2026-10-10.md) | reb/Pinnacle H2 blend 2024: model-free intercept correction alone -0.0012 [-0.0023, -0.0001] (T-60, n 6,645, 1,257 games); model player-level part over intercept -0.0011 [-0.0022, +0.0001] (T-60), -0.0014 [-0.0026, -0.0002] (T-5, n 7,011); within-game shuffles keep -0.0012 of the -0.0022; same blend vs DK on Pinnacle-quoted same-line rows -0.0027 [-0.0047, -0.0007]; T-5 Holm 0.083 at m=33 | -0.0022 | -0.0035 | -0.0009 | game | 0.007 (rule family) | WOUNDED: no leak, not noise; half bias, model part below floor; EV CI includes 0 | not holdout (<=2024; 2025 never loaded) |
