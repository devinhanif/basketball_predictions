# Do Kalshi prices have bullish / bearish runs? Scratch exploration (2026-10-23)

Status: exploration, not a claim. No rule was frozen before looking; nothing here is a ledger row. Two nights is tiny.
Question (Devin): momentum or mean reversion in Kalshi prices?

## Data and definitions (chosen before the numbers were read)
- Byte copies of `data/raw/live_2026-10-21/kalshi` (120 s ticks) and `live_2026-10-22/kalshi` (60 s ticks). Games dated 10-21 come
  from the first capture, 10-22 from the second. Series: GAME, PTS, REB, AST, 3PT, SPREAD, TOTAL (KXNBATOTAL, not team totals).
  **13 games (11 + 2), 3,514 markets** (3PT 745, REB 688, PTS 645, AST 554, SPREAD 458, TOTAL 398, GAME 26). Season 2025 not touched;
  nothing from ET 10-25 onward read.
- Mid = (yes_bid + yes_ask)/2, only when the book is two-sided with spread <= 10c. "Last" = `last_price_dollars` (last trade).
- Horizons: 1 / 5 / 15 min = 1 tick / 5 min / 15 min of ticks. 10-22 is exact; 10-21 has 120 s ticks so it uses 2 / 6 / 16 min.
- Autocorrelation = correlation of consecutive non-overlapping h-changes (the h-change ending at t versus the h-change after t).
  Pre-tip = before scheduled tip. In-game = first in-progress to first final on the scoreboard. Intervals straddling a phase are dropped.
- Variance ratio VR(h) = Var(h-change) / (k * Var(1-tick change)), mean-zero second moments, overlapping changes.
- Every CI is a 95% percentile bootstrap over the 13 games (game-clustered). With 13 clusters these are rough.
- Large-move test: a >5c move in mid over the last 5 min; enter at that tick, exit 15 min later; non-overlapping per market.
  Continuation = buy the side that moved (at the ask), fade = buy the other side; exit at the bid on that side. Spread is therefore
  paid twice. Fee = 0.07 * P * (1-P) (`configs/parlay.yaml` taker coefficient 0.07; unrounded), shown at entry only and round trip.

## Results
| | lag 1 | lag 5 | lag 15 |
|---|---|---|---|
| Pre-tip mid autocorr (n pairs 225k / 201k / 165k) | -0.29 [-0.32,-0.25] | -0.31 [-0.35,-0.26] | -0.29 [-0.32,-0.24] |
| In-game mid autocorr (126k / 108k / 84k) | -0.03 [-0.08,0.02] | +0.05 [0.00,0.08] | +0.13 [0.05,0.17] |
| Pre-tip last-trade autocorr | -0.06 [-0.08,-0.05] | -0.15 [-0.24,-0.07] | -0.18 [-0.21,-0.12] |
| In-game last-trade autocorr | -0.06 [-0.08,-0.04] | -0.05 [-0.07,-0.04] | -0.02 [-0.08,0.02] |

Variance ratio (mid): pre-tip 0.53 [0.46,0.61] at 5 min and 0.31 [0.24,0.39] at 15 min; in-game 1.06 [1.01,1.14] and 1.23 [1.11,1.39].

By series, in-game lag-15 mid autocorr: SPREAD +0.21 [0.10,0.28], GAME +0.23 [0.11,0.32] (n 1,250 pairs), TOTAL +0.22 [-0.06,0.41], props
(PTS/REB/AST/3PT) +0.01 to +0.06, all CIs include 0. In-game VR(15): 3PT 1.66, REB 1.42, PTS 1.39, AST 1.37, SPREAD 1.02, TOTAL 1.05
(wide CIs). Pre-tip autocorr is -0.26 to -0.33 for props at every lag, -0.05 to -0.11 for SPREAD/TOTAL, about 0 for GAME. By band,
pre-tip is -0.18 to -0.35 everywhere; in-game lag-15 is +0.09 to +0.17 in the 10-90c bands and +0.04 in the >90 band.

Large moves (>5c), cents per contract, game-clustered CI:
| Cell | n events / games | gross continuation | continuation net (spread + entry fee) | fade net (spread + entry fee) |
|---|---|---|---|---|
| Pre-tip, all | 42 / 10 | -1.6 [-2.5,-0.8] | -7.2 [-8.6,-5.9] | -4.0 [-4.6,-3.4] |
| In-game, all | 3,979 / 13 | +0.4 [-0.9,1.4] | -4.0 [-5.2,-3.1] | -4.8 [-5.8,-3.4] |
| In-game GAME | 102 / 12 | +3.1 [0.3,5.7] | +0.8 [-2.0,3.4] | -5.5 [-8.0,-2.8] |
| In-game SPREAD | 1,144 / 13 | +2.4 [-0.1,4.8] | -1.3 [-3.9,1.1] | -6.1 [-8.5,-3.8] |
| In-game PTS / REB / AST / 3PT / TOTAL | 787 / 264 / 232 / 313 / 1,137 | -1.4 to +0.5 | -5.1 to -5.6 | -2.2 to -6.5 |

Round-trip fees lower every continuation net by a further ~1.1c (in-game all: -5.1c; GAME: -0.5c [-3.3,2.3]; SPREAD: -2.4c [-5.0,0.0]).
The surfer paper (MARKET_SURFER_PAPER, DECISIONS 2026-10-23) measured -4.2c per trade [-6.5,-2.7], n 1,378 trades, 13 games; this
independent cut lands in the same place (-4.0c to -5.1c for in-game continuation).

## Reading
1. Pre-tip negative autocorrelation (-0.29) is flat across lags 1, 5 and 15 min. That is the signature of measurement noise
   (quote flicker in thin books: the mid jumps around a stable fair value), not reversion of a real move; true mean reversion would
   fade with lag. Last-trade prices show a much weaker version (-0.06 to -0.18), so bid-ask bounce on the mid is not the whole story,
   but the quote noise is real and untradeable: trading a pre-tip >5c "reversion" costs -4.0c net (n=42, too few to claim anything).
2. In-game there is mild momentum at 15 min (+0.13, VR 1.23), concentrated in game-level markets (SPREAD, GAME, TOTAL). That is
   what a market tracking a running score should do (a lead is persistent), so it is information arriving over time, not a lag we can
   see being exploited. Last-trade prices do not show it (-0.02), so it may partly be a stale-quote effect in the mid.
3. Gross continuation after a >5c move is about zero overall and +2 to +3c in GAME/SPREAD; the round-trip cost is ~3.2c of spread plus
   ~2.3c of fees (1.2c at entry, 1.1c at exit). Nothing clears cost. `no_positive_ev_found`.

## Caveats
Two nights, 13 games (two at 60 s, eleven at 120 s); bootstrap with 13 clusters understates tail uncertainty; events inside a game
share causes (one shot moves many markets); in-game price changes carry score information, so "momentum" here is not a clean
sentiment effect; thin books mean the 10c spread cap selects liquid moments; ESPN/scoreboard times set the phases (start-to-final
includes breaks). Tip-to-start minutes are excluded. The first-in-progress and first-final stamps are poll-quantized (60 to 120 s).

## Worth a frozen forward rule?
No. No cell clears spread plus fee with its CI above zero; the best (GAME, +0.8c entry-fee-only net, CI [-2.0,3.4], n=102) fails
once the exit fee is counted. A rule would be justified only if a pre-declared cell shows a gross continuation above ~5.5c
(spread ~3.2c + round-trip fee ~2.3c) over at least 20 games; no rule sketch drafted. If the question returns, the cheaper
instrument is the existing capture plus the frozen forward logs, with n and a game-clustered CI, not a new trading rule.
