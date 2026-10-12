# RETURNING_TEAMMATE_LIVE_T30: when a teammate is confirmed back at T-30, does the T-30 arm over-forecast the players who covered for him? (pre-registration)

Status: **DRAFT (written by the modeler Mon Oct 12 15:47 CDT 2026, clock read by `date`). Not frozen.** Forward live rule for the 2026-27
regular season only; **no history verdict**, season 2025 is never loaded or touched under this rule, and no 2025 touch is ever earned.
It freezes only by a hand that can commit, after Devin reads it and before the first logged row: `prereg(RETURNING_TEAMMATE_LIVE_T30):
<sha256>` of everything above `=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}'
docs/prereg/RETURNING_TEAMMATE_LIVE_T30.md | shasum -a 256`). Origin: RETURNING_TEAMMATE (DRAFT, gate FAIL at T-60; its one allowed
follow-up is this T-30 version) and DECISIONS 2026-10-12 (Devin: "props yes, lets see this"). **It cannot run until the steward ships
the shadow logging listed under "Dependencies"; forward-only, no back-fill.**

## The rule in six lines (recommended form, P1; the form Devin asked for is P2 and is reported, see "Gate")
1. Population: every logged T-30 row (`props_context_residual_t30`, pts) of a regular-season or play-in game of season 2026 whose team has
   at least one **confirmed returner** at T-30 (`ret_min_t30 > 0`, below) and whose player is not that returner: the **flagged rows**.
   Unflagged rows of the same logged games are the control.
2. **P1 (mechanism, the pass):** D = mean(y - m30) over flagged rows minus mean(y - m30) over unflagged rows, where m30 is the T-30
   arm's integer median (10th of the 19 stored quantiles, integer support). 95% percentile bootstrap clustered by **date** (2,000 draws,
   seed 20262027; game-clustered reported). **Pass: D <= -0.25 pts AND CI upper < 0** (the arm over-forecasts the covering players).
3. **P2 (Devin's arm, reported, no pass in 2026-27):** RT30 = the T-30 heads plus `ret_min_t30`, `ret_min_star_t30`, `n_ret_t30`,
   `ret_own_gain` (shadow-logged); integer CRPS RT30 minus T30 on the flagged rows, game-clustered CI. Its minimum-data gate fails in
   one season (below), so it is shown with its CI at every look and decides nothing until pooled with a later season under a new id.
4. Looks: weekly scorer, **interim (kill only) at >= 1,500 flagged rows**, **final at >= 3,000 flagged rows AND >= 60 dates** (first
   Sunday both hold; estimated 2027-02-28 to 2027-03-14). No look before the final is a claim.
5. Guards: every row's `made_at` and lineup `snapshot_fetched_at` strictly before tip minus 30 min (real tip); flag rate by realised
   minutes bucket flat within 0.02 (missingness/leak audit); the returner's own rows excluded; logged share of games >= 0.70 (Wilson
   lower bound) or the result is "keep shadowing".
6. A P1 pass is a recommendation to red-team and to Devin (a T-30 correction is worth building); never a bet, never a production change
   by itself. A null is closed; one follow-up at most.

## Hypothesis
When a rotation teammate who missed >= 3 straight team games is confirmed active at T-30, the players who absorbed his minutes give
some back, and a forecaster whose recency windows are full of absence games over-forecasts them on the return night. Post-hoc
evidence (T476, 2023-24, stored A0, the star DID play): rotation teammates of a 30+ mpg returner after >= 3 missed games, -0.81
[-1.06, -0.55] pts vs baseline on return game 1 (n 2,039). At T-60 the flag failed because the returner's status is not known
(returner played 49% of flagged games; RETURNING_TEAMMATE gate). At T-30 the confirmed lineup feed says who is Active: the
information the T-60 rule lacked (the house lesson: later information is the lever).

## Columns at T-30 (as-of; definition carried from RETURNING_TEAMMATE variant B, status replaced by the T-30 feed)
For player i of team T in game g (real tip t, cutoff c = t - 30 min):
* **Returner j:** played for T earlier this season; played none of T's last >= 3 games before g; his last played game anywhere before
  g's date was for T; listed with any status on the latest official-report snapshot stamped <= real tip - 60 of **T's previous game**;
  and **Active** (`roster_status = 'Active'`, not Inactive) for T in the latest lineup snapshot with `fetched_at < c` whose both fives
  are Confirmed (the frozen dedup rule `nba/lineups/store.py::dedup_snapshot_rows`, the same snapshot the T-30 arm uses). A game with
  no usable confirmed snapshot has no T-30 row at all (it is a T-30 skip, counted).
* `ret_min_t30` = sum over returners j != i of j's mean minutes over his last 10 played games before g; `ret_min_star_t30` = the same
  over returners with that mean >= 30; `n_ret_t30` = count; `ret_own_gain` = i's mean minutes over T's last min(5, longest streak)
  games before g where i played, minus his mean over his last 40 played games before that window (0 if empty or nobody returns).
* Sources at T-30: box rows of earlier games, the official report (previous game's T-60 snapshot only), the T-30 lineup snapshot. Not
  tonight's report (the T-30 arm's report rule is unchanged: `min(now, tip - 60)`), not tonight's box.
* **Training proxy for RT30 (P2 only):** history has no lineup feed (snapshots start 2026-10-09), so RT30's heads are fit with "Active
  at T-30" replaced by "played tonight" (box minutes > 0), exactly as the T-30 arm is fit on box-score starters and served announced
  starters. Train/serve skew stated, not corrected; an Active returner who then sits makes live flags noisier than the proxy.

## Gate (non-negotiable 8), measured Mon Oct 12 14:47-14:51 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.returning_teammate_t30_gate` (read-only `nba.duckdb`, every query `season <= 2024`,
7 s; JSON `reports/prereg_returning_teammate_t30/gate.json`). **Lineup snapshots do not exist for 2023-24** (the collector started
2026-10-09; 17 games), so the gate is sized from the T-60 data with the proxy "returner played tonight" for "Active at T-30". The proxy
is generous (Active is not the same as played). Per-row sd and design effect of a CRPS difference come from the stored FULL-minus-A0
pts deltas on the proxy-flagged rows (a heads refit moves every row); the CRPS effect is outcome-free (A0 grid shifted by
-0.81 x min(ret_min / 33, 2) on rotation rows, p = 1); the residual sd is a variance of the comparator's residuals on the flagged rows
(its mean was not computed).
* Flagged rows (2023 / 2024): 4,746 / 5,222 (17.2% / 18.9% of rows; 3.60 / 3.96 per game); rotation teammates 4,264 / 4,633.
* **P2 (CRPS, RT30 - T30, flagged rows):** sd_row 0.343 / 0.318, deff (worst of game/date, floor 1) 1.00 / 1.12; expected effect at
  p = 1 **-0.0153 / -0.0174**. Rows needed for a half-width <= 0.005: **18,100 / 17,400 flagged rows = about 5,200-5,900 games**, i.e.
  about 4.5 seasons. One live season (1,230 games x 0.85 logged share x 3.6-4.0 rows = 3,760-4,140 rows): half-width **0.0110 / 0.0103:
  FAIL** (> 0.005); the effective pass line becomes about -0.011, power 0.78-0.91 at the generous effect and 0.28-0.38 at half of it.
  Pooled with a second live season: half-width 0.0073-0.0078, still FAIL. **P2 cannot pass its own gate within two seasons; under
  non-negotiable 8 it is not a pass-carrying rule.** This is the honest answer to the question as posed.
* **P1 (bias, flagged minus unflagged):** residual sd 5.92 / 5.81 pts per flagged row, deff 1.00 (measured below 1 at both clusterings,
  floored). Expected shift at p = 1: **-0.45 / -0.49 pts** (the post-hoc -0.81 scaled by returning minutes, on rotation rows). At the
  final look (3,000 flagged rows, about 13,000 unflagged in the same games): se about 0.119 pts, half-width about 0.23 (<= 0.25:
  PASS), pass line -0.25; **power 0.95 at the generous shift, 0.42 at half of it (-0.23)**. n_min for a half-width <= 0.25 pts: about
  2,650 flagged rows (with the unflagged control at 4.3 per flagged row).
* Unknowns that move the gate: the live logged share (FORWARD_PREREG placeholder 0.85; re-estimated at L14), the share of Active
  returners who play (not measurable before the season), and whether the T-30 arm's `t30_n_changed` already absorbs part of the signal
  (a returning starter changes the five; a returning bench player does not).

## Looks and dates (planning; 2026-10-20 opening, about 49 games a week, logged share 0.85, 3.6-4.0 flagged rows a game, so about
150-165 flagged rows a week)
* **Interim, kill only, at >= 1,500 flagged rows:** about 9-10 weeks, week ending **2026-12-27 to 2027-01-03**. Kills: (a) D's
  date-clustered CI lower bound > 0 (the arm reliably UNDER-forecasts the covering players: the hypothesis is wrong in sign); (b) a
  guard failure (any row stamped after its cutoff, or a flag rate by realised-minutes bucket differing by > 0.02) not fixed by a
  documented, result-blind repair; (c) projected final later than 2027-04-11 (end of the regular season). The interim cannot pass.
* **Final at >= 3,000 flagged rows AND >= 60 dates:** about 18-20 weeks, week ending **2027-02-28 to 2027-03-14**. If the season ends
  first, the final is the last regular-season Sunday with whatever n exists, and P1 is reported with its gate status.
* Weekly offline scorer (Sundays), read-only; no `heavy.lock` needed.

## Multiplicity and population
Family = {P1}; P2 and the PIT version of P1 (mean mid-PIT minus 0.5 on flagged rows) are descriptive. Secondary stats (reb, ast, fg3m)
D values reported, BH over m = 3, labels only. Slices (no pass role): star returner present (`ret_min_star_t30 > 0`), returner a usual
starter vs bench (the `t30_n_changed` overlap), own_gain above/below 0, first vs second half of the look.

## Why these choices (what each protects against)
* **Flagged minus unflagged, same arm, same games:** the T-30 arm's median residual is not zero on average for a skewed stat; the
  difference cancels that, so the test is about the return nights, not the arm's general skew.
* **Date clustering primary:** a returner's whole team shares a date and a game; several teams on one night share news.
* **Floor -0.25 pts:** below about a quarter point a correction is not worth a model change (the T-30 pts arm's median error is ~4-5).
* **P2 kept but stripped of pass power:** Devin asked for the arm; its CRPS gate fails in one and in two seasons, so it informs and
  cannot claim (non-negotiable 8).
* **The returner's own rows excluded:** his own minutes are the most uncertain in the game and are not the hypothesis.

## Dependencies (steward; this rule changes no ops script and no nba/daily file)
1. **Column builder** (modeler, `nba/props/returning_teammate.py`, pure function, unit-tested with planted future and the lineup
   status): `returning_columns_t30(games, pgs, report_rows, lineups) -> DataFrame[game_id, team_id, player_id, ret_min_t30,
   ret_min_star_t30, n_ret_t30, ret_own_gain, returner_ids]`, from the T-30 `GameLineup.active` sets and the previous game's T-60
   report snapshot. The modeler can build it after the freeze; it is not built now.
2. **Shadow log table** in `nba.duckdb` (steward): `forward_t30_rt_features(run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,
   tipoff TIMESTAMP, cutoff TIMESTAMP, snapshot_id VARCHAR, snapshot_fetched_at TIMESTAMP, report_snapshot_prev_game TIMESTAMP,
   team_id INT, player_id INT, ret_min_t30 DOUBLE, ret_min_star_t30 DOUBLE, n_ret_t30 INT, ret_own_gain DOUBLE, returner_ids JSON,
   version VARCHAR)`, one row per T-30-logged player row (including zeros), idempotent per (game_id, player_id, version); a
   `LeakageError` if `made_at >= tipoff - 30 min` or `snapshot_fetched_at >= cutoff`.
3. **Hook** (steward, `nba/daily/t30.py::run_t30`, after `load_game_lineup` and `t30_prop_predictions` for the same logged games): call
   the builder with the same `lineups` dict and `report_out` the arm used; append the feature rows in the same transaction as the
   `props_context_residual_t30` rows (same `made_at`, same game set); never on the T-15 tick. P1 needs only this table and the existing
   T-30 rows and `forward_scores`.
4. **RT30 arm for P2** (steward + modeler): model name `props_context_residual_t30_rt`, version `ctxres-v1-t30-rt`, the T-30 model with
   the four columns appended (`stat_feature_names(..., lineups_known=True, extra=RT_COLS)`), fit in the same daily cache step on history
   built with the played-tonight proxy, rows appended to `forward_predictions` in the same batch; settled by the existing
   `forward_scores` path. Optional for P1.
5. **Scorer** (modeler, `nba/eval/returning_teammate_live.py`, fixture-tested, weekly, read-only): integrity gates (cutoffs, idempotence,
   game set equals the logged T-30 set), flag-rate audit, D with date and game CIs, P2 table, slices, kill checks, `reports/
   prereg_returning_teammate_live_t30/week_<date>.json`.
6. **Verification before the first row:** the steward confirms the new table is empty and season-2026 `games` rows are 0 at the
   freeze (as for FULL_SHADOW_LIVE), so the rule precedes every live row.

## Kill criteria (the arm stops and the result is recorded)
(a)-(c) above at the interim; (d) the shadow logging unavailable for > 10% of logged T-30 games; (e) any change to the T-30 arm's
model, features or lineup rule during the look (a new arm is a new id).

## Unstated details chosen before scoring
m30 = the 10th of the 19 stored T-30 quantiles mapped to integer support (`ceil(q - 0.5)`); y from `forward_scores` (`status =
'scored'`, DNP rows excluded from P1, counted); rows of games with id prefix 002 or 005; streak counts team games including the
play-in; "played" = minutes > 0; star = 30.0 mpg; a returner traded mid-absence drops out; ties in snapshots resolved by the frozen
dedup rule; flagged rows per team are all non-returner rows of the team; the unflagged control includes both teams of a flagged game
when the other team has no returner.

=== RESULTS BELOW ===

(not run)
