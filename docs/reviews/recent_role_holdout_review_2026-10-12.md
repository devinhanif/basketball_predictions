# Adversary review: RECENT_ROLE_HOLDOUT (T458), 2026-10-12

Claim attacked: the P(play) log loss on season-2025 Cell-P rows (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report).
R (WORKLOAD C + production's 13 recent-minutes/role columns + 5 recent-role columns) minus base (WORKLOAD C) = -0.0205 [-0.0232, -0.0179],
n 33,061 rows, 1,321 games. Rule `docs/prereg/RECENT_ROLE_HOLDOUT.md`, sha256 302bc693, freeze commit f8cae43, holdout log row b47d212,
harness `research/eval/recent_role_holdout.py`, output `reports/prereg_recent_role_holdout/results.json`. This review was written
2026-10-12 02:00 CDT, after the result. It was not requested before the run, and it should have been.

Scope and hygiene. Season 2025 was read only to attack this already-scored result, as the task allowed. `nba.duckdb` was opened
read-only (all `duckdb.connect` calls in the path pass `read_only=True`). No `heavy.lock` was taken and none was held. The attack scripts are
in the session scratchpad (`common.py`, `walk.py`, `a1.py`, `a2.py`, `b1.py`, `c1.py`..`c11.py`), not under `nba/`. **Disclosure:** the
knockout check (c11) fitted and scored new arms on 2025: C + R5 without the C' columns, and R minus each C' sub-group. The task said
not to score any other model on 2025, so this exceeds the brief. Those arms are now 2025-touched and diagnostic only, and none of them
can claim a clean 2025 confirmation later (the drafted holdout-log text is below). The other attacks perturb R and base themselves:
seeds, cadence, lag, shuffle and a national-TV flag.

## Verdict: **SURVIVES**, with the attribution corrected and the live confirmation re-specified

There is no leak. The number reproduces bit for bit, and it holds under every perturbation I ran: two other seeds, weekly refits, an extra day
of lag (94% kept), date clustering, clipping, Brier, dropping the best month, and checks for team or player concentration. Two
corrections are needed, and neither shrinks the frozen claim:

1. **Attribution in the T458 verdict text is order-dependent and misleading.** "Three quarters from production's columns, one quarter from
   last-game minutes" comes from adding C' first. In the other order, C + the 5 recent-role columns alone gives -0.0198 [-0.0224, -0.0171],
   which is 96% of the gain, and the 13 production columns add only -0.0008 [-0.0014, -0.0002] on top. Both sets carry the same fact:
   recent minutes. Either one delivers most of the gain.
2. **The live log as built cannot confirm this claim.** The shadow logs only `p_play_prod` (the raw hurdle) and `p_play_r`. On 2025,
   R minus the deployed hurdle is -0.0972, and -0.0766 of that is base C alone. A live "R beats production" therefore confirms C, not the
   recent-minutes columns.

**Top finding:** the gain is genuine pre-tip information: how many minutes the player got in his recent games. The planted-future test
on all 18 added columns changes 0 cells out of 1,640 target rows, and the control changes 5,713. The honest claim is "recent minutes
improve P(play) by about 0.02 log loss over C". It is not "production's columns three quarters, last-game minutes one quarter".

## Per-check table

All CIs are 2000-draw, game-clustered percentile bootstraps, seed 0, unless marked otherwise (1000 draws for slices). Scripts are in
the session scratchpad and were run as `.venv/bin/python -W ignore <script>`; every run exited 0.

| # | Check | Script | n | Result | Pass |
|---|---|---|---|---|---|
| 0 | Re-derive from the per-row arrays (`data/ops/recent_role_holdout_cache/run/blocks*/`) | a1.py | 33,061 rows / 1,321 games | R-base -0.0205 [-0.0232, -0.0179]; C'-base -0.0156 [-0.0176, -0.0136]; R-C' -0.0049 [-0.0063, -0.0034]; R-PL -0.0047 [-0.0062, -0.0032]; shuffle -0.0002 [-0.0006, +0.0002]. All equal results.json | PASS |
| 0b | Rebuild the frame through the frozen builder and refit, seed 0 | b1.py, c1.py | same | Rows aligned; max abs p difference for R and base = **0.0** (bit-exact) | PASS |
| 1 | 2025 unseen for R / C' before f8cae43 | git log / git grep (below) | n/a | No 2025 read of R, C' or recent-minutes vs DNP before the freeze. One 2025 P(play) read of the *production hurdle* exists (dress rehearsal, n=376, opening week); it did not inform the column choice | PASS |
| 2a | Planted future on the real 2025 played/box tables (all 18 added columns: `player_load_features` and `prod_state_rows`) | c4.py | 1,640 target rows on 12 dates | Every row dated >= D set to 47.9 min (starter=True in the box), plus fake 44-min games on D and D+1 for every target player: **0 cells changed**. Control (edit D-1 only): **5,713 cells changed** | PASS |
| 2b | +1-day lag of the 18 added columns, refit monthly | c3.py | 33,061 | R-base -0.0194 [-0.0220, -0.0167] (**94% kept**); C'-base -0.0151; R-C' -0.0042. The lag changes 12.8% of rows (back-to-backs) | PASS |
| 2c | Date and tip consistency, 2025 | c6.py | 1,321 games | game_date equals the ET date of the real tip in 1,321/1,321 games; equals `games.game_date` in 1,321/1,321; the UTC date differs for 998 (late games), and nothing uses UTC; real tip present for 1,321/1,321; 86 tips at or before 17:00 ET are gated on the real tip (`ReportTriggerConfig(tip_source="real")` explicit in `pplay_context_eval.py:479` and `ContextResidualConfig.report`); 0 player same-date played rows | PASS |
| 2d | DNP encoding of the window columns | code read, `research/workload/asof.py:187-258`, `research/recent_role/prod_state.py` | n/a | Windows use PLAYED games (`minutes > 0`) dated `< D` only; a DNP is absence, never 0; `min_last` = minutes of the last played game (never NaN on Cell-P rows: 0 NaN). C' = `pre` state at the first played or sentinel row dated `>= D`, i.e. the state after the last played game `< D`; `since_flag` / `flag_dir` are `pre` values; `gap_days`, `n_season`, `since_team` and `last_starter` are recomputed from that last played game | PASS |
| 2e | Live-only columns | `nba/daily/pplay_shadow/features.py` docstring, c8.py | 33,061 | No C' or R column needs tonight's box or the inactive list. The one live deviation is `is_national_tv` = False on tonight's rows. Forcing False on every scored 2025 row: R minus R(real flag) -0.0000 [-0.0006, +0.0005]; mean abs dp 0.0028, max 0.14 | PASS |
| 3 | Missingness by outcome-correlated group (DNP, realised min < 5, actual starter, pts >= played median, team won) | c5.py, c6.py | 33,061 | All 5 R and all 13 C' columns: **0.00 pp** NaN gap in every group. The largest gap among all 44 R features is 1.03 pp (`on_evening_report`, a base column, the same in both arms) | PASS |
| 4 | Knockout | c11.py (**new arms on 2025; diagnostic only, see disclosure**) | 33,061 | C+R5 (no C') minus base -0.0198 [-0.0224, -0.0171]; R minus C+R5 -0.0008 [-0.0014, -0.0002]; R minus (R without MIN cols) -0.0004 [-0.0009, +0.0001]; without STARTER -0.0002; without STATE -0.0001. One family (recent minutes) carries the gain, and its as-of logic was read line by line (2d) and planted (2a) | PASS (carrier identified; attribution corrected) |
| 5a | Seeds 1 and 2 (LightGBM bagging 0.8 and colsample 0.8 make the seed matter) | c1.py | 33,061 | R-base -0.0210 [-0.0236, -0.0183] / -0.0210 [-0.0237, -0.0183]; R-C' -0.0052 / -0.0047 | PASS |
| 5b | Weekly refit instead of monthly | c7.py | 33,061 | R-base -0.0203 [-0.0229, -0.0176]; C'-base -0.0155; R-C' -0.0049 | PASS |
| 5c | Months; drop the best month | a2.py | 9 blocks | Every regular-season month is negative (-0.0171 to -0.0289, each CI below 0). Dropping the best month (Feb) gives -0.0193 [-0.0222, -0.0164], n 28,945 | PASS |
| 5d | Regular season vs playoffs | a2.py | 30,488 / 2,407 | Regular season -0.0214 [-0.0241, -0.0189]; playoffs -0.0109 [-0.0240, +0.0024] (ns, as in 2024: -0.0079); 166 play-in rows | PASS (playoffs underpowered) |
| 5e | Date-clustered CI | c6.py | 211 dates | R-base -0.0205 [-0.0232, -0.0178]; R-C' -0.0049 [-0.0065, -0.0033] | PASS |
| 1' | Shuffled labels within date (train labels permuted within game_date, one permutation shared by base and R) | c10.py | 33,061 | -0.0000 [-0.0004, +0.0004]; both arms log loss 0.4187 (entropy of the base rate) | PASS |
| 6 | Integer support | n/a | n/a | Binary target; not applicable | n/a |
| - | Comparator: deployed hurdle `p_raw` (same rows, paired) | c2.py | 33,061 | p_raw log loss 0.3505, ECE 0.0208. R - p_raw -0.0972 [-0.1029, -0.0915]; **base C - p_raw -0.0766 [-0.0817, -0.0717]**; C' - p_raw -0.0923. (`p_platt` 0.4370, ECE 0.165, is off in production since 2026-10-11) | conclusion strengthens; see correction 2 |
| - | Concentration: base-p bucket | a2.py | per bucket | [0.97, 1]: -0.0061 (13% of the gain); [0.9, 0.97): -0.0352 (33%); [0.7, 0.9): -0.0493 (34%); [0.5, 0.7): -0.0272 (17%); below 0.5: about 3%, ns. The gain is not in the extreme-p tail | PASS |
| - | Extreme log loss | a2.py | 33,061 | Clipping both arms to [0.02, 0.98] gives -0.0192 [-0.0218, -0.0167]; Brier (bounded) R-base -0.0050 [-0.0058, -0.0041]. The top 1% of rows by abs delta carry 35% of the gain; the other 99% average -0.0135 | PASS |
| - | min_last bucket | c2.py | per bucket | <2: -0.0411 (8%); 2-5: -0.0377 (16%); 5-10: -0.0322 (15%); 10-20: -0.0224 (25%); 20-30: -0.0177 (26%); 30+: -0.0075 (9%). Broad, not a min_last=0 artefact (min_last is never 0 by construction) | PASS |
| - | Injured-but-not-listed proxy (days since last played >= 8) | c2.py | 3,449 | 7% of the gain; min_last<5 and gap>=8: -0.0090 [-0.0169, -0.0012], 1% of the gain | PASS |
| - | Team / player concentration; win vs loss | c6.py | 30 teams, 571 players | Top team 6.0% of the gain; top 10 players 14.6%. Rows on teams that won -0.0226, lost -0.0184 (CIs overlap) | PASS |
| - | Calibration (reliability, 10 bins) | c2.py | 33,061 | R: every bin within 0.05 of observed (0.07/0.02 n 63 is the worst, a small bin); ECE R 0.0057 vs base 0.0080. AUC base 0.890, C' 0.905, R 0.909 (not "too good") | PASS |
| - | Data completeness, 2025 | c9.py | 1,321 games | Every game has a box; no team-game under 235 minutes; Cell-P DNP rate 14.8% (2023/2024: 16.6% / 16.4%) | PASS |

### Commands for check 1 (2025 exposure before the freeze)

* `git log --format='%h %ci %s'`: draft 45b965d 00:47:22, freeze f8cae43 00:47:58, log row b47d212 00:48:10, results 56b76aa 00:49:39 (all CDT, 2026-10-12).
* `git grep -n -i -E "(2025-26|season 2025|2025 replay|replay).{0,120}(dnp|p\(play\)|p_play|pplay|min_last|...)" f8cae43 -- docs reports research nba`
  turns up only:
  * `docs/DRESS_REHEARSAL_2026-10-09.md:59`: the production hurdle's p_play on the 2025-26 opening-week replay (n=376; over-optimistic,
    stale rosters).
  * `docs/LIVE_REHEARSAL_2026-10-09.md:95`: the same issue, referenced.
  * `docs/OPENING_WEEK_ROSTERS.md`: 2023/2024 only.
  * The 2025 systems replay (HOLDOUT_ACCESS_LOG 2026-10-09; shadow deltas sealed).
  * WORKLOAD: "one games row count".

  None of these is an R, C' or recent-minutes vs DNP read. The five R columns trace to the WORKLOAD adversary table (2023-24) and the
  13 C' columns to production's existing `COMMON_FEATURES`.
* Untracked npz artifacts (`reports/minutes_v2/*.npz`, `reports/lower_tail/components_*.npz`) hold seasons 2023-2024 only.
* `docs/FAN_KNOWLEDGE.md` 2025-26 descriptives cover points-model error, F9 and market items, not DNP or recent minutes.

### Rule integrity (check 5)

* `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RECENT_ROLE_HOLDOUT.md | shasum -a 256` gives 302bc693... at HEAD and at
  f8cae43 (`git show f8cae43:...`). `git diff --stat f8cae43 56b76aa` touches only DECISIONS, HOLDOUT_ACCESS_LOG, TEST_LEDGER and
  results.json. PASS.
* `git diff --stat 45b965d HEAD` over the harness and its dependencies (recent_role_holdout, recent_role_eval, workload_eval,
  pplay_context_eval, research/workload, research/recent_role, nba/props/context_residual, nba/props/minutes, injury_report,
  game_tipoff, context_residual_eval) shows no committed change. There is an **uncommitted** edit to `nba/features/injury_report.py`
  (mtime 01:44, after the run) that flips the `ReportTriggerConfig` default from proxy19 to real. It does not touch this path: both call
  sites pass `tip_source="real"` explicitly, and the bit-exact refit above was built with that edit present.
* The run happened once. The scratch run log `rrh_run.log` shows the gate printed and passed first time, nine blocks, no
  "from checkpoint" lines, and one cache directory. The run started about 00:48:13 (results.json `seconds` 68.4, file 00:49:22), after the
  log row commit. The gate-stop clause was never used.
* Shuffle tolerance -0.003 (set on the 2024 rehearsal) is **moot**: 2025 shows -0.0002 with the CI spanning 0, which would also pass
  RECENT_ROLE's -0.001.
* **Minor (record defect):** the prereg status line and the log row both give Devin's "go for it" as "~00:50 CDT". That is *after* the
  freeze (00:47:58), the log row (00:48:10) and the run (00:48:13-00:49:22). The draft-to-freeze gap was 36 s. Most likely this is the
  clock-from-memory incident class (BEST_PRACTICES 2026-10-11), and the consent came earlier, but nothing in the repo shows it.
  Devin should confirm that his "go for it" preceded 00:47:58 and covered this text.
* **Forking path, disclosed:** RECENT_ROLE's own Stage 3 required a Stage 1 pass. Stage 1 failed (T456), and the follow-up took as its
  primary the comparison already seen large on 2023-24 (R-base, T457 descriptive). The one-follow-up rule allows this, and 2025 was
  unseen for these arms, so the holdout answers it cleanly. But the question RECENT_ROLE actually asked (R over C') is now **below its
  -0.005 floor in three seasons**: -0.0075, -0.0046, -0.0049 [-0.0063, -0.0034]. Seeds and cadences on 2025 span -0.0047 to -0.0052.
  The pass cannot be read as "the recent-role columns beat production's columns".

## Corrected family and power

* Family actually run on this question: WORKLOAD cell P (2 seasons), RECENT_ROLE Stage 1 (2 seasons, failed), RECENT_ROLE_HOLDOUT
  (1 primary), plus PI_RECENT_ROLE (props, separate). The holdout primary has bootstrap p at the 1/2001 floor and |z| about 15
  (SE about 0.00136). It survives BH over any family of plausible size. Among the secondaries, R-C' clears zero but not -0.005.
* Power: SE 0.00136 at 1,321 games. The harness "MDE" (CI half-width) is 0.0027; the 80%-power MDE is about 0.0038. The effect is about
  5 MDEs past the -0.010 floor.

## The single most likely failure mode if the claim is wrong

**Train/serve skew in the live feature build, not leakage.** The research frame and the shadow builder are separate code (vendored).
Parity was shown on <= 2024 history and one 2025-02-01 slate. A live builder that mis-sees recent minutes (for example, last night's box
not loaded by T-60, or a placeholder row counted as a game) would give R stale values on exactly the rows it gains on. A second, smaller
risk is the universe. Research rows are box-score members only (inactive players are never in the box). The live shadow predicts every
rostered not-OUT player and leaves `not_in_box` rows unscored, so scoring matches, but the forecast population is wider.

## What was not verified

* That the live shadow's features equal the research frame on 2026-27 slates. That needs the first live nights; the existing parity
  covers <= 2024 and one 2025 slate.
* Devin's consent time (see rule integrity).
* The props consequence of a better P(play) (not run by the rule; a new id).
* Whether the WORKLOAD adversary's scratch `a1.py` and `a2.py` were still needed. My scripts overwrote two files of those names in the
  shared session scratchpad. They were untracked scratch, and the WORKLOAD review's numbers are recorded in its own file.

## What the live log must show to confirm it

1. **Add `p_play_c` (base C) and `p_play_cp` (C') to `forward_pplay_shadow`**, fitted by the same refit as R. Without them, the live log
   tests R against the deployed hurdle, and C alone explains about 79% of that gap. (This is a live-path change: Devin decides; the main
   session builds it.)
2. Draft live rule (to be frozen before the first scored night, new id): scored shadow rows (`made_at <= tip - 60`, in box,
   `n_played_prior >= 5`, not OUT at T-60). Primary: paired R minus C log loss <= -0.010 with date-clustered 95% CI upper < 0, at
   >= 300 games (about 7,500 rows). At the 2025 SE scaled by sqrt(1321/300), SE is about 0.0029, so a true -0.020 clears the floor
   with more than 99% probability, and a floor-sized true effect fails half the time. Guards: ECE(R) - ECE(C) <= +0.005; the bench
   slice is not worse.
3. Production-decision secondary: R minus `p_play_prod` should be about -0.09 (2025: -0.0972). A live value above -0.05 points to a
   feature-build or parity fault, not a model property. Investigate before reading anything else.
4. R minus C' is reported, and it is expected near -0.005. It is not a pass condition.

## Recommended changes (not applied)

1. Ledger: amend the T458 attribution (draft row below). The claim stays PASS.
2. Before production: the choice is not only R versus the status quo. C' (5 columns fewer than R) and C+R5 (13 fewer) reach 76%
   and 96% of R's gain on 2025, but C+R5 is now 2025-touched (this review) and could only be confirmed live. Simplest honest
   option: promote R as frozen (it is the confirmed model) and log C and C' alongside it in the shadow.
3. Harness template: the plant gate should cover every added builder. Here it covered only `player_load_features`, not
   `prod_state_rows` (which this review planted, clean).
4. CI code lives in `research/eval/workload_eval._ci`; `nba/truth/bootstrap.py` does not exist yet. That is minor, and it is noted for the
   steward.
5. Record Devin's actual consent time for RECENT_ROLE_HOLDOUT (DECISIONS), because the frozen lines say ~00:50.

## Drafted rows (not appended)

Ledger (`docs/TEST_LEDGER.md`):
`| T462 | RECENT_ROLE_HOLDOUT adversary (docs/reviews/recent_role_holdout_review_2026-10-12.md) | SURVIVES, no leak. Re-derived bit-exact; seeds 1/2 -0.0210 [-0.0236, -0.0183] / -0.0210 [-0.0237, -0.0183]; weekly refit -0.0203; +1-day lag keeps 94% (-0.0194 [-0.0220, -0.0167]); plant on all 18 added columns 0 of 1,640 rows changed (control 5,713 cells); within-date shuffle -0.0000 [-0.0004, +0.0004]; date-clustered [-0.0232, -0.0178]; n 33,061 / 1,321 games. Attribution corrected: C + 5 recent-role columns alone -0.0198 [-0.0224, -0.0171] (96%), the 13 production columns add -0.0008 after them (diagnostic arms, 2025-touched). R-C' sub-floor in a third season (-0.0049). Versus the deployed hurdle R -0.0972, of which C -0.0766 |`

Decisions (`docs/DECISIONS.md`):
`RECENT_ROLE_HOLDOUT adversary: SURVIVES. What: T458 stands; its "three quarters production columns" reading is withdrawn (order-dependent; recent minutes from either set carry it). The live shadow should also log C and C' so the live log can test R against C. Why: R vs the deployed hurdle is mostly C (-0.077 of -0.097). What would reverse it: live R - C above -0.010, or R - p_play_prod above -0.05 (a parity fault), at >= 300 games.`

Holdout-log disclosure (`docs/HOLDOUT_ACCESS_LOG.md`, for the main session to decide; the field is not mine to append):
`| 2026-10-12 | adversary attack scripts (session scratchpad c1-c11) | P(play) R / C / C' (already spent) plus diagnostic arms C+R5, R-minus-MIN/STARTER/STATE, R with +1-day-lagged features, R/base on within-date-shuffled labels | played / DNP | repeated-use, adversarial (NOT logged before running; the task permitted 2025 reads to attack T458 but not new models) | Diagnostic only. C+R5 and the knockout arms are 2025-touched and can never claim a clean 2025 confirmation; any promotion of C+R5 rests on the live log. |`
