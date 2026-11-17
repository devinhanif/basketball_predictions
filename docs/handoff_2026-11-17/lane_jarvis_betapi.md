# Lane J4a: betting quote board API (read-only, switch ON only)

Branch `agent/app-jarvis-betting-api`, worktree `/Users/devin/Downloads/nba-wt-1117-jv-betapi`. Worked 00:38-00:56 CST
2026-11-17 (times from `date`). Paths touched: `app/api/` and `docs/contracts/betting_board_v1.md`, plus this report.
`app/error_codes.json` was not changed: every refusal reuses an existing code.

**Deviations from the briefed contract (all small):**
- **`/providers` `note` is the exact briefed sentence. On the fixture replay, the board's `note` adds one sentence
  ("This data source (the fixture replay) stores no quote snapshots."). Why: the replay has no quote rows, so
  every board there is empty, and the payload should say why.**
- **`/slate` is not paged and has no `limit`/`offset`. Why: the largest night on the frozen export is 70,451 B, below
  the 100 KB bar. Slate `home`/`away` are team abbreviations (the brief gave no type).**
- **`model_ref.props` is the template with ids filled in; that route still needs `?line=`.**
- **Two tests in `test_betting_flag.py` (from the flag lane) were updated, because they pinned "one route":
  `test_off_adds_no_route` now expects exactly `BETTING_ROUTES`, and the hostile test accepts any typed 200 on the new
  routes (status keeps its exact body check). No other existing test changed.**

## Contract
`docs/contracts/betting_board_v1.md` has the full contract: the rule, every field, the refusals, and example
payloads. In short:
- `GET /api/betting/providers` lists providers with at least one served quote, sorted by id. `n_quotes` is exactly
  what the boards serve (tested). `fee` and `settlement_rule_id` are null, and `unknown` names fee,
  settlement_rule_id, liquidity and live_status.
- `GET /api/betting/board/{game_id}/{player_id}/{stat}`. Rule: for each (line, provider, side) only the newest
  snapshot provably from before tip-off. A quote counts as before tip-off when it has a snapshot time, its stored
  `minutes_before_tip` is above 0 when present, and its snapshot is earlier than the tip-off when the tip-off is
  known; at least one of those two facts must exist. On a tie, the main market comes before an alternate ladder,
  then a usable quote before a flagged one, then the stored row sequence. Only the shown prop's own quote run is
  read (the run the store already loads). Sort: line, then provider id, then over before under. An unusable quote
  has null price and null implied value. No model number is served; `model_ref` points to `/api/props/...` and
  `/api/record/...`, so their locks keep applying.
- `GET /api/betting/slate/{date}` lists the games and the props that have at least one served quote.
- Refusals: `unknown_game`, `unknown_player`, `unknown_stat`, `no_forecast`, `bad_request` (422), `not_a_date`,
  `unknown_slate`, `export_refused` (503). A prop with no quote returns 200 with `lines: []` and every provider in
  `providers_absent`.

## Store hook (the only change outside `betting/`)
`export_rows._prop_lines_for` takes optional `extra` columns. `ExportStore` reads `implied_prob_raw` when the table
has it and keeps it as `quote_implied`: one `array('d')` per prop, aligned with `quotes`, NaN where missing. On the
frozen export that is 283,345 values in 2,191 arrays, about 2.4 MB. Quote dicts are unchanged, so no existing
payload gains a key (tested: props, card, breakdown, game, slate and meta are identical OFF and ON).
`PROP_LINE_COLS` is unchanged.

## Frozen export (2026-11-16), TestClient, warm unless said
- Providers (served quotes, kind): betmgm 11,529; betonlineag 21,707; betopenly 734 (exchange); betrivers 16,292;
  bovada 22,185; draftkings 19,743; fanatics 22,709; fanduel 23,662; kalshi 16,584 (exchange); mybookieag 18,585;
  novig 21,501 (exchange); pinnacle 3,010; polymarket 13,772 (exchange); prophetx 31,232 (exchange);
  williamhill_us 24,370 (label Caesars). Newest snapshot for every provider: 2026-11-17T00:45:47Z. No PrizePicks.
- Totals: 283,345 quotes loaded, 267,615 served. 0 loaded quotes failed the before-tip test; the rest are older
  snapshots or same-key duplicates (main and alternate markets at the same line).
- `/providers`: 2,956 B, 1.6 ms. The first call costs the store load (4.8-10.5 s across runs; the machine was shared)
  plus a one-time summary of 1.1 s.
- `/slate`: 685 B to 70,451 B (2026-11-11, 550 props), 1.2-3.8 ms. 2026-11-14 and 2026-11-15 have 0 props with quotes.
- `/board`: mean 38,976 B, max 100,469 B (0022600203/1629029/pts: 40 lines, 317 quotes), 1-3.5 ms. All 2,191
  boards answered in 3.9 s total.

## Tests (`cd app/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`, then `PH_BETTING=1 uv run pytest`)
Before: 581 passed, 1 skipped (OFF). After: OFF 604 passed, 1 skipped (exit 0); ON 604 passed, 1 skipped (exit 0).
ruff, format and mypy are clean (102 files). New: `tests/test_betting_board.py`. Hostile generator: three templates
added to `BETTING_ROUTES` (fuzzed in ON runs by every route-walking test, and in both states by
`test_betting_flag.py`).

## Commits
`6beaa376` routes, store hook, tests; `5d03fda5` contract; then this report.

## Decisions (what / why / what would reverse it)
1. No model number in these routes, only `model_ref`. Why: it is the simplest way to keep every display lock intact.
   Reverse: a later lane routes model fields through the props route's own helpers.
2. Exchange = the five ids in `configs/odds_live.yaml:4` (`us_ex`). Why: a documented list, not inferred from data.
   Reverse: the export starts storing a provider kind.
3. Only the shown prop's quote run is read; older runs and snapshots are not served. Why: the store already loads
   exactly this, and it adds no memory or load time. Reverse: v2 serving snapshot history (needs a new DuckDB read).
4. On a tie the main market comes before the alternate, then usable before flagged. Why: deterministic and not
   price-based. Reverse: Devin wants both markets shown (the quote_id would then need the market).
5. Unusable quotes keep no price or implied value. Why: the existing rule that a flagged quote is never shown as a
   price. Reverse: none planned.
6. PrizePicks is denied by id even though it is absent. Why: brief rule 6. Reverse: none.
7. Summary counts are cached per store object (WeakKeyDictionary); a new export means a new store. Why: 1.1 s per
   call otherwise. Reverse: none needed.
8. The explicit season-2025 guard in the routes sits on top of the store's own refusal. Reverse: none.

## Not verified
- The web vitest money-safety scan (`app/web/src/bettingSafety.test.ts`) was not run: there is no `node_modules`
  in this worktree. Its regexes are ported to `test_betting_board.py` and pass on every file in `betting/` and on
  every payload.
- No uvicorn server run; all timings are in-process TestClient on a shared machine.
- The 100 KB slate bar on a full 15-game night is not measured; my estimate is about 106 KB.
- Labels other than Caesars come from common knowledge of the brands, not from a repo source.
- Nothing pushed or merged.
