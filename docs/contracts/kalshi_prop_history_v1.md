# kalshi_prop_history v1: Kalshi player-prop price history for the app's Market lens

_2026-11-17. Producer: `nba/kalshi/prop_history.py` (market agent); scheduled build `nba/kalshi/prop_history_job.py`
(launchd plist prepared, not installed: `docs/reviews/kalshi_prop_history_sched_2026-11-17.md`). Consumer: the app API (app agent)
later, read-only. Additive changes keep v1; a breaking change bumps `schema`._

**Display data only.** Nothing here is a forecast, an edge or a signal. No alert, EV, JARVIS or paper-trade path reads it
(Devin's live rule: calm calls, no swing-chasing). The file carries **no model number** and **no derived indicator** (no
momentum, no trend arrow, no "moving toward the model"): prices and volumes as captured. The app shows it in the Market lens only.

## Command
```
python -m nba.kalshi.prop_history --date YYYY-MM-DD \
    --capture-dir <COPY of data/raw/live_<date>> --snapshot-db <COPY of data/kalshi/kalshi.duckdb> --out <dir>
python -m nba.kalshi.prop_history --date YYYY-MM-DD \
    --capture-dir data/raw/live_<date> --snapshot-raw data/kalshi/raw --out <dir>     # raw pages read in place, no DB
```
Sends no request (the module imports no HTTP client). Opens the DuckDB copy read-only and **refuses** any path shaped like a live
store (`data/kalshi/*.duckdb`, `data/*.duckdb`, `nba.duckdb`). Refuses slates before 2026-10-20 (current season only). Either input
may be omitted; the file then simply has no series from it.

## Scheduled locations (additive, 2026-11-17)
Once the job is installed, the files below live under `data/export/kalshi_prop_history/`:
- `latest/`: the slate being captured (`status: "partial"`), refreshed every 15 minutes while the live capture runs, and
  after the night the same files as `<slate>/` until the next slate's capture starts. Each file is replaced atomically;
  `index.json` is written after the game files, so read the index first and then the files it lists (a game file may be one
  build newer than the index; never of another slate). Files of the previous slate are removed after the new index lands.
- `<slate_date>/`: the finished night (`status: "final"`), written once, about 4 h 10 min after the last scheduled tip, as one
  directory rename (it appears whole or not at all). Never rewritten. **Gzipped** (main session's decision, 2026-11-17: plain JSON
  would be about 3.5 GB a season on a disk that was 97% full on 2026-11-10; gzipped about 3.2 MB a night, about 0.5 GB a season):
  `index.json.gz` and `<event_code>.json.gz`; decompressed, each is byte for byte the plain file the same build put in `latest/`.
  **What the app API needs** to read a finished night: open `index.json.gz` and the listed `<file>.gz` (the index's `file` field keeps
  the plain name, so append `.gz`) with `gzip.open` (Python) or `zlib.gunzip` (Node); or serve the bytes unchanged with
  `Content-Encoding: gzip` and `Content-Type: application/json` (the browser decompresses). The index's `bytes` is the plain JSON size
  and `gz_bytes` its size at gzip level 6 (the dated files use level 9, slightly smaller). `latest/` stays plain JSON for now.
- No capture for the slate (no games, capture not started): nothing is written and `latest/` keeps the previous night.
The bytes are those the command below would write for the same inputs (tested byte-for-byte on 2026-11-16).

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
