# FAV_DIP_PAPER — buy the pre-game favourite's first in-game dip and hold (paper only)

Status: FROZEN (this commit; text sha256 in DECISIONS). Forward only, paper only (non-negotiable 5: no orders,
no credentials, no execution code). Drafted 2026-10-22 by the main session at Devin's request; adversary
pre-freeze review docs/reviews/prefreeze_line_move_fav_dip_2026-10-22.md (MUSTs F1-F5 adopted verbatim; SHOULDs
F-S1..F-S4 adopted as reporting). Found on the 2026-10-21 exploration night (agent/market-swing-replay, 13d9074;
4 of 6, picked after the fact from ~40 tries), so 2026-10-21 is EXCLUDED from scoring.

**A NULL here means no edge above ~25% ROI, not no edge** (F-S1: at n = 100 the 80%-power minimum detectable
effect is about +$2.5 per $10).

## Rule
- **Favourite**: each game has two `KXNBAGAME` tickers, one per team. The pre-tip snapshot is the last capture
  snapshot strictly before tipoff (`forward_predictions.tipoff`, read as UTC). It must be no more than 10 minutes
  before tipoff, and both teams' quotes must be two-sided (0 < bid < ask < 1) with the favourite's ask − bid <= 0.05.
  Otherwise the game is skipped and counted. The favourite is the team whose own ticker has the higher mid;
  p0 is that mid. Skip the game if p0 < 0.52 or the mids tie. All later prices are the favourite's own
  ticker's quotes (never 1 − the other team's).
- **Trigger**: the earliest Kalshi snapshot at or after tipoff at which ALL of these hold at once: the favourite's
  mid <= p0 − 0.10; its ask < 1.00; its ask − bid <= 0.05; and the game is in period 1-3. Period = ESPN
  `status.period` for this game (matched by home and away team) from the latest ESPN scoreboard snapshot
  taken at or before the Kalshi snapshot and no more than 180 s before it. Periods 1, 2 (including halftime)
  and 3 qualify; status "pre"/period 0 and period >= 4 do not. Only when no such ESPN snapshot exists, the
  Kalshi snapshot qualifies if it is less than 100 minutes after tipoff. The number of trades entered under
  the fallback is reported.
- **Entry**: buy $10 of the favourite at the trigger snapshot's ask: contracts = floor(10 / ask);
  taker fee = ceil_to_cent(0.07 × contracts × ask × (1 − ask)) once per order (multiplier 1.0 for KXNBAGAME, the
  `configs/parlay.yaml` values at this rule's freeze commit; later config changes do not apply to this rule).
  One entry per game.
- **Exit**: hold to settlement: $1 per contract if the favourite wins by the official final score, else $0.
  A game postponed, or a market voided by Kalshi, is dropped and counted. No trade is dropped for any other
  reason. No other exit.
- **Return** per trade = payout − cost − fee, in dollars per $10 stake.

## Primary test
Mean return per trade > 0 with the lower bound > 0 of the 95% percentile CI from resampling dates with replacement
(10,000 resamples, `numpy.random.default_rng(20261022)`; dates sorted ascending, trades within a date sorted by
game_id before resampling); the statistic on each resample is the pooled mean over every trade in the resampled
dates (each trade weighted equally). PASS = that. HARM = CI wholly below 0. Else NULL.

## Comparison (reported, not decisive)
Buying the favourite at the pre-tip snapshot's ask and holding, on EVERY game with a favourite
(p0 >= 0.52, pre-tip quote valid), with the same $10 stake, contract and fee math: its mean return per trade
with its own date-clustered CI, and the difference (FAV_DIP mean − pre-tip mean) with a CI from resampling
dates jointly. If FAV_DIP passes but its lower bound on the difference is <= 0, the report says the gain is
buying favourites, not the dip. No same-game paired comparison is used, because on triggered games the dip
entry is cheaper by construction.

## Horizon (fixed)
Scored once, on the first date by which the window holds >= 100 trades AND >= 14 distinct dates, starting
2026-10-22. Paper only throughout; going live would need a separate frozen rule and Devin's decision.

## Also reported
Minutes since the previous in-game snapshot at each entry (F-S2); results for p0 in [0.52, 0.60) and >= 0.60
(F-S3); fallback-entry count and whether ESPN was captured every tick (F-S4); size at the ask.

## Minimum-data gate (non-negotiable 8), label-free, on the 2026-10-21 capture
298 `KXNBAGAME` snapshots (median gap 1.1 min) from 18:56Z to 06:59Z covering every tip; ESPN scoreboard only
from 01:00Z (hence the fallback). The trigger fired in about 6 of 11 games on the exploration night, so 100
trades take ~25-30 slate nights (late November). PASS on coverage; the horizon is long by design.

## Known limits
- Fills at the quoted ask; book depth (captured from 2026-10-22) is reported, not used.
- A person is slower than the capture; the minute cadence flatters the entry slightly.
- The harness is written after this freeze and checked against this text by the adversary before scoring.
