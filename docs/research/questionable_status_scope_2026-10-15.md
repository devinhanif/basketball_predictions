# Questionable / doubtful / probable at T-60: a scoping inventory (descriptive only)

Written Wed 2026-10-14 23:2x CDT (clock from `date`; file dated -15 per the brief). Archivist, overnight, Devin asleep.
No model fitted for a verdict, no rule written, no test approved. Devin has not approved a test; this is scoping.
Reads: `nba.duckdb` and `data/history/nba_history.duckdb` both `read_only=True`; box scores and minutes read with `season <= 2024` only.
Season 2025: status counts from `player_availability` only; 2025 statuses were never joined to 2025 minutes, points or outcomes.
Season label = start year (2022 = 2022-23). Scratch scripts (not committed): `scratchpad/a.py` (coverage, snapshots), `b.py`/`c.py` (joins, minutes), `d.py` (points CRPS read).

## 0. The premise needs one correction before the numbers

"The props/minutes features only count OUT players at T-60" is true of **production as served**, and false of the rest of the tree:

| path | uses Q / D / P? | evidence |
|---|---|---|
| production props P(play) and minutes (`nba/daily/predict.py:267`, `ReportTriggerConfig(statuses=("out",))`, `slate_report_outs`) | **No.** OUT only, removed before P(play). Doubtful reaches only injury-Elo (game winner, weight 0.5, `nba/models/injury_elo.py`). | T484 attack, `docs/reviews/T482_post_result_2026-10-13.md` "Production as served" |
| `props_context_residual` (`nba/props/context_residual.py:103`, default statuses `("out",)`) | No (OUT vacated stats only) | code |
| P(play) shadow R (`nba/daily/pplay_shadow/features.py`: `STATUS_CODE`, `tag60_code`, `q60`, `d60`, `on_morning_report`, `on_evening_report`) | **Yes, all five statuses.** | code; PPLAY_CONTEXT T316, PPLAY_MINUTES_VALID T482/T484 |
| AVG-shadow sequence model (`nba/daily/avg_shadow/sequences.py`: `st_available/probable/questionable/doubtful/out`) | **Yes, one-hot statuses.** | code |
| lineups T-30 (`nba/lineups/analysis.py::open_decisions`, `GTD_STATUSES = ("questionable","doubtful")`) | Measured, not a feature: share of Q/D players whose lineup is still "Expected" at T-30. Live collection starts 2026-27, so no data yet. | code, `docs/FORWARD_PREREG_2026_27.md` |

So the blind spot is real **in production as served**, but the information has already been tested offline for P(play) and is running in shadow. What is **not** established: whether the tag moves stat CRPS (points, rebounds, assists, threes) once P(play) feeds the props. That is the live PPLAY_INTO_PROPS_LIVE test (frozen 3a305482, first look fg3m about 2026-11-15). Relevant prior evidence (ledger):

- T316 (PPLAY_CONTEXT): the T-60 report **tag** carries the P(play) gain; dropping it costs -0.0084 / -0.0124 log loss (2023 / 2024).
- T482/T484: R19 beats production P(play) by -0.0851 / -0.0918 log loss on served rows; "even a production that knew the outcome of every Q/D player loses by 5x the floor". The gain is spread across groups; removing the whole report group still leaves -0.075.
- QUESTIONABLE_STREAK (draft, gate FAIL, not frozen): a streak/change refinement of the tag has 105-227 rows a season and power about 0 on all rows; recommended closed.
- Nothing in the ledger scores the tag against **stat CRPS** directly.

GTD is not a report status. The official report has only available / probable / questionable / doubtful / out. "Game-time decision" is a reporter/lineup concept; the report reason text carries "GTD" or "game-time" strings on only 6 non-OUT rows across both databases (about 480k rows), so GTD = Q or D plus the T-30 resolution, which is what the lineup collector will measure.

## 1. What is stored, from where, how complete

**Source.** One source: the NBA official injury-report PDFs (`player_availability.source = 'nba_official_report'`), parsed to `(player_id, as_of, game_id, status, reason, pulled_at)`; `as_of` is the report stamp (naive US-Eastern), never the game date. No other source is stored in the table. Live PDFs: `data/availability_official/` (1,682 files, 2022-10-18 to 2026-07-19); history PDFs: `data/history/availability_official/` (5,502 files, 2019-10-21 to 2022-06-16, 707 dates, 38 dates with no report, 0 failed). Both DBs hold status, reason and as_of.

**Rows.** Production DB 118,238 rows (2022-10-18 .. 2026-06-13): out 84,049, questionable 18,858, probable 6,513, available 6,071, doubtful 2,747. History DB 362,790 rows (2019-10-21 .. 2022-06-16): out 283,217, questionable 42,578, probable 13,563, available 18,219, doubtful 5,213. Reasons are present on every row (no NULL reason).

**Known-at.** Every row has `as_of`. Real tip-offs exist for 100% of games in 2019-2025 (`data/schedule`, `data/history/schedule`; the proxy is not used). Usable = `as_of <= real tip - 60 min`.

**Coverage and cadence, by season (all 13 seasons in `games`; reports exist only from 2019):**

| season | games | any report | usable T-60 snapshot | snapshots/game (all) | usable/game (mean, median) | latest usable lead, min p10 / p50 / p90 |
|---|---|---|---|---|---|---|
| 2019 | 1143 | 1141 | 1141 | 4.71 | 3.92, 4 | 60 / 150 / 210 |
| 2020 | 1171 | 1171 | 1170 | 4.57 | 3.79, 4 | 60 / 150 / 180 |
| 2021 | 1323 | 1323 | 1323 | 24.9 | 20.5, 21 | 60 / 60 / 90 |
| 2022 | 1320 | 1318 | 1318 | 2.58 | 2.50, 3 | 120 / 180 / 300 |
| 2023 | 1318 | 1317 | 1317 | 2.55 | 2.48, 3 | 120 / 180 / 300 |
| 2024 | 1320 | 1317 | 1317 | 2.51 | 2.43, 3 | 120 / 180 / 300 |
| 2025 (counts of timing only) | 1321 | 1317 | 1315 | 2.55 | 2.44, 3 | 75 / 150 / 285 |

(All rows are my own run; the T-60 coverage matches the QUESTIONABLE_STREAK audit for 2019-2024, which counts pre-T-60 snapshots per game as 3.9 / 3.8 / 20.5 / 2.5 / 2.5 / 2.4. 2013-2018 have no reports and no real tips in these files.)

Reading: the T-60 snapshot exists for 99.7-100% of games 2019-2024 (1-3 games a season missing; 6 in 2025). But **the "T-60" snapshot is usually about T-180**: median lead 150-180 minutes in 2019-20 and 2022-24, 60 in 2021-22 (hourly reports, 20 snapshots a game), shorter and wider in 2025. The live 2026-27 pull is a true T-60, a third cadence. Anything fitted on 2022-24 sees a status that is about two hours staler than the live one: some questionable players have since been upgraded or ruled out. That is a train/serve skew in the direction of making the live tag **more** informative, and it is not measured.

Only flagged players are listed; absence from the latest snapshot means "not on the report", not "healthy". `latest_pretip_flagged` uses the latest snapshot only; I also ran "latest status per player over every usable snapshot" and the counts differ by < 1% (e.g. 2024 questionable 1,972 vs 1,973).

Other defects in the table: 167 rows with NULL `game_id` (0.14%, unresolved matchup; excluded by the loaders); history load had 4,466 unmatched names (1.24% of rows, mostly `not_in_pool`) before the 2026-10-11 repairs, the early-2019 re-load had 0 unmatched. Reasons in the Q/D/P snapshot rows: 2022-24 questionable rows are mostly "Injury/Illness" (about 5,000 of 5,780), plus about 640 "G League" assignments, 33 personal, 12 rest.

## 2. Player-games at T-60 (latest usable snapshot)

Counts of listed player-games, by status:

| season | questionable | doubtful | probable | (sum Q+D+P) | available | out |
|---|---|---|---|---|---|---|
| 2019 | 765 | 178 | 596 | 1,539 | 756 | 7,891 |
| 2020 | 991 | 172 | 486 | 1,649 | 460 | 6,775 |
| 2021 | 450 | 45 | 129 | 624 | 1,591 | 10,603 |
| 2022 | 1,763 | 213 | 678 | 2,654 | 647 | 8,371 |
| 2023 | 2,045 | 232 | 659 | 2,936 | 810 | 9,278 |
| 2024 | 1,972 | 262 | 777 | 3,011 | 826 | 10,261 |
| 2025 (count only) | 1,703 | 334 | 634 | 2,671 | 1,041 | 9,742 |

Per game this is about 1.5 questionable, 0.2 doubtful, 0.6 probable (2022-24). Of box-score player-rows, Q+D+P is about 7.5% of all rows and 6.7-8.0% of rotation rows (prior 10-game minutes average >= 15): 1,512 / 1,701 / 1,828 rotation rows in 2022 / 2023 / 2024. About 68-73% of listed Q/D/P players have a row in the box score at all (the rest were inactive or never dressed, so the box has no row for them; this is the "~2,000 rostered-unreported-undressed rows a season" wound in T484).

## 3. 2022-2024: how many played, and minutes against recent average

Method: listed at T-60 on the latest usable snapshot (as above). Outcome: minutes from `player_game_stats` (`played` = minutes > 0; a listed player with no box row counts as not played, minutes 0). Recent average = mean minutes of the player's previous 10 *played* games, strictly before game date, as-of (carries across seasons; needs >= 3 prior played games; rows with a prior average >= 5 minutes and last played game within 30 days). Comparison group: players with a box row who are not on the T-60 snapshot. n is player-games; games are the clustering unit.

| status (2022-24 pooled) | n | games | P(played) | mean minutes | mean recent avg | minutes / recent avg, **among those who played** p10 / p50 / p90 |
|---|---|---|---|---|---|---|
| questionable | 5,106 | 2,490 | **0.514** | 13.4 | 25.5 | 0.58 / 0.97 / 1.26 |
| doubtful | 622 | 512 | **0.024** | 0.6 | 25.0 | 0.40 / 0.83 / 1.24 (n played about 15) |
| probable | 1,993 | 1,228 | **0.928** | 26.1 | 28.0 | 0.68 / 1.00 / 1.26 |
| available | 1,854 | 1,131 | 0.852 | 20.6 | 22.9 | 0.50 / 0.98 / 1.35 |
| not on report (box row) | 84,461 | 3,915 | 0.866 | 20.1 | 21.6 | 0.53 / 1.00 / 1.46 |

By season, questionable P(played): 2022 0.506, 2023 0.542, 2024 0.494 (2019-21: 0.517, 0.536, 0.666; 2021-22 hourly era differs, as the prereg audit also found). Two cautions on the 0.51:

1. **It depends on who is in the denominator.** Restricting to rotation (recent avg >= 15) gives 0.557 for questionable, 0.95 for probable, 0.026 for doubtful. Restricting further to players who also score (recent points average >= 3) gives about 0.77 for questionable. A listed player with few minutes or points is often a G-League-assigned or fringe player who was never going to play, so the Q probability is bimodal across the roster. A first pass of mine that dropped listed players with no box row (no prior average attached) gave 0.76-0.80 and was biased upward by that exclusion; the table above attaches the as-of average from earlier box rows and keeps them. The 30-day gap filter and the 5-minute floor also matter; the figure is a range 0.5-0.8 by population, not a number.
2. Among questionable players who **do** play, minutes relative to the recent average are close to everyone else's (median 0.97 vs 1.00, p10 0.58 vs 0.53, p90 1.26 vs 1.46): the tag mostly moves **whether** a player plays, and a little how long. Probable is nearly the unconditional distribution.

Play rate also drifts with how stale the stale snapshot is: questionable rotation players with a gap of 0-3 days since their last game play 0.53, 4-10 days 0.58, 11-30 days 0.67, > 30 days 0.70 (n 2,590 / 1,341 / 314 / 152).

## 4. Do current features see them?

Answered in section 0. Production: no (OUT only; doubtful only inside injury-Elo for the winner). Shadow/offline: yes, P(play) R and the AVG sequence model read the tag. The minutes module (`nba/props/minutes.py`) and the stat quantile models receive P(play) from production's own hurdle, which is blind to the tag.

## 5. Sources we could add, repairable gaps, costs (non-negotiable 8 audit style)

Capture audit: every season we hold with reports = 2019, 2020, 2021 (history DB), 2022-2025 (production DB). Nothing earlier is held and no earlier report is verified to exist.

| item | what | cost | note |
|---|---|---|---|
| Games without a T-60 snapshot | 2, 1, 0, 2, 1, 3, 6 games in 2019-2025 | unknown cause; list from the SQL in section 1; probably no PDF in the window | immaterial (<= 0.5% of games) |
| 167 NULL `game_id` rows | unresolved matchup | previous pass resolved 21 of 188 (F2b); remainder need hand review; 0.14% of rows | cheap, tiny |
| Unmatched player names (history load, 4,466 = 1.24%) | `not_in_pool`/`no_candidate` | alias review table; no network; about a half day | the early-2019 pass reached 0 unmatched on its window |
| 2021-22 hourly cadence (20 snapshots/game, lead 60) | era-identifying; classifier AUC 0.80 | snapshot-time matching to production's pull times, about a day, no leak risk if matched on stamp only (prereg audit) | a cutoff harmonisation does not work (drops 2022-24 snapshots in half the games) |
| Pre-2019 reports | PDFs may or may not exist on the official site; **not verified** | one probe request tells; if present, about 1,600 PDF requests per season at 0.5/s, about 1 hour per season from Devin's shell (agent network blocked) | would add seasons but at an older, unknown format |
| Post-game inactive list (nba_api `BoxScoreSummaryV2`) | who did not dress; validation only, post-game | about 7,600 read-only requests for 2019-24 (QUESTIONABLE_STREAK audit); Devin's shell | answers "reported OUT vs actually inactive" and the never-dressed gap; cannot be a feature |
| Pro Sports Transactions | second source for missed absences | not fetchable within the rules: HTTP 403 Cloudflare, the client needs a bypass; Kaggle scrape needs an account (credential). **Skipped** | recorded in the QUESTIONABLE_STREAK audit |
| Beat-reporter feed (X API, built: `nba/ingest/reporters.py`) | pre-tip "GTD", "will play", minute restrictions, with time and source | needs Devin's pay-per-use token (a few dollars a month); forward only, **no history**; nothing in the daily path imports it | the one source that says how many minutes |
| Lineup T-30/T-15 collector (built) | whether the GTD resolved before T-30 (`open_decisions`) | live from 2026-27; no history | the pre-registered explanation for any backtest shortfall |
| Live T-60 pull | true T-60 status (third cadence) | exists; no extra cost | train/serve skew above is unmeasured until a season of it is logged |
| Third-party aggregators (Rotowire/ESPN etc.) | not evaluated here | unknown; terms of use unread | not recommended without Devin |

Whether the data capture is comprehensive enough to declare a gate failed on data: for this slice the answer is yes, the report is captured for 99.7%+ of games in all seven seasons, so any failure on counts would be a failure of the slice, not the capture. The one thing missing is a clean true-T-60 history, which cannot be bought; only the 2026-27 log produces it.

## 6. Rough power read (not a test)

Question: would a test on the affected rows plausibly reach a CRPS floor of -0.005? Units note: the brief's floor is not tied to a stat; I used points CRPS (the stat the house scores most) and minutes CRPS as a second read.

Design (descriptive, rough): fit the empirical distribution of outcome / recent average on 2019-2021 rows separately for each status and for not-on-report players, then score 2022-24 Q/D/P rows two ways: "blind" (all rows use the not-on-report ratio distribution) and "tag-aware" (each row uses its status's distribution). 99 equally spaced quantiles, empirical CRPS, 2,000 game-clustered bootstrap draws, seed 0. The blind arm is a **straw man**: it is not production, which already has recent-form and play-rate priors. Treat the deltas as the ceiling of what a tag can add over a model that ignores the tag completely.

| read | rows | blind CRPS | aware minus blind | 95% CI (game-clustered) |
|---|---|---|---|---|
| minutes, Q+D+P rows (prior avg >= 5) | 7,721 / 2,966 games | 8.65 | **-2.74** | [-2.91, -2.55] |
| minutes, questionable | 5,106 / 2,490 | 9.35 | -2.24 | [-2.45, -2.03] |
| minutes, doubtful | 622 / 512 | 15.28 | -14.74 | [-15.31, -14.07] |
| minutes, probable | 1,993 / 1,228 | 4.80 | -0.27 | [-0.30, -0.24] |
| points, Q+D+P rows (recent avg >= 3 pts, >= 5 min) | 5,327 rows | 4.68 | **-0.188** | [-0.240, -0.135] |
| points, questionable | 3,320 | 4.72 | +0.002 | [-0.068, +0.068] |
| points, doubtful | 138 | n/a | -4.95 | [-5.75, -3.99] |
| points, probable | 1,869 | n/a | -0.173 | [-0.189, -0.156] |
| points, all rows (82,710), affected share 6.4% | | 3.72 | -0.012 | [-0.016, -0.009] |

Reading:

- On the affected rows the ceiling is far larger than the floor in every cell except questionable-points. Doubtful carries it (almost nobody plays, so a straw-man model is badly wrong); probable is a modest, tight gain; **questionable points are about zero** because about half play, the minutes ratio among players is nearly unchanged, and a mixture of "zero" and "usual" is a hard shape for a quantile ensemble to beat the unconditional one at pts scale. That is the cell that matters most (it is 2/3 of the rows), and in the straw-man comparison it does not show a gain.
- **Resolution is the real constraint.** The half-width of the pooled points CI on affected rows is 0.053, about 10x the floor. At 80% power, 5% two-sided, the minimum detectable effect is about 0.075 CRPS pooled over three seasons (SE about 0.027) and about 0.13 for one season; for questionable alone about 0.10 pooled. A true effect of -0.005 to -0.03 on affected rows is **not resolvable** with these n; a "pass" at -0.005 on the slice would only be a pass because the true effect is large, and the diluted all-rows floor (-0.005) needs an affected-row effect of about 0.08 or more (affected share 6.4%).
- Per season, affected-row n for points is about 1,800 (rows with enough history; box rows only), so the report season alone cannot confirm anything smaller than about 0.13.
- The effect against production is unknown and probably smaller than the straw-man ceiling: production's minutes model already uses recent minutes, DNP streaks and rest; T484 found a learned model on production's six inputs recovers only part of the gain, and the tag is only one of the groups. T316/T482 show the P(play) tag effect on log loss is large (5x floor) and robust; whether it survives translation into stat CRPS through P(play) is exactly PPLAY_INTO_PROPS_LIVE.
- Unscored rows: listed players with no box row (about 27-32% of questionable) are never scored offline, and they are the ones the tag is most useful for (P(play) near 0); any CRPS test on rows with a box row selects on the outcome (post-T-60 box membership). Both T484 wounds apply here.

**Plausible answer:** a CRPS test on this slice would reach -0.005 easily **if** the effect against production were as large as the straw-man ceiling for doubtful/probable, would very likely not for questionable stat CRPS at the resolution available, and in any case is not independent of PPLAY_INTO_PROPS_LIVE, which is already frozen, running from opening week and will give about 2,600 affected player-games a season of clean forward data with a true T-60 tag.

## Recommendation (opinion, not a decision)

1. Do not draft a new historical test on Q/D/P now. The tag question for P(play) is answered (T316, T482, T484); the stat-CRPS question is the frozen live test; a historical slice test cannot resolve effects below about 0.1 CRPS on the affected rows, and its rows are selected on tonight's box (wounded the same way T482 was).
2. If Devin wants something before the live log, the cheapest informative step is **descriptive**: the unscored-row gap (listed Q/D with no box row, about 2,000 a season) and the stale-snapshot skew (T-180 history vs T-60 live), both measurable from data we hold; neither needs a rule.
3. First live look at the open-decision share (Q/D whose lineup is unconfirmed at T-30) will tell how much of the Q mass resolves after T-60; that decides whether any Q-specific modelling is worth a rule.
4. Not touched here: `nba.duckdb` was opened read-only only; no table, parquet or manifest was changed, so no changelog entry is warranted and `data_version` is unchanged.
