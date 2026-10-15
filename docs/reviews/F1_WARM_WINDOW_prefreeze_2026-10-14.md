# F1_WARM_WINDOW pre-freeze review (adversary, 2026-10-14)

Object: `docs/prereg/F1_WARM_WINDOW.md` and `research/eval/f1_warm_window_gate.py` on `agent/modeler-f1-warm-window`
at 184b25e (read from the worktree `.claude/worktrees/modeler-f1-warm-window`; main checkout not switched). Rule text
above `=== RESULTS BELOW ===` at that commit: sha256 `cfb410d4...` (full: `cfb410d4d2bca77138048d14be2a11a7de311f7236580655a4e986114c8244ea`).
Nothing fitted, no contrast computed, season 2025 never read (every query `season <= 2024`, DuckDB `read_only=True`
in `load_inputs`, verified at `nba/eval/context_residual_eval.py:123,137`).

Verdict: APPROVED WITH EDITS

Top finding: **the order of operations already approved in DECISIONS (2026-10-14 20:31) makes F1 void under its own
kill criterion.** The WOWY_STRUCT E7 power re-measure (WOWY draft line 111, "a 2019+ A0_warm forecast on 2023 / 2024 test
rows") fits exactly arm C and produces its 2023-24 forecasts. DECISIONS says the re-measure runs first and "F1 freezes
first" only at signing. Once C's forecasts exist, C minus A is one join with the labels away (A's CRPS is production's,
already known), so "outcome-free" is enforced by trust only, and F1's kill criterion ("any arm of this rule OR any
WOWY_STRUCT arm was fitted or scored before this rule's freeze commit") fires. F1 needs no fit for its own gate, so the
fix is free: freeze F1 before the re-measure runs (edit 1). As of this review no re-measure output exists
(`reports/prereg_wowy_redistribution/` holds only `gate.json` 19:34 and `crps_null.json` 19:36; no `data/ops/heavy.lock`).

Model class: gradient-boosted trees on tabular features (LightGBM context-residual heads), with the lower-tail mixture
(quantile/distribution layer) refit per arm.

## Checks run (all read-only, seasons <= 2024, CPU, each under 10 s)

| # | check | command | n | result | pass |
|---|---|---|---|---|---|
| 1 | gate re-run, compare to the draft | `cd .claude/worktrees/modeler-f1-warm-window && F1_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=$PWD .venv/bin/python -W ignore -m research.eval.f1_warm_window_gate $SCRATCH/f1_gate.json` (exit 0, 9.0 s wall) | 84,170 cold / 327,115 warm played rows | every number in the draft reproduced exactly: keys 55,320 cold, 0 missing in warm, 114 extra (89 / 25); first block 25,110 / 27,330 / 102,924, last 80,271 / 82,605 / 158,199, 18 blocks; usable report 1,141/1,143, 1,170/1,171, 1,323/1,323, 1,318/1,320, 1,317/1,318, 1,317/1,320; planted future max abs diff 0.0 over 256,691 rows x 73 columns, 56 late columns moved; NULL range 0.0020, 0 history-vs-current gaps > 0.01; sizing pts 0.679 / 0.859 etc. | yes |
| 2 | reproducible from main | `git diff --stat 184b25e main -- nba/props/context_residual.py nba/eval/context_residual_eval.py nba/features/game_tipoff.py nba/props/metrics.py nba/props/ configs/context_residual.yaml configs/mov_elo_tuned.yaml` | n/a | empty: every module and config the gate imports is identical on main (main's 16 extra nba/ files are shadow/ops code, not imported). The gate script itself exists only on the branch, and the gate JSON is committed nowhere | yes for code; **no for the record** (edit 5) |
| 3 | real tips, no silent proxy | scratch `attack1.py`: both schedule dirs, join to games 2019-24 | 7,595 games | 0 games without a real tip in every season 2019-2024; schedule dirs overlap on 0 game_ids (so `unique(keep="last")` decides nothing); 0 duplicate game_ids across the two DBs; 0 duplicate (game, player) box keys. The monkeypatch of `GT.build_game_tipoff` is effective because `attach_real_tips` resolves the name at call time (`game_tipoff.py:77`) | yes |
| 4 | planted post-tip report row (check 2 of six) | `attack1.py` / `attack2.py`: add one OUT row for a player who played, `source=nba_official_report`, in 120 random games of 2019 / 2020 / 2021 / 2023, at tip-59, tip+1, tip+30 min; positive control at tip-61 and tip-90 | 120 games per offset | rejected at tip-59 / +1 / +30: 0 of 120 OUT sets change; positive control tip-61 and tip-90: 86 of 120 change (not inert; the other 34 have a later real snapshot inside the window) | yes |
| 5 | NULL share by outcome-correlated group (check 3 of six) | `attack3b.py`: every column of the pts stat frame, by season, NULL-or-NaN share split by minutes < 5, pts above season median, starter10 >= 0.5 | warm 2019-24 161,765 rows; cold 2022-24 | largest asymmetry warm 0.0014 (`vac_*` / `n_out_rot`, minutes < 5, 2022: 0.0029 vs 0.0015, n 2,067), cold 0.0020 (`opp_*`, 2022 warm-up); none in 2019-21 above these; the pts-median and starter splits are smaller. Played-vs-DNP is not applicable (the heads' frame is played rows only, as production) | yes |
| 6 | report-era timing signature | `attack2.py`: lead (tip minus `as_of`) of the latest usable snapshot per game, and `as_of` hour by season | 7,586 games | lead median / p10 / p90 in minutes: 2019 150 / 60 / 210; 2020 150 / 60 / 180; **2021 60 / 60 / 90**; 2022-24 180 / 120 / 300. `as_of` hours: 2019-20 three a day (13, 17, 20 ET), 2021 hourly (18-23), **2022-24 only two a day (11, 17)**. Timestamps are ET-consistent with tips (no UTC shift). This is a finding, not a leak: see F3 | n/a (finding) |
| 7 | firewall vs WOWY_STRUCT text | `git show agent/modeler-wowy:docs/prereg/WOWY_STRUCT.md` (c44ee72) and `research/eval/wowy_struct_gate.py::warm_frame` | n/a | C matches A0_warm in frame builder (same two-DB concat, same tip patch, same `unique(keep="last")` on static), window 2019-10-01, seed 0, production settings, mixture refit in-block (WOWY E7(b), DECISIONS 20:16 item 1: matches F1 line 6). WOWY's 2022-window reproduction run uses the STORED cold `pi` / `w_q` (WOWY line 56), so it is NOT arm B: F1 must fit B itself. Sequencing conflict: top finding | conditional (edits 1-3) |
| 8 | lineage: has C minus A or B minus A on 2023-24 been seen? | `git show origin/agent/adversary-struct-leak:docs/reviews/struct_leak_audit_2026-10-14.md`, ledger T420-T423 | n/a | warm-versus-cold comparisons so far are on 2020-22 only (struct audit D0/D1/D2). HW3 (T420/T421) is the closest seen result and is disclosed. No 2023-24 C or B forecast exists | yes |
| 9 | power re-derivation | `research.eval.f1_warm_window_gate.sizing()` and T420/T421 intervals in `docs/TEST_LEDGER.md:600-601` | n/a | half-widths match the ledger exactly (pts 0.00485 / 0.00365, reb 0.00195 / 0.0014, ast 0.0015 / 0.0012, fg3m 0.0009 / 0.00085). Per-season numbers reproduce. Joint (both seasons, independent, same true effect) at 1.5 x floor: pts 0.58, reb 0.77, ast 0.86, fg3m 0.98; at the floor: pts 0.18, others about 0.25; at the modeler's own prior (2024 effect about 0) about 0. BH at m = 4 lowers a single-stat pass further (p <= 0.0125 when only one stat is real) | yes, but G6 as written is per season (edit 7) |

Not applicable pre-freeze (no fit exists, by design): shuffled labels, knockout, replication, integer support, per-row
re-derivation. These are result-time checks for this adversary.

## Findings (severity-ranked; each with the line and the fix)

**B1 (blocker for the freeze order, not the text). Line 82 (kill criteria) plus DECISIONS 2026-10-14 20:31.** The
approved sequence (WOWY power re-measure, then review, then sign, F1 first) fits arm C before F1 is committed. Fix: the
main session commits `prereg(F1_WARM_WINDOW)` BEFORE the WOWY E7 power re-measure runs, and the re-measure's A0_warm
forecasts are never joined to 2023-24 labels outside the frozen F1 / WOWY harnesses. Add to the firewall section (line
38): "the WOWY E7 power re-measure is a fit of arm C; it runs only after this rule's freeze commit." If the re-measure
runs first, F1 is void as written (new id).

**M1 (major). Line 79, R2: "C's forecast arrays equal WOWY's `A0_warm` arrays exactly ... else stop."** Two problems:
(a) it does not say which fit the verdict uses, leaving a post-hoc choice between two C arrays if both exist; (b) exact
equality can fail for reasons unrelated to leakage (LightGBM thread count, library version, row order), which would kill
F1 for nothing. Fix: "The verdict uses the C arrays produced by <named harness file> at the freeze commit's code. If WOWY
reuses them, R2 is checked by WOWY, not F1. If F1 and WOWY fit separately, R2 equality (max abs diff 0.0, same
`n_jobs` 4, same LightGBM version recorded) is a cross-check; a mismatch is reported and F1's verdict still uses its own
named arrays."

**M2 (major). Line 34 (B row) and line 9: B sharing is left "verify".** The WOWY text settles it: its 2022-window run uses
stored `pi` / `w_q` (WOWY line 56) and B refits them. Fix: "B is fitted by F1 (WOWY's 2022-window run uses stored
`pi` / `w_q` and is not B)."

**M3 (major). Line 44: "two-sided bootstrap p" has no formula and no code path; R1 cites "harness `stored_reference()`"
without a file.** `nba/truth/bootstrap.py` does not exist (`nba/truth/` holds `metrics.py`, `walkforward.py`). Fix:
name `nba/props/metrics.py::paired_score_delta_ci` (draws from `_clustered_boot_means`) as it stands at the freeze commit,
the exact p (e.g. two-sided p = min(1, 2 x (1 + #{draws with mean delta >= 0}) / 2001)), and
`research/eval/recent_role_full_eval.py::stored_reference` for R1. Name the harness file that will run the arms, and
require that its frame builder is the gate's `build()` (or WOWY's `warm_frame`) unchanged; a second builder is a second
treatment.

**M4 (major). Line 58: "The sums are reproduced by the script from `main` before the freeze (a gate item below)".** No
such gate item exists in the table, the gate JSON is committed nowhere, and the data version is not pinned: the draft says
`nba.duckdb` was modified 19:50 after manifest `d0960b5a561c`; at this review it was modified again at 20:20:12
(`stat -f "%Sm"`), still not matching the manifest (history DB 2026-10-13 13:16:30, unchanged). My re-run at 20:4x
reproduced every number, so the gate tables did not move. Fix: add gate item G7 "reproduced from main code (diff of
imported modules empty)", commit the gate JSON (e.g. `reports/prereg_f1_warm_window/gate.json`) in the freeze commit, and
record the `nba.duckdb` data_version from a manifest refreshed at freeze time plus the history DB sha256; R4 re-checks
both at run time.

**M5 (major). Line 63: "no knockout arm is added (... every evaluation row is later than every training row, so a gain
cannot come from information unavailable at tip-off)".** The reasoning is wrong: the `opp_adjusted_ridge` leak was a
walk-forward design too; a leak lives in an evaluation row's features, which time ordering does not protect. Dropping the
knockout is still defensible, for different reasons. Fix: replace the parenthesis with "the HW3 knockout guarded a
model routing history rows by `has_report = 0`; history rows now carry real reports (`has_report` 0.998-1.000 every
season) and the NULL pattern is flat across eras and outcome groups (G5; adversary check 5, max asymmetry 0.0014); the
only evaluation-row features that change are box-score windows, covered by the planted-future test."

**M6 (major). Playbook fair test for trees requires "a past-only placebo and shuffle guards sized from a null"; the
draft has neither and does not say why.** Fix (text only, no new arm): state "not applicable: the contrast adds no
column; it changes the training window and the data the same columns are computed from. The leak guards are the
planted-future test (box and report rows) and the NULL audit; a label-shuffle of history rows would be a new variant and
is not run." Also name the quantile/mixture class (calibration volume is 15% of each arm's rows, so it grows about
fourfold in C: part of the treatment, already stated in line 41, and G1 / G2 are its guards).

**m1 (minor). Line 52: "starter / bench (production `starter10` definition)".** `starter10` is a 10-game share
(`context_residual.py:487`), not a binary; no threshold is given. Fix: use `last_starter` as WOWY does, or
`starter10 >= 0.5`; also write `first 15 team games = team_game_no < 15` and `playoffs = game_id prefix 004`.

**m2 (minor). Lines 14 and 55, G1: "within 0.01 of A".** A symmetric band fails C if C is better calibrated than A by
more than 0.01. Fix: `|cov_C - tau| <= |cov_A - tau| + 0.01`.

**m3 (minor). Line 44: "no stat is HARM" plus "a HARM stat is excluded from any recommendation".** Read together, a HARM
in reb fails a confirmed pts (verdict FAIL), so the second sentence never applies. Fix: pick one. Either keep FAIL on
any HARM (my preference, simpler), or "PASS for the confirmed stats; a HARM stat is excluded."

**m4 (minor). Slices and decomposition as support.** The prior expects the gain in the early 2023 blocks (one-season
refits); the "first 15 team games" and "cold start" slices will show it there. Fix: add to line 52 and line 82 "slices,
B minus A, C minus B, per-block or early-season results are guards or descriptions only; they cannot create, rescue or
qualify a pass, and a 2023-only gain is reported as FAIL."

**m5 (minor). G6 (line 72) is per season; the pass needs both.** Fix: add the joint power (pts 0.58, reb 0.77, ast
0.86, fg3m 0.98 at 1.5 x floor; pts 0.18 at the floor) and one sentence: "F1 can confirm a durable gain of about 1.5 x
floor; it cannot detect a gain that lives only in one-season refits, which is the modeler's own expectation."

**m6 (minor). Runtime is not stated.** Three arms, 4 stats x 18 blocks, C on 100-160k rows plus 54 mixture fits; it
will exceed an agent's ~600 s watchdog. Fix: state an estimate and that it runs in the background under `heavy.lock`
with per-(arm, stat) checkpoints (R5 already says the latter).

**Cross-rule (for the WOWY_STRUCT text, not F1).** WOWY E7(e) says "a pass here implies production needs the longer
window too". That is a back door: a WOWY pass could be quoted for the window change even if F1 fails 2024. Fix in WOWY:
"the window change is licensed only by F1's verdict; a WOWY pass is a candidate for the WOWY columns only."

## F3: the report-era signature, measured (finding, no edit forced)

The 2021-22 history rows carry OUT sets from a snapshot about 60 minutes before tip (hourly reports, median lead 60,
p90 90), 2019-20 about 150, and the 2022-24 production rows about 180 (only 11:00 and 17:00 ET snapshots in
`nba.duckdb`). So C learns from a 2021 season whose OUT information is fresher and fuller (n_out_rot mean 2.60, vac_min
66.6) than what any 2023-24 evaluation row gets. This cannot flatter the evaluation (evaluation leads are >= 120 minutes at
p10 and identical in A and C); it biases C toward over-trusting `vac_*`, i.e. against a pass. Two notes: (a) the
2021 snapshot at exactly tip-60 is admitted by an inclusive cutoff on a nominal PDF stamp; if publication lags the stamp,
a few 2021 training OUT sets are minutes past T-60 (training-only; same rule as production); (b) the live season's
cadence differs again, so a pass would transfer to live only as well as the 180-minute rows do. Recommend adding the
lead table to the report-era descriptives (line 63).

## Rule completeness

| element | present | note |
|---|---|---|
| metric | yes | integer CRPS, 199-point grid, every arm, identical cold keys |
| floors + justification | yes | pts -0.005; counts -0.0025 from HW3's pre-run MDE (0.002, HW3 line 182) x 1.25, widths only. HW3 used -0.005 for all four; the lower count floor sits at the CI requirement (1.96 se = 0.0024 reb 2023), so it adds little permissiveness. Under it, HW3's T420 reb (-0.0039 [-0.0059, -0.0020]) would have passed 2023 and still failed 2024. Acceptable; Devin's open question 3 |
| CI / clustering | yes | game-clustered percentile, 2000 draws, seed 0; date reported. Code path and p formula missing (M3) |
| multiplicity | yes | m = 4 BH per season, intersection over seasons; B, C-B descriptive |
| select / report | yes | both 2023 and 2024 must pass; 2025 untouched |
| slices | yes | definitions incomplete (m1); support language (m4) |
| kill criteria | yes | order with the WOWY re-measure (B1) |
| minimum-data gate on real data | yes | reproduced exactly by this review; record not committed (M4) |

## Playbook check

1. Information or capacity: both, separated by B (frame) and C (frame + window). Agreed.
2. Enough data for this class: trees are moderate-appetite (2-4 seasons). A's 2023 blocks train on one season, C's on
   four; this is a fair capacity test. The playbook note "a 2023-only fail is weak evidence" has a mirror: a 2023-only
   GAIN is the expected learning-curve artefact and must be a FAIL (m4).
3. Test valid: real tips (0 proxy fallbacks, check 3), integer grid for all arms, identical keys, planted future on box
   (0.0) and on reports (check 4), NULL audit by season and outcome group (check 5). Placebo/shuffle guard absent with no
   stated reason (M6).
4. Lineage and inherited guards: HW3 (T420-T423), Elo window (T318-T319), report-era work (T315, T325) disclosed. HW3's
   knockout dropped; the reason given is wrong but a right one exists (M5).
5. Scored against: production CRPS, not the market. Correct.

Class checks: missingness beyond as-of cause (done, clean); planted future (done, clean); re-expression of the base (B
minus A, C minus B, descriptive). The mixture layer's calibration volume changes by arm; guarded by G1 / G2.

## Shared-fit firewall: can either rule become post hoc?

- F1 post hoc: yes, if any fit of C (WOWY re-measure, WOWY A0_warm, or F1 itself) exists before F1's freeze commit
  (B1). Otherwise no: A is production, C is not yet fitted, B not yet fitted.
- WOWY post hoc via F1: F1's result reveals A0_warm's CRPS, which is WOWY's G2 comparator and STRUCT-alone comparator.
  WOWY's primary contrast (S+R minus ROLE) does not depend on it, and WOWY must also be frozen before any fit
  (DECISIONS 20:31: sign together). Safe if both are committed before the first fit; state in both texts "F1 freeze
  commit, then WOWY freeze commit, then the WOWY power re-measure, then any fit".
- Scoring a subset (cold keys) of the shared C forecasts is valid: forecasts are row-wise from block fits; no test-set
  dependent step was found in the gate path.

## Required edits (numbered; all text or record, no measurement needs a fit)

1. Freeze order (B1): F1 commit before the WOWY E7 power re-measure; add the sentence to the firewall section; mirror it
   in WOWY.
2. R2 (M1): name the verdict's C arrays and their harness; R2 mismatch is a cross-check, not a silent swap.
3. B (M2): "B is fitted by F1".
4. Inference code (M3): name `nba/props/metrics.py::paired_score_delta_ci`, the exact p formula, the R1 reference file,
   and the harness file; one frame builder.
5. Record (M4): add G7 (reproduced from main code), commit the gate JSON, record data_version from a manifest refreshed at
   freeze and the history DB sha256.
6. Knockout reason (M5): replace the parenthesis in line 63.
7. Playbook (M6) and power (m5): placebo-not-applicable reason; joint power sentence.
8. Slices and verdict wording (m1, m3, m4): binary starter definition, team_game_no / playoffs definitions, one HARM
   rule, "slices and decompositions cannot create or qualify a pass; a 2023-only gain is FAIL".
9. G1 band (m2): calibration distance, not difference from A.
10. Runtime (m6) and the report-lead table (F3) in the descriptives.

## Single most likely failure mode if a future F1 pass is wrong

A 2023 learning-curve gain (one-season refits versus four-season refits) plus a 2024 result that happens to clear
-0.005 by noise or by the 2021 report-era rows: joint power is 0.18 at the floor, so a borderline 2024 pass at m = 4 is
the place to look first (per-block 2024 deltas, date-clustered CI, and whether the 2024 gain concentrates in
`n_out_rot >= 2` rows that the 2021 era taught).

## What was not verified

- Whether official 2018-19 injury reports exist (the modeler could not; not needed for this rule).
- LightGBM determinism across `n_jobs` for R2 exact equality.
- The live 2026-27 report cadence (affects transfer, not the test).
- The WOWY harness file (not written); its builder was checked only via `wowy_struct_gate.py::warm_frame`.
- Publication lag of 2021 report PDFs versus their nominal stamps.

## Proposed rows (drafted, not appended)

Ledger: none (no result). DECISIONS (for the main session, if Devin agrees):
`2026-10-14 | F1_WARM_WINDOW pre-freeze review: APPROVED WITH EDITS (docs/reviews/F1_WARM_WINDOW_prefreeze_2026-10-14.md). F1 is committed before the WOWY_STRUCT E7 power re-measure, because that re-measure fits arm C. Reverse if: none; a C fit before the F1 commit voids F1.`
