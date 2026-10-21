# PRESEASON_ROLE_PRIOR pre-freeze review (adversary, 2026-10-20, revised for rule revision 2 on 2026-10-21)

Object: `docs/prereg/PRESEASON_ROLE_PRIOR.md` on `agent/modeler-preseason-prior` at **0a86ab5 (revision 2)**. Freeze hash of the text above
the results marker: `7d8b9aab41dd1c52ba7bffdac3d34b4ab3a9f7985c812fbbc64a0be93bb1347e` (my `awk … | shasum -a 256`, exit 0). Also read:
`reports/prereg_preseason_role_prior/{gate,calibrate,check,rehearse}.json`. I read the harness `research/eval/preseason_role_prior.py` only
where the rule text depends on it. I started on the first draft f4b0dea, and every finding against it is reconciled below as RESOLVED or OPEN.
Compared with the frozen `LINE_CALIBRATION.md` and `WOWY_STRUCT.md` and their pre-freeze reviews.

Model class: a fixed-weight multiplicative rescale of the whole served grid of the production forecast (LightGBM context residual + conformal
grid), scored by integer-support CRPS. Playbook sections: "Gradient-boosted trees" and "Quantile and distribution layers".

Verdict: FIX_BEFORE_FREEZE

Scope: the required edits R1-R8 are **text only**. They make the rule say what the harness already does, or say how a result will be read.
None changes an arm, a floor, a word or the family. A diff re-check of the listed lines approves the new hash. The optional changes O1-O4
would change arms. They are recommendations for the main session and Devin, not conditions of this verdict.

Top finding: **A2's mean shift is generic, and A4 shares it. The P condition is a real guard, but the rule text describes A4 as a different
arm from the one the harness runs.** On covered mover rows (harness `lambdas()`, label-free; n = 911 / 761):

| arm | mean lambda, pts 2023 / 2024 |
|---|---|
| A1 (minutes only) | 1.068 / 1.057 |
| A2 | 1.074 / 1.065 |
| A4 | 1.077 / 1.059 |

The correlation of log A2 with log A4 lambdas is **0.59-0.78** across the 8 cells. So about +6-7% of A2 is a minutes push that a permuted
donor gives just as well. The push is close to stratum-mean reversion of `min10` toward kappa x a typical preseason mpg. Stayers show it too:
+2-8% on 2,669 / 2,739 rows. That makes A4 a generic-shrink control in practice, which is what the P condition needs.

But the rule says A4 permutes "the covered affected **players'** preseason vectors". `a4_perm` permutes **rows**. A player's 10 rows get
about 10 different donors, and in 2.2% of rows the donor is the player himself. A second person following the text would build a player-level
placebo. That placebo is noisier per player and has a different P failure rate. R1 fixes the text.

No leak was found. No path lets 2023/2024 regular-season information into any weight or lambda. kappa (2022 regular-season minutes) is clean.

## Checks (CPU, read-only, seasons <= 2024; 2025 never loaded; exit codes shown, never piped)

`$S` = `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/763dc211-fdb9-4487-b696-02f28aa88347/scratchpad/adv_attack`;
`PY` = `/Users/devin/Downloads/nba-prediction/.venv/bin/python -I`, cwd = the main checkout. Every DuckDB connection is `read_only=True`.

For 2023 and 2024 **no outcome was read**: no `y`, no tonight's `minutes`, no current-season stat totals. `harness_lam.py` calls
`read_rows` with an explicit column list that excludes both. **Season 2022 outcomes were read** (kappa, rate reliability). That is the rule's
own calibrate season, outside select and report, and it is disclosed here.

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| 1 | preseason tables, as-of | inline `PY -c` (exit 0) | 2,464 / 2,432 / 2,636 rows (2022/23/24); 65 / 64 / 69 games | last preseason game 2022-10-14 / 2023-10-20 / 2024-10-18 vs first `002` game 10-18 / 10-24 / 10-22: strictly before. `starter`: 0 NULLs, exactly 5 per team-game in 130/130, 128/128, 138/138. NULL minutes are DNPs (623 / 598 / 646). `loaded_at` is one stamp (2026-10-21 02:26:35 UTC) | yes |
| 2 | leakage paths | rule lines 2-5, 7; harness `build_ctx`, `lambdas`, `load_pre_rows` | n/a | kappa: 2022 RS minutes, before 2023 and outside select/report; clean. Starter flag: preseason `starter` (`pre_start`); `starter10` is loaded into ctx but never used by `lambdas`. Mover / first team / g: the row's team == first team and g counts earlier played games, so nothing after the row is used (G4 removes later RS rows and asserts equal lambdas). rho_prev and A3: S-1 only. `build_ctx` selects columns without `y` or tonight's `minutes`, and `lambdas` takes no outcome argument (closes my rev-1 E8). The harness never reads `loaded_at` | yes |
| 3 | gate reproduction | `PY $S/slice.py`, `PY $S/lam.py` (exit 0) | 1,202 / 487 games; 1,025 / 468 | mover K = 10 rows and games equal gate.json. Covered 911 / 761. Movers 137 vs 136 (2023; one mover without A0 rows) and 115 = 115. Covered movers 96 / 80 | yes |
| 4 | kappa (2022, label-using, disclosed) | `PY $S/kappa2022.py` (exit 0), independent code | 165 bench / 244 starter | ratio of sums per played game **0.940 / 1.244** = calibrate.json 0.9402 / 1.2435 with the same n. Other readings: literal x20 0.918 / 1.240; median of ratios 0.896 / 1.277. Revision 2's text now names the ratio-of-sums reading. **RESOLVED (rev-1 E3)** | yes |
| 5 | lambda shift and A4 (top finding) | `PY $S/harness_lam.py` (exit 0): the harness's own `lambdas()` and `a4_perm()` | 911 / 761 covered rows x 4 stats | mean lambda A1 1.068 / 1.057; A2 pts 1.074 / 1.065, reb 1.078 / 1.070, ast 1.091 / 1.078, fg3m 1.070 / 1.067; A4 1.077-1.110; A3 1.011-1.033. corr(log A2, log A4) 0.765, 0.782, 0.720, 0.661 (2023) and 0.716, 0.668, 0.626, 0.591 (2024). A4 own-player donor share 0.022 | see R1, O2 |
| 6 | rate channel under zero news | `PY $S/r2_decomp_zero.py` vs `r2_decomp.py` (exit 0), rev-2 `rho_A0 = m_recency / max(min10, 1)` | 911 / 761 | with `rho_pre := rho_prev` the rate factor averages pts **1.007 / 1.007** (observed 1.007 / 1.007); ast 1.008 / 1.009; fg3m 1.014 / 1.010. rho_prev / rho_A0 = 1.043 (pts). Rev 1 (`mean_prod / min10`) gave 1.025 / 1.031. **Mostly RESOLVED by revision 2**: still the whole mean of the rate factor, but under 1% | yes (O1 optional) |
| 7 | undefined lambda | harness `lambdas` | fg3m: 13 / 30 rows `mean_prod = 0` | code: rate ratio := 1 when `rho_a0 <= 0`, non-finite lambda := 1, `min10 < 1` -> A0. The text states only the `min10` case. **Code RESOLVED; text R6** | yes, R6 |
| 8 | rate constants vs 2022 reliability | `PY $S/kappa2022.py` | 315 players | slope of first-10 RS rate deviation on preseason deviation (player bootstrap 2000, seed 0): pts 0.173 [0.085, 0.260], reb 0.120 [0.035, 0.201], ast 0.172 [0.095, 0.248], fg3m 0.060 [-0.018, 0.137]. Rule transmission 0.156 at g = 0, 0.086 over the fade (median m_pre 72). The constants are in range, so attack 2 does not break the rule. About 83% of a preseason rate deviation is noise: that is the honest ceiling | yes |
| 9 | zq bracket sensitivity (rev-2 item f) | `PY $S/bracket.py` (exit 0): November block, all rows, harness `block_zq` / `grid_q`, lo vs mid vs hi zq at lambda 1, 1.08, 0.92 | 4,522 / 4,624 rows per stat | lambda = 1: mid reproduces the grid (0.0-0.2% of rows differ mid vs hi). lambda = 1.08: 3.9-5.0% of rows have a grid point that differs mid vs hi. The mean of the integer grid differs by **0.0004-0.0005** lo vs hi, which bounds the per-row CRPS difference (CRPS change <= L1 change of the quantile function). That is below every floor (fg3m: half of 0.001) and has no sign. Max bracket width in the block is 0.0003-0.005 z, but 0.18 z on fg3m 2023, one tail point. Negligible | yes (O4 report) |
| 10 | words exhaustive and exclusive | rule line 7; `evaluate()` | n/a | the order is good -> PASS / PASS_OTHER_STAT, then `both` -> PLACEBO_TOO / FAIL_GUARD, then any clear -> FAIL_ONE_SEASON, else FAIL_NO_GAIN. That covers every case and each case gets one word. Code matches text. P sign: `a4gain >= 0.5 * gain`, gains positive. **RESOLVED (rev-1 E4)** | yes |
| 11 | arms fully specified (second-person test) | rule line 5 vs harness | n/a | A4 rows vs players (R1). A3 "no kappa" is implicit in code (`values_min = l10_min / l10_n`), not in text (R7). Whole-grid rescale `q0 * lam` after production's clip-at-0 and max-accumulate: declared in 'What min10 is'. **RESOLVED (rev-1 E7)** | R1, R7 |
| 12 | 2023 -> 2024 selection | rule; `run_real` | n/a | nothing is selected on 2023. `run_real` scores both seasons in one invocation. **RESOLVED (rev-1 E11)**. The one exposure is opening night 2026 (disclosed). Its direction matches A2's built-in +7%, and A4 shares that | yes |
| 13 | loaded_at = backfill | check 1; grep of the harness | all rows | the replay does not read `loaded_at`, so the stamp cannot matter for 2023/24. The text says only "not needed" (R8). Forward note: 2026 preseason loaded 02:26 UTC on 10-21, after opening night's tips | yes, R8 |
| 14 | Pinnacle secondary | rule lines 65, 98; `run_real` | 53-230 legs | the coordinator says it is not implemented in `run`. The text still lists "Pinnacle CLV" among the reported results and cites "CLV method of MODEL_CLV". A secondary that is promised but not built, and then built after the CRPS result, is a fork (R3) | **no**, R3 |
| 15 | power / MDE | gate.json | 1,202 / 1,025 | half-widths pts 0.0101 / 0.0104, reb 0.0038 / 0.0044, ast 0.0030 / 0.0032, fg3m 0.0016 / 0.0015. MDE at 80% about 1.43 x: **pts 0.0144 / 0.0149**, reb 0.0055 / 0.0063, ast 0.0043 / 0.0046, fg3m 0.0023 / 0.0021. Every floor is below its MDE, and the text does not say how a FAIL reads (R2) | R2 |
| 16 | rehearsal | rehearse.json | 60 + 200 seeds | 60 of 60. No-gain world: 182 FAIL_NO_GAIN, 18 FAIL_ONE_SEASON, 0 PASS. Planted effects 0.030 / 0.012 / 0.008 / 0.004 are about 2-3 half-widths, so the rehearsal proves the words are reachable, not that the test has power at the floors (that is R2's point) | yes |
| 17 | priors coherence | arithmetic below | n/a | unchanged from rev 1, still incoherent (R4) | **no**, R4 |
| 18 | audit text | rule line 70 (iii) | n/a | "used for kappa only": A4 strata use the flag too (R5) | minor |

Not applicable before a result: shuffled labels (nothing fitted; A4 replaces it), knockout, replication, drop-best-month. Integer support
holds: every arm is `crps_int_q` on `q0 * lam`, and the base is the same reconstruction at lambda = 1. Missingness: coverage is a pre-season
fact and uncovered rows are A0 inside the primary slice. I deliberately did not tabulate coverage by 2023/24 outcome.

## Rev-1 findings reconciled

| rev-1 | status in revision 2 |
|---|---|
| top finding (+9% generic shift; rate channel non-identity of +2.5%) | **partly resolved.** The rate part fell to +0.7% (check 6). The minutes part (+6-7%) remains, but A4 shares it (corr 0.6-0.8), so the P condition can catch a generic gain. Remaining: R1 (text), O1 / O2 (optional) |
| E1 rate ratio form | downgraded to optional O1 (effect < 1%) |
| E2 undefined rows | code resolved; text R6 |
| E3 kappa exact | **resolved**; values reproduced independently |
| E4 word precedence | **resolved** |
| E5 A4 spec / A5 | A4 spec: open as R1 (rows vs players). The 20-draw mean was dropped by design (one draw, none other made): accepted, no fork. A5: optional O2 |
| E6 A3 spec | resolved in code (>= 5 games, no kappa); text R7 |
| E7 grid | **resolved** ('What min10 is') |
| E8 planted future / stray columns | **resolved** (no outcome columns in ctx; later RS rows removed in G4). Line-70 text: R5 |
| E9 FAIL reading | open: R2 |
| E10 Pinnacle | open, changed: R3 |
| E11 one run | **resolved** |
| E12 loaded_at | code resolved; text R8 |
| E13 stayer / rate normaliser / K fade | optional O3 (`lambdas` already uses the sensitivity K in the fade: resolved for that part) |
| E14 priors | open: R4 |

## Required edits (text only)

**R1 (major). A4 as run.** Replace the A4 bullet's first sentence with:
> **A4 permuted-identity placebo:** the covered affected ROWS of a season are permuted within preseason starter flag (seed 0, one draw; no other draw
> is made or reported); each row takes the donor row's `mpg_adj` and `rho_pre` and keeps its own weights (`m_pre`, fade), `min10`, `m_recency`, `rho_prev`
> and kappa class. A row's donor is another row of the same player in about 2% of rows (measured 0.022); this is accepted.

**R2 (major). What a FAIL means.** Append to the power paragraph (line 91):
> MDE at 80% power on the K = 10 slice: pts 0.0144 / 0.0149, reb 0.0055 / 0.0063, ast 0.0043 / 0.0046, fg3m 0.0023 / 0.0021. FAIL_NO_GAIN or FAIL_ONE_SEASON
> reads "no gain at or above the MDE was detected", never "preseason has no value". The 2022 reliability slope (pts 0.17) is the reason a large gain is not expected.

**R3 (major). Pinnacle: say it is not run.** In line 65, delete ", Pinnacle CLV" from the list of reported results. In Playbook 5, replace
"P(over) at the stored Pinnacle T-60 line is descriptive (CLV method of MODEL_CLV), never a gate" with:
> no line metric is computed under this id (the gate's Pinnacle leg counts, 53-230, are recorded only to show it would be too thin); any line analysis is a new id.

**R4 (minor). Priors re-entered, coherent.** P(pts clears both) = 0.12 x 0.45 = 0.054, so P(PASS) = 0.05 leaves almost nothing for a pts
double clear that fails P or a guard. PASS_OTHER_STAT + PLACEBO_TOO + FAIL_GUARD = 0.13 implies P(some stat clears both) = 0.18, which is 0.72
of P(any clears 2023) = 0.25, against 0.45 for pts. The stated expected gains for reb / ast / fg3m (23-33% of their half-widths) support about
0.01-0.02 each for a double clear, not 0.13 combined. The words also imply P(clears 2024 only) >= 0.15 with no stated reason. Re-enter them
before the freeze, still blind.

**R5 (minor).** Line 70: "(used for kappa only)" -> "(used for kappa and the A4 strata; measured: 0 NULLs, exactly 5 starters in every team-game, 2022-2024)".

**R6 (minor).** Rule line 5, A2: add "A row with `rho_A0 <= 0` keeps a rate factor of 1, and a non-finite lambda is served as A0 (fg3m: 13 / 30 rows with a zero base)."

**R7 (minor).** A3 bullet: after "from his last 10 regular-season games of S-1" insert "(regular-season minutes, so no kappa; the 100-minute shrink toward `rho_prev` as in A2)".

**R8 (minor).** Playbook 3, after "(not needed for 2023/24)": "the replay never reads `loaded_at`; known-at for a preseason row is its `game_date`, asserted before the first scored row".

## Optional (would change arms; main session or Devin)

- **O1.** Rate factor in ratio form, `r_fac = 1 + w_r' (rho_pre_shrunk / b - 1)` with b = rho_prev (or rho_A0 as fallback). This makes zero
  news exactly the identity. Gain: removes a +0.7% shift that A4 already shares. Low value; skip unless another edit reopens line 4.
- **O2.** A5 "typical preseason line" placebo (stratum-mean mpg and rate, own weights), reported. Given corr 0.6-0.8, row-level A4 already
  does most of this job, so I no longer require it.
- **O3.** Reported, no verdict: A2 on stayers' first K team games (3,045 / 3,228 rows). If stayers gain as much as movers, the mechanism is
  "preseason minutes level", not "new team".
- **O4.** Report A2 - A0 with lo and hi bracket zq as a sensitivity (check 9 bounds the difference at about 0.0005).

## Playbook check

1. **Information or capacity?** Information: preseason minutes and rates come about 4 days before the opener. Most of A2's movement is
   minutes, and A4 shares it, so the P condition decides whether any gain is the player's own information.
2. **Enough data?** Nothing is fitted on 2023/24. kappa uses 409 players from 2022 and reproduces independently. The slice is the limit
   (MDE pts 0.014).
3. **Valid test?** Real tip comes from stored A0. Played rows are shared. Integer support holds (same reconstruction for base and arms;
   bracket effect <= 0.0005). Power is stated, and R2 says how a null reads. No placebo uses the future. The comparator has everything
   production had. The whole-grid rescale (centre and scale) is a declared approximation, and G3 guards the interval. Noisy inputs pull toward
   a null (2022 slope 0.17), and that is stated, not hidden.
4. **Lineage:** F9b (a plausible sign, not clearing), RECENT_ROLE ties. Integer support, the clustered CI and a placebo are carried forward;
   the shuffle is dropped for a stated reason.
5. **Scored against:** whole-distribution CRPS. There is no line metric under this id (R3). A PASS is not a betting claim (T477).

Class checks: missingness (pre-season coverage, uncovered = A0); duplicated signals (`rho_prev` is in A0's history: residual +0.7%, O1);
planted future (later RS rows; outcome columns not loaded).

## Most likely failure mode if a PASS is later claimed

A0 under-forecasts early-season minutes for movers. A2's +7% minutes push collects that, and the single A4 draw happens to collect less than
half of it on the cleared stat. The P condition passes on one draw's luck. Mitigation within this id: R1 states the draw honestly. The
reader of a PASS should look at A1 vs A2 and at A2 - A4's CI (`A2_vs_A4` is computed), not only the point estimates.

## Not verified

Real outcome contrasts (by design). Whether the residual `mu` scales with minutes (a declared assumption). The single-draw variance of A4's
gain (needs outcomes). The 38 possible overtime preseason team-games. The harness test suite and pin were not re-run by me (code review was
limited to what the rule text depends on).

## My prior (blind to 2023/24 outcomes; revision 2 as written)

PASS 0.04, PASS_OTHER_STAT 0.03, PLACEBO_TOO 0.08, FAIL_GUARD 0.03, FAIL_ONE_SEASON 0.18, FAIL_NO_GAIN 0.64. P(pts clears 2023) 0.12.

## Proposed rows (drafted, not appended)

- PREDICTIONS_LEDGER, adversary prior for PRESEASON_ROLE_PRIOR (rule hash after R1-R8; written 2026-10-21, blind to 2023/24 outcomes): the six
  numbers above.
- DECISIONS: none from the adversary. O1-O4 are the main session's to take or leave.
