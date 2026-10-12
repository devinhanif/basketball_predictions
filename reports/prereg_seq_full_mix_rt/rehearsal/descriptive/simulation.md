# SEQ_FULL_MIX user simulation (DESCRIPTIVE)

Personas fixed in the rule before any 2025 number existed. P0 keep your money. P1 flat $20 on every leg where the arm's P(side) minus the de-vigged market P(side) >= 0.03, at the stored vigged T-60 price. P2 the same at >= 0.06. P3 quarter-Kelly on a $500 bankroll, at most $20 per bet, nightly stakes scaled to the bankroll held at the start of the night. P4 one 2-leg same-night parlay per night from the two highest-edge legs on different games, $10, legs assumed independent (the price is the product of the legs' prices; clustered on the first leg's game).

**This is descriptive, not a test of edge. Hit rates and ROI on one season are noisy; no persona is a recommendation.** Seasons marked seen=True (2023, 2024) are already-seen: the construction was chosen on them. One bet per (player, stat, night, book) at the T-60 price; Pinnacle and DK separately. ROI = P&L / staked with a game-clustered 95% CI.

| season | seen | book | arm | persona | bets | staked | P&L | ROI [CI] | max drawdown | longest losing streak | months in profit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | True | pinnacle | A0 | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | pinnacle | A0 | P1 | 26421 | 528420 | -16675.0 | -0.032 [-0.044, -0.020] | 17091.3 | 11 | 0.11 |
| 2023 | True | pinnacle | A0 | P2 | 18308 | 366160 | -8208.5 | -0.022 [-0.037, -0.008] | 8633.3 | 12 | 0.33 |
| 2023 | True | pinnacle | A0 | P3 | 24889 | 20196 | -490.3 | -0.024 [-0.044, -0.005] | 511.4 | 12 | 0.44 |
| 2023 | True | pinnacle | A0 | P4 | 184 | 1840 | -174.9 | -0.095 [-0.312, 0.152] | 490.3 | 21 | 0.25 |
| 2023 | True | pinnacle | FULL | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | pinnacle | FULL | P1 | 26207 | 524140 | -15798.1 | -0.030 [-0.043, -0.018] | 16101.6 | 12 | 0.11 |
| 2023 | True | pinnacle | FULL | P2 | 18252 | 365040 | -6297.3 | -0.017 [-0.032, -0.003] | 7295.6 | 12 | 0.33 |
| 2023 | True | pinnacle | FULL | P3 | 24699 | 21571 | -465.8 | -0.022 [-0.041, -0.001] | 522.8 | 13 | 0.44 |
| 2023 | True | pinnacle | FULL | P4 | 184 | 1840 | -104.8 | -0.057 [-0.290, 0.183] | 376.9 | 15 | 0.38 |
| 2023 | True | pinnacle | AVG | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | pinnacle | AVG | P1 | 25313 | 506260 | -14155.1 | -0.028 [-0.041, -0.016] | 14530.2 | 14 | 0.11 |
| 2023 | True | pinnacle | AVG | P2 | 16632 | 332640 | -5090.5 | -0.015 [-0.030, -0.001] | 5354.6 | 10 | 0.33 |
| 2023 | True | pinnacle | AVG | P3 | 23624 | 33057 | -472.0 | -0.014 [-0.033, 0.005] | 501.2 | 13 | 0.33 |
| 2023 | True | pinnacle | AVG | P4 | 184 | 1840 | -49.5 | -0.027 [-0.262, 0.203] | 311.1 | 15 | 0.38 |
| 2023 | True | draftkings | A0 | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | draftkings | A0 | P1 | 39827 | 796540 | -11647.9 | -0.015 [-0.025, -0.004] | 13149.4 | 11 | 0.11 |
| 2023 | True | draftkings | A0 | P2 | 27518 | 550360 | 1190.8 | 0.002 [-0.011, 0.015] | 2805.2 | 16 | 0.56 |
| 2023 | True | draftkings | A0 | P3 | 39353 | 78820 | -108.4 | -0.001 [-0.016, 0.013] | 558.2 | 11 | 0.44 |
| 2023 | True | draftkings | A0 | P4 | 187 | 1870 | 63.8 | 0.034 [-0.212, 0.296] | 244.1 | 12 | 0.50 |
| 2023 | True | draftkings | FULL | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | draftkings | FULL | P1 | 39603 | 792060 | -9456.8 | -0.012 [-0.022, -0.001] | 10380.3 | 15 | 0.22 |
| 2023 | True | draftkings | FULL | P2 | 27345 | 546900 | -358.3 | -0.001 [-0.014, 0.012] | 3867.7 | 12 | 0.33 |
| 2023 | True | draftkings | FULL | P3 | 39111 | 75939 | 31.9 | 0.000 [-0.015, 0.016] | 503.1 | 14 | 0.44 |
| 2023 | True | draftkings | FULL | P4 | 187 | 1870 | 396.2 | 0.212 [-0.025, 0.469] | 138.0 | 11 | 0.62 |
| 2023 | True | draftkings | AVG | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2023 | True | draftkings | AVG | P1 | 38224 | 764480 | -6184.5 | -0.008 [-0.019, 0.003] | 7343.2 | 12 | 0.44 |
| 2023 | True | draftkings | AVG | P2 | 24920 | 498400 | 2767.2 | 0.006 [-0.008, 0.019] | 3082.0 | 15 | 0.56 |
| 2023 | True | draftkings | AVG | P3 | 37717 | 128969 | 102.6 | 0.001 [-0.014, 0.015] | 916.1 | 11 | 0.67 |
| 2023 | True | draftkings | AVG | P4 | 187 | 1870 | 238.7 | 0.128 [-0.111, 0.382] | 141.8 | 11 | 0.62 |
| 2024 | True | pinnacle | A0 | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | pinnacle | A0 | P1 | 27178 | 543560 | -12928.5 | -0.024 [-0.036, -0.012] | 15004.2 | 12 | 0.22 |
| 2024 | True | pinnacle | A0 | P2 | 17971 | 359420 | -6404.2 | -0.018 [-0.033, -0.002] | 8516.3 | 11 | 0.22 |
| 2024 | True | pinnacle | A0 | P3 | 25494 | 16060 | -431.0 | -0.027 [-0.052, -0.001] | 497.3 | 12 | 0.22 |
| 2024 | True | pinnacle | A0 | P4 | 184 | 1840 | 174.7 | 0.095 [-0.131, 0.343] | 340.4 | 12 | 0.62 |
| 2024 | True | pinnacle | FULL | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | pinnacle | FULL | P1 | 26967 | 539340 | -15641.6 | -0.029 [-0.041, -0.017] | 17469.7 | 12 | 0.22 |
| 2024 | True | pinnacle | FULL | P2 | 17819 | 356380 | -6512.0 | -0.018 [-0.033, -0.003] | 8264.4 | 13 | 0.44 |
| 2024 | True | pinnacle | FULL | P3 | 25343 | 19818 | -378.3 | -0.019 [-0.041, 0.005] | 493.8 | 11 | 0.33 |
| 2024 | True | pinnacle | FULL | P4 | 184 | 1840 | -65.7 | -0.036 [-0.261, 0.219] | 456.1 | 17 | 0.62 |
| 2024 | True | pinnacle | AVG | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | pinnacle | AVG | P1 | 26095 | 521900 | -12686.0 | -0.024 [-0.037, -0.012] | 14622.9 | 11 | 0.22 |
| 2024 | True | pinnacle | AVG | P2 | 16593 | 331860 | -3937.6 | -0.012 [-0.028, 0.004] | 5862.9 | 10 | 0.33 |
| 2024 | True | pinnacle | AVG | P3 | 24454 | 20288 | -376.3 | -0.019 [-0.045, 0.008] | 494.5 | 11 | 0.33 |
| 2024 | True | pinnacle | AVG | P4 | 184 | 1840 | 328.9 | 0.179 [-0.060, 0.432] | 318.2 | 11 | 0.75 |
| 2024 | True | draftkings | A0 | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | draftkings | A0 | P1 | 38471 | 769420 | -15677.1 | -0.020 [-0.031, -0.010] | 18385.0 | 15 | 0.22 |
| 2024 | True | draftkings | A0 | P2 | 25250 | 505000 | -8240.7 | -0.016 [-0.029, -0.004] | 10784.5 | 13 | 0.22 |
| 2024 | True | draftkings | A0 | P3 | 37487 | 29212 | -332.0 | -0.011 [-0.029, 0.005] | 492.7 | 15 | 0.22 |
| 2024 | True | draftkings | A0 | P4 | 183 | 1830 | -11.2 | -0.006 [-0.231, 0.233] | 191.4 | 12 | 0.38 |
| 2024 | True | draftkings | FULL | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | draftkings | FULL | P1 | 38208 | 764160 | -17942.9 | -0.023 [-0.034, -0.013] | 20420.6 | 13 | 0.22 |
| 2024 | True | draftkings | FULL | P2 | 24866 | 497320 | -9407.7 | -0.019 [-0.033, -0.006] | 11622.7 | 13 | 0.22 |
| 2024 | True | draftkings | FULL | P3 | 37240 | 32220 | -275.6 | -0.009 [-0.025, 0.008] | 489.0 | 13 | 0.22 |
| 2024 | True | draftkings | FULL | P4 | 183 | 1830 | 38.9 | 0.021 [-0.208, 0.261] | 212.6 | 10 | 0.38 |
| 2024 | True | draftkings | AVG | P0 | 0 | 0 | 0.0 |  [, ] | 0.0 | 0 |  |
| 2024 | True | draftkings | AVG | P1 | 36944 | 738880 | -13829.1 | -0.019 [-0.030, -0.008] | 16136.9 | 12 | 0.22 |
| 2024 | True | draftkings | AVG | P2 | 23111 | 462220 | -6242.3 | -0.014 [-0.027, -0.000] | 8145.4 | 11 | 0.22 |
| 2024 | True | draftkings | AVG | P3 | 35967 | 47184 | -28.5 | -0.001 [-0.017, 0.015] | 476.7 | 15 | 0.44 |
| 2024 | True | draftkings | AVG | P4 | 183 | 1830 | -60.2 | -0.033 [-0.270, 0.208] | 279.2 | 12 | 0.50 |
