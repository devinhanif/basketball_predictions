# Pre-freeze review: SEASON_BOUNDARY (T515), 2026-10-22

Reviewed: `docs/prereg/SEASON_BOUNDARY.md` at 3904706 (branch `agent/modeler-season-boundary`), plus
`research/eval/season_boundary_{gate,rule}.py`, `reports/prereg_season_boundary/gate.json`, and
`nba/props/context_residual.py` (production states and training window). No arm fitted, nothing scored, season 2025 never loaded.
Attack script: scratchpad `attack/sb_premise.py`. It reads league-level box sums for seasons 2014-2022 only, which are non-scored seasons the gate already exposed.

Verdict: BLOCKED

Pre-freeze status: **NOT_READY**. There are 1 blocker and 5 majors, and each has exact replacement text below. None of them asks for a new idea. Most of the rule is sound: as-of builders, clustering, integer support and family size.

**Top finding (blocker).** `nba.duckdb` holds seasons 2022-2024 only (`select season,count(*) from games where season<=2024`: 2022/2023/2024, exit 0). Production states start in 2022. That makes `L0` NaN for **every 2022 row**, so A1 = A0 on the entire 2022 training season. The month-block walk-forward trains each block on all earlier rows. So the Oct-Nov 2023 blocks, which are the primary slice, fit their residual heads and conformal calibration only on `y - m10` rows and then apply them to an `m1` base. Production's trees already correct `m10` toward `m40/m100` (via `role_gap`). On top of `m1` that correction is applied twice. C1 on 2023, and the k choice made on it, would be an **invalid test biased toward the null**. A3a is also hobbled, because its `L_adj - m10` column is all-NaN in 2022. 2024 is less affected, because 2023 returners have `L0`.

## Playbook check
Class: GBT residual on a recency base plus a quantile layer (props); LightGBM mixture (minutes).
1. **Information or capacity?** Architecture plus two pieces of pre-tip information: age, and the as-of league ratio. The draft states this correctly.
2. **Enough data?** Yes for the composite (half-width 0.63-0.65 floor units). pts alone is not powered.
3. **Valid test?** **No, as drafted.** The 2022 training rows carry no A1 base (blocker). A4 leaks the future (M3). A3a lacks `m_this` (M2). The premise test was run at one horizon only (M4).
4. **Lineage.** T500/T504/T512 guards are carried forward. The T508 information-matched baseline is only partly met (M2).
5. **Scored against.** Integer-support CRPS against A0 and against controls, with no line claim. Correct.

## The six checks (pre-freeze, label-free)

| # | check | command / evidence | n | result | pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels | n/a pre-run; A4 is the placebo | n/a | A4 as written can draw a late-season `f` for an early date, so it leaks the future (M3) | FAIL (design) |
| 2 | Planted future | `gate.json: planted_future` (x50+7 on rows >= 15 Nov; states and league table byte-identical through the cut) | 24,795 / 24,373 rows edited | identical, both seasons; `league_asof` uses `cumsum().shift(1)` by date (strict `<`) | PASS |
| 3 | Missingness | `L0` NaN iff `n_prior < 20`, which is knowable pre-tip; audit by realised-minutes bucket declared "not built" | n/a | NaN for **all** 2022 rows (blocker) | FAIL (design) |
| 4 | Knockout | n/a pre-run; A3a/A3b are the knockouts | n/a | A3a not information-matched (M2); C2's control conflates A1 with A2 (M2) | FAIL (design) |
| 5 | Replication | select 2023 / report 2024 | 7,558 / 8,104 rows | design fine once the blocker is fixed | conditional |
| 6 | Integer support | `ceil(q-0.5)` on the 199 grid for every props arm | all arms | stated | PASS |

## Focus 1: the two amended bars

**G2 player-id overlap (rookie denominator).** This amendment was **legitimate and label-free**. The bar as first written divided by all 2022 players, rookies included, and rookies cannot appear in 2021. My re-derivation:

- 541 players in 2022.
- 456 of them appear anywhere in history; 435 of those appear in 2021, a share of **0.9539**.
- **456 of 456 shared ids carry identical `birth_date` in both `players_static` tables.** The id scheme matches.

The 0.954 figure is computed nowhere in the committed harness, so it is not reproducible from the repo (M5). After the blocker fix this check becomes load-bearing, because history then feeds `L0`. It should be a reproducible birth-date id-scheme check, not a post-hoc overlap number.

**G4 fg3m driver (3P% to 3PA).** The amendment is **label-free but its reason is wrong, and the premise test was mis-specified.** Two problems:

- The amended check G4' tests whether the **3PA** update predicts **3PA**. It does not test whether a 3PA-driven factor predicts league **fg3m**, which is the quantity A2 scales.
- The premise checks only league game 20. The slice spans the first 20 *player* games, which is roughly league games 20-300.

I re-ran it with the fg3m target at four horizons, seasons 2014-2022 (exit 0):

| league games seen | 3PA driver beats no-shift on fg3m | 3PM driver | mean abs log err: no-shift / 3PM / 3PA |
|---|---|---|---|
| 20 | 8/9 | 5/9 | 0.0412 / 0.0374 / 0.0305 |
| 80 | 7/9 | 7/9 | 0.0518 / 0.0390 / 0.0327 |
| 160 | 7/9 | 9/9 | 0.0635 / 0.0309 / 0.0366 |
| 300 | 8/9 | 7/9 | 0.0505 / 0.0170 / 0.0239 |

How to read this:

- The 3PM failure at league game 20 is a small-sample artifact. From game 80 on, 3PM passes 7-9/9 and has the **lower** error at games 160 and 300.
- "League 3P% is noise" is not supported.
- 3PA is still a defensible driver. It passes the corrected bar at every horizon, and keeping it (Devin's default) is acceptable **only if** the reason and the bar are rewritten (M4).
- It is a driver switch made after a failed bar, on non-scored seasons. It is not contamination, but the record must say "selected after seeing 5/9", which the draft does.

## Focus 2: controls A3a / A3b
- **A3a is not information-matched.** A1's base uses `m_this`, the recency mean over this season only, and neither A0 nor A3a has that column. In addition, `w = g/(g+k)` is a monotone function of `n_season`, so for a tree it adds nothing.
- **A3b is weak, and C2's control comparison is wrong.** "C2 vs A3b" compares A2 (with the A1 base) against A3a plus league columns (with no A1 base). A1's margin over A3a therefore passes C2's control requirement for free.
- The era-flag description in the draft is also inaccurate. `f` takes about 160 values per season, drifting from 1.0, so as a column it is an early-season calendar flag. The fair control is the A2 offset given as a per-player column on the A1 base.

## Focus 3: BH m = 6 and the composite
- m = 6 per season, with C1-C6 as listed, is the family actually run. The k grid (3 fits) is a selection made inside 2023 only; 2024 has to pass again, which absorbs it.
- The p-value is undefined in the draft (which clustering? one-sided?). How 2024 is treated for cells that failed 2023 is also ambiguous (M5).
- The composite is the T507 construction and is sound. The per-stat guard (+1.0 floor unit) stops trades between stats. A gain carried by one stat (for example fg3m at -4 with the others at 0) passes; per-stat shares are reported as descriptive.
- Power: half-width 0.63-0.65 floor units, so MDE80 is about 0.9 for C1. C2's expected effect is -0.1 to -0.4, below its pass line. That is futile by design and stated, which is acceptable.

## Focus 4: planted future and as-of
- League table: strict date-`<`, identity exact. `prev` for 2023 is the 2022 regular season and for 2024 the 2023 regular season; both are known before opening night. For 2022 training rows, `prev` comes from history 2021 (`gate.run`); the rule text must say so.
- Age table: built from history seasons 2013-2021 only (8 transitions). Age is taken at 20 Oct of the season from a static `birth_date`, so it is pre-tip. Cells under 300 are set to 0 in the gate code, but `season_boundary_rule.py:25` says "borrows from the pooled tail". That is a contradiction (minor m1).
- History use: the draft uses history for the age table, the premise and the 2022 G5 sigma, but **not** for `L0`. That is the cause of the blocker. The fix extends history use to `L0` only.

## Focus 5: continuity slice
Correctly fenced: "No verdict word, no BH, no pass." Its inputs are pre-tip (preseason log, S-1 minutes, the row's team). There are about 54 unadjusted intervals (3 terciles x {all, movers, stayers} x 2 seasons x {props CM, minutes}, plus the per-stat ones). The slice must say they are unadjusted and use k* only (minor m2).

## Focus 6: run cost and the k grid
- The selection statistic is under-specified: which rows, which point, and how the tie rule reads.
- A3a's `w` and `L_adj` depend on k, so A3a must be fit after k* is fixed.
- There is no wall-clock ceiling (M6).

## Focus 7: ambiguities
These are covered in M5:
- "|bias| <= 0.5 (pts)": the arm's or the delta's?
- PIT coverage: randomised? which stats? row order?
- top-5 share: per stat or composite?
- "gain at least halves under A4": the formula.
- "A1 = A0 exactly for rookies": the refit changes the trees for everyone, so only the **base** can be identical.
- The fallback `m10` source: harness recomputation or the production column.

## MUSTs (exact replacement text)

**B1 (blocker): replace the `L0` sentence in A1** (`NaN with < 20 prior games ... new players)`) with:
> `L0` is built from played regular- and post-season box rows of `data/history` seasons 2019-2021 chained before `nba.duckdb` 2022-2024 (history feeds `L0` and the returner test ONLY; `m_this`, `m10` and every production feature stay `nba.duckdb`-only, identical to A0). `L0` is NaN with < 20 prior games in that chain; then the base is production's `m10_{stat}` column read from the A0 feature frame (not recomputed). This gives 2022 training rows the A1 base, so every month block's residual head and calibration are fit on `y - m1`. G1 counts, G5 and G6 are re-run under this construction before freezing; the gate reports the 2022 rows with `L0` defined.

**M1: replace the identity sentence** (`Rookies and new players ... (delta must be ~0)`) with:
> Rookies and new players get the identical BASE (`m1 == m10_{stat}` byte-exact on every row with `L0` NaN; a unit test and a run-time assert). Their forecast delta (trees are refit) is descriptive, with no bar. Run-time identity: the harness's `m10` equals production's `m10_{stat}` on every row (max abs diff 0) or the run is void.

**M2: replace the A3a/A3b bullet and the control clause of the pass rule** with:
> **A3a** (control for A1): A0 refit (target `y - m10`) plus raw columns `age`, `L_adj - m10`, `m_this - m10` (NaN at g = 0), `m1 - m10`, all at k*. **A3b** (control for A2): A1 refit (base `m1`) plus raw columns `f_stat` (four) and `m2 - m1` per stat. N3a/N3b are built the same way for minutes (`age`, `L_min - min10`, `m_this_min - min10`, `m1_min - min10`; N3b = N1 + the two stratum ratios + `m2_min - m1_min`). Control clause: C1 needs CM(A1 - A3a) <= -0.5 with binding CI upper < 0; C2 needs CM(A2 - A3b) <= -0.5 with upper < 0; C3 needs N1 - N3a <= -0.015 min with upper < 0; C4 needs N2 - N3b <= -0.015 min with upper < 0; C5/C6 the same against F3a/F3b.

**M3: replace the A4 bullet** with:
> **A4** (placebo, A2 only): A2 with `f_stat(d)` replaced by `1 / f_stat(d)` (same dates, same magnitude, opposite direction; past-only, no seed). Pass needs CM(A4 - A1) >= 0.5 * CM(A2 - A1) (the placebo keeps at most half the gain).

**M4: replace the G4' row and the driver reason** (`fg3m <- 3PA (attempts; ... see G4)`) with:
> fg3m <- 3PA. G4' (post-hoc, non-scored seasons 2014-2022, label: selected after the 3PM driver scored 5/9 at league game 20): the driver's as-of factor applied to last season's league **fg3m** rate beats no-shift on league fg3m over the next 150 games at league games 20/80/160/300 in 8/7/7/8 of 9 seasons (bar >= 6/9 at every horizon; `attack/sb_premise.py` logic moved into `season_boundary_gate.premise`). The 3PM driver has the lower error at 160/300; 3PA is kept for stability at small n_g, not because league 3P% is noise. The same multi-horizon check is reported for pts/reb/ast and both minutes strata.

**M5: add after the pass rule** (`A cell PASSES only if ...`):
> Definitions. p = one-sided bootstrap p `(1 + #{boot mean >= 0}) / (1 + 2000)` under the binding clustering (`role_prior_salary_eval.three_way`). BH: in each season all six p-values are computed and adjusted at m = 6; a cell failing 2023 cannot pass. Bias = mean(y - forecast mean) of the ARM on the primary slice, pts only, |bias| <= 0.5. PIT: randomised PIT, seed 0, rows sorted by (game_id, player_id), share in [0.10, 0.90] in [0.75, 0.85], each of the four stats, for the arm. Top-5 = `role_prior_salary_eval.top5_share` on the per-row composite (floor units). k*: argmin over {5, 10, 20} of the 2023 primary-slice CM(A1(k) - A0) point; if CM(k*) > CM(10) - 0.10, then k* = 10; A3a, A2, A3b, A4 and N1-N3b are fit only after k* is recorded. G2 id check (reproducible in the harness): the share of 2022 players with any history row found in 2021 (0.954), and birth_date agreement on shared ids (456/456).

**M6: replace open choice 8 / the fit-cost sentence** with:
> Ceiling: one month-block per stat is timed first; the full run is approved only if the projected wall time is <= 6 h local CPU; per-stage checkpoints; halt at the ceiling and report partial, never extend.

Minor (fix in the same edit):
- m1: `season_boundary_rule.py:25` comment becomes "else the bin's logdelta is 0".
- m2: in the continuity section add "about 54 unadjusted intervals, k* only".
- m3: harness `sigma_eff` uses `f_fg3m` (3PM) for the fg3m `sd_log_m2_over_m1`; switch it to `f_fg3a`.
- m4: "Bars were written in the harness before it first ran" cannot be verified. The harness and gate.json were first committed together at f0f711a 20:52, after the 20:47-20:51 run. Say "not verifiable from git".

## Power and family note
Family: 6 per season under BH (C1-C6). Descriptive and outside the family: per-stat results, the continuity slice, g buckets, A2-A0. MDE80 is about 0.9 floor units for C1. C2 is expected at -0.1 to -0.4, below its line. C5/C6 are expected at about 0, so they are underpowered by effect, not by n. A null on C2, C5 or C6 means "underpowered, cannot conclude" plus the smallest excluded effect (BEST_PRACTICES 2026-10-21), not "no effect".

## Single biggest risk if the run were made as drafted
A 2023 C1 null caused by the 2022 training rows having no A1 base. It would be recorded as "season reset does not help", it would kill C2, C5 and C6 by the kill rule, and the test could not have moved.

## Not verified
- Unit tests and ruff: the main `.venv` has no pytest or ruff. `uv run --no-sync` created an untracked `.venv` inside the worktree, which I could not remove (permission denied); remove it with `rm -rf <wt_sb>/.venv`.
- The fitted minutes grid, and the size of the 2022-base mismatch (it needs a fit).
- Whether the bubble seasons distort the premise.

## Drafted rows (not appended)
- DECISIONS: "2026-10-22 SEASON_BOUNDARY pre-freeze adversary NOT_READY: blocker B1 (no `L0` on 2022 training rows), M1-M6; G2 amendment upheld (456/456 birth dates agree); G4 3PA driver upheld under a multi-horizon fg3m-target bar, 3P%-is-noise reason withdrawn. Reverse if: B1 is shown inert by a fit-free argument."
