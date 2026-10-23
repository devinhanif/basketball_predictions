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

## Refinement (Devin, 2026-10-22 ~21:10): transaction TYPE, not contract year
"Contract year isn't real but getting waived is; a player can be waived and sign somewhere vs traded and the model
wouldn't know the difference." The model sees only that a player's team changed. Transaction type carries
information: traded (old team valued him; planned role, low pressure) vs waived-then-signed (judged not worth a
roster spot; usually minimum/non-guaranteed; highest survival pressure) vs free-agent signing vs 10-day. Primary
version of the idea now: per-minute production after the move relative to the age-curve/recency expectation, by
transaction type; prediction waived->signed > traded. Survivorship control: re-signed waivers are pre-selected by a
team, so compare excess-over-expectation within comparable profiles and use the guarantee-date DiD as the second check.
Data: official NBA transaction log (nba_api; ~150-300 requests; ToS to check first). Also feeds cold start (T512):
the move type tells the model what role a newcomer is stepping into.
