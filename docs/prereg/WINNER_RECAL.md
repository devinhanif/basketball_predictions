# WINNER_RECAL: does a walk-forward recalibration of production's game-winner probability narrow or close the gap to Kalshi's price? (pre-registration)

Status: **DRAFT, written by the market agent Thu Oct 15 00:37:10 CDT 2026 (clock read by `date`). NOT frozen, and the draft recommends it is NOT frozen as a pass/fail test: it FAILS its own power gate (non-negotiable 8). See "Gate verdict" and the options.** No fit, no score, no outcome read.
- **How it would become the rule:** the main session commits `prereg(WINNER_RECAL): <sha256>` of everything above `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/WINNER_RECAL.md | shasum -a 256`.
- **Advice or momentum:** Devin's override of the order (DECISIONS 2026-10-15 00:28, item 5 of "1, 2, 3, 4 and 5 now"; Claude recommended #1 first). Claude's own recommendation on this item: do not run it as a pass/fail test (gate below). Flagged here because CLAUDE.md asks it.
- **Read-only:** public Kalshi GETs and stored Odds API rows only; no order, no credential, no execution code.

## The question
Our game-winner probability (production `rung0_injury_elo` v2, MOV-Elo logit plus official injury-report terms, refit daily on earlier games) is behind the market. 2025-26 replay (docs/REPLAY_2025_26.md, exploratory): Brier model 0.2039 vs Kalshi mid 0.1975, paired model minus market +0.0064 [+0.0023, +0.0104]; log loss +0.0172 [+0.0077, +0.0269]; n 2,622 market rows = 1,311 games x 2 complementary markets, 207 date clusters (the two rows per game are one game, so the effective n is 1,311). On 2023-24 against de-vigged Pinnacle at T-60 (T235/T236): +0.0212 [+0.0126, +0.0300] (2023, n 1,318) and +0.0107 [+0.0006, +0.0211] (2024, n 1,315).

Can a Platt or isotonic map on production's probability narrow that gap? Two ways it could: the model is miscalibrated (a map would fix it), or the market simply knows more (a map cannot fix it). The record says the second: WINPROB_FAMILY (T178) fit Platt, slope-only Platt, beta and isotonic walk-forward on production's 2022-24 out-of-fold probabilities (n 3,951 games): production is already calibrated (ECE 0.024); slope-only Platt -0.0002 (CI spans 0); isotonic +0.0034 (worse); no calibrator kept. This draft asks the same question against the price, on live data, because the gap is what we care about.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: **Ratings (MOV-Elo, injury-Elo)** with a **Linear/logistic** recalibration layer on top.
1. **Information or capacity?** Neither: a 2-parameter (Platt) or monotone (isotonic) map of one stored number. It adds no pre-tip information. The playbook says the market's edge over Elo sits in "late information it cannot see (close games and back-to-backs)"; a map cannot supply that. Expected effect is therefore close to zero by construction.
2. **Was the training data enough?** Yes for Platt (3,951 out-of-fold games), marginal for isotonic. Elo-class models gain nothing from more seasons (ELO_HISTORY_WINDOW null).
3. **Was the test valid?** Real tip gating (forecast and price both stamped before tip; the price must be read at or before the forecast's `made_at`); no price in any fit (market-free recalibration; the market is only the benchmark); no season-2025 data; game- and date-clustered CIs; shuffled-label null and planted-future guard as in LC. One validity issue I cannot remove: the historical seasons are already read for this exact contrast (see "Data exposure"), so the only clean test is forward.
4. **Lineage.** WINPROB_FAMILY's calibrator arms (T178) are this model class, on the same out-of-fold probabilities. They failed their rule (R2: log-loss CI wholly below 0, or ECE improvement) on 2022-24. Carried forward: fit on earlier out-of-fold only, game-clustered CI. Changed: the comparator is now the price, the venue is the live log, and Kalshi 15-minute snapshots replace de-vigged Pinnacle. Closest in spirit: MARKET_SHRINK / MARKET_UPDATE (the market as a prior), which is a different question and not a recalibration.
5. **What does it score against?** Log loss (primary; MARKET_FEATURE says "Win: log loss primary, Brier secondary") and Brier of P(home win), against the raw production probability and against the Kalshi de-vigged mid at prediction time. "Gap" means model minus market in log loss.

## Data audit (non-negotiable 8): which seasons have game-winner prices, and are they known-at?
Script: `research/eval/winner_recal_gate.py`; output `reports/prereg_winner_recal/gate.json` (sha256 c47cae38f37513fc3f6e4f2d368312c983b34149b933077725bfb213694f865d). Measured Thu Oct 15 00:41:38 CDT 2026 (`date`; exact stamp `run_at_local` 00:41:38 inside the json). Read-only; `home_win`/`y` never selected (asserted).

| Source | Seasons | Pairs with a stored production p | Known-at |
|---|---|---|---|
| Odds API h2h, Pinnacle (eu), `t60` capture | 2023 (2023-24): 1,267 games; 2024 (2024-25): 1,279 | 1,267 / 1,279 | all snapshots before tip: lead 62 to 64+ min (median 64); price after the model's `made_with_data_through`: 0 violations |
| Odds API h2h, consensus (all books), `t60` | 2023: 1,318; 2024: 1,315 (Pinnacle gaps: 51 and 36 games filled by consensus, as T235/T236 did) | 1,318 / 1,315 | same |
| Same, `t5` capture | Pinnacle 1,302 / 1,292; consensus 1,318 / 1,315 | | lead median 9 min; a closing proxy only, never the primary (rule: price at prediction time, not the close) |
| Kalshi `KXNBAGAME`, historical hourly candles (separate DB `data/rehearsal/kalshi_2025.duckdb`) | season 2024 only from 2025-04-14: 86 playoff/play-in games, 172 markets, 7,422 candle rows, last 2025-06-20; **no 2024-25 regular season** | model p not paired here (candle `ts` is the end of the hour, so a pre-tip price can be up to 60 min stale) | candles only; bid/ask at the hour close |
| Kalshi `KXNBAGAME`, live 15-min snapshots (`data/kalshi/kalshi.duckdb`) | 2026-27 preseason: 50 of 64 game events had a two-sided quote before close (78%) | model p for preseason: not paired | 15-minute cadence, about 2% of slots lost to sleep |
| Seasons 2019-2022 | no game-winner prices of any kind in the store (`odds_history` starts 2023; Kalshi game winners start 2025-04) | | n/a |
| Season 2025 (2025-26) | exists in the stores; **never loaded here** | | n/a |

Model p: `reports/odds_history/game_rows_{2023,2024}.parquet` (stored production out-of-fold; `max_abs_dp_vs_stored_realtip` = 0.0), `made_with_data_through` 450 min before tip (median). Out-of-fold probabilities also exist for 2022 (`data/injury_elo/oof_predictions.parquet`, model `rung0_injury_elo`), but no 2022 price. Select/report seasons 2023-24 **do** have prices with known-at times, but both are already exposed to this very contrast (below). So there is no clean historical select/report pair.

## Data exposure (honest)
- **Season 2025 (holdout): the 0.0064 Brier gap came from it.** REPLAY_2025_26 read 2025-26 Kalshi prices and outcomes against production's replayed p (exploratory, "already-spent season"), and the GA-tune holdout row (T182) read 2025 for MOV-Elo. This draft does not load 2025; it inherits the finding, the direction and the size, so priors below are not blind.
- **Seasons 2023-24:** T235/T236 measured raw production vs Pinnacle (the gap, with outcomes); T178 measured Platt/beta/isotonic on production's out-of-fold probabilities (with outcomes). Both seasons are therefore read for "does a calibrator help" and "how big is the gap". The confirmatory test is the 2026-27 live log.
- **Incidental 2025 touches by this run (no price, no outcome, nothing used):** (a) an exploratory `GROUP BY season, snapshot_kind` count on `odds_history.duckdb` did not filter `season <= 2024`, so per-season game counts of 2025 (1,313 to 1,315 per snapshot kind) were seen; (b) a `GROUP BY game_id` over `prop_predictions` in `nba.duckdb` listed game ids and row counts including 2025 replay ids. A drafted HOLDOUT_ACCESS_LOG disclosure is in the report for Devin to append or reject. The gate script itself enforces `season <= 2024` / `close_time < 2025-08-01` in SQL.

## Rule (as it would be frozen; see the gate verdict first)

### Unit and population
- **Unit:** a regular-season 2026-27 game (2026-10-20 to 2027-04-12) with a stored production P(home win) stamped at or before tip - 60 min **and** a two-sided Kalshi quote for both team contracts at or before that forecast's `made_at`, quote age <= 30 min, bid-ask <= 0.30 (the bid-ask filter is F19's; F19 allowed age <= 2 h on hourly candles, the live 15-minute snapshots allow a tighter 30 min). Preseason, play-in and playoffs are reported separately and excluded from the primary.
- **Price:** Kalshi `KXNBAGAME` de-vigged mid (`devig_two_way`), last quote with `ts <= made_at`. Secondary: Pinnacle `t60` from the live Odds API capture when it exists (the capture is 12 points per game; the one nearest at or before `made_at`).
- **Counted, not dropped:** games with no ticker match, no two-sided quote, a stale quote, an unmatched team abbreviation.

### Arms (market-free: no price enters any fit)
- **A0:** raw production P(home win).
- **R1 (primary): Platt.** Logistic regression of the outcome on logit(p), 2 parameters, ridge toward (0, 1) with a fixed penalty declared in the harness before the freeze; fit on `rung0_injury_elo` out-of-fold rows of 2022-2024 (no 2025 labels, the 9c43882 contract) plus every live 2026-27 game settled before the prediction date; refit in calendar-month blocks, weights `0.5 ** (age_days / 365)`.
- **R2 (secondary): isotonic** on equal-mass bins blended with Platt, as WINPROB_FAMILY defined it; same walk-forward.
- **C1:** a constant (the weighted home-win base rate) and **SH_k, k = 1..50**, R1 fit on shuffled labels (sized null, as LC).

### Primary metric and checks
Per-game log loss, R1 minus A0 (so it equals the change in the gap to the price), regular season, one game one row. CI: paired 95% percentile bootstrap over 2,000 game resamples (seed 0); a date-clustered CI beside it.
- **P1 (narrows):** R1 minus A0 <= **-0.002**, CI upper < 0.
- **P2 (no harm by slice):** the playoff slice is not in the primary; the b2b and home-favourite slices each have a point estimate <= +0.001.
- **P3 (guards):** the shuffled null separates from R1 by at least 2 null sd; planted-future passes; the gap row below is stated.
- **Gap rows (label, with CI):** A0 minus market; R1 minus market; closure share = -(R1 minus A0) / (A0 minus market), reported only if the gap CI lower bound is above 0.
- **Verdicts:**
  - **CLOSES:** P1, P2, P3 pass and closure share >= 0.5 with the gap CI lower > 0.
  - **NARROWS:** P1, P2, P3 pass and closure share < 0.5.
  - **NO_EFFECT:** anything else with n at the minimum. The served object stays raw.
  - **HARMS:** R1 minus A0 CI lower > +0.001. Kill; the Platt layer stays off.
  - **INSUFFICIENT SAMPLE:** n below 600 priced games at the final look; claims nothing.
- Minimum **600 priced games** at any look; none at fewer.

### Looks (pre-declared)
- **2027-02-15:** may KILL (HARMS at a 99% interval) or report INSUFFICIENT; cannot pass. About 695 priced games projected (0.85 coverage assumption: replay matched 1312/1316 = 0.997, live preseason 78%).
- **2027-04-15:** the only binding look; about 1,020 priced games projected (1,200 listed x 0.85).
- No sample-size re-estimation and no floor change after a look. At most one pre-registered follow-up, then closed.

### Secondary outcomes (no pass, labelled)
Brier, ECE and reliability (ten bins), slices (home favourite, b2b, month), isotonic, the Pinnacle comparison, and a **fee-aware EV row**: buy the team contract at the ask, fee from the verified `configs/parlay.yaml` `FeeModel` via `nba.parlay.ev.ev_per_contract` (taker, coefficient read from config), spread included; count flagged games, `no_positive_ev_found` if none. No stake, no sizing. A hit is annotated "this one hit; it was still a -X% bet".

## Gate (non-negotiable 8)
Y-free sizing (script section 5): under "the market probability is the truth", the per-game variance of a log-loss difference is `q(1-q) (logit p_b - logit p_a)^2` plus the variance of its per-game expectation. The **ceiling effect** is the best monotone intercept-slope map of p if the market were the truth (a map fit to the market, used only as an upper bound; it never enters a candidate).
| Season (pairs) | ceiling map | ceiling gain (R1 minus A0) | se at 1,020 games | pass line | pass power at the ceiling | raw-minus-market gap, y-free expectation / se |
|---|---|---|---|---|---|---|
| 2023 (1,267) | logit' = 0.061 + 0.886 logit | -0.00142 | 0.00156 | -0.00257 | **0.23** | +0.0136 / 0.0052 |
| 2024 (1,279) | logit' = 0.023 + 0.907 logit | -0.00075 | 0.00121 | -0.00200 | **0.15** | +0.0157 / 0.0056 |

(At 600 games power is 0.17 and 0.12. The minimum detectable effect at 1,020 games is 0.0039 / 0.0030, vs the -0.002 floor.)
- The ceiling closes about 10% (2023) and 5% (2024) of the gap. Any map fit to outcomes instead of to the market closes less. The remaining 90 to 95% is information the model lacks, not a calibration error.
- The gap itself, raw minus market, has se 0.0052 to 0.0056 at 1,020 games: it can be shown to exist (point 0.011 to 0.021), and a recalibrated model's gap CI cannot be told from the raw one's.
- Criterion (draft convention; ROAD_STARS and RETURNING_TEAMMATE failed their gates at power 0.13 to 0.19 and were not frozen): pass power >= 0.50 at the ceiling effect.
- **Gate verdict: FAIL (power 0.15 to 0.23).** The rule as written cannot pass its own gate, so it is not frozen.

Options for Devin:
1. **Close it from the record (recommended).** T178 (calibrators on production: no gain, ECE already good) plus this y-free ceiling (at most 5 to 10% of the gap) answer the question without a live run; keep serving raw production; the gap is information, not calibration. Zero cost.
2. **Freeze a no-harm-only rule.** Keep the live log running as a descriptive row: report R1 minus A0 and the gap with n and a clustered CI, no pass line, verdict is only HARMS or NO_EFFECT. Cheap; claims nothing positive.
3. **Freeze as written despite the failed gate (precedent: ROAD_STARS, Devin's call).** Expected null at pass probability about 0.03.
4. **A different question, not a recalibration:** market-as-prior shrinkage (MARKET_SHRINK/MARKET_UPDATE lineage). This is where a gap to the price can be narrowed, because it adds the price's information; it is a new id and a new rule.

## Priors (binary events; PREDICTIONS_LEDGER rows drafted in the report, not appended)
Written Thu Oct 15 00:37 CDT 2026, before any live game is scored. Disclosure: I have seen the 2025-26 gap (REPLAY_2025_26), T178 and T235/T236, so these are not blind.
1. P(the rule passes its own gate as drafted) = **0.03** (measured FAIL, 0.15 to 0.23 power).
2. If run: P(R1 minus A0 <= -0.002 with CI upper < 0 at the final look) = **0.05**.
3. P(R1 minus A0 point estimate < 0 at the final look) = **0.55** (the ceiling is slightly negative; sampling noise at se about 0.001 to 0.0015 swamps it).
4. P(raw production is behind the market at the final look, point estimate of A0 minus market log loss > 0) = **0.93**; with CI lower > 0 = **0.65**.
5. P(closure share >= 0.5 with gap CI lower > 0) = **0.01**.
6. P(at least one game flagged positive EV after fee and spread, bound above 0) = **0.05**; P(`no_positive_ev_found` is the winner-market verdict) = 0.95.

## Open questions for Devin
1. Which option (1 to 4) for the rule. I recommend 1; if you want the live log to measure it anyway, 2.
2. Is the pass-power convention (0.50) the one you want recorded? No repo convention was found; 0.5 is mine.
3. The live game-winner forecast row name and table for 2026-27 (I assumed `forward_predictions` with model `rung0_injury_elo`, `nba/daily/predict.py` `INJURY_ELO_MODEL_NAME`); no regular-season row exists yet, so I could not verify its columns.
4. The 2025-26 Kalshi price file is `data/rehearsal/kalshi_2025.duckdb` (hourly candles). If you want a 2025-based Stage, that is a holdout touch needing a logged row first; I did not do it.
5. Pinnacle in the live capture needs the `eu` region (in `configs/odds_live.yaml` regions); confirm it stays on.

=== RESULTS BELOW ===
(none; no game has been scored)
