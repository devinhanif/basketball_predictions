# SPREAD_TAIL_NO: is buying NO on the tail of a Kalshi spread ladder ("Team X wins by over N.5") profitable after fees? (pre-registration)

Status: **DRAFT, not frozen.** Drafted 2026-10-23 (about 22:30 CDT) by the market agent on branch `agent/market-spread-tail-no`
for adversary review, then Devin's freeze. Everything above the line `=== RESULTS BELOW ===` is the rule. No outcome has been read:
no score, no realised margin, no `result` value. The drafter read (i) `odds_history` main `spreads` lines at `t60` (prices only), (ii)
Kalshi quotes at T-60 (prices only), (iii) the season-2026 `games` rows for `game_id, game_date, home_team, away_team` only (to map
Kalshi events to tip times), (iv) COUNTS of settled Kalshi markets, never their `result`. `season 2025` is never loaded.

- **Advice or momentum:** Devin's idea (2026-10-23 ~21:40 CDT: "scan games by threshold and say the spread NO"). No earlier
  recommendation of ours covers it. Claude's stated prior is that there is no edge (Priors below); it is drafted because the question is
  cheap to settle and a clean "no" is a result we are proud of. Not momentum.
- **Ledger check (main session):** no prior spread-tail test. Closest: T494 ALT_RUNG_CAL (prop rungs), T511 FAV_DIP_PAPER (forward,
  game winner), T474 (minutes EDA). KALSHI_VS_PINNACLE found Kalshi props within 2-3c of Pinnacle, which is the reason for the low prior.
- **Model class:** none fitted. No Elo, no props model, no stored forecast. The only inputs are prices and final scores. The
  playbook check is therefore "calibration of a price" (market-implied probability against realised frequency), not model skill.
- **What it protects against:** a bias story that sounds right (favourite-longshot bias, starters pulled in blowouts) being traded
  on without a count. Both mechanisms are about the REALISED tail being thinner than the PRICED tail; the test is whether the gap
  exceeds the fee plus spread, with a CI that respects one margin driving a whole ladder.

## The honest shape of the question
Buying NO at ask `q = 1 - yes_bid` on a leg with YES price `y` earns `1 - q - fee` if the margin stays under the line. With a 1c
Kalshi spread and the config fee, a leg priced at `y` in 5-25c costs about 1.5c of overpricing just to break even (ROI about -1.5%
at zero bias; simulated, power section). The practical floor of +2% therefore needs the YES tail overpriced by about 3c on
average. This is a large bias for a market that Kalshi props already track to within 2-3c. The rule exists to find out.

## Data audit (non-negotiable 8): what we hold, what the rule needs, what is repairable and at what cost
**Part A needs alternate-spread ladders. We do not hold them.**
- `data/odds/odds_history.duckdb`, table `odds_history`, markets present for games: `h2h*`, `spreads` (482,774 rows), `spreads_q1..q4`,
  `spreads_h1/h2`, `totals*`, `team_totals_*`. **Zero rows** for any `alternate_spreads*` market. The `alt` phase
  (`configs/odds_history.yaml: alt_markets`) pulled the 11 PLAYER `*_alternate` markets only. (The brief said "alt phase"; the
  game-level alternate ladder was never pulled. Corrected here.)
- The main `spreads` market gives each book ONE line per game (about 20 books at `t60`, season 2023: 1,318 games; 2024: 1,315; median
  |spread| 5.5 and 6.5; max 19.5 and 23.0). That is a centre, not a tail; the 5-25c tail lies 6-25 points out. No tail can be read.
- **Repair (a), recommended: a one-snapshot `alternate_spreads` pull**, `t60` only, seasons 2023 and 2024 only (2025 is refused in
  code), the-odds-api.com v4 (NOT theoddsapi.com), the existing paid plan. Cost: 1 market x 2 regions (`us`, `eu`) x 10 credits + 1
  events call = 21 credits per game x 2,638 games = **55,398 credits upper bound**; the player alt pull realised 83% of its bound
  (484,980 of 583k), so expect about 46,000. `us`-only halves it but loses Pinnacle (eu). Server balance at the last alt ledger line was
  1,810,449 (2026-10-14); a pull is running now and the balance must be re-read first. **Not run; Devin's spend decision.** The code
  needs a small `nba/odds` change (a game-level alt phase with its own ledger and cap; `alt_markets` must NOT be extended, that would
  re-buy the player pull). Written and tested on fixtures after the freeze; the exact command will be given then. The vendor docs
  (https://the-odds-api.com/liveapi/guides/v4/) say additional markets are available historically after 2023-05-03; NBA
  `alternate_spreads` coverage and whether Pinnacle posts it are UNVERIFIED until a one-day probe (about 40 credits).
- Repair (b), Kalshi `/historical` for `KXNBASPREAD`: the settled-market history that exists is 2025-26 = **season 2025, the frozen
  holdout**; reading it needs a logged holdout row (Devin). Whether KXNBASPREAD existed for 2023-24 or 2024-25 is unknown (a public
  docs question, not answered by search). Not used; listed as an option, not a plan.
- Repair (c), other vendors' alternate-spread history (SBR-style archives): none held, none costed, none reasonable to add now.
- Repair (d), model-based tail from the main line + a fitted margin distribution: rejected. That tests a model's tail, not the
  market's tail price, and the market-implied tail is the thing being claimed overpriced.
- **If (a) is not approved, Part A cannot run and the rule is Part B only** (forward, slower, smaller). That is a legitimate frozen
  state; the data gate below then reads "Part A: not run, data absent".

**Part B data (held):** `data/raw/live_<date>/kalshi/KXNBASPREAD_p0/*.json.gz` (about one snapshot per minute; carries `yes_bid`,
`yes_ask`, sizes, `floor_strike`) for slate dates 2026-10-21 and 2026-10-22; `data/kalshi/kalshi.duckdb` (`kalshi_prices` `source='live'`,
about 15-minute cadence, from 2026-10-16; `kalshi_markets.result` after settlement). **Two capture findings (ops, not rule):** the raw
live capture ended 2026-10-23T05:30Z (heartbeat `status: done`) and **no `live_2026-10-23` directory exists although it is 02:41Z on
2026-10-24**: the 10-23 slate has no raw minute capture (only the 15-minute duckdb snapshots, which also have afternoon gaps).
Coverage is therefore a skip count in this rule, not an assumption.

## Part A: historical, select 2023, report 2024 (needs repair (a))
### Object (one row = one Kalshi-grid leg)
- **Games:** regular season only (`game_id` starts `002`), seasons 2023 and 2024. `season <= 2024` asserted before any read.
- **Ladder rung:** for each book, game, and half-point `point`, the alternate-spread pair (team T at `-(N+0.5)`, opponent at `+(N+0.5)`),
  both sides posted by the same book in the same `t60` snapshot (duplicates: the last listing, as in ALT_LADDER_HIST). One pair gives
  one leg: "T wins by over N+0.5", `N >= 1`. Integer points (pushes) and one-sided lines are dropped and counted.
- **De-vig (declared, primary = power method):** raw implied `r1, r2` from American odds; find `k > 0` with `r1^k + r2^k = 1`
  (bisection); the leg's probability is `r_T^k`. The power method removes MORE probability from the long-shot side than proportional
  scaling, so it is the HARDER test of a long-shot-bias story and the conservative primary. Sensitivity S1: proportional
  `r_T/(r1+r2)`. The gap between S1 and primary is reported: it is the part of any "edge" that is only how the vig was allocated.
- **Market-implied YES `y` (declared):** Pinnacle's power-devigged value when Pinnacle posts the pair; otherwise the MEDIAN of the
  power-devigged values over all books posting the pair, requiring at least 3 books; otherwise the leg is dropped and counted. The
  Pinnacle share of legs is reported. Sensitivity S2: consensus-median only. S3: DraftKings only (the retail book Devin's peers use).
- **Ladder integrity (price-only, fixed now):** a (book, game) ladder is excluded when its devigged survival is non-monotone in the
  line by more than 0.03 anywhere, or when its rung nearest 0.5 lies more than 1.5 points from that book's own main-spread line at the
  same snapshot. Counted per book and season; S4 adds them back.
- **Realised:** `YES = 1{ team_pts - opp_pts > N + 0.5 }` from the stored final scores (overtime included, as Kalshi settles on the
  final score). `y` and `YES` are computed in separate stages; stage 0 (below) is label-free and must pass before any score is joined.

### Kalshi-like price and fee (declared; nothing here is fitted to outcomes)
- Kalshi quote at the leg: `yes_bid = y - 0.005`, `yes_ask = y + 0.005` (a 1c width: the median observed width at T-60 is 0.01,
  mean 0.011, 90th percentile 0.01-0.02, n = 363 legs, 11 games). NO ask `q = 1 - yes_bid = 1 - y + 0.005`, clipped to [0.01, 0.99].
  Sensitivity S5: width 0.02 and width 0.00. Kalshi's price is ASSUMED to track the book consensus; Part A cannot show it does (Part B can).
- **Fee: the verified config object only.** Read from `configs/parlay.yaml: fee_model` (`kalshi_fee_schedule`, `p_one_minus_p`,
  taker `coefficient`, `fee_multiplier` for the longest matching series prefix, `KXNBASPREAD` unlisted so `default_multiplier`)
  as of this rule's freeze commit. Per contract `fee = coefficient * mult * q * (1 - q)`; the rule computes it through
  `nba.parlay` fee code, never a literal. Rounding: one contract unrounded; sensitivity S6: a 100-contract order with the
  ceil-to-cent rule applied once. The maker coefficient in the same object is reported as an informational alternative only (maker
  fills carry fill risk that a price series cannot show).
- Per-leg result for NO: `profit = 1{YES = 0} - q - fee`; `cost = q + fee`. One contract per leg. No stake, no sizing.

### Estimands
- **E1 (decision): band ROI** = `sum(profit) / sum(cost)` over the legs in the band (ratio estimator, one contract each).
- **E2 (supporting): gap** = mean(`y` - `YES`) over the band's legs (positive = YES overpriced). **E3: contracts-weighted profit
  in cents per contract** (descriptive).
- **Bands (the declared family, fixed): B1 y in [0.05, 0.10), B2 [0.10, 0.15), B3 [0.15, 0.25).** Boundaries are inclusive below,
  exclusive above, on `y`. The pooled [0.05, 0.25) band is reported, descriptive, not in the family. **Placebo band: y in [0.35, 0.65).**
- **Clustering and effective n.** One game contributes many nested thresholds (one margin decides them all): rows are NEVER treated as
  independent. CI = percentile bootstrap resampling GAMES (`default_rng(20261023)`, B = 5,000, one resample matrix per season shared by
  every band and slice) and, separately, resampling DATES (`default_rng(20261024)`, B = 5,000); decision interval = union
  (lower = min, upper = max); BH p = max of the two one-sided bootstrap p-values for `ROI <= 0` with the +1 correction. Reported
  beside every cell: legs, games, dates, mean legs per game, and `n_eff = N * v_indep / v_cluster` (independent-leg variance of the
  ratio over cluster-robust variance). Conservative half-width = every row of a game perfectly correlated:
  `1.96 * sd_leg * sqrt(sum_g n_g^2) / N`.

### Select 2023, report 2024
1. **Stage 0 (label-free, below):** the data gate on the pulled ladders. Fails closed.
2. **Select (2023 only):** for each of B1-B3 compute E1. A band ADVANCES iff its 2023 E1 point estimate is >= +0.01 (half the floor).
   Not CI-based by design: select is a filter, report is the test. The family stays m = 3 for BH whether or not bands advance
   (not shrunk). If no band advances, the rule ends: **NULL (select)**; 2024 outcomes are not joined, and 2023's E1 / E2 per band are
   published with their intervals.
3. **Report (2024 only, advancing bands only), read once.**

### Decision words (report season 2024, per advancing band)
- **PASS** iff ALL of: E1 >= +0.02 AND lower end of the union interval (95%) > 0; BH q < 0.05 over the m = 3 family; E2 lower end > 0;
  E1 point > 0 in 2023 (the select sign); the placebo method check holds (below).
- **NULL** iff the union interval's upper end < +0.02 (the floor is out of reach: "no edge of the size that pays").
- **INCONCLUSIVE** otherwise. A PASS is read as "the SPORTSBOOK tail, priced at Kalshi-like quotes and the config fee, was overpriced by
  more than the cost, at about T-64"; it makes the band a candidate for Part B's forward log, never a bet (non-negotiable 6).
- **Placebo / method check (decision-relevant):** the same E2 and E1 on y in [0.35, 0.65). The check HOLDS iff the placebo E2 interval
  lies within +-0.02 AND (band E2 minus placebo E2) has lower end > 0 (clustered, joint resample). If it does not hold, a PASS is
  downgraded to **PASS-METHOD-SUSPECT** and is not counted as evidence: a gap that also appears at the money is the de-vig or the
  timing, not a long-shot bias. (Expected placebo E2 is slightly NEGATIVE if tails are overpriced, since probability is conserved.)

### Slices (descriptive, no words, not in BH; n and interval on each)
(i) ladder side: favourite's ladder vs underdog's ladder (favourite = team with the negative main-spread consensus); and home-favourite
vs away-favourite games; (ii) |main-spread consensus| in <4.5, 4.5-8.5, >=9 (fixed cuts); (iii) back-to-back: the leg's favourite
played the previous calendar date (from the schedule, no scores); (iv) late season: `game_date` on or after 1 March of the season's
second year. A slice with fewer than 300 legs or 60 games is shown as "insufficient".

### Attacks (only if any band is PASS, before anyone believes it)
(i) shuffle `YES` across games of the same season with the ladder structure intact, 50 seeds: ROI must collapse to the E1 null;
(ii) planted future: swap each game's ladder with another game of similar |spread|; (iii) leave-out: drop the best 5% of games and
the best month; (iv) S1-S6 and the ladder-integrity add-back S4 all keep the sign; (v) replicate with DraftKings-only (S3) and
Pinnacle-only subsets; (vi) 2025 is NOT used as a replication (holdout), the forward Part B is the replication.

### Stage 0: minimum-data gate on the PULLED data (non-negotiable 8; label-free, run before any score join)
Per season and per band: **>= 300 games, >= 2,000 legs, conservative ROI half-width <= 0.04**, and in each season >= 70% of games
with >= 6 in-band legs under the primary construction; Pinnacle share, books per leg, dropped counts reported. Failure after the audit
above ends Part A as "gate failed, data". The thresholds were set from the simulated counts below (so they are not a surprise).

## Part B: forward Kalshi, paper only
### Object
- Series `KXNBASPREAD` exactly (both teams' ladders; `floor_strike` N+0.5). Regular-season 2026-27 games only (`game_id` starts `00226`;
  preseason excluded). **Scoring starts with the first game whose T-60 snapshot is after the freeze commit's timestamp** (the
  2026-10-20..23 captures are used for the label-free gate only; none is scored: the idea was formed on 2026-10-23).
- **Snapshot (declared):** the latest quote at or before tip - 60 min and no more than 15 min earlier (`data/raw/live_*` minute capture
  preferred; `kalshi_prices` `source='live'` as fallback; the source and age are logged). Tip from `data/schedule/live_tips_2026-27.parquet`
  (UTC). A game with no quote in the window is SKIPPED AND COUNTED (coverage is reported, never filled).
- **Leg:** a quote with `0 < yes_bid`, `yes_ask < 1`, `ask - bid <= 0.06`, and YES mid in a band (same bands B1-B3, placebo
  [0.35, 0.65)). Mid is taken on the leg's own ticker, never `1 - the other team's`.
- **Paper entry:** one contract, NO at `q = 1 - yes_bid` (the NO ask), fee = the same `configs/parlay.yaml` fee object as at the freeze
  commit (later config changes do not apply), settled by Kalshi's own `result` on the official final score; a postponed game or
  voided market is dropped and counted. Paper only, in its own store (not `kalshi.duckdb`, not `nba.duckdb`); no stake, no sizing.
- **Statistics:** E1, E2, `n_eff` and the conservative half-width exactly as in Part A, game- and date-clustered union interval,
  but the family is **m = 3 bands at each look**.

### Looks and words (declared)
- **Look 1: the first date by which the scored window holds >= 150 in-band legs AND >= 14 distinct dates AND >= 40 games.** Level 99%
  (alpha 0.01 spent). Words as Part A. **Honest reading: look 1 can almost never say NULL or PASS** (simulated SE of pooled ROI about 3%
  at 40 games; below). It is a pipeline, coverage, fill-rate and spread check and a descriptive E2 with its interval.
- **Look 2: >= 120 games AND >= 40 dates**, level 95% (alpha 0.04 spent; total 0.05). This is the decision look.
- Independent of Part A's outcome (Part A can fail on data; Part B is the real venue). If Part A PASSES, Part B is the replication and
  the bands that passed carry a pre-declared sign expectation; if Part A is NULL, Part B still runs as a log (it costs nothing).
- Nothing is promoted by Part B; going live is a separate frozen rule and Devin's decision (this project places no orders).

## Minimum-data gate, measured now on the real data (label-free)
**Part A (SIMULATED counts; real counts need repair (a)).** Centre = the real main-spread consensus (median |spread| over about 20
books, `t60`, 2023: 1,318 games, 2024: 1,315), margin sd 13.4 fitted to the Kalshi T-60 ladder prices (22 team-ladders; range 12.7-14.5),
every integer-step half-point rung up to a posted-ladder cap on each team's ladder:
| Cap on |line| | Season | B1 5-10c legs / games | B2 10-15c | B3 15-25c | Placebo 35-65c |
|---|---|---|---|---|---|
| 25.5 | 2023 | 10,115 / 1,318 | 8,283 / 1,290 | 10,433 / 1,297 | 12,652 / 1,318 |
| 25.5 | 2024 | 9,732 / 1,313 | 8,139 / 1,274 | 10,153 / 1,282 | 12,810 / 1,315 |
| 15.5 | 2023 | 4,538 / 1,187 | 4,588 / 1,290 | 7,075 / 1,184 | 12,113 / 1,318 |
| 15.5 | 2024 | 4,812 / 1,210 | 4,528 / 1,274 | 6,554 / 1,155 | 12,148 / 1,313 |
Both caps clear the stage-0 floors (>= 300 games, >= 2,000 legs); the conservative ROI half-width is about 0.017 (B1), 0.02 (B2), 0.028 (B3)
at cap 25.5 and below 0.04 at cap 15.5. **Alt-spread coverage: 0 rows held** (above). The real gate is stage 0 after the pull.

**Power, Part A, report season 2024 alone** (outcomes SIMULATED from the price-fitted normal ladder with one margin draw per game, so
nesting is exact; YES overpriced by delta in the tail via a monotone shift; PASS = ROI >= +0.02 and lower 95% bound > 0; sub-bands
at the BH worst-case z 2.13; 40 replicates per cell): break-even overpricing is about 1.5c (ROI -1.5% at delta 0, SE 0.5%).
| delta (overpricing) | pooled 5-25c | B1 | B2 | B3 |
|---|---|---|---|---|
| 0c | 0.00 | 0.00 | 0.00 | 0.00 |
| 2c | 0.00 | 0.00 | 0.07 | 0.00 |
| 3c | 0.53 | 0.70 | 0.53 | 0.40 |
| 4c | 1.00 | 1.00 | 0.97 | 0.88 |
Cap 15.5: 3c 0.47 / 0.70 / 0.62 / 0.30; 4c 0.95 / 1.00 / 0.88 / 0.80. **The rule cannot see an overpricing of 2c or less; it is
powered (80%) from about 3.3-3.5c.** A true 2c bias is real money on paper and invisible to this test, by design of the floor:
after fees it is worth about +0.5%, below the practical floor.

**Part B (real captures, 2026-10-20..22 label-free).** Raw minute capture: 11 of 28 games have a quote in (T-66, T-60]; the duckdb
15-minute fallback covers 15 of 28 in (T-75, T-60]; union about 18 of 28 (64%); the 10 uncovered games include every 10-23 game (capture
outage above). In the 11 raw-covered games: **13.0 in-band legs per game (range 12-14)**, 143 legs in B1-B3 (B1 55, B2 35, B3 53) and
100 placebo legs; **all two-sided, ask - bid median 0.010 and at most 0.02**; median bid size 3,500-4,600 contracts in the bands. So
150 legs is about 12 covered games: **the binding constraints are 14 dates and 40 games; look 1 is reachable about 2026-11-07 to
11-10 if the capture runs on every slate**, look 2 (120 games, 40 dates) about late December. Simulated pooled-ROI SE at 40 games is
about 3.0% and at 60 games 2.3%: **look 1 has about 17-35% power at a true 3-4c bias and 66% at 6c** (pooled, alpha 0.05; less at 99%).
Conservative half-width at look 1 is far above 0.04 and is stated, not gated: Part B is a log, not a gate.

## Priors (stated before any result; drafted into docs/PREDICTIONS_LEDGER.md)
- P(Part A runs): depends on the pull (Devin); conditional on the pull, P(stage 0 passes) 0.85 (Pinnacle alt-spread coverage is the risk).
- Part A: P(any band PASS) **0.05**; P(a band advances at select) 0.40 (noise: three coin flips against a +1% bar); P(NULL on all advancing
  bands) 0.45; P(INCONCLUSIVE) 0.45. Expected pooled E2 (5-25c) **+0.004** (80%: -0.01 to +0.02); expected E1 -0.012 (80%: -0.035 to +0.012).
  P(placebo check holds) 0.80. P(power method E2 smaller than proportional E2 by >= 0.005) 0.90 (vig allocation is the biggest single mover).
- Part B: P(look 1 PASS) 0.02; P(look 2 PASS) **0.05**; expected E2 +0.006 (80%: -0.02 to +0.03), more likely positive than Part A because
  Kalshi's retail flow may like long shots more than Pinnacle does, but 2-3c Kalshi-vs-Pinnacle tracking on props says small.
- **Honest default:** no. The mechanisms are plausible and the price of finding out is small; the expected answer is "keep your money".

## What this rule cannot say
- Part A is about sportsbook ladders at about T-64 priced at ASSUMED Kalshi quotes; Kalshi tails may differ. Part B is small and slow.
- It is not a statement about the game-winner, moneyline or totals markets, other ladders (1H, quarters, team totals, player props),
  maker fills, depth, or live in-game prices. Each is a new id.
- Starters-pulled-in-blowouts is a mechanism, not tested separately; the favourite-ladder vs underdog-ladder and spread-size slices
  are the only descriptive handle.
- A late scratch after T-60 moves a game's whole ladder; it is noise in both seasons and in Part B, not corrected.

## Kill criteria
Part A: NULL (select), NULL, or gate failed ends Part A; no re-cut of bands, floor, de-vig, width or select bar after a result. PASS
triggers the attack list, then Part B as replication, never a bet. Part B: any look's word stands; a NULL at look 2 closes the idea.
A different band, ladder or window is a new id. One allowed follow-up at most, then closed.

## Not in this rule
Harness code (written after the freeze, checked against this text by the adversary before scoring), the game-level alt pull
implementation, the paper-trade store schema, the ledger rows (drafted in the report, appended by the main session).

=== RESULTS BELOW ===
