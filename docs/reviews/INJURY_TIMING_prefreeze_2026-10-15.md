# INJURY_TIMING rev 2: pre-freeze adversary review (2026-10-15)

Verdict: BLOCKED

Reviewed: `docs/prereg/INJURY_TIMING.md` (draft rev 2; text sha above the marker
`48c59d27746b0359a272250b763d1da6e05719755e45f4cc81b0c7baaff7f066`, recomputed and matching), `research/eval/injury_timing_{eval,gate,rule}.py`,
`tests/eval/test_injury_timing_harness.py`, `reports/prereg_injury_timing/`, `reports/injury_timing/`, the pinned scratch table
`data/scratch/injury_intraday.duckdb` (sha256 `24f0e1a52504ad8c30d00cb07eb7afb91d7d9fce6193784ce0913ba4b0fbc100`, recomputed and matching
the rule's `REPAIRED_DB_SHA256`), and the builder `nba/ingest/injury_intraday.py`. Main at 36f57bd at the start and 4c61f11 at the end; nothing
under the rule, harness, `nba/` feature/eval code or configs changed between the two. No 2025 row, file or schedule was loaded. Every DuckDB
connection was read-only. Attack scripts are in the scratchpad (`it/*.py`), not in the repo.

**Top finding: the repaired table leaks about 30 minutes of future into two thirds of games.** Every hour-only report PDF
(`Injury-Report_YYYY-MM-DD_HHAM|PM.pdf`) prints `Injury Report: ... HH:30` in its header, but the parser stamps it `HH:00`
(`nba/ingest/injury_intraday.py:140-147`, `known_at = report_dt` at `:646`). Header minus filename time was +30 min in 80 of 80 sampled intraday PDFs,
40 of 40 stored 2024 files, 40 of 40 stored 2023 files and 30 of 30 stored 2022 files. The `HH_MM` files (`..._05_45PM`) were +0 in 30 of 30. Under the
rule's cutoff (`as_of <= tip - 60`), **883 of 1,317 (2023) and 871 of 1,319 (2024) games** use a report actually published 30 minutes before tip.
That is the 66-67% of games at "lead exactly 60", which is where the gate's p50 of 60 comes from. The 613 / 791 "OUT set differs" exposure is
mostly this leak: with corrected stamps it is 321 / 517.

Single most likely failure mode if this rule ran as drafted: M minus S on pts would come out "real", because M is trained on
tip-30 OUT lists that live can never see (the T-30 lineup lever relabelled as report freshness). The planted-future guard G1 cannot catch it,
because it plants rows that carry correct stamps.

## Playbook check

Model class: gradient-boosted trees (LightGBM context residual on a recency base). One input list changes; no capacity is added.

1. **Information or capacity:** information. Correct as drafted.
2. **Enough data for this class:** 2023 trains on 2022 plus part of 2023, and 2022 has no intraday stamps, so M's training in early 2023 is
   almost the same as S's. 2023 is attenuated by construction, which is more than the "weaker use" the rule declares (`INJURY_TIMING.md:39`).
3. **Test validity:** **invalid as drafted.** The fresh list sits after the live cutoff in 67% of games (B1). The S "live analog" serves a
   list that live cannot have (B2). An invalid test is not a null; it must not run.
4. **Lineage and inherited guards:** the report trigger (`latest_pretip_flagged`), with N15 (proxy tip admitted post-tip reports) as the direct
   ancestor. This is the same failure class one layer down: N15 was a wrong tip clock, this is a wrong report clock. The inherited
   planted-future guard tests the cutoff function, not the stamp, so it is blind here.
5. **Scored against:** integer CRPS at four count stats, against S (and A0). The comparator is right in spirit; its serving rule is wrong (B2).

Class checks (trees): missingness: games absent from R but present in S are reported; G3 holds (the build path and the gate path give identical
OUT sets in all 2,636 / 2,634 games, check 7). Planted future: present but blind to stamp errors. Re-expression of the base: no.

## Per-check table

| # | check | command (scratchpad `it/`) | n | result | pass |
|---|---|---|---|---|---|
| 1 | rule text sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INJURY_TIMING.md \| shasum -a 256` (exit 0) | 1 file | `48c59d27…f066`, matches | pass |
| 2 | input pin | `shasum -a 256 data/scratch/injury_intraday.duckdb` | 1 file | `24f0e1a5…0bc100`, matches rule line 12; preflight compares it (`eval.py:451-453`) | pass |
| 3 | **stamp = report time** (planted future, input side) | `headers.py <dir> N seed [filter]`: PDF header time minus filename time | 80 intraday, 110 stored 2022-24 hour-only, 30 stored `HH_MM` | hour-only: **+30 min in 190 of 190**; `HH_MM`: 0 in 30 of 30 | **FAIL (blocker B1)** |
| 4 | games using a post-cutoff report | `shift.py` | 1,317 / 1,319 games | R: **883 (67.0%) / 871 (66.0%)** games have labelled lead 60-89, i.e. true lead 30-59. Stored (production): 50 (3.8%) / 63 (4.8%) | **FAIL (B1)**; production note m5 |
| 5 | S = the live analog? | `live_analog.py`, `live_analog_shiftR.py`; live rule = report slot strictly before the last :20/:50 ET pretip run with made_at <= tip-60 | 1,316 / 1,317 games | corrected stamps: the R-at-tip-60 list differs from the live list in **157 / 215** games (stamp differs in 434 / 432); +0.22 / +0.25 OUT players per game live does not have. Live lead p10/50/90 = 90/90/120 against 60/90/90 | **FAIL (blocker B2)** |
| 6 | gate re-run (NN8) on the data the run must use | `shift.py` (corrected stamps, rule's tip-60 cutoff); `live_analog_shiftR.py` (corrected stamps, live cutoff) | 2023 / 2024 | tip-60 corrected: usable 1,316 / 1,317, fresh share 0.998 / 0.982, OUT diff vs stored 325 / 510 (vs production's stored table: 321 / 517): PASS. **Live cutoff: OUT diff vs stored 174 / 322: 2023 FAILS >= 300** | **FAIL for the live-analog design (B3)** |
| 7 | one function builds every list (G3 precondition) | `paths.py`: `flagged_from_availability` (run path) vs `gate.leads` (preflight path) | 2,636 R / 2,634 S games | 0 missing either side, 0 differing sets; 2022 warm-up R = S in all 1,318 games | pass |
| 8 | G4 feasibility: pins and refit | `g4_probe.py`: recompute pins with `ev.arm_crps` on `reports/lower_tail/components_*.npz`; rebuild stored features; refit pts block 0 | 8 pins; 55,320 test rows; block 0 1,062 rows | all 8 pins reproduce to 6 dp; test key set identical (sym diff 0); block 0 same rows, same order, same y; **refit c and s max abs diff 0.0** | pass (one block of 72 refit; the rest not verified) |
| 9 | the repaired union does not change A0 | code read (`eval.py:350-361`) and check 7 | n/a | A0 uses `avail_s` only; R never enters A0; no 2022 list changes | pass |
| 10 | parse quality | `parse_check.py` (player-status entries in PDF text vs parsed rows at that stamp) | 30 PDFs, 2,440 text entries | parsed 2,411 (0.988); the shortfall is almost all `out` | pass, with caveat M1 |
| 11 | stored vs intraday parse of the same report (17:00) | inline SQL (stored minus intraday rows at the same 176 stamps) | 39,029 / 38,742 rows | 287 stored rows missing from intraday, all `out`, all from **3 players** (1642260, 1641747, 1642357) the intraday resolver never matches; 0 rows the other way; 0 status conflicts in the union | pass (minor; feeds M1) |
| 12 | order: freeze, then pin, then preflight, then labels | code read `eval.py:486-500`, `rule.py:119-153`; test `test_run_refuses_before_any_data_without_the_freeze` | n/a | `require_ready` runs before `preflight`, and the preflight before `build_frames` (first label read). The pin must descend from the freeze; disk must equal the pin blobs | pass |
| 13 | number-moving imports pinned | code read `rule.py:32-36` | 3 files pinned | not pinned: `nba/props/context_residual.py`, `nba/eval/lower_tail_eval.py`, `nba/eval/context_residual_eval.py`, `nba/features/injury_report.py`, `nba/features/game_tipoff.py`, `configs/context_residual.yaml`, `configs/mov_elo_tuned.yaml`; `nba.duckdb` and `data/schedule` have no fingerprint | FAIL (M2) |
| 14 | library versions | `.venv/bin/python -I -c "import lightgbm,polars,…"` | n/a | lightgbm 4.7.0, polars 1.44.2, numpy 2.5.3, scipy 1.18.1, duckdb 1.5.6; **the harness neither checks nor logs them** (MEDIAN_BIAS does: `median_bias_eval.py:233-264`) | FAIL (M3) |
| 15 | every word in its own world | rehearsal files and `test_injury_timing_harness.py:48-69` | 3 truths | `null`->NULL, `fix`->FIX_WORTH_IT (+SKEW, INFO_GAIN), `harm`->NULL+CRPS_HARM, matching the stored rehearsal JSONs. **No world or test produces `PARTIAL`; `GUARD_FAIL` only by a hand-built score; `FRESHER_REPORT_HELPS_ALREADY` is never true in any test** | FAIL (M4) |
| 16 | power / MDE | `power.py`: game-clustered SE of a paired per-row integer-CRPS delta between two production-protocol arms on the same rows (`reports/minutes_v2/props_<stat>_A1 - A0`, OOF seasons <= 2024) as the width proxy; MDE80 = 2.80 x SE | 27,583 rows / 1,315 games (2024) | MDE80 2024: pts 0.0037, reb 0.0016, ast 0.0013, fg3m 0.0007. **The -0.005 floor binds, not the CI.** See the power note | informational (M5) |
| 17 | ruff / format / mypy / pytest | `.venv/bin/ruff check …` exit 0; `ruff format --check` exit 0; `mypy` 3 files exit 0; `pytest --no-cov tests/eval/test_injury_timing_harness.py` 16 passed, exit 0 (with coverage on, a single-file run exits 1 on the repo-wide 70% gate, not a test failure) | 16 tests | clean | pass |

The gate's own widths: `gate.json` carries counts and leads only, with no CI or dispersion, so it cannot give an MDE. Check 16 uses the nearest paired-arm proxy on disk and says so.

## Findings (severity-ranked; file:line and exact fix)

**B1 (blocker): the report clock is 30 minutes early for every hour-only file, so the pinned R leaks.**
`nba/ingest/injury_intraday.py:140-147` (`parse_report_filename`) and `:619-646` (`report_dt` = `known_at` = filename time).
Fix: stamp each report with the time printed in its PDF header (`Injury Report: MM/DD/YY HH:MM AM|PM`). Fail the file if the header is missing
or disagrees with the filename by anything other than the +30 (hour-only) or 0 (`HH_MM`) convention. Re-parse into a **new** scratch DB, put
its sha on `INJURY_TIMING.md:12`, and add a label-free preflight guard G0 to `research/eval/injury_timing_eval.py:440-468` that compares every
report's stored stamp with its header (from `injury_intraday_reports`, after adding a `header_dt` column) and refuses on any mismatch.
The stored table (11:00 / 17:00) has the same +30 offset (checks 3 and 4); R takes those rows too (`gate.py:73-75`, `eval.py:352-354`),
so shift them as well when building R. Leave A0/S training on the table as production has it (that is what production trains on), and state this.

**B2 (blocker): S does not serve what live serves.** Live writes pretip runs at :20 and :50 ET (`~/Library/LaunchAgents/local.nba.daily-pretip.plist`;
`ops/nba_daily.sh` header). The forward log scores the latest prediction with made_at <= tip - 60 (`nba/daily/settle.py:120,188`). That run pulls
only report slots strictly before made_at (`nba/daily/pipeline.py:213`, `nba/daily/injury.py:37-46`) and serves per game with
`serve_pretip_flagged(now = made_at)` (`nba/daily/predict.py:247-270`). S's serving rule (`INJURY_TIMING.md:24`; `latest_pretip_flagged` at tip-60
in `eval.py:357-358`) ignores made_at. Even with corrected stamps, the two lists differ in 157 / 215 games.
Fix: give the harness one cutoff function, `live_cutoff(tip) = (latest hh:20 or hh:50 ET <= tip - 60) - 1 µs`, and use it for S-serve,
M-train and M-serve (and the gate). Rewrite §2 S and M and §1 to say "the list a T-60-eligible live run sees". Keep the rule's tip-60 list only as a
declared, wordless sensitivity, or drop it.

**B3 (blocker, NN8): the gate has to be re-run on the corrected, live-cutoff selection, and in 2023 it fails.** OUT-diff vs stored is
174 (2023) and 322 (2024) against the >= 300 threshold (`gate.py:36-38`). The capture audit (§8) is complete: the hourly cadence is everything
the CDN published, there is no 2023-24 source with finer timing, and history 2021-22 is out of scope, so this is a data failure in 2023 under NN8.
Question for Devin (headless; safe default first):
(a) **Safe default:** freeze on the live cutoff with 2024 deciding, and demote P3 (`INJURY_TIMING.md:44`, `eval.py:145`) to "2023 point < 0,
reported" because 2023 cannot pass its own exposure gate. This is a rule change before any result, so it is allowed, but it must be written
as rev 3 with this reason.
(b) Use the corrected tip-60 cutoff (gate passes 321 / 517) and rename S "fresh-served" rather than "live analog". This answers a question
live does not ask.
(c) Close the rule as a data failure and let the 2026-27 live log (which records stamp, made_at and tip) answer it.

**M1 (major): require a past-only placebo arm. Decided: yes.** M differs from S in source and parse as well as freshness. Three players'
OUT rows are never matched in the intraday parse (check 11), about 1.2% of PDF entries go unparsed (check 10), and early-2023 M is mostly
2022 stale rows (playbook item 2). Placebo P: train on R-sourced lists cut at each game's stored used stamp (the same freshness as the stored
table, from the intraday parse), serve as S. Add to `eval.py:400` (`("P", (fp, fr_live))`). Report P minus S; a stat counts toward P1 only
if M minus P also meets P1 (`eval.py:144`). Cost: 72 fits, the same order as one arm.

**M2 (major): pin the number-moving imports and the data.** `research/eval/injury_timing_rule.py:32-36`: add `nba/props/context_residual.py`,
`nba/eval/lower_tail_eval.py`, `nba/eval/context_residual_eval.py`, `nba/features/injury_report.py`, `nba/features/game_tipoff.py`,
`configs/context_residual.yaml`, `configs/mov_elo_tuned.yaml` and the ingest/parse files used for the new scratch DB.
`nba.duckdb` is written daily. G4 protects the A0 path only. Add a label-free preflight fingerprint (sha256 of the sorted per-game
`(game_id, used stamp, OUT set)` for S and R, seasons 2023-24) to the rule above the marker.

**M3 (major): library versions.** Add `PINNED_VERSIONS = {"lightgbm": "4.7.0", "polars": "1.44.2", "numpy": "2.5.3", "scipy": "1.18.1", "duckdb": "1.5.6"}`
with a refusing check in `preflight` (`eval.py:440`) and log them in `results.json` (`eval.py:500`), as in `median_bias_eval.py:233-264`.

**M4 (major): each word in its own world.** `eval.py:57` (`TRUTHS`) and `:192-194`: add a `partial` truth (exactly one stat's S biased) that
must give `PARTIAL`, and a `guard` path in which a failing preflight or G4 yields the declared outcome. Add `words()` tests that make
`FRESHER_REPORT_HELPS_ALREADY` and `INFO_GAIN_M_VS_A0` true and false at their thresholds (`tests/eval/test_injury_timing_harness.py:48-69`).
Also resolve `eval.py:426` (`words(sc, True)` hard-coded): §4 says `GUARD_FAIL` replaces the word, but `run` raises instead. Either emit
`GUARD_FAIL` with the reason, or change §4/§6 to "the run stops; no word".

**M5 (major, rule text): power and reachability.** The CI is not the constraint: the MDE80 proxy is 0.0007-0.0037. The -0.005 floor is.
For ast (CRPS 0.90) and fg3m (0.59), -0.005 is 0.55% and 0.85% of the whole score. On the live cutoff only 322 of 1,317 games (24%) change
in 2024, so the effect inside affected games would need to be about -0.02. The nearest paired proxy (a minutes-input change) moved pts by
-0.0018 [-0.0044, +0.0008] and ast by -0.0005 in 2024. `FIX_WORTH_IT` (two stats) is close to unreachable, and `PARTIAL` (pts) is the
realistic ceiling. Write this into §3 (`INJURY_TIMING.md:35`) and restate the §10 priors (P(FIX_WORTH_IT) 0.12 looks high) before the freeze.

**M6 (major, rule text): restate every number measured on the mis-stamped clock.** `INJURY_TIMING.md:18` (stored lead median 180 is really
150), `:28` and `:85` (the "T-180 to T-60" table is really T-150 to T-30 for most games; `reports/injury_timing/gap_2023_24.json`,
`research/scratch/injury_timing_gap.py`), `:77-83` (gate table), `:111-114` (priors that cite the 47% / 60% exposure).

**m1:** `eval.py:135` docstring cites "section 5"; the rule is §4.
**m2:** `INJURY_TIMING.md:39`: declare that 2022 warm-up rows have no intraday stamps, so M is close to S in early 2023 (attenuation, not just "about one season").
**m3:** `eval.py:206-246` G1 tests the cutoff, not the clock. Keep it, but G0 (B1) is the guard that would have caught this.
**m4:** the unmatched-name rate is not recorded per file in the scratch DB (`injury_intraday.py:621-646`; the 5% cap only raises). Add an `n_unmatched` column at re-parse.
**m5 (production, outside this rule; for the main session):** production's report trigger is trained on the same mis-stamped stored history.
In 2023 / 2024, 50 / 63 games (3.8% / 4.8%) use a report published after tip-60 (true lead 30-59, tips 18:00-18:29 and matinees). 2022 files are
the same, and 2019-21 history was not checked. The live path is conservative, not leaky: if only an hour-only file exists, the newest-first slot
probe stamps it at a :15/:30/:45 slot after its real time (`injury.py:300-306` via `official_report_url_candidates`). This was not verified
against a live 2026-27 file.

## Corrected family and power note

The family as drafted (4 one-sided M minus S tests in 2024, BH) is right in shape. With the placebo (M1) the family stays at 4: the M minus P
condition is a per-stat requirement, not new tests. Secondary contrasts stay wordless. No result exists, so there are no surviving wins to
report. Power: n is about 27.6k rows and 1,315 games per season per stat. The MDE80 proxy is 0.0007 (fg3m) to 0.0037 (pts) in 2024, below the
floor, so a NULL here will be informative ("smaller than -0.005") as long as the CI upper is above -0.005. It must not be read as "no effect".

## What was not verified

- The G4 refit beyond pts block 0. That block matched exactly; the other 71 (stat, block) refits were not run (watchdog).
- Parse fidelity at the name level. Check 10 counts entries with a regex lower bound; names were not matched one by one.
- The 2026-27 report cadence and filename form. If reports come every 15 minutes, the live list is fresher than the hourly 2023-24
  simulation, and the live-cutoff arm is a conservative bound.
- Publication lag after the header time. The header is treated as known-at, and :20/:50 runs leave 20-50 min of slack in the hourly cadence.
- 2019-21 history stamps.

## Freeze steps (after the fixes; in this order)

1. Fix B1 in the ingest (header stamp), re-parse to a new scratch DB, record its sha and per-file `header_dt` / `n_unmatched`.
2. Rev 3 of `docs/prereg/INJURY_TIMING.md`: live cutoff (B2), Devin's choice on B3, placebo P (M1), the power text (M5), restated numbers (M6),
   new `REPAIRED_DB_SHA256` and the data fingerprint (M2).
3. Harness: G0, the live-cutoff function, arm P, pinned files and versions (M2, M3), the `partial` and `guard` worlds plus flag tests (M4).
   Re-run `rehearse` for every truth and `check` (label-free). Then ruff, format, mypy and pytest, unpiped.
4. Re-run the gate on the new table (NN8) with `date` before and after; paste it into §7.
5. Adversary re-check of rev 3 (diff only plus new contradictions).
6. Main session with Devin: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INJURY_TIMING.md | shasum -a 256`, then
   `git commit --allow-empty -m "prereg(INJURY_TIMING): <sha>"` (the rule text already committed and unchanged).
7. `.venv/bin/python -m research.eval.injury_timing_rule pin`, then `git commit --allow-empty -m "harness(INJURY_TIMING): <pin>"`. The pin must
   descend from step 6, with no harness edit in between.
8. Only then: heavy lock, `check`, `run --sha <sha> --repaired <new db>`.

## Drafted rows (not appended)

Ledger (for the main session to number):
`| T??? | 2026-10-15 | data: official-report hour-only filenames are stamped 30 min early (header HH:30, filename HH:00; 190/190 sampled 2022-24 PDFs; HH_MM files correct 30/30) | production stored table: 50/1,317 (2023) and 63/1,319 (2024) games use a post-tip-60 report; INJURY_TIMING rev 2 R: 883 / 871 games | BLOCKED INJURY_TIMING rev 2; production impact unmeasured | docs/reviews/INJURY_TIMING_prefreeze_2026-10-15.md |`

BEST_PRACTICES (proposed): "A report's known-at is the time printed in the report, not the time in its filename; every ingest checks
header against filename and refuses a mismatch (INJURY_TIMING rev 2, 2026-10-15: hour-only files are HH:30 reports)."
