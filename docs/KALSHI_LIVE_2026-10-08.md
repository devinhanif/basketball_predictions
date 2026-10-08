# Kalshi live ingest (2026-10-08)

The scaffold (`docs/KALSHI_SCAFFOLD_2026-10-08.md`) is now live. It makes public,
unauthenticated GETs only. No order, portfolio or credential path exists.

## Verified API facts (real responses, 2026-10-08)

- Base: `https://api.elections.kalshi.com/trade-api/v2`. It serves all of Kalshi despite the name.
- `GET /historical/cutoff` returns `market_settled_ts`, `trades_created_ts`, `orders_updated_ts`
  and `market_positions_last_updated_ts`. Today `market_settled_ts` = 2026-08-09T00:00:00Z.
  The scaffold looked for `settled_markets_cutoff`. The parser now accepts `market_settled_ts`.
- Markets settled before the cutoff are only on `GET /historical/markets`. After it they are on `GET /markets`.
- `GET /markets` takes `series_ticker`, `status` (one of open|closed|settled), `limit` (max 1000),
  `cursor`, `min_settled_ts`. The listing omits `series_ticker`, so it is derived from the ticker prefix.
- Prices are dollar strings (`yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`).
  Counts are `volume_fp` / `open_interest_fp` (decimal strings, rounded to int on load).
- Candlesticks:
  - live: `GET /series/{series}/markets/{ticker}/candlesticks`, fields `close_dollars`, `volume_fp`.
  - historical: `GET /historical/markets/{ticker}/candlesticks`, fields `close`, `volume`.
    The scaffold used the live path for both. This is fixed.
  - Params are `start_ts`, `end_ts`, `period_interval` (minutes).
- Prop titles look like `"Victor Wembanyama: 40+ points"`, with `floor_strike` = 39.5 and the stat in the series ticker.
  The scaffold regex (`"<Player> Points 25+"`) did not match this, so `parse_prop_title` was added. The old regex is kept.
- `result` can be `yes`, `no` or `scalar`. `scalar` appeared on the LeBron James preseason points markets.
  It looks like a void or fair-price settlement. Treat it as "exclude from calibration" until confirmed.

## NBA series that exist (from `GET /series?category=Sports`, 300 NBA-ish tickers)

`uv run python -m nba.kalshi discover` lists them. The ones we ingest (`configs/kalshi.yaml`):

| Series | Meaning | Open right now (preseason) |
|---|---|---|
| KXNBAGAME | game winner (2 markets per game) | 76 markets, closes 10-10 to 10-23 |
| KXNBASPREAD | team wins by more than X | 150 |
| KXNBATOTAL | game total over X | 126 |
| KXNBATEAMTOTAL | team total | 0 |
| KXNBAPTS / REB / AST / 3PT | player points / rebounds / assists / threes, "N+" ladders | 0 open |
| KXNBAPRA / PR / PA / RA | player combos | 0 open |
| KXNBASTL / BLK | steals / blocks | 0 open |

Other series exist but are not ingested. They are quarter/half markets (`KXNBA1H*`, `KXNBA1Q*`, ...),
head-to-head (`KXNBAH2H*`), `KXNBA2D`, `KXNBA3D`, `KXNBAFTM`, `KXNBAFIRSTBASKET`, `KXNBAWINS` and awards/futures.
Add a ticker to the config to ingest it. Player-prop parsing is only wired for the `SERIES_STAT` map in `thresholds.py`.

Props are posted per game close to tip-off. Preseason props did exist for some games.
`KXNBAPTS` had markets on 2026-10-06 (NYK@PHI preseason: Jaylen Brown, LeBron James, ...) that have already settled.
No prop market is open today. Expect props to appear game-day, so snapshot frequency matters most on game days.
Fee metadata: the series listing reports `fee_type` (`quadratic` for props, `quadratic_with_maker_fees`
for game/spread/total) and `fee_multiplier`. The EV engine should read these, not hardcode a rate.

## What the first live run captured

First snapshot, 2026-10-08 15:23Z, to `data/kalshi/kalshi.duckdb`:

- Open markets: 352 (76 game, 150 spread, 126 total).
- `kalshi_prices` rows (source='live'): 352. Mean yes_ask - yes_bid = 0.053.
- Settled markets swept (3-day lookback, upserted with `result`): 765. This includes 8 settled `KXNBAPTS` preseason rows.
- Raw gz files: 28, 2.2 MB total for `data/kalshi`.
- Historical backfill sample (`KXNBAPTS`, `KXNBAGAME`, `KXNBAREB`, one page of 100 each):
  - 300 markets; June 2026 Finals Game 5 NYK@SAS; 16 unmatched player names.
  - With `--candles --max-candle-markets 5`: 341 hourly candle rows for 5 markets (source='historical').
- Re-running the same backfill gave identical counts and made zero new network calls (cached raw).

## Commands

All commands use `uv run python -m nba.kalshi [--db FILE] <cmd>`. The default DB is `data/kalshi/kalshi.duckdb`.

```
uv run python -m nba.kalshi discover                         # list NBA series tickers
uv run python -m nba.kalshi cutoff
uv run python -m nba.kalshi snapshot                         # exit 1 if a series failed or a name is unmatched
uv run python -m nba.kalshi backfill-historical --max-pages 5 --page-limit 1000 --candles --max-candle-markets 200
uv run python -m nba.kalshi alias-candidates                 # review-only; writes data/kalshi/alias_candidates.json
```

- Snapshot idempotency: markets upsert on `ticker`; a price row is keyed `(ticker, ts, source)`.
  Replaying the same snapshot timestamp replaces rather than duplicates; a new run adds a new observation.
- Raw snapshots: `data/kalshi/raw/<YYYY-MM-DD>/<HHMMSS>_<series>_<status>_<page>.json.gz`.
- Unmatched player names are not dropped. The market and its prices are stored with `player_id` NULL
  and the names are appended to `data/kalshi/unmatched_names.json` (the CLI prints a warning and exits 1).
  After review, add rows to `configs/kalshi_aliases.yaml`. Then run
  `UPDATE kalshi_markets SET player_id = ...` or re-run the backfill. This is the "fail loudly" requirement
  without losing history during the first weeks. The alias table is the real bottleneck:
  All 17 names seen so far had exactly one nba_api candidate, but none are auto-applied.

## Separate DB and the merge path

`nba.duckdb` is single-writer and busy, so Kalshi writes to `data/kalshi/kalshi.duckdb` (gitignored under `/data/`).
The schema is the same (`kalshi_markets`, `kalshi_prices`, `ingest_log`, and the rest, created by `connect()`).
Downstream code reads it from a read-only connection to `nba.duckdb`:

```python
con = duckdb.connect("nba.duckdb", read_only=True)
con.execute("ATTACH 'data/kalshi/kalshi.duckdb' AS k (READ_ONLY)")
con.sql("SELECT * FROM k.kalshi_markets m JOIN k.kalshi_prices p USING (ticker)")
```

If a physical merge is wanted later, do it once with the writer free:
`INSERT OR REPLACE INTO kalshi_markets SELECT * FROM k.kalshi_markets` and
`INSERT INTO kalshi_prices SELECT * FROM k.kalshi_prices`.
A DuckDB file cannot be attached read-only while the snapshot job holds it open for writing.
The snapshot opens the file for a few seconds per run. Query between runs, or copy the file first.

## Scheduling on this Mac (launchd, free). Template only; not installed.

Pick the interval. Prices change fastest on game days. 15 min = 900 s is a reasonable default
(about 350 rows per snapshot now, roughly 35k rows/day, and a few hundred KB of gz per run).
Save as `~/Library/LaunchAgents/local.nba.kalshi-snapshot.plist` after editing the paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.nba.kalshi-snapshot</string>
  <key>WorkingDirectory</key><string>/Users/devin/Downloads/nba-prediction</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/uv</string>
    <string>run</string><string>python</string><string>-m</string><string>nba.kalshi</string>
    <string>snapshot</string>
  </array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/devin/Downloads/nba-prediction/data/kalshi/snapshot.log</string>
  <key>StandardErrorPath</key><string>/Users/devin/Downloads/nba-prediction/data/kalshi/snapshot.err</string>
</dict>
</plist>
```

Check `which uv` and adjust the path. Then, by hand:

```
launchctl load   ~/Library/LaunchAgents/local.nba.kalshi-snapshot.plist
launchctl list | grep kalshi
launchctl unload ~/Library/LaunchAgents/local.nba.kalshi-snapshot.plist   # stop
```

Notes: launchd only fires while the Mac is awake, so lid-closed periods leave gaps. History for those gaps can be
recovered only for settled markets (via candlesticks), not for the live top-of-book. If the job overlaps itself
(a slow run), DuckDB's file lock makes the second run fail fast with a non-zero exit. That is harmless.
The log files are unbounded, so truncate them occasionally.

## Known gaps / honest notes

- Live top-of-book is captured; live candlesticks are not pulled (the historical backfill is the candle path).
  Live candles can be added if gap-filling matters.
- Only 1 page per series is fetched in the backfill sample. A full backfill needs `--max-pages` raised, and the
  historical `/markets` filters (e.g. a date range) were not verified.
- Player-prop markets seen so far: 8 live-tier (preseason) and 300 historical-sample. This is too thin for any
  calibration claim. `min_settled_markets: 30` in the config is the floor used by `sampling.py`.
