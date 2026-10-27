# HALFTIME_OVERS_VS_PRICE: at halftime, does the in-game line model find Kalshi player-prop Yes (over) contracts whose ask, after the fee, is below its probability? (pre-registration, forward only, paper only)

Status: **DRAFT 2026-10-27, modeler, branch `agent/modeler-halftime-overs` off `main` 8907f3a. NOT FROZEN; no hash taken; nothing
fitted; no model probability computed; no outcome, settlement or second-half state read.** The gate counts below come from
`nba/eval/halftime_overs_gate.py` (label-free) on the write-once live capture, `reports/halftime_overs/gate.json`.
The gate is **not yet measurable in full** (one input, the entry fraction f, needs the live scorer that does not exist yet), so this rule
**cannot be frozen as of this draft** (non-negotiable 8). The freeze sequence is in "Before any freeze".

**Advice or momentum: follows Claude's recommendation** (DECISIONS 2026-10-27 16:32: after triaging an outside LLM's list, the only
untested idea that is information-based and actionable for Devin at halftime, by hand, Yes only). Not momentum. Claude flags one
thing against it up front: the market sees the same first-half box score, and the model's fair-baseline edge at minute 24 is smaller than
its ~0.0025 average (T508-attack: the residual is concentrated at minute >= 30). The prior below is low for that reason.

Model class: **two-stage gradient-boosted trees** (T508 arm M: LightGBM remaining-minutes regressor, then a monotone LightGBM classifier
of `final >= line`), scored against a **market price** and a **player-aware logistic baseline** (glm_e). Paper only: no orders, no
credentials, no stake decision follows from any result here (rule 5 and 6).

## Playbook check
1. **Information or capacity.** Information: the first-half state (current stat, minutes, fouls, margin, starter) is true at halftime and
   unknown at tip. But it is **public** information the market also sees; the rule asks whether the market prices it worse than a
   pace-plus-pre-game model, not whether the state is informative (it obviously is). Capacity is T508's, unchanged (8 leaves, 200 trees).
2. **Enough data for this class.** Training is not the bottleneck: T508's halftime training rows are 2019-2024 play-by-play (gate PASS,
   smallest required pts cell 3,575 player-games). The bottleneck is the **forward evaluation sample** (gate below).
3. **Test validity.** Entry state and quote are both stamped and both from before any second-half event (decision time, quote window,
   state age, all fixed below). Same rows for every arm. Kalshi's own settlement, not a box-score proxy. Date-clustered CI. One entry per
   player-stat-game (ladder rungs are nested; UNDERS_FORWARD measured nesting alone as a design effect of 1.9). Comparator with the
   information M has: glm_e (player-aware pace + pre-game + need, T508-attack) and the market's own price. Power is checked below and is
   the weak point.
4. **Lineage and inherited guards.** T508 IN_GAME_LINE_MODEL (FROZEN rule c956fe4; result PASS vs B0; attack WOUNDED: M - glm_e
   -0.0024 pts 2023, -0.0025 2024, under the 0.005 floor; DECISIONS 2026-10-22 "any forward shadow or follow-up binds on a player-aware
   pace baseline, not T508's B0"). Carried forward: presence by on-court status, never box membership; truncation identity; integer
   strikes; glm_e as the binding model baseline. From T510/T510b and UNDERS_FORWARD: Kalshi DNP rule text, settlement from Kalshi's stored
   settled pages, one rung per player-stat-game, integer-cent prices, verified fee object, refuse-on-unverified. From T477: a log-loss gain
   at a line is not P&L; P&L is primary here.
5. **What it is scored against.** Primary: realized P&L per $1 staked against the price Devin would actually pay (ask + fee). Secondary:
   log loss of M vs the Kalshi mid (and ask) and vs glm_e on the same halftime rows.
Class checks (GBM): monotone constraints as T508; no tuning; seed 0; `n_jobs=1`; train-serve parity on minutes (below); missingness:
a row with any missing state field is excluded and counted before any probability is computed, never imputed.

## Hypothesis
H1: among halftime Kalshi Yes quotes on pts / reb / ast / 3PM where M's probability exceeds the ask plus the one-contract fee by at least
0.05, buying one Yes contract and holding to settlement earns at least +0.10 per $1 staked (fee included), with the date-clustered 95% CI
lower bound above 0. H0: it does not (the market's halftime price already carries what M knows, or the fee and spread eat it).

## 1. Data, window, known-at
* **Capture.** The write-once live capture `data/raw/live_<ET date>/` (`nba.live capture`, launchd `local.nba.live-capture-start`):
  `espn_scoreboard` (game status every ~60 s), `espn_summary/<event_id>` (box score), `kalshi/<SERIES>_p<n>` (open markets with
  `yes_ask_dollars`, `yes_ask_size_fp`, `yes_bid_dollars`, `status`), `manifest.jsonl` (`received_at` per file). Every time below is the
  manifest `received_at`, never a body field.
* **Settlement.** Kalshi's own settled pages `data/kalshi/raw/<date>/*_settled_*.json.gz` (as UNDERS_FORWARD section 6).
* **Scoring window.** Regular-season 2026-27 games whose ET date is **after the freeze commit's date**, through 2027-04-11 (148 ET
  nights with games from 2026-10-28; 1,144 games, `data/schedule/live_tips_2026-27.parquet`). Nights 2026-10-21..10-27 are used for
  the label-free gate and the f measurement only and are **never scored** (their outcomes are public and partly seen).
* **No season 2025 outcome is read.** Training is 2019-2024 (`season <= 2024`). See open choice C3 for the pre-game recency input.

## 2. The entry (exact)
For each game, from the capture only:
1. **Halftime start** `t_ht` = `received_at` of the first `espn_scoreboard` file in which the game's status is `STATUS_HALFTIME`.
2. **Decision time** `t* = t_ht + 120 s` (time for Devin to open the app; the first-half box to finish updating).
   If the scoreboard shows the game in period >= 3 at or before `t*`, the game has no entry (`q3_before_decision_time`, counted).
3. **Quote.** Per series (KXNBAPTS, KXNBAREB, KXNBAAST, KXNBA3PT), the first Kalshi page with `received_at` in `[t*, t* + 180 s]`
   (and every page of that series received within 30 s of it). Quote age limit: 180 s after `t*`. No page in the window: no entry for that
   series and game (`series_no_page_in_window`, counted).
4. **State.** The latest `espn_summary` for the game with `received_at <= ` the quote page's, at most 150 s older than it, whose header
   status is `STATUS_HALFTIME`. Otherwise no entry for that series and game (`series_state_stale_or_missing` / `series_state_not_halftime`).
5. **Market filter (mechanics, no outcome).** `strike_type == "greater"`, strike `k = floor_strike + 0.5` an integer, `status == "active"`;
   ask in integer cents `ask_c = round(100 * yes_ask)` with **2 <= ask_c <= 97**; `yes_ask_size >= 1` (top-of-book depth; a $5 depth flag
   `yes_ask_size x ask >= 5` is recorded and reported as a slice of its own, never a filter).
6. **Player filter.** Kalshi title `"<Name>: <k>+ ..."` matched to the ESPN box of that game by folded name (ASCII letters, suffixes
   dropped). Unmatched: excluded, counted. **DNP-at-halftime exclusion:** ESPN `didNotPlay`, no stats line, or first-half `MIN == 0`:
   excluded and counted (`x_dnp_first_half`). Such a player may still enter later or never; Kalshi's rule for a player who never takes the
   court is a fair-price settlement (`rules_secondary`, verified text in T510b; `kalshi_prop_dnp_rule_verified: false` in
   `configs/jarvis.yaml` refers to that branch). Excluding them means **every scored row is a player who has entered the game**, for whom
   the verified rule text says "Once a player enters the game the market settles based on the player's [stat] recorded." Ejected at
   halftime: excluded, counted. Already resolved (`current >= k`; Kalshi usually closes these early): excluded, counted.
7. **Line grid.** Only strikes on T508's trained grid (pts 10/15/20/25/30, reb 2/4/6/8/10, ast 2/4/6/8, 3PM 1/2/3/4); others counted
   (`x_off_t508_grid`), never scored (tree models extrapolate flat outside their grid).
8. **Fee.** `configs/parlay.yaml` `fee_model` through `nba.parlay.config.FeeModel`; the scorer refuses unless `verified: true`, coefficient
   0.07, formula `p_one_minus_p`, multiplier 1.0 for these four series (Devin confirmed the multipliers 2026-10-23, UNDERS_FORWARD s5).
   **Primary: fee on a one-contract taker order, rounded up to the cent once**: `fee_c = ceil(7 * ask_c * (100 - ask_c) / 10000)` cents
   (1 or 2 cents on this universe; mean 1.53 c on the gate rows, 4.4% of the mean ask 34.4 c). Sensitivities (reported, never deciding):
   unrounded fee; the fee on a `floor(4 / ask)`-contract order (Devin's typical $4 stake, rounding spread over the order).
9. **Edge and entry.** `edge = p_M - ask - fee` (dollars). A (player, stat, game) unit enters at most once: at the strike with the
   largest `edge` among its eligible strikes, iff that `edge >= 0.05`. Ties: the lower strike. Nothing after `t*` + 180 s enters the
   decision. Hold to settlement; no sell, no re-entry, no second-half trade.

## 3. Probabilities (arms)
* **M (the arm under test).** T508's arm M, same specification and constants (`research/eval/in_game_line_rule.py` at the T508 pin),
  **refit once on 2019-2024** (T508's report fit used 2019-2023) and evaluated at checkpoint e = 24 only. Train-serve parity: the live
  feed's minutes are ESPN's integer `MIN`; the refit uses stint minutes rounded to the nearest integer at every checkpoint (disclosed
  deviation; ESPN's rounding convention is checked on the gate nights against stint-free play-by-play time before the freeze and the
  choice of round vs floor is fixed then). Margin from the scoreboard at `t_ht`; fouls `PF`; starter ESPN `starter`; pre-game mean per C3.
* **glm_e (the binding model baseline).** The T508-attack construction (`docs/reviews/IN_GAME_LINE_MODEL_post_result_2026-10-22.md`
  row 6/7): logistic on [logit B0p, logit B0, logit B1, log1p(need)] at e = 24, trained on 2019-2024 halftime rows only.
* **Market.** `mid = (yes_bid + yes_ask) / 2` and the ask itself, both from the entry page.
* Model P's are written to a forward log with `made_at <= t* + 180 s` before any settlement exists; the scorer refuses a row whose
  `made_at` is later or whose state file is not the one named in step 4.

## 4. Primary metric, floor, CI, decision
* **Primary metric** `R$` = sum over entries of `(y - ask - fee)` divided by the sum of `(ask + fee)`: realized P&L per $1 staked.
  `y` = Kalshi settlement (`yes` 1, `no` 0, `scalar` its `settlement_value_dollars`; a scalar with no stored value excludes the whole
  game, counted). Per-contract mean `y - ask - fee` printed beside it.
* **Floor (practical): `R$ >= +0.10`** (C1: the alternative +0.05 is not powered in one season; see the gate).
* **CI:** percentile bootstrap of the ratio, B = 10,000, `numpy.random.default_rng(2026)`, clusters = ET dates (cross-game co-movement
  on a night: one pace or officiating environment, and every game's halftime shares Devin's hour). Game-clustered CI printed beside it.
* **One look** at the earlier of: (a) the night the entry count first reaches `n_req` (counted label-free from the forward log, so
  stopping on it is not optional stopping), scored 2 days later so settlements land; (b) 2027-04-13 (season end + 2 days).
* **PASS** iff all: n entries >= `n_req` (or the season ended) AND n dates >= 30; `R$ >= 0.10`; date-clustered 95% lower bound > 0;
  guards hold (section 6).
* **NO_POSITIVE_EV_FOUND** (first-class) iff n reached and the 95% upper bound < 0.10.
* **INCONCLUSIVE** otherwise, including the season ending short of `n_req`; the rule closes, no extension.
* Never a verdict: a point estimate alone; a slice; any sensitivity.

## 5. Secondary (own family, descriptive claims)
On **all** eligible halftime units (every on-grid strike that passed steps 1-8, not only entries; integer strikes, same rows for every arm):
log loss of M, glm_e, the market mid and the market ask. Reported: M - mid, M - ask, M - glm_e, mid - glm_e, each with a date-clustered
95% CI, n rows, n units, n games, n dates; reliability tables (10 bins) for M and mid. Family of three for BH at q = 0.05 over
{M - mid, M - glm_e, mid - glm_e}. The question it answers is "who is better calibrated at halftime, the model or the market?"; it never
rescues or blocks the primary. Floor for a calibration claim: |difference| >= 0.005.

## 6. Guards (all must hold for a PASS)
* **Baseline knows what the model knows.** glm_e is run through the identical entry rule (its own `edge >= 0.05`). If `R$(glm_e) >=
  R$(M)` the verdict word is **PASS_BASELINE**: the halftime price is beatable by a pace model and M adds nothing over it; anything built
  afterwards uses the simpler one. (Also the honest result if it happens: actionable for Devin, not evidence for M.)
* **Concentration.** Top-5 players' share of total P&L <= 0.5; no single date > 0.3 of it.
* **Not a stale-quote artifact.** Recompute `R$` with the quote of the LAST Kalshi page received while the game is still at halftime
  (the price Devin sees later in the break): sign must agree. Reported with n.
* **Planted-future check (in code, before scoring):** a copy of the forward log with each row's state replaced by the next ESPN summary
  after halftime must change `p_M` on > 90% of rows; the real log must equal a re-run from the named state files bit for bit.
* **Settlement integrity:** on rows with a final box score in `nba.duckdb` (season 2026), Kalshi's `yes`/`no` must agree with
  `final >= k`; disagreements listed; > 1% disagreement voids the read (a mapping bug).

## 7. Slices (pre-declared; BH family of 8, q = 0.10; descriptive, never a verdict)
Per slice: n entries, n dates, `R$` with the date-clustered CI, one-sided bootstrap p for `R$ > 0`; BH across the 8. A slice is
"flagged for a new rule" only if BH-significant AND `R$ >= 0.10` AND >= 200 entries over >= 20 dates; a flag is a hypothesis, not a claim.
1. **Blowout bench:** |margin| >= 15 at `t_ht`, ESPN `starter == false`.
2. **Blowout starters:** |margin| >= 15, starter. (T508 SHAPE: Q3 starter minutes barely drop in a halftime blowout, 0.96-0.99, so a
   market that marks starters down at halftime would be the mispricing.)
3. **Foul trouble:** first-half `PF >= 3`.
4. **Cold first half:** first-half FG made <= 0.30 x attempted on >= 5 attempts (ESPN box only; knowable at halftime; no pre-game input).
5-8. **Stat:** pts, reb, ast, 3PM.
Also printed, not in the family: ask bucket (2-15c, 16-35c, 36-60c, 61-97c), $5-depth flag, strike, home/away.

## 8. Minimum-data gate (non-negotiable 8), measured on the real capture 2026-10-21..10-26
`uv run python -m nba.eval.halftime_overs_gate --nights live_2026-10-21 ... live_2026-10-26` (label-free, 2 s; the module docstring
lists what it does not read). Data version: `data/raw/live_<date>/manifest.jsonl` as of 2026-10-27 ~16:40 CDT; 10-27 excluded (slate
not started). Opening night 10-20 has no live capture directory.

| night (ET) | games scheduled | halftime seen | with a valid snapshot | player-stat units | eligible on-grid strikes | strikes at snapshot |
|---|---|---|---|---|---|---|
| 10-21 | 11 | 9 | 4 (120 s cadence; summaries stale) | 168 | 518 | 738 |
| 10-22 | 2 | 2 | 2 | 102 | 326 | 424 |
| 10-23 | 12 | 2 | 2 (capture ran 11 min; Mac asleep) | 93 | 283 | 389 |
| 10-24 | 8 | 1 | 1 (capture started 02:35Z) | 28 | 83 | 118 |
| 10-25 | 7 | 7 | 7 | 282 | 849 | 1,216 |
| 10-26 | 9 | 9 | 8 (one game's state not at halftime) | 342 | 964 | 1,461 |
| **total** | **49** | **30** | **24** | **1,015** | **3,023** | **4,346** |

By stat (pooled): eligible strikes pts 860 / reb 797 / ast 651 / 3PM 715; units 278 / 264 / 234 / 239. Exclusions: no tradeable ask 713,
off-grid 488, unmatched name 72, DNP at halftime 37, already resolved 13, ejected 0. $5 depth at the ask: 2,002 of 3,023 (66%).
Slice rows among eligible strikes: blowout (>= 15) 462 (bench 101, starters 361), foul trouble 269, cold 485.
**Capture rate:** 24 of 49 scheduled games (0.49) over the six nights; 15 of 16 on the two healthy nights (0.94). About 42 units per
captured game.

**Power floor.** Per entry, under the null the SD of P&L per $1 is `sqrt(a(1-a)) / (a + fee)` = 1.32 at the gate's mean ask 0.344 and fee
0.0153. Design effect 2.5 for date clustering of one-rung-per-unit entries (UNDERS_FORWARD measured 4-6 with all rungs, of which ladder
nesting is 1.9; one rung per unit removes nesting; assumption, replaced by the realized cluster variance at the read). Two-sided 95%,
80% power: `n_req = 2.5 x 2.80^2 x 1.32^2 / floor^2` = **3,424 entries at floor +0.10**; 13,697 at +0.05.
Entries per night = 7.73 games x capture x 42.3 units x f, where **f = share of units with `edge >= 0.05` (unmeasured: needs M live)**.

| floor | capture | f = 0.10 | f = 0.25 | f = 0.50 |
|---|---|---|---|---|
| +0.10 | 0.94 | 111 nights | 45 | 22 |
| +0.10 | 0.49 | 214 | 85 | 43 |
| +0.05 | 0.94 | 446 | 178 | 89 |
| +0.05 | 0.49 | 855 | 342 | 171 |
148 nights remain. **Gate rule (frozen with the text):** the rule is freezable only if, with f measured label-free by M on the six gate
nights' snapshots and capture measured over the last 7 nights before the freeze, the projected nights to `n_req` <= 0.8 x the nights
remaining after the freeze date. At floor +0.10 that needs roughly f >= 0.10 with healthy capture, or f >= 0.20 at today's capture rate. At
+0.05 it needs f >= 0.40 with healthy capture: **not freezable at +0.05 in one season under any plausible f.**
**Status: NOT MEASURABLE YET (f needs the live scorer). Not frozen.**

**Data capture audit (non-negotiable 8, second half: is there historical halftime Kalshi price data anywhere we hold?)**
* `data/kalshi/kalshi.duckdb` `kalshi_prices`: source `live` from 2026-10-08 (props from 2026-10-16), median spacing 916-918 s (the
  15-minute snapshot job): too coarse for a 15-minute halftime with a 180 s quote age. Source `historical`: 341 hourly KXNBAPTS rows,
  2026-06-11..14 (Finals). No halftime-resolved rows.
* `data/rehearsal/kalshi_2025.duckdb`: KXNBAGAME only (2,898 markets, 174,132 hourly candles, 2025-04-14..2026-06-14). No props.
* `data/rehearsal/kalshi_props_2025.duckdb`: 61,105 prop market definitions (PTS 17,755, REB 17,570, AST 14,040, 3PT 11,740;
  2026-01-16..06-14), **0 price rows**.
* `data/kalshi/raw/kalshi-candles`: the KXNBAGAME hourly backfill (log: 2,898 markets, 174,132 rows).
* **Repairable gap, with cost:** Kalshi's public candlestick endpoint (no key) could be asked for 1-minute candles of the 61,105 2025-26
  prop markets around each halftime: about 61k requests (about 2-3 h at the capture's 8 req/s), free; whether 1-minute history is retained
  for settled markets is **unverified**; candles carry no depth and need 2025-26 halftime wall-clock times we do not hold. And it is
  **season 2025**: scoring on it is a holdout touch (one logged touch per never-evaluated model; M has never been evaluated on 2025).
  Devin's call (C4); not the default.
* **Repairable gap, cheap:** capture coverage (10-23 and 10-24 lost most games: the Mac slept / the capture started late). Steward item;
  every point of capture rate shortens the projection proportionally.
Disclosure: the audit ran aggregate counts and min/max timestamps on the two `kalshi_*2025*` rehearsal files (season-2025 Kalshi market
counts; no price joined to an outcome, no result column read). Drafted holdout-log DISCLOSURE row in the report; the archivist appends it.

## 9. What each choice protects against
* `t*` = halftime + 120 s, quote within 180 s, state within 150 s and still at halftime: a quote or state from after the second half
  starts (a planted future), and an entry Devin could not have made.
* Ask, not mid, plus the fee on one contract: scoring a price nobody can trade at (the surfer and UNDERS lessons: spread and fee are the
  whole story at these sizes).
* One rung per unit at the largest edge: 5 nested rungs of one player counted as 5 observations.
* DNP-at-halftime exclusion: rows whose settlement is a fair price we cannot observe in advance (and whose value the model cannot price).
* T508 grid only: tree extrapolation on strikes it never saw.
* glm_e guard: calling a pace-model edge "our model" (the T508 lesson: 94% of its gain was baseline weakness).
* Kalshi settlement, settlement-integrity check: box-score proxies that disagree with what Kalshi pays.
* Date clusters: one night's environment counted as many independent bets.
* One look, stopping on a label-free count: optional stopping.
* Scoring only post-freeze nights: the gate nights' outcomes are public and partly seen.

## 10. Kill criteria, forking paths, follow-up
Gate not met at the freeze attempt: not frozen; recorded as "not freezable on captured data" with the audit above. NO_POSITIVE_EV_FOUND or
INCONCLUSIVE: the question "does M beat Kalshi's halftime over price" is closed for this model and these features; **one pre-registered
follow-up at most** (default candidate: the BH-flagged slice, if any, as its own forward rule). Fixed now and never re-tried after a result:
threshold 0.05, floor 0.10, `t*` 120 s, windows 180/150 s, ask band 2-97 c, the grid, the fee convention, the slices and their cut-offs
(15 points, 3 fouls, 30% on 5 FGA). Not this rule: the Q3-end checkpoint (where the attack found the residual), No side, maker orders,
combos, other venues.
**What changes in production: nothing.** A PASS makes "halftime over flags" a candidate for JARVIS's halftime check-ins at the "thin"
label, Devin's decision. No 2025 touch in this rule.

## 11. What would reverse it; what a null means
* **Reverse a PASS:** the adversary breaks it (stale quote, state after `t*`, settlement mapping, concentration), PASS_BASELINE, or the
  first 30 live JARVIS halftime plays lose to their own later halftime price (CLV kill switch, TRADING_CONTEXT).
* **A null means:** at halftime the Kalshi Yes ask already carries what a first-half state model knows, net of a 1-2 c fee. It does NOT
  mean the in-game model is miscalibrated (the secondary answers that), nor that later checkpoints (end of Q3) or the No side carry no
  edge. An INCONCLUSIVE from thin capture is not a null; it is a capture failure and is reported as such.

## Before any freeze (order)
1. Adversary pre-freeze review of this text.
2. Devin's answers to C1-C4 (defaults below).
3. Build (modeler, behind this draft, outcome-blind): the e = 24 refit of M and glm_e on 2019-2024 (maintainer's command, heavy lock);
   the live state builder from the capture (steps 1-7); the forward log writer; the scorer with the fee refusal tests, the
   planted-future check, the settlement-integrity check; unit tests on `tests/fixtures/loader.py::build_fixture_db` and synthetic capture
   folders.
4. Measure f label-free: M and glm_e on the 24 gate games' snapshots (no settlement, no finals read); capture over the last 7 nights.
5. Gate decision per section 8. Only if it passes: main session commits `prereg(HALFTIME_OVERS_VS_PRICE): <sha256>` to `main`.
6. Priors appended to `docs/PREDICTIONS_LEDGER.md` before step 5 (draft row in the report).

## Open choices for Devin (defaults taken in the text)
* **C1. Floor.** Default **+0.10 per $1**. +0.05 is the smallest edge worth anything, but it needs 13,697 entries and is not freezable
  in one season; +0.10 is the smallest that can be tested this season (and at $3-5 stakes, ~1 h a night, +0.05 is about 20 c a night).
* **C2. Primary unit.** Default P&L per $1 staked (ratio). Alternative: per contract. Same verdict direction; per $1 is what Devin's
  money sees.
* **C3. Pre-game recency mean for M early in the season.** Default: the production as-of read (last 10 played games before the game
  date, which in Oct-Nov includes 2025-26 games), as the daily props pipeline already does; a DISCLOSURE row is logged before the
  first live scorer run (precedent: the 2026-10-20 scorecard disclosure). Alternative: 2026-27 games only with `n_prior >= 5`, which drops
  most rows before about Nov 2 and every gate-night row.
* **C4. Historical repair.** Default: none (forward only). Alternative: fetch 1-minute Kalshi candles for 2025-26 prop markets and spend
  M's one 2025 touch on a retrospective read; costs the touch and has no depth or quote age.
