# Closing-line capture (the-odds-api.com, live)

**2026-10-11 23:05 CDT update: full pre-tip schedule (Devin: "do all captures of prices before the
game now, don't skimp on credits").** Sections below that say "t60 and close" describe the first
version; the schedule is now the list in `configs/odds_live.yaml`, run by
`python -m nba.odds capture --kind all` (the lineups hook). See "The schedule" at the end.

Devin, 2026-10-11: the live season's benchmark is the CLOSING line. This is the capture that makes
it exist. **Off unless `NBA_CLOSE_CAPTURE=1`**; with the flag unset nothing here runs, reads a key,
opens a file or makes a request. A closing price that was not captured before tip can never be
backfilled, so the failure mode that matters is a silent miss: every miss is a row in
`live_capture_log` and, when it is an error, a line in `data/ops/ALERTS.md`.

## What it does

`nba.odds capture --kind close|t60 --date <ET date>` (code: `nba/odds/live_close.py`, config:
`configs/odds_live.yaml`, base URL / markets from `configs/odds_history.yaml`). One call is one tick:

1. Reads the slate's REAL scheduled tips from `game_tips` in `data/lineups/lineups.duckdb`
   (read-only; the lineups poller already maintains it). No real tip, no pull. No proxy times.
2. Selects games with `now + lo <= tip <= now + hi` and `tip > now`: `close` = [0, 10] minutes,
   `t60` = [55, 65] minutes (config `windows`). Nothing in the window: exit 0 before the key is read.
3. Skips games that already have a snapshot of that kind (or a terminal outcome, or two failed
   attempts). Idempotent and safe every 5 minutes.
4. `GET /v4/sports/basketball_nba/events` (free) to find the vendor event, matched on both team ids
   and `commence_time` within 30 minutes of our tip. No unique match: logged `unmatched`, never guessed.
5. `GET /v4/sports/basketball_nba/events/{id}/odds` once, all game markets (`h2h, spreads, totals`)
   and prop markets (`player_points, player_rebounds, player_assists, player_threes`), regions
   `us,eu` (Pinnacle is eu), American odds.
6. Caches the raw response (gzip, key-free) under `data/odds/raw/the_odds_api_live/<game_id>/`
   before parsing, then writes rows to `live_book_odds` in `data/markets/market_asof.duckdb`
   (the file `nba.markets.capture` already writes `market_at_prediction` to; no existing table is
   touched). `live_capture_log` holds one row per attempt (status, rows, credits, note).

Rows carry: `requested_at` (our clock before the request), `received_at` (after the response),
`tipoff` (ours), `commence_time` (the vendor's), `snapshot_kind` (`t60` | `close`), `book`, `market`,
`player_name`, `player_id` (NULL when unresolved, never fuzzy), `side`, `point`, `price_american`,
`implied_prob_raw` (vigged), `implied_prob` (proportional de-vig within a two-sided pair),
`last_update` (the book's own stamp), `raw_file`.

**Known-at rule.** A row is written only if `received_at` is strictly before both our tip and the
vendor's `commence_time`; the request is not even sent if either has passed. A response that lands
after tip is kept as raw, logged `post_tip_refused`, and stored nowhere else. `write_snapshot` is
the only writer of the table and enforces this itself.

**Honest timing.** The tick is the 5-minute lineups job, so a game is first seen in the close window
at 5 to 10 minutes before tip and is pulled then: the typical close is T-5 to T-10, inside the
10-minute tolerance but not at T-5 on average. If a tick is skipped (lock held, machine asleep) the
next one is still inside the window; if both are missed the game has no close row and the log says so.
Games that tip after ET midnight belong to the previous `--date`; the hook passes today's ET date.

## What it costs

Rule (vendor documentation, the-odds-api.com v4 usage guide): `/events` costs 0; `/events/{id}/odds`
costs **1 credit per market returned per region**. The historical endpoint is 10x that
(`configs/odds_history.yaml`, `cost_per_market_region: 10`), which is why a live pull is cheap. The
guard uses the upper bound; the real charge is read from the `x-requests-last` response header and
booked in the daily ledger.

- One game, one snapshot: 7 markets x 2 regions = **14 credits**.
- 10-game slate: close 140 + t60 140 = **280 credits a night**; close only 140.
- 15 games, both kinds: 420. Season (about 1,230 games, both kinds): about 34,000 credits, under 1%
  of the 5,000,000-credit plan.

Guards (`configs/odds_live.yaml`): `daily_max_credits: 600` (one ledger file per ET day,
`data/odds/budget/the_odds_api_live_<date>.json`, refuses a call that would pass it) and
`min_server_remaining: 100000` (refuses below the server-reported floor, the same floor as the
historical pull, which shares the plan). A refusal is logged `credit_guard`, exits 4, alerts.

The credit rule above is quoted from memory of the vendor docs and has not been checked against a
live response. The first night's `x-requests-last` values in the ledger are the check.

## How to enable

The hook is in `ops/nba_lineups.sh`, not a new job: the tick already runs every 300 s with per-game
timing, and a new launchd job would be a scheduler install, which is the maintainer's. It runs
before the T-30 block so a long wait for `nba.duckdb` cannot delay a close pull. The flag is read
from the environment or from a line in `.env`; the key is read from `THE_ODDS_API_KEY` in `.env`
and handed to python by `uv run --env-file .env`. Neither is echoed.

```sh
echo 'NBA_CLOSE_CAPTURE=1' >> /Users/devin/Downloads/nba-prediction/.env   # key already in .env as THE_ODDS_API_KEY=...
```

Disable: delete that line. The tree is what launchd runs, so this merges to main only after review.

## How to verify after one night

```sh
cd /Users/devin/Downloads/nba-prediction
# one row per game and kind, all ok, credits near 14 each, received_at before tipoff
uv run python -c "
import duckdb
c = duckdb.connect('data/markets/market_asof.duckdb', read_only=True)
print(c.execute('SELECT game_id, snapshot_kind, status, n_rows, credits, tipoff - received_at AS lead FROM live_capture_log ORDER BY tipoff, snapshot_kind').fetchall())
print(c.execute('SELECT count(*) FROM live_book_odds WHERE received_at >= tipoff').fetchone())  # must be 0
"
cat data/odds/budget/the_odds_api_live_$(TZ=America/New_York date +%F).json   # spent vs 280 expected
find data/odds/raw/the_odds_api_live -name '*.gz' -exec zgrep -l apiKey {} +   # must print nothing
```

Expect: every slate game has a `close` and a `t60` row with status `ok`; `lead` for close between
about 0 and 10 minutes; no `unmatched` or `post_tip_refused`; unresolved-player counts in the log
line are the alias table's, not a capture fault.

## How the paper ledger will read `close` rows

Nothing in `nba/parlay` reads `live_book_odds` yet; this is the contract for the change that wires
it (not part of this branch). For a leg `(game_id, market, player_id, side, point)`:

- price = the `snapshot_kind = 'close'` row for the same book, market, player, side and point, using
  `implied_prob` (de-vigged) for the benchmark and `price_american` for the payout; a pair with a
  NULL `implied_prob` is not a benchmark.
- benchmark book order is declared before scoring (Pinnacle first, then the consensus median across
  books that quote that exact point); a leg with no close row at its exact point is "no close
  available", counted, never filled from `t60`.
- model-vs-close is paired by `game_id` with date-clustered intervals, per FORWARD_PREREG_2026_27;
  `t60` rows exist to measure how far the line moves from the model's time to the close.
- the closing line is a benchmark, not a training target and not a price the paper account could have
  taken at T-60.

## Not verified without a network call

`commenceTimeFrom/To` on `/events`, the bare-event shape of `/events/{id}/odds` (the parser expects
`{id, commence_time, home_team, away_team, bookmakers}`, as in the historical `data` object), the
header names `x-requests-last/remaining/used`, the credit rule, and which of the 7 markets each book
returns for a given game. Fixtures in `tests/odds/test_live_close.py` encode these assumptions.


## The schedule (2026-10-11)

`configs/odds_live.yaml: schedule` is the list; change it there, no code. One entry is one snapshot
kind per game, pulled once per game (idempotent), same markets (h2h, spreads, totals, player
points/rebounds/assists/threes), same regions (`us,eu`) and books as before. Every row carries
`snapshot_kind`, `requested_at`, `received_at` and the book's `last_update`; the never-after-tip
refusal is unchanged and applies to every kind (request not sent after tip, response after tip kept
as raw only).

| kind | when | window (min before real tip) |
|---|---|---|
| `morning` | first tick at or after 10:00 ET on the slate day (up to 60 min later) | clock-based |
| `t180` `t120` `t90` | | 177.5-182.5, 117.5-122.5, 87.5-92.5 |
| `t60` | unchanged | 55-65 |
| `t45` `t30` `t20` `t15` `t10` `t5` | | tick +- 2.5 min around each |
| `close` | the last tick before tip | 0-5 (was 0-10) |

Notes. (1) The tick is still every 5 minutes (launchd, not ours to change), so a 5-minute-wide window
is hit by exactly one tick; `t5` and `close` are therefore usually the same minute (two requests, two
rows, 14 credits each; the close is the one the benchmark uses). A finer cadence than 5 minutes needs
a scheduler change, which is Devin's. (2) `close` was narrowed from [0, 10] to [0, 5] because `t10` and
`t5` now cover the earlier part; a missed tick at T-5 now loses the close (T-10/T-5 rows remain).
(3) `morning` is "first line of the day we saw", not "the book's opening line"; props are often not
posted at 10:00, which shows as `empty` or fewer rows, not an error. (4) Scorable games only:
`read_slate` drops game ids starting `001` (preseason) and `003` (All-Star) with the same
`is_scorable_game_id` the daily slate uses (commit c310720), so no credit is spent on them. (5) The
hook now runs one process, `--kind all`, close first; idle kinds print nothing.

### Credits (upper bound; the real charge is `x-requests-last`)

One call = 7 markets x 2 regions x 1 = 14 credits; `/events` is free. 12 points x 14 = **168
credits a game**.

| volume | credits |
|---|---|
| 7-game night / 15-game night | 1,176 / 2,520 (daily guard raised 600 -> 3,200) |
| peak month, 215 games | 36,120 |
| average month, ~205 games | 34,440 |
| season, 1,230 games | 206,640 |

Quota as recorded in the repo: START 5M, $119 per **month**, 5,000,000 credits per billing period
(DECISIONS 2026-10-10; the pre-reg ledger shows 3,441,402 remaining of the current period at
2026-10-11 22:59 UTC). The full schedule is 0.7% of a month's credits, so nothing is gated: the
`extended` entries stay ON while the monthly estimate is within `plan.max_plan_fraction` (0.5) of
`plan.monthly_credits`, else `LiveConfig.active_schedule()` drops them, leaving `close` and `t60`.
Smaller plans on https://the-odds-api.com/#get-access at the time of writing: START 20K $30/month
(20,000: too small, auto-gates to close + t60 at 2 x 14 x 215 = 6,020), START 100K $59/month
(100,000: full schedule fits at the 0.5 rule). **Caveat:** DECISIONS 2026-10-10 says Devin cancels the
5M plan after the month; the key stops working then and every capture degrades (`error`, alert). The
smallest plan that carries this schedule all season is START 100K, $59/month. Buying is Devin's.
