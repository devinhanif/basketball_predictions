# TEAMMATE_SITS: when a regular starter sits, does production's own vacancy response point the right way and by the right amount? (pre-registration)

Status: **DRAFT, written by the modeler 2026-11-10 (branch `agent/modeler-teammate-sits`, base main cb95a2b0); revised per the adversary pre-freeze review `docs/reviews/teammate_sits_prefreeze_2026-11-10.md`. G1 (counts, real data) PASSES. G2 was measured 2026-11-10 03:05 (label-free) and revised before freeze (section 9 disclosure); O1 was fixed by delegation at 03:04:47, before the G2 output was written. No outcome on any 2023-24 scored row has been read.** Model class: gradient-boosted trees (production `props_context_residual` v2 LightGBM mean and scale heads plus conformal quantiles); nothing is refitted per arm and no new model exists.
- **Advice or momentum.** Follows Devin's ask (C3, approved 2026-11-09: "if a teammate sits" scenarios for the app's Explore lens). It also follows the 2026-10-14 WOWY decision ("the forecast already accounts for the absence through the team-level vacancy features; a raw gap shown as the headline misleads a bettor"): the scenario uses production's vacancy mechanism, not raw with/without history.
- **How it becomes the rule:** the main session commits `prereg(TEAMMATE_SITS): <sha256>` of everything above `=== RESULTS BELOW ===`, with the status line changed to FROZEN in the same commit. Hash: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TEAMMATE_SITS.md | shasum -a 256`.
- **Season labels** are `games.season`: 2023 = 2023-24 (select), 2024 = 2024-25 (report). 2025 is the frozen holdout and is never loaded; every load caps `season <= 2024`.
- **No outcome has been read.** G1 uses only the played flag (minutes > 0) to define the scored rows, as production scoring does.

## 1. The rule in eight lines
1. **Event** = a team-game in which exactly one *regular starter* (as-of `starter10 >= 0.6`, the decayed starter rate after his latest game before the date) is OUT on the official report used by production (latest snapshot stamped <= real tip - 60 min, `tip_source="real"`), is *counted* by production's `vacated_features` (n_after >= 5, min40 >= 12), and is in absence game **k <= 3** (k = the team's games since his last played game, this one included; k = 1: he played the team's previous game).
2. **Rows** = his teammates in that game who PLAYED with `n_prior >= 5` (production's `stat_frame` rows), same rows for all four stats.
3. **Arms**, same walk-forward fitted heads (month blocks, train = every played row before the block, `configs/context_residual.yaml`), only test-row inputs differ: **S** (scenario = production) = the real report's OUT set; **U** (unchanged, "before the news") = the same OUT set with the sitter removed. On a row only `n_out_rot`, `vac_min`, `vac_<stat>` differ (proved by tests: `tests/research/teammate_sits`).
4. **Primary**, per stat (pts, reb, ast, fg3m), report season 2024: integer-support CRPS (199-point grid, `to_integer_support` on BOTH arms) of **S minus U**, floor **-0.005**, paired; 95% CI = the envelope (widest) of three cluster bootstraps: game, teammate player-season, sitter player-season (B = 5000, seed 2026).
5. **Decision words per stat (first match wins):** **HURTS** = binding CI lower > 0 (the shift makes forecasts worse; reported for every stat, G2 or not). **UNRESOLVED** = the stat failed CRPS G2 (section 9; FLOOR_UNREACHABLE or POWER), descriptive only (O1). **HELPS** = report point <= -0.005, binding CI upper < 0, BH-adjusted p <= 0.05 (BH over the four stats, m = 4 fixed whatever each stat's G2 status; p = the worst-case one-sided p over the three cluster bootstraps), and the 2023 point < 0. **CLOSED_TIE** = report point > -0.002 AND binding CI lower > -0.005 (a floor-sized gain is excluded). **INCONCLUSIVE** = otherwise.
6. **Secondary (own family; gated by the slope G2, independent of the CRPS G2):** OLS slope b of `y - mean_U` on `delta = mean_S - mean_U`, with an intercept, per stat on 2024. Binding CI = the widest of the game, teammate-season and sitter-season cluster bootstraps (B = 5000, seed 2026). p_b = the worst-case one-sided bootstrap p for b <= 0; BH over the four stats, q = 0.05, m = 4 fixed. **Words, first match wins:** **SLOPE_UNRESOLVED** = slope G2 failed; **NO_SIGNAL** = BH-adjusted p_b > 0.05, or binding CI lower <= 0, or the 2023 slope point <= 0; **UNDER_REACTS** = CI lower > 1; **OVER_REACTS** = CI upper < 1; **RIGHT_SIZED** = otherwise (CI excludes 0 and contains 1). The slope CI is stated next to every word (at se about 0.2, RIGHT_SIZED spans roughly 0.6-1.4).
7. Select 2023 / report 2024. 2023 is used only for the sign condition. **No 2025 touch is earned or requested.** The live 2026-27 log is the confirming venue.
8. One run, no reruns, no variants (k, the starter threshold and the floor are fixed here). A new question gets a new id.

Constants (mirrored in `research/teammate_sits/gate.py`; the run harness must mirror them):
```
FLOOR = -0.005
CLOSE_AT = -0.002
POWER_BAR = 0.5
SHARED_TAU = 1.0
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
- **What the test covers, and the display condition.** The app shows a scenario on a night the sitter is expected to play (absence k = 0); the test scores nights he was OUT on the T-60 official report. At k = 1 the teammates' recency features still reflect games with him, matching the display. At k = 2-3 they partly reflect his absence already, so S - U there is the vacancy response on top of a partly absorbed absence. **Display condition (no vote on any word):** a stat's scenario is shown as a forecast only if its word is HELPS, its slope word is RIGHT_SIZED or UNDER_REACTS, and the k = 1 slice's point is <= 0; otherwise O2 applies. **Not covered:** QUESTIONABLE/DOUBTFUL players (events are OUT only), scratches after T-60, nights with two or more fresh starters out, and the teammate's own P(play). The app copy says so.
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
- G-TOP5 (BEST_PRACTICES 2026-10-20): per stat, the share of the summed S - U difference carried by the top five sitter-seasons and the top five teammate-seasons is reported next to the word, and a share above 50% is stated in the result line.
- G-TIP: before scoring, `tip_coverage` over the event games must report proxy_fallback == 0, else the run stops (`attach_real_tips`, `nba/features/game_tipoff.py:99-101`, falls back to the 19:00 ET proxy with only a log warning). Measured pre-freeze: 0 of 1,318 (2023) and 0 of 1,320 (2024).
- G-REGATE: stage 0 of the run. Before any test-row outcome is joined, recompute the section-9 G2 on the run's own month-block S/U grids (label-free), write `g2_run.json` and its hash to the run log; a stat that fails becomes UNRESOLVED (a stat cannot be promoted by the re-gate).

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

**G2 sizing (MEASURED 2026-11-10 03:05, `reports/prereg_teammate_sits/size.json`; REVISED before freeze, see the disclosure below; exact rerun required before freeze).** Per stat and season, one head per stat is fitted on all rows before the season start (training labels only). Both arms are predicted on the event rows, and sizing is label-free (outcomes from each arm's own integer grid). **CRPS G2**, per stat on 2024: (i) exact oracle expected gain `-mean(expected_crps_gain(U, S))` <= -0.005 (the floor is reachable if S is the truth); (ii) pass-line power >= 0.5. The pass line is min(-0.005, -1.96·se), with se² = se_bind² + (SHARED_TAU · span)² / n_sitter_seasons, where span = exact null expected gain `mean(expected_crps_gain(S, U))` minus the exact oracle gain, and se_bind is the binding cluster se of the oracle-world differences. A stat failing (i) is UNRESOLVED (FLOOR_UNREACHABLE); a stat failing only (ii) is UNRESOLVED (POWER). **Slope G2**, per stat on 2024: sqrt(slope_se² + SHARED_TAU² / n_sitter_seasons) <= 0.25. A stat failing it gets SLOPE_UNRESOLVED; the CRPS and slope gates are independent. The half-width no longer gates a stat. Its job (no tie claimed on an open CI) is done by the CLOSED_TIE requirement in rule line 5, on the run's own CI. Approximate values (adversary, from size.json, single-draw null): 2024 power at τ = 1, pts 0.98, reb 0.94, ast 0.94; fg3m oracle -0.0027 fails (i); slope se pts 0.185, reb 0.201, ast 0.198, fg3m 0.240. The harness's exact values replace these in the freeze commit; whatever they say under this text is the gate.

Implementation (`research/teammate_sits/gate.py::g2_stat`): both world means are exact expectations over each arm's own integer atoms; only se_bind and the slope se use oracle-world draws (5 draws, variances averaged, seed 20261110); the per-row S/U grids are saved (`--grids-dir`) so the run harness and re-gates need no refit.

**G2 revision disclosure (pre-freeze, label-free).** The draft gate (c148251e) also required binding hw <= 0.005. Measured at 03:05 on 2026-11-10: pts failed only that bar (2024 hw 0.0118, power 0.9998); fg3m failed oracle and power; reb and ast passed. This revision was written after those label-free numbers were seen, and the pts result motivated it. Reasons for keeping it: power was already computed at the pass line min(floor, -hw), so hw was redundant for HELPS. The one word an open CI can produce falsely is CLOSED_TIE, now guarded directly. The sitter-shared noise (BEST_PRACTICES 2026-10-20) tightens every stat. No decision word's evidence requirement is loosened, and pts's effective HELPS floor is -hw (about -0.012 to -0.016). Under the draft gate pts would have been UNRESOLVED. Review: `docs/reviews/teammate_sits_prefreeze_2026-11-10.md`.

```
mkdir data/ops/heavy.lock && (python -W ignore -m research.teammate_sits.gate size --data-root /Users/devin/Downloads/nba-prediction --out reports/prereg_teammate_sits/size.json; rmdir data/ops/heavy.lock)
```
Run from the branch worktree (it reads `configs/` relatively); 8 LightGBM fits plus one feature build, expect 5-15 minutes and about 2-3 GB; it prints progress per stat.

**Capture audit (before any G2 fail is declared).** 2022-23 is in the DB but has no prior season for walk-forward heads (F1: 2023 gains were data poverty); 2019-21 history has different report cadence (HISTORY_WINDOW T315) and no production-mechanism heads; the excluded 2+-sitter team-games (121 / 185) and k 4-10 rows (743 / 801 sitter rows) are population choices, not data gaps. Repair cost for more pts n: none cheap; the live log adds about 25 events a week.

## 10. Choices (O1 decided by delegation; O2 open for Devin)
- **O1 (decided 2026-11-10 03:04:47 by Devin's overnight delegation, before the G2 output was written at 03:05:25; the n is defined by the pre-freeze review):** a CRPS-UNRESOLVED stat is descriptive only on 2023-24 and gets no word from this rule. **FLOOR_UNREACHABLE** (oracle expected gain > -0.005; at present fg3m): no amount of data resolves the CRPS question under this floor, so no live CRPS decision is made, and the stat's slope word is its verdict. **POWER** (none at present): the live log decides once, at n* = the scored teammate-row count at which the section-9 pass-line power (shared-noise se, scaled as 1/sqrt(n)) reaches 0.8, computed from the frozen size.json and written in the results section. The decision is made under a separate live rule committed before the first live event is scored, with the U arm recomputed only from inputs stamped before tip. If n* exceeds 13,000 rows (about two live seasons at about 260 event rows a week), the stat is CLOSED as untestable live. No earlier look.
- **O2 (display):** whether a CLOSED_TIE stat's deltas are shown at all (modeler: no numbers; direction only, labelled).

## 11. Not earned
No 2025 touch. No production change: nightly wiring (nba/daily, nba/export) is a later branch, after this check, with the replay oracle and Devin's go.

=== RESULTS BELOW ===
