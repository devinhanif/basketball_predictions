# RECENT_ROLE: does last-game / recent-role minutes improve P(play) beyond what production's own recency columns already say, and does that reach the props (pre-registration)

Status: **DRAFT (Mon Oct 12 00:36 CDT 2026), written by the modeler. Not frozen.** Devin pre-confirmed ("run, run, run", 2026-10-12 ~00:25 CDT); the main session commits
`prereg(RECENT_ROLE): <sha256>` BEFORE any arm is fitted and then runs Stages 1 and 2. Everything above the line `=== RESULTS BELOW ===` is the rule. Freeze evidence:
commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RECENT_ROLE.md | shasum -a 256`. Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25,
2025 = frozen holdout, never loaded in Stages 1 and 2). No arm other than the control C has been fitted.

## What Devin said
> "go, if it works, then tune the mov-elo to factor it in, and also the props model" (2026-10-12), on the WORKLOAD finding that recent-role minutes improve P(play).

## The rule in plain lines
1. Stage 1 (primary): for rostered, not-OUT players, candidate R = C' + five recent-role minutes columns must beat comparator C' (WORKLOAD's C plus production's own recent-minutes and role columns) by at least -0.005 log loss, game-clustered CI upper < 0, in 2023 (select) and 2024 (report, p <= 0.05).
2. It must also beat its past-only placebo by <= -0.003 in each season, survive a shuffled-label check that retrains BOTH arms on the same permuted labels, keep ECE within +0.005, and make no slice (n >= 300) worse by more than +0.01.
3. Stage 2a (only if Stage 1 passes): the improved P(play) enters the props through the only door it has, the unconditional legs: unconditional integer CRPS on all Cell-P rows, R vs C', floor -0.005, BH over the four stats, 2023 select / 2024 report.
4. Stage 2b (injury-Elo) does not apply; MOV-Elo has no player inputs. Both are stated below with the code reading.
5. Stage 3 (season 2025, one touch) never runs unless a committed HOLDOUT_ACCESS_LOG row with Devin's confirmation exists; the harness refuses otherwise.
6. A null is closed: no other windows, columns, or interactions; at most one follow-up, new id, only after a pass.

## Honest prior (read first)
* WORKLOAD cell P: LOAD family -0.0167 / -0.0157 log loss against C; adversary (docs/reviews/workload_review_2026-10-12.md): real pre-tip information, carried by `min_last` (-0.0094 [-0.0113,-0.0071] alone, 2024), bench rows 86-91% of the gain. Open question it could not answer: C held no last-game minutes, production's own recency columns (exponentially weighted minutes with half-lives 5/10/40, last-game starter flag, gap days) might absorb it. This rule asks exactly that. My estimate that R clears -0.005 against C': about 40%. `min5` is a half-life-5 average, so the last game carries about 14% of its weight; the sharp "he played 2 minutes last night" fact is diluted in it, which is why a gain is plausible.
* Stage 2 is much less likely to pass (about 10%). The scored props distributions are CONDITIONAL on playing. `p_play` never enters the lower-tail mixture: the mixture's `pi` is P(short | plays), fitted by `nba/props/lower_tail.py: fit_short_state` on played rows only. `p_play` enters only `mean_uncond = p*mean` and `p_ge_uncond` in `nba/props/forward.py: _ctx_row`, the numbers the parlay engine uses for unconditional legs. So "props integer CRPS vs A0 (pts 3.140236 / 3.176840)" cannot move with P(play) by construction; Stage 2a below scores the unconditional forecast instead, where it can.
* Even if Stage 1 passes, production's raw hurdle p_play is already far worse than the LightGBM P(play) arms (log loss 0.342 vs 0.283-0.298 on these rows, PPLAY_CONTEXT), so wiring any of them in would help; this rule credits only the recent-role increment over C'.

## Stage 1: P(play)
Rows: Cell P of WORKLOAD / PPLAY_CONTEXT (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report; 33,093 / 33,127 rows, DNP rate about 16.6 / 16.4%). Label `played = minutes > 0`. Month-block walk-forward, train strictly before each block, 2022 warm-up, production LightGBM binary hyper-parameters, seed 0, CPU.
* **C** (WORKLOAD's comparator, reproduced): `BASE_FEATURES` + T-60 tag-by-strength terms + morning/evening flags (pplay_context_eval).
* **C'** = C + production's recent-minutes and role columns, evaluated at any (player, date) by `research/recent_role/prod_state.py` (equal to `build_features` on every played row and to the forward placeholder path on DNP rows; gates below): `min5, min10, min40, min_gap, min_gap10, starter10, starter40, last_starter, gap_days, since_flag, flag_dir, since_team, n_season` (13 columns). Which of production's paths uses what: production's `p_play` (the minutes hurdle, `nba/props/minutes.py`) uses `BASE_FEATURES`-type columns only (trailing-150-game minutes and start rates, rest, b2b, context), all already in C; the `min5/min10/min40/starter10/gap_days/last_starter` family belongs to the props mean/scale heads and the mixture's `pi` classifier (`COMMON_FEATURES`). C' gives P(play) all of them, the strongest honest comparator. Not added: team/opponent/report columns of COMMON_FEATURES (rest, b2b, exp_margin are in C; the tag is in C).
* **R** = C' + 5 columns from `research/workload/asof.py` (unchanged, 13 unit tests): `wl_min_last` (minutes of his last played game), `wl_mean_min_l3` (mean minutes of the last 3 played games), `wl_min_3d` (minutes in the prior 3 calendar days), plus the two declared extras `wl_min_7d` and `wl_mean_min_l5`. The extras are the next-ranked columns on the adversary's 2023-24 table (a ranking that used outcomes: disclosed); fixed here, no others.
* Descriptive arms: **R1** = C' + `wl_min_last` only (does one column do it), **KO1** = R without `wl_min_last`, **PL** = C' + the five R columns replaced by the past-only placebo.
* **Past-only placebo** (`research/recent_role/placebo.py`, 4 tests): each row takes the five columns of one uniformly drawn STRICTLY EARLIER row of its player-season (NaN for a player-season's first row, 1.7% of rows). I chose past-only over a circular shift because a circular shift hands the earliest rows the latest values; a past-only draw keeps real past values, the player-level scale and the covariance among the columns, and it breaks only the link to this row's recent state. It correlates with the truth through persistence, so it is a hard null for R (conservative). It protects against crediting capacity or player-level scale, and avoids WORKLOAD's defect (a full-season permutation put later games' values on earlier rows).
* **Shuffled-label check** (the WORKLOAD one was inert): for each month block ONE permutation of the training labels (global over the training rows, `default_rng([0, block])`) trains both C' and R; both are scored on the REAL labels. Fails if `R_shuf - C'_shuf <= -0.001` with CI upper < 0. Unit tests: both arms are fitted on identical permuted labels (hash equal, different from the real labels); on a planted-signal frame the check passes with the permutation and FAILS when the permutation is switched off (the old defect), so it can fail.
* Planted-future and same-game plants on the real tables for the LOAD columns (WORKLOAD's `planted_tests`: rows on or before the cutoff unchanged, controls change), the production-state lookup (synthetic planted-future test and the parity gates), and a schedule plant.
* Missingness: null rate of every added column by realised-minutes bucket, DNP its own bucket; gap <= max(2 pp, 3 x the permuted-bucket gap).

Metric, support, inference: log loss; paired difference (candidate minus comparator) on the same rows; percentile bootstrap clustered by `game_id` (2000 draws, seed 0), two-sided clustered-bootstrap p. Primary cell: R - C', one test per season (m = 1; BH is the identity, stated not hidden). MDE (1.96 x clustered SE) printed beside each result; from T451 (CI [-0.0180,-0.0134]) I expect about 0.0024, below the floor.
**Floor -0.005 log loss** (house P(play) floor; protects against a p-value on a gain nobody could use).

Slices (descriptive plus guard; n per slice in the gate): starter vs bench (as-of `starter10 >= 0.5`), second night of a back-to-back, cold start (5 <= n_played_prior < 10), playoffs (game_id prefix 004), `wl_min_last < 5`.

## Pass rule, Stage 1 (2023 selects, 2024 confirms; all must hold)
1. 2023: R - C' <= -0.005 and CI upper < 0. 2024: the same, and p <= 0.05.
2. Guards: ECE(R) - ECE(C') <= +0.005 in each season; no slice with n >= 300 worse by more than +0.01.
3. Attack: R - PL <= -0.003 in EACH season; the shuffled-label check not failed in each season; all gate checks and plants pass; missingness within limit.
4. Naming: if R1 keeps >= 80% of the R - C' gain in both seasons the claim is "min_last"; otherwise "the recent-role set". Not a pass condition. The knockout-one-family rule of WORKLOAD does not apply: there is one family by construction.
A PASS means: Stage 2a runs automatically in the same command; a red-team review and a drafted one-touch holdout row follow. Nothing is promoted or wired in; that is Devin's.

## Stage 2 (only if Stage 1 passes; same freeze)
**2a. Unconditional integer CRPS of the props, R vs C'.** Rows: every Cell-P row (played and DNP), 2023 and 2024. Conditional distribution per stat: A0 = production context-residual + the lower-tail mixture (`pi`, `w_q`, calibration from the same walk), REFIT live per month block (train strictly before the block, as `nba/eval/lower_tail_eval.collect_stat` + `collect_pi`), predicted for played rows AND DNP rows, integer grid `to_integer_support` for every arm, identical across P(play) arms. DNP-row features: the production state at (player, date) from `prod_state_rows` (gated equal to the forward path) plus the game-level columns of a teammate row of the same game-team plus his position code. The unconditional forecast for a row is the discrete distribution with a point mass 1 - p at 0 and the 199 conditional integer quantile points with weight p/199 each; the label is the stat if he played and 0 otherwise; score = CRPS of that weighted point set (`weighted_crps`, brute-force tested). P(play) arms: `prod` (production raw hurdle), `C`, `C'`, `R`. Primary contrast **R - C'** per stat (isolates the recent-role increment; `R - prod` and `C' - prod` reported, they include the tag and the learner and are NOT credited to recent-role). Floor -0.005 CRPS (house floor), game-clustered CI, 2023 point <= floor and CI upper < 0, 2024 the same with BH p <= 0.05 over the m = 4 stats {pts, reb, ast, fg3m}, separately by season. Live-A0 gate: the refit's played-row CRPS must be within 0.01 of the stored A0 on the same rows (production minutes drifted in 2024 after the 2026-10-11 clean-up; COACH_PHASE T450); if not, Stage 2a is "stopped at its gate" and no verdict is made. Honest note: the CRPS scale here is the whole unconditional distribution including a point mass at 0, so the units differ from the conditional A0 numbers; they are comparable only across P(play) arms.
**2b. injury-Elo: does not apply.** `nba/models/injury_elo.py` adds `b*(V_out_away - V_out_home)/10 + g*(V_doubt_away - V_doubt_home)/10` where a player enters only if the official report lists him OUT (weight 1) or doubtful (constant `doubtful_weight` 0.5). No availability probability for a not-OUT player enters (`_side_sums`, test-checked), so a better P(play) has no input to replace. Had it applied the floor would have been -0.001 game log loss (select 2023, report 2024; the injury-Elo effect on 1,316 holdout games was -0.0102). A different question, "weight doubtful/questionable players by P(play) instead of 0.5/0", is a NEW id after this one.
**MOV-Elo** has no player inputs (team ratings and margin only): there is nothing for P(play) to feed.

## Stage 3 (season 2025; only after Stages 1 and 2a)
Only if Stage 1 passes AND Stage 2a passes for at least one stat. One touch on 2025 for the model that was never evaluated there: the R arm and the Stage 2a contrast on the stats that passed, same fits, same rule; a logged row in `docs/HOLDOUT_ACCESS_LOG.md` written and committed BEFORE the run, containing `RECENT_ROLE`, the first 8 hex of this rule's sha, `PRE-REGISTERED` and `DEVIN CONFIRMED`. The harness (`recent_role_eval.require_holdout_row`) refuses unless that row exists, is in a commit and the file is unmodified. It also refuses without the freeze commit. Devin's explicit confirmation at that time is required; Devin's "run" on 2026-10-12 covers Stages 1 and 2 only. The 2025 frame loader is plumbing added in the commit that follows the row (the frame builders assert `season <= 2024`).

## Data exposure (honest)
Seen: WORKLOAD's cell P and pts results and the adversary's per-column table (2023-24); gate statistics below. Not seen: any R, C' or placebo log loss, any Stage 2 number. Season 2025 never loaded. `nba.duckdb` read-only; `season <= 2024` in SQL and asserted. The five R columns were chosen from the adversary's 2023-24 ranking (disclosed above), which is why the placebo, the shuffle and a hard comparator are required.

## What each choice protects against
* C' with production's own columns: a "win" that only says the comparator lacked a column production can compute (adversary's most likely failure mode).
* Past-only placebo and the working shuffle: capacity and noise; the two WORKLOAD harness defects.
* Floor -0.005 and MDE: statistical significance without use. Select 2023, report 2024: tuning on the report season.
* Stage 2a on the unconditional forecast and the R - C' contrast: crediting the tag or the learner (PPLAY_CONTEXT lesson) and scoring a quantity P(play) cannot move.
* The freeze-commit and holdout-row refusals in the harness: starting from an uncommitted rule; an unlogged holdout touch.

## Kill criteria
* A gate or plant fails after the freeze: stop, fit nothing further, report; the fix is a new id.
* Stage 1 not a pass: closed; Stage 2 does not run. No other windows, columns, interactions or learners afterwards. At most ONE follow-up (new id), only after a pass.

## Minimum-data gates (non-negotiable 8): measured Mon Oct 12 00:30 CDT 2026 on `nba.duckdb` (read-only), seasons 2022-2024, no Cp/R arm fitted
Command: `.venv/bin/python -W ignore -m research.eval.recent_role_eval gate --allow-draft` (22 s; JSON `reports/prereg_recent_role/gate_draft.json`). All PASS.
* A0 stored-component reproduction: max abs diff 0.0, n equal (pts mixture 3.140236 / 3.176840 etc.; stored components used because the rule needs exact A0 numbers for the conditional reference).
* Cell P rows now: 33,093 (2023) / 33,127 (2024), equal to the WORKLOAD run (the 141-row drift the adversary noted is WORKLOAD's gate vs run, not present now).
* Control C refit on the current data reproduces WORKLOAD's stored `p_base`: log loss 0.283202 / 0.281514, abs diff 0.0 in both seasons, n equal. The data clean-up that moved the production MINUTES model (COACH_PHASE) does not touch this path.
* Production-state lookup vs `build_features` on all 84,170 played rows of the real props frame: max abs diff 0.0 over all 14 scalar + 28 stat columns, 0 rows off. Vs the forward placeholder truth on 300 sampled real DNP rows: max abs diff 0.0.
* Missingness of the added columns by realised-minutes bucket (DNP its own): max gap 1.21 pp (`pl_wl_min_last`, the placebo's own NaN rows), limit 2.00 pp (permuted-bucket gap 0.52 pp). Placebo NaN share 1.68% / 1.68%.
* Plants on the real tables: same-game (350 rows identical), control (16,508 rows change), future (16,619 identical), schedule (6,618 identical).
* n per slice, 2023 / 2024 (rows): all 33,093 / 33,127; starter 12,578 / 12,462; bench 20,515 / 20,665; second night 5,222 / 5,519; cold start 1,003 / 742; playoffs 2,246 / 2,363; `wl_min_last < 5` 4,976 / 4,789. All >= 300.
* Stage 2a gates measured at run time (they need the live refit): live-A0 vs stored within 0.01 per stat and season. Feasibility already shown: the lookup builds DNP-row features for all 66,220 rows with 0 rows lacking a teammate template (two-block smoke of the code path, production p in place of the arms, no verdict, nothing written).

## Outputs and the one command
`research/eval/recent_role_eval.py` (Stage 1, freeze and holdout refusals), `research/eval/recent_role_props.py` (Stage 2), `research/recent_role/{prod_state,placebo,gates}.py`, tests in `tests/research/recent_role/`.
Outputs `reports/prereg_recent_role/{gate,results,stage2}.json` and `reports/prereg_recent_role.md`; per-block checkpoints in `data/ops/recent_role_cache/`.
The command (the main session, after the freeze commit; it takes `data/ops/heavy.lock` itself with retry 120 s and removes only its own):
`.venv/bin/python -W ignore -m research.eval.recent_role_eval run --sha <64 hex>`
runs gate -> Stage 1 -> (if pass) Stage 2a -> report. Estimated runtime on the Mac CPU, single process: gate 25 s; Stage 1 arms about 2 min (6 + 2 arms, half a second a block each); Stage 2a about 5 min (18 blocks x 4 stats of a live LightGBM refit, about 4 s a block in the smoke, plus about 1 min to build the frames); bootstrap and report 1 min. `stage3 --sha` refuses without the committed holdout row.

=== RESULTS BELOW ===

(not run)
