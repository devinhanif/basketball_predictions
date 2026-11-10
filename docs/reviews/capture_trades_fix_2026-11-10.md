# Adversary review: live capture trade catch-up (agent/market-capture-trades-fix 23d477b)

Date: 2026-11-10. Reviewer: adversary. Scope: `nba/live/capture.py`, `nba/live/kalshi_pull.py`,
`tests/live/test_trades_catchup.py` (diff `main...23d477b`, +512/-60). No live Kalshi request, no
replay, no season 2025, no touch of the running capture or launchd. Attacks ran on fake servers in the
scratchpad; the empirical checks read the stored raw trees of 2026-11-07 to 11-09 read-only.

**Grade: WOUNDED.** The fix is real: it removes the 5-page cap that dropped older prints, and every
attack on the ordinary paths came out clean. Two edge cases can still lose prints silently, and the
real lever (trades getting no pages at all while games are live) is untouched.

Verdict: BLOCKED

Unblock with F1 and F2 (both small, each with a test). F3 to F8 are recommended, not required.

## Top finding

**F1 (major): an expired cursor that comes back as 200 with an empty page counts as a finished
catch-up.** The mark then moves to `open_max`, and every print between the cursor and the old mark is
skipped for good with no log line. Attack A: 3,000 prints. One page was read and the cursor carried.
The resumed cursor then returned `{"trades": [], "cursor": ""}`. Result: 1,000 of 3,000 stored, mark
set, queue empty. The branch treats a stale cursor as stale only when it gets a 400 or 404
(`capture.py` `_pull_trades`, the `if resumed and status in (400, 404)` line). Kalshi's cursor is
opaque and its expiry behaviour is not documented. On 2026-11-09 trades got no pages from about 01:00
to 03:30Z, so a carried cursor can be hours old when it is resumed.

## Per-check table

| # | Check | Command / evidence | n | Result |
|---|---|---|---|---|
| 1a | min_ts inclusive at the second | `python3 -I scratchpad/ctf/mints.py 2026-11-07 2026-11-08 2026-11-09` (exit 0) | 86,224 pulls that had a prior mark, 1,232 files | **PASS (empirical)**: 514 prints in later pulls had ts == the earlier mark, 213 pulls had at least one. An exclusive filter could not return those. 0 prints had ts < mark, so the server applies the filter. The branch's inclusive assumption holds. |
| 1b | Ids at the mark second, late prints in that second | attack E (`scratchpad/ctf/attack1.py`, exit 0) | 290 prints | PASS: 290/290, 0 duplicates |
| 1c | New prints at the head during a carried catch-up, offset cursor, arrivals > offset | attack D | 7,000 | PASS: 7,000/7,000, 0 duplicates, queue cleared |
| 1d | Same with a keyset cursor | attack C | 6,500 | PASS: 6,500/6,500 |
| 1e | Verify pass after a carried catch-up | code read of `kalshi_trades` / `_absorb_trades` plus the tests | n/a | PASS. Gap: if a carried walk's first pull saves no new ids, `trade_open` is never created, `carried` is False and the verify pass is skipped. This needs at least one full page of ids already seen at the mark second. Negligible. |
| 1f | Expired cursor answering 200 empty | attack A | 3,000 | **FAIL**: 1,000/3,000 stored, mark moved (F1) |
| 2 | Can trades starve books, ESPN or markets? | code order in `tick()`: scoreboard, summaries, markets, books, **then** trades with `room = tick_budget - tick_requests - 12` | n/a | PASS: trades only get what is left over. |
| 2b | The reverse: books and markets starve trades | `data/raw/live_2026-11-09/ticks.jsonl` (401 ticks, 8 games) | 401 ticks | **Finding (F3)**: requests hit the 300 cap every tick. From about 01:00 to 03:30Z trades fetched **0 pages** (`tr n 0`, 150/150 incomplete) and book skips peaked at 159. The backlog peaked at 4,768 tickers and was **71 at window end**. The 12.7% loss was this delay plus the 5-page cap. The branch fixes the cap, not the delay. |
| 3a | Crash between raw write and state write | code: `store.save` then `_save_trade_state` | n/a | PASS: an old state reloads, the walk re-reads, readers dedupe on trade_id (`candles15.CaptureTrades.frame`, `poll15.all_trades`) |
| 3b | Raw write raises after absorb | attack B: first `store.save` raises OSError | 1,510 | **FAIL (F2)**: 10/1,510 stored. `_absorb_trades` moves the in-memory mark before `store.save`, and the next tick persists that mark. |
| 3c | Corrupt or partial state | `_load_trade_state` except branch, `write_json_atomic` (tmp + replace, no fsync) | n/a | PASS: a parse error clears everything and the night is re-walked from first sight. That costs duplicates, not losses. All volumes are first-sight after a restart, so every slate ticker with volume is queued again. |
| 3d | State from a previous night | out dir is `live_<ET day>` (`nba/live/__main__.py:65-66`) plus the `day` check | n/a | PASS: double-guarded. A restart after ET midnight starts a new folder and day (pre-existing, outside this diff). |
| 4 | 429 storms, shared IP | `errors.log`, 17 nights | 458 trade 429s (218 on 10-22) | PASS with notes: 429s appear on `/markets/trades` only. One final 429 (after the Fetcher's 3 attempts) stops trade paging for the tick. The skips are 0/1/3/3. Books and markets have no backoff (pre-existing). Retries count as requests but not as pages, so trades can eat the 12-request probe reserve (F6). |
| 5 | Memory and disk growth | code read | n/a | Minor (F5): `trade_open` keeps every id of an open catch-up (no cap) and the whole thing is rewritten to `trade_state.json` each tick. A 100k-print catch-up is about 5 MB a minute. Ids at the mark second are bounded by prints per second. |
| 6 | Other readers or writers | `grep -rnE "kalshi_trades|trades_max_pages|iter_trade_pages|trade_state|trades_page_budget"` | 3 modules | PASS: `poll15.py` and `candles15.py` read the store and dedupe by trade_id. `poll15.trades_max_pages` is its own config (reviewed separately). No caller sets `Budget(trades_max_pages=...)`. Late carried prints get a later `fetched_at` (the file stamp), which is correct as-of. |
| 7 | Fake server realism and documentation | `test_trades_catchup.py` docstring | n/a | PARTIAL: the module docstring states the assumptions (newest first, inclusive min_ts, offset cursor, 400 on a bad cursor). Inclusive min_ts is now backed by evidence (1a). Expired-cursor-as-200-empty and keyset cursors are not modelled (F1, F7). |
| V | Tests and lint | `PYTHONPATH=$PWD .venv-1/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/live tests/kalshi`; `ruff check nba/live tests/live`; `ruff format --check ...`; `mypy nba/live` | 182 tests | 182 passed, exit 0. ruff 0, format 0, mypy 0. (With coverage on, a subset run exits 1 on the 70% floor only.) |

## Findings (severity, line, fix)

- **F1 major: false completion on an empty resumed page** (`capture.py::_pull_trades`, the
  `if not nxt: return TradePull(out, True, None, pages)` line). Fix: before declaring the walk
  complete, require evidence that it reached the mark:
  - When `hwm is not None`, at least one id from `st.trade_ids[ticker]` must have been seen again in
    this walk. Inclusive min_ts guarantees the mark-second prints come back, so this check also
    re-tests 1a every night.
  - When a resumed request returns 0 trades and no cursor, treat it as the 400/404 case: restart from
    the newest page and dedupe on trade_id.
  - If neither holds, log `trades <t> walk ended without reaching the mark` and keep the ticker open.
  - Test: the fake server returns 200 empty for an old cursor, and all 3,000 prints must be stored.
- **F2 major (low likelihood): state moves before the raw write** (`kalshi_trades`: `_absorb_trades`
  runs in the result loop, `self.store.save` after it). Fix: build `got` first, call `store.save`,
  then absorb. If save raises, absorb nothing, so the next tick re-reads. Test: the first save raises
  and all prints are stored afterwards.
- **F3 major, pre-existing, not addressed: trades starve while games are live, and nothing drains the
  backlog at window end.** 2026-11-09: 0 trade pages for about 2.5 h, 71 tickers still queued at the
  end. `sweep("end")` does not pull trades, and the next night's folder does not load this night's
  state. Fix, either:
  - drain trades in `run()` after the loop, under the sweep budget (900 requests / 420 s), until the
    queue is empty or the budget is spent, logging any remainder; or
  - give trades a guaranteed floor per tick, e.g. 30 pages before the books past the first N.
  The drain is the cheap one.
- **F4 minor: a stale cursor can stick.** If the restart page after a 400 is refused for budget,
  `pull.cursor` is None and `_absorb_trades` keeps the old stale cursor. Each tick then wastes one
  request on it. Fix: `st.trade_cursor.pop(t, None)` when the pull's cursor is None.
- **F5 minor: open-id set has no bound.** Keep only the ids at the newest second plus the last page's
  ids for dedupe, or write the state file only when it changed.
- **F6 minor: retries are not counted against `_pages_left`.** A 3-attempt page costs 3 requests and
  1 page, so the 12-request probe reserve can be eaten. Fix: compute `_take_page` against
  `fetch.tick_budget - fetch.tick_requests - reserve` live.
- **F7 minor (tests): no test covers a keyset cursor or a 200-empty expired cursor.** Add both;
  attacks C and A are ready-made.
- **F8 minor: player double-double and triple-double series are ranked with the rest.**
  `PROP_PREFIXES` omits `KXNBA2D` and `KXNBA3D`, so those player markets rank as class 2 instead of
  props.

## The single most likely failure mode if the claim is wrong

Kalshi expires a cursor carried over several minutes or through a starved stretch, and answers 200
with an empty page. The ticker is marked complete, and that night's middle prints are missing with no
log line (F1).

## Power / family note

This is not a statistical claim; no family or multiplicity applies. Counts are exact on fake servers.
The empirical inclusivity check (1a) covers 3 nights and 86,224 pulls. It cannot establish ordering
or late-publication behaviour (a print published after a newer print was seen): that is not
verified, and it is shared with poll15 (review kalshi_candles15_2026-11-09, check 6c).

## Playbook check

Model class: none. This is data-capture infrastructure, not a forecasting model; the playbook's class
checks do not apply.
- Information or capacity: information. Completeness of the print store feeds candles and any future
  flow features.
- Enough data: n/a.
- Test validity: fake-server tests are valid only as far as their pagination model matches Kalshi.
  Inclusive min_ts is now evidenced; cursor expiry is not (F1, F7).
- Lineage and inherited guards: inherits trade_id dedupe in both readers, the write-once raw store,
  and the per-tick request budget.
- Scored against: Kalshi official 1-minute candles, through poll15's reconciliation (KXNBAGAME only).

## Not verified

Real cursor type (offset or keyset) and expiry behaviour. Whether `/markets/trades` has its own rate
limit. A 15-game night (projected from 8 games: the markets fetch alone grows, so trade starvation
gets worse). The replay oracle, which is the main session's job.

## Recommended changes (never applied by me)

F1 and F2 before merge. F3 as the next live-path task: an end-of-window drain plus a ticks.jsonl
field for the backlog at end. F4 to F8 when convenient.

## Drafted DECISIONS row (not appended)

| 2026-11-10 | Live capture trade catch-up (23d477b) held for F1 (completion requires evidence of reaching the mark; an empty resumed page restarts) and F2 (absorb only after the raw write); end-of-window trade drain queued as the next live-path task (F3). | Adversary WOUNDED (docs/reviews/capture_trades_fix_2026-11-10.md). | A documented Kalshi cursor contract that excludes 200-empty expiry, or a night showing zero `walk ended without reaching the mark` lines over a week. |
