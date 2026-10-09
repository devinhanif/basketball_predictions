# Routing table: which model for which target and context

Status: infrastructure built and run on 2023-24 evidence only. NOTHING in this file has
touched season 2025. The 2025-26 and 2026-27 plans below are fixed BEFORE any 2025 number
exists and may be amended only by a dated addendum.

Seasons are start years: `2023` = 2023-24, `2024` = 2024-25, `2025` = 2025-26 (frozen holdout,
see `HOLDOUT_ACCESS_LOG.md`), `2026` = 2026-27 (forward season).

## What exists

| piece | where |
|---|---|
| route spec (versioned JSON) + validation + registry I/O | `nba/registry/routing.py` |
| `python -m nba.registry route show / build / validate / promote` | `nba/registry/__main__.py` |
| OOF store loaders for existing artifacts | `nba/stack/artifacts.py`, `nba/stack/populate.py artifacts` |
| frozen gate (JSON, no pickles) | `nba/stack/frozen.py` |
| line-level (threshold) routing | `nba/stack/threshold.py` |
| per-cell evidence + guardrails | `nba/stack/cells.py` |
| build orchestration | `nba/stack/routebuild.py` |
| one-shot holdout evaluator (NOT run) | `nba/stack/holdout.py` |
| daily application (opt-in; default unchanged) | `nba/daily/predict.py` (`load_active_routes`, `apply_active_route`, `context_prop_predictions_routed`) |

A route spec is stored at `registry_store/route_<target>/<version>/route.json` and as one
`experiments` row (`model_name = route_<target>`, `tags.kind = "route"`, `tags.route_hash`).
`route build` writes DRAFTS to `registry_store/routes_draft/` and does not touch the DB;
`route build --register` logs them as `candidate` versions (a WRITE; maintainer only).
Promotion is separate and manual (`route promote`); a `--scenario` run can never be promoted.
`route_hash` is the identity of what a route does (kind, champion, candidate ids, frozen gate
numbers, fit seasons, holdout season); evidence and cells do not change it.

## Targets and candidates

Targets: `win`; props `pts reb ast fg3m` (scored by 19-level grid CRPS); line-level
`thr_pts thr_reb thr_ast thr_fg3m` (P(stat >= N) events, scored by log loss); `joint_parlay`
(champion-only: no per-candidate OOF exists, so no router can be fit).

Candidate sets (the OOF contract is `nba/stack/oof.py`; 19 quantiles at 0.05..0.95):

* `full` (router fit on 2023 -> 2024): props `context_residual`, `recency`; win `injury_elo`, `mov_elo`.
* `ext` (2024 only, router fit inside 2024): props `full` + `seq_props` + `ctxres_v2`
  (= exp-2 candidate `xgb_v12_poisson_nb`). Other exp-2 arms store no q19 grid or were selected
  after viewing 2024, and are deliberately excluded.
* New candidates (for example the experiment-3 hybrid) are added with
  `route build --candidates-file extra.json` (a new set name, `fit_seasons`, candidate
  fields); each new set produces NEW specs with new hashes, each needing its own
  pre-registration row before its holdout touch.

Holdout status per candidate (cross-checked against the ledger by `route validate`):

| candidate | registry | status | basis |
|---|---|---|---|
| context_residual | props_context_residual v1 (production) | clean-confirmed | ledger: confirmed all 4 stats vs recency_conformal |
| recency | none (daily comparison `props_recency_v1`) | repeated-use | comparator inside the context_residual touch |
| seq_props | seq_props v1 (candidate) | untouched | code refuses 2025 |
| ctxres_v2 (xgb_v12_poisson_nb) | none | untouched | exp-2 verdict is a 2024 result |
| injury_elo | rung0_injury_elo v1 (production) | clean-confirmed | ledger: CONFIRMED |
| mov_elo | rung0_mov_elo v1 (production) | repeated-use | comparator inside the injury_elo touch |

(`rung4_stepheads` v1-v3 are registered but have no OOF grids, so they are not routable.)

## Router and context features

Gate: softmax-linear over candidates (`nba.stack.router.SoftmaxGate`, l2 = 0.01), trained on the
pooled score (grid CRPS for props, log loss on the logit pool for binary targets). One gate per
spec, fit once on ALL fit-window rows and frozen into JSON (`FrozenGate`). A LightGBM gate is run
walk-forward on the props CRPS targets as a second opinion and is never the frozen route.
Smooth gates only; there are no hard cells in a route.

Gate features (all as-of; computed by the same code for training rows and forward rows):
`log_career`, `games_played_season`, `min_avg10`, `min_trend` (last-3 minus last-10 minutes),
`start_rate10`, `cold_start_bucket`, plus `archetype` and `pos_group` when built with
`--archetypes`. Archetype: `nba/coldstart/archetypes.py`, k = 5 (fixed, not tuned), KMeans fit
ONCE on the feature frame as of 2023-10-01 and frozen; each season is scored by the frozen model on
features as of its own `{season}-10-01`. Position group: argmax of the guard/forward/center
fractions of `players_static.position`. Win: `abs_logit`, `logit_gap` (injury minus MOV logit),
`team_game_no`. Line level adds `line_z`, `line_n`, `line_region`.

Teammates-OUT (`has_report`, `n_rep_out` from the seq_props meta) is a SLICE and a cell dimension
but not a gate feature in v1, because its forward definition lives in the report pipeline and is
not yet computed by the same code. It is a candidate gate feature for v2. The actual `starter`
flag is never a gate feature (as-of `start_rate10 >= 0.5` is the slice label).

Line level (`thr_<stat>`): ladder = `nba.props.config.THRESHOLDS` (pts 10..30, reb 2..10, ast
2..8, fg3m 1..4). P(Y >= N) is read from the 19-level grid by linear CDF interpolation clamped to
[0.025, 0.975]; absolute log losses are therefore NOT comparable to the exact-family `tll` in the
exp-2 report (same ordering of candidates, different level). A line is kept when the equal-weight
pooled probability is in [0.05, 0.95]. Region is relative to the player's own distribution:
`line_z = (N - 0.5 - median) / sigma`, `below` z < -0.5, `above` z > 0.5, else `near`
(median and sigma, = IQR/1.349, from the equal-weight pooled grid).

## Cell evidence guardrails (pre-registered)

Per-cell "best model" is descriptive and only a hypothesis generator.

1. A cell carries a claim only with >= 500 rows AND >= 150 distinct games; smaller cells print the
   scores but `claim = null`.
2. The claim is: the cell's best candidate beats the target's overall best single candidate in that
   cell; game-clustered paired bootstrap p-value, Benjamini-Hochberg over ALL eligible cells of the
   target, q < 0.05. Cells where the overall champion is already best never produce a claim.
3. Dimensions: cold-start bucket, season phase, starter/bench, minutes trend (up/flat/down at +-2
   min), teammates-out, archetype, position group, and for thresholds `line_region`.
4. The router is judged as a whole against the best single model per target, never cell by cell.
5. A cell claim never changes a route by itself. Acting on one needs a new spec and a new
   pre-registration.

## Current table (2023-24 evidence only; built 2026-10-08)

Scores: props = 19-level grid CRPS (lower is better; coarse quadrature, comparable across
candidates, not numerically equal to the 199-level CRPS elsewhere); thresholds and win = log loss.
`full` n = rows 2023+2024 (2633 games); `ext` n = rows 2024 (1315 games). The router column is the
walk-forward softmax gate (30-day blocks, 60-day for thresholds) scored on rows it predicted; delta
is vs the best single candidate with a game-clustered 95% CI; BH over the 6 router comparisons of
the target.

| target / set | n | context_residual | recency | seq_props | ctxres_v2 | router delta vs best single | kind |
|---|---|---|---|---|---|---|---|
| pts / full | 55,202 | 3.3246 | 3.4417 | | | -0.0034 (below the 0.005 floor) | champion context_residual |
| reb / full | 55,202 | 1.3777 | 1.4224 | | | -0.0009 | champion context_residual |
| ast / full | 55,202 | 0.9629 | 1.0017 | | | -0.0006 | champion context_residual |
| fg3m / full | 55,202 | 0.6323 | 0.6670 | | | -0.0009 | champion context_residual |
| pts / ext | 27,583 | 3.3424 | 3.4718 | 3.3671 | 3.3101 | -0.0171 [-0.0230,-0.0115], q 0.001 | **router** (slice rule met) |
| reb / ext | 27,583 | 1.3888 | 1.4387 | 1.4046 | 1.3357 | 0 (gate collapses onto ctxres_v2) | champion ctxres_v2 |
| ast / ext | 27,583 | 0.9604 | 1.0019 | 0.9721 | 0.9284 | 0 (collapses) | champion ctxres_v2 |
| fg3m / ext | 27,583 | 0.6445 | 0.6799 | 0.6435 | 0.6112 | 0 (collapses) | champion ctxres_v2 |
| win / full | 2,633 games | injury_elo 0.6024 | mov_elo 0.6110 | | | +0.0001 [-0.0015,+0.0018] | champion injury_elo |
| thr_pts / full | 135,270 | 0.5044 | 0.5203 | | | -0.0009 | champion context_residual |
| thr_reb / full | 178,355 | 0.5110 | 0.5285 | | | -0.0002 (CI spans 0) | champion context_residual |
| thr_ast / full | 117,995 | 0.5136 | 0.5331 | | | -0.0006 | champion context_residual |
| thr_fg3m / full | 155,170 | 0.5259 | 0.5452 | | | -0.0018 | champion context_residual |
| thr_pts / ext | 68,525 | 0.5077 | 0.5267 | 0.5120 | 0.5088 | -0.0029 | champion context_residual |
| thr_reb / ext | 90,898 | 0.5108 | 0.5305 | 0.5199 | 0.4954 | -0.0004 | champion ctxres_v2 |
| thr_ast / ext | 59,649 | 0.5132 | 0.5353 | 0.5234 | 0.5062 | -0.0006 | champion ctxres_v2 |
| thr_fg3m / ext | 80,023 | 0.5258 | 0.5467 | 0.5300 | 0.5168 | -0.0004 | champion ctxres_v2 |
| joint_parlay | 15,798 parlays | gaussian copula beats independence by log loss -0.00053 [-0.00106,-0.00003]; t copula tied with gaussian; joint_game_set not yet run | | | | no router (no per-candidate OOF) | champion gaussian_copula |

Reading it honestly:

* Only `pts / ext` is a router. Its gain (-0.0171 CRPS) comes from mixing `ctxres_v2` (best overall
  on pts) with `context_residual`, which is better on starters (the cell claim: starters n = 12,652,
  context_residual beats ctxres_v2 by -0.0200 CRPS, q = 0.001, the same slice that made exp-2 fail
  pts). It is 2024-only evidence, and ctxres_v2 was designed after 2024 was viewed, so this is a
  selection-pool result, not a confirmation.
* On `full` the router beats the best single by statistically detectable but economically tiny
  amounts (-0.0034 pts CRPS is 0.1%); none reaches the 0.005 pre-registered floor. Win routing is a
  null (injury_elo already contains the information).
* On reb/ast/fg3m `ext` the gate puts essentially all weight on `ctxres_v2`; routing adds nothing
  over choosing the better model, which is what the table's champion column says.
* Per-cell claims that survived the guardrails: `thr_fg3m` line region `below` (recency beats
  context_residual, delta -0.0062 over n = 29,031 threshold rows), `thr_pts / ext` cold-start
  (ctxres_v2, -0.0123, n = 1,543 rows), `pts / ext` starter (above). Archetype and position group
  produced no surviving claim. These are hypotheses, not routes.
* `seq_props` is never the best candidate and never earns weight that matters.
* The registry champion for every target is still the registered production model. `ctxres_v2` is
  unregistered and untouched on 2025; nothing here promotes it.

Reproduce: `uv run python -m nba.registry route build --archetypes` (about 7 minutes;
`nba.duckdb` opened read-only, OOF store read-only after population) then `route show`.

## Pre-registration: 2025-26 (season 2025, frozen holdout)

Fixed before any 2025 routing number exists.

1. **What is tested.** Every spec with `kind = router` at freeze time. At the time of writing that is
   `pts_ext` (hash `4b6df168ff9e9503`) only. Champion specs make no routing claim and are scored
   descriptively. A spec is evaluated only if EVERY candidate in it has its own holdout predictions
   (store version `<oof_version>+h2025`), produced by that candidate's own pre-registered single
   touch: `context_residual` and `recency` re-scored with the committed procedure; `ctxres_v2` and
   `seq_props` only via their own pre-registered touches. A silent subset is refused. If a candidate's
   touch does not happen, that spec is not evaluated; it is not re-fit without it.
2. **Router fit.** On 2023 -> 2024 rows only (`pts_ext`: 2024 only), frozen as JSON, hash quoted in the
   ledger row before the run. No refit, no retune, no gate-type choice after viewing 2025.
3. **Run once.** `python -m nba.stack.holdout <route.json>...` refuses without the quoted hash in the
   ledger, writes a touch marker before scoring, refuses a second run (`--allow-repeat` relabels it
   repeated-use). The 2025 context uses the same as-of code with the frozen archetype model scored at
   `2025-10-01`.
4. **Keep rule** (per router spec; best single = hindsight best on the 2025 rows, conservative):
   a. point delta vs best single <= -0.005 and game-clustered 95% CI upper bound < 0 (2000 resamples);
   b. Benjamini-Hochberg q < 0.05 over all router specs in the run;
   c. calibration not worse than the best single: props |cov80 - 0.80| <= best's + 0.01; binary targets
      ECE(10 bins) <= best's + 0.005;
   d. no slice among cold_start, season_phase, starter with n >= 200 regresses by more than +0.01.
   Otherwise NOT KEPT, reported as such; the production route stays. Per-candidate paired deltas,
   bias, cov80 and per-cell tables are reported descriptively; cell claims are not decisions.
5. **Status labels.** Candidate rows reproduced from an already-touched model keep their existing label
   (`context_residual` clean-confirmed, `recency` repeated-use). The router's 2025 touch is its own
   first touch. The ledger currently says there is no virgin holdout; this touch is "clean first touch
   of this router", not of the season.
6. **Nothing here runs 2025 scoring.** The evaluator is built and unit-tested on synthetic data only.

Draft `HOLDOUT_ACCESS_LOG.md` row (to be appended by the maintainer; fill the hash at freeze time):

> | _date_ | `python -m nba.stack.holdout registry_store/routes_draft/pts_ext/route.json` (hash `4b6df168ff9e9503`, procedure frozen at the commit that adds this row) | learned router (softmax gate over context_residual, recency, seq_props, ctxres_v2) vs best single candidate | pts | **CLEAN first touch of this router** (candidates enter with their own existing labels: context_residual clean-confirmed, recency repeated-use, seq_props and ctxres_v2 first touch only if their own rows precede this one) | PRE-REGISTERED in docs/ROUTING.md before running. Frozen: gate fit on 2024 only, l2 0.01, features log_career, games_played_season, min_avg10, min_trend, start_rate10, cold_start_bucket, archetype (k=5, frozen KMeans at 2023-10-01), pos_group. Metric: paired per-row grid CRPS delta vs the best single candidate on the 2025 rows, CI clustered by game (2000 resamples), BH q = 0.05 over routers in the run. KEEP only if delta <= -0.005 with CI upper < 0, BH q < 0.05, cov80 gap not worse by > 0.01, no slice (cold_start, season_phase, starter; n >= 200) worse by > 0.01. Run once; no re-runs or config changes after viewing. **Result:** _pending_. |

## Plan: 2026-27 (forward season is the confirmatory test)

Preconditions (HOLDOUT_ACCESS_LOG rule 3): the 2026-27 season has >= 20 games ingested.
`route validate` checks this against the DB when a spec's `holdout_season` is 2026.

1. **Rollover.** In one reviewed change: bump `FROZEN_SEASON` in `nba/stack/__init__.py` and
   `nba/registry/routing.py` to 2026 and re-designate the holdout in the ledger. Fit windows and
   the `ext`/`full` sets follow `FROZEN_SEASON`; `season = 2025` rows then enter the OOF store as
   ordinary tuning rows (candidates' 2025 predictions written under their normal version names).
2. **Refit and freeze.** `route build --archetypes --parent-dir registry_store/routes_draft
   --register` refits every router on 2023-2025 OOF, freezes it as a new version (`parent_route_hash`
   links to the 2025-26 spec) and logs it as `candidate`. No tuning on 2026 rows.
3. **Promotion.** `route promote <target> <version> --verdict <holdout verdict.json>`: a router needs
   a verdict with `kept = true` whose `route_hash` equals the spec's or its parent's. A champion
   route only needs to validate. Until a route is promoted, `nba/daily` behaves exactly as before.
4. **Daily wiring.** `context_prop_predictions_routed(...)` in `nba/daily/predict.py` returns the
   same result type as `context_prop_predictions`; per stat with an applicable promoted route the
   primary rows are the routed rows (`routed_to = route:<model>:<version>`, weights in
   `route_weights`), and the candidate rows stay as comparison rows (`recency` is already logged;
   the unrouted context primary is kept in `info`). A route whose candidates cannot all be produced
   forward (today only `context_residual` and `recency` can) is reported "not applied" with the
   reason, never applied partially. `pipeline.py` is not changed here; swapping its call to
   `context_prop_predictions_routed` is a one-line change for its owner. Line-level `thr_` routes
   are evidence only until a forward threshold step exists.
5. **Confirmatory test.** The forward season is scored by the same keep rule (a-d above) on settled
   daily predictions, primary (routed) vs the best single comparison model, game-clustered CI. No
   verdict before >= 20,000 settled player-games per stat; interim looks are descriptive and never a
   stopping rule. 2025-26 (frozen-v1 router) and 2026-27 (frozen-v2 router) are reported as separate
   tables and never pooled.

## Commands

```
# populate the OOF store (reads parquet artifacts; recency from nba.duckdb read-only)
uv run python -m nba.stack.populate artifacts
uv run python -m nba.stack.populate season-avg
# routing table
uv run python -m nba.registry route build --archetypes      # drafts only
uv run python -m nba.registry route validate --oof data/stack/oof.duckdb
uv run python -m nba.registry route show
# maintainer-only WRITES (registry): after review
uv run python -m nba.registry route build --archetypes --register
uv run python -m nba.registry route promote pts <version> --verdict reports/routing/verdict_pts_<hash>.json
```

## 2026-10-08 19:35 — contamination notice
The `ctxres_v2` OOF used by the draft routes (`*_ext` sets choosing `ctxres_v2` for reb/ast/fg3m, and the
`pts_ext` router hash `4b6df168ff9e9503`) contains the leaking `opp_adjusted_ridge` column (NULL iff
tonight's minutes < 5). Those drafts are INVALID and must not be pre-registered or promoted. Rebuild them
from leak-fixed OOF (nba/features/opponent_ridge_v2.py `ref_fixed`) before any 2025 touch. The `full`
(2023-24) champion routes, which use context_residual v1 and injury_elo, are unaffected.
