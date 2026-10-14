# STRUCT leak audit (WOWY_REDISTRIBUTION follow-up 2), adversary, 2026-10-14

Reviewed: branch `agent/modeler-wowy` at 45771f3 (`docs/prereg/WOWY_REDISTRIBUTION.md` follow-up 2 / STRUCT and the PROP
variant, `nba/props/struct_alloc.py`, `research/eval/wowy_struct_gate.py`, `reports/prereg_wowy_redistribution/gate.json`).
Written Wed Oct 14 18:45 CDT 2026 (clock read by `date`). The attack was asked for before any rule is drafted.
Data: seasons 2020, 2021 (`data/history/nba_history.duckdb`) and 2022 (`nba.duckdb`) only. Every query is capped at
`season <= 2022` (`load_inputs(..., max_season=2022)` and an explicit `WHERE g.season <= 2022`). No 2023 or 2024 outcome
was read, season 2025 was not loaded, and every DuckDB connection was `read_only=True`. All runs were CPU-local, about 5
minutes in total.

**Verdict: WOUNDED.** No leak. STRUCT is built from pre-tip information only: the OUT set comes from the T-60 report with
real tips, the pool from earlier games only, and a planted future changes nothing earlier. Shuffles and placebos remove the
gain. But the claimed size is inflated, and part of it is not redistribution:
1. **pts: about 30% of the gate's gain is warm-up, not redistribution.** The gate trains on 2020-21 frames with warm features
   and tests on the 2022 `nba.duckdb` frame, where every production feature starts cold. STRUCT itself is warm. When the
   2022 production features are built warm on the same rows, VAC24 falls from +0.77% [0.52, 1.01] to +0.58% [0.33, 0.82]
   (3 seeds: 0.76-0.90 cold, 0.56-0.58 warm). In the fully warm 2020-to-2021 replication it is +0.14% [-0.10, 0.38], which
   does not clear zero. The USAGE version was picked on data that included the test season. The RATE version gives only
   +0.31% warm.
2. **ast holds up on size and replication but is partly a role feature.** Warm-up accounts for about 10% (+0.89 cold, +0.82%
   [0.45, 1.21] warm). The gain replicates in both 2020/2021 directions (+0.69%, +0.66%). But in those seasons, a plain
   "share of the rotation's assists" column with no OUT information carries most of it. STRUCT beyond that column is +0.20%
   [-0.14, 0.51] and +0.22% [-0.17, 0.61].

Top finding: **the gate's GBM check compares a warm feature against a cold production feature set, so its +0.77% pts
number overstates what the 2023-24 contrast can show.** By 2023 production is warm (V-vs-`vac_*` gap 0.08 pts in 2023,
0.011 in 2024). The right working number is the warm one.

Verdict: BLOCKED

(BLOCKED applies to freezing a `WOWY_STRUCT` rule on the current gate text. Drafting may proceed. B1-B4 below must be in the
draft before it is frozen. Nothing here is a leak.)

## The model and the claim
Class: gradient-boosted trees (LightGBM) on tabular features. The test is the gate's held-out GBM residual model (stat minus
`m10`). It adds one as-of column on top of the production feature set, trains on 2020-21 and scores squared error on 2022,
with a game-clustered percentile CI. The claim (prereg line 105): pts VAC24 +0.77% [0.54, 0.99], top 10% +2.94% [1.87,
4.10]; ast VAC24 +0.89%.

## Per-check table
Scripts are in the adversary scratchpad
(`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/4a3e37f1-4418-426a-8cc6-897f102142f0/scratchpad/struct/`). They
are not committed: adversary attack code stays out of `nba/`. All were run as
`cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -W ignore <scratch>/struct/<script>`, each exit 0:
`build.py` (cached frames, 14 s), `gbm.py pts,ast` (204 s), `gbm2.py` (27 s), `audit.py` (4 s), `plant.py` (9 s),
`plant_diag.py`. CIs are paired 2000-draw percentile bootstraps clustered by `game_id`, seed 0, on relative squared-error
gain (positive = the column helps). Unless stated otherwise, n is VAC24 on 2022: 14,898 rows, 1,038 games. D0 = the gate's
design (train hist 2020-21, test cold 2022). D1 = the same rows and slices, with the 2022 production frame built warm
(history + 2022 in one `build_features`). D2a / D2b = 2020 to 2021 and 2021 to 2020, both from the warm history frame.

| # | check | what was run | n | result (VAC24 unless stated) | pass |
|---|---|---|---|---|---|
| 0 | reproduce the gate | D0, `struct` arm | 14,898 / 1,038 g | pts +0.77 [0.52, 1.01], top10 +2.94 [1.79, 4.14]; ast +0.89 [0.56, 1.24]. Same point estimates as gate.json (its CI used 400 draws) | PASS (reproduces) |
| 1a | who counts as OUT | traced: `flagged_from_availability` (status "out", latest snapshot with `as_of <= real tip - 60`) -> `wowy.out_rotation_players` (as-of `min40 >= 12`, `n_after >= 5`). The box score is never read for the OUT set | 2020-22 games 1,171 / 1,323 / 1,320 | real tip on 100% of games (0 proxy fallbacks). OUT entries that actually played that night: 2 / 12,010 (2020-21), 3 / 3,831 (2022). So OUT is the report, not realised DNPs | PASS |
| 1b | is the pool filtered by tonight? | `wowy.asof_rotation_pool`: >= 5 of T's previous 10 games, mean >= 12 min, strictly earlier games | 38,187 / 16,014 pool-minus-OUT members | 8.4% (2020-21) and 8.7% (2022) of pool members did NOT play that night and stay in the denominator. The pool uses no future information | PASS |
| 1c | firing / size by realised minutes, within as-of `min10` quintiles, real vs placebo OUT set | `audit.py` | 45,307 (2020-21), 18,095 (2022) rows with someone OUT | firing ~1.00 in every bucket (ast 0.98-1.00; zero only when own as-of ast = 0, which is pre-tip). Relative size, buckets <5 / 5-10 / 10-20 / >=20 min, 2022: real 0.52 / 0.78 / 0.93 / 1.32, placebo 0.73 / 0.93 / 0.90 / 1.13. The real gradient is steeper than the placebo's. Given 2 and 5 below, this is the feature's predictive content (it moves with the minutes players get), not a firing leak | PASS (no firing asymmetry) |
| 2 | planted future on real rows | `plant.py`: from 2023-01-15 onward, every box row scrambled (stats x3+7, minutes x0.5+3, 15% turned into DNPs, every DNP made a 31-min appearance), every report status flipped; 4,279 post-tip OUT rows planted in 633 earlier games (as_of 23:59). Then OUT set, production states, pool and STRUCT were rebuilt | 13,746 early rows, 9,485 rows 30+ days later | early rows: max abs diff 3.6e-15 (struct_u_pts) to 2.8e-14 (V_min); discrete columns (`leader_out_*`, `n_out_rot`) and every early-game OUT set identical. Two UNPERTURBED builds differ by the same 1e-14 (polars group-by summation order), and so does production `vac_*`. Later rows: 89% of struct_u_pts changed, mean abs diff 7.2. Lag one extra day (states shifted +1 day): pts D0 +0.78, D1 +0.50; ast +0.93 / +0.85 (unchanged within noise) | PASS (to float precision; "bit-identical" cannot hold, see B5) |
| 3 | knockout: OUT-only (V alone) vs pool-only (the player's share, V fixed) | `gbm.py` arms `V_only`, `pool_only`, `V_plus_share` | as row 0 | pts D1: V only +0.02 [-0.14, 0.18], pool only +0.49 [0.24, 0.76], share beyond V +0.56 [0.29, 0.85]. ast D1: V -0.06, pool +0.62 [0.27, 0.99]. In D0, V only is +0.12 (seeds 0.03-0.14) because cold `vac_*` lags. **The share carries the gain; V is `vac_*`** (identical to 0.00 in every month once both are warm, `parity_by_month`) | PASS (not a V artefact) |
| 3b | is the share redistribution or just role? `share_all` = the player's as-of share of the whole pool's stat, every row, OUT ignored | `gbm2.py` | as row 0; D2a 22,441 / 1,274 g; D2b 18,228 / 1,083 g | pts: share_all adds nothing (D1 -0.13, D2a -0.14, D2b +0.05); STRUCT beyond share_all D1 +0.67 [0.41, 0.93], D2b +0.38 [0.15, 0.61], D2a +0.18 [-0.05, 0.42]. ast: share_all alone +0.43 [0.18, 0.68] (D2a), +0.29 [0.03, 0.53] (D2b); STRUCT beyond it +0.20 [-0.14, 0.51] / +0.22 [-0.17, 0.61], but +0.75 [0.40, 1.13] in D1 | pts PASS; **ast WOUNDED** (redistribution part not shown in 2020-21) |
| 4 | NULL / missingness | `audit.py` | no-report rows: 21 (2020-21, 1 game), 45 (2022, 2 games) | STRUCT is **0** on no-report rows while production `vac_*` is NULL there (`attach()` in the gate does `fill_null(0.0)` on every row). The GBM drops `has_report == 0`, so the claimed numbers are unaffected: "STRUCT = 0 where the report was missing" gives exactly the claimed run. Scored player himself on the OUT list but played: 2 / 3 rows. Zeros among OUT rows: only own as-of stat = 0 (pre-tip) | PASS for the number; B4 for the rule |
| 5 | shuffled STRUCT, within team-game and within player-season | `gbm.py` arms `shuf_teamgame`, `shuf_playerseason` (the permuted column in train and test) | as row 0 | team-game: pts D0 -0.10, D1 -0.20 [-0.36, -0.04]; ast -0.00 / +0.05. Player-season: pts **D0 +0.21 [0.03, 0.39]**, D1 -0.03; ast -0.02 / -0.09; D2 ast +0.26 / +0.30 (player-level level information). Placebo OUT set: pts **D0 +0.18 [0.02, 0.34]**, D1 -0.00; ast +0.18 / +0.09 | PASS in the warm design. In the gate's cold design two guards carry gain, which is the warm-up fingerprint |
| 5b | replication | 3 GBM seeds (D0, D1); D2a / D2b; drop the best month; RATE vs USAGE version | see cells | pts struct by seed D0 0.77 / 0.76 / 0.90, D1 0.58 / 0.57 / 0.56. D2a **+0.14 [-0.10, 0.38]** (drop best month +0.04), D2b +0.48 [0.25, 0.71]. Drop best month D1 +0.52 [0.25, 0.80]. RATE version D1 +0.31 [0.12, 0.51], D2a -0.02, D2b +0.16 [-0.06, 0.37]. ast by seed D0 0.89 / 0.80 / 0.92, D1 0.82 / 0.68 / 0.85; D2a +0.69 [0.35, 1.05], D2b +0.66 [0.31, 1.03]; drop best month D1 +0.67 [0.31, 1.03] | pts **WOUNDED** (sign holds, does not clear zero in 2021, version-dependent); ast PASS |
| 6 | warm-up | D0 vs D1 on identical rows and the gate's slices | as row 0 | pts VAC24 mean of 3 seeds 0.81 -> 0.57: **0.24 pp = 30% (seed range 25-38%) is warm-up**. Top10 2.94 -> 2.32 (21%). ast 0.87 -> 0.78: 10% (8-15%). Cold vs warm 2022 production frame on the same 25,110 rows: `vac_pts` differs by 3.79 on average, `vac_min` by 8.9 min, `n_out_rot` on **32.6% of rows**, `m100_pts` by 1.83. The cold OUT set misses OUT players with fewer than 5 games in `nba.duckdb` (October 2022: 108 OUT rows cold vs 1,502 warm) | WOUNDED (pts) |
| 6b | integer support | n/a: squared error of a mean, no distribution is scored. The CRPS contrast the rule would use is not run here | | | n/a |

## Inference
- **Re-derived from the per-row arrays**, not taken from gate.json: D0 reproduces the gate's point estimates to two
  decimals (pts 0.77, top10 2.94, ast 0.89, n 14,898 / 1,038 games, 1,490 top-10 rows).
- **Baseline fairness (the main finding).** The gate's arms share rows and seeds, but the base's production features in
  2022 lack the 2013-2021 history that STRUCT's states have, and the training seasons have it. That is a comparator missing
  information the candidate has (playbook question 3). The gate's guards did not catch it: its `noise` arm permutes over the
  whole frame, and its `prod` arm uses the cold `rate x vac_min`. In the cold design the player-season shuffle and the
  placebo OUT set both show positive gains. In the warm design they vanish.
- **Multiplicity.** The family actually run for STRUCT in the gate is 4 stats x 4 slices x {struct, prod, noise,
  struct_vs_prod} = 64 GBM contrasts, plus 48 for PROP: m >= 112, not the 3 reported. With BH at q = 0.05 over m = 112, the
  surviving claims in the warm design are pts VAC24 (z about 4.6, p about 4e-6), pts top10 (z about 4.3) and ast VAC24 (z
  about 4.2). pts in D2a (p about 0.26) does not survive. My own attack ran about 400 diagnostic contrasts. They are
  diagnostics, not claims, and are not added to any family.
- **Power / MDE.** VAC24 2022: se about 0.125 pp (pts) and 0.19 pp (ast), so the MDE at 80% power, two-sided 5%, is about
  0.35 pp and 0.54 pp. D2a (pts, se 0.12 pp, MDE 0.34 pp): 0.14 is under the MDE. If the true effect were the warm 0.57,
  seeing 0.14 would be a 3.5 se shortfall. So the pts effect is smaller in 2021, or a one-season training set (24,556 rows)
  cannot use it. "Underpowered, cannot conclude" is the reading for 2021 alone. It is not "no effect".
- **Bootstrap code.** `nba/truth/bootstrap.py` does not exist yet. The gate (`gbm_summary`) and this audit
  (`common.boot_rel`) each use local game-clustered percentile code. COSMETIC, but the rule's CI must come from the house
  bootstrap once it exists.
- **Squared error is not CRPS.** The prereg's conversion (CRPS gain about half the relative squared-error gain x CRPS)
  gives pts VAC24 about -0.012 from the cold number. The same arithmetic on the warm number gives about -0.009. That
  conversion is unverified. It is 3-4x the kappa-based sizing the power table uses (pts VAC24 -0.0031 / -0.0039), so the
  prereg's two estimates contradict each other (B3).

## Blockers and findings (severity-ranked; SUBSTANTIVE = would change a result, a gate or what runs)
- **B1 SUBSTANTIVE (blocker).** Prereg line 105 (the "overlap with production" bullet) reports the GBM check on a cold
  test frame. Fix: restate it on warm 2022 production features (pts VAC24 +0.58 [0.33, 0.82], top10 +2.32 [1.28, 3.38]; ast
  +0.82 [0.45, 1.21]) and the 2020/2021 replications (pts +0.14 [-0.10, 0.38] / +0.48 [0.25, 0.71]; ast +0.69 / +0.66). Add
  the sentence "about 30% of the cold pts gain was warm-up". In `research/eval/wowy_struct_gate.py::main` (the
  `gbm_arms(train_all, test_all, ...)` call), build the 2022 test frame from history + 2022 (one `build_features` over
  concatenated games, box and the merged report dicts), or drop 2022 as the GBM test season.
- **B2 SUBSTANTIVE (blocker).** Prereg line 103 picks the pts version (USAGE) by clustered t on pooled 2020-22, which
  includes the GBM test season. RATE is about half as good warm (+0.31 vs +0.58) and null in 2021 (-0.02). Fix: state that
  the pts USAGE choice used 2022, and freeze USAGE as the only pts version with that disclosure. Or re-choose on 2020-21
  only and report the 2022 GBM as held out.
- **B3 SUBSTANTIVE.** Line 107 (sizing at the kappa) and line 105 (the "-0.012" conversion) disagree by 3-4x on the same
  cell. Fix: carry the kappa sizing as the working number, label the conversion "unverified, from a cold comparison", and
  drop it from the recommendation (line 110).
- **B4 SUBSTANTIVE (rule-level, numerically tiny here).** `research/eval/wowy_struct_gate.py`, `attach()`: `j.with_columns(
  [pl.col(c).fill_null(0.0) for c in zero])` sets STRUCT to 0 on rows with no usable report (21 / 45 rows in 2020-22). That
  is the "nobody out" imputation that production's `vac_*` refuses. Fix: `pl.when(pl.col("has_report") == 1).then(
  pl.col(c).fill_null(0.0)).otherwise(None)`, the `attach_wowy` rule. Add a unit test that STRUCT is NULL when
  `has_report == 0`.
- **B5 COSMETIC.** Prereg line 104 and freeze blocker text ask for "bit-identical" earlier rows in the planted-future test.
  Polars group-by sums are order-nondeterministic (two identical builds differ by 1e-14, production `vac_*` too). Fix: the
  test asserts `max abs diff <= 1e-9` on float columns and equality on discrete ones, or sorts before every `group_by(...).
  agg(sum)` in `build_struct`.
- **B6 SUBSTANTIVE (guard design for the rule).** The guards the prereg proposes (permuted-struct arm, product arm) would
  not have caught B1. Fix: add (a) a player-season-permuted STRUCT arm and (b) a `share_all` arm (role share with no OUT
  information) to the draft rule. STRUCT must beat `share_all` by the floor, or the ast claim is "role share", not
  redistribution.
- **F1 (finding, for the main session, not this rule).** Production's 2022 warm-up season has a materially wrong OUT set.
  `n_out_rot` differs from a warm build on 32.6% of 2022 rows, `vac_min` by 8.9 min on average, and October 2022 has 108
  OUT rows against 1,502. Every 2023 walk-forward refit trains on those rows. It does not touch 2023-24 features (parity
  0.08 / 0.011), but it is a known-at-the-time question worth one line in DECISIONS: should the props frame be built over
  history + `nba.duckdb`?
- **F2 (scope).** The PROP variant (prereg lines 112-119) was folded into STRUCT as "one more variant, not a new follow-up".
  It is a fourth forking path on a family that allows one follow-up. It is not used, so no harm here, but the rule should
  list it among the paths already taken.

## The single most likely failure mode if the claim is wrong
The pts gain in 2023-24 is the D2a size (about 0.1-0.2%), not 0.77%. Part of the 2022 number was cold production features.
The rest rests on a version picked partly on 2022. The rule then fails its -0.005 CRPS floor on pts while ast passes as a
role-share effect that a `share_all` arm would match.

## Playbook check
Class: gradient-boosted trees on tabular features (LightGBM residual model; the rule would refit the production
`context_residual` heads).
1. **Information or capacity?** Information. STRUCT uses only pre-tip inputs (report OUT set, as-of states, as-of pool) and
   carries a specific allocation (share among the remaining rotation). Capacity-only controls (permuted, product, placebo)
   add nothing in the warm design. But part of the gate's apparent information was warm history the comparator lacked
   (B1).
2. **Enough data for this class?** The GBM check trains on 2 seasons (52,042 rows), adequate for trees. D2 trains on one
   season (24,556 / 27,486 rows). The playbook says a one-season fail is weak evidence for trees, so D2a's pts null is read
   as "cannot conclude".
3. **Test valid?** Real tips 100%, played rows as production, as-of inputs proven by the planted future. **Not valid as
   run in the gate**: the comparator in the test season was missing the history the candidate had. Fixed in D1. Squared
   error, not integer CRPS: the rule's own test is still to be run.
4. **Lineage and inherited guards.** Descends from T047 (usage redistribution 2A/2B, inert) and production `vac_*` (V is
   exactly `vac_*` when both are warm). Inherited guards: placebo OUT set (WOWY), as-of pool. Missing: the T459 lesson
   applies in reverse here. A player-season permutation carries player-level information, so it is a diagnostic, not a
   null. In the cold design it showed +0.21, the signature of B1.
5. **Scored against.** A GBM base on the production features by squared error of the residual. Not CRPS, not the market
   line (T477: CRPS gains have not reached the line).
Class checks: missingness beyond the as-of cause (only no-report zeros, B4), planted future (pass), does the arm only
re-express the base? (V does, the share does not, and for ast in 2020-21 the share is mostly role).

## What was not verified
- Integer-support CRPS of any arm: the rule's real metric, not run (it needs the walk-forward heads; 2023-24 outcomes are
  not to be read).
- reb and fg3m: not attacked (descriptive in the prereg).
- The top-25% and leader-out slices: not re-run. Only VAC24 and top 10% were.
- Report-lead sensitivity (T-120 instead of T-60): not run. The OUT set is production's own audited path (real tips 100%),
  shared with `vac_*`.
- The PROP variant: not attacked (not chosen).
- The gate's 2023-24 sizing that reads stored FULL-minus-A0 per-row deltas (version A, second moments of 2023-24 outcomes)
  was not examined. It is disclosed in the prereg as outside the strict "no 2023/24 outcomes" reading.

## Recommended changes (not applied by the adversary)
B1-B6 above. Two concrete additions to a `WOWY_STRUCT` draft: a `share_all` control arm for ast and pts, with a
STRUCT-minus-`share_all` guard of -0.003 on the primary slice, and a warm production frame for any 2022-trained component.
Expected pts effect for the prior: use the warm number, not the cold one.

## Drafted rows (for the main session; not appended)
TEST_LEDGER (descriptive, adversary; no 2023-25 data):
`| T4xx | 2026-10-14 | WOWY STRUCT leak audit (docs/reviews/struct_leak_audit_2026-10-14.md), seasons 2020-22 only, GBM relative squared-error gain, game-clustered 2000 draws | **WOUNDED, no leak.** OUT set = T-60 report (real tips 100%); pool keeps 8.4-8.7% of members who did not play; planted future identical to 1e-14 (same as two clean builds), later rows move (89%). Warm-up: pts VAC24 +0.77 [0.52, 1.01] cold -> +0.58 [0.33, 0.82] warm (n 14,898 / 1,038 games; about 30% warm-up), 2020->2021 +0.14 [-0.10, 0.38], 2021->2020 +0.48 [0.25, 0.71]; RATE version +0.31 warm. ast +0.89 -> +0.82 [0.45, 1.21] warm; replicates +0.69 / +0.66; but beyond a role-share column only +0.20 [-0.14, 0.51] / +0.22 [-0.17, 0.61] in 2020-21. V = vac_* when warm (knockout: the share carries the gain). | Freeze blockers B1-B6; the cold-2022 GBM number is not to be cited |`

DECISIONS (proposed, Devin's call):
`2026-10-14 | Props frame warm-up: production's 2022 rows are built from nba.duckdb alone, so 32.6% of 2022 rows have a different n_out_rot than a history-inclusive build and vac_min differs by 8.9 min on average; every 2023 refit trains on them. Option: build the props feature frame over data/history + nba.duckdb (read-only). Reverse if a replay shows no change to 2023-24 forecasts | adversary STRUCT audit`
