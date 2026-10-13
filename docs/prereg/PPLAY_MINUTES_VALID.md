# PPLAY_MINUTES_VALID: a valid test of production's P(play)/minutes model, head-to-head with P(play) R (pre-registration)

Status: **DRAFT (written Mon Oct 12 17:20 CDT 2026), modeler. STOPPED AT THE MINIMUM-DATA GATE: after the 2026-10-12 backfill (cache 20:35, fetch 21:33 CDT) checks 1-14 pass except one open point in check 13: 2 rows of one 2019 player (id 1626218, stats.nba.com returns an empty record) stay NULL, both non-playing. Freezable once Devin rules on those 2 rows (proposed: exclude them from training, stated here before any fit).** Devin asked for this redo ("we are redoing the production p plays/minutes model", DECISIONS 2026-10-12 16:27; "make sure all 3 go back to 2019 2020", 16:28). The main session reads it with Devin and commits `prereg(PPLAY_MINUTES_VALID): <sha256>` before any fit. Everything above `=== RESULTS BELOW ===` is the rule; freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PPLAY_MINUTES_VALID.md | shasum -a 256`.
Season labels: 2019 = 2019-20 ... 2025 = 2025-26 (frozen holdout). No season-2025 row was read for this draft. Every query filtered `season <= 2024` in SQL; no lock was taken.

## Why this test exists (and what it is not)
The validity audit (docs/reviews/validity_audit_2026-10-12.md) lists T043-T046 as the deployed P(play)/minutes hurdle's only evidence: no bounds, calibration split likely including 2025. **Lineage check for this draft found that T043-T046 never described the served model.** T043-T046 measured "Option 1A" (commit 8b79ad3: ridge-learned game-context adjustment, cold-start-gated); that path is `use_learned_game_context=False` by default, no config sets it, and no caller outside `nba/props/minutes.py` fits it (`fit_learned_game_context` has no consumer). What production serves (`nba/props/forward.py:predict_slate`) is the plain empirical-Bayes hurdle of commit 0f4a8f7 (2026-10-05): P(play) = trailing-150-team-game play rate shrunk to 0.82 with pseudo-count 8, minutes given play = Normal(mu, sigma) truncated to [0, 48] with mu, sigma shrunk to 24 / 8 (pseudo-count 6), game context off, Platt map off since 2026-10-11 (`p_play == p_play_raw`). It has **no fitted parameter**: the constants are hand-set defaults, unchanged since their first commit. The only fitted piece it ever had, `P_PLAY_PLATT = (-0.327, 0.581)` (commit 8db734f, 2026-10-08, fit in-sample), is off.
So the honest redo is: (1) measure the served function exactly, (2) give it the one fitted layer it could fairly have, a walk-forward Platt map that never sees the scored rows, and (3) score both against R on the same rows with clustered CIs. This is a measurement of a deployed component, not a discovery: the direction is already known on 2023-24 (MINUTES_V2 secondary, PPLAY_CONTEXT T316-T317, PPLAY_INTO_PROPS_LIVE rehearsal) and on 2025 (T462 diagnostic, R minus served p -0.0972). Its value is a valid, bounded ledger row for the component, the size of the calibration share, and the history-window effect for R.

## The rule in seven lines
1. **Frame U**: seasons 2019-2024; box, games, schedule (real tips), official reports and era flags from `data/history/nba_history.duckdb` (2019-2021) and `nba.duckdb` (2022-2024), both `read_only=True`, `season <= 2024` in SQL, unioned into one in-memory scratch DB so every trailing window spans history exactly as live serving does with full history. 2018-19 excluded (partial report archive). Population = Cell-P rows (rostered, `n_played_prior >= 5`, not OUT on the latest report snapshot at or before real tip - 60 min; 2021-22: tip - 180 min, rule 4).
2. **Arms** (same rows, paired): **P0** = production as served (`build_minutes_features(U, use_game_context=False)` -> `predict_minutes` with `MinutesModelConfig()` defaults; p = raw hurdle p; minutes given play = its truncated Normal). **P0c** = P0 with a walk-forward Platt map `sigmoid(a_b + b_b * logit(clip(p0, 1e-3, 1-1e-3)))`, (a_b, b_b) by unpenalised logistic regression on every Cell-P row dated strictly before block b from 2019-20 on, excluding era-flagged games (bubble, no-fans, limited-fans). **R19** = P(play) R's frozen columns (`research.eval.recent_role_eval.arm_columns()["R"]`) plus four era channels `era_bubble, era_nofans, era_limited, era_report_dropped`, LightGBM binary with RECENT_ROLE's parameters, seed 0, no tuning, refit before every calendar month on every row dated strictly before it from 2019-20 on.
3. **Primary per season (2023 and 2024)**: P(play) log loss and Brier, paired, 95% game-clustered percentile bootstrap (2,000 draws, seed 0). Contrasts: **D1 = R19 - P0c** (fair calibration comparator) and **D2 = R19 - P0** (as served). Floors: log loss **-0.010**, Brier **-0.003**. Family: 2 contrasts x 2 metrics x 2 seasons = 8 bootstrap p-values, BH at q = 0.05.
4. **2021-22 reports (written before any result)**: report-derived features dropped on every 2021-22 row (the existing "no usable report" encoding) and `era_report_dropped = 1` on exactly those rows (a separate channel, so "no report" on the 0.1-0.2% of report-less test games is not confounded with 2021-22, the disclosure SEQ_FULL_MIX_RT had to make). The OUT population filter on 2021-22 uses the latest snapshot at or before tip - 180 min (the 2022-24 median lead), so the hourly archive cannot make 2021-22 training rows cleaner than other seasons. 2021-22 rows are training-only.
5. **Verdict**: **PRODUCTION_BEATEN** = in both seasons D1 and D2 meet the floor with CI upper < 0 and BH p <= 0.05 on both metrics, and every guard and run-time gate is clean. **PRODUCTION_HOLDS** = D1 log loss CI lower > -0.005 in both seasons. Anything else = **MIXED** (reported, no proposal). A failed guard or gate = **NO VERDICT**.
6. **Secondaries (no effect on the verdict, CIs reported)**: P0c - P0 (the calibration share); R19 - R22 (history-window effect; R22 = R exactly as frozen on the 2022-start frame); minutes CRPS conditional on playing, **M2_19 - P0** (MINUTES_V2's M2 head refit monthly on U with the era channels; floor -0.10 min, CI upper < 0 per season = "production minutes head beaten"), plus P0's minutes bias, 80% coverage and PIT deciles; props (pts, reb, ast, fg3m) unconditional integer CRPS for R19, P0c, P0 on the stored A0 conditional grids, DNP rows on the PPLAY_INTO_PROPS_LIVE proxy: descriptive only.
7. **Report venue**: the live 2026-27 log under the frozen PPLAY_INTO_PROPS_LIVE (3a305482), unchanged by this rule. **No 2025 touch** (reasoning below). A PRODUCTION_BEATEN verdict is a production-change proposal only; switching is Devin's decision.

## Comparator relations (explicit)
* Information nesting: P0's inputs (trailing play rate, games, played games, mean/sd minutes, starter rate) are all inside R19 (`BASE_FEATURES`), which adds game context, age, Elo margin, `dnp_last5`, `days_since_played`, the T-60 report tag x team strength, morning/evening report flags, production's 13 recent-minutes/role columns and the five recent-role columns. Both arms share the population, so the report's OUT information production uses (exclusion) is in both (F10 cleared: the comparator lacks nothing production has).
* D2 - D1 = P0c - P0 = what clean recalibration alone recovers. D1 = information + learned mapping beyond calibration. Decomposition, descriptive: P0c -> C19 -> C'19 -> R19 (C and C' of T458 on the same window and channels).
* Fair-baseline rule: same rows, same DNP handling (DNP = minutes NULL or 0, label played = minutes > 0), same window (2019-20 on), same clipping, same walk (calendar-month blocks, strictly earlier rows). P0's constants are not refit: refitting them would test a model production does not serve.

## Metrics, slices, calibration
* Log loss with p clipped to [1e-6, 1-1e-6]; Brier = mean (p - y)^2; ECE 10 equal-width bins; reliability table per arm, per season, per slice.
* Slices (declared, guards not BH members): questionable or doubtful at T-60 (`tag60_code` in {3, 4}); bench (`starter10 < 0.5`); second night of a back-to-back (`is_b2b_second`); returners (missed the team's previous >= 3 games, as-of from box plus the team schedule, same team); reported only: playoffs, cold start (5 <= `n_played_prior` < 10), history-window effect by month of season.
* Season 2022 and 2020 are scored as **descriptive replications** (2022: R's never-scored warm-up season, training 2019-2021 plus earlier 2022 months; 2020: era-flagged limited-fans season). 2021 is not scored (its report features are dropped, so R is not R there); 2019 is warm-up only.

## Guards (each must hold per verdict season, else NO VERDICT)
* **G1 planted future** (on the real frame at run start, before any arm is scored): every box row, report snapshot and schedule fact dated after a cutoff, plus the target game's own box (minutes, starter), edited wildly -> every P0 value, every P0c map and every R19 / M2_19 input for rows at or before the cutoff byte-identical; a control edit before the cutoff must change at least one value.
* **G2 missingness beyond the as-of cause**: for every fitted-arm input, NULL share by outcome group (DNP; played in minute buckets) within max(2 pp, 3 x the permuted-outcome gap), measured separately on history rows and modern rows of every training season; plus NULL share by row source and era for every input, each NULL cause listed. Any input outside tolerance in any season stops the run (this is the check that catches the birth-date pattern found below).
* **G3 shuffled-input placebo (non-inert scorer)**: R19's p permuted across the players of one calendar date; LL(R19_dayshuf) - LL(P0c) must have CI lower > 0.
* **G4 past-only placebo**: R19 against the player's previous Cell-P row's p_R19 (strictly earlier, same season): D <= -0.003 with CI upper < 0 (the gain is current information, not a better static player rate).
* **G5 permutation null, K = 50, sized on the select seasons**: seeds 1-50; each permutes the training labels once per block (one global permutation shared by R19 and P0c's map), scored on real labels; s_k = LL(R_shuf) - LL(P0c_shuf) per season. Holds iff (i) mean_k s_k >= -0.001 (a leak-free harness cannot make a shuffled-label R beat a shuffled-label map) and (ii) observed D1 < min_k s_k (permutation p <= 1/51). No single-draw shuffle CI guard is used: T473's fired on about 19% of honest shuffles (audit F9).
* **ECE and slices**: ECE(R19) - ECE(P0c) <= +0.005; no slice with n >= 300 where D1 > +0.01.

## Run-time gates (stop before scoring; a stop is not a result)
* **Parity (F10)**: on 10 seeded 2024 dates, `forward.predict_slate` on `nba.duckdb` (read-only) and P0 on the nba-only scope agree on every shared (game, player): |p diff| and |mu diff| <= 1e-12; no Cell-P row carries a rookie prior or new-team multiplier. The union-vs-nba-only change in P0 on 2023-24 rows (share changed, mean |dp|) is printed as the declared data-scope effect.
* **Reproduction**: R22 on the 2022-start frame equals stored RECENT_ROLE `p_R` (`data/ops/recent_role_cache/scores.npz`), max |diff| <= 1e-9, n 33,093 / 33,127.
* **2021-22**: no 2021-22 row carries a report feature; `era_report_dropped` is 1 exactly on 2021-22 rows; era channels are 0 on every scored row.
* **Holdout**: every frame asserts `season <= 2024`; the 2025 path does not exist in this harness.
* Data version (`docs/data_manifests/latest.json`) and both DB file hashes stamped into results.

## What each choice protects against
* Served function exactly, parity-gated: testing a model production does not run (T458 tested C, not the served hurdle; T043-T046 tested Option 1A).
* Walk-forward Platt, strictly earlier rows, era-flagged games excluded: an in-sample map (the old `P_PLAY_PLATT`, 0.07 worse out of sample, T316-T317) and a comparator handicapped by stale-era calibration, which would flatter R.
* D1 as well as D2: crediting R with a gain that is only calibration.
* Floors -0.010 / -0.003: twice the house P(play) floor (T458's floor); Brier at the measured Brier/log-loss delta ratio (0.26-0.33) times -0.010; both above the measured 95% half-widths (0.0043-0.0045 / 0.0016-0.0018).
* BH over 8, both seasons required: a one-season or one-metric fluke.
* Game-clustered bootstrap: shared game-level outcomes (design effect 1.3-2.0 measured).
* Era channels and 2021-22 handling: era identification and the hourly-report freshness leak (SEQ_FULL_MIX_RT rule 4; classifier AUC 0.80).
* G2 by row source: the NULL-pattern leak class (F3), which in this frame is real (birth dates, below).
* K = 50 null mean instead of one shuffle: a guard that fires by chance (F9).

## 2025 and the report venue (decided honestly with lineage)
* **P0** has been read on 2025: the T462 adversary diagnostic (n 33,061: log loss 0.3505, ECE 0.0208), the 2025-26 dress rehearsal (n 376) and the 2025 replays the production confirmations ran through. It is not a never-evaluated model: no touch.
* **R** spent its touch (T458).
* **P0c** is technically never evaluated, but a 2025 run is **not admissible in substance**: its only comparator is R, so the run would re-read R's spent holdout; and it cannot change a decision, because on 2025 the served p is already well calibrated (ECE 0.0208), so a 2-parameter map can recover only a small part of the known -0.0972 gap. A run that re-reads a spent holdout to learn nothing decision-relevant is not one we take.
* **Relation to PPLAY_INTO_PROPS_LIVE** (frozen 3a305482): that rule scores the live log, raw `p_play_prod` vs `p_play_r`, on unconditional props CRPS; this rule changes nothing in it and cannot pass or fail it. This rule replaces T043-T046 as the offline evidence row for the served component; if P0c - P0 is large, a live recalibrated-production arm is a new id, not an edit.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 17:00 CDT 2026, read-only, no fit, no lock (`reports/prereg_pplay_minutes_valid/gate_draft.json`, 0.7 s)
| # | check | measured | threshold | pass |
|---|---|---|---|---|
| 1 | Cell-P rows per verdict season (stored 2022-start frame; union recount at run time) | 33,093 / 33,127 | >= 25,000 | PASS |
| 2 | games per verdict season | 1,318 / 1,320 | >= 1,000 | PASS |
| 3 | 95% half-width, log loss vs P0 (proxy: MINUTES_V2 v2 - raw, same rows) | 0.0043 / 0.0045 (sd 0.30-0.32, deff 1.65-1.77) | <= 0.010 | PASS |
| 4 | 95% half-width, Brier vs P0 (same proxy) | 0.0016 / 0.0018 | <= 0.003 | PASS |
| 5 | 95% half-width, fitted vs fitted log loss (R - C, stored) | 0.0024 / 0.0023 | <= 0.005 | PASS |
| 6 | slice n per verdict season: questionable / bench / b2b-second / returner>=3 | 1,251-1,345 / 20,515-20,665 / 5,222-5,519 / 2,970-3,128 (returners before the OUT filter) | each >= 300 | PASS |
| 7 | training rows before the first 2023 block (n_prior >= 5, DB scope, before OUT filter) | 118,091 (vs ~30,000 for the 2022 start) | >= 2 x the 2022-start pool | PASS |
| 8 | real tips 2019-2024 | 100% of games (history gate 14:25) | >= 99% | PASS |
| 9 | usable T-60 report 2019-2024; 2021-22 snapshot at or before tip - 180 | 99.8-100%; 100% | >= 98% | PASS |
| 10 | box DNP-row capture vs 2022-24 mean (19.0%) | 16.1 / 20.6 / 17.1 / 18.5 / 19.0 / 19.5%; max gap 2.95 pp | <= 5 pp | PASS |
| 11 | history and production game ids disjoint; player ids shared | 0 overlap; 80.5% of 2022 players seen in 2021 | 0 overlap | PASS |
| 12 | era flags present for every history game | 1,143 / 1,171 / 1,323 of 1,143 / 1,171 / 1,323 | 100% | PASS |
| 13 | **birth date (input `age`) coverage on training rows, 2019 / 2020 / 2021** (re-measured Mon Oct 12 21:33 CDT 2026 after the cache fill (20:35, 736 rows) and the network fetch (46 of 47 players; id 1626218 returns an empty record); before: 70.0 / 77.1 / 86.5%) | **99.99 / 100 / 100%**; the only NULL left is 2 rows of player 1626218 in 2019, both non-playing | >= 98%, and no outcome-dependent NULL | **PASS on coverage; the 2 remaining rows are outcome-dependent by construction (n = 2 of 28,707), proposed excluded from training** |
| 14 | **`is_national_tv` source coverage, 2019-2021** (re-measured 20:36 CDT after filling `games.national_tv` from the held raw schedules) | **30.97 / 31.17 / 27.66%** of history games (was 0%), vs 27.5-27.8% for 2022-24; same first-national-broadcaster field and "NBA TV counts" convention as the modern pull | captured like 2022-24 | **PASS** |
| - | runtime of G5 (K = 50 walks) | not measured (needs the heavy lock after 18:15 and the harness); estimate 50-100 min Mac CPU, one process, per-permutation checkpoints | <= 2 h | unmeasured |

Check 13 is a NULL-pattern leak, not just a gap: a 2019-21 row lacks a birth date because the player is absent from the post-2022 player table, i.e. because he was out of the league later. That is future information correlated with the label (play rate 0.62-0.72 vs 0.85-0.89). Check 14 turns `is_national_tv` into an era flag in disguise (constant 0 on all history rows). Both are fixable from free or already-held data: birth dates for the 301 missing ids from the NBA stats `commonplayerinfo` endpoint (archivist); `games.national_tv` for 2019-21 from the held `data/history/schedule/raw_*.parquet` broadcaster columns (no fetch). After the backfill, checks 13-14 and the union-frame row counts are re-measured; then the rule can be frozen.

## Data-capture audit (2019-2024, measured; numbers in gate_draft.json)
* Box rows per team-game 12.6 / 13.5 / 12.8 / 12.9 / 13.2 / 13.3; DNP rows coded as NULL minutes in every season (minutes = 0 on 1-9 rows a season). Players absent from the box (inactive) are not capturable in any season, the same as production (box membership defines the roster history, never tonight's box).
* Play rate among n_prior >= 5 rows: 0.866 / 0.806 / 0.840 / 0.833 / 0.821 / 0.812. 2020-21 (COVID protocols) is lowest; it is flagged (`era_limited`) and excluded from the P0c map.
* Reports: official PDFs, real tips, 99.8-100% of games usable at T-60; 2021-22 hourly (20.5 snapshots before T-60 per game vs 2.4-3.9; median lead 60 vs 150-180 min); listed OUT per game 6.9 / 5.8 / 8.0 and questionable 0.67 / 0.85 / 0.34 in 2019-21, so 2021-22's report mix differs (handled by rule 4).
* Team strength tercile for 2019-20 rows needs 2018-19 standings, which are not held: NULL on 2019-20 rows (team-level, flagged, never imputed; G2 must show it outcome-independent).
* `team_context` is empty in both databases; rest and travel come from the schedule in both, identically.
* History `players_static` is empty (check 13). History `games.national_tv` is empty (check 14).

## Playbook check
Model classes: P0 is an empirical-Bayes shrinkage formula (no fit); P0c adds a calibration layer (playbook "quantile and distribution layers"); R19 and M2_19 are gradient-boosted trees.
1. **Information or capacity?** R19 vs P0 is mostly information (reports, recent minutes) plus a learned mapping; P0c vs P0 is calibration only. The window change (2019-start) is data, not information about tonight.
2. **Enough data for the class?** Trees: 2-4 seasons is usually enough; W19 gives ~118k pre-2023 rows vs ~30k; the R19 - R22 secondary is the learning-curve point. Platt: 2 parameters, any window suffices.
3. **Test valid?** Real tips only; DNP = minutes NULL/0 in both arms; parity gate against the served function; same rows; planted future and missingness by row source; K = 50 null; clustered CIs. Integer support does not arise for P(play); minutes are continuous on both arms' common quantile grid as MINUTES_V2 scored them; props descriptive uses the integer grid for every arm. **Currently invalid as specified** until checks 13-14 are fixed: the frame carries a future-derived NULL pattern.
4. **Lineage** (next section).
5. **Scored against?** P(play) log loss and Brier on not-OUT rostered players; minutes CRPS given play; props CRPS only descriptively. Nothing here reaches a market line.
Class checks (GBM): missingness beyond the as-of cause (G2, and it fires on history birth dates); planted future (G1); "does the arm only re-express the base?" (D1 vs D2 and the C19 -> C'19 -> R19 decomposition). Calibration layer: the reliability table at the decision point that matters, the 0.5 eligibility threshold on raw p, is printed for P0 and P0c.

## Lineage (which earlier constructions this is, which guards carried)
* **Served P0**: `nba/props/minutes.py` hurdle, 0f4a8f7 (2026-10-05), constants unchanged since. Evidence about it: MINUTES_V2 T162-T165 (M2 beats production minutes by -0.83 / -0.85 min CRPS, VALID*); MINUTES_V2 P(play) secondary (v2 - raw -0.049 / -0.058 log loss, descriptive); T316-T317 (raw p 0.3419 / 0.3556 on 2023 / 2024); T462 2025 diagnostic. None is a pre-registered test of the served component with bounds; this rule is.
* **`P_PLAY_PLATT`**: 8db734f, fit in-sample; off since 2026-10-11 (DECISIONS 14:10). Carried only as a lineage print (P0p), never as an arm.
* **T043-T046 (Option 1A)**: not the served model (above). Its 2025-in-calibration flaw (F12) concerns an unserved component; the guard it motivates (walk-forward calibration, never on scored rows) is carried into P0c.
* **R**: RECENT_ROLE (T455-T457), RECENT_ROLE_HOLDOUT (T458 PASS vs C), adversary T462 SURVIVES. Carried: past-only placebo, non-inert shuffle (as G3), planted future on all added columns, missingness by outcome group, ECE and slice guards. **Not carried**: T473's single-draw shuffle CI guard (fires ~19% on honest shuffles, F9); replaced by G5.
* **History window**: SEQ_FULL_MIX_RT's W_ALL (2019-start, era channels, 2021-22 tokens dropped) and its disclosure about the shared no-report value, fixed here with a separate channel. ENSEMBLE's era-embedding probe is not carried (no embedding in a tree); its purpose is met by era channels that are zero on every scored row (run-time gate) and by G2 by era.
* **MINUTES_V2 M2**: its frozen feature set and scoring of arm P are reused for the minutes secondary; F7 (minutes table moved later) is moot on the current data version.

## Kill / stop criteria
* Any guard or run-time gate failing: NO VERDICT for the season, stop, report, fix plumbing only (no rule change). A failed gate after the freeze is not a result.
* No new arm, window, floor, slice or metric after any result. One pre-registered follow-up at most (e.g. a live recalibrated-production arm), new id.

## Unstated details chosen before scoring
Month blocks = `month_blocks` on the verdict seasons, MIN_TRAIN 1,500; Platt by `LogisticRegression(C=1e6, lbfgs)` on the single logit feature; bootstrap percentile with two-sided p = 2 x min(share >= 0, share <= 0), floor 1/2000; BH via `bh_adjust`; returner = missed >= 3 of the team's immediately preceding games while on that team (`k - last_played_k - 1 >= 3`); questionable = `tag60_code` in {3, 4} (PPLAY_CONTEXT coding: unlisted 0, available 1, probable 2, questionable 3, doubtful 4, NULL when no snapshot); era-flagged = any of `covid_bubble, no_fans, limited_fans` true; LightGBM threads fixed at 4; one process.

## Harness and command (not built; stops here)
To be built after the freeze as `research/eval/pplay_minutes_valid.py` (reusing, not copying, `recent_role_eval`, `pplay_context_eval`, `minutes_v2_eval`), with `run` refusing unless a commit `prereg(PPLAY_MINUTES_VALID): <sha>` exists and the text hashes to it, fixture tests for every gate, then the maintainer command (takes `data/ops/heavy.lock` itself, retry 120 s, removes only its own; per-stage checkpoints; prints per block): `.venv/bin/python -W ignore -m research.eval.pplay_minutes_valid run --sha <64 hex>`.

=== RESULTS BELOW ===

(not run)
