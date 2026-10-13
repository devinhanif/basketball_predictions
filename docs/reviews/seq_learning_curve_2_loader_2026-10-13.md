# SEQ_LEARNING_CURVE_2 loader review (adversary, 2026-10-13)

Verdict: APPROVED
Rule: SEQ_LEARNING_CURVE_2 86f31649
Reviewed-commit: 750be0a4823557386e51d25281131413cce1667b
Exports-manifest: e12d04c90ba5a6fe2f800988e121b36339c56ea4531b936b21684240644d9da0

**Summary.** The arrays are clean. The planted-future, truncation and post-tip report attacks run on the real
export all pass, and each has a positive control that fails as it should. Row counts match D2/D4. Positions are
complete (0 unknown) and the 13 sidecar ids carry the sidecar position. There is no NaN or inf, no season past
2023 and nothing from 2025. Both data_versions recompute to the pinned values. The job's U, M and UP arms read
the arrays as the rule says.

**Re-review (same day).** The first pass reviewed 950dcdf and was BLOCKED on B1: the frozen `RULE_SHA` was not
yet committed in the job. It is now committed as 750be0a. Every data finding carries over, because none of them
depends on that line. The checks on 750be0a, all with exit codes shown in the session:
- `git diff --stat 950dcdf 750be0a`: 2 files, 2 insertions, 2 deletions, which is exactly the `RULE_SHA` line
  in `seq_learning_curve_2_train.py` and in `seq_learning_curve_2.ipynb`
  (`"DRAFT_NOT_FROZEN"` -> `"86f3164917fe...232f8a"`).
- `git status --porcelain -- <REVIEWED_PATHS>`: empty.
- `git diff --quiet 750be0a HEAD -- <REVIEWED_PATHS>`: exit 0.
- `git merge-base --is-ancestor cf2ba61 750be0a`: exit 0.
- npz sha256 unchanged (S1 e2abd30c, S2 a4ac6665, S3 25931b1b), so the manifest is still `e12d04c9...`.

Uncommitted changes under `nba/daily/` (the steward's AVG shadow work) are outside `REVIEWED_PATHS`
(rule.py lines 45-52) and do not affect this gate. The substantive verdict is **SURVIVES**.

Model class: neural sequence model (transformer over the last 20 player-games plus a 41-wide context vector).
This is a loader review: nothing was fitted, and 2025 was never loaded.

## Header forms (checked against `research/eval/seq_learning_curve_2_rule.py`)
- `Rule` must be `SEQ_LEARNING_CURVE_2 <sha[:8]>` as a whole line. `require_frozen()` returned sha
  `86f3164917fe...` and freeze commit `cf2ba61d544b...` (exit 0).
- `Exports-manifest` is **not** the sha256 of `export_info.json`, which is
  `1ce9c8ac4dcb8a8472b50867196a85d2e0f877968bd035da5c59bc3d387b223e`. The value is
  `manifest_sha({npz name: npz sha256})` (`exports_manifest_of`, rule.py lines 108-121), recomputed from the
  files on disk as `e12d04c90ba5...`. That equals `export_info.json["manifest_sha256"]`. The brief's form was
  wrong and the code's form is used above.

## Findings (severity-ranked)

**B1 (was blocker, RESOLVED in 750be0a): at 950dcdf the reviewed paths were dirty, so that commit could not be the run commit.**
`git status --porcelain -- <REVIEWED_PATHS>` shows ` M research/colab/jobs/seq_learning_curve_2/seq_learning_curve_2.ipynb`
and ` M .../seq_learning_curve_2_train.py`. Each diff is exactly one line (train.py line 306, and the matching
ipynb line):
```
-RULE_SHA = "DRAFT_NOT_FROZEN"
+RULE_SHA = "86f3164917fe66b191b40f39c1ba98617074d3fd1ff209ef06c589c920232f8a"
```
The new value equals the frozen sha, so the content is correct. At HEAD, though, the committed notebook refuses
to fit (`check_gates`: rule sha mismatch). No review of 950dcdf can open the gate. **Fix:** the main session
commits these two paths only, with no other change under REVIEWED_PATHS. The adversary then adds a short
re-review whose Reviewed-commit is that commit and whose Exports-manifest is unchanged (`e12d04c9...`), provided
the npz files are unchanged. A re-review would check only that `git diff 950dcdf <new> -- <REVIEWED_PATHS>` is
this one-line change in two files. Everything below carries over.

**m1 (minor): nba.duckdb data_version is recorded, not enforced.** `seq_learning_curve_2_export.py` line 301
records `nba_duckdb_data_version`, but only the history version is checked (`require_data_version`, line 296;
scorer `check_run`, eval.py line 523). The effective pin for S3 is the npz sha manifest, which `require_review`
and `check_run` do enforce, so this does not block. **Fix (optional, future rules):** add
`NBA_DUCKDB_DATA_VERSION = "d0960b5a561c"` and refuse on mismatch, as for history. Note that nba.duckdb refits
daily, so a re-export would change it.

**m2 (minor, disclosed in rule): the sidecar positions are single positions.** Every row in every segment is
single-hot (S1: G 44.5%, F 39.6%, C 15.9%; S2 and S3 likewise; 0 multi-hot rows). The 13 hand fills therefore
cannot be told apart by their encoding, which is where the survivorship NULL pattern would show. Their 550 rows
do have lower `base_min` (0.403 vs 0.623), which is as-of information: past minutes, which the model sees
anyway.

**m3 (minor): `export_summary.json` says the planted-future test was "not run separately".** It is now run
(below). No code change is needed.

## The six checks, applied to a loader (nothing is fitted, so 1, 4 and 5 are n/a)

| # | check | command (scratchpad) | n | result | pass |
|---|---|---|---|---|---|
| 2a | truncation: rebuild each segment from the same DB with all games dated >= cut removed; rows before the cut must be byte-identical across all 15 row arrays | `trunc_attack.py SEG CUT` for S3 2024-01-15, S3 2023-03-01, S2 2021-01-15, S2 2020-03-05, S1 2015-01-15, S1 2018-02-01 | 40,334 / 19,641 / 27,820 / 19,545 / 39,632 / 126,429 rows | 0 rows differ in any array; merge both-sides only (0 left_only, 0 right_only); `stat_scale` globally equal; exit 0 each | PASS |
| 2b | planted future: add fake games the day after the cut (same players, stats x2 + 30, 48 min) | `trunc_attack.py SEG CUT plant` (S1 2015-01-15, S2 2021-01-15, S3 2024-01-15) | 206 / 130 / 136 planted rows | earlier rows 0 differing; the planted rows appear only as new rows; exit 0 | PASS |
| 2c | positive control: +25 pts on one early date | `trunc_attack.py S1 2015-01-15 poison`, `... S3 2024-01-15 poison` | 39,632 / 40,334 | 28,382 and 28,841 `seq` rows differ (the comparator does detect a change) | PASS (control fails as it should) |
| 2d | planted post-tip report: every player of 400 games marked `out`, stamped tip+1 min and tip-59 min (inside the 60-min lead) | `report_attack.py S3 {1,-59}`, `report_attack.py S2 {1,-59} 2020` | 10,579 (S3 2023) / 11,007 (S2 2020) planted rows | all arrays identical to the real export | PASS |
| 2e | report positive control: stamped tip-61 min | `report_attack.py S3 -61`, `... S2 -61 2020` | same | `ctx` changes on 8,085 and 7,469 rows (only `ctx`) | PASS |
| 3 | missingness: no-report share and no-position count by y_pts = 0 / > 0, starter / bench, above / below median, per season | inline script (S2, S3 meta) | 24,097-28,197 rows/season | no-report share <= 0.32% in every group in every report season (2021 = 100%, report-dropped by design); no-position = 0 rows in every group | PASS |
| 6 | integer support | n/a (loader; scoring is the scorer's integer grid, unchanged) | - | - | n/a |

Lag-one-day: n/a for a loader with no fit. The 60-minute-lead boundary test (2d/2e) is the tip-proximity check.

## Data checks requested

| item | evidence | pass |
|---|---|---|
| per-season rows vs D2/D4 | S1 2013-2018 = 27,324 / 27,478 / 27,817 / 27,709 / 27,683 / 27,739 = DB played rows per season (query on history, read-only) = D2/D4 text | PASS |
| S1 dropped 980 non-playing rows = DNP mask | DB `coalesce(minutes,0) <= 0`, 2013-18: 202+158+163+167+161+129 = 980; 0 null minutes; `s1_rules` keeps `minutes > 0` | PASS |
| S2 targets carry no DNP rows either | merge of S2 meta to DB: 0 of 77,195 with minutes <= 0, 0 unmatched | PASS (consistent) |
| positions | `no_position_rows` = 0 in every season of every segment (export_info, recomputed on arrays); the 13 sidecar ids' S1 rows (sum 550) carry exactly the sidecar position (e.g. 201946 Forward -> [0,1,0] on 188 rows; 202970 Center -> [0,0,1]; 203810 Guard -> [1,0,0]) | PASS |
| NaN / inf | 0 in every float array (`seq`, `ctx`, `y`, `base`, `norm_c`, `stat_scale`) of S1/S2/S3 | PASS |
| season caps | max season S1 2018, S2 2021, S3 2023; no row >= 2024; last date 2024-06-17; history DB max season 2021; `load_raw` refuses >= frozen season | PASS |
| real tips | tip_coverage S2 3,637/3,637, S3 2,638/2,638; S1 none (`raw.tips = None`, has_report = 0 on all 165,750 rows, all report channels 0) | PASS |
| era encoding (T483 caveat) | one era vector per season except 2019 (two: bubble/no-fans games); 2023 = 2022 = [0,0,0,1,1,1,1,1,0,0] (run-time gate ok); S1 flag = 1 on exactly 2013-18; report-dropped = 1 on exactly 2021; rule_timeouts_2017 = 1 on exactly 55,422 = 2017+2018 rows; 2013-15 rule flags all 0 (disclosed) | PASS |
| data_versions | `db_data_version` recomputed: history `c90139d87ded`, nba.duckdb `d0960b5a561c` (exit 0) | PASS (m1) |
| shas on disk | `shasum -a 256`: S1 e2abd30c..., S2 a4ac6665..., S3 25931b1b..., metas 56cee21a / 06a2fddd / 0a7e01d8 = export_info; reports copy and data copy of export_info byte-identical (`cmp` exit 0) | PASS |

## Train script vs rule (`seq_learning_curve_2_train.py` at 750be0a)
- Windows: `window_data` takes S1/S2 rows with labels in `range(2022-N+1, 2023)` plus all of S3. Recomputed on
  the arrays: fit rows N = 4/6/8/9/10 are 97,774 / 152,530 / 207,559 / 234,755 / 260,649; val is 3,286 (2022
  season on or after 2023-04-01) and test 28,197 for every N; the test key and input shas are asserted equal
  across units.
- U: `fit(..., warm epochs = 3)` over `pool = date < month start & pool_eligible`, which asserts the pool is
  strictly before the block. M: `steps_of(pool_count(meta, 4, start))`. `meta` is drawn from all three segments,
  so this is the same count as U_4's pool. UP_4: window-4 nets with U_8's step count. M_4 is not fitted.
- `fit_steps` uses consecutive permutation sweeps, the same AdamW, clip and seed (`1000 + sd`) as U.
  `run_epoch` counts the last partial batch, which matches `steps_of` (ceil).
- Steps per refit recomputed from the arrays: U4 297 -> 378, U8 618 -> 699, U10 774 -> 855; U8/U4 is 2.08 in
  2023-10 and 1.85 in 2024-06, against the rule's "about 2x". Cold nets are shared within a unit by deepcopy, as
  the rule says.
- `load_capped` refuses season > 2023; `concat_all` requires non-row keys to be equal across segments
  (`ctx_features`, `step_features` and `stat_scale` are identical in S1/S2/S3).

## Verification (unpiped)
`ruff check` (12 files) exit 0. `ruff format --check` exit 0. `mypy` (exporter, rule, scorer) exit 0.
`pytest tests/research/seq_learning_curve_2 --no-cov`: 65 passed, exit 0. A first run with coverage on also
passed all 65 but exited 3 from a pytest-cov teardown DataError (stale coverage files mixing statement and
branch data; this is the environment, not these tests).

## Playbook check (neural sequence model)
1. Information or capacity: capacity/data. The loader adds no new pre-tip information, and attacks 2a-2e show
   that no future value or post-tip report enters.
2. Enough data for this class: 97.8k-260.6k fit rows. That is the question the rule asks; not judged here.
3. Test validity: the same 28,197 test rows and inputs across windows (asserted). Era vectors on the test season
   equal the previous season's. S1 has no reports and no DNP targets, disclosed and flagged by
   `era_s1_derived_noreport`. Under T483, E measures "more seasons including a distinct era". The loader neither
   fixes nor worsens this.
4. Lineage and inherited guards: real tips with no proxy (2d/2e at the 60-minute lead); past-only normalizer
   (2a/2c: truncation identical, and a past change propagates); cumulative rule flags; position gate D4 (0
   unknown).
5. Scored against: AVG vs FULL pts CRPS on the integer grid (scorer, not exercised here).

## Most likely failure mode if the data side is wrong
A difference between the exports reviewed here and the exports staged on Colab. This is mitigated, because
`stage` and `check_run` pin all three npz shas through `e12d04c9...`.

## Not verified
- The job's model code was not run; `fit_steps` bit-identity to `fit` is asserted by the job's CPU test, not
  re-derived here.
- That the tz of `tip_et` and `as_of` agree beyond the boundary tests (both naive; the -59/-61 boundary behaves
  as expected).
- The scorer's bootstrap and decision words: out of scope for a loader review.
- The working-tree ipynb was not rebuilt from `build_notebook.py` and compared byte for byte; `test_job.py`
  covers the regeneration.

## Recommended changes (not applied)
1. B1: done (750be0a).
2. m1: pin the nba.duckdb data_version in future exporters and scorers.
3. Fix the brief's definition of Exports-manifest: it is the npz manifest sha, not the sha of export_info.json.

## Drafted rows (not appended)
None. A ledger row comes with the result, not with the loader review.
