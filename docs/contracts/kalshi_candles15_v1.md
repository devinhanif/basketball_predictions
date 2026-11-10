# kalshi_candles15 v1: 15-second Kalshi price history for the app's candle chart

_2026-11-09. Producer: `nba/kalshi/candles15.py` (market agent). Consumer: the app API (app agent) later, read-only.
Additive changes keep v1; a breaking change bumps `schema`._

**Display data only.** Nothing here is a forecast, an edge or a signal. No alert, EV, JARVIS or paper-trade path reads it
(Devin's live rule: calm calls, no swing-chasing). The app shows it as price history; it never turns a move into a prompt.

## Files
- `data/export/kalshi_candles15/latest.json`: rebuilt about once a minute while the poller runs (`status: "live"`), and once
  more at the end of the night (`status: "final"`).
- `data/export/kalshi_candles15/<slate_date>.json`: the finished night, written once, at `status: "final"`. `<slate_date>` is the ET game date.
- Both are written atomically (temp name then `os.replace`). A failed build leaves the previous file. The app must tolerate a
  missing file (no game tonight, or the poller has not started): render "no live prices", not an error.
- Size: about 0.25 MB per game ticker for a full night (measured on synthetic data: 30 tickers x 1,560 polls plus 100,000 prints is
  7.5 MB). Spread and total markets add one point a minute each. Serve it as is, or slice by `t` on the API side.

## Shape
```jsonc
{
  "schema": 1,
  "kind": "kalshi_candles15",
  "display_only": true,
  "built_at": "…Z",                     // ISO-8601 UTC, our clock
  "status": "live" | "final",
  "slate_date": "YYYY-MM-DD",           // ET game date of the markets
  "bucket_s": 15,
  "time_unit": "t is the bucket start in unix seconds (UTC)",
  "price_unit": "dollars per contract, 0 to 1, yes side",
  "quote_fields":  ["t","bid","ask","mid","last","volume","open_interest","n_polls"],
  "candle_fields": ["t","o","h","l","c","volume","n_trades"],
  "notes": [str],
  "reconciliation": {…} | null,         // after the game: our 1-minute rollup vs Kalshi's official 1-minute candles
  "markets": [{
    "ticker": "KXNBAGAME-26NOV09SASPHX-SAS",
    "series": "KXNBAGAME",              // KXNBAGAME every 15 s; KXNBASPREAD / KXNBATOTAL every 60 s
    "event_ticker": str, "title": str,
    "first_poll": "…Z", "last_poll": "…Z",
    "n_polls": int, "n_quote_points": int, "n_candles": int, "n_trades": int,
    "max_gap_s": int|null,              // the longest hole between quote points; a missed poll shows up here
    "quotes":  [[t, bid, ask, mid|null, last|null, volume, open_interest, n_polls], …],   // one row per bucket, by t
    "candles": [[t, o, h, l, c, volume, n_trades], …]                                      // trade buckets only, by t
  }]
}
```
Rows are arrays in the order of `quote_fields` / `candle_fields` (columnar-ish, to keep the file small). `t` is unix seconds, not ISO
(the one exception to the desk contract's ISO rule, because charts index on it).

## Meaning (what the app must not blur)
- **`candles` are real trades.** Open, high, low and close are executed yes prices from Kalshi's public trade prints (Kalshi's own
  `created_time`), `volume` is contracts, `n_trades` the prints. A bucket with no trade has **no candle**: hold the previous
  close flat, draw nothing new, and never interpolate. Within one microsecond the print order is not recoverable, so a bucket
  whose first or last prints share a timestamp breaks the tie by trade id.
- **`quotes` are the polled book.** One row per 15 s bucket: the last poll in the bucket (`n_polls` says how many there were,
  normally 1). `bid`/`ask` are the yes best bid and ask; Kalshi reports an empty side as `0.0`, so `mid` is `null` unless both
  sides are positive (a one-sided book has no midpoint; do not substitute one). `volume` and `open_interest` are cumulative.
- **Gaps are visible.** A poll that fails or is skipped (backoff, request budget) leaves a hole in `quotes`; `max_gap_s` reports it.
  Do not fill it.
- **Prices are the market's, not ours.** The yes ask is what a buyer pays before fees; this file carries no fee, no EV, no stake.
- **Fresh within about 75 s, not 15 s.** The poller samples every 15 s, but the export is rebuilt every fourth poll. An app that wants
  tighter freshness should read the raw parquet through a new producer, not poll this file faster.

## `reconciliation` (after the game; `null` while live)
```jsonc
{ "tickers": int, "official_traded_minutes": int, "compared_minutes": int,
  "official_minutes_missing_ours": int, "ours_minutes_missing_official": int,
  "agree_open": int, "agree_high": int, "agree_low": int, "agree_close": int,
  "volume_official": num, "volume_ours": num, "errors": [str], "built_at": "…Z", "note": str }
```
Counts of (ticker, minute) pairs where Kalshi's official 1-minute candle (`period_interval=1`, the finest official period) shows a
trade price, from the window start. Agreement is exact to 1e-6. No statistical claim is made; the numbers say whether the chart's
trade rollup matches the exchange's own. Missing minutes on our side are the honest measure of catch-up gaps after a late start or an outage.
