# LINE_CAL_LIVE pre-freeze review (adversary), 2026-10-29

Verdict: BLOCKED

**Pre-freeze outcome: AMEND** (exact edits below; none needs a new question, none touches an outcome). After B1-B3 and M1-M4
are applied, a re-review of the revision is enough to return READY_TO_FREEZE; I expect no new outcome-facing issue.

- Object: draft text bf74ce71 (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CAL_LIVE.md | shasum -a 256` gives
  `bf74ce71d34e926be0b42ad6aced6dacb239fd52b3894795c2035aa0938310be`), branch `agent/modeler-props-recal` HEAD ec98d17, harness
  `research/eval/line_cal_live_eval.py`, gate `research/eval/line_cal_live_gate.py`, tests `tests/eval/test_line_cal_live_eval.py`.
- Model class: quantile and distribution layers, recalibration at the line (MODEL_PLAYBOOK). A forward transport check of a frozen
  map (T493, payload 443743a1), with nothing fitted.
- Data touched: season 2026 label-free counts (forecasts, lines, tips, box minutes as priors only); 2023-24 Pinnacle t60 fit legs,
  inputs only (the `over` column dropped before any computation). **Season 2025 never loaded. No 2026 outcome (y, brier, crps,
  log_loss) read or attached to any CAL or RAW leg.** All DuckDB connections `read_only=True`.
- Top finding: **the harness lets one `run --today <a future date>` produce a verdict word early.** On partial data it reports it as
  the "backstop" look and skips the settlement check (B1). Second: **the map is not served the way it was fit.** Its bench term was
  fit on `starter10` but is served on `starter_rate` (a different statistic). The two disagree on 12.8% of the map's own fit legs
  (M1). The rule says "bench = starter rate < 0.5" as if they were the same thing.

## Checks run (exact commands; all from the worktree; exit codes shown, nothing piped)

`PY=/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, `WT=<worktree>`, `S=<scratchpad>/attack`. `PYTHONPATH=.` resolves
`nba` and `research` to the worktree (verified: `nba.__file__` is under `wt_recal/`).

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| T1 | harness + gate tests | `PYTHONPATH=. $PY -m pytest -q --no-cov tests/eval/test_line_cal_live_eval.py tests/eval/test_line_cal_live_gate.py -p no:cacheprovider` | 21 tests | 21 passed, exit 0 (with coverage on, the subset trips the 70% repo coverage gate: exit 1, not a test failure) | yes |
| T2 | dryrun (synthetic outcomes) | `PYTHONPATH=. $PY -W ignore -m research.eval.line_cal_live_eval dryrun --data-root /Users/devin/Downloads/nba-prediction` | 1,787 real legs tiled to 7,148 / 228 games | transport: TRANSPORTS (Brier CAL-RAW -0.0080 [-0.0100, -0.0060]); no_transport: NOT_TRANSPORTED (+0.0097 [+0.0078, +0.0115]); under_gap: PARTIAL naming P2, P4. `dryrun OK`, exit 0 | yes |
| T3 | status (label-free) | `... line_cal_live_eval status --data-root ... --assume-freeze 2026-10-29` | 6,028 scored rows | prefreeze 57 games / 1,787 legs; window 0; look not_reached; exit 0 | yes |
| A1 | bench input, fit vs served (2023-24 legs, label-free) | `$PY -W ignore $S/a1_bench.py $WT` | 72,674 Pinnacle t60 legs | 9,305 (12.80%) flip bench; fit-starter to served-bench 6,413, reverse 2,892; mean abs dCAL 0.00178, 0.0139 on flipped legs, max 0.196; signed under-lean +0.00039, over-lean -0.00030; Oct-Nov 11.4% | finding M1 |
| A1b | fallback rows in the live legs (label-free) | `$PY -W ignore $S/a1b_fallback.py $WT` | 1,787 legs | 74 (4.1%) are recency-fallback rows (`p_ge_full.n` 10,000, `fallback_reason`/`routed_to` set), never in the map's fit population (pts 26, reb 20, ast 13, fg3m 15) | finding M2 |
| A2 | label-free shift: RAW, CAL, Pinnacle mean P(over), fit legs vs live | `$PY -W ignore $S/a2_shift.py $WT` | 72,674 / 1,787 | fit: RAW 0.4326, CAL 0.4746, Pin 0.4903; live: 0.4131, 0.4659, 0.4848. CAL-Pin fit -0.017 (Oct-Nov -0.017), live -0.019. **ast RAW-Pin live -0.085 vs fit -0.047** (the others are within 0.016) | info (m9) |
| A3 | timing: forecast vs line vs tip | `$PY -W ignore $S/a3_timing.py $WT`; `$PY -W ignore $S/a3b_age.py $WT` | 1,787 legs, 57 games | `live_book_odds.tipoff` = schedule UTC tip on all 57 games (diff 0.0 min); 0 post-tip lines (min lead 55.3); 0 post-tip forecasts (min forecast lead 9.9 min); **the 0.2% = 3 legs: one player, game 0022600126 (ast/pts/reb), forecast made at T-9.9 min, after the t60 line at T-63.8**; one scored made_at per key; forecast age is not staleness (`append_predictions` is write-on-change, so a morning made_at means no later run changed it) | yes (m5 wording) |
| A4 | can the look be forced early | `$PY -W ignore $S/a4_look.py $WT` (synthetic counts) | 168 games / 5,208 legs by 11-22 | real clock 11-23: `not_reached`; `--today 2027-02-01`: `Look(kind='backstop', date=2027-01-31, games=168)` leads to `verdict('PARTIAL', 168, 'backstop')` = `PARTIAL`; settlement check skipped (`kind != 'look'`) | **fails: B1** |
| A5 | settlement vs schedule, 10-20..10-28 (label-free) | inline query (forward_scores distinct games per date vs `live_tips_2026-27.parquet`) | 68 games | every scheduled game has rows (3/11/2/12/8/7/9/4/12); version `ctxres-v1` only (9,592 rows); schedule file has 1,200 games, no status column | yes, but see B2 |
| A6 | code drift since the OOF build (`model_rows_*` at git a2e7609, 2026-10-10 09:59) | `git diff --stat a2e7609 HEAD -- nba/props nba/features configs/context_residual.yaml nba/daily/predict.py` + read diffs | 4 relevant files | context_residual: `max_season` plumbing only (the OOF already used `tip_source="real"`, line 101-103 at a2e7609); forward.py: P(play) Platt off (p_play fields only; eligibility still on raw), grids199 persisted; injury_report: default `real` (context path already real); baselines: blk/stl/combo std only. Replay oracle IDENTICAL 89889f6d on every merge since 10-11. **No code drift found in the conditional prop path** | yes (M3) |
| R1 | status/dryrun never read an outcome (code path, not the test) | read `cmd_status` to `_load_window` to `gate.load_legs`; `cmd_dryrun` to `legs_frame`, `synth_outcomes` | n/a | `load_legs` selects game_id, game_date, target, player_id, made_at, prediction; box query reads `minutes` of games `< until` used only as priors (`d < r[1]`), never emitted; no call to `attach_outcomes`; `status = 'scored'` conditions on played (as the rule does). Verified. The test (`test_status_and_gate_loader_never_read_an_outcome`) is a source-string check only (m6) | yes |
| R2 | CAL as served = CAL in harness | read `top_source.py:175-197, 446-514`, `export/line_cal_fields.py:117-132`, `top_history.py:70` | n/a | every served path calls `CalMap.apply(book, stat, top_source.p_over(...), line, q50, (q75-q25)/1.349, starter_rate)` with `top_source.quartiles` on the 19-point `q_grid`, the harness's exact inputs. The card serves the DK cell; the parlay panel serves the Pinnacle cell at the newest line at or before `made_at` (formula identical, line differs: morning vs t60) | yes, but M1 |
| R3 | rule hash, priors | `git show 15a99f5:docs/prereg/LINE_CAL_LIVE.md \| awk ... \| shasum -a 256` = 479b111a; `git show f7d271f`, `git show 4313513` | n/a | priors sum to 1.00 and reference draft 479b111a; Devin's defaults row matches section 11 | yes (m8) |

The six adversary checks, mapped to a pre-freeze review with no outcome:
1. *Shuffled labels*: n/a, nothing is fitted. The dryrun `no_transport` world (truth = RAW) is the null control and returns
   NOT_TRANSPORTED.
2. *Planted future*: no post-tip line, no post-tip forecast (A3). The harness ignores lines at or after tip
   (`x[7] < x[9]`), and the store refuses `made_at >= tipoff`. The lag-a-day probe does not apply (no features are built).
3. *Missingness*: 4,241 of 6,028 rows drop for "no Pinnacle t60 line". That depends on Pinnacle's listing, which is set before tip,
   so it is not a leak. It is a population restriction to players Pinnacle prices. 0 missing quartiles or starter_rate. The bench
   input is a definitional mismatch, not missingness (M1).
4. *Knockout*: n/a (no feature groups).
5. *Replication*: the bootstrap is seeded (0 game, 1 date) and the dryrun is deterministic. The post-result attack is pre-declared
   in section 10 (second seed, top-five player-seasons dropped, team clustering).
6. *Integer support*: x.5 lines only. RAW comes from the integer-grid `p_ge_full` counts, and both arms use the same support and
   the same legs. Pass.

## Findings (severity-ranked; each with the line and the exact fix)

### B1 (blocker, harness): a future `--today` forces a verdict on partial data
`line_cal_live_eval.py:753` accepts any `--today`. `find_look` (lines 170-171) returns `Look("backstop", BACKSTOP, g, n)` whenever
`today > BACKSTOP`, whatever the real date. `cmd_run` (line 703) then skips the settlement check because `lk.kind != "look"`. It
loads every leg up to `min(today - 1, BACKSTOP)`, which is all data so far, and `verdict()` returns a word once games >= 150 (A4:
168 games on 11-23 gives "PARTIAL"). An early verdict, from one mistyped flag, written to `results.json` with an official-looking
"backstop" label.
**Fix:** in `main()`, for `cmd == "run"`, refuse unless `a.today == date.today()` (or drop `--today` from `run`; keep it for
`status` and `dryrun`). In `cmd_run`, also require `datetime.now(ET).date() > lk.date` for both kinds, and apply the settlement
check to the backstop date too (`if unsettled_games(root, lk.date): refuse`, for any kind). Add a test: `cmd_run(..., today=date(2027,
2, 1))` on a fixture whose real clock is earlier must raise.

### B2 (blocker, harness and text): settlement check deadlocks on a postponed or unforecast game, and checks only one date
`unsettled_games` (lines 175-190) counts scheduled games on the look date that have no `forward_scores` row. A postponed game, or a
game production never forecast (a pretip outage), never gets a row. `find_look` keeps returning the same `look` date (it returns
before the backstop branch), so `run` refuses forever, including after 2027-01-31. Late-settling games on earlier window dates are
not checked at all: they silently drop from the scored set, and the result depends on when `run` is invoked.
**Fix:** define "settled" in the text (section 2.5) and the code. For every date in `[start, look]`, a game that has a final score
(`games.home_pts > 0 AND away_pts > 0`) and production `forward_predictions` rows must have `forward_scores` rows (else refuse:
settle pending). A scheduled game with no final score 36 h after its tip is listed in `results.json` as `unplayed_or_postponed` and
does not block. A game with a final score but no production prediction is listed as `unforecast` and does not block. Add tests for
both.

### B3 (blocker, rule integrity): the operational rule (harness and the served functions) is not pinned to the freeze
The freeze pins only the text above the marker. `cmd_run` records `harness_sha256` but never compares it with anything. RAW and CAL
depend on `nba/parlay/top_source.py` (`p_over`, `quartiles`), `nba/parlay/line_cal.py`, `nba/forward/scorecard.py:_main_point`, and
the gate's `load_legs`. The CALIBRATION_EVERYWHERE wiring branch, the one TRANSPORTS would trigger, is expected to touch exactly these
files before the look. Section 13 still says the harness is "not yet written".
**Fix:** (a) section 13: name the harness, gate and test files and the four dependencies, and state "the freeze commit carries them
at their final bytes; a change to any of them before the look is a new rule id, or a logged amendment that leaves RAW and CAL
byte-identical on the pre-freeze legs". (b) `cmd_run`: `git diff --quiet <freeze_commit> -- research/eval/line_cal_live_eval.py
research/eval/line_cal_live_gate.py nba/parlay/line_cal.py nba/parlay/line_cal_map_v1.json nba/parlay/top_source.py
nba/forward/scorecard.py`, and refuse on a non-zero exit (print the paths). Record each file's sha256 in `results.json`.

### M1 (major, text and interpretation): the bench term is served on a different statistic than it was fit on
The map's design is `bench = starter10 < 0.5` (`line_cal.py:4`, `LINE_CALIBRATION.md:28`). `starter10` is the exponentially weighted
(half-life 10) starter share over **played** games (`context_residual.py:240-262, 492`). Every served path, and this harness
(`line_cal_live_gate.py:255-259`), passes `pred["starter_rate"]`. That is `minutes.starter_rate_prior`: the plain mean of `started`
over the previous **150 box rows, DNP rows included** (`minutes.py:218-220`), with NULL set to 0.0 (`forward.py:483`).
`checkpoint.py:961` already calls it "proxy for starter10". On the map's own 72,674 Pinnacle fit legs the two disagree on 12.80%,
mostly fit-starter to served-bench (6,413), so the served bench group is 23% larger than the fitted one. The mean CAL shift is small
(0.0018; 0.014 on the flipped legs; max 0.196), so the effect on P1 is second order (about dCAL^2 on 13% of legs, under 1e-4 Brier).
But P6 and the S6 bench slice are defined on a group the map's bench coefficients were never fit on. The export review
(2026-10-21) missed this, and the card and panel carry it today.
Section 0 ("bench = starter rate < 0.5") and section 8.3 ("as-of inputs") describe the served input as if it were the fitted one.
**Fix (safe default, keeps the question):** section 0 row 1: "bench = starter10 < 0.5 in the fit; served (and scored here) with the
stored `starter_rate` (150-row starter share incl. DNP rows; NULL -> 0), which disagrees with starter10 on 12.8% of the 2023-24
Pinnacle legs (mean abs dCAL 0.0018)". Section 2.2: append "with the served `starter_rate`, not the fitted starter10 (section 8.3)".
Section 8.3: add the mismatch as a known serving deviation. Section 10: "a NOT_TRANSPORTED or a bench-slice failure is read as
'the served map does not hold'. This test cannot separate season drift from the serving deviation".
**Devin's option** (needs his go; live path): persist `starter10` as an additive prediction field, and start the window after that
merge. Then the test checks the map as fitted. Recommended only if Devin wants the card fixed anyway. It is a separate LINE_CALIBRATION
serving defect either way (drafted note below).

### M2 (major, text): 4.1% of live legs come from a model the map never saw
74 of the 1,787 legs are recency-fallback rows served under `props_context_residual` (`p_ge_full.n` = 10,000; `fallback_reason`,
`routed_to` set). The OOF fit rows are context-model rows only (199-count grids). The rule says nothing about them.
**Fix:** section 4 S6: add the slice "fallback rows (`fallback_reason` not null in the stored prediction)" and its complement.
Section 12: "fallback rows are kept (they are served)". The harness `legs_frame` gains a boolean `fallback` from the prediction JSON.

### M3 (major, text): drift and the positive control
- *Code drift* since the map's OOF rows (A6): none found in the conditional prop path, and oracle 89889f6d has been unchanged since
  10-11. State this in section 8.3 with the commit range, replacing "code drift since unmeasured".
- *Serving-path differences* are not code drift, and they are what the test actually measures: the bench input (M1), fallback rows
  (M2), daily refits on all rows before the slate (2025-26 included) against monthly-block OOF refits, and the official-roster
  source. So NOT_TRANSPORTED means the served number is not honest. It does not explain why. The text should say that.
- *Positive control "CAL on 2024 replay rows reproducing T493"*: **not a control.** The served static map was fit on every
  2023-24 leg, so an outcome score on 2024 is in-sample and passes by construction. T493's walk-forward is already the historical
  control.
- **Fix:** add **D0 (label-free bridge, optional, before the look)** to section 6: "replay N >= 10 dates of 2024 through the live
  path on a DB copy (`nba.daily.replay_season`, seasons <= 2024), and compare RAW, q50, spread, the bench flag and CAL with the
  fit-path values on the same Pinnacle legs. Pre-declared reading: median abs dCAL <= 0.005 means the serving path matches the fit
  path, and a NOT_TRANSPORTED is read as season drift; otherwise it is read as a serving mismatch. No outcome is read." The
  starter-definition part of D0 is already measured (A1) and should be quoted.

### M4 (major, harness): the secondaries carry pass words the rule forbids
`cmd_run` stores the full `evaluate()` output for `S3_draftkings` and `prefreeze_EXPOSED_no_pass` (lines 727-733), and that output
includes `checks`, `verdict_core` and `failing`. Section 11.1 says EXPOSED gets "no pass word". S3 is "label only".
**Fix:** after each secondary `evaluate`, `for k in ("checks", "verdict_core", "failing"): r.pop(k, None)`. Add a test.

### Minor
- **m1 (harness):** S3 (DraftKings) gets the Pinnacle C0 and the Pinnacle x band (`pairs_frames` and `x_band` are Pinnacle only,
  lines 196-227 and 715). Fix: give `pairs_frames` and `_pairs_x_band` a `book` argument, and pass DK for S3.
- **m2 (text vs code):** section 2.4 "from the first slate after the freeze commit" against code "dates strictly after the ET
  calendar date of the freeze commit's committer time" (`freeze_commit`, `start = fz + 1 day`). Code is conservative. Fix the text to
  match the code, and the main session merges with `--no-ff` (a rebase or squash moves the committer date).
- **m3 (text):** the status line still says "revision 0 ... 08:30", and section 13 still says the harness is "not yet written". Fix:
  "revision 1, 12:13 CDT", and list the `status` / `dryrun` / `run` commands as in the harness docstring.
- **m4 (text):** section 4 does not list the EXPOSED pre-freeze secondary. Add S10 with "no pass word". Devin said "defaults". The
  drafted default was "excluded", and the alternative was "a labelled secondary". The DECISIONS row 4313513 and section 11.1 record
  both (excluded from checks AND reported as EXPOSED). Low harm, but it is the main session's reading of "defaults". Ask in one line
  (safe default: as recorded).
- **m5 (text):** section 8.3 "forecast made before the t60 snapshot on 99.8%" should be stated exactly: "3 legs (one player, one
  game), forecast at T-9.9 min after the line at T-63.8; 0 post-tip forecasts; 0 post-tip lines; min line lead 55.3 min;
  `live_book_odds.tipoff` = scheduled tip on all 57 games". Keep them (served).
- **m6 (test):** the no-outcome test is a source-string check. Add a dynamic test: a fixture DB whose `forward_scores.y` is NULL (or
  a sentinel) gives identical `status` output.
- **m7 (harness):** the lead check (gate line 247) runs before the whole-number and input checks, so the 5% stop denominator is
  "legs with a t60 main line", not "otherwise eligible". Either reorder or reword section 10.
- **m8 (ledger):** the priors (f7d271f) cite draft 479b111a. The substance carries over (the defaults are the drafted bold
  options). Add "carried unchanged to <freeze sha>" in the freeze commit's ledger line. P(INSUFFICIENT_DATA) 0.08 reads as
  pipeline-failure risk, since the backstop would hold about 500 games; fine as stated.
- **m9 (information, no action):** label-free, the ast cell already sits further from Pinnacle live (RAW-Pin -0.085, n 294) than
  in the fit (-0.047). If any stat fails P5, it is most likely ast. Recorded as a prior, not a rule change.

### Verified and fine
- Side by the model's own probability, fixed by RAW, is the same partition for both arms. The scorecard's mean-vs-line split is
  only the S9 bridge (scorecard.py:474 `lean_side(mean, line)`, `p_side_from_over`). Item (6) is handled.
- 19- vs 199-point quartiles: harness CAL equals served CAL by construction (same functions). The 0.00022 gap is live 19 vs live
  199. The fit used block-recovered 199-grid quantiles, pinned by LINE_CALIBRATION's tests. Accepted.
- Bench and every other input are known before tip (starter_rate uses rows strictly before the game). No leak; the issue is
  definitional (M1).
- The bootstrap is paired, game-clustered, one shared multinomial matrix, seeds 0/1, B 2,000, with a date CI beside it. The
  p-value definition matches section 12. The intersection verdict needs no BH (intersection-union). BH m=4 and m=8 cover the
  secondaries actually run.
- The map sha (443743a1) and the pairs shas are refused on mismatch. The season-2026 filter is in every live query. The DNP
  handling (played only) matches the fit legs.

## Power / MDE (honest)
At 200 games the P1 half-width is about 0.0022, so se is about 0.00112. **MDE at 80% power for P1 is about -0.0031 Brier**
(binding: CI upper < 0; the -0.0015 floor binds only below about 0.0024). That is 37% of the exact-transport effect (-0.0085), so
the test detects transport down to about lam 0.3 on the gate's synthetic family. Power 0.95 at lam 0.25 and 0.15 at lam 0.4 are
as stated. The over-lean side is under-powered for a gain by design (P3 asks only "no significant harm"). The P2 gap's se is
about 0.007 against the 0.03 bar, so a true gap of 0.03 fails half the time. The power family assumes truth on the CAL-RAW path;
side-specific failure is exercised only by the dryrun `under_gap` world. The draft's MDE claims are honest; add the -0.0031 figure
to section 5.

## Playbook check (quantile and distribution layers; recalibration at the line)
1. *Information or capacity:* neither; it is a restatement of production's information at the line. Correctly declared.
2. *Enough data for the class:* nothing is fit. Per-slice volume at the look is roughly bench-over 190 legs and fg3m-over 210, thin
   for the reliability bins but enough for "no significant harm".
3. *Test validity:* valid for "is the served number honest at Pinnacle's t60 line". It does not test "the map as fitted", because
   of the bench-input mismatch (M1) and the fallback rows (M2). An invalid reading of NOT_TRANSPORTED as "season drift" must be
   prevented by text (M3). Lines and forecasts are pre-tip (A3).
4. *Lineage:* CALIBRATED_GRID, then LINE_CALIBRATION (T493), then this check. Guards carried as listed. Dropping the shuffled and
   planted probes is justified (no fit). Inherited without notice: the serving deviation of the bench term.
5. *Scored against:* Brier at the market line, log loss as agreement, Pinnacle as reference. Matches the playbook's "reliability at
   the point that matters" (S7 at the line, slices fixed in advance).

## Single most likely failure mode if a TRANSPORTS later proves wrong
The served map differs from the fitted one, through the bench input (12.8% of legs) and fallback rows (4.1%), and the RAW
distribution is moving within the season (ast is already 4 pp further from the market). A pooled Brier gain can then come from
location shrinkage alone, while the bench or ast cell stays mis-stated. P4-P6 and the S6 slices are the guard. Keep them
intersected.

## What I could not verify
- How large the serving-path difference is beyond the bench input (refit cadence, training window, roster source). That needs the D0
  replay, which is a multi-minute job and was not run (watchdog).
- Whether pretip runs failed near tip on any window date. If they did, the "latest made_at" would be stale; forecast age alone
  does not show it, because writes are on change only.
- starter10 on the live legs. Computing it needs 2025-26 box rows (holdout season), so it was not computed.
- The pairs `p_model` equalling the live `p_over` definition was taken from LINE_CALIBRATION's pinned tests, not re-derived.

## Drafted rows (not appended; the main session acts)
- **DECISIONS (draft):** `| 2026-10-29 | LINE_CAL_LIVE pre-freeze review: AMEND (B1 future --today forces a backstop verdict; B2
  settlement deadlock and single-date check; B3 harness and served functions not pinned; M1 bench term served on starter_rate, fit on
  starter10, 12.8% of 2023-24 Pinnacle legs disagree; M2 4.1% fallback legs; M3 drift reading and D0 bridge; M4 pass words on
  secondaries). Safe default on M1: freeze testing the served definition, disclosed; Devin may instead persist starter10 first. |
  Follows the adversary review. | A re-review finding the edits incomplete. |`
- **TEST_LEDGER / known-defects note (draft, LINE_CALIBRATION serving):** `LINE_CALIBRATION served inputs: bench term served on
  forward starter_rate (150-row starter share incl. DNP, NULL->0) instead of the fitted starter10 (EW hl-10, played games); 12.80%
  of 72,674 2023-24 Pinnacle legs flip bench; mean abs dCAL 0.0018, max 0.196; card, panel, export affected; label-free, no
  outcome. Devin's call whether to fix (live path).`

## Scratch files (not in the repo)
`<scratchpad>/attack/a1_bench.py`, `a1b_fallback.py`, `a2_shift.py`, `a3_timing.py`, `a3b_age.py`, `a4_look.py`,
`<scratchpad>/dryrun.out`.
