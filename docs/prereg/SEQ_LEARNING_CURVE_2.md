# SEQ_LEARNING_CURVE_2: does more history help the W_ALL sequence model once training steps are held fixed, and does the curve keep falling at 9 and 10 seasons? (pre-registration, descriptive, no holdout touch)

Status: **DRAFT 2026-10-13. Not frozen, no sha, no fit.** Devin approved drafting (DECISIONS 2026-10-13 07:15). Freeze needs: every gate item D1-D8 below PASS, then Devin's read, then the commit `prereg(SEQ_LEARNING_CURVE_2): <sha256>` made by a hand that can commit (hash command: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/SEQ_LEARNING_CURVE_2.md | shasum -a 256`). Everything above `=== RESULTS BELOW ===` is the rule. Season labels are `games.season` (2013 = 2013-14, ..., 2023 = 2023-24 = the select season, 2024 = 2024-25 = reserved for the replication rule in "What happens next", 2025 = the frozen holdout).
Advice or momentum: **follows advice** (Claude's recommendation, T486 attack, DECISIONS 2026-10-12 and 2026-10-13 07:15; Devin approved). This is the one pre-registered follow-up SEQ_LEARNING_CURVE allowed; it closes the curve either way.
Holdout: none touched. Exports are capped at season 2023 in code; the gate reads `season <= 2024` in SQL, read-only. 2024 and 2025 outcomes are never scored here.

## Question and why T485 could not answer it
T485 (RISING; A = C(8)-C(4) -0.0062 [-0.0079, -0.0044]; B = C(8)-C(6) -0.0029 [-0.0044, -0.0014]) left two things open (T486, WOUNDED):
1. **Data or steps?** Warm refits run 3 epochs over the whole pool, month after month, so N = 8 takes about 2x the warm gradient steps of N = 4. The cold-fit validation loss also falls with N, so data matters, but the share of A that is data is unmeasured.
2. **Is B real?** One 3-seed ensemble per window; B (-0.0029) is about 2.2x the largest seed difference and below the seed-inclusive MDE (about 0.0036).
This rule separates data from steps, pools 6 seeds per window with seed-inclusive CIs, and extends the curve to N = 9, 10 (2013-14, 2014-15), if and only if the data gate passes.

## Windows, segments, arms
Window N = the N seasons ending 2022-23 as training target rows; all test rows, blend, comparators, normalizer, era channels, S1 handling, early-stopping validation set and cold/warm hyperparameters are exactly SEQ_LEARNING_CURVE's (cold 25 epochs max, patience 4, lr 1e-3, batch 1024, hidden 64; warm 3 epochs, lr 3e-4 before every calendar month of 2023-24). Windows run here: **N = 4, 6, 8, 9, 10** (9 adds 2014-15, 10 adds 2013-14). N = 4 (= W_ALL) is the anchor; N = 1-3, 5, 7 are not rerun (T485 holds them; they are not re-read as results of this rule).
Segment S1 extends to labels 2013-2018 (derived box, played rows only, `has_report = 0`, S1 era channel, no DNP rows). Cumulative rule-flag channels are unchanged (none changes before 2016-17, so 2013-15 rows carry all zeros there; disclosed, no new channel). The normalizer adds two frozen constants, Lprev for 2014-15 = the 2013-14 full-season team-game means and for 2013-14 = L_ref (as 2015-16 was in the parent), computed at the gate (D8) and written into the rule before the freeze.
**Position channels.** Decided by gate D4: if the static fetch passes, position is real for all rows of all segments (the S2 322 survivor-pattern rows are also filled); if D4 fails after the fetch, the fallback is **zero for every pre-2019 row (S1 incl. 2013-15)**, uniform, as in the parent. Either way every window uses one construction; the choice is made at the gate, never after a curve.

Arms (all scored on 2023-24 with the parent's walk, blend SEQc, and AVG = 0.5 SEQc + 0.5 FULL on the integer grid):
* **U(N), N = 4, 6, 8, 9, 10: unmatched** (parent construction: warm epochs fixed, so steps grow with the pool).
* **M(N), N = 6, 8, 9, 10: step-matched.** At each warm refit the number of gradient steps equals what U(4) takes at that same refit (3 epochs x ceil(pool_4/1024)); the batches are drawn from the full window-N pool by consecutive random-permutation sweeps (seeded by the arm seed, so a batch never repeats within a sweep) until the step count is reached. Same lr, same optimizer state handling as U. Data volume differs, steps do not. M(4) = U(4) by definition (not a separate fit). The **cold** fit is unchanged (early stopping on the common 2022 validation set); cold steps and best-validation loss are reported per window as the no-warm-step data signal.
* **UP(4): N = 4 with steps matched upward.** Window-4 data, warm steps per refit equal to what U(8) takes at that refit (about 2x). It measures the pure step effect on fixed data.
* **Seeds: 6 per arm (0-5)**, fits stored per seed. No arm is tuned. Seed 0-5 fits from T485 are not reused (the position rule and S2 fills may differ); T485 numbers are context only.
Fits: U 5 windows + M 4 windows + UP 1 = 10 arms x 6 seeds.

## Metric, contrasts, CIs (written before any window is run)
* **Metric:** pts integer-grid CRPS, 2023-24, same rows for every arm (the job asserts identical row sets). C_X(N) is the CRPS of AVG built from the pooled 6-seed SEQ member of arm X at N.
* **Primary contrast: A_m = C_M(8) - C_M(4)** (U(4) = M(4)): what four more seasons (2015-16 .. 2018-19) add **at equal warm steps**. Why primary: it is the open T486 question and the only contrast in which window size is not confounded with optimisation. A (unmatched) is reported beside it, and the step share is **A - A_m** (CI by the same draws).
* **Second decision contrast: E = C_M(10) - C_M(8)** (the two added seasons 2013-14, 2014-15, matched), since "does the curve keep falling" is the question this rule closes. Negative is better.
* Descriptive, no family: C_U(N) and C_M(N) at every N; C_UP(4) - C_U(4) (pure steps); C_M(N) - C_M(N-1); the cold best-validation loss by N; reb/ast/fg3m for A_m and E (BH over those 6, cannot change a verdict); calibration coverage at q05..q95 for SEQc.
* **CI (seed-inclusive):** 2,000 two-way draws, drawn once and shared by every contrast: resample `game_id` with replacement (seed 0) **and**, independently within each arm, resample its 6 seeds with replacement; recompute the pooled-seed pmf and the contrast. Blend weights are those fit on the full 6-seed ensemble (disclosed approximation; the leave-one-seed-out spread of each contrast is also reported). Percentile intervals. Because the decision family is m = 2 (A_m, E), decision intervals are **97.5% (Bonferroni)**; the 95% interval is also printed. A date-clustered variant (207 dates) is a robustness print, not a decision input.
* **Power, stated now:** one 3-seed ensemble has seed sd about 0.00074 (3 values, 95% range 0.6-3.7x); a 6-seed pool has about 0.0005, a contrast of two pools about 0.00074; with bootstrap se about 0.0008 the combined se is about 0.0011 and the 80%-power MDE about 0.0031 (about 0.0034 at 97.5%). The floors below sit at that MDE; an effect smaller than 0.003 will usually be reported AMBIGUOUS, not zero.

### Decision words (all must hold unless stated; the gates D1-D8, reproduction and run-time gates pass first, else nothing is read)
**For A_m (data vs steps):**
* **DATA**: A_m <= -0.003 with the 97.5% CI upper < 0.
* **STEPS**: A_m's 97.5% CI lower > -0.003 **and** A <= -0.003 (the unmatched gain reproduces) **and** point A_m >= -0.0015: the gain is mostly optimisation.
* **MIXED**: neither (A_m between -0.0015 and -0.003, or CI straddling the floor).
**For E (the curve past 8):**
* **RISING_10**: E <= -0.003 with CI upper < 0 **and** A_m is DATA or MIXED.
* **FLAT**: point E in [-0.0015, +0.0015] **and** 97.5% CI lower > -0.003 (bounded flatness).
* **HURTS**: E >= +0.0015 with CI lower > 0.
* **AMBIGUOUS**: everything else; reported with the interval; no further follow-up (the curve is closed).
**Reproduction and construction gates (voids the run, harness fixed, rule untouched):** U(4) AVG - FULL within 0.003 of T485's NORM(4) (-0.0201) and U(8) within 0.004 of T485's NORM(8) (-0.0263); looser at 8 because position and S2 fills may legitimately move it (disclosed). FULL and A0 grids reproduce to 1e-6. Era vector of the 2023-24 rows equals 2022-23's (run-time gate). Epoch-cap diagnostic: more than one third of cold fits at the 25-epoch cap makes the verdict INVALID (budget), as the parent.
**Fallback (declared now):** if U(4) - T485's NORM(4) reproduction fails by more than the band after D4 changed position, the verdict is INCONCLUSIVE (construction) and the next step is Devin's.
* **Slices (descriptive, n and 97.5% clustered CI each):** month blocks (Oct-Nov, Dec-Jan, Feb-Apr, postseason), starters vs bench, `n_prior < 20`, realised minutes < 5, rows without a report token, players debuting 2022+ (no S1 rows; the T486 survivorship check), team drop-one.

## Data gate (CLAUDE.md non-negotiable 8; checked 2026-10-13 against what we hold)
Not frozen until every row PASSes on real data. Script stubs: `reports/prereg_seq_learning_curve_2/gate_static.py` (written, run; D4 only) and, to be written after Devin's download, `gate_check_2.py` (the parent's `gate_check.py` with seasons 2013-2014 added).

| id | gate | threshold (set now) | current | verdict |
|---|---|---|---|---|
| D1 | 2013-14 and 2014-15 files present, intact | `nbastats_2013`, `nbastats_2014`, `nbastats_po_2013`, `nbastats_po_2014` downloaded, extract OK, sha256 recorded in the folder README | **absent** (the parent's folder holds 2015-2020 only; `list_data.txt` confirms all four exist upstream) | **FAIL (data absent)**; repair below |
| D2 | ingest QC for both new seasons (the parent's G1-G7 applied to 2013, 2014) | box rows for every game; played rows >= 24,000/season; null pts/reb/ast/fg3m/starter/fga/tov <= 0.1%; exactly 5 starters per team-game >= 99.5%; box minutes per game / 240 in 2.00-2.03; possessions for 100% of games; era-flag row per game | not run | pending |
| D3 | independent box reconcile, 218-game style | 50 random games per new season (seed `20261013*10000 + season`) plus every game whose feed final score disagrees with the box-sum; all fetched from stats.nba.com; **pass:** missing/extra played rows <= 0.1% of player-games; pts/ftm off by 1 in <= 1% of player-games; minutes within tolerance for >= 91% of rows (DECISIONS 18:30) | not run (the T485 sample was 218 games for 2015-18 and passed at 0.34% off-by-one) | pending |
| D4 | **position known** for played rows of every pre-2022 season used | **>= 99% known (<= 1% unknown rows) per season**, and the unknown-row mean minutes within 3 of the known (no survival pattern); else the fallback "zero for all pre-2019 rows" is adopted (declared, uniform) and recorded | **FAIL, measured 2026-10-13** (`gate_static.json`): played rows with no position in production, history or cache: 2015-16 39.9% (241 ids), 2016-17 30.7% (202), 2017-18 20.7% (188), 2018-19 9.5% (104); pooled 25.2% = 27,984 of 110,948 rows, 380 ids (the audit's 25.4% / 384 ids includes DNP-type rows); unknown rows average 14-18 minutes vs 24-26 for known | **FAIL**; repair below (fetch preferred) |
| D5 | review-gate fix in this rule's `require_review2` | tests in a temporary git repo: (a) APPROVED then a later BLOCKED review then **deletion or rename** of the BLOCKED file => refuse; (b) `Rule:` line must equal `Rule: SEQ_LEARNING_CURVE_2 <sha8>` as a whole line (no substring: the parent id SEQ_LEARNING_CURVE is a substring of this id); (c) rewriting an earlier review's header in a later commit => refuse; (d) two `Verdict:` lines => refuse | not written | **pending** (spec below) |
| D6 | carried parent gates for held seasons (G1-G7, G13) | as T485 | PASS in the parent for 2015-2024 | PASS (carried) |
| D7 | normalizer constants for 2013-14, 2014-15 | team-game means of pts/reb/ast/fg3m written into the rule before freeze; 2013-14 Lprev = L_ref | not computable until D2 | pending |
| D8 | Colab budget | `configs/cost.yaml` rate read; units cover the estimate below | units remaining 75 (as of 2026-10-09), per-hour rate **unread** | **UNKNOWN** (Devin reads the Colab runtime picker) |

**Comprehensive-capture audit before any gate is called failed (non-negotiable 8):**
* *Seasons and sources for 2013-15:* local `data/history` (2015-2021 only), `nba.duckdb` (2019 on; no 2013-15), `data/raw/nba_data_shufinskiy_2026-10-12` (nbastats 2015-2020; upstream has 1996-97 on). Reasonable additions: shufinskiy `nbastats_2013/2014` (+`_po_`) (pbp-derived, the same adapter and derivation as 2015-18; free, Apache-2.0, ~16 MB); `nbastatsv3_*` and `pbpstats_*` (same seasons, alternate schemas; not needed); direct stats.nba.com box queue for the whole 2,600 games (~9 h of queue per 5,249 games in the parent's estimate; this would also give DNP rows, which S1 deliberately does without). Cheapest sufficient: the shufinskiy files.
* *Repairable gaps and cost:* (1) 2013-15 files: download by Devin (below), then one archivist ingest through the existing adapter, the D3 sample fetch (about 100 games at the 6 s boxsample rate = about 15-30 minutes of queue plus the disagreeing games) and a gate rerun. (2) Position for the 380 S1 ids plus the new 2013-15 ids: `uv run python -m nba.ingest.history_static_fix fetch --apply` (one CommonPlayerInfo call each, resumable, about 0.6-6 s per call, so roughly 10-60 minutes for 400-600 ids; ids with no record remain unknown and count against the D4 1%). (3) DNP rows and 2018-19 reports for S1: not repaired; the rule masks them as in the parent. Fallback if D4 still fails: zero position for all pre-2019 rows.

### Exact commands for Devin (agents cannot curl; run in a terminal, no leading "!")
```
cd /Users/devin/Downloads/nba-prediction
mkdir -p data/raw/nba_data_shufinskiy_2026-10-13_b && cd data/raw/nba_data_shufinskiy_2026-10-13_b
for f in nbastats_2013 nbastats_2014 nbastats_po_2013 nbastats_po_2014; do
  curl -L -f -o "$f.tar.xz" "https://github.com/shufinskiy/nba_data/raw/main/datasets/$f.tar.xz"
done
cp ../nba_data_shufinskiy_2026-10-12/LICENSE . && shasum -a 256 *.tar.xz > SHA256SUMS && ls -l
```
Then (archivist, Mac, CPU, one at a time, `mkdir data/ops/heavy.lock` around the load, `rmdir` in `finally`, outside xx:15-25 and xx:45-55):
```
# extract with python -I tarfile filter="data" into data/raw/nba_data_shufinskiy_2026-10-13_b_extracted/
uv run python -m nba.parse.history_nbastats --extracted data/raw/nba_data_shufinskiy_2026-10-13_b_extracted --season 2013 --season 2014 --stage data/history/nbastats_stage
uv run python -m nba.parse.history_nbastats_load --stage data/history/nbastats_stage --season 2013 --season 2014 --qc-only
# load, sample fetch (nba.ingest.boxsample), reconcile (nba.parse.history_reconcile reconcile ... --season 2013 --season 2014) as in docs/reviews/history_2015_2019_ingest_2026-10-12.md steps 4-6
uv run python -m nba.ingest.history_static_fix fetch --apply     # D4 repair; then rerun .venv/bin/python -I reports/prereg_seq_learning_curve_2/gate_static.py
```
I did not run these (no network from agents); flag names for `boxsample` season selection and `history_reconcile` are as in the parent ingest note and are unverified for 2013-14.

### The review-gate hole in the parent, fixed here
The parent's `require_review` picks the newest eligible review by adding commit among files that still exist, and checks the rule name by substring. Holes: a later BLOCKED review that is deleted or renamed drops out of the candidates and reopens an earlier APPROVED one; an earlier review whose header is rewritten later passes (fix-check F1 residual); `SEQ_LEARNING_CURVE` is a substring of this id. `require_review2` (to be written after the freeze, tested under D5): enumerate every review ever added for this id from `git log --all --diff-filter=ADR`, deleted and renamed ones included; parse each with a whole-line `Rule: SEQ_LEARNING_CURVE_2 <8 hex>` match against the frozen sha; take the lineage latest by commit order; **a deleted or renamed review counts as BLOCKED** until a strictly later APPROVED review exists; exactly one `Verdict:` line, exactly `APPROVED`; any later commit that modifies an earlier review's header voids it. The glob is `seq_learning_curve_2_loader_*.md` so the parent's glob cannot see these files.

## Cost (estimate from measured T485 runtimes)
T485 info files: 62.4 T4 minutes for 14 arms; about 0.93 minute per season-unit per 3-seed set (NORM(8) 7.4 min). Here a 3-seed set costs about 0.93N min; 6 seeds = two sets. U(4,6,8,9,10): 37 units x 0.93 x 2 = **69 min**; M(6,8,9,10): 33 units x 0.93 x 2 x about 0.75 (fewer warm steps) = **46 min**; UP(4): about 8 units-equivalent x 0.93 x 2 = **15 min**. Total about **130 T4 minutes (range 110-190)**, wall about 2.5-3.5 hours with per-arm checkpoints and restarts; **ceiling 240 T4 minutes**, unfinished arms reported unfinished (verdict INCONCLUSIVE, never a silent cut). Colab units: rate unread (D8). Mac side: exports for the new seasons, ~15 minutes, then scoring ~10 minutes. Download 16 MB free; the static fetch is free API calls.

## What happens next
* Nothing here is a betting claim or a production proposal. Closed either way: no further window, seed, floor or normalizer variants after a result.
* **Any change to the production training window** (even a DATA + RISING_10 outcome) needs its own **2024-25 replication rule** (select = this result, report season = 2024 = 2024-25, scored once, pre-registered) and is a never-evaluated model variant that would need its own logged 2025 touch (SEQ's single touch was spent on W_ALL). Devin decides; the live shadow (AVG_SHADOW_LIVE) stays on W_ALL meanwhile.
* If the data gate cannot pass (D1-D4 after the audit), the rule is not frozen, a reduced rule with N = 4, 6, 8 only (step-matched arms, 6 seeds) could be redrafted as a new id before any result.

## Playbook check (neural sequence model; docs/MODEL_PLAYBOOK.md)
1. **Information or capacity?** Capacity/data, no new pre-tip information. The step-matched arm separates data volume from optimisation, which the playbook's "data volume first" claim has not separated.
2. **Enough data for this class?** This is the learning curve extended to 10 seasons (about 280k trainable rows) with the sources checked (D1-D4, audit above).
3. **Test valid?** Same rows, blend and comparators across arms; integer-grid scoring; real-tip tokens (S1 none); game-and-seed two-way CI; step-matched and step-up controls; reproduction anchors against T485; cap diagnostic; the survival-pattern NULL in position is gated (D4) or zeroed uniformly.
4. **Lineage:** ENSEMBLE E2 -> E3 -> SEQ_FULL_MIX_RT W_ALL -> SEQ_LEARNING_CURVE (T485/T486). Guards carried: real tips, no 19:00 proxy, normalizer past-only, cumulative rule flags none-on-test-only, history position audit (SURVIVES, 2026-10-13). New guard: the review lineage cannot be reopened (D5).
5. **Scored against:** AVG vs FULL pts CRPS, no market comparison.
Class checks: window long enough? that is the question; era channels equal on test rows? run-time gate; lineage guards carried? item 4.

## What each choice protects against
* Step-matched M(N) and UP(4): a window gain that is just extra gradient steps (T486 wound 2); and the reverse, a data gain hidden by fewer steps.
* 6 seeds, two-way bootstrap: seed noise masquerading as a curve (T486 wound 1; B was 2.2x seed).
* Floors at the stated MDE with a bounded-flat word: reading a null as "no effect" when the test cannot see 0.002.
* Bonferroni for m = 2: two decision words from one run.
* Data gate with the 1% position threshold and the declared fallback: the survivorship NULL pattern (the leak class that slipped through three experiments) cannot ride in on 2013-18.
* Review lineage fix: an unfavourable review cannot be deleted to reopen a favourable one.
* One select season, no 2024/2025: no holdout touched.

## Honest prior and forking paths
Seen before drafting: T485/T486 numbers, the position audit, run times. Not seen: any matched, step-up or 6-seed window, any 2013-15 data. Priors are in docs/PREDICTIONS_LEDGER.md (pending row): P(A_m is DATA) 0.50, P(MIXED) 0.35, P(STEPS) 0.15; expected A_m -0.0040 (80% -0.0075 to -0.0005) against A -0.0062; expected E -0.0010 (80% -0.0040 to +0.0015); P(RISING_10) 0.15, P(FLAT) 0.50, P(AMBIGUOUS) 0.27, P(HURTS) 0.08; P(D1-D3 pass) 0.85, P(D4 passes by fetch) 0.70, P(UP(4) - U(4) <= -0.002) 0.40. Forking paths: floor 0.003 (the MDE), matching at U(4)'s step count rather than a fixed absolute number, Bonferroni rather than a hierarchy, pooled-seed blend weights held fixed in the bootstrap, choosing N = 4, 6, 8, 9, 10.

## Kill criteria
Any gate D1-D8, reproduction, or run-time gate failing: nothing is read; harness fixed under this id or rule withdrawn before any curve is seen. Any 2024-25/2025-26 outcome loaded: void. Ceiling reached with an unfinished arm: INCONCLUSIVE. After a result: no variants.

## Before the run
Adversary loader review `docs/reviews/seq_learning_curve_2_loader_<date>.md` (lines `Verdict: APPROVED`, `Rule: SEQ_LEARNING_CURVE_2 <sha8>`, `Reviewed-commit`, `Exports-manifest`) committed after the freeze, enforced by `require_review2` (D5).

## Open questions for Devin
1. Run the download and ingest commands now? [Yes: nothing else can proceed.]
2. Fetch the static ids (preferred) or accept zero-position for all pre-2019 rows? [Fetch; zero is the declared fallback.]
3. Floors A_m and E -0.003 and Bonferroni 97.5%? [Yes.]
4. Colab units: read the T4 rate off the runtime picker (cost.yaml); about 130 T4 minutes needed. [Run if units allow.]

## Data exposure and unverifieds
Read here: T485 outputs and run-time info files, the audit, the two DBs (counts only, read-only, `season <= 2024`), the static cache. Not verified: that 2013-14 and 2014-15 files ingest cleanly through the 2015-18 adapter; boxsample/reconcile season flags for 2013-14; the static fetch hit rate for retired ids; the Colab per-hour rate; the 0.75 matched-step cost factor.

=== RESULTS BELOW ===

(not run)
