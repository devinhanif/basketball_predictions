# INFO_PROGRAM_2026_27: eight sources of new pre-tip information as one declared family (umbrella pre-registration)

Status: **DRAFT (modeler, 2026-11-17, branch `agent/modeler-info-program`). NOT FROZEN. No member has been fitted, scored or joined to
any outcome. The modeler never loads season 2025; a stage-2 holdout touch, if Devin chooses it, is logged and run by others.** Origin: DECISIONS 2026-11-17 19:43 (Devin: "do all of the 8", his override of the main
session's recommendation to concentrate on items 1 and 2). This file is the umbrella: it declares the family, the shared metric and
floors, the shared windows, the shared gate, the attack list and the multiplicity rule. Each member that passes its gate gets its own
member rule `docs/prereg/INFO_<MEMBER>.md`, frozen separately (`prereg(INFO_<MEMBER>): <sha256>`), which may tighten but never loosen
anything here. The umbrella itself freezes as `prereg(INFO_PROGRAM_2026_27): <sha256>` of everything above `=== RESULTS BELOW ===`
(`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INFO_PROGRAM_2026_27.md | shasum -a 256`), before the first member freezes.

Intake notes (one per item): `docs/research/info_0{1..8}_*_2026-11-17.md`. Program report: `docs/reviews/info_program_2026-11-17.md`.

## 1. The eight items and where each lives

| # | Item | In THIS family? | Rule id(s) | Owner / state |
|---|---|---|---|---|
| 1 | Later lineup and availability news | No (owned elsewhere) | FORWARD_T15_2026_27 (frozen 5c0dec6d, f2389f5); LATE_REPORT_FORWARD (scoring rule sketched only in the build agent's hand-back, no file in the repo, NOT frozen; prior in PREDICTIONS_LEDGER 2026-11-02 21:01); REPORTER_FEED_LIVE (DRAFT eae9efab on `agent/modeler-reporter-feed`, not frozen); REPORTER_LEAD_TRIAL ($50 cap, approved 2026-11-17 19:33, rule NOT yet written) | FORWARD_PREREG family (T-15: m = 4, its own BH); reporter rules their own family |
| 2 | In-game state | No (owned elsewhere) | IN_GAME_LINE_MODEL (T508, provisional pass, attack: mostly a weak baseline); INGAME_WINPROB_V2 (T513, frozen stage 1); the new in-game rule drafted on `agent/modeler-live-model` | Different forecast time and target (P(finish >= line given state)); its own family |
| 3 | The market's numbers as inputs | **Yes**: member **PROP_LINE_INPUT** (the book's main line as an input to the props distribution) | spread and total as inputs: CLOSED by GAME_MARKET_CONTEXT (T389-T414, re-derived T501), not re-proposed | this program |
| 4 | Minutes restrictions and return from injury | **Yes**: member **RETURN_RAMP** (games since return, absence length, report reason) | stated limits: REPORTER_FEED_LIVE M1/C1 (owned); back-to-backs and load: CLOSED (WORKLOAD T451-T454, WORKLOAD_PTS_V2 T521, PPLAY_CONTEXT T316, B2B_ELO gate fail) | this program |
| 5 | Roster moves and role changes | No (owned elsewhere) | MINUTES_DECISION (ROLE_SHIFT part 1, draft ca87c7b2 on `agent/modeler-role-shift`, approved 11-17 to run H1-H3); mover cold start: NOT FREEZABLE (T512, T504) | ROLE_SHIFT program, its own families REP/SL/D/D4R |
| 6 | Referee assignments | **Declared**: member **REF_FOUL_MINUTES** (the one allowed follow-up of REFEREES) | REFEREES (frozen 14613c3a, T238-T243, null) | this program; gate FAILS (section 9) |
| 7 | Schedule context | **Declared**: member **SEASON_STAKES** (as-of standings stakes, last 20 games) | travel, altitude, rest: CLOSED (T521 TRAVEL/SCHEDULE families, T451-T454, T180) | this program; gate FAILS for a live report (section 9) |
| 8 | Kalshi prop price moves as a news signal | **Yes**: member **KALSHI_NEWS_MOVES** (Stage 0 label-free lead, then a forward stage-2 rule; no backtest possible) | none earlier (LINE_MOVE_TOWARD_MODEL T510 asks a different question) | this program; gate PENDING Stage 0 |

## 2. The family and multiplicity

* **Two stages (coordinator addition, 2026-11-17, from Devin's "why can't we backtest any of this").** Wherever history carries a
  trustworthy known-at time, a member is a backtest first. **Stage 1 (selection, never a claim):** fit or select on season 2023, report
  on season 2024 (walk-forward), as-of rules enforced, the attacks of section 8 run. **Stage 2 (the claim):** for a member that passes
  stage 1, the designed confirmation is ONE logged touch of the frozen holdout (season 2025: CLAUDE.md, one logged touch per
  never-evaluated model, logged by the archivist before the run, Devin's decision each time; the modeler neither reads it nor logs it)
  and/or the forward look on post-freeze season-2026 live rows for anything about how the live system serves it. A member with no usable
  history (KALSHI_NEWS_MOVES) goes straight to a forward stage 2.
* **Family F-INFO** = one primary **stage-2** test per member whose stage 2 is FROZEN by the membership date **2027-01-15** (23:59 CT).
  Candidates: PROP_LINE_INPUT, RETURN_RAMP, KALSHI_NEWS_MOVES, REF_FOUL_MINUTES, SEASON_STAKES. m = the number frozen by that date,
  written into this file's results section the day after, before any stage-2 outcome is joined to a forecast. Membership is decided by
  label-free gates and by stage-1 results on 2023-24 (selection on seasons the claim never uses), never by a stage-2 outcome. A stage-1
  fail leaves the family before any claim exists. A member frozen after 2027-01-15 is a new family.
* **Benjamini-Hochberg at q = 0.05** over the m stage-2 primary p-values at the family close (the last member's stage-2 look; at the
  latest 2027-04-25 for a forward stage 2). p is the two-sided bootstrap p of the member's primary contrast (2 x min(share of draws >= 0,
  share <= 0), floored at 1/(B+1)). Stage-1 p-values never enter BH.
* **Early word.** BH always rejects a p <= q/m. A member whose stage-2 p <= 0.05/m and which clears its floor gets its word at its own
  stage-2 look. Every other member's word waits for the family close. A member that freezes a stage 2 but never reaches a valid look
  (pipeline failure, coverage kill, Devin declines the holdout touch) enters BH with p = 1.
* **Secondary tests** (other stats, other targets, the market comparison) are BH-corrected within their member only and are
  descriptive: they never carry a pass.
* **What this protects against.** Eight tries manufacture a false win at about 1 - 0.95^8 = 0.34 if each is read alone at 0.05. One
  primary per member, membership fixed outcome-blind, and BH across the members hold the expected false-discovery share at 0.05. The
  floor (below) stops a significant but useless effect from counting.
* **Not pooled.** Items 1, 2 and 5 have their own frozen or drafted families; merging them here after the fact would be the forbidden
  "pool unrelated families after the fact" (TEST_LEDGER line 25). A joint claim across F-INFO and those families is never made.

## 3. Metric and practical floor per target (the project's existing floors)

| Target | Metric | Floor (pass needs point <= floor AND 95% CI upper < 0) | Source of the floor |
|---|---|---|---|
| Props pts, reb, ast, fg3m | integer-support CRPS on the 199-point grid, `ceil(q - 0.5)`, the same grid for every arm (T114-T118) | -0.005 per row (all rows or affected rows alike) | FORWARD_PREREG_2026_27, GAME_MARKET_CONTEXT, TEAMMATE_SITS |
| Minutes (given played) | continuous minutes CRPS | -0.10 min per row | WORKLOAD cell M, MINUTES_DECISION H1 |
| P(play) | log loss, DNP included | -0.005 nats per row | WORKLOAD cell P, PPLAY_CONTEXT |
| At the book line (secondary, never a pass) | log loss of the over event vs the de-vigged market price on the same legs | none (descriptive; "worse than the market" is reported, never hidden) | ODDS_HISTORY, MARKET_ANCHORED_PROPS |

Each member names ONE primary (stat and target) in its member rule; the umbrella fixes them now: PROP_LINE_INPUT = pts CRPS on line
rows; RETURN_RAMP = minutes CRPS on returner rows; KALSHI_NEWS_MOVES = P(play) log loss on all logged rows at T-15; REF_FOUL_MINUTES =
minutes CRPS on foul-prone rows; SEASON_STAKES = P(play) log loss on stake-state rows.

## 4. Windows

* **Fit / select (stage 1):** fit on seasons up to 2023 (2013-2021 from `data/history` where the member needs it, 2022 warm-up), select
  and report the stage-1 contrast on 2024 walk-forward; `season <= 2024` in SQL. A stage-1 pass = point <= floor, CI upper < 0 on 2024
  with the same sign on 2023 (no floor needed on 2023), and every guard of sections 7-8. Stage 1 is selection: it is never quoted as a
  claim.
* **Where a backtest would flatter (not proposed as evidence):** history that lacks the timestamp that made the information knowable.
  The confirmed-lineup feed's timing (no announced-five history; the box starter flag already resolves game-time decisions, T085 WOUNDED),
  reporter posts (a paid archive gives X `created_at`, not our first-seen; deleted posts are missing), intraday official reports for
  2022-24 (two stored stamps a day, a backfill artifact, filename clock 30 min early: INJURY_TIMING rev 3), Kalshi prop and in-game quotes
  (none before 2026-10-21 here, and the 2025-26 season is the holdout), transactions (box-derived moves are known only after the first
  game), referee assignments (history holds the recorded crew, not the morning assignment: mild, stated).
* **Stage 2:** (a) one logged 2025 touch (Devin's decision; the row is drafted by the modeler in the member rule, logged by the archivist
  before the run); (b) the forward look on season-2026 rows logged after the member's own freeze commit, regular season and play-in, one
  efficacy look at END (within 7 days of the last regular-season game, or the first date its minimum n is met if the member rule sets
  one), one kill-only interim at half the projected n (harm: point > +|floor| with the CI lower bound > 0; guard failure; coverage below
  0.70). When a member has both, the member rule names which one is the F-INFO primary BEFORE stage 1 is run (default: the 2025 touch
  for the model question, the forward look descriptive for serving).
* **Open question for Devin and the archivist before any touch:** PROJECT_STATUS (2026-11-16) says "season 2025 is burned", the halftime
  scorer read 2025 box scores (HOLDOUT_ACCESS_LOG disclosure, DECISIONS 2026-10-27 18:41), and the nightly export carries 2025 box rows.
  Whether a 2025 touch is still a clean confirmation for these never-evaluated members is their call; if it is not, stage 2 is the forward
  look only and the gate arithmetic of section 9 for the forward look applies.
* **The LINE_CAL_LIVE lock.** No member joins an outcome of any slate from 2026-10-30 onward to any forecast before LINE_CAL_LIVE's
  scheduled look is recorded (projected 2026-12-01, backstop 2027-01-31). Label-free work (counts, lead times, price moves, report
  changes) is allowed before it.
* **The modeler never reads 2025.** No member's stage 1 reads season 2025, and no Kalshi prop price of the 2025-26 season is ever read.

## 5. The fair baseline each member must beat

* **Production as served at the same forecast time**, with every piece of information production had at that time:
  - T-60 members: the latest `props_context_residual` row with `made_at <= real tip - 60` (P(play) and minutes from the same row).
  - T-15 members: the T-15 lineup arm's row where it logged (`props_context_residual_t15`), else the LATE_REPORT row
    (`props_context_residual_late`), else the T-60 row; the rule records which, per row.
* **Same rows, same DNP handling, same calibration, same grid** for both arms; an arm that sees a row the baseline does not, or vice
  versa, is excluded and counted.
* **The market itself** where the information is a market number (PROP_LINE_INPUT, KALSHI_NEWS_MOVES): at the main line, log loss vs
  the de-vigged price on the same legs, reported beside the primary. A member that beats production but is worse than the market at the
  line is reported as such and earns no betting statement ("markets are a prior, not a target").
* **A free information-equivalent baseline** where one exists: RETURN_RAMP must beat production plus a constant returner shift fitted on
  the same fit seasons (the REPORTER_FEED_LIVE `B_ret` idea), so the arm is credited only for what the ramp adds beyond "returners play
  less".

## 6. Minimum-data gate (non-negotiable 8), the same arithmetic for every member

0. **Which window.** The gate is computed for every stage: stage 1 on 2024 rows (and 2023 for the sign), stage 2 on a 2025 touch
   (projected from the 2024 row count of the same population, label-free; the modeler counts nothing in 2025) and on the forward window.
1. **Projected forward n.** Post-freeze regular-season games: 1,230 minus games played by the freeze date (205 through 2026-11-17 per
   `docs/data_manifests/latest.json`, `games` season 2026; about 7.5 a night). For a 2026-12-01 freeze: about 927 games. Logged share
   at T-15 0.86 (172 of 200, PROJECT_STATUS 2026-11-16); at T-60 taken as 1.0. Rows per game from the member's own measured rate.
2. **Half-width h** of the 95% CI of the primary at that n: from the per-row variance measured on real rows where it exists, otherwise
   scaled from the nearest ledger anchor by sqrt(n), times the member's design effect (player-season 1.5 for per-player treatments,
   BEST_PRACTICES 2026-10-20; 1.2 for status questions).
3. **Planning effect e_plan** from a measured source (a ledger row, a stored-array descriptive, or the member's own select stage), never
   from hope; an oracle ceiling is stated beside it.
4. **Freezable iff** the probability of a pass at e_plan is at least 0.5, i.e. approximately |e_plan| >= max(|floor|, h). The member
   rule prints n, h, e_plan, the ceiling and the power. A member that fails is listed as **not freezable this season** with what would
   change that, after the data audit of non-negotiable 8 (every season held including `data/history`, every source we could add, every
   repairable gap with its cost). A floor is never loosened to pass a gate (T512 precedent).

## 7. Red flags and the missingness audit every member must pass before its result is read

* **Too-good bound.** Each member states before freezing the largest effect its information could plausibly carry (the oracle
  ceiling). A result beyond it is treated as a leak and audited before it is believed. PROP_LINE_INPUT: more negative than the actual-
  stints oracle -0.058 pts (F11, T244-T245); RETURN_RAMP: beyond the constant-correction oracle of about -0.42 min on first-game rows;
  KALSHI_NEWS_MOVES: beyond -0.02 nats on all rows.
* **Single feature group.** Each member adds one information group by construction. Inside it, if one column carries the whole gain
  and that column's known-at is the weakest in the group (for example the newest line snapshot, the report reason updated after tip, a
  price print stamped by the exchange rather than our receive clock), the result is held as a suspected leak (the `opp_adjusted_ridge`
  fingerprint) until a knockout and a planted-future test clear it.
* **Missingness audit (the NULL-pattern leak).** For every new column: null rate by season, by played vs unplayed, by realised-minutes
  bucket and by report status. Gap between realised-minutes buckets > 2 percentage points, or played vs unplayed > 2 pp, fails unless
  the as-of cause is shown and a positive control (planted dependence) is detected by the same audit (MINUTES_DECISION, T450 lesson).
* **Known-at.** Every input carries its known-at (our receive clock, never the source's created_at), and `known_at <= forecast
  made_at` holds for 100% of rows; a single violation voids the row and is counted; more than 0.1% voids the look.

## 8. Attacks (run on every claimed pass before anyone believes it; the adversary owns them)

1. **Shuffled labels**: within-date for P(play), within-game for props and minutes; the gain must vanish (guard sized from a
   permutation null on the select season, BEST_PRACTICES 2026-10-12).
2. **Planted future**: shift every new input's known-at by +24 h (the T-60 or T-15 set must empty or the forecast must equal
   production bit for bit); plant a future value and require it to be refused.
3. **Placebo information** (the T526 rule, not only a label shuffle): the same input taken from a different player or a different night
   of the same player (PROP_LINE_INPUT: the player's line from his previous game; RETURN_RAMP: the returner flag moved to a non-return
   game; KALSHI_NEWS_MOVES: a move series from a random other player of the same slate). The real arm must beat its placebo.
4. **Knockout**: drop the new group (gain vanishes), drop the top-5 player-seasons, the top team, the best month.
5. **Replication**: second clustering (game, player-season, ISO week, date; the widest binds), a second book or price source where
   one exists, the other select season.
6. **Positive control and "arm saw it"** (BEST_PRACTICES 2026-10-21): a planted effect of floor size is detected by the harness; the
   arm's forecasts differ from production on the rows the information touches.

## 9. Gate results today (2026-11-17; arithmetic in section 6; detail in the intake notes)

### 9a. Stage 1 (backtest 2023 -> 2024): possible, what it needs, run cost, time to first answer

| Member | Stage 1 possible? | Historical data with a trustworthy known-at | Flattering risk | Run cost and where | Time to first answer |
|---|---|---|---|---|---|
| PROP_LINE_INPUT | **Yes** | the-odds-api historical props 2023-24 (already bought): pts 2024 13,021 Pinnacle / 17,993 consensus t60 legs; stored "t60" lead 64.4 min median (T502) | low (snapshot stamps measured) | LightGBM residual refits by month, 4 stats x 2 seasons, about 30-60 min CPU, about 2-3 GB; the Mac outside game windows under `heavy.lock` (trees beat Colab here) | about 3 working days after the umbrella and member freeze (harness 1.5 d, run, attack) |
| RETURN_RAMP | **Yes** | box scores (as-of exact); report reasons 2022-24 (stamps on the filename clock, 30 min early) | moderate on the reason column only: history reason features restricted to stamps <= tip - 90 so a 30 min clock error cannot leak; and the -3 min bias was seen on 2023-24 (REPORTER note), so the 2024 stage-1 result reads "consistent with" | about 10-20 min CPU, Mac outside game windows | about 4 working days (count 0.5 d, harness 1.5 d, run, attack) |
| KALSHI_NEWS_MOVES | **No** | none: capture starts 2026-10-21; 2025-26 Kalshi prices are holdout-season data | n/a | Stage 0 parses capture copies (about 10 s and up to 780 MB a slate, 27 slates), outside game windows | Stage 0 about 1 week; stage-2 answer at END (about April 2027) |
| REF_FOUL_MINUTES | Yes on data (2022-24 recorded crews), **not run**: REFEREES T238-T243 already was this backtest on all rows (null) and the follow-up's ceiling is below the floor | recorded crew, not the morning assignment | mild (late crew changes) | about 15 min CPU if Devin wants it anyway | about 2 days (not recommended) |
| SEASON_STAKES | Yes on data (as-of standings from `games`), **not freezable**: about 2,500 stake rows a season, h about 0.0086 nats > 0.005 in 2024 alone | standings as-of exact | low; 2023-24 seen descriptively (T476) | n/a | n/a |

### 9b. Stage 2 gate (can the confirmation resolve the floor?)

| Member | Primary | 2025 touch: n, h (projected from 2024 counts) | Forward 2026: n, h | e_plan (source) | Result |
|---|---|---|---|---|---|
| PROP_LINE_INPUT | pts CRPS, line rows | about 13,000-18,000 legs, h about 0.0076-0.0090 | 4,380-10,920 legs (797 logged T-15 games x 5.5-13.7 legs), h 0.0154-0.0098 | the stage-1 2024 effect (not yet measured) | **CONDITIONAL on stage 1**: freezable for the 2025 touch iff the 2024 effect <= -max(0.005, h_2025) (about -0.008 to -0.009); the forward look needs <= -0.010 to -0.015. LINEUPS_KNOWN-size information (-0.020 to -0.042) passes both; GAME_MARKET_CONTEXT-size (within 0.005) passes neither |
| RETURN_RAMP | minutes CRPS, returner games 1-3 | about 350 first games x 2.5 = about 875 rows, h about 0.110 min, power about 0.71 | 584-732 rows, h 0.134-0.120 min, power 0.54-0.63 | -0.14 min: 60% of an oracle -0.24 (first game -0.42 at the measured -3.3 min bias, sd 7.25; games 2-3 about -0.12) | **YES** for the 2025 touch; borderline for the forward look; the ramp factor 2.5 and the games-2-3 bias are counted label-free on 2022-24 before the freeze |
| KALSHI_NEWS_MOVES | P(play) LL, all logged rows, T-15 | none (no history) | about 20,700 rows, h 0.0037 nats | unknown until Stage 0 | **PENDING Stage 0** (label-free; no new data). Power needs about 0.08 correctly flagged post-T-15 scratches a game with no false alarms (0.12 with 0.1 false alarms) |
| REF_FOUL_MINUTES | minutes CRPS, foul-prone rows | about 3,900 rows, h about 0.013 min | about 2,900 rows, h about 0.015 min | <= 0.06 min (ceiling translated from REFEREES pts CI, T238-T239) | **NO (effect ceiling below the floor).** Not a data failure: more seasons add n, not effect |
| SEASON_STAKES | P(play) LL, stake-state rows | about 2,500 rows, h about 0.0086 nats | about 2,170 rows, h about 0.0092 nats | 0.0006-0.006 (oracle 0.001-0.010 from T476 rates; 60% capture) | **NO** in any single season (h > floor and e_plan at or below it). History 2019-2024 without 2025 (the report era) resolves the floor (h about 0.0035) but e_plan stays at or below it |

## 10. Order and what changes in production

Nothing changes in production by this document. A member PASS is a recommendation to the adversary and then to Devin (a shadow, then a
promotion decision with evidence). A null closes the member; at most one pre-registered follow-up, then closed. Order of work: the
program report, section "Order of work".

## 11. What each choice protects against

* One primary per member, BH over a membership fixed outcome-blind: eight tries posing as one win.
* Existing floors, not new ones: a floor chosen after looking at the expected effect.
* Stage 1 on 2023 -> 2024 as selection, the claim only from a stage 2 (one logged 2025 touch or post-freeze live rows): a rule tuned on
  the rows it is judged on; a backtest quoted as a claim; a history without the real known-at timestamp quoted as evidence.
* Production at the same forecast time as the baseline, plus the market and a free equivalent: crediting the arm for the comparator's
  missing information (the sim and T508 lessons).
* Integer grid for every props arm: the support artifact (T114-T118).
* Too-good bound, single-group flag, missingness audit, known-at on our clock: the `opp_adjusted_ridge` and NULL-pattern leaks.
* The LINE_CAL_LIVE lock: reading a locked slate's outcomes through a side door.
* Gate before freeze with the audit of non-negotiable 8: freezing rules that cannot pass (F9, F12), and failing a gate without first
  showing the data are complete.

=== RESULTS BELOW ===

(empty: nothing is fitted or scored under this draft)
