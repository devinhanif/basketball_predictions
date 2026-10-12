# Preseason rehearsal 2026-10-12 (one-off, scratch stores only)

Generated 2026-10-12 from `data/rehearsal/preseason_2026-10-12/`. Descriptive only: preseason rotations are not regular-season rotations, so nothing below says anything about model skill. No stakes, no EV, no recommendation. Nothing was written to production tables or logs.

## 1. Games found

`GET /sports/basketball_nba/events` (free) over now-14h..now+48h returned 0 events: the preseason is NOT under `basketball_nba`. The vendor lists a separate sport key `basketball_nba_preseason` (active); its free `/events` call returned 4 games, all matched by tip time and both team ids to the NBA schedule (`ScheduleLeagueV2`, the `001` game ids):

| game_id | matchup (away @ home) | tip (UTC) | tip (CDT) | vendor event |
|---|---|---|---|---|
| 0012600007 | Oklahoma City Thunder @ Atlanta Hawks | 23:00 | 18:00 | `67a4008e` |
| 0012600050 | Brooklyn Nets @ Washington Wizards | 23:00 | 18:00 | `a4d55f0b` |
| 0012600043 | Minnesota Timberwolves @ New York Knicks | 23:30 | 18:30 | `6a3e301a` |
| 0012600044 | San Antonio Spurs @ Utah Jazz | 01:00 (10-13) | 20:00 | `1343506e` |

Also on the NBA schedule today: 0012600042 Trail Blazers vs Lions (international opponent, 20:00 UTC): not listed by the vendor, excluded by the team-id range. The daily path's `ScheduleLeagueV2` filter (`KEPT_GAME_TYPE_PREFIXES`) drops all `001` games before `slate_for_date`, so the rehearsal supplied the four games from an unfiltered fetch of the same endpoint.

## 2. Credits

| call | cost (`x-requests-last`) | note |
|---|---|---|
| `/sports?all=true` | 0 | free |
| `/sports/basketball_nba/events` | 0 | free, 0 events |
| `/sports/basketball_nba_preseason/events` | 0 | free, 4 events |
| event odds, 4 games, 89 markets x [us, eu] | 22 each = 88 | vendor charges markets RETURNED x regions: 11 markets x 2 regions = 22, not the 89 x 2 = 178 upper bound |
| event odds, game 0012600007 first attempt | 22 | wasted: a snapshot-kind validation error (rehearsal kind name) stopped the script after a paid call; raw kept in `raw_first_call/`, game re-pulled |

Total 110 credits (5 paid calls) against a 5 x 178 = 890 ceiling. Server remaining before 2,297,047; after 2,296,937 at the last paid call (from the ledger). Regular-season estimate of 178 per call is an upper bound; the real figure depends on which markets books post.

## 3. Parser results (first real payload)

1,440 outcomes returned, 1,440 rows parsed, 1440 stored (sum over games), 0 parse reasons (no `unparsed_outcome`, `bad_outcome`, `unresolved_player`, `duplicate_row_dropped`). 388/388 player-prop rows resolved to a `player_id` through the alias tables.

Markets returned (11 of 89 requested): `h2h`, `spreads`, `totals`, `alternate_spreads`, `alternate_totals`, `team_totals`, `h2h_lay` (not requested; Betfair adds it), `player_points_alternate`, `player_rebounds_alternate`, `player_assists_alternate`, `player_threes_alternate`.

| book | markets returned |
|---|---|
| betfair_ex_eu | h2h, h2h_lay |
| betonlineag | h2h, spreads, totals |
| betrivers | h2h, spreads, totals |
| betsson | h2h, spreads, totals |
| betus | h2h, spreads, totals |
| bovada | h2h, spreads, totals |
| draftkings | alternate_spreads, alternate_totals, h2h, player_assists_alternate, player_points_alternate, player_rebounds_alternate, player_threes_alternate, spreads, team_totals, totals |
| fanduel | h2h, spreads, totals |
| leovegas_se | h2h, spreads |
| mybookieag | h2h, spreads, totals |
| pinnacle | alternate_spreads, alternate_totals, h2h, spreads, totals |
| unibet_se | h2h, spreads |
| williamhill_us | h2h, spreads, totals |

Shapes exercised by a real payload: h2h, spreads, totals, alternates (spreads, totals), team totals (`Over`/`Under` with the team in `description`; stored as `outcome_name='Over (Team)'`, `player_name` NULL, both teams kept), one-sided player milestone ladders (`Over` only, `implied_prob` NULL because there is no pair to de-vig; `implied_prob_raw` kept).

Shapes NOT exercised (the vendor returned none in preseason, so the parser remains unverified on a real payload): period lines q1-q4/h1/h2, `h2h_3_way*`, yes/no player props (double/triple double), first basket / first team basket / method of first basket, `player_*_q1`, the extra full-game player lines (blocks, steals, turnovers, combos, FG, FT), alternate team totals, period alternates. Try again on 2026-10-20 (regular season).

Parser finding (fixed, with a test): Betfair's undocumented `h2h_lay` market was de-vigged as if it were a back market. A lay price is not the back price, so the pair now keeps `implied_prob` NULL (`_devig` skips `*_lay`; test `test_exchange_lay_market_is_kept_but_not_devigged`). Rows are still stored under their own market name, so `h2h` queries are unaffected. Other observations, not changed: `alternate_spreads` has `implied_prob` for only 58 of 180 rows (de-vig needs both sides at the same |line| from one book; DraftKings posts uneven ladders); player milestone and `h2h_lay` rows have no de-vigged probability by design.

## 4. Pipeline run (copy of `nba.duckdb`, rehearsal mode)

Run: `data/rehearsal/preseason_2026-10-12/run_rehearsal.py` (scratch), `run_daily` on `nba_copy.duckdb`, date 2026-10-12, made_at 2026-10-12 16:00 UTC (11:00 CDT, 7 h before the first tip), `--roster-source official --log-int-variant --log-lower-tail-variant --log-pplay-shadow --log-full-shadow` equivalents, ingest skipped, every cache/dir scratch (rosters, models, registry, injury data_dir). The scorable-game filter is bypassed in-process only (`nba.daily.pipeline.slate_for_date` replaced inside the script); nothing in `nba/daily` changed, so the replay oracle was not re-run (nothing to compare). Wall time 10 min 53 s (most of it injury-report probing).

Output: 4 games predicted, 1,932 rows to the COPY (4 game-winner rows, 692 props rows, 408 int-variant rows, 136 lt rows, 692 recency rows), P(play) R shadow 179 rows (142 with R), FULL shadow 568 rows. `nba.duckdb` mtime unchanged (10:50); production has no new `001` rows.

## 5. Game winner: model vs de-vigged moneyline

Model: `rung0_mov_elo` v1 (the injury-Elo fell back to it: no injury report was published, see section 7). Market: proportional de-vig of each book's home price.

| game | model P(home) | DraftKings (-> home / away price) | Pinnacle | 13-book mean | book range | model - mean |
|---|---|---|---|---|---|---|
| Thunder @ Hawks | 0.368 | 0.734 (-325 / +260) | 0.743 | 0.730 | 0.711-0.743 | -0.363 |
| Nets @ Wizards | 0.508 | 0.543 (-130 / +110) | 0.537 | 0.553 | 0.537-0.568 | -0.045 |
| Timberwolves @ Knicks | 0.703 | 0.511 (-115 / -105) | 0.532 | 0.514 | 0.497-0.533 | +0.189 |
| Spurs @ Jazz | 0.185 | 0.428 (+124 / -148) | 0.415 | 0.423 | 0.415-0.432 | -0.238 |

n = 4 games; mean |model - market| = 0.209. Three of four differ from the 13-book mean by 0.19-0.36 (Hawks vs Thunder: market 0.73 on the home side, model 0.37; Knicks vs Timberwolves: model 0.70, market 0.51). That is what an Elo built on regular-season results does with preseason exhibition lineups it cannot see, not a finding. n = 4 supports no claim and no interval.

## 6. Props: model P(stat >= k) vs DraftKings milestone ladders

There are no 'main' player lines in preseason: DraftKings posts only alternate (milestone) ladders for points, rebounds, assists and threes; `Over x.5` is compared with P(stat >= x+1). Model probability = P(play) x P(Y >= k | plays) from the stored 19-point quantile grid (the stored `p_ge` values were reproduced: 0 of 3,114 differ by more than 0.06). Market probability is the raw vigged `1/odds` (a one-sided ladder cannot be de-vigged), so it is biased upward by the vig; this is part of, but not all of, the gap below.

| arm | n lines | mean(model - market) | mean abs diff | correlation with market |
|---|---|---|---|---|
| production props_context_residual | 388 | -0.134 | 0.157 | 0.82 |
| P(play) R (p_play_r x production heads) | 388 | -0.129 | 0.156 | 0.81 |
| RECENT_ROLE_FULL (q_grid_full x production p_play) | 388 | -0.126 | 0.157 | 0.81 |

Lines are clustered in 4 games and about 100 players, so these n are lines, not independent events; no CI is claimed.

By stat (production arm): pts n=116 mean diff -0.132; reb n=112 mean diff -0.102; ast n=91 mean diff -0.168; fg3m n=69 mean diff -0.145.

By market probability bin (production arm): [0.0,0.2) n=129 diff -0.037; [0.2,0.4) n=69 diff -0.111; [0.4,0.6) n=60 diff -0.189; [0.6,0.8) n=52 diff -0.232; [0.8,1.0) n=78 diff -0.208.

Reading: the model is lower than the market on every stat, most in the middle of each ladder (-0.19 to -0.23). The market prices who actually plays and for how long tonight; the model has no injury report (none published), no confirmed lineups at 11:00, and offseason-stale features (`features_as_of_before` 2026-10-12, training through 2026-06-13). This is the 'later information is the lever' finding in miniature, and it is not evidence about regular-season skill.

Per-game points table (players with projected minutes >= 24; the DraftKings milestone nearest 50%). Full 388-line comparison: `reports/preseason_rehearsal_2026-10-12_props.csv`.

**Oklahoma City Thunder @ Atlanta Hawks**

| player | line (>=) | DK price | DK implied | model prod | R | FULL | P(play) prod / R | proj min |
|---|---|---|---|---|---|---|---|---|
| Jalen Johnson | 18 | -122 | 0.55 | 0.39 | 0.46 | 0.39 | 0.83 / 0.97 | 35 |
| C.J. McCollum | 15 | -108 | 0.52 | 0.59 | 0.61 | 0.64 | 0.95 / 0.98 | 31 |

**Brooklyn Nets @ Washington Wizards**

| player | line (>=) | DK price | DK implied | model prod | R | FULL | P(play) prod / R | proj min |
|---|---|---|---|---|---|---|---|---|
| Trae Young | 15 | +111 | 0.47 | 0.58 | 0.54 | 0.59 | 0.86 / 0.80 | 34 |
| Egor Demin | 15 | +131 | 0.43 | 0.14 | 0.14 | 0.17 | 0.94 / 0.97 | 25 |
| Noah Clowney | 12 | -114 | 0.53 | 0.27 | 0.29 | 0.28 | 0.86 / 0.93 | 24 |

**Minnesota Timberwolves @ New York Knicks**

| player | line (>=) | DK price | DK implied | model prod | R | FULL | P(play) prod / R | proj min |
|---|---|---|---|---|---|---|---|---|
| Mikal Bridges | 15 | +117 | 0.46 | 0.38 | 0.38 | 0.37 | 0.99 / 0.99 | 34 |
| Karl-Anthony Towns | 18 | -120 | 0.55 | 0.39 | 0.39 | 0.41 | 0.97 / 0.98 | 32 |

**San Antonio Spurs @ Utah Jazz**

| player | line (>=) | DK price | DK implied | model prod | R | FULL | P(play) prod / R | proj min |
|---|---|---|---|---|---|---|---|---|
| Keyonte George | 18 | -120 | 0.55 | 0.46 | 0.47 | 0.45 | 0.94 / 0.96 | 32 |
| Victor Wembanyama | 20 | -126 | 0.56 | 0.67 | 0.67 | 0.75 | 0.99 / 0.99 | 31 |
| Stephon Castle | 15 | -131 | 0.57 | 0.51 | 0.51 | 0.57 | 0.99 / 0.99 | 29 |
| Ace Bailey | 15 | +108 | 0.48 | 0.38 | 0.38 | 0.41 | 0.97 / 0.98 | 27 |

## 7. Errors and oddities seen on real preseason data

- Injury report: `no_report_found` (the pipeline probed about 9 min); the injury-Elo therefore fell back to `rung0_mov_elo` for all 4 games and `n_games_with_report` is 0 in every props row. Whether the NBA publishes official injury reports for preseason games at all is unknown from this run.
- `players_static` autofill: 113 of 115 debutant/rostered players pulled; 2 failed with `KeyError: 'resultSet'` (player ids 1643368, 1643635), so they have no rookie draft-slot prior. Official rosters: 30/30 teams, largest list 21 (preseason rosters carry camp/two-way players; floor 18). 148 of 692 production prop rows used the recency fallback.
- The pipeline's own schedule path cannot see these games (`KEPT_GAME_TYPE_PREFIXES` excludes `001` in `parse_schedule_frame`), so enabling preseason in production would need both that filter and `slate_for_date`; this run needed neither changed.
- `lineups.duckdb` was write-locked by the 5-minute lineups job during the rehearsal (a scratch reader was refused once); read-only opens retried by hand. Not a defect.
- `data/ops/ALERTS.md` shows `[pretip] predict failed (rc=5)` at 10:02 today and stale-watchdog lines at 10:02; these predate this rehearsal (it started 10:57) and are not caused by it.
- The 136 `props_context_residual_t30` rows for preseason game 0012600038 in production `forward_predictions` (2026-10-10) are the known pre-skip rehearsal rows (DECISIONS 2026-10-11 21:28); no new preseason rows were added.

## 8. Files

Scratch (ignored/untracked): `data/rehearsal/preseason_2026-10-12/` (`odds.duckdb`, `raw/`, `raw_first_call/`, `events_*_raw.json`, `preseason_schedule.json`, `nba_copy.duckdb`, `run_rehearsal.py`, `pull_summary.json`, `analysis.json`, logs). Tracked: `nba/odds/the_odds_api.py` and `tests/odds/test_period_markets.py` (lay-market fix), this report, the props CSV.

## 9. Honest no-edge note

Four preseason games, one book offering player milestones, no injury report, no lineups: this was a plumbing rehearsal and it passed (every returned payload parsed, forecasts and both shadows ran end to end on a copy, production untouched). It carries no information about whether the model beats the price. Keep your money.
