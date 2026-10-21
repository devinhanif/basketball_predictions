# ROLE_PRIOR_SALARY (pre-registration) -- DRAFT, priors section committed first

Status: **DRAFT stub, priors only.** The full draft replaces everything above the 'Pending priors' heading; that section is not edited after this commit.

## Pending priors (written before any gate number of this rule was computed; the main session copies them to docs/PREDICTIONS_LEDGER.md)
Not blind to: the task's counts (2,773 salary rows with an id and a dollar value, 511-531 players per completed season) and T500's mover counts (136 / 115 movers, 1,202 / 1,025 K = 10 rows per stat). Blind to: every coverage, crosswalk, power and Spearman number of this rule, and to every 2023/24 outcome contrast.
* P(share of K = 10 mover rows with a salary row on the new team >= 0.85, each of 2023, 2024) 0.55; P(>= 0.70 each) 0.90.
* P(crosswalk resolves >= 95% of mover player-seasons to an nba_api id, each season) 0.65.
* P(mover K = 10 rows with salary and id >= 400 per stat, each season) 0.90.
* P(player-season-clustered half-width of the paired CI at a half-floor true gain <= the pts floor 0.005, each season) 0.03; reb (floor 0.003) 0.05; ast (0.002) 0.05; fg3m (0.001) 0.10. Game-clustered pts half-width at T500's value 0.010 or below: 0.70.
* P(Spearman of within-team salary rank vs minutes per game on non-mover rows, select season >= 0.50) 0.80; P(bootstrap CI excludes 0) 0.99.
* P(the full minimum-data gate, as defined in the final draft, passes with the power check included) 0.05; P(the label-free parts alone, coverage and crosswalk, pass) 0.55.
* If ever frozen and run: P(any stat clears both seasons with the top-5 guard and the player-season CI) 0.02; P(pts clears) 0.01. Reason: T500 (the same cohort, a stronger information source) tied; 5 of 115 players carried the gain; salary has no information beyond what a mover's role-in-old-team and the old-team usage already hold for the top of a roster.
