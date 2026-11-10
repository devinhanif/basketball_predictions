# Kalshi 15-second candles for the app chart

_2026-11-09. Branch `agent/market-kalshi-15s-candles`. Not merged, not installed. Read-only market data, display only._

Markets are a prior and a benchmark, never a target. This job exists so the app can draw a live price chart. It feeds no alert,
EV, signal or JARVIS path (calm calls: no swing-chasing), and a test greps the new modules for order, portfolio, credential and
websocket code (non-negotiable 5).

## 1. What Kalshi's public docs say (checked 2026-11-09)

| Question | Answer | Source |
|---|---|---|
| Finest official candle period | `period_interval` is **1, 60 or 1440 minutes**. One minute is the finest. 15-second candles cannot come from the official endpoint | https://docs.kalshi.com/api-reference/market/get-market-candlesticks |
| Unauthenticated REST for markets | `GET /markets` declares `security: []` (no auth scheme); `limit` max 1000; filters `series_ticker`, `status`, `tickers`; prices are `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`, sizes `yes_bid_size_fp`, volume `volume_fp`, `open_interest_fp`. `updated_time` is documented as a non-trading metadata update, so it is **not** a price timestamp | https://docs.kalshi.com/api-reference/market/get-markets |
| Unauthenticated trades | `GET /markets/trades` declares `security: []`; params `ticker`, `min_ts`, `max_ts`, `limit` (max 1000), `cursor`; each trade has Kalshi's `created_time`, `yes_price_dollars`, `count_fp`. Sort order is **not documented** (observed newest first) | https://docs.kalshi.com/api-reference/market/get-trades |
| Order book | Spec says no security, but also lists a 401 "authentication required"; the docs contradict themselves. **Not used** (the poller does not need depth; the existing live capture has its own 401 handling) | https://docs.kalshi.com/api-reference/market/get-market-orderbook |
| Rate limits | Published only for **authenticated** tiers (Basic: 200 read tokens per second, 10 tokens per request, so about 20 reads/s). The page does not cover unauthenticated reads. A 429 carries no `Retry-After` and no `X-RateLimit-*`; there is no penalty, the bucket refills; the docs recommend exponential backoff | https://docs.kalshi.com/getting_started/rate_limits |
| Websockets | The connection itself requires API-key headers, **even for public channels** such as `ticker`. **Not used**: it would need a credential, which is forbidden | https://docs.kalshi.com/websockets/websocket-connection |

Consequences. (1) 15 s candles are **derived by us**: from executed trades (true OHLC) and polled quotes. (2) Unauthenticated limits are
unknown, so the poller holds its own limits far below the authenticated Basic tier: at least 0.5 s between requests, a sliding budget of
60 requests per minute (1 per second, 5% of Basic's 20 per second), jittered exponential cool-down on 429/5xx/transport errors. (3) The
official 1-minute candles are pulled once after the games, for reconciliation only.

## 2. What runs

`python -m nba.kalshi.poll15 run --date D` (launched by `ops/nba_kalshi_candles.sh`, every 10 minutes by launchd, idempotent):

- **Window.** From first tip minus 30 min (the launcher starts it 10 min earlier; it waits) until three consecutive successful polls
  after the last tip show no open game market of the slate date (the game-winner series lists only open markets), hard stop last tip plus 4 h. Tips come
  from the same schedule cache as the live capture (`nba.ops.watchdog.todays_tips`). An unreadable schedule alerts and does nothing
  (a 15 s poller of an unknown slate cannot guess its window).
- **Per poll** (`configs/kalshi_candles.yaml`): `GET /markets?series_ticker=KXNBAGAME&status=open&limit=1000` every 15 s (one request returns
  every open game winner, filtered to the slate date from the event ticker); `KXNBASPREAD` and `KXNBATOTAL` every 60 s. Player props are **not**
  polled: ten series, thousands of markets, several pages each, and the batched list call does not return them with the game winners.
  They can be added by config (`KXNBAPTS: 120`) after reading the estimate below.
- **Trades.** For the game-winner tickers whose cumulative `volume_fp` rose since the previous poll (largest jump first, at most 8 per poll),
  `GET /markets/trades?ticker=T&min_ts=…`, deduplicated by trade id. These prints make the candles real.
- **Raw is write-once parquet, one file per poll and series** (`data/kalshi/candles15/<ET date>/quotes/*.parquet`, `trades/*.parquet`; trades are one
  file per poll with every ticker's new prints). Why per poll and not a per-minute rollup: a crash or a kill loses at most the poll in flight,
  nothing is held in memory for a minute, files are never rewritten (tmp name then rename), and a restart reloads the night from disk
  (`Poller.restore`) and never re-pulls a print. The cost is many small files; `python -m nba.kalshi.poll15 compact --date D [--prune]` merges
  them (verified by reading back, `--prune` deletes the per-poll files only after the row count matches; maintainer's choice, unscheduled).
- **Every row carries** `fetched_at` (our clock, UTC, when the response arrived), `request_at`, `server_date` (Kalshi's HTTP `Date` header,
  whole seconds), Kalshi's `market_updated` (metadata only, see above) and, for trades, Kalshi's own `created_time` (microseconds).
- **Derived** (rebuildable): `quote_points` and `trade_candles` per 15 s bucket, in memory while running and as parquet under
  `<date>/derived/` at the end; the display export `data/export/kalshi_candles15/latest.json` every 4th poll (contract:
  `docs/contracts/kalshi_candles15_v1.md`).
- **After the game** (`finalize`, automatic at the end; `python -m nba.kalshi.poll15 finalize --date D` to re-run): official 1-minute candles for every
  game-winner ticker, one request each, raw gzipped and write-once (a re-run fetches only what is missing), then the reconciliation
  against our 1-minute rollup of the trades.
- **Heartbeat** `data/ops/heartbeat/kalshi_candles15.json`, rewritten every poll (status `waiting|running|backoff|finalizing|done`, polls,
  requests, errors, n429, cool-down, raw bytes). The launcher writes `kalshi-candles-launch.json`. `problem()` in `poll15.py` is a pure function
  for the independent watchdog; wiring it in is the ops owner's change (see section 6).

Not touched: `nba.duckdb` (never opened), `data/kalshi/kalshi.duckdb` (the 15-minute snapshot job's file, never opened here), the live capture
(`nba.live`, separate lock and separate raw tree), `app/`, and any model, truth or daily path.

## 3. What a candle is (and is not)

A 15-second candle from a 15-second quote poll would be one sample per bucket: open = high = low = close. We do not draw that and call it a
candle. `candles` are built from **executed trades** (true open/high/low/close and volume per bucket; no trade, no candle); `quotes` are the
polled book as one point per bucket. The honest limit: a bucket's range comes from trades, so a quiet market has no candle even while its
bid and ask moved; the chart should draw the quote line underneath and the candles on top.

## 4. Estimate (file sizes and CPU measured: live on 2026-11-09 during the 5-game slate, and on a synthetic full night; the trade request count is an estimate, no full game was observed)

| | Per night | Basis |
|---|---|---|
| Requests, game poll | 1,560 | 15 s over a 6.5 h window (23,400 s) |
| Requests, spreads + totals | 780 | 60 s, two series |
| Requests, trades | about 1,500 to 12,500 | 1 to 8 tickers per poll with a volume rise; a quiet poll costs none. Upper bound is 8 per poll for every poll |
| Requests, 1-minute reconciliation | about 10 to 30 | one per game ticker |
| **Total** | **about 4,000 typical, 15,000 ceiling** | 0.2 to 0.6 requests per second; the 60 per minute budget caps any minute (worst steady minute computes to 38) |
| For comparison | the live capture sent 76,572 requests on 2026-11-09 (as of 02:49Z) | `data/raw/live_2026-11-09/heartbeat.json` |
| Disk, per-poll files | about 20 MB quotes + 5 to 25 MB trades | measured: a GAME poll file is 7 KB (10 rows), a spread or total file 10 to 11 KB (about 160 rows); 565 quote rows were 98.6 KB as 12 files and 19.6 KB compacted (5x); trades 24 bytes per print |
| Disk, compacted | about 8 to 12 MB | quotes about 35 bytes per row, trades about 24 bytes per print |
| Disk, export | 0.25 MB per game ticker, atomic rewrite each minute | synthetic: 30 tickers + 100,000 prints = 7.5 MB |
| Season (about 165 game nights) | about 4 to 7 GB per-poll, about 1.5 GB compacted | flag growth above this; the readiness note's snapshot raw is 7.5 MB a day, this is a different store |
| CPU | under 1% of one core | a tick is a few requests and a parquet write; the export build is 0.7 s (frames 0.42 s, build 0.27 s, JSON 0.16 s) once a minute on the synthetic night; `caffeinate -i` keeps the Mac awake |

The two live dry-runs (2026-11-09, 02:54Z to 03:05Z, 3 + 5 + 8 polls, read-only, scratch store) sent 3, 32 and 52 requests with 0 rate-limit responses and no
HTTP or network error; the third run logged 7 "page cap" lines, which are the first-pull catch-up (the run started mid-game with a window
start hours earlier) and are reported, not hidden.

## 5. Reconciliation result (live, scratch store, a late start so it measures the catch-up, not a clean night)

Second dry-run, 8 game tickers, official 1-minute candles vs our trade rollup, from a window start 3.4 h before the poller began: 1,604 ticker-minutes with an
official trade price, 491 of them in our data; of those 491, open agreed in 487, high in 489, low in 487, close in 490; volume 1.97 M ours vs
2.08 M official on the compared minutes (the shortfall is the minutes where our page cap cut the catch-up). The other 1,113 official minutes
are before our first pull. This says the trade OHLC matches the exchange where we have the prints; it does not say a normal night is
complete. **The first full night's `reconciliation` block is the real check**, and `official_minutes_missing_ours` should be near zero.

## 6. Open items (for the maintainer; none needs a decision before merge)

- **Watchdog.** `nba.ops.watchdog` (not mine) should call `nba.kalshi.poll15.problem(now, tips, hb, hb_time, day, cfg)` next to `live_capture_problem`,
  with the heartbeat path `data/ops/heartbeat/kalshi_candles15.json` and a never-installed guard like the live capture's. Until then the
  launcher alerts to `data/ops/ALERTS.md` on a failed or unreadable-schedule night, and the heartbeat is readable by hand.
- **Overlap with the live capture.** Both read `/markets` for the game series; the capture also pulls order books and trades at up to 8 requests per
  second. Our extra load is about one tenth of the capture's. If either side ever sees 429s, ours backs off (15 s, doubling to 240 s) and the
  capture's own retry runs as before. The two raw trees and locks are separate.
- **Props at 15 s are not offered.** Ten series and thousands of markets would multiply the request count by roughly 20 to 50 at the same cadence; the
  game-winner chart is the product the app needs first. Props are a config line away at a slower cadence.
- **Trade order is undocumented.** The first pull after a late start is capped at 2 pages (2,000 prints) per ticker and logs when the cap hits;
  a normal poll sees a handful of prints.

## 7. Rollback

Nothing is installed by the branch. After install (the plist is in `ops/launchd/local.nba.kalshi-candles.plist`):

```
launchctl bootout gui/$(id -u)/local.nba.kalshi-candles
rm ~/Library/LaunchAgents/local.nba.kalshi-candles.plist
rmdir data/ops/kalshi_candles_lock 2>/dev/null     # only if a poller was killed hard
```
The data stays (`data/kalshi/candles15/`, `data/export/kalshi_candles15/`, the heartbeat files); delete them at will, nothing else reads them. To
revert the code: `git revert` the merge. `KalshiClient` changed backward-compatibly (new `max_attempts` argument defaulting to 4, a request counter,
the last `Date` header, `fetch_trades_page`; the retry loop no longer sleeps after its final failed attempt).
