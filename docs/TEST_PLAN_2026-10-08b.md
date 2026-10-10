# Test plan 2026-10-08b — pre-registration

Written BEFORE any test below is run. Follows `ACCEPTANCE_CRITERIA_2026-10-08.md`
(standing FDR plan) and `HOLDOUT_ACCESS_LOG.md`. Deviations go to `docs/ESCALATIONS.md`
before the run. Every result appends rows to `TEST_LEDGER.md` (next id T062), including
losses and exclusions.

## Global rules (apply to all four families)
- CIs: clustered by `game_id` (`paired_score_delta_ci(..., cluster_ids=game_id)`), seed
  logged, n_boot >= 2000. Per-row-only CI => PROVISIONAL regardless of outcome.
- Raw numbers (per-cell point, lo, hi, n, n_games) saved as JSON/CSV in the repo, not just
  markdown. Otherwise PROVISIONAL permanently.
- Per-player dependence: also report a player-clustered sensitivity CI. A result whose
  verdict flips under it is downgraded to PROVISIONAL (the sensitivity is not in the BH family).
- BH at q=0.05 within each family below, m fixed here. Families are never pooled or split
  after the fact. p-approx = normal approx from the CI (`se=(hi-lo)/3.92`).
- Each test reports MDE = 2.8 x SE. If MDE > 2x the practical floor, the non-significant
  verdict is "underpowered, cannot conclude", never "no effect".
- Holdout: `season=2025` has no virgin status for props. Any tuning uses `season<2025`
  walk-forward only. A model never previously evaluated may touch 2025 once, logged first;
  that touch is "first touch for this model", still not independent confirmation. Confirmation
  comes only from (d).

---

## (a) Time-decay carryover tuning — Family TD, m=6

Candidate: `research/features/time_decay.py` (tuned) vs current production as-of features
(equal-weight pooling). Metric: CRPS delta (tuned - current), paired per player-game.
Cells: {pts, reb, ast} x {first-15 team games, games 16+}.

Tuning protocol (fixed now): grid over `half_life_seasons`, `carryover_decay_weight`, `k`
(peak_age and width stay default). Select by walk-forward CV on `season<2025` only, tuning
seasons 2022-23/2023-24, validation 2024-25. Selection objective: mean first-15 CRPS summed
over the 3 stats. Grid size and chosen point logged; no re-tuning after seeing 2025.
Selection happens on a different season than the reported test, so the reported delta is not
a best-of-grid max.

Decision rule, all required to wire in (flag on):
1. First-15 cells (3 tests, in BH m=6): at least one stat has clustered CI upper < 0 and
   survives BH, with |delta| >= 0.005 CRPS.
2. No-regression guard on games 16+ (3 cells, in m=6 for reporting; decision uses the bound):
   CI upper <= +0.003 CRPS for all 3 stats. Failure => REJECT or restrict to first-15 via gate.
3. Reported on validation 2024-25 AND the single 2025 touch (pre-register row in the holdout
   log before it). Both must agree in sign for the winning stat.
Min n: >= 500 player-games per first-15 cell (~15/82 of data, so check). Below that: exclude
that cell from m and report "insufficient power".
Failure modes: all-3-flat => document null, flag stays off. Any win is PROVISIONAL until (d)
replicates it forward (first-15 games of 2026-27 are the natural confirmatory slice).

## (b) Usage redistribution 2A/2B re-run — Family UR, m=8

Precondition (checked and logged BEFORE the A/B; if unmet, no test is run and nothing is
ledgered as a null): historical injury reports backfilled with (i) coverage >= 80% of
team-games in the window, (ii) every report timestamp <= tip-off minus 60 min (leakage
test; use last report before cutoff), (iii) player_id match 100% or unmatched rows reported.
Mechanism-fires check: restricted sample n > 0.

Metric: CRPS delta (mechanism on - off), paired, restricted sample = teammates of a
rotation player (prior avg minutes >= 20) listed OUT. Cells: {2A, 2B} x {pts, reb, ast, fg3m}.
Status mapping: OUT only in primary. Doubtful/Questionable/Probable = secondary sensitivity,
outside the family.

Decision rule: ship a mechanism x stat only if clustered CI upper < 0, BH-survives (m=8,
q=0.05), |delta| >= 0.005, AND full-sample (unrestricted) CI upper <= +0.003 (no dilution
regression). Min n: >= 500 restricted player-games and >= 150 distinct games per stat; else
excluded and reported underpowered. Tuning (if any) on `season<2025`; 2025 one touch, logged first,
first-touch label. If 2025 has weak report coverage, say so; do not impute.
Prior-session 2A/2B nulls (T047, n=0) are not part of this family: they were inert, not tested.

## (c) Rung-4 step-heads — Family R4H (heads) m=5, Family R4R (router) m=30

Training: Colab, `season<2025` only; hyperparameters chosen by walk-forward CV inside that
pool; `HOLDOUT_SEASON` must be set explicitly to 2025 (not the default `max`). Seed logged.
Exactly ONE evaluation pass on `season=2025` yields both families below. Pre-register the
holdout-log row (model, commit, config hash, seed) before the pass. If anything is retrained
after seeing it, the touch is spent and later passes are "repeated-use".

R4H: per head (outcome, zone, make, rebound, duration), holdout loss delta (head - frequency
baseline), paired by possession, clustered by game_id. Baseline = training-pool marginal class
frequencies (duration: training-pool mean/variance under the head's own loss). Note: beating
marginal frequency is a weak bar; also report a context baseline (frequency conditional on
period/score-diff bucket) as non-family information. A head "adds information" iff CI upper
< 0, BH-survives m=5, and |delta| >= 0.002 nats (or 0.5% relative for duration).
A head that fails stays out of any downstream sim use.

R4R: acceptance-criteria grid, unchanged: 3 stats x 5 volatility buckets x 2 incumbents (sim,
season-avg) = 30 comparisons, all reported. Rung-4 enters the router for a cell only if it
beats BOTH incumbents with BH-corrected (q=0.05, m=30) CI excluding 0 in the improving
direction. Cells with < 150 player-games: excluded from m, directional only. Added fixes:
(1) all three arms run at the same `n_sims` = 2000 (the 500-vs-2000 mismatch would confound);
(2) bucket assignment is as-of (volatility from data before the game);
(3) pooled CRPS vs savg across buckets also reported, for continuity with Family A.
Power note to state in advance: BH at m=30 needs best p <= 0.0017; with small buckets most cells
will be underpowered. Default expectation is "no cell enters the router"; that is an acceptable
outcome and is reported as such, not as a failure to find an effect. Any router entry is
PROVISIONAL until (d).

---

## (d) Forward 2026-27 evaluation of production models — Family FWD, m=4

What counts as confirmatory (all required):
1. Model frozen before the first forecast date: registry version, git SHA, config hash, seed
   recorded in the experiments table. Predictions written to `prop_predictions` with
   `made_at` before tip-off; never overwritten. As-of rate updates are allowed; hyperparameter
   or code changes are not. Any change = new model id with its own clock and n restarted;
   its backfilled predictions on 2026 games are "repeated-use".
2. 2026 games are never used in tuning, CV, or router fitting until the pre-registered final
   evaluation is done. After it, 2026 is burned and 2027 becomes next holdout.
3. Router (volatility buckets) and calibration maps use data from `season<=2025` only.

Family FWD (m=4), each clustered by game_id:
- F1 reb: sim-routed minus season-avg CRPS (replication of T001)
- F2 ast: same
- F3 pts: same
- F4 win-prob: Elo minus home-court-only log loss (per game; no closing lines available)
Secondary, pre-specified acceptance targets (not in BH): mean bias within +/-0.5 with CI
including 0 (pts/reb/ast, n >= 500 player-games), 80% interval coverage in [75%, 85%].

Minimum n and looks:
- Interim looks (at 100, 300 games, monthly) are descriptive only: logged to the ledger as
  `forward-interim`, excluded from BH, may not change any model or router. Sole exception: a
  safety stop if bias CI excludes +/-0.5 with n >= 500 player-games, escalated, not auto-acted.
- Single confirmatory look: at >= 600 games ingested for 2026-27 (about half season) AND
  >= 8,000 player-games per stat; if not reached, report "insufficient n". A second and final
  look is allowed at season end with q split 0.025/0.025 (BH within look). Nothing else.
- Power statement required: at 600 games pts MDE is about 0.044 CRPS (T003). A rebounds-sized
  effect (0.014) in pts would need on the order of 5,000+ games, i.e. not reachable in one
  season. Therefore pts "not different" is never claimable from F3; report MDE, not "tie".

Rollover rule: when 2026-27 has >= 20 games ingested, record in `HOLDOUT_ACCESS_LOG.md`
that `season=2026` is the canonical props holdout and `season=2025` returns to the tuning
pool (also update `--holdout-season` default; maintainer's call, escalate if not done).
Check at start of every session.

Verdict mapping: CONFIRMED only if BH-survives in FWD with clustered CI, floor 0.005 CRPS,
and the direction matches the earlier claim. Failure to replicate T001 downgrades reb from
PROVISIONAL to REJECTED for routing purposes.

---

## (e) Fair rematch: DNP-aware sim vs DNP-aware season average — Family FR, m=3

Written BEFORE the first run; module `research/eval/fair_rematch.py` carries the same text in its
docstring (this section wins on disagreement). Motivation: `DNP_AUDIT_2026-10-08.md` — DNP rows
(minutes NULL, stats 0, 18.6%) bias the historical season-avg baseline low on played rows and
deflate the sim's shot-share / reb / ast rates, so earlier sim-vs-savg verdicts compared two
differently contaminated estimands. This is a re-test of T001-T003-type questions, not a new
tuning exercise. Both sides get their DNP-aware form; nothing is tuned.

Setup (fixed): scored season 2024 only (2025 is never scored; code refuses it); ~600 games
sampled evenly (linspace over date-sorted game ids), identical set for all arms; rates use full
prior history; `n_sims=1000`, seed 0; minutes = the sims' projected minutes (no actual minutes).
Rosters = box-score row membership, as in the existing evals (identical across arms).
Arms: A = old sim as shipped; B = sim `played_only=True` + current-season-only shot rates
(`TimeDecayConfig(carryover_decay_weight=1.0)`; the reb/ast builder has no time-decay path, so
for reb/ast B is played_only with full-history rates — a stated asymmetry); S = fair season
average: played-only, recency-weighted, half-life 10 played games, career-to-date, via the same
`recency_weighted_dist` as `nba/props/forward.py`.

Primary family (m=3): B vs S, played rows only, CRPS delta (B - S), cells {pts, reb, ast};
clustered by `game_id` (n_boot 2000, seed logged); p from normal approx to the CI; BH q=0.05.
Decision: "B beats S" iff CI upper < 0, BH-survives, delta <= -0.005; "S beats B" iff CI lower
> 0, BH-survives, delta >= +0.005; else no verdict (labelled "underpowered, cannot conclude" if
MDE = 2.8 SE > 0.010). Player-clustered CI reported per primary cell; a verdict that flips under
it is PROVISIONAL (outside the family). Any result is PROVISIONAL until FWD replicates.

Descriptive, outside the family (no verdicts, no BH): B vs A and A vs S on played rows; all-rows
results with the unconditional mixture (B and S conditional dists mixed with a point mass at 0 of
weight 1 - p_play, p_play = shrunk as-of played share of the prior 20 rows; A as shipped); B vs S
sliced by the played-only SB bucket (min_games=10, buckets with < 30 played rows omitted).
Min n: per cell, report n and n_games; raw cells saved to `docs/fair_rematch_2024.json`.
Ledger: log as next free T-ids, including a null/loss.
Command: `uv run python -m research.eval.fair_rematch --db nba.duckdb --n-sims 1000 --sample-games 600`.
