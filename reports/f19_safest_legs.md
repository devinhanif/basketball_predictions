# F19 descriptive: do the night's safest game-winner legs hit as their price implies?

Descriptive only. No rule, no claim, no holdout touch (2025-26 replay, already-spent season). Read-only.

**Plain answer.** Devin's feeling is the arithmetic, not a flaw in the favourites. On 153 nights with at
least 4 games, the four highest-priced winners all won on 62 nights (40.5%); their prices multiplied to
37.4%. The gap (+3.1 points) is inside noise (CI below). Single safe legs are priced about right, with one
possible exception at p >= 0.90 (see the caveat). After the fee, a rolled 4-leg ticket at the ask lost about
5% of stake on average, CI wide enough to include zero.

## Setup (exact)
- Prices: `data/rehearsal/kalshi_2025.duckdb`, `kalshi_prices` for the `KXNBAGAME` tickers (game winners only; the copy
  has no player-prop markets, hourly candles). Last candle with `ts < real tip-off` (strict, like `nba/markets/asof.py`),
  both team contracts required, age <= 2 h, bid-ask <= 0.30. Implied p = de-vigged mid (`devig_two_way`).
  Favourite side = the side with p >= 0.5. 1312 of 1316 games matched a market (4 had no ticker match).
- Tip-off: `max(tipoff)` per game from `replay2025.duckdb` `forward_predictions` (the replay's real tips).
- Results: `games.home_pts > away_pts`. Our model: `forward_scores_elig.pred` for `rung0_injury_elo`
  (fallback `rung0_mov_elo` when absent), P(home win).
- Night = `games.game_date`. Rank that night's games by favourite probability, take the top N (nights with
  fewer than N games dropped). Includes playoffs (through 2026-06-13).
- CI: `nba.eval.metrics.bootstrap_ci`, 4000 resamples, on one number per night (so the clusters are dates);
  leg calibration uses a night-level resample of (hits - mean p). 
- Fee: `configs/parlay.yaml`, taker 0.07 * P * (1-P) per contract, multiplier 1.0 (no KXNBAGAME override). Ticket ROI
  models a "rolled" ticket: each leg bought at its ask plus fee, proceeds rolled into the next leg; pays
  prod(1/(ask+fee)) if all legs win, else 0; ROI = payout - 1. Kalshi has no native parlay here, so
  this is the cost of the legs bought one after another, ignoring fills, depth and the per-order rounding to the cent.
- Reproduce: `.venv/bin/python data/scratch/f19_safest_legs.py` (about a minute; prints everything below).

## Numbers
## Market (Kalshi de-vigged mid at last candle before tip)
Games with a usable pre-tip two-sided market: 1312 on 207 nights.

### Market-ranked safest N legs
| N | nights | all-hit rate | mean product of p | gap (hit - product) [95% CI, night-clustered] | mean ticket ROI after fee (ask-based) [CI] |
|---|---|---|---|---|---|
| 2 | 188 | 0.644 (121/188) | 0.652 | -0.009 [-0.074, +0.055] | -0.068 [-0.175, +0.034] |
| 3 | 170 | 0.541 (92/170) | 0.503 | +0.039 [-0.033, +0.110] | -0.008 [-0.157, +0.145] |
| 4 | 153 | 0.405 (62/153) | 0.374 | +0.031 [-0.041, +0.105] | -0.053 [-0.251, +0.148] |
| 5 | 140 | 0.286 (40/140) | 0.268 | +0.018 [-0.048, +0.085] | -0.150 [-0.390, +0.107] |

- Market, p >= 0.70: n legs 589 on 173 nights; hit 0.817 (481/589); mean implied 0.811; mean ask 0.817; gap +0.006 [-0.025, +0.036]
- Market, p >= 0.80: n legs 307 on 142 nights; hit 0.857 (263/307); mean implied 0.865; mean ask 0.872; gap -0.008 [-0.049, +0.029]
- Market, p >= 0.85: n legs 180 on 94 nights; hit 0.917 (165/180); mean implied 0.893; mean ask 0.899; gap +0.023 [-0.020, +0.061]
- Market, p >= 0.90: n legs 66 on 42 nights; hit 0.985 (65/66); mean implied 0.927; mean ask 0.933; gap +0.058 [+0.024, +0.076]

## Our model (rung0_injury_elo, mov_elo fallback), same games
Games: 1312 on 207 nights.

### Our-ranked safest N legs, our probabilities
| N | nights | all-hit rate | mean product of p | gap (hit - product) [95% CI, night-clustered] | mean ticket ROI after fee (ask-based) [CI] |
|---|---|---|---|---|---|
| 2 | 188 | 0.628 (118/188) | 0.633 | -0.006 [-0.074, +0.059] | -0.063 [-0.177, +0.049] |
| 3 | 170 | 0.471 (80/170) | 0.481 | -0.010 [-0.079, +0.064] | -0.123 [-0.273, +0.033] |
| 4 | 153 | 0.333 (51/153) | 0.354 | -0.020 [-0.088, +0.051] | -0.168 [-0.367, +0.048] |
| 5 | 140 | 0.250 (35/140) | 0.249 | +0.001 [-0.062, +0.068] | -0.157 [-0.424, +0.149] |

### Market-ranked, same game set (check)
| N | nights | all-hit rate | mean product of p | gap (hit - product) [95% CI, night-clustered] | mean ticket ROI after fee (ask-based) [CI] |
|---|---|---|---|---|---|
| 4 | 153 | 0.405 (62/153) | 0.374 | +0.031 [-0.041, +0.105] | -0.053 [-0.251, +0.148] |

- Ours, p >= 0.70: n legs 557 on 174 nights; hit 0.790 (440/557); mean implied 0.801; mean ask 0.806; gap -0.011 [-0.043, +0.019]
- Ours, p >= 0.80: n legs 263 on 125 nights; hit 0.867 (228/263); mean implied 0.861; mean ask 0.864; gap +0.006 [-0.034, +0.043]

## Examples, market top-4 nights
4 givens all hit on 62 of 153 nights.
- 2026-04-12: ALL 4 HIT (product 0.75): TOR 0.98 in BKN@TOR (101-136) W; POR 0.94 in SAC@POR (110-122) W; PHI 0.92 in MIL@PHI (106-126) W; DET 0.89 in DET@IND (133-121) W
- 2026-03-27: ALL 4 HIT (product 0.75): LAL 0.94 in BKN@LAL (99-116) W; DEN 0.94 in UTA@DEN (129-135) W; OKC 0.94 in CHI@OKC (113-131) W; HOU 0.89 in HOU@MEM (119-109) W
- 2026-04-03: ALL 4 HIT (product 0.74): BOS 0.94 in BOS@MIL (133-101) W; HOU 0.94 in UTA@HOU (106-140) W; ATL 0.93 in ATL@BKN (141-107) W; CHA 0.92 in IND@CHA (108-129) W
- 2026-03-21: MISSED (product 0.70): OKC 0.97 in OKC@WAS (132-111) W; SAS 0.94 in IND@SAS (119-134) W; CHA 0.94 in MEM@CHA (101-124) W; PHX 0.83 in MIL@PHX (108-105) L
- 2025-11-21: MISSED (product 0.63): OKC 0.90 in OKC@UTA (144-112) W; BOS 0.89 in BKN@BOS (113-105) L; CLE 0.89 in IND@CLE (109-120) W; TOR 0.89 in WAS@TOR (110-140) W
- 2026-03-18: MISSED (product 0.63): OKC 0.95 in OKC@BKN (121-92) W; DEN 0.88 in DEN@MEM (118-125) L; MIN 0.88 in UTA@MIN (111-147) W; POR 0.87 in POR@IND (127-119) W
- 2025-11-17: MISSED (product 0.58): OKC 0.94 in OKC@NOP (126-109) W; MIN 0.88 in DAL@MIN (96-120) W; DEN 0.87 in CHI@DEN (130-127) L; DET 0.82 in IND@DET (112-127) W
Miss nights: 91; most common number of losing legs on those: [0, 63, 22, 5, 1] (index = losers)

## Reading it
- All-hit rate vs the product of prices: gaps at N=2..5 are -0.9, +3.9, +3.1, +1.8 points, every CI spans zero
  (about +-7 points). The market's favourites hit about as often as priced; no evidence either way for
  favourite-longshot bias at ticket level.
- The model's own top legs hit 33.3% for N=4 vs 35.4% predicted (CI spans zero); the market's top-4 beat ours on
  the same 153 nights (40.5% vs 33.3% hit) but these are different legs and the hit-rate difference is not
  tested here. The two rankings overlap partly; not decomposed.
- Single legs: hit rate vs implied is within noise for p >= 0.70, 0.80, 0.85. The p >= 0.90 bucket hit 65 of 66
  (98.5%) vs 92.7% implied, CI [+2.4, +7.6] points excludes zero. Caveat: 66 legs, the 4th of four thresholds looked at,
  no multiplicity correction, and the threshold was not pre-declared. Treat as a curiosity to replicate on 2026-27, not a finding. Also some 0.98 prices
  are thin late-candle quotes (e.g. TOR 0.98 vs BKN on 2026-04-12), so the top bucket may be partly stale quotes.
- Reading of "never 4 givens": at the top-4 prices, a clean sweep happened 62 of 153 nights, i.e. more often
  than not it does NOT happen (60%). 91 miss nights: 63 had exactly one loser, 22 two, 5 three, 1 four.

## Examples (4 highest-priced winners, 62 of 153 nights swept)
Hits (the three highest-priced sweeps; chosen as most vivid, not random) and misses (the four highest-priced
failed nights) are in the "Examples" block above. Short version: 2026-04-12 (TOR, POR, PHI, DET all won,
product 0.75) and 2026-04-03 (BOS, HOU, ATL, CHA, product 0.74) swept; 2026-03-21 had OKC, SAS and CHA all
win comfortably at 0.94-0.97 but PHX (0.83) lost 108-105 to MIL at home, so the ticket died on a 3-point
game. Typical failure: one 87-89% leg loses (BOS over BKN 2025-11-21, DEN over MEM 2026-03-18, DEN over CHI 2025-11-17 by 3).

## Honest catches
- 207 nights, one season, ~1 ticket per night; CIs on ticket ROI are +-20 points.
- Examples are named by date and matchup so they can be checked in `games`; scores shown are away-home.
- Playoff nights have few games, so they enter only the N=2 rows.
- Midpoint devig of a quote that is the last hourly candle before tip, which can be up to 2 h old.
- Fee and ROI are an approximation of a real ticket (no depth, no cent rounding).
