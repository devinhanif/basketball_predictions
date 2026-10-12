# Which inputs matter for each prop (points, rebounds, assists, threes), 2026-10-12

Descriptive. Seasons 2023 and 2024 only (2022 = warm-up history); season 2025 never loaded; no claim, no production change, no ledger row.
Code: `research/eval/feature_significance.py` (stages `arms`, `score`); numbers in `reports/feature_significance_2026-10-12.results.json`.
n = 55,320 walk-forward OOF player-games (played rows, >= 5 prior games) in 2,638 games, 18 month blocks, identical rows for every arm and every stat.

## Summary for Devin (one screen)

- **What I did.** For each prop I refit the production model (recency mean + LightGBM correction, same config, same month-by-month walk-forward)
  with one *group* of inputs removed, and measured how much worse the forecast gets (integer CRPS, lower is better; "+0.0245" = removing the group
  costs 0.0245 CRPS). Same rows, paired by row, 95% CIs clustered by game, Benjamini-Hochberg across the 16 tests (15 drop groups + the recent-role add) within each prop. Groups hold near-duplicate
  columns together (the m5/m40/m100 averages, the minutes averages, rest/back-to-back, injury, and so on). I removed by *refitting*, not by shuffling a column,
  because a within-player shuffle leaks future information (the placebo defect in `docs/reviews/ledger_audit_2026-10-12.md`).
- **The one input that matters everywhere is the official injury report** ("who is out, and how many minutes/points they usually take"):
  removing it costs +0.0245 CRPS for points (0.8% of the 3.170 total), +0.0073 rebounds, +0.0047 assists, +0.0026 threes -- the largest for every prop, 2-3x the next group.
- **Top 5 significant inputs per prop** (BH q < 0.05, ranked by cost of removal):
  - points: injury/vacated (+0.0245), games history (+0.0074), starter flags (+0.0062), season timing (+0.0043), opponent recent scoring/allowed (+0.0035)
  - rebounds: injury/vacated (+0.0073), games history (+0.0036), minutes averages (+0.0031), starter flags (+0.0027), the player's own m5/m40/m100 rebound averages (+0.0025)
  - assists: injury/vacated (+0.0047), starter flags (+0.0017), games history (+0.0015), opponent recent scoring/allowed (+0.0010), minutes averages (+0.0009)
  - threes: injury/vacated (+0.0026), own m5/m40/m100 three-point averages (+0.0009), games history (+0.0009), opponent recent scoring/allowed (+0.0007), starter flags (+0.0007)
- **Do not read the small ones as precise.** Refitting the *same* model with a different random seed moves CRPS by about +/-0.0005 (the reference row in each table).
  Anything under about 0.001 is inside that refit noise even when its game-clustered CI excludes zero, because that CI only captures sampling of games, not refit noise.
  Only 4 groups clear the project's practical floor of 0.005: injury (points and rebounds), games history (points), starter flags (points).
- **The new recent-role columns** (last game's minutes, last-3 and last-5 average minutes, minutes in the last 3 and 7 days) add real value on top of production:
  -0.0086 CRPS for points [-0.0111, -0.0062], -0.0029 rebounds, -0.0020 assists, -0.0007 threes (all BH-significant). For points that is the second largest
  effect on the page after the injury report, and larger than every production group except injury.
- **Real hit and real miss (points, from the injury information).** Hit: Jackson Jr., game 0022400453, 8 rotation teammates out (about 103 points per game
  of usual scoring unavailable): the full model's median was 23, he scored 38; it was the biggest gain from the injury columns among 20-point scorers. Miss: Ayton,
  game 0022301183, 7 teammates out: the model with injury information raised him to a median of 22 and he scored 6, the biggest loss among 20-point scorers. Across all
  55,320 rows the injury columns helped 54.4% of forecasts, i.e. they are a small average edge, not a per-game oracle.

## Method (fixed before scoring)

- Model: `nba.props.context_residual` as deployed (config `configs/context_residual.yaml`, `n_jobs` lowered 4 -> 2 for the shared machine; LightGBM runs with
  `deterministic=True`), lower-tail off as in production. 18 monthly refits per stat per arm, training on everything dated before the month, newest 15% as the conformal calibration window.
- All four stats scored on the integer grid (quantiles mapped by `ceil(q - 0.5)`), as the project rule requires for every arm. Production maps only reb/ast/fg3m; the points
  numbers here therefore are not the continuous-grid production numbers.
- Arms per stat: full; full with seed 1 (refit-noise reference); one drop-group arm per group (15); and "add recent-role" (the 5 columns `wl_min_last`, `wl_mean_min_l3`, `wl_min_3d`,
  `wl_min_7d`, `wl_mean_min_l5` from `research/workload`, appended to the full production feature list). 18 fits per block per stat.
- Group = the columns in the table row; the groups partition production's feature list exactly (asserted in code). The recency center `m10_<stat>` that the model corrects is never removed; only the copy
  of it that the heads see is in "own_m10_all_stats".
- Effect = mean over rows of (CRPS of the arm - CRPS of full); CI = 2,000-resample bootstrap clustered by game_id; p = clustered bootstrap two-sided; BH within stat over 16 p-values
  (15 drops + the add arm; the seed row is outside the family). "significant" = BH q < 0.05.
- LightGBM gain share is the mean-head gain normalised per fit and averaged over the 18 blocks. It is **not inferential**: gain is spread across correlated columns and is not a CRPS effect.
- Not done: no placebo shuffle (deliberately; see above), no interaction effects, no conditioning on a slice.

## Results by stat (ranked)

### pts (n = 55,320 OOF rows, 2,638 games, full-model integer CRPS 3.1702; refit-noise reference, seed 1 minus seed 0: -0.0002 [-0.0019, +0.0015])

| rank | group (columns) | CRPS change when removed [95% CI] | BH q | significant | LightGBM gain share (non-inferential) |
|---|---|---|---|---|---|
| 1 | injury_vacated (n_out_rot, vac_min, has_report, vac_pts) | +0.0245 [+0.0199, +0.0295] | 0.002 | yes | 24.5% |
| 2 | games_history (n_prior, n_season, gap_days) | +0.0074 [+0.0049, +0.0099] | 0.002 | yes | 7.8% |
| 3 | starter_flags (starter10, starter40, last_starter) | +0.0062 [+0.0040, +0.0085] | 0.002 | yes | 6.7% |
| 4 | season_timing (team_game_no) | +0.0043 [+0.0025, +0.0063] | 0.002 | yes | 3.2% |
| 5 | opp_r20_and_allowed (opp_r20_pts_for, opp_r20_pts_against, opp_allow_pts) | +0.0035 [+0.0015, +0.0056] | 0.004 | yes | 6.9% |
| 6 | minutes_avgs (min5, min10, min40, min_gap, min_gap10) | +0.0034 [+0.0015, +0.0053] | 0.003 | yes | 9.9% |
| 7 | own_avg_windows (m5_pts, m40_pts, m100_pts, role_gap_pts) | +0.0028 [+0.0004, +0.0052] | 0.042 | yes | 10.7% |
| 8 | own_rate_spread (rate_pts, sd10_pts) | +0.0026 [+0.0010, +0.0044] | 0.004 | yes | 7.6% |
| 9 | own_m10_all_stats (m10_pts, m10_reb, m10_ast, m10_fg3m) | +0.0024 [+0.0004, +0.0043] | 0.034 | yes | 10.7% |
| 10 | role_change_flags (since_flag, flag_dir, since_team) | +0.0019 [+0.0001, +0.0037] | 0.060 | no | 3.2% |
| 11 | home (is_home_f) | +0.0010 [-0.0006, +0.0026] | 0.292 | no | 0.4% |
| 12 | team_r20 (r20_pts_for, r20_pts_against) | +0.0008 [-0.0009, +0.0025] | 0.416 | no | 4.3% |
| 13 | position (pos_code) | +0.0005 [-0.0010, +0.0021] | 0.579 | no | 0.2% |
| 14 | rest_b2b (rest_days, b2b, opp_rest_days, opp_b2b) | +0.0005 [-0.0011, +0.0021] | 0.613 | no | 0.7% |
| 15 | elo_margin (exp_margin, abs_margin) | -0.0000 [-0.0017, +0.0017] | 0.958 | no | 3.3% |
| 16 | ADD recent_role (change when added) (wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5) | -0.0086 [-0.0111, -0.0062] | 0.002 | yes | n/a (not in production) |

### reb (n = 55,320 OOF rows, 2,638 games, full-model integer CRPS 1.3035; refit-noise reference, seed 1 minus seed 0: +0.0005 [-0.0002, +0.0013])

| rank | group (columns) | CRPS change when removed [95% CI] | BH q | significant | LightGBM gain share (non-inferential) |
|---|---|---|---|---|---|
| 1 | injury_vacated (n_out_rot, vac_min, has_report, vac_reb) | +0.0073 [+0.0055, +0.0091] | 0.001 | yes | 15.7% |
| 2 | games_history (n_prior, n_season, gap_days) | +0.0036 [+0.0027, +0.0046] | 0.001 | yes | 6.4% |
| 3 | minutes_avgs (min5, min10, min40, min_gap, min_gap10) | +0.0031 [+0.0021, +0.0041] | 0.001 | yes | 13.0% |
| 4 | starter_flags (starter10, starter40, last_starter) | +0.0027 [+0.0019, +0.0036] | 0.001 | yes | 6.5% |
| 5 | own_avg_windows (m5_reb, m40_reb, m100_reb, role_gap_reb) | +0.0025 [+0.0015, +0.0036] | 0.001 | yes | 12.3% |
| 6 | own_rate_spread (rate_reb, sd10_reb) | +0.0018 [+0.0010, +0.0026] | 0.001 | yes | 10.6% |
| 7 | role_change_flags (since_flag, flag_dir, since_team) | +0.0014 [+0.0007, +0.0022] | 0.002 | yes | 2.7% |
| 8 | rest_b2b (rest_days, b2b, opp_rest_days, opp_b2b) | +0.0012 [+0.0005, +0.0020] | 0.002 | yes | 1.2% |
| 9 | season_timing (team_game_no) | +0.0012 [+0.0003, +0.0019] | 0.005 | yes | 3.6% |
| 10 | opp_r20_and_allowed (opp_r20_pts_for, opp_r20_pts_against, opp_allow_reb) | +0.0008 [-0.0001, +0.0016] | 0.135 | no | 7.9% |
| 11 | position (pos_code) | +0.0007 [-0.0000, +0.0013] | 0.084 | no | 1.1% |
| 12 | team_r20 (r20_pts_for, r20_pts_against) | +0.0007 [-0.0002, +0.0015] | 0.158 | no | 4.3% |
| 13 | own_m10_all_stats (m10_pts, m10_reb, m10_ast, m10_fg3m) | +0.0006 [-0.0003, +0.0014] | 0.199 | no | 11.2% |
| 14 | home (is_home_f) | +0.0004 [-0.0003, +0.0010] | 0.327 | no | 0.4% |
| 15 | elo_margin (exp_margin, abs_margin) | +0.0002 [-0.0005, +0.0009] | 0.572 | no | 3.2% |
| 16 | ADD recent_role (change when added) (wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5) | -0.0029 [-0.0039, -0.0018] | 0.001 | yes | n/a (not in production) |

### ast (n = 55,320 OOF rows, 2,638 games, full-model integer CRPS 0.9031; refit-noise reference, seed 1 minus seed 0: -0.0004 [-0.0009, +0.0002])

| rank | group (columns) | CRPS change when removed [95% CI] | BH q | significant | LightGBM gain share (non-inferential) |
|---|---|---|---|---|---|
| 1 | injury_vacated (n_out_rot, vac_min, has_report, vac_ast) | +0.0047 [+0.0034, +0.0061] | 0.003 | yes | 17.8% |
| 2 | starter_flags (starter10, starter40, last_starter) | +0.0017 [+0.0010, +0.0024] | 0.003 | yes | 5.6% |
| 3 | games_history (n_prior, n_season, gap_days) | +0.0015 [+0.0007, +0.0022] | 0.004 | yes | 6.9% |
| 4 | opp_r20_and_allowed (opp_r20_pts_for, opp_r20_pts_against, opp_allow_ast) | +0.0010 [+0.0003, +0.0017] | 0.016 | yes | 8.1% |
| 5 | own_avg_windows (m5_ast, m40_ast, m100_ast, role_gap_ast) | +0.0009 [+0.0001, +0.0017] | 0.069 | no | 11.4% |
| 6 | minutes_avgs (min5, min10, min40, min_gap, min_gap10) | +0.0009 [+0.0002, +0.0016] | 0.027 | yes | 10.0% |
| 7 | season_timing (team_game_no) | +0.0007 [-0.0000, +0.0013] | 0.104 | no | 4.1% |
| 8 | role_change_flags (since_flag, flag_dir, since_team) | +0.0004 [-0.0002, +0.0009] | 0.313 | no | 2.0% |
| 9 | home (is_home_f) | +0.0002 [-0.0003, +0.0008] | 0.598 | no | 0.8% |
| 10 | own_rate_spread (rate_ast, sd10_ast) | +0.0002 [-0.0004, +0.0009] | 0.649 | no | 14.4% |
| 11 | rest_b2b (rest_days, b2b, opp_rest_days, opp_b2b) | +0.0000 [-0.0005, +0.0006] | 0.860 | no | 0.4% |
| 12 | own_m10_all_stats (m10_pts, m10_reb, m10_ast, m10_fg3m) | +0.0000 [-0.0006, +0.0007] | 0.860 | no | 9.4% |
| 13 | team_r20 (r20_pts_for, r20_pts_against) | -0.0002 [-0.0007, +0.0004] | 0.649 | no | 4.4% |
| 14 | elo_margin (exp_margin, abs_margin) | -0.0002 [-0.0007, +0.0004] | 0.649 | no | 3.6% |
| 15 | position (pos_code) | -0.0002 [-0.0007, +0.0004] | 0.649 | no | 1.1% |
| 16 | ADD recent_role (change when added) (wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5) | -0.0020 [-0.0028, -0.0012] | 0.003 | yes | n/a (not in production) |

### fg3m (n = 55,320 OOF rows, 2,638 games, full-model integer CRPS 0.5806; refit-noise reference, seed 1 minus seed 0: +0.0000 [-0.0004, +0.0004])

| rank | group (columns) | CRPS change when removed [95% CI] | BH q | significant | LightGBM gain share (non-inferential) |
|---|---|---|---|---|---|
| 1 | injury_vacated (n_out_rot, vac_min, has_report, vac_fg3m) | +0.0026 [+0.0020, +0.0033] | 0.005 | yes | 12.2% |
| 2 | own_avg_windows (m5_fg3m, m40_fg3m, m100_fg3m, role_gap_fg3m) | +0.0009 [+0.0003, +0.0016] | 0.010 | yes | 16.6% |
| 3 | games_history (n_prior, n_season, gap_days) | +0.0009 [+0.0004, +0.0013] | 0.005 | yes | 6.1% |
| 4 | opp_r20_and_allowed (opp_r20_pts_for, opp_r20_pts_against, opp_allow_fg3m) | +0.0007 [+0.0002, +0.0012] | 0.011 | yes | 9.8% |
| 5 | starter_flags (starter10, starter40, last_starter) | +0.0007 [+0.0003, +0.0011] | 0.005 | yes | 3.7% |
| 6 | own_rate_spread (rate_fg3m, sd10_fg3m) | +0.0006 [+0.0002, +0.0010] | 0.010 | yes | 11.9% |
| 7 | minutes_avgs (min5, min10, min40, min_gap, min_gap10) | +0.0004 [-0.0001, +0.0008] | 0.194 | no | 11.0% |
| 8 | home (is_home_f) | +0.0002 [-0.0001, +0.0006] | 0.479 | no | 0.6% |
| 9 | elo_margin (exp_margin, abs_margin) | +0.0002 [-0.0002, +0.0006] | 0.479 | no | 5.0% |
| 10 | season_timing (team_game_no) | +0.0002 [-0.0002, +0.0005] | 0.558 | no | 2.6% |
| 11 | position (pos_code) | +0.0001 [-0.0003, +0.0005] | 0.642 | no | 0.6% |
| 12 | role_change_flags (since_flag, flag_dir, since_team) | +0.0001 [-0.0003, +0.0005] | 0.786 | no | 2.8% |
| 13 | own_m10_all_stats (m10_pts, m10_reb, m10_ast, m10_fg3m) | -0.0001 [-0.0005, +0.0003] | 0.779 | no | 11.7% |
| 14 | rest_b2b (rest_days, b2b, opp_rest_days, opp_b2b) | -0.0002 [-0.0005, +0.0002] | 0.493 | no | 0.8% |
| 15 | team_r20 (r20_pts_for, r20_pts_against) | -0.0002 [-0.0006, +0.0002] | 0.479 | no | 4.4% |
| 16 | ADD recent_role (change when added) (wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5) | -0.0007 [-0.0012, -0.0002] | 0.021 | yes | n/a (not in production) |

## Reading the tables, honestly

- **Injury report vs the rest.** Gain share says injury/vacated carries 12-25% of the mean-head gain and removal cost says it is 2-3x the next input. This is the single-group-carries-it
  pattern the project treats as a leak fingerprint; it is not a new one: the injury report is a pre-tip public fact, stamped before the (real) tip-off, and it is the
  holdout-confirmed production win (CLAUDE.md). I did not re-audit its as-of logic here. A no-report game gets NaN (`has_report = 0`), not "nobody out".
- **"Games history" and "season timing" are probably proxies.** n_prior / n_season / gap_days (games played before, days since the last game) and team_game_no are not causal stories. Gap days
  capture returns from injury; team game number capture season phase (rest of the starters late in the year, trade-deadline role shifts). They carry real signal here, but I would not
  call them "what drives scoring".
- **Near-zero rows are not zeros.** Home, position, rest/back-to-back, Elo margin and team recent scoring are not distinguishable from 0 for points (all q >= 0.29) -- but they are largely
  redundant with each other and with the opponent columns and the Elo margin (collinear), so removing one group lets another pick up part of the signal. "Not significant when removed" does not
  mean "carries no information"; it means the model does not need it given everything else. Rest/back-to-back matters a little for rebounds (+0.0012, q = 0.002) and not elsewhere.
- **Wins that fit the project's lesson.** Information beats architecture: the largest effects are the injury report and the minutes/role columns (minutes averages, starter flags, recent-role),
  which are all about *how long the player will play*, which is where most prop error lives.
- Why the two add/drop signs differ: dropping removes information the model had; adding the recent-role group gives it new information (recent minutes), which production does not yet include.
  The recent-role gains reuse the same walk-forward and the same rows, but this table is not the pre-registered test (PI_RECENT_ROLE, RECENT_ROLE_HOLDOUT); it is descriptive context.

## What I could not check
- Refit noise from the LightGBM column subsampling is represented by one seed pair per stat only, so the +/-0.0005 band is rough.
- Whether the effects differ by slice (starters vs bench, cold start, by month): not sliced.
- The points numbers use the integer grid; the deployed continuous points quantiles would give somewhat different absolute CRPS (not the ranking, to my knowledge, but untested).
- Season 2025 untouched; this says nothing about it.

## Proposed rows (drafted, not appended)
- LEDGER (descriptive, no claim): "FEATURE_SIGNIFICANCE: drop-group refit importance for the production props, OOF n = 55,320 (2023-24); injury/vacated is the largest group for all four stats (+0.0245 pts, +0.0073 reb, +0.0047 ast, +0.0026 fg3m CRPS); recent-role group adds -0.0086/-0.0029/-0.0020/-0.0007; effects < 0.001 are inside refit noise."
- DECISIONS: none.
