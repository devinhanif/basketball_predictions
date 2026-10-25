# MARKET_ANCHORED_PROPS: eval harness checked against the frozen text before `run` (adversary, 2026-10-24)

Verdict: BLOCKED

**Return: NOT_READY.** The harness implements the frozen decomposition, words, precedence, K1, BH family and refusals
correctly. I re-derived every one of them from the code. Four run-integrity fixes are needed before the harness pin
commit, and none of them changes a rule constant:
- F1: ISO weeks are hard-coded, not left as a run-time choice.
- F2: the run never opens the odds database.
- F3: `model_rows` is pinned.
- F4: a failure in a descriptive input cannot void or crash the primary result.

After that, a diff-only re-check, then the pin, then `run`. The usual SURVIVES / WOUNDED / BROKEN verdict does not apply
yet: no result exists.

- Rule: `docs/prereg/MARKET_ANCHORED_PROPS.md` on main. The text above the marker hashes to
  `f7691bbfade479ef2400faf43820b5e5923c483d9de52d1c4423f50c15aa0091` (the rule's own `awk ... | shasum -a 256`, exit 0).
  Freeze commit `f52a974`.
- Harness: `agent/modeler-map-eval` at `87a2073`, checked out detached in a scratch worktree. `data` is a symlink to
  the main checkout's `data/`, verified with `ls -la data`
  (`data -> /Users/devin/Downloads/nba-prediction/data`).
- Scope: I did not execute `run` and read no real outcome. I loaded no season-2025 row and opened no DuckDB file myself;
  the dryrun I re-ran opens only the builder's byte copy, read-only. All of my checks were label-free: schemas, game-id
  prefixes, dates, leg and week counts, and a re-run of the label-free dryrun reproduction.

## 1. Rulings on the builder's ambiguities

I received the numbers and topics of #1, #2, #3, #5 and #15, but not the text of the other eleven. Those five are ruled
here. Rulings R6-R14 cover every other choice I found in the harness where the text leaves room. The main session should
map them to the builder's list. Any item on that list not covered below comes back for a diff-only ruling before the pin.

| # | question | ruling | why |
|---|---|---|---|
| #1 | Week clustering: the text says ISO; the core and the gate used `datetime64[W]` | **ISO binds. `run` must not offer a choice (F1).** | The text wins (rule docstring line 4; CLAUDE.md). It says ISO twice: section 4 ("ISO week") and section 14 ("`iso_year-week` of `game_date`"). The precedent it inherits, `market_prior_residual_rule.cluster_codes`, uses true ISO weeks (`dt.iso_year()` / `dt.week()`). The core's weeks are a bug: core line 244 names the variable `iso`, but numpy weeks are counted from 1970-01-01, a Thursday, so they run Thursday to Wednesday. I re-derived this: `2024-10-23 Wed -> 2859`, `2024-10-24 Thu -> 2860`. The gate only measured that the data suffice, and ISO passes the same bars. G2 under ISO is 35 / 35 weeks against a bar of >= 20 (core weeks gave 35 / 36), re-derived label-free (command C3). G8's binding half-width of 9e-05 is about 11 times below its 0.001 bar, and moving one week boundary cannot close that gap. So no gate re-measure is needed. A run-time `--week` flag is a forking path: two envelopes, one look each. Hard-code ISO in `run`. Keep `core` only inside the dryrun, where the gate is reproduced as measured. |
| #2 | Part L and the exchange slice need an odds-DB copy; the text says the run never opens the odds database | **The run never opens the odds DB. Part L and exchange inputs are extracted label-free into pinned files before the pin (F2).** | Rule line 17 is unconditional: "The run reads only those files ... it never opens the odds database." Section 4 also requires Part L and the exchange slice, which are descriptive with no pass role. Both lines are satisfied if a new label-free `extract` subcommand runs `load_rungs` / `load_exchange` on a byte copy once, before the pin, and writes `reports/prereg_market_anchored_props/{part_l_rungs,exchange_quotes}.parquet`. Their sha256 go into `market_anchored_props_rule.py` and are covered by the harness pin. `run` reads only those files and refuses a mismatch before any label is read. The extract also reports its counts against section 8's Part L table (rungs / player-games / games) and section 9's exchange counts, as a reconciliation, not a gate. Today's rehearsal has 210,434 rungs; section 8's table sums to 212,712. The difference is the played / known-at / duplicate filters, and the extract should state that. **Safe default if F2 is not built:** run without the odds copy, record Part L and the exchange slice as `NOT_COMPUTED`, and add a DECISIONS row naming the omission. Never pass `--odds-copy` to `run`. |
| #3 | `model_rows_{2023,2024}.parquet` is not pinned | **Pin it now, pre-look (F3).** | It feeds `min10` (slices), `minutes` (the missingness split) and `p_ge` / `y` (Part L). No word depends on it. It is git-ignored (`.gitignore:5 *.parquet`) and dated 2026-10-10. Today's bytes are `002e53ef1a5fae333e8ff6edc92654de9f449d29c9be1775017e65fc378eea85` (2023) and `ec47dcdf566eac29742f8269b8c3cf4a10845440c9b298b41a40282577b62d70` (2024), equal to the values the dryrun recorded. Add `MODEL_ROWS_PINNED_SHA256` to the rule mirror and check it next to the pairs and extras pins, before `load_labels`. Today `model_rows_sha()` only records the hash (eval line 1033). The tie to the pinned pairs is already shown label-free: `p_model_vintage` share 1.0, max diff 0.0, n 72,674. Also take Part L's rung outcome from the pinned pairs' `y` (same KEY), not from `model_rows.y`, or report their agreement on the primary legs as a descriptive line (R13). |
| #5 | Harness pin commit | **ALT_RUNG_CAL precedent (`ff040aa`, an empty commit whose subject is the pin).** | Order: (1) F1-F4 land on `agent/modeler-map-eval`. (2) Diff-only adversary re-check. (3) The main session runs `.venv/bin/python -m research.eval.market_anchored_props_rule pin` on that clean tree and commits `harness(MARKET_ANCHORED_PROPS): <pin>` exactly (`git commit --allow-empty`, nothing else in the subject; `pin_commit` uses `re.fullmatch`). (4) It confirms `git merge-base --is-ancestor f52a974 <pin>` (true for `87a2073`). (5) `run` starts from a clean checkout of that commit. `HARNESS_FILES` must include the new pin constants (they live in `market_anchored_props_rule.py`, which is already listed). I checked that the five frozen-era files have identical blobs at `f52a974`, `87a2073` and `main` (C2). |
| #15 | The scored-count check turns a mismatch into an invalid run | **Allowed, as part of leg reproduction. Move it before labels (F5).** | Section 6 states 28,108 and 37,528 as facts. `core.walk_forward` sets `scored` from `len(tr) >= N_MIN_TRAIN` alone (core line 405), so the count is label-free and only leg drift can break it. It is a leg-reproduction failure under the text's `NO_RESULT_INVALID_RUN` list. Today it fires only after Stage 1 has fitted real labels (eval line 930). Compute it from `A` (months with >= 3,000 prior legs) right after `load_legs_checked`, so a mismatch refuses before any label is read. |

| # | further harness choice | ruling |
|---|---|---|
| R6 | Planted future in `run` on real labels, test month `2025-01` | **Allowed.** `2025-01` is January of season 2024 (2024-25), not the holdout. Label-free check (C4): `pairs_2024` rows dated 2025-01 have `season == [2024]` and game-id prefix `00224` only (75,462 pair rows, 6,552 legs). Kill criterion (2) in section 6 requires the planted future "in the run". The flip touches only `A`, which holds seasons 2023-2024. The check fits 2024 months before K1 is known, but it computes no loss and records only prediction differences, so "2024 is not scored" holds. |
| R7 | Missingness written before any component | **Complies.** In `run` (lines 1036-1039), `load_labels`, then `missingness`, then `missingness.json` is written, then the planted future, then `evaluate`. The text sets no stop threshold, so the table is reported first and the run goes on. The missingness file survives an invalid run. That is accepted: it is the audit the text orders first, not a component. |
| R8 | K1 stops Stage 2 | **Complies.** `evaluate` returns at line 932 before `derangements` and `stage2`. The rehearsal shows `stage2_scored: false`. `k1_word` uses strict `>`, matching "above +0.0010", and the boundary is unit-tested. |
| R9 | Precedence and NOT_EVALUATED | **Complies.** `words()` orders invalid, then K1, then `core.verdict` (WORSE first inside it), then the two words. Each replacement returns `NOT_EVALUATED` / `NOT_EVALUATED`, never `NONE`. `consequences` sets `evaluated: false` and all proposals false when replaced. I read both word ladders against section 4 line by line, and both match, including the strict / non-strict inequalities: `hi >= 0` gives NONE, select `mean >= 0` gives FAIL, `<=` floors, and the market guard `c_aft["hi"] < 0` for INFO only. |
| R10 | BH over 10 | **Complies.** There are exactly 10 tests, asserted at eval line 392: C_mod by stat (4), C_mkt by stat (4), C_mod on bench, C_mod on heavy depletion. The p-value is the gain tail `(1 + #{bm >= 0}) / (B + 1)`, max over the three clusterings (`core.gain_p`), and the tail is pinned by a hand-recompute test. `mrule.bh_adjust` is a correct monotone BH step-up. The family actually run equals the family declared. |
| R11 | `build_extras` cannot run | **Complies.** No call site in `run`. `grep` finds only `gate.py:220` behind `--rebuild-extras`. The dryrun passes `rebuild_extras=False` and monkeypatches `core.build_extras` to raise. |
| R12 | Season 2025 can never be read | **Complies.** `assert_seasons` runs at every entry. Input files are per-season, and C4 shows `pairs_{2023,2024}` with seasons `[2023]` / `[2024]` and date maxima 2024-06-17 / 2025-06-22, and `model_rows` and `extras` with game-id season digits `23` / `24` only. The SQL filters are `season = 2023/2024`. Weakness: core line 182 is a bare `assert`, which `python -O` strips. The run is never invoked with `-O`. Minor, and the core is frozen, so no edit. |
| R13 | Part L rung outcome from unpinned `model_rows.y` | Take it from the pinned pairs `y` (one value per KEY), or report the agreement share of `(model_rows.y >= line + 0.5) == over` on the primary legs. Descriptive only. |
| R14 | Planted K1 / WORSE worlds (the WORSE redesign) | **Accepted as plumbing tests only; the redesign does make the test easier.** The reversal world (+1.0 x delta in 2023, -1.0 in 2024) gives a 2024 C_total of +0.0150 [+0.0068, +0.0243], 30 times `WORSE_FLOOR`. The K1 world gives a 2023 C_total of +0.0108, about 11 times the limit. Both prove that the override replaces both words, that consequences switch off, and whether Stage 2 runs. Neither says anything about the threshold. The threshold is two lines of code (core line 545), already checked by reading. Its power comes from the gate: the binding half-width of C_total under the null is 0.00019, so WORSE fires about half the time at a true C_total near +0.0007. The redesign must never be cited as evidence that WORSE is sensitive. Add boundary unit tests (F6). |

## 2. Required fixes (blockers before the pin)

- **F1 (blocker): ISO weeks only.**
  - Eval lines 1291-1296: delete the `--week` argument.
  - Line 1014: `run(sha, odds_copy, ...)` calls `with_week(A0, "iso")` unconditionally.
  - `base["week_definition"] = "iso (rule sections 4, 14)"`.
  - Test: `run`'s parser has no `--week`, and `with_week(A, "iso")` is the path `run` takes. Monkeypatch the loaders and assert `A["week_idx"]` equals `iso_week_idx(A["date"])`.
- **F2 (blocker): the run never opens the odds database.**
  - Delete `--odds-copy` from `run` (lines 1297-1301) and the `if odds_copy` block (lines 1048-1058).
  - Add an `extract --db <copy>` subcommand. It refuses the live file, as `load_rungs` already does, and writes the rungs frame (`load_rungs` output) and the exchange frame (`load_exchange` output) as sorted zstd parquet under `reports/prereg_market_anchored_props/`, committed, with no label column.
  - Add `PART_L_RUNGS_SHA256` / `EXCHANGE_SHA256` to `market_anchored_props_rule.py`.
  - In `run`, hash the files before `load_labels` and refuse on a mismatch (InvalidRun, a pin failure).
  - Test: a tampered pin raises before any label read.
  - Or take the safe default in R2 (#2).
- **F3 (blocker): pin `model_rows`.**
  - Add `MODEL_ROWS_PINNED_SHA256 = {2023: "002e53ef...eea85", 2024: "ec47dcdf...62d70"}` (full values in R3 above) to the rule mirror.
  - Add `check_model_rows_pins()`, raising InvalidRun, called right after `check_extras_pins()` at eval line 1030, before `aux_columns` and `load_labels`.
  - Test: a tampered pin raises.
- **F4 (blocker): isolate the descriptive outputs.**
  - Line 1045-1046: the DraftKings legs and their labels are loaded before the primary, and `load_labels(dk_legs, "draftkings")` raises `InvalidRun` on any DK leg without a label. A missing descriptive label would then void the primary (`NO_RESULT_INVALID_RUN`). The text lists only the planted future, leg reproduction and pin failures as invalidating.
  - Any non-`InvalidRun` exception in `evaluate`'s descriptive part leaves no `results.json`. Candidates: the `RuntimeError` in `refit_b0_md` (line 446), the `assert` in `_join_aligned` (line 148), a polars error in the slices or Part L. The stage logs would still have shown whether Stage 2 ran, and a re-run would need a harness change after a partial look.
  - Fix: split `evaluate` into a primary part (Stage 1, K1, Stage 2, words, consequences, identity check, placebo draws, secondary BH family). Write `results.json` with `"descriptive": "PENDING"` immediately after it. Run each descriptive block (coefficients, Brier, slices, top five, DK, Part L, exchange) in its own `try/except Exception`, recording `{"status": "FAILED", "error": repr(e)}`. Rewrite `results.json` at the end.
  - DK labels are loaded inside the DK block, and a missing DK label is recorded there, not raised.
  - Test: a monkeypatched DK loader that raises leaves the words and the primary intact and records `FAILED` for DK.
- **F5 (major): scored count before labels.** Compute `n_scored` label-free from `A` for both stages right after `load_legs_checked`. Raise `InvalidRun` on a mismatch with `R.SCORED` before `load_labels`. Keep the in-stage check as an assertion.

## 3. Recommended (not blocking)

- **F6 (minor):** boundary tests for WORSE. Mean `0.0005` with lo `1e-9` fires; mean `0.0005` with lo `0.0` does not; mean `0.000499` with lo > 0 does not.
- **F7 (minor):** mypy `no-any-return` at eval lines 688 and 828 under the current toolchain. The frozen core line 127 and nine other repo files show the same error, so this is environment drift, not new code. Wrap the result in `pl.DataFrame(...)` or `cast`.
- **F8 (ops):** the dryrun took 5,808 s. The rehearsal ran the full pipeline at K = 50 / B = 2,000 plus two small evaluates. Section 15's "5-10 minutes" is wrong for `run`. Expect roughly an hour holding `heavy.lock`, and start it outside the nightly and morning windows. This is a commands note, not a rule change.
- **F9 (minor):** a run-path test showing `missingness.json` exists before `stage1` is called. Monkeypatch `evaluate` to assert the file exists.
- **F10 (note):** the DraftKings direction check's `u1` uses the soft-book set that includes DraftKings itself (extras exclude only Pinnacle), so `u1` is pulled toward DK's own price. Sign only, descriptive. Disclose it in the result note.

## 4. Per-check table (all label-free; exit codes shown; nothing piped through tail or head)

| id | check | command | n | result | pass |
|---|---|---|---|---|---|
| C1 | frozen hash | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' <main:docs/prereg/MARKET_ANCHORED_PROPS.md> \| shasum -a 256` | 292 lines | `f7691bbf...0091`, exit 0 | yes |
| C2 | frozen-era harness unchanged | `git rev-parse {f52a974,87a2073,main}:<file>` for core, gate, mpr eval, mpr rule, model_clv_eval, gate.json, rule doc | 7 files | identical blobs on all three; `merge-base --is-ancestor f52a974 87a2073` exit 0 | yes |
| C3 | week definitions | `PYTHONPATH=$PWD .venv/bin/python -W ignore weeks_check.py` (core `load_legs`, label-free) | 72,674 legs | 2023: core 35 / ISO 35; 2024: core 36 / ISO 35; core weeks start Thursday | ISO passes G2 (>= 20) |
| C4 | no 2025-26 rows; 2025-01 is season 2024 | `.venv/bin/python -I s2025_check.py <wt>` (columns game_id, season, game_date only) | pairs 425,921 / 429,951 rows | seasons [2023] / [2024]; game-id digits 23 / 24 in pairs, model_rows, extras; 2025-01 rows: season [2024], prefix 00224 | yes |
| C5 | model_rows bytes | `shasum -a 256 reports/odds_history/model_rows_202{3,4}.parquet` | 2 files | `002e53ef...`, `ec47dcdf...` = dryrun record; git-ignored | unpinned (F3) |
| C6 | tests | `PYTHONPATH=$PWD uv run --no-project --python .venv/bin/python --with pytest --with pytest-cov python -W ignore -m pytest -q tests/research/market_anchored_props/ --no-cov -p no:cacheprovider` | 41 tests | 41 passed, exit 0 | yes |
| C7 | lint / format | `uv run --no-project --with ruff ruff check <eval, rule, test_eval>`; `ruff format --check` | 3 files | exit 0; exit 0 | yes |
| C8 | types | `uv run --no-project --with mypy --with numpy --with polars==1.44.2 mypy <eval, rule> --ignore-missing-imports` | 2 files (+ imports) | exit 1, 17 errors, 2 in eval (688, 828), same pattern as frozen core:127 | minor (F7) |
| C9 | gate reproduction through the eval path | `PYTHONPATH=$PWD .venv/bin/python -W ignore -m research.eval.market_anchored_props_eval dryrun --db <builder's byte copy> --out <scratch> --no-rehearse` | 100 replicate worlds | REPRODUCED, 0 differences (all keys except seconds, git_head, data_capture); 364.8 s, exit 0; eval planted future equals gate G6: true; heavy.lock released | yes |
| C10 | words, precedence, K1, BH, refusals | line-by-line read of `core.verdict`, `core.run_words`, `eval.words`, `eval.evaluate`, `eval.run`, `eval.secondary_family`, `rule.require_*` against section 4-6, 14 | n/a | matches the text (R7-R12) | yes |
| C11 | planted K1 / WORSE / invalid (builder's dryrun.json) | read `reports/prereg_market_anchored_props/dryrun.json` | synthetic | KILLED_K1 (select +0.0108, Stage 2 not scored); WORSE_THAN_MARKET (2024 +0.0150 [+0.0068, +0.0243]); NO_RESULT_INVALID_RUN on a tampered pin; all pairs NOT_EVALUATED | plumbing yes; threshold not tested (R14) |

## 5. Family and power (from the frozen gate; no new numbers)
- Primary: one decomposition, two words; no correction between them (section 5). Secondary: 10 tests, BH 0.05. The family run equals the family declared (R10).
- Power: 37,528 scored legs; 1,260 games, 414 player-seasons, 35 ISO weeks (2024). The binding half-width of C_mod under the null is 9e-05 (gate G8, core weeks). With ISO weeks it may move by one cluster's share and stays far below the 0.001 information floor. C_mod detects an oracle gain of 0.0011 in 10 of 10 replicates (G7c). The smallest detectable C_mod is about -0.0002. WORSE becomes reachable near a true C_total of +0.0007.

## 6. Playbook check (docs/MODEL_PLAYBOOK.md; class: ridge logistic pool on a market offset, scored at the line)
1. **Information or capacity:** unchanged from the frozen text. This review only checks that the harness implements it; the 22-parameter capacity is fixed in the mirror (`check_mirror`: MR 15, MD 7, MM 22, B0 8).
2. **Enough data for the class:** yes. The leg counts are reproduced exactly (`check_leg_reproduction` against section 8), and the scored counts are fixed (F5 moves that check pre-label).
3. **Valid test:** the planted future equals G6 (6,552 rows, diffs 0 / 0 / 0.98785). The placebo has K = 50 with seeds 1000 + k. B = 2,000 with seed 0, three clusterings, envelope. The test stays valid only if (a) the week definition is fixed before the look (F1) and (b) every input is pinned (F2, F3). Both are open today, and that is why this review blocks.
4. **Lineage and inherited guards:** the T510 L-S2 precedent (adversary eval check before `run`) is being followed. The ALT_RUNG_CAL pin precedent applies (R5). The MPR rule's ISO cluster code is the inherited definition the core drifted from (R1).
5. **Scored against:** Pinnacle's de-vigged price (M) by log loss at the line. Brier is descriptive. No CRPS against the market.

## 7. The one biggest risk
A run that is valid on paper but un-repeatable in practice. Today a descriptive input (DK labels, a polars join, the
B0 / MD bit-for-bit refit) can fail after the labels are read and Stage 2's logs are printed. The result would be either
`NO_RESULT_INVALID_RUN` for a reason the text does not list, or a crash with no `results.json`. Either way the fix would
be a harness change made after a partial look. F4 removes this risk; F1-F3 remove the forking paths (two week
definitions, unpinned files).

## 8. Not verified
- The builder's list of 16 ambiguities: I had the numbers and topics of #1, #2, #3, #5 and #15 only. #4, #6-#14 and #16 are covered only insofar as they match R6-R14.
- The full dryrun rehearsal (5,808 s) was not re-run. I read its committed output. Only the gate-reproduction leg (C9) was re-run.
- Real-label behaviour of anything: the missingness table, the planted future on real labels, whether any DK leg lacks a label. None was read, by design.
- Whether `model_rows.y` agrees with the pairs `over` (needs labels; R13 makes the run report it).
- The transitive imports of `model_clv_eval` (`nba.eval.context_residual_eval`, `research.eval.f11_lineup_context`, `minutes_hazard_eval`, `odds_history_eval`) are not pinned. None is on the computation path (MAP uses `mrule.bh_adjust`, not the `nba` one), so only import side effects are exposed.

## 9. Drafted DECISIONS rows (pre-look; not appended; the main session appends after the fixes land)

| when (CDT) | what | why | reverses if |
|---|---|---|---|
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval, ruling #1 (pre-look): the week clustering is ISO (`iso_year-week` of `game_date`), hard-coded in `run`; core Thursday weeks are used only to reproduce gate.json in the dryrun. | Frozen text sections 4 and 14 say ISO; the inherited MPR `cluster_codes` is ISO; ISO passes G2 (35 / 35 >= 20) and G8 has an ~11x margin; a run-time choice is a forking path. Adversary eval review 2026-10-24. | Never after the look; before it, only a new rule id. |
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval, ruling #2 (pre-look): `run` never opens the odds database; Part L rungs and exchange quotes are extracted label-free from a byte copy into committed parquet pinned in the rule mirror before the harness pin (default if not built: both NOT_COMPUTED, never `--odds-copy`). | Rule line 17 ("never opens the odds database") and section 4 (Part L, exchange slice) are both satisfied only this way. | The extract cannot reproduce section 8's Part L counts within the stated filters: report it and use the NOT_COMPUTED default. |
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval, ruling #3 (pre-look): `reports/odds_history/model_rows_{2023,2024}.parquet` pinned at 002e53ef... / ec47dcdf... (git-ignored, dated 2026-10-10); refused on mismatch before labels; Part L outcomes from the pinned pairs `y`. | Unpinned inputs to slices, missingness and Part L. | n/a |
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval, ruling #5 (pre-look): harness pin by an empty commit `harness(MARKET_ANCHORED_PROPS): <pin>` (ALT_RUNG_CAL ff040aa precedent), after F1-F5 and a diff-only adversary re-check; `run` from a clean checkout of that commit. | `require_harness_pinned` contract; one look. | n/a |
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval, ruling #15 (pre-look): a scored-count mismatch (28,108 / 37,528) is a leg-reproduction failure (NO_RESULT_INVALID_RUN), checked label-free before any label is read. | `scored` depends only on leg counts per month (core line 405); section 6 states both counts. | n/a |
| 2026-10-24 01:24 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval (pre-look): the planted future in `run` uses real labels with test month 2025-01 = season 2024 (2024-25), allowed by section 6 kill criterion (2); descriptive failures (DK, Part L, exchange, slices, refit) are recorded per block and never void or crash the primary; planted K1 / WORSE worlds are plumbing tests, not evidence of threshold power. | Adversary eval review 2026-10-24, R6, F4, R14. | n/a |

---

## Re-check (diff only): `git diff 045b6b9 45cf843`, 2026-10-24 01:40 CDT

Verdict: APPROVED

**Return: READY_TO_PIN.** F1-F7, F9 and F10 are applied as specified, and the diff introduces no new contradiction with the
frozen text. I withdraw F8; the builder's measurement is right. Still not done here, by instruction: the pin commit and
`run`.

**Next steps for the main session, in order:**
1. On a clean checkout of `45cf843` (or any later commit that changes no file in `HARNESS_FILES` and no pinned
   artifact), run `.venv/bin/python -m research.eval.market_anchored_props_rule pin`.
2. Commit `git commit --allow-empty -m "harness(MARKET_ANCHORED_PROPS): <pin>"`.
3. Confirm `git merge-base --is-ancestor f52a974 <pin commit>`.
4. Run `run --sha f7691bbfade479ef2400faf43820b5e5923c483d9de52d1c4423f50c15aa0091` from that clean checkout.

### What the diff does, checked line by line
- **F1:** `--week` and `--odds-copy` are gone from the parser (`build_parser`). `run` calls `with_week(A0, "iso")`
  unconditionally and records `R.WEEK_DEFINITION`. The core's weeks remain only in the dryrun's gate reproduction.
  Tested by `test_run_parser_has_no_week_and_no_odds_copy` and `test_run_path_order_iso_weeks_and_isolated_descriptive`.
  **Applied.**
- **F2:** `run` no longer imports or opens DuckDB (the only `duckdb.connect` calls are in `load_rungs` and `load_exchange`,
  reached only from `extract`). `extract` refuses the live file and refuses to overwrite. It asserts that no `y` / `over`
  column is present and writes sorted zstd parquet. The pins are in `market_anchored_props_rule.py`, which `HARNESS_FILES`
  covers. `check_descriptive_pins` runs before `load_labels`. **Applied.**
- **F3:** `MODEL_ROWS_PINNED_SHA256` equals the two values I measured. `check_model_rows_pins` runs before `aux_columns` and
  the labels; a missing file or a mismatch raises `InvalidRun`. **Applied.**
- **F4:** `evaluate_primary` writes `results.json` through `on_words` as soon as the words exist, with
  `descriptive: PENDING`. Every descriptive block, and the secondary BH family, runs under `_block`, which catches
  `Exception` and records `FAILED`. DraftKings labels are read inside the DK block; a missing one drops that leg and is
  counted (`n_dk_legs_without_label`), never raised. The dryrun's planted DK failure gives words `NONE`/`NONE` intact,
  `draftkings_direction: FAILED`, `descriptive: DONE`. **Applied.**
- **F5:** `check_scored_counts(A)` runs label-free right after `load_legs_checked` and returns {2023: 28108, 2024: 37528}
  in the new dryrun. The in-stage check is now an `assert`. **Applied.**
- **F6:** `test_worse_than_market_boundaries` covers mean 0.0005 with lo 1e-9 (fires), lo 0.0 (does not fire), and mean
  0.000499 (does not fire). **Applied.**
- **F7:** mypy now shows 0 errors in `market_anchored_props_eval.py` or `market_anchored_props_rule.py`. The other 15 are
  pre-existing errors in other files, including the frozen core line 127. **Applied.**
- **F9:** the run-path test asserts that `missingness.json` exists before Stage 1. **Applied.**
- **F10:** `DK_NOTE` is written into the DK block's output. **Applied.**
- **R13:** Part L rung outcomes now come from the pinned pairs' `y`. Agreement of `(y >= line + 0.5)` with `over` on the
  primary legs is reported as `pairs_y_vs_over_agreement_on_legs` (descriptive). **Applied.**

### F8 (runtime): the builder is right, and F8 is withdrawn
In `dryrun2.log`, 45 `heavy.lock held` retries run from 23:34:36 to 01:04:36, which is 5,400 s of waiting. Compute ran
from 01:04:36 to 01:11:24, 408 s, for the gate reproduction plus the full rehearsal. My own reproduction-only re-run took
364.8 s, and the new dryrun reports 296.0 s. `run` performs one pipeline and no gate reproduction, so section 15's 5-10
minutes of compute is credible. The real risk is lock wait: start the run outside the nightly and morning windows. The
eval docstring now says this, and the frozen text is untouched.

### Rulings on the builder's items 1-12 (pre-look)
| # | item | ruling |
|---|---|---|
| 1 | FREEZE subject suffix accepted only if the hash matches | **Accept.** `freeze_commit` requires the subject's 64-hex value to equal `--sha` **and** the doc at that commit to hash to it. `f52a974`'s suffix ` FREEZE` passes only through that double check. |
| 2 | Slice components C_mod / C_mkt / C_total | **Accept.** Section 4 names the slices but not their contents. These three are the claim, the market component and the pool. C_pl is not sliced; it is a per-leg placebo with no slice role. Descriptive. |
| 3 | Tip-hour buckets ET < 19, 19, 20, >= 21 | **Accept.** Fixed label-free before the look and labelled "tip-hour slice, not freshness", as section 4 requires. |
| 4 | Top five = the five player-seasons with the most negative summed C_mod | **Accept.** Raw sums are reported next to the share, so a share computed on a positive total (no gain) is readable for what it is. |
| 5 | Exchange slice: novig / prophetx two-sided at Pinnacle's line, 2024 legs from 2024-12-19, scored legs; X - M plus C_mod / C_mkt / C_total | **Accept.** Matches section 4 (report only, no select season). |
| 6 | Part L: x.5 rungs only, multiplicative de-vig, V_cal on the B0 design (walk-forward, N_MIN_TRAIN 3,000 rungs, 365-day weights, LAM 50), distance buckets, clip 1e-6 | **Accept.** Each choice follows section 4 or carries a frozen constant. `n_anchor_undefined` is reported (0 in the rehearsal). |
| 7 | Brier arms M / B0 / MR / MD / MM | **Accept.** These are the section 3 arms. PL is omitted because it is the mean of K losses, not one probability. |
| 8 | Stale core docstrings ("rule section 5", "12 / 19 columns", the variable `iso` on Thursday weeks) | **Accept as disclosure; do not edit.** The core is frozen (blob at `f52a974`), and an edit would fail `require_harness_pinned`. The eval docstrings state the truth. |
| 9 | K1 suppresses every descriptive block | **Accept.** Section 6: K1 means "stop, 2024 is not scored, closed". The descriptive blocks are 2024 outputs; their 2023 halves have no role once the rule is closed. |
| 10 | Secondary BH family runs after the first results write, isolated | **Accept.** It is labels-only with no pass role. Isolating it guarantees that the words are on disk first. A `FAILED` family is recorded, not re-run. |
| 11 | Extract keeps only the primary legs' rungs and quotes; counts 0.6-1.3% below the section 8 table | **Accept.** Reconciled in `extract_manifest.json`. Game counts are identical in all 8 season-stat cells; rungs run 0.6-1.3% below. novig has 19,848 / 862 against 20,211 / 862; prophetx 7,852 / 776 against 7,966 / 779. The gap is the played / known-at / x.5 / unique filter. A reconciliation, not a gate. |
| 12 | A missing pinned file is a pin failure | **Accept.** Section 6 kill criterion (2) and the header ("refuses ... a missing pin"). Every such check runs before `load_labels`, so an `InvalidRun` at that point has seen no label. Restoring the pinned bytes and re-running is not a second look. Record it in DECISIONS if it happens. |

### Re-check table (label-free; exit codes shown)
| id | check | command | result | pass |
|---|---|---|---|---|
| D1 | extract bytes reproduce independently (4th extraction) | in my scratch worktree at `45cf843`: `PYTHONPATH=$PWD .venv/bin/python -W ignore -m research.eval.market_anchored_props_eval extract --db <builder's copy, sha 62f68cc0...> --overwrite`, then `shasum -a 256`, then `git checkout --` to restore | exit 0; `1412e09d...f278` and `befa3c30...b5ae`, identical to the pins; only the manifest timestamp and path differ | yes |
| D2 | extracts carry no label | `pyarrow.parquet.read_schema` | rungs 210,434 rows `[game_id, player_id, stat, point, price_over, price_under, line, p_over_mult, season, game_date]`; quotes 27,700 rows `[game_id, player_id, stat, book, po, pu]` (= 19,848 + 7,852) | yes |
| D3 | tests | `PYTHONPATH=$PWD uv run --no-project --python .venv/bin/python --with pytest --with pytest-cov python -W ignore -m pytest -q tests/research/market_anchored_props/ --no-cov -p no:cacheprovider` | 50 passed, exit 0 | yes |
| D4 | lint / format | `ruff check`, `ruff format --check` on eval, rule and both test files | exit 0 / exit 0 | yes |
| D5 | types | `uv run --no-project --with mypy --with numpy --with polars==1.44.2 mypy <eval, rule> --ignore-missing-imports` | exit 1, 15 errors, 0 in eval or rule (all pre-existing elsewhere) | yes (for this diff) |
| D6 | new dryrun.json | read the committed file | 296.0 s; gate REPRODUCED, 0 differences; scored counts {28108, 37528}; planted DK failure isolated | yes |
| D7 | lock-wait claim | `grep -c 'heavy.lock held' dryrun2.log` and the first / last compute lines | 45 waits; compute 01:04:36-01:11:24 (408 s) | yes |

### Residual risk (minor, not blocking)
An exception inside the primary itself, before `on_words` (for example a singular Newton solve in a fit), still leaves
no `results.json`. The Stage 2 log line would by then have shown that K1 did not fire. The fits were stable in 100 gate
worlds plus the rehearsal, so the risk is small. If it happens, record a DECISIONS row before any fix and treat the fix
as a new harness pin made after a partial look.

### Drafted DECISIONS rows (pre-look; not appended)
| when (CDT) | what | why | reverses if |
|---|---|---|---|
| 2026-10-24 01:40 (drafted; clock read by `date`) | MARKET_ANCHORED_PROPS eval re-check APPROVED (READY_TO_PIN) at `45cf843`: F1-F7, F9 and F10 applied; F8 withdrawn (5,808 s was 5,400 s of heavy.lock waits; compute is 296-408 s); extracts pinned at 1412e09d / befa3c30 and reproduced independently. | Adversary diff-only re-check, 2026-10-24. | Any file in `HARNESS_FILES` or any pinned artifact changes before the pin commit: re-check. |
| 2026-10-24 01:40 (drafted) | MARKET_ANCHORED_PROPS eval, builder items 1-12 accepted as listed in the re-check (FREEZE suffix with hash; slice components; tip-hour buckets; top-five ranking; exchange slice; Part L details; Brier arms; core docstrings disclosed, not edited; K1 suppresses descriptive; secondary BH isolated after the first write; extract counts reconciled; missing pinned file = pin failure, pre-label, re-runnable without a second look). | Fixed before any label is read. | n/a |
