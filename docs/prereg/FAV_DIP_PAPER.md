# FAV_DIP_PAPER — buy the pre-game favourite's first in-game dip and hold (paper only)

Status: DRAFT (freeze pending adversary pre-freeze review; must freeze before 21:00Z 2026-10-22). Forward only, paper only (non-negotiable 5:
no orders, no credentials, no execution code). Drafted 2026-10-22 by the main session at Devin's request.
Found on the 2026-10-21 exploration night (agent/market-swing-replay, 13d9074; 4 of 6, +$23 per $10 units,
picked after the fact from ~40 tries), so 2026-10-21 is EXCLUDED from scoring.

## Rule
- **Favourite**: in each game, the team with the higher Kalshi `KXNBAGAME` mid in the last capture snapshot
  strictly before tip (the pre-tip price p0). Skip the game if that mid is < 0.52 (no favourite) or the
  snapshot is missing.
- **Trigger**: the first in-game snapshot (at or after tip) where the favourite's mid <= p0 - 0.10, the game is
  in period 1-3 by the ESPN scoreboard of the same or an earlier snapshot no older than 180 s, the favourite's
  ask < 1.00, and ask - bid <= 0.05. If no fresh ESPN period exists, the snapshot counts as period 1-3 only
  when it is less than 100 minutes after tip.
- **Entry**: buy $10 of the favourite at the trigger snapshot's ask: contracts = floor(10 / ask); taker fee from
  `configs/parlay.yaml` (rounded up to the cent per order). One entry per game.
- **Exit**: hold to settlement ($1 per contract if the favourite wins, else $0). No other exit.
- **Return** per trade = payout - cost - fee, in dollars per $10 stake.

## Primary test
Mean return per trade > 0 with the 95% date-clustered bootstrap CI (10,000 resamples, seed 20261022) lower bound
> 0, over every trade in the window. PASS = that. HARM = CI wholly below 0. Else NULL.

## Comparison (reported, not decisive)
Buying the same favourite at the pre-tip ask p0 and holding, on the same games: the difference per game,
with a CI. If FAV_DIP passes but does not beat this, the report says the gain is the favourite, not the dip.

## Horizon (fixed)
Scored once, on the first date by which the window holds >= 100 trades AND >= 14 distinct dates, starting
2026-10-22. Paper only throughout; going live would need a separate frozen rule and Devin's decision.

## Minimum-data gate (non-negotiable 8), checked 2026-10-22 on the 2026-10-21 capture, label-free
298 `KXNBAGAME` snapshots (median gap 1.1 min) from 18:56Z to 06:59Z, covering every tip; 216 ESPN scoreboard
snapshots, but only from 01:00Z (the early games had no period data, hence the 100-minute fallback). The trigger
fired in 6 of 11 games on the exploration night (~0.5 per game), so 100 trades take ~25-30 slate nights (late
November). PASS on capture coverage; the horizon is long by design.

## Known limits
- Fills at the quoted ask; book depth is captured from 2026-10-22 and reported (size at the ask), not used.
- A person is slower than the capture; the minute cadence flatters the entry slightly.
- The harness is written after this freeze and checked against this text by the adversary before scoring.
