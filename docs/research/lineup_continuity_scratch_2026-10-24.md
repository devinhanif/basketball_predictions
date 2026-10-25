# Lineup "length": continuity and stint length (scratch, 2026-10-24)

**Status: scratch exploration. Not a claim, not a pre-registration, no ledger row.** Descriptive only;
nothing here may be used to select a model without a new frozen rule.

Data: `nba.duckdb` (read-only), `stints` x `possessions` (tracker v2), regular season (`002%`),
seasons 2022-2024 only (`games.season between 2022 and 2024`; 2025 not read). 3,690 games, 153,624
stints, 734,846 possessions (~99% of possessions matched to a stint unit). "Relative per 100" = unit
points per 100 possessions minus the team's season average (offense: points scored vs team ORtg;
defense: points allowed vs team DRtg, **lower is better**). Net = off rel minus def rel. CIs: 95%
percentile bootstrap, 1,000 resamples of games (game-clustered), seed 7. Script kept in scratch only.

## (A) Continuity: prior shared minutes of the exact five, same season, before that game date

| prior mins | unit-games | minutes | games | off poss | off rel/100 | def rel/100 (lower=better) | net/100 |
|---|---|---|---|---|---|---|---|
| 0 | 50,601 | 123,053 | 3,679 | 251,877 | -1.54 [-2.00, -1.09] | -1.62 [-2.07, -1.14] | +0.08 [-0.42, +0.59] |
| 1-20 | 47,340 | 137,698 | 3,647 | 281,165 | -0.24 [-0.69, +0.22] | -0.08 [-0.53, +0.37] | -0.16 [-0.65, +0.35] |
| 20-100 | 11,102 | 61,410 | 3,266 | 125,117 | +1.74 [+1.07, +2.40] | +0.08 [-0.58, +0.75] | +1.66 [+0.82, +2.43] |
| 100+ | 2,996 | 34,279 | 1,897 | 69,699 | +2.25 [+1.33, +3.14] | +0.03 [-0.85, +0.86] | +2.22 [+1.14, +3.40] |

Contrasts (game-clustered): net 0 minus 100+ = **-2.13 [-3.46, -0.95]**; net 0 minus 1-20 = +0.25
[-0.63, +1.14]. Offense alone 0 minus 100+ = -3.78 [-4.80, -2.86].

Reading: brand-new fives score ~1.5/100 below team average but also *allow* ~1.6/100 less, so on net
they are at team average and indistinguishable from 1-20-minute units. The gradient is at the top:
units with 20+ prior minutes are +1.7 to +2.2 net. That is almost certainly **who** they are, not
**familiarity**: 100+ minute units are starters (median 12 min/game vs 1.8 for 0-minute units), and
coaches keep playing fives that are good. The low-scoring, low-allowing 0-minute bucket looks like
pace/context (late-game, foul-trouble, garbage time, end-of-period), not a "new unit" penalty.
No player-quality control was applied; a fair test would compare to the same players' on-court
ratings (or an RAPM-style expectation) and be pre-registered.

## (B) Stint length within a game

| stint | stints | minutes | off rel/100 | def rel/100 (lower=better) |
|---|---|---|---|---|
| <2 min | 80,655 | 80,915 | -9.82 [-10.37, -9.25] | +3.54 [+2.98, +4.14] |
| 2-4 | 47,174 | 132,529 | +0.70 [+0.24, +1.14] | -0.09 [-0.51, +0.39] |
| 4-6 | 17,772 | 87,060 | +3.84 [+3.26, +4.44] | -2.92 [-3.47, -2.37] |
| 6+ | 8,023 | 55,936 | +7.20 [+6.49, +7.90] | -3.75 [-4.43, -3.05] |

This is selection plus mechanics, not a causal effect of "staying together": coaches pull units
that are getting outscored (and timeouts/subs follow opponent runs), stint length is defined by
the outcome, and very short stints are full of end-of-period heaves, intentional-foul and
free-throw substitution possessions. Do not read +11 net/100 for 6+ minute stints as anything
but "units that play well get left on the floor." Not usable pre-tip.

## (C) Tonight, GSW@PHX: expected five Melton, B. Williams, Santos, Ike, Lendeborg

- Shared minutes as a five in 2022-2024: **0** (Ike and Lendeborg have no stints in 2022-2024;
  Lendeborg is a rookie, Ike is not in the nba_api static player list we checked).
- Pairs: Melton-Williams 0, Melton-Santos 0, Williams-Santos 0. Melton was PHI (2022: 2,150 min;
  2023: 1,023) then GSW 2024 (121 min); Williams DAL (2023: 113; 2024: 487); Santos GSW (2023: 192;
  2024: 762). The 2024 Melton/Santos overlap was zero minutes (Melton's season ended early).
- Per (A), a zero-history five does not carry a measurable net penalty on its own; the gap shows
  up only against the established, mostly-starter fives. Tonight's GSW group is a depleted roster
  and should be judged by its players, not by its novelty.

Unverified: player IDs taken from `nba_api` static list (Melton 1629001, Williams 1630314, Santos
1630611); Ike and Lendeborg IDs not found, so their absence is by name, not ID. Team seasonal
averages include the unit itself. 2025-26 data was not used, so any current-season shared minutes
for this five are not counted here.
