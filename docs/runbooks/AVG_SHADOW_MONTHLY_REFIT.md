# AVG shadow: monthly weights refit (runbook)

Rule: `docs/prereg/AVG_SHADOW_LIVE_2.md` (FROZEN, sha256 `9afbfeb6`), "What the steward builds" 3 and "Refit staleness". The new month's weights must be installed
by **12:00 CT on the 1st** (11-01 for November). A missed install is flagged stale (`stale_model = 1`), scored in the stale slice and counts toward the 10% INCONCLUSIVE
limit; the app silently falls back to production for AVG props meanwhile. Written 2026-10-14 by the modeler; nothing below has been run on live data (no October game
exists yet). Every command was exercised only on synthetic arrays or on seasons <= 2024.

What the job is: the NEXT BLOCK of the same chain. Start from last month's saved weights, warm refit (3 epochs, lr 3e-4, batch 1024, seed 1000 + k, hidden 64, 3 seeds, vocabulary
unchanged) on every row dated before the 1st with `n_prior >= 3`: hist 2019-21, 2022-25 and the live season's settled rows. Same as the rebuild's install fit. Nothing is
predicted or scored. Budget 20 T4-minutes (expected 2-4), ceiling 60 (halts, never cuts an arm). The gate refit and the export run on the Mac.

## Before 11-01 (any day this month)

1. **Decide the gate (needs Devin).** The rule says the gate is refit monthly, on all earlier rows, "later months add the shadow's own stored SEQ and A0 forecasts of earlier
   months". `gate_work/gate_rows.npz` was deleted on 10-14 (build review S2), so the gate cannot be refit without rebuilding it (a second season-2025 training-class use, logged first).
   Refitting on the SAME rows gives the same gate, so the real choice is:
   * **A (default here, a deviation to approve):** carry October's `gate.json` (gate_id `58043d34`, `gate_fit_through` 2026-06-13) into November unchanged. No 2025 touch.
   * **B (rule text):** rebuild the 2023-2025 rows with the S1-fixed `gate_rows.py`, append October's live rows from `forward_avg_shadow` / `forward_avg_scores`, `fit_gate.py`,
     delete the npz. Not built: the live A0 grid is the stored INTEGER quantiles while the gate was trained on continuous 199-point grids (a support mismatch the rule does not address),
     and the loader for live rows does not exist. Needs a decision and code first.
2. **Log row (archivist, before anything runs).** `docs/HOLDOUT_ACCESS_LOG.md` must contain a line with `AVG_SHADOW_LIVE_2 monthly refit` and `2026-11`; the notebook and
   `stage_monthly.py` refuse otherwise. Draft: `2026-11-01 | AVG_SHADOW_LIVE_2 monthly refit 2026-11 (warm block on every row before 2026-11-01, includes season-2025 labels as TRAINING rows; same class as row 72 use 1) | training only | nothing scored | no touch beyond row 72's three uses`. The archivist decides whether row 72 already covers monthly refits.
3. **Dry-run the deadline check** (nothing installed for November, expect FAIL after the deadline, WARN in the last 3 days, OK before):
   `PYTHONPATH=. .venv/bin/python -m nba.daily.avg_shadow.install_check --now-ct 2026-11-01T13:00:00` -> exit 1; `--now-ct 2026-10-29T09:00:00` -> exit 0.

## 11-01 morning (about 07:00 CT; each step prints what it did; ~15 min of Devin's time plus ~5 min of T4)

0. **Data are in.** Season-2026 box scores and possessions must exist for every game through 10-31 (read-only count, expect > 0 and equal game counts):
   `.venv/bin/python -c "import duckdb;c=duckdb.connect('nba.duckdb',read_only=True);print(c.execute(\"select count(*),count(distinct game_id) from player_game_stats s join games g using(game_id) where g.season=2026 and g.game_date<'2026-11-01'\").fetchall(), c.execute(\"select count(distinct game_id) from possessions p join games g using(game_id) where g.season=2026 and g.game_date<'2026-11-01'\").fetchall())"`.
   If possessions are missing for any October game, STOP: the exporter would build those rows without possession features, which is not the training distribution (open question 1).
1. **Export (Mac, CPU, about 1 min).** `mkdir data/ops/heavy.lock` (retry in 120 s if it exists; `rmdir` when done), then
   `PYTHONPATH=. .venv/bin/python -W ignore research/colab/jobs/avg_shadow_monthly/export_monthly.py --month 2026-11`
   -> `data/colab/avg_shadow_monthly/2026-11/{seq_ensemble_rt_live.npz,export_info_live.json}`. It refuses a row on/after 11-01 and any game without a real tip. Expect roughly 1,500-2,500 rows (about 70-120 games x ~21 rows); read `targets`, `games`, `max_date` (should be 2026-10-31 or the last game day) and `tip_coverage.without_real_tip` = 0.
2. **Stage (Mac).** `PYTHONPATH=. .venv/bin/python research/colab/jobs/avg_shadow_monthly/stage_monthly.py --month 2026-11 --drive "$HOME/Library/CloudStorage/GoogleDrive-<account>/My Drive/nba_colab/avg_shadow_monthly"`
   (omit `--drive` and copy `data/colab/avg_shadow_monthly/2026-11/run` to Drive `nba_colab/avg_shadow_monthly/2026-11` by hand). It verifies the previous (October) bundle, the rule sha and the log row, and prints the folder size (about 80 MB).
3. **Run (Colab, one click).** Open `research/colab/jobs/avg_shadow_monthly/avg_shadow_monthly.ipynb` (repo path:
   `/Users/devin/Downloads/nba-prediction/research/colab/jobs/avg_shadow_monthly/avg_shadow_monthly.ipynb`; upload to Colab or open from Drive), Runtime > Change runtime type > **T4 GPU**,
   Runtime > Run all. The last cell finds the staged folder itself (newest `export_info_live.json` under Drive `nba_colab/avg_shadow_monthly/`), prints which, and writes
   `<run folder>/artifacts/<stamp>/`. It halts at 60 min and warns past 20. Read the Colab unit cost from the runtime picker before pressing run (`configs/cost.yaml` has null unit rates).
4. **Pull + assemble (Mac).** Copy `seed0.pt seed1.pt seed2.pt vocab.json bundle_parts.json config.json metrics.json` from the artifacts folder to `data/colab/avg_shadow_monthly/2026-11/artifacts/`, then
   `cp data/avg_shadow/bundles/2026-10/gate.json data/colab/avg_shadow_monthly/2026-11/gate.json` (gate option A) and
   `PYTHONPATH=. .venv/bin/python research/colab/jobs/avg_shadow_rebuild/assemble_bundle.py --run data/colab/avg_shadow_monthly/2026-11/artifacts --gate data/colab/avg_shadow_monthly/2026-11/gate.json --gate-fit-through 2026-06-13 --out data/avg_shadow/incoming/2026-11`.
   Check the printed `month_block` 2026-11 and `fit_through` = the last pool date (< 2026-11-01); `manifest.json` carries `prev_model_id` (October's `b8d2b27b...`).
5. **Install (the stamp that matters; before 12:00 CT).** `PYTHONPATH=. .venv/bin/python -m nba.daily.avg_shadow.bundle install data/avg_shadow/incoming/2026-11` -> `data/avg_shadow/bundles/2026-11`, `installed_at` stamped (UTC).
   Then `PYTHONPATH=. .venv/bin/python -m nba.daily.avg_shadow.install_check` -> `avg install 2026-11: ok` (exit 0).
6. **Record.** Archivist: sha256s of the 3 weights and `model_id` into `forward_avg_meta`/the ledger line; steward: the pretip run on 11-01 should show `month_block 2026-11`, `stale_model` 0 in `forward_avg_shadow`.

If anything fails: do not install a partial bundle. Rows from 12:00 CT on are stale until a valid bundle is installed (never overwritten, never silently reused); the watchdog alerts past the 3rd.

## Proofs already run (no live data)

* `export_monthly.build_month_export` is bit-identical to the T478 export (`seq_ensemble_rt_cur.npz`): season 2023 before 2024-02-01 (15,304 rows) and season 2024 before 2025-01-01 (10,435 rows); `seq, mask, opp_step, ctx, player_id, team_idx, opp_idx, date_days, season, game_id, y, base, n_prior` all identical; `ctx_features`, `step_features`, `stat_scale` equal.
* `tests/daily/test_avg_shadow_monthly.py`: synthetic end to end (refuses without rule/log row/with a November row; weights move; assembled bundle verifies, installs, and is selected for November).

## Open questions (Devin)

1. Are possessions parsed for live games by the nightly path? (step 0 checks; if not, the October rows are built without possession features.) 2. Gate A vs B (step 1). 3. Does HOLDOUT_ACCESS_LOG row 72 cover monthly refits on a pool that contains season-2025 labels, or is a row per month wanted (the notebook demands one)? 4. The rule's P1b/P4 proofs were run for the October bundle only; a monthly CPU-vs-GPU check of the new weights is not built.
