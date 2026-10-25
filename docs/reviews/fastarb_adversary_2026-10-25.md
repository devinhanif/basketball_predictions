# Adversary review: fast pre-tip poller and Kalshi / Polymarket-US arbitrage checker (2026-10-25)

Branch `agent/market-fast-arb` at `7ce3be0`. Builder review: `docs/reviews/fastarb_2026-10-25.md`. Reviewed in a
scratch detached worktree at 7ce3be0, with `data/` symlinked to the main checkout (`ls -la` showed
`data -> /Users/devin/Downloads/nba-prediction/data`). Fixtures and code only. No network, no outcomes, no
season-2025 data, no DuckDB write connection. The one DuckDB read was a read-only open of a scratch copy of
`lineups.duckdb`. Attack script: scratchpad `fastarb_attack/attack.py` (not in the repo).

Verdict: BLOCKED

(In the caller's terms this is **CHANGES_REQUIRED**. The builder's null, "no arbitrage after fees on 10-25", **SURVIVES**
as a description. The alerting path is **BROKEN**: it can write an exact two-leg "ARB" buy built on a stale price whose
depth it never checked, and one Pinnacle error stops the arb check for every later game.)

## Top finding

When a Polymarket US order book is not fetched, the poller prices that leg from the cached event payload, which can be
up to 300 s old, with unknown depth. `is_alertable` then skips the depth gate completely, so the ARB alert goes out
anyway. A book is missing when the fetch fails (a 429 after retries, the 50 s tick deadline, a timeout), when the
line's Kalshi ask is outside `price_band`, or when the line falls beyond the 24-book cap. That is also when prices are
moving. Reproduced on the recorded DET@PHI fixtures: a planted 4.5-line move on Kalshi with every `/book` call failing
wrote

```
- 2026-10-25 15:15 [fastarb] ARB DET@PHI home margin>4.5: BUY PolyUS asc-nba-det-phi-2026-10-25-pos-4pt5 '-4.50' @0.38 + BUY Kalshi KXNBASPREAD-26OCT25DETPHI-PHI5 NO @0.55 = 0.93; net +3.6c/ct after fees at 20 ct, depth unknown key=29b366d9
```

to ALERTS.md. The Kalshi leg's depth was known (1,401 contracts). The unknown Polymarket leg turned the whole gate off.

## What the work claims, and what holds

| Claim (builder review) | Status |
|---|---|
| Opposite sides correctly mapped across venues | **Holds.** All 22 cross-venue pairs recomputed from the fixtures have gross gaps between -2.0c and 0.0c. A single inverted side would show a gap near +/-25c on these lines. |
| Asks used on both sides, not bids | **Holds.** Kalshi `yes_ask_dollars` / `no_ask_dollars`, with NO size = `yes_bid_size_fp` (correct for a binary book). Polymarket US long = offers, short = 1 - bid (`venues.py:258-270`). The event `quote` is the ask (fixture: long 0.63 = bestAsk, short 0.38 = 1 - bestBid 0.62). |
| Both fees with correct rounding | **Holds for the formulas as configured.** Kalshi `ceil` per order through the verified `configs/parlay.yaml` object. Polymarket US 0.0695 x C x p(1-p), banker's cent, cross-checked against each market's `feeCoefficient`. I could not open docs.polymarket.us (no network), so the rounding rule rests on the builder's reading. |
| "Alert when ... depth supports at least 5 contracts" (builder review line 55) | **False when depth is unknown** (blocker B1). |
| "Polymarket US depth is the full book" (line 54) | **Only for booked lines.** Every other line uses the event quote with no depth (B1, B2). |
| Lag check uses only information available at alert time | **Holds.** News time is our own fetch time: `lineup_snapshots_dedup.fetched_at`, a naive-UTC TIMESTAMP, checked on a copy, max 20:09:19Z against now 20:14Z. Injury-report time is the file mtime converted to UTC. Pinnacle `last_update` is on or after the news; the ask comes from the current tick. |
| Pinnacle no-vig computed correctly | **Holds.** Proportional devig `(1/d_a)/((1/d_a)+(1/d_b))`. Spread mapping: home covers hp means margin > -hp. Whole lines skipped. |
| GET only; no credentials; key never logged; `nba.duckdb` never opened; lineups read-only | **Holds in the package** (grep and layering tests). Two small gaps: m4 and m5. |
| ~134-140 requests per tick, under Polymarket US's 25 req/s | **Holds.** `Fetcher(rate_per_s=10)` caps it at 10 req/s. 429 handling: 2 s then 4 s, `Retry-After` ignored, no global slow-down (m2). |
| Pinnacle credit ledger guard | **Holds as a guard.** `ledger.check` runs before the spend, on the shared daily ledger with a file lock. Hitting it crashes the tick (B3). |
| Underdog has no network code | **Holds.** `underdog.py` imports csv, dataclasses, pathlib, statistics and typing only. A test pins it. |
| "Cannot delay a daily job or be delayed by one" (`docs/DAILY_PIPELINE.md:1001`) | **False.** The lineups writer retries (`nba/lineups/store.py:169-191`, 10 x 3 s), but the poller does not. A lock collision on its first tick ends the poller's day (B4). |
| Stops at tip | **Holds** (`runner.py:315`, `c.tip - lead <= now < c.tip`; test `test_game_outside_the_window_is_not_polled`). No in-play guard if the tip time is wrong (M3). |

## Checks run (commands exact; all exit codes shown, nothing piped through tail/head)

Python is `/Users/devin/Downloads/nba-prediction/.venv-1/bin/python`, with `PYTHONPATH=<scratch worktree>` (confirmed:
`nba.__file__` resolves into the worktree).

| # | Check | Command | n | Result | Pass/fail |
|---|---|---|---|---|---|
| V1 | Lint | `ruff check nba/markets/fastarb tests/markets/fastarb` | 15 files | All checks passed, exit 0 | pass |
| V2 | Format | `ruff format --check nba/markets/fastarb tests/markets/fastarb` | 15 files | exit 0 | pass |
| V3 | Types | `mypy nba/markets/fastarb` | 11 files | no issues, exit 0 | pass |
| V4 | Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/markets/fastarb tests/test_layering.py` | 44 | 44 passed, exit 0. With coverage on: 44 passed, exit 1 from the repo-wide 70% gate on a subset run, not a failure | pass |
| C1 | Side mapping, all pairs | inline script in `fastarb_attack/` over the three fixtures (Kalshi, PM US event + ML book, PM intl) | 22 pairs (12 US, 10 INTL) | gross in [-2.0c, 0.0c], net in [-5.5c, -3.0c]; no inversion | pass |
| A1 | Stale / unknown-depth leg alerts (planted move, books fail) | `python attack.py <wt> <out>`, section A1 | 1 tick, 10 requests, 6 `book_failed` | 1 alertable ARB written to ALERTS.md, "depth unknown", net +3.6c | **FAIL (blocker)** |
| A2 | `price_band` does not gate alerts | attack.py A2: Kalshi total 225.5 YES 0.95 / NO 0.06, PM Over event quote 0.88 | 1 prop | `verdict=alert`, net +4.85c, depth_known False | **FAIL (blocker)** |
| A3 | Pinnacle error inside the tick | attack.py A3: `core_odds` raises `CreditBudgetExceeded` | 1 tick | tick raised `CreditBudgetExceeded`; nothing after the first game's arb check runs | **FAIL (blocker)** |
| A4 | Startup robustness | attack.py A4: (a) `read_slate` raises `duckdb.IOException` (lineups writer holding the lock); (b) Kalshi 503 on the first tick | 2 runs | both `rc 0`, 0 sleeps, heartbeat `"status": "no_games"`: the poller quits for the day and reports a normal empty slate | **FAIL (blocker)** |
| A5 | Look-ahead in the lag definition | read `news.py`, `runner.py:280-296, 358-386`; schema of a lineups copy | all inputs | only fetch-time stamps, all knowable at alert time | pass |
| A6 | Read-only invariants | `grep -rn -i -E "\.post\(|\.put\(|\.delete\(|\.patch\(|portfolio|/orders|authorization|api[_-]?key|KALSHI_|secret|private_key|nba\.duckdb|read_only|duckdb\.connect|import (requests|urllib|socket|httpx)|from (requests|urllib|httpx)" nba/markets/fastarb ops/launchd/local.nba.fastarb.plist configs/fastarb.yaml` | package | only comments and the read-only connect, exit 0 | pass |
| A7 | Rate limit | `Fetcher` (`nba/live/http.py`) plus the builder's dry run (140 requests / 15 s) | 6 games | 10 req/s cap; 15 games is about 345 book requests, about 35-40 s of the 50 s tick deadline | pass, with M1 / m2 |
| A8 | Underdog has no network | `test_underdog_module_has_no_network_imports` plus reading the imports | 1 module | none | pass |

The six standard checks: (1) shuffled labels, (2) planted future (except as the A5 known-at reading), (3) missingness
and (6) integer support do not apply, because this is no model and no outcome was scored. (4) Knockout applies only in the
sense of A3. (5) Replication: the "no arb" null is one evening, 5 ticks x 6 games = 30 game-ticks. It is descriptive,
with no CI because nothing is sampled. It does not cover news windows, which the builder states.

## Findings, severity-ranked (each with line and fix)

### Blockers (before install, and before merge if the plist ships as installable)

**B1. Unknown depth turns the depth gate off, and a stale event quote can be an alert leg.**
`nba/markets/fastarb/arb.py:131-137`: `if r.depth_known and r.max_n < min_depth` skips the gate when either leg lacks
levels. `arb.py:45-46` treats an unknown leg as infinitely deep. `venues.py:238-240` falls back to the event `quote`,
which `runner.py:173-175` refreshes only every `meta_ttl_s` = 300 s.
*Fix:* in `is_alertable`, return a new verdict `unknown_depth` (counted, never emitted) unless
`r.yes_ask.depth_known() and r.no_ask.depth_known()`. In `find_arbs` / `_game_books`, use Polymarket US event-quote
prices for candidate selection only, never as an alert leg. Add a test that sends every `/book` to `None`, plants a Kalshi
move and asserts no ALERTS.md line.

**B2. Legs are not simultaneous, and nothing confirms a gap before the alert.** `runner.py:304` fetches all Kalshi
markets once at the start of the tick. `runner.py:216-219` then fetches the Polymarket books game by game. Tonight a leg
can be about 15 s older than the other; on a 15-game night about 40 s. Around news, which is where the builder expects
gaps, that skew alone produces phantom arbitrage.
*Fix:* before `sink.emit`, re-fetch the two legs back to back (Kalshi `GET /markets/{ticker}` or `/markets/{ticker}/orderbook`,
then Polymarket US `/v1/markets/{slug}/book`; 2 requests). Recompute `evaluate_pair` and emit only if it is still
`alert`. Put both venue timestamps (Kalshi `updated_time`, Polymarket `transactTime`) and their difference in the alert
text, and refuse if they differ by more than 5 s. Record every candidate and its confirm result in a JSONL file
(`data/raw/fastarb_<date>/candidates.jsonl`) so the DECISIONS reversal condition ("> 5 false alerts a night") can be
measured. As built it cannot be.

**B3. A Pinnacle error aborts the tick.** `runner.py:359` calls `self._pinnacle(...)` with no guard, and `pinnacle.py:37`
(`LiveClient._request`) raises `CreditBudgetExceeded`, `AuthError`, `RequestRejected` (for example a 422 once an event
goes live) or `KeyMissingError`. Games run in tip order (`runner.py:322`), so every game after the first loses its arb
check, every tick, for the rest of the night once the shared ledger is spent (live capture and fastarb share the 72,000 cap).
*Fix:* wrap the `_pinnacle` call in `try/except Exception`, set `stat.pinnacle = f"error:{type(exc).__name__}"`,
count `rep.skipped["pinnacle:error"]`, and on `CreditBudgetExceeded` or `AuthError` set `self.pc = None` for the rest of
the run. Also wrap the per-game body (`runner.py:323-378`) so one game's exception cannot cancel the others. Add a test
with a raising fake client and two games.

**B4. A failed first tick ends the poller's day and reports it as a normal empty slate.** `runner.py:148-151` calls
`read_slate`, and `nba/odds/live_close.py:358` opens DuckDB outside its `try`. A collision with the 5-minute lineups
writer raises `duckdb.IOException`. Then `runner.py:408-410` returns 0 with heartbeat `no_games` after one tick, and
a Kalshi 5xx at 10:30 does the same. launchd starts the job once a day, so the night is lost silently.
*Fix:* in `_slate_tips`, catch `duckdb.Error` and keep the previous cache (count `news:tips_unavailable`). In `run`,
decide `no_games` only after a tick whose three Kalshi series all returned a body. Otherwise write
`"status": "error"` and keep polling until a cut-off (for example 21:00 ET) before giving up with rc 1, so the watchdog
sees a failure rather than a quiet day.

**B5. Settlement differences are not in the alert.** Kalshi says "close and expire after a winner is declared", with
`latest_expiration_time` 48 h after the scheduled time (live raw 10-25, all three series). The Polymarket US postponement,
cancellation and overtime rules were not read, and `us_filter_event` (`venues.py:142-177`) drops any rules or description
fields from what it archives. A postponed or cancelled game can settle the two legs differently. The pair is then not
risk-free, and the alert text (`sinks.py:87-94`) gives no hint of that.
*Fix:* add ` | RESOLUTION RISK: postponement/OT rules differ by venue, not verified` to `arb_text` until Devin has read
both rulebooks and `configs/fastarb.yaml` carries `settlement_rules_verified: true` with sources. Once a day, archive
the Polymarket US market rules text and the Kalshi series contract terms (`GET /series/{ticker}`) in the raw directory.

### Major

- **M1. `price_band` does not do what its config says.** `configs/fastarb.yaml:45` reads "ignore lines whose Kalshi mid
  is outside this band", but `runner.py:202-213` only skips fetching books, and it uses the Kalshi YES ask, not the mid.
  Out-of-band lines still alert from event quotes (A2). *Fix:* apply the band, computed from the Kalshi mid, inside
  `find_arbs` / `find_lags`, or rewrite the comment. B1's fix removes the false-alert half either way.
- **M2. The lag check also prices from event quotes and has no band.** `arb.py:154-177` uses `ask.price` with no
  depth or freshness check, and proportional devig overstates longshots. *Fix:* the same "booked levels only" rule as
  B1, plus the band.
- **M3. No in-play guard.** `us_quotes` (`venues.py:180-203`) ignores the event's `live` and `ended` flags, which
  `us_filter_event` keeps. If `game_tips` is late or wrong, the poller polls in play, where a 15-40 s leg skew is
  enormous. *Fix:* skip the event (count `polymarket_us:live`) when `live` or `ended` is true, and skip Kalshi markets
  whose `occurrence_datetime` or `expected_expiration_time` implies the game has started.
- **M4. The watchdog cannot see this job.** The heartbeat (`sinks.py:113-114`, `runner.py:412-423`) lacks the
  watchdog's `end_ts` / `rc` / `mode` / `host` contract, and the job is not in `nba/ops/watchdog.py:JOBS`. Alerts land in
  ALERTS.md with no push, and a 60-second gap is useless an hour later. *Fix (with the install, Devin's call):* add a
  `JobSpec("fastarb", optional=True, ...)` and the flat keys. State in the builder review that alerts are not delivered
  anywhere yet.

### Minor

- m1. `runner.py:389-428`: if no game ever gets a tip (lineups tips missing and every Polymarket US event missing), `run`
  never exits, and the next day's launch exits 0 on the held `flock`. *Fix:* hard stop at 06:00 ET the next day.
- m2. `nba/live/http.py:89-91`: 429 back-off ignores `Retry-After` and has no global cool-down, and every retry eats the
  tick deadline, which feeds B1. *Fix:* honour `Retry-After`, and after any 429 halve the rate for the rest of the tick.
- m3. Pinnacle is called every tick for every active game (`runner.py:359`), even with no news in the window. *Fix:* call it
  only when `recent` is non-empty. That saves most of the ~540 credits per game, and the gate is open about a third of the
  time anyway, because injury PDFs arrive every ~30 min (8 files on 10-25 by 15:50 ET).
- m4. `runner.py:255` writes the Pinnacle body through `RawWriter` without the key-in-blob refusal that
  `live_close.write_raw` has (`nba/odds/live_close.py:595-599`). *Fix:* in `_pinnacle`, raise if `self.pc.key_text()` appears
  in `json.dumps(body)`.
- m5. The plist's `--env-file .env` loads every variable in `.env` into the poller. By name that includes `X_BEARER_TOKEN`
  and `ODDS_API_KEY`; values were not read. The code never uses them, but rule 5 is cleaner with a fastarb-only env file.
  *Fix:* `--env-file .env.fastarb` holding `THE_ODDS_API_KEY` only.
- m6. `arb.py:73`: the fee is evaluated at the last-fill price for a multi-level walk, not per level. The effect is under
  0.1c at 20 contracts. Note it in the docstring.
- m7. The `docs/DAILY_PIPELINE.md` "dry-run writes nothing" claim is wrong with `--with-pinnacle`, which appends to the credit
  ledger (correctly). Reword it.
- m8. Layering test (`tests/markets/fastarb/test_layering_fastarb.py`): the banned-token and verb scans cover the package
  only, not its import closure (`nba.odds.live_close`, `nba.live.http`). Today those are GET-only. *Fix:* extend
  `test_only_get_requests...` to every module in `closure()` under `nba/`.

### Scope

The diff stays inside its declared paths. One deviation goes to Devin: the approved DECISIONS row (2026-10-25 14:30) names
"Polymarket public gamma/CLOB API", but the build alerts on a different venue, Polymarket US (QCX), for a sound reason
(polymarket.com blocks US trading). The builder lists it as open for Devin. It must be confirmed before install.

## Playbook check

Model class: **none (market-structure and alerting tool; no fitted model)**. The closest class in
`docs/MODEL_PLAYBOOK.md` is none. The cross-cutting questions still apply:

1. **Information or capacity?** Speed of information only (cross-venue prices, news timing). No capacity.
2. **Enough data for the class?** Not applicable to fitting. The evidence base is one evening (30 game-ticks), enough
   only to say there were no standing pre-tip gaps at that hour.
3. **Test valid?** The null has a positive control (`test_tick_alerts_once_with_exact_buys_then_dedups` plants a 20c
   gap and it alerts) and an "arm saw it" count (`n_props_both_venues`), so it meets BEST_PRACTICES 2026-10-21. The
   alert path has no negative control for stale or unknown-depth legs (B1) and no confirm step (B2), so positives are not
   yet valid.
4. **Lineage and inherited guards.** Inherits `Fetcher` (GET-only, rate, retries), `LiveClient` (ledger, key scrubbing),
   the verified Kalshi fee object and `read_slate`. It drops `write_raw`'s key-in-blob refusal (m4) and does not inherit
   `connect_lineups`' lock retry (B4).
5. **Scored against?** Nothing. Alerts are not logged in a scoreable form, so "false alerts per night" (the DECISIONS
   reversal condition) is unmeasurable until B2's candidate log exists.

## Corrected family and power note

No inferential family was run. There are no p-values, so BH does not apply. Power: one evening, no news window. "No
arbitrage" means "none at this hour on these 6 games", not "none". The smallest gap the build can see is a quote that
lives longer than one 60 s tick.

## The one biggest risk

A confident, exact two-leg "ARB ... BUY X @a + BUY Y @b, net +3c" line built on a 1-5-minute-old or unconfirmed leg.
JARVIS's always-a-play override (DECISIONS 2026-10-25 14:41) raises the stakes: Devin acts on a phantom and ends up with a
one-legged position, or a "risk-free" pair that a postponement settles differently.

## Not verified

- The Polymarket US fee page and its rounding rule (no network). The builder's reading plus the live `feeCoefficient` is
  the only evidence.
- Polymarket US and Kalshi postponement, cancellation and overtime rule texts (not in any payload I hold).
- The Polymarket US participant agreement and rulebook on automated reads (the builder could not read the PDFs either).
- Live behaviour of the lag check against a real Pinnacle body (no key in the agent shell; synthetic body only).
- Real 429 behaviour of gateway.polymarket.us.

## Recommended changes (not applied by me)

B1-B5 before install, each with a regression test as described. M1-M4 before install. m1-m8 when convenient.

## Drafted rows (for the main session; not appended)

DECISIONS (draft):
`| 2026-10-25 <time> | Adversary review of agent/market-fast-arb (7ce3be0): CHANGES_REQUIRED. Mapping, fees, read-only invariants, lag known-at and Underdog all hold; alerting blocked on 5 findings (unknown-depth/stale-leg alerts, no confirm fetch, Pinnacle error aborts the tick, first-tick failure ends the day as "no_games", no settlement-risk disclaimer). docs/reviews/fastarb_adversary_2026-10-25.md. | Adversary finding; merge/install are Devin's. | The five blockers fixed with regression tests and a re-review. |`

TEST_LEDGER: none (no test or outcome was run).
