# Lane E2a: live daily / props cleanups (2026-11-16/17 overnight)

Branch `agent/steward-1117-live-daily`, worktree `/Users/devin/Downloads/nba-wt-1117-live-daily`.
No production data linked, no DuckDB opened outside tests, no replay run (the main session runs it).

## ACTION NEEDED (main session)

1. Run the replay oracle on the branch tip; golden `89889f6d`. Commits that touch live-path code:
   `f20c535a` (lead constant; `nba/daily/predict.py` on the live path) and `fe5c7efb` (pplay shadow
   config path). The others are tests or docstrings.
2. `tests/shadow_llm/test_runner.py::test_mixed_forward_table_still_settles` FAILS on the branch
   base too (verified by stashing this lane's edits: same failure, `assert 0 > 0` on
   `prop_scored`). Pre-existing, not caused here, not chased.

## Per task

### 1. One 60-minute lead constant: DONE, `f20c535a`

New `nba/leadtime.py` (stdlib only): `LEAD_MINUTES: int = 60`, `LEAD: timedelta = timedelta(minutes=60)`.

Every T-60 lead definition found under `nba/` (AST scan + grep for `timedelta(minutes=60)`,
`*LEAD* = 60`, `lead*=60` defaults, `60 * INTERVAL`, `3600`):

| site | before | decision |
|---|---|---|
| `nba/daily/predict.py:49` | `REPORT_LEAD_MINUTES = 60` | changed: `= LEAD_MINUTES` (int) |
| `nba/explain/data.py:30` | `LEAD_MINUTES = 60` | changed: `= _LEAD_MINUTES` (int, explicit module name kept for `slate.py`'s `D.LEAD_MINUTES`) |
| `nba/shadow_llm/payload.py:36` | `LEAD_MINUTES = 60` | changed: `= _LEAD_MINUTES` (int) |
| `nba/daily/avg_shadow/display.py:17` | `LEAD = 60` | changed: `= LEAD_MINUTES` (int) |
| `nba/daily/combo_shadow/display.py:17` | `LEAD = 60` | changed: `= LEAD_MINUTES` (int) |
| `nba/lineups/analysis.py:28` | `T60 = timedelta(minutes=60)` | changed: `= _PROD_LEAD` (timedelta; imported under an alias because this module's own `LEAD` is the T-30 lead; the first attempt shadowed it and the new test caught T60 = 30 min before commit) |
| `nba/daily/settle.py:261` | `DEFAULT_LEAD_MINUTES = 60` | NOT edited: frozen scoring code (FORWARD_PREREG rule 3); tied by test |
| `nba/daily/{avg,combo,full,pplay}_shadow/settle.py` (18, 20, 34, 18) | `LEAD_MINUTES = 60` | NOT edited: settle code of frozen shadow rules; tied by test |
| `nba/eval/{avg,full,combo}_shadow_live.py` (49, 34, 39) | `LEAD_MINUTES = 60` | NOT edited: frozen readers ("the reader is the rule's text"); tied by test |
| `nba/features/injury_report.py:75` | `lead_minutes: int = 60` | NOT edited: sha-pinned by research rules (f1_warm_window, injury_timing, replacement_delta, wowy_struct); tied by test |
| `nba/models/injury_elo.py:88` | `lead_minutes: int = 60` | other lane AND sha-pinned (wowy_struct pin list); leave; tied by test |
| `nba/ingest/reporter_tick.py:430` | `tip - timedelta(minutes=60)` | other lane: replace with `tip - LEAD` and add `from nba.leadtime import LEAD` |
| `nba/odds/combo_pull.py:563` | `return timedelta(minutes=60)` (in `_t60`) | other lane: `from nba.leadtime import LEAD` (lazy, like the existing import) and `return LEAD` |
| `nba/data/report_freshness.py:39` | `def freshness(con, lead_minutes: int = 60)` | other lane: `lead_minutes: int = LEAD_MINUTES` with `from nba.leadtime import LEAD_MINUTES` |
| `nba/explain/known.py:24` | `CUTOFFS = (60, 30)` | left: a (T-60, T-30) display pair; T-30 logic out of scope |
| `nba/daily/replay_season.py:116`, `nba/ops/watchdog.py:950` | `timedelta(hours=1)` | unrelated (run-slot hour; drill heartbeat duration), allow-listed |

Guard: `tests/test_leadtime.py` (31 tests): the constant's value and type; every alias above equal
in value AND type; both dataclass defaults; an AST scan with an allow-list that fails both ways
(new literal, or stale entry). When another lane applies its replacement, it removes that row from
`ALLOWED` in the same change.

### 2. `configs/context_residual.yaml` vs code defaults: EQUAL, path made absolute, `fe5c7efb`

| field | YAML | `ContextResidualConfig` default | type |
|---|---|---|---|
| n_estimators | 200 | 200 | int |
| learning_rate | 0.04 | 0.04 | float |
| num_leaves | 15 | 15 | int |
| min_child_samples | 100 | 100 | int |
| feature_fraction | 0.8 | 0.8 | float |
| bagging_fraction | 0.8 | 0.8 | float |
| cal_frac | 0.15 | 0.15 | float |
| min_cal_rows | 2000 | 2000 | int |
| seed | 0 | 0 | int |
| n_jobs | 4 | 4 | int |

Fields only in code (not in YAML, so the shadow gets the same defaults): `integer_support`,
`integer_support_stats`, `lower_tail`, `lower_tail_stats`, `young_pick`, `report`.
`load_cfg() == ContextResidualConfig()` holds. `nba/daily/pplay_shadow/model.py` `CONFIG` now resolves
from the module file (`parents[3] / "configs" / "context_residual.yaml"`), the same file whenever the
job runs from the repo root. Production still reads the dataclass defaults; the YAML (sha-pinned by
research rules) is untouched. Guard: `tests/daily/test_context_residual_config.py` (13 tests,
including a run from another cwd). Other cwd-relative `configs/context_residual.yaml` defaults remain
in `nba/eval/{context_residual_eval,lower_tail_eval,f9_young_pick,f9b_young_pick}.py` (research CLIs,
pinned or prereg-named; left).

### 3. Forward-table DDL in one place: DIFFERENT, code left alone, test `c2385ad1`

Compared through `PRAGMA table_info` (order, name, type, NOT NULL, default, pk) after executing each DDL:
`forward_predictions` and `forward_scores` are identical in `nba/db/schema.sql` and
`nba/daily/store.py`; `player_availability` is identical in schema.sql,
`nba/ingest/availability.py::ensure_player_availability_table` and `nba/ingest/injury_intraday._REPORTS_DDL`.
Difference: `forward_withdrawals` exists only in `store.py`. Per the brief no source was chosen;
`tests/db/test_forward_ddl_parity.py`: 5 pass + the table-set test `xfail(strict=True)`, with a
sibling test pinning `forward_withdrawals` as the only difference.

### 4. Stubs marked: DONE (docstrings only), `da3ea72b`

| item | evidence | decision |
|---|---|---|
| `nba/coldstart/` (`shrinkage`, `sb_classification` shims) | imported by `research/features/possession_features.py`; not in the live closure (prefix in `RESEARCH_PREFIXES`, `KNOWN_EDGES` empty) | kept; `STUB: not used by the live path (2026-11-17)` in `__init__` |
| `nba/shared/version.py` | read only by `tests/shared/test_version.py` | kept; STUB line in `__init__` |
| `nba/sim/usage_redistribution.py` | pickle-compat shim: pre-2026-10-10 model caches unpickle `ReportTriggerConfig` by this path (invisible to the import graph) | untouched; "not used by the live path" would be false. Deletable once every cache post-dates 10-10 (needs a data check, Devin's) |
| `PossessionSimEngine` (`nba/parlay/engines.py:69`) | reachable: `make_engine("possession_sim")`, `ENGINE_NAMES`, `configs/parlay.yaml` option; `nba.parlay.__main__` is a live entrypoint | other lane; proposal: add `STUB: not used by the live path (2026-11-17); raises NotImplementedError without a sampler.` to its class docstring |

### 5. Finished research out of `nba/`: NOTHING MOVED (proposal)

| file | live closure | pinned / prereg-named | decision |
|---|---|---|---|
| `nba/props/wowy.py`, `wowy_repl.py`, `repl_delta.py`, `struct_alloc.py` | no | yes: `research/archive/wowy_struct/wowy_struct_rule.py` pin list; `research/eval/replacement_delta_rule.py`; preregs WOWY_STRUCT, WOWY_REDISTRIBUTION, REPLACEMENT_DELTA | stay |
| `nba/eval/avg_shadow_live.py`, `full_shadow_live.py`, `combo_shadow_live.py`, `q1_starters_live.py` | no (run by hand at looks) | named scorers of frozen forward preregs | stay (brief: keep `*_live.py`) |
| `nba/eval/context_residual_eval.py`, `lower_tail_eval.py` | no | sha-pinned by 4 research rules; imported by 80+ research modules | stay |
| `nba/eval/metrics.py`, `walkforward.py`, `injury_elo_eval.py` | no | KEEP per research/INDEX.md (Appendix A); imported by research and tests | stay |
| `nba/eval/f9_young_pick.py` (T184, stopped) | no | named in `docs/prereg/F9_YOUNG_PICK_PRIOR.md:132` | stay |
| `nba/eval/f9b_young_pick.py` (T186-T201, null) | no | ledger row names the path; imported by `research/eval/f11_lineup_context.py` | stay |
| `nba/eval/f12_closing_risk.py` (T185, stopped) | no | named in `docs/prereg/F12_RETEST.md:11` (`run_checks` call), `reports/F12.md` run command | stay |
| `nba/eval/f12b_closing_risk.py`, `f12b_pooled.py` (T523) | no | named in preregs F12B_CLOSING_RISK / F12B_POOLED | stay |
| `nba/eval/integer_quantiles_eval.py` (T124-T127) | no | run command in `docs/INTEGER_QUANTILES.md:127` (prereg sha ac87bd71) | stay |

Proposal: the F9, F9b, F12 and INTEGER_QUANTILES harnesses could move to `research/eval/` with
their tests if Devin accepts that the frozen docs' run commands then point at an old path (add one
line to `research/INDEX.md` per move). Not done: a frozen document naming the path counts as a pin.

### 6. Split `nba/daily/pipeline.py`: SKIPPED (proposal)

Coverage measured (after 22:00): `pytest tests/daily --cov=nba.daily.pipeline`: 365 passed, exit 0,
`nba/daily/pipeline.py` 89% (410 statements, 39 missed, 15 partial branches; 4 min 22 s).
Coverage is real, but that did not settle it.

Why skipped: the two natural seams are exactly what tests patch on the `pipeline` module.
`tests/daily/test_combo_shadow.py:556` patches `pipeline._wide_roster_frame`, which the shadow wrappers
`_full_shadow`/`_combo_shadow`/`_pplay_shadow` call (lines 411, 529, 578); `tests/daily/test_review_fixes.py:114`
patches `pipeline._frame_preds`, which `_context_props`/`_recency_only_props` call (997-1090). Moving
either group makes the patch miss under test. Plan for a supervised session: (a) move
`_full_shadow`, `_avg_shadow`, `_combo_shadow`, `_pplay_shadow`, `_wide_roster_frame`, `_WIDE_CONFIG`
to `nba/daily/shadow_hooks.py`, re-export from `pipeline`, and change the one test patch target to
the new module in the same commit; (b) `_context_props`, `_recency_only_props`, `_frame_preds`,
`_additive_fields`, `_injury_frame` to `nba/daily/props_rows.py` likewise; (c) `_official_roster`,
`_withdraw_misplaced`, `_schedule_with_fallback` to `nba/daily/slate.py`. One commit each, replay per step.

### 7. Doc-comment drift: DONE, `e34e5fe7`

- `nba/daily/store.py` module docstring said the forward DDL is "Deliberately NOT in schema.sql";
  schema.sql declares two of the three tables. Rewritten to say which and point to the parity test.
- Seen, not edited (frozen or pinned): `nba/daily/settle.py:239` comment lists only the 60 and T-30
  leads (T-15 and late arms exist); `nba/props/context_residual.py:11` and `nba/models/injury_elo.py:20,590`
  still cite `nba.sim.usage_redistribution` for code now in `nba.features.injury_report`.

## Commits for the oracle (in order)

1. `f20c535a` leadtime constant (live path: `nba/daily/predict.py`; oracle-relevant)
2. `fe5c7efb` pplay shadow config path (shadow tables only; oracle-relevant by caution)
3. `c2385ad1` DDL parity test (tests only)
4. `da3ea72b` coldstart/shared STUB docstrings
5. `e34e5fe7` store.py docstring (comment only)
6. this report

## Verification (unpiped, exit codes read from files)

- `ruff check` / `ruff format --check` / `mypy` on all 14 changed .py files: 0 / 0 / 0.
- `pytest tests/test_layering.py --no-cov -q`: 10 passed, exit 0.
- `pytest tests/daily` (with pipeline coverage): 365 passed, exit 0.
- `pytest tests/test_leadtime.py tests/db tests/explain tests/lineups tests/shadow_llm tests/shared
  tests/ml/test_sb_classification.py tests/eval`: 503 passed, 1 skipped, 1 xfailed, 1 failed
  (the pre-existing `test_mixed_forward_table_still_settles`), exit 1; 7 min 34 s.

## Decisions (what / why / what would reverse it)

1. Frozen settle/scorer files and sha-pinned files keep their literal 60 and are tied to the
   constant by a test, not edited / editing frozen rule code is out of bounds even when
   value-identical / Devin calling a value-identical import acceptable in frozen code.
2. A frozen prereg or ledger row naming a module path counts as a pin, so nothing in Task 5
   moved / moving would orphan run commands in documents nobody may edit / Devin accepting stale
   paths in frozen docs.
3. Task 6 skipped / the seams are patch targets; a move changes what the tests exercise / a
   supervised session that moves the patch targets together with the code.
4. `nba/sim` left without a STUB line / pickle can import it at runtime / a data check showing no
   cache older than 2026-10-10.

## Not verified

- Replay oracle (main session). The full 5,700-test suite was not run by this lane.
- `make ci`-level mypy on the whole tree (mypy ran on each changed file, all clean).
- One pytest run started while 3 other pytest processes were alive (the check and the run were in
  the same shell command); it completed normally.
