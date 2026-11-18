# Kalshi prop price history for the app: inventory, export, proposal

_2026-11-17 (written from `date`: 2026-11-17 19:11 CST). Branch `agent/market-prop-candles`. Market agent. Not merged, not
scheduled, nothing installed. Display data only._

Devin asked: "can we get the kalshi candles of the props in the app". Label: **follows Devin's request** (a direct ask; the
market agent's standing view that prices are display and benchmark only is kept: nothing here feeds an alert, EV, JARVIS or
paper-trade path, and the export carries no model number).

## 1. Inventory (what we already hold)

All numbers below were computed on **copies** (capture pages and trade files copied to
`/Users/devin/Downloads/nba-scratch-prop-candles/live_<date>/`, the snapshot DB copied to
`/Users/devin/Downloads/nba-scratch-prop-candles/kalshi_copy.duckdb` after a snapshot run had finished, 2026-11-18T01:04:46Z).
No live store was opened in place; no request was sent. Tips are ESPN's **scheduled** start; "in game" is scheduled tip to tip + 150 min.
Counts are descriptive, not claims.

### 2026-11-16 (finished slate, 8 games)
| Source | Prop markets | Points | Points per market (median; p10 to p90) | Median gap before tip | Median gap in game | Span | Fields |
|---|---|---|---|---|---|---|---|
| Live capture `/markets` pages (`data/raw/live_<d>/kalshi/<SERIES>_p<n>/`) | 2,340 (3PT 490, REB 488, PTS 452, AST 354, PRA 310, STL 181, BLK 65) | 684,485 | 302; 217 to 387 | 60 s (n = 2,254 markets) | 60 s (n = 2,279) | 22:00:01Z to 04:38:40Z | yes bid, yes ask, last, volume, open interest, on every point (plus sizes, title, `floor_strike`, Kalshi player uuid) |
| 15-minute snapshot (`kalshi.duckdb` `kalshi_prices`, `source='live'`; raw pages in `data/kalshi/raw/<d>/`) | 2,340 (the same set) | 171,154 | 81; 33 to 95 | 918 s (n = 2,252) | 929.5 s (n = 2,247) | 2026-11-16T03:15:11Z to 2026-11-17T04:34:55Z | yes bid, yes ask, last, volume, open interest (prices stored as FLOAT, volume and OI as INTEGER) |
| Live capture trade prints (`kalshi_trades/<d>/*.json.gz`) | 2,301 markets with prints | 139,212 prints, 13.68 M contracts | n/a | n/a | n/a | from 2026-11-16T03:05:44Z | trade id, Kalshi `created_time` (µs), yes price, count, taker side |

Print coverage (contracts printed between a market's first and last capture poll, against the book's cumulative volume change over
the same span): slate 12.97 M / 12.93 M = **1.003**; per market median 1.0; **2,288 of 2,289** markets with book volume at or above 0.9.
On props the capture's prints are complete enough for true 1-minute candles (unlike the game-winner measurement of 2026-11-09, 41%
of minutes, before the capture's trade pull was reworked).

### 2026-11-17 (tonight, partial: capture copy at 2026-11-18T01:03:10Z, DB copy 01:04:46Z, 5 games)
| Source | Prop markets | Points | Points per market (median) | Median gap before tip | Median gap in game |
|---|---|---|---|---|---|
| Live capture pages | 1,434 | 217,513 | 153 | 60 s (n = 1,432) | 60 s (n = 818) |
| 15-minute snapshot | 1,434 | 92,072 | 70 | 918 s (n = 1,432) | 923 s (n = 260) |
| Trade prints | 1,207 markets | 34,178 prints, 1.73 M contracts | | | |

Print coverage so far: 0.9985 for the slate; 996 of 1,014 markets at or above 0.9.

### Sources that do **not** hold prop history
- `data/kalshi/candles15/` and `data/export/kalshi_candles15/` (the 15-second poller, installed and running tonight): game winners at
  15 s, spreads and totals at 60 s; **no props** (as its doc says).
- **Official 1-minute candles:** `nba.kalshi.candles15.finalize` pulls `GET /series/{s}/markets/{t}/candlesticks?period_interval=1`
  through `KalshiClient.fetch_candlesticks` (plain unauthenticated GET; the client has no auth header and no order or portfolio path).
  Its reconcile list is game winners only: `data/kalshi/candles15/2026-11-16/official_1m/` holds 16 files, all `KXNBAGAME` (about 6 KB
  gzipped each). **No prop 1-minute candle is on disk.** `kalshi_prices` has 341 `source='historical'` rows (June 2026), none for this season.
- `kalshi_orderbook/` in the capture: depth for the game series plus the open paper-leg tickers only (14 prop tickers in a sampled
  10 minutes on 11-16). Not exported.
- Series the capture also stores but that are out of scope (`*PROT`, `ESCALATOR*`, `PTSLEADER`, `2D`/`3D`); `KXNBAPR`, `KXNBAPA`,
  `KXNBARA` had no markets on either slate.

## 2. What was built
- `nba/kalshi/prop_history.py`: producer and CLI (`python -m nba.kalshi.prop_history --date D --capture-dir <copy> --snapshot-db <copy>
  --out <dir>`). Imports no HTTP client, sends no request, opens DuckDB read-only, refuses live-store paths and slates before 2026-10-20.
- `docs/contracts/kalshi_prop_history_v1.md`: the contract (display-only header, shape, meaning).
- `tests/kalshi/test_prop_history.py`: 15 tests on synthetic inputs in `tmp_path`.

### Choices (made without asking; each is reversible)
1. **Quotes are points, not candles.** Both quote sources are polled book states. They are exported as run-length rows
   `[t_first, t_last, n_polls, bid, ask, last, volume, open_interest]` (identical consecutive polls collapse; 684,485 capture polls became
   255,206 rows on 11-16). A row never spans a gap (> 2.5 nominal periods); gaps are listed per market and per source.
2. **True candles only from prints**, 1-minute buckets (the finest official period, so a later reconciliation is like for like), on Kalshi's
   trade clock, one per trade id, with per-market print coverage.
3. **Two quote sources, never blended**: capture (60 s, our receive time from the manifest) and snapshot (15 min, the run's timestamp).
   The snapshot series is the only history before the capture window opens (markets list the day before). It costs 4.5 MB of the 22 MB on
   11-16; kept whole rather than clipped to the capture's holes, so each series is what its source saw.
4. **Name resolution through the existing path**: `nba.kalshi.parse.parse_market` with `load_reviewed_aliases()`; stat from the series,
   threshold from the title's "N+" (Kalshi's `floor_strike`, e.g. 29.5 for "30+", is carried as sent). Unmatched names keep
   `player_id: null` and are listed.
5. **Scope: one file per game plus an index; budget 4 MB raw / 0.75 MB gzipped per game.** Measured (11-16): one slate file would be
   22.0 MB; per game 2.47 to 3.15 MB raw, 0.39 to 0.49 MB gzipped (about 290 markets a game, about 9 KB a market). A per-game file is what
   the Market lens needs on one screen; the API can slice one market out. I did not scope to "props with a model forecast": that needs a
   read of the model export, and the brief keeps model numbers out of this path.
6. **Tips**: ESPN's scheduled start from the newest stored scoreboard page, labelled as scheduled. `status` is `final` once the build is
   4 h past the slate's last scheduled tip (the capture's window), else `partial`.
7. **Current season only**: the CLI refuses dates before 2026-10-20.

## 3. Outputs (on copies; scratch, not tracked)
| File | Bytes | Gzipped |
|---|---|---|
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16CHICLE.json` | 2,470,194 | 386,592 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16GSWNOP.json` | 2,607,238 | 401,549 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16HOUMIA.json` | 2,637,713 | 415,707 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16MEMWAS.json` | 2,630,746 | 400,369 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16ORLBOS.json` | 2,890,934 | 459,879 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16PHIBKN.json` | 2,724,043 | 426,862 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16PHXDEN.json` | 2,893,611 | 476,209 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/26NOV16PORMIN.json` | 3,147,798 | 485,178 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-16/index.json` | 82,044 | 5,516 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/26NOV17CHALAL.json` | 1,001,001 | 147,199 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/26NOV17DETSAS.json` | 1,083,292 | 163,334 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/26NOV17NYKTOR.json` | 1,373,976 | 220,071 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/26NOV17PORMIN.json` | 1,226,780 | 181,848 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/26NOV17UTALAC.json` | 835,727 | 108,537 |
| `/Users/devin/Downloads/nba-scratch-prop-candles/out/2026-11-17/index.json` | 69,401 | 4,708 |

11-16: 2,340 markets, 1,886 resolved, 454 unresolved (29 names), status `final`, 8.5 s and 780 MB peak memory. 11-17 (partial): 1,434
markets, 1,048 resolved, 386 unresolved (22 names), status `partial`, 2.9 s and 473 MB. No unreadable input on either.

### Alias review
Unresolved names across both slates: **47**, all `name_not_in_reviewed_alias_table` (no title failed to parse). Review-only candidates
(exact normalized match in nba_api's bundled static list, no network, no team-as-of check) are in
`/Users/devin/Downloads/nba-scratch-prop-candles/alias_candidates_review_only.json`: 44 names with exactly one candidate id, 3 with none
(AJ Dybantsa, Meleek Thomas, Trevon Brazile; not in the static list). Nothing was written to `configs/` (off limits tonight): the
maintainer's step is a dated candidates file (`configs/kalshi_aliases_candidates_<date>.yaml`) and then reviewed rows in
`configs/kalshi_aliases.yaml`. Until then those markets show with Kalshi's name and no player link.

## 4. Checks (worktree `/Users/devin/Downloads/nba-wt-prop-candles`, `nba` resolves into it)
- `ruff check nba/kalshi/prop_history.py tests/kalshi/test_prop_history.py`: exit 0. `ruff format --check` on both: exit 0.
- `mypy nba/`: exit 0 (400 files).
- `pytest tests/kalshi/test_prop_history.py --no-cov -q -p no:cacheprovider`: 15 passed, exit 0. All of `tests/kalshi`: 159 passed, exit 0.
- Repo-wide `ruff check .` and `ruff format --check .` exit 1 on two pre-existing files under `app/fixtures/` (not touched here).

The tests cover: event-ticker parsing, resolved / unmatched / unparsed titles, run-length rows that never span a gap, resolution labels
per source, true OHLC from prints with tie-breaking and no fill, print coverage, an end-to-end build (capture pages with a hole,
duplicate and other-day prints, an unreadable file, a snapshot DB with a float32 price and a `historical` row that must not appear,
out-of-scope series), final versus partial, refusal of live-store paths and pre-season dates, a grep for order, credential, URL and
websocket strings, an import check (no HTTP client, no `nba.kalshi.client`, `nba.live`, models, daily, truth, parlay), and a key scan
of the export for model, probability, edge, trend or stake fields.

## 5. Proposal only (not built): finer prop candles going forward

**My recommendation is neither, for now.** What we already hold answers the request: 60 s quotes inside the capture window, 15-minute
quotes all day, and true 1-minute trade candles at 100% print coverage on 11-16. Installing this producer (below) is the cheap step.

**Installing this producer** (Devin's decision): a launchd job after the night (last tip + 4.5 h) and, if wanted live, every 10 to 15
minutes during games (not every minute: a full slate takes 8.5 s and 780 MB on this Mac). For the install it should read the snapshot's
write-once raw pages (`data/kalshi/raw/<d>/*_open_*.json.gz`) instead of the DuckDB, so no copy of a 126 MB store is needed and the
snapshot job is never touched; that reader is not written yet. Output to `data/export/kalshi_prop_history/<date>/`; the app API route
is the app agent's.

**(a) Official 1-minute candlesticks for a bounded prop set.** Same call as `candles15.finalize` (public, unauthenticated, one request
per ticker, `period_interval=1`). What it adds: per-minute bid and ask open/high/low/close (the quote range inside a minute) and an
official trade rollup to reconcile our prints. Sets: (i) only markets whose `print_coverage` is below 0.9: 1 market on 11-16, 18 so far
tonight, so about 1 to 20 requests a night, negligible disk; (ii) props the app pins or shows with a forecast: count not measured here;
(iii) every prop that traded: about 2,300 requests a night. Rate: the repo's docs (`docs/KALSHI_CANDLES_15S.md` section 1) say
unauthenticated limits are unpublished; candles15 holds itself to 60 requests a minute, at which (iii) takes about 40 minutes. Disk at
about 6 KB gzipped a ticker (the game-winner files; props are thinner, not measured): up to about 14 MB a night, 2.3 GB a season for
(iii). Where: a finalize step after last tip + 4.5 h, write-once raw, resumable, its own request budget. Review: it is outside the daily
replay, so the oracle is unaffected; it needs an adversary review of the budget and resume logic, the no-orders grep extended to it, and
a reconciliation report (official versus our print candles) as the acceptance test.

**(b) Prop series in the 15-second poller.** The capture already polls the prop series every 60 s, so adding them at 60 s duplicates
it. At 15 s: 7 series at one page each = 28 requests a minute, about 11,000 a night over 6.5 h, nearly half of the poller's 60-a-minute
budget, which it shares with the game-winner trade catch-up (a direct risk to the existing chart's coverage). Disk: a prop page is about
28 KB gzipped, so about 300 MB a night raw before compaction. Memory: about 3.6 M quote rows a night in the poller's frames on an 8 GB
Mac (the 11-09 review measured 38 MB and 6 s per export when every series sat in one file). If 15 s is ever wanted, do it for a handful
of pinned tickers with `GET /markets?tickers=…` (a documented filter): one extra request every 15 s.

**Risk to the 15-minute snapshot job** in both: separate process and store, so no lock contention; the shared risk is Kalshi's
unauthenticated rate bucket, which the capture (about 76,000 requests on 2026-11-09), the 15 s poller and the snapshot all draw on. Run
(a) after the games with backoff on 429, never during the snapshot's settled sweep window if it can be avoided.

## 6. Not verified
- Kalshi's current candlestick limits and whether a batch candlestick endpoint exists: the public docs page fetch was denied tonight;
  the costs above use only what the repo's docs record.
- Prop 1-minute candle file size (estimated from game-winner files).
- Coverage at the edges of a market's life (prints before the first capture poll are kept as candles but are outside the coverage span).
- The snapshot's growth against the readiness note's estimate (the check was denied; `data/kalshi/raw/2026-11-17` was 90 MB in 2,882
  files at 19:02 CST, `data/kalshi/raw` 2.4 GB in total).
- Tonight's slate was exported from a 01:03Z copy; a refresh of the copy was denied, so the 11-17 files stop there.

## 7. MODEL_PLAYBOOK check
`docs/MODEL_PLAYBOOK.md` has no section for market-data plumbing; it does not apply (no model is fitted or judged here).

## 8. Drafted rows (not appended)
DECISIONS (draft):
> 2026-11-17 · Prop price history for the app (display only) · Built `nba.kalshi.prop_history` and contract `kalshi_prop_history_v1` on
> `agent/market-prop-candles`: per-game files from the live capture's 60 s prop quotes, the 15-minute snapshot, and 1-minute trade
> candles from the capture's prints (coverage 1.003 on 2026-11-16, 2,288 of 2,289 markets at or above 0.9). No official 1-minute prop
> pulls and no prop polling at 15 s, because what we hold already gives 60 s quotes and near-complete minute candles. Display only; no
> alert/EV/JARVIS/paper path reads it. Reverse if: the app needs sub-minute prop quotes for pinned props, or print coverage falls below
> 0.9 on a slate (then proposal (a)(i)).
