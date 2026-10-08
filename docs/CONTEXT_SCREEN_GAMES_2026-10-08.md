# Game location / tip time / market size screen (exploratory, seasons 2022-24)

## Summary

Question: does where, when, and in what market a game is played explain what the injury-adjusted
Elo (`rung0_injury_elo`) misses? Almost certainly not, with one weak exception.

* 14 pre-registered tests (BH q=0.10, effect floor dLL >= 0.001 per game). Exactly one survivor:
  **`is_national_tv`** (home teams win more than Elo expects in nationally televised games;
  +0.098 log-odds per SD, game-bootstrap CI [+0.030, +0.167], dLL +0.00106, a hair above the
  floor). It holds in the regular-season-only rerun (below) and under team-season clustering.
  It is a CANDIDATE only. Plausible confound: national games feature the best/most-rested
  teams and Elo is imperfectly calibrated at the top end; not a tip-time or location effect.
* Altitude (Denver/Utah): direction matches the prior (home win rate 61.3% vs Elo mean 57.7%,
  residual +3.6 pts) but the CI includes 0 (n=271) and the screen test is p=0.081 (not a
  survivor, dLL 0.0004). Elo's home-court term plus team strength already absorbs most of it.
  Note PHX/OKC/ATL (0.3-1.0 km) show a similar +3.9 pt residual, so it is not specific to height.
* West-to-east early tips (visitor body-clock < 17:00, >= 2h zone shift; n=279): home win rate
  48.0% vs Elo 51.0% (residual -2.9 pts, CI [-8.3, +2.7]); direction opposite to the folk prior
  ("west teams lose early eastern tips" would imply the *home* team over-performs). Not significant.
* Market size, tip bucket, tz shift, days into season, neutral site: nothing at q=0.10.
  Near-misses (p 0.05-0.08, dLL < 0.0005): `tip_late`, `days_into_season`, `home_log_pop`,
  `home_altitude_km`; all below the effect floor and none would survive the stricter BH q=0.05
  standard of the confirmatory plan. Treat as noise unless replicated on fresh seasons.

Season 2025 (burned holdout) was never loaded (asserted absent in `load_frame`).

## Results (all 14 tests; n=3951 games, 2022-24 incl. play-in/playoffs; B=2000, seed 20261008)

Coefficient c = log-odds change in home win per 1 SD of the feature, beyond `logit(p)`
(fit: `y ~ a + b*logit(p) + c*z`). dLL = in-sample per-game log-loss gain from adding the feature.
CI(G) game-clustered (= row bootstrap, one game per cluster); CI(T) clustered by home-team x season.
BH is on the G-bootstrap p-value.

| feature | c per SD | CI(G) | CI(T) | dLL/game | p(G) | verdict |
|---|---|---|---|---|---|---|
| home_altitude_km | +0.065 | [-0.009, +0.139] | [-0.024, +0.171] | +0.00042 | 0.081 | no |
| altitude_diff_km | +0.004 | [-0.065, +0.077] | [-0.078, +0.075] | +0.00000 | 0.870 | no |
| home_log_pop | -0.059 | [-0.128, +0.008] | [-0.133, +0.017] | +0.00037 | 0.083 | no |
| away_log_pop | -0.054 | [-0.117, +0.012] | [-0.111, +0.003] | +0.00031 | 0.122 | no |
| log_pop_gap | -0.003 | [-0.070, +0.062] | [-0.069, +0.067] | +0.00000 | 0.904 | no |
| away_bodyclock_hr | +0.026 | [-0.043, +0.092] | [-0.049, +0.099] | +0.00007 | 0.458 | no |
| tz_shift_east_hr | -0.024 | [-0.094, +0.043] | [-0.098, +0.047] | +0.00006 | 0.510 | no |
| west_to_east_early | -0.049 | [-0.119, +0.019] | [-0.115, +0.016] | +0.00026 | 0.144 | no |
| tip_matinee | -0.012 | [-0.084, +0.055] | [-0.082, +0.064] | +0.00001 | 0.766 | no |
| tip_early | -0.004 | [-0.069, +0.065] | [-0.075, +0.061] | +0.00000 | 0.893 | no |
| tip_late | -0.058 | [-0.141, -0.000] | [-1.202, +0.000] | +0.00035 | 0.051 | no (n=11 late tips; CI(T) degenerate) |
| is_neutral | -0.034 | [-0.922, +0.036] | [-0.920, +0.034] | +0.00013 | 0.290 | no (4 games; unidentified) |
| days_into_season | -0.067 | [-0.138, +0.001] | [-0.139, +0.003] | +0.00049 | 0.055 | no |
| **is_national_tv** | +0.098 | [+0.030, +0.167] | [+0.037, +0.162] | **+0.00106** | **0.005** | **SURVIVOR (candidate)** |

BH thresholds at q=0.10, m=14: rank-1 0.0071, rank-2 0.0143 ... only p=0.005 clears; the
next-smallest p (0.051) misses its 0.0214 line.

Post-hoc diagnostic (NOT in the family; do not BH-pool): regular season only (n=3683),
`is_national_tv` c=+0.102, CI [+0.030, +0.170], dLL +0.00114, p=0.004. Home win rate in national
regular-season games 59.9% vs Elo 55.2% (n=822); non-national 54.4% vs 55.0% (n=2861).
`days_into_season` regular only: c=-0.094, CI [-0.165, -0.022], p=0.016 (descriptive, post hoc).

## Descriptive (not tests; residual = y - p, CI by game bootstrap)

| group | n | home win | mean p | resid | resid CI | mean log loss |
|---|---|---|---|---|---|---|
| home altitude >= 1.0 km (DEN, UTA) | 271 | .613 | .577 | +.036 | [-.020, +.090] | .606 |
| home altitude < 1.0 km | 3680 | .554 | .550 | +.004 | [-.012, +.018] | .617 |
| tip matinee (<17h local) | 299 | .552 | .555 | -.003 | [-.054, +.055] | .635 |
| tip early (17-19h) | 487 | .548 | .544 | +.004 | [-.036, +.046] | .605 |
| tip standard (19-21h) | 3154 | .561 | .552 | +.008 | [-.009, +.025] | .617 |
| tip late (>=21h) | 11 | .364 | .593 | -.230 | [-.434, +.001] | .551 |
| home market small tercile | 1427 | .546 | .529 | +.017 | [-.009, +.039] | .611 |
| home market mid tercile | 1224 | .600 | .591 | +.009 | [-.019, +.035] | .613 |
| home market large tercile | 1300 | .531 | .538 | -.007 | [-.032, +.017] | .626 |
| west->east early | 279 | .480 | .510 | -.029 | [-.083, +.027] | .639 |
| not west->east early | 3672 | .564 | .555 | +.009 | [-.006, +.024] | .615 |
| neutral site | 4 | n too small | | | | |

Large markets have the highest log loss (.626) simply because they are more evenly matched with
more volatile outcomes; this is not a residual bias (resid ~ 0).

## Data and sources

* Tip time / arena: `nba_api` ScheduleLeagueV2, seasons 2022-23, 2023-24, 2024-25, cached raw at
  `data/schedule/raw_<season>.parquet`; derived features at `data/schedule/game_context_features.parquet`
  (not in nba.duckdb). `gameDateTimeUTC` is the schedule's tip instant (scheduled, not actual).
* Team static: `configs/team_markets.yaml`. Populations are US Census 2020 MSA (Toronto: StatCan
  2021 CMA), altitude approximate site elevation, coordinates from `nba/ingest/arenas.py`.
  **All numbers were entered from memory with no network verification**; 16 teams flagged
  `pop_confidence: high`, the rest `check`. Analysis uses log(pop), so a few percent error is
  immaterial, but verify before relying on it. LA and NY teams share a metro (gap = 0 for
  intra-city games).
* Outcomes / national TV: `nba.duckdb` `games` (read-only). Predictions:
  `data/injury_elo/oof_predictions.parquet`, model `rung0_injury_elo`, 2022-24.
* Results JSON: `reports/context_screen_games/results.json`.

## Caveats

* Exploratory. n=3951 games; a 1-df feature needs |c| ~ 0.07 per SD to be detectable, i.e. dLL
  ~ 0.0005. The floor (0.001) is deliberately above the noise level (~0.0001 expected by chance).
* Game-clustered bootstrap is a row bootstrap here (one OOF row per game); CI(T) is the more
  honest one given shared team strength. All survivors held under both.
* `west_to_east_early` / body-clock use home-zone to home-zone only; the visitor's actual prior
  city (e.g. on a road trip, or arriving the day before) is not modelled. Local tip features are
  wrong for the handful of international games. `isNeutral` in the raw schedule is False for
  2022-24 Paris/Mexico City games, so neutral = flag OR different arena state (4 games; the
  Las Vegas Cup final is id 006, absent from the OOF set). Initial implementation flagged
  Inglewood/Austin as neutral (56 games); fixed before the reported numbers (bug fix, same
  feature definition and family).
* Playoffs and play-in are included (OOF covers them); `days_into_season` thus conflates season
  phase and postseason.
* `is_national_tv` is a schedule fact but correlates with team quality/rest and star availability.
  The OOF Elo already includes injuries; the residual could be a calibration issue at high-p
  games rather than a TV effect. Next step if pursued: add to a fresh-season (not 2025) A/B only
  after a pre-registered plan; **2025 is spent and must not be used**.
* `ruff format` was accidentally run on the whole `nba/` package once; it may have re-wrapped
  other in-flight files (formatting only). Review `git diff` before committing.
* `nba/features/game_context.py` already exists (tracked: travel, national TV, standings), so
  the new features live in `nba/features/game_location_context.py` instead; no existing file
  was edited.
