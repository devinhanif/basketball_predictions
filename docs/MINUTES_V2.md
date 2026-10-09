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
