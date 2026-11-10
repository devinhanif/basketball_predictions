# Adversary review: app Phase B (modes, tour, what-ifs, track record, "what the game knew")

Branch `agent/app-modes-tour` (29344c2, b75127d, 21462f2, f265af4), diff vs `main`, `app/` only.
Worktree `.claude/worktrees/agent-ab379b3210004fb6c`. Reviewed 2026-11-09 against `data/export/latest`
(manifest `seasons: [2026]`, data_version ec40abfa83e5, 62 games 2026-11-02..09, 54 final). Nothing from season 2025
was evaluated. The running servers (5173, 8000, cloudflared) were left alone. I used API :8765 and vite :5291, both
stopped when I finished. DuckDB was opened `read_only=True` only.

**Overall: WOUNDED.** Privacy holds. The calibration and guess-deck math re-derives exactly. The lock is correct. One
headline claim is false: "Learn lens price-free app-wide (... Parlay)". On `#/parlay` in Learn, whenever a top-parlays
file exists, the page shows American prices and EV per $1. Swipe's card detail also shows posted prices in Learn
(found by reading the code).

Verdict: BLOCKED

The block clears when F1 is fixed (a one-line gate plus a test). F2 and the minors can follow.

## Model class and playbook check

This is not a model. It is a display layer over stored forecasts. The closest MODEL_PLAYBOOK class is "evaluation /
scoring surface": the calibration tab scores the raw props model and the win model.
- **Information or capacity:** no new information. It shows stored pre-tip forecasts and outcomes.
- **Enough data:** props calibration n=850 in 32 games and winners n=54. Thin bins are suppressed correctly (n<20 or
  fewer than 5 games). The winners per-bin intervals are all suppressed, which is the honest result.
- **Test validity:** the outcome is `actual > half-point line`, with the line known before tip. I checked actuals
  against `box_scores` (4776 checked, 0 mismatches) and home_won against `games` (54/54).
- **Lineage and inherited guards:** the export's post-tip exclusion (`excluded.post_tip`, `lines_post_tip`) is the only
  as-of guard on snapshots and news. The app does not re-check it (F3).
- **Scored against:** outcomes only. No market comparison on these tabs. This is fine.

## Checks

| # | Check | Command / method | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Privacy: new endpoints | `probe2.py` swept every `/api/knows/{g}` and `/api/knows/{g}/{p}/{s}` and grepped for `/Users`, `/private`, `data/`, `.duckdb`, `desk`, `stake`, `bankroll`, `balance`, `run_id`, `credential`, `api_key` | 62 games, 8236 props | 0 hits. New routes read only `store.*` (the export) and never touch the desk. `run_id` is dropped by `_snap` | PASS |
| 1b | Guess deck hides outcome and forecast | `GET /api/track-record/guess?seed=7&n=12`, JSON inspected | 12 cards | Cards have only id, date, player, teams, line, and form (`last5`, `avg_minutes5`, `n_prior`, built from `Context` strictly before the slate date). The reveal is fetched only after a pick (`Record.tsx` `useEffect` on `picked`) | PASS |
| 2a | Calibration re-derivation | `rederive.py`: cases from the store, my own bins, my own game-clustered bootstrap (seed 7, B=4000) | props 850 / 32 games; winners 54 | Props overall said 0.4543, happened 0.4718. API 95% CI [0.424, 0.529], mine [0.419, 0.528]. Every bin's n, said and happened match exactly. Bin CIs agree within 0.006. Exclusions are counted: 6362 no posted line, 0 whole-number line, 16 DNP. Row-level CI [0.438, 0.506] is narrower, so clustering is doing its job | PASS |
| 2b | Claim text beyond numbers | crawl of the `record?tab=calibration` DOM | n/a | Numbers and "not enough cases to say more" only. No "well calibrated" claim | PASS |
| 2c | Minutes what-if | read `whatif.ts`, `minutes.py`; vitest | 247 web tests | `affineCounts` maps to `round(aY+b)`, so integer support is kept and the identity holds at a=1. It is labelled "an approximation" and "model only... calibration does not apply". `proj_minutes == 0` is a silent no-op (F5). The export minimum `proj_minutes` is 7.17, so this does not occur today | PASS with minor |
| 2d | EV calc | read `priceRead`/`feeFor`/`bookChance`; vitest | n/a | Break-even = implied + fee. EV = q/(c+fee) - 1. De-vig is multiplicative. The schedule fee is said to be "a shade low" (no cent rounding). Correct | PASS |
| 2e | Learn price-free (DOM) | `crawl.mjs` (headless Chrome, 375 px, every `<details>` opened, `innerText` grepped for ¢, `\d+c`, edge, EV, odds, `[+-−]\d{3}`, fee, price, vig, venue names, `$\d`, bet, payout) on 20 routes | 20 routes | On the live export: no numeric price anywhere. With the fixture top-parlays file (`#/parlay?date=2024-01-01`), Learn shows "DraftKings -140", "+$0.05 per $1", "Gains about 5 cents per $1" | **FAIL (F1)** |
| 2f | Timeline known-at | sweep of all snapshots and news against `tip_utc` | 8236 props, 2197 with 2 or more snapshots | 0 snapshots at or after tip, 0 news at or after tip. Shown snapshot p_over 0.678 equals `/api/props` p_over 0.678 | PASS (relies on the exporter, F3) |
| 3a | Lock | `lock_attack.py` with fake loaders | 16 / 8+8 / 4 threads | Cold start: 1 load. Two swaps: 2 loads, final key = content key. A raising load releases the lock and the next caller retries (0 hung threads) | PASS |
| 3b | Latency | `probe.py` against the real export | n/a | Calibration 0.046 s cold, 0.002 s cached. Guess 0.004 s. `/api/knows/{g}` worst 0.002 s. Player knows worst 0.095 s over 8236 calls. Store cold load 3.6 s | PASS |
| 4a | Private mode | code read | n/a | `PH_PRIVATE` gates only `/api/desk`, which is unchanged. New routes never read the desk | PASS |
| 4b | Artifact build | `ls app/web/artifact-data` | n/a | Absent in both the worktree and main, so it **was not built**. In code, Knows, Explore and Record are gated by `IS_ARTIFACT` | NOT VERIFIED |
| 5 | Gates (unpiped) | `uv run --frozen pytest -q -x`; `ruff check`; `ruff format --check`; `mypy src`; `npm run lint`; `npm run typecheck`; `npm test` | 377 passed + 1 skipped; 247 web | All exit 0 | PASS |
| 5b | Screenshots 375 | read `shots-phase-b/*.png`; `audit.json` | 14 audited of 69 | Audit clean, but it skips the guess, explore and tour shots. `record-guess_375_light.png`: the OVER button is cut off ("7 OR MORE REBOU") and the bottom nav cuts "My pic" (F7) | minor |

Planted future (check 2): the app has no as-of filter of its own (F3). It is safe today only because the exporter drops
post-tip rows. Shuffled labels, knockout and integer support do not apply: no claimed model gain is under review here.

## Findings

**F1 (major): Learn shows prices on the Parlay page.** `app/web/src/pages/Parlay.tsx:137` renders
`<LatestTopParlays date={props.date} />` with no lens gate. `TopParlaysPanel.tsx:41-42,83-84` prints book prices
(`american(leg.price_american)`) and EV per $1.
- Evidence: the Learn crawl of `#/parlay?date=2024-01-01` returned "DraftKings -140 / -150" and "Expected result per
  $1: +$0.05" (`crawl_tp/crawl-learn.json`).
- This contradicts commit f265af4 ("Learn lens price-free app-wide (... Parlay)") and the MarketOnly copy.
- Fix: in `ParlayPage`, `{useLens() === "market" ? <LatestTopParlays .../> : <MarketOnly name="Ticket list" what="..."/>}`.
- Add a render test that mounts ParlayPage in Learn with the fixture tickets and asserts no `/[+-]\d{3}\b|\$\d|cents per/`.

**F2 (major, code read, not reproduced: no open swipe deck at review time): Learn shows posted prices in Swipe's card
detail.** `app/web/src/CardDetail.tsx:250-252` prints "Posted line X at Book: over -110, under -115" with no lens check.
It is mounted from `pages/Swipe.tsx:199`.
- The same gap is in `SharedUI.tsx:109`: FriendsPicks shows "Took over 15.5 (-110)". That is artifact and shared picks
  only.
- Fix: pass `market` into CardDetail and FriendsPicks and drop the price clause outside Market, as Picks.tsx already does.

**F3 (minor, defence in depth): the timeline does not refuse post-tip rows itself.**
- `export_store.py:529` drops only `lead_minutes is None`, not `lead_minutes <= 0`.
- `knows.py:146` (`news_items`) and `knows.py:263` ("latest report" teammates) take every injury row, whatever its time.
- Today every row is pre-tip (snapshot lead minimum 10.1 min, report lead minimum 75 min). If an export ever admits a
  post-tip row, a public screen titled "what the game knew" would show it.
- Fix: in `_snapshots` add `if r["lead_minutes"] <= 0: continue`. In `_injury` / `news_items` skip rows with
  `at_utc >= tip_utc`.
- Add a negative test that plants one of each.

**F4 (minor, honesty of framing): the export holds one injury report per game.** There are 32 distinct report times
across 62 games, and every game has exactly 1. So the timeline cannot show "when the news broke": a player out since
yesterday appears as first listed at T-75 min. The "Why the range is this wide" list includes "teammates listed out" from
a report that can post-date the shown forecast. For example, game 0022600208 has its shown forecast at 15:20Z and the
report at 16:15Z. The model did not see that report, and the screen implies it explains the width.
- Fix: say "only the latest pre-tip report is stored, so earlier reports are missing".
- Only list report facts with `at_utc <= shown.made_at` under "why", or label the rest "published after this forecast".

**F5 (minor): a zero baseline turns the minutes what-if into a silent no-op.**
- `minutes.py:69` accepts `proj_minutes == 0.0` as a `"model"` baseline.
- `whatif.ts:38` then uses `s = 1`, while the screen says "stretched to M minutes".
- Fix: treat `proj <= 0` as missing (fall back to recent, else None) in `minutes_info`. Add a test.

**F6 (minor, copy):** in Explore, the game-page legend still says "the price beside it is one book's quote" when no
price is shown (crawl `explore-game-*`).
- Opting into "Show the price arithmetic" offers "Use the stored posted prices (-110 / -115)". That is acceptable
  because the visitor asks for it, but the MarketOnly copy "The Learn and Explore lenses leave prices... out" should
  say "unless you turn on the price arithmetic".
- Learn Discover hard-codes "No parlay tonight. Keep your money." (`Today.tsx:195`) even on a night the engine had a
  pick. That sentence is a factual claim about the engine. Prefer "Parlays live in the Market lens."

**F7 (minor, UI):** on `record-guess_375_light.png` the guess buttons overflow at 375 px. `.gs-actions`
(`modes.css:357`) uses `1fr 1fr` with long sub-labels; use `minmax(0,1fr)` and wrap. The five-tab bottom nav also clips
the last label. `shots.mjs`' audit does not cover the guess, explore and tour shots: add them.

**F8 (nit):** calibration bootstrap code lives in `trackrecord.py:_interval`, not in `truth/bootstrap.py`. The app
cannot import `nba/`. Note this as a sanctioned second copy, with a test pinning it to the re-derivation here.
`_cache["calibration"]` keeps the previous store alive until the next calibration request: one extra store in memory
after a swap. `get_store` computes `key` before taking the lock (main.py:86), so two swaps inside one load can cause
one extra reload. It converges and is never stale.

## Corrected family and power note
No inferential claim is made. Calibration is descriptive, with 7 props bins with intervals and 0 winner bins.
- With 32 game clusters the props overall CI half-width is about 5 pts. A 63.9% vs 44.0% gap in the 0.6-0.7 bin
  (n=50, 21 games) is visible, but bins are not multiplicity-corrected.
- The page makes no claim from them. Keep it that way: any sentence drawn from a bin needs BH across the 10 bins.

## Single biggest risk
A Learn visitor, the default for every first-time public visitor, opens "Parlay of the day" on a night with a tickets
file and sees book prices and "+$0.05 per $1". Only the Market lens is meant to show that.

## Not verified
- Artifact build: no `artifact-data`.
- Swipe CardDetail in a live deck: there was no open deck, so this was code read only.
- Whether the stored grid is conditional on playing, which the copy "The range above assumes he plays" relies on.
- Behaviour during a real RENAME_SWAP mid-load: simulated with fakes only.
- The tunnelled origin: I did not touch it.

## Drafted rows (not appended)
- Ledger: none. There is no model claim.
- DECISIONS (draft): "App lenses: anything showing a price, fee, EV or book quote renders only under `lens === market`
  or behind an explicit opt-in; enforced by a DOM-grep test over every route in Learn. Reverse if the Learn lens is
  dropped."
