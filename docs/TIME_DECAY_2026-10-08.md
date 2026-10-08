# Time-decay season carryover (cold-start method 4) — 2026-10-08

## What this closes
NEXT_SESSION.md "Known data gaps": as-of features currently pool all prior
seasons with EQUAL weight, so the model is slow to reflect season-over-season
change. Concrete failure cited: Tatum game 1 of a season predicted 0.1 pts /
actual 35 — no credit for "last season was great," because current-season `n`
is 0 and the existing shrinkage has nothing to fall back on but the generic
archetype/position prior.

## Method (new module, no edits to shared code)
`nba/features/time_decay.py`:
1. `season_decay_weights(gap, half_life_seasons)` — `0.5 ** ((gap-1)/half_life)`,
   gap=1 (last season) always weight 1.0.
2. `decayed_prior_season_rate(...)` — collapses N prior **completed** seasons
   into one "effective last season" rate, weighted by `sample_size x decay`.
3. `blend_as_of_rate(...)` — the full chain: current-season observed rate,
   **empirical-Bayes shrunk** (`nba.coldstart.shrinkage.shrink_rate`, reused
   unchanged) toward a **carryover component**. The carryover component is
   `nba.coldstart.carryover.carryover_blend` (reused unchanged) applied to the
   decayed prior-season rate and the caller-supplied archetype/position prior,
   with the existing age-curve multiplier.
4. `build_player_season_decayed_rates(con, numerator_col, denominator_col,
   archetype_prior, config, age_as_of)` — DuckDB builder. Current-season
   component uses `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` (the
   standard window); prior-season components use **full season totals** from
   `season < cur.season`, which is leakage-safe because `games.season` is
   monotonically increasing with calendar time in this schema — any game in a
   strictly earlier season occurred entirely before any game in the current
   season, regardless of date-within-season.

## Parameters (explicit, NOT yet tuned — `TimeDecayConfig`)
- `half_life_seasons = 1.5` — seasons for a prior season's weight to halve.
- `carryover_decay_weight = 0.3` — regression-to-mean weight blending the
  decayed prior-season rate with the archetype prior.
- `k = 150.0` — EB pseudo-count shrinking current-season observed toward the
  carryover component as current `n` grows.
- `peak_age = 27.0`, `age_curve_width = 9.0` — reused from
  `nba.coldstart.carryover.age_curve_multiplier`'s existing defaults.

All four should be tuned by walk-forward CV the same way
`nba.coldstart.shrinkage.tune_pseudo_count` tunes `k`, once wired into a real
backtest. Not done here — this deliverable is the leakage-safe mechanism, not
the tuned numbers.

## How this would wire in LATER (deliberately deferred tonight)
Owned by other agents tonight: `nba/features/player_possession_features.py`,
`nba/features/player_rebound_assist_features.py`, `nba/coldstart/archetypes.py`.
The wiring, when it happens, is a drop-in replacement of each module's
"position prior" shrinkage target:

- Today: `shrink_rate(player_obs, n, position_prior, k)` — position prior is a
  single as-of pooled rate with no season weighting.
- After wiring: call `build_player_season_decayed_rates` per rate (shot_share,
  zone_fg_pct, orb_rate, ast_rate, ...) with `archetype_prior` = that same
  position-level as-of prior (computed exactly as today), and use
  `rate_carryover_blended` as the new shrinkage target in place of the raw
  position prior. The player's own current-season observed rate still gets
  the final empirical-Bayes shrink toward this richer target instead of the
  plain position mean — the `shrink_rate` call signature does not change,
  only what's passed as `r_prior`.
- Requires picking `numerator_col`/`denominator_col` per stat (e.g. `fgm_rim`/
  `fga_rim` for zone FG%, `oreb`/`n_poss` for ORB rate) and threading
  `age_as_of` from `players_static.birth_date` for the age-curve term.

## Expected effect on the Tatum-style case
Season opener, current `n_cur = 0`: today's pipeline falls back fully to the
position/archetype prior (a league-average, ~12-15 pt proxy for a wing).
With this module, the blend instead sits strictly between the archetype prior
and last season's full-season rate (e.g. ~27-30 pts for a star who averaged
~30), proportional to `(1 - carryover_decay_weight)` — see
`test_builder_picks_up_decayed_prior_season_at_season_opener` and
`test_blend_converges_to_carryover_not_archetype_alone_at_cold_start` in
`tests/features/test_time_decay.py`. This should materially shrink the
season-opener bias for returning stars/regulars; it does nothing for true
rookies (no prior season exists, so it still falls back to the archetype
prior — correct behavior, not a gap).

## Verification
- `uv run ruff check nba/features/time_decay.py tests/features/test_time_decay.py` — clean.
- `uv run mypy nba/features/time_decay.py` — clean.
- `uv run pytest tests/features/test_time_decay.py -q` — 12/12 passed, including
  the planted-future-game leakage test and the limiting-behavior tests
  (converges to observed as `n` grows; converges to carryover component, not
  archetype alone, at `n -> 0`; age curve monotonic over a plausible range).
- All on the synthetic in-memory fixture; no real-DB job was run (none needed
  — this module has no multi-minute step).
