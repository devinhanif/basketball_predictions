# DECISION_SCORING: score the bets we would actually place, at the actual price

Status: definition for other pre-registrations to cite (Devin approved the metric 2026-10-11). The module is
`nba/markets/decision_scoring.py`; first descriptive run: `reports/decision_scoring_2026-10-11.md`.
Descriptive output is not a claim. A rule that declares this metric still needs its own committed floor, CI and
multiplicity plan before any result exists.

## Definition
- **Leg**: one (game, player, stat) with a two-sided American price at one book at T-60 (the capture stamped about
  tip - 64 min) and a stored walk-forward model P(over) at that book's line. The price must precede tip.
- **Model side and EV**: for each side, `EV = p_side * decimal_odds_side - 1 - fee`, decimal odds from the actual
  American price (not a de-vigged price). The model bets the side with the larger EV (ties to the over). `fee` is a
  `FeeSpec` object; a sportsbook has no explicit per-bet fee (the overround is in the quoted price), so it is 0.0 and the
  source string says so. A Kalshi-priced run would pass the verified `nba.parlay` fee config instead; no rate is
  hardcoded in the scorer.
- **Bet rule**: bet when `EV >= threshold`; thresholds reported: 0, 2%, 5%, 10% (nested sets, not independent tests).
- **Staking**: one unit per bet, for scoring only. No stake sizing, no recommendation.
- **Reported per stat and pooled**: number of bets, hit rate, mean decimal price, ROI per unit staked
  (`sum(profit) / n`, profit = decimal - 1 on a win, -1 on a loss; no pushes, lines end in .5), with a 95% CI.
- **CI**: game-clustered bootstrap (multinomial resample of games, 2000 draws, seed 0, percentile), one resample
  matrix shared by every statistic. Below 300 bets a cell is flagged "insufficient sample" and nothing is claimed.
- **Comparators with the same bet count n**:
  - *market lean*: the n eligible legs whose de-vigged price is most lopsided (largest |p_over_mult - 0.5|), betting the
    favoured side at its actual price. Same CI method.
  - *random-selection placebo*: n random eligible legs, random side, 200 draws; reported as the mean of the draws with the
    2.5 and 97.5 percentiles of the draws (random-draw spread only, not a clustered CI). The placebo ROI is about minus the
    overround, so "beats the placebo" only means "recovers the vig"; the line that matters is ROI > 0 with the CI.
- **By confidence bin**: the model's chosen-side probability, bins <0.45, 0.45-0.50, 0.50-0.55, 0.55-0.60, >=0.60,
  among EV >= 0 bets, beside the market's de-vigged probability of the same side and the realised hit rate.
- **Scoring against the price at prediction time**: T-60 price and a forecast that never saw later information. Never the close.

## What this metric is not
It is not a profit claim and not advice. It is silent on correlation between legs (hence game clustering), on limits
and line availability at a real book, and on whether DraftKings' T-60 price was available to take. Selection on
`EV >= threshold` picks the legs where the model disagrees most with the price, which are also where a miscalibrated
model is most wrong (see the confidence-bin table).

## Reproduce
`.venv/bin/python -W ignore -m nba.markets.decision_scoring run --pairs-dir data/ops/model_clv_pairs` (about 6 s; reads
the two MODEL_CLV pairs parquets, seasons 2023 and 2024 only, asserted).
