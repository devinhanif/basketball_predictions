# Adversary review: JARVIS halftime scorer (agent/market-jarvis-halftime-scorer e02514e on 8ee3409), 2026-10-27

Scope: the branch diff 8ee3409..e02514e (16 files): `nba/markets/halftime_{entry,model,scorer,store}.py`,
`nba/jarvis/halftime_overs.py`, `nba/jarvis/runner.py`, `nba/jarvis/config.py`, `configs/jarvis.yaml`,
`research/eval/halftime_scorer_train.py`, tests, `docs/MARKET_FEATURE.md`. Context: the DRAFT
`docs/prereg/HALFTIME_OVERS_VS_PRICE.md` (agent/modeler-halftime-overs 5a8d140), T508 and its post-result attack
(`docs/reviews/IN_GAME_LINE_MODEL_post_result_2026-10-22.md`), DECISIONS 2026-10-27 18:16.
This is a code and test-validity review; no claim with a result exists yet, so SURVIVES / WOUNDED / BROKEN does not apply. No leak was found.

Verdict: BLOCKED

(In the caller's terms: **NOT_READY**. One blocker and three majors, all small fixes. The display path is not dangerous tonight;
the paper table is not yet trustworthy as the forward log that the gate's f, and later the rule, will read.)

**Top finding.** The paper table's "stamped before the second half" guarantee can be broken with one command: `replay --db <live store>`
writes rows for a night whose games are over, stamped `made_at = max(quote, state) received_at` (historical), with
`within_prereg_window = true`, and no column tells those rows apart from live ones.

## Playbook check

Model class: two-stage gradient-boosted trees (T508 arm M), with a logistic baseline (glm_e), scored against a market price.
1. **Information or capacity.** The first-half state is information, but public: the market sees it too. Capacity is T508's, unchanged (checked in the
   artifact dump: 200 trees, 8 leaves, lr 0.05, min_data_in_leaf 200, seed 0, deterministic; stage-B monotone vector
   `0,-1,0,0,0,0,0,0,0,1,1,0,0,0,1` = need -1, pregame_mean +1, rem_min_hat +1, b0_logit +1, as `ev.MONOTONE_B`).
2. **Enough data for this class.** Training: 4.07M pts rows (630,358 at e = 24), 2019-2024; enough. The forward sample is the bottleneck (the draft's gate).
3. **Test validity.** The entry definition is implemented faithfully (counts match the modeler's label-free gate exactly on 10-26: 1,461 markets at
   snapshot, 964 eligible). Three validity holes: halftime detection trusts one ESPN tick (M1), live snapshots can freeze a partial tick (M2), and
   the strict recency window feeds the model 5-9-game means it was never trained on (M3).
4. **Lineage and inherited guards.** T508 constants, features and grid are pinned to research by tests; glm_e is kept as the binding baseline.
   It is a reconstruction (the attack's scripts were not kept). Measured below, it is at least as strong as M at e = 24.
5. **What it is scored against.** Nothing yet (paper). The draft scores P&L at ask + fee, and glm_e through the same entry rule.
Class checks (GBM): monotone ok; no tuning; seed 0; `n_jobs=1`. Train-serve parity on minutes was measured (immaterial, check 2b).
Missingness: a state row missing any field is excluded and counted before scoring (`halftime_entry.py:375-396`), never imputed.

## Checks run (all on stored 2026-27 capture with a strict 2026-27 recency window, or on season 2024; no season-2025 row read by me)

| # | check | command (exact) | n | result | pass |
|---|---|---|---|---|---|
| T | tests | `uv run pytest tests/jarvis tests/markets tests/test_layering.py -q -p no:cacheprovider` | 414 tests | 414 passed in 100.9 s, exit 0 | PASS |
| L | ruff / format / mypy on the diff | `uv run ruff check ...`; `uv run ruff format --check ...`; `uv run mypy nba/markets/halftime_*.py nba/jarvis/{halftime_overs,runner,config}.py` | 62 files / 7 modules | exit 0 / 0 / 0 | PASS |
| 6 | layering: nba never imports research | in the 414 (`test_nba_never_imports_research`) + `grep -rn research nba/markets/halftime_*.py nba/jarvis/halftime_overs.py nba/jarvis/runner.py` for import lines | - | no import (grep exit 1) | PASS |
| R1 | replay dry run, shipped settings | `uv run python -m nba.markets.halftime_scorer replay --night data/raw/live_2026-10-26 --artifact data/models/halftime_ingame/2026-10-27/artifact.json --nba-db /Users/devin/Downloads/nba-prediction/nba.duckdb --min-game-date 2026-10-01 --top 5` | 9 halftimes, 964 candidates | exit 0; stored 0 (959 `x_insufficient_history`: no 2026-27 player has 5 games yet), f null | PASS (as designed: scores nothing before ~Nov 2) |
| R2 | replay wiring demo | same, `--min-prior 2 --top 12` | 948 rows, 336 units | exit 0; `valid_for_f: false`; top edges include JJJ reb 4+ ask 38c (bid 2c), Caruso pts 10+ ask 37c | wiring only |
| 1 | write-once / no UPDATE-DELETE | read `halftime_store.py`; `test_module_has_no_update_or_delete_statement` | - | no UPDATE / DELETE / OR REPLACE / DO UPDATE; PK (event_id, ticker) + `ON CONFLICT DO NOTHING` | PASS |
| 1 | outcome into the table (any column / path) | read every column source: capture pre-window (quote, state, t_ht score), `nba.duckdb` rows with `game_date < slate`, artifact | 39 columns | none from after `t* + 180 s + 30 s`; no settlement, final or result read anywhere (`grep -i settle\|result\|outcome` = docstrings only) | PASS |
| 1 | row written after the second half starts | live path: `score_night(now=...)` on 10-26 at 00:20Z and 03:00Z; replay path: read `halftime_scorer.py:283` | 109 stored / 24 refused | live refuses on a Q3 tick that has been seen (protection lags real Q3 by up to `FEED_MAX_AGE_S` = 180 s). **Replay into a persistent `--db` writes backdated rows** (B1) | **FAIL (B1)** |
| 1 | `within_prereg_window` honest | `halftime_scorer.py:233-234` | - | live: honest (wall clock). Replay: computed from a reconstructed stamp, so it is always true (B1). `share_f` computes `in_win` and never uses it (m1) | partial |
| 1 | `share_f` count-only | `halftime_store.py:181-209` | - | counts only, no outcome; but it mixes out-of-window rows and any `--min-prior` value into f (m1) | PASS on leakage, minor on scope |
| 2a | artifact binds T508 spec and the tested formulas | dump parse of the shipped artifact; `shasum -a 256` of the 5 sources vs `meta.sources_sha256_16` | 5 boosters | spec matches; all 5 source hashes match the branch files; meta parity_max_abs_diff 0.0 on all four stats' real training rows; digest b9b53d63 verifies | PASS, with m4 (nothing pins *which* artifact) |
| 2b | minutes deviation (ESPN integer MIN vs stint minutes) | scratch `attack_minutes_glm.py`: score 2024 e = 24 rows with the shipped artifact, minutes as-is vs round / floor / ceil | 109,248 / 94,419 / 81,281 / 76,182 rows (pts/reb/ast/3PM), 1,230 games | M mean abs dp (round) 0.0009 / 0.0014 / 0.0009 / 0.0012; share abs dp > 0.05: 0.10% / 0.28% / 0.09% / 0.02% (floor: 0.17% / 0.72% / 0.14% / 0.09%); log-loss change (round) +0.00010 [+0.00004, +0.00015] / +0.00007 [-0.00002, +0.00016] / -0.00001 [-0.00008, +0.00005] / +0.00008 [+0.00001, +0.00015] | PASS (immaterial; text mismatch m2) |
| 2c | glm_e still "knows what the model knows" | same script: M - glm_e log loss, 2024 e = 24, game-clustered, 2,000 draws, seed 0 (in-sample for both: 2024 is in the artifact's training) | as 2b | pts -0.00057 [-0.00134, +0.00016]; reb **+0.00199 [+0.00110, +0.00290]** (glm_e better); ast +0.00005 [-0.00072, +0.00083]; 3PM +0.00008 [-0.00068, +0.00085] | PASS: the baseline is at least as strong as M at halftime |
| 2c' | alternative reading of the attack's K (share K = 40 as well as rate K) | same; glm refit on 2024 in-sample (favours it) | as 2b | M - alt: pts -0.00145 [-0.00215, -0.00075]; reb -0.00070 [-0.00141, +0.00004]; ast -0.00139 [-0.00206, -0.00074]; 3PM -0.00080 [-0.00143, -0.00015] | the shipped reading is the STRONGER baseline (by 0.001-0.003); the conservative choice |
| 3 | entry filters vs draft | read `halftime_entry.py` against draft s2 steps 1-9 | - | t_ht = first halftime tick; t* = +120 s; q3-before-t* exclusion; first page in [t*, t*+180] plus pages within 30 s; state <= 150 s older and HALFTIME; greater, integer strike, active; 2 <= ask_c <= 97; size >= 1; $5 depth recorded; fold match; MIN 0 / DNP / ejected / resolved / off-grid excluded; fee ceil one contract, verified + pinned (formula, 0.07, multiplier 1.0 on all 4 series) | PASS (step 1 is unsafe as written: M1) |
| 3 | halftime start detection on real capture | scratch `htgap.py`, `mincle.py` over live_2026-10-21..26 | 30 halftimes | 4 of 30 have no earlier tick (capture started mid-halftime, so t_ht is late); **10-26 MIN@CLE: ESPN sent `STATUS_HALFTIME`, period 2, clock 11:04, during Q2 (scoreboard AND summary)**. It lasted 60 s, so it was excluded (`q3_before_decision_time`), and the real halftime at 00:15:13Z was lost | **FAIL (M1)** |
| 3 | live partial tick | scratch: KXNBAPTS pages per tick on 10-26 | 492 ticks | 3 pages/tick, median span 5.0 s, max 29.7 s, cadence 60 s; a live pass inside the span freezes a partial snapshot (`store.has` skips the series after) | **FAIL (M2)** |
| 3 | spreads (label-free) | scratch `spreads.py`: all scored candidates 10-21..26, strict window, `min_prior=1` (wiring) | 2,330 rows, 790 units, 20 games, 5 dates | spread median 6c, p75 12c, p90 20c, p95 31c; 110 rows with bid 0 or none; a 10c cap keeps 724 / 790 units (92%) | recommendation below |
| 4 | Yes only | read entry / scorer / overs; `test_no_side_fields_are_read`, `test_yes_side_only_rows_have_no_other_side_fields` | - | only `yes_ask_dollars`, `yes_ask_size_fp`, `yes_bid_dollars` read; text says "(Yes)"; no No price, field or order anywhere | PASS |
| 4 | fail-soft | `runner.py:353-373`, `halftime_overs.py:58-103`; `test_scorer_exception_in_the_tick_is_contained`, `test_scorer_failure_is_fail_soft`, `test_missing_artifact_never_raises` | - | scorer, store and recency errors (including a DuckDB lock on `nba.duckdb` or on the store) are returned as ok False; the check-in loop and each game's follow-up are in separate try blocks; heavy imports are lazy. Live tick cost 0.5-0.6 s | PASS |
| 5 | holdout, shipped config | `configs/jarvis.yaml:165` `recency_min_game_date: "2026-10-01"` -> `RecencyProvider` SQL `game_date >= 2026-10-01 AND game_id LIKE '002%'`; trainer SQL `season <= 2024` | - | no season-2025 row is returned on the shipped config; `test_config_shipped_state_is_paper_strict_and_valid` pins it. Code defaults are unsafe (m6). Conflict with Devin's C3 = allow (M3) | PASS (shipped) |
| 5 | settlement / outcome read | grep and read | - | none | PASS |

## Defects (file:line, severity, smallest fix)

**B1 blocker: backdated rows via replay.** `nba/markets/halftime_scorer.py:283` (`made = ... snap.known_at`) together with `:321` (`--db`
any path) and `nba/markets/halftime_store.py:24-65,111-119` (no wall-clock column; `open` refuses only `nba.duckdb` and `data/kalshi`).
`replay --night data/raw/live_2026-10-26 --db data/jarvis/halftime_overs.duckdb` today would insert 10-26 rows stamped 10-26 evening with
`within_prereg_window = true`, after every game had ended. The draft's whole forward guarantee ("model P written with made_at <= t* + 180 s
before any settlement exists") rests on that stamp.
Smallest fix: add `written_at TIMESTAMP NOT NULL DEFAULT current_timestamp` (set by DuckDB, never by the caller) and `mode VARCHAR NOT NULL`
('live' or 'replay') to `halftime_scored`; compute `within_prereg_window` from `written_at` in live mode; `HalftimeStore.open(path, mode='replay')`
refuses the configured live store path (`configs/jarvis.yaml:158`). Add a test that replays into the live store and is refused.

**M1 major: halftime start trusts one ESPN tick.** `nba/markets/halftime_entry.py:152` (first `STATUS_HALFTIME` of any period or clock),
`:170-172` (any later non-halftime tick counts as "second half started", so a glitch kills the real halftime), `:319` (the summary check reads only the
status name). On 10-26 ESPN reported MIN@CLE as halftime during Q2 (period 2, 11:04 left), and the summary agreed. Had the glitch lasted 120 s,
the scorer would have scored a Q1 box as the e = 24 state. Separately, 4 of 30 halftimes were first seen with no earlier tick (capture started late),
so `t*` is not halftime + 120 s.
Smallest fix: the halftime tick must have `period == 2` and a game clock of zero (`displayClock in {"0.0", "0:00"}`), and an immediately earlier tick
must show the game in period 2 within 150 s (else exclude as `ht_start_unobserved`, counted). Set `q3_first` = the first later tick with `period >= 3`.
Apply the same period-and-clock test to the summary at `:319`. Write the same definition into the draft's s2 step 1 **before the freeze** (it changes the entry set).

**M2 major: a live pass can freeze a partial tick.** `nba/markets/halftime_entry.py:308-313` with `nba/markets/halftime_scorer.py:276`. The tick is
"pages within 30 s of the first in-window page", but live, `as_of = now` may land inside that span (median 5 s, max 29.7 s on 10-26). The snapshot then
holds only the pages received so far, and `store.has(eid, series)` stops it from ever being completed. Live rows then differ from a replay of the
same night, and so does f's denominator. Smallest fix: in `snapshot`, when `self.as_of is not None and self.as_of < first_ts + SAME_TICK_S`, return None
(counted `tick_not_closed`) so the next pass takes the whole tick.

**M3 major: recency window, train-serve mismatch and an unreconciled decision.** `configs/jarvis.yaml:165` ships `"2026-10-01"`. Training
recency (`ev.recency`, `halftime_scorer_train.py:83-84`) crosses seasons and almost always averages 10 prior games. The strict window gives 5-9-game
means from 2026-27 only, through roughly mid-November, which the model never saw. In the wiring demo, model-to-mid disagreement on tight books falls
with history: mean abs(p_M - mid) 0.101 / 0.069 / 0.063 at n_prior 1 / 2 / 3 (299 / 393 / 342 rows, spread <= 5c). So an early-season f measured under
the strict window will be inflated by noisy inputs, and f is the gate's input. Also, `docs/HOLDOUT_ACCESS_LOG.md` (2026-10-27) records C3 = allow
(Devin, 18:41) for the replay, while the shipped live config is strict. Main session / Devin decide. Smallest fix, either: (a) set `recency_min_game_date: null`
(matches training; the disclosure row exists, extend it to cover live runs) or (b) keep strict, and for any f used by the gate require `n_prior >= 10`
and report f by n_prior bucket. My recommendation is (a): it is what the model was trained on and what production reads daily.

**m1 minor.** `nba/markets/halftime_store.py:190-194`: `in_win` is computed and unused, so f counts out-of-window rows. The store also does not record
`min_prior`, so a `--min-prior 2` replay into a persistent `--db` would pollute f. Fix: `count(*) FILTER (in_win)` and friends; add a `min_prior`
column, or refuse a non-default `--min-prior` with a persistent `--db`.

**m2 minor, text vs code.** Draft s3 says the refit "uses stint minutes rounded to the nearest integer". `research/eval/halftime_scorer_train.py:52`
uses them unrounded. Measured immaterial (check 2b). Fix: amend the draft text to "unrounded stint minutes in training; ESPN integer MIN at serve;
measured abs dp <= 0.003 mean, <= 0.72% of rows > 0.05 (this review)". ESPN's floor-or-round convention is still to be checked before freeze, as the draft says.

**m3 minor, undisclosed deviation.** `research/eval/halftime_scorer_train.py:40-46,101`: Platt is fitted by sklearn's unregularised LBFGS, not
`rule.platt_fit` (Newton). The two agree at the MLE on the pooled fit (coefficients near (0.005-0.023, 1.00-1.03)); the divergence the T508 attack found
was per-checkpoint B0e at e = 6, not this fit. Fix: one line in the draft s3 and the artifact `meta.note`.

**m4 minor.** Nothing pins *which* artifact is served: `configs/jarvis.yaml:157` gives a path, and no test loads the shipped file. Fix: add
`artifact_digest: b9b53d636279337ab01c56f6146fc20936d354dd0a01ec1f42bc262efcb9d053` to the config, and have `score_halftimes` refuse on a mismatch
(the per-row `artifact_digest` column is already there).

**m5 minor.** `nba/markets/halftime_scorer.py:333`: replay hardcodes the fee pin instead of reading `configs/jarvis.yaml` `halftime_overs.fee_pin`. A pin
change would not reach replay. Fix: load the pin from the JARVIS config.

**m6 minor, holdout default.** `nba/markets/halftime_scorer.py:90` (`min_game_date=None`) and `:324` (CLI default None) read season-2025 rows silently
when the flag is forgotten. `MARKET_FEATURE.md`'s documented command carries the flag; the orchestrator's brief elided it. Fix: make
`--min-game-date` required (or default `2026-10-01`), and have `None` require an explicit `--allow-prior-season-features` flag naming the holdout-log row.

**m7 minor, scope.** `halftime_store.py:72-78,161-165`: the `halftime_skipped` table and `note_skip` are never called. The draft's "counted" exclusions
(`q3_before_decision_time`, `series_no_page_in_window`, and so on) live only in the return value; live, they are lost. Fix: persist per-(event, series) counts once,
or delete the dead table.

**m8 minor, text vs code.** Draft s3 says the scorer "refuses a row whose made_at is later" than t* + 180 s. The code stores it with
`within_prereg_window = false`. Either is fine; align the draft text with the code ("stored, flagged, never scored").

**Live path.** `configs/jarvis.yaml` and `nba/jarvis/runner.py` are the live path. Per CLAUDE.md the merge needs the replay oracle IDENTICAL and Devin's go.
Not verified here.

## Wide spreads: recommendation (prereg-level, before the freeze)

The primary metric buys at the ask, so a wide book does not fake the price. You really can pay 38c. Three things still go wrong on books like
ask 38c / bid 2c: (1) the draft's secondary "M - mid" uses a mid that is not a price; (2) a lone resting ask far below a pace model's value is where
adverse selection lives (someone in the building knows about the locker room); (3) after a 120 s delay plus up to 180 s of quote age, the displayed ask
is the least likely to still be there. The largest demo edges sit on such books (top 3: spreads 36c, 2c, 31c).
Recommendation: add to draft s2 step 5, before the freeze: **`bid_c >= 1` and `ask_c - bid_c <= 10`**. Chosen from the label-free spread distribution
(median 6c, p90 20c; it keeps 92% of units). Record wider books as a pre-declared descriptive slice, and restrict the secondary mid comparisons to the same rows.
It must go in the frozen text: added after the freeze it would be a new rule id. Disclosure: I saw label-free demo model edges by spread bucket on the gate
nights (no outcome, no settlement); the cap was set from spread percentiles, not from those edges. For the JARVIS line, apply the same cap now (display only,
not a rule) and print the bid beside the ask. Also print glm_e's P: at halftime glm_e is as good as M (check 2c), so Devin should see both.

## Corrected family and power note

No inferential family was run (the scorer makes no claim). The two numbers here (2b, 2c) are descriptive, in-sample checks on season 2024. Each is
game-clustered with 2,000 draws and seed 0, using `numpy.random.default_rng` in a scratch script, not `truth/bootstrap.py` (flagged: scratch-only, not a claim).
Power for 2c: half-widths about 0.0007-0.0009 per stat at n 76k-109k rows. At e = 24, M and glm_e are indistinguishable for pts / ast / 3PM, and glm_e is
better for reb. That matches the attack's "residual concentrated at e >= 30". **Implication for the draft: PASS_BASELINE is the likely ending if anything
passes; the prior in PREDICTIONS_LEDGER should say so.**

## Biggest risk

That the forward table is believed. Unfixed, B1 lets a replay manufacture "pre-second-half" rows, and M1 lets one ESPN status glitch put a Q1 box into an
"e = 24" row. Neither is a leak of the label, but both make the gate's f and the rule's entries untrustworthy in ways no downstream test would see.

## Not verified

- ESPN's MIN rounding convention (floor or round) against stint time (draft pre-freeze item). The impact bound is in 2b.
- That the attack's lost `a1.py` glm_e equals this reconstruction; only that this one is at least as strong as M at e = 24, and stronger than the other K reading.
- Out-of-sample M - glm_e at e = 24 (2b/2c are in-sample on 2024; a 2019-2023 refit would take about 3 min, under the heavy lock, and is the maintainer's run if wanted).
- The replay oracle for the live-path change; a JARVIS end-to-end tick (not run: no Discord, no launchd).
- The f measurement for the gate (the modeler's step 4; my demo f is `min_prior=1` and invalid for f by the scorer's own flag).

## Drafted rows (not appended; the main session decides)

`docs/DECISIONS.md` (draft):
`| 2026-10-27 | Halftime scorer review (docs/reviews/halftime_scorer_2026-10-27.md): NOT_READY. Fix B1 (written_at/mode, replay refused on the live store), M1 (halftime tick = period 2 at clock 0 with an observed period-2 tick before it; q3 = first period >= 3), M2 (live waits for the tick to close); decide C3 for the live config (M3); add the spread cap (bid >= 1c, spread <= 10c) to HALFTIME_OVERS_VS_PRICE s2 before freeze. | No leak found; the paper table's timing guarantee and the halftime definition are not yet robust. | Re-review after the fixes passes. |`

No ledger row: nothing was scored.

---

# Round 2: re-review of c5a6451 (fixes for B1, M1, M2, M3, spread filter, m1-m8), 2026-10-27

Scope: `git diff e02514e c5a6451` (15 files) and `docs/reviews/halftime_scorer_amendments_2026-10-27.md`. The revision was also checked for
new contradictions, not only the listed fixes. Read-only on data/ and the main checkout; no Discord, no launchd; scratch stores only
(`$SCRATCH/ht2/`). No leak found. No claim with a result exists, so SURVIVES / WOUNDED / BROKEN does not apply.

Verdict: APPROVED

(In the caller's terms: **APPROVED**, with five minors and four nits below. None of them can make a row count as forward when it is not;
two of them, m9 and m12, should be fixed before the first live night or the freeze. The live-path merge still needs the replay oracle
IDENTICAL and Devin's go. That was not verified here.)

**Top finding.** B1 is fixed where it mattered: a replay row is tagged `mode = 'replay'`, `within_prereg_window = false`, and is excluded
from live f by SQL, so no path manufactures a "pre-second-half" forward row. The path guard is still bypassable (case-variant path on
case-insensitive APFS, a hard link, or a checkout whose data/ is not the main one), but only while the live store holds no scored live row.
The effect is not backdating. The **live writer is then refused on every tick and the forward log stops silently** (m9).

## Playbook check (round 2)

Model class unchanged: two-stage GBM (T508 arm M) with logistic baseline glm_e, scored against the Kalshi Yes ask.
1. Information or capacity: unchanged; public first-half state. 2. Data: unchanged (training ample; the forward sample is the bottleneck).
3. Test validity: the three round-1 holes are closed (M1 definition, M2 closed tick, M3 window now matches training). One new text
contradiction in the amendments over where the gate's f comes from (m12). 4. Lineage: artifact now pinned by digest (b9b53d63), with a
refusal on mismatch, tested. 5. Scored against: paper only. glm_e is printed beside M on every Discord line, and `share_f` reports
glm_e and both-edge unit counts, so a PASS_BASELINE situation is visible to Devin.
Class checks (GBM): unchanged from round 1 (monotone, seed 0, no tuning).

## Checks run

| # | check | command (exact; unpiped) | n | result | pass |
|---|---|---|---|---|---|
| T | tests | `uv run pytest tests/jarvis tests/markets tests/test_layering.py -q -p no:cacheprovider` | 443 | 443 passed in 121 s; **exit 1 from the repo-wide `--cov-fail-under=70`** (7.47% on a subset run), not a test failure | see next row |
| T' | tests without the coverage floor | same `--no-cov` | 443 | 443 passed in 72 s, exit 0 | PASS |
| L | lint / format / types | `uv run ruff check <12 changed py files>`; `uv run ruff format --check <same>`; `uv run mypy nba/markets/halftime_{entry,scorer,store}.py nba/jarvis/{halftime_overs,runner,config}.py` | 12 / 12 / 6 | exit 0 / 0 / 0 | PASS |
| B1a | replay CLI into the live store path | `uv run python -m nba.markets.halftime_scorer replay --night data/raw/live_2026-10-26 --artifact data/models/halftime_ingame/2026-10-27/artifact.json --nba-db /Users/devin/Downloads/nba-prediction/nba.duckdb --min-game-date 2026-10-01 --db data/jarvis/halftime_overs.duckdb --top 1` (and the same with the absolute main path) | 2 | exit 1, `ForeignStoreError: ... is the live paper table`; `ls data/jarvis/`: no store file created | PASS |
| B1b | path-guard equivalents (predicates only on the real path; behaviour on scratch with `live_store_path` redirected) | `uv run python $SCRATCH/ht2/store_attack.py $SCRATCH/ht2/att1` | 7 variants | refused: relative, absolute, `..`, symlink. **Not refused: case variant (`data/JARVIS/Halftime_Overs.duckdb`, same inode), hard link, other checkout root**. Each succeeds only while the store has no scored row (a live pass that only skips, e.g. `tick_not_closed`, creates exactly that). After it, the live open raises `already holds rows of the other mode` | **minor m9** |
| B1c | replay rows carry mode, never in window, excluded from f | replay 10-26 `--min-prior 2 --db $SCRATCH/ht2/replay_1026_mp2.duckdb`, then a read_only query | 1,084 rows | all `mode='replay'`, `min_prior=2`, `bool_or(within_prereg_window)=false`, `written_at > max(made_at)`; `share_f` units 0 (min_prior filter); `valid_for_f: false` | PASS |
| TZ | `within_prereg_window` vs clocks | `uv run python $SCRATCH/ht2/tz_attack.py` (and with `TZ=Asia/Tokyo`); `$SCRATCH/ht2/txn.py` | 4 session zones x 2 process zones | `timezone('UTC', current_timestamp)` equals Python UTC within 2 ms under DuckDB session zones America/Chicago (this Mac's default), New_York, Tokyo and UTC, and under process TZ Tokyo; ICU loaded; the column default under `SET TimeZone='America/Los_Angeles'` is UTC. `t_star` is aware UTC from the manifest `received_at` (`+00:00`) and `_naive` strips it without shifting; the JARVIS clock is `datetime.now(UTC)` naive. Capture and DuckDB share one host clock. The clock **freezes at transaction start** inside an explicit `BEGIN` (no code opens one today) | PASS (nit n2) |
| M1a | halftime counts, old vs new definition | `uv run python $SCRATCH/ht2/htscan.py <night>`; `$SCRATCH/ht2/htdefs.py live_2026-10-2{1..6}` (scoreboard status, period, clock, received_at only; no score read) | 30 halftimes, 6 nights | **10-25: 7 halftimes, 0 `ht_start_unobserved`; 10-26: 9, 0 unobserved** (MIN@CLE's real halftime at 00:15:13Z is now found; the 23:39:09Z 11:04 glitch is the only off-definition HALFTIME tick on the six nights). Six nights: old definition 29 eligible, new 25 (24 shared, 5 lost, 1 gained) | PASS |
| M1b | does the loss bias the entry set? | same scripts | 5 lost games | all 5 lost to capture timing: 10-21 MIN@MIA, CHA@BKN, CHI@TOR (capture started 01:00:43Z, mid-halftime; under the old rule t* landed many minutes into the break, e.g. MIN@MIA's Q3 tick came 166 s after the first seen tick); 10-21 DAL@HOU (predecessor 165 s before, past the 150 s bound; 27 scoreboard gaps > 150 s that night); 10-23 NO@UTAH (capture started 02:45Z). Selection is on capture uptime (and on tip slot on late-start nights), set independently of the first-half state and the outcome. No outcome-correlated asymmetry; costs n only. On healthy-capture nights (10-25/26) the loss is 0 | PASS |
| M1c | replay counts, strict window | `uv run python -m nba.markets.halftime_scorer replay --night data/raw/live_2026-10-2{5,6} ... --min-game-date 2026-10-01 --db $SCRATCH/ht2/replay_102{5,6}.duckdb --top 3` | 7 / 9 halftimes; 1,216 / 1,664 markets | exit 0 / 0; no `ht_start_unobserved`, no `q3_before_decision_time`; stored 0 (strict window: `x_insufficient_history` 841 / 1,095, as designed) | PASS |
| M2 | closed tick | read `halftime_entry.py:361-365`; tests `test_live_snapshot_waits_until_30s_after_the_first_page`, `test_live_pass_inside_an_open_tick_scores_nothing_then_everything` | - | live returns None until `as_of >= first_ts + 30 s`, counted; replay unaffected | PASS (nit n1) |
| M3 | recency window | `configs/jarvis.yaml:165` | - | `recency_min_game_date: null`, matching T508 training, per Devin's C3. The holdout-log row (line 173, logged at 105a6dc) covers the **replay on 2026-10-21..26 only**, not nightly live runs | PASS on code; **minor m11** on the log |
| S | spread filter, glm_e shown | `halftime_overs.py:108-112, 147-150`; `runner.py:388-391`; tests | - | Discord line requires bid >= 1c and ask - bid <= 10c (config-driven, validated >= 1); line reads `model 58% (pace baseline 55%) vs ask 44c, bid 41c + 2c fee`; paper table keeps every row | PASS (nit n4) |
| m1 | f scope | `halftime_store.py` `share_f`; replay above | - | f filters `mode`, `min_prior >= 5`, and in live `within_prereg_window`; `min_prior` stored per row | PASS |
| m4 | artifact pin | `configs/jarvis.yaml:166`; `halftime_overs.py:71-72`; `test_a_different_artifact_is_refused`, `test_shipped_artifact_matches_the_pinned_digest` | - | mismatch returns ok False; config validation requires the key | PASS |
| m5 | fee pin source | `halftime_scorer.py` `load_fee_pin` | - | replay reads `configs/jarvis.yaml halftime_overs.fee_pin` | PASS |
| m6 | holdout default | CLI without `--min-game-date` | 1 | exit 2, `the following arguments are required: --min-game-date`; `_min_game_date('none')` / `('NULL')` -> None; `RecencyProvider(min_game_date=)` keyword-only, no default | PASS |
| m7 | skips persisted | `halftime_store.py` `note_skip`; scratch store query | 7 reasons | persisted once per (mode, event, series, reason); but see m10 | PASS (minor m10) |
| m2, m3, m8 | text vs code | amendments doc | - | aligned (m8 via A4), but A4 adds a new contradiction (m12) | see m12 |

Deviation from the brief, stated: I did **not** run the replay with `--min-game-date none`. That reads season-2025 box lines, and my operating
rule forbids loading season 2025 for attacks, whatever the disclosure row says. Safe default taken: the strict window (`2026-10-01`). Every
entry-set count above (halftimes, unobserved, q3, closed tick, markets) is independent of the recency window. The `none` path was checked
at the parser (`_min_game_date`) and by the required-flag refusal. If the main session wants the `none` replay counts, it is the market
agent's run under row 173: `uv run python -m nba.markets.halftime_scorer replay --night data/raw/live_2026-10-26 --artifact data/models/halftime_ingame/2026-10-27/artifact.json --nba-db nba.duckdb --min-game-date none --db <scratch.duckdb>`.

## Remaining defects (file:line, severity, smallest fix)

**m9 minor: a replay can still reach an empty live store, and the result silently stops the forward log.**
`nba/markets/halftime_store.py:144` compares `p.resolve()` to `live_store_path()`. It misses a case-variant path on case-insensitive APFS
(demonstrated: same inode), a hard link, and any checkout whose `data/` is not the main one (`live_store_path()` is rooted at the importing
module's repo, `:116-122`). `:151-156` checks mode only on `halftime_scored`, so a store that a live pass created with skips only (the usual
first pass, `tick_not_closed`) accepts a replay. The next live open then raises at `:156`. That is caught at
`nba/jarvis/halftime_overs.py:103` and never surfaced: `nba/jarvis/runner.py:353-358` puts `scored` errors nowhere in the tick report. No row
is miscounted. The window is open only until the first live scored row exists (today: the store file does not exist).
Smallest fix: (1) on first open write a one-row `halftime_meta(mode, created_at DEFAULT timezone('UTC', current_timestamp))` and refuse any
open whose mode differs (closes every path variant at once). (2) In `runner._halftime`, append `{"kind": "halftime_scorer", "ok": False, "error": ...}`
to `out` when `scored["ok"]` is False, so a persistent refusal shows in the tick report. Optionally also compare `os.path.normcase` and
`(st_dev, st_ino)` when the file exists.

**m10 minor: `halftime_skipped` is not a table of exclusions.** `nba/markets/halftime_scorer.py:284-285` persists every reason seen on the first
pass, including transient ones (`tick_not_closed`, `series_no_page_in_window` before t* + 180 s) for series that a later pass scores, and
`:308-310` persists `markets_at_snapshot`, a tally, not an exclusion. Anyone summing the table as "counted exclusions" (MARKET_FEATURE.md's
wording) over-counts. Smallest fix: in live mode, persist `tick_not_closed` and `series_no_page_in_window` only once `as_of > t* + 180 s`, and skip
`markets_at_snapshot`; or rename the doc line to "first-seen reason tallies, not final exclusions".

**m11 minor, holdout hygiene: the live scorer's season-2025 feature read has no covering row.** `configs/jarvis.yaml:165` now ships `null`, so
every live JARVIS halftime pass reads season-2025 box lines as recent-form features. `docs/HOLDOUT_ACCESS_LOG.md:173` covers "replay on
stored live captures 2026-10-21..26 ... output to a scratch store" only. Substance is nil (production makes the same read daily), but the rule
is a row first. Smallest fix: the main session appends one systems-use row covering live runs of `nba/jarvis/halftime_overs.py` with a null
window, before the merge. A draft is below.

**m12 minor, new contradiction in the amendments (must be fixed before the freeze).** `docs/reviews/halftime_scorer_amendments_2026-10-27.md:67-68`
(A4) says "`f` and the entry count `n_req` use live in-window rows with `n_prior >= 5` only". The draft's s8 gate rule and step 4
(`HALFTIME_OVERS_VS_PRICE.md:177-179, 239`) measure f **label-free by M on the six gate nights' snapshots**, a replay, and the code agrees
(`share_f` in a replay store counts replay rows). Read literally, A4 makes the gate unmeasurable before freeze. `n_req` is also a computed
constant (3,424), not a row count. The same paragraph says a row is refused when its "`written_at` is after the second half started", but the
code refuses when the capture, read as of the caller's clock, already shows a period-3 tick (`halftime_scorer.py` `ht.q3_first <= made`).
Smallest fix, A4 text: "The gate's f is measured in replay mode on the gate nights (label-free, rows with `min_prior >= 5`). The forward entry
count n that triggers the one look counts live in-window rows with `n_prior >= 5` only. A live pass is refused when the capture as of its clock
already shows a period-3 tick. In-window rows (`written_at <= t* + 180 s`, 300 s after halftime starts, while the shortest observed break was
842 s) are before the second half by construction."

**m13 minor.** `nba/markets/halftime_store.py:69-73`: `halftime_refused` has no `mode` and its `refused_at` is caller-set (`_naive(made_at)`),
the stamping round 1 removed from the scored table. Fix: add `mode VARCHAR NOT NULL` and `written_at ... DEFAULT timezone('UTC', current_timestamp)`.

**Nits.** n1 `halftime_entry.py:361`: the closure test has no margin for manifest write latency (a page received at +29.7 s may land in the
manifest after a pass at +30.0 s); use `SAME_TICK_S + 5`. n2 `halftime_store.py:206`: `current_timestamp` is the transaction start; add a
comment or test so nobody wraps `append` in `BEGIN ... COMMIT` (that would stamp rows earlier than written and flatter the window). n3
`halftime_scorer.py:372`: `valid_for_f: true` on a replay reads like forward validity; rename to `min_prior_ok` or add `"mode": "replay (gate
measurement only)"`. n4 `halftime_overs.py:150`: "pace baseline" undersells glm_e (it is player-aware); "baseline glm_e" matches the draft.

**Observation for the gate (estimate, not measured).** JARVIS ticks every 120 s (`ops/launchd/local.nba.jarvis.plist:39`). The tick closes at
first page + 30 s (t* + 30..90 s), and in-window requires `written_at <= t* + 180 s`, so by lattice arithmetic about 6% of live (game, series)
passes land out of window. They are stored, flagged and never scored: honest, but the live entry yield is below the replay yield. The gate's
"capture" factor should be measured on live in-window yield, not replay availability.

## Corrected family and power note

No inferential family was run; nothing was scored. Every number here is a label-free count (halftimes, ticks, rows) or a clock difference.
No CI applies and none is claimed. Power is unchanged from round 1. At e = 24, glm_e is as strong as M (round-1 check 2c), so PASS_BASELINE
remains the likely ending. Both are now displayed.

## Biggest risk

That the forward log stops and nobody sees it (m9). The guard that keeps replay rows out is now a guard that can lock the live writer out,
and the scorer's failures do not reach the tick report. A forward log that silently has no rows on the night it should costs the gate a
night of n, with no alert.

## Not verified (round 2)

- The `--min-game-date none` replay (declined above; strict-window counts substitute for every entry-set check).
- The replay oracle for the live-path change, and an end-to-end JARVIS tick (no Discord, no launchd).
- The 6% cadence loss (lattice estimate only).
- Whether other worktrees in `git worktree list` have a real `data/` rather than a symlink (relevant to m9's other-root case; the reviewed
  worktree's `data` is a symlink to the main checkout).

## Drafted rows (not appended; the main session decides)

`docs/DECISIONS.md` (draft):
`| 2026-10-27 | Halftime scorer round-2 review (docs/reviews/halftime_scorer_2026-10-27.md, c5a6451): APPROVED. B1, M1, M2, M3, the display spread filter and m1-m8 verified fixed; minors m9 (meta-table mode lock + surface scorer errors in the tick report), m10, m11 (holdout row for live runs), m12 (A4 text: gate f from replay, forward n from live), m13 recommended. Merge still needs the replay oracle IDENTICAL and Devin's go. | No row can count as forward unless written live inside the window by the database clock; the remaining defects are liveness and text. | A replay row counted in live f, or a live night with zero stored rows and no reported error. |`

`docs/HOLDOUT_ACCESS_LOG.md` (draft, for the main session; I do not append):
`| 2026-10-27 | DISCLOSURE, logged BEFORE the first live run: the JARVIS halftime scorer (nba/jarvis/halftime_overs.py, configs/jarvis.yaml halftime_overs.recency_min_game_date: null) reads, on every live halftime pass in 2026-27, each player's last 10 played regular-season box lines before the slate date, including season-2025 games in Oct-Nov, as recent-form FEATURES (the read production makes daily). No label, score, settlement or outcome is computed from season 2025. HALFTIME_OVERS_VS_PRICE C3 = allow (Devin, 2026-10-27 18:41). | systems use (feature read) | last-10-game box lines of season 2025 for players on 2026-27 slates | NOT a confirmatory touch. |`

No ledger row: nothing was scored.
