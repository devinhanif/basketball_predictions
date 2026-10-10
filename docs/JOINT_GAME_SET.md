# Joint game-set model (set transformer) -- design and PRE-REGISTRATION

Status: written 2026-10-08 BEFORE any Colab run of `joint_game_set`. The keep rule below is applied
by `research/eval/joint_game_set_eval.py` (its module docstring repeats it) and is not changed after
seeing results. A negative result is reported as a negative result. Season 2025 is never loaded
(both the exporter and the job raise); this experiment never touches the frozen holdout.

## Question

`nba/parlay/joint.py` models a same-game parlay with a Gaussian copula whose correlations are POOLED
by relation (same player / teammate / opponent) and stat pair, estimated once from historical
residuals. It cannot represent dependence that is non-linear or state dependent: a blowout that
cuts every starter's minutes (and flips sign with the margin), teammates sharing a fixed pool of
usage, a star night that co-moves with a win. On 2023-24 the Gaussian copula beat independence by
log loss -0.00053 [-0.00106, -0.00003] (`docs/PARLAY_ENGINE.md`). Does a model that sees the WHOLE
game at once (every rostered player of both teams plus the game context) capture more of that
dependence, with calibrated marginals?

## Data (`research/features/game_sets.py`, ~8 s, read-only DB)

One set per game (seasons 2022-2024, 3,951 games):

* **Player tokens**: every box-score roster row of both teams (played AND DNP rows; max 16 per team,
  32 slots). Feature vector (51): home/away, history counts, play rates over the last 5/10/20 rows,
  gaps, exponentially weighted minutes (half-lives 3/10/40), minutes sd, starter rate, EW means and
  sds of pts/reb/ast/fg3m, per-36 rates, pre-tip injury-report status + absence streaks (production
  `pretip_status` / `absence_features`), teammates' out / flagged minutes, position flags, height,
  draft slot. Uniform in kind for every row whether or not the player played.
* **Game token** (13): injury-Elo logit, the `nba.parlay.game_model` margin and total mean/sd, rest
  days, back-to-back flags, games played, playoff flag, season phase.
* **Labels** (never inputs): played flag, pts/reb/ast/fg3m per played player, game margin and total.
* **Targets live in normal-score (PIT) space** relative to a pre-game marginal. 2023-24: the production
  context-residual quantile grid (`reports/context_residual/oof_context_residual.parquet`, walk-forward
  OOF). 2022 (no OOF exists; optional pre-training only): a recency Normal built as-of from the same
  token features. The export holds the PIT bounds `(F(y-0.5), F(y+0.5))` (the job re-randomizes them
  every epoch) and a fixed-seed draw used for validation and test. Outcomes beyond the extended grid
  ends have PIT unknown but extreme; they are spread uniformly over the outer 1% of the unit interval
  instead of a point mass at the clip (a point mass is a degenerate target for a density model).
  Margin and total targets are `(y - mu) / sd` from the game model fitted excluding season 2024.

No-leakage, enforced by tests (`tests/features/test_game_sets.py`):

1. every history feature uses the player's strictly earlier dates (planted-future-game proof);
2. flipping played/DNP and the whole stat line of a game does not change ANY token feature of that
   game (the set cannot reveal who played);
3. report rows stamped after the tip-off proxy are ignored;
4. the production OOF grid (played rows only) is used ONLY to standardize labels, never as an input: a
   feature present only for players who played would leak who played;
5. the data-source embedding is a per-GAME flag (season >= 2023), not a per-token indicator;
6. the job refuses season 2025.

Residual risk, stated plainly: the ROSTER ROW LIST of a game (including inactive and two-way rows) is
taken from the realized box score. It is the game-day roster and nearly known pre-tip, but not
point-in-time. Feature values do not depend on it.

## Model (`research/colab/jobs/joint_game_set/joint_game_set.py`)

* **Encoder**: pre-norm transformer over `[game token] + 32 player tokens` with key-padding mask;
  home/away and data-source embeddings; permutation equivariant (tested), padding invariant (tested).
* **Joint head**: a mixture of `M` low-rank-plus-diagonal Gaussians over the normal-score vector
  `z` (4 stats x 32 slots + margin + total): `z | state k ~ N(mu_k, D_k + L_k L_k^T)`. State weights
  come from the game token; `mu/d/L` per token. The discrete game state makes the dependence
  non-Gaussian and context dependent (a blowout state couples the margin with every starter's
  stats). The NLL is exact (Woodbury + determinant lemma) with missing dims (DNP, no marginal)
  marginalized exactly (tested against `scipy.stats.multivariate_normal`).
* **Auxiliary head**: `p_play` per token (BCE). Loss = per-dim joint NLL + `lam` * BCE.
* **Sampling** gives joint draws of every leg of a game; the same draws serve all parlays of that
  game (common random numbers), as `JointModel` does.
* **Variants** (all from the same trained network): `set_full` (its own marginals: per-player
  corrections to the production marginals) and `set_copula` (each dim transformed through its own
  mixture marginal CDF, then the production marginal: dependence only).
* **Regularization**: dropout 0.3-0.5, AdamW weight decay 1e-3..1e-1, grad clip, early stopping,
  4-12 states, rank 2-8, small init near independence (`mu=0, d=0.95, L~0`).
* **Selection** (24 random-search trials, `full`): early stopping and cross-trial selection use ONLY
  a time-ordered slice of season 2023 (the last 20% of games by date), criterion = per-dim joint NLL
  + 0.2 * BCE (a fixed weight so trials are comparable). Search space includes whether to pre-train on
  2022 (with its source flag) and its loss weight. Final = 5 seeds fit through all of 2023 for
  the best epoch count; predictions pool the 5 models (mixture ensemble: pooled draws).
* **Secondary arm**: monthly walk-forward refits during 2024 (warm start from the static models, 8
  epochs at 0.3x lr on all games dated before the block).

## Evaluation (pre-registered)

Units: the synthetic same-game parlays of `nba/parlay/joint_eval.py` for season 2024, regenerated with
the same generator, thresholds, seed (20261008), rng stream and 6 per game (the 2023 draws are
replayed so the 2024 parlays are the very events `joint_eval` scores), with the same walk-forward
game-model parameters (fitted excluding 2024) and the cross-fit correlation matrix estimated on
2023. Parlay legs are built only on players who played, so joint probabilities are conditional on
playing for every engine. Engines: independence, Gaussian copula (published 10k draws), Gaussian copula
at 100k draws (`p_gaussian_hi`, the COMPARATOR: it has the same total Monte-Carlo budget as the set
model, so sampling noise cannot favor either), t copula (reported), `set_full`, `set_copula`, and the
walk-forward variants.

**Keep rule.** The set model is KEPT (registered as a candidate; eligible for a later, separately
pre-registered confirmatory touch; never auto-promoted) iff ALL hold on 2024:

* **R1 joint log loss**: mean(set_full - Gaussian copula @100k) < 0 and the 95% game-clustered paired
  bootstrap CI (2000 resamples) upper bound < 0.
* **R2 marginal calibration not worse**: ECE(set_full) - ECE(production marginal) <= +0.005 (point
  estimate; 10 equal-width bins; ladder legs with production P in [0.15, 0.85], the same legs for both).
* **R3 game-level CRPS non-inferior to `game_model.py`**: the upper 95% CI bound of
  mean(CRPS_set - CRPS_normal) is <= +0.05 points for the margin AND for the total (non-inferiority
  margin fixed in advance, about 0.8% of the margin CRPS).

Anything else is NOT KEPT. Never used to keep, select or tune: Brier; `set_copula`; the walk-forward
arm; slices (parlay size 2/3/4; props-only, prop+result, teammates, opponents, same-player, game-only);
reliability tables by size and mix; the z-space joint NLL gain over the copula (nats per game, paired
bootstrap; the most powerful test of dependence but not the decision metric); the `p_play` head vs the
as-of 20-row play-rate heuristic. If the primary fails and a descriptive variant looks better, nothing
is kept on that basis.

**Power note.** The Gaussian copula's whole gain over independence was -0.00053 (CI width about 0.001) on
~15.8k parlays. 2024 alone has about 7.9k parlays in 1,315 games, so R1 can only be met by an effect of
that order or larger. The honest prior is that a small-data (about 2.4k training games) neural joint model
does NOT clear it; the z-space NLL and the slices say where dependence is or is not found.

## Run

```
# local, once (several minutes; read-only DB; produces data/colab/joint_game_set/*)
uv run python -m research.features.game_sets --db nba.duckdb --out-dir data/colab/joint_game_set
uv run python -m research.eval.joint_game_set_eval --stage --db nba.duckdb --stage-dir data/colab/joint_game_set
# stage to Drive (runs the producers above if missing/stale), then press Run all on a GPU
make colab-push JOB=joint_game_set
# after the run
make colab-status JOB=joint_game_set
make colab-pull JOB=joint_game_set
uv run python -m research.eval.joint_game_set_eval --run data/colab/runs/joint_game_set/<run_id> --stage-dir data/colab/joint_game_set
```

`NBA_BUDGET`: `full` (default, thorough: 24 trials, 5 seeds, 100k draws, walk-forward; estimated 60-100
min on a T4/L4 and 40-60 min on an A100, ESTIMATES from CPU step timings of about 2.5-5 s/epoch, not
measured on a GPU; a measured projection prints after the first trial), `fast` (about 25-40 min),
`smoke` (CPU test). The job prints the device and GPU name. Crash safety: mid-fit state every few epochs,
every trial, final model, walk-forward model and prediction chunk is checkpointed to
`<run folder>/checkpoints/<hash>/`; re-run the notebook to resume. Small artifacts first;
`metrics.json` is written last (`complete: true`).

## Artifacts

`metrics.json`, `best_config.json`, `search_log.json`, `training_log.json`, `data_summary.json`,
`weights.pt` (5 state dicts + scalers), `parlay_preds.parquet`, `single_preds.parquet`,
`game_preds.parquet`, `play_preds.parquet`. Scoring writes `report.md` and `result.json` next to them.

## Known limitations

* The production OOF is played-only, so production marginal summaries cannot be token inputs; the
  network corrects the marginals using its own as-of features. Scoring DNP rows in
  `context_residual` would allow it (a request for the props owner, not done here).
* Training sets are small (2,365 games with 2022; 1,047 without). 2022 pre-training uses a cruder
  recency-Normal marginal and is selected on the 2023 slice only.
* The z-space NLL compares against a copula whose PSD repair is an eigenvalue floor (descriptive only).
* Synthetic parlays are a proxy for real Kalshi parlay legs; absolute joint calibration is
  conditional on playing.
