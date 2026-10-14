# Context and cross-leg correlation: sizing on 2020-22 (2026-10-14)

SIZING, not a test. Nothing is frozen, no rule exists, no model or live path is touched, no price is read, no stake is suggested. Outcomes used: seasons 2020, 2021, 2022 only (walk-forward, production-equivalent marginals). Seasons 2023-24 (the select/report seasons of any later rule) and 2025 (frozen holdout) were never loaded. Code: `nba/parlay/context_data.py` (PIT residuals), `context_corr.py` (pair estimation), `context_money.py`, `context_report.py` (this file).

## Question

A feature that is real but does not improve a player's marginal forecast is redundant for the marginal. For a parlay a different quantity matters: does context change the correlation BETWEEN legs' residuals? The production joint (`nba/parlay/joint.py`: one correlation per relation x stat pair, pooled; `nba/props/combo_copula.py`: one 6x6 matrix) is NOT conditioned on any context. This file measures how much it would matter if it were.

## Data

79,286 played player-games over 3,814 games (labels are `games.season`; 2022 = 2022-23). Marginals: the production props model (`context_residual`, same config, same month-block refit, integer support), trained only on earlier games from 2019-20 on; randomised PIT -> normal score `z`; `hit` = outcome >= the integer line whose model probability is nearest 0.5.

| stat | n | P(PIT<.1) | P(PIT<.2) | P(PIT<.8) | P(PIT<.9) | mean p(line) | mean hit |
|---|---|---|---|---|---|---|---|
| pts | 79,286 | 0.128 | 0.220 | 0.800 | 0.900 | 0.501 | 0.499 |
| reb | 79,286 | 0.111 | 0.207 | 0.802 | 0.901 | 0.500 | 0.500 |
| ast | 79,286 | 0.117 | 0.213 | 0.803 | 0.903 | 0.489 | 0.487 |
| fg3m | 79,286 | 0.115 | 0.213 | 0.789 | 0.896 | 0.411 | 0.421 |

Lower tails are a touch heavy (0.111-0.128 at 0.10), the same pattern as production in 2023-24 (docs/reviews, minutes_spread_eda). Marginals are production-like, not better.

## How the contexts were defined

Declared with the task before any cell was read: `vac24`, `lead_out`, `compete`, `blowout`, `fav`. Added after the first table was read: `vac48` (VAC24 is true for 75% of rows) and `lead_x_compete`, plus the narrow ceiling cells below. The first table was also run without stratum centring (not shown; the centred version moves some `phi` differences by 0.01 to 0.015).
- `vac24` / `vac48`: team's OUT rotation players' as-of minutes >= 24 (about one starter) / >= 48, vs < 24 (report required; the production `vac_min`). VAC24 is true for about 75% of rows, so its complement is small and special.
- `lead_out`: the team's highest as-of per-36 rate in the pair's stat(s) (rotation players with long-run minutes >= 20) belongs to an OUT player. Per-possession rates are not in the feature frame; per-36 minutes is the stand-in.
- `compete` (teammates only): both legs are the team's top-3 by as-of `m10` in the stat, ranked among the players who played (a mild look-ahead on who plays; the legs are conditional on playing anyway). `lead_x_compete` stacks the two.
- `blowout`: top tercile (|Elo margin| >= 4.6) vs bottom tercile (<= 2.0) of the pre-game Elo margin (the only pre-game spread we have for 2020-22).
- `fav`: the player's team is the Elo favourite vs underdog (margin / total classes).
Pair classes: same player (pts-reb, pts-ast, ast-reb, pts-fg3m); teammates same stat (4) and cross stat (3, both orientations); opponents same stat (4); player x own-team margin (4); player x game total (4). Statistics: normal-score correlation of the PIT residuals `corr_z` (copula scale; randomised-PIT noise attenuates it for count stats, so differences are lower bounds) and `phi` of the median-line hit indicators (what a parlay pays on). Both are centred INSIDE each stratum so a context-specific marginal bias is not counted as dependence. CI: 1,000 resamples of whole games.

## Result 1: how many cells move, and by how much

114 (class x context) cells with >= 300 pairs and >= 40 games on each side. Game-clustered CI of the correlation difference excludes 0 in 36 cells for `corr_z` and 25 for `phi`. Placebo (the same procedure with the team-game context labels permuted across team-games, player ranks re-drawn at random, 3 shuffles at 300 resamples): seed 1000: 5 / 12; seed 1001: 5 / 12; seed 1002: 4 / 5 cells excluding 0 (z / phi) out of 114, largest placebo |phi diff| 0.036 (noise alone reaches that). So there is real context structure beyond noise in the normal-score correlations (about 36 cells against about 5 under the placebo), and it is small: the largest |phi difference| over all cells is 0.051, and phi differences under about 0.035 are inside the placebo range. The cells overlap and 100+ were examined; read them as a map, not as findings.

Cells where either CI excludes 0, largest |phi difference| first (top 24):

| class | context | n pairs ctx / ref | games ctx / ref | corr_z ctx / ref | diff [95% CI] | phi ctx / ref | diff [95% CI] |
|---|---|---|---|---|---|---|---|
| own_margin:reb | fav | 40,099 / 39,187 | 3,814 / 3,814 | +0.043 / +0.086 | -0.042 [-0.057, -0.029] | +0.021 / +0.072 | -0.051 [-0.066, -0.037] |
| same_player:pts-ast | lead_out | 25,828 / 53,392 | 2,097 / 3,342 | +0.233 / +0.182 | +0.051 [+0.036, +0.067] | +0.183 / +0.138 | +0.045 [+0.031, +0.059] |
| teammate_cross:pts_i-ast_j | lead_x_compete | 18,276 / 487,240 | 2,097 / 3,342 | +0.083 / +0.030 | +0.053 [+0.039, +0.068] | +0.053 / +0.021 | +0.032 [+0.018, +0.046] |
| teammate:pts-pts | lead_x_compete | 4,959 / 286,187 | 1,440 / 3,598 | +0.023 / -0.024 | +0.047 [+0.017, +0.077] | +0.014 / -0.013 | +0.028 [-0.002, +0.057] |
| same_player:pts-ast | vac48 | 38,842 / 20,041 | 2,801 / 1,603 | +0.213 / +0.188 | +0.026 [+0.009, +0.043] | +0.166 / +0.140 | +0.026 [+0.010, +0.042] |
| total:reb | lead_out | 12,655 / 66,565 | 1,140 / 3,693 | -0.018 / -0.047 | +0.029 [+0.008, +0.050] | -0.009 / -0.035 | +0.026 [+0.006, +0.045] |
| same_player:ast-reb | lead_out | 26,041 / 53,179 | 2,109 / 3,338 | +0.238 / +0.202 | +0.036 [+0.021, +0.052] | +0.182 / +0.157 | +0.026 [+0.010, +0.040] |
| teammate:ast-ast | compete | 22,884 / 358,306 | 3,814 / 3,814 | -0.031 / +0.001 | -0.032 [-0.045, -0.017] | -0.026 / -0.001 | -0.025 [-0.038, -0.013] |
| teammate:ast-ast | lead_x_compete | 5,097 / 285,180 | 1,483 / 3,595 | -0.006 / -0.002 | -0.005 [-0.034, +0.025] | -0.025 / -0.001 | -0.024 [-0.053, -0.000] |
| teammate_cross:pts_i-ast_j | compete | 53,731 / 708,649 | 3,814 / 3,814 | +0.074 / +0.032 | +0.042 [+0.033, +0.052] | +0.045 / +0.021 | +0.024 [+0.015, +0.033] |
| teammate_cross:pts_i-reb_j | lead_x_compete | 18,060 / 494,983 | 2,011 / 3,401 | +0.044 / -0.006 | +0.049 [+0.031, +0.067] | +0.020 / -0.004 | +0.023 [+0.008, +0.038] |
| same_player:pts-reb | lead_out | 24,481 / 54,739 | 2,011 / 3,401 | +0.359 / +0.318 | +0.041 [+0.026, +0.056] | +0.253 / +0.230 | +0.023 [+0.008, +0.038] |
| same_player:pts-reb | vac48 | 38,842 / 20,041 | 2,801 / 1,603 | +0.346 / +0.308 | +0.038 [+0.021, +0.056] | +0.243 / +0.222 | +0.021 [+0.005, +0.038] |
| same_player:pts-reb | vac24 | 59,179 / 20,041 | 3,545 / 1,603 | +0.339 / +0.308 | +0.031 [+0.015, +0.046] | +0.242 / +0.222 | +0.020 [+0.006, +0.037] |
| own_margin:fg3m | vac24 | 59,179 / 20,041 | 3,545 / 1,603 | +0.074 / +0.096 | -0.022 [-0.038, -0.007] | +0.048 / +0.068 | -0.020 [-0.035, -0.006] |
| own_margin:pts | fav | 40,099 / 39,187 | 3,814 / 3,814 | +0.079 / +0.093 | -0.014 [-0.029, +0.000] | +0.049 / +0.069 | -0.020 [-0.033, -0.005] |
| teammate_cross:ast_i-reb_j | lead_x_compete | 20,017 / 480,527 | 2,109 / 3,338 | +0.010 / -0.003 | +0.013 [-0.001, +0.028] | +0.017 / -0.002 | +0.019 [+0.004, +0.031] |
| own_margin:fg3m | lead_out | 14,203 / 65,017 | 1,283 / 3,685 | +0.055 / +0.085 | -0.030 [-0.051, -0.011] | +0.038 / +0.057 | -0.018 [-0.036, -0.001] |
| own_margin:fg3m | vac48 | 38,842 / 20,041 | 2,801 / 1,603 | +0.076 / +0.096 | -0.020 [-0.037, -0.004] | +0.050 / +0.068 | -0.018 [-0.035, -0.003] |
| same_player:pts-ast | vac24 | 59,179 / 20,041 | 3,545 / 1,603 | +0.203 / +0.188 | +0.016 [-0.001, +0.033] | +0.157 / +0.140 | +0.017 [+0.002, +0.032] |
| teammate_cross:pts_i-reb_j | compete | 56,882 / 705,498 | 3,814 / 3,814 | +0.024 / -0.002 | +0.026 [+0.017, +0.035] | +0.015 / -0.002 | +0.016 [+0.008, +0.025] |
| own_margin:reb | vac24 | 59,179 / 20,041 | 3,545 / 1,603 | +0.069 / +0.052 | +0.017 [+0.000, +0.031] | +0.049 / +0.035 | +0.014 [-0.003, +0.028] |
| own_margin:reb | vac48 | 38,842 / 20,041 | 2,801 / 1,603 | +0.071 / +0.052 | +0.019 [+0.002, +0.034] | +0.049 / +0.035 | +0.014 [-0.004, +0.029] |
| total:ast | fav | 40,099 / 39,187 | 3,814 / 3,814 | +0.086 / +0.091 | -0.005 [-0.019, +0.008] | +0.052 / +0.065 | -0.013 [-0.027, -0.001] |

Readings. (0) VAC24 alone moves little: same-player points-rebounds +0.020 and points-assists +0.017 (VAC48: +0.021, +0.026), everything else within +-0.01. (1) Leader OUT raises the same-player points-assists, points-rebounds and assists-rebounds co-movement by phi +0.02 to +0.05 (shared minutes and usage move together). (2) Teammates who are both top-3 in a stat: points(A)-assists(B) co-move MORE (stars rise together), assists-assists co-move LESS (the competition the hypothesis predicted), by phi +0.02 to +0.03 and -0.025. (3) Same-stat teammates: points/rebounds/threes show no usable shift in any context (|phi diff| <= 0.011, CIs span 0). (4) Opponents: nothing (|phi diff| <= 0.008, every CI spans 0). (5) Blowout: nothing for any pair class (largest |phi diff| 0.014; every phi CI spans 0 except teammates rebounds-rebounds at -0.009 [-0.016, -0.001]). (6) Favourite vs underdog: a player's rebounds are tied to his own team's result for underdogs (phi +0.072) and barely for favourites (+0.021); points -0.020, assists/threes ~0. (7) 'Shared vacated usage makes teammates compete' is not what the data show for the leader-out context: points-points and assists-assists among teammates become slightly LESS negative when the leader is out (z +0.013, +0.014), phi unchanged.

## Result 2: post-hoc ceiling cells

Chosen after the table, to see how large the largest plausible shift is (each is one look among many, and a cell picked for its size is shrunk in practice; WOWY's coefficient fell inside exactly the slices chosen for size). Same-player `phi` with stratum centring.

| cell | n ctx / ref | phi ctx / ref | diff [95% CI] | 2-leg joint prob change |
|---|---|---|---|---|
| pts-ast: assist leader OUT, player is a top-2 assister vs assist leader present | 3,398 / 62,253 | +0.154 / +0.144 | +0.010 [-0.023, +0.042] | +0.9% |
| pts-reb: scoring leader OUT, player is a top-2 scorer vs scoring leader present | 3,306 / 62,607 | +0.152 / +0.232 | -0.080 [-0.112, -0.045] | -6.5% |
| pts-ast: scoring leader OUT, player is a top-2 scorer vs scoring leader present | 3,306 / 62,607 | +0.116 / +0.143 | -0.027 [-0.057, +0.007] | -2.4% |
| pts-ast: points AND assists leaders OUT with VAC48, vs VAC < 24 | 7,155 / 20,041 | +0.203 / +0.140 | +0.063 [+0.036, +0.090] | +5.5% |
| pts-reb: points AND assists leaders OUT with VAC48, vs VAC < 24 | 7,155 / 20,041 | +0.258 / +0.222 | +0.036 [+0.010, +0.065] | +3.0% |

The Trae-Young cell (assist leader OUT, the remaining top-2 assister's points and assists) shows no change at all (phi +0.010, CI spans 0). The largest swing is the points-rebounds pair of a top-2 scorer when the scoring leader is out: they DECOUPLE (phi -0.080).

## Result 3: in money (legs at p = 0.5, Gaussian copula, Sheppard map from phi)

| parlay | independent | pooled copula | reference group | context group | fair odds pooled -> context | context vs pooled | context vs reference | at far end of every CI |
|---|---|---|---|---|---|---|---|---|
| 2-leg same player: points + assists overs, team leader OUT | 0.250 | 0.2882 (+15.3% vs indep.) | 0.2846 | 0.2957 | 3.47 -> 3.38 | +2.6% | +3.9% | +5.2% |
| 2-leg same player: points + rebounds overs, team leader OUT | 0.250 | 0.3093 (+23.7% vs indep.) | 0.3074 | 0.3131 | 3.23 -> 3.19 | +1.3% | +1.9% | +3.1% |
| 2-leg teammates: A's points + B's assists overs, both top-3, leader OUT | 0.250 | 0.2561 (+2.4% vs indep.) | 0.2553 | 0.2633 | 3.91 -> 3.80 | +2.8% | +3.1% | +4.5% |
| 2-leg teammates: A's assists + B's assists overs, both top-3 assisters | 0.250 | 0.2496 (-0.1% vs indep.) | 0.2498 | 0.2434 | 4.01 -> 4.11 | -2.5% | -2.6% | -3.8% |
| 2-leg rebounds over + own team beats its Elo margin, favourite vs underdog | 0.250 | 0.2614 (+4.6% vs indep.) | 0.2680 | 0.2551 | 3.83 -> 3.92 | -2.4% | -4.8% | -6.1% |
| 3-leg same player: points + rebounds + assists overs, team leader OUT | 0.125 | 0.1944 (+55.5% vs indep.) | 0.1906 | 0.2022 | 5.14 -> 4.95 | +4.0% | +6.1% | +9.0% |
| 3-leg: A's points + A's assists + B's assists (teammates, top-3, leader OUT) | 0.125 | 0.1470 (+17.6% vs indep.) | 0.1449 | 0.1512 | 6.80 -> 6.61 | +2.9% | +4.4% | +5.8% |

What a parlay costs, from data already in the repo (cost = loss per unit at the model's own fair probability; required lift = how far the true joint probability must exceed the price-implied one to offset it):

| cost reference | cost | required lift | source |
|---|---|---|---|
| single-leg alternate lines, sportsbook (ALT_LINE_70, T489) | 7.0% | +7.5% | ledger T489: pooled ROI -0.070 [-0.095, -0.044], n 4,546 rungs |
| single-leg DraftKings alternate ladders (ALT_LADDER_HIST, T491) | 5.7% | +6.0% | ledger T491: pooled ROI -0.057 [-0.062, -0.053], n 76,109 rungs |
| cross-game 2-leg parlay of 70%+ overs (ALT_PARLAY_X, T490) | 12.9% | +14.8% | ledger T490: ge70 2-leg ROI -0.129 [-0.188, -0.069], n 3,337 parlays (pooled cells -0.122 to -0.222) |
| retail 2-leg hold, marketing figure (unverified) | 7.0% | +7.5% | docs/research/lines_everywhere_2026-10-14.md B3: 6-8% for two legs; 15-25% for four-leg SGPs |
| Kalshi taker fee at a 28-cent contract, 100-lot order (verified config) | 5.1% | +5.3% | configs/parlay.yaml fee_model kalshi_fee_schedule (verified=True); excludes the bid/ask spread |

The largest swing between the context group and its reference group over the seven typical parlays is +6.1% (+9.0% if every pair sits at the far end of its CI at once); the largest post-hoc ceiling cell is +6.5%. Expected log loss saved per parlay by conditioning (inside the context segment; `log_loss_gain_ctx_segment` in the JSON): between 4e-05 and 2e-04 nats, below anything the 2023-24 joint evaluation could resolve (Gaussian vs independence there was -0.00053 [-0.00106, -0.00003] on 15,798 parlays).
