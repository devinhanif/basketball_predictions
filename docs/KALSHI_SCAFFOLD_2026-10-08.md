# Kalshi read-only ingestor — scaffold (2026-10-08)

Milestone 9 (CLAUDE.md Phase 3). Structure + parsing + fixtures + tests only.
No live network calls were made to build or validate this; everything below
is exercised against recorded/synthetic JSON fixtures.

## What's built

`nba/kalshi/`:
- `client.py` — `KalshiClient`: thin `httpx`-based wrapper over Kalshi's
  **public** market-data REST API (`api.elections.kalshi.com/trade-api/v2`,
  live + `/historical/*` tiers). Every method is a plain `GET` with no
  auth header. `fetch_cached_json` mirrors `nba.ingest.cache.fetch_cached`'s
  resumable/idempotent pattern for raw JSON (vs. that helper's
  parquet-DataFrame shape): checks `ingest_log`, skips the network call if
  already `'done'`, snapshots raw JSON under `data/kalshi_raw/` before
  marking done, marks `'failed'` (not `'done'`) on any exception.
- `cutoff.py` — parses `GET /historical/cutoff` and decides `live` vs.
  `historical` tier for a given timestamp (ties go to `live`, the
  conservative choice). Pure, no network/DB.
- `thresholds.py` — regex parse of `"<Player> <Stat> <N>+"` titles into
  `(player_name, stat, threshold)`, stat vocabulary normalized to this
  project's `pts|reb|ast|fg3m|pra`. Raises `UnparseableTitleError` (caught
  upstream as "not a player-prop market", not an error) for anything else.
- `aliases.py` — small, hand-reviewed `name -> player_id` table plus
  `resolve_kalshi_player_name` (exact-normalized match, then a
  high-confidence fuzzy fallback via `difflib`). Raises
  `UnmatchedKalshiNameError` loudly — **never** silently skips or guesses —
  for any name not in the reviewed table. Only 7 real players are seeded;
  every new one needs a human-reviewed row added here before ingest.
- `parse.py` — raw `Market`/`MarketCandlestick` JSON → `kalshi_markets` /
  `kalshi_prices` row frames (schemas match CLAUDE.md's DuckDB tables
  exactly, already present in `nba/db/schema.sql` — no schema change was
  needed, so there was no collision risk with tonight's injury-feed work).
  Tolerates both the real API's nested OHLC candlestick shape and a flat
  shape (for simpler synthetic fixtures); never coerces a genuinely missing
  quote to `0`.
- `sampling.py` — `check_sample_size` / `describe` / `require_sufficient`:
  the one place "refuse to claim superiority from fewer than N settled
  markets" is enforced, config-driven floor (`DEFAULT_MIN_SETTLED_MARKETS
  = 30`, overridable), never a hardcoded claim.
- `ingest.py` — orchestration: `get_cutoff`, `pull_markets_for_series`,
  `pull_candlesticks_for_market`. Idempotent (`kalshi_markets.ticker` PK ->
  upsert; `kalshi_prices` has no PK -> delete-then-insert per
  `(ticker, source)`, same convention as `player_availability`).
- `__main__.py` — `python -m nba.kalshi {cutoff,markets,candles}` CLI.
  Network calls only fire when a human explicitly runs a command; nothing
  here is wired to auto-run.

`tests/kalshi/` — 37 tests, all against `tests/kalshi/fixtures/*.json`
(synthetic, hand-written to match documented Kalshi field shapes):
cutoff parsing + tier selection, threshold-title parsing (valid + invalid),
alias resolution (exact/fuzzy/unmatched-raises), market/candlestick
parsing (including the unmatched-name-raises and skip-non-prop-market
paths), sample-size guardrail, and ingest idempotency/resumability (fake
client with a call counter, asserting a second pull of the same
series/market does **not** re-invoke the (fake) network and does **not**
duplicate DB rows).

## What's stubbed / explicitly not built tonight

- No alias entries beyond 7 well-known players — this is a seed, not a
  complete table. CLAUDE.md requires this to be hand-reviewed, so growing
  it is an ongoing, deliberate process, not a backfill job.
- No pagination loop in `__main__.py` (the client supports a `cursor` param
  per page; the CLI pulls one page). Fine for a scaffold; real ingest runs
  will need a thin loop added to `pull_markets_for_series`.
- No odds-provider adapter (optional, flagged in CLAUDE.md as out of scope
  for this milestone).
- `nba/parlay/` (milestone 10/11, joint-probability + EV engine) is
  untouched — that's the next milestone, building on these tables.
- The CLI was never run against the live network in this session — only
  `uv run ruff check`, `uv run mypy nba/kalshi`, and
  `uv run pytest tests/kalshi -q` were executed, all fixture/unit-only.

## No-auth / no-orders guarantee

`KalshiClient._get` is the only place an HTTP request is constructed in
this package; it sends a bare `httpx.get(url, params=...)` with no
`Authorization` header and no API-key parameter anywhere in `client.py`.
The only paths this client knows how to call are `/historical/cutoff`,
`/markets` (+ `/historical/markets`), and
`/series/{series}/markets/{ticker}/candlesticks` (+ historical equivalent)
— all public market-data reads. There is no `place_order`, `cancel_order`,
`portfolio`, or any trading-scope method anywhere in `nba/kalshi/`, and
none is planned; this matches the hard constraint that the system never
places orders or stores trading credentials.

## Honest caveat: thin history

This is unavoidable and stated here explicitly rather than glossed over:
Kalshi NBA player-prop markets are a new product with a short settled
history and coverage limited to a subset of players. `sampling.py`'s
`DEFAULT_MIN_SETTLED_MARKETS = 30` is a starting guess, not a validated
number — nobody has run this against real settled-market counts yet, since
no live pull has happened. The honest status of "is there enough history
to compare model probabilities to Kalshi prices" is **unknown** until
milestone 10/11 actually pulls real data and someone reports `n`. Any
report produced by this scaffold must carry that `n` and the
`describe()`/`check_sample_size()` caveat, never a bare win/loss claim.

## Maintainer commands

```
uv run ruff check nba/kalshi tests/kalshi
uv run mypy nba/kalshi
uv run pytest tests/kalshi -q
```

All three pass as of this commit (37 tests, 0 network calls, 0 mypy
errors, 0 ruff errors). The repo-wide `--cov-fail-under=70` gate in
`pyproject.toml` only applies to a full `pytest` run across `tests/`, not
to this subdirectory in isolation — running `tests/kalshi` alone reports
low overall coverage because it doesn't exercise the rest of the repo;
that's expected, not a regression.

To actually exercise the live client against the real Kalshi API (NOT run
in this session, and not something CI should do), a human would run e.g.:

```
python -m nba.kalshi cutoff
python -m nba.kalshi markets --series KXNBAPTS
```
