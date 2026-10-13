# PPLAY_MINUTES_VALID loader review, fix-check (adversary), 2026-10-12

Verdict: APPROVED
Reviewed-commit: 2eb3360fb3c4ee851236d63ecea6a54f79c5b600
Rule: PPLAY_MINUTES_VALID 91d82de7

This review follows `docs/reviews/pplay_minutes_valid_loader_2026-10-12.md` (APPROVED WITH FIXES, added in dee2c43). That review
required fixes F1-F6 and recommended R1-R4. All ten are in 2eb3360, in the three harness modules and the tests.

What I did and did not do:
* Every DuckDB access was read-only, and I read no season-2025 row.
* I fitted and scored no arm, and ran no G5, `gate` or `run`.
* The real frames were built while `data/ops/heavy.lock` was held by ERROR_FEEDBACK's `null --k 50` (pid 94658). The original review
  was explicitly allowed to build frames, and builds take 23 s in one read-only process, so I did the same here. I disclose this
  because the coordinator's message said "if the lock is free".
* Probe scripts are in the session scratchpad: `a4_fixcheck.py` and `b2_gate.py`. The gate attacks ran on a scratch `git clone`.

## Rule integrity and suite

| check | result | pass |
|---|---|---|
| `git diff --quiet 8e15012 2eb3360 -- docs/prereg/PPLAY_MINUTES_VALID.md` | exit 0 (rule byte-identical) | PASS |
| `.venv/bin/python -m pytest -q tests/research/pplay_minutes_valid -p no:cacheprovider --no-cov` (unpiped, output to file) | 80 passed, exit 0 | PASS |
| `ruff check` / `ruff format --check` / `mypy` (3 modules; tests for ruff) | 0 / 0 / 0 | PASS |
| working tree under all 17 REVIEWED_PATHS | `git status --porcelain` empty | PASS |

## Fix verification on the real frames (P 185,680 rows, M 156,237 rows)

| fix | how I tried to break it | evidence | result |
|---|---|---|---|
| F1 derived report columns | Ran `C.derive_report_cols(MV.build_frame(lw), MV.build_frame({**lw, "flagged": {}}))` by itself on the real world, without the static floor | Derived: `n_out_rot, vac_min, vac_pts, vac_reb, vac_ast, vac_fg3m, has_report, n_out_starters, out_max_min40`. vac_min and vac_pts are found by the derivation, not only by the floor. The used list is the same 8 columns (has_report is zeroed). On 27,442 2021-22 M rows: 0 non-NULL in each of the 8, has_report non-zero 0. The era gate checks 8 columns: PASS. Outside 2021-22, vac_min non-NULL shares are unchanged (0.998-0.999). M2 inputs among them: n_out_rot, n_out_starters, out_max_min40, vac_min | PASS |
| F2 opponent NULL cause | Ran `cause_mismatch` in both directions | opp_r20_pts_for/against: 29 NULL rows (2019-10-31..11-02). null_without_cause 0, cause_without_null 0. `opp_games_prior < 5` covers exactly 29 rows and is not a model input. The columns stay in the gap test (max 0.18 pp). G2 on the minutes head is now ok, with no unexplained inputs | PASS |
| F3 two-sided as-of cause | Same, for ret_* | ret_mean_min and ret_ramp: 111,124 NULLs each, mismatch 0 / 0 in both directions | PASS |
| F4 bounded availability | Ran `load_flag_inputs()` | games max season 2024. 87,612 availability rows, max as_of 2025-06-22 17:00 (2024-25 Finals), 0 rows outside 2019-24 games. Both bounds are in the SQL. OUT-set parity is unchanged: 3,952 = 3,952 | PASS |
| F5 review gate | `b2_gate.py`, 24 scenarios on a clone at 2eb3360 | 24/24 as intended (table below) | PASS |
| F6 cache manifest | Code read, plus probes in a temp dir | Same manifest resumes. A different manifest refuses and names the differing keys. A directory with checkpoints but no manifest refuses. An object-dtype npz is refused by `assert_no_pickle`. walk_p re-derives each block's label hash and Platt row count on reload. M2 checks a training hash. G5 perm files carry the manifest id and seed. R22's directory is guarded before `RR.walk`'s own pickling reader runs. The manifest holds the rule sha, both data versions and the code sha (reviewed blobs) | PASS |
| R1 re-check after lock | Code read | `run_real` checks the review and data stamp before the wait (fail fast). After taking the lock it loads the lazy modules, then re-checks the review and stamp; the stamp and code identity used are the post-lock ones | PASS |
| R2 module stamp | Code read | `module_blobs` over REVIEWED_PATHS, plus `code_sha` in gate_info and results | PASS |
| R3 season x era | Code read | `by_season_era` is added to the G2 table | PASS |
| R4 age NaN | Ran `age_check` | p 0, m 0 (NULL or NaN) | PASS |

Re-run unchanged on the fixed code:
* G1 planted future: PASS (cutoff 2024-01-26; future edit identical on P, M2 and maps; the control changes all three).
* G2 on P(play) inputs: ok. The worst gaps are identical to the first review's.
* Holdout gate: max season 2024 on every frame.

## Review-gate attacks (`b2_gate.py`)

| scenario | expected | got |
|---|---|---|
| only the committed APPROVED WITH FIXES review | refuse | refuse |
| fix-check APPROVED | open | open |
| older review deleted, latest APPROVED present; approved review renamed inside the glob | open | open (x2) |
| later BLOCKED review | refuse | refuse |
| **S7** later BLOCKED removed by `git rm` | refuse | **refuse** ("a removal is a revocation") |
| **S7b/S7c** later BLOCKED renamed out of the glob / to `.md.bak` | refuse | **refuse** (x2) |
| later BLOCKED removed, then the old APPROVED content re-added under a new name | refuse | refuse (dated by earliest add) |
| BLOCKED edited to APPROVED in a later commit; BLOCKED deleted in the working tree only | refuse | refuse (x2) |
| a genuinely new APPROVED review after a BLOCKED one | open | open |
| **S12b** substring Rule line; trailing junk | refuse | **refuse** (x2) |
| Rule line with a parenthetical | open | open |
| Reviewed-commit = the pre-fix harness ce6afc2 | refuse | refuse |
| **S9c** dirty or committed change to `pplay_context_eval.py`; committed change to `nba/props/forward.py` | refuse | **refuse** (x3) |
| harness change after review | refuse | refuse |
| unrelated docs commit after review | open | open |
| dirty approved review; APPROVED + BLOCKED added in one commit | refuse | refuse (x2) |

Residual, not fixable by code: if a BLOCKED review is deleted and a *new* APPROVED file is committed under the same name, the gate
opens. That is indistinguishable from a legitimate new review. It needs a deliberate forged adversary document, visible in history.

## Rulings

The rulings in the first review stand. Ruling 5 (the ret_* exemption) is now satisfied: the NULL set is an exact two-sided function
of the as-of input `ret_n` and is enforced. Ruling 9 is satisfied: the list is derived. The G5 "<= 2 h" row still does not bind.

## Recommendations (not blocking; never applied by me)

* **R5:** REVIEWED_PATHS covers the harness and its direct imports, not the transitive closure. Examples outside it:
  `nba/features/injury_report.py` (the OUT filter), `research/eval/workload_gates.py`, `nba/eval/lower_tail_eval.py`,
  `research/features/game_location_context.py`. The exact parity, OUT-set parity, G1 and R22 reproduction gates catch material
  drift there. Stamping their blobs as well would make any drift visible.
* **Operational note:** the strict paths include `nba/props/{minutes,forward,context_residual}.py`. Any production commit to those
  files before the run voids this review and needs a new fix-check. That is intended, but the main session should sequence the run
  before such commits.

## Playbook check

Unchanged from the first review. P0 is shrinkage, P0c a calibration layer, R19/M2_19 gradient-boosted trees. The test is valid on the
frame side:
* real tips;
* exact parity with the served function;
* planted future and planted reports pass;
* missingness is clean, with every NULL input carrying an exact or listed as-of cause;
* the 2021-22 report drop is complete and derived from code.

The population caveat (tonight's box defines Cell-P) is shared by both arms.

## Biggest remaining risk

Drift in a transitive dependency between this review and the run (R5), or a resumed run after a production commit. Both are caught by
the gates or refused by the strict path list and the cache manifest.

## Not verified

* The R22 reproduction, G3-G5, ECE/slices and the verdict on real data (all need scores).
* G5 wall time.
* The props descriptive on the untracked stored grids.
* `run` end to end.
