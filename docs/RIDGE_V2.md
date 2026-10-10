# ridge_v2 (experiment 4) -- opponent-adjusted ridge variants: design and PRE-REGISTRATION

Status: written 2026-10-08 BEFORE any ridge_v2 Colab run. The rule in "Pre-registered keep rule" is
applied by `research/eval/ridge_v2_eval.py` and is not changed after seeing results. A negative result is
reported as a negative result. Season 2025 is never loaded (not by the export, the job or the eval).

Files: `research/features/opponent_ridge_v2.py` (estimator, tuning, export), `research/colab/jobs/ridge_v2_sweep/`
(job, notebook, `job.yaml`), `research/eval/ridge_v2_eval.py` (rule), `tests/features/test_opponent_ridge_v2.py`.
Group (i) of `research/props/context_features_v2.py` is imported, not edited.

## 0. Finding that changes the question: the experiment-2 ridge column leaks

The drop-one ablation in `docs/CTXRES_V2_EXP2_RESULTS.md` attributes almost all of v2's gain to group (i)
(dropping it costs pts +0.066, reb +0.035, ast +0.010, fg3m +0.006 CRPS). Reading the implementation
(`opp_adjusted_ridge`) shows why that number cannot be taken at face value: the function filters its
OUTPUT rows with `minutes >= 5` -- tonight's minutes -- so the feature is NULL for every player who played
fewer than 5 minutes in the target game and non-null otherwise. Those rows have almost no production.

| check on the exported `ctxres_v2.parquet` (80,312 rows) | value |
|---|---|
| rows with tonight's minutes < 5 | 6,435 (8.0%) |
| ridge column NULL among those rows | **100.0%** |
| ridge column NULL among rows with minutes >= 5 | 6.1% (only Oct-Nov 2022, before 3,000 training rows exist) |
| mean pts, ridge NULL vs non-null | 0.86 vs 11.76 |
| NULL share of the ridge column over test seasons 2023-2024 | 8.5% (essentially all of it the low-minute rows) |

A tree model reads "NULL" as "this player barely played", which is post-outcome information. No other v2
column shows this pattern (null-vs-label scan of all v1/v2 columns: the next largest gap is `yrs_since_draft`,
a static draft fact). The fix in this module: the ridge is evaluated for EVERY candidate row, whatever tonight's
minutes were (`RidgeInputs` keeps the training filter `minutes >= 5` on past rows only). The exported reference
estimator reproduces the experiment-2 coefficients on the rows where both exist (median |diff| 3e-4, max 0.035 for
pts player effects, 4e-4 for opponent effects; the residual difference is sklearn's `sparse_cg` tolerance --
my solve matches sklearn's exact `cholesky` solver to 1e-14, tested).

Consequences, stated plainly:

* The experiment-2 confirmatory verdict (reb/ast/fg3m kept, pts failed) was computed with this leaky column in
  every v12 arm. Its gain over `v1_prod` is contaminated by an unknown amount. The decomposition below measures it.
* This experiment therefore has TWO references: `exp2_ref` (the leaky column exactly as exported; reported, not
  used for decisions) and **`ref_fixed`** (the same estimator without the leak; the PRIMARY reference).
* Leak decomposition (descriptive, reported by the eval): `no_ridge - exp2_ref` = what experiment 2 credited to
  group (i); `no_ridge - ref_fixed` = its honest value; `ref_fixed - exp2_ref` = the leak effect.
* Because the other v2 groups are ~0 in the ablation, the honest ridge value may be small. That is the expected
  case for a rate estimator that only re-weights past box scores, and the experiment is built to see it.

The production props model uses v1 features only and never used group (i), so it is unaffected; the v2 candidate
must not be promoted on the experiment-2 numbers.

## 1. Estimator (strictly as-of)

Per stat, once per calendar month `M`, on played rows with `minutes >= 5` dated STRICTLY BEFORE the first day of `M`
and within 540 days (same window as experiment 2), minimising

    sum_i w_i (y_i - mu - x_i beta)^2 + sum_b alpha_b ||beta_b||^2

`y` = stat per 36 minutes (pace-adjusted for r3b), `w` = minutes x decay, `mu` = weighted mean of `y`. Month-`M`
rows read the coefficients; unseen players/opponents get 0 (or the group effect under r5). Months with fewer than
3,000 training rows are NULL (an unknown-missing, only Oct-Nov 2022 = warm-up season; never in 2023/2024). The
normal equations are assembled from a sparse block design and solved by Cholesky (`solve_spd`); the Gram matrix is
shared by every (stat, penalty) pair of a design. Opponent effects are centred on the league as in experiment 2.

Hierarchy of variants (a component is a `RidgeCfg` field; all penalties are tuned on 2023 months only, section 3):

| id | field | what it adds | hypothesis |
|---|---|---|---|
| r1 | `halflife` | w *= 0.5^(age_days / hl) (age from the refit date) | roles and form drift; flat weights over 540 days over-weight stale games |
| r2 | `opp_grp` pos/arch | opponent x (position group or archetype) deviation, penalised | defences differ by who they face (guards/wings/bigs); archetype if position is too coarse |
| r3a | `venue` | global home term, opponent x venue and player x venue deviations | home/away splits exist and the opponent effect is venue-specific |
| r3b | `pace` | target divided by (game pace / window mean pace), pace = box-score possessions per team | separates volume from efficiency; opponent effects stop absorbing pace |
| r4 | `star` | shared + player-specific effect of "top-usage teammate OUT" | usage redistributes when the star sits; the GBM only sees aggregates |
| r5 | `hier` pos/arch | group mean (lightly penalised) + penalised player deviation | rookies/low-minute players inherit a sensible prior instead of 0 |
| r6 | `alpha_p`, `alpha_o` | per-stat player and opponent penalties | alpha = 50 for every stat is arbitrary |

r4 detail (the only variant that touches lineups): partner(i) = the team's top as-of usage player (second if i is
the top); usage = past `fga + 0.44 fta + tov` per game over >= 10 window games. TRAINING regressor = partner had no
played row in that game (past box score). PREDICTION regressor = partner is on the pre-tip OUT report
(`flagged`), never tonight's box score or lineup; no report means 0. I used the box-score presence of the star rather
than on-court overlap from the possessions table: at prediction time both reduce to the same report-based
indicator, and on-court overlap would only change the training target noise (not tested; stated as a limitation).
Archetypes (r2arch / r5arch): KMeans (k = 6, fixed, seed 20261008) per refit month on height, weight, position
fractions (`research.coldstart.archetypes.encode_position_fractions`) and window per-36 rates shrunk toward the league
(200 pseudo-minutes); players without static data get an "unknown" group. k was not tuned (no cheap proxy for it).

Exported components per (stat, variant): `p` player effect (+ group effect under r5), `o` opponent effect, `ox`
opponent interaction total (r2/r3a), `px` player-venue total (r3a), `tm` teammate-context contribution (r4).
Column names `rg_<8-hex cfg tag>_<comp>_<stat>`; the tag hashes the canonical config so equal estimators share
columns. No `rate` sum is exported (it would add a feature the reference lacks).

## 2. Arms (all share the fixed base models)

Base models are HELD FIXED at the experiment-2 per-stat winners with the experiment-2 tuned XGBoost parameters
(`base_config.json`, copied from run `20261008_171446` `search_v12`): pts = multi-quantile XGBoost
(`xgb_v12_quantile` recipe), reb/ast/fg3m = XGBoost `count:poisson` mean + NegBin dispersion per position group
(`xgb_v12_poisson_nb` recipe). Features of every arm = v1 + v2 groups a-h and j + the arm's ridge columns.

| arm | ridge columns |
|---|---|
| `exp2_ref` | the experiment-2 columns as exported (leaky) |
| `ref_fixed` | reference estimator, all rows (PRIMARY reference) |
| `no_ridge` | none |
| `r1`, `r6`, `r5`, `r5arch`, `r2`, `r2arch`, `r3a`, `r3b`, `r4` | reference + exactly one component (penalty tuned in the reference context) |
| `L1` | r1 + r6 jointly tuned |
| `L2`, `L3`, `L4` | L1 + r5; + r2; + r3a + r3b (cumulative ladder; penalties tuned in the L1 context) |
| `ALL` | L4 + r4 |
| `drop_r1`, `drop_r6`, `drop_r5`, `drop_r2`, `drop_r3a`, `drop_r3b`, `drop_r4` | ALL with one component reverted to its reference setting (`drop_r4` is identical to `L4` and is not re-run; the eval maps it) |
| `proxy_sel` | L1 + each of r5, r2, r3a, r4 (in that order) whose 2023 ridge-level proxy gain in the L1 context is >= 0.2% (r3b has no proxy) |

21 new arms plus the three references (24 trainings); the combination arms are fixed by this table (not chosen after seeing
any 2024 result); the choice among them is made on 2023 CRPS (section 4).

## 3. Tuning record (2023 months only; recorded BEFORE any Colab result)

The ridge-level proxy is the minutes-weighted MSE of the ridge's own per-36 prediction `mu + terms` on rows of the
2023 season with >= 5 minutes (months Oct 2023 - Jun 2024), using only coefficients fit before each month. No 2024
(report season) target is read by the tuner (`selection_months(inp, 2023)`). It is a proxy: a better rate predictor
is not guaranteed to be a better GBM feature, which is why the arms are then compared on 2023 CRPS in the job.
Grids: half-life {None,30,60,120,240,480} days; alpha_p {2,5,10,25,50,100,200,400}; alpha_o {10,50,250,1000,4000,16000};
interaction penalties {50,200,800}; group-mean penalty {1,5,25}. The first tuning pass (narrower grids) put the
optimum at grid edges, so the grids were widened once before this record (a 2023-only decision); r6's alpha_o still
sits at the upper edge for pts/reb/fg3m (opponent effects are shrunk to ~0: opponent adjustment carries little
rate signal at this granularity, consistent with the repo's earlier finding).

Selected values (full table with all grid points in `data/colab/ridge_v2/ridge_v2_tuning.json`) and proxy MSE change
vs the reference (negative = better):

| stat | r1 half-life | r1 | r6 (alpha_p, alpha_o) | r6 | L1 (hl, alpha_p, alpha_o) | L1 |
|---|---|---|---|---|---|---|
| pts | 60 | -4.0% | 25, 16000 | -6.5% | 120, 10, 4000 | -8.2% |
| reb | 240 | -0.5% | 50, 16000 | -0.1% | 240, 25, 4000 | -0.6% |
| ast | 120 | -1.6% | 50, 1000 | -0.3% | 120, 25, 1000 | -2.4% |
| fg3m | 240 | -0.7% | 50, 16000 | -0.5% | 120, 25, 4000 | -1.3% |

Component proxy gain (positive = better) in the reference / L1 context:

| stat | r5 pos | r5 arch | r2 pos | r2 arch | r3a | r4 |
|---|---|---|---|---|---|---|
| pts | +6.9% / +0.4% | +6.9% / +0.4% | +2.7% / +0.2% | +4.8% / +0.4% | +3.4% / 0.0% | -1.3% / +0.3% |
| reb | +0.2% / +0.1% | +0.4% / +0.3% | -0.2% / +0.2% | 0.0% / 0.0% | -0.3% / -0.2% | -0.1% / -0.1% |
| ast | +0.3% / +0.1% | +0.2% / +0.1% | -0.3% / 0.0% | +0.3% / 0.0% | -0.4% / -0.2% | 0.0% / +0.5% |
| fg3m | +0.6% / 0.0% | +0.6% / +0.1% | -0.5% / -0.1% | +0.2% / +0.1% | -0.4% / -0.2% | -0.1% / 0.0% |

Reading, before any model result: the large proxy gains are in pts and mostly come from weaker player shrinkage
and from switching opponent effects off; rebounds barely move. `proxy_sel` is mechanical (L1-context gain >= 0.2%):
pts = L1 + r5 + r4, reb = L1, ast = L1 + r4, fg3m = L1.

## 4. Protocol and PRE-REGISTERED KEEP RULE

* Walk-forward: every calendar-month block is predicted by models fit on rows dated strictly before the block
  (newest 15% of the window = calibration window for the count models; early-stopping tail for XGBoost), same
  blocks as experiment 2. Test blocks = months of seasons 2023 and 2024.
* **Selection uses season 2023 only**: for each stat, candidate = the NEW arm (any arm except `exp2_ref`,
  `ref_fixed`, `no_ridge`) with the lowest 2023 CRPS for that stat (`metrics.json` `selection_2023`). Season 2024 is
  report-only and never used for a choice. The candidate may differ per stat.
* Reference = `ref_fixed`. Data = season-2024 played rows. A stat is KEPT iff ALL hold (candidate - ref_fixed):
  1. mean CRPS delta <= -0.005;
  2. game-clustered 95% bootstrap CI upper bound < 0 (2000 resamples);
  3. clustered two-sided p survives Benjamini-Hochberg across the 4 stats (m = 4), q = 0.05;
  4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games, starter vs bench;
     n >= 300) has a CRPS delta > +0.01;
  5. mean bias within +/-0.5;
  6. 80% interval coverage within 0.75-0.85 under Amendment A1.1 (`pit_coverage80`). pts uses a quantile grid;
     reb/ast/fg3m use NegBin `ppf` integer grids, for which A1.1 documents about +/-0.035 estimator uncertainty
     (it cannot decide a borderline case by itself).
  and the run is complete with a leak audit (below).
* Overall "kept" = all four stats kept. A passing stat is only a CONFIRMATORY CANDIDATE for one later 2025 touch
  under a separate pre-registration (not now), and is never a production change by itself.
* Confirmatory family: exactly the 4 candidate-vs-`ref_fixed` cells (BH m = 4). EVERYTHING else is DESCRIPTIVE:
  the leaderboard of all arms vs `ref_fixed` (bootstrap CIs are not multiplicity-adjusted and carry no verdict), the
  ladder, the drop-one ablation (`drop_X - ALL` > 0 means X helps), the leak decomposition, comparisons with
  `exp2_ref` and `no_ridge`.
* Leak audit (run before any model is fit, `null_label_audit`): static allow-list (v1/v2 names or an `rg_` column
  matching the pattern, none matching the deny regex); for every ridge column of every arm except `exp2_ref`, if
  >= 0.5% of rows are NULL the standardised label gap between NULL and non-null rows must be < 0.35 sd, and no
  column may rank-correlate >= 0.95 with its same-game label; violation raises. `exp2_ref` is exempt and its gap
  is logged (the documented leak). The temporal tests (planted future rows, tonight's own row, DNP/cameo rows,
  report-only star regressor) are in `tests/features/test_opponent_ridge_v2.py`.
* Seeds: `SEED = 20261008` (XGBoost, bootstrap, KMeans); GPU XGBoost is not bit-reproducible, so a re-run differs in
  the 3rd-4th decimal. `ref_fixed` and `exp2_ref` are re-run inside the job. Comparisons are paired by row
  (`game_id`, `player_id`) even when arms come from different concurrent sessions (`NBA_SHARD` puts the three
  references in shard 0 only); the session-to-session GPU noise is far below the 0.005 floor, and an unsharded run
  removes the question.

Expected cost: about 6 min per arm on a T4 (estimate from experiment-2 timings: the pts quantile fit about 5 min,
the three count stats about 1.5 min each, run in parallel threads) x 24 arms = about 2.4 h in one session (about 35-40 min with 4 concurrent sessions).

## 5. Honest disclosure of what was seen

* The experiment-2 2024 results (including the ridge ablation and the `xgb_v12_poisson_nb` / `xgb_v12_quantile`
  numbers) were known when this was written; `exp2_ref` is expected to reproduce them up to GPU nondeterminism and
  that reproduction is a sanity check, not a result.
* The leak was found by an audit of null patterns of the exported parquet over all seasons (a table of label means
  for NULL vs non-null rows, which includes 2024 rows). No ridge-variant CRPS on any 2024 row existed before the
  run. The ridge-level tuner reads 2023 months only.
* The local CPU smoke test (2% of games, `NBA_BUDGET=smoke`) printed only 2023 CRPS ratios and timings; its
  2024 values were not looked at.

## 6. Data card (all ridge families)

| item | statement |
|---|---|
| source columns | played-row `pts/reb/ast/fg3m`, `minutes`, `team_id`, `opp_id`, `is_home_f`, `pos_code`, past box-score `fga/fta/tov/oreb` (usage, pace), `players_static` height/weight/position, pre-tip OUT report set |
| as-of window | rows dated strictly before the first day of the refit month, 540 days; r1 decays by days since the refit date |
| imputation | NULL (not filled) when the training window has < 3,000 rows (Oct-Nov 2022 only); unseen player 0 (reference) or group effect (r5); unseen opponent 0; r4 regressor 0 without report / partner |
| transform | per-36 rate, optional pace division, weighted ridge, opponent effects centred; float32 in the parquet |
| missingness | 5.89% of exported rows per column, all in the warm-up season (0% in 2023, 0% in 2024) |
| rationale | opponent-adjusted, shrunk rate estimates are the only v2 group that moved CRPS; the variants ask whether better shrinkage, recency, context or hierarchy move it further |

## 7. Commands

```
# 1. export (CPU, about 2 min; read-only DB; add --device cuda on a GPU host)
uv run python -m research.features.opponent_ridge_v2 --db nba.duckdb --out-dir data/colab/ridge_v2
# 2. stage on Drive (also refreshes ctxres_v2 inputs if stale), open the printed notebook, Runtime > GPU, Run all
make colab-push JOB=ridge_v2_sweep
#    concurrent sessions: push several times; in each notebook's setup cell add one line, e.g.
#    os.environ["NBA_SHARD"] = "0/4"  (then "1/4", "2/4", "3/4")  or  os.environ["NBA_ARMS"] = "ref_fixed,r1,r6"
make colab-status JOB=ridge_v2_sweep
make colab-pull JOB=ridge_v2_sweep RUN=<run_id>        # once per pushed session
# 3. apply the pre-registered rule (merges sharded runs by arm)
uv run python -m research.eval.ridge_v2_eval --run data/colab/runs/ridge_v2_sweep/<id1> [<id2> ...] \
    --out reports/ridge_v2_verdict.json --md reports/ridge_v2_report.md
```

A crash is resumed by re-running the same notebook (finished arms log `RESUMED`). Local CPU smoke (needs xgboost,
not a project dependency): `uv run --with xgboost` with `NBA_BUDGET=smoke NBA_PARQUET=<staged parquet>
NBA_ARTIFACT_ROOT=<dir> NBA_MAX_ARMS=3`; `NBA_CRASH_AFTER_ARM=<arm>` forces a kernel-death exit (137) to test resume.

## 8. A/B plan and honest no-signal note

* If `ref_fixed` is about equal to `no_ridge` (likely, given every other v2 group is ~0), the experiment-2 gain was
  the leak and the v2 feature set has no demonstrated value; the variants are then judged against an honest, nearly
  empty reference, and a pass needs a real 0.005 CRPS gain.
* Per-component A/B: standalone `r*` arms vs `ref_fixed` (does the component help alone?), `L1..ALL` (does it stack?),
  `drop_*` (is it necessary inside ALL?). A component is only trusted if standalone, ladder and drop-one agree in sign.
* Expected scale: the ridge-level proxy gains are large for pts (up to -8% MSE) and small for reb/ast/fg3m
  (<= 2.4%); the GBM already sees recency means (`m5/m10/m40/m100`), so a proxy gain may not carry over. A
  documented negative (no stat clears -0.005) is a perfectly acceptable outcome of this experiment.
* Passing on 2024 -> 2025 confirmatory candidate (separate pre-registration, one touch). Not a production change.

## 9. Results (run 20261008_194547, L4, scored 2026-10-09 by `research.eval.ridge_v2_eval` as pre-registered)

**Verdict: NOT KEPT for any stat** (T098-T101). Primary reference `ref_fixed` (the v2 feature set with the
leak-free ridge). 2023-selected candidates on 2024 (n = 27,583 per stat): pts `drop_r5` -0.0004
[-0.0014, +0.0007], reb `drop_r3a` -0.0001, ast `drop_r3a` +0.0002, fg3m `L4` -0.0001; all fail floor, CI and BH.

**Leak decomposition (descriptive), CRPS, positive = first arm worse:**

| stat | exp-2 "ridge gain" (no_ridge - exp2_ref) | honest ridge gain (no_ridge - ref_fixed) | leak effect (ref_fixed - exp2_ref) |
|---|---|---|---|
| pts | +0.0684 | -0.0001 [-0.0007, +0.0005] | +0.0685 [+0.0634, +0.0737] |
| reb | +0.0430 | +0.0007 [+0.0001, +0.0014] | +0.0423 |
| ast | +0.0143 | +0.0001 | +0.0142 |
| fg3m | +0.0073 | +0.0002 | +0.0071 |

The experiment-2 ablation's "ridge carries the gain" was **entirely the leak**: the honest, as-of opponent-adjusted
ridge adds nothing measurable on top of the other v2 features (reb +0.0007 is the only CI excluding 0, below the
floor). None of the ~20 new ridge variants (rolling windows, archetype, position, drop-one, L1-L4) moves CRPS by more
than 0.002. Since exp-2's v2-vs-production margins (pts -0.030, reb -0.051, ast -0.030, fg3m -0.032) are smaller
than or comparable to the leak effect, **the v2 feature set's advantage over production is presumed to be mostly or
entirely leak until re-measured** with `ref_fixed` against `v1_prod` on identical rows (next step for the leak-fixed
experiment 3; needs its own pre-registration).

**Follow-up (DESCRIPTIVE, not pre-registered; 2024 already seen, so it can only motivate a new pre-registration):**
`ref_fixed` (leak-free v2) vs `v1_prod` (experiment-2 OOF, identical 27,583 rows per stat, identical labels),
game-clustered bootstrap (2,000 draws, seed 0):

| stat | leak-free v2 - production | leaky exp-2 ref - production |
|---|---|---|
| pts | -0.0074 [-0.0122, -0.0025] | -0.0759 [-0.0826, -0.0687] |
| reb | -0.0081 [-0.0103, -0.0058] | -0.0504 [-0.0540, -0.0465] |
| ast | -0.0164 [-0.0182, -0.0146] | -0.0306 [-0.0327, -0.0284] |
| fg3m | -0.0251 [-0.0264, -0.0239] | -0.0322 [-0.0337, -0.0307] |

Most of the pts/reb advantage was leak; ast and fg3m keep most of theirs. This motivates the leak-fixed experiment 3
(per-stat hybrid on `ridge_v2.parquet` features), which needs a NEW pre-registration with selection on 2023 and the
2024 result treated as already seen (i.e. any confirmation must come from a holdout touch or the 2026-27 forward log).
