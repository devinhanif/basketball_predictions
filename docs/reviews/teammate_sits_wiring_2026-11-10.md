# Adversary review: teammate_sits wiring (branch agent/market-teammate-sits-wiring, tip 0ed14883), 2026-11-10

Scope: `nba/export/sits_core.py`, `nba/export/teammate_sits.py`, `nba/export/build.py` / `__main__.py`, `app/api/.../scenarios.py`
(+ export_store, store, main), web picker (`TeammateSits.tsx`, `sits.ts`), contract `docs/contracts/teammate_sits_v1.md`, tests.
Live path (export runs from `nba.ops.app_export`). No full export build, no replay oracle (main session runs it), no season 2025
loaded: every probe ran on the branch's synthetic test league or on 2026 rows of the read-only 2026-11-09 backup.

**Overall: WOUNDED.** The design is right (production heads, the report capped at tip - 60, no refit, isolated failure, a
server-enforced display rule) and the main honesty claims hold. Four things are wrong or missing. Two of them break a requirement
the task stated: the import can fail the export, and a player ruled OUT can appear as a teammate who gains. The other two are
copy the frozen rule asked for and a small code path outside the guard. All the fixes are small.

First-pass verdict (superseded by the Re-review at the end): BLOCKED

(Merge after B1, M1 and M2. The minors can wait.)

Most likely failure if the claim "the scenario step can never hurt the export" is wrong: an import-time error in
`nba.props.context_residual` or its dependencies (lightgbm/libomp after a brew upgrade, or a bad merge in a module the export
never used to load) kills `python -m nba.export build` before `compute`'s try/except exists. The app keeps yesterday's export,
which loses scores, box, lines and props as well as the scenarios.

## Model class and playbook check

Class: gradient-boosted trees (LightGBM context residual), read with one input edited (a counterfactual of the vacancy features).
Nothing is fitted or new.
1. Information or capacity: neither. It re-expresses production's own response to the report. The evidence is T526/T526-a, which
   measured whether that response is right.
2. Data enough: not applicable (no fit). The heads are production's cached morning heads.
3. Test validity: T526 was integer-support CRPS, clustered union-envelope CIs, real tips 1504/1504, a placebo sitter reversing
   every stat (T526-a). This review tests the wiring, not the model.
4. Lineage: `research/teammate_sits/scenario.py` (the T526 harness reference). Inherited guards: report lead >= 60 min
   (`slate_report_outs`), the as-of join strictly before the game date (`vacated_features`, `sitter_rows` `_key = date - 1 day`),
   `has_report == 1`. All of them are carried forward (checked, section 2).
5. Scored against: the wiring is not scored. The display is bounded by T526-a's words (pts direction + team size; reb direction,
   size uncertain; ast/fg3m nothing).
GBT class checks: missingness (no report is not imputed as "nobody out", so a game without a report gets no rows; tested);
planted future (the report is capped at tip - 60 even for in-progress games; history is `< day`); "only re-expresses the base"
(yes, by design, and T526-a showed a constant scale recovers about 92% of the pts gain; the copy does not claim the split).

## Findings (severity-ranked; exact line, exact fix)

### B1 (blocker against the stated requirement): importing the export now loads lightgbm and the context-residual stack, outside any guard
- `nba/export/build.py:22` imports `teammate_sits`. `nba/export/teammate_sits.py:36` imports `sits_core`, and
  `nba/export/sits_core.py:21-30` imports `nba.props.context_residual` (lightgbm, `nba.features.injury_report`,
  `nba.models.rung0_baselines`, `nba.props.baselines`) at module load. `build.py:220` also needs `teammate_sits.COLUMNS` at import.
- Evidence (exit 0 both):
  `PYTHONPATH=<main> python -c "import nba.export.build, sys; print('lightgbm' in sys.modules)"` gives False on main (0.90 s).
  The same on the branch gives True, with `nba.props.context_residual` loaded (1.03 s).
- Effect: an ImportError or any module-level exception in that chain fails `python -m nba.export build` with a traceback (rc 1).
  `app_export` keeps the previous export and alerts. The "never raises" guarantee covers `compute`, not the import.
- Fix: move `COLUMNS` (and `SCHEMA_VERSION`, `RULE_*`, `EXPORT_STATS`, `MAX_ROWS`, `DEFAULT_BUDGET_S`) into a dependency-free module
  (e.g. `nba/export/sits_schema.py`, stdlib only) that `build.py` imports. In `build._sits_stage`, import `teammate_sits` lazily
  inside a `try/except Exception` that returns `SitsResult([], {status: absent, reason: "error:<Type>"})`. Add a test that
  monkeypatches `sys.modules["nba.export.sits_core"] = None` (forces ImportError) and asserts `build_export` still writes the export
  with `manifest.teammate_sits.reason == "error:ImportError"`.

### M1 (major, display correctness): a teammate ruled OUT on the report is listed as a receiver, "likely to play", and counted in the team total
- `teammate_sits.py:213` builds `roster` from every prop row of the day (`fwd.prop_grids`: all models, superseded rows included).
  A player ruled OUT after an earlier run keeps his earlier row, so he is on the roster. `rostered_not_out` keeps him out of the
  **sitters**. `sits_core.scenario_features` (`teammate_sits.py:280`) joins every base row of the team, so he stays a **teammate**.
  His `p_play` comes from `_p_play_map`, which falls back (line 144) to the first prop row found, i.e. a pre-OUT number.
- Probe (scratch `test_probe_sits.py` on the branch's synthetic league, exit 0): with `report_out[g] = {s}`, player `s` appears in
  8 scenario rows as `player_id` (receiver) with `p_play` 0.9. The API would mark him `likely_plays: true` and list him under
  "other teammates if X sits" with a points arrow, and `TeamBump.total` weights his delta by 0.9.
- How often (approximate; read-only 2026-11-09 backup, 2026 rows only): of 1,939 (game, player) pairs with a pts/reb forward row
  over Nov 1-8 (58 games), 125 (6.4%, about 2.2 per game) were OUT on the latest availability row at or before tip - 60. Caveat:
  the availability clock may be ET against UTC tips, so treat this as order-of-magnitude.
- Fix: in `_compute` after `scn = ...` (line 280), drop receivers with `player_id in flagged[game_id]`, e.g.
  `long = long.filter(~pl.struct("game_id","player_id").is_in(<out pairs>))`, or filter `roster` to `rostered_not_out` before
  building `base` targets. Count them in `null_reasons["receiver_out"]`. Test: the probe above, asserting zero rows.

### M2 (major, frozen-rule copy): the "not covered" disclosures the rule requires are partly missing
- `docs/prereg/TEAMMATE_SITS.md` (display condition paragraph): "**Not covered:** QUESTIONABLE/DOUBTFUL players (events are OUT
  only), scratches after T-60, nights with two or more fresh starters out, and the teammate's own P(play). The app copy says so."
- `app/api/src/parlayhitters_api/scenarios.py:43` `NOT_MODELLED` covers two or more sitters and the teammate's own chance to play.
  It does not say the test only scored starters ruled OUT on the report at least an hour before tip. That gap matters here: the
  picker's buttons are labelled "listed questionable" / "listed probable" (`sits.ts` `STATUS_WORD`), which is exactly the
  untested case, and a scenario picked near tip is a late scratch.
- T526-a recommendation 2 asked for reb copy "uncertain size, concentrated in a few players". The app says only "uncertain size"
  (`scenarios.py:40`, `sits.ts` `rebSentence`).
- Fix: add to `NOT_MODELLED` "Tested only on starters ruled out on the official report at least an hour before tip; a questionable
  player or a late scratch was not part of the test." Change line 40 to "...rebounds direction only; the size is uncertain and
  came mostly from a few players."

### m1 (minor): `_sits_stage` does work outside the never-raise guard
- `build.py:379-382`. The `props` comprehension (`p["game_id"]`, `p["player_id"]`, `teams_of.get(...)[0]`) runs before `compute`'s
  try, inside the build's `try/finally`. Probe: a prop row without `player_id` raises `KeyError` out of `_sits_stage` (exit 0,
  raised). In practice prop rows always carry both keys, so the risk is low. Fix: build `props` inside `compute`'s guard (pass
  `teams_of` through), or wrap `_sits_stage`'s body in the same try as in B1.

### m2 (minor): the time budget is checked once, before the candidate and forecast stages
- `teammate_sits.py:253-254` is the only check, and it comes after the feature build. The vacancy + forecast stages after it are
  unbounded. Estimate (not measured here): production's pretip `predict` step does the same history read, feature build and four
  head predictions plus five shadows in about 25-30 s (`data/ops/pretip_20261109.log`, 07:20:01 to 07:20:30). That is far inside
  the 150 s budget and the 600 s run deadline, so this is hygiene. Fix: a second check after `scenario_forecasts`, before rows are
  materialised.

### m3 (minor): the cache fingerprint check is weaker than production's
- `teammate_sits.py:245` compares only `n_rows` and `max_date`. Production (`forward._load_or_fit_models`) also keys on `cfg`,
  `tip_source`, `stats` and `lineups_known`. Today every writer of `data/models/context_residual/<day>` uses the default config, and
  the lt/full/combo/t30/late caches use their own directories (checked: `forward.py:888`, `late_report.py:251,261`, `t30.py:313`).
  A code change to `ContextResidualConfig` merged mid-day would still pass this check with morning heads fitted under the old
  config. Fix: factor production's fingerprint builder into a function and compare the whole dict.

### m4 (minor): `per_player` is a per-player size when `n_likely == 1`
- `scenarios.py:203`. With one likely teammate, "about X per teammate likely to play" is that player's own delta. T526-a: no
  per-player size for pts. Fix: send `per_player = None` when `n_likely < 3`.

### m5 (minor, docs): the contract body contradicts its new status block
- `docs/contracts/teammate_sits_v1.md:23` ("The baseline is production's own row") and `:34` (`made_at` = "the production run's
  made_at (must equal the paired props rows')") disagree with the implementation and with the status block (same-run recompute;
  `made_at` = the export's). Fix: edit both lines to match the code.

### m6 (minor): dead relative path
- `teammate_sits.py:56` `DEFAULT_MODEL_ROOT = Path("data/models/context_residual")` is unused (the CLI passes
  `REPO_ROOT / data / models / context_residual`). Fix: delete it, or make it `REPO_ROOT`-based.

### m7 (minor, wording): "2023-25 history"
- `scenarios.py:37,40`, `sits.ts` `EVIDENCE_HEADLINE`. It is accurate (select 2023 = 2023-24, report 2024 = 2024-25), but "2025"
  can read as the frozen holdout season. Fix: "Tested on the 2023-24 and 2024-25 seasons".

## Per-question answers

1. **Failure isolation.** `compute` never raises (tested; probe confirmed). A cold cache returns `no_model_cache` before any work.
   A stale cache returns `model_cache_stale` and never fits (tested). The row cap withholds the table rather than truncating it
   (tested). **Not isolated:** the import (B1) and the props comprehension (m1). The time cost is small by analogy (m2). The step
   holds the read-only `nba.duckdb` connection a few seconds longer, inside the 600 s deadline and the `MIN_ATTEMPT_S` design.
   **cwd:** not a real risk. `app_export._build_subprocess` runs with `cwd=root` (`NBA_ROOT` or the repo, `app_export.py:363,577`),
   the plist sets `WorkingDirectory` to the repo, and the CLI default is `REPO_ROOT`-absolute. `reader` uses
   `DEFAULT_SCHEDULE_CACHE_DIR` (`__file__`-based), and `load_elo_params` uses `REPO_ROOT/configs`. The only relative path is dead
   code (m6).
2. **Known-at.** The report goes through `slate_report_outs(con, tips, made_at)`: latest stamped at or before
   `min(now, real tip - 60)`, at most 36 h old, chosen per game. So an in-progress game uses its pre-tip report. History is
   `game_date < day`. Placeholder rows dated today cannot feed today's features: the as-of joins use `date - 1 day`, and team
   totals are `shift(1)`. Production's cached heads are reused, with the fingerprint check (weaker than production's, m3). The
   only post-tip-capable input is `sitter_status` (`_status_map` over the explainer's latest report lines). It is a label, not a
   model input. Pass.
3. **Display honesty vs T526-a.** The API sends no per-player pts size (except m4), no reb size, no ast/fg3m, and no
   means/quantiles/deltas/prices. The pydantic models make this structural, and `test_assists_threes_prices_and_raw_means_never_leave`
   enumerates the keys and bans `scn_`/`base_`/`delta`/`price`/`probab`. Per-teammate pts direction is supported (T526-a: "who
   gains" directionally right, slope 1.13 [0.50, 1.72]). The raw export file carries sizes, but nothing in the API serves it
   (no FileResponse/StaticFiles; checked). Evidence lines: accurate on seasons (m7). Required coverage copy is missing (M2).
4. **Baseline vs production.** No baseline number is sent to the client, so the page cannot show two numbers that disagree. The
   only cross-page coupling is semantic: the team total is a move of **the context model's** forecasts. Today the app's primary
   prop is `props_context_residual` on every row (latest export: 7,416 `fallback_stale` + 836 `fallback_missing`, 0 AVG; AVG is
   stale). If AVG returns as primary, "teammates' points forecasts rise by about X" would describe a model other than the one
   shown. Latent; recommend the copy say "the context model's forecasts" or the API suppress it when the primary source differs.
   Sources of base != production: a newer report usable at export time (a pretip run between :20/:50 and the export at :05/:35),
   and the late-report model rows (`props_context_residual_late`, not compared). **Not quantified:** the agent's measured
   `base_vs_production` (n_compared, max_abs_diff, n_over_0_05) is not in the repo or the commit messages. The main session should
   paste it from the measured build's manifest into this review.
5. **sits_core equality.** Real. `tests/export/test_sits_core.py` runs both implementations on the same synthetic 120-day league
   and `assert_frame_equal`s every stage (sitter rows with height > 50, candidates, vacancy, features with height > 0, and
   forecasts from a fitted model on pts/reb). The orchestration in `_compute` re-implements `slate_scenarios`
   (`rostered_not_out` + `sitter_rows` + the starter10 filter equals `candidates`). It is covered indirectly by
   `test_rows_follow_the_contract`. Gap: no test that a **real** sitter changes something (positive mean pts delta is asserted,
   `test_teammate_sits.py:165-166`: OK).
6. **Privacy and price.** No prices, no stakes, no credentials. `p_play` and `sitter_status` are already public in the app. Pass.
7. **Tests.** Meaningful: contract columns, determinism, base == production in the synthetic world, report status precedence,
   OUT not a sitter, no-report game excluded, every absent reason, never fits, the budget, error isolation, API key enumeration,
   the lock on finished games, and the flat threshold. Missing: the import-failure test (B1), the OUT-receiver test (M1), and
   `n_likely < 3` (m4).

## Check table

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels | n/a (no model gain claimed; T526-a found the shuffle an invalid null for this contrast and used a placebo sitter instead) | - | - | n/a |
| 2 | Planted future / as-of | Read `slate_report_outs`, `vacated_features`, `sitter_rows`, `_with_slate_placeholders` | - | Report capped at tip - 60; joins `< game_date`; placeholders cannot reach their own row | PASS |
| 3 | Missingness | Read `_compute` + `scenario_features` (`has_report == 1`); test `test_game_without_a_report_gets_no_scenario` | synthetic | No-report game produces no rows and is counted in `no_report` | PASS |
| 3b | Receiver population | probe `test_out_teammate_still_listed_as_receiver` (scratch) | 8 rows | OUT player listed as receiver, `p_play` 0.9 | **FAIL (M1)** |
| 4 | Import isolation | `python -c "import nba.export.build, sys; print('lightgbm' in sys.modules)"` main vs branch | - | False vs True | **FAIL (B1)** |
| 4b | Guard coverage | probe `test_sits_stage_outside_guard_raises` (scratch) | 1 | KeyError escapes `_sits_stage` | FAIL (m1, minor) |
| 5 | Equality with reference | `pytest tests/export/test_sits_core.py` | 3 tests | pass | PASS |
| 6 | Integer support | n/a (no scoring in the wiring) | - | - | n/a |
| 7 | Display rule | `pytest app/api/tests/test_scenarios.py` + read `scenarios.py` | 11 tests | no size/ast/fg3m/price leaves; m4 edge case | PASS (m4) |
| 8 | Rule integrity | `RULE_SHA8 = "c23f7b36"` vs ledger T526 "frozen c23f7b36" | - | equal | PASS |

Verification (unpiped):
- `PYTHONPATH=<worktree> <venv>/python -m pytest -q --no-cov -W ignore tests/export/test_sits_core.py tests/export/test_teammate_sits.py tests/export/test_export.py`: 44 passed, exit 0.
- `app/api`: `python -m pytest -q tests/test_scenarios.py`: 11 passed, exit 0.
- `ruff check` (changed Python files): exit 0. `ruff format --check`: exit 0. mypy not run. Web tests (`sits.test.ts`) not run.

Corrected family / power: no inferential claim in this diff, so there is no family. The display's evidential basis is T526 (BH
m = 4) with T526-a's words. pts was measured at about the 80%-power MDE (likely inflated, truth plausibly -0.01 to -0.03), which is
why the app shows direction plus a team-level size only.

## Not verified
- Runtime and `base_vs_production` on a real slate (no export build by rule; the agent's measured numbers were not found in the repo).
- The replay oracle (main session).
- Web unit tests and mypy.
- The OUT-receiver rate on live data, beyond the approximate backup count above (clock caveat).
- Whether `best_model.pplay_rows` writes a near-zero P(play) for players ruled OUT after the morning run. If it does, M1 would
  show the player as "not likely" but still list him, and his weight in the total would be small.

## Recommended changes (main session acts; nothing applied here)
B1, M1, M2 before merge. m1-m7 when convenient. No ledger row is needed (wiring, no new result). Draft DECISIONS row (not appended):
`| 2026-11-10 | teammate_sits wiring adversary review: WOUNDED / BLOCKED until (B1) lazy guarded import + schema split, (M1) OUT receivers dropped, (M2) rule-required "not covered" copy added. Display rule otherwise holds. | Adversary sequence. | A re-review finding a new contradiction. |`

Scratch note: running `uv run` in the worktree created an ignored, empty `.venv/` there. git status is clean. Deleting it was
denied by permissions, so remove it by hand if wanted.

## Re-review (fixes at 6c7d26df), 2026-11-10

A light re-review of the diff only (`git diff 0ed14883..6c7d26df`, 11 files). I re-ran the original probes.

| Finding | Check | Result | Pass |
|---|---|---|---|
| B1 import isolation | `PYTHONPATH=<main or branch> python -c "import nba.export.build, sys; print('lightgbm' in sys.modules, 'nba.props.context_residual' in sys.modules)"` | main False False; branch False False (exit 0 both). `build.py` imports only the stdlib `sits_schema`. `teammate_sits` is imported inside `_sits_stage`'s `try`, which also wraps the props comprehension (m1). New tests: `test_a_broken_scenario_import_never_fails_the_export` (forced ImportError, manifest reason `error:ImportError`) and a subprocess no-lightgbm test | PASS |
| M1 OUT receivers | scratch probe `test_out_teammate_still_listed_as_receiver` (it asserts the bug exists) | 0 receiver rows for the OUT player, so the probe now FAILS as intended. The fix anti-joins `flagged` (the report used) from `long` and from the expected-pairs roster, and counts `null_reasons.receiver_out` | PASS |
| m1 guard coverage | scratch probe `test_sits_stage_outside_guard_raises` | no longer raises; logged `teammate_sits skipped: KeyError`, step absent | PASS |
| M2 copy vs frozen rule | `scenarios.py` `NOT_MODELLED` and `EVIDENCE` against TEAMMATE_SITS "Not covered" and T526-a rec. 2/3 | All four "not covered" items are now stated: questionable/doubtful, scratches after T-60, two or more sitters, own P(play). reb: "uncertain and came mostly from a few players" (API and web). pts: no claim about the split | PASS |
| m4 per_player | `scenarios.py:204` `if len(likely) >= 3 else None`; test `test_per_player_size_needs_three_likely_teammates` | holds | PASS |
| m2 budget | checks added after vacancy and after forecasts | holds | PASS |
| m3 fingerprint | `expected_fingerprint` mirrors `_load_or_fit_models`' pops for the default T-60 heads and compares the whole dict. A mismatch can only withhold scenarios. The agent reports it matching on the real 2026-11-09 cache (2,024 rows) | holds; a drift risk if production's pops change (fails safe) | PASS |
| m5 / m6 / m7 | contract lines 23 and 34; `DEFAULT_MODEL_ROOT` deleted; "2023-24 and 2024-25 seasons" | fixed, apart from one leftover (below) | PASS |

Tests (unpiped): `pytest -q --no-cov tests/export/test_sits_core.py tests/export/test_teammate_sits.py tests/export/test_export.py` gives 48 passed, exit 0. `app/api pytest -q tests/test_scenarios.py` gives 13 passed, exit 0. Not run: web tests, mypy, ruff on the new commit, the replay oracle (main session).

Question 4 is now quantified by the agent (contract status block; not re-measured here because no export builds are allowed): slate 2026-11-09, 8 games, step 2.78 s, 2,024 rows. `base_vs_production` against the paired 12:00 UTC run: 190 compared, max abs diff 0.0049, none over 0.05. Against later runs it differs by up to 3.8 because the reports changed. That is expected, and no baseline number reaches the client.

New contradiction found (minor, does not block): `docs/contracts/teammate_sits_v1.md:30` still says the file is "written by the same run (same `made_at`, same model cache) as the production props rows it pairs with". That disagrees with the corrected line 34 (the export's `made_at`, a same-run recompute). Fix: "written by the export build (its `made_at`), from the morning model cache; it pairs with no stored run".

Still open, not blocking: the latent AVG-primary wording risk (question 4) and the pre-OUT `p_play` fallback for players who are not OUT (`_p_play_map`, the first prop row found).

Verdict: APPROVED
