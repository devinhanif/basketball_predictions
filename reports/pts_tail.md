# Points upper-tail screen (pts_tail)

Prereg: docs/PTS_TAIL.md (frozen sha256 prefix c4d1161e). Production OOF-equivalent walk-forward, pts, 2023 selection / 2024 report.

| season | arm | dTLL vs production [95% game-clustered CI] | p (BH, 3) | dCRPS [CI] | pooled ECE | P(y<=q0.90/0.95/0.99) | pass |
|---|---|---|---|---|---|---|---|
| 2023 | production | (TLL 0.19960, CRPS 3.1571) | | | 0.00412 | 0.904 / 0.954 / 0.991 | |
| 2023 | sqrt | +0.00045 [+0.00009,+0.00080] | 0.027 | +0.0101 [+0.0074,+0.0127] | 0.00751 | 0.904 / 0.954 / 0.991 | no |
| 2023 | mondrian_up | +0.00006 [-0.00012,+0.00023] | 0.499 | -0.0023 [-0.0037,-0.0007] | 0.00228 | 0.903 / 0.953 / 0.990 | no |
| 2023 | gamma_tail | +0.00004 [-0.00001,+0.00009] | 0.194 | -0.0000 [-0.0001,+0.0000] | 0.00418 | 0.902 / 0.953 / 0.992 | no |
| 2024 | production | (TLL 0.20242, CRPS 3.1928) | | | 0.00384 | 0.897 / 0.949 / 0.989 | |
| 2024 | sqrt | +0.00045 [+0.00011,+0.00079] | 0.042 | +0.0061 [+0.0034,+0.0086] | 0.00662 | 0.898 / 0.949 / 0.989 | no |
| 2024 | mondrian_up | -0.00016 [-0.00034,+0.00002] | 0.140 | -0.0022 [-0.0037,-0.0007] | 0.00289 | 0.898 / 0.949 / 0.989 | no |
| 2024 | gamma_tail | -0.00001 [-0.00006,+0.00004] | 0.708 | +0.0000 [-0.0000,+0.0000] | 0.00399 | 0.896 / 0.949 / 0.989 | no |

Verdict: no candidate passes. Production pts upper tail is already calibrated at q0.90/0.95/0.99; the red-team thin-tail figure was the hybrid arm.
