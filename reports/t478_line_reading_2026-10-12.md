# T478 line reading, 2026-10-12 (DESCRIPTIVE, selected after the result; no bearing on the T478 verdict)

Commitment (3) of DECISIONS 2026-10-12 17:19. Question: how much of the CRPS gain of AVG over FULL reaches the posted line?

## Data and method
- Source: `reports/prereg_seq_full_mix_rt/stage2/descriptive/rows.parquet` only (the logged T478 touch, run 20261012_162217). The 2025 lines are already in
  that artifact (HOLDOUT_ACCESS_LOG row 61 discloses the descriptive step read odds_history.duckdb). No DB opened, no new touch, no disclosure row needed.
- Books: Pinnacle (primary) and DraftKings. T-60 prices; x.5 lines only (no pushes). Model P(over) = share of the arm's 199-point integer grid above the line
  (saved `<arm>_p_over_<book>`). Arms: A0, FULL, AVG (= 50/50 pmf mixture of SEQc and FULL).
- Fair price: the saved `<book>_p_over_devig` = `p_over_mult`, multiplicative de-vig of the over/under American prices (not the raw price). Used only as the market reference column (MKT).
- Over event = actual > line. Log loss with P clipped to [1e-4, 1-1e-4]. Brier = (P-o)^2.
- CIs: paired per-row differences, game-clustered bootstrap (ratio of resampled sums, 2000 draws, seed 20261012). n = rows with a line. Negative = AVG better.
- Splits: minutes >= 5 vs < 5 (realised minutes, ex-post, as in the review), regular season (game_id 002...) vs post (004 playoffs + 005 play-in, pooled).
- Code: `data/scratch/t478_line_reading_2026-10-12.py` (scratch-grade, kept out of the linted tree). Full numbers: `reports/t478_line_reading_2026-10-12.json`.
- Caveat: these P(over) were never calibrated to lines; the model grids are trained for CRPS, not for the over/under event.

## Headline: pts, Pinnacle (n = 14,620 lines, 1,291 games; over rate 0.488)
| metric | A0 | FULL | AVG | market (de-vigged) | AVG-FULL | AVG-A0 | AVG-market |
|---|---|---|---|---|---|---|---|
| log loss | 0.7085 | 0.7077 | 0.7059 | 0.6912 | -0.0018 [-0.0027,-0.0010] | -0.0026 [-0.0035,-0.0017] | +0.0147 [+0.0115,+0.0179] |
| Brier | 0.2569 | 0.2566 | 0.2558 | 0.2490 | -0.0008 [-0.0012,-0.0005] | -0.0012 [-0.0016,-0.0007] | |
| CRPS (same rows) | | | | | -0.0152 [-0.0186,-0.0116] | | |

pts, DraftKings (n = 17,544, 1,290 games): log loss A0 0.7103, FULL 0.7093, AVG 0.7068, market 0.6923. AVG-FULL -0.0025 [-0.0032,-0.0018]; AVG-A0 -0.0035 [-0.0044,-0.0025]; AVG-market +0.0144 [+0.0114,+0.0174]. Brier AVG-FULL -0.0011 [-0.0015,-0.0008]. CRPS AVG-FULL -0.0157 [-0.0190,-0.0125].

## Secondary stats (AVG-FULL, log loss; Brier; CRPS on the same line rows). Pinnacle then DraftKings
| stat | book | n (games) | log loss AVG-FULL | Brier AVG-FULL | CRPS AVG-FULL | AVG ll vs market |
|---|---|---|---|---|---|---|
| reb | Pin | 7,118 (1,288) | -0.0007 [-0.0016,+0.0001] | -0.0003 [-0.0007,+0.0001] | -0.0048 [-0.0068,-0.0029] | +0.0054 [+0.0010,+0.0098] |
| reb | DK | 16,239 (1,288) | -0.0010 [-0.0015,-0.0004] | -0.0004 [-0.0007,-0.0002] | -0.0036 [-0.0046,-0.0025] | +0.0084 [+0.0058,+0.0111] |
| ast | Pin | 6,338 (1,283) | -0.0016 [-0.0024,-0.0007] | -0.0007 [-0.0011,-0.0003] | -0.0055 [-0.0073,-0.0037] | +0.0138 [+0.0085,+0.0189] |
| ast | DK | 12,214 (1,288) | -0.0019 [-0.0026,-0.0013] | -0.0009 [-0.0012,-0.0006] | -0.0050 [-0.0062,-0.0040] | +0.0103 [+0.0070,+0.0135] |
| fg3m | Pin | 7,908 (1,145) | -0.0027 [-0.0039,-0.0016] | -0.0013 [-0.0018,-0.0007] | -0.0049 [-0.0062,-0.0037] | +0.0035 [-0.0002,+0.0071] |
| fg3m | DK | 13,145 (1,288) | -0.0025 [-0.0034,-0.0017] | -0.0011 [-0.0016,-0.0007] | -0.0036 [-0.0045,-0.0027] | +0.0030 [+0.0001,+0.0058] |

AVG-A0 log loss (all negative): pts Pin -0.0026, reb Pin -0.0007 [-0.0018,+0.0004], ast Pin -0.0013 [-0.0026,0.0000], fg3m Pin -0.0033 [-0.0048,-0.0020]; DK pts -0.0035, reb -0.0015, ast -0.0023, fg3m -0.0029.

## Splits (AVG-FULL log loss; CRPS in brackets for the same rows)
| stat | book | slice | n (games) | log loss AVG-FULL | CRPS AVG-FULL |
|---|---|---|---|---|---|
| pts | Pin | regular | 13,325 (1,200) | -0.0019 [-0.0028,-0.0011] | -0.0169 [-0.0207,-0.0131] |
| pts | Pin | post (playoff+play-in) | 1,295 (91) | -0.0006 [-0.0026,+0.0015] | +0.0029 [-0.0056,+0.0110] |
| pts | Pin | minutes >= 5 | 14,580 (1,291) | -0.0018 [-0.0026,-0.0011] | -0.0152 [-0.0188,-0.0115] |
| pts | Pin | minutes < 5 | 40 (39) | -0.0021 [-0.0161,+0.0133] | +0.0017 [-0.0798,+0.0866] |
| pts | Pin | regular and min >= 5 (the T478 claim cell) | 13,297 (1,200) | -0.0020 [-0.0028,-0.0011] | -0.0171 [-0.0209,-0.0132] |
| pts | DK | regular | 16,161 (1,200) | -0.0027 [-0.0034,-0.0020] | -0.0175 [-0.0209,-0.0140] |
| pts | DK | post | 1,383 (90) | -0.0002 [-0.0024,+0.0019] | +0.0047 [-0.0045,+0.0137] |
| pts | DK | minutes < 5 | 71 (68) | +0.0105 [-0.0012,+0.0222] | +0.0593 [-0.0001,+0.1205] |
| pts | DK | regular and min >= 5 | 16,106 (1,200) | -0.0027 [-0.0035,-0.0020] | -0.0178 [-0.0213,-0.0146] |
| reb | DK | regular / post | 14,939 / 1,300 | -0.0012 [-0.0017,-0.0006] / +0.0012 [-0.0007,+0.0031] | -0.0042 / +0.0029 |
| ast | DK | regular / post | 11,071 / 1,143 | -0.0022 [-0.0028,-0.0015] / +0.0003 [-0.0011,+0.0019] | -0.0056 / +0.0006 |
| fg3m | DK | regular / post | 12,032 / 1,113 | -0.0028 [-0.0038,-0.0019] / +0.0009 [-0.0007,+0.0024] | -0.0040 / +0.0015 |
| fg3m | DK | minutes < 5 | 38 (38) | +0.0269 [+0.0090,+0.0462] | +0.0284 [+0.0084,+0.0495] |

Minutes < 5 cells are tiny (38-71 lines; Pinnacle reb/ast/fg3m have fewer than 30); they are noise except that the direction (AVG worse, as in the review's +0.044 CRPS on all <5 rows) matches. Almost all of these players went under (over rate 0 to 0.03). All other cells are in the JSON.

## Calibration of P(over) by decile (pts, AVG; predicted / observed over rate; deciles of the arm's own P(over))
| decile | Pin n | Pin AVG | DK n | DK AVG |
|---|---|---|---|---|
| 1 | 1,470 | 0.310 / 0.466 | 1,774 | 0.307 / 0.469 |
| 2 | 1,655 | 0.392 / 0.482 | 1,915 | 0.392 / 0.481 |
| 3 | 1,487 | 0.430 / 0.477 | 1,769 | 0.430 / 0.465 |
| 4 | 1,446 | 0.457 / 0.467 | 1,693 | 0.457 / 0.467 |
| 5 | 1,252 | 0.480 / 0.482 | 1,887 | 0.482 / 0.490 |
| 6 | 1,569 | 0.502 / 0.486 | 1,536 | 0.505 / 0.473 |
| 7 | 1,512 | 0.527 / 0.481 | 1,802 | 0.527 / 0.494 |
| 8 | 1,371 | 0.552 / 0.506 | 1,866 | 0.554 / 0.494 |
| 9 | 1,428 | 0.584 / 0.492 | 1,621 | 0.589 / 0.484 |
| 10 | 1,430 | 0.653 / 0.549 | 1,681 | 0.656 / 0.535 |

Weighted slope of observed on predicted across deciles (1.0 = calibrated): pts AVG 0.19 (FULL 0.17, A0 0.16) Pinnacle, 0.15 / 0.13 / 0.11 DraftKings; ECE (n-weighted) pts Pin AVG 0.062 vs FULL 0.065 vs A0 0.069.
Secondary slopes (AVG, Pin): reb 0.48, ast 0.42, fg3m 0.72; ECE reb 0.049, ast 0.058, fg3m 0.029 (AVG better than FULL on fg3m 0.039, about equal on reb/ast). All arms and stats by decile are in the JSON.

## Reading (descriptive)
Yes, in direction and significance, but not in any size that matters for a line. On pts the AVG gain reaches the over/under: log loss -0.0018 [-0.0027,-0.0010] vs FULL and Brier -0.0008 [-0.0012,-0.0005] at Pinnacle (n 14,620, 1,291 games), and -0.0025 / -0.0011 at DraftKings, concentrated in the regular season (regular-season minutes >= 5 pts: -0.0020 [-0.0028,-0.0011], n 13,297) and absent in the postseason (CI spans 0, as the CRPS review found), so the claim keeps its T478 conditions. The line-level gain is about 0.25% of FULL's log loss, against a CRPS gain of 0.5%; the line only sees the part of the distribution near the line. reb/ast/fg3m show the same sign (reb Pinnacle's CI touches 0). The point that matters: every arm is worse than the market at the line (AVG minus de-vigged price log loss +0.0147 [+0.0115,+0.0179] on pts, positive on every stat), the AVG-FULL gain closes about 12% of that pts gap, and all three arms have pts log loss above ln 2 = 0.6931, i.e. a coin flip at the line scores better than the model's P(over). The uncalibrated P(over) is overdispersed against the lines: decile slope 0.15 to 0.19 on pts, so predictions of 0.31 realise 0.47. AVG is the least miscalibrated arm on pts and fg3m, which is where its log-loss gain comes from, but nothing here is an edge; it is consistent with CLAUDE.md's "the market beats us" and with the T477 sim finding. Not tested here: a line-aware recalibration of AVG, which would be a new rule and a new question.

## What I could not verify
- Whether the saved P(over) grid is exactly the pmf used for CRPS scoring (taken from `p_over_line` in `seq_full_mix_descriptive.py`; same equal-mass integer grid).
- Minutes < 5 is realised minutes (ex post), so that split cannot be used to bet. Playoff and play-in are pooled (n = 91 games).
- Lines are the T-60 snapshot per the descriptive step; line availability is not random (players with props are rotation players), so n differs from the 28,173 of the primary.
