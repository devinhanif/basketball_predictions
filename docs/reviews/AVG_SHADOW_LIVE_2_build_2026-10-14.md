# AVG_SHADOW_LIVE_2 build review (adversary), 2026-10-14

Subject: commit 678ba21 ("AVG_SHADOW_LIVE_2: G1 read on avg_pmf199; P3 PASS; rebuild notebook pinned to 9afbfeb6; oracle 89889f6d unchanged").
Rule: `docs/prereg/AVG_SHADOW_LIVE_2.md`, FROZEN, commit 6e86f1a (2026-10-14 11:11:53 CDT). Freeze evidence re-derived:
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/AVG_SHADOW_LIVE_2.md | shasum -a 256` gives `9afbfeb6e164...fa87`, and the file has
no commit after 6e86f1a (`git log -- docs/prereg/AVG_SHADOW_LIVE_2.md`): the rule text is unchanged between freeze and build. Rule commit
(11:11) precedes the P3 artifact (commit 12:05).

Model class: neural sequence model (W_ALL E2 transformer) blended 0.5/0.5 with the gradient-boosted FULL residual (playbook "Neural sequence
models", "Gradient-boosted trees"). This is a build and conformance review, not the review of a claimed win: no live row exists, and every number below
is a season-2024 rehearsal.

Not read or opened by this review: any season-2025 forecast, any replay copy under `data/rehearsal/oracle_avg_*` (listed with `ls` only; the two
`.log` files and the `.status` file read are timing and hash text), the rebuild OOF file (it does not exist yet).

## Verdict on the three steward additions

| # | Addition | Conforms? | Finding |
|---|---|---|---|
| 1 | `ORDER BY f.game_id, f.player_id` in `load_live` (`nba/eval/avg_shadow_live.py` line 149) | **Conforms** (fills an unstated detail; changes no rule-defined quantity) | see A |
| 2 | P3 pass also requires (f) <= -0.003 (`p3_equivalence.py`, `pass_f`) | **Conforms** (this is the frozen text's reading, not a stricter rule) | see B |
| 3 | `job.yaml` `touches_holdout: true` -> `NBA_SCORE_HOLDOUT=True` | **Conforms for the code** (the flag is inert; it was already `true` before this commit); **the rule's 2025 hygiene is not met on disk** | see C (blocker) |
| 4 | G1 reader on `avg_pmf199` for ITT rows (rule line 79) | **Conforms** | see D |

### A. The fixed row order (item 1)

What the order touches (`nba/eval/avg_shadow_live.py`, `nba/eval/full_shadow_live.py`):
* `randomized_pit(q, y, 0)` (`nba/eval/lower_tail_eval.py` line 192) draws `rng.random(len(y))` by row position: G1 depends on row order.
* `shuffle_grids_within_day` (`full_shadow_live.py` line 147) permutes `ix = np.flatnonzero(day == d)` in row order: check (e) depends on row order.
* `cluster_boot` (`full_shadow_live.py` line 83) aggregates through `np.unique(cluster, return_inverse=True)` and `bincount`: order-invariant
  (to float summation). Means, BH, later-half (median date), G2-G5 are order-invariant.

So the ORDER BY changes no definition. It picks one realisation of "randomised PIT, seed 0" (rule line 79, unstated details line 162) and of
"day-shuffle, seed 0" (line 75), where before the realisation was whatever DuckDB's physical join order produced. With 0 live rows in existence
(rule line 16; nothing could have been seen), choosing a deterministic order is exactly what "seed 0" intends: a fixed seed over an unfixed row
order is not reproducible, and the integrity section demands reproduction.

How much it matters (re-derived on 2024 pts, the P3 arrays, n = 25,767, 1,230 games; 200 random row orders, seed-0 PIT each; script
`scratchpad/attack_g1.py`, exit 0): q10 coverage ranges 0.1113-0.1157 (sd 0.00087), q20 0.2106-0.2157 (sd 0.00089). At the live final look
(about 10,000 pts rows) the order sd scales to about 0.0014, against a q20 margin of 0.0049 on 2024. Without the fix the G1 verdict could be moved by
re-running the reader after a table rewrite; that is a forking path the fix closes. The stored-order realisation P3 used (q20 0.2151) sits near the top
of the range, i.e. it is not a favourable draw.

Gaps (minor):
* A1 (minor). No test pins row-order invariance. Fix: in `tests/eval/test_avg_shadow_live.py`, a fixture test that inserts the same rows in two
  physical orders and asserts `score_stat(...)["guards"]["G1_calibration"]` and `shuffled_minus_full` are identical.
* A2 (minor). The choice is recorded only in a code comment. Fix: one DECISIONS line (drafted below) naming it an unstated detail fixed before any live
  row; the rule text above the results line must not be edited.
* A3 (minor, outside this rule). `nba/eval/full_shadow_live.py` `_LIVE_SQL` (ends line 188) has no final ORDER BY, and its day-shuffle (line 389) is
  order-dependent. Recommend the same fix there before FULL_SHADOW_LIVE's first look, with its own DECISIONS line; it changes no FULL_SHADOW_LIVE definition either.

### B. (f) as a P3 pass condition (item 2)

The brief's premise quotes the superseded rule. AVG_SHADOW_LIVE (6e57fc11) line 52 said P3 "rehearses G1, (f) and the 19-atom guards". The frozen
AVG_SHADOW_LIVE_2 line 98 says instead: P3 "rehearses on 2024 (a) G1 on the 199-point pmf ... which must satisfy the rule's own G1 limits on pts
(`pass_g1_rehearsal`), (b) (f) on the 19-atom form (floor -0.003, point). The 19-atom G1 is printed, descriptive, not a pass condition ... P3 passes iff
its own numbers pass the rule". The text names a floor for (f), exempts only the 19-atom G1 from passing, and says P3 passes iff its numbers pass the rule.
Reading (f) as a pass condition is the plain reading of the frozen text, not an addition; reading it as print-only would be the looser, unsupported
reading. "The 19-atom guards" (G2-G5, (c), (g)) are no longer in P3 under _2 (they were rehearsed in `reports/prereg_avg_shadow_live_2/rehearsal.json`),
so `p3_equivalence.py` not gating on them conforms.

Even under the strict "stricter than rule" reading, nothing hinges on it: the value was in the frozen rule before the build (line 50: -0.00847), so the
condition carried no new information and could not have selected an outcome. Re-derived: (f) pts = **-0.008471**, game-clustered 90% CI
[-0.01068, -0.00604], n 25,767, 1,230 games (2,000 draws, seed 0, `cluster_boot`). Pass.

Gap B1 (minor): no test that `pass` is False when `pass_f` is False. Fix: a unit test in `tests/daily/test_avg_shadow_rebuild.py` on the pass
arithmetic (factor `rec["pass"]` into a function of the three booleans).

### C. The rebuild's season-2025 uses (item 3): code conforms; disk does not

Code read line by line (`avg_shadow_rebuild_train.py`, which equals the notebook cells; `fit_gate.py`; `_world.py`; `p1b_check.py`; `research/colab/push.py`, `jobs.py`):
* (1) Training on 2025 labels: `run_walk_forward` warm-refits on `pool = date < block start` (asserted strictly before), then the install fit on
  every row before `INSTALL_BEFORE`. `FROZEN_SEASON = 2026` (line 306) is redefined in the tail and asserted at line 402. Conforms.
* (2) 2025 OOF forecasts are written to `oof_E2.npz` and never scored by the notebook: `metrics.json` holds `info` only (counts, `val_pinball_cold`
  on the 2022 validation rows, block details, runtime). `_world.filtered_oof` and `p1b_check.py` (lines 53, 71) drop `season > 2024` before use.
  `fit_gate.py` fits the SoftmaxGate and prints `n_fit` only. Conforms. **Not verifiable yet:** the "aligned 2023-25 npz" assembler does not exist
  (DAILY_PIPELINE: "Not run: the gate fit needs the 2025 A0 members of T478's held-out path"); it is the one place a 2025 forecast could be scored or
  printed, and it must come to the adversary before it runs.
* `touches_holdout: true` was already in the job before this commit (`git show 678ba21~1:research/colab/jobs/avg_shadow_rebuild/job.yaml`); this commit
  changed only `preregistration_id` and the description. `NBA_SCORE_HOLDOUT` is read by no file of this job (`grep -c SCORE_HOLDOUT` = 0 for every
  file in the job directory and the notebook): the flag is inert here, and `jobs.py` requires `touches_holdout: true` to carry a prereg id, which is
  how a training-class 2025 use is declared. Conforms. Minor C2: `push.run_md` will print "PRE-REGISTERED holdout scoring ON (AVG_SHADOW_LIVE_2)" in
  RUN.md, which misdescribes a rule that forbids 2025 scoring. Fix: in `research/colab/push.py` `run_md`, print "holdout rows used under
  <prereg>; scoring per the rule" (or key off the `holdout_scored: false` tag).
* The notebook's log gate (`check_rule`, `LOG_ROW_KEY = "Colab chain rebuild for AVG_SHADOW_LIVE"`) matches row 72. Major C3: the rule (line 125)
  says "the archivist adds a cross-reference line naming AVG_SHADOW_LIVE_2"; `grep -c AVG_SHADOW_LIVE_2 docs/HOLDOUT_ACCESS_LOG.md` = 0. Fix: the
  archivist appends that cross-reference row before the notebook is run (and before the staged log is copied by `push`).
* (3) **BLOCKER C1: the P4 replay copies are not deleted.** Rule line 4 use (3): "the replay copy is deleted unread"; line 99: "the replay copy is
  deleted"; HOLDOUT_ACCESS_LOG row 72: "replay copy deleted". `ls -la data/rehearsal/ | grep oracle_avg` shows nine replays still on disk
  (`oracle_avg_1` ... `_7`, `_a`, `_b`: each a ~0.7 GB replay DuckDB plus a ~0.65 GB `_full.duckdb` source copy, plus state dirs; about 12 GB), all
  holding season-2025 production rows. Two are new today: `oracle_avg_6` (11:32) and `oracle_avg_7` (11:52), i.e. this build re-ran P4 twice although
  rule line 30 says P4 is inherited. DAILY_PIPELINE says the copies "are deleted by the maintainer (command in the hand-back)"; that has not happened
  for the 2026-10-13 copies either. Nothing shows the copies were read beyond the oracle hash (logs show hash and timing only), and no AVG row is in
  them (no FULL shadow runs in the replay), so this is a hygiene breach, not a leak; but row 72 currently states something false, and P4 cannot be
  recorded `pass` in `forward_avg_meta` while the rule's own condition on it is unmet. Fix (maintainer, after confirming nothing else uses them):
  `rm -rf data/rehearsal/oracle_avg_1* data/rehearsal/oracle_avg_2* data/rehearsal/oracle_avg_3* data/rehearsal/oracle_avg_4* data/rehearsal/oracle_avg_5* data/rehearsal/oracle_avg_6* data/rehearsal/oracle_avg_7* data/rehearsal/oracle_avg_a* data/rehearsal/oracle_avg_b* data/rehearsal/oracle_avg_run.status`
  then `ls data/rehearsal | grep -c oracle_avg` must print 0, and a holdout-log disclosure row (drafted below) records the two extra replays and the
  deletion time.
* 60-minute ceiling (minor C4): documented only (`EST` string, job.yaml, INTRO); nothing in the notebook halts at 60 minutes, and the walk is
  copied verbatim so a check cannot be spliced inside it without changing T478's code. Expected runtime about 12 min (T478 measured 599 s), so the
  risk is low. Fix: in `avg_shadow_rebuild_tail.py` `main`, start a `threading.Timer(3600, ...)` that writes `HALTED_AT_CEILING` to the run folder and
  calls `os._exit(3)`; or state in RUN.md that the maintainer stops the runtime at 60 minutes (the rule says "halt at the ceiling" without naming who).

### D. G1 reader on avg_pmf199 (rule line 79)

* `load_live` reads `avg_pmf199`, raises if `avg_q19` and a 199-value `avg_pmf199` are not stored together (fail-closed; an integrity failure).
* `g1_pmf199`: on-time AVG row -> stored `avg_pmf199`; otherwise (missing, late-only or failed: `has_avg` is "on-time row exists") ->
  `to_integer_support(ext19(qf))`, identical to `grids.build_arms` line 95 (`fulli`), the shadow's own FULL construction. Rule line 79 and the unstated
  detail at line 162 say exactly this. Population: `pop["verdict"]` = played, `n_prior >= 3`, finite y, not stale (rule 2). `share_without_avg_row`
  printed; the 19-atom reading is in `atom19_descriptive` and does not enter `ok`. Conforms.
* Limits re-derived from code: `MAX_BIAS, COV_LO, COV_HI = 0.5, 0.75, 0.85`; `GUARD_TOL = 0.02` at `REQUIRED_GUARD_TAUS = (0.10, 0.20, 0.80, 0.90,
  0.95)`, 0.05 reported: equal to rule line 79.

## Checks run

| Check | Command | n | Number (CI) | Pass |
|---|---|---|---|---|
| Rule integrity | `awk ... AVG_SHADOW_LIVE_2.md \| shasum -a 256`; `git log -- docs/prereg/AVG_SHADOW_LIVE_2.md` | n/a | 9afbfeb6; no commit after 6e86f1a | pass |
| Re-derive P3 | `.venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/p3_equivalence.py --sel-run data/colab/runs/seq_full_mix_rt_sel/20261012_144207 --out <scratch>/p3_rerun.json` (exit 0) | 25,767 rows, 1,230 games per stat | JSON identical to committed `p3.json` except `seconds` | pass |
| G1 199-point, pts | scratch `attack_g1.py` (exit 0), game-clustered, 2,000 draws, seed 0, seed-0 PIT held fixed | 25,767 / 1,230 games | q10 0.1129 [0.1098, 0.1160]; q20 0.2151 [0.2111, 0.2192] (90%); limits 0.12 / 0.22 | pass (q20 upper bound 0.0008 from the limit) |
| (f) pts | same | 25,767 / 1,230 | -0.00847 [-0.01068, -0.00604] (90%); floor -0.003 | pass |
| Order sensitivity of G1 | same, 200 random row orders | 25,767 | q10 sd 0.00087 (range 0.1113-0.1157); q20 sd 0.00089 (0.2106-0.2157) | fix justified |
| Shuffled labels / planted future / missingness / knockout / integer support | not applicable: no claimed gain, no live row; the reader's fixture tests cover planted miscalibration on avg_pmf199 and a 19-atom-only shift | n/a | n/a | n/a |
| Replay copies deleted | `ls -la data/rehearsal/ \| grep oracle_avg` | 9 replays | present, 2 created today | **FAIL** |
| Log cross-reference for _2 | `grep -c AVG_SHADOW_LIVE_2 docs/HOLDOUT_ACCESS_LOG.md` | n/a | 0 | **FAIL (before rebuild)** |
| Lint and types | `.venv/bin/ruff check <5 changed files>`; `.venv/bin/ruff format --check <same>`; `.venv/bin/mypy nba/eval/avg_shadow_live.py` | n/a | exits 0 / 0 / 0 | pass |
| Tests | `.venv/bin/python -m pytest -q tests/eval/test_avg_shadow_live.py tests/daily/test_avg_shadow_rebuild.py tests/daily/test_avg_shadow.py` | 59 tests | 59 passed, 609 s, background exit 0 (the summary line was filtered through grep; rerun unpiped if the exit code is load-bearing) | pass |

Multiplicity: none here (proofs are gates, not tests; no family). Power: G1 at the live final look (about 10,000 pts rows) has a game-clustered se of
about 0.0039 at q20 against 0.0049 of 2024 slack (rule line 50); the order fix removes a further sd of about 0.0014 of arbitrary variation.

## Playbook check

1. Information or capacity: a capacity/averaging change (sequence model blended with FULL); the rule says so and the verdict venue is live-only.
2. Enough data for this class: W_ALL 2019-20 on, the class's known need; unchanged by this build.
3. Test valid: G1 now reads the object it was written for (the 199-point pmf); P3 rehearses it on 2024; CRPS contrasts stay on the 19 atoms for every
   arm (same support). The row-order fix makes the seeded G1 and day-shuffle reproducible. Valid.
4. Lineage and inherited guards: T478 walk copied verbatim (AST), T442 gate, era/2025 asserts, strictly-before pool asserts: carried.
5. Scored against: FULL (primary) and A0 / AVG_A0 / placebo / shuffle, on the live 2026-27 log only.
Class checks (neural sequence): training window long enough (W_ALL) yes; era channels zero on test rows (P2, inherited) yes; lineage guards yes.

## Single most likely failure mode

Not a leak in this commit: the most likely way the build's hygiene claim is wrong is the one found, a season-2025 artifact that the rule and the log
say is deleted but is not (C1), followed by the unwritten 2023-25 aligned-npz assembler (C, not verified), which is the only code that will hold 2025
forecasts next to labels on the Mac.

## What was not verified

* The aligned-npz assembler for `fit_gate.py` (does not exist yet).
* The rebuild run itself, its runtime against the 60-minute ceiling, and P1a / P1b (need the Colab run).
* That the two replays run today (`oracle_avg_6`, `_7`) were not read beyond the oracle hash: logs show hash and timing only.
* `ops/nba_daily.sh`, `tests/ops/*` are modified in the working tree but not in this commit; not reviewed.

## Recommended changes (not applied by the adversary)

1. Blocker: delete the replay copies (command in C1); add the disclosure row below. Then P4 may be recorded `pass`.
2. Major, before the notebook runs: archivist cross-reference row for AVG_SHADOW_LIVE_2 in HOLDOUT_ACCESS_LOG (rule line 125).
3. Major, before it runs: the aligned-npz assembler comes to the adversary.
4. Minor: A1, A2, A3, B1, C2, C4 as written above.

## Drafted rows (not appended)

DECISIONS (`docs/DECISIONS.md`):
`2026-10-14 | AVG_SHADOW_LIVE_2 reader: rows ordered by (game_id, player_id) before the seed-0 randomised PIT (G1) and the seed-0 day-shuffle (e) | unstated detail fixed before any live row (0 rows existed); the rule text is unchanged; without it G1 depended on DuckDB's physical order (2024 pts: q20 sd 0.0009 across row orders at n 25,767) | reverse only by a new rule id`

HOLDOUT_ACCESS_LOG (`docs/HOLDOUT_ACCESS_LOG.md`), two rows:
`2026-10-14 | cross-reference: AVG_SHADOW_LIVE_2 (sha 9afbfeb6) inherits rows 72 and 73; no new season-2025 use | training and systems use only | all | NOT a confirmatory touch | archivist line promised by the rule (line 125)`
`2026-10-14 | steward AVG_SHADOW_LIVE_2 build: P4 replay re-run twice (oracle_avg_6 11:32, oracle_avg_7 11:52 CDT), hash 89889f6d both | none scored | 20 dates of season 2025 in replay copies | DISCLOSURE: the rule inherits P4 and did not ask for a re-run; replay copies of 2026-10-13 and 2026-10-14 deleted at <time> (rule use 3, row 72) | Not a touch.`

Verdict: BLOCKED
