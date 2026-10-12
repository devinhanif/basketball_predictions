# Ledger audit part 3: T300-T458 and the four 2026-10-11 reports (2026-10-12)

Read-only audit by the modeler. No fits of anything that could become a claim, no season 2025 loaded, `nba.duckdb` and the stored pairs opened
`read_only` / read-only with `season <= 2024`. Trigger (Devin, 2026-10-12): "look at all failed tests and see if we missed anything similar to
RECENT_ROLE... you've done valuable work you're not stringing together." Slice: `docs/TEST_LEDGER.md` T300-T458 and their reports/prereg/review docs, plus
`reports/confusion_2026-10-11.md`, `decision_scoring_2026-10-11.md`, `market_slow_2026-10-11.md`, `over_anatomy_2026-10-11.md`.
Patterns: P1 component gap, P2 consistent near-miss, P3 descriptive never followed up, P4 slice win masked, P5 oracle gap, P6 possibly invalid
result, P7 connection. Every number below is from a stored file (path given) unless it is marked EXPLORATORY (my own descriptive crosstab on
2023-24 data, labelled, not a claim, scripts in the session scratchpad and not committed).

## 0. The string Devin asked for (read this first)

**One weak component sits upstream of four separate results.** Production's minutes path (`nba/props/minutes.py: predict_minutes`: shrunk
trailing-150 play rate and minutes given played; the P(play) hurdle) uses no T-60 report tag, no last-game minutes and no recent-role columns.
RECENT_ROLE_HOLDOUT (T458) fixed it for P(play) and measured the gain against WORKLOAD's comparator C (-0.0205 [-0.0232, -0.0179], n 33,061).
But C is not production. The deployed raw hurdle scored log loss 0.3419 / 0.3558 (2023 / 2024, `reports/minutes_v2.md` secondary table) while C
scored 0.2832 / 0.2815 and R scored 0.2663 / 0.2649 (`reports/prereg_recent_role.md`). So R beats the deployed hurdle by about **-0.076 / -0.091**
log loss (point differences; the 2024 row sets differ by 141 rows, so no CI), not -0.02. The same weak path is the input of:

1. **Unconditional legs / parlays** (`forward.py _ctx_row`: `p_play` enters only `mean_uncond` and `p_ge_uncond`). In flight: PPLAY_INTO_PROPS_LIVE.
2. **TEAM_BUDGET (T446-T449)**: the budget `e_i = p_play x mu` came from this path (sum 264.2 / 258.6 vs 241.5, sd 46; `docs/prereg/TEAM_BUDGET.md`).
   "The factor is mostly noise" was measured with the noisiest available `e_i`. Candidate C5.
3. **ENSEMBLE E1 router (T440)**: its gate `g = P(minutes >= 5 | plays)` is the survival of this same minutes path with `use_game_context=False`, "no report"
   (`docs/prereg/ENSEMBLE.md`, E1 paragraph). The rule's own named follow-up (a g with the T-60 report and T-30 lineups, T445) has not been run. Candidate C2.
4. **WORKLOAD pts (T452)**: the LOAD columns were appended to the mean and scale heads only; the A0 lower-tail mixture's stored `pi` (P(short | plays)) was
   held fixed (`research/eval/workload_eval.py: run_props_arm` uses `load_pic()`), and `pi`'s feature list `COMMON_FEATURES` (`nba/props/context_residual.py:690`)
   has `min5 / min10 / min40 / last_starter` but no last-game minutes. The adversary showed the pts gain runs through minutes (D3: A3W - A3 = -0.0009 of B - A0 = -0.0082).
   Candidate C2c.

Earlier hints that this was visible two days ago: LINEUPS_KNOWN P(play) fair-baseline -0.0376 (T096); MINUTES_V2 `p_v2` beat the raw hurdle by -0.0486 [-0.0531, -0.0443]
(2023) / -0.0577 [-0.0622, -0.0532] (2024) and was filed "Secondary: P(play) (no pass/fail)" because the rule's only metric, conditional props CRPS, cannot
move with P(play) (RECENT_ROLE.md says so in its own Stage 2 paragraph); PPLAY_CONTEXT found the tag carries it (-0.0084 / -0.0124, T316); OWN_TAG (T326-T338)
then found the tag moves no conditional props stat, and DECISIONS 2026-10-11 wrote "the tag's information lives in P(play)". **Pattern: a P(play)-side improvement judged on a
metric P(play) cannot reach registers as a null.** I found no other T300-T458 test of that shape that is still open (OWN_TAG, PPLAY_CONTEXT, WORKLOAD P are closed loops).

## 1. Candidates

Prior = my probability that the candidate's pre-registered primary condition passes in both seasons with its guards (not that "something is there").
Rules are DRAFT sketches; none exists as a file yet. Floors are the house floors (CRPS -0.005 integer support; log loss -0.005 for a classifier).

| # | Pattern | T rows | Evidence (stored file) | Cheapest honest next test | Venue (holdout state) | Prior |
|---|---|---|---|---|---|---|
| C1 | P1, P6 (wrong metric) | T451-T458, MINUTES_V2 secondary (T162-T173), T316 | R vs deployed raw hurdle about -0.076 / -0.091 log loss (see section 0). Rehearsal (descriptive, 2024 walk-forward, n 32,838 rows / 1,320 games, `reports/prereg_pplay_into_props_live/rehearsal.json`) unconditional integer CRPS, R minus raw production P(play): pts -0.1033 [-0.1132, -0.0932], reb -0.0499 [-0.0545, -0.0455], ast -0.0207 [-0.0231, -0.0184], fg3m -0.0105 [-0.0117, -0.0092]; versus the previous-row placebo: -0.0610 / -0.0267 / -0.0123 / -0.0056; the Platt map (off since 2026-10-11 14:10) was +0.1656 worse than raw on pts. | **Already in flight** (harness `research/eval/pplay_into_props_live.py`, untracked; its prereg doc was not on disk when I looked). Two additions: report the effect against the deployed hurdle as well as against C; and state which product surface counts a DNP as a loss, because P(play) only matters where an unplayed leg is not voided (DK voids it: the decision-score legs drop DNP). | Live 2026-27 forward log only. R / C' P(play) 2025 touch is spent (T458, log row b47d212); no stored 2025 A0 components exist for props (holdout results.json `props_secondary`), so a 2025 props-with-R run would be a second touch on R and not clean. | 0.9 that R > deployed hurdle on log loss; 0.5 that it matters to a dollar decision |
| C2 | P5, P7, P1 | T440-T445, T382-T388, T452 | (a) E1 pts -0.0053 [-0.0109, +0.0001] (2023, n 27,619) / -0.0012 [-0.0073, +0.0046] (2024, n 27,583) vs its oracle gate -0.0191 / -0.0136 (T440; oracle descriptive). The gate is the weakest component (section 0, item 3). (b) E2 learned blend, pts -0.0110 [-0.0149, -0.0070] / -0.0131 [-0.0171, -0.0092]; realised minutes < 5: +0.057 (n 2,383) / +0.055 (n 2,326); 5-20: -0.016 / -0.027; > 20: -0.018 / -0.016 (`reports/prereg_ensemble/score_results.json`); by my arithmetic from those slices the rows with >= 5 minutes gain about -0.0174 / -0.0194, 3.5-3.9x the floor, and the near-DNP rows give back 31% / 26% of that gain. XGBoost showed the same split (T387: >= 5 min -0.0148 [-0.0214, -0.0085], < 5 min +0.162 [+0.145, +0.180], 2024). Two independent models, one pattern. (c) EXPLORATORY crosstab, 2023-24 played rows n 55,320: in the min10 10-20 stratum P(minutes < 5 given plays) is 37.6% after a game under 5 minutes (n 1,451) vs 7.6% otherwise (n 12,966); P(short, < half of min10) 46.0% vs 12.5%; min10 20+: 20.0% (n 120) vs 0.4% (n 32,964). Last-game minutes is a strong state variable for exactly the rows the blends and the router mishandle; `pi` and `g` never see it. | **ROUTER_G2** (one rule, three arms, stored artefacts): g' = walk-forward LightGBM P(minutes >= 5 given plays) on COMMON_FEATURES + the T-60 report tag/flags + the five RECENT_ROLE columns (the same 55k rows; about 20 small classifiers). Arms: E1' = g'.A1 + (1-g').A0 using the stored XGBoost grid; E2' = g'.E2-blend + (1-g').A0 using the stored SEQ OOF (no retrain); pi' = A0's mixture re-scored with `pi` refit on +5 columns (stored components, only pi changes). Declare before the freeze that routing is post-hoc by origin (as ENSEMBLE did for E1) and add the pre-tip-known hard rule "game_id prefix 004 -> A0", because the playoff slice (E1 +0.016 / +0.050, E2 learned +0.016 in 2024) breaks the +0.01 guard independently of g. Primary: integer CRPS vs A0, floor -0.005, both seasons, game-clustered CI, BH over the cells actually declared; secondary: classifier log loss of g' and of pi'; guards as ENSEMBLE plus minutes < 5. Numpy rescoring plus classifier fits: minutes, Mac CPU, no Colab. | 2023 select / 2024 report; 2023 is not independent for A1 (its XGBoost parameters were selected on 2023). Clean confirmation: one 2025 touch for E1' (XGBoost A1 was never evaluated on 2025; the ctxres_v3 hybrid touch was VOID and unrun, HOLDOUT log 2026-10-08), local trees. E2' on 2025 needs a Colab SEQ run: not cheap. g' itself is a new model, not R. | E1' pts 12%; E2' pts 10%; E2' count stats under 3% (floors); pi' props CRPS under 5% (the whole mixture is worth -0.011 on pts), pi' log loss floor 50% |
| C3 | P7 (pre-freeze action) | T442, T440, T452 | `docs/prereg/NEAR_MISS_BUNDLE.md` (DRAFT, not frozen): its ROUTER member reuses the stored weak `g` (`data/ensemble/g_pts.npz`, "recomputed ... equals the stored g exactly"), its BLEND gate context is E2's five columns (`log1p(n_prior), starter10, n_rep_out, has_report, team_game_no`) with no near-DNP signal, and LOAD enters the mean/scale heads but not `pi`. Its own pass rule includes minutes < 5 <= +0.01 and postseason <= +0.01, the two guards E2 failed (+0.055 and +0.016). The draft already forecasts "bundle better than A0, SEQ carries it, guards fail". | Not a new test: an edit decision before the freeze. Options for Devin: (1) freeze as is and accept the likely clean negative; (2) add g' (C2) and the postseason hard route to the gate before the freeze and disclose it as post-hoc by origin; (3) run ROUTER_G2 first, bundle second. | n/a (design). Note the draft says membership may not change; gate context and `pi` are not membership. | Bundle passing any stat as drafted about 5% (the draft's own figure); with g' and the playoff route about 10-12% on pts |
| C4 | P2, P6 (guard cannot pass) | T443, T445 | E3 = SEQ trained with 2019-21 history (report tokens zeroed), 50/50 blend with production, **reb**: -0.0078 [-0.0096, -0.0060] (2023) / -0.0070 [-0.0089, -0.0052] (2024), n 27,619 / 27,701; core, calibration, slices (minutes < 5 +0.009 / +0.033, postseason -0.010 / -0.001), concentration and missingness all pass in both seasons; shuffled-label blend +0.0100 / +0.0096 (harm, as it should be); no-report-token knockout -0.0063 / -0.0044; E3 minus E2 reb -0.0062 [-0.0075, -0.0049] / -0.0035 [-0.0047, -0.0023]. Verdict `FAIL_ERA` only. The era probe (embedding classifier AUC) is 0.9986 for 2019 v 2022 against a limit of 0.883, but the placebo pairs among CURRENT seasons are already 0.840 (2022 v 2023), 0.863 (2022 v 2024), 0.767 (2023 v 2024) and rise with distance: a network with player-id embeddings (15% id dropout) separates seasons by roster turnover alone. As written the guard cannot be passed by an id-aware model with history (non-negotiable 8, the F9/F12 class). ast: -0.0056 / -0.0040 (2023 only); fg3m -0.0046 / -0.0037; pts -0.0274 / -0.0173 but fails calibration and the near-DNP slice. | **Not a re-score** of the stored E3 (the numbers are seen; a new guard on the same OOF is not independent). New rule ENSEMBLE_E3_REB: SEQ with player ids ablated (or hashed per season), history 2019-21 as E3, reb (and ast declared as a second cell), era guard replaced by a check that can pass (id-ablated probe AUC within the current-season placebo band), same blend and guards, full budgets, one Colab run with the committed rule, `configs/cost.yaml` budget and the exact notebook link. | E3 was never evaluated on 2025: one touch available, needs Devin's word and a logged row first. Production cost is a transformer in the daily path for -0.007 CRPS on 1.3 (0.5%): value is small even if real. | 25% that reb survives id ablation and 2025; 5% that it is worth deploying |
| C5 | P1, P5, P6 | T446-T449 | TEAM_BUDGET T1 pts +0.1017 [+0.0910, +0.1120] / +0.1179 [+0.1062, +0.1302]; half strength +0.029 (pts 2024); oracle realised shares O2 pts -0.180 [-0.194, -0.166], reb -0.066, ast -0.026, fg3m -0.016. `e_i = p x mu` from the weak hurdle (section 0). The oracle shows the closed-system idea has value; the test used the noisiest estimate of availability. A correct budget conditions on the availability SET, not on marginal expectations, which is a T-30 object. | Label-free gate first (no outcome): recompute `sum e_i` per team-game with p from R and mu from M2 (or props min10) and compare its sd with 46 / 258.6; stop if the sd does not fall below about 25 minutes. If it does, rescore T1 at gamma 1 and 0.5 from the stored A0 grids (numpy only). | 2023 / 2024 for the gate and score; a T-30 version only on the live log (historical inactive lists do not exist: T089 notes). No 2025 touch requested. | 4% to reach the floor |
| C6 | P2 (weak), P3 | T415-T419, T237, decision scoring | MARKET_SHRINK 2024 B2 minus market log loss: reb -0.0014 [-0.0024, -0.0005] (Holm 0.014), fg3m -0.0012 [-0.0022, -0.0001] (Holm 0.042); but the planted shuffle also beat the market on ast (-0.0004) and the red team (T237, reb vs Pinnacle) found an intercept-only correction with no model gives -0.0012 [-0.0023, -0.0001] while the model's player-level share is -0.0011 [-0.0022, +0.0001]. ROI@EV>=5% reb +1.9% [+0.5%, +3.4%] (n 15,620) is 1 of 20 uncorrected cells. REB_LIVE_2026_27 (DRAFT) already carries reb with a stated 12% prior. | Add fg3m as a second declared cell to REB_LIVE with BH over 2 (cost: nothing; same pipeline), and add the T237 decomposition (intercept-only arm) as a declared diagnostic so a recalibration win is not read as a model win. | Live log only. | reb live ROI lower bound > 0 at season end: 10% (power 42% even if +2% is real, per the draft); fg3m 4% |

Top five in one line each are in the hand-back. C1 is real and in flight; C2 and C4 are the two with a stored, seen effect and a mechanism; C3 is a
decision for Devin; C5 and C6 are long shots recorded so they are not re-derived.

## 2. Harnesses that share WORKLOAD's defects

WORKLOAD's two defects (adversary, `docs/reviews/workload_review_2026-10-12.md`): D1 the shuffled-label arm is inert (a label-free arm trained
on permuted labels is compared with an unshuffled trained base, so it always loses by construction); D2 the placebo permutes a feature block within
player-season (or team-season), which puts future values on earlier rows.

| Harness (file) | D2 future-leaking placebo | D1 inert shuffle | Did it touch a verdict? |
|---|---|---|---|
| `workload_eval.py` | yes (`permute_within_player_season`, `permute_within_team_season`) | yes | yes: P passed on the 2024-only placebo reading, pts wounded |
| `advanced_rates_eval.py` | **yes**, `placebo_permute` jointly within (player, season) | **yes**, `_attack_cell` compares the shuffled B with the unshuffled A0 (`shuffled_ok = g_shuf >= -abs(g_full) * 0.2`); never executed because no cell passed | The null itself stands (B minus A0: no cell reaches -0.005, best 2024 -0.0024). But "the placebo is better than the real columns in 14 of 15 cells" (T439) is **contaminated**: the USAGE_PACE / pts placebo beats A0 by -0.0218 [-0.0267, -0.0169] (2023) and -0.0151 [-0.0196, -0.0108] (2024), 3-4x the floor, which is what a column set carrying the player's season-level future usage and pace would do. It is an oracle for player level, not a null. |
| `team_budget_eval.py` | **yes**, `permute_within(..., team_id, season)` jointly, train and test rows | **yes**, shuffled T2 factor scored against A0 = factors of 1 (a noise factor can only lose; `shuffled_ok = g_sh >= -mde`) | Verdict HARM (+0.10) is unaffected |
| `coach_phase_eval.py` | no (coach identities permuted within season and phase, `placebo_map`; no time leak) | **yes**, `walk_forward_delta(permute=True)` ridge on a permuted target vs the unshuffled base | Never ran (T450 gate stop). Fix before any COACH_PHASE_2 |
| `four_factors.py placebo_ff`, `f11_lineup_context.py placebo_frame` | **yes**, joint permutation within team-season | not checked | Both nulls; the placebo tied the real arm, so a harder placebo cannot change a null |
| `ensemble_eval.py` | no placebo for the props arms | **no**: the shuffled arm is a SEQ trained on permuted labels BLENDED with production, so it can fail, and did (E3 learned reb `VOID_SHUFFLE`) | n/a |
| `game_market_context_eval`, `own_tag_eval`, `pplay_context_eval` | no: within-date derangement; within team-date; across team-games within season (other teams' as-of values, no same-entity future) | their shuffles not re-read | clean as far as I looked |
| `market_shrink_eval`, `model_clv_eval` | no | no: `p_model` permuted within (game, stat, book), non-inert (MARKET_SHRINK's planted shuffle failed on ast) | n/a |
| `recent_role_eval`, `recent_role_holdout`, `near_miss_bundle_eval` (draft), `pplay_into_props_live` | fixed: past-only placebo (`research/recent_role/placebo.py`) | fixed: both arms shuffled (R-shuf minus C'-shuf), SHUF-C in the A0-null world | n/a |

Practical rule: a template for the next freeze should import `research/recent_role/placebo.py` (past-only) and the both-arms shuffle, and refuse the
`permute_within_*` helpers. D2 makes a placebo harder to beat, so it cannot create a false pass; it can only mislabel a real gain as "not beating the
placebo" (WORKLOAD cell M: B minus the leaky placebo is -0.034 [-0.044, -0.025] / -0.043 [-0.052, -0.033] min, CIs below 0 in both seasons, rejected
against a -0.05 margin; the cell M floor of -0.10 min was also missed, so the verdict stands, but the margin was judged against an oracle-flavoured null).

Other P6 items: (i) **Data fixed after the run.** The 2026-10-11 clean-up (118 restored 2024 games, stint rebuild) moved the stored minutes table by up to
1.53 min for 14% of 2024 regular-season rows (T450) and the production OOF reference from 3.1884 (n 27,583) to 3.187553 (n 27,701) on 2024 pts. Results on the old table:
MINUTES_V2 (T162-T173), FOUR_FACTORS, F8, F11, ROUTER_TIME_WEIGHTED (T254-T299, all n 27,583). None is a near-floor positive; they are nulls or closed
rules, so I would not re-run them. Everything from OWN_TAG (T326) on reports n 27,701. (ii) **Wrong comparator.** WORKLOAD's C and the deployed hurdle (section 0). (iii) **Wrong
support.** None found in T300-T458: every props rule here scores on the integer grid for every arm.

## 3. Holdout state per model (`docs/HOLDOUT_ACCESS_LOG.md`)

Standing caveat in the log: "NO VIRGIN HOLDOUT" at the research-programme level since 2026-10-08. The columns below are per model.

| Model | 2025 state |
|---|---|
| injury-Elo v2 (win) | **spent**, 2026-10-08, CONFIRMED (-0.0102 [-0.0180, -0.0026], 19:00 ET tip proxy; Elo effect nil under real tips) |
| context-residual props v2 | **spent**, 2026-10-08, confirmed; the pts figure is probably overstated by 0.003-0.004 CRPS (proxy tip, red team notice) |
| ctxres_v3 hybrid (XGBoost v12) | touch VOID, not run (leak in `opp_adjusted_ridge`); **virgin**. XGB_PTS_VS_LT's leak-free A1 is a different refit; first touch available |
| Systems replay 2026-10-09 (production + `_int`, `_lt`, `_t30` shadows) | not confirmatory; shadow deltas SEALED; shadows' confirmation is the 2026-27 forward test. Treat the lower-tail mixture A0 and the T-30 arm as "run on 2025, unread": a confirmatory touch of either needs a Devin decision |
| P(play) R and C' (RECENT_ROLE_HOLDOUT) | **spent**, 2026-10-12, PASS (R minus C -0.0205 [-0.0232, -0.0179], n 33,061). The props-with-R unconditional score on 2025 was not run |
| SEQ E2 / E3, XGBoost A1 / E1, AL_LOAD, `pi'`, `g'`, M2 / `p_v2`, OWN_TAG, bundle | never touched. Venue rule: one logged touch each, Devin's word, row committed first |
| Season 2026 | becomes the holdout at 20 games (rollover TODO in the log); nothing to score today. The live forward log is the only clean venue for C1, C5 (T-30), C6 |

## 4. Row-by-row classification and the "nothing here" list

| Rows / group | Pattern | Verdict (one line) |
|---|---|---|
| T300 MINUTES_HAZARD gate | P6 | Stopped at the gate correctly (label missing for 5.79% of sub-10-minute rows). Nothing |
| T301-T314 MINUTES_HAZARD_2 | none | On-court hazard worse than M2 by +0.55 / +0.58 min, still 0.44-0.47 min behind with the actual margin as an input; props null. Structure loses to features. Nothing |
| T315, T325 HISTORY_WINDOW(_2); T318-T319 ELO_HISTORY; T420-T423 HISTORY_WINDOW_3 | P2 (2023 only) | History equals about one season of data and only while the window is short; Elo forgets it (2024 delta exactly 0.0000). Production has three seasons. The single exception is the data-hungry SEQ (C4) |
| T316-T317 PPLAY_CONTEXT | P1 | The tag carries P(play) (-0.0084 / -0.0124); the team-habit terms are noise. Followed up by WORKLOAD and RECENT_ROLE; Platt off. Loop closed |
| T320-T324 MARKET_LINE_CLASSIFIER | none | Worse than the book in 2023 (pts +0.0019 [+0.0001, +0.0039]); one-weight blend beats it. Nothing |
| T326-T338 OWN_TAG | P6 (wrong metric, closed) | Conditional props null (best cell -0.0002); the tag lives in P(play). Loop closed |
| T339 CALIBRATED_GRID | none | Shuffled-label control gains more than every map (generic shrinkage). See also the EXPLORATORY note below |
| T340-T381 MODEL_CLV | P3 | CLV +0.0009 to +0.0040 in every cell, but the no-model shuffle null earns +0.0018 to +0.0026 (floor +0.005). L1: the direction of Pinnacle's pre-tip move is predictable from the Pinnacle-DK gap alone (0.6730 vs constant 0.6934). EXPLORATORY follow-up below: the gap does not predict outcomes. Nothing |
| T382-T388 XGB_PTS_VS_LT | P4, P7 | Complementary halves; carried into C2 |
| T389-T414 GAME_MARKET_CONTEXT | none | Market spread and total add nothing beyond Elo margin (r 0.90) and 20-game rates; every real arm within +-0.001 of its placebo (placebo construction is safe). Own history is 13% of skill. Nothing |
| T415-T419 MARKET_SHRINK | P2 weak | C6 |
| T424-T439 ADVANCED_RATES | P6 (placebo) | Null stands; the "placebo beats real" statement is invalid (section 2). The leaky placebo is an oracle for player level worth about -0.015 to -0.022 pts CRPS: headroom for a better talent estimate, which is a representation idea, not new information. Nothing |
| T440-T445 ENSEMBLE | P5, P7, P2 | C2, C3, C4 |
| T446-T449 TEAM_BUDGET | P5, P1 | C5 |
| T450 COACH_PHASE gate | P6 | Coach habits add +0.0002 (TEAM_BUDGET T2 minus T2-noCoach), blowout habit reliability r -0.07. Coach effect is capped by its own rule. Nothing; if COACH_PHASE_2 is written it must fix D1 |
| T451-T458 WORKLOAD, RECENT_ROLE, HOLDOUT | P1 | The case that started this audit. Remaining loose end is the sizing against the deployed hurdle (C1) |
| confusion report | P3 | Model calls OVER on 29.3% [28.8, 29.8] of 106,999 legs; model minus market lean accuracy -0.4 pts [-0.8, -0.1]; when they disagree the model is right 48.9% (pts, n 10,940). Explained by a level shift (next row). Accuracy is not profit. Nothing. Period/quarter markets: no price history exists to test (free sources only; a paid add-on in the odds API, Devin's call); injury-Elo-mapped Q1 / 1H accuracy 55.6% / 59.7% with no market is a product surface, not evidence of an edge |
| decision-scoring report | P3 | The reb ROI lead is already REB_LIVE (C6). Over-confidence (said 66.7%, hit 57.0%, n 38,598) is selection at the line (T339). Nothing |
| market-slow report | P3 | Line moves are informative (57.8% [57.0, 58.6] follow-the-move) and bench players' prices learn more (Brier gain contrast +0.023 [+0.001, +0.043], borderline). Already carried by MARKET_UPDATE (DRAFT). Add: declare bench as a slice there. Nothing else |
| over-anatomy report | P3 | Minutes surplus is 7-20% of the variance of y minus line and unknowable at T-60; the rate surprise is 79-88%. Count-stat upper tails are slightly too fat (ast realised / predicted 0.85, 0.72, 0.55 at k = 4, 6, 8) but this only matters for far alt lines; revisit only if ladders become a product surface. Nothing |

**EXPLORATORY checks I ran to size priors (descriptive, 2023-24, DraftKings T-60 stored pairs, not claims):**
* Model P(over) is below the realised over rate at the line in every stat-season, all 8 cells with CI above 0 (over minus p_model, game-clustered 300 draws): pts 2023 +0.031 [+0.023, +0.039], 2024 +0.034 [+0.027, +0.042]; reb +0.029 / +0.020; ast +0.038 / +0.027; fg3m +0.036 / +0.047 (n 9,650-15,882 per cell); and in all 12 role buckets (bench +0.045 to +0.052, starters +0.026 to +0.040). It explains why the model calls under 70% of the time.
* But it is not an edge: shifting p_model by +0.033 and betting EV >= 5% at the actual prices gives ROI -0.8% [-1.6%, +0.1%] (n 52,101) vs -0.1% (n 58,968, matches the decision-score report) unshifted; by side (unshifted) overs -2.5% [-4.2%, -0.6%] (n 11,240), unders +0.4% [-0.5%, +1.5%] (n 47,728). The book shades the lines; a level fix is worth about 0.001-0.002 log loss (T339 floor 0.002). Nothing here.
* Following Pinnacle against DraftKings: same-line pairs 61,169 (57.2% of DK legs); betting the DK side Pinnacle favours when |gap| >= 3 pts: ROI +0.8% [-1.1%, +2.6%] (n 10,015; 2023 +2.4%, 2024 -0.8%); >= 5 pts: +1.2% [-3.7%, +6.0%] (n 1,343). Nothing.

**Injury-Elo built on OUT / doubtful flags while a P(play) model exists (Devin's example): nothing, bounded.** INJURY_ELO_T30 (T160-T161; `reports/injury_elo_t30.md`) resolves every status
with the T-30 inactive list and finds -0.0021 [-0.0060, +0.0021] (2022), +0.0006 [-0.0026, +0.0037] (2023), -0.0008 [-0.0044, +0.0026] (2024, n 1,315) log loss. A probabilistic
P(play)-weighted value cannot do better than a perfect resolution of those statuses; prior under 3%; RECENT_ROLE already declared Stage 2b not applicable. If Devin wants it
anyway the test is one extra ridge feature (expected value lost = sum V_i (1 - p_i)) on the existing walk-forward, n 1,318 / 1,315 games, MDE about 0.004 against the -0.004 floor.

## 5. What I could not verify

* R vs the deployed hurdle is a difference of point estimates on different row sets (2024: 32,986 vs 33,127); there is no paired CI. The unconditional rehearsal gives a paired CI for pts / reb / ast / fg3m but is labelled descriptive.
* That the deployed hurdle today is still the trailing-150 raw hurdle: taken from T457 and the TEAM_BUDGET doc, not re-read in the daily path.
* The E1 gate definition was read from the rule text and the `g_scores` docstring (`ensemble_eval.py:355`), not stepped through.
* `four_factors.py` / `f11_lineup_context.py` shuffles were not read; only their placebo constructions.
* The 25% / 12% / 10% priors are judgement, not computed.
* I did not read the full text of the COACH_PHASE, F10A, HISTORY_WINDOW gate JSONs beyond the ledger rows.

## 6. Files and scratch

Written: this file. Scratch (not committed, session scratchpad): `bias.py`, `side.py`, `short.py`, `gap.py` (the EXPLORATORY crosstabs). No `nba.duckdb` write, no season 2025, no fit that
produces a claim, no ledger or holdout-log edit. Proposed ledger or DECISIONS rows: none drafted (an audit adds no test row).
