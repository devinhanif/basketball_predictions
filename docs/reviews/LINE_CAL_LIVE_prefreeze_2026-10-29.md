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

## Re-review of revision 1 (adversary), 2026-10-29 12:42 CDT (clock read by `date`)

Verdict: BLOCKED

**Pre-freeze outcome: AMEND (small).** Every listed fix (B1-B3, M1-M4, m1-m9, the MDE) is applied and verified. Two new findings
come from the B3 fix itself. My own B3 specification was too coarse, so part of the defect is mine. Both sit in files that are
pinned at the freeze and cannot be changed afterwards without a new id, so they are fixed now or never. Neither touches an outcome,
the question, or a threshold. After N1 and N2 a third read of the diff only is enough. I expect READY_TO_FREEZE.

- Object: rule text **d7ad926c** (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CAL_LIVE.md | shasum -a 256` gives
  `d7ad926c32b015c8a2a2a1fa0e617e8def17a0b5f63616f1dbcfb91187b5f413`), branch `agent/modeler-props-recal` HEAD f963dbf.
- Model class: quantile and distribution layers, recalibration at the line (forward transport check of a frozen map; nothing fitted).
- Data touched: season 2026 label-free (forecasts, lines, schedule, `games` final flags, `forward_scores` existence only); 2023-24
  pairs (C0 and the x band for both books). **Season 2025 never loaded. No 2026 outcome (y, brier, crps, log_loss) read or attached to
  any CAL or RAW leg.** The real DBs were opened `read_only=True` by the harness. The only write connections were to scratch fixture
  DBs I created under `<scratchpad>/attack/fx_b2/`.

### Checks re-run (worktree; `PY=/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, `S=<scratchpad>/attack`; nothing piped)

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| V1 | tests | `PYTHONPATH=. $PY -m pytest -q --no-cov tests/eval/test_line_cal_live_eval.py tests/eval/test_line_cal_live_gate.py -p no:cacheprovider` | 29 tests | 29 passed, exit 0 | yes |
| V2 | lint, format, types | `$PY -m ruff check <3 files>`; `$PY -m ruff format --check <3 files>`; `PYTHONPATH=. $PY -m mypy research/eval/line_cal_live_eval.py research/eval/line_cal_live_gate.py` | 3 files | exit 0 / 0 / 0 | yes |
| V3 | dryrun | `PYTHONPATH=. $PY -W ignore -m research.eval.line_cal_live_eval dryrun --data-root /Users/devin/Downloads/nba-prediction` | 1,787 real legs | TRANSPORTS / NOT_TRANSPORTED / PARTIAL each equal to the expected world; `dryrun OK`, exit 0 | yes |
| V4 | status (label-free) | `... status --data-root ... --assume-freeze 2026-10-29` | 6,028 scored rows | prefreeze 57 games / 1,787 legs; window 0; not_reached; exit 0 (unchanged) | yes |
| A4' | **B1** future clock | `$PY -W ignore $S/rr_b1b2.py $WT $S/fx_b2` | n/a | `main(["run","--sha",…,"--today","2027-02-01"])` gives 2, and `--assume-freeze` gives 2 ("run takes no --today or --assume-freeze"). `cmd_run(..., date(2027,2,1))` raises "run uses the real clock only". Real-clock `find_look` on 168 games by 11-22 is not_reached. In code, `today > lk.date` is required and the settlement check runs for both kinds (lines 818-827) | **yes** |
| A7 | **B2** planted postponed / unforecast / final-unscored (scratch fixture through the real `load_game_states` + `classify_settlement`) | same script | 6 planted games | Postponed (schedule only, no `games` row) is pending before 36 h, then `unplayed_or_postponed`. A 0-0 `games` row is treated the same way. Final+predicted+unscored is `pending` (blocks). Final+unpredicted is `unforecast`. A `games`-only id missing from the schedule file is still classified. **At 2027-02-05 with only the postponed and unforecast games left: pending [], blocks False: no deadlock** | **yes** |
| A8 | B2 on real data (label-free) | same script, 2026-10-20..10-28 | 68 games | settled 68, pending 0, postponed 0, unforecast 0 | yes |
| A9 | **B3** does the pin check cover the code that runs? | `$PY -W ignore $S/rr_b3.py $WT $S/fx_b3` | 6 pinned files | With `--repo` pointing at a clean repo whose committed `top_source.py` differs from the executing one: `pinned_diff` gives `[]` (run proceeds). `pinned_sha256` records the repo's file (9eedf922…), not the executed one (aa248eb4…). The run-once guard is also `repo / OUT_REL` (line 828) | **fails: N2** |
| A10 | B3 liveness: how often do pinned live-path files change? | `git log --all --since=2026-09-29 -- <file>` | 4 files | `top_source.py` 5 commits (10-15..10-21), `scorecard.py` 3 (10-20..10-25), `line_cal.py` and map 1 each. None of the 2026-10-21 `top_source` commits touched `p_over` / `quartiles` | **finding N1** |
| A11 | pin closure | read the imports of `top_source.p_over`, `quartiles`, `scorecard._main_point`, `american_to_prob` | n/a | self-contained in the pinned files (`N_Q`, `Q_IDX`, `lc`). The gate uses only `ts.p_over`, `ts.quartiles`, `_main_point`, `MARKET_STAT` | yes |
| A12 | **m1** DK inputs | inline (`ev.pairs_frames(R, b)`, `c0_rates`, `x_band`) | 72,674 Pin / 106,999 DK legs, 2023-24 only | DK C0 pts 0.4881 / reb 0.4732 / ast 0.4815 / fg3m 0.4738 (Pin 0.4864 / 0.4657 / 0.4815 / 0.4641); DK band distinct (fg3m hi 1.039 vs 0.840). DK legs use the DK map cell (gate line 262 `cmap.apply(book, …)`) | yes |
| A13 | **M2** fallback flag | inline `gate.load_legs(…, 2026-10-20, 2026-10-28, "pinnacle")`; `$PY -W ignore $S/a1b_fallback.py $WT` | 1,787 legs | 74 by `fallback_reason`, matching 74 by the non-199 grid; pts 26 / reb 20 / ast 13 / fg3m 15, identical to A1b | yes |
| A14 | **M4** pass words | read `evaluate` (lines 543-552, 572-580) | n/a | pass words exist only at the top level (`checks`, `verdict_core`, `failing`); `bh_q` is descriptive. `strip_pass_words` on S3 and S10 (lines 859-868) | yes |

### Item-by-item
- **B1 fixed.** `main` returns 2 for `run` with `--today` or `--assume-freeze` (lines 910-912). `cmd_run` refuses `today != real_today()`.
  `today > lk.date` holds for both kinds. Text 2.5 matches.
- **B2 fixed.** "Settled" is defined in 2.5 and in code over every game in `[start, D]` (schedule plus `games` union). Postponed and
  unforecast games are listed in `results.json["settlement"]` and do not block. Final-but-unscored refuses. The backstop gets the same
  treatment. One residual (N4, minor) below.
- **B3 partly fixed.** Section 13 names the pins and `cmd_run` refuses a diff (lines 806-810). But the check reads `--repo`, not the
  executing modules (N2), and the whole-file pin on two hot live-path files makes the run likely to refuse with no executable amendment
  path (N1).
- **M1 fixed.** The served-bench disclosure, with 12.80% / 72,674 / 6,413 / 2,892 / about 23% / 0.0018 / 0.014 / 0.196, appears in
  sections 0, 2.2, 8.3 (a), 10 (Reading), 12 (Starter) and 9 (NOT_TRANSPORTED).
- **M2 fixed.** S6 `fallback_rows` / `context_model_rows` are in text and code; section 12 says "kept (they are served)"; counts reproduce (A13).
- **M3 fixed.** D0 is optional, label-free and before the look only, with the 0.005 reading. "If D0 is not run, NOT_TRANSPORTED is not
  attributed to either cause" agrees with sections 9 and 10. The in-sample 2024 score is explicitly **not** a positive control, and T493's
  walk-forward is named as the historical control. No other place in the text claims a positive control (grep: only line 169).
- **M4 fixed** (A14; test `test_secondaries_carry_no_pass_word`).
- **m1** fixed (A12). **m2** fixed (2.4 says strictly after the committer-date ET day; `--no-ff` stated). **m3** fixed (status line rev 1
  12:32; section 13 commands). **m4** fixed (S10 EXPOSED, no check, no word). **m5** fixed (section 5 row and 8.3, exact). **m6** fixed:
  a dynamic test, with status identical for y NULL vs y 12.0 and sentinel scores. **m7** fixed: the denominator is legs with a two-sided
  main line (`with_line`, lines 833-836, keys match gate lines 251-260), and section 10 is reworded to match. **m8** still a
  freeze-time action for the main session (ledger line "priors carried unchanged from 479b111a to <freeze sha>"). **m9** recorded in
  section 7. **MDE** -0.0031 is in section 5, with the P2 half-fail note.
- **Contradiction scan of the revision:** sections 2.5, 3, 9, 10, 12 and 13 agree on the clock, settlement, the served-bench reading and
  pins, except for the N1 sentence. The docstring (line 19) still says the pairs are "Pinnacle legs" only, though DK is now read for S3 (nit).

### New findings (each with the line and the exact fix)

**N1 (major, text section 13 line 267, and the operational path): the whole-file pin on hot live-path files will very likely stop the look,
and the amendment path the text offers cannot be executed.** `top_source.py` and `scorecard.py` took 8 commits in the 10 days after
10-15 (A10). The window runs about five weeks (to about 12-01). Any routine edit on main (a loader, a note, the CALIBRATION_EVERYWHERE
accessor) makes `pinned_diff` non-empty, and `run` refuses. The text then says "a new rule id, or a logged amendment that leaves RAW and
CAL byte-identical on the pre-freeze legs". But the code has no way to accept an amendment: honouring one means editing the harness,
which is itself pinned, so in practice every route is a new id and a restarted window. The protection the pin exists for still holds:
the look must compute RAW and CAL with the frozen code. That is achieved by running from the frozen code, not by forbidding main to move.
**Fix (safe default, text only):** replace the sentence at line 267 ("A change to any pinned file before the look is a new rule id, or a
logged amendment that leaves RAW and CAL byte-identical on the pre-freeze legs.") with: "`run` executes from a checkout of the freeze
commit (`git worktree add <dir> <freeze commit>`; `--data-root` the main checkout), so a later edit on main to a pinned file cannot
change the look's computation and does not stop it. The results list every commit on main since the freeze that touches a pinned file.
If one changes `p_over`, `quartiles`, `_main_point` or `CalMap.apply`, the served number differs from the scored one and the results say
so (a serving difference, descriptive)." Optional harness line (recommended, since the harness is pinned):
`res["main_commits_touching_pinned"] = _git(root, "log", "--format=%h %cI %s", f"{commit}..HEAD", "--", *PINNED)` (wrapped to record
"unknown" if `root` is not a git checkout). Alternative (more code): pin function sources (`inspect.getsource` of `p_over`,
`quartiles`, `_main_point`, `american_to_prob`, plus `N_Q`, `Q_IDX`, `MARKET_STAT`) instead of whole live-path files.

**N2 (major, harness lines 806, 828, 850, 887): the pin check, the recorded sha256s and the run-once guard read `--repo`, not the code
that executes.** A9: `run --repo <a clean checkout>` from a tree whose `top_source.py` differs passes `pinned_diff` and records the
clean file's sha256 (9eedf922…) while executing another (aa248eb4…). Under N1's checkout-of-the-freeze procedure this is the natural
mistake: running from the wrong directory with `--repo` set to the right one. A fresh checkout also has no `results.json`, so
`repo / OUT_REL` does not stop a second look. **Fix:** in `cmd_run`, before `pinned_diff`: for each `(rel, f)` in
`(("research/eval/line_cal_live_eval.py", __file__), ("research/eval/line_cal_live_gate.py", gate.__file__), ("nba/parlay/line_cal.py",
lc.__file__), ("nba/parlay/line_cal_map_v1.json", lc.MAP_PATH), ("nba/parlay/top_source.py", gate.ts.__file__),
("nba/forward/scorecard.py", sys.modules[gate._main_point.__module__].__file__))`, compare
`hashlib.sha256(Path(f).read_bytes())` with the sha256 of `subprocess.run(["git","-C",str(repo),"show",f"{commit}:{rel}"],
capture_output=True, check=True).stdout`. Refuse on any mismatch, and record the executing-file shas as `pinned_sha256`. Make the
run-once guard refuse if `repo / OUT_REL` **or** `root / OUT_REL` exists. Add a test: a clean tmp repo as `--repo` with a differing
committed pinned file must raise (template `<scratchpad>/attack/rr_b3.py`).

**N3 (minor, text section 13): no holdout-log row before the look.** Season 2026 becomes the designated holdout at 20 games (CLAUDE.md;
the rollover is a standing TODO, HOLDOUT_ACCESS_LOG lines 26-28), and `run` reads 2026 outcomes once. **Fix:** add to section 13: "Before
`run`, the main session logs a HOLDOUT_ACCESS_LOG row (rule sha, the one look, season 2026 outcome read), whether or not the rollover
has been done."

**N4 (minor, harness line 248): an ingest outage longer than 36 h reads as a postponement.** `final` comes only from `games` scores. If
the box and score ingest both stall past 36 h, played games are listed `unplayed_or_postponed` and their legs drop. The missing games are
not selected on outcome (an outage does not depend on results), so this costs n, not bias. The list is visible in `results.json`.
**Fix (optional; cheap now, impossible after the freeze):** treat a game as final if `games` has scores **or** `player_game_stats` has
rows for it (a row count, no values). Also refuse if any window date has scheduled games but no final game at all (a whole-night
outage, not a postponement).

**N5 (minor, tests): the settlement wiring is untested in the repo.** `classify_settlement` is tested; `load_game_states` (the
schedule/`games` union, the 0-0 row, the games-only id) and the backstop path through `cmd_run` are not. **Fix:** port
`<scratchpad>/attack/rr_b1b2.py`'s fixture into `tests/eval/test_line_cal_live_eval.py` (expected outputs above, A7).

**Nit:** docstring line 19: "the 2023-24 Pinnacle legs (pinned pairs frames) are read for C0 and the x band" becomes "the 2023-24
Pinnacle and DraftKings t60 legs (pinned pairs frames) are read for C0 and the x band (DraftKings for S3 only)".

### Family, power, MDE (unchanged)
Primary verdict is an intersection (P1-P6; intersection-union, no BH needed). BH m = 4 (per stat) and m = 8 (side x stat) are the
secondaries actually run. S3 and S10 now carry no pass word. MDE for P1 at 200 games is about -0.0031 Brier (se about 0.00112), 37% of the
exact-transport effect. The P2 gap se is about 0.007 against the 0.03 bar. No result exists. Nothing here is a claim.

### Playbook check (quantile and distribution layers; recalibration at the line)
1. *Information or capacity:* neither; it restates production's information at the line, correctly declared.
2. *Enough data:* nothing is fit; the look at 200 games / 5,000 legs, as before.
3. *Test validity:* valid for "the map as served at Pinnacle t60", now disclosed as such (M1-M3). Still open: the look must run the
   frozen computation without being stopped by unrelated edits (N1), and the guard must check the code that runs (N2).
4. *Lineage and inherited guards:* CALIBRATED_GRID, then T493, then this check. Map sha refusal, season refusal and the clustered
   bootstrap are carried. Serving deviations are now inherited with notice.
5. *Scored against:* Brier at the line, log loss as agreement, Pinnacle and C0 as references, as before.

### Single most likely failure mode now
Not a leak. **The look dies on an unrelated edit to `top_source.py` or `scorecard.py`, and the window is restarted under a new id**
(N1). Or it is run from the wrong tree and silently scores unfrozen code (N2). Both are fixed now at little cost; after the freeze,
neither can be fixed.

### What was not verified
- Whether production will actually score a game that is final but late (a scoring backfill path exists in principle; not exercised).
  If one game never scores, `run` refuses indefinitely. That is the intended behaviour (a defect to repair, not skip), but it can delay
  the look.
- Whether the schedule file is refreshed with new dates for rescheduled games (it has no status column; a rescheduled id is classified
  by its schedule date).
- D0 (still optional, not run).

### Drafted rows (not appended; the main session acts)
- **DECISIONS (draft):** `| 2026-10-29 | LINE_CAL_LIVE re-review of revision 1 (d7ad926c): AMEND (small). B1, B2, M1-M4, m1-m7, m9 and
  the MDE verified fixed (29 tests, dryrun OK, planted postponed game does not deadlock, future --today refused). New: N1 whole-file pins
  on top_source.py / scorecard.py would likely stop the look, so run from a checkout of the freeze commit; N2 pin check and run-once guard
  read --repo, not the executing code. Minors N3 holdout-log row, N4 ingest-outage, N5 settlement tests. | Follows the adversary
  re-review. | A third read finding N1/N2 not applied. |`
- **TEST_LEDGER:** none (no result).

### Scratch files (not in the repo)
`<scratchpad>/attack/rr_b1b2.py`, `rr_b3.py`, `rr_dryrun.out`, `rr_status.out`, `rule_d7ad.md`, fixtures `fx_b2/`, `fx_b3/`.

## Third read of revision 2 (adversary, diff only), 2026-10-29 12:54 CDT (clock read by `date`)

Verdict: APPROVED

**Pre-freeze outcome: READY_TO_FREEZE.** N1, N2, N3, N5 and the docstring nit are applied and verified. N4 is applied, but it does
less than its new text sentence claims. That is partly my fault: my N4 fix assumed box rows arrive on their own, and they do not
(O1 below). O1 is a text-only nit. It can go into the freeze commit verbatim, with no further read, or it can be left out. It does
not touch an outcome, a threshold or the question. Nothing found would make a result wrong.

- Object: rule text **313a044d** (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CAL_LIVE.md | shasum -a 256` gives
  `313a044d88fc6a6a5867548b27b1b75770bb1d14866244cfeeb30e9b3c15b66d`), branch `agent/modeler-props-recal` HEAD 36cc342. The diff
  f963dbf..36cc342 covers 4 files: the rule, the harness, the eval tests and this review. Nothing else changed: the gate, `nba/` and
  `configs/` are untouched (no scope creep).
- Model class: quantile and distribution layers, recalibration at the line (a forward transport check of a frozen map; nothing fitted).
- Data touched: season 2026 label-free only. That means legs, counts, `games` score presence, and `player_game_stats` row existence per
  game id (`count(*)` and `DISTINCT game_id`, no stat value). **Season 2025 never loaded. No 2026 outcome (y, brier, crps, log_loss)
  read or attached to any CAL or RAW leg.** Every real DuckDB connection was `read_only=True`. Write connections went only to scratch
  fixture DBs under `<scratchpad>/attack/fx_r3b2/`. The scratch git repos (`fx_r3b3/`) are throwaway `git init` fixtures, the same
  pattern the repo tests use. The project repo was not committed to.

### Checks (worktree; `PY=/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, `S=<scratchpad>/attack`, `WT=<worktree>`; nothing piped)

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| T1 | tests | `PYTHONPATH=. $PY -m pytest -q --no-cov tests/eval/test_line_cal_live_eval.py tests/eval/test_line_cal_live_gate.py -p no:cacheprovider` | 34 tests | 34 passed, exit 0 | yes |
| T2 | lint, format, types | `$PY -m ruff check <harness, gate, eval tests>`; `$PY -m ruff format --check <same>`; `PYTHONPATH=. $PY -m mypy <harness> <gate>` | 3 files | exit 0 / 0 / 0 | yes |
| T3 | dryrun | `PYTHONPATH=. $PY -W ignore -m research.eval.line_cal_live_eval dryrun --data-root /Users/devin/Downloads/nba-prediction > $S/r3_dryrun.out` | 1,787 real legs, 57 games (tiled 228) | TRANSPORTS / NOT_TRANSPORTED / PARTIAL each equal to expected; `dryrun OK (0.9 s)`, exit 0 | yes |
| T4 | status (label-free) | `$PY -W ignore -m research.eval.line_cal_live_eval status --data-root /Users/devin/Downloads/nba-prediction --assume-freeze 2026-10-29` | 6,028 scored rows | prefreeze 57 / 1,787; window 0; not_reached; exit 0 (unchanged from rev 1). With `--sha 313a044d…` it refuses "the rule is not frozen", exit 1, as intended for a draft | yes |
| R1 | **N2: rr_b3 re-run through `cmd_run`**, every pinned file | `$PY -W ignore $S/r3_b3.py $WT $S/fx_r3b3` | 6 pinned files x 1 mutant each | For each file, a clean `--repo` whose freeze commit differs from the executing bytes: the old `pinned_diff` gives `[]` (it would have run). `cmd_run` now refuses with "the executing code differs from the freeze commit …: ['<that file>']". With an identical freeze, `bad = []`, 6 shas recorded, and `cmd_run` gets past both pin checks to the data load. A path missing from the freeze commit (`git show` fails) counts as a mismatch | **yes** |
| R2 | N2 run-once guard | same script | 2 cases | result present in the repo only: refused. Result present in the data root only: refused. `cmd_run` calls the guard before the window load and again just before the write, and writes to both checkouts | yes |
| R3 | N2 tests bite | `$PY -W ignore $S/r3_mut.py $WT` (monkeypatch only, no file edit) | 2 mutants | Exec check disabled: `test_run_refuses_when_the_executing_code_differs_from_the_freeze` fails. Guard reduced to repo-only: `test_run_once_guard_checks_both_checkouts` fails | yes |
| R4 | N1 look path in the section-13 shape (cwd = a checkout, no PYTHONPATH, `--repo` defaulted) | inline `$PY -c` from `$WT` | 6 modules | All 6 modules (and `MAP_PATH`) resolve to the checkout, not the editable install on main. `REPO` defaults to the checkout. `executing_vs_freeze(REPO, 36cc342)` gives `[]`; against f963dbf it gives `['research/eval/line_cal_live_eval.py']`. With `--repo` the main checkout and the branch commit, it gives `[]` (worktrees share the object DB). Nothing in the harness, gate or `line_cal` reads `repo/data` (all data via `--data-root`), so the stale tracked `data/` of a fresh worktree is not read | yes |
| R5 | N1 commit listing | inline `ev.main_commits_touching_pinned(main, …)` (git log, read-only) | 3 cases | since 0ae8aac: 0. Positive control `35447e9^..HEAD`: 8 commits (68d415c … listed with `%cI`). Non-git root: "unknown (…)" | yes |
| R6 | **B2 fixture re-run** | `$PY -W ignore $S/rr_b1b2.py $WT $S/fx_r3b2_old`, then `$S/r3_b1b2.py` (the same fixture plus an empty `player_game_stats` table) | 6 planted games; real 68 | The old fixture exits 1 with `CatalogException: player_game_stats does not exist`. This is expected: `load_game_states` now queries the table, which exists in the real DB. With the table, every output equals the A7 outputs: 11-13 gives pending [203, 205], postponed [202], unforecast [204]; 11-20 gives pending [203]; 2027-02-05 gives pending [], blocks False. B1 refusals unchanged (`--today`, `--assume-freeze` give 2; a future `today` is refused). Real 10-20..10-28: settled 68, pending 0 | **yes** |
| R7 | N4 on real data (existence only) | inline read-only SQL on `nba.duckdb`, season 2026 | 71 games | 68 with scores, 68 with box rows, box-without-score 0, score-without-box 0. The 3 unscored games are today's (10-29) | yes (see O1) |
| R8 | N4 writers of `player_game_stats` | `grep -rn player_game_stats nba --include='*.py' \| grep -i -E "insert\|create\|delete\|replace\|write"` | 4 sites | Production writes only through `boxscores.upsert_player_game_stats`, which is fed by `missing_boxscore_game_ids` (`nba/daily/ingest_step.py:21-32`: `home_pts > 0` required). The `props/forward.py:285` insert targets an in-memory scratch (`:memory:`, line 199). No pre-game placeholder rows reach production, so a postponed game cannot become "final" and deadlock the look | yes |

### Item-by-item
- **N1 fixed.** Section 13 (lines 264-274) runs from `git worktree add <dir> <freeze commit>` with `--data-root` on main. A later edit
  on main is now "a serving difference, descriptive; not a stop and not a new id". Section 10 (line 237) now reads "the executing code
  of any pinned file … differs". The command block carries the worktree step. Contradiction scan (`grep -n -i "pinned|new id|git
  diff|amendment"`): line 97 (threat table, "pinned to the freeze commit") still holds under the new meaning. No sentence still forces
  a new id for an edit on main.
- **N2 fixed** (R1-R4). `executing_files` maps each pinned path to the loaded module file (`gate.ts` and `served_top_source` are the
  same module object; `_main_point` is `served_scorecard`'s). `executing_vs_freeze` compares the executing bytes with `git show
  <commit>:<rel>` and refuses on any mismatch or a missing blob (line 863-868). `pinned_sha256` = the executing shas (line 911). The
  guard covers both roots. The leftover `pinned_diff(repo, commit)` (line 869) is redundant but harmless: it is clean in a freeze
  checkout.
- **N3 fixed** (text, section 13 lines 275-276). This is procedural; the harness does not check the log. Acceptable, because the main
  session holds the log.
- **N4 applied** (lines 260-267 of the harness; text 2.5). It is correct and harmless, but redundant in production (R7, R8). See O1.
- **N5 fixed.** `test_settlement_fixture_through_the_loader` and `test_box_rows_make_a_game_final_when_the_score_ingest_lags` port the
  A7 fixture with the same expected outputs, through the real `load_game_states`. The backstop path through `cmd_run` is still not
  driven end to end; that needs a frozen doc plus a reached look, and the classification it uses is the one tested.
- **Docstring nit fixed** (harness lines 21-22: "Pinnacle and DraftKings t60 legs … (DraftKings for S3 only)").
- **New contradictions in revision 2:** only O1.

### O1 (nit, text 2.5 lines 66-67; my N4 fix was mis-specified)
The new parenthetical "an ingest outage is not a postponement" is not true for the outage that actually happens. Production pulls
box rows only for games that already have scores (`missing_boxscore_game_ids`, `home_pts > 0`). So when the daily ingest is down, both
feeds stop together, and after 36 h the games are listed `unplayed_or_postponed` and dropped. This costs n, not bias: an outage is not
selected on outcome, and the list is in `results.json`. But the look runs once, so a run made during a multi-day outage spends the one
look on fewer games. **Fix (text only, verbatim, safe default):** in 2.5, replace "or `player_game_stats` holds rows for it: an ingest
outage is not a postponement)" with "or `player_game_stats` holds rows for it; production pulls box rows only for scored games, so an
outage of the daily ingest longer than 36 h still lists its games as `unplayed_or_postponed`)". In section 13, after the holdout-log
bullet, add: "Before `run`, the maintainer confirms the daily ingest heartbeat has been green for the last three days (a longer outage
drops games as postponed, and the look runs once)." The code-level guard (refuse if any window date with scheduled games has no
final game) would be stronger, but it changes the pinned harness and needs another read. I do not ask for it.

### Family, power, MDE (unchanged)
The primary is an intersection (P1-P6; intersection-union, no BH). BH m = 4 (per stat) and m = 8 (side x stat) over the secondaries
actually run. MDE for P1 at 200 games is about -0.0031 Brier (se about 0.00112). The P2 gap se is about 0.007 against the 0.03 bar. No
result exists; nothing here is a claim (no n or CI to report beyond the label-free counts).

### Playbook check (quantile and distribution layers; recalibration at the line)
1. *Information or capacity:* neither. It restates production's information at the line, declared as such.
2. *Enough data:* nothing is fit. The look is at 200 games / 5,000 legs (window 0 today).
3. *Test validity:* valid for "the map as served at Pinnacle t60". The look now runs the frozen computation regardless of later edits on
   main (N1), and the guard checks the bytes that execute (N2). Residual: an edit on main to `p_over` / `quartiles` / `_main_point` /
   `CalMap.apply` inside the window makes the scored number differ from what users saw. It is listed, not corrected.
4. *Lineage and inherited guards:* CALIBRATED_GRID, then T493, then this check. Map sha, season refusal, the clustered bootstrap and
   the served-bench disclosure are carried. The executing-byte pin is new and tested.
5. *Scored against:* Brier at the line, log loss as agreement, Pinnacle and C0 as references.

### Single most likely failure mode now
Not a leak and not a code-pin hole. It is operational: **the one look is run during a multi-day ingest outage and spends itself on
fewer games** (O1). The next most likely is a mid-window edit to `p_over` on main. The look then scores the frozen number, not the one
users were shown. The results list that commit, but the verdict would then describe the frozen code, not the live card.

### What was not verified
- The full `run` CLI from a real `git worktree add` of a real freeze commit. That needs a commit in the project repo, which I do not
  make. R1, R4 and R5 cover each part: from a checkout, the exec check passes on identical bytes, refuses on any drift, and `cmd_run`
  reaches the data load.
- `results.json` persistence. If the freeze worktree is removed and the main copy is not committed, the file-based run-once guard is
  lost. Recommended: the main session commits `reports/prereg_line_cal_live/results.json` in the same step as the look.
- The backstop branch of `cmd_run` end to end (see N5).
- Whether production ever back-scores a game that is final but late (unchanged from the re-review).

### Recommended (main session acts; none applied by me)
1. Freeze revision 2 (optionally with O1 verbatim) as `prereg(LINE_CAL_LIVE): <sha256>`, merged `--no-ff`. Carry the m8 ledger line.
2. At the look: commit the HOLDOUT_ACCESS_LOG row first, run from the freeze worktree, then commit the results file on main at once.

### Drafted rows (not appended; the main session acts)
- **DECISIONS (draft):** `| 2026-10-29 | LINE_CAL_LIVE third read of revision 2 (313a044d): READY_TO_FREEZE. N1 (look runs from a
  freeze worktree; edits on main are a listed serving difference), N2 (executing-byte check vs git show, shas recorded, run-once guard on
  both roots; rr_b3 refused for all 6 pinned files), N3, N5 and the docstring verified; 34 tests, dryrun OK. O1 text nit: the N4 box-row
  rule does not cover a full ingest outage (box pulls require scores); reword and check the heartbeat before run. | Follows the
  adversary third read. | A defect found at the look in the pinned harness (a new id). |`
- **TEST_LEDGER:** none (no result).

### Scratch files (not in the repo)
`<scratchpad>/attack/r3_b3.py`, `r3_b1b2.py`, `r3_mut.py`, `r3_dryrun.out`, `r3_status.out`, `r3_b1b2.out`, `r3_b1b2_old.out`,
fixtures `fx_r3b3/`, `fx_r3b2/`, `fx_r3b2_old/`.
