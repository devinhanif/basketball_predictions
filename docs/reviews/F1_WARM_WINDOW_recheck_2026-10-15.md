# F1_WARM_WINDOW pre-freeze re-check (adversary, 2026-10-15)

Object: `origin/agent/modeler-f1-warm-window-2` at d08d886: `docs/prereg/F1_WARM_WINDOW.md`,
`research/eval/f1_warm_window_{rule,run,gate}.py`, `tests/research/f1_warm_window/`, `reports/prereg_f1_warm_window/gate.json`.
Read from a detached scratch worktree of d08d886; data root is the main checkout. The earlier review is
`origin/agent/adversary-f1-prefreeze` b02d403 (APPROVED WITH EDITS, 10 edits). Devin's answers are in DECISIONS 2026-10-15 17:28 and 17:30.
Nothing was fitted. No score, delta or label statistic was computed. Season 2025 was never read. Every DuckDB connection was `read_only=True`.
`check --smoke` was NOT run, because it fits A's first block on real labels and this re-check is no-fit.

Verdict: BLOCKED

Scope of the verdict: do not freeze sha `cebff083…2f8f` with pin `2ed1c59c…94df` as they stand. All 10 earlier edits hold, and the sha,
the pin, the gate and the ten input hashes all reproduce exactly. Two items still have to change before a freeze:
(B1) the rule voids itself under its own kill criterion, because the label-free smoke is a pre-freeze fit of arm A;
(B2) the stamp section claims the stamp offset "cannot create a false pass", and that claim is false. One eval-row channel does not cancel.
Both fixes are small. The re-check after them is a diff review of the listed lines plus the new test.

Top finding: the 2023-24 evaluation rows themselves carry the stamp leak. With corrected stamps, 521 of 27,619 (1.89%, 2023) and
654 of 27,701 (2.36%, 2024) production evaluation rows change their report features. A and C both see those late OUT sets, but they
were trained on different exposure: A on 2022+ rows where about 2% changed, C also on 2021 rows where 13.8% changed (3,784 rows, 330 games).
The interaction "trained to trust late OUT information" x "evaluation row carrying late OUT information" is second-order and does not
cancel in C minus A. Alone it cannot make a pass. It would need C to beat A by 0.21 (2024) to 0.26 (2023) pts CRPS on every affected row,
about 6% of the pts CRPS level. Added to a real but borderline gain, though, it can push the result over the floor, and its size cannot
be known without a fit. Fix: guard G9 (B2).

Model class: gradient-boosted trees on tabular features (LightGBM context-residual heads), with the lower-tail mixture (`pi`, `w_q`) refit per arm.

## Checks (all CPU, read-only, seasons <= 2024; exit codes shown unpiped)

| # | check | command (cwd = scratch worktree of d08d886 unless stated) | n | result | pass |
|---|---|---|---|---|---|
| 1 | the 10 earlier edits | read the text at d08d886, lines 14, 26, 35, 39, 45, 53, 56, 59, 64, 73-77, 102 | n/a | all 10 present (table below) | yes |
| 2a | rule sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F1_WARM_WINDOW.md \| shasum -a 256` (exit 0) and `python -m research.eval.f1_warm_window_rule freeze` (exit 0) | n/a | `cebff0839b110a1dee77bca40cab4c7732fdb90abbf8b271522f1f1b8e1c2f8f`, matching the stated value | yes |
| 2b | harness pin | `python -m research.eval.f1_warm_window_rule pin` (exit 0) | 19 files | `2ed1c59cd2fdf55956629a4a67731db4893d655dae305cae92d8fdb2a90694df`, matching the stated value. `git diff --stat main origin/agent/modeler-f1-warm-window-2` touches only the 10 F1 files, so every pinned `nba/` file and config equals main d99fbb5 | yes |
| 3 | harness does what the text says | read `f1_warm_window_run.py` against rule lines 9-15, 45, 53, 56, 82, 102 | n/a | windows, frames, floors, the p formula, BH m = 4 per season, HARM/clear thresholds, word order, G1 distance band, G2 ECE, G3 2 x floor on binding slices (n >= 300), slice definitions and R1 tolerances all match. Deviations: R3 "tip_source = real" is asserted nowhere (m3); configs are read relative to cwd, not to the pinned root (M2); line 14 lists a WOWY cross-check under G5 that F1 never runs (m1) | mostly |
| 4 | preflight and pins before any label | `run_real`, run.py:745-769 | n/a | `require_ready` (746), `preflight` (749) and the lock all come before `load_frames`. The first-block A reproduction (754) is label-free on evaluation rows. BUT the planted-future re-run R4/G4 (767) runs AFTER `fit_arms` and `build_bundle`: every arm's 2023-24 scores exist in memory, and the checkpoints with `row_y` are on disk, before G4 can STOP. Also, R1 (764) runs after all three arms are scored, not before "any contrast" in the sense of line 82 (M1) | partly |
| 5 | pin closure | scratch `closure.py`: import `research.eval.f1_warm_window_run` and list loaded modules (exit 0) | 26 modules | every local module that is loaded is in `HARNESS_FILES` (`lineup_features.py` is pinned for a lazy import). The unpinned `__init__.py` files are docstrings only. Configs read: the two pinned yaml files only. Data read: the four tables in `load_inputs` plus schedule parquet, all covered by the FP hashes. Hole: configs are read from `Path.cwd()`, while the pin checks `rule.ROOT` (M2) | yes, with M2 |
| 6 | library versions | `importlib.metadata` for the loaded third-party packages | n/a | pinned and equal: lightgbm 4.7.0, numpy 2.5.3, polars 1.44.2, duckdb 1.5.6, scipy 1.18.1, pyyaml 6.0.3. Loaded but unpinned: scikit-learn 1.9.1 (required by `LGBMRegressor`/`LGBMClassifier`; does not move numbers), pyarrow 25.0.1 (DuckDB `.pl()`; any change would show in the FP hashes and STOP), Python 3.12.13 (m2) | yes |
| 7 | freeze/pin commands | rule.py:14-18, 195, 205; prereg line 83 | n/a | `git commit --allow-empty -m "prereg(F1_WARM_WINDOW): <sha>"`, then `... rule pin`, then `git commit --allow-empty -m "harness(F1_WARM_WINDOW): <pin>"`. The order is right. `require_harness_pinned` checks that the pin descends from the freeze. Line 83 writes `.venv/bin/python` relative, but a worktree has no `.venv` (m4) | yes |
| 8 | rehearsal words, one `evaluate()` test per word | `python -m research.eval.f1_warm_window_run rehearse --world W` for each of the 5 worlds (each exit 0); `pytest --collect-only` | 5 worlds | helps -> PASS, null -> FAIL_NO_GAIN, harm -> FAIL_HARM, one_season -> FAIL_ONE_SEASON, guard -> FAIL_GUARD. `test_each_word_is_reachable_in_its_world[*]` (5) plus `test_every_word_is_tested_and_written_in_the_rule`, `test_harm_outranks_a_confirmed_stat`, `test_gate_or_reproduction_failure_is_a_stop_not_a_word`, `test_slice_guard_failure_blocks_confirmation` | yes |
| 9 | THE STAMP ISSUE | scratch `stamp_attack.py`: rebuild both frames with every whole-hour `nba_official_report` stamp moved +30 min (BEST_PRACTICES 2026-10-15; upper bound) and count stat-frame rows whose report columns (`n_out_rot`, `vac_*`, `since_*`, `has_report`) change. Features only (exit 0) | below | the claim is wrong as written (B2) | **no** |
| 10 | defaulted questions | prereg lines 113-121 against DECISIONS 17:28 / 17:30 | n/a | listed below. The caller's list "1, 2, 4, 5, 7" is not what the text says: 4 and 5 are Devin's decisions (verified in DECISIONS), and 6 is also a default. 7 contradicts 8 and line 23 (m5) | see list |
| 11 | gate re-run on real data (NN 8) | `F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD .venv/bin/python -W ignore -m research.eval.f1_warm_window_gate ../f1rc/gate_recheck.json` at 17:46 CDT (exit 0, 6.7 s); `nba.duckdb` mtime 17:20:09 | 7,595 games; 256,691 x 73 planted | **byte-identical** to the committed gate.json: sha256 `e0f320a1…2ae6` = GATE_SHA256. Usable reports 1,141/1,143 … 1,317/1,320. Planted future max abs diff 0.0, 0 columns differ. Stamp exposure 2019-24: 0.1218 / 0.1231 / 0.6636 / 0.0455 / 0.0380 / 0.0478 | yes |
| 11b | label-free preflight | `... -m research.eval.f1_warm_window_run check --data-root /Users/devin/Downloads/nba-prediction` (exit 0, 1.4 s; the text says about 40 s) | 10 hashes | `mismatch_vs_rule: {}`: every pinned input hash equals its line, versions equal | yes |
| 12 | season <= 2024 content pin of `nba.duckdb` | read `content_fp` (run.py:523-531); scratch query on both DBs (read_only) | 838 history-only players | Sound in the direction that matters: it hashes exactly what `gate.build` reads (games, box columns, static position, availability of <= 2024 games), after the `.pl()` conversion, sorted, and written with pinned polars. Any change to <= 2024 content STOPs the run, and live writes to 2025-26 rows do not. One hole: the warm frame's `static.unique(keep="last")` lets `nba.duckdb`'s `players_static` override history rows for players who are not in `nba.duckdb`'s box. Those rows are in neither FP. Measured: 2 such players, 0 with a different position, so the hole is immaterial today (m6) | yes, with m6 |
| 13 | kill criterion vs work already done | rule lines 102, 105; `data/ops/`; WOWY branch outputs | n/a | no `f1_warm_window_cache`, no heavy.lock, no WOWY re-measure output. `wowy_struct_crps_null_cache` (10-14 19:33) is the WOWY CRPS null (train 2020-21, test 2022; ae26fff), not arm C. BUT line 102 records a 17:36 refit of A's first block, which is a fit of an arm of this rule before the freeze, and line 105 voids the rule for exactly that (B1) | **no** |
| 14 | tests and lint | `pytest -q -p no:cacheprovider --no-cov tests/research/f1_warm_window` (45 passed, exit 0); `ruff check` (exit 0); `ruff format --check` (exit 0); `mypy` on the 3 modules (exit 0) | 45 tests | without `--no-cov` the subset run exits 1 only because of the repo-wide 70% coverage floor (45 passed). That is not a test failure | yes |

### The 10 earlier edits (check 1)

| edit | where at d08d886 | holds |
|---|---|---|
| 1 freeze order / firewall | line 39 ("It runs only after this rule's freeze commit"; the order is F1 commit, then WOWY commit, then any fit) and Devin's 17:28 / 17:30. The WOWY mirror is pending on its own branch (DECISIONS 17:30 sequences it after F1) | yes |
| 2 R2 names the verdict's C arrays | line 102 R2 | yes |
| 3 B is fitted by F1 | line 35 | yes |
| 4 inference code path, p formula, R1 reference, harness, one builder | line 45. `_frame` calls `gate.build` unchanged (run.py:283-288); `test_ci_equals_paired_score_delta_ci_and_p_formula` | yes |
| 5 G7, gate JSON committed, data version | lines 59, 73, 84-94 (now content FPs, which is stronger than a manifest) | yes |
| 6 knockout reason | line 64 | yes |
| 7 placebo n/a, joint power | lines 26, 75, 77 | yes |
| 8 slice definitions, one HARM rule, support language | lines 45, 53 | yes |
| 9 G1 distance band | lines 14, 56; `test_g1_uses_distance_to_nominal_not_difference_from_a` | yes |
| 10 runtime, report-lead table | lines 64, 102 R5 | yes |

## Check 9 in detail: can fresher 2019-21 OUT sets give C a false pass on 2023-24?

The modeler's claim (line 99 b): the training-only exposure "biases C AGAINST a pass … It cannot create a false pass in 2023-24."

Measured, label-free (stat-frame rows, pts frame; "changed" = any report column differs once whole-hour official stamps get +30 min):

| season | role | rows changed / rows | share | games changed / games |
|---|---|---|---|---|
| 2019 | C training only | 711 / 23,531 | 3.0% | 63 / 1,143 |
| 2020 | C training only | 697 / 24,577 | 2.8% | 59 / 1,171 |
| **2021** | C training only | **3,784 / 27,486** | **13.8%** | **330 / 1,323** |
| 2022 (cold) | A, B, C training | 457 / 25,110 | 1.8% | 38 / 1,249 |
| 2023 (cold keys) | **evaluation, all arms** | **521 / 27,619** | **1.89%** | 37 / 1,318 |
| 2024 (cold keys) | **evaluation, all arms** | **654 / 27,701** | **2.36%** | 44 / 1,320 |

G8's 66% (2021) counts games whose used stamp is exposed. Only 25% of 2021 games (13.8% of rows) actually get a different OUT set, which
makes the training-side shift about a fifth of what G8 suggests. The evaluation rows are not clean: about 2% of them carry a report
published after tip-60.

Two channels:

1. **Evaluation-row interaction (does not cancel).** (a) is right that the leak enters every arm's evaluation rows through the same code.
   But each arm's response to those features was learned under a different exposure: A about 2% of rows, C about 2% plus 13.8% in 2021.
   A model trained where late OUT sets are common learns to lean on them harder, and on the 1.9-2.4% of evaluation rows that carry a late
   OUT set, leaning harder is right. This term is a product of two differences, so it is second-order, and its sign favours C.
   Size, as a bound only: for this channel alone to clear the floor, C must beat A on every changed row by floor / share: pts 0.26 (2023)
   and 0.21 (2024) CRPS, about 6% of the pts level (about 3.4-4.2, WOWY null `role_crps_level`); reb, ast and fg3m 0.13 / 0.11. Both arms
   see the same late information, so that is implausible alone. A generous reference is T085 (T-30 confirmed starters, a much richer late
   signal): pts -0.0412 per row on all rows. Even if C captured all of that on changed rows and A none, the channel would add at most about
   0.0412 x 0.0236 = 0.0010 pts (20% of the floor) and 0.0162 x 0.0236 = 0.0004 reb (15%). So it cannot create a pass by itself, but it can
   push a real borderline gain over the floor. The joint power at the floor is 0.18 (pts), so a borderline pass is the likely shape of any pass.
2. **Training-side shift (sign not determined; not a leak on evaluation rows).** "Over-trust, therefore against a pass" assumes classical
   measurement error. A late scratch adds a player to the OUT set roughly independently of who is already listed, which is closer to
   Berkson error. Under Berkson error a fresher-trained slope is about right for stale inputs, and cleaner training signal can help a
   capacity-limited learner. So the sign is open. What can be said: this channel uses pre-tip information (about tip-30) and lives in
   training rows only. A production refit on the same stored history would carry it too, so it is not a false pass about the model
   production would run. It is a pass conditional on the uncorrected stamps, and it would change once history stamps are repaired (the
   repair is planned after 10-20, DECISIONS 17:23).

Fix (B2): add binding guard G9 (below) and rewrite line 99 (b)-(d).

## Findings (severity-ranked; each with line and fix)

**B1 (blocker). Line 105 vs line 102.** The kill criterion voids the rule if "any arm of this rule … was fitted or scored before this
rule's freeze commit". Line 102 records a pre-freeze refit of arm A's first block (2026-10-15 17:36, 1,062 rows), and `check --smoke`
(run.py:808-810) exists to do exactly that. A literal reader can call F1 void the moment it is frozen. Fix, line 105: after "fitted or
scored before this rule's freeze commit" insert "(except the label-free first-block reproduction of A, `check --smoke`, which re-creates
stored production components and computes no score; no B or C block of any size is fitted before the freeze)".

**B2 (blocker). Line 99 (b)-(d): "It cannot create a false pass in 2023-24" is false (check 9, channel 1), and (d) misquotes DECISIONS.**
Fix, three parts:
(i) Rewrite (b) as: "It does not cancel in the training window … The sign of this training-side shift is not determined (classical
versus Berkson error), and it uses pre-tip information present in any refit on stored history, so it is not a leak on evaluation rows. It
interacts with the about 2% of 2023-24 evaluation rows that carry a late report (1.89% / 2.36% of rows, measured), where it can favour C.
That interaction is closed by G9."
(ii) Add guard **G9** to line 14 and line 45 and implement it: "a stat-season counts as clearing only if C minus A also has delta <=
-floor and 95% game-clustered CI upper < 0 on the evaluation keys of games NOT stamp-exposed under G8's definition (2023: 50 games, 2024:
63; BH not re-applied). A failure is FAIL_GUARD." Code: `stamp_exposure` returns the exposed game ids per season, which go into gate.json,
so GATE_SHA256 changes. `build_bundle` gets a `slices["unexposed"]` mask, and `evaluate` adds `g9` to `guards_ok`. Add a test world
`stamp` (gain placed only in exposed games) that must return FAIL_GUARD, and update `test_every_word_is_tested_and_written_in_the_rule`
if it enumerates worlds. Power cost: dropping at most 4.8% of games widens SE by about 2.5%.
(iii) Line 99 (d): "The live path is conservative" becomes "The live path appears conservative (unverified on 2026-27 files, DECISIONS
2026-10-15 17:23)".

**M1 (major). run.py:760-769: planted-future G4/R4 runs after every arm is fitted and scored.** If G4 failed, the rule would STOP with
no word, yet the full 2023-24 contrast would already be computable from `data/ops/f1_warm_window_cache` (checkpoints carry `row_y`).
Fix: move `planted = gate.run(...)["planted_future"]` and its `g4` check to just after `smoke_block0` and before `fit_arms`, and raise on
failure there. In line 82, add "then the planted-future re-run (R4)" before "only then the arms". Optional: build A's bundle and check R1
before B and C are scored. As written, all three arms are scored in memory before R1, though nothing is printed.

**M2 (major, pin closure). run.py:293 and gate.py:105-108, 159 read `Path("configs/...")` relative to the CURRENT directory, while
`require_harness_pinned` checks `rule.ROOT/configs/...`.** A run launched from another cwd would read unpinned configs silently. Fix: read
configs from `rule.ROOT / "configs/..."` (and `Path(__file__).resolve().parents[2]` in the gate), or have `run_real` refuse unless
`Path.cwd().resolve() == rule.ROOT`. One test.

**m1 (minor). Line 14, G5: "C versus WOWY's `A0_warm` per R2 is a cross-check".** Line 102 says WOWY checks that, not F1, and the
harness has no such check. Fix: G5 reads "(G5) reproduction checks R1 (A reproduces stored production) and the first-block exact check".

**m2 (minor). run.py:90-97.** Add `scikit-learn 1.9.1` and `pyarrow 25.0.1` to `PINNED_VERSIONS`, plus a Python `3.12.13` check, and
add both to line 82. Neither is likely to move a number (pyarrow is covered by the FP hashes), but both are on the path.

**m3 (minor). Line 102, R3: "`tip_source = real`" is claimed as a run-time check and is asserted nowhere.** Fix: in `load_frames`, assert
`cfg.report.tip_source == "real"`.

**m4 (minor). Line 83: `.venv/bin/python …` is relative, and a worktree has no `.venv`.** Fix: use `/Users/devin/Downloads/nba-prediction/.venv/bin/python`
and state "cwd = the checkout holding the freeze and pin commits". Also "about 40 s" for `check`: measured 1.4 s.

**m5 (minor, needs Devin). Lines 23, 120 and 121 contradict each other.** Line 23 says "One pre-registered follow-up at most; none after
this". Q7 says "a null closes it for good". Q8 says a repaired-stamp re-run after a FAIL is Devin's call under a new id. Fix: state one
thing. My recommendation: line 23 and Q7 read "none after this, except the single repaired-stamp re-run Devin may choose under Q8 (new id)".
Devin should choose now, before the result, because a FAIL is the likely outcome (prior: FAIL 0.91).

**m6 (minor). run.py:523-531, content FP static hole.** Hash the merged static that `gate.build` actually uses (both DBs concatenated,
`unique(keep="last")`, semi-joined to the union of box player ids) as one more pinned line, or semi-join each DB's static to the union of
both DBs' player ids. Measured exposure today: 2 players, 0 differing positions.

**Nits (no fix forced).** Line 3 "17:4x CDT" is a guessed clock time; write the time printed by `date`. `reports/lower_tail/components_*.npz`
are untracked in the main checkout and pinned only by hash. If they are lost, R1 cannot run, so copy them to a backup before the run.

## Questions defaulted under Devin's go (for Devin to see)

| Q | what was decided | by whom | reasonable? |
|---|---|---|---|
| 1 | one confirmatory arm (C minus A, m = 4); B minus A and C minus B descriptive | **default as drafted** | yes. Two arms would make m = 8 and let a thin B borrow weight |
| 2 | full production recipe per arm, mixture `pi` / `w_q` refit in-block | **default as drafted** | yes. It is what production does, and the calibration window changing by arm is named as part of the treatment, guarded by G1 / G2 |
| 3 | count floors -0.0025 | Devin, DECISIONS 17:28 | n/a |
| 4 | only F1 licenses a window change | Devin, DECISIONS 17:30 | n/a (the caller listed it as defaulted; it is not) |
| 5 | F1 first | Devin, DECISIONS 17:28 / 17:30 | n/a (same) |
| 6 | Stage 2: forward shadow of C, not a 2025 touch | **default** (not on the caller's list) | yes. It needs no holdout row, and Devin decides after a pass |
| 7 | this rule is the one allowed follow-up; "a null closes it for good" | **default as drafted** | yes in spirit, but it contradicts Q8 and line 23 (m5) |
| 8 | repaired-stamp re-run after a FAIL | deferred to Devin, new id | needs a choice before the result (m5) |

## Corrected family and power

Family unchanged: C minus A, m = 4, BH per season, both seasons required (8 tests; B minus A and C minus B descriptive). G9 adds a guard,
not a test. Power is unchanged from the gate (pts joint 0.18 at the floor, 0.58 at 1.5 x floor; reb 0.77, ast 0.86, fg3m 0.98 at
1.5 x floor). G9 widens SE by about 2.5%. Underpowered at the floor; a FAIL at the floor is "cannot detect", not "no effect".

## Playbook check (docs/MODEL_PLAYBOOK.md, gradient-boosted trees on tabular features)

1. Information or capacity: both, separated by B (frame) and C (frame + window). New: part of the "information" C gains in 2021 is
   stamp-late OUT sets (13.8% of 2021 rows), which is a property of stored history, not of the game.
2. Enough data: 25,110 rows (one season) at A's first block versus 102,924 for C. A fair learning-curve test; both seasons must pass.
3. Test valid: real tips, integer grid in every arm, identical keys, planted future 0.0 (re-run today), NULL audit flat. Not valid as
   written on one point: about 2% of evaluation rows carry a post-cutoff report, and the arms may exploit it differently (B2 / G9).
4. Lineage and inherited guards: HW3 (T420-T423), T318-T319, T315 / T325, struct audit F1, INJURY_TIMING B1 (the stamp offset). Guards
   inherited and present; the stamp guard is missing (G9).
5. Scored against: production's integer CRPS on production's keys, not the market. Correct.

Class checks: missingness beyond the as-of cause (flat), planted future (0.0), re-expression of the base (B minus A, C minus B,
descriptive). Mixture calibration volume differs by arm, guarded by G1 / G2.

## Single biggest risk

A borderline pts pass in 2024 (joint power 0.18 at the floor) whose margin past -0.005 is about the size of the evaluation-row stamp
interaction (up to about 0.001 under a generous bound). G9 removes it by requiring the pass on the unexposed games too.

## What was not verified

- The first-block exact reproduction of A (`check --smoke`, claimed 0.0 at 17:36). Not run: it is a real-label fit.
- Full-run LightGBM determinism with `n_jobs` 4 across all blocks. R1 tolerances cover it at run time.
- The true per-row size of channel 1. Only a fit could measure it; it is bounded above, not measured.
- Whether every minute-0 stamp is really an hour-only file. G8 and my count both treat them all as hour-only, so both are upper bounds.
- The WOWY_STRUCT mirror edits (its own branch; sequenced after F1).

## Exact freeze, pin and run steps (after B1, B2, M1, M2 are applied and a diff re-check approves the NEW sha / pin)

The modeler applies the edits on `agent/modeler-f1-warm-window-2`, re-runs the gate (GATE_SHA256 changes because of the G9 ids), updates
the pinned line, runs `pytest --no-cov tests/research/f1_warm_window` / ruff / mypy unpiped, commits and pushes. The adversary diff-checks.
Then the main session, from the modeler worktree (cwd = the checkout that holds the commits; it equals `rule.ROOT`):

```
cd /Users/devin/Downloads/nba-prediction/.claude/worktrees/modeler-f1-warm-window-2
git status --short                       # pinned files clean
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.f1_warm_window_rule freeze
#   must print the sha the re-check approved; if not, STOP
git commit --allow-empty -m "prereg(F1_WARM_WINDOW): <sha>"
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.f1_warm_window_rule pin
#   must print the pin the re-check approved; no edit between the two commits
git commit --allow-empty -m "harness(F1_WARM_WINDOW): <pin>"
git push origin agent/modeler-f1-warm-window-2
F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.f1_warm_window_run check --data-root /Users/devin/Downloads/nba-prediction      # mismatch_vs_rule must be {}
F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.f1_warm_window_run run --data-root /Users/devin/Downloads/nba-prediction --sha <sha> \
  > /Users/devin/Downloads/nba-prediction/data/ops/f1_warm_window_run.log 2>&1      # background; takes heavy.lock itself
```

Order relative to WOWY_STRUCT: no WOWY arm is fitted, and nothing is scored on `A0_warm`, until the F1 freeze commit exists.
Do not freeze `cebff083` / `2ed1c59c`.

## Proposed rows (drafted, not appended)

Ledger: none (no result). DECISIONS (main session, if Devin agrees):
`2026-10-15 | F1_WARM_WINDOW pre-freeze re-check: BLOCKED for sha cebff083 / pin 2ed1c59c (docs/reviews/F1_WARM_WINDOW_recheck_2026-10-15.md). The 10 earlier edits hold, and sha, pin, gate and input hashes reproduce exactly. Two blockers: the kill criterion voids the rule on its own label-free smoke fit, and the stamp section's "cannot create a false pass" is false (about 2% of 2023-24 evaluation rows carry a post-cutoff report, and C is trained on 13.8% such rows in 2021). Fix: kill-criterion carve-out, guard G9 (pass must hold on stamp-unexposed games), planted-future before the fits, configs from the pinned root. Devin to choose Q7 vs Q8 (one repaired-stamp re-run or none) before the result. Reverse if: none; a diff re-check approves the new sha.`

---

# Diff-only re-check of b1148c7 (adversary, 2026-10-15 18:0x CDT; `date` printed 17:59:01 at its start)

Object: `origin/agent/modeler-f1-warm-window-2` b1148c7 (one commit on top of d08d886). Rule sha `9b5a7d6f…9ddc`, pin `39cfe40f…9bd2`,
GATE_SHA256 `b872a576…146e`. Devin's decisions on Q8 and G9 are in DECISIONS 2026-10-15 17:55 (ff02126). Same constraints as above:
no fit, no score, no 2025, read-only.

Verdict: APPROVED

Approval condition (it changes neither the sha nor the pin, because `tests/` is not in `HARNESS_FILES`): commit the functional config-read
test below as `tests/research/f1_warm_window/test_f1_warm_window_config_read.py` on the branch BEFORE the freeze commit. The parenthesis
bug the modeler fixed in the gate's config read is covered in the branch only by `test_configs_are_read_from_the_pinned_root_never_the_cwd`.
That test checks that the string `Path("configs` is absent, and a misplaced parenthesis or a wrong key would pass it.

## Checks (unpiped exit codes)

| # | check | command / evidence | result | pass |
|---|---|---|---|---|
| 1 | sha | `awk … \| shasum -a 256` at b1148c7 (exit 0) | `9b5a7d6fad1e5e91cd71be7fd8026b84ca58f318013ab124fbbbbaab99899ddc` | yes |
| 2 | pin | `python -m research.eval.f1_warm_window_rule pin` (exit 0) | `39cfe40f112571535267fe06db5ca3791611e1ac1d1780fe4501283c3b239bd2` | yes |
| 3 | gate re-run on real data (NN 8) | `F1_ROOT=… -m research.eval.f1_warm_window_gate ../f1rc/gate_recheck2.json` (exit 0) | byte-identical to the committed gate.json: sha256 `b872a576…146e` = GATE_SHA256 | yes |
| 4 | preflight | `… f1_warm_window_run check --data-root /Users/devin/Downloads/nba-prediction` (exit 0) | `mismatch_vs_rule: {}` over 11 hashes. FP_NBA / FP_HISTORY changed by definition: static moved to the new `FP_STATIC_MERGED` (m6) | yes |
| 5 | B1 | prereg line 107 | carve-out for the label-free first-block reproduction of A; no B or C block before the freeze | yes |
| 6 | B2 text | prereg line 101 (b)-(d) | measured shares quoted correctly. Training-side sign stated as undetermined; the interaction is closed by G9; "appears conservative (unverified …)" restored | yes |
| 7 | B2 / G9 code | run.py `evaluate`: `g9 = unexposed delta <= -floor and ci_hi < 0`, inside `guards_ok`; `exposed_games()` reads the ids from the pinned gate.json | matches line 14. Scratch `exposed_align.py` (exit 0): the ids are `str` in the evaluation format (e.g. `0022300142`). 2023: 50 of 50 hit evaluation games, 1,043 of 27,619 rows. 2024: 63 of 63, 1,317 of 27,701 rows | yes |
| 8 | G9 rehearsal | `rehearse --world stamp` (exit 0) -> FAIL_GUARD. `test_stamp_world_clears_on_the_numbers_and_fails_only_g9` asserts that G1-G3 hold and only G9 fails, and that G9 holds in `helps` | yes |
| 9 | M1 | run.py `run_real`: `smoke_block0` -> `gate.run` planted-future STOP -> `fit_arms`; `test_planted_future_is_checked_before_any_arm_is_fitted` | yes |
| 10 | M2 | gate `CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"`; run reads `rule.ROOT / "configs/…"`; `run_real` refuses unless cwd = `rule.ROOT` (tested) | yes |
| 11 | parenthesis-bug test | scratch `test_config_read_functional.py`, run from a cwd with no `configs/`. It records the kwargs passed to `ContextResidualConfig` by `gate.build`, `gate.stamp_exposure` and `run.load_frames`, and requires them to equal the yaml `model` section. 2 passed, exit 0 on b1148c7 | **no such test in the branch** (condition above). A mutation run to show it fails on a broken read was not executed (sandbox denied); by construction a defaulted config gives `{}` != the model section | condition |
| 12 | m1-m4, m6 | line 14 G5 wording; `PINNED_VERSIONS` + `PINNED_PYTHON`; `tip_source == "real"` asserted in `load_frames`; absolute `.venv` paths and cwd in line 83; `static_fp` over merged static | yes |
| 13 | Q7 / Q8 / line 23 | lines 23, 122, 123: one stamp-corrected re-run under a NEW id after a FAIL, decided before the result (DECISIONS 17:55) | consistent | yes |
| 14 | tests, lint | `pytest -q -p no:cacheprovider --no-cov tests/research/f1_warm_window` 52 passed, exit 0; `ruff check` 0; `ruff format --check` 0; `mypy` (3 modules) 0 | yes |

New contradictions looked for: none that block. Notes, no fix forced:
(a) G3 now also iterates over the `unexposed` slice. That is harmless: it is looser than G9.
(b) `exposed_games(root)` ignores its argument and reads from `rule.ROOT`, which is the pinned file, as intended.
(c) R1 still runs after all three arms are scored in memory. Line 82 now says so, and nothing is printed before it.

## The test to commit before the freeze (verbatim; not pinned)

```python
"""Functional: the gate and the run read the PINNED configs from a foreign cwd, and parse the `model` section (catches a misplaced parenthesis)."""
from pathlib import Path

import pytest
import yaml

from research.eval import f1_warm_window_gate as G
from research.eval import f1_warm_window_run as R

REPO = Path(G.__file__).resolve().parents[2]
WANT = yaml.safe_load((REPO / "configs/context_residual.yaml").read_text())["model"]


class Stop(Exception):
    pass


def _recorder(seen):
    real = G.ContextResidualConfig

    def rec(**kw):
        seen.append(kw)
        return real(**kw)

    return rec


def test_gate_build_and_stamp_exposure_read_pinned_configs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no configs/ here
    seen = []
    monkeypatch.setattr(G, "ContextResidualConfig", _recorder(seen))

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(G, "load_inputs", boom)
    with pytest.raises(Stop):
        G.build(tmp_path, ["x.duckdb"])
    with pytest.raises(Stop):
        G.stamp_exposure(tmp_path)
    assert seen == [WANT, WANT]


def test_run_load_frames_reads_pinned_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(R, "ContextResidualConfig", _recorder(seen))

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(R, "_frame", boom)
    with pytest.raises(Stop):
        R.load_frames(tmp_path)
    assert seen == [WANT]
```

(Add type hints if the tests directory is under mypy; ruff/format it on commit.)

## Exact freeze, pin and run steps

```
cd /Users/devin/Downloads/nba-prediction/.claude/worktrees/modeler-f1-warm-window-2
git checkout agent/modeler-f1-warm-window-2 && git pull --ff-only     # HEAD must be b1148c7 (plus only the test commit below)
# 0. commit the functional test (tests/ is not pinned; sha and pin unchanged)
git add tests/research/f1_warm_window/test_f1_warm_window_config_read.py
git commit -m "test(F1_WARM_WINDOW): functional config read from a foreign cwd" -- tests/research/f1_warm_window/test_f1_warm_window_config_read.py
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/research/f1_warm_window   # expect 54 passed, exit 0
git status --short                                                     # pinned files clean
# 1. freeze
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.f1_warm_window_rule freeze
#    must print 9b5a7d6fad1e5e91cd71be7fd8026b84ca58f318013ab124fbbbbaab99899ddc, else STOP
git commit --allow-empty -m "prereg(F1_WARM_WINDOW): 9b5a7d6fad1e5e91cd71be7fd8026b84ca58f318013ab124fbbbbaab99899ddc"
# 2. pin (no edit between 1 and 2)
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.f1_warm_window_rule pin
#    must print pin 39cfe40f112571535267fe06db5ca3791611e1ac1d1780fe4501283c3b239bd2, else STOP
git commit --allow-empty -m "harness(F1_WARM_WINDOW): 39cfe40f112571535267fe06db5ca3791611e1ac1d1780fe4501283c3b239bd2"
git push origin agent/modeler-f1-warm-window-2
# 3. label-free preflight (expect "mismatch_vs_rule": {})
F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.f1_warm_window_run check --data-root /Users/devin/Downloads/nba-prediction
# 4. run (background, from this cwd; the harness takes data/ops/heavy.lock itself)
F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.f1_warm_window_run run --data-root /Users/devin/Downloads/nba-prediction \
  --sha 9b5a7d6fad1e5e91cd71be7fd8026b84ca58f318013ab124fbbbbaab99899ddc \
  > /Users/devin/Downloads/nba-prediction/data/ops/f1_warm_window_run.log 2>&1
```

Order relative to WOWY_STRUCT is unchanged: no WOWY arm is fitted and nothing is scored on `A0_warm` before step 1. The live job must not
rewrite season <= 2024 rows between step 3 and step 4, or the run STOPs at the preflight; that is the safe direction.

## Proposed DECISIONS row (drafted, not appended)

`2026-10-15 | F1_WARM_WINDOW diff re-check of b1148c7: APPROVED (docs/reviews/F1_WARM_WINDOW_recheck_2026-10-15.md). sha 9b5a7d6f, pin 39cfe40f, gate b872a576 reproduced on real data; B1, B2/G9, M1, M2, m1-m6 applied; G9 ids align with the evaluation keys (2023 50 games / 1,043 rows, 2024 63 / 1,317). Condition: commit the functional config-read test before the freeze (not pinned; sha and pin unchanged). Reverse if: the freeze or pin command prints anything else.`
