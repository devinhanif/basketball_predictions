# WOWY_STRUCT pre-freeze review of the replacement-twist revision (adversary, 2026-10-16)

Object: `origin/agent/modeler-wowy-repl` at 73998a0. Files: `docs/prereg/WOWY_STRUCT.md` (rule sha `cf4d3751…5ec2`, the text above the marker),
`research/eval/wowy_struct_{eval,rule,repl_gate,gate}.py`, `nba/props/{wowy,wowy_repl,struct_alloc}.py`, `reports/prereg_wowy_repl/gate.json`
and `reports/prereg_wowy_struct/`. Earlier reviews: f1f0add (pre-freeze, APPROVED WITH EDITS, E1-E6) and `origin/agent/adversary-struct-leak`
6d3a3bf (leak audit, WOUNDED, no leak). Devin chose option A (DECISIONS 2026-10-16 14:57) and signed at 15:00 ("freeze after the review";
edits that change the decision rule come back to him). A0 is F1_WARM_WINDOW's arm-C cache (F1 frozen b22ebd5, pinned d9c2166, run T498).

How it was read: a detached scratch worktree of 73998a0, with the main checkout as data root. Every DuckDB connection was `read_only=True`
and every query had `season <= 2024`. Season 2025 was never loaded. No arm of this rule was fitted or scored. No 2023-24 outcome was related to
any column. One thing was fitted, and it is disclosed here: the **A0 walk with no extra column** (F1's arm C, code unchanged) on all 18 blocks
of both stats. I compared centre and scale only. `row_y` was dropped before the comparison and no score was computed (check 7).

Model class: gradient-boosted trees on tabular features (production `context_residual` LightGBM heads with added as-of columns), read through
the lower-tail mixture (`pi`, `w_q` from F1's arm C) and scored by integer-support CRPS.

Verdict: APPROVED WITH EDITS

Scope of the verdict: none of the edits changes the decision rule. Cells, floors, t_c, words, comparator, family and window are untouched, so
under Devin's 15:00 condition none of them goes back to him. Edits E1-E4 must land before the freeze. E5-E12 are cheap and should land in the
same commit. E1-E3 and the disclosures E10-E12 change rule text, so the sha changes from `cf4d3751`. E4 and E6-E9 change pinned
files, so the pin changes from `f64c9ce6`. A diff re-check of the listed lines approves the new sha and pin.

Top finding: **the S_R family is clean and the harness does what the rule says, but the thin cell will probably fail on its own data.**
pts top 25% (fixed) passes gate (a) only at its in-sample 2020-21 kappa (power 0.56 / 0.70). At its 2022 held-out kappa (0.036, se 0.045)
the same outcome-free method gives **0.29 / 0.38** (check 12). Keep the cell: dropping it now would itself be a fork, and with BH at m = 3 it
costs the other cells almost nothing. But the rule should say now that a FAIL there reads "cannot detect", not "no effect" (E10).
The stamp channel is real but small. Cell rows in stamp-exposed games are 4.7-7.8%, enriched 1.2-1.6x over all rows. G9 closes the channel,
and it is implemented correctly (check 10).

## Checks (all CPU, read-only, seasons <= 2024; exit codes shown, never piped)

`$S` = the adversary scratchpad (`/private/tmp/claude-501/…/scratchpad`). `$C` = `$S/cand`, the detached worktree of 73998a0.
`PY` = `/Users/devin/Downloads/nba-prediction/.venv/bin/python`. Attack scripts are in `$S/attack/`, never under `nba/`.

| # | check | command (cwd = `$C` unless stated) | n | result | pass |
|---|---|---|---|---|---|
| 1 | rule sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/WOWY_STRUCT.md \| shasum -a 256` (exit 0); `PY -m research.eval.wowy_struct_rule freeze` (exit 0) | n/a | `cf4d3751e0f7233faadd991eab193a8ac73d102d647982d53df3fb64209d5ec2`, the stated value | yes |
| 2 | harness pin | `PY -m research.eval.wowy_struct_rule pin > $S/pin_out.txt` (exit 0) | 76 files | `f64c9ce6b637afada4cc20489afab88c22b313eb7027f12a6b45630658e55f42`. The rule text states no pin, which is correct: the pin is taken after the freeze. `git diff --stat 73998a0 main -- <76 pinned files>` is empty (exit 0), so merging main changes no pinned byte. `git diff d9c2166 73998a0 -- <same>` is empty, so nothing F1 pinned has moved since F1's run. All 19 of F1's pinned files are in this pin | yes |
| 3 | pin closure covers every number-moving import | `$S/attack/ast_closure.py` (static transitive closure, including function-level imports; exit 0) and `$S/attack/closure.py` (runtime; exit 0) | 74 code files | closure minus pin = **empty**. Unpinned: 8 `__init__.py` files, all docstrings or a `__future__` import. The nba/props modules `wowy.py`, `wowy_repl.py`, `struct_alloc.py` and `repl_delta.py` are pinned. Data inputs: F1 cache (3 file hashes plus tag), both gate JSONs, F1 gate, the history DB hash, the 4 content FPs. Library versions: F1's 8 pins plus Python 3.12.13 (`f1_warm_window_run.py:91-101, 128-139`) | yes |
| 4 | configs from the pinned root | read `wowy_struct_eval.py:740-744, 803-807`, `f1_warm_window_gate.py:39`, `f1_warm_window_run.py:306` | n/a | both configs come from `rule.ROOT` or the file-relative `CONFIG_DIR`; `tip_source == 'real'` is asserted (eval:743). **Two cwd-relative data paths remain in a pinned file**: `wowy_struct_repl_gate.py:48` `OUT` and `:49-51` `STRUCT_GATE`. `STRUCT_GATE` is read on the run path by `wowy_struct_eval.py:395` to get the cell cut-offs. `run` is safe because it refuses unless cwd == `rule.ROOT` (eval:717). `check` has no such guard (E6). And a gate re-run inside the pinned checkout overwrites the pinned artifact (E7; seen in check 11) | yes, with E6/E7 |
| 5 | order: preflight, pins and versions before any label | read `run_real` (eval:716-775); `tests/…/test_run_order_frozen_pinned_then_preflight_then_planted_then_smoke_then_fits` | n/a | `require_ready`, then `preflight` (versions, 14 hashes, F1 tags), then world, then counts == gate, then priors sha, then **planted future before any fit** (F1's M1 is carried over), then `tip_source`, then training frame, then block-0 equality, then fits, then A0 load, bundle, evaluate. `stored_counts_check` opens the C npz but reads only gid, pid and season. Minor: `heavy.lock` is taken after the ~1 min world and frame build (E9) | yes |
| 6 | freeze and pin commands: `--allow-empty`, in order | `wowy_struct_rule.py:14-18, 252, 262`; prereg line 3 | n/a | prereg first, then pin, both `git commit --allow-empty`. `require_harness_pinned` checks that the pin descends from the freeze and that disk equals the pin commit. It does **not** check that the pinned files are unchanged between the freeze commit and the pin commit (E8). The docstring uses a relative `.venv/bin/python`, and a worktree has no `.venv` (E8) | yes, with E8 |
| 7 | A0 from F1's cache equals a fresh walk, **all blocks** | `NBA_ROOT=… nice -n 10 PY -W ignore $S/attack/attack_main.py C` (exit 0, 135 s): `training_frame` plus `collect_heads_x(frame, st, cfg, [])` on all blocks; `row_y` and `cal_*` dropped; centre and scale aligned to the cache by key | 55,434 rows x 2 stats, 18 blocks each | **max abs diff 0.0 on every block, pts and ast**. The harness only checks block 0 (eval:657-676). The cache row order is (game_date, game_id, player_id), equal to the order `add_permuted` produces, so `qi_of`'s strict alignment holds for every arm (`$S/attack/order.py`, exit 0). `pi` / `w_q` were not re-walked; they are pinned by `C_SHA256_PIC` and the tag | yes |
| 8 | label-free preflight on today's data | `NBA_ROOT=… PY -W ignore -m research.eval.wowy_struct_eval check --data-root /Users/devin/Downloads/nba-prediction --out $S/check_out` (exit 0, 31 s; **no `--smoke`**) | 6 cells | tags equal `9b5a7d6f…:39cfe40f…` on C_pts, C_ast and C_pic. Versions equal. `priors_match_rule` true. Six cell counts equal gate.json (4,316/5,678, 3,568/4,473, 4,942/4,414 rows). Planted future pass (12,597 early rows; later rows move: rb 4.61, rx 15.39). `mismatch_vs_rule` showed only `GATE_SHA256`, and only because my check-11 re-run had overwritten the scratch copy. Restored with `git checkout`: sha `6be61a4f…` = the pinned line | yes |
| 9 | planted future, including **tonight's own box** | the modeler's test compares rows strictly before the cut. Mine, `$S/attack/attack_main_dtype.py B` (exit 0): every box, state and played row on or after cut D gets minutes := 1 (tonight < 5 minutes) and stats x3+7, **dtypes kept**; T and S columns rebuilt; rows dated exactly D compared | 125 / 229 / 187 rows on D = 2023-12-01 / 2024-01-15 / 2024-03-10 | T columns (14, placebo included) max abs diff 8.9e-16; S columns (25: struct, role share, leader, V, Vmax) **0.0**; NULL pattern equal. A first run that cast float32 `minutes` to float64 showed 1e-7. A no-op plant reproduced it exactly (`attack_B4.py`: same 1.16e-4 in the cumulative sums with nothing planted), so that was an artifact of my attack, not a leak | yes |
| 10 | G9 and the report stamp | `attack_main.py A` (exit 0): cell rows of F1's exposed game ids at A0's keys; code read `eval:202-205, 465, 760`, `f1_warm_window_run.py:790-796` | below | G9 is implemented as the rule says (point <= -0.005 and CI upper < 0 on unexposed cell rows, BH not re-applied, ids from the pinned F1 gate). Bound and power cost below | yes |
| 11 | NN 8: gate re-run on real data | `NBA_ROOT=… nice -n 10 PY -W ignore -m research.eval.wowy_struct_repl_gate` (exit 0, 15:02:15-15:04:11 CDT; nba.duckdb mtime 14:54, manifest `762aa642e389`) | 187 differing leaves of the JSON | **not byte-identical** (sha `5926457d…` vs `6be61a4f…`), but every numeric difference is <= **1.1e-13** (summation order). Every power, kappa, cell-gate and count value reproduces. The priors hash rounds to 10 significant digits, so it is stable | yes |
| 12 | thin cell and kappa robustness | `$S/attack/attack_E.py` (exit 0): the gate's own outcome-free version-B code (`c_forecasts`, `expected_gain_var`, `sizing_row`) on C forecasts with `row_y` = NaN, at other kappas | 3 cells x 2 seasons | reproduces the gate exactly at the 2020-21 kappa. At the 2022 held-out kappa: pts leader out 0.97 / 1.00, **pts top 25% 0.29 / 0.38**, ast leader out 1.00 / 1.00. At the 2020-21 kappa minus 1 se: 0.52 / 0.70, **0.25 / 0.32**, 1.00 / 1.00. Break-even multiplier of the thin cell in 2023: 1.071 (the rule's 7% is right) | see E10 |
| 13 | missingness (check 3 of 6), dev seasons only | `$S/attack/attack_D.py` (exit 0): 2020-22, `n_prior >= 5`, has_report = 1 | 79,327 rows | struct, role share and leader: 0 NULLs in every group. `rb` / `rx`: 4.8% (minutes < 5) vs 5.7% (>= 5) overall, but **33.4% vs 32.5% inside the first 15 team games and 0.0% vs 0.0% after**, so the gap is composition. win 5.69% / loss 5.62%; pts above / below median 5.58% / 5.73%. No asymmetry that is not knowable pre-tip. The actual starter flag was not tested (the box starter column was not loaded) | yes |
| 14 | cell gate extended to T_R: is S_R's family untouched? | `cell_gate_rule` (repl_gate:580-621) against `gate.json`; old `struct_warm.cell_gate` | 3 cells | S_R passes (a), (b) and (c) in all three cells, the same cells and m = 3 as the reviewed text. T_R fails (c) in every cell and (a) in every cell; ast also fails (b). So T_R's exclusion does not rest on its power alone. Two changes the text understates: (c) for S_R was "permuted vs base < STRUCT vs base" in the old gate and is now "P+R beyond ROLE < S+R beyond ROLE"; and the S_R beyond-role numbers are re-estimates, not a reproduction (pts leader out now +1.46 / +0.78 / +1.39, before +1.40 / +0.86 / +1.55). Both definitions pass (E11) | yes |
| 15 | rehearsal | `PY -W ignore -m research.eval.wowy_struct_eval rehearse --world all --seeds 10 --out $S/rehearse_out` (exit 0) | 4 worlds x 10 seeds | identical to the committed `rehearsal.json`: PASS 10, FAIL_GUARD 10, FAIL_ONE_SEASON 10, FAIL_NO_GAIN 10. One world per word, plus `test_each_word_is_reachable_in_its_world[*]`. Adequacy below | partly (E4) |
| 16 | tests, lint, types | `PY -m pytest -q -p no:cacheprovider --no-cov tests/props/test_wowy_repl.py tests/props/test_struct_alloc.py tests/props/test_wowy.py tests/props/test_wowy_struct_*.py tests/research/wowy_struct` (90 passed, exit 0); `ruff check` (exit 0); `ruff format --check` (exit 0); `mypy` on 6 modules (exit 0) | 90 tests | `--no-cov` because a subset run trips the repo-wide coverage floor | yes |
| 17 | layering: nba/props changes stay off the live path | `grep -rn -E "struct_alloc\|wowy_repl\|props\.wowy\|repl_delta" nba ops scripts app` minus the modules themselves (exit 1 = no match); `PY -m pytest … tests/test_layering.py` (10 passed, exit 0); `nba/props/__init__.py` imports nothing | n/a | no live module imports `wowy`, `wowy_repl`, `struct_alloc` or `repl_delta`; they are new files, imported only by `research/` and tests. No `nba/daily`, `ops/` or `configs/` file is in the diff (`git diff --name-only 5ce5e56 73998a0` outside docs, reports, research and tests = the 3 new nba/props files) | yes |
| 18 | holdout | grep of the harness; `MAX_SEASON` assert in `collect_heads_x` (eval:294); `season <= 2024` in every SQL; F1 build raises on > 2024 | n/a | no 2025 input. `HOLDOUT_ACCESS_LOG.md` needs no row: no touch is scheduled | yes |
| 19 | kill criterion vs work already done | rule lines 3, 271; `data/ops/` | n/a | there is no `wowy_struct_cache` (no arm checkpoint exists). Line 271 voids the rule only if an arm is **scored** before the freeze, and nothing was scored. Line 3, though, says the prereg commit comes "BEFORE any arm is fitted". The modeler's `check --smoke` (block 0) and my check 7 (all blocks) fitted the A0 walk. A0 is F1's arm and is not refit by this rule, but a literal reader could object. This is F1's B1, in a milder form (E1) | yes, with E1 |
| 20 | merge | `git merge-tree --write-tree --name-only main origin/agent/modeler-wowy-repl` (exit 1) | 1 file | the only conflict is `docs/PREDICTIONS_LEDGER.md`; no rule, harness or pinned file conflicts | yes |

Not applicable before a result: shuffled labels (the rule drops it as invalid for a mixture contrast; P_R and ROLE are the guards that move),
knockout and replication (no gain exists yet), and lagging every feature one day (the stamp channel, check 10, is the tip-proximity risk here).
Integer support: every arm is scored through `F1R.qi_of` (which goes to `to_integer_support`) and then `crps_int` (eval:472-473).

### Check 10 in detail: what the late-OUT channel can do to S_R minus ROLE

A correction to the caller's premise: ROLE is not OUT-blind. Every arm carries production's `vac_*` and `n_out_rot`, and `role_share_s` has the
OUT players' as-of stat in its denominator. What only S_R adds is the **per-player allocation** of the vacated production. That allocation is
exactly the part that a late OUT set (a report published about 30 minutes after T-60) makes more accurate. So the channel is real for S_R minus ROLE.

| cell | season | cell rows | rows in exposed games | share (all rows of the season) | needed per exposed row for the channel ALONE to reach the floor | SE cost of G9 |
|---|---|---|---|---|---|---|
| pts leader out | 2023 | 4,316 | 249 (21 games) | 5.8% (3.8%) | 0.087 | x1.030 |
| pts leader out | 2024 | 5,678 | 359 (30) | 6.3% (4.8%) | 0.079 | x1.033 |
| pts top 25% | 2023 | 3,568 | 229 (41) | 6.4% (3.8%) | 0.078 | x1.034 |
| pts top 25% | 2024 | 4,473 | 347 (50) | 7.8% (4.8%) | 0.064 | x1.041 |
| ast leader out | 2023 | 4,942 | 231 (19) | 4.7% (3.8%) | 0.107 | x1.024 |
| ast leader out | 2024 | 4,414 | 282 (24) | 6.4% (4.8%) | 0.078 | x1.033 |

Exposed games are a superset of the games whose OUT set changes under a +30 min stamp. The F1 re-check measured about half of exposed rows
actually changing, so the per-**changed**-row gain needed is about twice the column above: pts 0.13-0.17 (3-5% of the pts CRPS level), ast
0.16-0.21 (16-21%). That is 9-14 times the expected cell gain, so the channel cannot make a pass alone. It can add to a real gain. Suppose
changed rows carried three times the cell effect (about 0.045 pts). The channel would then add about 0.045 x 0.03 = 0.0014, which is 27% of
the floor. That is enough to push a borderline cell over. G9 requires the pass on unexposed games, which removes the channel on evaluation
rows. Power cost: the SE grows by 2-4%. The thin cell's 2023 margin falls from 1.071 to about 1.036. The training-side channel (2019-21 rows,
up to 13.8% changed in 2021) has an undetermined sign and is not a leak on evaluation rows (F1 re-check). It makes any pass conditional on the
unrepaired history stamps. The history re-stamp after opening night re-checks this rule (DECISIONS 14:57 item 3).

### The placebo design (one permutation index per player-season)

`add_permuted` (eval:264-281) draws one within-player-season index per season over ALL rows (training and test), `default_rng(0)` fresh per
season, groups in `np.unique` order, rows sorted first. The columns of an arm share the index. Two consequences, neither a blocker:
(i) a training row's permuted value can come from a later game of the same player-season. That is a feature value, not a label, but it lets
P_R carry a little future player-season role information. This makes P_R stronger and G1 stricter, so it is conservative for S_R.
(ii) t_c was sized with train and test permuted separately in a two-season design. The spread of a single draw under the walk-wide
convention is not re-measured, and the rule says so (C6, E7(d)); t_c is not re-derived. G1 power remains unsized (rule line 196). A FAIL_GUARD
on G1 with a primary pass reads "not shown to be beyond a wrong-row copy", as the text says.

### The rehearsal: is a planted-score rehearsal enough?

It is enough for `evaluate()`: words, BH, guards, twist labels, the one-sided p and the clustering are all unit-tested, and each word has a world.
It is **not** enough for the path from the fit to the scores. `build_bundle` (eval:439-477) has no functional test. That function holds the
per-arm key alignment, the outcome-equality check, the integer grid through `qi_of` with A0's `pi` / `w_q`, the PIT and the cell masks.
Neither do `training_frame` or `fit_arms` (only their call order is tested, by source text). A wiring error there, such as the wrong arm's
comps, a mask on the wrong keys, or a pic misalignment, would surface only in the real run: as a crash after 14 walks, or, worse, silently.
The G9 unit test (`test_g9_the_gain_must_hold_outside_stamp_exposed_games`) is also weak. With the gain only in the 5% exposed rows, the primary
already fails, so the test never shows that G9 by itself turns a PASS into FAIL_GUARD. Fix: E4.

## Findings and edits (severity-ranked; file:line at 73998a0; none changes the decision rule)

**E1 (must, before freeze). `docs/prereg/WOWY_STRUCT.md:3` and `:271`, fit vs score wording.** Line 3: "commits `prereg(WOWY_STRUCT)` BEFORE
any arm is fitted". The A0 walk has already been fitted twice: the modeler's `check --smoke` (block 0, 00:06 CDT) and this review (all blocks,
check 7). Neither scored anything. Fix: after "BEFORE any arm is fitted" insert "(A0_warm is F1's arm C and is never refit by this rule; the
label-free A0 reproductions, `check --smoke` and the adversary's all-block comparison of 2026-10-16, re-create F1's frozen components and
compute no score)". At line 271, after "any arm of this rule scored before the freeze commit (rule void)", insert "(the A0 reproductions above
are not a score)".

**E2 (must, before freeze). Promised outputs the harness does not produce. `docs/prereg/WOWY_STRUCT.md:64, :108, :29` vs `wowy_struct_eval.py:140-245`.**
(a) "the sensitivity label at m = 8 … is reported beside it" (line 64, again at line 108): not computed. (b) "reb and fg3m (not in scope)" in
the reported list (line 108): `STATS = ("pts", "ast")` (eval:50), so no reb or fg3m arm exists. (c) "the stamp-exposed games are
reported-only slices" (line 29): only the unexposed delta is computed. Fix: strike (b) from line 108; it would need 14 more walks.
Implement (c) in `evaluate`: `r["exposed"] = delta1(sc["S_R"][m & ~b["unexposed"]] - sc["ROLE"][m & ~b["unexposed"]], …)`, reported only,
after eval:203. For (a), either implement it as a label only (the 5 dropped cells' masks exist in `SG.cell_masks`; BH over the 8 primary p's,
printed beside the m = 3 result) or strike it from lines 64 and 108. My recommendation is to strike it: it can never rescue a cell, and a
printed m = 8 table invites a second reading.

**E3 (must, before freeze). `docs/prereg/WOWY_STRUCT.md:27`, the CI code clause.** "the house bootstrap `nba/truth/bootstrap.py` is used only if it
exists at the freeze commit, else never". The harness hard-codes `nba.props.metrics._clustered_boot_means` (eval:43, 112). If someone added
`truth/bootstrap.py` to main before the freeze, the text would require code the harness does not call. Fix: replace the clause with "the CI
code is `nba/props/metrics.py::_clustered_boot_means` as pinned; `nba/truth/bootstrap.py`, if it ever exists, is never used by this rule".
(The file does not exist on main today: `git ls-tree main nba/truth/` lists `__init__.py`, `metrics.py`, `walkforward.py`.)

**E4 (must, before pin). Missing negative and functional tests. `tests/research/wowy_struct/test_wowy_struct_eval.py`.**
(a) Add a `build_bundle` test on synthetic comps. Two arms with the arm's rows deliberately shuffled, a synthetic `pic` aligned to A0. Assert
that `score[arm]` equals `F1R.crps_int(F1R.qi_of(c, pic, st)[idx], y)` row by row, that a missing key raises "an evaluation key is missing", and
that a changed `row_y` raises "outcomes differ".
(b) Strengthen the G9 test so the primary clears on all rows (gain of 0.015 everywhere plus a large extra gain in exposed rows) while the
unexposed delta sits above -0.005 (gain of 0.003 there). Assert `word == "FAIL_GUARD"` and `not g9`.
(c) Optional: a `stamp` world in `synthetic_bundle` and the rehearsal table, as F1 had.

**E5 (should). `wowy_struct_eval.py:206-208`.** The descriptive contrasts carry a `p` from `delta1`. Rule line 64 says they "carry no p-values
into any verdict". Drop `p` from the `desc` dicts, or rename the field `p_descriptive_not_a_claim`. Otherwise a reader can quote the smallest of
48 descriptive p-values as a finding.

**E6 (should, pinned file). `wowy_struct_repl_gate.py:49-51`.** `STRUCT_GATE = Path("reports/…")` is cwd-relative and is read on the run path
(eval:395, the cell cut-offs). Use `Path(__file__).resolve().parents[2] / "reports/prereg_wowy_redistribution/gate.json"`. Extend
`test_configs_are_read_from_the_pinned_root_never_the_cwd` (test line 401) to reject `Path("reports/` in the three harness files.

**E7 (should, pinned file). `wowy_struct_repl_gate.py:48`.** `OUT = Path("reports/prereg_wowy_repl/gate.json")` is cwd-relative, and the gate is
not byte-reproducible (check 11: <= 1.1e-13 but a different sha). A re-run in the pinned checkout silently overwrites the pinned artifact, and
`run` then STOPs on `GATE_SHA256`. Fix: add an `--out` argument with a scratch default, or refuse to overwrite an existing file. Add one
sentence to the freeze steps: "never re-run the gate in the pinned checkout".

**E8 (should, pinned file). `wowy_struct_rule.py:197-223` and `:15-17`.** (a) Also require that every `HARNESS_FILES` blob at the pin commit
equals its blob at the freeze commit, so "no harness edit between the two commits" is enforced, not just stated. Add one test. (b) Use
`/Users/devin/Downloads/nba-prediction/.venv/bin/python` in the docstring (F1's m4).

**E9 (minor, pinned file). `wowy_struct_eval.py:728-746`.** Take `heavy.lock` before `WG.build_world` (the world and frame build is the first
CPU-heavy step). Today the replay oracle holds the lock (since 14:58), and `run` would build features alongside it before blocking.

**E10 (should, text). `docs/prereg/WOWY_STRUCT.md:256` (and `:35`).** Add: "At its 2022 held-out kappa (0.036, se 0.045) the same
outcome-free method gives power 0.29 / 0.38 for pts top 25% fixed (0.25 / 0.32 at the 2020-21 kappa minus one se); the cell stays in the
family as declared; a FAIL there reads 'cannot detect', not 'no effect'." Disclosure only; no criterion changes.

**E11 (minor, text). `docs/prereg/WOWY_STRUCT.md:172`.** "S+R reproduces the earlier STRUCT gate" is overstated. Write instead: "S+R
re-estimated in this harness gives the same signs and the same cell-gate result (pts leader out +1.46 / +0.78 / +1.39 here vs +1.40 / +0.86 /
+1.55 in the 2026-10-14 block); criterion (c) for S_R is now 'P+R beyond ROLE < S+R beyond ROLE' (earlier: 'permuted vs base < STRUCT vs
base'); both hold in all three cells".

**E12 (minor, text). `docs/prereg/WOWY_STRUCT.md:111` and `:251`.** At line 111, replace "ROLE carries no OUT information at all" with "ROLE
carries the team-level OUT information (`vac_*`, `n_out_rot`, and the OUT players' stat in its denominator) but no per-player allocation". At
line 251, "written to a scratch directory here" contradicts the committed `reports/prereg_wowy_struct/rehearsal.json`. Say it is committed and
that it reproduces (check 15).

**Nits (no fix forced).** `docs/prereg/WOWY_STRUCT.md:259` quotes `check` at 55 s, and I measured 31 s without `--smoke`. G5's playoffs slice
(`gid` prefix `004`) excludes play-in (`005`); it is never gated anyway (n < 300). The twist label TWIST_BETTER is reached only in a unit test,
never in a rehearsal world. The branch also carries the earlier unmerged WOWY_REDISTRIBUTION draft and gates (the 33-file diff), so merge by
path.

## Corrected family, multiplicity and power

- **Verdict family (unchanged):** S_R minus ROLE x {pts leader out, pts top 25% fixed, ast leader out}, BH at q = 0.05 over m = 3 within each
  season, select 2023, report 2024, both required. The cell gate's extension to T_R added no cell and removed none, so S_R's family is exactly
  the reviewed one. The T_R exclusion is robust: it fails (c) in every cell, independent of its power.
- **Family actually run:** 3 S_R tests per season enter BH. The descriptive set is 8 pairs x 3 cells x 2 seasons = 48 intervals, plus the
  twist labels, the G5 slices and the G9 subsets. Not a family and never a claim (E5). The forking-path list (items 1-10) covers every dev look
  I found: WOWY columns, versions, PROP, the minutes variant, 8 cells, the cold check, the hand-switched criterion (a), the G5 cuts, the twist
  construction and the extended gate.
- **Power and MDE (version B, outcome-free, C forecasts):** per cell-season half-width 0.0101-0.0109 (pts leader out), 0.0099-0.0106 (pts top
  25%), 0.0060-0.0069 (ast). The MDE at 80% power is about 1.43 x the half-width: pts about 0.015, ast about 0.009. The CI condition, not the
  -0.005 floor, binds for pts. Joint two-season power at the gate kappa is 0.72 / 0.39 / 1.00. At the held-out 2022 kappa it is about 0.97 /
  0.11 / 1.00. G1 is unsized and binds before the primary for both pts cells (about -0.008 to -0.011 implied). G9 widens the SE by 2-4%.
  The likely shape of the result: ast leader out decides, pts leader out is plausible, and pts top 25% probably reads "cannot detect".

## Playbook check (docs/MODEL_PLAYBOOK.md; gradient-boosted trees on tabular features)

1. **Information or capacity:** information (tonight's OUT list x the as-of pool; for T, also each pair's past with and without j).
   Capacity is controlled by P_R / TP_R (wrong-row copies), TQ (wrong identity) and ROLE (role share alone). The claim contrast is S_R minus
   ROLE, so a role-share or capacity gain cannot pass as redistribution. PASS.
2. **Enough data for this class:** with E7 the first 2023 block trains on 102,924 rows for pts (about four seasons). Both seasons must pass.
   The twist's dev designs train on one to two seasons, which is thin for four columns. The text says this, and T_R is descriptive. PASS.
3. **Test valid:** real tips asserted; integer grid in every arm; A0 = F1's frozen C, equal to a fresh walk on every block (check 7);
   planted future passes on earlier rows and on **tonight's own box** (check 9); NULL pattern flat within as-of strata (check 13); G9 closes
   the late-report channel on evaluation rows (check 10); game-clustered CIs from the pinned house draws. Weak points, stated in the text:
   t_c's squared-error-to-CRPS conversion (checked once on 2022), box-approximated possessions (for T only), and the training-side stamp
   channel. Not invalid. The one gap is a missing functional test of the scoring path (E4).
4. **Lineage and inherited guards:** T047, production `vac_*`, TEAM_BUDGET T446-T449, F11, WOWY_REDISTRIBUTION, MINUTES_V2 T166-T167,
   RECENT_ROLE_FULL T467-T473 (passed 2023-24, failed the 2025 touch: the reason a PASS here is only a candidate), REPLACEMENT_DELTA (the
   `rb` half, closed), F1 T498 (the window helped 2023 only). Carried: integer support, real tips, permutation-null sizing, planted future,
   NULL rule, clustered CIs, no-harm, G9 (F1's re-check). PASS.
5. **Scored against:** whole-distribution integer CRPS vs ROLE (verdict) and A0_warm (G2, descriptive), not the market line. Correct for a
   feature question. A pass says nothing about edge vs the market (T477).

Class checks: missingness beyond the as-of cause (none, check 13); planted future (pass, checks 8-9); does the arm only re-express the base?
`rb` correlates 0.56-0.85 with the STRUCT column, which is why TQ and TP_R exist; for S_R, ROLE and P_R answer it.

## Single biggest risk

A pts pass driven by late-report rows plus an in-sample kappa. G9 neutralises the first part. The second part is the thin cell's 0.56 power,
which rests on a 2020-21 coefficient that held out at two thirds of its size with an se larger than itself. The risk is not a false pass,
because BH, both seasons and G1 all stand in the way. The risk is that a FAIL in that cell gets read as evidence against redistribution. E10
puts the reading in writing before the result.

## What was not verified

- The A0 mixture components (`pi`, `w_q`) against a fresh walk: pinned by hash and tag only.
- `fit_arms` / `build_bundle` on real data: they run only in `run` (E4 adds the synthetic test).
- The size of the evaluation-row stamp interaction: bounded above, not measured (only a fit could measure it).
- G1's power and t_c's spread under the walk-wide permutation convention.
- The actual-starter missingness group (box starter not loaded).
- That every minute-0 stamp is an hour-only file: F1's exposure is an upper bound, so G9 over-excludes, which is the safe direction.
- LightGBM determinism across all 14 arm walks with `n_jobs` > 1: the A0 walk was bit-exact on 36 blocks today, which is evidence, not proof.

## Freeze, pin and run steps (after E1-E12 land and a diff re-check approves the NEW sha and pin)

The modeler applies the edits on `agent/modeler-wowy-repl`, runs `pytest --no-cov tests/research/wowy_struct tests/props/test_wowy_repl.py`,
`ruff check`, `ruff format --check` and `mypy` unpiped, and does **not** re-run the gate into the pinned path (E7; `GATE_SHA256` stays
`6be61a4f…`). Then commit and push; the adversary diff-checks the listed lines. Then the main session, from the modeler worktree
(cwd = the checkout holding the commits = `rule.ROOT`):

```
cd /Users/devin/Downloads/nba-prediction-wt-wowy-repl
git status --short                                   # pinned files and the rule clean
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.wowy_struct_rule freeze
#   must print the sha the diff re-check approved (not cf4d3751: E1-E3, E10-E12 change the text); else STOP
git commit --allow-empty -m "prereg(WOWY_STRUCT): <sha>"
/Users/devin/Downloads/nba-prediction/.venv/bin/python -m research.eval.wowy_struct_rule pin
#   must print the pin the re-check approved (not f64c9ce6: E4, E6-E9 change pinned files); no edit between the two commits
git commit --allow-empty -m "harness(WOWY_STRUCT): <pin>"
git push origin agent/modeler-wowy-repl
NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.wowy_struct_eval check --data-root /Users/devin/Downloads/nba-prediction       # mismatch_vs_rule must be {}
ls /Users/devin/Downloads/nba-prediction/data/ops/heavy.lock 2>/dev/null && echo "WAIT: another heavy job holds the lock"
NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore \
  -m research.eval.wowy_struct_eval run --data-root /Users/devin/Downloads/nba-prediction --sha <sha> \
  > /Users/devin/Downloads/nba-prediction/data/ops/wowy_struct_run.log 2>&1     # background; 14 walks, about 15-40 min; takes heavy.lock
```

No holdout row is needed (no 2025). The run is the maintainer's command (longer than the 600 s watchdog). Results go to the ledger with n and
the game-clustered CI per cell-season, the twist label beside the word, and the descriptive T_R contrasts without p-values.

## Proposed rows (drafted, not appended)

Ledger: none (no result). DECISIONS (main session):
`2026-10-16 | WOWY_STRUCT pre-freeze review of the replacement-twist revision (docs/reviews/WOWY_STRUCT_prefreeze_2026-10-16.md): APPROVED WITH EDITS for 73998a0 (sha cf4d3751, pin f64c9ce6). Sha, pin, closure (74 files), F1 tags, counts, priors and planted future reproduce; the A0 walk equals F1's arm-C cache on all 18 blocks of pts and ast (max abs diff 0.0); tonight's-box plant leaves S and T columns unchanged; gate re-run reproduces to 1e-13. No edit changes the decision rule: fit/score carve-out for the A0 reproductions, strike or implement the promised m = 8 / reb-fg3m / exposed-slice outputs, pin the CI code clause, a build_bundle functional test and a real G9 test, cwd-relative gate paths, freeze-to-pin blob check, thin-cell disclosure (power 0.29 / 0.38 at its 2022 held-out kappa). Reverse if: the diff re-check finds a new contradiction.`
