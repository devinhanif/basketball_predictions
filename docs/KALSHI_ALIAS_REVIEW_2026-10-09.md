# Kalshi alias review, 2026-10-09 (prep for opening night 2026-10-20)

Read-only and offline. Inputs were `data/kalshi/kalshi.duckdb` and `nba.duckdb` (both opened
`read_only`) plus nba_api's bundled static player list. No stats.nba.com call and no Kalshi call.
`configs/kalshi_aliases.yaml` was not touched.

## Counts (n = 20 distinct player names, 1,514 markets in the DB, 224 are prop markets)

| Status under the reviewed table | Names |
|---|---|
| matched | 20 |
| unmatched | 0 |
| ambiguous | 0 |
| unmatched with a `high` proposal | 0 |

- All 20 names resolve: the 18 reviewed YAML rows plus built-ins (LeBron James, Jayson Tatum).
- No prop market is open today (`n_open` = 0 for every name). All prop history is Finals-era
  and the 2026-10-06 preseason games.
- Some stored `kalshi_markets.player_id` values are still NULL (e.g. 24 of 124 `KXNBAPTS` rows
  carry an id). Those rows were ingested before the aliases were added. The `unmatched_names.json`
  entries are the same 18 names. A re-resolve of stored rows is a separate step and was not done here
  (the file is written by the snapshot job, so this run does not write it).
- Only the prop-series names are counted. Game, spread and total markets name teams, not players.

Honest read: the unmatched queue is empty today because the only markets seen so far name players
already reviewed. The real test comes on 2026-10-20, so the file also pre-stages the roster.

## Review file

`configs/kalshi_aliases_candidates_2026-10-09.yaml` (review only, never loaded by code):

- `resolution`: every Kalshi prop name, status, method, market counts.
- `unmatched`: per unmatched or ambiguous name, ranked proposals with `player_id`, `matched_name`,
  `team` (team of the player's latest game in the latest season in the DB), `method`, `confidence`, `why`.
  Empty now.
- `preseed_roster`: 497 active players with latest-season games who are not yet in the reviewed table.
  488 are `high`, 9 are `low`. The `low` ones are all suffix collisions (see below).
- `rookies_not_in_players_static`: active in the static list, no box-score rows, no `players_static` row.

Confidence rules: `high` needs exact full name (case-insensitive), a single player with that normalized
name in the static list, and box-score rows in the latest season. Accent or suffix differences, or no
latest-season games, give `medium`. Duplicates or fuzzy matches give `low`.
"Current team" is a proxy: the team of the latest game in the DB (2026 Finals). The DB cannot see
offseason trades, so a `high` row still needs a glance at the team before opening night.

## Findings that need a human

1. **Suffix collisions.** `normalize_name` strips `Jr.`/`II`, so these pairs share one key: Jaren Jackson Jr.,
   Gary Trent Jr., Jabari Smith Jr., Kevin Porter Jr., Larry Nance Jr., Tim Hardaway Jr., Ron Harper Jr.,
   Gary Payton II, and Brandon Williams. Each has two ids in the static list (father and son, or namesakes).
   Do not promote these blindly. The alias key is the normalized name, so one id will be chosen for both
   forms. Pick the active player's id and confirm.
2. **Rookies.** Four active static-list players have no NBA games and no `players_static` row:
   Alex Toohey (1642893), Eli John Ndiaye (1642947), Nikola Djurisic (1642365), Thomas Sorber (1642850).
   None is on Kalshi yet. The 2026 draft class is not in the bundled static list at all (its newest ids
   are 1643xxx two-way and undrafted signings), so 2026 rookies named on Kalshi will show as
   `unmatched` with no proposal. Resolve them after the throttled backfill refreshes player data.
   Until then a rookie prop cannot be priced: there is no `player_rates` history and no `players_static` row.
3. **Preseason scalar results.** `result = scalar` on LeBron James preseason markets, see
   `docs/KALSHI_LIVE_2026-10-08.md`. Unchanged.

## How to review and promote

1. Regenerate right before the first slate (after a snapshot has run):
   `.venv/bin/python -m nba.kalshi.alias_review --out configs/kalshi_aliases_candidates_2026-10-20.yaml`
2. Open the `unmatched` section. For each name check `matched_name`, `team`, and `why`.
3. Copy confirmed rows by hand into `configs/kalshi_aliases.yaml` as `"Kalshi Display Name": player_id`.
4. For a bulk start, `preseed_roster` rows with `confidence: high` are reasonable to promote after a spot
   check. Keys are normalized, so `Nikola Jokic` and `Nikola Jokić` are the same alias.
5. Re-run the snapshot. Stored rows with a NULL `player_id` are fixed only if the ingest re-resolves them.
6. Never promote a `low` row without checking the id against the player's team page.

The existing `python -m nba.kalshi alias-candidates` still works. It reads `unmatched_names.json`
(18 names, now all reviewed) and gives exact-name hits only, with no team or confidence.

Tests: `tests/kalshi/test_alias_review.py`.
