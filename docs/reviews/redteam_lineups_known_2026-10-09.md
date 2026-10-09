# Red team: LINEUPS_KNOWN (T-30 starter features), T085-T090, commit c24cc85

Date: 2026-10-09. Reviewer: red team (adversarial). Scope: docs/LINEUPS_KNOWN.md, reports/lineups_known.md,
nba/props/lineup_features.py, nba/eval/lineups_known_eval.py, ledger rows T085-T090.

## Verdict: WOUNDED

There is no leak beyond the starter flag itself. The t30_* features are a pure function of tonight's
box-score starting five plus strictly earlier games. Shuffling the flag removes the gain, a one-game time
shift removes most of it, and the result replicates on 2023 and with a second seed. The claimed gain is
still too large, because the starter proxy is more optimistic than the report says. Late-resolved
game-time decisions (GTDs) are a structured channel, not 5% random noise:

- **pts and reb survive even the worst case** (2024: pts -0.0230 [-0.0278,-0.0183], reb -0.0087
  [-0.0108,-0.0065]).
- **ast becomes conditional.** Its worst-case 2024 delta is -0.0050 [-0.0064,-0.0038], exactly at the
  floor, and 2023 is -0.0041, below it.
- **fg3m stays below the floor.**
- **The P(play) secondary (T089/T090) is overstated about 2x** because its baseline cannot see the T-60
  OUT report. On a fair baseline the bench-tonight gain is -0.018, not -0.036.

The real T-30 gain lies between the pessimistic and the headline numbers. Treat the headline as an upper
bound and pess2 as a lower bound.

**Most likely failure mode if the claim is wrong:** the announced lineup at T-30 does not resolve
game-time decisions the way the box score does. Usual starters listed questionable or doubtful at T-60
then became inactive or dressed-but-DNP in 1,519 cases across 2022-24 (16.7% of 2024 test rows sit in
such team-games). If those decisions are made after the T-30 snapshot, about 45% of the pts and ast gain
is look-ahead to the final box score.

## Setup and reproduction

- All runs are CPU-local, `nba.duckdb` was opened read-only through `load_inputs`, and data is limited to
  `season <= 2024`. My loader asserts this; month labels 2025-01..06 are 2024-25 season games. Season
  2025 was never loaded.
- Heavy fits ran under `data/ops/heavy.lock`, through the scratch wrapper `locked.sh`.
- Scratch dir: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/7ff798e7-932b-4807-b103-45d365238231/scratchpad/lk/`
  - `light.py`: missingness, late-scratch decomposition on the run's own OOF, drop-best-month.
  - `dbg_plant.py`: planted future.
  - `gone.py`: absence categories.
  - `heavy.py`: 11-arm walk-forward.
  - `analyze.py`: CIs.
  - `pplay.py`: P(play) fairness.
- Commands: `uv run python -W ignore <scratch>/lk/{light,dbg_plant,gone}.py`;
  `<scratch>/lk/locked.sh uv run python -W ignore <scratch>/lk/heavy.py` (973 s);
  `uv run python -W ignore <scratch>/lk/analyze.py`;
  `<scratch>/lk/locked.sh uv run python -W ignore <scratch>/lk/pplay.py`.
- heavy.py reuses the eval's exact machinery: month-block walk-forward over test seasons {2023, 2024},
  `stat_frame` rows, `ContextResidualModel`, production config with n_jobs 2, seed 0, and the 199-quantile
  CRPS. Every arm is fit on identical rows; an assert checks row identity per block.
- **Reproduction is exact.** B - A for 2024 gives pts -0.0412 [-0.0475,-0.0346], reb -0.0182, ast -0.0089
  and fg3m -0.0041, with n = 27,583 and 1,315 games, the same as the committed report.
- The working tree has uncommitted edits to `context_residual.py` and `context_residual_eval.py` from
  other work (a `tip_source == "real"` hook). The default config path is unaffected, as the exact
  reproduction shows.
- CIs are 95% game-clustered bootstraps (2,000 resamples; 500 for small slices). Delta = candidate minus
  baseline CRPS, so negative means the candidate is better.

## Attacks

### 1. Does any t30_* feature encode more than "who started"? (PASS)

- **Code audit.** `minutes`, `played` and `mins0` enter `build_lineup_features` only through window
  games (`p_no < tg_no`). Tonight's row contributes only `started_ann`.
- **Perturbation tests** (`dbg_plant.py`, compared with `np.isclose`):
  - Set every tonight row to DNP and zero tonight's points, flags kept (13 games, 334 rows): 0 features
    changed.
  - Set tonight's bench rows to DNP: 0 changed.
  - Delete tonight's 54 DNP rows: 0 changed.
  - Sanity check: flipping tonight's starter flags changes all 334 rows.
  - An earlier strict-equality run reported "changed". That was a Float32-to-Float64 cast artifact from
    my perturbation, not a feature change.
- **"starter=1 implies played" is moot for CRPS.** `build_features` keeps only `minutes > 0` rows for
  both training and test in both arms, so both arms already condition on playing. No conditioning on
  played rows is left to do, and the CRPS gain cannot run through P(play). Inside played rows, a starter
  flag goes with real minutes: starters under 5 minutes are 0.17%, bench rows under 5 minutes 7,394 of
  84,052. That is the legitimate value of the announcement, not a leak.
- **The channel that does exist is the proxy itself** (attack 4): the box-score five has already
  resolved every scratch and game-time decision.

### 2. Planted future and time shift (PASS)

- **Planted future.** I scrambled starter flags in every game after mid-2024, set those games' minutes
  to NULL, and planted a fake future game with inverted starters. Features of all 86,439 earlier rows
  were unchanged (0 diffs). The repo's own `test_future_games_never_change_earlier_t30_features` covers
  the same ground.
- **Time shift.** The "lag" arm rebuilds every t30 feature with tonight's five replaced by the team's
  previous-game five, which is knowable at T-60.

| stat | season | lag - A | B - lag |
|---|---|---|---|
| pts | 2024 | -0.0061 [-0.0096,-0.0029] | -0.0350 [-0.0410,-0.0290] |
| pts | 2023 | -0.0035 [-0.0078,+0.0008] | -0.0330 |
| reb | 2024 | -0.0043 | -0.0139 |
| ast | 2024 | -0.0015 | -0.0074 |
| fg3m | 2024 | -0.0004 | -0.0037 |

About 85% of the gain needs tonight's five. Nothing leaks from the feature engineering on prior games.
One small fairness note: a T-60-knowable "previous five" feature set already buys A about 0.006 on pts.

### 3. Shuffle the starter flag within team-game (PASS)

Starter flags were permuted within each (game_id, team_id) across all box rows, keeping five per
team-game, then all features were rebuilt in train and test.

| stat | 2024 shuffle - A | 2023 shuffle - A |
|---|---|---|
| pts | +0.0005 [-0.0013,+0.0023] | +0.0015 [-0.0011,+0.0042] |
| reb | -0.0006 [-0.0015,+0.0003] | +0.0007 |
| ast | -0.0003 [-0.0010,+0.0004] | +0.0003 |
| fg3m | +0.0004 [-0.0001,+0.0008] | +0.0007 [+0.0001,+0.0012] (slightly worse) |

Permuting t30 rows within date gives the same picture: pts +0.0012 [-0.0007,+0.0031]. The gain vanishes,
and in the promoted slice the shuffle gives +0.0015 against B's -0.303.

### 3b. Missingness (PASS)

I compared null rates for each t30 feature by played vs DNP, minutes < 5, starter, win/loss, and pts
above/below the median (all 2022-24 box rows; 103,785 rows, 84,052 played).

- `t30_starter` and the team features are never NULL except at season starts.
- The largest asymmetry is `t30_start_delta`: 2.28% NULL on DNP rows vs 1.50% on played rows, and 2.53%
  vs 1.40% for minutes < 5 vs >= 5. Both come from a player's first box row of the season, which is
  known before tip.
- The value asymmetries are the intended signal:
  - starter mean 0.009 for players under 5 minutes, vs 0.515 otherwise;
  - absent usual starters 1.09 in losses vs 0.91 in wins.

There is no NULL-iff-outcome pattern of the `opp_adjusted_ridge` kind.

### 4. Late scratches and game-time decisions vs the T-60 report (WOUND)

I classified every usual starter (started at least 50% of the last 10 or fewer team games, minimum 3)
who was absent from tonight's box-score five (`light.py`, `gone.py`). 2022-24 totals:

| category | n | meaning |
|---|---|---|
| on the T-60 OUT report | 3,696 | already known to A |
| benched but played | 2,287 | a coach decision; legitimately in the announcement |
| inactive (no box row), not on the OUT report | 1,135 | 905 are back with the same team later, so not trades. 851 had a T-60 report row: 689 questionable, 153 doubtful, 9 probable |
| dressed but DNP, not on the OUT report | 630 | 419 had a report row, mostly questionable |

So **1,519 usual-starter absences were game-time decisions resolved after T-60**: 630 dressed-DNP plus
889 GTD-inactive. The box-score five reflects the final resolution. The proxy's optimism is therefore
structured and frequent: 16.7% of 2024 test rows (4,598 of 27,583) sit in team-games with a
late-resolved absence. The report's synthetic "5% of team-games, random starter" sensitivity does not
capture this.

**Results on the eval's own OOF (2024).**

- Team-games with a dressed-DNP usual starter: 8.2% of rows, 19-21% of the pts/reb/ast gain
  (pts -0.094 there vs -0.036 elsewhere).
- Excluding all late-resolved team-games, B - A is:

| stat | 2024 | 2023 |
|---|---|---|
| pts | -0.0315 [-0.0373,-0.0257] | -0.0281 |
| reb | -0.0135 | -0.0125 |
| ast | -0.0066 | -0.0069 |
| fg3m | -0.0036 | -0.0026 |

**Worst case ("pess2").** Every late-resolved usual starter is treated as announced and then scratched
after T-30. Rule: he is flagged a starter (a DNP starter row is added for inactive ones), and the
box-score starter with the fewest prior starts is flagged bench-announced. Applied in train and test,
with features rebuilt.

| stat | 2024 pess2 - A | 2023 pess2 - A | dressed-DNP only ("pess"), 2024 |
|---|---|---|---|
| pts | -0.0230 [-0.0278,-0.0183] | -0.0190 [-0.0235,-0.0145] | -0.0322 |
| reb | -0.0087 [-0.0108,-0.0065] | -0.0086 [-0.0105,-0.0068] | -0.0132 |
| ast | -0.0050 [-0.0064,-0.0038] | -0.0041 [-0.0053,-0.0029] | -0.0065 |
| fg3m | -0.0024 [-0.0033,-0.0016] | -0.0014 | -0.0032 |

Coverage and bias stay within the guards for pess2 (pts cov80 0.793, bias +0.051). In the worst case,
pts and reb still clear the -0.005 floor with CIs below 0, ast sits on or below the floor, and fg3m
fails. The real number depends on how many GTDs are resolved before the T-30 announcement or inactive
list. This DB cannot observe that.

### 5. Replication: second seed, 2023, drop best month (PASS)

**Seed 1** (cfg.seed = 1, both arms refit):

| stat | 2024 B - A | 2023 B - A |
|---|---|---|
| pts | -0.0426 [-0.0490,-0.0362] | -0.0371 |
| reb | -0.0175 | -0.0166 |
| ast | -0.0083 | -0.0105 |
| fg3m | -0.0037 | -0.0037 |

**2023 at seed 0:** pts -0.0366, reb -0.0162, ast -0.0094, fg3m -0.0032. Same sign, similar size.

**Drop the best month.**

| stat | seed 0, 2024 | seed 1, 2024 (month dropped) | seed 1, 2023 (month dropped) |
|---|---|---|---|
| pts | -0.0403 [-0.0470,-0.0340] | -0.0413 (2025-02) | -0.0361 (2023-10) |
| reb | -0.0163 | -0.0156 | -0.0155 |
| ast | -0.0079 | -0.0071 | -0.0098 |
| fg3m | -0.0039 | -0.0034 | -0.0037 |

Months with a negative delta: 7-9 of 9 in each case.

**Concentration.** No single team carries the gain: the largest team share is 6.6% of the pts gain, and
30 of 30 teams show a net gain. Relative gains are 1.0-1.4%, so this is not "too good to be true".

### 6. Baseline fairness (CRPS: PASS; P(play): WOUND)

- **CRPS.** Same rows, same played-only training, same conformal calibration window, same refit per
  month block, same seed. The baseline has the T-60 OUT report (2024 report coverage is 1,314 of 1,315
  games) and `last_starter`/`starter10`. The best fair upgrade I tried, A plus the lagged previous-game
  five features, still loses to B by pts -0.0350 and reb -0.0139.
- **Knockout.** Own features (`t30_starter`, `t30_start_delta`) carry most of the gain: pts -0.0326,
  reb -0.0153, ast -0.0074. Team features alone give pts -0.0148. No single hidden group carries it, and
  the own-flag group is exactly the legitimate T-30 fact.
- **P(play) baseline is unfair.** A_cal is `logit(p_raw)` with no game context, so it never sees the
  T-60 OUT report. Production T-60 drops report-OUT players from the slate (`predict_slate_context`,
  `exclude |= report_out`). B can partly infer those absences from tonight's five. `pplay.py`, 2024:

| comparison | all rows | bench tonight |
|---|---|---|
| reported B - A_cal | -0.0538 [-0.0569,-0.0509] (n = 32,642) | -0.0358 |
| A_cal + T-60 OUT flag ("A_out") | log loss 0.3979 -> 0.3343, beats B outright: B - A_out +0.0097 [+0.0050,+0.0143] | B - A_out +0.0612 |
| fair: (B + OUT) - A_out | -0.0376 [-0.0397,-0.0354] | -0.0176 [-0.0208,-0.0144] |
| (B + OUT) - (A_out + lagged five) | -0.0242 [-0.0268,-0.0217] | -0.0184 |
| production population (T-60 OUT excluded, n = 31,633) | -0.0383 [-0.0407,-0.0361] | -0.0181 [-0.0216,-0.0147] (n = 18,569) |

The non-mechanical bench-tonight P(play) gain is about half the reported value. The same GTD-resolution
optimism as in attack 4 also applies to it, unquantified here.

### 7. Holdout hygiene (PASS)

- `load_inputs` filters `season <= MAX_SEASON (2024)` in SQL, and `run()` raises if a later season
  appears.
- HOLDOUT_ACCESS_LOG has no LINEUPS_KNOWN row, which is correct because there was no 2025 touch.
- None of my attacks touched 2025.

## Recommendations (no source, config or ledger was modified)

1. **Restate T085-T088 as bounds.**

   | stat | upper (headline) | lower (pess2) | note |
   |---|---|---|---|
   | pts | -0.041 | -0.023 | passes either way |
   | reb | -0.018 | -0.009 | passes either way |
   | ast | -0.009 | -0.005 / -0.004 | conditional; floor not robustly cleared |
   | fg3m | -0.004 | -0.002 | fails |

   Replace the "5% random scratch" sensitivity with the GTD-structured one in the report.
2. **Correct T089/T090** with a new ledger row citing them. The P(play) baseline must get the T-60 OUT
   report, or use the production population. The fair bench-tonight delta is -0.018 [-0.022,-0.015].
3. **Forward collector.** Record the announcement time and the inactive-list time per game. On collected
   games, measure the share of questionable/doubtful usual starters still unresolved at the snapshot.
   That share sets where the true gain sits between the bounds.
4. **Fairer T-60 baseline.** Feed the T-60 questionable/doubtful status as a feature to both arms. A
   currently ignores it, so part of what B "learns" from the five is GTD resolution that A could
   partially price at T-60.
5. **Builder test.** Add a regression test that tonight's minutes, DNP status and stats cannot change
   t30 features (the perturbation in attack 1). The existing tests cover only future games.
