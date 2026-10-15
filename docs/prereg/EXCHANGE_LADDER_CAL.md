# EXCHANGE_LADDER_CAL: is the model's P(stat >= k), calibrated with the LINE_CALIBRATION map, honest on Kalshi and Polymarket player-prop ladders? (pre-registration)

Status: **DRAFT, written by the market agent Thu Oct 15 00:37:10 CDT 2026 (clock read by `date`). NOT frozen. The minimum-data gate cannot be measured yet (see "Gate"); by non-negotiable 8 the rule is not frozen until it can.** No fit, no score, no outcome read.
- **How it becomes the rule:** the main session commits `prereg(EXCHANGE_LADDER_CAL): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/EXCHANGE_LADDER_CAL.md | shasum -a 256`.
- **Advice or momentum:** Devin's override of the order (DECISIONS 2026-10-15 00:28: "Let's do 1, 2 3 4 and 5 now"; Claude recommended #1 first), on a question that DECISIONS 2026-10-14 13:12 itself named ("Devin's venues get a forward-only rule on Kalshi/Polymarket ladders from the us_ex capture, from 10-20"). It is not a recommendation to bet. The standing answer stays "keep your money" until a live log proves skill.
- **Venue label:** Devin's venues (Kalshi, Polymarket). ALT_LINE_70 / ALT_LADDER_HIST speak for a sportsbook bettor and cannot say what an exchange ladder pays.
- **Read-only:** public GETs and the Odds API `us_ex` region only. No order, portfolio or execution endpoint, no credential in any file. Paper-trade log only.

## The question
LINE_CALIBRATION Stage 1 (T493, frozen a5441242) found that a market-free logistic map corrects production's P(over) at Pinnacle main lines (CAL minus A0 log loss -0.0117 [-0.0139, -0.0096], n 37,528 legs / 1,260 games; CAL minus C0, the base rate, -0.0037 [-0.0047, -0.0027]). Two things are not known:
1. Does that map hold on a ladder rung ("k+", the line k - 0.5) on an exchange, where rungs sit far from the model's median and the listed players are stars? ALT_LINE_70 / ALT_LADDER_HIST found overconfidence at 70%+ rungs at sportsbooks (-7.0% and -5.7% returns; threes worst), so extrapolation along the ladder is the risk.
2. Does an exchange-specific refit (small, 2 parameters) do better than the frozen map once enough exchange legs exist?

"Honest" means calibrated, not "beats the price". Whether the calibrated probability beats the price is a separate, label-only row; the EV row is secondary.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: **Quantile and distribution layers (recalibration at the line)**; the stored forecast underneath is the production context-residual quantile layer (gradient-boosted trees). Nothing is fitted in this draft.
1. **Information or capacity?** Neither: a map of a stored forecast. No new pre-tip information. Expectation is therefore small and mostly "less overconfident", not "sharper".
2. **Was the training data enough?** The frozen map is fit on 72,674 Pinnacle/DraftKings legs of 2023-24 (T477 count). The exchange refit has no history at all (no Kalshi or Polymarket player-prop rows exist for seasons <= 2024, `docs/research/lines_everywhere_2026-10-14.md` A3); it accumulates only live. Hence the refit is two parameters and gated on its own fit count.
3. **Was the test valid?** Real tip gating (forecast `made_at` <= tip - 60 min and stamped before the quote is read), integer support (P(Y >= k) from the stored `p_ge`, not a continuous approximation), no price in any fit, shuffled-label null sized before the freeze (K = 50, as LC), planted-future check, power computed below. Population caveat: exchange rungs are chosen by the exchange (stars, rungs near the median, roughly 10 to 40 points); the result applies to listed rungs only.
4. **Lineage.** This is LINE_CALIBRATION's CAL applied out of its fit support, plus CALIBRATED_GRID's lesson (a shrinkage control beats raw by construction, so "beats raw" is cheap). Carried forward: walk-forward only, game-clustered bootstrap seed 0 B = 2,000, shuffled null, planted future, BH over stats. Dropped: Pinnacle-only primary (exchanges have no Pinnacle quote); isotonic (too many parameters for live n).
5. **What does it score against?** Log loss of P(stat >= k) at the listed rung, vs raw production (A0) and vs the walk-forward base rate (C0). The exchange price is a reference row, not the target.

## Data exposure (honest)
Seen before drafting: LC Stage 1 summary numbers (T493); T477 (Kalshi preseason prop counts, "24 points ladders on 2 priced games"; re-measured below as 24 rung markets, 6 player ladders, 3 events, of which 16 markets had a two-sided quote before close); the Odds API vendor documentation. Not seen: any outcome of any Kalshi or Polymarket ladder (the gate counts `result IS NOT NULL`, never its value), any live forward forecast scored against an exchange. Season 2025 is never loaded. The confirmatory venue is entirely forward, so exposure to history is not a concern for the verdict; it is a concern for the design constants (power uses T493's CI width, cited).

## Rule

### Units and populations
- **Leg:** (venue, game, player, stat, k) where the venue listed the rung "k+" before the forecast `made_at` (T - 60 min) and the market settled. One leg is scored once per venue (a rung listed on both venues counts on each; the pooled primary de-duplicates by (game, player, stat, k) and keeps the Kalshi row).
- **Forecast:** production `props_context_residual` v2 stored row with `made_at` <= tip - 60 min, the T-60 arm (the T-30 lineup arm is a shadow and is not used). P(Y >= k) is the stored integer-support `p_ge`.
- **Eligible stats:** pts, reb, ast, fg3m (the four LC stats). PRA/PR/PA/RA/STL/BLK ladders are counted and reported, not scored (the LC map was not fit on them).
- **Support filter (fixed now):** a leg is IN SUPPORT if its `z` (the LC definition, from the continuous 199-point grid) lies within the 1st to 99th percentile of z over the LC Stage 1 fit rows. Primary = in-support legs. Out-of-support legs are reported separately, no pass.
- **Regular season only** for the primary (2026-10-20 to 2027-04-12). Playoffs are a separate regime and are reported alone.
- **Excluded, counted:** DNP/void legs, unmatched names (`UnmatchedKalshiNameError`; candidates go to a dated candidates file for the maintainer, never the live alias table), legs with no stored forecast, legs listed after `made_at`.

### Arms (all market-free: no price enters any fit)
- **A0:** raw production P(Y >= k).
- **CAL:** the LC map as frozen by a5441242, fit through season 2024 only (the contract of commit 9c43882: no season-2025 labels until Devin logs a training-use row), Pinnacle T-60 legs, applied at line k - 0.5 with terms `[1, x, z, bench, bench*x]` and the per-stat fit. Frozen for the whole season: **no refit on exchange data inside CAL.**
- **C0:** the weighted base rate of the same fit rows (the shrinkage control that took a third of LC's gain).
- **EXCH (secondary):** 2-parameter intercept and slope on logit(CAL), ridge-shrunk to (0, 1), fit walk-forward by calendar month on all earlier settled exchange legs of that venue. Eligible only once at least 600 earlier legs from at least 150 earlier games exist; before that EXCH = CAL.
- **SH_k, k = 1..50:** CAL's fit with shuffled fit labels (the LC null), sd declared before the freeze.

### Primary metric and checks
Mean per-leg log loss, in-support legs, four stats pooled, venues pooled, every leg weighing the same. CI: paired 95% percentile bootstrap, 2,000 resamples of **games** (seed 0), date-clustered CI beside it. Same-game dependence is real (same-player legs co-move; a ladder is nested), so the game is the cluster and a ladder is never split.
- **P1 (gain over raw):** CAL minus A0 <= **-0.005**, CI upper < 0.
- **P2 (calibration):** (a) |mean(CAL) - realised rate| <= 0.03; (b) the calibration slope of the outcome on logit(CAL) (game-clustered 95% CI) contains 1.
- **P3 (label only, no pass, reported in the verdict wording):** CAL minus C0, CI. At the power below it is mostly inconclusive; if its CI upper >= 0, the verdict says the map is "honest but the model adds no demonstrated information beyond the base rate on these rungs".
- **P4 (no stat worse):** no stat has CAL minus A0 with CI lower > 0 (BH over m = 4 for the secondary table only).
- **P5 (guards):** shuffled null (SH_k) separates from CAL by at least 2 null sd on the pooled primary, else the verdict is "gain not distinguishable from shrinkage"; planted-future check passes.

### Verdicts (stated before any leg is scored)
- **HONEST:** P1, P2, P4, P5 pass at the final look with n at or above the minimum.
- **NOT_HONEST:** CAL minus A0 CI lower > +0.003 (the map hurts), or P2(a) fails with the realised rate outside the band and a CI that excludes the band edge.
- **INCONCLUSIVE:** n below the minimum at the final look, or neither of the above. Claims nothing.
- A **separate first-class line** is always reported: `no_positive_ev_found` or the count of flagged legs (secondary outcome below).

### Minimum settled-leg count (set from the gate, below)
**At least 400 games with at least one usable settled priced leg and at least 1,500 usable settled legs, pooled.** A per-venue statement (Kalshi alone, Polymarket alone) needs the same minimum for that venue; otherwise only the pooled number is stated and the venue split is "insufficient sample". Below the minimum the report says "insufficient sample" and claims nothing (configs/kalshi.yaml `min_settled_markets: 30` is the global floor for any superiority claim; this rule's floor is higher by design).

### Look dates (pre-declared; days counted from tip, a game counts if it tipped at least 1 day before)
- **L1 2026-12-15:** descriptive only (counts, listing rate, reliability table, spread distribution). No pass, no kill.
- **L2 2027-02-15:** may **KILL** only: CAL minus A0 CI lower > +0.003 at a 99% game-clustered interval, with n at the minimum. Cannot pass. Rationale: one-sided early stopping for harm only, so repeated looks do not inflate a pass.
- **L3 2027-04-15 (regular season complete):** the only look that can produce HONEST / NOT_HONEST at 95%.
- The rule does not re-estimate its sample size or change a floor after any look.

### Kill and inconclusive rules
- **KILL (harm):** at L2 or L3, CAL minus A0 CI lower > +0.003. CAL is not applied to exchange ladders; reported as a finding.
- **KILL (venue):** if by 2026-10-27 the `us_ex` capture returns no Kalshi/Polymarket player-prop rows and the direct Kalshi snapshot lists fewer than 5 prop events in 7 days, the rule is CLOSED as DATA (after the non-negotiable-8 audit of every season we hold, which for exchange props is none, and every source we could add, which is the direct Polymarket gateway at `gateway.polymarket.us`, public, no key). It is not a variant.
- **INCONCLUSIVE:** n below the minimum at L3. Closes as DATA, with the projected-vs-actual count in the report.
- **No second chance by variant:** one pre-registered follow-up at most (the EXCH refit, if its own gate passes), then closed.

### Secondary outcomes (no pass; labelled)
- **Price rows:** exchange mid at the last quote with `received_at` <= `made_at` (age <= 30 min; Kalshi direct snapshots are 15-minute, `kalshi.duckdb`; us_ex via the Odds API are displayed prices with no depth). Reference row: CAL minus MID log loss, with CI and n. Expected: the price wins.
- **EV after fee and spread (secondary only, the "keep your money" check):** for each in-support leg, both sides priced at the executable quote (Kalshi: buy YES at `yes_ask`, buy NO at 1 - `yes_bid`); the fee is `nba.parlay.ev.ev_per_contract` with the `FeeModel` loaded from `configs/parlay.yaml` (taker, `verified: true`, source "user-provided from Kalshi published fee schedule, 2026-10-08", coefficient read from the config, never typed here); rounding convention: the fee is computed at 100 contracts per order only so per-order cent rounding is negligible (a notional for rounding, not a stake). EV uses CAL's probability with a probability interval from the game-clustered bootstrap of the calibration slope; flagged only if the interval's lower bound clears 0 after fee and spread. **No stake, no sizing.** Count flagged legs; `no_positive_ev_found` if zero. A hit is annotated "this one hit; it was still a -X% bet" in any display.
- **Polymarket EV:** not computed. `configs/parlay.yaml` has no Polymarket fee object. The vendor page (https://docs.polymarket.us/fees.md, fetched 2026-10-15) states a taker `Fee = theta x C x p x (1 - p)` with theta 0.0695, banker's rounding to $0.01, effective 2026-10-07, maker and taker rebate tiers changing 2026-10-19; this is documentation, not a verified config entry. Until Devin adds a verified entry (draft below, `verified: false`), Polymarket legs contribute to calibration only. Even then the Odds API price is a displayed price with no spread; EV would be an upper bound.
- **EXCH minus CAL** (once eligible), per venue and pooled.
- **Reliability table** in ten bins, calibration in the large by stat, by rung depth (z terciles), by venue, by starter/bench.
- **Alias review:** matched / unmatched / candidates, listed each look.
- **Paper-trade log:** with n on every comparison, written to the separate shadow store (never `kalshi.duckdb`, never `nba.duckdb`).

Draft fee config entry (for Devin to verify; not added by this draft): `fee_model_polymarket: {name: polymarket_us_fee, formula: p_one_minus_p, coefficient: 0.0695, verified: false, source: "https://docs.polymarket.us/fees.md fetched 2026-10-15", rounding: bankers_half_even_cent, effective_from: 2026-10-07}`. The current `FeeModel` rounds up (`ceil`); Polymarket rounds half-even, so the code needs a rounding field before this can be used.

## Gate (non-negotiable 8)

Gate script: `research/eval/exchange_ladder_cal_gate.py`; output: `reports/prereg_exchange_ladder_cal/gate.json` (sha256 f3400d155a2a3d93c74589e91501b4d911cb01d75b82f8a7b555534f89e6cfcb; inputs hashed inside). Measured Thu Oct 15 00:41:38 CDT 2026 (`date`; exact stamp `run_at_local` 00:41:37 inside the json). About 5 s, read-only, the live Kalshi DB opened as a copy with its WAL. No outcome value read.

**What is captured so far (Kalshi direct snapshots, `data/kalshi/kalshi.duckdb`, last snapshot 2026-10-15 05:28:46 UTC):**
| Item | Value |
|---|---|
| Preseason KXNBAGAME events (game codes before 10-20) | 64 |
| Preseason prop events (ladders exist) | 3 (4.7% of games) |
| Preseason prop markets / player ladders / rungs | 24 markets, 6 player ladders, 4 rungs each (10, 15, 20, 25), points only |
| Preseason markets with a two-sided quote before close | 16 of 24; mean spread at the last quote 0.33, max 0.85 |
| Finals 2026 (KXNBAPTS + KXNBAREB) | 5 events, 200 markets, median 45 markets/event, thresholds 2 to 40; `player_id` NULL on all (not mapped) |
| Stored forecast rows for preseason games | 1 game (0012600038, 34 players, 136 rows, tip 2026-10-10) |
| Games with BOTH a stored forecast and a Kalshi ladder | **0** (ladders on 10-05 and 10-08; the forecast game tipped 10-10) |
| Paired settled legs available today | **0** |
| `us_ex` Kalshi/Polymarket player-prop rows | **0** (no 2026 live raw directory; the only Kalshi/Polymarket rows in `market_asof.duckdb` are 2026-10-10 h2h rows from the old vendor theoddsapi.com) |
| Polymarket NBA player-prop structure (lines per player, ladders or single line) | unknown until the first live pull |

So today's measured paired count is 0, against a minimum of 400 games / 1,500 legs. The T477 sentence "24 points ladders on 2 priced games" is re-read here as 24 rung markets on 3 events.

**Projected counts from the 2026-27 schedule** (`data/schedule/live_tips_2026-27.parquet`: 1,200 games listed, 2026-10-20 19:00 UTC to 2027-04-12 00:30 UTC; 14 to 56 games per 7-day block, median 52; the file has 1,200, not 1,230, reported as found). Usable fraction 0.80 is an assumption chosen before scoring (alias matched x forecast stored x two-sided quote x in support); none of its parts is measured.
| Scenario (listing) | usable games / legs at 2026-12-15 | at 2027-02-15 | at 2027-04-15 | first date both minima met |
|---|---|---|---|---|
| L: preseason rate (4.7% of games, 8 markets each) | 12.7 / 102 | 30.7 / 245 | 45 / 360 | never |
| M: every game, preseason depth (8 markets) | 271 / 2,170 | 654 / 5,235 | 960 / 7,680 | 2027-01-06 |
| H: every game, Finals depth (45 markets) | 271 / 12,204 | 654 / 29,448 | 960 / 43,200 | 2027-01-06 |

**Power** (y-free; uses T493's CI width as the only outcome-derived input, cited: half-width 0.0022 at 1,260 games gives a per-game sd of 0.0398; sensitivity x1.5 for fewer, nested legs per game). Floor -0.005, pass line min(-0.005, -1.645 se):
| Games | se (x1.0 / x1.5) | power if true gain = T493's -0.0117 | = half of it (-0.006) | = CAL-C0 size (-0.0037) |
|---|---|---|---|---|
| 400 | 0.0020 / 0.0030 | 1.00 / 0.99 | 0.69 / 0.63 | 0.26 / 0.33 |
| 600 | 0.0016 / 0.0024 | 1.00 / 1.00 | 0.73 / 0.66 | 0.21 / 0.30 |
| 960 | 0.0013 / 0.0019 | 1.00 / 1.00 | 0.78 / 0.70 | 0.16 / 0.25 |

**Gate verdict: NOT MEASURABLE YET.** The binding unknown is the regular-season listing rate on Kalshi and Polymarket. Preseason (4.7% of games, stars only) would fail by construction; the Finals depth suggests the regular season could be far deeper; nobody has seen a regular-season night.
- **First date it can be measured: 2026-10-21** (the first night's `us_ex` capture and the direct Kalshi snapshots: games listed, rungs per player, Polymarket structure).
- **Date it is confirmed: 2026-10-27** (first full 7-day block, 47 + 45 games): the rule passes its gate if the 2026-10-20 to 10-26 listing rate projects at least 400 usable games by 2027-04-15 (scenario M or better). Otherwise it is closed as DATA after the audit above.
- If it passes, the rule has adequate power for a gain half the size of LC's (0.63 to 0.78), and no power at all to tell CAL from the base rate (0.16 to 0.33), so P3 is a label, not a pass.
- **The rule cannot be frozen before 2026-10-27.** It can still be committed as a draft today.

## Data-capture audit (non-negotiable 8)
| Source | Held | Gap | Repair cost |
|---|---|---|---|
| Kalshi direct (read-only snapshots, 15 min) | 2026-06 Finals (KXNBAPTS 124, KXNBAREB 100 markets), 2026-10 preseason (24 markets) | regular-season props unknown; Mac sleep loses about 2% of 15-minute slots; `player_id` NULL for Finals (no alias review then) | none to run; alias review each look |
| Kalshi historical (`/historical`, settled) | `data/rehearsal/kalshi_props_2025.duckdb`: 61,105 prop markets 2026-01-17 to 2026-06-14, about a fifth with a `player_id` | season-2025 data: not loaded here (holdout); not a venue for this rule | n/a |
| Odds API `us_ex` | none (capture starts 2026-10-20; `NBA_CLOSE_CAPTURE=1`) | Polymarket/Kalshi/Novig/ProphetX/BetOpenly player props: zero history <= 2024 | ~14% of the plan per month, already decided (DECISIONS 2026-10-14 12:31) |
| Polymarket direct (`gateway.polymarket.us`, public, no key) | none | whether NBA player props carry ladders: unknown | an adapter beside `nba/kalshi`, read-only; build only if the `us_ex` look shows listings |
| Model forecasts | production T-60 rows for forward games; preseason: 1 game | no forecast for the 10-05/10-08 ladder games | none (opening night is the start) |

## Priors (binary events; PREDICTIONS_LEDGER rows drafted in the report, not appended)
Written Thu Oct 15 00:37 CDT 2026, before any exchange leg is scored. Disclosure: I have seen LC Stage 1 (T493) and the ALT_LINE_70 / ALT_LADDER_HIST summaries, so priors are not blind.
1. P(the rule's gate is confirmed on 2026-10-27, i.e. the first week projects at least 400 usable games by 2027-04-15) = **0.45**. Why not higher: preseason shows 4.7% listing; props "post game day, close to tip"; Polymarket structure unknown.
2. P(n at or above the minimum at L3 | frozen) = **0.40**.
3. P(CAL minus A0 <= -0.005 with CI upper < 0 at L3 | n at the minimum) = **0.45**. Why: LC's gain was large on main lines (-0.0117), but rungs far from the median are out of the fit's centre and ALT tests show overconfidence at 70%+.
4. P(CAL minus MID CI upper < 0, i.e. the calibrated model beats the exchange mid) = **0.04**.
5. P(at least one leg flagged with positive EV after fee and spread, interval lower bound above 0) = **0.12**; P(`no_positive_ev_found` is the final Kalshi verdict) = 0.88.
6. P(HONEST at L3) = 0.40 x 0.45 x P(P2, P4, P5 | P1) 0.8 = about **0.14** overall.
7. P(CAL minus A0 CI lower > +0.003 at L2 or L3, i.e. KILL for harm | n at the minimum) = **0.08**.

## Open questions for Devin
1. Confirm the primary is calibration (P1 + P2), not beating the price.
2. Confirm floor -0.005 and the minima (400 games, 1,500 legs). The 400-game minimum came from the power table, not from the data.
3. Is the LC map to be served as the frozen a5441242 fit through 2024 (the contract of 9c43882), or the Stage 2 map after a 2025 training-use row? This rule assumes through 2024.
4. Add a verified Polymarket fee entry (or decline; Polymarket then stays calibration-only).
5. The `FeeModel` rounds up; Polymarket rounds half-even. Needs a rounding field before any Polymarket EV.
6. `player_id` is NULL for Finals Kalshi markets: the alias review must run on the first regular-season night, not at L1.
7. LC map availability: an applyable artifact of CAL (the fit through 2024) is a dependency; I could not confirm one is shipped.

=== RESULTS BELOW ===
(none; no leg has been scored)
