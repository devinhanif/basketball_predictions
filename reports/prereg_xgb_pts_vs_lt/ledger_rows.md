| id | test | result |
|---|---|---|
| T### | XGB_PTS_VS_LT pts 2023, A1 (hybrid_lf quantile) - A0 (production lower-tail mixture), integer-support CRPS (n 27619, 1318 games) | -0.0036 [-0.0094, +0.0019] game-clustered, p_bh 0.4300; floor -0.005 |
| T### | XGB_PTS_VS_LT pts 2024, A1 (hybrid_lf quantile) - A0 (production lower-tail mixture), integer-support CRPS (n 27583, 1315 games) | -0.0000 [-0.0063, +0.0060] game-clustered, p_bh 0.9850; floor -0.005 |
| T### | XGB_PTS_VS_LT pts 2023, threshold log loss A1 - A0 at DK T-60 main lines (n 14354) | -0.0021 [-0.0038, -0.0004]; margin +0.002 |
| T### | XGB_PTS_VS_LT pts 2024, threshold log loss A1 - A0 at DK T-60 main lines (n 15882) | +0.0011 [-0.0003, +0.0024]; margin +0.002 |
| T### | XGB_PTS_VS_LT descriptive 2024, A2 (same recipe, v1 features) - A0 | +0.0023 [-0.0037, +0.0079] |
| T### | XGB_PTS_VS_LT descriptive 2024, minutes >= 5 / < 5 slices A1 - A0 | -0.0148 [-0.0214, -0.0085] / +0.1620 [+0.1448, +0.1800] |
| T### | XGB_PTS_VS_LT verdict | CLOSED; conditions {"P1_2023": false, "P2_2024": false, "P3_tll": true, "P4_calibration": true, "P5_slices": false, "P6_minutes>=5_no_harm": true, "P7_concentration": false, "P8_proxy_safe": false} |
