# SEQ_PROPS: GPU sequence model for player props

Status: built and smoke-tested locally; NOT yet run on Colab. Nothing below is a result.

Question: does a model that reads each player's raw recent game history (including the
games he missed) learn something that the hand-made recency features in
`nba/props/context_residual.py` (production) cannot? It is a new candidate props model and
a router input.

## Seasons and splits

`season` is the start year (2023 = 2023-24). Targets are exported for seasons 2022-2024;
season 2025 is the frozen holdout and is never loaded (the loader, builder, trainer and
evaluator each raise on it). 2022 rows have a truncated history (the DB starts there).

- fit: seasons 2022 and 2023 with date < 2024-02-01 (and >= 3 prior played games)
- early stopping: 2023-season rows with date >= 2024-02-01 (time-ordered slice inside 2023)
- test: every played row of season 2024 (OOF, compared on the intersection with production)

## Sequence spec (`research/features/player_sequences.py`)

Window: last K=20 tokens before the target date, oldest to newest, left-padded (mask 0).
A token is a game strictly before the target game date. The player's timeline = his own box
rows (played or DNP) plus "gap" games: games of the team he most recently appeared for
(rows strictly before the gap) in which he has no row. Games before a rookie's / arrival's
first row for a team are padding, not "absent". Absent token = `absent=1`, `present=0`, stat
channels 0; team-game context (home, rest, opponent, pace, teammates out) is still filled.

Per-step channels (28 minus one: the parser emits only rim/mid/above3 zones, so 27):
present, absent, same_team (token team == target team), days_ago (log1p/6), min/36,
pts/20, reb/10, ast/8, fg3m/4, fga/18, fta/8, tov/4, stl/2, blk/2, starter, home,
rest/7, b2b, usg_poss (shooter possessions + FT trips over team possessions, x5),
zone_rim/mid/ab3 (share of his shot attempts), usg_box ((FGA+0.44 FTA+TOV) share, x5),
opp as-of defensive rating, team as-of pace (rolling 20 games, shrunk to constants 113/99),
n_tm_out (rotation teammates with a DNP row that game), vac_tm (sum of their as-of usage).
Plus an opponent-id index per step (embedding).

Target-game context (31): home, rest, b2b, Elo margin (production MOV-Elo, signed), opp/own
rating and pace, pre-tip report (has_report, own status one-hot, reported-OUT rotation
teammates count and vacated usage, "returning" = rotation teammates who sat the previous
game and are not reported out), log career/season games, days since last game, position,
team game number, shrunk recency bases (minutes, starter rate, 4 stats).
Report rule: latest official snapshot with `as_of <= game_date + 19:00 - 60 min`
(`usable_report_rows`); later snapshots ignored; no usable snapshot gives `has_report=0`.
Player id: embedding over players with >= 20 fit rows; everyone else is OOV index 0 (also
used by 15% id-dropout in training), so unseen players follow the cold-start path.

Tests (`tests/features/test_player_sequences.py`): windows never contain the target or
later games; altering the target game's box score does not change its own inputs but does
change the next game's; absent tokens for DNP rows and missing rows; arrival padding; report
rule (post-cutoff snapshot ignored); 2025 refused.

## Model (`research/colab/jobs/seq_props/seq_props_train.py`)

Small transformer (2 layers, d=64, 4 heads, pre-norm; dropout 0.15 in attention/FF, 0.4 on the head, 15% player-id dropout, AdamW weight decay 5e-2 to avoid memorising season-specific sequences) over 20 step tokens + one target token
built from context and player/team/opponent embeddings (GRU arm only in `full`). Heads:
19 quantiles per stat via cumulative softplus (monotone by construction) as a residual over
the shrunk recency mean, plus a mean head. Loss: pinball normalised by per-stat scale +
0.1 x normalised MSE. AdamW, batch 1024, autocast + GradScaler on CUDA, early stopping.
One knob: `NBA_BUDGET=fast` (default: transformer, 2 seeds, train-through-2023 once, ~10-15
min on a T4) or `full` (+GRU, 3 seeds, warm-start walk-forward refit before each 2024 month,
~35-50 min). Seeds are in `config.json`. Quantile grids are Vincentized across seeds.
`oof_2024.parquet` is the arm with the best 2023 validation pinball (never 2024).

## Pre-registered evaluation rule (fixed before any Colab run)

Implemented and documented in `research/eval/seq_props_eval.py` (module docstring is canonical).
Rows: 2024 played rows common to the seq OOF and production `context_residual` OOF (which
requires >= 5 prior games, so "cold-start" here means 5-19 career games in the DB window).
Score: 19-level grid CRPS, game-clustered paired bootstrap 95% CI, BH over the 4 stats,
floor 0.005, and no slice (cold-start vs established, first-15 vs rest, starter vs bench,
teammate-out vs none; n >= 300) worse by more than +0.01. Three families, each BH-corrected
separately: A seq alone, B equal 50/50 blend, C learned softmax-gate blend
(`research/stack/router`, walk-forward 30-day blocks within 2024). B or C may be kept even when A
fails (router candidate). A null result is a valid outcome. MAE, bias and 80% coverage are
descriptive.

## Commands

    make colab-push JOB=seq_props          # exports the npz if missing/stale, stages notebook
    make colab-status JOB=seq_props
    make colab-pull JOB=seq_props
    uv run python -m research.eval.seq_props_eval --oof data/colab/runs/seq_props/<run_id>/oof_2024.parquet
    # add --write-store to put the 2024 grids in data/stack/oof.duckdb as model seq_props/v1

Known caveats: usage from the possessions table is only as good as shooter attribution
(FT trips and turnovers are partly unattributed; the box-score usage channel covers that);
teammate-out counts rely on DNP rows (players with no row at all are not counted);
`push` inserts its config cell before the notebook's first code cell, so if Drive is not
already mounted the config cell cannot find the run folder (same layout as rung4_stepheads).
