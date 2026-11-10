# combo export v1 (nba/parlay -> nba/export -> app): the no-prices combo payload

Status: contract and reference implementation only. The export wiring comes after the steward's export change merges; nothing on
the nightly path is touched by this document. Devin approved the mode on 2026-11-09: pure probability math, vig and market kept
entirely separate. **No prices, no EV, no fees, no stakes, no Kelly appear in this payload or in anything computed from it.** The
only market-derived input is the posted main LINE (a number such as 24.5, never a price), which is already shown by `line_cal`
(`docs/contracts/line_cal_export_v1.md`).

Reference implementation: `nba/parlay/combo.py` (standard library only; the browser port is tested against it).
Golden vectors: `tests/parlay/golden/combo_golden_v1.json` (regenerate with `python tests/parlay/combo_fixture.py --write`).
Builders: `nba/parlay/combo_build.py` (numpy, server side). Evidence harness: `nba/parlay/combo_eval.py` (rule pre-stated in its docstring).

## 1. File

One file per night, `combo_<YYYY-MM-DD>.json`, `schema = "combo_export_v1"`. Additive evolution only (new optional keys); a reader
ignores unknown keys. Top level:

| key | type | meaning |
|---|---|---|
| `schema` | string | `"combo_export_v1"` |
| `night` | string | slate date `YYYY-MM-DD` |
| `shared` | object | night-level block (section 2), identical for every game |
| `games` | array | one object per game (section 3) |

## 2. `shared` (about 4.1 KB raw, 1.1 KB gzipped)

- `corr`: array of `[relation, stat_a, stat_b, rho, n]`, `stat_a <= stat_b` alphabetically. Relations: `same_player`, `teammate`,
  `opponent` (player latents), `own_margin` (player vs the game margin, home minus away, sign applied by the reader: `+rho` for a
  home player, `-rho` for an away player; keyed `[own_margin, margin, pts]` since `margin < pts`), `game` (player vs `total`, and
  `[game, margin, total]`). Pooled normal-score correlations of the calibrated randomized PIT residuals, shrunk to 0 with
  k = 200 (`joint.estimate_game_corr`), 2023-24, n of player-pairs per row. A missing row means 0. Rows below are the ones that
  matter; everything else is within +-0.03 of 0:

  | relation | pair | rho |
  |---|---|---|
  | same_player | fg3m / pts | +0.49 |
  | same_player | pts / reb | +0.30 |
  | same_player | ast / reb | +0.21 |
  | same_player | ast / pts | +0.19 |
  | same_player | fg3m / reb, ast / fg3m | +0.13, +0.11 |
  | own_margin | each stat | +0.07 to +0.08 |
  | game | pts / total, fg3m / total, ast / total | +0.11, +0.09, +0.08 |
  | teammate, opponent | any | -0.02 to +0.03 |

  Stability: the fold tables (2023-only, 2024-only) agree with the pooled table to within 0.05 (largest: same_player fg3m / pts
  0.455 vs 0.493) and with the engine's `data/parlay/joint_model.json` (2022-24, raw grids) to within 0.09 on the same row.
- `scripts.definitions`: `{"blowout_abs_margin": 15, "close_abs_margin": 5, "total_delta": 12.0}` (section 6).
- `scripts.params`: array of `[script, stat, role, mu, sigma, n]`; `role` is `starter` (`starter10 >= 0.5`) or `bench`. Latent shift
  and scale of the player normal score under that script, shrunk toward `(0, 1)` with k = 200.
- `scripts.status`: `{script: {"status": "calibrated" | "exploratory", "why_not": [..]}}` from the pre-stated check of section 7 (overridden, see below).
  **Review override 2026-11-10: in v1 every script is `exploratory`** (grind's pass in the second run is not confirmatory, section 7).
  The app MUST show the word "exploratory" next to any script whose status is not `calibrated`; none is.
- `provenance`: `map` `"LINE_CALIBRATION"`, `map_sha8` (`443743a1`), `fit_through` (`2025-06-30`), `corr_fit_seasons`
  (`[2023, 2024]`), `corr_n_games` (2,638), `combo_params_sha8` (sha of `nba/parlay/combo_params_v1.json`), `rule_sha8` of the
  evaluation rule (docstring of `combo_eval.py` at the commit that produced the params), `code_version` (git sha of `nba/parlay`).

## 3. Per game

```
{ "game_id", "date", "home_team", "away_team", "tip_utc", "made_at",
  "game": {"mu_m", "sd_m", "mu_t", "sd_t"},
  "players": [ {"player_id", "name", "team", "p_play", "starter", "stats": {"pts": {...}, "reb": {...}, "ast": {...}, "fg3m": {...}}} ] }
```

- `game`: normal margin (home minus away) and total from the engine's game model (`game_model.py`; `mu_m = b + s * logit(p_home)`,
  `mu_t` from the as-of scoring environment; `sd_m = 13.63`, `sd_t = 18.82`). Two decimals. Win / spread / total legs and the script
  priors read these.
- `team`: integer team id; `home_team` / `away_team` say which side is home.
- `p_play`: P(plays), 4 decimals, or `null` (unknown: then only conditional numbers are shown). All joint probabilities are
  CONDITIONAL on every named player playing; the reader reports `p_all_play = product of p_play over the distinct players` and
  `p_joint_incl_dnp = p_all_play * p_joint` next to it. Policy, stated once: with no money in the picture there is no void; a
  combo "hits" only if every player plays and every leg hits, so a DNP counts as a miss in `p_joint_incl_dnp`. Availability is
  assumed independent across players (not tested; it is the engine's standing assumption, `docs/PARLAY_ENGINE.md`).
- `starter`: 0 / 1 (`starter10 >= 0.5`, the line-calibration "bench" flag inverted).
- per stat entry: `k0` (int), `surv` (array of 4-decimal numbers), `main_line` (x.5 or `null`), `raw_at_main` (the uncalibrated
  P(over main line), only when `main_line` is set), `cal_k` (`[lo, hi]`, 1-based rungs where the map was applied directly).
  **`surv[j] = S(k0 + j) = P(Y >= k0 + j)`; `S(k) = 1` for `k < k0`; `S(k) = 0` past the end of `surv`.** The array is nonincreasing and
  trimmed where it rounds to 1.0000 (front) or 0.0000 (back). "Over x.5" is `S(x + 1)`; "under x.5" is `1 - S(x + 1)`. Whole-number
  lines are not offered (no push in this mode).
- Calibration of the grid (`combo_build.calibrate_surv_banded`): the frozen LINE_CALIBRATION map (`line_cal.py`, DraftKings cells; no
  price enters, `design()` takes none) is applied at line `k - 0.5` with the player's own median and spread (from the 19-point
  `q_grid`) and starter flag, only for rungs with `|z| <= 1` (`z = (line - median) / spread`; posted main lines sit at
  `z in [-0.5, 1.0]`, which is where the map was fitted). Beyond the band the logit offset (calibrated minus raw) is HELD at the band
  edge; those rungs are extrapolation and the app labels them "outside the calibrated range" using `cal_k`. At or beyond the
  199-sample grid resolution raw passes through. Then the cumulative minimum makes it monotone. At the main line the value is the
  panel's `p_cal` / `line_cal.p_over_cal` exactly (same map, same inputs). Why the band: applying the map at every rung fattened the
  tails (PIT normal-score sd 0.63 to 0.90 against 1.07 to 1.13 for raw grids on 2024), which is an invalid distribution.
  Remaining flaw: even banded, the grid's PIT sd is 0.76 to 0.98, so it is a set of calibrated leg probabilities at plausible lines,
  not a validated whole distribution; interval legs (`over a` and `under b` on one stat) inherit this.
- Known-at: `made_at` is the production forecast's own run clock (UTC, ISO Z) and `tip_utc` the real tip. A game block is built only
  from a production row stamped at or before `made_at` and before tip, uses the main line pair stamped at or before that same
  `made_at` (the `line_cal` rule), and is never rebuilt after tip. `main_line` carries no timestamp of its own: it is the
  `line_cal.main_line` of the same prop (one value per game, player, stat). Correlations, script parameters and the calibration map are
  fitted on 2023-24 only; no later information reaches a forecast.

## 4. Size budget

Measured on real 2024-25 nights with the shipped map (all rotation players, all four stats, main lines from DraftKings T-60):

| night | games | raw bytes | gzipped | stored survival values per game |
|---|---|---|---|---|
| median (6 games, 2024-11-19) | 6 | 88,118 | 19,387 | 1,170 |
| busiest (15 games, 2025-04-11) | 15 | 235,387 | 50,163 | 1,423 |

About 20 players per game, 13 to 16 KB raw per game. **Budget: <= 300 KB raw and <= 60 KB gzipped per night**; flag growth above it
(a player with a wide `pts` grid costs about 60 values). To shrink if ever needed: drop `raw_at_main`, trim `surv` at 0.001.

## 5. What the browser computes (normative; every function exists in `combo.py`)

1. **Legs.** Player leg `{player_id, stat, line, side}`; `team_margin` `{team, gt}` (that team wins by more than `gt`; margins are
   integers); `total` `{line, side}`. Same-entity legs merge by intersecting their integer intervals before anything else
   (comonotone); an empty intersection has probability 0.
2. **Latent.** Player-stat: `Y >= lo` iff `z > a`, `a = Phi^-1(1 - S(lo))`; `Y <= hi` iff `z <= b`, `b = Phi^-1(1 - S(hi + 1))`
   (infinite where S is 1 or 0). Margin and total latents are the standardised normals `(x - mu) / sd`.
   `Phi^-1` is Wichura AS241 (`statistics.NormalDist.inv_cdf`; any implementation within 1e-9 will do), `Phi` via `erfc`.
3. **Correlation.** Entities of one game get `R[i][j]` from `shared.corr` by relation (same player; teammate vs opponent by `team`;
   margin and total rows with the home-sign rule). Principal submatrices of a PSD matrix are PSD: on 200 real 2024 games the full
   game matrix (about 82 entities including margin and total) had minimum eigenvalue 0.234 and needed no repair (Higham max
   change 0.0). If a user subset still fails Cholesky (pivot <= 1e-10) the reader shrinks `(1 - t) R + t I` by bisection on t
   (20 steps) and reports `repair_t`. The Gaussian copula only: the t copula (df 6) was not kept over the Gaussian
   (`docs/PARLAY_ENGINE.md`, log-loss difference -0.00005 [-0.00021, +0.00011]).
4. **Joint.** One entity: the marginal itself (`p_joint = p_independent`, lift exactly 1). Two entities: exact bivariate normal
   rectangle by Simpson on 2,000 intervals over `[-9, 9]`, clamped `|rho| <= 0.9999`. Three or more: Genz (1992) separation of
   variables with a Richtmyer lattice `frac(j * sqrt(p_i) + shift_i)` (primes 2, 3, 5, ..), j = 1..1500, 8 fixed shifts from the LCG
   `x <- (1664525 x + 1013904223) mod 2^32` seeded 12345 (drawn row by row, one value per dimension), estimate = mean over shifts,
   `se` = sd across shifts / sqrt(8). Then clamp to the Frechet bounds `[max(0, sum p - (k - 1)), min p]`. Cross-game: the product of
   the games' joints (independent by construction); `se` combines by the delta method on the product.
5. **Outputs per combo.** `p_joint`, `p_independent` (product of the merged entities' marginals), `lift = p_joint / p_independent`,
   `marginals`, `se`, `method` (`marginal | exact_bivariate | qmc | product_of_games | empty`), `p_all_play`, `p_joint_incl_dnp` (both `null` when any named player's `p_play` is null: unknown availability is not 'no DNP risk'),
   `label`, `range_2se`. Show `p_joint +- 2 se` whenever `method == "qmc"`; the exact methods show no error bar. Any combo with three
   or more entities carries `label = "model estimate, not checked against results"`: only two-leg combos were scored (section 7), and
   never call a three-leg number "calibrated". Game legs use the integer half-point correction (a whole `gt` is `gt + 0.5`; whole
   total lines are refused, as for players).
6. **Tolerances for the TS port** (golden file): `exact_bivariate`, `marginal`, `empty`, script priors: 2e-6 absolute. `qmc`: within
   `4 * se + 1e-4` of the stored `p_joint` (a port that copies the lattice and shifts exactly will agree to about 1e-9). Lift
   ranking and target search: same legs in the same order (ties break on the stringified legs, as in the reference).

## 6. The four framings

All four use the same `evaluate` core. None shows a price.

1. **Game-script lift** (`lift_ranking`). Legs at the posted `main_line`, all overs or all unders; pairs of any two entities and
   triples that contain a same-player pair; combos with `p_joint < 0.02` dropped (a large lift on a 0.1% event is noise); ranked by
   `lift = p_joint / p_independent` (ties: higher `p_joint`). The reason is a fixed template from the relation of the strongest pair
   (same player: shared minutes, or "threes are points"; different players: "no reliable link, treated as near independent") with
   the latent r printed. Top of the list is always same-player combos; different-player pairs sit at lift 1.00 +- 0.01.
2. **Target probability** (`target_search`). User sets `target` and `k_legs`. **Objective, stated:** over combos of `k_legs` over-legs
   on distinct (player, stat) entities of ONE game, each leg's marginal in `[0.35, 0.75]` and not above the tested range (below), with `p_joint >= target`, MAXIMISE
   `lift`; ties by smaller `p_joint - target`. The marginal band is the guard against the degenerate answer: without it the
   cheapest way to reach any target is to stack near-certain lowest lines at lift 1.00. **Known tendency: the best answer usually has a
   leg at the band floor** (marginal near 0.35), because lift is largest for low-probability legs; the band moved the degenerate
   answer from "near-certain filler" to "the 0.35 edge", it did not remove it. Legs the app would label "above the tested range"
   are excluded here (labelled and excluded, never capped). `floor_mode = "main_line"` additionally
   requires each leg's line to be at or above the posted main line. k = 2 is exhaustive (every pair screened on a coarse integral, the leaders recomputed exactly; checked against brute force in
   the tests). For k >= 3 a partial combo below the target is pruned (adding an over leg can only lower the joint, an exact bound) and
   the survivors are beam-searched (width 40): a model estimate, labelled as such. The answer
   says `feasible: false` and returns the highest joint found when the target cannot be reached. Cross-game combos are not searched
   (their lift is 1 by construction; the user can build them in framing 4).
3. **Pick a script** (`evaluate(..., script=...)`). Definitions, all checkable from the final score:
   `blowout`: `|final margin| >= 15`; `close`: `|final margin| <= 5`; `shootout`: `final total >= ceil(mu_t + 12)`; `grind`:
   `final total <= floor(mu_t - 12)` (`mu_t` the pre-game game-model expectation, so the thresholds move with each game and with
   the scoring environment). Not exclusive. Under a script the player latent is `z = mu + sigma * eps` with `(mu, sigma)` from
   `shared.scripts.params` by (script, stat, role) and `eps` keeping the pooled within-game correlations (an approximation:
   within-script correlations were not re-estimated). `script_prior` is P(script) from the game's normals with integer support
   (margin 15 is `M >= 14.5`); the shootout and grind priors are the same number for every game by definition (about 0.27), while the REALISED share has ranged by season from 22% to 31% (shootout
   22.1% / 28.6%, grind 31.1% / 22.7% in 2023 / 2024) because the season mean of `total - mu_t` moved from -2.4 to +2.1. Show the prior
   with that range next to it.
   Game legs (win, total) are refused inside a script. The status in `shared.scripts.status` comes from the historical check below.
4. **Build your own** (`evaluate_combo`). Any legs, any lines, any games: live `p_joint`, `p_independent`, `lift`, `se`, DNP line.

## 7. Evidence (2023-24; n and game-clustered CIs; `reports/parlay/combo_eval_2026-11-09.json`)

Setup, fixed before the run (docstring of `combo_eval.py`, commit fd81aaf; one amendment, recorded in git before the second run,
see below). Legs are OVERS at DraftKings T-60 main x.5 lines. Fold A: calibration map, correlations and script parameters fitted on
2023, tested on 2024 (walk-forward). Fold B: fitted on 2024, tested on 2023 (cross-fit, not walk-forward). Game-model parameters
(five numbers, fitted 2022-24) are in-sample for `mu_t`; disclosed. Season 2025 never loaded. CIs: 2,000 resamples of the game (the
date for cross-game pairs).

**How big is the lift really.** Two-leg combos, exact bivariate copula with calibrated marginals:

| class | fold | n pairs | clusters | predicted lift | realised hits / product of calibrated marginals | realised / copula | residual r (calibration-free) |
|---|---|---|---|---|---|---|---|
| same player, different stats | A 2024 | 69,070 | 1,299 games | 1.169 | 1.197 [1.174, 1.222] | 1.024 [1.004, 1.045] | +0.172 [+0.163, +0.182] |
| same player, different stats | B 2023 | 68,323 | 1,316 games | 1.171 | 1.179 [1.155, 1.203] | 1.007 [0.987, 1.027] | +0.174 [+0.165, +0.183] |
| teammates | A 2024 | 150,000 (sample) | 1,299 | 1.002 | 1.007 [0.985, 1.030] | 1.006 [0.983, 1.030] | -0.003 [-0.008, +0.002] |
| teammates | B 2023 | 150,000 (sample) | 1,314 | 1.004 | 0.981 [0.958, 1.003] | 0.977 [0.955, 1.000] | +0.001 [-0.005, +0.007] |
| opponents | A / B | 150,000 each | 1,274 | 1.001 | 1.020 [0.997, 1.043] / 0.986 [0.961, 1.011] | 1.019 / 0.985 | +0.002 [-0.003, +0.008] / -0.003 [-0.009, +0.003] |
| cross-game, same date | A / B | 150,000 each | 183 / 187 dates | 1.000 | 1.028 [1.006, 1.051] / 0.984 [0.961, 1.008] | same | +0.002 [-0.004, +0.008] / -0.001 [-0.006, +0.004] |

Plain reading: **lift is real for one thing, the same player's different stats (about 1.17 to 1.20 for a pair, from shared
minutes; 1.16 to 1.20 after removing the season-level marginal offset seen in cross-game pairs: 1.197 / 1.028 = 1.164 in A,
1.179 / 0.984 = 1.198 in B), and it is nothing for everything else.** The copula's own predicted lift is 1.169 / 1.171. The headline
"1.197" mixes dependence with a 3% marginal offset, so quote the range, not the third decimal. Teammates, opponents and different games have a leg-outcome residual
correlation of 0 within +-0.01 (all six CIs inside [-0.009, +0.008]), consistent with T490 (independence supported in 55 of 65 pools;
its "dependent" cells were a weighting artifact). The deviations of "realised / product" from 1 for cross-game pairs (+-3%) are
marginal calibration offsets of the legs, not dependence (the residual r is 0). The copula beats independence on log loss only for
same-player pairs (-0.0090 [-0.0101, -0.0077] A, -0.0071 [-0.0081, -0.0059] B); for the other classes the difference is within
-0.00005 of 0. Triples and larger were NOT measured against outcomes here (two-leg only); their joint comes from the same copula
and is labelled as such. Residual r is on hit indicators, so it shows co-movement on the posted-line event, not a latent r.

**Calibrated vs raw** (marginals; over legs by RAW probability band; observed minus predicted, game-clustered):

| raw band | fold | n legs | mean raw | gap raw | mean calibrated | gap calibrated |
|---|---|---|---|---|---|---|
| 0.5-0.6 | A 2024 | 12,552 | 0.542 | -0.016 [-0.025, -0.007] | 0.517 | +0.009 [-0.001, +0.018] |
| 0.6-0.7 | A 2024 | 3,030 | 0.636 | -0.062 [-0.079, -0.043] | 0.558 | +0.017 [-0.000, +0.034] |
| 0.7-0.8 | A 2024 | 452 | 0.736 | -0.086 [-0.131, -0.043] | 0.621 | +0.029 [-0.016, +0.075] |
| 0.6-0.7 | B 2023 | 3,058 | 0.638 | -0.063 [-0.081, -0.045] | 0.577 | -0.002 [-0.021, +0.016] |
| 0.7-0.8 | B 2023 | 608 | 0.737 | -0.117 [-0.156, -0.077] | 0.658 | -0.038 [-0.077, +0.001] |
| 0.8+ | B 2023 | 85 | 0.847 | -0.306 [-0.410, -0.204] | 0.703 | -0.161 [-0.273, -0.055] |

Raw 60%+ legs run 6 to 12 points high (the T489-T491 finding, reproduced), 80%+ raw legs about 30 points high; the map removes most
of it up to 80% and **not all of it above 80%** (n = 85; the 2024 fold has fewer than 50 such legs and reports none). Low raw legs (<0.3) are under-predicted by
0.15 raw and +0.035 [+0.018, +0.055] after calibration in fold A. Compounding: cross-game two-leg combos whose both legs have raw
P >= 0.70 (n = 686 pairs / 96 dates in A; 1,610 / 124 in B) hit 0.423 and 0.360 against a raw product of 0.557 and 0.565 (realised
/ raw product 0.759 [0.647, 0.873] and 0.637 [0.527, 0.755]); the calibrated product (0.400, 0.425) is much closer (realised /
calibrated product 1.056 [0.889, 1.218] and 0.847 [0.711, 0.982]). The app must show the calibrated number and may show the raw
one labelled "uncalibrated model".

**Script calibration check** (pre-stated: for each script, in BOTH folds, over main-line over legs of games that realised the script:
(i) paired Brier difference script-conditional minus unconditional has CI upper bound < 0, (ii) 95% CI of observed minus mean
conditional prediction inside +-0.02, (iii) for same-player pairs the 95% CI of observed / predicted conditional joint contains 1).

| script | realised share of games 2023 / 2024 | fold A: legs / games, observed, conditional pred, unconditional pred | fold A dBrier | fold B dBrier | verdict |
|---|---|---|---|---|---|
| blowout | 32.8% / 34.4% | 18,404 / 444: 0.443, 0.439, 0.477 | -0.00187 [-0.00260, -0.00118] | -0.00181 [-0.00272, -0.00094] | exploratory (fold B same-player joint ratio CI [1.015, 1.097] excludes 1) |
| close | 23.5% / 25.2% | 13,686 / 331: 0.513, 0.490, 0.476 | -0.00047 [-0.00089, -0.00004] | -0.00081 [-0.00125, -0.00038] | exploratory (under-reacts: obs - pred +0.023 [+0.013, +0.032] A, +0.021 [+0.011, +0.030] B) |
| shootout | 22.1% / 28.6% | 15,380 / 373: 0.521, 0.497, 0.477 | -0.00274 [-0.00341, -0.00213] | -0.00212 [-0.00272, -0.00157] | exploratory (under-reacts: +0.025 [+0.014, +0.035] A, +0.033 [+0.022, +0.043] B) |
| grind | 31.1% / 22.7% | 12,030 / 291: 0.434, 0.440, 0.476 | -0.00351 [-0.00438, -0.00271] | -0.00463 [-0.00560, -0.00364] | **exploratory (review override)**: the frozen check passed in run 2 (obs - pred -0.006 [-0.014, +0.003] A, +0.001 [-0.007, +0.009] B; pair ratio 0.965 [0.925, 1.005], 1.002 [0.966, 1.041]) but failed in run 1 (three conditions), the amendment came after run 1 on test-season outcomes, and the pass is fragile (fails under 1 of 5 alternate PIT seeds and when 2024-10 or 2024-11 is dropped; adversary review combo_math_2026-11-10) |

Reading: every script moves the legs the right way and beats the unconditional prediction on Brier, but only `grind` met all three
pre-stated conditions in both folds, and only in the second run (see the override above). The shift is large and robust: over legs hit 3.5 to 4.6 points LESS in blowouts and grinds and
2.7 to 4.6 points MORE in close games and shootouts than the unconditional forecast. `shootout` and `close` are under-predicted by
about 2 to 3 points. The cross-fit fold is not walk-forward; one season each; the 2026 forward log has not been used and nothing
is claimed about it. No script is labelled calibrated until a frozen forward rule says so (draft: `docs/contracts/combo_script_forward_rule_DRAFT.md`, not
frozen, not run). The check also cannot see a joint miscalibration smaller than about 4% (the same-player ratio CI half-width is about
0.04, and "CI contains 1" is a non-rejection test). The script is a realised outcome, so this tests the conditional probabilities, not anyone's ability to predict the script
(`script_prior` is just the game model's normal tail).

**Amendment, recorded before the second run (commit 4fa66a1).** The first run applied the map at every rung; the PIT diagnostic
(normal-score sd 0.63 to 0.90) showed an invalid latent and the script parameters were re-estimated on the banded grid. Thresholds were
unchanged. The first run's pair and band numbers are unchanged to the third decimal by the amendment (same-player lift 1.1960 vs 1.1971; main-line
legs beyond |z| = 1 moved slightly and the bootstrap stream shifted). The amendment was decided after run 1 failed all four scripts, on a PIT
diagnostic computed from 2024 outcomes, and it flipped grind: that is a second look at the same test seasons, not a pre-registered pass. The
first-run output is committed at `reports/parlay/combo_eval_2026-11-09_run1.json` (reproduced by the adversary from fd81aaf; all four
exploratory, grind failing three conditions). `resid_r` and the PIT sd were added after the first run, descriptive only. A follow-up that would likely close the
under-reaction (a log-odds offset per script and stat estimated directly on rotation-player legs instead of via the PIT latent) is a
new question and needs its own rule before it is run.

## 8. What the app may and may not say

- Show `p_joint` (calibrated), `p_independent`, `lift`, the reason in words, `p_all_play`, and the `se` for `qmc`. Show the word
  "calibrated" only for legs inside `cal_k`; outside it say "outside the calibrated range".
- "Exploratory" beside every script (none is calibrated in v1), with the n above on request.
- A leg with calibrated P above 0.75, or at the posted main line with `raw_at_main >= 0.80`, is shown with "above the tested range,
  likely too high" (2023-24: calibrated 0.75-0.80 legs hit 12.6 points less than predicted, n = 67; raw 80%+ legs 16 points less after
  calibration, n = 85) and is excluded from target search. The number itself is not capped (a cap invents a probability).
- Any combo with three or more legs: "model estimate, not checked against results", with the +-2 se range.
- Cross-game combos: "different games are treated as independent (measured r about 0)". Never present a lift for them.
- No stakes, no sizing, no EV, no fee, no price, no "should I bet". The market is not an input and is not compared here; any
  price comparison lives in the separate markets panel with its own verdicts (`keep_your_money` until the log proves skill).
- A lift above 1 means the legs co-move, not that the combo is a good bet: a more probable event is not a more valuable one.
