# Pre-freeze adversary review: TEAM_STRENGTH_MARKET_STATE, TEAM_STRENGTH_MARKET_ANCHOR_LIVE, MOVER_DEPTH_LIVE

Reviewed: branch `agent/modeler-team-strength-movers`, commit e005d52 (`git rev-parse --short e005d52`), files
`docs/prereg/TEAM_STRENGTH_MARKET_STATE.md`, `docs/prereg/TEAM_STRENGTH_MARKET_ANCHOR_LIVE.md`, `docs/prereg/MOVER_DEPTH_LIVE.md`,
`docs/research/team_strength_and_movers_2026-10-23.md`, and the three PREDICTIONS_LEDGER rows in the same commit. Main was at 2f3368f
(`odds history config: add season 2022, cap 2.25M credits`) when this was written. Written 2026-10-23 20:30 CDT (`date`).
Attacks ran on seasons 2023-2024 only, every DuckDB file opened `read_only=True`, `build_game_tipoff(max_season=2024)`, no outcome
column (scores, wins) read; season 2025 never loaded. Scripts live in the adversary's scratchpad; the core one is reproduced in the
appendix because scratch is not durable.

Verdict: BLOCKED

| Draft | Verdict |
|---|---|
| TEAM_STRENGTH_MARKET_STATE | **NOT_READY** (13 required edits; wait for the 2022-23 lines, then re-gate and freeze) |
| TEAM_STRENGTH_MARKET_ANCHOR_LIVE | **NOT_READY** (12 required edits; cannot freeze before its live gate L2 is measured, non-negotiable 8) |
| MOVER_DEPTH_LIVE | **NOT_READY**, expected to become **CLOSE** at its own gate L3 (7 required edits; projected n below the declared n) |

Devin's "yes, freeze all three after the review" cannot be carried out tonight for any of the three without breaking a
non-negotiable: two drafts have unmeasured minimum-data gates (rule 8 says the gate is checked on the real data before the freeze),
and the third's power gate measured the wrong arm.

**Top finding.** MARKET_STATE's power gate (check 4) sized an oracle, not the arm. It moved injury-Elo toward the game's OWN
Pinnacle T-60 line (I reproduce its numbers exactly: lambda 0.25 gives -0.0059 / -0.0069). The arm can only use PAST lines. Running the
draft's filter label-free on the stored lines and pricing the real arm in a world where Pinnacle T-60 is the truth (the most
favourable world for the arm), the best expected gain at any mixing weight is **-0.0018 (2023, n 1,267) and -0.0034 (2024, n 1,279)**:
under the -0.004 floor in both seasons even if the market is exactly right. As drafted, PASS is out of reach by construction
(prior 0.25 should be about 0.03), and the named fail verdict ("the gap is game-day information; team strength closed") would be
a false reading: the same label-free run shows the past-lines rating correlates 0.35 / 0.46 with the injury-Elo-to-market gap.

## 1. Checks run (commands, n, numbers, pass/fail)

All commands: `cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -I <scratch>/attack/<script>.py; echo exit $?`, exit 0 each.

| # | Check | Script | n | Result | Pass |
|---|---|---|---|---|---|
| G1 | MARKET_STATE gate 1, Pinnacle T-5 spread coverage and fallback | gates.py | 1,318 / 1,315 OOF keys | Pinnacle 1,302 / 1,292; >= 5 books 1,318 / 1,315; min books 16 / 14, median 20; one point per (game, book, side) at T-5, 0 multi-rung rows | reproduces |
| G2 | gate 2, key identity OOF vs odds_history | gates.py | same | 0 / 0 keys missing | reproduces |
| G3 | gate 3, both teams >= 10 earlier season games | inline (in this review, section 2.3) | 1,318 / 1,315 keys | 2023 1,162 of 1,318; 2024 1,165 of 1,320 nba.duckdb games, **1,161 of 1,315 OOF keys** | reproduces (denominator mixed in the draft, minor) |
| G4 | gate 4 as drafted (oracle arm on own-game line) | inline | 1,267 / 1,279 | injury-Elo base, lambda 0.25 / 0.5 / 1.0: 2023 -0.0059 / -0.0101 / -0.0136; 2024 -0.0069 / -0.0118 / -0.0157 | reproduces; **measures the wrong arm** |
| G4' | real arm, label-free: draft filter on past T-5 spreads, A0 logit + c (M - E), truth = Pinnacle T-60, 400 simulated outcome draws (seed 0) for the half-width | kalman.py, kalman2.py | 1,267 / 1,279 (1,049 / 1,050 after 15-game burn-in) | see table 2.1: best expected dLL -0.0018 / -0.0034 at c about 0.5, half-width 0.0038 / 0.0039 at that c | **FAIL against the -0.004 floor in both seasons** |
| L1 | Leak: timing of the T-5 snapshot that feeds y_g, vs real tip | kalman.py | 2,633 games | tip minus snapshot_ts = 9 min for every game (min 9, max 9); 0 after tip; 0 missing tips | pass (no in-play lines enter the state) |
| L2 | Leak: the own-game line path | read of the draft | n/a | own-game lines enter only the reference arm; the planted-future gate perturbs y_g and scores, not raw odds rows | partial (edit S9) |
| L3 | Missingness: games with no usable y_g | kalman.py | 2,638 nba.duckdb games 2023-24 | 5 without any T-5 spread (the five restored neutral-site games, outside the OOF) | pass, specify handling (edit S10) |
| L4 | Missingness: the c1 feature on training rows before 2023 | read of `nba/models/injury_elo.py` 600-628 | n/a | the second stage trains on all earlier games and `fill_null(0.0)`; with no 2022 lines, M - E is 0 for every 2022 row and nonzero from 2023: a season-constant NULL pattern (an era flag) and no c1 signal for the first 2023 block(s) | **FAIL** (edit S3; the 2022 purchase repairs it) |
| L5 | Placebo P2 draws "a random other game in the same 14-day block": can it draw a later game? | read | n/a | yes: a block contains games after g's date, so P2's y can come from the future | **FAIL** (a placebo that leaks the future can only false-fail; edit S5) |
| L6 | Holdout | read + queries | n/a | all three drafts and every query of mine filter season <= 2024; MOVER L3 needs 2025-26 team ids and minutes, which needs a HOLDOUT_ACCESS_LOG row first (precedent row of 2026-10-20, scorecard `prior_teams`) | pass for MARKET_STATE / ANCHOR; edit M3 for MOVER |
| L7 | Live capture exists for ANCHOR's fresh stratum | read-only `data/markets/market_asof.duckdb` | 22 games in `live_book_odds` | `last_update` column present; ladder kinds close 12, t5 15, t60 17 games (2026-10-20..23) | gate L2 unmeasured: **cannot freeze** |
| L8 | Comparator separation (T502 failure mode) | read | n/a | ANCHOR's K1 calibration-only comparator is the right idea; it is not validated in T502's synthetic worlds; MARKET_STATE does not compare with the market and its C2 control is mis-specified | edits A6, S4 |

Not run: any outcome-reading check (shuffled labels, knockout, replication need a fitted harness; none exists); the
stale-report statistics in the ANCHOR draft (296 / 386 / 536 min, 5 of 1,251) were not re-derived; no live fresh-stratum count.

## 2. TEAM_STRENGTH_MARKET_STATE

### 2.1 The real arm's ceiling (label-free)

Filter exactly as drafted (y_g from T-5 spreads, Pinnacle else median of books, 0.1325 logit/pt, R 6.25, q 0.11, P0 16, carry 0.609,
H_t prior 2.5 weight 50, predict a date from the state at the end of the previous date) except the 2023 start (theta 0 instead of the
Elo/28 map; the burn-in column shows it does not matter). E = logit of stored `rung0_mov_elo`, A0 = logit of stored
`rung0_injury_elo`, M = 0.1325 (theta_h - theta_a + H_t). Arm = A0 + c (M - E), i.e. the draft's A2 with c1 = c and b, g left as A0's.
Truth = Pinnacle T-60 de-vigged home probability; expected log loss computed exactly, half-width from 400 Bernoulli draws.

| Season, set | sd(M - E) | corr(M - E, mkt - A0) | c 0.25 | c 0.4 | c 0.5 | c 0.6 | c 0.75 | half-width at c 0.5 |
|---|---|---|---|---|---|---|---|---|
| 2023 all, n 1,267 | 0.297 | 0.347 | -0.0015 | -0.0018 | -0.0018 | -0.0016 | -0.0009 | 0.0038 |
| 2024 all, n 1,279 | 0.316 | 0.462 | -0.0023 | -0.0031 | -0.0034 | -0.0034 | -0.0031 | 0.0039 |
| 2023 burn-in >= 15, n 1,049 | 0.286 | 0.349 | -0.0015 | -0.0019 | -0.0019 | -0.0018 | -0.0013 | 0.0039 |
| 2024 burn-in >= 15, n 1,050 | 0.313 | 0.443 | -0.0021 | -0.0028 | -0.0030 | -0.0030 | -0.0026 | 0.0042 |

Sensitivity (not tuning; the conclusion is what is tested): R = 16 or R = 2.25 moves the best 2023 / 2024 gain to -0.0021 / -0.0030
and -0.0018 / -0.0030. In no setting does the arm reach -0.004 in either season. Caveats: the second stage is not refit (a refit
can re-weight b and g, a few ten-thousandths), and real outcomes are not Pinnacle; the arm could gain more only where the market
itself mis-prices past information, which is the speculative part. Reading: past market views carry real strength information
(a third to a half of the gap's correlation), worth about 0.002-0.003 log loss per game at best, below this floor.

### 2.2 Leakage and as-of

* The game's own line: never an input to A1/A2/C2/P2 in the text. The T-5 snapshot is 9 minutes before the real tip for all
  2,633 games, so no in-play line ever enters the state. Past lines enter after the date. Holds.
* The planted-future gate (check 5) perturbs y_g and scores; a leak through another path (H_t from a different query, the reference
  arm's own-game T-60 join) would not show. Edit S9.
* Missingness: the M - E column is undefined for every pre-2023 training row of the second stage (L4). This is not a label leak,
  but a NULL-to-0 pattern constant across a season is an era flag and leaves c1 untrained for the first 2023 block(s).
* P2 can draw future games (L5).
* Report-stamp offset: the stored OOF (file dated 2026-10-09) predates the 2026-10-15 PDF-header fix (3.8% / 4.8% of 2022-24 games
  use a report published after tip-60). Both arms share it, so A2 - A0 is not biased by it; the draft's "real-tip report gating"
  sentence should say so (edit S13).

### 2.3 Comparator and controls

* What A2 - A0 can show: past market views add to injury-Elo. Market mis-calibration is not in play (nothing is scored against
  the market), so T502's failure does not recur in that form. The live question (own-game line plus our news) is ANCHOR's.
* C2 is not information-matched: it feeds realised margins into the same filter with R = 6.25 (a 2.5-point noise), while a result
  differs from the strength difference by about 12 points. C2 is then a badly over-reactive filter; its c1 fits to about 0, so
  C2 is about A0 and "A2 - C2 <= -0.002" is just pass condition 1 again. Also, A0 already contains a results filter (E).
* Guard thresholds (P2 - A0 >= -0.001; placebo) are fixed numbers, not sized from a permutation null (BEST_PRACTICES 2026-10-12).

### 2.4 Verdicts, floors, family, slices

* Floor -0.004, both seasons, CI upper < 0: keep the floor. Lowering it after this power number would be choosing the bar from
  the gate (rejected precedent: ROLE_PRIOR_SALARY option (c), DECISIONS 2026-10-21).
* The verdict set is not exhaustive: 2023 fails and 2024 passes, reliability fails, or date-block CI fails all name no verdict.
* FAIL_MARKET_NOT_STRENGTH claims "the gap is game-day information". Section 2.1 already says a null at this floor is the
  expected outcome even if past lines carry real information; the sentence must go.
* Family: primary as an intersection, descriptive BH over 8: fine. Slices: fine and fixed. Playoffs guard +0.01 on about 84 games
  is descriptive in practice; say so.
* Game id prefixes: the stored OOF holds 002 / 004 / 005 (005 = play-in, 12 rows per season per model), no 003. The draft says
  "002, 003, 004".

### 2.5 Should it wait for the 2022-23 lines? Yes.

Devin approved the purchase and main already carries the config (2f3368f: season 2022 added, cap 2,250,000 credits, the same
t60 / t5 snapshot definition as 2023-24). Reasons to wait:
1. The burn-in itself costs little (2.1: 2023 -0.0018 all games vs -0.0019 after 15 games), but the second stage's c1 has no
   training rows before 2023 without 2022 lines (L4). With them, c1 trains from 2022 like b and g, and the NULL pattern is gone.
2. Adding 2022 later would be a new rule run after 2023-24 were scored: a forking path on the same seasons.
3. The pull uses the same snapshot code, so gate 1 and the timing check carry over directly.
Cost of waiting: until the pull lands and gates G1-G3 plus the timing check are re-run on 2022. Nothing else depends on it.

### 2.6 Required edits (MARKET_STATE)

S1. Replace gate 4 with the real-arm, label-free power: the filter as frozen, arm A0 + c (M - E) for c in {0.25, 0.5, 0.75},
truth = Pinnacle T-60, reporting the table in 2.1 (re-run with 2022 lines and the Elo/28 start). State in the signing line: "under
market-as-truth the best expected gain is about -0.002 (2023) / -0.003 (2024), below the -0.004 floor; the expected verdict is NULL".
Append a new PREDICTIONS_LEDGER row with revised priors (do not edit the old one): suggested P(PASS) 0.03.
S2. Inputs: add the 2022-23 lines (odds_history season 2022, same `spreads` / `t5` query). The filter starts on the first 2022 date
with theta = (Elo - 1500)/28 from the production Elo at the last 2021 date; 2022 is warm-up and descriptive only (scored keys stay 2023
and 2024). Gate 1 (>= 97% Pinnacle, 100% >= 5 books), gate 2, gate 3 and the "T-5 snapshot before real tip for every game" check are
re-measured on 2022 and written in. Delete the follow-up clause and "no 2022-23 purchase to save a null" (moot). If 2022 fails gate 1,
the rule says so and runs without 2022 under edit S3's handling.
S3. c1 feature rows: say exactly how training rows without M are handled (with 2022 lines: none in 2022-24; earlier seasons excluded
from the c1 fit, never filled with 0), and how the first block is fitted.
S4. C2: give it its own noise, R_C2 = 144 (12-point result-vs-strength sd, stated now, not fitted), same q, P0, carry; or drop C2 and pass
condition 2 and say A0's E is the results control. Either way it must be information-matched (BEST_PRACTICES 2026-10-22).
S5. P2: draw the substitute y only from games dated strictly before g's date (same season, within the previous 14 days); define
"teams scrambled" as "the drawn game's y, applied to g's two teams"; seed fixed.
S6. Size the placebo guard (P2 - A0) from a permutation null: K >= 50 P2 seeds on synthetic outcomes drawn from Pinnacle T-60 (label-free),
fail beyond 2 null sd; report the null sd next to the bootstrap CI.
S7. Verdicts: map every outcome to PASS / NULL / INCONCLUSIVE, with LEAK_SUSPECT as a stop state. NULL = any failure of pass
conditions 1-5 with realised half-width <= 0.004 (including 2023 fails / 2024 passes, reliability, date-block). Rename
FAIL_MARKET_NOT_STRENGTH and ARCHITECTURE_NOT_INFORMATION to NULL sub-labels and delete "the gap to the market is game-day
information; team-strength modelling is closed".
S8. Null hygiene (BEST_PRACTICES 2026-10-21): the results file reports (a) the arm read its input (M coverage, c1 by month, its sign),
(b) a positive control (synthetic outcomes drawn from a truth that contains c* (M - E) with c* = 0.5; the harness must detect it), (c) the
smallest effect the CI excludes, beside the floor.
S9. Planted future at the source: perturb every odds_history row (all markets, all snapshot kinds, including the own-game h2h used by
the reference) and every score of games dated on or after d; logits of games dated <= d are byte-identical; plus a descriptive
"state lagged one extra date" arm.
S10. Game ids 002 / 004 / 005 (not 003). Neutral-site games: H_t = 0 and whether they update the filter; the five games without a T-5
spread do not update it.
S11. Gate 3: one denominator (OOF keys: 2024 1,161 of 1,315).
S12. Commit the gate scripts (label-free) with the freeze, so the gate numbers are reproducible.
S13. Say that A0's stored OOF predates the report-stamp fix and both arms share it.

## 3. TEAM_STRENGTH_MARKET_ANCHOR_LIVE

The direction is right and the comparator is the right shape: K1 (market calibrated with a and c, b = 0) is what any information
claim must beat, and the fresh-vs-stale contrast is the separating prediction T502 lacked. But the rule is not a working test yet.

* Rule 8: L2 (fresh-game count) is unmeasured; the draft itself says the rule stops before freeze if the projection fails. 22 games
  sit in `live_book_odds` today. It cannot be frozen tonight.
* b is undefined where it matters: one b or one per stratum, fitted on which games, walk-forward from what start. With lambda_b = 400
  toward 0 and about 150 fresh games, the penalised b is near 0 by construction, so K2 is K1 and FAIL_B_ZERO ("CI upper of b < 0.05")
  fires from the prior, not the data. That is an invalid test.
* F is defined two ways: "newest injury-report snapshot" (line 28 area) vs "news item, a status or lineup change" (Strata). A new
  snapshot with unchanged statuses is not news.
* K1 is "fitted on market data only" (L3), yet a and c are a calibration and need outcomes. The retrospective initial fit is T-5
  close (L4) while live stamps use the latest capture (T-60 / T-30 / T-15): different snapshots calibrate differently.
* K4 "one slate earlier" is undefined.
* "First 15 live games excluded" appears under protections but nowhere in the design.
* H2 names no model for margins and totals (no `m_model`).
* Playoffs / play-in not addressed. Guards K3, K4 at +-0.001 are not sized from a null.
* Separation is claimed, not shown. T502 was closed after synthetic worlds showed its checks fired on mis-calibration with inert
  features. The same worlds are available label-free here (2023-24 Pinnacle, stored OOF).
* Selection, not leak: a fresh game is one where the line did not move after our news, which is partly the market judging the news
  immaterial. This pulls toward null; state it.

Required edits (ANCHOR):
A1. Measure L2 before the freeze (the draft's own 2026-11-10 date is fine): fresh games per week under the edit-A2 definition, from
`live_book_odds.last_update` and the report/lineup stamps; stop as UNDERPOWERED if the projection is < 150 by 2027-02-28.
A2. One definition of F: as_of of the newest status-changing news item (report status change, lineup confirmation) minus the
`last_update` of the book used, per game at the stamp.
A3. Define b exactly: b_fresh and b_stale separately, month walk-forward from b = 0, lambda_b stated; K2 is scored with the
walk-forward b. Judge "b is zero" on an unpenalised estimate with its bootstrap CI, or delete FAIL_B_ZERO.
A4. K1: a and c fitted walk-forward on outcomes of games tipped before the scoring block; initial values from 2023-24 Pinnacle at the
matching snapshot (T-60 for T-60 stamps and so on); delete "market data only".
A5. K4: "the report release immediately before the one the model used (by printed as_of)".
A6. Before freeze, label-free: re-run T502's two synthetic worlds on 2023-24 Pinnacle T-60 + stored OOF with this rule's K0-K4
and strata: (i) inert features + planted market mis-calibration: K2 - K1 must not pass; (ii) planted b = 0.25 in the fresh stratum
at the declared n: report the rate it passes.
A7. Size K3 and K4 guards from K >= 50 permutation seeds (synthetic, label-free), fail beyond 2 null sd.
A8. Define the first-15 exclusion (or delete it) and state that counting starts at the freeze commit time, by tip time.
A9. Name the margin and total models, or drop H2.
A10. Population: regular season and play-in only, or say playoffs are in.
A11. Verdicts onto PASS / NULL / INCONCLUSIVE (LEAK_SUSPECT as a stop), with the excluded effect stated on a NULL.
A12. Reconcile the 386-minute median against T503 or drop it from the justification (it is support, not a gate).

## 4. MOVER_DEPTH_LIVE

* Rule 8: L1-L4 are unmeasured, and the draft says it is not a rule before L3. Freezing tonight contradicts its own text.
* The declared n is very likely unreachable. T500's cohort gave 1,202 / 1,025 pts rows over movers' whole first 10 games (2023 / 2024).
  Counting starts at the freeze; by 2026-10-24 most teams have played 2-3 games, so roughly a quarter of the population is gone:
  about 770-900 pts rows, below 1,000, and the population ends with game 10 (mid-November), so "or at season end" never helps.
  Each day of delay costs about another tenth. (Estimate from the ledger counts; the live count is L3's job.)
* "Row" is (stat, game, player) in the population text, while power is quoted per pts row: the 1,000 is ambiguous by a factor of 4.
* Prior-season minutes rank and the mover flag read 2025-26 statistics (team ids and minutes per game): a holdout-log row is
  required before L3 runs (class of the 2026-10-20 scorecard `prior_teams` disclosure and the training-use rows).
* Shuffle S (>= -0.002; LEAK at -0.004) is not sized from a null. Played-only scoring (both arms scored, not DNP) is fine paired,
  but say P(play) is untouched.
* Good: player clustering, leave-top-5-out, Mc information control, minutes-only isolation, floors unchanged, INCONCLUSIVE expected.

Required edits (MOVER):
M1. Measure L1-L4 before any freeze; if L3's projection is < the declared n, record UNDERPOWERED_STOP (CLOSE) instead of freezing.
M2. Define n in pts rows; declare the population as fixed (all eligible pts rows with k <= 10 tipping after the freeze commit) and state
its projected size and MDE, or make the primary the pooled 2026-27 + 2027-28 population from the start (the follow-up becomes the rule).
M3. A HOLDOUT_ACCESS_LOG row (identifiers and prior-season minutes, training/systems class) logged before L3 or any live computation.
M4. Size S's guard from K >= 50 rank permutations (synthetic outcomes from M0's own distribution), fail beyond 2 null sd.
M5. State that DNP rows and P(play) are outside the contrast.
M6. Verdicts onto PASS / NULL / INCONCLUSIVE (FAIL_HARM a NULL sub-label; UNDERPOWERED_STOP a pre-freeze close).
M7. Say freeze timing costs population (about a tenth per day in the first week).

Recommendation (opinion): unless L3 surprises, close at the gate and take ROLE_PRIOR_SALARY's option (d) path: show the depth-chart
prior beside a mover's forecast, unscored. Movers are ~4% of rows; even T500's 2024 point gain is about 0.001 overall.

## 5. Playbook check

| Question | MARKET_STATE (Ratings + linear second stage) | ANCHOR_LIVE (logistic pool on a market base) | MOVER (shrinkage minutes input into GBM/count layer) |
|---|---|---|---|
| 1 Information or capacity | information (past market views); measured label-free: real, corr 0.35 / 0.46 with the gap | information only in the fresh stratum; elsewhere capacity (calibration) | information (new-roster rank); pace/quality second order |
| 2 Data enough for the class | yes for a rating (one season warm-up; 2022 lines make it clean) | no retrospectively (draft's own power table); live count unmeasured | curve yes (stayers); evaluation no |
| 3 Test valid | not yet: gate on the wrong arm, C2 not information-matched, placebo can see the future, c1 era NULL | not yet: penalised-b verdict artifact, undefined F/b/K4, separation not shown | not yet: gates unmeasured, n ambiguous and likely short |
| 4 Lineage and guards | T091/T128, T178 (playoff guard carried), T182/T318-T319, T235/T236, T502; guards carried except permutation-sized placebo | T204/T212, T235/T236, T472, T502 (its synthetic worlds not carried: edit A6) | T500 (player clustering, top-5 share carried), T504, T512, T446-T449 |
| 5 Scored against | injury-Elo log loss; market is reference only | the calibrated market K1, correct | production integer CRPS, correct |

Class check, Ratings: "is the effect plausibly larger than about 0.002 log loss, one season detecting 0.004-0.005": measured label-free,
the effect's ceiling is 0.002-0.003; one season cannot certify it at the floor.

## 6. Biggest risk if MARKET_STATE is frozen as drafted

A NULL that is read as "team strength is closed; the gap is game-day information" when the test could never have passed. That is an
invalid test recorded as a finding (BEST_PRACTICES 2026-10-12, "An invalid test is not a null").

## 7. Corrected family and power note

MARKET_STATE: one decision-bearing intersection (2023 and 2024) plus 8 descriptive contrasts under BH q 0.10; slices descriptive. Power:
half-width about 0.004 per season at c about 0.5 (standard error about 0.002), so the 80%-power MDE is about 2.8 x 0.002 = 0.0056
per season, above the 0.002-0.003 the label-free bound allows. The honest expected verdict is NULL, recorded as "no gain larger than
about 0.004-0.006 per season", not as "no effect". ANCHOR: MDE 0.008 log loss at 150 fresh games (draft's own figure, arithmetic
checked: 1.96 x 0.05 / sqrt(150) = 0.0080). MOVER: MDE about 0.024-0.030 pts at about 800-1,000 rows (T504 scaling).

## 8. Not verified

The ANCHOR stale-report statistics; the live fresh-stratum rate; the 2022 Odds API Pinnacle coverage (the pull has not run); a refit
second stage (my arm adds c (M - E) to the stored A0 logit); the Elo/28 start (I used theta 0, which the burn-in column shows does not
change the conclusion); credit cost of the 2022 game-lines pull (the config caps it; the 50,000 estimate in the draft is unverified).
No outcome-reading attack (shuffle, knockout, replication) was possible: there is no harness or result.

## 9. Drafted rows (not appended; main session decides)

PREDICTIONS_LEDGER (adversary priors, before any fit):
`| 2026-10-23 | OPEN: adversary priors on TEAM_STRENGTH drafts (pre-freeze review, docs/reviews/team_strength_drafts_prefreeze_2026-10-23.md) | MARKET_STATE as revised (S1-S13): P(PASS) 0.03, P(NULL) 0.80, P(INCONCLUSIVE) 0.15, P(LEAK_SUSPECT) 0.02; A2 - A0 expected -0.0020 (80% -0.0040 to 0.0000) per season. ANCHOR: P(L2 passes by 2026-11-10) 0.35. MOVER: P(L3 projection >= 1,000 pts rows) 0.15. |`

DECISIONS (for the main session to write if adopted):
`| 2026-10-23 | Team-strength drafts not frozen tonight despite Devin's yes: adversary NOT_READY on all three (MARKET_STATE: gate 4 sized the own-game-line oracle; real arm's ceiling -0.002/-0.003 vs floor -0.004; wait for 2022-23 lines, apply S1-S13; ANCHOR and MOVER: live gates unmeasured, rule 8). | Non-negotiable 8; an invalid test is not a null. | Devin may override; a freeze without S1/S7 is recorded as his override. |`

## Appendix: core attack script (kalman2.py, label-free)

Run: `RN=6.25 BURN=15 .venv/bin/python -I <scratch>/attack/kalman2.py` (also RN 16 / 2.25, BURN 0 / 15).

```python
# inputs: odds_history (read_only) spreads t5 + h2h t60 pinnacle, season<=2024; nba.duckdb games (ids, dates, teams only; no scores);
# stored OOF rung0_mov_elo / rung0_injury_elo probabilities 2023-24.
# y_g = -home_point + logit(home cover prob, multiplicative de-vig)/0.1325; Pinnacle else median over books.
# filter: per date predict from end-of-previous-date state (theta_h - theta_a + H_t), then update each game:
#   S = P_h + P_a + R; innov = y - H - (th_h - th_a); th_h += P_h/S innov; th_a -= P_a/S innov; P *= 1 - P/S
#   between dates P += 0.11 * days/2; season boundary theta *= 0.609, P = 16; H_t = (2.5*50 + sum y)/(50 + n) over earlier games.
# arm = logit(A0) + c (0.1325 M_pts - logit(E)); truth = Pinnacle T-60 de-vigged home prob;
# expected dLL = mean over games of E_truth[LL(arm) - LL(A0)]; half-width = 1.96 sd over 400 simulated outcome vectors (seed 0).
```
