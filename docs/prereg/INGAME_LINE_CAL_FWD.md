# INGAME_LINE_CAL_FWD: late in a game, is the in-game line model's chance of a player finishing at or over a Kalshi line better calibrated than simple arithmetic on the box score, and than the market? (pre-registration, forward only, no money)

Status: **DRAFT 2026-11-17, modeler, branch `agent/modeler-live-model` off `main` 55ccf5ca. NOT FROZEN; no hash taken. No checkpoint row's
outcome, settlement or final box score was read; no row was scored; no model or baseline probability was computed for any row.** The gate
counts below are label-free replays of the write-once capture (section 9). The main session reads this with Devin and commits it
(`prereg(INGAME_LINE_CAL_FWD): <sha256>`); the modeler cannot commit to `main`.

**Origin.** Devin, 2026-11-17: the in-game line model (T508 arm M) has logged every 5 game-minutes since 2026-11-10 (DECISIONS 2026-11-10
10:45, `configs/jarvis.yaml` `ingame_checkpoints.enabled: true`) and is edited onto the Discord game cards with no frozen rule; he wants a
live chance shown in the app labelled "experimental, not yet tested", and a rule that would make it tested. The decisions review of
2026-11-17 (row 6) asks for exactly this: freeze or declare descriptive before more rows accrue. HALFTIME_OVERS_VS_PRICE 12.12 C6 proposed
this calibration question as the testable alternative to its P&L rule, which fails its own gate (177 nights needed, 106 allowed).

**Advice or momentum: follows advice** (decisions review row 6; HALFTIME_OVERS_VS_PRICE C6) **and Devin's request.** Claude flags one thing
against it: the T508 attack found M's edge over a fair player-aware baseline at 0.0024 log loss overall and -0.004 to -0.007 at game minute
>= 30, about the size of this floor. A pass is possible but not likely, and a pass against the market is unlikely (prior, section 12).

**Model class:** two-stage gradient-boosted trees (T508 arm M: LightGBM remaining-minutes regressor, then a monotone LightGBM classifier of
`final >= line`, pooled Platt), served from the pinned artifact `b9b53d636279337ab01c56f6146fc20936d354dd0a01ec1f42bc262efcb9d053`. Scored
against an **arithmetic count baseline** (no fitted parameters except an optional historical Platt) and a **market price**.

## Playbook check (docs/MODEL_PLAYBOOK.md)
1. **Information or capacity.** The in-game state (current stat, minutes, fouls, margin, clock) is information, true and unknown at tip. But
   it is public and the market sees it too, and simple arithmetic on the same state captures most of it. M's own contribution over the
   arithmetic is capacity (how minutes bend with margin and fouls). The T508 lesson (94% of the gain was a weak baseline) is the reason the
   baseline here is given the player's own pre-game rate and minutes, exactly what M sees.
2. **Enough data for this class.** Training is not the question (T508 gate PASS on 2019-2024 play-by-play; smallest pts cell 3,575
   player-games). The forward evaluation sample is: section 9.
3. **Test validity.** Same rows for every arm; integer support for the baseline (NegBin on the integer grid; the line is an integer strike,
   so every arm is a probability of the same binary event and log loss compares like with like); known-at stamps from the store
   (`known_at`, `within_prereg_window`); outcome from Kalshi's settlement with a box-score integrity check; game- and date-clustered paired
   bootstrap; power stated with its assumption; only rows logged after the freeze. The comparator has every input M has (section 3).
   The live inputs (ESPN integer minutes, a feed about 67 s behind ESPN's own clock, docs/reviews/live_model_2026-11-17.md Part 1) are the
   same for every arm.
4. **Lineage.** T508 (IN_GAME_LINE_MODEL, frozen, PASS against B0 and B1) and the T508 attack (WOUNDED: B0 used the league rate; against
   glm_e the gain was under the floor). The baseline here is T508's B0 with the league rate replaced by the player's own pre-game rate and
   share (the attack's fix, kept as arithmetic). Carried forward: integer NegBin with T508's VMRs, the period-aware Q4 blowout rule, the
   better-of baselines rule, top-5 concentration, planted-future identity. Dropped: B1's recalibrated recency variant (needs a fit) and
   glm_e (fitted at e = 24 only; off its support at 30-40, HALFTIME_OVERS_VS_PRICE 12.3), reported as a descriptive comparator only.
5. **Scored against.** Log loss of P(final >= k) at the Kalshi strike, natural log, per row; not CRPS and not money. The money question
   stays HALFTIME_OVERS_VS_PRICE (draft, unfreezable).

## Hypotheses (two primaries, Bonferroni)
* **P1 (model vs arithmetic).** At checkpoints 30, 35 and 40 game-minutes, mean log loss of M minus that of the binding arithmetic baseline
  A* (section 3) is <= -0.005.
* **P2 (model vs market).** On the subset of the same rows with a two-sided quote (bid present, ask - bid <= 10 cents), mean log loss of M
  minus that of the market mid is <= -0.005.
* H0 for each: the difference is above the floor (the model adds less than 0.005 over arithmetic, or over the market).

## 1. Rows (exact)
From `data/jarvis/ingame_checkpoints.duckdb`, table `checkpoint_scored`, read by the scorer only after the look date, through a copy (the
live writer connects without retry; contract `docs/contracts/ingame_checkpoint_snapshot_v1.md`):
* `mode = 'live'`, `within_prereg_window = true`, `artifact_digest =` the pinned digest above, `model_support = 'trained'`, `n_prior >= 5`.
* **Forward only:** `slate_date` strictly after the ET date of the freeze commit (the whole night after the commit, so no night is split).
  Every row with an earlier `slate_date` is **burned**: never scored, never read for an outcome, never used to choose anything. Counting
  burned rows (n, by night, checkpoint and stat) is allowed and is all this draft did.
* Primary rows: `checkpoint in (30, 35, 40)`. Every checkpoint 5-45 is a slice (section 7).
* Regular season only; through 2027-04-11. Overtime checkpoints do not exist (the scorer refuses them); games that later go to overtime
  stay in (their settlement includes overtime, for every arm alike) and are a guard (G9).

## 2. Outcome
`y = 1` iff Kalshi settles the market `yes` (the store's `ticker`; `data/kalshi/raw/<date>/*_settled_*`, as HALFTIME_OVERS_VS_PRICE s1).
A `scalar` settlement (fair-price resolution) excludes the row (counted; should not happen, every scored player had entered the game). A
row with no stored settlement uses the ESPN final summary box (`STATUS_FINAL`, write-once capture): `y = 1` iff the stat `>= k` (counted).
Integrity (G3): on rows with both, Kalshi and the box must agree on >= 99%.

## 3. Arms (every arm on the same rows; every probability clipped to [0.005, 0.995])
* **M:** the stored `p_model` (no recomputation).
* **A1r, arithmetic, recency inputs (the same pre-game inputs M has).** From the stored row: current stat `x = cur`, minutes played
  `t = minutes`, elapsed game minutes `e = e_used`, team margin `d = margin`, pre-game mean `mu = pregame_mean`, pre-game minutes
  `m = rec_minutes`, strike `k`.
  - pre-game per-minute rate `r0 = mu / max(m, 1)`; in-game rate `r = (x + 10 r0) / (t + 10)` (10 pseudo-minutes, T508's B0 weight);
  - pre-game share of the game `s0 = min(m, 48) / 48`; share so far `s = (t + 6 s0) / (e + 6)` (6 pseudo game-minutes, T508's B0 weight);
  - game minutes left outside Q4 `g3 = max(0, 36 - e)` and in Q4 `g4 = 48 - max(e, 36)`; Q4 factor `q = 0.5` if `|d| >= 20` else `1.0`;
  - expected remaining minutes `R = min(s (g3 + q g4), 48 - e)`; expected remaining stat `lam = r R`;
  - remaining stat `Y ~ NegBin(mean lam, variance v lam)` on the integer grid, `v` = pts 2.5, reb 1.4, ast 1.6, fg3m 1.3 (T508's VMRs; if
    `lam = 0`, `Y = 0`); `p = P(Y >= k - x)`.
* **A1p, arithmetic, production inputs (the "pre-game calibrated" numbers the app shows).** The same formula with `mu` = the production
  forward row's mean given plays and `m` = its projected minutes (`forward_predictions`, latest `made_at <= tipoff - 60 min`, as
  FORWARD_PREREG_2026_27 s3; read-only copy of `nba.duckdb`). A row whose player has no such forward row uses A1r's value (counted).
* **A1e (optional, open choice C3):** A1r passed through a per-checkpoint 2-parameter Platt fitted on 2019-2024 T508 rows only (heavy
  maintainer command before the freeze; digest pinned in the freeze commit). If C3 is declined, A1e does not exist.
* **A\* (binding baseline)** = the one of {A1r, A1p, A1e} with the lowest mean log loss on the scored primary rows. Choosing the best
  baseline after the outcomes can only make P1 harder for M (the T508 "better baseline" rule).
* **Market:** `mid = (bid_c + ask_c) / 200` on rows with `bid_c` present and `ask_c - bid_c <= 10`; P2 uses only those rows, with M on
  the same rows. The ask alone is printed beside it.
* **Descriptive only (never a verdict):** `p_glm_e` as stored (off its support), and B0 as in T508 (league rate) to show how much of
  T508's gap the player-aware arithmetic closes.

## 4. Metric, floor, interval, words
* **Metric:** per-row `LL = -[y ln p + (1 - y) ln(1 - p)]`; the statistic is the mean over rows of `LL(M) - LL(baseline)` (paired).
* **Floor:** **-0.005** (the project's practical log-loss floor; C1).
* **Interval:** paired percentile bootstrap of the mean difference, B = 10,000, `numpy.random.default_rng(2026)`, resampling **games** and,
  separately, **ET dates**; **97.5% two-sided** (Bonferroni over P1 and P2); **the wider of the two decides**. Row-level intervals are
  printed for contrast only (anti-conservative; BEST_PRACTICES).
* **Words (each primary separately):** `BEATS` iff point <= -0.005 AND the 97.5% upper bound < 0 AND the guards hold; `NO_PRACTICAL_GAIN`
  iff the 97.5% lower bound > -0.005; `INCONCLUSIVE` otherwise; `INSUFFICIENT_SAMPLE` iff the season ends before the look count is met.
  A `BEATS` with a failed guard is reported as `BEATS_GUARD_FAILED` and is not a pass.
* **Always printed (descriptive, 95%):** market mid minus A\* on the P2 rows ("is the market better than arithmetic?"), and every arm's
  mean log loss with n rows, n units (game, player, stat), n games, n dates.

## 5. One look
At the earlier of: (a) the first night on which the label-free count of post-freeze primary rows reaches **>= 240 games AND >= 30 ET
dates** (counted from the store: rows, games, dates only), scored **2 days later** so settlements land; (b) 2027-04-13. Status prints
(counts only, no score, no outcome): 2026-12-15 and at the look. The rule closes at its look; no extension, no second look.

## 6. Guards (all must hold for a BEATS on P1; G1-G4 and G7-G9 for P2)
* **G1 same rows:** every arm defined on every scored row; a row missing any arm is dropped for all (counted).
* **G2 pinned model:** every scored row carries the pinned `artifact_digest`; a different digest on any post-freeze row voids the read
  (the model changed mid-test: a new rule id).
* **G3 settlement integrity:** >= 99% agreement between Kalshi settlement and the ESPN final box on rows with both.
* **G4 concentration:** top-5 players' share of the total gain (sum of positive per-player contributions of `LL(A*) - LL(M)`) <= 0.5; no
  single date > 0.2 of it.
* **G5 calibration of M:** logistic recalibration slope of M on the scored primary rows in [0.85, 1.15] (P1 only).
* **G6 arithmetic code identity:** the A1 code is the module hashed at the freeze; re-running it on the stored rows and state files
  reproduces every A1 value bit for bit; a planted-future copy (each row's state replaced by the next summary after its checkpoint) changes
  A1r on > 90% of rows (the code reads the state it is given and nothing later).
* **G7 freshness:** the sign of the point holds on rows with `lateness_s <= 60` and `play_age_s <= 90` (n printed).
* **G8 no burned row:** the scorer asserts `min(slate_date) >` the freeze date and prints the count of rows it refused for that reason.
* **G9 overtime:** the sign of the point holds without games that went to overtime.

## 7. Slices (S-family; descriptive; never a verdict)
For each: n rows / units / games / dates, `LL(M) - LL(A*)` and (on two-sided rows) `LL(M) - LL(mid)` with 95% game-clustered intervals,
and the one-sided bootstrap p for `M - A* < 0`; **BH at q = 0.10** over the slice family. A slice is "flagged for a new rule" only if
BH-significant AND point <= -0.005 AND >= 2,000 rows over >= 20 dates; a flag is a hypothesis, not a claim.
1-9. Each checkpoint 5, 10, ..., 45 (5 and 45 are extrapolated by M: printed, never flaggable).
10-13. Stat: pts, reb, ast, fg3m.
14. Blowout: `|margin| >= 20` at checkpoints 30-40.
15. Close: `|margin| <= 5` at checkpoints 35-40.
16. Foul trouble (the Maymin, Maymin and Shen "Q+1" definition): `fouls >= period + 1`.
17-18. Starter / bench (`starter`).
Also printed, outside the family: reliability tables (10 bins) for M, A\*, mid on the primary rows; calibration intercept and slope of each.

## 8. What the app and the cards may show before the look (so the display does not leak the test)
* **Allowed:** for a game in progress, the newest checkpoint's `p_model` per (player, line), with the checkpoint, game clock and `known_at`
  age, under the label **"Experimental, not yet tested"** (replacing or beside today's "UNPROVEN" label). The current box score and the final
  stat after the game (public facts).
* **Not allowed, anywhere (app, Discord cards, JARVIS check-ins, morning report, agent reports):** any running score of this model against
  outcomes: hit rate, "right X of Y", log loss, Brier, a reliability chart, "model vs market" tallies, would-have-won P&L, or a model number
  shown next to the settled result for a finished game. Finished games hide the model numbers (already the snapshot contract).
* **Nobody computes an interim score.** Status prints are counts only. Why: a visible running score invites stopping, re-specifying or
  "fixing" the model on what it shows (optional stopping and forking paths), and Devin's reaction to it would become an input to the test.
* A model change (new artifact) during the window is allowed for the app only under a new rule id; this rule's rows then end (G2).

## 9. Minimum-data gate (non-negotiable 8), measured label-free on the real capture
Source: `nba.markets.checkpoint_scorer.CheckpointReader` replays (the `gate` path: summaries and Kalshi pages only; no model, no outcome,
no final box) on nights **2026-11-10 .. 11-16**, the nights the live store has logged (data version: `data/raw/live_2026-11-1{0..6}/`
as of 2026-11-17 ~20:30 CST). Scratch wrappers counted by stat and by quote shape. **The live store itself was not opened** (game night,
and no DuckDB under the main checkout may be opened by this agent): its counts can differ by live-only refusals (`refused_late_write`,
`tick_not_closed`) and by `n_prior >= 5`. Command for the main session to run when no game is live (copy first; the writer does not retry):
`cp data/jarvis/ingame_checkpoints.duckdb /tmp/ick.duckdb && uv run python -c "import duckdb; print(duckdb.connect('/tmp/ick.duckdb', read_only=True).sql(\"select slate_date, checkpoint, stat, count(*) n, count(distinct event_id) games, avg(within_prereg_window::int) inwin from checkpoint_scored where mode='live' group by all order by all\").fetchall())"`
(label-free: the table has no outcome column). Expected: about the replay counts below.

| checkpoint | games valid | rows (on-grid strikes): pts / reb / ast / fg3m | rows | units | refused |
|---|---|---|---|---|---|
| 5 | 50 | 1,645 / 1,677 / 1,430 / 1,564 | 6,316 | 1,855 | 4 stale |
| 10 | 51 | 2,226 / 2,355 / 1,843 / 2,123 | 8,547 | 2,588 | 0 |
| 15 | 50 | 2,179 / 2,258 / 1,745 / 2,025 | 8,207 | 2,573 | 4 missed late |
| 20 | 50 | 2,160 / 2,137 / 1,698 / 1,932 | 7,927 | 2,576 | 4 stale |
| 25 | 49 | 2,044 / 1,970 / 1,562 / 1,767 | 7,343 | 2,501 | 8 missed late |
| **30** | 46 | 1,811 / 1,698 / 1,356 / 1,544 | **6,409** | 2,320 | 8 missed late, 12 stale |
| **35** | 51 | 1,873 / 1,777 / 1,403 / 1,611 | **6,664** | 2,526 | 0 |
| **40** | 49 | 1,584 / 1,533 / 1,255 / 1,421 | **5,793** | 2,359 | 8 stale |
| 45 | 51 | 1,238 / 1,364 / 1,167 / 1,260 | 5,029 | 2,259 | 0 |

**Primary (30/35/40): 18,866 rows in 51 games over 7 nights** (370 rows and 141 unit-checkpoints per game; 51 of 51 scheduled games
captured). **Two-sided quotes (P2 rows): 12,631 of 18,866 (0.67)**; by night 0.50 to 0.89. Blowout rows (|margin| >= 20): 3,839 (0.20).

**Power.** The decision quantity is a game-clustered mean difference. Planning value for the SD of a game's mean difference near the floor:
**0.025** (assumption: T508-attack's M - glm_e pts interval [-0.0031, -0.0018] on 1,230 games implies at most about 0.012 per game for two
close forecasters on about 570 rows a game; doubled for the forward rows' different mix and the market arm). At 97.5% two-sided and 80%
power, `G_req = (2.241 + 0.842)^2 x SD^2 / 0.005^2`:

| SD per game | 0.015 | 0.020 | **0.025** | 0.033 | 0.045 |
|---|---|---|---|---|---|
| games needed | 86 | 152 | **238** | 414 | 770 |
| look trigger if frozen by 2026-11-19 (first night 11-20), with >= 30 dates | 12-29 | 12-29 | **12-30** | 2027-01-22 | 2027-03-16 |

The look count is fixed at **240 games AND 30 dates** (the planning value); the realized interval decides the word, so an SD larger than
planned yields INCONCLUSIVE, not a false claim. **Gate rule:** freezable iff the projected nights to the look count <= 0.8 x the nights
remaining after the freeze. From 11-20: 126 nights, 974 games remain (`data/schedule/live_tips_2026-27.parquet`); 0.8 x 126 = 100; the
look count is reached in **31 nights** at today's capture (7.7 games a night, 1.00 capture). **PASS** (it would still pass at SD <= about
0.044). **Projected look: trigger 2026-12-30, scored 2027-01-01** if frozen by 2026-11-19 (2027-01-03 / 01-05 if frozen by 11-24; 01-09 /
01-11 if frozen by 12-01).

**Data-capture audit (non-negotiable 8, second half).** The only in-game Kalshi prop prices we hold start 2026-10-21 (live capture);
HALFTIME_OVERS_VS_PRICE s8 and 12.9 audited every other store (15-minute snapshots too coarse; 2025-26 rehearsal stores have no prop
prices; the 2025-26 1-minute candle repair would spend M's one season-2025 touch). Capture of scheduled games over the 7 nights is 1.00;
4.4% of late-family decisions were refused (stale or missed), repairable by the fresh poller only if the scorer read it (it does not;
not this rule). The gate passes without any repair.

## 10. What each choice protects against
* Forward only, burned rows never read: the 7+ nights already logged were displayed on cards and could have shaped this text.
* Same inputs for A1r as M (stored recency mean and minutes): the T508 failure (a baseline missing the player's own rate).
* A1p from the production forward row: a model that beats recency arithmetic but not the numbers the app already shows has added nothing.
* Best-of baselines after outcomes: a weak baseline handing M a win; it can only make P1 harder.
* Integer NegBin for the baseline: scoring a continuous approximation against an integer strike (T114-T118 support lesson).
* Two-sided quotes with spread <= 10 c for the market: a stale ask or a one-sided book posing as the market's belief.
* 97.5%, wider of game and date clustering: two primaries, and one night's environment counted as many games.
* One look on a label-free count: optional stopping. Status prints with counts only: peeking.
* Display rule (section 8): the test leaking into decisions through a visible tally.
* G2 pinned digest: a model swapped mid-test.
* Late family as primary: eight extra looks at the same games; earlier checkpoints are slices under BH.

## 11. Kill criteria, forking paths, what a result means
* Fixed now and never re-tried after a result: the late family {30, 35, 40}, the floor, the A1 formula and constants, the clip, the spread
  cap, 97.5%, B, seed, the look count, the slices and their cut-offs.
* **Gate fails at the freeze** (it should not): not frozen, recorded as such. **NO_PRACTICAL_GAIN on P1**: the app's live chance should
  be the arithmetic (cheaper, explainable, same skill); M stays a display at most. **BEATS on P1, NO_PRACTICAL_GAIN on P2**: M is a better
  explainer than arithmetic but the market already knows it; still no bet signal. **BEATS on P2**: a candidate for a forward money rule
  (a new id), Devin's call. **INCONCLUSIVE / INSUFFICIENT_SAMPLE**: closed; the label "experimental, not yet tested" stays.
* One pre-registered follow-up at most (default: the BH-flagged slice, if any).
* **What changes in production: nothing.** A verdict changes the app's label and, at most, which number the live view shows; promotion
  and any outward-facing change are Devin's.

## 12. Priors (stated before any post-freeze row exists; drafted for `docs/PREDICTIONS_LEDGER.md`, see the review)
Modeler: P(P1 BEATS) **0.25**; P(P1 NO_PRACTICAL_GAIN) 0.40; P(P1 INCONCLUSIVE) 0.30; P(INSUFFICIENT_SAMPLE) 0.05. Expected P1 point -0.004
(80% -0.010 to +0.001). P(P2 BEATS) **0.05**; P(P2 NO_PRACTICAL_GAIN) 0.55; expected P2 point +0.004 (80% -0.004 to +0.015: the market
better). P(market mid beats A\* by >= 0.005, descriptive) 0.60. P(A\* = A1p, the production-input arithmetic) 0.45. P(G5 calibration
slope of M in [0.85, 1.15]) 0.70. P(frozen by 2026-11-24) 0.60.

## 13. Before the freeze (order)
1. Adversary pre-freeze review of this text.
2. Devin's answers to the open choices (defaults below).
3. Build, outcome-blind, before the hash: the A1 module and its tests (`nba/eval/ingame_arith_baseline.py`, `tests/eval/`, drafted on this
   branch), hashed with this text; if C3 = yes, the A1e Platt fit (maintainer's heavy command, `season <= 2024`, digest pinned).
4. The main session runs the store count in section 9 (label-free) and appends the numbers to this text.
5. Priors appended to `docs/PREDICTIONS_LEDGER.md`; then `prereg(INGAME_LINE_CAL_FWD): <sha256>` on `main`.
6. A DECISIONS row superseding L1375's "no live use" for display only, with the label of section 8.
The scorer (`score` entry; reads a copy of the store after the look) is written after the freeze and checked by the adversary against this
text before the look (T510 precedent).

## Open choices for Devin (defaults taken in the text)
* **C1. Floor.** Default -0.005 (the project floor). Alternative -0.0025: about 4x the games (about 950), not reachable this season.
* **C2. Primary family.** Default the late checkpoints {30, 35, 40} (where the T508 attack found the residual). Alternative: all nine
  pooled (more rows, but early checkpoints are where arithmetic and M agree most, diluting any gain).
* **C3. Historical Platt for the arithmetic baseline (A1e).** Default **yes** (fair calibration: M has a Platt; one heavy maintainer
  command on 2019-2024 before the freeze). If no: A\* = best of {A1r, A1p}, disclosed as "baseline uncalibrated".
* **C4. P2 as a co-primary.** Default yes (Bonferroni over two). Alternative: P2 descriptive only (P1 then at 95%: about 200 games at the planning SD; the 30-date minimum binds first).
* **C5. Label text.** Default "Experimental, not yet tested" plus the checkpoint age. Alternative: keep "UNPROVEN ..." beside it.
* **C6. Relation to HALFTIME_OVERS_VS_PRICE H2.** Default: this rule is the in-game model's test; H2 (money) stays a draft and is not
  frozen this season. No conflict: H2 scores entries and P&L, this rule scores calibration on all rows.
