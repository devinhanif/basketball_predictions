# Validity audit of every ledger test, T001-T477 (2026-10-12)

Archivist, read-only. Started at Mon Oct 12 16:19 CDT 2026 (`date`). Asked by Devin: "if there's more data, better more complete data, and the test
we did in the past wasn't done correctly, then we don't know if it was right, especially if neural networks get better w data, and the results
can be bc of data starvation." Rule applied: BEST_PRACTICES "An invalid test is not a null". Nothing was fitted or re-run, no DuckDB was opened,
season 2025 was not loaded. Sources: TEST_LEDGER (T001-T477), HOLDOUT_ACCESS_LOG, DECISIONS, DATA_CHANGELOG, docs/reviews/* (29 files),
docs/prereg/NEURAL_FAMILY_WALL.md (inventory), docs/MODEL_PLAYBOOK.md (189cad2), `git log` for the switch times, and the stored run metadata
`data/colab/runs/seq_full_mix_rt_sel/*/metrics.json` (runtimes only). No ledger row is edited; the annotation block at the end is a DRAFT.

## Labels
- **VALID**: no known flaw applies.
- **VALID\***: a known flaw applies but it cannot have flipped the verdict (the margin is many times the flaw, the flaw is shared by both arms,
  or it flatters the candidate that lost anyway). The result may be cited.
- **INVALID**: a known flaw applies and could have flipped the verdict. The result may not be cited against the idea (BEST_PRACTICES).
- **UNCERTAIN**: no numbers or bounds to judge, or two flaws pull in opposite directions by unknown amounts.
- **n/a**: not a test (corrections, descriptives without a claim, untested mechanisms).

## 1. Systematic flaws (with the fix and its record)

| id | flaw | fixed | fixed by (commit / record) | direction of bias | who it hits |
|---|---|---|---|---|---|
| F1 | Injury-report gating on a 19:00 ET tip proxy instead of the real tip: admits the 17:00 snapshot for the ~7.7% of games tipping at or before 17:00 | production + `context_residual` default 2026-10-09 08:08 CDT (76cc177, T128); library default `ReportTriggerConfig` 2026-10-12 02:37 CDT (4ea569a) | redteam_production_2026-10-09; REALTIP T091-T095 | flatters any arm that reads reports; pts props by ~0.003, Elo nil (-0.00786 vs -0.00812) | every test before 08:08 on 10-09; after it, research harnesses on the default: `research/features/player_sequences.py` (every SEQ export until SEQ_FULL_MIX_RT: T065-T072, T441-T445, T464-T466, T471), `research/props/context_features_v2.py` (CTXRES V2/V3, XGB_PTS_VS_LT A1 frame), `research/features/game_sets.py` (JOINT) |
| F1b | proxy19 SEQ report-token channel inflates exactly the SEQ-minus-FULL contrast (2024 leaky games AVG-FULL -0.0256 vs clean -0.0100) | 2026-10-12 14:24 CDT (SEQ_FULL_MIX withdrawn unrun; SEQ_FULL_MIX_RT real-tip export, e1560ec) | seq_full_mix_loader_review_2026-10-12; DECISIONS 14:24 | flatters SEQ; real-tip + rerun cost +0.0013 / +0.0017 on the blend (gain attack) | all SEQ rows above |
| F2 | DNP rows averaged as zeros / played-games bug: baselines ~1 pt low, volatility buckets became DNP detectors | 2026-10-08 (DNP_AUDIT_2026-10-08; fair rematch T062-T064 note) | docs/DNP_AUDIT_2026-10-08.md | arbitrary; created the sim "wins" | T001-T060, T183 |
| F3 | Leaky inputs: `opp_adjusted_ridge` NULL iff tonight's minutes < 5 | 2026-10-08 19:35 (ridge v2 builder, `ref_fixed`); missingness check in `datamanifest` 2026-10-09 | TEST_LEDGER leak notice; leak_audit_2026-10-09 | flatters the candidate (whole v2 gain) | T073-T080 (incl. CTXRES_V2 `mlp_quantile`); the CTXRES_V3 2025 touch was voided unrun |
| F3b | Minor look-ahead: `sch_days_to_next` / `sch_b2b_first` in the postseason | c9b00d1 | redteam_ctxres_v3 | <= 0.0005, flatters v2-feature arms | T102-T117, T382-T388 (frozen frame, disclosed) |
| F4 | Continuous-vs-integer support mismatch: integer-grid arms scored against continuous production quantiles on integer outcomes | 2026-10-09 (red team; T114-T118; every later props rule scores both arms on integer support) | redteam_ctxres_v3_leakfree_2026-10-09; INTEGER_QUANTILES | rewards the integer arm by 92-126% of the claimed count-stat margins | T073-T080 (exp-2 NB "wins"), T102-T109; "NB wins counts" in BEST_PRACTICES s4 |
| F5 | Training window too short for the model class (2022-start; 2023 select season trained on one season) | not fixed as a practice; W_ALL first used 2026-10-12 (SEQ_FULL_MIX_RT run #1) | seq_full_mix_rt_gain_attack_2026-10-12: SEQ W_ALL minus W22 AVG-FULL -0.0120 [-0.0145,-0.0097] / -0.0072 [-0.0091,-0.0053]; RAW member -0.0470 [-0.0560,-0.0386] / -0.0254 [-0.0328,-0.0181]; trees: HISTORY_WINDOW_3 T420-T423 (history helps 2023 pts -0.0108, nothing in 2024) | penalises the data-hungry arm; for trees it starves both arms on the 2023 select season only | neural: T065-T072, T081, T082-T084, T177, T178 (mlp_offset), T441-T445, T464-T466; trees: every 2023 select row (no tree verdict depends on 2023 alone, see s2) |
| F6 | Underpowered or no CI: point-only, row-level bootstrap, MDE above the effect, failed power gate | standing plan caps 2026-10-08; non-negotiable 8 (2026-10-09) | ACCEPTANCE_CRITERIA; F9/F12 lesson | either direction | T001-T061, T160-T161, T176-T182, T475 |
| F7 | Data defects in parsed tables at run time: stints ~47-49% within 1 min of box before the v2 tracker (e9b76c58, 2026-10-09), possessions in `action_number` order (85d8a412, 2026-10-10; 4,258 games had mis-ordered trips), Missi free throws / team-0 rows / 10 neutral-site games / short appearances missing from stints (data_version 1bee33d0 and 418510d4, 2026-10-11) | 2026-10-09 .. 2026-10-11 | DATA_CHANGELOG; stint_rebuild, possession_order, data_audit_2026-10-11 | noise in lineup/possession inputs attenuates real effects toward zero (it can hide a gain, contrary to "defects blur, cannot create nulls" in the merged audit) | lineup/PBP consumers before 10-09: T001-T064 sim, T004, T042, T082-T084 (PBP_GPT tokens), T176 RAPM, T177 rung-4, T185 F12; between 10-09 and 10-11 (residual 1-2%): T244-T299, T162-T173 (minutes table moved up to 1.53 min for 14% of 2024 rows) |
| F8 | Harness defects: D1 inert shuffle (can only create a false PASS); D2 placebo permuted within player/team-season, carrying future values (can only create a false FAIL "does not beat the placebo") | RECENT_ROLE harness (2026-10-12 ~00:37): past-only placebo, both-arms shuffle | workload_review_2026-10-12; ledger_audit part3 s2 | D1 false pass, D2 false fail | WORKLOAD T451-T454, ADVANCED_RATES T424-T439 (reading only, T459), TEAM_BUDGET, FOUR_FACTORS, F11, COACH_PHASE (never ran) |
| F9 | Guards that cannot pass or are mis-sized: ENSEMBLE era probe (current-season placebo pairs already AUC 0.77-0.86 for an id-aware network); RECENT_ROLE_FULL shuffle guard fires ~19% on honest shuffles | 2026-10-12 11:27 (permutation-null sizing, BEST_PRACTICES) | shuffle_guard_diagnostic_2026-10-12; ledger_audit part3 C4 | false FAIL | T443 (E3), T473 |
| F10 | Wrong / weak comparator: gain measured against a comparator missing information production already had | recognised 2026-10-12 (merged audit) | ledger_audit_2026-10-12 "the string"; overnight_final_audit N3 | overstates a gain | T085-T090, T096-T097 (T-60 P(play) lacked recent minutes), T451 (WORKLOAD C), T458 (C, not the deployed hurdle) |
| F11 | Box-score proxy for later information (starter flag for the announced five; inactive-list proxy) | open; live T-30 / T-15 collector is the fix | redteam_lineups_known_2026-10-09 (WOUNDED) | overstates a gain (range given) | T085-T090, T160-T161, ROUTER_TIME_WEIGHTED C4 |
| F12 | Season-2025 rows in a training/calibration export or pooled split | holdout discipline from 2026-10-08 | HOLDOUT_ACCESS_LOG "NO VIRGIN HOLDOUT" | flatters, unknown size | T001-T061 (burned pools), T043-T046 (cal split likely incl. 2025), T177 (2025 rows in the export, unscored) |

## 2. Per-experiment classification

Class = MODEL_PLAYBOOK class. "Used later" = the verdict was cited against the idea or used to close a direction.

| T rows | experiment | class | label | flaws, and why they can or cannot flip | used later against the idea? |
|---|---|---|---|---|---|
| T001-T003, T183 | sim vs season-avg | possession sim | INVALID | F2 (savg with DNP zeros), F6 (row bootstrap), F7 (pre-v2 stints), F1. Already reversed by T062-T064 | yes (retracted, superseded) |
| T004 | on-court usage denominator | linear | VALID\* | F7, F6 no CI; +0.398 is far beyond any lineup-noise effect | yes (feature off); stands |
| T005-T019 | volatility-bucket routing | routing | INVALID | F2: buckets were DNP-frequency labels | superseded |
| T020-T027 | archetype-cluster routing | routing | INVALID | F2, selective reporting | superseded |
| T028-T033 | walk-forward/holdout routing | routing | INVALID | F2, F6, F12 | superseded |
| T034-T041 | matchup / opponent adjust | linear | INVALID | F6 point-only, F2-era pipeline; question later answered validly by ADVANCED_RATES defense context (T424-T439) and RIDGE_V2 | superseded |
| T042 | lineup archetype-mix R2 | linear | UNCERTAIN | F7 (half the stints wrong) attenuates; R2 0.016 could be larger. Lineup context later null on fixed data (F11, F8) | superseded by T244-T263 |
| T043-T046 | minutes 1A (the deployed P(play)/minutes hurdle) | GBM | UNCERTAIN | F6 no bounds, F12 cal split likely incl. 2025, F2-era. **In production.** T462: R beats the deployed hurdle by -0.0972 [-0.1029,-0.0915] on 2025 | used to ship the gated hurdle |
| T047 | usage redistribution | - | n/a | n = 0 | - |
| T048-T060 | matchup 3A + aux | linear | INVALID | F2 (aux rows compare different DNP handling, unreconciled), F6 point-only gate | superseded |
| T061 | pts sim @ 2000 sims | sim | VALID\* | F2/F7; +0.19 worse, 40x any floor, agrees with T062 | yes; stands |
| T062-T063 | fair rematch pts, reb | sim | VALID\* | F7 sim lineup inputs pre-v2, F1; losses +0.268 / +0.123 | yes; stands |
| T064 | fair rematch ast | sim | UNCERTAIN | F7 on a sim that reads lineups; +0.0143 [+0.0052,+0.0229] is 3x the floor (ledger audit part1 C4 prior 0.04) | yes |
| T065-T068 | SEQ alone vs production (seq_props) | neural sequence | INVALID | F5: trained on 2022 + part of 2023. W_ALL moves the RAW member by -0.047 / -0.025, larger than the losses (+0.0247 pts, +0.0158 reb, +0.0117 ast). F1 flatters SEQ (small) | yes ("architecture lost") |
| T069-T072 | SEQ blends vs production | neural sequence | UNCERTAIN | F5 understates, F1 overstates; comparator was continuous-support production. Superseded by ENSEMBLE / SEQ_FULL_MIX_RT | superseded |
| T073-T080 | CTXRES_V2 exp1/exp2 (incl. `mlp_quantile`) | GBM / MLP | INVALID | F3 leak carried the gain; F4 for counts; F1 | superseded (T098-T117, T382-T388) |
| T081 | JOINT set transformer vs copula | set / joint | UNCERTAIN | F5 (2,365 training games), F6 (one season detects ~0.002 vs a plausible effect ~0.001), F1 via `game_sets` default | yes ("the copula is enough") |
| T082-T084 | PBP_GPT P1/P2/P3 | PBP generative | UNCERTAIN | F5 (2022 + 80% of 2023, very high appetite), F7 (trained on action-number-ordered PBP; 4,258 games had displaced events), F6 (P2 n = 150). Losses are 10x floors on P1/P3, so a flip needs a very steep learning curve | yes ("architecture loses") |
| T085-T090, T096-T097 | LINEUPS_KNOWN T-30 | GBM | UNCERTAIN (size) | F11 starter proxy (WOUNDED, range), F10 T-60 P(play) comparator lacked recent minutes, F1 shared. Direction valid; size open | no; live T-30 shadow is the venue |
| T091-T095, T128 | REALTIP | Elo / GBM | VALID | the F1 fix itself | - |
| T098-T101 | RIDGE_V2 (leak-free) | linear | VALID\* | F1 shared by both arms (pre-switch); \|delta\| <= 0.0004 | yes; stands |
| T102-T109 | CTXRES_V3 leak-free replication | GBM | INVALID | F4 (92-126% of the count margins); pts restated in T114 | superseded by T114-T117 |
| T110-T117 | V3 threshold log loss; red-team fair restatement | GBM | VALID\* | F1/F3b flatter the hybrid, which still failed (reb/ast/fg3m) or was WOUNDED (pts) | yes; stands |
| T118, T124-T127 | integer-support convention | quantile layer | VALID | P(>=N) invariant; scoring convention only | - |
| T119-T123, T174-T175 | TRACKING / hustle screen | GBM | VALID\* | F1 shared (pre-switch); hustle row-existence depends on minutes (data audit F9) but features are past-game only; nulls 4x inside the floor | yes; stands |
| T129-T135 | PTS_TAIL | quantile layer | VALID | after the switch; integer support | - |
| T136-T159 | LOWER_TAIL | quantile layer | VALID | F5 (tree) on 2023 only; pts passed both seasons, others below the floor in 2024 too | - |
| T160-T161 | INJURY_ELO_T30 | ratings | UNCERTAIN | F6: CI half-width 0.0035 vs floor 0.002; F11 inactive-list proxy | yes ("no T-30 win model"); low value |
| T162-T173 | MINUTES_V2 | GBM | VALID\* | F7 minutes table moved later (both arms); minutes gain -0.83 min is huge, props null in 2024 | the P(play) secondary was never acted on (now RECENT_ROLE) |
| T176 | RAPM in injury-Elo | linear ratings | INVALID | F7: RAPM was built on pre-v2 stints (47-49% within 1 min of box): the regression's design matrix was half wrong. F6 (CI spans 0, MDE ~0.0035). RAPM also wants multi-season data (F5 for this class) | yes ("RAPM lost") |
| T177 | rung-4 step heads | neural (step heads) | UNCERTAIN | no comparator, no CI, F12 (2025 rows in the export), F7 possession order | consumer (sim) dead |
| T178 | WINPROB_FAMILY | Elo / GBM / MLP | VALID\* | F1 nil for Elo; the `mlp_offset` member alone is UNCERTAIN (F5) but ties at -0.0006 [-0.0030,+0.0017] | yes; stands for Elo |
| T179 | TIME_DECAY | - | n/a | untested mechanism | - |
| T180-T182 | context screens, GA tune | Elo / linear | UNCERTAIN | F6 (in-sample, n 102 rookies, no CI) | leads, not negatives |
| T184 | F9 gate | GBM | VALID | gate measurement on the data of the day; F9b ran later | - |
| T185 | F12 gate (closing risk) | descriptive | INVALID | F7: the reconciliation check failed on pre-v2 stints (48.1% within 1 min; now 99.7%); the premise check read the same stints and blowout garbage time | yes ("closed") |
| T186-T201 | F9b | GBM | VALID | powered (slice MDE 0.013-0.017 vs floor 0.02) | - |
| T202, T238-T243 | REFEREES gate + run | GBM | VALID | gate fixed before the run | - |
| T203-T237 | ODDS_HISTORY + red team | GBM vs market | VALID | real tips, integer production grid | - |
| T244-T263 | F11, F8 lineup context | GBM | VALID\* | F7 residual 1-2% stints, F8 D2 placebo; nulls fail the floor with MDE 0.0006-0.0054; oracles valid | yes; stands |
| T264-T279 | FOUR_FACTORS | GBM | VALID\* | F8 D2 placebo, possessions pre-Missi fix (0.016 pts reconcile gap); null on floor | yes; stands |
| T280-T283 | PTS_COMPONENTS | quantile / count | VALID | +0.03, 6x the floor | - |
| T284-T299 | ROUTER_TIME_WEIGHTED | router | VALID\* | F11 (C4 is the starter proxy), F7 n 27,583; the router-vs-best-single verdict is unaffected | - |
| T300-T314 | MINUTES_HAZARD (+_2) | hazard GBM | VALID | ran after the stint rebuild; +0.55 min worse | - |
| T315 | HISTORY_WINDOW gate | GBM | INVALID (gate) | report coverage later repaired to 99.8-100% (DECISIONS 2026-10-12 14:28); question answered validly by HW3 T420-T423 for trees | superseded |
| T316-T339 | PPLAY_CONTEXT, ELO_HISTORY_WINDOW, MARKET_LINE, HW2 gate, OWN_TAG, CALIBRATED_GRID | GBM / Elo / logistic | VALID | real tips, clean placebos (audit part3), 2024 also null wherever 2023 failed | - |
| T340-T388 | MODEL_CLV; XGB_PTS_VS_LT | GBM | VALID / VALID\* | XGB A1 frame on proxy19 and F3b (flatters A1), still tied | - |
| T389-T439 | GAME_MARKET_CONTEXT, MARKET_SHRINK, HW3, ADVANCED_RATES | GBM | VALID | ADVANCED_RATES placebo reading withdrawn (T459), null stands | - |
| T440 | ENSEMBLE E1 XGB router | router | VALID\* | weak gate (design, not a validity flaw); proxy flatters A1 | superseded by ROUTER_G2 |
| T441-T445 | ENSEMBLE E2 alone, E2 blend, E3, E4, verdict | neural sequence | INVALID | E2 alone HARM +0.0279 / +0.0173 vs a W_ALL RAW-member move of -0.047 / -0.025 (F5; estimated flip to about -0.019 / -0.008 if W22 RAW is close to E2 RAW, not measured). E2 blend failed slice guards; F5 does not repair near-DNP rows, so that guard result may survive. E3 failed only on an era probe that an id-aware network cannot pass (F9). F1 proxy channel throughout | yes: "the sequence network is worse than production alone, again" and the empty neural family (withdrawn by DECISIONS 16:14) |
| T446-T449 | TEAM_BUDGET | GBM + raking | VALID\* | F8 D1/D2 (harm unaffected); built on the weak hurdle (design), T-30 version untested | yes for T-60; stands |
| T450 | COACH_PHASE gate | - | VALID | gate stop on data drift | - |
| T451, T453-T454 | WORKLOAD P, M, verdict | GBM | VALID\* | P pass with an inert shuffle (D1) but superseded by the fixed-harness RECENT_ROLE/T458; M missed the -0.10 floor regardless | - |
| T452 | WORKLOAD pts | GBM | INVALID | the only failed condition was "beats the placebo" (+0.0006) and that placebo leaks future player-season values (F8 D2); -0.0071 / -0.0082 clears the floor | yes (NEAR_MISS_BUNDLE, not frozen) |
| T455-T462, T467-T469, T472 | RECENT_ROLE, HOLDOUT, PI_RECENT_ROLE, FULL stage 1, PACE_TOTALS | GBM | VALID | fixed harness; real tips | - |
| T464-T466 | ROUTER_G2 | router over SEQ | VALID\* | the verdict ("the gate adds nothing over a constant") does not depend on SEQ quality; the SEQ blend's size does (F5, F1), see T441-T445 | - |
| T473 | RECENT_ROLE_FULL stage 2 (2025) | GBM | INVALID (as a negative) | failed only on a shuffle guard that fires ~19% on honest shuffles (F9); null mean -0.0003 (se 0.0003); primary -0.0116 [-0.0159,-0.0076]. The frozen rule's verdict stands as a rule outcome; it may not be cited as evidence against the columns | live FULL shadow is the remaining venue |
| T475 | ROAD_STARS | GBM | UNCERTAIN | F6: run despite a failed power gate (best case ~0.001-0.003 vs floor -0.005) | no (live slice) |
| T459, T463, T470, T471, T474, T476, T477 | corrections and descriptives | - | n/a | no claims | - |

Holdout touches outside the T numbers: injury-Elo 2025 CONFIRMED on proxy19 (F1 nil for Elo): VALID. context_residual 2025 CONFIRMED on proxy19 (pts
overstated ~0.003 of -0.1206): VALID\*. CTXRES_V3 2025: VOID unrun. SEQ_FULL_MIX: withdrawn unrun. SEQ_FULL_MIX_RT: logged, pending.

**Counts by T id (477 ids; grouped ids such as T005-T019 count once each):** VALID 249, VALID\* 106, INVALID 84, UNCERTAIN 29, n/a 9.
By experiment group (59 rows of this table, holdout touches excluded): VALID 14, VALID\* 15, one mixed VALID / VALID\* row, INVALID 15, UNCERTAIN 11, n/a 3.

What the INVALID set has in common: 71 of 84 ids are the 2026-10-08 back-fill, leak/support-era or repaired-gate rows already superseded by valid reruns. The other 13
ids still matter: the neural sequence results (T065-T068, T441-T445), RAPM (T176), F12 (T185), WORKLOAD pts (T452) and T473.

No tree (GBM) verdict depends on F5: every tree rule that failed its 2023 select season also failed or was null in 2024, where the window holds two
seasons and HW3 shows trees saturate.

## 3. Redo list (INVALID or UNCERTAIN, question still matters), ranked by plausible effect x 1/cost

| rank | item (T rows) | why it could change | first step and cost | venue |
|---|---|---|---|---|
| 1 | SEQ alone and SEQ blends (T065-T072, T441-T445; feeds T464-T466) | W_ALL RAW gain -0.047 / -0.025 exceeds the recorded harm +0.028 / +0.017; history gain largest when the pool is small | **Learning curve on 2023/24 (step 1 for all neural models):** SEQ E2 on the real-tip export, train windows of 1, 2, 3, 4, 5 seasons ending before each test month (2023 test: 1-4 available, 2019-20..2022-23; 2024 test: 1-5), era channels on (bubble, 2020-21, 2021-22 report drop), 3 seeds, same warm-refit walk, plus one shuffled-input arm at the largest window; score RAW member and AVG-FULL by window, game-clustered CI; "still rising at the largest window" = starvation. **Cost:** run #1 took 160 s (W22, 22,853 fit rows) and 419 s (W_ALL, 97,774) per program for 3 seeds, so 5 windows + 1 placebo is about 25-35 GPU minutes, about 1 T4 hour with setup and export; Colab units unknown (rates null in configs/cost.yaml, 75 units left). Mac scoring about 10 min under heavy.lock | 2023/24 only (spends nothing); then the already logged SEQ_FULL_MIX_RT 2025 touch is the confirmation for pts and reb (secondary) |
| 2 | RECENT_ROLE_FULL stage 2 (T473) | primary -0.0116 met; failed only on a mis-sized guard | nothing to run; FULL_SHADOW_LIVE_2026_27 (13831b52) already frozen with a K=100 null | live log |
| 3 | Production P(play)/minutes hurdle (T043-T046) | the deployed component's only evidence has no bounds; R beats it by -0.0972 on 2025 (T462, adversary diagnostic) | none new; PPLAY_INTO_PROPS_LIVE (3a305482) and the P(play) shadow already measure R vs the deployed hurdle | live log; production change is Devin's |
| 4 | WORKLOAD pts (T452) | failed only on a future-leaking placebo; -0.007 / -0.008 clears the floor; may overlap recent-role columns | rerun the pts cell with the past-only placebo and the both-arms shuffle, on top of FULL (A0 + 5 recent-role columns), new rule id; Mac CPU, under an hour | 2023/24; one 2025 touch available (LOAD heads never touched 2025) |
| 5 | LINEUPS_KNOWN size (T085-T090, T096-T097) | comparator lacked recent minutes (F10), starter proxy (F11); the live T-30 arm's expected size depends on it | descriptive re-baseline: P(play) R vs R + T-30 starter flag, and props T-30 vs FULL, stored 2023/24 arrays; Mac CPU, minutes | 2023/24 descriptive; live T-30 / T-15 shadows decide |
| 6 | JOINT set transformer (T081) | F5 (2,365 games) and F6 together | learning curve as in rank 1 (W_ALL game_sets export with real tips and injury-Elo OOF 2019-21 needed first); original run 47.6 min on L4, about 2-3 T4 hours for the curve; power check before any rule (one season detects ~0.002) | 2023/24; pooled seasons; live parlay log |
| 7 | RAPM (T176) | design matrix built on half-wrong stints; RAPM needs multi-season data | rebuild RAPM on the current stints (data_version 418510d4) with 2019-21 history as a prior, rescore inside injury-Elo; Mac CPU, about an hour; MDE ~0.004 at n 3,951, so set the floor accordingly | 2022-24 OOF; 2025 untouched for RAPM (one touch) |
| 8 | F12 closing risk (T185) | both gate checks ran on pre-v2 stints | descriptive re-measure of reconciliation and the premise on the rebuilt stints with a defined garbage-time exclusion; Mac CPU, minutes; F12b only if it passes | 2023/24 |
| 9 | PBP_GPT (T082-T084) | very data-hungry, trained on mis-ordered PBP | only if rank 1 shows starvation: rebuild tokens in game order, learning curve with 2019-21 PBP (same schema); original 2.6 A100 hours, curve about 4-5x that; low product value (live win is priced) | 2023/24 |
| 10 | MLP tabular arms (CTXRES_V2 `mlp_quantile` T073-T080; WINPROB `mlp_offset` T178) | leaky inputs (mlp_quantile), F5 | only inside a neural-family rule after rank 1, leak-free export 2019-24, compared with the tree on the same inputs | 2023/24 |

Below the line (record, do not chase now): INJURY_ELO_T30 (T160-T161) and ROAD_STARS (T475), both underpowered: pool on the live log; T042 and
T064 (lineup data): superseded by F11/F8 and by the sim's other losses; rung-4 (T177): consumer dead; context screens (T180-T182): leads only.

## 4. Production check
- `rung0_injury_elo` v2: 2025 confirmation used the proxy, but the proxy effect on Elo is nil (T091). VALID. Not affected.
- `props_context_residual` v2: 2025 confirmation used the proxy; pts overstated by about 0.003 of -0.1206. VALID\*. Not affected.
- **The P(play)/minutes hurdle inside production props has no valid confirming test** (T043-T046: no bounds, 2025 likely in the calibration split).
  It is not wrong by evidence of a flaw, but it is beaten by -0.0972 [-0.1029,-0.0915] on 2025 by R (T462). The live P(play) shadow and
  PPLAY_INTO_PROPS_LIVE are the venue; the switch is Devin's decision.
- Shadows: lower-tail (VALID), integer arm (VALID), T-30 (direction valid, size UNCERTAIN), FULL (its 2025 negative is INVALID as evidence; live
  rule frozen), GA-tuned MOV-Elo comparator (UNCERTAIN, comparator only).

## 5. Playbook check (docs/MODEL_PLAYBOOK.md)
Classes judged as the playbook says: F5 counted as a validity flaw only for neural, set and PBP classes (and RAPM); for Elo and trees it was checked
against ELO_HISTORY_WINDOW and HW3 and never flips a verdict; leaky inputs (F3) and support mismatch (F4) invalidate any class.

Statements this audit contradicts or should add:
1. **Neural "driven by data volume first, then input timing":** the timing step is not measured separately; real-tip tokens and Colab rerun noise
   are confounded in one +0.0013 / +0.0017 step. Say "timing effect unmeasured; proxy19 flattered every pre-RT SEQ number slightly".
2. **Add to neural and PBP failures:** every SEQ export before SEQ_FULL_MIX_RT pinned proxy19 (`player_sequences.py`), and PBP_GPT was trained on
   action-number-ordered play-by-play (fixed 2026-10-10). Past numbers carry a starvation penalty and small timing/order defects at once.
3. **Add RAPM under linear/ratings:** high appetite for possessions with correct lineups; T176 was built on pre-v2 stints and is INVALID.
4. **Trees:** add that the 2023 select season is a one-season window (HW3: -0.011 pts from history on 2023, nothing on 2024); a 2023-only fail for a
   tree is weak evidence, a 2024 fail is not.
5. **Cross-cutting check 3 should add:** guards that cannot pass (E3 era probe), harness D1/D2 (inert shuffle gives false passes, future-leaking
   placebo gives false fails), the comparator's completeness (F10), and data versions of lineup/possession inputs at run time.
6. The merged ledger audit's line "defects blur, cannot create nulls" is wrong as a general statement: input noise attenuates effects toward zero and
   can create a null. It did not change the F11/F8 verdicts here (powered nulls with valid oracles), but it should not be reused.

## 6. Drafted rows (DRAFT; not appended; committing them is the main session's act)

### Ledger annotation block (to add below the existing rows)
```
**VALIDITY AUDIT 2026-10-12 (docs/reviews/validity_audit_2026-10-12.md; no row edited, no test re-run; season 2025 not loaded).** Labels: INVALID = a known flaw could have flipped the verdict, not citable against the idea; VALID* = flawed, cannot have flipped; UNCERTAIN = cannot judge. By id: VALID 249, VALID* 106, INVALID 84, UNCERTAIN 29, n/a 9.
- T001-T003, T005-T041, T048-T060, T183 INVALID (DNP-zero baselines, point-only or row-level, pre-v2 stints; already superseded by T062-T064 and later valid rules).
- T065-T068 INVALID (training window: 2022 + part of 2023; W_ALL moves the raw SEQ member by -0.047 / -0.025, larger than the recorded losses; could flip). T069-T072 UNCERTAIN (window understates, proxy19 overstates).
- T073-T080 INVALID (opp_adjusted_ridge missingness leak; count stats on mismatched support). T102-T109 INVALID (support mismatch; restated in T114-T117).
- T081 UNCERTAIN (2,365 training games; one season cannot detect the plausible effect). T082-T084 UNCERTAIN (training window; play-by-play in action-number order before 2026-10-10; P2 n = 150).
- T042, T064 UNCERTAIN (pre-v2 stints). T043-T046 UNCERTAIN (no bounds; 2025 likely in the calibration split) - this is the deployed P(play)/minutes hurdle.
- T085-T090, T096-T097 UNCERTAIN in size (starter proxy; T-60 comparator lacked recent minutes); direction stands.
- T160-T161, T180-T182, T475 UNCERTAIN (underpowered or no CI). T177 UNCERTAIN (no comparator; 2025 rows in the export).
- T176 INVALID (RAPM built on pre-v2 stints, 47-49% within 1 min of box; could flip). T185 INVALID (both F12 checks on pre-v2 stints). T315 INVALID as a gate (coverage later repaired; question answered by T420-T423).
- T441-T445 INVALID (SEQ training window and proxy19 report tokens; E3 failed only an era probe an id-aware network cannot pass). T452 INVALID (failed only against a future-leaking placebo). T473 INVALID as a negative (failed only a shuffle guard that fires ~19% on honest shuffles; T473's rule outcome stands).
- VALID*: T004, T061-T063, T098-T101, T110-T123, T162-T175, T178, T244-T279, T284-T299, T382-T388, T440, T446-T449, T451, T453-T454, T464-T466 (flaw present, cannot flip).
Redo order: (1) SEQ learning curve on 2023/24, windows 1-5 seasons, W_ALL with era flags, ~1 T4 hour; (2) T473 via FULL_SHADOW_LIVE; (3) deployed P(play) hurdle via PPLAY_INTO_PROPS_LIVE; (4) WORKLOAD pts with a past-only placebo on FULL; (5) LINEUPS_KNOWN re-baseline vs R; (6) JOINT learning curve; (7) RAPM on rebuilt stints; (8) F12 re-measure; (9) PBP_GPT only if (1) shows starvation; (10) MLP arms only inside a neural-family rule.
```

### DECISIONS (draft)
```
2026-10-12 — Validity audit: 84 ledger ids INVALID, 29 UNCERTAIN (docs/reviews/validity_audit_2026-10-12.md).
What: annotation block appended below the ledger (no row edited); the neural sequence results (T065-T068, T441-T445), RAPM (T176), F12 (T185),
WORKLOAD pts (T452) and the T473 negative are no longer cited against their ideas. First redo: a SEQ learning curve on 2023/24 (windows 1-5 seasons,
W_ALL, era flags, 3 seeds, about 1 T4 hour, spends no holdout). Production unchanged; the deployed P(play) hurdle has no valid confirming test
and is measured by the live P(play) shadow. Why: Devin, "an invalid test is not a null". What would reverse it: a re-run under the fixed
conditions that reproduces the original verdict (the label then returns to VALID with the new row cited).
```
