# TEAMMATE_SITS pre-freeze review (adversary), 2026-11-10

Object: `docs/prereg/TEAMMATE_SITS.md` (DRAFT, body sha256 c148251e..., re-hashed here: `c148251e91ed028632dc1c6fcae86591a6e90396c01dfea47d350368f963851b`, unchanged since merge 886d867e), `research/teammate_sits/{scenario,gate}.py`, `reports/prereg_teammate_sits/{counts,size}.json`, `tests/research/teammate_sits/`.
Model class: gradient-boosted trees (production `props_context_residual` v2 heads, read with one input edited; no refit per arm).
Scope: label-free only. Season 2025 never loaded. No outcome on any 2023-24 scored row was read; every number below comes from `size.json`/`counts.json` or from schedule metadata.

**NOT_READY** (seven MUST edits, all text; one maintainer rerun of the label-free sizing after the revised G2 is written).

Verdict: BLOCKED

Top finding: the half-width bar is not the right G2 for pts, but for a reason the draft does not state. The power criterion already uses the pass line min(floor, -hw), so for HELPS the hw bar adds nothing. The one word a wide CI can wrongly produce is **CLOSED_TIE**, and as drafted CLOSED_TIE does not require the CI to exclude the floor, *for any stat*. Fix CLOSED_TIE and move the hw protection there. That makes pts testable, it is a principled change if it is disclosed, and it tightens every other stat. Second finding: G2's synthetic outcomes are independent across rows, so its design effects carry no sitter-shared noise. BEST_PRACTICES 2026-10-20 (T500) requires that noise. With it, reb's 2024 hw goes from 0.00423 to about 0.00511, which would fail the draft's own bar.

## Checks run

| # | check | command (exact) | n | result | pass/fail |
|---|---|---|---|---|---|
| A | rule hash unchanged since merge | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TEAMMATE_SITS.md \| shasum -a 256` (exit 0); `git diff 886d867e HEAD --stat -- docs/prereg/TEAMMATE_SITS.md research/teammate_sits` (empty) | n/a | c148251e, no diff | PASS |
| B | O1 chosen before G2 output | `git show 8271b697` (DECISIONS 03:04:47); `stat` of worktree `size.json` = 03:05:25 | n/a | O1 (a) was delegated 38 s before the file was written. O1 (a) is the drafter's default, fixed in c07dca57 at 03:03:44. Not verifiable: `_log` prints each stat's hw during the run, so 2023 lines may have been on screen before 03:04:47 | PASS (low residual) |
| C | real-tip coverage on the seasons the rule runs on (no 19:00 proxy) | scratch `tipcov.py` (read_only DuckDB; `build_game_tipoff(max_season=2024)` + `tip_coverage`), exit 0 | 1,318 (2023), 1,320 (2024) games | proxy_fallback 0 / 0 | PASS (the fallback is silent, see S1) |
| D | as-of joins (starter10, min10, v_stat, k) | read `scenario.py:88-125` (`join_asof` on `game_date - 1 day`, backward, played rows only), `gate.py:94-112` (k from last played date + team schedule) | n/a | every event input is knowable before the date | PASS |
| E | report timing | `nba/features/injury_report.py:141-164` (latest snapshot with `as_of <= real tip - 60`, status `out`) | n/a | T-60 on real tips; the latest snapshot replaces earlier ones | PASS |
| F | planted future (code test) | `.venv-1/bin/python -m pytest -q tests/research/teammate_sits -p no:cacheprovider` → 10 passed; **exit 1** comes only from the repo-wide 70% coverage floor on a subset run (0.65% total), not from a test | 10 tests | the planted test plants only the sitter's **pts** on and after the date | PARTIAL (S2) |
| G | missingness asymmetry between arms | read `scenario.py:181-190` and `context_residual.py:536-547` | n/a | both arms require `has_report == 1`. U fills explicit zeros, as production does when a report lists nobody out. No null pattern differs between arms; the post-game `minutes > 0` row filter is shared and disclosed | PASS |
| H | integer support on both arms | `gate.py:328` `to_integer_support` on qs and qu | n/a | both arms are on the grid | PASS |
| I | re-derive G2 from size.json | scratch `g2_rederive.py`, `g2_power_tau.py` (`.venv-1/bin/python -I`), exit 0 | 6,544 rows per stat (2024), 168 sitter-seasons | the drafter's power (2023 fg3m 0.166) reproduces exactly; table below | see below |
| J | lint / types on reviewed code | `.venv-1/bin/ruff check` exit 0; `ruff format --check` exit 0; `mypy research/teammate_sits` exit 0 | 3 files | clean | PASS |

### Re-derived G2 (label-free; 2024 gates, 2023 shown)
I treat the truth as a mixture with weight w on the S world. Expected S - U is linear in w (expected CRPS is linear in the outcome law): gain(w) = null - w·span, where span = null - oracle. Then **hw/span** is how well the test can locate w, and **w@floor** is how far toward S the truth must sit for the floor to be reached. τ is a sitter-season-shared sd on w, added as (τ·span)²/n_sitters (an approximation; the harness must compute it exactly, M1).

| season stat | oracle | null (1 draw) | span | hw (τ=0) | hw/span | w@floor | power τ=0 / τ=1 | hw τ=1 | slope se τ=0 / τ=1 |
|---|---|---|---|---|---|---|---|---|---|
| 2024 pts | -0.0329 | +0.0356 | 0.0684 | 0.0118 | 0.17 | 0.59 | 1.000 / 0.984 | 0.0157 | 0.168 / 0.185 |
| 2024 reb | -0.0091 | +0.0098 | 0.0190 | 0.0042 | 0.22 | 0.78 | 0.973 / 0.939 | **0.0051** | 0.185 / 0.201 |
| 2024 ast | -0.0087 | +0.0119 | 0.0206 | 0.0034 | 0.16 | 0.82 | 0.985 / 0.944 | 0.0046 | 0.182 / 0.198 |
| 2024 fg3m | -0.0027 | +0.0022 | 0.0049 | 0.0015 | 0.31 | **1.47** | 0.002 / 0.004 | 0.0017 | 0.227 / 0.240 |
| 2023 pts | -0.0369 | +0.0498 | 0.0867 | 0.0129 | 0.15 | 0.63 | 1.000 / 0.973 | 0.0186 | 0.163 / 0.181 |
| 2023 reb | -0.0087 | +0.0045 | 0.0131 | 0.0048 | 0.37 | 0.72 | 0.932 / 0.900 | 0.0053 | 0.247 / 0.259 |

What this shows: **measured on the scale-free axis (hw/span), pts is the second-best-resolved stat, not the worst** (0.17; ast 0.16, reb 0.22). It fails hw <= 0.005 only because the floor is one absolute CRPS number for stats whose CRPS scales differ about sixfold. The floor is also much harder for reb and ast (the truth must be 78-82% of the way to S) than for pts (59%). fg3m cannot reach the floor even if S is exactly true (w@floor 1.47), so more data cannot resolve it under this floor. The 2023 pts oracle gain from one simulated draw (-0.0220) is 2.3 se away from the exact value (-0.0369). Single-draw world means are noisy, so G2 should use exact expectations for both worlds (M1).

### The six checks, pre-freeze status
1 shuffled labels: n/a until the run (the harness must permute y within date and show S - U → oracle-free 0; add it as a guard, S4). 2 planted future: partial (F; S2). Lag one extra day: n/a here (the report T-60 is the binding time; S2 adds a post-T-60 report row). 3 missingness: PASS (G). 4 knockout: S - U *is* the knockout of the vacancy group; the T499/T503 lineage says pts may not resolve, which is carried. 5 replication: the 2023 sign condition exists for CRPS; the slope family has none (M3). 6 integer support: PASS (H).

## Answers to the six questions

**(1) hw <= 0.005 versus power.** As drafted, the hw bar does not do the job it is there for. `sizing_row` (research/eval/prereg_gates_common.py:166-168) computes power at the pass line min(floor, -hw), so a stat whose hw exceeds |floor| already gets an effective HELPS floor of -hw, and its power reflects that. For HELPS and HURTS the hw bar is redundant. The decision words are CI-based, so an under-resolved test produces INCONCLUSIVE, not a false HELPS. The one word an open CI *can* produce falsely is CLOSED_TIE (rule line 14: "report point > -0.002 and not HURTS"). For reb at hw 0.0042, a point of -0.0019 has a CI lower bound of -0.0061, beyond the floor, and would still be called a "closed" tie. **Principled revision:** take hw out of G2 and put the protection in the word, with CLOSED_TIE requiring binding CI lower > FLOOR (decided on the run's own CI). Keep oracle <= floor and power >= 0.5, and add the sitter-shared noise term BEST_PRACTICES 2026-10-20 requires (the draft's synthetic outcomes are independent across rows; `gate.py:329-333`). **Is it a goal-seek?** It was motivated by seeing pts fail, so it must be disclosed as such. It survives the test I would apply: no decision word's evidence requirement is loosened, two requirements are tightened (CLOSED_TIE, shared noise), and pts's effective HELPS floor becomes -0.012 to -0.016, stricter than -0.005. The other defensible option is to freeze the draft gate unchanged (pts UNRESOLVED). I recommend against it: under O1 (a) pts would then wait for the live log at n = 36,379 rows (`n_for_hw_at_floor`), about 140 weeks at about 260 event rows a week. That means never, for the rule's headline stat. Disclosure text is in M1.

**(2) O1's declared n.** That number depends on why a stat fails. For FLOOR_UNREACHABLE (fg3m), no n resolves it, so the CRPS question gets no live decision and the slope word is fg3m's verdict. For a power failure (none at present under the revised G2), use the n at which the shared-noise pass-line power reaches 0.8, from the frozen size.json. If that n is above two live seasons (about 13,000 rows), the stat is closed as untestable live. Text in M4.

**(3) Does S vs U test the display?** The mechanism, yes. U is production with the sitter not OUT, which is exactly the display's baseline, and S is exactly the display's scenario (byte-identity tests). Scoring a conditional forecast ("if X sits, Z") on the nights X sat is the right evaluation. **The population, only partly.** The display runs on a night X is expected to play (absence k = 0). At k = 1 the teammates' recency features still reflect games with X, which matches the display. At k = 2-3 they already partly encode his absence, so S - U there measures the vacancy response *on top of* an absence the features have partly absorbed. That is a different object, probably with a smaller delta. About half the rows are k = 1 (3,190 of 6,544 in 2024). Also not covered: QUESTIONABLE/DOUBTFUL players (events are OUT-only, yet the app's most-used case is "will he sit?"), late scratches after T-60, nights with two or more fresh starters out, rest versus injury nights, and the teammate's own P(play). Fix: a display condition on the k = 1 slice and a scope sentence (M5). No change to the primary population: restricting it to k = 1 would push reb's hw to about 0.006.

**(4) Leakage.** Clean on everything I could read (checks C-H). Starter10, min10 and v_stat come from the state after the last *played* game strictly before the date. k comes from the last played date and the team schedule. The report is the latest snapshot at or before real tip - 60, and 2023-24 have 100% real tips. Arms differ only in three vacancy columns. Gaps: `attach_real_tips` falls back to the 19:00 proxy with only a log warning (a guard is needed in the run, S1), and the planted-future test covers only the sitter's pts (S2).

**(5) Multiplicity and the slope secondary.** The primary BH family must be fixed at m = 4 whatever each stat's G2 status, because the family run is four (M2). The slope family is under-specified (M3). "BH over four stats" names no p-value. The words overlap: a CI of [-0.1, 0.8] is both NO_SIGNAL and OVER_REACTS. There is no 2023 sign condition. And it is unclear whether a CRPS G2 fail also silences the slope; it should not, because the table on line 64 already declares the slope its own family with its own bar.

**(6) Season-start fits versus month blocks.** The 2024 sizing heads train on 2022-23 only (about 2 seasons). The run's month blocks add up to about 8 months of 2024, and the 2023 sizing head trains on one season, which the playbook flags. The conformal calibration slice (`cal_frac 0.15`) is also the tail of the prior season, playoffs included, rather than recent in-season rows. The sign of the bias in hw is unknown. More data usually means more vacancy splits, larger |delta|, and a larger oracle gain and per-row sd together. This decides whether a stat is run, not whether a word is wrong (words are CI-based), and reb/ast power holds at τ = 1, so it is a SHOULD: a label-free re-gate on the run's own month-block grids before any outcome is joined, which can only move a stat to UNRESOLVED (S3).

## MUST edits (exact replacement text)

**M1. Section 9, replace the paragraph starting "**G2 sizing (NOT MEASURED; maintainer command).**" and the constants block line `HW_BAR = 0.005` with:**

Constants: delete `HW_BAR = 0.005`; add `POWER_BAR = 0.5`, `SHARED_TAU = 1.0`, `SLOPE_SE_BAR = 0.25` (kept).

> **G2 sizing (MEASURED 2026-11-10 03:05, `reports/prereg_teammate_sits/size.json`; REVISED before freeze, see the disclosure below; exact rerun required before freeze).** Per stat and season, one head per stat is fitted on all rows before the season start (training labels only). Both arms are predicted on the event rows, and sizing is label-free (outcomes from each arm's own integer grid). **CRPS G2**, per stat on 2024: (i) exact oracle expected gain `-mean(expected_crps_gain(U, S))` <= -0.005 (the floor is reachable if S is the truth); (ii) pass-line power >= 0.5. The pass line is min(-0.005, -1.96·se), with se² = se_bind² + (SHARED_TAU · span)² / n_sitter_seasons, where span = exact null expected gain `mean(expected_crps_gain(S, U))` minus the exact oracle gain, and se_bind is the binding cluster se of the oracle-world differences. A stat failing (i) is UNRESOLVED (FLOOR_UNREACHABLE); a stat failing only (ii) is UNRESOLVED (POWER). **Slope G2**, per stat on 2024: sqrt(slope_se² + SHARED_TAU² / n_sitter_seasons) <= 0.25. A stat failing it gets SLOPE_UNRESOLVED; the CRPS and slope gates are independent. The half-width no longer gates a stat. Its job (no tie claimed on an open CI) is done by the CLOSED_TIE requirement in rule line 5, on the run's own CI. Approximate values (adversary, from size.json, single-draw null): 2024 power at τ = 1, pts 0.98, reb 0.94, ast 0.94; fg3m oracle -0.0027 fails (i); slope se pts 0.185, reb 0.201, ast 0.198, fg3m 0.240. The harness's exact values replace these in the freeze commit; whatever they say under this text is the gate.
>
> **G2 revision disclosure (pre-freeze, label-free).** The draft gate (c148251e) also required binding hw <= 0.005. Measured at 03:05 on 2026-11-10: pts failed only that bar (2024 hw 0.0118, power 0.9998); fg3m failed oracle and power; reb and ast passed. This revision was written after those label-free numbers were seen, and the pts result motivated it. Reasons for keeping it: power was already computed at the pass line min(floor, -hw), so hw was redundant for HELPS. The one word an open CI can produce falsely is CLOSED_TIE, now guarded directly. The sitter-shared noise (BEST_PRACTICES 2026-10-20) tightens every stat. No decision word's evidence requirement is loosened, and pts's effective HELPS floor is -hw (about -0.012 to -0.016). Under the draft gate pts would have been UNRESOLVED. Review: `docs/reviews/teammate_sits_prefreeze_2026-11-10.md`.

Code (modeler, `research/teammate_sits/gate.py:329-371`): compute `null_gain = +expected_crps_gain(qs_i, qu_i).mean()` exactly (S - U under U-truth; positive = S worse), the shared-noise se, and the two G2 flags as above; drop `HW_BAR` from `g2_pass`; save the per-row grids (npz) so the run harness and re-gates need no refit. Then the main session runs the section 9 command once under heavy.lock. That run is label-free, and its rule text is committed before it runs.

**M2. Rule line 5, replace the whole line with:**

> 5. **Decision words per stat (first match wins):** **HURTS** = binding CI lower > 0 (the shift makes forecasts worse; reported for every stat, G2 or not). **UNRESOLVED** = the stat failed CRPS G2 (section 9; FLOOR_UNREACHABLE or POWER), descriptive only (O1). **HELPS** = report point <= -0.005, binding CI upper < 0, BH-adjusted p <= 0.05 (BH over the four stats, m = 4 fixed whatever each stat's G2 status; p = the worst-case one-sided p over the three cluster bootstraps), and the 2023 point < 0. **CLOSED_TIE** = report point > -0.002 AND binding CI lower > -0.005 (a floor-sized gain is excluded). **INCONCLUSIVE** = otherwise.

**M3. Rule line 6, replace the whole line with:**

> 6. **Secondary (own family; gated by the slope G2, independent of the CRPS G2):** OLS slope b of `y - mean_U` on `delta = mean_S - mean_U`, with an intercept, per stat on 2024. Binding CI = the widest of the game, teammate-season and sitter-season cluster bootstraps (B = 5000, seed 2026). p_b = the worst-case one-sided bootstrap p for b <= 0; BH over the four stats, q = 0.05, m = 4 fixed. **Words, first match wins:** **SLOPE_UNRESOLVED** = slope G2 failed; **NO_SIGNAL** = BH-adjusted p_b > 0.05, or binding CI lower <= 0, or the 2023 slope point <= 0; **UNDER_REACTS** = CI lower > 1; **OVER_REACTS** = CI upper < 1; **RIGHT_SIZED** = otherwise (CI excludes 0 and contains 1). The slope CI is stated next to every word (at se about 0.2, RIGHT_SIZED spans roughly 0.6-1.4).

**M4. Section 10, replace the O1 bullet with:**

> - **O1 (decided 2026-11-10 03:04:47 by Devin's overnight delegation, before the G2 output was written at 03:05:25; the n is defined by the pre-freeze review):** a CRPS-UNRESOLVED stat is descriptive only on 2023-24 and gets no word from this rule. **FLOOR_UNREACHABLE** (oracle expected gain > -0.005; at present fg3m): no amount of data resolves the CRPS question under this floor, so no live CRPS decision is made, and the stat's slope word is its verdict. **POWER** (none at present): the live log decides once, at n* = the scored teammate-row count at which the section-9 pass-line power (shared-noise se, scaled as 1/sqrt(n)) reaches 0.8, computed from the frozen size.json and written in the results section. The decision is made under a separate live rule committed before the first live event is scored, with the U arm recomputed only from inputs stamped before tip. If n* exceeds 13,000 rows (about two live seasons at about 260 event rows a week), the stat is CLOSED as untestable live. No earlier look.

**M5. Section 2, add after the "Null." bullet:**

> - **What the test covers, and the display condition.** The app shows a scenario on a night the sitter is expected to play (absence k = 0); the test scores nights he was OUT on the T-60 official report. At k = 1 the teammates' recency features still reflect games with him, matching the display. At k = 2-3 they partly reflect his absence already, so S - U there is the vacancy response on top of a partly absorbed absence. **Display condition (no vote on any word):** a stat's scenario is shown as a forecast only if its word is HELPS, its slope word is RIGHT_SIZED or UNDER_REACTS, and the k = 1 slice's point is <= 0; otherwise O2 applies. **Not covered:** QUESTIONABLE/DOUBTFUL players (events are OUT only), scratches after T-60, nights with two or more fresh starters out, and the teammate's own P(play). The app copy says so.

**M6. Section 7, add a guard:**

> - G-TOP5 (BEST_PRACTICES 2026-10-20): per stat, the share of the summed S - U difference carried by the top five sitter-seasons and the top five teammate-seasons is reported next to the word, and a share above 50% is stated in the result line.

**M7. Line 3 (status), replace the first sentence through "BEFORE seeing it.**" with:**

> Status: **DRAFT, written by the modeler 2026-11-10 (branch `agent/modeler-teammate-sits`, base main cb95a2b0); revised per the adversary pre-freeze review `docs/reviews/teammate_sits_prefreeze_2026-11-10.md`. G1 (counts, real data) PASSES. G2 was measured 2026-11-10 03:05 (label-free) and revised before freeze (section 9 disclosure); O1 was fixed by delegation at 03:04:47, before the G2 output was written. No outcome on any 2023-24 scored row has been read.**

(The main session changes DRAFT to FROZEN in the freeze commit, as line 5 of the draft says. Any edit after this review gives a new body hash.)

## SHOULD edits

- **S1** Section 7, add: "- G-TIP: before scoring, `tip_coverage` over the event games must report proxy_fallback == 0, else the run stops (`attach_real_tips`, `nba/features/game_tipoff.py:99-101`, falls back to the 19:00 ET proxy with only a log warning). Measured pre-freeze: 0 of 1,318 (2023) and 0 of 1,320 (2024)."
- **S2** `tests/research/teammate_sits/test_scenario.py:186-203`: also plant the sitter's `minutes` and `starter` on and after the date, and a report row for another teammate stamped tip - 59 min. Assert both arms' vacancy and both arms' feature rows are unchanged.
- **S3** Section 7, add: "- G-REGATE: stage 0 of the run. Before any test-row outcome is joined, recompute the section-9 G2 on the run's own month-block S/U grids (label-free), write `g2_run.json` and its hash to the run log; a stat that fails becomes UNRESOLVED (a stat cannot be promoted by the re-gate)."
- **S4** Section 7, add: "- G-SHUF: y permuted within date (seed 2026); S - U for every stat must lie within the shuffled run's binding CI around its expected value under exchangeability, else the run is invalid." (The paired arms share rows, so a shuffle should give no S advantage beyond the grid shift; this catches a scoring bug.)
- **S5** Section 6, add slices "regular season vs playoffs/play-in (game_id prefix 002 vs 004/005)". The seasons run to June (2023-10-24 to 2024-06-17; 2024-10-22 to 2025-06-22).
- **S6** Section 1 line 7: declare the live confirmation n for the HELPS stats, e.g. "the live check is one look at 6,544 scored teammate rows or the last 2026-27 regular-season game, whichever comes first, under its own committed rule".
- **S7** G-DELTA (line 74): "|delta_mean| > 0" is nearly always true for a tree that splits on vac columns even once (2024 abs p50: pts 0.55, reb 0.14, ast 0.11, fg3m 0.055). Use "|delta_mean| >= 0.05 stat units" or drop the guard.

## Playbook check
- **Information or capacity?** Information already in production (the report OUT set). The question is whether production's *response* to it is right. No new capacity.
- **Enough data for the class?** Trees: moderate appetite. The 2024 heads see about 2 prior seasons plus in-season months; 2023 is about one season (playbook: a 2023-only fail is weak evidence). This is why 2023 is sign-only, which is correct.
- **Test validity:** real tips 100%, T-60, integer support on both arms, paired identical rows, three-way clustered envelope. Defects: the CLOSED_TIE definition (M2), no shared noise in the sizing (M1), and the population versus the display object (M5). A FLOOR_UNREACHABLE fg3m is not a null about fg3m.
- **Lineage and inherited guards:** T476 (k <= 3 window, under-reaction prior), T499 (pts one-season fail), T503 (pts hw 0.0062, not frozen), RETURNING_TEAMMATE_2 (hw 0.00505 vs 0.005, not frozen). The last two were stopped by the same hw bar. Under this revision they would be judged on power plus a CI-based CLOSED_TIE; if either is revived, the revision applies to it in the same way, not selectively.
- **Scored against:** CRPS of the whole distribution on played teammates' rows, not log loss at a market line (T477). A HELPS here does not mean the app's numbers beat a prop price.
- **Class checks (trees):** missingness beyond the as-of cause, PASS (G); planted future, partial (S2); "does the arm only re-express the base", n/a (the arm *is* the base with one input edited).

## Power note and the corrected family
Primary family: four stats, m = 4 fixed. Under the revised G2 (approximate, 2024), pts, reb and ast are resolvable and fg3m is FLOOR_UNREACHABLE. MDE at 2024 n = 6,544 rows (168 sitter-seasons), with shared noise at τ = 1: pts about -0.016, reb about -0.0051, ast -0.005 (floor-bound), fg3m -0.005 (above its own oracle). Secondary family: four slopes, m = 4. With shared noise, slope se is 0.185-0.240, so slope CIs are about ±0.4. "RIGHT_SIZED" is a wide statement and must be shown with its CI.

## Single most likely failure mode if a future HELPS is wrong
The k = 2-3 half of the rows carries the result, while the k = 1 rows, the ones that match the display, tie or reverse. The app would then show "who gains" deltas that the test never validated for a healthy player's night. M5's display condition is the guard.

## Not verified
- Exact shared-noise numbers: mine are an analytic approximation with a single-draw null mean. The M1 rerun replaces them.
- Whether month-block heads change |delta| or hw materially (S3 re-gate).
- Whether the main session saw stat lines on screen before the 03:04:47 O1 line (low stakes: O1 (a) was the drafter's pre-written default).
- Real within-game and within-sitter outcome correlation. It cannot be measured without outcomes; it is covered only by τ.
- The full test suite and the production `flagged_from_availability` tests: I ran only `tests/research/teammate_sits`.

## Drafted rows (not appended)
DECISIONS (draft):
`| 2026-11-10 <time> | TEAMMATE_SITS pre-freeze adversary: NOT_READY (7 MUST, 7 SHOULD). G2 revised before freeze on label-free numbers. The hw bar leaves G2 (redundant with pass-line power) and becomes a CLOSED_TIE requirement (CI lower > floor), with sitter-shared noise tau = 1 added per BEST_PRACTICES 2026-10-20; disclosed as motivated by pts failing hw. O1's n is defined (FLOOR_UNREACHABLE: no live CRPS decision; POWER: n at power 0.8, closed if > 13,000 rows). Display condition on the k = 1 slice. | docs/reviews/teammate_sits_prefreeze_2026-11-10.md | The exact G2 rerun disagrees with the approximation for a stat (the rerun's answer under the revised text governs). |`

Ledger: none (no result).

---

## Re-review (diff edc9662a..5179d07c, branch agent/modeler-teammate-sits-2), 2026-11-10

**NOT_READY**: four small text edits, no rerun. After these edits the rule is ready to freeze without another review round, provided the diff touches only the four lines below.

Verdict: BLOCKED

Top finding: the revision copied my text faithfully, including the sentence I wrote for a state that no longer holds. Section 9 still says "exact rerun required before freeze" and still carries my approximate values, among them "fg3m 0.240" under "all pass". The exact rerun gives fg3m a shared slope se of **0.282, SLOPE_UNRESOLVED**. Frozen as is, the rule would state a fg3m slope pass that its own governing numbers contradict. O1 also tells fg3m that "the stat's slope word is its verdict", which is now empty. Separately, the G-DELTA guard (line 76) still forces "CLOSED_TIE by construction", which contradicts the new first-match words.

### Checks

| check | command | result | pass |
|---|---|---|---|
| body hash | `git show 4022228f:docs/prereg/TEAMMATE_SITS.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256`, same at 5179d07c | a007d9fe34a9e2cd... at both commits | PASS |
| text before rerun | `git log -1 --format=%ci` 4022228f = 03:19:30; worktree `size.json` mtime 03:20:13; 5179d07c = 03:20:23 | revised rule committed before the rerun's output | PASS |
| size.json identity | `shasum -a 256` of `git show 5179d07c:reports/prereg_teammate_sits/size.json` | 5a9a74d0630d4563... | PASS |
| verbatim M1-M7, S1, S3 | scratch script: each `> ` block of this review searched as a substring of the revised file | all 8 quoted blocks found verbatim; constants block updated (HW_BAR gone, POWER_BAR, SHARED_TAU added); G-TIP and G-REGATE present | PASS |
| S2 test | `tests/research/teammate_sits/test_scenario.py` planted test | now plants pts, minutes and starter on and after the date, plus a teammate's report row at tip - 59 (proxy19 in the synthetic league; the real-tip path is covered by G-TIP); asserts S rows, U rows and both vacancies unchanged | PASS |
| gate code = text | read `gate.py::g2_stat` | oracle = -mean(ecg(U,S)); null = mean(ecg(S,U)); span = null - oracle; se_bind = max cluster se (5 draws, variance-averaged); se² = se_bind² + (τ·span)²/n_sit; line = min(FLOOR, -1.96 se); power = pass_power(oracle, line, se); slope se_sh = sqrt(se² + τ²/n_sit); FLOOR_UNREACHABLE checked before POWER. Matches section 9 and the added implementation sentence | PASS |
| null = -oracle identity | derivation below; size.json: oracle + null for all 8 stat-seasons ∈ [-1.4e-17, +4.3e-18]; `CRPS_TAUS` = 199 midpoints (i-½)/N, symmetric (checked) | exact for this estimator | PASS |
| tests / lint / types | worktree 5179d07c, `.venv-1`: `pytest -q tests/research/teammate_sits --no-cov -W ignore` → 11 passed, exit 0 (unpiped); `ruff check` exit 0; `ruff format --check` exit 0; `mypy research/teammate_sits` exit 0 | clean | PASS |

**The identity.** With pinball CRPS on midpoint taus and Y drawn from G's own atoms, write ρ_τ(x) = ½|x| + (τ-½)x. Then A(F,G) := E_{Y~G} CRPS(F,Y) = M(F,G) - c(F), where M(F,G) is the mean over all atom pairs of |g - f| and c(F) = (2/N)Σ(τ_i - ½)f_i (the G term vanishes because Σ(τ_i - ½) = 0). For sorted atoms with τ_i = (i-½)/N, M(F,F) = (2/N²)Σ(2i-N-1)f_i = 2c(F). So oracle + null = [M(S,S) - 2c(S)] - [M(U,U) - 2c(U)] = 0 exactly. It is the energy-distance symmetry; with i/(N+1) taus it would be only approximate. Consequences: span = 2·|oracle|, the zero point is at w = 0.5, and my earlier single-draw null (+0.0356 for 2024 pts) was noise around +0.0329.

**Exact G2, 2024 (gates):** pts PASS (oracle -0.03286, power 0.988, hw 0.0152, shared slope se 0.182); reb PASS (-0.00915, 0.951, 0.0049, 0.210); ast PASS (-0.00871, 0.937, 0.0048, 0.191); fg3m FLOOR_UNREACHABLE (-0.00270, power 0.005) and SLOPE_UNRESOLVED (0.282). n = 6,544 rows and 168 sitter-seasons per stat. Label-free sizing, no CIs. 2023 (not gating): all three PASS; fg3m FLOOR_UNREACHABLE with slope 0.238 (it would pass, but 2024 gates). The 5-draw slope se for fg3m (0.271 before shared noise) is higher than my single-draw 0.227. I accept the exact numbers; this is what the rule says governs.

**The added implementation sentence:** accepted. It describes exactly what `g2_stat` does, it was committed before the rerun, and it is the right design: means exact, only the se uses draws. **Section 10 header** ("Choices (O1 decided by delegation; O2 open for Devin)"): accepted. It is accurate, and O2 is a display choice that does not block a freeze. **S4-S7 not applied:** S4/S5/S6 stay SHOULD. S7 matters only through the G-DELTA contradiction, handled as R2.

### MUST edits (exact replacement text)

**R1. Section 9, in the G2 paragraph:** replace `REVISED before freeze, see the disclosure below; exact rerun required before freeze).` with `REVISED before freeze, see the disclosure below; re-measured exactly at 03:20 under the revised text, which was committed first at 4022228f).` Then replace everything from `Approximate values (adversary, from size.json, single-draw null):` through `whatever they say under this text is the gate.` with:

> Exact values (rerun 2026-11-10 03:20, commit 5179d07c, `size.json` sha256 5a9a74d0630d4563f4cb04017f559a3ef1073c2c3fb75da16ff99815932e3b10), 2024, n = 6,544 rows and 168 sitter-seasons per stat: CRPS G2 pts PASS (oracle -0.03286, power 0.988), reb PASS (-0.00915, 0.951), ast PASS (-0.00871, 0.937), fg3m FLOOR_UNREACHABLE (-0.00270); slope G2 (shared se) pts 0.182, reb 0.210, ast 0.191 PASS, fg3m 0.282 SLOPE_UNRESOLVED. These are the gate. The exact null expected gain equals minus the oracle gain (an identity of this CRPS estimator on midpoint taus), so span = 2·|oracle|.

**R2. Section 7, replace the G-DELTA bullet (line 76) with:**

> - G-DELTA: on 2024, the share of rows with |delta_mean| > 0 is reported per stat, for information. It assigns no word: rule line 5 decides. A stat whose heads ignore vacancy has S = U on every row, so its CI is [0, 0] and line 5 already gives CLOSED_TIE.

**R3. Section 10, O1 bullet:** replace `so no live CRPS decision is made, and the stat's slope word is its verdict.` with:

> so no live CRPS decision is made, and the stat's slope word, if its slope G2 passes, is its verdict. fg3m's slope G2 does not pass (2024 shared slope se 0.282 > 0.25), so this rule gives fg3m no word; its lines are descriptive.

**R4. Section 8:** replace `A CLOSED_TIE on all four stats closes` with `A CLOSED_TIE on every stat that is not UNRESOLVED closes` (otherwise the kill criterion can never fire while fg3m is UNRESOLVED).

### SHOULD
- `size.json` has no provenance block (only `by_season`). Add `made_at`, the git sha, the rule body hash and `data_version` in the next harness touch; until then the freeze commit cites sha 5a9a74d0.
- The section 9 runtime note ("8 LightGBM fits ... 5-15 minutes") is stale (the run took about 35 s); cosmetic.
- S4 (shuffle guard), S5 (playoff slice), S6 (live confirmation n) stand as advised.

### Playbook check (delta)
Unchanged from the first review. The new identity strengthens test validity: the null world is now exact, not one draw. For fg3m, a FLOOR_UNREACHABLE plus SLOPE_UNRESOLVED result is "this rule cannot answer for fg3m", which is not a null about fg3m.

### Drafted DECISIONS row (not appended)
`| 2026-11-10 <time> | TEAMMATE_SITS re-review (diff edc9662a..5179d07c): M1-M7 and S1-S3 verbatim; null = -oracle identity confirmed (exact on midpoint taus); gate code matches text; exact 2024 G2: pts/reb/ast PASS, fg3m FLOOR_UNREACHABLE + SLOPE_UNRESOLVED. NOT_READY on four stale-text edits (R1-R4: section 9 exact values, G-DELTA word, O1 fg3m, kill criterion); freeze after those without another round. | docs/reviews/teammate_sits_prefreeze_2026-11-10.md | A diff beyond R1-R4 before freeze. |`
