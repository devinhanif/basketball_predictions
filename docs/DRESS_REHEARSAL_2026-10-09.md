# Opening-night dress rehearsal (replay of 2024-10-22 .. 2024-10-24)

Goal: exercise the full forward path end to end on a COPY of the database to find bugs before the
2026-27 opener (2026-10-20). Read-only w.r.t. markets. Season 2025 was not touched (the copy is
truncated at the replay date, which also hides season 2025, and no 2025 game was predicted or scored).

## Setup (all under `data/rehearsal/`, gitignored)

| Item | How |
|---|---|
| DB copy | `cp nba.duckdb data/rehearsal/nba_full_copy.duckdb` after `lsof nba.duckdb` was empty; the working copy `nba_work.duckdb` is a second copy. The real `nba.duckdb`, `kalshi.duckdb` and paper DB were never opened for write (mtimes unchanged). |
| Truncation | `python -m nba.daily.rehearsal truncate --work ... --date 2024-10-22` (new). `games` rows stay with NULL scores (like scheduled games) so the slate can come from the DB; every table with a `game_id` loses its rows for games on/after the date (player_game_stats, possessions, stints, ...); `player_rates` snapshots on/after the date are deleted. `player_availability` (injury reports) is kept on purpose: the pipeline must filter by report timestamp itself, which is part of what is being tested. |
| Settlement | `python -m nba.daily.rehearsal restore --full nba_full_copy.duckdb --date D --through D` re-adds scores and per-game rows for one day from the untouched copy (idempotent). Order per day: predict (results hidden) -> restore -> settle. |
| Slate | `--schedule-from-db` (new flag): all `games` rows of the season string, 19:00 ET tip proxy (the same proxy the injury-Elo backfill used). Default is still `fetch_schedule_nba_api`. |
| Clock | `--now ISO` (new flag, `run` and `settle`): naive UTC; offsets are converted. Default is the real clock. Prediction clock = tip - 70 min (21:50 UTC) so the 17:45 ET report is usable (`<= tip-60`); settlement clock = next day. |
| Model cache | `--model-cache DIR` (new flag): keeps the context-residual cache out of `data/models/`. Default unchanged. |
| Injury | `--skip-ingest --skip-injury`; the replayed reports are the `player_availability` rows already in the copy (no network). |
| Synthetic Kalshi | `data/rehearsal/make_synth_kalshi.py` builds `kalshi_synth.duckdb` (78 markets, every title prefixed `SYNTHETIC:`, prices invented around the model's own probabilities with spreads and a few deliberate mispricings, plus 6 edge-case markets: no price row, unknown player, other date, unsupported PRA stat, unparseable event, ask at 1.00). It is NOT market data and says nothing about edge. |
| Locks | `heavy.lock` acquired (retry 2 min, released by trap) around every context-model fit via `data/rehearsal/heavy.sh`. |

## Steps, outputs, timings (this Mac, 8 GB)

| # | Step | Result | Wall | Peak RSS |
|---|---|---|---|---|
| 1a | `nba.daily run --date 2024-10-22` | slate 2, predicted 2, 364 rows (4 win rows, 180 prop primary + 180 recency), out_excluded 29, ctxres fit 4 s (n=55,934, through 2024-06-17), 2/2 games with a usable report, 0 fell back | 8.3 s | 856 MB |
| 1b | `run --date 2024-10-23` (after restoring 10-22) | slate 10, predicted 10, 1,980 rows, ctxres fit 5 s (through 2024-10-22), 10/10 games with report, 4 players on recency fallback (<5 prior games) | 9.1 s | 812 MB |
| 1c | `run --date 2024-10-24` | slate 4, 696 rows; its start-of-run settle scored 10-23: 20 win rows, 1,088 prop rows, 872 DNP | 9.2 s | 834 MB |
| 1d | rerun 10-24, +20 min | second run_id with 696 rows, context model `cached`, settle scored nothing twice (append-only, latest per key settles) | ~6 s | - |
| 2 | after-tip run (`--now` = tip) | CLI exit 2, `predicted=0 refused_after_tipoff=2`, no rows written | ~5 s | - |
| 2b | store `LeakageError` called directly with made_at == tip and tip+1 s | Both raise `LeakageError`, the WHOLE batch (including a valid later-game row) is refused; row count unchanged (364 -> 364). | - | - |
| 3 | `nba.daily settle --now 2024-10-23T12:00` | win_scored 4, prop_scored 224, prop_dnp 136 | 2.2 s | 444 MB |
| 4 | `nba.daily report` (3 dates) | renders; paired table, CAUTION lines at n=16 games / 229 player-games; independence check "waiting: 3 < 30 dates" | 1.9 s | 455 MB |
| 5 | `nba.parlay evaluate --date 2024-10-23` (synthetic Kalshi, fresh paper copy) | 78 markets read, 74 mapped, skipped `{no_model_for_player 1, game_not_in_slate 1, unsupported PRA 1, unparseable_event 1, no_price 3}`; 146 contracts (both sides + 1 combo); 0 flagged; verdict `no_positive_ev_found`; model_weight 0; 145 shadow rows logged; combo labelled `product_of_leg_asks_NOT_A_TRADABLE_PRICE` | 1.3 s | 273 MB |
| 6 | `evaluate --settle --no-log` (what the morning job runs) | on a date with no predictions: no crash, note printed, settled shadow=136; 9 rows stayed open forever (bug 2) | ~2 s | - |
| 7 | assistant toolbox called directly (`data/rehearsal/assist_run.py`; no Ollama) | `list_slate` 0.59 s (first slate load), `evaluate_slate`, `price_parlay` (single and 2-leg with user ask), `explain_leg`, `what_if`, `track_record` (insufficient sample on a fresh book; full stats on the settled synthetic book), unknown player -> error "not guessing" | <1 s each | 265 MB |
| 8 | `ops/nba_daily.sh` equivalent | See below | - | - |

Model names written: `rung0_injury_elo` (primary=True for every slate game; `rung0_mov_elo` comparison with the injury delta, e.g. 0.453 vs 0.484), `props_context_residual`
(routed_to `context_residual`, 4 recency fallbacks) and `props_recency_v1`. Memory peak of the whole rehearsal: 856 MB.

### Validation (`data/rehearsal/validate.py`, all three days)
* Every slate game has both win models, exactly one primary; 0 NaN/inf anywhere in any prediction JSON; all `made_at < tipoff`.
* `p_ge` and `p_ge_uncond` non-increasing in N for every row (0 violations); `p_play` in [0,1]; `q_grid` monotone.
* `q10 <= q50 <= q90` always; strict `<` fails on 8 + 41 + 22 rows (day 1/2/3, counting the rerun), all but ~6 rows being `fg3m` (discrete, zero-heavy: q10 = q50 = 0). Not a bug; anything keyed on strict ordering would break on fg3m.
* Means vs actual (played rows only, conditional on playing), pooled n=294 player-games, 3 days. Context-residual bias pts -0.9, reb +0.0, ast +0.2, fg3m -0.2; MAE 4.7-5.2 / 1.9-2.1 / 1.3 / 0.9-1.3; 80% interval coverage 0.71-0.82. Recency: pts bias +1.0, ast +0.5. Forward report CRPS: context better on all four stats (paired CRPS deltas ast -0.12, fg3m -0.05, reb -0.09, pts -0.09 with a CI that includes 0 for pts). n is tiny (3 dates); read as "plausible, not broken", not as a result.
* Win probability: log loss 0.612 (injury-Elo) vs 0.606 (MOV-Elo) on 16 games; paired delta +0.006 [-0.009, +0.054]. No claim.

## Bugs found and fixed (working tree; NOT committed, see "Commit status")

1. **No way to rehearse the forward path offline (feature gap).** `nba.daily run` always called nba_api for the schedule and used the wall clock. Added `--schedule-from-db`, `--now` (run and settle) and `--model-cache`, plus `nba/daily/schedule.py::schedule_from_db` and `nba/daily/rehearsal.py` (truncate/restore). Defaults unchanged. Tests: `tests/daily/test_schedule_from_db.py`, `tests/daily/test_rehearsal.py`, `tests/daily/test_cli_defaults.py` (flags default off, `--now` parses to naive UTC).
2. **Players absent from a final box score never settle (real bug).** `papertrade._leg_yes` returned "no result yet" for a player with NO row in `player_game_stats`, even when the game was final and the box score was ingested. Inactive players have no row (only some DNPs have a NULL-minutes row), so those paper trades / shadow rows stayed open forever: 9 of 145 shadow rows here (about 6%), and a flagged parlay containing such a leg would never settle or refund. Now a final game with an ingested box and no row for the player raises `DnpLeg` (void or loss per `--dnp-policy`, same as a DNP row); an un-ingested box or a non-final game stays open. Test: `test_player_absent_from_final_box_score_is_dnp_not_open`. Behavior change: yes, but only for rows that could never settle before. (`nba.daily.settle` already handled this case as `dnp`.)
3. **Track-record `n` double counted every market (real bug, affects when the engine stops deferring to the market).** `evaluate` logs both the YES and the NO side of every market; `track_record` and the assistant's `track_record` counted both, doubling `n_settled` against `min_settled=30` (and the NO row is the same event). Now only `side='yes'` rows count. Test: `test_track_record_counts_each_market_once_not_both_sides`. See open issue A: this fix is necessary but not sufficient.

Earlier the same night (not mine): the daily CLI defaulted to the wrong props model; the guard test `test_cli_default_matches_pipeline_default` covers it and still passes.

## Open issues for the maintainer (not fixed; need a decision or are model limits)

A. **The market-deferral gate opens after ONE day of data.** `n_settled` counts shadow rows, ~300+ markets per slate (more with the 2-per-game win markets and 3-5 thresholds per player, all strongly correlated). With min_settled=30 and k=100, the model weight is n/(n+k): after the first settle (day 1) the weight is ~0.5-0.8 if the raw Brier skill happens to be positive, and `positive_ev_at_conservative_bound` can be emitted on the second night. Reproduced: 136 settled synthetic rows from one date gave model_weight 0.576 and 15 flagged contracts. Recommendation (decision needed): add `min_settled_dates` (e.g. >= 10 distinct slates) and count one row per (game, player, stat) cluster; until then treat any positive verdict in the first two weeks as noise. Also `KXNBAGAME` home/away markets are complements and are still counted as two.
B. **Opening-week rosters are stale.** Rosters come from each team's last 10 games, which for the opener are last spring's. Of 276 played player-games on the 3 replay days (2 + 10 slate days), 112 (41%) had no prediction: 47 moved teams (31 played >= 15 min), 33 had no prior games (rookies; 10 >= 15 min), 28 were same-team players outside the last-10 window (22 >= 15 min). And 40-45% of predicted players DNP'd (p_play mean 0.70 vs 0.57 observed; bins 0.4-0.6 observed 0.36, 0.6-0.8 observed 0.58, 0.8-1.0 observed 0.84, n=376). Consequences: the parlay engine cannot price a large share of props in week 1 (`no_model_for_player`), p_play is over-optimistic (DNP-void EV and p_all_play are too high), and traded players can be attributed to their old team. Suggested fix (a model change, so not done here): in the offseason (last game > 45 days ago) widen the roster window, seed rosters from the first box scores of the new season, and recalibrate p_play for the first ~3 team games.
C. **NBA Cup knockout games (`006` ids, ~3 per season) are dropped** by `KEPT_GAME_TYPE_PREFIXES`; they get no forward prediction and no Elo update.
D. **Actual schedule/injury fetchers remain unrehearsed.** `fetch_schedule_nba_api` column names (`gameId`, `gameDateTimeUTC`, `homeTeam_teamId`, `awayTeam_teamId`) were confirmed to exist in the installed `nba_api` endpoint definition, but no live call was made (no network allowed). The injury-report PDF probe and parser were not exercised; the first pretip run on 10-20 (and a preseason day if one exists) is the real test.
E. **`ops/nba_daily.sh` cannot be pointed at a copy** (hard-coded ROOT, `nba.duckdb`, default DB/paper/Kalshi paths, `lsof nba.duckdb`). I ran its steps by hand with `--db-path/--nba-db/--kalshi-db/--paper-db`, with the same arguments the script uses. To make the script itself rehearsable it would need `NBA_DB`, `PAPER_DB`, `KALSHI_DB` and `ROOT` environment overrides (4 lines). Steps not rehearsed: post-game fetch/load (network), `datamanifest snapshot/diff/check` (writes tracked files under `docs/data_manifests/`), backups and the new `rclone` steps (another agent has added `backup_*_drive` steps; a missing rclone remote will raise an alert each morning, not break the job).
F. Strict `q10 < q50 < q90` does not hold for fg3m (see above); document before anything downstream assumes it.
G. The `report` rollover line says "Completed 2026-27 games ingested" for whatever `--season` is passed (cosmetic when replaying).

## Go / no-go checklist for 2026-10-20

| Check | Status |
|---|---|
| Daily CLI defaults to `props_context_residual`; primary win model `rung0_injury_elo` | GO (tested, replayed) |
| Pre-tip only: after-tip run exits 2 and writes nothing; store refuses the whole batch at made_at >= tip | GO (proved) |
| Same-day rerun is idempotent; context model cache reused; settle never double-scores | GO |
| Settle / report / parlay shadow log + settle end to end, no NaN, monotone p_ge | GO |
| Runtime and memory (about 10 s, < 1 GB per run on 8 GB) | GO |
| Engine says `no_positive_ev_found` with zero track record | GO |
| Engine keeps deferring to the market for the first weeks (issue A) | NO-GO until decided: either accept that early "positive" flags are noise, or add the date gate before 10-20 |
| Week-1 coverage of rookies / moved players (issue B) | KNOWN LIMIT: expect ~40% of real minutes unpredicted; do not read early prop skill as representative |
| Live schedule + injury PDF fetch (issue D) | UNVERIFIED: do a supervised `nba.daily run --date <preseason or 10-20>` the morning of; check slate count against the NBA site |
| `ops/nba_daily.sh` / launchd install | UNVERIFIED end to end (issue E); install, then watch `data/ops/ALERTS.md` on 10-19 and 10-20 |
| Season-boundary Elo regression on day 1 | GO (exercised: replay day 1 had max training season < slate season) |
| Backups / `rclone` Drive steps | Maintainer to confirm the remote exists |

## Commit status

Nothing is committed or pushed: the run constraints for this session forbid committing, and the working tree also
holds other agents' uncommitted work. Files changed by this rehearsal: `nba/daily/__main__.py`, `nba/daily/schedule.py`,
`nba/daily/rehearsal.py` (new), `nba/parlay/papertrade.py`, `nba/parlay/shadow.py`, `nba/parlay/assistant/toolbox.py`,
`tests/daily/test_cli_defaults.py`, `tests/daily/test_schedule_from_db.py` (new), `tests/daily/test_rehearsal.py` (new),
`tests/parlay/test_joint.py`, this doc. Suggested commits: (1) `feat(daily): --schedule-from-db, --now, --model-cache and replay helpers for rehearsals`,
(2) `fix(parlay): settle legs of players absent from a final box score as DNP`, (3) `fix(parlay): count each market once in the shadow track record`.

Verification: ruff check/format --check and mypy are clean on `nba/daily nba/parlay tests/daily tests/parlay` and `mypy nba` passed; at the final run repo-wide `ruff check nba tests` failed ONLY in other agents' in-progress files (`nba/ingest/__main__.py` import order, `nba/parse/history.py` and `tests/ingest/test_history_mode.py` line length / Yoda) and is not mine to fix;
`uv run pytest tests/daily tests/parlay tests/registry tests/kalshi tests/datamanifest --no-cov`: 242 passed.
Re-run the rehearsal: `data/rehearsal/` holds the scripts (`heavy.sh`, `validate.py`, `make_synth_kalshi.py`, `assist_run.py`).
