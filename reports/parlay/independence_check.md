# Cross-game independence check (historical OOF)

## Season 2023

Correlation of DIFFERENT games' residuals on the SAME date; 95% CI = date-cluster bootstrap. Flag = CI excludes 0 and |r| above the configured floor.

Props (game-mean normal-score residual per stat):
- pts: +0.0200 [-0.0068, +0.0472] (games=1318, dates=187, pairs=4957)
- reb: +0.0147 [-0.0146, +0.0493] (games=1318, dates=187, pairs=4957)
- ast: -0.0060 [-0.0334, +0.0242] (games=1318, dates=187, pairs=4957)
- fg3m: +0.0014 [-0.0243, +0.0313] (games=1318, dates=187, pairs=4957)

Game results (home-win residual; std_resid is the primary):
- std_resid: -0.0060 [-0.0331, +0.0257] (games=1318, dates=187, pairs=4957)
- probit_pit: +0.0121 [-0.0158, +0.0427] (games=1318, dates=187, pairs=4957)

Synthetic cross-game parlays (n=4548, dates=187; legs mixed moneyline + prop N+):
- independence-product mean pred 0.0963 vs observed 0.0939; observed - pred -0.00239 [-0.01012, +0.00543]
- log loss 0.2332 [0.2160, 0.2512]; Brier 0.06694 [0.06146, 0.07265]

| legs | n | mean pred | observed |
|---|---|---|---|
| 2 | 1990 | 0.1607 | 0.1563 |
| 3 | 1339 | 0.0637 | 0.0650 |
| 4 | 1219 | 0.0269 | 0.0238 |

| pred bin | n | mean pred | observed |
|---|---|---|---|
| [0.00,0.02) | 1432 | 0.0082 | 0.0105 |
| [0.02,0.05) | 999 | 0.0333 | 0.0330 |
| [0.05,0.10) | 707 | 0.0708 | 0.0481 |
| [0.10,0.20) | 692 | 0.1423 | 0.1199 |
| [0.20,0.40) | 533 | 0.2770 | 0.2964 |
| [0.40,1.00) | 185 | 0.5233 | 0.5622 |
(parlay legs share a date, so the bootstrap resamples dates, not parlays.)

Flagged: none

## Season 2024

Correlation of DIFFERENT games' residuals on the SAME date; 95% CI = date-cluster bootstrap. Flag = CI excludes 0 and |r| above the configured floor.

Props (game-mean normal-score residual per stat):
- pts: +0.0249 [-0.0046, +0.0553] (games=1315, dates=184, pairs=4838)
- reb: -0.0015 [-0.0337, +0.0346] (games=1315, dates=184, pairs=4838)
- ast: +0.0218 [-0.0059, +0.0512] (games=1315, dates=184, pairs=4838)
- fg3m: +0.0147 [-0.0265, +0.0666] (games=1315, dates=184, pairs=4838)

Game results (home-win residual; std_resid is the primary):
- std_resid: -0.0160 [-0.0411, +0.0119] (games=1315, dates=184, pairs=4838)
- probit_pit: -0.0182 [-0.0422, +0.0072] (games=1315, dates=184, pairs=4838)

Synthetic cross-game parlays (n=4493, dates=184; legs mixed moneyline + prop N+):
- independence-product mean pred 0.0980 vs observed 0.1026; observed - pred +0.00465 [-0.00434, +0.01344]
- log loss 0.2507 [0.2322, 0.2699]; Brier 0.07431 [0.06783, 0.08105]

| legs | n | mean pred | observed |
|---|---|---|---|
| 2 | 1925 | 0.1673 | 0.1745 |
| 3 | 1359 | 0.0624 | 0.0692 |
| 4 | 1209 | 0.0275 | 0.0256 |

| pred bin | n | mean pred | observed |
|---|---|---|---|
| [0.00,0.02) | 1462 | 0.0082 | 0.0096 |
| [0.02,0.05) | 882 | 0.0330 | 0.0306 |
| [0.05,0.10) | 750 | 0.0712 | 0.0693 |
| [0.10,0.20) | 697 | 0.1452 | 0.1621 |
| [0.20,0.40) | 502 | 0.2804 | 0.2948 |
| [0.40,1.00) | 200 | 0.5181 | 0.5350 |
(parlay legs share a date, so the bootstrap resamples dates, not parlays.)

Flagged: none

## Season pooled

Correlation of DIFFERENT games' residuals on the SAME date; 95% CI = date-cluster bootstrap. Flag = CI excludes 0 and |r| above the configured floor.

Props (game-mean normal-score residual per stat):
- pts: +0.0215 [+0.0008, +0.0423] (games=2633, dates=371, pairs=9795)
- reb: +0.0100 [-0.0121, +0.0338] (games=2633, dates=371, pairs=9795)
- ast: +0.0021 [-0.0172, +0.0216] (games=2633, dates=371, pairs=9795)
- fg3m: -0.0024 [-0.0258, +0.0250] (games=2633, dates=371, pairs=9795)

Game results (home-win residual; std_resid is the primary):
- std_resid: -0.0109 [-0.0288, +0.0091] (games=2633, dates=371, pairs=9795)
- probit_pit: +0.0034 [-0.0148, +0.0227] (games=2633, dates=371, pairs=9795)

Synthetic cross-game parlays (n=9043, dates=371; legs mixed moneyline + prop N+):
- independence-product mean pred 0.0976 vs observed 0.0993; observed - pred +0.00168 [-0.00407, +0.00720]
- log loss 0.2484 [0.2357, 0.2612]; Brier 0.07281 [0.06852, 0.07704]

| legs | n | mean pred | observed |
|---|---|---|---|
| 2 | 3856 | 0.1629 | 0.1603 |
| 3 | 2742 | 0.0673 | 0.0744 |
| 4 | 2445 | 0.0287 | 0.0311 |

| pred bin | n | mean pred | observed |
|---|---|---|---|
| [0.00,0.02) | 2808 | 0.0082 | 0.0089 |
| [0.02,0.05) | 1944 | 0.0332 | 0.0329 |
| [0.05,0.10) | 1481 | 0.0713 | 0.0749 |
| [0.10,0.20) | 1380 | 0.1432 | 0.1399 |
| [0.20,0.40) | 1034 | 0.2787 | 0.2863 |
| [0.40,1.00) | 396 | 0.5150 | 0.5278 |
(parlay legs share a date, so the bootstrap resamples dates, not parlays.)

Flagged: none
