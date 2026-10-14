# SEQ_LEARNING_CURVE_2 loader re-review: the team-join fix (adversary, 2026-10-14)

Verdict: APPROVED
Rule: SEQ_LEARNING_CURVE_2 86f31649
Reviewed-commit: 64348864fbba056ddb314dc991a7333c9aab59da
Exports-manifest: e12d04c90ba5a6fe2f800988e121b36339c56ea4531b936b21684240644d9da0

**Summary.** On the gate's `REVIEWED_PATHS`, the only commit between the last approved commit (750be0a) and HEAD
(6434886) is 6a129be. Its change to the scorer is the team-join fix and nothing else. The fix touches only the
descriptive team drop-one, which is a point range with no CI and cannot change a verdict (rule line 47). It
changes no population, floor, CI, arm, bootstrap, decision word or exported array. The rule text recomputes to
86f31649. The npz shas still give manifest e12d04c9. The job, export, rule and git-primitive files are
byte-identical to 750be0a. I ran the new join on the real 2023-24 export and the real A0 world: 27,619 rows,
30 teams, 0 missing, 0 duplicates, never more than 2 teams per game, and it matches an independent pandas
`validate="one_to_one"` merge. The substantive verdict is **SURVIVES**.

The data findings of `docs/reviews/seq_learning_curve_2_loader_2026-10-13.md` carry over by reference, because
neither the exports nor the export code changed. Those findings cover planted future, truncation, post-tip
reports, row counts, positions, NaN/inf, seasons, data_versions and the job's reading of the arrays.

Model class: neural sequence model (the same one the 10-13 review covered). This is a code re-review. Nothing was
fitted or scored, no arm contrast was computed, and season 2025 was never loaded: `stored_world_base` refuses
seasons above 2024, and the restricted world holds only 2023.

## Diff on the gate's paths (`git diff 750be0a..HEAD -- <REVIEWED_PATHS>`)
`REVIEWED_PATHS` (rule.py lines 45-52) lists six paths. The brief named five; the sixth is
`tests/research/seq_learning_curve_2`, and it is covered here too.

| Path | Change | Class | Judgement |
|---|---|---|---|
| `research/eval/seq_learning_curve_2_eval.py` 286-300 | new `team_by_key`: left join export (game_id, player_id, team) onto the scored A0 base, sorted by base position; refuses duplicate keys on either side and any null team | (a) fix | correct. A str/int dtype mismatch on game_id would produce nulls, which raise rather than pass silently |
| same file 498-499 | `assemble` raises if `len(team)` differs from any arm's pts row count | (a) fix, guard | correct; it runs only inside the drop-one block |
| same file 779-780, 814 | `team` was a positional slice of S3 (28,197 rows); it is now keys from S3 in 2023, joined after `load_world_2023()` and before `score_run` | (a) fix | correct; same `team_idx` source, now aligned by key |
| `tests/research/seq_learning_curve_2/test_eval.py` +15 | permuted export with an extra row; missing-row and duplicate-key negatives | (a) test | passes; it would fail on the positional version |
| `research/colab/jobs/seq_learning_curve_2`, `research/features/seq_learning_curve_2_export.py`, `research/eval/seq_learning_curve_2_rule.py`, `research/eval/pplay_minutes_valid_rule.py` | none | n/a | `git diff --quiet 750be0a HEAD --` exit 0 |

Outside the gate, 6a129be also edits `docs/prereg/SEQ_LEARNING_CURVE_2.md`, but only below the
`=== RESULTS BELOW ===` marker (5 changed lines). See finding m2.

## Findings (severity-ranked)
- **Blockers: none. Majors: none.**
- **m1 (minor; missing negative test).** The guard at eval.py lines 498-499 has no test. Fix: add
  `test_assemble_refuses_team_of_another_length` to `tests/research/seq_learning_curve_2/test_eval.py`, calling
  `V.assemble` on the existing synthetic world with `team` one row short and expecting
  `RuntimeError("same population")`. This does not block: `team_by_key` already guarantees the length, and the
  real-data run below confirms it. Adding the test changes a reviewed path, so it would need another review. Do it
  after scoring, or not at all.
- **m2 (minor; scope).** The "Cap diagnostic bookkeeping" paragraph (prereg line 141) is not part of the team fix.
  It reads the rule's cap text two ways: the job's 5/30 = 0.167 against the scorer's `fit_reached_cap` at
  2/30 = 0.067. Both readings are under the 1/3 gate, so the gate outcome is the same either way. The code that
  gates (eval.py lines 165-174, `P1.fit_reached_cap`) is unchanged since 750be0a, and the paragraph sits below
  the marker, so the rule is unchanged. The epoch counts are training run-time info, not outcomes. No fix needed.
- **m3 (minor; wording precision).** Strictly, attempt 1 did compute the decision. `assemble` sets
  `res["decision"]` at eval.py line 452, before the team block at line 497. It existed only in memory and was
  thrown away by the IndexError. `assemble` does not log, `reports/prereg_seq_learning_curve_2/results/` and
  `reports/prereg_seq_learning_curve_2_results.md` do not exist, and no scorer log file exists. Nothing was
  emitted or read, so the bug note's "no outcome had been read" is accurate. No fix needed.
- **i1 (info; cache).** `data/ops/seq_learning_curve_2_cache/tables__*` were written by attempt 1. They hold
  per-pool cluster sums from which the contrasts can be derived. The table code is unchanged since 750be0a, so
  reusing them is legitimate. Nobody should open them before the scored report exists.
- **i2 (info; attestation).** `data/colab/seq_learning_curve_2/loader_review_attestation.json` still names the
  10-13 review. `run()` calls `RULE.require_review()` directly (eval.py line 772), so the local scorer does not
  read the attestation. Re-run `stage` only if the Colab notebook is used again.

## Checks run (all exit codes shown in the session, unpiped)
| Check | Command | Result | Pass |
|---|---|---|---|
| commits on gate paths | `git log --oneline 750be0a..HEAD -- <5 brief paths>` | only 6a129be | yes |
| diff stat | `git diff --stat 750be0a..HEAD -- <paths>` | eval.py +22/-1 only | yes |
| untouched paths | `git diff --quiet 750be0a HEAD -- jobs, export, rule, pplay rule` | exit 0 | yes |
| working tree | `git status --porcelain -- <6 REVIEWED_PATHS>` | empty | yes |
| freeze ancestry | `git merge-base --is-ancestor cf2ba61 HEAD` | exit 0 | yes |
| rule sha (code) | `R.require_frozen()` | 86f3164917fe..., freeze cf2ba61d | yes |
| rule sha (awk) | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' ... \| shasum -a 256`, HEAD and 750be0a | both 86f3164917fe...232f8a | yes |
| exports | `shasum -a 256 data/colab/seq_learning_curve_2/*.npz`; `R.exports_manifest_of(R.STAGE_DIR)` | S1 e2abd30c, S2 a4ac6665, S3 25931b1b; manifest e12d04c90ba5... | yes |
| S3 seasons | `np.unique(z3["season"])` | 2022: 27,737, 2023: 28,197; nothing past 2023 | yes |
| join on real data | scratch `team_join_check.py` (`team_by_key` on S3 2023 keys and `load_world_2023()["base"]["pts"]`) | 27,619 rows (export 28,197), 30 teams, 0 nulls, pandas one_to_one match True, max 2 teams per game over 1,318 games, 14.9 s | yes |
| scorer tests | `.venv/bin/python -m pytest tests/research/seq_learning_curve_2 -q --no-cov` | 66 passed, exit 0 | yes |
| new test alone | `... -k team_is_joined` | 1 passed, exit 0 | yes |
| lint | `ruff check` / `ruff format --check` on eval.py and the tests | exit 0 / exit 0 | yes |
| types | `mypy research/eval/seq_learning_curve_2_eval.py` | exit 0 | yes |

With the repo's coverage addopts (`--cov-fail-under=70`, pyproject line 45), the same subset run reports
66 passed, exit 1. The cause is project-wide coverage of 4.41% on a subset run; no test failed.

The six checks: 1 (shuffled labels), 4 (knockout) and 5 (replication) do not apply, because nothing is fitted
or scored. 2 (planted future) and 3 (missingness) carry over from 10-13, since the exports are unchanged.
6 (integer support) does not apply here; scoring support is fixed by the rule and the scoring code is unchanged.

## Playbook check (neural sequence model)
1. Information or capacity: unchanged from 10-13; this diff adds neither.
2. Enough data for this class: unchanged; the learning-curve design is the rule's own question.
3. Test validity: the fix restores a descriptive slice that crashed. The decision contrasts (A_m, E, A, the
   game-clustered shared bootstrap) are untouched, so the test stays valid.
4. Lineage and inherited guards: `oof_index` keys each arm to the A0 base, `check_world_rows` and `c_lookup`
   refuse missing rows, and `restrict_world` refuses mismatched season masks. `team_by_key` follows the same
   key-join pattern.
5. Scored against: U_4 (A_m) and M_8 (E) on the same A0 rows, as frozen.

Class checks: no new input and no tip-time exposure. `team_idx` is the player's own team, which is known pre-tip.

## Most likely failure mode if this review is wrong
A difference between the stored A0 base for `pts` and the rows `full[(arm, "pts")]` is computed on. Both come
from `world["base"]["pts"]` in the same process, and the new guard raises on a length mismatch. The guard cannot
catch an order mismatch at equal length. Both orders derive from the same base dict, though, so that would need
a bug outside this diff.

## Not verified
- I did not run the end-to-end scorer, by instruction. The join is verified on real data in isolation, and
  `assemble` is not run on real arms.
- I did not open the attempt-1 seed-pool cache, deliberately.
- I did not re-run the 10-13 data attacks; they are carried over because the exports are byte-identical.

## Recommended changes (not applied)
- m1: add the `assemble` length-guard negative test after scoring (it is a reviewed path).
- Commit this review by path, then run the scorer. `require_review` needs it committed, unedited, and as the
  latest file under `seq_learning_curve_2_loader_*`.

## Drafted rows (not appended)
None. No result exists. The bug note in the prereg results section is the record.
