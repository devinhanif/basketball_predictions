# Adversary review: SEQ_FULL_MIX loader, Colab job and scorer (before the 2025 export) — 2026-10-12

Rule: `docs/prereg/SEQ_FULL_MIX.md`, sha256 `2fed975a45cedd4f68dc91e5146f0f2ff5610cf09968decacd2700b9e6e49cb9`
(re-hashed with the rule's awk command and with `seq_full_mix_rule.frozen_sha()` and `build_notebook.rule_sha()`: all three equal).
Freeze commit bc0fbd7 (14:04:55 CDT), rule text c5e10a9, holdout row e8927c6 (14:05:09, after the freeze), built notebook 74987c4
(byte-identical to `build_notebook.build()` output today; sha baked, no placeholder left). `git diff bc0fbd7 -- docs/prereg/SEQ_FULL_MIX.md` is empty.
HEAD at review time d87d3ef; no commit after 74987c4 touches any reviewed path. Reviewed 13:55-14:20 CDT (clock: `date`).

Scope: `research/features/seq_full_mix_export.py`, `research/colab/jobs/seq_full_mix/*` (plus the reused `seq_props_train.py` head and
`player_sequences.py` builder), `research/eval/seq_full_mix_{eval,rule,descriptive}.py`, `tests/research/seq_full_mix/`.
No season-2025 outcome, forecast, price, report row or tip time was loaded by this review. The 2025 export was not built; `run` was not run.
`rehearse` was deliberately **not** run, because it loads 2025 box-score minutes (finding N2).

## Verdict: **APPROVED WITH FIXES**

The construction, walk-forward, placebo, shuffle, blend, mixture, decision rule, BH family and the T473 identity match the frozen text.
I found no leak in the code beyond the one the rule discloses (proxy19 report tokens). Before `run`, the main session must fix two
**blockers**. Both are cases where the code allows something the rule forbids on the one-touch command:

* **B1. `--resume` does not enforce "same sha and code" and ignores a failed gate.** The rule says "a re-entry needs `--resume` on the same
  sha and code", and "A run-time gate fails: stop, nothing is scored; the fix is a new id". `require_unspent(out_dir, resume=True)` only checks
  that `results.json` is absent. It never reads `touch_started.json`. It accepts a resume after `gate_failed.json` with `touch_consumed: true`,
  and `_body` then **overwrites** `touch_started.json` with the new HEAD, which destroys the evidence of the first start. Demonstrated:
  a marker with a different sha, HEAD and seq_run plus a consumed `gate_failed.json` → `require_unspent(d, True)` returns without error.
  Consequences: a stop at gate B, the data gate or the T473 identity can be "fixed" and resumed under the same id. A resume can also point at
  a different Colab run, which is a second SEQ draw on the same touch, and it silently reuses `stage2/null_cache/` draws computed with the
  first run's SEQ member.
* **B2. `run` honours `--k`.** `main()` passes `a.k` into `run_held_out(..., null_k=a.k)`, so `run ... --k 3` (the same flag the brief and
  the docstring use for `rehearse`) scores the permutation null with K = 3 instead of the frozen K = 50. Nothing refuses it.

## Code fixes for the main session (code to match the rule; no rule text changes)

Blockers:
1. B1, in `research/eval/seq_full_mix_eval.py`:
   (a) `require_unspent`: refuse if `out_dir/gate_failed.json` exists (consumed or not; the rule's remedy for any failed gate is a new id).
   (b) On `--resume`, read `touch_started.json` and require `sha256 == --sha`, `head == git rev-parse HEAD`, and
   `Path(seq_run).resolve() == Path(--seq-run).resolve()`; otherwise refuse.
   (c) Add the sha256 of `oof_E2.npz`, `oof_E2P.npz`, `oof_E2S.npz` and `config.json` to the marker and require equality on resume. The null cache
   reuse depends on this.
   (d) Never overwrite the marker: write it only when absent; on resume append `touch_resumed_<n>.json` with the time.
   Add unit tests for each refusal (a marker with a different sha, HEAD or seq_run; a `gate_failed.json`; a changed oof hash).
2. B2: in `run_held_out` (or `main` for `run`), `if null_k != NULL_K: raise RuntimeError(...)`, or drop the `--k` pass-through for `run`.
   Add a test.

Non-blockers (recommended before `run`; none changes what the rule computes):
- **N1 (preflight; a stop after the marker wastes the touch).** The following are checked only after `touch_started.json` is written:
  (a) `EXPORT25_META` exists, (b) the oof files cover every stored 2023/24 A0 row (`load_seq_run` raises), (c) `T473` and `E.DK_CACHE`
  exist. Check (a) through (c) before the cap is raised: (b) is `load_seq_run(seq_run, p, stored_world()['base'], 2025)` restricted to
  season <= 2024 keys, which does not read 2025 rows. Today's risk is low: the drift check below found the stored 2022-24 export bit-identical
  to a rebuild from the current DB.
- **N2 (holdout hygiene).** `seq_full_mix_descriptive.run_descriptive` queries `games` and `player_game_stats` with
  `WHERE season >= 2022` and no upper bound. In `rehearse` (target 2024; documented as "every other command asserts season <= 2024") this loads
  season-2025 box-score minutes, plus any 2026 rows, into memory. The committed rehearsal (c5e10a9, `rehearsal/descriptive/`) has already done
  this once. I checked that nothing derived from 2025 reaches the written 2024 outputs: the flags are backward-looking and are joined to 2024
  rows only. Fix: `AND season <= {target}` on both queries. Disclose the past load (row drafted below). Re-run `rehearse` only after the fix,
  since the rule lists the rehearsal as a run-time gate.
- **N3 (Colab evidence).** `main()` calls `write_metrics(out_dir, infos, ...)` with only the programs run in the current session. A re-run
  in the same stamp dir rewrites `config.json`/`metrics.json` with `programs: []` and loses each program's `blocks_detail`. The e2e test does
  this and still passes. `config.json` also records `seeds: [0,1,2]` even in smoke mode. Fix: write `info_<prog>.json` (seeds actually used,
  hidden, smoke, epochs, blocks_detail) next to each `oof_<prog>.npz`, and merge rather than overwrite `metrics.json`. Extend `check_seq_run`
  to verify per program: seeds [0,1,2], hidden 64, not smoke, every block `max_pool_date < min_block_date`, and oof seasons
  {2023, 2024, 2025}. In practice each session gets a fresh timestamp dir, so this is unlikely to bite, but the scorer should refuse rather
  than trust a config that may not describe the files beside it.
- **N4 (provenance).** Write the sha256 of `seq_ensemble_cur25.npz` and its meta into `export_info.json`, have the Colab job copy the npz
  hash into `config.json`, and have the scorer compare them before joining `EXPORT25_META`. Without this, a re-export between push and run
  (the push producer re-runs whenever `nba.duckdb` is newer, and production writes it nightly) would join covariates from a different
  build. That would not be a leak, but it would be a silent inconsistency.
- **N5.** The export's `identity_vs_stored_export` ("reported" per the rule) lives only in `data/colab/seq_full_mix/export_info.json`;
  copy it into `results.json`.
- **N6.** Bound the descriptive step's `seasons`/queries in the real run too (the `>= 2022` query reads 2026 schedule rows; harmless).
- **N7 (minor).** `freeze_commit` matches the subject line only; it does not check that the file at that commit hashes to the sha. Today it
  does (`git diff bc0fbd7` empty). `research.colab pull` `rmtree`s an existing destination, so do not re-pull into the `--seq-run` dir after
  `run` has started. No `nba/truth/bootstrap.py` exists yet: CIs come from `paired_score_delta_ci` (via `ensemble_eval.clustered`) and the
  p-value from `context_residual_eval.boot_p_value`. Both are game-clustered with 2000 draws and seed 0, but they use separate RNG streams.
  That is the house convention, flagged per the role.

## Checks run (seasons <= 2024 or synthetic only)

| # | check | command (scratch, outside the repo) | n | result | pass |
|---|---|---|---|---|---|
| T | unit tests | `.venv/bin/python -m pytest tests/research/seq_full_mix -q -p no:cacheprovider --no-cov` | 28 tests | 28 passed, exit 0 (with coverage on: 28 passed, exit 3 from a pytest-cov "can't combine statement/branch data" teardown error, environmental) | pass |
| L | lint/types | `ruff check` / `ruff format --check` / `mypy` on the 4 modules + job + tests | 10 files | exit 0 / 0 / 0 | pass |
| R | rule integrity | awk hash; `frozen_sha()`; `build_notebook.rule_sha()`; `require_ready(sha)`; `git diff bc0fbd7 -- rule` | — | all `2fed975a…`; freeze bc0fbd7 precedes row e8927c6 and notebook 74987c4; diff empty; built notebook byte-identical | pass |
| 2a | planted future, synthetic, season 2025 under the lifted bound (`attack_synth.py`) | post-game stat planted on one target (pts +50, min 1.0) | fixture | target's seq/mask/ctx/base/n_prior/opp_step unchanged, its y changed; every row through that game unchanged; later rows of that player do change (control live) | pass |
| 2b | planted future report | snapshot dated the next day / 18:01 same day | fixture | ignored / ignored | pass |
| 2c | proxy channel | snapshot 17:30 same day (after a matinee tip) | fixture | **admitted** (`has_report` 0 → 1): the disclosed proxy19 leak, reproduced | expected (disclosed) |
| 2d | bound restored | `P.FROZEN_SEASON` after the context | — | 2025 restored | pass |
| 2e | Colab pool strictly earlier | code read + e2e test (`max_pool_date < min_block_date` per block, 2025 months train on earlier 2025 rows) | — | holds; the E2 loop is ENSEMBLE's line for line except TEST_SEASONS and the target-keyed `pid_true` (both disclosed) | pass |
| 2f | label/normalisation leak in the model | read `seq_props_train.py` head: vocab from 2022 fit rows only; LayerNorm (per row), no BatchNorm or fitted global scaling; `forward` never reads `y` | — | none found | pass |
| 2g | data drift / as-of on real data (`attack_drift.py`) | ENSEMBLE `build_current()` rebuilt today (<= 2024) vs the stored export | 84,170 rows | 84,170 found; every array 100% bit-identical | pass |
| 1 | shuffled labels (y permuted within date, blend relearned; `attack_real.py`, `attack_leak.py`) | pts AVG − FULL | 27,619 / 27,701 | 2023 +0.00005 [−0.0041, +0.0042]; 2024 −0.0282 [−0.0318, −0.0248]. Control: the same with the SEQ member permuted within date gives −0.631 / −0.618, so the real SEQ is 0.63 / 0.59 *worse* than a content-free member under noise labels. The 2024 value is dispersion that rewards a mixture under shuffled labels, not row information. The plain shuffled-label test is ill-posed for a pmf mixture; the rule's permutation null (−0.0015 / −0.0006) is the right control | pass (with the caveat stated) |
| 3 | missingness, SEQ ctx zero-rate gap by group (stored export joined to the A0 rows) | minutes<5, pts ≥ median, as-of starter, tip < 18 ET | 55,320 rows | largest gaps are knowable pre-tip: starter10 (+29.8 pp for min<5; it is the as-of base), position (±5 pp), n_rep_out (+4.7 pp), b2b (+16 pp for matinees: schedule). `has_report` gap ≤ 0.3 pp; window length gap ≤ 0.11 tokens. No outcome-conditional NULL pattern | pass |
| 4 | knockout, leak channel: games whose proxy19 snapshot differs from the real-tip snapshot (`attack_leak.py`; 267 of 3,952 games 2022-24, all tip < 18 ET) | AVG − FULL leaky vs clean | 2023: 1,864 / 25,755; 2024: 1,821 / 25,880 | 2023 leaky −0.0107 [−0.0227, +0.0013] vs clean −0.0100 [−0.0125, −0.0070]; **2024 leaky −0.0256 [−0.0447, −0.0087] vs clean −0.0087 [−0.0112, −0.0062]**. SEQc − A0: leaky −0.0258 / **−0.0539** vs clean −0.0097 / −0.0115. Early-tip games *without* a snapshot difference: AVG − FULL +0.0041 (n 271, 13 games) / −0.0004 (n 336, 17 games), underpowered | see risk |
| 4b | knockout by slice (concentration) | team, regular/post, minutes | as above | top-team share 0.091 / 0.086, delta without it −0.0094 / −0.0091. Regular −0.0114 / −0.0099. Postseason+play-in +0.0095 [+0.0006, +0.0179] in 2023 (SEQc routes these to A0, so FULL beats the mixture there). Minutes < 5 +0.034 / +0.033 vs FULL (AVG gives back FULL's near-DNP gain; the frozen g2b is against A0, where it passes) | pass |
| 5 | replication | season swap; months; drop best month | 9 months each | 8 of 9 months negative both seasons; drop best month −0.0082 [−0.0110, −0.0054] / −0.0082 [−0.0108, −0.0059]. A different SEQ seed is not testable on CPU; the Colab rerun's 2023/24 is reported as "rerun vs stored" | pass |
| 6 | integer support | dtype/values of every arm (A0, FULL, SEQc, AVG, SEQc_S, AVG_S, AVG_A0) | all rows | all integer 199-grids; CRPS = RPS on integers for every arm | pass |
| D | re-derived primary (own paired game-clustered bootstrap, 2000 draws, seed 20261012) | pts AVG − FULL | 27,619 / 1,318 games; 27,701 / 1,320 games | −0.010001 [−0.01269, −0.00736] / −0.009783 [−0.01240, −0.00712]; equals T471 to 1e-6 | pass |

Decision-rule read against the text (code, not re-run on 2025): the primary (≤ −0.005, CI upper < 0, p ≤ 0.05), AVG − A0 (≤ −0.005, CI
upper < 0), the ROUTER_G2 guards (g1 taus 0.10/0.20/0.80/0.90/0.95 at ±0.02 with 0.05 reported only; |bias| ≤ 0.5; cov80 in [0.75, 0.85];
the g2 slice list; g2b; g3 0.25 / −0.0025; g4 fixed thresholds ≤ +0.005 when the season has no DK lines, and the DK cache holds only
2023/2024: verified 14,354 / 15,882 rows; g5 n ≥ 300 and ≤ −0.005), the three −0.003 placebo/mixture margins (a missing arm fails), the
null `delta ≤ mu25 − 2·max(0.00010, sd25)` with K = 50 and seeds `[s, 20261012]`, BH over the declared m = 7 secondary family, the T473
identity (logged n 28,173; 3.144051633928212 / 3.1325005020059655, matching the brief), the order (sha and row → spent check → oof presence
→ config check → gate A under cap 2024 → cap → marker → gate B → data gate → expected.json → members → T473 identity → SEQ read → score →
verdict → descriptive) and "results.json exists → refuse": **all match**. The descriptive step refuses without a verdict, opens it
read-only, writes under `stage2/descriptive/`, and its exceptions are caught (tests cover byte-identity).

## The one biggest risk (not a code defect; the rule froze it)

**The proxy19 report tokens inflate exactly the primary contrast, and the frozen guard that is meant to protect against them (g5) is computed
against A0, not FULL.** On 2024, the 6.6% of rows in games where proxy19 admitted a later snapshot than the real tip allows carry an
AVG − FULL of −0.0256, against −0.0087 elsewhere. That is an inflation of the season figure of about **−0.0011** (11% of −0.0098). 2023
shows none (−0.00005). g5 asks AVG − A0 on tips ≥ 19:00 ET to be ≤ −0.005, and FULL alone clears that (FULL − A0 was −0.0116 on 2025, T473),
so g5 cannot catch a SEQ-specific leak in AVG − FULL. If the true 2025 effect sits near the floor (the modeler expects −0.007 with a 0.70
chance of clearing it), a −0.001 artifact can turn a miss into a pass. The 2025 matinee share is unknown to me (not loaded).

Options for Devin (the touch is not spent yet):
(a) **Proceed as frozen** and accept the adversary's post-verdict read committed here, before any 2025 number exists: *if the verdict is PASS,
the adversary computes AVG − FULL on the 2025 rows of games whose proxy19 snapshot equals the real-tip snapshot (from the per-row export the
rule already produces, plus the 2025 report timestamps, after a disclosure row). If that clean-game delta is > −0.005 or its game-clustered CI
upper is ≥ 0, the post-result review is WOUNDED and recommends no production proposal.* This read cannot change the frozen verdict.
(b) **Withdraw before the touch** and pre-register a new id with real-tip report tokens. That needs a fresh E2 run on 2023/24 and a new gate:
cleaner, but hours of work, and "E2 as it ran" no longer holds.
Safe default if no answer: (a).

## Power / MDE and the family

n on 2025 is about 28,173 rows / 1,321 games (T473). The 2023/24 CI half-width of AVG − FULL is 0.0026, so SE ≈ 0.0013 and the 80%-power
MDE ≈ 0.0037. The binding constraint is the −0.005 point floor, not the CI. The family actually run is m = 1 primary and m = 7 secondary,
as declared. The construction was picked among six on 2023/24 (disclosed), so 2023/24 is plausibility evidence only. Nothing in this review
is a new claim.

## Not verified

The Colab job on a GPU (only the tiny CPU e2e in the tests). A different SEQ seed or a feature-group knockout of the transformer (needs a
refit). The 2025 export itself (not built, by instruction). The 2025 matinee share and leak-channel size. `rehearse` today (skipped, N2).
The `F.build_frame`/`season_cap`/`data_gate_2025` path, which is reused unchanged from the RECENT_ROLE_FULL loader approved earlier (4d7a98b).
I did not re-review it.

## Drafted rows (not appended; the main session decides)

HOLDOUT_ACCESS_LOG.md (DISCLOSURE):
`| 2026-10-12 | \`research.eval.seq_full_mix_eval rehearse\` (committed outputs c5e10a9, reports/prereg_seq_full_mix/rehearsal/descriptive/) | none (descriptive context flags) | season-2025 player_game_stats minutes and games rows loaded into memory by seq_full_mix_descriptive.run_descriptive (query \`season >= 2022\`, no upper bound) while the rehearsal target was 2024 | **DISCLOSURE, not a confirmatory touch.** Found by the adversary loader review (docs/reviews/seq_full_mix_loader_review_2026-10-12.md, N2). Nothing derived from 2025 was written: the flags are backward-looking and joined to 2024 rows only. No model, score or price involved. Fix: bound the queries by the target season. |`

TEST_LEDGER.md:
`| T478 | Adversary loader review, SEQ_FULL_MIX (2fed975a), docs/reviews/seq_full_mix_loader_review_2026-10-12.md | APPROVED WITH FIXES: blockers B1 (--resume does not check sha/HEAD/seq_run, accepts a consumed failed gate, overwrites the marker) and B2 (run honours --k). Stand-in attacks pass: planted future, missingness, replication (8/9 months negative; drop-best −0.0082 both seasons), integer support, re-derived AVG − FULL −0.0100 [−0.0127, −0.0074] / −0.0098 [−0.0124, −0.0071] (n 27,619 / 27,701). Risk: proxy19 leak channel carries AVG − FULL −0.0256 vs −0.0087 on 2024 (inflation −0.0011; 2023 none); g5 is vs A0 and does not cover it |`
(The id T478 assumes the ledger still ends at T477 when appended.)

DECISIONS.md (needs Devin): "SEQ_FULL_MIX proxy19 exposure: proceed as frozen with the adversary's pre-committed clean-game read (default),
or withdraw and re-register with real-tip tokens. Reverse if: the 2025 matinee share is materially above 2024's 6.6%."
