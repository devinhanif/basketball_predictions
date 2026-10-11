# F8 gate G3 recheck: possession OREB vs box-score OREB (2026-10-10)

Prerequisite for docs/prereg/F8_LINEUP_REBOUNDING.md (DRAFT, not edited). Nothing was written to
`nba.duckdb`: the `--flags-only` write gate failed (below). Every "after" number is the new parser's
output computed in memory from `data/pbp/*.parquet` (5,269 games, 1,047,292 possessions) and
reconciled read-only. Reproduce: `uv run python -m nba.parse.rebuild_possessions --db nba.duckdb
--flags-only` (summary: `reports/oreb_flag_rebuild/summary.json`).

Scope: seasons 2022-2024 only (`season <= 2024` in SQL, 7,906 team-games). The draft's "n 10,538
team-games" for the 0.80 correlation is 7,906 + 2,632, i.e. it also included the 2025 team-games; the
figures below drop them. 2025 is not read by any statistic here. Box OREB = `sum(player_game_stats.oreb)`
per team-game (player-credited; it excludes the feed's team rebounds).

## What was wrong
`oreb` was set on any `Rebound` event of the offensive team. Of 2,498 offense-side rebounds with no
player attached (`team_id` 0, "76ers Rebound") in a 211-game sample, 1,021 sit between two free throws
(a dead ball; the second free throw is the same trip) and 1,477 follow a missed field goal or last free
throw. None is in the box score. Player-credited offensive rebounds (the feed's `(Off:n Def:m)` counter)
sum to box OREB exactly in that sample (4,571 vs 4,571), and the parser already classified 4,542 of them
as offensive (27 more were offense-team rebounds whose counter was a defensive one; 8 offensive ones met
a trip owned by the other team). So the event is in the feed and the old flag over-counted by adding
team rebounds. It was never "True on a made shot with no rebound": a trip that opens with a miss and an
offensive rebound and ends in a putback or a turnover legitimately carries `oreb` with that outcome.

Real stored rows (game 0022200001, pre-fix; three wrong, one kept):
* poss 36, Q1 3:39, FT_trip, oreb True: Harris misses free throw 1 of 2 (action 124), "76ers Rebound"
  team_id 0 (125), free throw 2 of 2. Nothing was extended. Now False.
* poss 138, Q3 2:01, TOV, oreb True: Harden misses a three (460), "76ers Rebound" team_id 0 (461),
  Harrell offensive charge (465) and a turnover. No player rebound. Now False.
* poss 152, Q4 11:00, FGM2, oreb True: Niang misses a three (521), "76ers Rebound" team_id 0 (522),
  loose-ball foul, Maxey layup (525). No player rebound. Now False.
* kept True: poss 0, Q1 11:38, TOV: Embiid misses (7), Tucker REBOUND (Off:1 Def:0) (9), Tucker turnover (10).

## G3 numbers (draft rule: ratio within 0.95-1.05 AND team-game correlation >= 0.95)
Team-game sums of the possession-derived OREB measure vs box OREB.

| Season | team-games | box OREB | old flag | ratio | corr | MAD | new flag (trips) | ratio | corr | MAD | new `n_oreb` (rebounds) | ratio | corr | MAD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022 | 2,640 | 27,534 | 37,137 | 1.349 | 0.799 | 3.77 | 24,338 | 0.884 | 0.952 | 1.21 | 27,524 | 0.9996 | 0.9999 | 0.004 |
| 2023 | 2,636 | 27,749 | 36,852 | 1.328 | 0.788 | 3.63 | 24,433 | 0.881 | 0.948 | 1.26 | 27,741 | 0.9997 | 0.9999 | 0.004 |
| 2024 | 2,630 | 29,223 | 38,452 | 1.316 | 0.781 | 3.69 | 25,587 | 0.876 | 0.946 | 1.38 | 29,219 | 0.9999 | 1.0000 | 0.002 |
| all | 7,906 | 84,506 | 112,441 | 1.331 | 0.790 | 3.70 | 74,358 | 0.880 | 0.949 | 1.28 | 84,484 | 0.9997 | 0.9999 | 0.003 |

(The draft's 37,375 / 37,173 / 38,698 were measured before the possession-order rebuild; the stored
table gives the "old flag" column.)

Verdict: **G3 with the boolean `oreb` as stored (any column named `oreb`) still FAILS**, but for a
different reason than before. The corrected flag marks "this trip was extended by an offensive
rebound", and a trip with two offensive rebounds has one flag: 84,484 rebounds sit in 74,358 flagged
trips. The ratio is therefore capped near 0.88 by construction and the correlation (0.946-0.952) sits
on the 0.95 line. **G3 PASSES with the rebound count** (`n_oreb`, the number of player-credited
offensive rebounds in the trip): ratio 0.9996-0.9999, correlation 0.9999-1.0000, 99.97% of box OREB.

Mix of `oreb` True after the fix, by trip outcome (2022-24): FGA_miss 8.6%, FGM2 12.9%, FGM3 7.5%,
FT_trip 8.9%, TOV 6.7% (before: 12.8 / 14.6 / 9.3 / 31.6 / 10.3). The share of FGM/TOV trips with
`oreb` True is 10.1% (before 12.3%); these are the putback-then-score and rebound-then-turnover trips,
not an error, and `oreb` is True iff `n_oreb >= 1` (test `test_flag_is_exactly_n_oreb_positive`).
The big drop is FT_trip (24,234 -> 6,829).

Structure: nothing but the flag differs. 1,047,292 rows before and after, 0 key mismatches, 0 changed
cells in period, clock_start, clock_end, off_team, def_team, score_diff, outcome, shooter_id,
shot_zone, assister_id, fta, pts, and 0 changed lineups. 0 flags added, 50,999 removed (all seasons;
38,083 of them in 2022-24). poss_idx numbering, possession count, points and FTA did not move.

## Write gate (fixed before the full run) and its outcome
Per season |flag / box - 1| <= 0.10 and correlation >= 0.95 (each season and overall), structure
unchanged, no flag added. Failed: ratio 0.884 / 0.881 / 0.876 (2022/23/24), correlation 0.948 (2023),
0.946 (2024), 0.949 (overall). Passed: structure, subset, all games parsed (5,269 of 5,269),
correlation 0.952 (2022). **No write; stored `possessions.oreb` is unchanged.** The gate was not
edited after seeing the result.

## What G3 means for F8 (draft untouched)
* The draft's consequence ("group C is DROPPED unless a tracker fix makes G3 pass before freeze") is
  met only by a count: group C needs on-court OREB counts, and `n_oreb` reconciles. A boolean `oreb`
  would still drop it. The DREB side of `lr_dreb_pct` is not covered here (`possessions` has no DREB
  flag or count; a defensive rebound is the event that closes a trip, so a count would be a second
  new column).
* Re-stating G3 so that a trip flag passes would be a rule change made after seeing the numbers; this
  note does not do that.
