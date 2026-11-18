# LADDER_CAL: an honest ladder. Does a threshold-wise map, fitted on every integer threshold of past seasons, make production's P(Y >= k) honest off the main line, on integer support? Backtest first, then confirm. (pre-registration)

Status: **DRAFT, revision 1 (two-stage), branch `agent/modeler-calibration-program`. Revision 0 committed Tue 2026-11-17 19:58 CST; revision 1
written after Devin's decision relayed by the coordinator the same evening ("two-stage where history allows: backtest first, then confirm
only what survives"). Not frozen. No map fitted, no outcome read, no leg scored.**
- **How it becomes the rule:** the main session commits `prereg(LADDER_CAL): <sha256>` of everything above `=== RESULTS BELOW ===`
  (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LADDER_CAL.md | shasum -a 256`) after a harness with planted-future and
  synthetic tests exists, an adversary pre-freeze read returns READY, and Devin confirms section 10. Stage 1 runs only after the freeze,
  as the maintainer's command.
- **Origin:** Devin 2026-11-17 ("calibrated always"); DECISIONS 2026-11-17 19:08 (combo v1 main line only: "56% for 18+ points and 44% for
  32+"; reversed by "a pre-registered test of off-main rung calibration passes"). **Follows advice** (retrain survey part 3 rows 3 and 10).
  The two-stage shape is **Devin's decision** (2026-11-17 evening).
- **Season labels:** `games.season`. Stage 1: 2023 (fit/select) and 2024 (report). Stage 2a: season 2025, one logged touch (section 4,
  Devin's choice 10.1). Stage 2b: season 2026 live rows. Nothing else.
- **A stage-1 pass is selection, not a claim.** The claim, if any, comes from stage 2.

## 1. Question
Every ladder the app could draw (alternate rungs, Kalshi "N+", combo per-leg ladders, Analyst curves) needs P(Y >= k) away from the
book's main line. What exists:
- RAW (production's integer pmf `p_ge_full`): overconfident off the line (T491: OVERCONFIDENT at 80-90% in all four stats, -0.022 to -0.050).
- LINE_CALIBRATION at rungs: worse than raw (T494 CAL-main +0.044 log loss); fitted where z sits near 0; flattens ladders.
- The rung map (T494, RUNG_REFIT_FIXES, WOUNDED: 92% of the gain is the book main line): fitted only on listed DraftKings rungs at or below
  the main line with raw P >= 0.5 (r 1st-99th percentile -1.45 to 0.0). Kalshi rungs above the main line and every raw P < 0.5 are outside it.

Outcomes for every integer threshold are known for every played leg, listed rung or not, so the fit need not be restricted to listed rungs.
- **H1.** A threshold-wise map fitted on all integer thresholds within +-3 spreads of the DraftKings main line makes P(Y >= k) honest in every
  probability bin out of sample, and beats RAW on threshold-averaged Brier by the floor.
- **Null.** Not honest in some bin (most likely the tails, as T496 found above 0.65), or no gain over RAW.

## 2. The object (common to both stages)
1. **Legs and thresholds.** Leg i = (game, player, stat) with a DraftKings two-sided main x.5 line M at the t60 snapshot, played (DNP voided).
   RAW_k = P(Y >= k) from production's integer pmf (exact on the integer grid). K_i = every integer k with r_k = (k - 0.5 - M) / spread_i in
   [-3, +3], spread_i = max((q75 - q25)/1.349, 0.5), RAW_k clipped to [0.5/199, 1 - 0.5/199]. Quartiles: historical rows from the continuous
   grid `zq_to_quantiles(c, s)` exactly as LINE_CALIBRATION built its fit legs; live rows from the stored 19-point `q_grid`
   (`top_source.quartiles`); the two differ by at most 0.00022 in CAL (LINE_CAL_LIVE section 5).
2. **Map LADDER.** P_k = sigmoid(b0 + b1 x_k + b2 zm + b3 r_k + b4 r_k x_k + b5 bench + b6 bench x_k); x_k = logit RAW_k; zm = (M - q50) /
   spread clipped to [-5, 5]; bench = `starter10` < 0.5. T494's rung form, fitted on all thresholds. One weighted ridge logistic per stat
   (ridge 1.0 on non-intercept terms, IRLS tolerance 1e-10), weights `0.5 ** (age_days / 365)` x 1/|K_i| (each leg weighs one). Served ladder
   monotone by running minimum over increasing k; the share of thresholds that step changes is guard G-mono.
3. **Metrics.** P1 = per leg, mean over k in K_i of Brier(P_k) - Brier(RAW_k), averaged over legs (a band-limited ranked probability score
   difference on integer support); **floor -0.0015**. P2 = per stated-probability bin [0.03, 0.2), [0.2, 0.4), [0.4, 0.6), [0.6, 0.8),
   [0.8, 0.97], realised rate minus mean P_k, pooled over stats and thresholds; pass = 95% CI inside [-0.03, +0.03]. CIs: paired percentile
   bootstrap over games (B 2,000, seed 0), ratio estimator for P2; a date-clustered CI beside (the game CI binds).
4. Nothing derived from a price beyond the main point M enters any fit (M enters, as in T494; said on screen).

## 3. Stage 1: walk-forward backtest on 2023-24 (selection, not a claim)
**Rows (exactly).**
- Forecasts: `reports/odds_history/model_rows_{2023,2024}.parquet` (production out-of-fold rows: `p_ge`, `c`, `s`, `starter10`, `y`; 110,476
  rows in 2023 over four stats). These are the monthly-block out-of-fold forecasts every line test since T477 used (LINE_CAL_LIVE section 8.3:
  "the fit rows came from monthly-block OOF refits"): no row's forecast came from a model trained on that row's game.
- Main lines: `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet`, DraftKings t60 x.5 played legs: 53,247 (2023), 53,752 (2024) (LINE_CAL_LIVE
  capture audit; the same pins as LINE_CALIBRATION, sha256 050dba3e / 560a3808).
- Listed rungs (secondary S4 and the comparator): the DraftKings t60 alternate rungs ALT_RUNG_CAL used, `reports/alt_rung_cal/arrays_draftkings.npz`
  (134,462 rungs, 1,818 games, 2023-24 pooled; vendor alternate history starts 2023-12-07, the December 2023 defect window already excluded),
  including its walk-forward `CAL_rung` and `CAL_main` columns.

**Were any of these rows used to fit something this draft depends on?**
- LADDER fits its own map; it does not depend on the served LINE_CALIBRATION or rung maps. The served LINE_CALIBRATION map (sha 443743a1) was
  fit on every 2023-24 Pinnacle and DraftKings leg, so **scoring the served map on 2024 would be scoring fitted rows**. Stage 1 therefore never
  uses the served map: the LINE_CAL comparator on 2024 is the frozen LINE_CALIBRATION harness's own walk-forward month-block fits (each 2024
  month fit on 2023 plus earlier 2024 months), and the rung comparator is T494's walk-forward `CAL_rung` column.
- LADDER's own fit in stage 1 is walk-forward by calendar month: each 2024 month's map is fit on 2023 plus the 2024 months before it, never
  on rows of the month it scores. Cross-fitting is by date block (month), never by row; game clusters never straddle a fit and a score.
- **What cannot be avoided:** 2024 is exposed. T491 and T494 read 2024 rung outcomes, T493 read 2024 main-line outcomes, and this map's form
  (T494's terms) was chosen knowing T494's result. So stage 1 can select or kill; it cannot claim.

**Select and report.** Select season 2023: every choice is fixed now (band +-3 spreads, five bins, the seven terms). The 2023 step is a
sanity pass of the same construction on 2023 months fit on earlier 2023 months only (blocks with fewer than 2,000 fit legs per stat use the
identity and are counted); it can stop the rule (P1 point > 0), it cannot change anything. Report season 2024 as above.

**Stage-1 pass (all required):** on 2024, P1 <= -0.0015 with CI upper < 0; every P2 bin inside +-0.03; inside the rung map's own support
(listed DK rungs, raw >= 0.5, r in band) LADDER minus walk-forward `CAL_rung` Brier point <= +0.0005 (no material loss where a tested map
already exists); G-mono <= 2%; the shuffled-label refit (within-date shuffle of 2023-24 outcomes, K 20) fails P1 in at least 19 of 20; the
planted-future check passes (a future outcome planted in a fit row moves the fit; a line after tip is refused). Fail any: **the rule closes
at stage 1; no stage 2, no 2025 touch, no live look.** Attacks reported with the stage-1 result: drop the top five player-seasons, team
clustering, a second seed, zm knockout (how much is the book's line), the 2024 -> 2023 reverse fit (descriptive only: not walk-forward).

**Stage-1 gate (non-negotiable 8), arithmetic.**
- Fit rows: 53,247 DraftKings legs in 2023, about 13,300 per stat, against `MIN_FIT` 2,000 per stat: PASS. The first 2024 block (October
  2024) fits on 2023 alone; later blocks add 2024 months.
- Report rows: 53,752 legs on about 1,300 games (41 legs per game). P1 precision: T494's log-loss contrast on 134,462 listed rungs over 1,818
  games had a 95% half-width of 0.0012; Brier near p 0.5 is about half of log loss, so about 0.0006, and on 1,300 games about 0.0006 x
  sqrt(1,818 / 1,300) = 0.0007. The minimum detectable P1 at 80% power is about (1.96 + 0.84) x 0.00036 = 0.0010, below the 0.0015 floor, so
  the floor binds. PASS.
- P2 precision: thresholds of one leg are one outcome, so a bin has about one effective threshold per leg: 41 per game x 1,300 games = 53,300;
  half-width about 1.96 x sqrt(0.2 / 53,300) x sqrt(1.3 design effect) = 0.0043 against the +-0.03 band. PASS.
- Thresholds per leg: an estimate of 6 (fg3m) to 36 (pts) from typical spreads, **not measured**; whether 2023-24 fg3m legs reach r >= +2 is
  a label-free harness count before freeze (if fewer than 500 legs per stat do, that stat's band narrows to the measured support by that
  count alone).
- **Gate: PASS** on the documented counts, with the one open harness count. (Counts are from LINE_CAL_LIVE's audit and T494; I did not
  re-read the files' outcome columns.)

**Cost and where it runs.** Mac CPU (logistic fits; no GPU needed), outside game windows (before 11:00 CT or after the last lineups tick,
about 00:30 CT), under `data/ops/heavy.lock` (retry 120 s, `rmdir` in `finally`), progress printed per month block. Size: roughly 107,000
legs x up to 36 thresholds = under 2.5 million threshold rows per season, seven float64 columns, under 1 GB. Fits: 4 stats x about 9 month
blocks x (1 + 20 shuffles) = about 760 IRLS fits: an estimate of 10-20 minutes in total, not timed. No credits. Not Colab.

## 4. Stage 2: confirmation, run only if stage 1 passes
The served map is refit once on all 2023-24 legs (`fit_through` 2025-06-30), written with its sha before either stage-2 part runs, and never
refit inside this rule.
- **2a, one logged touch of season 2025 (Devin's choice 10.1; my default: yes, before 2b).** Rows: production's 2025-26 replay forecasts
  (REPLAY_2025_26: pretip runs at a fake clock, real tips) with DraftKings t60 main x.5 lines from `odds_history` (t60 prop snapshots for
  2023-2025 are held, per the movement note in configs/odds_history.yaml); played legs; all integer thresholds in the band. DraftKings
  alternate rungs for 2025 are **not** held (the alt phase refuses 2025 in code), so S4 is absent in 2a; Kalshi 2025 prop markets are held
  (`data/rehearsal/kalshi_props_2025.duckdb`), S3 descriptive only. Same P1 and P2 and the same pass condition as stage 1.
  - **Why this is a never-evaluated model:** the LADDER map is new; it is fit on 2023-24 only and has never been scored on any season after
    stage 1. Its inputs (production's 2025 replay pmf) have been seen at 2025 main lines (T480: raw A0 and AVG at Pinnacle and DraftKings x.5
    lines, pts; REPLAY_2025_26 exploratory), which is disclosed: we know raw is overconfident at 2025 main lines, which favours any
    shrinking map at the main line, not off it (the bins away from M are unseen).
  - **The row the archivist would log before the run (I do not log it):** `| <date> | LADDER_CAL stage 2a | rule sha256 <64 hex>, freeze
    commit <sha>; harness pin <sha>; map sha <sha> fit_through 2025-06-30 | season 2025 (2025-26): production replay pmfs (p_ge_full,
    quartiles, starter10 as of date) and DraftKings t60 main x.5 lines, played legs; outcomes y read once for P(Y >= k), k within +-3
    spreads of the main line | never-evaluated model (LADDER map, fit 2023-24 only); prior 2025 exposure T480 and REPLAY_2025_26 (raw at main
    lines) | Devin: "<his words>" | one touch; the result is written once and never re-run |`.
  - **Why in addition to 2b, not instead:** 2a answers in December on a full season (about 1,300 games) of the construction the map was fit
    for; 2b answers the different question of transport to this season's live system (the population the app serves).
- **2b, forward look on the live log.** Production rows scored by `forward_scores` (played; DNP voided), season 2026, slates strictly after
  the later of this rule's freeze, the stage-1 result and LINE_CAL_LIVE's look; forecast = the production row valid at the t60 line's
  `received_at`; DraftKings t60 main x.5 line. One look at the first settled date with >= 200 games with a leg and >= 8,000 legs; backstop
  2027-03-31; real clock only.
- **Verdict (per part).** HONEST_LADDER: P1 passes and all five P2 bins pass and the guards pass. BETTER_NOT_HONEST: P1 passes, a bin fails
  (named). NOT_FIXED: P1 fails. INSUFFICIENT_DATA: 2a with fewer than 600 games with a leg; 2b with fewer than 200 games or 8,000 legs at the
  backstop. A product proposal only if 2b is HONEST_LADDER (and 2a, if run, is not NOT_FIXED). If 2a is NOT_FIXED, 2b still runs once and
  the disagreement is reported, not resolved by a rerun.

## 5. Why these choices
| choice | protects against |
|---|---|
| backtest first, kill-only by design | spending a 2025 touch or live games on a map that fails where it was built |
| walk-forward by month; comparators never the served map on its own fit rows | scoring fitted rows; a flattered comparator |
| fit on every integer threshold, not only listed rungs | a map undefined where Kalshi and the combo builder need it |
| leg weight 1/|K_i| | wide ladders (pts) dominating |
| band +-3 spreads | extrapolation where fit data are thin |
| running-minimum monotonicity, counted | ladders that say P(18+) < P(20+) (11-10 app review M2) |
| five bins, +-0.03 | "honest" judged where ladders are used, tails included |
| no harm inside the rung map's support | replacing a tested map with a worse one where both apply |
| 2b after LINE_CAL_LIVE's look | a second look at LINE_CAL_LIVE |
| floor -0.0015 | LINE_CAL_LIVE's floor (Brier value of a 4 pp bias), carried |

## 6. Secondary results (labels; n, games and game CI on every row; every stage)
S1 per stat P1 and P2 (BH m 4 for P1); S2 by r band ([-3, -1), [-1, 1], (1, 3]) and above vs below M; S3 at Kalshi N+ thresholds (2a, 2b);
S4 at listed DraftKings rungs (stage 1, 2b); S5 comparators: LINE_CAL at every k (walk-forward fits in stage 1; the served map in 2a and 2b)
and the rung map inside its support; S6 whole distribution on integer support: randomised PIT of RAW and of the LADDER-implied pmf (row order
by game_id, player_id, stat; seed 0), PIT sd (ideal 0.2887), coverage at the 19 stored quantile levels 0.05..0.95 (mid-PIT for ties), RPS
LADDER - RAW over the full support as a no-harm guard (CI upper <= +0.005); S7 starter / bench, minutes-uncertain (P(play) < 0.9), month;
S8 Brier decomposition (reliability, resolution, uncertainty) of P_k beside RAW's.

## 7. Minimum-data gate for stage 2b (live), measured Tue 2026-11-17 19:48-19:52 CST, label-free
Scratch copy of the app export (sha256 `750c736e...`, data_version `6edbcef0c89d`, 2026-11-10..17) via `reports/prereg_calibration_program/gate.py`
(output sha256 `3629b223...`).

| check | measured | bar | pass |
|---|---|---|---|
| live legs | DraftKings main legs: 49 per game at t60, coverage 0.64 (CAL_EVERYWHERE, 10-20..23); 50.8 per game at t15 tonight | >= 30 legs/game | yes |
| live thresholds | DK alternate over rungs 426 per game (pts 132, reb 135, ast 87, fg3m 72) on 54 legs per game; Kalshi rungs 209 per game on 53 legs per game (35 games) | S3/S4 populated | yes |
| power P2 | synthetic half-width at 200 games about 0.010 (51 legs/game) to 0.012 (31); P(pass given gap 0) 0.99-1.00; given gap 0.015 0.66-0.88; false pass given gap 0.04: 0.00 | P(pass given gap 0) >= 0.8 | yes |
| look | 200 games with a DK t60 main leg from 2026-12-04 at coverage 0.64: **2027-01-22** | before the backstop | yes |

Stage 2a gate: 2025 DraftKings t60 main lines and replay forecasts are held (I did not count them: counting is a 2025 read); the harness
checks the 600-game minimum at run time, after the holdout row.

## 8. Interactions with frozen tests
- **LINE_CAL_LIVE:** stage 1 and 2a read no 2026 row; 2b's window starts after its look. At the main line LADDER and LINE_CALIBRATION differ;
  the card keeps LINE_CALIBRATION until this rule reads (CAL_SERVING_CHECKS C5).
- **GAME_SCRIPTS_FORWARD (being frozen tonight; window not read by me):** it scores joints of the same legs. Before freeze the main session
  checks its window; if it overlaps 2b, 2b's look moves after that rule's look or Devin accepts a disclosed marginal-vs-joint exposure.
- **ALT_RUNG_CAL (T494):** its arrays are read in stage 1; its map is unchanged and stays served inside its support.
- **FORWARD_PREREG_2026_27, UNDERS_FORWARD:** no shared pass word; S3 and S6 are labels.
- **Holdout:** 2a is the only 2025 read, behind a logged row; stage 1 and 2b never load 2025 (`MAX_FIT_DATE` 2025-06-30 refuses fit rows).

## 9. Playbook check (docs/MODEL_PLAYBOOK.md; classes: quantile and distribution layers; count models for the integer support)
1. **Information or capacity?** The book main line is information (T494: 92% of the rung gain); the rest is a shape correction.
2. **Enough data for the class?** 7 parameters per stat on about 13,000 legs per stat per season, each with many thresholds: ample for
   pooled bins; thin per stat in the tails (labels).
3. **Valid test?** Integer support for every arm; walk-forward by month in stage 1 with comparators that never score fitted rows; nested
   thresholds clustered by game; known-at pairing; DNP voided. Weak points: 2024 exposed (stage 1 is selection only); 2025 forecasts are
   replays; the live t60 DraftKings rate is October's.
4. **Lineage.** T339 (grid temperature/Platt slightly worse on CRPS; S6 guards it), T491, T494, T495/T496, COMBO_LINE_CAL (draft, not
   touched), combo mode 2026-11-09 (banded LINE_CAL; PIT sd 0.76-0.98).
5. **What it scores against.** Threshold-averaged Brier and bin reliability of P(Y >= k) against RAW; LINE_CAL at every k and the rung map as
   comparators; prices are not scored.

## 10. Devin's choices (defaults marked; his answers recorded here when relayed)
1. **Stage 2a, one 2025 touch:** *default yes, in addition to 2b, before it.* Alternatives: 2b only (the live log alone; answer late January);
   2a only (no live transport check; not recommended). Answer: pending.
2. **Anchor on the main line (zm) or market-free:** *default anchored.* Alternative: market-free (covers Kalshi rungs without a DK main line,
   33% of them on 10-20..23; likely less honest). Answer: pending.
3. **Band:** *default +-3 spreads*; alternative +-2. Answer: pending.
4. **Main-line number after a pass:** *default keep LINE_CALIBRATION at the main line*, LADDER elsewhere, kink disclosed; alternative
   LADDER everywhere. Answer: pending.
5. **Display lock for 2b's window:** *default yes until the look* (about 2027-01-22). Answer: pending.

## 11. Priors (drafted for PREDICTIONS_LEDGER, not appended)
P(stage 1 passes) 0.55; P(a P2 tail bin is the cause, given stage 1 fails) 0.6; P(2a HONEST_LADDER given stage 1 passes) 0.55; P(2b
HONEST_LADDER given stage 1 passes) 0.45; expected stage-1 P1 -0.006 (80% interval -0.011 to -0.002).

## 12. Commands
Gate (label-free): CAL_SERVING_CHECKS section 8. Stage 1, 2a and the served fit: harness not written; maintainer commands under
`data/ops/heavy.lock` on the Mac CPU, written after the freeze review. 2a additionally needs the archivist's committed HOLDOUT_ACCESS_LOG row
first.

=== RESULTS BELOW ===

(none)
