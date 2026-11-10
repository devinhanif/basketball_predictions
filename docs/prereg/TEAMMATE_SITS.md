# TEAMMATE_SITS: when a regular starter sits, does production's own vacancy response point the right way and by the right amount? (pre-registration)

Status: **DRAFT, written by the modeler 2026-11-10 (branch `agent/modeler-teammate-sits`, base main cb95a2b0). NOT FROZEN. Gate G1 (counts, real data) PASSES; gate G2 (sizing) is NOT MEASURED: it needs one maintainer command (section 9). Not freezable until G2 is measured and Devin picks O1 (section 10) BEFORE seeing it.** Model class: gradient-boosted trees (production `props_context_residual` v2 LightGBM mean and scale heads plus conformal quantiles); nothing is refitted per arm and no new model exists.
- **Advice or momentum.** Follows Devin's ask (C3, approved 2026-11-09: "if a teammate sits" scenarios for the app's Explore lens). It also follows the 2026-10-14 WOWY decision ("the forecast already accounts for the absence through the team-level vacancy features; a raw gap shown as the headline misleads a bettor"): the scenario uses production's vacancy mechanism, not raw with/without history.
- **How it becomes the rule:** the main session commits `prereg(TEAMMATE_SITS): <sha256>` of everything above `=== RESULTS BELOW ===`, with the status line changed to FROZEN in the same commit. Hash: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TEAMMATE_SITS.md | shasum -a 256`.
- **Season labels** are `games.season`: 2023 = 2023-24 (select), 2024 = 2024-25 (report). 2025 is the frozen holdout and is never loaded; every load caps `season <= 2024`.
- **No outcome has been read.** G1 uses only the played flag (minutes > 0) to define the scored rows, as production scoring does.

## 1. The rule in eight lines
1. **Event** = a team-game in which exactly one *regular starter* (as-of `starter10 >= 0.6`, the decayed starter rate after his latest game before the date) is OUT on the official report used by production (latest snapshot stamped <= real tip - 60 min, `tip_source="real"`), is *counted* by production's `vacated_features` (n_after >= 5, min40 >= 12), and is in absence game **k <= 3** (k = the team's games since his last played game, this one included; k = 1: he played the team's previous game).
2. **Rows** = his teammates in that game who PLAYED with `n_prior >= 5` (production's `stat_frame` rows), same rows for all four stats.
3. **Arms**, same walk-forward fitted heads (month blocks, train = every played row before the block, `configs/context_residual.yaml`), only test-row inputs differ: **S** (scenario = production) = the real report's OUT set; **U** (unchanged, "before the news") = the same OUT set with the sitter removed. On a row only `n_out_rot`, `vac_min`, `vac_<stat>` differ (proved by tests: `tests/research/teammate_sits`).
4. **Primary**, per stat (pts, reb, ast, fg3m), report season 2024: integer-support CRPS (199-point grid, `to_integer_support` on BOTH arms) of **S minus U**, floor **-0.005**, paired; 95% CI = the envelope (widest) of three cluster bootstraps: game, teammate player-season, sitter player-season (B = 5000, seed 2026).
5. **Decision words per stat:** **HELPS** = point <= -0.005, binding CI upper < 0, BH (four stats, worst-case one-sided p, q = 0.05) <= 0.05, and the 2023 point < 0. **HURTS** = binding CI lower > 0 (the shift makes forecasts worse). **CLOSED_TIE** = report point > -0.002 and not HURTS. **INCONCLUSIVE** = otherwise. **UNRESOLVED** = the stat failed G2 on 2024 (then descriptive only; O1).
6. **Secondary (own family, BH over four stats), the right-size check the app needs:** OLS of `y - mean_U` on `delta = mean_S - mean_U` with an intercept, 2024, same envelope CI. **RIGHT_SIZED** = CI contains 1 and excludes 0; **UNDER_REACTS** = CI lower > 1; **OVER_REACTS** = 0 < CI upper < 1; **NO_SIGNAL** = CI contains 0.
7. Select 2023 / report 2024. 2023 is used only for the sign condition. **No 2025 touch is earned or requested.** The live 2026-27 log is the confirming venue.
8. One run, no reruns, no variants (k, the starter threshold and the floor are fixed here). A new question gets a new id.

Constants (mirrored in `research/teammate_sits/gate.py`; the run harness must mirror them):
```
FLOOR = -0.005
CLOSE_AT = -0.002
HW_BAR = 0.005
SLOPE_SE_BAR = 0.25
REGULAR_STARTER10 = 0.6
MAX_K = 3
N_BOOT = 5000
SEED = 2026
SELECT = 2023
REPORT = 2024
BH_Q = 0.05
```

## 2. Hypothesis, null, what a result would mean
- **H1.** Production's props heads respond to a teammate being OUT only through three team-level vacancy sums. The app's scenario "if P sits" is exactly that response. If the response is informative, S beats U on the teammates' rows when a starter really sits, and the shift is of about the right size (slope near 1).
- **Expected direction of the slope (prior, not evidence):** T476 found star-out games 1-3 still under-forecast by +0.47 pts [+0.30, +0.65] after FULL (174 star-seasons, uncorrected, many slices): production may UNDER_REACT.
- **Null.** The heads barely use the vacancy columns (delta near 0, a tie), or they use them in the wrong amount (HURTS, OVER_REACTS).
- **If HELPS and RIGHT_SIZED/UNDER_REACTS:** the app may show "who gains" for that stat as the model's estimate, with the slope stated if under. **If CLOSED_TIE / NO_SIGNAL:** the app should not present the deltas as forecasts (modeler's advice: show direction only, or not at all; Devin's call). **If HURTS / OVER_REACTS:** the scenario is not shown for that stat, and the production vacancy features become a question of their own (new id).

## 3. Lineage (stated first)
**New in information: no** (the report's OUT set is already production's input). **New in construction: no** (it is production read with one input edited). What is new is the *question*: whether the response the app would display is right. Expected: a direction-confirming result for the big-scale stats and an under-reaction, with points the hardest to resolve.

| ancestor | numbers (ledger) | carried / dropped |
|---|---|---|
| T092-T095 (production context residual, vac_* included) | CRPS vs recency pts -0.0862 ... | the heads, the features, the conformal layer |
| T476 teammate-absence EDA | star out g1-3 +0.47 [+0.30, +0.65]; back g1 -0.81 | the k <= 3 window; the slope prior |
| WOWY_REDISTRIBUTION (not frozen, 2026-10-14) | power 0.07 on VAC24 | dropped the with/without history; production's mechanism instead |
| WOWY_STRUCT T499 | pts leader out 2023 -0.0096 [-0.0170, -0.0021], 2024 -0.0037 [-0.0097, +0.0023] | a structural refinement failed one season; this rule asks about production's own response, which T499 used as its baseline |
| QUESTIONABLE_CASCADE T503 (not frozen) | pts TM half-width 0.00620 at 11,181 rows (2024) | the same production-OUT tie (0 of 6,253 here too); its warning that pts may not resolve |
| RETURNING_TEAMMATE_2 (not frozen) | pooled half-width 0.00505 vs 0.005 | the reverse event (return); not re-asked |

## 4. Arms, exactly
Reference code: `research/teammate_sits/scenario.py`. `scenario_vacancy(..., remove=True)` recomputes `vacated_features` for the game with the sitter removed (one batched call over keyed games); `scenario_features` replaces the three vacancy columns on the sitter's teammates' rows and fills a reported team-game with nobody rotation-OUT with zeros (production semantics). Tests prove S-rows equal `build_features` with the real report and U-rows equal `build_features` without the sitter, byte for byte, that the opponent's rows never change, and that the sitter's stats on or after the date never reach either arm (planted future).

## 5. Metric, CI, multiplicity, and what each protects against
| choice | protects against |
|---|---|
| paired S - U on identical rows and fitted heads | refit noise and population differences masquerading as an effect |
| integer support for every arm and stat | the continuous-vs-grid artifact (T114-T118) |
| floor -0.005 | a p-value-only win too small to matter |
| envelope of game, teammate-season, sitter-season clusters | anti-conservative row CIs; one sitter's many events or one game's many teammates dominating |
| BH over four stats | the multiple-stat family |
| 2023 sign condition | a report-season fluke |
| slope family separate from CRPS | conflating "points the right way" with "right size" |
| exactly one sitter per event, k <= 3 | ambiguous attribution when two starters sit; stale states in long absences |
| played teammates only | the U/S contrast cannot touch P(play), so DNP rows add nothing but noise; disclosed as a limit (the scenario does not model a teammate's P(play)) |

## 6. Slices (descriptive, declared, no vote; n >= 300 to be shown)
k = 1 vs k = 2-3; sitter `min10` >= 30 vs < 30; teammate `starter10` >= 0.5 vs < 0.5; teammate `pos_code` equal to the sitter's vs not.

## 7. Guards
- G-EQ: the S arm's feature rows equal `build_features` with the real report (tested; re-asserted in the run on every block).
- G-DET: two runs of the first block give identical grids.
- G-DELTA: on 2024, the share of rows with |delta_mean| > 0 is reported; if under 50% for a stat, that stat's word is CLOSED_TIE by construction (the heads ignore vacancy there) and the CRPS line is shown for information.
- Season-cap assertion before and after loads (`season <= 2024`).

## 8. Kill criteria
HURTS on pts or OVER_REACTS on pts closes the "who gains points" display. A CLOSED_TIE on all four stats closes the scenario as a forecast (it may still be shown as "what the model's inputs do", labelled, Devin's call). No follow-up variant on this data; one pre-registered live follow-up at most.

## 9. Minimum-data gate (non-negotiable 8), measured on the data it will run on
Data: `nba.duckdb` read-only from the main checkout, newest manifest `data_version` ec40abfa83e5 (2026-11-09 12:10Z; the DB file is newer, from live-season ingest; seasons <= 2024 not re-fingerprinted per season). Output `reports/prereg_teammate_sits/counts.json` (sha256 3673a6bf; it embeds a made_at stamp, so a rerun changes the hash, not the counts), `python -W ignore -m research.teammate_sits.gate counts --data-root <main> --out ...` (1 s).

**G1 counts (PASS).** Bars: per season >= 400 events, >= 4,000 teammate rows, >= 100 sitter player-seasons, >= 20 weeks, production tie exact.

| | 2023 (select) | 2024 (report) |
|---|---|---|
| events (team-games), k <= 3, one sitter | 569 | 629 |
| of which k = 1 | 271 | 306 |
| games | 502 | 551 |
| sitter player-seasons | 159 | 168 |
| teammate rows (each stat) | 5,872 | 6,544 |
| teammate rows k = 1 | 2,823 | 3,190 |
| teammate player-seasons | 526 | 528 |
| ISO weeks | 30 | 29 |
| regular-starter OUT rows, all k (k1 / k2 / k3 / 4-10 / >10) | 2,422 (406 / 241 / 186 / 743 / 846) | 2,779 (556 / 286 / 226 / 801 / 910) |
| fresh team-games with 2+ sitters (excluded) | 121 | 185 |

Production tie: my event table's per-team sums reproduce `vacated_features` on all 6,253 reported team-games (0 count mismatches, 0 `vac_min` mismatches above 1e-9).

**G2 sizing (NOT MEASURED; maintainer command).** Per stat and season: fit one head per stat on all rows before the season start (training labels only), predict both arms on the event rows, and size label-free (outcomes drawn from each arm's own integer grid). Bars, on 2024 per stat: binding half-width <= 0.005; oracle expected gain (if S were the truth) <= -0.005, i.e. the floor is reachable; pass-line power >= 0.5; slope se <= 0.25 (from the S-grid sd and the delta spread). A stat failing on 2024 is UNRESOLVED (O1). Rough expectation from QUESTIONABLE_CASCADE's per-row sd scaled to these n (not a measurement): pts about 0.008 (fails), reb about 0.004, ast 0.003, fg3m 0.002; the sd here should be larger because a whole starter's vacancy moves (not a coin flip), and so should the effect.

```
mkdir data/ops/heavy.lock && (python -W ignore -m research.teammate_sits.gate size --data-root /Users/devin/Downloads/nba-prediction --out reports/prereg_teammate_sits/size.json; rmdir data/ops/heavy.lock)
```
Run from the branch worktree (it reads `configs/` relatively); 8 LightGBM fits plus one feature build, expect 5-15 minutes and about 2-3 GB; it prints progress per stat.

**Capture audit (before any G2 fail is declared).** 2022-23 is in the DB but has no prior season for walk-forward heads (F1: 2023 gains were data poverty); 2019-21 history has different report cadence (HISTORY_WINDOW T315) and no production-mechanism heads; the excluded 2+-sitter team-games (121 / 185) and k 4-10 rows (743 / 801 sitter rows) are population choices, not data gaps. Repair cost for more pts n: none cheap; the live log adds about 25 events a week.

## 10. Open choices for Devin (decide before G2 is run)
- **O1 (if pts fails G2 on 2024):** (a) pts UNRESOLVED, descriptive only, decided by the live log at a declared n (modeler's default); (b) one pooled 2023+2024 test for pts with its own pooled gate (12,416 rows), a pass reading "consistent", the live log confirming.
- **O2 (display):** whether a CLOSED_TIE stat's deltas are shown at all (modeler: no numbers; direction only, labelled).

## 11. Not earned
No 2025 touch. No production change: nightly wiring (nba/daily, nba/export) is a later branch, after this check, with the replay oracle and Devin's go.

=== RESULTS BELOW ===
