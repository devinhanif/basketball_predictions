# Adversary review: 15 s candles poller trade catch-up (agent/market-candles-trades-carry 369fdc8)

Date: 2026-11-10. Reviewer: adversary. Scope: diff `9d5cbe1..369fdc8`, which touches
`nba/kalshi/poll15.py` (+189/-45), `configs/kalshi_candles.yaml`, `docs/KALSHI_CANDLES_15S.md`,
`docs/DECISIONS.md` (one row) and `tests/kalshi/test_trades_carry.py`. No live requests, no launchd,
no season 2025. Attacks reuse the branch's own `Feed`/`FakeClient` in the scratchpad.

**Grade: WOUNDED.** It is the same carried-cursor design as the capture fix (23d477b), and quote polls
are never starved. It has the same false-completion hole, plus a fallback that loses an open catch-up
silently, and the reconciliation does not attribute that loss.

Verdict: BLOCKED

Unblock with C1 to C3. C4 to C6 are recommended.

## Top finding

**C2 (major): if the state file is missing mid catch-up, the open catch-up is dropped silently.**
`restore()` then sets the mark to the newest stored print (the `if not self._load_trade_state():`
block). Pages are read newest first, so that print sits at the top of the open walk. Everything
between it and the real old mark is never requested. Attack C: 1,000 prints, 600 stored with a cursor
open, then `trade_state.json` deleted and the process restarted. Result: **600/1,000 stored, no
errors.log line.** A missing file (OSError) returns False without logging. Only a parse error logs.

**Does the reconciliation show the loss?** Only as an unattributed drop in `sources.own.minute_coverage`
and `volume_coverage` for the minutes involved. The union (`own ∪ capture`) can hide it completely
when the capture holds those prints. Nothing names the ticker or the cause.

## Per-check table

| # | Check | Command / evidence | n | Result |
|---|---|---|---|---|
| 1a | min_ts inclusive | shared with the capture review, check 1a (514 prints at ts == mark, 0 below, 86,224 pulls) | 86,224 | PASS. Here dedupe uses every stored id (`seen_ids`), so the mark second is safe either way. |
| 1b | Expired cursor answering 200 empty | `scratchpad/ctf/attack2.py` A (exit 0) | 900 | **FAIL (C1)**: 300/900 stored, queue cleared, mark moved |
| 1c | New prints during a carried catch-up, keyset cursor | branch tests `test_a_burst...`, `test_the_verify_pass...` | 2,057 / 453 | PASS |
| 1d | Same, offset cursor | `scratchpad/ctf/attack2b.py` (exit 0) | 1,350 prints | No loss, but **in-walk duplicates (C4)**: 1,750 raw rows for 1,250 unique ids. `new` is checked against `seen` only, and `seen` is not updated during the walk. |
| 2 | Quote polls never starved | `RequestBudget.take`: `len + reserve >= per_minute` refuses; trades use reserve 10 | n/a | PASS. Trades can use at most 50 of 60 per sliding minute, and quotes need about 6 plus retries. The branch test `test_trade_pages_never_starve_the_quote_polls` freezes the window and confirms all 10 quote polls went out. 12 pages × 0.5 s spacing is about 6 s per 15 s poll. |
| 3a | Crash between raw write and state write | `pull_trades` `finally`: write, `_remember_trades`, then `_save_trade_state` | n/a | PASS: re-read, deduped against `seen_ids` rebuilt from the store |
| 3b | Raw write raises after a complete walk | attack D | 255 | **FAIL (C3)**: 10/255 stored. `_pull_ticker_trades` moves `last_trade_ts` before the write in `finally`, and the next save persists that mark. |
| 3c | Missing or corrupt state mid catch-up | attack C plus the branch's corrupt-file test | 1,000 | **FAIL (C2)**: 600/1,000 stored, silent on a missing file. The branch test only covers a corrupt file after a *complete* catch-up. |
| 3d | State from another day | `day` check, `store.day_dir` per day, launcher day = ET(now − 6 h) | n/a | PASS |
| 4 | 429 and the shared IP | `_fail` opens a global cool-down (15 to 240 s) | n/a | PASS with a note (C5): a 429 on a trade page also pauses the quote polls. That is pre-existing (old code also called `_fail`). The old per-poll ceiling was 16 trade pages (8×2) and the new one is 12, so exposure does not grow. In the capture's errors.log, 429s appear only on `/markets/trades`. |
| 5 | Memory and disk | `seen_ids` holds every stored id for the night (pre-existing); the state file stores no id lists | n/a | PASS. The state file is small (marks, cursors, queue). |
| 6 | Other readers | `candles15.minute_rollup` runs `unique(trade_id)`; `RawStore.load` dedupes; `all_trades` dedupes | n/a | PASS for candles and reconciliation. `st.trade_rows` and `own_trades()` row counts are inflated by C4. |
| 7 | Fake feed realism and documentation | `Feed` docstring: newest first, keyset cursor, inclusive min_ts | n/a | PARTIAL: documented, but only a keyset cursor is modelled. No offset-cursor test and no 200-empty expiry test. `client.fetch_trades_page` says "order not specified by the docs; observed newest first". |
| V | Tests and lint | `PYTHONPATH=$PWD .venv-1/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/kalshi tests/live`; `ruff check nba/kalshi tests/kalshi`; `ruff format --check ...`; `mypy nba/kalshi` | 178 tests | 178 passed, exit 0. ruff 0, format 0, mypy 0. |

## Findings (severity, line, fix)

- **C1 major: false completion** (`_pull_ticker_trades`, the
  `if not nxt: complete = True; cursor = None; break` block). Fix: before setting `complete = True`
  after a resumed request:
  - A resumed page with 0 trades and no cursor is treated as stale: drop the cursor, restart from
    the newest page, dedupe.
  - When `hwm is not None`, require that the walk re-saw at least one stored id with ts == hwm.
    Inclusive min_ts guarantees one exists if any print was stored in that second.
  - Otherwise log `walk ended without reaching the mark` and keep the ticker open.
  - Test: the attack-A fake.
- **C2 major: the fallback loses an open catch-up** (the `restore()` fallback that sets
  `last_trade_ts` to the newest stored print). Fix:
  - On fallback, leave `last_trade_ts` unset, so `min_ts = window_start`, and put every stored
    ticker in `pending`. The re-walk is deduped by `seen_ids`, and with the carried cursor it now
    costs pages, not prints. Expect about (night prints / 1,000) pages per ticker, spread over polls.
  - Log `trade_state missing/unreadable: re-walking N tickers from window start` on a missing file
    too, not only on a parse error.
  - Have `finalize` report `trade_state_fallback: true` in the reconciliation JSON, so a coverage
    drop has a named cause.
  - Test: delete the state mid catch-up, and 1,000/1,000 must be stored.
- **C3 major (low likelihood): mark moves before the write** (`_pull_ticker_trades` sets
  `st.last_trade_ts` and pops the cursor; the write comes later in `pull_trades`' `finally`). Fix:
  return the completion (`top`, cursor pop) to `pull_trades` and apply it only after `store.write`
  succeeds. On a write failure, apply nothing.
- **C4 minor: in-walk duplicates under an offset cursor.** Fix: `seen_walk = set(seen)` and add each
  new id as it is appended. This removes the raw duplicates and the `trade_rows` inflation.
- **C5 minor, pre-existing: a 429 on a trade page cools down the quote polls too.** Give trades their
  own cool-down (`trades_cooldown_until`), as the capture does.
- **C6 minor (docs): the restart text is too strong.** `docs/KALSHI_CANDLES_15S.md` says a restart
  "resumes mid catch-up" and the fallback uses "the newest stored print". It should say the fallback
  loses an open catch-up, or describe the C2 fix once it is applied.

## The single most likely failure mode if the claim is wrong

A restart with no readable state (an older version's store, a disk problem, or a manual cleanup)
during a big in-game burst. The poller resumes above the gap, and the only trace is a lower own-source
coverage number that the union with the capture can mask (C2). The expired-cursor case (C1) is next.

## Power / family note

This is not a statistical claim; there is no family. The attack counts are exact.

## Playbook check

Model class: none (data-capture infrastructure).
- Information or capacity: information (print completeness for display candles; "no alert, EV or
  signal path reads it").
- Enough data: n/a.
- Test validity: limited by the keyset-only feed (C1, C4).
- Lineage: the same design as capture 23d477b, so it inherits that branch's F1 and F2 one-for-one
  (C1, C3).
- Scored against: Kalshi official 1-minute candles (reconciliation).

## Not verified

Real cursor semantics. Combined trades-endpoint load from both processes on one IP during a 429
storm. Behaviour on a 15-game night.

## Recommended changes (never applied by me)

C1 to C3 before merge. C4 to C6 with them if cheap. Fix both branches' false-completion guard the same
way, and share a test.

## Drafted DECISIONS row (not appended)

| 2026-11-10 | Candles poller trade catch-up (369fdc8) held for C1 (completion requires evidence of reaching the mark), C2 (state fallback re-walks from window start and logs, never trusts the newest stored print) and C3 (mark applied only after the raw write). | Adversary WOUNDED (docs/reviews/candles_trades_carry_2026-11-10.md). | Kalshi documents its cursor contract, or a week of nights shows no fallback and no unreached-mark walk. |
