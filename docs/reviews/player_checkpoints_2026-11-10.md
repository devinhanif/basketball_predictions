# Adversary review: JARVIS player checkpoints (T-180 / T-30 / T-5), 2026-11-10

Branch `agent/market-player-checkpoints` at f8dcd5a3, against `main` (c37bd6d2). Live-path change (JARVIS `watch --once`,
launchd every 120 s, main checkout). The review was a fast code pass plus targeted attack tests, not a six-check statistical
attack: the branch claims no win; it is delivery and formatting. Reviewed in worktree branch
`agent/adversary-player-checkpoints-review` (= f8dcd5a3; nothing committed).

Verdict: WOUNDED

Do not merge for tonight until M1 and M2 below are fixed. Each fix is about five lines. The rest of the pass is sound:
contained exceptions, shared budgets, silent flag, no edits, the same pricing code, and read-only connections.

## Must-fix before merge

**M1 (major): the window decision uses the tick-start clock, so a T-5 message can post after tip.**
`runner._tick` captures `now = rt.clock()` at tick start. The card pass (`_cards`) runs LAST, after alerts, halftime,
in-game checkpoints and in-game signals. `run_player_checkpoints` decides `due` from that stale `now`
(players.py:639-655) and never reads the clock again before `client.post` (players.py:719). The T-5 window is
[T-5, T-2], so a tick that starts at T-2 and reaches the card pass more than 2 minutes later posts after the real tip.
Ticks of that length happen. On 11-09 22:00Z to 11-10 03:59Z the launchd start-to-start gap was median 123 s, but 5 of
162 gaps were at least 184 s (184, 192, 209, 306 and 745 s), so some ticks ran past 3 minutes during game hours. The
scoreboard refusal does not cover this, because ESPN flips status at the actual tip, usually minutes after the scheduled
one.
- Reproduced: scratch test `test_attack_stale_tick_clock_posts_after_tip` (tick at T-2, real clock T+1). Result:
  `{'posted': 2, 'skipped_late': 4}`, so both team messages went out after tip.
- Smallest fix, in `_post_checkpoint` immediately before `res = client.post(...)` (players.py:719):
  ```python
  real = rt.clock()
  if window_state(real, g.tipoff, minute, pc.tolerance[minute], pc.after_tip_guard_min, False) != "due":
      counts["skipped_late"] += 1
      continue  # the next tick records it as skipped_late; never posted late
  ```
  Tests keep a fixed `rt.clock`, so the existing suite is unchanged. Add the attack test above as a negative test.

**M2 (major): `player_availability.as_of` is naive US-Eastern, and `read_store` treats it as naive UTC.**
This convention is documented at nba/explain/data.py:8 and :57 and in nba/daily/late_report.py:141-158 (`to_report_dt`).
I confirmed it on the live DB: the newest snapshot is `as_of = pulled_at = 2026-11-10 12:15` at 17:22Z, which is
12:22 ET. The branch has two consequences:
1. Every posted message gets the injury-report time wrong by 5 h (4 h after DST). The preview built at 12:20 PM ET reads
   "injury report as of 7:15 AM ET" for the 12:15 PM ET report, because `T.clock` converts naive input as UTC. This is
   wrong posted text on every message.
2. Known-at bound (replay and preview `--at` only): `as_of <= now` admits reports stamped up to 5 h after the
   checkpoint. `--at 2026-11-10T13:00:00` (8:00 AM ET) for MIL@NYK used the 12:15 ET report, and 9 snapshots after
   08:00 ET were admitted (the correct newest is 07:00 ET). The live tick is not affected: no future row exists yet.
- The test fixture follows the wrong convention. `seed()` in tests/jarvis/cards/test_players.py stamps the OUT row
  "23:00Z", so `test_known_at_a_checkpoint_uses_only_rows_made_before_it` passes on a convention production does not
  use. An invalid test is not a pass.
- Smallest fix, players.py:266-270: bind `to_report_dt(now)` (from `nba.daily.injury`) instead of `now` in the
  `player_availability` query. At players.py:290-291, convert `as_of` to naive UTC before storing it
  (`as_of.replace(tzinfo=ET).astimezone(UTC).replace(tzinfo=None)`). In the test seed, stamp the availability row in
  ET (`datetime(2026,10,28,19,0)` for 23:00Z) and assert that a report stamped after the checkpoint in ET is excluded.

## Should-fix (not blocking tonight)

- **S1 Duplicate on transport error after Discord accepted (reproduced).** `CardClient._send` maps any
  `httpx.HTTPError` to `deferred` (discord.py:147-149). `_post_checkpoint` records nothing for `deferred`
  (players.py:738-739), so the next tick inside the window posts again. Scratch test
  `test_attack_accepted_then_timeout_duplicates`: the LAL webhook received the T-180 message twice. Impact is cosmetic,
  a duplicate silent message, and the risk is rare. Cheap fix: at-most-once for uncertain sends. In the `elif` at
  players.py:738, if `res.note` starts with `"transport"` or `(res.code or 0) >= 500`, write the `posted` record with
  `message_id=""` and the snapshot, and count it as `lost_id`. A 429 or a gate stop sent nothing and stays a deferral.
  Recording intent before the post would be the alternative, but it costs a lost message on every failed attempt.
  At-most-once on uncertain responses is the better trade for informational messages.
- **S2 `state.write` after a successful post sits outside any try (players.py:736).** An OSError there aborts the rest
  of the pass; the wrapper in run.py catches it. Because launchd starts a fresh process every tick, the same message is
  then reposted on each tick in the window, up to 5 times for T-180. Fix: wrap the write in try/except, log it, and
  count `state_write_failed`. Rare.
- **S3 `open_ro(path, retries=10, wait_s=3.0)` (players.py:258) can block the tick for 30 s per due game** while a
  writer holds nba.duckdb. Every other tick read uses the defaults (4 x 1.5 s). The tick budget then defers the
  remaining games, and the card pass waits. Fix: use the defaults. The pretip writer runs at :20/:50 ET for under
  1 min (pretip_20261109.log), so collisions with the windows are unlikely. This is a latency cap, not a lock hazard.
- **S4 The T-5 window overlaps the tradable cutoff.** `min_minutes_to_tip: 3` (configs/jarvis.yaml:55) drops every
  quote inside T-3. A T-5 message posted on a tick between T-3 and T-2 says "no Kalshi price" for every player when
  prices exist and are only withheld. Fix: either `lateness_tolerance_min` 5 to 2 (validate still passes), or print
  "prices withheld inside 3 min of tip" when `g.tipoff - st.now < min_minutes_to_tip`.
- **S5 Status is the per-player newest row, not the game's newest snapshot.** A player dropped from the latest report
  keeps his older status: 3 of 1,319 player-games since 10-20. This matches `Knowledge.injuries` (knowledge.py:135-143),
  so it is consistent with the rest of JARVIS. Noted, not required.
- **S6 Cosmetic.** Unresolved names render as "player 1642504" (4 of 33 listed in MIL@NYK).

## The seven requested checks

| # | Check | Command / evidence | Result |
|---|---|---|---|
| 1 | Tick safety | Read run.py `_player_checkpoints` wrapper (catches Exception), `_post_checkpoint` per-game and per-team try blocks, runner `_cards` (LAST, wrapped). `load_config` calls `validate()` only when the block is enabled; the shipped block passes (`test_the_shipped_config_is_enabled_and_valid`). | PASS. No path escapes into the tick; alerts, check-ins and in-game posts run before cards. A bad hand edit of the block would fail `load_config` and stop all of JARVIS, the same as existing `team_cards` validation. |
| 1b | Budget sharing | Same `RateGate` (rate 20 s, total 25 s) created in `run_cards`; the checkpoints go first; DB time counts against the deadline. Preview of 6 games x 2 teams took 2.07 s wall including `build_state` (`/usr/bin/time -p ... --phase players --checkpoint 5`, exit 0). | PASS. Cards can defer by one tick when many checkpoints post at once; they are never lost. |
| 2 | Timing | `window_state` boundaries are correct and tested. Naive UTC throughout for tip and now. Scoreboard abbreviations are normalized (`ESPN_ABBR`). Slate is `et_today()`. | **FAIL: M1** (stale clock) and **M2** (ET `as_of`). DST itself is fine: display goes through `ZoneInfo`. |
| 3 | Idempotency | `PcpState` first record wins; `write()` is a no-op on an existing key; a fresh process re-reads the file (tested). | WOUNDED: S1 duplicate on transport error after accept (reproduced); S2. |
| 4 | Known-at | `forward_predictions.made_at` is TIMESTAMP naive UTC, so `made_at <= now` is correct. Lineups are refused when the snapshot is later than `now`. Quotes come from `st` built at tick start. | **FAIL on `player_availability` (M2).** Live content is correct; displayed time and replays are wrong. |
| 5 | EV path | See "Finding 5" below. `priced_lines` calls the same `single_candidate`, `is_capped`, `hold` and `over_from_play`; it only selects. `_WithPPlay` is used only in `gather_game`. `pregame_overs`, `ModelView`, `slate.py` and the alert path are unchanged (`git diff main...HEAD --stat` touches no other module). | PASS. Disclosure: checkpoint EVs are priced on P(plays) x P(over), a path production has never served live. |
| 6 | Discord | `client.post` adds `flags: 4096` and `?wait=true`; `allowed_mentions` is empty. `enforce()` is applied; previews are 2,190 and 2,332 description characters, well under 4,096. No edit call (`test_the_module_never_edits_a_message`). No outcomes are ever added. RAW model % next to a pre-tip price is precedented by the existing cards (text.py:95). | PASS (apart from M1). |
| 7 | Read-only DB | `read_store` uses `open_ro` (`read_only=True`), a short-lived connection, with queries of about 50 ms on the live DB. `kb.lineups` uses `open_ro(copy_ok=True)` (existing). Nothing writes nba.duckdb or the data/jarvis stores except the append-only jsonl. | PASS. A read-only connection takes a shared lock for about 50 ms, which is the same exposure the tick already has (`build_state`, `late_report`). S3 is the only latency concern. |

## Finding 5 (production cards list no overs): CONFIRMED on main

The script ran against the **main checkout's code**: `nba.jarvis.slate.__file__` = /Users/devin/Downloads/nba-prediction/nba/jarvis/slate.py,
HEAD `main`. It used today's live data, read-only:
`.venv-1/bin/python <scratch>/cap_count.py` (exit 0):
- `model type ModelView has p_play False`. `git grep "def p_play"` on main finds no implementation anywhere in `nba/`.
- 955 yes-side prop quotes: 14 `None` and 941 priced. Of the 941, 870 have no Pinnacle reference (NaN edge, never shown).
  All 71 with a Pinnacle reference are capped with the note "prop DNP rule unverified and no P(plays): capped at thin"
  (`kalshi_prop_dnp_rule_verified: false`, configs/jarvis.yaml:60).
- `pregame_overs` returns 0 overs and 0 priced for all 6 games (MIL@NYK, BOS@PHI, LAC@PHX, MIA@UTA, ORL@SAC, OKC@POR).
- The effect is wider than the cards. `classify(..., capped=True)` can never return `strong` (edge.py:78), and
  `alert_labels: [arbitrage, strong]`, so **no Kalshi prop over can alert on main** while the DNP rule is unverified.
  Arbitrage is unaffected. This is a separate production issue for Devin. The fix is a `p_play` method on `ModelView`;
  it is a live-path change with oracle and Devin's go, and it is not part of this branch.

## Playbook check

Model class: none trained here; this is a delivery layer over the production props forecast (`props_context_residual`,
LightGBM context residual plus P(play)) and the existing Pinnacle-reference pricing.
- Information or capacity: neither. No new information enters any decision; the messages display existing forecasts,
  reports, lineups and prices.
- Enough data: n/a, nothing is fit.
- Test validity: the known-at test seeded the wrong time convention (M2), so it could not have caught the bug. The
  timing tests fix `rt.clock`, so they cannot see M1.
- Lineage and inherited guards: it inherits `single_candidate` caps, the stale-reference `hold` and `tradable()`
  freshness. It also inherits `tradable()`'s 3-minute cutoff (S4) and the per-player status convention of
  `Knowledge.injuries` (S5).
- What it is scored against: nothing. The messages are never scored; LINE_CAL_LIVE hygiene holds (pre-tip only, no
  edits, no outcomes).

## Verification (unpiped, in this worktree at f8dcd5a3)

- `/Users/devin/Downloads/nba-prediction/.venv-1/bin/python -m pytest tests/jarvis -q -p no:cacheprovider --no-cov`:
  467 passed, 1 skipped, exit 0. With coverage on, the same 467 passed but exit 1 from the repo-wide 70% gate on a
  subset run; that is not a test failure.
- `ruff check nba/jarvis tests/jarvis/cards/test_players.py`: exit 0. `ruff format --check` on the 7 touched .py
  files: exit 0. `mypy` on the 6 touched modules: exit 0.
- Previews (never send; written to scratch): `python -m nba.jarvis cards --root /Users/devin/Downloads/nba-prediction
  --no-polymarket --phase players --checkpoint {30,5} --preview --out <scratch>`, exit 0. Also `--checkpoint 180 --at
  2026-11-10T13:00:00 --game MIL@NYK`, exit 0 (the M2 replay evidence).
- Attack tests: `<scratch>/test_pcp_attack.py`, 2 failed as intended (M1 and S1 reproduced).

## Not verified

- The real Discord response or timeout behaviour of `?wait=true` under load. S1 is shown with a stand-in.
- The exact offset from tick start to card pass on game nights. The logs carry no per-step timestamps; tick length is
  inferred from launchd start-to-start gaps.
- Whether the pretip or intraday writers retry when a JARVIS read-only connection holds the shared lock. This exposure
  already exists and the branch only adds about 50 ms per due game.
- Team webhooks: none were configured in this shell, so the live wiring was not exercised end to end.

## Drafted DECISIONS row (not appended)

| 2026-11-10 | Production prop overs never price: `ModelView` has no `p_play`, and with `kalshi_prop_dnp_rule_verified: false`, `single_candidate` caps every prop with a Pinnacle reference at thin (71/71 today). Cards list 0 overs in all 6 games and no prop over can alert. Found by the player-checkpoint builder; confirmed on main by the adversary (docs/reviews/player_checkpoints_2026-11-10.md). Options: add `ModelView.p_play` from the stored forecast (live path, replay oracle, Devin's go), or keep it and say so on the cards. | Adversary finding. | Kalshi's DNP rule verified (the cap then no longer depends on P(plays)). |

## Re-review of aa5fd8ba (diff f8dcd5a3..aa5fd8ba only), 2026-11-10

Verdict: APPROVED

I checked out aa5fd8ba detached in the adversary worktree. The branch worktree was not edited.

| Item | What changed | Check | Result |
|---|---|---|---|
| M1 | `_post_checkpoint` reads `_real_now(rt, now)` immediately before `client.post` and re-runs `window_state(...)`. If the window is no longer `due`, or `tipoff` is None, it records `skipped_late` (via `_safe_write`) and does not post. This runs per team and inside the existing try. | Original scratch attack `test_pcp_attack.py` (tick at T-2, real clock T+1): `{'skipped_late': 6, 'posted': 0}`, 0 posts. New variant `test_pcp_attack_v2.py` (tick at T-3.5, which is still `due` under the new T-5 tolerance of 2; real clock T+1): `{'skipped_late': 6, 'posted': 0}`, 0 posts. Both exit 0. The branch test `test_a_stale_tick_clock_never_posts_after_tip` covers the same case. `rt.clock` is `runner.Runtime.clock`, which returns naive UTC, the same clock as `tipoff`. | PASS |
| M2 | The query binds `to_report_dt(now)` (UTC to ET), and `as_of` is converted ET to UTC before it is stored or displayed. The seed is now stamped in ET (19:00 ET = 23:00Z). | Unit test of the round trip: 17:15Z = 12:15 ET. The known-at test now runs on the production convention. | PASS |
| S1 | `_maybe_delivered` returns true for a 5xx or for `transport X` unless X is ConnectError, ConnectTimeout, PoolTimeout, UnsupportedProtocol or InvalidURL. When true, the attempt is recorded as `posted` with `message_id=""` plus the snapshot. A 429, a gate stop or a budget wait stays deferred. | The scratch attack `accepted_then_timeout` now passes (1 POST per webhook). The notes match `discord.py:141-176`. | PASS |
| S2 | `_safe_write` wraps every state write and counts `state_write_failed`. | Read. | PASS |
| S3 | `open_ro(path)` now uses the defaults, 4 x 1.5 s. | Read. | PASS |
| S4 | The T-5 tolerance is 2 in both `configs/jarvis.yaml` and `DEFAULTS`. | Read. | PASS |
| S6 | `_player_name` falls back to `nba.explain.local_names` for "player N" only. It is lru_cached, and any exception gives `{}`. Lookups use `DEFAULT_DATA_DIR` relative to the module, so they resolve from the main checkout's data/ under launchd. The read is DuckDB `read_only=True` and does not wait on a lock. | Timed from the main checkout cwd: first load 0.764 s (5,269 names), warm reload 0.132 s, exit 0. This is paid at most once per process (launchd starts a fresh process each tick), and only when a placeholder name appears. | PASS |

Can anything new raise out of the pass? No. `_real_now` swallows clock errors. The re-check runs inside the per-team
try. `_maybe_delivered` calls `int(code)` only on `CardResult.code`, which is an int or None. `_local_names` catches
everything. `_safe_write` catches everything. Can anything new slow the tick? Only the name load, at most about 0.8 s
cold, once per process.

Minor findings, none blocking:
- m1. players.py, the `_maybe_delivered` branch: the `posted` record is stamped with the stale tick `now` and has no
  `as_of` block, while the skipped path uses `real`. This is cosmetic. Fix: in that branch, pass `_real_now(rt, now)` to `_rec` instead of `now`.
- m2. players.py `_local_names`: `lru_cache` also caches the `{}` returned after a transient failure. This is harmless
  because each launchd tick is a fresh process. Fix if JARVIS ever becomes long-running: cache only a non-empty result.
- m3. `load_local_names` prints a "player-name conflicts" line to stderr on every cold load. That is noise in the
  launchd log.

Verification at aa5fd8ba, unpiped:
- `pytest -q tests/jarvis`: 470 passed, 1 skipped. Exit 1 comes only from the repo-wide coverage gate (`fail-under 70`)
  on a subset run, as at f8dcd5a3. No test failed.
- `ruff check` and `ruff format --check` on players.py and test_players.py: exit 0 and 0. `mypy nba/jarvis/cards/players.py`: exit 0.

Not verified: the real Discord behaviour on ReadTimeout or WriteTimeout. WriteTimeout and WriteError are treated as
"maybe delivered", which is at-most-once by design. Live webhooks were not exercised end to end.
