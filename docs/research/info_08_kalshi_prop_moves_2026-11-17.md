# Reading log: Kalshi prop price moves as a sign that news arrived (information program item 8), 2026-11-17

DRAFT intake note (modeler). Member of F-INFO as **KALSHI_NEWS_MOVES** (`docs/prereg/INFO_PROGRAM_2026_27.md`). Advice or momentum:
*Devin's override* (all eight). Source of the data finding: `docs/reviews/kalshi_prop_history_2026-11-17.md` on branch
`agent/market-prop-candles` (market agent; not merged).

## Claim (restated)
When news about a player arrives (a scratch, a minutes limit, a late upgrade), traders move his Kalshi prop prices before our official
inputs show it. A common move across all of a player's prop markets, with no matching report change, flags news we do not have.
Target: P(play) at T-15 (primary), then props CRPS through minutes.

## Information and known-at
* The live capture stores every Kalshi NBA prop market (PTS, REB, AST, 3PT, PRA, STL, BLK series) about once a minute inside the capture
  window (on 2026-11-16: 2,340 prop markets, 684,485 polls, median gap 60 s, from 22:00Z, about two hours before the first tip); the
  15-minute snapshot covers the rest of the day (171,154 points); trade prints from 2026-11-16 with about 100% print coverage.
* Known-at = **our receive time** (`received_at` in the capture manifest), never Kalshi's `created_time`. A move counts for a forecast only
  if its last poll was received before the forecast's `made_at`.
* Forecast time: **T-15** (the move window [T-120, T-15]); a T-60 version would use the 15-minute snapshot only and is weaker.
* Mechanism: **minutes / P(play)**. The Kalshi DNP rule (verified 2026-11-10: a withdrawal before tip settles at the last fair price, not
  No) means prices of a scratched player do not collapse to zero; the signal is the move before the scratch is public, not after.

## What we capture today (file listings only; no capture page was parsed tonight)
* `data/raw/live_<date>/kalshi/` prop series folders on 27 nights, 2026-10-21 to 2026-11-17 (5 series folders on 10-21, 11 from 10-22,
  13 from 10-29, 15 from 11-12; **11-03 has no live folder**).
* `data/kalshi/raw/` 45 dated folders from 2026-10-08 (15-minute snapshot pages); `data/kalshi/kalshi.duckdb` (not opened).
* Trade prints from 2026-11-16 only.
* Name resolution: on 11-16, 1,886 of 2,340 markets resolved to a player id, 454 unresolved (29 names, all "not in the reviewed alias
  table"); 47 names across 11-16 and 11-17 (market agent's review). About 19% of markets cannot meet the model until the alias review.
* No Kalshi prop history before 2026-10-21 here. Kalshi prop prices of the 2025-26 season would be holdout-season data: never read.

## Missing and cost
* **Stage 0 counts** (label-free): per night, players with a material common move in [T-120, T-15], and how many of those had an official
  report change, a T-15 lineup change, or neither; the lead of the move over the first report stamp carrying the change. Uses pre-tip
  facts only (report stamps up to tip, the lineup feed up to tip), never a box score or a DNP flag, so it reads no outcome and is allowed
  before the LINE_CAL_LIVE look. The rate of correctly flagged scratches is therefore projected from moves that precede a report OUT
  posted after T-15, not from who played. Cost: modeler 0.5-1 day on copies of the capture (the market agent's
  `nba/kalshi/prop_history.py` already reads them without touching live stores).
* Alias review of the 47 unresolved names: Devin about 15 minutes (candidates file already prepared by the market agent).
* A clock check (our receive clock vs Kalshi's): reuse the live-model branch's feed-delay work.

## Outside evidence
I found no peer-reviewed study of how fast prediction-market or sportsbook prices react to injury news; search results were industry
blogs and vendor pages asserting fast moves and overshoot, with no data (not credible as evidence). Internal evidence is stronger: on
2026-11-10 all three replayed under alerts were Pinnacle lagging news Kalshi had already priced, and 14 of 17 shown unders saw Pinnacle
move toward Kalshi (0.82, game-clustered CI [0.68, 1.00], 11 games; `docs/reviews/cards_unders_2026-11-10.md`). Sources consulted:
[LSports blog](https://www.lsports.eu/pt-br/?p=26756), [Odds Reference guide](https://oddsreference.pages.dev/sports/learn/line-movement).

## Prior evidence here
LINE_MOVE_TOWARD_MODEL (T510, frozen, forward): do lines move toward our model (a different question: our skill, not their news).
MARKET_UPDATE (draft, never frozen): start from the T-60 price and adjust for later news (the reverse construction). INJURY_TIMING and
LATE_REPORT: fresher official report. Nothing earlier used Kalshi prop moves as an input.

## Draft rule summary (the forward stage-2 rule, fixed transform, nothing fitted)
* Move definition (frozen before Stage 0 is read): for player i, the median over his resolved prop markets of the change in mid
  (bid/ask midpoint, spread <= 5c) between the first poll at or after T-120 and the last poll received before T-15; "material" if the
  median change <= -6c on at least 3 markets (or >= +6c for upgrades). Thresholds are fixed now, not tuned.
* Treatment: for a material downward move with no OUT/Doubtful on the T-15 report, p_play_T = 0.5 x p_play_B (upward: p_play_T =
  p_play_B + 0.5 x (1 - p_play_B)). Fixed factors, stated now; a different factor is a new id.
* Primary: P(play) log loss on all logged rows at T-15 (DNP included), arm minus production-at-T-15 (umbrella section 5); floor -0.005;
  date-clustered (binding) with player-season as a replication.
* Secondary: props CRPS through the minutes ratio on flagged rows (descriptive); Stage 0's lead distribution.

## Gate (umbrella section 6)
* All-rows n (live, post-12-01): 797 logged T-15 games x about 26 not-OUT rostered players = **about 20,700 rows**; per-row sd 0.25
  assumed, design effect 1.2: **h about 0.0037 nats**. The data resolve the floor.
* e_plan: unknown. Arithmetic for what is needed: a correctly flagged post-T-15 scratch moves its row by about ln(0.7 / 0.1) = 1.9 nats
  (p_play 0.9 to 0.45 under the factor above saves about ln(0.55/0.10) = 1.7 nats); false alarms cost about ln(0.9/0.45) = 0.7 nats each on
  players who play. To reach -0.005 nats a row on 26 rows a game the net gain must be about 0.13 nats a game: (1.7 a - 0.7 f >= 0.13 for a correct
  flags and f false alarms a game) about **0.08 correctly flagged scratches a game with no false alarms, or about 0.12 with 0.1 false
  alarms**. Whether Kalshi moves carry that many is exactly Stage 0's
  count.
* **Result: PENDING Stage 0.** The forward stage 2 is freezable after Stage 0 if its counts (on nights before the freeze, label-free) project power >= 0.5.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Kalshi prop quotes, 60 s | 2026 from 10-21 (27 nights) | about 680,000 polls a full slate (11-16) | 11-03 missing; capture starts about T-2 h for the first game | 11-03: maybe via Kalshi's public candlesticks endpoint (retention unverified) | market agent 0.25 day |
| Kalshi prop quotes, 15 min | 2026 from 10-08 | about 171,000 points a slate | none known | n/a | n/a |
| Trades | 2026 from 11-16 | 139,212 prints on 11-16 | before 11-16 none | official 1-minute candles per ticker (public) | about 2,300 requests a night, market agent |
| Name aliases | 2026 | 81% of markets resolved (11-16) | 47 names | yes | Devin about 15 min |
| History before 2026-27 | none admissible | n/a | 2025-26 is the holdout season | no | n/a |

## Playbook check
Class: information source feeding a fixed adjustment of production's P(play) (no fit; quantile layer for props). (1) Information: yes,
if moves lead the report. (2) Data: no fit, so no appetite; power comes from the live season. (3) Validity: receive clock; thresholds
frozen before any count is read; flagged rows are a selected population (stated); the T-15 comparator includes LATE_REPORT and the T-15
lineups, so news the report already carries is not credited. (4) Lineage: MARKET_UPDATE, LATE_REPORT, LINE_MOVE_TOWARD_MODEL. (5) Scores
against production P(play) at T-15; the market itself has no P(play), and at the line we expect to lose to Kalshi's own price.

## Verdict
**PURSUE Stage 0 (label-free, no new data)**; the forward stage-2 rule only if Stage 0 projects power. Likely null at the -0.005 all-rows floor: post-T-15
scratches not on the report or the lineup feed are rare. Prior: P(Stage 0 shows the move leads the report change on at least half of
matched events) 0.45; P(forward stage 2 freezable) 0.35; P(pass) 0.10.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: **impossible.** Kalshi prop quotes exist here only from 2026-10-21, and the 2025-26 season's prices are holdout-season
data whatever the source. Straight to a forward stage 2. Time to first answer: Stage 0 about 1 week (label-free); the stage-2 claim at END
(about April 2027).
