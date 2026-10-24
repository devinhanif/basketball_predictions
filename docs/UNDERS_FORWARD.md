# UNDERS_FORWARD - does buying NO on Kalshi NBA player props, held to settlement, earn a positive return after fee and spread? (forward-only)

STATUS: DRAFT, NOT FROZEN. Written 2026-10-23 by the market agent (branch `agent/market-unders-forward`).
Origin: Devin's idea, his override of Claude's lean (Claude's prior P(pass) 0.10-0.15 before costs; this draft's own prior is lower, see the
PREDICTIONS_LEDGER row). Forward-only; season 2025 and the holdout are not touched; no historical Kalshi pull. Prices only: no orders, no
credentials, no stakes. The paper Kelly column (Section 9) is a unit-free fraction shown on paper, never a stake.

Same family and plumbing as KALSHI_LONGSHOT_FORWARD (draft `docs/KALSHI_LONGSHOT_FORWARD.md` on branch `agent/market-kalshi-longshot-fwd`,
commit 00c1516). Everything not restated below is that draft's text and numbers (entry snapshot, fee object, DNP settlement, clustering,
guards, looks). The differences are listed in Section 0 so a reader needs only this file to score it.

FREEZE RECORD (outside the frozen section; filled by the hand that freezes):
- Frozen on: ____ (before any scored row exists; the scoring window opens at the first ET date after the freeze commit, not earlier than 2026-10-25)
- sha256 of the frozen section: ____
- Verify: `awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' docs/UNDERS_FORWARD.md | shasum -a 256`

## Question and what the rule protects against

Across ALL price levels of Kalshi NBA player-prop contracts (not just long shots), does BUYING NO at the executable T-60 price and holding
to settlement return more than it costs? "NO" is the under on a "k or more" contract. The spread (the NO buyer pays `1 - yes_bid`) and the
taker fee are inside every return.

Protects against: (a) calling the under "the market's soft side" from a hit-rate or a Pinnacle bias without netting cost (FAN_KNOWLEDGE:
at lines +0.5 sd above our mean the realised over-rate is 0.42 against Pinnacle's 0.45, a 3-point gap that is about the size of spread plus
fee); (b) reusing the intra-game trading result as if it were evidence about holding (the surfer lost 4.2c a trade, below); (c) judging against
the close or against later information; (d) 160 correlated rungs of one game counted as 160 observations; (e) dropping DNPs silently;
(f) reading a rate off an unverified fee; (g) letting a paper Kelly number read as advice.

Prior evidence, cited not relied on:
- MARKET_SURFER_PAPER (branch `agent/market-surfer-paper`, results cd46134, DECISIONS 2026-10-23 21:xx): intra-game trading lost **4.2c per
  contract per trade**, game-clustered CI [-6.5, -2.7], n 1,378 trades / 13 games / 2 nights; the same entries held to settlement were +0.6c
  [-7.2, +6.3] (no information); placebo -5.2c; costs were 2.8c fee + 2.4c spread against +1.0c of drift. Lesson used here: no trading in
  and out, one entry per contract, held to settlement, so only one spread and one fee are paid.
- FAN_KNOWLEDGE lines section (Pinnacle T-60, 72,674 pairs, historical, a model-conditioned slice): over-rate 0.54 -> 0.42 as the line moves
  from -0.5 to +0.5 sd of our mean while Pinnacle says 0.54 -> 0.45. That is a statement about Pinnacle's price being 3 points too high on
  overs at high lines; Kalshi quotes track Pinnacle within 2-3c (KALSHI_VS_PINNACLE gate), so the gross edge is at most that, before about
  2.5-3c of cost.

This rule uses NO model forecast to trade. Our forecast and Pinnacle's line enter only as STRATUM labels, fixed at T-60. It can never justify
a stake. A PASS is "candidate for a small declared experiment" at most; money stays Devin's decision.

<!-- FROZEN-BEGIN -->
## 0. What differs from KALSHI_LONGSHOT_FORWARD (00c1516)

| Item | LONGSHOT | UNDERS (this rule) |
|---|---|---|
| Price filter | mid <= 15c | ALL mids; same tradeability filters |
| Series | props, combos, game markets | player props and stat-sum combos only (Section 1.7); game markets excluded |
| Side | NO only (long shots) | NO only, every price |
| Strata | kind and bucket | bucket, stat, line position vs our mean and vs Pinnacle (Section 3) |
| Alpha | 0.01 / 0.04, m = 1 | Bonferroni across the two rules: 0.005 / 0.02 (Section 7) |
| Trading | none | none; one entry per ticker, hold to settlement (Section 4) |
| Extra column | none | paper Kelly fraction, paper only (Section 9) |

The long-shot band here (YES-mid <= 15c) is the SAME trade on the same contracts as LONGSHOT's props. It is reported (Section 2) and
declared OVERLAPPING; neither rule's verdict is a substitute for the other, and the overlap count is printed.

## 1. Universe

1. **Capture.** `kalshi_prices` (source `live`) and `kalshi_markets` in `data/kalshi/kalshi.duckdb`, read from a COPY (the live file has a
   launchd writer). Series: `prop_series` in `configs/kalshi.yaml`.
2. **Scoring window.** Regular-season games of season 2026 whose scheduled tip is at or after 00:00 ET of the first ET date after the freeze
   commit (not earlier than 2026-10-25). Preseason (2026-10-05..16) and every game on or before 2026-10-24 are excluded, decided now.
   Playoffs and Cup knockout excluded. An ET date uses `America/New_York` (not a fixed offset).
3. **Tip time.** Real scheduled tip from `data/schedule/live_tips_2026-27.parquet`, joined by game; Kalshi `occurrence_datetime` minus 180 min
   is the fallback only if the schedule row is missing (counted, reported).
4. **Contract.** One Kalshi ticker = one contract. A "game" is the `<DATE><AWAY><HOME>` suffix of the event ticker. A "date" is the ET date of tip.
   A line is the market's `floor_strike` (x.5); YES pays if the stat exceeds it (the stat is at least `threshold`); NO pays if it does not.
5. **Entry snapshot (price at prediction time).** The latest `source='live'` row with `ts <= tip - 60 min` and age `tip - 60 min - ts <= 240 min`.
   None: the contract is excluded and counted (`no_entry_snapshot`). Never a later row, never the close. The age of every entry is stored; the
   fraction with age <= 30 min is reported (S2).
6. **Eligibility (applied to the entry snapshot only).** `yes_bid >= 0.01` (so the NO price is at most 0.99) and `yes_ask - yes_bid <= 0.05`.
   Counted by reason (`bid_zero`, `wide_spread`) and never scored. There is NO upper limit on the mid. Filters use only the entry snapshot.
7. **Kinds.** `prop` = KXNBAPTS/REB/AST/3PT/STL/BLK; `combo` = KXNBAPRA/PR/PA/RA (stat sums). Game markets (KXNBAGAME/SPREAD/TOTAL/TEAMTOTAL)
   are OUT: the question is player props, and KXNBASPREAD tails belong to SPREAD_TAIL_NO. Kalshi multivariate/parlay contracts are not captured
   and are out of scope.

## 2. Price buckets (by entry YES-mid; the trade is always NO)

U1 [0.01,0.05), U2 [0.05,0.10), U3 [0.10,0.15] (these three are the long-shot band, OVERLAPPING with LONGSHOT), U4 (0.15,0.30], U5 (0.30,0.50],
U6 (0.50,0.70], U7 (0.70,0.85], U8 (0.85,0.99]. The decision pools every bucket. Buckets are descriptive with no decision weight; a bucket
with fewer than 30 games prints "insufficient sample" and nothing else. The long-shot band's own pooled R is printed as `R_band` for comparison
with LONGSHOT.

## 3. Strata (descriptive, fixed at T-60, no decision weight)

All strata are assigned from information stored with a timestamp at or before `tip - 60 min`; a missing input puts the contract in the `unknown`
stratum of that axis (counted), never imputed.

- **By stat:** pts, reb, ast, fg3m, stl, blk, combo.
- **By line position vs our mean (FAN_KNOWLEDGE axis):** `z = (line - mean) / sd`, `mean` and `p10/p90` from the latest `prop_predictions`
  row for the (game, player, stat) with `made_at <= tip - 60 min` (the production model of record; nothing is refit here), `sd = (q90 - q10) / 2.563`.
  Bins: z <= -0.5, -0.5 < z <= +0.5, z > +0.5. Our median `q50` position is printed alongside as `(line - q50)`.
- **By line position vs Pinnacle:** the latest `book = 'pinnacle'` row in `data/markets/market_asof.duckdb` `live_book_odds` for the same game, player
  and stat with `received_at <= tip - 60 min` and age <= 240 min. `D = line_kalshi - line_pinnacle` (both x.5, so an integer): bins D <= -1, D = 0, D >= +1,
  `no_pinnacle`. For D = 0 the devigged Pinnacle over-probability minus the Kalshi YES-mid is printed (descriptive only; the formal Kalshi-vs-Pinnacle
  test is KALSHI_VS_PINNACLE).
- The cross cell "z > +0.5 and D = 0" is the one the FAN_KNOWLEDGE number refers to; it is printed first. It is NOT a second decision (Section 7).

## 4. Trade definition and return

Primary: buy NO at the executable ask `P_no = 1 - yes_bid` (crossing the spread), hold to settlement, one contract per ticker, one entry at the
entry snapshot, no sell, no re-entry, no intra-game trade of any kind (nothing is evaluated that would need a second quote).
`r_no = (1 - y) - P_no - fee(P_no)`. Mirror (reported only): `r_yes = y - yes_ask - fee(yes_ask)`.
Integrity identity the scorer asserts on every contract: `r_yes + r_no = -(spread) - fee(yes_ask) - fee(P_no)` exactly.
Maker pricing is not evaluated (fill risk cannot be scored from a top-of-book snapshot).

## 5. Fee (verified config object only)

Loaded at run time from `configs/parlay.yaml` `fee_model` through `nba.parlay.config.FeeModel`; the scorer REFUSES to run unless `verified: true` and
prints the config's `source`, the coefficient and the multiplier it used in every report. Per contract, P the price paid in dollars:
`fee = coefficient * M(series) * P * (1 - P)`, taker coefficient, `M` by longest series prefix else `default_multiplier`. Primary: UNROUNDED.
S1: rounded up to a whole cent per single contract. At freeze, Devin re-verifies the schedule against Kalshi's published one (the 2026-10-08
verification is "user-provided"; an automated fetch on 2026-10-23 was rate-limited and verified nothing).

## 6. Settlement, DNP, metric, floor, decision

`y` (YES payoff in dollars) is Kalshi's own settlement: `yes` -> 1, `no` -> 0, `scalar` (inactive, or active but never takes the court) -> the
`settlement_value_dollars` in the stored raw settled page `data/kalshi/raw/<date>/*_settled_*.json.gz`. A scalar with no stored value is
excluded, counted and flagged (never guessed, never from a box score, never the entry mid). Unsettled by the read date: excluded and counted.
DNPs are scored at the fair price and counted (`n_dnp`); S5 shows R without them.

- Primary metric `R` = mean of `r_no` over all eligible settled contracts (all buckets pooled, equal weight per contract). ROI per dollar paid
  (`sum r_no / sum (P_no + fee)`) is printed beside it. Also printed per bucket and stratum: n contracts, n games, n dates, n_dnp, mean YES-mid,
  realised mean `y`, mean spread, mean fee, mean `r_no`.
- Floor (practical): `R >= +0.010` dollars per contract.
- CI: percentile bootstrap, B = 10,000, `numpy.random.default_rng(2026)` re-seeded at each read, seed logged. Clusters: whole games, and whole ET
  dates; the decision CI is the WIDER of the two. Both printed.
- **PASS** iff ALL: n games >= 300, n dates >= 50, `R >= 0.010`, the one-sided lower bound of the decision CI > 0 at the look's level (Section 7),
  and the guards (Section 8) hold.
- **NO_POSITIVE_EV_FOUND** (first-class) iff n is reached and the upper bound of the decision CI (same level) < 0.010, or `R` is clearly negative.
- **INSUFFICIENT_SAMPLE** iff n games < 300 or n dates < 50 at a look: counts and projected date only, no direction.
- **KEEP_SHADOWING** otherwise.
- A bucket or stratum can NEVER be promoted from a pooled PASS, and a pooled NO_POSITIVE_EV_FOUND does not rule out a stratum; strata are
  hypotheses for a new rule, not findings.

Power (assumption-based; realised variance replaces it at the look). Per-contract SD of `r_no` about 0.44 (mean of mid(1-mid) is about 0.19 on the
real capture); about 159 eligible contracts per game; within-game intraclass correlation assumed 0.06 (unmeasured; rungs of one player's ladder
co-move, so 0.03-0.10 is the plausible range), design effect 10.5, effective N about 4,500 at 300 games, SE of `R` about 0.0066 (0.0049-0.0083
across the range). Detectable edge at 80% power: about **+2.3c at Read 1** (alpha 0.005, 300 games) and about **+1.1c at Read 2** (alpha 0.02,
about 900 games, SE 0.0038). Power to PASS if the true net edge is +2.0c: about 0.68 at Read 1, near 1 at Read 2; if +1.0c: at most 0.5 (the point
estimate must also reach the floor); if 0: alpha. "CI includes 0" at Read 1 means "no power below about 2c", not "no edge".

## 7. Looks, alpha, multiplicity

Reads, same as LONGSHOT: **Read 1 on 2027-01-15** (settlements through 2027-01-14) and **Read 2 (final) on 2027-04-14**; a look is never moved.
Multiplicity: two rules test overlapping contracts on the same capture (LONGSHOT, UNDERS). **Bonferroni across the two rules**: each rule's
one-sided level is halved, **0.005 at Read 1 and 0.02 at Read 2** (decision CI = the matching 99% / 96% two-sided percentile interval). Within this
rule the family is m = 1 (pooled `R`); strata are descriptive. LONGSHOT's draft states 0.01/0.04 (m = 1) and must be amended to 0.005/0.02 by
whoever freezes it, in the same commit or before; if LONGSHOT is shelved, this rule's levels are NOT relaxed after the fact (a rule never changes
after a result). A second look cannot undo a Read 1 PASS or NO_POSITIVE_EV_FOUND that met its own rule.

## 8. Guards

1. Entry coverage: if fewer than 70% of otherwise-eligible settled contracts have an entry snapshot, INSUFFICIENT_SAMPLE for capture reasons, naming
   the dates with gaps (coverage is by clock, not outcome).
2. Concentration: no game above 3% of eligible contracts unless `R` is shown without it; `R` leaving out each stat in turn (no single stat may
   carry the whole sign).
3. Settlement completeness: scalar markets missing `settlement_value_dollars` above 2% block the look.
4. Identity: `player_id` NULL props stay in (the test needs no player model) but are counted and listed; the scorer never guesses names.
5. No later information: never re-scored with a later snapshot, report or close.
6. Scorer self-tests run before every look and are printed: the Section 4 identity; a within-game outcome shuffle (B = 1,000) giving a bootstrap
   rejection rate of 0.05 +/- 0.02 at one-sided 5% (the CI procedure is neither too wide nor too narrow); and a planted-edge check recovering a +3c
   planted NO edge to within 0.5c.
<!-- FROZEN-END -->

## 9. Paper Kelly column (paper only; outside the frozen decision)

Shown per bucket and stratum so the reader sees how small an honest fraction is. Definition: with `c` the bucket's mean cost per contract
(`P_no + fee`) and `q_lo` the 10th percentile of the decision-CI bootstrap distribution of the bucket's mean NO payoff `1 - y`, the paper full-Kelly
fraction of a NOTIONAL bankroll is `f = max(0, (q_lo - c) / (1 - c))`; half-Kelly is `f / 2`. Printed as a percent of a notional bankroll, labelled
"PAPER ONLY. Not a stake. Not advice." It takes no bankroll in dollars and prints no dollar amount; it is zero wherever the lower bound does not clear the
cost, which is the expected case in almost every cell. It does not use `nba/parlay/bayes_kelly.py` (an unmerged stake sizer on
`agent/market-bayes-kelly`), so no stake path is imported. DECISIONS 2026-10-09 ("no stakes, no sizing prompts") stands: this is a calibration
display Devin asked for on 2026-10-23, and it cannot enter a verdict.

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none. This is a market-calibration test of a fixed trade (buy NO, hold); no model is fitted or promoted. Our forecast appears only
as a stratum label.
1. **Information or capacity?** Neither: the only information is the T-60 price. No capacity is added. Strata add pre-tip information we already hold
   (our mean, Pinnacle's line), used to slice, not to select trades.
2. **Was the training data enough?** No training. The data gate is Section "Gate" below.
3. **Was the test valid?** Real scheduled tip, entry stamped at or before T-60 with a stored age, DNP scored at Kalshi's fair price (not dropped),
   integer support is native (Kalshi lines are x.5 on integer stats), fee from a verified object, clustered by game and date, power stated with its
   assumptions, self-tests including a shuffle null and a planted edge (guard 8.6). Known weaknesses: the eligibility filter (spread <= 5c) removes
   26% of otherwise-priced contracts, so the claim is about tradeable quotes only; the intra-class correlation is assumed, not measured; the 15-minute
   capture is irregular (stale nights below).
4. **Lineage.** Descends from KALSHI_LONGSHOT_FORWARD (same plumbing, wider price range) and from MARKET_SURFER_PAPER (its trading arm lost; this is the
   hold-only version). It is not the long-shot bias test: that is the overlapping band.
5. **What does it score against?** Realised net dollars per contract, not CRPS or Brier. A Brier edge against the price is a different question
   (KALSHI_VS_PINNACLE, MARKET_ANCHORED_PROPS).

## Data-capture audit and minimum-data gate (non-negotiable 8; counts only, from a COPY of `data/kalshi/kalshi.duckdb` taken 2026-10-24T03:1xZ)

What exists. `kalshi_prices` live rows from 2026-10-08, regular-season props from 2026-10-20. Prop and combo markets with a known tip on regular-season
games 2026-10-20..23: **6,847 markets over 28 games** (5,733 with a result stored; 27 `scalar`). Tip = occurrence - 180 min (the LONGSHOT draft
verified this against the schedule on 352 of 352 events).

| Gate item | Measured | Verdict |
|---|---|---|
| Entry snapshot, max age 240 min (the rule) | 6,288 of 6,847 markets (91.8%) | Passes guard 8.1 (70%) |
| max age 30 min | 3,846 of 6,847 (56%) | Stale nights, below |
| Eligible after filters (bid >= 1c, spread <= 5c) | **4,462 contracts in 28 of 28 games** (about 159 per game); 1,664 dropped as wide spread, 162 as bid zero | Rate source |
| Eligible and with a result stored | 3,717 contracts, 28 games | |
| By series | PTS 1,060, 3PT 998, REB 943, AST 827, STL 280, PRA 217, BLK 137; PR/PA/RA 0 | Combos: PRA only |
| By YES-mid bucket (n contracts / games) | U1 321/27, U2 391/28, U3 380/28, U4 874/28, U5 995/28, U6 838/28, U7 463/27, U8 200/27 (191 below mid 0.95 plus 9 above) | Every bucket populated; the long-shot band holds 1,092 (24%) |
| Pinnacle prop rows at T-60 (`live_book_odds`, book pinnacle) | 1,282 rows, 147 players, 17 of 28 games (61%) | The Pinnacle axis exists for roughly 60% of games; the rest is `no_pinnacle` |
| Our forecast at prediction time | `market_at_prediction` forward rows: 14,415 across 28 games | The z axis covers every game; stat-sum combos may be `unknown` |
| DNP settlement values | 27 scalar of 5,733 (0.47%), values in raw settled pages; `kalshi_markets` has no column for it | Scorer reads raw until `nba/kalshi` stores it |

Stale-capture nights (the 15-minute job ran irregularly; gaps are between consecutive 15-minute slots seen in `kalshi_prices`):
- 2026-10-20 (opening day): gaps of 450 min (to 11:00Z) and 390 min (to 17:30Z); entry coverage at 240 min 92.6%.
- 2026-10-21: one 195 min gap (ends 22:30Z); coverage 94.8% at 240 min but only 51.6% at 30 min.
- 2026-10-22: no gap over 45 min; 94.3% at 240 and at 30 min.
- 2026-10-23: gaps of 255 min (ends 18:30Z), 195 min (21:45Z) and 150 min (2026-10-24 00:15Z); 88.4% at 240 min and only 43.3% at 30 min.
These nights pass guard 8.1 individually but show why max age 240 is the rule and S2 (age <= 30) is a sensitivity: on 10-21 and 10-23 about half of the
entry prices are 30 minutes to 4 hours old, so the "T-60 price" is partly a stale price. Nothing is done about it here (ops path is not mine); it is repairable
(a launchd heartbeat, an independent watchdog, cost small) and is open choice 7.

Seasons and sources not used: preseason capture (resting-player regime); Kalshi `/historical` (hourly candlesticks only, no top-of-book at T-60), sportsbook
prices (a different market); excluded by Devin's forward-only choice. A `/historical` backfill is a separate question with its own rule.

### Gate: can the minimum n be reached, and when?

Observed fraction of scheduled games with at least one eligible contract: 28 / 28 = 1.0 (the LONGSHOT draft's 0.82 was for contracts at mid <= 15c; with
all prices a game always has contracts). The date projection is the LONGSHOT draft's, driven by `data/schedule/live_tips_2026-27.parquet` from 2026-10-25
(not recomputed here): 150 games on 2026-11-14 and 300 on 2026-12-13 at fraction 1.0; 300 on 2026-12-23 at 0.8; 300 on 2027-01-08 at 0.6. The 300-game and
50-date gate passes at Read 1 (2027-01-15) with margin if capture is at least 60% reliable, and the final read has about 900 games. **Gate on counts: PASS.**
**Gate on power: HONEST-WEAK** for edges under about 2.3c at Read 1, about 1.1c at Read 2 (Section 6). Cost per contract against which an edge must clear:
mean fee about 1.3c (0.07 x mean P(1-P)) plus half the spread (about 1-1.5c); the pooled hurdle is therefore about 2.5-3c gross, and the data cannot
rule out a net edge of +1c at Read 1. Gate failure on data has NOT been declared: no season, source or repair beyond those listed would change the count.

### Disclosure of what was seen (before this draft was committed)

- The audit printed counts only: eligibility, entry ages, buckets, series, coverage. It computed NO return, no fee, no spread-adjusted payoff and no mean mid.
- It also printed the result column's value counts on all 5,733 settled regular-season prop markets (any price, filter-free): no 3,588, yes 2,118, scalar 27,
  an unconditional YES share of 37.1% of 5,706 binary results. No mid was printed beside it, so it is not a calibration, but it is outcome information
  and is disclosed. It is excluded from every scored number (the scoring window starts 2026-10-25).
- The author also knows the LONGSHOT draft's gross glance (realised YES below price in all three long-shot buckets on 775 contracts, 13 games, 10-20..23) and
  Devin's exploratory live week. The prior below is conditioned on those; they are outside the window.

## Open choices for Devin (default in bold)

1. Alpha across the two rules: **Bonferroni, 0.005 / 0.02 each**; alternative: freeze UNDERS alone at 0.01 / 0.04 and treat LONGSHOT as its descriptive band
   (cleaner, but then the long-shot band gets a second chance at a PASS).
2. Decision scope: **pooled R only, strata descriptive**; alternative: name the cell "z > +0.5 and D = 0" a second confirmatory hypothesis (Holm, m = 2), which
   raises power for the FAN_KNOWLEDGE effect and halves the level again.
3. Floor: **+1.0c per contract net**; alternatives 0.5c or 2c (2c matches the Read 1 detectable edge).
4. Entry max age: **240 min**; 120 min would cut coverage on the current capture.
5. Fee rounding: **unrounded** (no stakes); per-contract rounding is S1.
6. Game markets: **excluded** (they belong to LONGSHOT and SPREAD_TAIL_NO); including them mixes a different question into a props rule.
7. Capture reliability: **Devin or the ops owner schedules a heartbeat/watchdog so the 15-minute job cannot go dark**; not done here.
8. Paper Kelly column: **kept, display only** (Section 9); alternative: drop it (DECISIONS 2026-10-09 said no sizing prompts). It cannot change a verdict either way.

## Amendments (append only, dated)

(none)
