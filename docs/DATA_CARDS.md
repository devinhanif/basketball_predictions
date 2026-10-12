# Data cards (notes from the 2026-10-11 audit, docs/reviews/data_audit_2026-10-11.md)

One card per table that needs a reader's warning. Definition, source, as-of window, imputation. Numbers are the audit's.

## player_game_hustle (F9)
- Definition: post-game hustle counts per (game_id, player_id). Source: NBA hustle stats endpoint. 79,263 rows, seasons 2022-24 only.
- As-of: POST-GAME. Known only after the game; never a feature for the same game. Only strictly prior games are usable.
- **Row existence depends on tonight's minutes**: a row exists for 14% of players under 1 minute, 32% at 1-2, 65% at 2-5, 88% at 5-10,
  98% at 10-20 and 99.8% at 20+. A LEFT JOIN from the box makes NULLs that are NULL iff minutes are low (the opp_adjusted_ridge shape).
- Imputation: none. "No row" means unknown, never zero. Join through `nba.data.side_tables.join_side_table`, which keeps values NULL and
  adds an explicit presence flag (tests/data/test_side_tables.py). Consumer: `research/features/tracking_features.py` (as-of means).
- `player_game_tracking` has a row for every box row (DNPs included, zeros); the same rule applies to its values for the current game.

## player_game_stats DNP rows
`minutes` NULL, all stats 0 (18.6% of rows). 18 rows list 0.0 minutes. Exclude from labels and averages; model P(play) separately.

## games.national_tv (F14)
NULL for 77.5% of regular-season games (about 952 per season). NULL means "not nationally televised" and "unknown" at once. The only
consumer (`nba/features/game_context.py`) treats NULL as False, which is the intended reading. If a consumer ever needs to tell the two
apart, the source (data/national-tv) must be re-read per game; do not infer from NULL.

## Empty tables with no writer in the daily path (F13)
`game_era_flags` (now materialised, 5,279 rows), `kalshi_markets`, `kalshi_prices`, `paper_trades`, `player_game_matchups`,
`player_rates`, `shots`, `team_context`, `team_game_advanced`, `forward_scores`, `forward_scores_elig`. Left in place on purpose (no drops).
`forward_predictions` (136 rows) are all preseason ids (001...), which `games` excludes, so they can never be scored.

## team_coaches (F5, open)
The NBA endpoint returns the staff as recorded after the following offseason: 14 of 87 reference team-seasons name the wrong head coach,
11 of them the next season's hire. Do not use as a known-at fact. `team_coach_games` (reviewed reference, 2022-24 verified, 2025
unverified with NULL `known_at`) is the replacement; Devin is reviewing the coaches.

## player_availability.game_id
167 rows NULL after the clean-up (156 rescheduled beyond 7 days, 7 ambiguous, 4 NBA Cup final 006 not kept in `games`). NULL = unlinked, not
"no game"; the injury model cannot see them.

## lineup_snapshots (lineups.duckdb)
Raw, append-only; the feed lists a player twice in one snapshot (starters block + confirmed roster block). Read through
`nba.lineups.store.dedup_lineup_snapshots(con)` or the view `lineup_snapshots_dedup`: Confirmed over Expected, then latest `source_ts`;
`announced_starter` is the OR over duplicates.
