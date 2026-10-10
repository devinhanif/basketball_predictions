# F9b young top-10 picks: role-anchored prior, re-registered with a draft-year season index (pre-registration)

Status: DRAFT 2026-10-09, to be committed by the maintainer BEFORE any fit. Everything above the line
`=== RESULTS BELOW ===` is frozen. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F9b_YOUNG_PICK_PRIOR.md | shasum -a 256`
(record the first 8 hex chars in the results section and in every ledger row). No candidate exists yet.
This is a NEW rule, the single follow-up F9 allowed; F9 stays closed and unedited. After F9b there is no F9c.

## What Devin said
> "Reed Sheppard ... got the role and was expected to do his best; 'he's young so he's volatile.'"
> "Pedigree lasts 3-4 years; after that teams move on to the next new guy." (FAN_KNOWLEDGE, rounds 3-4)

## What F9 got wrong (T184, reports/F9.md) and how F9b differs
F9 closed at its own minimum-data checks with no model fitted, because the rule was mis-specified, not
because the idea was tested. (1) It treated `draft_pick = NULL` as missing data; 139 of 630 players with
>= 20 games in 2022-24 are undrafted, a category, so check 1 (77.9% < 90%, NaN gap 13.4 pp) could never
pass. (2) It counted seasons from our loaded games, which start in 2022, so in 2023 every player with a 2022
game had index 2 and slice S was mostly mislabelled veterans (only 1,043 of 6,022 rows had
`season - draft_year <= 1`; veterans slice empty). F9b: undrafted is its own level with a "draft status known"
check; the season index comes from the draft year / first NBA season; the slice and its minimum n are
re-derived from the real counts F9 observed. Everything else (arms, metrics, gates, seasons) is carried over.

## Hypothesis
H1: for players in their first 2 NBA seasons with a top-10 pick who hold a real role, production's recency
prior is too conservative in centre and spread. A role-anchored prior (draft slot x true seasons-in-league
decay x minutes/usage share and trend) added to context_residual lowers integer-support pts CRPS on that
slice, moves slice bias toward 0 and slice 80% PIT coverage toward 0.80, without worsening overall CRPS.
Null: no change. Protects against: fitting a story to one team (HOU/Sheppard).

## Data exposure (honest)
* Seen, descriptive only, season 2025 (2025-26 replay): slice pts bias -0.561 (n=1,300) vs -0.06 other
  young (n=5,358) vs +0.094 veterans; cov80 0.776 / 0.813 / 0.795. Season 2025 is the frozen holdout and is
  NOT loaded; no holdout row is consumed. That descriptive was computed with the truncated index, so it is
  motivation only, not evidence about this slice.
* F9 fitted no arm. Seen from F9: only row counts (6,022 / 912), draft coverage, and the 1,043 / 825 counts of
  slice rows with `season - draft_year <= 1`. No outcome-by-slice number for 2023/2024 has been computed.
* Selection season 2023, report season 2024, never 2025. Only `season <= 2024` loaded (in-memory filter),
  `nba.duckdb` opened `read_only=True`. Same walk-forward as production: month blocks, train strictly before
  the block, 2022 warm-up, rows = played, `n_prior >= 5`, 4 stats, seed 0, CPU.

## Fixed design (ONE candidate; constants final)
Flag `ContextResidualConfig.young_pick` (default `off` = byte-identical output, unchanged forward
fingerprint; F9's flag is re-used with the new column definitions). When on, the columns below are appended
through `stat_feature_names(stat, extra=...)` for ALL four stats' mean and scale heads. All as-of the game date.

**Draft status (a)**: `yp_status` in {drafted, undrafted, unknown}. drafted = `players_static.draft_pick`
non-null. undrafted = pick null AND the nba_api commonplayerinfo raw field (`DRAFT_YEAR`/`DRAFT_NUMBER`) reads
"Undrafted" (positive evidence, not mere absence). Players with a draft_year but null pick are
unknown-pick drafted (code NaN). Prerequisite before the checks: if the raw status string is not cached,
resolve it with one resumable cached commonplayerinfo pull for the ~139 players (data ingest, no fit, no
outcome data; record the pull date). Anything still unresolved is `unknown`.
**First NBA season (b)**: `first_season` = `players_static.first_season` (new column, from nba_api
`FROM_YEAR`, the first NBA season start year, same integer convention as `games.season`). Fallback when null:
`draft_year` (rookie season = draft year; flagged `fs_fallback=1`, share reported). Fallback 2 (neither
known): the player's first loaded game season, flagged `fs_truncated=1`; such rows are excluded from slice S
and veterans and reported. Delayed-debut draftees are handled by `FROM_YEAR`; with the fallback they appear
one-plus seasons "younger" than reality, and the number of S rows whose index differs by source is reported.

| column | definition |
|---|---|
| `yp_pick_ord` | draft pick 1..60 as an ordinal; **61 = undrafted** (explicit code); NaN only for `unknown` |
| `yp_undrafted` | 1 if undrafted, else 0 (NaN if unknown) |
| `yp_seasons` | `season - first_season + 1` (rookie = 1); static, known at the draft/debut, so no future games used |
| `yp_w` | pedigree weight `{1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25, >= 5: 0}` x `1[yp_pick_ord <= 10]` (c: the 3-4 year decay Devin stated, fixed; undrafted = 0) |
| `yp_min_share` | as-of `min10 / 240` (production's decayed minutes) |
| `yp_min_trend` | `(min5 - min100) / 48` |
| `yp_pts_share` | `m10_pts / T10`, team points per game over its prior 10 games |
| `yp_w_x_role` | `yp_w * yp_min_share` |

Slice S (reporting only, d): `yp_seasons <= 2` AND `yp_pick_ord <= 10` AND as-of `min10 >= 20`.
Complements: other young (`yp_seasons <= 2`, not S, incl. undrafted) and veterans (`yp_seasons >= 5`).
Seasons 3-4 top-10 players are reported descriptively (A1 vs A0 dCRPS, no gate): the 3-4 year decay is
written into the feature but cannot be confirmed by this slice, and is not claimed.

## Arms (e; identical rows, seeds, calibration windows)
* A0 production (control; must reproduce the LOWER_TAIL pts integer CRPS 3.1528 (2023) / 3.1884 (2024)).
* A1 candidate = A0 + the 8 columns.
* A2 placebo: A1 with `yp_pick_ord`/`yp_undrafted` permuted across players within (season, min(`yp_seasons`,5))
  and `yp_w`, `yp_w_x_role` recomputed from the permuted pick (breaks pedigree, keeps role and decay shape).
* A3 role-only: A1 without `yp_pick_ord`, `yp_undrafted`, `yp_w`, `yp_w_x_role`.
A2 and A3 are controls except through gate 5.

## Metrics, support, inference (f)
* Integer support: every arm mapped with `ceil(q - 0.5)` before CRPS or events (BEST_PRACTICES).
* Primary (per season, per stat): paired dCRPS (A1 - A0) on slice S; game-clustered bootstrap (cluster =
  `game_id`, 2000 resamples, seed 0). Pts is the hypothesis stat; reb/ast/fg3m run as declared guards.
* Also: overall and complement-slice dCRPS; slice mean bias (stat - mean); continuity-corrected randomized
  PIT 80% coverage (seed 0) and lower-tail PIT at q0.10; threshold log loss at pts 10/15 (secondary).
* Multiplicity: BH over the 4 stats' slice-dCRPS p-values within each season.
* Power: MDE (1.96 x clustered SE of slice dCRPS) printed next to each result. A slice with < 600 rows in a
  season is "underpowered, not a null" and cannot pass.

## Minimum data checks (g; fail = stop, report, no model result)
1. Draft status known (`yp_status != unknown`) for >= 99% of players with >= 20 games in 2022-24 (n=630 in F9;
   F9 found 491 drafted, 137 undrafted-with-no-draft-year, 2 with a draft_year and no pick; so >= 99% is
   reachable once raw "Undrafted" is read). Missingness audit: NaN rate of `yp_pick_ord` (unknown only) in
   played<10 min rows vs the rest differs by <= 2 pp; the undrafted share by minutes bucket is listed, not gated.
2. `first_season` known (FROM_YEAR or draft_year fallback, not first-loaded-game) for >= 98% of those players;
   `fs_fallback` and `fs_truncated` shares reported.
3. Slice S (draft-year index) has >= 600 rows, >= 12 players and >= 300 games in each of 2023 and 2024.
   Evidence it can pass: F9 counted 1,043 (2023) and 825 (2024) rows with `season - draft_year <= 1` inside
   its slice (both above 600; 2024's 912-row F9 slice had 19 players). Thresholds were set from these counts,
   not from any effect. Also: veterans slice (`yp_seasons >= 5`) >= 3,000 rows each season (F9: empty only
   because of truncation; 2025 replay had 19,790).
4. Planted future: a fake game dated tonight or later for a player leaves every `yp_*` column unchanged
   (static fields use draft date / FROM_YEAR, not ingest date). Reuse tests/props/test_young_pick.py, extended.
5. A0 reproduces production to 1e-6; flag `off` byte-identical.

## Pass rule (per stat; all of 1-5 hold on 2023, then again on 2024)
1. Slice dCRPS point <= -0.02 (integer CRPS units), CI upper < 0, BH p < 0.05.
2. Slice |bias| of A1 <= 0.75 x |bias| of A0 AND |bias| of A1 <= 0.5.
3. Slice 80% PIT coverage of A1 in [0.75, 0.85] and |cov - 0.80| not larger than A0's.
4. Overall dCRPS point <= +0.001 and CI upper <= +0.003; complement slices (other young, veterans) dCRPS
   point <= +0.003; overall 80% coverage in [0.75, 0.85]; no slice of {n_prior < 20, starter/bench, first 15
   team games, teammate-OUT} with n >= 300 worse by more than +0.01 CRPS.
5. Attack gate: A1 beats A2 on the slice by point <= -0.01 and is no worse than A3 by more than +0.005 (if A3
   matches A1 the gain is role, not pedigree: reported as "role effect", F9b NOT confirmed).
Secondary metrics cannot rescue a failure. 2023 decides whether a stat proceeds; 2024 confirms. Nothing is
promoted; a confirmed stat gets a DRAFTED (not appended) holdout row and a shadow-logging recommendation.

## Kill criteria
* Check 1, 2 or 3 fails: close as "data/power insufficient"; no other cut-offs, no F9c.
* Fails gate 1 on pts in 2023: close; no other pick cut-offs, decays or role definitions.
* Gate 4 fails on any overall/complement check: closed as harmful regardless of slice gain.

## Outputs (exact)
`reports/prereg_f9b/results.json` and `reports/prereg_f9b.md` with tables `[stat, season, arm, slice, n_rows,
n_games, crps_int, dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80, pit_q10, tll_pts10_15]` and `[check, value,
threshold, ok]` for checks 1-5; plus seeds, git SHA, frozen sha256. Ledger: one row per (stat, season,
A1 vs A0, slice S) and one overall; `not holdout (<=2024)`.

=== RESULTS BELOW ===

Run 2026-10-10; frozen sha256 884afcdb (verified against a461f49 before the run). Ledger T186-T201.

Frozen sha256 (first 8): 884afcdb. Checks 1-5: all passed; seasons <= 2024 only, nba.duckdb read-only, CPU, seeds {'model': 0, 'bootstrap': 0, 'pit': 0, 'placebo': 0}.

* pts: 2023: slice S n=948 dCRPS -0.0029 [-0.0195, +0.0143] p_BH 0.780 MDE 0.0169; overall -0.0022 [-0.0049, +0.0006] | 2024: slice S n=759 dCRPS -0.0063 [-0.0197, +0.0071] p_BH 0.844 MDE 0.0134; overall -0.0015 [-0.0038, +0.0009]
* reb: 2023: slice S n=948 dCRPS -0.0059 [-0.0123, +0.0010] p_BH 0.364 MDE 0.0066; overall +0.0007 [-0.0005, +0.0018] | 2024: slice S n=759 dCRPS +0.0028 [-0.0043, +0.0095] p_BH 0.844 MDE 0.0069; overall -0.0003 [-0.0012, +0.0006]
* ast: 2023: slice S n=948 dCRPS -0.0025 [-0.0067, +0.0016] p_BH 0.413 MDE 0.0041; overall -0.0011 [-0.0020, -0.0002] | 2024: slice S n=759 dCRPS +0.0001 [-0.0034, +0.0037] p_BH 0.910 MDE 0.0035; overall -0.0001 [-0.0009, +0.0006]
* fg3m: 2023: slice S n=948 dCRPS +0.0018 [-0.0018, +0.0051] p_BH 0.413 MDE 0.0034; overall -0.0002 [-0.0008, +0.0004] | 2024: slice S n=759 dCRPS -0.0003 [-0.0022, +0.0018] p_BH 0.910 MDE 0.0020; overall +0.0002 [-0.0003, +0.0007]

Verdict: confirmed on 2024 = []; pts gate 1 failed on 2023 = True (kill criterion: close, no other cut-offs, no F9c). Nothing promoted.

Implementation readings fixed before any arm was fitted (not rule changes): cov80 = central 80% interval coverage (0.10 < PIT <= 0.90) of the continuity-corrected randomized PIT on the integer-mapped grid; bias = y - mean of the integer-mapped quantile grid; MDE = half-width of the clustered 95% CI; gate 5 'role effect' = A3 alone has slice dCRPS <= -0.02 and A1 - A3 > -0.01 (such a stat is reported as role effect, not confirmed); fs_fallback rows (draft_year when FROM_YEAR is NULL) cannot be cross-checked against FROM_YEAR, and draft_year <= FROM_YEAR means a delayed debut would make the fallback index too OLD, not too young as the doc says.
