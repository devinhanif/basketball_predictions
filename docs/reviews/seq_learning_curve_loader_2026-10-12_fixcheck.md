# SEQ_LEARNING_CURVE loader review, fix-check (adversary), 2026-10-12

Verdict: APPROVED
Reviewed-commit: acc4e61495eebb7ba9d97884262ac756b3fbb6a7
Rule: SEQ_LEARNING_CURVE ad0408a7
Exports-manifest: 9bf217e60af7a62d5b3e932fcee09f4044b764e4474277e9a81468cf2de46064

This follows `docs/reviews/seq_learning_curve_loader_2026-10-12.md` (APPROVED WITH FIXES, added in 59c0bf3). That review's fixes
F1-F9 are at acc4e61, in four files: `research/eval/seq_learning_curve_rule.py`, `research/eval/seq_learning_curve_eval.py` and two test
files. The builder rebuilt the exports from the committed code. Its report says manifest 9bf217e6... with 12 of 12 files byte-identical;
I re-hashed the six npz files in the first review. No training, no Colab, no 2024/2025 scoring. All DuckDB and git access was read-only.
Probe scripts are in the session scratchpad (`b1_f1.py`, `b2_f2.py`).

## Fix verification

| fix | how I tried to break it | evidence (unpiped, exit codes shown) | result |
|---|---|---|---|
| F1 verdict and code binding | `b1_f1.py`: live `require_review()` at HEAD 59c0bf3; then `check_review_text` with crafted texts against the real repo and the real staged exports | Live gate refuses: "Verdict is 'APPROVED WITH FIXES' ... refusing". Refused: `**APPROVED**`, `APPROVED WITH FIXES`, `approved`, BLOCKED placed before an APPROVED line (first line wins), Reviewed-commit b695b5d (reviewed paths changed since), a 7-hex sha, a non-existent commit, a wrong manifest, rule 1081eeed (the superseded freeze), a Reviewed-commit not before the review's adding commit. Opens: exactly this file's header | PASS |
| F1 residual (found here) | edit an EARLIER review's committed header (e.g. the first review rewritten to `Verdict: APPROVED` / `Reviewed-commit: acc4e61`, still dated by its adding commit 59c0bf3) | `check_review_text(..., rc=59c0bf3)` OPENS | Hole, but it needs a deliberate rewrite of an adversary document, visible in git history. It is moot once this file is the latest review. Recommendation R1, not blocking |
| F1 Colab side | can an unapproved or forged Colab run be scored? | `check_run` requires `cfg.review_commit` = the approving review's adding commit and the run's exports manifest = the review's manifest. A run attested before this file is committed cannot carry this file's commit sha, so it cannot be scored without hand-editing artifacts. The notebook's acceptance of any attestation therefore does not matter for the decision path | PASS (R2 hardens it) |
| F2 gate first, curve last | `b2_f2.py`: real 2023-24 world (27,619 rows), run #1 OOF standing in for every arm, a recording `load_oof`, CACHE in scratch, five scenarios | repro fail → VOID, loaded {RAW_1, RAW_4}, cache {RAW_1, RAW_4}. Budget fail → INVALID (budget), same two. NORM_8 unfinished → INCONCLUSIVE (unfinished), same two. Runtime fail → VOID, nothing loaded, nothing cached. No NORM/NREP/RAW_8 arm was ever loaded or cached | PASS |
| F3 one configuration | code read plus the tests: `check_run` checks hidden, cold/warm configs, seeds per arm (NREP 3-5), rule sha, n_test 28,197, smoke, variant, per-info exports and review commit, and duplicate or unknown arms; any mismatch makes runtime fail → VOID | key names match what the tail writes (`hidden`, `cold_cfg`, `warm_cfg`, `seeds`, `n_test`, `smoke`, `variant`, `exports`, `review_commit`; config `cold`/`warm`/`seeds_main`/`seeds_rep`) | PASS |
| F4 cap definition | code: `fit_reached_cap` = `epochs_run >= 25 and best_epoch >= 21`, computed in the scorer, never trusted from the job; per-arm counts, NORM-only share and arms with every fit at the cap are printed | matches ruling 1 | PASS |
| F5 missing reproduction arm | `b2_f2.py` scenario with RAW_4 missing | INCONCLUSIVE (unfinished); nothing loaded | PASS |
| F6 norm_c source | code: `meta_from_exports` reads norm_c from the attested S3 npz (its sha is bound to the manifest) | first review: npz norm_c == meta parquet and same row order | PASS |
| F7-F9 | code read | seed-noise figures relabeled; `allow_pickle=False` on OOF and S3 loads; ceiling and seed-noise disclosures written into results | PASS |
| suite | `pytest -q tests/research/seq_learning_curve -p no:cacheprovider --no-cov`; `ruff check`; `ruff format --check`; `mypy` (2 changed modules) | 42 passed, exit 0; ruff 0; format 0; mypy 0 | PASS |

## Rulings on interpretations 5, 6, 9, 10, 12 (fixed now)

- **5 (review content not read):** superseded by F1. Consistent now.
- **6 (job.yaml cannot carry the prereg id):** consistent. The rule's identity travels as the sha baked into the notebook, the staged
  document and the attestation, and `touches_holdout: false` / `holdout_scored: false` are correct. Not a defect.
- **9 (A0/FULL caches holding 2024 rows, dropped on load):** consistent. First review checks 2 and 10: everything before the drop is
  row-local, and run #1's 2023 figures reproduce bit-for-bit after it.
- **10 (slices report A and B on the same bootstrap draws):** consistent. The rule asks for n and a clustered CI per slice. Reusing the
  one set of 2,000 game draws is the rule's paired design. Draws with no row in a slice are dropped, and the slice's n and games are
  printed. Descriptive only.
- **12 (generated train.py reformatted by ruff, same content):** consistent. At b695b5d the committed train.py and ipynb equal
  `build()` byte for byte, and neither changed in acc4e61. The model code equals run #1's.

Rulings 1-4, 7, 8 and 11 from the first review stand unchanged.

## Recommendations (not blocking; never applied by me)

- **R1:** `require_review` should also require the chosen review file to be unchanged since its adding commit
  (`git diff --quiet <rc> HEAD -- <review>`), so a committed rewrite of an adversary file's header cannot open the gate.
- **R2:** the scorer should refuse a run whose artifact stamp (or the earliest info file) predates the approving review's commit time,
  as a second guard against a Colab run made before approval.
- **R3:** `REVIEWED_PATHS` covers this rule's own files, but not the scorer's imported modules (`research/eval/seq_full_mix_eval.py`,
  `ensemble_eval`, `near_miss_bundle_eval`, `recent_role_full_eval`, `research/stack/*`, `nba/eval/context_residual_eval`, ...) or the
  untracked caches under `reports/lower_tail/`. The FULL-A0 1e-6 identity and the RAW_1/RAW_4 reproduction gates catch most drift.
  Recording the sha of those files and caches in `results.json` would make any drift visible after the fact.

## Playbook check (neural sequence model)

Unchanged from the first review: data volume only; the learning curve is the playbook's demanded test; integer support for every arm;
paired game-clustered CI; reproduction anchors re-derived exactly; the bootstrap omits training-seed noise, which only the single
replicate set addresses. These fixes change only the gates and the scorer's order, not the model or the test.

## Biggest remaining risk

An artifact-level tamper (R1, R2), or drift in the scorer's dependencies (R3). Both need either deliberate edits or changes outside this
rule's files, and the reproduction gates catch large dependency drift. Not verified: Colab runtime behaviour and GPU determinism; the
builder's byte-identical rebuild claim for the six meta parquets (only the npz manifest is bound, and the scorer no longer reads the
meta).
