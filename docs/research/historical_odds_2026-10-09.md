# Historical odds data: player props + game lines (2022-23 to 2025-26)

Date: 2026-10-09. Scout report. No code, data or ledger was touched.
Prices and coverage were checked on vendor primary pages on this date unless marked
**[unverified]**. Kalshi coverage was spot-checked with live public API calls.

## Verdict

**PURSUE**: The Odds API, paid historical tier, one month of the **5M plan ($119)**,
plus **free Kalshi historical data** for 2025-26 as the second source. Expected total:
**$119** (add a second month, **$238** in total, only if the pull overruns). Cancel right after the pull.

**PARK**: SportsDataIO Betting Data Archive. This is the only plausible route to
**2022-23 regular-season props**, but the price is quote-only (email sales). Ask for a
quote only if the 2023-24 to 2025-26 comparison shows something worth extending.

**SKIP**: OpticOdds, OddsJam API, Unabated, Action Network, BettingPros, SBR scraping,
odds-api.io and Kaggle game-line dumps. The reasons are in the table below.

### Hard limit to know before paying
The Odds API's historical **player props start 2023-05-03**. **2022-23
regular-season props cannot be bought from it.** For 2022-23 you get game lines only
(those go back to June 2020). So "4 seasons of props" with this vendor really means
2023-24, 2024-25 and 2025-26, plus the 2023 playoffs after May 3.

### Expected value vs cost
The repo's biggest open question is "does any of this beat the market?" (PROJECT_STATUS:
"No odds data yet ... unanswered"). Every props and parlay claim is unanchored until
there is a market comparator. Kalshi gives one season of thin ladders on about 5 players
per game. For $119 and roughly half a day of rate-limited pulling, The Odds API gives
about 3 seasons of main-line O/U props from several US books at chosen pre-tip
timestamps, alternate (N+) ladders, and game lines with Pinnacle. That turns
model-vs-market from impossible into a powered, pre-registered test. The data is
stored locally and retained indefinitely under their terms. The downside is small and
bounded: one month's fee, plus name-matching work the Kalshi alias table already
prototypes. The likely finding is that the market wins on main lines. That is still
worth knowing (CLAUDE.md risk 7), and it would directly calibrate the market-as-prior
shrink weight. EV is clearly positive.

## Comparison table

| Provider | Props history (NBA) | Game lines history | Books | Timestamps | Access / format | Price (verified?) | Est. cost for our pull | ToS for personal research | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **The Odds API** | From **2023-05-03** (`player_points/rebounds/assists/threes` + `_alternate` N+ ladders) | From 2020-06-06; 10-min snapshots, **5-min from Sep 2022** | us: DK, FD, BetMGM, Caesars, BetRivers, Fanatics, Bovada…; us2; **eu incl. Pinnacle**; us_ex incl. Kalshi, Novig, Polymarket, ProphetX (historical availability per book only from when the book was added) | Any timestamp → closest snapshot **at or before** it (leak-safe by design) | REST JSON; per-event endpoint for props | Plans: 20K $30, 100K $59, **5M $119**, 15M $249 per month (verified); historical requires a paid plan | ~0.5-1.5M credits → **one month of 5M = $119** | Research use and indefinite storage explicitly allowed; no redistribution as a data product (verified) | **PURSUE** |
| **Kalshi (free)** | KXNBAPTS etc.: present **2025-12-25** (5 players x 5 thresholds, volume 0-5.3k contracts), **absent opening night 2025-10-21**; exact start date unverified | KXNBAGAME from opening night 2025-26 (10-12M contracts per side, verified); 2024-25 Finals ticker not found (series may differ) **[unverified]** | Single exchange | Candlesticks 1 min / 1 h / 1 day, bid/ask OHLC + volume + OI | Public REST; `/historical/*` behind cutoff (now `market_settled_ts` = 2026-08-10) | Free | $0 | Public market data; repo already ingests it read-only | **PURSUE (complement)** |
| SportsDataIO Betting Data Archive | NBA player props, opening and closing "with full history between them" | Yes | "SportsbookGroup"; list visible only in account | Line movement | Separate API key; sales-enabled | **Quote only** (email sales). Third-party "Discovery Lab" ~$99-149/mo **[unverified]** | Unknown | Not checked (needs an account) | **PARK** (2022-23 props only) |
| SportsGameOdds | Claims props + historical on **Pro** | Yes | 82 | "Opening line through tip-off" claimed | REST; Pro is unlimited objects at 300 req/min | Pro **$299/mo** on the pricing page (third parties say $499 monthly / $299 annual) | $299-499 | Not checked | SKIP for now: historical depth **unverified**, 2.5-4x the cost |
| odds-api.io | Closing lines incl. basketball props | Closing only | Up to 30 per request | **Closing only, back to Dec 2025** (verified) | REST | Closing-lines endpoint paid; price not checked | n/a | Not checked | SKIP: one season, no pre-tip snapshots |
| OpticOdds | Historical endpoint exists; props depth unknown | Yes | Many | Unknown | License key via contact form | Quote only; third parties say ~$5k/mo per sport **[unverified]** | >>$119 | Not checked | SKIP: cost |
| OddsJam API | Unknown | Unknown | Many | Unknown | Quote only | Third parties say $500 to $5k+/mo **[unverified]** | >>$119 | Not checked | SKIP: cost |
| Unabated API | "Closing" listed; props history unconfirmed | Yes | US books + consensus line | Unknown | WebSocket/REST | $20 to $1k+/mo per a directory **[unverified]** | Unknown | Not checked | SKIP: no confirmed props history |
| PropLine | Claims prop history on paid tiers | ? | ? | ? | ? | Hobby $9 / Pro $19 per Capterra **[unverified]** | Maybe very cheap | Not checked | Worth one email if The Odds API falls through; tiny, unknown vendor |
| BettingPros / Action Network / SBR / OddsPortal | Web prop-history pages, consumer tools | SBR / OddsPortal have game-line archives | Various | Varies | **Scraping only** | Consumer subscriptions | n/a | Scraping typically prohibited by ToS (BettingPros terms not located) | SKIP: scraping risk, fragile, against the "free and legal" rule |
| Kaggle "NBA Betting Data Oct 2007 to Jun 2026" (cviaxmiwnptr) | None | Title confirms range to Jun 2026; **columns, source and licence unverified** (page did not render) | Probably 1 consensus line | Probably closing only | CSV | Free | $0 | Check the licence on the page | Optional free cross-check for 2022-23 game lines once the licence is read |

## Cost estimate for The Odds API (one pull, 2022-23 to 2025-26)

Credit rules (verified): historical odds cost **10 x markets x regions**. Historical
event odds (props) cost **10 x unique markets returned x regions**. Historical events
list costs **1** per call. Empty responses are free.

| Pull | Calls | Credits |
|---|---|---|
| Events list, one per game date (~700 dates over 4 seasons) | ~700 | ~0.7k |
| Game lines, slate endpoint `h2h,spreads,totals`, regions `us,eu` (Pinnacle), at each distinct tip time (~4 per date) plus one "open" snapshot ~24 h out | ~3.5k | ~210k |
| Props main lines, 4 markets, region `us`, 3 snapshots per game (T-24h "open", T-60 to match the repo's comparator, T-5 "close"), ~4,000 games (2023 playoffs after 5/3, 2023-24, 2024-25, 2025-26) | ~12k | ~480k |
| Optional: `_alternate` N+ ladders (matches Kalshi's format), same 3 snapshots | ~12k | +~480k |
| Optional: add `us2` or `us_ex` region for props | | +~480k each |

Base pull is about **0.7M credits**. With the alternates it is about 1.2M, and with
everything about 2.2M. Every scenario fits the **5M plan ($119)** in one month. The
100K plan ($59) does not fit even closing-only props for one season once game lines are
added. The 15M plan is not needed.
Request count is about 16k to 30k. At a conservative 1 request/s with backoff on 429
("space out requests over several seconds"), that is 5 to 9 hours of wall time, resumable.

Unverified items to confirm on day 1 (before spending most of the credits). Pull one
2023-11 game and one 2025-01 game and check:
(a) which us books actually return NBA props in 2023-24;
(b) whether Pinnacle (eu) returns any props; assume game lines only;
(c) whether `us_ex` has historical Kalshi or Novig data for NBA;
(d) the snapshot density near tip.

## Pull plan (for whoever implements; adapter behind a flag, per CLAUDE.md)

1. **Pre-register first** (draft below). Put a cost line into `configs/` as a value,
   not hardcoded. Key via `ODDS_API_KEY` env var only. Add it to `.env.example` and
   never commit it. Adapter `nba/odds/` (or similar) behind `--odds-source theoddsapi`.
   It is read-only like `nba/kalshi/`.
2. **Probe** (≤5k credits): the four checks above. Log `x-requests-remaining` and
   `x-requests-used` headers per call.
3. **Raw-first, idempotent:** store every JSON response gzipped under
   `data/odds/raw/<date>/<event>_<ts>.json.gz`, keyed by (endpoint, event, timestamp,
   markets, regions). Skip any key already cached, so a rerun costs 0 credits.
4. **Order:** 2024-25 props (select season) → 2023-24 → game lines for all 4 seasons
   → 2025-26 props last. Then cancel the plan.
5. **Mapping:**
   - Events → `games.game_id` via (home team name, away team name, commence_time ±
     12 h). Fail loudly on 0 or ≥2 matches.
   - Player names (`description` / outcome `description` fields) → `player_id` via a
     **reviewed alias table**, reusing the Kalshi alias pattern
     (`configs/kalshi_aliases_candidates_2026-10-09.yaml`, incl. the Jr./II collision
     list). Unmatched names fail loudly and are reported with counts.
   - Store the American price, the decimal price, and the line. Pre-2022-09-18 American
     prices are converted from decimal and may carry rounding (vendor note). That only
     affects the 2022-23 game lines' first weeks.
6. **Leakage guard:** snapshot timestamp ≤ the as-of time used by the model (T-60 for
   the primary comparison). Never use T-5 "close" as a model *input*, only as a
   benchmark. Missingness (a player with no prop posted) is itself informative (role,
   injury news). Never impute it into features; restrict comparisons to posted props
   and report the posting rate.
7. **Kalshi free pull in parallel:** `/historical/markets` for `KXNBAGAME`,
   `KXNBASPREAD`, `KXNBATOTAL`, `KXNBAPTS/REB/AST/3PT` for the 2025-26 season, plus
   hourly candlesticks (1-min only within the last hour pre-tip). The existing
   `nba/kalshi` client already speaks these endpoints. Note that the 2025-12 prop titles
   read `"Jalen Brunson records 30+ points"`. That differs from the `"Name: 40+ points"`
   format the parser is verified on, so it is a parser test case to add.

## Prior evidence in this repo
- TEST_LEDGER: no market or odds comparison has ever been run. This is not a re-test.
- PROJECT_STATUS: "Markets / parlays ... No odds data yet → the 'is there money here?'
  question is unanswered". Kalshi live snapshots run every 15 min since 2026-10-08.
  KALSHI_READINESS has 124 KXNBAPTS and 100 KXNBAREB rows seen.
- STORYBOARD / OVERVIEW quote "sharp closing line ~0.58-0.60 log-loss scale" from
  memory, not from data. This pull would replace that guess with a measured number.
- HOLDOUT_ACCESS_LOG: **2025-26 is the frozen holdout season.** Any model-vs-market
  comparison on 2025-26 is a confirmatory touch and must be logged before it runs. The
  Kalshi 2025-26 data is therefore holdout-only for confirmatory claims.

## DRAFT pre-registration (not logged; maintainer or lead logs it)

**DRAFT: Model vs market, props and game win probability**

- **Hypotheses.**
  - H1 (props): the production props model's P(stat ≥ line) has lower log loss than the
    de-vigged market probability (main O/U line, median across available books) at T-60.
  - H2 (blend): a market-as-prior blend `logit(p) = logit(p_mkt) + w·(logit(p_model) -
    logit(p_mkt))`, with `w` fit on the select season, beats the market alone.
  - H3 (games): Elo/injury-Elo win probability vs the de-vigged Pinnacle (else consensus)
    moneyline at T-60.
  - Expectation stated up front: H1 and H3 likely fail and H2 may show w ≈ 0.
- **Primary metric:** log loss on binary over/under outcomes (pushes excluded and
  counted). Secondary: Brier, ECE, CLV-style drift (T-60 vs T-5 line move in the model's
  direction).
- **De-vig:** multiplicative (proportional) as primary, power method as a sensitivity
  check, fixed in advance.
- **Practical floor:** Δ log loss ≥ 0.002 per event to claim "model beats market".
  Anything smaller is "indistinguishable".
- **CI:** paired bootstrap clustered by **game_id** (all props in a game resampled
  together), 2,000 reps, 95%.
- **Multiplicity family:** {pts, reb, ast, fg3m} x {H1, H2} = 8 tests, Holm. H3 is
  separate (1 test).
- **Seasons:** fit any de-vig choice or blend weight `w` on **2023-24**, select on
  **2024-25**, report 2024-25. **2025-26 holdout:** one logged touch, for the frozen
  production model and the frozen `w` only, with books and timestamp fixed. Kalshi
  ladders are a 2025-26-only secondary analysis inside that same touch.
- **Slices:** cold-start bucket, starter vs bench, line tier (low / mid / high), home/away,
  season phase (first 15 games), favorite/underdog, book (DK vs FD vs consensus), posted
  T-60 vs posted-late.
- **Kill criteria:**
  - Name-match rate < 97% of posted props after review → stop and fix mapping.
  - Fewer than 2 books per prop on > 50% of events in 2023-24 → drop "multiple books"
    claims.
  - Probe shows props absent before 2024 → rescope to 2 seasons.
  - Any feature row with snapshot_ts > as-of → void the run.
- **Holdout touch earned?** Yes, once, only after H1/H2 are reported on 2024-25 with the
  frozen rule.

## Not verified
- Which books carry NBA props in The Odds API history for 2023-24 (vendor says props are
  "mainly US books"; no per-book history list).
- Whether Pinnacle props or historical `us_ex` (Kalshi, Novig) appear for NBA.
- The Odds API historical rate limit in requests/s (docs say only "space out over
  several seconds").
- SportsDataIO archive price and book list; SportsGameOdds historical depth; OpticOdds,
  OddsJam and Unabated prices (all third-party or competitor claims).
- Kaggle dataset columns and licence (page did not render); Kalshi NBA prop start date
  (present 2025-12-25, absent 2025-10-21); Kalshi NBA game markets for 2024-25 (Finals
  ticker guess returned nothing).
- BettingPros terms of use (not located).

## Sources
- The Odds API v4 docs (historical endpoints, coverage dates, credit costs, 429 guidance): https://the-odds-api.com/liveapi/guides/v4/
- The Odds API plans: https://the-odds-api.com/
- The Odds API terms: https://the-odds-api.com/terms-and-conditions.html
- The Odds API markets (NBA prop keys, alternates): https://the-odds-api.com/sports-odds-data/betting-markets.html
- The Odds API bookmakers/regions: https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
- SportsDataIO historical archive guide: https://sportsdata.io/help/historical-data-integration-guide
- SportsDataIO third-party review: https://sportsapis.dev/apis/sportsdata-io
- SportsGameOdds pricing: https://sportsgameodds.com/pricing/
- odds-api.io historical guide: https://docs.odds-api.io/guides/historical
- OpticOdds developer docs: https://developer.opticodds.com/ ; competitor comparison: https://sportsgameodds.com/blog/optic-odds-vs-sports-game-odds
- OddsJam pricing (competitor blog): https://oddspapi.io/blog/?p=2860
- Unabated (directory review): https://sportsapis.dev/apis/unabated
- PropLine (Capterra): https://www.capterra.com/p/10049733/PropLine/
- Kaggle NBA betting data: https://www.kaggle.com/datasets/cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024
- Kalshi historical data docs: https://docs.kalshi.com/getting_started/historical_data
- Kalshi candlesticks: https://docs.kalshi.com/api-reference/market/get-market-candlesticks
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Kalshi live API checks (2026-10-09): `/trade-api/v2/historical/cutoff`; `/historical/markets?event_ticker=KXNBAGAME-25OCT21HOUOKC`; `...KXNBAPTS-25DEC25CLENYK`; `...KXNBAPTS-25OCT21HOUOKC` (empty); `...KXNBAGAME-25JUN22INDOKC` (empty)
