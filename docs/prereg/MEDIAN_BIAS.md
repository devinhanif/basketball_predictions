# MEDIAN_BIAS: does a cell-wise standardized-residual quantile map remove the low median in production props? (pre-registration)

Status: **DRAFT, revision 1, written by the modeler Thu Oct 15 2026 (gate clock, `date` output, see "Minimum-data gate": `Thu Oct 15 08:00:33 CDT 2026`). Not frozen. No candidate quantile has been scored on a real outcome; the harness has been run end to end on synthetic outcomes only. The production arm A0 has been read on 2023-24 outcomes (descriptive diagnosis and the A0 reproduction pins), so both seasons are exposed to the A0 gaps, not to the candidate.**
- **How it becomes the rule:** the main session reads it with Devin and commits `prereg(MEDIAN_BIAS): <sha256>` of everything above the line `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MEDIAN_BIAS.md | shasum -a 256`.
- **Gate script (exists):** `research/eval/median_bias_gate.py`, counts and widths (and, with `--a0-labels`, A0-only reproduction numbers); output `reports/prereg_median_bias/gate.json`.
- **Harness (exists, pinned at the freeze; see "Harness contract (pinned)"):** `research/eval/median_bias_eval.py` with `research/eval/median_bias_rule.py`. `run` refuses without the `prereg(MEDIAN_BIAS)` commit and without the `harness(MEDIAN_BIAS)` pin commit, and asserts `season <= 2024` before any read.
- **Diagnosis behind it:** `docs/research/median_bias_diagnosis_2026-10-15.md` (script `research/scratch/median_bias_diag.py`, tables `reports/median_bias_diag_2026-10-15.json`).
- **Advice or momentum:** follows advice. DECISIONS 2026-10-15 ("Draft MEDIAN_BIAS: fix A0's low median inside the model, with no market input", Claude's advice from the T494 attack, which named "does production's median bias have a model-side fix? That is an information question" as its own id). Nothing here uses a price, and the standing answer stays "keep your money until the live log proves skill".
- **Pinned inputs** (sha256 of the files the gate measured; the harness refuses on any mismatch; `GATE_SHA256` pins the A0 reproduction numbers):

```
COMPONENTS_SHA256_PTS: 1d38ac0ca7188b8c32c604169f87e7fdb47ed7ce759da1a1f0fad9bd60805269
COMPONENTS_SHA256_REB: 18846ced207326fe40bc0c5f6840ab22ce648918501cff58abb62fc4eee510bc
COMPONENTS_SHA256_AST: 024c15eedd2d090d7f38c081525aa14ab4b0a9939e0a0c05f5faddadf380f72e
COMPONENTS_SHA256_FG3M: 1ace9fbcefe6b051053059d51db23b9f4c5dc42561aa8446b074124a73703d8e
PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c
GATE_SHA256: 2a767cd49a94526f679fb51f018fd0f7f1357f9052cc70762e1623a8b334e92b
```

## 1. Question

Production's props distribution (A0, the context-residual model: recency mean + LightGBM residual + conformal standardized quantiles) puts the actual outcome above its own median more often than it should on the players who carry a prop line: P(Y > A0 continuous median) = 0.536 on DraftKings-line player-games (T494 attack check 10; reproduced here: 0.5363, n 106,999), and the P(over) at book lines is 3-4 pp low (T477, POVER_BIAS scratch). **Where does the low median come from inside the model, and does one market-free change remove it?**

## 2. Diagnosis in brief (descriptive, labels of 2023-24 seen for A0 only; full tables in the research note)

* On the whole population A0 is calibrated at its median: MIDMED gap -0.11 pp [-0.35, +0.14] (n 221,280 stat-rows). The bias is by sub-population: DraftKings-line rows +3.11 pp [+2.79, +3.46] (n 106,999), no-line rows -3.12 [-3.50, -2.73] (n 114,281). It is a **gradient** in projected minutes (min10 < 15: -3.96; 15-24: -0.77; 24-30: +1.41; 30+: +2.73) and in level (+3.0 high tertile, -3.2 low), same sign in all four stats and both seasons. No month or season trend; the played-only conditioning is not a source; the recency half-life reproduces exactly and is skew, not drift.
* **Mechanism (structural):** every quantile of A0 uses one pooled empirical distribution of standardized residuals from the newest 15% of training rows. Its shape depends on minutes (calibration-window skew of z, pts: 1.61 / 0.84 / 0.42 for min10 < 15 / 15-25 / 25+; median z -0.36 / -0.23 / -0.11 vs the pooled -0.21). The centre is right (mean z of DK-stratum outcomes within 0.05 of 0); the median offset is wrong by group.
* **What a cell map cannot fix:** inside the same minutes cell the DK-line rows sit 4-6 pp above the no-line rows. That is the book's knowledge that the player plays a normal role (minutes information). The cell-wise calibration median recovers 60-80% of the needed standardized shift in the DK stratum (pts 62%, reb 81%, ast 64%, fg3m 60%), so about 1.5 pp of the 3.1 pp is expected to remain.

## 3. The change under test (one candidate, no tuning)

**CAND (cell quantile map).** Same centre `c`, same scale `s`, same walk-forward month blocks, same calibration window and integer map `ceil(q - 0.5)` as A0. For a test row in minutes cell `k` (as-of `min10` < 15, [15, 25), >= 25, the existing `nba.props.lower_tail.MINUTES_EDGES`), the standardized-residual quantiles over the **whole** tau range (199-point grid) are those of the calibration rows of the same block that fall in cell `k`; a (block, cell) pool with fewer than 200 rows (`MIN_BIN`) keeps the pooled quantiles. Nothing is fitted; no constant is tuned (edges and MIN_BIN are inherited from LOWER_TAIL, whose `mondrian_minutes` replaced only tau < 0.5). **A0** = production's pooled map on the same components. **PLACEBO** (guard only, not a family member) = CAND with the cell labels of the test rows permuted within each block (seeded).

**Information or architecture?** It adds no new pre-tip information: `min10` is already an input of A0's mean and scale heads. It is a bias fix in how existing residuals become quantiles, and architecture-only ideas have a losing record here (CLAUDE.md). It is still worth one frozen test because: (a) the bias is measured, signed, replicated in four stats and two seasons, and has a mechanism visible in calibration rows without any test label; (b) it has no fitted capacity beyond three empirical quantile maps per block; (c) the claim is calibration of the median, not CRPS, where architecture ideas lost (and LOWER_TAIL's lower-half cell maps scored -0.0008 to -0.0034 CRPS, under the -0.005 floor, T137-T144, so CRPS is not the claim); (d) the part that is information (the book's expectation of a normal role) is explicitly left out and its expected residual is stated before the run. If the map closes the gap only in the cells it is built on, the word says so (PARTIAL).

## 4. Design

* **Data:** the walk-forward components of A0 (`reports/lower_tail/components_<stat>.npz`, pinned): per month block the test rows' `c`, `s`, as-of `min10` and the calibration arrays `cal_z`, `cal_min10` (blocks 0-8 = season 2023, 9-17 = season 2024). Played player-games with at least 5 prior played games; stats pts, reb, ast, fg3m; seasons 2023 and 2024 only (`season <= 2024` asserted; the DB's 2025 rows are never queried). DNP rows carry no label and are outside every number (the forward log voids DNP legs, the same population).
* **Metrics (integer support for every arm):** (i) **MIDMED gap**, percentage points: for the arm's own integer median `m` (the 100th of the 199 sorted integer-mapped points), `R = 1{Y > m} + 0.5 * 1{Y = m}`, `E = 0.5 * (S(m) + S(m+1))`, `S(k) = P(Y >= k)`; gap = mean(R - E), zero if calibrated at the median. (ii) **Integer CRPS**, `2 * mean pinball` on the 199 grid after `ceil(q - 0.5)`, per row, CAND minus A0. (iii) Reported, not decisive: P(Y > continuous median) with the arm's own median (the T494 statistic; A0 must reproduce 0.5363).
* **Strata:** H = cell 3 (`min10` >= 25; rows with a normal rotation role; per stat-season n 12,334-12,489). The three cells are also scored. The **DraftKings-line population** (DK; player-game has a DraftKings T-60 main line in `pairs_{2023,2024}`) is a pre-tip fact used **only to describe**; it is never a model input or a fit weight.
* **CI:** game-clustered percentile bootstrap, B = 5,000, seed 2026 + season, one resample of games per season shared by all arms and stats of that season (ratio of resampled per-game sums). Paired: |gap(CAND)| minus |gap(A0)| is computed inside each replicate. Date-clustered intervals are not computed (declared limit: games of one night share a tail of refs and rest, which game clustering does not see).
* **Select 2023 / report 2024.** There is nothing to select (no tuned constant). 2023 is the consistency season (P3); **2024 is the report season** and decides. Both seasons are exposed to A0's gaps, which is why the confirmatory test is the 2026-27 live log (section 9).
* **Multiplicity:** the primary endpoint is one pooled test (no correction needed). The per-stat words ("fixed in <stat>") are a family of 4 one-sided tests on 2024, Benjamini-Hochberg at 0.05. The CRPS no-harm checks are intersection-union (all four must pass), so need no adjustment.
* **Slices (fixed now, computed by the harness):** stat (4), minutes cell (3), season (2), DK population. Month, starter/bench and prior-games cuts are descriptive in the research note and are **not** computed by the harness.
* **Planted future / leaks:** CAND is a pure function of `(c, s, min10, cal_z, cal_min10)`; it takes no outcome (unit test: a planted outcome array changes nothing). G3 checks that no test row's exact `(c, s)` pair sits in its own block's calibration pool (unit test plants one and asserts the detector fires). `check` and `rehearse` never load `row_y`. As-of timing is inherited from A0 (`tip_source="real"` in the production config).
* **Fair baseline:** A0 on the same rows, the same blocks, the same calibration window, the same integer map, the same `min10` (the cell uses the A0 feature as-of the same tip).

## 5. Decision rule (frozen at the freeze; the harness `words()` implements exactly this)

Let H-gap be the MIDMED gap in stratum H, pooled over the four stats (n about 49,000 per season), percentage points; "reduction" = |gap(A0)| minus |gap(CAND)|.

* **P1 (calibration, 2024).** P1a: |H-gap(CAND)| <= 1.0 pp. P1b: reduction >= 0.5 pp and the one-sided paired-bootstrap p <= 0.05. P1c: no stat has |H-gap(CAND)| > 1.5 pp. P1 = all three.
* **P2 (no CRPS harm, 2024).** For each stat the upper 95% limit of CRPS(CAND) minus CRPS(A0) is <= +0.001.
* **P3 (replication, 2023).** Pooled reduction CI upper limit < 0 for |CAND| minus |A0| and at least 3 of 4 stats smaller in point.
* **P4 (the line population, secondary).** Pooled over stats, 2024, DK population: |gap(A0)| minus |gap(CAND)| >= 1.0 pp with the CI lower limit > 0.
* **P5 (no cell made worse, 2024).** In each of the 12 stat x cell gaps, |CAND| minus |A0| <= +0.5 pp.
* **Guards.** G1: 50 draws of the 2024 outcomes permuted within each (stat, block) (breaks the cell-outcome link, keeps every block marginal); the observed pooled H reduction must exceed the null mean by 3 null sd, and the observed mean CRPS change must be below the null mean by 3 null sd. G2: PLACEBO's pooled H reduction <= 25% of CAND's (not evaluated if CAND's reduction < 0.5 pp). G3: no collision (above). G4: A0 reproduces its pinned integer CRPS per stat and season to 1e-9 and the DK pooled P(Y > median) to 1e-9.
* **Words.** `MEDIAN_FIXED` = P1 and P3 and P5 and all four P2 and G1-G4. `FIXED_BUT_HARMS_CRPS` = the same with a P2 failing. `GUARD_FAIL` = P1 and P3 and P5 hold but a guard fails (a gain that cannot be trusted). `PARTIAL` = P1b holds (the gap shrinks significantly) but P1a, P1c, P3 or P5 fails. `NOT_FIXED` = otherwise (a null stays a null whatever the guards say). Flags, only with `MEDIAN_FIXED`: `ON_LINES` (P4), `IMPROVES_CRPS` (every stat's CRPS change <= -0.002 with the CI below 0; the project's -0.005 floor is not the claim here, see section 3c). Per-stat words ("fixed in pts", ...) = |stat H-gap(CAND)| <= 1.0 pp and BH p <= 0.05.
* **Kill criteria / what a null means.** P1b failing, or any P2 upper limit above +0.001, closes the idea: no variant re-tried. **The one allowed follow-up**, named now: the nine-cell map (minutes cell x tertile of the calibration-window centre `c`) under a new id, only if the word here is `PARTIAL`. A `MEDIAN_FIXED` word earns a forward shadow (CAND medians logged beside A0 in 2026-27), never a promotion; promotion is Devin's.
* **2025 touch:** none requested. The 2025 holdout stays unloaded. Claude recommends the 2026-27 live log as the confirmatory test (Devin's call).

## 6. Minimum-data gate, measured on the data the run will use

Measured by `research/eval/median_bias_gate.py` on the pinned component files (blocks and rows of the current `nba.duckdb`: components rows equal the DB's played rows with >= 5 prior games, 27,619 and 27,701). `date` output: **`Thu Oct 15 08:00:33 CDT 2026`** at the start of the final gate run, **`Thu Oct 15 08:00:34 CDT 2026`** at its end; output `reports/prereg_median_bias/gate.json` (sha256 above, deterministic across reruns). Thresholds were fixed before the run and are in the harness: stratum H rows >= 8,000 per stat-season (the SE of a mid-quantile gap is about 0.5 / sqrt(n), 0.56 pp at 8,000), smallest cell >= 4,000, games >= 1,000, DK-line rows >= 5,000, (block, cell) pools below MIN_BIN <= 10%.

| stat | season | rows | games | H rows (>= 8,000) | smallest cell (>= 4,000) | DK-line rows (>= 5,000) | pass |
|---|---|---|---|---|---|---|---|
| pts / reb / ast / fg3m | 2023 | 27,619 | 1,318 | 12,489 | 6,374 | 14,354 / 15,052 / 9,650 / 14,191 | yes |
| pts / reb / ast / fg3m | 2024 | 27,701 | 1,320 | 12,334 | 6,389 | 15,882 / 14,748 / 10,574 / 12,548 | yes |

Calibration pools: 18 blocks per stat, smallest (block, cell) pool 854 rows (MIN_BIN 200); share of pools on the pooled fallback 0.0 in every stat. DK legs (DraftKings T-60 main lines, both seasons) 106,999. All 36 checks pass (`gate_pass true`). **What the sample can resolve:** the A0 H-gap per stat-season has a 95% half-width of about 0.8 pp (pts 2023: 3.09 [2.25, 3.92]), the pooled one about 0.4 pp, so the per-stat words are weak and the pooled P1 carries the decision. Honest power note: A0's 2024 H-gap is already small in two stats (reb 1.23 [0.47, 2.08], ast 0.93 [0.20, 1.71]; 2023: 2.84 and 2.75), which is why P1b is pooled and P1c only caps each stat at 1.5 pp.

A0 reproduction pins (A0 only, outcomes read; in `gate.json: a0_pins`): integer CRPS, 2023 / 2024: pts 3.152830 / 3.187553, reb 1.292817 / 1.314247, ast 0.905903 / 0.900215, fg3m 0.569049 / 0.592213; the 2023 values equal the stored `model_rows_2023.meta.json` values to 1e-12; P(Y > A0 continuous median) on the DK population 0.536276 (n 106,999; per stat pts 0.5355, reb 0.5277, ast 0.5403, fg3m 0.5437).

## 7. Data-capture audit (non-negotiable 8)

| source | what we hold | used | gap or repair, with cost |
|---|---|---|---|
| `nba.duckdb` production | seasons 2022-2024 loaded here: games 1,320 / 1,318 / 1,320; played rows 27,737 / 28,197 / 28,236; `n_prior >= 5` rows 25,110 / 27,619 / 27,701 (components equal the last two). Season 2025 exists in the file and is never queried | components of 2023-24 | none |
| `data/history/nba_history.duckdb` | seasons 2013-2021 (played rows 24,097 in 2019, 25,053 in 2020, 28,045 in 2021; 2019-20 are short seasons) | no | **Repairable gap:** an out-of-fold 2022 season (a third replication season) needs A0 trained on history 2019-21 with injury-report features for those seasons (`availability_official`, `availability_backfill` exist). Cost about half a day of code plus one heavy-lock pass; not needed for the gate. 2022 has no A0 out-of-fold rows today (it is the warm-up window), so the research note's 2022 figures are the recency base only |
| components files | written 2026-10-11 from the DB at that time; row sets equal today's DB counts | yes (pinned) | **Version drift:** `model_rows_2024.parquet` (2026-10-10, A0 for T493-T495) differs: 27,583 rows / 1,315 games vs 27,701 / 1,320; `mean_int` differs by > 1e-6 in 57-69% of 2024 rows per stat (2023 identical). Whether current `main` production reproduces the components is **not verified**; verification is a 3-minute heavy-lock pass (section 10). If it does not, a new revision of this draft with new pins comes before the freeze |
| `pairs_{2023,2024}` | DraftKings T-60 main lines, 106,999 legs | population label for P4 only | name-resolution gaps: `market_unresolved_names` 75 keys in 2023 and 2 in 2024 (a few lines are missing from the DK population; direction unknown, effect on P4 is small) |
| other books (Pinnacle, FanDuel), T-5 snapshots | held | no | not needed: no price enters any statistic |
| tracking, lineups, T-30 information | held in part | no | the second component of the bias (the book's expectation of a role) is exactly later information; a different id |

## 8. What each choice protects against

* **MIDMED instead of "P(Y > continuous median) vs 0.5":** for integer Y the latter is not 0.5 for a calibrated forecast (A0's implied value is 0.502 on the DK rows, 0.465 on the others); the former is zero for any calibrated integer forecast (unit test), so the metric cannot create the bias it hunts.
* **Pooled primary, per-stat words by BH:** a per-stat gate cannot be passed at 0.8 pp half-width when A0's own gap is 0.9 (ast 2024); a pooled test can, and per-stat honesty is kept by the cap P1c and the BH words.
* **Stratum H is a market-free cell, not "has a DK line":** the line is a pre-tip fact that carries the minutes information A0 lacks; scoring only on it would reward using it. P4 reports it, and the draft predicts the shortfall.
* **CRPS no-harm instead of a CRPS gain floor:** a calibration fix may move CRPS by a few thousandths; the -0.005 floor would fail a correct fix and pass a lucky one.
* **PLACEBO and the within-block shuffle:** the gain must come from the minutes cell, not from re-estimating a quantile map at all.
* **Pinned components and a gate before any outcome:** the 2024 A0 changed once between 2026-10-10 and 2026-10-11 (section 7); the rule is about the pinned object.
* **No tuning, edges inherited:** the cell edges and MIN_BIN come from LOWER_TAIL; a swept grid would be variant chasing on exposed seasons.

## 9. Priors (binary events, written before any candidate outcome; NOT blind: the A0 gaps of both seasons and the calibration-window mechanism numbers are seen; the candidate has not been scored)

| event | P |
|---|---|
| word = MEDIAN_FIXED | 0.55 |
| word = PARTIAL / NOT_FIXED / FIXED_BUT_HARMS_CRPS / GUARD_FAIL | 0.25 / 0.10 / 0.05 / 0.05 |
| P1a: pooled 2024 H-gap(CAND) within 1.0 pp (expected +0.3 pp, 80% -0.6 to +1.2) | 0.80 |
| P1b: reduction >= 0.5 pp with p <= 0.05 (expected reduction 1.4 pp; A0 pooled 2024 H-gap about 1.7) | 0.90 |
| P1c: no stat above 1.5 pp | 0.85 |
| all four P2 (no CRPS harm) | 0.93 |
| IMPROVES_CRPS (all four <= -0.002, CI below 0) given MEDIAN_FIXED | 0.15 (pts <= -0.002: 0.65; expected pts change -0.0035, 80% -0.0065 to -0.0010; reb / ast / fg3m about -0.001) |
| P3 replication in 2023 | 0.92 |
| P5 no cell worse by > 0.5 pp | 0.85 |
| P4: DK-population |gap| reduction >= 1.0 pp, CI lower > 0 (expected reduction 1.4, from about 2.8 in 2024 to about 1.4) | 0.65 |
| DK-population |gap(CAND)| still >= 1.0 pp (the information part remains) | 0.65 |
| per-stat BH word "fixed": pts / fg3m / reb / ast | 0.95 / 0.80 / 0.60 / 0.50 |
| G1 / G2 / G4 pass | 0.95 / 0.97 / 0.98 |
| a gate or integrity stop at the run | 0.03 |

## 10. Harness contract (pinned)

* **Files pinned by `harness(MEDIAN_BIAS): <pin>`:** `research/eval/median_bias_eval.py`, `research/eval/median_bias_rule.py`, `research/eval/median_bias_gate.py` (self-contained: the production helpers it reimplements are equal-tested against `nba.props.lower_tail`, `nba.props.context_residual` and `nba.eval.context_residual_eval` in `tests/eval/test_median_bias_harness.py`). Pin procedure: after the `prereg(MEDIAN_BIAS)` commit and the adversary's read of the harness and the rehearsal, `.venv/bin/python -m research.eval.median_bias_rule pin`, then the printed `git add ... && git commit -m "harness(MEDIAN_BIAS): <pin>"`. `gate.json` must be committed unchanged (its sha256 is a pinned line above).
* **Commands (maintainer, from the main checkout):**
  `.venv/bin/python -W ignore -m research.eval.median_bias_eval check --data-root /Users/devin/Downloads/nba-prediction` (label-free preflight);
  `... rehearse --data-root ... --truth oracle|a0` (synthetic outcomes; output `reports/prereg_median_bias/rehearsal/<truth>/results.json`);
  the real run, with the heavy lock (sequential, about 1 GB, about 15 s measured on the rehearsal; it reads four component files, two pairs files and gate.json, opens no DuckDB):
  `until mkdir data/ops/heavy.lock 2>/dev/null; do echo "heavy.lock held; retry in 120s"; sleep 120; done; trap 'rmdir data/ops/heavy.lock' EXIT; .venv/bin/python -W ignore -m research.eval.median_bias_eval run --data-root /Users/devin/Downloads/nba-prediction --sha <64 hex of the frozen rule>`
  (output `reports/median_bias/results.json`). Dry-run counts the preflight asserts: rows 27,619 / 27,701 per stat, games 1,318 / 1,320, H rows 12,489 / 12,334, cells 6,374 / 8,756 / 12,489 and 6,389 / 8,978 / 12,334, DK legs 106,999, DK rows per stat-season as in section 6.
* **Optional A0-version check (not part of the run):** `mkdir data/ops/heavy.lock; .venv/bin/python -m nba.eval.lower_tail_eval collect --out-dir reports/median_bias/components_regen; rmdir data/ops/heavy.lock` (about 3 minutes) and compare `row_c`, `row_s` with the pinned files. A difference means a new revision with new pins before the freeze.
* **Synthetic rehearsal (done, `reports/prereg_median_bias/rehearsal/`):** truth `oracle` (outcomes drawn from the row's own cell pool): word MEDIAN_FIXED, pooled H-gap A0 2.29 pp, CAND 0.09, PLACEBO 2.07 (2024), CRPS change -0.0046 mean, ON_LINES; G1 null mean gain -1.05 pp, sd 0.03; truth `a0` (pooled pool): word NOT_FIXED, CAND worsens the H-gap (0.17 to -2.18) and G1 fails. The harness recovers a planted cell effect and refuses a null.

## 11. Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile / distribution layers, on a GBM context-residual base)

1. **Information or capacity?** Neither new: no new pre-tip information (section 3), no added capacity beyond three empirical quantile maps. The information-bearing part of the bias (the book's role expectation) is left unaddressed on purpose.
2. **Enough data?** Smallest pool 854 rows against a 200-row fallback; H n 12,334-12,489 per stat-season; the gate passes.
3. **Valid test?** Integer support for every arm, same rows, same calibration window, real tip gating inherited, planted future, within-block shuffle null (sized from 50 draws, sd 0.03 pp), placebo, A0 reproduction to 1e-9. Not valid if: the pinned components are not production's current A0 (disclosed, section 7), or the DK population label drifts (P4 is secondary).
4. **Lineage.** LOWER_TAIL `mondrian_minutes` (lower half only, judged on lower-tail CRPS, NOT PASS T137-T144), PTS_TAIL `mondrian_up` (upper tail, T130-T135 NOT PASS), LINE_CALIBRATION / ALT_RUNG_CAL (market-input maps; the T494 attack showed the gain was the book's main line). Inherited guards: integer support, walk-forward month blocks, no price in any fit, shuffle + placebo.
5. **Scored against?** Calibration of the median and integer CRPS vs A0; never a price. Passing means "A0's median is no longer biased by minutes cell", not "the model beats the book" (about 1.5 pp of the line gap should remain by the draft's own prediction).
* **Class check:** reliability at the point that matters (the median, by cell and by population), slices fixed in advance, the slope (cell medians) reported, not only the gap.

## 12. Unknowns and limits

* Whether current `main` production reproduces the pinned components (section 7).
* 2022 out-of-fold behaviour (no A0 rows); the 2024 gap in reb and ast being already small is unexplained.
* Date-clustered CIs are not computed.
* The DK population label carries 77 unresolved name keys.
* CRPS size is a guess from LOWER_TAIL; the full-range map could be larger or zero.

=== RESULTS BELOW ===
(no result exists)
