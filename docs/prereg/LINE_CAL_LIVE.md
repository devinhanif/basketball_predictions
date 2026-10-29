# LINE_CAL_LIVE: does the frozen LINE_CALIBRATION map make production's P(over) honest at Pinnacle's line on the 2026-27 live log? (pre-registration)

Status: **DRAFT, revision 2, written by the modeler Thu 2026-10-29 12:46 CDT (clock read by `date`; revision 0 08:30, revision 1 12:32). Revision 1
applied Devin's settled choices (section 11) and the adversary pre-freeze review (docs/reviews/LINE_CAL_LIVE_prefreeze_2026-10-29.md:
B1-B3, M1-M4, m1-m9); revision 2 applies its re-review (N1-N5). Not frozen. No map fitted; no outcome-scored number exists for the candidate on any 2026 row.**
- **How it becomes the rule:** the main session commits `prereg(LINE_CAL_LIVE): <sha256>` of everything above the line
  `=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CAL_LIVE.md | shasum -a 256`), with the status
  line changed to FROZEN in the same commit. Before that: Devin reads it, the open choices (section 11) are settled, the harness is written
  and an adversary pre-freeze review returns READY (a hard stop, BEST_PRACTICES 2026-10-12).
- **Origin:** Devin, 2026-10-29 08:17 ("lets do recommendation number 4"; DECISIONS row). **Follows advice.** The DECISIONS row named this
  outcome in advance: "or LINE_CALIBRATION already covers it (then the question becomes wiring + a live check)". Step 0 found exactly that,
  so this file is the live check, not the 2023-fit / 2024-report recalibration the request sketched. Section 1 says why.
- **Season labels** are `games.season`. This rule reads only season 2026 (2026-27) live rows, plus the 2023-24 Pinnacle legs the frozen map
  was fit on (for the C0 reference row). Season 2025 is never loaded.

## 0. Step 0: what already exists, and what was already tested
| object | what it calibrates | lines, seasons | wired where | evidence |
|---|---|---|---|---|
| **LINE_CALIBRATION** (frozen a5441242, commit 06d76e3; `nba/parlay/line_cal.py`, `line_cal_build.py`, map `line_cal_map_v1.json`, payload sha 443743a1) | production's integer-grid P(over) at a book's x.5 MAIN line. Terms `[1, x, z, bench, bench*x]`: x = logit raw P, z = line vs continuous median in spread units, bench = `starter10` < 0.5 in the fit (exponentially weighted starter share over played games, half-life 10), but **served (and scored here) with the stored `starter_rate`** (plain starter share over the previous 150 box rows including DNP rows, NULL set to 0). The two disagree on 12.80% of the map's own 72,674 Pinnacle fit legs (6,413 fit-starters served as bench, 2,892 the reverse, so the served bench group is about 23% larger), mean absolute CAL change 0.0018 (0.014 on the flipped legs, max 0.196). One ridge logistic per (book, stat), books Pinnacle and DraftKings. Nothing from a price enters a fit | T-60 lines of 2023-24, walk-forward by month for the test; the served static map is fit on all 2023-24 legs (last row 2025-06-22), weights 0.5^(age/365) | parlay top-picks panel (`top_source`, `top_parlays`, legs carry `p_cal`); app export `line_cal` and the card (DraftKings cell at the DK main line, display only). **Not** in the scorecard, **not** in JARVIS, **not** in the briefing | **T493 CALIBRATED** (select 2024): Pinnacle log loss CAL - raw -0.0117 [-0.0139, -0.0096], n 37,528 legs / 1,260 games; CAL - base rate -0.0037; CAL 0.6882 vs coin 0.6931 vs Pinnacle 0.6865. Attack SURVIVES; fg3m carries 54% of the gain over the base rate, pts almost none (-0.0007). Stage 2 (2025) never run; Claude recommended the live log instead. **No forward check has ever been run** |
| ALT_RUNG_CAL (T494; `rung_cal*`) | P(over) at DraftKings ALTERNATE rungs, raw P >= 0.5 only | DK 2023-24 | export `rung_cal`, card | RUNG_REFIT_FIXES, WOUNDED: 92% of the gain is the book's main line. Not this question (main lines only here) |
| PARLAY_JOINT_CAL (T495) | the JOINT of parlay legs, using LINE_CALIBRATION marginals | DK primary, 2023-24 | panel joint | "T1 CALIBRATED in all 4 DK cells" means the joint of CAL legs is honest for random tickets; WOUNDED: mostly a confidence correction, and the panel's top picks still run 3-5 pts high (selection). It checks the same map, not a new one |
| TAIL_LEG_CAL (T496) | CAL legs with P >= 0.65 | DK 2023-24 | none | NOT_FIXED, closed; tail flag only |
| CALIBRATION_EVERYWHERE plan (branch agent/market-calibration-everywhere 2813fac, not merged) | inventory of every raw-P consumer; proposed `CAL_FORWARD_2026_27` (cell A = this map at DK main lines, live) | n/a | nothing; "awaiting Devin's go", the forward rule was never drafted | label-free scope counts only |
| Bayesian-Kelly shrink (branch agent/market-bayes-kelly dce13b8, not merged) | nothing about the model: shrinks a caller's p toward the Kalshi price (prior_edge_sd 3c) | n/a | not wired | none |
| JARVIS Pinnacle blend (`nba/jarvis/weights.py`, `configs/jarvis.yaml` model_weights, frozen 2026-10-25) | fair = Pinnacle no-vig + w x (model - Pinnacle); w earned from CLV | live | live; **w = 0 for every stat** (`data/jarvis/model_weights.jsonl`, 10-26: n 0, hold), so the model does not move JARVIS's fair price today. JARVIS's model number is the RAW 19-point-grid `leg_marginal` x P(play) | none yet |
| MARKET_ANCHORED_PROPS (T517, frozen f7691bbf) | pools the Pinnacle price with production's raw P | Pinnacle t60 2023-24 | none | model word DETECTED_BELOW_INFO_FLOOR (-0.00033), market word NONE; WOUNDED; keep your money. Not a calibration of the model |

**Plain reading.** The live finding (production's chosen side hits about 54% while it states about 60%, worst on unders) is the T477
signature that LINE_CALIBRATION was built for: production's P(over) sits about 4 pp low at the line and is far too steep. On the 1,787
Pinnacle T-60 legs of 2026-10-20..28 (label-free, section 5): raw mean P(over) 0.413 against Pinnacle's de-vigged 0.485, and the model
leans under on 78% of legs (2023-24: 75-76%). The map already exists, passed its historical test on exactly this book and line type, and
is already shown to users on the card and the parlay panel, yet it has never been checked on a single live row. So:
- **A new map fit on 2023 and scored on 2024 would re-test T493** on the same seasons, the same legs (Pinnacle t60, 35,146 / 37,528) and the
  same object, with 2024 exposed four times (T477, the POVER_BIAS scratch, T493, T517). Its pass would be foregone and its evidence weak.
  Not drafted.
- **The open question is transport**: does the 2023-24 map still make the served number honest for this season's production, on the
  side that is broken (unders)? That is answerable only on the live log, and it is overdue, because the card already serves the map.
- **One more step-0 finding (descriptive, no outcome).** The scorecard's "model side" is mean vs line, not the model's probability. On 347
  of the 1,787 legs (19%) the mean is above the line while raw P(over) < 0.5. Those legs are counted as "over" with a stated P(over) below
  0.5, which is why the over side looks "near honest" (stated 0.519). This rule splits on the model's own probability; the scorecard's
  split is a bridge row only.

## 1. Hypothesis
- **H1 (transport).** On Pinnacle T-60 main x.5 lines of the 2026-27 live log, the frozen map's P(over) (CAL) scores better than raw
  production (RAW) on the same legs by at least the floor, and is calibrated on each side of the model's own lean.
- **Null.** The 2023-24 map does not transport to this season's production: no gain, or a gain that leaves one side (or a stat, or bench)
  mis-stated.
- **Not claimed either way:** an edge. T493 left CAL 0.0017 log loss behind Pinnacle; honest probabilities are not better ones.

## 2. The rule in ten lines
1. **Legs.** Every production (`props_context_residual`) prop row that `forward_scores` scored (`status = 'scored'`: settled and played;
   DNP rows are voided, as in the map's fit legs), season 2026, stats pts / reb / ast / fg3m, latest `made_at` per (game, stat, player),
   joined to Pinnacle's main line from its `t60` snapshot (newest t60 `received_at` strictly before tip; main point = the two-sided point
   whose raw prices are closest to even, `nba.forward.scorecard._main_point`), lead >= 55 min, x.5 lines only.
2. **Arms.** RAW = `top_source.p_over(pred, line)` clipped to [0.5/199, 1 - 0.5/199]. **CAL** = `CalMap.apply("pinnacle", stat, RAW,
   line, q50, (q75 - q25)/1.349, starter_rate)` with the frozen map (payload sha 443743a1, refused otherwise) and quartiles from the stored
   19-point `q_grid` (the served definition: export and parlay panel). No refit, ever, inside this rule. The bench input is the served
   `starter_rate`, not the fitted `starter10` (section 8.3): this rule tests the map as served.
3. **Side.** under-lean iff RAW < 0.5, else over-lean. Fixed by RAW, so both arms are scored on the same partition.
4. **Window.** Slate dates (ET) strictly after the ET calendar date of the freeze commit's committer time (read from git; the main
   session merges with `--no-ff`, because a rebase or squash moves the committer date). Pre-freeze rows (10-20 to that date) are excluded
   from every check (section 7) and reported once, at the look, as the EXPOSED secondary S10.
5. **One look.** The first slate date D on which the window holds >= 200 games with at least one leg AND >= 5,000 legs (projected
   2026-12-01, section 5), counting only dates before the run date. Backstop: if not reached by 2027-01-31, D = 2027-01-31. `run`
   reads the real ET clock only (no date override), requires the real date to be after D, and requires every game on every window date
   through D to be settled. **Settled:** a game that is final (`games.home_pts > 0 AND away_pts > 0`, or `player_game_stats` holds rows
   for it: an ingest outage is not a postponement) and has production
   `forward_predictions` rows has `forward_scores` rows (else pending: the run refuses and is retried later). A scheduled game with no final
   score 36 h after its tip is listed in the results as `unplayed_or_postponed` and does not block; before 36 h it is pending. A final game
   with no production prediction is listed as `unforecast` and does not block. The same rules apply to the backstop. A verdict word needs
   >= 150 games, else INSUFFICIENT_DATA. No interim look at any CAL outcome number.
6. **Primary metric.** Mean per-leg Brier at the line, CAL minus RAW, four stats pooled, legs weighted equally. 95% paired percentile
   bootstrap over games (B = 2,000, seed 0); a date-clustered CI is printed beside it (cross-game same-night moves); the game CI binds.
7. **Checks.** P1 Brier CAL - RAW <= **-0.0015** with CI upper < 0. P1b log loss CAL - RAW CI upper < 0. P2 under-lean: Brier CAL - RAW
   CI upper < 0, and |realised under rate - mean CAL P(under)| <= 0.03. P3 over-lean: Brier CAL - RAW CI lower <= 0 (no significant
   harm), and |realised over rate - mean CAL P(over)| <= 0.03. P4 pooled |mean CAL P(over) - realised over rate| <= 0.015. P5 no stat has
   CI lower > 0. P6 starter and bench point estimates each <= 0.
8. **Verdicts.** TRANSPORTS: P1-P6 all pass. PARTIAL: P1 and P1b pass, any of P2-P6 fails (names the failing slice). NOT_TRANSPORTED: P1
   or P1b fails. INSUFFICIENT_DATA: fewer than 150 games at the backstop.
9. **Multiplicity.** One primary family (P1); the verdict is an intersection of all checks (every one must pass), so no BH is applied to
   it. BH runs over the secondary families: per stat (m = 4), side x stat (m = 8).
10. **Outcome.** TRANSPORTS: a wiring proposal goes to Devin (section 9). Anything else: recorded, and section 10 says what follows. Nothing
   about this rule changes after the look.

## 3. Why these choices (what each protects against)
| choice | protects against |
|---|---|
| no fit; the frozen map as served | re-testing T493 on exposed seasons; a "calibration" tuned on the outcomes it is judged on |
| Pinnacle T-60, the map's own fit book and snapshot | judging a map on a line type it never saw (close lines move 1-2% of the time, and the scorecard uses them; bridge row only) |
| side by the model's own probability, fixed by RAW | the scorecard's mean-vs-line artifact (19% of legs); choosing the partition by arm |
| floor -0.0015 Brier | a gain too small to matter. It is the Brier value of the bias the map exists to remove (a 4 pp miss near p 0.45 costs about 0.04^2 = 0.0016 per leg) and the Brier twin of LINE_CALIBRATION's log-loss floor -0.003 (log loss is about twice Brier near 0.5) |
| P2 and P3 side checks | a pooled gain hiding a side that stays mis-stated: the side IS the finding |
| P4, the 0.015 calibration-in-the-large bar (LINE_CALIBRATION P4) | a gain from shrinking while the location bias stays |
| P5, P6 | a pooled gain hiding a stat or the bench (T477: bench off by 5.9 pp) |
| pre-freeze rows excluded | the raw side-split hit rates through 10-28 have been read (section 7) |
| one look, a fixed size, a backstop; real clock only; every window date settled | peeking; a test that never ends; a verdict forced early by a mistyped date; a result that depends on when it is run |
| harness, gate and served functions pinned to the freeze commit (section 13) | a wiring branch changing RAW or CAL before the look |
| DNP voided | scoring a played-only map on a mixture it was never fit on (the Kalshi DNP-as-miss case is a wiring question, D3) |
| game-clustered paired bootstrap, date CI beside it | row-level CIs that were anti-conservative (BEST_PRACTICES 3) |

## 4. Secondary results (labels only, no pass)
- **S1 vs the market.** CAL and RAW minus Pinnacle's two-way de-vigged T-60 price, same legs, Brier and log loss. Expected: CAL still worse
  than Pinnacle. A CAL win would be a surprise to attack, not a claim.
- **S2 base rate C0.** The frozen harness's C0 at the static map's block (per stat, the 0.5^(age/365)-weighted over rate of the 2023-24
  Pinnacle t60 legs, block start 2025-07-01). CAL - C0 says whether the map beats "say the old base rate" live.
- **S3 DraftKings cell.** The same contrasts for the DK cell at the DK t60 main line (the cell the card serves; CAL_FORWARD's cell A), with
  the DraftKings C0 and the DraftKings x band. Label only: no check, no verdict word; BH not shared with the primary.
- **S4 coin** (0.5) reference row.
- **S5 per stat** CAL - RAW (BH m = 4), and **side x stat** (BH m = 8).
- **S6 slices** (each CAL - RAW with game CI, n and games): starter / bench; minutes-uncertain = P(play) < 0.9 OR minutes sd >= 6 over the
  player's season-2026 games before the date (>= 4 games; else "unknown"), and each flag alone; RAW outside the map's x band (1st-99th
  percentile of the Pinnacle fit legs per stat); recency-fallback rows (`fallback_reason` set in the stored prediction; 74 of the 1,787
  pre-freeze legs, pts 26 / reb 20 / ast 13 / fg3m 15, a model the map never saw) and their complement; calendar month; production
  version segment (`forward_scores.version`).
- **S7 reliability** in ten equal-width bins for RAW and CAL, per side.
- **S8 descriptive recalibration fit** on the window (intercept and slope of logit outcome on logit RAW, and on logit CAL). It is a
  description of the residual, not a candidate: no fitted number is served or scored.
- **S9 bridge.** The scorecard's mean-vs-line side table for RAW and CAL, so the two views can be read together.
- **S10 pre-freeze rows, EXPOSED.** The same contrasts on 2026-10-20 through the freeze date, reported once at the look, marked EXPOSED,
  no check, no verdict word (section 11.1).

## 5. Minimum-data gate (non-negotiable 8), measured Thu 2026-10-29 08:29 CDT on the live data it will run on
- **Command:** `.venv-1/bin/python -W ignore -m research.eval.line_cal_live_gate --data-root /Users/devin/Downloads/nba-prediction --until 2026-10-28 --out reports/prereg_line_cal_live/gate_draft.json`
  (1.5 s, read-only; output sha256 1944ba51; worktree branch agent/modeler-props-recal off main 0ae8aac).
- **Data version:** `nba.duckdb` (no meta table; mtime 2026-10-29 08:20), `data/markets/market_asof.duckdb` (mtime 08:20), map payload
  443743a1, pairs 050dba3e / 560a3808 (unchanged from LINE_CALIBRATION's pins). Season 2026 only; 2023-24 pairs read for `p_model` only.
- **Label-free:** no `y`, `crps`, `brier` or `log_loss` read; box minutes only of games before each row's date. Power uses synthetic labels.

| check | measured (10-20..10-28) | bar | pass |
|---|---|---|---|
| scored production rows | 6,028 (68 games) | n/a | n/a |
| legs with a Pinnacle t60 main x.5 line and all inputs | 1,787 (57 games, 9 dates; 31.4 legs per game; 83.8% of scored games have >= 1 leg) | rate >= 20 legs/game | yes |
| why the rest drop | 4,241 no Pinnacle t60 line for that player (Pinnacle lists fewer players); 0 whole-number, 0 missing inputs | n/a | n/a |
| t60 lead to the real tip | median 62.0 min, min 55.3 | >= 55 | yes |
| forecast made at or before the t60 snapshot | 1,784 of 1,787; the other 3 legs are one player in one game (0022600126, ast/pts/reb), forecast at T-9.9 min after the line at T-63.8; 0 post-tip forecasts, 0 post-tip lines; `live_book_odds.tipoff` equals the scheduled tip on all 57 games (adversary A3). Kept: they are served | n/a | n/a |
| side split (RAW) | under 1,396 / over 391 (pts 459/185, reb 385/93, ast 240/54, fg3m 312/59) | both sides present | yes |
| starter / bench | 1,414 / 373 (bench under 319, over 54) | both present | yes |
| minutes-uncertain | P(play) < 0.9: 448; min sd >= 6: 28; sd unknown (< 4 season games): 1,557 (shrinks as the season grows); either: 470 | n/a | descriptive |
| RAW outside the map's fit x band | 92 (5.2%) | reported | yes |
| CAL with 19-point vs 199-point quartiles | max diff 0.00022 on 1,711 legs with a stored 199 grid | <= 0.001 | yes |
| production version in window | `ctxres-v1` only | reported | yes |
| projected look (200 games), counting from 10-29 at 83.8% of scheduled games | 2026-12-01 (150: 11-22; 300: 12-25; 400: 2027-01-10) | before the backstop | yes |

**Power at the look (200 games), synthetic truth `p_t = sigmoid((1 - lam) logit CAL + lam logit RAW)` on the real legs, 200 draws:**
| truth | expected Brier CAL - RAW | per-game sd | 95% half-width at 200 games | P(pass P1) | under-lean P(CI upper < 0) | over-lean P(CI upper < 0) |
|---|---|---|---|---|---|---|
| lam 0 (map transports exactly) | -0.0085 | 0.0163 | 0.0023 | 1.00 | 1.00 | 0.67 |
| lam 0.25 (three quarters transports) | -0.0040 | 0.0157 | 0.0022 | 0.95 | 0.91 | 0.28 |
| lam 0.4 (about one fifth transports) | -0.0010 | 0.0154 | 0.0021 | 0.15 | 0.14 | 0.05 |

**Minimum detectable effect:** at 200 games the P1 standard error is about 0.00112, so the MDE at 80% power is about **-0.0031 Brier**
(CI upper < 0 binds; the -0.0015 floor binds only for effects smaller than about -0.0024). That is 37% of the exact-transport effect
(-0.0085): transport is detectable down to about lam 0.3 on this synthetic family.
Calibration-gap sd per game on under-lean legs: 0.098, so the P2 gap's standard error at 200 games is about 0.007 against the 0.03 bar (a
true gap of exactly 0.03 fails half the time).
The over-lean slice is under-powered for a gain, which is why P3 asks only for no significant harm. **Gate: PASS.**

**Capture audit (before any gate could be declared failed; it passed, recorded anyway).** Prop lines held: 2023 and 2024 (odds_history t60:
Pinnacle 35,630 / 38,333 raw legs, DraftKings 54,013 / 54,716, FanDuel 45,290 / 47,114; paired played legs Pinnacle 35,146 / 37,528,
1,249 / 1,260 games, under-lean 76% / 75%; DraftKings 53,247 / 53,752; FanDuel 44,682 / 46,378), 2025 (holdout, never used), and the
2026 live capture (this rule). None before 2023: `odds_history` 2022 rows are game lines only (0 player markets) and `data/history/` holds
no prop lines. Nothing repairable changes this rule's power: it is set by the live season's pace.

## 6. Pre-declared diagnostics (descriptive; game-clustered CIs; no pass word)
- **D0 serving bridge (optional, label-free, before the look only).** Replay at least 10 dates of 2024 through the live path on a copy of
  the database (`nba.daily.replay_season`, seasons <= 2024), and compare RAW, q50, spread, the bench flag and CAL with the fit-path values
  on the same Pinnacle legs. No outcome is read. Pre-declared reading: a median absolute CAL difference <= 0.005 says the serving path
  matches the fit path, and a NOT_TRANSPORTED is then read as season drift; otherwise it is read as a serving mismatch. The tool is not
  written; if D0 is not run before the look, NOT_TRANSPORTED is not attributed to either cause. The bench part is already measured (12.80%
  of the 2023-24 Pinnacle legs flip, mean absolute CAL change 0.0018; section 0). An outcome score of CAL on 2024 is NOT a positive control:
  the served map was fit on every 2023-24 leg, so it would pass by construction; T493's walk-forward is the historical control.
- **D1 minutes realisation on under-lean legs.** Groups by actual minus projected minutes: short (<= -5), near (-5, +5), long (>= +5), no
  projection. Per group, for RAW and CAL: n, mean stated P(under), realised under rate, gap, and the group's share of the side's total excess
  sum(stated - hit). Question: is the under overconfidence carried by players who played more than projected (a minutes miss), or by
  players who hit their minutes (a rate or location miss)?
- **D2 error decomposition on under-lean legs.** The scorecard's split of actual minus forecast into minutes miss and rate miss (they sum
  exactly), means and shares.
- **D3 P(play).** Forecasts are conditional on playing and DNP legs are voided, so P(play) cannot create overconfidence on scored legs. D3
  counts the voided DNP legs that had a Pinnacle t60 line, by RAW side, because the Kalshi use (DNP settles as a miss in JARVIS) serves
  P(play) x P(over).
- **D4 location or slope.** RAW calibration in the large (mean P(over) minus realised over rate) and the S8 slope: whether the live
  overconfidence is T477's location bias, a slope, or both.

## 7. Data exposure (honest)
- **Seen before this draft:** the scorecards of 10-20..10-27 and the main session's 10-28 recomputation: RAW's hit rates at Pinnacle's
  close line on the mean-vs-line side (all 0.534, under 0.558, over 0.500; n 1,975 / 62 games); T477, T493, T517 on 2023-24.
- **Computed by this draft (label-free):** CAL's stated probabilities on the 1,787 pre-freeze legs: stated P(chosen side) RAW 0.620 (under
  0.632, over 0.575) to CAL 0.545 (under 0.551, over 0.526); mean P(over) RAW 0.413, CAL 0.466, Pinnacle 0.485; CAL flips the side on 7.7%.
  With the scorecard's under hit rate near 0.56, a reader can guess that CAL's under gap on the pre-freeze rows is small. That is why those
  rows are excluded from every check.
- **Seen at the review (label-free):** the ast cell sits further from Pinnacle live than in the fit (RAW minus Pinnacle mean P(over) -0.085
  on 294 live legs against -0.047 on the fit legs; the other stats within 0.016). If a stat fails P5, ast is the likeliest. A prior only.
- **Not seen:** any CAL (or C0) outcome score on any 2026 leg. No map fitted.

## 8. Lineage and playbook check (docs/MODEL_PLAYBOOK.md; class: quantile and distribution layers, recalibration at the line)
1. **Information or capacity?** Neither: no new pre-tip information and no new capacity. A correction of how production's information
   is stated at the line, already built. The class's named failure ("our P(over) 4 pp low at the line") is the target.
2. **Enough data for the class?** The map is 5 parameters per cell fit on 12,984-25,210 Pinnacle legs; nothing is fit here. The live test
   needs about 200 games for P1 at a three-quarters transport (power 0.95).
3. **Valid test?** Real tips (live `tipoff` equals the scheduled tip on all 57 gate games; line lead >= 55 min, min 55.3), played legs
   only, as-of inputs (0 post-tip forecasts and lines; 3 legs of one player have a forecast made after the t60 line, at T-9.9 min, and
   are kept because they are served), probabilities at x.5 lines exact on the integer grid, both arms on the same legs and partition,
   fixed look, power measured on the real legs. The comparator is what production serves. **It tests the map as served, not as fitted.**
   Known serving deviations: (a) the bench term is served on `starter_rate`, not the fitted `starter10` (12.80% of fit legs flip; served
   bench group about 23% larger). P6 and the S6 bench slice are therefore defined on a bench group the map's bench coefficients were not
   fit on; a bench failure means the served bench probabilities are not honest, not that the fitted coefficients are wrong. (b) 4.1% of
   live legs are recency-fallback rows (S6). (c) Production refits daily on all rows before the slate, while the fit rows came from
   monthly-block OOF refits; the roster source differs. **Code drift:** none found in the conditional prop path between the OOF build
   (`model_rows_*` at a2e7609, 2026-10-10) and ec98d17 (adversary A6: `max_season` plumbing, P(play) Platt off, grids persisted; replay
   oracle 89889f6d unchanged since 10-11).
4. **Lineage.** CALIBRATED_GRID (T339, isotonic/Platt/temperature on DK, 60-day window; closed, its shuffled control beat every map) ->
   LINE_CALIBRATION (T493, the one allowed follow-up; CALIBRATED; Stage 2 not run) -> this forward check (T493's own recommended
   confirmation). Guards carried: map sha refusal, holdout refusal, game-clustered paired bootstrap with seed 0, B 2,000, per-stat BH, P4,
   P5, P6 from T493, starter / bench, x-band. Dropped: the shuffled-label null and planted-future probe (nothing is fit, so there is no
   fit to leak into or to shrink by chance), Stage 2 (2025 is not used). Added: the side checks, the date CI, the minutes diagnostics.
5. **What it scores against.** Brier at the market line (Devin's ask), log loss as agreement, the Pinnacle price as a reference row. CRPS is
   not claimed.

## 9. What a result changes
- **TRANSPORTS.** A separate wiring branch (market or steward agent; not this one), through the CALIBRATION_EVERYWHERE accessor
  (`nba/parlay/calibrated.py`, plan section 5): (a) the scorecard gains CAL columns beside RAW; (b) the card shows CAL at Pinnacle's line
  where that is the line shown (DK cell unchanged); (c) JARVIS shows `P(play) x CAL` as its model number only where the Kalshi line
  N - 0.5 equals the book's main line (in scope), raw labelled "uncalibrated" elsewhere. JARVIS's frozen weight rule (`configs/jarvis.yaml`
  model_weights, 2026-10-25) keeps reading RAW; switching its "leaning" filter to CAL is a new rule version and Devin's decision. Replay
  oracle IDENTICAL on every frozen surface, adversary review, Devin's go. No stake language changes: honest is not an edge.
- **PARTIAL.** The named slice is recorded. If it is a side (P2 or P3), one follow-up is allowed under a new id (LINE_CAL_SIDE): the same
  logistic plus a side term (`1[x < 0]` and its interaction with x; Platt-family, two parameters, not isotonic: isotonic cannot use role or
  z and already lost in CALIBRATED_GRID), fit on 2023-24 Pinnacle legs, tested forward on the next 200 games. No other follow-up.
- **NOT_TRANSPORTED.** This means **the served number is not honest**, not "season drift": the test cannot tell drift from the serving
  deviations of section 8.3 unless D0 was run (section 6). The card and the parlay panel are serving a map that does not hold: the proposal to Devin is to label
  them "historical calibration, not confirmed live" or to show Pinnacle's price instead, and a refit question under a new id on 2026 live
  legs only (walk-forward, no 2025), drafted no earlier than 400 games.
- **INSUFFICIENT_DATA.** Recorded; no wiring.

## 10. Kill criteria and what reverses it
- Stop unscored if: the map sha differs; more than 5% of legs with a Pinnacle t60 two-sided main line drop for lead < 55 min (0% at the
  gate); any season other than 2026 enters; the executing code of any pinned file (section 13) differs from the freeze commit. A production prop-model version change inside the window does not stop the look: legs are scored as served
  (the question is the served number) and S6 reports each version segment.
- Reading: a NOT_TRANSPORTED, or a bench-slice (P6) failure, is read as "the served map does not hold". This test cannot separate season
  drift from the serving deviation of the bench input (section 8.3), except through D0.
- A TRANSPORTS verdict is reversed by the post-result attack (re-derivation, a second seed, dropping the top five player-seasons, team
  clustering) or by a production change to the grid (the map must then be re-checked on legs scored by the new grid).

## 11. Choices (settled: Devin, 2026-10-29 12:05, "defaults"; DECISIONS row of that time)
1. Pre-freeze rows 10-20..freeze: **excluded from every check; reported once, at the look, as a labelled secondary marked EXPOSED with no
   pass word** (as recorded in the DECISIONS row).
2. Quartile source: **the served 19-point q_grid** (export and panel; 0.00022 max difference from the 199-point grid).
3. Line snapshot: **Pinnacle t60** (the map's own).
4. Look size: **200 games and 5,000 legs, about 2026-12-01**; backstop 2027-01-31; fewer than 150 games is INSUFFICIENT_DATA.
5. Primary metric: **Brier, with log loss as agreement**.
6. DraftKings cell: **secondary only** (no BH shared with P1).
7. JARVIS weight rule: **display only; the weight rule keeps reading RAW**.

## 12. Unstated details fixed before any scored number
- Clipping: model probabilities to [0.5/199, 1 - 0.5/199] for both scores; market to [1e-9, 1 - 1e-9].
- Outcome: over iff `y > line` (x.5 lines, no pushes). Under rate is 1 - over rate on the under-lean legs.
- Bootstrap: one multinomial game-resample matrix (seed 0, B 2,000) shared by every statistic; date clusters resampled the same way
  (seed 1). One-sided p for BH = (1 + #{boot of RAW - CAL <= 0}) / 2,001.
- Starter: the served `starter_rate >= 0.5` (not the fitted `starter10`; section 8.3). Fallback rows are kept (they are served). Minutes sd: sample sd (ddof 1) of minutes > 0 in season-2026 games strictly before the date.
- Duplicate legs: one per (game, player, stat). A player traded mid-window keeps every leg.
- Month: calendar month of the ET slate date.

## 13. Commands and pins (maintainer; light, numpy only, no heavy.lock needed)
- **Pinned to the freeze commit:** the harness `research/eval/line_cal_live_eval.py`, the gate `research/eval/line_cal_live_gate.py`, and
  the served functions `nba/parlay/line_cal.py`, `nba/parlay/line_cal_map_v1.json`, `nba/parlay/top_source.py` (`p_over`, `quartiles`)
  and `nba/forward/scorecard.py` (`_main_point`). Tests: `tests/eval/test_line_cal_live_eval.py`, `tests/eval/test_line_cal_live_gate.py`.
- **How the look runs:** from a checkout of the freeze commit (`git worktree add <dir> <freeze commit>`), with `--data-root` the main
  checkout, so a later edit on main to a pinned file cannot change the look's computation and does not stop it. `run` compares the
  bytes of every module it actually executes (the harness, the gate, `line_cal`, the map file, `top_source`, `scorecard`) with
  `git show <freeze commit>:<path>`, refuses on any mismatch, and records those sha256s. The results list every commit on main since
  the freeze that touches a pinned file; if one changes `p_over`, `quartiles`, `_main_point` or `CalMap.apply`, the served number differs
  from the scored one, and the results say so (a serving difference, descriptive; not a stop and not a new id). The one result is
  written to `reports/prereg_line_cal_live/results.json` in both the freeze checkout and the main checkout; `run` refuses if either
  exists.
- **Holdout log first:** before `run`, the main session commits a HOLDOUT_ACCESS_LOG row (rule sha, the one look, season-2026 outcomes
  read once), whether or not the season-2026 holdout rollover (at 20 games) has been done.
```
cd /Users/devin/Downloads/nba-prediction
M="-m research.eval.line_cal_live_eval"
.venv-1/bin/python -W ignore $M status --sha <64 hex>     # counts only: games, legs, look date (no outcome)
.venv-1/bin/python -W ignore $M dryrun                    # synthetic outcomes end to end on the real legs (no outcome read)
git worktree add ../lcl_look <freeze commit>               # the look runs from the frozen code
(cd ../lcl_look && /Users/devin/Downloads/nba-prediction/.venv-1/bin/python -W ignore $M run --sha <64 hex> --data-root /Users/devin/Downloads/nba-prediction)
                                                          # the one look; real clock only; refuses before the look or with pending games
.venv-1/bin/python -W ignore -m research.eval.line_cal_live_gate --data-root . --until <date> --out reports/prereg_line_cal_live/gate_<date>.json
```

=== RESULTS BELOW ===
