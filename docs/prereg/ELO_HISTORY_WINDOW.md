# ELO_HISTORY_WINDOW: does MOV-Elo gain from three more seasons of scores? (pre-registration; the one allowed HISTORY_WINDOW follow-up)

Status: DRAFT 2026-10-11 ~10:00 CT, NOT FROZEN; awaiting Devin's "confirmed". Committed by the maintainer's hand BEFORE any fit.
Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ELO_HISTORY_WINDOW.md | shasum -a 256` (first 8 hex chars into the results
section and every ledger row). Season labels: 2019 = 2019-20 … 2024 = 2024-25; 2025 = 2025-26 (frozen holdout, never loaded).

## What Devin asked
> "Should we retrain the model on the total old data to see how MOV-Elo works with more data?" (2026-10-11)
HISTORY_WINDOW (T315) stopped at its coverage audit: the 2019-21 injury reports carry era identity, so no model that reads reports
can be extended cleanly. MOV-Elo reads only scores, dates and home/away, so Devin's question survives in a report-free form.

## Hypothesis
H1: the GA-tuned MOV-Elo (configs/mov_elo_tuned.yaml: k 9.49, home 40.4, mov_c 3.69, mov_div 6.7e-4; T182) is better on 2023-24
and 2024-25 win log loss when (a) its rating replay starts in 2019-20 instead of 2022-23 (ratings carry more history into the first
weeks of each season), and/or (b) its four hyper-parameters are re-tuned on 2019-20 to 2022-23 by the same GA under the same
budget, then frozen. Null: no change. Counter-hypothesis up front: Elo ratings forget with half-life ~ 1/k games, so a 2019 start
changes nothing after the first ~30 games of 2022-23; the COVID seasons (bubble, no fans, 72-game schedule) are a different
home-court and margin process and may hurt the tuning. Prior pass probability ~15%. Season carry-over (the between-season
regression toward the mean) is the one place more seasons could legitimately help, because it is estimated from season boundaries
and the current window has only two of them.

## Data exposure (honest)
Seen: T178/T182 (win-model bake-off; injury-Elo beats MOV-Elo; MOV-Elo tuned 2022-24 and once on the 2025 holdout, spent);
HISTORY_WINDOW's gate numbers (scores complete for 3,637 history games; era flags). Not seen: any Elo number with a 2019 start.
Season 2025 never loaded; `season <= 2024` asserted; both DBs read_only. Select on 2023, report on 2024.

## Fixed design (constants final)
Inputs: `games` (game_id, date, home/away, final scores) from nba.duckdb (2022+) and data/history/nba_history.duckdb (2019-21);
real tips not needed (Elo is a per-game sequence); `game_era_flags` read for the slice only. No report, roster or player feature.
Arms (same evaluation games: every 2023 and 2024 regular-season game with a result; identical keys asserted):
* E0 production comparison arm: GA-tuned MOV-Elo, replay from 2022-10-18 (reproduces the stored 2023/2024 OOF log loss to 1e-6).
* E1 long replay: same parameters, replay from 2019-10-22 with the production season carry-over rule applied at each boundary.
* E2 re-tuned: the GA (research/… the archived tuner, same population, generations and seed as T182) fit on 2019-20 … 2022-23
  game log loss, parameters frozen, then replayed from 2019-10-22 and scored on 2023/2024.
* E3 (descriptive): E2 with the bubble and no-fans games down-weighted to 0.5 in the tuning objective (era flags), to show
  whether the COVID seasons help or hurt the tuning.
* Reference: production `rung0_injury_elo` v2 OOF log loss on the same games (not an arm; the bar nothing here is expected to clear).

## Metrics, support, inference
Per-game win log loss (primary) and Brier (secondary); paired delta (arm − E0) per season with a game-level bootstrap (2000, seed 0)
and a date-block bootstrap (14-day blocks) as the clustering sensitivity; reliability in ten bins. Family = {E1, E2} per season, BH.
Slices (descriptive): first 15 team games of the season (where carry-over matters), rest-of-season, home favourites, close games
(|Elo diff| < 50). MDE: SE ≈ 0.004 per season (T178), so the floor below is at the detectable edge.

## Minimum data checks (fail = stop, no model result). Measured 2026-10-11 (HISTORY_WINDOW gate):
1. History `games` complete vs the cached game list: 3,637/3,637, no duplicates (pass). Scores non-null for all (to verify at freeze).
2. E0 reproduces the stored MOV-Elo OOF log loss to 1e-6 (T182 numbers 0.6245 / 0.6271-style values from the registry run).
3. Planted-future: a score edited after the evaluated game changes no rating used for it.
4. Era flags present for 100% of history games (pass: 3,637 rows, 0 rule disagreements).

## Pass rule (2023 selects, 2024 confirms; all must hold)
Arm log-loss delta vs E0 <= −0.004 with CI upper < 0 (game bootstrap) and BH p < 0.05; the date-block CI also below 0; reliability
not worse (ECE within +0.005); the first-15-games slice not worse by more than +0.01. A pass gives the win model a candidate
comparison arm only (injury-Elo remains production; promotion is Devin's and needs the live log).

## Kill criteria
No arm passes on 2023: close; no GA budget increase, no extra seasons (2013-18 are out of scope), no carry-over rule tuning. This is
the one follow-up HISTORY_WINDOW allowed; after it the history-window direction is closed for 2026-27.

## Outputs (exact)
`reports/prereg_elo_history/results.json`, `reports/prereg_elo_history.md`: `[season, arm, n_games, logloss, brier, dlogloss,
ci_lo, ci_hi, ci_block_lo, ci_block_hi, p, p_bh, ece]`; `[season, slice, arm, n, dlogloss, ci_lo, ci_hi]`; the frozen parameters of
E2 and E3; `[test, ok]` for checks 1-4; seeds, SHA, sha256. Code under `research/` only (`research/eval/elo_history_eval.py`).

=== RESULTS BELOW ===

(not run)
