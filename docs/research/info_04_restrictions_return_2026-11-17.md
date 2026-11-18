# Reading log: minutes restrictions and return from injury (information program item 4), 2026-11-17

DRAFT intake note (modeler). Member of F-INFO as **RETURN_RAMP** (`docs/prereg/INFO_PROGRAM_2026_27.md`). Advice or momentum: *Devin's
override* (all eight); the returner bias was first flagged by the modeler (REPORTER_FEED_LIVE note, 2026-10-27), so this part also
*follows advice* ("build the free returner rule").

## Claim (restated)
A player returning from an absence plays fewer minutes than his recent role says, for a few games, by an amount that depends on how long
he was out and why. Stated limits ("on a 20-minute restriction"), back-to-backs and load management add more. Target: minutes (then
pts) for returners.

## The four sub-items and where each goes
| Sub-item | Knowable when | Status |
|---|---|---|
| (a) games since return, absence length | from prior box scores, before the morning of the game | **RETURN_RAMP (this member)** |
| (b) report reason at the last listing (injury type, "Injury Management", surgery, illness, G League) | official report, hours before tip (2026 export: 439 rows with reasons such as Left Hamstring Strain, Knee Injury Management, Surgery) | inside RETURN_RAMP as a coarse category; INJURY_REASON was a stub, never run |
| (c) stated minutes limits | reporters only (the report never carries them) | owned by REPORTER_FEED_LIVE (M1, C1) |
| (d) back-to-backs, load management, schedule density | schedule, days before | **CLOSED** (below) |

## Information, known-at, forecast time, mechanism
(a) and (b) are as-of: box scores strictly before the game, report stamp <= real tip - 60. Forecast time: T-60 (production's) and every
later one. Mechanism: **minutes** (the size of the cut and how fast it fades), then pts through the minutes column.

## Captured today
* Box scores 2013-2024 (history `player_game_stats` 2013-2021 about 27,500-33,800 a season; main 2022-2024 about 34,000-35,000) and
  2026 live (5,840 rows by 2026-11-17). Season 2025 held, never read.
* Report reasons: 2019-2021 (history `player_availability` 44,010 / 36,702 / 278,390 rows; T315: 2019-21 reports are structurally
  different), 2022-2024 (26,757 / 29,378 / 31,477), 2026 (32,905).
* Measured on 2023-24 (descriptive, REPORTER_FEED_LIVE note section 7B; `reports/reporter_feed/rates_2023_24.json`): rotation returners
  (team missed >= 3 straight games, prior-10 average >= 15 min) 0.252 / 0.315 a game (310 / 387 rows); production over-forecasts their
  minutes by **-3.80 [-4.66, -2.93]** / **-2.84 [-3.57, -2.14]** min (K >= 10: -6.45 / -4.86); minutes CRPS 4.70 / 4.37 vs 4.30 / 4.28 on
  all rows; sd of (y - P mean) 7.27 / 7.25.

## Missing and cost
* Rows for returner games 2 and 3 and their bias: not measured (assumption: 2.5 games per return, bias about -1.5 to -2.0 min on games
  2-3). Cost: a label-free count plus a descriptive on the stored `reports/minutes_v2/minutes_scores.npz` for 2022 (warm-up season),
  modeler 0.5 day, maintainer command (it reads nba.duckdb read-only).
* Production's minutes sigma per live row is not logged (only `proj_minutes` = mu); a minutes CRPS on live rows needs sigma logged or
  a verified reconstruction (same dependency as REPORTER_FEED_LIVE). Steward 0.5 day on the live path (oracle, Devin's go).

## Outside evidence
Return-to-play minutes restrictions are standard practice reported by beat writers; no peer-reviewed NBA effect size was looked up
(the internal measurement is stronger than any I could verify). Unverified.

## Prior evidence here
* Production already sees `gap_days`, `since_flag`, `flag_dir`, `since_team` (RETURNING_TEAMMATE_2 playbook check) and still shows the
  -3 min bias above, so the gap is real and not closed by those columns.
* WORKLOAD (T451-T454): fatigue, schedule density and travel add nothing; the passing cell was recent-role minutes (P(play)).
  WORKLOAD_PTS_V2 (T521): powered null; LOADX, SCHEDULE, TRAVEL families each under 0.002. RECENT_ROLE / PI_RECENT_ROLE (T455-T461):
  near-misses on the same mechanism. PPLAY_CONTEXT (T316): b2b habits add nothing. B2B_ELO: gate failed (draft). **So (d) is closed
  and not re-proposed.**
* RETURNING_TEAMMATE / _2: the teammate side of a return (gate failed by 1% pooled). TEAMMATE_SITS (T526): the absence side helps pts.
* SEASON_BOUNDARY (T515, draft): returners across seasons, different population (off-season, not injury).

## Fit with the pipeline
No new model class: a small, monotone adjustment of production's minutes distribution for returner rows only (shift and scale as a
function of games since return 1-3, absence length band, reason category), fitted on 2013-2022 rows (history plus warm-up), applied as
a fixed transform at serve time; props rescaled through the minutes ratio (the REPORTER C2 recipe). Non-returner rows are untouched.

## Draft rule summary
(Written before the two-stage addition; the section "Two-stage design" at the end governs the windows: stage 1 on 2023 -> 2024 is
selection, stage 2 is the claim, default the one logged 2025 touch, with the forward look for serving. The metric, floor, baseline and
guards below are unchanged.)
* Primary: minutes CRPS on returner games 1-3 (played rows), arm minus production-plus-constant-shift (B_ret refitted on 2013-2022, not
  the 2023-24 constant); floor -0.10 min; CI clustered by player-season (binding) with game and date as replications.
* Secondary (member BH): pts integer CRPS on the same rows (floor -0.005); log loss at the main line vs the market where a line exists.
* Too-good bound: beyond -0.42 min on first-game rows (the constant-correction oracle) is a suspected leak.
* Placebo: the returner flag moved to a non-return game of the same player.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Box scores (returner definition) | 2013-2024, 2026 | about 27,500-35,000 player-games a season | none material | n/a | n/a |
| Report reason text | 2019-2024, 2026 | 2019-21 different structure (T315) | 2013-2018 none | no (reports before 2019 not archived here) | n/a: the box-only features cover 2013-2018 |
| Production minutes on history | 2022-2024 stored | 27,619 / 27,583 rows (MINUTES_V2) | 2013-2021 not run | yes, a production-recipe run on history | modeler 1 day, maintainer command |
| Live minutes sigma | 2026 | not logged | gap | yes | steward 0.5 day |

## Gate (umbrella section 6)
n (live, post-12-01): 927 games x 0.25-0.315 first games x 2.5 = **584-732** rows. Per-row sd of the paired minutes-CRPS difference 1.35
(simulated: 1.88 at a 3.3 min bias, 0.86-1.15 at 1.5-2.0, weighted 40/60; a constant correction on Normal(sd 7.25) outcomes, seed 0,
200,000 draws). h = 1.96 x 1.35 x sqrt(1.5 / n) = **0.134-0.120 min**. e_plan = 0.6 x oracle 0.24 = **-0.14 min**. Power (point <
-max(0.10, h)) = **0.54-0.63**. **Result: YES, borderline.** It turns NO if games 2-3 carry less bias than assumed or the ramp factor is
below about 2.0; the label-free count is due before the freeze. History option (Devin's choice): 2013-2022 fit, 2023-2024 report pooled
(697 first games x 2.5 = about 1,740 rows, h about 0.078) passes clearly, with the caveat that 2023-24 bias was seen descriptively (the
RETURNING_TEAMMATE_2 "consistent with" reading).

## Playbook check
Class: quantile / distribution layer on production's minutes (a fixed reshaping), fitted coefficients from history. (1) Information:
absence length and reason are pre-tip facts production uses only partly. (2) Data: about 350 first-game rows a season x 10 seasons for
the fit; enough for a few coefficients. (3) Validity: comparator includes a free constant shift so the arm is credited only for the
ramp; per-player clustering; minutes on played rows and P(play) left to production (the population is conditional on playing: stated).
(4) Lineage: REPORTER C1r, RETURNING_TEAMMATE (teammate side), WORKLOAD (closed fatigue). (5) Scores against production plus constant
(minutes CRPS) and the market at the line (descriptive).

## Verdict
**PURSUE** (no new data; first in order with item 1). Prior: P(freezes after the count) 0.60; P(live pass) 0.25.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 (backtest, selection): **possible now, no new data.** Fit the ramp on 2013-2023 returner rows (box-only features) or, minimal
design per the coordinator, fit 2023 / report 2024; select on 2024 against production plus a constant shift fitted on the fit seasons.
Known-at: box scores exact; report reasons only from stamps <= tip - 90 (the 2022-24 stored stamps are on a clock 30 min early,
INJURY_TIMING rev 3), so the reason column cannot leak. Flattering risk: the -3 min bias was seen on 2023-24 descriptively, so a 2024
pass reads "consistent with" (RETURNING_TEAMMATE_2 precedent), never a claim. Run: about 10-20 min CPU on the Mac outside game windows.
Time to first answer: about 4 working days. Stage 2 (claim): ONE logged 2025 touch (about 875 returner rows 1-3, h about 0.110 min, power
about 0.71 at e_plan: passes the gate) and the forward look for serving (needs live sigma logging). Draft row: "INFO RETURN_RAMP, frozen
<sha8>: minutes CRPS on 2025 returner games 1-3, arm minus production plus constant shift; first and only touch."
