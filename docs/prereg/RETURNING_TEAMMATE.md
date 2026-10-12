# RETURNING_TEAMMATE: does knowing a teammate is back tonight sharpen the props of the players who covered for him? (pre-registration)

Status: **DRAFT (written by Mon Oct 12 13:57 CDT 2026, clock read by `date`), modeler. Not frozen. The minimum-data gate FAILS for the all-rows design and is borderline for the flagged-rows design; the modeler recommends NOT freezing a T-60 history rule (see "Gate verdict and recommendation").** Queue item 3 of DECISIONS 2026-10-12 13:27 CDT (Devin: "lets do everything"). If Devin still wants a rule, the main session commits `prereg(RETURNING_TEAMMATE): <sha256>` BEFORE any arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RETURNING_TEAMMATE.md | shasum -a 256`. Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = the frozen holdout, never loaded).

## The rule in five lines (as it would be frozen; not recommended at T-60)
1. Candidate RT = production `context_residual` stat heads refit walk-forward WITH four as-of columns (`ret_min`, `ret_min_star`, `n_ret`, `ret_own_gain`, below), read out through the stored lower-tail mixture exactly as RECENT_ROLE_FULL's HEADS arm (stored `pi` and `w_q`, production month blocks, production LightGBM, seed 0, no tuning).
2. Comparator A0 = stored production + stored mixture (`reports/lower_tail/components_*.npz`), reproduced to 1e-6 first; FULL (RECENT_ROLE_FULL's stored arm) is a descriptive second comparator only.
3. Metric: integer-support CRPS, pts primary, on the declared pre-tip population "flagged rows" (`ret_min > 0`); RT minus A0, paired, 95% percentile bootstrap clustered by `game_id` (2000 draws, seed 0); floor **-0.005**, CI upper < 0, select 2023, report 2024, both must pass. reb/ast/fg3m secondary, BH over m = 3 within each season, labelled only.
4. Guards: all rows (flagged + unflagged) not worse than A0 by more than +0.001; past-only placebo (RT - PL <= -0.003 on flagged rows, each season); noise-column control and shared-permutation shuffle sized from a permutation null (K >= 50 per season, measured BEFORE the freeze, fail beyond 2 null sd); planted-future, control and missingness gates; slices (n >= 300) not worse by > +0.01.
5. A pass is a CANDIDATE (forking paths below). Stage 2 = the 2026-27 forward log (preferred) or one logged 2025 touch, only on Devin's words. A null is closed; at most one pre-registered follow-up (the T-30 version, below).

## Hypothesis
When a rotation teammate who missed >= 3 straight team games is back tonight, the players who absorbed his minutes give them back, and production (recency windows that are full of absence games) over-forecasts them on the return night. Descriptive evidence (T476, reports/race_tanking_eda_2026-10-12.md; 2023-24; stored A0): rotation teammates (prior-10 >= 10 min) of a 30+ min star returning after >= 3 missed games score -0.81 [-1.06, -0.55] pts vs baseline on return game 1 (n 2,039), -0.52 over games 1-3 (n 6,072); +0.47 [+0.30, +0.65] in games 1-3 of the star's absence (n 6,597); 174 star-seasons; FULL does not close it.

## Forking paths and data exposure (read first)
* The idea, the -0.81 and the "star = 30+ min, out >= 3" definition were all found on 2023-24 (T476), and the same seasons are the select/report pair here. **2023/24 are not clean seasons for this question.** A pass would be a candidate; only the forward log or a single 2025 touch can confirm.
* **Prior T-60 null, same idea (FAN_KNOWLEDGE, 2026-10-10 ~21:20):** the T-60 flag "absent 3+ team games, rotation, not OUT tonight" fired 4,345 times in 2023-24 and 2024-25 but the flagged player played only 16.5% of the time; the star residual under it was -0.16 [-0.49, +0.15], n 3,022 (null). The -0.81 above is POST-HOC (the star did play).
* What this draft changes, and when it was chosen: the gate's first run (Mon Oct 12 ~13:45 CDT) measured the returner's own play rate under two definitions. The brief's literal one (A: not OUT tonight) reproduces the old problem (returner plays 21.7%, n 11,300 returner-games; stars 65.5%). Requiring the returner to be **listed on the latest T-60 snapshot of the team's previous game** (B, the "report-listed absence") and excluding **doubtful** tonight (doubtful returners played 1.0% of 204) raises it to **48.9% overall, 63.1% rotation (pre-absence >= 10 min, n 1,256), 77.0% stars (>= 30 min, n 313)**. Both choices were made after seeing the returner's play rate (not the target stat); disclosed here. No stat outcome under the new columns, no arm, no CRPS of RT has been computed.
* Seen: T476's table, the old T-60 gate, the gate numbers below (returner play rates; feature firing by realised-minutes bucket; outcome-free expected gains; per-row sd/deff of the stored FULL-minus-A0 deltas, already frozen and scored in T467).

## Features (as-of; `research/eval/returning_teammate_gate.py::build_returning`, unit-tested)
For player i of team T in game g; `k` = T's game order in the season (`games.game_date`, real tip `nba.features.game_tipoff`, ties by `game_id`):
* **Returner j:** j played (minutes > 0) for T earlier this season; j played none of T's last >= 3 games before g (streak = T games since j's last played T game); j's last played game anywhere before g's date was for T (traded players drop out); j is listed with any status on the **latest snapshot of `player_availability` (source `nba_official_report`) stamped <= real tip - 60 min of T's previous game**; and j is neither `out` nor `doubtful` on the latest snapshot stamped <= g's real tip - 60 min (the `nba.features.injury_report.usable_report_rows` rule; a game with no usable snapshot has no returner).
* `ret_min` = sum over returners j != i of j's mean minutes over his last 10 played games before g; `ret_min_star` = the same over returners with that mean >= 30; `n_ret` = count; `ret_own_gain` = i's mean minutes in T's last min(5, longest streak) games before g where i played, minus i's mean over his last 40 played games before that window (0 if either is empty or nobody returns).
* Sources: `player_game_stats` (box rows of earlier games only), `games`, `player_availability`; nothing from g's box except the enumeration of which A0 rows exist (they are the stored rows already).
* Missing values: none by construction (0 = nobody returns); the firing rate by realised-minutes bucket is flat (below).

## Arms (2022 = warm-up; production month blocks)
| arm | heads | pi | role |
|---|---|---|---|
| A0 | stored | stored | comparator |
| **RT** | + 4 columns | stored | **the claim** |
| PL | + past-only placebo of the 4 (each row takes the columns of one uniformly drawn strictly earlier row of the same player-season, `research/recent_role/placebo.py`) | stored | placebo guard |
| NOISE | + the 4 columns row-permuted within season, real labels | stored | noise-column control |
| SHUF_CTRL / SHUF_RT | production / + 4 columns, SAME permuted residuals per block | stored | shuffle guard |
| FULL | stored RECENT_ROLE_FULL | stored | descriptive second comparator |
| ORACLE_T30 | + the 4 columns recomputed with "returner played tonight" in place of the T-60 status | stored | descriptive ceiling of the T-30 version (uses tonight's box; never a claim) |

## Pass rule (all must hold, per stat; pts is the claim)
1. 2023 and 2024: RT - A0 on flagged rows <= -0.005, CI upper < 0 (pts); secondary stats BH-adjusted p <= 0.05 for the label "improves".
2. All rows: RT - A0 <= +0.001 (no harm where the columns are 0).
3. RT - PL <= -0.003 on flagged rows, each season.
4. NOISE - A0 and SHUF_RT - SHUF_CTRL within 2 sd of the permutation null measured before the freeze (K >= 50 per season, `research/eval/shuffle_guard_diagnostic.py` pattern; BEST_PRACTICES "Shuffle guards must be sized from a permutation null").
5. Coverage at tau 0.10/0.20/0.80/0.90 no worse than A0 by > 0.01; slices (bench, starters, star returner present, cold start, playoffs; n >= 300) no worse by > +0.01.
6. Gate, plants and missingness pass.

## Why these choices (what each protects against)
* Flagged-rows primary: the columns are 0 on ~69% of rows; an all-rows contrast dilutes a real effect below any floor (gate below). The population is fixed pre-tip by the flag, so it is not selected on the outcome. The all-rows no-harm guard stops a gain bought by perturbing every other row.
* Floor -0.005 and integer support for every arm: the house floor and the T114-T118 support artifact.
* Placebo/noise/shuffle: a GBM given four more columns can gain from capacity or player identity; the permutation-null sizing fixes the RECENT_ROLE_FULL guard failure (T473).
* Select 2023 / report 2024 plus the forking-paths disclosure: protects against tuning inside this rule, NOT against the earlier looks.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 13:53 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.returning_teammate_gate` (read-only `nba.duckdb`, every query `season <= 2024`; 28.5 s; JSON `reports/prereg_returning_teammate/gate.json`). Sizing: per-row sd and design effect from the stored FULL-minus-A0 pts deltas (a heads refit with extra columns moves every row); expected effect outcome-free = E over hypothesised truth G of CRPS(A0) - CRPS(G) on the integer grid (`prereg_gates_common.expected_crps_gain`), where G shifts the A0 mixture by `b = -0.81 x p_play x min(ret_min / 33, 2)` on rotation rows (prior-10 >= 10 min). This model is generous: -0.81 is post-hoc, measured on stars, and assumed linear in returning minutes.
* Attach: the columns exist for 55,320 of 55,320 A0 rows; nulls 0. Flagged share 29.8% (2023, 8,241 rows) / 32.0% (2024, 8,860 rows); star-returner rows with a rotation teammate 1,176 / 1,495.
* Firing by realised minutes (<5, 5-10, 10-20, >=20): 30.7%, 30.1%, 31.0%, 31.0% (max gap 0.9 pp; a pattern tracking tonight's minutes would be a leak; none).
* **All-rows design (floor -0.005):** n 27,619 / 27,701, sd_row 0.340 / 0.330, deff 1.16 / 1.09 (worst of game/date), half-width 0.0043 / 0.0041 (<= 0.005: PASS); expected effect at the star play rate (p 0.77) **-0.0025 / -0.0032** -> power 0.13 / 0.19 per season, **0.02 / 0.03 both seasons: FAIL.** At p = 1 (the flag as good as post-hoc): -0.0042 / -0.0053.
* **Flagged-rows design (floor -0.005):** n 8,241 / 8,860, sd_row 0.333 / 0.325, deff 0.97 / 1.12, **half-width 0.0071 / 0.0072 (> 0.005: FAIL; the pass line becomes delta <= -0.0071)**; expected effect -0.0084 / -0.0099 (p 0.77) -> power 0.64 / 0.77, **both seasons about 0.49**; at the rotation play rate (p 0.63) -0.0057 / -0.0067 -> both seasons about 0.2.
* Secondary stats (flagged rows): half-widths reb 0.0031 / 0.0029, ast 0.0022 / 0.0021, fg3m 0.0014 / 0.0013; no descriptive effect exists for them.
* Unit tests: `tests/research/prereg_gates/test_prereg_gates.py` (planted future: tonight's and later minutes and statuses rewritten, features of g unchanged; planted past: the returner's earlier minutes move `ret_min`; OUT/doubtful/unlisted-previous-game/traded exclusions; the latest-snapshot rule ignores rows stamped after tip - 60).

## Gate verdict and recommendation
**Gate: FAIL** for the all-rows design (power 0.02-0.03); **borderline FAIL** for the flagged-rows design (half-width 0.0071 above the floor; P(pass both seasons) about 0.49 under a generous effect model, about 0.2 under the rotation play rate). Under non-negotiable 8, **recommend not freezing at T-60 on 2023/24.** The information that would make it pass is the T-30 active list: with the returner's status known (p = 1) the flagged-rows effect is -0.014 / -0.016 (power about 0.97 / 0.99 per season), which is the house lesson again (the lever is later information). Proposed instead: add `ret_min`, `ret_min_star`, `n_ret`, `ret_own_gain` (with "active at T-30" in place of the T-60 status) as a descriptive column in the live T-30 arm's log for 2026-27 and pre-register a forward T-30 rule once the collector has a month of real announcement times. That is Devin's call.

## Stage 2 (proposal only, not run)
If a frozen version passes: the 2026-27 forward log (T-60 columns computed live, scored at a pre-declared n) is the clean test; a 2025 touch is possible (no RT model has touched 2025; HOLDOUT_ACCESS_LOG has no RETURNING_TEAMMATE row) but spends the holdout on a borderline-powered idea; the modeler does not recommend it.

## Kill criteria
* A gate, plant or the A0 reproduction fails after the freeze: stop, fit nothing further, report; a fix is a new id.
* Any RT, PL, NOISE or shuffled arm scored before the freeze commit: rule void.
* Fail in either season: closed; no other windows, thresholds or definitions. One follow-up at most (the T-30 version).

## Unstated details chosen before scoring
Streak counts team games including playoffs and the play-in; "played" = minutes > 0; pre-absence minutes over the returner's last 10 played games anywhere (not only for T); the own-gain window is capped at 5 games; the A0 row set (`n_prior >= 5`) defines the population; star threshold 30.0 mpg; date clustering reported beside game clustering (worst deff used for sizing).

=== RESULTS BELOW ===

(not run)
