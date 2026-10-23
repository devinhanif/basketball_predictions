# Reading log: the "out of the league" effect (Devin, 2026-10-22)

**Claim (Devin).** Players whose careers are on the line (risk of being out of the league) play harder, and it shows
up in their production, quantifiably, through age, prior peak and contract security ("Paul George can still get like
that").

**Formalisation.**
- Survival pressure S_ig = P(out of the league next season | as-of age, decline from peak, contract security,
  minutes trend), a logit fit on careers.
- Effect: per-minute production_ig = mu_i(age) + beta * S_ig + e; and, in the "upside" version, a spread term
  sigma_i * exp(delta * M_ig) so pressure fattens the right tail rather than moving the mean.

**Credibility.** The obvious comparison is confounded by survivorship: fringe players keep minutes only when they play
well, so a naive regression finds beta > 0 even if effort has no effect. Published contract-year evidence is mixed and
mostly small.

**Design that beats survivorship.** Difference-in-differences around the league-wide contract guarantee date (early
January): non-guaranteed vs already-guaranteed players, per-minute production before vs after the date, same
players; second check on 10-day contracts (10-day vs after a rest-of-season signing). Prediction under the effort
story: the non-guaranteed group's per-minute production drops after guarantee, relative to the control group.

**Prior evidence here.** None tested. Related: SEASON_BOUNDARY (T515) adds an age curve; ROSTER_CONTINUITY closed on
effect size; T476 descriptive EDA on tanking/race status.

**Fit / data.** Box scores 2019-24 (+ data/history to 2013) hold minutes and per-minute stats. Basketball-Reference
salary tables (data/raw/bref_salaries_2026-10-21) give amounts, NOT guarantee status; a guarantee-status source must
be found and its licence checked before any gate. Bettable payoff if real: bench and fringe players' props in
December, priced off season averages.

**Draft rule (outline, not frozen).** OUT_OF_LEAGUE_DID: primary = DiD on per-36 production around the guarantee date,
seasons 2019-23 select / 2024 report, player-clustered CI; secondary = props CRPS gain on high-S rows in December
with an information-matched control. Minimum-data gate first (non-guaranteed player-seasons per year, minutes either
side of the date). Queued after SEASON_BOUNDARY.
