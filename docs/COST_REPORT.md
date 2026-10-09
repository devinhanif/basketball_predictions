# Cost report, 2026-10-08 (generated overnight, read-only)

## Summary
Colab GPU use today is about 5.4 GPU-hours (estimate, lower bound): about 1.9 h on the T4 ctxres_v2 runs, 0.79 h on L4 (joint_game_set), 2.65 h on A100 (pbp_gpt), plus ridge_v2_sweep on L4, which has no artifacts yet and is probably still running. `configs/cost.yaml` has all per-hour unit rates null, so units cannot be estimated. Rates are needed (Colab > runtime picker). Balance is 93 units as of 2026-10-08 19:15 (fresh, under 3 days), but only one reading exists, so no burn-rate projection is possible. Drive is fine (gdrive 6.9 of 100 GiB used by this account; the Colab folder is 4.4 GiB, of which about 3.1 GiB is checkpoints of completed runs). The rclone shared client_id is being retired in 2026 (action below). Local disk is fine (270 GiB free). GitHub Actions: repo is public, so minutes are free; 31 of 100 recent runs were cancelled (superseded pushes).

## Colab GPU usage (source: data/colab/runs/*/*/metrics.json timings_s / wall_s; Drive listing)
| Job / run id | GPU | Wall (h) | Source / status |
|---|---|---|---|
| ctxres_v2_sweep 131747 | T4 (per maintainer; metrics only say cuda) | 0.38 | sum of timings_s, lower bound; artifacts present |
| ctxres_v2_sweep 140126 | T4 | 0.31 | sum of timings_s; artifacts present |
| ctxres_v2_sweep 140943 | T4 | unknown | artifacts on Drive, never pulled, no metrics |
| ctxres_v2_sweep 171446 | T4 | 1.23 | sum of timings_s; artifacts present |
| joint_game_set 184207 | L4 (metrics) | 0.79 (2857 s) | wall_s |
| pbp_gpt 191436 | A100-SXM4-80GB (metrics) | 2.65 (9530 s) | wall_s |
| ridge_v2_sweep 194547 | L4 (per maintainer) | unknown | no artifacts, 715 MiB checkpoints: running or partial |
| ctxres_v3_hybrid 193049 | n/a | unknown | staged only, no artifacts |
| rung4_stepheads 123134, seq_props 125041 | n/a | about 0 (seq_props 28 s) | negligible |

Per day (2026-10-08, ESTIMATE): T4 >= 1.92 h, L4 >= 0.79 h (+ridge), A100 2.65 h. Total >= 5.4 h.
Compute units: NOT ESTIMATED. Rates `colab_units_per_{t4,l4,a100}_hour` are null. I did not cite Colab's published rates because I could not verify them from a primary source in this run. Please fill them in from the Colab runtime picker. Note A100 is the dominant cost per hour; pbp_gpt sampling_s (7446 s) was 78% of its wall time.

## Colab balance
| Item | Value |
|---|---|
| units remaining | 93 (as_of 2026-10-08 19:15, about 4 h old at report time, fresh) |
| plan | pro |
| burn rate | not computable (single reading; rates null). Since 19:15, pbp_gpt (A100) plus joint_game_set/ridge (L4) ran, so the balance is likely much lower now. Please paste a fresh reading. |

## Google Drive (source: rclone about gdrive:, rclone size nbacolab:)
| Resource | Used | Limit | Notes |
|---|---|---|---|
| gdrive account | 6.9 GiB (plus 1.3 GiB trash) | 100 GiB (25.3 GiB free; 67.8 GiB used by other Google services) | free is lower than total minus used because of "Other" |
| nbacolab: (1082 objects) | 4.449 GiB | n/a | mostly ctxres_v2 171446 (3.0 GiB) and ridge_v2 (0.76 GiB) |

Checkpoints of runs that already have `artifacts/<ts>/` (safe cleanup candidates, NOT deleted):
| Run | Checkpoint size |
|---|---|
| ctxres_v2_sweep/20261008_171446 | 2.749 GiB |
| pbp_gpt/20261008_191436 | 297.5 MiB |
| joint_game_set/20261008_184207 | 112.9 MiB |
Total about 3.15 GiB. Not candidates: ridge_v2_sweep/20261008_194547 (715 MiB, no artifacts, may be running or resumable).
```
rclone purge nbacolab:ctxres_v2_sweep/20261008_171446/checkpoints
rclone purge nbacolab:pbp_gpt/20261008_191436/checkpoints
rclone purge nbacolab:joint_game_set/20261008_184207/checkpoints
```
Verify first that each run has been pulled to data/colab/runs (they have) and registered.
Caveat: Drive artifact folder timestamps (e.g. ctxres_v2 171446 -> 20261008_233713, pbp_gpt -> 20261009_004453) look like the Colab VM clock (probably UTC), not local time.

## GitHub Actions (source: gh run list --limit 100; billing API returned 404)
| Metric | Value |
|---|---|
| Runs in window (2026-10-06 to 10-09) | 100: 54 success, 31 cancelled, 13 failure, 2 other |
| Summed run wall time | about 356 min (about 6 h; ESTIMATE, includes queued time) |
| Billing | repo is public, so standard runners are free; billing endpoint unavailable |
Trend: about 100 runs in 3 days (concurrency cancels). Consider `paths-ignore` for docs/reports to cut churn.

## Local disk (source: du, df)
| Path | Size | Trend |
|---|---|---|
| data/colab | 7.2 GiB (rclone_stage 3.9 GB, runs_local 2.5 GB, runs 449 MB) | growing with each Colab job; stage copies duplicate Drive |
| data/backups | 202 MB (1 copy, nba_20261008.duckdb 212 MB) | planned 7 rotating copies = about 1.5 GB cap, trivial |
| nba.duckdb | 202 MB | |
| data/backfill_db 607 MB, data/rehearsal 207 MB, data/pbp 103 MB, availability_official 108 MB, stack 79 MB | | static |
| data/kalshi | 15 MB (raw 10 MB; kalshi.duckdb 4.8 MB) | first snapshots 10-08 6.3 MB, 10-09 3.1 MB (partial day); estimated 5-7 MB/day, about 2 GB/yr; far below 200 MB/day |
| registry_store | 1.8 MB | |
Disk: 270 GiB free of 460 GiB (36% used). Nothing grows above 200 MB/day except data/colab on heavy Colab days (about 0.5 GB+ pulled/staged per job).

## Recommended actions
1. Rclone client_id retirement: create your own Google Drive client_id (https://rclone.org/drive/#making-your-own-client-id) and run `rclone config` to set `client_id`/`client_secret` on the gdrive and nbacolab remotes, before the shared id stops working in 2026. Otherwise Colab hand-off (push/pull) will break.
2. Fill the three `colab_units_per_*_hour` rates and paste a fresh `colab_units_remaining` after tonight's runs, so units and burn can be computed.
3. Delete checkpoints of completed runs (commands above, about 3.15 GiB). Trim local staging after verifying pulls: `du -sh data/colab/rclone_stage data/colab/runs_local` (6.4 GB, candidates for pruning; not deleted).
4. Pull ctxres_v2_sweep/20261008_140943 artifacts (never pulled; timings unknown) or record it as not needed.
5. A100 is the most expensive per hour: pbp_gpt sampling took 2 h of 2.65 h; run sampling on L4/T4 or reduce samples next time.
6. Compact Kalshi raw JSON older than 30 days into one archive when it passes about 100 MB (currently unnecessary).
7. Add `paths-ignore: docs/**, reports/**` to the CI trigger to reduce cancelled runs.
