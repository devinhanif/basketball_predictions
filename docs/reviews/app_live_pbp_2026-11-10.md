# Adversary review: live play-by-play (agent/app-live-pbp, b11efe9b API + de4ca503 web), public via the tunnel

- Worktree `.claude/worktrees/agent-a774eb25b6ab9e893`, reviewed 2026-11-10.
- Attacks ran on my own servers only: API :8765 with `APP_LIVE_DIR` set to a scratch copy of the committed fixture and
  `APP_CHECKPOINT_DB` pointing at a missing file; vite :5293; Chrome :9672. All were stopped. I did not touch 5173, 8000
  or cloudflared. Nothing was read from the real `data/raw/live_*` except a directory count.
- Context: Devin's override (DECISIONS 2026-11-10 03:10) covers republishing ESPN play-by-play on the public link. This
  review does not reopen that; the redistribution risk Claude flagged stands as recorded.

**Overall: WOUNDED.** The file-reading surface is tight: no path traversal, no file paths or desk data in any payload,
and no prices in any lens. The viewer caps are enforced and released. Three problems block a public merge:
- **M1:** public requests to `/checkpoint` open the live JARVIS DuckDB store, and an open reader makes the JARVIS
  writer fail.
- **M2:** the viewer-cap path leaks watch slots until restart.
- **M3:** on locked nights, an in-progress tracker shows the calibrated chance beside a prop that has already gone
  over. That puts a chance next to an outcome, against the display lock merged yesterday.

Verdict: BLOCKED

## Playbook check
This is not a model. It is a display and transport layer over stored ESPN bodies and an optional in-game checkpoint
model (JARVIS, labelled UNPROVEN). The relevant MODEL_PLAYBOOK concerns are test validity (the LINE_CAL_LIVE display
lock) and inherited guards: the calibrated-only rule and the M3 lock from `agent/app-calibrated-only`. No claim is
scored.

## Checks

| # | Check | Command / method | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Path traversal / arbitrary read | `live_http.py`: `..%2F..%2Fetc`, `%2e%2e`, `..%2f..%2fapp/snapshot`, `//etc/passwd`, `%0A`-suffixed date and id, Arabic-Indic digits, plus a code read of every path join | 17 requests | Every traversal attempt returns 404 before any disk work (routing plus `DATE_RE`/`EVENT_RE`). Joins are `root/live_<date>/espn_summary/<id>` with digits only. Two nits: `$` accepts a trailing newline and `\d` accepts Unicode digits. `/api/live/2026-11-09%0A` returns 200 with `"date":"2026-11-09\n"` and an empty list. Harmless (no directory exists), but use `fullmatch` and `[0-9]` | PASS (nit) |
| 2a | Stream caps enforced before work | 400 concurrent `/stream` opens on one game; then all closed and 160 re-opened | 400 + 160 | 150 got 200 and 250 got 503 in 0.14 s. A store route answered in 0.002 s during the flood. After closing, 150 got 200 and 10 got 503, so slots are released on disconnect | PASS |
| 2b | Watch-slot leak | `live_attack.py`: `LiveHub` in-process, total cap lowered to 2, 40 extra valid events | 38 HubFull | Each refused subscribe for a new game leaves an empty `GameWatch` in `hub.games` (inserted before the viewer-cap check, never removed). After every viewer left: **28 of 30 slots held by dead entries with no subscribers and no task**. Only 2 more distinct games can ever be watched until restart. On a busy night the cap hits naturally, so no attacker is needed | **FAIL (M2)** |
| 2c | Query abuse | `before=-1` returns 422, `limit=100000` returns 422, `before=1e20` returns 200 (bounded at 200 rows), plus code read | 3 | Bounded. `plays_before` sorts every play per request, which is O(n) with n of about 500. Fine | PASS |
| 2d | Non-stream routes from the internet | code read | n/a | `/api/live/{date}`, `/snapshot`, `/plays` and `/checkpoint` have no caps. They run in `asyncio.to_thread`, which is the same AnyIO thread pool (40 tokens) the sync store routes use. `/api/live/{date}` has a 1 s cache, but a flood of distinct `/snapshot`/`/plays` ids (each does `iterdir` of `data/raw`, 33 entries, plus stats) can queue the pool. Rely on a Cloudflare rate-limit rule, or add a per-IP token bucket | minor |
| 2e | Corrupt or huge bodies | code read of `load_state` | n/a | `gzip.decompress(read_bytes())` has no size cap, so a gzip bomb in a capture body would exhaust memory. The bodies come from our own capture of ESPN, so the risk is low; capping it is cheap. Parse errors are caught and logged with the file name only, never returned | minor |
| 3 | Leaks in payloads and errors | grep every response above for `/Users`, `/private`, `/tmp`, `data/raw`, `.duckdb`, `Traceback`, desk, stake, price, ask, bid, edge | 17 + stream | 0 hits. Snapshot keys are ESPN content plus timing `meta`; box rows are ESPN stats only. Errors are fixed strings. `/checkpoint` reasons are fixed strings | PASS |
| 3b | Checkpoint store safety | scratch DuckDB: one process holds `read_only=True`, another writes | 1 | **The writer fails: `IOException: Could not set lock on file`.** `read_checkpoint` opens `data/jarvis/ingame_checkpoints.duckdb` read-only on every public request. `nba/markets/checkpoint_store.py:150` opens the same file for writing with no retry. Once the store exists, any visitor (or a flood) holding a read open makes the live JARVIS checkpoint write fail. That is the live path, against the single-writer rule | **FAIL (M1)** |
| 4 | LINE_CAL_LIVE display lock | code read of `live.ts:291-328` and `LiveParts.tsx:240-320`; crawl of the Live game with the final body held back so it reads in progress | n/a | Final games: trackers give way to the lock note (`trackersLocked`). The finished box score and Live's final scores show outcomes with no chance, which is consistent with the merged lock (outcome alone is fine). **In progress:** each tracker shows "pre-game chance of the over 42% calibrated" beside "has 23 … Over the line" (`pace: cleared`). A cleared over is a final per-prop outcome, and in late Q4 most props are effectively decided. That puts a chance next to an outcome on one screen on locked nights. Also, if the capture stops before ESPN flips to `post`, trackers stay unlocked indefinitely. I could not render trackers with lines: the fixture's ESPN names do not match the export slate ("No lines to track here"), so this rests on the code | **FAIL (M3, needs Devin)** |
| 4b | Tracker label | code read `LiveParts.tsx:265` | n/a | `<CalTag source="line_cal" />` is hard-coded even when the prop's chance is `rung_cal`. Pass the real `model_p_source` | minor |
| 5 | No prices in any lens | `crawl3.mjs` (my price regex, aria and title text, every `<details>` open) in Learn, Explore and Market on `#/live?date=2026-11-09`, `#/live/401909952?…`, the game page panel and the trackers tab; code grep of the live files for `american\|price\|odds\|edge\|ask\|bid` | 3 lenses | 0 numeric prices, 0 book names in the live views. The only hit is the 1-800-GAMBLER footer. The checkpoint payload is SELECTed without price, ask, bid or edge | PASS |
| 6 | SSE through Cloudflare / service worker | headers from `/stream`, with and without `Accept-Encoding: gzip`; `sw.js` diff | 2 | `text/event-stream`, `X-Accel-Buffering: no`, no `Content-Encoding` even when gzip is asked (Starlette 1.8 excludes event-stream), `retry: 2000`, a 15 s heartbeat, and the stream closes after `final`. **But** the global middleware (`main.py:183`) overwrites the route's `Cache-Control: no-cache, no-transform` with `no-cache`, so `no-transform` is lost. Cloudflare does not buffer event-stream by default, but keep `no-transform` for intermediaries. The service worker skips `/api/live*` (no cache, no proxy) | minor |
| 7 | Tests meaningful | read `test_live.py` (21 tests) and `live.test.ts`; gates | 412+1 skipped; 282 | All gates exit 0 (pytest, ruff, format, mypy, eslint, tsc, vitest). The tests cover parsing, deltas, newest-across-sources, real-server SSE with landing bodies, and the 503 cap. Missing: traversal and odd-id tests, the M2 leak, client disconnect releasing a slot, the checkpoint-lock interaction, and in-progress tracker lock behaviour | partial |

## Findings

**M1 (blocker for public): public reads can block the live JARVIS writer.**
- Where: `live.py read_checkpoint` (`duckdb.connect(str(path), read_only=True)` per request). The writer is
  `nba/markets/checkpoint_store.py:150` (`duckdb.connect(str(path))`, no retry).
- A read-only DuckDB connection takes a shared file lock, so the writer's connect fails while any reader is open.
  Reproduced with a scratch file.
- The store does not exist today ("flag off"), so the bug sleeps until the checkpoint flag is turned on. Then any
  visitor can make live writes fail.
- Fix, either:
  - (a) never open the live store from the app. Have the JARVIS writer (or a small job) publish a read-only copy or a
    JSON snapshot of the newest checkpoint per game after each write, and have the app read that; or
  - (b) at least cache one read for 10 to 30 s per process (single-flight), so public load is one open per interval,
    and add a retry with backoff to the writer.
- (a) matches the single-writer rule. Add a test that a reader open never coincides with a writer open.

**M2 (major): watch slots leak when the viewer cap is hit.**
- `LiveHub.subscribe` inserts `self.games[event_id] = gw` before checking `MAX_SUBS_TOTAL`/`MAX_SUBS_PER_GAME`. On
  `HubFull` the entry has no subscribers and no task, and nothing removes it.
- Reproduced: 28 of 30 slots dead after viewers left.
- Fix: check both viewer caps before inserting. On any raise, pop the entry if `not gw.subs and (gw.task is None or
  gw.task.done())`.
- Add a test: fill the total cap, request N new games, release everyone, assert `len(hub.games) == 0`.

**M3 (major, needs Devin): an in-progress tracker shows the chance beside a decided over.**
- The merged lock (calibrated-only, nights from 2026-10-30) says chance and per-prop outcome never share a screen.
  Trackers keep the calibrated chance while showing "Over the line" for cleared props and live counts that decide most
  props in Q4.
- They also stay unlocked if ESPN's `post` body is never captured.
- Options:
  - (a) on locked nights, trackers show pace only, with no chance (simplest; recommended);
  - (b) hide the chance on a tracker once `pace === "cleared"` and from Q4 onwards;
  - (c) Devin accepts the in-game juxtaposition in a DECISIONS row.
- In every option, lock on "the slate's tip was more than 4 h ago" as well as on `state === "post"`.

**Minors:**
- **m1:** use `fullmatch` and `[0-9]` in `EVENT_RE` and `DATE_RE` (`live.py:50-51`).
- **m2:** cap decompressed body size (for example 20 MB) in `load_state`.
- **m3:** let `/api/live*` keep `no-transform`. In `main.py:183`, skip the override when the response already sets
  `Cache-Control`.
- **m4:** pass the real calibration source to `CalTag` in `TrackerRow`.
- **m5:** document the rate-limit expectation for the non-stream live routes (Cloudflare rule), or add a light per-IP
  limiter; they share the thread pool with the store routes.

## Single biggest risk
Turning on the checkpoint flag with this branch merged lets any visitor to the public link make the live JARVIS
checkpoint writer fail mid-game, silently, from the internet.

## Not verified
- A real Cloudflare tunnel (headers checked at the origin only).
- Trackers rendered with real lines: the fixture's ESPN names do not match the export slate.
- Client-disconnect detection through cloudflared, as opposed to direct to uvicorn.
- The `fresh/` poller directory path with real bodies.
- The artifact build.

## Drafted rows (not appended)
- DECISIONS (only if Devin chooses M3 option (c)): "2026-11-10: live prop trackers show the calibrated pre-game chance
  beside in-game counts, including cleared overs, on LINE_CAL_LIVE-locked nights; accepted as display exposure, no
  tally; Claude and agents do not aggregate. Reverse: any aggregate read before the look."

---

## Re-review (1e84a039; diff de4ca503..1e84a039)

All of M1, M2, M3 and the minors are fixed. My replay of the checks turned up one new major: the game-page live embed
puts the per-prop outcome back next to the calibrated chances that the merged lock hides on that page. It is a small
gate, but it reverses the lock merged yesterday, so the branch is not mergeable yet. **Overall: WOUNDED.**

Verdict: BLOCKED

How I tested: worktree `agent-a774eb25b6ab9e893` at 1e84a039. I ran my own API (:8765, `APP_LIVE_DIR` set to a scratch
copy of the fixture with the final body held back, `APP_CHECKPOINT_SNAPSHOT` set to a missing file), vite (:5293) and
Chrome (:9672). All were stopped. I did not touch 5173, 8000 or cloudflared.

| Fix | Command / method | n | Result | Pass |
|---|---|---|---|---|
| M2 slot leak | replanted `live_attack.py` (total cap 2, 40 extra valid events) | 38 HubFull | Games dict 2 while full. **0** dead entries after viewers left (was 28/30). A fresh viewer is fine | PASS |
| M1 checkpoint lock | `grep -rn "import duckdb\|duckdb.connect" app/`; read `live.py` | n/a | No DuckDB anywhere in `live.py`/`live_routes.py`. The only runtime DuckDB in `app/api/src` is `export_store.py:281` (`read_only=True` on the export copy, which was already there and is not a live store). Checkpoints are read from a published JSON (`ingame_checkpoint_snapshot_v1`), with a 15 s cache, a 5 MB cap, a version check and whitelisted fields | PASS |
| Traversal set | the same 7 crafted paths over the real server | 7 | All 404 (`..%2F`, `%2e%2e`, `%0A` date and id, Arabic-Indic digits, `..%2f..%2fapp`, `//etc/passwd`). The `%0A` date that returned 200 before now returns 404 (`fullmatch`, `[0-9]`) | PASS |
| Gzip bomb | 200 MB of spaces compressed into a 204 KB body under a valid event | 1 | Refused ("larger than 20000000 bytes decompressed"), `UnknownGame`, max RSS +40 MB, no entry left behind | PASS |
| Stream headers | `/api/live/401909955/stream` with `Accept-Encoding: gzip` | 1 | `text/event-stream`, `Cache-Control: no-cache, no-transform` (now kept), no `Content-Encoding`, `X-Accel-Buffering: no`. Store routes still get `no-cache` | PASS |
| M3 trackers on a locked night | `locked_render.mjs`: Live game 401909952 (2026-11-09, held in progress) with the Prop trackers tab opened; code read of `buildTrackers`/`chanceLocked`/`treatAsFinal` | 1 render + code | The note reads "Pre-game chances are not shown here. The model's number for this night is part of a locked test until about Dec 1." `chance` and `source` are null whenever `date >= 2026-10-30`, in progress or final. A game more than 4 h past tip is treated as final. The fixture's ESPN names do not match the export slate, so no tracker rows render ("No lines to track here"). The no-chance rule is pinned by `live.test.ts` | PASS |
| Gates (unpiped) | pytest; ruff; format; mypy; eslint; tsc; vitest | 420+1 skipped; 284 | all exit 0 | PASS |

**New M5 (major): the game-page live embed shows the per-prop outcome beside calibrated chances on locked nights.**
- `pages/Game.tsx:221` renders `<GameLive g={g} />` on every game page.
  - For a finished game it sits behind "Show play by play (shows the final score)".
  - For a game in progress it is open.
- Inside are the Box score tab (per-player PTS, REB, AST, 3PM, final or running) and the play feed.
- The same page renders each player's calibrated chance (`Game.tsx:61,129`). The merged lock deliberately replaces
  each per-prop result there with `RESULT_HIDDEN` (`Game.tsx:89,150`).
- Rendered on `#/game/0022600203` (2026-11-09): after "Show play by play" and "Box score", the page lists, for example,
  "LUKA DONCIC … 22 PTS 6 REB 9 AST 3 3PM". In this export the game has no posted-line chances, so the overlap did not
  render here. On any locked night with posted lines, the chance and the outcome share one screen, which is what the
  lock forbids and what M3 just removed from the trackers.
- Fix (one gate): on a locked night (`isLockedNight(g.date)`), the game page shows only status, score and the "Open
  the full live view" link, without `LiveGame`'s Box and Prop tabs (Plays text also reveals per-player scoring). The
  full Live screen carries no chances on locked nights, so linking there is safe.
- Add a render or unit test: on a locked date the game page never mounts the live box.

The display-level lock is now consistent everywhere except this embed. With M5 fixed I expect to approve without
another full pass: re-check only the game page on a locked date.

**Not verified:** a real Cloudflare tunnel; tracker rows with real lines (fixture names do not match); the published
checkpoint snapshot written by JARVIS (the contract exists, the writer is not on this branch).
