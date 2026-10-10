# ODDS_BENCHMARK - do our live props probabilities beat the sharp line? (pre-registration)

Status: DRAFT 2026-10-10, for Devin to confirm. To be frozen (hash recorded below, committed) before
the first scored pair exists; captures may accumulate before the freeze, scoring may not.
Verify: `awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' docs/prereg/ODDS_BENCHMARK.md | shasum -a 256`
sha256 of the frozen section: (recorded at freeze)

<!-- FROZEN-BEGIN -->
## 0. Question and what this binds

The project has never seen a historical prop price. This rule binds how the LIVE 2026-27 props
forecasts are scored against the sharp market, using the read-only theoddsapi.com capture
(`nba/odds`, Pinnacle plus US books) stored next to Kalshi in the as-of market store. It decides one
thing per stat: whether the production props model has skill relative to the market price, and
whether that skill survives the price. Nothing here promotes or wires anything; a pass is a
"promote-candidate" for the budget tool's market-as-prior gate (CLAUDE.md "Markets are a prior")
and a Devin decision.

## 1. Population and pairing

1. Regular-season games of `season = 2026` only. Units are `(stat, game_id, player_id, line)` where a
   Pinnacle two-sided over/under quote exists for the player's `player_points | player_rebounds |
   player_assists | player_threes` market AND the production model (`props_context_residual`) has a
   stored pre-tip row for the same player-game. Both sides of the quote must exist (de-vig needs both).
   One line per unit: the Pinnacle main line (the line nearest the book's median when several exist).
2. Two timings, scored separately, both pre-declared:
   - **Matched-time:** the last Pinnacle quote captured at or before the model row's `made_at`, and the
     latest model row with `made_at <= tip - 60 min`. Same information horizon on both sides.
   - **Closing:** the last Pinnacle quote captured before tip (our capture cadence, about every 30 min,
     bounds "closing" to the last pre-tip snapshot) against the same T-60 model row. The market has
     more information here by construction; this is the harder, money-relevant comparison.
3. Pairs are formed by key intersection; one-sided rows are counted (`no_quote`, `no_model_row`,
   `one_sided_quote`, `unresolved_name`) and reported, never silently dropped. Player and team names
   resolve through the same resolver as the Kalshi path; unresolved rows are excluded and counted.
4. DNP rows are excluded identically from both sides (the market voids them too) and counted.

## 2. Probabilities on the same support

- Model: P(over L) with L = k + 0.5 is P(Y >= k + 1) from the stored integer-support thresholds
  (`p_ge_full`, convention P(Y >= N) = P(continuous >= N - 0.5)); for a whole-number line the push
  mass P(Y = L) is removed from both sides and the pair is scored on over vs under conditional on no
  push. The production arm is scored; shadow arms (`_int`, `_lt`, `_t30`) are reported descriptively
  on the same pairs, never decided here (their rules live in FORWARD_PREREG_2026_27).
- Market: de-vigged implied probability from the two American prices (multiplicative normalisation).
  The raw prices are kept for the EV section.

## 3. Metrics and inference

1. **Primary (skill):** paired log loss on the over/under outcome, model minus market, per stat and
   timing. Negative = model better. Secondary: Brier; reliability of each side in ten bins.
2. **CI:** percentile bootstrap over GAME DATES (one cluster per calendar date), B = 10,000,
   `numpy.random.default_rng(2026)` re-seeded at each look; two-sided p from the bootstrap.
3. **Practical floor (skill):** mean paired log-loss delta <= -0.005 with the CI upper bound < 0.
4. **Money question (EV):** for each pair, the model's edge at the book's price is
   `p_model - p_breakeven(price)`; declared bets are the pairs with edge >= 0.03 at matched-time.
   Report the realised return per unit stake of those bets, with the same date-clustered CI, after a
   stated fee/vig model (the price already contains the vig; no further fee for a sportsbook, Kalshi's
   fee formula for Kalshi pairs). **Positive EV is claimed only if the CI lower bound of realised
   return > 0 and the skill floor passed for that stat.**
5. **Multiplicity:** family of 8 = 4 stats x 2 timings; BH at q = 0.10 within each look.
6. **Looks:** at >= 1,000 pairs AND >= 30 slate dates per stat (first look), then monthly. Alpha is
   spent as in FORWARD_PREREG_2026_27 section 5 (same O'Brien-Fleming-style spend; same seed rule).
   A look before the gate is descriptive only and says so.
7. **Slices (reported, no decision weight):** volatility bucket, scoring tier (<5, 5-10, 10-15,
   15-20, 20-25, 25+ ppg), starter vs bench, line distance `|L - model mean| / model std` in thirds,
   over vs under side, home/away, and T-60 vs T-30 model rows where a T-30 row exists.

## 4. Minimum data checks (fail = the look is descriptive, not a decision)

1. Name resolution >= 95% of Pinnacle prop rows on the slate; the unresolved list is written.
2. Two-sided quotes on >= 80% of captured prop rows (a one-sided book cannot be de-vigged).
3. Capture coverage: a pre-tip Pinnacle snapshot within 90 minutes of tip for >= 90% of slate games
   (the feed is never a dependency of the daily job; missing captures are counted, not imputed).
4. Model rows: a T-60-eligible production row for >= 95% of paired player-games.

## 5. Outcomes

- Skill floor passed on a stat at closing: the strongest result we can get; promote-candidate for
  market-as-prior shrinkage weight AND for the budget tool to show model-vs-line for that stat.
- Passed at matched-time only: the model knows things the market prices in later; the lever is
  timing (T-30, late scratches); report, no EV claim.
- Failed: the market is better; the budget tool keeps answering "keep your money" for that stat and
  shows the market's number as the benchmark. This is a finding, recorded in the ledger like any other.

## 6. Threats declared in advance

- The closing comparison favours the market by construction (more information); that is the point.
- Pinnacle limits and moves lines; a stale capture between snapshots looks like an edge that could
  not have been bet. The 30-minute capture cadence is a declared limitation; "closing" means our
  last snapshot, and the EV section uses matched-time prices only.
- Selection: units require a quote, so low-interest players are under-represented; slices report it.
- Vendor reliability (archive since 2026-05-13): a feed outage drops dates, never fills them.
<!-- FROZEN-END -->

## Amendments (append only, dated)
(none)
