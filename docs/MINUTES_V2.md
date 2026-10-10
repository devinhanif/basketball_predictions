# Minutes model v2: pre-registration

Status: PRE-REGISTERED 2026-10-09 BEFORE any minutes-v2 number was computed. Everything above the
line `=== RESULTS BELOW ===` is frozen; its sha256 is recorded in the results section, in the
ledger rows and in the hand-off message. (The agent that wrote this was instructed not to commit;
the freeze evidence is therefore the sha256 plus the file timestamp, and the maintainer commits the
file. No candidate fit exists before this file was written.)

## Hypothesis

H1: minutes are the largest source of prop error, so a better PRE-TIP MINUTES DISTRIBUTION (not just
a mean) improves the prop distributions of every stat.

H1 splits into two testable parts:
* H1a: a pre-tip minutes-given-plays distribution built from the candidate inputs below has lower
  minutes CRPS than production's minutes path, and than a strong recency baseline.
* H1b: feeding that distribution to the production props model (context_residual) lowers props
  integer-support CRPS.

Null: no change. A negative result is a reportable result. The production props model already
contains the recency minutes (`min5/min10/min40`), starter history, `since_flag`/`flag_dir`/`gap_days`,
`b2b`, rest, `abs_margin` (the |injury-Elo expected margin| blowout proxy), `n_out_rot`, `vac_min`,
so the expected gain from re-feeding the same information through a second GBM is small; the
candidate's new information is the V2 feature group below. That is stated up front so a null result
is not a surprise.

## What has already been seen (honest data-exposure statement)

* Seasons <= 2024 only will be loaded; season 2025 is never loaded (in-memory copy filtered to
  `season <= 2024`, `nba.duckdb` opened `read_only=True`).
* SEEN before this document, for BOTH 2023 and 2024: production props OOF diagnostics (coverage,
  strata), the pre-tip short-minutes classifier (docs/LOWER_TAIL.md: AUC 0.855 pooled over 2023+2024
  test rows, mean pi 0.091 vs observed 0.104) and the full lower-tail mixture screen for all four
  stats. The "short game" definition (minutes < 0.5 x `min10`) and the idea of a mixture with a
  short component therefore come from a screen that already saw 2024. The 2024 confirmation below is
  consequently NOT independent of that idea; it is independent of every number produced here.
* Earlier minutes-model A/Bs by other agents (docs/NEXT_OPTIONS.md, the hand-set and learned
  game-context adjustments, garbage-time branch) were run on the 4-season DB including 2025. Their
  results (rest/b2b/travel/tanking carry no minutes signal beyond shrinkage) shaped my priors; none
  of their numbers enter this design, and I do not load 2025.
* NOT seen: any minutes distribution score of any arm below on any season; `player_game_stats.pf`
  has never been used as a feature in this repo (grep), so foul features are new information.
* Selection season 2023, report season 2024. Rule: a gate is "selected" only if it passes on 2023;
  2024 is then the confirmation. If 2023 fails, 2024 is still reported but cannot upgrade the verdict.

## Targets

* PRIMARY target: `minutes | plays` on PLAYED rows (`minutes > 0`; DNP rows have NULL minutes and are
  excluded from labels and states, as in production), rows with >= 5 prior played games
  (`MIN_PRIOR_PLAYED`, the exact production props row set). 2023 n ~ 27.6k, 2024 n ~ 27.6k.
* SECONDARY target: P(play) on rostered rows (design in "Secondary: P(play)"). Reported either way,
  no floor, cannot rescue or fail the verdict.

## Candidate inputs (all as-of / pre-tip; the V2 group is the new information)

Base group = production `COMMON_FEATURES` (includes recency minutes, starter history, rest/b2b,
`exp_margin`/`abs_margin` blowout risk, `since_flag`/`flag_dir`/`gap_days`, `n_out_rot`, `vac_min`,
report flag). The V2 group (22 columns; exact names are frozen; all computed from games strictly
before the target game date, the pre-tip OUT list gated on the real tip, or schedule facts known
pre-tip):

| group | columns | definition |
|---|---|---|
| foul-trouble risk | `pf36_h20`, `pf5_rate_h40` | decayed (half-life 20) fouls per 36 minutes over prior played games; decayed (half-life 40) share of prior games with pf >= 5 |
| age | `age` | years from `players_static.birth_date` to the game date (NaN if unknown) |
| rest-day pattern | `own_b2b_gap` | decayed (half-life 40) mean minutes on prior back-to-back games minus on prior non-b2b games, each shrunk toward the player's all-games mean with pseudo-count 3 games |
| injury-return ramp | `ret_n`, `ret_mean_min`, `ret_ramp` | games played since the last within-season absence of >= 14 days (0 = tonight is the return game; 99 = no such absence this season); mean minutes in those games (NaN if none); `ret_mean_min / pre-absence min10` |
| blowout history | `own_blow_gap`, `team_blow_rate` | decayed (half-life 40) mean minutes in prior games with final margin >= 15 minus the all-games mean (shrunk, pseudo-count 3); share of the team's prior 20 games with final margin >= 15 |
| rotation tightness | `rot_hhi10`, `rot_n15_10`, `rot_top5_10` | over the team's prior 10 games: mean Herfindahl index of minute shares, mean number of players with >= 15 minutes, mean top-5 minute share |
| teammates OUT | `n_out_starters`, `out_max_min40` | on the pre-tip OUT list (real-tip gated): count of OUT teammates whose as-of `starter40 >= 0.5`; the largest as-of `min40` among OUT teammates (NaN where no report; 0 where reported with none) |
| game importance | `win_pct_asof`, `opp_win_pct_asof`, `tanking_incentive`, `in_playoff_pos`, `in_playin` | from `nba.features.game_context.build_standings_features` (as-of) for the player's team and the opponent |
| season phase / type | `cal_month`, `is_playoff`, `is_playin` | calendar month; game_id prefix `004` / `005` (scheduled game type, known pre-tip) |

Blowout risk proper is `abs_margin` (already a base feature); a monotone transform of it
(e.g. P(|margin| >= 20)) adds nothing for trees and is not added. Foul-outs are modelled only through
the foul-rate features (no per-game foul-out label is used as an input).

## Model family (fixed; no tuning; constants final)

All heads are LightGBM with production's hyper-parameters (`ContextResidualConfig` defaults via
`_params`: 200 trees, lr 0.04, 15 leaves, min_child 100, feature_fraction 0.8, bagging 0.8, seed 0,
deterministic), fitted by month-block walk-forward (train = all eligible rows strictly before the
block's first day; minimum 1,500 training rows; 2022 season = warm-up). Calibration window = newest
15% of the training rows on a date boundary (`split_calibration`).

Primary candidate **M2** = two-component mixture of minutes given plays, features = base + V2:
* short event `S = 1[minutes < 0.5 * min10]`; `pi(x) = P(S = 1 | plays, x)` from one LightGBM binary
  classifier on all training rows, clipped to [0.005, 0.5];
* regular component (S = 0 rows): mean head on `minutes - min10`, scale head on `|minutes - min10|`
  (floored at 1.0 minute), centre `c = min10 + mu(x)`; standardized residuals `z` on the S = 0
  calibration rows (>= 500 else the regular z of all calibration rows);
* short component: `min10 * w_q`, `w_q` = empirical 199-quantiles of `minutes / max(min10, 0.5)` over
  the S = 1 training rows (>= 100 such rows else pi = 0);
* predictive distribution `(1 - pi) * regular + pi * short` read off the pooled point masses with
  the same routine as docs/LOWER_TAIL.md (`mixture_quantiles`), clipped to [0, 60]; its mean is the
  mean of the 199-grid.

Comparison arms for the minutes distribution (identical rows):
* **P** production's minutes path: `build_minutes_features(..., use_game_context=False)` +
  `predict_minutes` defaults (shrunk trailing-150-game mean/sd), conditional-on-plays truncated
  Normal(mu, sigma) on [0, 48], quantiles from `MinutesHurdleDist` truncated-normal ppf. The
  rookie-prior and new-team-k extras of the forward path are NOT applied (they need the live roster
  frame); stated limitation. This is the comparator the maintainer named.
* **R** recency baseline: `min10 * ratio_q`, `ratio_q` = empirical 199-quantiles of
  `minutes / max(min10, 0.5)` on the calibration-window rows (proportional conformal). A fair strong
  baseline: production props already use `min10` as the centre.
* **S1** single-component (no mixture) with base + V2 features: isolates the mixture shape.
* **M1** the same mixture with base features only: isolates the V2 feature group.

## Downstream path (ONE path, decided up front)

The minutes distribution enters `context_residual` AS FEATURES (not via a frequency-severity
re-derivation of the stat): six appended columns from M2, out-of-fold per month block exactly as
above: `mv2_mean`, `mv2_q10`, `mv2_q50`, `mv2_q90`, `mv2_sd`, `mv2_pi`. They are appended after the
production columns through `stat_feature_names(stat, extra=...)`; production's feature list,
heads, calibration and quantile construction are otherwise unchanged. The minutes model is refit
per month block on rows strictly before it, so every training row's `mv2_*` value is the one a live
pipeline would have had at that time (rows before the first fitted block have NaN). Frequency-severity
(minutes x per-minute rate) is NOT run; this keeps one multiplicity family.

Downstream arms (each re-fit by the identical walk-forward, same seeds, same rows):
* **A0** production features (control; reproduces production);
* **A1** production + `mv2_*` (the candidate);
* **A2** production + the six `mv2_*` columns permuted jointly across rows within season (noise
  control for the extra columns; descriptive);
* **A3** production + realised minutes of the same game (ORACLE: a leak by construction, never a
  model; it only measures the ceiling of what perfect minutes could give; descriptive).

All four stats (pts, reb, ast, fg3m).

## Metrics, support and inference

* Minutes (continuous support): per-row CRPS (2 x mean pinball over the 199 grid); paired delta
  (candidate - comparator), game-clustered bootstrap (2000 resamples, seed 0), per season.
* Props: integer-support CRPS for EVERY arm and every stat (`ceil(q - 0.5)`, BEST_PRACTICES), paired
  delta A1 - A0, same bootstrap. Continuous-support CRPS reported as secondary.
* Threshold log loss (secondary): minutes P(min >= 10/20/30) mean; props P(stat >= N) at the
  LOWER_TAIL thresholds (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2), mean over the two.
* Multiplicity: Benjamini-Hochberg over the 4 downstream stats within each season (A1 vs A0); over
  the 2 minutes comparisons (M2 vs P, M2 vs R) within each season.
* Game-clustered CIs everywhere (cluster = `game_id`).

## Pass rules (floors absolute)

Minutes gate (per season; M2):
1. dCRPS(M2 - P) point <= -0.10 minutes, CI upper < 0, BH p < 0.05;
2. dCRPS(M2 - R) point < 0, CI upper < 0, BH p < 0.05 (the new model must beat the strong simple
   baseline too, otherwise "better than production" only means "better than a weak trailing mean");
3. guards: |mean bias of M2 mean vs actual| <= 0.5 minute; central 80% interval (q10..q90)
   coverage in [0.75, 0.85] (A1.1 style, continuous PIT); lower-tail PIT P(PIT <= 0.10) in
   [0.08, 0.12]; no slice (cold start `n_prior < 20`; starter `starter10 >= 0.5` / bench; first 15
   team games / rest; teammate-OUT; b2b; return-from-absence `gap_days >= 14` within season) with
   n >= 300 where M2 is worse than P or worse than R by more than +0.05 minute CRPS.

Props gate (per stat, per season; A1 vs A0):
1. dCRPS point <= -0.005 (integer-support units), CI upper < 0, BH p < 0.05;
2. guards: |bias| <= 0.5; 80% interval PIT coverage in [0.75, 0.85] (continuity-corrected,
   randomized, seed 0); no slice (cold start `n_prior < 20`, starter/bench, teammate-OUT, first 15
   team games) with n >= 300 worse by more than +0.01 CRPS.

Verdict:
* **PASS** = minutes gate passes on 2023 AND 2024, and >= 1 stat passes the props gate on 2023 AND
  2024. Consequence: recommend shadow-logging plus red-team review. Never promotion.
* **MINUTES-ONLY** = minutes gate passes (both seasons) but no stat passes both seasons:
  H1a supported, H1b not; no shadow recommendation.
* **FAIL** otherwise; the reasons are reported check by check.
* A2 and A3 never enter the verdict. If |A2 - A0| is of the same size as A1 - A0 the A1 effect is
  declared noise. A3 is read as: if even oracle minutes move CRPS by less than the floor for a
  stat, H1b cannot hold for that stat through any minutes model.

## Secondary: P(play)

Rows: `build_minutes_features` rows (played or DNP) in 2023/2024 with `n_played_prior >= 5`,
excluding players on the pre-tip OUT list for the game; label = `minutes > 0`. Arms: production raw
`p_play` and production + Platt (`forward.P_PLAY_PLATT`; that Platt was fitted on 2023-24 slates, so
it is in-sample on these rows and advantaged); M2-p = LightGBM binary (production hyper-parameters,
month-block walk-forward) on the minutes-frame columns + rest/b2b/standings columns + `age`,
`exp_margin`, `dnp_last5` (DNPs among the player's previous 5 team-box rows), `days_since_played`.
Metrics: paired log loss delta (game-clustered), ECE (10 bins), Brier. No floor, no pass/fail.

## Implementation notes (frozen)

* Code: `nba/props/minutes_v2.py` (features, model, scoring helpers), `nba/eval/minutes_v2_eval.py`
  (runner). Default-off: nothing in production imports them; `stat_feature_names(stat)` and
  `build_features` outputs are unchanged unless a caller opts in.
* Tests: planted-future rows, planted same-game values (minutes/pf/pts of the target row), DNP rows,
  flag-off identity, mixture monotonicity.
* CPU only; `data/ops/heavy.lock` around heavy fits; `nba.duckdb` read-only, closed after loading.
* Reproduce: `uv run python -m nba.eval.minutes_v2_eval all` (command recorded in the results).
* Out of scope: season 2025, promotion, live wiring, the frequency-severity path, rookie / new-team
  cold-start extras.

=== RESULTS BELOW ===

## Results (run 2026-10-09; frozen-section sha256 `4a7833c2` = committed blob 92e05cc; seasons <= 2024 only)

CPU, about 9 minutes end to end (minutes stage about 5 min, props 4 stats x 4 arms about 6 min, P(play) 20 s).
Full tables: `reports/minutes_v2.md`, `reports/minutes_v2/results.json`. Rows per season: 27,619 (2023, 1,318
games) / 27,583 (2024, 1,315 games). Fidelity: arm A0 reproduces production (pts integer CRPS 3.1528 / 3.1884,
identical to docs/LOWER_TAIL.md).

**Verdict: MINUTES-ONLY.** H1a supported, H1b not confirmed.

Minutes given plays (continuous CRPS, minutes; M2 minus comparator, game-clustered 95% CI, BH over {P, R}):

| season | M2 CRPS | P (production) | R (recency) | M2-P | M2-R | M2-M1 (V2 features) | M2-S1 (mixture shape) |
|---|---|---|---|---|---|---|---|
| 2023 | 3.4674 | 4.2987 | 4.0477 | -0.831 [-0.870,-0.792] | -0.580 [-0.607,-0.554] | -0.021 [-0.027,-0.015] | -0.001 [-0.009,+0.006] |
| 2024 | 3.4305 | 4.2809 | 4.0853 | -0.850 [-0.889,-0.812] | -0.655 [-0.690,-0.623] | -0.003 [-0.008,+0.003] | -0.008 [-0.015,-0.002] |

The minutes gate passes in both seasons (floor, CI, BH, bias +0.46 / +0.17 min, 80% cover 0.810 / 0.800, PIT<=.10
0.105 / 0.104, all slices incl. cold start, bench/starter, first 15 games, return from absence improve vs P and R).
Honest decomposition: almost all of the gain comes from the production COMMON feature set run through a
LightGBM distributional head (M1 already -0.81 / -0.85 vs P). The new V2 feature group adds -0.021 in 2023 and
-0.003 (CI spans 0) in 2024; the short-component mixture adds nothing measurable over a single component for minutes
CRPS (S1 vs M2: -0.001 / -0.008). pi AUC 0.857 / 0.855, mean pi 0.094 / 0.092 vs observed 0.106 / 0.102.
The P comparator is a weak shrunk trailing-150 mean, so "beats production's minutes path" mostly says that the
production props model's own minutes features are better than that path.

Props (integer-support CRPS, A1 = production + six mv2 columns minus A0 production):

| stat | 2023 dCRPS [CI] (BH p) | 2024 dCRPS [CI] (BH p) | A3 oracle-minutes (2023 / 2024) | gate |
|---|---|---|---|---|
| pts | -0.0053 [-0.0083,-0.0023] (0.002) | -0.0018 [-0.0044,+0.0008] (0.253) | -0.573 / -0.571 | 2023 pass, 2024 fail (floor, CI, BH) |
| reb | -0.0023 [-0.0035,-0.0010] (0.004) | -0.0024 [-0.0035,-0.0012] (0.004) | -0.190 / -0.191 | below floor both seasons |
| ast | -0.0006 [-0.0016,+0.0004] (0.205) | -0.0005 [-0.0014,+0.0004] (0.285) | -0.079 / -0.077 | fail |
| fg3m | -0.0008 [-0.0014,-0.0001] (0.024) | -0.0004 [-0.0010,+0.0001] (0.228) | -0.049 / -0.050 | fail |

* pts is selected on 2023 (barely at the -0.005 floor: -0.0053) and NOT confirmed on 2024 (-0.0018, CI spans 0). Under
  the pre-registered rule that is not a pass. The permuted-feature control A2 is +0.0029 / +0.0026 for pts (worse
  than A0), so the extra-columns noise cost is of similar magnitude to the A1 effect: the pts 2023 point estimate is
  inside the noise band of the design.
* reb is significant in both seasons (about -0.0023) but below the -0.005 floor. ast / fg3m: nothing.
* Guards for A1: bias |+0.05| or less, 80% cover 0.76-0.79 (inside 0.75-0.85) for all stats.
* Oracle ceiling (A3, leak by construction, diagnostic only): perfect realised minutes would lower CRPS by about
  18% (pts 0.57), 15% (reb), 9% (ast), 8.5% (fg3m). This supports "minutes are the dominant source of prop error", but
  a PRE-TIP minutes distribution recovers about 1% of it at best (pts 0.9%, reb 1.2%, ast 0.8%, fg3m 1.6% of the oracle gain, none beyond noise except reb) through this feature path: the pre-tip information in
  the minutes distribution is already in the production features, so, as an interpretation (not tested here), what is left is mostly
  in-game minutes variation rather than a modelling gap.

Secondary P(play) (no pass/fail; rows = minutes-frame rows with >= 5 prior played games, pre-tip OUT players
excluded; played rate 0.834 / 0.836; note this is a roster-wide population, not the live rostered-slate population
the Platt was fit on): log loss raw 0.3419 / 0.3558, Platt 0.4147 / 0.4283 (miscalibrated on this population, mean p
0.69 vs 0.83 observed), v2 LightGBM 0.2933 / 0.2981; v2 minus raw -0.049 [-0.053,-0.044] / -0.058 [-0.062,-0.053].
The v2 head has recent-absence features (`dnp_last5`, `days_since_played`) that production p_play lacks; no ablation was run to attribute the gain.
Not comparable to the live slate use; reported as descriptive only. (A first run of this stage mis-labelled DNP rows
as NaN; fixed (`fill_null(False)`) and rerun before the numbers above; no other stage was rerun.)

What was seen / process notes: the preregistration was committed (92e05cc, sha256 4a7833c2) before any real-data
number existed; only synthetic unit tests ran before. No parameter, feature or threshold was changed after viewing
results. Season 2025 never loaded. Reproduce: `uv run python -m research.eval.minutes_v2_eval all` (about 9 min CPU; take
`data/ops/heavy.lock`).

Recommendation: no promotion and no shadow-logging of A1 for props (nothing passes on both seasons; the pts 2023
gain did not replicate). The minutes distribution itself (M2, or simply M1/S1 with COMMON features) is a candidate
for the minutes path used by the sim/Kalshi tooling in place of the shrunk trailing mean; that would need its own
pre-registration and an adversary review. An adversary check of the pts 2023 selection is cheap but not recommended given
the 2024 non-confirmation.
