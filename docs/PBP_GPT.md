# Basketball GPT (decoder-only transformer over play-by-play) - design and PRE-REGISTRATION

Status: written 2026-10-08 BEFORE any Colab run of `pbp_gpt`. The keep rules below are applied by
`nba/eval/pbp_gpt_eval.py` (its module docstring repeats them) and are not changed after seeing
results. A negative result is reported as a negative result. Season 2025 is never loaded (exporter,
job and evaluator all raise); this experiment never touches the frozen holdout.

## Question

Earlier sims (`nba/sim`, rung 3 step heads, rung 4 team-only step heads) lost to the recency average
for pregame player props and to injury-Elo for win probability (`docs/RESULTS_2026-10-08.md`,
`docs/TEST_LEDGER.md`). Those models were pregame and team-level. A sequence model that conditions
on the actual events of a game so far has a different advantage: IN-GAME state. Does a decoder-only
transformer trained to predict the next play-by-play event, sampled forward from a mid-game state,
beat a standard live baseline on win probability and on remaining-game player lines, and is it
usable for pregame full-game sampling?

## Data and tokenization (`nba/features/pbp_tokens.py`)

Seasons 2022-2024, regular season and playoffs, from `data/pbp/*.parquet` (V3 play-by-play) joined
to the DB tables `games`, `player_game_stats` (starters flag, as-of history), `player_availability`
and the injury-Elo out-of-fold probabilities. Splits: **train** = 2022 + first 80% (by date) of 2023;
**val** = last 20% of 2023 (early stopping / trial selection, next-token loss only); **report** = 2024.

One sequence per game, ~1,800 tokens (5%-95%: 1,650-2,000; context 2,304). Grammar:

```
HEADER (loss-masked)   BOS M_HOME TEAM_h M_AWAY TEAM_a [M_PLAYOFF] ELO_b RAT_b RAT_b REST_b REST_b
                       M_OUT_H <<=6 OUT players> M_OUT_A <..> M_START_H <5> M_START_A <5>
                       M_ROST_H <<=10 rotation players> M_ROST_A <..> GO
STATE BLOCK (forced)   M_STATE SC_<diff bucket> PER_<period> CLK_<30 s bin> BON_<bonus state>
                       [M_LINEUP <5 home on-court, sorted> <5 away on-court, sorted>]
EVENT (learned)        DT_<k>  EVT_<kind>  payload...
```

* **Vocabulary** (~215 structural tokens + one token per player): specials 4, markers 11, `TEAM_*` 31,
  `ELO_*` 24, `RAT_*` 12, `REST_*` 4, `DT_*` 16, `SC_*` 47, `PER_*` 6, `CLK_*` 24, `BON_*` 4, `EVT_*` 45,
  `NEW_*` 16, plus `P_<id>` for players with >= 10 played games in the TRAIN split (about 600-700;
  total vocabulary roughly 850-950, far below the 8k target). The exact table is `vocab.json`
  (`Vocab.to_json`).
* **Event tokens** carry side (home/away), kind, zone and assisted flag together, which keeps a game near 1,800
  tokens: `{H,A}_MISS_{rim,mid,three}`, `{H,A}_MAKE_{zone}_{U,A}` (unassisted / assisted), `FT_MAKE`, `FT_MISS`,
  `REB_O`, `REB_D`, `REB_O_TEAM`, `REB_D_TEAM`, `TOV`, `TOV_TEAM`, `FOUL_D` (counts toward the bonus), `FOUL_O`,
  `FOUL_X` (technical/flagrant/other), `SUB`, `TIMEOUT`, and `PERIOD_END`.
* **Payloads**: ACTOR for shots / FTs / rebounds / turnovers / fouls, ASSIST after an assisted make, `SUB`
  has OUT then IN. Points, rebounds and assists are therefore decodable from the token stream (box totals are
  reconstructed in tests).
* **Time** is `DT_k` (representatives 0,1,2,3,5,7,9,12,15,19,24,30,40,55,80,120 s) with ERROR DIFFUSION: the
  bucket is chosen so the accumulated token clock stays within one bucket of the true clock. A sampler that
  only sees tokens therefore reproduces the clock process the model was trained on.
* **State blocks** are inserted when the token clock crosses a 120 s grid line and at every period start (with
  a lineup refresh at each period start and every third in-period block). They are deterministic functions
  of the history, so they are FORCED in sampling and EXCLUDED from the loss (otherwise the model spends
  capacity on arithmetic and a sampled game could contradict its own scoreboard).
* **Cold start**: a player outside the player vocabulary is `NEW_k`, k = rank among the game's unknown
  players by id: unique and reversible inside a game, one shared embedding across games. Training can
  replace known players by `NEW_k` (player-token dropout, searched). Header rosters list rotation players by
  as-of minutes so a rookie can still be named in the header.
* **On-court tracking** is derived from the stream (starters, SUB swaps, and a least-recently-active repair
  when an actor is not tracked on court). ~96% of real actors are on the tracked court; the rest are
  repairs (the main cause is a substitution whose incoming player never appears elsewhere in the game and
  cannot be resolved from the free-text name; counted as `sub_unresolved` in `export_report.json`).

### As-of discipline (leakage controls)

The header uses ONLY (a) the game's own starters flag (lineups are public before tip), (b) pre-tip injury
report rows admitted by `pretip_status` (reports stamped after tip-off minus the lead are ignored; tested),
(c) the injury-Elo out-of-fold probability, and (d) quantities from STRICTLY EARLIER games: rotation
(last 10 team games' minutes), trailing 20-game margin, rest days, player last-10 means. `AsOfHistory`
is updated after a game is tokenized. Tests (planted-future-game and own-box-score rewrites) prove the
header does not move; they also prove the tip prefix contains no event token and that every checkpoint
state equals a replay of its prefix. The player vocabulary is built from TRAIN games only.

Residual risk, stated plainly: the starting lineups come from the box score `starter` flag (known at tip but
not time-stamped); the Elo OOF is trusted as walk-forward. 2022 games early in the window have thin
as-of rosters (history starts with the export).

## Model and training (`nba/colab/jobs/pbp_gpt/pbp_gpt.py`)

Pre-norm GPT, learned absolute positions (2,304), weight-tied output head, **grouped-query attention** (KV heads
= heads/3, so sampling thousands of continuations fits in memory), dropout 0.1-0.3, AdamW (betas 0.9/0.95,
wd 0.05-0.1 on matrices), linear warm-up + cosine LR, grad clip 1.0, bf16 autocast (fp16 + GradScaler on T4),
batch 8 games. Loss = next-token cross-entropy on LEARNED tokens only (DT, EVT, ACTOR, ASSIST, SUB_OUT, SUB_IN).
Search (`full`): 6 trials = default (6L, d384), large (10L, d512) and four seeded random configs over
{256,384} x {4,6,8} layers, dropout, lr, wd, player-token dropout; <= 40 epochs, patience 5, selected by 2023-val
NLL. Crash-safe per-epoch `last.pt`, per-trial result JSON, resume with `RESUMED` log lines. A measured
runtime projection prints after epoch 1.

## Sampling

Ancestral sampling (temperature 1, no truncation) from a checkpoint state, S continuations per game, batched on
the GPU with a vectorised state machine that is the twin of `StreamState`: grammar phase, actor
constraints (actors drawn from the on-court five of the event's side; assist from a teammate other than
the shooter; SUB-IN from the bench of the header roster; an unseen incoming player takes a free roster slot),
forced state blocks / lineup refresh / EOS, score, token clock, team fouls, bonus. The prefix KV is shared by
the S continuations of a game and per-row KV grows only with generated tokens. The twin is tested
token-for-token against the python reference by teacher forcing real streams; sampled streams parse with
zero mismatches. Continuations that do not finish inside the token cap (game time remaining + 300 s overtime
slack, x1.35 tokens/s) keep their current margin and are counted (`frac_done`). Throughput (continuations/s,
tokens/s, device) is written to `metrics.json` and the report.

Checkpoints: **tip** (header + initial block), **q2** / **q3** / **q4** (first boundary after the period-start
block), **q4_5min** (first event boundary in Q4 at token clock <= 300 s).

## Evaluation - PRE-REGISTERED (season 2024 only)

Evaluation games: seeded random subset of `ok` 2024 games (`ok` = token-stream final score equals the box
score, no truncation, no period-structure inconsistency; all five checkpoints valid; >= 9 rostered players per
side). `full`: 300 games for the four live checkpoints, the first 150 of them also at tip. The same games for
every target. Game-clustered 95% bootstrap CIs (2000 resamples, seed 20261008); BH at q = 0.05 inside each family.

* **G0 sanity gate: next-token perplexity** on all 2024 learned tokens vs (a) slot-conditional unigram and
  (b) interpolated trigram (weights chosen on the 2023 val slice). Gate passes iff the upper CI of
  (GPT - trigram) per-token NLL < 0. A GPT that does not beat a trigram is not a simulator; sampled results are
  then reported but flagged.
* **P1 PRIMARY: live win probability** at q2, q3, q4, q4_5min. GPT p = (home wins + 0.5 ties + 0.5)/(S + 1).
  Baseline: logistic regression on `[diff/sqrt(t_rem+0.5), logit(p_pre)*sqrt(t_rem/48), 1]` (minutes remaining;
  p_pre = pregame injury-Elo), fitted on TRAIN-split checkpoint states of the four checkpoints pooled, no 2024
  re-fit. Decision metric: log loss (Brier, ECE descriptive plus the ECE condition). Family = the 4
  per-checkpoint log-loss differences (GPT - baseline). A checkpoint PASSES iff upper CI < 0, BH-adjusted p
  <= 0.05 and ECE(GPT) - ECE(baseline) <= +0.01. **Target KEPT iff >= 1 checkpoint passes and none is
  significantly worse** (lower CI > 0 and adjusted p <= 0.05). No recalibration of the GPT is used.
* **P2 secondary: pregame (tip)**: win log loss vs injury-Elo; margin and total CRPS vs the
  `nba.parlay.game_model` Normal(mu, sd). BH over these three. Expected to LOSE. Kept only if all three upper CIs < 0.
* **P3 secondary: live player lines**: remaining-game points / rebounds / assists for rostered players with
  as-of mean minutes >= 12, CRPS of the GPT samples vs "current + pro-rated recency" (as-of last-10 mean x
  fraction of regulation remaining) with a Negative Binomial whose dispersion is fitted on TRAIN checkpoints.
  Paired bootstrap clustered by game; BH across the 12 (stat x checkpoint) cells; a cell wins iff upper CI
  < 0 and adjusted p <= 0.05.
* **Descriptive only** (never used to keep or tune): cold-start NLL (NEW-player actors vs known), per-category
  NLL, split-half agreement of sampled probabilities, injury-Elo reference at each checkpoint, unfinished-row
  share, throughput.

Power note: the live baseline already knows the score and the clock. Early checkpoints (q2) are the best
chance for a gain from sampled play; by q4_5min the outcome is mostly determined and the sampling
noise floor (SE of p-hat is up to 0.5/sqrt(S): 0.022 at S = 512, 0.016 at S = 1024) eats small effects.
The honest prior is that the model ties or loses P1 and P2 and may win some P3 cells.

## Run

```
# local, once (about 2-4 min, read-only DB, low memory; writes data/colab/pbp_gpt/*, gitignored)
uv run python -m nba.features.pbp_tokens --db nba.duckdb --out-dir data/colab/pbp_gpt
# stage + run on Colab (maintainer): make colab-push JOB=pbp_gpt  -> open the printed notebook, GPU, Run all
# pull, then score (applies the rules above):
make colab-pull JOB=pbp_gpt
uv run python -m nba.eval.pbp_gpt_eval --run data/colab/runs/pbp_gpt/<run_id>/artifacts/<ts> --data data/colab/pbp_gpt
```

Runtime ESTIMATES (FLOP / memory-bandwidth arithmetic, not measured on GPU): `full` about 2-3 h on a T4/L4 and
1-1.5 h on an A100 (search ~75 min on a T4, sampling ~40 min on a T4); `fast` about 40-70 min. Colab prints a
measured projection after epoch 1 and after the first sampled chunk. Local CPU smoke: `NBA_BUDGET=smoke`
(tiny model, 24 synthetic games) in under 15 s inside pytest.

## Artifacts

`metrics.json` (written last), `best_config.json`, `search_log.json`, `training_log.json`,
`data_summary.json`, `weights.pt` (fp16, < 100 MB), `ppl_by_game.parquet` (per-game, per-category NLL for val
and report), `samples_{tip,q2,q3,q4,q4_5min}.npz` (final margin / total per continuation, per-player
remaining pts / reb / ast per roster slot).

## Results

Run `20261008_191436` (A100, full budget, 6 trials, best `rand3` 6.3M params, val NLL 1.643 on the 2023 slice;
2025 never loaded). Scored 2026-10-08 by `nba.eval.pbp_gpt_eval` exactly as pre-registered; full output in
`reports/pbp_gpt_report.md` (local).

* **G0 gate (next-token NLL vs trigram, 2024): PASS.** NLL 2.233 vs 4.005 (CI [-1.79, -1.75]). Note the
  val -> report jump (1.64 -> 2.23) is almost all in player-identity tokens (actor 1.68 -> 3.34, sub_in
  1.87 -> 3.93): the model learned *who* through 2023 and rosters moved in 2024. Event/timing tokens barely
  moved (evt 1.66 -> 1.67, dt 1.57 -> 1.58).
* **P1 live win probability (primary): NOT KEPT.** Significantly WORSE than the score+clock+injury-Elo
  logistic at all four checkpoints (BH p_adj 0.018 each): q2 +0.038 [+0.006, +0.070], q3 +0.032, q4 +0.020,
  q4_5min +0.028. GPT calibration was better at q2 (ECE 0.028 vs 0.078) but its probabilities were less sharp.
* **P2 pregame: NOT KEPT** (expected). Win LL 0.643 vs injury-Elo 0.556 (+0.088 [+0.040, +0.134]); margin and
  total CRPS also worse.
* **P3 live player lines: 2/12 cells won**, both tiny and late: q4_5min pts -0.095 [-0.120, -0.072] and
  q4_5min ast -0.012. 8 cells significantly worse (q2 pts +1.25). The GPT is nearly unbiased (pts bias
  -0.06..-0.36) where the pro-rated baseline over-predicts (+0.17..+1.03), but its spread is too wide early.

**Verdict: NOT KEPT** (T082-T084). Sequence modelling learned basketball grammar but not who wins: the score,
the clock and team strength already carry the live signal, and per-player identity tokens do not transfer
across seasons. Not registered as a candidate. A rerun is not planned; if revisited, the cheap ideas are
season-agnostic player tokens (rate embeddings instead of ids) and conditioning on injury-Elo, each as a new
pre-registration.
