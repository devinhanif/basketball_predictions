# Paper user: $10 a week, DraftKings main lines at T-60, production props model (OOF rows)

Descriptive replay, 2026-10-11. Not a claim; no production change; season 2025-26 never loaded. Habits fixed in
data/scratch/paper_user_10_per_week_2026-10-11.py before any result was seen. $10 deposited each week with games; the week's
$10 is the only money staked; winnings sit in a balance and are not re-bet. 35 weeks per season, $350 deposited.
Profit CI = week-resampling bootstrap (4,000). 2023-24 is in-sample for the blend weight (selected there).

## 2023-24

| persona | bets | hit rate | staked | profit | 95% CI | max drawdown | best / worst week |
|---|---|---|---|---|---|---|---|
| A model singles, 1 leg/night, edge ≥ 5% | 207 | 0.527 | $350 | $+23.74 | [-33, +83] | $11 | +11.25 / -10.00 |
| B model 3-leg parlay, $10 once a week | 28 | 0.143 | $280 | $+8.85 | [-220, +299] | $100 | +71.02 / -10.00 |
| C blend singles (25% model), edge ≥ 3% | 201 | 0.493 | $340 | $-5.33 | [-59, +50] | $27 | +11.25 / -10.00 |
| D control: book's shortest price each night | 207 | 0.715 | $350 | $-24.29 | [-60, +8] | $29 | +4.44 / -10.00 |
| E control: random leg each night (300 reps) | 207-212 | — | $350 | $-21.06 mean | 5-95%: [-60, +23] | — | — |
| F control: keep your money | 0 | — | $0 | $0 | — | $0 | — |

Weekly cumulative profit, persona A: [-6.67, -5.63, -5.51, -4.66, -5.02, 1.34, -1.02, -2.68, -0.03, -4.74, 1.53, 3.38, 1.89, 3.59, -3.35, -2.23, -1.98, 3.15, 3.56, -0.94, -0.3, -4.95, 1.61, 2.72, -7.28, -5.0, -1.53, -3.09, -4.37, -0.01, 2.17, 12.49, 2.49, 13.74, 23.74]

Parlay weeks that hit (4 of 28): 2024-01-05 6.31x; 2024-01-29 8.1x; 2024-03-13 8.02x; 2024-04-21 6.45x

## 2024-25

| persona | bets | hit rate | staked | profit | 95% CI | max drawdown | best / worst week |
|---|---|---|---|---|---|---|---|
| A model singles, 1 leg/night, edge ≥ 5% | 212 | 0.58 | $350 | $+61.43 | [+17, +104] | $9 | +9.00 / -7.33 |
| B model 3-leg parlay, $10 once a week | 28 | 0.107 | $280 | $-31.06 | [-260, +284] | $150 | +91.37 / -10.00 |
| C blend singles (25% model), edge ≥ 3% | 175 | 0.56 | $274 | $+24.36 | [-15, +63] | $15 | +7.00 / -5.90 |
| D control: book's shortest price each night | 212 | 0.755 | $350 | $+3.51 | [-27, +32] | $14 | +4.14 / -5.61 |
| E control: random leg each night (300 reps) | 207-212 | — | $350 | $-18.05 mean | 5-95%: [-58, +21] | — | — |
| F control: keep your money | 0 | — | $0 | $0 | — | $0 | — |

Weekly cumulative profit, persona A: [-3.25, -2.3, 3.58, 11.02, 14.45, 17.62, 21.86, 25.95, 27.23, 23.74, 21.71, 22.79, 26.6, 27.4, 20.07, 18.04, 23.14, 28.51, 27.73, 22.87, 21.23, 24.96, 26.35, 27.57, 23.53, 25.51, 29.5, 30.87, 37.68, 34.27, 37.16, 39.56, 48.56, 57.13, 61.43]

Parlay weeks that hit (3 of 28): 2024-11-15 10.14x; 2024-12-13 9.28x; 2025-01-15 5.48x

## Reading

Persona A is 87% unders (reb/ast/pts/fg3m under): the 'edge' is the systematic under lean the fan doc already records (realised over
rate below the de-vigged over price in every stat), bought at a retail book's price. Against Pinnacle the same model has no edge
(docs/FAN_KNOWLEDGE.md). Eight persona-season cells were looked at; one CI excludes zero; a true forward season is the only test.
The parlay persona hits 3-4 weeks of 28, swings $100-150 peak-to-trough, and ends near zero either way. Random legs lose the vig (~$20 a season).
