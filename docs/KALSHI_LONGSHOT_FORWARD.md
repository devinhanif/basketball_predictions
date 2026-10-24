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
   all games on or before 2026-10-24 are excluded, decided now.
3. **Tip time and window.** The window is the set of games in `data/schedule/live_tips_2026-27.parquet` as committed at freeze (sha256 written in the FREEZE RECORD;
   1,200 games). The Cup knockout games and the Cup-week games scheduled later (no games ET 2026-12-04..11 are in the file) are excluded and
   counted; playoffs and play-in are after Read 2. The file has no team columns, so a Kalshi game is in the window iff its
   `occurrence_datetime − 180 min` equals a scheduled tip in that file on the same ET date (28 of 28 regular-season games 2026-10-20..23
   matched); tip = that scheduled time. No match: excluded and counted (`no_schedule_match`). ET dates use `America/New_York`.
4. **Contract.** One Kalshi market (one ticker) = one contract. A "game" is the `<DATE><AWAY><HOME>` suffix of the event
   ticker (shared by all series for that game). A "date" is the ET date of tip.
5. **Entry snapshot (price at prediction time).** The latest `source='live'` row with `ts <= tip - 60 min` and age
   `tip - 60 min - ts <= 240 min`. No row in that range: the contract is excluded and counted
   (`no_entry_snapshot`). Never a later row, never the close. The age of every entry snapshot is stored; the
   fraction with age <= 30 min is reported (sensitivity S2).
6. **Eligibility (price filters, applied to the entry snapshot only).** `yes_bid >= 0.01`, `yes_ask - yes_bid <= 0.05`,
   `mid = (yes_bid + yes_ask) / 2 <= 0.15`. Contracts failing a filter are counted by reason (`bid_zero`,
   `wide_spread`, `above_15c`) and never scored. These filters use only the entry snapshot, never the outcome.
   All price comparisons, buckets and returns use integer cents: `bid_c = round(100 * yes_bid)`, `ask_c = round(100 * yes_ask)` from the
   stored value; eligibility is `bid_c >= 1` and `ask_c - bid_c <= 5` and `bid_c + ask_c <= 30`; `mid = (bid_c + ask_c) / 200`;
   `P_no = (100 - bid_c) / 100`, `P_yes = ask_c / 100`. The stored FLOAT columns are never compared directly (on the 2026-10-20..23 capture a
   float comparison rejects 258 of the 499 contracts whose spread is exactly 5c).
7. **Kinds.** `prop` = KXNBAPTS/REB/AST/3PT/STL/BLK; `combo` = stat-sum series (KXNBAPRA/PR/PA/RA); `game` = KXNBAGAME/TOTAL/TEAMTOTAL. KXNBASPREAD is excluded from the decision pool because its tails are SPREAD_TAIL_NO Part B's trade
   (separately frozen, own levels); it is reported as a descriptive slice with the overlap count. Kalshi multivariate/parlay contracts are NOT captured and are out of scope.
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

TO BE CONFIRMED BY DEVIN AT FREEZE against Kalshi's published schedule. The values below are copied from `configs/parlay.yaml` as of this draft
(coefficient 0.07, formula `p_one_minus_p` = P(1-P), default_multiplier 1.0, empty `fee_multiplier`, rounding up once per ORDER in the config
and UNROUNDED per contract in this rule's primary) and are UNCONFIRMED until he does.

The fee parameters are pinned by this text: taker coefficient 0.07, formula `p_one_minus_p`, multiplier M = 1.0 for every series in
Section 1.7 (Devin confirms these against Kalshi's published schedule before the freeze commit; if his check differs, these numbers are
edited before the freeze commit). The scorer REFUSES to run if `verified` is not true, or if the loaded coefficient, `default_multiplier`
or `fee_multiplier` give a different fee for any series in this universe than the pinned values. The one exception is a Kalshi schedule
change recorded in `configs/parlay.yaml` with its Kalshi source URL and effective date before the read date. Such a change applies only
to entries on or after its effective date. A config edit for any other reason never changes this rule's fee. Negative tests: the scorer
refuses on `verified: false` and on a changed coefficient.

## 4. Trade definitions and return

Primary (the question): SELL the long shot = buy NO at the executable price `P_no = 1 - yes_bid` (crossing the spread).
Return per contract `r_no = payoff_no - P_no - fee(P_no)`, `payoff_no = 1 - y`.
Mirror (reported, not decided): buy YES at `P_yes = yes_ask`, `r_yes = y - P_yes - fee(P_yes)`.
The spread is therefore inside every return: the NO buyer pays `1 - bid`, the YES buyer pays `ask`.
Integrity identity the scorer asserts on every contract (guard 8.6(a)), in integer hundredths of a cent:
`r_yes + r_no = -(ask_c - bid_c) / 100 - fee(P_yes) - fee(P_no)`.

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
- A look scores only games whose ET tip date is at least 2 days before the read date (Read 1: tips on or before 2027-01-13; Read 2: on or
  before 2027-04-12). A game with any eligible contract unsettled at the read is excluded WHOLE and counted (`unsettled_game`), never
  contract by contract: Kalshi settles a YES on a "k or more" market as soon as it is reached and a NO only after the final (2026-10-23
  slate at 03:34Z: 904 of 1,644 eligible contracts settled; YES median 2.8 h after tip, NO 3.0 h; DNP scalars up to 13.75 h), so a
  contract-level exclusion would keep YESes and drop NOs.
  A DNP is NOT dropped; it is scored at the fair price and
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
  data rule out a net edge at the floor from the NO side.
- **INSUFFICIENT_SAMPLE** iff n games < 300 or n dates < 50 at a look: no direction is claimed, only the counts and the projected date.
- **KEEP_SHADOWING** otherwise (point at or above the floor but the bound does not clear 0, or the CI straddles the floor).
- **Final read.** At Read 2, KEEP_SHADOWING is recorded as **INCONCLUSIVE** and the rule closes; INSUFFICIENT_SAMPLE at Read 2 also
  closes it. There is no Read 3, no extension, and no re-read with later settlements. A point estimate below zero is reported with its CI
  and is never by itself a verdict.
Power (measured on 2026-10-20..23 entry mids, label-free, plus season-2024 within-game correlations; the realised cluster variance
replaces it at each look): design effect about 3.8 with KXNBASPREAD included. Exact ladder nesting contributes 2.2 with it and 1.7
without it, at the same effective contracts per game (about 29). The rest is same-player cross-stat and cross-player co-movement. SE
of `R` at 300 games about 0.0038. MDE at 80% power: about 1.3c at Read 1 (one-sided 0.005) and about 0.6-0.7c at Read 2 (one-sided
0.02, 700-900 games). Power to clear the bound at a true +1c: about 0.50-0.53 at Read 1 and 0.97-0.99 at Read 2. The Gaussian copula
understates tail co-movement, so these are optimistic by an unknown margin; the earlier assumed ICC of 0.2 overstated the design effect
about 3.5x.

## 7. Looks and alpha

Two looks, fixed now: **Read 1 on 2027-01-15** (games with ET tip dates through 2027-01-13; Section 5 game-level settlement rule) and **Read 2 (final) on 2027-04-14**. Every look reports the
full table whether or not it can decide. One-sided level 0.005 at Read 1 and 0.02 at Read 2 (the decision CI is the matching 99% or 96% two-sided
percentile interval); a PASS needs the bound at the level of the look it occurs at; a second look cannot undo a Read 1 PASS or
NO_POSITIVE_EV_FOUND that met its own rule. Family: this rule and UNDERS_FORWARD test overlapping contracts on the same capture, under Bonferroni across the two rules and both looks
(each rule 0.005 + 0.02; family one-sided 0.05). These levels hold whether or not UNDERS_FORWARD is ever frozen and are never relaxed.
Within this rule m = 1 (pooled `R`); slices are descriptive.

## 8. Guards

1. Entry-snapshot coverage: if fewer than 70% of the settled markets of this rule's series on in-window games have a `source='live'` row with `ts <= tip − 60 min` and
   age <= 240 min (price filters are not applied to this denominator; they need the snapshot) (Section 1.5), the look is
   INSUFFICIENT_SAMPLE for capture reasons and names the dates with gaps. Coverage is by clock, not by outcome, so exclusion does not select on results.
2. Concentration: no single game may contribute more than 3% of eligible contracts at the look unless the report shows `R` without it; PASS additionally requires that `R` recomputed leaving out each series in turn stays > 0 (point estimate). If any
   leave-one-out `R` is <= 0, the verdict is KEEP_SHADOWING at Read 1 and INCONCLUSIVE at Read 2.
3. Settlement completeness: any scalar market missing `settlement_value_dollars` above 2% of scalar markets make the look INSUFFICIENT_SAMPLE (settlement) unless the missing values are fetched from Kalshi's market endpoint within 7 days of the
   read date; the look's game cutoff never moves.
4. Alias/identity: prop contracts with `player_id` NULL stay in the universe (the test needs no player model) but are counted and listed; the scorer does not
   guess names.
5. No later information: a contract is never re-scored with a later snapshot, a later injury report or a closing price.
6. Scorer self-tests run before every look and are printed. Any failure voids that look's verdict: the report prints SELFTEST_FAILED and
   the counts only, until the scorer is fixed under an adversary-reviewed code change. The rule text never changes. (a) The Section 4
   identity on every contract, in integer hundredths of a cent. (b) Calibrated-null coverage: K = 200 synthetic outcome sets on the
   look's own contracts. Each ladder (one player-stat, one team's spread or team total, one game total) gets one uniform draw u, and a
   rung is YES iff u < its entry mid, so ladders stay nested. DNPs are not simulated. The game and date structure is the real one. The
   decision-CI one-sided 5% bound must exclude the known true `E[r_no]` in 2% to 9% of sets. (c) Planted edge: the same sets with every
   YES probability multiplied by 0.80 (long shots overpriced). The point estimate must fall within 0.5c of the known true `R`, and the
   decision CI must cover it.
<!-- FROZEN-END -->

## Sensitivities (descriptive, no decision weight)

S1 fee rounded up per single contract; S2 entry snapshots with age <= 30 min only; S3 YES-side mirror; S4 leave-one-series-out; S5 DNP contracts
excluded (to show how much the fair-price settlement dilutes); S6 maker pricing is NOT evaluated (fill risk cannot be scored from a top-of-book snapshot).

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none. This is a market-calibration test of a fixed trade (buy NO, hold); no model is fitted or promoted.
1. **Information or capacity?** Neither: the only information is the T-60 price. No capacity is added.
2. **Was the training data enough?** No training. The data gate is the section "Gate" below.
3. **Was the test valid?** Real scheduled tip, entry stamped at or before T-60 with a stored age, DNP scored at Kalshi's fair price (not dropped),
   integer support is native (Kalshi lines are x.5 on integer stats), fee from a verified object, clustered by game and date, whole-game
   settlement exclusion (M2), schedule-matched window (M8), and calibrated-null and planted-edge self-tests (M10, guard 8.6). Known weaknesses: the
   eligibility filter (spread <= 5c) removes contracts, so the claim is about tradeable quotes only; the design effect is measured from the entry
   mids plus season-2024 correlations with a copula caveat (Section 6); the 15-minute capture is irregular.
4. **Lineage.** Descends from the favourite-longshot hypothesis and from MARKET_SURFER_PAPER (its trading arm lost; this is the hold-only version).
   Shares contracts with UNDERS_FORWARD (long-shot band) and formerly with SPREAD_TAIL_NO (KXNBASPREAD, now excluded from the decision).
5. **What does it score against?** Realised net dollars per contract, not CRPS or Brier. A Brier edge against the price is a different question
   (KALSHI_VS_PINNACLE, MARKET_ANCHORED_PROPS).

## Data-capture audit (non-negotiable 8; 2026-10-23, from a copy of `data/kalshi/kalshi.duckdb` taken 2026-10-24T02:58Z)

What exists: `kalshi_prices` live rows 2026-10-08..10-24 (1,187,826 rows, 13,234 tickers); regular season began 2026-10-20. Tip = occurrence - 180 min
verified against the schedule. Settled regular-season markets with a known tip: 8,164 over 260 events (of 28 scheduled games 2026-10-20..23; all 28 have at least one eligible long shot at max age 240, and 23 of them were fully settled
at the audit time).

| Gate item | Measured | Verdict |
|---|---|---|
| Entry snapshot coverage, max age 30 min | 3,863 of 8,164 settled markets (47%) | Fails guard 8.1 |
| max age 120 min | 4,230 (52%) | Fails |
| max age 240 min (the rule) | 7,427 (91%) | Passes 8.1 |
| Cause of the gap | The 15-minute snapshot job ran irregularly: 15-23 UTC distinct snapshots per day: 10-20: 24, 10-21: 23, 10-22: 35, 10-23: 2 (expected about 32; the cause was not investigated here, only the gap measured). | Repairable: capture reliability (a launchd heartbeat, or the watchdog the project already prescribes). Cost: small; not done here (ops path is not mine). |
| Eligible long shots (mid <= 15c, bid >= 1c, spread <= 5c), max age 240 | 1,456 contracts, 23 games, 4 nights (about 63 contracts per game, 5.75 games per night) | Rate source for the projection |
| Decision pool after M11 (KXNBASPREAD out), integer cents, max age 240 | 1,430 contracts, 28 games (about 51 per game; 368 KXNBASPREAD contracts reported descriptively) | Rate source after the revision |
| By series | 3PT 277, AST 223, BLK 15, PTS 196, REB 169, STL 36, SPREAD 301, TEAMTOTAL 128, TOTAL 111; GAME (winner) 0; combo (PRA/PR/PA/RA) 0 | Combos and game-winner long shots are not yet populated |
| Excluded by filter at max age 240 | 303 wide-spread, 95 bid = 0 | Reported by reason at every look |
| DNP settlement values | 39 `scalar` markets exist (27 on 2026-10-22). The raw settled page has `settlement_value_dollars` (e.g. 0.16, 0.39) but `kalshi_markets` has NO column for it | Repairable at low cost: add a `settlement_value` column (owner: `nba/kalshi`) or read raw. Until then the scorer reads raw. |
| Seasons/sources not used | Preseason capture (resting players, different regime); Kalshi `/historical` settled markets (hourly candlesticks only, no T-60 top-of-book); sportsbook odds (a different market). All excluded by Devin's forward-only choice, 2026-10-23. | Listed, not used; a `/historical` backfill is possible later as a separate question with its own rule |
| Kalshi multivariate (combo/parlay) contracts | Not captured by any series in `configs/kalshi.yaml` | A real gap for "combos"; `combo` here means stat-sum series only |

### Gate: can the minimum n be reached, and when?

Projection from `data/schedule/live_tips_2026-27.parquet` scheduled games from 2026-10-25, times the measured eligible-game fraction.
Observed fraction of scheduled games with at least one eligible long shot, 2026-10-20..23: 28 / 28 = 1.0 (the earlier 23 / 28 counted only games already settled). Games are not the
binding count (dates are, below); for reference, cumulative games reach:

| Fraction of scheduled games that enter | 150 games | 300 games |
|---|---|---|
| 1.0 | 2026-11-14 | 2026-12-13 |
| 0.8 | 2026-11-19 | 2026-12-23 |
| 0.6 (capture stays unreliable) | 2026-11-28 | 2027-01-08 |

Dates bind, not games: the schedule has 538 games on 70 ET dates from 2026-10-25 to 2027-01-13 (1,164 on 151 dates through 2027-04-12).
Capture failures come as whole nights, so 50 dates by Read 1 needs at least 72% of nights captured. At 100% both counts are met on
2026-12-23, at 80% on 2027-01-06, at 71% on 2027-01-14, and at 60% only on 2027-01-27, which is an INSUFFICIENT_SAMPLE Read 1. Every
scheduled game 2026-10-20..23 had at least one eligible contract (LONGSHOT: 28 of 28; the earlier "23 / 28" counted only games already
settled). Power is measured in Section 6 (MDE 1.3-1.6c at Read 1, 0.6-0.9c at Read 2).
The gate PASSES on counts at 72% or better night capture; power at Read 1 is about 0.5 at a true +1c (Section 6),
so a Read 1 bound that includes 0 means no power below about 1.3c, not no edge. A look is never moved to chase the answer.

### Exploratory glance, disclosed (not part of the scoring window)

While measuring the audit, per-bucket realised YES rates were printed for the 2026-10-20..23 contracts (775 contracts, 13 games, spread <= 5c, age <= 30 min): B1 2.9% vs
mean mid 3.5%, B2 2.6% vs 7.3%, B3 8.8% vs 12.3%. Realised was below the price in every bucket. No fee, no spread, no clustering and no test were applied; with
about 13 games it is noise-sized, it is excluded from every scored number, and it is the reason the P(pass) prior is not set lower. The rule was written before any net-of-cost return was computed, but AFTER this glance: the 15c cap, the bucket edges and the 240-min
entry age were chosen with the glance already seen (DECISIONS 2026-10-23 22:14). The scoring window starts 2026-10-25 and contains none
of these games, so the glance cannot raise the false-pass rate. It can only have shaped the prior and the power.

## Open choices for Devin (default in bold)

1. Floor: **+1.0c per contract net**; alternatives 0.5c or 2c. (2c was the drafted Read 1 detectable edge; the measured one is 1.3-1.6c.)
2. Entry-snapshot max age: **240 min** (91% coverage); 120 min would drop coverage to 52% on the current capture.
3. Fee rounding in the primary: **unrounded** (no stakes); per-contract rounding is S1.
4. Looks: **2027-01-15 and 2027-04-14**, alpha 0.005 / 0.02 (Bonferroni with UNDERS_FORWARD).
5. Calibration slope: **descriptive, not a gate** (power is thin over a 1c-15c range); making it a second PASS condition lowers P(pass).
6. Scope: **price filters (bid >= 1c, spread <= 5c) are conditions for being tradeable**; dropping them inflates n and inflates apparent edge.
7. Overlap with SPREAD_TAIL_NO Part B (agent/market-spread-tail-no) on KXNBASPREAD tails: **KXNBASPREAD dropped from the decision pool, reported descriptively with the overlap count** (M11); alternative: keep it and use three-rule Bonferroni, 0.0033 / 0.0133 each.
8. Capture reliability: **Devin or the ops owner schedules a heartbeat so the 15-minute job cannot go dark**; not done here.

## Amendments (append only, dated)

(none)
