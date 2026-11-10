# HALFTIME_OVERS_VS_PRICE: at halftime, does the in-game line model find Kalshi player-prop Yes (over) contracts whose ask, after the fee, is below its probability? (pre-registration, forward only, paper only)

Status: **DRAFT 2026-10-27, modeler, branch `agent/modeler-halftime-overs` off `main` 8907f3a. NOT FROZEN; no hash taken; nothing
fitted; no model probability computed; no outcome, settlement or second-half state read.** The gate counts below come from
`nba/eval/halftime_overs_gate.py` (label-free) on the write-once live capture, `reports/halftime_overs/gate.json`.
The gate is **not yet measurable in full** (one input, the entry fraction f, needs the live scorer that does not exist yet), so this rule
**cannot be frozen as of this draft** (non-negotiable 8). The freeze sequence is in "Before any freeze".

**Amendment A (section 12, DRAFT 2026-11-09)** extends the question to every 5 game-minutes (checkpoint as a pre-declared slice, one late-game
primary family H2, Bonferroni across H1 and H2). Its gate on the real capture **fails** (177 nights needed, 106 allowed): the rule is still not
frozen, and the build behind it (paper log, silent card block) does not depend on a freeze.

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
(15 points, 3 fouls, 30% on 5 FGA). Not this rule: the Q3-end checkpoint (where the attack found the residual; reversed by amendment A, section 12), No side, maker orders,
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

---

# 12. Amendment A (DRAFT 2026-11-09, market agent): every 5 game-minutes, checkpoint as a pre-declared slice

Status: **DRAFT, not frozen, no hash.** Written before any checkpoint row has been scored against an outcome and before any settlement of a
checkpoint quote has been read. Branch `agent/market-ingame-checkpoints`. Sections 1-11 above (the halftime question, called **H1** from here)
are unchanged except where this section says so; where the two disagree, this section binds the checkpoint rows and sections 1-11 bind e = 24.

**Advice or momentum: follows advice and Devin's request.** DECISIONS 2026-11-09 21:31: Devin asked for the in-game model every 5 game-minutes
per game, paper-logged, silent card updates only, no new pings, and for this draft to be extended to all checkpoints (checkpoint as a
pre-declared slice) before it is frozen; that row reads it as Claude's halftime proposal #2 widened. **Claude flagged against part of it
earlier and says so again:** section 10 above lists "the Q3-end checkpoint" as *not this rule*, and the T508 attack found the residual over a
fair baseline concentrated at e >= 30 and below the 0.005 floor even there (-0.004 to -0.007 log loss). This amendment reverses that exclusion
because Devin asked and because the capture already holds the data (no new request); it does not change the prior, which is low (12.9).

## 12.1 What this protects against

Adding eight more looks at the same games multiplies the ways to find a "pattern" by cherry-picking the minute. So: one **primary late-game
family** (the prior says where an edge could be), every other checkpoint a **descriptive slice** under BH, the **entry rule counts each
player-stat-game once** (the settlement of one player's line is one outcome whatever minute he was priced at), and **no checkpoint outside the
model's trained range decides anything**.

## 12.2 The checkpoint (exact; implemented in `nba/markets/checkpoint_scorer.py`)

1. **Elapsed game time** `E = 720 (period - 1) + (720 - seconds on the clock)` for periods 1-4 (ESPN summary header `period`, `displayClock`).
   Checkpoint `c` in {5, 10, 15, 20, 25, 30, 35, 40, 45} (game minutes) is *reached* by the first summary of the game with `E >= 60 c`.
   24 is not a checkpoint (the half is H1, scored by the halftime job, unchanged).
2. **The tick.** The first Kalshi page of the series received at or after that summary and within 90 s of it (every page of the series within
   30 s of it is the same tick). `known_at` = that page's manifest `received_at`; a live tick waits until the page's tick has closed
   (page + 35 s). Per (game, series, checkpoint): one decision, once.
3. **Refused, counted by name, never retried or backfilled:** the state is more than **150 s** past the checkpoint (`checkpoint_missed_late`;
   with a 60 s cadence this is a missed tick); the state's status is not `STATUS_IN_PROGRESS` or `STATUS_END_PERIOD` (`state_not_live`: halftime,
   final, scheduled); **overtime** (`period > 4`; `overtime_excluded`: **OT is excluded**, no checkpoint is defined in it and none fires after
   45); the newest summary received by the quote page already shows the game over or in overtime (`game_over_before_quote`); the summary's
   newest play (ESPN `wallclock`) is more than **180 s** older than `known_at` (`state_stale`), or the summary has no plays (`state_no_plays`);
   live only: the write comes more than **180 s** after `known_at` (`refused_late_write`). A live pass never reads a file received after
   `now`, and a game not in play at `now` scores nothing (the buzzer).
4. **Served at the actual elapsed minutes** `e_used = E / 60` (>= c, at most c + 2.5): minutes, current stat, fouls and margin are those of
   that summary (margin from its header scores). The market filters, name matching, DNP and grid rules are section 2 steps 5-8's, verbatim
   (the code is shared). One difference of wording only: `x_dnp_first_half` at a checkpoint means "no minutes recorded yet"; such a player is
   not scored at that checkpoint and may be at a later one, and Kalshi's rule text then applies to every scored row ("once a player enters
   the game ...").
5. **Store.** A sibling of the halftime table (`data/jarvis/ingame_checkpoints.duckdb`; the halftime table and its rows are untouched): the same
   row content plus `checkpoint`, `e_used`, `model_support`, `period`, `clock`, `known_at`, `last_play_at`, `play_age_s`, `lateness_s`;
   key (event_id, ticker, checkpoint); append-only; no outcome column; `within_prereg_window` = written (DuckDB clock) within 180 s of
   `known_at`. A second pass over the same capture stores nothing.

## 12.3 What the model is, at a checkpoint (and what it is not)

* **M** is T508 arm M as fitted for the halftime scorer (artifact digest `b9b53d63...`, pooled over e in {6, 12, ..., 42}, Platt pooled). It is
  served at `e_used`. `model_support` = **trained** for 6 <= e_used <= 42 (interpolation between its 6-minute training grid) and
  **extrapolated** otherwise (checkpoint 5 and 45: trees extrapolate flat).
* **glm_e was fitted at e = 24 only.** Its probability is stored at every checkpoint, but away from the half it is **off its fitted support**
  (on the 2026-11-08 replay its edge share at 30/35/40 was 0.26/0.22/0.10 against M's 0.13/0.05/0.01; a baseline that lights up where the
  model does not is a mis-specified baseline, not a finding). Therefore: **no H2 PASS can be issued on the e = 24 glm_e.** Pre-freeze step R1:
  refit glm_e per checkpoint (the T508-attack construction, 2019-2024 labels only, maintainer's heavy command, outcome-blind to every
  2026 row) and pin its digest; the baseline guard of 12.7 uses it. Until R1 exists the rule cannot be frozen.
* Optional R2 (Devin's call, C5): refit M on the 5-minute grid 5..45; then 45 becomes trained and H2 may include it. Default: no refit, H2 uses
  {30, 35, 40}.

## 12.4 Families and multiplicity (declared before any result)

* **H1** (halftime, e = 24): sections 1-11, unchanged entry, floor, one look.
* **H2 (new, the primary late-game family): checkpoints 30, 35, 40** (the pre-declared "minute >= 30" family, restricted to e inside the
  trained range). The T508 attack's prior is that any edge sits here; H2 asks it as one pre-registered question.
* **Two primaries, Bonferroni:** each is judged on a **97.5% two-sided** date-clustered interval (one-sided 0.0125 each, family-wise at most
  0.025), not 95%. Everything below uses that interval for H2; H1's n_req (section 8) is re-run at the same level before its freeze
  (it grows by about 21%).
* **Everything else is a slice, never a verdict.** **S-family: the nine checkpoints** (5, ..., 45), BH at q = 0.10 over the nine one-sided
  bootstrap p-values for R$ > 0 (the slices overlap in time and outcomes, positively dependent; BH holds under positive dependence; stated, not
  assumed away). A slice is "flagged for a new rule" only if BH-significant AND R$ >= 0.10 AND >= 200 entries over >= 20 dates. Checkpoints 5
  and 45 are extrapolated and early checkpoints (5-25) are descriptive by the prior; none of them can carry a PASS.
* Not declared, so not reported as findings: checkpoint x blowout / foul / cold slices, checkpoint x stat, checkpoint x ask bucket (these may be
  *printed* as counts with n and a CI, labelled exploratory).

## 12.5 The H2 entry (exact)

Per **unit** = (game, player, stat). At each of the checkpoints {30, 35, 40} the unit's best strike is the one with the largest
`edge = p_M - ask - fee` among its rows at that checkpoint that passed 12.2 (ties: the lower strike). The unit **enters once, at the earliest
checkpoint whose best edge is >= 0.05**, at that strike, at the ask of that tick, fee = the verified object's one-contract taker fee rounded up
to the cent (section 2 step 8; never a hardcoded rate), held to Kalshi's settlement; no sell, no re-entry at a later checkpoint, no other
strike. (Why first-trigger: the same player-stat-game priced at 30, 35 and 40 is one settlement; counting three would triple n without
information, the UNDERS_FORWARD nesting lesson again. Why not the best edge over all three: choosing the maximum over checkpoints after the
fact is a selection no one can trade.) Only rows with `within_prereg_window` true and `n_prior >= 5` enter.

## 12.6 Metric, interval, one look, words (H2)

* **R$** as section 4, P&L per $1 staked; per-contract mean beside it; floor **R$ >= +0.10** (the same practical floor as H1; the T508 attack
  suggests the true effect is far below it, so this is a test of whether anything large exists, not of whether anything exists).
* **Interval:** percentile bootstrap of the ratio, B = 10,000, `numpy.random.default_rng(2026)`, clusters = ET dates, **97.5%** two-sided;
  game-clustered interval printed beside it; the wider decides.
* **One look** at the earlier of (a) the night the label-free count of H2 entries first reaches `n_req` (scored 2 days later), (b) 2027-04-13.
  **Status prints** (no verdict, no stopping, n only, "insufficient sample" below the configured minimum): 2027-01-15 and 2027-03-01.
* **Words:** `PASS` iff n >= n_req (or the season ended) AND n dates >= 30 AND R$ >= 0.10 AND the 97.5% lower bound > 0 AND the guards of
  12.7 hold; `PASS_BASELINE` iff the per-checkpoint glm_e (R1) run through the identical entry has R$ >= M's; `NO_POSITIVE_EV_FOUND` iff n
  reached and the 97.5% upper bound < 0.10; `INSUFFICIENT_SAMPLE` iff the season ends short of n_req (a capture failure, not a null);
  `INCONCLUSIVE` otherwise. The rule closes at its look; no extension.

## 12.7 Guards (all must hold for a PASS; section 6's, plus)

Section 6 unchanged (baseline, concentration, stale-quote, planted-future, settlement integrity), with: the planted-future check replaces each
row's state with the next summary after its checkpoint and must change `p_M` on > 90% of rows; the stale-quote check uses the last Kalshi page
before the next checkpoint's state. Added: (i) **lateness:** R$ recomputed on entries with `lateness_s <= 60` keeps its sign (n reported);
(ii) **play age:** the same on entries with `play_age_s <= 90`; (iii) **support:** every H2 entry is `model_support = trained` by construction
(a violation voids the read); (iv) rows with `within_prereg_window` false are excluded and their count reported (a live-lag measurement).

## 12.8 Slices (S-family, pre-declared; descriptive)

The nine checkpoint slices of 12.4. For each: n entries (first-trigger entries *as if only that checkpoint existed*: every unit whose best
strike clears 0.05 at that checkpoint), n dates, R$ with the 97.5% date-clustered interval, one-sided bootstrap p for R$ > 0, BH across nine at
q = 0.10. Printed beside each: `model_support`, the baseline (per-checkpoint glm_e) R$ on the same units, the market-mid log loss minus M's log
loss on all eligible rows of the checkpoint (secondary, section 5's definition, with n rows / units / games / dates). Below the configured
minimum of settled markets: "insufficient sample" and nothing else. A slice that is BH-significant is a hypothesis for a new rule, not a claim.

## 12.9 Minimum-data gate on the real capture (non-negotiable 8): counts only, no outcome, no final score, no settlement read

Source: `uv run python -m nba.markets.checkpoint_scorer gate --night <nights>` (label-free; reads summaries and Kalshi pages only;
`reports/ingame_checkpoints/gate_counts_2026-11-09.json`, 19 nights 10-21..11-09, 11-09 partial at the time of the run) and `replay` (the model,
2026-27 recency only, no season-2025 read; rows are replay-stamped, never forward rows).

**Per checkpoint, capture nights 10-28..11-08** (11 nights, 90 games = 90 scheduled; capture of scheduled games 1.00; the nights 10-21..10-27 are
excluded as in section 8; 4 series per game, so 360 decisions per checkpoint):

| checkpoint | games reached | decisions | valid | refused (stale / missed late / no page) | on-grid strikes (quotes) | units | units per game |
|---|---|---|---|---|---|---|---|
| 5 | 90 | 360 | 344 | 16 / 0 / 0 | 10,443 | 3,081 | 34.2 |
| 10 | 90 | 360 | 348 | 12 / 0 / 0 | 13,237 | 4,064 | 45.2 |
| 15 | 90 | 360 | 312 | 8 / 40 / 0 | 11,595 | 3,698 | 41.1 |
| 20 | 90 | 360 | 308 | 12 / 40 / 0 | 10,968 | 3,647 | 40.5 |
| 25 | 90 | 360 | 340 | 12 / 8 / 0 | 11,209 | 3,961 | 44.0 |
| 30 | 90 | 360 | 340 | 16 / 4 / 0 | 10,542 | 3,897 | 43.3 |
| 35 | 90 | 360 | 352 | 8 / 0 / 0 | 10,040 | 3,917 | 43.5 |
| 40 | 90 | 360 | 320 | 12 / 28 / 0 | 8,441 | 3,531 | 39.2 |
| 45 | 90 | 360 | 320 | 12 / 28 / 0 | 7,112 | 3,269 | 36.3 |
| **all** | **810 game-checkpoints** | 3,240 | 2,984 (92.1%) | | 93,587 | 33,065 | |

Valid share of decisions at the late family {30, 35, 40}: 0.937. Refusals are named, never silent. Strikes and units are before the model's
history requirement (n_prior >= 5): units are (game, player, stat).

**The edge share f and the H2 entry rate** (model measured label-free on nights 11-04, 11-05, 11-06, 11-07, 11-08: 39 games; recency window
2026-27 only, so the history requirement binds for more players than under Devin's C3 = allow):

| checkpoint | units (n_prior >= 5) | f(M) = share with best edge >= 0.05 | f(glm_e at e = 24 coefficients) |
|---|---|---|---|
| 5 | 1,336 | 0.238 | 0.273 |
| 10 | 1,766 | 0.160 | 0.138 |
| 15 | 1,778 | 0.104 | 0.084 |
| 20 | 1,777 | 0.131 | 0.089 |
| 25 | 1,684 | 0.095 | 0.069 |
| 30 | 1,703 | 0.086 | 0.178 |
| 35 | 1,698 | 0.039 | 0.148 |
| 40 | 1,600 | 0.009 | 0.071 |
| 45 | 1,536 | 0.032 | 0.062 |

**H2 first-trigger (12.5):** 1,800 units, **194 entries** (share 0.108; by checkpoint 147 / 37 / 10), mean ask 0.329, RMS null SD of P&L per $1 =
**1.70** (the cheap asks, 2-15 c, carry SD up to 4 per $1, which is why it exceeds section 8's 1.32). **38.8 entries per night** (194 over 5
nights; 7.8 games per night). The share of units where M disagrees with the ask by 5 c or more falls with the minute (0.086 at 30, 0.039 at
35, 0.009 at 40): a disagreement is not an edge, it is the entry condition the rule tests. The e = 24 glm_e lights up at 30 and 35 on 18% and 15% of units: that is the
off-support baseline of 12.3, and it is why R1 is a precondition.

**Power.** Per entry SD 1.70; design effect 2.5 for date clustering of one-entry-per-unit (assumption, as section 8; the realized cluster variance
replaces it at the look); 97.5% two-sided (12.4), 80% power: `n_req = 2.5 x (2.241 + 0.842)^2 x 1.70^2 / floor^2` =
**6,868 entries at floor +0.10**; 4,064 at +0.13; 3,053 at +0.15; (at 95%: 5,671 / 3,356 / 2,521; at design effect 1.5 and floor +0.10:
4,121). Nights to `n_req` at 38.8 entries per night: **177 at +0.10**.

**Gate rule (section 8's, unchanged):** freezable only if projected nights to `n_req` <= 0.8 x the nights remaining after the freeze date. Nights
remaining from 2026-11-13 ET to 2027-04-11: **133 nights, 1,026 games**; 0.8 x 133 = 106 nights.

**H2 as drafted FAILS its own gate: 177 nights needed against 106 allowed. It is NOT frozen and cannot be frozen as written.** It would pass
only at floor >= +0.13 with design effect 2.5 (105 nights, marginal); at design effect 1.5 and floor +0.10 it just misses (107); a lower power or a
looser interval would also pass, and none of these is a repair: each moves the question, and the floor is Devin's (C1). Nothing changes in the
paper log either way: it records every checkpoint regardless of whether a rule is ever frozen.

**Data-capture audit before calling the gate failed (non-negotiable 8, second half):**
* Seasons and sources we hold: 2019-2024 play-by-play and box scores (training, no Kalshi in-game price); `data/kalshi/kalshi.duckdb` live
  snapshots at 15-minute spacing from 2026-10-08 (props from 10-16: too coarse for a 5-game-minute checkpoint); the section-8 audit of the
  rehearsal stores (no prop price rows, KXNBAGAME hourly only) stands, run 2026-10-27, not re-run. No source we hold has in-game Kalshi prop
  prices at finer than 15-minute spacing before the first capture night, 2026-10-21. The only repairable historical gap is Kalshi's public 1-minute candlesticks for 2025-26 prop markets (about 61k
  requests, free, retention for settled markets unverified, no depth or quote age, and it is **season 2025**: scoring on it spends M's one
  holdout touch). Devin's C4; not taken.
* Capture gaps inside the window and their size: 810 of 810 scheduled game-checkpoints were reached (capture 1.00); 7.9% of decisions are refused
  (stale 3.3%, missed late 4.6%, no page 0%): night 11-01 alone has 60 of 144 decisions stale (ESPN's summary stopped moving for several
  games), and missed-late appears on eight of the eleven nights (none on 11-05, 11-06, 11-08). Even capture of every decision would lift entries per night
  by about 7%, from 38.8 to about 41.5: 6,868 entries would still need about 165 nights. The gap is not the binding constraint; f and the floor are.
  Repairable at no cost: the ESPN freshness poller (built, OFF) targets exactly the stale and missed-late refusals.
* Measurement uncertainty, stated: f comes from 5 nights (194 entries); the projection is a point estimate and is re-measured on >= 15 nights
  before any decision. Night to night the shares move a lot (11-08 alone, 7 games: f(M) at 30/35/40 = 0.129 / 0.052 / 0.010; the five-night
  pool: 0.086 / 0.039 / 0.009).

## 12.10 Kill criteria, forking paths, what a result means

Fixed now and never re-tried after a result: the checkpoint grid, 150 s, 180 s, 90 s, 0.05, 0.10, the H2 family {30, 35, 40}, first-trigger entry,
the 97.5% level, the slices, B = 10,000 and seed 2026. A gate fail is recorded as "not freezable on the captured data" (12.9); the paper log
continues (it is not a rule), and the one allowed re-check is label-free (entries per night on >= 15 nights), dated **2026-12-15**. NO_POSITIVE_EV_FOUND
or INSUFFICIENT_SAMPLE closes H2 for this model and these features; one pre-registered follow-up at most. Not this rule: the No side, maker
orders, combos, other venues, overtime, a model refit on the 5-minute grid unless C5 says so.
**What changes in production: nothing** beyond the paper log and a silent card block (`ingame_checkpoints`, ships OFF in
`configs/jarvis.yaml`); no alert, no stake, no label. A PASS would be Devin's decision to act on.

## 12.11 Priors (stated before any checkpoint row meets an outcome; also drafted for `docs/PREDICTIONS_LEDGER.md`)

Claude / market agent. P(H2 passes at its look, if it is ever frozen) **0.02**; P(NO_POSITIVE_EV_FOUND at its look) 0.55; P(INSUFFICIENT_SAMPLE) 0.30;
P(INCONCLUSIVE) 0.13. Expected pooled H2 R$ at the look **-0.04** (80% -0.09 to +0.01): the fee is 1-2 cents on a 33-cent ask and the spread
is wider in-game than at the half. P(any S-family slice is BH-flagged) **0.08**; if one is, P(it is a checkpoint >= 30) 0.35 (the T508 attack points
there, the entry shares so far point away). P(M's log loss beats the market mid on eligible rows at >= 30
game-min, n >= 5,000 rows) **0.15**. P(the rule is frozen at all before 2027-01-01) **0.15** (the gate).
Advice or momentum for this build: follows Devin's 2026-11-09 request; Claude's own recommendation stays "paper-log it, do not freeze a P&L rule
on 39 entries per night".

## 12.12 Open choices for Devin, and the freeze order

* **C5. M refit on the 5-minute grid?** Default no (H2 = {30, 35, 40}). A refit adds checkpoint 45 and removes the interpolation caveat; costs a
  heavy run and does not change the gate.
* **C6. If the P&L rule cannot pass its gate, a calibration rule instead?** The secondary (M versus the market mid in log loss, thousands of
  rows per night) is far better powered than R$: a frozen "M - mid <= -0.005 at >= 30 game-min, date-clustered" rule would be testable this
  season. Default: draft it only if Devin asks; it answers "is the model better calibrated than the market late in the game", not "is there money".
* C1 (floor) as in section "Open choices": the gate above says +0.10 is not testable on 39 entries per night; +0.13 is marginal.
* **Order:** (1) adversary pre-freeze review of this section; (2) R1, the per-checkpoint glm_e refit (maintainer's command, heavy lock); (3) Devin's
  answers to C1, C5, C6; (4) re-measure entries per night on >= 15 nights label-free (2026-12-15); (5) gate decision per 12.9; only if it passes,
  the main session commits `prereg(HALFTIME_OVERS_VS_PRICE): <sha256>`. The drafted priors go to the predictions ledger before step 5.
