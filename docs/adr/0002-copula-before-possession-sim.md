# ADR 0002: Gaussian copula before possession-sim joint

Status: accepted (2026-10-08)

## Context
Same-game parlay legs are correlated (a player's pts/reb/ast share minutes; teammates share
pace and usage). Independence is known wrong. The possession sim (rungs 3-4) would give joint
outcomes natively but is not built, and has not yet been shown to beat rung 2 on win-prob
calibration (CLAUDE.md risk 1).

## Decision
Build joint engines in this order, each behind a flag: independence (floor), Gaussian copula
(with a t-copula variant for tail dependence), then a possession-sim hook (stub).
The copula keeps each leg's fitted marginal from `prop_predictions` and estimates a correlation
from historical same-game normal-score residuals by (relation, stat-pair), shrunk toward 0 and
repaired to the nearest correlation matrix (Higham). It is cheap, needs only data we already
have, and runs in milliseconds.

## Consequences
- The sim engine is adopted only if it beats the copula on joint log loss / Brier by more than
  the paired-bootstrap noise (`nba/parlay/calibration.py`).
- Pairwise-by-relation correlations ignore game context (blowouts); that is a known limitation.
- Kalshi history is thin, so joint calibration is evaluated on box-score history with model
  marginals, not market prices; any market comparison must state its sample size.
