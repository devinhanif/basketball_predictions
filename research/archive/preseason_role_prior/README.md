# PRESEASON_ROLE_PRIOR (archived 2026-10-20)

- Rule: PRESEASON_ROLE_PRIOR, `docs/prereg/PRESEASON_ROLE_PRIOR.md`. Frozen sha256 `4774b943` (prereg commit `36c30bb`), harness pin `dcaa62ee` (commit `36f8dc8`).
- Ledger: T500, verdict FAIL_ONE_SEASON (2024 gains wounded by the post-result attack,
  `docs/reviews/preseason_role_prior_postresult_2026-10-20.md`); closed, no retrospective follow-up. Results: `reports/prereg_preseason_role_prior/results.json`.
- Not archived and not research: `nba/ingest/preseason.py` and the `preseason_*` tables stay.

| old path | new path |
|---|---|
| `research/eval/preseason_role_prior.py` | `research/archive/preseason_role_prior/preseason_role_prior.py` |
| `tests/research/preseason_role_prior/test_preseason_role_prior.py` | `tests/research/archive/preseason_role_prior/test_preseason_role_prior.py` |

The harness's `HARNESS_FILES` pin list was repointed to the new path and `ROOT` to `parents[3]` (the file is one directory deeper); no
logic changed. The pin `dcaa62ee` was taken over the old path, so the pin check only passes at commit `36f8dc8`. The frozen prereg text
still names `research/eval/preseason_role_prior.py`; this README is the forwarding note.
Run: `python -m research.archive.preseason_role_prior.preseason_role_prior` (was `research.eval.preseason_role_prior`).
