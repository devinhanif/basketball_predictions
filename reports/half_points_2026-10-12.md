# First-half, second-half and first-quarter player points: how well do we predict them? (2026-10-12)

Descriptive. Seasons 2023 and 2024 only (2022 = warm-up history); season 2025 never loaded. No claim, no production change, no ledger row.
Built by `research/eval/half_points.py` (stages `build`, `arms`, `score`); raw numbers in `reports/half_points_2026-10-12.results.json`.
n = 55,320 OOF player-games (played rows, >= 5 prior games) in 2,638 games. Every CI is a 2,000-resample bootstrap clustered by game.

## Summary for Devin (one screen)

- **The data is clean.** Per-player 1H, 2H, OT points from play-by-play add up to the box-score points for every one of the 84,170 played
  rows in 2022-2024 (0 mismatches, mismatch rate 0.0%); team halves add up to the final score in all 7,916 team-games.
- **There is no 1H/2H player-points market** in our odds history (nine markets pulled: player points/rebounds/assists/threes/steals, h2h,
  spreads, totals, h2h_lay) and, per the main session, the-odds-api lists none; it does list first-quarter player props, which are not in our
  history yet. **No market comparison is made here.** Pending the 2023/2024 back-fill, no lines were invented.
- **We can forecast a half a little better than "half the game total", but not by much.** Typical miss on a half (median forecast vs actual):
  1H about 2.8 points, 2H about 3.1 points, Q1 about 1.8 points. That is because a half is small and lumpy: a player averages 5.4 points per
  half with a standard deviation of 5.1 (1H) and 5.3 (2H) -- the spread is as large as the average.
- **Best method = (b) the direct model**: integer CRPS (lower is better) 1H 1.965, 2H 2.180, Q1 1.285, versus 2.008 / 2.232 / 1.415 for
  "full-game forecast x 0.5" (x as-of Q1 share for the quarter). Gains -0.043 / -0.052 / -0.130 CRPS, all CIs well clear of zero.
  The big win is the quarter (the naive Q1 share is a bad guess); for halves the gain is about 2%.
- **The second half is harder**: about 11% worse CRPS than the first half for every method. Averages are equal (5.3 vs 5.4 points) but 2H
  results spread out more relative to what we can know before tip: who sits in a blowout, who plays 40 in a tight finish, foul trouble.
  None of that is knowable at T-60, and we have no per-half minutes to model it directly.
- **Over/under at the player's own median line is nearly a coin flip.** Accuracy 60.5% (1H), 58.9% (2H), 63.6% (Q1). But the line
  (floor(median)+0.5) is set so "under" is the common outcome, so *always saying under* scores 58.7% (1H), 56.1% (2H), 64.1% (Q1).
  So 1H and 2H beat the trivial rule by about 1.8 and 2.8 points; **Q1 does not beat it**. Do not read these as betting skill.
- **Real hit:** DeRozan, game 0022300070 (he averaged 22.4): forecast median 10 in each half, scored exactly 10 and 10 (total 20 vs
  median 21). Picked by rule (first of 37 stars with both halves exactly on the median), so it is a flattering example by construction.
- **Real miss:** Tatum, game 0022400381 (averaged 27.2): median 13 / 12; he scored 16 in the first half and 27 in the second (43 total) in a
  25-point Boston win. Picked by rule (largest 2H error among stars in games decided by >= 25). The forecast cannot see a star taking over.

## What was built (all as-of; chosen before scoring)

1H = periods 1-2, 2H = periods 3-4, Q1 = period 1, OT = period >= 5 (excluded from 2H; 212 OT games, 1,332 OT player-rows; the sensitivity
row "no overtime" below changes nothing). Points per event: made shot -> shot value; free throw not starting "MISS" -> 1.

| arm | what it is |
|---|---|
| naive | production full-game points quantiles x 0.5 (Q1: x the as-of league Q1 share of the position class, about 0.245), then mapped to the integer grid |
| (a) split | production's 199-point full-game distribution T, then H ~ Binomial(T, s) with s = the player's as-of share of his points in that period (half-life-10 weighted over prior games, shrunk with K = 20 points to the as-of league share of his position class). Nothing fitted. |
| (b) direct | the production LightGBM mean/scale heads + conformal quantiles, fit separately per period (walk-forward month blocks, same config), on production's pts features + the share, the weight behind it, and 8 (Q1: 4) as-of team/opponent scoring-by-period columns (rolling 20 games) |

"Production full-game forecast" = the walk-forward production-config points model (the `full` arm of the companion feature report). Production's
points quantiles are continuous; here all arms are mapped to the integer grid so every arm is scored on the same support.
Planted-future test of the as-of builder: inflating every half/quarter and team score from 2024-01-15 on by +50 changed no feature of any
earlier row (max difference 0.0) and moved later rows. Scoring: integer CRPS (199-quantile pinball form), MAE of the median forecast,
over/under at line = floor(median of the player's last 15 prior values) + 0.5, predict over iff P(over) > 0.5.

## Accuracy, all rows (n = 55,320; 95% CI clustered by game)

| target | arm | CRPS | MAE (median) | over/under accuracy | always-under accuracy |
|---|---|---|---|---|---|
| 1H (mean 5.39, sd 5.10) | naive 0.5 x full | 2.008 [1.993, 2.024] | 2.822 [2.801, 2.844] | 58.5% [58.0, 58.9] | 58.7% |
| | (a) split | 1.979 [1.965, 1.995] | 2.799 [2.778, 2.821] | 59.6% [59.2, 60.0] | |
| | (b) direct | **1.965 [1.951, 1.980]** | 2.790 [2.768, 2.811] | 60.5% [60.1, 60.9] | |
| 2H (mean 5.32, sd 5.25) | naive 0.5 x full | 2.232 [2.214, 2.252] | 3.100 [3.077, 3.125] | 59.3% [58.9, 59.7] | 56.1% |
| | (a) split | 2.206 [2.188, 2.224] | 3.110 [3.087, 3.135] | 59.1% [58.7, 59.5] | |
| | (b) direct | **2.180 [2.164, 2.197]** | 3.114 [3.091, 3.138] | 58.9% [58.5, 59.3] | |
| Q1 (mean 2.70, sd 3.21) | naive share x full | 1.415 [1.404, 1.427] | 1.925 [1.911, 1.940] | 54.1% [53.7, 54.5] | 64.1% |
| | (a) split | 1.306 [1.295, 1.317] | 1.836 [1.821, 1.851] | 61.6% [61.1, 62.0] | |
| | (b) direct | **1.285 [1.275, 1.296]** | 1.821 [1.805, 1.836] | 63.6% [63.2, 64.0] | |

Paired CRPS differences (negative = better; game-clustered CI):

| target | (a) - naive | (b) - naive | (b) - (a) |
|---|---|---|---|
| 1H | -0.029 [-0.032, -0.025] | -0.043 [-0.046, -0.040] | -0.014 [-0.017, -0.011] |
| 2H | -0.026 [-0.030, -0.023] | -0.052 [-0.057, -0.048] | -0.026 [-0.030, -0.022] |
| Q1 | -0.109 [-0.113, -0.105] | -0.130 [-0.134, -0.127] | -0.021 [-0.023, -0.018] |

Reading the table honestly:
- The MAE of the median barely moves (2H gets very slightly worse for (a) and (b), +0.01 to +0.015, within noise of the CIs). The CRPS gain is in
  the *shape* of the distribution, not the centre. A half is too lumpy for a point forecast to improve much.
- Mean bias of the forecast mean is +0.04 to +0.13 points for (a)/(b); naive halves read -0.18 (1H) and -0.11 (2H), mostly the integer
  rounding of 0.5 x quantiles (half-values round down), not the 50.4% first-half share of points.
- The 80% central interval (q10-q90 of the grid) covers 70% to 87% of outcomes (naive 1H 78.2%, naive Q1 70.1%, (b) 85.7% / 85.3% / 87.3% for 1H / 2H / Q1): integer quantiles make intervals
  conservative. Calibration is acceptable, not sharp. The over-coverage of (b) says its distributions are somewhat too wide, not too narrow.
- Over/under: the line is the player's own median, so the information a model adds is small by construction. Versus always-under, (b) is +1.8 points
  (1H), +2.8 points (2H, but naive (59.3%) beats (b) (58.9%) there), and -0.5 points (Q1). No arm beats always-under by a margin that
  matters; treat as descriptive.

## Why the second half is harder (and the margin slice)

CRPS and the 2H-minus-1H gap of the best arm (b), by *pre-tip* expected margin (Elo, absolute; terciles of the test rows: < 2.4, 2.4-5.6, >= 5.6 points):

| slice (n) | 1H CRPS naive / (b) | 2H CRPS naive / (b) | 2H minus 1H, (b) | (b) - naive, 2H [CI] |
|---|---|---|---|---|
| expected margin low (18,428) | 2.009 / 1.968 | 2.201 / 2.156 | +0.188 | -0.045 [-0.051, -0.038] |
| expected margin mid (18,446) | 2.009 / 1.970 | 2.239 / 2.188 | +0.218 | -0.051 [-0.058, -0.044] |
| expected margin high (18,446) | 2.006 / 1.957 | 2.257 / 2.195 | +0.238 | -0.062 [-0.070, -0.053] |
| expected margin >= 8 (9,521) | 1.991 / 1.938 | 2.236 / 2.176 | +0.238 | -0.060 [-0.072, -0.049] |

(The >= 8 slice was added after a first look at partial data; it is descriptive, not a hypothesis test.)

- The 2H penalty grows from +0.19 to +0.24 CRPS as the game is expected to be lopsided; the 1H is flat across the same slices. The 2H gain of the direct
  model over naive also grows with expected margin (it can use the Elo margin and team context), so the model does learn *some* of the blowout effect.
- After the fact (not usable before tip), games decided by >= 15 points (20,697 rows): 2H mean 4.74 points (sd 4.59) vs 5.66 (sd 5.57) in closer games,
  and the (b) gain over naive on 2H is -0.080 [-0.088, -0.072] vs -0.036 [-0.040, -0.031]. In blowouts starters are pulled, so the 2H is low and
  compressed; in close games stars stay on the floor and the outcomes spread. Whether a game becomes a blowout is the single biggest unknown for the 2H.
  (1H also averages lower in games that end up lopsided, 4.84 vs 5.71, so part of this is team and role mix, not only garbage time.)
- Players who end up playing under 60% of their usual minutes (7,599 rows, ex-post) have 1H mean 0.86 and 2H 0.89; here (a) is *worse* than naive on
  2H (+0.063 CRPS) because the share-split assumes a normal-minutes player, and (b) is only equal. Early exits (injury, foul trouble, benching)
  are not predictable and hit both halves.
- Plain words: in the first half, both coaches run their normal rotation, so a player's points look like "his usual rate times his usual minutes".
  In the second half the game has a story: close games stretch the stars, foul trouble and garbage time redistribute minutes, and the intentional-
  foul free throws pile up at the end. We predict the average player, not the story. Per-half minutes are not in our data, so this mechanism
  is inferred from the slices above, not measured directly.

Other slices (all rows unless stated; full table in the JSON): no-overtime games (n = 52,891) are identical to the headline within 0.01 CRPS; stars
(m10_pts >= 20, n = 6,706): 1H CRPS naive 2.934 / (a) 2.881 / (b) 2.879, 2H 3.450 / 3.392 / 3.371, Q1 2.292 / 2.112 / 2.099 -- stars have about 45% higher
CRPS because their half totals are larger (mean about 11.9 points).

## Markets

`data/odds/odds_history.duckdb` markets present: player_points, player_rebounds, player_threes, player_assists, player_steals, h2h, spreads, totals,
h2h_lay (no first-half, second-half or quarter player market). The main session reports the-odds-api lists first-quarter player props
(player_points_q1 etc.) and a market agent is probing/back-filling 2023/2024. **Q1 market comparison: pending that back-fill; none made here.**

## What I could not check
- Per-half minutes and the starter/bench split by half (not in the pbp parquet as minutes). The explanation for the 2H gap is inferred.
- K = 20 and the 15-game line window were fixed, not tuned; a different K could move (a) by a hundredth of CRPS (not tested; tuning rarely matters here).
- The binomial split ignores that points come in lumps of 1-3 and that halves are correlated; (a) is a transparent baseline, not a joint model.
- Production's points model has no lower-tail mixture in the config, as deployed; the 2H "early exit" rows are where that matters.

## Proposed rows (drafted, not appended)
- LEDGER (descriptive, no claim): "HALF_POINTS: 1H/2H/Q1 player-points forecasts from pbp, 2023-24 OOF n=55,320; direct model beats 0.5 x full by
  -0.043 / -0.052 / -0.130 CRPS [CIs exclude 0]; 2H ~11% harder; over/under at the median line does not beat always-under in Q1; no market exists for 1H/2H."
- DECISIONS: none (no direction change).
