# kalshi_prop_history v1: Kalshi player-prop price history for the app's Market lens

_2026-11-17. Producer: `nba/kalshi/prop_history.py` (market agent). Consumer: the app API (app agent) later, read-only.
Additive changes keep v1; a breaking change bumps `schema`. Nothing schedules the producer yet (see the review,
`docs/reviews/kalshi_prop_history_2026-11-17.md`)._

**Display data only.** Nothing here is a forecast, an edge or a signal. No alert, EV, JARVIS or paper-trade path reads it
(Devin's live rule: calm calls, no swing-chasing). The file carries **no model number** and **no derived indicator** (no
momentum, no trend arrow, no "moving toward the model"): prices and volumes as captured. The app shows it in the Market lens only.

## Command
```
python -m nba.kalshi.prop_history --date YYYY-MM-DD \
    --capture-dir <COPY of data/raw/live_<date>> --snapshot-db <COPY of data/kalshi/kalshi.duckdb> --out <dir>
```
Sends no request (the module imports no HTTP client). Opens the DuckDB copy read-only and **refuses** any path shaped like a live
store (`data/kalshi/*.duckdb`, `data/*.duckdb`, `nba.duckdb`). Refuses slates before 2026-10-20 (current season only). Either input
may be omitted; the file then simply has no series from it.

## Files
- `<out>/<slate_date>/<event_code>.json`: **one file per game** (`event_code` is Kalshi's game part, e.g. `26NOV16MEMWAS`), every
  in-scope prop market of that game.
- `<out>/<slate_date>/index.json`: the slate: games with file name, market count, measured `bytes` and `gz_bytes`, `over_budget`;
  the unresolved markets; unreadable inputs.
- Written atomically (temp name, then `os.replace`). The app must tolerate a missing file: render "no prop prices", not an error.
- **Size budget per game file: 4 MB raw, 0.75 MB gzipped.** Measured on 2026-11-16 (8 games, 2,340 markets, final): 2.47 to 3.15 MB raw,
  0.39 to 0.49 MB gzipped per game; the whole slate in one file would be 22.0 MB (why it is split). Over budget is flagged in the index
  (`over_budget: true`), never truncated. Serve gzipped; slicing one market out of a game file on the API side is fine (about 9 KB a market).

## Scope
Series in `SERIES_STAT` (`KXNBAPTS`, `KXNBAREB`, `KXNBAAST`, `KXNBA3PT`, `KXNBAPRA`, `KXNBAPR`, `KXNBAPA`, `KXNBARA`, `KXNBASTL`,
`KXNBABLK`), markets whose event date is the slate date. Not exported: the `*PROT`, `ESCALATOR*`, `PTSLEADER`, `2D`/`3D` series and
every game-level series (game winners are in `kalshi_candles15`).

## Shape (game file)
```jsonc
{
  "schema": 1, "kind": "kalshi_prop_history", "display_only": true,
  "built_at": "…Z", "status": "partial" | "final",   // final = built at least 4 h after the slate's last scheduled tip
  "slate_date": "YYYY-MM-DD", "event_code": "26NOV16MEMWAS", "away": "MEM"|null, "home": "WAS"|null,
  "scheduled_tip": "…Z"|null, "tip_source": "ESPN scoreboard scheduled start (not the actual tip)",
  "time_unit": "unix seconds, UTC", "price_unit": "dollars per contract, 0 to 1, yes side",
  "quote_fields":  ["t_first","t_last","n_polls","bid","ask","last","volume","open_interest"],
  "candle_fields": ["t","o","h","l","c","volume","n_trades"],
  "sources": {
    "capture":  { "provenance": str, "nominal_resolution_s": 60,  "as_of": "…Z", "poll_gaps": { "<series>": [[t_from, t_to], …] } },
    "snapshot": { "provenance": str, "nominal_resolution_s": 900, "as_of": "…Z", "poll_gaps": [[t_from, t_to], …] },
    "prints":   { "provenance": str, "bucket_s": 60 }
  },
  "n_markets": int,
  "markets": [{
    "ticker": "KXNBAPTS-26NOV16MEMWAS-WASADYBANTSA4-30", "series": "KXNBAPTS", "event_ticker": str, "title": str,
    "stat": "pts", "threshold": 30.0,        // the title's "N+" (existing parser); null if the title did not parse
    "floor_strike": 29.5,                    // Kalshi's field, as sent
    "player_name": str|null, "player_id": int|null, "kalshi_player_uuid": str|null,
    "resolved": bool, "unresolved_reason": null | "name_not_in_reviewed_alias_table" | "title_unparsed",
    "quotes": [                              // one entry per source that saw the market; never blended
      { "source": "capture"|"snapshot", "kind": "quote_points", "nominal_resolution_s": 60|900, "gap_threshold_s": 150|2250,
        "first_seen": t, "last_seen": t, "n_polls": int, "median_gap_s": num|null, "max_gap_s": int|null,
        "gaps": [[t_from, t_to], …],
        "rows": [[t_first, t_last, n_polls, bid, ask, last, volume, open_interest], …] }
    ],
    "candles": { "source": "prints", "kind": "trade_candles", "bucket_s": 60, "n_trades": int,
                 "print_volume": num|null, "book_volume_delta": num|null, "print_coverage": num|null,
                 "rows": [[t, o, h, l, c, volume, n_trades], …] }
  }]
}
```

## Meaning (what the app must not blur)
- **`quotes` are polled book states, not candles.** Each source keeps its own series and resolution: `capture` about every 60 s inside
  the live capture's window (2 h before the first tip to 4 h after the last), `snapshot` every 15 min all day (from when the market
  opened). A row `[t_first, t_last, n_polls, …]` says: at each of `n_polls` consecutive polls from `t_first` to `t_last` the book showed
  exactly these values. It says nothing about the time between polls (the price may have moved and come back). Draw it as steps or
  points at the poll times; never as open/high/low/close.
- **Gaps are gaps.** A row never spans a hole longer than `gap_threshold_s` (2.5 nominal periods); each hole is listed in `gaps`, and a
  source-wide outage in `sources.*.poll_gaps`. Do not fill or interpolate. A market's series ends at `last_seen` because the market
  closed or left the open list (a prop closes early once it is hit), not because of a gap.
- **`bid`/`ask`** are the yes best bid and ask; Kalshi sends an empty side as `0.0`, kept as `0.0` (no midpoint is exported).
  `volume` and `open_interest` are cumulative contracts (fractional contracts exist). The snapshot store keeps prices as 32-bit floats; they are
  rounded back to Kalshi's 4 decimals and its volume and open interest are whole contracts.
- **`candles` are real trades.** Open, high, low and close of executed yes prices per minute (Kalshi's own `created_time`), `volume`
  contracts, `n_trades` prints; one row per trade id. No print, no row: never carry the previous close across. Within one microsecond
  the print order is not recoverable; ties are broken by trade id.
- **Coverage.** `print_volume` is the contracts printed between the market's first and last capture poll; `book_volume_delta` what the
  book's cumulative volume says traded over the same span; `print_coverage` their ratio (null without a volume change). Below 0.9 a
  missing candle is unknown, not quiet. Measured 2026-11-16: 1.003 for the slate, 2,288 of 2,289 markets at or above 0.9 (a ratio a
  little above 1 comes from prints near the span's edges: Kalshi's trade clock against our receive clock).
- **Three clocks.** Capture quotes use our receive time (manifest `received_at`, whole seconds), snapshot quotes the snapshot run's
  timestamp (up to the run's length off the page), candles Kalshi's trade time. Do not align them to better than a minute.
- **Unresolved markets** are exported with `player_id: null` and listed in the index; the name is Kalshi's text. Nothing is guessed;
  a name gets an id only through a reviewed row in `configs/kalshi_aliases.yaml`.
- **Prices are the market's, before fees.** No fee, no EV, no stake, no model probability is in this file, and none may be joined
  onto it in the Market lens.
