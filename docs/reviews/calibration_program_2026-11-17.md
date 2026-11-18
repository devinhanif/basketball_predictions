# Calibration program, 2026-11-17: audit against forecasting best practice, four draft rules, and what calibration cannot do

Modeler, branch `agent/modeler-calibration-program` (off main `127de40d`). Written Tue 2026-11-17 19:38-19:58 CST (worktree created 19:38; first commit 19:58, both read by `date`).
Asked by Devin today: "do everything in forecasting best practices to calibrate the model", and earlier "make sure the model is calibrated
always". This is **advice work** (it follows the retrain survey part 3, the decisions review row 7 and the CALIBRATION_EVERYWHERE plan). It
drafts rules; it freezes nothing, fits nothing and reads no outcome.

**What I read:** CLAUDE.md; MODEL_PLAYBOOK; LINE_CALIBRATION, LINE_CAL_LIVE (section 0 inventory), COMBO_LINE_CAL, WINNER_RECAL,
PPLAY_INTO_PROPS_LIVE, FORWARD_PREREG section 7; TEST_LEDGER T339, T477, T491, T493-T496, T517, T524-T525; `nba/parlay/line_cal.py`,
`nba/export/line_cal_fields.py`, `rung_cal_fields.py`, `reader.pair_quotes`; DESIGN_EXPORT_LINE_PAIRING; the CALIBRATION_EVERYWHERE plan
(2813fac); tonight's retrain survey (agent/modeler-retrain-nov 4b825848) and decisions review (agent/archivist-decisions-review 2cecabac);
the 11-10 app calibrated-only and cards-unders reviews; DECISIONS 2026-10-29 12:29, 11-09 23:02, 11-17 19:08 and 19:26.

**What I measured (label-free):** a scratch copy of `data/export/latest/export.duckdb` (sha256 `750c736e...`, data_version
`6edbcef0c89d`, export 2026-11-17, slates 11-10..17, 56 games, season 2026 only), opened read-only, and the schedule parquet. Script
`reports/prereg_calibration_program/gate.py` refuses any SQL naming `box_scores`, `settled_scores`, `home_pts` or `away_pts`, refuses a path
under the main checkout's `data/`, and asserts seasons = 2026. Output `reports/prereg_calibration_program/gate_draft.json` (sha256
`3629b223...`), 6 s, reproduced byte-identical on a second run (seeded; row order fixed). No DuckDB under the main checkout was opened;
`nba.duckdb` was not touched. No season-2025 row was read. No forecast was joined to an outcome; no reliability number on any 2026 slate was
computed or read. Power figures use synthetic labels drawn from real stated probabilities.

## 0. Revision 1 (same evening): two-stage design, Devin's decision
Devin decided the drafts should be two-stage where history allows: a walk-forward backtest on 2023-24 first (selection, not a claim), then
confirmation only of what survives. LADDER_CAL and PPLAY_CAL_LIVE are now revision 1 with a stage 1; CAL_BY_TIME gains section 11 (what a
backtest by slot needs, where it flatters, a per-slot recommendation); CAL_SERVING_CHECKS is unchanged (live pass/fail checks). Sections 3.2-3.4
below describe the stage-2 forward looks; the table here supersedes their timing. Dates for stage 1 are work-queue estimates (harness,
adversary pre-freeze read, freeze, one run), not measured.

| draft | stage 1 (backtest; selection) | stage-1 gate | stage 2 (confirmation) | time to first answer | time to the confirming answer |
|---|---|---|---|---|---|
| CAL_SERVING_CHECKS | n/a (live checks) | PASS (measurable now) | n/a | **now**: tonight's scan already says SERVING_GAPS (C1 0.266, C2 5.6% in window, C3 not stored) | about 1-2 weeks after the fixes land (7 slates) |
| CAL_BY_TIME | morning via m360 and t15 via t5 lines, 2024, LINE_CALIBRATION walk-forward block maps; t60 already has T493; t30 forward-only | **BLOCKED** until the held m360/t5 legs are counted (command in its section 11); bar 600 games / 15,000 legs per slot | forward look per slot after LINE_CAL_LIVE's look | about **2026-12-01** (if the m360 rows are ingested and the harness is written) | about **2027-01-08** (t15), 01-12 (t60), **02-01** (morning) |
| LADDER_CAL | walk-forward on 2024 (fit 2023 + earlier 2024 months), DK t60 main lines and all integer thresholds; T494 arrays as comparator | **PASS**: 53,247 fit legs (2023), 53,752 report legs on about 1,300 games; P1 half-width about 0.0007 (MDE 0.0010 < floor 0.0015); P2 half-width about 0.0043 vs +-0.03; one open harness count (fg3m r >= +2) | 2a: one logged 2025 touch (default yes; never-evaluated model); 2b: live look | about **2026-12-01 to 12-05** (stage 1, 10-20 min on the Mac) | about **mid-December** (2a), **2027-01-22** (2b) |
| PPLAY_CAL_LIVE | select raw R vs walk-forward Platt of R on 2023, report 2024 | counts PASS (33,093 / about 33,000 rows; smallest-bin half-width about 0.016 vs +-0.05); **BLOCKED** on the missing per-row array for the live arm (2022-start R; `scores.npz` holds R19 and P0 only) | live look after PPLAY_INTO_PROPS_LIVE's season-end looks; **no 2025 touch** (R spent its touch; a Platt of R is not a never-evaluated model in substance) | about **early December** (once R22 rows are located or regenerated) | about **2027-04-13** (02-26 if Devin allows an earlier look) |

## 1. Bottom line
1. **The calibrated chance the app shows is mostly computed at a line time the map never saw, and reaches about a quarter of the props
   that could have one.** Of 1,877 props with a DraftKings main line known while the forecast was valid (11-10..17), 500 got a calibrated
   value (0.266; 0 of 155 on 11-16). Of those 500, 68.6% use a line read two or more hours before tip; 5.6% sit in the T-60 window the map
   was fitted on. The bench input is the wrong definition (12.80% of fit legs flip). These are serving bugs, fixable without a new fit:
   **CAL_SERVING_CHECKS**.
2. **Whether the map holds at other line times is untested and testable**, after LINE_CAL_LIVE's look, by about 2027-02-01:
   **CAL_BY_TIME**.
3. **Off the main line there is no honest ladder**, and the combo screen rightly shows the main line only. A threshold-wise map fitted on
   every integer threshold of 2023-24 can be tested live by about 2027-01-22: **LADDER_CAL**.
4. **Of win probability, P(play) and the under side, only P(play) passes its gate this season** (look at season end, so it does not peek at
   PPLAY_INTO_PROPS_LIVE): **PPLAY_CAL_LIVE**. Win probability fails on power (an honesty claim needs about 1,100+ post-freeze games; 939
   remain). The under side is LINE_CAL_LIVE's own P2 check; a new rule would be a second look.
5. **Calibration adds no information.** It makes a stated chance honest; it does not make it better than the price (T493: CAL 0.6882 vs
   Pinnacle 0.6865; T517: the model adds about a third of an info floor beyond the market). Section 5.

## 2. Audit: the checklist a careful forecaster would hold this system to
Status words: DONE, PARTIAL, MISSING, BLOCKED (by a frozen test until its look, or by data).

| # | Best-practice item | What the project does today (evidence) | Status | Draft that closes it |
|---|---|---|---|---|
| 1 | Reliability by probability bin, out of sample, n and clustered CI per cell | Main line: T493 walk-forward on 2024 (ten-bin reliability, calibration in the large by role, game-clustered, n 37,528 Pinnacle legs); app tab copies it exactly (app review 11-10 check 4). Live: LINE_CAL_LIVE S7 (ten bins per side) at its one look | DONE historically; **BLOCKED live** until LINE_CAL_LIVE's look (about 12-03 to 12-12) | none needed beyond the look |
| 1b | ... by slice: stat, side, starter/bench, home/away, line distance from the median, book | LINE_CAL_LIVE: stat, side, starter/bench, x band, month, fallback, version. **Not anywhere:** home/away, z (line distance) bins, books other than DK and Pinnacle | PARTIAL; live part BLOCKED | CAL_BY_TIME S1-S7 (stat, side, role, line move, x band); home/away and z bins: not drafted (loose end) |
| 2 | Calibration at every forecast time served (morning, T-60, T-30, T-15, late report) | One T-60 map, applied once per prop and copied to all snapshots (`line_cal_fields`). Served values tonight: line lead median 358 min; 68.6% >= 120 min; 5.6% in [45, 90). No arm other than production has a map (`_t15`, `_late`, AVG) | **MISSING** | CAL_BY_TIME (morning, t60, t15; t30 gate not measured) |
| 3a | Whole-distribution calibration: PIT histogram and dispersion, coverage at the 19 stored quantiles | FORWARD_PREREG settle stores PIT bounds, q10/q90 hits, pinball19 per row (`forward_scores_elig`); published at its looks. Combo latent PIT sd 0.63 -> 0.76-0.98 (not a validated distribution). COMBO_STOCKS_SHADOW G1 tails. No PIT-dispersion or 19-level coverage check with a pass word | PARTIAL | LADDER_CAL S6 (PIT sd, 19-level coverage, RPS no-harm) |
| 3b | Off-main-line probabilities (alternate rungs, Kalshi N+, combo ladders) | Raw ladder overconfident at 80-90% (T491); LINE_CAL at rungs worse than raw +0.044 (T494); rung map only raw >= 0.5 and at/below main (T494, WOUNDED); combo v1 main line only (DECISIONS 11-17 19:08: 56% for 18+ vs 44% for 32+) | **MISSING** off the rung map's support | LADDER_CAL |
| 3c | Integer-support handling | Integer grid for every arm in scoring (INTEGER_QUANTILES, T114-T118); `p_ge_full` exact at x.5 lines; whole-number lines refused | DONE | carried in every draft |
| 4 | Calibrator choice, minimum sample, overfitting control | 5-term ridge logistic (ridge 1.0, MIN_FIT 2,000 legs, 365-day half-life), walk-forward by month block (never by row), shuffled-label null and base-rate C0 (T493). Isotonic, Platt and temperature lost to their shuffled control (T339). Beta calibration and per-slot terms never tried. Tail term unstable across seasons (T496 NOT_FIXED) | DONE for the main line; PARTIAL for tails | LADDER_CAL (7-term form fitted on all thresholds, shuffled null K 20) |
| 5 | Recalibration cadence and drift | Static map fit on 2023-24, "refit only after Devin logs a 2025 training-use row"; LINE_CAL_LIVE allows a refit question only under a new id, no earlier than 400 games. No drift monitor exists | **MISSING** (and BLOCKED for the main-line map until the look) | Not drafted (four-draft limit). Recommendation in section 4.5 |
| 6 | Sharpness beside calibration (Brier decomposition, CRPS) | T493 reports CAL vs coin, base rate C0 and the market (a resolution check); FORWARD_PREREG publishes CRPS. The reliability / resolution / uncertainty decomposition is never reported | PARTIAL | all three scored drafts report the decomposition (CAL_BY_TIME S9, LADDER_CAL S8, PPLAY_CAL_LIVE S7) |
| 7a | Win probability | No post-hoc map; backtest ECE 0.024 and no calibrator kept (T178); FORWARD_PREREG section 7 publishes Brier and ECE descriptively; WINNER_RECAL closed (FAIL_POWER) | **BLOCKED by data** this season (gate below) | none: fails its gate |
| 7b | Chance of playing | Shown for every player and multiplied into card prices; Platt map off since 10-11; R vs production frozen live test is CRPS, not reliability; ECE ~0.01 vs ~0.14 contradiction in the repo (retrain survey row 8) | **MISSING** | PPLAY_CAL_LIVE |
| 7c | Combos and stocks at book lines | COMBO_LINE_CAL draft fails its own gate (no main-line combo history; blk/bs no data) | BLOCKED by data | none (its draft and repairs stand) |
| 7d | Halftime overs, in-game | T508 arm M plus a Platt fit on 2019-24 (backtest only); HALFTIME_OVERS_VS_PRICE is a draft; Devin shows the live chance labelled "experimental, not yet tested" (DECISIONS 11-17 19:08) | MISSING live | **not drafted here** (agent/modeler-live-model owns it); plug-in point in section 4.6 |
| 7e | Joint of parlay legs | T495: with CAL legs the DK joint is calibrated for random tickets, WOUNDED (mostly a confidence correction); copula same-player lift kept (T525) | DONE historically (random tickets); no live check | none drafted |
| 7f | The under side | Live finding: chosen side hits about 54% against a stated 60%, worst on unders (raw, pre-freeze rows). LINE_CAL_LIVE P2 checks CAL on under-lean legs; LINE_CAL_SIDE is its pre-registered follow-up | **BLOCKED** until LINE_CAL_LIVE's look | none: a new rule would be a second look |
| 8 | Conditional calibration where decisions are made (surfaced legs, alerts) | T495: panel top picks run 3-5 pts high (DK), 4-10 (Pinnacle); T496 tail fix NOT_FIXED; 11-10 unders review: 3 of 3 alert-eligible unders and 14 of 17 shown were sharp-line lag | PARTIAL historically; **MISSING** live | CAL_BY_TIME S11 (surfaced legs, label only). A rule with a pass word is a loose end |
| 9a | Serving: same inputs at fit and serve time | Bench term fit on `starter10`, served on `starter_rate`: 12.80% of fit legs flip (DECISIONS 10-29 12:29); `starter10` not stored (prediction_json keys checked tonight) | **MISSING** | CAL_SERVING_CHECKS C3 |
| 9b | Serving: known-at pairing of line and forecast | Display prices use the extended pairing (lines_through); calibration fields deliberately keep the made_at cut (design note B2): 500 of 1,877 eligible props calibrated; line leads above | PARTIAL (correct but narrow) | CAL_SERVING_CHECKS C1, C2, C7 |
| 9c | Serving: nothing shown where no calibrated value exists, never raw | App build 11-10 removed raw from screens but the API still served `p_over_raw` / `p_raw` (review M1; fix status not verified tonight); JARVIS "model X%" is raw (survey row 11); combo Analyst mode shows raw beside calibrated | PARTIAL | CAL_SERVING_CHECKS C4, C5 |
| 10 | Honest communication: n and intervals beside every reliability claim; display lock while a test is open | T493 tab copied with n and CI; display lock enforced (DECISIONS 11-09 23:02 disclosure, 11-10, 11-17 choice 2: History tab without chances); "calibrated" labels do not say at which line time | PARTIAL | CAL_SERVING_CHECKS C2 (time scope on the label), C8 |
| 11 | As-of inputs and planted-future checks on calibration inputs | T493 harness: planted future, A0 reproduction to 1e-9, holdout refusal; map loader refuses fit_through after 2025-06-30 | DONE | carried |
| 12 | Proper scoring rules, pre-registration, clustering, multiplicity | Log loss / Brier at the line, game-clustered paired bootstrap, BH families, frozen before results | DONE | carried |

## 3. The four drafts (one paragraph each; full text in `docs/prereg/`)
Common: all DRAFT revision 0; none frozen; none reads season 2025; none reads a slate inside LINE_CAL_LIVE's window (2026-10-30 through its
look date D) except CAL_SERVING_CHECKS, which reads no outcome at all. Projected D: 2026-12-03 if the 11-14 and 11-15 outage slates are the
only loss, 2026-12-12 if 11-16 is also lost (it had lines for 3 of 8 games), at LINE_CAL_LIVE's 0.838 coverage; the real number is its
`status` command (it needs `nba.duckdb`, not opened tonight).

### 3.1 CAL_SERVING_CHECKS (`docs/prereg/CAL_SERVING_CHECKS.md`): bugs and gaps, stated as checks
Eight checks with pass conditions on label-free counts and equalities: C1 pairing coverage >= 0.98 of eligible props per slate on 7
consecutive slates (today **0.266**, 500 / 1,877); C2 every served value carries its line time and a `time_scope` (fit window vs untested;
today 68.6% of values at lines >= 120 min, 5.6% in [45, 90)); C3 `starter10` stored and used (today not stored; 12.80% of fit legs flip);
C4 no raw model probability in any user-facing payload (JARVIS text, API fields, Analyst mode today); C5 nothing outside a validated scope
and no ladder inversions; C6 one number per (prop, book, line, line time) across card, panel, JARVIS and combo; C7 zero post-tip or
post-validity lines; C8 lock hygiene, replay oracle IDENTICAL, and no edit to LINE_CAL_LIVE's pinned files before its look. **Gate: PASS**
(all measurable now; C3's live half becomes measurable when the fix stores the field). No look, no outcome, no interaction with any
frozen test's computation.

### 3.2 CAL_BY_TIME (`docs/prereg/CAL_BY_TIME.md`): does the T-60 map hold at other line times?
Per slot (morning, t60, t30, t15), Pinnacle primary: HOLDS iff calibration in the large mean(y - CAL) has a game-clustered 95% CI inside
[-0.03, +0.03] and Brier CAL - RAW <= **-0.0015** with CI upper < 0 (BH over the four slots); HONEST_ONLY, NOT_HONEST, INCONCLUSIVE named.
Window: slates after the later of its freeze and LINE_CAL_LIVE's look. **Gate: n needed 200 games per slot and 3,000 (morning) / 5,000
legs; available rates 15-20 legs per game in the morning (coverage about 0.5), 31.4 at t60 (0.838, borrowed from October), 35-51 at t15
(0.95); P(HOLDS on the calibration part | true gap 0) 0.94-1.00 at 200 games, P(false pass | gap 0.04) 0.00. PASS for morning, t60 and
t15; t30 NOT MEASURED** (no run pairs at T-30; the capture count on `market_asof.duckdb` is a maintainer step before freeze; if still
unmeasured, t30 leaves the family by that count alone). **Projected look: about 2027-02-01** (the morning slot is last; t60 reaches 200
games 01-12, t15 01-08).

### 3.3 LADDER_CAL (`docs/prereg/LADDER_CAL.md`): an honest ladder on integer support
A threshold-wise logistic (T494's rung form: logit raw P(Y >= k), the main line's distance from the median, the threshold's distance from
the main line, its interaction, bench terms) fitted per stat on **every integer threshold within +-3 spreads of the DraftKings main line**
on 2023-24 (not only listed rungs, not only raw >= 0.5), leg-weighted, time-decayed, ridge, monotone by running minimum (violations a
guard). Revision 1: stage 1 is a walk-forward backtest on 2024 that must pass before anything else (section 0). Live verdict: HONEST_LADDER iff threshold-averaged Brier (a band-limited RPS) beats RAW by
<= **-0.0015** with CI upper < 0 and every one of five stated-probability bins has its game-clustered CI inside [-0.03, +0.03]; PIT
dispersion, 19-level coverage and RPS no-harm are secondaries; Kalshi N+ thresholds and LINE_CAL-at-every-rung (today's combo behaviour)
are reported. **Gate: fit side about 53,000 DK played legs per season with full `p_ge` arrays (pass); live 49 DK main legs per game at
t60 (coverage 0.64), 426 DK alternate rungs and 209 Kalshi rungs per game; synthetic P(pass | calibrated) 0.99-1.00 at 200 games; n
needed 200 games and 8,000 legs; projected look about 2027-01-22. PASS, with one open harness check** (whether 2023-24 fg3m legs reach
r >= +2; if not, fg3m's band narrows by that count before freeze).

### 3.4 PPLAY_CAL_LIVE (`docs/prereg/PPLAY_CAL_LIVE.md`): is the chance of playing honest?
The value the app shows at T-60 (`p_play_primary`; R for 1,287 of 1,416 rows tonight), scored against the frozen PPLAY_INTO_PROPS_LIVE
scorer's outcome, in five bins fixed now from label-free shares ([0, 0.5) 10.0%, [0.5, 0.7) 23.7%, [0.7, 0.9) 12.1%, [0.9, 0.97) 10.9%,
[0.97, 1] 43.3%). HONEST iff every bin's game-clustered CI is inside +-0.05 and the overall gap's inside +-0.02. **Gate: n needed 600
games; available 939 scheduled from 11-25 (25.3 rows per game); P(pass | calibrated) 0.95 at 600 games, 0.99 at 880; P(false pass | +0.08)
0.00. PASS. Projected look about 2027-04-13**, after PPLAY_INTO_PROPS_LIVE's season-end looks (600 games would be reached 02-26; looking
then is Devin's choice and a disclosed partial exposure). **Win probability, gate checked and failed:** P(CI inside +-0.03 | calibrated)
0.00 at 500 games, 0.04 at 900, 0.21 at 1,100; not drafted. **Under side:** LINE_CAL_LIVE P2 plus LINE_CAL_SIDE; not drafted.

### 3.5 Interactions with frozen tests (none of the four is a second look at LINE_CAL_LIVE)
| frozen rule | how each draft avoids it |
|---|---|
| LINE_CAL_LIVE (8e492f33; one look, display lock) | SERVING: no outcome read; does not edit pinned files before the look; recommends storing `starter10` after the look so the look still tests what was served. BY_TIME and LADDER: windows start after the look. PPLAY: P(play) is not a prop chance at a line; no CAL read |
| PPLAY_INTO_PROPS_LIVE (3a305482) | PPLAY_CAL_LIVE looks only after its season-end looks |
| FORWARD_PREREG_2026_27 | publishes CRPS and win Brier/ECE at its looks anyway; no draft shares a pass word with it |
| GAME_SCRIPTS_FORWARD (being frozen tonight, window not read by me) | LADDER_CAL: the main session checks the window before freezing; if they overlap, LADDER's look goes after it or Devin accepts a disclosed marginal-vs-joint exposure |
| UNDERS_FORWARD, BEST_PRICE_FORWARD | read prices and ROI; drafts read calibration; labels only where they touch the same Kalshi outcomes (LADDER S3) |
| JARVIS weight rule (frozen 10-25) | keeps reading RAW; only the shown text changes (SERVING C4) |
| LINE_CALIBRATION map, ALT_RUNG_CAL map | never edited; served unchanged inside their support |

## 4. Recommendations that are not drafts
1. **Order of work:** CAL_SERVING_CHECKS first (it needs no outcome and decides what users see tonight), and within it C4 (no raw anywhere)
   and C1 (pairing) before C3 (bench; after the look). Then freeze CAL_BY_TIME and LADDER_CAL so their windows open the day after
   LINE_CAL_LIVE's look; PPLAY_CAL_LIVE any time before its window opens (freeze by 11-25 for the 939-game count).
2. **The pairing fix and the time question are coupled.** Pairing with later lines (median 13 min before tip under the extended rule)
   raises coverage about 3.7x and moves the line time further from the fit window. That is why C2 labels the time scope and CAL_BY_TIME
   exists. My recommendation: do the pairing fix with the label, not the strict 45-90 min variant (which shows nothing in the morning).
3. **Do not refit the main-line map before LINE_CAL_LIVE's look**, and not on 2026 rows before 400 games (its own section 9).
4. **Delete nothing yet.** The 5.6% fit-window share is not evidence the served values are wrong; it is evidence they are untested.
5. **Drift (audit item 5), after the look:** a monitor that alarms without revealing outcomes needs its boundary frozen with the rule it
   protects. Proposed shape for the next main-line rule (not drafted): a two-sided sequential test on mean(y - CAL) per week with an
   alpha-spending boundary, whose only output before the look is the word "inside" or "outside"; crossing "outside" triggers that rule's
   look early. LINE_CAL_LIVE cannot take one now (it forbids interim looks), so the first chance is the rule after it.
6. **In-game plug-in (not drafted; agent/modeler-live-model owns it):** the in-game line model's calibration is CAL_BY_TIME's structure with
   the slot replaced by the game clock (halftime, end of Q3): per checkpoint, calibration in the large with a game-clustered equivalence
   margin, Brier vs the baseline that knows what the model knows (BEST_PRACTICES 2026-10-22), the Brier decomposition, and a display lock
   on "experimental" chances until its look. Its served number should pass CAL_SERVING_CHECKS C4-C7 the same way.
7. **Loose ends recorded, not chased:** home/away and z-distance slices (audit 1b); a pass-word rule for surfaced legs (audit 8); the
   P(play) ECE contradiction in the repo (survey row 8); a market-free ladder arm for Kalshi rungs without a DK main line (LADDER choice 1).

## 5. What calibration cannot do (one page, plain)
Calibration is bookkeeping on our own words. It takes the chances the model already produces and bends them so that "60%" happens about 60%
of the time. It does that by learning from past mistakes of the same model; it adds nothing the model did not already know. Three
consequences:
- **It cannot beat the price.** The tested map sits at log loss 0.6882 against Pinnacle's 0.6865 (T493), and the model's information beyond
  the market is about a third of the smallest gain we would call real (T517). An honest 55% is still a 55% the market already prices.
  "Keep your money" stands; `no_positive_ev_found` stays a first-class answer.
- **It shrinks.** Most of a calibration gain is moving overconfident numbers toward the middle (the base rate C0 gets two thirds of T493's
  gain; T495's joint gain is mostly a confidence correction). Honest chances will look less exciting. That is the point.
- **It cannot see news.** Most prop error is minutes, and most minutes error is not knowable 60 minutes before tip. What reduces it is
  information: the T-30 starting lineups (T522: real for points among players who played, not for late scratches), faster injury news (the
  11-10 unders were all the sharp line lagging news Kalshi already had), and in-game state (T508). Devin's goals that need information, not
  calibration: beating the market anywhere, better unders (the under problem is the median sitting low plus late news, T494 and the 11-10
  review), better P(play) for questionable players (report timing), and live betting.

**What the app may show now, by this audit:**
- The main-line calibrated chance where it exists, with its line time on screen and the words "map fitted at T-60" until CAL_BY_TIME
  reads; nothing where it does not exist (Devin's rule). Raw nowhere outside the T493 history tab.
- No ladder or curve off the main line (combo v1 as built). Rung chances only inside the frozen rung map's support (DK listed rungs, raw >=
  0.5), labelled as such.
- P(play) with "chance of playing, not yet checked live". Win probability with "not calibrated against results this season; the market
  is better on winners".
- No chance beside its outcome for slates on or after 10-30 until LINE_CAL_LIVE's look; and, if Devin keeps the defaults, for each new
  rule's window until its own look.

## 6. Proposed rows (drafted here, not appended)
**PREDICTIONS_LEDGER** (priors stated before any result; seen before: stated probabilities only, label-free; no 2026 outcome):
| Date | Rule / question | Prior stated before the result | Where written | Outcome | Hit? |
|---|---|---|---|---|---|
| 2026-11-17 19:58 | CAL_SERVING_CHECKS (draft rev 0) | P(C1 coverage >= 0.98 on the first 7-slate scan after a pairing fix) 0.60; P(SERVING_OK, all of C1-C8, by 2026-12-31) 0.30 | docs/prereg/CAL_SERVING_CHECKS.md | pending | |
| 2026-11-17 19:58 | CAL_BY_TIME (draft rev 0) | P(t60 HOLDS) 0.45; P(t15 HOLDS) 0.40; P(morning HOLDS) 0.30; P(>= 1 slot NOT_HONEST) 0.35; P(look by 2027-02-15) 0.70; expected Pinnacle t15 M1 +0.01 (80% -0.02 to +0.04) | docs/prereg/CAL_BY_TIME.md | pending | |
| 2026-11-17 (rev 1) | CAL_BY_TIME stage 1 (section 11, partial backtests) | P(morning passes the m360 backtest) 0.55; P(t15 passes the t5 backtest) 0.40; P(the held m360 count clears 600 games / 15,000 legs in 2024) 0.70 | docs/prereg/CAL_BY_TIME.md | pending | |
| 2026-11-17 (rev 1) | LADDER_CAL (draft rev 1, two-stage; supersedes the rev 0 row, never appended) | P(stage 1 passes) 0.55; P(a P2 tail bin is the cause, given stage 1 fails) 0.6; P(2a HONEST_LADDER given stage 1 passes) 0.55; P(2b HONEST_LADDER given stage 1 passes) 0.45; expected stage-1 P1 -0.006 (80% -0.011 to -0.002) | docs/prereg/LADDER_CAL.md | pending | |
| 2026-11-17 (rev 1) | PPLAY_CAL_LIVE (draft rev 1, two-stage; supersedes the rev 0 row, never appended) | P(stage 1 chooses raw R) 0.35; P(chooses Platt of R) 0.35; P(stage 1 stops) 0.30; P(stage-2 HONEST given stage 1 passes) 0.45; P([0.5, 0.7) bin on raw R realises > stated + 0.05 in stage 1) 0.40 | docs/prereg/PPLAY_CAL_LIVE.md | pending | |
| 2026-11-17 19:58 | Freezes | P(at least two of the four frozen by 2026-12-05) 0.50 | this review | pending | |

**DECISIONS** (for the main session after Devin reads it): "2026-11-17 | Calibration program drafted (agent/modeler-calibration-program):
audit of 22 best-practice rows (DONE 5, PARTIAL 7, MISSING 6, BLOCKED 4 by LINE_CAL_LIVE or by data); four drafts: CAL_SERVING_CHECKS
(checks, no outcome), CAL_BY_TIME (look about 2027-02-01), LADDER_CAL (about 2027-01-22), PPLAY_CAL_LIVE (about 2027-04-13); win probability
and the under side not drafted (gate fail; LINE_CAL_LIVE P2). | Follows advice (retrain survey part 3; decisions review row 7). | A
LINE_CAL_LIVE NOT_TRANSPORTED would reorder: the main-line map is then withdrawn and CAL_BY_TIME loses its premise."

**TEST_LEDGER, HOLDOUT_ACCESS_LOG:** none (no result, no holdout touch).

## 7. Not verified, and what I was not able to do
- LINE_CAL_LIVE's current leg and game count (needs `nba.duckdb` and `market_asof.duckdb`; not opened by instruction). The look date is a
  schedule projection.
- T-30 line availability (G3 of CAL_BY_TIME); the t60 rates are October's (LINE_CAL_LIVE 10-20..28, CAL_EVERYWHERE 10-20..23), not
  re-measured, because the export keeps only the newest quote per pairing time.
- Whether the 11-10 app review's M1 (raw served on API routes) has been fixed since; JARVIS raw text is taken from the retrain survey, not
  re-read in code.
- Live `starter10` vs `starter_rate` disagreement (not stored; I chose not to rebuild it from box rows to stay clear of outcome tables).
- GAME_SCRIPTS_FORWARD's window (on agent/market-game-scripts, being frozen tonight).
- Whether 2023-24 fg3m legs reach r >= +2 spreads (LADDER_CAL G-fit support); a label-free harness check before freeze.
- No harness exists for CAL_BY_TIME, LADDER_CAL or PPLAY_CAL_LIVE; each needs one plus a synthetic dry run before freeze.
- Revision 1: whether the paid movement kinds (m360, m180, m90) were ingested into `odds_history.duckdb`, and how many 2023-24 prop legs
  m360 and t5 hold (CAL_BY_TIME stage-1 gate; count command in its section 11). The movement ledger shows 283,040 credits over 7,993 calls.
- Revision 1: whether per-row 2023-24 forecasts of the live P(play) arm (the 2022-start R) are stored anywhere; `scores.npz` holds R19 and
  P0 only (schema read, no values). The historical bin shares of P(play) are not measured (they need the p columns read).
- Revision 1: the remaining credit balance on the odds key swapped in on 11-16 (relevant only if Devin buys m30/m15).
- Revision 1: the stage-1 runtimes are estimates (LADDER 10-20 min; PPLAY regeneration under 30 min), not timed.
- Nothing was denied. I did not push, merge, open any database under the main checkout, or read any outcome. For revision 1 I read only
  file schemas (`scores.npz`, `arrays_draftkings.npz` keys and shapes), configs, ledgers and docs.

## 8. Playbook check (this review; class: quantile and distribution layers, plus trees-as-probabilities for P(play) and ratings for wins)
1. **Information or capacity?** Neither. Every draft corrects or checks how existing information is stated. Section 5 says where information
   would help instead.
2. **Enough data for the class?** Per-slice calibration volume is the driver: enough for pooled bins in every draft (gates above), thin for
   per-stat tails (labels only); not enough for win probability honesty this season.
3. **Valid tests?** Live windows after their own freeze and after LINE_CAL_LIVE's look; as-of pairing; DNP voided where books void; integer
   support; game clustering with date beside; synthetic-label power on real stated values; serving checks through shipped entry points.
4. **Lineage.** T339 -> T493 -> T524 for the main line; T491/T494/T495/T496 for rungs, joints and tails; T178/WINNER_RECAL for wins;
   T316/T482/PPLAY_INTO_PROPS_LIVE for P(play); COMBO_LINE_CAL and CALIBRATION_EVERYWHERE for scope and serving.
5. **What it is scored against.** Calibration in the large, bin reliability and Brier (or band-limited RPS) against RAW; prices only as
   reference rows; nothing claims an edge.
