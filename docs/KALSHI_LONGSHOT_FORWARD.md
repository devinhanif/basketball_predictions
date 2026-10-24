# KALSHI_LONGSHOT_FORWARD - are Kalshi NBA long shots overpriced after fees? (forward-only)

STATUS: DRAFT, NOT FROZEN. Written 2026-10-23 by the market agent (branch `agent/market-kalshi-longshot-fwd`).
Forward-only was Devin's choice on 2026-10-23. Season 2025 and the holdout are not touched; no historical
Kalshi pull is part of this rule. It reads prices only (no orders, no credentials, no stakes, no sizing).

FREEZE RECORD (outside the frozen section; filled by the hand that freezes):
- Frozen on: ____ (before any scored row exists; the scoring window opens at the first ET date after the freeze commit)
- sha256 of the frozen section: ____
- Verify: `awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' docs/KALSHI_LONGSHOT_FORWARD.md | shasum -a 256`

## Question and what the rule protects against

Are contracts priced at 15c or less (player props, stat-sum combos, game markets) paid for at more than they
realise, so that SELLING them (buying the NO side at the bid-implied price) earns a positive return after the
fee and the spread? The favourite-longshot bias says yes in many betting markets. Spread and fee are of the
same order as the bias, so the answer is not obvious, and "no" is a result we are proud of.

Protects against: (a) pricing long shots off a bias story with no net-of-cost evidence (Devin's "swing" money is
partly long shots, DECISIONS 2026-10-23 19:3x); (b) judging against the close or against later information
(the price is the one available at T-60); (c) counting 60 correlated rungs of one game as 60 observations
(clusters are games and dates); (d) dropping DNPs silently; (e) reading a rate off a fee that was never verified.

This rule uses NO model forecast. It is a market-calibration test only. It can never justify a stake; a PASS
is "candidate for a small declared experiment" at most, and money stays Devin's decision.

<!-- FROZEN-BEGIN -->
## 1. Universe

1. **Capture.** `kalshi_prices` (source `live`) and `kalshi_markets` in `data/kalshi/kalshi.duckdb`, read from a
   COPY (the live file has a launchd writer). Series: `game_series` and `prop_series` in `configs/kalshi.yaml`.
2. **Scoring window.** Regular-season games of season 2026 whose scheduled tip is at or after 00:00 ET of
   the first ET date after the freeze commit (not earlier than 2026-10-25). Preseason (2026-10-05..16) and
   all games on or before 2026-10-24 are excluded, decided now. Playoffs/Cup knockout excluded (not in
   the window of any read below).
3. **Tip time.** Real scheduled tip from `data/schedule/live_tips_2026-27.parquet`, joined by game. Kalshi's own
   `occurrence_datetime` minus 180 minutes equals a scheduled tip in 352 of 352 events checked on 2026-10-23, and is the
   permitted fallback only if the schedule row is missing (counted, reported).
4. **Contract.** One Kalshi market (one ticker) = one contract. A "game" is the `<DATE><AWAY><HOME>` suffix of the event
   ticker (shared by all series for that game). A "date" is the ET date of tip.
5. **Entry snapshot (price at prediction time).** The latest `source='live'` row with `ts <= tip - 60 min` and age
   `tip - 60 min - ts <= 240 min`. No row in that range: the contract is excluded and counted
   (`no_entry_snapshot`). Never a later row, never the close. The age of every entry snapshot is stored; the
   fraction with age <= 30 min is reported (sensitivity S2).
6. **Eligibility (price filters, applied to the entry snapshot only).** `yes_bid >= 0.01`, `yes_ask - yes_bid <= 0.05`,
   `mid = (yes_bid + yes_ask) / 2 <= 0.15`. Contracts failing a filter are counted by reason (`bid_zero`,
   `wide_spread`, `above_15c`) and never scored. These filters use only the entry snapshot, never the outcome.
7. **Kinds.** `prop` = KXNBAPTS/REB/AST/3PT/STL/BLK; `combo` = stat-sum series (KXNBAPRA/PR/PA/RA); `game` = KXNBAGAME/
   SPREAD/TOTAL/TEAMTOTAL. Kalshi multivariate/parlay contracts are NOT captured and are out of scope.
   As of 2026-10-23 no `combo` contract met the filters (see audit); it is reported at n = 0 until it does.

## 2. Price buckets

By entry `mid`: B1 [0.01, 0.05), B2 [0.05, 0.10), B3 [0.10, 0.15]. The decision uses the pooled universe
(B1 + B2 + B3). Buckets and kinds are descriptive slices with no decision weight; a slice with fewer than
30 games shows "insufficient sample" and nothing else.

## 3. Fee (verified config object only)

Loaded at run time from `configs/parlay.yaml` `fee_model` through `nba.parlay.config.FeeModel` (no rate in code). The
scorer REFUSES to run unless `verified: true` and prints the config's `source` and the coefficient/multiplier it used in
every report. Per contract, with P the price paid in dollars:
`fee = coefficient * M(series) * P * (1 - P)`, taker coefficient, `M` from `fee_multiplier` (longest series prefix, else
`default_multiplier`). Primary: the fee is UNROUNDED per contract (equals a large order; no stake is assumed).
Sensitivity S1: fee rounded UP to a whole cent per single contract (the small-order worst case).
At freeze the config's fee schedule is re-verified by Devin against Kalshi's published schedule (the 2026-10-08
verification is "user-provided"; an automated fetch on 2026-10-23 was rate-limited and verified nothing).

## 4. Trade definitions and return

Primary (the question): SELL the long shot = buy NO at the executable price `P_no = 1 - yes_bid` (crossing the spread).
Return per contract `r_no = payoff_no - P_no - fee(P_no)`, `payoff_no = 1 - y`.
Mirror (reported, not decided): buy YES at `P_yes = yes_ask`, `r_yes = y - P_yes - fee(P_yes)`.
The spread is therefore inside every return: the NO buyer pays `1 - bid`, the YES buyer pays `ask`.

## 5. Settlement and DNP

`y` is the YES payoff in dollars per contract:
- `result = yes` -> 1; `result = no` -> 0.
- `result = scalar` (inactive player, or active but never takes the court, or a game postponed past 48 hours): Kalshi settles to
  a fair price. `y = settlement_value_dollars` read from the stored raw settled page
  (`data/kalshi/raw/<date>/*_settled_*.json.gz`, market object, `expiration_value` e.g. "Confirmed DNP"). Kalshi's rules text
  (`rules_secondary`) covers an inactive player explicitly ("If a player is inactive, or active but never takes the court,
  the market settles to a fair price"), which closes the open question in `docs/PARLAY_ENGINE.md`.
  A scalar market with no `settlement_value_dollars` is excluded, counted and flagged loudly; the value is never guessed, never
  taken from a box score, and never replaced by the entry mid.
- Not yet settled by the read date: excluded and counted (`unsettled`). A DNP is NOT dropped; it is scored at the fair price and
  counted separately (`n_dnp`), because a seller of a long shot is paid or paying for DNP risk and dropping DNPs would bias the sign.
Outcomes come only from Kalshi's own settled markets (the contract being traded), never from our box-score tables.

## 6. Metric, floor and decision

Primary metric: `R` = mean of `r_no` over the pooled eligible contracts that are settled (equal weight per contract).
Secondary (descriptive, not a gate): calibration slope `b` and intercept `a` from the OLS fit `y = a + b * mid` on the same
contracts (`y` may be fractional for DNPs; slope is OLS rather than logistic for that reason), with the same clustered CI;
`b < 1` and a negative gap `mean(y) - mean(mid)` are the signature of overpricing. Also reported: realised mean `y` versus mean `mid`
per bucket, mean spread and mean fee per bucket (so the cost side is visible), n contracts, n games, n dates, n_dnp.

- **Floor (practical):** `R >= +0.010` dollars per contract after fee and spread (about +1.1% on the roughly 0.93 paid for a NO).
- **CI:** percentile bootstrap, B = 10,000, `numpy.random.default_rng(2026)` re-seeded at each read, seed logged. Two clusterings are
  computed: whole games, and whole ET dates (a date contains several games, so date clusters also absorb same-night news and
  injury-report timing). The decision CI is the WIDER of the two (conservative). Both are printed.
- **PASS** iff ALL: n games >= 300, n dates >= 50, `R >= 0.010`, and the one-sided lower bound of the decision CI > 0 at the look's level
  (Section 7), and the guards in Section 8 hold.
- **NO_POSITIVE_EV_FOUND** (the first-class verdict) iff n is reached and the upper bound of the decision CI (same level) < 0.010, i.e. the
  data rule out a net edge at the floor from the NO side. Also reported if `R` is clearly negative.
- **INSUFFICIENT_SAMPLE** iff n games < 300 or n dates < 50 at a look: no direction is claimed, only the counts and the projected date.
- **KEEP_SHADOWING** otherwise (point at or above the floor but the bound does not clear 0, or the CI straddles the floor).
Power (assumption-based, Bernoulli variance; the realised variance replaces it at the look): per-contract SD about 0.27; 63 contracts
per game with an assumed within-game intraclass correlation of 0.2 gives about 4.6 effective observations per game, so the SE of `R`
is about 0.0073 at 300 games. Power for a one-sided 95% bound: about 0.86 if the true net edge is +0.020, about 0.39 if +0.010, about 0.05 if 0. The
detectable edge at 80% power is therefore about +0.020, twice the floor. This is stated so that "CI includes 0" at 300 games is read as
"no power for edges below about 2c", not as "no edge".

## 7. Looks and alpha

Two looks, fixed now: **Read 1 on 2027-01-15** (data through settlements of 2027-01-14) and **Read 2 (final) on 2027-04-14**. Every look reports the
full table whether or not it can decide. One-sided level 0.01 at Read 1 and 0.04 at Read 2 (the decision CI is the matching 98% or 92% two-sided
percentile interval); a PASS needs the bound at the level of the look it occurs at; a second look cannot undo a Read 1 PASS or
NO_POSITIVE_EV_FOUND that met its own rule. Family size m = 1 (the pooled NO-side return). No BH is needed; slices are descriptive.

## 8. Guards

1. Entry-snapshot coverage: if fewer than 70% of otherwise-eligible settled contracts have an entry snapshot (Section 1.5), the look is
   INSUFFICIENT_SAMPLE for capture reasons and names the dates with gaps. Coverage is by clock, not by outcome, so exclusion does not select on results.
2. Concentration: no single game may contribute more than 3% of eligible contracts at the look unless the report shows `R` without it; no single series
   may carry the whole sign of `R` (report `R` leaving out each series in turn).
3. Settlement completeness: any scalar market missing `settlement_value_dollars` above 2% of scalar markets blocks the look until repaired.
4. Alias/identity: prop contracts with `player_id` NULL stay in the universe (the test needs no player model) but are counted and listed; the scorer does not
   guess names.
5. No later information: a contract is never re-scored with a later snapshot, a later injury report or a closing price.
<!-- FROZEN-END -->

## Sensitivities (descriptive, no decision weight)

S1 fee rounded up per single contract; S2 entry snapshots with age <= 30 min only; S3 YES-side mirror; S4 leave-one-series-out; S5 DNP contracts
excluded (to show how much the fair-price settlement dilutes); S6 maker pricing is NOT evaluated (fill risk cannot be scored from a top-of-book snapshot).

## Data-capture audit (non-negotiable 8; 2026-10-23, from a copy of `data/kalshi/kalshi.duckdb` taken 2026-10-24T02:58Z)

What exists: `kalshi_prices` live rows 2026-10-08..10-24 (1,187,826 rows, 13,234 tickers); regular season began 2026-10-20. Tip = occurrence - 180 min
verified against the schedule. Settled regular-season markets with a known tip: 8,164 over 260 events (of 28 scheduled games 2026-10-20..23; 23 games have
at least one eligible long shot at max age 240).

| Gate item | Measured | Verdict |
|---|---|---|
| Entry snapshot coverage, max age 30 min | 3,863 of 8,164 settled markets (47%) | Fails guard 8.1 |
| max age 120 min | 4,230 (52%) | Fails |
| max age 240 min (the rule) | 7,427 (91%) | Passes 8.1 |
| Cause of the gap | The 15-minute snapshot job ran irregularly: 15-23 UTC distinct snapshots per day: 10-20: 24, 10-21: 23, 10-22: 35, 10-23: 2 (expected about 32; the cause was not investigated here, only the gap measured). | Repairable: capture reliability (a launchd heartbeat, or the watchdog the project already prescribes). Cost: small; not done here (ops path is not mine). |
| Eligible long shots (mid <= 15c, bid >= 1c, spread <= 5c), max age 240 | 1,456 contracts, 23 games, 4 nights (about 63 contracts per game, 5.75 games per night) | Rate source for the projection |
| By series | 3PT 277, AST 223, BLK 15, PTS 196, REB 169, STL 36, SPREAD 301, TEAMTOTAL 128, TOTAL 111; GAME (winner) 0; combo (PRA/PR/PA/RA) 0 | Combos and game-winner long shots are not yet populated |
| Excluded by filter at max age 240 | 303 wide-spread, 95 bid = 0 | Reported by reason at every look |
| DNP settlement values | 39 `scalar` markets exist (27 on 2026-10-22). The raw settled page has `settlement_value_dollars` (e.g. 0.16, 0.39) but `kalshi_markets` has NO column for it | Repairable at low cost: add a `settlement_value` column (owner: `nba/kalshi`) or read raw. Until then the scorer reads raw. |
| Seasons/sources not used | Preseason capture (resting players, different regime); Kalshi `/historical` settled markets (hourly candlesticks only, no T-60 top-of-book); sportsbook odds (a different market). All excluded by Devin's forward-only choice, 2026-10-23. | Listed, not used; a `/historical` backfill is possible later as a separate question with its own rule |
| Kalshi multivariate (combo/parlay) contracts | Not captured by any series in `configs/kalshi.yaml` | A real gap for "combos"; `combo` here means stat-sum series only |

### Gate: can the minimum n be reached, and when?

Projection from `data/schedule/live_tips_2026-27.parquet` scheduled games from 2026-10-25, times the measured eligible-game fraction.
Observed fraction of scheduled games with at least one eligible long shot, 2026-10-20..23: 23 / 28 = 0.82 (and the capture was broken on parts of 10-20 and
10-23, so this is not an upper bound). Cumulative eligible games reach:

| Fraction of scheduled games that enter | 150 games | 300 games |
|---|---|---|
| 1.0 | 2026-11-14 | 2026-12-13 |
| 0.8 (observed 0.82) | 2026-11-19 | 2026-12-23 |
| 0.6 (capture stays unreliable) | 2026-11-28 | 2027-01-08 |

By 2027-01-15 (Read 1) the 0.6 case has about 330 games; by the end of the regular season the 0.6 case has well over 700. The n gate (300 games, 50 dates)
passes with margin at Read 1 if capture is at least 60% reliable, and fails Read 1 (INSUFFICIENT_SAMPLE, no claim) only if capture is worse than that. The
power gate is the binding one: 300 games detects about +2c, not +1c (Section 6); the end-of-season look at about 700 games detects about +1.3c.
The gate PASSES on counts and is HONEST-WEAK on power. A look is never moved to chase the answer.

### Exploratory glance, disclosed (not part of the scoring window)

While measuring the audit, per-bucket realised YES rates were printed for the 2026-10-20..23 contracts (775 contracts, 13 games, spread <= 5c, age <= 30 min): B1 2.9% vs
mean mid 3.5%, B2 2.6% vs 7.3%, B3 8.8% vs 12.3%. Realised was below the price in every bucket. No fee, no spread, no clustering and no test were applied; with
about 13 games it is noise-sized, it is excluded from every scored number, and it is the reason the P(pass) prior is not set lower. The rule was written before any
net-of-cost return was computed.

## Open choices for Devin (default in bold)

1. Floor: **+1.0c per contract net**; alternatives 0.5c or 2c. A 2c floor matches the 80%-power detectable edge at 300 games.
2. Entry-snapshot max age: **240 min** (91% coverage); 120 min would drop coverage to 52% on the current capture.
3. Fee rounding in the primary: **unrounded** (no stakes); per-contract rounding is S1.
4. Looks: **2027-01-15 and 2027-04-14**, alpha 0.01 / 0.04.
5. Calibration slope: **descriptive, not a gate** (power is thin over a 1c-15c range); making it a second PASS condition lowers P(pass).
6. Scope: **price filters (bid >= 1c, spread <= 5c) are conditions for being tradeable**; dropping them inflates n and inflates apparent edge.
7. Overlap with SPREAD_TAIL_NO Part B (agent/market-spread-tail-no) on KXNBASPREAD tails: **keep separate, report the overlap count**; the pooled test is not a substitute for its rule.
8. Capture reliability: **Devin or the ops owner schedules a heartbeat so the 15-minute job cannot go dark**; not done here.

## Amendments (append only, dated)

(none)
