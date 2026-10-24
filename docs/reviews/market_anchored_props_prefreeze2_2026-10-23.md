# MARKET_ANCHORED_PROPS: adversary pre-freeze review, second look (2026-10-23)

Reviewer: adversary. Clock: `date` read Fri 2026-10-23 22:02 CDT at the start of the checks and 22:12 CDT at the end. Object: the DRAFT rule on branch
`agent/modeler-market-anchored-props` at 77814a2 (commits 053b540, 6f60249, 77814a2 since the first review at 976dfdf).
First review: `docs/reviews/market_anchored_props_prefreeze_2026-10-23.md` (on main, 905bbd0): NOT_READY, 3 blockers, 4 majors, 1 minor.
Read scope: seasons 2023 and 2024 only, every outcome synthetic. No real label was selected, nothing was fit on a real outcome, and season 2025 was never loaded.
DuckDB was opened read-only only, on a byte copy (`cp` at 22:02, no `history-pull` process running).

Verdict: BLOCKED

Freeze readiness: **NOT_READY.** All eight edits from the first review are in the text and the code, and the statistical design now holds up.
The gate reproduces bit for bit, and five new off-span worlds plus a combined world produce no false model word. What still blocks the freeze is
plumbing, not statistics. The pinned extras parquet, the one input the rule now depends on by sha256, cannot be rebuilt from its source: two builds
from the same DB copy give different bytes. The parquet exists as a single untracked, gitignored copy in the shared main checkout, and the rule's own
documented gate command overwrites it from the live DB. The code-side pin is also empty and fails open. The run words are still ambiguous in two
places. Every fix is small and needs no gate rerun, because the pinned shas can stay as they are.

**Top finding.** `core.build_extras` is not byte-deterministic. DuckDB's parallel `avg` / `stddev_samp` summation order changes the last bits of `u_mean`
and `u_sd` on 20k-32k rows (max diff 4.4e-16). Two consecutive builds from one DB copy gave sha256 `19f4d93b...` / `33f317a1...` (2023) and `5dc5994d...` /
`4412754...` (2024), against the pins `9cf98865...` / `aac40cd5...`. The content is identical, but the pin is on bytes. `gate.run` (gate.py 213-215) rebuilds
and overwrites the files on every call, defaulting to the live DB. So the next person who runs the section 15 command, or a post-freeze `dryrun` that
reuses `gate.run`, silently replaces the only pinned copy. The frozen run then either refuses (NO_RESULT_INVALID_RUN, no recovery without a new id) or,
with today's empty code pin, does not notice.

## 1. Edit-by-edit confirmation (first review section 4)

| # | first-review item | text | code | evidence | status |
|---|---|---|---|---|---|
| 1 | (blocker) MR carries bench, tmout, disp10 main effects | rule line 78 (MR 15), 80 (MM 22), 70-71 (why), 228, 241, 263; note lines 160-163 | `MR_COLS` + 3 names; `_market_cov` 7 columns (core.py 241-252); `BETA0_MR` zeros(7) (283); `BETA0_MM` 22 | `assert core._market_cov(A, ...).shape[1] == 7 and len(core.BETA0_MM) == 22` held in my attack script | **FIXED** |
| 2 | (blocker) off-span worlds in the gate, G7g | rule line 180 (G7g), 195-199 (worlds); note line 167 sentence corrected ("a market error outside that span can still reach C_mod") | `core.off_span_z` (5 kinds), `gate.calibrate_coef`, G7g check | G7g: false model word 0/10 and C_mod CI<0 0/10 in all 5 kinds; reproduced exactly (section 2) | **FIXED** |
| 3 | (blocker) pin the market-side inputs | rule line 16-17 and 207 (shas), section 7 / 9 corrected (lines 143, 214-215) | `build_extras`, `EXTRAS_REL`, pin check in `load_legs` | pins match the files on disk; **the pin is fragile and fails open** (B1, M1 below) | **PARTLY FIXED** |
| 4 | (major) consequences need C_total CI upper < 0 | rule lines 107-109 | n/a (consequences are not code) | present for both words | **FIXED** |
| 5 | (major) eval dryrun reproduces gate; adversary checks eval; missingness table first | rule lines 13-15 | eval not written (by design) | text present | **FIXED (text)**; see B1, because a dryrun that calls `gate.run` destroys the pin |
| 6 | (major) market INFO needs the C_mkt_after CI guard | rule line 100 | core.py `verdict`: `c_mkt["mean"] <= INFO_FLOOR and c_aft["hi"] < 0` | unit test `test_market_info_tier_needs_the_guard_ci_and_run_words` passes; committed gate: W_mod 0.0030 market words MODEL_EXPLAINS 7, NONE 3 (the false INFO is gone) | **FIXED** |
| 7 | (major) secondary p-value tail | rule lines 112-113 | `core.gain_p`: `(1 + #{bm >= 0}) / (B + 1)`, max over the 3 clusterings | `test_gain_p_is_the_losing_tail` (gain p < 0.01, loss p > 0.99) passes; I read the code and the tail is correct | **FIXED** |
| 8 | (minor) exhaustive vocabulary, precedence, restated priors | rule lines 101-102, 255-257; PREDICTIONS_LEDGER new row (old row unedited) | `core.run_words` | words exist; **still ambiguous** (M2) | **PARTLY FIXED** |

## 2. The six checks, plus replication of the gate

| # | check | command / evidence | n | numbers | pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels (null world W0) | committed gate, rerun | 72,674 legs, 37,528 scored 2024, 10 reps | any word fires 0/10; C_mod +0.00005, binding half-width 0.00009; C_total +0.00021 (2024) / +0.00050 (2023) | PASS |
| 2 | Planted future | gate G6, rerun | 6,552 test-month legs (2025-01 calendar, season 2024) | max abs change MM 0.0, MR 0.0; a training-window flip moves MM by 0.988 | PASS |
| 3 | Missingness (label-free) | as in the first review; real-label table deferred to the run (edit 5c) | 72,674 | unchanged inputs (extras content equal to 4.4e-16) | PASS (label-free); real-label part not allowed here |
| 4 | Knockout / off-span attribution: 5 gate kinds | gate G7g, rerun | 37,528 scored, 10 reps each, oracle 0.002 | false model word 0/10 in each; C_mod CI<0 0/10 in each; mean C_mod +0.00001 to +0.00005 | PASS |
| 4b | Off-span worlds NOT in the gate (new attack) | `attack3.py` (below), K = 10, B = 500 | 37,528 scored, 10 reps each, oracle 0.002 | see section 3: 0/40 false DETECTED/INFO/BET; C_mod CI<0 in 1/40 | PASS |
| 4c | Combined world: true model 0.0011 plus a bench market over-bias 0.002 (not run in the first review) | `attack3.py` | 10 reps | C_mod -0.00067 vs -0.00068 in the model-only reference: **no inflation**; model word DETECTED 8, INFO 1, NONE 1 | PASS |
| 5 | Replication | full gate rerun at 77814a2 with build_extras stubbed to hash the pinned files (`gate_wrap2.py`) | 23 checks, 10 worlds x 10 reps | 23/23 pass; every check value identical; `world_summary` dict **identical** to committed gate.json; 276.9 s | PASS |
| 6 | Integer support | binary log loss at x.5 lines on the same legs for every arm | 72,674 | no grid-vs-continuous mixing | PASS (n/a by construction) |

Commands (from the worktree; scripts are in my scratchpad, not the repo; every exit code shown):
```
S=/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/214e8eaf-cfca-4a90-ae80-a0349b39bd8b/scratchpad
P=/Users/devin/Downloads/nba-prediction/.venv/bin/python
cp /Users/devin/Downloads/nba-prediction/data/odds/odds_history.duckdb $S/dbcopy/                               # exit 0
for r in a b; do PYTHONPATH=$PWD $P -W ignore -c "...core.build_extras('$S/extras_$r', '$S/dbcopy/odds_history.duckdb')"; done   # exit 0, exit 0
PYTHONPATH=$PWD $P -W ignore $S/attack3.py $S/attack3.json 10                                                   # exit 0, 91 s
PYTHONPATH=$PWD $P -W ignore $S/gate_wrap2.py --k-null 20 --boot 1000 --reps 10 --db $S/dbcopy/odds_history.duckdb --out $S/gate_rerun.json   # exit 0
uv run --no-project --with pytest --with pytest-cov ... -m pytest -q tests/research/market_anchored_props/ --no-cov   # 13 passed, exit 0
uv run --no-project --with ruff ruff check <3 paths>          # exit 0
uv run --no-project --with ruff ruff format --check <3 paths> # exit 0 (4 files already formatted)
uv run --no-project --with mypy ... -m mypy research/eval/market_anchored_props_{core,gate}.py --ignore-missing-imports   # exit 0
```

## 3. New attack: off-span worlds the gate does not contain (synthetic, fixed design as committed)

Before the attack I checked how `delta` relates to the market, label-free (n = 72,674). corr(delta, m) = -0.009 pooled and -0.054 within bench,
so `delta x bench` is close to a bench intercept, not a bench slope. Mean delta on bench vs starter legs by stat: reb -0.45 vs -0.25,
ast -0.35 vs -0.17, pts -0.25 vs -0.19, fg3m -0.32 vs -0.28. A **stat-specific** bench bias is outside MR's pooled bench intercept and correlates
with `delta_bench`, so that is where I aimed.

| world (oracle on 2024) | mean C_mod | C_mod CI<0 | model words | C_pl CI<0 | market words | C_total CI<0 |
|---|---|---|---|---|---|---|
| reb-only bench over-bias (0.002) | -0.00004 | 0/10 | NONE 10 | 4/10 | DETECTED_BELOW 7, NONE 2, FAIL_SELECT_SIGN 1 | 8/10 |
| ast-only bench over-bias (0.002) | -0.00001 | 1/10 | NONE 9, FAIL_SELECT_SIGN 1 | 1/10 | DETECTED_BELOW 3, NONE 5, FAIL_SELECT_SIGN 2 | 6/10 |
| bench x tmout over-bias (0.002) | +0.00003 | 0/10 | NONE 10 | 0/10 | INFO 4, DETECTED_BELOW 6 | 10/10 |
| bench-only market over-confidence, m x bench (0.002) | +0.00004 | 0/10 | NONE 10 | 0/10 | DETECTED_BELOW 6, NONE 4 | 5/10 |
| combined: model 0.0011 + bench over-bias 0.002 (total oracle 0.0041) | -0.00067 (sd 0.00023) | 9/10 | DETECTED 8, INFO 1, NONE 1 | 10/10 | MISCALIBRATION_INFO 10 | 10/10 |
| reference: model 0.0011 alone | -0.00068 (sd 0.00014) | 10/10 | DETECTED 10 | 10/10 | NONE 7, MODEL_EXPLAINS 3 | 9/10 |

Reading. No off-span world produced a false DETECTED / INFO / BET model word (0 of 40). The one FAIL_SELECT_SIGN came from a replicate whose C_mod CI
was below 0 (1/40), and the select-sign condition caught it. The stat-specific bench worlds leak a little into the placebo contrast (C_pl CI<0 in 4/10
for reb) but not into C_mod. **The market word is under-sensitive** to stat-specific errors: it reports NONE or DETECTED_BELOW at an oracle of 0.002,
recovering about a quarter of it. That is a missed market finding, not a false model claim, and the rule never sells a market word as money. The
combined world does not inflate C_mod. It adds noise (sd 0.00023 vs 0.00014), which costs one replicate's detection and gives one INFO by noise
(point -0.0010).

## 4. Findings still open (severity-ranked; each with the exact line and fix)

**B1 (blocker) The extras pin is not durable, not reproducible, and the rule's own command overwrites it.**
- `research/eval/market_anchored_props_core.py` 128-142 (`build_extras`) writes non-deterministic bytes (shown in the top finding).
- `research/eval/market_anchored_props_gate.py` 213-215: `db = db_path or str(mpr.resolve(core.ODDS_DB))` followed by an unconditional `core.build_extras(...)`. Every gate run
  rewrites the pinned files, from the **live** DB when `--db` is absent.
- Rule line 283 (section 15): the documented re-measure command has no `--db`. That contradicts rule line 16 ("a byte copy is read, never the live file").
- The pinned files exist only at `/Users/devin/Downloads/nba-prediction/data/ops/market_anchored_props/extras_{2023,2024}.parquet` (gitignored by `/data/`,
  untracked, no backup). `resolve()` looks in the worktree's `data/` first, so a gate rerun in a worktree writes a new copy that shadows main's.
- Fix (keeps both shas, so no gate rerun is needed): (a) `build_extras` raises if the target file exists, unless an explicit `--rebuild-extras` flag is
  given. The default gate path hashes the existing files against the pins, as my `gate_wrap2.py` stub does. (b) Copy both files to a backup that is
  committed or kept outside `data/`, for example `reports/prereg_market_anchored_props/extras_{2023,2024}.parquet` (1.6 MB each, the same bytes, so the
  same sha), and name that path in rule line 16. (c) Rule line 283 gains `--db <byte copy>`. (d) Rule line 13 (a): the eval dryrun must not call
  `build_extras`. Optional: round `u_mean` / `u_sd` to 12 decimals before writing, so a rebuild can be checked by content. That changes the shas,
  so it needs a gate rerun before the freeze.

**M1 (major) The code-side pin is empty and fails open.** `core.py` 58-60 `EXTRAS_PINNED_SHA256: dict[int, str] = {}` and line 173
`if s in EXTRAS_PINNED_SHA256 and ...`. As committed, `load_legs` accepts any extras file. The text (line 12) says the run refuses a mismatch, but core.py
is the harness that gets pinned at the freeze. Fix: set `{2023: "9cf98865d6fe7744f73b8532dc1411e39fff5eedbc96e3153e53b34e3ddf6c1f", 2024: "aac40cd57fff58d6c20b654c44a254cf7959305f844614a0ed07d19d6aa8fabc"}`
now, change line 173 to `if rule.file_sha256(ex_file) != EXTRAS_PINNED_SHA256[s]: raise` (a missing key raises KeyError, so it fails closed),
and add a test that a tampered or missing pin raises.

**M2 (major) The run words are still ambiguous in three places.**
- (i) `core.py` 497, 499, 517: `run_words` and the override return `"model": "NONE", "market": "NONE"`. But rule line 101 says NO_RESULT_INVALID_RUN means
  "no word, no number". `NONE` is a substantive word ("tested, nothing found"), so a reader of `results.json` would record a null for an invalid or
  killed run. The priors (lines 256-257) also do not add up: the model-word list sums to 1.00 and the market-word list to 1.00, yet P(WORSE) 0.04,
  P(KILLED_K1) 0.05 and P(INVALID) 0.03 are stated alongside them. Fix: the replaced pair is `"NOT_EVALUATED"` in code and text, and line 256 states
  that the word priors are conditional on no override or run word. Scoring then follows that rule.
- (ii) "Stage 2" names two different things: rule line 118 ("Stage 2 (descriptive, no pass role): ladder anchoring", also line 10 and line 58) and line 131
  ("Stage 2 (report, 2024)"). Fix: rename the ladder "Part L (ladder anchoring, descriptive)" at lines 10, 58 and 118.
- (iii) Rule line 133, the Stage 3 trigger: "written only if Stage 1's model word is not NONE or the market word is MISCALIBRATION_*". Stage 1 is the 2023
  select season, which has no model word. "Not NONE" also admits PLACEBO_EXPLAINS and FAIL_SELECT_SIGN, so read literally a placebo failure would earn
  the one allowed follow-up. Fix: "only if the 2024 report model word is DETECTED_BELOW_INFO_FLOOR, INFO or BET_FLOOR_PASS, or the 2024 market word is
  MISCALIBRATION_*, and no override or run word fired".

**m1 (minor) Stale numbers in the hashed text contradict its own section 8.** Rule line 33 and line 101 say "+0.00016", but the committed null C_total is +0.00021
(line 190). Line 129 says "+0.0004 ... 2.4 times the null cost", but 2023 is now +0.00050, which makes K1 2.0x. Line 3 says "20:26-20:45 CDT", but the final
gate ran at 21:39. Research note section 8 (lines 285-290, not hashed) still quotes the old numbers: null +0.00016 / +0.00041, "INFO in 1 and 10", and W_both
"MISCALIBRATION_INFO 10 of 10" (now INFO 6, DETECTED_BELOW 4). Fix: update to the gate.json values. No constant changes.

Recommended, not required: (a) record the DB path in gate.json (it does not say whether the committed run read the live file or a copy; the content
match makes this moot for the data, not for the audit trail); (b) the market word's under-sensitivity to stat-specific errors (section 3) is
worth a sentence in section 4 "What the tiers can see".

## 5. Minimum-data gate on the real data (non-negotiable 8)

Holds. G1-G5 are computed on the real legs and features: legs 35,146 / 37,528, equal to MARKET_PRIOR_RESIDUAL G2; per-stat minimum 6,339; clusters
1,249 / 1,260 games, 377 / 414 player-seasons, 35 / 36 weeks; smallest bench / tmout cell 886; u1 available at least 93.3%; spread at least 99.35%; hi share
0.110-0.408; clipped delta 0.01%; minimum lead 62.55 min. My rerun reproduced every value exactly, and pin check `equal: True` for both pairs frames.
The capture audit (section 9) is corrected for the movement pull. Rows before 2023 are now 401,816 (game markets of season 2022), and the text says
so (line 214). The pinned extras content equals a fresh build from today's DB copy to 4.4e-16, so the tonight's pull did not change the t60 inputs.

## 6. Playbook check (docs/MODEL_PLAYBOOK.md)

Class: logistic pool on a market offset (playbook "Linear and logistic models"), scored at the line (the "Quantile and distribution layers" question,
reliability at the point that matters).
1. **Information or capacity:** no new model information. The added inputs are market-side, and the class is low-capacity (22 ridge parameters). Correctly stated.
2. **Enough data:** yes for this class (low to moderate appetite). There are 28,108 / 37,528 scored legs for 22 parameters, with a measured null cost of
   +0.00021 (2024) / +0.00050 (2023).
3. **Valid test:** yes, on everything checkable label-free. The comparator now holds the main effect of every covariate the model terms touch, which
   closes the playbook's named invalidity "a comparator missing information production had". The placebo is a within-(date, stat) derangement
   that does not leak the future, and guards are sized from a null. The remaining validity risk is the input pin (B1, M1), which is about the run's
   integrity, not the statistic.
4. **Lineage:** rule section 2 is accurate. T494's -0.0002 is consistent with the restated prior C_mod -0.0001.
5. **Scored against:** binary log loss at the Pinnacle line, the T477 / T480 question. CRPS is rightly dropped.
Class checks: features as-of (PASS: same snapshot, planted future exact); era flags (none: `blowout`, `u1`, `disp10` vary within date); duplicated
signals (interaction without main effect: fixed); quantile-layer check (reliability at the line): the B0 slope-departure diagnostic is declared
descriptive, which is fine.

## 7. Multiplicity, power, holdout, integrity

- **Multiplicity:** unchanged. There are two primary words from two contrasts, and the secondary is BH over 10 with the correct tail (edit 7). My attacks
  used synthetic labels only, so they add no hidden family on outcomes.
- **Power / MDE:** binding null half-width of C_mod is 0.00009. An oracle of 0.0011 is detected 10/10 (C_mod about -0.0006 to -0.0007), INFO needs an oracle
  of about 0.0017+, and BET about 0.0045+. Below 0.0011 the MDE is still unmeasured.
- **Holdout:** no 2025 load. `assert_seasons` guards are unchanged, and every load in my scripts asserted `season.max() <= 2024`. No log row is needed.
- **Rule integrity:** still a DRAFT, with no hash yet. The branch diff 976dfdf..77814a2 is 7 files, all in scope. The gate ran at 6f60249 (gate.json `git_head`),
  and 77814a2 changes only docs, so the gate is current for the harness.

## 8. Biggest risk, not verified, drafted rows

- **Single most likely failure if this freezes as is:** the pinned extras file is overwritten or lost before the run (B1). The frozen rule then either
  cannot run (a new id needed) or, with the empty code pin (M1), runs on unpinned inputs without noticing.
- **Not verified:** the eval harness (not written, by design); the real-label missingness table (not allowed here); oracles below 0.0011; whether the
  committed gate read the live DB or a copy (gate.json does not record it; the content is equal either way); `pytest` of the whole repo (only
  `tests/research/market_anchored_props/` was run, with `--no-cov`, because the repo's coverage addopts need the full suite).
- **Drafted rows:** none. This is a pre-freeze review; the ledger row comes with the result. Suggested DECISIONS line for the main session (not appended):
  "2026-10-23 22:1x CDT | MARKET_ANCHORED_PROPS second pre-freeze review NOT_READY: edits 1-8 confirmed, gate reproduced exactly, 0/40 false model words in
  new off-span worlds; open: B1 extras pin durability, M1 empty fail-open code pin, M2 run-word ambiguity, m1 stale numbers. All text and plumbing, no
  gate rerun needed if shas kept. Reverse if: the fixes land and a third glance (diff-only) passes."

**After the fixes:** the remaining items are mechanical. A diff-only third glance covering B1, M1, M2 and m1 needs no new attack and no gate rerun,
provided the shas are unchanged, and could return READY_TO_FREEZE.
