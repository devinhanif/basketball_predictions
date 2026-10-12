# Adversary review: RECENT_ROLE_FULL candidate pass on pts (T467/T468), 2026-10-12

Rule `docs/prereg/RECENT_ROLE_FULL.md`, sha256 73f3f4b0 (freeze commit 81e3f91, 03:09:29 CDT), harness
`research/eval/recent_role_full_eval.py`, results `reports/prereg_recent_role_full/results.json` (commit 5a5706b).
Seasons 2023/2024 and the run's own cached arrays only. **Season 2025 was not loaded** (every frame asserted `season <= 2024`;
the cached frame's max season is 2024). DuckDB was opened read-only, once, through the harness's own `build_frame`. No lock was
taken or removed; `data/ops/heavy.lock` was absent throughout. All attack scripts live in the session scratchpad (`rrf/s1..s6`), not under `nba/`.

## Verdict for pts: **SURVIVES** (as a 2023/24 CANDIDATE; Stage 2 is still the clean test)

The gain is real pre-tip information, the size is as reported (within seed noise), and the forking paths explain little of it.
Top finding: **lagging all five columns by an extra day costs only +0.0006 / +0.0004 (CI spans 0), and `wl_min_last` alone, a column
someone could have pre-specified, clears the floor in both seasons (-0.0065 / -0.0075).** So the gain is neither near-tip information
nor a product of column selection.

What it is not: it is not "the pieces add". FULL is about 69% of HEADS + PI, and in 2024 the refit `pi` adds nothing once the heads
carry the columns (FULL - HEADS -0.0007 [-0.0022, +0.0009]). The prereg's additive conjecture is not supported; the rule's claim (the
two components together) is.

Single most likely failure mode if the claim is wrong: **winner's curse on a 1.5-SE margin**. The point estimate sits 0.0033 above the
floor with SE about 0.0022 per season. These columns have been looked at on these same rows five times (WORKLOAD, RECENT_ROLE,
PI_RECENT_ROLE, feature significance, ROUTER_G2's gate). The true effect is plausibly nearer the pre-specifiable `wl_min_last` level
(about -0.0065 to -0.007), and there the 2025 floor pass probability is about 0.75 to 0.82. A 2025 miss would then be "real but
sub-floor", not a leak.

## Rule integrity

| check | command / evidence | result |
|---|---|---|
| rule text unchanged freeze -> result -> HEAD | `git show <c>:docs/prereg/RECENT_ROLE_FULL.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256` for 81e3f91, 5a5706b, HEAD | 73f3f4b0 all three. PASS |
| harness and dependencies unchanged since the freeze | `git diff --quiet 81e3f91 HEAD -- research/eval/recent_role_full_eval.py research/eval/pi_recent_role_eval.py research/eval/workload_eval.py research/recent_role research/workload nba/props nba/eval tests/research/recent_role_full` | exit 0; work tree clean (exit 0). PASS |
| freeze precedes every candidate artifact | `ls -la data/ops/recent_role_full_cache/` | heads/pi arm npz 03:12-03:21 > freeze 03:09:29; `gate_draft.json` 03:05 contains only control refits + T460 PI arm (disclosed). PASS |
| result commit scope | `git show --stat 5a5706b` | TEST_LEDGER.md, gate.json, results.json only. PASS |
| harness unit tests | `.venv/bin/python -m pytest -q --no-cov tests/research/recent_role_full` | 9 passed, exit 0 (with coverage on, exit 1 from the repo-wide 70% floor on a 9-test subset; not a test failure) |

## Re-derivation (do not trust)

`s2_rederive.py` rebuilt every pts arm's per-row integer CRPS from the cached heads/pi arrays through `RF.arm_qi` -> `PR.crps_int`:
FULL-A0, HEADS-A0, PI-A0 and FULL-PL_FULL equal `results.json` to 0.0 in both seasons. Per-row arrays saved to scratch `rrf/pts_scores.npz`.

| contrast (pts) | 2023 (n 27,619; 1,318 games) | 2024 (n 27,701; 1,320 games) |
|---|---|---|
| FULL - A0, game-clustered, seed 0 | -0.00835 [-0.01241, -0.00422] | -0.00831 [-0.01275, -0.00408] |
| same, game-clustered, seed 7 | [-0.01227, -0.00462] | [-0.01222, -0.00431] |
| same, date-clustered (207 / 213 dates) | [-0.01260, -0.00401] | [-0.01218, -0.00445] |
| same, player-clustered (551 players) | [-0.01307, -0.00408] | [-0.01226, -0.00428] |

The CI does not depend on the clustering unit.

## The six checks and the brief's six questions

| # | check | command (scratch `rrf/`) | n | number, CI | pass |
|---|---|---|---|---|---|
| 1a | Shuffle non-inert in the HEADS component | `s2_rederive.py`: SHUF_CTRL heads + stored pi vs A0 | 27,619 / 27,701 | +0.0833 [+0.0717, +0.0948] / +0.0986 [+0.0868, +0.1104] | PASS (heads really fit on permuted residuals; `ContextResidualModel.fit` reads `train["resid"]`, the permuted column; label hashes asserted equal across arms) |
| 1b | Shuffle: columns add nothing under permuted labels, heads only | SHUF_R heads - SHUF_CTRL heads, stored pi | same | -0.0001 [-0.0024, +0.0022] / -0.0004 [-0.0022, +0.0014] | PASS |
| 1c | Shuffle, both components (rule's guard) | results.json | same | +0.0019 [-0.0006, +0.0044] / -0.0004 [-0.0024, +0.0014] | PASS |
| 2a | Planted future on the real column builder | `s5b_plant.py`: rows dated D, D+1, D+3, D+30 with 48 min injected into `played` for 569 2024 targets (one per player) | 569 targets | max abs change in the 5 columns 0.0 | PASS |
| 2b | Planted-future control | same, plant at D-1 | 569 | min_last changed 100%, min_3d changed 100% | PASS (non-inert) |
| 2c | Heads walk as-of (harness gate) | `gate.json` planted_heads | 10,064 early / 12,790 late rows | 0.0 / max 97.4 | PASS |
| 2d | Extra-day lag of all 5 columns (heads + pi refit) | `s1_build.py` (targets' date - 1 day), `s4_walk.py LAG`, `s6_score.py` | same | LAG FULL - A0 -0.0077 [-0.0116, -0.0038] / -0.0079 [-0.0119, -0.0041]; LAG - FULL +0.0006 [-0.0024, +0.0037] / +0.0004 [-0.0020, +0.0028] | PASS (93% / 95% of the gain survives a one-day lag) |
| 2e | Date / tip consistency | `s5_plants.py` | 3,958 games, 84,170 played rows | game_date != ET tip date: 0; player-dates with > 1 row: 0; tip-order inversions: 0; min gap previous tip -> this tip 22 h | PASS |
| 2f | Frame columns equal an independent recompute | `s5_plants.py` | 84,170 | max abs diff 0.0 | PASS |
| 3 | Missingness by outcome group | `s5_plants.py`, rows 2023-24 n_prior >= 5 | 55,320 | null rate of all 5 columns 0.0 in min<5 vs not, pts above/below median (gate: 0.0 pp by minutes bucket). Value asymmetries exist (min_3d == 0: 48% of min<5 rows vs 15%) but are computed from strictly earlier dates (2a) and are knowable pre-tip. P(wl_min_last == own minutes) 0.00119 vs P(lag-2 == own minutes) 0.00111: no echo of the target | PASS |
| 4 | Knockout / where the gain sits | results.json + `s3_conc.py` | slices below | HEADS -0.0062 / -0.0076; PI -0.0059 / -0.0046; FULL - HEADS -0.0022 [-0.0037, -0.0006] / -0.0007 [-0.0022, +0.0009]; FULL - PI -0.0024 [-0.0058, +0.0008] / -0.0037 [-0.0070, -0.0004]. No single component carries it. Columns read line by line: `research/workload/asof.py:187-257` (prior = `date < D`, `minutes > 0`) | PASS |
| 5a | Seed 1 (LightGBM, both components, own seed-1 control) | `s4_walk.py SEED1_CTRL / SEED1_R` | same | FULL - CTRL -0.0076 [-0.0115, -0.0037] / -0.0091 [-0.0133, -0.0050]; control seed noise +0.0005 / -0.0004 | PASS |
| 5b | Refit cadence: heads every 2 months (stored pi) | `s4_walk.py CAD2_CTRL / CAD2_R` | same | HEADS - CTRL -0.0066 [-0.0101, -0.0031] / -0.0061 [-0.0094, -0.0029] (monthly: -0.0062 / -0.0076) | PASS |
| 5c | Season halves (by date) | `s3_conc.py` | ~13,700 each | 2023 H1 -0.0078 [-0.0133, -0.0022], H2 -0.0089 [-0.0142, -0.0034]; 2024 H1 -0.0090 [-0.0146, -0.0033], H2 -0.0076 [-0.0135, -0.0017] | PASS |
| 5d | Drop best month / best team / top-10 players | `s3_conc.py` | 23,097 / 23,051 etc. | month: -0.0072 [-0.0114, -0.0032] / -0.0074 [-0.0119, -0.0028]; team: -0.0071 / -0.0073 (CIs exclude 0); top-10 players: -0.0054 [-0.0095, -0.0013] / -0.0058 [-0.0102, -0.0015] | PASS (sign holds everywhere) |
| 6 | Integer support | code: every arm goes through `WE.mixture_qi` -> `to_integer_support` -> `crps_from_quantiles` | all | same support for every arm | PASS |

### Brief questions

1. **Leakage in the HEADS path:** none found. The heads read the same five columns as `pi`. The builder uses only played games
   dated strictly before the target (2a, 2b). The dates agree with real ET tips for all 3,958 games. The one-day lag removes almost
   nothing (2d), so no information sits close to tip. A DNP is skipped by `min_last`/`l3`/`l5` and shows up as zero minutes in the
   3/7-day windows, which is knowable pre-tip.
2. **The guards as implemented:**
   - The shuffle is non-inert in the heads (1a) and shares one permutation across arms (hash check, unit test).
   - The placebo is past-only: `research/recent_role/placebo.py` draws from a strictly earlier date in the same player-season; NaN for
     the first row, 2.2% / 1.6%. Its NaNs can act as a flag, which favours the placebo, so the guard is conservative for FULL.
   - Slices are as declared: min<5 is realised minutes, bench is as-of `starter10 < 0.5`, playoffs is prefix `004` (play-in `005`
     counts as regular season; minor), cold start is `n_prior < 10`. All slices are at or above 300 rows. The worst guard slice is
     2024 playoffs, -0.0044 [-0.0197, +0.0111], which is not worse than A0.
   - Coverage: the largest worsening is 2024 tau 0.20 (0.2144 -> 0.2168, +0.0024 < 0.01).
   - pi ECE delta is +0.0003 / -0.0002.
3. **Concentration:**
   - No team holds more than 17% of the gain (23 of 30 teams negative), and the top 10 players hold 37% / 32%. Both halves of both
     seasons are negative.
   - **Two real concentrations, both explained and neither a failure:**
     - (a) Realised minutes < 5 rows (8.6% of rows) contribute -0.0047 / -0.0045 of the -0.0083. On minutes >= 5 the gain is
       -0.0040 [-0.0081, +0.0002] / -0.0041 [-0.0085, -0.0000].
     - (b) October and November carry 41% / 45% of the gain. This fits the mechanism: production's exponentially weighted `min5`
       gives the last game about 13% weight, and roles change most early in the season.
   - Playoffs were -0.031 in 2023 and -0.004 in 2024, which is unstable.
4. **Robustness:**
   - Seed 1 replicates (5a).
   - A 2-month refit cadence keeps the heads gain (5b). Cadence was tested for the heads only; `pi` cadence was not re-tested here.
5. **Additive with the holdout-passed P(play)?** There is no double use in this score:
   - The props CRPS is on played rows (`feats` = played rows; `stat_frame` keeps `n_prior >= 5`).
   - No P(play) output or feature enters the heads or `pi` (`COMMON_FEATURES` + stat columns, checked).
   - P(play) models a disjoint event (play vs DNP); the heads and `pi` model the distribution given play. Using the same columns in
     both is not double counting.
   - **Not verified:** the unconditional forecast (P(play) refit × FULL conditional) has never been scored jointly, so additivity in
     the unconditional metric is an assumption.
6. **Forking paths:**
   - The pre-specifiable `wl_min_last` alone gives FULL -0.0065 [-0.0102, -0.0026] / -0.0075 [-0.0114, -0.0035], above the floor in
     both seasons. The other four columns add -0.0019 [-0.0051, +0.0015] / -0.0008 [-0.0035, +0.0017], not significant.
   - Selection therefore inflates the point estimate by roughly 0.001 to 0.002, not the existence of the gain.

## Multiplicity and power

- **Declared family:** m = 4 stats per season. pts p_BH 0.001 / 0.0007 (the bootstrap p is floored at 0.0005 by 2000 draws; the normal
  approximation gives z 3.76, p 1.7e-4 for 2024).
- **Family actually run on these rows:** these five columns were tested on 2023/24 in WORKLOAD T452, RECENT_ROLE, PI_RECENT_ROLE T460,
  the feature-significance table, ROUTER_G2 (gate) and this rule. That is about 6 rules × 4 stats × 2 seasons, i.e. m ≈ 48 at the
  outside. pts 2024 then has BH-adjusted p ≤ 48 × 1.7e-4 ≈ 0.008, so **pts survives**. reb/ast/fg3m pass nothing under either family.
- **Power:** MDE (half CI width) 0.0041 / 0.0043. If the true effect is -0.0083, -0.0070, -0.0065 or -0.0055, the probability that a
  2025 run clears the floor is 0.93, 0.82, 0.75 or 0.59 (SE 0.0022, normal approximation). A 2025 miss at a true -0.0055 is
  "underpowered against the floor", not "no effect".

## Code findings (severity-ranked)

- **minor:** the CIs come from a private `_ci` in `research/eval/workload_eval.py:336`, not a shared truth module
  (`nba/truth/bootstrap.py` does not exist; `nba/truth/` holds `metrics.py` and `walkforward.py`). The method is correct: a
  multinomial game-clustered percentile bootstrap, seeded, 2000 draws.
- **minor:** `gate --allow-draft` writes `data/ops/recent_role_full_cache/pi_R.npz` (line 628) before the freeze, and `run_arms` reuses
  any existing checkpoint. That was harmless here: it is the T460 arm, and the frozen gate rewrote it at 03:12. But a stale pre-freeze
  checkpoint of a candidate arm would be reused silently. Recommend `run_arms` refuse a checkpoint older than the freeze commit.
- **minor:** the playoffs slice is prefix `004` only; play-in `005` falls into "regular".
- **minor:** `planted_heads` plants pts only. pts is the only claim, so this is acceptable.
- No blocker or major found.

## What was not verified

- `pi` refit cadence (heads only).
- Live-path latency of the box scores feeding the five columns at T-60. The lag attack bounds the cost of a one-day-late ingest at
  about +0.0005.
- The unconditional (P(play) × props) joint score.
- The decision-score (DraftKings) secondary.
- reb/ast/fg3m: not attacked; they did not pass.

## What a Stage-2 2025 touch should require (recommendations; the main session and Devin decide)

1. The committed `HOLDOUT_ACCESS_LOG.md` row as the rule states (RECENT_ROLE_FULL, 73f3f4b0, PRE-REGISTERED, DEVIN CONFIRMED), logged
   before the run. One run, pts only (the only carried stat; BH over m = 1), seed 0, no reruns.
2. The 2025 loader commit should be reviewed by the adversary before it runs, and should change nothing but the loader. Its own gate,
   run on the 2024 blocks before any 2025 row is fitted, must reproduce:
   - the stored A0 heads and `pi` to 0.0;
   - the stored-mixture `w_q` to 1e-6;
   - T467's 2024 FULL - A0 = -0.008314.
   Run this through the same code path as the 2025 walk, so "A0 refit live on 2025" is the same construction that was compared here.
3. `w_q` for 2025 must come from the production function under the season cap, be identical for A0 and FULL, and never be read from
   any stored 2025 file.
4. State the expected value in advance: about -0.007 (shrunk from -0.0083 for selection), with pass probability about 0.75 to 0.82. A
   miss is then read as sub-floor, not as a broken pipeline.
5. Do NOT add the `wl_min_last`-only or HEADS-only arms as 2025 scored arms. Each would be a separate model touch. Leave them
   descriptive on 2023/24 (this review).
6. A 2025 pass is a production-change proposal only. Before promotion, the live log should show the joint unconditional forecast
   (P(play) refit + FULL), since that is what production would serve and it has never been scored.

## Drafted ledger rows (not appended; the main session appends)

```
| T469 | Adversary review of T467/T468 RECENT_ROLE_FULL (docs/reviews/recent_role_full_review_2026-10-12.md) | SURVIVES (pts, candidate). Re-derived bit-exact; date- and player-clustered CIs agree. Shuffle non-inert in the heads (+0.083 / +0.099 vs A0), columns add -0.0001 / -0.0004 under permuted labels. Planted rows dated D..D+30 change 0.0 (control 100%). +1-day lag keeps 93% / 95% (-0.0077 / -0.0079). Seed 1 -0.0076 / -0.0091. 2-month heads cadence -0.0066 / -0.0061. wl_min_last alone -0.0065 [-0.0102, -0.0026] / -0.0075 [-0.0114, -0.0035]. Drop best month/team/top-10 players: sign and CI hold. Not additive: FULL ~69% of HEADS + PI; FULL - HEADS 2024 -0.0007 [-0.0022, +0.0009]. 55% of the gain from realised minutes<5 rows, 41-45% from Oct-Nov. n 27,619 / 27,701 |
```
