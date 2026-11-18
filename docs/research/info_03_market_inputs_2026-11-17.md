# Reading log: the market's own numbers as inputs (information program item 3), 2026-11-17

DRAFT intake note (modeler). Member of F-INFO as **PROP_LINE_INPUT** (`docs/prereg/INFO_PROGRAM_2026_27.md`). Advice or momentum:
*Devin's override* (he chose all eight; the main session recommended items 1 and 2). Claude's view, stated before any result: this
member will probably improve CRPS and cannot, by construction, produce an edge at the line it reads (T517).

## Claim (restated)
Two numbers: (a) the book's main prop line, (b) the game spread and total as blowout and pace signals. Fed to the props model as inputs,
they reduce props error. Target: integer-support CRPS of pts (primary), reb, ast, fg3m.

## The information and when it is knowable
* Prop main lines: posted the day before to the morning of the game; our capture stores snapshot kinds `morning`, t480 ... t60, t45,
  t30, t20, t15, t10 (`prop_lines.snapshot_kind`, export of 2026-11-17). A line is an input only if its snapshot's **received_at <=
  forecast made_at**. The t15 snapshot is taken in [12.5, 17.5] min before tip, so it can arrive after a T-15 cutoff; a T-15 forecast
  uses the t20 snapshot (or the latest received before the cutoff).
* Spread and total: same timing, game markets.
* Forecast times: T-60 (history select stage; Pinnacle and consensus t60 in history) and T-15 (live report).
* Mechanism: mostly **minutes** (the line knows the injury news and rotation talk we lack) and some **rate** (matchup); the spread and
  total carry **game environment** (blowout minutes, pace).

## What we capture today (counts)
* History (`data/odds/odds_history.duckdb`, the-odds-api historical, t60 and t5): 2023 and 2024 props (2025 exists and is the holdout:
  never read). 2024 report-season legs, Pinnacle / consensus t60: pts 13,021 / 17,993; reb 6,645 / 17,595; ast 6,771 / 16,926; fg3m
  11,091 / 16,030 (T203-T218). 2022: games only, no props.
* Live (export copy of 2026-11-17, 56 games 11-10 to 11-17, season 2026 only, no outcome read): main lines for pts at t60 in 30 of 56
  games (164 player-legs, about 5.5 a game with lines), t30 35 games (308), t20 35 (307), t15 33 (392); **Pinnacle** at t60 in only 4 of
  56 games (10 legs), at t15 21 games (680 legs, all stats). 13 books in the window (DraftKings, FanDuel, Caesars, Fanatics, BetMGM,
  BetRivers, Bovada, BetOnline, MyBookie, ProphetX, Novig, Kalshi, Polymarket). The week includes the the-odds-api key outage (11-14 to
  11-16, `401 DEACTIVATED_KEY`), so these counts are a low-side estimate.

## Missing and cost
* Live T-60 Pinnacle prop coverage is thin (4 of 56 games). Cost: archivist / market agent check of the snapshot schedule and the new
  key (0.25 day); credits per `configs/cost.yaml` (not read here; any increase is Devin's).
* History props before 2023: not held. The-odds-api historical props availability before mid-2023 **not verified**; paid credits.
  Not needed for the gate.

## Outside evidence
None needed: the claim is that the market's line carries information. Credibility is internal (ODDS_HISTORY H1: the market beats the
production model at the line in all 32 cells, +0.007 to +0.024 log loss, game-clustered, 2023-24).

## Prior evidence here (do not re-propose a closed idea)
* **Spread and total as props inputs: CLOSED.** GAME_MARKET_CONTEXT (frozen fd37a3f3, T389-T414): replacing or adding the T-60 spread
  and total moved CRPS by at most 0.005, every arm within 0.001 of its within-date placebo; Elo margin r 0.90 with the spread.
  Re-derived by the adversary (T501, stands). Nothing in item 3(b) is different (same numbers, same timing); **not re-proposed**.
  PACE_TOTALS (T472): the line already has the pace.
* At the line: ODDS_HISTORY H2 blend indistinguishable from the market in 30 of 32 cells (reb vs Pinnacle the exception, WOUNDED,
  T237); MARKET_LINE_CLASSIFIER (T320-T324) worse than the book; MARKET_SHRINK (T415-T419) no positive EV; MARKET_ANCHORED_PROPS (T517):
  the model adds about -0.0003 log loss beyond Pinnacle, a third of the info floor; MARKET_PRIOR_RESIDUAL (T502) closed at the draft
  (its verdicts could not separate model information from market mis-calibration).
* **What is different here:** none of those used the main line as an input to the *whole distribution* scored by CRPS. The question
  is whether the line re-centres our distribution (minutes and rate), not whether we beat the line. The information is the same as H2's;
  the construction and target differ.

## Fit with the pipeline
A residual on top of production: one extra input group to the context-residual mean head (line minus production median, the line's
de-vigged over probability, books agreeing) and, by construction, no change where there is no line (rows without a line keep production
exactly; the arm is scored only on line rows). History select at T-60; live at T-15 inside the T-15 tick. Live path work: a shadow
model name in the T-15 tick (steward, replay oracle, Devin's go).

## Draft rule (member rule to be written after the select stage)
(Written before the two-stage addition; the section "Two-stage design" at the end governs the windows: stage 1 on 2023 -> 2024 is
selection, stage 2 is the claim, default the one logged 2025 touch, with the forward look for serving. The metric, floor, baseline and
guards below are unchanged.)
* Primary: pts integer CRPS, line rows, arm minus production-at-T-15 (umbrella section 5); floor -0.005; date-clustered (live) with
  player-season and game as replications, the widest binds.
* **Select stage (history, part of the frozen rule):** fit on 2023 blocks walk-forward, evaluate on 2024 at T-60 with Pinnacle, then
  consensus. Output e_S with its CI. **Mechanical live gate:** freeze the live claim iff e_S <= -h_live, where h_live is recomputed
  at the freeze date from the live line density then measured. If not, stop: record as a select-stage result, no live claim.
* Must-report beside it: log loss at the main line vs the de-vigged market (expected: not better than the market; T517). A member that
  passes CRPS and is worse than the market at the line earns no betting statement.
* Too-good bound: more negative than -0.058 pts (the actual-stints oracle, F11) is a leak until shown otherwise.
* Placebo: the player's line from his previous game.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Historical prop lines (t60, t5) | 2023, 2024 (2025 held, holdout) | pts 13,021 Pinnacle / 17,993 consensus legs (2024) | 2013-2022 props none | maybe (paid; availability before mid-2023 unverified) | credits; Devin's purchase |
| Historical spreads/totals | 2022-2024 | about 1,300 games a season | closed question | n/a | n/a |
| Live prop lines | 2026 (market_asof.duckdb, about 4.96 GB) | 868,680 quotes in the 7-day export | key outage 11-14 to 11-16; Pinnacle t60 4 of 56 games | yes (new key 11-16, unconfirmed; schedule check) | 0.25 archivist day |

## Gate (umbrella section 6)
Projected live n: 797 logged T-15 games x 5.5-13.7 pts line legs a game = **4,380-10,920**. h = 1.96 x 0.521 / sqrt(n) = **0.0154-0.0098**
(k = 0.521 from FORWARD_T15's pts SE 0.0064 at 60 dates x 130 pairs x 0.85; line rows are higher-minute players, so this is a lower
bound on h). Select-stage h at 17,993 legs about 0.0076. **Result: CONDITIONAL**, decided mechanically by the select stage.

## Playbook check
Class: gradient-boosted residual (context residual) with a market input; nearest classes "trees" and "quantile layers". (1) Information:
yes, but it is the market's information, so it is a prior, not skill. (2) Data: trees on 2023 lines (about 17,000 legs a stat); enough
for a residual. (3) Validity: received_at before made_at; the same rows, grid and DNP handling; scored only on line rows, which are a
selected population (players the book prices), stated. (4) Lineage: GMC (closed), H2, MLC, MARKET_SHRINK, T517, T502; guards carried:
placebo, within-0.001-of-placebo reading, the at-the-line market comparison. (5) Scores against production (CRPS) and the market (at the
line); expected to beat the first and not the second.

## Verdict
**PURSUE (select stage first), with the honest caveat:** a pass makes the distribution better for display, alt rungs and combos; it
cannot be an edge at the line it reads. Prior: P(select clears the live gate) 0.55; P(live pass) 0.35.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 (backtest, selection): **possible now, no new data.** Fit on 2023 lines walk-forward, select on 2024 at T-60 (Pinnacle, then
consensus), from the already-bought the-odds-api history; the stored t60 snapshots are a true T-60 (lead 64.4 min median, T502). Run: about
30-60 min of LightGBM refits on the Mac outside game windows under `heavy.lock`; not Colab. Time to first answer: about 3 working days
after the freeze. Stage 2 (claim): the designed confirmation is ONE logged 2025 touch (2025 props are held in `odds_history.duckdb`; the
modeler never reads them; Devin decides; the archivist logs the row first). Draft row: "INFO PROP_LINE_INPUT, frozen <sha8>: pts integer
CRPS on 2025 Pinnacle/consensus t60 line rows, arm minus production; first and only touch for this never-evaluated model." The forward
look at T-15 on 2026 rows is about serving (the line received before the cutoff) and is descriptive unless the member rule names it
primary. Gate: 2025 touch about 13,000-18,000 legs, h about 0.008-0.009: freezable iff the 2024 effect <= about -0.009.
