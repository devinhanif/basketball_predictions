# CALIBRATION_EVERYWHERE: plan (scoping only; nothing in the live path changes)

Status: PLAN, written 2026-10-23 night by the market agent on `agent/market-calibration-everywhere`. Devin's "yes" (DECISIONS 2026-10-23 21:38,
follows Claude's recommendation). This document changes no code path that runs on game night, edits no frozen rule, appends no ledger row.
The one code addition, `nba/parlay/cal_scope_gate.py` (+ its test), is a read-only, label-free counting tool for section 3.

The question: the raw props model is overconfident (logit slope about 0.46 at sportsbook lines; Luka 2+ threes showed 0.993 against an 86c ask).
We already hold two validated recalibrations. Where may each be applied, where would applying it be an extrapolation, and how do we get
every consumer to read one calibrated number without disturbing anything that is frozen on the raw definition?

Short answer: a calibrated number is not an edge. Calibration makes our probabilities honest; it does not make them better than the price
(T494: the gain over a reliability table is the book's main line, "not model skill"). On the scope data below, calibrating shrinks the
apparent disagreement with Kalshi roughly in half (173 to 92 of 283 markets with a gap of 5c or more) and the verdict stays `no_positive_ev_found`
until a forward log says otherwise. Keep your money.

## 1. What each map was validated on (the declared scope)

| | LINE_CALIBRATION (T493) | ALT_RUNG_CAL (T494) |
|---|---|---|
| Frozen | a5441242 / commit 06d76e3; map `nba/parlay/line_cal_map_v1.json` sha 443743a1 | 425e4153, pin 7eb08466; map `nba/parlay/rung_cal_map_v1.json` sha 116f4480 |
| Object | P(over) at the book's MAIN line, terms `[1, x, z, bench, bench*x]`, `z = (line - median)/spread` | P(over) at a RUNG of the book's ladder, terms `[1, x, zm, r, r*x, bench, bench*x]`; `zm` = book main line vs model median, `r` = rung distance from main |
| Books | Pinnacle and DraftKings, one fit per (book, stat) | DraftKings only (FanDuel: label only, no map) |
| Stats | pts, reb, ast, fg3m | pts, reb, ast, fg3m |
| Snapshot | T-60 (`odds_history` t60; production lead 64 min median) | T-60 |
| Lines | x.5 main lines (whole-number lines refused) | x.5 alternate rungs, served only where raw P(over) >= 0.5 (`FIT_SUPPORT_MIN_P`) |
| Data | 2023 + 2024, walk-forward by month, select 2024; static map fit through 2025-06-22; 2025 never loaded | 2023-24 pooled, 134,462 rungs / 1,818 games; replicates in each season |
| Result | CAL minus raw log loss: Pinnacle -0.0117 [-0.0139, -0.0096], n 37,528 legs / 1,260 games; DK -0.0095 [-0.0109, -0.0081], n 53,752. CAL minus base rate (C0) only -0.0037: a third is shrinkage. fg3m carries 54% of the gain | DK CAL-rung minus raw -0.0106 [-0.0118, -0.0094]; minus C0 -0.0066 [-0.0076, -0.0057]; every stat, bench and starters. Calibrated in every raw-P bin 55 to 95 pooled over stats (per-cell gaps <= 0.012 where eligible) |
| What it is NOT | Not pts line information (T493 attack). Input support: raw P 0.22 to 0.71 (x 1st-99th percentile -1.29 to +0.89). **Applied to rungs it is WORSE than raw (+0.044 log loss, T494 CAL-main)** | Not model skill: the main line carries 92% of the gain (shuffling it kills it). Fit rungs sit at or below the main line: `r` 1st-99th percentile -1.45 to 0.0 (pts to +0.2) spread-units, so rungs ABOVE the main line, and raw P < 0.5, were never fitted |
| Companions | T495 PARLAY_JOINT_CAL: with CAL legs the DK joint (same-game and cross-game tickets) is CALIBRATED in 4 of 4 cells; the panel's TOP picks still run 3-5 pts high (selection). T496: no tail fix (NOT_FIXED) | none |

Never run, for either map: a 2025 touch (Stage 2), a 2026-27 forward confirmation, Kalshi or any exchange price, FanDuel/BetMGM/other books,
combo stats (PRA, PR, PA, RA), steals/blocks, win/spread/total legs. T-60 only: live production forecasts are written once a morning and the
live main line is the one at or before that run, a timing difference the maps never saw (carried as a caveat everywhere below).

## 2. Inventory of every consumer of a raw model probability

"Raw" is not one definition. Four exist in the tree, all stored, none calibrated:
(a) `p_ge_full` counts/n, integer support from the 199-point grid (export, top parlays, T510, paper trader, scorecard, LLM payload; **the definition both maps were fit on**);
(b) `p_ge` thresholds, same samples, limited thresholds (FORWARD_PREREG scoring);
(c) `QuantileGridDist` on the stored 19-point `q_grid`, piecewise-linear CDF (the Kalshi parlay engine: `slate.load_slate`, `joint.leg_marginal`, `analysis._shadow_p`, assistant);
(d) the parametric `nba.props` distributions (`legs.p_yes`, older path).
(c) differs from (a) by 0.0044 on average (n 40,555 stat-threshold pairs, 2026-10-20..23), p95 0.020, max 0.025, and runs +0.011 high where P > 0.95 and -0.004 low where P < 0.2.
So the accessor must standardise on (a) before it applies a map: calibrating (c) would apply a map to an input it was not fit on.

Scope key: **IN** = inside what the map was validated on; **IN (caveat)** = inside, with the T-60 timing caveat; **OUT** = extrapolation or no map.

| # | Consumer (file) | Probability used now | Applicable map | Scope |
|---|---|---|---|---|
| 1 | Top parlays panel (`parlay/top_source.py`, `top_parlays.py`, `top_history.py`; app `TopParlaysPanel`) | `p_cal` = LC map on (a) at each DK/Pinnacle quoted main-line leg; ranks and joints on `p_cal`; `p_raw` shown beside | LC (already live) | **IN (caveat)**. T495 covers the joint (DK). Pinnacle cells are IN for the map but T495's Pinnacle joint was 2 of 4 INCONCLUSIVE. Tail legs (p_cal >= 0.65) are flagged, not fixed (T496) |
| 2 | App export `line_cal` (`export/line_cal_fields.py`), card slider `CardDetail`/`slider.ts` | LC at the DK main line, bit-identical to #1 | LC | **IN (caveat)**. Already live |
| 3 | App export `rung_cal` (`export/rung_cal_fields.py`) | rung map on the persisted 199 grid, only lines with raw P >= 0.5 | rung | **IN**. Already live, already support-restricted |
| 4 | App lists and game pages: `Today.tsx`, `Game.tsx`, `ledger.ts`, `leg.ts` (`model_p_over`, `compareLine`, API `app/api/.../main.py:142 lp.p_over`) | RAW P(over) at the displayed line; "Our model X% over" next to the book's % | LC if line = DK main; rung map if a listed rung with raw >= 0.5 and `r` in band; else show raw labelled "uncalibrated" | **Raw today = the overconfident number on the front page.** Calibrated version is IN at DK main lines and listed rungs; every other line is OUT. Not under `nba/` (app owner), so only a contract is specified here |
| 5 | Explainer pages (`explain/page.py`, `data.py`, `render.py`): "we said X% over, the market priced Y%" | RAW `p_over(surv, line)` at the T-60 main line of Pinnacle, else DraftKings, else a "consensus of k books" median line | LC (Pinnacle, DraftKings cells) | Pinnacle/DK lines **IN**; "consensus of k books" lines **OUT** (no such map). If a replay page shows 2023-24 games that is the map's own fit window: an in-sample display, and must say so |
| 6 | Briefings (`explain/slate.py`, `daily/report.py`) | win probability, means, P(play); no stat-threshold P(over) is printed | none for wins (WINNER_RECAL gate FAIL_POWER, closed); none for P(play) | **Nothing to calibrate.** Wins stay raw and defer to the market (2025-26 Brier +0.0064 vs Kalshi) |
| 7 | Kalshi single legs, `parlay evaluate` / `analysis.py` / `budget.py` / `narrate.py` / `report.py` (`ev.evaluate`, `price_singles`) | (c) via `leg_marginal`; `raw_model_prob`, `raw_ev`, `raw_ev_low`; the verdict uses `p_use` = market while `model_weight` = 0 | rung map (Kalshi N+ is the rung "line N-0.5") or LC when N-0.5 equals the DK main | See section 3: of 1,001 Kalshi prop markets on 10-20..10-23, **83 at the DK main (IN), 183 in the rung fit support (IN, but never tested on an exchange), 18 outside the band, 390 raw P < 0.5 (OUT), 327 with no DK main at T-60 (nothing to apply)** |
| 8 | Kalshi joints and combos (`joint.py` copula marginals, `top_parlays` same-game tickets, `analysis` combos) | (c) marginals + pair correlations estimated on the raw PIT | marginal calibration as in #1 (T495) | **IN** for DK main-line legs (T495). **OUT** for Kalshi rung legs and mixed tickets; cross-game legs: residual r about 0, product of calibrated marginals is fine once each is IN |
| 9 | Shadow log (`parlay/shadow.py`, `shadow_predictions.raw_model_prob`, `track_record`, `TrackRecord.gated_skill`) and the market-deferral gate (`configs/parlay.yaml`: `min_settled_for_claims` 30, `min_settled_dates_for_claims` 14) | raw model prob vs market mid, scored by Brier, game-clustered lower bound | not applicable to the frozen column | **Frozen on raw (do not change).** Add a parallel calibrated column in a NEW table; the gate keeps scoring raw (section 4) |
| 10 | Paper trader (branch `agent/market-paper-trader`, `nba/papertrade`, rule `paper-trader-v2`, config digest; **not merged to main**) | `p_ge_full` (a), blended `0.25 * model + 0.75 * Kalshi mid` (the 0.25 is itself a hand recalibration, evidence T480 slope 0.15-0.19 and ODDS_HISTORY H2) | rung map / LC by scope | Rule is DRAFT with a placeholder freeze date; Devin "to reconfirm before the first scored run". Calibrating AND blending 0.25 double-shrinks. Treat as a new rule version `paper-trader-v3` (section 4), not a silent swap |
| 11 | Bayesian-Kelly sizer (branch `agent/market-bayes-kelly`, `nba/parlay/bayes_kelly.py`; not merged) | caller-supplied `p` (centre = Pinnacle de-vig else mean of sources) and `p_sd`; no model probability is read inside | in-scope calibrated p only | Advice-only tool; "no stakes, no sizing" (DECISIONS 2026-10-09) governs. It must refuse a raw model p and a p whose scope is OUT, so the "sure edge" case cannot be built from an overconfident number |
| 12 | Assistant (`parlay/assistant/*`, `numguard.py`) and Claude's ad hoc recommendations (the 10-23 19:1x row: raw 0.993 vs 0.86 ask) | (c) marginals via `toolbox.leg_marginal`; `raw_model_prob` rendered as "hypothesis only" | accessor | Every number printed must come from the accessor with its scope label; OUT numbers are printed as "no validated calibration, raw shown only as a hypothesis" |
| 13 | Forward scorecard (`forward/scorecard.py`: model vs Pinnacle/DK at the line, Brier/log loss, game-clustered CI) | (a) at Pinnacle/DK main lines | LC | **IN** for a calibrated column BESIDE the raw one; raw stays (a diagnostic, not a claim test) |
| 14 | LLM shadow payload (`shadow_llm/payload.py`: `p_over_prod`, rule LLM_SHADOW_2026_27) | (a) | none | **Frozen, do not touch.** The payload is a comparator, "nothing else is sent" |
| 15 | Frozen forward rules: FORWARD_PREREG_2026_27 (CRPS / threshold log loss of the shadow arms), T510 LINE_MOVE_TOWARD_MODEL (`p_M = p_ge_full c[k-1]/n * p_play`), T511 FAV_DIP_PAPER and T514 STAR_FLOOR_PAPER (historical hit rates, no model probability), the MARKET_ANCHORED_PROPS and KALSHI_VS_PINNACLE_PAPER drafts | raw (a)/(b), or no model probability at all | none | **Frozen; unchanged by construction** (section 4) |

Not consumers (checked): `nba/markets/decision_scoring.py` and `market_slow.py` (historical scoring harnesses on stored arrays), `daily/settle.py`
(scores the stored grid), `props/*` (producers). `props_context_residual_int/_lt/_t30/_t15`, AVG, combo and full shadows are alternative forecasts
with their own frozen scoring; no map was fit on any of them (the LC/rung maps are "a map of the PRODUCTION model's P(over)"), so they are OUT
for calibration and stay raw. The export deliberately builds `line_cal`/`rung_cal` from the production row even when the displayed primary is AVG; keep that.

## 3. Measured scope on real data (label-free; `nba/parlay/cal_scope_gate.py`)

Window: Kalshi prop markets (pts, reb, ast, fg3m series, `player_id` mapped) on regular-season slates 2026-10-20..10-23 (10-23 partial), 28 games with a production forecast
at or before T-60, season 2026 only (2025 never loaded). Inputs read-only: `nba.duckdb`, `data/markets/market_asof.duckdb`, a byte copy of `data/kalshi/kalshi.duckdb`
(the live snapshot writer held the lock; copy including the WAL), and the fit-band arrays `reports/alt_rung_cal/arrays_draftkings.npz`. Settled = `result IS NOT NULL`; no result value read.
Category order: no forecast, no DK main (no two-sided DK main line at or before T-60, or no persisted 199 grid), at main (Kalshi line = DK main), in support (raw P >= 0.5 and `r` in the DK fit band), below half (raw P < 0.5), outside band.

| Category | Kalshi markets | Settled | pts | reb | ast | fg3m | Verdict on the map |
|---|---|---|---|---|---|---|---|
| at DK main line | 83 | 58 | 8 | 23 | 15 | 37 | LC applies (IN, T493). The rung map also applies |
| in rung fit support | 183 | 139 | 43 | 55 | 39 | 46 | Rung map is inside its fit region, **but never tested on an exchange price or label** |
| outside band (P >= 0.5, `r` below -1.45 etc.) | 18 | 12 | 1 | 7 | 9 | 1 | OUT: the stars' deep rungs (the 0.99 cases) |
| below half (raw P < 0.5) | 390 | 297 | 108 | 90 | 85 | 107 | OUT: the map was never fit below 0.5; includes every cheap-YES long shot and its high-priced NO |
| no DK main at T-60 | 327 | 305 | 74 | 86 | 76 | 91 | Nothing to apply: both maps need the book main line as an input |

So 266 of 1,001 (27%) markets are IN scope as of today; 39% are below half and 33% have no main line.
Games with at least one in-scope leg: 11 of 28 (39%), 24.2 in-scope legs per such game, 4 nights. DK-side counts at the same clock: main two-sided legs with a forecast 885 in 18 of 28 games
(49 per game); alt rung legs with raw P >= 0.5 and x.5 lines 1,794 in 18 games (100 per game).

Descriptive only (no outcomes, the market is not the truth): mean of raw / rung-calibrated / Kalshi mid at T-60 is 0.427 / 0.469 / 0.486 at the main line (n 83),
0.752 / 0.767 / 0.746 in support (n 182), 0.954 / 0.930 / 0.865 outside the band (n 18). By raw bin: 0.9 to 0.97 raw 0.944 -> cal 0.898 vs mid 0.819 (n 35); above 0.97 raw 0.985 -> cal 0.936 vs mid 0.876 (n 8).
Luka 2+ threes (LAL, 10-23): raw 0.975 -> 0.918 vs mid 0.875, **still above the price after calibration, and outside the fit band (r -1.32 to -1.54), so it is an extrapolated number**: not an edge, not evidence of one.
Gaps of 5c or more between the model and the Kalshi mid fall from 173 to 92 of the 283 at-main, in-support and outside-band markets; the sign of (p - mid) flips on 72.

## 4. Out-of-scope uses: the smallest pre-registered test, and its minimum-data gate

### 4.1 The test: one forward rule, five cells, no refit (name proposed: `CAL_FORWARD_2026_27`; drafted by the modeler/market agent, frozen by the main session)

Not drafted here as a rule file: it needs Devin's yes and an adversary pre-freeze read first. Outline (a draft for that review):
- **Maps served as frozen** (LC sha 443743a1, rung sha 116f4480, fit through 2025-06-30; no refit, no 2025 training-use row). Forecast: production row, newest with `made_at` <= tip - 60 min; line: the DK main at or before that clock; P from `p_ge_full` (definition (a)).
- **Cells** (each scored on log loss of the calibrated P against the outcome, CAL minus raw, game-clustered 95% CI, plus calibration slope and gap; BH across the five; floor -0.005 for the "helps" word, calibration band |gap| <= 0.03 and slope CI containing 1 for the "honest" word):
  - **A** DK main lines, LC (transport of T493 to 2026-27; the confirmation T493 itself asked for);
  - **B** DK alternate rungs in support, rung map (transport of T494);
  - **C** Kalshi N+ where N - 0.5 = DK main, LC (the exchange price is a reference row; the label is the stat outcome);
  - **D** Kalshi N+ in rung fit support, rung map (the extrapolation that matters; supersedes the CAL arm of the EXCHANGE_LADDER_CAL draft);
  - **E** Kalshi below half and outside band: descriptive, **no pass word**, because no map was fit there.
- **Cells A and B come first on purpose**: if the 2023-24 maps do not transport to 2026-27 at DraftKings, a Kalshi result cannot be read.
- **Superseding a draft arm.** `docs/prereg/EXCHANGE_LADDER_CAL.md` (draft, never frozen, gate date 10-27) applies the LC main-line map at exchange rungs. T494 (written an hour after that draft) measured exactly that as worse than raw (+0.044 log loss). Amend the draft before any freeze: replace its CAL arm with the rung map for rungs and LC at the main line; keep its EXCH refit and fee/EV rows as secondary. This is the cheapest correction in the plan.
- Verdict vocabulary per cell: HONEST / NOT_HONEST / INCONCLUSIVE (n below minimum claims nothing), and the separate first-class line `no_positive_ev_found` or the count of flagged legs.

### 4.2 Minimum-data gate on the real data (non-negotiable 8; label-free; counts and measured variances only)

Per-game spread of the paired statistic is measured on the DK rung arrays of the map's own validation (`reports/alt_rung_cal/arrays_draftkings.npz`, 1,818 games, 134,462 rungs): with 17 randomly drawn rungs per game (about Kalshi's per-game leg count),
the sd across games of the mean log-loss difference (CAL-rung minus raw) is **0.0423**, of the mean residual (hit minus CAL-rung) **0.127**. Kalshi ladders are nested and star-only, so a 1.5x inflation is also shown.

Projected usable games (a game is usable if it has at least one in-scope leg, today 11 of 28 = 0.39; half of that as the pessimistic case), from `data/schedule/live_tips_2026-27.parquet` (1,200 games):
| Look | Games on schedule from 10-24 to the look | usable games at f = 0.39 | at f = 0.20 |
|---|---|---|---|
| L1 2026-12-15 | 324 | 138 | 69 |
| L2 2027-02-15 | 798 | 324 | 162 |
| L3 2027-04-12 | 1,172 | 471 | 236 |

Power of "CAL minus raw <= -0.005 with CI upper < 0" (Kalshi cells C+D pooled), by games, if the true gain equals the DK value (-0.0106) / half of it (-0.0053):
| Games | sd 0.0423, full | sd 0.0423, half | sd x1.5, full | sd x1.5, half |
|---|---|---|---|---|
| 100 | 0.81 | 0.35 | 0.51 | 0.21 |
| 150 | 0.92 | 0.46 | 0.66 | 0.27 |
| 200 | 0.97 | 0.54 | 0.76 | 0.32 |
| 300 | 0.99 | 0.55 | 0.89 | 0.42 |
| 400 | 1.00 | 0.56 | 0.96 | 0.51 |
Calibration-in-the-large half-width (1.96 x 0.127 / sqrt(games)): 0.035 at 50 games, 0.025 at 100, 0.018 at 200, 0.014 at 300, 0.012 at 460.

Reading (honest): the "honest" word (|gap| <= 0.03) is decidable at about 100 games; the "helps" word is powered only if the Kalshi gain resembles DK's, and a half-size gain is not detectable this season (power <= 0.56). So the proposed minimum is
**150 usable games and 2,500 settled in-scope legs for the Kalshi pair C+D (the "honest" word and a descriptive gain), and 400 games and 10,000 legs for cells A and B**.
Dates at the measured rates (schedule file, counting scheduled games from 10-24): Kalshi C+D reaches 150 usable games on 2026-12-20 at f = 0.39 and on 2027-02-06 at f = 0.20, and 400 games only at f = 0.39 (mid-March) or never at f = 0.20 (236 at season end).
A and B (DK has a main line at T-60 in 18 of 28 games, about 0.64 of games, 49 legs per game): 150 games on 2026-11-20 and 400 games on 2027-01-20.
**Gate verdict: PASS for A and B; PASS for C+D at the 150-game minimum at either rate (the 400-game "helps" claim is out of reach if f is 0.20); E descriptive.** The listing rate f is measured on 4 nights, so re-run the counter weekly; if by 2026-12-15 the measured f projects fewer than 150 usable games by 2027-04-12, close C+D as DATA after the non-negotiable-8 audit (the only other source, Kalshi `/historical` props in `data/rehearsal/kalshi_props_2025.duckdb`, is season 2025, the frozen holdout, so it is not an option here).

### 4.3 Other out-of-scope uses: no test proposed, with the reason

| Use | Disposition |
|---|---|
| Win / spread / total legs | WINNER_RECAL gate FAIL_POWER (0.15 to 0.23), closed. Stay raw; defer to the market |
| Combo stats (PRA, PR, PA, RA), STL, BLK | No map and no production forecast on the Kalshi side; COMBO_LINE_CAL is a separate draft. Not served |
| Books other than DK/Pinnacle (FanDuel, consensus lines) | FanDuel rung is "label only"; a FanDuel map is a new fit, not a scoping item |
| Kalshi combos and tickets mixing IN and OUT legs | Calibrated joint only when every leg is IN; the joint check rides on cell C+D outcome rows as a descriptive D1 (T495 style). No separate test this season |
| Alternative arms (`_int`, `_lt`, `_t30`, `_t15`, AVG, combo, full) | A new map per arm; not worth it before the production arm's forward cells report |
| Tail legs (p_cal >= 0.65) | T496 NOT_FIXED; tail flag stays. Cell D's high-P bins will say whether the tail is calibrated live (TAIL_LEG_CAL_LIVE) |

## 5. Implementation plan

### 5.1 One accessor: `nba/parlay/calibrated.py` (new, no consumer wired in step 1)

```
calibrated_p(forecast, stat, k, side, book_ctx, *, now) -> CalibratedP
  CalibratedP: p_raw (definition (a), side-adjusted), p_cal (None when OUT), scope in
               {"main_line","rung","out_of_scope"}, reason (no_dk_main | below_half | outside_band |
               unsupported_stat | unsupported_book | whole_number_line | no_grid | no_starter_rate),
               map_name, map_sha8, inputs {main_line, main_line_at, book, median, spread, r, zm, starter_rate}
```
Rules (all from the section 1 table, nothing invented): refuse a whole-number line; LC when the line equals the book main (DK or Pinnacle); rung map when book is DK, raw P(over) >= 0.5 and `r` inside the stored fit band; anything else is `out_of_scope` and `p_cal` is `None` (never a guess, never the raw number relabelled). A NO side is `1 - p_cal(YES)` only when the YES is IN. Inputs: `p_ge_full` counts, the persisted 199 grid (median, spread) for the rung map and the 19-point `q_grid` quartiles for LC exactly as the export and `top_source` do today (bit-identical to `line_cal` and `rung_cal`, a test compares). The fit band moves to a sidecar `nba/parlay/rung_cal_support_v1.json` (r 1st/99th percentile per stat from the frozen arrays, with the arrays' sha256), because the frozen map file must not be edited. The accessor also exposes `raw_p(...)` as the ONLY reader of the stored raw fields, so "no consumer reads raw p directly" is checkable by an AST test with an allow-list (the producers, the frozen harnesses and the frozen shadow writers named below).

### 5.2 Per consumer: what changes in outputs, and what must not

| Consumer | Change | Output delta |
|---|---|---|
| 1-3 (panel, export fields) | route their existing calls through the accessor | **None** (bit-identical; golden fixtures) |
| 4 App lists/pages (`model_p_over`) | contract v3: add `p_over_cal`, `p_over_scope`; pages show the calibrated value with a "calibrated (T493/T494)" tag where IN, and "raw model, uncalibrated" otherwise; `compareLine` and `ledger.ts` side logic read the calibrated value only where IN | **Intended**: smaller model-vs-book gaps; ledger "model side" can flip near 50%. Owned by app; this plan only specifies the fields |
| 5 Explainer | add "calibrated" beside "we said" for Pinnacle/DK lines, with the in-sample note; consensus lines keep raw labelled | Intended text change; replay page hashes change only in those sentences |
| 6 Briefing | none | none |
| 7-8 Kalshi engine | `Recommendation` gains `cal_model_prob`, `cal_scope`, `cal_ev`, `cal_ev_low` (additive keys); `raw_*` fields unchanged; verdict logic unchanged: still `p_use` = market while `model_weight` = 0 | **Verdict counts identical** (test: recorded 1,122-line slate gives the same `no_positive_ev_found` count); new fields appear. The calibration interval keeps today's logit sds (0.6, 1.6 beyond 0.70) until cell D supplies a measured slope CI |
| 9 Shadow log | new table `shadow_predictions_cal` (shadow_id FK, `p_cal`, scope, map sha8, inputs JSON), written by the same call; the existing table and `track_record` untouched | Existing rows and gate **byte-identical**. A second `track_record_cal` is reported beside it ("n and CI on every comparison") and cannot open the gate |
| 10 Paper trader | log `p_cal` and scope as extra columns only; decisions stay on the v2 raw/0.25 definition. `paper-trader-v3` (calibrated, no 0.25 blend) is a separate rule, frozen after cell D reports | none to v2 |
| 11 Sizer | require a `CalibratedP` input with scope IN, else refuse and print "keep your money" | new refusal behaviour (branch not merged) |
| 12 Assistant / ad hoc | all probabilities via the accessor; OUT printed as "no validated calibration"; `numguard` allow-list extended to the accessor's fields | Intended text changes |
| 13 Scorecard | add calibrated columns (LC at Pinnacle/DK lines) beside raw | additive |
| 14-15 Frozen | not modified | **None** |

### 5.3 Keeping the frozen rules byte-identical

1. The accessor never writes. Calibrated values are computed at read time or written only to NEW tables or NEW JSON fields (`shadow_predictions_cal`, export `p_over_cal`); nothing is ever added to `forward_predictions.prediction`, `forward_scores`, `p_ge`, `p_ge_full`, `q_grid` or `p_ge_uncond`. That is what keeps the replay oracle (sha 89889f6d over `forward_predictions` + `forward_scores`) blind to this work.
2. Frozen readers keep calling their own raw paths: T510 `p_M` (harness reads `p_ge_full` x `p_play`), FORWARD_PREREG scorers (`settle.py`, `crps_int`), LLM payload (`p_over_from_row`), `shadow.log_shadow(raw_model_prob=...)`. They are listed in the AST test's raw-read allow-list with their rule ids; adding a name to the list needs a DECISIONS line.
3. Golden-hash tests: (i) T510 `p_M` over a recorded Kalshi/forecast fixture; (ii) the `shadow_predictions` table DDL and rows for a recorded evaluate run; (iii) LLM payload bytes for a recorded game; (iv) `Recommendation.to_dict()` keys minus the new `cal_*` keys equal the old dict byte for byte.
4. Map files, `MAX_FIT_DATE` guard, the `map_sha256` verification on load: unchanged. A refit stays behind Devin's 2025 training-use row.
5. The deferral gate (`min_settled_for_claims` 30, `min_settled_dates_for_claims` 14, game-clustered lower bound > 0) keeps scoring RAW. Opening it on a calibrated column would be a new rule with its own freeze, decided after cells C and D.

## 6. Proposed order

1. **Now (no live effect): this plan; `cal_scope_gate.py`; amend the EXCHANGE_LADDER_CAL draft (rung map, not LC, at rungs).** Devin's go for drafting `CAL_FORWARD_2026_27`.
2. Draft `CAL_FORWARD_2026_27` (modeler + market agent) -> adversary pre-freeze review -> main session freezes. Start counting from the freeze date (4 pre-freeze nights are exposure, excluded).
3. Accessor `calibrated.py` + sidecar + AST/golden tests, wired to NOTHING; consumers 1-3 re-pointed (bit-identical). Merge only with the steward oracle unchanged.
4. Additive outputs: consumers 7-9, 12, 13 (new fields and tables); consumer 5 and 4 display (contract v3 with the app owner).
5. Cells A, B report (expected first, December): only then do the calibrated numbers on the app lists lose the "pending" tag.
6. Cells C, D report (January at the earliest, 150 games): decide per cell whether Kalshi EV rows, `paper-trader-v3` and the sizer may read `p_cal`. Until then Kalshi EV rows show calibrated numbers labelled "extrapolated, not validated on an exchange".
7. Never: make `p_cal` the basis of a "bet this" verdict before a frozen rule passes after fees with CI > 0 (the robotrader gate, DECISIONS 2026-10-22 21:0x).

## 7. Verification plan and merge gates

Unit tests (`tests/parlay/test_calibrated.py`, new): accessor == `top_source.load_legs` `p_cal` and == export `line_cal` / `rung_cal` bit for bit on the existing fixtures; scope classification table (every reason code, boundary at P = 0.5 and at the band edges; NO side symmetric); whole-number line refused; OUT never returns a number; definition (c) input rejected (a `QuantileGridDist` p cannot be passed); map sha/holdout guards still raise; AST test that only allow-listed modules read `p_ge_full`/`q_grid`-derived P directly; no-order/no-credential grep (already in the repo's tests, kept true); `cal_scope_gate` classifier test (written).
Replay oracle: **must stay IDENTICAL** - `forward_predictions`/`forward_scores` dump (sha 89889f6d, 46,658 predictions, 44,562 scores), the pplay/full/AVG/combo shadow tables, `forward_prop_grid199`, `shadow_predictions`, T510 `p_M`, LLM payload bytes, top-parlays JSON (already calibrated), `line_cal`/`rung_cal` export columns, every `raw_*` and `verdict` field, the `no_positive_ev_found` count. **Intended to change**: app list/page `model_p_over` presentation (contract v3), explainer sentences, new `cal_*` keys and the new shadow table, assistant text, scorecard added columns.
Merge gates, in order: (1) tests green (ruff, ruff format --check, mypy, pytest, exit codes, unpiped); (2) steward replay oracle unchanged (a branch touching `nba/daily`, `ops/` or `configs/` also needs Devin's go; `calibrated.py` and the parlay changes are outside `nba/daily`, but the export and app contract touch live output, so the oracle is required anyway); (3) adversary review of the scope table and the frozen-surface tests (one question: can any frozen output move?); (4) Devin's go for anything outward-facing (app display, contract v3) and for the CAL_FORWARD freeze; (5) the main session merges; no promotion, no stake language.

## 8. Honest no-edge note

Calibration is the fix for a number that was too confident; it is not a source of edge. On today's 283 in-or-near-scope Kalshi markets the calibrated model sits within 5c of the mid on two thirds of them, and where it still disagrees (the stars' deep rungs) the number is an extrapolation of a map that was fit on rungs between 0 and 1.45 spreads below the main line. The verdict stays `no_positive_ev_found` (1,122 of 1,122 on 10-23; 0 positives after fee and spread, 0 flagged by this work). Keep your money until a forward cell says otherwise.

## 9. Commands

Re-run the scope counter (read-only; copy the Kalshi store first because the snapshot writer holds its lock; the second command is the whole gate, about 20 s):
```
cp /Users/devin/Downloads/nba-prediction/data/kalshi/kalshi.duckdb* <scratch>/kcopy/
cd /Users/devin/Downloads/nba-prediction && .venv-1/bin/python -m nba.parlay.cal_scope_gate --nba-db nba.duckdb --kalshi-db <scratch>/kcopy/kalshi.duckdb --market-db data/markets/market_asof.duckdb --arrays reports/alt_rung_cal/arrays_draftkings.npz --since 2026-10-20 --until <date> --out <scratch>/scope.json
```
Verification of this branch: `.venv-1/bin/ruff check nba tests`, `.venv-1/bin/ruff format --check nba tests`, `.venv-1/bin/mypy`, `.venv-1/bin/python -m pytest tests/parlay -q --no-cov`.

## 10. Drafted rows (not appended)

DECISIONS: "2026-10-23 night | CALIBRATION_EVERYWHERE plan delivered (agent/market-calibration-everywhere). 15 consumers inventoried; 266 of 1,001 Kalshi prop markets (27%) on 10-20..23 are inside a validated scope; 390 (39%) are below raw P 0.5 and 327 (33%) have no DK main, both OUT; the EXCHANGE_LADDER_CAL draft applies the main-line map at rungs, which T494 measured worse than raw, so its arm is amended to the rung map before any freeze; proposed forward rule CAL_FORWARD_2026_27 (cells A-E); frozen rules stay raw and byte-identical. | Follows Claude's recommendation. | CAL_FORWARD cells A/B fail to transport the 2023-24 maps to 2026-27 (then no display change ships)."
PREDICTIONS_LEDGER: "P(cells A and B HONEST at 150+ games) 0.65; P(cell D HONEST at 150 usable games | gate met) 0.35; P(cell D CAL minus raw <= -0.005, CI upper < 0) 0.45; P(any Kalshi leg flagged positive EV after fee and spread with calibrated p, interval lower bound > 0) 0.10." (priors, written before any outcome of these cells; 4 nights of price data seen.)
