# Reading log: in-game state (information program item 2), 2026-11-17

DRAFT intake note (modeler). **Owned elsewhere**: branch `agent/modeler-live-model` is measuring feed delay, drafting the in-game rule
and writing the reading log tonight. This note only places item 2 in the program. Advice or momentum: *follows advice* for the rule
(the 2026-11-17 decisions review: in-game logs accrue without a frozen rule); the live model itself is Devin's request.

## Claim (restated)
The box score so far, fouls, score margin and minutes left predict a player's final stat line better than the pre-tip forecast. Target:
P(player finishes at or above a line | state at a checkpoint).

## Information and known-at
Knowable during the game, with feed delay: ESPN summary polled about every 60 s in the capture; the live-model branch measures the
lag against ESPN's own event wall clock and against Kalshi moves (results on that branch, descriptive). A checkpoint forecast may use
only state received (our clock) before the checkpoint's `made_at`. Forecast time: **in-game only**; it never enters a pre-tip forecast.

## Captured today
pbp history 2013-2024 (`data/history` pbp 11,516 files; main pbp 5,484 files), stints 2013-2026 (history 612,948 rows; main 2022-26),
live capture `data/raw/live_<date>/` for 27 nights from 2026-10-21 (11-03 missing), 15-second Kalshi game quotes from 2026-11-10.

## Prior evidence here
IN_GAME_LINE_MODEL (T508): pass on every pre-registered criterion, about 8x the floor, then the attack found most of the gain was a weak
baseline (a player-aware pace baseline closes most of it). INGAME_WINPROB_V2 (T513): frozen stage 1, gate pass. Live win probability is
heavily priced by the market (MODEL_PLAYBOOK).

## Place in the program
* **Not a member of F-INFO.** Different forecast time (in-game), different target (line probability at checkpoints), different
  comparator (Kalshi in-game mid and a player-aware pace baseline). Pooling it with pre-tip members would mix families after the fact.
* **Dependency:** none on the pre-tip members. It shares the live capture with KALSHI_NEWS_MOVES (item 8): both read the same Kalshi
  prop pages; Stage 0 of item 8 should reuse the live-model branch's clock alignment (our receive clock vs exchange time) rather than
  build its own.
* **Order:** parallel to everything here (separate lane).

## Data audit (non-negotiable 8)
Owned by the live-model lane; the T513 gate (label-free, 2022-24, 1,230 games each) is the current audit.

## Verdict
**PURSUE (owned elsewhere).** Expected to be useful for display and teaching; a betting claim only through a frozen forward test against
the market.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: possible on play-by-play 2013-2024 (T508 did it, select 2023 / report 2024), but history has no feed delay, so a
backtest flatters any checkpoint forecast that would in practice see the state 30-140 s late; the live-model lane's delay measurement
is the correction. Owned there.
