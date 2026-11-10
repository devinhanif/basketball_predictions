# Kalshi 15-second candles for the app chart

_2026-11-09, revised the same night after the adversary review (docs/reviews/kalshi_candles15_2026-11-09.md). Branch
`agent/market-kalshi-15s-candles`. Not merged, not installed. Read-only market data, display only._

Markets are a prior and a benchmark, never a target. This job exists so the app can draw a live price chart. It feeds no alert,
EV, signal or JARVIS path (calm calls: no swing-chasing), and a test greps the new modules for order, portfolio, credential and
websocket code (non-negotiable 5).

## 1. What Kalshi's public docs say (checked 2026-11-09)

| Question | Answer | Source |
|---|---|---|
| Finest official candle period | `period_interval` is **1, 60 or 1440 minutes**. One minute is the finest. 15-second candles cannot come from the official endpoint | https://docs.kalshi.com/api-reference/market/get-market-candlesticks |
| Unauthenticated REST for markets | `GET /markets` declares `security: []` (no auth scheme); `limit` max 1000; filters `series_ticker`, `status`, `tickers`; prices are `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`, sizes `yes_bid_size_fp`, volume `volume_fp`, `open_interest_fp`. `updated_time` is documented as a non-trading metadata update, so it is **not** a price timestamp | https://docs.kalshi.com/api-reference/market/get-markets |
| Unauthenticated trades | `GET /markets/trades` declares `security: []`; each trade has Kalshi's `created_time`. Sort order is **not documented** (observed newest first). This job does **not** call it (see section 2); the live capture does | https://docs.kalshi.com/api-reference/market/get-trades |
| Order book | Spec says no security, but also lists a 401 "authentication required"; the docs contradict themselves. **Not used** | https://docs.kalshi.com/api-reference/market/get-market-orderbook |
| Rate limits | Published only for **authenticated** tiers (Basic: 200 read tokens per second, 10 tokens per request, so about 20 reads/s). The page does not cover unauthenticated reads. A 429 carries no `Retry-After` and no `X-RateLimit-*`; there is no penalty, the bucket refills; the docs recommend exponential backoff | https://docs.kalshi.com/getting_started/rate_limits |
| Websockets | The connection itself requires API-key headers, **even for public channels** such as `ticker`. **Not used**: it would need a credential, which is forbidden | https://docs.kalshi.com/websockets/websocket-connection |

Consequences. (1) 15 s candles are **derived by us**. (2) Unauthenticated limits are unknown, so the poller holds its own limits far below the
authenticated Basic tier: at least 0.5 s between requests, a sliding budget of **30 HTTP requests per minute** (client retries count; steady load is
6 a minute), jittered exponential cool-down on 429/5xx/transport errors (15 s doubling to 240 s). (3) The official 1-minute candles are pulled once
after the games, for reconciliation only.

## 2. What runs

`python -m nba.kalshi.poll15 run --date D` (launched by `ops/nba_kalshi_candles.sh`, every 10 minutes by launchd, idempotent):

- **Window.** From first tip minus 30 min (the launcher starts it 10 min earlier; it waits). It ends when the game-winner list has been empty for at least
  3 polls and **10 minutes, and only counting from 150 minutes after the last tip** (no NBA game ends sooner; review F2), hard stop last tip plus 4 h.
  Tips come from the same schedule cache as the live capture (`nba.ops.watchdog.todays_tips`). An unreadable schedule alerts and does nothing.
  The launcher's slate date is the ET date of now minus 6 h, so a crash at 00:30 ET is restarted for the day it belongs to (F7).
- **Per poll** (`configs/kalshi_candles.yaml`): `GET /markets?series_ticker=KXNBAGAME&status=open&limit=1000` every 15 s (one request returns every open
  game winner, filtered to the slate date from the event ticker); `KXNBASPREAD` and `KXNBATOTAL` every 60 s. Player props are **not** polled.
- **No trade requests (F5).** The 15 s candles are built from the KXNBAGAME prints the **live capture** already stores
  (`data/raw/live_<ET date>/kalshi_trades/*/*.json.gz`), read-only, incrementally, deduplicated on `trade_id`. One copy of the prints, one source of truth,
  zero extra requests. **Caveat, measured below: tonight that store held about 13% of the official in-game volume.** The candles are only as complete as
  the capture's trade pull.
- **Raw quotes are write-once parquet, one file per poll and series**, written with fsync before the rename (F1). A file that cannot be read back (power cut,
  full disk) is renamed `.bad`, logged to `errors.log` and the heartbeat (`quarantined`), and the night goes on. A restart reloads the night from disk.
- **Every row carries** `fetched_at` (our clock, UTC, when the response arrived), `request_at`, `server_date` (Kalshi's HTTP `Date` header, whole seconds),
  Kalshi's `market_updated` (metadata only). The trades carry Kalshi's own `created_time` (microseconds).
- **Derived** (rebuildable): `quote_points` and `trade_candles` per 15 s bucket; the display export `data/export/kalshi_candles15/latest.json` every 4th poll
  carries **game winners only** (F3); spreads and totals go to `<day>_lines.json` once, at the end. Contract: `docs/contracts/kalshi_candles15_v1.md`.
  The night's quote rows are held as polars frames, not Python dicts.
- **Finalize (automatic at the end; `finalize --date D` to re-run).** Official 1-minute candles for every game-winner ticker seen in the quotes or the
  capture (one request each; raw gzipped, fsynced, write-once; the run waits for the request budget instead of skipping and beats the heartbeat between
  tickers, F10), the reconciliation of the capture's prints against them, the final export, then **compaction with prune** (F6): the night's ~2,300 per-poll
  quote files become 1 compacted file (verified by reading it back before any file is deleted). So `data/kalshi/candles15` holds about 3 files and a few MB per night
  after finalize, not about 2,300 (the VM sync rsyncs `data/kalshi`; this keeps it small instead of moving the store).
- **Heartbeat** `data/ops/heartbeat/kalshi_candles15.json`, rewritten every poll: status `waiting|running|backoff|finalizing|done`, polls, requests, errors, n429,
  `consecutive_failures`, **`last_success_ts`** (the last game-series poll that returned at least one slate row, F4), cool-down, raw bytes, quarantined files.
  `problem()` in `poll15.py` (pure; for the independent watchdog) returns a problem when the heartbeat is stale (180 s), when 8 polls in a row failed, or, while a game can
  be on (first tip to last tip + 150 min), when no slate row has come back for 10 minutes. A poller that gets only 403s or only empty 200s therefore no longer
  looks healthy. Wiring `problem()` into `nba.ops.watchdog` is the ops owner's change (section 6). A full disk cannot crash the error log or the heartbeat.

Not touched: `nba.duckdb` (never opened), `data/kalshi/kalshi.duckdb`, the live capture's code, schedule and files (only read), `app/`, and any model, truth or daily path.

## 3. What a candle is (and is not)

`candles` are built from **executed trades** (true open/high/low/close and volume per bucket; no trade, no candle); `quotes` are the polled book as one point per
bucket. A 15 s quote poll alone would give open = high = low = close, which we do not call a candle. The honest limits: a quiet market has no candle even while its
bid and ask moved, so the chart draws the quote line underneath and the candles on top; and **candles bucket on Kalshi's trade time while quotes bucket on our receive
time** (two clocks, F12; the contract says so; the offset is the poll latency, normally under a second).

## 4. Measurements (2026-11-09)

### Reconciliation of the capture's prints against Kalshi's official 1-minute candles (tonight's real data)
`finalize` run read-only against `data/raw/live_2026-11-09/kalshi_trades` (179 files, 74,863 unique KXNBAGAME prints, 16 tickers) and 16 official 1-minute pulls,
window from 23:30Z to 03:47Z:

| | |
|---|---|
| Ticker-minutes with an official trade price | 3,472 |
| Of those, present in the capture's prints | 1,413 (**40.7%**) |
| Official volume in all those minutes | 42.73 M contracts |
| Volume the capture's prints cover (on the minutes it has) | 5.41 M = **12.7% of official** |
| On the minutes both have: open / high / low / close agree | 1,413 / 1,411 / 1,411 / 1,409 of 1,413; volume 99.5% |
| Prints the capture has that Kalshi shows no trade for | 0 minutes |

Reading: where the capture has a minute, it is right (the OHLC matches the exchange to the cent). But **during the games it has about 41% of the traded minutes and about
13% of the volume**; the missing minutes sit in 00:00 to 04:00Z (329 / 778 / 598 / 354 missing ticker-minutes per hour). That is a limit of what the capture stored, not of
the candle code. A likely cause (read from `nba/live/capture.py`, not verified): its pull pages newest-first with a 5-page cap and moves `min_ts` to the newest print, so a burst larger than the cap is lost. The capture is not mine to change in this branch. Candles from this source are therefore sparse in live games until the capture's pull is fixed or this job
pulls its own prints again (the earlier design, with the volume-jump queue). **This is the decision the maintainer should make; the reconciliation block now reports it
every night** (`minute_coverage`, `volume_coverage`, `official_minutes_missing_ours`).

### Cost (planning numbers; sizes and CPU measured on a synthetic full slate)
| | Per night | Basis |
|---|---|---|
| Requests, game poll | 1,560 | 15 s over a 6.5 h window (23,400 s) |
| Requests, spreads + totals | 780 | 60 s, two series |
| Requests, 1-minute reconciliation | about 10 to 30 | one per game ticker |
| **Total** | **about 2,400 to 2,500** | 0.1 requests per second; 30 per minute cap; the live capture sent 76,572 on 2026-11-09 as of 02:49Z |
| Raw quotes during the night | about 20 MB in about 2,300 files | a game poll file is 7 KB (10 rows); a spread or total file 10 to 11 KB |
| Raw quotes after finalize | about 1 to 4 MB in 1 file | measured 5x compaction: 565 rows, 98.6 KB as 12 files, 19.6 KB compacted |
| Season | about 0.2 to 0.6 GB (about 165 nights) | no trade copy any more |
| Live export `latest.json` (game winners only) | **6.4 MB, built in 1.4 s (1.2 s build + 0.2 s JSON) once a minute** | synthetic 12-game slate: 24 game tickers x 1,560 polls + 150,000 prints |
| `<day>_lines.json`, once at the end | 3.4 MB, 0.6 s | 128 spread and total tickers x 390 polls |
| Review's measurement before the fixes | 38.0 MB and 6.35 s every minute, 295 MB of Python dicts | all series in one file, per-ticker filters, dict rows |
| CPU | about 2% of one core (1.4 s per 60 s) | frames are built by a concat of per-poll frames; no tracemalloc in the timing |

## 5. Dry-runs (read-only, scratch store; 2026-11-09 02:54Z to 03:05Z, before the review)
Three runs (1, 5 and 8 polls) sent 3, 32 and 52 requests with no 429 and no HTTP or network error. Those runs also pulled trades (the old design); the trade code is gone.

## 6. Open items

- **Capture completeness (above).** The decision: fix the capture's trade pull, or restore this job's own pulls (cost about 1.5k to 12.5k requests a night and a second copy of the prints).
- **Watchdog wiring** (ops owner, `nba/ops/watchdog.py`): call `nba.kalshi.poll15.problem(now, tips, hb, hb_time, day, cfg)` next to `live_capture_problem`, heartbeat
  `data/ops/heartbeat/kalshi_candles15.json`, with a never-installed guard like the live capture's.
- **F9, due before April: playoffs and play-in are not polled.** `todays_tips` keeps regular-season game ids only, so `plan` returns `no_games` on those nights, which are the nights the chart
  matters most. Needs a `todays_tips(..., include_postseason=True)` variant for this job.
- **F12 (documented):** two clocks on one chart, above and in the contract.
- **Overlap with the live capture.** Both read `/markets` for the game series; ours is about 3% of its request rate. On a 429 ours backs off.
- **Props at 15 s are not offered** (ten series, thousands of markets).

## 7. Rollback

Nothing is installed by the branch. After install (the plist is in `ops/launchd/local.nba.kalshi-candles.plist`):

```
launchctl bootout gui/$(id -u)/local.nba.kalshi-candles
rm ~/Library/LaunchAgents/local.nba.kalshi-candles.plist
rmdir data/ops/kalshi_candles_lock 2>/dev/null     # only if a poller was killed hard (the lock also holds pid + start time)
```
The data stays (`data/kalshi/candles15/`, `data/export/kalshi_candles15/`, the heartbeat files); delete them at will, nothing else reads them. To revert the code: `git revert` the merge.
`KalshiClient` changed backward-compatibly (new `max_attempts` argument defaulting to 4, a request counter, the last `Date` header, `fetch_trades_page` (now unused here); the retry loop no
longer sleeps after its final failed attempt, so `snapshot` gives up a failing series 8 s sooner; review F11, benign).
