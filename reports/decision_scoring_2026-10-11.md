# Decision scoring, 2026-10-11 (DraftKings T-60, seasons 2023 and 2024)

**Descriptive, not claims.** Nothing here is a pre-registered test; it describes what the stored model forecasts would have
done at the stored DraftKings T-60 prices. Seasons 2023 (2023-24) and 2024 (2024-25) only; season 2025 was never loaded.
Read-only: no orders, no credentials. Every number carries n and a game-clustered 95% bootstrap CI. Definition:
`docs/DECISION_SCORING.md`; code `nba/markets/decision_scoring.py`; numbers `reports/decision_scoring_2026-10-11/results.json`.

## One-screen summary for Devin

**Question**: if we had bet every prop where the model's chance, priced at DraftKings' actual odds, beat the price by at
least X%, one unit each, how would we have done?

**Answer in plain words: keep your money.** Across 106,999 DraftKings T-60 legs (2,616 games):

| rule (model's edge after the actual price, fee 0 at a sportsbook) | bets | hit rate | ROI per unit [95% CI] | market-lean, same count | random, same count |
|---|---|---|---|---|---|
| EV >= 0% | 79,675 | 52.6% | -1.2% [-1.9%, -0.4%] | -4.8% [-5.4%, -4.2%] | -6.1% |
| EV >= 2% | 71,287 | 52.8% | -0.8% [-1.5%, +0.1%] | -5.1% [-5.7%, -4.6%] | -6.1% |
| EV >= 5% | 58,968 | 53.0% | -0.1% [-1.0%, +0.8%] | -4.6% [-5.3%, -4.0%] | -6.2% |
| EV >= 10% | 41,008 | 52.9% | +0.4% [-0.6%, +1.5%] | -3.9% [-4.7%, -3.1%] | -6.1% |

- Picking blindly loses the bookmaker's cut (about 6 cents per dollar). Following the market's favourite loses about 4-5.
  The model's picks lose about 1 cent at the loosest rule and are indistinguishable from break-even at 5% and 10%
  (CIs straddle zero). So the model recovers most of the vig; it does not clear it.
- **Seasons disagree**: at 5% the all-stat ROI is +0.6% [-0.7%, +1.8%] in 2023 and -0.9% [-2.1%, +0.4%] in 2024.
- **By stat**: rebounds is the only cell with a CI above zero (5%: +1.9% [+0.5%, +3.4%], n 15,620; 10%: +2.0%
  [+0.2%, +3.8%], n 10,686). It is 2 of 20 pooled cells with no multiplicity correction; 2023 +2.4% [+0.5%, +4.5%], 2024
  +1.2% [-1.0%, +3.3%]. Points is negative (5%: -1.6% [-3.0%, -0.2%]). Worth a pre-registered follow-up on rebounds at most,
  not a bet.
- **Confidence**: the model is informative but over-confident. When it said 60%+ for its side (n 38,598) the side hit
  57.0%, the market's de-vigged price said 53.3%, and the model said 66.7%. ROI there +0.5% [-0.4%, +1.5%].

**One real example** (player_id 1641707, Utah, 2024-03-04, threes made, DraftKings T-60; the Utah forward drafted 2023 from UCF): line 1.5, over +140 / under -180.
The market's de-vigged chance of the over was 39.3%; the model said 26.6%. At -180 the under had a model EV of +14.1%
per unit, so the rule bets it. He made 2 threes, the over hit, and the bet lost 1 unit. A loss does not make it a bad bet and a
win would not have made it a good one: the number to judge is the ROI over thousands of such bets (above), not this one.

## Tables

### Pooled 2023+2024, stat = all (106999 eligible legs)

| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |
|---|---|---|---|---|
| model, EV >= 0% | 79675 | 52.6% | 1.906 | -1.2% [-1.9%, -0.4%] |
| market lean, same count | 79675 | 56.2% | 1.700 | -4.8% [-5.4%, -4.2%] |
| random placebo, same count | 79675 | 50.0% | | -6.1% [-6.8%, -5.5%] draws |
| model, EV >= 2% | 71287 | 52.8% | 1.908 | -0.8% [-1.5%, +0.1%] |
| market lean, same count | 71287 | 56.4% | 1.688 | -5.1% [-5.7%, -4.6%] |
| random placebo, same count | 71287 | 50.0% | | -6.1% [-6.7%, -5.5%] draws |
| model, EV >= 5% | 58968 | 53.0% | 1.915 | -0.1% [-1.0%, +0.8%] |
| market lean, same count | 58968 | 57.5% | 1.666 | -4.6% [-5.3%, -4.0%] |
| random placebo, same count | 58968 | 50.0% | | -6.2% [-6.9%, -5.5%] draws |
| model, EV >= 10% | 41008 | 52.9% | 1.928 | +0.4% [-0.6%, +1.5%] |
| market lean, same count | 41008 | 59.2% | 1.627 | -3.9% [-4.7%, -3.1%] |
| random placebo, same count | 41008 | 50.0% | | -6.1% [-6.9%, -5.3%] draws |

### Pooled 2023+2024, stat = pts (30236 eligible legs)

| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |
|---|---|---|---|---|
| model, EV >= 0% | 22820 | 51.6% | 1.883 | -3.0% [-4.2%, -1.8%] |
| market lean, same count | 22820 | 52.7% | 1.797 | -5.3% [-6.5%, -4.2%] |
| random placebo, same count | 22820 | 50.0% | | -6.3% [-7.2%, -5.0%] draws |
| model, EV >= 2% | 20649 | 52.0% | 1.884 | -2.3% [-3.5%, -1.0%] |
| market lean, same count | 20649 | 52.7% | 1.793 | -5.5% [-6.7%, -4.4%] |
| random placebo, same count | 20649 | 50.0% | | -6.2% [-7.5%, -5.0%] draws |
| model, EV >= 5% | 17160 | 52.3% | 1.886 | -1.6% [-3.0%, -0.2%] |
| market lean, same count | 17160 | 53.1% | 1.785 | -5.3% [-6.7%, -3.9%] |
| random placebo, same count | 17160 | 50.0% | | -6.3% [-7.8%, -4.8%] draws |
| model, EV >= 10% | 12219 | 52.4% | 1.889 | -1.3% [-3.0%, +0.4%] |
| market lean, same count | 12219 | 53.1% | 1.774 | -5.8% [-7.4%, -4.1%] |
| random placebo, same count | 12219 | 50.0% | | -6.4% [-8.2%, -4.9%] draws |

### Pooled 2023+2024, stat = reb (29800 eligible legs)

| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |
|---|---|---|---|---|
| model, EV >= 0% | 21696 | 53.5% | 1.901 | +0.5% [-0.6%, +1.8%] |
| market lean, same count | 21696 | 55.6% | 1.709 | -5.3% [-6.3%, -4.1%] |
| random placebo, same count | 21696 | 50.0% | | -6.1% [-7.3%, -4.9%] draws |
| model, EV >= 2% | 19182 | 53.7% | 1.902 | +1.1% [-0.2%, +2.4%] |
| market lean, same count | 19182 | 56.2% | 1.698 | -4.7% [-5.8%, -3.5%] |
| random placebo, same count | 19182 | 50.0% | | -6.2% [-7.4%, -5.0%] draws |
| model, EV >= 5% | 15620 | 54.0% | 1.908 | +1.9% [+0.5%, +3.4%] |
| market lean, same count | 15620 | 57.3% | 1.682 | -3.7% [-5.0%, -2.4%] |
| random placebo, same count | 15620 | 50.0% | | -6.1% [-7.5%, -4.5%] draws |
| model, EV >= 10% | 10686 | 53.8% | 1.915 | +2.0% [+0.2%, +3.8%] |
| market lean, same count | 10686 | 58.5% | 1.658 | -3.1% [-4.6%, -1.5%] |
| random placebo, same count | 10686 | 50.0% | | -6.2% [-7.9%, -4.4%] draws |

### Pooled 2023+2024, stat = ast (20224 eligible legs)

| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |
|---|---|---|---|---|
| model, EV >= 0% | 14593 | 51.9% | 1.923 | -1.5% [-3.1%, +0.1%] |
| market lean, same count | 14593 | 56.5% | 1.679 | -5.3% [-6.6%, -4.0%] |
| random placebo, same count | 14593 | 50.0% | | -5.9% [-7.5%, -4.5%] draws |
| model, EV >= 2% | 12936 | 51.9% | 1.927 | -1.5% [-3.1%, +0.2%] |
| market lean, same count | 12936 | 57.3% | 1.668 | -4.6% [-5.9%, -3.1%] |
| random placebo, same count | 12936 | 50.0% | | -5.9% [-7.4%, -4.3%] draws |
| model, EV >= 5% | 10604 | 52.0% | 1.933 | -0.9% [-2.7%, +1.1%] |
| market lean, same count | 10604 | 58.1% | 1.650 | -4.3% [-5.9%, -2.8%] |
| random placebo, same count | 10604 | 50.0% | | -6.0% [-7.9%, -4.1%] draws |
| model, EV >= 10% | 7212 | 52.0% | 1.946 | -0.4% [-2.6%, +2.0%] |
| market lean, same count | 7212 | 58.9% | 1.622 | -4.5% [-6.4%, -2.7%] |
| random placebo, same count | 7212 | 50.0% | | -5.9% [-7.8%, -4.1%] draws |

### Pooled 2023+2024, stat = fg3m (26739 eligible legs)

| arm @ EV threshold | bets | hit rate | mean decimal price | ROI per unit [95% CI] |
|---|---|---|---|---|
| model, EV >= 0% | 20566 | 53.4% | 1.923 | -0.7% [-2.0%, +0.6%] |
| market lean, same count | 20566 | 59.7% | 1.610 | -4.4% [-5.5%, -3.4%] |
| random placebo, same count | 20566 | 50.0% | | -6.2% [-7.3%, -4.9%] draws |
| model, EV >= 2% | 18520 | 53.4% | 1.929 | -0.4% [-1.9%, +0.9%] |
| market lean, same count | 18520 | 60.6% | 1.594 | -3.8% [-5.0%, -2.7%] |
| random placebo, same count | 18520 | 50.0% | | -6.2% [-7.8%, -5.0%] draws |
| model, EV >= 5% | 15584 | 53.2% | 1.943 | -0.0% [-1.6%, +1.5%] |
| market lean, same count | 15584 | 61.3% | 1.569 | -4.1% [-5.3%, -3.0%] |
| random placebo, same count | 15584 | 50.0% | | -6.2% [-7.7%, -4.7%] draws |
| model, EV >= 10% | 10891 | 53.2% | 1.974 | +1.4% [-0.5%, +3.2%] |
| market lean, same count | 10891 | 63.3% | 1.529 | -3.5% [-4.9%, -2.1%] |
| random placebo, same count | 10891 | 50.0% | | -6.1% [-8.1%, -4.0%] draws |

### By season (model arm only, all stats)

| season | EV >= | bets | hit | ROI [95% CI] |
|---|---|---|---|---|
| 2023 | 0% | 40746 | 52.8% | -0.6% [-1.7%, +0.4%] |
| 2023 | 2% | 36806 | 53.0% | -0.1% [-1.1%, +1.0%] |
| 2023 | 5% | 30951 | 53.1% | +0.6% [-0.7%, +1.8%] |
| 2023 | 10% | 22312 | 53.2% | +1.4% [+0.1%, +2.9%] |
| 2024 | 0% | 38929 | 52.5% | -1.7% [-2.8%, -0.7%] |
| 2024 | 2% | 34481 | 52.6% | -1.5% [-2.6%, -0.3%] |
| 2024 | 5% | 28017 | 52.8% | -0.9% [-2.1%, +0.4%] |
| 2024 | 10% | 18696 | 52.5% | -0.8% [-2.3%, +0.7%] |

### By confidence bin (model chosen-side probability; bets with EV >= 0; pooled)

| bin | bets | hit rate | model mean p | market de-vigged mean p | ROI [95% CI] |
|---|---|---|---|---|---|
| [0.00, 0.45) | 2118 | 37.4% | 41.3% | 36.7% | -3.6% [-9.3%, +1.6%] |
| [0.45, 0.50) | 5490 | 42.3% | 47.8% | 42.5% | -5.9% [-8.7%, -3.0%] |
| [0.50, 0.55) | 12953 | 48.1% | 52.8% | 46.4% | -2.4% [-4.2%, -0.6%] |
| [0.55, 0.60) | 20516 | 51.8% | 57.6% | 49.6% | -2.0% [-3.4%, -0.6%] |
| [0.60, 1.00) | 38598 | 57.0% | 66.7% | 53.3% | +0.5% [-0.4%, +1.5%] |

## Method notes and honest limits
- Prices: DraftKings, the `t60` capture (stamped about tip - 64 min), asserted to precede tip. Forecasts: the stored
  walk-forward out-of-fold `p_model` at DraftKings' own line. Nothing is refit here and no later information is used.
- Market lean = the n legs with the most lopsided de-vigged price, favoured side at its actual price. Placebo = n random legs, random
  side, 200 draws (interval = spread of the draws, not a clustered CI).
- Fee: sportsbook has no explicit fee, so `FeeSpec.per_unit = 0.0`; the vig sits in the price and so in the ROI.
- Thresholds are nested sets, so the four rows per stat are not independent looks. 20 pooled cells x 3 arms were examined
  with no multiplicity correction; treat a single positive cell as a lead at most.
- Not modelled: correlation between a player's several props in one game beyond game-clustered CIs, bet limits, whether
  the T-60 price was takeable, and that the 0.0 fee is a sportsbook's; Kalshi prices would use the verified fee config.
- Proposed ledger/DECISIONS rows are in the hand-back, not appended.
