# BEST_PRICE_FORWARD: do production's prop picks, taken at the best pre-tip price across venues, reach ROI >= 0 after fees on the 2026-27 live log? (pre-registration)

Status: **DRAFT, revision 1, written by the modeler on Mon 2026-11-09 (revision 0 about 21:00-22:00 CST; revision 1 applies the
adversary pre-freeze review docs/reviews/best_price_forward_prefreeze_2026-11-09.md: MUSTs M1-M6 verbatim, SHOULDs S1, S3, S5, S6,
S7, S8, S10, S11, and the delegated choices D_OVER and U1). Not frozen. No ROI, hit rate or settled outcome of any pick defined here
has been computed on any 2026 row.** Branch `agent/modeler-best-price-forward`.
- **How it becomes the rule:** the main session commits `prereg(BEST_PRICE_FORWARD): <sha256>` of everything above the line
  `=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/BEST_PRICE_FORWARD.md | shasum -a 256`), with this
  status line changed to FROZEN in the same commit. Before that: Devin reads it and settles the open choices (section 13), the scorer is
  written and reproduces `reports/prereg_best_price_forward/gate.json` on the same capture, and an adversary pre-freeze review returns
  READY (BEST_PRACTICES hard stop).
- **Origin: follows advice.** Claude's recommendation to Devin, 2026-11-09: "cut the cost before improving the forecast". Evidence it
  cites: T489 arm A (one book, BetRivers) ROI -0.070 [-0.095, -0.044], n 4,546 rungs, against arm B (best price at any book) -0.0415
  [-0.0600, -0.0237], n 8,329; T491/T492: the model's selection beats price-matched peers by about +1.2 points (DK +0.012 [+0.008,
  +0.017]) while the vig is 6.5-9 points; the Kalshi taker fee is 0.07 x P x (1-P) per contract (verified, KALSHI_LONGSHOT_FORWARD freeze
  row in docs/DECISIONS.md 2026-10-23 23:01). If selection is worth about +1 point and the best price removes 3 of the 6.5-9 points, the
  gap closes but does not reverse. This rule asks the live log whether it does.
- **Season labels** are `games.season`. Only season 2026 (2026-27) is read. Season 2025 is the frozen holdout and is never loaded, by
  this rule, its gate or its scorer (`season = 2026` in every SQL statement).

## 0. What protects against what (short form)
| choice | protects against |
|---|---|
| forward-only window opening after the freeze (section 3) | peeking: the live hit rate of production's side and its Brier gap to the market on 2026-10-20..11-08 have already been seen (section 12) |
| one entry stamp per game, every price quoted at or before it, post-tip refused (section 4) | later information (closing prices, T-30 lineups, the box score) leaking into a pre-tip bet |
| pick decided against the consensus no-vig, price chosen after (section 5) | the best price choosing the bet (winner's curse on stale or erroneous quotes) |
| stale-quote filter + executability attack (sections 4, 10) | "best" prices that were never really available |
| venues as they settle: books void, Kalshi pays its fair price (section 7) | silently dropping DNPs, which flatters any venue that does not void |
| flat 1 unit, ROI over non-void stakes (section 8) | sizing choices that let a few large bets carry the result |
| game-, date- and player-clustered bootstrap, decision on the widest (section 8) | 16 picks per game sharing one game's minutes and pace; one night's news; the same player picked on the same side night after night |
| practical floor, two fixed looks, own alpha (sections 8-9) | a "profitable" claim that is 0.1 point above zero; optional stopping |
| fair comparator on the same picks and rows (section 6) | comparing best price to a book on a different set of bets |
| attacks on any PASS (section 10) | a pass from shuffled-pick luck, a leak, one hot week, or quote missingness |

## 1. Hypothesis
- **H1 (primary).** Production's prop picks (section 5) bought at the best pre-tip price across the venue universe U1 (section 2), with
  each venue's fee and settlement, return ROI >= 0 per unit staked; a PASS needs ROI >= +0.010 and the one-sided lower bound > 0.
- **Null.** ROI < 0: the vig left after line shopping is larger than the model's selection skill at the line.
- Comparators and their information: the null is the U1 consensus at E, which holds every pre-tip price and therefore at least the
  information in the model's inputs; the shuffled-pick control (10.2) holds the same rows, venues and prices with the model's side
  removed.
- **Not claimed either way:** that the model is calibrated (LINE_CAL_LIVE's question), or that any stake should be placed. A PASS is
  "candidate for a small declared experiment", nothing more; money stays Devin's decision.

## 2. Venues (universe U1) and fees
1. **Sportsbooks**: `live_book_odds` (`data/markets/market_asof.duckdb`) rows with `book` in {`draftkings`, `fanduel`, `betmgm`,
   `williamhill_us` (Caesars), `betrivers`, `fanatics`}: the-odds-api region `us`, US-licensed books. No separate fee: the margin is in
   the price. Markets `player_points`, `player_rebounds`, `player_assists`, `player_threes` (main markets only; alternates excluded).
2. **Kalshi**: `kalshi_prices` (`source = 'live'`) and `kalshi_markets` in `data/kalshi/kalshi.duckdb`, series KXNBAPTS, KXNBAREB,
   KXNBAAST, KXNBA3PT, `player_id` not NULL. A rung "k+" is the over at point k - 0.5. Over = buy YES at `yes_ask`; under = buy NO at
   `1 - yes_bid` (integer cents, as KALSHI_LONGSHOT_FORWARD 1.6). Fee per contract `0.07 x M x P x (1 - P)`, M = 1.0 for all four series,
   UNROUNDED in the primary, loaded from `configs/parlay.yaml` `fee_model` through `nba.parlay.config.FeeModel`; the scorer REFUSES to
   run unless `verified: true` and the loaded coefficient and multipliers give exactly these pinned values (the same pin and the same
   schedule-change exception as KALSHI_LONGSHOT_FORWARD section 3; a config edit for any other reason never changes this rule's fee).
3. **Captured but outside U1** (reported in descriptive arm S6, never in a decision and never in the secondary family, S2-S3): `pinnacle`, `bovada`, `betonlineag` (offshore or
   non-US; not lawful venues for a US bettor), `polymarket` (the vendor label does not say whether it is Polymarket US, fee 0.0695,
   verified in `configs/fastarb.yaml`, or polymarket.com, not tradable from the US), `novig`, `prophetx`, `betopenly` (exchanges with no
   verified fee object in the repo). A venue joins U1 only by a new rule id, never by an amendment.
4. **Net decimal odds** per venue and side: books `1 + A/100` (A > 0) or `1 + 100/|A|`; Kalshi `1 / (P + fee(P))`. The best price is the
   highest net decimal odds; tie: a sportsbook before Kalshi, then alphabetical.

## 3. Window (forward only)
Regular-season games of season 2026 whose ET slate date is strictly after the ET calendar date of the freeze commit's committer time
(read from git), through the read cutoffs in section 9. Cup knockout games (no games 2026-12-04..11 in `live_tips_2026-27.parquet`),
play-in and playoffs are out. Every captured date up to and including the freeze date is used for the gate counts only (label-free) and
never scored. Why not score the captured dates 2026-10-20..freeze, which would double the sample: outcome summaries of exactly this pick
population were seen before drafting (section 12), so choices made in this text could carry them. Nobody has computed this ROI on 2026
data; a forward-only window keeps it that way until the rule is frozen.

## 4. Entry stamp and known-at rules
1. **Entry stamp E (per game)** = the earliest `received_at` of the game's `t60` snapshot in `live_book_odds` (one vendor call per game,
   pulled 55-65 min before the real scheduled tip; the writer refuses rows at or after tip). A game with no `t60` snapshot is excluded and
   counted (`no_t60`): exclusion is by clock, never by outcome.
2. **Sportsbook quotes**: the `t60` rows of that game with `received_at <= E` only; a row labelled `t60` with `received_at > E` is
   refused and counted (`post_entry_quote`). Where a (book, side, point) has more than one admissible row, the latest `received_at`, then
   the latest `last_update`, then the worse price for the bettor is used. A planted `t60` row stamped E + 1 min with a better price must
   leave the pick and its price unchanged (unit test). A quote whose own `last_update` is more than 60 min before E is dropped
   (`book_last_update`; on the gate capture this filter never fires: median age 0.5 min, p95 1.6 min, n 41,133, so it is inert and the
   stale protection is items 4 and section 10.3).
3. **Kalshi quote**: per ticker, the latest `source = 'live'` row with `E - 20 min <= ts <= E` (15-minute capture); none: Kalshi does not
   quote that pick. Quotable iff `bid_c >= 1`, `ask_c <= 99`, `ask_c > bid_c`. Never a row after E (planted-future unit test).
4. **Stale filter**: at the reference point (section 5.3) each U1 venue's de-vigged P(over) (books: proportional two-way; Kalshi: mid)
   is compared with the median over all quoting U1 venues; a venue more than 0.07 away is dropped for that row (`consensus_dev`). With
   exactly two venues more than 0.07 apart both are dropped.
5. **Model row**: the latest `forward_predictions` row of `model_name = 'props_context_residual'` (production, whatever registry
   version was serving; versions are reported) with `made_at <= E`. Post-E rows are refused (unit test).
6. **Post-tip**: nothing stamped at or after the real tip is read for a price or a model input; outcomes (section 7) are read after the
   game only for settlement.

## 5. Pick rule
1. **Population**: (game, player, stat) with stat in pts, reb, ast, fg3m; the model row's `p_play >= 0.90` (the production panel's
   `MIN_P_PLAY`; 31% of book-quoted rows fail it on the gate capture, section 11).
2. **Model probability (primary arm CAL)**: `p_cal = CalMap.apply("pinnacle", stat, p_raw, L, q50, (q75 - q25)/1.349, starter_rate)`
   with the frozen LINE_CALIBRATION map (payload sha 443743a1, refused otherwise), `p_raw = top_source.p_over(pred, L)` (conditional on
   playing, integer grid), quartiles from the stored 19-point `q_grid`. This is the number production serves on the card and the parlay
   panel. RAW is a descriptive arm (S5), never a decision.
3. **Reference point L**: each U1 sportsbook's own main point (two-sided x.5 pair whose raw over/under probabilities are closest; tie
   lower point); L = the point that is main at the most books; tie: the point whose median de-vigged P(over) is closest to 0.5; tie: lower.
   Whole-number points are never used (no pushes). Kalshi enters only through its rung at exactly L.
4. **Consensus**: `p_cons` = median de-vigged P(over) at L over the U1 venues left after the stale filter; at least 2 venues required
   (`fewer_than_2_venues` otherwise).
5. **Side**: over iff `p_cal - p_cons >= 0.03`; under iff `p_cons - p_cal >= 0.03`; else no pick. At most one side and one point per
   (game, player, stat). The pick never looks at any venue's individual price.
6. **Price**: the best net decimal odds on the pick side at L among the U1 venues that survived the stale filter and quote that side; a
   pick needs >= 2 such venues (the question is a best price, not a price).

## 6. Arms
- **Primary (B)**: best price in U1 (section 5.6).
- **S1 single book, same picks**: DraftKings' own price on the picks where DraftKings quotes the pick side at L after the stale filter
  (90% of picks on the gate capture). Reported: ROI(DK), ROI(B) on the same rows, and the paired difference with its clustered CI.
- **S2 accounts held (Kalshi only, taker)**: the picks where Kalshi quotes L, priced at Kalshi's taker price. ROI with CI.
- **S3 Kalshi maker (paper, an ASSUMPTION about fills, no execution code)**: on picks where Kalshi quotes L, a resting order one cent
  better than the best bid on the pick side (over: `bid_c + 1`; under: NO bid `100 - ask_c + 1`), only where that stays strictly below
  the pick side's ask. **Fill assumption (conservative, stated as an assumption):** filled iff a later live snapshot with E < ts < tip
  shows the pick side's ask at or below the order price minus 1c (the market traded through us); otherwise no position (counted). Fee
  `0.0175 x P x (1-P)` (Kalshi's maker coefficient, charged even though the four prop series are plain `quadratic` and may charge makers
  nothing: conservative). Queue position, partial fills and cancellations are not modelled. The fill rule selects on later price moves
  that are correlated with news, which is the adverse selection a real maker faces; the arm is still paper and cannot justify a stake.
  Sensitivity: fill at equality (ask <= order price).
- **S5 RAW (descriptive)** and **S6 outside-U1 venues (descriptive)**: ROI with CI, no word, no family.
- **D_OVER (descriptive, pre-declared)**: the primary's over picks only, at the best U1 price, and at Kalshi's taker price where Kalshi
  quotes L; ROI with the decision CI, no word, no family (Devin's own venue allows overs only; about 28% of picks on the gate capture).
- Descriptive: Kalshi-priced picks re-costed with the fee rounded up to the cent per order at a $1 and a $10 order (configs/parlay.yaml
  rounds per order).
- Descriptive winner's-curse table: ROI by the best venue's de-vigged P(pick side) minus `p_cons` in bins (< -0.04, -0.04..-0.02,
  -0.02..0, >= 0) and by Kalshi quote age (< 10 min, >= 10 min).

## 7. Settlement and DNP (as each venue settles)
- **Books**: a player with `minutes > 0` in `player_game_stats` (season 2026) is graded on the box-score stat (`y = stat > L`); a player
  absent from `player_game_stats` or with `minutes` NULL or 0 is a VOID (stake returned, excluded from the ROI denominator, counted `n_void`).
- **Kalshi**: Kalshi's own settled result only (`yes` 1, `no` 0); `scalar` (inactive, never takes the court, postponed past 48 h) settles
  at `settlement_value_dollars` from the stored raw settled page; NO pays `1 - v`. The stake counts. A scalar with no stored value is
  excluded, counted and flagged; never guessed, never the entry mid, never the box score. Box score vs Kalshi disagreement on a
  yes/no market is a guard (section 10.8).
- **Whole-game rule**: a look scores only games whose ET date is at least 2 days before the read; a game with any Kalshi-priced pick
  unsettled at the read is excluded whole (`unsettled_game`), never pick by pick (KALSHI_LONGSHOT_FORWARD M2: YES settles at the
  threshold, NO at the whistle).

## 8. Metric, CI, floor, words
- **Stake**: flat 1 unit per pick. Profit per pick: book win `d - 1`, loss `-1`, void 0 (not staked); Kalshi `(payoff - P - fee)/(P + fee)`.
- **Metric**: `ROI = sum(profit) / sum(stake)` over the non-void picks of the look.
- **CI**: percentile bootstrap of the ratio, B = 10,000, `numpy.random.default_rng(2026)` re-seeded at each look; clusters = whole games,
  separately whole ET dates, and separately players (all of a player's picks in the look); the **decision CI is the widest** of the
  three. All three printed, with the share of total profit carried by the top five players (BEST_PRACTICES 2026-10-20: picks repeat
  the same player on the same side, 80% of a player's picks on the gate capture). Before any random draw, picks are sorted by
  (game_date, game_id, player_id, stat) and clusters are indexed in sorted key order; the gate's synthetic draws use the same order,
  so the gate and every look reproduce bit for bit. The decision CI is computed by one named function of the scorer,
  `research/eval/best_price_forward_eval.py::decision_ci` (to be written before the freeze; `nba/truth/` has no bootstrap module);
  the self-test 10.9(a) is its coverage test.
- **Floor (practical)**: +0.010 per unit staked.
- **Minimum data (per look)**: Read 1: >= 250 games, >= 35 dates, >= 2,500 non-void picks; Read 2: >= 500 games, >= 70 dates, >= 6,000
  non-void picks.
- **PASS** iff the minimum is met, `ROI >= +0.010`, the one-sided lower bound of the decision CI at the look's level > 0, every guard in
  section 10 holds and every attack in section 10 passes.
- **NO_POSITIVE_EV_FOUND** iff the minimum is met and the one-sided upper bound at the look's level < +0.010.
- **INSUFFICIENT_SAMPLE** iff the minimum is not met (counts and the projected date only; no direction claimed).
- **KEEP_SHADOWING** otherwise at Read 1; at Read 2 this, a failed attack after a nominal pass, and INSUFFICIENT_SAMPLE are recorded
  as **INCONCLUSIVE** and the rule closes. No Read 3, no extension, no re-read with later settlements.
- **Order of evaluation (exactly one word per look).** (1) `t60` coverage below 70% (10.1) or the minimum not met → INSUFFICIENT_SAMPLE.
  (2) Settlement agreement (10.8) or a self-test (10.9) fails → SELFTEST_FAILED, no direction claimed: at Read 1 the rule continues to
  Read 2; at Read 2 one code-only fix, logged in docs/DECISIONS.md before the re-run, and one re-run on the same rows are allowed, and a
  second failure is INCONCLUSIVE. (3) PASS conditions met → PASS. (4) NO_POSITIVE_EV_FOUND conditions met → NO_POSITIVE_EV_FOUND.
  (5) Otherwise KEEP_SHADOWING at Read 1 and INCONCLUSIVE at Read 2. A nominal pass that fails any guard or attack in section 10 falls
  to (5). The rule closes at Read 2 whatever the word.

## 9. Looks, alpha, family
- **Read 1 on 2027-01-15** (ET dates through 2027-01-13), one-sided 0.005 (99% two-sided interval). **Read 2 (final) on 2027-04-14**
  (through 2027-04-12), one-sided 0.02 (96%). A Read 1 PASS or NO_POSITIVE_EV_FOUND that met its own rule stands; Read 2 cannot undo it.
- **Family F_BPF = {H1}**, m = 1, total one-sided 0.025 across the two looks (Bonferroni over looks). S2-S3 form a separate secondary
  family, Read 2 only, BH at q = 0.10 over two one-sided tests (S2, S3: ROI > 0); S1's paired difference is reported with its CI but is not tested
  (best price >= DraftKings on every pick, so the difference is non-negative by construction except on Kalshi-priced DNPs); a secondary can earn
  "flagged" and never a money word.
- **Relation to KALSHI_LONGSHOT_FORWARD (0b50d17e) and UNDERS_FORWARD (149be979)**: they share a Bonferroni family at 0.005 + 0.02 each
  and test the market's calibration with no model. This rule tests model selection, mostly at sportsbooks (Kalshi is the best venue on
  13% of gate picks, quotes 50%). It overlaps them only on Kalshi under-side prop contracts. It is NOT merged into their family (their
  levels are frozen and a fourth split would starve all three); the project-wide one-sided false-pass rate across the three rules is
  therefore at most 0.075, stated here. No outcome, return or tally from either sibling's scoring window was read in drafting this rule.

## 10. Guards and attacks (run at every look; any PASS must survive all)
1. **Capture**: if fewer than 80% of in-window scheduled games have a `t60` stamp, or fewer than 60% of picks' games have any Kalshi
   snapshot within 20 min of E, the look reports the gap by date; below 70% `t60` coverage the look is INSUFFICIENT_SAMPLE (capture).
2. **Shuffled picks**: 1,000 relabelings flipping each pick's side with probability 0.5 (same rows, same venues, best price on the
   flipped side). PASS needs the observed ROI above the 95th percentile of the shuffled ROIs.
3. **Winner's curse / stale quotes**: (a) ROI at the second-best price must be >= 0 (point); (b) ROI excluding picks whose best net
   decimal exceeds the second-best by more than 0.10 must be >= 0 (point); (c) executability: ROI restricted to picks whose best venue
   quotes the same side and point at an equal or better price at its next snapshot before tip (books: the game's `t45`; Kalshi: the next
   15-minute row) must be >= 0 (point), and the share of picks that fail (c) is reported.
4. **Planted future**: the scorer is run on the look's rows with every model row replaced by one stamped E + 1 min and every price by
   the `close` snapshot: it must refuse them all (zero picks). And a planted-outcome feature (the settled y written into `p_cal`) must
   produce a self-test ROI of at least +0.30, proving the pipeline can see an edge.
5. **Leave out the best week**: ROI without the ISO week with the highest ROI must be >= 0 (point).
6. **Quote missingness**: ROI on picks with >= 3 venues must be >= 0 (point); report ROI by venue count, by whether a venue that quoted
   the player at `t90` is missing at `t60` (a pulled prop is news), and by Kalshi present or absent.
7. **Leave one venue out**: ROI with each U1 venue removed in turn (re-choosing the best price) stays >= 0 (point).
8. **Settlement agreement**: Kalshi yes/no results disagreeing with the box-score grade on more than 1% of Kalshi-priced picks gives
   SELFTEST_FAILED under section 8's order.
9. **Self-tests** (printed; a failure gives SELFTEST_FAILED under section 8's order; the rule text never changes): (a) K = 1,000
   synthetic outcome sets drawn from `p_cons` (market right) on the look's own picks, rows and clustering, with outcomes correlated
   within (game, player): latent `z = sqrt(0.3) u_(game,player) + sqrt(0.7) e`, `over = 1{Phi(z) < p_cons}`, the pick wins iff its side
   matches; plus a player-persistent shift `N(0, 0.03)` added to every `p_cons` of that player in the set (clipped to [0.01, 0.99]), the
   known true ROI of a set being its expectation under the shifted probabilities. The decision-CI one-sided lower bound exceeds the
   known true ROI in at most 1.5% of sets at Read 1 (nominal 0.5%) and at most 3.5% at Read 2 (nominal 2%), and the one-sided upper
   bound falls below it in at most the same shares. A CI that is too wide is not a failure. `numpy.random.default_rng(2027)`.
   (b) planted edge: outcomes drawn from `p_cons + 0.5 (p_cal - p_cons)`, with the same within-(game, player) correlation: the point
   estimate lands within 0.005 of the known true ROI on average over the 1,000 sets and the CI covers it in >= 90% of sets.
10. **Concentration**: no game > 2% and no date > 6% of picks unless ROI without it is reported; PASS also needs ROI > 0 (point) leaving
    out each stat in turn, and ROI > 0 (point) leaving out the five players with the most picks.

## 11. Minimum-data gate (non-negotiable 8), measured on real counts only
Script `research/eval/best_price_forward_gate.py`, output `reports/prereg_best_price_forward/gate.json`; data_version ec40abfa83e5;
capture through `live_book_odds` received_at 2026-11-09 01:59:51 UTC and `kalshi_prices` ts 2026-11-10 03:04 UTC; dates 2026-10-20..11-08.
Label-free: no `forward_scores`, no box-score stat, no Kalshi result is read. Run in about 10 s. Revision 1 code: book quotes filtered
`received_at <= E` with the deterministic duplicate choice (M2), picks sorted before any draw (M6), JSON keys sorted; two runs give a
byte-identical gate.json (sha256 8d26fc85...).

| item | measured | note |
|---|---|---|
| season-2026 games scheduled in the counted window | 146 on 19 dates (no games 11-03) | |
| games with a `t60` stamp (E) | 135 (92.5%); since 2026-10-24: 100% | the 11 misses: 2026-10-20..23 (seven on 10-23, the Mac asleep) |
| production rows at or before E | 18,508 (game, player, stat) | whole rosters |
| rows failing `p_play >= 0.90` | 11,524 of 18,508; among rows a book quotes, 2,132 of 6,903 (31%) | open choice 4 |
| rows with no two-sided x.5 US-book quote | 2,480 | mostly players with no prop |
| rows with >= 2 U1 venues at L after the stale filter | 4,389 (venue-count histogram 0:11, 1:104, 2:206, 3:603, 4:1,072, 5:1,255, 6:1,108, 7:145) | |
| stale filter drops | 28 venue-rows (Kalshi 15, DraftKings 8, BetRivers 4, BetMGM 1) | |
| book `t60` rows refused as post-entry (M2) | 0 (one `t60` pull per game, all at E) | the filter is a guard against a retry or relabel |
| **CAL picks (delta 0.03) with >= 2 venues on the pick side** | **2,129 picks, 135 games, 19 dates; 15.8 per game** | fg3m 570, reb 636, ast 574, pts 349; under 1,527, over 602 |
| venues per pick | 2: 119, 3: 305, 4: 495, 5: 591, 6: 542, 7: 77 | 94% have >= 3 |
| venue coverage of picks | DraftKings 90.1%, BetRivers 83.0%, FanDuel 81.2%, Caesars 76.6%, BetMGM 73.6%, Kalshi 50.4%, Fanatics 9.2% | |
| best venue | DraftKings 492, FanDuel 467, BetMGM 423, BetRivers 327, Kalshi 272, Caesars 128, Fanatics 20 | no venue dominates |
| Kalshi rung at L | quotes 1,072 picks; maker order possible on 415 | S2, S3 base |
| Kalshi snapshot age at E | median 9.4 min, p95 15.0 (n 2,068) | |
| best vs DraftKings price, same picks | mean net-odds gain +3.3%; best strictly better on 72.5% (n 1,919) | a price fact, not a return |
| RAW arm (descriptive) | 3,407 picks; 80.1% of CAL picks share RAW's side | |
| players (M4) | 185 players; median 11 picks per player in 19 dates; players with >= 5 picks take one side 80.2% of the time; 80.8% of picks sit on a (game, player) with >= 2 picks; top five players 6.0% of picks | |
| D_OVER | 602 over picks, 126 games | descriptive only |
| best minus second-best net decimal | median 0.034, p95 0.130; 10.9% of picks above 0.10 | guard 10.3(b) |
| expected ROI at the second-best price if consensus true | -0.054 (best: -0.032) | guard 10.3(a), S7 |
| outside-U1 coverage at L (rows) | Novig 4,429, ProphetX 4,313, Bovada 4,171, BetOnline 4,060, Pinnacle 2,797, Polymarket 2,141, BetOpenly 22 | S6 / open choice 2 |

**Label-free expectations (declared truths, not outcomes).** If the consensus no-vig is the truth, the CAL picks at the best price return
-0.032 per unit (synthetic, n 2,129; this is the vig left after line shopping, against about -0.045 to -0.07 at one book). If CAL were
the truth, +0.096; that number is the model's claimed edge and is not believed (the live model is worse than the market at the line,
section 12).

**Power** (revision 1, M4 and M1 model: 200 synthetic seasons per row, built by drawing whole gate nights with replacement until the
game count; players keep their identity, so a player's picks accumulate as over a season; outcomes correlated 0.3 within (game,
player) and a player shift N(0, 0.03); SE = widest of game, date and player clustering; `power_cal_arm` in gate.json):
| games | picks | median picks per player | SE game / date / player | widest | 99% half-width | 96% half-width | P(NPEF) at Read 1 level if market right | at Read 2 level |
|---|---|---|---|---|---|---|---|---|
| 259 | 4,103 | 21 | 0.0172 / 0.0167 / 0.0175 | 0.0183 | 0.047 | 0.038 | 0.40 | 0.63 |
| 333 | 5,279 | 29 | 0.0151 / 0.0146 / 0.0155 | 0.0160 | 0.041 | 0.033 | 0.52 | 0.71 |
| 415 | 6,597 | 35 | 0.0136 / 0.0135 / 0.0141 | 0.0146 | 0.038 | 0.030 | 0.67 | 0.78 |
| 507 | 8,145 | 42 | 0.0121 / 0.0120 / 0.0127 | 0.0130 | 0.033 | 0.027 | 0.75 | 0.89 |
| 832 | 13,091 | 68 | 0.0097 / 0.0096 / 0.0102 | 0.0104 | 0.027 | 0.021 | 0.95 | 0.99 |
| 1,046 | 16,222 | 83 | 0.0087 / 0.0086 / 0.0093 | 0.0094 | 0.024 | 0.019 | 0.97 | 0.99 |
Player clustering is the widest in 45-79% of sets, more as the season accumulates picks per player. Read 1 (one-sided 0.005) gives
P(NO_POSITIVE_EV_FOUND | market right) of about 0.5 at 331 games (80% capture) and 0.67 at 414 (full capture); Read 2 (0.02) about
0.9-0.99. Under a half-edge truth (`p_cons + 0.5 (p_cal - p_cons)`, expected best-price ROI +0.031) the CI-and-floor part of PASS is
met in 0.32 of sets at Read 1 (414 games) and 0.84 at Read 2 (832 games), before the section-10 guards. Guard 10.3(a) needs ROI >= 0
at the second-best price, about 2.1 points below the best on the gate capture (label-free), so a PASS effectively needs a best-price
ROI of about +0.02 to +0.03 beyond the floor arithmetic. A true ROI of +0.01 is not separable from 0: the rule can say "no" with good
power and "yes" only to a large edge. That asymmetry is accepted: the question Devin needs answered is whether to keep his money.
Normal-approximation check of the widest SE on the same sets: the one-sided lower bound exceeds the truth in 0-2% of sets at the
Read 1 level and 0.5-4.5% at the Read 2 level (200 sets per row, so about +-1.4 points of noise; mean about 2%, nominal). Caveats: the
synthetic season reuses the gate's 185 players (a real season adds players, so per-player counts and the player SE are somewhat
pessimistic) and treats each drawn night as a new date (about 61 dates per 414 games against 54 scheduled, so the date SE is slightly
optimistic).

**Projection** (`live_tips_2026-27.parquet`, Cup gap removed, window opening 2026-11-11):
| look | scheduled games / dates | at 100% capture | at 80% | at 60% |
|---|---|---|---|---|
| Read 1 (through 2027-01-13) | 414 / 54 | 414 g, 54 d, ~6,500 picks: PASSES | 331 g, 43 d: PASSES | 248 g, 32 d: FAILS (games and dates) |
| Read 2 (through 2027-04-12) | 1,040 / 135 | ~16,400 picks: PASSES | 832 g, 108 d: PASSES | 624 g, 81 d: PASSES |
Measured capture since 2026-10-24 is 100% of games. If the freeze slips, Read 1 shrinks: window from 2026-11-20 gives 348 games / 45
dates scheduled, from 2026-12-01 271 / 35 (the Read 1 date minimum is then met only at 100% capture). **The gate passes on counts if
the window opens by about 2026-11-20 and capture stays >= 80%; Read 2 passes in every scenario measured.**

**Capture audit (non-negotiable 8).** Every season held was considered: only season 2026 has live multi-venue pre-tip prop prices with
Kalshi; `data/odds/odds_history.duckdb` has 2023-25 sportsbook history (no Kalshi; T489-T492 already used 2023-24; 2025 is the
holdout). Repairable gaps, with cost: (a) the 2026-10-23 dark night (fixed: watchdog and heartbeat since); (b) Novig, ProphetX,
Polymarket and BetOpenly lack verified fee objects (about 10 minutes each for Devin to read each venue's published fee page; adding
them is a new rule id); (c) Kalshi's scalar `settlement_value_dollars` is not a column in `kalshi_markets` (read from raw pages, as
the siblings do; a column is a small `nba/kalshi` change); (d) no alternate-line markets are used (they would add Kalshi matches at
5-point rungs, at higher vig; a separate question).

## 12. What was seen before drafting (disclosure)
- The live scorecard's hit rate of production's side (about 54% vs about 60% stated on 2026-10-20..28) and LINE_CAL_LIVE's label-free
  draft numbers (raw P(over) 0.413 vs Pinnacle 0.485; leans under 78%): known from LINE_CAL_LIVE.md before this draft. They pushed the
  primary toward CAL (the served, recalibrated number). CAL's live outcome is unknown to the author.
- **Incidental, 2026-11-09 ~21:10 CST:** running a scratch counting script from a shared scratch directory imported another session's
  `inspect.py`, which printed JARVIS paper-book state: record 36 won / 92 lost / 2 void (n 128 settled), CLV -0.88c [-1.43, -0.52]
  (n 83, 12 dates), and the forward scorecard's model-minus-market Brier +0.0095 [+0.0064, +0.0125], n 6,812 legs, 133 games
  (scorecard 2026-11-07). These are outcome summaries on the captured dates. They lowered the author's prior; they did not set any
  parameter here (delta, floor, venues, entry and arms were fixed before the gate ran and before this print). Fixed since: scratch
  scripts now run from a private directory with `python -I` / `-P`.
- While reading the Kalshi schema, one preseason row (2026-10-05, Jaylen Brown 20+/25+ points) printed with its `result` column.
  Preseason, outside every window.
- One definition changed after the first gate run, for a code reason only: the stale filter compared each venue with the median of the
  OTHER venues, which let one outlier drop a good quote (a unit test caught it); it now uses the median of all venues (section 4.4).
  Pick count before and after: 2,129 both times (stale drops 31 -> 28). No outcome existed for either version.
- Revision 1 (after the adversary review): no outcome was read by the author or, per its own record, by the reviewer. The power model
  and the gate code changed; the pick definition did not (2,129 picks before and after).
- Gate quantities: counts, venue coverage, best-venue counts, price gains vs DraftKings, and label-free expected ROI under declared
  truths. No settled outcome joined to any pick.

## 13. Open choices for Devin (default in bold)
1. **Model arm: CAL primary, RAW descriptive.** Alternative: RAW primary (the registry model's own number; it leans under 80% and
   overstates its side, so its picks are more numerous, 3,407 vs 2,129, and expected worse).
2. **Venues: six US-licensed books + Kalshi.** SETTLED 2026-11-09 (Devin delegated: proceed without the morning review): U1 as
   written, Polymarket out (the vendor label cannot be tied to Polymarket US). Overs-only slice D_OVER declared (section 6). Which venues can you actually use? If only Kalshi and Polymarket (your stated accounts),
   S2 is the arm that matters for money and the primary is an upper bound on what you could get. Adding Novig / ProphetX / Polymarket
   needs their fee pages verified and a new rule id.
3. **Pick threshold delta 0.03** against the consensus no-vig. 0.0 is "every lean"; 0.05 gives fewer picks (count not measured).
4. **p_play >= 0.90** (the panel's rule). Dropping it adds about 45% more rows, mostly questionable players, and more voids.
5. **Entry at T-60** (the `t60` snapshot). T-30 would use more information (lineups) but production's prop rows are not re-stamped then
   and Kalshi coverage is the same; a T-30 arm is a new rule.
6. **Floor +0.010; looks 2027-01-15 and 2027-04-14 at 0.005 / 0.02; own family** (not merged with LONGSHOT/UNDERS).
7. **Single-book comparator DraftKings** (widest coverage, the card's book). BetRivers would match T489.
8. **Maker fill rule: trade-through by 1c**; equality is the sensitivity.

## 14. Playbook check (docs/MODEL_PLAYBOOK.md)
Model class: no new model. A betting-strategy evaluation of the production props model (gradient-boosted context residual over a
recency average, quantile layer) plus the frozen LINE_CALIBRATION quantile/distribution layer, scored as money at the line.
1. **Information or capacity?** Neither is added to the forecast. The lever is cost: the same forecast, a better price. The only new
   pre-tip information used is other venues' prices, and only for execution, never for the pick (section 5.5).
2. **Enough data for the class?** Nothing is fitted. The gate (section 11) shows ~16 picks per game, power to say "no" by Read 1-2,
   and power to say "yes" only to an edge of about +3-4 points.
3. **Was the test valid?** Real tip (the vendor call is refused at tip; E is a stored stamp), model and prices at or before E with
   planted-future unit tests, integer support native (x.5 lines on integer stats; whole-number lines excluded), DNP handled as each
   venue settles, fee pinned and verified, game, date and player clustering (widest decides), calibrated-null and planted-edge self-tests, shuffled picks. Known
   weaknesses: the `last_update` stale filter is inert (vendor stamps are fresh), so stale protection rests on the consensus filter and
   the executability attack; the-odds-api's US book prices may be one state's; the maker arm's fill rule is an assumption; CAL is itself
   under test (LINE_CAL_LIVE), so a CAL failure there would explain a loss here.
4. **Lineage.** T489 (one book vs best book, 2023-24, model at 70%+ on alternates), T491/T492 (selection +1.2 points against
   price-matched peers), ODDS_HISTORY T203-T237 (market better on every props cell), MARKET_ANCHORED_PROPS (WOUNDED, keep your money),
   LINE_CAL_LIVE (frozen, the calibration of the same probability). Guards carried: whole-game settlement exclusion and pinned fee from
   KALSHI_LONGSHOT_FORWARD; real-tip gating; game clustering; per-player clustering and player-shared noise in power (BEST_PRACTICES
   2026-10-20, added in revision 1). Dropped: none.
5. **What does it score against?** Realised money per unit at executable prices after fees, not CRPS or log loss. A Brier edge at the
   line is a different question (LINE_CAL_LIVE, MARKET_ANCHORED_PROPS); the live Brier gap to the market (+0.0095) says the money answer
   is most likely no.

## 15. Priors (also in docs/PREDICTIONS_LEDGER.md)
- Claude (2026-11-09, stated in the request): P(PASS) 0.08; expected best-price ROI about -0.02 to -0.04.
- Modeler (revision 0, unchanged in revision 1; after the gate and the section-12 disclosures, before any outcome of these picks): P(PASS at either look) 0.04;
  P(NO_POSITIVE_EV_FOUND by Read 2) 0.80; P(INCONCLUSIVE at Read 2) 0.14; P(INSUFFICIENT_SAMPLE at Read 1) 0.12; expected pooled ROI at
  Read 2 -0.035 (80% interval -0.065 to -0.005); S1 expected ROI gain of best over DraftKings on the same picks +0.025 (80% +0.012 to
  +0.035); S2 Kalshi-only taker ROI -0.05 (80% -0.10 to 0.00); S3 maker fill rate 0.35 (80% 0.15 to 0.55) and maker ROI -0.03
  (80% -0.10 to +0.04). Reason the PASS prior is half of Claude's: the live model is measurably worse than the market at the line
  (Brier +0.0095 [+0.0064, +0.0125], 133 games, seen incidentally), the picks lean 72% under where production is known to overstate, and
  the best price still leaves about 3.2 points of vig if the market is right.

=== RESULTS BELOW ===

(none; the rule is not frozen)
