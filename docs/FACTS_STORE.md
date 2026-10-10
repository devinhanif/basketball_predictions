# Fact store (`nba/facts/`, built 2026-10-10)

## What it is
Two DuckDB tables, `entities` and `facts`, in `data/facts/facts.duckdb`, derived from tables we already have. Every fact
says when it became knowable (`known_at`, naive UTC). One query, `nba.facts.query.as_of(con, game_id, t)`, returns everything
knowable about a game at time `t`: the game, its two teams, and the players tied to it by a fact known by `t`. Design note:
`docs/research/knowledge_graph_2026-10-10.md`.

## What it is not
Not a model, not a graph database, not a feature table, and it has no pre-registration. Nothing a model produces is written
into it. It does not change any existing reader; the existing tables stay the source of truth.

## Conventions
- Entity ids: `player:<nba_id>`, `team:<nba_id>`, `game:<game_id>`, `coach:<id>`, `official:<id>`.
- **Game-scoped facts put the game in `object`** (`player:1 -listed_status-> game:002...`, payload in `value_text`/`value`);
  this is what lets `as_of` keep one game's facts out of another's. (Deviation from the brief, which had `object` = status.)
- `tips_at` carries the tip in `valid_from` (naive UTC) and `value_text`; the latest-known `tips_at` is "the tip".
- Holdout: the builder reads `games.season <= 2024` only (`--max-season`); season 2025 is never loaded.
- `as_of` raises `PostTipError` if `t` is after the tip (or the tip is unknown) unless `allow_post_tip=True`.

## Predicates and known_at rules
| predicate (subject -> object / payload) | source | known_at (choice) |
|---|---|---|
| `tips_at` game -> tip in `valid_from` | schedule parquet (`data/schedule`) | 00:00 ET the day before the tip (schedule-publish PROXY) |
| `tips_at` (second source) | lineups `game_tips` | `recorded_at` |
| `home_team`, `away_team` game -> team | `games`; lineup feed | 00:00 ET the day before game_date; first snapshot fetch |
| `listed_status` player -> game, status in `value_text` | `player_availability`, source `nba_official_report` (rows read by `nba.features.injury_report.load_report_rows`) | the report `as_of` (ET clock converted to UTC). Post-tip rows are kept; `as_of` gates them |
| `announced_starter` player -> game, 1/0 in `value`, Expected/Confirmed in `value_text` | lineups `lineup_snapshots` | snapshot `fetched_at` (every snapshot is a fact) |
| `position`, `height_in`, `first_season` player | `players_static` | 1 Oct of `first_season`. Players with no `first_season` (208 of 891) get no static facts |
| `head_coach` team -> coach, season in `value` | `team_coaches` (`coach_type = 'Head Coach'`) | 1 Oct of the season; confidence 0.5 because the table is wrong in 14 of 87 team-seasons (NEXT_SESSION loose end) |
| `played_minutes` player -> game, minutes in `value` (DNP: NULL and `value_text` = DNP) | `player_game_stats` | real tip + 2h30 (game-end PROXY). Games with no real tip get none |

Entity names come from `configs/kalshi_aliases.yaml` (reviewed; preferred) and `configs/odds_player_aliases.yaml` (generated,
unreviewed), with all aliases in `source_ids`; coach names from `team_coaches`.

## Rebuild
`python -m nba.facts build --source nba.duckdb --out data/facts/facts.duckdb [--since YYYY-MM-DD]`. Drops and recreates the out
DB each run (seconds). Opens `nba.duckdb` and `data/lineups/lineups.duckdb` read-only; exits 2 if either is locked. Prints
counts per predicate and per source. Rebuild after any load.

## Readers planned (none exist yet)
1. The explainer: one `as_of` call per game instead of five joins.
2. The pretip run: `known_before_tip(con, game_id, 60)` and `(..., 30)` as the information set it may read.
3. The reporter feed: tweets and reports enter as *claimed* edges (own `source`, `confidence < 1`), shown in the briefing and
   never a feature until a frozen rule says how they are used.

## Readers (status)
- Explainer: DONE 2026-10-10. `python -m nba.explain game … --facts-db data/facts/facts.duckdb` adds "What was known, and when"
  (T-60 and T-30 report listings with publish times, announced starters with snapshot times, the tip, and a count of post-tip
  facts withheld). Missing store or unknown game → one sentence, page unchanged. Use `data/rehearsal/replay2025.duckdb` as `--db`
  for 2025-26 pages (nba.duckdb holds no forecasts for them).
- Pretip job: not yet (additive read; needs a 20-date replay oracle before it touches the live path).
- Reporter feed: not yet (writes *claimed* edges with provenance; gated on the Bluesky probe and Devin's allow-list).
