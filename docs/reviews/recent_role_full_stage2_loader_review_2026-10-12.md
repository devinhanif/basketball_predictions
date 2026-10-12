# Adversary review: RECENT_ROLE_FULL Stage-2 loader (pre-touch), 2026-10-12

Scope: commit d006d8e (`research/eval/recent_role_full_eval.py` stage2 / rehearse / `_stage2_body` / `stage2_pipeline` /
`reproduction_checks` / `restrict_stored` / `stored_reference` / `data_gate_2025` / `stage2_scores` / `decide_stage2` / `render_stage2`),
tests `tests/research/recent_role_full/`, rehearsal `reports/prereg_recent_role_full/stage2_rehearsal/`. Rule
`docs/prereg/RECENT_ROLE_FULL.md` frozen 73f3f4b0 (81e3f91). No season-2025 row was loaded or read for this review; the only data read
was the committed 2024 rehearsal JSON and the Stage 1 `results.json`.

## Verdict: **CHANGES REQUIRED** (one blocker, two majors). Do not run `stage2` until B1, M1, M2 are fixed and re-reviewed.

Top finding (B1): run the documented way (`python -m research.eval.recent_role_full_eval stage2`), the module executes as `__main__`.
`season_cap` only patches modules whose name starts with `nba.` / `research.`, so `__main__.MAX_SEASON` stays 2024 and
`collect_heads` (line 210, `if max(test_seasons) > MAX_SEASON: raise`) refuses the 2025 walk. The refusal fires AFTER gate B, the
2025 data gate, `expected.json`, `touch_started.json` and the 2025 `collect_pi` fit. Outcome: the one touch is burned (the marker
makes `require_unspent` refuse forever), 2025 outcomes have been fitted, nothing is scored. The tests import the module as
`research.eval.recent_role_full_eval`, where the patch works, so they cannot see it; the rehearsal never raises the cap, so it
cannot see it either.

## Checks

| # | Check | Command | Result | Pass |
|---|---|---|---|---|
| R1 | Rule text unchanged since freeze | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/RECENT_ROLE_FULL.md \| shasum -a 256` | 73f3f4b04754ec9a... (= frozen sha) | PASS |
| R2 | Loader commit scope | `git show --stat HEAD` | loader + tests + rehearsal artefacts only; no rule, threshold or Stage-1 function changed (diff is additive; `holdout()` stub replaced) | PASS |
| B1 | Cap reaches the code that runs under `-m` | scratch `probe_main.py` (loads the file under a non-`research.` name, enters `season_cap(2099)`, calls `collect_heads` on an empty frame; no data) | `-m`-like module: MAX_SEASON 2024, `collect_heads` REFUSES "season 2025 is the frozen holdout"; imported module: 2099, passes the check. Exit 0 | **FAIL (blocker)** |
| M1 | Coverage guard scores FULL | read `stage2_scores` l.1059-1060; rehearsal vs Stage 1 `results.json` | `qf = WE.mixture_qi(h0, ...pis["FULL"])` uses the A0 heads: it is the PI arm's coverage, not FULL's. Stage 1 `score_stat` uses `comps["FULL"]`. Rehearsal FULL 2024 coverage 0.1163/0.2156/0.8027/0.9008 vs Stage 1 FULL 2024 0.1190/0.2168/0.8017/0.8999 (n 27,701) | **FAIL (major)**: the rule's guard 4 is applied to the wrong forecast |
| M2 | Nothing reads 2025 outcomes before the touch marker | read `_stage2_body` | inside the raised cap, the frame with 2025 rows is built, gate B runs, then `data_gate_2025` reads 2025 realised `minutes` (missingness by minutes bucket) and the played-row count, all BEFORE `touch_started.json`. A stop there writes `touch_consumed: false` and allows a rerun, i.e. a second 2025 load | **FAIL (major, hygiene)**. Information content is low (aggregates, no model) and cannot leak into training, but "not consumed" is false and the one-run rule is not enforced |
| 1 | One run, pts only, seed 0 | read `stage2`, `require_unspent`, `STAGE_STAT`, `SEED` | refuses on `results.json` or `touch_started.json`; pts only; seeds as Stage 1 | PASS (subject to M2) |
| 2 | Refusals before any load | `stage2` and `main`: `require_holdout_row` (committed, unmodified log, sha8, PRE-REGISTERED, DEVIN CONFIRMED) then `require_unspent`, before the lock and the cap; tests `test_stage2_refuses_*` | PASS. `grep RECENT_ROLE_FULL docs/HOLDOUT_ACCESS_LOG.md`: no row yet, so it refuses today | PASS |
| 3 | Gate A on 2024 under cap 2024 before any 2025 load; gate B after the cap is raised | rehearsal `results.json` gate list | A0 heads 0.0, A0 pi 0.0, w_q 0.0, FULL-A0 -0.0083144764 (stored exact equal; 4.8e-7 to the rounded T467 constant), A0 CRPS 3.1768395 equal, FULL-PL -0.0085785 equal; gate B same on the rebuilt frame. n 27,701, 1,320 games | PASS |
| 4 | w_q live, identical for A0 and FULL, never stored 2025 | `arm_scores` uses `res["pic"]` (live `collect_pi`) through `candidate_pic` for every arm; `stored_reference` reads only stored 2023-24 and asserts max season <= 2024; test `test_stage2_pipeline_never_reads_a_stored_file` | PASS |
| 5 | A0 for 2025 built walk-forward as the stored components | `collect_heads` / `collect_pi` with `test_seasons=(2025,)`; gate A shows the single-season walk reproduces the two-season stored walk to 0.0 for 2024 (blocks train on all rows strictly before the block start) | PASS |
| 6 | No extra arms on 2025 | `stage2_pipeline(..., guards=("PL","SHUF"))`: A0, FULL, PL, SHUF_CTRL, SHUF_FULL only; no HEADS / PI / MINLAST / ORACLE | PASS |
| 7 | Guards fitted as the frozen guards | PL = `ARMS_PI["PL"]` + `heads_names("PL")`; SHUF hashes asserted equal | PASS (coverage: see M1) |
| 8 | Thresholds and inference unchanged | FLOOR -0.005, CI upper < 0, p_BH <= 0.05 with m = 1 ("BH over the carried stats"), ECE_TOL 0.005, COV_TOL 0.01, SLICE_TOL 0.01, MIN_SLICE_N 300, GUARD_SLICES min<5/bench/playoffs/cold_start, PLACEBO_MARGIN -0.003, SHUF_TOL -0.001; `WE.delta_stats` (same game-clustered bootstrap as Stage 1) | PASS: nothing easier than the frozen rule |
| 9 | Expected value written before scoring | `expected.json` written before `touch_started.json` and the 2025 walk: -0.007, pass p 0.75-0.82 | PASS |
| 10 | Cap restored on exit, also on error | `season_cap` try/finally; test `test_stage2_cap_restored_when_the_2025_walk_raises` | PASS (for the modules it patches; see B1) |
| 11 | Unit tests | `.venv/bin/python -m pytest -q tests/research/recent_role_full/` | 22 passed; exit 1 only from the repo-wide `--cov-fail-under=70` on a subset run (total 1.77%), not a test failure | PASS (tests) |

## Shuffle-guard noise (asked): can it fire spuriously?

pts SHUF_FULL - SHUF_CTRL draws so far: Stage 1 2023 +0.0019 [-0.0006, +0.0044], Stage 1 2024 -0.0004 [-0.0024, +0.0014], rehearsal
2024 +0.0037 [+0.0019, +0.0056] (n 27,701). The rehearsal and Stage 1 2024 are the SAME rows with different permutations
(`default_rng([0, b])` with `b` indexed from the first test block, which moves when `test_seasons` changes), so the 0.0041 swing is
pure permutation variance. The game-clustered CI is conditional on one permutation and does not include it; the between-permutation
sd (~0.002 from three draws, very rough) is about the size of the CI half-width (~0.0018). The guard fires when the delta is about
-0.0019 or lower. With a centre between 0 and +0.0017 and sd 0.002, the spurious-fire probability is roughly 4% to 17%. It can only
make a pass HARDER (a fire disqualifies), never easier, and the construction is frozen, so it stays. Consequence to state in advance:
a 2025 NOT A PASS driven only by a shuffle fire near -0.002 is a permutation-noise outcome under the frozen rule; it is still a
NOT A PASS (no rerun, no new seed), and the follow-up, if any, is a new id. The stated pass probability 0.75-0.82 should be read as
including this cost.

## Required changes (the main session applies; re-review B1 and M1 before the run)

- **B1 (blocker)**: make the cap reach the running code. Simplest: in the `if __name__ == "__main__":` block, dispatch to the
  imported module (`from research.eval import recent_role_full_eval as E; raise SystemExit(E.main())`), or have `collect_heads` read
  `CRE.MAX_SEASON` at call time instead of the module-level binding. Add a preflight inside the cap, BEFORE `touch_started.json`:
  assert the `MAX_SEASON` seen by `collect_heads`, `collect_pi`, `PR.walk_pi` and the loaders equals the target. Add a test that runs
  the CLI path under `runpy.run_module(..., run_name="__main__")` (fake builder/pipeline) and asserts the 2025 call is not refused.
- **M1 (major)**: `qf = WE.mixture_qi(res["heads"]["FULL"], PR.candidate_pic(res["pic"], res["pis"]["FULL"]), STAGE_STAT)`. Add a
  gate-A check that the Stage-2 FULL and A0 coverage on 2024 equal Stage 1's (same PIT rows and seed, or within 0.002 if the
  randomised-PIT draw depends on the 2-season array, which it does: A0 0.11718 vs 0.11805 today).
- **M2 (major)**: write `touch_started.json` (with HEAD sha) as the first statement inside `cap_ctx`, before the 2025-capable
  build; any stop inside the cap records `touch_consumed: true`; a rerun after a gate-B or data-gate stop is Devin's call, logged.
- m1 (minor): restore the Stage-1 precondition the old stub had (`results.json` verdict passes for pts) before raising the cap.
- m2 (minor): compare gate-A FULL-A0 to the stored exact value (`ref["full_minus_a0"]`) at 1e-9, not the rounded constant at 1e-6.
- m3 (minor): rule pass item 8 (plants) is not re-run in Stage 2; acceptable because the walk functions are unchanged and Stage 1
  plants passed on them; say so in the Stage-2 report.

## The single most likely failure mode if the run went ahead today
B1: the touch is consumed by a crash after `collect_pi` has fitted on 2025, with no score and no rerun allowed.

## Not verified
- Behaviour on real 2025 rows (by design). Whether every loader module the frame build imports is in `preimport()`; a missed one
  leaves 2025 rows out and the data gate (>= 25,000 rows, >= 1,000 games) would stop it (safe), but after M2 that stop consumes the touch.
- The between-permutation sd of the shuffle guard beyond three draws (a descriptive 2024 run of 5-10 extra permutations would pin
  it; optional, scratch only, cannot change the rule).

## Drafted ledger row (not appended)
```
| T470 | Adversary pre-touch review of the RECENT_ROLE_FULL Stage-2 loader d006d8e (docs/reviews/recent_role_full_stage2_loader_review_2026-10-12.md) | CHANGES REQUIRED. Blocker: under `python -m` the module runs as __main__, season_cap does not patch it, collect_heads refuses 2025 after touch_started.json and the 2025 collect_pi fit (touch burned, nothing scored); proven with a no-data probe. Major: Stage-2 coverage guard uses A0 heads for FULL (rehearsal FULL 2024 cov 0.1163 vs Stage 1 0.1190 at tau 0.10, n 27,701); touch marker written after 2025 rows and realised minutes are read. Passed: rule hash 73f3f4b0 intact, refusals, gate A/B reproduce T467 -0.0083144764 exactly (n 27,701, 1,320 games), live w_q shared, no extra arms, thresholds/BH m=1/CI unchanged, expected -0.007 written first, cap restored. Shuffle guard permutation sd ~0.002 (2024: -0.0004 vs +0.0037 same rows): spurious-fire risk ~4-17%, conservative direction only |
```

---

## Re-review of the fix commit 4d7a98b (2026-10-12): **APPROVED**

The loader may run once, when Devin chooses to, after the committed `HOLDOUT_ACCESS_LOG.md` row (RECENT_ROLE_FULL, 73f3f4b0,
PRE-REGISTERED, DEVIN CONFIRMED). No season-2025 row was loaded for this re-review. The scratch probes import modules and read the
committed 2024 rehearsal only.

| Item | Command / evidence | Result | Pass |
|---|---|---|---|
| Rule unchanged | the diff touches only `research/eval/recent_role_full_eval.py`, tests and the rehearsal artefacts (`git show --stat 4d7a98b`) | no rule, threshold, BH or CI change; decide_stage2 untouched | PASS |
| B1 hand-off | `if __name__ == "__main__"`: `import research.eval.recent_role_full_eval as _E; raise SystemExit(_E.main())`; test `test_cli_path_as_documented_reaches_the_cap` runs `runpy.run_module(..., run_name="__main__")` and calls the real `collect_heads` under the cap. Removing the hand-off makes the `__main__` copy run unpatched refusals, so the test fails | the cap reaches the running code | PASS |
| B1 preflight | `assert_cap(target)` is the first statement inside `cap_ctx`, before the marker and before any build. It reads `MAX_SEASON` from the `__globals__` of collect_heads, collect_pi, WE.assert_seasons, F.load_study/load_extra, G.load_played and the `CRE.load_inputs` default | PASS |
| Coverage of the 2025 frame build (asked) | scratch `probe_cap2.py` (imports only, exit 0): after `preimport()` the lazily imported modules on the build path (`game_location_context`, `research.workload.asof`, `research.recent_role.placebo`) are already loaded. All 12 loaded modules that carry `MAX_SEASON` are moved by `season_cap(2099)` and restored on exit (stale = {}, restored = {}). They are context_residual_eval, lower_tail_eval, rookie_minutes, f11_lineup_context, four_factors_eval, minutes_hazard_eval, minutes_v2_eval, pi_recent_role_eval, pplay_context_eval, recent_role_full_eval, workload_eval and workload_gates. `load_inputs(db_path, injury_db, max_season=2024)` has its default moved. A grep for hard-coded `202[45]` season literals in every loaded `nba.*` / `research.*` module finds only docstrings, error-message strings and constants that are not SQL bounds. | yes: `preimport` + `season_cap` cover every loader the `PR.build_frame` path touches (F.load_study -> load_inputs/load_extra, WE.load_sources -> G.load_games/load_played, W.player_load_features, PB.permute_past_only). The backstop is the data gate (>= 25,000 rows, >= 1,000 games), which now consumes the touch if it fires | PASS |
| M1 | `arm_coverage` builds each arm through its own heads and `pi` (test `test_arm_coverage_scores_full_with_the_full_heads`). Gate A replays Stage 1's PIT uniforms (`n_prefix`) | rehearsal: A0 and FULL coverage reproduce Stage 1 with max abs diff **0.0** (n 27,701). In 2025, A0 and FULL share the same uniforms (seed 0, same length), so the coverage comparison is paired | PASS |
| M2 | marker first inside the cap with HEAD, season, rule sha, timestamp (test `test_touch_marker_is_the_first_write_inside_the_cap_and_carries_head`); gate-B and data-gate stops write `touch_consumed: true` | PASS |
| m1 | `require_stage1_pass` in `main` and `stage2` before the cap; test | PASS |
| m2 | gate compares to the stored exact value at 1e-9: abs diff **0.0** (FULL-A0 -0.0083144764, CI [-0.01275, -0.00408], n 27,701 rows / 1,320 games) | PASS |
| m3 | `PLANTS_NOTE` in results and report | PASS |
| Tests | `.venv/bin/python -m pytest -q tests/research/recent_role_full/ -p no:cacheprovider --no-cov` | 29 passed, exit 0 | PASS |

Unchanged from the first review, to be read with the result. The shuffle guard's between-permutation spread (~0.002) gives roughly
a 4-17% chance of a spurious fire, which can only fail the run, never pass it. A shuffle-only NOT A PASS is final under the frozen
rule.

Non-blocking nit: add `nba.props.rookie_minutes` to `assert_cap`'s list for symmetry. `season_cap` already moves it (probe) and
nothing in the walk reads it directly.

Not verified: behaviour on real 2025 rows (by design); LightGBM determinism across a fresh process for 2025 (2024 reproduced to
0.0 in two builds, which is the best available evidence).

Drafted ledger row (not appended):
```
| T471 | Adversary re-review of the RECENT_ROLE_FULL Stage-2 loader fixes 4d7a98b (docs/reviews/recent_role_full_stage2_loader_review_2026-10-12.md) | APPROVED. B1: CLI hands off to the imported module; assert_cap before marker and loads; runpy CLI test. Cap probe (imports only): all 12 MAX_SEASON-bearing loaded modules moved and restored, lazy build modules preloaded, no SQL season literals. M1: FULL coverage on FULL heads, gate replays Stage 1 PIT, diff 0.0. M2: touch marker first inside the cap, stops consume the touch. 2024 rehearsal FULL-A0 -0.0083144764 [-0.01275, -0.00408] exact (0.0), n 27,701 / 1,320 games. 29 tests pass. Shuffle spurious-fire risk ~4-17% (conservative only) |
```
