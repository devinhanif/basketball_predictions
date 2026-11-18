# App live card (2026-11-17)

Branch `agent/app-live-card` (worktree `/Users/devin/Downloads/nba-wt-live-card`), off `main` 109f8c1d. App only. Asked by Devin through the
main session: "build the live card... also the playbyplay takes a long time to load".

Advice or momentum: Devin's request (his words above), not a recommendation of mine.

## Part 1: play-by-play slow to appear (commit df1c9b2d)

Cause (measured by the main session): behind a quick tunnel the event stream's headers arrive and then no bytes for 45 s; the page waited for the
stream's first `snapshot` event before drawing anything.

- (a) `useLive.ts` `connectLive` now fetches `/api/live/<id>/snapshot` at once and renders from it; the stream is opened beside it for updates.
  An older copy never replaces a newer one on screen (compared by `meta.fetched_at`). A final game in the snapshot closes the stream.
- (b) No stream event of any kind within `SILENT_MS` (5 s) of opening, or a stream that errors before it ever spoke, switches to polling
  `/snapshot` every `POLL_MS` (15 s plus up to 2 s jitter). The page says "Updating every 15 s." in a muted line (`PollingNote`), not an error.
  The stream is retried with a growing wait (`STREAM_RETRY_MS` 30, 60, 120, 300 s, plus a quarter jitter); the first stream event ends the polling.
  A stream that had spoken keeps the old reconnect path on its first drops.
- (c) Server: `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform` were already on the response (kept); the stream now starts with an
  SSE comment line of 2,048 spaces (`live.STREAM_PADDING`) before `retry:` and the snapshot. GZip already excludes `text/event-stream`.
- (d) The live list page only ever polled `/api/live/<date>`; it never waited on a stream. No change needed, and a test of `connectLive` without
  `fetchSnapshot` pins that the old stream-only path is unchanged.

Tests: `app/web/src/liveFallback.test.ts` (11): immediate snapshot render with a silent stream and no timer fired; failed snapshot does not stop the
stream; older copy never overrides; silent stream falls back (phase stays `live`, transport `polling`, next poll and stream retry queued); retry
wait grows; a stream that speaks ends polling; errored-before-speaking falls back; stop leaves no timer or open stream.
`app/api/tests/test_live.py::test_stream_starts_with_padding_and_asks_proxies_not_to_buffer`.

Timings (local, headless Chrome, navigation to first `.play` row in the DOM, 3 runs each, fixture capture on my own ports):

| Case | Before | After |
|---|---|---|
| Local, stream working | about the stream's first bytes plus render (main session: 6 ms server side) | 143, 166, 167 ms |
| Stream held (hold-proxy on 8331: headers, then silence) | no play within 45 s (main session's tunnel measurement; the page waited on the stream) | 149, 153, 169 ms |

"Before" for the held case is not re-run by me (I did not keep an old build running); it is the main session's tunnel measurement, and
`connectLive` without `fetchSnapshot` (the old path) is pinned by a test to stay empty on a silent stream. Not verified through a real tunnel;
the main session will.

## Part 2: the live score card, step 1 (commits 2af30e15, d308ecce)

API (`live_card.py`, `live_card_routes.py`, registered from `live_routes.register_live`; GET only):
- `GET /api/live/{game_id}/card` and `GET /api/live/{date}/cards`. Joins the live box the hub holds (or the newest capture body parsed in a
  worker thread) with the export's stored posted lines for the same night and teams. Typed `no_live_capture` (404) for a game with no capture.
- Never waits on the export store: if its first load is not done, the card answers at once with `lines_state: "loading"` (and starts the load in a
  thread); a refused export is `"unavailable"`; no stored game is `"no_stored_game"`. Never a sample in place of real data.
- Speed (fixture capture, store loaded): first call 35 ms (parses the body), then 1.1 to 1.7 ms; target was under 50 ms. Stored side cached per export key.
- Tracked: every player with a posted main line (`line_source == "posted"`) for pts, reb, ast, fg3m. Reference-only lines are counted and
  said ("2 more stored lines are the model's own reference point, not a sportsbook line, so they are not tracked").

Web (`liveCard.ts`, `LiveCardParts.tsx`, `useLiveCard.ts`, `liveApi.ts`, `liveCard.css`, edits in `LiveParts.tsx` and `pages/Live.tsx`):
- Board is the first panel after the score on a live game's page; the Live list has a "Games / Every tracked player" switch for the slate view
  (sort: closest to the line, game, recently changed; changed rows get an 8 s non-flashing tint, a word "up N", and a static mark under reduced motion).
- Row: Avatar (existing component, no image copied), stat and line, count in mono, flat square bar with a tick at the line, one sentence, minutes,
  fouls, the standing word (IN PLAY, OVER NOW, UNDER, NOT ENTERED, DID NOT PLAY, FOULED OUT, NOT STARTED) so colour is never alone.
- Freshness: "as of Q3 4:12, about 1 min ago" (ticks each second), and the one line that the feed runs about a minute behind.

## Choices I made (you could not be asked)

1. **Display lock is stricter than the brief's "in-play games".** Existing rule (web `chanceLocked`, tested in `live.test.ts`: "on a locked night show
   progress and pace only: no chance, in progress or final") applies from `MODEL_LOCK_FROM` 2026-10-30. The card follows it on the server: a calibrated
   pre-game chance is returned only before tip, or in play on a night before the lock; never for a final game. Tonight (2026-11-17) is locked, so tonight
   the card shows no pre-game chance once a game starts, and says so once ("Pre-game chances are not shown for games under way or finished. The model's
   number for this night is part of a locked test until about Dec 1."). If Devin wants in-play chances on locked nights that is a LINE_CAL_LIVE
   question, not an app one.
2. Learn lens: the card appears (facts only, no price words; a vitest pins the sentences against the learn-dom price patterns). Explore: same, lines as
   numbers. Market: adds "Stored price before tip: over -115, under -105 (DraftKings). Not live." from stored fields only.
3. Time left is game time left from the clock only (48 minus elapsed regulation minutes), worded "about N minutes of game time left". I did not use
   `proj_minutes` from the export, so no "usual share" wording appears.
4. "Cannot be reached in regulation" is only stated when regulation time is 0: "Not reached in regulation: 18 of 24.5 points." I do not call a count
   impossible earlier from arithmetic, since that needs a pace assumption.
5. Score and clock come from the freshest body the hub already has (newest of capture and fresh-poller bodies). The scoreboard, about 20 s fresher, is not
   read by the app today; adding it would put a newer score beside older box counts. Left as a later step.
6. Simple mode shows the 8 rows closest to the line with "Show all N lines"; Analyst shows all, denser, two columns at 900 px and up.
7. Changed-row marks are computed in the browser by comparing consecutive reads (the server is stateless); the first read marks nothing.
8. Classes are prefixed `lcd-` (the existing live list already owns `lc-row`, `lc-teams`; my first prefix collided and I renamed it).
9. No wide display font was needed; the card uses the app's `--mono` and tokens.
10. Fixture dates: the synthetic stored game is dated 2026-11-09 because it must join the committed live capture fixture (a real 2026-11-09 capture);
    player ids are fake (9000001 up), lines and prices are made up and say so in the file. No season-2025 data. Same date caveat as the existing live fixtures.

## Part 3: the in-game model's chance, behind a constant that is OFF

- `live_card.INGAME_MODEL_CHANCE_ON = False`. With it off the payload has no `in_game` field, `model_on` is false, and the version-2 file is never read
  (test monkeypatches the reader to raise).
- `read_ingame_v2` reads `version == 2` only, from `APP_CHECKPOINT_SNAPSHOT` (same path as v1), 15 s cache, 5 MB cap. Age check:
  `SNAPSHOT_STATE_MAX_AGE_S = 180`, the contract's state-age limit, applied to the file's `generated_at`; older shows nothing. Each row carries the age
  of the game's `known_at`. Matches a snapshot row to a card row by normalised player name, stat, and `k == floor(line) + 1`.
  In-play games only (a pre or final game shows nothing).
- Row text is exactly `Experimental, not yet tested: 62% (model as of Q3 3:39, about 2 min ago)`. No running score, hit rate, record or tally exists in
  code or payload (test greps the payload and sources).
- The v1 reader (`live.read_checkpoint`) is untouched. NOTE for the merge: it accepts only `version == 1`, so when JARVIS starts writing version 2 the
  existing `/checkpoint` panel (CheckpointPanel) will go dark ("not readable") unless that reader also accepts 2. I did not change it, per the brief.
- To turn on: set `INGAME_MODEL_CHANCE_ON = True` in `app/api/src/parlayhitters_api/live_card.py` (one line) after `agent/market-game-scripts-2` merges
  and Devin has looked. Fixture: `app/fixtures/live_card/checkpoint_v2.json`.

## Rules and the test that pins each

| Rule | Test |
|---|---|
| Page paints from /snapshot, not the stream | `liveFallback.test.ts` "asks for the snapshot the moment it starts" |
| Silent stream falls back to polling with a plain note, stream retried with backoff | `liveFallback.test.ts` "a stream that opens and stays silent (b)" |
| Padding, X-Accel-Buffering, no-transform | `test_live.py::test_stream_starts_with_padding...` |
| Tracks posted lines only, says how many and why not | `test_live_card.py::test_tracks_every_posted_line_and_says_why_others_are_not` |
| Counts from the box, need is floor(line)+1-count | `test_counts_come_from_the_live_box_and_the_need_is_arithmetic` |
| Not entered / DNP / fouled out / no box / final / overtime | `test_status_words`, `test_players_who_are_not_in_the_game...`, `test_overtime_is_flagged...`, `liveCard.test.ts` pace sentence |
| Game time left from the clock only | `test_regulation_time_left_comes_from_the_game_clock_only`, "never claims a usual share" |
| No probability computed, nothing called likely | `liveCard.test.ts` "no sentence ... likely", "compute no probability" (source grep), `test_the_payload_has_no_probability_computed_here` |
| Freshness: clock and age; feed-delay line once | `test_freshness_fields...`, `liveCard.test.ts` "freshness" |
| Final game: no chance | `test_a_final_game_never_carries_a_chance` |
| Locked night: no in-play chance; calibrated only, never raw | `test_locked_night_in_play...`, `test_unlocked_night_in_play_shows_calibrated_numbers_only` |
| Before tip chance shows | `test_before_tip_the_calibrated_chance_shows...` |
| Price only in Market; Learn has no price words | `liveCard.test.ts` "price words ... only behind the Market lens", "the Learn lens" |
| Typed error, no sample | `test_unknown_game_is_a_typed_error_not_a_sample`, `test_no_stored_game_is_said_plainly` |
| Cold store never waited on | `test_a_cold_store_is_never_waited_on` |
| Fast | `test_the_card_is_fast_with_the_store_loaded` (loose guard; real numbers above) |
| GET only | `test_the_route_is_get_only`, source test |
| Changed-row marks | `liveCard.test.ts` "what changed since the last update" |
| Step 2 off / on / old / wrong version / final | `test_the_constant_is_off...`, `test_reader_v2_*`, `test_on_state_through_the_route...`, `test_the_model_has_no_running_score_anywhere` |

## Not verified / denied

- **Tonight's real capture was not used.** My command to start the API against `/Users/devin/Downloads/nba-prediction/data/raw` and
  `data/export/latest` was denied by the permission system, so I did not retry or work around it. Everything was run on the committed live fixture
  (LAL at ATL, 2026-11-09, re-stamped to now) and the synthetic stored game, via `app/api/tests/live_card_demo.py`. Real name matching between ESPN box names
  and export names is therefore tested only on the fixture's names (the web `matchRow` rule ported to Python).
- `curl` is not available to me; timings used Python `urllib` and headless Chrome.
- No tunnel. The held-stream case is a local hold-proxy.
- Screenshots: 375 px Simple and Analyst, dark (board and slate; the list at 375 px), and one 1280 px light Market-lens board. Not every combination.
- The export's `proj_minutes` and the scoreboard source are unused (choices 3 and 5).

## Shared files I wanted but did not change

None required. `api.ts` was left alone (the live calls live in `liveApi.ts`). Optional: add `liveApi` calls to the artifact bake list is not needed (live is not baked).

## Check results (tip 295130d3 plus this note)

`nice app/scripts/check.sh --bake`: API ruff, format, mypy clean (106 files); pytest 632 passed, 1 skipped; fixtures ruff; web eslint, tsc clean; vitest 606 passed
(58 files); build; artifact bake; flag parity passed. `nice app/scripts/check.sh --betting`: same counts, all pass. `tests/test_layering.py`: 15 passed.
Widths walk (`WALK_API_PORT=8341 WALK_WEB_PORT=5341 app/scripts/walk.sh --widths`, 390 injected, 360, 768, 1180): 0 violations.
An earlier full run failed once on `test_route_contracts` because the new routes were not in `tests/hostile_gen.py`; fixed and re-run.
