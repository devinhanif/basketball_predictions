# Adversary review: Kalshi 15-second candle poller (agent/market-kalshi-15s-candles, e167610)

_2026-11-09. Reviewer: adversary. Scope: `nba/kalshi/client.py`, `nba/kalshi/poll15.py`, `nba/kalshi/candles15.py`,
`configs/kalshi_candles.yaml`, `ops/nba_kalshi_candles.sh`, `ops/launchd/local.nba.kalshi-candles.plist`, the tests, `docs/KALSHI_CANDLES_15S.md`,
`docs/contracts/kalshi_candles15_v1.md`. Read from the branch worktree `.claude/worktrees/agent-a5301ae41f3a65442`. No live Kalshi
request was sent, no running process or launchd job was touched, no replay was run, season 2025 was not read._

**Verdict: WOUNDED.** The read-only guarantee holds and nothing reads the output. The code does what it says on a clean night.
It fails on unclean nights: one truncated raw file stops every restart for the rest of the night, three empty 200 responses end
the night early, a poller that fails on every request looks healthy to `problem()`, and the live export is about 38 MB and takes
about 6 s to build every minute, not the 7.5 MB claimed. The design question matters more: the live capture already stores
the same KXNBAGAME trade prints that the poller pulls again.

Verdict: BLOCKED

(Merge is blocked until F1 to F4 are fixed. Each is a few lines. F5 is Devin's call and does not block a fixed branch.)

**Top finding (F5, design).** The candles are trade-built, so how often we poll does not change them; only having the prints does.
The live capture (`nba/live/capture.py:429-520`, 60 s ticks, up to 150 tickers and 5 pages per tick) already stores every KXNBAGAME print
with its `trade_id` and `created_time`. Tonight it held **74,863 unique KXNBAGAME prints on 16 tickers** (`data/raw/live_2026-11-09/kalshi_trades`,
178 files up to 03:12Z). The export only refreshes every 60 s anyway (`export_every_polls: 4`). Of what this branch adds, only
the 15 s bid/ask quote points for game winners are new information. Everything else re-fetches data we already store.

## Model class and playbook check

Model class: none. This is a data-capture job on the live path, not a model. The playbook questions, answered for a capture job:
1. *Information or capacity?* The new information is the 15 s KXNBAGAME book. The trade candles add no information over the live capture's trade store (F5).
2. *Enough data?* n/a (no fit). The relevant point: the reconciliation on a full clean night has never been observed. The dry run started 3.4 h late.
3. *Test validity.* The tests run on recorded fixtures and pass. They do not cover a corrupt raw file, empty-but-200 responses after the last tip,
   a perpetually failing poller as seen by `problem()`, or the size of a full slate including spreads and totals. Those are exactly where F1 to F4 sit.
4. *Lineage.* This is a smaller copy of the live capture (`nba.live.capture`: volume-jump trade queue, trade_id dedupe, write-once raw, heartbeat). It inherits
   the capture's guards (dedupe, page-cap logging). It drops two of them: the capture's schedule source includes live games via the ESPN scoreboard, while this job uses regular-season tips only (F9),
   and the capture's watchdog wiring is not included (F4).
5. *Scored against?* Kalshi's official 1-minute candles (reconciliation). This is the right comparator. The rollup's period convention (`ceil`, inclusive end) matches the documented `end_period_ts`.

## Per-check table

| # | Check | Command / evidence | Result | Pass |
|---|---|---|---|---|
| 1 | Read-only, no secrets | `grep -nEi 'authorization\|api[_-]?key\|headers\|httpx\.(post\|put\|delete\|patch\|Client)\|environ\|getenv\|websocket\|wss://\|portfolio\|order\|secret\|token' nba/kalshi/{poll15,candles15,client}.py ops/nba_kalshi_candles.sh configs/kalshi_candles.yaml` | Only docstrings, comments, the `Date` response-header read, and polars `maintain_order`. Every request is `httpx.get(url, params, timeout)`. The paths are `/markets`, `/markets/trades`, `/series/{s}/markets/{t}/candlesticks`. No credential argument exists. The test `test_no_order_credential_or_websocket_code_in_the_new_modules` greps the same thing | PASS |
| 2 | client.py callers depend on old retry/sleep? | `grep -rnE 'KalshiClient\|_MAX_ATTEMPTS\|\._sleep\|retry_backoff_s\|last_server_date' --include='*.py' .` | Only `nba/kalshi/{ingest,snapshot,__main__,cutoff}.py` and `poll15/candles15` use `KalshiClient`. The live capture, JARVIS and fastarb use `nba.live.http.Fetcher` / `PoliteFetcher` (`nba/markets/fastarb/http.py:16`), which this branch does not change. Default `max_attempts=4` is unchanged. The one behaviour change: after the last failed 429/5xx attempt the client no longer sleeps 8 s before raising, so `snapshot` moves to the next series 8 s sooner on a failing series. No test or caller relies on the old sleep | PASS (benign) |
| 3 | Contention with live capture (shared IP) | live capture heartbeat: 86,828 requests 21:31Z to 03:25Z = about 4.1 req/s. Fetcher default 8 req/s (`nba/live/http.py:18`); fastarb `PoliteFetcher` 10 req/s when it runs | This poller adds 0.2 to 0.6 req/s on average with a 60/min cap, under 10% extra. On a 429 it backs off 15 s, doubling to 240 s, so it reduces load in a storm and cannot start one. No IP-wide budget exists across the three Kalshi clients (true before this branch too). The budget counts `_call`s, not HTTP requests: client retries (`max_attempts=2`) are not counted (F8) | PASS (note) |
| 4a | Window: first tip -30 min to 3 quiet polls, cap +4 h | `attack_restore.py` case 2 | **3 empty 200 responses 5 min after the last tip end the night** (`status=done`, note "no open game market left", at +5:45). Every later `plan` says `done`, so no restart that night (F2) | FAIL |
| 4b | Schedule unreadable -> alert, no-op | `nba/ops/watchdog.py:463` raises when no schedule source is readable; `poll15.main` prints `unknown ...`; the launcher alerts to ALERTS.md and exits 0 | Correct. A cache that is readable but stale or missing the date returns `no_games` silently (same as the live capture) | PASS |
| 4c | DST / schedule clock | Tips are UTC (`todays_tips`), buckets are UTC epoch, the slate date comes from `TZ=America/New_York date` | No DST dependence found | PASS |
| 4d | Midnight ET rollover | `attack_restore.py` case 4 | At 00:30 ET a late game is still on, but the launcher's `TODAY_ET` (`ops/nba_kalshi_candles.sh:26`) is now D+1. `plan` returns `early`, so a poller that crashed after midnight is never restarted and D's finalize and reconciliation never run (F7) | FAIL (minor) |
| 4e | Launcher lock, stale lock, RunAtLoad re-entrancy | read `ops/nba_kalshi_candles.sh:43-58`; `tests/kalshi/test_launcher.py` | Taking the lock with mkdir is atomic. A dead owner pid is cleared, and the EXIT trap removes the lock only after it was acquired. launchd never runs two instances of one label, so StartInterval during a foreground run is a no-op. If launchd kills the shell, it kills the process group (default `AbandonProcessGroup=false`), so the python grandchild does not outlive the lock. Residual risk: if a recycled pid belongs to a live process of the same user, the lock is held forever and nothing alerts (F13) | PASS (minor) |
| 5a | Write-once (tmp+rename) | `poll15.py:255-263`, `candles15.py:390-392` | Each file is written to a `.tmp` name and renamed, `exists()` is checked against the suffix, and `*.parquet` globs skip `.tmp`. There is **no fsync before rename**, so after a power cut a renamed file can be empty or truncated | PASS with F1 |
| 5b | Corrupt raw file | `attack_restore.py` case 1: one zero-length `quotes/*.parquet` | `Poller.run` -> `restore()` -> `RawStore.load` raises `polars ComputeError`, the process exits non-zero, and every 10-min relaunch hits the same file. **The rest of the night is lost**, with one ALERTS.md line per relaunch (F1) | FAIL |
| 5c | File count / disk | per night: 1,560 game + 390 + 390 spread/total + up to 1,560 trade files, about 3,900. Over a season (about 165 nights) that is about 640k files under `data/kalshi/candles15` | Compaction is manual and unscheduled. `ops/vm/sync_from_mac.sh:35` rsyncs `data/kalshi` wholesale and `ops/nba_daily.sh:328` runs `du -sh data/kalshi` nightly. Disk full: `log_error` itself raises inside the `except` (`poll15.py:672-673`, `429-431`) -> crash -> relaunch every 10 min (F6) | FAIL (major, ops) |
| 6a | OHLC from trades only, UTC 15 s buckets | `candles15.py:114-149` | Each bucket comes from `created_time` floored to 15 s on UTC epoch, deduplicated on `trade_id`, sorted by (created_time, trade_id). A bucket with no trade gets no candle. Quote points use **our** `fetched_at`, candles use **Kalshi's** `created_time`: one chart, two clocks (F12) | PASS |
| 6b | Duplicate prints across pages | `poll15.py:540` filters only against earlier pulls, not within the same pull | Duplicates within a pull reach the raw file and `self.trades` (the `trade_rows` counter is inflated), but `trade_candles`, `minute_rollup` and `RawStore.load` all deduplicate on `trade_id`, so candles and volume are correct | PASS |
| 6c | Late trades | `min_ts = last_trade_ts` (`poll15.py:523`) | A print published after a newer print was already seen is never fetched. The page cap drops the oldest prints for good (logged). The reconciliation shows both | PASS (documented) |
| 6d | Reconciliation | `candles15.py:283-345` | Inclusive-end `ceil` periods match Kalshi's `end_period_ts`. The comparison uses the same tickers and minutes, with tolerance 1e-6. An official file is pulled once and never refreshed, so a finalize run seconds after the last print can freeze an incomplete last minute (minor) | PASS |
| 7 | Nothing reads the candles | `grep -rnE 'kalshi_candles\|candles15\|poll15' --include='*.py' --include='*.sh' --include='*.ts' --include='*.tsx' --include='*.js' --include='*.yaml' --include='*.plist' .`; `grep -rnE 'data/export' nba ops app` | Only the new modules, their tests, the launcher and plist reference it. `data/export/` is read only by the app export (`nba/ops/app_export.py`, local directory). No alert, EV, JARVIS, signal or truth path | PASS |
| 8a | Heartbeat + `problem()` | `attack_restore.py` case 3: 40 polls of HTTP 403 | `errors=40 quote_rows=0 status=running`, **`problem()` = None**. A poller that never succeeds, or that succeeds with zero rows (schema or ticker-format change), looks healthy (F4) | FAIL |
| 8b | Silent failure modes until the watchdog is wired | reasoning from `ops/nba_kalshi_candles.sh` | Silent today: plist not loaded or exec blocked (TCC/uv path), a hung poller (launchd will not relaunch while it runs), permanent backoff, 200s with zero slate rows, export failures (errors.log only), a false `done` (F2), and the playoffs (F9). These do alert today: a non-zero exit (rc line in ALERTS.md), unknown plan, unexpected plan output | n/a |
| 9 | Export size / CPU (claim: 0.25 MB per game ticker, 7.5 MB, 0.7 s) | `PYTHONPATH=. .venv/bin/python scratchpad/attack/bench_night.py 12 32` (12 games, 32 spread and 32 total markets per game as measured in the branch's own §4, 6.5 h) | 336,960 quote rows + 150,000 prints held in memory = **295 MB Python heap**. The export covers 792 markets: **38.0 MB JSON, 6.35 s** to build (frames 2.83, build 2.37, json 1.15), rewritten every 60 s inside the poll loop. The doc's synthetic run had 30 game tickers only (F3) | FAIL |
| 10 | Tooling | `.venv/bin/ruff check nba/kalshi tests/kalshi ops` exit 0; `ruff format --check` exit 0; `mypy nba/kalshi/{poll15,candles15,client}.py` exit 0; `pytest -q -p no:cacheprovider --no-cov tests/kalshi tests/live/test_no_orders.py tests/ops` **362 passed, exit 0** (with coverage on, a subset run exits 1 only because of the repo-wide 70% gate) | clean | PASS |

## Findings (severity, exact line, exact fix)

**F1 major: a truncated raw file stops every restart for the rest of the night.** `nba/kalshi/poll15.py:277`
(`pl.concat([pl.read_parquet(f) for f in files], ...)`) runs inside `restore()` (`poll15.py:654`), before the loop's try. Fix: in `RawStore.load`, read each
file in its own try. On failure, rename it to `<name>.bad` and record it (return the list so `run` can `log_error` it), then continue. In `RawStore.write`
(`poll15.py:262-263`), write via an open file handle and `os.fsync` it before `tmp.rename(path)`. Same for `candles15.py:391-392`. Add a test
with a zero-length parquet in `quotes/` that asserts `run(once=True)` returns 0 and the file is quarantined.

**F2 major: three empty responses end the night while games are on.** `poll15.py:591-592` counts a quiet poll for any successful empty
KXNBAGAME list once `now >= tips[-1]`, and `plan` then returns `done` for the rest of the night (`poll15.py:723-724`). Fix: count quiet only after
`now >= tips[-1] + timedelta(minutes=150)` (no NBA game ends sooner) and require `quiet_polls_to_finish` to cover at least 10 minutes
(e.g. 40 polls at 15 s, or a `quiet_min` config). Keep the 4 h cap. Add a test: empty 200s at last tip + 5 min must not finish.

**F3 major: the export is 38 MB and takes 6.4 s to build every minute on a full slate, not 7.5 MB.** `candles15.py:183-206` exports every polled
series (spread and total markets are about 96% of the tickers). The loop at `candles15.py:184-187` filters the whole frame once per ticker,
which costs O(tickers x rows). `self.quotes`/`self.trades` (`poll15.py:386-387`) keep the night as Python dicts (295 MB). Fixes: (a) `latest.json` carries
KXNBAGAME only, and spreads/totals go to `latest_lines.json`, or only to the final dated file; (b) group once with `partition_by("ticker", as_dict=True)`
instead of per-ticker `filter`; (c) keep the in-memory night as appended polars frames, not dicts; (d) correct `docs/KALSHI_CANDLES_15S.md` §4 (the
"Disk, export" and "CPU" rows) and the contract's "Size" bullet with the measured numbers. Until (a), the app consumer will load 38 MB a minute.

**F4 major: `problem()` calls a failing poller healthy.** `poll15.py:751-755` checks only that the heartbeat is fresh, and a poller in permanent
backoff, or one getting 200s with zero slate rows, still writes a fresh beat. Fix: write `last_success_ts` (the last poll that returned at least one
slate row for the game series) in `beat()` (`poll15.py:401-424`). In `problem()`, inside the window after first tip, return a problem when
`now - last_success_ts > 10 min` or `consecutive_failures >= 8`. Add the 403 case above as a test. This matters even before the watchdog is wired,
because the wiring will call this function as written.

**F5 major (design; Devin's call): the trade pulls duplicate the live capture.** `poll15.py:487-556` re-fetches `/markets/trades` for KXNBAGAME,
and the live capture already stores those prints (74,863 unique on 16 tickers tonight through 03:12Z, `data/raw/live_2026-11-09/kalshi_trades`).
The candles do not depend on poll cadence. Options: (a) build the trade candles from the live capture's raw trade files (zero extra requests,
no second copy of the same prints, one source of truth), and keep this poller for KXNBAGAME quotes only; (b) keep it as is and accept a second
copy plus about 1.5k to 12.5k extra requests a night. The safe default is (a). This finding is advice; it does not block a merge of a branch with F1 to F4 fixed.

**F6 major (ops): about 3,900 files a night, about 640k a season, under `data/kalshi`.** `RawStore` writes one file per poll per kind, and compaction
is manual. `ops/vm/sync_from_mac.sh:35` rsyncs all of `data/kalshi`. Fix: at the end of `Poller.finalize` (`poll15.py:684-704`), after the
reconciliation, run `store.compact(kind, schema, prune=True)` for both kinds. It already verifies before pruning. Or move the store out of
`data/kalshi` (e.g. `data/raw/kalshi_candles15/`) so the VM sync and kalshi-dir tooling do not pick it up. Disk-full sub-finding: `log_error` (`poll15.py:429-431`)
raises inside the `except` at `poll15.py:672-673`. Wrap the errors.log write in `contextlib.suppress(OSError)`.

**F7 minor: no restart after midnight ET.** `ops/nba_kalshi_candles.sh:26` sets `TODAY_ET` to the calendar date. A crash at 00:30 ET during a
late game is not restarted, and that night's finalize never runs. Fix: slate date = ET date of `now - 6 h` (e.g. `TZ=America/New_York date -v-6H +%Y-%m-%d`
on macOS), or have `plan` check D-1 first when D-1's heartbeat is not finished and `now < D-1 window_end`.

**F8 minor: the budget comment is wrong and retries are not counted.** `configs/kalshi_candles.yaml:24` gives the worst minute as 38, but with
`max_pages: 2` it is 4 + 4x8x2 + 2 = 70, so the 60 budget binds in a catch-up minute and the trade pulls can starve a later game poll (a quote hole).
`Poller._call` (`poll15.py:434-438`) takes one budget slot per call, while the client may send 2 requests. Fix: take the budget from the
`client.requests` delta after each call (or let the client take it per attempt), and correct the comment.

**F9 minor (loose end, due before April): playoffs and play-in are not polled.** `todays_tips` keeps only `REGULAR_SEASON_PREFIX` game ids
(`nba/ops/watchdog.py:447,461`), so `plan` returns `no_games` on playoff and play-in nights, which are the nights the chart matters most. Fix later: a
`todays_tips(..., include_postseason=True)` variant for this job.

**F10 minor: the heartbeat goes stale during finalize when the network is bad.** `candles15.py:370-393` makes up to about 20 requests with no beat.
At `max_attempts=2` and a 10 s timeout that is up to about 7 min, more than `heartbeat_stale_s=180`. Fix: pass a beat callback into `finalize_day`
and call it per ticker.

**F11 minor: client behaviour change.** `client.py:121-122` removes the sleep after the last failed attempt. Effect on `snapshot`: a failing
series is given up 8 s sooner. This is benign. Keep it, and note it in the merge DECISIONS row (already in the doc §7).

**F12 minor: two clocks on one chart.** Quote buckets use `fetched_at` (`candles15.py:92`), candles use `created_time`. Fix: document it in the
contract's Meaning section, or bucket quotes on `server_date` when present (whole seconds).

**F13 minor: lock pid reuse.** `ops/nba_kalshi_candles.sh:46` trusts `kill -0`. A recycled pid of the same user keeps the lock forever, with no alert
until F4 is wired. Fix: store `pid` and the `ps -o lstart= -p` start time, and treat a mismatch as stale.

## Corrected family and power note

There is no statistical claim in this branch: no metric, floor or CI, so no BH family applies. The one quantitative claim is the
dry-run reconciliation (491 compared minutes: open 487, high 489, low 487, close 490 agree). It is a descriptive count from a run that started 3.4 h late,
not a test. The real check is the first full clean night, with `official_minutes_missing_ours` near 0.

## The one biggest risk

The poller is silently half-working on a game night: in permanent backoff, getting empty 200s, or ended early by a false quiet. It writes a fresh
heartbeat, `problem()` returns None (F4), and the app shows a chart with holes or a stale `latest.json` while every check says healthy.

## Not verified

- Kalshi's real behaviour for `status=open` on game-winner markets during and after a live game (the quiet logic assumes they drop out only once closed).
- Whether unauthenticated reads are rate-limited per IP (not documented; no 429 observed in the branch's three dry runs).
- launchd's process-group kill on the exact uv -> sh -> caffeinate -> uv -> python chain (from documented launchd defaults, not exercised).
- The trade API's sort order (documented as unspecified). The page-cap logic assumes newest first.
- The replay oracle (run separately by the main session).
- How complete the live capture's KXNBAGAME trades are versus Kalshi's official candles (F5's option (a) would need its own reconciliation).

## Recommended changes (not applied)

F1, F2, F3 (a)+(b)+(d), F4 before merge. Then F6 (finalize-time compaction) and F7. F5 goes to Devin as a design decision. F8 to F13 can wait.

## Drafted DECISIONS row (not appended)

| 2026-11-09 | Adversary review of agent/market-kalshi-15s-candles (docs/reviews/kalshi_candles15_2026-11-09.md): WOUNDED, merge blocked on F1 (corrupt-file restart loop), F2 (false early finish), F3 (38 MB export a minute on a 12-game slate), F4 (`problem()` blind to a failing poller). Read-only and no-consumer claims hold. Open for Devin: build the trade candles from the live capture's existing KXNBAGAME trade store instead of re-pulling (F5). Would reverse: the first full clean night reconciles near-completely and the live capture's trade store proves incomplete for KXNBAGAME. |

## Scratch

Attack scripts (not in `nba/`): `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/attack/attack_restore.py`,
`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/attack/bench_night.py`.
Both were run from the branch worktree with `PYTHONPATH=. .venv/bin/python <script>` (exit 0).

---

## Re-review: e167610..45e17e2 (2026-11-09, diff only, against F1 to F13)

_Read from the branch worktree at 45e17e2. Same rules: no live Kalshi request, no replay, no season 2025, no running process or launchd job touched._

**Verdict for merge and install: WOUNDED.** All four blockers are fixed, and the plants confirm it. The install is safe: the job is read-only, no longer
sends trade requests, and nothing consumes its output. One problem is new, and it comes from my own F5 advice. The candles now depend on the live capture, which
holds 41% of the traded ticker-minutes and 13% of the in-game volume (11-09, measured by the branch agent; I did not re-measure, see "Not verified").
While live, the export gives the app no way to tell a missing print from a quiet market. The contract still says "no trade, no candle: hold the previous close flat".
So the candles are honest only after the game, and only for an app that reads `reconciliation`.

Verdict: APPROVED

(Approved for merge and install of the poller. Condition, R1 below: no app reads `markets[].candles` until R1 is done. Reading the quotes is fine.)

### Plants against 45e17e2

Command: `PYTHONPATH=. .venv/bin/python <scratchpad>/attack/rereview.py` from the worktree, exit 0.

| Plant | Before (e167610) | After (45e17e2) | Pass |
|---|---|---|---|
| Zero-length `quotes/*.parquet` (F1) | `ComputeError`, crash loop | `run` rc 0, file renamed `.bad`, listed in `quarantined`, logged | PASS |
| Empty 200s from last tip +5 min (F2) | night ended at +5:45, `done` | 160 polls to +44 min, still `running`, not finished | PASS |
| Empty 200s from last tip +150 min (F2) | n/a | ends at +2:40:00 (10 min, 40 polls), `done` | PASS |
| Perpetual HTTP 403 (F4) | `problem()` None | `'30 failed polls in a row (HTTPStatusError http=403 ...)'` | PASS |
| 200s with zero slate rows (ticker-format change) (F4) | `problem()` None | at +13:45: `'no successful game poll for 14 min (no slate row yet)'` | PASS |
| 12-game slate export (F3) | 38.0 MB, 6.35 s, 792 markets | `latest.json` 24 markets, **7.3 MB, 0.71 s** (with 150k prints; the contract's 6.4 MB / 1.4 s is the same order). Quotes are held as polars frames, not dicts | PASS |
| Recycled pid with a different start time (F13) | lock held forever | `test_a_recycled_pid_with_another_start_time_is_a_stale_lock` passes; the launcher clears the lock and starts one poller | PASS |
| Midnight ET (F7) | crash at 00:30 ET not restarted | `TODAY_ET` = ET date of now - 6 h (`ops/nba_kalshi_candles.sh:28`). No NBA tip is before 06:40 ET, so day boundaries are safe | PASS |
| Trade requests (F5) | `/markets/trades` pulls | `grep -nE 'fetch_trades_page\|/markets/trades' nba/kalshi/poll15.py nba/kalshi/candles15.py` exit 1 (none) | PASS |
| Budget (F8) | retries uncounted, wrong comment | `_call` charges `client.requests - before - 1`; budget 30/min against a steady 6/min; comment corrected. Finalize waits for the budget (max 120 s per ticker, with beats) | PASS |
| Finalize heartbeat (F10) | no beat for up to ~7 min | `beat` before each ticker and on each budget-wait second | PASS |
| File count (F6) | ~3,900 files a night, manual compaction | quotes compacted with prune at the end of `finalize` after verification; no trades tree. `log_error`/`beat` suppress `OSError` on a full disk | PASS |
| Live capture store read safety | n/a | the capture writes `*.json.gz.tmp` then renames (`nba/live/store.py:48-51`); `CaptureTrades` globs `*/*.json.gz`, so it never sees a partial file. Unreadable files are listed in `bad` | PASS |
| Tooling | | ruff 0, format 0, mypy 0 (3 files); `pytest -q -p no:cacheprovider --no-cov tests/kalshi tests/live/test_no_orders.py tests/ops` **371 passed, exit 0** | PASS |

F9 (playoffs) and F12 (two clocks) are documented as designed. F11 is unchanged and benign.

### New findings in the revision

**R1 major (product honesty; caused by the F5 default I recommended): the live candles are sparse and look like quiet markets.**
The contract (`docs/contracts/kalshi_candles15_v1.md`, Meaning, first bullet) still says "A bucket with no trade has **no candle**: hold the previous close flat".
A later bullet in the same section says the capture held 41% of traded minutes and tells the app to "show the coverage when it is below 90%". But `reconciliation`
is `null` while live (plant 4: `live_reconciliation=None`), so the app has no live coverage to show. The live chart would hold the close flat through about 59% of the minutes that traded.
Where the capture has a minute, it has the whole minute (volume ratio 0.995 on compared minutes), so the OHLC that exists is right. The gaps are whole minutes.
Fix, in either order:
(a) Live coverage per ticker from data we already have. In `candles15.build_export` (the per-market dict), add `print_volume` (the sum of capture prints since
the first poll) and `book_volume_delta` (last minus first cumulative `volume` from the quotes). Their ratio is a live coverage figure. In the contract, replace "no trade, no
candle: hold the previous close flat" with "no candle = no print **held**; when `print_volume / book_volume_delta < 0.9`, the app shows the gap as unknown, not flat".
(b) Restore a bounded trade source for KXNBAGAME only. That means 24 tickers and the e167610 `pull_trades` code, which was tested, with the budget raised to cover it (e.g. 60/min). Or have the capture's
trade queue put KXNBAGAME first (`nba/live/capture.py:429-445`). That is a live-capture change for its owner, not this branch.
My F5 advice rested on a premise I listed as unverified (that the capture's trades were complete). The measurement says they are not. For candle completeness, (b) with the
poller's own pull is probably better than the capture. Neither has been measured on a full clean night. Devin's call. Until (a) or (b), the app should draw quotes only.

**R2 minor: a false watchdog alert after a short night.** `problem()` (`poll15.py`, the `first <= now <= last + quiet_after_tip_min` branch) measures from the last poll with a
slate row. When the last game's market closes before +150 min, every later poll is a successful empty one, and the alert fires. Plant 3c: market closed at
+135, `problem()` fires at +146 ("no successful game poll for 11 min"). Fix: write `last_ok_poll_ts` (any successful game poll, empty or not) in `beat()`. In
`problem()`, once a slate row has ever been seen (`last_success_ts` not None), measure staleness from `last_ok_poll_ts` instead. A ticker-format change is still caught,
because it shows from the start of the night as "no slate row yet".

**R3 minor: lock ownership with an empty start file.** `acquire_lock` (`ops/nba_kalshi_candles.sh`) treats a live pid whose `start` file is missing or empty as
stale. That covers a lock written by the e167610 launcher, and the instant between `echo $$ > pid` and `start_of $$ > start`. launchd never runs two instances,
so this only matters for a manual run next to a launchd run. Fix: `[ -s "$LOCK/start" ] || return 1` before the start-time comparison (keep the old `kill -0` meaning
when no start time was recorded).

**R4 minor (scope): `KalshiClient.fetch_trades_page` (`client.py`) has no caller now.** Keep it if (b) is chosen; otherwise delete it.

### Biggest remaining risk

An app reads `latest.json` candles while live and draws flat "quiet" stretches where the market traded heavily (R1). That is a display that misleads, inside a product meant to teach.

### Not verified in the re-review

- The 12.7% volume and 40.7% minute coverage figures: they come from the branch agent's reconciliation against live official candles, which I could not re-pull without live requests. I checked only that the capture's KXNBAGAME store exists and is deduplicated (74,863 unique prints, first review).
- Whether the poller's own trade pull (option b) would reach full coverage on a clean night (never measured).
- `date -v-6H` is BSD `date`. It is correct on the Mac that runs launchd; the test checks the string, not the behaviour.

### Drafted DECISIONS row (not appended)

| 2026-11-09 | Re-review of agent/market-kalshi-15s-candles 45e17e2 (docs/reviews/kalshi_candles15_2026-11-09.md, Re-review): F1 to F4 fixed and verified by plant (quarantine rc 0; no early end before last tip +150 min; `problem()` catches 403s and zero-row nights; `latest.json` 7.3 MB / 0.7 s on 12 games). Merge and install approved; WOUNDED because the capture-sourced candles hold 41% of traded minutes and the live export carries no coverage. No app reads `candles` until R1 (live coverage field, plus either an own KXNBAGAME trade pull or capture priority; Devin's call). Would reverse: a full night's reconciliation shows coverage at or above 90%. |

---

## Re-review 2: 45e17e2..2c9386a (2026-11-09, diff only, R1 to R4)

_Worktree at 2c9386a. Same rules: no live Kalshi request, no replay, no season 2025, no running process or launchd job touched.
Command: `PYTHONPATH=. .venv/bin/python <scratchpad>/attack/rereview2.py` from the worktree, exit 0._

**Verdict for merge: SURVIVES.** R1 to R4 are fixed and the three plants pass. Install remains Devin's decision.

Verdict: APPROVED

| Plant / check | Result | Pass |
|---|---|---|
| Low-coverage night from capture prints only (R1a): own pulls return no prints; the capture store holds 10 prints x 300 per ticker on 24 tickers | export `coverage` = `{ratio: 0.077, floor: 0.9, complete_enough: false}`; every market's `print_coverage` is 0.077; the notes read "no candle = no print held (unknown if coverage < 0.9)"; no note says "hold". The contract (Meaning, first bullet) now says "no print held, not no trade. Never hold the previous close flat", and tells the app to treat the gap as unknown when `print_coverage < 0.9` or `complete_enough` is false | PASS |
| Catch-up burst (R1b): all 24 tickers' volume rises every poll, and every trade pull hits the 2-page cap, for 20 min, with a budget on the poller's clock and 0.5 s per request | **80 of 80 game polls sent, max gap 15.9 s**; line polls on schedule; trade requests 37.7/min, total 43.7/min, under the 60 cap. The 257 skipped requests are all trade pulls held back by the 10-slot reserve (`RequestBudget.take(reserve)`); none was a quote poll | PASS |
| R2 early close: the last slate row at +135 min, followed by successful empty polls | `problem()` None at +146 and +149 (measured from `last_ok_poll_ts`). If the polls stop succeeding it still fires ("no successful game poll for 11 min"). If no slate row was ever seen it still fires ("no slate row yet") | PASS |
| R3 lock with an empty start file | `[ -s "$LOCK/start" ] \|\| return 1` before the start-time comparison; new launcher test passes | PASS |
| R4 `fetch_trades_page` | has a caller again (the own bounded pull) | PASS |
| Reconciliation per source | `sources: {own, capture}`, each with minute and volume coverage and OHLC agreement, so the first full night will settle which source is complete | PASS |
| Tooling | ruff 0, format 0, mypy 0 (3 files); `pytest -q -p no:cacheprovider --no-cov tests/kalshi tests/live/test_no_orders.py tests/ops` **379 passed, exit 0** | PASS |

**New in the revision (minor, not blocking):**
- **N1:** the contract defines unknown as `print_coverage < 0.9`. When the book shows no volume, `print_coverage` is `null`, and the contract does not say what the app should do with null. A null here means the book recorded no trades, so a missing candle really is a quiet market. Fix: one clause in the Meaning bullet ("`null` = the book shows no volume: no candle is expected").
- **N2:** the slate-level `coverage.ratio` adds volume across tickers, so one heavily traded ticker can hide a near-zero one. The contract already points to the per-market `print_coverage`; the app should use that, not the slate ratio.
- **N3:** in a sustained burst the reserve correctly gives up trade pulls (257 skipped in 20 min), so candle coverage drops in exactly those minutes. That is by design: `print_coverage` shows it, and the per-source reconciliation measures it.

**Remaining unknown:** full-night coverage of the poller's own pulls against the capture's (the first `sources` block will answer it). Until then, an app showing candles must apply the coverage rule above.

**Drafted DECISIONS row (not appended):**
| 2026-11-09 | Re-review 2 of agent/market-kalshi-15s-candles 2c9386a (docs/reviews/kalshi_candles15_2026-11-09.md): SURVIVES for merge. Live coverage fields with the "no print held, unknown below 0.9" contract; own bounded KXNBAGAME pull with a 10-slot reserve (80/80 quote polls kept through a 20-min burst); early-close watchdog false alarm fixed; lock start-file check. Install is Devin's. Would reverse: the first full night's `sources` show both print sources under 0.9 volume coverage. |
