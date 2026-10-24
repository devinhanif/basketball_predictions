# MARKET_ANCHORED_PROPS: adversary pre-freeze review, third look (diff-only, 2026-10-23)

Reviewer: adversary. Object: `git diff 99cd770 2535712` on `agent/modeler-market-anchored-props` (7 files, +143/-46). This look checks the fixes for
B1, M1, M2 and m1 from `docs/reviews/market_anchored_props_prefreeze2_2026-10-23.md`, plus five changes the coordinator listed beyond them.
Scope: as requested, there were no new attacks and no gate rerun. No label was read, no DuckDB file was opened, and season 2025 was not loaded.

Verdict: APPROVED

Freeze readiness: **READY_TO_FREEZE.** Every open item from the second look is fixed in both the text and the code, and the extra changes are
consistent with the rule. Nothing that the gate numbers depend on changed: the extras bytes are identical to the files the gate ran on, and no
override fired in any gate replicate, so `gate.json` (git_head 6f60249) still stands as the measurement. The hash of the text above
`=== RESULTS BELOW ===` at 2535712 is `aabbe127671f2213be8b88f932ebe81359cd9821e71f4fac388f7926a9c1388d`. The freeze commit changes the status line,
so the frozen hash will differ; that is expected.

## 1. Items from the second look

| item | fix in 2535712 | evidence (commands from the worktree, exit codes shown) | status |
|---|---|---|---|
| B1 pin durability | The extras files are committed at `reports/prereg_market_anchored_props/extras_{2023,2024}.parquet` and `EXTRAS_REL` points there. `build_extras` raises `FileExistsError` before opening the DB unless `overwrite=True`. The gate only hashes the files unless `--rebuild-extras` is given. Rule line 283 now uses `--db <copy>`. Rule line 13 says the dryrun never calls `build_extras`. | `shasum -a 256 reports/prereg_market_anchored_props/extras_*.parquet` gives `9cf98865...6c1f` and `aac40cd5...fabc`, equal to the pins. `git ls-files -s` shows both tracked. `git check-ignore` exit 1 (not ignored). `git show 2535712:...extras_2024.parquet \| shasum` gives `aac40cd5...`. `cmp` against the data/ops original: exit 0 (same bytes). `grep -rn build_extras\|overwrite=True research tests nba` finds the only write path behind `--rebuild-extras` (gate.py 218-220). | **FIXED** |
| M1 fail-open pin | `EXTRAS_PINNED_SHA256` is filled with both shas. `check_extras_pin` raises on a missing season key and on a mismatch. `load_legs` calls it unconditionally (core.py 198). | `test_extras_pin_fails_closed` covers a mismatch, a missing pin (2022, and 2024 after a monkeypatch) and a match. `test_committed_extras_match_their_pins` passes on the committed bytes. | **FIXED** |
| M2(i) replaced pair | `NOT_EVALUATED` for `NO_RESULT_INVALID_RUN`, `KILLED_K1` and `WORSE_THAN_MARKET` (core.py run_words and verdict). The text (line 102) says "never NONE", and line 256 says the word priors are conditional on no replacement. | Tests assert the full dict for all three. | **FIXED** |
| M2(ii) two "Stage 2"s | The ladder is "Part L" at rule lines 10, 58, 118, 206 and 212 and in the note. Only "Stage 2 (report, 2024)" (line 131) remains. | `grep -n "Stage 2"` returns line 131 only. | **FIXED** |
| M2(iii) Stage 3 trigger | "the 2024 report model word is DETECTED_BELOW_INFO_FLOOR, INFO or BET_FLOOR_PASS, or the 2024 market word is MISCALIBRATION_*, and no override or run word fired". | word diff | **FIXED** |
| m1 stale numbers | +0.00021 / +0.00050, K1 2.0x, clock line, note section 8 (INFO 0 and 10; W_both INFO 6 / DETECTED_BELOW 4) | Matches gate.json W0 and W_both summaries. | **FIXED** |

## 2. Changes beyond the list

1. **EXTRAS_REL moved to reports/, so data/ops is no longer read.** Sound, and better than a backup. One residual: with `--rebuild-extras`, the
   gate writes over the committed files, but git can restore them, and the pin check then refuses the rebuilt bytes. That is fail-closed. Accepted.
2. **WORSE_THAN_MARKET also returns NOT_EVALUATED.** Consistent with "the override replaces both words" (line 101). The gate's G7a counts any word
   other than NONE as firing (gate.py 437-438), so NOT_EVALUATED would count as a false fire, which is conservative. No override fired in any
   of the 100 committed gate replicates (counted from gate.json), so the eval dryrun's exact-reproduction requirement is not affected. Accepted.
3. **Consistency wording** (rule lines 34, 230 and 257, note line 266): "a model word of DETECTED_BELOW_INFO_FLOOR, INFO or BET_FLOOR_PASS" replaces
   "other than NONE" everywhere it bears on a consequence. Matches the Stage 3 trigger. Accepted.
4. **Part L at lines 206 and 212:** a label change only. Accepted.
5. **Runtime 3 to 5 minutes:** matches my measured rerun (277 s). Accepted.

## 3. Verification (unpiped)

```
PYTHONPATH=$PWD uv run --no-project --with pytest --with pytest-cov ... -m pytest -q tests/research/market_anchored_props/ --no-cov   # 16 passed, exit 0
uv run --no-project --with ruff ruff check <core, gate, tests>            # exit 0
uv run --no-project --with ruff ruff format --check <core, gate, tests>   # exit 0 (4 files already formatted)
uv run --no-project --with mypy ... -m mypy <core, gate> --ignore-missing-imports   # exit 0
awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MARKET_ANCHORED_PROPS.md | shasum -a 256   # aabbe127... (draft status line)
```

## 4. Still open (none blocking)

- (minor) Without `--db`, the gate still opens the live `odds_history.duckdb` read-only for `data_capture`. That cannot touch the pins, and the
  documented command passes `--db`. Optional: make `--db` required.
- Optional items not taken, and not required: the DB path in gate.json; a sentence on the market word's under-sensitivity to stat-specific errors
  (second look, section 3); rounding `u_mean` / `u_sd`.
- Carried "not verified": the eval harness (written after the freeze; the rule requires an adversary check before `run`), the real-label
  missingness table (the run's first output), and oracles below 0.0011.

## 5. Playbook check

Unchanged from the second look (class: logistic pool on a market offset, scored at the line). This diff touches only run integrity and wording.
Question 3 (valid test): the input-pin risk the second look named as the remaining validity risk is now closed.

## 6. Biggest risk now

The eval harness, which does not exist yet. A frozen text is only as good as the code that implements it. The rule already requires
(a) a dryrun that reproduces gate.json exactly and (b) an adversary check of the eval against the frozen text before `run`. Hold to both.

Drafted rows: none. Suggested DECISIONS line (main session; not appended): "2026-10-23 <time read by date> | MARKET_ANCHORED_PROPS third pre-freeze
look (diff-only, 99cd770..2535712) READY_TO_FREEZE: B1/M1/M2/m1 fixed, extras committed and pinned fail-closed, 16 tests pass; freeze under Devin's
pre-authorization. Reverse if: the eval dryrun fails to reproduce gate.json."
