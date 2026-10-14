# Player "model reliability" badge: what the stored forecasts can honestly support (2026-10-14)

DESCRIPTIVE. No claim, no pre-registered rule, nothing about beating the market. Reliability is not an edge.
Work label: Devin's request (a per-player badge for the app); not momentum. Model class: gradient-boosted residual over a
recency average (`props_context_residual` v2, real-tip gated), scored through its stored out-of-fold quantiles; no model was
fitted. Code: `nba/eval/player_reliability.py`, tests `tests/eval/test_player_reliability.py`. Artifacts:
`reports/player_reliability/reliability_2024.parquet` (and `reliability_2023.parquet`, `reliability_summary.json`).
Parquet is gitignored in this repo, so the artifacts are regenerable (`uv run python -m nba.eval.player_reliability`) and
the 2024 file is force-added for the app hand-off.

## Bottom line

* The tiers are almost entirely **games played** plus noise. Among players with at least 30 games in both seasons the tier
  agreement 2023 -> 2024 is 63.5% (CI 60.9-66.3), but chance agreement given the tier shares is nearly as high:
  Cohen's kappa 0.07 (CI 0.02-0.12). Per stat the kappa CIs include 0 for pts, reb and ast.
* "Skill vs recent form" persists weakly: Spearman 0.09 (CI 0.03-0.15) for shrunk skill, 0.05-0.12 per stat. Between-player
  spread in true skill is small (tau about 0.02 against a typical skill of 0.04-0.05), so a 50-game season cannot rank players.
* What does persist is **minutes steadiness**: Spearman 0.81 (CI 0.77-0.84) for the coefficient of variation of minutes.
* So the badge must not be a promise of "the model is more accurate on this player next season". Frame it as
  **"how predictable this player's role has been"** and show n and minutes steadiness next to it.

## Data and its limits (read before trusting any number)

* Forecasts: `reports/context_residual_v2_realtip/oof_context_residual.parquet` (walk-forward month blocks, real-tip report
  gating, the file behind T092-T095 and T128; byte-identical in size to the `context_residual_realtip` copy). 19-point
  `q_grid` (taus 0.05..0.95). 2023: 27,619 played player-games per stat, 2024: 27,583, 551 players per season, 4 stats.
  Played rows only (minutes > 0), so everything is conditional on playing; "n" is games scored, not games on the roster.
* Outcomes: `nba.duckdb`, read-only, `season <= 2024` in SQL. The OOF's stat values match the outcome stored with
  `reports/lower_tail/components_pts.npz` (data_version 418510d401d3) on all 55,202 pts rows (0 mismatches).
* **Neutral-site note (DATA_CHANGELOG 2026-10-11, F2).** Five 2024 neutral-site games were restored after this OOF was
  built (current 2024 n is 27,701 per stat, 118 rows more than the stored 27,583). They are not scored here, and I removed
  them from the baseline history too (0022400147, 0022400621, 0022400633, 0022401229, 0022401230) so the baseline does not
  know about games the stored forecast did not. 2023 is unaffected (27,619 both).
* Season 2025 is never loaded. These are 2023 and 2024 out-of-fold numbers, two seasons old by now; the live 2026-27 badge
  should be recomputed from the forward log, not read from this table.

## Method (everything below was fixed before any player was inspected; no player name is printed or stored)

Unit: (player, stat, season). Stats pts, reb, ast, fg3m.

* **Integer support, same grid, same rows.** Production quantiles and the baseline are both mapped to integers with
  `ceil(q - 0.5)` and scored with the same 19-tau pinball form of CRPS (`crps_from_quantiles`), on the same rows.
  Absolute CRPS values are therefore on the 19-grid and are not comparable to the 199-grid ledger numbers.
* **Baseline ("recent form").** The player's previous up to 20 played games strictly before the game (date, game id), weighted
  `0.5 ** (games_ago / 10)`, taken as a weighted empirical pmf (integer-valued by construction), at least 5 prior games
  (production's own minimum; no row was dropped for this). It is NOT the ledger's `recency_conformal` (Normal mean/std with
  conformal quantiles), so skill here is not the ledger's delta. Planted-future test: changing the row's own value or adding
  a later game does not move the baseline.
* **Skill** = 1 - sum CRPS(model) / sum CRPS(baseline) over the player's scored games. Pooled over players it is
  0.041-0.042 (pts, reb, ast) and 0.031 (fg3m) in 2023, 0.050, 0.050, 0.049 and 0.038 in 2024: the model beats this baseline
  by about 4-5% of CRPS, and per-player differences around that are small.
* **Coverage** = share of the player's games whose integer outcome lies in the stored 80% interval [q10, q90]. Pooled
  0.79-0.81 in every stat-season, matching the 0.799-0.801 of the ledger report. Disclosure: my first attempt used a
  "continuity-corrected" fraction of the unit cell; it gave pooled fg3m coverage 0.63 because production quantiles are
  clipped at 0 with a point mass (a certain 0 counts half). I replaced it with the naive event, which is what production's
  conformal step is calibrated for, before any tier was read. The integer-rounded interval over-covers (INTEGER_QUANTILES
  caveat) and is not used.
* **Minutes volatility** = sd / mean of the player's minutes over the scored games (descriptive; not in the tier rule).
* **Empirical-Bayes shrinkage** (per stat and season, no cross-season information): normal-normal with the DerSimonian-Laird
  between-player variance, fitted on cells with n >= 10. Skill's sampling variance is a delta-method variance of the CRPS
  ratio (game-level; it ignores serial correlation, so it is a little optimistic); coverage's is binomial at the pooled rate.
  Mean shrinkage weight on a player's own skill is 0.10-0.26: most of a player's raw skill is treated as noise. A player
  whose baseline CRPS is exactly zero (never records the stat; 43-46 fg3m cells, a few ast/reb) has undefined skill and is
  shrunk entirely to the stat mean.
* **Tier rule** (applied to the shrunk skill and coverage; n is raw):
  * low: n < 30, or shrunk coverage outside [0.70, 0.90];
  * high: n >= 50, shrunk skill at or above the top third (66.7th percentile) of that stat-season's cells with n >= 30,
    and shrunk coverage within [0.75, 0.85] (and the between-player spread is estimated to be above zero);
  * medium: everything else.
* **Stability.** Players present in both seasons: tier agreement, Cohen's kappa, Spearman correlations on within-(stat,
  season) percentile ranks (so pooling stats cannot manufacture persistence), 95% CIs from a player-clustered bootstrap
  (1,000 draws, seed 0). Reported both for all cells and for cells with n >= 30 in both seasons, which removes the
  mechanical persistence of "missed many games".

## Results

n: 2,204 cells per season (551 players x 4 stats); 1,660 cells with n >= 30 in 2024 (1,580 in 2023).

Tier shares (all cells):

| season | low | medium | high |
|---|---|---|---|
| 2023 | 31.6% | 53.6% | 14.8% |
| 2024 | 27.4% | 57.8% | 14.8% |

In 2024, 544 of the 604 "low" cells are low because n < 30, only 60 because of coverage. Among cells with n >= 30 the
shares are 3.6% low, 76.7% medium, 19.6% high. By stat in 2024 (low/medium/high): pts 24.7/57.7/17.6, reb 24.7/58.6/16.7,
ast 26.1/59.2/14.7, fg3m 34.1/55.7/10.2 (fg3m has the most low because its coverage is the most variable and its skill
the least identified).

Stability 2023 -> 2024 (player-clustered 95% CI):

| cells | n (players) | same tier | kappa | rho shrunk skill | rho raw skill | rho abs(cov - 0.8) | rho minutes CV |
|---|---|---|---|---|---|---|---|
| all | 1,760 (440) | 55.2% (51.9-58.4) | 0.17 (0.12-0.23) | 0.09 (0.04-0.14) | 0.05 (0.00-0.11) | 0.12 (0.07-0.18) | 0.80 (0.76-0.83) |
| n >= 30 both seasons | 1,272 (318) | 63.5% (60.9-66.3) | 0.07 (0.02-0.12) | 0.09 (0.03-0.15) | 0.11 (0.04-0.17) | 0.06 (0.01-0.12) | 0.81 (0.77-0.84) |
| n >= 30, pts | 318 | 64.8% (59.7-70.1) | 0.04 (-0.07-0.15) | 0.12 (0.01-0.23) | 0.13 (0.02-0.23) | 0.08 (-0.03-0.20) | 0.81 |
| n >= 30, reb | 318 | 60.7% (55.3-66.0) | -0.04 (-0.14-0.06) | 0.07 (-0.04-0.17) | 0.07 (-0.05-0.18) | -0.05 (-0.15-0.05) | 0.81 |
| n >= 30, ast | 318 | 67.9% (62.9-72.6) | 0.08 (-0.03-0.19) | 0.12 (0.02-0.23) | 0.12 (0.02-0.23) | 0.08 (-0.03-0.19) | 0.81 |
| n >= 30, fg3m | 318 | 60.7% (55.7-66.7) | 0.13 (0.05-0.23) | 0.05 (-0.06-0.16) | 0.10 (-0.02-0.22) | 0.12 (0.00-0.23) | 0.81 |

Persistence of "high" (n >= 30 both seasons, 1,272 cells): 23.0% of 2023-high cells are high in 2024 against 19.7% of all
such cells, a lift of 3 points. 2023-medium stays medium 77.9% of the time (base rate 76.6%). In the all-cells table (1,760 cells)
the one strong persistence is "low" (180 of 394 2023-low cells are low again, 45.7% against a 20.5% base rate), and 624 of
the 697 2023-low cells were low for n < 30, i.e. games played, not a model property.

Skill is higher where minutes are less steady: within n >= 30 cells in 2024, Spearman between minutes CV and raw skill is
+0.29 (pts), +0.34 (reb), +0.18 (ast), +0.19 (fg3m). The model's edge over recent form is larger exactly for players whose
role changes, which are the players whose games are hardest to forecast in absolute terms. "Skill vs the baseline" therefore
cannot be read as "easy to predict"; this is the main reason the badge must not be driven by skill alone.

## What it means for the badge

1. Tiers do not persist beyond what games played and chance give. Do not present them as a promise about 2026-27 accuracy.
2. Frame it as **how predictable this player's role has been**. The one thing that persists (rho 0.81) is minutes steadiness,
   which is also where most prop error lives. If the app wants a stable signal, show n and minutes steadiness; a badge
   built on minutes steadiness would be a NEW question (new id, pre-registered, with its own gate); it is not tested here
   and I did not try variants.
3. Do not show "high" for a player below 50 games. The artifact keeps n so the app can print it.

Plain-language wording for the app (draft):

* Badge label: **Role steadiness** (not "model accuracy"). Values: Steady / Mixed / Limited data.
* Tooltip: "Based on {n} games last season. This describes how closely our forecasts for this player tracked his actual
  stats and minutes in the past. It does not predict that next season will be the same, and a steady role is not an edge:
  sportsbook and market prices already reflect it."
* Limited data (n < 30): "Too few games to say anything. Treat the forecast as a rough guess."
* Always show n beside the badge, never a bare "High".

## Playbook check

Class: GBM residual over a recency average, scored from its stored OOF quantiles (no new fit).
1. Information or capacity: neither; this is a descriptive readout of existing forecasts. No new pre-tip information.
2. Enough data: 551 players x about 50 games per season; per-player skill is mostly noise (tau about 0.02), hence the
   shrinkage; two seasons only for stability.
3. Test validity: integer support for both arms, same rows, as-of baseline with a planted-future test, outcomes cross-checked
   against the ledger component file (0 mismatches in 55,202), season <= 2024 only. Not a power-planned test; CIs are
   player-clustered for stability. No multiplicity family (descriptive).
4. Lineage: production OOF `context_residual_v2_realtip`; the baseline is a recency pmf (playbook comparator for count
   stats), not `recency_conformal`.
5. Scored against: a recent-form baseline, not the market. Nothing here says anything about betting value.
Class checks: P(Y = 0) reliability and randomised PIT were not computed (coverage uses the naive stored interval); the
production quantile family is continuous and rounded, which is the known source of fg3m shape error, and fg3m coverage per
player is the noisiest (between-player sd 0.08).

## Not verified

* The live app's exact interval (rounded or continuous) is unknown; coverage here is on the continuous stored interval.
* Skill sampling variances ignore serial correlation within a player-season.
* Whether tiers computed on 2026-27 forward forecasts (the T-30 arm, new data_version) behave differently.
* Only 2023 -> 2024; one transition cannot separate a changing league from non-persistence.
* Full pytest was not run (tests/eval and the layering test: 101 passed; the whole suite exceeds the watchdog on this box).
