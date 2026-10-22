# Swing replay, 2026-10-21 moneylines (EXPLORATORY, hindsight, not a claim)

Prior stated BEFORE computing the grid: P(any rule beats hold-to-settle out of sample) = 0.15.

## Caveats (all apply)
- One night, 11 games (n=11 clusters); ticks about every 2.3 min; no order books on 10-21, so fills are the listing bid/ask and real depth is unknown; buying at the ask and selling at the bid, taker fee (configs/parlay.yaml) on entry AND exit; never trade at ask 1.00 or bid 0.
- The ESPN capture began at 01:00Z, so the first part of the early games has prices but no state: those ticks count for price-only rules and the ceilings, not for rules needing our prob.
- 37 rules tried (7 entries x 5 exits + 2 baselines): the best cell is biased upward because it was picked after the fact.
- The capture lag means a real person would be slower. 10-21 is now the exploration night and may not be used to score a frozen swing rule. Ceilings are UNATTAINABLE (hindsight).
- Spreads and player props not done (time); only KXNBAGAME moneylines, both sides.

## 1. Hindsight ceilings per game, $ per $10 stake (UNATTAINABLE)

| game | winner | best single round trip | best path (DP, spread+fees) | DP spread only | DP frictionless mid |
|---|---|---|---|---|---|
| ATL@ORL | ATL | 33.02 (ATL 01:21:06Z @0.22 -> settlement @1.00) | 76.91 | 95.53 | 107.87 |
| MIL@WAS | WAS | 18.04 (WAS 01:05:45Z @0.34 -> settlement @1.00) | 49.57 | 66.93 | 75.08 |
| MIN@MIA | MIN | 11.18 (MIN 00:20:37Z @0.45 -> settlement @1.00) | 24.20 | 29.74 | 34.85 |
| CHA@BKN | CHA | 12.38 (CHA 23:51:38Z @0.42 -> settlement @1.00) | 42.97 | 57.36 | 65.51 |
| CHI@TOR | TOR | 2.26 (TOR 23:29:33Z @0.80 -> settlement @1.00) | 4.74 | 8.20 | 18.00 |
| UTA@MEM | MEM | 14.24 (MEM 00:40:38Z @0.39 -> settlement @1.00) | 21.76 | 27.30 | 42.23 |
| IND@NOP | IND | 29.14 (IND 02:02:54Z @0.24 -> settlement @1.00) | 56.18 | 69.58 | 75.17 |
| DAL@HOU | HOU | 3.33 (HOU 00:45:09Z @0.73 -> settlement @1.00) | 7.79 | 12.53 | 15.26 |
| GSW@LAL | LAL | 5.45 (LAL 02:37:55Z @0.62 -> settlement @1.00) | 7.99 | 12.42 | 14.88 |
| PHX@POR | POR | 5.60 (POR 01:52:05Z @0.61 -> settlement @1.00) | 5.96 | 7.54 | 36.28 |
| SAC@LAC | LAC | 10.60 (SAC 03:16:59Z @0.13 -> 04:01:15Z @0.30) | 20.55 | 28.71 | 36.82 |
| **total (11 games, $110 staked)** | | **145.24** | **318.61** | 415.85 | 521.96 |

What spread and fees ate (DP paths): frictionless 521.96 -> spread only 415.85 (spread ate 106.11) -> with fees 318.61 (fees ate 97.24). DP = non-overlapping round trips, each a fresh $10 (profits not reinvested, depth ignored), fractional contracts, unrounded fees; the frictionless line is a bound, not a price anyone can get.

Per-trip decomposition (single best trips): gross at mid, spread cost, fees:
- ATL@ORL: gross 33.76, spread 0.21, fees 0.52, net 33.02
- MIL@WAS: gross 18.62, spread 0.14, fees 0.44, net 18.04
- MIN@MIA: gross 11.65, spread 0.10, fees 0.37, net 11.18
- CHA@BKN: gross 12.87, spread 0.11, fees 0.38, net 12.38
- CHI@TOR: gross 2.46, spread 0.06, fees 0.14, net 2.26
- UTA@MEM: gross 14.76, spread 0.12, fees 0.40, net 14.24
- IND@NOP: gross 29.84, spread 0.20, fees 0.50, net 29.14
- DAL@HOU: gross 3.58, spread 0.07, fees 0.18, net 3.33
- GSW@LAL: gross 5.78, spread 0.07, fees 0.25, net 5.45
- PHX@POR: gross 5.92, spread 0.07, fees 0.25, net 5.60
- SAC@LAC: gross 12.96, spread 0.72, fees 1.64, net 10.60

## 2. Rule grid, mean P&L per game per $10 (games with no signal count 0)

| rule | mean $/game | 95% CI (game bootstrap, n=11) | total $ | trades | hit rate |
|---|---|---|---|---|---|
| baseline_fav_hold | 2.49 | [-1.41, 5.54] | 27.35 | 11 | 0.818 |
| watch65_then_momentum5c5m|eoq3 | 1.12 | [0.572, 1.706] | 12.31 | 8 | 1.0 |
| watch65_then_momentum5c5m|trail70 | 0.30 | [-0.295, 0.973] | 3.28 | 8 | 0.625 |
| watch65_then_momentum5c5m|trail60 | 0.23 | [-1.425, 1.676] | 2.50 | 8 | 0.75 |
| momentum_x10c_n5m|hold | 0.12 | [-3.914, 3.827] | 1.38 | 9 | 0.667 |
| baseline_ride98_hold | 0.04 | [0.008, 0.082] | 0.45 | 4 | 1.0 |
| momentum_x10c_n10m|trail70 | -0.28 | [-2.525, 1.383] | -3.06 | 9 | 0.444 |
| watch65_then_momentum5c5m|hold | -0.70 | [-3.666, 1.663] | -7.67 | 8 | 0.75 |
| momentum_x5c_n5m|eoq3 | -0.77 | [-4.169, 2.202] | -8.51 | 11 | 0.636 |
| momentum_x10c_n10m|hold | -0.85 | [-4.37, 2.239] | -9.32 | 9 | 0.667 |
| watch65_then_momentum5c5m|fair | -1.04 | [-3.061, 0.438] | -11.43 | 8 | 0.25 |
| reversion_g5c|trail60 | -1.04 | [-4.581, 3.611] | -11.44 | 9 | 0.333 |
| momentum_x10c_n5m|trail70 | -1.11 | [-2.954, 0.021] | -12.27 | 9 | 0.333 |
| momentum_x10c_n10m|eoq3 | -1.24 | [-3.835, 1.176] | -13.64 | 9 | 0.556 |
| momentum_x5c_n5m|trail70 | -1.31 | [-3.081, -0.266] | -14.39 | 11 | 0.182 |
| momentum_x10c_n5m|fair | -1.31 | [-2.726, 0.127] | -14.39 | 9 | 0.333 |
| momentum_x10c_n5m|trail60 | -1.32 | [-3.417, 0.876] | -14.53 | 9 | 0.333 |
| reversion_g10c|hold | -1.39 | [-5.726, 3.625] | -15.27 | 6 | 0.333 |
| momentum_x10c_n10m|trail60 | -1.47 | [-3.776, 0.836] | -16.14 | 9 | 0.444 |
| momentum_x5c_n5m|trail60 | -1.48 | [-3.958, 1.171] | -16.22 | 11 | 0.455 |
| momentum_x10c_n10m|fair | -1.51 | [-2.97, -0.047] | -16.62 | 9 | 0.111 |
| reversion_g10c|trail70 | -1.80 | [-3.779, -0.315] | -19.85 | 6 | 0.167 |
| momentum_x5c_n10m|eoq3 | -1.92 | [-5.449, 1.359] | -21.06 | 11 | 0.545 |
| reversion_g5c|fair | -1.98 | [-4.686, 0.463] | -21.78 | 9 | 0.333 |
| reversion_g5c|trail70 | -2.04 | [-4.629, 0.25] | -22.38 | 9 | 0.222 |
| reversion_g10c|fair | -2.04 | [-4.51, -0.019] | -22.50 | 6 | 0.333 |
| reversion_g10c|trail60 | -2.17 | [-4.434, 0.117] | -23.85 | 6 | 0.167 |
| reversion_g10c|eoq3 | -2.19 | [-4.796, 0.004] | -24.11 | 6 | 0.333 |
| momentum_x5c_n5m|fair | -2.21 | [-4.809, 0.313] | -24.37 | 11 | 0.455 |
| momentum_x5c_n10m|trail70 | -2.33 | [-4.75, -0.539] | -25.59 | 11 | 0.091 |
| momentum_x5c_n10m|trail60 | -2.38 | [-4.837, 0.345] | -26.15 | 11 | 0.364 |
| reversion_g5c|hold | -2.52 | [-7.361, 3.325] | -27.74 | 9 | 0.333 |
| momentum_x5c_n10m|fair | -2.66 | [-5.196, -0.052] | -29.23 | 11 | 0.364 |
| momentum_x10c_n5m|eoq3 | -2.80 | [-5.696, 0.087] | -30.84 | 9 | 0.333 |
| reversion_g5c|eoq3 | -3.50 | [-6.709, -0.168] | -38.53 | 9 | 0.333 |
| momentum_x5c_n5m|hold | -4.00 | [-7.686, -0.082] | -43.99 | 11 | 0.455 |
| momentum_x5c_n10m|hold | -5.15 | [-8.682, -1.232] | -56.67 | 11 | 0.364 |

### Same entry, exit rule minus hold-to-settle (paired by game, $ per $10)

15 of 28 exit cells beat hold on the mean.

| cell | mean diff | 95% CI |
|---|---|---|
| momentum_x5c_n10m|eoq3 | 3.24 | [0.497, 6.575] |
| momentum_x5c_n5m|eoq3 | 3.23 | [0.474, 6.469] |
| momentum_x5c_n10m|trail70 | 2.83 | [-0.664, 6.269] |
| momentum_x5c_n10m|trail60 | 2.77 | [1.301, 4.111] |
| momentum_x5c_n5m|trail70 | 2.69 | [-0.917, 6.283] |
| momentum_x5c_n5m|trail60 | 2.52 | [1.028, 4.028] |
| momentum_x5c_n10m|fair | 2.50 | [0.167, 5.047] |
| watch65_then_momentum5c5m|eoq3 | 1.82 | [-0.51, 5.042] |
| momentum_x5c_n5m|fair | 1.78 | [-0.686, 4.472] |
| reversion_g5c|trail60 | 1.48 | [-2.878, 4.894] |
| watch65_then_momentum5c5m|trail70 | 0.99 | [-1.065, 3.517] |
| watch65_then_momentum5c5m|trail60 | 0.93 | [0.0, 2.292] |
| momentum_x10c_n10m|trail70 | 0.57 | [-1.838, 3.385] |
| reversion_g5c|fair | 0.54 | [-4.885, 5.603] |
| reversion_g5c|trail70 | 0.49 | [-5.333, 5.46] |
| watch65_then_momentum5c5m|fair | -0.34 | [-2.286, 2.356] |
| momentum_x10c_n10m|eoq3 | -0.39 | [-3.402, 2.518] |
| reversion_g10c|trail70 | -0.42 | [-5.754, 3.777] |
| momentum_x10c_n10m|trail60 | -0.62 | [-3.74, 2.094] |
| reversion_g10c|fair | -0.66 | [-4.758, 2.936] |
| momentum_x10c_n10m|fair | -0.66 | [-3.409, 2.034] |
| reversion_g10c|trail60 | -0.78 | [-5.537, 2.283] |
| reversion_g10c|eoq3 | -0.80 | [-5.768, 3.003] |
| reversion_g5c|eoq3 | -0.98 | [-5.506, 2.943] |
| momentum_x10c_n5m|trail70 | -1.24 | [-4.43, 2.219] |
| momentum_x10c_n5m|fair | -1.43 | [-5.127, 2.365] |
| momentum_x10c_n5m|trail60 | -1.45 | [-4.796, 1.699] |
| momentum_x10c_n5m|eoq3 | -2.93 | [-6.495, 0.104] |

Best cell by mean (picked after the fact, biased upward): baseline_fav_hold.

## 3. Pelicans game (IND @ NOP), winner IND

Ceiling (UNATTAINABLE): best single trip {'pnl': 29.14, 'team': 'IND', 't_in': '02:02:54Z', 'ask_in': 0.24, 't_out': 'settlement', 'px_out': 1.0, 'contracts': 39, 'gross_at_mid': 29.835, 'spread_cost': 0.195, 'fees': 0.5}; DP full {'pnl': 56.1777, 'round_trips': 14}.
Best NOP-only trip: {'pnl': 11.24, 'team': 'NOP', 't_in': '00:40:38Z', 'ask_in': 0.33, 't_out': '02:02:54Z', 'px_out': 0.76, 'contracts': 28, 'gross_at_mid': 12.32, 'spread_cost': 0.28, 'fees': 0.8}.
Best IND-only trip: {'pnl': 29.14, 'team': 'IND', 't_in': '02:02:54Z', 'ask_in': 0.24, 't_out': 'settlement', 'px_out': 1.0, 'contracts': 39, 'gross_at_mid': 29.835, 'spread_cost': 0.195, 'fees': 0.5}.

Annotation (from the price path below): NOP was an underdog early (0.33-0.35 around 00:36-00:43Z), rallied to 0.69-0.70 by 01:13Z (halftime), swung 0.56-0.69 through Q3, hit 0.76 at 02:02Z with a 9-point lead, then collapsed to 0.54 at 02:14Z and lost. The hindsight best trades are buying NOP at the 00:36-00:43Z low and selling near 0.70 at halftime (or at 0.76 early Q4: the best NOP-only trip, +11), and buying IND at 0.24 at 02:02Z and holding to its win. Both needed prior knowledge of which swing was the top; the rules below did not have it. The momentum entries bought NOP into the early rise (or IND into the 00:20Z drop of NOP) and the state-based entries (reversion, watch band) bought NOP at 0.60-0.70 because our score-only model had it at 0.80+; the market's 0.70 was closer. Holding any NOP entry to settlement lost the full stake; end-of-Q3 and trailing exits cut some of that but also sold IND early.
(Ticks between 01:52Z and 02:00Z and after 02:14Z have no state: the capture had gaps.)

What each rule did (team, in, ask, out, bid_out, pnl; t_out None = held to settlement):

- momentum_x5c_n5m|hold: {'team': 'NOP', 't_in': '00:16:38Z', 'ask': 0.51, 't_out': None, 'bid_out': None, 'pnl': -9.5}
- momentum_x5c_n5m|trail70: {'team': 'NOP', 't_in': '00:16:38Z', 'ask': 0.51, 't_out': '00:56:31Z', 'bid_out': 0.53, 'pnl': -0.28}
- momentum_x5c_n5m|trail60: {'team': 'NOP', 't_in': '00:16:38Z', 'ask': 0.51, 't_out': '02:29:35Z', 'bid_out': 0.32, 'pnl': -4.02}
- momentum_x5c_n5m|fair: {'team': 'NOP', 't_in': '00:16:38Z', 'ask': 0.51, 't_out': None, 'bid_out': None, 'pnl': -9.5}
- momentum_x5c_n5m|eoq3: {'team': 'NOP', 't_in': '00:16:38Z', 'ask': 0.51, 't_out': '02:02:54Z', 'bid_out': 0.76, 'pnl': 3.95}
- momentum_x5c_n10m|hold: {'team': 'NOP', 't_in': '00:14:24Z', 'ask': 0.49, 't_out': None, 'bid_out': None, 'pnl': -9.65}
- momentum_x5c_n10m|trail70: {'team': 'NOP', 't_in': '00:14:24Z', 'ask': 0.49, 't_out': '00:18:24Z', 'bid_out': 0.42, 'pnl': -2.0}
- momentum_x5c_n10m|trail60: {'team': 'NOP', 't_in': '00:14:24Z', 'ask': 0.49, 't_out': '02:29:35Z', 'bid_out': 0.32, 'pnl': -3.86}
- momentum_x5c_n10m|fair: {'team': 'NOP', 't_in': '00:14:24Z', 'ask': 0.49, 't_out': None, 'bid_out': None, 'pnl': -9.65}
- momentum_x5c_n10m|eoq3: {'team': 'NOP', 't_in': '00:14:24Z', 'ask': 0.49, 't_out': '02:02:54Z', 'bid_out': 0.76, 'pnl': 4.54}
- momentum_x10c_n5m|hold: {'team': 'IND', 't_in': '00:20:37Z', 'ask': 0.63, 't_out': None, 'bid_out': None, 'pnl': 5.3}
- momentum_x10c_n5m|trail70: {'team': 'IND', 't_in': '00:20:37Z', 'ask': 0.63, 't_out': '00:26:24Z', 'bid_out': 0.65, 'pnl': -0.19}
- momentum_x10c_n5m|trail60: {'team': 'IND', 't_in': '00:20:37Z', 'ask': 0.63, 't_out': '01:08:30Z', 'bid_out': 0.4, 'pnl': -3.96}
- momentum_x10c_n5m|fair: {'team': 'IND', 't_in': '00:20:37Z', 'ask': 0.63, 't_out': '01:03:22Z', 'bid_out': 0.5, 'pnl': -2.47}
- momentum_x10c_n5m|eoq3: {'team': 'IND', 't_in': '00:20:37Z', 'ask': 0.63, 't_out': '02:02:54Z', 'bid_out': 0.23, 'pnl': -6.44}
- momentum_x10c_n10m|hold: {'team': 'IND', 't_in': '00:24:38Z', 'ask': 0.67, 't_out': None, 'bid_out': None, 'pnl': 4.4}
- momentum_x10c_n10m|trail70: {'team': 'IND', 't_in': '00:24:38Z', 'ask': 0.67, 't_out': None, 'bid_out': None, 'pnl': 4.4}
- momentum_x10c_n10m|trail60: {'team': 'IND', 't_in': '00:24:38Z', 'ask': 0.67, 't_out': '02:02:54Z', 'bid_out': 0.23, 'pnl': -6.56}
- momentum_x10c_n10m|fair: {'team': 'IND', 't_in': '00:24:38Z', 'ask': 0.67, 't_out': '01:03:22Z', 'bid_out': 0.5, 'pnl': -2.85}
- momentum_x10c_n10m|eoq3: {'team': 'IND', 't_in': '00:24:38Z', 'ask': 0.67, 't_out': '02:02:54Z', 'bid_out': 0.23, 'pnl': -6.56}
- reversion_g5c|hold: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': None, 'bid_out': None, 'pnl': -9.87}
- reversion_g5c|trail70: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '01:31:21Z', 'bid_out': 0.6, 'pnl': -0.54}
- reversion_g5c|trail60: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '02:29:35Z', 'bid_out': 0.32, 'pnl': -5.0}
- reversion_g5c|fair: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': None, 'bid_out': None, 'pnl': -9.87}
- reversion_g5c|eoq3: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '02:02:54Z', 'bid_out': 0.76, 'pnl': 2.08}
- reversion_g10c|hold: {'team': 'NOP', 't_in': '01:15:58Z', 'ask': 0.7, 't_out': None, 'bid_out': None, 'pnl': -9.3}
- reversion_g10c|trail70: {'team': 'NOP', 't_in': '01:15:58Z', 'ask': 0.7, 't_out': '02:05:25Z', 'bid_out': 0.74, 'pnl': 0.14}
- reversion_g10c|trail60: {'team': 'NOP', 't_in': '01:15:58Z', 'ask': 0.7, 't_out': '02:29:35Z', 'bid_out': 0.32, 'pnl': -5.34}
- reversion_g10c|fair: {'team': 'NOP', 't_in': '01:15:58Z', 'ask': 0.7, 't_out': None, 'bid_out': None, 'pnl': -9.3}
- reversion_g10c|eoq3: {'team': 'NOP', 't_in': '01:15:58Z', 'ask': 0.7, 't_out': '02:02:54Z', 'bid_out': 0.76, 'pnl': 0.41}
- watch65_then_momentum5c5m|hold: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': None, 'bid_out': None, 'pnl': -9.87}
- watch65_then_momentum5c5m|trail70: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '01:31:21Z', 'bid_out': 0.6, 'pnl': -0.54}
- watch65_then_momentum5c5m|trail60: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '02:29:35Z', 'bid_out': 0.32, 'pnl': -5.0}
- watch65_then_momentum5c5m|fair: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': None, 'bid_out': None, 'pnl': -9.87}
- watch65_then_momentum5c5m|eoq3: {'team': 'NOP', 't_in': '01:08:30Z', 'ask': 0.6, 't_out': '02:02:54Z', 'bid_out': 0.76, 'pnl': 2.08}
- baseline_fav_hold: {'team': 'IND', 't_in': '23:58:24Z', 'ask': 0.57, 't_out': None, 'bid_out': None, 'pnl': 7.01}
- baseline_ride98_hold: no signal

Price path (ours/ESPN are P(NOP win)):

| time | score IND-NOP | per | clock | ours | ESPN | NOP bid/ask | IND bid/ask |
|---|---|---|---|---|---|---|---|
| 23:58:24Z |  | None | None | None | None | 0.44/0.45 | 0.56/0.57 |
| 00:00:38Z |  | None | None | None | None | 0.43/0.44 | 0.56/0.57 |
| 00:02:26Z |  | None | None | None | None | 0.43/0.44 | 0.56/0.57 |
| 00:04:37Z |  | None | None | None | None | 0.44/0.45 | 0.54/0.55 |
| 00:06:23Z |  | None | None | None | None | 0.44/0.45 | 0.55/0.56 |
| 00:08:37Z |  | None | None | None | None | 0.44/0.45 | 0.55/0.56 |
| 00:10:23Z |  | None | None | None | None | 0.44/0.45 | 0.55/0.56 |
| 00:12:38Z |  | None | None | None | None | 0.45/0.46 | 0.54/0.55 |
| 00:14:24Z |  | None | None | None | None | 0.48/0.49 | 0.51/0.52 |
| 00:16:38Z |  | None | None | None | None | 0.5/0.51 | 0.49/0.5 |
| 00:18:24Z |  | None | None | None | None | 0.42/0.43 | 0.57/0.58 |
| 00:20:37Z |  | None | None | None | None | 0.37/0.38 | 0.62/0.63 |
| 00:22:24Z |  | None | None | None | None | 0.37/0.38 | 0.62/0.63 |
| 00:24:38Z |  | None | None | None | None | 0.33/0.34 | 0.66/0.67 |
| 00:26:24Z |  | None | None | None | None | 0.34/0.35 | 0.65/0.66 |
| 00:29:31Z |  | None | None | None | None | 0.33/0.34 | 0.66/0.67 |
| 00:31:36Z |  | None | None | None | None | 0.34/0.35 | 0.64/0.65 |
| 00:34:00Z |  | None | None | None | None | 0.34/0.35 | 0.65/0.66 |
| 00:36:06Z |  | None | None | None | None | 0.32/0.33 | 0.67/0.68 |
| 00:38:31Z |  | None | None | None | None | 0.39/0.4 | 0.6/0.61 |
| 00:40:38Z |  | None | None | None | None | 0.32/0.33 | 0.67/0.68 |
| 00:43:03Z |  | None | None | None | None | 0.34/0.35 | 0.65/0.66 |
| 00:45:09Z |  | None | None | None | None | 0.37/0.38 | 0.62/0.63 |
| 00:47:33Z |  | None | None | None | None | 0.46/0.47 | 0.53/0.54 |
| 00:49:38Z |  | None | None | None | None | 0.53/0.54 | 0.46/0.47 |
| 00:52:02Z |  | None | None | None | None | 0.55/0.56 | 0.44/0.45 |
| 00:54:07Z |  | None | None | None | None | 0.55/0.56 | 0.44/0.45 |
| 00:56:31Z |  | None | None | None | None | 0.53/0.54 | 0.46/0.47 |
| 00:58:53Z |  | None | None | None | None | 0.47/0.48 | 0.52/0.53 |
| 01:03:22Z | 48-48 | 2 | 271.0 | 0.543 | 0.432 | 0.49/0.5 | 0.5/0.51 |
| 01:05:45Z | 50-51 | 2 | 190.0 | 0.571 | 0.464 | 0.56/0.57 | 0.43/0.44 |
| 01:08:30Z | 50-55 | 2 | 166.0 | 0.68 | 0.566 | 0.59/0.6 | 0.4/0.41 |
| 01:10:53Z | 50-55 | 2 | 92.0 | 0.684 | 0.607 | 0.61/0.62 | 0.38/0.39 |
| 01:13:36Z | 52-61 | 2 | 57.0 | 0.78 | 0.64 | 0.69/0.7 | 0.3/0.31 |
| 01:15:58Z | 56-66 | 2 | 0.0 | 0.805 | 0.74 | 0.69/0.7 | 0.3/0.31 |
| 01:18:43Z | 56-66 | 2 | 0.0 | 0.805 | 0.757 | 0.69/0.7 | 0.3/0.31 |
| 01:21:06Z | 56-66 | 2 | 0.0 | 0.805 | 0.757 | 0.69/0.7 | 0.3/0.31 |
| 01:23:51Z | 56-66 | 2 | 0.0 | 0.805 | 0.757 | 0.69/0.7 | 0.3/0.31 |
| 01:26:14Z | 56-66 | 2 | 0.0 | 0.805 | 0.757 | 0.7/0.71 | 0.29/0.3 |
| 01:28:58Z | 56-66 | 2 | 0.0 | 0.805 | 0.757 | 0.68/0.69 | 0.3/0.31 |
| 01:31:21Z | 58-68 | 3 | 668.0 | 0.809 | 0.719 | 0.6/0.61 | 0.39/0.4 |
| 01:34:06Z | 65-70 | 3 | 551.0 | 0.699 | 0.63 | 0.68/0.69 | 0.3/0.31 |
| 01:36:29Z | 69-77 | 3 | 410.0 | 0.79 | 0.753 | 0.69/0.7 | 0.3/0.31 |
| 01:39:14Z | 69-77 | 3 | 410.0 | 0.79 | 0.739 | 0.56/0.58 | 0.42/0.43 |
| 01:41:38Z | 72-77 | 3 | 351.0 | 0.714 | 0.657 | 0.56/0.57 | 0.43/0.44 |
| 01:44:23Z | 74-79 | 3 | 287.0 | 0.72 | 0.62 | 0.67/0.68 | 0.32/0.33 |
| 01:46:50Z | 76-84 | 3 | 196.0 | 0.815 | 0.755 | 0.68/0.69 | 0.31/0.32 |
| 01:49:37Z | 76-84 | 3 | 196.0 | 0.815 | 0.613 | 0.61/0.63 | 0.37/0.38 |
| 01:52:05Z | 81-88 | 3 | 79.0 | 0.804 | 0.779 | 0.55/0.56 | 0.44/0.45 |
| 02:00:00Z |  | None | None | None | None | 0.64/0.65 | 0.35/0.36 |
| 02:02:54Z | 86-95 | 4 | 641.0 | 0.879 | 0.763 | 0.76/0.77 | 0.23/0.24 |
| 02:05:25Z | 88-96 | 4 | 603.0 | 0.863 | 0.827 | 0.74/0.76 | 0.25/0.26 |
| 02:07:33Z | 91-99 | 4 | 545.0 | 0.874 | 0.723 | 0.72/0.73 | 0.27/0.28 |
| 02:09:57Z | 93-101 | 4 | 505.0 | 0.882 | 0.821 | 0.59/0.6 | 0.42/0.44 |
| 02:14:03Z | 98-101 | 4 | 461.0 | 0.711 | 0.596 | 0.54/0.55 | 0.46/0.47 |
| 02:29:35Z |  | None | None | None | None | 0.32/0.34 | 0.66/0.68 |
| 02:37:55Z |  | None | None | None | None | 0.0/0.01 | 0.99/1.0 |
