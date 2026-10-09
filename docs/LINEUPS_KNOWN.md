# LINEUPS_KNOWN — value of a second prediction time, T-30 ("lineups known")

Status: PRE-REGISTERED 2026-10-08, written before any result was computed. Results are
filled in below the line "RESULTS" only after the run. The rule is not changed after seeing results.

## Leak statement (read first)

This experiment defines a DIFFERENT prediction time. At **T-30** the confirmed starting five and the
active/inactive list are public. At **T-60** (the production `props_context_residual` time) they are
NOT. Every feature introduced here (`t30_*`) uses tonight's starter information and is a **leak if
used at T-60**. They exist only behind an explicit opt-in flag (`lineups_known=True`) and must never
be fed to the production T-60 path. Nothing here is wired into production, registered, or promoted.

## Hypothesis

H1. Knowing the confirmed starters improves the per-player stat distribution (pts, reb, ast, fg3m,
conditional on playing) versus the T-60 model, because starter status is the dominant driver of
minutes, and the T-60 model only knows a player's *past* starter rate. The largest gain is expected
for players whose starter status changed relative to their previous game.

H2 (secondary). Knowing the lineup improves P(play) log loss. Caveat decided up front: in box-score
data every starter played (0 starters with NULL minutes in 2022-2024), so "starter" implies "played"
by construction; the overall P(play) gain is mostly mechanical and the interesting number is the
bench-only slice.

## Proxy and its bias (decided up front)

Historical announced starting five is not stored. Proxy: `player_game_stats.starter`. A late warm-up
scratch of an announced starter makes the box-score flag *more informative than the announcement*
(the replacement is flagged a starter; the announced-but-scratched player is a DNP row and appears
nowhere as a starter). So the proxy is **optimistic: the estimated gain is biased upward.** The
scratch rate itself is unobservable in this DB. Bounds reported:
1. Share of box-score starters with minutes < 10 and < 5 (announced starters who barely played).
2. Share of team-games where the starter flags do not total 5 (data quality).
3. Sensitivity arm (descriptive only, not part of the rule): re-run arm B with T-30 features
   degraded by a synthetic scratch process (per team-game probability 0.05, ~1% per starter, one
   random starter flagged as bench-announced, seed 0, applied to train and test). The gain surviving
   this contamination is reported next to the clean gain.

## Features added at T-30 only (all as-of tonight's lineup + strictly prior games)

Built by `nba/props/lineup_features.py`. "Previous game" for team features = the team's previous game in
the SAME season (NULL for a team's first game of a season; season-scope rule). "Window" = the team's
previous up-to-10 games in the same season, at least 3 required (else NULL).
1. `t30_starter` - own starter flag tonight (0/1; NULL flag counts as 0).
2. `t30_n_changed` - number of tonight's five starters not in the team's previous-game five.
   NULL if either game does not have exactly five flagged starters.
3. `t30_n_usual_absent` - count of usual starters (start in >= 50% of the window games) who are not
   in tonight's five (injured, resting, or benched; includes the player himself).
4. `t30_vac_starter_min` - sum, over those absent usual starters, of their mean minutes in the
   window games they played (vacated starter minutes).
5. `t30_start_delta` - `t30_starter` minus the same player's starter flag in his previous row in the
   same season (NULL if none). Nonzero = "status changed vs previous game".
For P(play) the active/inactive list would add "listed active"; it is NOT available historically
(DNP-coach-decision vs inactive is indistinguishable) so the P(play) arm uses features 1-5 only and
the "listed active" signal is explicitly out of scope of this measurement (an unmeasured extra).

## Arms (identical rows, identical DNP/played handling, identical calibration)

- **A (T-60, production)**: `nba.props.context_residual` features `stat_feature_names(stat)`; played
  rows only, >= 5 prior played games; month-block walk-forward; newest 15% of the training window
  is the conformal calibration window; production `ContextResidualConfig` (n_jobs lowered to 2 for
  memory; deterministic, so results are unaffected).
- **B (T-30)**: the same code, same seed, same rows, same calibration, with
  `stat_feature_names(stat, lineups_known=True)` (A's features + the 5 `t30_*` features), refit.
- P(play): **A_cal** = production minutes model p_play (`build_minutes_features` +
  `predict_minutes`, default config, no game context) -> walk-forward logistic recalibration on
  logit(p_raw) alone (the same Platt form as production, refit per month block); **B** = the same
  logistic with the 5 `t30_*` features added. Population: rows with `games_played_prior >= 5` and
  `p_play_raw >= 0.5` (the forward filter), months of test seasons. Reference only: the frozen
  production Platt constants.

Seed 0, the walk-forward is month blocks, test seasons {2023, 2024} predicted from models fit
strictly before each block (2022 onward). Season 2025 is never loaded (the loader filters season <= 2024
in SQL; `run_eval_frames` raises if it sees one). HOLDOUT_ACCESS_LOG is untouched: no holdout touch.

## Metrics and decision rule (per stat, season 2024 is the reported season)

- Primary: paired per-row CRPS (199-quantile pinball form, `nba.eval.context_residual_eval`),
  delta = B minus A, 95% CI clustered by game_id (2000 resamples), two-sided clustered-bootstrap
  p-value, Benjamini-Hochberg across the 4 stats at q = 0.05.
- Practical floor: mean CRPS delta <= -0.005.
- **T-30 "adds value" for a stat iff ALL:** (1) delta <= -0.005; (2) CI upper bound < 0;
  (3) BH-adjusted p <= 0.05; (4) guards: B 80% interval coverage within 0.80 +/- 0.03; |mean bias of B|
  <= 0.5 and |bias B| - |bias A| <= 0.05; no slice with n >= 300 has a CRPS delta > +0.01.
- Slices (fixed now): production slices (teammate-out report status, minutes-change bucket,
  first-15/rest, role by `starter10`) plus T-30 slices: **bench tonight** vs **starter tonight**
  (`t30_starter`), and **status**: `promoted` (delta > 0), `demoted` (delta < 0), `unchanged`
  (delta = 0), `unknown` (delta NULL); "changed" = promoted + demoted (expected largest gain; reported).
- Select-nothing-on-2024: there is no choice made on any data (single pre-specified config, no tuning,
  no feature selection). 2023 is reported as a descriptive replication only. No result in 2024 feeds
  back into any choice.
- Diagnostics (not in the rule): MAE, bias with CI, coverage, importances of B's t30 features.
- Headline verdict: per stat as above; the all-4 conjunction is also reported.
- P(play) secondary (not part of the stat rule): paired log-loss delta B minus A_cal, game-clustered CI,
  overall and on the bench-tonight slice (the non-mechanical part). Single secondary comparison.
- A negative result is reported as such. Even if the rule is met: no wiring into production, no
  registration or promotion; recommend red-team review and a maintainer decision.

## Forward-pipeline note (design only, not implemented)

See reports/lineups_known.md ("What the forward pipeline would need").

## RESULTS

Run 2026-10-08 (local CPU, `python -m nba.eval.lineups_known_eval`, seed 0, n_jobs 2; commit/push left to the
maintainer). Frozen pre-registration blob: `git hash-object` e7615f2f18406d1088ee6a8e25f82a9549bdf533
(the text above this section; sha256 prefix 9185d26f9dadd191). Test rows: played rows, >= 5 prior games,
2024 n = 27,583 (2023 n = 27,619, descriptive). Delta = T-30 minus T-60 CRPS (negative = lineups-known helps).
Full tables, slices and the forward-pipeline design note: reports/lineups_known.md. Ledger: T085-T090.

| stat | delta (2024) | 95% CI (game-clustered) | BH p | floor -0.005 | cov80 A/B | bias A/B | adds value at T-30 |
|---|---|---|---|---|---|---|---|
| pts | -0.0412 | [-0.0475, -0.0346] | 0.0005 | pass | 0.795/0.796 | +0.045/+0.044 | YES |
| reb | -0.0182 | [-0.0210, -0.0154] | 0.0005 | pass | 0.800/0.800 | +0.032/+0.039 | YES |
| ast | -0.0089 | [-0.0106, -0.0071] | 0.0005 | pass | 0.796/0.794 | +0.037/+0.039 | YES |
| fg3m | -0.0041 | [-0.0051, -0.0030] | 0.0005 | FAIL | 0.791/0.793 | -0.010/-0.005 | NO (below floor) |

All guards pass for all four stats (coverage, bias, no slice regression > +0.01). The p-value floor
0.0005 is the 2000-resample bootstrap resolution. Key slices (pts / reb / ast / fg3m CRPS delta): status
changed vs previous game (n=2,422) -0.238 / -0.108 / -0.058 / -0.021 (promoted n=1,468: pts -0.303;
demoted n=954: pts -0.138); unchanged (n=24,792) -0.021 / -0.009 / -0.004 / -0.003; bench tonight
-0.031 / -0.014 / -0.006 / -0.003; starter tonight -0.053 / -0.023 / -0.012 / -0.006.
2023 (descriptive replication): pts -0.0366, reb -0.0162, ast -0.0094, fg3m -0.0032 (same pattern).

P(play) secondary (2024, n = 32,642): log loss production-Platt 0.442, recalibrated T-60 (A_cal) 0.398,
T-30 logistic 0.344; delta B - A_cal -0.0538 [-0.0569, -0.0509]. Mostly mechanical (all box-score starters
played). Bench-tonight rows only (n = 19,578): A_cal 0.609 -> B 0.574, delta -0.0358 [-0.0399, -0.0318].
Exploratory, NOT pre-registered control A2 (A_cal + the T-60 starter rate): bench delta B - A2 -0.0412
[-0.0458, -0.0369], overall -0.0540 [-0.0573, -0.0509]. "Listed active" could not be measured
(no historical inactive list).

Proxy-bias bounds: box-score starters with NULL minutes 0/39,530 (so scratched announced starters are
invisible: the proxy cannot show them); starters playing < 10 min 0.70%, < 5 min 0.17%; team-games not
having exactly five flagged starters 0/7,906. Sensitivity (descriptive): a synthetic late-scratch process
(5% of team-games, one starter flagged bench-announced, train and test) attenuates the gain only slightly:
pts -0.0398 [-0.0462,-0.0332], reb -0.0176, ast -0.0088, fg3m -0.0038. The true announced-vs-box-score gap
is unobservable here, so the headline numbers remain an upper-biased estimate.

Missingness audit: `t30_start_delta` is NULL for 713/55,202 rows (first game of a season); mean outcome
9.9 vs 10.8 pts, correlation with y -0.011, i.e. not an outcome-dependent missingness pattern. Importance
(gain share, pts mean head): vacated-starter minutes 0.079, own starter flag 0.061, start delta 0.019,
n starters changed 0.007, n usual absent 0.002.
