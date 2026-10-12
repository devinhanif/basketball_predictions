# COACH_PHASE: coach habits that differ between playoffs and regular season, and shift game by game (pre-registration)

Status: **FROZEN (Sun Oct 11 23:25 CDT 2026) by the main session on Devin's "yes to all", accepting that the coach claim cannot PASS (at most suggestive; shadow column in 2026-27).** Earlier: **DRAFT (Sun Oct 11 23:10 CDT 2026), passes its minimum-data gate (non-negotiable 8) with two stated limits.** Not frozen. The main
session commits `prereg(COACH_PHASE): <sha256>` BEFORE any arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the rule.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/COACH_PHASE.md | shasum -a 256`.
Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25; 2022 is warm-up only; 2025 is the frozen holdout, never loaded).
No arm exists. The only model computation run is the A0 reproduction from stored components (`research/eval/coach_phase_gates.py`).

## What Devin said (2026-10-11)
> "configure coach habits that differ between playoffs and regular season, more so each game of the season"
Framing agreed in the hand-off: TEAM_BUDGET (T446-T449) showed that coach habits at T-60 add nothing in the regular season because the
unknown is who plays, and its oracle says realised minutes shares are worth -0.18 pts CRPS. So this is a PHASE regime for minutes: coaches
shorten the rotation in the playoffs and in meaningful late-season games, differently coach by coach, and a coach's habit is learned game by game.

## Honest prior (read first)
* My estimate that any props cell clears the floor: 10-15%; for the minutes target about 50% for the PHASE arm (production's minutes path is
  visibly worse in the playoffs: continuous CRPS 4.88 / 4.49 min in 2023 / 2024 playoffs and 4.80 / 4.74 in the last 15 games, against
  4.13 / 4.15 early in the season; descriptive production-only numbers, seen), and below 10% that coach identity adds anything beyond the phase.
* The honest reasons: (1) the phase effect (stars play more in the playoffs, rotations shrink, late-season rest and tanking) is shared by all
  coaches and is information the production minutes path does not carry (it has no playoff indicator); that is the real candidate. (2) Coach-specific
  playoff habits rest on thin data: at most 7 (2023) and 9 (2024) coaches have 20 or more earlier playoff games in our data (2022-24); the rest are
  shrunk to the league. (3) TEAM_BUDGET found coach columns add +0.0002 pts CRPS. (4) The realised-minutes oracle bounds everything; a pre-tip
  forecast cannot approach it, and who plays (DNP, rest) is untouched here because the target is minutes GIVEN the player plays.
* Nearest closed neighbours: TEAM_BUDGET (T446-T449), MINUTES_V2 (T162-T173: minutes CRPS -0.83 min but pts -0.0053 / -0.0018), MINUTES_HAZARD,
  ENSEMBLE (blend failed the playoff slice, +0.024), F11 lineup context. Architecture-only ideas have lost every time, so this rule keeps ONE
  declared model class (ridge) and puts all the weight on the information (phase state, coach history).

## Hypotheses
* H1 (phase): a minutes forecast that adds the season-phase and playoff-series state lowers minutes CRPS on playoff and late-season rows versus
  production's minutes path, and the shift carries into integer-support props CRPS for at least one of pts, reb, ast, fg3m.
* H2 (coach regime): adding per-coach as-of habits, separately for regular season and playoffs and updated game by game, lowers both beyond H1.
* Null: no change beyond the floors below. Protects against crediting the phase to the coach (H2 is judged against H1's own arm).

## Data exposure (honest)
Seen: gate counts, noise-scale MDEs, production-only minutes CRPS by slice (above), A0 reproduction, TEAM_BUDGET results (coach columns, regular
season, T-60). Not seen: any arm's output, any habit related to any minutes or stat error, the series or clinch features against any outcome.
Season 2025 not loaded (`season <= 2024` in SQL and code); `nba.duckdb` read-only.

## Rows, slices, venue
Rows are the A0 rows (played, `n_prior >= 5`; 27,619 / 27,701 per stat; minutes rows 27,619 / 27,583), identical across arms.
* PO = playoff games (`game_id` prefix 004). LATE15 = regular-season games in which either team has played 67 or more games (its last 15 of 82).
  PHASE = PO or LATE15. Play-in (005, 6 games a season) belongs to neither slice. Everything else is EARLY (the guard slice).
* Venue: the backtest on 2023 (select) and 2024 (report), pre-tip information only (T-60 report, schedule, standings, earlier boxes).
  **T-30 confirmed starters are used ONLY in the live 2026-27 log (L arm, below)**; history has no T-30 lineups.

## Fixed design (constants final; none tuned)

### Coach identity and habits (as-of)
The coach of the team's PREVIOUS game (TEAM_BUDGET rule: `team_coach_games.known_at` equals each game's own tip, so tonight's coach is never
knowable at T-60; equals the actual coach in 99.6%). Habits follow the coach across teams: every earlier game of his in our data (2022 onward),
strictly earlier dates, kept separately for phase s in {REG, PO}. For each habit x and phase s:
`h_s = (sum_i w_i x_i + K L_s) / (sum_i w_i + K)`, `w_i = 0.5^(a_i / 20)` where a_i is the number of his phase-s games since game i (newest 0),
K = 10 pseudo-games, `L_s` = league as-of mean of x over all earlier phase-s games (PO falls back to REG when fewer than 30 earlier league
playoff games exist). The EWMA is what makes the habit move "each game of the season". Habits (all from post-game boxes of EARLIER games):
`depth` (players with >= 10 minutes), `top5` and `top8` (shares of team minutes), `starter_min` (mean minutes of the players with `starter`),
`bench_share` (share of minutes by non-starters), `blowout` (top-5 minutes in |margin| >= 20 games minus <= 10 games; career-pooled over statuses, as
in TEAM_BUDGET). Coach columns: the six REG habits, the six PO habits, and six deltas `h_PO - h_REG`.
"More so each game of the season" is encoded exactly twice and nowhere else: (i) the EWMA above; (ii) interactions `gp_frac x delta_x` and
`late15 x delta_x`, where `gp_frac = games_prior / 82` (1.0 during the playoffs). Reliability screen run BEFORE any stat error (box data only,
coaches with >= 60 games in 2022-23, odd/even alternate games, Spearman-Brown >= 0.30 keeps a REG habit): TEAM_BUDGET already kept depth,
concentration and blowout; `top8`, `starter_min`, `bench_share` are screened in the first harness stage and dropped if they fail. The PO habits
and deltas cannot be screened (median earlier playoff games per playoff coach: 12 in 2023, 21 in 2024); they enter only shrunk (K = 10), and
that is stated as the reason the coach claim below cannot be a PASS.

### Phase and series state (`research/features/phase.py`, tested: as-of, same-day blind, planted-future)
Season phase: `gp_frac`, `late15`, `clinched_6`, `clinched_10`, `elim_6`, `elim_10` (sufficient conditions from standings over games dated strictly
earlier; ties counted against the team). Playoff: `po`, `series_game` (1-7), `wins_for`, `wins_against`, `elim_game` (opponent already has 3
wins), `close_out_chance`, `is_home`, `home_court`, `round` (digit 8 of `game_id`), and the player's minutes in the previous game of the same series
(first game: his production mean with an indicator). Outside the playoffs the series columns are 0 by construction (structural, audited below).

### Arms (identical rows, folds, seeds; integer support `ceil(q - 0.5)` for EVERY props arm)
* P = production minutes path (`trunc_normal_quantiles(p_mu, p_sigma)`, stored `P_crps` in `reports/minutes_v2/minutes_scores.npz`).
  A0 = production + lower-tail mixture props (`arm_grid("mixture")`): pts integer CRPS 3.140236 / 3.176840 (2023 / 2024); reb 1.288179 / 1.311401;
  ast 0.901954 / 0.898517; fg3m 0.566212 / 0.590155. Reproduced for pts to 5e-7 (gate G1; the other stats were reproduced by TEAM_BUDGET G4 to 0.0).
* PH (phase): ridge regression (alpha 10 on standardized columns, one fit, no search) of `minutes - p_mu` (rows that played) on the phase and series
  columns, each entered alone and times `z_mu = (p_mu - 24) / 12` and times `starter10` (role interactions; a star and a bench player react differently).
  Month-block walk-forward, 2022 warm-up, trained strictly before the block. Minutes arm: `trunc_normal_quantiles(p_mu + delta_hat, p_sigma)`
  (location only; scale from P). Props arm: A0 quantile grid x `r = clip((p_mu + delta_hat) / p_mu, 0.8, 1.25)`, gamma = 1; then the integer grid.
* CP (coach regime): PH plus the 18 coach columns, the interactions of Section "Coach identity", and each coach column times `z_mu` and `starter10`.
* PL (placebo): CP with each coach's whole habit vector replaced by another coach's (permuted across coaches, within season and within phase; marginals kept).
* S0 (simplest phase): ridge on {po, late15} x role only; descriptive, it says how much a plain playoff flag already buys.
* O1 (oracle, descriptive, never in the family): realised minutes in the same factor; bounds what is possible; a non-oracle arm that beats it is a presumed leak.
* L (live, 2026-27 forward log only): CP plus the T-30 confirmed starter flag. Not scored here; its venue (about 85 playoff games, about 1,700 player rows)
  has a noise-scale MDE near 0.025 pts, 5x the floor, so the live log is a sign-replication check and a shadow column, never a floor test.

## Metrics, support, inference
Minutes: continuous CRPS in minutes (199-quantile form, as MINUTES_V2). Props: integer-support CRPS per stat. Paired difference arm minus comparator
(P for minutes, A0 for props); 95% percentile bootstrap clustered by `game_id` (2000 draws, seed 0; the two teams of a game share a cluster); two-sided
clustered p; Benjamini-Hochberg q = 0.05 within each family and season. Each cell prints its own MDE (1.96 x clustered SE).
* Family M (minutes, 4 cells per season): {PH, CP} x {PO, LATE15}, each versus P. Floor **-0.10 min** (the MINUTES_V2 floor).
* Family R (props, 8 cells per season): {PH, CP} x {pts, reb, ast, fg3m} on the PHASE slice versus A0. Floor **-0.005**.
* Coach claim (outside the families; guard, not a headline): CP minus PH on the same rows: minutes <= -0.03 min or props <= -0.002, and the gain does
  not fall by >= 80% when any one habit group is removed alone.
* Secondary, cannot rescue a failure: overall (all rows) and EARLY dCRPS, bias, 80% PIT coverage, lower-tail PIT, threshold log loss, the decision score
  (`docs/DECISION_SCORING.md`: P(over) at the DraftKings T-60 line on PHASE legs, thresholds 0 / 2 / 5 / 10%, ROI per unit with the game-clustered CI,
  "insufficient sample" below 300 bets), O1 and S0 gaps, elasticity gamma = 0.5 (reported, not chosen).
* Descriptive slices (n < 100 rows printed as "insufficient"): starter vs bench, series game 1-7, elimination games, home vs away, round, clinched /
  eliminated status in LATE15, coach in his first 20 games, back-to-back, star-out.

## Minimum-data gates (non-negotiable 8), measured 2026-10-11 on `nba.duckdb` (read-only) and stored components
Reproduce: `.venv/bin/python -m research.eval.coach_phase_gates` (7 s; light; no lock; JSON `reports/prereg_coach_phase/gates.json`).
| slice | 2023 rows / games | 2024 rows / games | noise-scale MDE pts, 2023 / 2024 | noise-scale MDE minutes, 2023 / 2024 |
|---|---|---|---|---|
| all (incl. play-in) | 27,619 / 1,318 | 27,701 / 1,320 | 0.0047 / 0.0045 | 0.031 / 0.030 |
| PO | 1,684 / 82 | 1,804 / 84 | 0.0248 / 0.0235 | 0.126 / 0.087 |
| LATE15 | 5,156 / 244 | 5,134 / 246 | 0.0110 / 0.0118 | 0.091 / 0.091 |
| PHASE | 6,840 / 326 | 6,938 / 330 | 0.0104 / 0.0107 | 0.077 / 0.071 |
* G1 A0 reproduction (pts): raw 3.152830 / 3.187553 and mixture 3.140236 / 3.176840, max difference 4.8e-7, n matches. PASS.
* G2 minimum n: PHASE has 326 / 330 games (gate: 200). PASS. PO alone has 82 / 84 games, which the gate would not pass; PO is therefore not the props
  headline slice but sits in Family M, where the effect scale (-0.8 min in MINUTES_V2) is large against the MDE.
* G3 power, stated honestly: the "noise-scale MDE" is the clustered 1.96 x SE of a deliberately noisy paired difference (raw A0 minus mixture A0 for
  props; recency minus production for minutes), so it is pessimistic for an arm highly correlated with its baseline (the prior studies on these
  rows had real MDEs 0.0006-0.0029 over all rows; scaled to PHASE games by sqrt(1318/328) = 2 that is 0.0012-0.0058). Props PHASE MDE therefore
  lies between 0.001 and 0.011 against a floor of 0.005: borderline. Rule: a props cell whose OWN printed MDE exceeds 0.005 is labelled "cannot
  confirm" and a null there reads as "not detected", never "absent". It can still PASS if the point and CI clear the floor. Minutes cells: PHASE and
  LATE15 MDE 0.07-0.09 < 0.10; PO 0.087-0.126 straddles it (same rule).
* G4 coach playoff history (earlier playoff games of the coach, any team, 2022 onward): 164 / 168 playoff team-games, 16 / 16 coaches; share with any
  earlier playoff game 0.957 / 0.970; coaches with >= 20 earlier playoff games: **7 (2023) and 9 (2024)** (gate for a coach claim: 8); with >= 10: 11 / 12;
  median 12 / 21. The 2024 report season passes, the 2023 select season FAILS. Consequence declared now: the coach claim has no select-season
  confirmation, so it can reach at most "SUGGESTIVE (report season only)", never PASS, and earns only a shadow column in the 2026-27 log.
* G5 series state: derived for all playoff games (max series game 7; game counts by series game 1-7: 30/30/30/30/24/14/6 in 2023 and
  30/30/30/30/26/14/8 in 2024 per team-game side, i.e. 2023 and 2024 each have only 26 and 27 elimination-game team-games, about 260 player rows). The
  elimination-game coefficient is nearly unidentifiable; that slice is descriptive only.
* G6 clinch / elimination in LATE15 team-games (488 / 492): clinched_6 0.098 / 0.163; clinched_10 0.410 / 0.411; elim_6 0.342 / 0.350; elim_10 0.238 /
  0.185. Enough variance to estimate; the tanking and resting interaction is descriptive.
* G7 missingness: structural zeros of the series columns in REG are audited post-freeze by minutes bucket (< 5 / 5-10 / 10-20 / > 20 min): the rate of
  `prev_series_minutes` missing and of every coach column at its league fallback must differ across buckets by < 2 percentage points; the PO coach
  columns at their league fallback are expected for first-time playoff coaches and are reported, not hidden (a feature that is "missing" for exactly
  the low-minute players is the NULL-pattern leak shape).

## Pass rule (per cell; 2023 selects, 2024 confirms; all must hold)
1. 2023: point <= floor with clustered CI upper < 0. 2024: point <= floor, CI upper < 0, BH p <= 0.05 over its family.
2. Guards: `|cov80_arm - 0.80| <= |cov80_base - 0.80| + 0.03`; `|bias_arm| <= |bias_base| + 0.05` (props) / 0.5 min (minutes); EARLY slice not worse than
   +0.003 (props) / +0.03 min; no slice with n >= 300 worse by more than +0.01 (props) / +0.10 min.
3. Attack gates (PASS only if all hold): arm beats PL by a point <= -0.003 (props) / -0.03 min; shuffled-label refit (ridge on `delta` permuted within
   block) gives a difference >= -MDE; planted tests (below) pass with non-inert controls; the G7 audit passes; the arm does not beat O1.
4. Role of the phase: a cell is credited to the COACH only if CP minus PH meets the coach-claim thresholds AND the 2024 report season has >= 8 coaches
   with >= 20 earlier playoff games (it does: 9); otherwise the gain is credited to the phase.
A PASS means a red-team review and a drafted one-touch holdout row. Nothing is promoted or wired in; that is Devin's.

## Checks run after the freeze (fail = stop, no model result, the fix is a new id)
1. G1 (and G4-G6) re-run on the frozen data. 2. Planted same-game: edit the target game's box, minutes, starters and score: every phase, series and coach
column of that game is byte-identical; a control (the NEXT game) must move. 3. Planted-future: any game dated >= the target date is ignored by every
feature (a series game, a standings result, a coach game). 4. Flag-off (all arm deltas zero) reproduces P and A0 to 1e-6; the ridge is deterministic.

## What each choice protects against
* PH before CP, PL, S0, O1: crediting the coach for the playoff effect every coach shares; a bigger model for a plain flag.
* Ridge, one alpha, location-only: a GBM memorizing 1,700 playoff rows; tuning on the select season (60-trial searches added nothing before).
* Coach history from earlier games only, the previous game's coach, EWMA with constants fixed now: the box-score leak and the known_at-equals-tip finding.
* Integer grid for every props arm, A0 = production + mixture, same rows: the T114-T118 support artifact; weak baselines.
* Minutes first, props second, "cannot confirm" labels, no pooling of seasons to manufacture power: a floor the data cannot reach cannot produce a claim.
* No T-30 lineups in the backtest: history has none; a leak by construction.
* Select 2023 and report 2024, 2025 untouched; the coach claim declared incapable of PASS before it can disappoint or tempt anyone.

## Kill criteria
* Checks 1-4 fail or G7 fails: stop. No cell selects on 2023 in Family M: closed; no variant (other half-lives, K, windows, habits, GBM).
* PH fails but CP "wins": reported as a coach story only if the CP-minus-PH claim holds; otherwise a presumed noise artifact.
* One pre-registered follow-up at most (a phase-aware P(play) / who-plays term, or the live L arm on the 2026-27 playoffs), only after a PASS.
  Season 2025: no touch is requested by this rule.

## Outputs and harness
After the freeze: `research/features/coach_habits.py` (the EWMA builder, planted tests) and `research/eval/coach_phase_eval.py` with stages `gate`,
`habits`, `arms`, `attack`, `report`, refusing unless the sha256 matches; per-stage checkpoints in `data/ops/coach_phase_cache/`. Estimated Mac CPU time
(single process, ridge fits are cheap): production minutes table about 3 min, habits about 3 min, 4 arms x 18 blocks under 2 min, scoring plus 2000-draw
bootstraps about 6 min: 15 minutes. The maintainer takes the lock (`mkdir data/ops/heavy.lock`, retry 120 s, `rmdir` in `finally`); the modeler never does.
Command after the harness exists: `.venv/bin/python -m research.eval.coach_phase_eval run`. Today's runnable command (gates only):
`.venv/bin/python -m research.eval.coach_phase_gates`.

=== RESULTS BELOW ===

(not run)
