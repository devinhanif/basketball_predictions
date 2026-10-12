# MARKET_UPDATE: start from the T-60 market price and adjust only for news that arrived after it (pre-registration)

Status: DRAFT 2026-10-11. Not frozen. Nothing is fitted or scored by this document. It freezes only by a hand that can commit,
after Devin confirms, after the pre-freeze live gate G-FREEZE below passes, and before any live outcome is looked at.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_UPDATE.md | shasum -a 256`.
Seasons: history 2023 (2023-24) and 2024 (2024-25) is used for the coverage gate, for the descriptive diagnostic D and to FIT frozen
coefficients, never to report a verdict. The report set is the season-2026 (2026-27) live forward log ONLY. Season 2025 is never loaded.
Origin: Devin, 2026-10-11, approved the reframing "make the forecast an update: start from the market line as the prior and adjust only for
information that arrived after the line was set."

## Why
Evidence so far, all in this repo: the market beats our game-winner model; MARKET_SHRINK (frozen b5522fe, run 6b9907f, T415-T419) shrank the
model toward the DraftKings T-60 price and found `no_positive_ev_found`; MODEL_CLV (run a832d61) found no price CLV worth a bet. Yet
`reports/market_slow_2026-10-11.md` (commit 3f5363a, descriptive) shows where the market still learns after T-60: when a line moved between
T-60 and the `t5` capture (10.2% of lines) the move agreed with the outcome 57.8% [57.0, 58.6] (n legs 262,629 joined; 2,633 games); price-only
moves were 50.6% [50.4, 50.9], noise; our T-60 lean predicted the direction of the move by +2.2 points over a shuffled null but price CLV was
about 0. So the information that matters arrives after T-60 (reports, lineups), and a forecast that redoes the whole job loses to a price that
already contains most of it. This rule asks the narrower question the evidence invites: does post-T-60 news, entered as a small correction on the
market logit, improve on the T-60 price, and if so does it improve on the price the market itself reaches?
Priors (mine): conditions 1-5 pass for the lineup arm U2 about 35%; U2 beats the t5 price about 4%; U1 (reports alone) reaches its n_min at
all about 25%. INJURY_ELO_T30 (7dc8b7b) found the T-30 inactive list adds nothing to win probability beyond the T-60 report (2024 -0.0008
[-0.0044, +0.0026]); LINEUPS_KNOWN (c24cc85) found confirmed starters improve OUR props distribution (CRPS -1.0 to -1.4%), but that was against
our own T-60 model, not against a market that also reprices at T-30. Devin's prior: not yet stated.

## Information boundary (the rule's core)
- **Prior time** = the T-60 price's own `snapshot_ts` (about tip - 64 min), per game. **Decision time** = tip - 30 min.
- A feature is **news** iff its `known_at` satisfies prior time < known_at <= decision time. Everything at or before prior time is already in
  the price and is excluded by construction (the model's own forecast `p_model` is NOT an input; this is an update of the market, not of us).
- Report news: a `player_availability` row (`source='nba_official_report'`) with `as_of` in the window (`as_of` is naive US-Eastern; the price and
  tip times are converted to ET). Lineup news: the lineup collector's last snapshot with `fetched_at` <= tip - 30 min, `game_status` = scheduled.
- Every feature table carries `known_at`; the harness asserts `known_at <= decision time` for 100% of rows and `known_at > prior time` for the
  news rows before any arm is fit (planted-future test: a row stamped after decision time is injected and must change no feature).

## Fixed design
Legs: DraftKings `t60` rows of the pairs frames `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` (history) and the live `live_book_odds`
`t60` snapshot (live), stats pts / reb / ast / fg3m, two-sided half-point main line, de-vigged multiplicative P(over) `m` (= `p_over_mult`), outcome
= the over event (no pushes). Players whose own report status at decision time is OUT are dropped and counted (the book voids them); DNP-after-the-fact
legs are dropped as in the historical pairs. Pinnacle is descriptive only (same fits, no pass role).
Arms (logit of the over probability, clipped to [0.005, 0.995]; `z0 = logit m`):
- **U0** market prior alone: p = m. No fit.
- **U0c** recalibrated prior, no news: `z = a_s + b_s z0`, per stat s. The control that stops a slope/intercept fix passing as news.
- **U1** = U0c + report news F1.   **U2** = U1 + lineup news F2.   (U2 is the primary arm.)
- **PLACEBO** = U2 with the news vectors (F1, F2) permuted among legs of the same (game_date, stat), 20 permutations (seeds 1..20), same fit and
  scoring pipeline; the reported placebo is the mean of the 20 deltas.
- **D** (history only, diagnostic, no pass role, never a feature): U0c + signed line move and price move from T-60 to `t5`. Measured at the gate:
  history has only two snapshot kinds per game (`t60`, `t5`), no intermediate price, so a move observed at `t5` (about tip - 9) is NOT
  actionable at T-30. D says how much of the market's learning a perfect-hindsight logistic could recover; fit 2023, scored 2024, descriptive.
- **Shuffled-label check**: the outcome permuted within game, the whole pipeline re-run: no arm may show a gain (delta >= 0 on average).
F1 (report news, all as-of): `f1_own_up` (+1 if own status improved, e.g. questionable -> available/probable; -1 if worsened to
questionable/doubtful), `f1_tm_out_min` (sum over same-team players newly OUT/DOUBTFUL of their mean minutes over their previous <= 10 games
strictly before the game date / 48), `f1_tm_back_min` (same, newly cleared), `f1_opp_out_min` (opponent newly OUT). Plus the indicator
`has_post_report` (a report row exists in the window) so that "no report" is never confused with "report, no change" (the NULL-pattern lesson).
Team is the player's team as of his latest prior box row (trade guard as in INJURY_ELO_T30).
F2 (lineup news): exactly `nba.props.lineup_features.T30_FEATURES` (`t30_starter`, `t30_n_changed`, `t30_n_usual_absent`, `t30_vac_starter_min`,
`t30_start_delta`), computed from the collector's announced starters live. Plus `has_lineup_snapshot`.
Fit: per stat, binary log loss, ridge on the news coefficients only (lambda fixed at 1.0 on features standardised by the fit window, none on a_s, b_s),
L-BFGS. No grid, no tuning. Fit window: all history legs of 2023 and 2024 for U0c / U1; for U2 the F2 columns come from the box-score starter flag
(the post-hoc PROXY: it violates the known-at boundary by construction and is biased upward, see reports/lineups_known.md), so the history F2 fit is
declared train/serve skew; the coefficient file (JSON) and its sha256 are written and committed BEFORE the first live scoring, and are never refit.
The live skew check (announced vs box-score starters on collected games) is reported beside every result.

## Scores (live report set; history reports none except D and the diagnostics above)
- Primary: paired binary log loss, arm minus comparator, per leg, game-clustered bootstrap 95% CI (2000 draws, seed 0). Secondary: Brier,
  ECE (10 bins), reliability with n. Pooled over the four stats (one test); per stat descriptive.
- Comparators: U0 (as the instruction requires), U0c (news-only effect), PLACEBO, and **the `t5` price** on unchanged-line legs
  (`T5`: the de-vigged DraftKings price about 55 minutes later, which has digested the same news).
- **Decision score** (reported, never a pass condition), reusing `nba/markets/decision_scoring.py` unchanged: ROI per unit at EV >= 5% (0, 2, 10%
  descriptive) with p = the arm's probability, n, hit rate, game-clustered CI, beside the market-lean and random placebos at the same count.
  **Price available at decision time**: there is NO T-30 price (history: `t60`, `t5` only; live capture: `t60` and `close` kinds only, checked 2026-10-11),
  so the decision is scored at (i) the `t60` price, which is stale by 34 minutes and is an UPPER BOUND on realism (the market moves toward the news we
  condition on, on the very legs where we have news: adverse selection runs against us), and (ii) the `t5` price on unchanged lines, which is
  feasible (lineups are known at T-30) but already contains the news. Truth lies between; the verdict never rests on (i). If Devin approves a live
  `t30` capture kind (an ops change, not made here: about 1 additional credit-bundle per game), the decision price becomes the `t30` price and (i) is dropped.
- "True odds" are displayed identically after wins and losses; a hit is annotated "this one hit; it was still a -X% bet". No stakes, no sizing.

## Hypotheses and pass condition (live, DraftKings, family = U2 vs U0, one pooled test; U1 secondary)
A: U2 improves on the T-60 price. B: the improvement is news, not recalibration, and survives the placebo. C: it survives the price the market
reaches. Conditions for A and B, all required, on the report set once n_min (below) is met:
1. Log loss: U2 - U0 <= -0.0010 (practical floor), 95% CI upper bound < 0.
2. News not recalibration: U2 - U0c <= -0.0005, CI upper bound < 0.
3. Placebo: U2 - PLACEBO CI upper bound < 0, and PLACEBO - U0c CI contains 0.
4. Brier: U2 - U0 point estimate <= 0; ECE (10-bin) delta <= +0.005.
5. No slice with n >= 300 news legs has U2 - U0c > +0.005. Slices fixed now: starter / bench (T-60 `starter10`), report-change legs vs no-change legs,
   stat, lineup-flip legs (`t30_start_delta` != 0) vs none, game start time (afternoon / evening / late).
6. Guards (fail = stop, before any arm is scored): known-at assertions 100%; planted-future injects no change; missingness audit (news-feature NULL rate
   by stat / start time / month reported; `has_*` indicators present; a NULL pattern that predicts the outcome is a leak and stops the run); the shuffled-label
   pipeline shows no gain.
Condition C (verdict tier, not a pass of A/B): U2 - T5 on unchanged-line legs, CI upper bound < 0 with floor -0.0005.
Verdict per arm: `insufficient_sample` (n_min unmet: claim nothing) | `no_information_beyond_prior` (1-6 not all met: **no_positive_ev_found, defer to the
market**) | `information_priced_by_close` (1-6 met, C not: the market learns the same news; keep your money) | `beats_close_candidate` (1-6 and C: a
shadow candidate for a longer live log only; promotion, money and stakes are Devin's). A clean "no" is a result.

## Gate (counted 2026-10-11 by `research/eval/market_update_eval.py gate`; no arm scored, no parameter fit; reports/prereg_market_update/gate.json)
Legs: 270,733 T-60 legs (DraftKings 106,999; Pinnacle 72,674; FanDuel 91,060) in 2,633 games, 40.9 DK legs per game. Window = prior time < as_of <= tip - 30.
(a) **Post-T-60 report news**: DK legs whose game has any report row in the window **4.29%** (4,594 / 106,999; 2023 3.77%, 2024 4.82%); whose game has a
status change 3.98% (4,262); whose OWN player's status changed **0.34%** (367 legs; 2023 141, 2024 226). All books: 4.26% / 3.95% / 0.34% (11,542 / 10,694 / 931 of
270,733). Game-level: 138 of 2,633 games have a change. Only two reports per day are stored for 2023-24 (11:00 and 17:00 ET), so a post-T-60 report exists
only for early-evening games and almost never otherwise. Change types (all books, row = player-game): none -> out 179, questionable -> out 104,
questionable -> available 97, probable -> available 51.
(b) **T-30 lineup snapshots**: history 0 of 2,633 games (collector began 2026-10-09; 13 games so far, all preseason). The only 2023-24 starter data is the
post-hoc box-score flag, which violates the known-at boundary, so F2 cannot be evaluated on history.
(c) **Intermediate prices**: 0. 2023 and 2024 hold `t60` and `t5` for every game and nothing in between; the live capture log has `t60` and `close`
kinds only (6 rows, all `unmatched`: preseason, no vendor event; `live_book_odds` has 0 rows today).
Consequence: **history cannot carry a verdict** (U1 on history would rest on about 60-70 report-change games per season and a 0.34% own-player rate;
F2 and any T-30 price do not exist there). The rule runs on the live 2026-27 log.

## Sample size and where it runs (stated before any live data)
Unit of evidence = DraftKings legs with lineup news (own starter flag flipped vs his previous game, `t30_start_delta` != 0, or a usual starter absent),
because that is where U2 differs from U0c; all other legs add noise equally to both arms. Planning rate: 5% of legs (conservative; LINEUPS_KNOWN
measured 8.8% of rows with a status change on the box-score proxy) x 40.9 legs per game = 2.0 news legs per game. Capacity as in REB_LIVE_2026_27: about 49 games
per week, opening night 2026-10-20 (week 1).
- **n_min = 1,500 DK lineup-news legs AND >= 500 games with a T-30 lineup snapshot and a DK `t60` capture**, both. Below that, every number is
  reported as `insufficient_sample` with its n and claims nothing. (Reasoning: with a per-leg log-loss change of about 0.05 on news legs and
  zero elsewhere, game clustering design effect about 2, 1,500 news legs give a CI half-width near 0.0008 on the pooled delta, the size of the floor;
  the 0.05 is an assumption and the first interim read is only the count.)
- Week reached (news legs / week = 2.0 x 49 x capture): at 5% rate and 100% capture **week 15 (ends 2027-02-01)**, at 85% capture **week 18 (ends 2027-02-22)**;
  at the 8.8% rate week 9-11 (the 500-game floor binds at week 11, ends 2027-01-04). U1 (post-T-60 reports, game-level change rate 4.0% on the 2023-24 cadence):
  1,500 changed-game legs at week 19 (100% capture, ends 2027-03-01) to week 23 (85%): it is secondary and most likely `insufficient_sample` unless the 2026-27
  report cadence is denser than twice a day; own-player report changes (0.34%) would need week 74+, so they are never a standalone arm.
- Interim looks: none for decision. One count-only status at weeks 6 and 12 (n, coverage, skew), no scores, no stopping on results.
- **G-FREEZE (before freeze; count only)**: (i) DK `t60` prop capture matched for >= 80% of games over the first 14 days of the regular season (preseason shows
  0 matches: `no unique vendor event`); (ii) a lineup snapshot with `fetched_at` in [tip - 45, tip - 25] for >= 90% of games; (iii) the live report cadence
  measured (post-T-60 report rows per game) and weeks recomputed with the formula in `weeks_to_n`; if the projected week for U2 exceeds 25 the rule is
  frozen as a descriptive log with no verdict. If (i) or (ii) fail the rule is not frozen (non-negotiable 8).

## Data exposure (honest)
Seen before drafting: the market_slow and decision_scoring reports, the MARKET_SHRINK / MODEL_CLV / ODDS_HISTORY results, LINEUPS_KNOWN and
INJURY_ELO_T30 results, and the coverage counts above. NOT seen: any U0c / U1 / U2 / placebo / D log loss, Brier or ROI. Season 2025 never loaded
(the harness asserts `season <= 2024`); `nba.duckdb` opened `read_only=True`; `data/ops/heavy.lock` is never taken or removed (another job holds it).

## What each choice protects against
- Prior = the market, model forecast excluded: protects against re-fitting what the price already knows (MARKET_SHRINK null).
- Known-at window with planted-future test: protects against a later report or the post-hoc box-score starter leaking into a "T-30" feature.
- U0c control and the within-date placebo: protect against a recalibration, or a feature-count effect, being read as news.
- `t5` comparator and the stale-price statement: protect against crediting us for news the market digests within the hour (market_slow: +0.030 x100 Brier).
- D as diagnostic only, never a feature: a move seen at `t5` is not actionable at T-30.
- n_min with the week it is reached: protects against a verdict from a thin sample (non-negotiable 8; as REB_LIVE).
- Verdict tiers: `information_priced_by_close` is a successful, publishable "no".

## Outputs (code after the freeze; only the gate exists now)
`research/eval/market_update_eval.py` (gate now; `fit`, `score` stages after the freeze, using `nba/markets/decision_scoring.py` unchanged), tests in
`tests/research/eval/test_market_update_eval.py`, `reports/prereg_market_update/gate.json`, `reports/market_update.md`, ledger rows with T### placeholders.
The gate takes about 20 seconds and takes no lock.

=== RESULTS BELOW ===
