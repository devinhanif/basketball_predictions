# MARKET_SURFER_PAPER (rule version `market-surfer-paper-v1`)

Frozen 2026-10-23, committed BEFORE any captured price series was read by this agent (see "What was looked at" below).
Paper only. Public GET data already captured by the live capture; no credential, no order, nothing that can reach an order
(CLAUDE.md non-negotiable 5). Parameters live in `configs/market_surfer.yaml`; fees live in `configs/parlay.yaml` `fee_model`
(verified object, taker, rounded up once per order; never a number in code). Requested by Devin 2026-10-23 ~20:30 CDT
("market surfer": scan, enter fast, exit at +10% with a stop, many quick round trips). Label: Devin's override of Claude's
advice (FAN_KNOWLEDGE "exit velocity" entry: no exit timing adds EV at fair prices); we test it so the answer is data, not opinion.

## The question and what it protects against

Does a mechanical momentum entry with a +10% take-profit / -15% stop / 20-minute time stop earn positive net P&L per trade on
single Kalshi NBA contracts, buying at the ask and selling at the bid with taker fees both ways? Protects against: a backtest
that fills at the mid, a story told from the round trips that hit, fees ignored on the way out, and comparing against nothing.

## Honest prior (stated before the result)

With fair prices an exit rule adds no expected value (optional stopping on a martingale). Every round trip pays the spread
(about 1-2c each way inside the allowed 2c spread) plus two taker fees (0.07*p*(1-p) per contract, about 1.7c at p=0.5 each,
rounded up per order of 10). The expected net P&L per trade is therefore about -3c to -5c per contract. Claude's P(the rule shows
a positive net mean per trade that survives the claim gate) = 3%. Momentum at 1-minute scale could be mildly positive or
negative from short-horizon continuation or reversal; I have no evidence for either sign, and the placebo and the hold
comparator exist to separate that from fee drag. A clean "no" is the expected result and a fine one.

## Instruments

- Single binary contracts in the 11 captured series that also have settlement results in `data/kalshi/kalshi.duckdb`
  (`kalshi_markets.result`): KXNBAGAME, SPREAD, TOTAL, TEAMTOTAL, PTS, REB, AST, 3PT, STL, BLK, PRA. Chosen on settlement
  coverage, not on price. Quarter, half, OT, margin and *PROT series are out of scope (no settlement table).
- Either side: YES executable at `yes_ask`, exit at `yes_bid`; NO entry at `1 - yes_bid`, exit at `1 - yes_ask`; sizes cross
  (NO ask size = `yes_bid_size_fp`, NO bid size = `yes_ask_size_fp`).
- **Combos are declared untestable.** No cash-out offer or combo quote is captured: neither `data/raw/live_2026-10-21` nor
  `live_2026-10-22` has a combo-offer source (the `nba/live/offer.py` log exists on the paper-trader branch but wrote nothing
  to these directories). A combo that is only an implied product of leg asks and bids is the `synthetic` instrument the
  paper-trader prereg already refuses to score; this test does the same. Revisit when offers are captured.
- Order books (`kalshi_orderbook`, 2026-10-22 only, subset) are NOT used: night 10-21 has none, and one source for both nights
  keeps the nights comparable. Fills are therefore limited to the touch size (>= 25 contracts required vs a 10-contract unit).

## Data and nights

Source: `data/raw/live_2026-10-21/kalshi/<SERIES>_p0/*.json.gz` and `data/raw/live_2026-10-22/kalshi/<SERIES>_p0/*.json.gz`
(market pages: bid, ask, sizes per snapshot); ESPN scoreboard files in the same trees (tip time and game state);
settlement from `kalshi_markets.result` in the live snapshot DB, opened `read_only=True`. A market's night is its event date
(`26OCT21` -> night 2026-10-21, `26OCT22` -> 2026-10-22). Nights from 2026-10-23 on are reserved as the clean forward
replication of this exact frozen rule (not part of this run). The run reads nothing from `nba.duckdb`; no season-2025 data.

## Snapshot unit and latency

A snapshot is one stored page file of a series (cadence 60 s on 10-22; median 68 s but p90 165 s on 10-21). Decide on snapshot
t using only data at or before t; fill on snapshot t+1 at the executable side of t+1's quote. No fill if t+1 is more than 300 s
after t, if the t+1 ask is more than 2c worse than the t ask, or if the t+1 touch size is below 25. Missed fills are counted
and reported, not retried.

## Entry scan (the only entry rule; no variants)

At snapshot t, for each in-scope market and each side s, enter (decision) when ALL hold:
1. In play: t >= tip of the mapped game (ESPN `date`) and the latest scoreboard state at or before t is not `post`.
2. Both bid and ask exist; executable ask in [0.15, 0.85]; spread (ask - bid) <= 0.02; top size at ask and at bid >= 25.
3. Momentum: side mid (mid of yes bid/ask for YES; 1 - that for NO) at t minus side mid 3 snapshots earlier >= +0.03, with the
   3 snapshots spanning <= 400 s.
4. No open position in the market; >= 600 s since this market's last exit.
Mapping market -> game uses the team pair in the event ticker and the scoreboard abbreviation map (GS->GSW, NY->NYK, SA->SAS,
NO->NOP, UTAH->UTA, WSH->WAS). An unmappable event is listed and excluded, never guessed.
Why momentum: the surfer idea is to ride a move. The mirror (fade) is a different question and gets a new rule if wanted.

## Exit rules (evaluated on each snapshot u after the fill, in this order; the exit fills on snapshot u+1 at the bid)

1. Take-profit: executable bid at u >= 1.10 x entry ask (+10% on the executable side, gross of fees).
2. Stop: executable bid at u <= 0.85 x entry ask.
3. Time stop: u is >= 1200 s after the fill.
4. Fallback: if no exit fires before the market stops appearing (settlement), hold to settlement: payout 1 if the side's
   outcome won else 0, no exit fee.
If the exit snapshot (u+1) has no valid bid or the touch size < 25, the exit waits one more snapshot, then falls to settlement.
Positions open at the end of the night's data settle by result. A trade whose market has no `result` is excluded and counted.

## P&L, fees, accounting unit

Per trade, per contract: pnl = exit_proceeds - entry_ask - entry_fee/10 - exit_fee/10, with fees from
`order_fee(price, fee_model, contracts=10, order_type='taker', series=<series>)` (round up once per order of 10 contracts;
the 10 is an accounting unit that fixes the rounding, not a stake or a sizing suggestion). Settlement exits pay no exit fee.
Reported also in dollars per 10-contract unit. A maker variant is declared and NOT run: a resting order cannot be simulated
from snapshots without queue position.

## Metrics, comparators, clustering

- Primary: mean net P&L per contract per trade, and total net P&L per night (10-contract unit), with n = trades.
- Comparator A (paired): the same entries (same fill, same entry fee) held to settlement. Report strategy minus hold per trade.
- Comparator B (placebo, seed 20261023, 500 replications): for each real entry draw a random eligible (market, side, snapshot)
  from the same night and the same series, eligible by conditions 1, 2 and 4 only (no momentum), run through the identical
  fill and exit rules. Report the mean placebo net P&L per trade with the 2.5 and 97.5 percentiles of the 500 replication means
  and the real mean's percentile rank.
- Decomposition: fees paid, spread cost (entry ask - entry mid + exit mid - exit bid), and mid-to-mid drift.
- Also reported: win rate (net > 0), exit-reason counts, median hold time, missed-fill count, trades per night.
- CI: primary clustered by NIGHT (t interval on per-night mean trade P&L, k-1 df; with k < 5 nights this is shown but is not
  a usable interval and says so); secondary clustered by GAME (event pair, bootstrap 10000, seed 20261023, 95% percentile).
  Series within a game and trades in one market co-move, so trades are never treated as independent.

## Verdict rule (fixed now)

- `insufficient_sample`: fewer than 100 trades or fewer than 2 nights -> claim nothing.
- `positive_ev_candidate` (needs a fresh forward test before anything else): nights >= 5, trades >= 300, games >= 20, net mean
  per trade > 0 with BOTH the night-clustered and game-clustered CI lower bounds > 0, AND the real mean above the placebo 97.5
  percentile AND above the hold comparator. Nothing less.
- `negative_ev_supported`: net mean < 0 and game-clustered CI upper bound < 0 with games >= 20 (a claim about this rule, on
  these nights; the night-clustered interval is reported but with < 5 nights does not carry the claim).
- otherwise `no_positive_ev_found`.
With 2 nights the strongest available outcomes are `negative_ev_supported`, `no_positive_ev_found` or `insufficient_sample`.
A positive result on 2 nights is a lead for the 10-23+ replication, never a claim, and never a stake.

## Minimum-data gate (checked before freezing; structure only, no price read)

Run gate: >= 2 complete nights (10-21 and 10-22 are complete; 10-23 is still being captured); median snapshot gap <= 90 s and
p90 <= 180 s (10-22: 60 s / 60 s; 10-21: 68 s / 165 s, so it passes); >= 100 trades after the run (known only after; below it
the verdict is `insufficient_sample`); combos have no captured quotes (declared above).
Claim gate (for a positive claim only): >= 5 nights, >= 300 trades, >= 20 games; it FAILS today on nights (2). That is why the
strongest positive verdict is out of reach on this data and the run is a pilot whose forward replication is nights 10-23 on.
Move horizon: a +10% move on a 0.15-0.85 contract is 1.5-8.5c, one to a few ticks; at a 60-68 s cadence a 20-minute time stop
spans about 18 snapshots, so the cadence resolves the exit horizon. It does NOT show that a fast scalp is fillable: any move
that reverses inside one snapshot is invisible, and the t+1 fill is a stated conservative assumption, not a measured latency.
Gate check results are written into the results note before the run's numbers.

## What was looked at before freezing

File listings and counts, market-page field names, the fee config, and the settlement-table coverage by event date (counts of
non-empty `result`, no values). One market-page record (OKC game winner, one snapshot) was printed while checking the format;
no price series, no outcome, no momentum or exit statistic was computed. 2026-10-23 captures are not in `data/raw` yet and are
untouched.

## Stop conditions and what would reverse this

If the run gate fails the work stops and says so. Nothing here changes after a result: a new question gets a new rule version.
Reversal of "keep your money": only the forward replication on nights >= 5 passing the claim gate, then Devin's decision.
