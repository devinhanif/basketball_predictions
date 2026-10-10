# F9 young top-10 picks: role-anchored prior for context_residual (pre-registration)

Status: DRAFT 2026-10-09, to be committed by the maintainer BEFORE any fit. Everything above the line
`=== RESULTS BELOW ===` is frozen. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F9_YOUNG_PICK_PRIOR.md | shasum -a 256`
(record the first 8 hex chars in the results section and in every ledger row). No candidate exists yet.

## What Devin said
> "Reed Sheppard ... got the role and was expected to do his best; 'he's young so he's volatile.'"
> "Pedigree lasts 3-4 years; after that teams move on to the next new guy." (FAN_KNOWLEDGE, rounds 3-4)

## Hypothesis
H1: for players with <= 2 NBA seasons and a top-10 draft slot who hold a real role, production's
recency prior is too conservative in centre and spread. A role-anchored prior (draft slot x seasons-played
decay x minutes/usage share and its trend) added to context_residual lowers integer-support pts CRPS on
that slice, moves slice bias toward 0, and moves slice 80% PIT coverage toward 0.80, without worsening
overall CRPS. Null: no change. Protects against: fitting a story to one team (HOU/Sheppard).

## Data exposure (honest)
* Seen, descriptive only, season 2025 (2025-26 replay): slice pts bias -0.561 (n=1,300) vs -0.06 other
  young (n=5,358) vs +0.094 veterans; cov80 0.776 / 0.813 / 0.795. Season 2025 is the frozen holdout
  and is NOT loaded by this study; no holdout row is consumed.
* Seen: production OOF for 2023 and 2024 in aggregate (LOWER_TAIL, MINUTES_V2). To the drafter's knowledge
  nobody has sliced them by draft slot. Candidate arms: not computed. Only `season <= 2024` is loaded
  (in-memory filter), `nba.duckdb` opened `read_only=True`.
* Selection season 2023, report season 2024. Same walk-forward as production: month blocks, train
  strictly before the block, 2022 warm-up, rows = played, `n_prior >= 5`, 4 stats, seed 0, CPU.

## Fixed design (ONE candidate; constants final)
Flag `ContextResidualConfig.young_pick` (default `off` = byte-identical output, unchanged forward
fingerprint). When on, 6 columns are appended through `stat_feature_names(stat, extra=...)` for ALL four
stats' mean and scale heads (one design, no per-stat choices). All are as-of the game date.

| column | definition |
|---|---|
| `yp_pick` | `players_static.draft_pick` (NaN if undrafted/unknown); static, known at draft |
| `yp_seasons` | count of distinct NBA seasons with >= 1 played game strictly before tonight's season, plus 1 (= season index; rookie = 1) |
| `yp_w` | pedigree weight `{1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25, >= 5: 0}` x `1[yp_pick <= 10]`; the 3-4 year decay Devin stated, fixed |
| `yp_min_share` | as-of `min10 / 240` (decayed minutes over team-minutes; `min10` is production's) |
| `yp_min_trend` | `(min5 - min100) / 48` (role growth; uses production's decayed minute means) |
| `yp_pts_share` | `m10_pts / T10`, `T10` = team points per game over its prior 10 games (same team as tonight) |

Plus one derived column `yp_w_x_role = yp_w * yp_min_share`. The GBM may use the others freely. Role
condition for the slice is NOT a model input threshold; it is only a reporting definition (below).

Slice S (reporting only): `yp_seasons <= 2` AND `yp_pick <= 10` AND as-of `min10 >= 20`.
Complement slices: other young (`yp_seasons <= 2`, not S) and veterans (`yp_seasons >= 5`).

## Arms (identical rows, seeds, calibration windows)
* A0 production (control; must reproduce the LOWER_TAIL pts integer CRPS 3.1528 (2023) / 3.1884 (2024)).
* A1 candidate = A0 + the 7 columns.
* A2 placebo: A1 with `yp_pick` permuted across players within season (breaks pedigree, keeps role columns).
* A3 role-only: A1 without `yp_pick`, `yp_w`, `yp_w_x_role` (isolates whether pedigree adds anything).
A2 and A3 are descriptive controls except through gate 5.

## Metrics, support, inference
* Support: every arm mapped to integers with `ceil(q - 0.5)` before CRPS or events (BEST_PRACTICES).
* Primary (per season, per stat): paired dCRPS (A1 - A0), integer support, on slice S rows; game-clustered
  bootstrap (cluster = `game_id`, 2000 resamples, seed 0). Pts is the hypothesis stat; reb/ast/fg3m run
  because production-wide features can hurt them (declared, not hunted).
* Also reported: overall dCRPS (all rows) and complement-slice dCRPS; slice mean bias (stat - mean);
  PIT coverage of the central 80% interval, A1.1 style (continuity-corrected randomized PIT, seed 0),
  also lower-tail PIT at q0.10; threshold log loss at pts 10/15 (secondary).
* Multiplicity: BH over the 4 stats' slice-dCRPS p-values within each season.
* Power note: MDE reported per season from the clustered SE (1.96 x SE of the slice dCRPS) next to each
  result; a slice with fewer than 300 rows in a season is "underpowered, not a null" and cannot pass.

## Minimum data checks (fail = stop, report, no model result)
1. `players_static.draft_pick` non-null for >= 90% of players with >= 20 games in 2022-2024; list the
   unmatched with their minutes. Missingness audit: NaN rate of `yp_pick` vs realised minutes bucket and
   vs `yp_seasons` must differ by <= 2 pp between played<10 min and the rest (the ridge-leak pattern).
2. Slice S has >= 300 rows in each of 2023 and 2024 (else the study is declared underpowered).
3. `yp_seasons` for a planted future: inserting a fake game dated tonight or later for a player leaves
   every `yp_*` column unchanged. Static draft fields use the draft date, not the ingest date.
4. A0 reproduces production to 1e-6; flag `off` is byte-identical.

## Pass rule (per stat; a stat passes only if 1-5 all hold on 2023, then again on 2024)
1. Slice dCRPS point <= -0.02 (integer-support CRPS units; slice floor, larger than the -0.005 overall
   floor because the slice is small), CI upper < 0, BH p < 0.05.
2. Slice |bias| of A1 <= 0.75 x |bias| of A0 AND |bias| of A1 <= 0.5 (pts target from the success criteria).
3. Slice 80% PIT coverage of A1 in [0.75, 0.85] and |cov - 0.80| not larger than A0's.
4. Overall dCRPS point <= +0.001 and CI upper <= +0.003; complement slices (other young, veterans) dCRPS
   point <= +0.003; overall 80% PIT coverage stays in [0.75, 0.85]; no slice of {cold start `n_prior < 20`,
   starter/bench, first 15 team games, teammate-OUT} with n >= 300 worse by more than +0.01 CRPS.
5. Attack gate: A1 beats A2 (placebo) on the slice by point <= -0.01 and A1 is no worse than A3 by more
   than +0.005 (if A3 matches A1, the gain is role, not pedigree: reported as "role effect", F9 as worded
   NOT confirmed).
Secondary metrics cannot rescue a failure. Selection: 2023 decides whether a stat proceeds; 2024 is the
confirmation. Nothing is promoted; a confirmed stat gets a DRAFTED (not appended) holdout row and a
recommendation to shadow-log live. The 2025 holdout is not touched by this document.

## Kill criteria
* Check 1 or 2 fails: close as "data/power insufficient"; no follow-up with other cut-offs.
* Fails gate 1 on pts in 2023: close; do not try other pick cut-offs, decays or role definitions (a new
  question gets a new rule). At most ONE pre-registered follow-up, then closed.
* Gate 4 fails on any overall/complement check: closed as harmful regardless of slice gain.

## Outputs (exact)
`reports/prereg_f9/results.json` and `reports/prereg_f9.md` with tables:
`[stat, season, arm, slice, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80,
pit_q10, tll_pts10_15]` and `[check, value, threshold, ok]` for checks 1-4; plus seeds, git SHA, frozen
sha256. Ledger: one row per (stat, season, A1 vs A0, slice S) and one per overall; `not holdout (<=2024)`.

=== RESULTS BELOW ===

(not run)
