# Adversary review: live-path part of the 2026-11-17 morning merge (61e3a741)

Date: 2026-11-17 (evening, after the merge was already running). Reviewer: adversary.
Scope: `git diff 2cc4e5c9 61e3a741 -- . ':!app' ':!docs'` (45 files). Before = 2cc4e5c9 (first parent),
after = 61e3a741. Both trees were checked out read-only as detached worktrees in the reviewer's scratch directory;
every Python run used `PYTHONPATH=<tree>` with that tree as cwd (the venv's editable install points at
`nba-wt-int-1117`, so without it `import nba` resolves to the wrong tree; checked with an assert on `nba.__file__`).

**Result: SURVIVES.** I found no input on which the merged code makes the nightly pipeline, JARVIS or the market
jobs behave differently from before. Four checks back this: a fee grid run through both trees, a whitespace-blind
diff of the moved code, AST checks of the files whose edits were said to be docstring-only, and parsing both
versions of the two configs. The problems I did find are outside the running path. Two are majors: a cleanup script
that has not been run yet, and a CI change that weakens the gate for future merges. Neither alters tonight's behaviour.

Verdict: APPROVED

(APPROVED here means no revert is needed. Fix the two majors before `ops/cleanup_2026-11-17.sh --really` is run
and before the next live-path merge leans on CI.)

## Findings by severity

### Blockers
None.

### Major

**M1. `ops/cleanup_2026-11-17.sh --really` can delete things that are not provably scratch.** It is not run yet;
read as if Devin runs it tomorrow.
- *Line 104*: `item "REVIEW FIRST ... .env.save" ... rm -- "$ROOT/.env.save"`. `--really` runs every printed
  command (line 43-46), so the item labelled "REVIEW FIRST" is deleted with no review. The script's own comment
  (line 103) says this "may be the only copy of a key". Scenario: `.env` was edited after `.env.save` was made, Devin
  runs `--really` to free disk, and the only copy of the old key is gone. That loss cannot be undone.
  *Fix*: delete line 104, or print it only as a `[review]` line with no command, as section 1 does for detached
  worktrees.
- *Lines 72-80, section 1*: "fully merged" is `git merge-base --is-ancestor "$br" main`. That is also true for a
  branch with **zero** commits beyond main, which describes every freshly made lane worktree. The exclusion list
  (line 69) covers only `*nba-wt-1117-*` and `*nba-wt-int-1117*`. A read-only replica of the selection (my script
  `cleanup_select.sh`, no deletion and no `du`) picks **30 worktrees today**. Two were created or modified today:
  this review's own worktree `nba-wt-adv-morning-live` (tip 109f8c1d = main, made minutes earlier) and
  `nba-wt-ci-combo-golden` (mtime 2026-11-17 16:41). Scenario: Devin runs `--really` while a lane is working in a
  worktree that is fresh and so far clean. `git worktree remove` deletes the worktree under the running agent,
  and `git branch -d` deletes the branch it was about to commit to.
  Also, `git worktree remove` without `--force` refuses only for tracked changes and untracked files, so it
  deletes **ignored** files without listing them. The selected worktrees hold 0 to 141 ignored entries, for
  example `data/models/` and `registry_store/` in `agent-ae2730aaf82f85d52`.
  *Fix*: in section 1, require `git rev-list --count main..$br` > 0 (the branch actually carries merged commits)
  and a worktree mtime older than 24 h. Print `git -C "$wt" status --porcelain --ignored | grep '^!!'` (excluding
  caches) beside each candidate, and send any worktree with non-cache ignored files to `[review]`.
- *Lines 93-96*: the glob `"$ROOT"/.claude/worktrees/data_aside_*` deletes **any** aside directory that exists on
  the day the script runs. The claim "each holds only an empty raw/" was true when the script was written; nothing
  checks it at run time. *Fix*: before the `item` call, skip unless `find "$d" -type f | head -n 1` is empty.

No `rm -rf` runs on a variable that could be empty. Every `rm` target is either a literal path or a non-empty loop
variable checked with `[ -e ]`. The two `data/ops` log deletions (lines 109-112) are named, deliberate and Devin's
call.

**M2. CI no longer blocks on the only tests of three live-path modules, and their nightly failures stay green.**
- `tests/conftest.py:53-55` marks `tests/eval/test_avg_shadow_live.py`, `tests/eval/test_full_shadow_live.py` and
  `tests/daily/test_checkpoint.py` as `slow`. `make test-fast` (the blocking CI `unit` job, `ci.yml:65`) now
  deselects them. Nothing else tests these modules. `grep -rln` finds no other test importing
  `nba.daily.checkpoint`, and the only other importer of the two `nba.eval.*_shadow_live` readers is
  `tests/test_leadtime.py`, which reads a constant. `nba.daily.checkpoint` is a nightly step
  (`ops/nba_daily.sh:255`, writing the immutable pre-registered checkpoint snapshots). The two eval modules read
  the frozen AVG_SHADOW_LIVE and FULL_SHADOW_LIVE rules.
- They now run only in `unit-full` (`ci.yml:71`, schedule or dispatch only). That job has `continue-on-error: true`
  (`ci.yml:74`), so the workflow ends green even when the job fails. The 70% coverage ratchet sits behind the same
  switch.
- Scenario: a future edit breaks the checkpoint snapshot writer. Push CI is green, the nightly run is green with a
  hidden failed job, and the first sign is a missing or wrong immutable snapshot on a look date.
- *Fix (smallest)*: delete those three lines from `SLOW_PREFIXES`. I ran `test_checkpoint.py` together with two
  JARVIS book files: 58 tests in 58 s. Also drop `continue-on-error: true` from `unit-full`: a red nightly run is
  the point of having one.
- Context, not new: before the merge, 35 of the last 40 `unit` runs were cancelled (the builders' count), so the old
  gate was weak in practice. The change still narrows what blocks.

### Minor

- **m1. Makefile:35-36 contradicts the code.** It says "The rung-4 torch file is marked slow, so it is excluded here
  and the torch/lightgbm OpenMP deadlock cannot occur." It is not marked slow: it is absent from `SLOW_PREFIXES`
  and from the 284 slow tests collected, and `lane_ci.md:51` says so. `make test-fast` runs it in one process with
  the LightGBM preload (`tests/conftest.py:16-18`), which is the combination that conftest itself documents as
  segfaulting on macOS. It passed in the builders' fast runs, but by ordering luck. *Fix*: correct the comment, or
  add `"tests/research/models/test_rung4_stepheads.py"` to `SLOW_PREFIXES`.
- **m2. `tests/research/f1_warm_window/test_f1_warm_window_run.py:355`** now skips instead of failing when the
  environment differs from the F1 version pin. A frozen rule's environment mismatch turns into a quiet skip.
  Research only; not live. *Fix*: keep the skip, but make it visible: `pytest.xfail` with `strict=False`, or list it
  in the CI summary.
- **m3. `nba/daily/pplay_shadow/model.py:29`**: `CONFIG` is now resolved from `__file__` instead of the cwd. This is
  a real run-time change, but it is the same file whenever cwd is the repo root, which it is for launchd
  (WorkingDirectory = the main checkout) and for the replay. The path is not stored in any row or digest
  (`feature_set_sha` hashes features, parameters and the lightgbm version only). No fix needed; recorded because
  it is not a pure refactor.
- **m4. `.gitignore:38-41`** stops new daily `reports/briefing`, `forward_scorecard`, `props_analysis` and
  `parlay/evaluate_*` files from showing as untracked. No prereg or frozen harness cites these paths (checked by
  grep over `docs/prereg/`, `FORWARD_PREREG_2026_27.md` and `TEST_LEDGER.md`). Readers use the working tree, and
  `nba/daily/runmeta.py` already used `--untracked-files=no`. Risk: a future evidence file there is forgotten. The
  comment's `git add -f` instruction is the mitigation.
- **m5. `model-gate` was skipped**, which is not new. `ci.yml:135` `if: github.event_name == 'pull_request'` is
  unchanged by this diff. The merge changed `nba/parlay/ev.py` (a gated path) but reached main by push, so the
  gate never looked at it. The fee grid below shows `order_fee` is bit-identical, so nothing was missed this time.
  The process gap remains.
- **m6. `nba/jarvis/cli.py` docstring** is the argparse `description` (`cli.py:66`), so `python -m nba.jarvis --help`
  prints the new Discord-sink text. Display only.

## What I reproduced (exact commands, numbers)

Variables: `S=<scratchpad>/advml`, `PY=/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python`.

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Fee grid, end to end through each tree's own public code (`jarvis.edge._order_fee` for polymarket and kalshi, both `FastArbConfig` fee schedules, `parlay.ev.order_fee` taker/maker plus a 0.5/0.25 series-multiplier FeeModel; real configs loaded) | `cd $S/{old,new} && PYTHONPATH=$PWD nice $PY $S/scripts/feegrid.py x` | 1,143 prices (every 0.01 and 0.001 tick, two float spellings) x 863 contract counts (1-500, 501-3000 step 7, 3500-10000) = 986,409 pairs x 9 fee calls; + 240,000 random-float-price pairs x 4; = **9,837,681 fee evaluations** | sha256 of every output identical in both trees: `9b40659c...5c791465`, 5,114,625 records | PASS |
| 1b | JARVIS decisions on the same run: `edge_cents`, `classify` (strong/thin/none), `max_price` for both venues; `arb_max_price` (polymarket); `contracts_for` + `order_cost` (stakes) | same run | 197,802 (fair 0.001-0.999 x ask 0.01-0.99 x 2 venues) + 9,801 arb + 1,386 stake cases | identical (inside the same hash) | PASS |
| 1c | Old JARVIS Decimal form vs `bankers_cent_fee`, adversarial: 17-digit random float prices, C up to 20,000, coefficients 0.0695/0.05/0.10/0.07, plus every 1/10,000 price x C 1-399 | `cd $S/new && PYTHONPATH=$PWD nice $PY $S/scripts/feerand.py` | **18,958,404** comparisons, of which 1,291 exact half-cent raw fees (banker's rounding exercised) | **0 differences** | PASS |
| 2 | Moved code verbatim: `book.py` removed lines vs `book_summary.py`; `poll15.py` removed lines vs `poll15_parse.py` | `diff -w -B` of `git diff` minus-lines against the new module body | 2 splits | only `import random` / `import re` differ | PASS |
| 2b | Public names: module namespace of `nba.jarvis.book` and `nba.kalshi.poll15`, old vs new | `vars()` dump diff | all names | only `random` and `re` gone; no importer or monkeypatch uses either (grep over nba/, tests/, ops/) | PASS |
| 2c | Circular import / import order | import `book_summary` before `book`, `poll15_parse` before `poll15`, fresh interpreters; identity of re-exports | 5 orders | all exit 0; `book.kill_switch is book_summary.kill_switch` etc. | PASS |
| 2d | Import-time set change for `nba.parlay.ev`, `nba.jarvis.edge` | `$PY -X importtime` both trees | | adds only `nba.markets` (6-line docstring `__init__`) and `nba.markets.fees` | PASS |
| 3 | Lead constant: value and type at each replaced site | `tests/test_leadtime.py` (in the targeted run below) | 6 aliases + 8 frozen literals + 2 dataclass defaults | `int` 60 / `timedelta(minutes=60)` everywhere, as before | PASS |
| 3b | Docstring-only edits really are docstring-only | AST dump with docstrings stripped, old vs new | `store.py`, `coldstart/__init__`, `jarvis/__init__`, `jarvis/cli.py` (+ `shared/__init__`: empty -> docstring) | AST-equal | PASS |
| 4 | Config diffs are comments only | `yaml.safe_load` old vs new | `configs/jarvis.yaml`, `configs/cost.yaml` | equal objects | PASS |
| 4b | New plist vs installed job | `plutil -extract <key> raw` on `~/Library/LaunchAgents/local.nba.kalshi-snapshot.plist` (non-secret keys only; `EnvironmentVariables` absent, exit 1) | 7 keys | identical except the added `--no-sync`; no installer globs `ops/launchd/` | PASS (installing it changes only uv's sync behaviour) |
| 5 | Frozen pins untouched | `git diff --stat 2cc4e5c9 61e3a741 -- nba/truth nba/eval nba/forward nba/parlay/line_cal.py nba/parlay/line_cal_map_v1.json nba/parlay/top_source.py research configs/parlay.yaml configs/fastarb.yaml configs/kalshi_aliases.yaml docs/FORWARD_PREREG_2026_27.md docs/prereg` | | only two NEW draft preregs (`PARLAY_SPECTRAL_DIAGNOSTIC_DRAFT.md`, `TDA_REGIME_DIAGNOSTIC_DRAFT.md`); no pinned file changed; no code hashes the bytes of any changed file (grep `read_bytes()` + sha) | PASS |
| 6 | Oracle commit vs merge commit | `git diff --stat 59625c3c 61e3a741 -- nba configs ops pyproject.toml uv.lock Makefile` | | only Makefile, the cleanup script, the plist and pyproject differ; `nba/` and `configs/` identical to what the oracle hashed | PASS |
| 7 | Targeted tests on the merge tree | `cd $S/new && PYTHONPATH=$PWD nice $PY -m pytest --no-cov -q -p no:cacheprovider tests/markets/test_fees_differential.py tests/markets/test_fee_config_agree.py tests/test_leadtime.py tests/jarvis/test_book.py tests/kalshi/test_poll15.py tests/kalshi/test_trades_carry.py tests/daily/test_context_residual_config.py tests/test_layering.py tests/jarvis/test_edge.py` | 134 | 134 passed, exit 0 | PASS |
| 7b | Slow live-path tests that CI no longer blocks on | `... pytest ... tests/daily/test_checkpoint.py tests/jarvis/test_features.py tests/jarvis/test_review_fixes.py` | 58 | 58 passed, exit 0, 57.9 s | PASS |
| 7c | ruff / format / mypy on the changed live modules | `ruff check`, `ruff format --check`, `mypy` (14 files) | | exit 0 / 0 / 0 | PASS |
| 8 | What `-m "not slow"` deselects | `pytest --no-cov -q -m slow --collect-only` | 284 | 51 live-path tests (checkpoint 13, avg_shadow_live 19, full_shadow_live 19); 233 research | see M2 |

Fee-grid answer to the brief: **0 differences** in 9,837,681 end-to-end fee evaluations, 18,958,404 pure-form
comparisons and 208,989 label, max-price, arb and stake decisions. No label, stake or arbitrage decision in
`nba/jarvis/edge.py` differs. Why: with the multiplier at 1, both evaluation orders are exact under Decimal's
28-digit context for any price with up to 3 decimals, and on 17-digit float prices the two roundings never
straddled a half cent in 19M tries. Decision row 16 did not need to accept any difference, and none exists in the
tested domain.

## Changed modules outside the replay oracle's closure

The closure is the static transitive import set of `nba.daily.replay_season` and `nba.daily.canonical_dump`,
including function-level imports (169 modules; `$S/scripts/closure.py`). Changed modules **outside** it, which
have no oracle evidence:
`nba/jarvis/edge.py`, `nba/jarvis/book.py`, `nba/jarvis/book_summary.py`, `nba/jarvis/cli.py`,
`nba/jarvis/__init__.py`, `nba/kalshi/poll15.py`, `nba/kalshi/poll15_parse.py`, `nba/markets/fastarb/config.py`,
`nba/explain/data.py`, `nba/shadow_llm/payload.py`, `nba/coldstart/__init__.py`, `nba/shared/__init__.py`.
Inside (oracle-covered as far as the replay executes them): `nba/daily/{predict,store}.py`,
`nba/daily/{avg,combo}_shadow/display.py`, `nba/daily/pplay_shadow/model.py`, `nba/leadtime.py`,
`nba/lineups/analysis.py`, `nba/markets/fees.py`, `nba/parlay/ev.py`.
Every outside module is covered here instead by checks 1, 1b, 1c, 2, 2b, 2c, 3 and 3b.

## Playbook check

Model class: none. This is an infrastructure refactor of fee arithmetic, a module split and a named constant. No
estimator is trained or scored.
1. Information or capacity: neither; no new pre-tip information and no new model.
2. Data enough for the class: n/a.
3. Test validity: the before/after comparison runs each tree's own code on the same inputs with real configs, so it
   is not an oracle copied from the new code. It does not exercise live network quotes; the prices it uses span
   every tick a venue can quote plus arbitrary floats.
4. Lineage: the fee forms descend from `configs/fastarb.yaml` (7ce3be0) and the JARVIS paper build (bfacdde); the
   guards against unverified fees (`require_verified`, `fees_verified` on `Cover`) are untouched.
5. Scored against: identical outputs (bitwise), not a metric.

## Power / multiplicity note

No statistical claim is made or tested; the question is bitwise equality, so BH, CI and MDE do not apply. The
search is exhaustive on the tick grid and random beyond it. The 18.9M random comparisons put the chance of a
difference on a uniformly random float price below about 1.6e-7 per call (rule of three); I found none.

## The single biggest risk

Not a behaviour change tonight. The biggest risk is `ops/cleanup_2026-11-17.sh --really` (M1): it deletes
`.env.save` unreviewed, and it can remove worktrees and branches of lanes that are active but clean, together
with their ignored outputs.

## What I could not verify

- The GitHub Actions run itself (no network): whether `unit-full` passes on the runner, and the runner's `uv sync`.
- The replay oracle (not run by instruction). I relied on the builders' IDENTICAL `89889f6d` at 59625c3c and showed
  `nba/` and `configs/` are byte-identical between 59625c3c and the merge.
- The full test suite (not run by instruction; game night, one process at a time).
- Live behaviour with real Polymarket quotes. I covered every 0.001 tick and arbitrary floats, but not the gateway's
  actual payloads.
- The contents of `.env` and `.env.save` (not read by instruction), so I cannot say whether `.env.save` is the only
  copy of anything.
- Whether `git worktree remove` follows a `data` symlink. Per git it does not, but I did not test it destructively.

## Recommended changes (not applied by me)

1. `ops/cleanup_2026-11-17.sh`: drop line 104; tighten section 1 as in M1; add an emptiness check to section 3.
2. `tests/conftest.py`: remove lines 53-55 from `SLOW_PREFIXES`; `.github/workflows/ci.yml:74`: remove
   `continue-on-error: true`.
3. `Makefile:35-36`: correct the rung-4 comment, or mark that file slow.

## Proposed rows (drafted, not appended)

DECISIONS (draft): `| 2026-11-17 | Adversary review of the 61e3a741 live path: SURVIVES (no behaviour change; fee
grid 0 differences in 9.8M end-to-end evaluations). Two majors outside the running path: the cleanup script must
not be run with --really until fixed (M1), and CI should restore the three live-path test files to the fast set
(M2). | docs/reviews/adversary_morning_merge_live_path_2026-11-17.md | Reverse if a live-quote replay shows a fee
difference. |`
