# LADDER_CAL: an honest ladder. Does a threshold-wise map, fitted on every integer threshold of 2023-24, make production's P(Y >= k) honest off the main line, on integer support, on the live log? (pre-registration)

Status: **DRAFT, revision 0, written by the modeler Tue 2026-11-17 20:20 CST (clock read by `date`), branch
`agent/modeler-calibration-program`. Not frozen. No map fitted, no outcome read, no 2026 leg scored.**
- **How it becomes the rule:** the main session commits `prereg(LADDER_CAL): <sha256>` of everything above `=== RESULTS BELOW ===`
  (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LADDER_CAL.md | shasum -a 256`) after a harness and its planted-future and
  synthetic tests exist, an adversary pre-freeze read returns READY, and Devin confirms section 10. The fit (Stage 0) runs only after the
  freeze, as the maintainer's command.
- **Origin:** Devin 2026-11-17 ("calibrated always"); DECISIONS 2026-11-17 19:08 (combo v1 shows the main line only because "applying
  the main-line map at other rungs flattens the ladder: 56% for 18+ points and 44% for 32+ for one player"; reversed by "a pre-registered
  test of off-main rung calibration passes"). **Follows advice** (retrain survey part 3 rows 3 and 10; CALIBRATION_EVERYWHERE section 4.3).
- **Season labels:** `games.season`. Fit: 2023 and 2024 only (`MAX_FIT_DATE` 2025-06-30, refused after). Report: season 2026 live rows
  after this rule's freeze and after LINE_CAL_LIVE's look. Season 2025 is never loaded.

## 1. Question
Every ladder the app could draw (alternate rungs, Kalshi "N+", the combo builder's per-leg ladders, Analyst-mode curves) needs P(Y >= k)
at thresholds k away from the book's main line. What exists:
- RAW (production's integer pmf `p_ge_full`): overconfident off the line (T491: OVERCONFIDENT at 80-90% in all four stats, -0.022 to -0.050).
- LINE_CALIBRATION applied at rungs: worse than raw (T494 CAL-main +0.044 log loss); it was fitted where z sits near 0 and flattens ladders.
- The rung map (T494, RUNG_REFIT_FIXES, WOUNDED: 92% of the gain is the book main line): fitted only on listed DraftKings rungs at or below
  the main line with raw P >= 0.5 (r 1st-99th percentile -1.45 to 0.0). Kalshi rungs above the main line and every raw P < 0.5 are outside it
  (390 of 1,001 Kalshi markets on 10-20..23 were below half).

The gap is a map fitted where the ladder is used. Outcomes for every integer threshold are known in 2023-24 for every played leg, whether or
not a book listed that rung, so the fit need not be restricted to listed rungs.
- **H1.** A threshold-wise map fitted on all integer thresholds within +-3 spreads of the book main line (2023-24) makes P(Y >= k) honest in
  every probability bin on live 2026 legs, and beats RAW on threshold-averaged Brier by the floor.
- **Null.** The fitted ladder is not honest live in some bin (most likely the tails, as T496 found for legs above 0.65), or it does not beat
  RAW.

## 2. The rule in ten lines
1. **Object.** For leg i (game, player, stat) with a DraftKings two-sided main x.5 line M at the t60 snapshot, and integer threshold k:
   RAW_k = `p_ge_full` at k (exact on the integer grid). Thresholds K_i = every integer k with r_k = (k - 0.5 - M) / spread_i in [-3, +3],
   spread_i = max((q75 - q25)/1.349, 0.5), and RAW_k in [0.5/199, 1 - 0.5/199]. Quartiles: live from the stored 19-point `q_grid`
   (`top_source.quartiles`, the served definition); fit side from the continuous grid `zq_to_quantiles(c, s)` exactly as LINE_CALIBRATION
   built its fit legs (the two differ by at most 0.00022 in CAL, LINE_CAL_LIVE section 5).
2. **Map LADDER.** P_k = sigmoid(b0 + b1 x_k + b2 zm + b3 r_k + b4 r_k x_k + b5 bench + b6 bench x_k), x_k = logit RAW_k, zm = (M - q50) /
   spread (clipped to [-5, 5]), bench = `starter10` < 0.5 (fit and serve). This is T494's rung form, fitted on all thresholds. One weighted
   ridge logistic per stat (ridge 1.0 on non-intercept terms, IRLS tol 1e-10), weights `0.5 ** (age_days / 365)` times 1/|K_i| (each leg
   weighs one in total). Monotone in k by construction of the served ladder: P_k is replaced by its running minimum over increasing k;
   the number of thresholds changed by that step is a guard (G-mono, section 7).
3. **Fit data.** 2023-24 production out-of-fold rows (`reports/odds_history/model_rows_{2023,2024}.parquet`: `p_ge`, `c`, `s`, `starter10`,
   `y`) joined to DraftKings t60 main lines (`data/ops/model_clv_pairs/pairs_{2023,2024}.parquet`, played legs, x.5). Nothing derived from a
   price beyond the main point M enters the fit (M enters, as in T494; say so on screen).
4. **Stage 0 (kill-only rehearsal, run once after the freeze).** Walk-forward by month on 2024 (fit on 2023 plus earlier 2024 months). If
   threshold-averaged Brier LADDER - RAW has a game-clustered one-sided lower bound > 0, the rule closes before any live leg (the map hurts on
   the population it was fitted for). 2024 is exposed (T491, T494): Stage 0 can kill, never pass.
5. **Served map.** Fit on all 2023-24 legs (`fit_through` 2025-06-30), written once with its sha before the live window opens. No refit
   inside this rule.
6. **Live legs.** Production rows scored by `forward_scores` (played; DNP voided), season 2026, slates strictly after the later of this
   rule's freeze and LINE_CAL_LIVE's look; forecast = the production row valid at the t60 line's `received_at`; DraftKings t60 main x.5 line.
7. **Primary metric P1.** Per leg, mean over k in K_i of Brier(P_k) - Brier(RAW_k); averaged over legs; 95% paired percentile bootstrap over
   games (B 2,000, seed 0), date CI beside. **Floor -0.0015.**
8. **Honesty P2.** Reliability of P_k in five stated-probability bins [0.03, 0.2), [0.2, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 0.97], pooled
   over stats and thresholds: per bin, realised rate minus mean P_k, game-clustered (ratio estimator) 95% CI inside [-0.03, +0.03].
9. **Verdict.** HONEST_LADDER: P1 passes (<= -0.0015, CI upper < 0) and all five P2 bins pass and the guards pass. BETTER_NOT_HONEST: P1
   passes, some bin fails (named). NOT_FIXED: P1 fails. INSUFFICIENT_DATA: fewer than 200 games or 8,000 legs at the backstop.
10. **One look** at the first settled date with >= 200 games with a leg and >= 8,000 legs; backstop 2027-03-31; real clock only.

## 3. Why these choices
| choice | protects against |
|---|---|
| fit on every integer threshold, not only listed rungs | a map that is undefined where Kalshi and the combo builder need it (above the main line, raw P < 0.5) |
| leg weight 1/|K_i| | legs with wide ladders (pts) dominating the fit |
| r band +-3 spreads | extrapolation beyond where fit data are dense; tails beyond the band show nothing |
| running-minimum monotonicity, counted | a ladder that says P(18+) < P(20+) (the 11-10 app review M2 inversion) |
| Brier averaged over thresholds | one metric for the whole curve: on integer support it is a band-limited ranked probability score (RPS), the integer CRPS |
| five bins, equivalence +-0.03 | "honest" judged where it is used (the tails are where T495/T496 found overconfidence) |
| Stage 0 kill-only on exposed 2024 | spending live games on a map that fails where it was built |
| window after LINE_CAL_LIVE's look | a second look at LINE_CAL_LIVE through its legs |
| floor -0.0015 | LINE_CAL_LIVE's floor (Brier value of a 4 pp bias), carried |

## 4. Secondary results (labels only; n, games and game CI on every row)
- S1 per stat P1 and P2 (BH m 4 for P1). S2 by r band ([-3, -1), [-1, 1], (1, 3]) and above vs below the main line. S3 **at Kalshi N+
  thresholds** (k = N) on legs with a Kalshi market, the decision surface; S4 at listed DraftKings alternate rungs. S5 comparator arms:
  LINE_CALIBRATION applied at every k (today's combo behaviour; expected worse, T494) and the frozen rung map inside its own support.
- S6 whole distribution on integer support: randomised PIT of RAW and of the LADDER-implied pmf (fixed row order by game_id, player_id,
  stat; seed 0), PIT sd (ideal 0.2887) and coverage at the 19 stored quantile levels 0.05..0.95 for RAW (the fraction of y at or below each
  integer-supported quantile, with mid-PIT for ties); RPS LADDER - RAW over the full support as a no-harm guard (CI upper <= +0.005).
- S7 starter / bench; minutes-uncertain (P(play) < 0.9); month; production version segment.
- S8 Brier decomposition of P_k (reliability, resolution, uncertainty) beside RAW's, so sharpness is read with calibration.

## 5. Minimum-data gate (non-negotiable 8), measured Tue 2026-11-17 19:48-19:52 CST, label-free
Live: scratch copy of the app export (sha256 `750c736e...`, data_version `6edbcef0c89d`, 2026-11-10..17), via
`reports/prereg_calibration_program/gate.py` (output sha256 `3629b223...`). Fit side: schemas and row counts only (no `y` read), plus the
LINE_CAL_LIVE capture audit.

| check | measured | bar | pass |
|---|---|---|---|
| G-fit rows | `model_rows_2023.parquet` 110,476 rows with `p_ge` arrays, `c`, `s`, `starter10` (all four stats); DraftKings t60 played paired legs 53,247 (2023) / 53,752 (2024) (LINE_CAL_LIVE section 5) | >= 2,000 legs per stat per fit (`MIN_FIT`) | yes |
| G-fit support | thresholds in [-3, +3] spreads exist on every leg by construction; whether the 2023-24 grid supports r up to +3 for low-mean fg3m legs is **not measured** (it needs the arrays read; label-free; harness check before freeze) | >= 500 legs per stat with a threshold at r >= +2 | **open, harness check** |
| G-live legs | DraftKings main legs with a line: 49 per game at t60, coverage 0.64 (CAL_EVERYWHERE, 10-20..23); 50.8 per game at t15 tonight | >= 30 legs/game | yes |
| G-live thresholds | DraftKings alternate over rungs 426 per game (pts 132, reb 135, ast 87, fg3m 72) on 54 legs per game; Kalshi rungs 209 per game on 53 legs per game (35 games with lines, 11-10..17) | S3/S4 populated | yes |
| G-power P2 | per bin about one leg per game per bin after nesting (thresholds of one leg are one outcome): with 49 legs per game the synthetic half-width at 200 games is about 0.010 (L 51) to 0.012 (L 31); P(pass | gap 0) 0.99-1.00; P(pass | gap 0.015) 0.66-0.88; P(false pass | gap 0.04) 0.00 | P(pass | gap 0) >= 0.8 | yes (pooled bins); per-stat bins are labels |
| G-look | 200 games with a DK t60 main leg from 2026-12-04 at coverage 0.64: **2027-01-22**; 8,000 legs at 49 per game reached with 200 games | before the backstop | yes |

**Gate: PASS for the pooled rule; G-fit support at r >= +2 for fg3m is measured by the harness before the freeze (if it fails, fg3m's band is
narrowed to the measured support by that count alone, before any outcome).**

## 6. Interactions with frozen tests
- **LINE_CAL_LIVE:** window after its look; no slate of 2026-10-30..D is read. At the main line itself LADDER and LINE_CALIBRATION give
  different numbers; until this rule reads, the main-line card keeps LINE_CALIBRATION (CAL_SERVING_CHECKS C5).
- **GAME_SCRIPTS_FORWARD / combo script rule (being frozen on agent/market-game-scripts tonight; window not read by me):** it scores joint
  events of the same legs. Before this rule is frozen, the main session checks its window; if they overlap, this rule's look is placed after
  that rule's look, or Devin accepts the overlap as a disclosed exposure (marginals here, joints there). Unknown tonight.
- **ALT_RUNG_CAL (frozen, T494):** unchanged; its map stays served inside its support until this rule reads.
- **FORWARD_PREREG_2026_27:** CRPS of the raw grid is published at its looks; S6 reads the same rows after this rule's freeze, no pass word
  shared. **UNDERS_FORWARD** (Kalshi NO ROI) reads prices and outcomes of Kalshi contracts; S3 reads the same outcomes for P_k honesty, not
  ROI; no pass word shared; S3 is a label.

## 7. Guards, kill criteria, attack list
- **G-mono:** share of thresholds changed by the running minimum <= 2% (else the map's form is wrong: verdict withheld, reported).
- **Planted future:** a threshold whose outcome is planted in the fit inputs must move the fit; a line received after tip must be refused.
- **Shuffled-label null:** the map refit on within-date shuffled 2023-24 outcomes (K 20) must not pass P1 on Stage 0 legs.
- Stop unscored if the map sha differs, a season other than 2026 enters the live legs, or 2025 enters the fit.
- **Attack before belief:** re-derivation; second seed; drop top five player-seasons; team clustering; knockout of zm (how much is the
  book's line, as T494 found); a market-free arm (zm removed) reported beside.

## 8. What a result changes
- **HONEST_LADDER:** proposal to Devin: the combo builder, Kalshi rung chances and Analyst curves may show LADDER inside the +-3 band;
  nothing outside it. Not an edge; the main line M is an input, so on Kalshi rungs the number is partly the book's.
- **BETTER_NOT_HONEST:** ladders shown only in the passing bins; the failing bin shows nothing. **NOT_FIXED:** ladders stay main-line only.
  One follow-up at most (a tail term), new id; then closed.

## 9. Playbook check (docs/MODEL_PLAYBOOK.md; classes: quantile and distribution layers; count models for the integer support)
1. **Information or capacity?** The book main line is information (T494: 92% of the rung gain); the rest is a shape correction. No new
   pre-tip information beyond the line.
2. **Enough data for the class?** 7 parameters per stat on about 53,000 legs per season, each with many thresholds: ample. Per-slice
   calibration volume (the class's driver) is enough for pooled bins, thin per stat in the tails (labels).
3. **Valid test?** Integer support for every arm (P(Y >= k) exact from the integer pmf; the map acts on those numbers); live legs only for
   the verdict; nested thresholds clustered by game; known-at pairing; DNP voided. Weak points: Stage 0 is exposed; the t60 DK rate is
   borrowed from October; the fit uses `starter10` while serving needs CAL_SERVING_CHECKS C3 first.
4. **Lineage.** T339 (temperature/Platt on the grid made CRPS slightly worse; S6 guards it), T491 (raw ladder overconfident 80-90%),
   T494 (rung map; main-line input carries the gain; support raw >= 0.5 and r <= 0), T495/T496 (tail overconfidence, NOT_FIXED),
   COMBO_LINE_CAL (draft, gate failed for combos; not touched here), combo mode 2026-11-09 (banded LINE_CAL; PIT sd 0.76-0.98).
5. **What it scores against.** Threshold-averaged Brier (band-limited RPS) and bin reliability of P(Y >= k), against RAW; LINE_CAL-at-
   every-k and the rung map as comparators; prices are not scored.

## 10. Devin's choices (my recommended default first)
1. **Anchor on the book main line (zm) or market-free:** *default anchored* (T494: honest ladders came from the line). Cost: no ladder where
   DraftKings posts no main line (33% of Kalshi markets on 10-20..23). Alternative: market-free (drop zm); covers every leg, likely less honest.
2. **Band:** *default +-3 spreads*; alternative +-2 (safer tails, fewer rungs shown).
3. **Main-line number on the card after a pass:** *default keep LINE_CALIBRATION at the main line* and use LADDER elsewhere, with the
   kink disclosed; alternative: LADDER everywhere for one coherent curve.
4. **Display lock for this window:** *default yes until the look* (about 2027-01-22).

## 11. Priors (drafted for PREDICTIONS_LEDGER, not appended)
P(Stage 0 kills) 0.08; P(P1 passes live) 0.75; P(all five P2 bins pass) 0.35; P(the [0.8, 0.97] bin is the one that fails, given any
fails) 0.55; expected P1 -0.006 (80% interval -0.012 to -0.001).

## 12. Commands
Gate (label-free): see CAL_SERVING_CHECKS section 8. Stage 0 and the served fit: harness not written; it will be a maintainer command under
`data/ops/heavy.lock` (Mac CPU, a few minutes, numpy on two parquet files), written after the freeze review.

=== RESULTS BELOW ===

(none)
