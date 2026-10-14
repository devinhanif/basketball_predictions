# AVG_SHADOW_LIVE_2: review of the T442 gate-row assembler (`gate_rows.py`, 2584d1b) and the 2026-10 bundle

Reviewer: adversary, 2026-10-14. Rule: AVG_SHADOW_LIVE_2, FROZEN, sha256 `9afbfeb6` (re-hashed with the rule's own awk command:
`9afbfeb6e16449f3...92fa87`, matches `nba/daily/avg_shadow/__init__.py:10`). Object: `research/colab/jobs/avg_shadow_rebuild/gate_rows.py`
on `agent/steward-avg2-proofs` @ 2584d1b (109 lines, the only file in the commit; no scope creep), its outputs in
`data/avg_shadow/gate_work/`, and the bundle `data/avg_shadow/incoming/2026-10`. The build review required this assembler to come
here (docs/reviews/AVG_SHADOW_LIVE_2_build_2026-10-14.md, recommendation 3).

Model class: a T442 SoftmaxGate (a small linear-softmax stacker, playbook "Linear and logistic models") over a neural sequence model
(E2 W_ALL, "Neural sequence models") and the gradient-boosted A0 mixture ("Gradient-boosted trees").

**Season-2025 hygiene of this review.** No season-2025 forecast, label or covariate value was read. Method: `gate_rows.npz` is stored
uncompressed, rows are season-sorted, and only the season<=2024 prefix of every member was read (column-major members by seeking past
each column's tail); in `oof_E2.npz` only the `season` id array was read in full, and `game_id`, `player_id` and `q` were
stream-decompressed up to the last season<=2024 row (asserted to be a prefix). The 2025 meta rows were excluded by a parquet
`season <= 2024` filter. `metrics.json` and `info_E2.json` in the run folder were not opened. gate B (cap 2025) was **not** re-run,
because it builds the season-2025 frame. No metric of any season was computed: every number below is a count, a hash or an
equality boolean, so the n-and-CI requirement does not apply (there is no effect estimate).

## The five questions asked

| # | Question | Answer |
|---|---|---|
| 1 | Alignment by keys, no duplicate keys | **Pass for seasons <= 2024 (re-derived by an independent dict-on-keys join, exact); 2025 tail pass by indirect evidence only.** See S1. |
| 2 | Strictly earlier rows (no leakage into the gate's fit window) | **Pass.** Gate rows span 2023-10-24..2026-06-13; the gate serves month 2026-10; 2026-06-13 < 2026-10-01. The one-fit-on-every-earlier-row equals T442's 30-day walk for a block starting after 2026-06-13 (`router.walk_forward` trains on `dates < start`, expanding). |
| 3 | 2025 rows used only as gate training rows, nothing outputs a 2025 metric | **Pass on code; one hygiene finding (S2).** No call to `score_world`, CRPS, pinball or any contrast; the report prints counts, dates and the four gate-B booleans only; `gate_rows.log` has 0 lines matching `crps|delta|brier|loss|pinball|cover|ece|contrast`. `gate.json` holds encoder statistics and coefficients only. |
| 4 | Reproduces the stored 2024 gate inputs exactly | **Pass, by a stronger check than the steward's.** The steward's 4/4 is gate B (A0/FULL integer grids of 2024 regenerated under cap 2025 equal the stored arrays); it does not test the npz. I re-derived every 2023/2024 field of the npz from the stored caches and the OOF file: 9 fields x 4 stats x 55,320 rows, all exact (table below). gate B itself was not re-run (needs the 2025 frame). |
| 5 | Bundle matches the rule | **Pass.** All five data-file sha256s recomputed equal the manifest; `model_id` b8d2b27b recomputed by `bundle verify` (exit 0); weights shas equal `bundle_parts.json` (the notebook's record); `oof_sha256` 9772d3d3 equals the file on disk; `gate.json` 58043d34 equals `gate_work/gate.json`; `rule_sha256` 9afbfeb6; `month_block` 2026-10; `fit_through` 2026-06-13 < 2026-10-01; `gate_fit_through` 2026-06-13 equals the assembler's `date_max`; `n_fit` 83,493 per stat = npz rows (>= 3,000). Not installed (`installed_at` None), as it should be. |

## Checks with commands

All commands run unpiped; exit codes shown. Scratch script: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/4a3e37f1-4418-426a-8cc6-897f102142f0/scratchpad/avg2/rederive.py` (not in the repo).

| Check | Command | n | Result | Pass |
|---|---|---|---|---|
| npz shapes, no join expansion | `python -I scratchpad/avg2/npzhdr.py data/avg_shadow/gate_work/gate_rows.npz` (headers only, no data read), exit 0 | 40 members | every member has 83,493 rows; a duplicated key in the OOF or meta would lengthen the joined side and break this | pass |
| re-derivation of 2023/24 by keys | `.venv/bin/python -W ignore scratchpad/avg2/rederive.py /Users/devin/Downloads/nba-prediction`, exit 0 | 55,320 rows (2023 27,619; 2024 27,701) x 4 stats | `a0_199`, `seq_199` (max abs diff 0.0), `y`, `date_days`, `n_prior`, `team_game_no`, `has_report`, `n_rep_out`, `starter10`: exact for pts, reb, ast, fg3m | pass |
| planted misalignment | same script: the npz seq rows rolled by one row compared with the reference | 55,320 | detected for all 4 stats (the comparison has power) | pass |
| duplicate keys | same script | stored 55,320; OOF <= 2024 56,433; T478 RT meta | 0, 0, 0 duplicates; 0 stored keys missing from the OOF | pass |
| 2025 meta vs T478 meta on shared rows | same script, `cur25_meta` filtered to season <= 2024 | 55,320 | all four context columns identical; files byte-identical anyway (`shasum`: rebuild and T478 `seq_ensemble_rt_cur25_meta.parquet` both `fdf575ae`; export npz both `316d8e1e` = manifest `export_npz_sha256`) | pass |
| research code identity | `diff -rq` main checkout vs branch `research/eval`, `research/stack` | shared files | identical (only extra untracked files in main) | pass |
| bundle | `PYTHONPATH=. .venv/bin/python -m nba.daily.avg_shadow.bundle verify data/avg_shadow/incoming/2026-10`, exit 0; `shasum -a 256` on all files | 5 files | listed in question 5 | pass |
| rule hash | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/AVG_SHADOW_LIVE_2.md \| shasum -a 256` | 1 | 9afbfeb6 | pass |
| lint/type | `ruff check` exit 0; `ruff format --check` exit 0; `mypy` exit 0 (the file) | 1 file | clean | pass |

The six adversary checks of a claimed win (shuffle, planted future, missingness, knockout, replication, integer support) do not apply:
this is an assembler, not a result, and nothing is scored. The planted-misalignment row above is the analogue that applies.

## Findings

**S1 (SUBSTANTIVE, not blocking this bundle; required before the next refit).** The assembler aligns by key joins whose output is then consumed
**by position**, and polars 1.44.2 (the venv) documents `maintain_order` default `"none"`: "the ordering might differ across Polars
versions or even between different runs". Joins concerned: `research/eval/seq_full_mix_eval.py:438` (`load_seq_run`, SEQ to A0 rows),
`:764-765` (`target_base`, the 2025 covariates and `date_days`), `:375` (`rt_covariates`), `research/eval/near_miss_bundle_eval.py:362`
(stored dates). The npz (`gate_rows.py:102`) carries no `game_id`/`player_id`, so `fit_gate.py` cannot re-check alignment. Evidence that
it did not bite here: the SEQ attach is ONE join over all 83,493 rows (`gate_rows.py:78`), and its 55,320-row 2023/24 prefix is exact
against a dict join; `rt_covariates` (same join shape) is exact; a reorder of the 2025 `target_base` join would break `combine_world`'s
date-monotone check unless it permuted rows only within a date, which that check cannot see. **Fix:** in `gate_rows.py`, save
`{st}_game_id` / `{st}_player_id` in the npz, and after line 78 assert, by a dict (not a join), that `seq[st]` equals
`E.ext19(oof_q[idx])` and that the context columns equal the meta values for every row; in the four joins pass
`maintain_order="left"` and assert the joined keys equal the left keys. `fit_gate.py` should then assert the keys are unique.

**S2 (SUBSTANTIVE, hygiene; before install).** `data/avg_shadow/gate_work/gate_rows.npz` (1.08 GB, sha256 `15bc438e...`) keeps the
season-2025 A0 and E2 forecasts next to the 2025 labels after the gate fit, a scoring-ready file. HOLDOUT_ACCESS_LOG row 72 says the
2025 OOF forecasts are "consumed only by the gate fit"; the gate is fit. **Fix:** Devin deletes `gate_rows.npz` (agent deletes are
blocked) and the archivist adds the drafted row below with its sha; keep `gate_rows_report.json` and `gate.json`. If the steward wants
the npz for the November refit, say so in the log row instead; the later months add rows to it, they do not need 2025 re-read.

**C1 (COSMETIC).** `gate_rows.py:28` `REBUILD_META` is unused (`--meta` is required with no default) and the report
(`gate_rows.py:103`) records neither the paths nor the sha256 of its inputs (run folder, `oof_E2.npz` 9772d3d3, meta fdf575ae) nor of
its output npz; the chain OOF -> npz -> `gate.json` is reconstructable only by this review. **Fix:** delete the constant or use it as the
default; write `inputs: {oof_sha256, meta_sha256, run}` and `npz_sha256` into `gate_rows_report.json`, and `aligned_sha256` into `gate.json`.

**C2 (COSMETIC).** `gate_fit_through` is hand-typed to `assemble_bundle.py --gate-fit-through`; `fit_gate.py:86` asserts only
`max(date) <= fit_through`, and `bundle.select_bundle` (`nba/daily/avg_shadow/bundle.py:109`) checks `fit_through < first` but never
`gate_fit_through < first`. Correct today (2026-06-13 = the assembler's `date_max`). **Fix:** `fit_gate.py` writes the actual max date
into `gate.json`; `assemble_bundle` reads it; `select_bundle` also requires `b.gate_fit_through < first`.

**C3 (COSMETIC).** T478's gate A (2024 regenerated under cap 2024) is not run; gate B (cap 2025) is the one that matters for the claim
"raising the cap does not move 2024", so nothing is lost. No negative test exists for the assembler (a shuffled-key OOF and a
duplicated key must fail); add one with the S1 fix.

## Playbook check

1. Information or capacity: the gate adds no information; it learns per-row weights over two forecasts from five pre-tip context
   columns (capacity). Its inputs are the real-tip covariates (T478 RT lineage), verified equal here for 2023/24.
2. Enough data for this class: a 12-coefficient linear gate on 83,493 rows per stat; ample (minimum 3,000).
3. Test valid: not a test; the assembler is a training-data builder. The validity question is alignment and as-of, checked above.
4. Lineage: T442 `SoftmaxGate` + `ContextEncoder` via `fit_gate.py`; T478's `forecast_members`, `target_base`, `combine_world`,
   `attach_seq` reused verbatim (gate B carried; gate A dropped, C3). Class check (linear): "a feature constant across rows of a date is
   an era flag in disguise": `team_game_no` is close to that (a calendar proxy), inherited from T442 and frozen by the rule; noted, not
   reopened.
5. Scored against: nothing here; the gate feeds SEQc, scored only on the live 2026-27 log.

## Single most likely failure mode

A within-date reordering of the 2025 rows by the order-unspecified polars join in `target_base` (S1), which would scramble the gate's
context for a third of its training rows without tripping any existing check. Indirect evidence says it did not happen; no direct check
exists for the 2025 tail without reading it.

## What was not verified

* The 2025 tail alignment directly (would need the 2025 keys; out of bounds for the adversary). Indirect evidence in S1.
* gate B under cap 2025 (needs the 2025 frame). The maintainer's command, if wanted, is the steward's own:
  `PYTHONPATH=<repo> .venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/gate_rows.py --run data/colab/runs/avg_shadow_rebuild/20261014_112103 --meta data/colab/avg_shadow_rebuild/seq_ensemble_rt_cur25_meta.parquet --root <repo> --out <dir>/gate_rows.npz`, then compare the npz sha to `15bc438e` (a second training-class use; log it first).
* That `gate.json` (58043d34) is exactly `fit_gate.py` on this npz (refitting reads the 2025 rows). Consistent: `n_fit` 83,493 x 4, columns as `GATE_COLUMNS`.
* Which `--meta` path the steward passed (not recorded, C1); both candidates are byte-identical, so it does not matter.

## Recommended changes (not applied by the adversary)

1. Before install: S2 (delete `gate_rows.npz`, log row below).
2. Before the November refit: S1 (keys in the npz, dict-based post-join assertions, `maintain_order="left"`), with the C3 negative test.
3. Any time: C1, C2.

## Drafted rows (not appended)

HOLDOUT_ACCESS_LOG (`docs/HOLDOUT_ACCESS_LOG.md`):
`2026-10-14 | AVG_SHADOW_LIVE_2 T442 gate rows (gate_rows.py 2584d1b; data/avg_shadow/gate_work/gate_rows.npz sha256 15bc438e, 83,493 rows: 2023 27,619 / 2024 27,701 / 2025 28,173) | training use only (rule use 2, row 72) | 2025 A0 regenerated, rebuild E2 OOF, labels, as gate training rows | NOT a confirmatory touch; nothing scored; gate.json 58043d34 fit; npz DELETED unread by Devin at <time>. Adversary review read only the season<=2024 prefix (docs/reviews/AVG_SHADOW_LIVE_2_gate_rows_2026-10-14.md). |`

DECISIONS: none.

Verdict: APPROVED
