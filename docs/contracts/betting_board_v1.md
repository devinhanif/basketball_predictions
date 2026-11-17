# betting board v1: stored quote snapshots side by side (app/api, 2026-11-17)

Three GET routes of the betting module. They exist only when the API's betting switch is on (`PH_BETTING=1`, read once
by `create_app`; see `docs/handoff_2026-11-17/lane_jarvis_flag.md`). With the switch off every path below is the
ordinary typed 404 `not_found`, and no other response changes. Response models: `app/api/src/parlayhitters_api/betting/models.py`;
selection rule: `betting/board.py`; fixed words and lists: `betting/catalog.py`; routes: `betting/routes.py`.

Read-only and offline: the quotes are the rows the nightly export already stores in `prop_lines`, as the API store
already loads them (each shown prop's own quote run: the `lines_run_id` of its primary forecast row, else its run id).
No outbound request, no credential, no write route, no server-side user state.

**What these routes never serve:** a probability other than the stored `implied_prob_raw`, an edge, an expected value,
a fair or de-vigged price, a "best price", any ranking of quotes by price, an arbitrage flag, or a model number.
`model_ref` names the existing routes that serve the model's numbers; their display locks (LINE_CAL_LIVE, the
teammate-sits rules, the finished-game locks) apply there, so the board cannot break one. Quotes are sorted by line,
then provider id, then side (over before under); nothing is sorted by price.

## The selection rule (one quote per line, provider and side)

1. A stored quote is eligible only if it can be shown to be from before tip-off: it has a snapshot time; its stored
   `minutes_before_tip`, when present, is above zero; its snapshot is earlier than the game's tip-off, when the
   tip-off is known; and at least one of those two facts exists.
2. Of the eligible quotes for one (line, provider, side), the newest snapshot is served. On an equal snapshot (to the
   second) the main market's quote comes before an alternate ladder's, a usable one before a flagged one, then the
   export's own row sequence decides.
3. Older snapshots are not returned in v1. Quote runs other than the shown prop's are not read.

## `GET /api/betting/providers`

```
{ "as_of": str,                 // manifest made_at (fixture replay: its built_at)
  "data_version": str,
  "providers": [ { "id": str,   // the export's book value as stored
                   "label": str, "kind": "sportsbook" | "exchange", "price_unit": "american",
                   "newest_snapshot_utc": str | null, "n_quotes": int, "fee": null, "settlement_rule_id": null } ],
  "unknown": ["fee", "settlement_rule_id", "liquidity", "live_status"],
  "note": "Stored snapshots from the nightly export. Delayed, not live. Analysis only; this service is read-only." }
```

* `providers`: every provider with at least one served quote anywhere in the loaded export, sorted by `id`.
  `n_quotes` is the number of quotes the boards serve for it (after the rule above), and the sum over every board is
  exactly this count (tested). `newest_snapshot_utc` is the newest snapshot among them.
* `label`: a small static map (`catalog.LABELS`; `williamhill_us` is Caesars per
  `docs/research/lines_everywhere_2026-10-14.md`), else the id itself.
* `kind`: `exchange` for the five ids the odds config documents as the vendor's exchange region
  (`configs/odds_live.yaml` line 4: "us_ex = Kalshi, Polymarket, Novig, ProphetX, BetOpenly"), `sportsbook` for every
  other id. A static list; nothing is inferred from prices.
* `fee`, `settlement_rule_id`: always null (not in the export; never defaulted). Liquidity and live status are not
  in the export either. All four are named in `unknown`.
* PrizePicks is not in the export and is never a provider here, even if a row under that id ever appeared.
* Fixture replay (no `APP_EXPORT_DIR`): it stores no quote snapshots, so `providers` is `[]`.

## `GET /api/betting/board/{game_id}/{player_id}/{stat}`

```
{ "game_id": str, "date": "YYYY-MM-DD", "tip_utc": str | null, "final": bool,
  "player_id": int, "player": str, "team": str | null, "opponent": str | null, "stat": str,
  "as_of": str, "data_version": str, "provenance": "delayed",
  "lines": [ { "line": number,
               "quotes": [ { "quote_id": str, "provider": str, "side": "over" | "under",
                             "price_american": int | null, "implied_prob_raw": number | null,
                             "snapshot_utc": str, "minutes_before_tip": number | null,
                             "is_alternate": bool, "usable": bool, "unusable_reason": str | null } ] } ],
  "providers_absent": [str],
  "model_ref": { "props": "/api/props/{game_id}/{player_id}/{stat}", "source": "/api/record/{game_id}/{player_id}/{stat}" },
  "unknown": ["fee", "settlement_rule_id", "liquidity", "live_status"],
  "note": str }
```

* `team`, `opponent`: team abbreviations (null if unknown). `date`: the slate night. `final`: the game is finished.
* `lines`: ascending by line; within a line by provider id, then over before under.
* `quote_id`: `game_id|player_id|stat|line|provider|side|snapshot_utc`, line written with Python's `:g` (`20.5`,
  `20`). Unique within a board because the rule serves one quote per (line, provider, side).
* `price_american`: as stored. `implied_prob_raw`: the raw implied probability the export stored, vig included
  (not recomputed here). Both are null when the quote is not usable: the export flagged it (`unusable_reason` is its
  stored reason, e.g. `polymarket_under_placeholder`), or the stored number is not an American price (0 or between
  -100 and 100; reason `not_american_odds`), the same test the props route applies. `implied_prob_raw` is also null
  when the export stored none or a value outside 0..1.
* `snapshot_utc`: ISO UTC with `Z`, to the second. `minutes_before_tip`: as stored.
* `providers_absent`: the providers of `/providers` with no served quote for this prop ("unavailable", not an error).
* `model_ref.props` needs a `?line=` query, as that route always has.
* A prop with a forecast but no stored quote is **200** with `lines: []` and every provider in `providers_absent`.
* `note` (fixed text): states the rule, that prices are as stored, the `implied_prob_raw` label, that fees, settlement
  rules, liquidity and live status are unknown, and that no model number is served. On the fixture replay it adds
  "This data source (the fixture replay) stores no quote snapshots." and `lines` is always `[]`.

Refusals (all from `app/error_codes.json`, the shared error body): `unknown_game` (404, `game_id`; also a game in a
season-2025 slate), `unknown_stat` (404, `stat`; anything but pts, reb, ast, fg3m), `unknown_player` (404,
`player_id`; not listed in that game), `no_forecast` (404, `stat`; listed but no forecast for that stat),
`bad_request` (422, `player_id` not an integer), `export_refused` (503).

## `GET /api/betting/slate/{date}`

```
{ "date": "YYYY-MM-DD", "as_of": str,
  "games": [ { "game_id": str, "tip_utc": str | null, "home": str, "away": str, "final": bool,
               "n_props_with_quotes": int } ],
  "props": [ { "game_id": str, "player_id": int, "player": str, "team": str | null, "stat": str,
               "n_providers": int, "n_lines": int } ] }
```

* The find step: every game of the night (in the slate's own sequence) and the props with at least one served quote.
  `home`/`away` are team abbreviations. `n_providers`/`n_lines` count what that prop's board serves (tested equal).
* `props`: by game, then player name, then stat (pts, reb, ast, fg3m).
* Not paged: the largest night of the frozen 2026-11-16 export is 70,451 B (550 props), under the 100 KB bar.
  A full 15-game night would be about 1.5 times the largest seen (estimate, not measured); add `limit`/`offset` then.
* Refusals: `not_a_date` (404, `date`), `unknown_slate` (404, `date`; also a season-2025 slate), `export_refused` (503).

## Holdout

Season 2025 is never served: the store refuses an export whose `games` holds it (503 on every route, tested with a
planted row); a quote or forecast row for a game the store does not list is never read (tested with planted 2025-26
rows under a fresh provider id: not counted, not served); and these routes also refuse a slate or game whose season
is 2025 (tested by marking a loaded slate).

## Example payloads (frozen export of 2026-11-16, trimmed)

```json
{"as_of": "2026-11-17T01:05:05.861924Z", "data_version": "764432af8422",
 "providers": [{"id": "betmgm", "label": "BetMGM", "kind": "sportsbook", "price_unit": "american",
                "newest_snapshot_utc": "2026-11-17T00:45:47Z", "n_quotes": 11529, "fee": null, "settlement_rule_id": null},
               {"id": "kalshi", "label": "Kalshi", "kind": "exchange", "price_unit": "american",
                "newest_snapshot_utc": "2026-11-17T00:45:47Z", "n_quotes": 16584, "fee": null, "settlement_rule_id": null}],
 "unknown": ["fee", "settlement_rule_id", "liquidity", "live_status"],
 "note": "Stored snapshots from the nightly export. Delayed, not live. Analysis only; this service is read-only."}
```

```json
{"game_id": "0022600229", "date": "2026-11-12", "tip_utc": "2026-11-13T01:00:00Z", "final": true,
 "player_id": 1642383, "player": "Walter Clayton Jr.", "team": "DAL", "opponent": "MIN", "stat": "ast",
 "as_of": "2026-11-17T01:05:05.861924Z", "data_version": "764432af8422", "provenance": "delayed",
 "lines": [{"line": 3.5, "quotes": [{"quote_id": "0022600229|1642383|ast|3.5|novig|over|2026-11-13T00:46:48Z",
   "provider": "novig", "side": "over", "price_american": 138, "implied_prob_raw": 0.42016806722689076,
   "snapshot_utc": "2026-11-13T00:46:48Z", "minutes_before_tip": 13.1843788, "is_alternate": false,
   "usable": true, "unusable_reason": null}]}],
 "providers_absent": ["betmgm", "betonlineag", "...", "williamhill_us"],
 "model_ref": {"props": "/api/props/0022600229/1642383/ast", "source": "/api/record/0022600229/1642383/ast"},
 "unknown": ["fee", "settlement_rule_id", "liquidity", "live_status"], "note": "..."}
```

## Changes to this contract

A new field is additive (v1 stays). Serving older snapshots, paging the slate, or any computed number is a v2.
