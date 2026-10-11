# Lineups known (T-30) vs production T-60 props

Pre-registration: docs/LINEUPS_KNOWN.md (frozen blob e7615f2f18406d1088ee6a8e25f82a9549bdf533). Ledger T085-T090.
Raw output: reports/lineups_known/results.json (rows parquet is git-ignored). Run:
`uv run python -m nba.eval.lineups_known_eval --n-jobs 2` (about 4 minutes, CPU, read-only DB, season <= 2024).

**Leak statement.** The T-30 features use tonight's confirmed starters. They are a leak at T-60 and are only
available behind `lineups_known=True`. This measures the value of a different prediction time, not an improvement
to the production model. Nothing is wired, registered or promoted. Season 2025 is untouched.

## Result (2024 walk-forward, n = 27,583 played rows; delta = T-30 minus T-60 CRPS)

| stat | CRPS T-60 | CRPS T-30 | delta [95% CI, game-clustered] | BH p | floor | cov80 T-60/T-30 | bias T-60/T-30 | verdict |
|---|---|---|---|---|---|---|---|---|
| pts | 3.1890 | 3.1479 | -0.0412 [-0.0475, -0.0346] | 0.0005 | pass | 0.795/0.796 | +0.045/+0.044 | adds value |
| reb | 1.3249 | 1.3068 | -0.0182 [-0.0210, -0.0154] | 0.0005 | pass | 0.800/0.800 | +0.032/+0.039 | adds value |
| ast | 0.9164 | 0.9075 | -0.0089 [-0.0106, -0.0071] | 0.0005 | pass | 0.796/0.794 | +0.037/+0.039 | adds value |
| fg3m | 0.6150 | 0.6109 | -0.0041 [-0.0051, -0.0030] | 0.0005 | FAIL (> -0.005) | 0.791/0.793 | -0.010/-0.005 | not enough |

Relative CRPS gain: pts 1.3%, reb 1.4%, ast 1.0%, fg3m 0.7%. Guards (coverage within 0.80 +/- 0.03, |bias| <= 0.5 and
not worse, no slice with n >= 300 regressing > +0.01) pass everywhere. p = 0.0005 is the bootstrap resolution.
2023 replicates (descriptive): pts -0.0366, reb -0.0162, ast -0.0094, fg3m -0.0032.

## Slices (2024, CRPS delta, pts / reb / ast / fg3m)

| slice | n | pts | reb | ast | fg3m |
|---|---|---|---|---|---|
| status changed vs previous game | 2,422 | -0.238 [-0.282,-0.184] | -0.108 | -0.058 | -0.021 |
| - promoted (bench -> start) | 1,468 | -0.303 | -0.136 | -0.067 | -0.027 |
| - demoted (start -> bench) | 954 | -0.138 | -0.064 | -0.044 | -0.012 |
| status unchanged | 24,792 | -0.021 | -0.009 | -0.004 | -0.003 |
| bench tonight | 14,471 | -0.031 | -0.014 | -0.006 | -0.003 |
| starter tonight | 13,112 | -0.053 | -0.023 | -0.012 | -0.006 |
| bench by T-60 role (starter10) | 15,526 | -0.058 | -0.027 | -0.012 | -0.005 |
| starter by T-60 role | 12,057 | -0.019 | -0.007 | -0.005 | -0.002 |

As expected the gain concentrates in the 8.8% of rows whose starter status changed; the 90% of unchanged rows gain
little (pts -0.021). Gain sources (importance, pts): vacated-starter minutes 0.079, own starter flag 0.061, start delta
0.019, starters changed 0.007, usual-absent count 0.002.

## P(play) (secondary; 2024 n = 32,642; log loss, lower is better)

| arm | all rows | bench tonight (n = 19,578) |
|---|---|---|
| production Platt constants | 0.442 | 0.583 |
| T-60 recalibrated logistic (A_cal) | 0.398 | 0.609 |
| T-30 logistic (B) | 0.344 | 0.574 |
| B - A_cal [95% CI] | -0.0538 [-0.0569,-0.0509] | -0.0358 [-0.0399,-0.0318] |
| exploratory (not pre-registered) B - A2, A2 = A_cal + T-60 starter rate | -0.0540 | -0.0412 [-0.0458,-0.0369] |

The overall number is mostly mechanical: no box-score starter ever has NULL minutes (0 / 39,530), so "starter"
implies "played". The bench-only slice is the real signal. A_cal is a one-feature logistic (A_cal's bench log loss 0.609
is slightly worse than a constant predictor, about 0.60), so "knowing he is not starting" is a large part of that gain. "Listed active"
could not be measured: DNP-coach-decision and inactive rows are indistinguishable historically. Knowing the true
inactive list would add more, unmeasured.

## How optimistic is the starter proxy?

- Box-score starters with NULL minutes: 0 of 39,530. A scratched announced starter never appears as a starter, so the
  proxy cannot show scratches; this is the upward bias. Its size is not observable here.
- Starters who barely played: 0.70% under 10 min, 0.17% under 5 min (an observable lower-bound-style proxy, not the
  scratch rate).
- Team-games without exactly five flagged starters: 0 / 7,906.
- Sensitivity (descriptive): flag one random starter as bench-announced in 5% of team-games (about 1% per starter),
  in train and test. The gain survives almost intact: pts -0.0398 [-0.0462,-0.0332], reb -0.0176, ast -0.0088, fg3m
  -0.0038 (vs -0.0412, -0.0182, -0.0089, -0.0041). The verdict does not change. A real announcement-vs-box-score gap
  could be larger or structured (star rest decisions made late), so treat the headline as an upper-biased estimate.

## Other caveats

- The decision uses 2024 only; 2023 is a descriptive replication and nothing was selected (single config, no tuning).
- The market also reprices at T-30 on the same information. Beating the T-60 model with lineups says nothing about beating
  a T-30 price. Edge vs the market would have to be measured separately.
- T-30 vs T-60 differ in more than lineups: late injury news and line moves exist at T-30 too, but are not modelled here.
- Importance mix and the exact ordering of the 5 features are descriptive, not a causal attribution.
- fg3m: significant but small (0.7%); not worth a second run on this evidence alone.

## What the forward pipeline would need (design note; not implemented)

1. T-30 collector: poll an announced-starters source (official NBA lineup/game-detail or boxscore-preview feed via
   nba_api; confirm which is stable) from roughly 45 to 25 minutes before each tip. Store raw snapshots in a separate
   store (new table or file, written by the collector only; keep the single-writer rule) with `fetched_at`, game, team,
   player, `announced_starter`, `listed_active` and the inactive list. Idempotent and resumable like the injury report
   collector; a snapshot after tip-off must be ignored (same cutoff discipline as `latest_pretip_flagged`).
2. A second daily run, "pretip30", after the collector: rebuild the slate features with `lineups_known=True` but with
   `t30_starter` etc. read from the collected snapshot (not box-score `starter`). Predictions logged with a distinct
   model name and `made_at`, alongside the T-60 predictions, so both settle and compare on the same games. Rosters for
   slate construction can use the active list. Scratches after T-30 are then true noise rather than optimism.
3. Training: until enough collected history exists (a season), train on box-score starters (the proxy used here) and
   monitor the train/serve skew by comparing announced vs box-score starters on collected games.
4. The P(play) head would use `listed_active` directly (inactive implies DNP with certainty), which this study could not
   measure.
5. Gate: score T-30 vs T-60 forward on 2026-27 games before relying on it; downstream, parlay legs priced before T-30
   keep using T-60 and legs priced after use T-30. Nothing in nba/daily, ops/ or launchd was touched.

## Verdict

Confirmed starters at T-30 improve pts, reb and ast distributions by 1.0 to 1.4% CRPS with tight game-clustered CIs and
all guards passing (fg3m 0.7%, below the pre-registered floor), concentrated in rows whose starter status changed. The
estimate is upward-biased by the box-score proxy (modestly, by the sensitivity run). Recommendation: red-team review of
the proxy and a maintainer decision on building the T-30 collector; do not wire, register or promote anything now.
