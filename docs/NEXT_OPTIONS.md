# Decision Brief — Next Wave of Options (2026-10-07)

Planning only. Grounded in the current code (`nba/props/minutes.py`,
`nba/props/config.py`, `research/sim/player_attribution.py`,
`research/features/player_possession_features.py`, `research/props/opponent.py`) and
the latest real-DB results (`docs/RESULTS_2026-10-08.md`). No implementation
in this document.

**Current state recap (what already failed, so we don't repeat it):**
- `MinutesModelConfig.use_game_context=False` — hand-set rest/b2b/travel/tanking
  fixed-effect nudges fixed the aggregate bias (0.572→0.037) but slightly
  *worsened* DNP log loss (0.3881→0.3908) and minutes MAE (6.185→6.222). Not a
  learned fit — just constants.
- `build_player_shot_rates(use_oncourt_usage=True)` — true on-court usage
  denominator REGRESSED points CRPS by +0.398 (CI excludes 0) vs. the
  whole-game-proxy default. Over-concentrates shot distribution once
  renormalized across the on-court five.
- `OpponentAdjustmentConfig.enabled=False` — box-score-derived opponent
  defense×pace multiplier WORSENED CRPS on every stat (pts 3.670→3.676, reb
  1.539→1.541, ast 1.297→1.300, fg3m 0.743→0.745).
- Archetype lineup-mix effect on PPP is real but tiny (R²≈0.016); not worth a
  rung-4 encoder yet.
- The sim's only proven player-prop win is rebounds; points/assists tie the
  season average. The documented routing table (sim wins
  cold-start/intermittent/erratic, season-avg wins "smooth" regulars) is the
  one unambiguous, already-shipped asset to build on.

**Standard acceptance rule used throughout:** paired per-player-game
bootstrap (n_boot=500, same convention as `PropsConfig.n_boot`) on CRPS
(primary) and threshold log loss (secondary), sliced by cold-start bucket
(Syntetos-Boylan volatility class / `n_poss`-based cold-start flag) in
addition to the overall sample. Accept only if the CI on (candidate −
baseline) excludes 0 in the *winning* direction, on ≥500 player-games
overall and reported (even if not required to pass) per cold-start bucket. A
result that beats overall but loses or is a wash on the cold-start bucket is
flagged, not shipped, per CLAUDE.md's "wins overall but loses cold-start is
not done."

---

## 1. Minutes model improvement

### Option A — Learned (not hand-set) game-context fit
**What:** Replace the fixed-effect constants in `MinutesModelConfig`
(`b2b_mu_adjust=-1.0` etc.) with coefficients fit by a small as-of ridge/GBM
regression of `mu`/`p_play` residuals on `GAME_CONTEXT_FEATURE_COLUMNS`
(rest_days, b2b, travel_miles_asof, tanking_incentive, games_into_season,
in_playoff_pos, in_playin), fit only on a chronologically-earlier calibration
split (same `cal_frac` pattern as `DispersionConfig`/`ConformalConfig`).
**Attacks:** the exact blind spot that killed Option "game context" last
time — the mechanism (rest/travel/tanking move who-plays) was plausible but
the hand-picked magnitudes/signs were wrong; a fit lets the data set sign
and size instead of a guess.
**Effort:** M (new fit step + walk-forward CV for the ridge/GBM; reuses
existing `build_minutes_features(use_game_context=True)` plumbing untouched).
**A/B test:** candidate = `predict_minutes` with learned coefficients vs.
baseline = current shipped `use_game_context=False` hurdle. Metric: DNP log
loss + minutes MAE/bias (the existing `MinutesEval` fields). Paired bootstrap
CI on (candidate − baseline) must exclude 0 in the improving direction,
sliced by cold-start bucket (rookies/low-`n_played_prior` vs. established).
**Regression risk:** this is literally retrying something that already
failed once with hand-set constants — if a learned fit still can't beat the
null, that's strong evidence rest/b2b/travel/tanking carry no *minutes*
signal beyond the shrinkage baseline (mirrors the win-prob rungs where the
same signals didn't beat Elo). Overfitting the ridge/GBM on a thin
calibration split is the concrete failure mode.

### Option B — Explicit blowout / garbage-time branch
**What:** Add a third hurdle branch — `P(garbage_time_reduction)` — using
projected pre-game point-spread (from the team-level sim/Elo, as-of) as a
feature: when `|projected_margin|` is large, shrink `mu` toward a learned
"garbage time" mean and widen `sigma`, rather than one Normal for all
closeness-of-game scenarios.
**Attacks:** the "blowouts" half of the noisiest-term claim directly —
currently a 35-point blowout and a 2-point nail-biter get the same `mu`/
`sigma` from history alone; margin is knowable pre-game (sim/Elo output) and
is a different information channel than rest/travel/tanking.
**Effort:** M (needs the projected-margin feature piped in as-of from
`nba.sim`/Elo; new branch in `predict_minutes`; a new as-of leakage test
mirroring `test_minutes_game_context.py`).
**A/B test:** same `MinutesEval` metrics, baseline = current hurdle, sliced
additionally by `|projected_margin|` tercile (own slicing, not just
cold-start bucket) to see if the gain concentrates where the mechanism
predicts. CRPS of the downstream points/reb/ast distributions (not just
DNP/MAE) should also be reported since minutes error propagates there.
**Regression risk:** projected margin is itself a noisy, model-dependent
input (Elo log loss 0.627, not near-zero error) — feeding a noisy predictor
into another noisy predictor can compound error rather than cancel it;
also only a small fraction of games are true blowouts, so the bucket may be
too small to clear the n≥500 bar without pooling multiple seasons.

### Option C — Foul-trouble / within-role variance decomposition
**What:** Split `std_minutes_given_played_prior` into two components — a
player's normal game-to-game variance vs. foul-trouble-specific truncation
(detect via games where the player's own `minutes` fell far below their
`mu` with a plausible same-game explanation, e.g. early personal fouls, if
derivable from `player_game_stats`/`possessions`) — and model the hurdle's
`sigma` as a mixture rather than a single Normal tail.
**Attacks:** the "foul trouble" half of the noisiest-term claim; a single
truncated Normal cannot represent a bimodal "normal game" vs. "fouled out
early" outcome, which the pure mean/MAE metric partially masks (MAE penalizes
big misses the same regardless of cause).
**Effort:** L — no existing foul-event column at the possession level
(`possessions` doesn't carry personal-foul counts per player; `fta`/`oreb`
are the only per-possession player fields available), so this likely needs a
new data/ingest column before it can be built, which crosses into
the archivist's territory and isn't a modeler-only change.
**A/B test:** same CRPS/MAE framework, but with an explicit slice on
"low-outlier-minutes" games (bottom decile of `actual/mu`) to check whether
the mixture model specifically improves the tail the single Normal was
blind to, not just the mean.
**Regression risk:** without a direct per-player foul-trouble signal, any
proxy (e.g. "minutes far below prior average") is circular — it's detecting
the thing it's trying to predict, risking a leakage-adjacent, in-sample-only
"improvement" that the no-leakage test would need to scrutinize hard; also
the highest effort of the three for a payoff that only helps a subset of
games.

**Recommendation: Option A.** It's the cheapest, reuses 90% of existing
plumbing (`use_game_context` flag, `MinutesEval`), and converts an already-
diagnosed failure mode (right mechanism, wrong hand-tuned numbers) into a
clean, fast A/B. Option B is the natural follow-up once A either ships or is
rejected. Option C should wait for an archivist ingest decision.

---

## 2. Usage redistribution

### Option A — Conditional (sit-aware) shot-share reallocation, not a denominator swap
**What:** Keep the shipped whole-game proxy `shot_share_prior` as the base
rate, but add a *second*, as-of "teammate-out multiplier": when a
projected-high-usage teammate (top-2 `shot_share_prior` on the roster) is
projected absent (`predict_minutes(...).p_play` below a threshold, or
excluded from the lineup input), redistribute a fraction of that teammate's
usage to the remaining on-court players' `_usage_weight` in
`research.sim.player_attribution`, weighted by their own `shot_share_prior`
(richer-get-richer, not uniform). This differs from the failed
`use_oncourt_usage` in that it does not change the *denominator* of the
proxy (which already works) — it only perturbs the weight vector
`_roster_weights_by_outcome` feeds the nested multinomial for games where
absence is actually projected.
**Attacks:** the stated blind spot exactly — "it doesn't reallocate usage
when a high-usage teammate sits" — without touching the thing that
regressed (the on-court/whole-game denominator choice).
**Effort:** M (new multiplier function in `player_attribution.py` plus a
historical reference rate — "how much did teammates' shot_share actually
rise, historically, in games a given high-usage player sat" — computed
as-of from `player_game_stats`/`player_possession_features`, same shrinkage
discipline).
**A/B test:** restrict the eval to games with ≥1 projected-out top-2-usage
player (a specific, reportable n, likely a few hundred per 4 seasons —
state the exact count). CRPS of points/PRA for the *teammates*, paired
bootstrap vs. the current flat `shot_share_prior` weights, CI must exclude 0.
Also report the full unrestricted-sample CRPS to prove no regression
elsewhere (since the multiplier is a no-op when no top-2 player is out).
**Regression risk:** the same over-concentration failure mode that killed
on-court usage could reappear if the reallocation multiplier is too
aggressive — a shrinkage-style cap (bounded redistribution, not winner-take-
all) is essential; also the eligible-game sample may be too small per
cold-start bucket to clear n≥500 without pooling, so this needs to be
reported honestly as a secondary, smaller-n result if so.

### Option B — Minutes-weighted, not shot-share-weighted, redistribution denominator
**What:** Separate "gets more shots when X sits" from "gets more minutes
when X sits" — model the two independently: `projected_minutes` already
responds to injury/role (via `MinutesModelConfig`/role_change), but
`shot_share_prior` never does. Add a lightweight as-of regression: team's
observed per-game shot-share dispersion (Gini-like concentration) as a
function of which top-usage players were in/out, fit at the team-season
level (not per-player), then apply the *team-level* redistribution curve
uniformly to all available players' weights.
**Attacks:** same blind spot, but via a coarser, lower-variance team-level
signal instead of a player-level multiplier — avoids the risk of fitting a
reallocation rule per (absent star, replacement) pair on too little data.
**Effort:** M (team-season level regression is cheap; integration point is
the same `_roster_weights_by_outcome`).
**A/B test:** identical framework to Option A (restricted to teammate-out
games), but also report whether the team-level curve generalizes better
across archetype clusters than Option A's player-specific one — i.e. compare
A vs. B head-to-head on the SAME restricted sample, not just each vs.
baseline, since both are candidates for the same slot.
**Regression risk:** team-level averaging could wash out exactly the
player-specific signal (e.g., the backup PG absorbing a star PG's usage is
different from a stretch-4 absorbing it) that makes redistribution useful in
the first place — may show CI including 0 even if a sharper rule would have
worked, giving a false "no signal" read.

### Option C — Usage reallocation as a learned residual on top of the sim, via rung-3.5 GBM head
**What:** Instead of touching `_usage_weight` directly, train a small GBM
that predicts each on-court player's realized shot-share *given the full
on-court five's identities and projected-absent flags* (a genuine
multi-player interaction feature), and use its output as a per-game
override of `shot_share_prior` only for games with a flagged absence,
falling back to the proxy otherwise.
**Attacks:** the blind spot most directly and with the most modeling power
(can learn nonlinear "who specifically absorbs whose usage" patterns a hand-
written multiplier can't), at the cost of being the first ML head in the
attribution path.
**Effort:** L (new training data prep — labeled shot-share-given-lineup —
new model artifact, registry entry, no-leakage test, and a fallback path).
**A/B test:** same restricted-sample paired bootstrap on CRPS as A/B, plus a
required comparison against Option A's simpler multiplier (if both are
prototyped) — CLAUDE.md's cheap-first principle says the GBM only earns its
complexity if it beats the multiplier by more than bootstrap noise, not just
beats baseline.
**Regression risk:** highest overfitting risk of the three (interaction
features on a relatively rare event — teammate absence — mean a small
effective training set); this is exactly the kind of "fancier model, same or
worse result" pattern already seen with `use_oncourt_usage` and opponent
adjustment, so it should not be attempted before A is tried and shown to
have a real, if small, effect to improve upon.

**Recommendation: Option A.** Smallest, most mechanistically targeted change
that doesn't touch the already-regressed denominator; it's the natural
"next thing to try" the routing/results doc points at, and its failure mode
(over-concentration) is well understood and cheaply guarded with a shrinkage
cap. Escalate to C only if A shows a real but capped-too-conservatively
effect.

---

## 3. Matchup specificity

### Option A — Position/archetype-vs-archetype factor (not team-vs-team)
**What:** Replace `OpponentAdjustmentConfig`'s team-level "opponent allows
X" factor with an archetype-level one: compute each defense's as-of
points/reb/ast allowed *broken out by the shooting player's archetype
cluster* (the same k=6 clusters from `docs/RESULTS_2026-10-08.md` §2/§3),
e.g. "Team Y allows 1.08x league-average rebounds specifically to
big-man-archetype players." Apply as a per-player, per-stat multiplier
analogous to `apply_opponent_adjustment`, but keyed on
`(opponent, player_archetype)` instead of `(opponent, stat)` alone.
**Attacks:** the literal "who guards whom" ask, approximated at archetype
granularity (no play-by-play defender-assignment data exists, so true
man-to-man is out of reach) — this is more specific than the team-level
signal that already failed, on the theory that averaging across archetypes
is what diluted the real signal into noise last time.
**Effort:** M (reuses `research.props.opponent`'s SQL pattern, adds a `GROUP BY
archetype_cluster`; needs the archetype assignment already computed for
`model_routing.py` joined in as-of).
**A/B test:** same paired bootstrap CRPS framework as the original
opponent-adjustment A/B (`tests/props/test_opponent.py` pattern), baseline =
current shipped (`enabled=False`, i.e. no adjustment at all — the thing that
already won). Must clear the same min-games-for-factor honesty guard,
AND must be compared against the already-failed team-level version on the
SAME eval window to confirm archetype granularity, not re-running on
different data, explains any improvement. Report both overall and
cold-start-bucket CIs.
**Regression risk:** this is retrying a documented failure (team-level
opponent adjustment) at one more level of granularity — if the mechanism
really is "box-score-derived defense/pace is just a noisy proxy for a
quantity the shrinkage-based season rate already captures," splitting by
archetype only shrinks the per-cell sample size and could make the noise
problem worse, not better. Needs a hard pre-commit: if the per-cell
`opp_games_played_prior` rarely clears `min_games_for_factor`, don't ship
regardless of the point estimate.

### Option B — Possession-sim-level matchup (rung-3 off/def tilt by zone)
**What:** Instead of a post-hoc multiplier on the prop mean, push matchup
specificity into the possession sim itself: let each team's as-of
`zone_fg_pct` (already computed per-player in
`player_possession_features.py`) be tilted by the *opponent's* as-of
zone-specific defensive rate (e.g., opponent allows above-league-average
rim FG%), analogous to `tilt_outcome_probs` in `nba.sim.possession_model`
but at the zone level rather than the whole-game PPP level.
**Attacks:** matchup specificity mechanistically closer to the sim's own
language (zone outcome probabilities) rather than a bolt-on multiplier on
the final mean — plausibly avoids the "double-noise" problem of multiplying
two already-noisy numbers (team-level opponent factor × player mean) by
instead moving the thing the sim already samples from.
**Effort:** L (touches the core possession/zone probability machinery in
`nba.sim.possession_model`/`player_attribution`, needs its own
reconciliation test against the existing engine's validated team-level
output, i.e. `test_team_totals_match_engine_team_sim` must still hold).
**A/B test:** CRPS/threshold-log-loss on points/reb specifically (where zone
mix matters most), baseline = current unadjusted sim, required to also not
regress the one stat that currently wins (rebounds, −0.014 CI
[−0.024,−0.003]) — a regression gate on rebounds specifically, not just an
overall average, since that's the one proven asset.
**Regression risk:** highest-effort, highest-blast-radius option — touches
code the rebounds win already depends on; a zone-level defensive tilt
fit on sparse per-zone opponent history could easily be noisier than the
team-level factor that already failed, and the rebounds-regression gate
makes failure costly to not just ignore.

### Option C — Opponent factor shrunk toward 1.0 by the model's own measured usefulness per stat (meta-shrinkage)
**What:** Rather than a binary enabled/disabled, blend in a continuous
Bayesian-shrinkage way: `adjusted_factor = 1 + w * (combined_factor − 1)`
where `w` is fit (walk-forward, per stat) to be the weight that minimizes
backtest CRPS — i.e., let the data find that `w≈0` for most stats
(consistent with the current failure) but possibly `w>0` for a specific
stat/bucket where the earlier all-or-nothing A/B masked a small positive
effect inside an overall-negative average.
**Attacks:** matchup specificity, cautiously — this treats "opponent
adjustment helps or doesn't" as a continuous question instead of the
on/off toggle that already returned "doesn't, on average," checking
whether it was thrown out too bluntly (CLAUDE.md's market-as-prior shrink
pattern, applied to this signal instead of the market).
**Effort:** S (small addition on top of existing `OpponentFactors`/
`apply_opponent_adjustment` — just a fitted scalar per stat/bucket).
**A/B test:** fit `w` per stat on a chronologically-earlier calibration
split; compare `w`-weighted factor vs. baseline (`w=0`, current shipped) on
a later holdout, paired bootstrap CRPS, CI must exclude 0 — and report the
fitted `w` itself (if it's statistically indistinguishable from 0, that's
the honest "still no signal" answer, not a reason to force it in).
**Regression risk:** cheapest option, but also the most likely to just
reproduce the existing "no signal" finding with extra steps — if `w` fits to
~0 everywhere, this option correctly produces a negative result, which is
useful to know but isn't a win; there's also a risk of fitting `w>0` on
calibration-split noise that doesn't hold on the holdout (classic
overfitting on a small-weight parameter), guarded only by requiring the
holdout CI to exclude 0 before shipping.

**Recommendation: Option A.** It's the most literal interpretation of
"who-guards-whom at archetype granularity" with moderate effort, is directly
built on the already-shipped archetype clusters (zero new infra), and has a
built-in honesty guard (`min_games_for_factor` per cell). Option C is the
cheap complementary check to run alongside it. Option B should wait — it's
the highest-risk touch to the one subsystem (rebounds) that currently wins.

---

## 4. The modeler's first innovative representation project

### Option A — Player co-occurrence / lineup embeddings (word2vec-style on possessions)
**What:** Train a lightweight skip-gram-style embedding over
`possessions.off_players`/`def_players` 5-man groups (CLAUDE.md's own
"Sequence embeddings (word2vec-style)" borrowed-technique entry), as-of
fit incrementally per season so no future-game context leaks into an
embedding used for an earlier game. Output: a per-player vector usable as a
cold-start prior (kNN similar-player, method 5 in the cold-start ladder) and
as a lineup-interaction feature richer than the archetype-cluster counts
already shown to be "real but negligible" (R²≈0.016) in §3 of the results
doc.
**Attacks:** blind spots 2 and 3 simultaneously — gives usage-redistribution
a "which teammate is a similar usage-profile replacement" signal, and gives
matchup-specificity a continuous player-similarity space finer than the k=6
archetype buckets that already showed a small but real non-additive effect.
**Effort:** L (new as-of-safe training loop, versioned embedding artifact in
the local registry, no-leakage test proving an embedding trained through
date D doesn't shift when future games are added).
**Acceptance test:** not a direct CRPS production A/B by itself — first
gate is a representation-quality check (does kNN-in-embedding-space predict
held-out rookie first-season rates better than the current archetype-prior
cold-start method, CLAUDE.md cold-start method 5 vs. method 2, paired
bootstrap on rookie-only CRPS, CI excludes 0). Only promoted to feed §2/§3
options if it clears that bar.
**Regression risk:** word2vec-style embeddings need a lot of co-occurrence
volume per entity to be stable; with ~1.05M possessions but only ~891
players and highly skewed playing time, rarely-used players' embeddings may
be too noisy to beat the already-working position/archetype prior — same
failure shape as `use_oncourt_usage` (fancier representation, same or worse
result) if not validated before being wired into anything downstream.

### Option B — CUSUM/changepoint-based "effective sample" reweighting for shot-share and opponent factors
**What:** Formalize what §1/§2/§3's "teammate out" and "role change"
detection do ad hoc today into one reusable preprocessing primitive: a
Bayesian online changepoint / CUSUM detector (CLAUDE.md Phase-2 technique,
partially built already in `nba.props.role_change`) applied not just to
minutes but to `shot_share_prior` and the opponent-allowed series, emitting
an as-of "effective n" that downweights pre-changepoint history uniformly
across every downstream consumer (minutes, usage, opponent factor) instead
of each one reinventing its own ad hoc flag.
**Attacks:** blind spot 1 and 2's shared root cause — every one of the
three "regressed" experiments (game-context constants, on-court usage,
opponent adjustment) implicitly assumed history is stationary; a shared
changepoint-aware effective-n could let the SAME underlying signals (that
already failed as unconditional multipliers) work only in the post-
changepoint window where they're actually informative (e.g., opponent
adjustment right after a trade reshuffles a defense, not as a universal
rule).
**Effort:** M (extends the already-existing `nba.props.role_change` module
rather than building from scratch; "effective n" output needs a clean
interface contract so minutes/usage/opponent consumers can adopt it
independently).
**Acceptance test:** re-run the exact three already-failed A/Bs
(game-context, on-court usage, opponent adjustment) restricted to rows
flagged by the changepoint detector as "recently changed," paired bootstrap
CRPS vs. baseline on that restricted sample, CI must exclude 0 — a direct,
falsifiable test of the "it only works near changepoints" hypothesis against
three known negative results, reported with the restricted sample's n
explicitly (likely small — state it).
**Regression risk:** the restricted-sample n could be too small to produce
a non-degenerate CI (CLAUDE.md's own power-concerns risk #2) and this option
could easily reproduce the "no signal" finding a third time with extra
machinery; also role_change-flagged rows overlap with the cold-start bucket
already shown to be the sim's strength, so a positive result here risks
double-counting a win already captured by the routing table rather than
revealing new signal.

### Option C — Reference-class (survival/hazard) minutes-ceiling feature from role/depth-chart position
**What:** Build a leakage-safe "depth-chart rank" feature (as-of ranking of
each team's players by trailing `avg_minutes_given_played_prior` /
`starter_rate_prior`, already computed in `nba.props.minutes`) and, per
CLAUDE.md's "Survival/hazard models... time until rest day" borrowed
technique, a hazard-style "P(this player's minutes get capped this game |
depth-chart rank, rest, injury news)" feature as a first-class preprocessing
output, rather than embedding this logic implicitly inside the minutes
hurdle model's fixed constants.
**Attacks:** blind spot 1 directly (minutes is the #1 blind spot) by giving
the minutes model a genuinely new, structurally motivated input (depth-chart
pressure) instead of re-trying the same rest/b2b/travel signals a different
way — complements rather than duplicates §1 Option A/B.
**Effort:** S/M (pure SQL feature on top of existing as-of columns, no new
data needed, reuses `nba.props.minutes`'s own window machinery).
**Acceptance test:** feed the depth-chart-rank + hazard feature into §1's
Option A learned-fit pipeline as an additional candidate column (not a
separate model) and require it to improve the fit's walk-forward DNP log
loss / minutes MAE over Option A without it, paired bootstrap CI excluding 0,
sliced by cold-start bucket — i.e., this option's test piggybacks on and is
subordinate to whichever minutes option is chosen in §1, which keeps the
comparison honest (one new variable at a time).
**Regression risk:** depth-chart rank by trailing minutes is highly
collinear with `play_rate_prior`/`starter_rate_prior` already in the model,
so it may show zero marginal lift (overlapping, not novel, information) —
the test must report the marginal CI, not just a standalone one, to avoid
claiming credit for signal the shrinkage baseline already has.

**Recommendation: Option C.** Lowest effort, directly serves the #1 priority
(minutes) with a genuinely new structural signal rather than retrying a
failed family of features, and its test is cheap (piggybacks on §1's
pipeline) so it can be evaluated in the same pass as the minutes work. Option
B is the more ambitious, higher-payoff bet if the team wants the modeler
to tackle the shared "stationarity assumption" root cause behind three past
failures, but it needs the manager's sign-off on spending effort to possibly
reproduce a third negative result. Option A (embeddings) is the right
long-horizon investment but should wait until B or C either succeeds or is
exhausted, consistent with CLAUDE.md's "cheap-first" rule.
